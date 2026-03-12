"""
Observability & Processing Stats Router

Endpoints:
  GET /api/stats/summary    — pipeline health at a glance (status counts, error breakdown,
                               Qdrant vector count, cache hit ratio)
  GET /api/stats/processing — per-file timing, slowest jobs, hourly throughput,
                               re-index session detection
"""

import os
import time
from collections import Counter
from datetime import datetime, timedelta

import numpy as np

from fastapi import APIRouter, Query
from qdrant_client import QdrantClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

router = APIRouter()

# ---------------------------------------------------------------------------
# DB + Qdrant clients (sync — stats queries are fast, no need for async)
# ---------------------------------------------------------------------------

_engine = None


def _get_session():
    global _engine
    if _engine is None:
        _engine = create_engine(
            os.getenv(
                "DATABASE_URL",
                "postgresql://lumen_user:secure_password_here@postgres:5432/lumen",
            ),
            echo=False,
            pool_pre_ping=True,
        )
    return sessionmaker(bind=_engine)()


def _get_qdrant():
    return QdrantClient(
        host=os.getenv("QDRANT_HOST", "qdrant"),
        port=int(os.getenv("QDRANT_PORT", "6333")),
    )


# ---------------------------------------------------------------------------
# /api/stats/summary
# ---------------------------------------------------------------------------

@router.get("/stats/summary")
def processing_summary():
    """
    Pipeline health at a glance.

    Returns:
      - total files ingested + breakdown by status (done / processing / error / pending)
      - breakdown by file type (image / video)
      - top error messages (to surface systemic failures fast)
      - Qdrant vector count vs DB 'done' count (drift detection)
      - frame cache stats (hit count inferred from files with no extracted_at gap)
    """
    db = _get_session()
    try:
        # --- Status counts ---
        status_rows = db.execute(
            text("SELECT processing_status, COUNT(*) FROM media_files GROUP BY processing_status")
        ).fetchall()
        by_status = {row[0]: row[1] for row in status_rows}
        total = sum(by_status.values())

        # --- File type counts ---
        type_rows = db.execute(
            text("SELECT file_type, COUNT(*) FROM media_files GROUP BY file_type")
        ).fetchall()
        by_type = {row[0]: row[1] for row in type_rows}

        # --- Error breakdown (top 10 most common errors) ---
        error_rows = db.execute(
            text(
                """
                SELECT
                    LEFT(error_message, 120) AS error_snippet,
                    COUNT(*) AS occurrences
                FROM media_files
                WHERE processing_status = 'error'
                  AND error_message IS NOT NULL
                GROUP BY LEFT(error_message, 120)
                ORDER BY occurrences DESC
                LIMIT 10
                """
            )
        ).fetchall()
        top_errors = [{"error": row[0], "count": row[1]} for row in error_rows]

        # --- Stuck files (in 'processing' > 2 hours with no processed_at) ---
        # NOTE: created_at = discovery time, not processing start time.
        # Using created_at as a conservative proxy: a file stuck in 'processing'
        # for > 2 hours since it was first seen is almost certainly stalled.
        stuck_rows = db.execute(
            text(
                """
                SELECT COUNT(*) FROM media_files
                WHERE processing_status = 'processing'
                  AND processed_at IS NULL
                  AND created_at < NOW() - INTERVAL '2 hours'
                """
            )
        ).fetchone()
        stuck_count = stuck_rows[0] if stuck_rows else 0

        # --- Qdrant vector count ---
        qdrant_vectors = None
        qdrant_status = "ok"
        try:
            qdrant = _get_qdrant()
            collection_name = os.getenv("QDRANT_COLLECTION_NAME", "media_vectors")
            info = qdrant.get_collection(collection_name)
            # qdrant-client >= 1.7: attribute renamed vectors_count → points_count
            qdrant_vectors = getattr(info, "points_count", None) or getattr(info, "vectors_count", None)
        except Exception as e:
            qdrant_status = f"error: {str(e)}"

        done_count = by_status.get("done", 0)
        vector_drift = (
            (qdrant_vectors - done_count) if qdrant_vectors is not None else None
        )

        return {
            "generated_at": datetime.utcnow().isoformat(),
            "total_files": total,
            "by_status": by_status,
            "by_type": by_type,
            "stuck_processing": stuck_count,
            "qdrant": {
                "status": qdrant_status,
                "vector_count": qdrant_vectors,
                "db_done_count": done_count,
                # Expected: vectors > db_done_count (videos produce N frame vectors each).
                # Negative drift = DB records without vectors — indicates a pipeline gap
                # (files marked 'done' in Postgres but never upserted to Qdrant).
                "drift": vector_drift,
            },
            "top_errors": top_errors,
        }
    finally:
        db.close()


# ---------------------------------------------------------------------------
# /api/stats/processing
# ---------------------------------------------------------------------------

@router.get("/stats/processing")
def processing_times(
    hours: int = Query(default=720, ge=1, le=8760, description="Lookback window in hours (default 720 = 30 days)"),
    limit: int = Query(default=20, ge=1, le=200, description="Number of slowest jobs to return"),
):
    """
    Processing time analysis and throughput over time.

    Returns:
      - average / median / p95 processing time across all done files
      - slowest N individual files (useful for spotting large or corrupt files)
      - hourly throughput buckets (files completed per hour, last N hours)
      - re-index session detection: groups of files processed together
        (burst of activity separated by idle gaps > 10 min = a new session)
    """
    db = _get_session()
    try:
        since = datetime.utcnow() - timedelta(hours=hours)

        # --- Aggregate timing stats ---
        # Use embedding_ms (actual ML processing time) not processed_at - created_at
        # (which includes queue wait time and inflates to days).
        agg = db.execute(
            text(
                """
                SELECT
                    COUNT(*)                                                          AS total,
                    AVG(embedding_ms) / 1000.0                                        AS avg_secs,
                    PERCENTILE_CONT(0.5) WITHIN GROUP
                        (ORDER BY embedding_ms) / 1000.0                              AS median_secs,
                    PERCENTILE_CONT(0.95) WITHIN GROUP
                        (ORDER BY embedding_ms) / 1000.0                              AS p95_secs,
                    MIN(embedding_ms) / 1000.0                                        AS min_secs,
                    MAX(embedding_ms) / 1000.0                                        AS max_secs
                FROM media_files
                WHERE processing_status = 'done'
                  AND processed_at >= :since
                  AND embedding_ms IS NOT NULL
                """
            ),
            {"since": since},
        ).fetchone()

        timing_stats = {
            "total_completed": agg[0],
            "avg_secs": round(agg[1], 2) if agg[1] else None,
            "median_secs": round(agg[2], 2) if agg[2] else None,
            "p95_secs": round(agg[3], 2) if agg[3] else None,
            "min_secs": round(agg[4], 2) if agg[4] else None,
            "max_secs": round(agg[5], 2) if agg[5] else None,
        }

        # --- Slowest individual files ---
        slow_rows = db.execute(
            text(
                """
                SELECT
                    file_path,
                    file_type,
                    ROUND(embedding_ms / 1000.0, 1) AS duration_secs,
                    processed_at
                FROM media_files
                WHERE processing_status = 'done'
                  AND processed_at >= :since
                  AND embedding_ms IS NOT NULL
                ORDER BY embedding_ms DESC
                LIMIT :limit
                """
            ),
            {"since": since, "limit": limit},
        ).fetchall()

        slowest = [
            {
                "file_path": row[0],
                "file_type": row[1],
                "duration_secs": float(row[2]),
                "completed_at": row[3].isoformat() if row[3] else None,
            }
            for row in slow_rows
        ]

        # --- Hourly throughput ---
        hourly_rows = db.execute(
            text(
                """
                SELECT
                    DATE_TRUNC('hour', processed_at) AS hour,
                    COUNT(*)                         AS files_completed,
                    SUM(CASE WHEN file_type = 'video' THEN 1 ELSE 0 END) AS videos,
                    SUM(CASE WHEN file_type = 'image' THEN 1 ELSE 0 END) AS images
                FROM media_files
                WHERE processing_status = 'done'
                  AND processed_at >= :since
                GROUP BY 1
                ORDER BY 1 ASC
                """
            ),
            {"since": since},
        ).fetchall()

        hourly_throughput = [
            {
                "hour": row[0].isoformat() if row[0] else None,
                "files_completed": row[1],
                "videos": row[2],
                "images": row[3],
            }
            for row in hourly_rows
        ]

        # --- Re-index session detection ---
        # Fetch completed timestamps ordered by time, then group bursts
        # separated by gaps > 10 minutes into discrete "sessions".
        # Scoped to the same lookback window as the rest of this endpoint.
        ts_rows = db.execute(
            text(
                """
                SELECT processed_at
                FROM media_files
                WHERE processing_status = 'done'
                  AND processed_at IS NOT NULL
                  AND processed_at >= :since
                ORDER BY processed_at ASC
                """
            ),
            {"since": since},
        ).fetchall()

        sessions = []
        if ts_rows:
            GAP_THRESHOLD = timedelta(minutes=10)
            session_start = ts_rows[0][0]
            session_count = 1
            prev_ts = ts_rows[0][0]

            for row in ts_rows[1:]:
                ts = row[0]
                if ts - prev_ts > GAP_THRESHOLD:
                    sessions.append(
                        {
                            "started_at": session_start.isoformat(),
                            "ended_at": prev_ts.isoformat(),
                            "files_processed": session_count,
                            "duration_mins": round(
                                (prev_ts - session_start).total_seconds() / 60, 1
                            ),
                        }
                    )
                    session_start = ts
                    session_count = 1
                else:
                    session_count += 1
                prev_ts = ts

            # Close final session
            sessions.append(
                {
                    "started_at": session_start.isoformat(),
                    "ended_at": prev_ts.isoformat(),
                    "files_processed": session_count,
                    "duration_mins": round(
                        (prev_ts - session_start).total_seconds() / 60, 1
                    ),
                }
            )

        return {
            "generated_at": datetime.utcnow().isoformat(),
            "lookback_hours": hours,
            "timing": timing_stats,
            "slowest_files": slowest,
            "hourly_throughput": hourly_throughput,
            "indexing_sessions": {
                "total_sessions": len(sessions),
                "sessions": sessions[-10:],  # last 10 sessions
            },
        }
    finally:
        db.close()


# ---------------------------------------------------------------------------
# /api/stats/collection
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Semantic topic tags — CLIP-based vocabulary probe (replaces filename parsing)
# ---------------------------------------------------------------------------

# Human-readable concept labels that mirror the SEARCH_QUERIES used to build
# this collection.  At stats-call time we encode these once with CLIP, scroll
# a random sample of Qdrant vectors, assign each vector to its nearest label,
# and return the most-common k labels.  The vocabulary encode is cached for
# _TOPIC_CACHE_TTL seconds so the CLIP forward-pass only happens on cold start
# (or after expiry).
_TOPIC_VOCABULARY: list[str] = [
    # Sports
    "basketball dribbling on court",
    "soccer players on field",
    "tennis match rally",
    "running sprint on track",
    "swimming competition in pool",
    "gym weight training",
    "yoga stretching outdoors",
    "skateboard tricks in park",
    "boxing training punching bag",
    "cycling on mountain road",
    "golf swing on course",
    "martial arts sparring",
    "surfing ocean waves",
    "rock climbing wall",
    "dance performance on stage",
    # Nature
    "ocean waves crashing shore",
    "mountain hiking trail",
    "waterfall in forest",
    "golden sunset sky",
    "wildlife animals in nature",
    "aerial drone landscape",
    "snow covered winter scene",
    # City & lifestyle
    "busy city street traffic",
    "crowd walking downtown",
    "city lights at night",
    "outdoor market vendors",
    "people in coffee shop",
    "cooking in kitchen",
    "travel adventure exploration",
    # Office & work
    "team meeting in office",
    "people working at desk",
    "presentation in boardroom",
    "working from home",
    "colleagues at whiteboard",
    "modern coworking space",
    "coding on laptop",
]

# Module-level cache: vocabulary CLIP vectors only (DB sample is done fresh each call)
_topic_vecs_cache: "tuple[np.ndarray, float] | None" = None
_TOPIC_CACHE_TTL = 600.0  # 10 minutes — vocabulary is static so this is conservative


def _compute_topic_tags(k: int = 10) -> list[str]:
    """Return k topic labels representative of the collection content.

    Algorithm (all cheap after warm-up):
      1. Encode _TOPIC_VOCABULARY with CLIP (cached for 30 min).
      2. Scroll up to 200 vectors from Qdrant.  Since point IDs are random UUIDs,
         the first 200 in sorted-UUID order are a uniform sample across the
         collection — no explicit random offset needed.
      3. Compute cosine similarity between each sample vector and all topics.
      4. Assign each sample to its nearest topic; return the top-k by count.

    Falls back to the first k vocabulary entries on any error so the
    endpoint never returns an empty list.
    """
    global _topic_vecs_cache

    # --- Load model (already preloaded by main.py lifespan handler) ---
    try:
        from routers.search import get_clip_model
        model = get_clip_model()
    except Exception:
        return _TOPIC_VOCABULARY[:k]

    # --- Encode vocabulary (cached) ---
    now = time.monotonic()
    if _topic_vecs_cache is None or (now - _topic_vecs_cache[1]) > _TOPIC_CACHE_TTL:
        raw = model.encode(_TOPIC_VOCABULARY, convert_to_tensor=False)
        vecs = np.array(raw, dtype="float32")
        norms = np.linalg.norm(vecs, axis=1, keepdims=True)
        vecs /= np.where(norms == 0, 1.0, norms)
        _topic_vecs_cache = (vecs, now)

    topic_vecs, _ = _topic_vecs_cache  # shape [N_topics, D]

    # --- Sample random point IDs from DB, then retrieve from Qdrant ---
    # ORDER BY RANDOM() gives a true uniform sample across ALL indexed files,
    # not just the first N in UUID sort order (which biases toward the earliest
    # ingested content and ignores newer additions).
    try:
        db = _get_session()
        try:
            id_rows = db.execute(
                text(
                    "SELECT qdrant_point_id FROM media_files "
                    "WHERE processing_status = 'done' AND qdrant_point_id IS NOT NULL "
                    "ORDER BY RANDOM() LIMIT 400"
                )
            ).fetchall()
        finally:
            db.close()
    except Exception:
        return _TOPIC_VOCABULARY[:k]

    if not id_rows:
        return _TOPIC_VOCABULARY[:k]

    point_ids = [str(r[0]) for r in id_rows]

    try:
        qdrant = _get_qdrant()
        collection_name = os.getenv("QDRANT_COLLECTION_NAME", "media_vectors")
        retrieved = qdrant.retrieve(
            collection_name=collection_name,
            ids=point_ids,
            with_vectors=True,
        )
    except Exception:
        return _TOPIC_VOCABULARY[:k]

    if not retrieved:
        return _TOPIC_VOCABULARY[:k]

    # --- Build sample matrix ---
    sample_vecs = np.array(
        [p.vector for p in retrieved if p.vector is not None],
        dtype="float32",
    )
    if sample_vecs.ndim != 2 or len(sample_vecs) == 0:
        return _TOPIC_VOCABULARY[:k]

    # L2-normalise for cosine sim via dot product
    norms = np.linalg.norm(sample_vecs, axis=1, keepdims=True)
    sample_vecs /= np.where(norms == 0, 1.0, norms)

    # Cosine similarity: [n_samples, n_topics]
    sims = sample_vecs @ topic_vecs.T

    # Nearest topic per sample → frequency count
    nearest = np.argmax(sims, axis=1)
    counts: Counter = Counter(int(i) for i in nearest)

    return [_TOPIC_VOCABULARY[idx] for idx, _ in counts.most_common(k)]


@router.get("/stats/collection")
def collection_info():
    """
    Collection summary for demo context UI.

    Returns file counts by type/status, caption coverage from Qdrant, and
    topic tags derived from CLIP semantic similarity.
    """
    db = _get_session()
    try:
        rows = db.execute(
            text(
                "SELECT file_type, processing_status, COUNT(*) "
                "FROM media_files GROUP BY file_type, processing_status"
            )
        ).fetchall()

        by_type: dict = {}
        total = 0
        indexed = 0
        for ftype, status, count in rows:
            by_type[ftype] = by_type.get(ftype, 0) + count
            total += count
            if status == "done":
                indexed += count

        # Caption coverage — query Qdrant for frames that have a caption payload
        vector_points: int | None = None
        captioned_count: int | None = None
        caption_pct: float | None = None
        try:
            from qdrant_client.http import models as qmodels
            qdrant = _get_qdrant()
            collection_name = os.getenv("QDRANT_COLLECTION_NAME", "media_vectors")
            vector_points = qdrant.get_collection(collection_name).points_count
            captioned_result = qdrant.count(
                collection_name=collection_name,
                count_filter=qmodels.Filter(
                    must_not=[
                        qmodels.IsEmptyCondition(
                            is_empty=qmodels.PayloadField(key="caption")
                        )
                    ]
                ),
                exact=True,
            )
            captioned_count = captioned_result.count
            caption_pct = round(
                (captioned_count / vector_points * 100) if vector_points else 0, 1
            )
        except Exception:
            pass  # degrade gracefully — caption stats are non-critical

        # Semantic topic tags: CLIP-based vocabulary probe (see _compute_topic_tags)
        try:
            top_topics = _compute_topic_tags(k=10)
        except Exception:
            top_topics = []

        return {
            "total": total,
            "indexed": indexed,
            "percent_indexed": round((indexed / total * 100) if total else 0, 1),
            "by_type": by_type,
            "topic_tags": top_topics,
            "vector_points": vector_points,
            "captioned_count": captioned_count,
            "caption_pct": caption_pct,
        }
    finally:
        db.close()
