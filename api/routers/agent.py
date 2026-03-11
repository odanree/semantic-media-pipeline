"""
Agent router — POST /api/agent/query

Multi-agent endpoint that coordinates Search, Metadata, and Vision agents
via a LangGraph StateGraph. Use this for complex queries that benefit from
multi-modal reasoning. Use /api/ask for simple RAG.
"""

import time

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from agents.coordinator import coordinator
from rate_limit import limiter, LIMIT_ASK  # reuse same rate limit as /ask
from metrics import METRICS

router = APIRouter()


class AgentQueryRequest(BaseModel):
    query: str
    limit: int = Field(default=10, ge=1, le=50)
    threshold: float = Field(default=0.2, ge=0.0, le=1.0)


class AgentQueryResponse(BaseModel):
    answer: str
    query: str
    intent: str | None = None
    search_result_count: int = 0
    metadata_result_count: int = 0
    vision_result_count: int = 0
    elapsed_ms: float = 0.0


@router.post("/agent/query", response_model=AgentQueryResponse)
@limiter.limit(LIMIT_ASK)
async def agent_query(request: Request, body: AgentQueryRequest):
    t0 = time.perf_counter()

    initial_state = {
        "query": body.query,
        "limit": body.limit,
        "threshold": body.threshold,
        "intent": None,
        "search_results": [],
        "metadata_results": [],
        "vision_results": [],
        "final_answer": None,
        "error": None,
    }

    try:
        final_state = await coordinator.ainvoke(initial_state)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Agent coordinator failed: {exc}")

    if final_state.get("error"):
        raise HTTPException(status_code=500, detail=final_state["error"])

    elapsed_ms = (time.perf_counter() - t0) * 1000
    METRICS.agent_latency.observe(elapsed_ms / 1000)

    return AgentQueryResponse(
        answer=final_state.get("final_answer") or "No answer generated.",
        query=body.query,
        intent=final_state.get("intent"),
        search_result_count=len(final_state.get("search_results", [])),
        metadata_result_count=len(final_state.get("metadata_results", [])),
        vision_result_count=len(final_state.get("vision_results", [])),
        elapsed_ms=round(elapsed_ms, 1),
    )
