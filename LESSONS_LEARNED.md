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

## 7. Chrome ORB Blocks `<img>` Responses with Non-Image MIME Type

**Commits:** `bc6e4b3`

**Symptom**
All video thumbnails showed as broken images in the browser. Chrome DevTools
network tab showed `ERR_BLOCKED_BY_ORB` on every `/api/thumbnail` request.
Response times were 700ms–2.5s (not instant 4xx), meaning ffmpeg *was* being
called. CORS headers (`allow_origins=["*"]`) were already present and had
no effect on the error.

**What happened**
When ffmpeg failed (because the binary was missing — see Lesson 8), the
thumbnail endpoint called `raise HTTPException(status_code=500, ...)`. FastAPI
serialises `HTTPException` as `Content-Type: application/json`. Chrome's
**Opaque Response Blocking (ORB)** policy inspects the MIME type of responses
loaded into `<img>` tags and blocks any response that is not an image type,
regardless of the HTTP status code or CORS headers.

**Root cause**
ORB operates at the fetch layer, below CORS. A JSON body with status 200 would
also be blocked. The fix is not a header — it is ensuring the response body is
always a valid image, even in error cases.

**The fix**
Replace every `raise HTTPException` in the thumbnail endpoint with a PIL-
generated dark-gray placeholder JPEG:
```python
def _placeholder_jpeg() -> bytes:
    buf = io.BytesIO()
    PILImage.new("RGB", (320, 180), (30, 30, 30)).save(buf, format="JPEG", quality=40)
    return buf.getvalue()

# On any error path:
return Response(_placeholder_jpeg(), media_type="image/jpeg",
                headers={"Cache-Control": "no-store"})
```
The `<img>` tag always receives `image/jpeg`; ORB never fires. Error
observability is preserved via `log.warning()` instead of HTTP error codes.

**Interview talking point**
> "ORB is a browser security policy that CORS headers cannot override — it
> operates one layer below CORS on the fetch primitives. The invariant for
> any endpoint loaded into an `<img>` tag is: always return an image MIME
> type, even on failure. I now treat `raise HTTPException` in image-serving
> endpoints as a bug, not a pattern. Log the error server-side; return a
> placeholder client-side."

---

## 8. BuildKit Apt Cache Poisoning — Package in Dockerfile, Not in Container

**Commits:** `bc6e4b3` (force `--no-cache` rebuild)

**Symptom**
`ffmpeg` was present in the Dockerfile's `apt-get install` line (added in
PR #9). The image rebuilt. The container restarted. But
`docker exec lumen-api sh -c "which ffmpeg"` returned exit code 1 — the binary
was not in the running container.

**What happened**
Docker's BuildKit caches each `RUN` layer keyed on the layer's inputs (the
command string + parent layer hash). The `apt-get install` command string was
present in the Dockerfile before PR #9 (it installed `build-essential` and
`curl`). Adding `ffmpeg` to the same `RUN` command *changed the command
string*, which should have invalidated the cache — but the
`--mount=type=cache,target=/var/cache/apt` BuildKit cache mount caused the
old apt package state to persist on the host, so BuildKit served the stale
layer (without `ffmpeg`) from cache even though the command string changed.

**Root cause**
BuildKit's `--mount=type=cache` for apt persists the package cache between
builds. When the package list changes inside an existing `RUN` block, the
mount can satisfy `apt-get install` from the stale cache without downloading
or unpacking the new package — producing a layer that looks fresh but is not.

**The fix**
```powershell
docker compose build --no-cache api
```
Forces all layers to rebuild from scratch, bypassing both the BuildKit layer
cache and the apt cache mount. After the rebuild, `which ffmpeg` confirmed
`/usr/bin/ffmpeg`.

**When to use `--no-cache`:**
- After adding a new package to an existing `RUN apt-get install` block
- After upgrading a base image that packages depend on
- Whenever "it's in the Dockerfile but not in the container" describes your situation

**Interview talking point**
> "BuildKit's caching is aggressive by design — it's what makes incremental
> builds fast. But it's a two-edged sword: when the cache is stale, Docker
> tells you the build succeeded while silently serving an image that doesn't
> match your Dockerfile. The operational rule I now follow: after any change
> to a `RUN apt-get install` block, always rebuild with `--no-cache` and
> verify the binary is present with `docker exec` before restarting dependent
> services."

---

## 9. Blocking Proxy Encode in the Critical Pipeline Path (2 commits)

**Commits:** `41a3594`, `f5e4b6e` (see also Lesson #10 — cross-device failure discovered on first deploy)

**Symptom**
563 video records stuck in `processing` status. Zero vectors in Qdrant after
hours of the worker running. No errors in logs — workers were busy, tasks were
being consumed, but the pipeline produced no searchable output.

**What happened**
`process_video` called `apply_faststart()` synchronously before frame
extraction. For 4K source files (5–20 GB), this transcode operation took
**hours per file** (timeout ceiling up to 9.8 hours for a 20 GB movie).
With 6 Celery workers and 6 large files, all worker slots were occupied 100%
of the time encoding proxies. Frame extraction and Qdrant upserts — the
operations that actually produce search results — never ran. The pipeline was
doing real work but none of it contributed to the stated goal.

The proxy encode was also undifferentiated: a 20 GB H264 file was re-encoded
at the same cost as a non-H264 file, despite H264 → H264 needing only a
container remux (stream copy), not a transcode.

**Root cause**
Three compounding design decisions:
1. **Variable-cost blocking step placed before fast invariant steps** — proxy
   encoding cost scales with file size/duration (0s to 9h); frame extraction
   and embedding are comparatively fast. The ordering guaranteed starvation.
2. **No distinction by codec** — H264 sources don't benefit from re-encoding
   to H264; only the moov atom needs moving. Stream copy takes ~30s; transcode
   of the same file takes hours.
3. **No escape hatch for large files** — a 2-hour non-H264 movie queued
   indefinitely ahead of faster, higher-value work.

**The fix**
Three changes applied together:
```
Option 1: Decouple — generate_proxy dispatched async to 'proxies' queue
          process_video finishes in minutes regardless of source size

Option 2: Duration threshold — non-H264 files > PROXY_MAX_DURATION_SECS
          (default 3600s) are skipped; full movies don't block the pipeline

Option 3: Codec-aware routing — H264 sources use -c copy (stream copy, ~30s)
          only non-H264 sources pay the full transcode cost
```

**Interview talking point**
> "The lesson is about pipeline ordering: place cheap invariant operations
> before expensive variable-cost ones. The proxy encode was optional
> (non-fatal, best-effort) but was sequenced before the mandatory work that
> produced actual value. When you have a step whose cost can range from seconds
> to hours depending on input, that step belongs at the end of the chain or in
> a separate async lane — never before steps that must complete for the
> pipeline to make progress. I also learned to look at what workers are
> *actually doing* vs. what they *should* be doing: `docker exec` + `/proc`
> inspection showed 6 FFmpeg processes in a full-encode loop with no frame
> extraction ever starting."

---

## 10. `os.replace()` Fails Across Docker Volume Mount Points

**Commit:** `f5e4b6e`

**Symptom**
All `generate_proxy` tasks for HEVC source files failed immediately after FFmpeg
completed successfully. Flower showed `FAILURE` with 0 retries. Traceback:
```
OSError: [Errno 18] Invalid cross-device link:
  '/tmp/tmp5phpffa_.mp4' -> '/mnt/proxies/d/4K/...
```

**What happened**
`apply_faststart()` wrote the FFmpeg output to `tempfile.mktemp(dir="/tmp")`,
then called `os.replace(str(tmp_path), str(dest))` to move it to
`/mnt/proxies/...`. This always fails when source and destination are on
different filesystems — `os.replace()` is backed by POSIX `rename(2)`, which
is an atomic operation only valid within a single filesystem. Docker volume
mounts (`/tmp` on the container's overlay filesystem, `/mnt/proxies` on a
bind-mounted host path) are always different filesystems.

**Root cause**
Two compounding issues:
1. **`os.replace()` ≠ cross-device copy** — it is not a file copy; it is a
   filesystem rename. Use `shutil.move()` when source and destination may be
   on different mounts.
2. **`OSError` not in `autoretry_for`** — `autoretry_for=(FFmpegError,)` meant
   that OS-level errors went straight to FAILURE with 0 retries, bypassing the
   backoff-retry mechanism entirely. The fix was already deployed but the
   6 failed tasks needed manual re-dispatch.

**The fix**
```python
# Before — fails across volume mounts:
os.replace(str(tmp_path), str(dest))

# After — copy+delete fallback on cross-device move:
import shutil
shutil.move(str(tmp_path), str(dest))
```
Also updated `autoretry_for=(FFmpegError, OSError)` so future OS-level
errors retry with exponential backoff.

**Bonus: one-time scripts should not be committed**
The re-dispatch involved a one-time Python script with real file paths
hardcoded — a privacy risk. Added `.gitignore` patterns
(`scripts/retry_*.py`, `scripts/dispatch_*.py`, `scripts/*_local.py`) to
prevent operational one-offs from accidentally landing in the repo.

**Interview talking point**
> "`os.replace()` is POSIX `rename()` — atomic and instant within one
> filesystem, but always `EXDEV` (cross-device) across filesystems.
> Docker volume mounts are separate filesystems by definition. The rule:
> whenever source and destination paths could be on different mounts, use
> `shutil.move()` which falls back to copy+unlink. Also: narrow your
> `autoretry_for` to the exceptions you expect, but always include `OSError`
> for I/O-heavy tasks — OS-level I/O failures are transient more often than
> they are permanent."

---

## 11. `os.getenv('VAR', default)` Does Not Guard Against Empty String (1 commit)

**Commit:** `4a7f9a2`

**Symptom**
`process_video` tasks stuck in perpetual RETRY. No useful error in the
traceback — just `ValueError: invalid literal for int() with base 10: ''`.
The tasks retried on backoff but never progressed, consuming worker slots
indefinitely.

**What happened**
`KEYFRAME_RESOLUTION` and `EMBEDDING_BATCH_SIZE` were declared in
`docker-compose.second.yml` as:
```yaml
- KEYFRAME_RESOLUTION=${KEYFRAME_RESOLUTION:-224}
- EMBEDDING_BATCH_SIZE=${EMBEDDING_BATCH_SIZE:-96}
```
The host shell had these variables exported as empty strings
(`KEYFRAME_RESOLUTION=`). The `:-` default in shell substitution fills in a
default only when the variable is **unset or null**; an exported empty-string
variable is considered set, so `${KEYFRAME_RESOLUTION:-224}` expands to `""`.
The container received `KEYFRAME_RESOLUTION=""`. Then:
```python
resolution = int(os.getenv("KEYFRAME_RESOLUTION", "224"))
# os.getenv returns "" (var is set), not "224" (fallback only fires if unset)
# int("") → ValueError
```

**Root cause**
`os.getenv('VAR', 'fallback')` has the same semantics as shell `:-`: fallback
only activates when the key is absent from `os.environ`. An empty-string value
is returned as-is. Any subsequent `int()` or `float()` cast will raise.

Because `autoretry_for=(Exception,)` covers `ValueError`, the task retried on
exponential backoff indefinitely — never succeeding, never failing permanently.

**The fix**
```python
# Before — silently passes empty string to int():
resolution = int(os.getenv("KEYFRAME_RESOLUTION", "224"))

# After — empty string is falsy, falls back to default:
resolution = int(os.getenv("KEYFRAME_RESOLUTION") or "224")
```
Applied to all five affected variables across `tasks.py` and
`ingest/ffmpeg.py`.

**Interview talking point**
> "`os.getenv('VAR', default)` and shell `${VAR:-default}` share the same
> footgun: neither protects against an explicitly-set empty string. The
> Python `or` idiom is more defensive because it treats any falsy value
> (empty string, zero, None) as 'use the fallback'. For configuration values
> that will be passed to int() or float(), always use `os.getenv('VAR') or
> 'default'`. The bug was invisible until real data hit the pipeline because
> the dev environment had the variables unset rather than empty."

---

---

## 12. Video Player "Unknown error" — Four Overlapping Layers (4 fixes to resolve)

**Symptom**
Video player showed "Failed to load video / Unknown error" even after the
stream endpoint returned HTTP 200 with `Content-Type: video/mp4`.

**What happened — four separate layers, each masking the next**

*Layer 1 — ORB blocking (addressed first, correctly)*
The original endpoint raised `HTTPException(status_code=404)`, which FastAPI
serializes as `application/json`. Chrome's Opaque Response Blocking (ORB)
silently blocked the response for `<video>` tags because the MIME type was not
a video type. Fix: wrap the endpoint in `try/except` and return
`Response(placeholder, media_type="video/mp4")` on every error path.

*Layer 2 — Invalid placeholder bytes (first fix attempt was broken)*
The placeholder was implemented with a hardcoded hex string labeled
"minimal valid MP4". The hex was invalid — the browser parsed the 200 response,
detected the bytes were not a real MP4 container, and threw `MediaError` code 4
("Unknown error") with no further detail. The fix must be a real MP4 generated
by ffmpeg at startup, not crafted bytes:
```python
subprocess.run(["ffmpeg", "-f", "lavfi", "-i", "color=c=black:s=320x180:d=1",
                "-movflags", "+faststart", "-f", "mp4", "pipe:1"], ...)
```
Cache the result in a module-level variable — ffmpeg takes ~300ms on first
call and the output is deterministic.

*Layer 3 — Wrong API port in the browser URL*
`NEXT_PUBLIC_STREAM_URL` was absent from `frontend/.env.local`. The component
fell back to `http://localhost:8000` (lumen1) instead of `http://localhost:8001`
(lumen2). Lumen1 couldn't find the files and returned JSON 404, re-triggering
the ORB issue even though the error path was now handled. The symptom looked
identical to Layers 1 and 2.

*Layer 4 — Next.js production mode requires build-time injection*
Setting `NEXT_PUBLIC_STREAM_URL` in `docker-compose.yml` under `environment:`
has no effect in production mode. Next.js embeds `NEXT_PUBLIC_*` values
directly into the compiled JS bundle during `next build`. A runtime env var
arrives too late — the bundle was already built without it. Fix: pass it as a
Docker `ARG` before the `RUN npm run build` step:
```dockerfile
ARG NEXT_PUBLIC_STREAM_URL=http://localhost:8000
ENV NEXT_PUBLIC_STREAM_URL=${NEXT_PUBLIC_STREAM_URL}
RUN npm run build          # ← value is now embedded in the bundle
```
Supply the value in `docker-compose.yml` under `build.args:`, not
`environment:`.

**Root cause**
Four independent bugs shared a single symptom ("video won't play"). Each fix
revealed the next layer. Debugging required observable checkpoints at every
layer: `curl` directly against the API to confirm bytes, `docker exec` to
verify the env var inside the container, and checking whether the bundle
actually contained the expected URL string.

**The fixes**
1. All error paths in `/stream` return `Response(content, media_type="video/mp4")`
2. Placeholder MP4 generated by `ffmpeg` subprocess, cached in memory at startup
3. `NEXT_PUBLIC_STREAM_URL` added to `frontend/.env.local` for local dev runs
4. `Dockerfile` uses `ARG`/`ENV` before `npm run build`; compose passes it via `build.args`

**Interview talking point**
> "The debugging was a case study in symptom convergence: four completely
> different root causes all produced the same MediaError code 4. I learned to
> add a checkpoint at each architectural boundary — curl against the API to
> inspect raw bytes, verify env vars inside the container, check the built
> bundle's source map — rather than cycling through guesses at the code level.
> The most counter-intuitive part: runtime `environment:` vars are silently
> ignored by Next.js for `NEXT_PUBLIC_*` in production. The value gets baked
> into the JS at `next build` time; no container env var can change it after the
> fact. I now always use Docker `ARG` for anything a Next.js prod build needs."

---

## Cross-Cutting Themes

| Theme | Issues |
|---|---|
| **Silent failures from broad exception handlers** | #3 (faststart), #2 (streaming 404) |
| **Default configurations assume a workload class** | #4 (Celery concurrency), #5 (chunk size) |
| **Layer-by-layer debugging** | #2 (URL → proxy → build), #1 (API method names) |
| **Fixed constants that should scale with data** | #6 (FFmpeg timeout), #5 (chunk size) |
| **Read-only vs. read-write contract violations** | #3 (`:ro` mount + write attempt) |
| **Browser security policies below CORS** | #7 (ORB blocks non-image `<img>` responses) |
| **Build tooling caches can lie** | #8 (BuildKit serves stale apt layer) |
| **Variable-cost blocking steps before invariant fast steps** | #9 (proxy encode starvation) |
| **`os.replace()` is a rename, not a copy — fails cross-device** | #10 (cross-device volume mount) |
| **`os.getenv('VAR', default)` does not protect against empty string** | #11 (empty env var int cast) |
| **`NEXT_PUBLIC_*` in Next.js prod = build-time only; use Docker `ARG`** | #12 (wrong stream URL) |
| **Placeholder media must be real bytes — crafted hex is almost never correct** | #12 (invalid MP4 stub) |
| **Symptom convergence: multiple independent bugs → identical error** | #12 (video player layers) |

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

7. **`<img>` endpoints must always return an image MIME type — ORB operates
   below CORS and cannot be overridden with headers.**
   Return a placeholder image on error; log server-side.

8. **After changing `apt-get install` in an existing `RUN` block, rebuild
   with `--no-cache` and verify with `docker exec`.**
   BuildKit's layer cache can serve the pre-change image silently.

9. **Order pipeline steps by cost: cheap invariants first, variable-cost
   optionals last (or async).** A best-effort step with unbounded cost placed
   before mandatory fast steps will starve the pipeline under real-world data.
   Use `/proc` inspection to verify workers are doing the right work, not just
   any work.

10. **`os.replace()` is `rename(2)` — use `shutil.move()` whenever source and
    destination may be on different mount points.** Docker volume mounts are
    always separate filesystems. Also include `OSError` in `autoretry_for` for
    any task that performs I/O between mounts.

11. **`os.getenv('VAR', 'default')` returns `''` when the variable is set but
    empty — use `os.getenv('VAR') or 'default'` for any cast to `int`/`float`.**
    An empty-string env var causes `int('')` → `ValueError`, which with
    `autoretry_for=(Exception,)` results in infinite retries with no progress.
12. **`NEXT_PUBLIC_*` in a Next.js production Docker image is baked at `next build`
    time — `docker-compose` `environment:` vars arrive too late.**
    Pass values as `ARG`/`ENV` before the `RUN npm run build` step and supply
    them under `build.args:` in compose, not `environment:`. When multiple stacks
    share the same image with different ports/endpoints, each compose file needs
    its own `build.args` block to produce a distinct image.