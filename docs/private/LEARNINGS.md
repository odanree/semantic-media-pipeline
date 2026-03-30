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
- [D — Testing & Production Safety](#d--testing--production-safety)
  - [D1. Mocked Tests Passing, Production Failing — qdrant-client API Mismatch](#d1-mocked-tests-passing-production-failing--qdrant-client-api-mismatch) `[BQ: The Complex Debug]`
  - [D2. Schema Drift — init-db.sql ↔ Migrations Diverge](#d2-schema-drift--init-dbsql--migrations-diverge) `[BQ: The High Bar]`
- [E — Architecture & Code Quality](#e--architecture--code-quality)
  - [E1. Voting System — Separation of Concerns for Testability](#e1-voting-system--separation-of-concerns-for-testability) `[BQ: The Innovation]`
- [F — Data Integrity & Defensive Programming](#f--data-integrity--defensive-programming)
  - [F1. Qdrant Dimension Mismatch — Startup Safety](#f1-qdrant-dimension-mismatch--startup-safety) `[BQ: The Failure]`

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

## Glossary

- **At-least-once delivery:** Message may be delivered multiple times; idempotent design required
- **CLIP:** Vision + language model; produces embeddings for images and text
- **Celery:** Distributed task queue for async work
- **Integration test:** Tests against real external services, not mocks
- **Qdrant:** Vector database for semantic search
- **STAR format:** Situation, Task, Action, Result — structured story format for interviews
- **Throughput:** Operations/second (vs latency: single request time)
- **Vector dimension:** Size of embedding vector (768-dim CLIP, 512-dim CLIP, etc.); must match collection schema
