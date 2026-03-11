"""
Async database session factory for the API service.
"""

from __future__ import annotations

import os

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

_DATABASE_ASYNC_URL = os.getenv(
    "DATABASE_ASYNC_URL",
    "postgresql+asyncpg://lumen_user:secure_password_here@postgres:5432/lumen",
)

_async_engine = None
_AsyncSessionLocal = None


def get_async_engine():
    """Get or create async engine (lazy initialization)."""
    global _async_engine
    if _async_engine is None:
        _async_engine = create_async_engine(_DATABASE_ASYNC_URL, echo=False, future=True)
    return _async_engine


def get_async_session_factory():
    """Get or create async session factory (lazy initialization)."""
    global _AsyncSessionLocal
    if _AsyncSessionLocal is None:
        _AsyncSessionLocal = sessionmaker(
            get_async_engine(), class_=AsyncSession, expire_on_commit=False
        )
    return _AsyncSessionLocal


async def get_async_db():
    """Dependency — yields an async DB session."""
    async with get_async_session_factory()() as session:
        yield session
