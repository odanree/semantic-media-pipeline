"""
Tests for /api/admin/* endpoints.

Coverage targets
----------------
- POST /admin/backfill-captions → 200 + task_id
- POST /admin/backfill-captions (celery error) → 503
- POST /admin/backfill-moov-sidecars → 200
- POST /admin/backfill-moov-sidecars (celery error) → 503
- POST /admin/backfill-yolo → 200
- POST /admin/backfill-yolo (celery error) → 503
- GET  /admin/task/{task_id} → pending state
- GET  /admin/task/{task_id} → success state
- GET  /admin/task/{task_id} → failure state
"""

from unittest.mock import AsyncMock, MagicMock, patch


def _mock_task(task_id: str = "task-abc-123") -> MagicMock:
    t = MagicMock()
    t.id = task_id
    return t


# ---------------------------------------------------------------------------
# POST /api/admin/backfill-captions
# ---------------------------------------------------------------------------

def test_backfill_captions_returns_200(client):
    with patch("routers.admin._celery.send_task", return_value=_mock_task()):
        resp = client.post("/api/admin/backfill-captions", json={})
    assert resp.status_code == 200


def test_backfill_captions_returns_task_id(client):
    with patch("routers.admin._celery.send_task", return_value=_mock_task("cap-001")):
        data = client.post("/api/admin/backfill-captions", json={}).json()
    assert data["task_id"] == "cap-001"


def test_backfill_captions_dry_run_flagged(client):
    with patch("routers.admin._celery.send_task", return_value=_mock_task()) as mock_send:
        client.post("/api/admin/backfill-captions", json={"dry_run": True})
    # send_task("task_name", kwargs={...}, queue=...) — dry_run is in kwargs kwarg
    assert mock_send.call_args.kwargs["kwargs"]["dry_run"] is True


def test_backfill_captions_celery_error_returns_503(client):
    with patch("routers.admin._celery.send_task", side_effect=Exception("broker unreachable")):
        resp = client.post("/api/admin/backfill-captions", json={})
    assert resp.status_code == 503


# ---------------------------------------------------------------------------
# POST /api/admin/backfill-moov-sidecars
# ---------------------------------------------------------------------------

def test_backfill_moov_sidecars_returns_200(client):
    with patch("routers.admin._celery.send_task", return_value=_mock_task()):
        resp = client.post("/api/admin/backfill-moov-sidecars", json={})
    assert resp.status_code == 200


def test_backfill_moov_sidecars_has_task_id(client):
    with patch("routers.admin._celery.send_task", return_value=_mock_task("moov-001")):
        data = client.post("/api/admin/backfill-moov-sidecars", json={}).json()
    assert data["task_id"] == "moov-001"


def test_backfill_moov_sidecars_celery_error_returns_503(client):
    with patch("routers.admin._celery.send_task", side_effect=Exception("broker down")):
        resp = client.post("/api/admin/backfill-moov-sidecars", json={})
    assert resp.status_code == 503


# ---------------------------------------------------------------------------
# POST /api/admin/backfill-yolo
# ---------------------------------------------------------------------------

def test_backfill_yolo_returns_200(client):
    with patch("routers.admin._celery.send_task", return_value=_mock_task()):
        resp = client.post("/api/admin/backfill-yolo", json={})
    assert resp.status_code == 200


def test_backfill_yolo_has_task_id(client):
    with patch("routers.admin._celery.send_task", return_value=_mock_task("yolo-001")):
        data = client.post("/api/admin/backfill-yolo", json={}).json()
    assert data["task_id"] == "yolo-001"


def test_backfill_yolo_celery_error_returns_503(client):
    with patch("routers.admin._celery.send_task", side_effect=Exception("gpu queue down")):
        resp = client.post("/api/admin/backfill-yolo", json={})
    assert resp.status_code == 503


# ---------------------------------------------------------------------------
# GET /api/admin/task/{task_id}
# ---------------------------------------------------------------------------

def test_get_task_pending_state(client):
    mock_result = MagicMock()
    mock_result.state = "PENDING"
    mock_result.result = None
    with patch("routers.admin._celery.AsyncResult", return_value=mock_result):
        data = client.get("/api/admin/task/some-id").json()
    assert data["state"] == "PENDING"
    assert data["task_id"] == "some-id"
    assert data["result"] is None


def test_get_task_success_state(client):
    mock_result = MagicMock()
    mock_result.state = "SUCCESS"
    mock_result.result = {"indexed": 42}
    with patch("routers.admin._celery.AsyncResult", return_value=mock_result):
        data = client.get("/api/admin/task/done-id").json()
    assert data["state"] == "SUCCESS"
    assert data["result"] == {"indexed": 42}


def test_get_task_failure_state(client):
    mock_result = MagicMock()
    mock_result.state = "FAILURE"
    mock_result.result = RuntimeError("worker crashed")
    with patch("routers.admin._celery.AsyncResult", return_value=mock_result):
        data = client.get("/api/admin/task/fail-id").json()
    assert data["state"] == "FAILURE"
    assert "error" in data["result"]


# ---------------------------------------------------------------------------
# POST /api/agent/query — multi-agent coordinator endpoint
# ---------------------------------------------------------------------------

def test_agent_query_returns_200(client):
    """Coordinator succeeds → 200 with answer."""
    final_state = {
        "final_answer": "The answer is 42.",
        "intent": "search",
        "search_results": [1, 2],
        "metadata_results": [],
        "audio_results": [],
        "vision_results": [],
        "error": None,
    }
    with patch("routers.agent.coordinator") as mock_coord:
        mock_coord.ainvoke = AsyncMock(return_value=final_state)
        resp = client.post("/api/agent/query", json={"query": "what is in the footage"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["answer"] == "The answer is 42."
    assert data["search_result_count"] == 2


def test_agent_query_returns_500_on_coordinator_exception(client):
    """Coordinator raises → 500 with detail."""
    with patch("routers.agent.coordinator") as mock_coord:
        mock_coord.ainvoke = AsyncMock(side_effect=RuntimeError("graph exploded"))
        resp = client.post("/api/agent/query", json={"query": "crash test"})
    assert resp.status_code == 500
    assert "graph exploded" in resp.json()["detail"]


def test_agent_query_returns_500_on_state_error(client):
    """Coordinator returns error in state → 500."""
    final_state = {
        "final_answer": None,
        "error": "search agent timed out",
        "search_results": [],
        "metadata_results": [],
        "audio_results": [],
        "vision_results": [],
        "intent": None,
    }
    with patch("routers.agent.coordinator") as mock_coord:
        mock_coord.ainvoke = AsyncMock(return_value=final_state)
        resp = client.post("/api/agent/query", json={"query": "slow query"})
    assert resp.status_code == 500


def test_agent_query_missing_query_returns_422(client):
    resp = client.post("/api/agent/query", json={})
    assert resp.status_code == 422
