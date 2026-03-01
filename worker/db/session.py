"""
Database session configuration
"""

import os

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

# Async engine for FastAPI/async contexts (created lazily on first use)
DATABASE_ASYNC_URL = os.getenv(
    "DATABASE_ASYNC_URL",
    "postgresql+asyncpg://lumen_user:secure_password_here@postgres:5432/lumen",
)

_async_engine = None
_AsyncSessionLocal = None


def get_async_engine():
    """Get or create async engine (lazy initialization)"""
    global _async_engine
    if _async_engine is None:
        _async_engine = create_async_engine(DATABASE_ASYNC_URL, echo=False, future=True)
    return _async_engine


def get_async_session_factory():
    """Get or create async session factory (lazy initialization)"""
    global _AsyncSessionLocal
    if _AsyncSessionLocal is None:
        _AsyncSessionLocal = sessionmaker(
            get_async_engine(), class_=AsyncSession, expire_on_commit=False
        )
    return _AsyncSessionLocal


async def get_async_db():
    """Get async database session"""
    async with get_async_session_factory()() as session:
        yield session


# Sync engine for Celery tasks (created lazily on first use, not at import)
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker as sync_sessionmaker

DATABASE_SYNC_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://lumen_user:secure_password_here@postgres:5432/lumen",
)

_sync_engine = None
_SyncSessionLocal = None


def get_sync_engine():
    """Get or create sync engine (lazy initialization)"""
    global _sync_engine
    if _sync_engine is None:
        _sync_engine = create_engine(DATABASE_SYNC_URL, echo=False)
    return _sync_engine


def get_sync_session_factory():
    """Get or create sync session factory (lazy initialization)"""
    global _SyncSessionLocal
    if _SyncSessionLocal is None:
        _SyncSessionLocal = sync_sessionmaker(
            bind=get_sync_engine(), expire_on_commit=False
        )
    return _SyncSessionLocal


def SyncSessionLocal():
    """Get sync database session (lazy factory pattern)"""
    return get_sync_session_factory()()


def get_sync_db():
    """Get sync database session for Celery tasks"""
    session = SyncSessionLocal()
    try:
        yield session
    finally:
        session.close()
