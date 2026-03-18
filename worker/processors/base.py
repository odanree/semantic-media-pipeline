"""
BaseProcessor — interface every file-type processor must implement.
"""

from abc import ABC, abstractmethod
from typing import FrozenSet


class BaseProcessor(ABC):

    @property
    @abstractmethod
    def file_type(self) -> str:
        """
        Canonical string written to media_files.file_type and Qdrant payload.
        Must be <= 10 chars (VARCHAR(10) column).  Examples: "image", "video", "document"
        """

    @property
    @abstractmethod
    def extensions(self) -> FrozenSet[str]:
        """Lowercase dot-prefixed extensions this processor handles."""

    @property
    def hash_full_file(self) -> bool:
        """
        True  → hash the entire file (images: small, content-sensitive).
        False → hash only the first 8 KB (videos/docs: large, header is unique enough).
        """
        return True

    @abstractmethod
    def get_celery_task(self):
        """
        Return the @app.task decorated function that processes this file type.
        Callers do: processor.get_celery_task().delay(file_path, record_id)
        The task name (module.function) must remain stable across deploys.
        """
