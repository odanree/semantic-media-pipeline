# Lumen Implementation Guide

## Quick Start

### Prerequisites
- Docker and Docker Compose (v2+)
- NVIDIA Container Toolkit (for GPU support, optional but recommended)
- At least 10GB free disk space for containers
- 20GB+ free disk space for media library

### Step 1: Clone and Setup

```bash
cd c:\Users\Danh\Documents\Projects\Semantic-Media-Pipeline
cp .env.example .env
```

Edit `.env` and configure:
- `MEDIA_ROOT`: Path to your media library (default: `/data/media`)
- `DATABASE_PASSWORD`: Set a secure password
- `STORAGE_BACKEND`: Choose `local`, `s3`, or `gcs`
- `CUDA_VISIBLE_DEVICES`: GPU device IDs (leave empty for auto-detect)

### Step 2: Start Services

```bash
# Build and start all services
docker-compose up -d --build

# Wait for services to be healthy (2-3 minutes)
docker-compose ps
```

Verify all services are healthy:
```bash
curl http://localhost:8000/api/health
```

### Step 3: Initialize Media Library

```bash
# Start ingestion of your media
curl -X POST http://localhost:8000/api/ingest \
  -H "Content-Type: application/json" \
  -d '{"media_root": "/data/media"}'
```

### Step 4: Monitor Progress

- **Flower (Task Monitor)**: http://localhost:5555
  - Watch tasks complete in real-time
  - See worker utilization

- **Frontend (Search UI)**: http://localhost:3000
  - Status panel shows processing progress
  - Search bar appears once indexing begins

- **Prometheus Metrics** (optional): http://localhost:9090
  ```bash
  docker-compose --profile monitoring up -d
  ```

- **Grafana Dashboard** (optional): http://localhost:3001
  ```bash
  docker-compose --profile monitoring up -d
  ```

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────┐
│                    Your Media Library                       │
│                   (500GB+ Photos/Videos)                    │
└──────────────────────────┬──────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────┐
│                  Ingestion Layer                            │
│  ┌───────────────────────────────────────────────────────┐  │
│  │ Crawler: os.scandir discovery                         │  │
│  │ Hasher: SHA-256 idempotence (skip duplicates)         │  │
│  │ FFmpeg: Frame extraction (1 frame/2s)                 │  │
│  └───────────────────────────────────────────────────────┘  │
└──────────────────────────┬──────────────────────────────────┘
                           │
        ┌──────────────────┼──────────────────┐
        │                  │                  │
        ▼                  ▼                  ▼
    ┌────────┐         ┌────────┐         ┌────────┐
    │ Worker │         │ Worker │         │ Worker │
    │  #1    │         │  #2    │         │  #3    │
    │        │         │        │         │        │
    │ CLIP   │         │ CLIP   │         │ CLIP   │
    │ GPU    │         │ GPU    │         │ GPU    │
    └───┬────┘         └───┬────┘         └───┬────┘
        │                  │                  │
        └──────────────────┼──────────────────┘
                           │
                ┌──────────┴──────────┐
                │                     │
                ▼                     ▼
            ┌──────────┐         ┌──────────────┐
            │ Qdrant   │         │ PostgreSQL   │
            │ 512-dim  │         │ Metadata     │
            │ Vectors  │         │ & Hashes     │
            └────┬─────┘         └──────────────┘
                 │
        ┌────────┴────────┐
        │                 │
        ▼                 ▼
   ┌────────────┐    ┌────────────┐
   │ Next.js    │    │ Search API │
   │ Dashboard  │    │ (FastAPI)  │
   └────────────┘    └────────────┘
```

## Key Components

### 1. **Celery Worker** (`worker/`)
- Distributed task queue
- Exponential backoff retry logic
- Batch CLIP inference (32 frames at a time)
- Cold storage streaming (no full-file loads)
- Supports GPU + CPU fallback

**Key files:**
- `tasks.py` - Task definitions
- `celery_app.py` - Celery configuration
- `ingest/` - Crawler, hasher, FFmpeg wrapper
- `ml/embedder.py` - CLIP ViT-B-32 wrapper
- `storage/` - Local/S3/GCS abstraction

### 2. **FastAPI Backend** (`api/`)
- Health check and status endpoints
- Qdrant search integration
- Text embedding endpoint
- Task status monitoring

**Key files:**
- `main.py` - FastAPI app
- `routers/` - API endpoints
- `routers/search.py` - Semantic search

### 3. **Next.js Frontend** (`frontend/`)
- Modern search dashboard
- Real-time status monitoring
- Video player with Range requests
- Tailwind CSS styling

**Key files:**
- `app/page.tsx` - Main search page
- `components/` - React components
- `app/api/stream/[id]/route.ts` - Range-request streaming

### 4. **Infrastructure** (Docker Compose)
- Redis (Celery broker)
- PostgreSQL (metadata)
- Qdrant (vector DB)
- Flower (task monitoring)
- Prometheus + Grafana (optional)

## Performance Tuning

### Scale Horizontally
```bash
# Run 4 worker instances
docker-compose up -d --scale worker=4
```

### Adjust Batch Size
In `.env`:
```
EMBEDDING_BATCH_SIZE=64  # Larger batch = more GPU utilization
```

### Adjust Frame Sampling
```
KEYFRAME_FPS=0.33  # 1 frame per 3 seconds (lower=fewer frames, faster)
KEYFRAME_FPS=1.0   # 1 frame per second (higher=more detail, slower)
```

### Monitor GPU Usage
```bash
# Inside worker container
docker exec lumen-worker nvidia-smi
```

## Troubleshooting

### No tasks being created
```bash
# Check if crawler is running
curl -X POST http://localhost:8000/api/ingest \
  -H "Content-Type: application/json" \
  -d '{"media_root": "/data/media"}'

# Check Flower for task details
# Open http://localhost:5555
```

### Out of memory errors
- Reduce `EMBEDDING_BATCH_SIZE` in `.env`
- Scale down to 1 worker
- Check for stuck tasks: `docker logs lumen-worker`

### GPU not being used
- Verify NVIDIA Container Toolkit: `docker run --rm --gpus all nvidia/cuda:12.8.1-runtime-ubuntu22.04 nvidia-smi`
- Check logs: `docker logs lumen-worker`
- Fall back to CPU: Set `USE_GPU=false` in build args

### Qdrant connection errors
```bash
# Test Qdrant health
curl http://localhost:6333/health

# Check collection size
curl http://localhost:6333/collections
```

## API Endpoints

### Health
```bash
GET /api/health
```

### System Status
```bash
GET /api/status
```

### Start Ingestion
```bash
POST /api/ingest
Content-Type: application/json

{
  "media_root": "/data/media"
}
```

### Search
```bash
POST /api/search
Content-Type: application/json

{
  "query": "family trip to Vietnam",
  "limit": 20,
  "threshold": 0.3
}
```

### Embed Text
```bash
POST /api/embed-text
Content-Type: application/json

{
  "query": "baby playing with toys"
}
```

## Storage Backends

### Local (Default)
```env
STORAGE_BACKEND=local
MEDIA_ROOT=/data/media
```

### S3 / MinIO
```env
STORAGE_BACKEND=s3
S3_ENDPOINT_URL=http://minio:9000
S3_BUCKET=media
S3_ACCESS_KEY=minioadmin
S3_SECRET_KEY=minioadmin
```

### Google Cloud Storage
```env
STORAGE_BACKEND=gcs
GCS_PROJECT_ID=my-project
GCS_BUCKET=my-bucket
GOOGLE_APPLICATION_CREDENTIALS=/path/to/key.json
```

## Next Steps

### Phase 4: Observability (Optional)
Enable Prometheus + Grafana:
```bash
docker-compose --profile monitoring up -d
```

View metrics on http://localhost:3001 (Grafana, admin/admin)

### Phase 5: Kubernetes Deployment

The `k8s/` directory contains a complete set of production-ready manifests.

#### Quick Deploy

```bash
# One-command deploy (installs all dependencies + Lumen services)
chmod +x k8s/deploy.sh
./k8s/deploy.sh
```

The script automatically:
1. Creates the `lumen` namespace
2. Adds Helm repos (Bitnami, Qdrant, NVIDIA)
3. Installs NVIDIA GPU Operator (if GPU nodes detected)
4. Deploys Redis, PostgreSQL, Qdrant via Helm
5. Applies ConfigMap, Secrets, PVCs
6. Deploys Worker (3 GPU replicas), API (2 replicas), Frontend (2 replicas), Flower
7. Configures HPA autoscaling and NetworkPolicies

#### Manual Step-by-Step Deploy

```bash
# 1. Namespace
kubectl apply -f k8s/namespace.yaml

# 2. Helm dependencies
helm install redis bitnami/redis -n lumen --set auth.enabled=false
helm install postgresql bitnami/postgresql -n lumen \
  --set auth.username=lumen_user --set auth.password=YOUR_PASSWORD --set auth.database=lumen
helm install qdrant qdrant/qdrant -n lumen --set image.tag=v1.17.0

# 3. Config & Secrets
kubectl apply -f k8s/configmap.yaml
kubectl apply -f k8s/secrets.yaml
kubectl apply -f k8s/initdb-configmap.yaml
kubectl apply -f k8s/pvc.yaml

# 4. Application
kubectl apply -f k8s/worker-deployment.yaml
kubectl apply -f k8s/api-deployment.yaml
kubectl apply -f k8s/frontend-deployment.yaml
kubectl apply -f k8s/flower-deployment.yaml

# 5. Autoscaling & Security
kubectl apply -f k8s/hpa.yaml
kubectl apply -f k8s/network-policies.yaml
```

#### Port Forwarding (Local Access)

```bash
kubectl port-forward svc/lumen-api 8000:8000 -n lumen
kubectl port-forward svc/lumen-frontend 3000:3000 -n lumen
kubectl port-forward svc/lumen-flower 5555:5555 -n lumen
```

#### Scaling Workers

```bash
# Manual scaling
kubectl scale deployment/lumen-worker --replicas=5 -n lumen

# HPA automatically scales 1-10 workers based on CPU (70% threshold)
kubectl get hpa -n lumen
```

#### CPU-Only Fallback

If no GPU nodes are available, scale down GPU workers and scale up CPU workers:

```bash
kubectl scale deployment/lumen-worker --replicas=0 -n lumen
kubectl scale deployment/lumen-worker-cpu --replicas=3 -n lumen
```

#### Teardown

```bash
chmod +x k8s/teardown.sh
./k8s/teardown.sh
```

#### Manifest Reference

| File | Contents |
|:---|:---|
| `namespace.yaml` | `lumen` namespace |
| `configmap.yaml` | All non-sensitive config (Redis, Qdrant, CLIP settings) |
| `secrets.yaml` | Database credentials (base64) |
| `pvc.yaml` | PVCs for media (500Gi), temp frames (50Gi), model cache (10Gi) |
| `initdb-configmap.yaml` | PostgreSQL schema init script |
| `worker-deployment.yaml` | GPU worker (3 replicas) + CPU fallback (0 replicas) |
| `api-deployment.yaml` | FastAPI Deployment + Service + Ingress |
| `frontend-deployment.yaml` | Next.js Deployment + Service + Ingress |
| `flower-deployment.yaml` | Celery monitor Deployment + Service |
| `hpa.yaml` | Autoscalers for worker (1-10) and API (2-6) |
| `network-policies.yaml` | Least-privilege pod communication rules |
| `values.yaml` | Helm values for Redis, PostgreSQL, Qdrant, GPU Operator |
| `deploy.sh` | One-command full deployment |
| `teardown.sh` | One-command full removal |

## Development

### Running locally without Docker
```bash
# Setup Python env
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install worker deps
pip install -r worker/requirements.txt

# Run Celery worker
celery -A worker.celery_app worker --loglevel=info

# In another terminal, run FastAPI
cd api
pip install -r requirements.txt
uvicorn main:app --reload --port 8000

# In another terminal, run Next.js
cd frontend
npm install
npm run dev
```

## Support & Issues

- Check logs: `docker-compose logs <service>`
- Monitor tasks: http://localhost:5555 (Flower)
- API health: `curl http://localhost:8000/api/health`
