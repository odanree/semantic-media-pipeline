"""
Storage Backend Abstraction Layer
Supports local filesystem, S3, and Google Cloud Storage
"""

from abc import ABC, abstractmethod
from pathlib import Path
from typing import List, Optional


class StorageBackend(ABC):
    """Abstract base class for storage backends"""

    @abstractmethod
    def read(self, path: str) -> bytes:
        """Read file contents"""
        pass

    @abstractmethod
    def write(self, path: str, data: bytes) -> None:
        """Write file contents"""
        pass

    @abstractmethod
    def exists(self, path: str) -> bool:
        """Check if a file exists"""
        pass

    @abstractmethod
    def list_dir(self, path: str) -> List[str]:
        """List files in a directory"""
        pass

    @abstractmethod
    def delete(self, path: str) -> None:
        """Delete a file"""
        pass

    @abstractmethod
    def get_url(self, path: str, expires_in: int = 3600) -> str:
        """Get a URL for the file (for streaming)"""
        pass
