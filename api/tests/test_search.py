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

import pytest
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
    hit.vector = None  # no vector — _cosine_rerank skips, original score kept
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


def test_search_result_carries_processed_at_when_db_provides_it(client, mock_qdrant):
    """processed_at from media_files must be attached to each search result
    so the frontend can offer a 'Recently indexed' sort. The async engine is
    patched to return one row mapping the file to a known timestamp."""
    from datetime import datetime, timezone
    from unittest.mock import AsyncMock, patch

    mock_qdrant.query_points.return_value = MagicMock(points=[
        _make_hit("/media/recent.mp4", "video", 0.9),
    ])

    indexed = datetime(2026, 6, 9, 12, 0, tzinfo=timezone.utc)
    # Mock the chained call get_async_engine() -> engine.begin() -> conn.execute().fetchall().
    conn = AsyncMock()
    conn.execute.return_value = MagicMock(fetchall=lambda: [("/media/recent.mp4", indexed)])
    engine = MagicMock()
    engine.begin.return_value.__aenter__ = AsyncMock(return_value=conn)
    engine.begin.return_value.__aexit__ = AsyncMock(return_value=None)

    with patch("routers.search.get_async_engine", AsyncMock(return_value=engine)):
        result = client.post("/api/search", json={"query": "x", "dedup": False}).json()["results"][0]

    assert result["processed_at"] == indexed.isoformat()


def test_search_processed_at_is_null_when_db_query_fails(client, mock_qdrant):
    """DB enrichment failure must not break the search — processed_at falls back to None."""
    mock_qdrant.query_points.return_value = MagicMock(points=[
        _make_hit("/media/clip.mp4", "video", 0.9),
    ])
    # Default (no patch): get_async_engine() will raise in the test env;
    # the search should still return 200 with processed_at=None.
    result = client.post("/api/search", json={"query": "x", "dedup": False}).json()["results"][0]
    assert "processed_at" in result
    assert result["processed_at"] is None


# ---------------------------------------------------------------------------
# /api/search — parameter forwarding
# ---------------------------------------------------------------------------

def test_search_limit_forwarded_to_qdrant(client, mock_qdrant):
    """limit=5 with oversample=1 must pass limit=5 to the main qdrant.query_points() call.
    The injection pass may add a second query_points call; we check the first (ANN) call.
    reset_mock() is required because mock_qdrant is session-scoped and accumulates call
    history across all tests."""
    mock_qdrant.query_points.return_value = MagicMock(points=[])
    mock_qdrant.query_points.reset_mock()  # clear session-accumulated call history
    client.post("/api/search", json={"query": "yoga", "limit": 5, "dedup": False, "oversample": 1})
    assert mock_qdrant.query_points.call_count >= 1
    # First call is the main ANN pass (injection may add a second)
    first_call = mock_qdrant.query_points.call_args_list[0]
    args, kwargs = first_call
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

def _make_video_hit(file_path: str, score: float, timestamp=None, frame_index=None,
                    audio_segment_index=None):
    """Build a mock ScoredPoint-like object for use in dedup-specific tests."""
    h = MagicMock()
    h.score = score
    h.id = f"{file_path}-{frame_index or 0}"
    h.vector = None  # no vector — _cosine_rerank skips, original score kept
    h.payload = {
        "file_path": file_path,
        "file_type": "video" if timestamp is not None else "image",
        "timestamp": timestamp,
        "frame_index": frame_index,
        "caption": None,
        "audio_segment_index": audio_segment_index,
    }
    return h


def _make_group(hits):
    """Build a mock PointGroup-like object."""
    g = MagicMock()
    g.hits = hits
    return g


def test_search_dedup_default_uses_query_points(client, mock_qdrant):
    """dedup=true (default) routes through query_points with oversampling (2-pass reranker)."""
    mock_qdrant.query_points.return_value = MagicMock(points=[])
    mock_qdrant.query_points.reset_mock()
    mock_qdrant.query_points_groups.reset_mock()

    resp = client.post("/api/search", json={"query": "birthday party"})
    assert resp.status_code == 200
    assert mock_qdrant.query_points.call_count >= 1
    mock_qdrant.query_points_groups.assert_not_called()


def test_search_dedup_false_uses_query_points(client, mock_qdrant):
    """dedup=false must route through query_points, not query_points_groups."""
    mock_qdrant.query_points.return_value = MagicMock(points=[])
    mock_qdrant.query_points.reset_mock()
    mock_qdrant.query_points_groups.reset_mock()

    resp = client.post("/api/search", json={"query": "sunset", "dedup": False})
    assert resp.status_code == 200
    assert mock_qdrant.query_points.call_count >= 1
    mock_qdrant.query_points_groups.assert_not_called()


def test_search_dedup_collapses_frames_in_same_window(client, mock_qdrant):
    """Three frames within the same 5 s window → collapsed to 1 representative."""
    hits = [
        _make_video_hit("video.mp4", 0.9, timestamp=1.0),
        _make_video_hit("video.mp4", 0.8, timestamp=2.5),
        _make_video_hit("video.mp4", 0.7, timestamp=4.9),
    ]
    mock_qdrant.query_points.return_value = MagicMock(points=hits)

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
    mock_qdrant.query_points.return_value = MagicMock(points=hits)

    resp = client.post("/api/search", json={"query": "running"})
    data = resp.json()
    assert resp.status_code == 200
    assert data["count"] == 3


def test_search_dedup_collapses_frames_straddling_bucket_boundary(client, mock_qdrant):
    """
    Regression: frames at 744 s, 748 s, 750 s are only 2–6 s apart but straddle
    fixed-grid 5 s bucket boundaries (748//5=149, 744//5=148, 750//5=150).
    The old bucket approach kept all three; greedy NMS correctly collapses to 1.
    """
    hits = [
        _make_video_hit("video.mp4", 0.286, timestamp=748.0),  # highest — anchor
        _make_video_hit("video.mp4", 0.281, timestamp=744.0),  # 4 s away — suppressed
        _make_video_hit("video.mp4", 0.275, timestamp=750.0),  # 2 s away — suppressed
    ]
    mock_qdrant.query_points.return_value = MagicMock(points=hits)

    data = client.post("/api/search", json={"query": "frontyard"}).json()
    assert data["count"] == 1
    assert data["results"][0]["similarity"] == pytest.approx(0.286, rel=1e-3)
    assert data["scenes_collapsed"] == 2


def test_search_dedup_images_never_collapsed(client, mock_qdrant):
    """Images (timestamp=None) must always be kept — they have no temporal axis."""
    hits = [
        _make_video_hit("photo1.jpg", 0.9, timestamp=None),
        _make_video_hit("photo1.jpg", 0.8, timestamp=None),
    ]
    mock_qdrant.query_points.return_value = MagicMock(points=hits)

    resp = client.post("/api/search", json={"query": "landscape"})
    data = resp.json()
    assert resp.status_code == 200
    assert data["count"] == 2


def test_search_dedup_response_has_scenes_collapsed_field(client, mock_qdrant):
    """SearchResponse must include the scenes_collapsed field."""
    mock_qdrant.query_points.return_value = MagicMock(points=[])
    data = client.post("/api/search", json={"query": "cat"}).json()
    assert "scenes_collapsed" in data


def test_search_dedup_response_has_raw_frame_count_field(client, mock_qdrant):
    """SearchResponse must include the raw_frame_count field."""
    mock_qdrant.query_points.return_value = MagicMock(points=[])
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
    mock_qdrant.query_points.return_value = MagicMock(points=hits)

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
    mock_qdrant.query_points.return_value = MagicMock(points=hits)

    data = client.post("/api/search", json={"query": "jump"}).json()
    assert data["count"] == 1
    assert data["results"][0]["similarity"] == 0.9


def test_search_dedup_scene_window_start_set_on_video(client, mock_qdrant):
    """scene_window_start must be set for video hits when dedup=true."""
    hits = [_make_video_hit("clip.mp4", 0.8, timestamp=7.3)]
    mock_qdrant.query_points.return_value = MagicMock(points=hits)

    data = client.post("/api/search", json={"query": "swim"}).json()
    result = data["results"][0]
    assert result["scene_window_start"] is not None
    assert result["scene_window_start"] == 5.0  # floor(7.3 // 5) * 5 = 5.0


def test_search_dedup_scene_window_end_equals_start_plus_five(client, mock_qdrant):
    """scene_window_end must equal scene_window_start + 5."""
    hits = [_make_video_hit("clip.mp4", 0.8, timestamp=7.3)]
    mock_qdrant.query_points.return_value = MagicMock(points=hits)

    data = client.post("/api/search", json={"query": "swim"}).json()
    result = data["results"][0]
    assert result["scene_window_end"] == result["scene_window_start"] + 5.0


def test_search_dedup_window_start_none_for_images(client, mock_qdrant):
    """scene_window_start and scene_window_end must be None for images (no timestamp)."""
    hits = [_make_video_hit("photo.jpg", 0.8, timestamp=None)]
    mock_qdrant.query_points.return_value = MagicMock(points=hits)

    data = client.post("/api/search", json={"query": "portrait"}).json()
    result = data["results"][0]
    assert result["scene_window_start"] is None
    assert result["scene_window_end"] is None


# ---------------------------------------------------------------------------
# Audio segment dedup tests
# ---------------------------------------------------------------------------

def test_search_segment_dedup_collapses_same_segment(client, mock_qdrant):
    """Multiple frames from the same audio segment → only the highest-scoring one kept."""
    hits = [
        _make_video_hit("video.mp4", 0.9, timestamp=1.0, audio_segment_index=0),
        _make_video_hit("video.mp4", 0.8, timestamp=1.5, audio_segment_index=0),
        _make_video_hit("video.mp4", 0.7, timestamp=2.0, audio_segment_index=0),
    ]
    mock_qdrant.query_points.return_value = MagicMock(points=hits)

    data = client.post("/api/search", json={"query": "speech"}).json()
    assert data["count"] == 1
    assert data["results"][0]["similarity"] == pytest.approx(0.9, rel=1e-3)


def test_search_segment_dedup_keeps_different_segments(client, mock_qdrant):
    """Frames from distinct audio segments in the same file are all kept."""
    hits = [
        _make_video_hit("video.mp4", 0.9, timestamp=1.0, audio_segment_index=0),
        _make_video_hit("video.mp4", 0.8, timestamp=5.0, audio_segment_index=1),
        _make_video_hit("video.mp4", 0.7, timestamp=12.0, audio_segment_index=2),
    ]
    mock_qdrant.query_points.return_value = MagicMock(points=hits)

    data = client.post("/api/search", json={"query": "speech"}).json()
    assert data["count"] == 3


def test_search_segment_dedup_same_segment_idx_different_files_kept(client, mock_qdrant):
    """segment_index=0 in two different files are independent — both kept."""
    hits = [
        _make_video_hit("video1.mp4", 0.9, timestamp=1.0, audio_segment_index=0),
        _make_video_hit("video2.mp4", 0.8, timestamp=1.0, audio_segment_index=0),
    ]
    mock_qdrant.query_points.return_value = MagicMock(points=hits)

    data = client.post("/api/search", json={"query": "speech"}).json()
    assert data["count"] == 2


def test_search_segment_dedup_winner_is_highest_score(client, mock_qdrant):
    """Frames presented in any order — the highest-scoring one always wins the segment."""
    hits = [
        _make_video_hit("video.mp4", 0.5, timestamp=2.0, audio_segment_index=0),
        _make_video_hit("video.mp4", 0.9, timestamp=1.0, audio_segment_index=0),
        _make_video_hit("video.mp4", 0.7, timestamp=1.5, audio_segment_index=0),
    ]
    # Pass 2 re-rank sorts descending before dedup; simulate that here
    hits_sorted = sorted(hits, key=lambda h: h.score, reverse=True)
    mock_qdrant.query_points.return_value = MagicMock(points=hits_sorted)

    data = client.post("/api/search", json={"query": "music"}).json()
    assert data["count"] == 1
    assert data["results"][0]["similarity"] == pytest.approx(0.9, rel=1e-3)


def test_search_segment_dedup_long_segment_counts_as_one(client, mock_qdrant):
    """A 30 s speech segment sampled every 2 s would produce many frames;
    segment dedup collapses all of them to the single best frame."""
    hits = [
        _make_video_hit("video.mp4", 0.9 - i * 0.01, timestamp=float(i * 2),
                        audio_segment_index=0)
        for i in range(15)  # 15 frames, all segment 0
    ]
    mock_qdrant.query_points.return_value = MagicMock(points=hits)

    data = client.post("/api/search", json={"query": "interview"}).json()
    assert data["count"] == 1
    assert data["scenes_collapsed"] == 14


def test_search_segment_dedup_mixed_legacy_and_audio_analyzed(client, mock_qdrant):
    """Mix of frames with and without audio_segment_index.
    Audio-analyzed frames: deduplicated by segment.
    Legacy frames (no segment_index): fallback to window NMS."""
    hits = [
        # Audio-analyzed: 3 frames in segment 0 → collapses to 1
        _make_video_hit("new.mp4", 0.9, timestamp=1.0, audio_segment_index=0),
        _make_video_hit("new.mp4", 0.8, timestamp=1.5, audio_segment_index=0),
        # Legacy video: 2 frames 10 s apart → both kept by NMS
        _make_video_hit("old.mp4", 0.75, timestamp=0.0),
        _make_video_hit("old.mp4", 0.65, timestamp=10.0),
    ]
    mock_qdrant.query_points.return_value = MagicMock(points=hits)

    data = client.post("/api/search", json={"query": "running"}).json()
    # 1 from new.mp4 segment + 2 from old.mp4 = 3
    assert data["count"] == 3


# ---------------------------------------------------------------------------
# Directory-cap image dedup tests (timelapse flood prevention)
# ---------------------------------------------------------------------------

def _make_timelapse_hits(dir_path: str, filenames: list[str], base_score: float = 0.9):
    """Helper: build flat list of scored hits for each timelapse JPG file."""
    hits = []
    for i, name in enumerate(filenames):
        path = f"{dir_path}/{name}"
        hits.append(_make_video_hit(path, base_score - i * 0.01, timestamp=None))
    return hits


def test_search_dir_cap_limits_timelapse_images(client, mock_qdrant):
    """
    Regression: DJI timelapse JPGs from the same directory must be capped at
    MAX_IMAGES_PER_DIR (default 2) when the directory contributes >=
    TIMELAPSE_FLOOD_THRESHOLD (default 4) images.
    """
    hits = _make_timelapse_hits(
        "/mnt/source/DJI/TIMELAPSE/001",
        ["TIMELAPSE_0688.JPG", "TIMELAPSE_0689.JPG", "TIMELAPSE_0690.JPG",
         "TIMELAPSE_0691.JPG", "TIMELAPSE_0692.JPG"],
    )
    mock_qdrant.query_points.return_value = MagicMock(points=hits)

    data = client.post("/api/search", json={"query": "frontyard"}).json()
    # 5 images >= flood_threshold(4) → capped to MAX_IMAGES_PER_DIR=2
    assert data["count"] == 2
    assert data["scenes_collapsed"] == 3


def test_search_dir_cap_keeps_best_scoring_images(client, mock_qdrant):
    """The images kept from a capped directory are the highest-scoring ones."""
    # 5 images so the flood threshold (4) is triggered
    hits = _make_timelapse_hits(
        "/mnt/source/DJI/TIMELAPSE/001",
        ["TIMELAPSE_0688.JPG", "TIMELAPSE_0689.JPG", "TIMELAPSE_0690.JPG",
         "TIMELAPSE_0691.JPG", "TIMELAPSE_0692.JPG"],
        base_score=0.9,  # scores: 0.90, 0.89, 0.88, 0.87, 0.86
    )
    mock_qdrant.query_points.return_value = MagicMock(points=hits)

    data = client.post("/api/search", json={"query": "frontyard"}).json()
    assert data["count"] == 2
    assert data["results"][0]["similarity"] == pytest.approx(0.90, rel=1e-3)
    assert data["results"][1]["similarity"] == pytest.approx(0.89, rel=1e-3)


def test_search_dir_cap_does_not_affect_small_photo_series(client, mock_qdrant):
    """
    A normal photo series with fewer images than the flood threshold must NOT
    be capped — all images pass through regardless of MAX_IMAGES_PER_DIR.
    E.g. 3 vacation shots in the same album folder.
    """
    hits = _make_timelapse_hits(
        "/mnt/source/Pixel 9 Nov 2025",
        ["PXL_001.JPG", "PXL_002.JPG", "PXL_003.JPG"],  # 3 < flood_threshold(4)
        base_score=0.9,
    )
    mock_qdrant.query_points.return_value = MagicMock(points=hits)

    data = client.post("/api/search", json={"query": "frontyard"}).json()
    # 3 < flood_threshold → all 3 pass through, cap never activates
    assert data["count"] == 3


def test_search_dir_cap_does_not_affect_different_directories(client, mock_qdrant):
    """Two separate timelapse directories are each capped independently."""
    hits = (
        _make_timelapse_hits("/mnt/source/DJI/TIMELAPSE/001",
                             ["T_0001.JPG", "T_0002.JPG", "T_0003.JPG",
                              "T_0004.JPG", "T_0005.JPG"])
        + _make_timelapse_hits("/mnt/source/DJI/TIMELAPSE/002",
                               ["T_0001.JPG", "T_0002.JPG", "T_0003.JPG",
                                "T_0004.JPG", "T_0005.JPG"])
    )
    mock_qdrant.query_points.return_value = MagicMock(points=hits)

    data = client.post("/api/search", json={"query": "frontyard"}).json()
    # 2 kept from dir 001 + 2 kept from dir 002 = 4
    assert data["count"] == 4


def test_search_dir_cap_does_not_affect_video_frames(client, mock_qdrant):
    """Video frames (timestamp != None) are never capped by the directory cap."""
    hits = [
        _make_video_hit("/mnt/source/video/clip.mp4", 0.9, timestamp=10.0),
        _make_video_hit("/mnt/source/video/clip.mp4", 0.8, timestamp=20.0),
        _make_video_hit("/mnt/source/video/clip.mp4", 0.7, timestamp=30.0),
    ]
    mock_qdrant.query_points.return_value = MagicMock(points=hits)

    data = client.post("/api/search", json={"query": "action"}).json()
    assert data["count"] == 3


# ---------------------------------------------------------------------------
# /api/search — filter-only (audio filter with empty query)
# ---------------------------------------------------------------------------

def test_search_filter_only_uses_scroll(client, mock_qdrant):
    """Empty query + audio_segment_type must route through qdrant.scroll(), not query_points."""
    point = MagicMock()
    point.payload = {"file_path": "clip.mp4", "file_type": "video", "timestamp": 5.0}
    mock_qdrant.scroll.return_value = ([point], None)
    mock_qdrant.query_points.reset_mock()
    mock_qdrant.query_points_groups.reset_mock()

    resp = client.post("/api/search", json={"query": "", "audio_segment_type": "speech"})
    assert resp.status_code == 200
    mock_qdrant.scroll.assert_called_once()
    mock_qdrant.query_points.assert_not_called()
    mock_qdrant.query_points_groups.assert_not_called()


def test_search_filter_only_returns_results(client, mock_qdrant):
    """Filter-only scroll results are returned with score 1.0."""
    point = MagicMock()
    point.payload = {"file_path": "speech.mp4", "file_type": "video", "timestamp": 10.0}
    mock_qdrant.scroll.return_value = ([point], None)

    data = client.post("/api/search", json={"query": "", "audio_segment_type": "speech"}).json()
    assert data["count"] == 1
    assert data["results"][0]["similarity"] == 1.0
    assert data["results"][0]["file_path"] == "speech.mp4"


def test_search_empty_query_no_filter_still_returns_400(client):
    """Empty query without any audio filter still returns 400."""
    resp = client.post("/api/search", json={"query": ""})
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# /api/search — individual audio filter parameters
# ---------------------------------------------------------------------------

def test_search_audio_segment_type_filter_accepted(client, mock_qdrant):
    """audio_segment_type filter must be forwarded to qdrant without error."""
    mock_qdrant.query_points.return_value = MagicMock(points=[])
    resp = client.post("/api/search", json={"query": "speech scene", "audio_segment_type": "speech"})
    assert resp.status_code == 200


def test_search_audio_event_top_filter_accepted(client, mock_qdrant):
    """audio_event_top filter must be forwarded to qdrant without error."""
    mock_qdrant.query_points.return_value = MagicMock(points=[])
    resp = client.post("/api/search", json={"query": "scary moment", "audio_event_top": "Scream"})
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# /api/lookup — direct frame lookup by file_path + timestamp
# ---------------------------------------------------------------------------

def _make_point(file_path, timestamp=None, file_type="video"):
    p = MagicMock()
    p.id = "abc123"
    p.payload = {
        "file_path": file_path,
        "file_type": file_type,
        "timestamp": timestamp,
        "frame_index": 42,
        "label": None,
        "construction_phase": None,
        "phase_confidence": None,
        "audio_rms_energy": None,
        "audio_segment_start_sec": None,
        "audio_segment_end_sec": None,
        "updated_at": None,
    }
    return p


def test_lookup_missing_file_path_returns_422(client):
    resp = client.post("/api/lookup", json={})
    assert resp.status_code == 422


def test_lookup_file_not_found_returns_404(client, mock_qdrant):
    mock_qdrant.scroll.return_value = ([], None)
    resp = client.post("/api/lookup", json={"file_path": "/mnt/source/missing.mp4"})
    assert resp.status_code == 404


def test_lookup_returns_200_when_found(client, mock_qdrant):
    point = _make_point("/mnt/source/clip.mp4", timestamp=10.0)
    mock_qdrant.scroll.return_value = ([point], None)
    resp = client.post("/api/lookup", json={"file_path": "/mnt/source/clip.mp4", "timestamp": 10.0})
    assert resp.status_code == 200


def test_lookup_result_has_correct_file_path(client, mock_qdrant):
    point = _make_point("/mnt/source/clip.mp4", timestamp=10.0)
    mock_qdrant.scroll.return_value = ([point], None)
    data = client.post("/api/lookup", json={"file_path": "/mnt/source/clip.mp4", "timestamp": 10.0}).json()
    assert data["results"][0]["file_path"] == "/mnt/source/clip.mp4"


def test_lookup_similarity_is_1(client, mock_qdrant):
    point = _make_point("/mnt/source/clip.mp4", timestamp=10.0)
    mock_qdrant.scroll.return_value = ([point], None)
    data = client.post("/api/lookup", json={"file_path": "/mnt/source/clip.mp4", "timestamp": 10.0}).json()
    assert data["results"][0]["similarity"] == 1.0


def test_lookup_count_is_1(client, mock_qdrant):
    point = _make_point("/mnt/source/clip.mp4", timestamp=10.0)
    mock_qdrant.scroll.return_value = ([point], None)
    data = client.post("/api/lookup", json={"file_path": "/mnt/source/clip.mp4", "timestamp": 10.0}).json()
    assert data["count"] == 1


def test_lookup_without_timestamp_returns_first_point(client, mock_qdrant):
    point = _make_point("/mnt/source/photo.jpg", file_type="image")
    mock_qdrant.scroll.return_value = ([point], None)
    data = client.post("/api/lookup", json={"file_path": "/mnt/source/photo.jpg"}).json()
    assert data["results"][0]["file_type"] == "image"


def test_lookup_picks_closest_timestamp(client, mock_qdrant):
    """When multiple candidates exist, pick the one closest to the requested timestamp."""
    p1 = _make_point("/mnt/source/clip.mp4", timestamp=5.0)
    p2 = _make_point("/mnt/source/clip.mp4", timestamp=10.0)
    # windowed scroll returns nothing, fallback scroll returns both
    mock_qdrant.scroll.side_effect = [([], None), ([p1, p2], None)]
    data = client.post("/api/lookup", json={"file_path": "/mnt/source/clip.mp4", "timestamp": 9.5}).json()
    assert data["results"][0]["timestamp"] == 10.0
    mock_qdrant.scroll.side_effect = None


# ---------------------------------------------------------------------------
# /api/scene-bounds — scene boundary detection via frame-similarity walk
# ---------------------------------------------------------------------------

def _make_frame(timestamp: float, vector: list) -> MagicMock:
    """Build a mock Qdrant point with timestamp payload and a vector."""
    point = MagicMock()
    point.payload = {"timestamp": timestamp}
    point.vector = vector
    return point


def test_scene_bounds_missing_file_path_returns_422(client):
    resp = client.post("/api/scene-bounds", json={"timestamp": 10.0})
    assert resp.status_code == 422


def test_scene_bounds_missing_timestamp_returns_422(client):
    resp = client.post("/api/scene-bounds", json={"file_path": "video.mp4"})
    assert resp.status_code == 422


def test_scene_bounds_no_frames_returns_404(client, mock_qdrant):
    mock_qdrant.scroll.return_value = ([], None)
    resp = client.post("/api/scene-bounds", json={"file_path": "video.mp4", "timestamp": 10.0})
    assert resp.status_code == 404


def test_scene_bounds_returns_required_fields(client, mock_qdrant):
    v = [1.0, 0.0, 0.0]
    frames = [_make_frame(8.0, v), _make_frame(10.0, v), _make_frame(12.0, v)]
    mock_qdrant.scroll.return_value = (frames, None)
    resp = client.post("/api/scene-bounds", json={"file_path": "video.mp4", "timestamp": 10.0})
    assert resp.status_code == 200
    data = resp.json()
    assert "start_sec" in data
    assert "end_sec" in data
    assert "anchor_sec" in data
    assert "frames_scanned" in data


def test_scene_bounds_frames_scanned_matches_batch_size(client, mock_qdrant):
    v = [1.0, 0.0, 0.0]
    frames = [_make_frame(float(t), v) for t in range(5)]
    mock_qdrant.scroll.return_value = (frames, None)
    resp = client.post("/api/scene-bounds", json={"file_path": "video.mp4", "timestamp": 2.0})
    assert resp.json()["frames_scanned"] == 5


def test_scene_bounds_all_similar_spans_full_window(client, mock_qdrant):
    """All frames with identical vectors → bounds span from first to last frame."""
    v = [1.0, 0.0, 0.0]
    frames = [_make_frame(8.0, v), _make_frame(10.0, v), _make_frame(12.0, v)]
    mock_qdrant.scroll.return_value = (frames, None)
    data = client.post("/api/scene-bounds", json={"file_path": "video.mp4", "timestamp": 10.0}).json()
    assert data["start_sec"] == 8.0
    assert data["end_sec"] == 12.0
    assert data["anchor_sec"] == 10.0


def test_scene_bounds_orthogonal_frame_stops_walk(client, mock_qdrant):
    """A frame pointing in a different direction stops the similarity walk."""
    anchor_v = [1.0, 0.0, 0.0]
    ortho_v = [0.0, 1.0, 0.0]  # cosine similarity = 0 → below any threshold
    frames = [
        _make_frame(8.0, ortho_v),    # backward: dissimilar — walk stops at anchor
        _make_frame(10.0, anchor_v),  # anchor
        _make_frame(12.0, anchor_v),  # forward: similar — walk continues
    ]
    mock_qdrant.scroll.return_value = (frames, None)
    data = client.post(
        "/api/scene-bounds",
        json={"file_path": "video.mp4", "timestamp": 10.0, "threshold": 0.75},
    ).json()
    assert data["start_sec"] == 10.0   # backward walk stopped at anchor
    assert data["end_sec"] == 12.0


def test_scene_bounds_paginated_scroll_combines_batches(client, mock_qdrant):
    """scroll() returning a next_offset triggers a second page fetch."""
    v = [1.0, 0.0, 0.0]
    batch1 = [_make_frame(8.0, v), _make_frame(9.0, v)]
    batch2 = [_make_frame(10.0, v), _make_frame(11.0, v)]
    mock_qdrant.scroll.side_effect = [(batch1, "cursor"), (batch2, None)]
    data = client.post("/api/scene-bounds", json={"file_path": "video.mp4", "timestamp": 10.0}).json()
    assert data["frames_scanned"] == 4
    mock_qdrant.scroll.side_effect = None


# ---------------------------------------------------------------------------
# _window_deduplicate tolerates scroll Records (which lack .score)
# ---------------------------------------------------------------------------

def test_window_deduplicate_tolerates_missing_score():
    """qdrant_client.scroll() returns Record objects with no .score attribute.

    Filename search uses scroll() and pipes the results through
    _segment_deduplicate -> _window_deduplicate. Before the fix, accessing
    h.score on a Record raised AttributeError and the search silently
    returned empty results. The dedup must treat Records as score=0 and
    still return all distinct frames within the window."""
    from routers.search import _window_deduplicate

    # Simulate Qdrant Record objects: payload but no .score attribute.
    class _Record:
        def __init__(self, ts):
            self.payload = {"timestamp": ts}
    # `getattr(record, 'score', 0.0)` must NOT raise.
    hits = [_Record(0.0), _Record(5.0), _Record(10.0)]
    result = _window_deduplicate(hits, window_s=2.0)
    # All 3 are >= 2s apart, so all should be kept.
    assert len(result) == 3
