"""
LLMGenerateStep — builds grounded context from retrieved/reranked items
and generates a natural language answer via the LLM provider.

Uses reranked list if populated, else retrieved.
"""

import logging

from rag.pipeline import RAGContext, RetrievedItem
from llm.base import ILLMProvider

log = logging.getLogger(__name__)

_SYSTEM_PROMPT = """You are a helpful assistant that answers questions about a personal media library.
You are given a list of relevant photos and videos retrieved from the library.
Answer the user's question based ONLY on the provided media context.
If the context is insufficient, say so clearly. Do not make up details."""


def _build_context(items: list[RetrievedItem]) -> str:
    lines = []
    for i, item in enumerate(items, 1):
        parts = [f"{i}. [{item.file_type}] {item.file_path} (similarity: {item.similarity:.3f})"]
        if item.caption:
            parts.append(f"   Caption: {item.caption}")
        if item.timestamp is not None:
            parts.append(f"   Timestamp: {item.timestamp:.1f}s")
        if item.rerank_score is not None:
            parts.append(f"   Relevance score: {item.rerank_score:.3f}")
        lines.append("\n".join(parts))
    return "\n\n".join(lines)


class LLMGenerateStep:
    """Generates the final answer from grounded media context."""

    def __init__(self, llm: ILLMProvider, model: str | None = None) -> None:
        self._llm = llm
        self._model = model

    async def run(self, context: RAGContext) -> RAGContext:
        items = context.reranked if context.reranked else context.retrieved
        if not items:
            context.answer = "No relevant media found for your query."
            return context

        media_context = _build_context(items)
        messages = [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    f"Media context:\n{media_context}\n\n"
                    f"Question: {context.query}"
                ),
            },
        ]
        try:
            context.answer = await self._llm.complete(
                messages=messages,
                model=self._model,
                temperature=0.3,
                max_tokens=1024,
            )
        except Exception as exc:
            context.error = f"LLMGenerateStep failed: {exc}"
            log.error("LLM generate error: %s", exc)
        return context
