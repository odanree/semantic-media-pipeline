# Lessons Learned — Semantic Media Pipeline

Issues that required multiple commits to resolve, with the root cause analysis
and the generalizable principle extracted from each one.

---

## 1. Qdrant Client API Mismatch (3 commits to fix)

**Commits:** `836c524` → `2afd4ee` → `de9a591`

**Symptom**
Search returned 500 errors immediately after the feature appeared to work in
isolation. The method name looked right but the call failed at runtime.

**What happened**
The code was written against the Qdrant client docs, but the installed package
version (`qdrant-client==1.17.0`) had renamed and restructured its search API
multiple times across minor versions:
- Attempt 1: `.search_points()` → `AttributeError: no such method`
- Attempt 2: `.search_vectors()` → `AttributeError: no such method`
- Attempt 3: `.query_points()` with correct payload shape → ✓

**Root cause**
Qdrant's Python client does not follow semantic versioning strictly. Method
names changed between 1.x minor versions without deprecation warnings. The
online docs were ahead of the pinned package version.

**The fix**
Pin the exact client version in `requirements.txt` AND verify the installed
version's actual API surface (either via `help()` in a REPL or reading the
changelog for the pinned version, not the latest docs).

**Interview talking point**
> "I learned to treat third-party SDK docs with suspicion unless I'm verifying
> against the exact installed version. The canonical source of truth is
> `help(client)` or the GitHub tag for the pinned release, not the latest
> hosted docs. I now always pin transitive dependencies and note the version
> in a comment next to the call site."

---

## 2. Video Streaming 404 — Silent Routing Conflict (4 commits to fix)

**Commits:** `45892fa` → `221ea8b` → `8acb4e2` → `d8e5bb7`

**Symptom**
The video player showed a black screen with a 404. The file path was correct.
The file existed on disk. The API was running.

**What happened**
- The original route used a path segment (`/api/stream/{path}`) but the file
  path contained `/` characters, which FastAPI treated as nested path segments
  and couldn't match.
- Switching to a query param (`/api/stream?path=`) fixed the 404, but video
  still buffered — because requests were going through the Next.js API proxy,
  which reads the entire response before forwarding, defeating streaming.
- Moving to direct browser→FastAPI (`NEXT_PUBLIC_STREAM_URL`) fixed buffering,
  but a TypeScript build error appeared: `streamBase` was declared at module
  scope (outside the React component function body), which Next.js's build
  rejects because `process.env` access outside components isn't guaranteed.
- Final fix: moved `streamBase` inside the `VideoPlayer()` function body.

**Root cause**
Three separate layers each had their own failure mode: URL routing, proxy
buffering, and build-time environment variable scoping. Each fix revealed the
next layer.

**The fix**
1. Query param for paths containing `/` (never path segments for file paths)
2. Browser-direct URL for streaming (never route binary streams through a
   Node.js proxy)
3. `process.env` reads inside function/component bodies in Next.js, not at
   module scope

**Interview talking point**
> "Debugging this taught me to treat streaming as a distinct concern from
> regular API calls. A proxy that works perfectly for JSON will silently
> destroy streaming because it buffers the full response body. I also learned
> to decompose 'video doesn't play' into three separate questions: is the URL
> reachable? is the response being proxied? is the client receiving bytes
> incrementally? Each question has a different diagnostic tool — network tab,
> curl with --no-buffer, and a Range request."

---

## 3. apply_faststart() Silently No-Op on Every File (2 commits + discovery script)

**Commits:** `196e3a5` → `aee8a9c` (+ ephemeral `check_faststart.py` script)

**Symptom**
The code ran without errors. Logs showed no warnings. But zero files were being
optimized. The non-fatal `try/except` in `tasks.py` swallowed the real error.

**What happened**
`apply_faststart()` wrote a temp file to `/tmp`, then called `os.replace(tmp,
source_path)` — an atomic move of the result over the original. This raised a
`PermissionError` because the source volume is mounted `:ro` (read-only) in
`docker-compose.yml`. The error was swallowed by a non-fatal `except Exception`
block in the calling code, so the function returned `False` silently and
processing continued as if nothing had happened. The issue was
**invisible in production logs**.

Discovery required a 64-byte binary scanner (`check_faststart.py`) to read MP4
atom headers directly and prove that 163 of 171 files still had `moov` at the
end.

**Root cause**
Two compounding problems:
1. **Overly broad exception handler in caller** (`except Exception` hides
   `PermissionError`)
2. **Incorrect write target** (trying to write beside a `:ro` mounted file)

**The fix**
- Write the tmp output to `/tmp` (always writable), not alongside the source
- Catch `PermissionError` *separately and explicitly* with an actionable log
  message rather than letting it fall into the generic handler
- Long-term: adopted the proxy sidecar pattern — worker writes to a separate
  `:rw` volume, source never touched

**Interview talking point**
> "This is a classic silent failure pattern. A broad `except Exception` is
> essentially a lie detector malfunction — it tells you 'everything is fine'
> when something quietly went wrong. The lesson: non-fatal exception handlers
> should catch only the specific exception types they expect and always log
> enough context to reconstruct what happened. I now treat 'non-fatal' as
> 'must still be observable,' not 'can be ignored.'"

---

## 4. Worker RAM Thrash — Load Average 23.75 (2 commits to stabilize)

**Commits:** part of `8332525`, follow-up tuning

**Symptom**
The host machine became unresponsive during batch ingestion. `uptime` showed
load average 23.75 on a machine with 24 logical cores — effectively 100% CPU
contention. Individual tasks completed, but overall throughput collapsed.

**What happened**
Celery defaulted to `--concurrency=24` (one worker per CPU core). Each worker
loaded its own copy of the CLIP model into RAM (~600 MB) and also spawned
FFmpeg subprocesses (~400 MB each for 4K video). With 24 workers:
- CLIP alone: `24 × 600 MB = 14.4 GB`
- FFmpeg peaks: `24 × 400 MB = 9.6 GB`
- Combined: ~24 GB+ — exceeding available RAM → heavy swap → thrashing

**Root cause**
Default Celery concurrency is based on CPU count, which is correct for
CPU-bound tasks but catastrophically wrong for memory-heavy ML workloads.
Each worker was also never recycled, so PyTorch and FFmpeg memory leaks
accumulated over the lifetime of the process.

**The fix**
```
CELERY_CONCURRENCY=4              # floor(free_RAM_GB / ~2 GB per worker)
worker_max_tasks_per_child=50     # recycle child after 50 tasks (clears leaks)
worker_max_memory_per_child=1500000  # hard 1.5 GB ceiling per child
```
Load average dropped from 23.75 → 8.59.

**Interview talking point**
> "Default configurations assume a class of workload. Celery's default
> concurrency is designed for I/O-bound tasks where workers mostly wait.
> For memory-heavy ML inference, the right number is `floor(RAM / model_size)`,
> not CPU count. I also learned that long-running ML workers need periodic
> recycling — `max_tasks_per_child` is the Celery equivalent of connection
> pool recycling in database clients. Without it, PyTorch's CUDA allocator
> and FFmpeg's buffer pools cause gradual memory growth that only appears
> after hours of operation."

---

## 5. Streaming Throughput — 64 KB Chunks vs. 9P Latency (2 commits)

**Commits:** `3a3a8f7` (4 MB chunks), preceded by the `aiofiles` `FileResponse` swap

**Symptom**
Video loaded only 8 MB in 30 seconds. `curl` showed ~880 KB/s despite the files
being on a local volume. The server CPU was idle.

**What happened**
FastAPI's `FileResponse` (and Starlette's default streaming) uses 64 KB read
chunks. Docker Desktop on Windows mounts volumes via the WSL2 9P filesystem
protocol. Each 9P read has ~1–2 ms of round-trip overhead *regardless of chunk
size*. At 64 KB/read:

```
8 MB ÷ 64 KB = 128 reads × 2 ms = 256 ms overhead just for 9P calls
```

At 4 MB/read:
```
8 MB ÷ 4 MB = 2 reads × 2 ms = 4 ms overhead
```

The bottleneck was the *number* of filesystem calls, not bandwidth.

**Root cause**
The default chunk size was designed for local POSIX filesystems where syscall
overhead is measured in microseconds. On a virtual filesystem (9P, SMB, NFS),
the latency per call dominates, so you need far larger chunks to amortize it.

**The fix**
```python
STREAM_CHUNK_SIZE = 4 * 1024 * 1024  # 4 MB
```
Throughput jumped 14×. The comment in the code documents the reasoning so the
next engineer doesn't revert it thinking it's an oversight.

**Interview talking point**
> "Performance problems on virtualized or networked filesystems are latency
> problems, not bandwidth problems. The fix isn't to use a faster network —
> it's to make fewer, larger requests. This is the same principle behind
> database batch inserts vs. row-by-row inserts, or HTTP/2 multiplexing vs.
> repeated TCP handshakes. I learned to ask 'how many round trips does this
> make?' before assuming throughput is the limiting factor."

---

## 6. FFmpeg Timeout — Fixed Ceiling vs. Variable Content (PR #5)

**Commits:** squashed into `7dd035f`

**Symptom**
Long videos (>20 min) failed with `FFmpegError: timeout` even though the
machine had plenty of CPU and the file was intact. Short videos always worked.

**What happened**
The original timeout was a fixed `120` seconds. A 30-minute 4K Pixel 9 video
at 0.5 fps extraction took longer than 120s even under ideal conditions. The
timeout was a constant in the code with no way to configure it per-deployment.

**Root cause**
The timeout was chosen based on "feels reasonable for a short video" without
considering how it scales with content duration. The relationship is:

```
extraction_time ≈ video_duration × (1/fps) × decode_cost_per_frame
```

Longer video → more frames → linearly more time. A fixed timeout is guaranteed
to fail at some threshold.

**The fix**
```python
timeout = max(base_timeout, base_timeout + (video_duration * 1.5))
```
Base is configurable via `FFMPEG_TIMEOUT` env var. Duration is read from
`probe_media()` output before extraction starts.

**Interview talking point**
> "Timeouts should be proportional to expected work, not fixed guesses.
> A timeout set to 'what feels right for a test case' will fail in production
> on real-world data that doesn't match your test data. The pattern I use now:
> compute a baseline from the workload's own metadata (duration, file size,
> record count) and configure the constant multiplier as an env var so it can
> be tuned without a redeploy."

---

## Cross-Cutting Themes

| Theme | Issues |
|---|---|
| **Silent failures from broad exception handlers** | #3 (faststart), #2 (streaming 404) |
| **Default configurations assume a workload class** | #4 (Celery concurrency), #5 (chunk size) |
| **Layer-by-layer debugging** | #2 (URL → proxy → build), #1 (API method names) |
| **Fixed constants that should scale with data** | #6 (FFmpeg timeout), #5 (chunk size) |
| **Read-only vs. read-write contract violations** | #3 (`:ro` mount + write attempt) |

---

## General Debugging Heuristics Extracted

1. **Silence is not success.** A non-fatal handler that logs nothing is a
   production blindspot. Every suppressed exception should emit at minimum
   a `WARNING` with the exception type and the affected resource path.

2. **Check the installed version, not the latest docs.** Pin all SDK
   dependencies and link the pinned tag in comments at the call site.

3. **Streaming ≠ regular HTTP.** Any proxy, middleware, or server that
   accumulates a response body before forwarding it will destroy streaming.
   Validate streaming end-to-end with `curl --no-buffer` before trusting
   the browser network tab.

4. **Latency × call_count, not bandwidth, limits virtual filesystem I/O.**
   Increase chunk size before investigating bandwidth.

5. **Concurrency for ML workers = floor(RAM / model_footprint), not CPU count.**
   Add `max_tasks_per_child` to any worker running ML inference.

6. **Timeouts that don't scale with input size will fail on real data.**
   Always derive timeout from a measurable property of the work unit.
