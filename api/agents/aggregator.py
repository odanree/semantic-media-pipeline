"""
Aggregator — fuses outputs from all agents into a final LLM answer.
"""

from __future__ import annotations

import logging

from dependencies import get_llm_provider

log = logging.getLogger(__name__)

_SYSTEM = """You are a helpful media library assistant.
You have results from multiple specialized agents:
- SearchAgent: visually similar photos/videos (CLIP semantic search)
- MetadataAgent: files matching temporal/location filters
- AudioAgent: video frames matching audio characteristics (music, speech, events)
- VisionAgent: detailed descriptions of key frames

Synthesize all available information into a clear, grounded answer.
When audio results are present, use the transcript, segment type, and event labels
to answer questions about what is being said or heard in the media.
Do not invent details not supported by the provided context."""


def _format_audio_result(r: dict) -> str:
    path = r.get("file_path", "unknown")
    ts = r.get("timestamp")
    seg_type = r.get("audio_segment_type", "unknown")
    parts = [f"  - {path}"]
    if ts is not None:
        start = r.get("audio_segment_start_sec", ts)
        end = r.get("audio_segment_end_sec")
        span = f"{start:.1f}s–{end:.1f}s" if end else f"{ts:.1f}s"
        parts.append(f"    • Time: {span} | Type: {seg_type}")
    else:
        parts.append(f"    • Type: {seg_type}")
    if r.get("audio_event_top"):
        parts.append(f"    • Event: {r['audio_event_top']}")
    if r.get("audio_transcript"):
        transcript = r["audio_transcript"][:200]
        parts.append(f"    • Transcript: \"{transcript}\"")
    return "\n".join(parts)


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

    if state.get("audio_results"):
        lines = [_format_audio_result(r) for r in state["audio_results"][:10]]
        parts.append("Audio matches:\n" + "\n".join(lines))

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
