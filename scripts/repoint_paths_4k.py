"""
Repoint indexed media to 4K replacements WITHOUT re-embedding.

The CLIP vectors are effectively resolution-independent, so when a higher-res
copy of an already-indexed file exists we only need to rewrite the stored
file_path (Qdrant payload + Postgres media_files + vote_events) — no re-crawl,
no frame extraction, no embedding.

Matching is by PRODUCT CODE, not filename, because the 4K files are renamed
and any trailing title text may differ or be reordered:

    indexed:  <SRC_PREFIX>/ABC-123 Some Title.mp4
    4K file:  <FOURK_DIR>/abc123.4K Some Title.mp4   ->  new path:
    new:      <DST_PREFIX>/abc123.4K Some Title.mp4

Code extraction: leading letters + first number group, hi-res markers
(".4K"/".HD") stripped, case/separators normalised.  "ABC-123" and
"abc123.4K" both reduce to "ABC123".

Categories (printed in the dry-run preview):
  UPGRADE     clean code match, indexed file is not already hi-res        -> repointed
  REVIEW      matched, but indexed name has a variant suffix (-ub/-u/A/B) -> only with --include-variants
  CONFLICT    >1 indexed file maps to the same 4K code                    -> never auto-applied
  ALREADY     indexed file is already .4K/.HD                             -> skipped
  NO-MATCH    no 4K file with that code                                   -> left untouched

Default is DRY-RUN (writes nothing).  Add --apply to write.

Usage:
    # Preview (no writes):
    python scripts/repoint_paths_4k.py

    # Apply clean upgrades:
    python scripts/repoint_paths_4k.py --apply

    # Also repoint the -ub/-u variant cuts, and refresh file_hash:
    python scripts/repoint_paths_4k.py --apply --include-variants --rehash

Paths are supplied via args or env vars (no paths are hardcoded):
    FOURK_DIR    host directory holding the 4K replacement files (required)
    SRC_PREFIX   stored-path prefix of the indexed files to upgrade (required)
    DST_PREFIX   stored-path prefix to write (defaults to /mnt/source/am-4k/)
DB / Qdrant connection defaults target a local stack; override via env.
"""

from __future__ import annotations

import argparse
import hashlib
import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path, PureWindowsPath

log = logging.getLogger("repoint_4k")

VIDEO_EXTS = {".mp4", ".mkv", ".m4v", ".avi", ".wmv", ".mov", ".flv", ".webm"}
HIRES_MARKER = re.compile(r"\.?(4k|hd)\b", re.IGNORECASE)
CODE_RE = re.compile(r"^([A-Za-z]+)[-_ ]?0*(\d+)(.*)$")


# ---------------------------------------------------------------------------
# Code parsing
# ---------------------------------------------------------------------------

@dataclass
class ParsedName:
    code: str          # normalised join key, e.g. "ABC123"
    is_hires: bool     # name carried a .4K/.HD marker
    has_variant: bool  # trailing tokens after the number (e.g. "-ub", "A", "-1-u")


def parse_name(stem: str) -> ParsedName | None:
    """Parse a filename stem (no extension) into a join key + flags."""
    token = stem.split(" ", 1)[0]                 # leading code token
    is_hires = bool(HIRES_MARKER.search(token)) or ".4k" in stem.lower() or ".hd" in stem.lower()
    token = HIRES_MARKER.sub("", token)           # drop hi-res marker before parsing
    m = CODE_RE.match(token)
    if not m:
        return None
    letters, digits, rest = m.group(1), m.group(2), m.group(3)
    rest = rest.strip(" -_.")
    return ParsedName(
        code=f"{letters.upper()}{digits}",
        is_hires=is_hires,
        has_variant=bool(rest),
    )


# ---------------------------------------------------------------------------
# 4K folder index
# ---------------------------------------------------------------------------

def build_fourk_index(fourk_dir: str) -> tuple[dict[str, str], list[str]]:
    """Map code -> 4K filename. Returns (index, duplicate_codes)."""
    index: dict[str, str] = {}
    dupes: list[str] = []
    root = Path(fourk_dir)
    if not root.is_dir():
        raise SystemExit(f"4K directory not found: {fourk_dir}")
    for p in sorted(root.rglob("*")):
        if not p.is_file() or p.suffix.lower() not in VIDEO_EXTS:
            continue
        parsed = parse_name(p.stem)
        if not parsed:
            log.warning("4K file with unparseable name, skipped: %s", p.name)
            continue
        if parsed.code in index and index[parsed.code] != p.name:
            dupes.append(f"{parsed.code}: {index[parsed.code]} | {p.name}")
            continue
        index[parsed.code] = p.name
    return index, dupes


# ---------------------------------------------------------------------------
# Planning
# ---------------------------------------------------------------------------

@dataclass
class Plan:
    upgrade: list[tuple[str, str]] = field(default_factory=list)   # (old_path, new_path)
    review: list[tuple[str, str]] = field(default_factory=list)
    conflict: list[str] = field(default_factory=list)
    already: list[str] = field(default_factory=list)
    no_match: list[str] = field(default_factory=list)


def build_plan(indexed_paths: list[str], fourk: dict[str, str], src_prefix: str,
               dst_prefix: str) -> Plan:
    plan = Plan()
    # Detect codes claimed by >1 indexed file
    by_code: dict[str, list[str]] = {}
    for fp in indexed_paths:
        parsed = parse_name(PureWindowsPath(fp).stem)
        if parsed:
            by_code.setdefault(parsed.code, []).append(fp)

    for fp in indexed_paths:
        stem = PureWindowsPath(fp).stem
        parsed = parse_name(stem)
        if not parsed or parsed.code not in fourk:
            plan.no_match.append(fp)
            continue
        if len(by_code.get(parsed.code, [])) > 1:
            plan.conflict.append(fp)
            continue
        if parsed.is_hires:
            plan.already.append(fp)
            continue
        new_path = dst_prefix + fourk[parsed.code]
        if parsed.has_variant:
            plan.review.append((fp, new_path))
        else:
            plan.upgrade.append((fp, new_path))
    return plan


# ---------------------------------------------------------------------------
# DB / Qdrant writes
# ---------------------------------------------------------------------------

def fetch_indexed_paths(db_url: str, src_prefix: str) -> list[str]:
    import psycopg2
    conn = psycopg2.connect(db_url)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT file_path FROM media_files WHERE file_path LIKE %s ORDER BY file_path",
                (src_prefix + "%",),
            )
            rows = [r[0] for r in cur.fetchall()]
    finally:
        conn.close()
    return [p for p in rows if PureWindowsPath(p).suffix.lower() in VIDEO_EXTS]


def video_hash_first8k(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        h.update(f.read(8192))
    return h.hexdigest()


def apply_changes(pairs: list[tuple[str, str]], db_url: str, collection: str,
                  qdrant_host: str, qdrant_port: int, qdrant_grpc: int,
                  fourk_dir: str, dst_prefix: str, rehash: bool) -> None:
    import psycopg2
    from qdrant_client import QdrantClient
    from qdrant_client.models import Filter, FieldCondition, MatchValue

    # Generous timeout: filtered set_payload can be slow while Qdrant optimizes.
    qc = QdrantClient(host=qdrant_host, port=qdrant_port, grpc_port=qdrant_grpc,
                      prefer_grpc=True, timeout=300)
    conn = psycopg2.connect(db_url)
    conn.autocommit = False
    try:
        for old_path, new_path in pairs:
            # 1) Qdrant: every point (scene/frame) whose payload path == old.
            #    Idempotent — if already repointed, the filter matches nothing.
            qc.set_payload(
                collection_name=collection,
                payload={"file_path": new_path},
                points=Filter(must=[FieldCondition(key="file_path",
                                                   match=MatchValue(value=old_path))]),
                wait=True,
            )
            # 2) Postgres media_files (+ optional re-hash of the 4K file)
            new_hash = None
            if rehash:
                win = dst_prefix_to_host(new_path, dst_prefix, fourk_dir)
                new_hash = video_hash_first8k(win)
            with conn.cursor() as cur:
                if new_hash:
                    cur.execute(
                        "UPDATE media_files SET file_path=%s, file_hash=%s WHERE file_path=%s",
                        (new_path, new_hash, old_path),
                    )
                else:
                    cur.execute(
                        "UPDATE media_files SET file_path=%s WHERE file_path=%s",
                        (new_path, old_path),
                    )
                # 3) vote_events lineage
                cur.execute(
                    "UPDATE vote_events SET file_path=%s WHERE file_path=%s",
                    (new_path, old_path),
                )
            conn.commit()  # commit per file so progress survives a later failure
            log.info("repointed: %s -> %s", old_path, new_path)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def dst_prefix_to_host(new_path: str, dst_prefix: str, fourk_dir: str) -> str:
    """Translate <DST_PREFIX>/<name> back to the host <FOURK_DIR>/<name>."""
    rel = new_path[len(dst_prefix):]
    return str(Path(fourk_dir) / rel)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _default_db_url() -> str | None:
    """Use DATABASE_URL if set; else build from a password env var. No secrets hardcoded."""
    url = os.getenv("DATABASE_URL")
    if url:
        return url
    pw = os.getenv("DATABASE_PASSWORD_2") or os.getenv("DATABASE_PASSWORD")
    return f"postgresql://lumen2_user:{pw}@localhost:5433/lumen2" if pw else None


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--apply", action="store_true", help="Write changes (default: dry-run)")
    p.add_argument("--include-variants", action="store_true",
                   help="Also repoint REVIEW items (-ub/-u/part variants)")
    p.add_argument("--rehash", action="store_true",
                   help="Recompute file_hash from the 4K file (video = first 8KB)")
    p.add_argument("--fourk-dir", default=os.getenv("FOURK_DIR"),
                   help="Host dir holding the 4K replacement files (or set FOURK_DIR)")
    p.add_argument("--src-prefix", default=os.getenv("SRC_PREFIX"),
                   help="Stored-path prefix of indexed files to upgrade (or set SRC_PREFIX)")
    p.add_argument("--dst-prefix", default=os.getenv("DST_PREFIX", "/mnt/source/am-4k/"),
                   help="Stored-path prefix to write (or set DST_PREFIX)")
    p.add_argument("--collection", default=os.getenv("QDRANT_COLLECTION_NAME", "media_vectors2"))
    p.add_argument("--db-url", default=_default_db_url(),
                   help="Postgres URL (or set DATABASE_URL; or DATABASE_PASSWORD_2 for the default host)")
    p.add_argument("--qdrant-host", default=os.getenv("QDRANT_HOST", "localhost"))
    p.add_argument("--qdrant-port", type=int, default=int(os.getenv("QDRANT_PORT", "6340")))
    p.add_argument("--qdrant-grpc", type=int, default=int(os.getenv("QDRANT_GRPC_PORT", "6341")))
    return p.parse_args()


def _print_section(title: str, rows: list) -> None:
    print(f"\n=== {title} ({len(rows)}) ===")
    for r in rows:
        if isinstance(r, tuple):
            print(f"  {PureWindowsPath(r[0]).name}")
            print(f"      -> {PureWindowsPath(r[1]).name}")
        else:
            print(f"  {PureWindowsPath(r).name}")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    args = parse_args()

    if not args.fourk_dir or not args.src_prefix:
        raise SystemExit("Set --fourk-dir/--src-prefix (or FOURK_DIR/SRC_PREFIX env vars).")
    if not args.db_url:
        raise SystemExit("Set --db-url (or DATABASE_URL / DATABASE_PASSWORD_2 env var).")

    fourk, dupes = build_fourk_index(args.fourk_dir)
    log.info("4K folder: %d unique codes indexed from %s", len(fourk), args.fourk_dir)
    if dupes:
        log.warning("4K folder has %d duplicate codes (ignored):", len(dupes))
        for d in dupes:
            log.warning("  %s", d)

    indexed = fetch_indexed_paths(args.db_url, args.src_prefix)
    log.info("Indexed videos under %s: %d", args.src_prefix, len(indexed))

    plan = build_plan(indexed, fourk, args.src_prefix, args.dst_prefix)

    _print_section("UPGRADE (clean, will repoint)", plan.upgrade)
    _print_section("REVIEW (variant cut — needs --include-variants)", plan.review)
    _print_section("CONFLICT (multiple indexed files share a code — skipped)", plan.conflict)
    _print_section("ALREADY hi-res (skipped)", plan.already)
    _print_section("NO 4K match (left untouched)", plan.no_match)

    to_apply = list(plan.upgrade)
    if args.include_variants:
        to_apply += plan.review

    print(f"\nWould repoint {len(to_apply)} file(s) "
          f"({len(plan.upgrade)} upgrade"
          f"{' + %d review' % len(plan.review) if args.include_variants else ''}).")

    if not args.apply:
        print("DRY-RUN — no changes written. Re-run with --apply to commit.")
        return

    apply_changes(
        to_apply, args.db_url, args.collection,
        args.qdrant_host, args.qdrant_port, args.qdrant_grpc,
        args.fourk_dir, args.dst_prefix, args.rehash,
    )
    print(f"\nApplied {len(to_apply)} repoint(s).")


if __name__ == "__main__":
    main()
