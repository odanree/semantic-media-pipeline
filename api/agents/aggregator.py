"""
Aggregator — fuses outputs from all agents into a final LLM answer.
"""

from __future__ import annotations

import logging

from dependencies import get_llm_provider

log = logging.getLogger(__name__)

_SYSTEM = """You are a helpful media library assistant.
You have results from multiple specialized agents:
- SearchAgent: visually similar photos/videos
- MetadataAgent: files matching temporal/location filters
- VisionAgent: detailed descriptions of key frames

Synthesize all available information into a clear, grounded answer.
Do not invent details not supported by the provided context."""


async def build_final_answer(state: dict) -> str:
    parts = []

    if state.get("search_results"):
        lines = [f"  - {r['file_path']} (similarity: {r['similarity']:.3f})"
                 for r in state["search_results"][:5]]
        parts.append("Visual matches:\n" + "\n".join(lines))

    if state.get("metadata_results"):
        lines = [f"  - {r['file_path']} ({r.get('created_at', 'unknown date')})"
                 for r in state["metadata_results"][:5]]
        parts.append("Temporal matches:\n" + "\n".join(lines))

    if state.get("vision_results"):
        lines = [f"  - {r['file_path']}: {r['description']}"
                 for r in state["vision_results"]]
        parts.append("Visual analysis:\n" + "\n".join(lines))

    if not parts:
        return "No relevant media found for your query."

    context = "\n\n".join(parts)
    llm = get_llm_provider()

    try:
        return await llm.complete(
            messages=[
                {"role": "system", "content": _SYSTEM},
                {"role": "user", "content": f"Context:\n{context}\n\nQuery: {state['query']}"},
            ],
            temperature=0.3,
            max_tokens=1024,
        )
    except Exception as exc:
        log.error("Aggregator LLM call failed: %s", exc)
        return f"Found {len(state.get('search_results', []))} visual matches. LLM synthesis unavailable: {exc}"
