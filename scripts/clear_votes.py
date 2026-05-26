#!/usr/bin/env python3
import sys
if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')
"""
Clear all votes (user_vote payloads in Qdrant + VoteEvent table in PostgreSQL).

This script is used when deploying the vote observability system to ensure:
1. Qdrant user_vote payloads are cleared (fresh ranking signal)
2. VoteEvent table is truncated (historical records cleared)
3. New votes going forward have full search_query context

Supports both lumen1 (public) and lumen2 (private) projects.

Usage:
    python scripts/clear_votes.py --project=lumen1
    python scripts/clear_votes.py --project=lumen2 --confirm
    python scripts/clear_votes.py --qdrant-only
    python scripts/clear_votes.py --postgres-only
"""

import argparse
import os
import sys
from datetime import datetime

from dotenv import load_dotenv
load_dotenv()  # Load .env

# Load project-specific overrides if they exist (e.g., .env.lumen2.local for localhost)
project = None
if '--project=lumen1' in ' '.join(sys.argv):
    project = 'lumen1'
elif '--project=lumen2' in ' '.join(sys.argv):
    project = 'lumen2'

if project:
    local_env = f'.env.{project}.local'
    if os.path.exists(local_env):
        load_dotenv(local_env, override=True)

try:
    from qdrant_client import QdrantClient
    from qdrant_client.models import PointIdsList
except ImportError:
    print("ERROR: qdrant-client not installed. Run: pip install qdrant-client")
    sys.exit(1)

try:
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine
    import asyncio
except ImportError:
    print("ERROR: sqlalchemy not installed. Run: pip install sqlalchemy asyncpg")
    sys.exit(1)


# Configuration
PROJECTS = {
    "lumen1": {
        "qdrant_host": os.getenv("QDRANT_HOST", "qdrant"),
        "qdrant_port": int(os.getenv("QDRANT_PORT", "6333")),
        "qdrant_collection": os.getenv("QDRANT_COLLECTION_NAME", "media_vectors"),
        "postgres_url": os.getenv(
            "DATABASE_ASYNC_URL",
            "postgresql+asyncpg://lumen_user:secure_password_here@postgres:5432/lumen"
        ),
    },
    "lumen2": {
        "qdrant_host": os.getenv("QDRANT_HOST", "qdrant"),
        "qdrant_port": int(os.getenv("QDRANT_PORT", "6333")),
        "qdrant_collection": os.getenv("QDRANT_COLLECTION_NAME", "media_vectors"),
        "postgres_url": os.getenv(
            "DATABASE_ASYNC_URL",
            "postgresql+asyncpg://lumen_user:secure_password_here@postgres:5432/lumen"
        ),
    },
}


def clear_qdrant_votes(host: str, port: int, collection: str, dry_run: bool = False) -> dict:
    """
    Clear all user_vote payloads from Qdrant.

    Returns:
        dict with stats: {points_processed, payloads_deleted, errors}
    """
    print(f"\n{'='*80}")
    print("QDRANT: Clearing user_vote payloads")
    print(f"{'='*80}")
    print(f"Host: {host}:{port}")
    print(f"Collection: {collection}")
    print(f"Dry run: {dry_run}")

    try:
        client = QdrantClient(host=host, port=port)

        # Verify connection
        collections = client.get_collections()
        if not any(c.name == collection for c in collections.collections):
            print(f"❌ Collection '{collection}' not found")
            return {"error": f"Collection {collection} not found"}

        # Scroll all points and collect IDs with user_vote
        point_ids_to_clear = []
        offset = 0
        batch_size = 10000  # Increased for faster scanning

        print("\nScanning for points with user_vote payload...")

        while True:
            try:
                results, next_offset = client.scroll(
                    collection_name=collection,
                    limit=batch_size,
                    offset=offset,
                    with_payload=True,
                    with_vectors=False,
                )

                if not results:
                    break

                for point in results:
                    if point.payload and "user_vote" in point.payload:
                        point_ids_to_clear.append(point.id)

                if len(point_ids_to_clear) % 100 == 0:
                    print(f"  • Found {len(point_ids_to_clear)} points with user_vote...")

                if next_offset is None or next_offset == offset:
                    break
                offset = next_offset

            except Exception as e:
                print(f"⚠️  Scroll error: {e}")
                break

        print(f"\n✓ Found {len(point_ids_to_clear)} points with user_vote payload")

        if len(point_ids_to_clear) == 0:
            print("ℹ️  No votes to clear")
            return {"points_processed": 0, "payloads_deleted": 0}

        # Clear payloads
        if not dry_run:
            print(f"\nClearing payloads in batches of {batch_size}...")
            for i in range(0, len(point_ids_to_clear), batch_size):
                batch = point_ids_to_clear[i:i + batch_size]
                try:
                    client.delete_payload(
                        collection_name=collection,
                        keys=["user_vote"],
                        points=PointIdsList(points=batch),
                    )
                    print(f"  • Cleared {len(batch)} payloads ({i + len(batch)}/{len(point_ids_to_clear)})")
                except Exception as e:
                    print(f"❌ Error clearing batch: {e}")
                    return {"error": str(e)}

            print(f"\n✅ Successfully cleared {len(point_ids_to_clear)} user_vote payloads from Qdrant")
            return {
                "points_processed": len(point_ids_to_clear),
                "payloads_deleted": len(point_ids_to_clear),
            }
        else:
            print(f"\n[DRY RUN] Would clear {len(point_ids_to_clear)} payloads")
            return {
                "points_processed": len(point_ids_to_clear),
                "payloads_deleted": len(point_ids_to_clear),
                "dry_run": True,
            }

    except Exception as e:
        print(f"❌ Qdrant error: {e}")
        return {"error": str(e)}


async def clear_postgres_votes(postgres_url: str, dry_run: bool = False) -> dict:
    """
    Clear VoteEvent table from PostgreSQL.

    Returns:
        dict with stats: {rows_deleted, timestamp}
    """
    print(f"\n{'='*80}")
    print("POSTGRESQL: Clearing vote_events table")
    print(f"{'='*80}")
    print(f"Database: {postgres_url.split('/')[-1]}")
    print(f"Dry run: {dry_run}")

    try:
        engine = create_async_engine(postgres_url, echo=False)

        async with engine.begin() as conn:
            # Check if table exists
            result = await conn.execute(
                text("""
                    SELECT EXISTS (
                        SELECT 1 FROM information_schema.tables
                        WHERE table_name = 'vote_events'
                    )
                """)
            )
            table_exists = result.scalar()

            if not table_exists:
                print("⚠️  Table 'vote_events' does not exist")
                await engine.dispose()
                return {"error": "Table vote_events not found"}

            # Get count before
            result = await conn.execute(text("SELECT COUNT(*) FROM vote_events"))
            row_count = result.scalar()
            print(f"\nFound {row_count} rows in vote_events table")

            if row_count == 0:
                print("ℹ️  No votes to clear")
                await engine.dispose()
                return {"rows_deleted": 0}

            if not dry_run:
                # Truncate table
                print("\nTruncating vote_events table...")
                await conn.execute(text("TRUNCATE TABLE vote_events CASCADE"))
                print(f"✅ Successfully truncated vote_events table ({row_count} rows deleted)")

                await engine.dispose()
                return {
                    "rows_deleted": row_count,
                    "timestamp": datetime.utcnow().isoformat(),
                }
            else:
                print(f"\n[DRY RUN] Would truncate {row_count} rows")
                await engine.dispose()
                return {
                    "rows_deleted": row_count,
                    "dry_run": True,
                }

    except Exception as e:
        print(f"❌ PostgreSQL error: {e}")
        return {"error": str(e)}


def print_summary(qdrant_result: dict, postgres_result: dict):
    """Print summary of cleanup results."""
    print(f"\n{'='*80}")
    print("SUMMARY")
    print(f"{'='*80}")

    qdrant_ok = "error" not in qdrant_result
    postgres_ok = "error" not in postgres_result

    if qdrant_ok:
        print(
            f"✅ Qdrant: Cleared {qdrant_result.get('payloads_deleted', 0)} "
            f"user_vote payloads from {qdrant_result.get('points_processed', 0)} points"
        )
    else:
        print(f"❌ Qdrant: {qdrant_result.get('error', 'Unknown error')}")

    if postgres_ok:
        print(
            f"✅ PostgreSQL: Deleted {postgres_result.get('rows_deleted', 0)} "
            f"rows from vote_events table"
        )
    else:
        print(f"❌ PostgreSQL: {postgres_result.get('error', 'Unknown error')}")

    if qdrant_result.get("dry_run") or postgres_result.get("dry_run"):
        print("\n⚠️  DRY RUN: No actual changes were made")
        print("   Run without --dry-run to apply changes")

    print()

    return qdrant_ok and postgres_ok


async def main():
    parser = argparse.ArgumentParser(
        description="Clear all votes (Qdrant + PostgreSQL) for vote observability system",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Clear both Qdrant and PostgreSQL (lumen1)
  python scripts/clear_votes.py --project=lumen1

  # Dry run (no changes)
  python scripts/clear_votes.py --project=lumen1 --dry-run

  # Force without confirmation
  python scripts/clear_votes.py --project=lumen1 --confirm

  # Clear only Qdrant
  python scripts/clear_votes.py --project=lumen1 --qdrant-only

  # Clear only PostgreSQL
  python scripts/clear_votes.py --project=lumen1 --postgres-only
        """,
    )

    parser.add_argument(
        "--project",
        choices=["lumen1", "lumen2"],
        default="lumen1",
        help="Project to clear votes for (default: lumen1)",
    )
    parser.add_argument(
        "--qdrant-only",
        action="store_true",
        help="Only clear Qdrant votes (skip PostgreSQL)",
    )
    parser.add_argument(
        "--postgres-only",
        action="store_true",
        help="Only clear PostgreSQL votes (skip Qdrant)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be cleared without making changes",
    )
    parser.add_argument(
        "--confirm",
        action="store_true",
        help="Skip confirmation prompt (useful for scripts)",
    )

    args = parser.parse_args()

    # Validate project
    if args.project not in PROJECTS:
        print(f"❌ Unknown project: {args.project}")
        sys.exit(1)

    config = PROJECTS[args.project]

    # Print header
    print(f"\n{'='*80}")
    print("VOTE CLEANUP SCRIPT")
    print(f"{'='*80}")
    print(f"Project: {args.project}")
    print(f"Qdrant: {config['qdrant_host']}:{config['qdrant_port']}")
    print(f"Dry run: {args.dry_run}")
    print()

    # Confirmation
    if not args.confirm and not args.dry_run:
        print("⚠️  WARNING: This will clear all votes!")
        print("   - Qdrant: user_vote payloads will be deleted")
        print("   - PostgreSQL: vote_events table will be truncated")
        print()
        response = input("Are you sure? Type 'yes' to confirm: ").strip().lower()
        if response != "yes":
            print("Cancelled.")
            sys.exit(0)

    # Run cleanup
    qdrant_result = {}
    postgres_result = {}

    if not args.postgres_only:
        qdrant_result = clear_qdrant_votes(
            host=config["qdrant_host"],
            port=config["qdrant_port"],
            collection=config["qdrant_collection"],
            dry_run=args.dry_run,
        )

    if not args.qdrant_only:
        postgres_result = await clear_postgres_votes(
            postgres_url=config["postgres_url"],
            dry_run=args.dry_run,
        )

    # Summary
    success = print_summary(qdrant_result, postgres_result)

    sys.exit(0 if success else 1)


if __name__ == "__main__":
    asyncio.run(main())
