# Changelog

All notable changes to Semantic-Media-Pipeline (Lumen) are documented here.

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/). Versions follow [Semantic Versioning](https://semver.org/).

---

## [v2.0.0] — 2026-03-11

### Added
- **RAG Ask pipeline** — `/api/ask` retrieves semantically relevant frames, reads captions via local VLM, and returns a grounded answer + source list via GPT-4o-mini
- **Multi-agent coordinator** — LangGraph graph with `classify_intent`, `search_agent`, `metadata_agent`, `vision_agent`, and `aggregate` nodes; dispatches to the right specialist and merges results
- **YOLO object detection** — `/api/detect` accepts a raw image upload, runs YOLOv8n inference, returns bounding boxes and confidence scores
- **Audit middleware** — Every API request is logged to a Postgres `audit_logs` table (endpoint, method, HTTP status, response time in ms)
- **`api/db/models.py` + `api/db/session.py`** — API-side SQLAlchemy models and async session factory, decoupled from the `worker` package
- **`api/ml/yolo_detector.py`** — YOLO inference module accessible from the API container without the worker namespace
- **`scripts/local_backfill_dev.py`** — Caption backfill script that targets local Qdrant directly (no SSH tunnel); works on both Windows and macOS workers
- **Vision captioning backfill** — Ran llava:7b captions on 80,000+ uncaptioned frames across Windows and Mac workers

### Fixed
- **`NotRequired` Python 3.10 import** — Changed `from typing import NotRequired` to `from typing_extensions import NotRequired` in `api/agents/coordinator.py`
- **LangGraph `INVALID_CONCURRENT_GRAPH_UPDATE`** — Each agent node now returns only its own partial state dict instead of `{**state, ...}`, eliminating concurrent update conflicts
- **`worker.*` import paths in API** — All `from worker.db.models`, `from worker.db.session`, and `from worker.ml.yolo_detector` imports replaced with local API-side equivalents

### Changed
- **Qdrant port exposed in dev** — Added `ports: - "6333:6333"` to qdrant service in `docker-compose.yml` for local backfill access
- **Backend test coverage** — 78% → **82%** (335 tests passing)

---

## [v1.7.2] — 2026-03-10

### Fixed
- **Ask endpoint similarity threshold** — Lowered default CLIP similarity threshold from 0.25 → 0.2 to improve recall for marginal semantic matches; aligns Ask with Search endpoint for consistency (PR #56)
- Users can still override with custom `threshold` parameter in request

---

## [v1.7.1] — 2026-03-10

### Fixed
- **Ask panel thumbnails missing in production** — Thumbnail proxy was using `NEXT_PUBLIC_STREAM_URL` (video streaming endpoint) instead of frontend's `/api/thumbnail` proxy, causing broken images in Ask results (PR #55)
- **API route endpoint fallbacks** — Added fallback chain `API_URL || NEXT_PUBLIC_API_URL || 'http://api:8000'` to all 7 Next.js proxy routes for robustness in various deployment contexts (PR #55)

### Changed
- **Frontend test coverage threshold maintained** — Branch coverage increased from 72.97% → 73.24% via comprehensive thumbnail and caption rendering tests (PR #55)

---

## [v1.7.0] — 2026-03-10

### Added
- **Caption backfill task** — `backfill_captions` Celery task iterates all existing Qdrant video-frame points and writes a `caption` field via moondream VLM; idempotent, restartable, dry-run supported
- **Admin trigger endpoint** — `POST /api/admin/backfill-captions` dispatches the backfill and returns a Celery task ID; `GET /api/admin/task/{task_id}` polls progress

### Fixed
- Duplicate `ask.router` include in `api/main.py` removed

---

## [v1.6.0] — 2026-03-10

### Added
- **RAG `/api/ask` endpoint** — natural language Q&A over indexed media; retrieves relevant frames via CLIP then summarises with an LLM (PR #47)
- **Greedy NMS temporal dedup** — Non-Maximum Suppression over 5-second scene windows eliminates near-duplicate video frames from search results (PR #48)
- **Timelapse flood cap** — hard limit on frames returned from a single media file prevents time-lapses from dominating result sets (PR #48)
- **Dedup A/B toggle** — `dedup=false` query param returns raw pre-dedup frames for debugging; dedup checkbox in the Ask UI lets users toggle this at query time (PR #47, #48)
- **Comprehensive frontend test suite** — vitest coverage for all Next.js proxy routes, AskPanel UI, and search logic; quality gate raised to 70% stmts/lines, 73% branches (PR #44, #45, #47)

### Fixed
- Proxy route `frontend/app/api/search/route.ts` was silently dropping the `dedup` field before forwarding to FastAPI
- API key (`X-API-Key`) missing from several server-side Next.js proxy calls (PR #38, #39, #43)
- Missing `/api/thumbnail` Next.js proxy route (PR #42)
- Default similarity threshold lowered from 0.3 → 0.2 for better recall (PR #41)
- Internal services bound to `127.0.0.1` — removed unintentional public port exposure (PR #40)

---

## [v1.5.0] — 2026-03-08

### Added
- **Semantic topic tag extraction** — CLIP-based tag suggestions surfaced in search UI (PR #27)
- **Tag pill search injection** — click a tag to inject it as a search query; filter state persisted in `localStorage` (PR #26)
- **Cloud deploy (CI/CD)** — GitHub Actions SSH deploy to Hetzner CAX21; rebuilds only changed service directories (`api/`, `worker/`, `frontend/`) (PR #19)
- **Cloudflare R2 / S3 object storage** — dual backend: `local` volume for dev, S3-compatible presigned-URL redirect for cloud; zero proxying cost (PR #19)
- **Dual-worker support** — simultaneous Mac + Windows workers over SMB; fast-skip for already-indexed files (PR #18)
- **Proxy sidecar generation decoupled** — 720p H.264/AAC faststart proxy files generated asynchronously, not blocking ingest (PR #12)
- **ViT-L-14 upgrade** — switched from ViT-B-32 (512-dim) to ViT-L-14 (768-dim) on lumen1 stack for higher-quality embeddings (PR #16)
- **Observability columns** — `embedding_started_at`, `worker_id`, `frame_cache_hit`, `embedding_ms`, `model_version` on `media_files` table

### Fixed
- Deploy health check used wrong endpoint; replaced `sleep 8` with 90-second retry loop (PR #25, #30)
- Deploy used `git pull` (fails on dirty worktree); switched to `git reset --hard` (PR #29)
- Thumbnail ORB algorithm fix + worker concurrency tuning (PR #10)
- Container path regression for frame cache (PR #17)
- `shutil.move` for cross-device temp → proxies writes (PR #13)

### Performance
- Validated 944 files indexed across two production runs on Hetzner CAX21 (ARM64, CPU-only): ~2.7 files/min average
- Worker pool optimisations (PR #2)

---

## [v1.1.0] — 2026-03-01

### Added
- **Real-time WebSocket dashboard** — PostgreSQL `LISTEN/NOTIFY` trigger system with two broadcast channels:
  - `media_processing` — status transitions (pending → completed/failed)
  - `vector_indexed` — vector embedding completions
- FastAPI WebSocket endpoints for live streaming to frontend
- React hooks for live dashboard updates with automatic reconnection
- Zero-polling architecture replacing previous polling approach

---

## [v1.0.0] — 2026-03-01

### Added
- **Media ingestion pipeline** — discovers and indexes photos (JPEG/PNG) and videos (MP4/MOV) across nested directories
- **CLIP ViT-B-32 embeddings** — 512-dim vectors stored in Qdrant HNSW index; CPU + DirectML fallback
- **Distributed processing** — Celery + Redis task queue, `concurrency=4`, `max_tasks_per_child=50`
- **Dual storage** — PostgreSQL for metadata/tracking, Qdrant for vector search
- **Video frame extraction** — FFmpeg temporal sampling with adaptive timeout
- **Semantic search API** — FastAPI backend with natural language query support
- **Docker Compose orchestration** — `api`, `worker`, `frontend` containers with shared Redis/PostgreSQL/Qdrant
- Validated against 2,271+ media items

[v1.6.0]: https://github.com/odanree/semantic-media-pipeline/releases/tag/v1.6.0
[v1.5.0]: https://github.com/odanree/semantic-media-pipeline/releases/tag/v1.5.0
[v1.1.0]: https://github.com/odanree/semantic-media-pipeline/releases/tag/v1.1.0
[v1.0.0]: https://github.com/odanree/semantic-media-pipeline/releases/tag/v1.0.0
