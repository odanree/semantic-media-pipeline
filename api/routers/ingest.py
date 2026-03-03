"""
Ingest, processing, and media serving endpoints
"""

import mimetypes
import os
from datetime import datetime

from celery import Celery
from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

router = APIRouter()

# Initialize Celery client
celery_app = Celery(
    broker=os.getenv("CELERY_BROKER_URL", "redis://redis:6379/0"),
    backend=os.getenv("CELERY_RESULT_BACKEND", "redis://redis:6379/0"),
)


class IngestRequest(BaseModel):
    """Ingest request model"""

    media_root: str


class IngestResponse(BaseModel):
    """Ingest response model"""

    status: str
    timestamp: str
    media_root: str
    task_id: str
    message: str


@router.post("/ingest")
async def start_ingest(request: IngestRequest):
    """
    Start media ingestion pipeline.
    Crawls the specified directory and enqueues processing tasks.

    Args:
        media_root: Path to media directory to crawl

    Returns:
        Task ID for monitoring progress
    """
    try:
        if not os.path.isdir(request.media_root):
            raise ValueError(f"Invalid directory: {request.media_root}")

        # Send task via Celery using the correct task name ('tasks.crawl_and_dispatch')
        # which is defined in the worker's tasks module and registered with @app.task
        task = celery_app.send_task(
            "tasks.crawl_and_dispatch",
            args=(request.media_root,),
        )

        return IngestResponse(
            status="accepted",
            timestamp=datetime.utcnow().isoformat(),
            media_root=request.media_root,
            task_id=task.id,
            message=f"Starting ingest crawl of {request.media_root}",
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


class TaskStatusResponse(BaseModel):
    """Task status response"""

    task_id: str
    status: str
    result: dict = None
    error: str = None


@router.get("/task/{task_id}")
async def get_task_status(task_id: str):
    """
    Get the status of a Celery task.

    Args:
        task_id: Task ID to check

    Returns:
        Task status and result
    """
    try:
        from celery.result import AsyncResult

        task = AsyncResult(task_id, app=celery_app)

        response = {
            "task_id": task_id,
            "status": task.status,
            "timestamp": datetime.utcnow().isoformat(),
        }

        if task.ready():
            if task.successful():
                response["result"] = task.result
            else:
                response["error"] = str(task.info)

        return response
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ===========================================================================
# Media streaming
# ===========================================================================

ALLOWED_ROOTS = [
    os.path.realpath("/mnt/source"),
    os.path.realpath("/data/media"),
]


@router.get("/stream")
async def stream_media(path: str):
    """
    Stream a media file with HTTP Range support.
    Uses Starlette FileResponse which handles byte-range requests natively,
    enabling efficient video seeking and low-buffer playback in browsers.
    """
    resolved = os.path.realpath(path)

    if not any(resolved.startswith(root) for root in ALLOWED_ROOTS):
        raise HTTPException(status_code=403, detail="Access denied")

    if not os.path.isfile(resolved):
        raise HTTPException(status_code=404, detail="File not found")

    media_type, _ = mimetypes.guess_type(resolved)
    return FileResponse(
        resolved,
        media_type=media_type or "application/octet-stream",
        headers={"Cache-Control": "public, max-age=3600"},
    )
