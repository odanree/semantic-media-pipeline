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
Bugs #14–#18 all stem from treating Docker Compose like a system service manager (`restart` ≈ `systemctl restart`). It is not. Container recreation, network membership, volume naming, and environment injection are all creation-time decisions. Partial restarts, missing `name:` fields, and missing `external:` declarations each create subtle configuration drift that compounds over time into a fragmented, inconsistent cluster. Treat `docker compose up -d` as the canonical deployment operation and `docker restart` as reserved for emergency use only.

### Operational changes need the same rigour as code changes
Bugs #12–#20 were all operational rather than code bugs — wrong queue flags, missing compose fields, Ollama GPU init order. They had the same or greater impact as code bugs but left no git trail and were harder to diagnose. Document every operational change (compose flags, env vars, startup order) in the codebase itself, not just in chat logs.
