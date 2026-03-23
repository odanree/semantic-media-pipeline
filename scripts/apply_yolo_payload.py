#!/usr/bin/env python3
"""
Apply pre-computed YOLO results from a JSON file to Qdrant.

Run this INSIDE the Docker container where Qdrant is on the internal network
(~0.1ms/call vs ~2ms through Docker NAT from Windows).

Usage:
    # On Windows — copy file into container:
    docker cp yolo_results.json lumen-worker:/tmp/yolo_results.json

    # Inside container:
    docker exec lumen-worker python /app/scripts/apply_yolo_payload.py /tmp/yolo_results.json
"""

import json
import os
import sys
import time
from collections import defaultdict

from qdrant_client import QdrantClient

QDRANT_HOST     = os.environ.get("QDRANT_HOST", "qdrant")
QDRANT_PORT     = int(os.environ.get("QDRANT_PORT", 6333))
COLLECTION_NAME = os.environ.get("QDRANT_COLLECTION_NAME", "media_vectors")


def main(input_file: str) -> None:
    print(f"Loading {input_file} …")
    with open(input_file) as f:
        records: list[dict] = json.load(f)
    print(f"  {len(records):,} records loaded\n")

    client = QdrantClient(host=QDRANT_HOST, port=QDRANT_PORT)

    # Group by label set and count for batch set_payload calls
    label_groups: defaultdict = defaultdict(list)
    count_groups: defaultdict = defaultdict(list)

    for r in records:
        label_key = ",".join(r["yolo_labels"])
        label_groups[(label_key, r["yolo_model"])].append(r["id"])
        count_groups[r["yolo_object_count"]].append(r["id"])

    total_calls = len(label_groups) + len(count_groups)
    print(f"Applying {len(label_groups)} label groups + {len(count_groups)} count groups "
          f"= {total_calls} Qdrant calls …\n")

    t0 = time.perf_counter()
    done = 0

    for (label_key, yolo_model), pids in label_groups.items():
        labels = label_key.split(",") if label_key else []
        client.set_payload(
            collection_name=COLLECTION_NAME,
            payload={"yolo_labels": labels, "yolo_model": yolo_model},
            points=pids,
            wait=False,
        )
        done += 1
        if done % 50 == 0 or done == total_calls:
            elapsed = time.perf_counter() - t0
            rate = done / elapsed
            eta = (total_calls - done) / rate if rate > 0 else 0
            print(f"  {done}/{total_calls} calls  {rate:.0f} calls/s  ETA {eta:.1f}s", end="\r")

    for cnt, pids in count_groups.items():
        client.set_payload(
            collection_name=COLLECTION_NAME,
            payload={"yolo_object_count": cnt},
            points=pids,
            wait=False,
        )
        done += 1
        if done % 50 == 0 or done == total_calls:
            elapsed = time.perf_counter() - t0
            rate = done / elapsed
            eta = (total_calls - done) / rate if rate > 0 else 0
            print(f"  {done}/{total_calls} calls  {rate:.0f} calls/s  ETA {eta:.1f}s", end="\r")

    elapsed = time.perf_counter() - t0
    print(f"\n\nDone. {len(records):,} frames enriched in {elapsed:.1f}s "
          f"({len(records)/elapsed:.0f} frames/s)")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: apply_yolo_payload.py <input.json>")
        sys.exit(1)
    main(sys.argv[1])
