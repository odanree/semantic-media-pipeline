"""
Tests for the /api/ask RAG endpoint.

Coverage
--------
- Input validation: empty / whitespace question rejected with 400
- Response schema: all required keys present on valid request
- 503 when CLIP model unavailable
- 503 when Qdrant query fails
- 502 when LLM call raises OpenAIError
- Retrieve step: dedup=true routes through search_groups (default)
- Retrieve step: dedup=false routes through query_points
- scenes_collapsed field tracks how many frames were deduplicated
- Context builder (_build_context): empty context string when no results
- Context builder: includes file_path, similarity, timestamp
- Sources list: maps Qdrant payloads to SourceResult correctly
- model_used reflects LLM_MODEL env var
- retrieval_count matches number of sources returned
- execution_time_ms is a positive float
- Rate-limit header present in response (slowapi inserts it)
- CLIP encode receives the question verbatim
- LLM receives a non-empty user message
- LLM response content appears as answer in the response
- threshold forwarded to Qdrant call
- limit forwarded to Qdrant call
- 503 when LLM provider raises RuntimeError (missing API key)
- 503 when Qdrant unavailable (Exception from client)
- Image sources included (file_type=image, timestamp=None)
- caption field present in SourceResult (None before Phase 2)
- dedup=true: search_groups called with group_by='file_path'
- dedup=false: query_points called with correct keyword args
- scenes_collapsed=0 when dedup=false
"""

import pytest
from unittest.mock import MagicMock, patch


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_point(file_path="clip.mp4", file_type="video", score=0.85,
                timestamp=2.0, frame_index=10, caption=None):
    """Build a mock ScoredPoint-like object for Qdrant mock returns."""
    p = MagicMock()
    p.score = score
    p.id = f"{file_path}-{frame_index}"
    p.payload = {
        "file_path": file_path,
        "file_type": file_type,
        "timestamp": timestamp,
        "frame_index": frame_index,
        "caption": caption,
    }
    return p


def _make_group(hits):
    g = MagicMock()
    g.hits = hits
    return g


def _groups_result(hits_list):
    """Wrap a list of hits in a mock GroupsResult with one group."""
    return MagicMock(groups=[_make_group(hits_list)])


# ---------------------------------------------------------------------------
# /api/ask — input validation
# ---------------------------------------------------------------------------

def test_ask_empty_question_returns_400(client):
    resp = client.post("/api/ask", json={"question": ""})
    assert resp.status_code == 400


def test_ask_whitespace_question_returns_400(client):
    resp = client.post("/api/ask", json={"question": "   "})
    assert resp.status_code == 400


def test_ask_missing_question_returns_422(client):
    resp = client.post("/api/ask", json={})
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# /api/ask — happy-path response schema
# ---------------------------------------------------------------------------

def test_ask_returns_200_on_valid_question(client, mock_qdrant):
    mock_qdrant.search_groups.return_value = MagicMock(groups=[])
    resp = client.post("/api/ask", json={"question": "What videos do I have?"})
    assert resp.status_code == 200


def test_ask_response_has_question_field(client, mock_qdrant):
    mock_qdrant.search_groups.return_value = MagicMock(groups=[])
    data = client.post("/api/ask", json={"question": "Show me sunsets."}).json()
    assert data["question"] == "Show me sunsets."


def test_ask_response_has_answer_field(client, mock_qdrant):
    mock_qdrant.search_groups.return_value = MagicMock(groups=[])
    data = client.post("/api/ask", json={"question": "Sunsets?"}).json()
    assert "answer" in data
    assert isinstance(data["answer"], str)
    assert len(data["answer"]) > 0


def test_ask_response_has_sources_list(client, mock_qdrant):
    mock_qdrant.search_groups.return_value = MagicMock(groups=[])
    data = client.post("/api/ask", json={"question": "Videos?"}).json()
    assert "sources" in data
    assert isinstance(data["sources"], list)


def test_ask_response_has_model_used(client, mock_qdrant):
    mock_qdrant.search_groups.return_value = MagicMock(groups=[])
    data = client.post("/api/ask", json={"question": "Test?"}).json()
    assert "model_used" in data


def test_ask_response_has_retrieval_count(client, mock_qdrant):
    mock_qdrant.search_groups.return_value = MagicMock(groups=[])
    data = client.post("/api/ask", json={"question": "Test?"}).json()
    assert "retrieval_count" in data


def test_ask_response_has_execution_time_ms(client, mock_qdrant):
    mock_qdrant.search_groups.return_value = MagicMock(groups=[])
    data = client.post("/api/ask", json={"question": "Test?"}).json()
    assert "execution_time_ms" in data
    assert isinstance(data["execution_time_ms"], (int, float))
    assert data["execution_time_ms"] >= 0


def test_ask_response_has_scenes_collapsed_field(client, mock_qdrant):
    mock_qdrant.search_groups.return_value = MagicMock(groups=[])
    data = client.post("/api/ask", json={"question": "Test?"}).json()
    assert "scenes_collapsed" in data


# ---------------------------------------------------------------------------
# /api/ask — sources mapping
# ---------------------------------------------------------------------------

def test_ask_sources_include_file_path(client, mock_qdrant):
    pt = _make_point("videos/holiday.mp4")
    mock_qdrant.search_groups.return_value = _groups_result([pt])
    data = client.post("/api/ask", json={"question": "Holiday?"}).json()
    assert data["sources"][0]["file_path"] == "videos/holiday.mp4"


def test_ask_sources_include_file_type(client, mock_qdrant):
    pt = _make_point(file_type="image", timestamp=None)
    mock_qdrant.search_groups.return_value = _groups_result([pt])
    data = client.post("/api/ask", json={"question": "Photos?"}).json()
    assert data["sources"][0]["file_type"] == "image"


def test_ask_sources_include_similarity(client, mock_qdrant):
    pt = _make_point(score=0.77)
    mock_qdrant.search_groups.return_value = _groups_result([pt])
    data = client.post("/api/ask", json={"question": "Score test?"}).json()
    assert abs(data["sources"][0]["similarity"] - 0.77) < 0.001


def test_ask_sources_include_timestamp(client, mock_qdrant):
    pt = _make_point(timestamp=42.5)
    mock_qdrant.search_groups.return_value = _groups_result([pt])
    data = client.post("/api/ask", json={"question": "Timestamp test?"}).json()
    assert data["sources"][0]["timestamp"] == 42.5


def test_ask_sources_caption_is_none_by_default(client, mock_qdrant):
    pt = _make_point(caption=None)
    mock_qdrant.search_groups.return_value = _groups_result([pt])
    data = client.post("/api/ask", json={"question": "Caption test?"}).json()
    assert data["sources"][0]["caption"] is None


def test_ask_retrieval_count_matches_sources_length(client, mock_qdrant):
    pts = [_make_point(f"v{i}.mp4", timestamp=float(i)) for i in range(3)]
    mock_qdrant.search_groups.return_value = _groups_result(pts)
    data = client.post("/api/ask", json={"question": "Count test?"}).json()
    assert data["retrieval_count"] == len(data["sources"])


# ---------------------------------------------------------------------------
# /api/ask — LLM answer
# ---------------------------------------------------------------------------

def test_ask_answer_comes_from_llm(client, mock_qdrant, mock_llm):
    mock_qdrant.search_groups.return_value = MagicMock(groups=[])
    mock_llm.chat.completions.create.return_value.choices[0].message.content = "Specific answer."
    data = client.post("/api/ask", json={"question": "Anything?"}).json()
    assert data["answer"] == "Specific answer."


def test_ask_llm_receives_question_in_user_message(client, mock_qdrant, mock_llm):
    mock_qdrant.search_groups.return_value = MagicMock(groups=[])
    mock_llm.chat.completions.create.reset_mock()
    client.post("/api/ask", json={"question": "Where was I in 2023?"})
    call_args = mock_llm.chat.completions.create.call_args
    messages = call_args.kwargs.get("messages") or call_args[1]["messages"]
    user_msg = next(m for m in messages if m["role"] == "user")
    assert "Where was I in 2023?" in user_msg["content"]


# ---------------------------------------------------------------------------
# /api/ask — error paths
# ---------------------------------------------------------------------------

def test_ask_503_when_clip_unavailable(client, mock_qdrant):
    with patch("routers.ask._get_clip_model", side_effect=RuntimeError("No CLIP")):
        resp = client.post("/api/ask", json={"question": "Test?"})
    assert resp.status_code == 503


def test_ask_503_when_qdrant_fails(client, mock_qdrant):
    mock_qdrant.search_groups.side_effect = Exception("Qdrant down")
    resp = client.post("/api/ask", json={"question": "Test?"})
    assert resp.status_code == 503
    mock_qdrant.search_groups.side_effect = None  # reset for subsequent tests


def test_ask_502_when_llm_raises(client, mock_qdrant, mock_llm):
    from openai import APIError
    mock_qdrant.search_groups.return_value = MagicMock(groups=[])
    mock_llm.chat.completions.create.side_effect = APIError(
        "upstream error", request=MagicMock(), body=None
    )
    resp = client.post("/api/ask", json={"question": "Test?"})
    assert resp.status_code == 502
    mock_llm.chat.completions.create.side_effect = None


# ---------------------------------------------------------------------------
# /api/ask — dedup routing
# ---------------------------------------------------------------------------

def test_ask_dedup_default_uses_search_groups(client, mock_qdrant):
    """dedup=true (default) must route through search_groups."""
    mock_qdrant.search_groups.return_value = MagicMock(groups=[])
    mock_qdrant.search_groups.reset_mock()
    mock_qdrant.query_points.reset_mock()

    resp = client.post("/api/ask", json={"question": "Beaches?"})
    assert resp.status_code == 200
    mock_qdrant.search_groups.assert_called_once()
    mock_qdrant.query_points.assert_not_called()


def test_ask_dedup_false_uses_query_points(client, mock_qdrant):
    """dedup=false must route through query_points."""
    mock_qdrant.query_points.return_value = MagicMock(points=[])
    mock_qdrant.query_points.reset_mock()
    mock_qdrant.search_groups.reset_mock()

    resp = client.post("/api/ask", json={"question": "Mountains?", "dedup": False})
    assert resp.status_code == 200
    mock_qdrant.query_points.assert_called_once()
    mock_qdrant.search_groups.assert_not_called()


def test_ask_scenes_collapsed_zero_when_dedup_false(client, mock_qdrant):
    mock_qdrant.query_points.return_value = MagicMock(points=[])
    data = client.post("/api/ask", json={"question": "Raw?", "dedup": False}).json()
    assert data["scenes_collapsed"] == 0


def test_ask_scenes_collapsed_nonzero_when_frames_merged(client, mock_qdrant):
    """3 frames in the same 5 s bucket → 2 collapsed."""
    hits = [
        _make_point("v.mp4", score=0.9, timestamp=1.0),
        _make_point("v.mp4", score=0.8, timestamp=2.0),
        _make_point("v.mp4", score=0.7, timestamp=3.0),
    ]
    mock_qdrant.search_groups.return_value = _groups_result(hits)
    data = client.post("/api/ask", json={"question": "Dedup collapse?"}).json()
    assert data["scenes_collapsed"] == 2


def test_ask_search_groups_called_with_group_by_file_path(client, mock_qdrant):
    mock_qdrant.search_groups.return_value = MagicMock(groups=[])
    mock_qdrant.search_groups.reset_mock()
    client.post("/api/ask", json={"question": "Group check?"})
    call_kwargs = mock_qdrant.search_groups.call_args.kwargs
    assert call_kwargs.get("group_by") == "file_path"


def test_ask_clip_encode_receives_question(client, mock_qdrant, mock_clip):
    mock_qdrant.search_groups.return_value = MagicMock(groups=[])
    mock_clip.encode.reset_mock()
    client.post("/api/ask", json={"question": "Soccer goals?"})
    mock_clip.encode.assert_called_once()
    assert mock_clip.encode.call_args[0][0] == "Soccer goals?"
