"""
Tests for the media-serving endpoints in ingest.py and the helper functions.

Coverage targets
----------------
ingest.py:
  _placeholder_jpeg()           — callable directly, PIL-only, no side effects
  GET /api/stream               — S3 redirect, S3 presign error, access-denied,
                                  file-not-found paths (all return placeholder)
  GET /api/thumbnail            — access-denied, file-not-found, S3 presign error,
                                  S3 presign-ok paths (ffmpeg absent → placeholder)

Strategy
--------
  IS_S3 is a module-level bool set at import time from STORAGE_BACKEND env var.
  We patch *routers.ingest.IS_S3* directly so individual tests can toggle S3
  mode without restarting the app.

  _s3_presign() is patched at the module level rather than _get_s3_client() to
  keep the setup straightforward: `patch("routers.ingest._s3_presign", ...)`.

  Where ffmpeg is absent (always true in the test env) the thumbnail endpoint's
  asyncio.create_subprocess_exec call raises or produces empty stdout, both of
  which cause the code to return the JPEG placeholder — tests verify the
  safe-fallback behaviour.
"""

import os
import pytest
from botocore.exceptions import ClientError
from unittest.mock import patch, MagicMock


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _client_error(code: str = "NoSuchKey") -> ClientError:
    return ClientError({"Error": {"Code": code, "Message": "mocked"}}, "GetObject")


# ---------------------------------------------------------------------------
# _placeholder_jpeg() — pure-Python, no server needed
# ---------------------------------------------------------------------------

def test_placeholder_jpeg_returns_bytes():
    from routers.ingest import _placeholder_jpeg
    data = _placeholder_jpeg()
    assert isinstance(data, bytes)
    assert len(data) > 0


def test_placeholder_jpeg_starts_with_jpeg_magic():
    """JPEG files always start with 0xFF 0xD8."""
    from routers.ingest import _placeholder_jpeg
    data = _placeholder_jpeg()
    assert data[:2] == b"\xff\xd8"


def test_placeholder_jpeg_custom_dims():
    from routers.ingest import _placeholder_jpeg
    # Should not raise for non-default dimensions
    data = _placeholder_jpeg(width=160, height=90)
    assert len(data) > 0


# ---------------------------------------------------------------------------
# GET /api/stream — IS_S3 = True paths
# ---------------------------------------------------------------------------

def test_stream_s3_redirects(client):
    """IS_S3=True: stream should redirect to presigned URL."""
    with patch("routers.ingest.IS_S3", True):
        with patch("routers.ingest._s3_presign", return_value="https://cdn.example.com/vid.mp4"):
            resp = client.get(
                "/api/stream",
                params={"path": "media/video.mp4"},
                follow_redirects=False,
            )
    assert resp.status_code == 302
    assert "cdn.example.com" in resp.headers.get("location", "")


def test_stream_s3_presign_error_returns_placeholder(client):
    """IS_S3=True + ClientError from presign → safe 200 placeholder response."""
    with patch("routers.ingest.IS_S3", True):
        with patch("routers.ingest._s3_presign", side_effect=_client_error()):
            resp = client.get("/api/stream", params={"path": "media/gone.mp4"})
    assert resp.status_code == 200
    assert "video/mp4" in resp.headers.get("content-type", "")


# ---------------------------------------------------------------------------
# GET /api/stream — IS_S3 = False (local) paths
# ---------------------------------------------------------------------------

def test_stream_access_denied_returns_placeholder(client):
    """Path outside ALLOWED_ROOTS → safe 200 placeholder, no 403/500."""
    resp = client.get("/api/stream", params={"path": "/etc/shadow"})
    assert resp.status_code == 200
    assert "video/mp4" in resp.headers.get("content-type", "")


def test_stream_access_denied_cache_header(client):
    resp = client.get("/api/stream", params={"path": "/evil/path/video.mp4"})
    assert resp.status_code == 200
    # Safety header should be no-store for denied/placeholder responses
    assert "no-store" in resp.headers.get("cache-control", "").lower()


def test_stream_file_not_found_returns_placeholder(client):
    """Path within an allowed root but the file doesn't exist → placeholder."""
    allowed_root = os.path.realpath("/mnt/source")
    nonexistent = os.path.join(allowed_root, "totally_nonexistent_xyz_test_file_9182736.mp4")
    resp = client.get("/api/stream", params={"path": nonexistent})
    # Either it's access-denied (path resolves outside root on Windows) or
    # file-not-found — both return 200 placeholder.
    assert resp.status_code == 200
    assert "video/mp4" in resp.headers.get("content-type", "")


# ---------------------------------------------------------------------------
# GET /api/thumbnail — IS_S3 = False (local) paths
# ---------------------------------------------------------------------------

def test_thumbnail_access_denied_returns_jpeg_placeholder(client):
    """Path outside ALLOWED_ROOTS → always returns image/jpeg, never raises."""
    resp = client.get("/api/thumbnail", params={"path": "/etc/shadow", "t": "0.0"})
    assert resp.status_code == 200
    assert "image/jpeg" in resp.headers.get("content-type", "")


def test_thumbnail_access_denied_cache_no_store(client):
    resp = client.get("/api/thumbnail", params={"path": "/evil/video.mp4", "t": "0.0"})
    assert resp.status_code == 200
    assert "no-store" in resp.headers.get("cache-control", "").lower()


def test_thumbnail_file_not_found_returns_jpeg_placeholder(client):
    """File within allowed root but not on disk → placeholder JPEG."""
    allowed_root = os.path.realpath("/mnt/source")
    nonexistent = os.path.join(allowed_root, "no_such_video_xyz_9182736.mp4")
    resp = client.get("/api/thumbnail", params={"path": nonexistent, "t": "2.5"})
    assert resp.status_code == 200
    assert "image/jpeg" in resp.headers.get("content-type", "")


# ---------------------------------------------------------------------------
# GET /api/thumbnail — IS_S3 = True paths
# ---------------------------------------------------------------------------

def test_thumbnail_s3_presign_error_returns_jpeg_placeholder(client):
    """IS_S3=True + ClientError → no exception propagated, placeholder JPEG."""
    with patch("routers.ingest.IS_S3", True):
        with patch("routers.ingest._s3_presign", side_effect=_client_error()):
            resp = client.get("/api/thumbnail", params={"path": "media/video.mp4", "t": "0.0"})
    assert resp.status_code == 200
    assert "image/jpeg" in resp.headers.get("content-type", "")


def test_thumbnail_s3_ffmpeg_fails_returns_jpeg_placeholder(client):
    """
    IS_S3=True + presign succeeds + ffmpeg absent/fails → stdout=b'' →
    endpoint falls back to _placeholder_jpeg(), never raises.
    """
    with patch("routers.ingest.IS_S3", True):
        with patch("routers.ingest._s3_presign", return_value="https://cdn.example.com/v.mp4"):
            resp = client.get("/api/thumbnail", params={"path": "media/video.mp4", "t": "1.0"})
    assert resp.status_code == 200
    assert "image/jpeg" in resp.headers.get("content-type", "")
