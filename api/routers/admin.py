"""
Admin router — maintenance and backfill operations.
All routes require API key (applied at include_router level in main.py).
"""

import os

from celery import Celery
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import text as sa_text

from db.models import get_async_engine

router = APIRouter()

_celery = Celery(
    broker=os.getenv("CELERY_BROKER_URL", "redis://redis:6379/0"),
    backend=os.getenv("CELERY_RESULT_BACKEND", "redis://redis:6379/0"),
)


class BackfillRequest(BaseModel):
    dry_run: bool = False


class BackfillResponse(BaseModel):
    task_id: str
    dry_run: bool
    message: str


class TaskStatusResponse(BaseModel):
    task_id: str
    state: str
    result: dict | None = None


@router.post("/admin/backfill-captions", response_model=BackfillResponse, tags=["admin"])
async def trigger_backfill_captions(body: BackfillRequest = BackfillRequest()) -> BackfillResponse:
    """
    Dispatch the caption backfill Celery task.

    Iterates all Qdrant video-frame points and adds a `caption` field via
    moondream (skipping points that already have one).  Safe to call multiple
    times — idempotent.  Returns the Celery task ID so progress can be polled
    via GET /api/admin/task/{task_id}.
    """
    try:
        result = _celery.send_task(
            "tasks.backfill_captions",
            kwargs={"dry_run": body.dry_run},
            queue="celery",
        )
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Failed to dispatch task: {exc}") from exc

    mode = "dry-run" if body.dry_run else "live"
    return BackfillResponse(
        task_id=result.id,
        dry_run=body.dry_run,
        message=f"Caption backfill dispatched ({mode}). Poll /api/admin/task/{result.id} for status.",
    )


@router.post("/admin/backfill-moov-sidecars", tags=["admin"])
async def trigger_backfill_moov_sidecars(body: BackfillRequest = BackfillRequest()):
    """
    Dispatch moov sidecar generation for all existing video files on the source mount.

    Walks /mnt/source for .mp4/.mov files and enqueues generate_moov_sidecar_task
    for each via the 'proxies' queue. Skips files that already have a sidecar.
    Safe to call multiple times — idempotent.

    Returns the Celery task ID of the coordinator task.
    """
    try:
        result = _celery.send_task(
            "tasks.backfill_moov_sidecars",
            kwargs={"dry_run": body.dry_run},
            queue="celery",
        )
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Failed to dispatch task: {exc}") from exc

    mode = "dry-run" if body.dry_run else "live"
    return {
        "task_id": result.id,
        "dry_run": body.dry_run,
        "message": f"Moov sidecar backfill dispatched ({mode}). Poll /api/admin/task/{result.id} for status.",
    }


@router.post("/admin/backfill-yolo", response_model=BackfillResponse, tags=["admin"])
async def trigger_backfill_yolo(body: BackfillRequest = BackfillRequest()) -> BackfillResponse:
    """
    Dispatch YOLO object detection backfill for all construction frames missing yolo_labels.

    Reads from the JPEG frame cache — no video seeking.  Skips frames that already
    have yolo_labels.  Safe to call multiple times — idempotent.
    """
    try:
        result = _celery.send_task(
            "tasks.backfill_yolo",
            kwargs={"dry_run": body.dry_run},
            queue="gpu",
        )
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Failed to dispatch task: {exc}") from exc

    mode = "dry-run" if body.dry_run else "live"
    return BackfillResponse(
        task_id=result.id,
        dry_run=body.dry_run,
        message=f"YOLO backfill dispatched ({mode}). Poll /api/admin/task/{result.id} for status.",
    )


class RecentMediaItem(BaseModel):
    file_path: str
    file_type: str
    file_size_bytes: str | None = None
    duration_secs: str | None = None
    width: str | None = None
    height: str | None = None
    processed_at: str | None = None
    processing_status: str
    model_version: str | None = None
    embedding_ms: int | None = None
    has_vector: bool


class RecentMediaResponse(BaseModel):
    items: list[RecentMediaItem]
    limit: int


@router.get("/admin/recent", response_model=RecentMediaResponse, tags=["admin"])
async def list_recent_media(
    limit: int = Query(50, ge=1, le=10000),
    status: str | None = Query(None, description="Filter by processing_status (e.g. 'done')"),
) -> RecentMediaResponse:
    """
    Most recently indexed media files, ordered by `processed_at DESC NULLS LAST`.

    Rows reflect the DB write at the end of the worker pipeline — there can
    be a small window between a row appearing here and the vector becoming
    searchable in Qdrant. `has_vector` exposes that gap (qdrant_point_id IS NOT NULL).
    """
    where_clause = ""
    params: dict = {"lim": limit}
    if status:
        where_clause = "WHERE processing_status = :status"
        params["status"] = status

    sql = sa_text(
        f"""
        SELECT file_path, file_type, file_size_bytes, duration_secs,
               width, height, processed_at, processing_status,
               model_version, embedding_ms, qdrant_point_id
        FROM media_files
        {where_clause}
        ORDER BY processed_at DESC NULLS LAST
        LIMIT :lim
        """
    )

    try:
        engine = await get_async_engine()
        async with engine.begin() as conn:
            rows = (await conn.execute(sql, params)).fetchall()
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"DB query failed: {exc}") from exc

    items = [
        RecentMediaItem(
            file_path=r[0],
            file_type=r[1],
            file_size_bytes=r[2],
            duration_secs=r[3],
            width=r[4],
            height=r[5],
            processed_at=r[6].isoformat() if r[6] else None,
            processing_status=r[7],
            model_version=r[8],
            embedding_ms=r[9],
            has_vector=r[10] is not None,
        )
        for r in rows
    ]
    return RecentMediaResponse(items=items, limit=limit)


@router.get("/admin/task/{task_id}", response_model=TaskStatusResponse, tags=["admin"])
async def get_task_status(task_id: str) -> TaskStatusResponse:
    """Poll the status of a Celery task by ID."""
    async_result = _celery.AsyncResult(task_id)
    state = async_result.state
    result = None
    if state == "SUCCESS":
        result = async_result.result
    elif state == "FAILURE":
        result = {"error": str(async_result.result)}
    return TaskStatusResponse(task_id=task_id, state=state, result=result)
