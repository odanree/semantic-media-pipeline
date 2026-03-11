"""
Shared FastAPI dependency providers — DIP (Dependency Inversion Principle).

All new routers (agent, rag) inject these via FastAPI's Depends().
Existing routers (ask.py, search.py) are unchanged — they keep their own
module-level singletons. Migrate those lazily as they are touched.

Usage:
    from fastapi import Depends
    from dependencies import get_qdrant, get_clip_model, get_llm_provider

    @router.post("/endpoint")
    async def my_endpoint(qdrant: QdrantClient = Depends(get_qdrant)):
        ...
"""

import os
from functools import lru_cache
from typing import Optional

from qdrant_client import QdrantClient

# ---------------------------------------------------------------------------
# Qdrant
# ---------------------------------------------------------------------------

_qdrant_client: Optional[QdrantClient] = None


def get_qdrant() -> QdrantClient:
    """Singleton QdrantClient — shared across all new routers."""
    global _qdrant_client
    if _qdrant_client is None:
        _qdrant_client = QdrantClient(
            host=os.getenv("QDRANT_HOST", "qdrant"),
            port=int(os.getenv("QDRANT_PORT", "6333")),
            grpc_port=int(os.getenv("QDRANT_GRPC_PORT", "6334")),
            prefer_grpc=os.getenv("QDRANT_PREFER_GRPC", "true").lower() == "true",
        )
    return _qdrant_client


# ---------------------------------------------------------------------------
# CLIP model
# ---------------------------------------------------------------------------

_clip_model = None


def get_clip_model():
    """Singleton CLIP SentenceTransformer — shared across all new routers."""
    global _clip_model
    if _clip_model is None:
        import torch
        from sentence_transformers import SentenceTransformer

        model_name = os.getenv("CLIP_MODEL_NAME", "clip-ViT-L-14")
        device = "cuda" if torch.cuda.is_available() else "cpu"
        _clip_model = SentenceTransformer(model_name, device=device)
    return _clip_model


# ---------------------------------------------------------------------------
# LLM provider (uses ILLMProvider abstraction)
# ---------------------------------------------------------------------------

def get_llm_provider():
    """
    Factory: returns the configured ILLMProvider implementation.
    Set LLM_PROVIDER env var: openai | azure | local
    """
    from llm.factory import build_llm_provider
    return build_llm_provider()


# ---------------------------------------------------------------------------
# Collection name — single source of truth
# ---------------------------------------------------------------------------

def get_collection_name() -> str:
    return os.getenv("QDRANT_COLLECTION_NAME", "media_vectors")
