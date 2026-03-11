"""
Abstract repository interface + PostgreSQL implementation for MediaFile.

Demonstrates:
  - ISP / DIP: callers depend on the Protocol, not a concrete ORM class
  - Toggle via DB_BACKEND=postgres|mongodb env var
  - CosmosDB-compatible path: MongoDB wire protocol works identically
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Protocol, runtime_checkable


@runtime_checkable
class MediaRepository(Protocol):
    """Minimal interface over persistent media records."""

    async def get_by_id(self, media_id: int) -> Optional[Dict[str, Any]]:
        ...

    async def search_by_metadata(
        self,
        filters: Dict[str, Any],
        limit: int = 20,
    ) -> List[Dict[str, Any]]:
        """Return records matching *all* key-value filters."""
        ...

    async def upsert(self, record: Dict[str, Any]) -> Dict[str, Any]:
        """Insert or update a record; returns the persisted state."""
        ...

    async def delete(self, media_id: int) -> bool:
        """Return True if a record was deleted, False if not found."""
        ...


# ---------------------------------------------------------------------------
# PostgreSQL implementation (SQLAlchemy 2.0 async)
# ---------------------------------------------------------------------------

class PostgresMediaRepository:
    """
    Wraps the existing MediaFile ORM model using an async SQLAlchemy session.

    Usage:
        async with get_async_session() as session:
            repo = PostgresMediaRepository(session)
            records = await repo.search_by_metadata({"year": 2024}, limit=10)
    """

    def __init__(self, session) -> None:
        self._session = session

    async def get_by_id(self, media_id: int) -> Optional[Dict[str, Any]]:
        from sqlalchemy import select
        from api.models import MediaFile  # type: ignore[import]

        stmt = select(MediaFile).where(MediaFile.id == media_id)
        result = await self._session.execute(stmt)
        row = result.scalar_one_or_none()
        return _orm_to_dict(row) if row else None

    async def search_by_metadata(
        self,
        filters: Dict[str, Any],
        limit: int = 20,
    ) -> List[Dict[str, Any]]:
        from sqlalchemy import select
        from api.models import MediaFile  # type: ignore[import]

        stmt = select(MediaFile)
        for attr, value in filters.items():
            if hasattr(MediaFile, attr):
                stmt = stmt.where(getattr(MediaFile, attr) == value)
        stmt = stmt.limit(limit)

        result = await self._session.execute(stmt)
        return [_orm_to_dict(row) for row in result.scalars().all()]

    async def upsert(self, record: Dict[str, Any]) -> Dict[str, Any]:
        from sqlalchemy.dialects.postgresql import insert as pg_insert
        from api.models import MediaFile  # type: ignore[import]

        stmt = (
            pg_insert(MediaFile)
            .values(**record)
            .on_conflict_do_update(
                index_elements=["id"],
                set_={k: v for k, v in record.items() if k != "id"},
            )
            .returning(MediaFile)
        )
        result = await self._session.execute(stmt)
        await self._session.commit()
        return _orm_to_dict(result.scalar_one())

    async def delete(self, media_id: int) -> bool:
        from sqlalchemy import delete
        from api.models import MediaFile  # type: ignore[import]

        stmt = delete(MediaFile).where(MediaFile.id == media_id)
        result = await self._session.execute(stmt)
        await self._session.commit()
        return result.rowcount > 0


def _orm_to_dict(obj) -> Dict[str, Any]:
    """Convert SQLAlchemy ORM instance to plain dict."""
    return {c.name: getattr(obj, c.name) for c in obj.__table__.columns}


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def build_repository(session=None) -> MediaRepository:
    """
    Return the correct MediaRepository implementation based on DB_BACKEND.

    DB_BACKEND=postgres  →  PostgresMediaRepository  (default)
    DB_BACKEND=mongodb   →  MongoDBMediaRepository   (see mongo_repository.py)
    """
    import os
    backend = os.getenv("DB_BACKEND", "postgres").lower()

    if backend == "mongodb":
        from api.db.mongo_repository import MongoDBMediaRepository  # type: ignore[import]
        return MongoDBMediaRepository()

    if session is None:
        raise ValueError("PostgresMediaRepository requires a SQLAlchemy async session")
    return PostgresMediaRepository(session)
