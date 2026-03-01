"""
Search endpoint
"""

import os
import time
from datetime import datetime

import numpy as np
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from qdrant_client import QdrantClient
from sqlalchemy.ext.asyncio import AsyncSession

from db.session import get_async_db
from ml.embedder import get_embedder

router = APIRouter()

# Initialize Qdrant client
QDRANT_HOST = os.getenv("QDRANT_HOST", "qdrant")
QDRANT_PORT = int(os.getenv("QDRANT_PORT", "6333"))
QDRANT_GRPC_PORT = int(os.getenv("QDRANT_GRPC_PORT", "6334"))
QDRANT_PREFER_GRPC = os.getenv("QDRANT_PREFER_GRPC", "true").lower() == "true"
QDRANT_COLLECTION_NAME = os.getenv("QDRANT_COLLECTION_NAME", "media_vectors")

qdrant_client = QdrantClient(
    host=QDRANT_HOST,
    port=QDRANT_PORT,
    grpc_port=QDRANT_GRPC_PORT,
    prefer_grpc=QDRANT_PREFER_GRPC,
)


class SearchRequest(BaseModel):
    """Search request model"""

    query: str
    limit: int = 20
    threshold: float = 0.3


class EmbedTextRequest(BaseModel):
    """Embed text request model"""

    query: str


class SearchResult(BaseModel):
    """Individual search result"""

    file_path: str
    file_type: str
    similarity: float
    frame_index: int = None
    timestamp: float = None


class SearchResponse(BaseModel):
    """Search response model"""

    query: str
    results: list
    count: int
    execution_time_ms: float


@router.post("/search", response_model=SearchResponse)
async def search_media(request: SearchRequest):
    """
    Search for media using a text query.

    Args:
        query: Text search query
        limit: Maximum number of results
        threshold: Minimum similarity threshold (0-1)

    Returns:
        List of matching media with similarity scores
    """
    try:
        start_time = time.time()

        # Get embedder
        embedder = get_embedder()

        # Embed query
        query_embedding = embedder.embed_text(request.query)
        query_vector = query_embedding[0].astype(np.float32).tolist()

        # Search Qdrant
        search_result = qdrant_client.search(
            collection_name=QDRANT_COLLECTION_NAME,
            query_vector=query_vector,
            limit=request.limit,
            with_payload=True,
        )

        # Process results
        results = []
        for point in search_result:
            if point.score >= request.threshold:
                payload = point.payload
                result = SearchResult(
                    file_path=payload.get("file_path"),
                    file_type=payload.get("file_type"),
                    similarity=float(point.score),
                    frame_index=payload.get("frame_index"),
                    timestamp=payload.get("timestamp"),
                )
                results.append(result)

        execution_time_ms = (time.time() - start_time) * 1000

        return SearchResponse(
            query=request.query,
            results=results,
            count=len(results),
            execution_time_ms=execution_time_ms,
        )

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/embed-text")
async def embed_text(request: EmbedTextRequest):
    """
    Embed a text query.

    Args:
        query: Text to embed

    Returns:
        Vector embedding
    """
    try:
        embedder = get_embedder()
        embedding = embedder.embed_text(request.query)
        vector = embedding[0].astype(np.float32).tolist()

        return {
            "query": request.query,
            "embedding": vector,
            "dimension": len(vector),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
