"""
local_backfill_dev.py — Caption backfill against the LOCAL Docker Qdrant (no SSH tunnel).

Usage:
    python scripts/local_backfill_dev.py [--dry-run] [--batch-size N]

Requirements:
    pip install qdrant-client requests

Prerequisites:
    - lumen-qdrant port 6333 must be exposed on localhost.
      (docker-compose.yml already has  ports: - "6333:6333" for the qdrant service)
    - Apply with: docker compose up -d --no-deps qdrant
    - Ollama must be running locally with the caption model pulled:
      ollama pull moondream  (or set CAPTION_MODEL env var)
    - Frame cache must be accessible locally (FRAME_CACHE_DIR, default J:/frame_cache)
"""

import argparse
import base64
import hashlib
import datetime
import logging
import os
import time
from pathlib import Path

import requests
from qdrant_client import QdrantClient

# ─── Config ───────────────────────────────────────────────────────────────────

QDRANT_HOST   = os.getenv("QDRANT_HOST",   "localhost")
QDRANT_PORT   = int(os.getenv("QDRANT_PORT", "6333"))
COLLECTION    = os.getenv("QDRANT_COLLECTION_NAME", "media_vectors")
FRAME_CACHE   = Path(os.getenv("FRAME_CACHE_DIR", "J:/frame_cache"))
OLLAMA_URL    = os.getenv("OLLAMA_URL", "http://localhost:11434")
CAPTION_MODEL = os.getenv("CAPTION_MODEL", "moondream")
FPS           = float(os.getenv("KEYFRAME_FPS", "0.5"))
RESOLUTION    = int(os.getenv("KEYFRAME_RESOLUTION", "224"))
MOONDREAM_TIMEOUT = int(os.getenv("MOONDREAM_TIMEOUT", "60"))

# ─── Logging ──────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

# ─── Helpers ──────────────────────────────────────────────────────────────────

def _frame_cache_key(file_hash: str, fps: float, resolution: int) -> str:
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


# ─── Main ─────────────────────────────────────────────────────────────────────

def run_backfill(dry_run: bool = False, scroll_batch: int = 100):
    log.info("Starting local-dev backfill — dry_run=%s model=%s cache=%s", dry_run, CAPTION_MODEL, FRAME_CACHE)
    log.info("Connecting to local Qdrant at %s:%d", QDRANT_HOST, QDRANT_PORT)

    client = QdrantClient(host=QDRANT_HOST, port=QDRANT_PORT, prefer_grpc=False)

    try:
        info = client.get_collection(COLLECTION)
    except Exception as exc:
        log.error("Cannot connect to Qdrant at %s:%d — %s", QDRANT_HOST, QDRANT_PORT, exc)
        log.error("Make sure lumen-qdrant is running with port 6333 exposed:")
        log.error("  docker compose up -d --no-deps qdrant")
        raise SystemExit(1)

    total_points = info.points_count
    log.info("Connected — %d points in collection '%s'", total_points, COLLECTION)

    if total_points == 0:
        log.warning("Collection is empty — has lumen1 been ingested yet?")
        log.warning("  curl -X POST http://localhost:8000/api/ingest -H 'Content-Type: application/json' -d '{\"media_root\": \"/mnt/source\"}'")
        return

    # Warm up Ollama
    if not dry_run:
        log.info("Warming up Ollama model '%s'...", CAPTION_MODEL)
        try:
            requests.post(
                f"{OLLAMA_URL}/api/generate",
                json={"model": CAPTION_MODEL, "prompt": "hi", "images": [], "stream": False},
                timeout=15,
            )
        except Exception:
            pass
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

            file_hash = payload.get("file_hash")
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

        done = processed + failed
        if done > 0 and done % 500 == 0:
            avg = sum(timings[-500:]) / len(timings[-500:]) if timings else 0
            eta_h = ((total_points - total) * avg / 3600) if avg > 0 else "?"
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
    parser = argparse.ArgumentParser(description="Local-dev caption backfill → local Qdrant")
    parser.add_argument("--dry-run", action="store_true", help="Count captionable frames without calling Ollama")
    parser.add_argument("--batch-size", type=int, default=100, help="Qdrant scroll batch size")
    args = parser.parse_args()

    run_backfill(dry_run=args.dry_run, scroll_batch=args.batch_size)
