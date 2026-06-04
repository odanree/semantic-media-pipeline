"""
Tests for the held-out eval-set router (api/routers/eval_set.py):
  POST   /api/eval-set         — add (upsert) entry
  GET    /api/eval-set         — list entries (with optional ?query=)
  DELETE /api/eval-set/{id}    — remove entry

The DB session is mocked at the module level so these run without Postgres.
"""
import uuid
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def mock_eval_session():
    session = MagicMock(name="eval_session")
    with patch("routers.eval_set._get_session", return_value=session):
        yield session


# ── POST /api/eval-set ──────────────────────────────────────────────────────

def test_post_eval_set_returns_201(client, mock_eval_session):
    eid = uuid.uuid4()
    ts = datetime(2026, 6, 4, 12, 0, tzinfo=timezone.utc)
    mock_eval_session.execute.return_value.fetchone.return_value = (
        eid, "cat", "/m/a.mp4", None, 1, None, "test pin", ts,
    )
    res = client.post("/api/eval-set", json={
        "search_query": "cat",
        "file_path": "/m/a.mp4",
        "label": 1,
        "note": "test pin",
    })
    assert res.status_code == 201
    body = res.json()
    assert body["id"] == str(eid)
    assert body["search_query"] == "cat"
    assert body["label"] == 1
    assert body["note"] == "test pin"


def test_post_eval_set_rejects_invalid_label(client, mock_eval_session):
    res = client.post("/api/eval-set", json={
        "search_query": "cat",
        "file_path": "/m/a.mp4",
        "label": 0,  # only ±1 allowed
    })
    assert res.status_code == 400


def test_post_eval_set_upsert_keeps_uniqueness_per_segment(client, mock_eval_session):
    """Re-pinning the same (query, file_path, segment) tuple should not error —
    the router relies on ON CONFLICT to update in place. We assert the SQL
    actually contains ON CONFLICT so the contract isn't silently lost."""
    eid = uuid.uuid4()
    ts = datetime(2026, 6, 4, 12, 0, tzinfo=timezone.utc)
    mock_eval_session.execute.return_value.fetchone.return_value = (
        eid, "cat", "/m/a.mp4", 3, -1, None, None, ts,
    )
    res = client.post("/api/eval-set", json={
        "search_query": "cat",
        "file_path": "/m/a.mp4",
        "audio_segment_index": 3,
        "label": -1,
    })
    assert res.status_code == 201
    sql = str(mock_eval_session.execute.call_args.args[0])
    assert "ON CONFLICT" in sql


# ── GET /api/eval-set ───────────────────────────────────────────────────────

def test_list_eval_set_returns_entries(client, mock_eval_session):
    ts = datetime(2026, 6, 4, 12, 0, tzinfo=timezone.utc)
    rows = [
        (uuid.uuid4(), "cat", "/m/a.mp4",  None, 1,  None, None, ts),
        (uuid.uuid4(), "dog", "/m/b.mp4",  2,    -1, None, "iffy", ts),
    ]
    # First execute() call is the SELECT, second is the COUNT.
    mock_eval_session.execute.side_effect = [
        MagicMock(fetchall=lambda: rows),
        MagicMock(scalar=lambda: 2),
    ]
    res = client.get("/api/eval-set")
    assert res.status_code == 200
    body = res.json()
    assert body["total"] == 2
    assert len(body["entries"]) == 2
    assert body["entries"][1]["note"] == "iffy"


def test_list_eval_set_filters_by_query(client, mock_eval_session):
    ts = datetime(2026, 6, 4, 12, 0, tzinfo=timezone.utc)
    mock_eval_session.execute.side_effect = [
        MagicMock(fetchall=lambda: [(uuid.uuid4(), "cat", "/m/a.mp4", None, 1, None, None, ts)]),
        MagicMock(scalar=lambda: 1),
    ]
    res = client.get("/api/eval-set?query=cat")
    assert res.status_code == 200
    # The bound parameter must be 'cat' for both calls
    assert mock_eval_session.execute.call_args_list[0].args[1]["q"] == "cat"


# ── DELETE /api/eval-set/{id} ───────────────────────────────────────────────

def test_delete_eval_set_404_when_missing(client, mock_eval_session):
    mock_eval_session.execute.return_value.rowcount = 0
    res = client.delete(f"/api/eval-set/{uuid.uuid4()}")
    assert res.status_code == 404


def test_delete_eval_set_returns_204_on_success(client, mock_eval_session):
    mock_eval_session.execute.return_value.rowcount = 1
    res = client.delete(f"/api/eval-set/{uuid.uuid4()}")
    assert res.status_code == 204


def test_delete_eval_set_rejects_non_uuid(client, mock_eval_session):
    res = client.delete("/api/eval-set/not-a-uuid")
    assert res.status_code == 400
