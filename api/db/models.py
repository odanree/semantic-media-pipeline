"""
SQLAlchemy ORM models used by the API service.

Only includes models that the API service writes to directly.
Models shared with the worker (MediaFile, etc.) live in worker/db/models.py
and are accessed via the repository layer.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import JSON, TIMESTAMP, Boolean, Column, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import declarative_base

Base = declarative_base()


class MediaFile(Base):
    """MediaFile ORM model — mirrors worker/db/models.py for API-side queries."""

    __tablename__ = "media_files"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    file_hash = Column(String(64), unique=True, nullable=False, index=True)
    file_path = Column(Text, nullable=False)
    file_type = Column(String(10), nullable=False, index=True)
    file_size_bytes = Column(String)
    width = Column(String)
    height = Column(String)
    duration_secs = Column(String)
    created_at = Column(TIMESTAMP(timezone=True), default=datetime.utcnow, index=True)
    exif_data = Column(JSON, nullable=True)
    qdrant_point_id = Column(UUID(as_uuid=True), nullable=True)
    processing_status = Column(String(20), default="pending", index=True)
    error_message = Column(Text, nullable=True)
    processed_at = Column(TIMESTAMP(timezone=True), nullable=True)
    embedding_started_at = Column(TIMESTAMP(timezone=True), nullable=True)
    worker_id = Column(String, nullable=True, index=True)
    frame_cache_hit = Column(Boolean, nullable=True)
    embedding_ms = Column(Integer, nullable=True)
    model_version = Column(String(100), nullable=True, index=True)

    def __repr__(self) -> str:
        return f"<MediaFile(id={self.id}, file_path={self.file_path}, status={self.processing_status})>"


class AuditLog(Base):
    """
    Immutable audit log for every non-health API request.

    Written by api/middleware/audit.py via fire-and-forget asyncio task so the
    response latency is never affected.  The request body is hashed (SHA-256)
    rather than stored verbatim to avoid logging PII or large binary payloads.
    """

    __tablename__ = "audit_logs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    timestamp = Column(
        TIMESTAMP(timezone=True), default=datetime.utcnow, nullable=False, index=True
    )
    endpoint = Column(String(512), nullable=False, index=True)
    method = Column(String(10), nullable=False)
    request_body_hash = Column(String(64), nullable=True)  # SHA-256 hex; NULL for GETs
    response_status = Column(Integer, nullable=False, index=True)
    response_ms = Column(Integer, nullable=False)
    client_ip = Column(String(45), nullable=True)   # IPv4 or IPv6
    user_agent = Column(String(512), nullable=True)

    def __repr__(self) -> str:
        return (
            f"<AuditLog(id={self.id}, endpoint={self.endpoint!r}, "
            f"status={self.response_status}, ts={self.timestamp})>"
        )


Index("idx_audit_timestamp", AuditLog.timestamp)
Index("idx_audit_endpoint", AuditLog.endpoint)
Index("idx_audit_status", AuditLog.response_status)
