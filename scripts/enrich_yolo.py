#!/usr/bin/env python3
"""
Batch-enrich all construction-phase frames in Qdrant with YOLO object detections.

Reads file_path + timestamp from each Qdrant record, extracts the frame (image
or video seek), runs YOLOv8 inference, and writes yolo_labels / yolo_detections /
yolo_object_count back as payload fields.

Usage:
    python scripts/enrich_yolo.py [--dry-run] [--batch BATCH]
"""

import argparse
import os
os.environ["YOLO_AUTOINSTALL"] = "False"   # suppress ultralytics pip auto-update noise
os.environ["YOLO_VERBOSE"]     = "False"
import sys
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import cv2
import numpy as np
from qdrant_client import QdrantClient
from qdrant_client.models import SetPayloadOperation, SetPayload

sys.path.insert(0, str(Path(__file__).parent.parent))
from worker.ml.yolo_detector import get_yolo_model

QDRANT_HOST     = os.environ.get("QDRANT_HOST", "localhost")
QDRANT_PORT     = int(os.environ.get("QDRANT_PORT", 6333))
COLLECTION_NAME = os.environ.get("QDRANT_COLLECTION_NAME", "media_vectors")

# Docker container paths → local Windows paths
MOUNT_MAP: list[tuple[str, str]] = [
    ("/mnt/i-media", "I:/i-media"),
    ("/mnt/j-media", "J:/j-media"),
]

# Number of frames to prefetch from disk while GPU processes the current batch
IO_THREADS = 8

VIDEO_EXTS = {".mp4", ".mov", ".avi", ".mkv", ".mts", ".m2ts"}


def _resolve_path(qdrant_path: str) -> str | None:
    for mnt, local in MOUNT_MAP:
        if qdrant_path.startswith(mnt):
            return local + qdrant_path[len(mnt):]
    return None


def _load_frame(args: tuple) -> tuple:
    """
    Load a single frame from disk.
    args = (point_id, local_path, timestamp_sec)
    Returns (point_id, np.ndarray | None)
    """
    pid, local_path, timestamp = args
    try:
        ext = Path(local_path).suffix.lower()
        if ext in VIDEO_EXTS:
            cap = cv2.VideoCapture(local_path)
            cap.set(cv2.CAP_PROP_POS_MSEC, timestamp * 1000)
            ok, frame = cap.read()
            cap.release()
            if not ok or frame is None:
                return pid, None
            arr = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        else:
            # Image file — read directly
            arr = cv2.cvtColor(cv2.imread(local_path), cv2.COLOR_BGR2RGB)
            if arr is None:
                return pid, None
        return pid, arr
    except Exception:
        return pid, None


def main(dry_run: bool = False, batch_size: int = 32):
    t_start = time.perf_counter()

    client = QdrantClient(host=QDRANT_HOST, port=QDRANT_PORT)

    model = get_yolo_model()
    print(f"YOLO model ready.\n")

    # Pass 1 — collect construction asset IDs, local paths, and timestamps
    print("Pass 1: scanning Qdrant for construction assets …")
    # (point_id, local_path, timestamp_sec)
    assets: list[tuple[int | str, str, float]] = []
    scanned = 0
    offset = None
    while True:
        records, next_offset = client.scroll(
            collection_name=COLLECTION_NAME,
            with_vectors=False,
            with_payload=["file_path", "timestamp"],
            limit=1000,
            offset=offset,
        )
        if not records:
            break
        scanned += len(records)
        for r in records:
            fp = (r.payload or {}).get("file_path", "")
            if "Construction Timeline" not in fp:
                continue
            local = _resolve_path(fp)
            if not local or not Path(local).exists():
                continue
            timestamp = float((r.payload or {}).get("timestamp", 0.0))
            assets.append((r.id, local, timestamp))
        print(f"  scanned {scanned:,}  found {len(assets):,} …", end="\r")
        if next_offset is None:
            break
        offset = next_offset

    t_scan = time.perf_counter()
    print(f"\n{len(assets):,} construction assets to enrich  ({t_scan - t_start:.1f}s scan)\n")

    if not assets:
        print("Nothing to process.")
        return

    # Pass 2 — read frames in parallel, run YOLO in batches, write payloads
    print(f"Pass 2: YOLO inference  (batch={batch_size}, io_threads={IO_THREADS}) …")

    updated = 0
    skipped = 0
    t_last_print = time.perf_counter()

    with ThreadPoolExecutor(max_workers=IO_THREADS) as pool:
        for batch_start in range(0, len(assets), batch_size):
            batch = assets[batch_start : batch_start + batch_size]

            futures = {pool.submit(_load_frame, item): item[0] for item in batch}
            loaded: list[tuple[int | str, np.ndarray]] = []
            for future in as_completed(futures):
                pid, arr = future.result()
                if arr is not None:
                    loaded.append((pid, arr))
                else:
                    skipped += 1

            if not loaded:
                continue

            ids  = [pid for pid, _ in loaded]
            arrs = [arr for _, arr in loaded]

            results = model.predict(source=arrs, conf=0.25, verbose=False)

            if not dry_run:
                payloads_by_id: dict[int | str, dict] = {}
                for pid, result in zip(ids, results):
                    boxes = result.boxes
                    detections = []
                    if boxes is not None:
                        for box in boxes:
                            cls_id = int(box.cls[0])
                            label  = result.names.get(cls_id, str(cls_id))
                            conf   = round(float(box.conf[0]), 4)
                            bbox   = [round(float(v), 1) for v in box.xyxy[0].tolist()]
                            detections.append({"label": label, "confidence": conf, "bbox": bbox})
                    payloads_by_id[pid] = {
                        "yolo_labels":       sorted({d["label"] for d in detections}),
                        "yolo_detections":   detections,
                        "yolo_object_count": len(detections),
                        "yolo_model":        os.environ.get("YOLO_MODEL_NAME", "yolov8n"),
                    }

                # Group by identical label sets → fewer set_payload calls
                label_groups: dict[str, list] = defaultdict(list)
                for pid, payload in payloads_by_id.items():
                    label_groups[",".join(payload["yolo_labels"])].append(pid)

                for label_key, pids in label_groups.items():
                    client.set_payload(
                        collection_name=COLLECTION_NAME,
                        payload={"yolo_labels": label_key.split(",") if label_key else []},
                        points=pids,
                    )

                # Per-point fields in one batched op
                client.batch_update_points(
                    collection_name=COLLECTION_NAME,
                    update_operations=[
                        SetPayloadOperation(
                            set_payload=SetPayload(
                                payload={
                                    "yolo_detections":   payloads_by_id[pid]["yolo_detections"],
                                    "yolo_object_count": payloads_by_id[pid]["yolo_object_count"],
                                    "yolo_model":        payloads_by_id[pid]["yolo_model"],
                                },
                                points=[pid],
                            )
                        )
                        for pid in ids
                    ],
                )

            updated += len(loaded)

            now = time.perf_counter()
            if now - t_last_print >= 2.0:
                elapsed   = now - t_scan
                rate      = updated / elapsed
                remaining = (len(assets) - updated) / rate if rate > 0 else 0
                print(
                    f"  {updated:,}/{len(assets):,} ({updated/len(assets)*100:.0f}%)  "
                    f"{rate:.0f} img/s  "
                    f"ETA {remaining/60:.1f} min",
                    end="\r",
                )
                t_last_print = now

    t_end = time.perf_counter()
    total = t_end - t_start
    inference_time = t_end - t_scan
    rate = updated / inference_time if inference_time > 0 else 0

    print(f"\n\n{'='*50}")
    print(f"  Done.")
    print(f"  Updated : {updated:,}")
    print(f"  Skipped : {skipped:,}  (unreadable frame)")
    print(f"  Scan    : {t_scan - t_start:.1f}s")
    print(f"  Inference + write: {inference_time:.1f}s  ({rate:.0f} img/s)")
    print(f"  Total   : {total/60:.1f} min  ({total:.0f}s)")
    if dry_run:
        print("  (dry-run — no payloads written)")
    print(f"{'='*50}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--batch",   type=int, default=32)
    args = parser.parse_args()
    main(dry_run=args.dry_run, batch_size=args.batch)
