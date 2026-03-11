"""
RerankerStep — cross-encoder post-retrieval reranking.

Uses a lightweight cross-encoder to rescore (query, caption) pairs.
Requires Moondream captions to be populated in Qdrant payloads.
Falls back gracefully (no reranking) if cross-encoder is unavailable
or no captions exist.

Model: cross-encoder/ms-marco-MiniLM-L-6-v2
  - ~67MB, fast on CPU (~10ms for 10 pairs)
  - Returns relevance scores suitable for ranking
"""

import logging
from typing import Optional

from rag.pipeline import RAGContext, RetrievedItem

log = logging.getLogger(__name__)

_model = None


def _get_cross_encoder():
    global _model
    if _model is None:
        from sentence_transformers import CrossEncoder
        _model = CrossEncoder("cross-encoder/ms-marco-MiniLM-L-6-v2")
    return _model


class RerankerStep:
    """
    Reranks retrieved items using a cross-encoder over (query, caption) pairs.
    Items without captions are kept but scored 0.0 — they sink to the bottom.
    """

    def __init__(self, enabled: bool = True) -> None:
        self._enabled = enabled

    async def run(self, context: RAGContext) -> RAGContext:
        if not self._enabled or not context.retrieved:
            context.reranked = context.retrieved
            return context

        items_with_caption = [i for i in context.retrieved if i.caption]
        if not items_with_caption:
            # No captions available yet — pass through unmodified
            log.debug("RerankerStep: no captions found, skipping rerank")
            context.reranked = context.retrieved
            return context

        query = context.expanded_query or context.query
        pairs = [(query, item.caption) for item in items_with_caption]

        try:
            encoder = _get_cross_encoder()
            scores = encoder.predict(pairs)
        except Exception as exc:
            log.warning("RerankerStep failed (%s) — using CLIP order", exc)
            context.reranked = context.retrieved
            return context

        # Assign rerank scores to items that had captions
        score_map = {id(item): float(score) for item, score in zip(items_with_caption, scores)}

        for item in context.retrieved:
            item.rerank_score = score_map.get(id(item), 0.0)

        context.reranked = sorted(
            context.retrieved,
            key=lambda x: x.rerank_score or 0.0,
            reverse=True,
        )
        return context
