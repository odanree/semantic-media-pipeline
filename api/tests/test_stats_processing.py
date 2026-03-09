"""
Tests for /api/stats/processing endpoint and _compute_topic_tags internals.

/stats/processing makes 4 sequential DB execute() calls:
  1. aggregate timing stats     → fetchone()  [6-column tuple]
  2. slowest individual files   → fetchall()  [4-column rows]
  3. hourly throughput          → fetchall()  [4-column rows]
  4. session-detection timestamps → fetchall()  [1-column rows]

The per-test helpers set execute().side_effect to feed each call the right
data, then restore to the default empty sentinel in a finally block so the
session-scoped fixture is clean for subsequent tests.

_compute_topic_tags coverage:
  * Empty id_rows path — already hit by collection tests (fallback to vocab)
  * non-empty retrieved path — tested here by giving mock_qdrant.retrieve()
    real MagicMock points with .vector attributes, triggering the cosine-sim
    matrix branch (lines 477-495)
"""

import uuid
from datetime import datetime, timedelta
from unittest.mock import MagicMock, call, patch

import numpy as np
import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_agg(total=0, avg=None, median=None, p95=None, mn=None, mx=None):
    """Build a mock aggregate row (fetchone result from timing query)."""
    return (total, avg, median, p95, mn, mx)


def _make_slow_row(path="pexels-demo/clip.mp4", ftype="video", dur=1.5, ts=None):
    ts = ts or datetime(2026, 3, 9, 10, 0, 0)
    row = MagicMock()
    row.__getitem__ = lambda s, i: [path, ftype, dur, ts][i]
    row[0] = path; row[1] = ftype; row[2] = dur; row[3] = ts
    return (path, ftype, dur, ts)


def _make_hourly_row(hour=None, files=5, videos=3, images=2):
    hour = hour or datetime(2026, 3, 9, 10, 0, 0)
    return (hour, files, videos, images)


def _make_ts_row(ts):
    return (ts,)


def _restore(mock_db_session):
    """Reset db mock to default empty state."""
    mock_db_session.execute.side_effect = None
    mock_db_session.execute.return_value.fetchall.return_value = []
    mock_db_session.execute.return_value.fetchone.return_value = (0, None, None, None, None, None)


def _multi_execute(mock_db_session, responses):
    """
    Set execute() to return different result objects per call.
    `responses` is a list of (fetchone_val, fetchall_val) pairs, one per call.
    """
    results = []
    for fetchone_val, fetchall_val in responses:
        r = MagicMock()
        r.fetchone.return_value = fetchone_val
        r.fetchall.return_value = fetchall_val
        results.append(r)

    it = iter(results)

    def _side_effect(query, *args, **kwargs):
        return next(it)

    mock_db_session.execute.side_effect = _side_effect


# ---------------------------------------------------------------------------
# /api/stats/processing — basic structure
# ---------------------------------------------------------------------------

def test_stats_processing_returns_200(client, mock_db_session):
    _multi_execute(mock_db_session, [
        (_make_agg(), []),   # agg timing
        (None,       []),    # slowest
        (None,       []),    # hourly
        (None,       []),    # ts for sessions
    ])
    try:
        resp = client.get("/api/stats/processing")
        assert resp.status_code == 200
    finally:
        _restore(mock_db_session)


def test_stats_processing_has_required_keys(client, mock_db_session):
    _multi_execute(mock_db_session, [
        (_make_agg(), []),
        (None,        []),
        (None,        []),
        (None,        []),
    ])
    try:
        data = client.get("/api/stats/processing").json()
        for key in ("generated_at", "lookback_hours", "timing",
                    "slowest_files", "hourly_throughput", "indexing_sessions"):
            assert key in data, f"missing key: {key}"
    finally:
        _restore(mock_db_session)


def test_stats_processing_timing_keys(client, mock_db_session):
    _multi_execute(mock_db_session, [
        (_make_agg(total=100, avg=2.5, median=2.0, p95=5.0, mn=0.5, mx=10.0), []),
        (None, []),
        (None, []),
        (None, []),
    ])
    try:
        timing = client.get("/api/stats/processing").json()["timing"]
        for key in ("total_completed", "avg_secs", "median_secs", "p95_secs",
                    "min_secs", "max_secs"):
            assert key in timing, f"missing timing key: {key}"
    finally:
        _restore(mock_db_session)


def test_stats_processing_timing_values(client, mock_db_session):
    # The SQL query does /1000.0 for all _secs columns, so the DB row
    # returned to Python is already in seconds — mock at that level.
    _multi_execute(mock_db_session, [
        (_make_agg(total=50, avg=3.0, median=2.5, p95=6.0, mn=0.5, mx=10.0), []),
        (None, []),
        (None, []),
        (None, []),
    ])
    try:
        timing = client.get("/api/stats/processing").json()["timing"]
        assert timing["total_completed"] == 50
        assert timing["avg_secs"] == 3.0
    finally:
        _restore(mock_db_session)


def test_stats_processing_empty_db_timing_nulls(client, mock_db_session):
    """When no rows match, all timing values should be None."""
    _multi_execute(mock_db_session, [
        (_make_agg(total=0), []),
        (None, []),
        (None, []),
        (None, []),
    ])
    try:
        timing = client.get("/api/stats/processing").json()["timing"]
        assert timing["total_completed"] == 0
        assert timing["avg_secs"] is None
    finally:
        _restore(mock_db_session)


def test_stats_processing_default_lookback_hours(client, mock_db_session):
    _multi_execute(mock_db_session, [
        (_make_agg(), []),
        (None, []),
        (None, []),
        (None, []),
    ])
    try:
        data = client.get("/api/stats/processing").json()
        assert data["lookback_hours"] == 720
    finally:
        _restore(mock_db_session)


def test_stats_processing_custom_hours(client, mock_db_session):
    _multi_execute(mock_db_session, [
        (_make_agg(), []),
        (None, []),
        (None, []),
        (None, []),
    ])
    try:
        data = client.get("/api/stats/processing?hours=48").json()
        assert data["lookback_hours"] == 48
    finally:
        _restore(mock_db_session)


def test_stats_processing_hours_too_low_returns_422(client):
    """hours < 1 violates Query(ge=1) → 422."""
    resp = client.get("/api/stats/processing?hours=0")
    assert resp.status_code == 422


def test_stats_processing_hours_too_high_returns_422(client):
    """hours > 8760 violates Query(le=8760) → 422."""
    resp = client.get("/api/stats/processing?hours=9000")
    assert resp.status_code == 422


def test_stats_processing_slowest_files_listed(client, mock_db_session):
    ts = datetime(2026, 3, 9, 10, 0, 0)
    slow_rows = [
        ("pexels-demo/a.mp4", "video", 8.5, ts),
        ("pexels-demo/b.jpg", "image", 4.2, ts),
    ]
    _multi_execute(mock_db_session, [
        (_make_agg(total=2), []),
        (None, slow_rows),
        (None, []),
        (None, []),
    ])
    try:
        data = client.get("/api/stats/processing").json()
        assert len(data["slowest_files"]) == 2
        assert data["slowest_files"][0]["file_path"] == "pexels-demo/a.mp4"
        assert data["slowest_files"][0]["duration_secs"] == 8.5
    finally:
        _restore(mock_db_session)


def test_stats_processing_slowest_files_schema(client, mock_db_session):
    ts = datetime(2026, 3, 9, 10, 0, 0)
    _multi_execute(mock_db_session, [
        (_make_agg(total=1), []),
        (None, [("pexels-demo/x.mp4", "video", 5.0, ts)]),
        (None, []),
        (None, []),
    ])
    try:
        result = client.get("/api/stats/processing").json()["slowest_files"][0]
        for key in ("file_path", "file_type", "duration_secs", "completed_at"):
            assert key in result, f"missing key: {key}"
    finally:
        _restore(mock_db_session)


def test_stats_processing_hourly_throughput_listed(client, mock_db_session):
    hour = datetime(2026, 3, 9, 10, 0, 0)
    _multi_execute(mock_db_session, [
        (_make_agg(), []),
        (None, []),
        (None, [(hour, 12, 8, 4)]),
        (None, []),
    ])
    try:
        throughput = client.get("/api/stats/processing").json()["hourly_throughput"]
        assert len(throughput) == 1
        assert throughput[0]["files_completed"] == 12
        assert throughput[0]["videos"] == 8
        assert throughput[0]["images"] == 4
    finally:
        _restore(mock_db_session)


def test_stats_processing_indexing_sessions_block_present(client, mock_db_session):
    _multi_execute(mock_db_session, [
        (_make_agg(), []),
        (None, []),
        (None, []),
        (None, []),
    ])
    try:
        sessions = client.get("/api/stats/processing").json()["indexing_sessions"]
        assert "total_sessions" in sessions
        assert "sessions" in sessions
    finally:
        _restore(mock_db_session)


def test_stats_processing_no_sessions_when_empty(client, mock_db_session):
    _multi_execute(mock_db_session, [
        (_make_agg(), []),
        (None, []),
        (None, []),
        (None, []),
    ])
    try:
        sessions = client.get("/api/stats/processing").json()["indexing_sessions"]
        assert sessions["total_sessions"] == 0
    finally:
        _restore(mock_db_session)


def test_stats_processing_session_detection_single(client, mock_db_session):
    """5 timestamps within 10 minutes → 1 session."""
    base = datetime(2026, 3, 9, 10, 0, 0)
    ts_rows = [(base + timedelta(minutes=i),) for i in range(5)]
    _multi_execute(mock_db_session, [
        (_make_agg(), []),
        (None, []),
        (None, []),
        (None, ts_rows),
    ])
    try:
        sessions = client.get("/api/stats/processing").json()["indexing_sessions"]
        assert sessions["total_sessions"] == 1
        assert sessions["sessions"][0]["files_processed"] == 5
    finally:
        _restore(mock_db_session)


def test_stats_processing_session_detection_two_sessions(client, mock_db_session):
    """Gap of 15 minutes between two groups → 2 sessions detected."""
    base = datetime(2026, 3, 9, 10, 0, 0)
    first  = [(base + timedelta(minutes=i),) for i in range(3)]
    second = [(base + timedelta(minutes=20 + i),) for i in range(2)]
    _multi_execute(mock_db_session, [
        (_make_agg(), []),
        (None, []),
        (None, []),
        (None, first + second),
    ])
    try:
        sessions = client.get("/api/stats/processing").json()["indexing_sessions"]
        assert sessions["total_sessions"] == 2
    finally:
        _restore(mock_db_session)


def test_stats_processing_session_schema(client, mock_db_session):
    base = datetime(2026, 3, 9, 10, 0, 0)
    ts_rows = [(base + timedelta(seconds=i * 30),) for i in range(3)]
    _multi_execute(mock_db_session, [
        (_make_agg(), []),
        (None, []),
        (None, []),
        (None, ts_rows),
    ])
    try:
        session = client.get("/api/stats/processing").json()["indexing_sessions"]["sessions"][0]
        for key in ("started_at", "ended_at", "files_processed", "duration_mins"):
            assert key in session, f"missing session key: {key}"
    finally:
        _restore(mock_db_session)


def test_stats_processing_custom_limit(client, mock_db_session):
    """limit param is accepted without error (val forwarded to query)."""
    _multi_execute(mock_db_session, [
        (_make_agg(), []),
        (None, []),
        (None, []),
        (None, []),
    ])
    try:
        resp = client.get("/api/stats/processing?limit=5")
        assert resp.status_code == 200
    finally:
        _restore(mock_db_session)


def test_stats_processing_limit_too_high_returns_422(client):
    resp = client.get("/api/stats/processing?limit=201")
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# _compute_topic_tags — cosine-similarity path (non-empty retrieved vectors)
# ---------------------------------------------------------------------------

def test_topic_tags_with_real_vectors(client, mock_db_session, mock_qdrant, mock_clip):
    """
    Provide real UUID rows from DB + matching mock Qdrant points with
    realistic numpy vectors to exercise the full cosine-similarity branch
    (lines 477-495): sample matrix build → L2-normalize → matmul → argmax.
    """
    import routers.stats as stats_mod

    # Reset vocab cache so CLIP encode is called fresh
    stats_mod._topic_vecs_cache = None

    # DB returns 5 fake UUIDs
    fake_ids = [str(uuid.uuid4()) for _ in range(5)]
    id_rows = [(uid,) for uid in fake_ids]

    # Mock Qdrant points with 768-dim vectors
    rng = np.random.default_rng(42)
    mock_points = []
    for _ in range(5):
        p = MagicMock()
        p.vector = rng.random(768).astype(np.float32).tolist()
        mock_points.append(p)

    mock_qdrant.retrieve.return_value = mock_points

    # collection endpoint calls _get_session for its own query first,
    # then _compute_topic_tags calls _get_session for the qdrant_point_id query.
    # Patch _get_session directly for the topic tags call.
    db_for_ids = MagicMock()
    db_for_ids.execute.return_value.fetchall.return_value = id_rows

    with patch("routers.stats._get_session", side_effect=[mock_db_session, db_for_ids]):
        # Reset collection's own execute to return empty rows
        mock_db_session.execute.side_effect = None
        mock_db_session.execute.return_value.fetchall.return_value = []
        resp = client.get("/api/stats/collection")

    tags = resp.json()["topic_tags"]
    assert isinstance(tags, list)
    assert len(tags) > 0
    # All returned tags must come from the vocabulary
    from routers.stats import _TOPIC_VOCABULARY
    for tag in tags:
        assert tag in _TOPIC_VOCABULARY

    # Restore
    mock_qdrant.retrieve.return_value = []
    _restore(mock_db_session)


def test_topic_tags_qdrant_empty_falls_back_to_vocab(mock_db_session, mock_qdrant):
    """retrieve() returns [] → fallback to first k vocabulary entries."""
    import routers.stats as stats_mod
    stats_mod._topic_vecs_cache = None

    fake_ids = [str(uuid.uuid4()) for _ in range(3)]
    id_rows = [(uid,) for uid in fake_ids]

    db_for_ids = MagicMock()
    db_for_ids.execute.return_value.fetchall.return_value = id_rows
    mock_qdrant.retrieve.return_value = []

    with patch("routers.stats._get_session", return_value=db_for_ids):
        result = stats_mod._compute_topic_tags(k=5)

    assert result == stats_mod._TOPIC_VOCABULARY[:5]
    # Restore
    mock_qdrant.retrieve.return_value = []
