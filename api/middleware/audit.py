"""
Audit logging middleware — compliance/observability for every API call.

Records each request/response pair to the ``audit_logs`` Postgres table via a
fire-and-forget asyncio task.  The task is deliberately **not** awaited during
the request cycle so it can never inflate response latency or cause a handled
error to surface to the caller.

What is persisted per row:
    timestamp         — UTC time the response was returned
    endpoint          — path (query string stripped)
    method            — HTTP verb
    request_body_hash — SHA-256 of raw body bytes (NOT the body itself — avoids
                        storing secrets, credentials, or large binary uploads)
    response_status   — HTTP status code
    response_ms       — full round-trip wall time in milliseconds
    client_ip         — X-Forwarded-For header (first hop) or direct remote addr
    user_agent        — User-Agent header (first 512 chars)

Health-check paths (/api/health, /api/metrics, /docs, /openapi.json) are
excluded to avoid polluting the log with heartbeat noise.

Env vars:
    DATABASE_ASYNC_URL   — postgresql+asyncpg://... (same as the rest of the app)
    AUDIT_ENABLED        — "false" to disable entirely (default: "true")
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from typing import Optional

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession

log = logging.getLogger(__name__)

# Paths we never want to audit (exact prefix match)
_SKIP_PREFIXES = (
    "/api/health",
    "/api/metrics",
    "/docs",
    "/redoc",
    "/openapi.json",
    "/favicon.ico",
)


def _client_ip(request: Request) -> Optional[str]:
    """Extract the real client IP respecting reverse-proxy headers."""
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else None


def _sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


async def _write_audit_row(
    endpoint: str,
    method: str,
    body_hash: Optional[str],
    status: int,
    elapsed_ms: int,
    client_ip: Optional[str],
    user_agent: Optional[str],
) -> None:
    """
    Persist one audit row.  Failures are logged but never re-raised
    so the calling middleware task never surfaces them to the caller.
    """
    try:
        # Import lazily to avoid circular imports and allow the app to start
        # even if the DB is temporarily unreachable.
        from db.models import AuditLog
        from db.session import get_async_session_factory

        factory = get_async_session_factory()
        async with factory() as session:
            row = AuditLog(
                endpoint=endpoint[:512],
                method=method,
                request_body_hash=body_hash,
                response_status=status,
                response_ms=elapsed_ms,
                client_ip=client_ip,
                user_agent=(user_agent or "")[:512] if user_agent else None,
            )
            session.add(row)
            await session.commit()
    except Exception:
        log.exception("AuditMiddleware: failed to write audit row for %s %s", method, endpoint)


class AuditMiddleware(BaseHTTPMiddleware):
    """
    Starlette middleware that asynchronously logs every significant request.

    Usage (in api/main.py, after app is created):
        from middleware.audit import AuditMiddleware
        app.add_middleware(AuditMiddleware)
    """

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        import os
        if os.getenv("AUDIT_ENABLED", "true").lower() == "false":
            return await call_next(request)

        path = request.url.path
        if any(path.startswith(prefix) for prefix in _SKIP_PREFIXES):
            return await call_next(request)

        # Read body for hashing; stash it back so the route handler can re-read it.
        body = b""
        if request.method in ("POST", "PUT", "PATCH"):
            body = await request.body()
        body_hash = _sha256_hex(body) if body else None

        t0 = time.perf_counter()
        response = await call_next(request)
        elapsed_ms = int((time.perf_counter() - t0) * 1000)

        # Fire-and-forget — never block the caller.
        # Catch errors from ensure_future itself (e.g. loop not running in tests)
        # so the response is always returned to the caller.
        try:
            asyncio.ensure_future(
                _write_audit_row(
                    endpoint=path,
                    method=request.method,
                    body_hash=body_hash,
                    status=response.status_code,
                    elapsed_ms=elapsed_ms,
                    client_ip=_client_ip(request),
                    user_agent=request.headers.get("User-Agent"),
                )
            )
        except Exception:
            log.exception("AuditMiddleware: could not schedule audit task for %s %s", request.method, path)

        return response
