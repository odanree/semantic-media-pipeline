#!/usr/bin/env python3
"""
Scan a directory for media files already indexed in the database (matched by hash).
Useful for identifying duplicates before organizing or deleting source files.

Also checks whether the original indexed path still exists on disk. If it doesn't,
--restore-missing moves the scanned copy to F:\\storage\\F Index\\restore and updates
both PostgreSQL and Qdrant to point to the new location.

Videos are hashed by first 8 KB only (same strategy as the ingest worker).
Images are hashed in full.

Usage:
    # Dry run — show which files are already indexed
    python scripts/find_indexed_files.py --dir "E:/Unsorted" --project=lumen2

    # Move duplicates (original still exists) to a _duplicates/ subfolder
    python scripts/find_indexed_files.py --dir "E:/Unsorted" --project=lumen2 --move

    # Restore copies whose original is missing → update DB + Qdrant
    python scripts/find_indexed_files.py --dir "E:/Unsorted" --project=lumen2 --restore-missing
"""

import argparse
import asyncio
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")

from dotenv import load_dotenv

load_dotenv()

# Load project-specific localhost overrides (e.g. .env.lumen2.local)
_project_arg = next((a for a in sys.argv if a.startswith("--project=")), None)
if _project_arg:
    _local_env = f".env.{_project_arg.split('=')[1]}.local"
    if os.path.exists(_local_env):
        load_dotenv(_local_env, override=True)

try:
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine
except ImportError:
    print("ERROR: sqlalchemy not installed. Run: pip install sqlalchemy asyncpg")
    sys.exit(1)

try:
    from qdrant_client import QdrantClient
    from qdrant_client.models import FieldCondition, Filter, MatchValue
except ImportError:
    print("ERROR: qdrant-client not installed. Run: pip install qdrant-client")
    sys.exit(1)

VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".avi", ".flv", ".wmv", ".webm", ".m4v"}
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".tiff", ".tif", ".webp", ".heic", ".heif"}
SUPPORTED_EXTS = VIDEO_EXTS | IMAGE_EXTS
CHUNK_SIZE = 8192

# Where restored files land on Windows and their container path equivalent
RESTORE_WIN_DIR = Path("F:/storage/F Index/restore")
RESTORE_CONTAINER_PREFIX = "/mnt/source/f-index/restore"

# Container mount → env var mapping (matches docker-compose.second.yml)
CONTAINER_MOUNTS = [
    ("/mnt/source/f-ltv",      "L2_MEDIA_1"),
    ("/mnt/source/e",          "L2_MEDIA_2"),
    ("/mnt/source/d-4k-index", "L2_MEDIA_3"),
    ("/mnt/source/c-index",    "L2_MEDIA_4"),
    ("/mnt/source/f-index",    "L2_MEDIA_5"),
]


def build_path_map() -> list[tuple[str, Path]]:
    result = []
    for mount, env_var in CONTAINER_MOUNTS:
        win = os.getenv(env_var, "")
        if win:
            result.append((mount, Path(win)))
    return result


def container_to_windows(container_path: str, path_map: list[tuple[str, Path]]) -> Path | None:
    for mount, win_base in path_map:
        # Absolute container path: /mnt/source/e/storage/file.mp4
        if container_path.startswith(mount + "/") or container_path == mount:
            rel = container_path[len(mount):].lstrip("/")
            return win_base / rel
        # Relative path stored by _to_relative_path: e/storage/file.mp4
        mount_name = mount.rsplit("/", 1)[-1]  # "e", "f-ltv", etc.
        if container_path.startswith(mount_name + "/"):
            rel = container_path[len(mount_name) + 1:]
            return win_base / rel
    return None


def fmt_size(n: int | float | None) -> str:
    if n is None:
        return "?"
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def compute_hash(file_path: Path) -> str:
    sha256 = hashlib.sha256()
    hash_full = file_path.suffix.lower() not in VIDEO_EXTS
    bytes_read = 0
    with open(file_path, "rb") as f:
        while True:
            if not hash_full and bytes_read >= CHUNK_SIZE:
                break
            chunk = f.read(CHUNK_SIZE)
            if not chunk:
                break
            sha256.update(chunk)
            bytes_read += len(chunk)
    return sha256.hexdigest()


def res_label(width: int | None, height: int | None) -> str:
    h = height or 0
    if h >= 2160:
        return "4K"
    if h >= 1080:
        return "1080p"
    if h >= 720:
        return "720p"
    if h >= 480:
        return "480p"
    if h > 0:
        return f"{h}p"
    return "unknown"


def probe_resolution(path: Path) -> tuple[int | None, int | None]:
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=width,height", "-of", "json", str(path)],
            capture_output=True, text=True, timeout=10,
        )
        streams = json.loads(out.stdout).get("streams", [])
        if streams:
            return streams[0].get("width"), streams[0].get("height")
    except Exception:
        pass
    return None, None


async def lookup_hashes(db_url: str, hashes: list[str]) -> dict[str, tuple[str, int | None, str]]:
    """Return {hash: (file_path, file_size_bytes, res_label)} for hashes that exist in media_files."""
    engine = create_async_engine(db_url, echo=False)
    try:
        async with engine.begin() as conn:
            result = await conn.execute(
                text(
                    "SELECT file_hash, file_path, file_size_bytes, width, height "
                    "FROM media_files WHERE file_hash = ANY(:hashes)"
                ),
                {"hashes": hashes},
            )
            return {
                row[0]: (
                    row[1],
                    int(row[2]) if row[2] is not None else None,
                    res_label(
                        int(row[3]) if row[3] else None,
                        int(row[4]) if row[4] else None,
                    ),
                )
                for row in result
            }
    finally:
        await engine.dispose()


async def update_db_path(db_url: str, file_hash: str, new_path: str) -> None:
    engine = create_async_engine(db_url, echo=False)
    try:
        async with engine.begin() as conn:
            await conn.execute(
                text("UPDATE media_files SET file_path = :new_path WHERE file_hash = :hash"),
                {"new_path": new_path, "hash": file_hash},
            )
    finally:
        await engine.dispose()


def update_qdrant_path(host: str, port: int, collection: str, old_path: str, new_path: str) -> None:
    client = QdrantClient(host=host, port=port)
    client.set_payload(
        collection_name=collection,
        payload={"file_path": new_path},
        points=Filter(must=[FieldCondition(key="file_path", match=MatchValue(value=old_path))]),
    )


def scan_files(directory: Path) -> list[Path]:
    return sorted(
        p for p in directory.rglob("*")
        if p.is_file() and p.suffix.lower() in SUPPORTED_EXTS
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Find media files already indexed in the database (by content hash)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--dir", required=True, help="Directory to scan")
    parser.add_argument("--project", choices=["lumen1", "lumen2"], default="lumen1")
    parser.add_argument(
        "--dest",
        default="_duplicates",
        help="Subfolder name for --move (default: _duplicates)",
    )
    parser.add_argument(
        "--move",
        action="store_true",
        help="Move duplicates whose original still exists into --dest subfolder",
    )
    parser.add_argument(
        "--restore-missing",
        action="store_true",
        help=(
            f"Move copies whose original is missing to {RESTORE_WIN_DIR} "
            "and update DB + Qdrant to point to the new location"
        ),
    )
    args = parser.parse_args()

    scan_dir = Path(args.dir)
    if not scan_dir.is_dir():
        print(f"ERROR: {scan_dir} is not a directory")
        sys.exit(1)

    db_url = os.getenv("DATABASE_ASYNC_URL")
    if not db_url:
        print("ERROR: DATABASE_ASYNC_URL not set. Check .env or .env.<project>.local")
        sys.exit(1)

    qdrant_host = os.getenv("QDRANT_HOST", "localhost")
    qdrant_port = int(os.getenv("QDRANT_PORT", "6333"))
    qdrant_collection = os.getenv("QDRANT_COLLECTION_NAME", "media_vectors")

    path_map = build_path_map()

    print(f"\nDirectory: {scan_dir}")
    print(f"Project:   {args.project}")
    print(f"Database:  {db_url.split('@')[-1]}")
    print(f"Qdrant:    {qdrant_host}:{qdrant_port}  collection={qdrant_collection}")
    mode = []
    if args.move:
        mode.append(f"MOVE duplicates → {args.dest}/")
    if args.restore_missing:
        mode.append(f"RESTORE missing → {RESTORE_WIN_DIR}")
    print(f"Mode:      {', '.join(mode) if mode else 'DRY RUN (no changes)'}")
    print()

    files = scan_files(scan_dir)
    print(f"Found {len(files)} media files")
    if not files:
        print("Nothing to do.")
        return

    print("Hashing files...")
    hash_to_path: dict[str, Path] = {}
    errors: list[tuple[Path, str]] = []
    for i, f in enumerate(files, 1):
        try:
            h = compute_hash(f)
            hash_to_path.setdefault(h, f)
        except Exception as e:
            errors.append((f, str(e)))
        if i % 50 == 0 or i == len(files):
            print(f"  {i}/{len(files)}", end="\r")
    print()

    if errors:
        print(f"\n⚠  {len(errors)} hash error(s):")
        for p, err in errors:
            print(f"  {p}: {err}")

    print(f"\nQuerying database for {len(hash_to_path)} unique hash(es)...")
    hash_to_db = asyncio.run(lookup_hashes(db_url, list(hash_to_path.keys())))

    # Classify: original present vs missing
    duplicates_present: list[tuple[Path, str, str, int | None, str]] = []   # original exists
    duplicates_missing: list[tuple[Path, str, str, int | None, str]] = []   # original gone

    skipped_self = 0
    for h, (db_path, db_size, res) in hash_to_db.items():
        src = hash_to_path[h]
        win_original = container_to_windows(db_path, path_map)
        # Skip if the DB entry resolves to the same file being scanned
        if win_original is not None and win_original.resolve() == src.resolve():
            skipped_self += 1
            continue
        original_exists = win_original is not None and win_original.exists()
        entry = (src, h, db_path, db_size, res)
        if original_exists:
            duplicates_present.append(entry)
        else:
            duplicates_missing.append(entry)

    new_count = len(hash_to_path) - len(hash_to_db)

    print(f"\n{'=' * 70}")
    print(f"Already indexed — original present: {len(duplicates_present)}")
    print(f"Already indexed — original MISSING: {len(duplicates_missing)}")
    if skipped_self:
        print(f"Skipped (file IS the indexed original): {skipped_self}")
    print(f"Not in database (new files):        {new_count}")
    print(f"{'=' * 70}")

    res_order = ["4K", "1080p", "720p", "480p"]

    # --- Files whose original still exists (safe to move to _duplicates) ---
    if duplicates_present:
        # Group by resolution tier
        by_res: dict[str, list] = {}
        for entry in duplicates_present:
            by_res.setdefault(entry[4], []).append(entry)
        tiers = [r for r in res_order if r in by_res] + sorted(r for r in by_res if r not in res_order)

        print(f"\nDuplicates (original still on disk):")
        for tier in tiers:
            print(f"\n  [{tier}]  ({len(by_res[tier])} file(s))")
            for path, h, db_path, db_size, res in sorted(by_res[tier], key=lambda x: str(x[0])):
                src_size = path.stat().st_size
                print(f"    {path}  ({fmt_size(src_size)})")
                print(f"      → indexed as: {db_path}  ({fmt_size(db_size)})  [{h[:16]}…]")

        if args.move:
            dest_dir = scan_dir / args.dest
            dest_dir.mkdir(exist_ok=True)
            moved = 0
            move_errors: list[tuple[Path, str]] = []
            for path, _, _db_path, _db_size, _res in duplicates_present:
                try:
                    rel = path.relative_to(scan_dir)
                    target = dest_dir / rel
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(path), str(target))
                    moved += 1
                except Exception as e:
                    move_errors.append((path, str(e)))
            if move_errors:
                print(f"\n⚠  {len(move_errors)} move error(s):")
                for p, err in move_errors:
                    print(f"  {p}: {err}")
            print(f"\n✅ Moved {moved} duplicate(s) to {dest_dir}")
        else:
            print(
                f"\n[DRY RUN] Run with --move to move {len(duplicates_present)} "
                f"file(s) to {scan_dir / args.dest}"
            )

    # --- Files whose original is missing (restore candidates) ---
    if duplicates_missing:
        by_res_m: dict[str, list] = {}
        for entry in duplicates_missing:
            by_res_m.setdefault(entry[4], []).append(entry)
        tiers_m = [r for r in res_order if r in by_res_m] + sorted(r for r in by_res_m if r not in res_order)

        print(f"\nDuplicates — original path is MISSING:")
        for tier in tiers_m:
            print(f"\n  [{tier}]  ({len(by_res_m[tier])} file(s))")
            for path, h, db_path, db_size, res in sorted(by_res_m[tier], key=lambda x: str(x[0])):
                src_size = path.stat().st_size
                win_original = container_to_windows(db_path, path_map)
                print(f"    {path}  ({fmt_size(src_size)})")
                print(f"      → indexed as: {db_path}  ({fmt_size(db_size)})  [{h[:16]}…]")
                print(f"      → resolved:   {win_original}  ← NOT FOUND")

        if args.restore_missing:
            RESTORE_WIN_DIR.mkdir(parents=True, exist_ok=True)
            restored = 0
            restore_errors: list[tuple[Path, str]] = []
            for path, h, old_container_path, _db_size, _res in duplicates_missing:
                try:
                    target = RESTORE_WIN_DIR / path.name
                    # Avoid clobbering if name collision
                    if target.exists():
                        stem, suffix = path.stem, path.suffix
                        target = RESTORE_WIN_DIR / f"{stem}_{h[:8]}{suffix}"

                    shutil.move(str(path), str(target))
                    new_container_path = f"{RESTORE_CONTAINER_PREFIX}/{target.name}"

                    asyncio.run(update_db_path(db_url, h, new_container_path))
                    update_qdrant_path(
                        qdrant_host, qdrant_port, qdrant_collection,
                        old_container_path, new_container_path,
                    )

                    print(f"\n  ✅ Restored: {path.name}")
                    print(f"       file:    {target}")
                    print(f"       DB+Qdrant path: {new_container_path}")
                    restored += 1
                except Exception as e:
                    restore_errors.append((path, str(e)))

            if restore_errors:
                print(f"\n⚠  {len(restore_errors)} restore error(s):")
                for p, err in restore_errors:
                    print(f"  {p}: {err}")
            print(f"\n✅ Restored {restored} file(s) to {RESTORE_WIN_DIR}")
        else:
            print(
                f"\n[DRY RUN] Run with --restore-missing to move {len(duplicates_missing)} "
                f"file(s) to {RESTORE_WIN_DIR} and update DB + Qdrant"
            )

    if not duplicates_present and not duplicates_missing:
        print("\n✓ No duplicates found — all files are new or self-referential.")

    # Resolution breakdown of all scanned files
    print(f"\nResolution breakdown ({len(files)} files):")
    res_counts: dict[str, int] = {}
    for f in files:
        w, h = probe_resolution(f)
        label = res_label(w, h)
        res_counts[label] = res_counts.get(label, 0) + 1
    tier_order = ["4K", "1080p", "720p", "480p"]
    for tier in tier_order:
        if tier in res_counts:
            print(f"  {tier:<8} {res_counts[tier]:>4}")
    for tier in sorted(r for r in res_counts if r not in tier_order):
        print(f"  {tier:<8} {res_counts[tier]:>4}")


if __name__ == "__main__":
    main()
