"""
Tests for the database repository layer.

MongoDBMediaRepository: motor async collection is injected via the
_collection attribute so no real MongoDB connection is attempted.

PostgresMediaRepository: SQLAlchemy async session is mocked; api.models.MediaFile
is stubbed via conftest sys.modules injection so the lazy imports work.
"""

import asyncio
import sys
import types
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers — async iterable factory for motor cursor mocking
# ---------------------------------------------------------------------------

def _async_docs(*docs):
    """Return an async iterable over the given dicts (motor cursor mock)."""
    async def _gen():
        for doc in docs:
            yield doc
    return _gen()


def _inject_mongo_collection(repo, col):
    """Bypass _get_collection() lazy-init by injecting directly."""
    repo._collection = col


# ---------------------------------------------------------------------------
# _normalize helper
# ---------------------------------------------------------------------------

class TestNormalize:
    def test_maps_underscore_id_to_id(self):
        from db.mongo_repository import _normalize

        result = _normalize({"_id": 42, "file_path": "a.jpg"})
        assert result["id"] == 42
        assert "_id" not in result

    def test_passthrough_when_no_underscore_id(self):
        from db.mongo_repository import _normalize

        doc = {"id": 1, "file_path": "b.jpg"}
        result = _normalize(doc)
        assert result == doc

    def test_empty_dict_returns_empty(self):
        from db.mongo_repository import _normalize

        assert _normalize({}) == {}

    def test_none_returns_empty(self):
        from db.mongo_repository import _normalize

        assert _normalize(None) == {}


# ---------------------------------------------------------------------------
# MongoDBMediaRepository
# ---------------------------------------------------------------------------

class TestMongoDBMediaRepository:
    def _make_repo_and_col(self):
        from db.mongo_repository import MongoDBMediaRepository

        repo = MongoDBMediaRepository()
        col = MagicMock(name="motor_collection")
        col.find_one = AsyncMock()
        col.replace_one = AsyncMock()
        col.delete_one = AsyncMock()
        _inject_mongo_collection(repo, col)
        return repo, col

    def test_get_by_id_found_returns_normalised_dict(self):
        repo, col = self._make_repo_and_col()
        col.find_one.return_value = {"_id": 7, "file_path": "photo.jpg"}
        result = asyncio.run(repo.get_by_id(7))
        assert result == {"id": 7, "file_path": "photo.jpg"}
        col.find_one.assert_awaited_once_with({"_id": 7})

    def test_get_by_id_not_found_returns_none(self):
        repo, col = self._make_repo_and_col()
        col.find_one.return_value = None
        result = asyncio.run(repo.get_by_id(99))
        assert result is None

    def test_search_by_metadata_returns_normalised_list(self):
        repo, col = self._make_repo_and_col()
        col.find.return_value.limit.return_value = _async_docs(
            {"_id": 1, "file_type": "image"},
            {"_id": 2, "file_type": "video"},
        )
        results = asyncio.run(repo.search_by_metadata({"file_type": "image"}, limit=10))
        assert len(results) == 2
        assert results[0]["id"] == 1
        assert results[1]["id"] == 2

    def test_search_by_metadata_empty_result(self):
        repo, col = self._make_repo_and_col()
        col.find.return_value.limit.return_value = _async_docs()
        results = asyncio.run(repo.search_by_metadata({}))
        assert results == []

    def test_upsert_with_id_field_uses_id_as_mongo_key(self):
        repo, col = self._make_repo_and_col()
        saved = {"_id": 5, "file_path": "new.jpg"}
        col.find_one.return_value = saved
        result = asyncio.run(repo.upsert({"id": 5, "file_path": "new.jpg"}))
        # replace_one should be called with _id=5
        replace_args = col.replace_one.await_args
        assert replace_args.args[0] == {"_id": 5}
        assert result["id"] == 5

    def test_upsert_without_id_field(self):
        repo, col = self._make_repo_and_col()
        # Doc has no 'id' key — _id must already be in the dict
        saved = {"_id": 10, "file_path": "x.jpg"}
        col.find_one.return_value = saved
        result = asyncio.run(repo.upsert({"_id": 10, "file_path": "x.jpg"}))
        assert result["id"] == 10

    def test_delete_existing_returns_true(self):
        repo, col = self._make_repo_and_col()
        col.delete_one.return_value = MagicMock(deleted_count=1)
        result = asyncio.run(repo.delete(3))
        assert result is True
        col.delete_one.assert_awaited_once_with({"_id": 3})

    def test_delete_missing_returns_false(self):
        repo, col = self._make_repo_and_col()
        col.delete_one.return_value = MagicMock(deleted_count=0)
        result = asyncio.run(repo.delete(999))
        assert result is False


# ---------------------------------------------------------------------------
# PostgresMediaRepository
# ---------------------------------------------------------------------------

class TestPostgresMediaRepository:
    def _make_repo(self):
        from db.repository import PostgresMediaRepository

        session = MagicMock(name="async_session")
        session.execute = AsyncMock()
        session.commit = AsyncMock()
        return PostgresMediaRepository(session), session

    def _mock_select(self):
        """Return a MagicMock for sqlalchemy.select that chains .where()."""
        mock_stmt = MagicMock()
        mock_stmt.where.return_value = mock_stmt
        mock_stmt.limit.return_value = mock_stmt
        return MagicMock(return_value=mock_stmt), mock_stmt

    def test_get_by_id_not_found_returns_none(self):
        repo, session = self._make_repo()
        execute_result = MagicMock()
        execute_result.scalar_one_or_none.return_value = None
        session.execute.return_value = execute_result
        mock_select, _ = self._mock_select()
        with patch("sqlalchemy.select", mock_select):
            result = asyncio.run(repo.get_by_id(42))
        assert result is None

    def test_get_by_id_found_returns_dict(self):
        repo, session = self._make_repo()
        row = MagicMock()
        row.id = 1
        row.file_path = "found.jpg"
        col_id = MagicMock(); col_id.name = "id"
        col_fp = MagicMock(); col_fp.name = "file_path"
        row.__table__ = MagicMock()
        row.__table__.columns = [col_id, col_fp]
        execute_result = MagicMock()
        execute_result.scalar_one_or_none.return_value = row
        session.execute.return_value = execute_result
        mock_select, _ = self._mock_select()
        with patch("sqlalchemy.select", mock_select):
            result = asyncio.run(repo.get_by_id(1))
        assert result == {"id": 1, "file_path": "found.jpg"}

    def test_delete_returns_true_when_row_deleted(self):
        repo, session = self._make_repo()
        session.execute.return_value.rowcount = 1
        mock_stmt = MagicMock(); mock_stmt.where.return_value = mock_stmt
        with patch("sqlalchemy.delete", MagicMock(return_value=mock_stmt)):
            result = asyncio.run(repo.delete(1))
        assert result is True

    def test_delete_returns_false_when_no_row(self):
        repo, session = self._make_repo()
        session.execute.return_value.rowcount = 0
        mock_stmt = MagicMock(); mock_stmt.where.return_value = mock_stmt
        with patch("sqlalchemy.delete", MagicMock(return_value=mock_stmt)):
            result = asyncio.run(repo.delete(999))
        assert result is False


# ---------------------------------------------------------------------------
# build_repository factory
# ---------------------------------------------------------------------------

class TestBuildRepository:
    def test_defaults_to_postgres_with_session(self):
        from db.repository import build_repository, PostgresMediaRepository

        session = MagicMock()
        with pytest.MonkeyPatch.context() as mp:
            mp.setenv("DB_BACKEND", "postgres")
            repo = build_repository(session=session)
        assert isinstance(repo, PostgresMediaRepository)

    def test_postgres_without_session_raises(self):
        from db.repository import build_repository

        with pytest.MonkeyPatch.context() as mp:
            mp.setenv("DB_BACKEND", "postgres")
            with pytest.raises(ValueError, match="async session"):
                build_repository(session=None)

    def test_mongodb_backend_returns_mongo_repo(self):
        from db.repository import build_repository

        with pytest.MonkeyPatch.context() as mp:
            mp.setenv("DB_BACKEND", "mongodb")
            repo = build_repository()
        assert type(repo).__name__ == "MongoDBMediaRepository"


# ---------------------------------------------------------------------------
# Protocol runtime-checkable conformance
# ---------------------------------------------------------------------------

class TestMediaRepositoryProtocol:
    def test_postgres_repo_satisfies_protocol(self):
        from db.repository import MediaRepository, PostgresMediaRepository

        session = MagicMock()
        repo = PostgresMediaRepository(session)
        assert isinstance(repo, MediaRepository)

    def test_mongo_repo_satisfies_protocol(self):
        from db.mongo_repository import MongoDBMediaRepository
        from db.repository import MediaRepository

        repo = MongoDBMediaRepository()
        assert isinstance(repo, MediaRepository)
