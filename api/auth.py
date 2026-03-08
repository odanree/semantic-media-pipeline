"""
API Key Authentication
======================
Lightweight guard for all API endpoints.

Configuration (in .env):
    API_KEY_REQUIRED=true      # Set to false to disable (default: false for local dev)
    API_KEY=your-secret-key    # The key clients must send

Usage:
    Clients must send the header:
        X-API-Key: your-secret-key

    When API_KEY_REQUIRED=false (default), all requests pass through — no header needed.
    When API_KEY_REQUIRED=true but API_KEY is empty, startup will log a warning.
"""

import logging
import os

from fastapi import Depends, HTTPException, Security, status
from fastapi.security.api_key import APIKeyHeader

log = logging.getLogger(__name__)

_API_KEY_HEADER = APIKeyHeader(name="X-API-Key", auto_error=False)

_REQUIRED = os.getenv("API_KEY_REQUIRED", "false").strip().lower() in ("true", "1", "yes")
_API_KEY   = os.getenv("API_KEY", "").strip()

if _REQUIRED and not _API_KEY:
    log.warning(
        "API_KEY_REQUIRED=true but API_KEY is not set. "
        "All requests will be rejected until API_KEY is configured."
    )


async def require_api_key(api_key: str = Security(_API_KEY_HEADER)) -> None:
    """
    FastAPI dependency — inject into any route or router to enforce auth.

    - If API_KEY_REQUIRED=false (default), passes unconditionally.
    - If API_KEY_REQUIRED=true, validates X-API-Key header against API_KEY env var.
    """
    if not _REQUIRED:
        return  # Auth disabled — local dev mode

    if not _API_KEY:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="API key authentication is required but API_KEY is not configured.",
        )

    if not api_key or api_key != _API_KEY:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing API key. Set X-API-Key header.",
        )
