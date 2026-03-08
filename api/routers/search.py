"""
Search endpoint - Vector similarity search in Qdrant
"""

import os
import time
from typing import List, Optional

import numpy as np
import torch
from fastapi import APIRouter, HTTPException, Request
from rate_limit import limiter, LIMIT_SEARCH, LIMIT_SEARCH_VEC
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


# Initialize CLIP embedder (lazy-loaded)
_clip_model: Optional[object] = None
EMBEDDER_AVAILABLE = False


def _get_device() -> str:
    """Detect best available compute device."""
    try:
        import torch_directml
        torch.zeros(1, device=torch_directml.device())
        return "cpu"  # DirectML not available in API container, use CPU
    except Exception:
        pass

    if torch.cuda.is_available():
        return "cuda"

    return "cpu"


def get_clip_model():
    """Get or create the CLIP model instance (lazy loading)."""
    global _clip_model, EMBEDDER_AVAILABLE

    if _clip_model is None:
        try:
            # Import SentenceTransformer (should work now that accelerate.py is patched)
            from sentence_transformers import SentenceTransformer

            model_name = os.getenv("CLIP_MODEL_NAME", "clip-ViT-L-14")
            device = _get_device()
            print(f"Loading {model_name} on device: {device}")
            _clip_model = SentenceTransformer(model_name, device=device)
            EMBEDDER_AVAILABLE = True
            print("✓ CLIP embedder loaded successfully")
        except Exception as e:
            print(f"✗ Failed to load CLIP embedder: {e}")
            import traceback
            traceback.print_exc()
            EMBEDDER_AVAILABLE = False
            raise

    return _clip_model


class SearchRequest(BaseModel):
    """Search request model"""

    query: str
    limit: int = 20
    threshold: float = 0.2


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
@limiter.limit(LIMIT_SEARCH)
async def search_media(request: Request, body: SearchRequest):
    """
    Search for media using text query.

    Embeds the text query using CLIP and searches Qdrant for similar embeddings.

    Args:
        query: Text search query
        limit: Maximum number of results (default: 20)
        threshold: Minimum similarity threshold 0-1 (default: 0.3)

    Returns:
        List of matching media with similarity scores
    """
    if not body.query or not body.query.strip():
        raise HTTPException(status_code=400, detail="Query cannot be empty")

    try:
        start_time = time.time()

        # Load CLIP model (lazy-loaded on first use)
        try:
            model = get_clip_model()
        except Exception as e:
            raise HTTPException(
                status_code=503,
                detail=f"CLIP embedder failed to load: {str(e)}"
            )

        # Embed the text query using CLIP
        query_embedding = model.encode(body.query, convert_to_tensor=False)
        if isinstance(query_embedding, np.ndarray):
            query_vector = query_embedding.tolist()
        else:
            query_vector = query_embedding

        # Search Qdrant using query_points (qdrant-client v1.7+ API)
        search_result = qdrant_client.query_points(
            collection_name=QDRANT_COLLECTION_NAME,
            query=query_vector,
            limit=body.limit,
            with_payload=True,
            score_threshold=body.threshold,
        ).points

        # Process results
        results = []
        for point in search_result:
            payload = point.payload
            result = {
                "id": point.id,
                "file_path": payload.get("file_path"),
                "file_type": payload.get("file_type"),
                "similarity": float(point.score),
                "frame_index": payload.get("frame_index"),
                "timestamp": payload.get("timestamp"),
            }
            results.append(result)

        execution_time_ms = (time.time() - start_time) * 1000

        return SearchResponse(
            query=body.query,
            results=results,
            count=len(results),
            execution_time_ms=execution_time_ms,
        )

    except HTTPException:
        raise
    except Exception as e:
        print(f"Search error: {e}")
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Search failed: {str(e)}")


@router.post("/search-vector")
@limiter.limit(LIMIT_SEARCH_VEC)
async def search_by_vector(
    request: Request,
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

        # Search Qdrant using query_points (qdrant-client v1.7+ API)
        search_result = qdrant_client.query_points(
            collection_name=QDRANT_COLLECTION_NAME,
            query=vector,
            limit=limit,
            with_payload=True,
            score_threshold=threshold,
        ).points

        # Process results
        results = []
        for point in search_result:
            payload = point.payload
            result = {
                "id": point.id,
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
