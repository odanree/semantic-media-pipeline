#!/usr/bin/env python3
"""
Pexels → Cloudflare R2 Demo Content Downloader
================================================
Downloads sports/action videos from Pexels and uploads them to R2.

Usage:
    # Download only (inspect before uploading)
    python scripts/download_pexels_to_r2.py --download-only

    # Download + upload to R2
    python scripts/download_pexels_to_r2.py

    # Only upload what's already in the local folder
    python scripts/download_pexels_to_r2.py --upload-only

    # Dry run (prints what would happen, no downloads/uploads)
    python scripts/download_pexels_to_r2.py --dry-run

Requirements:
    pip install requests boto3 python-dotenv

Reads from .env:
    PEXELS_API_KEY      — required
    S3_ENDPOINT_URL     — R2 endpoint (https://<account>.r2.cloudflarestorage.com)
    S3_BUCKET           — R2 bucket name
    S3_ACCESS_KEY       — R2 Access Key ID
    S3_SECRET_KEY       — R2 Secret Access Key
    S3_REGION           — auto (for R2)

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
S3_ENDPOINT_URL = os.getenv("S3_ENDPOINT_URL", "")
S3_BUCKET       = os.getenv("S3_BUCKET", "")
S3_ACCESS_KEY   = os.getenv("S3_ACCESS_KEY", "")
S3_SECRET_KEY   = os.getenv("S3_SECRET_KEY", "")
S3_REGION       = os.getenv("S3_REGION", "auto")

# Where to save locally before uploading
LOCAL_DIR = Path(__file__).parent.parent / "data" / "pexels_demo"

# Hard cap: stop downloading once we hit this (leave headroom under 5GB)
MAX_TOTAL_BYTES = 4_500_000_000  # 4.5 GB

# Per-video max — skip anything bigger than this (keeps files manageable)
MAX_VIDEO_BYTES = 200_000_000  # 200 MB

# Pexels search queries: (query, per_page, pages)
# per_page max = 80, pages = how many result pages to fetch per query
SEARCH_QUERIES = [
    ("basketball dribbling",    80, 2),
    ("soccer match",            80, 2),
    ("tennis player",           80, 2),
    ("running athlete sprint",  80, 2),
    ("swimming competition",    80, 1),
    ("volleyball",              80, 1),
    ("skateboarding tricks",    80, 1),
    ("boxing training",         80, 1),
    ("cycling race",            80, 1),
    ("gym workout",             80, 1),
    ("football touchdown",      80, 1),
    ("golf swing",              80, 1),
    ("baseball pitch",          80, 1),
    ("martial arts",            80, 1),
    ("surfing wave",            80, 1),
    ("rock climbing",           80, 1),
    ("parkour",                 80, 1),
    ("dance performance",       80, 1),
    ("yoga exercise",           80, 1),
    ("aerial sports",           80, 1),
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
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
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
    vid_id = video["id"]
    query_slug = (video.get("url", "").split("/")[-2] or f"video-{vid_id}")[:40]
    return f"{query_slug}_{vid_id}.{ext}"


# ---------------------------------------------------------------------------
# Download
# ---------------------------------------------------------------------------
def download_videos(dry_run: bool = False) -> list[Path]:
    """Download all queued videos; return list of local paths."""
    if not PEXELS_API_KEY:
        print("ERROR: PEXELS_API_KEY not set in .env")
        sys.exit(1)

    LOCAL_DIR.mkdir(parents=True, exist_ok=True)

    total_bytes = sum(f.stat().st_size for f in LOCAL_DIR.glob("*.mp4"))
    downloaded: list[Path] = list(LOCAL_DIR.glob("*.mp4"))

    print(f"\n{'=' * 60}")
    print(f"  Pexels → R2 Demo Downloader")
    print(f"  Local dir : {LOCAL_DIR}")
    print(f"  Size cap  : {_fmt_bytes(MAX_TOTAL_BYTES)}")
    print(f"  Already   : {_fmt_bytes(total_bytes)} ({len(downloaded)} files)")
    print(f"{'=' * 60}\n")

    for query, per_page, pages in SEARCH_QUERIES:
        if total_bytes >= MAX_TOTAL_BYTES:
            print(f"  [CAP REACHED] Stopping at {_fmt_bytes(total_bytes)}")
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

            vfile = best_video_file(video)
            if not vfile:
                print(f"    SKIP  id={video['id']} — no suitable file found")
                continue

            filename = safe_filename(video)
            dest = LOCAL_DIR / filename

            # Skip if already downloaded
            if dest.exists():
                print(f"    EXIST {filename} ({_fmt_bytes(dest.stat().st_size)})")
                continue

            size = vfile.get("size") or 0
            if total_bytes + size > MAX_TOTAL_BYTES:
                print(f"    SKIP  {filename} — would exceed cap ({_fmt_bytes(size)})")
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
                print(f"  ✓ {_fmt_bytes(actual)}  total={_fmt_bytes(total_bytes)}")
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

    # Build set of already-uploaded keys to allow resuming
    print(f"  Checking existing R2 objects under {R2_PREFIX}/ ...")
    existing_keys: set[str] = set()
    try:
        paginator = s3.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=S3_BUCKET, Prefix=f"{R2_PREFIX}/"):
            for obj in page.get("Contents", []):
                existing_keys.add(obj["Key"])
    except Exception as e:
        print(f"  WARNING: Could not list R2 objects: {e}")

    print(f"  {len(existing_keys)} files already in R2\n")

    for path in files:
        key = f"{R2_PREFIX}/{path.name}"
        size = path.stat().st_size

        if key in existing_keys:
            print(f"  EXIST {path.name:60s} {_fmt_bytes(size)}")
            continue

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
            print("  ✓")
        except Exception as e:
            print(f"  ERROR: {e}")

    print(f"\n  Upload complete → s3://{S3_BUCKET}/{R2_PREFIX}/\n")


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
    args = parser.parse_args()

    if args.dry_run:
        print("\n  [DRY RUN MODE — no files will be written]\n")

    if not args.upload_only:
        files = download_videos(dry_run=args.dry_run)
    else:
        files = list(LOCAL_DIR.glob("*.mp4"))
        print(f"  Upload-only: found {len(files)} local files in {LOCAL_DIR}")

    if not args.download_only and not args.dry_run or (args.dry_run and not args.download_only):
        upload_to_r2(files, dry_run=args.dry_run)

    print("  Done.")


if __name__ == "__main__":
    main()
