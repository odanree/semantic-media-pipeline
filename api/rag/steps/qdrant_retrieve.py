"""
QdrantRetrieveStep — vector similarity search + temporal deduplication.

Reuses the same deduplication logic already in search.py (imported directly
to avoid duplication). Populates context.retrieved with RetrievedItem list.
"""

import logging
import os
from typing import Any

from qdrant_client import QdrantClient

from rag.pipeline import RAGContext, RetrievedItem

log = logging.getLogger(__name__)


class QdrantRetrieveStep:
    """Queries Qdrant and applies temporal deduplication."""

    def __init__(self, qdrant: QdrantClient, fetch_multiplier: int = 3) -> None:
        self._qdrant = qdrant
        # Fetch extra results before dedup/reranking, then trim to context.limit
        self._fetch_multiplier = fetch_multiplier

    async def run(self, context: RAGContext) -> RAGContext:
        if context.query_embedding is None:
            context.error = "QdrantRetrieveStep: no query embedding"
            return context

        fetch_limit = context.limit * self._fetch_multiplier
        try:
            result = self._qdrant.query_points(
                collection_name=context.collection,
                query=context.query_embedding.tolist(),
                limit=fetch_limit,
                score_threshold=context.threshold,
                with_payload=True,
            )
            hits = result.points
        except Exception as exc:
            context.error = f"QdrantRetrieveStep failed: {exc}"
            log.error("Qdrant search error: %s", exc)
            return context

        items = []
        for hit in hits:
            p = hit.payload or {}
            items.append(RetrievedItem(
                file_path=p.get("file_path", ""),
                file_type=p.get("file_type", "unknown"),
                similarity=hit.score,
                caption=p.get("caption"),
                frame_index=p.get("frame_index"),
                timestamp=p.get("timestamp"),
            ))

        if context.dedup:
            items = _temporal_dedup(items)

        context.retrieved = items[:context.limit]
        return context


def _temporal_dedup(items: list[RetrievedItem], window_secs: float = 5.0) -> list[RetrievedItem]:
    """
    Greedy NMS over timestamp windows per file — mirrors search.py logic.
    Videos only; images pass through unchanged.
    """
    kept: list[RetrievedItem] = []
    last_ts: dict[str, float] = {}  # file_path → last kept timestamp

    for item in items:
        if item.file_type != "video" or item.timestamp is None:
            kept.append(item)
            continue
        last = last_ts.get(item.file_path)
        if last is None or abs(item.timestamp - last) > window_secs:
            kept.append(item)
            last_ts[item.file_path] = item.timestamp

    return kept
