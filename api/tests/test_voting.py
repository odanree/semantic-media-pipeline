"""
Tests for the voting endpoints in routers/search.py:
  POST /api/vote          — set/clear a vote on a file_path (+ optional segment)
  POST /api/vote/bulk     — cascade a vote to many files with lineage
  GET  /api/votes/stats   — aggregate vote stats
  GET  /api/votes/batch/{batch_id} — per-batch lineage

Qdrant + the fire-and-forget vote-event logger are mocked, so these run with
no live services (same pattern as the other search tests).
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _point(pid="p1", payload=None):
    p = MagicMock()
    p.id = pid
    p.payload = payload if payload is not None else {}
    return p


@pytest.fixture(autouse=True)
def _isolate_side_effects():
    """Stub the async vote-event logger and the Celery cascade dispatch so the
    endpoints run without a live Redis broker or DB."""
    with patch("routers.search._log_vote_event", new=AsyncMock(return_value=None)), \
         patch("routers.search.celery_app") as celery:
        yield celery


# ── POST /api/vote ──────────────────────────────────────────────────────────

def test_vote_upvote_sets_payload(client, mock_qdrant):
    mock_qdrant.scroll.return_value = ([_point("a", {"vote_label": {}})], None)
    res = client.post("/api/vote", json={"file_path": "/m/v.mp4", "vote": 1, "search_query": "cat"})
    assert res.status_code == 200
    body = res.json()
    assert body["vote"] == 1 and body["patched"] == 1
    # user_vote + vote_label written
    payload = mock_qdrant.set_payload.call_args.kwargs["payload"]
    assert payload["user_vote"] == 1
    assert payload["vote_label"]["cat"] == 1.0


def test_vote_clear_deletes_user_vote(client, mock_qdrant):
    mock_qdrant.scroll.return_value = ([_point("a")], None)
    res = client.post("/api/vote", json={"file_path": "/m/v.mp4", "vote": 0})
    assert res.status_code == 200
    assert mock_qdrant.delete_payload.called
    assert mock_qdrant.delete_payload.call_args.kwargs["keys"] == ["user_vote"]


def test_vote_404_when_scene_missing(client, mock_qdrant):
    mock_qdrant.scroll.return_value = ([], None)
    res = client.post("/api/vote", json={"file_path": "/m/missing.mp4", "vote": 1})
    assert res.status_code == 404


def test_vote_internal_query_not_recorded_as_label(client, mock_qdrant):
    # search_query starting with ">" is an internal frame-lookup, not a label
    mock_qdrant.scroll.return_value = ([_point("a", {})], None)
    res = client.post("/api/vote", json={"file_path": "/m/v.mp4", "vote": 1, "search_query": ">frame"})
    assert res.status_code == 200
    payload = mock_qdrant.set_payload.call_args.kwargs["payload"]
    assert "vote_label" not in payload


# ── POST /api/vote/bulk ─────────────────────────────────────────────────────

def test_bulk_vote_length_mismatch_400(client, mock_qdrant):
    res = client.post("/api/vote/bulk", json={
        "file_paths": ["/m/a.mp4", "/m/b.mp4"],
        "audio_segment_indices": [0],
        "vote": 1, "search_query": "cat", "batch_id": "b1",
    })
    assert res.status_code == 400


def test_bulk_vote_patches_each_file(client, mock_qdrant):
    mock_qdrant.scroll.return_value = ([_point("a"), _point("b")], None)
    res = client.post("/api/vote/bulk", json={
        "file_paths": ["/m/a.mp4", "/m/b.mp4"],
        "audio_segment_indices": [0, 1],
        "vote": 1, "search_query": "cat", "batch_id": "seed",
    })
    assert res.status_code == 200
    body = res.json()
    assert body["batch_id"] == "seed"
    assert body["total_patched"] == 4  # 2 points × 2 files
    assert body["breakdown"]["/m/a.mp4"] == 2


def test_upvote_dispatches_cascade(client, mock_qdrant, _isolate_side_effects):
    """A manual upvote (vote=1, no batch_id) fires the cascade_votes Celery task."""
    mock_qdrant.scroll.return_value = ([_point("a")], None)
    res = client.post("/api/vote", json={"file_path": "/m/v.mp4", "vote": 1, "search_query": "cat"})
    assert res.status_code == 200
    assert _isolate_side_effects.send_task.called
    assert _isolate_side_effects.send_task.call_args.args[0] == "tasks.cascade_votes"


# NOTE: GET /api/votes/stats and /api/votes/batch/{id} use a real async DB engine
# (get_async_engine → asyncpg) and are covered by integration tests, not unit tests.
