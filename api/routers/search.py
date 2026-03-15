"""
Search endpoint - Vector similarity search in Qdrant
"""

import os
import time
from collections import defaultdict
from typing import List, Optional

import numpy as np
import torch
from fastapi import APIRouter, HTTPException, Request
from rate_limit import limiter, LIMIT_SEARCH, LIMIT_SEARCH_VEC
from pydantic import BaseModel
from qdrant_client import QdrantClient
from qdrant_client.models import Filter, FieldCondition, MatchValue, ScrollRequest

router = APIRouter()

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
)


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

def _event_deduplicate(hits: list, window_s: float = EVENT_WINDOW_SECONDS) -> list:
    """
    From a single file's candidate frames, keep only one frame per temporal window
    using greedy non-maximum suppression (NMS).

    The highest-scoring frame is kept first, then any frame within window_s seconds
    of an already-kept frame is suppressed — regardless of fixed-grid bucket boundaries.
    This avoids the boundary artifact where frames at t=744s and t=748s land in adjacent
    5-second buckets and are both kept despite being only 4 seconds apart.

    Images (timestamp=None) are always kept — they have no temporal axis.
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
        audio_conditions = []
        if body.audio_segment_type is not None:
            audio_conditions.append(
                FieldCondition(key="audio_segment_type", match=MatchValue(value=body.audio_segment_type))
            )
        if body.audio_event_top is not None:
            audio_conditions.append(
                FieldCondition(key="audio_event_top", match=MatchValue(value=body.audio_event_top))
            )
        audio_filter = Filter(must=audio_conditions) if audio_conditions else None

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
            # Embed the text query using CLIP
            query_embedding = model.encode(body.query, convert_to_tensor=False)
            if isinstance(query_embedding, np.ndarray):
                query_vector = query_embedding.tolist()
            else:
                query_vector = query_embedding

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
            pass2_ms = (time.time() - t_p2) * 1000

            if body.dedup:
                # Group re-ranked candidates by file, apply per-file event NMS,
                # then merge, cap timelapse dirs, and trim to limit.
                file_groups: dict[str, list] = defaultdict(list)
                for hit in raw_points:
                    file_groups[hit.payload.get("file_path", "")].append(hit)

                all_hits = []
                for hits_in_file in file_groups.values():
                    all_hits.extend(_event_deduplicate(hits_in_file, window_s=EVENT_WINDOW_SECONDS))

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
