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
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from qdrant_client import QdrantClient

# Import routers (these may use sentence_transformers internally)
from routers import health, ingest, search, updates

# Initialize FastAPI app
app = FastAPI(
    title="Lumen API",
    description="Semantic media indexing and search API",
    version="1.2.0",
)

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


# ============================================================================
# Global initialization
# ============================================================================


@app.on_event("startup")
async def startup_event():
    """Initialize on startup"""
    print("Lumen API starting up...")
    print(f"Qdrant host: {os.getenv('QDRANT_HOST', 'qdrant')}")
    print(f"Database URL: {os.getenv('DATABASE_ASYNC_URL', '***')}")

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
