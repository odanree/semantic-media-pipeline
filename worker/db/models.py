"""
Database Models and ORM Configuration
"""

import os
import uuid
import hashlib
from datetime import datetime
from typing import Optional

from sqlalchemy import JSON, TIMESTAMP, Boolean, Column, Index, Integer, String, Text, create_engine
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import declarative_base, sessionmaker

Base = declarative_base()


class MediaFile(Base):
    """MediaFile model for storing metadata about processed media"""

    __tablename__ = "media_files"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    file_hash = Column(String(64), unique=True, nullable=False, index=True)
    file_path = Column(Text, nullable=False)
    file_type = Column(String(10), nullable=False, index=True)  # 'image', 'video'
    file_size_bytes = Column(String)  # for large numbers
    width = Column(String)  # nullable for videos without metadata
    height = Column(String)  # nullable for videos without metadata
    duration_secs = Column(String)  # nullable for images
    created_at = Column(TIMESTAMP(timezone=True), default=datetime.utcnow, index=True)
    exif_data = Column(JSON, nullable=True)  # EXIF metadata as JSON
    qdrant_point_id = Column(UUID(as_uuid=True), nullable=True)  # Link to Qdrant vector
    processing_status = Column(
        String(20), default="pending", index=True
    )  # pending, processing, done, error
    error_message = Column(Text, nullable=True)  # If processing failed
    processed_at = Column(TIMESTAMP(timezone=True), nullable=True)
    # Observability columns (added Phase 2)
    embedding_started_at = Column(TIMESTAMP(timezone=True), nullable=True)  # Queue wait = embedding_started_at - created_at
    worker_id = Column(String, nullable=True, index=True)  # hostname: Mac vs Windows attribution
    frame_cache_hit = Column(Boolean, nullable=True)  # Video only: True = skipped FFmpeg
    embedding_ms = Column(Integer, nullable=True)  # CLIP inference wall time in ms
    model_version = Column(String(100), nullable=True, index=True)  # CLIP model that produced this vector (e.g. clip-ViT-L-14)

    def __repr__(self):
        return f"<MediaFile(id={self.id}, file_path={self.file_path}, status={self.processing_status})>"


# Create indexes
Index("idx_file_hash", MediaFile.file_hash, unique=True)
Index("idx_processing_status", MediaFile.processing_status)
Index("idx_file_type", MediaFile.file_type)
Index("idx_created_at", MediaFile.created_at)


class AuditLog(Base):
    """
    Immutable audit log for every non-health API request.

    Written by api/middleware/audit.py via fire-and-forget asyncio task so the
    response latency is never affected.  The request body is hashed (SHA-256)
    rather than stored verbatim to avoid logging PII or large binary payloads.

    Compliance note: this table should be treated as append-only.  Add a DB-level
    policy or use a write-only Postgres role for the API service account.
    """
    __tablename__ = "audit_logs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    timestamp = Column(TIMESTAMP(timezone=True), default=datetime.utcnow, nullable=False, index=True)
    endpoint = Column(String(512), nullable=False, index=True)
    method = Column(String(10), nullable=False)
    request_body_hash = Column(String(64), nullable=True)   # SHA-256 hex of raw body; NULL for GETs
    response_status = Column(Integer, nullable=False, index=True)
    response_ms = Column(Integer, nullable=False)           # full round-trip latency
    client_ip = Column(String(45), nullable=True)           # IPv4 or IPv6
    user_agent = Column(String(512), nullable=True)

    def __repr__(self) -> str:
        return (
            f"<AuditLog(id={self.id}, endpoint={self.endpoint!r}, "
            f"status={self.response_status}, ts={self.timestamp})>"
        )


Index("idx_audit_timestamp", AuditLog.timestamp)
Index("idx_audit_endpoint", AuditLog.endpoint)
Index("idx_audit_status", AuditLog.response_status)


async def get_async_engine():
    """Create async database engine"""
    database_url = os.getenv(
        "DATABASE_ASYNC_URL",
        "postgresql+asyncpg://lumen_user:secure_password_here@postgres:5432/lumen",
    )
    return create_async_engine(database_url, echo=False, future=True)


async def get_async_session(engine=None):
    """Get async session factory"""
    if engine is None:
        engine = await get_async_engine()

    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    return async_session


def get_sync_engine():
    """Create sync database engine (for Celery tasks)"""
    database_url = os.getenv(
        "DATABASE_URL",
        "postgresql://lumen_user:secure_password_here@postgres:5432/lumen",
    )
    return create_engine(database_url, echo=False)


def get_sync_session(engine=None):
    """Get sync session factory"""
    if engine is None:
        engine = get_sync_engine()

    return sessionmaker(bind=engine, expire_on_commit=False)
