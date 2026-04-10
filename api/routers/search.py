"""
Search endpoint - Vector similarity search in Qdrant
"""

import asyncio
import hashlib
import os
import time
import uuid
from collections import defaultdict
from datetime import datetime
from typing import List, Optional

import numpy as np
import redis
import torch
from celery import Celery
from fastapi import APIRouter, HTTPException, Request
from rate_limit import limiter, LIMIT_SEARCH, LIMIT_SEARCH_VEC, LIMIT_SCENE_BOUNDS
from pydantic import BaseModel
from qdrant_client import QdrantClient
from qdrant_client.models import Filter, FieldCondition, MatchValue, ScrollRequest, Range, PointIdsList
from sqlalchemy import insert

from db.models import VoteEvent, get_async_engine

router = APIRouter()

# Celery client for dispatching background tasks
_celery_broker = os.getenv("CELERY_BROKER_URL", "redis://redis:6379/0")
celery_app = Celery(broker=_celery_broker, backend=_celery_broker)

# Initialize Qdrant client
QDRANT_HOST = os.getenv("QDRANT_HOST", "qdrant")
QDRANT_PORT = int(os.getenv("QDRANT_PORT", "6333"))
QDRANT_GRPC_PORT = int(os.getenv("QDRANT_GRPC_PORT", "6334"))
QDRANT_PREFER_GRPC = os.getenv("QDRANT_PREFER_GRPC", "true").lower() == "true"
QDRANT_COLLECTION_NAME = os.getenv("QDRANT_COLLECTION_NAME", "media_vectors")

# ---------------------------------------------------------------------------
# Temporal deduplication config
# ---------------------------------------------------------------------------
SEARCH_GROUP_SIZE = int(os.getenv("SEARCH_GROUP_SIZE", "3"))
EVENT_WINDOW_SECONDS = float(os.getenv("EVENT_WINDOW_SECONDS", "5"))
# Max images returned from the same parent directory (prevents timelapse JPG floods).
# Set MAX_IMAGES_PER_DIR=0 to disable.
MAX_IMAGES_PER_DIR = int(os.getenv("MAX_IMAGES_PER_DIR", "2"))
# Minimum number of images from the same dir before the cap activates.
# Dirs contributing fewer images than this are never capped — regular photo
# series (e.g. 3 vacation shots in the same folder) pass through untouched.
TIMELAPSE_FLOOD_THRESHOLD = int(os.getenv("TIMELAPSE_FLOOD_THRESHOLD", "4"))

# ---------------------------------------------------------------------------
# 2-Pass Re-ranker config
# ---------------------------------------------------------------------------
# Pass 1 fetches limit * RERANKER_OVERSAMPLE candidates from Qdrant (ANN).
# Pass 2 re-ranks them with exact cosine on the API server.
# Set RERANKER_OVERSAMPLE=1 to disable re-ranking (pass-through mode).
RERANKER_OVERSAMPLE = int(os.getenv("RERANKER_OVERSAMPLE", "5"))

qdrant_client = QdrantClient(
    host=QDRANT_HOST,
    port=QDRANT_PORT,
    grpc_port=QDRANT_GRPC_PORT,
    prefer_grpc=QDRANT_PREFER_GRPC,
    timeout=60,
)

# ---------------------------------------------------------------------------
# Query embedding cache (Redis)
# ---------------------------------------------------------------------------
_EMBED_CACHE_TTL = int(os.getenv("EMBED_CACHE_TTL", str(7 * 24 * 3600)))  # 7 days

_redis_url = (
    os.getenv("CELERY_BROKER_URL")
    or os.getenv("REDIS_URL", "redis://lumen-redis:6379")
)
# Strip Celery DB suffix if present (e.g. redis://host:6379/0 → db 0 is fine)
try:
    _embed_cache: Optional[redis.Redis] = redis.Redis.from_url(_redis_url, decode_responses=False)
    _embed_cache.ping()
except Exception:
    _embed_cache = None


def _cache_key(query: str) -> str:
    digest = hashlib.sha256(query.lower().strip().encode()).hexdigest()[:24]
    return f"clip_emb:{digest}"


def _get_query_embedding(query: str, model) -> list:
    """Encode query with CLIP, using Redis cache to skip re-encoding repeated queries."""
    if _embed_cache is not None:
        key = _cache_key(query)
        try:
            cached = _embed_cache.get(key)
            if cached is not None:
                return np.frombuffer(cached, dtype=np.float32).tolist()
        except Exception:
            pass  # cache read failure → fall through to encode

    embedding = model.encode(query, convert_to_tensor=False)
    vec = embedding if isinstance(embedding, np.ndarray) else np.array(embedding, dtype=np.float32)

    if _embed_cache is not None:
        try:
            _embed_cache.set(key, vec.astype(np.float32).tobytes(), ex=_EMBED_CACHE_TTL)
        except Exception:
            pass  # cache write failure is non-fatal

    return vec.tolist()


# Initialize CLIP embedder (lazy-loaded)
_clip_model: Optional[object] = None
EMBEDDER_AVAILABLE = False


def _get_device() -> str:
    """Detect best available compute device."""
    try:
        import torch_directml
        torch.zeros(1, device=torch_directml.device())
        return "cpu"  # DirectML not available in API container, use CPU
    except Exception:
        pass

    if torch.cuda.is_available():
        return "cuda"

    return "cpu"


def get_clip_model():
    """Get or create the CLIP model instance (lazy loading)."""
    global _clip_model, EMBEDDER_AVAILABLE

    if _clip_model is None:
        try:
            # Import SentenceTransformer (should work now that accelerate.py is patched)
            from sentence_transformers import SentenceTransformer

            model_name = os.getenv("CLIP_MODEL_NAME", "clip-ViT-L-14")
            device = _get_device()
            print(f"Loading {model_name} on device: {device}")
            _clip_model = SentenceTransformer(model_name, device=device)
            EMBEDDER_AVAILABLE = True
            print("✓ CLIP embedder loaded successfully")
        except Exception as e:
            print(f"✗ Failed to load CLIP embedder: {e}")
            import traceback
            traceback.print_exc()
            EMBEDDER_AVAILABLE = False
            raise

    return _clip_model


class SearchRequest(BaseModel):
    """Search request model"""

    query: str
    limit: int = 20
    threshold: float = 0.2
    dedup: bool = True  # False = raw frames (A/B comparison / debug mode)
    # --- Segment-level audio filters ---
    audio_segment_type: Optional[str] = None  # speech | non_verbal | music | ambient | event | silence
    audio_event_top: Optional[str] = None     # e.g. "Scream" — AudioSet top label
    # --- Construction phase filter ---
    construction_phase: Optional[str] = None  # e.g. "Phase 3: Rough MEP"
    # --- Custom label filter ---
    label: Optional[str] = None              # e.g. "UNC" — custom drive/collection label
    # --- Unvoted filter ---
    exclude_voted: bool = False              # True = only return frames with no user_vote
    # --- Re-ranker ---
    oversample: Optional[int] = None  # override RERANKER_OVERSAMPLE for this request


class SearchResult(BaseModel):
    """Individual search result"""

    file_path: str
    file_type: str
    similarity: float
    frame_index: int = None
    timestamp: float = None
    scene_window_start: Optional[float] = None  # start of the 5s dedup bucket
    scene_window_end: Optional[float] = None    # end of the 5s dedup bucket


class SearchResponse(BaseModel):
    """Search response model"""

    query: str
    results: list
    count: int
    execution_time_ms: float
    scenes_collapsed: int = 0       # frames dropped by temporal windowing
    raw_frame_count: int = 0        # total frames Qdrant returned before dedup
    # Re-ranker diagnostics
    reranker_candidates: int = 0    # oversample pool size fed into Pass 2
    pass1_ms: float = 0.0           # Qdrant ANN search time
    pass2_ms: float = 0.0           # exact cosine re-rank time


# ---------------------------------------------------------------------------
# Temporal deduplication helpers
# ---------------------------------------------------------------------------

def _window_deduplicate(hits: list, window_s: float = EVENT_WINDOW_SECONDS) -> list:
    """
    Fallback dedup for frames that have no audio_segment_index.

    Greedy NMS: keep the highest-scoring frame, suppress any frame within
    window_s seconds of an already-kept frame. Images (timestamp=None) always pass.
    """
    hits = sorted(hits, key=lambda h: h.score, reverse=True)

    kept_timestamps: list[float] = []
    results = []
    for hit in hits:
        ts = hit.payload.get("timestamp")
        if ts is None:              # image — no temporal axis, always keep
            results.append(hit)
            continue
        if any(abs(ts - kept_ts) < window_s for kept_ts in kept_timestamps):
            continue
        kept_timestamps.append(float(ts))
        results.append(hit)
    return results


def _segment_deduplicate(hits: list) -> list:
    """
    Keep one frame per audio segment per file.

    Hits must be pre-sorted descending by score (Pass 2 cosine re-rank guarantees
    this). The first frame encountered for each (file_path, audio_segment_index)
    pair is the highest-scoring one and is kept; all subsequent frames from the
    same segment are suppressed.

    Frames that carry no audio_segment_index (images, or videos ingested before
    audio analysis was added) are collected and handled by _window_deduplicate as
    a fallback, preserving the original NMS behaviour for that media.
    """
    seen: set[tuple] = set()
    fallback: list = []
    results: list = []

    for hit in hits:  # pre-sorted: highest score first
        seg_idx = hit.payload.get("audio_segment_index")
        if seg_idx is None:
            fallback.append(hit)
            continue
        key = (hit.payload.get("file_path", ""), seg_idx)
        if key not in seen:
            seen.add(key)
            results.append(hit)

    # Fallback: window NMS grouped per file (identical semantics to the old path)
    if fallback:
        file_groups: dict[str, list] = defaultdict(list)
        for hit in fallback:
            file_groups[hit.payload.get("file_path", "")].append(hit)
        for hits_in_file in file_groups.values():
            results.extend(_window_deduplicate(hits_in_file))

    return results


def _dir_cap_images(
    hits: list,
    max_per_dir: int = MAX_IMAGES_PER_DIR,
    flood_threshold: int = TIMELAPSE_FLOOD_THRESHOLD,
) -> list:
    """
    Cap images from the same parent directory, but ONLY when that directory
    contributes >= flood_threshold images to the result set.

    This targets timelapse/burst-shot floods (DJI TIMELAPSE_0688.JPG …
    TIMELAPSE_0750.JPG all in the same folder) without accidentally suppressing
    a normal photo series where a user took 2-3 different shots in one album.

    Algorithm (two-pass):
      Pass 1 — count how many images each directory contributes.
      Pass 2 — if a dir's count >= flood_threshold, cap it at max_per_dir
               (keeping the best-scoring frames, since hits are score-sorted).
               Dirs below the threshold pass through entirely.

    Video frames are never touched — temporal dedup handles them.
    Set max_per_dir=0 to disable entirely.
    """
    if max_per_dir <= 0:
        return hits

    # Pass 1: count images per directory
    dir_total: dict[str, int] = {}
    for hit in hits:
        if hit.payload.get("timestamp") is not None:
            continue  # video
        parent = os.path.dirname(hit.payload.get("file_path", ""))
        dir_total[parent] = dir_total.get(parent, 0) + 1

    # Pass 2: apply cap only to flooded directories
    dir_kept: dict[str, int] = {}
    results = []
    for hit in hits:
        ts = hit.payload.get("timestamp")
        if ts is not None:          # video frame — pass through
            results.append(hit)
            continue
        parent = os.path.dirname(hit.payload.get("file_path", ""))
        if dir_total.get(parent, 0) < flood_threshold:
            # Normal photo series — never cap
            results.append(hit)
            continue
        # Timelapse/burst flood — apply cap
        if dir_kept.get(parent, 0) >= max_per_dir:
            continue
        dir_kept[parent] = dir_kept.get(parent, 0) + 1
        results.append(hit)
    return results


# ---------------------------------------------------------------------------
# Re-ranker helper
# ---------------------------------------------------------------------------

def _cosine_rerank(points: list, query_vector: list) -> list:
    """
    Re-rank a list of Qdrant ScoredPoints by exact cosine similarity.

    Replaces each point's ANN score with the exact dot-product cosine score
    computed from the stored 768-dim vector vs the query vector.
    Points are returned sorted descending by exact score.

    Requires points to have been fetched with with_vectors=True.
    Points missing a vector (should never happen) keep their original score.
    """
    if not points:
        return points

    q = np.array(query_vector, dtype=np.float32)
    q_norm = np.linalg.norm(q)
    if q_norm == 0:
        return points

    vecs = []
    valid_idx = []
    for i, p in enumerate(points):
        if p.vector is not None:
            vecs.append(p.vector)
            valid_idx.append(i)

    if not vecs:
        return points

    V = np.array(vecs, dtype=np.float32)                    # (N, D)
    norms = np.linalg.norm(V, axis=1)                       # (N,)
    scores = (V @ q) / (norms * q_norm + 1e-8)              # (N,) exact cosine

    for idx, score in zip(valid_idx, scores):
        points[idx].score = float(score)

    points.sort(key=lambda p: p.score, reverse=True)
    return points


# ---------------------------------------------------------------------------
# Vote re-ranking helper
# ---------------------------------------------------------------------------
VOTE_BOOST = 0.08      # thumbs up   → up to +0.08 (scaled by vote_label similarity)
VOTE_PENALTY = 0.12    # thumbs down → -0.12 (asymmetric: penalty > boost)


def _apply_vote_adjustment(points: list, query: Optional[str] = None) -> list:
    """
    Apply soft re-ranking based on user votes.

    Boost is proportional to the similarity score stored in vote_label[query]:
    - Exact query match:  +VOTE_BOOST * vote_label[query]  (e.g. 0.08 * 0.95 = 0.076)
    - Liked for different query or no label: +VOTE_BOOST * 0.5 (half boost)
    - vote == -1: -VOTE_PENALTY (flat)
    - vote == 0 or absent: no adjustment

    Clamps adjusted scores to [0, 1] to preserve cosine range.
    Points are re-sorted by adjusted score.
    """
    for p in points:
        vote = p.payload.get("user_vote")
        if vote == 1:
            vote_label = p.payload.get("vote_label") or {}
            if query and query in vote_label:
                boost = VOTE_BOOST * float(vote_label[query])
            else:
                boost = VOTE_BOOST * 0.5
            p.score = min(1.0, float(p.score) + boost)
        elif vote == -1:
            p.score = max(0.0, float(p.score) - VOTE_PENALTY)

    points.sort(key=lambda p: p.score, reverse=True)
    return points


async def _log_vote_event(
    batch_id: str,
    file_path: str,
    audio_segment_index: Optional[int],
    vote: int,
    search_query: Optional[str],
    vote_source: str,
    patched_count: int,
) -> None:
    """
    Log vote event to PostgreSQL for observability.
    Runs async and non-blocking (fire-and-forget).
    """
    try:
        engine = await get_async_engine()
        async with engine.begin() as conn:
            # Insert one row per vote source/action
            await conn.execute(
                insert(VoteEvent).values(
                    batch_id=uuid.UUID(batch_id),
                    file_path=file_path,
                    audio_segment_index=audio_segment_index,
                    vote=vote,
                    search_query=search_query,
                    vote_source=vote_source,
                    timestamp=datetime.utcnow(),
                    cascaded_count=0,  # Will be updated if this is a seed vote
                )
            )
    except Exception as e:
        # Log observability errors but don't fail the vote endpoint
        print(f"Failed to log vote event: {e}")


@router.get("/search-status")
async def search_status():
    """
    Health check for search service - verify Qdrant is reachable
    """
    try:
        collections = qdrant_client.get_collections()
        collection_count = len(collections.collections)
        collection_names = [c.name for c in collections.collections]

        return {
            "status": "healthy",
            "qdrant_host": QDRANT_HOST,
            "qdrant_port": QDRANT_PORT,
            "collection_count": collection_count,
            "collections": collection_names,
            "target_collection": QDRANT_COLLECTION_NAME,
            "target_collection_exists": QDRANT_COLLECTION_NAME in collection_names,
        }
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Qdrant connection failed: {str(e)}")


@router.post("/search", response_model=SearchResponse)
@limiter.limit(LIMIT_SEARCH)
async def search_media(request: Request, body: SearchRequest):
    """
    Search for media using text query.

    Embeds the text query using CLIP and searches Qdrant for similar embeddings.
    By default (dedup=true) applies two-layer temporal deduplication so that
    `limit` means distinct scenes, not individual frames.

    Args:
        query: Text search query
        limit: Maximum number of results / distinct scenes (default: 20)
        threshold: Minimum similarity threshold 0-1 (default: 0.2)
        dedup: Enable scene deduplication (default: true). Set false for raw frames.

    Returns:
        List of matching media with similarity scores
    """
    try:
        start_time = time.time()

        # Build optional audio payload filter first so we can decide whether
        # an empty query is valid (filter-only browse is allowed).
        filter_conditions = []
        if body.audio_segment_type is not None:
            filter_conditions.append(
                FieldCondition(key="audio_segment_type", match=MatchValue(value=body.audio_segment_type))
            )
        if body.audio_event_top is not None:
            filter_conditions.append(
                FieldCondition(key="audio_event_top", match=MatchValue(value=body.audio_event_top))
            )
        if body.construction_phase is not None:
            filter_conditions.append(
                FieldCondition(key="construction_phase", match=MatchValue(value=body.construction_phase))
            )
        if body.label is not None:
            filter_conditions.append(
                FieldCondition(key="label", match=MatchValue(value=body.label))
            )
        must_not_conditions = []
        if body.exclude_voted:
            # Exclude any point that has user_vote set (1 or -1).
            # IsNullCondition requires a special index; must_not+range works with the existing integer index.
            must_not_conditions.append(
                FieldCondition(key="user_vote", range=Range(gte=-1, lte=1))
            )
        audio_filter = Filter(
            must=filter_conditions if filter_conditions else None,
            must_not=must_not_conditions if must_not_conditions else None,
        ) if (filter_conditions or must_not_conditions) else None

        filter_only = audio_filter is not None and not body.query.strip()

        # Reject empty queries only when there are no audio filters to fall back on
        if not body.query.strip() and not filter_only:
            raise HTTPException(status_code=400, detail="Query cannot be empty")

        # Load CLIP model (lazy-loaded on first use); skip for filter-only requests
        if not filter_only:
            try:
                model = get_clip_model()
            except Exception as e:
                raise HTTPException(
                    status_code=503,
                    detail=f"CLIP embedder failed to load: {str(e)}"
                )

        pass1_ms = 0.0
        pass2_ms = 0.0
        reranker_candidates = 0

        if filter_only:
            # No query — scroll by filter only, no similarity threshold
            scroll_result, _ = qdrant_client.scroll(
                collection_name=QDRANT_COLLECTION_NAME,
                scroll_filter=audio_filter,
                limit=body.limit,
                with_payload=True,
            )
            # Attach a dummy score so downstream code is uniform
            for point in scroll_result:
                point.score = 1.0
            final_hits = scroll_result
            raw_frame_count = len(final_hits)
            scenes_collapsed = 0
        else:
            # Embed the text query using CLIP (Redis-cached)
            query_vector = _get_query_embedding(body.query, model)

            # When audio filters are active alongside a query, drop the threshold
            # so filter-matching frames aren't excluded by similarity alone.
            effective_threshold = 0.0 if audio_filter else body.threshold

            oversample = body.oversample if body.oversample is not None else RERANKER_OVERSAMPLE
            oversample_limit = body.limit * max(1, oversample)

            # ------------------------------------------------------------------
            # Pass 1: Qdrant ANN search with oversampling.
            # Fetch oversample_limit candidates with their stored vectors so
            # Pass 2 can re-rank without a second round-trip to Qdrant.
            # ------------------------------------------------------------------
            t_p1 = time.time()
            raw_points = qdrant_client.query_points(
                collection_name=QDRANT_COLLECTION_NAME,
                query=query_vector,
                limit=oversample_limit,
                with_payload=True,
                with_vectors=True,
                score_threshold=effective_threshold,
                query_filter=audio_filter,
            ).points
            pass1_ms = (time.time() - t_p1) * 1000
            reranker_candidates = len(raw_points)
            raw_frame_count = reranker_candidates

            # ------------------------------------------------------------------
            # Pass 2: Exact cosine re-ranking on the candidate pool.
            # Sub-millisecond for ≤500 candidates on CPU (pure numpy matmul).
            # ------------------------------------------------------------------
            t_p2 = time.time()
            raw_points = _cosine_rerank(raw_points, query_vector)
            # Apply vote-based soft re-ranking (additive boost/penalty)
            raw_points = _apply_vote_adjustment(raw_points, query=body.query)
            pass2_ms = (time.time() - t_p2) * 1000

            if body.dedup:
                # One frame per audio segment (or per 5 s window for legacy media),
                # then timelapse dir-cap, then trim to limit.
                # raw_points is already score-sorted descending from Pass 2.
                all_hits = _segment_deduplicate(raw_points)
                all_hits.sort(key=lambda p: p.score, reverse=True)
                all_hits = _dir_cap_images(all_hits)
                final_hits = all_hits[:body.limit]
                scenes_collapsed = raw_frame_count - len(final_hits)
            else:
                # dedup=false — raw frame mode (A/B comparison / debug)
                final_hits = raw_points[:body.limit]
                scenes_collapsed = 0

        # Build response dicts from whichever path was taken
        results = []
        for point in final_hits:
            payload = point.payload
            ts = payload.get("timestamp")
            window_start = (
                float(int(ts // EVENT_WINDOW_SECONDS) * EVENT_WINDOW_SECONDS)
                if ts is not None and body.dedup else None
            )
            window_end = (window_start + EVENT_WINDOW_SECONDS) if window_start is not None else None
            results.append({
                "id": point.id,
                "file_path": payload.get("file_path"),
                "file_type": payload.get("file_type"),
                "similarity": float(point.score),
                "frame_index": payload.get("frame_index"),
                "timestamp": ts,
                "scene_window_start": window_start,
                "scene_window_end": window_end,
                "updated_at": payload.get("updated_at"),
                # Clip boundary fields — None for legacy media ingested before audio analysis
                "audio_segment_index": payload.get("audio_segment_index"),
                "audio_segment_start_sec": payload.get("audio_segment_start_sec"),
                "audio_segment_end_sec": payload.get("audio_segment_end_sec"),
                "audio_rms_energy": payload.get("audio_rms_energy"),
                "construction_phase": payload.get("construction_phase"),
                "phase_confidence": payload.get("phase_confidence"),
                "label": payload.get("label"),
                "user_vote": payload.get("user_vote"),
                "vote_label": payload.get("vote_label"),
            })

        execution_time_ms = (time.time() - start_time) * 1000

        return SearchResponse(
            query=body.query,
            results=results,
            count=len(results),
            execution_time_ms=execution_time_ms,
            scenes_collapsed=scenes_collapsed,
            raw_frame_count=raw_frame_count,
            reranker_candidates=reranker_candidates,
            pass1_ms=round(pass1_ms, 2),
            pass2_ms=round(pass2_ms, 2),
        )

    except HTTPException:
        raise
    except Exception as e:
        print(f"Search error: {e}")
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Search failed: {str(e)}")


class LookupRequest(BaseModel):
    file_path: str
    timestamp: Optional[float] = None  # None for images


@router.post("/lookup")
@limiter.limit(LIMIT_SEARCH)
async def lookup_frame(request: Request, body: LookupRequest):
    """
    Direct frame lookup by file_path + timestamp.

    Skips CLIP inference entirely — scrolls Qdrant for the exact stored point
    matching the given file and timestamp, returns it as a single search result.
    Used by the > shortcut in the frontend.
    """
    source_conditions = [FieldCondition(key="file_path", match=MatchValue(value=body.file_path))]

    if body.timestamp is not None:
        windowed_conditions = source_conditions + [
            FieldCondition(
                key="timestamp",
                range=Range(gte=body.timestamp - 5.0, lte=body.timestamp + 5.0),
            )
        ]
        windowed_result, _ = qdrant_client.scroll(
            collection_name=QDRANT_COLLECTION_NAME,
            scroll_filter=Filter(must=windowed_conditions),
            limit=10,
            with_payload=True,
            with_vectors=False,
        )
    else:
        windowed_result = []

    if windowed_result:
        candidates = windowed_result
    else:
        candidates, _ = qdrant_client.scroll(
            collection_name=QDRANT_COLLECTION_NAME,
            scroll_filter=Filter(must=source_conditions),
            limit=20,
            with_payload=True,
            with_vectors=False,
        )

    if not candidates:
        raise HTTPException(status_code=404, detail="File not found in index")

    if body.timestamp is not None and len(candidates) > 1:
        point = min(candidates, key=lambda p: abs((p.payload.get("timestamp") or 0) - body.timestamp))
    else:
        point = candidates[0]

    payload = point.payload
    ts = payload.get("timestamp")
    result = {
        "id": str(point.id),
        "file_path": payload.get("file_path"),
        "file_type": payload.get("file_type"),
        "similarity": 1.0,
        "frame_index": payload.get("frame_index"),
        "timestamp": ts,
        "scene_window_start": None,
        "scene_window_end": None,
        "updated_at": payload.get("updated_at"),
        "audio_segment_index": payload.get("audio_segment_index"),
        "audio_segment_start_sec": payload.get("audio_segment_start_sec"),
        "audio_segment_end_sec": payload.get("audio_segment_end_sec"),
        "audio_rms_energy": payload.get("audio_rms_energy"),
        "construction_phase": payload.get("construction_phase"),
        "phase_confidence": payload.get("phase_confidence"),
        "label": payload.get("label"),
        "user_vote": payload.get("user_vote"),
        "vote_label": payload.get("vote_label"),
    }

    return {
        "query": f">{body.file_path}" + (f"@{body.timestamp}" if body.timestamp is not None else ""),
        "results": [result],
        "count": 1,
        "execution_time_ms": 0,
        "scenes_collapsed": 0,
        "raw_frame_count": 1,
    }


class SimilarRequest(BaseModel):
    file_path: str
    timestamp: Optional[float] = None   # None for images
    limit: int = 10                     # distinct files to return
    threshold: float = 0.5             # higher default — want genuinely similar
    label: Optional[str] = None        # restrict results to this label


class VoteRequest(BaseModel):
    file_path: str
    audio_segment_index: Optional[int] = None  # None for images or unsegmented videos
    vote: int  # 1 (thumbs up), -1 (thumbs down), or 0 (clear)
    search_query: Optional[str] = None  # Context: what query led to this vote
    batch_id: Optional[str] = None  # For bulk votes: inherit from seed vote


@router.post("/similar")
@limiter.limit(LIMIT_SEARCH)
async def find_similar(request: Request, body: SimilarRequest):
    """
    Find videos/images visually similar to a specific frame.

    Looks up the stored CLIP embedding for the given file_path + timestamp,
    then searches Qdrant for nearest neighbours grouped by file — so each
    result is a distinct video/image, not individual frames.
    No CLIP model inference required (reuses the stored vector).
    """
    start_time = time.time()

    # 1. Find the source frame's point ID using a tight timestamp window.
    # A ±5s range filter narrows scroll to frames near the target timestamp,
    # so even 2-hour videos (3600 frames at 0.5fps) return the right frame
    # without scrolling the entire file. Falls back to file-only scroll for
    # images (no timestamp) or if the windowed scroll returns nothing.
    source_conditions = [FieldCondition(key="file_path", match=MatchValue(value=body.file_path))]

    if body.timestamp is not None:
        windowed_conditions = source_conditions + [
            FieldCondition(
                key="timestamp",
                range=Range(gte=body.timestamp - 5.0, lte=body.timestamp + 5.0),
            )
        ]
        windowed_result, _ = qdrant_client.scroll(
            collection_name=QDRANT_COLLECTION_NAME,
            scroll_filter=Filter(must=windowed_conditions),
            limit=10,
            with_payload=True,
            with_vectors=False,
        )
    else:
        windowed_result = []

    if windowed_result:
        scroll_result = windowed_result
    else:
        # Fallback: no timestamp or no frames in window — scroll first page
        scroll_result, _ = qdrant_client.scroll(
            collection_name=QDRANT_COLLECTION_NAME,
            scroll_filter=Filter(must=source_conditions),
            limit=20,
            with_payload=True,
            with_vectors=False,
        )

    if not scroll_result:
        raise HTTPException(status_code=404, detail="File not found in index")

    # Pick the closest timestamp match among the returned frames
    if body.timestamp is not None and len(scroll_result) > 1:
        source_point = min(
            scroll_result,
            key=lambda p: abs((p.payload.get("timestamp") or 0) - body.timestamp),
        )
    else:
        source_point = scroll_result[0]

    # 2. Search using point ID as query — Qdrant resolves the vector server-side,
    # no client-side vector transfer needed.
    must_not_conditions = [FieldCondition(key="file_path", match=MatchValue(value=body.file_path))]
    must_conditions = []
    if body.label is not None:
        must_conditions.append(FieldCondition(key="label", match=MatchValue(value=body.label)))
    exclude_source = Filter(
        must=must_conditions if must_conditions else None,
        must_not=must_not_conditions,
    )

    # Oversample so per-file grouping still yields `limit` distinct files.
    # 5× is enough — no re-ranking needed here (scores come directly from Qdrant).
    oversample_limit = body.limit * 5

    raw_points = qdrant_client.query_points(
        collection_name=QDRANT_COLLECTION_NAME,
        query=source_point.id,
        limit=oversample_limit,
        with_payload=True,
        with_vectors=False,
        score_threshold=body.threshold,
        query_filter=exclude_source,
    ).points

    # 3. Group by file — keep best-scoring frame per file
    file_best: dict = {}
    for point in raw_points:
        fp = point.payload.get("file_path", "")
        score = float(point.score)
        if fp not in file_best or score > file_best[fp]["best_similarity"]:
            file_best[fp] = {
                "file_path": fp,
                "file_type": point.payload.get("file_type", ""),
                "best_similarity": score,
                "best_timestamp": point.payload.get("timestamp"),
                "best_frame_index": point.payload.get("frame_index"),
                "audio_rms_energy": point.payload.get("audio_rms_energy"),
                "user_vote": point.payload.get("user_vote"),
                "vote_label": point.payload.get("vote_label"),
            }

    # Apply vote-based soft re-ranking after grouping by file
    for result in file_best.values():
        vote = result.get("user_vote")
        if vote == 1:
            vote_label = result.get("vote_label") or {}
            weight = max(vote_label.values(), default=1.0)
            result["best_similarity"] = min(1.0, float(result["best_similarity"]) + VOTE_BOOST * weight)
        elif vote == -1:
            result["best_similarity"] = max(0.0, float(result["best_similarity"]) - VOTE_PENALTY)

    results = sorted(file_best.values(), key=lambda x: x["best_similarity"], reverse=True)[:body.limit]

    return {
        "source_file": body.file_path,
        "source_timestamp": body.timestamp,
        "results": results,
        "count": len(results),
        "execution_time_ms": round((time.time() - start_time) * 1000, 2),
    }


@router.post("/vote")
@limiter.limit(LIMIT_SEARCH)
async def set_vote(request: Request, body: VoteRequest):
    """
    Set or clear a user vote (thumbs up/down) on a file_path + audio segment.

    Vote is stored as a payload field (user_vote: 1 | -1) in Qdrant points
    matching the file_path and optionally audio_segment_index. A vote of 0 clears
    the vote. Votes persist across sessions and influence ranking in both /search
    and /similar endpoints.

    Observability: Each vote is logged to vote_events table with lineage tracking
    (batch_id links seed upvote to bulk cascade). Enables queries like:
    - "How many frames were labeled from this upvote?"
    - "What search queries generated labels?"
    """
    try:
        # Generate or inherit batch_id for lineage tracking
        batch_id = body.batch_id or str(uuid.uuid4())

        # Build filter for file_path + audio_segment_index (if provided)
        conditions = [FieldCondition(key="file_path", match=MatchValue(value=body.file_path))]
        if body.audio_segment_index is not None:
            conditions.append(
                FieldCondition(key="audio_segment_index", match=MatchValue(value=body.audio_segment_index))
            )

        # Scroll all points matching the criteria
        scroll_result, _ = qdrant_client.scroll(
            collection_name=QDRANT_COLLECTION_NAME,
            scroll_filter=Filter(must=conditions),
            limit=10000,
            with_payload=True,
            with_vectors=False,
        )

        if not scroll_result:
            raise HTTPException(status_code=404, detail="Scene not found in index")

        point_ids = [point.id for point in scroll_result]
        points_selector = PointIdsList(points=point_ids)

        if body.vote == 0:
            # Clear active vote signal only; vote_label history is permanent
            qdrant_client.delete_payload(
                collection_name=QDRANT_COLLECTION_NAME,
                keys=["user_vote"],
                points=points_selector,
            )
        else:
            # Build updated vote_label dict: {query: score} — append, don't overwrite
            current_label_dict = {}
            if scroll_result[0].payload:
                current_label_dict = scroll_result[0].payload.get("vote_label") or {}
            vote_payload: dict = {"user_vote": body.vote}
            if body.search_query and not body.search_query.startswith(">"):
                updated_labels = dict(current_label_dict)
                if body.vote in (1, -1):
                    updated_labels[body.search_query] = float(body.vote)
                elif body.vote == 0 and body.search_query in updated_labels:
                    del updated_labels[body.search_query]
                vote_payload["vote_label"] = updated_labels
            qdrant_client.set_payload(
                collection_name=QDRANT_COLLECTION_NAME,
                payload=vote_payload,
                points=points_selector,
            )

        # Log vote event for observability (async, non-blocking)
        asyncio.create_task(
            _log_vote_event(
                batch_id=batch_id,
                file_path=body.file_path,
                audio_segment_index=body.audio_segment_index,
                vote=body.vote,
                search_query=body.search_query,
                vote_source="bulk_upvote" if body.batch_id else "manual",
                patched_count=len(point_ids),
            )
        )

        # Auto-cascade upvote to visually similar frames (≥90% similarity)
        if body.vote == 1 and not body.batch_id:
            celery_app.send_task(
                "tasks.cascade_votes",
                args=[body.file_path, batch_id, 0.9, body.search_query],
                queue="gpu",
            )

        return {
            "patched": len(point_ids),
            "file_path": body.file_path,
            "audio_segment_index": body.audio_segment_index,
            "vote": body.vote,
            "batch_id": batch_id,
        }

    except HTTPException:
        raise
    except Exception as e:
        import grpc
        if isinstance(e, grpc.RpcError) and e.code() == grpc.StatusCode.DEADLINE_EXCEEDED:
            # Qdrant segment optimizer held a write lock; retry once after a short wait
            import asyncio as _aio
            await _aio.sleep(2)
            try:
                scroll_result, _ = qdrant_client.scroll(
                    collection_name=QDRANT_COLLECTION_NAME,
                    scroll_filter=Filter(must=conditions),
                    limit=10000,
                    with_payload=True,
                    with_vectors=False,
                )
                if scroll_result:
                    point_ids = [point.id for point in scroll_result]
                    points_selector = PointIdsList(points=point_ids)
                    if body.vote == 0:
                        qdrant_client.delete_payload(
                            collection_name=QDRANT_COLLECTION_NAME,
                            keys=["user_vote"],
                            points=points_selector,
                        )
                    else:
                        current_label_dict = {}
                        if scroll_result[0].payload:
                            current_label_dict = scroll_result[0].payload.get("vote_label") or {}
                        vote_payload: dict = {"user_vote": body.vote}
                        if body.search_query and not body.search_query.startswith(">"):
                            updated_labels = dict(current_label_dict)
                            if body.vote in (1, -1):
                                updated_labels[body.search_query] = float(body.vote)
                            elif body.vote == 0 and body.search_query in updated_labels:
                                del updated_labels[body.search_query]
                            vote_payload["vote_label"] = updated_labels
                        qdrant_client.set_payload(
                            collection_name=QDRANT_COLLECTION_NAME,
                            payload=vote_payload,
                            points=points_selector,
                        )
                    return {
                        "patched": len(point_ids),
                        "file_path": body.file_path,
                        "audio_segment_index": body.audio_segment_index,
                        "vote": body.vote,
                        "batch_id": batch_id,
                    }
            except Exception as retry_err:
                print(f"Vote retry also failed: {retry_err}")
                raise HTTPException(status_code=503, detail="Qdrant busy — try again in a moment")
        print(f"Vote error: {e}")
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Vote failed: {str(e)}")


class BulkVoteRequest(BaseModel):
    """Bulk upvote results from similar search (seed vote → cascade)"""
    file_paths: List[str]
    audio_segment_indices: List[Optional[int]]
    vote: int  # 1, -1, 0
    search_query: str  # Inherited from seed upvote
    batch_id: str  # Same batch as seed vote (links cascade)


@router.post("/vote/bulk")
@limiter.limit(LIMIT_SEARCH)
async def bulk_set_vote(request: Request, body: BulkVoteRequest):
    """
    Bulk upvote multiple results from similar search with lineage tracking.

    Workflow:
    1. User searches "labubu" → finds frame F
    2. User upvotes F (creates batch_id_seed)
    3. User runs similar(F) → gets 20 results at 90%+ similarity
    4. User calls bulk_vote(all 20 results, batch_id=batch_id_seed, search_query="labubu")
    5. All 20 get auto-labeled "labubu" via query_label field

    This endpoint logs all votes with the same batch_id, enabling queries:
    - "How many frames did this 1 upvote generate?" (count votes with batch_id)
    - "What queries are being auto-labeled?" (group by search_query)

    Args:
        file_paths: List of file paths to upvote
        audio_segment_indices: List of audio segment indices (parallel to file_paths)
        vote: 1 (up), -1 (down), 0 (clear)
        search_query: Query that led to this cascade (e.g., "labubu")
        batch_id: Seed vote's batch_id (links to original upvote)

    Returns:
        Total frames patched + breakdown per file
    """
    try:
        if len(body.file_paths) != len(body.audio_segment_indices):
            raise HTTPException(
                status_code=400,
                detail="file_paths and audio_segment_indices must have same length"
            )

        total_patched = 0
        file_breakdown = {}

        for file_path, audio_segment_index in zip(body.file_paths, body.audio_segment_indices):
            # Call set_vote directly (avoid HTTP roundtrip)
            try:
                conditions = [FieldCondition(key="file_path", match=MatchValue(value=file_path))]
                if audio_segment_index is not None:
                    conditions.append(
                        FieldCondition(key="audio_segment_index", match=MatchValue(value=audio_segment_index))
                    )

                scroll_result, _ = qdrant_client.scroll(
                    collection_name=QDRANT_COLLECTION_NAME,
                    scroll_filter=Filter(must=conditions),
                    limit=10000,
                    with_payload=True,
                    with_vectors=False,
                )

                if scroll_result:
                    point_ids = [point.id for point in scroll_result]
                    points_selector = PointIdsList(points=point_ids)

                    if body.vote == 0:
                        qdrant_client.delete_payload(
                            collection_name=QDRANT_COLLECTION_NAME,
                            keys=["user_vote"],
                            points=points_selector,
                        )
                    else:
                        qdrant_client.set_payload(
                            collection_name=QDRANT_COLLECTION_NAME,
                            payload={"user_vote": body.vote},
                            points=points_selector,
                        )

                    patched = len(point_ids)
                    total_patched += patched
                    file_breakdown[file_path] = patched

                    # Log bulk vote event
                    asyncio.create_task(
                        _log_vote_event(
                            batch_id=body.batch_id,
                            file_path=file_path,
                            audio_segment_index=audio_segment_index,
                            vote=body.vote,
                            search_query=body.search_query,
                            vote_source="bulk_upvote",
                            patched_count=patched,
                        )
                    )

            except Exception as e:
                print(f"Bulk vote error for {file_path}: {e}")
                # Continue with next file instead of failing entire batch
                continue

        return {
            "batch_id": body.batch_id,
            "total_patched": total_patched,
            "breakdown": file_breakdown,
            "search_query": body.search_query,
        }

    except HTTPException:
        raise
    except Exception as e:
        print(f"Bulk vote error: {e}")
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Bulk vote failed: {str(e)}")


# ─────────────────────────────────────────────────────────────────────────────
# Observability Endpoints: Query vote lineage and stats
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/votes/batch/{batch_id}")
async def get_vote_batch_stats(batch_id: str):
    """
    Get statistics for a vote batch.
    Shows: total votes in batch, breakdown by search_query, cascaded count.

    Example:
      GET /api/votes/batch/550e8400-e29b-41d4-a716-446655440000
      → {
          "batch_id": "550e8400-...",
          "total_votes": 21,
          "seed_vote": {"vote": 1, "search_query": "labubu", "timestamp": "2026-03-29..."},
          "cascaded_votes": 20,
          "breakdown_by_query": {"labubu": 21},
          "created_at": "2026-03-29..."
        }
    """
    try:
        engine = await get_async_engine()
        async with engine.begin() as conn:
            from sqlalchemy import select, func

            # Get all votes in batch
            result = await conn.execute(
                select(
                    func.count().label("total_votes"),
                    VoteEvent.vote,
                    VoteEvent.search_query,
                    VoteEvent.timestamp,
                    VoteEvent.vote_source,
                ).where(
                    VoteEvent.batch_id == uuid.UUID(batch_id)
                ).group_by(VoteEvent.vote, VoteEvent.search_query, VoteEvent.timestamp, VoteEvent.vote_source)
            )

            rows = result.fetchall()
            if not rows:
                return {"batch_id": batch_id, "votes": 0, "error": "Batch not found"}

            total_votes = sum(row[0] for row in rows)
            cascaded_count = sum(row[0] for row in rows if row[4] == "bulk_upvote")

            return {
                "batch_id": batch_id,
                "total_votes": total_votes,
                "seed_votes": sum(1 for row in rows if row[4] == "manual"),
                "cascaded_votes": cascaded_count,
                "breakdown": {
                    "by_query": dict((row[2], row[0]) for row in rows if row[2]),
                    "by_source": dict((row[4], sum(r[0] for r in rows if r[4] == row[4])) for row in rows),
                }
            }

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get batch stats: {str(e)}")


@router.get("/votes/stats")
async def get_vote_stats(
    query: Optional[str] = None,
    days: int = 7,
):
    """
    Get vote statistics across all batches.

    Query parameters:
      - query: Filter by search_query (e.g., "labubu")
      - days: Look back N days (default: 7)

    Returns:
      {
        "total_votes": 500,
        "total_batches": 50,
        "avg_cascade_ratio": 19.5,  # avg votes per seed
        "top_queries": [
          {"query": "labubu", "votes": 150, "batches": 8, "avg_cascade": 18.75},
          {"query": "toy", "votes": 100, "batches": 5, "avg_cascade": 20}
        ]
      }
    """
    try:
        engine = await get_async_engine()
        async with engine.begin() as conn:
            from sqlalchemy import select, func
            from datetime import timedelta

            cutoff = datetime.utcnow() - timedelta(days=days)

            # Get total and per-query stats
            result = await conn.execute(
                select(
                    VoteEvent.search_query,
                    func.count(VoteEvent.batch_id).label("batch_count"),
                    func.count(VoteEvent.id).label("vote_count"),
                ).where(
                    VoteEvent.timestamp >= cutoff
                ).group_by(VoteEvent.search_query).order_by(
                    func.count(VoteEvent.id).desc()
                ).limit(20)
            )

            rows = result.fetchall()
            total_votes = sum(row[2] for row in rows)
            total_batches = sum(row[1] for row in rows)

            return {
                "period_days": days,
                "total_votes": total_votes,
                "total_batches": total_batches,
                "avg_cascade_ratio": total_votes / max(total_batches, 1),
                "top_queries": [
                    {
                        "query": row[0] or "(no query)",
                        "batches": row[1],
                        "votes": row[2],
                        "avg_cascade": row[2] / row[1] if row[1] > 0 else 0,
                    }
                    for row in rows
                ]
            }

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get stats: {str(e)}")


@router.post("/search-vector")
@limiter.limit(LIMIT_SEARCH_VEC)
async def search_by_vector(
    request: Request,
    vector: List[float],
    limit: int = 20,
    threshold: float = 0.3
):
    """
    Search Qdrant using a pre-computed embedding vector.

    This endpoint is useful when you already have a vector embedding
    and just need to search Qdrant.

    Args:
        vector: Pre-computed embedding vector
        limit: Maximum number of results
        threshold: Minimum similarity threshold

    Returns:
        List of matching media with similarity scores
    """
    try:
        start_time = time.time()

        if not vector:
            raise ValueError("Vector cannot be empty")

        # Search Qdrant using query_points (qdrant-client v1.7+ API)
        search_result = qdrant_client.query_points(
            collection_name=QDRANT_COLLECTION_NAME,
            query=vector,
            limit=limit,
            with_payload=True,
            score_threshold=threshold,
        ).points

        # Process results
        results = []
        for point in search_result:
            payload = point.payload
            result = {
                "id": point.id,
                "file_path": payload.get("file_path"),
                "file_type": payload.get("file_type"),
                "similarity": float(point.score),
                "frame_index": payload.get("frame_index"),
                "timestamp": payload.get("timestamp"),
                "audio_segment_index": payload.get("audio_segment_index"),
                "user_vote": payload.get("user_vote"),
            }
            results.append(result)

        execution_time_ms = (time.time() - start_time) * 1000

        return {
            "vector_dimension": len(vector),
            "results": results,
            "count": len(results),
            "execution_time_ms": execution_time_ms,
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


class SceneBoundsRequest(BaseModel):
    file_path: str
    timestamp: float
    threshold: float = 0.75
    window_sec: float = 60.0  # max look-ahead/behind from anchor


@router.post("/scene-bounds")
@limiter.limit(LIMIT_SCENE_BOUNDS)
async def scene_bounds(request: Request, body: SceneBoundsRequest):
    """
    Find natural scene boundaries around a keyframe using frame-similarity walk.

    Fetches all stored frame vectors for the file within ±window_sec of the
    anchor timestamp, then walks outward from the anchor frame computing cosine
    similarity.  Stops when similarity drops below threshold.

    Returns the timestamp of the last in-bounds frame on each side — giving
    scene-aware clip bounds instead of a fixed ±N second window.
    """
    anchor_filter = Filter(must=[
        FieldCondition(key="file_path", match=MatchValue(value=body.file_path)),
        FieldCondition(
            key="timestamp",
            range=Range(
                gte=body.timestamp - body.window_sec,
                lte=body.timestamp + body.window_sec,
            ),
        ),
    ])

    frames = []
    offset = None
    while True:
        batch, next_offset = qdrant_client.scroll(
            collection_name=QDRANT_COLLECTION_NAME,
            scroll_filter=anchor_filter,
            limit=500,
            offset=offset,
            with_vectors=True,
            with_payload=["timestamp"],
        )
        frames.extend(batch)
        if next_offset is None:
            break
        offset = next_offset

    if not frames:
        raise HTTPException(status_code=404, detail="No frames found near timestamp")

    frames.sort(key=lambda p: (p.payload or {}).get("timestamp", 0))
    timestamps = [float((p.payload or {}).get("timestamp", 0)) for p in frames]
    vectors = [np.array(p.vector, dtype=np.float32) for p in frames]

    # Find the frame closest to the requested timestamp
    anchor_idx = min(range(len(timestamps)), key=lambda i: abs(timestamps[i] - body.timestamp))
    anchor_vec = vectors[anchor_idx]
    anchor_norm = np.linalg.norm(anchor_vec)
    if anchor_norm > 0:
        anchor_vec = anchor_vec / anchor_norm

    # Walk backward — last frame still above threshold is scene start
    start_idx = anchor_idx
    for i in range(anchor_idx - 1, -1, -1):
        v = vectors[i]
        norm = np.linalg.norm(v)
        sim = float(np.dot(anchor_vec, v / norm)) if norm > 0 else 0.0
        if sim < body.threshold:
            break
        start_idx = i

    # Walk forward — last frame still above threshold is scene end
    end_idx = anchor_idx
    for i in range(anchor_idx + 1, len(vectors)):
        v = vectors[i]
        norm = np.linalg.norm(v)
        sim = float(np.dot(anchor_vec, v / norm)) if norm > 0 else 0.0
        if sim < body.threshold:
            break
        end_idx = i

    return {
        "start_sec": timestamps[start_idx],
        "end_sec": timestamps[end_idx],
        "anchor_sec": timestamps[anchor_idx],
        "frames_scanned": len(frames),
    }


class FrameLabelsRequest(BaseModel):
    file_path: str
    timestamp: Optional[float] = None
    top_k: int = 5


@router.post("/frame-labels")
@limiter.limit(LIMIT_SEARCH)
async def get_frame_labels(request: Request, body: FrameLabelsRequest):
    """
    Reverse-lookup the most semantically matching labels for a stored frame.

    Looks up the frame's stored CLIP image vector in Qdrant, then scores it
    against all distinct search_query values seen in vote_events (the vocabulary).
    Returns top-K labels ranked by cosine similarity (image vec vs text vec).

    No CLIP inference on the image — reuses the stored vector.
    Text embeddings are Redis-cached via _get_query_embedding.
    """
    # 1. Fetch the frame's stored vector from Qdrant
    source_conditions = [FieldCondition(key="file_path", match=MatchValue(value=body.file_path))]

    if body.timestamp is not None:
        windowed_result, _ = qdrant_client.scroll(
            collection_name=QDRANT_COLLECTION_NAME,
            scroll_filter=Filter(must=source_conditions + [
                FieldCondition(
                    key="timestamp",
                    range=Range(gte=body.timestamp - 5.0, lte=body.timestamp + 5.0),
                )
            ]),
            limit=10,
            with_payload=False,
            with_vectors=True,
        )
    else:
        windowed_result = []

    if windowed_result:
        scroll_result = windowed_result
    else:
        scroll_result, _ = qdrant_client.scroll(
            collection_name=QDRANT_COLLECTION_NAME,
            scroll_filter=Filter(must=source_conditions),
            limit=20,
            with_payload=False,
            with_vectors=True,
        )

    if not scroll_result:
        raise HTTPException(status_code=404, detail="File not found in index")

    if body.timestamp is not None and len(scroll_result) > 1:
        source_point = min(
            scroll_result,
            key=lambda p: abs(((p.payload or {}).get("timestamp") or 0) - body.timestamp),
        )
    else:
        source_point = scroll_result[0]

    if not source_point.vector:
        raise HTTPException(status_code=500, detail="Frame has no stored vector")

    image_vec = np.array(source_point.vector, dtype=np.float32)
    image_norm = np.linalg.norm(image_vec)
    if image_norm == 0:
        raise HTTPException(status_code=500, detail="Frame vector is zero")
    image_vec = image_vec / image_norm

    # 2. Pull vocabulary: distinct non-null search_query values from vote_events
    try:
        engine = await get_async_engine()
        async with engine.begin() as conn:
            from sqlalchemy import select
            result = await conn.execute(
                select(VoteEvent.search_query)
                .where(VoteEvent.search_query.isnot(None))
                .where(~VoteEvent.search_query.startswith(">"))
                .distinct()
            )
            vocabulary = [row[0] for row in result.fetchall()]
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to load vocabulary: {str(e)}")

    if not vocabulary:
        return {"labels": [], "vocabulary_size": 0}

    # 3. Encode vocabulary terms via CLIP (Redis-cached per term)
    model = get_clip_model()
    text_vecs = np.array(
        [_get_query_embedding(term, model) for term in vocabulary],
        dtype=np.float32,
    )  # shape: (N, D)

    # Row-normalise text vectors
    norms = np.linalg.norm(text_vecs, axis=1, keepdims=True)
    norms = np.where(norms == 0, 1.0, norms)
    text_vecs = text_vecs / norms

    # 4. Cosine similarity: image_vec (D,) · text_vecs (N, D)^T → (N,)
    scores = text_vecs @ image_vec

    top_k = min(body.top_k, len(vocabulary))
    top_indices = np.argpartition(scores, -top_k)[-top_k:]
    top_indices = top_indices[np.argsort(scores[top_indices])[::-1]]

    return {
        "labels": [
            {"label": vocabulary[i], "score": round(float(scores[i]), 4)}
            for i in top_indices
        ],
        "vocabulary_size": len(vocabulary),
    }
