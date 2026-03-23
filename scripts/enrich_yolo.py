#!/usr/bin/env python3
"""
Batch-enrich all construction-phase frames in Qdrant with YOLO object detections.

Reads file_path from each Qdrant record, runs YOLOv8 inference, and writes
yolo_labels / yolo_detections / yolo_object_count back as payload fields.

Usage:
    python scripts/enrich_yolo.py [--dry-run] [--batch BATCH]
"""

import argparse
import os
os.environ["YOLO_AUTOINSTALL"] = "False"   # suppress ultralytics pip auto-update noise
os.environ["YOLO_VERBOSE"]     = "False"
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import numpy as np
from PIL import Image
from qdrant_client import QdrantClient

sys.path.insert(0, str(Path(__file__).parent.parent))
from worker.ml.yolo_detector import get_yolo_model

QDRANT_HOST     = os.environ.get("QDRANT_HOST", "localhost")
QDRANT_PORT     = int(os.environ.get("QDRANT_PORT", 6333))
COLLECTION_NAME = os.environ.get("QDRANT_COLLECTION_NAME", "media_vectors")

# Docker container paths → local Windows paths
# /mnt/i-media  →  I:/i-media
MOUNT_MAP: list[tuple[str, str]] = [
    ("/mnt/i-media", "I:/i-media"),
    ("/mnt/j-media", "J:/j-media"),
]

# Number of images to prefetch from disk while GPU processes the current batch
IO_THREADS = 8


def _resolve_path(qdrant_path: str) -> str | None:
    """Translate a Docker mount path to the local Windows path."""
    for mnt, local in MOUNT_MAP:
        if qdrant_path.startswith(mnt):
            local_path = local + qdrant_path[len(mnt):]
            # Qdrant stores forward slashes; Path handles mixed separators on Windows
            return local_path
    return None


def _load_image(file_path: str) -> tuple[str, np.ndarray | None]:
    """Read one image from disk. Returns (file_path, array) or (file_path, None) on error."""
    try:
        arr = np.array(Image.open(file_path).convert("RGB"))
        return file_path, arr
    except Exception:
        return file_path, None


def main(dry_run: bool = False, batch_size: int = 32):
    t_start = time.perf_counter()

    client = QdrantClient(host=QDRANT_HOST, port=QDRANT_PORT)

    # Warm up model — prints device info
    model = get_yolo_model()
    print(f"YOLO model ready.\n")

    # Pass 1 — collect all construction asset IDs + file paths
    print("Pass 1: scanning Qdrant for construction assets …")
    assets: list[tuple[int | str, str]] = []  # (point_id, local_windows_path)
    scanned = 0
    offset = None
    while True:
        records, next_offset = client.scroll(
            collection_name=COLLECTION_NAME,
            with_vectors=False,
            with_payload=["file_path"],
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
            if local and Path(local).exists():
                assets.append((r.id, local))
        print(f"  scanned {scanned:,}  found {len(assets):,} …", end="\r")
        if next_offset is None:
            break
        offset = next_offset

    t_scan = time.perf_counter()
    print(f"\n{len(assets):,} construction assets to enrich  ({t_scan - t_start:.1f}s scan)\n")

    if not assets:
        print("Nothing to process.")
        return

    # Pass 2 — read images in parallel, run YOLO in batches, write payloads
    print(f"Pass 2: YOLO inference  (batch={batch_size}, io_threads={IO_THREADS}) …")

    updated = 0
    skipped = 0
    t_last_print = time.perf_counter()

    with ThreadPoolExecutor(max_workers=IO_THREADS) as pool:
        for batch_start in range(0, len(assets), batch_size):
            batch = assets[batch_start : batch_start + batch_size]

            # Load this batch from disk concurrently
            futures = {pool.submit(_load_image, fp): (pid, fp) for pid, fp in batch}
            loaded: list[tuple[int | str, np.ndarray]] = []
            for future in as_completed(futures):
                fp_result, arr = future.result()
                pid = futures[future][0]
                if arr is not None:
                    loaded.append((pid, arr))
                else:
                    skipped += 1

            if not loaded:
                continue

            ids  = [pid for pid, _ in loaded]
            arrs = [arr for _, arr in loaded]

            # Single batched GPU call
            results = model.predict(source=arrs, conf=0.25, verbose=False)

            if not dry_run:
                payloads_by_id: list[tuple[int | str, dict]] = []
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
                    payload = {
                        "yolo_labels":       sorted({d["label"] for d in detections}),
                        "yolo_detections":   detections,
                        "yolo_object_count": len(detections),
                        "yolo_model":        os.environ.get("YOLO_MODEL_NAME", "yolov8n"),
                    }
                    payloads_by_id.append((pid, payload))

                # Group points by identical label sets to minimize Qdrant calls
                from collections import defaultdict
                label_groups: dict[str, list] = defaultdict(list)
                per_point_payloads = {}
                for pid, payload in payloads_by_id:
                    label_key = ",".join(payload["yolo_labels"])
                    label_groups[label_key].append(pid)
                    per_point_payloads[pid] = payload

                for label_key, pids in label_groups.items():
                    labels = label_key.split(",") if label_key else []
                    client.set_payload(
                        collection_name=COLLECTION_NAME,
                        payload={"yolo_labels": labels},
                        points=pids,
                    )
                # Write per-point fields (detections + count) individually in one batch op
                from qdrant_client.models import SetPayloadOperation, SetPayload
                ops = [
                    SetPayloadOperation(
                        set_payload=SetPayload(
                            payload={
                                "yolo_detections":   per_point_payloads[pid]["yolo_detections"],
                                "yolo_object_count": per_point_payloads[pid]["yolo_object_count"],
                                "yolo_model":        per_point_payloads[pid]["yolo_model"],
                            },
                            points=[pid],
                        )
                    )
                    for pid in ids
                ]
                client.batch_update_points(collection_name=COLLECTION_NAME, update_operations=ops)

            updated += len(loaded)

            # Progress every 2 seconds
            now = time.perf_counter()
            if now - t_last_print >= 2.0:
                elapsed    = now - t_scan
                rate       = updated / elapsed
                remaining  = (len(assets) - updated) / rate if rate > 0 else 0
                pct        = updated / len(assets) * 100
                print(
                    f"  {updated:,}/{len(assets):,} ({pct:.0f}%)  "
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
    print(f"  Skipped : {skipped:,}  (missing file)")
    print(f"  Scan    : {t_scan - t_start:.1f}s")
    print(f"  Inference + write: {inference_time:.1f}s  ({rate:.0f} img/s)")
    print(f"  Total   : {total/60:.1f} min  ({total:.0f}s)")
    if dry_run:
        print("  (dry-run — no payloads written)")
    print(f"{'='*50}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="Run inference but don't write to Qdrant")
    parser.add_argument("--batch",   type=int, default=32, help="GPU batch size (default: 32)")
    args = parser.parse_args()
    main(dry_run=args.dry_run, batch_size=args.batch)
