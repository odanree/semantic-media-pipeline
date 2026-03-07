"""
FastAPI Backend - Lumen Media Pipeline API
"""

# CRITICAL: Patch MUST happen before ANY other imports
# Fix transformers.integrations.accelerate NameError for 'nn'
import sys
import builtins
import torch.nn as nn
builtins.nn = nn  # Inject nn into builtins so it's globally accessible

import os
from datetime import datetime

from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

import numpy as np
from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from qdrant_client import QdrantClient

# Import routers (these may use sentence_transformers internally)
from routers import health, ingest, search, updates, stats
from auth import require_api_key
from rate_limit import limiter, LIMIT_DEFAULT
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware

# Initialize FastAPI app
# require_api_key is a global dependency — every route in every router is
# automatically protected. Toggle with API_KEY_REQUIRED=true in .env.
app = FastAPI(
    title="Lumen API",
    description="Semantic media indexing and search API",
    version="1.2.0",
    dependencies=[Depends(require_api_key)],
)

# Attach rate limiter — must happen before add_middleware
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_middleware(SlowAPIMiddleware)

# Add CORS middleware
# Note: allow_credentials=True is incompatible with allow_origins=["*"]
# Using explicit origins list to allow both localhost variants
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include routers
app.include_router(health.router, prefix="/api", tags=["health"])
app.include_router(ingest.router, prefix="/api", tags=["ingest"])
app.include_router(search.router, prefix="/api", tags=["search"])
app.include_router(updates.router, prefix="/api", tags=["realtime"])
app.include_router(stats.router, prefix="/api", tags=["observability"])


# ============================================================================
# Global initialization
# ============================================================================


@app.on_event("startup")
async def startup_event():
    """Initialize on startup"""
    print("Lumen API starting up...")
    print(f"Qdrant host: {os.getenv('QDRANT_HOST', 'qdrant')}")
    print(f"Database URL: {os.getenv('DATABASE_ASYNC_URL', '***')}")
    api_key_required = os.getenv("API_KEY_REQUIRED", "false").lower() in ("true", "1", "yes")
    api_key_set = bool(os.getenv("API_KEY", "").strip())
    if api_key_required and api_key_set:
        print("Auth: API key required (X-API-Key header)")
    elif api_key_required and not api_key_set:
        print("Auth: API_KEY_REQUIRED=true but API_KEY not set — all requests will be rejected!")
    else:
        print("Auth: disabled (API_KEY_REQUIRED=false — set to true for production)")
    print(f"Rate limits: search={os.getenv('RATE_LIMIT_SEARCH','30/min')}, stream={os.getenv('RATE_LIMIT_STREAM','60/min')}, default={os.getenv('RATE_LIMIT_DEFAULT','200/min')} [Redis: {os.getenv('REDIS_URL','redis://redis:6379')}]")

    # Preload CLIP model so first search request is instant
    import asyncio
    from routers.search import get_clip_model
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, get_clip_model)


@app.on_event("shutdown")
async def shutdown_event():
    """Cleanup on shutdown"""
    print("Lumen API shutting down...")


# ============================================================================
# Root endpoint
# ============================================================================


@app.get("/")
async def root():
    """Root endpoint"""
    return {
        "name": "Lumen API",
        "version": "0.1.0",
        "status": "running",
        "timestamp": datetime.utcnow().isoformat(),
    }


# ============================================================================
# Pydantic models (used across endpoints)
# ============================================================================


class SearchRequest(BaseModel):
    """Text search request"""

    query: str
    limit: int = 20
    threshold: float = 0.3


class SearchResult(BaseModel):
    """Search result item"""

    file_path: str
    file_type: str
    similarity: float
    frame_index: int = None
    timestamp: float = None


class SearchResponse(BaseModel):
    """Search response"""

    query: str
    results: list
    count: int
    execution_time_ms: float


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        app,
        host=os.getenv("API_HOST", "0.0.0.0"),
        port=int(os.getenv("API_PORT", "8000")),
        reload=os.getenv("API_RELOAD", "false").lower() == "true",
    )
