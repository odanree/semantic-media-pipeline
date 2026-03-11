"""
EmbedQueryStep — CLIP-embeds the (possibly expanded) query.

Uses expanded_query if set by QueryExpansionStep, otherwise falls
back to the original query. Stores the numpy embedding vector in
context.query_embedding for the retrieval step.
"""

import logging
from typing import Any

import numpy as np

from rag.pipeline import RAGContext

log = logging.getLogger(__name__)


class EmbedQueryStep:
    """CLIP-embeds the query text into a 768-dim vector."""

    def __init__(self, clip_model: Any) -> None:
        self._model = clip_model

    async def run(self, context: RAGContext) -> RAGContext:
        query_text = context.expanded_query or context.query
        try:
            # SentenceTransformer.encode() is synchronous — acceptable here
            # as CLIP inference is fast (<50ms on CPU for a text prompt)
            embedding = self._model.encode(query_text)
            context.query_embedding = embedding / np.linalg.norm(embedding)
        except Exception as exc:
            context.error = f"EmbedQueryStep failed: {exc}"
            log.error("EmbedQueryStep error: %s", exc)
        return context
