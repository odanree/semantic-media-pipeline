"""
backfill_audio.py — Patch audio DSP features onto already-indexed Qdrant points.

The normal ingest pipeline skips files with processing_status='done', so the 945
videos indexed before audio extraction was wired in need this one-time backfill.

For each Qdrant video-frame point that lacks 'audio_has_speech' in its payload:
  1. Reads the file_path from the payload
  2. Calls extract_audio_features() — FFmpeg → librosa/scipy DSP
  3. Calls qdrant_client.set_payload() to patch the 9 audio fields onto the point

Audio features are file-level (not frame-level), so all frames from the same file
get the same payload update. The script groups by file_path to avoid re-processing
the same video for every frame.

Usage:
    python scripts/backfill_audio.py --stack lumen2 [--dry-run] [--batch-size N]
    python scripts/backfill_audio.py --stack lumen  [--dry-run]
    python scripts/backfill_audio.py --stack prod   [--dry-run]  # SSH tunnel via .env.backfill

    # Override video source root (if paths differ between container and host):
    VIDEO_ROOT_OVERRIDE="/mnt/source/e:E:/" python scripts/backfill_audio.py --stack lumen2

Requirements:
    pip install qdrant-client ffmpeg-python librosa soundfile scipy numpy

Notes:
    - Video files must be accessible on the machine running this script.
    - Set VIDEO_ROOT_OVERRIDE if the /mnt/source/... paths in Qdrant don't resolve locally.
      Example: VIDEO_ROOT_OVERRIDE="E:/" replaces "/mnt/source/e/" prefix.
    - R2/S3 mode: if S3_ENDPOINT_URL + S3_BUCKET are set, file_path is treated as an object
      key and downloaded to a temp file for each extraction, then deleted. Requires boto3.
      Set AWS_ACCESS_KEY_ID + AWS_SECRET_ACCESS_KEY (or S3_ACCESS_KEY + S3_SECRET_KEY).
    - Idempotent: points already having 'audio_has_speech' are skipped.
    - Non-fatal per file: extraction errors are logged and counted, not fatal.
    - Prod SSH credentials (SSH_HOST, SSH_USER, QDRANT_DOCKER_IP) are read from env vars.
      Copy .env.backfill.example → .env.backfill and fill in secrets (file is gitignored).
"""

from __future__ import annotations

import argparse
import contextlib
import datetime
import logging
import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from qdrant_client import QdrantClient
from qdrant_client.models import Filter, FieldCondition, IsNullCondition, PayloadField

# ─── Config ───────────────────────────────────────────────────────────────────

STACK_PRESETS: dict[str, dict] = {
    "lumen":         {"host": "127.0.0.1", "port": 6333,  "use_tunnel": False, "collection": "media_vectors"},
    "lumen2":        {"host": "127.0.0.1", "port": 6340,  "use_tunnel": False, "collection": "media_vectors2"},
    "prod":          {"host": "127.0.0.1", "port": 26334, "use_tunnel": True,  "collection": "media_vectors"},  # SSH tunnel from Windows/Mac
    "prod-internal": {"host": "qdrant",    "port": 6333,  "use_tunnel": False, "collection": "media_vectors"},  # inside prod worker container
}

COLLECTION         = os.getenv("QDRANT_COLLECTION_NAME", "media_vectors")  # overridden per-stack below

# SSH tunnel config (prod stack only) — keep secrets in .env.backfill (gitignored)
SSH_HOST           = os.getenv("SSH_HOST")            # required for --stack prod
SSH_USER           = os.getenv("SSH_USER", "root")    # username only, not a secret
QDRANT_DOCKER_IP   = os.getenv("QDRANT_DOCKER_IP")    # required for --stack prod
QDRANT_REMOTE_PORT = int(os.getenv("QDRANT_REMOTE_PORT", "6333"))

# Path rewriting: if Qdrant stores /mnt/source/e/foo.mp4 but locally it's E:/foo.mp4
# Set VIDEO_ROOT_OVERRIDE as a comma-separated list of "qdrant_prefix:local_prefix" pairs.
# Example: VIDEO_ROOT_OVERRIDE="/mnt/source/e:E:/Unsorted,/mnt/source/f-downloads:C:/Users/<user>/Downloads/<media-folder>"
VIDEO_ROOT_OVERRIDE = os.getenv("VIDEO_ROOT_OVERRIDE", "")

# ─── Logging ──────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)


# ─── Path rewriting ───────────────────────────────────────────────────────────

def _build_path_map() -> list[tuple[str, str]]:
    """Parse VIDEO_ROOT_OVERRIDE into a list of (qdrant_prefix, local_prefix) pairs."""
    if not VIDEO_ROOT_OVERRIDE:
        return []
    pairs = []
    for entry in VIDEO_ROOT_OVERRIDE.split(","):
        entry = entry.strip()
        if ":" not in entry:
            continue
        # Split on first colon only (Windows paths have colons too — split on the first only
        # if it looks like a /mnt/... prefix; otherwise split on last non-drive colon)
        # Simple heuristic: qdrant paths always start with /
        idx = entry.index(":")
        pairs.append((entry[:idx], entry[idx + 1:]))
    return pairs


def _resolve_path(qdrant_path: str, path_map: list[tuple[str, str]]) -> str:
    """Rewrite a Qdrant file_path to a locally accessible path."""
    for qdrant_prefix, local_prefix in path_map:
        if qdrant_path.startswith(qdrant_prefix):
            return local_prefix + qdrant_path[len(qdrant_prefix):]
    return qdrant_path


# ─── SSH tunnel (prod mode) ───────────────────────────────────────────────────

def _port_open(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=2):
            return True
    except OSError:
        return False


def _start_ssh_tunnel(local_port: int) -> subprocess.Popen:
    if _port_open("localhost", local_port):
        log.info("SSH tunnel already open on localhost:%d", local_port)
        return None

    cmd = [
        "ssh", "-N", "-L",
        f"{local_port}:{QDRANT_DOCKER_IP}:{QDRANT_REMOTE_PORT}",
        f"{SSH_USER}@{SSH_HOST}",
        "-o", "StrictHostKeyChecking=no",
        "-o", "BatchMode=yes",
        "-o", "ConnectTimeout=10",
    ]
    log.info("Opening SSH tunnel: %s", " ".join(cmd))
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    for _ in range(15):
        time.sleep(1)
        if _port_open("localhost", local_port):
            log.info("SSH tunnel ready on localhost:%d", local_port)
            return proc

    proc.terminate()
    log.error("SSH tunnel failed to open within 15s")
    sys.exit(1)


# ─── R2 / S3 download ────────────────────────────────────────────────────────

S3_ENDPOINT_URL = os.getenv("S3_ENDPOINT_URL", "")
S3_BUCKET       = os.getenv("S3_BUCKET", "")
S3_ACCESS_KEY   = os.getenv("AWS_ACCESS_KEY_ID") or os.getenv("S3_ACCESS_KEY", "")
S3_SECRET_KEY   = os.getenv("AWS_SECRET_ACCESS_KEY") or os.getenv("S3_SECRET_KEY", "")


@contextlib.contextmanager
def _local_video(file_path: str, path_map: list[tuple[str, str]]):
    """
    Yields a local filesystem path for the video.

    - If S3_ENDPOINT_URL + S3_BUCKET are set, file_path is treated as an R2/S3
      object key: the file is downloaded to a temp file, yielded, then deleted.
    - Otherwise falls back to path_map rewriting (local mount or VIDEO_ROOT_OVERRIDE).
    """
    if S3_ENDPOINT_URL and S3_BUCKET:
        try:
            import boto3  # noqa: PLC0415
            from botocore.config import Config  # noqa: PLC0415
        except ImportError:
            log.error("boto3 not installed — required for R2 download. pip install boto3")
            yield None
            return

        s3 = boto3.client(
            "s3",
            endpoint_url=S3_ENDPOINT_URL,
            aws_access_key_id=S3_ACCESS_KEY or None,
            aws_secret_access_key=S3_SECRET_KEY or None,
            config=Config(signature_version="s3v4"),
        )
        suffix = Path(file_path).suffix or ".mp4"
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp_path = tmp.name
        try:
            log.info("Downloading s3://%s/%s", S3_BUCKET, file_path)
            s3.download_file(S3_BUCKET, file_path, tmp_path)
            yield tmp_path
        except Exception as exc:
            log.warning("R2 download failed for %s: %s", file_path, exc)
            yield None
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
    else:
        local = _resolve_path(file_path, path_map)
        yield local if Path(local).exists() else None


# ─── Audio extraction ─────────────────────────────────────────────────────────

def _extract(video_path: str) -> dict | None:
    """
    Import and call extract_audio_features from the worker package.
    Resolves the worker directory in order:
      1. Relative to this script (repo layout: scripts/../worker)
      2. /app (inside the Docker worker container)
    """
    candidate = Path(__file__).parent.parent / "worker"
    worker_dir = candidate if (candidate / "ingest").exists() else Path("/app")
    if str(worker_dir) not in sys.path:
        sys.path.insert(0, str(worker_dir))
    from ingest.audio_extractor import extract_audio_features  # noqa: PLC0415
    return extract_audio_features(video_path)


# ─── Main ─────────────────────────────────────────────────────────────────────

def run_backfill(stack: str, dry_run: bool = False, batch_size: int = 100):
    stack_cfg = STACK_PRESETS[stack]
    collection = os.getenv("QDRANT_COLLECTION_NAME") or stack_cfg["collection"]
    tunnel_proc = None

    if stack_cfg["use_tunnel"]:
        if not SSH_HOST or not QDRANT_DOCKER_IP:
            log.error("--stack prod requires SSH_HOST and QDRANT_DOCKER_IP env vars. "
                      "Copy .env.backfill.example → .env.backfill, fill in values, then: source .env.backfill")
            sys.exit(1)
        tunnel_proc = _start_ssh_tunnel(stack_cfg["port"])

    client = QdrantClient(host=stack_cfg["host"], port=stack_cfg["port"], prefer_grpc=False)

    try:
        info = client.get_collection(collection)
    except Exception as exc:
        log.error("Cannot connect to Qdrant at %s:%d — %s", stack_cfg["host"], stack_cfg["port"], exc)
        if tunnel_proc:
            tunnel_proc.terminate()
        sys.exit(1)

    total_points = info.points_count
    log.info("Connected — %d total points in '%s'", total_points, collection)

    path_map = _build_path_map()
    if path_map:
        log.info("Path rewrites active: %s", path_map)

    # Track which file_paths we've already extracted audio for this run
    # (all frames from the same file share the same audio features)
    file_cache: dict[str, dict | None] = {}

    total = processed_files = processed_points = skipped = failed = 0
    offset = None
    t_start = time.time()

    while True:
        records, next_offset = client.scroll(
            collection_name=collection,
            limit=batch_size,
            offset=offset,
            with_payload=True,
            with_vectors=False,
        )

        if not records:
            break

        for point in records:
            total += 1
            payload = point.payload or {}

            # Only process video frames
            if payload.get("file_type") != "video":
                skipped += 1
                continue

            # Skip if audio already backfilled
            if payload.get("audio_has_speech") is not None:
                skipped += 1
                continue

            file_path = payload.get("file_path")
            if not file_path:
                skipped += 1
                continue

            # Extract audio once per file, reuse for all its frames
            if file_path not in file_cache:
                if not dry_run:
                    with _local_video(file_path, path_map) as local_path:
                        if not local_path:
                            log.warning("File not found/downloadable: %s", file_path)
                            file_cache[file_path] = None
                            failed += 1
                        else:
                            log.info("Extracting audio: %s", file_path)
                            try:
                                features = _extract(local_path)
                                file_cache[file_path] = features
                                if features:
                                    processed_files += 1
                                    log.info("  → %d features extracted", len(features))
                                else:
                                    log.info("  → no audio track")
                            except Exception as exc:
                                log.warning("Extraction failed for %s: %s", file_path, exc)
                                file_cache[file_path] = None
                                failed += 1
                else:
                    # dry-run: just record it as seen
                    file_cache[file_path] = {"audio_has_speech": False}  # placeholder

            features = file_cache.get(file_path)

            if dry_run:
                processed_points += 1
                continue

            if not features:
                # No audio track — write a sentinel so we don't retry this file
                features = {
                    "audio_has_speech": False,
                    "audio_rms_energy": 0.0,
                    "audio_duration_secs": 0.0,
                    "audio_mfcc_mean": None,
                    "audio_mfcc_std": None,
                    "audio_mel_mean_db": None,
                    "audio_dominant_pitch_class": None,
                    "audio_speech_band_power": None,
                    "audio_peak_frequency_hz": None,
                }

            client.set_payload(
                collection_name=collection,
                payload={**features, "updated_at": datetime.datetime.utcnow().isoformat()},
                points=[point.id],
            )
            processed_points += 1

        # Progress report every 1000 points
        if total % 1000 == 0 and total > 0:
            elapsed = time.time() - t_start
            rate = total / elapsed if elapsed > 0 else 0
            eta_min = ((total_points - total) / rate / 60) if rate > 0 else "?"
            log.info(
                "Progress — seen=%d processed_points=%d files=%d skipped=%d failed=%d  rate=%.0f/s  ETA≈%.1fmin",
                total, processed_points, processed_files, skipped, failed, rate, eta_min,
            )

        if next_offset is None:
            break
        offset = next_offset

    elapsed_total = time.time() - t_start
    log.info(
        "Done — total_seen=%d processed_points=%d unique_files=%d skipped=%d failed=%d elapsed=%.1fmin",
        total, processed_points, processed_files, skipped, failed, elapsed_total / 60,
    )
    log.info("Unique files in cache: %d  (audio extracted: %d, no audio: %d)",
             len(file_cache),
             sum(1 for v in file_cache.values() if v and v.get("audio_rms_energy", 0) > 0),
             sum(1 for v in file_cache.values() if not v or v.get("audio_rms_energy", 0) == 0))

    if tunnel_proc:
        tunnel_proc.terminate()
        log.info("SSH tunnel closed")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Backfill audio DSP features onto existing Qdrant video points")
    parser.add_argument("--stack", choices=list(STACK_PRESETS), required=True,
                        help="Which Qdrant instance: lumen (6333), lumen2 (6340), prod (SSH tunnel to 26334)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Count affected points without extracting or writing")
    parser.add_argument("--batch-size", type=int, default=100,
                        help="Qdrant scroll batch size (default: 100)")
    args = parser.parse_args()

    run_backfill(stack=args.stack, dry_run=args.dry_run, batch_size=args.batch_size)
