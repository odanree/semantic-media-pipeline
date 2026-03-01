"""
FastAPI Backend - Lumen Media Pipeline API
"""

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

# Import routers
from routers import health, ingest, updates

# Initialize FastAPI app
app = FastAPI(
    title="Lumen API",
    description="Semantic media indexing and search API",
    version="1.1.0",
)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Configure as needed for production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include routers
app.include_router(health.router, prefix="/api", tags=["health"])
app.include_router(ingest.router, prefix="/api", tags=["ingest"])
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
