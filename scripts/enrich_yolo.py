#!/usr/bin/env python3
"""
Batch-enrich all construction-phase frames in Qdrant with YOLO object detections.

Reads file_hash + frame_index from each Qdrant record, looks up the pre-extracted
JPEG from the frame cache (J:/frame_cache), runs YOLOv8 inference, and writes
yolo_labels / yolo_object_count back as payload fields.

Usage:
    python scripts/enrich_yolo.py [--dry-run] [--batch BATCH]
"""

import argparse
import hashlib
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

# Frame cache — same defaults as worker/tasks.py
FRAME_CACHE_DIR = Path(os.environ.get("FRAME_CACHE_DIR", "J:/frame_cache"))
KEYFRAME_FPS        = float(os.environ.get("KEYFRAME_FPS", "0.5"))
KEYFRAME_RESOLUTION = int(os.environ.get("KEYFRAME_RESOLUTION", "224"))

# Number of frames to prefetch from disk while GPU processes the current batch
IO_THREADS = 8


def _cache_dir(file_hash: str) -> Path:
    """Reproduce the same key as worker/tasks.py _frame_cache_key."""
    raw = f"{file_hash}:fps={KEYFRAME_FPS}:res={KEYFRAME_RESOLUTION}"
    key = hashlib.sha256(raw.encode()).hexdigest()[:16]
    return FRAME_CACHE_DIR / key


def _load_cached_frame(args: tuple) -> tuple:
    """
    Load a frame from the pre-extracted JPEG cache.
    args = (point_id, file_hash, frame_index)
    Returns (point_id, np.ndarray | None)
    """
    pid, file_hash, frame_index = args
    try:
        cache_dir = _cache_dir(file_hash)
        frames = sorted(cache_dir.glob("frame_*.jpg"))
        if not frames or frame_index >= len(frames):
            return pid, None
        arr = cv2.cvtColor(cv2.imread(str(frames[frame_index])), cv2.COLOR_BGR2RGB)
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
    print(f"Frame cache: {FRAME_CACHE_DIR}\n")

    # Pass 1 — collect construction asset IDs, file_hash, and frame_index
    print("Pass 1: scanning Qdrant for construction assets …")
    # (point_id, file_hash, frame_index)
    assets: list[tuple[int | str, str, int]] = []
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
            with_payload=["file_hash", "frame_index"],
            limit=1000,
            offset=offset,
        )
        if not records:
            break
        scanned += len(records)
        for r in records:
            fh  = (r.payload or {}).get("file_hash")
            idx = (r.payload or {}).get("frame_index")
            if fh is None or idx is None:
                continue
            # Verify cache entry exists before queuing
            if not (_cache_dir(fh) / ".done").exists():
                continue
            assets.append((r.id, fh, int(idx)))
        print(f"  found {len(assets):,} …", end="\r")
        if next_offset is None:
            break
        offset = next_offset

    t_scan = time.perf_counter()
    print(f"\n{len(assets):,} construction assets to enrich  ({t_scan - t_model_ready:.1f}s scan)\n")

    if not assets:
        print("Nothing to process.")
        return

    # Pass 2 — read JPEGs from cache in parallel, GPU inference, background Qdrant writer
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
                counts = item.pop("_counts")
                for label_key, pids in item.items():
                    labels = label_key.split(",") if label_key else []
                    client.set_payload(
                        collection_name=COLLECTION_NAME,
                        payload={"yolo_labels": labels, "yolo_model": yolo_model_name},
                        points=pids,
                    )
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

            futures = {pool.submit(_load_cached_frame, item): item[0] for item in batch}
            done, pending = futures_wait(futures, timeout=15)
            for future in pending:
                future.cancel()
                skipped += 1
            loaded: list[tuple[int | str, np.ndarray]] = []
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
    print(f"  Skipped : {skipped:,}  (no cache entry)")
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
