"""
VisionAgent — deep frame analysis via multimodal LLM.

Invoked conditionally (only when CLIP search returns sparse results).
Takes the top search result file paths, fetches them, and asks a
vision-capable model to describe what it sees in detail.

Compatible with: GPT-4o, GPT-4-vision, LLaVA (via Ollama), Moondream.
Falls back gracefully if the LLM provider doesn't support vision.
"""

from __future__ import annotations

import base64
import logging
import os
from pathlib import Path

from dependencies import get_llm_provider

log = logging.getLogger(__name__)

_VISION_SYSTEM = (
    "You are a visual analysis assistant. "
    "Describe the content of this image in detail: people, activities, setting, objects, mood."
)

# Only analyze up to N frames to control cost/latency
MAX_FRAMES_TO_ANALYZE = int(os.getenv("VISION_AGENT_MAX_FRAMES", "3"))


async def vision_agent_run(search_results: list[dict]) -> list[dict]:
    """
    For each of the top N results, fetch the file and ask the vision LLM
    to describe it. Returns list of {file_path, description}.
    """
    if not search_results:
        return []

    top_results = [
        r for r in search_results
        if r.get("file_type") == "image"
    ][:MAX_FRAMES_TO_ANALYZE]

    if not top_results:
        return []

    llm = get_llm_provider()
    descriptions = []

    for result in top_results:
        file_path = result["file_path"]
        description = await _analyze_frame(llm, file_path)
        if description:
            descriptions.append({"file_path": file_path, "description": description})

    return descriptions


async def _analyze_frame(llm, file_path: str) -> str | None:
    """Encode a local file as base64 and send to vision LLM."""
    try:
        path = Path(file_path)
        if not path.exists():
            return None

        with open(path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode()

        suffix = path.suffix.lower().lstrip(".")
        mime = f"image/{suffix}" if suffix in ("jpg", "jpeg", "png", "webp") else "image/jpeg"

        messages = [
            {"role": "system", "content": _VISION_SYSTEM},
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}},
                    {"type": "text", "text": "Describe this image in detail."},
                ],
            },
        ]
        return await llm.complete(messages=messages, max_tokens=256)
    except Exception as exc:
        log.warning("VisionAgent frame analysis failed (%s): %s", file_path, exc)
        return None
