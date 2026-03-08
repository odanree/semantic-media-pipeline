#!/usr/bin/env python3
"""
Pexels -> Cloudflare R2 Demo Content Downloader
================================================
Downloads sports/action videos from Pexels and uploads them to R2.

Usage:
    # Download to local folder only (default)
    python scripts/download_pexels_to_r2.py

    # Download + upload to R2
    python scripts/download_pexels_to_r2.py --upload-to-r2

    # Only upload what's already in the local folder to R2
    python scripts/download_pexels_to_r2.py --upload-only --upload-to-r2

    # Dry run (prints what would happen, no downloads/uploads)
    python scripts/download_pexels_to_r2.py --dry-run

Requirements:
    pip install requests boto3 python-dotenv

Reads from .env:
    PEXELS_API_KEY      - required
    S3_ENDPOINT_URL     - R2 endpoint (https://<account>.r2.cloudflarestorage.com)
    S3_BUCKET           - R2 bucket name
    S3_ACCESS_KEY       - R2 Access Key ID
    S3_SECRET_KEY       - R2 Secret Access Key
    S3_REGION           - auto (for R2)

Output folder: ./data/pexels_demo/
R2 prefix: pexels-demo/
"""

import argparse
import os
import sys
import time
from pathlib import Path

import requests

# ---------------------------------------------------------------------------
# Load .env
# ---------------------------------------------------------------------------
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent.parent / ".env")
except ImportError:
    pass  # .env already sourced by shell or docker

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
PEXELS_API_KEY  = os.getenv("PEXELS_API_KEY", "")
# R2_* vars take priority over S3_* so the Docker MinIO config is not disturbed
S3_ENDPOINT_URL = os.getenv("R2_ENDPOINT_URL") or os.getenv("S3_ENDPOINT_URL", "")
S3_BUCKET       = os.getenv("R2_BUCKET")       or os.getenv("S3_BUCKET", "")
S3_ACCESS_KEY   = os.getenv("R2_ACCESS_KEY")   or os.getenv("S3_ACCESS_KEY", "")
S3_SECRET_KEY   = os.getenv("R2_SECRET_KEY")   or os.getenv("S3_SECRET_KEY", "")
S3_REGION       = os.getenv("R2_REGION")       or os.getenv("S3_REGION", "auto")

# Where to save locally before uploading
LOCAL_DIR = Path(__file__).parent.parent / "data" / "pexels_demo"

# Hard cap: stop downloading once we hit this (leave headroom under 10GB R2 free tier)
MAX_TOTAL_BYTES = 9_000_000_000  # 9 GB  (raised to accommodate office/work content batch)

# Per-video max - skip anything bigger than this (keeps files manageable)
MAX_VIDEO_BYTES = 200_000_000  # 200 MB

# Pexels search queries: (query, per_page, pages)
# per_page max = 80, pages = how many result pages to fetch per query
SEARCH_QUERIES = [
    # --- Sports (original) ---
    ("basketball dribbling",    50, 2),
    ("soccer match",            50, 2),
    ("tennis player",           50, 2),
    ("running athlete sprint",  50, 2),
    ("swimming competition",    50, 1),
    ("volleyball",              50, 1),
    ("skateboarding tricks",    50, 1),
    ("boxing training",         50, 1),
    ("cycling race",            50, 1),
    ("gym workout",             50, 1),
    ("football touchdown",      50, 1),
    ("golf swing",              50, 1),
    ("baseball pitch",          50, 1),
    ("martial arts",            50, 1),
    ("surfing wave",            50, 1),
    ("rock climbing",           50, 1),
    ("parkour",                 50, 1),
    ("dance performance",       50, 1),
    ("yoga exercise",           50, 1),
    ("aerial sports",           50, 1),
    # --- Nature & outdoors ---
    ("ocean waves",             50, 1),
    ("waterfall forest",        50, 1),
    ("mountain landscape",      50, 1),
    ("sunset timelapse",        50, 1),
    ("wildlife animals",        50, 1),
    ("drone aerial nature",     50, 1),
    ("rain storm lightning",    50, 1),
    ("snow winter",             50, 1),
    ("desert sand",             50, 1),
    ("birds flying",            50, 1),
    # --- City & urban life ---
    ("city traffic timelapse",  50, 1),
    ("people walking street",   50, 1),
    ("night city lights",       50, 1),
    ("busy market crowd",       50, 1),
    ("subway train commute",    50, 1),
    ("construction building",   50, 1),
    ("cafe coffee shop",        50, 1),
    ("rooftop skyline",         50, 1),
    # --- Technology & work ---
    ("coding programmer laptop",50, 1),
    ("drone flight fpv",        50, 1),
    ("robot automation factory",50, 1),
    ("3d printing",             50, 1),
    ("podcast recording studio",50, 1),
    ("photography camera",      50, 1),
    # --- Food & lifestyle ---
    ("cooking chef kitchen",    50, 1),
    ("coffee latte art",        50, 1),
    ("food market ingredients", 50, 1),
    ("travel adventure",        50, 1),
    ("family outdoor picnic",   50, 1),
    # --- Office & work ---
    ("office meeting business team",     50, 1),
    ("people working desk laptop",       50, 1),
    ("corporate presentation boardroom", 50, 1),
    ("remote work home office",          50, 1),
    ("business professionals handshake", 50, 1),
    ("coworkers whiteboard collaboration",50, 1),
    ("call center customer service",     50, 1),
    ("startup modern workspace",         50, 1),
]

# Preferred video quality (will fall back down the list)
QUALITY_PREFERENCE = ["hd", "sd", "hls"]

# R2 key prefix
R2_PREFIX = "pexels-demo"

# Pexels base URL
PEXELS_API_BASE = "https://api.pexels.com/videos"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _fmt_bytes(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1000:
            return f"{n:.1f} {unit}"
        n /= 1000
    return f"{n:.1f} TB"


def _pexels_headers() -> dict:
    return {"Authorization": PEXELS_API_KEY}


def search_videos(query: str, per_page: int = 80, page: int = 1) -> list[dict]:
    """Return a list of Pexels video objects for the query."""
    url = f"{PEXELS_API_BASE}/search"
    params = {"query": query, "per_page": per_page, "page": page, "orientation": "landscape"}
    r = requests.get(url, headers=_pexels_headers(), params=params, timeout=30)
    r.raise_for_status()
    return r.json().get("videos", [])


def best_video_file(video: dict) -> dict | None:
    """Pick the best quality video file under MAX_VIDEO_BYTES."""
    files = video.get("video_files", [])
    # Sort by quality preference, then by resolution descending
    for quality in QUALITY_PREFERENCE:
        candidates = [
            f for f in files
            if f.get("quality") == quality
            and (f.get("file_type") or "").startswith("video/")
            and (f.get("width") or 0) >= 640
        ]
        candidates.sort(key=lambda f: f.get("width", 0), reverse=True)
        for f in candidates:
            size = f.get("size") or 0
            if size == 0 or size <= MAX_VIDEO_BYTES:
                return f
    return None


def safe_filename(video: dict, ext: str = "mp4") -> str:
    """Stable filename based on source (search) + Pexels video ID - never changes between runs."""
    return f"pexels-search-{video['id']}.{ext}"


def extract_video_id(filename: str) -> str | None:
    """Extract Pexels video ID from any filename format.

    Handled formats (in order):
      pexels-search-12345.mp4   <- current format (source + id)
      pexels-12345.mp4          <- previous format (id only)
      some-slug_12345.mp4       <- legacy slug format
    """
    import re
    # Current format: pexels-<source>-12345.mp4
    m = re.match(r"pexels-[a-z]+-([0-9]+)\.mp4$", filename)
    if m:
        return m.group(1)
    # Previous format: pexels-12345.mp4
    m = re.match(r"pexels-([0-9]+)\.mp4$", filename)
    if m:
        return m.group(1)
    # Legacy format: some-slug_12345.mp4 (ID after last underscore)
    m = re.search(r"_([0-9]+)\.mp4$", filename)
    if m:
        return m.group(1)
    return None


# ---------------------------------------------------------------------------
# R2 helpers
# ---------------------------------------------------------------------------
def fetch_r2_video_ids() -> tuple[set[str], int]:
    """Return (set of Pexels video IDs already stored in R2, total R2 bytes used).

    Using R2 bytes as the cap baseline — not local disk — so re-running on a
    machine with a warm local cache doesn't falsely trigger the size limit.
    """
    if not all([S3_ENDPOINT_URL, S3_BUCKET, S3_ACCESS_KEY, S3_SECRET_KEY]):
        return set(), 0
    try:
        import boto3
        from botocore.config import Config
        s3 = boto3.client(
            "s3",
            endpoint_url=S3_ENDPOINT_URL,
            aws_access_key_id=S3_ACCESS_KEY,
            aws_secret_access_key=S3_SECRET_KEY,
            region_name=S3_REGION,
            config=Config(signature_version="s3v4"),
        )
        ids: set[str] = set()
        r2_bytes: int = 0
        paginator = s3.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=S3_BUCKET, Prefix=f"{R2_PREFIX}/"):
            for obj in page.get("Contents", []):
                fname = obj["Key"].split("/")[-1]
                vid_id = extract_video_id(fname)
                if vid_id:
                    ids.add(vid_id)
                r2_bytes += obj.get("Size", 0)
        return ids, r2_bytes
    except Exception as e:
        print(f"  WARNING: Could not list R2 objects: {e}")
        return set(), 0


# ---------------------------------------------------------------------------
# Download
# ---------------------------------------------------------------------------
def download_videos(dry_run: bool = False, r2_video_ids: set[str] | None = None, r2_bytes: int = 0, limit: int | None = None) -> list[Path]:
    """Download all queued videos; return list of local paths."""
    if r2_video_ids is None:
        r2_video_ids = set()
    if not PEXELS_API_KEY:
        print("ERROR: PEXELS_API_KEY not set in .env")
        sys.exit(1)

    LOCAL_DIR.mkdir(parents=True, exist_ok=True)

    # Cap is based on R2 usage, not local disk — re-running on a warm local cache
    # won't falsely trigger the limit before new queries are processed.
    local_file_count = len(list(LOCAL_DIR.glob("*.mp4")))
    local_bytes = sum(f.stat().st_size for f in LOCAL_DIR.glob("*.mp4"))
    total_bytes = r2_bytes  # tracks R2 total (existing + newly uploaded)
    # Only track newly downloaded files - don't re-upload the entire local dir each run
    downloaded: list[Path] = []

    print(f"\n{'=' * 60}")
    print(f"  Pexels -> R2 Demo Downloader")
    print(f"  Local dir : {LOCAL_DIR}")
    print(f"  Size cap  : {_fmt_bytes(MAX_TOTAL_BYTES)} (R2-based)")
    print(f"  R2 current: {_fmt_bytes(total_bytes)} ({len(r2_video_ids)} files)")
    print(f"  Local cache: {_fmt_bytes(local_bytes)} ({local_file_count} files)")
    print(f"  R2 headroom: {_fmt_bytes(MAX_TOTAL_BYTES - total_bytes)}")
    print(f"  R2 skip   : {len(r2_video_ids)} video IDs already in R2")
    if limit:
        print(f"  Limit     : {limit} new files")
    print(f"{'=' * 60}\n")

    new_count = 0  # track newly downloaded files for --limit

    for query, per_page, pages in SEARCH_QUERIES:
        if total_bytes >= MAX_TOTAL_BYTES:
            print(f"  [CAP REACHED] Stopping at {_fmt_bytes(total_bytes)}")
            break
        if limit is not None and new_count >= limit:
            print(f"  [LIMIT REACHED] Downloaded {new_count} new files")
            break

        videos = []
        for page in range(1, pages + 1):
            print(f"  Searching: '{query}' page {page}/{pages} (up to {per_page} results) ...")
            try:
                page_videos = search_videos(query, per_page, page)
                if not page_videos:
                    break
                videos.extend(page_videos)
                time.sleep(0.2)  # respect rate limit between pages
            except Exception as e:
                print(f"    WARNING: API error for '{query}' page {page}: {e}")
                break

        for video in videos:
            if total_bytes >= MAX_TOTAL_BYTES:
                break
            if limit is not None and new_count >= limit:
                break

            vfile = best_video_file(video)
            if not vfile:
                print(f"    SKIP  id={video['id']} - no suitable file found")
                continue

            filename = safe_filename(video)
            dest = LOCAL_DIR / filename

            # Skip if already in R2 (match by stable video ID)
            if str(video["id"]) in r2_video_ids:
                print(f"    R2    {filename} (id={video['id']} already in R2, skipping)")
                continue

            # Skip if already downloaded locally
            if dest.exists():
                print(f"    EXIST {filename} ({_fmt_bytes(dest.stat().st_size)})")
                continue

            size = vfile.get("size") or 0
            if total_bytes + size > MAX_TOTAL_BYTES:
                print(f"    SKIP  {filename} - would exceed cap ({_fmt_bytes(size)})")
                continue

            print(f"    GET   {filename:60s} ~{_fmt_bytes(size)}", end="", flush=True)

            if dry_run:
                print("  [DRY RUN]")
                continue

            # Stream download
            try:
                with requests.get(vfile["link"], stream=True, timeout=120) as resp:
                    resp.raise_for_status()
                    actual = 0
                    with open(dest, "wb") as fh:
                        for chunk in resp.iter_content(chunk_size=1024 * 256):
                            fh.write(chunk)
                            actual += len(chunk)

                total_bytes += actual
                downloaded.append(dest)
                new_count += 1
                print(f"  OK {_fmt_bytes(actual)}  total={_fmt_bytes(total_bytes)}")
            except Exception as e:
                print(f"  ERROR: {e}")
                dest.unlink(missing_ok=True)

            time.sleep(0.3)  # be polite to Pexels

    print(f"\n  Downloaded {len(downloaded)} files / {_fmt_bytes(total_bytes)}\n")
    return downloaded


# ---------------------------------------------------------------------------
# Upload to R2
# ---------------------------------------------------------------------------
def upload_to_r2(files: list[Path], dry_run: bool = False) -> None:
    """Upload local files to R2 bucket under R2_PREFIX/."""
    if not all([S3_ENDPOINT_URL, S3_BUCKET, S3_ACCESS_KEY, S3_SECRET_KEY]):
        print("ERROR: R2 credentials incomplete. Check S3_* vars in .env")
        print("  S3_ENDPOINT_URL, S3_BUCKET, S3_ACCESS_KEY, S3_SECRET_KEY required")
        sys.exit(1)

    try:
        import boto3
        from botocore.config import Config
    except ImportError:
        print("ERROR: boto3 not installed. Run: pip install boto3")
        sys.exit(1)

    s3 = boto3.client(
        "s3",
        endpoint_url=S3_ENDPOINT_URL,
        aws_access_key_id=S3_ACCESS_KEY,
        aws_secret_access_key=S3_SECRET_KEY,
        region_name=S3_REGION,
        config=Config(signature_version="s3v4"),
    )

    # Build set of already-uploaded keys + track current R2 usage
    print(f"  Checking existing R2 objects under {R2_PREFIX}/ ...")
    existing_keys: set[str] = set()
    r2_used_bytes: int = 0
    try:
        paginator = s3.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=S3_BUCKET, Prefix=f"{R2_PREFIX}/"):
            for obj in page.get("Contents", []):
                existing_keys.add(obj["Key"])
                r2_used_bytes += obj["Size"]
    except Exception as e:
        print(f"  WARNING: Could not list R2 objects: {e}")

    print(f"  {len(existing_keys)} files already in R2 ({_fmt_bytes(r2_used_bytes)} / {_fmt_bytes(MAX_TOTAL_BYTES)})")

    if r2_used_bytes >= MAX_TOTAL_BYTES:
        print(f"\n  R2 CAP REACHED: bucket already at {_fmt_bytes(r2_used_bytes)}, limit is {_fmt_bytes(MAX_TOTAL_BYTES)}.")
        print(f"  Skipping upload. Delete some objects or raise MAX_TOTAL_BYTES to continue.")
        return

    print()

    for path in files:
        key = f"{R2_PREFIX}/{path.name}"
        size = path.stat().st_size

        if key in existing_keys:
            print(f"  EXIST {path.name:60s} {_fmt_bytes(size)}")
            continue

        if r2_used_bytes + size > MAX_TOTAL_BYTES:
            print(f"  CAP   {path.name:60s} {_fmt_bytes(size)} -- would exceed R2 cap, stopping.")
            break

        print(f"  PUT   {path.name:60s} {_fmt_bytes(size)}", end="", flush=True)

        if dry_run:
            print("  [DRY RUN]")
            continue

        try:
            s3.upload_file(
                str(path),
                S3_BUCKET,
                key,
                ExtraArgs={"ContentType": "video/mp4"},
            )
            r2_used_bytes += size
            print("  OK")
        except Exception as e:
            print(f"  ERROR: {e}")

    print(f"\n  Upload complete -> s3://{S3_BUCKET}/{R2_PREFIX}/\n")


# ---------------------------------------------------------------------------
# R2 deduplication
# ---------------------------------------------------------------------------
def dedup_r2(dry_run: bool = False) -> None:
    """Remove duplicate R2 objects that share the same Pexels video ID.
    Keeps the newest object (by LastModified) and deletes the rest.
    """
    if not all([S3_ENDPOINT_URL, S3_BUCKET, S3_ACCESS_KEY, S3_SECRET_KEY]):
        print("ERROR: R2 credentials incomplete.")
        return
    try:
        import boto3
        from botocore.config import Config
    except ImportError:
        print("ERROR: boto3 not installed. Run: pip install boto3")
        return

    s3 = boto3.client(
        "s3",
        endpoint_url=S3_ENDPOINT_URL,
        aws_access_key_id=S3_ACCESS_KEY,
        aws_secret_access_key=S3_SECRET_KEY,
        region_name=S3_REGION,
        config=Config(signature_version="s3v4"),
    )

    print(f"  Scanning R2 bucket for duplicates under {R2_PREFIX}/ ...")
    objects: list[dict] = []
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=S3_BUCKET, Prefix=f"{R2_PREFIX}/"):
        objects.extend(page.get("Contents", []))

    print(f"  Found {len(objects)} total objects")

    # Group by extracted video ID
    from collections import defaultdict
    by_id: dict[str, list[dict]] = defaultdict(list)
    no_id: list[dict] = []
    for obj in objects:
        fname = obj["Key"].split("/")[-1]
        vid_id = extract_video_id(fname)
        if vid_id:
            by_id[vid_id].append(obj)
        else:
            no_id.append(obj)

    dupes_found = 0
    freed_bytes = 0
    for vid_id, objs in by_id.items():
        if len(objs) < 2:
            continue
        # Sort by LastModified descending - keep the newest
        objs.sort(key=lambda o: o["LastModified"], reverse=True)
        keep = objs[0]
        to_delete = objs[1:]
        dupes_found += len(to_delete)
        for obj in to_delete:
            freed_bytes += obj["Size"]
            print(f"  DELETE {obj['Key']:70s} {_fmt_bytes(obj['Size'])}  (keep: {keep['Key'].split('/')[-1]})", end="")
            if dry_run:
                print("  [DRY RUN]")
                continue
            try:
                s3.delete_object(Bucket=S3_BUCKET, Key=obj["Key"])
                print("  OK")
            except Exception as e:
                print(f"  ERROR: {e}")

    if dupes_found == 0:
        print("  No duplicates found.")
    else:
        action = "Would free" if dry_run else "Freed"
        print(f"\n  Removed {dupes_found} duplicate objects. {action} {_fmt_bytes(freed_bytes)}.")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main() -> None:
    parser = argparse.ArgumentParser(
        description="Download Pexels sports videos and upload to Cloudflare R2"
    )
    parser.add_argument("--download-only", action="store_true",
                        help="Download to local folder only, skip R2 upload")
    parser.add_argument("--upload-only",   action="store_true",
                        help="Upload already-downloaded files, skip new downloads")
    parser.add_argument("--dry-run",       action="store_true",
                        help="Print what would happen without downloading/uploading")
    parser.add_argument("--upload-to-r2",  action="store_true",
                        help="After downloading, also upload to Cloudflare R2 (default: local only)")
    parser.add_argument("--dedup-r2",       action="store_true",
                        help="Remove duplicate R2 objects (same video ID, old slug-based names kept newest)")
    parser.add_argument("--limit",          type=int, default=None, metavar="N",
                        help="Download/upload at most N new files (useful for testing)")
    args = parser.parse_args()

    if args.dry_run:
        print("\n  [DRY RUN MODE - no files will be written]\n")

    # Dedup is a standalone operation - run and exit
    if args.dedup_r2:
        dedup_r2(dry_run=args.dry_run)
        print("  Done.")
        return

    # Pre-fetch R2 contents so we skip re-downloading files already there
    r2_video_ids: set[str] = set()
    r2_bytes: int = 0
    if args.upload_to_r2 and not args.upload_only:
        print("  Checking R2 for already-uploaded files...")
        r2_video_ids, r2_bytes = fetch_r2_video_ids()
        print(f"  {len(r2_video_ids)} video IDs already in R2 ({_fmt_bytes(r2_bytes)}) - will skip downloading those\n")

    if not args.upload_only:
        files = download_videos(dry_run=args.dry_run, r2_video_ids=r2_video_ids, r2_bytes=r2_bytes, limit=args.limit)
    else:
        files = list(LOCAL_DIR.glob("*.mp4"))
        print(f"  Upload-only: found {len(files)} local files in {LOCAL_DIR}")

    if args.upload_to_r2 and not args.download_only:
        upload_to_r2(files, dry_run=args.dry_run)
    elif not args.upload_to_r2 and not args.download_only:
        print(f"  Skipping R2 upload (pass --upload-to-r2 to enable)")

    print("  Done.")


if __name__ == "__main__":
    main()
