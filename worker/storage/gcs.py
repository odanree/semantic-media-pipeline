"""
Google Cloud Storage Backend
"""

import os
from typing import List, Optional

from google.cloud import storage

from .base import StorageBackend


class GCSStorage(StorageBackend):
    """Google Cloud Storage backend"""

    def __init__(
        self,
        bucket: str,
        project_id: Optional[str] = None,
        credentials_path: Optional[str] = None,
    ):
        self.bucket_name = bucket
        self.project_id = project_id or os.getenv("GCS_PROJECT_ID")

        # Use service account credentials if provided
        if credentials_path:
            from google.oauth2 import service_account

            credentials = service_account.Credentials.from_service_account_file(
                credentials_path
            )
            self.storage_client = storage.Client(
                project=self.project_id, credentials=credentials
            )
        else:
            self.storage_client = storage.Client(project=self.project_id)

        self.bucket = self.storage_client.bucket(self.bucket_name)

    def read(self, path: str) -> bytes:
        """Read file contents from GCS"""
        blob = self.bucket.blob(path)
        if not blob.exists():
            raise FileNotFoundError(f"GCS object not found: {path}")
        return blob.download_as_bytes()

    def write(self, path: str, data: bytes) -> None:
        """Write file contents to GCS"""
        blob = self.bucket.blob(path)
        blob.upload_from_string(data)

    def exists(self, path: str) -> bool:
        """Check if a file exists in GCS"""
        blob = self.bucket.blob(path)
        return blob.exists()

    def list_dir(self, path: str) -> List[str]:
        """List files in a GCS prefix"""
        prefix = path.rstrip("/") + "/" if path else ""
        blobs = self.storage_client.list_blobs(
            self.bucket_name, prefix=prefix, delimiter=None
        )
        return [blob.name for blob in blobs]

    def delete(self, path: str) -> None:
        """Delete a file from GCS"""
        blob = self.bucket.blob(path)
        if blob.exists():
            blob.delete()

    def get_url(self, path: str, expires_in: int = 3600) -> str:
        """Generate a signed URL for the file"""
        blob = self.bucket.blob(path)
        from datetime import timedelta

        return blob.generate_signed_url(
            version="v4",
            expiration=timedelta(seconds=expires_in),
            method="GET",
        )


from typing import Optional
