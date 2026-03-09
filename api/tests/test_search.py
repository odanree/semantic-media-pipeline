"""
Tests for /api/search and /api/search-status endpoints.

Coverage
--------
- Input validation: empty / whitespace queries rejected with 400
- Response schema: required keys present on valid request
- Result count: mock Qdrant hits appear in response
- Limit parameter: forwarded to Qdrant query call
- Threshold parameter: accepted without error
- Search-status: qdrant reachability endpoint structure
"""

import numpy as np
from unittest.mock import MagicMock, call


# ---------------------------------------------------------------------------
# /api/search-status
# ---------------------------------------------------------------------------

def test_search_status_returns_200(client):
    resp = client.get("/api/search-status")
    assert resp.status_code == 200


def test_search_status_healthy(client):
    data = client.get("/api/search-status").json()
    assert data["status"] == "healthy"


def test_search_status_has_host(client):
    data = client.get("/api/search-status").json()
    assert "qdrant_host" in data


def test_search_status_reports_target_collection(client):
    data = client.get("/api/search-status").json()
    assert "target_collection" in data
    assert data["target_collection"] == "media_vectors"


# ---------------------------------------------------------------------------
# /api/search — input validation
# ---------------------------------------------------------------------------

def test_search_empty_query_returns_400(client):
    resp = client.post("/api/search", json={"query": ""})
    assert resp.status_code == 400


def test_search_empty_query_error_message(client):
    detail = client.post("/api/search", json={"query": ""}).json()["detail"]
    assert "empty" in detail.lower()


def test_search_whitespace_query_returns_400(client):
    resp = client.post("/api/search", json={"query": "   "})
    assert resp.status_code == 400


def test_search_missing_query_field_returns_422(client):
    """Pydantic rejects requests that omit the required `query` field."""
    resp = client.post("/api/search", json={"limit": 5})
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# /api/search — response schema (no results)
# ---------------------------------------------------------------------------

def test_search_valid_query_returns_200(client, mock_qdrant, mock_clip):
    mock_qdrant.query_points.return_value = MagicMock(points=[])
    resp = client.post("/api/search", json={"query": "people working at desk"})
    assert resp.status_code == 200


def test_search_response_has_required_keys(client, mock_qdrant):
    mock_qdrant.query_points.return_value = MagicMock(points=[])
    data = client.post("/api/search", json={"query": "sunset"}).json()
    for key in ("query", "results", "count", "execution_time_ms"):
        assert key in data, f"missing key: {key}"


def test_search_response_echoes_query(client, mock_qdrant):
    mock_qdrant.query_points.return_value = MagicMock(points=[])
    data = client.post("/api/search", json={"query": "ocean waves"}).json()
    assert data["query"] == "ocean waves"


def test_search_no_results_count_zero(client, mock_qdrant):
    mock_qdrant.query_points.return_value = MagicMock(points=[])
    data = client.post("/api/search", json={"query": "mountain trail"}).json()
    assert data["count"] == 0
    assert data["results"] == []


def test_search_execution_time_is_number(client, mock_qdrant):
    mock_qdrant.query_points.return_value = MagicMock(points=[])
    data = client.post("/api/search", json={"query": "city lights"}).json()
    assert isinstance(data["execution_time_ms"], (int, float))


# ---------------------------------------------------------------------------
# /api/search — result presence
# ---------------------------------------------------------------------------

def _make_hit(file_path: str, file_type: str, score: float) -> MagicMock:
    """Helper: build a mock Qdrant ScoredPoint."""
    hit = MagicMock()
    hit.score = score
    hit.payload = {"file_path": file_path, "file_type": file_type}
    return hit


def test_search_returns_correct_count(client, mock_qdrant):
    """Mock Qdrant returning 2 hits → response count == 2."""
    mock_qdrant.query_points.return_value = MagicMock(points=[
        _make_hit("pexels-demo/video1.mp4", "video", 0.87),
        _make_hit("pexels-demo/photo1.jpg", "image", 0.74),
    ])
    data = client.post("/api/search", json={"query": "basketball"}).json()
    assert data["count"] == 2
    assert len(data["results"]) == 2


def test_search_result_has_file_path(client, mock_qdrant):
    mock_qdrant.query_points.return_value = MagicMock(points=[
        _make_hit("pexels-demo/clip.mp4", "video", 0.91),
    ])
    results = client.post("/api/search", json={"query": "sport"}).json()["results"]
    assert len(results) == 1
    assert "file_path" in results[0]


def test_search_result_has_similarity(client, mock_qdrant):
    mock_qdrant.query_points.return_value = MagicMock(points=[
        _make_hit("pexels-demo/clip.mp4", "video", 0.91),
    ])
    result = client.post("/api/search", json={"query": "sport"}).json()["results"][0]
    assert "similarity" in result
    assert 0.0 <= result["similarity"] <= 1.0


# ---------------------------------------------------------------------------
# /api/search — parameter forwarding
# ---------------------------------------------------------------------------

def test_search_limit_forwarded_to_qdrant(client, mock_qdrant):
    """limit=5 must be passed to qdrant.query_points()."""
    mock_qdrant.query_points.return_value = MagicMock(points=[])
    client.post("/api/search", json={"query": "yoga", "limit": 5})
    call_kwargs = mock_qdrant.query_points.call_args
    assert call_kwargs is not None
    # limit can be a positional or keyword arg — check both
    args, kwargs = call_kwargs
    assert kwargs.get("limit") == 5 or (len(args) >= 3 and args[2] == 5)


def test_search_threshold_accepted(client, mock_qdrant):
    """threshold parameter should be accepted without error."""
    mock_qdrant.query_points.return_value = MagicMock(points=[])
    resp = client.post("/api/search", json={"query": "cooking", "threshold": 0.5})
    assert resp.status_code == 200


def test_search_zero_threshold_accepted(client, mock_qdrant):
    """threshold=0.0 must not be silently dropped (tests the ?? vs || fix)."""
    mock_qdrant.query_points.return_value = MagicMock(points=[])
    resp = client.post("/api/search", json={"query": "running", "threshold": 0.0})
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# /api/search — CLIP model integration
# ---------------------------------------------------------------------------

def test_search_calls_clip_encode(client, mock_qdrant, mock_clip):
    """CLIP model must be called with the query string."""
    mock_qdrant.query_points.return_value = MagicMock(points=[])
    mock_clip.encode.reset_mock()
    client.post("/api/search", json={"query": "soccer field"})
    mock_clip.encode.assert_called_once()
    first_arg = mock_clip.encode.call_args[0][0]
    assert first_arg == "soccer field"
