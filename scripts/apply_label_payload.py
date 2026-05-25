#!/usr/bin/env python3
"""
apply_label_payload.py — Apply a pre-dumped label payload to Qdrant points.

Run this INSIDE the Docker worker container so Qdrant is on the internal network
(~0.1ms/call vs ~2ms through Docker NAT from Windows).

Workflow:
  # 1. On Windows — dump matching IDs to JSON:
  python scripts/patch_label.py --stack lumen2 --path-filter d-4k-index --label UNC --dump label_unc.json

  # 2. Copy files into running worker container:
  docker cp scripts/apply_label_payload.py lumen2-worker:/tmp/apply_label_payload.py
  docker cp label_unc.json lumen2-worker:/tmp/label_unc.json

  # 3. Apply from inside the container:
  docker exec lumen2-worker python /tmp/apply_label_payload.py /tmp/label_unc.json

JSON format (produced by patch_label.py --dump):
  {"label": "UNC", "collection": "media_vectors2", "ids": [<uuid>, ...]}
"""

import json
import os
import sys
import time

from qdrant_client import QdrantClient

QDRANT_HOST     = os.environ.get("QDRANT_HOST", "lumen2-qdrant")
QDRANT_PORT     = int(os.environ.get("QDRANT_PORT", 6333))
BATCH_SIZE      = int(os.environ.get("APPLY_BATCH_SIZE", 2000))


def main(input_file: str) -> None:
    print(f"Loading {input_file} …")
    with open(input_file) as f:
        data: dict = json.load(f)

    label      = data["label"]
    collection = data.get("collection", os.environ.get("QDRANT_COLLECTION_NAME", "media_vectors2"))
    ids        = data["ids"]
    total      = len(ids)
    print(f"  {total:,} point IDs loaded")
    print(f"  label='{label}' → collection='{collection}'")
    print(f"  batch_size={BATCH_SIZE}\n")

    client = QdrantClient(host=QDRANT_HOST, port=QDRANT_PORT, prefer_grpc=False)

    # Verify connection
    try:
        info = client.get_collection(collection)
        print(f"Connected — {info.points_count:,} total points in '{collection}'\n")
    except Exception as exc:
        print(f"ERROR: Cannot connect to Qdrant at {QDRANT_HOST}:{QDRANT_PORT} — {exc}")
        sys.exit(1)

    done = 0
    t0 = time.perf_counter()

    for i in range(0, total, BATCH_SIZE):
        batch = ids[i : i + BATCH_SIZE]
        client.set_payload(
            collection_name=collection,
            payload={"label": label},
            points=batch,
            wait=False,
        )
        done += len(batch)
        elapsed = time.perf_counter() - t0
        rate = done / elapsed if elapsed > 0 else 0
        eta = (total - done) / rate if rate > 0 else 0
        print(f"  {done:,}/{total:,} patched  {rate:.0f} pts/s  ETA {eta:.1f}s", end="\r")

    # Final flush — one wait=True call to confirm all async writes landed
    if total > 0:
        client.set_payload(
            collection_name=collection,
            payload={"label": label},
            points=[ids[-1]],
            wait=True,
        )

    elapsed = time.perf_counter() - t0
    print(f"\n\nDone in {elapsed:.1f}s — {total:,} points labelled '{label}'")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: apply_label_payload.py <label_dump.json>")
        sys.exit(1)
    main(sys.argv[1])
