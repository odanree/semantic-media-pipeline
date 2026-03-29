"""
patch_label.py — Patch a custom `label` payload field onto Qdrant points.

Two-phase workflow (fast path — apply from inside the container):

  Phase 1: Dump matching point IDs to JSON (runs on host, read-only scroll)
    python scripts/patch_label.py --stack lumen2 --path-filter d-4k-index --label UNC --dump label_unc.json

  Phase 2: Copy JSON into container and apply with the companion script
    docker cp scripts/apply_label_payload.py lumen2-worker:/tmp/apply_label_payload.py
    docker cp label_unc.json lumen2-worker:/tmp/label_unc.json
    docker exec lumen2-worker python /tmp/apply_label_payload.py /tmp/label_unc.json

  Direct apply (slower — every set_payload crosses Docker NAT from Windows):
    python scripts/patch_label.py --stack lumen2 --path-filter d-4k-index --label UNC
    python scripts/patch_label.py --stack lumen2 --path-filter d-4k-index --label UNC --dry-run
    python scripts/patch_label.py --stack lumen2 --path-filter d-4k-index --label UNC --overwrite

Requirements:
    pip install qdrant-client
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time

from qdrant_client import QdrantClient
from qdrant_client.models import Filter, FieldCondition, MatchText

SCROLL_TIMEOUT  = 120  # seconds — generous: large batches can be slow under write load
SCROLL_RETRIES  = 5   # retry scroll on timeout before giving up

# ─── Stack presets ────────────────────────────────────────────────────────────

STACK_PRESETS: dict[str, dict] = {
    "lumen":  {"host": "127.0.0.1", "port": 6333, "collection": "media_vectors"},
    "lumen2": {"host": "127.0.0.1", "port": 6340, "collection": "media_vectors2"},
}

# ─── Logging ──────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _scroll_matching_ids(
    client: QdrantClient,
    collection: str,
    path_filter: str,
    label: str,
    overwrite: bool,
    batch_size: int,
) -> list:
    scroll_filter = Filter(
        must=[FieldCondition(key="file_path", match=MatchText(text=path_filter))]
    )
    ids = []
    skipped = 0
    offset = None
    t_start = time.time()

    while True:
        records, next_offset = client.scroll(
            collection_name=collection,
            scroll_filter=scroll_filter,
            limit=batch_size,
            offset=offset,
            with_payload=True,
            with_vectors=False,
        )
        if not records:
            break

        for point in records:
            existing = (point.payload or {}).get("label")
            if existing == label and not overwrite:
                skipped += 1
                continue
            ids.append(point.id)

        total = len(ids) + skipped
        if total % 5000 == 0 and total > 0:
            elapsed = time.time() - t_start
            rate = total / elapsed if elapsed > 0 else 0
            log.info("  %d scanned | %d collected | %.1f pts/s", total, len(ids), rate)

        if next_offset is None:
            break
        offset = next_offset

    return ids


# ─── Main ─────────────────────────────────────────────────────────────────────

def run(stack: str, path_filter: str, label: str, dry_run: bool, overwrite: bool,
        dump: str | None = None, batch_size: int = 500):
    cfg = STACK_PRESETS[stack]
    collection = os.getenv("QDRANT_COLLECTION_NAME") or cfg["collection"]

    client = QdrantClient(host=cfg["host"], port=cfg["port"], prefer_grpc=False,
                           timeout=SCROLL_TIMEOUT)

    try:
        info = client.get_collection(collection)
    except Exception as exc:
        log.error("Cannot connect to Qdrant at %s:%d — %s", cfg["host"], cfg["port"], exc)
        sys.exit(1)

    log.info("Connected — %d total points in '%s'", info.points_count, collection)
    log.info("Filter : file_path contains '%s'", path_filter)
    log.info("Label  : label = '%s'", label)

    # ── Dump mode: collect IDs to JSON, no writes ──────────────────────────
    if dump:
        log.info("DUMP MODE — collecting matching point IDs into '%s'", dump)
        ids = _scroll_matching_ids(client, collection, path_filter, label, overwrite, batch_size)
        out = {"label": label, "collection": collection, "ids": ids}
        with open(dump, "w") as f:
            json.dump(out, f)
        log.info("─" * 60)
        log.info("Wrote %d IDs to %s", len(ids), dump)
        log.info("Next steps:")
        log.info("  docker cp scripts/apply_label_payload.py lumen2-worker:/tmp/apply_label_payload.py")
        log.info("  docker cp %s lumen2-worker:/tmp/%s", dump, os.path.basename(dump))
        log.info("  docker exec lumen2-worker python /tmp/apply_label_payload.py /tmp/%s", os.path.basename(dump))
        return

    # ── Direct apply mode ──────────────────────────────────────────────────
    if dry_run:
        log.info("DRY RUN — no writes will be made")

    scroll_filter = Filter(
        must=[FieldCondition(key="file_path", match=MatchText(text=path_filter))]
    )

    total = patched = skipped = 0
    offset = None
    t_start = time.time()

    while True:
        # Retry scroll with exponential backoff — Qdrant can be slow under write pressure
        for attempt in range(1, SCROLL_RETRIES + 1):
            try:
                records, next_offset = client.scroll(
                    collection_name=collection,
                    scroll_filter=scroll_filter,
                    limit=batch_size,
                    offset=offset,
                    with_payload=True,
                    with_vectors=False,
                )
                break
            except Exception as exc:
                if attempt == SCROLL_RETRIES:
                    log.error("Scroll failed after %d attempts: %s", SCROLL_RETRIES, exc)
                    raise
                wait = 2 ** attempt
                log.warning("Scroll timeout (attempt %d/%d), retrying in %ds — %s",
                            attempt, SCROLL_RETRIES, wait, exc)
                time.sleep(wait)

        if not records:
            break

        ids_to_patch = []
        for point in records:
            total += 1
            existing = (point.payload or {}).get("label")
            if existing == label and not overwrite:
                skipped += 1
                continue
            ids_to_patch.append(point.id)

        if ids_to_patch and not dry_run:
            client.set_payload(
                collection_name=collection,
                payload={"label": label},
                points=ids_to_patch,
                wait=True,  # backpressure — wait for write to land before next scroll
            )

        patched += len(ids_to_patch)

        if total % 5000 == 0 and total > 0:
            elapsed = time.time() - t_start
            rate = total / elapsed if elapsed > 0 else 0
            log.info("  %d scanned | %d patched | %d skipped | %.1f pts/s",
                     total, patched, skipped, rate)

        if next_offset is None:
            break
        offset = next_offset

    elapsed = time.time() - t_start
    log.info("─" * 60)
    log.info("Done in %.1fs", elapsed)
    log.info("  Scanned : %d", total)
    log.info("  Patched : %d%s", patched, " (dry run — no writes)" if dry_run else "")
    log.info("  Skipped : %d (already labelled)", skipped)


# ─── CLI ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Patch a custom label onto Qdrant points by file_path prefix.")
    parser.add_argument("--stack", choices=list(STACK_PRESETS), default="lumen2",
                        help="Which Qdrant instance to target (default: lumen2)")
    parser.add_argument("--path-filter", default="d-4k-index",
                        help="Substring to match against file_path (default: d-4k-index)")
    parser.add_argument("--label", default="UNC",
                        help="Value to set for the 'label' payload field (default: UNC)")
    parser.add_argument("--dump", metavar="FILE",
                        help="Dump matching point IDs to JSON instead of writing directly. "
                             "Use with apply_label_payload.py inside the container for best throughput.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Scan and count without writing anything (direct mode only)")
    parser.add_argument("--overwrite", action="store_true",
                        help="Include points that already have a 'label' value")
    parser.add_argument("--batch-size", type=int, default=500,
                        help="Points per scroll page (default: 500). Lower if Qdrant times out under write pressure.")
    args = parser.parse_args()

    run(
        stack=args.stack,
        path_filter=args.path_filter,
        label=args.label,
        dry_run=args.dry_run,
        overwrite=args.overwrite,
        dump=args.dump,
        batch_size=args.batch_size,
    )


if __name__ == "__main__":
    main()
