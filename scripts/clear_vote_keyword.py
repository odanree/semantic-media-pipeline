#!/usr/bin/env python3
"""
clear_vote_keyword.py — Remove all vote_label entries for a specific keyword/query.

Two-phase workflow (follows the Qdrant bulk-update pattern):

  Phase 1 — Scan on Windows, produce JSON:
    python scripts/clear_vote_keyword.py --keyword "bright lights at night" --project=lumen2

  Phase 2 — Copy + apply inside container (no Docker NAT on writes):
    docker cp updates.json lumen2-worker:/tmp/updates.json
    docker exec lumen2-worker python /app/scripts/clear_vote_keyword.py --apply /tmp/updates.json

  Or with --dry-run to preview without writing anything:
    python scripts/clear_vote_keyword.py --keyword "fireworks" --dry-run
"""

import argparse
import json
import os
import sys

from dotenv import load_dotenv
load_dotenv()

_args_str = ' '.join(sys.argv)
if '--project=lumen2' in _args_str:
    _project = 'lumen2'
elif '--project=lumen1' in _args_str:
    _project = 'lumen1'
else:
    _project = None

if _project:
    local_env = f'.env.{_project}.local'
    if os.path.exists(local_env):
        load_dotenv(local_env, override=True)

try:
    from qdrant_client import QdrantClient
    from qdrant_client.models import PointIdsList
except ImportError:
    print("ERROR: qdrant-client not installed. Run: pip install qdrant-client")
    sys.exit(1)


# ---------------------------------------------------------------------------
# Phase 1: scan Qdrant on Windows → write JSON
# ---------------------------------------------------------------------------

def scan(keyword: str, out_file: str, dry_run: bool):
    host       = os.getenv("QDRANT_HOST", "localhost")
    port       = int(os.getenv("QDRANT_PORT", "6333"))
    collection = os.getenv("QDRANT_COLLECTION_NAME", "media_vectors")

    print(f"Qdrant : {host}:{port}  collection={collection}")
    print(f"Keyword: {keyword!r}")

    client = QdrantClient(host=host, port=port)
    if not any(c.name == collection for c in client.get_collections().collections):
        print(f"ERROR: collection '{collection}' not found")
        sys.exit(1)

    # Scroll all points, collect those with this keyword in vote_label
    records: list[dict] = []
    offset = None

    print("Scanning", end="", flush=True)
    while True:
        batch, next_offset = client.scroll(
            collection_name=collection,
            limit=10_000,
            offset=offset,
            with_payload=["vote_label"],
            with_vectors=False,
        )
        for point in batch:
            if not point.payload:
                continue
            vl = point.payload.get("vote_label")
            if isinstance(vl, dict) and keyword in vl:
                remaining = {k: v for k, v in vl.items() if k != keyword}
                # remaining=None signals "delete vote_label + user_vote"
                records.append({"id": point.id, "vote_label": remaining or None})

        print(".", end="", flush=True)
        if next_offset is None:
            break
        offset = next_offset

    print(f"\nFound {len(records)} points with keyword {keyword!r}")

    if not records:
        print("Nothing to do.")
        return

    if dry_run:
        for r in records[:10]:
            after = r["vote_label"] or "(empty — user_vote cleared too)"
            print(f"  [{r['id']}] vote_label → {after}")
        if len(records) > 10:
            print(f"  ... and {len(records) - 10} more")
        print("\nDry run — no file written.")
        return

    with open(out_file, "w") as f:
        json.dump({"keyword": keyword, "records": records}, f)

    worker = "lumen2-worker" if _project == "lumen2" else "lumen1-worker"
    collection_env = "QDRANT_COLLECTION_NAME=media_vectors2" if _project == "lumen2" else ""

    print(f"\nWrote {len(records)} records to {out_file}")
    print("\nNext steps:")
    print(f"  docker cp {out_file} {worker}:/tmp/updates.json")
    print(f"  docker exec {worker} python /app/scripts/clear_vote_keyword.py --apply /tmp/updates.json")


# ---------------------------------------------------------------------------
# Phase 2: apply JSON inside container
# ---------------------------------------------------------------------------

def apply(in_file: str):
    host       = os.getenv("QDRANT_HOST", "qdrant")
    port       = int(os.getenv("QDRANT_PORT", "6333"))
    collection = os.getenv("QDRANT_COLLECTION_NAME", "media_vectors")

    with open(in_file) as f:
        data = json.load(f)

    keyword = data["keyword"]
    records = data["records"]

    print(f"Applying {len(records)} updates for keyword {keyword!r}")
    print(f"Qdrant : {host}:{port}  collection={collection}")

    client = QdrantClient(host=host, port=port)

    BATCH = 500
    cleared = 0
    also_cleared_vote = 0

    for i in range(0, len(records), BATCH):
        chunk = records[i:i + BATCH]

        # Group: points with no remaining labels (delete both fields)
        empty_ids = [r["id"] for r in chunk if r["vote_label"] is None]
        # Group points with the same remaining vote_label dict together
        update_groups: dict[str, list] = {}
        for r in chunk:
            if r["vote_label"] is None:
                continue
            key = json.dumps(r["vote_label"], sort_keys=True)
            update_groups.setdefault(key, {"payload": r["vote_label"], "ids": []})
            update_groups[key]["ids"].append(r["id"])

        for group in update_groups.values():
            client.set_payload(
                collection_name=collection,
                payload={"vote_label": group["payload"]},
                points=group["ids"],
                wait=False,
            )
            cleared += len(group["ids"])

        if empty_ids:
            client.delete_payload(
                collection_name=collection,
                keys=["vote_label", "user_vote"],
                points=PointIdsList(points=empty_ids),
                wait=False,
            )
            cleared += len(empty_ids)
            also_cleared_vote += len(empty_ids)

        print(f"  {min(i + BATCH, len(records))}/{len(records)}")

    print(f"\nDone. Cleared {cleared} vote_label[{keyword!r}] entries.")
    if also_cleared_vote:
        print(f"       Also cleared user_vote on {also_cleared_vote} points (no remaining labels).")


# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Clear vote_label entries for a keyword (two-phase bulk update)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--keyword", help="Keyword/query to clear (scan phase)")
    group.add_argument("--apply", metavar="FILE",
                       help="Apply a previously generated JSON file (run inside container)")

    parser.add_argument("--project", choices=["lumen1", "lumen2"],
                        help="Project — loads .env.<project>.local for localhost port overrides")
    parser.add_argument("--out", default="updates.json",
                        help="Output JSON file path (default: updates.json)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Preview matches without writing JSON or modifying Qdrant")

    args = parser.parse_args()

    if args.apply:
        apply(args.apply)
    else:
        scan(args.keyword, args.out, args.dry_run)


if __name__ == "__main__":
    main()
