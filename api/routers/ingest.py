"""
Ingest, processing, and media serving endpoints
"""

import asyncio
import io
import logging
import math
import mimetypes
import os
import re
import shutil
import threading
import uuid
from collections import OrderedDict
from datetime import datetime, timedelta
from typing import List

import json
import struct

import aiofiles
import boto3
from botocore.exceptions import ClientError
from celery import Celery
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse, Response, StreamingResponse
from rate_limit import limiter, LIMIT_STREAM, LIMIT_THUMBNAIL, LIMIT_PLAYLIST
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
# Public router — no API key required. Endpoints here use their own
# access control (e.g. UUID tokens) and must be safe to call unauthenticated.
public_router = APIRouter()

# Initialize Celery client
celery_app = Celery(
    broker=os.getenv("CELERY_BROKER_URL", "redis://redis:6379/0"),
    backend=os.getenv("CELERY_RESULT_BACKEND", "redis://redis:6379/0"),
)


class IngestRequest(BaseModel):
    """Ingest request model"""

    media_root: str
    force_relocate: bool = False


class GooglePhotosIngestRequest(BaseModel):
    """Google Photos ingest request model"""

    start_date: str  # ISO date, e.g. "2025-10-08"
    end_date: str    # ISO date, e.g. "2026-03-21"


class GooglePhotosIngestResponse(BaseModel):
    status: str
    timestamp: str
    start_date: str
    end_date: str
    task_id: str
    message: str


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
            kwargs={"force_relocate": request.force_relocate},
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


@router.post("/ingest/google-photos")
async def ingest_google_photos(request: GooglePhotosIngestRequest):
    """
    Fetch media from Google Photos for a date range and enqueue ingest tasks.

    Requires GOOGLE_PHOTOS_CLIENT_ID, GOOGLE_PHOTOS_CLIENT_SECRET, and
    GOOGLE_PHOTOS_REFRESH_TOKEN set in the worker's environment.
    Run scripts/google_photos_auth.py once to generate the refresh token.

    Args:
        start_date: ISO date string, e.g. "2025-10-08"
        end_date:   ISO date string, e.g. "2026-03-21"
    """
    try:
        from datetime import datetime as _dt
        _dt.fromisoformat(request.start_date)
        _dt.fromisoformat(request.end_date)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"Invalid date format: {e}")

    task = celery_app.send_task(
        "tasks.crawl_google_photos",
        args=(request.start_date, request.end_date),
    )

    return GooglePhotosIngestResponse(
        status="accepted",
        timestamp=datetime.utcnow().isoformat(),
        start_date=request.start_date,
        end_date=request.end_date,
        task_id=task.id,
        message=f"Fetching Google Photos from {request.start_date} to {request.end_date}",
    )


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
    os.path.realpath("/mnt/i-media"),
]


_SOURCE_MOUNT = "/mnt/source"


def _translate_path(path: str) -> str:
    """Map stored paths to container filesystem paths.

    Handles three formats:
    - Relative (new): 'c-index/file.mp4' → '/mnt/source/c-index/file.mp4'
    - Absolute Linux (legacy): '/mnt/source/...' → unchanged
    - Windows (legacy): 'C:/...' → translated via LUMEN_PATH_MAP_n env vars
    """
    # Relative paths (new format): expand to /mnt/source/
    if not path.startswith("/") and not (len(path) >= 2 and path[1] == ":"):
        return _SOURCE_MOUNT + "/" + path

    # Windows path translation (legacy records written by native Windows worker)
    maps = [
        v for k, v in os.environ.items()
        if k.startswith("LUMEN_PATH_MAP_") and ":" in v
    ]
    norm = path.replace("\\", "/")
    for val in sorted(maps, key=lambda v: -len(v.split(":", 1)[1])):
        linux_prefix, win_prefix = val.split(":", 1)
        win_norm = win_prefix.replace("\\", "/")
        if norm.startswith(win_norm):
            remainder = norm[len(win_norm):]
            return linux_prefix.rstrip("/") + "/" + remainder.lstrip("/")
    return path

# Source root for proxy/sidecar lookup (must match worker PROXY_ROOT/SIDECAR_ROOT)
_SOURCE_ROOT = os.path.realpath("/mnt/source")
_PROXY_ROOT_DEFAULT = "/mnt/proxies"
_SIDECAR_ROOT = os.getenv("SIDECAR_ROOT", "/mnt/sidecars").strip()

# On-demand proxy: transcode short HEVC clips to H.264 on first play request.
# Written to ON_DEMAND_PROXY_DIR (default: frame_cache subdir, always writable).
_ON_DEMAND_PROXY_DIR_DEFAULT = "/mnt/frame_cache/on_demand_proxies"
_ON_DEMAND_MAX_DURATION = 300  # seconds — skip files longer than 5 minutes
_proxy_in_progress: set[str] = set()
_proxy_failed: set[str] = set()  # permanent skip within this process lifetime
_proxy_sem: asyncio.Semaphore | None = None  # created lazily inside the event loop
_proxy_tasks: set[asyncio.Task] = set()  # strong refs — prevent GC of fire-and-forget tasks


def _get_proxy_sem() -> asyncio.Semaphore:
    global _proxy_sem
    if _proxy_sem is None:
        _proxy_sem = asyncio.Semaphore(1)
    return _proxy_sem


async def _probe_duration(path: str) -> float | None:
    """Return media duration in seconds via ffprobe, or None on failure."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "ffprobe", "-v", "quiet", "-print_format", "json",
            "-show_entries", "format=duration", path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=10)
        return float(json.loads(stdout)["format"]["duration"])
    except Exception:
        return None


async def _probe_video_codec(path: str) -> str | None:
    """Return the video codec name (e.g. 'hevc', 'h264') via ffprobe, or None on failure."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "ffprobe", "-v", "quiet", "-print_format", "json",
            "-show_entries", "stream=codec_name",
            "-select_streams", "v:0", path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=10)
        streams = json.loads(stdout).get("streams", [])
        return streams[0]["codec_name"] if streams else None
    except Exception:
        return None


async def _generate_proxy_bg(
    source_path: str,
    proxy_path: str,
    start_sec: float | None = None,
    end_sec: float | None = None,
) -> None:
    """Fire-and-forget: transcode source (or a scene segment) to 720p H.264 proxy.

    - Full file: used when duration ≤ _ON_DEMAND_MAX_DURATION
    - Scene segment (start_sec/end_sec provided): used for long files — only the
      matched scene is transcoded, keyed by timestamps in the proxy filename.

    At most one transcode runs at a time (_proxy_sem).
    """
    if proxy_path not in _proxy_in_progress:
        return  # was never properly queued (shouldn't happen)
    tmp_path = proxy_path + ".tmp"
    is_segment = start_sec is not None and end_sec is not None
    try:
        codec = await _probe_video_codec(source_path)
        if codec != "hevc":
            log.warning("[Proxy] Skipping non-HEVC file (codec=%s): %s", codec, os.path.basename(source_path))
            _proxy_failed.add(proxy_path)
            return

        duration = await _probe_duration(source_path)

        if not is_segment:
            # Full-file mode: skip files longer than threshold
            if duration is None or duration > _ON_DEMAND_MAX_DURATION:
                log.warning("[Proxy] Skipping on-demand for %s (duration=%s)",
                         os.path.basename(source_path), duration)
                _proxy_failed.add(proxy_path)
                return

        async with _get_proxy_sem():
            # Re-check: another task may have finished while we were waiting
            if os.path.isfile(proxy_path):
                return

            os.makedirs(os.path.dirname(proxy_path), exist_ok=True)
            if is_segment:
                seg_dur = end_sec - start_sec
                log.warning("[Proxy] Starting scene-segment transcode: %s [%.1f–%.1fs] (%.0fs)",
                         os.path.basename(source_path), start_sec, end_sec, seg_dur)
                # -ss before -i = fast keyframe seek; -to is relative to -ss
                ffmpeg_input = [
                    "ffmpeg", "-y",
                    "-ss", f"{start_sec:.3f}", "-i", source_path,
                    "-to", f"{seg_dur:.3f}",
                ]
                timeout = seg_dur + 120
            else:
                log.warning("[Proxy] Starting on-demand transcode: %s (%.0fs)",
                         os.path.basename(source_path), duration)
                ffmpeg_input = ["ffmpeg", "-y", "-i", source_path]
                timeout = _ON_DEMAND_MAX_DURATION + 120

            proc = await asyncio.create_subprocess_exec(
                *ffmpeg_input,
                "-c:v", "libx264", "-preset", "fast", "-crf", "23",
                "-vf", "scale=1280:720:force_original_aspect_ratio=decrease"
                       ",pad=1280:720:(ow-iw)/2:(oh-ih)/2",
                "-c:a", "aac", "-b:a", "128k", "-ar", "48000",
                "-movflags", "+faststart",
                "-f", "mp4",
                tmp_path,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                _, stderr_bytes = await asyncio.wait_for(
                    proc.communicate(), timeout=timeout
                )
            except asyncio.TimeoutError:
                proc.kill()
                await proc.communicate()  # drain pipe
                log.warning("[Proxy] Transcode timed out: %s", source_path)
                return

            if proc.returncode == 0 and os.path.isfile(tmp_path):
                os.replace(tmp_path, proxy_path)
                log.warning("[Proxy] On-demand proxy ready: %s", proxy_path)
            else:
                stderr_tail = (
                    stderr_bytes.decode(errors="replace")[-600:] if stderr_bytes else ""
                )
                log.warning(
                    "[Proxy] Transcode failed (rc=%s): %s\n  stderr: %s",
                    proc.returncode, source_path, stderr_tail,
                )
                _proxy_failed.add(proxy_path)  # don't retry this session
    except Exception as e:
        log.warning("[Proxy] On-demand proxy error for %s: %s", source_path, e)
    finally:
        if os.path.isfile(tmp_path):
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
        _proxy_in_progress.discard(proxy_path)


@router.get("/proxy-status")
@limiter.limit("120/minute")
async def proxy_status(
    request: Request,
    path: str,
    start: float | None = None,
    end: float | None = None,
):
    """Return whether an on-demand proxy exists for the given path/segment."""
    try:
        resolved = os.path.realpath(_translate_path(path))
        if not resolved.startswith(_SOURCE_ROOT + os.sep):
            return {"status": "original"}

        rel = resolved[len(_SOURCE_ROOT) + 1:]

        proxy_root = os.getenv("PROXY_ROOT", _PROXY_ROOT_DEFAULT).strip()
        if proxy_root and os.path.isfile(os.path.join(proxy_root, rel)):
            return {"status": "proxy"}

        on_demand_dir = os.getenv("ON_DEMAND_PROXY_DIR", _ON_DEMAND_PROXY_DIR_DEFAULT).strip()
        if on_demand_dir:
            is_segment = start is not None and end is not None
            if is_segment:
                base, ext = os.path.splitext(rel)
                seg_rel = f"{base}__{start:.3f}-{end:.3f}{ext}"
                on_demand_path = os.path.join(on_demand_dir, seg_rel)
            else:
                on_demand_path = os.path.join(on_demand_dir, rel)
            if os.path.isfile(on_demand_path):
                return {"status": "proxy"}

        return {"status": "original"}
    except Exception:
        return {"status": "original"}


# 4MB chunks = 64x fewer filesystem calls than Starlette's 64KB default.
# Critical on Docker Desktop/Windows: each 9P volume-mount read has ~200ms
# latency, so 64KB chunks = 25s to load 8MB.  4MB chunks = <1s.
STREAM_CHUNK_SIZE = 4 * 1024 * 1024  # 4 MB


@router.get("/stream")
@limiter.limit(LIMIT_STREAM)
async def stream_media(
    request: Request,
    path: str,
    quality: str = "proxy",
    start: float | None = None,  # scene segment start (seconds)
    end: float | None = None,    # scene segment end (seconds)
):
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
        resolved = os.path.realpath(_translate_path(path))

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
        # If no pre-generated proxy exists, trigger on-demand transcode for files
        # under 10 minutes and serve the original for this request.
        proxy_source = "original"
        if quality != "original" and resolved.startswith(_SOURCE_ROOT + os.sep):
            rel = resolved[len(_SOURCE_ROOT) + 1:]
            proxy_found = False

            proxy_root = os.getenv("PROXY_ROOT", _PROXY_ROOT_DEFAULT).strip()
            if proxy_root:
                proxy_candidate = os.path.join(proxy_root, rel)
                if os.path.isfile(proxy_candidate):
                    resolved = proxy_candidate
                    proxy_found = True
                    proxy_source = "proxy"

            if not proxy_found:
                on_demand_dir = os.getenv(
                    "ON_DEMAND_PROXY_DIR", _ON_DEMAND_PROXY_DIR_DEFAULT
                ).strip()
                if on_demand_dir:
                    # Scene-segment proxy: embed timestamps in filename so each
                    # scene gets its own cached clip.
                    is_segment = start is not None and end is not None
                    if is_segment:
                        base, ext = os.path.splitext(rel)
                        seg_rel = f"{base}__{start:.3f}-{end:.3f}{ext}"
                        on_demand_path = os.path.join(on_demand_dir, seg_rel)
                    else:
                        on_demand_path = os.path.join(on_demand_dir, rel)

                    if os.path.isfile(on_demand_path):
                        log.warning("[Proxy] Serving on-demand proxy: %s", os.path.basename(on_demand_path))
                        resolved = on_demand_path
                        proxy_source = "proxy"
                    else:
                        # Mark in-progress synchronously before yielding to event loop,
                        # so concurrent range requests see it and don't queue duplicates.
                        if on_demand_path not in _proxy_in_progress and on_demand_path not in _proxy_failed:
                            _proxy_in_progress.add(on_demand_path)
                            log.warning("[Proxy] queuing task for %s", os.path.basename(on_demand_path))
                            _task = asyncio.create_task(
                                _generate_proxy_bg(
                                    resolved, on_demand_path,
                                    start_sec=start if is_segment else None,
                                    end_sec=end if is_segment else None,
                                )
                            )
                            _proxy_tasks.add(_task)
                            _task.add_done_callback(_proxy_tasks.discard)

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

        extra_headers = {"X-Proxy-Source": proxy_source, "Access-Control-Expose-Headers": "X-Proxy-Source"}

        if range_match:
            range_start = int(range_match.group(1))
            range_end = int(range_match.group(2)) if range_match.group(2) else file_size - 1
            range_end = min(range_end, file_size - 1)
            content_length = range_end - range_start + 1

            async def ranged_sender():
                async with aiofiles.open(resolved, "rb") as f:
                    await f.seek(range_start)
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
                    "Content-Range": f"bytes {range_start}-{range_end}/{file_size}",
                    "Accept-Ranges": "bytes",
                    "Content-Length": str(content_length),
                    "Cache-Control": "public, max-age=3600",
                    **extra_headers,
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
                **extra_headers,
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


# ---------------------------------------------------------------------------
# Thumbnail in-memory cache
# LRU with a hard cap — evicts oldest entry when full.
# Keyed on (path, rounded_timestamp) so near-identical seeks share entries.
# Thread-safe via a lock (asyncio tasks run in the same thread but ffmpeg
# subprocesses and cache writes can overlap across concurrent requests).
# ---------------------------------------------------------------------------
_THUMB_CACHE_MAX = int(os.getenv("THUMBNAIL_CACHE_MAX", "500"))
_thumb_cache: OrderedDict[tuple, bytes] = OrderedDict()
_thumb_cache_lock = threading.Lock()

# Semaphore — caps concurrent ffmpeg thumbnail extractions.
# Without this, a 20-result search fires 20 simultaneous ffmpeg processes
# which hammer the I/O layer (especially Docker bind mounts on Windows),
# causing all thumbnails to load slowly. Queuing them is faster overall.
_THUMB_MAX_CONCURRENT = int(os.getenv("THUMBNAIL_MAX_CONCURRENT", "6"))
_thumb_sem = asyncio.Semaphore(_THUMB_MAX_CONCURRENT)


def _cache_get(key: tuple) -> bytes | None:
    with _thumb_cache_lock:
        if key not in _thumb_cache:
            return None
        _thumb_cache.move_to_end(key)
        return _thumb_cache[key]


def _cache_set(key: tuple, value: bytes) -> None:
    with _thumb_cache_lock:
        _thumb_cache[key] = value
        _thumb_cache.move_to_end(key)
        while len(_thumb_cache) > _THUMB_CACHE_MAX:
            _thumb_cache.popitem(last=False)


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
        resolved = os.path.realpath(_translate_path(path))

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

    # Cache lookup — round to 1 decimal so t=5.001 and t=5.0 share an entry
    cache_key = (path, round(seek, 1))
    cached = _cache_get(cache_key)
    if cached is not None:
        return Response(
            content=cached,
            media_type="image/jpeg",
            headers={"Cache-Control": "public, max-age=86400", "X-Cache": "HIT"},
        )

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
    async with _thumb_sem:
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
                    proc.returncode, path,
                    stderr[-300:].decode(errors="replace"),
                )
        except asyncio.TimeoutError:
            log.warning("thumbnail: ffmpeg timed out for %s at t=%.1f", path, seek)
        except Exception as e:
            log.warning("thumbnail: ffmpeg exec error for %s: %s", path, e)

    if not stdout:
        return Response(
            content=_placeholder_jpeg(),
            media_type="image/jpeg",
            # Short cache so a retry after fixing ffmpeg picks up real frames
            headers={"Cache-Control": "public, max-age=60"},
        )

    _cache_set(cache_key, stdout)
    return Response(
        content=stdout,
        media_type="image/jpeg",
        headers={
            # Cache in browser for 24 h — thumbnails are deterministic
            "Cache-Control": "public, max-age=86400",
            "X-Cache": "MISS",
        },
    )


# ===========================================================================
# HLS Highlight Reel playlist generation
# ===========================================================================

PLAYLIST_DIR = "/tmp/lumen_playlists"
PLAYLIST_TTL_SECS = 3600
_MAX_CONCURRENT_SEGMENTS = 4

# Detect NVENC availability once at startup.
# h264_nvenc offloads encode to the GPU's fixed-function encoder, leaving CPU
# free for decode + audio (loudnorm). Falls back to libx264 if not available.
def _check_nvenc() -> bool:
    import subprocess
    try:
        r = subprocess.run(
            ["ffmpeg", "-hide_banner", "-f", "lavfi", "-i", "nullsrc=s=16x16:d=0.1",
             "-c:v", "h264_nvenc", "-f", "null", "-"],
            capture_output=True, timeout=10,
        )
        return r.returncode == 0  # pragma: no cover
    except Exception:
        return False

_NVENC_AVAILABLE = _check_nvenc()
log.info("[Playlist] NVENC available: %s", _NVENC_AVAILABLE)
# Global semaphore — shared across ALL concurrent playlist requests so total
# ffmpeg processes are capped regardless of how many reels are being compiled.
_PLAYLIST_SEM = asyncio.Semaphore(_MAX_CONCURRENT_SEGMENTS)


class ClipSpec(BaseModel):
    file_path: str
    start_sec: float
    end_sec: float


class PlaylistRequest(BaseModel):
    clips: List[ClipSpec]
    clip_padding_sec: float = 3.0
    title: str = "Highlight Reel"


class PlaylistResponse(BaseModel):
    playlist_url: str
    token: str
    clip_count: int
    total_duration_sec: float
    expires_at: str


async def _start_faststart_server(  # pragma: no cover
    sidecar_path: str, original_path: str, mdat_offset: int
) -> tuple[str, asyncio.AbstractServer]:
    """
    Start a minimal asyncio HTTP/1.1 server that presents a virtual faststart
    file to ffmpeg/ffprobe via HTTP Range requests.

    Bytes 0 … moov_size-1 are served from the local sidecar (fast).
    Bytes moov_size … N are served from the original file at mdat_offset + N
    (single pread per Range request — efficient on 9P mounts).

    Returns (url, server).  Caller must close the server after ffmpeg exits.
    """
    moov_bytes = open(sidecar_path, "rb").read()
    moov_size = len(moov_bytes)
    orig_size = os.path.getsize(original_path)
    total_size = moov_size + (orig_size - mdat_offset)

    async def _handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            headers: dict[bytes, bytes] = {}
            await reader.readline()  # request line
            while True:
                line = await reader.readline()
                if line in (b"\r\n", b"\n", b""):
                    break
                if b":" in line:
                    k, v = line.split(b":", 1)
                    headers[k.strip().lower()] = v.strip()

            range_hdr = headers.get(b"range", b"")
            if range_hdr.startswith(b"bytes="):
                parts = range_hdr[6:].split(b"-")
                start = int(parts[0]) if parts[0] else 0
                end = int(parts[1]) if len(parts) > 1 and parts[1] else total_size - 1
                end = min(end, total_size - 1)
                length = end - start + 1
                writer.write((
                    f"HTTP/1.1 206 Partial Content\r\n"
                    f"Content-Type: video/mp4\r\n"
                    f"Content-Length: {length}\r\n"
                    f"Content-Range: bytes {start}-{end}/{total_size}\r\n"
                    f"Accept-Ranges: bytes\r\n"
                    f"Connection: close\r\n"
                    f"\r\n"
                ).encode())
            else:
                # Non-Range (initial ffprobe GET): serve only the moov bytes which
                # are already in memory. Avoids reading from the slow 9P mount just
                # to satisfy ffprobe's probesize limit.
                start, end = 0, moov_size - 1
                length = moov_size
                writer.write((
                    f"HTTP/1.1 200 OK\r\n"
                    f"Content-Type: video/mp4\r\n"
                    f"Content-Length: {length}\r\n"
                    f"Accept-Ranges: bytes\r\n"
                    f"Connection: close\r\n"
                    f"\r\n"
                ).encode())

            pos = start
            remaining = length
            loop = asyncio.get_event_loop()

            # Serve moov portion from memory
            if pos < moov_size and remaining > 0:
                chunk_end = min(moov_size, pos + remaining)
                writer.write(moov_bytes[pos:chunk_end])
                remaining -= chunk_end - pos
                pos = chunk_end
                await writer.drain()

            # Serve mdat portion via pread on original file
            if remaining > 0:
                orig_pos = mdat_offset + (pos - moov_size)
                with open(original_path, "rb") as orig_f:
                    orig_f.seek(orig_pos)
                    while remaining > 0:
                        chunk = await loop.run_in_executor(
                            None, orig_f.read, min(remaining, 256 * 1024)
                        )
                        if not chunk:
                            break
                        writer.write(chunk)
                        remaining -= len(chunk)
                        await writer.drain()
        except Exception:
            pass
        finally:
            writer.close()

    server = await asyncio.start_server(_handle, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    return f"http://127.0.0.1:{port}/", server


def _sidecar_for(resolved_path: str) -> tuple[str, str] | None:
    """
    Return (sidecar_path, meta_path) if a moov sidecar exists for resolved_path,
    else None.
    """
    if not _SIDECAR_ROOT or not resolved_path.startswith(_SOURCE_ROOT + os.sep):
        return None
    rel = resolved_path[len(_SOURCE_ROOT) + 1:]
    sidecar = os.path.join(_SIDECAR_ROOT, rel + ".moov")
    meta = sidecar + ".json"
    if os.path.isfile(sidecar) and os.path.isfile(meta):
        return sidecar, meta
    return None


async def _probe_codecs(path: str) -> tuple[str, str, int]:
    """Return (video_codec, audio_codec, audio_sample_rate) or ('', '', 0) on failure.

    Probes both streams in a single ffprobe call.
    -probesize 10M limits reads to the first 10 MB of the file so ffprobe
    returns immediately from the container header instead of seeking to the
    moov atom at the end of large non-faststart DJI files on slow 9P mounts.
    Returns lowercase codec names e.g. ('h264', 'aac', 48000), ('hevc', 'ac3', 0).
    """
    cmd = [
        "ffprobe", "-v", "quiet",
        "-probesize", "10M",
        "-analyzeduration", "0",
        "-show_entries", "stream=codec_name,codec_type,sample_rate",
        "-of", "csv=p=0",
        path,
    ]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=15)
        video_codec = ""
        audio_codec = ""
        audio_sample_rate = 0
        for line in stdout.decode().splitlines():
            parts = line.strip().lower().split(",")
            if len(parts) >= 2:
                codec, ctype = parts[0], parts[1]
                if ctype == "video" and not video_codec:
                    video_codec = codec
                elif ctype == "audio" and not audio_codec:
                    audio_codec = codec
                    # sample_rate is the 3rd field for audio streams
                    if len(parts) >= 3 and parts[2].isdigit():
                        audio_sample_rate = int(parts[2])
        return video_codec, audio_codec, audio_sample_rate
    except Exception as e:
        log.warning("[Playlist] ffprobe failed for %s: %s", path, e)
        return "", "", 0


async def _extract_segment(resolved_path: str, start_sec: float, duration: float, out_path: str, normalize: bool = False) -> bool:
    """
    Extract a single .ts segment from a video file using ffmpeg.

    normalize=True (used by create_playlist):
      Skips stream-copy and always re-encodes to 1280×720 with letterbox
      padding.  This is required for HLS playlists because Chrome's MSE
      SourceBuffer is initialised with the codec parameters of the first
      segment and rejects appendBuffer() calls with a different resolution,
      causing HLS.js to raise a fatal bufferAppendError.

    normalize=False (default):
    Pass 1 — stream-copy (fast, lossless):
      Probes the codec first so we apply the correct MP4→Annex B bitstream
      filter for the container format:
        h264 → -bsf:v h264_mp4toannexb
        hevc → -bsf:v hevc_mp4toannexb
        other (av1, vp9…) → no bsf (direct remux)
      -map 0:v:0 -map 0:a:0? prevents track_id mismatches when mixing
      multi-track DJI files with single-track Pixel 9 files.

    Pass 2 — libx264 re-encode (CPU fallback):
      Used when stream-copy fails (codec incompatible with TS mux, or
      mismatched profiles across clips). -vsync cfr -r 30 locks frame
      rate across mixed DJI (30fps) / Pixel 9 (60fps) sources to prevent
      black flicker between segments.
      Note: NVENC is intentionally omitted — the API container has no GPU.
    """
    # Codec-specific MP4→Annex B bitstream filter required for MPEG-TS muxing
    _BSF_MAP = {"h264": "h264_mp4toannexb", "hevc": "hevc_mp4toannexb"}

    # --- Moov sidecar: present virtual faststart file to ffprobe + ffmpeg ---
    # If a sidecar exists for this file, start a local HTTP range server that
    # serves [corrected moov][original mdat...]. ffprobe reads the moov from
    # the first few MB (local, fast); ffmpeg seeks via Range requests so only
    # the keyframe bytes are fetched from the slow 9P mount.
    faststart_server: asyncio.AbstractServer | None = None
    input_path = resolved_path
    sidecar_info = _sidecar_for(resolved_path)
    if sidecar_info:
        sidecar_path, meta_path = sidecar_info
        try:
            with open(meta_path) as _f:
                _meta = json.load(_f)
            input_path, faststart_server = await _start_faststart_server(
                sidecar_path, resolved_path, _meta["mdat_offset"]
            )
            log.info("[Playlist] Using moov sidecar for %s → %s", resolved_path, input_path)
        except Exception as _e:
            log.warning("[Playlist] Sidecar setup failed for %s: %s", resolved_path, _e)
            input_path = resolved_path
            faststart_server = None

    try:
        codec, audio_codec, audio_sr = await _probe_codecs(input_path)
        # Only stream-copy AAC when it's already at a browser-compatible sample rate
        # (44100 or 48000 Hz). Pixel 9 records at 96 kHz — HLS.js's MPEG-TS demuxer
        # only supports 44100/48000 Hz AAC and throws DEMUXER_ERROR_COULD_NOT_PARSE
        # on 96 kHz. AC3/DTS/MP3 must always be transcoded (Chrome MSE rejects them).
        _COMPATIBLE_SR = {44100, 48000}
        if audio_codec == "aac" and audio_sr in _COMPATIBLE_SR:
            audio_args = ["-c:a", "copy"]
        else:
            audio_args = ["-c:a", "aac", "-b:a", "128k", "-ar", "48000"]
        log.info("[Playlist] codecs for %s: video=%s audio=%s → audio_args=%s",
                 resolved_path, codec, audio_codec, audio_args)

        if not codec:
            # ffprobe couldn't read codec — moov not readable within probesize limit.
            # If a sidecar was available it would have succeeded; skip this clip.
            log.warning("[Playlist] skipping %s — codec probe failed (non-faststart/slow mount)", resolved_path)
            return False

        # H264 stream-copy with MP4-to-Annex-B BSF. Falls through to libx264
        # re-encode if copy fails (e.g. unsupported profile).
        # HEVC is excluded from stream-copy: HEVC-in-MPEG-TS is not supported by
        # Chrome/Firefox MSE. HEVC clips go directly to libx264 re-encode below.
        # With the moov sidecar + HTTP range server, ffmpeg seeks without scanning
        # the full file, so re-encoding is fast even on slow 9P mounts.
        # normalize=True skips stream-copy entirely: HLS playlists require all
        # segments to share the same resolution because Chrome's MSE SourceBuffer
        # is initialised with the first segment's codec params and rejects
        # appendBuffer() calls when a later segment has a different resolution,
        # causing HLS.js to emit a fatal bufferAppendError.
        if not normalize and codec in _BSF_MAP and codec != "hevc":
            bsf = _BSF_MAP[codec]
            cmd_copy = [
                "ffmpeg", "-y",
                "-threads", "2",
                "-ss", str(start_sec), "-t", str(duration),
                "-i", input_path,
                "-map", "0:v:0",
                "-map", "0:a:0?",
                "-c:v", "copy",
                *audio_args,
                "-bsf:v", bsf,
                "-avoid_negative_ts", "make_zero",
                "-f", "mpegts",
                out_path,
            ]
            try:
                proc = await asyncio.create_subprocess_exec(
                    *cmd_copy,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                _, stderr = await asyncio.wait_for(proc.communicate(), timeout=20)
                if proc.returncode == 0:
                    return True
                log.warning("[Playlist] stream-copy failed for %s: %s",
                            resolved_path, stderr[-300:].decode(errors="replace"))
            except asyncio.TimeoutError:
                try:
                    proc.kill()
                except ProcessLookupError:
                    pass
                try:
                    await asyncio.wait_for(proc.communicate(), timeout=3)
                except Exception:
                    pass
                log.warning("[Playlist] stream-copy timed out for %s", resolved_path)
            except Exception as e:
                log.warning("[Playlist] stream-copy exec error for %s: %s", resolved_path, e)
        else:
            log.info("[Playlist] skipping stream-copy for %s (codec=%s, re-encoding)", resolved_path, codec)

        # --- Pass 2: libx264 re-encode (CPU only — API has no GPU) ---
        # -pix_fmt yuv420p: force 8-bit 4:2:0 — required for browser MSE compat
        # when source is 10-bit HEVC or other high-bit-depth formats.
        # Fast seek (-ss before -i) + sidecar HTTP server: ffmpeg Range-requests
        # only the bytes it needs — no full-file scan on the slow 9P mount.
        if _NVENC_AVAILABLE:
            video_enc_args = ["-c:v", "h264_nvenc", "-preset", "p2", "-rc", "vbr", "-cq", "23", "-b:v", "0"]
        else:
            video_enc_args = ["-c:v", "libx264", "-preset", "ultrafast", "-crf", "23", "-threads", "2"]
        cmd_enc = [
            "ffmpeg", "-y",
            "-ss", str(start_sec), "-t", str(duration),
            "-i", input_path,
            "-map", "0:v:0",
            "-map", "0:a:0?",
            *video_enc_args,
            # normalize=True: all playlist segments are scaled to exactly 1280×720
            # with letterbox padding so every segment shares identical SPS/PPS codec
            # parameters — required to avoid bufferAppendError in Chrome MSE.
            # normalize=False (single-clip use): cap width at 1280, preserve AR.
            "-vf", (
                "scale=1280:720:force_original_aspect_ratio=decrease,"
                "pad=1280:720:(ow-iw)/2:(oh-ih)/2,setsar=1,format=yuv420p"
                if normalize else
                "scale='min(iw,1280)':-2,format=yuv420p"
            ),
            "-fps_mode", "cfr", "-r", "30",
            "-c:a", "aac", "-b:a", "128k", "-ar", "48000",
            "-af", "loudnorm",
            "-avoid_negative_ts", "make_zero",
            "-f", "mpegts",
            out_path,
        ]
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd_enc,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await asyncio.wait_for(proc.communicate(), timeout=90)
            if proc.returncode == 0:
                log.info("[Playlist] re-encoded with libx264 for %s", resolved_path)
                return True
            log.warning("[Playlist] libx264 failed for %s: %s", resolved_path,
                        stderr[-200:].decode(errors="replace"))
        except asyncio.TimeoutError:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            try:
                await asyncio.wait_for(proc.communicate(), timeout=3)
            except Exception:
                pass
            log.warning("[Playlist] libx264 timed out for %s", resolved_path)
        except Exception as e:
            log.warning("[Playlist] libx264 exec error for %s: %s", resolved_path, e)

        return False

    finally:
        if faststart_server:
            faststart_server.close()
            await faststart_server.wait_closed()


@router.post("/playlist")
@limiter.limit(LIMIT_PLAYLIST)
async def create_playlist(request: Request, body: PlaylistRequest):
    """
    Compile a list of clip specs into a single HLS VOD playlist.

    Each ClipSpec's file_path is resolved + path-translated, the proxy is
    preferred over the original (same logic as /api/stream), and ffmpeg
    extracts each clip as a standards-compliant .ts segment.  A proper
    M3U8 manifest is written and served from /api/playlist/serve/{token}/.

    Clip boundaries should come from audio_segment_start/end_sec (VAD-aligned).
    Legacy media without those fields falls back to clip_padding_sec around
    the matched timestamp (handled by the caller — this endpoint just uses
    whatever start_sec/end_sec it receives).
    """
    if not body.clips:
        raise HTTPException(status_code=400, detail="No clips provided")

    token = str(uuid.uuid4())
    token_dir = os.path.join(PLAYLIST_DIR, token)
    os.makedirs(token_dir, exist_ok=True)

    proxy_root = os.getenv("PROXY_ROOT", _PROXY_ROOT_DEFAULT).strip()

    # Resolve and validate each clip path, prefer proxy
    resolved_clips: list[tuple[str, ClipSpec]] = []
    for clip in body.clips:
        resolved = os.path.realpath(_translate_path(clip.file_path))
        is_url = False
        # S3/R2: presign directly from the original object key — matches stream
        # endpoint behaviour. Must run before ALLOWED_ROOTS check because
        # _translate_path expands bare keys to /mnt/source/... which is an
        # allowed root, causing the S3 branch to be skipped and the file to be
        # looked up locally (where it doesn't exist).
        if IS_S3 and not clip.file_path.startswith("/"):
            try:
                resolved = _s3_presign(clip.file_path, expires=1800)
                is_url = True
            except Exception as e:
                log.warning("[Playlist] S3 presign failed for %s: %s", clip.file_path, e)
                continue
        elif not any(resolved.startswith(root) for root in ALLOWED_ROOTS):
            log.warning("[Playlist] access denied: %s", clip.file_path)
            continue
        if not is_url:
            if proxy_root and resolved.startswith(_SOURCE_ROOT + os.sep):
                rel = resolved[len(_SOURCE_ROOT) + 1:]
                proxy_candidate = os.path.join(proxy_root, rel)
                if os.path.isfile(proxy_candidate):
                    resolved = proxy_candidate
            if not os.path.isfile(resolved):
                log.warning("[Playlist] file not found: %s (resolved: %s)", clip.file_path, resolved)
                continue
        resolved_clips.append((resolved, clip))

    if not resolved_clips:
        shutil.rmtree(token_dir, ignore_errors=True)
        raise HTTPException(status_code=400, detail="No accessible clip files found")

    # Extract segments — use global semaphore to cap total ffmpeg processes
    # across all concurrent playlist requests (not just this one)
    sem = _PLAYLIST_SEM

    async def extract_one(idx: int, resolved: str, clip: ClipSpec):
        duration = max(0.1, clip.end_sec - clip.start_sec)
        out_path = os.path.join(token_dir, f"seg_{idx:03d}.ts")
        async with sem:
            ok = await _extract_segment(resolved, clip.start_sec, duration, out_path, normalize=True)
        return idx, duration, ok

    results = await asyncio.gather(*[
        extract_one(i, res, clip) for i, (res, clip) in enumerate(resolved_clips)
    ])

    successful = [(idx, dur) for idx, dur, ok in results if ok]
    if not successful:
        shutil.rmtree(token_dir, ignore_errors=True)
        raise HTTPException(status_code=500, detail="All segment extractions failed")

    total_duration = sum(dur for _, dur in successful)
    max_duration = max(dur for _, dur in successful)

    # Write HLS VOD manifest
    lines = [
        "#EXTM3U",
        "#EXT-X-VERSION:3",
        f"#EXT-X-TARGETDURATION:{math.ceil(max_duration)}",
        "#EXT-X-MEDIA-SEQUENCE:0",
        "#EXT-X-PLAYLIST-TYPE:VOD",
    ]
    for i, (idx, dur) in enumerate(successful):
        clip = resolved_clips[idx][1]
        label = clip.file_path.split("/")[-1]
        # Every clip comes from a different file/position so timestamps reset.
        # EXT-X-DISCONTINUITY tells HLS.js not to expect contiguous DTS.
        if i > 0:
            lines.append("#EXT-X-DISCONTINUITY")
        lines.append(f"#EXTINF:{dur:.6f},{label} @ {clip.start_sec:.1f}s")
        lines.append(f"/api/playlist/serve/{token}/seg_{idx:03d}.ts")
    lines.append("#EXT-X-ENDLIST")

    with open(os.path.join(token_dir, "playlist.m3u8"), "w") as f:
        f.write("\n".join(lines) + "\n")

    # Auto-cleanup after TTL — also swept on API startup
    loop = asyncio.get_event_loop()
    loop.call_later(PLAYLIST_TTL_SECS, lambda: shutil.rmtree(token_dir, ignore_errors=True))

    return PlaylistResponse(
        playlist_url=f"/api/playlist/serve/{token}/playlist.m3u8",
        token=token,
        clip_count=len(successful),
        total_duration_sec=round(total_duration, 2),
        expires_at=(datetime.utcnow() + timedelta(seconds=PLAYLIST_TTL_SECS)).isoformat(),
    )


@public_router.get("/playlist/serve/{token}/{filename}")
async def serve_playlist_file(token: str, filename: str):
    """
    Serve the M3U8 manifest or .ts segment files for a generated playlist.
    Token UUID is the sole access control — no path traversal is possible
    since both token and filename are validated before path construction.
    """
    try:
        uuid.UUID(token)  # Raises ValueError if not a valid UUID
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid token")

    if "/" in filename or "\\" in filename or filename.startswith("."):
        raise HTTPException(status_code=400, detail="Invalid filename")

    file_path = os.path.join(PLAYLIST_DIR, token, filename)
    if not os.path.isfile(file_path):
        raise HTTPException(status_code=404, detail="Playlist file not found or expired")

    if filename.endswith(".m3u8"):
        media_type = "application/vnd.apple.mpegurl"
    elif filename.endswith(".ts"):
        media_type = "video/mp2t"
    else:
        media_type = "application/octet-stream"

    async def sender():
        async with aiofiles.open(file_path, "rb") as f:
            while True:
                chunk = await f.read(STREAM_CHUNK_SIZE)
                if not chunk:
                    break
                yield chunk

    return StreamingResponse(
        sender(),
        media_type=media_type,
        headers={
            "Cache-Control": "public, max-age=3600",
            "Access-Control-Allow-Origin": "*",
        },
    )
