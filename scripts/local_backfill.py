"""
local_backfill.py — Run caption backfill locally using GPU Ollama, writing to prod Qdrant via SSH tunnel.

Usage:
    python scripts/local_backfill.py [--dry-run] [--batch-size N]

Requirements:
    pip install qdrant-client requests

Config (top of file or via env vars):
    SSH_HOST, SSH_USER — prod server connection
    QDRANT_DOCKER_IP — Qdrant container IP on prod Docker bridge (REDACTED_DOCKER_IP)
    FRAME_CACHE_DIR — local frame cache path (J:/frame_cache)
    OLLAMA_URL — local Ollama base URL
"""

import argparse
import base64
import hashlib
import datetime
import logging
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import requests
from qdrant_client import QdrantClient

# ─── Config ───────────────────────────────────────────────────────────────────

SSH_HOST       = os.getenv("SSH_HOST",       "REDACTED_PROD_IP")
SSH_USER       = os.getenv("SSH_USER",       "root")

QDRANT_DOCKER_IP   = os.getenv("QDRANT_DOCKER_IP", "REDACTED_DOCKER_IP")
QDRANT_REMOTE_PORT = int(os.getenv("QDRANT_REMOTE_PORT", "6333"))
QDRANT_LOCAL_PORT  = int(os.getenv("QDRANT_LOCAL_PORT",  "26333"))  # local port for tunnel

COLLECTION    = os.getenv("QDRANT_COLLECTION_NAME", "media_vectors")
FRAME_CACHE   = Path(os.getenv("FRAME_CACHE_DIR", "J:/frame_cache"))
OLLAMA_URL    = os.getenv("OLLAMA_URL", "http://localhost:11434")
CAPTION_MODEL = os.getenv("CAPTION_MODEL", "moondream")
FPS           = float(os.getenv("KEYFRAME_FPS", "0.5"))
RESOLUTION    = int(os.getenv("KEYFRAME_RESOLUTION", "224"))
MOONDREAM_TIMEOUT = int(os.getenv("MOONDREAM_TIMEOUT", "60"))  # 60s covers cold VRAM reload

# ─── Logging ──────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

# ─── Helpers ──────────────────────────────────────────────────────────────────

def _frame_cache_key(file_hash: str, fps: float, resolution: int) -> str:
    """Matches the task._frame_cache_key() implementation exactly."""
    raw = f"{file_hash}:fps={fps}:res={resolution}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def get_caption(img_b64: str) -> str:
    r = requests.post(
        f"{OLLAMA_URL}/api/generate",
        json={
            "model": CAPTION_MODEL,
            "prompt": "Describe what you see in this image in one concise sentence.",
            "images": [img_b64],
            "stream": False,
        },
        timeout=MOONDREAM_TIMEOUT,
    )
    r.raise_for_status()
    return r.json().get("response", "").strip()


# ─── SSH tunnel context manager ───────────────────────────────────────────────

class SSHTunnel:
    """Start 'ssh -N -L' as a subprocess and wait until the port is ready."""

    def __init__(self, host: str, user: str, remote_ip: str, remote_port: int, local_port: int):
        self.cmd = [
            "ssh", "-N",
            "-L", f"127.0.0.1:{local_port}:{remote_ip}:{remote_port}",
            "-o", "StrictHostKeyChecking=accept-new",
            "-o", "ServerAliveInterval=15",
            f"{user}@{host}",
        ]
        self.local_port = local_port
        self._proc: subprocess.Popen | None = None

    def __enter__(self):
        self._proc = subprocess.Popen(self.cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        # Wait up to 15s for the port to be ready
        for _ in range(30):
            try:
                s = socket.create_connection(("127.0.0.1", self.local_port), timeout=0.5)
                s.close()
                return self
            except OSError:
                time.sleep(0.5)
        raise RuntimeError(f"SSH tunnel on port {self.local_port} did not come up in time")

    def __exit__(self, *_):
        if self._proc:
            self._proc.terminate()
            self._proc.wait(timeout=5)


# ─── Main ─────────────────────────────────────────────────────────────────────

def run_backfill(dry_run: bool = False, scroll_batch: int = 100):
    log.info("Starting local backfill — dry_run=%s model=%s cache=%s", dry_run, CAPTION_MODEL, FRAME_CACHE)
    log.info("Connecting SSH tunnel: %s@%s → %s:%d → localhost:%d",
             SSH_USER, SSH_HOST, QDRANT_DOCKER_IP, QDRANT_REMOTE_PORT, QDRANT_LOCAL_PORT)

    with SSHTunnel(SSH_HOST, SSH_USER, QDRANT_DOCKER_IP, QDRANT_REMOTE_PORT, QDRANT_LOCAL_PORT) as tunnel:
        log.info("SSH tunnel open on localhost:%d", tunnel.local_port)

        client = QdrantClient(host="127.0.0.1", port=tunnel.local_port, prefer_grpc=False)
        info = client.get_collection(COLLECTION)
        total_points = info.points_count
        log.info("Connected to prod Qdrant — %d points in %s", total_points, COLLECTION)

        # Warm up Ollama (first call loads model into VRAM)
        if not dry_run:
            log.info("Warming up Ollama model %s...", CAPTION_MODEL)
            test_img = base64.b64encode(b'\xff\xd8\xff\xe0' + b'\x00' * 100).decode()
            try:
                requests.post(
                    f"{OLLAMA_URL}/api/generate",
                    json={"model": CAPTION_MODEL, "prompt": "hi", "images": [test_img], "stream": False},
                    timeout=15,
                )
            except Exception:
                pass  # warmup failure is non-fatal
            log.info("Ollama ready")

        total = processed = skipped = failed = 0
        offset = None
        t_start = time.time()
        timings = []

        while True:
            records, next_offset = client.scroll(
                collection_name=COLLECTION,
                limit=scroll_batch,
                offset=offset,
                with_payload=True,
                with_vectors=False,
            )

            if not records:
                break

            for point in records:
                total += 1
                payload = point.payload or {}

                # Skip non-video or already-captioned
                if payload.get("file_type") != "video" or payload.get("caption"):
                    skipped += 1
                    continue

                file_hash  = payload.get("file_hash")
                frame_index = payload.get("frame_index")
                if file_hash is None or frame_index is None:
                    skipped += 1
                    continue

                cache_dir = FRAME_CACHE / _frame_cache_key(file_hash, FPS, RESOLUTION)
                sorted_frames = sorted(cache_dir.glob("frame_*.jpg")) if cache_dir.exists() else []
                if not sorted_frames or frame_index >= len(sorted_frames):
                    log.debug("Frame not in local cache: hash=%s idx=%s", file_hash, frame_index)
                    failed += 1
                    continue

                frame_path = sorted_frames[frame_index]

                if dry_run:
                    processed += 1
                    continue

                # Call local Ollama (GPU)
                try:
                    t0 = time.time()
                    with open(frame_path, "rb") as fh:
                        img_b64 = base64.b64encode(fh.read()).decode()
                    caption = get_caption(img_b64)
                    elapsed = time.time() - t0
                    timings.append(elapsed)

                    now = datetime.datetime.utcnow().isoformat()
                    if caption:
                        client.set_payload(
                            collection_name=COLLECTION,
                            payload={"caption": caption, "updated_at": now},
                            points=[point.id],
                        )
                        processed += 1
                    else:
                        # Write placeholder so this point is skipped on future runs
                        log.warning("Empty caption for point %s — writing placeholder", point.id)
                        client.set_payload(
                            collection_name=COLLECTION,
                            payload={"caption": "[no description]", "updated_at": now},
                            points=[point.id],
                        )
                        failed += 1

                except Exception as exc:
                    log.warning("Caption failed for point %s (%s): %s", point.id, frame_path, exc)
                    failed += 1

            # Progress report every 500 processed frames
            done = processed + failed
            if done > 0 and done % 500 == 0:
                avg = sum(timings[-500:]) / len(timings[-500:]) if timings else 0
                remaining = (total_points - total) / scroll_batch * scroll_batch  # rough
                eta_h = (remaining * avg / 3600) if avg > 0 else "?"
                log.info(
                    "Progress — processed=%d skipped=%d failed=%d seen=%d  avg=%.2fs  ETA≈%.1fh",
                    processed, skipped, failed, total, avg, eta_h,
                )

            if next_offset is None:
                break
            offset = next_offset

        elapsed_total = time.time() - t_start
        avg_overall = sum(timings) / len(timings) if timings else 0
        summary = {
            "status": "complete",
            "total_seen": total,
            "processed": processed,
            "skipped": skipped,
            "failed": failed,
            "elapsed_min": round(elapsed_total / 60, 1),
            "avg_s_per_frame": round(avg_overall, 2),
        }
        log.info("Done: %s", summary)
        return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Local GPU caption backfill → prod Qdrant")
    parser.add_argument("--dry-run", action="store_true", help="Count frames without calling Ollama")
    parser.add_argument("--batch-size", type=int, default=100, help="Qdrant scroll batch size")
    args = parser.parse_args()

    run_backfill(dry_run=args.dry_run, scroll_batch=args.batch_size)
