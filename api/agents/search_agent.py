"""
SearchAgent — Qdrant CLIP similarity search node.

Thin wrapper: delegates to the RAG pipeline's QdrantRetrieveStep
so there is no duplicated retrieval logic.
"""

from __future__ import annotations

import os

from dependencies import get_clip_model, get_qdrant, get_collection_name
from rag.pipeline import RAGContext
from rag.steps.embed_query import EmbedQueryStep
from rag.steps.qdrant_retrieve import QdrantRetrieveStep


async def search_agent_run(query: str, limit: int = 10, threshold: float = 0.2) -> list[dict]:
    ctx = RAGContext(query=query, limit=limit, threshold=threshold, collection=get_collection_name())

    ctx = await EmbedQueryStep(get_clip_model()).run(ctx)
    if ctx.error:
        return []

    ctx = await QdrantRetrieveStep(get_qdrant()).run(ctx)
    if ctx.error:
        return []

    return [
        {
            "file_path": r.file_path,
            "file_type": r.file_type,
            "similarity": r.similarity,
            "caption": r.caption,
            "timestamp": r.timestamp,
        }
        for r in ctx.retrieved
    ]
