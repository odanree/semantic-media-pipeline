"""
QueryExpansionStep — Pre-retrieval LLM expansion.

Turns a terse user query into a richer semantic description so CLIP
retrieves more relevant results. Falls back to the original query
silently on timeout or LLM error.

Example:
    "vacation" → "beach, sunsets, palm trees, travel, family, poolside"
"""

import logging

from rag.pipeline import RAGContext
from llm.base import ILLMProvider

log = logging.getLogger(__name__)

_EXPANSION_PROMPT = """You are a visual search assistant.
Expand the user's query into a comma-separated list of visually descriptive terms
that capture what the user is likely looking for in photos and videos.
Return ONLY the comma-separated terms — no explanation, no punctuation other than commas.

Query: {query}
Expanded terms:"""


class QueryExpansionStep:
    """Expands the query using an LLM before CLIP embedding."""

    def __init__(self, llm: ILLMProvider, max_tokens: int = 60) -> None:
        self._llm = llm
        self._max_tokens = max_tokens

    async def run(self, context: RAGContext) -> RAGContext:
        try:
            expanded = await self._llm.complete(
                messages=[
                    {"role": "user", "content": _EXPANSION_PROMPT.format(query=context.query)}
                ],
                temperature=0.3,
                max_tokens=self._max_tokens,
            )
            context.expanded_query = expanded.strip()
            log.debug("QueryExpansion: %r → %r", context.query, context.expanded_query)
        except Exception as exc:
            # Non-fatal: fall back to original query
            log.warning("QueryExpansion failed (%s) — using original query", exc)
            context.expanded_query = context.query
        return context
