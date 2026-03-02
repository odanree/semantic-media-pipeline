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

## Summary Table

| # | Bug | Component | Severity | Introduced | Fixed |
|---|-----|-----------|----------|------------|-------|
| 1 | EXIF bytes not JSON-serializable | `worker/tasks.py` | Medium | `ae666df` | `d0cd069` |
| 2 | asyncpg callback using non-existent method | `api/utils/notifications.py` | Critical | `656b40b` | `d0cd069` |
| 3 | Search router never registered (404) | `api/main.py` | Critical | `656b40b` | `d0cd069` |
| 4 | API importing worker ML dependencies | `api/routers/search.py` | Critical | `656b40b` | `d0cd069` |
| 5 | WebSocket URL wrong protocol (http vs ws) | `frontend/hooks/*.ts` | High | `656b40b` | `76743e3` |
| 6 | Infinite WebSocket reconnect, no backoff | `frontend/hooks/*.ts` | High | `656b40b` | `d0cd069` |
| 7 | JSX in `.ts` file (build error) | `frontend/hooks/useMediaUpdates.ts` | Medium | `656b40b` | `d0cd069` |
| 8 | Docker hostname not resolvable in browser | `frontend/hooks/useStatusUpdates.ts` | High | `08b128f` | `76743e3` |
| 9 | CORS credentials+wildcard = 400 on all WS | `api/main.py` | Critical | `08b128f` | `387b50b` |
| 10 | ASGI double-close RuntimeError | `api/routers/updates.py` | High | `08b128f` | `37ef849` |
| 11 | useEffect infinite loop (unstable callbacks) | `frontend/hooks/*.ts` | Critical | `08b128f` | `53501da`, `be03473` |

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
