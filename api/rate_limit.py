"""
Rate limiting — slowapi with Redis backend.

Limits are per client IP. Redis backend (lumen-redis) persists counters
across container restarts and is accurate across multiple worker processes.

Per-route limits (override via env vars to tune without rebuild):
  POST /api/search          RATE_LIMIT_SEARCH      default 30/minute
  POST /api/search-vector   RATE_LIMIT_SEARCH_VEC  default 60/minute
  GET  /api/stream          RATE_LIMIT_STREAM       default 60/minute
  GET  /api/thumbnail       RATE_LIMIT_THUMBNAIL   default 120/minute
  everything else           RATE_LIMIT_DEFAULT     default 200/minute

429 responses include a Retry-After header automatically via slowapi.
"""

import os

from slowapi import Limiter
from slowapi.util import get_remote_address

# Redis-backed counters — falls back gracefully to in-memory if Redis is
# unreachable (acceptable for local dev, not for production).
# Prefer CELERY_BROKER_URL (always set to the correct container hostname in Compose)
# over REDIS_URL (which may point to a stale/wrong hostname from .env).
_storage_uri = (
    os.getenv("CELERY_BROKER_URL")
    or os.getenv("REDIS_URL", "redis://lumen-redis:6379")
)

# Per-route limit strings — configurable without rebuilding the container.
LIMIT_SEARCH     = os.getenv("RATE_LIMIT_SEARCH",     "30/minute")
LIMIT_SEARCH_VEC = os.getenv("RATE_LIMIT_SEARCH_VEC", "60/minute")
LIMIT_STREAM     = os.getenv("RATE_LIMIT_STREAM",     "60/minute")
LIMIT_THUMBNAIL  = os.getenv("RATE_LIMIT_THUMBNAIL",  "120/minute")
LIMIT_ASK        = os.getenv("RATE_LIMIT_ASK",        "10/minute")
LIMIT_DEFAULT    = os.getenv("RATE_LIMIT_DEFAULT",    "200/minute")

limiter = Limiter(
    key_func=get_remote_address,   # rate-key = client IP
    storage_uri=_storage_uri,
    default_limits=[LIMIT_DEFAULT],
    in_memory_fallback_enabled=True,  # fail open if Redis is temporarily unreachable
)
