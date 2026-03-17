"""
Migrate stored file paths from absolute (/mnt/source/...) to mount-relative format.

Before: /mnt/source/c-index/Miyuki Arisu/file.mp4
After:  c-index/Miyuki Arisu/file.mp4

Run ONCE after deploying the updated worker + API that understand relative paths.
Safe to run while the stack is live — updates are idempotent.

Usage:
    # Dry-run (print counts, make no changes):
    python scripts/migrate_paths_relative.py --dry-run

    # Migrate Postgres only:
    python scripts/migrate_paths_relative.py --postgres-only

    # Migrate Qdrant only (specify collection):
    python scripts/migrate_paths_relative.py --qdrant-only --collection media_vectors2

    # Migrate both (default):
    python scripts/migrate_paths_relative.py

Environment (defaults match docker-compose):
    DATABASE_URL     — sync Postgres URL
    QDRANT_HOST      — default: localhost
    QDRANT_PORT      — default: 6333
    QDRANT_GRPC_PORT — default: 6334
    QDRANT_COLLECTION_NAME — default: media_vectors
"""

from __future__ import annotations

import argparse
import logging
import os

log = logging.getLogger(__name__)

_SOURCE_PREFIX = "/mnt/source/"


# ---------------------------------------------------------------------------
# Postgres migration
# ---------------------------------------------------------------------------

def migrate_postgres(database_url: str, dry_run: bool) -> int:
    """Strip /mnt/source/ from file_path in media_files. Returns rows updated."""
    import psycopg2

    conn = psycopg2.connect(database_url)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) FROM media_files WHERE file_path LIKE %s",
                (_SOURCE_PREFIX + "%",),
            )
            count = cur.fetchone()[0]
            log.info("Postgres: %d rows to migrate", count)

            if dry_run:
                log.info("Dry-run — no changes written")
                return count

            cur.execute(
                """
                UPDATE media_files
                SET file_path = SUBSTR(file_path, %s)
                WHERE file_path LIKE %s
                """,
                (len(_SOURCE_PREFIX) + 1, _SOURCE_PREFIX + "%"),
            )
            updated = cur.rowcount
            conn.commit()
            log.info("Postgres: updated %d rows", updated)
            return updated
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Qdrant migration
# ---------------------------------------------------------------------------

def migrate_qdrant(
    host: str,
    port: int,
    grpc_port: int,
    collection: str,
    dry_run: bool,
    batch_size: int = 500,
) -> int:
    """Strip /mnt/source/ from file_path in Qdrant payload. Returns points updated."""
    from qdrant_client import QdrantClient
    from qdrant_client.models import PointIdsList

    client = QdrantClient(host=host, port=port, grpc_port=grpc_port, prefer_grpc=True)

    total_updated = 0
    offset = None

    while True:
        results, next_offset = client.scroll(
            collection_name=collection,
            scroll_filter=None,
            limit=batch_size,
            offset=offset,
            with_payload=["file_path"],
            with_vectors=False,
        )

        if not results:
            break

        to_update: list[tuple[str, str]] = []  # (point_id, new_path)
        for point in results:
            fp = (point.payload or {}).get("file_path", "")
            if fp.startswith(_SOURCE_PREFIX):
                to_update.append((point.id, fp[len(_SOURCE_PREFIX):]))

        if to_update and not dry_run:
            # Set payload one batch at a time (each point gets its own path)
            for point_id, new_path in to_update:
                client.set_payload(
                    collection_name=collection,
                    payload={"file_path": new_path},
                    points=PointIdsList(points=[point_id]),
                )

        total_updated += len(to_update)
        log.info(
            "Qdrant batch: scanned %d, queued %d for update (total so far: %d)",
            len(results), len(to_update), total_updated,
        )

        offset = next_offset
        if offset is None:
            break

    if dry_run:
        log.info("Dry-run — Qdrant: %d points would be updated", total_updated)
    else:
        log.info("Qdrant: updated %d points", total_updated)

    return total_updated


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Migrate file paths to mount-relative format")
    p.add_argument("--dry-run", action="store_true", help="Count rows/points without writing")
    p.add_argument("--postgres-only", action="store_true")
    p.add_argument("--qdrant-only", action="store_true")
    p.add_argument("--collection", default=os.getenv("QDRANT_COLLECTION_NAME", "media_vectors"))
    p.add_argument("--batch-size", type=int, default=500)
    return p.parse_args()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    args = parse_args()

    if args.dry_run:
        log.info("DRY-RUN mode — no changes will be written")

    run_postgres = not args.qdrant_only
    run_qdrant = not args.postgres_only

    if run_postgres:
        db_url = os.getenv("DATABASE_URL")
        if not db_url:
            log.error("DATABASE_URL not set")
            raise SystemExit(1)
        migrate_postgres(db_url, dry_run=args.dry_run)

    if run_qdrant:
        host = os.getenv("QDRANT_HOST", "localhost")
        port = int(os.getenv("QDRANT_PORT", "6333"))
        grpc_port = int(os.getenv("QDRANT_GRPC_PORT", "6334"))
        migrate_qdrant(
            host=host,
            port=port,
            grpc_port=grpc_port,
            collection=args.collection,
            dry_run=args.dry_run,
            batch_size=args.batch_size,
        )


if __name__ == "__main__":
    main()
