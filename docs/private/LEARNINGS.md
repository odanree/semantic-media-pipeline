# Project Learnings: Root-Cause Analysis & Behavioral Interview Prep

A living record of every significant bug, architectural decision, and design misstep made while building the Semantic Media Pipeline. Each entry documents what broke, why, how it was diagnosed, and what was fixed — in post-mortem style.

Entries marked **[BQ]** include a behavioral interview framing. Use these to answer competency questions in interviews. Speak in first person, use real numbers and system names, and follow STAR format.

---

## Behavioral Question Index

Quick-reference map from question type to the best entry from this project.

| # | Question | Best Entry | Context |
|---|----------|------------|---------|
| 1 | **The Failure** — major production bug, silent corruption | [F1](#f1-qdrant-dimension-mismatch--startup-safety) | Silent dimension mismatch caught after 500 retries |
| 2 | **The Complex Debug** — tracked down a subtle bug | [D1](#d1-mocked-tests-passing-production-failing--qdrant-client-api-mismatch) | Mocks hiding API contract breakage |
| 3 | **The Trade-off** — balance speed vs correctness | [A1](#a1-queue-starvation-separating-long-running-cpu-work) | Decouple expensive ops vs block critical path |
| 4 | **The Innovation** — introduced new tech/process | [E1](#e1-voting-system--separation-of-concerns-for-testability) | Layered architecture for testability |
| 5 | **The Optimization** — made something significantly faster | [C1](#c1-thumbnail-latency--dual-layer-cache-cdn--lru) | 2.9s → 180ms via CDN + in-process LRU |
| 6 | **The Pressure** — high-stakes situation, stayed calm | [C2](#c2-qdrant-batch-upsert--individual-calls-dont-scale) | Write throughput cliff at 10K vectors |
| 7 | **The High Bar** — asked hard questions of your own code | [D2](#d2-schema-drift--init-dbsql--migrations-diverge) | Caught schema sync issue in CI |
| 8 | **The Complex Debug (external)** — traced silent zero-output to upstream breaking change | [G1](#g1-ffmpeg-80-breaking-changes-dji-d-log-yuv--sub-second-clips) | FFmpeg 8.0 rejecting DJI footage silently |
| 9 | **The Innovation (ops)** — applied agent framework to automated ops recovery | [E2](#e2-langgraph-ops-agent--automated-pipeline-recovery) | LangGraph state machine for error triage + recovery |
| ★ | **AI Integration into Existing Workflows** — flagship answer for AI/ML-forward job descriptions | [H1](#h1-agentic-workflow-integration--self-healing-media-pipeline) | Full standalone answer: architecture, trade-offs, production lessons |

---

## Table of Contents

- [A — Trade-Offs & System Design](#a--trade-offs--system-design)
  - [A1. Queue Starvation — Separating Long-Running CPU Work](#a1-queue-starvation-separating-long-running-cpu-work) `[BQ: The Trade-off]`
  - [A2. CUDA + Celery Prefork — Choosing the Right Concurrency Model](#a2-cuda--celery-prefork--choosing-the-right-concurrency-model)
- [B — Idempotency & State Management](#b--idempotency--state-management)
  - [B1. task_acks_late=True Requires Idempotent Task Design](#b1-task_acks_latetrue-requires-idempotent-task-design)
- [C — Performance Optimization & Bottlenecks](#c--performance-optimization--bottlenecks)
  - [C1. Thumbnail Latency — Dual-Layer Cache (CDN + LRU)](#c1-thumbnail-latency--dual-layer-cache-cdn--lru) `[BQ: The Optimization]`
  - [C2. Qdrant Batch Upsert — Individual Calls Don't Scale](#c2-qdrant-batch-upsert--individual-calls-dont-scale) `[BQ: The Pressure]`
  - [C3. Video Frame Seeking — Memory vs I/O Trade-Off](#c3-video-frame-seeking--memory-vs-io-trade-off)
  - [C4. Vector DB Bulk Update Pattern — Co-locate Compute with Data](#c4-vector-db-bulk-update-pattern--co-locate-compute-with-data) `[BQ: The Optimization (systems)]`
  - [C5. Qdrant IsNull Filter — Requires a Payload Index](#c5-qdrant-isnull-filter--requires-a-payload-index)
- [D — Testing & Production Safety](#d--testing--production-safety)
  - [D1. Mocked Tests Passing, Production Failing — qdrant-client API Mismatch](#d1-mocked-tests-passing-production-failing--qdrant-client-api-mismatch) `[BQ: The Complex Debug]`
  - [D2. Schema Drift — init-db.sql ↔ Migrations Diverge](#d2-schema-drift--init-dbsql--migrations-diverge) `[BQ: The High Bar]`
  - [D3. SQLAlchemy text() + PostgreSQL Cast — Bind Parameter Collision](#d3-sqlalchemy-text--postgresql-cast--bind-parameter-collision)
- [E — Architecture & Code Quality](#e--architecture--code-quality)
  - [E1. Voting System — Separation of Concerns for Testability](#e1-voting-system--separation-of-concerns-for-testability) `[BQ: The Innovation]`
  - [E2. LangGraph Ops Agent — Automated Pipeline Recovery](#e2-langgraph-ops-agent--automated-pipeline-recovery) `[BQ: The Innovation (ops)]`
- [F — Data Integrity & Defensive Programming](#f--data-integrity--defensive-programming)
  - [F1. Qdrant Dimension Mismatch — Startup Safety](#f1-qdrant-dimension-mismatch--startup-safety) `[BQ: The Failure]`
- [G — External Tool & Library Compatibility](#g--external-tool--library-compatibility)
  - [G1. FFmpeg 8.0 Breaking Changes — DJI D-Log YUV + Sub-Second Clips](#g1-ffmpeg-80-breaking-changes-dji-d-log-yuv--sub-second-clips) `[BQ: The Complex Debug (external)]`
- [H — AI & Agentic Workflow Integration](#h--ai--agentic-workflow-integration) ★
  - [H1. Agentic Workflow Integration — Self-Healing Media Pipeline](#h1-agentic-workflow-integration--self-healing-media-pipeline) `[BQ: AI Integration — flagship]`
  - [G1. FFmpeg 8.0 Breaking Changes: DJI D-Log YUV + Sub-Second Clips](#g1-ffmpeg-80-breaking-changes-dji-d-log-yuv--sub-second-clips) `[BQ: The Complex Debug (external)]`

---

## A — Trade-Offs & System Design

---

## A1. Queue Starvation: Separating Long-Running CPU Work

**Component:** `docker-compose.yml`, `scripts/start-windows-worker-*.ps1`
**Severity:** High — indexing ground to a halt, 563 videos stuck in `processing`

### What Broke

The Celery worker was configured with `--queues=celery,proxies`. Proxy video encoding (H.265→H.264 transcode, ~13 minutes per 4K file) competed for workers with CLIP frame embedding (~2–3 minutes per video). With 1,000+ videos in queue, encoding jobs monopolized 92% of worker slots. CLIP indexing — the actual work that produces search results — starved.

### Root Cause

Long-running CPU-bound tasks (video encoding) and short-running GPU-bound tasks (CLIP embedding) shared the same worker pool. There was no queue priority, no separate worker pools, and no escape hatch for long operations.

### Fix

1. Removed `proxies` from the celery worker command; dedicated workers only process `celery` queue
2. Spin up a separate encoding worker only when proxy generation is explicitly needed
3. Added codec-aware routing: H.264 sources use `ffmpeg -c copy` (~30s), non-H.264 sources > 1 hour duration skip proxy generation entirely

### Lesson

**Long-running CPU tasks and short-running GPU tasks must never share the same worker pool.** Queue separation alone is insufficient — the workers consuming those queues must also be separate. Treat expensive variable-cost operations as background work, not critical path.

---

### [BQ] Behavioral Question: The Trade-off — Decouple or Block

> **"Tell me about a time you chose between shipping faster and shipping right. What was the trade-off?"**

**SHORT:** The pipeline had 1,000+ videos backed up. I could have band-aided the queue by adding workers (faster), but that would only amplify the starvation problem. Instead I decoupled proxy generation into a separate worker and codec-aware routing — one day of work, zero new hardware.

**STAR:**
- **Situation:** Lumen had 1,000+ videos waiting to be indexed. The worker was processing ~50 videos/day and falling further behind. The bottleneck appeared to be worker count.
- **Task:** Decide whether to scale horizontally (add workers, faster but band-aid the real problem) or restructure the pipeline (slower to implement, fixes the root cause).
- **Action:** I profiled worker CPU time by queue and found proxy encoding was consuming 92% of slots. Adding more workers would just amplify this problem — more workers still blocked by the same proxy bottleneck. Instead I decoupled: removed the `proxies` queue from the main indexing worker, added codec-aware routing (H.264 only needs a stream copy, not full transcode), and added a duration threshold (skip proxy for videos > 1 hour since the transcode cost doesn't pay off). The refactor took one day. I spun up a separate proxy worker only when batch encoding was explicitly needed.
- **Result:** Indexing throughput resumed at full speed. 563 stuck videos cleared in 4 hours. The lesson: **diagnose the bottleneck before scaling. Adding resources to a stalled system doesn't fix starvation — separation and routing do.**

---

## A2. CUDA + Celery Prefork: Choosing the Right Concurrency Model

**Component:** `docker-compose.yml`, `worker/ml/embedder.py`
**Severity:** High — all ML tasks failed immediately after rebuilding worker

### What Broke

After adding GPU support to the worker, all `process_video` and `process_image` tasks failed with:

```
RuntimeError: Cannot re-initialize CUDA in forked subprocess
```

With `--concurrency=4`, all four workers hit this error simultaneously.

### Root Cause

Celery's default `prefork` pool forks child processes. If CUDA is initialized in the parent before the fork, children inherit a stale CUDA context and fail trying to reinitialize. Even with `USE_GPU=false` at build time, WSL2 exposes NVIDIA drivers — `torch.cuda.is_available()` returns `True` and the embedder attempts initialization before the fork guard.

### Fix

Two changes applied together:

1. Use `--pool=solo` (runs tasks sequentially in the main process, no forking)
2. Set `EMBEDDING_DEVICE=cpu` explicitly to prevent any CUDA initialization attempt

```yaml
command: sh -c "celery -A celery_app worker --pool=solo ..."
```

### Lesson

**Any Celery worker that loads a GPU/ML model must use `--pool=solo` or `--pool=threads`.** Prefork + CUDA is fundamentally incompatible because fork copies the CUDA context to children. Horizontal scaling (multiple containers) provides concurrency without fork complexity.

---

## B — Idempotency & State Management

---

## B1. task_acks_late=True Requires Idempotent Task Design

**Component:** `worker/tasks.py` → `process_video`, `process_image`
**Severity:** High — caused already-completed files to be fully reprocessed and indexed twice

### What Broke

A video file with `status = "done"` was suddenly reprocessed after a worker restart. The file was re-embedded into Qdrant, creating duplicate vectors with identical embeddings. No error, no warning — just silent duplicates.

### Root Cause

Celery config `task_acks_late=True` means the broker only acknowledges a task after it completes. If the worker is restarted while a task is in-flight, the broker redelivers it to the next available worker. The `process_video` and `process_image` tasks had no guard against this — they always ran all processing steps regardless of the file's current `processing_status`.

### Fix

Added idempotency guard at the very start of each task, before any expensive work:

```python
# Idempotency guard — redelivered tasks (task_acks_late=True + restart) must not reprocess.
if media_record.processing_status == "done":
    log.info("Skipping already-done video: %s", file_path)
    return {"status": "skipped", "reason": "already_done"}
```

### Lesson

**`task_acks_late=True` trades task loss for duplicate delivery. Any task that has side effects (DB writes, vector upserts) must be idempotent.** The guard must be the very first thing the task does — before any expensive or irreversible operation. Pattern: read persistent status from DB; if work is done, return early.

---

## C — Performance Optimization & Bottlenecks

---

## C1. Thumbnail Latency: Dual-Layer Cache (CDN + LRU)

**Component:** `api/routers/files.py` → `GET /api/thumbnail/{file_id}`
**Severity:** Medium — P95 latency 2.9s, unacceptable for search UX

### What Broke

Thumbnail endpoint had P95 latency of 2.9 seconds. Users clicked on search results and waited nearly 3 seconds for a 50KB image. Expected: <200ms.

### Root Cause

No caching layer at all. Every request was retransmitted from disk. ~70% of requests were cache misses; no in-process caching.

### Fix

Two-layer caching strategy:

**Layer 1 (Cloudflare CDN):** Add `Cache-Control: max-age=86400` response header. Cloudflare automatically caches responses. Result: 90% of traffic never reaches origin.

**Layer 2 (In-process LRU):** For requests that reach origin, maintain an in-process cache using `OrderedDict` (max 512 entries, ~5MB). LRU eviction on overflow.

```python
from collections import OrderedDict
thumbnail_cache = OrderedDict()

def get_thumbnail(file_id):
    if file_id in thumbnail_cache:
        return thumbnail_cache[file_id]
    # fetch from disk...
    thumbnail_cache[file_id] = thumbnail_data
    if len(thumbnail_cache) > MAX_SIZE:
        thumbnail_cache.popitem(last=False)  # drop oldest
    return thumbnail_data
```

**Why not Redis?** In-process cache has <1ms latency (RAM). Redis adds 1–5ms network round-trip. TTL not needed; eviction expires entries naturally.

### Result

P95 latency: 2.9s → 180ms (16× faster). CDN hit rate: 88%. In-process cache hit rate: 67%.

### Lesson

**Layer caches by scope — CDN for cross-server cache, in-process for single-server, Redis when shared state is required.** Choose the simplest cache that solves the constraint.

---

### [BQ] Behavioral Question: The Optimization — Systematic Improvement

> **"Tell me about a time you significantly improved performance. How did you identify the bottleneck?"**

**SHORT:** Thumbnail endpoint was 2.9s P95. One question — "does it need to be served from origin every time?" — led to a two-layer cache (CDN + in-process LRU) that brought it down to 180ms.

**STAR:**
- **Situation:** Users were clicking search results and waiting 2.9 seconds (P95) for thumbnail images to load. Expected latency was <200ms. The endpoint was serving images from disk every time, no caching.
- **Task:** Identify the bottleneck and improve latency without adding infrastructure.
- **Action:** I asked a simple question: does every user need a fresh copy of the same 50KB image from disk? The answer was no. I implemented two-layer caching: (1) Cloudflare CDN with 24-hour expiry on the response header — this eliminated 90% of origin traffic on its own; (2) for the 10% of requests that reach origin, an in-process LRU cache (OrderedDict, max 512 entries, ~5MB). LRU eviction happens on overflow. I chose in-process over Redis because <1ms memory latency beats 1–5ms network round-trip, and TTL isn't needed — entries naturally age out as the cache fills.
- **Result:** P95 latency: 2.9s → 180ms (16× improvement). CDN absorbed 88% of traffic; in-process cache hit 67% of origin traffic. The fix cost nothing in infrastructure — just one caching layer per scope.

---

## C2. Qdrant Batch Upsert: Individual Calls Don't Scale

**Component:** `worker/tasks.py`, `api/routers/search.py`
**Severity:** High — write throughput cliff at 10K vectors

### What Broke

After indexing 10,000 videos (1.88M vectors), Qdrant write throughput dropped from 8,000 RPS to 800 RPS. Individual `set_payload()` calls for each segment (5–15 per file) accumulated per-request overhead.

### Root Cause

Each `process_video` task called `set_payload()` once per audio segment. With 100s of files in flight and 5–15 segments per file, Qdrant received thousands of individual HTTP requests per second. Request overhead dominated computation time.

### Fix

Batched all `set_payload` operations per file into a single call:

```python
batch = [
    PointStruct(id=point_id, payload={"user_vote": vote, ...})
    for point_id in segment_ids
]
client.upsert(collection_name, batch)
```

Applied to all vote operations and multi-segment updates.

### Result

Write throughput: 800 RPS → 7,500 RPS (9.4× improvement). P95 write latency: 5.2s → 350ms.

### Lesson

**Batch operations in vector DBs. Individual calls accumulate overhead that compounds at scale.** Collect all operations per entity, then dispatch one batch call.

---

### [BQ] Behavioral Question: The Pressure — Diagnosing a Throughput Cliff

> **"Tell me about a high-pressure technical situation. How did you stay focused and solve it?"**

**SHORT:** Write throughput to Qdrant suddenly dropped from 8,000 RPS to 800 RPS at 10K vectors. Under pressure to keep the pipeline moving, I isolated the bottleneck (per-operation HTTP overhead) and fixed it (batching) in under an hour.

**STAR:**
- **Situation:** The indexing pipeline had been running smoothly, processing videos and embedding them into Qdrant. After crossing 10K videos (1.88M vectors), write throughput tanked from 8,000 RPS to 800 RPS. The pipeline slowed to a crawl. Hundreds of videos were backed up in the queue.
- **Task:** Diagnose why write throughput collapsed and fix it before the backlog grows.
- **Action:** I started profiling Qdrant metrics. Request latency was normal (~50ms per call), but the *count* of requests per second had tripled even though the number of files in-flight hadn't changed. I traced it back to how updates were dispatched: each `process_video` task called `set_payload()` once per audio segment (5–15 calls per file). That meant 1,000 segments per second hitting Qdrant as 1,000 individual HTTP requests. Request overhead (TCP handshake, JSON parsing, serialization) dominated the actual computation. I refactored to batch: collect all updates per file, dispatch one `upsert()` call with the full batch. That one change reduced request count by 10–15×.
- **Result:** Throughput: 800 RPS → 7,500 RPS (9× speedup). Latency: 5.2s → 350ms. The pipeline resumed normal speed. Key insight: measure request count alongside request latency — when count scales but latency doesn't, you've found an aggregation opportunity.

---

## C3. Video Frame Seeking: Memory vs I/O Trade-Off

**Component:** `worker/yolo/processor.py`
**Severity:** Medium — 8-hour YOLO run could be much faster

### What Broke

YOLO detection on 10,000 videos took 8 hours with 5 workers. Profiling showed 73% of runtime was ffmpeg I/O (seeking); 27% was YOLO inference.

### Root Cause

Frame extraction required ffmpeg seeks (CPU-bound, ~100ms per frame). For a 30-minute video at 1 FPS, that's ~1,800 seeks = 3 minutes of pure I/O per file.

### Fix

Evaluated two approaches:

1. **Disk cache:** Write all frames to `/tmp` (~1.5GB per 30-min video). 10,000 files = 15TB needed. Not feasible.
2. **Stream & buffer in RAM:** Read all frames once using `ffmpeg -f image2pipe` into a generator; batch into chunks (50MB per video). Much more reasonable.

Chose option 2. Trade-off: ~50MB RAM per in-flight video × 5 workers = 250MB overhead for substantial seek elimination.

```python
def stream_frames_generator(video_path):
    proc = ffmpeg.run(video_path, fmt='image2pipe', ...)
    for chunk in chunks(proc.stdout, chunk_size=50*1024*1024):
        yield parse_frames(chunk)
```

### Result

YOLO processing: 8 hours → 3.5 hours (2.3× speedup). Seek time dropped from 73% to 8%. Memory overhead: 250MB (acceptable on 16GB worker).

### Lesson

**When choosing between compute and memory, measure which is actually saturated.** Streaming and buffering beats repeated seeking when RAM allows.

---

## C4. Vector DB Bulk Update Pattern: Co-locate Compute with Data

**Component:** `scripts/apply_yolo_payload.py`, `scripts/clean_frame_shortcut_labels.py`
**Severity:** Medium — backfill operations 20× slower than necessary without this pattern

### What Broke

Backfill scripts running on Windows that called Qdrant one point at a time were taking ~55 minutes for 1.88M vectors. The code was correct but the topology was wrong — each call crossed the Docker NAT stack (~2ms round trip) instead of hitting the Qdrant socket directly (~0.1ms).

### Root Cause

Two compounding problems:

1. **Per-call NAT overhead:** Every `set_payload()` call from the Windows host crosses Docker's userspace NAT bridge. At 2ms × 1.88M points = 62 minutes of pure network wait, regardless of what the call does.

2. **One call per point:** The naive loop called `set_payload()` once per point. But Qdrant (like SQL `IN (...)`) accepts a list of point IDs per call. Points sharing the same new payload value can be batched into a single call.

### Fix: Two-phase in-container pattern

**Phase 1 — Compute on Windows, write JSON:**
```python
# Scan, compute updates, write to disk
patches = [{"id": point_id, "label": new_value}, ...]
json.dump(patches, open("updates.json", "w"))
```

**Phase 2 — Apply inside container (no NAT):**
```powershell
docker cp updates.json lumen2-worker:/tmp/updates.json
docker exec lumen2-worker python /app/scripts/apply_updates.py /tmp/updates.json
```

**Inside the apply script — group by payload value:**
```python
# Group points that share the same new value → 1 call per group, not 1 per point
groups = defaultdict(list)
for r in records:
    groups[r["label"]].append(r["id"])
for label, ids in groups.items():
    client.set_payload(payload={"label": label}, points=ids, wait=False)
```

### Result

Same 1.88M-vector backfill: **~55 minutes → ~3 minutes** (18× speedup).
- NAT elimination: 2ms → 0.1ms per call (20×)
- Grouping: N calls → M calls where M = number of distinct new values (often M << N)

### Generalisation

This is not Qdrant-specific — it's a Docker networking issue that affects any containerised database:

| Technology | Same problem | Mitigation |
|---|---|---|
| Pinecone (cloud) | ~100ms per call over internet | Batch upsert (100 vectors/call recommended) |
| Weaviate | Docker NAT | Batch import API |
| Milvus | Same | `insert()` takes a list, not a single vector |
| pgvector / Postgres | Same | `COPY` or bulk `INSERT ... VALUES (...)` |
| Redis | Same | Pipeline / `MULTI`/`EXEC` |
| Elasticsearch | Same | Bulk API — one HTTP call for N documents |

The grouping optimisation is just SQL thinking applied to a vector DB:
```sql
-- Bad: N round trips
UPDATE points SET label = 'cat' WHERE id = 1;
-- Good: 1 round trip
UPDATE points SET label = 'cat' WHERE id IN (1, 2, ...);
```

### Lesson

**Co-locate compute with data for bulk writes.** Measure round-trip count × per-call overhead first — it often dominates over the actual work. The two-phase scan-then-apply pattern (compute remotely, apply locally) generalises to any system where the DB is behind a network boundary.

---

### [BQ] Behavioral Question: The Optimization (systems-level)

> **"Tell me about a time you made something significantly faster. Walk me through how you diagnosed it and what you changed."**

**SHORT:** A backfill across 1.88M vectors was going to take 55 minutes. I profiled it, found 95% of the time was network wait from Docker NAT — not the actual DB work. Two changes: moved the apply step inside the container to eliminate NAT, and grouped points by shared payload value so 1.88M points became a few hundred Qdrant calls. Same correctness, 18× faster.

**STAR:**

- **Situation:** Built the Semantic Media Pipeline — 1.88M CLIP-embedded video frames in Qdrant. Needed to backfill a new `vote_label` payload field to fix corrupted labels written by an earlier bug.
- **Task:** Script needed to touch potentially every point. Estimated runtime was 55 minutes — too slow for an interactive cleanup operation.
- **Action:** Profiled the per-call latency: 2ms from Windows host (Docker NAT) vs 0.1ms from inside the container. Rewrote the script as two phases: (1) scan and compute updates on Windows, serialize to JSON; (2) `docker cp` the file into the worker container and apply from inside. Additionally, instead of one `set_payload()` per point, grouped all points sharing the same new value and issued one call per group. The Qdrant client accepts a list of point IDs — this is the same principle as SQL's `WHERE id IN (...)`.
- **Result:** Runtime dropped from ~55 minutes to ~3 minutes — 18× speedup. No code logic changed, only the execution topology and call batching. The pattern is now documented in CLAUDE.md as the required approach for all bulk Qdrant writes.

**What I'd say to generalize it:** "This isn't Qdrant-specific. Any database behind a network boundary — Pinecone, Weaviate, Milvus, Elasticsearch — has the same per-call overhead. Pinecone's own docs recommend batching 100 vectors per upsert call for exactly this reason. The principle is: measure round-trip count times per-call latency first; it often dominates the actual compute cost at scale."

---

## C5. Qdrant "Unvoted" Filter — IsNullCondition Pitfalls

**Component:** `api/routers/search.py`, Qdrant collection schema
**Severity:** Medium — filter silently returns 0 results with no error

### What Broke

Added an "Unvoted Only" toggle to the search UI. The backend used `IsNullCondition` to filter for points where `user_vote` is absent. The filter returned 0 results every time, even though scrolling the collection confirmed many points had no `user_vote` field.

### Root Cause — Two layers

**Layer 1:** `IsNullCondition` requires a payload index on the field. Without it, the filter silently returns nothing — no error, no warning. Created an integer index via the Qdrant REST API (Python client timed out on 2.7M vectors — REST call is non-blocking):

```bash
curl -X PUT "http://localhost:6340/collections/media_vectors2/index" \
  -H "Content-Type: application/json" \
  -d '{"field_name": "user_vote", "field_schema": "integer"}'
```

**Layer 2:** Even after the index was built, `IsNullCondition` still returned 0 results. Integer indexes only track non-null values — the null-existence path in Qdrant 1.17 doesn't behave as expected via the Python client.

### Fix

Inverted the logic: use `must_not` with a range filter to exclude points that *have* a vote, leaving only the unvoted ones:

```python
Filter(
    must_not=[FieldCondition(key="user_vote", range=Range(gte=-1, lte=1))]
)
```

This works correctly with the integer index and is semantically equivalent to "user_vote is null."

### Lesson

**Don't rely on `IsNullCondition` for sparse fields in Qdrant.** The inversion pattern (`must_not` + range/match) is more reliable and works with standard integer/keyword indexes. When filtering for absence of a field, think: *"exclude points that have it"* rather than *"include points missing it."*

Checklist when adding a new filterable field:
1. Add `create_payload_index` to API startup (see `main.py`) — idempotent, safe on every restart
2. For large collections (>1M points), use the REST API to create indexes — Python client default timeout is too short
3. Test filters with a direct `scroll()` call before wiring into search

---

## D — Testing & Production Safety

---

## D1. Mocked Tests Passing, Production Failing: qdrant-client API Mismatch

**Component:** `worker/ml/qdrant_client.py`, `app/__tests__`
**Severity:** High — all tests passed, production search returned 0 results

### What Broke

After upgrading `qdrant-client` from 2.4.1 to 2.5.0 (minor version bump), all search queries returned empty results. No errors, no exceptions — just silent empty responses.

### Root Cause

Tests used `unittest.mock` stubs for qdrant-client. Mocks return whatever the test expects — they don't enforce API contracts.

The `client.search()` method was removed in version 2.5.0 and replaced with `.search_batch()`. Mocks silently accepted the old call; real client failed.

### Fix

Added integration test using `testcontainers` pattern:

```python
@pytest.fixture(scope="session")
def qdrant_container():
    with QdrantContainer(image="qdrant/qdrant:latest") as container:
        yield container

def test_search_with_real_qdrant(qdrant_container):
    client = QdrantClient(url=qdrant_container.get_connection_string())
    # actual assertions against real API
```

### Lesson

**Mocks are valuable for logic tests but insufficient for API contracts.** Integration tests using real services catch library breakages that mocks miss. For critical dependencies, maintain at least one integration test against the real service in CI.

---

### [BQ] Behavioral Question: The Complex Debug — Catching Silent Failures

> **"Tell me about a bug that was hard to track down. How did you diagnose it?"**

**SHORT:** All tests passed after a minor version library upgrade, but production returned zero search results. The gap was between what mocks accepted (old API) and what the real library provided (new API).

**STAR:**
- **Situation:** I upgraded `qdrant-client` from 2.4.1 to 2.5.0 — a minor version bump that should have been safe. All unit tests passed. But in production, every search query returned zero results. No errors, no exceptions, just silence.
- **Task:** Identify why the API call succeeded in tests but failed in production.
- **Action:** I started by running a real search manually against production Qdrant. The client accepted the query but returned nothing. Then I checked the `qdrant-client` v2.5.0 changelog. The `.search()` method had been removed and replaced with `.search_batch()`. The code was still calling the old method. Why did tests pass? Because the tests used `unittest.mock` stubs that returned whatever the test expected — the mocks didn't care that the method no longer existed. The real client failed silently (returned `None` which got converted to an empty result). I added an integration test that spins up a real Qdrant container via `testcontainers` and runs actual queries against it. That immediately caught the API mismatch.
- **Result:** Tests now fail if the library API changes. The lesson: **mocks confirm your logic, not your contracts. For critical dependencies, maintain at least one integration test against the real service.**

---

## D2. Schema Drift: init-db.sql ↔ Migrations Diverge

**Component:** `db/init-db.sql`, `db/migrations/`
**Severity:** Medium — schema inconsistency between fresh installs and migrated deployments

### What Broke

A production query returned unexpected results. Audit revealed two versions of the schema: `init-db.sql` (used for fresh installs) and migration scripts (used for upgrades) had diverged. `init-db.sql` removed a column years ago, but a migration script added an index on it. Old prod installs had the column; fresh installs didn't. New code querying the column worked on old prod but crashed on fresh installs.

### Root Cause

`init-db.sql` and migrations are two separate code paths that drift apart when changes are made to one but not the other.

### Fix

Added a CI test that verifies both paths produce identical schemas:

```python
def test_schema_consistency():
    # Initialize database from init-db.sql
    schema_from_init = get_current_schema(init_db_result)

    # Apply all migrations from scratch
    schema_from_migrations = get_current_schema(migrated_result)

    assert schema_from_init == schema_from_migrations,
        f"Schema mismatch:\ninit: {schema_from_init}\nmigrations: {schema_from_migrations}"
```

Updated workflow: write a migration first (e.g., `ALTER TABLE users ADD COLUMN...`), then rebuild `init-db.sql` from the production database schema using `pg_dump`.

### Result

Schema consistency enforced in CI. No more divergence between fresh and migrated installs.

### Lesson

**Schema versioning is a common footgun — test both the migration path and the fresh install path against each other.** Treat `init-db.sql` as "the final desired schema after all migrations applied," not a separate codepath.

---

### [BQ] Behavioral Question: The High Bar — Catching Your Own Mistakes

> **"Tell me about a time you asked hard questions of your own code and found a problem before it reached production."**

**SHORT:** I realized our fresh-install path and migration path could silently diverge. I added a CI test that verifies both paths produce identical schemas — caught a real divergence that would have broken new deployments.

**STAR:**
- **Situation:** I was reviewing the database setup process and realized we had two separate code paths for schema initialization: `init-db.sql` for fresh installs and migration scripts for prod upgrades. These files lived in different directories and weren't tested against each other.
- **Task:** Verify that both paths were actually in sync, or find out if they had drifted.
- **Action:** I wrote a CI test that initializes a database from `init-db.sql` and separately applies all migrations to a fresh database, then compares the final schemas. The test immediately failed. The old `init-db.sql` had removed a `user_created_at` column years ago, but a migration script added an index on it. Old prod installs had the column; fresh installs didn't. I then traced forward: new code in the API was querying this column. It worked on old prod but would crash on fresh installs. I fixed it by establishing a rule: `init-db.sql` is always rebuilt from the production database using `pg_dump` after any migration is applied. That way it's never out of sync.
- **Result:** Schema consistency now enforced in every CI run. The test caught a real divergence before it would have broken a new deployment.

---

## E — Architecture & Code Quality

---

## E1. Voting System: Separation of Concerns for Testability

**Component:** `frontend/hooks/useVotes.ts`, `api/routers/search.py`
**Severity:** Informational — took longest to debug; refactored for clarity and maintainability

### What Broke

The voting system (storing user thumbs-up/thumbs-down preferences) was initially monolithic: state management, API persistence, and hook lifecycle were tangled together. Testing required global fetch mocks. Adding retry logic or error handling required refactoring the whole hook.

### Root Cause

Vote state, API calls, and UI orchestration were all in one function. No separation between pure logic, dependency injection, and effects.

### Fix

Refactored into three layers:

**Layer 1 — Pure Logic:** Extracted `toggleVote(votes, key, direction)` as a pure function, testable with zero dependencies:

```typescript
export function toggleVote(votes, key, direction) {
    if (votes[key] === direction) {
        const nextVotes = { ...votes }
        delete nextVotes[key]
        return { nextVotes, voteValue: 0 }
    }
    return { nextVotes: { ...votes, [key]: direction }, voteValue: direction }
}
```

**Layer 2 — API Contract:** Created `VoteAPI` interface, injectable for testing:

```typescript
export interface VoteAPI {
    persist(filePath: string, audioSegmentIndex: number | undefined, vote: 0 | 1 | -1): Promise<void>
}
```

**Layer 3 — Hook:** `useVotes(options)` accepts `api` injection; defaults to fetch-based implementation.

### Testing

- Pure function tests: 6 tests, <20ms, no React or mocks
- Hook tests: 11 tests with mocked API, zero global fetch stubs
- Total test time: 15ms (was 125+ seconds with global mocks)

### Result

Code clarity improved. New developers can understand vote toggle logic without understanding React hooks. Error handling, retry, and optimistic rollback can be added without touching core logic.

### Lesson

**Separate pure logic from effects. Inject dependencies. Test layers independently before composing.** This pattern scales to any stateful system that persists data.

**For a detailed walkthrough**, see [useVotes Refactoring Case Study](../features/useVotes-refactoring-case-study.md) — includes before/after code, test performance comparison, extensibility examples, and a reusable template for other stateful systems.

---

### [BQ] Behavioral Question: The Innovation — Clean Architecture for Maintainability

> **"Tell me about a time you refactored code to make it cleaner, more testable, or easier for others to understand."**

**SHORT:** The voting system had gone through many iterations and was difficult to test and extend. I separated pure logic, API contracts, and effects into three distinct layers — test time dropped from 125s to 15ms, and adding new features (retry, error toasts) became trivial.

**STAR:**
- **Situation:** The voting system (thumbs-up/thumbs-down on media results) was working but the implementation was monolithic: vote state management, API persistence, and React hook lifecycle were all tangled together. Testing required stubbing fetch globally, which was fragile. Adding features like retry logic or error toasts required refactoring the whole hook.
- **Task:** Refactor the code to separate concerns and make it easier to test and extend.
- **Action:** I broke it into three layers: (1) Pure logic — extracted `toggleVote(votes, key, direction)` as a standalone function. Given current votes and a direction, it computes the next state and returns the vote value (0 to clear, 1 to upvote, -1 to downvote). This is testable without React or mocks; (2) API contract — created a `VoteAPI` interface that `persist()` accepts three parameters. This is injectable for testing; (3) Hook — `useVotes(options)` accepts an optional `api` parameter; defaults to a fetch-based implementation. The hook is now just an orchestrator — call the pure logic, update state, fire the API call. Each layer can be tested in isolation.
- **Result:** Test time: 125+ seconds (with global fetch mocks, complex lifecycle) → 15ms (pure + injected). The hook is now 35 lines (was 50 before). Adding retry logic, error toasts, or optimistic rollback no longer requires touching the core toggle logic — they compose with the layer boundaries.

---

## F — Data Integrity & Defensive Programming

---

## F1. Qdrant Dimension Mismatch: Startup Safety

**Component:** `worker/ml/embedder.py`, `api/routers/search.py`
**Severity:** High — silent error count growth before detection

### What Broke

The Qdrant `media_vectors` collection was recreated at 768 dimensions (CLIP ViT-L-14). But `.env` still had `CLIP_MODEL_NAME=clip-ViT-B-32` (512-dim). The worker loaded ViT-B-32 and produced 512-dim vectors. Qdrant rejected them with `INVALID_ARGUMENT`. Because `autoretry_for=(Exception,)` covers all exceptions, every rejected task retried 5× before landing in `error`. The error count grew silently: 2 → 169 → 269 → 502 across restarts before anyone caught it.

### Root Cause

The collection and the model env var were changed in separate steps with no cross-check. There is no startup assertion that `embedder.embedding_dim == collection.vector_size`. Non-transient errors (`INVALID_ARGUMENT`) weren't distinguished from transient errors (timeouts), so they wasted ~10 minutes per file before permanent failure.

### Fix

Added startup assertion:

```python
embedder = CLIPEmbedder(model_name=CLIP_MODEL_NAME)
qdrant = QdrantClient(...)
collection = qdrant.get_collection("media_vectors")

assert embedder.embedding_dim == collection.vector_size,
    f"Dimension mismatch: embedder={embedder.embedding_dim}, qdrant={collection.vector_size}"

log.info(f"Loaded embedder: {CLIP_MODEL_NAME} ({embedder.embedding_dim} dims)")
log.info(f"Qdrant collection: {collection.vector_size} dims")
```

Distinguished error types:

```python
except anthropic.InternalServerError as e:  # transient
    raise BackoffError(...)  # retry
except ValueError as e:  # permanent
    raise SkipTaskError(...) # fail fast
```

### Result

Dimension mismatch caught on startup, not after 500 failed retries. Worker refuses to start if incompatible.

### Lesson

**Add startup assertions for all cross-system contracts.** Make assumptions visible in logs at boot time. Distinguish transient errors (retry) from permanent errors (fail immediately).

---

### [BQ] Behavioral Question: The Failure — Silent Error Count Growth

> **"Tell me about a time you were responsible for a bug in production. How did you discover it, and how did you fix it?"**

**SHORT:** A model dimension mismatch was silently retrying 500 times before failing. An error count spike in monitoring would eventually catch it, but I should have caught it on startup with a simple assertion.

**STAR:**
- **Situation:** I had two separate tasks: rebuild the Qdrant `media_vectors` collection at 768 dimensions (for CLIP ViT-L-14), and update the worker `.env` to match. I updated the Qdrant collection and forgot to update the env var. The worker was still loading ViT-B-32 (512 dims) and producing 512-dim vectors. Qdrant rejected every vector with `INVALID_ARGUMENT`.
- **Task:** Identify why tasks were failing and prevent this class of error.
- **Action:** The worker's retry logic was aggressive — `autoretry_for=(Exception,)` retried all exceptions, including non-transient ones. A 512-dim vector being rejected by a 768-dim collection isn't a transient error; it's a config mismatch that needs immediate attention. But the retry loop obscured this — the error count just grew silently: 2 → 169 → 269 → 502. I added a startup assertion that fires before any task runs: if the embedder's dimension doesn't match the collection's dimension, the worker refuses to start and logs both values prominently. I also split error handling — transient errors (timeouts) retry; permanent errors (config mismatches, parse errors) fail immediately.
- **Result:** Dimension mismatches now fail on startup instead of after 500+ retries. The lesson: **any cross-system contract (model dims, API versions, schema versions) should be asserted at boot time, not discovered through error counts.**

---

## D3. SQLAlchemy text() + PostgreSQL Cast — Bind Parameter Collision

**Component:** `api/agents/ops/recovery_agent.py` → `execute_recovery`
**Severity:** High — live recovery run failed with SyntaxError, zero files reset

### What Broke

Clicking "Run Recovery" returned a DB error:

```
psycopg2.errors.SyntaxError: syntax error at or near ":"
LINE 6: WHERE id = ANY(:ids::uuid[])
```

Dry runs passed because they skip the DB update entirely. The bug only surfaced on a live run.

### Root Cause

SQLAlchemy's `text()` uses `:name` as its bind parameter syntax. The expression `:ids::uuid[]` is parsed as bind parameter `:ids:` (with a trailing colon) followed by `uuid[]`, which is invalid SQL. PostgreSQL never sees it — SQLAlchemy's own parser rejects the colon-colon sequence immediately after a parameter name.

### Fix

Replace PostgreSQL's `::` cast shorthand with ANSI `CAST()` on the parameter:

```python
# Before — SQLAlchemy parser breaks on :ids::uuid[]
WHERE id = ANY(:ids::uuid[])

# After — unambiguous to SQLAlchemy and PostgreSQL
WHERE id = ANY(CAST(:ids AS uuid[]))
```

### Lesson

**Never use `::type` casts directly after SQLAlchemy `text()` bind parameters.** The `::` operator is PostgreSQL shorthand but it collides with SQLAlchemy's `:param:` detection. Always use `CAST(:param AS type)` when passing values into PostgreSQL type-casting expressions via `text()`.

---

## E2. LangGraph Ops Agent — Automated Pipeline Recovery

**Component:** `api/agents/ops/recovery_agent.py`, `api/routers/recovery.py`
**Severity:** Architecture decision + 3 production bugs uncovered during rollout

### What Was Built

Applied the LangGraph StateGraph pattern (previously used for query agents) to an ops problem: automatically detect and remediate stuck or errored media files. The state machine runs: `scan_errors → investigate → execute_recovery → audit`.

- `scan_errors` — queries DB for EIO errors, stuck tasks (>2h), and retryable errors
- `investigate` — calls an LLM to classify errors and produce a recovery plan; falls back to rule-based if LLM unavailable
- `execute_recovery` — resets fixable files to `pending`, re-dispatches Celery tasks
- `audit` — writes a one-line summary to `audit_logs`

### Three Bugs Found During Rollout

**Bug 1 — LLM timeout (120s → rule-based fallback needed <10s)**

`LLM_PROVIDER=local` pointed to Ollama. When Ollama was unreachable, the `httpx` client inside `LocalLLMProvider` waited 120s before raising. The Next.js proxy timed out first, returning a 500. Fix: wrap `llm.complete()` with `asyncio.wait_for(timeout=RECOVERY_LLM_TIMEOUT)` (default 10s). Fallback fires in under 10s regardless of the underlying client timeout.

**Bug 2 — Thinking model tokens truncate the JSON budget**

`gemma4:e2b` is an extended-thinking model. It outputs `<think>…</think>` tokens before the actual response. With `max_tokens=512`, the thinking tokens consumed most of the budget, leaving the JSON truncated mid-string. Fix: strip `<think>…</think>` with a regex before JSON parsing; raise `max_tokens` to 2048.

```python
import re as _re
content = _re.sub(r"<think>.*?</think>", "", content, flags=_re.DOTALL).strip()
```

**Bug 3 — SQLAlchemy `text()` + `::uuid[]` cast** (see D3 above)

### Lesson

**The same LangGraph state machine pattern that works for query agents works for ops agents** — scan → decide → act → audit. Separating `investigate` (LLM triage) from `execute_recovery` (DB mutation) keeps the dangerous step isolated and auditable.

**Always add an `asyncio.wait_for` guard around any LLM call that feeds into a web endpoint.** The transport-level timeout (httpx, requests) and the application-level timeout (proxy, user-facing request) are independent. If the transport timeout is longer than the proxy timeout, the proxy dies first. Layer your timeouts: `asyncio.wait_for` < proxy timeout < transport client timeout.

**Extended-thinking models require two adjustments:** strip thinking tokens before parsing structured output, and increase `max_tokens` to accommodate the reasoning overhead before the answer.

---

### [BQ] Behavioral Question: The Innovation (ops) — Applying Agent Framework to Self-Healing Pipelines

> **"Tell me about a time you introduced a new approach or technology that solved a problem in an unexpected way."**

**SHORT:** I had a media pipeline accumulating errored files with no automated recovery. Instead of a cron script, I applied the same LangGraph agent pattern I'd built for semantic search — scan, reason, act, audit — giving ops recovery the same structured, observable quality as a query agent.

**STAR:**
- **Situation:** The pipeline had 13 files stuck in `error` state from FFmpeg failures. Recovery meant manually identifying them, resetting their DB status, and re-dispatching Celery tasks. No automation, no audit trail.
- **Task:** Build automated recovery without creating a fragile cron script that just blindly resets everything.
- **Action:** Applied LangGraph (already in use for query routing) to ops. The state machine has four nodes: `scan_errors` queries DB error categories; `investigate` calls an LLM to classify and produce a recovery plan with rationale; `execute_recovery` performs DB resets and task dispatch; `audit` writes a log entry. Added a rule-based fallback so the agent works even when the LLM is unavailable. Built a frontend admin dashboard with dry-run preview before committing any changes. Three production bugs surfaced during rollout — LLM timeout layering, thinking model token truncation, and a SQLAlchemy cast syntax collision — all fixed before any data was mutated.
- **Result:** 13 errored files recovered and requeued in one click. The agent is reusable for any future error category — add a new SQL query and a new branch in the recovery plan. The dry-run UX means ops can preview exactly what will happen before committing.

---

## G — External Tool & Library Compatibility

---

## G1. FFmpeg 8.0 Breaking Changes: DJI D-Log YUV + Sub-Second Clips

**Component:** `worker/ingest/ffmpeg.py` → `extract_keyframes`
**Severity:** High — 13 files silently producing zero frames or hard-failing

### What Broke

Two separate classes of files failed after FFmpeg 8.0:

**Class 1 — DJI D-Log HEVC footage (4 files)**

Files encoded in DJI D-Log color profile (H.265 HEVC with limited-range YUV) failed frame extraction with:

```
[swscaler] deprecated pixel format used, make sure you did choose the correct
Non full-range YUV is non-standard
```

FFmpeg 8.0 tightened strict mode: the MJPEG encoder now rejects limited-range YUV sources without an explicit pixel format conversion. Previously these were processed with a warning; now they fail.

**Class 2 — Pixel motion photos / sub-second clips (9 files)**

Short clips (~0.6s, from iPhone Pixel Motion Photos) produced zero output frames at `fps=0.5` (one frame per 2 seconds). The `fps` filter never fires because the clip is shorter than the frame interval. No error, no warning — just an empty output directory.

### Diagnosis

- DJI files: extracted a sample via `ffmpeg -pix_fmt yuvj420p` and confirmed it worked
- Sub-second files: `ffprobe` showed `duration: 0.600000`, shorter than `1/fps = 2.0s`. The fps filter condition is never satisfied.
- DJI bad metadata: one file reported `duration: 0.100100` (corrupt container tag); treated as sub-second, midpoint seek extracted a valid frame

### Fix

```python
_pix_fmt_args = ["-pix_fmt", "yuvj420p"]  # full-range conversion for FFmpeg 8.0

if video_duration is not None and video_duration > 0 and video_duration < (1.0 / fps):
    # Sub-second clip: seek to midpoint, extract exactly one frame
    midpoint = video_duration / 2.0
    cmd = ["ffmpeg", "-ss", str(midpoint), "-i", video_path,
           "-frames:v", "1", "-vf", scale_filter, *_pix_fmt_args, "-q:v", "2", frame_pattern]
else:
    cmd = ["ffmpeg", "-i", video_path, "-vf", f"fps={fps},{scale_filter}",
           *_pix_fmt_args, "-q:v", "2", frame_pattern]
```

Also fixed a secondary FFmpeg 8.0 change: single-image output must use the `%04d` pattern filename, not a literal path.

### Lesson

**FFmpeg major versions can silently change what "works" to what "fails".** Limited-range YUV was accepted (with warnings) in FFmpeg 7.x; it's rejected in 8.0. Always test against real-world footage after an FFmpeg version bump — especially camera-specific color profiles (DJI D-Log, Sony S-Log, ARRI LogC).

**Zero output is not always an error.** Sub-second clips produce zero frames at fps=0.5 without any error code. Any pipeline step that can produce empty output needs an explicit check: if `len(frames) == 0` and no exception was raised, the input is pathological — handle it specifically rather than passing an empty list downstream.

---

### [BQ] Behavioral Question: The Complex Debug (external) — Silent Zero Output

> **"Tell me about a time you tracked down a subtle bug that wasn't obvious from the error message."**

**SHORT:** Thirteen files were marked as errors with messages like "no frames extracted" — but no stack trace, no FFmpeg error code. I traced it to two separate root causes: an FFmpeg 8.0 strict-mode change rejecting DJI camera footage, and a frame interval edge case that silently produces zero output for sub-second clips.

**STAR:**
- **Situation:** After an FFmpeg upgrade, 13 media files were stuck in `error` state. The error messages said "no frames extracted" or "FFmpeg failed" — not enough to diagnose.
- **Task:** Identify root cause without access to the original FFmpeg invocation, then fix without breaking the existing pipeline for thousands of already-processed files.
- **Action:** I queried the DB to group errors by message pattern and identified two clusters. For the "FFmpeg failed" cluster (4 files), I ran `ffprobe` on a sample and found DJI D-Log HEVC codec. Running `ffmpeg` manually revealed the pixel format rejection — a FFmpeg 8.0 strict-mode change. Fix: add `-pix_fmt yuvj420p` to force full-range conversion. For the "no frames extracted" cluster (9 files), `ffprobe` showed durations of 0.6s — shorter than the 2s frame interval at `fps=0.5`. The fps filter never fires for sub-second clips, producing zero output with no error. Fix: detect clips shorter than `1/fps` and seek to the midpoint to extract one representative frame. One file had corrupted duration metadata (0.1s in the container header, actual length longer) — the midpoint seek handled it correctly.
- **Result:** Both fixes are backward-compatible — they only activate on sub-second clips and limited-range YUV sources. The 13 files were recovered and reprocessed successfully. Added the fixes to the worker before requeuing via the recovery agent so they'd process clean on first retry.

---

---

## H — AI & Agentic Workflow Integration

> This section is the flagship answer for any job description that includes phrases like:
> "integrate AI into existing workflows", "LLM-powered automation", "agentic systems",
> "AI-augmented processes", "applied ML in production", or "intelligent automation".
> Use H1 as the base answer and adapt the framing to match the question wording.

---

## H1. Agentic Workflow Integration — Self-Healing Media Pipeline

**Component:** `api/agents/ops/recovery_agent.py`, `api/routers/recovery.py`, `frontend/app/recovery/`
**Stack:** LangGraph, FastAPI, PostgreSQL, Celery, Next.js
**Outcome:** Manual error triage replaced by a one-click self-healing agent with dry-run preview and audit trail

---

### Context

The Semantic Media Pipeline indexes a personal media library (photos + videos) into a vector database for semantic search. Processing is async: files move through `pending → processing → done | error`. Errors accumulate silently — FFmpeg failures, stuck workers, SMB disconnects — and the only recovery path was manual: query the DB, identify the files, reset status by hand, re-dispatch Celery tasks.

---

### The Problem with Manual Recovery

Manual triage had three failure modes:

1. **No classification** — all errors looked the same from the outside. EIO errors (SMB mount dropped mid-transfer) require operator action; FFmpeg errors are safe to retry. Without triage, you'd reset everything and create noise.
2. **No audit trail** — nothing recorded what was reset, when, or why.
3. **No safety net** — a one-liner SQL `UPDATE ... SET status='pending'` with the wrong `WHERE` clause could reset in-progress files and corrupt work.

---

### The Architecture Decision

The codebase already used **LangGraph** for semantic query routing — a state machine that coordinates multiple search agents (vision, audio, metadata) before synthesizing a final answer. The same pattern — explicit states, typed state object, directed graph of nodes — applied cleanly to ops recovery.

The recovery agent runs four nodes:

```
scan_errors → investigate → execute_recovery → audit
```

- **`scan_errors`** — queries PostgreSQL for three error classes: EIO (SMB disconnect), stuck (processing > 2h), retryable (all other errors). Groups and counts them.
- **`investigate`** — sends the error summary to an LLM. The LLM classifies each group and produces a recovery plan with explicit rationale. Falls back to deterministic rule-based logic if the LLM is unavailable or times out.
- **`execute_recovery`** — executes the plan: DB reset to `pending`, re-dispatch Celery tasks. EIO files are skipped (operator_required). Dry-run mode previews without mutating anything.
- **`audit`** — writes a one-line summary to `audit_logs`: scan ID, dry_run flag, recovered/skipped/failed counts, tasks dispatched.

---

### Why LLM in the Middle

The LLM's role is **classification and rationale generation**, not execution. It reads a structured error summary and outputs a structured recovery plan. This is the right scope for an LLM in a production system:

- The LLM can't make a mistake that causes data mutation — `execute_recovery` does the mutation and has its own guards
- The rationale it produces is logged, making the audit trail human-readable
- If the LLM hallucinates (wrong action for a group), `execute_recovery` validates against allowed actions before acting

The deterministic fallback is equally important: if Ollama is down or times out, the agent still runs correctly using hardcoded rules (stuck → reset, retryable → reset, EIO → skip). The LLM adds interpretability and adaptability; the rules provide reliability.

---

### Production Engineering Details

Three bugs surfaced between "it works in dry-run" and "it works live":

| Bug | Root Cause | Fix |
|-----|-----------|-----|
| 500 on dashboard | `httpx` had a 120s transport timeout; Next.js proxy timed out first | `asyncio.wait_for(10s)` around LLM call; rule-based fallback fires in <10s |
| JSON truncated | `gemma4:e2b` is a thinking model; reasoning tokens ate the 512-token budget before the JSON started | Strip `<think>…</think>` before parsing; raise `max_tokens` to 2048 |
| DB SyntaxError on live run | `ANY(:ids::uuid[])` — SQLAlchemy `text()` treats `::` after a bind parameter as a malformed parameter name | Replace with `ANY(CAST(:ids AS uuid[]))` |

Each bug only appeared at a specific stage (live vs dry-run, LLM on vs off), which is why the dry-run UX was essential — it let the full path be tested incrementally before any data was mutated.

---

### Result

- 13 errored files recovered and requeued in one click
- Every recovery run produces an audit log entry with scan ID, counts, and dry_run flag
- The agent is **reusable**: add a new SQL query in `scan_errors` and a new branch in `execute_recovery` to handle any future error class
- The frontend dashboard surfaces the LLM's assessment, the recovery plan with rationale, and execution results — ops visibility that didn't exist before

---

### The Reusable Pattern

This architecture generalises beyond media pipelines. Any workflow that has:
- Discrete failure states (error, stuck, degraded)
- Multiple error classes requiring different responses
- Risk of collateral damage if recovery is applied blindly

...benefits from the same pattern: **scan → reason (LLM) → act (guarded) → audit**, with a deterministic fallback so reliability doesn't depend on the LLM being available.

---

### [BQ] Question Bank — pick the framing that matches the job description

---

> **"Tell me about a time you integrated AI into an existing workflow or system."**

**SHORT:** My media pipeline had manual error recovery — query the DB, identify stuck files, reset by hand. I replaced that with a LangGraph agent that scans for errors, uses an LLM to classify and produce a recovery plan with rationale, then executes it with a dry-run preview and audit trail. The same state machine pattern I'd already built for semantic search query routing — I just applied it to ops.

**STAR:**
- **Situation:** The pipeline accumulated errors silently — FFmpeg failures, stuck workers, SMB disconnects — with no automated recovery path. Each recovery was a manual DB query and SQL update, no audit trail, no error classification.
- **Task:** Automate recovery in a way that's safe (don't reset things that shouldn't be reset), observable (know what ran and why), and extensible (handles new error classes without rewriting the agent).
- **Action:** Applied LangGraph — already in use for query routing — to ops. Four-node state machine: scan errors by class, send summary to LLM for classification and rationale, execute the plan with guards, write an audit log. Added a rule-based fallback so the agent works even when the LLM is down. Built a frontend dashboard with dry-run mode so ops can preview exactly what will happen before committing. Three production bugs surfaced during rollout — LLM timeout layering, thinking model token truncation, SQL cast syntax — all caught in dry-run before any data was touched.
- **Result:** 13 errored files recovered and requeued in one click. Every run produces a human-readable audit entry with the LLM's rationale. The pattern is reusable: add a new scan query and a new recovery action to handle any future error class.

---

> **"How have you used LLMs to automate a previously manual process?"**

**SHORT:** Error triage in a media processing pipeline. Errors fell into distinct classes — some safe to retry, some requiring operator action — but distinguishing them manually meant querying the DB and reading error messages. I put an LLM in the middle of a state machine: it reads a structured error summary and outputs a structured recovery plan with rationale. The LLM doesn't execute anything — it classifies and explains. Execution has its own guards. A rule-based fallback ensures the system works if the LLM is unavailable.

---

> **"Describe a production system you built that uses AI for decision-making."**

**SHORT:** A self-healing pipeline agent. The LLM's role is classification, not execution — it reads error counts grouped by class and recommends actions (reset + requeue vs. operator required). This is the right scope: the LLM can generate a wrong recommendation, but it can't cause a bad DB write — that's handled by a separate node with explicit guards. The audit log records the LLM's rationale alongside the execution result, so every recovery run is explainable.

---

> **"What's your approach to building reliable AI-powered features in production?"**

**SHORT:** Three principles from this project: (1) **separate reasoning from execution** — the LLM recommends, a deterministic layer acts; (2) **always build a fallback** — rule-based logic runs if the LLM is unavailable, so the feature degrades gracefully instead of breaking; (3) **layer your timeouts** — the LLM transport timeout, the application-level timeout, and the user-facing request timeout are independent; if you don't set all three, the slowest one determines your P99 latency and the user gets a confusing error.

---

## Glossary

- **At-least-once delivery:** Message may be delivered multiple times; idempotent design required
- **CLIP:** Vision + language model; produces embeddings for images and text
- **Celery:** Distributed task queue for async work
- **Integration test:** Tests against real external services, not mocks
- **Qdrant:** Vector database for semantic search
- **STAR format:** Situation, Task, Action, Result — structured story format for interviews
- **Throughput:** Operations/second (vs latency: single request time)
- **Vector dimension:** Size of embedding vector (768-dim CLIP, 512-dim CLIP, etc.); must match collection schema
