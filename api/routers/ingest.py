"""
Ingest, processing, and media serving endpoints
"""

import asyncio
import mimetypes
import os
import re
from datetime import datetime

import aiofiles
from celery import Celery
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response, StreamingResponse
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
    os.path.realpath("/mnt/proxies"),
    os.path.realpath("/data/media"),
]

# Source root for proxy lookup (must match the worker's PROXY_ROOT)
_SOURCE_ROOT = os.path.realpath("/mnt/source")
_PROXY_ROOT_DEFAULT = "/mnt/proxies"


# 4MB chunks = 64x fewer filesystem calls than Starlette's 64KB default.
# Critical on Docker Desktop/Windows: each 9P volume-mount read has ~200ms
# latency, so 64KB chunks = 25s to load 8MB.  4MB chunks = <1s.
STREAM_CHUNK_SIZE = 4 * 1024 * 1024  # 4 MB


@router.get("/stream")
async def stream_media(path: str, request: Request, quality: str = "proxy"):
    """
    Stream a media file with full HTTP Range support.
    Uses 4MB read chunks to minimise 9P round-trips on Docker/Windows.

    quality=proxy    (default) transparently serves the 720p faststart proxy
                     if one exists, falling back to the original source.
    quality=original bypasses the proxy lookup and always serves the raw file.
    """
    resolved = os.path.realpath(path)

    if not any(resolved.startswith(root) for root in ALLOWED_ROOTS):
        raise HTTPException(status_code=403, detail="Access denied")

    # Transparently serve proxy when available (skipped for quality=original).
    # The worker writes a faststart copy to PROXY_ROOT mirroring the source
    # tree; that copy has moov-first so the browser can seek instantly.
    if quality != "original":
        proxy_root = os.getenv("PROXY_ROOT", _PROXY_ROOT_DEFAULT).strip()
        if proxy_root and resolved.startswith(_SOURCE_ROOT + os.sep):
            rel = resolved[len(_SOURCE_ROOT) + 1:]
            proxy_candidate = os.path.join(proxy_root, rel)
            if os.path.isfile(proxy_candidate):
                resolved = proxy_candidate

    if not os.path.isfile(resolved):
        raise HTTPException(status_code=404, detail="File not found")

    file_size = os.path.getsize(resolved)
    media_type, _ = mimetypes.guess_type(resolved)

    media_type = media_type or "application/octet-stream"

    range_header = request.headers.get("range")
    range_match = re.match(r"bytes=(\d+)-(\d*)", range_header or "")

    if range_match:
        start = int(range_match.group(1))
        end = int(range_match.group(2)) if range_match.group(2) else file_size - 1
        end = min(end, file_size - 1)
        content_length = end - start + 1

        async def ranged_sender():
            async with aiofiles.open(resolved, "rb") as f:
                await f.seek(start)
                remaining = content_length
                while remaining > 0:
                    chunk = await f.read(min(STREAM_CHUNK_SIZE, remaining))
                    if not chunk:
                        break
                    remaining -= len(chunk)
                    yield chunk

        return StreamingResponse(
            ranged_sender(),
            status_code=206,
            media_type=media_type,
            headers={
                "Content-Range": f"bytes {start}-{end}/{file_size}",
                "Accept-Ranges": "bytes",
                "Content-Length": str(content_length),
                "Cache-Control": "public, max-age=3600",
            },
        )

    # No Range header — stream the full file
    async def full_sender():
        async with aiofiles.open(resolved, "rb") as f:
            while True:
                chunk = await f.read(STREAM_CHUNK_SIZE)
                if not chunk:
                    break
                yield chunk

    return StreamingResponse(
        full_sender(),
        media_type=media_type,
        headers={
            "Accept-Ranges": "bytes",
            "Content-Length": str(file_size),
            "Cache-Control": "public, max-age=3600",
        },
    )


@router.get("/thumbnail")
async def get_thumbnail(path: str, t: float = 0.0):
    """
    Extract a single JPEG frame from a video at timestamp `t` (seconds).

    This is the "semantic thumbnail" endpoint — the timestamp comes directly
    from the Qdrant payload (the exact frame that matched the CLIP query),
    so the thumbnail shows the moment that is semantically closest to what
    the user searched for.

    Uses ffmpeg with -ss before -i (fast seek) + -vframes 1 to extract a
    single frame without decoding the entire video.  Output is piped to
    stdout to avoid any disk writes.  Browsers cache for 24 hours.
    """
    resolved = os.path.realpath(path)

    if not any(resolved.startswith(root) for root in ALLOWED_ROOTS):
        raise HTTPException(status_code=403, detail="Access denied")

    if not os.path.isfile(resolved):
        raise HTTPException(status_code=404, detail="File not found")

    # Clamp timestamp to >= 0
    seek = max(0.0, t)

    # Fast seek: -ss BEFORE -i jumps to keyframe near the target,
    # then -vframes 1 grabs the first decoded frame.
    # scale=-2:320 preserves aspect ratio (portrait + landscape).
    # -q:v 4 gives ~85% JPEG quality — crisp enough for a thumbnail.
    cmd = [
        "ffmpeg",
        "-ss", str(seek),
        "-i", resolved,
        "-vframes", "1",
        "-vf", "scale=-2:320",
        "-q:v", "4",
        "-f", "image2pipe",
        "-vcodec", "mjpeg",
        "pipe:1",
    ]

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=15)
    except asyncio.TimeoutError:
        raise HTTPException(status_code=504, detail="Thumbnail extraction timed out")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"ffmpeg error: {e}")

    if proc.returncode != 0 or not stdout:
        raise HTTPException(
            status_code=500,
            detail=f"ffmpeg returned no data: {stderr[-200:].decode(errors='replace')}",
        )

    return Response(
        content=stdout,
        media_type="image/jpeg",
        headers={
            # Cache in browser for 24 h — thumbnails are deterministic
            "Cache-Control": "public, max-age=86400",
        },
    )
