"""
Tests for POST /api/search-vector and the CLIP-503 branch in POST /api/search.

Coverage targets
----------------
search.py:
  - search_by_vector() — happy path, empty-vector error, schema, forwarded params
  - POST /api/search CLIP 503 branch — get_clip_model() raises → 503

Notes
-----
  - `vector: List[float]` in the endpoint signature is treated by FastAPI as a
    *body* parameter on POST endpoints (raw JSON array).  Tests POST a plain
    `json=[...]` list; `limit` and `threshold` remain query params alongside it.
  - The session-level patch on `routers.search.get_clip_model` (from conftest)
    is overridden per-test using a nested `with patch(...)` context for the 503
    test path.
"""

import pytest
from unittest.mock import MagicMock, patch


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_VECTOR = [0.1, 0.2, 0.3, 0.4, 0.5]   # minimal 5-d vector (qdrant is mocked)


def _make_hit(file_path="media/clip.mp4", file_type="video", score=0.91):
    """Build a minimal Qdrant ScoredPoint mock."""
    h = MagicMock()
    h.id = "abc-123"
    h.score = score
    h.payload = {
        "file_path": file_path,
        "file_type": file_type,
        "frame_index": 42,
        "timestamp": 1.5,
    }
    return h


# ---------------------------------------------------------------------------
# POST /api/search-vector — happy path
# ---------------------------------------------------------------------------

def test_search_vector_returns_200(client, mock_qdrant):
    mock_qdrant.query_points.return_value = MagicMock(points=[])
    resp = client.post("/api/search-vector", json=_VECTOR)
    assert resp.status_code == 200


def test_search_vector_response_schema(client, mock_qdrant):
    mock_qdrant.query_points.return_value = MagicMock(points=[])
    data = client.post("/api/search-vector", json=_VECTOR).json()
    assert "results" in data
    assert "count" in data
    assert "execution_time_ms" in data
    assert "vector_dimension" in data


def test_search_vector_dimension_reflects_input(client, mock_qdrant):
    mock_qdrant.query_points.return_value = MagicMock(points=[])
    data = client.post("/api/search-vector", json=_VECTOR).json()
    assert data["vector_dimension"] == len(_VECTOR)


def test_search_vector_execution_time_is_numeric(client, mock_qdrant):
    mock_qdrant.query_points.return_value = MagicMock(points=[])
    data = client.post("/api/search-vector", json=_VECTOR).json()
    assert isinstance(data["execution_time_ms"], (int, float))
    assert data["execution_time_ms"] >= 0


# ---------------------------------------------------------------------------
# POST /api/search-vector — results presence and schema
# ---------------------------------------------------------------------------

def test_search_vector_returns_hits(client, mock_qdrant):
    mock_qdrant.query_points.return_value = MagicMock(points=[_make_hit(), _make_hit()])
    data = client.post("/api/search-vector", json=_VECTOR).json()
    assert data["count"] == 2
    assert len(data["results"]) == 2


def test_search_vector_no_results(client, mock_qdrant):
    mock_qdrant.query_points.return_value = MagicMock(points=[])
    data = client.post("/api/search-vector", json=_VECTOR).json()
    assert data["count"] == 0
    assert data["results"] == []


def test_search_vector_result_has_required_fields(client, mock_qdrant):
    mock_qdrant.query_points.return_value = MagicMock(points=[_make_hit()])
    results = client.post("/api/search-vector", json=_VECTOR).json()["results"]
    r = results[0]
    assert "file_path" in r
    assert "file_type" in r
    assert "similarity" in r


def test_search_vector_result_similarity_value(client, mock_qdrant):
    mock_qdrant.query_points.return_value = MagicMock(points=[_make_hit(score=0.88)])
    r = client.post("/api/search-vector", json=_VECTOR).json()["results"][0]
    assert abs(r["similarity"] - 0.88) < 0.001


# ---------------------------------------------------------------------------
# POST /api/search-vector — parameter forwarding
# ---------------------------------------------------------------------------

def test_search_vector_limit_forwarded(client, mock_qdrant):
    mock_qdrant.query_points.return_value = MagicMock(points=[])
    client.post("/api/search-vector", json=_VECTOR, params={"limit": 5})
    call_kwargs = mock_qdrant.query_points.call_args
    assert call_kwargs.kwargs.get("limit") == 5 or (
        call_kwargs.args and 5 in call_kwargs.args
    )


def test_search_vector_threshold_forwarded(client, mock_qdrant):
    mock_qdrant.query_points.return_value = MagicMock(points=[])
    client.post("/api/search-vector", json=_VECTOR, params={"threshold": 0.75})
    call_kwargs = mock_qdrant.query_points.call_args
    assert call_kwargs.kwargs.get("score_threshold") == pytest.approx(0.75, abs=0.001) or (
        call_kwargs.args and any(abs(a - 0.75) < 0.001 for a in call_kwargs.args if isinstance(a, float))
    )


def test_search_vector_default_limit_is_20(client, mock_qdrant):
    mock_qdrant.query_points.return_value = MagicMock(points=[])
    client.post("/api/search-vector", json=_VECTOR)
    call_kwargs = mock_qdrant.query_points.call_args
    assert call_kwargs.kwargs.get("limit") == 20


# ---------------------------------------------------------------------------
# POST /api/search-vector — error paths
# ---------------------------------------------------------------------------

def test_search_vector_empty_vector_returns_500(client):
    """Empty JSON list → ValueError('Vector cannot be empty') inside endpoint → 500."""
    resp = client.post("/api/search-vector", json=[])
    assert resp.status_code == 500


# ---------------------------------------------------------------------------
# POST /api/search — CLIP model load failure → 503
# ---------------------------------------------------------------------------

def test_search_clip_load_failure_returns_503(client):
    """Override the session-level get_clip_model patch to raise an exception."""
    with patch("routers.search.get_clip_model", side_effect=Exception("CLIP load failed")):
        resp = client.post("/api/search", json={"query": "sunset over the ocean"})
    assert resp.status_code == 503


def test_search_clip_503_detail_mentions_embedder(client):
    with patch("routers.search.get_clip_model", side_effect=RuntimeError("no GPU")):
        resp = client.post("/api/search", json={"query": "mountains"})
    detail = resp.json()["detail"].lower()
    assert "clip" in detail or "embedder" in detail or "failed" in detail
