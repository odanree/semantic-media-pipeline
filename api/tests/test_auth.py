"""
Tests for API key authentication (auth.py).

auth._REQUIRED and auth._API_KEY are module-level constants read from env
at import time. Tests patch them directly to exercise each branch without
reimporting the module.

Coverage targets
----------------
- Auth disabled (default): requests pass through unconditionally     [lines 42-43]
- Auth enabled, no API_KEY set: 503 Service Unavailable              [lines 45-50]
- Auth enabled, wrong key: 401 Unauthorized                          [lines 52-55]
- Auth enabled, correct key: 200                                     [lines 52-55]
- Auth enabled, missing header: 401                                  [lines 52-55]
"""

import auth
from unittest.mock import patch


# ---------------------------------------------------------------------------
# Auth disabled (API_KEY_REQUIRED=false — the default in conftest)
# ---------------------------------------------------------------------------

def test_auth_disabled_no_header_passes(client):
    """Without auth required, requests succeed even with no X-API-Key."""
    resp = client.get("/api/health")
    assert resp.status_code == 200


def test_auth_disabled_any_header_passes(client):
    resp = client.get("/api/health", headers={"X-API-Key": "random-junk"})
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Auth enabled helpers — patches the module-level flags directly
# ---------------------------------------------------------------------------

def _auth_on(key: str = "secret-key"):
    """Context manager: enable auth with a specific key."""
    return patch.multiple(auth, _REQUIRED=True, _API_KEY=key)


def _auth_on_no_key():
    """Context manager: auth required but API_KEY not configured."""
    return patch.multiple(auth, _REQUIRED=True, _API_KEY="")


# ---------------------------------------------------------------------------
# Correct key → 200
# ---------------------------------------------------------------------------

def test_auth_correct_key_passes(client):
    with _auth_on("my-secret"):
        resp = client.get("/api/health", headers={"X-API-Key": "my-secret"})
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Wrong key → 401
# ---------------------------------------------------------------------------

def test_auth_wrong_key_returns_401(client):
    with _auth_on("my-secret"):
        resp = client.get("/api/health", headers={"X-API-Key": "wrong"})
    assert resp.status_code == 401


def test_auth_wrong_key_error_message(client):
    with _auth_on("my-secret"):
        detail = client.get("/api/health", headers={"X-API-Key": "nope"}).json()["detail"]
    assert "invalid" in detail.lower() or "missing" in detail.lower()


# ---------------------------------------------------------------------------
# Missing header → 401
# ---------------------------------------------------------------------------

def test_auth_missing_header_returns_401(client):
    with _auth_on("my-secret"):
        resp = client.get("/api/health")
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# API_KEY_REQUIRED=true but API_KEY not set → 503
# ---------------------------------------------------------------------------

def test_auth_key_required_but_not_configured_returns_503(client):
    with _auth_on_no_key():
        resp = client.get("/api/health", headers={"X-API-Key": "anything"})
    assert resp.status_code == 503


# ---------------------------------------------------------------------------
# /api/ping — must be accessible even when auth is enabled
# ---------------------------------------------------------------------------

def test_ping_accessible_when_auth_disabled(client):
    resp = client.get("/api/ping")
    assert resp.status_code == 200


def test_ping_accessible_when_auth_enabled_no_key(client):
    """Health check probe must succeed even with auth on and no X-API-Key header."""
    with _auth_on("my-secret"):
        resp = client.get("/api/ping")
    assert resp.status_code == 200


def test_ping_accessible_when_api_key_not_configured(client):
    """Even the 503 edge case should not apply to /api/ping."""
    with _auth_on_no_key():
        resp = client.get("/api/ping")
    assert resp.status_code == 200
