"""
Admin router — maintenance and backfill operations.
All routes require API key (applied at include_router level in main.py).
"""

import os

from celery import Celery
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

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
