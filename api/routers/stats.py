"""
Observability & Processing Stats Router

Endpoints:
  GET /api/stats/summary    — pipeline health at a glance (status counts, error breakdown,
                               Qdrant vector count, cache hit ratio)
  GET /api/stats/processing — per-file timing, slowest jobs, hourly throughput,
                               re-index session detection
"""

import os
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Query
from qdrant_client import QdrantClient
from sqlalchemy import create_engine, func, text
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

        # --- Stuck files (processing status older than 30 minutes) ---
        stuck_rows = db.execute(
            text(
                """
                SELECT COUNT(*) FROM media_files
                WHERE processing_status = 'processing'
                  AND created_at < NOW() - INTERVAL '30 minutes'
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
            qdrant_vectors = info.vectors_count
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
                # Positive = more vectors than DB records (e.g. videos have N frames each)
                # Negative = DB records without vectors (pipeline gap)
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
    hours: int = Query(default=24, ge=1, le=720, description="Lookback window in hours"),
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
        agg = db.execute(
            text(
                """
                SELECT
                    COUNT(*)                                                          AS total,
                    AVG(EXTRACT(EPOCH FROM (processed_at - created_at)))              AS avg_secs,
                    PERCENTILE_CONT(0.5) WITHIN GROUP
                        (ORDER BY EXTRACT(EPOCH FROM (processed_at - created_at)))   AS median_secs,
                    PERCENTILE_CONT(0.95) WITHIN GROUP
                        (ORDER BY EXTRACT(EPOCH FROM (processed_at - created_at)))   AS p95_secs,
                    MIN(EXTRACT(EPOCH FROM (processed_at - created_at)))              AS min_secs,
                    MAX(EXTRACT(EPOCH FROM (processed_at - created_at)))              AS max_secs
                FROM media_files
                WHERE processing_status = 'done'
                  AND processed_at >= :since
                  AND created_at IS NOT NULL
                  AND processed_at IS NOT NULL
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
                    ROUND(EXTRACT(EPOCH FROM (processed_at - created_at))::numeric, 1) AS duration_secs,
                    processed_at
                FROM media_files
                WHERE processing_status = 'done'
                  AND processed_at >= :since
                  AND created_at IS NOT NULL
                  AND processed_at IS NOT NULL
                ORDER BY duration_secs DESC
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
        # separated by gaps > 10 minutes into discrete "sessions"
        ts_rows = db.execute(
            text(
                """
                SELECT processed_at
                FROM media_files
                WHERE processing_status = 'done'
                  AND processed_at IS NOT NULL
                ORDER BY processed_at ASC
                """
            )
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
