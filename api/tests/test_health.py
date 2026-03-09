"""
Tests for /api/health and / endpoints.

Coverage
--------
- Happy path: all components respond → 200 healthy
- Qdrant failure: unhealthy Qdrant is reported but endpoint still returns 200
- Root endpoint: name + status fields present
- Response schema: required keys exist on every call
"""

from unittest.mock import MagicMock


# ---------------------------------------------------------------------------
# /api/health
# ---------------------------------------------------------------------------

def test_health_returns_200(client):
    resp = client.get("/api/health")
    assert resp.status_code == 200


def test_health_status_healthy(client):
    resp = client.get("/api/health")
    data = resp.json()
    assert data["status"] == "healthy"


def test_health_has_required_keys(client):
    resp = client.get("/api/health")
    data = resp.json()
    assert "status" in data
    assert "timestamp" in data
    assert "components" in data
    assert "collections" in data


def test_health_components_include_qdrant(client):
    resp = client.get("/api/health")
    components = resp.json()["components"]
    assert "qdrant" in components


def test_health_collection_count_is_int(client):
    resp = client.get("/api/health")
    assert isinstance(resp.json()["collections"], int)


def test_health_qdrant_error_still_returns_200(client, mock_qdrant):
    """
    When Qdrant raises an exception, the health endpoint must still return 200
    (it degrades gracefully and reports the error in the response body).
    """
    mock_qdrant.get_collections.side_effect = ConnectionError("qdrant unreachable")
    try:
        resp = client.get("/api/health")
        assert resp.status_code == 200
        data = resp.json()
        # Either qdrant component reports an error string, or the top-level
        # status flips to "unhealthy" — both are acceptable degraded states.
        qdrant_ok = data["components"]["qdrant"] == "ok"
        top_unhealthy = data["status"] == "unhealthy"
        assert not qdrant_ok or top_unhealthy  # at least one signals the problem
    finally:
        # Always restore so other tests are not affected
        mock_qdrant.get_collections.side_effect = None
        col = MagicMock()
        col.name = "media_vectors"
        mock_qdrant.get_collections.return_value = MagicMock(collections=[col])


# ---------------------------------------------------------------------------
# GET /  (root)
# ---------------------------------------------------------------------------

def test_root_returns_200(client):
    resp = client.get("/")
    assert resp.status_code == 200


def test_root_name_field(client):
    data = client.get("/").json()
    assert data["name"] == "Lumen API"


def test_root_status_running(client):
    data = client.get("/").json()
    assert data["status"] == "running"


def test_root_has_timestamp(client):
    data = client.get("/").json()
    assert "timestamp" in data


# ---------------------------------------------------------------------------
# /api/ping — unauthenticated liveness probe
# ---------------------------------------------------------------------------

def test_ping_returns_200(client):
    resp = client.get("/api/ping")
    assert resp.status_code == 200


def test_ping_returns_ok_status(client):
    resp = client.get("/api/ping")
    assert resp.json() == {"status": "ok"}
