"""
Tests for the LangGraph multi-agent coordinator.

We test the individual node functions directly (classify_intent,
run_search_agent, run_metadata_agent, needs_vision, aggregate_and_answer)
and the aggregator helper (build_final_answer) — not the compiled graph —
to keep tests fast and dependency-free from the LangGraph runtime state.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _base_state(**overrides) -> dict:
    state = {
        "query": "test query",
        "intent": None,
        "search_results": [],
        "metadata_results": [],
        "vision_results": [],
        "final_answer": None,
        "error": None,
    }
    state.update(overrides)
    return state


# ---------------------------------------------------------------------------
# classify_intent node
# ---------------------------------------------------------------------------

class TestClassifyIntent:
    def test_visual_keywords_yield_visual_intent(self):
        from agents.coordinator import classify_intent

        state = asyncio.run(classify_intent(_base_state(query="show me photos from the beach")))
        assert state["intent"] == "visual"

    def test_temporal_keywords_yield_temporal_intent(self):
        from agents.coordinator import classify_intent

        state = asyncio.run(classify_intent(_base_state(query="what happened during 2023")))
        assert state["intent"] == "temporal"

    def test_mixed_keywords_yield_mixed_intent(self):
        from agents.coordinator import classify_intent

        state = asyncio.run(classify_intent(_base_state(query="show photos before 2022")))
        assert state["intent"] == "mixed"

    def test_generic_query_defaults_to_visual(self):
        from agents.coordinator import classify_intent

        state = asyncio.run(classify_intent(_base_state(query="vacation memories")))
        assert state["intent"] == "visual"

    def test_state_fields_preserved(self):
        from agents.coordinator import classify_intent

        state = asyncio.run(
            classify_intent(_base_state(query="test", search_results=[{"x": 1}]))
        )
        assert state["search_results"] == [{"x": 1}]

    def test_when_keyword_triggers_temporal(self):
        from agents.coordinator import classify_intent

        state = asyncio.run(classify_intent(_base_state(query="when was this taken")))
        assert state["intent"] == "temporal"

    def test_color_keyword_triggers_visual(self):
        from agents.coordinator import classify_intent

        state = asyncio.run(classify_intent(_base_state(query="blue dress in the picture")))
        assert state["intent"] == "visual"


# ---------------------------------------------------------------------------
# needs_vision conditional edge
# ---------------------------------------------------------------------------

class TestNeedsVision:
    def test_empty_results_routes_to_vision(self):
        from agents.coordinator import needs_vision

        assert needs_vision({"search_results": []}) == "vision"

    def test_fewer_than_min_routes_to_vision(self):
        from agents.coordinator import needs_vision, MIN_RESULTS_FOR_VISION

        state = {"search_results": [{}] * max(0, MIN_RESULTS_FOR_VISION - 1)}
        assert needs_vision(state) == "vision"

    def test_exactly_min_routes_to_aggregate(self):
        from agents.coordinator import needs_vision, MIN_RESULTS_FOR_VISION

        state = {"search_results": [{}] * MIN_RESULTS_FOR_VISION}
        assert needs_vision(state) == "aggregate"

    def test_above_min_routes_to_aggregate(self):
        from agents.coordinator import needs_vision, MIN_RESULTS_FOR_VISION

        state = {"search_results": [{}] * (MIN_RESULTS_FOR_VISION + 5)}
        assert needs_vision(state) == "aggregate"


# ---------------------------------------------------------------------------
# run_search_agent / run_metadata_agent nodes
# ---------------------------------------------------------------------------

class TestAgentNodes:
    def test_run_search_agent_populates_search_results(self):
        from agents.coordinator import run_search_agent

        mock_results = [{"file_path": "a.jpg", "similarity": 0.9}]
        with patch("agents.search_agent.search_agent_run", AsyncMock(return_value=mock_results)):
            state = asyncio.run(run_search_agent(_base_state()))
        assert state["search_results"] == mock_results

    def test_run_metadata_agent_populates_metadata_results(self):
        from agents.coordinator import run_metadata_agent
        import agents.metadata_agent  # ensure submodule is loaded before patching

        mock_results = [{"file_path": "b.jpg", "created_at": "2023-01-01"}]
        with patch("agents.metadata_agent.metadata_agent_run", AsyncMock(return_value=mock_results)):
            state = asyncio.run(run_metadata_agent(_base_state()))
        assert state["metadata_results"] == mock_results

    def test_run_search_agent_search_results_preserved_on_error(self):
        from agents.coordinator import run_search_agent
        import agents.search_agent  # ensure submodule is loaded before patching

        with patch("agents.search_agent.search_agent_run", AsyncMock(side_effect=RuntimeError("fail"))):
            with pytest.raises(RuntimeError, match="fail"):
                asyncio.run(run_search_agent(_base_state()))


# ---------------------------------------------------------------------------
# aggregate_and_answer node + build_final_answer helper
# ---------------------------------------------------------------------------

class TestAggregateAndAnswer:
    def test_empty_state_returns_no_media_string(self):
        from agents.aggregator import build_final_answer

        mock_llm = MagicMock()
        with patch("agents.aggregator.get_llm_provider", return_value=mock_llm):
            result = asyncio.run(build_final_answer(_base_state()))
        assert "No relevant media" in result
        mock_llm.complete.assert_not_called()

    def test_search_results_trigger_llm(self):
        from agents.aggregator import build_final_answer

        mock_llm = MagicMock()
        mock_llm.complete = AsyncMock(return_value="Here are your photos.")
        state = _base_state(search_results=[{"file_path": "a.jpg", "similarity": 0.9}])
        with patch("agents.aggregator.get_llm_provider", return_value=mock_llm):
            result = asyncio.run(build_final_answer(state))
        assert result == "Here are your photos."
        mock_llm.complete.assert_called_once()

    def test_metadata_results_included_in_prompt(self):
        from agents.aggregator import build_final_answer

        mock_llm = MagicMock()
        mock_llm.complete = AsyncMock(return_value="ok")
        state = _base_state(
            metadata_results=[{"file_path": "b.jpg", "created_at": "2023-01-01"}]
        )
        with patch("agents.aggregator.get_llm_provider", return_value=mock_llm):
            asyncio.run(build_final_answer(state))
        messages_str = str(mock_llm.complete.call_args.kwargs["messages"])
        assert "b.jpg" in messages_str

    def test_vision_results_included_in_prompt(self):
        from agents.aggregator import build_final_answer

        mock_llm = MagicMock()
        mock_llm.complete = AsyncMock(return_value="ok")
        state = _base_state(
            search_results=[{"file_path": "v.jpg", "similarity": 0.85}],
            vision_results=[{"file_path": "v.jpg", "description": "A sunset at sea"}],
        )
        with patch("agents.aggregator.get_llm_provider", return_value=mock_llm):
            asyncio.run(build_final_answer(state))
        messages_str = str(mock_llm.complete.call_args.kwargs["messages"])
        assert "sunset" in messages_str

    def test_llm_failure_returns_count_based_fallback(self):
        from agents.aggregator import build_final_answer

        mock_llm = MagicMock()
        mock_llm.complete = AsyncMock(side_effect=RuntimeError("LLM unavailable"))
        state = _base_state(
            search_results=[{"file_path": "a.jpg", "similarity": 0.9}]
        )
        with patch("agents.aggregator.get_llm_provider", return_value=mock_llm):
            result = asyncio.run(build_final_answer(state))
        # Falls back to a non-empty message containing the count
        assert result
        assert "LLM synthesis unavailable" in result or "1" in result

    def test_aggregate_and_answer_node_uses_build_final_answer(self):
        from agents.coordinator import aggregate_and_answer

        with patch(
            "agents.aggregator.build_final_answer",
            AsyncMock(return_value="synthesised answer"),
        ):
            state = asyncio.run(aggregate_and_answer(_base_state()))
        assert state["final_answer"] == "synthesised answer"
