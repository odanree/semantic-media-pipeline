"""
Tests for AuditMiddleware and AuditLog ORM model.

Middleware tests call AuditMiddleware.dispatch() directly with mock
Request/Response objects and patch asyncio.ensure_future so no background
tasks are scheduled — avoids any network I/O in tests.
"""

import asyncio
import hashlib
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class TestAuditLogModel:
    def test_model_has_required_columns(self):
        from db.models import AuditLog
        cols = {c.key for c in AuditLog.__table__.columns}
        required = {"id", "timestamp", "endpoint", "method",
                    "request_body_hash", "response_status", "response_ms",
                    "client_ip", "user_agent"}
        assert required.issubset(cols)

    def test_model_tablename(self):
        from db.models import AuditLog
        assert AuditLog.__tablename__ == "audit_logs"

    def test_repr_contains_endpoint(self):
        from db.models import AuditLog
        row = AuditLog(endpoint="/api/search", method="POST",
                       response_status=200, response_ms=42)
        assert "/api/search" in repr(row)

    def test_id_column_has_uuid_default(self):
        from db.models import AuditLog
        id_col = AuditLog.__table__.columns["id"]
        assert id_col.default is not None
        assert callable(id_col.default.arg)

    def test_indexes_registered(self):
        from db.models import AuditLog
        index_cols = {col.name for idx in AuditLog.__table__.indexes for col in idx.columns}
        assert "timestamp" in index_cols
        assert "endpoint" in index_cols


class TestAuditHelpers:
    def test_sha256_hex_64_chars(self):
        from middleware.audit import _sha256_hex
        result = _sha256_hex(b"sensitive data")
        assert len(result) == 64
        assert all(c in "0123456789abcdef" for c in result)

    def test_sha256_hex_matches_hashlib(self):
        from middleware.audit import _sha256_hex
        data = b"api key: sk-test-12345"
        assert _sha256_hex(data) == hashlib.sha256(data).hexdigest()

    def test_sha256_hex_deterministic(self):
        from middleware.audit import _sha256_hex
        assert _sha256_hex(b"same") == _sha256_hex(b"same")

    def test_sha256_hex_different_inputs_differ(self):
        from middleware.audit import _sha256_hex
        assert _sha256_hex(b"aaa") != _sha256_hex(b"bbb")

    def test_client_ip_from_forwarded_header(self):
        from middleware.audit import _client_ip
        req = MagicMock()
        req.headers = {"X-Forwarded-For": "1.2.3.4, 5.6.7.8"}
        req.client = MagicMock()
        req.client.host = "10.0.0.1"
        assert _client_ip(req) == "1.2.3.4"

    def test_client_ip_fallback_to_remote_addr(self):
        from middleware.audit import _client_ip
        req = MagicMock()
        req.headers = {}
        req.client = MagicMock()
        req.client.host = "192.168.1.50"
        assert _client_ip(req) == "192.168.1.50"

    def test_client_ip_no_client_returns_none(self):
        from middleware.audit import _client_ip
        req = MagicMock()
        req.headers = {}
        req.client = None
        assert _client_ip(req) is None


class TestAuditMiddlewareDispatch:
    @pytest.fixture()
    def middleware(self):
        from middleware.audit import AuditMiddleware
        return AuditMiddleware(app=MagicMock())

    @staticmethod
    def _req(path="/api/search", method="GET"):
        req = MagicMock()
        req.url.path = path
        req.method = method
        req.headers = {"User-Agent": "pytest/1.0"}
        req.client = MagicMock()
        req.client.host = "127.0.0.1"
        req.body = AsyncMock(return_value=b"")
        return req

    @staticmethod
    def _resp(status=200):
        r = MagicMock()
        r.status_code = status
        return r

    def test_health_path_skipped(self, middleware):
        with patch("middleware.audit.asyncio.ensure_future") as m:
            asyncio.run(middleware.dispatch(self._req("/api/health"), AsyncMock(return_value=self._resp())))
        m.assert_not_called()

    def test_docs_path_skipped(self, middleware):
        with patch("middleware.audit.asyncio.ensure_future") as m:
            asyncio.run(middleware.dispatch(self._req("/docs"), AsyncMock(return_value=self._resp())))
        m.assert_not_called()

    def test_metrics_path_skipped(self, middleware):
        with patch("middleware.audit.asyncio.ensure_future") as m:
            asyncio.run(middleware.dispatch(self._req("/api/metrics"), AsyncMock(return_value=self._resp())))
        m.assert_not_called()

    def test_regular_path_schedules_audit(self, middleware, monkeypatch):
        monkeypatch.setenv("AUDIT_ENABLED", "true")
        with patch("middleware.audit.asyncio.ensure_future") as m:
            asyncio.run(middleware.dispatch(self._req("/api/search"), AsyncMock(return_value=self._resp(200))))
        m.assert_called_once()

    def test_audit_disabled_env_skips_future(self, middleware, monkeypatch):
        monkeypatch.setenv("AUDIT_ENABLED", "false")
        with patch("middleware.audit.asyncio.ensure_future") as m:
            asyncio.run(middleware.dispatch(self._req("/api/search"), AsyncMock(return_value=self._resp())))
        m.assert_not_called()

    def test_response_returned_even_if_ensure_future_fails(self, middleware, monkeypatch):
        monkeypatch.setenv("AUDIT_ENABLED", "true")
        resp = self._resp(202)
        with patch("middleware.audit.asyncio.ensure_future", side_effect=RuntimeError("loop closed")):
            result = asyncio.run(middleware.dispatch(self._req("/api/ingest"), AsyncMock(return_value=resp)))
        assert result.status_code == 202

    def test_post_body_is_hashed(self, middleware, monkeypatch):
        monkeypatch.setenv("AUDIT_ENABLED", "true")
        body = b'{"query": "cat photos"}'
        req = self._req("/api/search", "POST")
        req.body = AsyncMock(return_value=body)

        mock_write = AsyncMock()

        def _noop_future(coro):
            # Close unawaited coroutine to silence RuntimeWarning
            coro.close()

        with patch("middleware.audit._write_audit_row", mock_write):
            with patch("middleware.audit.asyncio.ensure_future", side_effect=_noop_future):
                asyncio.run(middleware.dispatch(req, AsyncMock(return_value=self._resp())))

        # _write_audit_row was called — inspect positional/keyword args
        mock_write.assert_called_once()
        _, kwargs = mock_write.call_args
        assert kwargs.get("body_hash") == hashlib.sha256(body).hexdigest()

    def test_get_body_hash_is_none(self, middleware, monkeypatch):
        monkeypatch.setenv("AUDIT_ENABLED", "true")
        mock_write = AsyncMock()

        def _noop_future(coro):
            coro.close()

        with patch("middleware.audit._write_audit_row", mock_write):
            with patch("middleware.audit.asyncio.ensure_future", side_effect=_noop_future):
                asyncio.run(middleware.dispatch(self._req("/api/search", "GET"), AsyncMock(return_value=self._resp())))

        mock_write.assert_called_once()
        _, kwargs = mock_write.call_args
        assert kwargs.get("body_hash") is None
