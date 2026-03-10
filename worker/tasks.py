"""
Celery Task Definitions
Main orchestration for media ingestion pipeline
"""

import errno as _errno
import hashlib
import logging
import os
import shutil
import socket
import tempfile
import time
import uuid
from datetime import datetime
from contextlib import contextmanager
from pathlib import Path
from typing import List, Optional

import numpy as np
from PIL import Image
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, PointStruct, VectorParams
from sqlalchemy import select

from celery_app import app
from db.models import MediaFile
from db.session import SyncSessionLocal
from ingest.crawler import crawl_media, crawl_s3
from ingest.ffmpeg import (
    FFmpegError,
    apply_faststart,
    extract_keyframes,
    extract_thumbnail,
    normalize_image,
    probe_media,
)
from ingest.hasher import compute_file_hash, get_existing_hash_record
from ml.embedder import get_embedder
from storage import get_storage_backend
import redis as _redis_sync

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Redis pub/sub: publish completion events to WS consumers
# ---------------------------------------------------------------------------

_redis_client = None


def _publish_update(payload: dict) -> None:
    """Fire-and-forget: publish a JSON update to 'lumen:media_updates' Redis channel.
    Failures are silently logged — never let a publish error interrupt processing."""
    global _redis_client
    try:
        if _redis_client is None:
            _redis_client = _redis_sync.from_url(
                os.getenv("REDIS_URL", "redis://redis:6379"),
                socket_connect_timeout=1,
                socket_timeout=1,
            )
        import json
        _redis_client.publish("lumen:media_updates", json.dumps(payload))
    except Exception as e:
        log.debug(f"Redis publish failed (non-fatal): {e}")

# ---------------------------------------------------------------------------
# Storage mode helpers
# ---------------------------------------------------------------------------

IS_S3 = os.getenv("STORAGE_BACKEND", "local").lower() == "s3"


@contextmanager
def _local_path(s3_key_or_local: str):
    """Yield a local filesystem path for file processing.

    Local mode: pass-through with no copy overhead.
    S3 mode: download object to a temp file, yield its path, then delete.
    """
    if not IS_S3:
        yield s3_key_or_local
        return
    storage = get_storage_backend()
    suffix = Path(s3_key_or_local).suffix
    tmp_fd, tmp_path = tempfile.mkstemp(suffix=suffix, prefix="lumen_s3_")
    try:
        with os.fdopen(tmp_fd, "wb") as fobj:
            fobj.write(storage.read(s3_key_or_local))
        yield tmp_path
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


def _s3_size_and_hash(key: str) -> tuple:
    """Return (file_size_bytes, sha256_hex) for an S3 object without a full download.

    Videos: byte-range read of first 8 KB (matches hasher.py local behaviour).
    Images: full download (small files; hash must cover full content).
    """
    storage = get_storage_backend()
    meta = storage.head(key)
    file_size = meta["size"]
    ext = Path(key).suffix.lower()
    is_video = ext in {".mp4", ".mov", ".mkv", ".avi", ".flv", ".wmv", ".webm", ".m4v"}
    data = storage.read_partial(key, 8192) if is_video else storage.read(key)
    return file_size, hashlib.sha256(data).hexdigest()


# Stable identifier for this worker process — used for Mac vs Windows attribution.
# Set WORKER_ID env var explicitly in docker-compose for cleaner names (e.g. "windows-1", "mac-1").
WORKER_ID = os.getenv("WORKER_ID") or socket.gethostname()

# Initialize Qdrant client
QDRANT_HOST = os.getenv("QDRANT_HOST", "qdrant")
QDRANT_PORT = int(os.getenv("QDRANT_PORT", "6333"))
QDRANT_GRPC_PORT = int(os.getenv("QDRANT_GRPC_PORT", "6334"))
QDRANT_PREFER_GRPC = os.getenv("QDRANT_PREFER_GRPC", "true").lower() == "true"
QDRANT_COLLECTION_NAME = os.getenv("QDRANT_COLLECTION_NAME", "media_vectors")

qdrant_client = QdrantClient(
    host=QDRANT_HOST,
    port=QDRANT_PORT,
    grpc_port=QDRANT_GRPC_PORT,
    prefer_grpc=QDRANT_PREFER_GRPC,
)


def _is_eio(exc: BaseException) -> bool:
    """
    Return True if exc or any chained cause is an OSError with EIO (errno 5).
    SMB/NFS mounts raise EIO when the transport drops — retrying is pointless
    until the mount is remounted by the operator.
    """
    seen: set = set()
    cur: BaseException | None = exc
    while cur and id(cur) not in seen:
        seen.add(id(cur))
        if isinstance(cur, OSError) and cur.errno == _errno.EIO:
            return True
        cur = cur.__context__ or cur.__cause__  # type: ignore[assignment]
    return False


def ensure_qdrant_collection():
    """Ensure the media_vectors collection exists"""
    try:
        qdrant_client.get_collection(QDRANT_COLLECTION_NAME)
    except Exception:
        # Collection doesn't exist — try to create it.
        # Guard against race condition: another worker may have created it
        # between our get and create calls (ALREADY_EXISTS is safe to ignore).
        try:
            vector_size = get_embedder().get_embedding_dimension()
            qdrant_client.create_collection(
                collection_name=QDRANT_COLLECTION_NAME,
                vectors_config=VectorParams(size=vector_size, distance=Distance.COSINE),
            )
            print(f"Created Qdrant collection: {QDRANT_COLLECTION_NAME} (dim={vector_size})")
        except Exception as create_err:
            if "already exists" in str(create_err).lower():
                pass  # Another worker won the race — collection exists, continue
            else:
                raise


def _frame_cache_key(file_hash: str, fps: float, resolution: int) -> str:
    """
    Build a deterministic cache key from extraction parameters.
    Model-agnostic — frames are raw pixels, independent of CLIP version.
    """
    raw = f"{file_hash}:fps={fps}:res={resolution}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def _frame_cache_dir(file_hash: str, fps: float, resolution: int) -> Path:
    """Return the cache directory for a given video + extraction params."""
    base = Path(os.getenv("FRAME_CACHE_DIR", "/tmp/lumen_frame_cache"))
    return base / _frame_cache_key(file_hash, fps, resolution)


def _get_cached_frames(file_hash: str, fps: float, resolution: int) -> Optional[List[str]]:
    """
    Return cached frame paths if a complete cache exists, else None.
    A cache is considered complete when a '.done' sentinel file is present
    (guards against partial writes from a previous interrupted extraction).
    """
    cache_dir = _frame_cache_dir(file_hash, fps, resolution)
    sentinel = cache_dir / ".done"
    if sentinel.exists():
        frames = sorted(cache_dir.glob("frame_*.jpg"))
        if frames:
            return [str(f) for f in frames]
    return None


def _save_frame_cache(file_hash: str, fps: float, resolution: int, frame_paths: List[str]) -> List[str]:
    """
    Copy freshly extracted frames into the persistent cache directory.
    Writes a '.done' sentinel only after all frames are copied.
    Returns the new frame paths inside the cache directory.
    """
    cache_dir = _frame_cache_dir(file_hash, fps, resolution)
    cache_dir.mkdir(parents=True, exist_ok=True)
    cached_paths = []
    for src in frame_paths:
        dst = cache_dir / Path(src).name
        shutil.copy2(src, dst)
        cached_paths.append(str(dst))
    (cache_dir / ".done").touch()
    log.info("[FrameCache] Saved %d frames → %s", len(cached_paths), cache_dir)
    return cached_paths


@app.task(
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_backoff_max=600,
    retry_jitter=True,
    max_retries=5,
)
def crawl_and_dispatch(self, media_root: str):
    """
    Crawl media directory and dispatch ingest tasks.
    Main entry point for the pipeline.

    Args:
        media_root: Root directory to crawl
    """
    try:
        print(f"Starting crawl of {media_root}")
        if IS_S3:
            files = crawl_s3(prefix=media_root)
        else:
            files = crawl_media(media_root)
        print(f"Found {len(files)} media files")

        # Dispatch a task for each file
        for file_path, file_type in files:
            ingest_media.delay(file_path, file_type)

        return {"status": "dispatched", "count": len(files)}
    except Exception as e:
        print(f"Crawl failed: {str(e)}")
        raise


@app.task(
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_backoff_max=600,
    retry_jitter=True,
    max_retries=5,
)
def ingest_media(self, file_path: str, file_type: str):
    """
    Lightweight ingestion task - creates DB record and dispatches to processor.
    File hash computation moved to child task to free up worker pool faster.

    Args:
        file_path: Full path to media file
        file_type: 'image' or 'video'
    """
    db = SyncSessionLocal()
    try:
        # Fastest path: if this exact file_path is already DONE in the DB, skip without
        # touching the filesystem at all. Avoids SMB reads for already-processed files.
        # Only skip 'done' — 'pending'/'processing' should be re-dispatched to the processor.
        path_done = db.query(MediaFile.id).filter(
            MediaFile.file_path == file_path,
            MediaFile.processing_status == "done",
        ).first()
        if path_done:
            return {"status": "skipped", "reason": "already_ingested"}

        # If file is pending/processing (stuck or not yet dispatched), re-dispatch to processor.
        stale = db.query(MediaFile).filter(
            MediaFile.file_path == file_path,
            MediaFile.processing_status.in_(["pending", "processing"]),
        ).first()
        if stale:
            if stale.file_type == "image":
                result = process_image.delay(file_path, str(stale.id))
            else:
                result = process_video.delay(file_path, str(stale.id))
            print(f"Re-dispatched {stale.processing_status} file: {file_path}")
            return {"status": "redispatched", "media_record_id": str(stale.id), "task_id": result.id}

        # Check file existence and compute size + hash.
        if IS_S3:
            # S3: head() for size, read_partial() for hash — no full download needed.
            try:
                file_size, file_hash = _s3_size_and_hash(file_path)
            except FileNotFoundError:
                print(f"S3 object not found: {file_path}")
                return {"status": "skipped", "reason": "file_not_found"}
            except Exception as e:
                print(f"Cannot stat/hash S3 object {file_path}: {e}")
                return {"status": "skipped", "reason": "cannot_hash_file"}
        else:
            if not os.path.isfile(file_path):
                print(f"File not found: {file_path}")
                return {"status": "skipped", "reason": "file_not_found"}

            # Get file size (fast)
            try:
                file_size = os.path.getsize(file_path)
            except Exception as e:
                print(f"Could not get file size for {file_path}: {e}")
                return {"status": "skipped", "reason": "cannot_stat_file"}

            # Compute hash here — fast (8KB read for video, full read for images)
            # Must be done before INSERT because file_hash is NOT NULL in the schema
            try:
                file_hash = compute_file_hash(file_path)
            except ValueError as e:
                print(f"Cannot hash file {file_path}: {e}")
                return {"status": "skipped", "reason": "cannot_hash_file"}

        # Check for duplicates before creating a record
        existing = db.query(MediaFile).filter(
            MediaFile.file_hash == file_hash
        ).first()
        if existing:
            print(f"Duplicate file (same hash), skipping: {file_path}")
            return {"status": "skipped", "reason": "duplicate_hash"}

        media_record = MediaFile(
            file_hash=file_hash,
            file_path=file_path,
            file_type=file_type,
            file_size_bytes=str(file_size),
            processing_status="processing",
        )
        db.add(media_record)
        db.commit()
        db.refresh(media_record)

        # Dispatch to processor — hash already set, no duplicate check needed there
        if file_type == "image":
            result = process_image.delay(file_path, str(media_record.id))
        else:
            result = process_video.delay(file_path, str(media_record.id))

        return {
            "status": "dispatched",
            "media_record_id": str(media_record.id),
            "task_id": result.id,
        }

    except Exception as e:
        print(f"Ingest failed for {file_path}: {str(e)}")
        raise
    finally:
        db.close()


@app.task(
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_backoff_max=600,
    retry_jitter=True,
    max_retries=5,
)
def process_image(self, file_path: str, media_record_id: str):
    """
    Process a single image file - compute hash, embed, and index.

    Args:
        file_path: Full path to image file
        media_record_id: ID of the MediaFile record
    """
    db = SyncSessionLocal()
    embedder = get_embedder()
    get_storage_backend()

    try:
        ensure_qdrant_collection()

        # Get media record
        media_record = db.query(MediaFile).filter_by(id=media_record_id).first()
        if not media_record:
            raise ValueError(f"Media record not found: {media_record_id}")

        # Normalize image
        temp_dir = tempfile.mkdtemp(prefix="lumen_images_")
        normalized_path = os.path.join(temp_dir, "normalized.jpg")

        try:
            from ingest.ffmpeg import normalize_image

            with _local_path(file_path) as _local_img:
                normalize_image(_local_img, normalized_path, resolution=224)

                # Extract metadata from image
                with Image.open(_local_img) as img:
                    width, height = img.size
                    # Skip EXIF extraction - causes JSON serialization issues with bytes

            media_record.width = str(width)
            media_record.height = str(height)
            # EXIF data not stored due to bytes serialization issues

            # Embed image
            print(f"Embedding image: {file_path}")
            media_record.embedding_started_at = datetime.utcnow()
            media_record.worker_id = WORKER_ID
            db.commit()
            t0 = time.monotonic()
            embeddings = embedder.embed_images([normalized_path], batch_size=1)
            embedding_ms = int((time.monotonic() - t0) * 1000)
            vector = embeddings[0].astype(np.float32)

            # Upsert to Qdrant
            point_id = str(uuid.uuid4())
            point = PointStruct(
                id=point_id,
                vector=vector.tolist(),
                payload={
                    "file_path": file_path,
                    "file_type": "image",
                    "file_hash": media_record.file_hash,
                    "created_at": datetime.utcnow().isoformat(),
                    "media_file_id": media_record_id,
                },
            )
            qdrant_client.upsert(collection_name=QDRANT_COLLECTION_NAME, points=[point])

            # Update database record
            media_record.qdrant_point_id = point_id
            media_record.processing_status = "done"
            media_record.processed_at = datetime.utcnow()
            media_record.embedding_ms = embedding_ms
            media_record.model_version = os.getenv("CLIP_MODEL_NAME", "unknown")
            db.commit()

            _publish_update({
                "channel": "media_processing",
                "id": str(media_record.id),
                "file_path": file_path,
                "file_type": "image",
                "status": "done",
                "processed_at": media_record.processed_at.isoformat(),
            })

            print(f"Successfully processed image: {file_path}")
            return {"status": "success", "media_record_id": media_record_id}

        finally:
            # Clean up temp directory
            shutil.rmtree(temp_dir, ignore_errors=True)

    except Exception as e:
        if _is_eio(e):
            # SMB mount dropped — retrying burns worker slots for hours.
            # Fail immediately; operator must remount the share then reset
            # these records: UPDATE media_files SET processing_status='pending'
            # WHERE processing_status='error' AND error_message LIKE '%EIO%';
            log.error("[EIO] SMB transport error on %s — failing fast, no retry", file_path)
            if 'media_record' in dir() and media_record is not None:
                try:
                    media_record.processing_status = "error"
                    media_record.error_message = f"SMB I/O error (EIO): {str(e)[:450]}"
                    db.commit()
                except Exception:
                    pass
            return  # suppress autoretry
        print(f"Image processing failed: {str(e)}")
        if 'media_record' in dir() and media_record is not None:
            try:
                media_record.processing_status = "error"
                media_record.error_message = str(e)[:500]
                db.commit()
            except Exception:
                pass
        raise
    finally:
        db.close()


@app.task(
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_backoff_max=600,
    retry_jitter=True,
    max_retries=5,
)
def process_video(self, file_path: str, media_record_id: str):
    """
    Process a single video file - compute hash, extract frames, embed, and index.

    Args:
        file_path: Full path to video file
        media_record_id: ID of the MediaFile record
    """
    db = SyncSessionLocal()
    embedder = get_embedder()

    try:
        ensure_qdrant_collection()

        # Get media record
        media_record = db.query(MediaFile).filter_by(id=media_record_id).first()
        if not media_record:
            raise ValueError(f"Media record not found: {media_record_id}")

        # Get video metadata
        print(f"Probing video: {file_path}")
        with _local_path(file_path) as _probe_local:
            metadata = probe_media(_probe_local)
        media_record.width = str(metadata["width"])
        media_record.height = str(metadata["height"])
        media_record.duration_secs = str(metadata["duration"])
        media_record.exif_data = {
            "codec": metadata["codec_name"],
            "frame_rate": metadata["frame_rate"],
        }
        db.commit()

        # Faststart proxy: dispatched async to the 'proxies' queue so this
        # task finishes in minutes regardless of source file size.
        # generate_proxy applies Option 2 (duration threshold) and Option 3
        # (stream-copy for H264) — see generate_proxy task below.
        proxy_root = os.getenv("PROXY_ROOT", "").strip()
        if proxy_root and not IS_S3 and file_path.startswith("/mnt/source/"):
            rel = file_path[len("/mnt/source/"):]  # preserve subdirectory tree
            proxy_path = os.path.join(proxy_root, rel)
            generate_proxy.apply_async(
                args=[file_path, proxy_path, metadata["duration"], metadata["codec_name"]],
                queue="proxies",
            )

        # Extract frames
        temp_dir = tempfile.mkdtemp(prefix="lumen_frames_")
        try:
            fps = float(os.getenv("KEYFRAME_FPS") or "0.5")
            resolution = int(os.getenv("KEYFRAME_RESOLUTION") or "224")

            # --- Frame cache check ---
            cached = _get_cached_frames(media_record.file_hash, fps, resolution)
            if cached:
                log.info("[FrameCache] HIT — %d frames for %s", len(cached), file_path)
                frame_paths = cached
            else:
                log.info("[FrameCache] MISS — extracting frames from %s", file_path)
                print(f"Extracting frames from video: {file_path}")
                with _local_path(file_path) as _extract_local:
                    raw_frame_paths = extract_keyframes(
                        _extract_local,
                        temp_dir,
                        fps=fps,
                        resolution=resolution,
                        video_duration=metadata["duration"],
                    )
                print(f"Extracted {len(raw_frame_paths)} frames")

                if not raw_frame_paths:
                    raise FFmpegError(f"No frames extracted from {file_path}")

                frame_paths = _save_frame_cache(media_record.file_hash, fps, resolution, raw_frame_paths)

            # Record cache hit/miss and start embedding
            cache_hit = cached is not None
            media_record.frame_cache_hit = cache_hit
            media_record.embedding_started_at = datetime.utcnow()
            media_record.worker_id = WORKER_ID
            db.commit()

            # Embed frames in batches
            batch_size = int(os.getenv("EMBEDDING_BATCH_SIZE") or "32")
            print(f"Embedding {len(frame_paths)} frames with batch size {batch_size}")
            t0 = time.monotonic()
            embeddings = embedder.embed_frames(frame_paths, batch_size=batch_size)
            embedding_ms = int((time.monotonic() - t0) * 1000)

            # Prepare Qdrant points (one per frame)
            points = []
            for frame_idx, embedding in enumerate(embeddings):
                point_id = str(uuid.uuid4())
                points.append(
                    PointStruct(
                        id=point_id,
                        vector=embedding.astype(np.float32).tolist(),
                        payload={
                            "file_path": file_path,
                            "file_type": "video",
                            "file_hash": media_record.file_hash,
                            "frame_index": frame_idx,
                            "timestamp": (frame_idx / fps),
                            "created_at": datetime.utcnow().isoformat(),
                            "media_file_id": media_record_id,
                        },
                    )
                )

            # Upsert to Qdrant
            print(f"Upserting {len(points)} vectors to Qdrant")
            qdrant_client.upsert(collection_name=QDRANT_COLLECTION_NAME, points=points)

            # Update database record (use first frame point ID as reference)
            if points:
                media_record.qdrant_point_id = points[0].id
            media_record.processing_status = "done"
            media_record.processed_at = datetime.utcnow()
            media_record.embedding_ms = embedding_ms
            media_record.model_version = os.getenv("CLIP_MODEL_NAME", "unknown")
            db.commit()

            _publish_update({
                "channel": "media_processing",
                "id": str(media_record.id),
                "file_path": file_path,
                "file_type": "video",
                "status": "done",
                "processed_at": media_record.processed_at.isoformat(),
            })

            print(f"Successfully processed video: {file_path}")
            return {
                "status": "success",
                "media_record_id": media_record_id,
                "frames_processed": len(frame_paths),
            }

        finally:
            # Clean up temp directory
            shutil.rmtree(temp_dir, ignore_errors=True)

    except Exception as e:
        if _is_eio(e):
            log.error("[EIO] SMB transport error on %s — failing fast, no retry", file_path)
            if 'media_record' in dir() and media_record is not None:
                try:
                    media_record.processing_status = "error"
                    media_record.error_message = f"SMB I/O error (EIO): {str(e)[:450]}"
                    db.commit()
                except Exception:
                    pass
            return  # suppress autoretry
        print(f"Video processing failed: {str(e)}")
        # media_record may not be assigned if failure occurred before the DB
        # query (e.g. ensure_qdrant_collection raised before we fetched it)
        if 'media_record' in dir() and media_record is not None:
            try:
                media_record.processing_status = "error"
                media_record.error_message = str(e)[:500]
                db.commit()
            except Exception:
                pass
        raise
    finally:
        db.close()


@app.task(
    bind=True,
    autoretry_for=(FFmpegError, OSError),
    retry_backoff=True,
    retry_backoff_max=600,
    retry_jitter=True,
    max_retries=3,
)
def generate_proxy(self, file_path: str, proxy_path: str, duration: float, codec: str) -> dict:
    """
    Async proxy generation — runs on the 'proxies' queue so it never blocks
    the main ingest pipeline.

    Decision matrix (Options 2 + 3):
      H264 source                     → stream-copy (seconds, full resolution)
      Non-H264, duration ≤ threshold  → transcode to 720p H264
      Non-H264, duration >  threshold → skip (too expensive, not worth it)

    Set PROXY_MAX_DURATION_SECS env var to control the skip threshold
    (default: 3600 = 1 hour).
    """
    max_dur = float(os.getenv("PROXY_MAX_DURATION_SECS") or "3600")

    if codec != "h264" and duration > max_dur:
        print(
            f"[Proxy] Skipping — non-H264 ({codec}) and duration "
            f"{duration:.0f}s > threshold {max_dur:.0f}s: {file_path}"
        )
        return {"status": "skipped", "reason": "duration_threshold", "file_path": file_path}

    try:
        apply_faststart(file_path, proxy_path, duration, source_codec=codec)
        return {"status": "success", "file_path": file_path, "proxy_path": proxy_path}
    except FFmpegError as e:
        print(f"[Proxy] Warning (non-fatal): {e}")
        return {"status": "error", "reason": str(e), "file_path": file_path}


@app.task(
    bind=True,
    max_retries=0,
    time_limit=86400,       # 24 h hard limit
    soft_time_limit=82800,  # 23 h soft — gives task time to flush final stats
)
def backfill_captions(self, dry_run: bool = False):
    """
    Backfill captions for all Qdrant video-frame points using moondream VLM.

    - Reads frames from the local frame cache (FRAME_CACHE_DIR env var).
    - Calls moondream via the native Ollama /api/generate endpoint.
    - Updates each Qdrant point in-place with set_payload — no re-indexing.
    - Idempotent: points that already have a caption are skipped.
    - Safe to re-run after a partial failure — progress is committed per frame.

    Returns a summary dict: {total, processed, skipped, failed}.
    """
    import base64
    import requests as _requests

    fps = float(os.getenv("KEYFRAME_FPS", "0.5"))
    resolution = int(os.getenv("KEYFRAME_RESOLUTION", "224"))
    frame_cache_base = Path(os.getenv("FRAME_CACHE_DIR", "/mnt/frame_cache"))
    caption_model = os.getenv("CAPTION_MODEL", "moondream")

    # Derive native Ollama base from the OpenAI-compat LLM_BASE_URL
    llm_base = os.getenv("LLM_BASE_URL", "http://172.18.0.1:11434/v1")
    ollama_base = llm_base.replace("/v1", "").rstrip("/")
    generate_url = f"{ollama_base}/api/generate"

    total = processed = skipped = failed = 0
    offset = None
    scroll_batch = 100

    log.info("[Backfill] Starting caption backfill — model=%s dry_run=%s", caption_model, dry_run)

    while True:
        records, next_offset = qdrant_client.scroll(
            collection_name=QDRANT_COLLECTION_NAME,
            limit=scroll_batch,
            offset=offset,
            with_payload=True,
            with_vectors=False,
        )

        if not records:
            break

        for point in records:
            total += 1
            payload = point.payload or {}

            # Skip non-video points and already-captioned points
            if payload.get("file_type") != "video" or payload.get("caption"):
                skipped += 1
                continue

            file_hash = payload.get("file_hash")
            frame_index = payload.get("frame_index")
            if file_hash is None or frame_index is None:
                skipped += 1
                continue

            # Locate the cached frame image
            cache_dir = frame_cache_base / _frame_cache_key(file_hash, fps, resolution)
            sorted_frames = sorted(cache_dir.glob("frame_*.jpg"))
            if not sorted_frames or frame_index >= len(sorted_frames):
                log.warning("[Backfill] Frame not in cache: hash=%s idx=%s dir=%s", file_hash, frame_index, cache_dir)
                failed += 1
                continue

            frame_path = sorted_frames[frame_index]

            if dry_run:
                processed += 1
                continue

            # Call moondream
            try:
                with open(frame_path, "rb") as fh:
                    img_b64 = base64.b64encode(fh.read()).decode()

                resp = _requests.post(
                    generate_url,
                    json={
                        "model": caption_model,
                        "prompt": "Describe what you see in this image in one concise sentence.",
                        "images": [img_b64],
                        "stream": False,
                    },
                    timeout=180,
                )
                resp.raise_for_status()
                caption = resp.json().get("response", "").strip()

                if caption:
                    qdrant_client.set_payload(
                        collection_name=QDRANT_COLLECTION_NAME,
                        payload={"caption": caption},
                        points=[point.id],
                    )
                    processed += 1
                else:
                    log.warning("[Backfill] Empty caption for point %s", point.id)
                    failed += 1

            except Exception as exc:
                log.warning("[Backfill] Caption failed for point %s (%s): %s", point.id, frame_path, exc)
                failed += 1

        if (processed + failed) > 0 and (processed + failed) % 500 == 0:
            log.info("[Backfill] Progress — processed=%d skipped=%d failed=%d total_seen=%d", processed, skipped, failed, total)

        if next_offset is None:
            break
        offset = next_offset

    summary = {"status": "complete", "total": total, "processed": processed, "skipped": skipped, "failed": failed}
    log.info("[Backfill] Done: %s", summary)
    return summary


@app.task(
    bind=True,
)
def health_check(self):
    """
    Health check task - verifies all components are accessible.
    """
    try:
        # Check Qdrant
        qdrant_client.get_collections()

        # Check PostgreSQL
        db = SyncSessionLocal()
        db.execute(select(1))
        db.close()

        # Check embedder
        get_embedder()

        return {
            "status": "healthy",
            "qdrant": "ok",
            "postgres": "ok",
            "embedder": "ok",
        }
    except Exception as e:
        return {
            "status": "unhealthy",
            "error": str(e),
        }


