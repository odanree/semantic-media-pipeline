"""
RAG pipeline — Open/Closed Principle.

RAGPipeline is closed for modification but open for extension:
add new steps by passing them in the constructor list.

Each step receives a RAGContext, transforms it, and returns it.
The pipeline chains steps in order — any step can short-circuit
by setting context.error.

Example assembly (in routers/agent.py or tests):

    pipeline = RAGPipeline([
        QueryExpansionStep(llm),
        EmbedQueryStep(clip_model),
        QdrantRetrieveStep(qdrant_client),
        RerankerStep(),
        LLMGenerateStep(llm),
    ])
    result = await pipeline.execute(RAGContext(query="beach vacation 2023"))
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Optional, Protocol, runtime_checkable


@dataclass
class RetrievedItem:
    """A single result from Qdrant, enriched through the pipeline."""
    file_path: str
    file_type: str
    similarity: float
    caption: Optional[str] = None
    frame_index: Optional[int] = None
    timestamp: Optional[float] = None
    rerank_score: Optional[float] = None


@dataclass
class RAGContext:
    """Mutable state object passed through every pipeline step."""
    query: str
    expanded_query: Optional[str] = None       # set by QueryExpansionStep
    query_embedding: Optional[Any] = None       # numpy array, set by EmbedQueryStep
    retrieved: list[RetrievedItem] = field(default_factory=list)  # set by QdrantRetrieveStep
    reranked: list[RetrievedItem] = field(default_factory=list)   # set by RerankerStep
    answer: Optional[str] = None               # set by LLMGenerateStep
    error: Optional[str] = None               # any step can set this to abort pipeline
    metadata: dict = field(default_factory=dict)  # timing, debug info etc.
    # Request params carried through
    limit: int = 10
    threshold: float = 0.2
    dedup: bool = True
    collection: str = "media_vectors"


@runtime_checkable
class RAGStep(Protocol):
    """Protocol — every pipeline step must implement this."""
    async def run(self, context: RAGContext) -> RAGContext:
        ...


class RAGPipeline:
    """
    Executes a list of RAGStep instances in order.
    Stops immediately if any step sets context.error.
    Records per-step timing in context.metadata["timings"].
    """

    def __init__(self, steps: list[RAGStep]) -> None:
        self.steps = steps

    async def execute(self, context: RAGContext) -> RAGContext:
        context.metadata.setdefault("timings", {})
        for step in self.steps:
            if context.error:
                break
            name = type(step).__name__
            t0 = time.perf_counter()
            context = await step.run(context)
            context.metadata["timings"][name] = round(time.perf_counter() - t0, 4)
        return context
