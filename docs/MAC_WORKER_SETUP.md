# Mac Worker Setup

Connects the Mac to the Windows PC pipeline over LAN via MinIO. No SMB mount needed.

**Windows PC IP:** `192.168.1.137`

---

## Step 1 — Clone the repo

```bash
git clone https://github.com/odanree/semantic-media-pipeline.git
cd semantic-media-pipeline
git checkout feat/minio-source-storage
```

---

## Step 2 — Verify Windows PC ports are reachable

```bash
curl -s http://192.168.1.137:9000/minio/health/live   # should return 200
curl -s http://192.168.1.137:8000/api/health          # optional — API health
nc -zv 192.168.1.137 6379                             # Redis
nc -zv 192.168.1.137 5432                             # Postgres
nc -zv 192.168.1.137 6333                             # Qdrant
```

---

## Step 3 — Verify MinIO bucket has files

```bash
docker run --rm \
  -e "MC_HOST_lumen=http://minioadmin:minioadmin@192.168.1.137:9000" \
  minio/mc ls lumen/lumen-media
```

You should see the media folders listed (DJI, Pixel 9, etc.).

---

## Step 4 — Create the env file

The `.env.mac-worker` file is gitignored (not in the repo). Create it manually:

```bash
cat > .env.mac-worker << 'EOF'
CELERY_BROKER_URL=redis://192.168.1.137:6379/0
CELERY_RESULT_BACKEND=redis://192.168.1.137:6379/0
DATABASE_URL=postgresql://lumen_user:lumen_secure_password_2026@192.168.1.137:5432/lumen
DATABASE_ASYNC_URL=postgresql+asyncpg://lumen_user:lumen_secure_password_2026@192.168.1.137:5432/lumen
QDRANT_HOST=192.168.1.137
QDRANT_PORT=6333
QDRANT_GRPC_PORT=6334
QDRANT_PREFER_GRPC=true
QDRANT_COLLECTION_NAME=media_vectors
STORAGE_BACKEND=s3
S3_ENDPOINT_URL=http://192.168.1.137:9000
S3_BUCKET=lumen-media
S3_ACCESS_KEY=minioadmin
S3_SECRET_KEY=minioadmin
S3_REGION=us-east-1
WORKER_ID=mac-1
REDIS_URL=redis://192.168.1.137:6379
CLIP_MODEL_NAME=clip-ViT-L-14
EMBEDDING_BATCH_SIZE=32
CELERY_CONCURRENCY=2
CELERY_WORKER_PREFETCH_MULTIPLIER=1
CELERY_MAX_TASKS_PER_CHILD=50
KEYFRAME_FPS=0.5
KEYFRAME_RESOLUTION=224
EOF
```

---

## Step 5 — Start the Mac worker

```bash
docker compose -f docker-compose.mac-worker.yml --env-file .env.mac-worker up --build -d
```

---

## Step 6 — Verify it connected

```bash
docker compose -f docker-compose.mac-worker.yml logs -f
```

You should see:
```
celery@mac-worker ready.
mingle: sync with 1 nodes
```

---

## Step 7 — Trigger an ingest from Mac (optional)

The Windows PC API handles dispatch — you don't need to trigger from Mac. But to test:

```bash
curl -s -X POST http://192.168.1.137:8000/api/ingest \
  -H "Content-Type: application/json" \
  -d '{"media_root": ""}'
```

Empty `media_root` = crawl the entire `lumen-media` bucket via S3.

---

## Troubleshooting

**Worker can't reach Redis/Postgres/Qdrant**
- Check Windows Firewall allows inbound on ports 6379, 5432, 6333, 6334, 9000
- Run Step 2 connectivity checks again

**`S3 object not found` errors in worker logs**
- MinIO bucket may be empty — verify Step 3 shows files
- Check `S3_ENDPOINT_URL` uses the LAN IP, not `lumen-minio` (that hostname only works inside Docker on Windows)

**Worker starts but processes nothing**
- Check Celery can see tasks: `docker compose -f docker-compose.mac-worker.yml exec worker celery -A celery_app inspect active`
