# Semantic-Media-Pipeline 📂🤖
**Internal Codename: Lumen**

![CI](https://github.com/odanree/semantic-media-pipeline/actions/workflows/ci.yml/badge.svg?branch=main)
![Version](https://img.shields.io/badge/version-v1.6.0-blue)
![Tests](https://img.shields.io/badge/tests-140%20passing-brightgreen)
![Backend Coverage](https://img.shields.io/badge/backend%20coverage-78%25-green)
![Frontend Coverage](https://img.shields.io/badge/frontend%20coverage-70%25-green)

> See [CHANGELOG.md](CHANGELOG.md) for full release history.

A distributed, multimodal ingestion engine designed to semantically index and cluster massive personal media archives (500GB+). It unifies photos and videos into a single searchable vector space using **CLIP embeddings**, **Celery**, and **Qdrant**.

```mermaid
flowchart LR
    subgraph Ingest["Ingestion (Celery Workers)"]
        direction TB
        P[📷 Photos\nJPEG / PNG] --> CE[CLIP Encoder]
        V[🎬 Videos\nMP4 / MOV] --> FF[ffmpeg\nFrame Extraction] --> CE
    end

    subgraph Storage["Storage"]
        direction TB
        M[MinIO / R2\nRaw Media] 
        Q[(Qdrant\nVector DB)]
        PG[(PostgreSQL\nMetadata)]
    end

    subgraph API["API + Frontend"]
        direction TB
        FA[FastAPI] --> NX[Next.js\nSearch UI]
    end

    CE -->|768-dim float32 vectors| Q
    CE -->|metadata + file path| PG
    P & V --> M
    Q & PG --> FA
```

## 🌟 The Vision
In the era of 4K Pixel cameras and high-capacity storage, manual organization is a bottleneck. **Semantic-Media-Pipeline** treats your 500GB+ backup not as a file tree, but as a **high-dimensional knowledge base**. 

Instead of searching by filename, you search by **intent**:
* *"Progress on the home ADU construction in Orange"*
* *"Our family trip to Vietnam in late 2025"*
* *"My son playing with his Labubu toys"*

---

## 📋 About

A distributed, multimodal ingestion engine that transforms massive personal media archives (500GB+) into a searchable semantic knowledge base.

### Key Features
- **Cross-Modal Search**: Query 4K photos and videos with natural language — images and videos share the same vector space using CLIP embeddings
- **Semantic Thumbnails**: Video grid shows the exact frame that matched your CLIP query, not a static keyframe — timestamp comes from Qdrant payloads
- **Dual Storage Backends**: `local` mode mounts a filesystem volume; `s3` mode uses any S3-compatible store (Cloudflare R2, AWS S3, MinIO). Stream and thumbnail endpoints issue presigned-URL redirects so the browser downloads directly from the object store — zero proxying cost
- **Proxy Sidecar**: Worker auto-generates 720p H.264/AAC faststart proxy files on ingest; browser streams the proxy by default for instant playback, with a one-click **[View Original]** toggle to stream the 4K source
- **Idempotent Processing**: File hashing ensures the 500GB library has zero processing cost on re-runs
- **Horizontally Scalable**: Add more Celery workers to decrease total indexing time linearly
- **Real-Time Monitoring**: WebSocket-based dashboard with live ingest progress and processing statistics
- **Cloud Deployable**: One-shot deploy script targets Hetzner CAX21 (ARM64); Caddy handles TLS and reverse-proxy automatically

### Tech Stack
| Layer | Technology |
|-------|-----------|
| **ML & Vision** | Sentence-Transformers CLIP (ViT-L-14, 768-dim), FFmpeg, Pillow |
| **Async Queue** | Celery + Redis — concurrency=4, prefetch-multiplier=1, max_tasks_per_child=50 |
| **Vector Storage** | Qdrant (768-dim HNSW indexing) |
| **Relational DB** | PostgreSQL (metadata, file tracking, observability columns) |
| **Frontend** | Next.js 15 (App Router), React 18, Tailwind CSS |
| **WebSocket Real-Time** | Starlette ASGI, Next.js API routes |
| **Object Storage** | Cloudflare R2 (cloud) / local volume (dev) — boto3 S3-compatible client |
| **Containerization** | Docker Compose, multi-stage builds |
| **TLS & Reverse Proxy** | Caddy (auto HTTPS via Let's Encrypt) |
| **Video Delivery** | Presigned-URL redirect (S3) or 4MB-chunk streaming (local), Range/206 support |

---

## 🏗️ System Architecture

Lumen is a **service-oriented monolith** split across three containers (`api`, `worker`, `frontend`) that share a single PostgreSQL database and communicate via a Redis task queue. This is intentionally not microservices — there are no independent service contracts or per-service databases. The split exists purely to isolate CPU/GPU-heavy ML processing from the HTTP API, not to create service boundaries.

The system is built on a "Producer-Consumer" architecture to ensure that processing 1,000s of high-resolution files doesn't overwhelm the host system.

### 1. Ingestion Layer (Python/FFmpeg)
* **Media Discovery:** Uses `os.scandir` for O(n) file discovery across nested backup directories.
* **Temporal Sampling:** Videos are intelligently sampled (1 frame per 2 seconds) to capture scene changes without redundant compute.
* **Standardization:** HEIC and specialized Pixel formats are normalized for the ML backbone.

### 2. Processing Layer (Distributed Workers)
* **Task Queue:** **Redis + Celery** manages the workload. If a worker hits a corrupted 1GB video, it fails gracefully while other workers continue.
* **Vectorization:** **CLIP (ViT-L-14)** maps both images and video frames into the same 768-dimension latent space.
* **GPU Acceleration:** Full support for NVIDIA CUDA, Apple MPS, and DirectML (Windows) — auto-detected at worker startup.

### 3. Storage & Retrieval
* **Vector DB:** **Qdrant** stores 768-dim embeddings with HNSW indexing.
* **Object Storage:** Cloudflare R2 in production (S3-compatible, no egress fees); local volume mount in dev. The worker writes to whichever backend is configured via `STORAGE_BACKEND`.
* **Metadata:** PostgreSQL maintains the mapping between vectors, file paths, and observability metrics (`embedding_ms`, `worker_id`, `frame_cache_hit`, `model_version`).

[Image of a vector database search mechanism using cosine similarity to match text queries with image embeddings]

---

## ⚙️ Design Rationale

### Why a Distributed Architecture?
**Scaling personal media to 500GB+ requires isolation:** Processing a single corrupted 1GB video should not block indexing the rest of the library. Celery + Redis separates the ingestion dispatcher (API) from compute-heavy workers, allowing graceful degradation and horizontal scaling. Each worker can fail independently while others continue.

### Why Not Microservices?
**Pragmatism over pattern-matching:** `api` and `worker` share the same PostgreSQL schema directly — there is no service contract between them. Splitting into true microservices (separate DBs, versioned HTTP/gRPC interfaces per capability) would add significant operational overhead with no benefit at this scale and team size. The worker/queue pattern gives the only meaningful isolation needed: keeping ML inference off the API process.

### Why CLIP + Qdrant?
**Unified semantic space:** By embedding both images and video frames into the same 768-dimensional CLIP space, a single semantic query ("my family vacation") returns both photos and video scenes. This cross-modal retrieval is impossible with traditional image tagging or keyword search.

### Why File Hashing?
**Zero re-processing cost:** The 500GB library is stable — files don't change. A SHA-256 hash of video headers (8KB, ~1ms) acts as a unique fingerprint. Re-running the ingest pipeline on the same library is idempotent: it walks the filesystem, computes hashes, finds exact matches in PostgreSQL, and skips already-indexed files. Full erasure and re-index takes hours; incremental updates take seconds.

### Why WebSocket + Real-Time Dashboard?
**Transparency during long-running jobs:** Ingesting 500GB takes hours. A WebSocket connection from the frontend to the API streams live ingest progress, frame counts, and vector counts. No polling, no stale UI — the dashboard updates as workers process files in real-time.

---

## � Lumen Internal Architecture

To maintain senior-level code organization, this project uses **Lumen** as the internal codename and applies it consistently across infrastructure components:

### Container & Network Naming
* **Docker Network:** `lumen-net` - Unified internal communication fabric across all services
* **Service Container Names:**
  - `lumen-redis` - Message queue broker
  - `lumen-postgres` - Metadata store
  - `lumen-qdrant` - Vector database
  - `lumen-worker-*` - Distributed GPU/CPU workers
  - `lumen-api` - FastAPI backend
  - `lumen-frontend` - Next.js dashboard
  - `lumen-flower` - Celery monitoring

### Python Package Structure
* **`worker/`** - Core ML pipeline package (historically named; consider as `lumen_core` internally)
  - `celery_app.py` - Celery application factory
  - `tasks.py` - Distributed task definitions
  - `ingest/` - Media discovery & preprocessing (part of lumen_core)
  - `ml/` - CLIP embedder & ML inference (part of lumen_core)
  - `storage/` - Storage backend abstraction (part of lumen_core)
  - `db/` - SQLAlchemy ORM & session management (part of lumen_core)

### Kubernetes Namespace
* **Namespace:** `lumen` - Production cloud deployment isolation
* **Helm Context:** All Kubernetes manifests and Helm charts reference `lumen` namespace

---

## 🧪 Testing

The API test suite runs in-process with no live services — all Qdrant, PostgreSQL, Redis, and CLIP dependencies are mocked.

### Run tests
```bash
# Install test dependencies (no torch/sentence-transformers required)
pip install -r api/requirements-noml.txt
pip install -r api/requirements-test.txt

# Run all tests with coverage
pytest --cov=api --cov-report=term-missing -q
```

### Coverage summary

| File | Coverage |
|------|----------|
| `auth.py` | 94% |
| `routers/health.py` | 85% |
| `routers/ingest.py` | 70% |
| `routers/search.py` | 72% |
| `routers/stats.py` | 89% |
| `utils.py` | 100% |
| **Total** | **78%** |

### Test file breakdown

| File | Tests | What it covers |
|------|-------|----------------|
| `test_health.py` | 10 | `/api/health`, `/` root, Qdrant degraded-graceful |
| `test_search.py` | 20 | Input validation, response schema, result schema, parameter forwarding |
| `test_search_vector.py` | 14 | `POST /search-vector` body/schema/params, CLIP load failure → 503 |
| `test_stats.py` | 15 | `/stats/summary`, `/stats/collection`, topic tags fallback |
| `test_stats_processing.py` | 21 | `/stats/processing`, session detection algorithm, `_compute_topic_tags` CLIP path |
| `test_ingest.py` | 14 | `POST /ingest`, task status GET, Celery task dispatch |
| `test_ingest_media.py` | 13 | Stream/thumbnail placeholder safety, S3 redirect, access-denied, ffmpeg-absent |
| `test_auth.py` | 7 | Auth disabled, correct/wrong/missing key, unconfigured 503 |
| `test_utils.py` | 11 | `get_env_bool/int/float` all branches |

CI enforces `--cov-fail-under=77`. The deploy workflow only fires when CI passes.

---

## ⚙️ Getting Started

### Prerequisites
- Docker Desktop with WSL2 backend (Windows) or Docker Engine (Linux/macOS)
- 16 GB+ RAM recommended (CLIP model ~600 MB  worker concurrency)
- A local media library of photos/videos to index

### 1. Clone
```bash
git clone https://github.com/odanree/semantic-media-pipeline.git
cd semantic-media-pipeline
```

### 2. Configure environment
Copy the example and fill in the required values:
```bash
cp .env.example .env
```

Key variables to set in `.env`:
```env
# Absolute path to your local media library (photos + videos)
MEDIA_SOURCE_PATH=/path/to/your/media

# PostgreSQL credentials
DATABASE_USER=lumen_user
DATABASE_PASSWORD=your_secure_password
DATABASE_NAME=lumen
DATABASE_URL=postgresql://lumen_user:your_secure_password@lumen-postgres:5432/lumen
DATABASE_ASYNC_URL=postgresql+asyncpg://lumen_user:your_secure_password@lumen-postgres:5432/lumen

# Worker tuning  start conservative, increase if RAM allows
# Rule of thumb: floor(free_RAM_GB / 2), max tested: 6
CELERY_CONCURRENCY=4
```

### 3. Build and start
```bash
docker compose up -d --build
```

First startup takes a few minutes  the worker downloads the CLIP ViT-B-32
model (~350 MB) on first run. Watch progress with:
```bash
docker logs lumen-worker -f
# Ready when you see: " CLIP embedder loaded successfully"
```

### 4. Verify services are healthy
```bash
docker compose ps
# All services should show "Up" or "Up (healthy)"
```

### 5. Trigger ingest
Open [http://localhost:3000](http://localhost:3000) and click **Start Ingest**,
or call the API directly:
```bash
curl -X POST http://localhost:8000/api/ingest/crawl \
  -H "Content-Type: application/json" \
  -d '{"path": "/mnt/source"}'
```

Monitor task progress at [http://localhost:5555](http://localhost:5555) (Flower).

### 6. Search your media
Once ingest completes, open [http://localhost:3000](http://localhost:3000) and
type a natural-language query — e.g. *"sunset at the beach"* or *"kids playing
in the backyard"*. Results show the semantically closest video frame or photo,
with thumbnails pinned to the exact matched timestamp.

---

## ☁️ Cloud Deployment

A one-shot deploy script provisions Lumen on a Hetzner CAX21 (ARM64, 4 vCPU / 8 GB, ~€5.39/mo).

### Prerequisites
- Hetzner server running Ubuntu 24.04
- Domain managed via Cloudflare (set SSL mode to **Full**, enable WebSockets)
- Cloudflare R2 bucket with credentials
- GitHub Actions secrets: `SSH_HOST`, `SSH_USER`, `SSH_PRIVATE_KEY`

### Deploy
```bash
# Run once on a fresh server (installs Docker, Caddy, swap, clones repo)
bash <(curl -s https://raw.githubusercontent.com/odanree/semantic-media-pipeline/main/scripts/deploy-cloud.sh)
```

### Required `.env` additions for S3/R2
```env
STORAGE_BACKEND=s3
S3_ENDPOINT_URL=https://<account>.r2.cloudflarestorage.com
S3_BUCKET=your-bucket-name
S3_ACCESS_KEY=your-access-key
S3_SECRET_KEY=your-secret-key
S3_REGION=auto

NEXT_PUBLIC_API_URL=https://your-domain.com
NEXT_PUBLIC_STREAM_URL=https://your-domain.com

# ARM64 / CPU-only server — no GPU
EMBEDDING_DEVICE=cpu
CELERY_CONCURRENCY=4
```

### GitHub Actions auto-deploy
Pushing to `main` automatically SSH-deploys to the server, rebuilding only the services whose source directories changed (`api/`, `worker/`, `frontend/`).

## ⚡ Real-World Performance

Measured on a **Hetzner CAX21** (4 vCPU ARM64, 8 GB RAM, CPU-only — no GPU), `CELERY_CONCURRENCY=4`.

| Run | Files | Wall-clock | Throughput |
|---|---|---|---|
| Session 1 | 498 | 3 h 28 min | ~2.4 files / min |
| Session 2 | 446 | 2 h 24 min | ~3.1 files / min |
| **Combined** | **944** | **5 h 52 min** | **~2.7 files / min avg** |

| Metric | Value |
|---|---|
| Total vectors in Qdrant | 944 |
| Monthly server cost | ~€5.39 / mo |

The bottleneck is CLIP inference (`clip-ViT-L-14`) running on CPU with 4 concurrent Celery workers. Each worker independently encodes a file's frames/thumbnail → 768-dim vector → upserts to Qdrant. No batching across workers; throughput scales roughly linearly with `CELERY_CONCURRENCY` up to ~8 on this instance class.
