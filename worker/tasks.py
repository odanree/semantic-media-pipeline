"""
Celery Task Definitions
Main orchestration for media ingestion pipeline
"""

import os
import shutil
import tempfile
import uuid
from datetime import datetime
from pathlib import Path
from typing import List

import numpy as np
from PIL import Image
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, PointStruct, VectorParams
from sqlalchemy import select

from celery_app import app
from db.models import MediaFile
from db.session import SyncSessionLocal
from ingest.crawler import crawl_media
from ingest.ffmpeg import (
    FFmpegError,
    extract_keyframes,
    extract_thumbnail,
    normalize_image,
    probe_media,
)
from ingest.hasher import compute_file_hash, get_existing_hash_record
from ml.embedder import get_embedder
from storage import get_storage_backend


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


def ensure_qdrant_collection():
    """Ensure the media_vectors collection exists"""
    try:
        qdrant_client.get_collection(QDRANT_COLLECTION_NAME)
    except Exception:
        # Collection doesn't exist, create it
        qdrant_client.create_collection(
            collection_name=QDRANT_COLLECTION_NAME,
            vectors_config=VectorParams(size=512, distance=Distance.COSINE),
        )
        print(f"Created Qdrant collection: {QDRANT_COLLECTION_NAME}")


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
        # Fast path: check if file exists and is readable
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
    storage = get_storage_backend()

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

            normalize_image(file_path, normalized_path, resolution=224)

            # Extract metadata from image
            with Image.open(file_path) as img:
                width, height = img.size
                # Skip EXIF extraction - causes JSON serialization issues with bytes

            media_record.width = str(width)
            media_record.height = str(height)
            # EXIF data not stored due to bytes serialization issues

            # Embed image
            print(f"Embedding image: {file_path}")
            embeddings = embedder.embed_images([normalized_path], batch_size=1)
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
            db.commit()

            print(f"Successfully processed image: {file_path}")
            return {"status": "success", "media_record_id": media_record_id}

        finally:
            # Clean up temp directory
            shutil.rmtree(temp_dir, ignore_errors=True)

    except Exception as e:
        print(f"Image processing failed: {str(e)}")
        media_record.processing_status = "error"
        media_record.error_message = str(e)
        db.commit()
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
        metadata = probe_media(file_path)
        media_record.width = str(metadata["width"])
        media_record.height = str(metadata["height"])
        media_record.duration_secs = str(metadata["duration"])
        media_record.exif_data = {
            "codec": metadata["codec_name"],
            "frame_rate": metadata["frame_rate"],
        }
        db.commit()

        # Extract frames
        temp_dir = tempfile.mkdtemp(prefix="lumen_frames_")
        try:
            print(f"Extracting frames from video: {file_path}")
            frame_paths = extract_keyframes(
                file_path,
                temp_dir,
                fps=float(os.getenv("KEYFRAME_FPS", "0.5")),
                resolution=int(os.getenv("KEYFRAME_RESOLUTION", "224")),
            )
            print(f"Extracted {len(frame_paths)} frames")

            if not frame_paths:
                raise FFmpegError(f"No frames extracted from {file_path}")

            # Embed frames in batches
            batch_size = int(os.getenv("EMBEDDING_BATCH_SIZE", "32"))
            print(f"Embedding {len(frame_paths)} frames with batch size {batch_size}")
            embeddings = embedder.embed_frames(frame_paths, batch_size=batch_size)

            # Prepare Qdrant points (one per frame)
            frame_index = 0
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
                            "timestamp": (frame_idx / float(os.getenv("KEYFRAME_FPS", "0.5"))),
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
            db.commit()

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
        print(f"Video processing failed: {str(e)}")
        media_record.processing_status = "error"
        media_record.error_message = str(e)
        db.commit()
        raise
    finally:
        db.close()


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
        embedder = get_embedder()

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


from PIL import Image
