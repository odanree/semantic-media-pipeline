#!/usr/bin/env python3
"""
Backfill voted_queries array field on all Qdrant points that have vote_label
but are missing the voted_queries field.

voted_queries = sorted(vote_label.keys()) — mirrors the vote_label dict keys
as a native Qdrant array field so injection can filter by query string natively
without hitting the old Python-side 2000-frame cap.

Usage:
    python scripts/backfill_voted_queries.py --project=lumen2 --dry-run
    python scripts/backfill_voted_queries.py --project=lumen2
"""

import argparse
import os
import sys
import time

from dotenv import load_dotenv
load_dotenv()

project = None
if '--project=lumen1' in ' '.join(sys.argv):
    project = 'lumen1'
elif '--project=lumen2' in ' '.join(sys.argv):
    project = 'lumen2'

if project:
    local_env = f'.env.{project}.local'
    if os.path.exists(local_env):
        load_dotenv(local_env, override=True)

from qdrant_client import QdrantClient
from qdrant_client.models import (
    FieldCondition, Filter, PointIdsList,
    SetPayload, SetPayloadOperation,
)

QDRANT_HOST = os.getenv("QDRANT_HOST", "localhost")
QDRANT_PORT = int(os.getenv("QDRANT_PORT", "6333"))
COLLECTION = os.getenv("QDRANT_COLLECTION_NAME", "media_vectors")

parser = argparse.ArgumentParser()
parser.add_argument("--project", choices=["lumen1", "lumen2"])
parser.add_argument("--dry-run", action="store_true")
args = parser.parse_args()

client = QdrantClient(host=QDRANT_HOST, port=QDRANT_PORT, timeout=120)
print(f"Connected to Qdrant at {QDRANT_HOST}:{QDRANT_PORT}, collection={COLLECTION}")

total_patched = 0
total_already_ok = 0
total_no_labels = 0
offset = None
batch_num = 0
WRITE_BATCH = 100  # flush batch_update_points every N ops

pending_ops = []


def flush(ops, dry_run, retries=3):
    if not ops:
        return
    if dry_run:
        print(f"  [dry-run] would batch_update {len(ops)} ops")
        return
    for attempt in range(retries):
        try:
            client.batch_update_points(
                collection_name=COLLECTION,
                update_operations=ops,
            )
            return
        except Exception as e:
            wait = 10 * (attempt + 1)
            print(f"  [flush] attempt {attempt+1} failed: {e.__class__.__name__} — retrying in {wait}s")
            time.sleep(wait)
    raise RuntimeError(f"flush failed after {retries} attempts")


while True:
    scroll_batch, next_offset = client.scroll(
        collection_name=COLLECTION,
        scroll_filter=Filter(must=[
            FieldCondition(key="user_vote", match={"value": 1}),
        ]),
        limit=500,
        offset=offset,
        with_payload=True,
        with_vectors=False,
    )

    if not scroll_batch:
        break

    batch_num += 1
    scroll_to_patch = 0

    for point in scroll_batch:
        payload = point.payload or {}
        vote_label = payload.get("vote_label") or {}
        if not vote_label:
            total_no_labels += 1
            continue

        expected = sorted(vote_label.keys())
        existing = payload.get("voted_queries")

        if existing == expected:
            total_already_ok += 1
            continue

        pending_ops.append(SetPayloadOperation(
            set_payload=SetPayload(
                payload={"voted_queries": expected},
                points=[point.id],
            )
        ))
        scroll_to_patch += 1
        total_patched += 1

        if len(pending_ops) >= WRITE_BATCH:
            flush(pending_ops, args.dry_run)
            pending_ops = []
            time.sleep(0.2)  # gentle throttle — give Qdrant optimizer breathing room

    print(f"[batch {batch_num}] scroll_size={len(scroll_batch)} patch={scroll_to_patch} "
          f"ok={total_already_ok} no_labels={total_no_labels} total_patched={total_patched}")

    offset = next_offset
    if next_offset is None:
        break

# Flush remaining
flush(pending_ops, args.dry_run)

print(f"\nDone. patched={total_patched}, already_ok={total_already_ok}, no_labels={total_no_labels}")
if args.dry_run:
    print("(dry-run -- no changes written)")
