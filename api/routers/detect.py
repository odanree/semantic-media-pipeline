"""
Object detection endpoint — POST /api/detect

Accepts an uploaded image and returns YOLO detection results.
Works as a standalone endpoint; future wiring into worker/tasks.py
will enrich Qdrant payloads with detections at ingest time.

Request:  multipart/form-data  with field `file` (image/*) and optional `conf` (float)
Response: DetectResponse with full bounding box list + label summary

Rate limit: RATE_LIMIT_DETECT env var (default: 20/minute) — detection is
GPU-bound and more expensive than CLIP text search.

Env vars:
    YOLO_MODEL_NAME        yolov8n (default) | yolov8s | yolov8m | yolov11n | ...
    YOLO_CONF_THRESHOLD    0.25 (default)
    YOLO_DEVICE            auto | cpu | cuda | mps
"""

from __future__ import annotations

import logging
import time
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Request, UploadFile
from pydantic import BaseModel

from rate_limit import limiter
from worker.ml.yolo_detector import detect_from_bytes  # type: ignore[import]

import os

log = logging.getLogger(__name__)

router = APIRouter()

LIMIT_DETECT = os.getenv("RATE_LIMIT_DETECT", "20/minute")

_ALLOWED_CONTENT_TYPES = {
    "image/jpeg", "image/png", "image/webp", "image/gif",
    "image/bmp", "image/tiff",
}
_MAX_UPLOAD_BYTES = int(os.getenv("DETECT_MAX_BYTES", str(20 * 1024 * 1024)))  # 20 MB


class Detection(BaseModel):
    label: str
    confidence: float
    bbox: List[float]  # [x1, y1, x2, y2]


class DetectResponse(BaseModel):
    labels: List[str]           # unique detected class names
    detections: List[Detection] # full bounding-box list
    object_count: int
    model: str
    conf_threshold: float
    execution_time_ms: float


@router.post("/detect", response_model=DetectResponse, tags=["cv"])
@limiter.limit(LIMIT_DETECT)
async def detect_objects(
    request: Request,
    file: UploadFile,
    conf: Optional[float] = None,
) -> DetectResponse:
    """
    Run YOLO object detection on an uploaded image.

    Returns detected objects with bounding boxes, confidence scores,
    and class labels. Supports all common image formats (JPEG, PNG, WebP, etc.).

    Args:
        file: Image file upload (multipart/form-data)
        conf: Confidence threshold 0–1 (optional; overrides YOLO_CONF_THRESHOLD)
    """
    # Content-type guard — defend against non-image uploads
    content_type = (file.content_type or "").lower().split(";")[0].strip()
    if content_type not in _ALLOWED_CONTENT_TYPES:
        raise HTTPException(
            status_code=415,
            detail=(
                f"Unsupported media type '{content_type}'. "
                f"Accepted: {', '.join(sorted(_ALLOWED_CONTENT_TYPES))}"
            ),
        )

    image_bytes = await file.read()
    if len(image_bytes) > _MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"Image exceeds {_MAX_UPLOAD_BYTES // (1024*1024)} MB limit.",
        )
    if not image_bytes:
        raise HTTPException(status_code=400, detail="Empty file upload.")

    t0 = time.perf_counter()
    try:
        payload = detect_from_bytes(image_bytes, conf=conf)
    except RuntimeError as exc:
        # ultralytics not installed — return 503 so the caller knows the
        # service is functional but the CV feature is not yet configured
        log.error("YOLO inference failed: %s", exc)
        raise HTTPException(status_code=503, detail=str(exc))
    except Exception as exc:
        log.error("Unexpected detection error: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail="Detection failed.")
    elapsed_ms = (time.perf_counter() - t0) * 1000

    return DetectResponse(
        labels=payload["yolo_labels"],
        detections=[Detection(**d) for d in payload["yolo_detections"]],
        object_count=payload["yolo_object_count"],
        model=payload["yolo_model"],
        conf_threshold=payload["yolo_conf_threshold"],
        execution_time_ms=round(elapsed_ms, 1),
    )
