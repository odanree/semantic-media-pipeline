"""
Local Filesystem Storage Backend
"""

import os
from pathlib import Path
from typing import List

from .base import StorageBackend


class LocalStorage(StorageBackend):
    """Local filesystem storage backend"""

    def __init__(self, base_path: str = "/data/media"):
        self.base_path = Path(base_path)
        self.base_path.mkdir(parents=True, exist_ok=True)

    def _get_full_path(self, path: str) -> Path:
        """Get full path, ensuring it's within base_path (security)"""
        full_path = (self.base_path / path).resolve()
        if not str(full_path).startswith(str(self.base_path.resolve())):
            raise ValueError(f"Path {path} is outside base_path {self.base_path}")
        return full_path

    def read(self, path: str) -> bytes:
        """Read file contents"""
        full_path = self._get_full_path(path)
        with open(full_path, "rb") as f:
            return f.read()

    def write(self, path: str, data: bytes) -> None:
        """Write file contents"""
        full_path = self._get_full_path(path)
        full_path.parent.mkdir(parents=True, exist_ok=True)
        with open(full_path, "wb") as f:
            f.write(data)

    def exists(self, path: str) -> bool:
        """Check if a file exists"""
        try:
            full_path = self._get_full_path(path)
            return full_path.exists()
        except ValueError:
            return False

    def list_dir(self, path: str) -> List[str]:
        """List files in a directory"""
        full_path = self._get_full_path(path)
        if not full_path.is_dir():
            return []
        return [str(p.relative_to(self.base_path)) for p in full_path.iterdir()]

    def delete(self, path: str) -> None:
        """Delete a file"""
        full_path = self._get_full_path(path)
        if full_path.exists():
            full_path.unlink()

    def get_url(self, path: str, expires_in: int = 3600) -> str:
        """Get a URL for the file (for local, return file path)"""
        return str(self._get_full_path(path))
