"""
Storage backend factory
"""

import os

from .base import StorageBackend
from .gcs import GCSStorage
from .local import LocalStorage
from .s3 import S3Storage


def get_storage_backend(backend_name: str = None) -> StorageBackend:
    """Factory function to get the configured storage backend"""
    backend_name = backend_name or os.getenv("STORAGE_BACKEND", "local").lower()

    if backend_name == "local":
        media_root = os.getenv("MEDIA_ROOT", "/data/media")
        return LocalStorage(media_root)

    elif backend_name == "s3":
        bucket = os.getenv("S3_BUCKET")
        if not bucket:
            raise ValueError("S3_BUCKET environment variable is required for S3 storage")
        return S3Storage(bucket=bucket)

    elif backend_name == "gcs":
        bucket = os.getenv("GCS_BUCKET")
        if not bucket:
            raise ValueError("GCS_BUCKET environment variable is required for GCS storage")
        return GCSStorage(bucket=bucket)

    else:
        raise ValueError(f"Unknown storage backend: {backend_name}")
