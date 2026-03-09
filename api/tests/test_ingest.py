"""
Tests for /api/ingest, /api/task/{id}, and /api/status endpoints.

Coverage targets
----------------
- POST /api/ingest (S3 mode) → 200 + task_id in response
- POST /api/ingest (local, bad path) → 400
- POST /api/ingest response schema
- GET  /api/task/{id} → task status object
- GET  /api/task/{id} pending state
- GET  /api/task/{id} succeeded state
- GET  /api/status → operational status
"""

import os
from unittest.mock import MagicMock, patch


# ---------------------------------------------------------------------------
# /api/status (health.py /status endpoint)
# ---------------------------------------------------------------------------

def test_status_returns_200(client):
    resp = client.get("/api/status")
    assert resp.status_code == 200


def test_status_has_timestamp(client):
    data = client.get("/api/status").json()
    assert "timestamp" in data


def test_status_is_operational(client):
    data = client.get("/api/status").json()
    assert data.get("status") == "operational"


# ---------------------------------------------------------------------------
# POST /api/ingest
# ---------------------------------------------------------------------------

def _mock_task(task_id: str = "abc-123") -> MagicMock:
    t = MagicMock()
    t.id = task_id
    return t


def test_ingest_s3_mode_returns_200(client):
    """
    With STORAGE_BACKEND=s3 the endpoint skips the local dir-existence check
    and immediately enqueues the Celery task.
    """
    with (
        patch.dict(os.environ, {"STORAGE_BACKEND": "s3"}),
        patch("routers.ingest.celery_app.send_task", return_value=_mock_task()),
    ):
        resp = client.post("/api/ingest", json={"media_root": "pexels-demo/"})
    assert resp.status_code == 200


def test_ingest_response_status_accepted(client):
    with (
        patch.dict(os.environ, {"STORAGE_BACKEND": "s3"}),
        patch("routers.ingest.celery_app.send_task", return_value=_mock_task()),
    ):
        data = client.post("/api/ingest", json={"media_root": "pexels-demo/"}).json()
    assert data["status"] == "accepted"


def test_ingest_response_has_task_id(client):
    with (
        patch.dict(os.environ, {"STORAGE_BACKEND": "s3"}),
        patch("routers.ingest.celery_app.send_task", return_value=_mock_task("xyz-789")),
    ):
        data = client.post("/api/ingest", json={"media_root": "pexels-demo/"}).json()
    assert data["task_id"] == "xyz-789"


def test_ingest_response_schema(client):
    with (
        patch.dict(os.environ, {"STORAGE_BACKEND": "s3"}),
        patch("routers.ingest.celery_app.send_task", return_value=_mock_task()),
    ):
        data = client.post("/api/ingest", json={"media_root": "pexels-demo/"}).json()
    for key in ("status", "timestamp", "media_root", "task_id", "message"):
        assert key in data, f"missing key: {key}"


def test_ingest_local_invalid_dir_returns_400(client):
    """Local backend with non-existent path raises ValueError → 400."""
    with patch.dict(os.environ, {"STORAGE_BACKEND": "local"}):
        resp = client.post("/api/ingest", json={"media_root": "/no/such/path"})
    assert resp.status_code == 400


def test_ingest_missing_media_root_returns_422(client):
    resp = client.post("/api/ingest", json={})
    assert resp.status_code == 422


def test_ingest_celery_send_task_called(client):
    """Verify celery send_task is invoked with the correct task name."""
    with (
        patch.dict(os.environ, {"STORAGE_BACKEND": "s3"}),
        patch("routers.ingest.celery_app.send_task", return_value=_mock_task()) as mock_send,
    ):
        client.post("/api/ingest", json={"media_root": "pexels-demo/"})
    mock_send.assert_called_once()
    assert mock_send.call_args[0][0] == "tasks.crawl_and_dispatch"


# ---------------------------------------------------------------------------
# GET /api/task/{task_id}
# ---------------------------------------------------------------------------

def _async_result_mock(task_id: str, status: str = "PENDING", ready: bool = False,
                       successful: bool = False, result=None):
    m = MagicMock()
    m.status = status
    m.ready.return_value = ready
    m.successful.return_value = successful
    m.result = result
    return m


def test_task_status_returns_200(client):
    mock_result = _async_result_mock("abc-123", status="PENDING")
    with patch("celery.result.AsyncResult", return_value=mock_result):
        resp = client.get("/api/task/abc-123")
    assert resp.status_code == 200


def test_task_status_pending(client):
    mock_result = _async_result_mock("abc-123", status="PENDING")
    with patch("celery.result.AsyncResult", return_value=mock_result):
        data = client.get("/api/task/abc-123").json()
    assert data["task_id"] == "abc-123"
    assert data["status"] == "PENDING"


def test_task_status_success(client):
    mock_result = _async_result_mock(
        "done-456", status="SUCCESS", ready=True, successful=True,
        result={"files_queued": 100},
    )
    with patch("celery.result.AsyncResult", return_value=mock_result):
        data = client.get("/api/task/done-456").json()
    assert data["status"] == "SUCCESS"
    assert data.get("result") == {"files_queued": 100}


def test_task_status_has_timestamp(client):
    mock_result = _async_result_mock("t-001")
    with patch("celery.result.AsyncResult", return_value=mock_result):
        data = client.get("/api/task/t-001").json()
    assert "timestamp" in data
