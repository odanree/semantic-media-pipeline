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
import uuid
from datetime import datetime, timedelta
from typing import List

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
    os.path.realpath("/mnt/i-media"),
]


def _translate_path(path: str) -> str:
    """Map Windows/host paths stored in DB to container mount paths.
    Uses LUMEN_PATH_MAP_n=/linux/prefix:C:/win/path env vars (Linux prefix first,
    same format as worker). Longest-win-prefix-first matching.
    No-op if path already starts with an allowed root or no maps are configured.
    """
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

    return Response(
        content=stdout,
        media_type="image/jpeg",
        headers={
            # Cache in browser for 24 h — thumbnails are deterministic
            "Cache-Control": "public, max-age=86400",
        },
    )


# ===========================================================================
# HLS Highlight Reel playlist generation
# ===========================================================================

PLAYLIST_DIR = "/tmp/lumen_playlists"
PLAYLIST_TTL_SECS = 3600
_MAX_CONCURRENT_SEGMENTS = 4
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


async def _probe_video_codec(path: str) -> str:
    """Return the video codec name ('h264', 'hevc', 'av1', …) or '' on failure.

    -probesize 10M limits reads to the first 10 MB of the file so ffprobe
    returns immediately from the container header instead of seeking to the
    moov atom at the end of large non-faststart DJI files on slow 9P mounts.
    """
    cmd = [
        "ffprobe", "-v", "quiet",
        "-probesize", "10M",
        "-analyzeduration", "0",
        "-select_streams", "v:0",
        "-show_entries", "stream=codec_name",
        "-of", "default=noprint_wrappers=1:nokey=1",
        path,
    ]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=15)
        return stdout.decode().strip().lower()
    except Exception as e:
        log.warning("[Playlist] ffprobe failed for %s: %s", path, e)
        return ""


async def _extract_segment(resolved_path: str, start_sec: float, duration: float, out_path: str) -> bool:
    """
    Extract a single .ts segment from a video file using ffmpeg.

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

    codec = await _probe_video_codec(resolved_path)

    if not codec:
        # ffprobe couldn't read codec within probesize limit — moov atom is
        # likely at the end of a large non-faststart file on a slow 9P mount.
        # Both stream-copy and re-encode would hang waiting for moov; skip.
        log.warning("[Playlist] skipping %s — codec probe failed (non-faststart/slow mount)", resolved_path)
        return False

    # Only H264 can be stream-copied into MPEG-TS and decoded by Chrome/Edge via
    # MSE. HEVC stream-copy produces valid .ts but browsers display black video
    # (audio only) without hardware HEVC decoder. Go straight to re-encode for
    # any non-H264 codec.
    if codec == "h264":
        bsf = _BSF_MAP["h264"]
        cmd_copy = [
            "ffmpeg", "-y",
            "-ss", str(start_sec), "-t", str(duration),
            "-i", resolved_path,
            "-map", "0:v:0",
            "-map", "0:a:0?",
            "-c", "copy",
            "-bsf:v", bsf,
            "-avoid_negative_ts", "make_zero",
            "-muxdelay", "0",
            "-f", "mpegts",
            out_path,
        ]
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd_copy,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await asyncio.wait_for(proc.communicate(), timeout=90)
            if proc.returncode == 0:
                return True
            log.warning("[Playlist] stream-copy failed for %s: %s",
                        resolved_path, stderr[-300:].decode(errors="replace"))
        except asyncio.TimeoutError:
            log.warning("[Playlist] stream-copy timed out for %s", resolved_path)
        except Exception as e:
            log.warning("[Playlist] stream-copy exec error for %s: %s", resolved_path, e)
    else:
        log.info("[Playlist] skipping stream-copy for %s (codec=%s, re-encoding to H264)", resolved_path, codec)

    # --- Pass 2: libx264 re-encode (CPU only — API has no GPU) ---
    # -pix_fmt yuv420p: force 8-bit 4:2:0 — required for browser MSE compat
    # when source is 10-bit HEVC (Pixel 9) or other high-bit-depth formats.
    cmd_enc = [
        "ffmpeg", "-y",
        "-ss", str(start_sec), "-t", str(duration),
        "-i", resolved_path,
        "-map", "0:v:0",
        "-map", "0:a:0?",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
        "-pix_fmt", "yuv420p",
        "-fps_mode", "cfr", "-r", "30",
        "-c:a", "aac", "-b:a", "128k",
        "-avoid_negative_ts", "make_zero",
        "-muxdelay", "0",
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
        log.warning("[Playlist] libx264 timed out for %s", resolved_path)
    except Exception as e:
        log.warning("[Playlist] libx264 exec error for %s: %s", resolved_path, e)

    return False


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
        if not any(resolved.startswith(root) for root in ALLOWED_ROOTS):
            log.warning("[Playlist] access denied: %s", clip.file_path)
            continue
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
            ok = await _extract_segment(resolved, clip.start_sec, duration, out_path)
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


@router.get("/playlist/serve/{token}/{filename}")
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
