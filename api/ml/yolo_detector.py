"""
YOLO-based object detection layer for Lumen.

Wraps ultralytics YOLOv8/v11 with:
  - Lazy model loading (cached singleton per process)
  - Detection result → Qdrant payload enrichment schema
  - Configurable model variant: YOLO_MODEL_NAME env var
  - Confidence threshold: YOLO_CONF_THRESHOLD env var
  - Device selection: YOLO_DEVICE env var (auto | cpu | cuda | mps)

Designed to be called from:
  1. api/routers/detect.py (real-time API endpoint)
  2. worker/tasks.py (batch enrichment during ingest — future wiring)

The detection payload written to Qdrant mirrors the existing payload schema
so search.py retrieval and reranking work unchanged:
    {
        "yolo_labels": ["person", "car"],          # unique detected classes
        "yolo_detections": [                        # full detection list
            {"label": "person", "confidence": 0.91, "bbox": [x1, y1, x2, y2]},
            ...
        ],
        "yolo_object_count": 3,
        "yolo_model": "yolov8n",
    }
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

_YOLO_MODEL_NAME = os.getenv("YOLO_MODEL_NAME", "yolov8n")
_YOLO_CONF = float(os.getenv("YOLO_CONF_THRESHOLD", "0.25"))
_YOLO_DEVICE = os.getenv("YOLO_DEVICE", "auto")

# Lazy singleton — loaded once per process on first call
_model = None


def _get_device() -> str:
    if _YOLO_DEVICE != "auto":
        return _YOLO_DEVICE
    try:
        import torch
        if torch.cuda.is_available():
            return "cuda"
        if hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
            return "mps"
    except ImportError:
        pass
    return "cpu"


def get_yolo_model():
    """Get or initialize the YOLO model singleton."""
    global _model
    if _model is None:
        try:
            from ultralytics import YOLO  # type: ignore[import]
        except ImportError:
            raise RuntimeError(
                "ultralytics not installed: pip install ultralytics"
            )
        device = _get_device()
        log.info("Loading YOLO model '%s' on device '%s'", _YOLO_MODEL_NAME, device)
        _model = YOLO(_YOLO_MODEL_NAME)
        _model.to(device)
        log.info("✓ YOLO model loaded")
    return _model


def detect_from_path(
    image_path: str | Path,
    conf: float | None = None,
    max_detections: int = 100,
) -> dict[str, Any]:
    """
    Run YOLO inference on a local image or video frame path.

    Args:
        image_path: Local filesystem path to an image file.
        conf:       Confidence threshold (defaults to YOLO_CONF_THRESHOLD env var).
        max_detections: Cap on returned detections per image.

    Returns:
        Dict ready to be merged into a Qdrant payload (or returned as API JSON).
    """
    model = get_yolo_model()
    conf = conf if conf is not None else _YOLO_CONF

    results = model.predict(
        source=str(image_path),
        conf=conf,
        max_det=max_detections,
        verbose=False,
    )

    return _results_to_payload(results, conf)


def detect_from_bytes(
    image_bytes: bytes,
    conf: float | None = None,
    max_detections: int = 100,
) -> dict[str, Any]:
    """
    Run YOLO inference on raw image bytes (e.g. from an API upload or video frame).

    Uses numpy/PIL to decode — no temp file written to disk.
    """
    import io
    import numpy as np
    from PIL import Image  # type: ignore[import]

    model = get_yolo_model()
    conf = conf if conf is not None else _YOLO_CONF

    img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    arr = np.array(img)

    results = model.predict(
        source=arr,
        conf=conf,
        max_det=max_detections,
        verbose=False,
    )

    return _results_to_payload(results, conf)


def _results_to_payload(results, conf: float) -> dict[str, Any]:
    """Convert ultralytics Results list → Lumen payload dict."""
    detections = []
    for result in results:
        boxes = result.boxes
        if boxes is None:
            continue
        names = result.names  # class id → label string
        for box in boxes:
            cls_id = int(box.cls[0])
            label = names.get(cls_id, str(cls_id))
            confidence = round(float(box.conf[0]), 4)
            # xyxy format: [x1, y1, x2, y2] in pixel coords
            bbox = [round(float(v), 1) for v in box.xyxy[0].tolist()]
            detections.append({
                "label": label,
                "confidence": confidence,
                "bbox": bbox,
            })

    unique_labels = sorted({d["label"] for d in detections})

    return {
        "yolo_labels": unique_labels,
        "yolo_detections": detections,
        "yolo_object_count": len(detections),
        "yolo_model": _YOLO_MODEL_NAME,
        "yolo_conf_threshold": conf,
    }
