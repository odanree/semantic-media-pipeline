"""
AudioAgent — Qdrant audio filter queries.

Parses audio intent from the query (segment type, speech, music, events)
and returns matching frames with full audio metadata for LLM context.
"""

from __future__ import annotations

import logging
import re

from dependencies import get_qdrant, get_collection_name

log = logging.getLogger(__name__)

# Keywords that map to segment_type values
_SEGMENT_TYPE_KEYWORDS: dict[str, str] = {
    "music": "music",
    "singing": "music",
    "song": "music",
    "melody": "music",
    "speech": "speech",
    "talking": "speech",
    "speaking": "speech",
    "dialogue": "speech",
    "conversation": "speech",
    "narration": "speech",
    "ambient": "ambient",
    "background noise": "ambient",
    "nature sounds": "ambient",
    "event": "event",
    "sound effect": "event",
    "explosion": "event",
    "gunshot": "event",
    "applause": "event",
    "crowd": "event",
    "silence": "silence",
    "quiet": "silence",
    "silent": "silence",
}

# Keywords indicating speech is required regardless of segment type
_SPEECH_KEYWORDS = {"speaking", "talking", "speech", "dialogue", "conversation",
                    "narration", "interview", "monologue", "voice"}

# Language hints to surface in transcript context
_LANGUAGE_HINTS = {"vietnamese", "english", "french", "spanish", "japanese",
                   "korean", "chinese", "thai", "tagalog"}


def extract_audio_filters(query: str) -> dict:
    """Extract audio filter parameters from a natural language query."""
    q = query.lower()
    filters: dict = {}

    # Detect segment type
    for keyword, seg_type in _SEGMENT_TYPE_KEYWORDS.items():
        if keyword in q:
            filters["segment_type"] = seg_type
            break

    # Detect speech requirement (overrides non-speech segment_type)
    if any(k in q for k in _SPEECH_KEYWORDS):
        filters["has_speech"] = True
        if "segment_type" not in filters:
            filters["segment_type"] = "speech"

    # Detect language hints for transcript filtering
    for lang in _LANGUAGE_HINTS:
        if lang in q:
            filters["language_hint"] = lang
            break

    return filters


async def audio_agent_run(query: str, limit: int = 20) -> list[dict]:
    """
    Scroll Qdrant by audio filters extracted from the query.
    Returns frames with full audio metadata for LLM context.
    """
    filters = extract_audio_filters(query)
    if not filters:
        return []

    try:
        from qdrant_client.models import Filter, FieldCondition, MatchValue

        must_conditions = []

        if "segment_type" in filters:
            must_conditions.append(
                FieldCondition(
                    key="audio_segment_type",
                    match=MatchValue(value=filters["segment_type"]),
                )
            )
        elif "has_speech" in filters:
            must_conditions.append(
                FieldCondition(key="audio_has_speech", match=MatchValue(value=True))
            )

        if not must_conditions:
            return []

        qdrant = get_qdrant()
        scroll_filter = Filter(must=must_conditions)

        points, _ = qdrant.scroll(
            collection_name=get_collection_name(),
            scroll_filter=scroll_filter,
            limit=limit,
            with_payload=True,
        )

        results = []
        for p in points:
            payload = p.payload or {}
            result = {
                "file_path": payload.get("file_path"),
                "file_type": payload.get("file_type"),
                "timestamp": payload.get("timestamp"),
                "audio_segment_type": payload.get("audio_segment_type"),
                "audio_has_speech": payload.get("audio_has_speech"),
                "audio_transcript": payload.get("audio_transcript"),
                "audio_transcript_words": payload.get("audio_transcript_words"),
                "audio_event_top": payload.get("audio_event_top"),
                "audio_event_labels": payload.get("audio_event_labels"),
                "audio_rms_energy": payload.get("audio_rms_energy"),
                "audio_segment_start_sec": payload.get("audio_segment_start_sec"),
                "audio_segment_end_sec": payload.get("audio_segment_end_sec"),
            }

            # Apply language hint post-filter on transcript
            if "language_hint" in filters and result.get("audio_transcript"):
                # Surface results with transcripts more prominently (keep all,
                # but flag for aggregator to prioritise)
                result["language_hint_match"] = filters["language_hint"]

            results.append(result)

        log.info(
            "[AudioAgent] query=%r filters=%r → %d results",
            query, filters, len(results),
        )
        return results

    except Exception as exc:
        log.error("AudioAgent query failed: %s", exc)
        return []
