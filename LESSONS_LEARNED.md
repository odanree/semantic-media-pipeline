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

## 13. SMB Mount Is Read-Only on Remote Host — Two Failure Modes

**Branch:** `chore/worker-concurrency`

**Symptom A — frame cache write fails with `[Errno 30] Read-only file system`**
Mac worker tasks failed with:
```
[Errno 30] Read-only file system: '/mnt/frame_cache/...'
```
The directory `/mnt/frame_cache` inside the container mapped to
`/Volumes/lumen-media-1/frame_cache` on the Mac host. That path didn't exist.
When Docker Desktop can't bind-mount a non-existent host path it falls back to
mounting the parent — which here is the SMB share root, mounted read-only.

**Symptom B — can't create the missing directory**
Running `mkdir -p /Volumes/lumen-media-1/frame_cache` on the Mac to pre-create
the path also failed with `Read-only file system` — the SMB share itself is
mounted read-only on the Mac (`smb://` default credentials gave read-only
access to the share, even though the Windows NAS that hosts the share is
writable).

**What happened**
The `docker-compose.mac-worker.yml` assumed the Mac could write to the SMB
share in the same way the Windows worker writes to `J:\frame_cache` locally.
But the Mac accesses the same storage over the network via SMB, where the
mount permissions are controlled by the share's access-control list — in this
case read-only.

Attempting to fix it by creating the directory from the Mac side hit the same
wall: `EROFS` because the SMB mount is read-only. The directory can only be
created by a user with write access to the share (i.e., **from Windows,
not from the Mac**).

**Root cause**
Two compounding problems:
1. **Frame cache bind-mount path didn't exist** — Docker falls back to mounting
   the parent with whatever permissions the parent has.
2. **Per-worker cache placed on a shared network volume** — the frame cache is
   a local performance optimisation (avoids re-extracting frames for the same
   video on the same worker). It doesn't need to be shared. Putting it on a
   network drive introduced an unnecessary SMB write dependency.

**The fix**
Change the Mac worker's frame cache to a **Docker named volume** — local to
the Mac's Docker daemon, always writable, zero SMB dependency:
```yaml
# docker-compose.mac-worker.yml
volumes:
  - mac_frame_cache:/mnt/frame_cache   # was: /Volumes/lumen-media-1/frame_cache:/mnt/frame_cache

volumes:
  mac_frame_cache:   # Docker-managed, created automatically
```
Docker creates the named volume on first `up`. No host directory required.
Cache hits are still effective per worker; cross-worker cache sharing was never
implemented anyway.

**Complementary note — two distinct SMB stale-mount error signatures**
A stale or dropped SMB mount produces *two different* error types depending
on which library hits the mount first:

| Source | Exception | Signal |
|---|---|---|
| PIL / Python file open | `OSError(errno=5 EIO)` | `_is_eio()` chain walk |
| ffprobe subprocess | `FFmpegError('ffprobe failed: ')` | empty stderr after colon |

Both require **fast-fail** (no retry). Only the EIO path is currently handled
by `_is_eio()`. The `FFmpegError` path still triggers the full
`autoretry_for=(Exception,)` backoff loop (up to 5× retries, ~600s) before
permanent failure. A companion check is needed:
```python
# companion to _is_eio() — detects stale SMB via empty ffprobe stderr
def _is_stale_mount_ffprobe(exc: BaseException) -> bool:
    return isinstance(exc, FFmpegError) and str(exc).strip().endswith("ffprobe failed:")
```

**Interview talking point**
> "I hit two separate `[Errno 30]` walls in one session. The first was Docker
> falling back to a read-only parent mount when the bind-mount target didn't
> exist. The second was the SMB share itself being read-only on the Mac,
> so `mkdir` couldn't fix it either. The correct solution wasn't to fight the
> network share — it was to recognise that the frame cache is a per-worker
> local optimisation that never needed to be on a shared drive. Switching to
> a Docker named volume removed the dependency entirely. The broader lesson:
> before adding a network mount to a service's compose file, ask whether the
> data *must* be shared, and if not, keep it local."

---

## 14. CLIP Model / Qdrant Collection Dimension Mismatch — Silent Backlog (multi-session)

**Branch:** `chore/worker-concurrency`

**Symptom**
Stats API showed a growing `error` count with `INVALID_ARGUMENT` after the
Qdrant collection was recreated and the re-index was restarted. Workers were
running and completing tasks, so the pipeline *appeared* healthy. The error
count grew from 2 → 169 → 269 → 502 across restarts before it was caught.

**What happened**
The Qdrant `media_vectors` collection was deleted and recreated at 768 dimensions
to match `clip-ViT-L-14`. But `.env` still had `CLIP_MODEL_NAME=clip-ViT-B-32`
(512-dim), left over from an earlier configuration. After a `docker compose up`
the Windows worker loaded ViT-B-32 and produced 512-dim vectors, which Qdrant
rejected:
```
status = StatusCode.INVALID_ARGUMENT
details = "Wrong input: Vector dimension error: expected dim: 768, got 512"
```
Because `autoretry_for=(Exception,)` covers all exceptions, every rejected
task retried 5× before landing in `error` — the error message in the DB made
the cause clear, but nobody was watching the error column closely during the
first hours of the re-index.

A second issue surfaced at the same time: `stats.py` read
`info.vectors_count` from the Qdrant `CollectionInfo` object, but the
installed `qdrant-client` version had renamed it to `points_count`. This caused
the entire `/api/stats/summary` endpoint to return 500 (see also Lesson #1 —
Qdrant renames attributes across minor versions without deprecation warnings).

**Root cause**
1. **`.env` was not updated when the Qdrant collection dimension changed.** The
   collection and the model env var were changed in separate steps with no
   cross-check. The worker silently loaded the wrong model because there is no
   startup assertion that `embedder.embedding_dim == collection.vector_size`.
2. **Qdrant `INVALID_ARGUMENT` errors are not distinguishable from transient
   errors** by the current retry logic — `autoretry_for=(Exception,)` retries
   them the full 5×, wasting ~10 minutes of worker time per file before
   permanent failure.
3. **Stats API was broken** so the growing error count was only visible via
   direct Postgres queries, not the dashboard.

**The fix**
- Updated `.env`: `CLIP_MODEL_NAME=clip-ViT-L-14`
- Reset 502 `INVALID_ARGUMENT` errors to `pending` (all produced by the wrong
  model — none were genuine vector defects)
- Fixed `stats.py`: `getattr(info, 'points_count', None) or getattr(info, 'vectors_count', None)`
  for forward/backward compat with qdrant-client version changes
- Rebuilt both `worker` and `api` containers

**Prevention**
Add a startup assertion in `tasks.py` or `embedder.py`:
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
This makes the mismatch loud and immediate (worker fails to start) rather than
silent and gradual (tasks fail one by one over hours).

**Interview talking point**
> "When you change a schema — whether it's a database column type or a vector
> dimension — every producer of that schema must be updated atomically. In this
> case I changed the Qdrant collection and the model name in separate steps and
> missed updating `.env`, so the worker ran with the wrong model for hours.
> The fix was straightforward, but the real lesson is: add a startup assertion
> that checks the producer's output format against the store's expected format
> before any work begins. Fail loud at startup, not silently at scale."

---

## 15. Docker Disk Bloat — Three Independent Root Causes

**Branch:** `fix/frame-cache-container-path`

**Symptom**
`docker system df` reported:
```
Images       66.85 GB   (88% reclaimable)
Containers   22.26 GB   (0 reclaimable)
Local Volumes 58.63 GB  (0 reclaimable)
```
The containers number was the surprise — all containers were healthy with no obvious
extra writes. The local volume figure was expected due to proxy transcode output, but
the container layer size warranted investigation.

**What happened — three separate causes**

**Cause 1: Orphaned temp mp4 in worker writable layer (8 GB)**
A video proxy transcode task was interrupted mid-encode. `ffmpeg` had already written
a temp file at `/tmp/tmp<random>.mp4` inside the container. Because `/tmp` was not
mounted as a named volume, the file lived in the container's writable overlay layer
and survived container restarts indefinitely. It was invisible to normal ops monitoring
because no log or metric tracked `/tmp` fill rate.

**Cause 2: HuggingFace model re-downloaded on every rebuild (3.2 GB × per container)**
The API containers load a CLIP model at startup via the HuggingFace `transformers` /
`sentence-transformers` library. With `/root/.cache/huggingface` unmounted, each
`docker compose up --build` created a fresh container layer and re-downloaded the
full model into it. With two API containers (`lumen-api` and `lumen2-api`) rebuilding
multiple times per session, this accumulated ~10 GB of duplicate model blobs spread
across container layers.

**Cause 3: Second stack API missing `REDIS_URL` / `DATABASE_URL`**
A secondary issue: `docker-compose.second.yml` was missing `REDIS_URL` and
`DATABASE_URL` on `api2`, so its WebSocket status poller was querying lumen1's
database and publishing to lumen1's Redis channel. This didn't directly cause disk
bloat but compounded debugging time.

**The fixes**

1. **Orphaned tmp**: `docker exec lumen2-worker sh -c "rm /tmp/tmp*.mp4"` (immediate);
long-term: worker should `os.unlink()` temp files in a `finally` block, or `/tmp`
should be a named volume (`worker_tmp`) so it's bounded and inspectable.

2. **Model cache**: Mounted `hf_cache` named volume at `/root/.cache/huggingface` in
both `api` and `api2` services. `lumen2` references it as `external: true` so both
stacks share one download — model persists across all future rebuilds.

3. **Second stack env vars**: Added `REDIS_URL=redis://lumen2-redis:6379` and
`DATABASE_URL=postgresql://...@lumen2-postgres:5432/lumen2` to `api2`, and
`REDIS_URL` to `worker2`, so each stack talks only to its own infrastructure.

**Diagnosis commands**
```powershell
# Which container layer is large?
docker ps --format "{{.Names}}: {{.Size}}"

# What's inside a specific container filling space?
docker exec <container> sh -c "du -sh /tmp/* /root/.cache/* 2>&1 | sort -rh | head -20"

# Verify a volume is actually mounted (not writing to layer)
docker exec <container> sh -c "df -h /root/.cache/huggingface"

# Prune build cache without touching volumes or running containers
docker builder prune --keep-storage 5GB
```

**Interview talking point**
> "Container writable layer bloat is silent — it doesn't show up in application
> metrics, and `docker ps` doesn't report it by default. I learned to treat `/tmp`
> and model cache directories as named volumes from day one. The rule is: anything
> that grows over time and survives a crash goes on a named volume where it's visible
> to `docker system df`. Anything that *should* be cleaned up on restart goes on a
> `tmpfs` mount so the OS enforces the boundary."

---

## 16. Qdrant Collection Not Pre-Created — `VectorParams(size=None)` Silent Retry Loop (2 commits)

**Branch:** `feat/cloud-deploy`

**Symptom**
All 396 tasks stuck in `processing` with no errors and `celery inspect active`
showing workers busy. `done: 0` after several minutes. No errors in `top_errors`.

**What happened**
`ensure_qdrant_collection()` is called at the start of every `process_video`
task. It tries `qdrant_client.get_collection()` first; on failure it calls
`get_embedder().get_embedding_dimension()` to get the vector size and creates
the collection. On a fresh cloud deploy the collection had never been created.

The race condition: `get_embedder()` is lazy-loaded. In a freshly-forked Celery
child process the embedder may not be loaded yet when `ensure_qdrant_collection()`
runs. If `embedding_dim` is `None` at that moment, `VectorParams(size=None)`
raises a Pydantic `ValidationError` which `autoretry_for=(Exception,)` catches
— so every task retried on 1s backoff indefinitely:
```
Retry in 1s: 1 validation error for VectorParams
size
  Input should be a valid integer [type=int_type, input_value=None]
```

**Root cause**
Two compounding issues:
1. Collection was never seeded on the fresh server.
2. `ensure_qdrant_collection()` calls `get_embedder()` inside the task hot path
   where the embedder may not be initialised yet. `None` dimension → Pydantic
   error → infinite retry.

**The fix (immediate)**
Create the collection manually before triggering ingest:
```bash
curl -X PUT http://localhost:6333/collections/media_vectors \
  -H "Content-Type: application/json" \
  -d '{"vectors": {"size": 768, "distance": "Cosine"}}'
```

**The fix (permanent)**
Move collection creation to the Celery worker startup hook, after the embedder
is fully loaded — not inside a task that runs thousands of times:
```python
@app.on_after_configure.connect
def setup_qdrant(sender, **kwargs):
    embedder = get_embedder()  # fully loaded — embedding_dim guaranteed non-None
    try:
        qdrant_client.get_collection(QDRANT_COLLECTION_NAME)
    except Exception:
        qdrant_client.create_collection(
            collection_name=QDRANT_COLLECTION_NAME,
            vectors_config=VectorParams(size=embedder.embedding_dim, distance=Distance.COSINE),
        )
```

**Interview talking point**
> "A lazy-loaded global and a retry-on-all-exceptions loop are individually fine
> patterns. Combined, they create a silent infinite-retry: the lazy load isn't
> ready, the operation fails validation, the retry fires into the same race
> condition — forever. Infrastructure setup belongs in the worker startup hook,
> not the task hot path. By the time tasks run, the collection must already exist."

---

## 17. DB Schema Drift Between `init-db.sql` and Migration Scripts (3 commits)

**Branch:** `feat/cloud-deploy`

**Symptom**
After fixing the Qdrant issue, workers crashed immediately on every task:
```
sqlalchemy.exc.ProgrammingError: column media_files.embedding_started_at
does not exist
```
Stats showed `processing: 396`, `done: 0`. The retry loop masked this as
`error: 0` — only visible via `docker compose logs worker`.

**What happened**
`init-db.sql` is the PostgreSQL init script executed on first container boot.
It was written at project start and never updated as observability columns were
added in later PRs (#10+). Missing columns: `embedding_started_at`, `worker_id`,
`frame_cache_hit`, `embedding_ms`, `model_version`. On the dev machine these
existed because they were added via migrations and manual `ALTER TABLE` commands.
The cloud server had a fresh Postgres container that only ran `init-db.sql`.

**Root cause**
Migration scripts and `init-db.sql` diverged. There is one migration script
(`migrate_add_model_version.sql`) for `model_version` but nothing for the
other four columns — and `init-db.sql` had none of them. Fresh deploys are
silently broken whenever the schema is extended without keeping `init-db.sql`
in sync.

**The fix**
1. Manual `ALTER TABLE … ADD COLUMN IF NOT EXISTS` on the running container.
2. Added all 5 columns to `init-db.sql` so future fresh deploys need no manual step.
3. Created `scripts/migrate_add_observability_columns.sql` for upgrading
   existing DBs that predate these columns.

**Interview talking point**
> "Migration scripts and the init script are two separate code paths that must
> stay in sync. The discipline: every `ALTER TABLE` that adds a column also gets
> a matching change to `init-db.sql`. The smell-check before merging any schema
> PR: 'if someone clones this repo today and runs docker compose up for the first
> time, will init-db.sql produce the same schema as a fully-migrated dev DB?'"

---

## 18. API Media Endpoints Were Filesystem-Only — S3 Path Produced Gray Placeholders (2 commits)

**Branch:** `feat/cloud-deploy`

**Symptom**
Search results showed gray placeholder images. Clicking a result showed a black
placeholder video. API returned HTTP 200 `image/jpeg` / `video/mp4` — no 4xx,
no server errors. Completely silent failure.

**What happened — two overlapping causes**

*Cause 1 — Wrong base URL baked into the frontend bundle:*
`NEXT_PUBLIC_STREAM_URL` was absent from the server `.env`. The frontend fell
back to `http://localhost:8000`. The site runs on `https://lumen.example.com`;
browsers block HTTP subresource requests from HTTPS pages (mixed content). Both
`<img>` and `<video>` tags silently received placeholder bytes.

*Cause 2 — Both endpoints called `os.path.isfile()` on S3 keys:*
Even after fixing the URL, `/api/thumbnail` and `/api/stream` called
`os.path.isfile(path)` before doing anything. S3 keys are not filesystem paths
— the check always returned `False` and the endpoint returned the placeholder
immediately. The `STORAGE_BACKEND=s3` check existed in the worker but was never
wired into the API media-serving layer. The API had no boto3 dependency.

**Root cause**
The media-serving endpoints were written when the project only supported local
volume storage. Adding S3 support to the worker didn't automatically apply to
the API — it was a separate code path that nobody audited.

**The fix**
- `/api/stream` (S3): 302 redirect to a presigned R2 URL. Browser downloads
  direct from R2 with full HTTP Range support — zero API proxying overhead.
- `/api/thumbnail` (S3): presigned URL passed as ffmpeg `-i`. ffmpeg issues its
  own HTTP range request to fetch only the bytes it needs for one frame.
- Added `boto3` to `api/requirements.txt` (was only in `worker/requirements.txt`).

**Second commit required — `UnboundLocalError` in shared `except` block:**
After adding the S3 branch, the `except` blocks in the thumbnail endpoint still
logged `resolved` — a variable only assigned in the local-filesystem branch. In
the S3 path `resolved` is never assigned → `UnboundLocalError` → HTTP 500.
Fixed by logging `path` (always present) instead of `resolved`.

**Interview talking point**
> "When you add a second code path alongside an existing one, audit every
> variable referenced in shared code below the branch point — especially
> exception handlers. They're supposed to be safe fallbacks but they
> silently inherit assumptions from the original path. The symptom (500 on
> every request) looked completely unrelated to the new S3 branch. I now
> treat exception handlers as a second function body: every variable they
> reference must be defined on all paths leading into them, not just the
> happy path."

---

## 18. FastAPI `List[float]` on a POST Endpoint Is a Body Param, Not a Query Param

**Commits:** `715aaf9` (test fix in feat/pytest-setup)

**Symptom**
14 new tests for `POST /api/search-vector` all returned 422 Unprocessable
Entity. The vector was being sent as repeated query params
(`?vector=0.1&vector=0.2&…`) following the same pattern used for primitives
like `limit` and `threshold`.

**What happened**
```python
# endpoint signature
async def search_by_vector(request: Request, vector: List[float], limit: int = 20):
```
FastAPI's parameter resolution rules for POST endpoints:
- Scalar types (`int`, `str`, `float`) without a `Body()` annotation → **query param**
- Collection types (`List[float]`, `List[str]`) → **body param** (FastAPI
  assumes repeated scalar query params can't represent an arbitrary-length
  list reliably)

The framework silently promoted `vector` to a body parameter. The correct call
is `client.post("/api/search-vector", json=[0.1, 0.2, …])` — a raw JSON array
body, not query params.

**Root cause**
FastAPI has a nuanced rule: the binding location of a parameter depends on its
*type*, not just the presence/absence of `Body()`. Scalar types are query
params by default on POST; collection types become body params. This is
documented but easy to miss when writing tests against existing endpoints.

**The fix**
```python
# Wrong — 422 on every call
client.post("/api/search-vector", params=[("vector", 0.1), ("vector", 0.2)])

# Correct — raw JSON array body
client.post("/api/search-vector", json=[0.1, 0.2, 0.3])

# Scalar query params still work alongside the JSON body
client.post("/api/search-vector", json=[0.1, 0.2], params={"limit": 5})
```

**Interview talking point**
> "FastAPI's implicit parameter binding is powerful but has non-obvious rules
> for collection types. When a test returns 422 and I know the data is
> correct, I reach for the OpenAPI schema first — FastAPI generates it
> automatically and will tell me exactly what it expects where. In this case,
> `/docs` showed `vector` under `requestBody`, not `parameters`, which
> immediately explained the 422. I now treat the auto-generated schema as
> the authoritative contract when writing tests against a FastAPI service."

---

## 19. Rate Limiter Redis Connection Kills All Tests in CI (109 failures)

**Commits:** `b7309ca` (fix in feat/semantic-topic-tags)

**Symptom**
109 out of 135 tests failed in CI with `redis.exceptions.ConnectionError:
Error 111 connecting to localhost:6379. Connection refused`. The same suite
passed locally in ~3 seconds.

**What happened**
`rate_limit.py` initialises a `slowapi.Limiter` at module import time with
`storage_uri = os.getenv("REDIS_URL", "redis://redis:6379")`. The `conftest.py`
overrode this to `redis://localhost:6379` for local dev. On the GitHub Actions
`ubuntu-latest` runner there is no Redis service — every request that hit a
rate-limited endpoint tried to check the counter and immediately raised.

The `rate_limit.py` module docstring even said *"falls back gracefully to
in-memory if Redis is unreachable"* — this was **incorrect**. `slowapi` does
not silently fall back; it raises on every request.

**Root cause**
Two compounding mistakes:
1. `conftest.py` defaulted `REDIS_URL` to a real Redis address, importing a
   live-service dependency into a supposedly self-contained test suite.
2. A misleading code comment implied automatic fallback that doesn't exist.

**The fix**
```python
# conftest.py — use in-memory backend, zero external dependencies
os.environ.setdefault("REDIS_URL", "memory://")

# ci.yml — belt-and-suspenders in case env is already set
- name: Run pytest
  env:
    REDIS_URL: memory://
  run: pytest ...
```
`limits` (the backend library used by `slowapi`) supports `memory://` as a
fully functional in-process counter store. Tests are isolated per process and
don't need Redis.

**Interview talking point**
> "This is a classic test environment parity trap. 'Passes locally' is not
> evidence that a test is self-contained — it may just mean the developer
> machine happens to have a Redis process running. The rule I apply now: every
> external service a test touches either needs to be in a `docker-compose` for
> the test runner, or needs to be mocked. A rate limiter providing no business
> logic is a perfect mock candidate. I also learned to audit every
> `os.getenv()` default in `conftest.py` and ask: 'does this URL actually
> exist on a clean CI runner, or am I importing a hidden dependency?'"

---

## 20. One-Character YAML Indentation Error Silently Disabled All CI Jobs

**Commits:** `6095da9` (fix in feat/semantic-topic-tags)

**Symptom**
After merging the pytest branch, no CI jobs appeared to run — not lint, not
typecheck, not docker-compose validation, not the new test job. The PR showed
no checks at all.

**What happened**
The `run:` key of the pytest step was written at column 0 (root level of the
file) instead of 8 spaces (indented under `- name: Run pytest`):

```yaml
# Broken — run: at root level
      - name: Run pytest
run: pytest --cov=api ...

# Correct
      - name: Run pytest
        run: pytest --cov=api ...
```

A YAML file with a root-level `run:` key alongside top-level `jobs:` and
`on:` keys is syntactically valid YAML but semantically invalid as a GitHub
Actions workflow. GitHub's workflow parser rejected the file silently — it
didn't write an error to the UI, it simply didn't schedule any of the jobs.
The result was indistinguishable from "CI hasn't run yet."

**Root cause**
The error was introduced when the `run:` line was written directly (not through
an editor with YAML indentation support), and the symptoms made it look like a
GitHub delay rather than a parse failure. There was no red ✗ on the PR — just
absence of checks, which is easy to misread as "pending."

**The fix**
Corrected indentation to 8 spaces. All 5 jobs (typecheck, lint, validate-compose,
test-api, check-requirements) immediately appeared and ran on the next push.

**Interview talking point**
> "GitHub Actions silently drops an entire workflow if the YAML is
> structurally invalid — there's no parsing error surfaced in the UI, just
> no jobs. I now validate workflow files with `actionlint` before pushing,
> or at minimum run `python -c 'import yaml; yaml.safe_load(open(\".github/workflows/ci.yml\"))'`
> to catch structural errors locally. The broader lesson: a missing check in
> CI is not the same as a passing check — absence of failure is not evidence
> of success."

---

## 21. Next.js Module-Level `process.env` Reads Are Captured at Build Time

**PR:** #35 (fix/backend-api-key-runtime-read)

**Symptom**
After enabling `API_KEY_REQUIRED=true` and setting `BACKEND_API_KEY` in `.env`,
all API requests from the frontend still returned `401 Unauthorized`. The env
var was confirmed present inside the running container, the source code had the
`X-API-Key` header forwarding, and the image had been rebuilt — yet FastAPI
never received the key.

**What happened**
The `BACKEND_API_KEY` read was placed at module level in all 4 Next.js API
route handlers:

```typescript
// ❌ WRONG — evaluated once at build time
const BACKEND_API_KEY = process.env.BACKEND_API_KEY || ''

export async function POST(request: NextRequest) {
  headers: { ...(BACKEND_API_KEY && { 'X-API-Key': BACKEND_API_KEY }) }
}
```

The Docker image was built without `BACKEND_API_KEY` set in the build
environment. Next.js evaluated the module-level expression during the build
and inlined `''`. Every container started from that image sent an empty string
regardless of what `.env` contained at runtime.

**Root cause**
Next.js API route modules are compiled — module-scope expressions that can be
statically resolved (including `process.env` reads without a `NEXT_PUBLIC_`
prefix) may be captured at build time depending on how the bundler tree-shakes
the output. Variables needed at runtime must be read inside the handler
function to guarantee a fresh `process.env` lookup per request.

**The fix**
```typescript
// ✅ CORRECT — evaluated on every request
export async function POST(request: NextRequest) {
  const BACKEND_API_KEY = process.env.BACKEND_API_KEY || ''
  headers: { ...(BACKEND_API_KEY && { 'X-API-Key': BACKEND_API_KEY }) }
}
```

**Interview talking point**
> "This was a build-time vs. runtime capture trap. The container had the
> correct env var, the code looked right, and the image was freshly built —
> three things that should mean it works. The clue was that `docker compose
> exec frontend node -e 'console.log(process.env.BACKEND_API_KEY)'` printed
> the key correctly, but requests still 401'd. That ruled out the container
> env and pointed to the compiled output. Module-level `process.env` reads
> in Next.js API routes are a footgun: move secrets inside the handler where
> they're evaluated at request time, not build time."

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
| **Docker bind-mount falls back to read-only parent when target dir is missing** | #13 (SMB frame cache) |
| **FastAPI collection-type params on POST are body params, not query params** | #18 (search-vector tests) |
| **Test env that "passes locally" may have hidden live-service dependencies** | #19 (Redis in CI) |
| **YAML parse failure in CI silently drops all jobs with no error shown** | #20 (workflow indentation) |
| **Next.js API route module-level `process.env` reads are captured at build time — read secrets inside the handler function** | #21 (BACKEND_API_KEY build-time capture) |
| **Per-worker local caches belong in Docker named volumes, not shared network mounts** | #13 (SMB frame cache) |
| **SMB stale mount produces two distinct exceptions: `OSError(EIO)` and `FFmpegError(ffprobe failed:)` — both need fast-fail** | #13 (stale mount signatures) |
| **Changing a vector store's dimension requires updating all producers atomically — add a startup dimension assertion** | #14 (CLIP/Qdrant dim mismatch) |
| **Third-party SDK attributes renamed across minor versions; use `getattr` fallbacks at call sites** | #14, #1 (Qdrant `vectors_count` → `points_count`) |
| **Container writable layer bloat is invisible — `/tmp` and model caches must be named volumes** | #15 (HF model cache, orphaned tmp mp4) |
| **Multi-stack compose files must hardcode stack-local service hostnames — missing env vars silently cross-connect stacks** | #15 (lumen2 REDIS_URL → lumen1) |
| **Qdrant collection must exist before any task runs — create in worker startup hook, not inside the task** | #16 (VectorParams(size=None) retry loop) |
| **`init-db.sql` and migration scripts must stay in sync — fresh deploys break silently if init script predates schema changes** | #17 (observability columns missing on cloud) |
| **`STORAGE_BACKEND=s3` must be checked in every layer that serves files — API endpoints are not exempt** | #18 (stream/thumbnail filesystem-only) |
| **Variables only assigned in one branch must not be referenced in shared `except` blocks** | #18 (UnboundLocalError in S3 error handler) |