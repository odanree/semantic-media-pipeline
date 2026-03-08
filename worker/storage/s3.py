"""
S3 Storage Backend
Supports AWS S3 and S3-compatible services (MinIO, etc.)
"""

import os
from typing import List, Optional
from urllib.parse import urlencode

import boto3
from botocore.exceptions import ClientError

from .base import StorageBackend


class S3Storage(StorageBackend):
    """S3 (and S3-compatible) storage backend"""

    def __init__(
        self,
        bucket: str,
        endpoint_url: Optional[str] = None,
        access_key: Optional[str] = None,
        secret_key: Optional[str] = None,
        region: str = "us-east-1",
    ):
        self.bucket = bucket
        self.region = region

        # Get credentials from env if not provided
        access_key = access_key or os.getenv("S3_ACCESS_KEY", "")
        secret_key = secret_key or os.getenv("S3_SECRET_KEY", "")
        endpoint_url = endpoint_url or os.getenv("S3_ENDPOINT_URL")

        session_kwargs = {
            "aws_access_key_id": access_key,
            "aws_secret_access_key": secret_key,
        }

        client_kwargs = {"region_name": region}
        if endpoint_url:
            client_kwargs["endpoint_url"] = endpoint_url

        session = boto3.Session(**session_kwargs)
        self.s3_client = session.client("s3", **client_kwargs)

    def read(self, path: str) -> bytes:
        """Read file contents from S3"""
        try:
            response = self.s3_client.get_object(Bucket=self.bucket, Key=path)
            return response["Body"].read()
        except ClientError as e:
            raise FileNotFoundError(f"S3 object not found: {path}") from e

    def write(self, path: str, data: bytes) -> None:
        """Write file contents to S3"""
        self.s3_client.put_object(Bucket=self.bucket, Key=path, Body=data)

    def exists(self, path: str) -> bool:
        """Check if a file exists in S3"""
        try:
            self.s3_client.head_object(Bucket=self.bucket, Key=path)
            return True
        except ClientError:
            return False

    def list_dir(self, path: str) -> List[str]:
        """List files in an S3 prefix"""
        prefix = path.rstrip("/") + "/" if path else ""
        paginator = self.s3_client.get_paginator("list_objects_v2")
        pages = paginator.paginate(Bucket=self.bucket, Prefix=prefix)

        files = []
        for page in pages:
            if "Contents" in page:
                files.extend([obj["Key"] for obj in page["Contents"]])
        return files

    def delete(self, path: str) -> None:
        """Delete a file from S3"""
        self.s3_client.delete_object(Bucket=self.bucket, Key=path)

    def get_url(self, path: str, expires_in: int = 3600) -> str:
        """Generate a presigned URL for the file"""
        try:
            url = self.s3_client.generate_presigned_url(
                "get_object",
                Params={"Bucket": self.bucket, "Key": path},
                ExpiresIn=expires_in,
            )
            return url
        except ClientError:
            raise ValueError(f"Could not generate presigned URL for {path}")

    def head(self, path: str) -> dict:
        """Return object metadata (size, etag) without downloading the body."""
        try:
            resp = self.s3_client.head_object(Bucket=self.bucket, Key=path)
            return {
                "size": resp["ContentLength"],
                "etag": resp.get("ETag", "").strip('"'),
            }
        except ClientError as e:
            raise FileNotFoundError(f"S3 object not found: {path}") from e

    def read_partial(self, path: str, num_bytes: int) -> bytes:
        """Read the first num_bytes of an S3 object using a byte-range request."""
        try:
            resp = self.s3_client.get_object(
                Bucket=self.bucket,
                Key=path,
                Range=f"bytes=0-{num_bytes - 1}",
            )
            return resp["Body"].read()
        except ClientError as e:
            raise FileNotFoundError(f"S3 object not found: {path}") from e


from typing import Optional
