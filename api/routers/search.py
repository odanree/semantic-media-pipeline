"""
Search endpoint - Vector similarity search in Qdrant
"""

import os
import time
from typing import List

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from qdrant_client import QdrantClient

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


@router.get("/search-status")
async def search_status():
    """
    Health check for search service - verify Qdrant is reachable
    """
    try:
        collections = qdrant_client.get_collections()
        collection_count = len(collections.collections)
        collection_names = [c.name for c in collections.collections]
        
        return {
            "status": "healthy",
            "qdrant_host": QDRANT_HOST,
            "qdrant_port": QDRANT_PORT,
            "collection_count": collection_count,
            "collections": collection_names,
            "target_collection": QDRANT_COLLECTION_NAME,
            "target_collection_exists": QDRANT_COLLECTION_NAME in collection_names,
        }
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Qdrant connection failed: {str(e)}")


@router.post("/search", response_model=SearchResponse)
async def search_media(request: SearchRequest):
    """
    Search for media using text query.
    
    NOTE: This endpoint requires text-to-vector embedding.
    The full implementation will integrate with Celery workers.
    
    For now, returns placeholder results.
    
    Args:
        query: Text search query
        limit: Maximum number of results (default: 20)
        threshold: Minimum similarity threshold 0-1 (default: 0.3)
    
    Returns:
        List of matching media with similarity scores
    """
    try:
        start_time = time.time()
        
        # TODO: Integrate with Celery worker for text embedding
        # For now, return empty results
        results = []
        
        execution_time_ms = (time.time() - start_time) * 1000
        
        return SearchResponse(
            query=request.query,
            results=results,
            count=0,
            execution_time_ms=execution_time_ms,
        )
    
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/search-vector")
async def search_by_vector(
    vector: List[float],
    limit: int = 20,
    threshold: float = 0.3
):
    """
    Search Qdrant using a pre-computed embedding vector.
    
    This endpoint is useful when you already have a vector embedding
    and just need to search Qdrant.
    
    Args:
        vector: Pre-computed embedding vector
        limit: Maximum number of results
        threshold: Minimum similarity threshold
    
    Returns:
        List of matching media with similarity scores
    """
    try:
        start_time = time.time()
        
        if not vector:
            raise ValueError("Vector cannot be empty")
        
        # Search Qdrant
        search_result = qdrant_client.search(
            collection_name=QDRANT_COLLECTION_NAME,
            query_vector=vector,
            limit=limit,
            with_payload=True,
        )
        
        # Process results
        results = []
        for point in search_result:
            if point.score >= threshold:
                payload = point.payload
                result = {
                    "file_path": payload.get("file_path"),
                    "file_type": payload.get("file_type"),
                    "similarity": float(point.score),
                    "frame_index": payload.get("frame_index"),
                    "timestamp": payload.get("timestamp"),
                }
                results.append(result)
        
        execution_time_ms = (time.time() - start_time) * 1000
        
        return {
            "vector_dimension": len(vector),
            "results": results,
            "count": len(results),
            "execution_time_ms": execution_time_ms,
        }
    
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
