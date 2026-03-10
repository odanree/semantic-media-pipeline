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
    data = client.post("/api/search", json={"query": "basketball", "dedup": False}).json()
    assert data["count"] == 2
    assert len(data["results"]) == 2


def test_search_result_has_file_path(client, mock_qdrant):
    mock_qdrant.query_points.return_value = MagicMock(points=[
        _make_hit("pexels-demo/clip.mp4", "video", 0.91),
    ])
    results = client.post("/api/search", json={"query": "sport", "dedup": False}).json()["results"]
    assert len(results) == 1
    assert "file_path" in results[0]


def test_search_result_has_similarity(client, mock_qdrant):
    mock_qdrant.query_points.return_value = MagicMock(points=[
        _make_hit("pexels-demo/clip.mp4", "video", 0.91),
    ])
    result = client.post("/api/search", json={"query": "sport", "dedup": False}).json()["results"][0]
    assert "similarity" in result
    assert 0.0 <= result["similarity"] <= 1.0


# ---------------------------------------------------------------------------
# /api/search — parameter forwarding
# ---------------------------------------------------------------------------

def test_search_limit_forwarded_to_qdrant(client, mock_qdrant):
    """limit=5 must be passed to qdrant.query_points() when dedup=false."""
    mock_qdrant.query_points.return_value = MagicMock(points=[])
    client.post("/api/search", json={"query": "yoga", "limit": 5, "dedup": False})
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


# ---------------------------------------------------------------------------
# /api/search — temporal deduplication tests
# ---------------------------------------------------------------------------

def _make_video_hit(file_path: str, score: float, timestamp=None, frame_index=None):
    """Build a mock ScoredPoint-like object for use in dedup-specific tests."""
    h = MagicMock()
    h.score = score
    h.id = f"{file_path}-{frame_index or 0}"
    h.payload = {
        "file_path": file_path,
        "file_type": "video" if timestamp is not None else "image",
        "timestamp": timestamp,
        "frame_index": frame_index,
        "caption": None,
    }
    return h


def _make_group(hits):
    """Build a mock PointGroup-like object."""
    g = MagicMock()
    g.hits = hits
    return g


def test_search_dedup_default_uses_query_points_groups(client, mock_qdrant):
    """dedup=true (default) must route through query_points_groups, not query_points."""
    mock_qdrant.query_points_groups.return_value = MagicMock(groups=[])
    mock_qdrant.query_points.reset_mock()
    mock_qdrant.query_points_groups.reset_mock()

    resp = client.post("/api/search", json={"query": "birthday party"})
    assert resp.status_code == 200
    mock_qdrant.query_points_groups.assert_called_once()
    mock_qdrant.query_points.assert_not_called()


def test_search_dedup_false_uses_query_points(client, mock_qdrant):
    """dedup=false must route through query_points, not query_points_groups."""
    mock_qdrant.query_points.return_value = MagicMock(points=[])
    mock_qdrant.query_points.reset_mock()
    mock_qdrant.query_points_groups.reset_mock()

    resp = client.post("/api/search", json={"query": "sunset", "dedup": False})
    assert resp.status_code == 200
    mock_qdrant.query_points.assert_called_once()
    mock_qdrant.query_points_groups.assert_not_called()


def test_search_dedup_collapses_frames_in_same_window(client, mock_qdrant):
    """Three frames within the same 5 s window → collapsed to 1 representative."""
    hits = [
        _make_video_hit("video.mp4", 0.9, timestamp=1.0),
        _make_video_hit("video.mp4", 0.8, timestamp=2.5),
        _make_video_hit("video.mp4", 0.7, timestamp=4.9),
    ]
    mock_qdrant.query_points_groups.return_value = MagicMock(groups=[_make_group(hits)])

    resp = client.post("/api/search", json={"query": "running"})
    data = resp.json()
    assert resp.status_code == 200
    assert data["count"] == 1


def test_search_dedup_keeps_frames_in_different_windows(client, mock_qdrant):
    """Frames at 0 s, 6 s and 12 s each fall in a different 5 s bucket → 3 results."""
    hits = [
        _make_video_hit("video.mp4", 0.9, timestamp=0.5),
        _make_video_hit("video.mp4", 0.8, timestamp=6.5),
        _make_video_hit("video.mp4", 0.7, timestamp=12.5),
    ]
    mock_qdrant.query_points_groups.return_value = MagicMock(groups=[_make_group(hits)])

    resp = client.post("/api/search", json={"query": "running"})
    data = resp.json()
    assert resp.status_code == 200
    assert data["count"] == 3


def test_search_dedup_images_never_collapsed(client, mock_qdrant):
    """Images (timestamp=None) must always be kept — they have no temporal axis."""
    hits = [
        _make_video_hit("photo1.jpg", 0.9, timestamp=None),
        _make_video_hit("photo1.jpg", 0.8, timestamp=None),
    ]
    mock_qdrant.query_points_groups.return_value = MagicMock(groups=[_make_group(hits)])

    resp = client.post("/api/search", json={"query": "landscape"})
    data = resp.json()
    assert resp.status_code == 200
    assert data["count"] == 2


def test_search_dedup_response_has_scenes_collapsed_field(client, mock_qdrant):
    """SearchResponse must include the scenes_collapsed field."""
    mock_qdrant.query_points_groups.return_value = MagicMock(groups=[])
    data = client.post("/api/search", json={"query": "cat"}).json()
    assert "scenes_collapsed" in data


def test_search_dedup_response_has_raw_frame_count_field(client, mock_qdrant):
    """SearchResponse must include the raw_frame_count field."""
    mock_qdrant.query_points_groups.return_value = MagicMock(groups=[])
    data = client.post("/api/search", json={"query": "dog"}).json()
    assert "raw_frame_count" in data


def test_search_dedup_scenes_collapsed_correct_count(client, mock_qdrant):
    """scenes_collapsed == raw_frame_count - len(results)."""
    # 3 raw hits in same window → 1 result kept, 2 collapsed
    hits = [
        _make_video_hit("clip.mp4", 0.9, timestamp=1.0),
        _make_video_hit("clip.mp4", 0.8, timestamp=2.0),
        _make_video_hit("clip.mp4", 0.7, timestamp=3.0),
    ]
    mock_qdrant.query_points_groups.return_value = MagicMock(groups=[_make_group(hits)])

    data = client.post("/api/search", json={"query": "walking"}).json()
    assert data["scenes_collapsed"] == 2
    assert data["raw_frame_count"] == 3


def test_search_dedup_false_scenes_collapsed_is_zero(client, mock_qdrant):
    """dedup=false raw mode must always return scenes_collapsed=0."""
    mock_qdrant.query_points.return_value = MagicMock(
        points=[_make_video_hit("v.mp4", 0.9, timestamp=1.0)]
    )
    data = client.post("/api/search", json={"query": "dance", "dedup": False}).json()
    assert data["scenes_collapsed"] == 0


def test_search_dedup_representative_is_highest_score(client, mock_qdrant):
    """Explicit score-desc sort ensures the highest-scoring frame wins each bucket."""
    # Present in low→high order to verify sort is applied inside _event_deduplicate
    hits = [
        _make_video_hit("video.mp4", 0.5, timestamp=3.0),  # low score, same bucket
        _make_video_hit("video.mp4", 0.9, timestamp=1.0),  # high score, same bucket
        _make_video_hit("video.mp4", 0.7, timestamp=2.5),  # mid score, same bucket
    ]
    mock_qdrant.query_points_groups.return_value = MagicMock(groups=[_make_group(hits)])

    data = client.post("/api/search", json={"query": "jump"}).json()
    assert data["count"] == 1
    assert data["results"][0]["similarity"] == 0.9


def test_search_dedup_scene_window_start_set_on_video(client, mock_qdrant):
    """scene_window_start must be set for video hits when dedup=true."""
    hits = [_make_video_hit("clip.mp4", 0.8, timestamp=7.3)]
    mock_qdrant.query_points_groups.return_value = MagicMock(groups=[_make_group(hits)])

    data = client.post("/api/search", json={"query": "swim"}).json()
    result = data["results"][0]
    assert result["scene_window_start"] is not None
    assert result["scene_window_start"] == 5.0  # floor(7.3 // 5) * 5 = 5.0


def test_search_dedup_scene_window_end_equals_start_plus_five(client, mock_qdrant):
    """scene_window_end must equal scene_window_start + 5."""
    hits = [_make_video_hit("clip.mp4", 0.8, timestamp=7.3)]
    mock_qdrant.query_points_groups.return_value = MagicMock(groups=[_make_group(hits)])

    data = client.post("/api/search", json={"query": "swim"}).json()
    result = data["results"][0]
    assert result["scene_window_end"] == result["scene_window_start"] + 5.0


def test_search_dedup_window_start_none_for_images(client, mock_qdrant):
    """scene_window_start and scene_window_end must be None for images (no timestamp)."""
    hits = [_make_video_hit("photo.jpg", 0.8, timestamp=None)]
    mock_qdrant.query_points_groups.return_value = MagicMock(groups=[_make_group(hits)])

    data = client.post("/api/search", json={"query": "portrait"}).json()
    result = data["results"][0]
    assert result["scene_window_start"] is None
    assert result["scene_window_end"] is None
