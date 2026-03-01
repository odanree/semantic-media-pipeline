# Lumen Internal Architecture & Branding Guide

**Project:** Semantic-Media-Pipeline  
**Internal Codename:** Lumen  
**Version:** 1.0.0  
**Last Updated:** February 2026

---

## 📋 Overview

This document defines the internal naming conventions, architecture, and branding guidelines for the **Lumen** semantic media indexing pipeline. It ensures senior-level code organization and consistency across all deployment contexts (local Docker Compose, Kubernetes production, and cloud environments).

---

## 🏛️ Naming Conventions

### 1. Repository & Package Naming

| Entity | Pattern | Example | Context |
|--------|---------|---------|---------|
| **Repository** | External name | `semantic-media-pipeline` | GitHub, GitLab (external visibility) |
| **Internal Codename** | Single word | `lumen` | Documentation, internal references, architecture |
| **Python Packages** | `lumen_*` (snake_case) | `lumen_core`, `lumen_worker` | Import statements, package namespaces |
| **Kubernetes Namespace** | `lumen` (lowercase) | `lumen` | K8s `--namespace lumen` |
| **Helm Release Names** | `lumen-*` (kebab-case) | `lumen-api`, `lumen-worker` | Helm deployments |

### 2. Docker & Container Naming

#### Container Names (Docker Compose)
All containers follow the `lumen-<service>` pattern:

```text
lumen-redis         ← Message queue (Celery broker)
lumen-postgres      ← Metadata database (PostgreSQL)
lumen-qdrant        ← Vector database (Qdrant)
lumen-worker        ← ML processing workers (Celery)
lumen-api           ← Backend API (FastAPI)
lumen-frontend      ← Frontend dashboard (Next.js)
lumen-flower        ← Celery monitoring (Flower)
lumen-prometheus    ← Metrics collection (Prometheus)
lumen-grafana       ← Metrics visualization (Grafana)
```

#### Docker Network Name
```text
lumen-net           ← Internal communication fabric
```
*Note: Hyphenated for consistency with Docker/Kubernetes conventions*

#### Environment Variables & Configuration
- Database Name: `lumen` (PostgreSQL database)
- Database User: `lumen_user` (PostgreSQL user)
- Redis Database: `0` (default, accessible via broker URL)

**Example Connections:**
```bash
# Service-to-service communication (Docker Compose)
CELERY_BROKER_URL=redis://lumen-redis:6379/0
DATABASE_URL=postgresql://lumen_user:password@lumen-postgres:5432/lumen
QDRANT_HOST=lumen-qdrant
QDRANT_PORT=6333

# From frontend to API
NEXT_PUBLIC_API_URL=http://lumen-api:8000
```

### 3. Kubernetes Resource Naming

#### Namespace
```yaml
metadata:
  namespace: lumen        # All resources live in this namespace
```

#### Deployment Names
```text
lumen-api           ← FastAPI backend deployment
lumen-frontend      ← Next.js frontend deployment
lumen-worker        ← Celery worker deployment (GPU)
lumen-worker-cpu    ← Celery worker deployment (CPU fallback)
lumen-flower        ← Flower monitoring deployment
```

#### Service Names
```text
lumen-api-service      ← Backend API ClusterIP service
lumen-frontend-service ← Frontend ClusterIP service
lumen-flower-service   ← Flower monitoring service
```

#### Ingress Names
```text
lumen-api-ingress      ← API ingress (api.lumen.example.com)
lumen-frontend-ingress ← Frontend ingress (lumen.example.com)
```

#### ConfigMap & Secret Names
```text
lumen-config       ← Main application configuration
lumen-secrets      ← Credentials (database, S3, etc.)
lumen-initdb       ← PostgreSQL initialization schema
```

#### Persistent Volume Names
```text
lumen-media-data   ← Media library (500Gi)
lumen-worker-tmp   ← Temporary frame storage (50Gi)
lumen-model-cache  ← CLIP model cache (10Gi)
```

---

## 🏗️ Python Package Structure

The worker codebase is organized under the `worker/` directory, which logically represents the `lumen_core` package in external documentation:

```
worker/                           # Root worker service
├── __init__.py                   # Package declaration
├── celery_app.py                 # Celery application factory
├── tasks.py                      # Distributed task definitions
│
├── ingest/                       # Media discovery & preprocessing
│   ├── __init__.py
│   ├── crawler.py                # File discovery (O(n) via os.scandir)
│   ├── hasher.py                 # SHA-256 idempotency fingerprinting
│   └── ffmpeg.py                 # FFmpeg integration (keyframes, HEIC)
│
├── ml/                           # ML pipeline components
│   ├── __init__.py
│   └── embedder.py               # OpenAI CLIP vectorization
│
├── storage/                      # Storage backend abstraction
│   ├── __init__.py
│   ├── base.py                   # Abstract StorageBackend interface
│   ├── local.py                  # Local filesystem implementation
│   ├── s3.py                     # AWS S3 / MinIO implementation
│   └── gcs.py                    # Google Cloud Storage implementation
│
├── db/                           # ORM & database layer
│   ├── __init__.py
│   ├── models.py                 # SQLAlchemy ORM (MediaFile entity)
│   └── session.py                # Session factories (sync & async)
│
└── requirements.txt              # Python dependencies
```

### Import Pattern (from external services)
When importing from the worker package in FastAPI or other services:
```python
# Direct imports from physical location
from worker.tasks import crawl_and_dispatch, ingest_media
from worker.ml.embedder import Embedder
from worker.db.models import MediaFile

# Or, conceptually reference as:
# from lumen_core.tasks import ...          [logical reference in docs]
# from lumen_core.ml.embedder import ...    [logical reference in docs]
```

---

## 🌐 Environment Variables

All Lumen components read configuration from a unified `.env` file:

```bash
# Service Naming
SERVICE_NAME=lumen              # Application identifier

# Redis/Celery
CELERY_BROKER_URL=redis://lumen-redis:6379/0
CELERY_RESULT_BACKEND=redis://lumen-redis:6379/0

# PostgreSQL
DATABASE_USER=lumen_user
DATABASE_PASSWORD=<secure_password>
DATABASE_NAME=lumen
DATABASE_HOST=lumen-postgres
DATABASE_PORT=5432
DATABASE_URL=postgresql://lumen_user:<password>@lumen-postgres:5432/lumen

# Qdrant Vector Database
QDRANT_HOST=lumen-qdrant
QDRANT_PORT=6333
QDRANT_GRPC_PORT=6334
QDRANT_COLLECTION_NAME=lumen_embeddings

# CLIP Model Configuration
EMBEDDER_MODEL=sentence-transformers/clip-ViT-B-32
EMBEDDING_DIMENSION=512
EMBEDDING_BATCH_SIZE=32
EMBEDDING_DEVICE=cuda          # or 'cpu' for CPU fallback

# Media Storage
MEDIA_ROOT=/data/media
STORAGE_BACKEND=local           # or 's3', 'gcs'

# Kubernetes-specific
LUMEN_NAMESPACE=lumen           # K8s namespace
LUMEN_ENV=production            # or 'development', 'staging'
```

---

## 🚀 Deployment Contexts

### Context 1: Local Development (Docker Compose)

**Command:**
```bash
docker-compose up -d --build
```

**Network Access:**
- API: `http://localhost:8000`
- Frontend: `http://localhost:3000`
- Flower: `http://localhost:5555`
- PostgreSQL: `localhost:5432` (lumen_user)
- Qdrant: `http://localhost:6333`
- Redis: `localhost:6379`

**Service Discovery:**
- Services communicate via DNS: `lumen-api`, `lumen-postgres`, etc.

---

### Context 2: Production (Kubernetes)

**Namespace:**
```bash
kubectl create namespace lumen
```

**Service Discovery:**
- Within cluster: `<service>.<namespace>.svc.cluster.local`
- Example: `lumen-api.lumen.svc.cluster.local:8000`

**Ingress:**
```yaml
# Internal DNS
api.lumen.example.com     → lumen-api-service
lumen.example.example.com → lumen-frontend-service
```

**Resource Allocation:**
- **Worker (GPU):** `requests: {cpu: 1, memory: 4Gi, nvidia.com/gpu: 1}`
- **API:** `requests: {cpu: 500m, memory: 2Gi}`
- **Frontend:** `requests: {cpu: 200m, memory: 256Mi}`

---

## 📊 Monitoring & Observability

### Celery Task Monitoring
```bash
# Access Flower UI
kubectl port-forward -n lumen svc/lumen-flower 5555:5555
# Then visit: http://localhost:5555
```

### Metrics Collection (Prometheus)
```bash
# Docker Compose profile
docker-compose --profile monitoring up

# Kubernetes
kubectl port-forward -n lumen svc/lumen-prometheus 9090:9090
```

### Log Aggregation
```bash
# View worker logs
docker logs lumen-worker -f

# On Kubernetes
kubectl logs -n lumen deployment/lumen-worker -f
```

---

## 🔐 Security Best Practices

### Secrets Management
1. **Never commit** `.env` files with real credentials
2. **Kubernetes:** Use external secret managers (HashiCorp Vault, AWS Secrets Manager)
3. **Development:** Use `.env.example` as template; fill with local values
4. **Production:** Base64-encode secrets in `secrets.yaml` only for demos; use operator in production

### Network Policies (Kubernetes)
All inter-service communication follows least-privilege:
- **Worker:** Egress-only to Redis, PostgreSQL, Qdrant
- **API:** Ingress from frontend; egress to databases
- **Frontend:** Ingress from Ingress Controller; egress to API

---

## 📦 Version Management

### Pinned Versions (High Reliability)
These versions are locked in requirements and Helm charts:
- **Python:** 3.10 (language runtime)
- **Celery:** 5.6.2 (task queue)
- **Redis:** 7.4.8 (message broker)
- **PostgreSQL:** 17.9-alpine (metadata DB)
- **Qdrant:** v1.17.0 (vector DB)
- **CLIP Model:** sentence-transformers/clip-ViT-B-32 (512-dim)
- **FastAPI:** Latest stable (in requirements.txt)
- **Next.js:** 15 (App Router)
- **NVIDIA CUDA:** 12.8.1 (GPU compute)

---

## 🎯 Extending Lumen

### Adding New Workers
```python
# worker/tasks.py
from celery import shared_task

@shared_task(name='lumen.process_custom')
def process_custom_media(file_id):
    """Custom processing task within lumen namespace."""
    pass
```

### Adding Services
1. Create service directory: `services/lumen-newservice/`
2. Follow naming: `lumen-newservice` (Docker), `lumen-newservice` (K8s)
3. Add to docker-compose: Update `docker-compose.yml`
4. Add Kubernetes: Create `k8s/newservice-deployment.yaml`

### Adding Helm Charts
```bash
# Install into lumen namespace
helm install lumen-myservice ./helm/myservice -n lumen
```

---

## 🔗 References

- **Docker Compose:** [docker-compose.yml](docker-compose.yml)
- **Kubernetes Manifests:** [k8s/](k8s/)
- **Implementation Guide:** [IMPLEMENTATION.md](IMPLEMENTATION.md)
- **README:** [README.md](README.md)
- **Environment Variables:** [.env.example](.env.example)

---

## 📝 Changelog

| Date | Version | Change |
|------|---------|--------|
| Feb 2026 | 1.0.0 | Initial Lumen architecture guide |

---

**Curator:** Your Organization  
**License:** [Your License Here]
