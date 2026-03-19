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


# ---------------------------------------------------------------------------
# POST /api/playlist — validation paths (no ffmpeg required)
# ---------------------------------------------------------------------------

def test_playlist_empty_clips_returns_400(client):
    resp = client.post("/api/playlist", json={"clips": []})
    assert resp.status_code == 400


def test_playlist_missing_clips_field_returns_422(client):
    resp = client.post("/api/playlist", json={})
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# GET /api/playlist/serve/{token}/{filename} — validation paths
# ---------------------------------------------------------------------------

def test_serve_playlist_invalid_token_returns_400(client):
    resp = client.get("/api/playlist/serve/not-a-uuid/index.m3u8")
    assert resp.status_code == 400


def test_serve_playlist_path_traversal_returns_400(client):
    import uuid as uuid_mod
    token = str(uuid_mod.uuid4())
    resp = client.get(f"/api/playlist/serve/{token}/../secret")
    # FastAPI will URL-decode the path — traversal via ".." in filename segment
    assert resp.status_code in (400, 404)


def test_serve_playlist_missing_file_returns_404(client):
    import uuid as uuid_mod
    token = str(uuid_mod.uuid4())
    resp = client.get(f"/api/playlist/serve/{token}/index.m3u8")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# _translate_path — pure function, no external deps
# ---------------------------------------------------------------------------

def test_translate_path_no_maps_noop():
    from routers.ingest import _translate_path
    with patch.dict(os.environ, {}, clear=False):
        # Remove any LUMEN_PATH_MAP_ vars for this test
        env = {k: v for k, v in os.environ.items() if not k.startswith("LUMEN_PATH_MAP_")}
        with patch.dict(os.environ, env, clear=True):
            assert _translate_path("/mnt/source/foo.mp4") == "/mnt/source/foo.mp4"


def test_translate_path_maps_windows_to_linux():
    from routers.ingest import _translate_path
    maps = {"LUMEN_PATH_MAP_0": "/mnt/source:C:/media"}
    with patch.dict(os.environ, maps):
        result = _translate_path("C:/media/clip.mp4")
    assert result == "/mnt/source/clip.mp4"


def test_translate_path_longest_prefix_wins():
    from routers.ingest import _translate_path
    maps = {
        "LUMEN_PATH_MAP_0": "/mnt/a:C:/media",
        "LUMEN_PATH_MAP_1": "/mnt/b:C:/media/sub",
    }
    with patch.dict(os.environ, maps):
        result = _translate_path("C:/media/sub/clip.mp4")
    assert result == "/mnt/b/clip.mp4"


def test_translate_path_backslash_normalised():
    from routers.ingest import _translate_path
    maps = {"LUMEN_PATH_MAP_0": "/mnt/source:C:/media"}
    with patch.dict(os.environ, maps):
        result = _translate_path("C:\\media\\clip.mp4")
    assert result == "/mnt/source/clip.mp4"


# ---------------------------------------------------------------------------
# _placeholder_jpeg / _placeholder_video_stub — sanity checks
# ---------------------------------------------------------------------------

def test_placeholder_jpeg_returns_bytes():
    from routers.ingest import _placeholder_jpeg
    data = _placeholder_jpeg()
    assert isinstance(data, bytes)
    assert len(data) > 0


def test_placeholder_video_stub_returns_bytes():
    from routers.ingest import _placeholder_video_stub
    data = _placeholder_video_stub()
    assert isinstance(data, bytes)


# ---------------------------------------------------------------------------
# _sidecar_for — filesystem lookup for moov sidecars
# ---------------------------------------------------------------------------

def test_sidecar_for_no_sidecar_root_returns_none():
    import routers.ingest as ing
    orig = ing._SIDECAR_ROOT
    try:
        ing._SIDECAR_ROOT = ""
        from routers.ingest import _sidecar_for
        assert _sidecar_for("/mnt/source/clip.mp4") is None
    finally:
        ing._SIDECAR_ROOT = orig


def test_sidecar_for_path_not_under_source_returns_none():
    import routers.ingest as ing
    orig_sr = ing._SIDECAR_ROOT
    try:
        ing._SIDECAR_ROOT = "/mnt/sidecars"
        from routers.ingest import _sidecar_for
        assert _sidecar_for("/mnt/other/clip.mp4") is None
    finally:
        ing._SIDECAR_ROOT = orig_sr


def test_sidecar_for_missing_files_returns_none():
    import routers.ingest as ing
    orig_sr = ing._SIDECAR_ROOT
    try:
        ing._SIDECAR_ROOT = "/mnt/sidecars"
        from routers.ingest import _sidecar_for
        with patch("os.path.isfile", return_value=False):
            assert _sidecar_for("/mnt/source/clip.mp4") is None
    finally:
        ing._SIDECAR_ROOT = orig_sr


def test_sidecar_for_existing_sidecar_returns_paths():
    import routers.ingest as ing
    orig_sr = ing._SIDECAR_ROOT
    try:
        ing._SIDECAR_ROOT = "/mnt/sidecars"
        from routers.ingest import _sidecar_for
        with patch("os.path.isfile", return_value=True):
            result = _sidecar_for("/mnt/source/clip.mp4")
        assert result is not None
        sidecar, meta = result
        assert sidecar.endswith(".moov")
        assert meta.endswith(".moov.json")
    finally:
        ing._SIDECAR_ROOT = orig_sr


# ---------------------------------------------------------------------------
# _probe_codecs — async ffprobe wrapper
# ---------------------------------------------------------------------------

def test_probe_codecs_parses_h264_aac():
    import asyncio
    from unittest.mock import AsyncMock
    from routers.ingest import _probe_codecs

    mock_proc = MagicMock()
    mock_proc.communicate = AsyncMock(
        return_value=(b"h264,video,\naac,audio,48000\n", b"")
    )
    with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=mock_proc)):
        video, audio, sr = asyncio.run(_probe_codecs("/fake/path.mp4"))

    assert video == "h264"
    assert audio == "aac"
    assert sr == 48000


def test_probe_codecs_parses_hevc_ac3():
    import asyncio
    from unittest.mock import AsyncMock
    from routers.ingest import _probe_codecs

    mock_proc = MagicMock()
    mock_proc.communicate = AsyncMock(
        return_value=(b"hevc,video,\nac3,audio,48000\n", b"")
    )
    with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=mock_proc)):
        video, audio, sr = asyncio.run(_probe_codecs("/fake/path.mp4"))

    assert video == "hevc"
    assert audio == "ac3"


def test_probe_codecs_returns_empty_on_error():
    import asyncio
    from unittest.mock import AsyncMock
    from routers.ingest import _probe_codecs

    with patch("asyncio.create_subprocess_exec", new=AsyncMock(side_effect=Exception("no ffprobe"))):
        video, audio, sr = asyncio.run(_probe_codecs("/fake/path.mp4"))

    assert video == ""
    assert audio == ""
    assert sr == 0


def test_probe_codecs_no_audio_stream():
    import asyncio
    from unittest.mock import AsyncMock
    from routers.ingest import _probe_codecs

    mock_proc = MagicMock()
    mock_proc.communicate = AsyncMock(
        return_value=(b"h264,video,\n", b"")
    )
    with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=mock_proc)):
        video, audio, sr = asyncio.run(_probe_codecs("/fake/path.mp4"))

    assert video == "h264"
    assert audio == ""
    assert sr == 0


# ---------------------------------------------------------------------------
# generate_playlist — happy path with mocked _extract_segment
# ---------------------------------------------------------------------------

def test_playlist_success_returns_manifest(client):
    from unittest.mock import AsyncMock, mock_open

    clips = [
        {"file_path": "/mnt/source/a.mp4", "start_sec": 0.0, "end_sec": 5.0},
        {"file_path": "/mnt/source/b.mp4", "start_sec": 10.0, "end_sec": 15.0},
    ]

    with (
        patch("routers.ingest._extract_segment", new=AsyncMock(return_value=True)),
        patch("routers.ingest.os.path.isfile", return_value=True),
        patch("routers.ingest.os.makedirs"),
        patch("builtins.open", mock_open()),
    ):
        resp = client.post("/api/playlist", json={"clips": clips})

    assert resp.status_code == 200
    data = resp.json()
    assert data["clip_count"] == 2
    assert "playlist_url" in data
    assert "token" in data


def test_playlist_all_segments_fail_returns_500(client):
    from unittest.mock import AsyncMock

    clips = [{"file_path": "/mnt/source/a.mp4", "start_sec": 0.0, "end_sec": 5.0}]

    with (
        patch("routers.ingest._extract_segment", new=AsyncMock(return_value=False)),
        patch("routers.ingest.os.path.isfile", return_value=True),
        patch("routers.ingest.os.makedirs"),
    ):
        resp = client.post("/api/playlist", json={"clips": clips})

    assert resp.status_code == 500
