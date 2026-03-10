"""
RAG endpoint — Retrieve-Augment-Generate over your media library.

Flow:
  1. Retrieve  — CLIP-embed the question → query Qdrant for top-N media
  2. Augment   — Inject retrieved metadata (paths, types, timestamps, scores)
                 into an LLM prompt as grounded context
  3. Generate  — LLM reasons over that context and writes a natural answer

LLM backend is configurable via env vars so the same code works with
OpenAI (cloud) or a local model via Ollama / LM Studio (OpenAI-compatible):

  LLM_PROVIDER   = openai | local          (default: openai)
  OPENAI_API_KEY = sk-...                  (required for openai provider)
  LLM_MODEL      = gpt-4o-mini             (default; override for any model)
  LLM_BASE_URL   = http://localhost:11434/v1  (local provider only)

Phase-2 note:
  Once the worker stores AI-generated captions in the Qdrant payload
  (e.g., payload["caption"] from BLIP), the _build_context() helper
  below already handles an optional "caption" key — no other changes needed.
"""

import os
import time
from typing import Optional

import numpy as np
import torch
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from qdrant_client import QdrantClient
from openai import OpenAI, OpenAIError

from rate_limit import limiter, LIMIT_ASK
from routers.search import _event_deduplicate, SEARCH_GROUP_SIZE, EVENT_WINDOW_SECONDS

router = APIRouter()

# ---------------------------------------------------------------------------
# Qdrant client (mirrors search.py — shares the same collection)
# ---------------------------------------------------------------------------
QDRANT_HOST = os.getenv("QDRANT_HOST", "qdrant")
QDRANT_PORT = int(os.getenv("QDRANT_PORT", "6333"))
QDRANT_GRPC_PORT = int(os.getenv("QDRANT_GRPC_PORT", "6334"))
QDRANT_PREFER_GRPC = os.getenv("QDRANT_PREFER_GRPC", "true").lower() == "true"
QDRANT_COLLECTION_NAME = os.getenv("QDRANT_COLLECTION_NAME", "media_vectors")

qdrant_client = QdrantClient(
    host=QDRANT_HOST,
    port=QDRANT_PORT,
    grpc_port=QDRANT_GRPC_PORT,
    prefer_grpc=QDRANT_PREFER_GRPC,
)

# ---------------------------------------------------------------------------
# CLIP model (lazy-loaded; same logic as search.py)
# ---------------------------------------------------------------------------
_clip_model: Optional[object] = None


def _get_clip_model():
    global _clip_model
    if _clip_model is None:
        from sentence_transformers import SentenceTransformer
        model_name = os.getenv("CLIP_MODEL_NAME", "clip-ViT-L-14")
        device = "cuda" if torch.cuda.is_available() else "cpu"
        _clip_model = SentenceTransformer(model_name, device=device)
    return _clip_model


# ---------------------------------------------------------------------------
# LLM client (lazy-loaded; configurable for cloud or local)
# ---------------------------------------------------------------------------
_llm_client: Optional[OpenAI] = None
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "openai").lower()
LLM_MODEL = os.getenv("LLM_MODEL", "gpt-4o-mini")
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "")  # e.g. http://localhost:11434/v1


def _get_llm_client() -> OpenAI:
    global _llm_client
    if _llm_client is None:
        if LLM_PROVIDER == "local":
            if not LLM_BASE_URL:
                raise RuntimeError(
                    "LLM_BASE_URL must be set when LLM_PROVIDER=local "
                    "(e.g. http://localhost:11434/v1)"
                )
            _llm_client = OpenAI(
                base_url=LLM_BASE_URL,
                api_key="not-needed",  # local servers typically ignore the key
            )
        else:
            api_key = os.getenv("OPENAI_API_KEY", "")
            if not api_key:
                raise RuntimeError(
                    "OPENAI_API_KEY must be set when LLM_PROVIDER=openai"
                )
            _llm_client = OpenAI(api_key=api_key)
    return _llm_client


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------

class AskRequest(BaseModel):
    question: str
    limit: int = 10        # how many media items to retrieve from Qdrant
    threshold: float = 0.2  # minimum CLIP similarity to include in context
    dedup: bool = True     # apply temporal scene dedup before building context


class SourceResult(BaseModel):
    file_path: str
    file_type: str
    similarity: float
    frame_index: Optional[int] = None
    timestamp: Optional[float] = None
    caption: Optional[str] = None  # populated once worker stores BLIP captions


class AskResponse(BaseModel):
    question: str
    answer: str
    sources: list[SourceResult]
    model_used: str
    retrieval_count: int
    execution_time_ms: float
    scenes_collapsed: int = 0  # frames dropped by temporal windowing


# ---------------------------------------------------------------------------
# Context builder  (Augment step)
# ---------------------------------------------------------------------------

def _build_context(results: list[dict]) -> str:
    """
    Format Qdrant results into a numbered context block for the LLM prompt.

    Each entry includes the path (often encodes location/date/event in real
    libraries), file type, similarity score, and — if available — a caption
    generated by BLIP or similar model stored in the Qdrant payload.
    """
    if not results:
        return "No media was found in the library matching this query."

    lines = []
    for i, r in enumerate(results, start=1):
        path = r["file_path"] or "unknown"
        ftype = r["file_type"] or "unknown"
        score = r["similarity"]
        parts = [f"[{i}] {path} ({ftype}, similarity={score:.2f})"]

        if r.get("timestamp") is not None:
            parts.append(f"  • Timestamp in video: {r['timestamp']:.1f}s")
        if r.get("caption"):
            parts.append(f"  • Visual description: {r['caption']}")

        lines.append("\n".join(parts))

    return "\n\n".join(lines)


SYSTEM_PROMPT = """\
You are an intelligent assistant for a personal media library called Lumen.
You have been given a list of media files retrieved by a semantic visual search.
Each entry includes the file path, type (image or video), visual similarity score,
an optional timestamp (for video clips), and an optional visual description.

Your job:
1. Read the retrieved context carefully.
2. Answer the user's question based ONLY on the retrieved context.
3. If the context contains file paths with meaningful folder names or dates, use
   that to infer location, time period, or event — and say so.
4. If you cannot answer confidently from the context, say so honestly.
5. Keep the answer concise (2–4 sentences) and cite source numbers like [1], [2].
6. Do NOT invent visual details that are not in the context.
"""


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------

@router.post("/ask", response_model=AskResponse)
@limiter.limit(LIMIT_ASK)
async def ask_about_media(request: Request, body: AskRequest):
    """
    RAG endpoint — ask a natural language question about your media library.

    **Retrieve**: Semantically searches Qdrant for relevant media using CLIP.
    **Augment**: Injects the retrieved metadata into an LLM prompt as context.
    **Generate**: The LLM produces a grounded answer citing the source files.

    Example questions:
    - "What videos do I have from Vietnam?"
    - "Show me what the sunset footage looks like."
    - "Do I have any birthday party photos from 2024?"
    """
    if not body.question or not body.question.strip():
        raise HTTPException(status_code=400, detail="Question cannot be empty.")

    start = time.time()

    # ------------------------------------------------------------------
    # 1. RETRIEVE — CLIP embed the question, query Qdrant
    # ------------------------------------------------------------------
    try:
        clip = _get_clip_model()
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"CLIP model unavailable: {e}")

    query_vec = clip.encode(body.question, convert_to_tensor=False)
    if isinstance(query_vec, np.ndarray):
        query_vec = query_vec.tolist()

    try:
        if body.dedup:
            # Layer 1: Qdrant query_points_groups — one group per file
            groups_result = qdrant_client.query_points_groups(
                collection_name=QDRANT_COLLECTION_NAME,
                query=query_vec,
                group_by="file_path",
                limit=body.limit,
                group_size=SEARCH_GROUP_SIZE,
                score_threshold=body.threshold,
                with_payload=True,
            )
            raw_count = sum(len(g.hits) for g in groups_result.groups)

            # Layer 2: 5s event windowing per group
            all_hits = []
            for group in groups_result.groups:
                all_hits.extend(_event_deduplicate(group.hits, window_s=EVENT_WINDOW_SECONDS))
            all_hits.sort(key=lambda p: p.score, reverse=True)
            points = all_hits[:body.limit]
            scenes_collapsed = raw_count - len(points)
        else:
            points = qdrant_client.query_points(
                collection_name=QDRANT_COLLECTION_NAME,
                query=query_vec,
                limit=body.limit,
                with_payload=True,
                score_threshold=body.threshold,
            ).points
            scenes_collapsed = 0
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Qdrant query failed: {e}")

    results = [
        {
            "file_path": p.payload.get("file_path"),
            "file_type": p.payload.get("file_type"),
            "similarity": float(p.score),
            "frame_index": p.payload.get("frame_index"),
            "timestamp": p.payload.get("timestamp"),
            "caption": p.payload.get("caption"),  # None until Phase 2
        }
        for p in points
    ]

    # ------------------------------------------------------------------
    # 2. AUGMENT — build the grounded context block
    # ------------------------------------------------------------------
    context = _build_context(results)

    user_message = (
        f"Context — retrieved media:\n\n{context}\n\n"
        f"Question: {body.question}"
    )

    # ------------------------------------------------------------------
    # 3. GENERATE — call the LLM
    # ------------------------------------------------------------------
    try:
        llm = _get_llm_client()
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))

    try:
        completion = llm.chat.completions.create(
            model=LLM_MODEL,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user",   "content": user_message},
            ],
            temperature=0.3,   # low temp = factual, grounded answers
            max_tokens=512,
        )
        answer = completion.choices[0].message.content.strip()
    except OpenAIError as e:
        raise HTTPException(status_code=502, detail=f"LLM request failed: {e}")

    elapsed_ms = (time.time() - start) * 1000

    return AskResponse(
        question=body.question,
        answer=answer,
        sources=[SourceResult(**r) for r in results],
        model_used=LLM_MODEL,
        retrieval_count=len(results),
        execution_time_ms=round(elapsed_ms, 1),
        scenes_collapsed=scenes_collapsed,
    )
