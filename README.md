# Semantic-Media-Pipeline 📂🤖
**Internal Codename: Lumen**

A distributed, multimodal ingestion engine designed to semantically index and cluster massive personal media archives (500GB+). It unifies photos and videos into a single searchable vector space using **CLIP embeddings**, **Celery**, and **Qdrant**.

[Image of a multimodal machine learning pipeline architecture showing image and video ingestion into a shared vector space]

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
- **Proxy Sidecar**: Worker auto-generates 720p H.264/AAC faststart proxy files on ingest; browser streams the proxy by default for instant playback, with a one-click **[View Original]** toggle to stream the 4K source
- **Idempotent Processing**: File hashing ensures the 500GB library has zero processing cost on re-runs
- **Horizontally Scalable**: Add more Celery workers to decrease total indexing time linearly
- **Real-Time Monitoring**: WebSocket-based dashboard with live ingest progress and processing statistics
- **Production-Ready**: Kubernetes deployment manifests and Docker Compose orchestration included

### Tech Stack
| Layer | Technology |
|-------|-----------|
| **ML & Vision** | Sentence-Transformers CLIP (ViT-B-32), FFmpeg, Pillow |
| **Async Queue** | Celery + Redis — concurrency=6, prefetch-multiplier=1, max_tasks_per_child=50 |
| **Vector Storage** | Qdrant (512-dim HNSW indexing) |
| **Relational DB** | PostgreSQL (metadata, file tracking) |
| **Frontend** | Next.js 15 (App Router), React 18, Tailwind CSS |
| **WebSocket Real-Time** | Starlette ASGI, Next.js API routes |
| **Containerization** | Docker Compose, multi-stage builds, Kubernetes |
| **Video Delivery** | 720p H.264 proxy sidecar (`:rw` volume), 4MB streaming chunks, Range/206 support |

---

## 🏗️ System Architecture

The system is built on a "Producer-Consumer" architecture to ensure that processing 1,000s of high-resolution files doesn't overwhelm the host system.

### 1. Ingestion Layer (Python/FFmpeg)
* **Media Discovery:** Uses `os.scandir` for O(n) file discovery across nested backup directories.
* **Temporal Sampling:** Videos are intelligently sampled (1 frame per 2 seconds) to capture scene changes without redundant compute.
* **Standardization:** HEIC and specialized Pixel formats are normalized for the ML backbone.

### 2. Processing Layer (Distributed Workers)
* **Task Queue:** **Redis + Celery** manages the workload. If a worker hits a corrupted 1GB video, it fails gracefully while other workers continue.
* **Vectorization:** **OpenAI CLIP (ViT-B-32)** maps both images and video frames into the same 512-dimension latent space.
* **GPU Acceleration:** Full support for NVIDIA CUDA for 15x faster inference.

### 3. Storage & Retrieval (Vector DB)
* **Database:** **Qdrant** stores the embeddings with HNSW indexing.
* **Metadata:** PostgreSQL maintains the mapping between vectors and original local file paths.

[Image of a vector database search mechanism using cosine similarity to match text queries with image embeddings]

---

## ⚙️ Design Rationale

### Why a Distributed Architecture?
**Scaling personal media to 500GB+ requires isolation:** Processing a single corrupted 1GB video should not block indexing the rest of the library. Celery + Redis separates the ingestion dispatcher (API) from compute-heavy workers, allowing graceful degradation and horizontal scaling. Each worker can fail independently while others continue.

### Why CLIP + Qdrant?
**Unified semantic space:** By embedding both images and video frames into the same 512-dimensional CLIP space, a single semantic query ("my family vacation") returns both photos and video scenes. This cross-modal retrieval is impossible with traditional image tagging or keyword search.

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

##  Getting Started

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
type a natural-language query  e.g. *"sunset at the beach"* or *"kids playing
in the backyard"*. Results show the semantically closest video frame or photo,
with thumbnails pinned to the exact matched timestamp.
