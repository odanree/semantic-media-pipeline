"""
LangGraph multi-agent coordinator.

State machine topology:
    User query
        → classify_intent (choose which agents to invoke)
        → [SearchAgent] parallel with [MetadataAgent]
        → [VisionAgent]  conditional — only if search returns < MIN_RESULTS
        → aggregate_results
        → generate_answer

Each node is a plain async function over AgentState (TypedDict).
LangGraph handles the state transitions and conditional edges.
"""

from __future__ import annotations

import logging
import os
from typing import Annotated, Optional, TypedDict
from typing_extensions import NotRequired

from langgraph.graph import END, StateGraph
from langgraph.graph.message import add_messages

log = logging.getLogger(__name__)

MIN_RESULTS_FOR_VISION = int(os.getenv("AGENT_MIN_RESULTS_FOR_VISION", "3"))


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------

class AgentState(TypedDict):
    query: str
    limit: NotRequired[int]                        # max results per agent
    threshold: NotRequired[float]                  # CLIP similarity threshold
    intent: NotRequired[Optional[str]]            # "visual" | "temporal" | "mixed"
    search_results: NotRequired[list[dict]]        # from SearchAgent
    metadata_results: NotRequired[list[dict]]      # from MetadataAgent
    vision_results: NotRequired[list[dict]]        # from VisionAgent (conditional)
    final_answer: NotRequired[Optional[str]]
    error: NotRequired[Optional[str]]


# ---------------------------------------------------------------------------
# Nodes
# ---------------------------------------------------------------------------

async def classify_intent(state: AgentState) -> AgentState:
    """
    Simple rule-based intent classification.
    Upgrade to LLM-based classification as needed.
    """
    query = state["query"].lower()
    temporal_keywords = {"when", "date", "year", "month", "before", "after", "during"}
    visual_keywords = {"look", "color", "show", "photo", "video", "picture", "scene"}

    has_temporal = any(k in query for k in temporal_keywords)
    has_visual = any(k in query for k in visual_keywords)

    if has_temporal and has_visual:
        intent = "mixed"
    elif has_temporal:
        intent = "temporal"
    else:
        intent = "visual"

    return {"intent": intent}


async def run_search_agent(state: AgentState) -> AgentState:
    """SearchAgent: CLIP vector similarity via Qdrant."""
    from agents.search_agent import search_agent_run
    results = await search_agent_run(
        state["query"],
        limit=state.get("limit", 10),
        threshold=state.get("threshold", 0.2),
    )
    return {"search_results": results}


async def run_metadata_agent(state: AgentState) -> AgentState:
    """MetadataAgent: PostgreSQL temporal/location queries."""
    from agents.metadata_agent import metadata_agent_run
    results = await metadata_agent_run(state["query"])
    return {"metadata_results": results}


async def run_vision_agent(state: AgentState) -> AgentState:
    """VisionAgent: deep frame analysis via GPT-4o Vision or LLaVA."""
    from agents.vision_agent import vision_agent_run
    results = await vision_agent_run(state["search_results"])
    return {"vision_results": results}


async def aggregate_and_answer(state: AgentState) -> AgentState:
    """Fuse results from all agents into a final answer via LLM."""
    from agents.aggregator import build_final_answer
    answer = await build_final_answer(state)
    return {"final_answer": answer}


# ---------------------------------------------------------------------------
# Conditional edge: invoke VisionAgent only when search is sparse
# ---------------------------------------------------------------------------

def needs_vision(state: AgentState) -> str:
    if len(state.get("search_results", [])) < MIN_RESULTS_FOR_VISION:
        return "vision"
    return "aggregate"


# ---------------------------------------------------------------------------
# Graph assembly
# ---------------------------------------------------------------------------

def build_coordinator() -> StateGraph:
    graph = StateGraph(AgentState)

    graph.add_node("classify_intent", classify_intent)
    graph.add_node("search_agent", run_search_agent)
    graph.add_node("metadata_agent", run_metadata_agent)
    graph.add_node("vision_agent", run_vision_agent)
    graph.add_node("aggregate", aggregate_and_answer)

    graph.set_entry_point("classify_intent")

    # classify → search + metadata in parallel
    graph.add_edge("classify_intent", "search_agent")
    graph.add_edge("classify_intent", "metadata_agent")

    # Both branches join at the conditional edge
    graph.add_conditional_edges(
        "search_agent",
        needs_vision,
        {"vision": "vision_agent", "aggregate": "aggregate"},
    )
    graph.add_edge("metadata_agent", "aggregate")
    graph.add_edge("vision_agent", "aggregate")
    graph.add_edge("aggregate", END)

    return graph.compile()


# Singleton compiled graph
coordinator = build_coordinator()
