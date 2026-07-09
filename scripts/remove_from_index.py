#!/usr/bin/env python3
"""
Remove one or more files from the index (Qdrant vectors + PostgreSQL row).

Dry-run by default. Pass --confirm to actually delete.

Uses `docker exec` + `psql` for Postgres and the Qdrant HTTP API — no Python
package installs required beyond the standard library.

Usage:
    # Preview what an exact path would remove
    python scripts/remove_from_index.py --project=lumen1 \\
        --file-path "some-mount/example.mp4"

    # Preview a SQL LIKE pattern (whole folder)
    python scripts/remove_from_index.py --project=lumen1 --like "some-mount/%"

    # Execute
    python scripts/remove_from_index.py --project=lumen1 \\
        --file-path "some-mount/example.mp4" --confirm
"""

import argparse
import json
import subprocess
import sys
import urllib.request

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")

# Per-project connection defaults. Match docker-compose port mappings +
# init-db*.sql users/collections. Kept inline (not env-loaded) so the
# script has no config prerequisites beyond Docker being up.
PROJECTS = {
    "lumen1": {
        "pg_container": "lumen-postgres",
        "pg_user": "lumen_user",
        "pg_db": "lumen",
        "qdrant_url": "http://localhost:6333",
        "qdrant_collection": "media_vectors",
    },
}


def psql(cfg: dict, sql: str) -> str:
    """Run SQL via `docker exec … psql -tAc` and return stdout."""
    result = subprocess.run(
        [
            "docker", "exec", "-i", cfg["pg_container"],
            "psql", "-U", cfg["pg_user"], "-d", cfg["pg_db"],
            "-v", "ON_ERROR_STOP=1", "-tAc", sql,
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        print(f"ERROR: psql failed (exit {result.returncode})")
        print(result.stderr.strip())
        sys.exit(result.returncode)
    return result.stdout


def find_matches(cfg: dict, file_path: str | None, like: str | None) -> list[str]:
    # Parameterize via psql variables so single quotes in filenames don't break the SQL.
    if file_path:
        cmd_input = file_path
        sql = "SELECT file_path FROM media_files WHERE file_path = :'fp'"
        # Feed the value via -v — but -v with special chars needs a stdin arg.
        # Simpler: quote-escape inline.
        escaped = file_path.replace("'", "''")
        sql = f"SELECT file_path FROM media_files WHERE file_path = '{escaped}'"
    else:
        escaped = like.replace("'", "''")
        sql = f"SELECT file_path FROM media_files WHERE file_path LIKE '{escaped}'"
    out = psql(cfg, sql)
    return [line for line in out.splitlines() if line]


def delete_from_postgres(cfg: dict, paths: list[str]) -> int:
    # Build a VALUES list — one row per path, single quotes escaped.
    values = ", ".join(f"('{p.replace(chr(39), chr(39) * 2)}')" for p in paths)
    sql = (
        f"WITH t(fp) AS (VALUES {values}) "
        f"DELETE FROM media_files WHERE file_path IN (SELECT fp FROM t) "
        f"RETURNING file_path;"
    )
    out = psql(cfg, sql)
    return sum(1 for line in out.splitlines() if line)


def qdrant_post(url: str, body: dict) -> dict:
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode("utf-8"))


def delete_from_qdrant(cfg: dict, paths: list[str]) -> int:
    base = f"{cfg['qdrant_url']}/collections/{cfg['qdrant_collection']}"
    flt = {"must": [{"key": "file_path", "match": {"any": paths}}]}

    # Count first so the caller sees a real "points removed" number.
    count = qdrant_post(f"{base}/points/count", {"filter": flt, "exact": True})
    n = count.get("result", {}).get("count", 0)

    qdrant_post(f"{base}/points/delete?wait=true", {"filter": flt})
    return n


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--project", choices=list(PROJECTS.keys()), required=True)
    grp = p.add_mutually_exclusive_group(required=True)
    grp.add_argument("--file-path", help="Exact file_path to remove")
    grp.add_argument("--like", help="SQL LIKE pattern (e.g. 'some-mount/%%')")
    p.add_argument("--confirm", action="store_true", help="Actually delete (default: dry-run)")
    args = p.parse_args()

    cfg = PROJECTS[args.project]

    paths = find_matches(cfg, args.file_path, args.like)
    if not paths:
        print("No matching rows in media_files.")
        return 0

    print(f"Project: {args.project}")
    print(f"Qdrant:  {cfg['qdrant_url']}/collections/{cfg['qdrant_collection']}")
    print(f"Postgres: {cfg['pg_container']} (db={cfg['pg_db']})")
    print(f"Matched {len(paths)} row(s):")
    for pth in paths[:20]:
        print(f"  {pth}")
    if len(paths) > 20:
        print(f"  ... and {len(paths) - 20} more")

    if not args.confirm:
        print("\nDry-run — pass --confirm to delete from Qdrant + Postgres.")
        return 0

    print("\nDeleting from Qdrant...")
    qdrant_deleted = delete_from_qdrant(cfg, paths)
    print(f"  Qdrant points deleted: {qdrant_deleted}")

    print("Deleting from Postgres...")
    pg_deleted = delete_from_postgres(cfg, paths)
    print(f"  media_files rows deleted: {pg_deleted}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
