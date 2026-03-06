"""
CLIP Embedder - Multimodal embedding using OpenAI CLIP model

Device priority:
  1. DirectML  (/dev/dxg via torch-directml, works on WSL2 + AMD/Intel/NVIDIA)
  2. CUDA/ROCm (torch.cuda, works on native Linux with proper drivers)
  3. MPS       (Apple Metal, works on macOS M-series)
  4. CPU       (fallback)
"""

import logging
import os
from typing import List, Optional, Union

import numpy as np
import torch
from PIL import Image
from sentence_transformers import SentenceTransformer

# Suppress noisy transformers info-level warnings that fire on every model load.
# use_fast=True is the new default and is fine; position_ids UNEXPECTED keys are
# expected when loading a CLIPModel from a SentenceTransformer checkpoint.
logging.getLogger("transformers.models.clip.image_processing_clip").setLevel(logging.ERROR)
logging.getLogger("transformers.modeling_utils").setLevel(logging.ERROR)

log = logging.getLogger(__name__)


def _detect_device() -> str:
    """
    Detect the best available compute device.

    Returns:
        Device string usable by PyTorch / SentenceTransformer.
    """
    # 1. Try DirectML (WSL2 + any DirectX 12 GPU)
    try:
        import torch_directml

        dml_device = torch_directml.device()  # e.g. 'privateuseone:0'
        # Quick sanity: allocate a tiny tensor to confirm the device works
        torch.zeros(1, device=dml_device)
        log.info("DirectML device detected: %s", dml_device)
        return str(dml_device)
    except Exception as exc:
        log.info("DirectML not available (%s), trying CUDA/ROCm...", exc)

    # 2. CUDA / ROCm
    if torch.cuda.is_available():
        log.info("CUDA/ROCm device detected: %s", torch.cuda.get_device_name(0))
        return "cuda"

    # 3. Apple Metal (MPS) — M-series Macs
    if hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
        log.info("Apple MPS device detected (Metal)")
        return "mps"

    # 4. CPU fallback
    log.info("No GPU backend available — using CPU")
    return "cpu"


class CLIPEmbedder:
    """CLIP embedder for images and text"""

    def __init__(
        self, model_name: str = "clip-ViT-B-32", device: Optional[str] = None
    ):
        """
        Initialize CLIP embedder.

        Args:
            model_name: HuggingFace model name (default: clip-ViT-B-32)
            device: Device to use ('privateuseone:0', 'cuda', 'cpu', or auto-detect)
        """
        self.model_name = model_name

        # Auto-detect best device if not specified
        if device is None:
            self.device = _detect_device()
        else:
            self.device = device

        log.info("Loading %s on device: %s", model_name, self.device)
        self.model = SentenceTransformer(model_name, device=self.device)
        # Query the model directly — never hardcode; changes with model name
        self.embedding_dim = self.model.get_sentence_embedding_dimension()

    def embed_images(
        self, image_paths: List[str], batch_size: int = 32
    ) -> np.ndarray:
        """
        Embed multiple images.

        Args:
            image_paths: List of image file paths
            batch_size: Batch size for inference (default: 32)

        Returns:
            NumPy array of shape (len(image_paths), embedding_dim)
        """
        images = []
        for image_path in image_paths:
            try:
                img = Image.open(image_path).convert("RGB")
                images.append(img)
            except Exception as e:
                log.warning("Skipping unreadable image %s: %s", image_path, e)
                # Do NOT append a placeholder — a black image produces a valid
                # embedding that would be stored in Qdrant as legitimate data,
                # poisoning search results with fake vectors.
                continue

        # Embed in batches
        embeddings = self.model.encode(
            images, batch_size=batch_size, convert_to_numpy=True
        )

        return embeddings.astype(np.float32)

    def embed_text(self, text: Union[str, List[str]]) -> np.ndarray:
        """
        Embed text query/queries.

        Args:
            text: Single text string or list of text strings

        Returns:
            NumPy array of shape (1, embedding_dim) if single text, (len(text), embedding_dim) if list
        """
        if isinstance(text, str):
            text = [text]

        embeddings = self.model.encode(text, convert_to_numpy=True)
        return embeddings.astype(np.float32)

    def embed_frames(
        self,
        frame_paths: List[str],
        batch_size: int = 32,
        skip_errors: bool = True,
    ) -> np.ndarray:
        """
        Embed video frames.

        Args:
            frame_paths: List of frame file paths
            batch_size: Batch size for inference
            skip_errors: If True, skip frames that fail to load

        Returns:
            NumPy array of shape (len(frame_paths), embedding_dim)
        """
        return self.embed_images(frame_paths, batch_size=batch_size)

    def get_embedding_dimension(self) -> int:
        """Get embedding dimension"""
        return self.embedding_dim


# Global embedder instance (lazy-loaded)
_embedder = None


def get_embedder(model_name: Optional[str] = None) -> CLIPEmbedder:
    """
    Get or create the global CLIP embedder instance.
    Uses lazy loading to avoid loading the model until needed.

    Args:
        model_name: Model name (default: from env var CLIP_MODEL_NAME)

    Returns:
        CLIPEmbedder instance
    """
    global _embedder

    resolved_name = model_name or os.getenv("CLIP_MODEL_NAME", "clip-ViT-B-32")

    # Invalidate cache if a different model is requested — returning the wrong
    # model silently is worse than the overhead of reloading.
    if _embedder is not None and _embedder.model_name != resolved_name:
        log.warning(
            "get_embedder() called with '%s' but cached instance is '%s' — reloading.",
            resolved_name,
            _embedder.model_name,
        )
        _embedder = None

    if _embedder is None:
        device = os.getenv("EMBEDDING_DEVICE", "").strip() or None
        _embedder = CLIPEmbedder(resolved_name, device)

    return _embedder
