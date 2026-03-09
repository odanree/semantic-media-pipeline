"""
Tests for /api/stats/summary and /api/stats/collection endpoints.

Coverage
--------
- Response schema: required top-level keys exist
- Status breakdown: by_status dict populated from DB rows
- Qdrant integration: vector_count reflected in response
- Collection endpoint: total / indexed / topic_tags keys present
- Empty DB: zeroed counters, no crash
- Topic tags: non-empty list returned (fallback vocabulary when DB is empty)
"""

from unittest.mock import MagicMock, patch
import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _status_rows(*pairs):
    """Return mock fetchall rows for status count queries, e.g. [('done',850),...]"""
    return [MagicMock(__getitem__=lambda s, i: pairs[s._idx][i]) for s in []]  # unused


# ---------------------------------------------------------------------------
# /api/stats/summary
# ---------------------------------------------------------------------------

def test_stats_summary_returns_200(client):
    resp = client.get("/api/stats/summary")
    assert resp.status_code == 200


def test_stats_summary_has_required_keys(client):
    data = client.get("/api/stats/summary").json()
    for key in ("generated_at", "total_files", "by_status", "by_type", "qdrant"):
        assert key in data, f"missing key: {key}"


def test_stats_summary_qdrant_block_structure(client):
    data = client.get("/api/stats/summary").json()
    qdrant = data["qdrant"]
    assert "status" in qdrant
    assert "vector_count" in qdrant
    assert "db_done_count" in qdrant


def test_stats_summary_qdrant_vector_count(client, mock_qdrant):
    """Qdrant mock returns points_count=942 — should appear in response."""
    mock_qdrant.get_collection.return_value = MagicMock(points_count=942, vectors_count=942)
    data = client.get("/api/stats/summary").json()
    assert data["qdrant"]["vector_count"] == 942


def test_stats_summary_empty_db_no_crash(client, mock_db_session):
    """Empty DB returns zeroed counters — must not raise."""
    mock_db_session.execute.return_value.fetchall.return_value = []
    mock_db_session.execute.return_value.fetchone.return_value = (0,)
    resp = client.get("/api/stats/summary")
    assert resp.status_code == 200
    assert resp.json()["total_files"] == 0


def test_stats_summary_by_status_populated(client, mock_db_session):
    """
    Simulate DB returning status rows; by_status should reflect them.

    Uses side_effect to return different data on each execute() call
    (first call = status counts, rest = type/error/stuck queries).
    """
    # Row objects that behave like (status, count) tuples
    done_row  = ("done",  800)
    error_row = ("error",  10)
    pending_row = ("pending", 50)

    call_count = 0

    def _execute_side_effect(query, *args, **kwargs):
        nonlocal call_count
        call_count += 1
        result = MagicMock()
        if call_count == 1:
            # First execute: status counts
            result.fetchall.return_value = [done_row, error_row, pending_row]
        else:
            result.fetchall.return_value = []
            result.fetchone.return_value = (0,)
        return result

    mock_db_session.execute.side_effect = _execute_side_effect

    try:
        data = client.get("/api/stats/summary").json()
        by_status = data["by_status"]
        # At minimum "done" should be present with expected count
        assert by_status.get("done") == 800
        assert by_status.get("error") == 10
    finally:
        # Restore default behaviour for other tests
        mock_db_session.execute.side_effect = None
        mock_db_session.execute.return_value.fetchall.return_value = []
        mock_db_session.execute.return_value.fetchone.return_value = (0, None, None, None)


def test_stats_summary_has_top_errors(client):
    data = client.get("/api/stats/summary").json()
    assert "top_errors" in data
    assert isinstance(data["top_errors"], list)


def test_stats_summary_has_stuck_processing(client):
    data = client.get("/api/stats/summary").json()
    assert "stuck_processing" in data


# ---------------------------------------------------------------------------
# /api/stats/collection
# ---------------------------------------------------------------------------

def test_stats_collection_returns_200(client):
    resp = client.get("/api/stats/collection")
    assert resp.status_code == 200


def test_stats_collection_has_required_keys(client):
    data = client.get("/api/stats/collection").json()
    for key in ("total", "indexed", "percent_indexed", "by_type", "topic_tags"):
        assert key in data, f"missing key: {key}"


def test_stats_collection_empty_db_zeroed(client, mock_db_session):
    mock_db_session.execute.return_value.fetchall.return_value = []
    data = client.get("/api/stats/collection").json()
    assert data["total"] == 0
    assert data["indexed"] == 0
    assert data["percent_indexed"] == 0.0


def test_stats_collection_topic_tags_is_list(client):
    data = client.get("/api/stats/collection").json()
    assert isinstance(data["topic_tags"], list)


def test_stats_collection_topic_tags_non_empty(client):
    """
    With an empty DB, _compute_topic_tags falls back to the first k vocabulary
    entries — topic_tags must not be an empty list.
    """
    data = client.get("/api/stats/collection").json()
    assert len(data["topic_tags"]) > 0


def test_stats_collection_topic_tags_are_strings(client):
    tags = client.get("/api/stats/collection").json()["topic_tags"]
    assert all(isinstance(t, str) for t in tags)


def test_stats_collection_percent_indexed_range(client, mock_db_session):
    """percent_indexed must be in [0, 100]."""
    # Simulate 2 done, 1 pending
    rows = [("video", "done", 2), ("video", "pending", 1)]
    mock_db_session.execute.return_value.fetchall.return_value = rows

    try:
        data = client.get("/api/stats/collection").json()
        pct = data["percent_indexed"]
        assert 0.0 <= pct <= 100.0
    finally:
        mock_db_session.execute.return_value.fetchall.return_value = []
