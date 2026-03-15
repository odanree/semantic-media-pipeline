"""
Pytest fixtures and mocking infrastructure for the Lumen API tests.

Strategy
--------
External dependencies (Qdrant, PostgreSQL, Redis, CLIP model) are mocked at
the function/method level so tests run in-process without a live server.

  - FastAPI startup events are cleared to prevent CLIP preloading.
  - QdrantClient instances created at router module level are patched in place.
  - _get_session() / _get_qdrant() in stats.py are patched to return mock objects.
  - get_clip_model() in search.py is patched to return a lightweight numpy mock.

Environment variables must be set BEFORE any app code is imported because
several modules (rate_limit.py, auth.py, routers/*) read env vars at module
load time.
"""

import os
import sys
from unittest.mock import MagicMock

import numpy as np
import pytest

# ---------------------------------------------------------------------------
# 0. Stub heavy ML / GPU libs before any app code is imported.
#
#    torch (2+ GB) and sentence-transformers are NOT installed in the test
#    venv — we inject lightweight MagicMock stand-ins into sys.modules so that
#    `import torch` and `from sentence_transformers import SentenceTransformer`
#    succeed without actually loading the libraries.
#
#    The real model is always mocked at the function level (see mock_clip
#    fixture), so the stubs only need to satisfy the import machinery.
# ---------------------------------------------------------------------------

# Build a mock torch module whose submodule attributes forward correctly.
_torch = MagicMock(name="torch")
_torch.nn = MagicMock(name="torch.nn")
_torch.cuda = MagicMock(name="torch.cuda")
_torch.cuda.is_available.return_value = False
_torch.zeros = MagicMock(name="torch.zeros", return_value=MagicMock())

for _name, _stub in [
    ("torch",                   _torch),
    ("torch.nn",                _torch.nn),
    ("torch.nn.functional",     MagicMock(name="torch.nn.functional")),
    ("torch.cuda",              _torch.cuda),
    ("torch.utils",             MagicMock(name="torch.utils")),
    ("torch.utils.data",        MagicMock(name="torch.utils.data")),
    ("torch_directml",          MagicMock(name="torch_directml")),
    ("sentence_transformers",   MagicMock(name="sentence_transformers")),
]:
    if _name not in sys.modules:
        sys.modules[_name] = _stub

# main.py does `import torch.nn as nn; builtins.nn = nn` at module level.
# Pre-inject so the attribute is ready when main.py is imported.
import builtins
builtins.nn = _torch.nn

# ---------------------------------------------------------------------------
# 1. Env vars — must happen at module level, before any app import
# ---------------------------------------------------------------------------
os.environ.setdefault("DATABASE_URL",     "postgresql://test:test@localhost:5432/testdb")
os.environ.setdefault("QDRANT_HOST",      "localhost")
os.environ.setdefault("QDRANT_PORT",      "6333")
os.environ.setdefault("API_KEY_REQUIRED", "false")
# Force in-memory rate limiter for tests — overrides any .env or container env var.
# CELERY_BROKER_URL is read first by rate_limit.py; setting it to memory:// keeps
# all rate-limit counters in-process so tests never hit 429 due to Redis state.
os.environ["CELERY_BROKER_URL"] = "memory://"
os.environ["REDIS_URL"]         = "memory://"
os.environ.setdefault("CLIP_MODEL_NAME",  "clip-ViT-L-14")
os.environ.setdefault("QDRANT_COLLECTION_NAME", "media_vectors")
# Raise rate-limit ceilings so the full test suite never hits 429.
os.environ["RATE_LIMIT_ASK"]    = "10000/minute"
os.environ["RATE_LIMIT_SEARCH"] = "10000/minute"
os.environ["RATE_LIMIT_DEFAULT"] = "10000/minute"
# Provide a dummy key so ask.py's OpenAI client init doesn't raise RuntimeError.
os.environ.setdefault("OPENAI_API_KEY",   "test-key")
# Disable audit middleware DB writes — no PostgreSQL in the test environment.
os.environ.setdefault("AUDIT_ENABLED",    "false")

# ---------------------------------------------------------------------------
# 2. Ensure api/ is on sys.path so `from main import app` and `from routers…`
#    work regardless of where pytest is invoked from.
# ---------------------------------------------------------------------------
_API_DIR = os.path.dirname(os.path.dirname(__file__))  # …/api/
if _API_DIR not in sys.path:
    sys.path.insert(0, _API_DIR)

# Expose 'api' as a package rooted at api/ so that `from api.db.*` and
# similar absolute imports resolve correctly inside scaffolded modules.
import types as _types
if "api" not in sys.modules:
    _api_pkg = _types.ModuleType("api")
    _api_pkg.__path__ = [_API_DIR]
    _api_pkg.__package__ = "api"
    sys.modules["api"] = _api_pkg

# Stub api.models — MediaFile ORM class is not yet defined; downstream
# repository methods import it with `# type: ignore[import]`.
if "api.models" not in sys.modules:
    _api_models = _types.ModuleType("api.models")
    _api_models.MediaFile = MagicMock(name="MediaFile")
    sys.modules["api.models"] = _api_models

# Stub db.session — session factory module referenced by metadata_agent but
# not yet implemented in the scaffold.
if "db.session" not in sys.modules:
    _db_session_mod = _types.ModuleType("db.session")
    _db_session_mod.get_async_session_factory = MagicMock(name="get_async_session_factory")
    sys.modules["db.session"] = _db_session_mod


# ---------------------------------------------------------------------------
# 3. Shared mock objects (session-scoped = created once per test run)
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def mock_qdrant():
    """
    Mock QdrantClient used by health, search, and stats routers.

    Pre-configures the most common method return values so callers get
    sensible data without hitting a real Qdrant instance.
    """
    m = MagicMock(name="qdrant_client")

    # get_collections() — health + search-status endpoints
    col = MagicMock()
    col.name = "media_vectors"
    m.get_collections.return_value = MagicMock(collections=[col])

    # get_collection() — stats/summary qdrant drift check
    m.get_collection.return_value = MagicMock(points_count=942, vectors_count=942)

    # query_points() — /api/search with dedup=False
    m.query_points.return_value = MagicMock(points=[])

    # query_points_groups() — /api/search with dedup=True (default)
    # Returns a GroupsResult with an empty groups list by default.
    # Individual tests override this for dedup-specific assertions.
    m.query_points_groups.return_value = MagicMock(groups=[])

    # retrieve() — stats topic_tag sampling
    m.retrieve.return_value = []

    return m


@pytest.fixture(scope="session")
def mock_clip():
    """
    Mock SentenceTransformer CLIP model.

    encode() returns a random numpy array with the correct dimensionality:
      - single string  → 1-D float32 array  shape (768,)
      - list of strings → 2-D float32 array  shape (N, 768)
    This matches what both search.py and stats._compute_topic_tags() expect.
    """
    m = MagicMock(name="clip_model")

    def _encode(texts, **kwargs):
        if isinstance(texts, list):
            return np.random.rand(len(texts), 768).astype(np.float32)
        return np.random.rand(768).astype(np.float32)

    m.encode.side_effect = _encode
    return m


@pytest.fixture(scope="session")
def mock_llm():
    """
    Mock OpenAI client returned by ask.py's _get_llm_client().

    chat.completions.create() returns a response with a single choice whose
    message content is a deterministic string — lets tests assert the answer
    field without calling any real LLM.
    """
    m = MagicMock(name="llm_client")
    completion = MagicMock()
    completion.choices[0].message.content = "Mock LLM response for testing."
    m.chat.completions.create.return_value = completion
    return m


@pytest.fixture(scope="session")
def mock_db_session():
    """
    Mock SQLAlchemy Session-like object.

    Returns empty result sets by default; individual tests can override
    execute().fetchall() / fetchone() return values for richer assertions.
    """
    session = MagicMock(name="db_session")
    session.execute.return_value.fetchall.return_value = []
    session.execute.return_value.fetchone.return_value = (0, None, None, None)
    return session


# ---------------------------------------------------------------------------
# 4. TestClient fixture — patches all external deps, clears startup events
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def client(mock_qdrant, mock_clip, mock_db_session, mock_llm):
    """
    Session-scoped FastAPI TestClient with every external dependency mocked.

    Patches applied
    ~~~~~~~~~~~~~~~
    routers.search.qdrant_client   — module-level QdrantClient in search.py
    routers.health.qdrant_client   — module-level QdrantClient in health.py
    routers.search.get_clip_model  — lazy CLIP loader (also used by stats.py
                                     via `from routers.search import get_clip_model`)
    routers.stats._get_session     — DB session factory in stats.py
    routers.stats._get_qdrant      — Qdrant factory in stats.py
    routers.ingest._get_s3_client  — boto3 S3 client in ingest.py

    FastAPI startup events are cleared so the CLIP model is never preloaded
    during the test session.
    """
    from unittest.mock import patch

    # Submodules must be imported before patch() can resolve dotted paths like
    # "routers.search.qdrant_client". patch() looks up the attribute on the
    # already-imported module; it cannot auto-import subpackages.
    import routers.search  # noqa: F401
    import routers.health  # noqa: F401
    import routers.stats   # noqa: F401
    import routers.ingest  # noqa: F401
    import routers.ask     # noqa: F401
    import routers.detect  # noqa: F401

    mock_s3 = MagicMock(name="s3_client")

    # detect endpoint: return a canned payload so no YOLO model is loaded
    _detect_payload = {
        "yolo_labels":         ["person", "bicycle"],
        "yolo_detections":     [
            {"label": "person",  "confidence": 0.91, "bbox": [10.0, 20.0, 200.0, 400.0]},
            {"label": "bicycle", "confidence": 0.77, "bbox": [50.0, 80.0, 300.0, 350.0]},
        ],
        "yolo_object_count":   2,
        "yolo_model":          "yolov8n",
        "yolo_conf_threshold": 0.25,
    }
    mock_detect = MagicMock(name="detect_from_bytes", return_value=_detect_payload)

    with (
        patch("routers.detect.detect_from_bytes",  mock_detect),
        patch("routers.search.qdrant_client",    mock_qdrant),
        patch("routers.health.qdrant_client",    mock_qdrant),
        patch("routers.ask.qdrant_client",       mock_qdrant),
        patch("routers.search.get_clip_model",   return_value=mock_clip),
        patch("routers.ask._get_clip_model",     return_value=mock_clip),
        patch("routers.ask._get_llm_client",     return_value=mock_llm),
        patch("routers.stats._get_session",      return_value=mock_db_session),
        patch("routers.stats._get_qdrant",       return_value=mock_qdrant),
        patch("routers.ingest._get_s3_client",   return_value=mock_s3),
    ):
        from main import app  # imported here so patches are in effect

        # Clear startup events — prevents CLIP preload, removes all async setup
        app.router.on_startup.clear()

        from fastapi.testclient import TestClient
        yield TestClient(app)
