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

## 🛠️ Tech Stack
| Component | Technology | Internal Reference |
| :--- | :--- | :--- |
| **Frontend** | Next.js 15 (App Router), Tailwind CSS | `lumen-frontend` |
| **Backend Logic** | Python 3.10, FastAPI | `lumen-api` |
| **Worker Queue** | Celery, Redis | `lumen-worker` / `lumen-redis` |
| **ML Models** | Sentence-Transformers (CLIP), FFmpeg | `lumen_core` |
| **Database** | Qdrant (Vector), PostgreSQL (Metadata) | `lumen-qdrant` / `lumen-postgres` |
| **DevOps** | Docker Compose, NVIDIA Container Toolkit | `lumen-net` (Docker network) |

---

## 🚀 Key Engineering Highlights
* **Cross-Modal Retrieval:** Because images and videos share a vector space, a single search query returns both file types seamlessly.
* **Idempotent Processing:** Uses file hashing to ensure that re-running the pipeline on the same 500GB library costs 0 in compute.
* **Scalability:** The architecture is "horizontally scalable"—add more Docker workers to decrease total indexing time.
* **Streaming UI:** The Next.js dashboard uses HLS/Range requests to preview 4K videos directly from the local filesystem without full-file downloads.

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

## �🚦 Getting Started

1.  **Clone & Build:**
    ```bash
    git clone [https://github.com/your-username/Semantic-Media-Pipeline.git](https://github.com/your-username/Semantic-Media-Pipeline.git)
    docker-compose up -d --build
    ```
2.  **Mount Data:** Map your local Pixel backup folder to `/app/back