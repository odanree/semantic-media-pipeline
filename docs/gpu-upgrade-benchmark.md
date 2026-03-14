# GPU Upgrade Benchmark: CPU vs RTX 3090

## Setup
- **CPU**: AMD Ryzen 7900X (12-core)
- **GPU**: NVIDIA GeForce RTX 3090 24GB GDDR6X
- **OS**: Windows 11 Pro
- **Pipeline**: Semantic Media Pipeline (CLIP ViT-L-14 + Whisper VAD)
- **Worker**: Windows native Celery (`--pool=solo`), 4 concurrent workers
- **Batch size**: 256 frames

---

## Video 1 — 3,543 frames, cache HIT, GPU CLIP only (CPU Whisper)

| Step            | CPU Only (est.) | RTX 3090    |
|-----------------|-----------------|-------------|
| Frame extraction| ~7m             | 0s (cached) |
| Audio (Whisper) | ~11m 6s         | 11m 6s      |
| CLIP embedding  | ~2h 45m         | **1m 22s**  |
| Qdrant upsert   | 3.4s            | 3.4s        |
| **Total**       | **~3h**         | **12m 33s** |

---

## Video 2 — 5,420 frames, no cache, GPU CLIP only (CPU Whisper)

| Step            | CPU Only (est.) | RTX 3090    |
|-----------------|-----------------|-------------|
| Frame extraction| ~3m 51s         | 3m 51s      |
| Audio (Whisper) | ~16m 4s         | 16m 4s      |
| CLIP embedding  | ~4.5 hours      | **1m 57s**  |
| Qdrant upsert   | 4.7s            | 4.7s        |
| **Total**       | **~5 hours**    | **21m 58s** |

---

## Video 3 — 4,769 frames, no cache, GPU CLIP + GPU Whisper

| Step            | CPU Only (est.) | RTX 3090    |
|-----------------|-----------------|-------------|
| Frame extraction| ~6m 39s         | 6m 39s      |
| Audio (Whisper) | ~14m            | **2m 28s**  |
| CLIP embedding  | ~4h             | **2m 54s**  |
| Qdrant upsert   | 3.7s            | 3.7s        |
| **Total**       | **~4.5 hours**  | **12m 5s**  |

---

## Video 4 — 5,760 frames, cache HIT, GPU CLIP + GPU Whisper

| Step            | CPU Only (est.) | RTX 3090    |
|-----------------|-----------------|-------------|
| Frame extraction| ~9m             | 0s (cached) |
| Audio (Whisper) | ~25m            | **9m 40s**  |
| CLIP embedding  | ~5 hours        | **3m 24s**  |
| Qdrant upsert   | 6.1s            | 6.1s        |
| **Total**       | **~5.5 hours**  | **13m 11s** |

> Note: Video 4 audio longer (167 segments vs 17 in Video 3) explaining higher Whisper time. CLIP slower with Whisper sharing GPU.

---

## Video 5 — 4,851 frames, no cache, GPU CLIP + GPU Whisper

| Step            | CPU Only (est.) | RTX 3090    |
|-----------------|-----------------|-------------|
| Frame extraction| ~6m 50s         | 6m 50s      |
| Audio (Whisper) | ~20m            | **7m 5s**   |
| CLIP embedding  | ~4h             | **3m 27s**  |
| Qdrant upsert   | 3.9s            | 3.9s        |
| **Total**       | **~4.5 hours**  | **17m 27s** |

---

## Embedding Throughput

| Hardware                  | Batches/sec | Seconds/batch |
|---------------------------|-------------|---------------|
| Ryzen 7900X CPU           | ~0.002      | ~476s         |
| RTX 3090 (CLIP only)      | ~0.33       | 3.06s         |
| RTX 3090 (CLIP + Whisper) | ~0.11       | 9s            |

> Single-worker GPU throughput (CLIP only): ~3.44 batches/sec (0.29s/batch) = **~1,600x vs CPU**.

---

## Key Takeaways
- CLIP embedding was 90% of total pipeline time on CPU
- GPU CLIP alone: ~14x overall speedup
- GPU CLIP + Whisper: audio drops 6-7x, total ~20x+ speedup vs CPU
- Frame cache eliminates re-extraction cost on reruns
- RTX 3090 (used, ~$895) is the critical upgrade for this pipeline
- Docker workers remain CPU-only (no GPU passthrough on Windows Docker Desktop)
- Windows native Celery worker required for GPU access on Windows
- GPU contention between CLIP and Whisper trades CLIP speed for audio speed — net positive

## Models
- **Visual**: `sentence-transformers/clip-ViT-L-14` (768-dim vectors)
- **Audio**: faster-whisper (tiny) + pyannote VAD + AST event classifier
- **Vector DB**: Qdrant v1.17
