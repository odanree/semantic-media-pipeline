"""
Ingest, processing, and media serving endpoints
"""

import asyncio
import mimetypes
import os
import re
from datetime import datetime

import io
import logging

import aiofiles
import boto3
from botocore.exceptions import ClientError
from celery import Celery
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse, Response, StreamingResponse
from rate_limit import limiter, LIMIT_STREAM, LIMIT_THUMBNAIL
from PIL import Image as PILImage
from pydantic import BaseModel

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# S3 / storage backend helpers
# ---------------------------------------------------------------------------
IS_S3 = os.getenv("STORAGE_BACKEND", "local").lower() == "s3"
S3_BUCKET = os.getenv("S3_BUCKET", "")

_s3_client = None


def _get_s3_client():
    """Lazy-init boto3 S3 client using env vars (same as worker)."""
    global _s3_client
    if _s3_client is None:
        _s3_client = boto3.client(
            "s3",
            endpoint_url=os.getenv("S3_ENDPOINT_URL") or None,
            region_name=os.getenv("S3_REGION", "auto"),
            aws_access_key_id=os.getenv("S3_ACCESS_KEY"),
            aws_secret_access_key=os.getenv("S3_SECRET_KEY"),
        )
    return _s3_client


def _s3_presign(key: str, expires: int = 3600) -> str:
    """Generate a presigned GET URL for an S3/R2 object."""
    return _get_s3_client().generate_presigned_url(
        "get_object",
        Params={"Bucket": S3_BUCKET, "Key": key},
        ExpiresIn=expires,
    )

# ---------------------------------------------------------------------------
# Thumbnail helpers
# ---------------------------------------------------------------------------

def _placeholder_jpeg(width: int = 320, height: int = 180) -> bytes:
    """
    Return a minimal dark-gray JPEG.  Used whenever the thumbnail endpoint
    cannot extract a real frame so that <img> tags always receive
    Content-Type: image/jpeg and Chrome ERR_BLOCKED_BY_ORB never fires.
    """
    buf = io.BytesIO()
    PILImage.new("RGB", (width, height), (30, 30, 30)).save(buf, format="JPEG", quality=40)
    return buf.getvalue()


_PLACEHOLDER_MP4_CACHE: bytes | None = None

def _placeholder_video_stub() -> bytes:
    """
    Return a real 1-second silent black MP4 generated via ffmpeg.
    Cached on first call. Used whenever the stream endpoint cannot serve a
    real file so <video> tags always receive Content-Type: video/mp4 and
    Chrome ORB (Opaque Response Blocking) never fires.

    Falls back to an empty bytes object if ffmpeg is unavailable — the
    browser will show its native 'video unavailable' UI rather than an
    ORB-blocked opaque error.
    """
    global _PLACEHOLDER_MP4_CACHE
    if _PLACEHOLDER_MP4_CACHE is not None:
        return _PLACEHOLDER_MP4_CACHE

    import subprocess
    try:
        result = subprocess.run(
            [
                "ffmpeg", "-y",
                "-f", "lavfi", "-i", "color=c=black:s=320x180:d=1:r=1",
                "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono",
                "-c:v", "libx264", "-pix_fmt", "yuv420p", "-t", "1",
                "-c:a", "aac", "-b:a", "64k",
                "-movflags", "+faststart",
                "-f", "mp4", "pipe:1",
            ],
            capture_output=True,
            timeout=15,
        )
        if result.returncode == 0 and result.stdout:
            _PLACEHOLDER_MP4_CACHE = result.stdout
            log.info(f"[Stream] Placeholder MP4 generated ({len(result.stdout)} bytes)")
            return _PLACEHOLDER_MP4_CACHE
        else:
            log.warning(f"[Stream] ffmpeg placeholder generation failed: {result.stderr[-200:]}")
    except Exception as e:
        log.warning(f"[Stream] Could not generate placeholder MP4: {e}")
    return b""

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
        storage_backend = os.getenv("STORAGE_BACKEND", "local").lower()
        if storage_backend != "s3" and not os.path.isdir(request.media_root):
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
@limiter.limit(LIMIT_STREAM)
async def stream_media(request: Request, path: str, quality: str = "proxy"):
    """
    Stream a media file with full HTTP Range support.
    Uses 4MB read chunks to minimise 9P round-trips on Docker/Windows.

    quality=proxy    (default) transparently serves the 720p faststart proxy
                     if one exists, falling back to the original source.
    quality=original bypasses the proxy lookup and always serves the raw file.

    S3/R2: returns a presigned-URL redirect so the browser downloads directly
    from the object store with full Range support — no proxying through the API.

    CRITICAL: Returns placeholder video on ANY error to prevent Chrome ORB
    (Opaque Response Blocking) from blocking <video> tag loads. Errors are
    logged server-side, not returned to client.
    """
    # ------------------------------------------------------------------
    # S3 path: redirect to a short-lived presigned URL
    # ------------------------------------------------------------------
    if IS_S3:
        try:
            url = _s3_presign(path, expires=3600)
            return RedirectResponse(url=url, status_code=302)
        except ClientError as e:
            log.warning("[Stream/S3] presign failed for %s: %s", path, e)
            return Response(
                content=_placeholder_video_stub(),
                status_code=200,
                media_type="video/mp4",
                headers={"Cache-Control": "no-store"},
            )

    try:
        resolved = os.path.realpath(path)

        if not any(resolved.startswith(root) for root in ALLOWED_ROOTS):
            log.warning(f"[Stream] Access denied: {path}")
            # Return placeholder to prevent ORB blocking
            return Response(
                content=_placeholder_video_stub(),
                status_code=200,
                media_type="video/mp4",
                headers={"Cache-Control": "no-store"}
            )

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
            log.warning(f"[Stream] File not found: {path} (resolved: {resolved})")
            # Return placeholder to prevent ORB blocking
            return Response(
                content=_placeholder_video_stub(),
                status_code=200,
                media_type="video/mp4",
                headers={"Cache-Control": "no-store"}
            )

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
    except Exception as e:
        log.error(f"[Stream] Internal error: {str(e)}", exc_info=True)
        # Return placeholder on any unexpected error
        return Response(
            content=_placeholder_video_stub(),
            status_code=200,
            media_type="video/mp4",
            headers={"Cache-Control": "no-store"}
        )


@router.get("/thumbnail")
@limiter.limit(LIMIT_THUMBNAIL)
async def get_thumbnail(request: Request, path: str, t: float = 0.0):
    """
    Extract a single JPEG frame from a video at timestamp `t` (seconds).

    This is the "semantic thumbnail" endpoint — the timestamp comes directly
    from the Qdrant payload (the exact frame that matched the CLIP query),
    so the thumbnail shows the moment that is semantically closest to what
    the user searched for.

    Uses ffmpeg with -ss before -i (fast seek) + -vframes 1 to extract a
    single frame without decoding the entire video.  Output is piped to
    stdout to avoid any disk writes.  Browsers cache for 24 hours.

    IMPORTANT: this endpoint NEVER raises HTTPException.  All error paths
    return a dark-gray placeholder JPEG so that browser <img> tags always
    receive Content-Type: image/jpeg and Chrome ERR_BLOCKED_BY_ORB never
    fires (ORB triggers when a no-cors image request gets a non-image MIME
    type such as the application/json that HTTPException would produce).
    """
    # ------------------------------------------------------------------
    # Resolve the input source: presigned URL (S3) or local path
    # ------------------------------------------------------------------
    if IS_S3:
        try:
            # 120 s is enough for ffmpeg to open the URL and grab one frame.
            ffmpeg_input = _s3_presign(path, expires=120)
        except ClientError as e:
            log.warning("thumbnail: S3 presign failed for %s: %s", path, e)
            return Response(
                content=_placeholder_jpeg(),
                media_type="image/jpeg",
                headers={"Cache-Control": "no-store"},
            )
    else:
        resolved = os.path.realpath(path)

        if not any(resolved.startswith(root) for root in ALLOWED_ROOTS):
            log.warning("thumbnail: access denied for path %s", path)
            return Response(
                content=_placeholder_jpeg(),
                media_type="image/jpeg",
                headers={"Cache-Control": "no-store"},
            )

        if not os.path.isfile(resolved):
            log.warning("thumbnail: file not found %s", resolved)
            return Response(
                content=_placeholder_jpeg(),
                media_type="image/jpeg",
                headers={"Cache-Control": "no-store"},
            )

        ffmpeg_input = resolved

    # Clamp timestamp to >= 0
    seek = max(0.0, t)

    # Fast seek: -ss BEFORE -i jumps to keyframe near the target,
    # then -vframes 1 grabs the first decoded frame.
    # scale=-2:320 preserves aspect ratio (portrait + landscape).
    # -q:v 4 gives ~85% JPEG quality — crisp enough for a thumbnail.
    cmd = [
        "ffmpeg",
        "-ss", str(seek),
        "-i", ffmpeg_input,
        "-vframes", "1",
        "-vf", "scale=-2:320",
        "-q:v", "4",
        "-f", "image2pipe",
        "-vcodec", "mjpeg",
        "pipe:1",
    ]

    stdout = b""
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=15)
        if proc.returncode != 0 or not stdout:
            log.warning(
                "thumbnail: ffmpeg non-zero exit %s for %s: %s",
                proc.returncode, resolved,
                stderr[-300:].decode(errors="replace"),
            )
    except asyncio.TimeoutError:
        log.warning("thumbnail: ffmpeg timed out for %s at t=%.1f", resolved, seek)
    except Exception as e:
        log.warning("thumbnail: ffmpeg exec error for %s: %s", resolved, e)

    if not stdout:
        return Response(
            content=_placeholder_jpeg(),
            media_type="image/jpeg",
            # Short cache so a retry after fixing ffmpeg picks up real frames
            headers={"Cache-Control": "public, max-age=60"},
        )

    return Response(
        content=stdout,
        media_type="image/jpeg",
        headers={
            # Cache in browser for 24 h — thumbnails are deterministic
            "Cache-Control": "public, max-age=86400",
        },
    )
