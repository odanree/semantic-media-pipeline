# Project Learnings: Root-Cause Analysis & Lessons Learned

A living record of every significant bug, outage, and architectural misstep encountered while building the Lumen Semantic Media Pipeline. Each entry documents what broke, why it broke, how it was diagnosed, and what was fixed — in the style of a post-mortem or engineering retrospective.

---

## Table of Contents

- [A — Data Pipelines & AI/ML in Production](#a--data-pipelines--aiml-in-production)
  - [A1. Celery Proxy Task Blocking the Indexing Queue](#a1-celery-proxy-task-blocking-the-indexing-queue)
  - [A2. Celery Prefork + CUDA](#a2-celery-prefork--cuda)
  - [A3. task_acks_late=True Causes Duplicate Task Processing](#a3-task_acks_latetrue-causes-duplicate-task-processing)
  - [A4. Blocking Proxy Encode in Critical Pipeline Path](#a4-blocking-proxy-encode-in-critical-pipeline-path)
  - [A5. CLIP Model / Qdrant Collection Dimension Mismatch](#a5-clip-model--qdrant-collection-dimension-mismatch)
  - [A6. qdrant-client Minor Version Removed .search() — Mocked Tests Passed, Prod Returned 0 Results](#a6-qdrant-client-minor-version-removed-search--mocked-tests-passed-prod-returned-0-results)
  - [A7. Google Takeout Zip Extraction Resets File mtime — Use Filename Date Parsing](#a7-google-takeout-zip-extraction-resets-file-mtime--use-filename-date-parsing)
  - [A8. ML Classifier Path Filter Must Cover All Source Folders, Not Just the Export Folder](#a8-ml-classifier-path-filter-must-cover-all-source-folders-not-just-the-export-folder)
- [B — Backend Services & REST APIs](#b--backend-services--rest-apis)
  - [B1. EXIF Bytes Not JSON-Serializable](#b1-exif-bytes-not-json-serializable)
  - [B2. asyncpg Callback Using Non-Existent Method](#b2-asyncpg-callback-using-non-existent-method)
  - [B3. Search Router Never Registered — All Search Endpoints 404](#b3-search-router-never-registered--all-search-endpoints-404)
  - [B4. API Container Importing Worker ML Dependencies](#b4-api-container-importing-worker-ml-dependencies)
  - [B5. slowapi Crashes on Redis ConnectionError](#b5-slowapi-crashes-on-redis-connectionerror)
  - [B6. Qdrant Client API Mismatch](#b6-qdrant-client-api-mismatch)
  - [B7. os.replace() Fails Across Docker Volume Mount Points](#b7-osreplace-fails-across-docker-volume-mount-points)
  - [B8. os.getenv('VAR', default) Does Not Guard Against Empty String](#b8-osgetenvvar-default-does-not-guard-against-empty-string)
  - [B9. DB Schema Drift Between init-db.sql and Migration Scripts](#b9-db-schema-drift-between-init-dbsql-and-migration-scripts)
  - [B10. FastAPI List[float] on POST Endpoint Is a Body Param](#b10-fastapi-listfloat-on-post-endpoint-is-a-body-param)
  - [B11. Next.js API Proxy Routes Silently Drop Body Params Not Explicitly Destructured](#b11-nextjs-api-proxy-routes-silently-drop-body-params-not-explicitly-destructured)
  - [B12. Flower 2.0 CLI Breaking Change — --broker Must Precede the flower Subcommand](#b12-flower-20-cli-breaking-change----broker-must-precede-the-flower-subcommand)
- [C — Performance & Optimization](#c--performance--optimization)
  - [C1. Worker RAM Thrash](#c1-worker-ram-thrash)
  - [C2. Thumbnail API 2.9s Latency — Dual-Layer Cache](#c2-thumbnail-api-29s-latency--dual-layer-cache)
  - [C3. Qdrant Batch Operations — Individual set_payload Calls Don't Scale](#c3-qdrant-batch-operations--individual-set_payload-calls-dont-scale)
- [D — Production Troubleshooting](#d--production-troubleshooting)
  - [D1. Stale DB Records From File Renames Leave Tasks Stuck as pending](#d1-stale-db-records-from-file-renames-leave-tasks-stuck-as-pending)
  - [D2. Windows ffprobe UnicodeDecodeError on Non-Latin File Metadata](#d2-windows-ffprobe-unicodedecodeerror-on-non-latin-file-metadata)
  - [D3. Rate Limiter Redis Connection Kills All Tests in CI](#d3-rate-limiter-redis-connection-kills-all-tests-in-ci)
  - [D4. pillow_heif register_heic_opener Renamed to register_heif_opener](#d4-pillow_heif-register_heic_opener-renamed-to-register_heif_opener)
  - [D5. WSL2 Shutdown Breaks Docker Port Bindings — Restart Containers After wsl --shutdown](#d5-wsl2-shutdown-breaks-docker-port-bindings--restart-containers-after-wsl---shutdown)
- [E — Frontend & Integration](#e--frontend--integration)
  - [E1. WebSocket URL Wrong Protocol](#e1-websocket-url-wrong-protocol)
  - [E2. Infinite WebSocket Reconnect With No Backoff](#e2-infinite-websocket-reconnect-with-no-backoff)
  - [E3. Docker-Internal Hostname Not Resolvable in Browser](#e3-docker-internal-hostname-not-resolvable-in-browser)
  - [E4. CORS Invalid Combination Silently Killed All WebSocket Connections](#e4-cors-invalid-combination-silently-killed-all-websocket-connections)
  - [E5. React useEffect Infinite Loop From Unstable Callback Dependencies](#e5-react-useeffect-infinite-loop-from-unstable-callback-dependencies)
  - [E6. Next.js Module-Level process.env Reads Captured at Build Time](#e6-nextjs-module-level-processenv-reads-captured-at-build-time)

---

## A — Data Pipelines & AI/ML in Production

- [A1. Celery Proxy Task Blocking the Indexing Queue](#a1-celery-proxy-task-blocking-the-indexing-queue)
- [A2. Celery Prefork + CUDA](#a2-celery-prefork--cuda)
- [A3. task_acks_late=True Causes Duplicate Task Processing](#a3-task_acks_latetrue-causes-duplicate-task-processing)
- [A4. Blocking Proxy Encode in Critical Pipeline Path](#a4-blocking-proxy-encode-in-critical-pipeline-path)
- [A5. CLIP Model / Qdrant Collection Dimension Mismatch](#a5-clip-model--qdrant-collection-dimension-mismatch)
- [A6. qdrant-client Minor Version Removed .search() — Mocked Tests Passed, Prod Returned 0 Results](#a6-qdrant-client-minor-version-removed-search--mocked-tests-passed-prod-returned-0-results)
- [A7. Google Takeout Zip Extraction Resets File mtime — Use Filename Date Parsing](#a7-google-takeout-zip-extraction-resets-file-mtime--use-filename-date-parsing)
- [A8. ML Classifier Path Filter Must Cover All Source Folders, Not Just the Export Folder](#a8-ml-classifier-path-filter-must-cover-all-source-folders-not-just-the-export-folder)

---

## A1. Celery Proxy Task Blocking the Indexing Queue

**Component:** `scripts/start-windows-worker-*.ps1`
**Severity:** High — indexing ground to a halt behind proxy encoding jobs

### What Broke

The Windows Celery worker was started with `--queues=celery,proxies`. The `generate_proxy` task encodes 720p H.265 video to H.264 and took ~13 minutes per file. With a backlog of thousands of videos, proxy jobs monopolised every worker slot and CLIP embedding — the actual indexing work — stalled.

### Root Cause

The `celery` and `proxies` queues shared the same worker pool. A long-running CPU-bound task (video encoding) starved the short-running GPU-bound tasks (CLIP embedding). There was no queue priority or dedicated worker separation.

### Fix

Remove `proxies` from the queues argument on all indexing workers. A dedicated encoding worker can be spun up separately when proxy generation is explicitly needed:

```powershell
# Before
--queues=celery,proxies
# After
--queues=celery
```

### Lesson

**Long-running CPU tasks and short-running GPU tasks must never share the same Celery worker pool.** Separate queues are not enough — the workers consuming those queues must also be separate. Treat proxy generation as a background maintenance job, not part of the critical indexing path.

---

## A2. Celery Prefork + CUDA → "Cannot Re-initialize CUDA in Forked Subprocess"

**Component:** `docker-compose.yml`, `worker/ml/embedder.py`
**Severity:** High — all `process_video` and `process_image` tasks failed immediately

### What Broke

After rebuilding the worker container, all ML tasks failed with `RuntimeError: Cannot re-initialize CUDA in forked subprocess`. Celery's default `prefork` pool forks child worker processes. If CUDA is initialized in the parent process before the fork, child processes inherit the CUDA context and fail when they try to re-initialize it. With `--concurrency=4`, four children all hit this simultaneously.

### Root Cause

Celery's `prefork` pool and CUDA are fundamentally incompatible. Even with `USE_GPU=false` at build time, the worker container runs inside WSL2 which exposes NVIDIA drivers — `torch.cuda.is_available()` returned `True`, and the embedder attempted CUDA initialization before the fork guard could prevent it.

### Fix

Two changes applied together. Use `--pool=solo` (runs tasks sequentially in the main process, no forking) and set `EMBEDDING_DEVICE=cpu` explicitly to prevent CUDA initialization regardless of hardware:

```yaml
command: sh -c "celery -A celery_app worker --pool=solo ..."
```

### Lesson

**Any Celery worker that loads a GPU/ML model must use `--pool=solo` or `--pool=threads`.** Prefork + CUDA is fundamentally incompatible because fork copies the CUDA context to child processes. Always set `EMBEDDING_DEVICE=cpu` explicitly when running a CPU-only worker — relying on `torch.cuda.is_available()` auto-detection is fragile in WSL2.

---

## A3. task_acks_late=True Causes Duplicate Task Processing on Worker Restart

**Component:** `worker/tasks.py`
**Severity:** High — caused already-completed files to be fully reprocessed (re-embedded, re-indexed into Qdrant)

### What Broke

A video file that was already `status = "done"` was picked up and fully reprocessed after a worker restart. Celery re-embedded the frames and created duplicate Qdrant points for an already-indexed file.

### Root Cause

`task_acks_late=True` means Celery only acknowledges a task from the broker queue after the task completes. If the worker is restarted while a task is in-flight, the broker redelivers it to the next available worker. The `process_video` and `process_image` tasks had no guard against this — they always ran all processing steps regardless of the file's current `processing_status`.

### Fix

Added an idempotency guard at the very start of each task, before any expensive work:

```python
# Idempotency guard — redelivered tasks (task_acks_late=True + restart) must not reprocess.
if media_record.processing_status == "done":
    log.info("Skipping already-done video: %s", file_path)
    return {"status": "skipped", "reason": "already_done"}
```

### Lesson

**`task_acks_late=True` trades task loss for duplicate delivery. Any task that has side effects (DB writes, vector upserts, file I/O) must be idempotent.** The idempotency check must be the very first thing the task does — before any expensive or irreversible operation. The general pattern: read a persistent status flag from the DB; if the work is already done, return early.

---

## A4. Blocking Proxy Encode in Critical Pipeline Path

**Component:** `worker/tasks.py`
**Severity:** High — 563 video records stuck in `processing`; zero vectors in Qdrant after hours

### What Broke

`process_video` called `apply_faststart()` synchronously before frame extraction. For 4K source files (5–20 GB), this transcode operation took hours per file. With 6 Celery workers and 6 large files, all worker slots were occupied 100% of the time encoding proxies. Frame extraction and Qdrant upserts — the operations that actually produce search results — never ran.

### Root Cause

Three compounding design decisions: (1) a variable-cost blocking step placed before fast invariant steps — proxy encoding cost scales from seconds to hours, while frame extraction and embedding are comparatively fast; (2) no distinction by codec — H264 sources only need a container remux (~30s), not a full transcode (hours); (3) no escape hatch for large files.

### Fix

Three changes applied together:

```
Option 1: Decouple — generate_proxy dispatched async to 'proxies' queue
          process_video finishes in minutes regardless of source size

Option 2: Duration threshold — non-H264 files > PROXY_MAX_DURATION_SECS
          (default 3600s) are skipped; full movies don't block the pipeline

Option 3: Codec-aware routing — H264 sources use -c copy (stream copy, ~30s)
          only non-H264 sources pay the full transcode cost
```

### Lesson

**Place cheap invariant operations before expensive variable-cost ones.** When you have a step whose cost can range from seconds to hours depending on input, that step belongs at the end of the chain or in a separate async lane — never before steps that must complete for the pipeline to make progress.

---

## A5. CLIP Model / Qdrant Collection Dimension Mismatch — Silent Backlog

**Component:** `worker/tasks.py`, `worker/ml/embedder.py`
**Severity:** High — growing `error` count across restarts before it was caught

### What Broke

The Qdrant `media_vectors` collection was recreated at 768 dimensions to match `clip-ViT-L-14`. But `.env` still had `CLIP_MODEL_NAME=clip-ViT-B-32` (512-dim). The worker loaded ViT-B-32 and produced 512-dim vectors, which Qdrant rejected with `INVALID_ARGUMENT`. Because `autoretry_for=(Exception,)` covers all exceptions, every rejected task retried 5× before landing in `error`. The error count grew from 2 → 169 → 269 → 502 across restarts before anyone caught it.

### Root Cause

The collection and the model env var were changed in separate steps with no cross-check. There is no startup assertion that `embedder.embedding_dim == collection.vector_size`. `INVALID_ARGUMENT` errors are not distinguishable from transient errors by the current retry logic, so they waste ~10 minutes of worker time per file before permanent failure.

### Fix

Updated `.env` (`CLIP_MODEL_NAME=clip-ViT-L-14`), reset 502 `INVALID_ARGUMENT` errors to `pending`, and added a startup assertion to catch mismatches loudly at start rather than silently at scale:

```python
qdrant_info = qdrant_client.get_collection(QDRANT_COLLECTION_NAME)
collection_dim = qdrant_info.config.params.vectors.size
if collection_dim != embedder.embedding_dim:
    raise RuntimeError(
        f"Dimension mismatch: Qdrant collection is {collection_dim}-dim "
        f"but {embedder.model_name} produces {embedder.embedding_dim}-dim vectors. "
        f"Check CLIP_MODEL_NAME in .env."
    )
```

### Lesson

**When you change a schema — whether a database column type or a vector dimension — every producer of that schema must be updated atomically.** Add a startup assertion that checks the producer's output format against the store's expected format before any work begins. Fail loud at startup, not silently at scale.

---

## A6. `qdrant-client` Minor Version Removed `.search()` — Mocked Tests Passed, Prod Returned 0 Results

**Component:** `api/agent/steps.py`, `api/requirements.txt`
**Severity:** High — agent endpoint always returned 0 results on prod; direct search endpoint worked fine

### What Broke

After merging the v2.0.0 multi-agent feature, `POST /api/agent/query` always returned 0 results on prod. The agent coordinator returned in 62ms — too fast for CLIP inference — meaning the search node was silently short-circuiting. `qdrant-client` was pinned to `>=1.7` in `requirements.txt`. Prod had installed `1.17.0`, which removed `QdrantClient.search()` entirely in favor of `query_points()`. All tests mock Qdrant at the dependency-injection layer, so the mock's `.search()` attribute worked fine in CI. On prod the real client raised `AttributeError`, caught by a broad `except Exception` in `QdrantRetrieveStep` and silently turned into an empty result list.

### Root Cause

Open-ended version pinning (`>=1.7`) allowed the prod install to pull in a breaking minor-version change. Mocking at the injection layer — rather than testing the real client method path — meant the breaking rename was invisible in CI. The broad `except Exception` in the search step converted a hard failure into a silent empty result.

### Fix

Replace `self._qdrant.search(...)` with `self._qdrant.query_points(...)`. Pin to an exact minor: `qdrant-client>=1.17.0,<2.0`. Add `threshold`/`limit` fields to `AgentState` TypedDict and thread them through `search_agent_run()` (they were silently ignored).

### Lesson

**Pin third-party SDK versions to an exact minor version — not open-ended `>=X.Y`.** Add a smoke test that calls the real client method path (even with a local Qdrant via a CI service container) to catch removed APIs. When a handler catches `Exception` and returns an empty result, always log a warning — silent failures are extremely hard to diagnose on prod.

---

## B — Backend Services & REST APIs

- [B1. EXIF Bytes Not JSON-Serializable](#b1-exif-bytes-not-json-serializable)
- [B2. asyncpg Callback Using Non-Existent Method](#b2-asyncpg-callback-using-non-existent-method)
- [B3. Search Router Never Registered — All Search Endpoints 404](#b3-search-router-never-registered--all-search-endpoints-404)
- [B4. API Container Importing Worker ML Dependencies](#b4-api-container-importing-worker-ml-dependencies)
- [B5. slowapi Crashes on Redis ConnectionError](#b5-slowapi-crashes-on-redis-connectionerror)
- [B6. Qdrant Client API Mismatch](#b6-qdrant-client-api-mismatch)
- [B7. os.replace() Fails Across Docker Volume Mount Points](#b7-osreplace-fails-across-docker-volume-mount-points)
- [B8. os.getenv('VAR', default) Does Not Guard Against Empty String](#b8-osgetenvvar-default-does-not-guard-against-empty-string)
- [B9. DB Schema Drift Between init-db.sql and Migration Scripts](#b9-db-schema-drift-between-init-dbsql-and-migration-scripts)
- [B10. FastAPI List[float] on POST Endpoint Is a Body Param](#b10-fastapi-listfloat-on-post-endpoint-is-a-body-param)
- [B11. Next.js API Proxy Routes Silently Drop Body Params Not Explicitly Destructured](#b11-nextjs-api-proxy-routes-silently-drop-body-params-not-explicitly-destructured)
- [B12. Flower 2.0 CLI Breaking Change — --broker Must Precede the flower Subcommand](#b12-flower-20-cli-breaking-change----broker-must-precede-the-flower-subcommand)

---

## B1. EXIF Bytes Not JSON-Serializable

**Component:** `worker/tasks.py`
**Severity:** Medium — caused worker task crashes for images with EXIF data

### What Broke

When processing images through Pillow, EXIF data is returned as a dictionary containing raw `bytes` values (e.g., maker notes, GPS binary data). Attempting to store this in the database as JSON or pass it through Celery's result backend caused a silent serialization crash. The initial workaround was to skip EXIF entirely; a later attempt to re-add it reproduced the same crash because it passed the raw dict without filtering non-serializable types.

### Root Cause

`json.dumps()` (and anything that internally serializes to JSON — SQLAlchemy JSON columns, Celery serializers) has no default handler for `bytes`. Python EXIF data contains a mix of integers, strings, tuples, and raw bytes, so a naive dict pass-through always fails.

### Fix

Either convert `bytes` values to hex strings during extraction, or skip EXIF storage entirely until a proper EXIF parsing library (e.g., `exifread`) is integrated. The simplest safe approach:

```python
# Safe approach: skip bytes values
if exif_data:
    media_record.exif_data = {
        k: v for k, v in exif_data.items() if not isinstance(v, bytes)
    }
```

### Lesson

**Never assume a third-party library's output is JSON-serializable.** Binary formats like images, audio, and video metadata almost always contain binary fields. Add explicit type guards before any JSON serialization step, especially when the data flows through a message queue or database JSON column.

---

## B2. asyncpg Callback Using Non-Existent Method

**Component:** `api/utils/notifications.py`
**Severity:** Critical — the entire real-time notification system was silently non-functional

### What Broke

The PostgreSQL LISTEN/NOTIFY listener registered an `_on_notification` callback with asyncpg. When a notification arrived, the callback tried to schedule work onto the asyncio event loop using a method that doesn't exist on an `asyncpg.Connection` object:

```python
def _on_notification(self, conn, pid, channel, payload):
    # BROKEN: asyncpg.Connection has no get_event_loop() method
    self.connection.get_event_loop().call_soon_threadsafe(
        self._notifications_queue.put_nowait, data
    )
```

The `AttributeError` was swallowed silently by asyncpg's callback dispatcher. The system appeared to connect and listen, but no notifications were ever delivered to WebSocket clients.

### Root Cause

Confusion between `asyncio.get_event_loop()` (a module-level function) and a non-existent method on a connection object. The asyncpg API does not expose the event loop on the connection — you use the standard library call.

### Fix

```python
def _on_notification(self, conn, pid, channel, payload):
    # Correct: use the asyncio module-level function
    asyncio.get_event_loop().call_soon_threadsafe(
        self._notifications_queue.put_nowait, data
    )
```

### Lesson

**Silent failures in async callbacks are extremely dangerous.** When wiring up callbacks from a third-party async library, always wrap the callback body in a broad `try/except` with explicit logging. An exception inside `asyncpg`'s notification handler disappears into the void — there is no automatic propagation to your application layer. Test the full end-to-end data path explicitly, not just the connection establishment.

---

## B3. Search Router Never Registered — All Search Endpoints 404

**Component:** `api/main.py`
**Severity:** Critical — all search functionality silently returned 404

### What Broke

`api/routers/search.py` was created with all the search endpoints, but was never imported or mounted in `main.py`. Every call to `/api/search` returned a 404 with no error in any log because FastAPI simply had no route registered for the path.

```python
# v1.1.0 main.py — search router missing entirely
from routers import health, ingest, updates
app.include_router(health.router, prefix="/api")
app.include_router(ingest.router, prefix="/api")
app.include_router(updates.router, prefix="/api")
# search.router never added
```

### Root Cause

The router file was built independently of the application entrypoint. There was no automated check or test that verifies all intended routes actually respond. The issue was invisible until explicitly testing the `/search` endpoint.

### Fix

```python
from routers import health, ingest, search, updates
app.include_router(search.router, prefix="/api", tags=["search"])
```

### Lesson

**When splitting a FastAPI application into routers, always write a smoke test that enumerates expected routes.** FastAPI exposes `app.routes` — a simple test that asserts `/api/search` is present would have caught this immediately. Alternatively, review the OpenAPI docs at `/docs` after every deployment to visually confirm all expected endpoints exist.

---

## B4. API Container Importing Worker ML Dependencies

**Component:** `api/routers/search.py`
**Severity:** Critical — API container crashed on startup with ModuleNotFoundError

### What Broke

`api/routers/search.py` imported ML embedding libraries that only exist in the `worker` container's Python environment:

```python
# BROKEN — these packages are not in api/requirements.txt
import numpy as np
from ml.embedder import get_embedder
from db.session import get_async_db
```

When the `lumen-api` container started, Python's import system failed at the `search` module import, which cascaded into a startup crash for the entire FastAPI application. The container would restart in a loop.

### Root Cause

The API and worker share some code structure visually, but they are separate containers with separate dependency trees. The worker contains multi-gigabyte ML models and GPU libraries. The API is intentionally a thin, fast HTTP layer. Importing across these boundaries violates service separation.

### Fix

Remove all ML imports from the API. The search endpoint calls Qdrant directly with a pre-computed vector, delegating embedding to the client or a separate embedding endpoint:

```python
# AFTER — API only talks to Qdrant, no ML dependencies
from qdrant_client import QdrantClient
client = QdrantClient(host=QDRANT_HOST, port=QDRANT_PORT)
results = client.search(collection_name="media", query_vector=vector, limit=limit)
```

### Lesson

**Each container's `requirements.txt` is a hard boundary.** In a microservices architecture, import boundaries must mirror deployment boundaries. A simple CI check — start the API container in isolation and hit `/docs` — would catch this before it ever ships.

---

## B5. slowapi Crashes on Redis ConnectionError Due to Wrong Hostname in REDIS_URL

**Component:** `api/rate_limit.py`
**Severity:** Critical — all API requests returned 500; the error was a slowapi bug triggered by a misconfigured env var

### What Broke

`rate_limit.py` used `os.getenv("REDIS_URL", "redis://redis:6379")` for the slowapi storage URI. The `.env` file had `REDIS_URL=redis://redis:6379` — the hostname `redis` never resolved inside the Docker network. Every request caused slowapi to get a `ConnectionError` and pass it to `_rate_limit_exceeded_handler`, which unconditionally accessed `exc.detail` — crashing with `AttributeError: 'ConnectionError' object has no attribute 'detail'`.

### Root Cause

Two compounding bugs: (1) `REDIS_URL` in `.env` used the wrong container hostname; (2) slowapi's middleware has a latent bug where it routes any exception from Redis — including `ConnectionError` — to `_rate_limit_exceeded_handler`, which expects an `HTTPException` subclass with a `.detail` attribute.

### Fix

Prefer `CELERY_BROKER_URL` (which is always set to the correct container hostname in Compose) over `REDIS_URL`, and add `in_memory_fallback_enabled=True` to fail open if Redis is temporarily unreachable:

```python
_storage_uri = (
    os.getenv("CELERY_BROKER_URL")          # already correct in Compose env
    or os.getenv("REDIS_URL", "redis://lumen-redis:6379")
)

limiter = Limiter(
    key_func=get_remote_address,
    storage_uri=_storage_uri,
    default_limits=[LIMIT_DEFAULT],
    in_memory_fallback_enabled=True,  # fail open if Redis is temporarily unreachable
)
```

### Lesson

**Verify that `REDIS_URL` matches the actual container hostname before deploying.** In Docker Compose stacks, the hostname is the service name. Run `docker exec <api_container> env | grep REDIS` after any env change to confirm. Add `in_memory_fallback_enabled=True` to any slowapi `Limiter` that uses a Redis backend — without it, a transient Redis unavailability crashes every in-flight request.

---

## B6. Qdrant Client API Mismatch

**Component:** `api/routers/search.py`, `worker/tasks.py`
**Severity:** High — search returned 500 errors after appearing to work in isolation

### What Broke

The code was written against the Qdrant client docs, but the installed package version (`qdrant-client==1.17.0`) had renamed and restructured its search API multiple times across minor versions. Three consecutive attempts were needed: `.search_points()` → `AttributeError`, `.search_vectors()` → `AttributeError`, `.query_points()` with the correct payload shape → success.

### Root Cause

Qdrant's Python client does not follow semantic versioning strictly. Method names changed between 1.x minor versions without deprecation warnings. The online docs were ahead of the pinned package version, so the published examples referenced methods that didn't exist in the installed release.

### Fix

Pin the exact client version in `requirements.txt` and verify the installed version's actual API surface via `help()` in a REPL or the GitHub tag changelog for the pinned version — not the latest hosted docs:

```
qdrant-client>=1.17.0,<2.0
```

### Lesson

**Treat third-party SDK docs with suspicion unless verifying against the exact installed version.** The canonical source of truth is `help(client)` or the GitHub tag for the pinned release, not the latest hosted docs. Always pin transitive dependencies and note the version in a comment next to the call site.

---

## B7. `os.replace()` Fails Across Docker Volume Mount Points

**Component:** `worker/tasks.py`, `worker/ingest/ffmpeg.py`
**Severity:** High — all `generate_proxy` tasks for HEVC source files failed immediately after FFmpeg succeeded

### What Broke

`apply_faststart()` wrote the FFmpeg output to `tempfile.mktemp(dir="/tmp")`, then called `os.replace(str(tmp_path), str(dest))` to move it to `/mnt/proxies/...`. This raises `OSError: [Errno 18] Invalid cross-device link` whenever source and destination are on different filesystems.

### Root Cause

`os.replace()` is backed by POSIX `rename(2)` — atomic and instant within one filesystem, but always `EXDEV` across filesystems. Docker volume mounts (`/tmp` on the container's overlay filesystem, `/mnt/proxies` on a bind-mounted host path) are always different filesystems. `OSError` was also not in `autoretry_for=(FFmpegError,)`, so tasks went straight to FAILURE with 0 retries.

### Fix

```python
# Before — fails across volume mounts:
os.replace(str(tmp_path), str(dest))

# After — copy+delete fallback on cross-device move:
import shutil
shutil.move(str(tmp_path), str(dest))
```

Also updated `autoretry_for=(FFmpegError, OSError)` so future OS-level errors retry with exponential backoff.

### Lesson

**`os.replace()` is POSIX `rename()` — atomic within one filesystem, but always fails across filesystems.** Docker volume mounts are separate filesystems by definition. Whenever source and destination paths could be on different mounts, use `shutil.move()` which falls back to copy+unlink. Also: include `OSError` in `autoretry_for` for I/O-heavy tasks — OS-level I/O failures are transient more often than they are permanent.

---

## B8. `os.getenv('VAR', default)` Does Not Guard Against Empty String

**Component:** `worker/tasks.py`, `worker/ingest/ffmpeg.py`
**Severity:** High — `process_video` tasks stuck in perpetual RETRY with `ValueError`

### What Broke

`KEYFRAME_RESOLUTION` was declared in `docker-compose.second.yml` as `${KEYFRAME_RESOLUTION:-224}`. The host shell had this variable exported as an empty string. The `:-` default in shell substitution fills in a default only when the variable is **unset or null** — an exported empty-string variable is considered set. The container received `KEYFRAME_RESOLUTION=""`, then `int(os.getenv("KEYFRAME_RESOLUTION", "224"))` returned `int("")` → `ValueError`. Because `autoretry_for=(Exception,)` covers `ValueError`, tasks retried on exponential backoff indefinitely.

### Root Cause

`os.getenv('VAR', 'fallback')` fires only when the key is absent from `os.environ`. An empty-string value is returned as-is. Any subsequent `int()` or `float()` cast will raise.

### Fix

```python
# Before — silently passes empty string to int():
resolution = int(os.getenv("KEYFRAME_RESOLUTION", "224"))

# After — empty string is falsy, falls back to default:
resolution = int(os.getenv("KEYFRAME_RESOLUTION") or "224")
```

Applied to all five affected variables across `tasks.py` and `ingest/ffmpeg.py`.

### Lesson

**`os.getenv('VAR', default)` and shell `${VAR:-default}` share the same footgun: neither protects against an explicitly-set empty string.** The Python `or` idiom is more defensive because it treats any falsy value (empty string, zero, None) as "use the fallback". For configuration values that will be passed to `int()` or `float()`, always use `os.getenv('VAR') or 'default'`.

---

## B9. DB Schema Drift Between `init-db.sql` and Migration Scripts

**Component:** PostgreSQL `media_files` table, `init-db.sql`
**Severity:** Critical — workers crashed immediately on every task on a fresh deploy

### What Broke

On a fresh cloud deploy, workers crashed immediately with `sqlalchemy.exc.ProgrammingError: column media_files.embedding_started_at does not exist`. The dev machine had the column because it was added via migrations and manual `ALTER TABLE` commands. The cloud server had a fresh Postgres container that only ran `init-db.sql`, which was never updated as observability columns were added in later PRs.

### Root Cause

Migration scripts and `init-db.sql` diverged. There was one migration script for `model_version` but nothing for `embedding_started_at`, `worker_id`, `frame_cache_hit`, or `embedding_ms` — and `init-db.sql` had none of them. Fresh deploys are silently broken whenever the schema is extended without keeping `init-db.sql` in sync.

### Fix

Manual `ALTER TABLE … ADD COLUMN IF NOT EXISTS` on the running container, then added all 5 columns to `init-db.sql` for future fresh deploys, and created `scripts/migrate_add_observability_columns.sql` for upgrading existing DBs.

### Lesson

**Migration scripts and the init script are two separate code paths that must stay in sync.** Every `ALTER TABLE` that adds a column also gets a matching change to `init-db.sql`. The smell-check before merging any schema PR: "if someone clones this repo today and runs `docker compose up` for the first time, will `init-db.sql` produce the same schema as a fully-migrated dev DB?"

---

## B10. FastAPI `List[float]` on a POST Endpoint Is a Body Param, Not a Query Param

**Component:** `api/routers/search.py`
**Severity:** Medium — 14 tests all returned 422 Unprocessable Entity

### What Broke

14 new tests for `POST /api/search-vector` all returned 422. The vector was being sent as repeated query params (`?vector=0.1&vector=0.2&…`) following the same pattern used for scalars like `limit`. FastAPI's parameter resolution rules for POST endpoints treat `List[float]` as a body param, not a query param — it expects a raw JSON array body.

### Root Cause

FastAPI has a nuanced rule: the binding location of a parameter depends on its *type*, not just the presence/absence of `Body()`. Scalar types are query params by default on POST; collection types become body params. This is documented but easy to miss when writing tests against existing endpoints.

### Fix

```python
# Wrong — 422 on every call
client.post("/api/search-vector", params=[("vector", 0.1), ("vector", 0.2)])

# Correct — raw JSON array body
client.post("/api/search-vector", json=[0.1, 0.2, 0.3])

# Scalar query params still work alongside the JSON body
client.post("/api/search-vector", json=[0.1, 0.2], params={"limit": 5})
```

### Lesson

**FastAPI's implicit parameter binding has non-obvious rules for collection types.** When a test returns 422 and the data looks correct, check the OpenAPI schema first — FastAPI generates it automatically and will show exactly what it expects where. `/docs` showing `vector` under `requestBody` (not `parameters`) immediately explains the 422.

---

## C — Performance & Optimization

- [C1. Worker RAM Thrash](#c1-worker-ram-thrash)
- [C2. Thumbnail API 2.9s Latency — Dual-Layer Cache](#c2-thumbnail-api-29s-latency--dual-layer-cache)
- [C3. Qdrant Batch Operations — Individual set_payload Calls Don't Scale](#c3-qdrant-batch-operations--individual-set_payload-calls-dont-scale)

---

## C1. Worker RAM Thrash — Load Average 23.75

**Component:** `docker-compose.yml`, `worker/celery_app.py`
**Severity:** High — host machine became unresponsive; overall throughput collapsed

### What Broke

Celery defaulted to `--concurrency=24` (one worker per CPU core). Each worker loaded its own copy of the CLIP model into RAM (~600 MB) and spawned FFmpeg subprocesses (~400 MB each for 4K video). With 24 workers: CLIP alone consumed 14.4 GB, FFmpeg peaks added 9.6 GB — exceeding available RAM and causing heavy swap thrashing.

### Root Cause

Default Celery concurrency is based on CPU count, which is correct for I/O-bound tasks but catastrophically wrong for memory-heavy ML workloads. Each worker was also never recycled, so PyTorch and FFmpeg memory leaks accumulated over the lifetime of the process.

### Fix

```
CELERY_CONCURRENCY=4              # floor(free_RAM_GB / ~2 GB per worker)
worker_max_tasks_per_child=50     # recycle child after 50 tasks (clears leaks)
worker_max_memory_per_child=1500000  # hard 1.5 GB ceiling per child
```

Load average dropped from 23.75 → 8.59.

### Lesson

**Default configurations assume a class of workload.** For memory-heavy ML inference, the right concurrency is `floor(RAM / model_size)`, not CPU count. `max_tasks_per_child` is the Celery equivalent of connection pool recycling — without it, PyTorch's CUDA allocator and FFmpeg's buffer pools cause gradual memory growth that only appears after hours of operation.

---

## C2. Thumbnail API 2.9s Latency — Dual-Layer Cache

**Component:** `api/routers/ingest.py`, Cloudflare Cache Rules
**Severity:** Medium — 1,066 production requests averaging 2.9s, P95 4.87s; noticeable UI lag on thumbnail grid load

### What Broke

Audit logs revealed `/api/thumbnail` was the slowest endpoint by a wide margin. Every request — including repeated thumbnails for the same video at the same timestamp — ran FFmpeg end-to-end, extracting a JPEG frame from scratch each time. With 1,066 origin hits averaging 2.94s, the thumbnail grid felt sluggish.

### Root Cause

No caching existed at any layer:
1. **No CDN caching**: Cloudflare was configured as DNS-only for the thumbnail path — every request passed through to the origin server.
2. **No server-side caching**: Each `GET /api/thumbnail?path=…&t=…` call unconditionally spawned an FFmpeg subprocess, even for identical `(path, t)` combinations loaded in the same browser session.

### Fix

Two-layer caching:

**Layer 1 — Cloudflare Edge Cache Rule** (eliminates origin hits entirely for repeat requests):
- Rule: `http.request.full_uri wildcard "https://lumen.danhle.net/api/thumbnail*"`
- Edge TTL: 1 day, eligible for cache
- `Cache-Control: public, max-age=86400` response header instructs Cloudflare to store the response

**Layer 2 — Server-side LRU Cache** (handles cold misses that reach origin):
```python
_THUMB_CACHE_MAX = int(os.getenv("THUMBNAIL_CACHE_MAX", "500"))
_thumb_cache: OrderedDict[tuple, bytes] = OrderedDict()
_thumb_cache_lock = threading.Lock()

cache_key = (path, round(seek, 1))
cached = _cache_get(cache_key)
if cached is not None:
    return Response(content=cached, media_type="image/jpeg",
        headers={"Cache-Control": "public, max-age=86400", "X-Cache": "HIT"})
# ... ffmpeg call ...
_cache_set(cache_key, stdout)
return Response(..., headers={..., "X-Cache": "MISS"})
```

### Results (stress test: 20 sequential requests, same URL)

| | Before | After |
|---|---|---|
| Origin hits (1,066 requests) | 1,066 | ~2 (Cloudflare served the rest) |
| Avg origin latency | 2,938ms | 868ms |
| P95 origin latency | 4,872ms | 892ms |
| Request 1 (cold) | 2,030ms | 2,030ms |
| Requests 2–20 (same URL) | 2,900ms avg | ~85ms (Cloudflare HIT) |

18 of 20 stress test requests were served by Cloudflare's edge — those never appeared in `audit_logs` at all because they never reached the origin. The 2 that did reach origin averaged 868ms (down from 2.9s).

### Lesson

**Measure before optimizing.** The `audit_logs` table (`endpoint`, `response_ms`, `PERCENTILE_CONT(0.95)`) made it trivial to find the worst offender. Once identified, the fix was a 15-minute two-layer approach:

1. **CDN first** — eliminates the problem at the edge for repeat requests. Zero code change.
2. **In-process LRU second** — handles misses and cross-session uniqueness with a stdlib `OrderedDict` + `threading.Lock`. No Redis, no external dependency.

The `X-Cache: HIT/MISS` header on responses gives instant observability into which layer is serving each request.

---

## D — Production Troubleshooting

- [D1. Stale DB Records From File Renames Leave Tasks Stuck as pending](#d1-stale-db-records-from-file-renames-leave-tasks-stuck-as-pending)
- [D2. Windows ffprobe UnicodeDecodeError on Non-Latin File Metadata](#d2-windows-ffprobe-unicodedecodeerror-on-non-latin-file-metadata)
- [D3. Rate Limiter Redis Connection Kills All Tests in CI](#d3-rate-limiter-redis-connection-kills-all-tests-in-ci)
- [D4. pillow_heif register_heic_opener Renamed to register_heif_opener](#d4-pillow_heif-register_heic_opener-renamed-to-register_heif_opener)
- [D5. WSL2 Shutdown Breaks Docker Port Bindings — Restart Containers After wsl --shutdown](#d5-wsl2-shutdown-breaks-docker-port-bindings--restart-containers-after-wsl---shutdown)

---

## D1. Stale DB Records From File Renames Leave Tasks Stuck as `pending` Forever

**Component:** `worker/tasks.py`, PostgreSQL `media_files` table
**Severity:** Medium — files permanently stuck as `pending`, never retried

### What Broke

Multiple files were showing as `pending` in the DB but never being picked up for processing. The files had been renamed on disk. The crawler matches files by their full `file_path` — when a file is renamed, the old path no longer exists on disk and the `pending` record is never updated or retried. The new path is treated as a brand new file, creating a duplicate `done` record.

### Root Cause

The crawler has no rename detection — it only matches by exact `file_path`. Renaming a file on disk orphans its DB record permanently, leaving one `done` record for the new name and one `pending` record for the old name that will never be processed.

### Fix

Identify stale records by cross-referencing `pending` paths against what actually exists on disk, then delete them. For now, manual cleanup is the remediation:

```sql
DELETE FROM media_files
WHERE processing_status = 'pending'
  AND file_path LIKE '<affected_directory>/%';
```

### Lesson

**The crawler has no rename detection — it only matches by exact `file_path`.** If files are regularly renamed, either implement a periodic cleanup query to delete `pending` records whose paths no longer exist on disk, or track files by inode/content hash rather than path.

---

## D2. Windows ffprobe UnicodeDecodeError on Non-Latin File Metadata

**Component:** `worker/ingest/ffmpeg.py`
**Severity:** Medium — caused worker crashes for files with non-Latin characters in metadata

### What Broke

Files failed with `UnicodeDecodeError: 'cp1252' codec can't decode byte 0x81 in position N`. The worker crashed during `ffprobe` metadata extraction and files were left in `error` status.

### Root Cause

`subprocess.run(..., text=True)` without an explicit `encoding=` argument uses the platform's default encoding. On Windows, this is `cp1252`. ffprobe outputs UTF-8, including metadata fields that may contain Japanese or other non-Latin characters. The Linux worker handled the same files fine because Linux defaults to UTF-8.

### Fix

Added `encoding="utf-8"` explicitly to all `subprocess.run` calls in `ffmpeg.py`, applied to both `probe_media()` and `extract_keyframes()`:

```python
result = subprocess.run(
    [...],
    capture_output=True,
    text=True,
    encoding="utf-8",  # prevents cp1252 crash on Windows
    timeout=30,
)
```

### Lesson

**Always specify `encoding="utf-8"` when using `text=True` in `subprocess.run` on cross-platform code.** Never rely on the platform default — Windows cp1252, Linux UTF-8, and macOS UTF-8 will behave differently. Media files routinely contain non-Latin metadata that will silently work on Linux/macOS and crash on Windows without explicit encoding.

---

## D3. Rate Limiter Redis Connection Kills All Tests in CI (109 failures)

**Component:** `api/rate_limit.py`, `conftest.py`
**Severity:** High — 109 out of 135 tests failed in CI; passed locally

### What Broke

`rate_limit.py` initialises a `slowapi.Limiter` at module import time with `storage_uri = os.getenv("REDIS_URL", "redis://redis:6379")`. The `conftest.py` overrode this to `redis://localhost:6379` for local dev. On the GitHub Actions `ubuntu-latest` runner there is no Redis service — every request that hit a rate-limited endpoint raised `redis.exceptions.ConnectionError`. The module docstring even claimed it "falls back gracefully to in-memory if Redis is unreachable" — this was incorrect.

### Root Cause

Two compounding mistakes: `conftest.py` defaulted `REDIS_URL` to a real Redis address, importing a live-service dependency into a supposedly self-contained test suite; and a misleading code comment implied automatic fallback that doesn't exist in `slowapi`.

### Fix

```python
# conftest.py — use in-memory backend, zero external dependencies
os.environ.setdefault("REDIS_URL", "memory://")

# ci.yml — belt-and-suspenders in case env is already set
- name: Run pytest
  env:
    REDIS_URL: memory://
  run: pytest ...
```

`limits` (the backend library used by `slowapi`) supports `memory://` as a fully functional in-process counter store.

### Lesson

**"Passes locally" is not evidence that a test is self-contained** — it may just mean the developer machine happens to have a Redis process running. Every external service a test touches either needs to be in a `docker-compose` for the test runner, or needs to be mocked. Audit every `os.getenv()` default in `conftest.py` and ask: "does this URL actually exist on a clean CI runner?"

---

## E — Frontend & Integration

- [E1. WebSocket URL Wrong Protocol](#e1-websocket-url-wrong-protocol)
- [E2. Infinite WebSocket Reconnect With No Backoff](#e2-infinite-websocket-reconnect-with-no-backoff)
- [E3. Docker-Internal Hostname Not Resolvable in Browser](#e3-docker-internal-hostname-not-resolvable-in-browser)
- [E4. CORS Invalid Combination Silently Killed All WebSocket Connections](#e4-cors-invalid-combination-silently-killed-all-websocket-connections)
- [E5. React useEffect Infinite Loop From Unstable Callback Dependencies](#e5-react-useeffect-infinite-loop-from-unstable-callback-dependencies)
- [E6. Next.js Module-Level process.env Reads Captured at Build Time](#e6-nextjs-module-level-processenv-reads-captured-at-build-time)

---

## E1. WebSocket URL Wrong Protocol (http vs ws)

**Component:** `frontend/hooks/useMediaUpdates.ts`, `useStatusUpdates.ts`
**Severity:** High — WebSocket connections failed immediately in browser

### What Broke

The environment variable `NEXT_PUBLIC_API_URL` is an HTTP URL (`http://api:8000`). This URL was passed directly to the `WebSocket` constructor without protocol substitution:

```typescript
// BROKEN — browsers reject "http://" as a WebSocket scheme
const ws = new WebSocket(`${process.env.NEXT_PUBLIC_API_URL}/api/ws/processing-status`)
// → new WebSocket("http://api:8000/api/ws/processing-status") → immediate error
```

Browsers only accept `ws://` or `wss://` as WebSocket protocols. The connection failed before the TCP handshake was even attempted.

### Fix

Convert the protocol before constructing the URL. This is the only required change; no server-side modifications are needed:

```typescript
const apiUrl = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'
const wsProtocol = apiUrl.startsWith('https') ? 'wss' : 'ws'
const apiHost = apiUrl.replace(/^https?:\/\//, '').replace(/\/$/, '')
const wsUrl = `${wsProtocol}://${apiHost}/api/ws/processing-status`
```

### Lesson

**WebSocket URLs are not HTTP URLs.** They share the same TCP/TLS transport but use `ws://` and `wss://` schemes. Environment variables that store API base URLs are almost always `http://` — never pass them raw to `new WebSocket()`. Create a single utility function that performs the scheme conversion, and use it everywhere.

---

## E2. Infinite WebSocket Reconnect With No Backoff

**Component:** `frontend/hooks/useMediaUpdates.ts`, `useStatusUpdates.ts`
**Severity:** High — hammered the API with connection storms during outages

### What Broke

The initial `ws.onclose` handler unconditionally scheduled a reconnect after a fixed 3-second delay with no counter and no stopping condition:

```typescript
ws.onclose = () => {
    reconnectTimer = setTimeout(connect, 3000); // Always, forever
};
```

If the API was unreachable, the client hammered it with a new connection attempt every 3 seconds indefinitely. With multiple browser tabs open, this multiplied.

### Fix

Exponential backoff with a capped maximum and retry counter reset on successful open:

```typescript
ws.onopen = () => {
    retryCount = 0; // Reset on success
}
ws.onclose = () => {
    if (retryCount < MAX_RETRIES) {
        retryCount++
        const delay = BASE_RETRY_DELAY * Math.pow(2, retryCount - 1) // 3s, 6s, 12s, 24s...
        reconnectTimer = setTimeout(connect, Math.min(delay, 30000)) // Cap at 30s
    } else {
        setError(new Error('Max retries exceeded'))
    }
}
```

### Lesson

**Every network reconnect loop must have a maximum retry count and exponential backoff.** Without these, a single API restart causes a thundering herd of connection attempts from all connected clients simultaneously. Always reset the retry counter on successful connection to handle transient outages gracefully.

---

## E3. Docker-Internal Hostname Not Resolvable in Browser

**Component:** `frontend/hooks/useStatusUpdates.ts`
**Severity:** High — WebSocket connections failed in browser even after protocol fix

### What Broke

`NEXT_PUBLIC_API_URL` was set to `http://api:8000` in the Docker Compose environment. Inside the Docker network, `api` resolves correctly via Docker's internal DNS. However, Next.js embeds `NEXT_PUBLIC_*` variables into the client-side JavaScript bundle at **build time**. The browser received `ws://api:8000/...` as the WebSocket URL, and `api` is not a hostname the browser can resolve.

### Root Cause

`NEXT_PUBLIC_*` variables are inlined into the JavaScript sent to the browser. A Docker-internal service hostname is meaningless outside the container network — there is a fundamental mismatch between the server-side network and the client-side network.

### Fix

Detect the Docker hostname at runtime and substitute `localhost`. The longer-term fix is to expose an environment variable specifically for the browser URL (e.g., `NEXT_PUBLIC_API_BROWSER_URL=http://localhost:8000`) separate from the server-side `NEXT_PUBLIC_API_URL`:

```typescript
let apiUrl = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'
if (typeof window !== 'undefined' && apiUrl.includes('api:8000')) {
    apiUrl = 'http://localhost:8000' // Browser cannot resolve Docker DNS
}
```

### Lesson

**In containerized Next.js apps, there are two distinct networks: the Docker internal network (server-to-server) and the host/browser network (client-to-server).** `NEXT_PUBLIC_*` variables end up in the browser. Never put Docker-internal hostnames in `NEXT_PUBLIC_*` variables. Use a separate environment variable for the browser-visible URL, or use Next.js API route proxying so the browser never talks to the backend directly.

---

## E4. CORS Invalid Combination Silently Killed All WebSocket Connections

**Component:** `api/main.py`
**Severity:** Critical — every browser WebSocket connection rejected with 400

### What Broke

The FastAPI CORS middleware was configured with a combination that violates the CORS specification:

```python
# BROKEN — illegal combination per CORS spec
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],     # Wildcard
    allow_credentials=True,  # Can't use credentials with wildcard origin
)
```

Per the specification, a server cannot respond with `Access-Control-Allow-Origin: *` while also setting `Access-Control-Allow-Credentials: true`. Starlette enforces this by returning HTTP 400 for any request carrying an `Origin` header. Browsers always send `Origin` on WebSocket upgrade requests, so every connection attempt was rejected before the handler was ever invoked.

### Root Cause

The illegal combination passes server startup and passes HTTP endpoint tests (because most test tools don't send `Origin`). It only manifests in browser WebSocket and cross-origin fetch requests. The only log evidence was a terse `connection rejected (400 Bad Request)` mixed among normal entries.

### Fix

```python
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,  # Cannot use True with wildcard origins
)
```

### Lesson

**`allow_credentials=True` with `allow_origins=["*"]` is not a runtime error — it silently breaks a specific class of requests.** Always test WebSocket connectivity specifically from a browser, not just from `curl` or Python scripts. A `400 Bad Request` on a WebSocket endpoint is almost always a middleware/CORS issue, not a handler bug.

---

## E5. React useEffect Infinite Loop From Unstable Callback Dependencies

**Component:** `frontend/hooks/useStatusUpdates.ts`, `frontend/hooks/useMediaUpdates.ts`
**Severity:** Critical — generated 100,000+ WebSocket errors; effectively a client-side DoS

### What Broke

Both hooks accepted `onUpdate` and `onError` callback props and listed them as `useEffect` dependencies. The caller passed inline arrow functions, which create a new function object on every render. React's `useEffect` saw the dependency change each render, closed the WebSocket, and opened a new one — triggering a state update that caused another render, closing the new WebSocket, and so on forever. The result was over 100,000 WebSocket connection attempts in a single browser session.

### Root Cause

Two interacting React patterns: (1) inline functions are not referentially stable — each render produces a new function object even if the logic is identical; (2) `useEffect` uses `Object.is()` for dependency comparison — two functions that do the same thing are not `===` equal if they are different instances. The hooks were written correctly for pure data dependencies but callbacks are not data.

### Fix

Store callbacks in refs that are updated on every render. The WebSocket `useEffect` uses the refs, not the callbacks directly, and has an empty dependency array — it runs once on mount and never restarts:

```typescript
import { useEffect, useRef, useState } from 'react'

export function useStatusUpdates({ onUpdate, onError }) {
    // Refs always hold the latest callbacks without being deps
    const onUpdateRef = useRef(onUpdate)
    const onErrorRef  = useRef(onError)
    useEffect(() => {               // No deps: runs after every render to sync refs
        onUpdateRef.current = onUpdate
        onErrorRef.current  = onError
    })

    useEffect(() => {
        const ws = new WebSocket(url)
        ws.onmessage = (e) => onUpdateRef.current?.(data)  // Uses ref, not prop
        // ...
        return () => ws.close()
    }, [])  // Empty: runs once on mount, never restarts
}
```

### Lesson

**Never put callbacks/functions in `useEffect` dependency arrays unless they are guaranteed to be referentially stable.** The `useRef` pattern is preferred because it puts the stability guarantee inside the hook, where it belongs, rather than imposing a burden on every caller. Always audit all copies of a pattern when fixing one instance.

---

## E6. Next.js Module-Level `process.env` Reads Captured at Build Time

**Component:** `frontend/app/api/**/*.ts`
**Severity:** High — API key was never forwarded; all authenticated requests returned 401

### What Broke

The `BACKEND_API_KEY` read was placed at module level in all 4 Next.js API route handlers. The Docker image was built without `BACKEND_API_KEY` set in the build environment. Next.js evaluated the module-level expression during the build and inlined `''`. Every container started from that image sent an empty key regardless of what `.env` contained at runtime.

### Root Cause

Next.js API route modules are compiled — module-scope expressions that can be statically resolved (including `process.env` reads without a `NEXT_PUBLIC_` prefix) may be captured at build time depending on how the bundler tree-shakes the output. Variables needed at runtime must be read inside the handler function to guarantee a fresh `process.env` lookup per request.

### Fix

```typescript
// WRONG — evaluated once at build time
const BACKEND_API_KEY = process.env.BACKEND_API_KEY || ''

// CORRECT — evaluated on every request
export async function POST(request: NextRequest) {
  const BACKEND_API_KEY = process.env.BACKEND_API_KEY || ''
  headers: { ...(BACKEND_API_KEY && { 'X-API-Key': BACKEND_API_KEY }) }
}
```

### Lesson

**Module-level `process.env` reads in Next.js API routes are a footgun: move secrets inside the handler where they're evaluated at request time, not build time.** The clue is that `docker compose exec frontend node -e 'console.log(process.env.BACKEND_API_KEY)'` prints the key correctly, but requests still 401 — that rules out the container env and points to the compiled output.

---

## A7. Google Takeout Zip Extraction Resets File mtime — Use Filename Date Parsing

**Component:** `worker/tasks.py`, `scripts/train_phase_classifier.py`, `scripts/predict_phases.py`
**Severity:** High — all 22,000+ exported photos received a `created_at` of the extraction date (March 22, 2026) instead of their true capture date

### What Broke

Google Takeout packages photos into zip archives. When extracted with standard tools (Windows Explorer, 7-Zip, `zipfile`), the OS sets each extracted file's `mtime` to the current time, not the original capture date embedded in the photo. The pipeline used `os.path.getmtime()` to set `created_at` in both the DB and the Qdrant payload — so every Construction Timeline photo appeared to be taken on the day of extraction. Date-range filters returned nonsense, and the classifier training script would have used the extraction date as the temporal label.

### Root Cause

Zip archives store a `last_modified` timestamp in the local file header, but this is set to the compression date when Google creates the archive — not the original EXIF capture date. `mtime` after extraction reflects neither capture date nor EXIF.

### Fix

Parse the capture date from the filename when available. Google Pixel photos follow the pattern `PXL_YYYYMMDD_HHmmssNNN.jpg`. A regex fallback chains EXIF → filename → `mtime`:

```python
import re

_PIXEL_DATE_RE = re.compile(r'PXL_(\d{4})(\d{2})(\d{2})_')

def parse_date_from_filename(path: str) -> datetime | None:
    m = _PIXEL_DATE_RE.search(os.path.basename(path))
    if m:
        return datetime(int(m[1]), int(m[2]), int(m[3]))
    return None
```

Training and prediction scripts also extract capture dates from filenames rather than relying on `mtime` or `created_at` in the Qdrant payload.

### Lesson

**Never trust `mtime` for media files that may have been extracted from an archive.** The safest priority chain is: EXIF `DateTimeOriginal` → filename date pattern → filesystem `mtime` as last resort. Document which source was used in the metadata payload so temporal bugs are debuggable. This applies to any bulk export workflow (Google Takeout, iCloud Export, OneDrive download).

---

## A8. ML Classifier Path Filter Must Cover All Source Folders, Not Just the Export Folder

**Component:** `scripts/predict_phases.py`
**Severity:** Medium — Foundation phase (Oct–Nov 2025) was underrepresented in classifier training and prediction because its photos lived in a different source folder

### What Broke

`predict_phases.py` filtered Qdrant records using a `should` (OR) filter on `file_path` containing `"Construction Timeline"` or `"DJI"`. This matched ~26,021 vectors correctly. However, Foundation-window photos (October 9 – November 6, 2025) taken before the Google Takeout export was organized were stored under `/mnt/source/Pre-Dec 2025/` — a different path entirely. These 18+ photos were never labeled, making Foundation the thinnest class (18 training examples) in an otherwise 26k-record dataset.

### Root Cause

The classification problem is defined by *date window* (when construction happened), but the path filter was defined by *folder name* (where photos happened to land after export). These two dimensions don't fully overlap — photos taken during construction but saved to a generic folder before the export was set up are excluded.

### Fix

Either (a) expand the path filter to include all folders that may contain construction-window dates, or (b) use a date-window filter (`created_at` range) instead of a path filter, after verifying that `created_at` is set from EXIF/filename (see A7). Option (b) is more robust because it captures any future photos regardless of folder structure.

### Lesson

**Define classification scope by the property that matters (time window, subject) rather than the folder structure.** Folder-based filters are convenient but brittle — photos move, exports are reorganized, and new source folders are added. When the labeling criterion is temporal (e.g., "photos taken during Phase 2"), use a date-range filter as the primary selector and treat folder names as a secondary sanity check.

---

## B11. Next.js API Proxy Routes Silently Drop Body Params Not Explicitly Destructured

**Component:** `frontend/app/api/search/route.ts`
**Severity:** High — `construction_phase` filter was sent by the frontend but never forwarded to the backend; filter appeared to work but had no effect

### What Broke

The `/api/search` Next.js proxy handler destructured only the original set of body parameters. When `construction_phase` was added to the frontend payload, the route handler received it in `body` but never destructured it — so the `JSON.stringify(...)` forwarded to the FastAPI backend simply omitted the field. The backend received a valid request, ran a search without the phase filter, and returned unfiltered results. No error was thrown anywhere in the chain.

### Root Cause

JavaScript spread (`...body`) is not used in the proxy — each forwarded field is explicitly listed. This is intentional (it prevents accidentally forwarding unknown or dangerous fields), but it means every new filter parameter requires a matching destructure line in the proxy handler or it is silently dropped.

### Fix

```typescript
// Add to destructuring:
const { query, limit = 20, threshold, min_similarity, dedup,
        audio_segment_type, audio_event_top, construction_phase } = body

// Add to forwarded body:
...(construction_phase && { construction_phase }),
```

### Lesson

**Every new filter added to the frontend payload requires a matching destructure + spread in the Next.js proxy route.** The silence of the failure (no error, just missing results) makes this easy to ship and hard to notice. Write an integration test that posts a request with the new field and asserts the backend received it, or use `...body` spread with an allowlist for known-safe fields.

---

## B12. Flower 2.0 CLI Breaking Change — `--broker` Must Precede the `flower` Subcommand

**Component:** `docker-compose.yml`
**Severity:** Medium — Celery Flower container crashed on startup with `"incorrectly specified celery arguments"`

### What Broke

The `flower` Docker service used the command form `celery flower --broker=... --purge_offline_workers=...`. After upgrading to `mher/flower:2.0.1`, the container exited immediately with:

```
Error: use -A to specify the app to use, or see --help for options.
Incorrectly specified celery arguments: ['--broker=redis://...']
```

### Root Cause

Flower 2.0 changed the CLI contract: `--broker` (and other Celery-level arguments) must come *before* the `flower` subcommand, because they are arguments to the `celery` parent command, not to `flower` itself. The pre-2.0 form happened to work by accident.

### Fix

```yaml
# WRONG (Flower <2.0 syntax)
command: celery flower --broker=redis://... --purge_offline_workers=1800

# CORRECT (Flower 2.0+)
command: >
  celery --broker=${CELERY_BROKER_URL:-redis://lumen-redis:6379/0}
  flower --purge_offline_workers=1800
```

### Lesson

**When upgrading Celery tooling, check the CLI argument order for subcommands.** Flower's changelog documents this as a breaking change in 2.0. The diagnostic is the exact phrase `"incorrectly specified celery arguments"` in the container logs — it's unambiguous once you know to look for it. Pin Flower to a minor version in `docker-compose.yml` to avoid surprise upgrades.

---

## C3. Qdrant Batch Operations — Individual `set_payload` Calls Don't Scale

**Component:** `scripts/predict_phases.py`
**Severity:** Medium — writing phase labels to 26,021 vectors took ~70,000 HTTP calls instead of ~6

### What Broke

The initial predict_phases implementation called `client.set_payload(...)` once per record inside a loop. With 26,021 records, this produced one HTTP round-trip per record — roughly 70,000 calls (including confidence values). At ~5ms per call on a local network, this would take ~6 minutes for payload writes alone.

Additionally, the initial Pass 1 used Python-side scanning (fetch all 84k records, filter in Python) instead of a server-side Qdrant filter, adding unnecessary data transfer.

### Fix

**Server-side filtering (Pass 1):** Use a Qdrant `Filter` with `FieldCondition(key="file_path", match=MatchText(...))` so only matching records are transferred:

```python
path_filter = Filter(should=[
    FieldCondition(key="file_path", match=MatchText(text="Construction Timeline")),
    FieldCondition(key="file_path", match=MatchText(text="DJI")),
])
records, _ = client.scroll(collection_name=..., scroll_filter=path_filter, with_vectors=True, limit=batch_size)
```

**Batch inference (Pass 2):** Build the full feature matrix, run `clf.predict_proba(X)` once, extract argmax in numpy:

```python
X = np.array([r.vector for r in valid_records])
probs_all = clf.predict_proba(X)  # shape: (N, 6)
idxs = probs_all.argmax(axis=1)
```

**Grouped payload writes:** Group point IDs by predicted phase, then call `set_payload` once per phase (6 calls max):

```python
from collections import defaultdict
phase_groups = defaultdict(list)
for pid, phase in zip(ids, phases):
    phase_groups[phase].append(pid)

for phase, pids in phase_groups.items():
    client.set_payload(collection_name=COLLECTION_NAME,
                       payload={"construction_phase": phase}, points=pids)
```

**Batch confidence writes:** Use `batch_update_points` with `SetPayloadOperation` for per-record confidence values:

```python
from qdrant_client.models import SetPayload, SetPayloadOperation
ops = [SetPayloadOperation(set_payload=SetPayload(
           payload={"phase_confidence": conf}, points=[pid]))
       for pid, conf in zip(ids, confs)]
client.batch_update_points(collection_name=COLLECTION_NAME, update_operations=ops)
```

### Lesson

**Always use server-side Qdrant filters for selective reads — never scan the full collection in Python.** For payload writes, group by value and call `set_payload` once per unique value, then use `batch_update_points` for heterogeneous per-record values. The rule of thumb: the number of Qdrant API calls should be proportional to the number of *unique values* being written, not the number of records.

Requires `file_path` to have a keyword payload index (`PayloadSchemaType.Keyword`) in Qdrant for `MatchText` to be efficient.

---

## D4. pillow_heif `register_heic_opener` Renamed to `register_heif_opener`

**Component:** `worker/ingest/ffmpeg.py`
**Severity:** Medium — HEIC/HEIF photo ingestion failed silently; all HEIC files were skipped

### What Broke

The worker imported `from pillow_heif import register_heic_opener` and called it at module load. With the installed version of `pillow_heif`, this raised `ImportError: cannot import name 'register_heic_opener'`, causing the entire ingest module to fail to import and all HEIC files to be skipped without an explicit error in the task log.

### Root Cause

`pillow_heif` renamed its Pillow integration function between versions:
- Old API: `register_heic_opener()`
- New API: `register_heif_opener()`

The rename reflects that the library handles the full HEIF container format (not just the HEIC subtype). The old name was removed in the installed version.

### Fix

```python
# WRONG
from pillow_heif import register_heic_opener
register_heic_opener()

# CORRECT
from pillow_heif import register_heif_opener
register_heif_opener()
```

### Lesson

**When an ingest module silently processes zero files of a specific type, check for import errors at the top of the module — they suppress the entire module without appearing in per-task logs.** Pin `pillow_heif` to an exact version in `requirements.txt` to prevent silent renames from breaking production. `register_heif_opener()` is the stable API going forward.

---

## D5. WSL2 Shutdown Breaks Docker Port Bindings — Restart Containers After `wsl --shutdown`

**Component:** Production Docker Compose stacks
**Severity:** High — all container ports became unreachable after a `wsl --shutdown` command; services appeared healthy but were inaccessible

### What Broke

After running `wsl --shutdown` (to free WSL2 memory), all Docker containers on both stacks were still listed as `Up (healthy)` but every port binding was silently broken. `curl http://localhost:8000/health` timed out. The Docker Desktop UI showed containers running normally.

### Root Cause

Docker Desktop on Windows uses WSL2 as its backend. When `wsl --shutdown` terminates the WSL2 kernel, Docker's internal networking (`vpnkit` / `wsl-vm` bridge) is torn down. When WSL2 restarts, Docker reconnects the containers to the daemon but does not automatically re-establish the `localhost` → container port forwarding rules. Containers keep their internal IPs and inter-container networking works, but host-side port bindings are gone.

### Diagnosis

1. `docker ps` shows all containers as `Up (healthy)` — misleading.
2. `curl http://localhost:8000` times out or connection refused.
3. `docker compose exec api curl http://localhost:8000/health` succeeds — confirms internal networking is fine.
4. Host-side bindings are the missing layer.

### Fix

Restart all containers after any `wsl --shutdown`:

```bash
# Restart each active stack
docker compose restart
```

A full `down` + `up` is not required; `restart` is sufficient to re-establish port bindings.

### Lesson

**`wsl --shutdown` is not safe to use when Docker containers need to remain accessible from the Windows host.** After any WSL2 restart (manual or Windows Update), run `docker compose restart` on every active stack before assuming services are reachable. Add this to any runbook covering Windows host reboots or WSL2 maintenance. Prefer `wsl --terminate <distro>` to shut down a specific distro without triggering a full Docker backend teardown.
