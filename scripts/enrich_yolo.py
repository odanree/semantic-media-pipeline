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
import queue
import sys
import threading
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, wait as futures_wait
from pathlib import Path

import cv2
import numpy as np
from qdrant_client import QdrantClient
from qdrant_client.models import Filter, FieldCondition, MatchText

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
            os.environ["OPENCV_FFMPEG_LOGLEVEL"] = "quiet"
            cap = cv2.VideoCapture(local_path)
            cap.set(cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, 8000)
            cap.set(cv2.CAP_PROP_READ_TIMEOUT_MSEC, 5000)
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

    t_model_start = time.perf_counter()
    model = get_yolo_model()
    t_model_ready = time.perf_counter()
    print(f"YOLO model ready.  ({t_model_ready - t_model_start:.1f}s)\n")

    # Pass 1 — collect construction asset IDs, local paths, and timestamps
    # file_path has a text index — MatchText filters server-side, no full scan needed
    print("Pass 1: scanning Qdrant for construction assets …")
    assets: list[tuple[int | str, str, float]] = []  # (point_id, local_path, timestamp_sec)
    scanned = 0
    construction_filter = Filter(must=[
        FieldCondition(key="file_path", match=MatchText(text="Construction")),
    ])
    offset = None
    while True:
        records, next_offset = client.scroll(
            collection_name=COLLECTION_NAME,
            scroll_filter=construction_filter,
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
            local = _resolve_path(fp)
            if not local or not Path(local).exists():
                continue
            timestamp = float((r.payload or {}).get("timestamp", 0.0))
            assets.append((r.id, local, timestamp))
        print(f"  found {len(assets):,} …", end="\r")
        if next_offset is None:
            break
        offset = next_offset

    t_scan = time.perf_counter()
    print(f"\n{len(assets):,} construction assets to enrich  ({t_scan - t_model_ready:.1f}s scan)\n")

    if not assets:
        print("Nothing to process.")
        return

    # Pass 2 — GPU inference + background Qdrant writer
    # Writer thread consumes a queue of (label_groups, count_groups) dicts
    # so the GPU never blocks on network I/O.
    print(f"Pass 2: YOLO inference  (batch={batch_size}, io_threads={IO_THREADS}) …")

    write_queue: queue.Queue = queue.Queue(maxsize=32)
    write_errors: list[str] = []

    def _writer():
        yolo_model_name = os.environ.get("YOLO_MODEL_NAME", "yolov8n")
        while True:
            item = write_queue.get()
            if item is None:
                write_queue.task_done()
                break
            try:
                # item = dict: label_key -> list of point ids
                #         plus "_counts": dict pid -> count
                counts = item.pop("_counts")
                for label_key, pids in item.items():
                    labels = label_key.split(",") if label_key else []
                    client.set_payload(
                        collection_name=COLLECTION_NAME,
                        payload={"yolo_labels": labels, "yolo_model": yolo_model_name},
                        points=pids,
                    )
                # Write counts grouped by value to minimise calls
                count_groups: dict[int, list] = defaultdict(list)
                for pid, cnt in counts.items():
                    count_groups[cnt].append(pid)
                for cnt, pids in count_groups.items():
                    client.set_payload(
                        collection_name=COLLECTION_NAME,
                        payload={"yolo_object_count": cnt},
                        points=pids,
                    )
            except Exception as e:
                write_errors.append(str(e))
            finally:
                write_queue.task_done()

    writer_thread = threading.Thread(target=_writer, daemon=True)
    writer_thread.start()

    updated = 0
    skipped = 0
    t_last_print = time.perf_counter()

    with ThreadPoolExecutor(max_workers=IO_THREADS) as pool:
        for batch_start in range(0, len(assets), batch_size):
            batch = assets[batch_start : batch_start + batch_size]

            futures = {pool.submit(_load_frame, item): item[0] for item in batch}
            loaded: list[tuple[int | str, np.ndarray]] = []
            done, pending = futures_wait(futures, timeout=15)
            for future in pending:
                future.cancel()
                skipped += 1
            for future in done:
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
                label_groups: dict[str, list] = defaultdict(list)
                counts: dict[int | str, int] = {}
                for pid, result in zip(ids, results):
                    boxes = result.boxes
                    labels: list[str] = []
                    if boxes is not None:
                        labels = sorted({result.names.get(int(b.cls[0]), str(int(b.cls[0]))) for b in boxes})
                    label_key = ",".join(labels)
                    label_groups[label_key].append(pid)
                    counts[pid] = len(boxes) if boxes is not None else 0

                item = dict(label_groups)
                item["_counts"] = counts
                write_queue.put(item)

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

    # Signal writer to finish and wait
    write_queue.put(None)
    write_queue.join()
    writer_thread.join()
    if write_errors:
        print(f"\n  ⚠  {len(write_errors)} write error(s): {write_errors[0]}")

    t_end = time.perf_counter()
    total = t_end - t_start
    inference_time = t_end - t_scan
    rate = updated / inference_time if inference_time > 0 else 0

    print(f"\n\n{'='*50}")
    print(f"  Done.")
    print(f"  Updated : {updated:,}")
    print(f"  Skipped : {skipped:,}  (unreadable frame)")
    print(f"  Model load : {t_model_ready - t_model_start:.1f}s")
    print(f"  Scan       : {t_scan - t_model_ready:.1f}s  ({scanned:,} records)")
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
