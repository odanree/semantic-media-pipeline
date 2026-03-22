# Project Learnings: Root-Cause Analysis & Lessons Learned

A living record of every significant bug, outage, and architectural misstep encountered while building the Lumen Semantic Media Pipeline. Each entry documents what broke, why it broke, how it was diagnosed, and what was fixed — in the style of a post-mortem or engineering retrospective.

---

## Table of Contents

1. [EXIF Bytes Not JSON-Serializable](#1-exif-bytes-not-json-serializable)
2. [asyncpg Callback Using Non-Existent Method](#2-asyncpg-callback-using-non-existent-method)
3. [Search Router Never Registered — All Search Endpoints 404](#3-search-router-never-registered--all-search-endpoints-404)
4. [API Container Importing Worker ML Dependencies](#4-api-container-importing-worker-ml-dependencies)
5. [WebSocket URL Wrong Protocol (http vs ws)](#5-websocket-url-wrong-protocol-http-vs-ws)
6. [Infinite WebSocket Reconnect With No Backoff](#6-infinite-websocket-reconnect-with-no-backoff)
7. [JSX Syntax in a .ts File](#7-jsx-syntax-in-a-ts-file)
8. [Docker-Internal Hostname Not Resolvable in Browser](#8-docker-internal-hostname-not-resolvable-in-browser)
9. [CORS Invalid Combination Silently Killed All WebSocket Connections](#9-cors-invalid-combination-silently-killed-all-websocket-connections)
10. [ASGI Double-Close RuntimeError](#10-asgi-double-close-runtimeerror)
11. [React useEffect Infinite Loop From Unstable Callback Dependencies](#11-react-useeffect-infinite-loop-from-unstable-callback-dependencies)
23. [Playlist 400 — S3 Object Keys Not in ALLOWED_ROOTS](#23-playlist-400--s3-object-keys-not-in-allowed_roots)
24. [Celery Prefork + CUDA → "Cannot Re-initialize CUDA in Forked Subprocess"](#24-celery-prefork--cuda--cannot-re-initialize-cuda-in-forked-subprocess)
25. [Qdrant Healthcheck Always Fails — No `curl` or `wget` in Image](#25-qdrant-healthcheck-always-fails--no-curl-or-wget-in-image)
26. [task_acks_late=True Causes Duplicate Task Processing on Worker Restart](#26-task_acks_latetrue-causes-duplicate-task-processing-on-worker-restart)
27. [Stale DB Records From File Renames Leave Tasks Stuck as `pending` Forever](#27-stale-db-records-from-file-renames-leave-tasks-stuck-as-pending-forever)
28. [Windows ffprobe UnicodeDecodeError on Non-Latin File Metadata](#28-windows-ffprobe-unicodedecodeerror-on-non-latin-file-metadata)
29. [Qdrant Client API Mismatch (3 commits to fix)](#29-qdrant-client-api-mismatch-3-commits-to-fix)
30. [Video Streaming 404 — Silent Routing Conflict (4 commits to fix)](#30-video-streaming-404-silent-routing-conflict-4-commits-to-fix)
31. [apply_faststart() Silently No-Op on Every File (2 commits + discovery script)](#31-apply_faststart-silently-no-op-on-every-file-2-commits-discovery-script)
32. [Worker RAM Thrash — Load Average 23.75 (2 commits to stabilize)](#32-worker-ram-thrash-load-average-2375-2-commits-to-stabilize)
33. [Streaming Throughput — 64 KB Chunks vs. 9P Latency (2 commits)](#33-streaming-throughput-64-kb-chunks-vs-9p-latency-2-commits)
34. [FFmpeg Timeout — Fixed Ceiling vs. Variable Content (PR #5)](#34-ffmpeg-timeout-fixed-ceiling-vs-variable-content-pr-5)
35. [Chrome ORB Blocks `<img>` Responses with Non-Image MIME Type](#35-chrome-orb-blocks-img-responses-with-non-image-mime-type)
36. [BuildKit Apt Cache Poisoning — Package in Dockerfile, Not in Container](#36-buildkit-apt-cache-poisoning-package-in-dockerfile-not-in-container)
37. [Blocking Proxy Encode in the Critical Pipeline Path (2 commits)](#37-blocking-proxy-encode-in-the-critical-pipeline-path-2-commits)
38. [`os.replace()` Fails Across Docker Volume Mount Points](#38-osreplace-fails-across-docker-volume-mount-points)
39. [`os.getenv('VAR', default)` Does Not Guard Against Empty String (1 commit)](#39-osgetenvvar-default-does-not-guard-against-empty-string-1-commit)
40. [Video Player "Unknown error" — Four Overlapping Layers (4 fixes to resolve)](#40-video-player-unknown-error-four-overlapping-layers-4-fixes-to-resolve)
41. [SMB Mount Is Read-Only on Remote Host — Two Failure Modes](#41-smb-mount-is-read-only-on-remote-host-two-failure-modes)
42. [CLIP Model / Qdrant Collection Dimension Mismatch — Silent Backlog (multi-session)](#42-clip-model-qdrant-collection-dimension-mismatch-silent-backlog-multi-session)
43. [Docker Disk Bloat — Three Independent Root Causes](#43-docker-disk-bloat-three-independent-root-causes)
44. [Qdrant Collection Not Pre-Created — `VectorParams(size=None)` Silent Retry Loop (2 commits)](#44-qdrant-collection-not-pre-created-vectorparamssizenone-silent-retry-loop-2-commits)
45. [DB Schema Drift Between `init-db.sql` and Migration Scripts (3 commits)](#45-db-schema-drift-between-init-dbsql-and-migration-scripts-3-commits)
46. [API Media Endpoints Were Filesystem-Only — S3 Path Produced Gray Placeholders (2 commits)](#46-api-media-endpoints-were-filesystem-only-s3-path-produced-gray-placeholders-2-commits)
47. [FastAPI `List[float]` on a POST Endpoint Is a Body Param, Not a Query Param](#47-fastapi-listfloat-on-a-post-endpoint-is-a-body-param-not-a-query-param)
48. [Rate Limiter Redis Connection Kills All Tests in CI (109 failures)](#48-rate-limiter-redis-connection-kills-all-tests-in-ci-109-failures)
49. [One-Character YAML Indentation Error Silently Disabled All CI Jobs](#49-one-character-yaml-indentation-error-silently-disabled-all-ci-jobs)
50. [Next.js Module-Level `process.env` Reads Are Captured at Build Time](#50-nextjs-module-level-processenv-reads-are-captured-at-build-time)
51. [`qdrant-client` Minor Version Removed `.search()` — Mocked Tests Passed, Prod Returned 0 Results (2 hotfixes)](#51-qdrant-client-minor-version-removed-search-mocked-tests-passed-prod-returned-0-results-2-hotfixes)
52. [Unclosed Mermaid Code Fence Broke Entire README Render on GitHub](#52-unclosed-mermaid-code-fence-broke-entire-readme-render-on-github)

---

## 1. EXIF Bytes Not JSON-Serializable

**Commit(s):** `ae666df` → `08b128f`  
**Component:** `worker/tasks.py`  
**Severity:** Medium — caused worker task crashes for images with EXIF data

### What Broke

When processing images through Pillow, EXIF data is returned as a dictionary containing raw `bytes` values (e.g., maker notes, GPS binary data). Attempting to store this in the database as JSON or pass it through Celery's result backend caused a silent serialization crash:

```python
exif_data = img._getexif()        # Returns {tag_id: value} — some values are bytes
media_record.exif_data = exif_data  # Fails: bytes is not JSON serializable
```

The initial workaround was to skip EXIF entirely. A later attempt to re-add it in PR#1 reproduced the same crash because it passed the raw dict without filtering the non-serializable types.

### Root Cause

`json.dumps()` (and anything that internally serializes to JSON — SQLAlchemy JSON columns, Celery serializers) has no default handler for `bytes`. Python EXIF data contains a mix of integers, strings, tuples, and raw bytes, so a naive dict pass-through always fails.

### Fix

Either convert `bytes` values to hex strings during extraction, or skip EXIF storage entirely until a proper EXIF parsing library (e.g., `exifread`) is integrated:

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

## 2. asyncpg Callback Using Non-Existent Method

**Commit(s):** `ae666df` → `656b40b`  
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

The `AttributeError` was swallowed silently by asyncpg's callback dispatcher. The system appeared to connect and listen, but no notifications were ever delivered to WebSocket clients. The feature was completely non-functional after shipping.

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

## 3. Search Router Never Registered — All Search Endpoints 404

**Commit(s):** `656b40b` → `d0cd069`  
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

## 4. API Container Importing Worker ML Dependencies

**Commit(s):** `656b40b` → `d0cd069`  
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

The API and worker share some code structure visually, but they are separate containers with separate dependency trees. The worker contains multi-gigabyte ML models and GPU libraries. The API is intentionally a thin, fast HTTP layer. Importing across these boundaries violates the service separation and makes the API container enormous and fragile.

### Fix

Remove all ML imports from the API. The search endpoint calls Qdrant directly with a pre-computed vector, delegating embedding to the client or a separate embedding endpoint:

```python
# AFTER — API only talks to Qdrant, no ML dependencies
from qdrant_client import QdrantClient
client = QdrantClient(host=QDRANT_HOST, port=QDRANT_PORT)
results = client.search(collection_name="media", query_vector=vector, limit=limit)
```

### Lesson

**Each container's `requirements.txt` is a hard boundary.** In a microservices architecture, import boundaries must mirror deployment boundaries. If service A and service B share source code in a monorepo, be explicit about which modules are owned by which service. A simple CI check — start the API container in isolation and hit `/docs` — would catch this before it ever ships.

---

## 5. WebSocket URL Wrong Protocol (http vs ws)

**Commit(s):** `656b40b`, `08b128f`, `2406dd0`  
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

Convert the protocol before constructing the URL:

```typescript
const apiUrl = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'
const wsProtocol = apiUrl.startsWith('https') ? 'wss' : 'ws'
const apiHost = apiUrl.replace(/^https?:\/\//, '').replace(/\/$/, '')
const wsUrl = `${wsProtocol}://${apiHost}/api/ws/processing-status`
```

### Lesson

**WebSocket URLs are not HTTP URLs.** They share the same TCP/TLS transport but use `ws://` and `wss://` schemes. Environment variables that store API base URLs are almost always `http://` — never pass them raw to `new WebSocket()`. Create a single utility function that performs the scheme conversion, and use it everywhere.

---

## 6. Infinite WebSocket Reconnect With No Backoff

**Commit(s):** `656b40b` → `d0cd069`, then `53501da`  
**Component:** `frontend/hooks/useMediaUpdates.ts`, `useStatusUpdates.ts`  
**Severity:** High — hammered the API with connection storms during outages

### What Broke

The initial `ws.onclose` handler unconditionally scheduled a reconnect after a fixed 3-second delay with no counter and no stopping condition:

```typescript
ws.onclose = () => {
    reconnectTimer = setTimeout(connect, 3000); // Always, forever
};
```

If the API was unreachable or took more than a few seconds to start, the client would hammer it with a new connection attempt every 3 seconds indefinitely. With multiple browser tabs open, this multiplied. The retry loop never reset its counter on successful reconnection, so transient outages eventually consumed all retries permanently.

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

**Every network reconnect loop must have a maximum retry count and exponential backoff.** Without these, a single API restart causes a thundering herd of connection attempts from all connected clients simultaneously. The exponential backoff spreads out reconnections and gives the server time to recover. Always reset the retry counter on successful connection to handle transient outages gracefully.

---

## 7. JSX Syntax in a .ts File

**Commit(s):** `656b40b` → `d0cd069`  
**Component:** `frontend/hooks/useMediaUpdates.ts`  
**Severity:** Medium — build-time compile error

### What Broke

React components that return JSX (`<div>`, `<span>`, etc.) were placed inside a `.ts` file. TypeScript and Next.js's webpack pipeline do not parse JSX in files with a `.ts` extension — only `.tsx`. The build failed at compile time:

```
Module parse failed: Unexpected token '<'
```

### Root Cause

The hook file was started as a pure logic file (`hooks/useMediaUpdates.ts`), then UI components were added to it later without changing the extension to `.tsx`.

### Fix

Move JSX components to a new `.tsx` file (`useMediaUpdates.tsx`) and keep the hook logic in the `.ts` file. Alternatively, rename the original file to `.tsx`.

### Lesson

**TypeScript file extension is part of the contract: `.ts` for pure logic, `.tsx` for anything that renders JSX.** If a file grows to include UI components, rename it immediately. The distinction is especially easy to miss in a `hooks/` directory where most files are `.ts`.

---

## 8. Docker-Internal Hostname Not Resolvable in Browser

**Commit(s):** `2406dd0` → `76743e3`  
**Component:** `frontend/hooks/useStatusUpdates.ts`  
**Severity:** High — WebSocket connections failed in browser even after protocol fix

### What Broke

`NEXT_PUBLIC_API_URL` was set to `http://api:8000` in the Docker Compose environment. Inside the Docker network, `api` resolves correctly via Docker's internal DNS. However, Next.js embeds `NEXT_PUBLIC_*` variables into the client-side JavaScript bundle at **build time**. The browser — running on the user's host machine — received `ws://api:8000/...` as the WebSocket URL, and `api` is not a hostname the browser can resolve. The connection failed with an immediate DNS error.

### Root Cause

`NEXT_PUBLIC_*` variables are not server-side secrets — they are inlined into the JavaScript sent to the browser. A Docker-internal service hostname is meaningless outside the container network. There's a fundamental mismatch between the server-side network and the client-side network.

### Fix

Detect the Docker hostname at runtime and substitute `localhost`:

```typescript
let apiUrl = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'
if (typeof window !== 'undefined' && apiUrl.includes('api:8000')) {
    apiUrl = 'http://localhost:8000' // Browser cannot resolve Docker DNS
}
```

The longer-term fix is to expose an environment variable specifically for the browser URL (e.g., `NEXT_PUBLIC_API_BROWSER_URL=http://localhost:8000`) separate from the server-side `NEXT_PUBLIC_API_URL`.

### Lesson

**In containerized Next.js apps, there are two distinct networks: the Docker internal network (server-to-server) and the host/browser network (client-to-server).** `NEXT_PUBLIC_*` variables end up in the browser. Never put Docker-internal hostnames in `NEXT_PUBLIC_*` variables. Use a separate environment variable for the browser-visible URL, or use Next.js API route proxying so the browser never talks to the backend directly.

---

## 9. CORS Invalid Combination Silently Killed All WebSocket Connections

**Commit(s):** `08b128f` → `387b50b`  
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

Per the specification, a server **cannot** respond with `Access-Control-Allow-Origin: *` while also setting `Access-Control-Allow-Credentials: true`. Starlette's implementation enforces this by returning `HTTP 400 Bad Request` for any request that carries an `Origin` header when both are set.

The critical detail: browsers **always** send an `Origin` header on WebSocket upgrade requests (and on cross-origin fetch requests). This meant every single WebSocket connection attempt from the browser was rejected at the middleware layer before the handler was ever invoked. The server-side handler never logged anything because it was never reached.

### Diagnosis

The only evidence in the logs was:
```
INFO: connection rejected (400 Bad Request)
```

This appeared mixed among normal `connection closed` entries and was easy to overlook. It was confirmed by testing the WebSocket endpoint with a raw socket using an explicit `Origin: http://localhost:3000` header and observing the 400 response.

### Fix

```python
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,  # Cannot use True with wildcard origins
)
```

### Lesson

**`allow_credentials=True` with `allow_origins=["*"]` is not a runtime error — it silently breaks a specific class of requests.** It passes server startup, it passes HTTP endpoint tests (because most HTTP test tools don't send `Origin`), and it only manifests in browser WebSocket and cross-origin fetch requests. Always test WebSocket connectivity specifically from a browser, not just from `curl` or Python scripts. Also: a `400 Bad Request` on a WebSocket endpoint is almost always a middleware/CORS issue, not a handler bug.

---

## 10. ASGI Double-Close RuntimeError

**Commit(s):** `08b128f` → `37ef849`  
**Component:** `api/routers/updates.py`  
**Severity:** High — WebSocket connections closed immediately after accepting, with cryptic error

### What Broke

The WebSocket handlers structured control flow such that an exception during message handling would propagate to the `except` block, then to the `finally` block. The `finally` block called `websocket.close()`. However, in some cases — particularly when the exception was triggered by the client disconnecting — the connection was already in a closed state. Calling `close()` on an already-closed WebSocket causes Uvicorn's ASGI implementation to raise:

```
RuntimeError: Unexpected ASGI message 'websocket.close',
after sending 'websocket.close' or response already completed
```

Uvicorn then reset the TCP connection, causing the browser to see an `Insufficient resources` error (the browser's error message when a WebSocket connection is reset by the server during or immediately after the handshake).

### Root Cause

FastAPI/Starlette WebSocket connections have state. Once `close()` has been called — either explicitly or implicitly by an exception that tears down the ASGI scope — calling it again is a protocol violation. The `finally` block ran unconditionally without checking connection state.

### Fix

Wrap the `close()` call in a `try/except RuntimeError`:

```python
finally:
    if websocket in active_connections["processing_status"]:
        active_connections["processing_status"].remove(websocket)
    try:
        await websocket.close()
    except RuntimeError:
        pass  # Already closed — expected during disconnect scenarios
```

### Lesson

**In ASGI WebSocket handlers, the connection is a state machine.** Calling lifecycle methods (`accept`, `close`, `send`) out of sequence raises `RuntimeError`. The `finally` block of an async WebSocket handler is a minefield — always guard `close()` calls, because the exception that triggered the `finally` may have already closed the connection. A pattern used in production systems is to check `websocket.client_state` before calling close.

---

## 11. React useEffect Infinite Loop From Unstable Callback Dependencies

**Commit(s):** `08b128f` → `53501da` and `be03473`  
**Component:** `frontend/hooks/useStatusUpdates.ts`, `frontend/hooks/useMediaUpdates.ts`  
**Severity:** Critical — generated 100,000+ WebSocket errors; effectively a client-side DoS

### What Broke

Both hooks accepted `onUpdate` and `onError` callback props and listed them as `useEffect` dependencies:

```typescript
// In useStatusUpdates.ts and useMediaUpdates.ts
export function useStatusUpdates({ onUpdate, onError }) {
    useEffect(() => {
        const ws = new WebSocket(url)
        ws.onmessage = (e) => onUpdate(data)
        // ...
        return () => ws.close()
    }, [onUpdate, onError])  // ← the bug
}
```

The caller (`StatusPanel.tsx`) passed inline arrow functions:

```typescript
const { status } = useStatusUpdates({
    onUpdate: (newStatus) => { setStatus(newStatus) }, // New function reference every render
    onError:  (err)       => { setError(err.message) }, // New function reference every render
})
```

In JavaScript, `() => {}` creates a new function object on every execution. On every render, `onUpdate` and `onError` have a different reference. React's `useEffect` sees the dependency changed and runs the cleanup (closing the WebSocket) then re-runs the effect (opening a new WebSocket). Opening a WebSocket triggers an async operation that updates state (`setIsConnected(true)`), which causes a re-render, which creates new callback references, which triggers `useEffect` cleanup again — an infinite loop.

The result was over 100,000 WebSocket connection attempts in a single browser session, each one immediately destroyed, each generating an entry in the browser console.

### Root Cause

Two interacting React patterns:
1. **Inline functions are not referentially stable** — each render produces a new function object even if the logic is identical.
2. **`useEffect` uses `Object.is()` for dependency comparison** — two functions that do the same thing are not `===` equal if they are different object instances.

The hooks were designed correctly for pure data dependencies (`wsUrl`, `maxHistorySize`), but callbacks are not data — they are behavior. The fix for this is a well-known React pattern.

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

**Never put callbacks/functions in `useEffect` dependency arrays unless they are guaranteed to be referentially stable.** Functions from props are almost never stable — the parent component creates a new one every render unless it wraps them in `useCallback`. The safe patterns are:
- Use `useRef` to hold callbacks that the effect uses (the pattern above)
- Require callers to memoize with `useCallback` (fragile — callers will forget)
- Restructure to not need callbacks in the effect at all

The `useRef` pattern is preferred because it puts the stability guarantee inside the hook, where it belongs, rather than imposing a burden on every caller. This bug was introduced in two places simultaneously because one hook was written by copying the other — always audit all copies of a pattern when fixing one instance.

---

---

## 12. Celery Proxy Task Blocking the Indexing Queue

**Commit(s):** `feat/windows-native-worker`
**Component:** `scripts/start-windows-worker-*.ps1`
**Severity:** High — indexing ground to a halt behind proxy encoding jobs

### What Broke

The Windows Celery worker was started with `--queues=celery,proxies`. The `generate_proxy` task encodes 720p H.265 video to H.264 and took ~13 minutes per file. With a backlog of thousands of videos, proxy jobs monopolised every worker slot. CLIP embedding — the actual indexing work — was queued behind hundreds of encoding jobs and effectively stalled.

### Root Cause

The `celery` and `proxies` queues shared the same worker pool. A long-running CPU-bound task (video encoding) starved the short-running GPU-bound tasks (CLIP embedding). There was no queue priority or dedicated worker separation.

### Fix

Remove `proxies` from the queues argument on all indexing workers:

```powershell
# Before
--queues=celery,proxies
# After
--queues=celery
```

A dedicated encoding worker can be spun up separately when proxy generation is explicitly needed.

### Lesson

**Long-running CPU tasks and short-running GPU tasks must never share the same Celery worker pool.** Separate queues are not enough — the workers consuming those queues must also be separate. Treat proxy generation as a background maintenance job, not part of the critical indexing path.

---

## 13. Windows Paths Written Into Qdrant Payload

**Commit(s):** `feat/windows-native-worker`
**Component:** `worker/tasks.py`
**Severity:** High — all Windows-indexed media unreachable from Linux API

### What Broke

A `_translate_path()` function was added to map Linux mount paths (`/mnt/source/...`) to Windows drive paths (`J:/lumen-media/...`) so the Windows worker could access files on disk. However, the translation was applied at the top of the task function, mutating `file_path` before the Qdrant upsert:

```python
file_path = _translate_path(file_path)  # Now a Windows path
...
qdrant_client.upsert(payload={"file_path": file_path})  # Stores Windows path
```

The Linux API later queried Qdrant and got back `J:/lumen-media/...` paths that it could not serve or resolve.

### Root Cause

`file_path` served two roles: the logical identifier stored in the database/Qdrant, and the physical path used for filesystem access. These must be kept separate across platforms.

### Fix

Introduce a `native_path` variable for filesystem access only; `file_path` remains the Linux path throughout:

```python
native_path = _translate_path(file_path)   # Used for disk I/O only
# All qdrant upserts and DB writes use file_path (Linux path)
```

### Lesson

**Never mutate the canonical record identifier to make local filesystem access work.** The stored path is the source of truth for the entire system. Filesystem access is a local concern — introduce a separate variable for it and treat the original `file_path` as immutable within the task.

---

## 14. `docker restart` Does Not Re-Read `env_file`

**Commit(s):** `feat/windows-native-worker`
**Component:** `docker-compose.yml`, `docker-compose.second.yml`
**Severity:** Medium — env changes silently ignored after restart

### What Broke

After editing `.env` to change `LLM_PROVIDER` from `openai` to `local`, running `docker restart lumen-api` kept serving the old configuration. The container continued to call OpenAI and throw `OPENAI_API_KEY must be set` errors.

### Root Cause

`docker restart` stops and restarts the existing container with the **same configuration it was created with**. The `env_file` is read at container creation time (`docker compose up`), not at restart time. Restarting reuses the frozen environment.

### Fix

Use `docker compose up -d --no-deps <service>` (with `--no-build` if image rebuild is not needed) to recreate the container with the current env:

```bash
docker compose up -d --no-build --no-deps api
```

### Lesson

**`docker restart` ≠ `docker compose up`.** For any configuration change — env vars, volume mounts, port bindings — the container must be recreated, not just restarted. Use `up -d` as the default for applying config changes.

---

## 15. `--no-deps` Puts Container on an Isolated New Network

**Commit(s):** `feat/windows-native-worker`
**Component:** `docker-compose.second.yml`
**Severity:** High — service started with `--no-deps` couldn't reach redis/postgres

### What Broke

Running `docker compose up -d --no-deps api2` to start only the API container created a **new** Docker network (`semantic-media-pipeline_lumen2-net`) rather than joining the existing network where `lumen2-redis` and `lumen2-postgres` were running. The API started successfully but every request failed with:

```
redis.exceptions.ConnectionError: Name or service not known
```

### Root Cause

`--no-deps` skips dependency container startup but also skips the network join logic. The API container was placed on its own isolated network instead of the shared project network.

### Fix

After starting with `--no-deps`, manually connect to the existing network:

```bash
docker network connect lumen2_lumen2-net lumen2-api
```

Or avoid `--no-deps` entirely and use the full `up -d` with all dependencies already running.

### Lesson

**`--no-deps` is a footgun for multi-service stacks.** It is useful for rebuilding a single image without touching others, but it silently skips network joining. The safer alternative is to bring the full stack down and up, or to manually connect the container to the correct network immediately after starting it.

---

## 16. Compose `env_file` Values Not Injected Without `environment:` Section

**Commit(s):** `feat/windows-native-worker`
**Component:** `docker-compose.second.yml`
**Severity:** Medium — LLM config from `.env` never reached the API container

### What Broke

`LLM_PROVIDER=local` was set in `.env` and `docker-compose.second.yml` had `env_file: - .env` on the worker service — but the `api2` service used an explicit `environment:` block without any LLM keys. Docker Compose only injects `env_file` values for services that declare `env_file`. The API defaulted to `LLM_PROVIDER=openai` (the code's default) and failed.

### Root Cause

`env_file` and `environment:` are per-service declarations. A value in `.env` is available for variable interpolation in the compose file (`${LLM_PROVIDER}`) but is not automatically injected into container environments — only `env_file:` or explicit `environment:` entries achieve that.

### Fix

Add the variables explicitly to the service's `environment:` block:

```yaml
environment:
  - LLM_PROVIDER=${LLM_PROVIDER:-local}
  - LLM_MODEL=${LLM_MODEL:-qwen3:14b}
  - LLM_BASE_URL=${LLM_BASE_URL:-http://host.docker.internal:11434/v1}
```

### Lesson

**`.env` is not a global environment injection mechanism.** It provides defaults for compose variable interpolation (`${VAR}`) and is injected into containers only when the service declares `env_file: - .env`. Always verify with `docker exec <container> env | grep <VAR>` after changing environment config.

---

## 17. Docker Project Name Fragmentation Across Restart Cycles

**Commit(s):** `feat/windows-native-worker`
**Component:** `docker-compose.yml`, `docker-compose.second.yml`
**Severity:** High — containers from the same stack spread across 3 different Docker projects

### What Broke

Neither compose file had a `name:` field. Docker Compose derives the project name from the directory name (`semantic-media-pipeline`) by default, but individual container restarts during debugging used different flags (`-p lumen2`) or no project flag at all. The result was containers registered under three different projects simultaneously, causing orphan warnings, network isolation between containers in the same stack, and volume conflicts.

### Root Cause

Without a `name:` field, the Docker project name is directory-derived and can silently change depending on how `docker compose` is invoked. Partial stack restarts (stopping/removing individual containers then recreating them) register new containers under whatever project name was active at that moment.

### Fix

Add `name:` to every compose file:

```yaml
# docker-compose.yml
name: lumen1

# docker-compose.second.yml
name: lumen2
```

### Lesson

**Every `docker-compose.yml` file should have an explicit `name:` field.** This makes the project name immutable regardless of the working directory, the `-p` flag, or who runs the command. It eliminates orphan warnings, ensures consistent network and volume naming, and makes Docker Desktop's project view clean and readable.

---

## 18. Volume Ownership Conflict When Renaming Docker Project

**Commit(s):** `feat/windows-native-worker`
**Component:** `docker-compose.yml`, `docker-compose.second.yml`
**Severity:** High — data loss risk; all lumen2 data became unreachable

### What Broke

Adding `name: lumen2` to `docker-compose.second.yml` changed the Docker project name from `semantic-media-pipeline` to `lumen2`. On the next `up -d`, Docker looked for volumes named `lumen2_qdrant2_data`, `lumen2_postgres2_data`, etc. — not the existing `semantic-media-pipeline_qdrant2_data` volumes. New empty volumes were created, and the old volumes with 916MB of indexed Qdrant data were silently orphaned.

### Root Cause

Docker Compose volume names are prefixed with the project name unless overridden with an explicit `name:` in the volume declaration. Changing the project name effectively changes what volumes are mounted, with no warning about data loss.

### Fix

Pin every named volume to its actual physical volume name using `name:` + `external: true`:

```yaml
volumes:
  qdrant2_data:
    name: lumen2_qdrant2_data   # the real volume name
    external: true
  postgres2_data:
    name: lumen2_postgres2_data
    external: true
```

The data was recovered by inspecting which orphaned volume had content (`du -sh /data` via a temporary alpine container) and updating the compose file to reference that volume.

### Lesson

**Before renaming a Docker Compose project, pin all named volumes with `name:` + `external: true`.** Volume names are project-namespaced by default — changing the project name is a silent breaking change for all volumes. When recovering from this situation, use `docker run --rm -v <volume>:/data alpine du -sh /data` to find which volume actually has your data.

---

## 19. JSX Unescaped Entities in Text Content Break Next.js Build

**Commit(s):** `feat/windows-native-worker`
**Component:** `frontend/components/AskPanel.tsx`
**Severity:** Medium — frontend Docker image build failed with exit code 1

### What Broke

Transcript snippets in the AskPanel were wrapped in literal quote characters inside JSX text content:

```tsx
<div>"{src.audio_transcript.slice(0, 80)}"</div>
```

The ESLint rule `react/no-unescaped-entities` treats bare `"` in JSX text as an error (not a warning), causing `npm run build` to exit with code 1. The Docker build failed silently — the error was buried in build output and only visible with `--progress=plain`.

### Fix

Use JavaScript expressions for special characters in JSX text:

```tsx
<div>{'"'}{src.audio_transcript.slice(0, 80)}{'"'}</div>
```

Or use HTML entities: `&quot;`.

### Lesson

**JSX text content is not a string — `"` and `'` must be escaped or expressed as JS.** The build error message (`exit code: 1`) gives no indication of the cause. Always run `docker compose build --progress=plain` to see the full output when a Docker build fails.

---

## 20. Ollama Running on CPU Because GPU Not Initialized at Start Time

**Commit(s):** N/A — operational issue
**Component:** Ollama / WSL2 GPU passthrough
**Severity:** High — 8 tok/s instead of 60+ tok/s

### What Broke

Ollama was started before the NVIDIA drivers were fully initialized in WSL2 (or in a Windows-native vs WSL conflict state). It fell back to CPU inference and ran at ~8 tok/s, consuming 100% CPU. The `ollama run` command gave no warning that it was running on CPU.

### Diagnosis

```bash
nvidia-smi   # Run inside WSL — confirms GPU visibility
ollama run qwen3:14b "test" --verbose  # Shows eval_rate tok/s
```

### Fix

Kill the Ollama process, confirm `nvidia-smi` works in WSL, then restart Ollama. GPU inference resumes at 60+ tok/s.

### Lesson

**Always verify `nvidia-smi` inside WSL before starting any GPU-dependent service.** If Ollama starts before the GPU driver is ready, it silently falls back to CPU. After switching from CPU to GPU, `eval_rate` jumps from ~8 to 60+ tok/s — the difference is immediately obvious in the benchmark output. Add `ollama run <model> "" --verbose` to any startup checklist for GPU-accelerated inference.

---

## 21. Manual Container Removal Breaks Docker DNS for the Entire Stack

**Commit(s):** ops — 2026-03-14
**Component:** `docker-compose.yml`
**Severity:** Critical — every dependent service lost connectivity to Redis; API returned 500 on all requests

### What Broke

`lumen-redis` was manually stopped and removed (`docker stop lumen-redis && docker rm lumen-redis`) to add a host port binding. It was then recreated with `docker compose up -d redis` — but without the `-p lumen1` project flag. Docker Compose derived the project name from the working directory (`semantic-media-pipeline`) and placed the new container on the `semantic-media-pipeline_lumen-net` bridge network. Every other container in the stack — `lumen-api`, `lumen-flower`, `lumen-worker`, etc. — was still on `lumen1_lumen-net`. Docker's internal DNS is network-scoped: `lumen-redis` was no longer resolvable from any of those containers.

Symptoms:
- `lumen-api`: every request returned `500 Internal Server Error` (slowapi rate-limit middleware hitting a `ConnectionError` trying to reach Redis)
- `lumen-flower`: `Error -2 connecting to lumen-redis:6379. Name does not resolve` in a tight retry loop
- `lumen-worker`: identical DNS failure on broker connection

### Root Cause

Docker's embedded DNS resolver is per-network. A container on network A cannot resolve the hostname of a container on network B. When the project name changes, the network name changes, and a recreated container lands on the new network while the rest of the stack remains on the old one. `docker container inspect` confirms the mismatch immediately:

```bash
# Before fix — mismatched networks
docker inspect lumen-redis  --format '{{range $k,$v := .NetworkSettings.Networks}}{{$k}}{{end}}'
# semantic-media-pipeline_lumen-net   ← wrong

docker inspect lumen-api --format '{{range $k,$v := .NetworkSettings.Networks}}{{$k}}{{end}}'
# lumen1_lumen-net   ← correct
```

### Fix

Remove the misnetworked container and recreate it under the correct project name:

```bash
docker stop lumen-redis && docker rm lumen-redis
docker compose -p lumen1 up -d redis   # explicit project name matches existing stack
```

Then restart all dependent containers so they pick up the new container in DNS:

```bash
docker restart lumen-api lumen-flower lumen-worker
```

The long-term fix is to add `name: lumen1` to `docker-compose.yml` (see entry #17), which makes the project name immutable regardless of invocation flags.

### Lesson

**Never `docker stop && docker rm` a container and recreate it with `docker compose up -d` without explicitly matching the original project name via `-p <name>` or a `name:` field in the compose file.** A missing `-p` flag puts the container on a new Docker network, severing DNS for every other container in the stack. Always verify network membership after recreation:

```bash
docker inspect <container> --format '{{range $k,$v := .NetworkSettings.Networks}}{{$k}}: {{$v.IPAddress}}{{end}}'
```

If networks don't match, the container is on the wrong network and DNS will silently fail.

---

## 22. slowapi Crashes on Redis ConnectionError Due to Wrong Hostname in REDIS_URL

**Commit(s):** ops — 2026-03-14
**Component:** `api/rate_limit.py`
**Severity:** Critical — all API requests returned 500; the error was a slowapi bug triggered by a misconfigured env var

### What Broke

After the Redis DNS breakage (entry #21), `lumen-api` was restarted. The container started successfully but returned `500 Internal Server Error` on every request. The traceback in the logs:

```
File "/usr/local/lib/python3.10/site-packages/slowapi/middleware.py", line 77, in sync_check_limits
    return exception_handler(request, exc), _bool
File "/usr/local/lib/python3.10/site-packages/slowapi/extension.py", line 81, in _rate_limit_exceeded_handler
    {"error": f"Rate limit exceeded: {exc.detail}"}, status_code=429
AttributeError: 'ConnectionError' object has no attribute 'detail'
```

`rate_limit.py` used `os.getenv("REDIS_URL", "redis://redis:6379")` for the slowapi storage URI. The `.env` file had `REDIS_URL=redis://redis:6379` — the hostname `redis` (not `lumen-redis`). This host never resolved inside the Docker network. Every request caused slowapi to attempt a Redis connection, get a `ConnectionError`, and pass it to `_rate_limit_exceeded_handler` — which expected a `RateLimitExceeded` (an `HTTPException` subclass with a `.detail` attribute) and called `exc.detail`, crashing with `AttributeError`.

### Root Cause

Two compounding bugs:
1. `REDIS_URL` in `.env` used the wrong container hostname (`redis` vs `lumen-redis`)
2. slowapi's middleware has a latent bug: it catches any exception from Redis (including `ConnectionError`) and routes it to `_rate_limit_exceeded_handler`, which unconditionally accesses `.detail` — an attribute that only exists on `HTTPException` subclasses, not on generic exceptions

### Fix

In `api/rate_limit.py`, prefer `CELERY_BROKER_URL` (which is always set to the correct container hostname in Compose) over `REDIS_URL`, and add `in_memory_fallback_enabled=True` to fail open if Redis is temporarily unreachable:

```python
# Before
_storage_uri = os.getenv("REDIS_URL", "redis://redis:6379")

# After
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

**Verify that `REDIS_URL` (or any Redis connection string) matches the actual container hostname before deploying.** In Docker Compose stacks, the hostname is the service name — not `redis`, not `localhost`. Run `docker exec <api_container> env | grep REDIS` after any env change to confirm.

Additionally: **add `in_memory_fallback_enabled=True` to any slowapi `Limiter` that uses a Redis backend.** Without it, a transient Redis unavailability (container restart, network blip) crashes every in-flight request with a 500 instead of gracefully degrading to in-memory rate limiting. The slowapi bug (routing non-`RateLimitExceeded` exceptions to `_rate_limit_exceeded_handler`) is upstream, but `in_memory_fallback_enabled` prevents it from ever being triggered.

---

## Summary Table

| # | Bug | Component | Severity | Introduced | Fixed |
|---|-----|-----------|----------|------------|-------|
| 1 | EXIF bytes not JSON-serializable | `worker/tasks.py` | Medium | `ae666df` | `d0cd069` |
| 2 | asyncpg callback using non-existent method | `api/utils/notifications.py` | Critical | `656b40b` | `d0cd069` |
| 3 | Search router never registered (404) | `api/main.py` | Critical | `656b40b` | `d0cd069` |
| 4 | API importing worker ML dependencies | `api/routers/search.py` | Critical | `656b40b` | `d0cd069` |
| 5 | WebSocket URL wrong protocol (http vs ws) | `frontend/hooks/*.ts` | High | `656b0b` | `76743e3` |
| 6 | Infinite WebSocket reconnect, no backoff | `frontend/hooks/*.ts` | High | `656b40b` | `d0cd069` |
| 7 | JSX in `.ts` file (build error) | `frontend/hooks/useMediaUpdates.ts` | Medium | `656b40b` | `d0cd069` |
| 8 | Docker hostname not resolvable in browser | `frontend/hooks/useStatusUpdates.ts` | High | `08b128f` | `76743e3` |
| 9 | CORS credentials+wildcard = 400 on all WS | `api/main.py` | Critical | `08b128f` | `387b50b` |
| 10 | ASGI double-close RuntimeError | `api/routers/updates.py` | High | `08b128f` | `37ef849` |
| 11 | useEffect infinite loop (unstable callbacks) | `frontend/hooks/*.ts` | Critical | `08b128f` | `53501da`, `be03473` |
| 12 | Proxy task blocking indexing queue | `scripts/start-windows-worker-*.ps1` | High | `adbf784` | `feat/windows-native-worker` |
| 13 | Windows paths written into Qdrant payload | `worker/tasks.py` | High | `feat/windows-native-worker` | `feat/windows-native-worker` |
| 14 | `docker restart` ignores env_file changes | `docker-compose*.yml` | Medium | ops | ops |
| 15 | `--no-deps` creates isolated network | `docker-compose*.yml` | High | ops | ops |
| 16 | env_file values not injected without environment: | `docker-compose.second.yml` | Medium | `feat/windows-native-worker` | `feat/windows-native-worker` |
| 17 | Docker project name fragmentation | `docker-compose*.yml` | High | ops | `feat/windows-native-worker` |
| 18 | Volume ownership conflict on project rename | `docker-compose*.yml` | High | `feat/windows-native-worker` | `feat/windows-native-worker` |
| 19 | JSX unescaped `"` breaks Next.js build | `frontend/components/AskPanel.tsx` | Medium | `feat/windows-native-worker` | `feat/windows-native-worker` |
| 20 | Ollama falling back to CPU inference | Ollama / WSL2 | High | ops | ops |
| 21 | Manual container removal breaks Docker DNS | `docker-compose.yml` | Critical | ops | ops |
| 22 | slowapi crashes on Redis ConnectionError (wrong hostname) | `api/rate_limit.py` | Critical | ops | ops |
| 23 | Playlist 400 — S3 object keys not in ALLOWED_ROOTS | `api/routers/ingest.py` | High | `feat/playlist-hls-serve` | `feat/playlist-hls-serve` |
| 24 | Celery prefork + CUDA → forked subprocess crash | `docker-compose.yml` | High | ops | ops |
| 25 | Qdrant healthcheck always fails — no `curl` in image | `docker-compose*.yml` | Low | ops | ops |

---

## Cross-Cutting Themes

### "It works in isolation but not in the browser"
Bugs #5, #8, and #9 all passed every server-side test but failed the moment a browser was involved. The browser imposes constraints (protocol schemes, `Origin` headers, CORS) that `curl`, Python scripts, and Postman typically skip. **Always test from an actual browser before declaring a WebSocket feature complete.**

### Silent failures in async systems
Bugs #2 and #9 produced no obvious errors — the system appeared to run, connections appeared to establish, but no data was ever delivered. Async callbacks that swallow exceptions and middleware that returns 400 without logging are both invisible failure modes. **Instrument everything. Log at entry and exit of every async boundary.**

### Container boundary violations  
Bug #4 illustrates a common monorepo anti-pattern: code in multiple services shares a `worker/` directory structure, making it tempting to import across services. The import worked locally (if you happened to have all packages installed) but crashed the production container. **Treat each container's `requirements.txt` as an inviolable contract. If a module isn't in it, don't import it.**

### React effects and referential stability
Bug #11 is one of the most common advanced React bugs in the industry. It's subtle, reproducible only under specific re-render conditions, and the symptom (network spam) looks nothing like the cause (object identity). Understanding `Object.is()`, referential equality, and the `useRef` pattern for callbacks is essential React knowledge.

### The cost of copy-paste
Bugs #11 (copied between two hooks), #5, and #6 all appeared in multiple files simultaneously because one was based on the other. When fixing a pattern bug, always search the entire codebase for all instances.

### Docker Compose is not a simple process manager
Bugs #14–#18, #21 all stem from treating Docker Compose like a system service manager (`restart` ≈ `systemctl restart`). It is not. Container recreation, network membership, volume naming, and environment injection are all creation-time decisions. Partial restarts, missing `name:` fields, and missing `external:` declarations each create subtle configuration drift that compounds over time into a fragmented, inconsistent cluster. Treat `docker compose up -d` as the canonical deployment operation and `docker restart` as reserved for emergency use only. Manually stopping and removing a container (`docker stop && docker rm`) then recreating it without `-p <project>` is the highest-risk variant — it silently severs DNS for the entire stack.

### Operational changes need the same rigour as code changes
Bugs #12–#20 were all operational rather than code bugs — wrong queue flags, missing compose fields, Ollama GPU init order. They had the same or greater impact as code bugs but left no git trail and were harder to diagnose. Document every operational change (compose flags, env vars, startup order) in the codebase itself, not just in chat logs.

### "It works locally ≠ works on prod"
Bug #23 passed local testing but broke on prod because local used filesystem paths while prod used S3 object keys. The prod environment is meaningfully different: storage backend, path format, auth layer, and network topology all differ. Any feature that touches file paths or storage must be tested against the actual prod storage backend before shipping.

---

## 23. Playlist 400 — S3 Object Keys Not in ALLOWED_ROOTS

**Commit(s):** `feat/playlist-hls-serve`
**Component:** `api/routers/ingest.py`
**Severity:** High — playlist generation silently dropped all R2-backed clips

### What Broke

On prod (`STORAGE_BACKEND=s3`), every clip in a generated playlist returned a 400 Bad Request. The worker logs showed `[Playlist] access denied: pexels-demo/clip.mp4` for every file.

### Root Cause

`ALLOWED_ROOTS` is a list of filesystem paths (`/mnt/source`, `/mnt/proxies`, etc.). S3 stores bare object keys (`pexels-demo/clip.mp4`) with no leading `/`. The path check `any(resolved.startswith(root) for root in ALLOWED_ROOTS)` always returned `False` for S3 keys, causing every clip to be silently dropped.

### Fix

Detect bare S3 keys (no leading `/` when `IS_S3=True`) and generate a presigned URL instead of checking against filesystem roots:

```python
if not any(resolved.startswith(root) for root in ALLOWED_ROOTS):
    if IS_S3 and not clip.file_path.startswith("/"):
        resolved = _s3_presign(clip.file_path, expires=1800)
        is_url = True
    else:
        log.warning("[Playlist] access denied: %s", clip.file_path)
        continue
```

### Lesson

**`ALLOWED_ROOTS` path-prefix checks silently fail for S3 object keys.** S3 keys look like `bucket-prefix/file.mp4` — they are never absolute paths. Any security check that assumes all paths start with `/` must explicitly handle the S3 case. When adding a new storage backend, audit every `startswith("/")` and `os.path` call in the codebase.

---

## 24. Celery Prefork + CUDA → "Cannot Re-initialize CUDA in Forked Subprocess"

**Commit(s):** ops — 2026-03-16
**Component:** `docker-compose.yml`, `worker/ml/embedder.py`
**Severity:** High — all `process_video` and `process_image` tasks failed immediately

### What Broke

After rebuilding the worker container with `docker compose build`, all ML tasks failed with:

```
RuntimeError: Cannot re-initialize CUDA in forked subprocess. To use CUDA with
multiprocessing, you must use the 'spawn' start method
```

### Root Cause

Celery's default pool is `prefork` — it forks child worker processes from the main process. If CUDA is initialized (even partially, via `import torch`) in the parent process before the fork, the child processes inherit the CUDA context and fail when they try to re-initialize it. With `--concurrency=4`, four children all hit this simultaneously.

The secondary issue: even with `USE_GPU=false` at build time, the worker container runs inside WSL2 which exposes NVIDIA drivers. `torch.cuda.is_available()` returned `True`, and the embedder attempted CUDA initialization before the fork guard could prevent it.

### Fix

Two changes:
1. Add `--pool=solo` to the Celery worker command — `solo` runs tasks sequentially in the main process with no forking:
```yaml
command: sh -c "celery -A celery_app worker --pool=solo ..."
```
2. Set `EMBEDDING_DEVICE=cpu` explicitly in the worker environment to prevent any CUDA initialization regardless of hardware availability.

### Lesson

**Any Celery worker that loads a GPU/ML model must use `--pool=solo` or `--pool=threads`.** Prefork + CUDA is fundamentally incompatible because fork copies the CUDA context to child processes where re-initialization fails. `--pool=solo` is the correct setting for single-machine ML workloads. Also: **always set `EMBEDDING_DEVICE=cpu` explicitly when running a CPU-only worker** — relying on `torch.cuda.is_available()` auto-detection is fragile in WSL2 where GPU drivers are visible even in containers built without GPU support.

---

## 25. Qdrant Healthcheck Always Fails — No `curl` or `wget` in Image

**Commit(s):** ops — 2026-03-16
**Component:** `docker-compose.yml`, `docker-compose.second.yml`
**Severity:** Low — false unhealthy status; no functional impact

### What Broke

Both Qdrant containers showed `(unhealthy)` in `docker ps` despite responding correctly to all API requests. The healthcheck was:

```yaml
test: ["CMD-SHELL", "curl -f http://localhost:6333/healthz || exit 1"]
```

### Root Cause

The `qdrant/qdrant` Docker image is a minimal distroless-style image — it contains only the Qdrant binary and its dependencies. Neither `curl` nor `wget` is installed. The healthcheck command failed immediately with `curl: executable file not found`, causing Docker to mark the container unhealthy on every check.

### Fix

Use bash's built-in TCP redirect to check port availability — `bash` is available in the Qdrant image:

```yaml
test: ["CMD-SHELL", "bash -c '</dev/tcp/localhost/6333' 2>/dev/null"]
```

This opens a TCP connection to port 6333. Exit code 0 means the port is open and Qdrant is listening; exit code 1 means it is not.

### Lesson

**Always verify that the tools used in a healthcheck exist inside the target container.** Minimal images (distroless, alpine, vendor-provided) routinely omit `curl`, `wget`, and even `sh`. Before writing a healthcheck, run `docker exec <container> which curl` to confirm. The bash `/dev/tcp` trick is a reliable fallback for pure TCP port checks in any container that has `bash`.

---

## 26. task_acks_late=True Causes Duplicate Task Processing on Worker Restart

**Commit(s):** `945cf20` — 2026-03-20
**Component:** `worker/tasks.py`
**Severity:** High — caused already-completed files to be fully reprocessed (re-embedded, re-indexed into Qdrant)

### What Broke

A video file that was already in `status = "done"` was picked up and fully reprocessed after a worker restart. Celery re-embedded the frames and created duplicate Qdrant points for an already-indexed file.

### Root Cause

`task_acks_late=True` is set on the worker. This means Celery only acknowledges (removes) a task from the broker queue **after** the task completes — not when it starts. If the worker is restarted while a task is in-flight, the broker redelivers it to the next available worker. This guarantees at-least-once delivery, but without an idempotency check the task executes twice.

The `process_video` and `process_image` tasks had no guard against this — they always ran all processing steps regardless of the file's current `processing_status`.

### Fix

Added an idempotency guard at the very start of `process_video` and `process_image`, before any expensive work:

```python
# Idempotency guard — redelivered tasks (task_acks_late=True + restart) must not reprocess.
if media_record.processing_status == "done":
    log.info("Skipping already-done video: %s", file_path)
    return {"status": "skipped", "reason": "already_done"}
```

### Lesson

**`task_acks_late=True` trades task loss for duplicate delivery. Any task that has side effects (DB writes, vector upserts, file I/O) must be idempotent.** The idempotency check must be the very first thing the task does — before any expensive or irreversible operation. The general pattern: read a persistent status flag from the DB; if the work is already done, return early.

---

## 27. Stale DB Records From File Renames Leave Tasks Stuck as `pending` Forever

**Commit(s):** ops — 2026-03-20
**Component:** `worker/tasks.py`, PostgreSQL `media_files` table
**Severity:** Medium — files permanently stuck as `pending`, never retried

### What Broke

Multiple files were showing as `pending` in the DB but never being picked up for processing. The crawler never re-dispatched them.

### Root Cause

The files had been renamed on disk. The crawler matches files by their full `file_path`. When a file is renamed:

1. The old path no longer exists on disk → the crawler never sees it again → the `pending` record is never updated or retried.
2. The new path is treated as a brand new file → a new `done` record is created for it.

The result: two DB records for the same content — one `done` (new name), one `pending` (old name) stuck forever.

### Fix

Identify stale records by cross-referencing `pending` paths against what actually exists on disk, then delete them:

```sql
DELETE FROM media_files
WHERE processing_status = 'pending'
  AND file_path LIKE '<affected_directory>/%';
```

### Lesson

**The crawler has no rename detection — it only matches by exact `file_path`.** Renaming a file on disk orphans its DB record permanently. If files are regularly renamed, either: (a) implement a periodic cleanup query to delete `pending` records whose paths no longer exist on disk, or (b) track files by inode/content hash rather than path. For now, manual cleanup is the remediation.

---

## 28. Windows ffprobe UnicodeDecodeError on Non-Latin File Metadata

**Commit(s):** `945cf20` — 2026-03-20
**Component:** `worker/ingest/ffmpeg.py`
**Severity:** Medium — caused worker crashes for files with non-Latin characters in metadata (Japanese titles, etc.)

### What Broke

Files failed with:

```
UnicodeDecodeError: 'cp1252' codec can't decode byte 0x81 in position N: ...
```

The worker crashed during `ffprobe` metadata extraction and the files were left in `error` status.

### Root Cause

`subprocess.run(..., text=True)` without an explicit `encoding=` argument uses the platform's default encoding. On Windows, this is `cp1252` (Windows-1252). ffprobe outputs UTF-8, including metadata fields that may contain Japanese or other non-Latin characters. When a byte invalid in cp1252 appeared in the output, Python raised `UnicodeDecodeError`.

The Linux worker handled the same files fine because Linux defaults to UTF-8.

### Fix

Added `encoding="utf-8"` explicitly to all `subprocess.run` calls in `ffmpeg.py`:

```python
result = subprocess.run(
    [...],
    capture_output=True,
    text=True,
    encoding="utf-8",  # prevents cp1252 crash on Windows
    timeout=30,
)
```

Applied to both `probe_media()` and `extract_keyframes()`.

### Lesson

**Always specify `encoding="utf-8"` when using `text=True` in `subprocess.run` on cross-platform code.** Never rely on the platform default — Windows cp1252, Linux UTF-8, and macOS UTF-8 will behave differently. Media files routinely contain non-Latin metadata (Japanese, Korean, Arabic titles) that will silently work on Linux/macOS and crash on Windows without explicit encoding.

---

## 29. Qdrant Client API Mismatch (3 commits to fix)

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

## 30. Video Streaming 404 — Silent Routing Conflict (4 commits to fix)

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

## 31. apply_faststart() Silently No-Op on Every File (2 commits + discovery script)

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

## 32. Worker RAM Thrash — Load Average 23.75 (2 commits to stabilize)

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

## 33. Streaming Throughput — 64 KB Chunks vs. 9P Latency (2 commits)

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

## 34. FFmpeg Timeout — Fixed Ceiling vs. Variable Content (PR #5)

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

## 35. Chrome ORB Blocks `<img>` Responses with Non-Image MIME Type

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

## 36. BuildKit Apt Cache Poisoning — Package in Dockerfile, Not in Container

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

## 37. Blocking Proxy Encode in the Critical Pipeline Path (2 commits)

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

## 38. `os.replace()` Fails Across Docker Volume Mount Points

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

## 39. `os.getenv('VAR', default)` Does Not Guard Against Empty String (1 commit)

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

## 40. Video Player "Unknown error" — Four Overlapping Layers (4 fixes to resolve)

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

## 41. SMB Mount Is Read-Only on Remote Host — Two Failure Modes

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

## 42. CLIP Model / Qdrant Collection Dimension Mismatch — Silent Backlog (multi-session)

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

## 43. Docker Disk Bloat — Three Independent Root Causes

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

## 44. Qdrant Collection Not Pre-Created — `VectorParams(size=None)` Silent Retry Loop (2 commits)

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

## 45. DB Schema Drift Between `init-db.sql` and Migration Scripts (3 commits)

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

## 46. API Media Endpoints Were Filesystem-Only — S3 Path Produced Gray Placeholders (2 commits)

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

## 47. FastAPI `List[float]` on a POST Endpoint Is a Body Param, Not a Query Param

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

## 48. Rate Limiter Redis Connection Kills All Tests in CI (109 failures)

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

## 49. One-Character YAML Indentation Error Silently Disabled All CI Jobs

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

## 50. Next.js Module-Level `process.env` Reads Are Captured at Build Time

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

## 51. `qdrant-client` Minor Version Removed `.search()` — Mocked Tests Passed, Prod Returned 0 Results (2 hotfixes)

**What happened:** After merging the v2.0.0 multi-agent feature, `POST /api/agent/query` always returned 0 results on prod. Direct `POST /api/search` worked fine. The agent coordinator returned in 62ms — too fast for CLIP inference — meaning the search node was silently short-circuiting.

**Root cause:** `qdrant-client` was pinned to `>=1.7` in `requirements.txt`. Prod had installed `1.17.0`, which removed `QdrantClient.search()` entirely in favor of `query_points()`. All tests mock Qdrant at the dependency-injection layer (`get_qdrant`), so the mock's `.search()` attribute worked fine in CI. On prod the real client raised `AttributeError`, which was caught by the broad `except Exception` in `QdrantRetrieveStep` and silently turned into an empty result list.

**Compounding bug:** The agent endpoint also accepted `threshold` and `limit` parameters that were never wired into `AgentState` or passed to `search_agent_run()` — they were silently ignored. This was masked by the Qdrant failure (0 results regardless) but would have caused incorrect behavior even after fixing the client.

**Fix:** Replace `self._qdrant.search(...)` with `self._qdrant.query_points(...)` in `QdrantRetrieveStep`. Add `threshold`/`limit` fields to `AgentState` TypedDict and thread them through `run_search_agent` → `search_agent_run()`.

**How to prevent:**
- Pin third-party SDK versions to an exact minor: `qdrant-client>=1.17.0,<2.0` — not open-ended `>=X.Y`
- Add a smoke test that calls the real Qdrant client method path (even with a local Qdrant in CI via a service container) to catch removed APIs
- When a handler catches `Exception` and returns an empty result, log a warning — silent failures are extremely hard to diagnose on prod
- When an endpoint accepts parameters (`threshold`, `limit`), write a test that passes non-default values and asserts they change the outcome

---

## 52. Unclosed Mermaid Code Fence Broke Entire README Render on GitHub

**What happened:** GitHub showed `Unable to render rich display` for the entire README. The Mermaid diagram starting at line 14 had no closing ` ``` ` fence — GitHub's renderer treated everything below it as part of the code block and gave up.

**Why `replace_string_in_file` failed repeatedly:** The file had CRLF line endings (`\r\n`) from Windows. The tool's old-string matching uses exact bytes, and patterns copied from the editor (which normalizes to LF) never matched. `re.search(r'```mermaid', content)` also returned `None` when reading the file in text mode on Windows because line endings were normalized differently at read time vs. what was on disk.

**Fix:** Read the file in binary mode (`open('README.md', 'rb')`), confirm the raw bytes, then use Python to overwrite the affected line range directly. Alternatively, replace the mermaid diagram with a plain ASCII art block which renders everywhere (GitHub, editors, terminals) without renderer support.

**How to prevent:**
- Close every fenced code block — use a linter or pre-commit hook (`markdownlint` rule `MD040`/`MD031`) that catches unclosed fences before push
- When `replace_string_in_file` fails on a known-present string, check line endings first: `file README.md` or read in binary mode before attempting more string replacements
- Prefer ASCII diagrams over Mermaid for README architecture overviews — no renderer dependency, copy-pastes cleanly, works in all diff views
