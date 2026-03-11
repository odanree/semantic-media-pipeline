"""
Tests for the RAG pipeline orchestrator and every individual step.

All external ML dependencies (CLIP / CrossEncoder, Qdrant, LLM) are mocked —
no real models are loaded and no network calls are made.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np
import pytest

from rag.pipeline import RAGContext, RAGPipeline, RetrievedItem


# ---------------------------------------------------------------------------
# RAGContext
# ---------------------------------------------------------------------------

class TestRAGContext:
    def test_default_values(self):
        ctx = RAGContext(query="beach at sunset")
        assert ctx.query == "beach at sunset"
        assert ctx.expanded_query is None
        assert ctx.query_embedding is None
        assert ctx.retrieved == []
        assert ctx.reranked == []
        assert ctx.answer is None
        assert ctx.error is None
        assert ctx.limit == 10
        assert ctx.threshold == 0.2
        assert ctx.dedup is True
        assert ctx.collection == "media_vectors"
        assert ctx.metadata == {}


# ---------------------------------------------------------------------------
# RAGPipeline
# ---------------------------------------------------------------------------

class TestRAGPipeline:
    def test_executes_steps_in_order(self):
        order: list[str] = []

        class S1:
            async def run(self, ctx: RAGContext) -> RAGContext:
                order.append("s1")
                return ctx

        class S2:
            async def run(self, ctx: RAGContext) -> RAGContext:
                order.append("s2")
                return ctx

        asyncio.run(RAGPipeline([S1(), S2()]).execute(RAGContext(query="q")))
        assert order == ["s1", "s2"]

    def test_records_per_step_timings(self):
        class Step:
            async def run(self, ctx: RAGContext) -> RAGContext:
                return ctx

        ctx = asyncio.run(RAGPipeline([Step()]).execute(RAGContext(query="q")))
        assert "Step" in ctx.metadata["timings"]
        assert isinstance(ctx.metadata["timings"]["Step"], float)

    def test_short_circuits_when_error_set(self):
        ran: list[str] = []

        class Boom:
            async def run(self, ctx: RAGContext) -> RAGContext:
                ctx.error = "boom"
                return ctx

        class Never:
            async def run(self, ctx: RAGContext) -> RAGContext:
                ran.append("never")
                return ctx

        asyncio.run(RAGPipeline([Boom(), Never()]).execute(RAGContext(query="q")))
        assert ran == []

    def test_empty_pipeline_returns_context_unchanged(self):
        ctx = asyncio.run(RAGPipeline([]).execute(RAGContext(query="q")))
        assert ctx.error is None
        assert ctx.answer is None


# ---------------------------------------------------------------------------
# EmbedQueryStep
# ---------------------------------------------------------------------------

class TestEmbedQueryStep:
    def _clip(self):
        m = MagicMock(name="clip_model")
        m.encode.return_value = np.random.rand(768).astype(np.float32)
        return m

    def test_uses_expanded_query_when_set(self):
        from rag.steps.embed_query import EmbedQueryStep

        clip = self._clip()
        ctx = RAGContext(query="original", expanded_query="expanded richer text")
        asyncio.run(EmbedQueryStep(clip).run(ctx))
        clip.encode.assert_called_once_with("expanded richer text")

    def test_falls_back_to_original_query(self):
        from rag.steps.embed_query import EmbedQueryStep

        clip = self._clip()
        ctx = RAGContext(query="original")
        asyncio.run(EmbedQueryStep(clip).run(ctx))
        clip.encode.assert_called_once_with("original")

    def test_embedding_is_l2_normalised(self):
        from rag.steps.embed_query import EmbedQueryStep

        clip = self._clip()
        ctx = asyncio.run(EmbedQueryStep(clip).run(RAGContext(query="q")))
        norm = float(np.linalg.norm(ctx.query_embedding))
        assert abs(norm - 1.0) < 1e-5

    def test_model_failure_sets_error(self):
        from rag.steps.embed_query import EmbedQueryStep

        clip = MagicMock()
        clip.encode.side_effect = RuntimeError("GPU out of memory")
        ctx = asyncio.run(EmbedQueryStep(clip).run(RAGContext(query="q")))
        assert ctx.query_embedding is None
        assert "EmbedQueryStep failed" in ctx.error


# ---------------------------------------------------------------------------
# RerankerStep
# ---------------------------------------------------------------------------

class TestRerankerStep:
    def _items(self, *captions):
        return [
            RetrievedItem(
                file_path=f"f{i}.jpg",
                file_type="image",
                similarity=0.9 - i * 0.1,
                caption=c,
            )
            for i, c in enumerate(captions)
        ]

    def test_disabled_passes_retrieved_through(self):
        from rag.steps.reranker import RerankerStep

        items = self._items("a", "b")
        ctx = RAGContext(query="q", retrieved=items)
        ctx = asyncio.run(RerankerStep(enabled=False).run(ctx))
        assert ctx.reranked is items

    def test_empty_retrieved_sets_reranked_to_empty(self):
        from rag.steps.reranker import RerankerStep

        ctx = RAGContext(query="q", retrieved=[])
        ctx = asyncio.run(RerankerStep().run(ctx))
        assert ctx.reranked == []

    def test_no_captions_passes_through_without_scoring(self):
        from rag.steps.reranker import RerankerStep

        items = self._items(None, None)
        ctx = RAGContext(query="q", retrieved=items)
        ctx = asyncio.run(RerankerStep().run(ctx))
        assert ctx.reranked is items

    def test_happy_path_sorts_by_score_descending(self):
        from rag.steps.reranker import RerankerStep

        items = self._items("caption0", "caption1", "caption2")
        # Scores ascending by index → item[2] should come first after sort
        mock_encoder = MagicMock()
        mock_encoder.predict.return_value = [0.1, 0.5, 9.0]
        ctx = RAGContext(query="q", retrieved=items)
        with patch("rag.steps.reranker._get_cross_encoder", return_value=mock_encoder):
            ctx = asyncio.run(RerankerStep().run(ctx))
        scores = [i.rerank_score for i in ctx.reranked]
        assert scores == sorted(scores, reverse=True)
        assert ctx.reranked[0].file_path == "f2.jpg"

    def test_items_without_caption_receive_zero_score(self):
        from rag.steps.reranker import RerankerStep

        items = [
            RetrievedItem("a.jpg", "image", 0.9, caption="good caption"),
            RetrievedItem("b.jpg", "image", 0.8, caption=None),
        ]
        mock_encoder = MagicMock()
        mock_encoder.predict.return_value = [10.0]  # only one pair (a.jpg)
        ctx = RAGContext(query="q", retrieved=items)
        with patch("rag.steps.reranker._get_cross_encoder", return_value=mock_encoder):
            ctx = asyncio.run(RerankerStep().run(ctx))
        # a.jpg score 10.0 > b.jpg score 0.0 → a.jpg first
        assert ctx.reranked[0].file_path == "a.jpg"
        b_item = next(i for i in ctx.reranked if i.file_path == "b.jpg")
        assert b_item.rerank_score == 0.0

    def test_encoder_failure_falls_back_gracefully(self):
        from rag.steps.reranker import RerankerStep

        items = self._items("a", "b")
        ctx = RAGContext(query="q", retrieved=items)
        with patch("rag.steps.reranker._get_cross_encoder", side_effect=RuntimeError("no model")):
            ctx = asyncio.run(RerankerStep().run(ctx))
        # Non-fatal: falls back to original order, no error set
        assert ctx.reranked is items
        assert ctx.error is None


# ---------------------------------------------------------------------------
# QdrantRetrieveStep
# ---------------------------------------------------------------------------

class TestQdrantRetrieveStep:
    def _hit(self, file_path="img.jpg", file_type="image", score=0.9, timestamp=None):
        hit = MagicMock()
        hit.score = score
        hit.payload = {
            "file_path": file_path,
            "file_type": file_type,
            "caption": "test caption",
            "frame_index": None,
            "timestamp": timestamp,
        }
        return hit

    def test_missing_embedding_sets_error(self):
        from rag.steps.qdrant_retrieve import QdrantRetrieveStep

        ctx = asyncio.run(QdrantRetrieveStep(MagicMock()).run(RAGContext(query="q")))
        assert "no query embedding" in ctx.error
        assert ctx.retrieved == []

    def test_qdrant_failure_sets_error(self):
        from rag.steps.qdrant_retrieve import QdrantRetrieveStep

        qdrant = MagicMock()
        qdrant.search.side_effect = RuntimeError("connection refused")
        ctx = RAGContext(query="q", query_embedding=np.random.rand(768))
        ctx = asyncio.run(QdrantRetrieveStep(qdrant).run(ctx))
        assert "QdrantRetrieveStep failed" in ctx.error
        assert ctx.retrieved == []

    def test_happy_path_populates_retrieved(self):
        from rag.steps.qdrant_retrieve import QdrantRetrieveStep

        qdrant = MagicMock()
        qdrant.search.return_value = [self._hit("a.jpg"), self._hit("b.jpg")]
        ctx = RAGContext(query="q", query_embedding=np.random.rand(768), dedup=False)
        ctx = asyncio.run(QdrantRetrieveStep(qdrant).run(ctx))
        assert len(ctx.retrieved) == 2
        assert ctx.retrieved[0].file_path == "a.jpg"
        assert ctx.retrieved[0].caption == "test caption"
        assert ctx.error is None

    def test_respects_limit(self):
        from rag.steps.qdrant_retrieve import QdrantRetrieveStep

        qdrant = MagicMock()
        qdrant.search.return_value = [self._hit(f"{i}.jpg") for i in range(10)]
        ctx = RAGContext(query="q", query_embedding=np.random.rand(768), limit=3, dedup=False)
        ctx = asyncio.run(QdrantRetrieveStep(qdrant).run(ctx))
        assert len(ctx.retrieved) == 3

    def test_fetch_multiplier_applied_to_search_limit(self):
        from rag.steps.qdrant_retrieve import QdrantRetrieveStep

        qdrant = MagicMock()
        qdrant.search.return_value = []
        ctx = RAGContext(query="q", query_embedding=np.random.rand(768), limit=5, dedup=False)
        asyncio.run(QdrantRetrieveStep(qdrant, fetch_multiplier=4).run(ctx))
        called_limit = qdrant.search.call_args.kwargs.get("limit") or qdrant.search.call_args.args[2]
        # 5 * 4 = 20
        assert called_limit == 20


# ---------------------------------------------------------------------------
# _temporal_dedup
# ---------------------------------------------------------------------------

class TestTemporalDedup:
    def test_images_pass_through_unchanged(self):
        from rag.steps.qdrant_retrieve import _temporal_dedup

        items = [
            RetrievedItem("a.jpg", "image", 0.9),
            RetrievedItem("b.jpg", "image", 0.8),
        ]
        assert _temporal_dedup(items) == items

    def test_video_frames_within_window_deduped(self):
        from rag.steps.qdrant_retrieve import _temporal_dedup

        items = [
            RetrievedItem("v.mp4", "video", 0.9, timestamp=10.0),
            RetrievedItem("v.mp4", "video", 0.8, timestamp=12.0),  # 2s — within 5s window
        ]
        result = _temporal_dedup(items, window_secs=5.0)
        assert len(result) == 1
        assert result[0].timestamp == 10.0

    def test_video_frames_outside_window_both_kept(self):
        from rag.steps.qdrant_retrieve import _temporal_dedup

        items = [
            RetrievedItem("v.mp4", "video", 0.9, timestamp=0.0),
            RetrievedItem("v.mp4", "video", 0.8, timestamp=10.0),  # 10s > 5s window
        ]
        result = _temporal_dedup(items, window_secs=5.0)
        assert len(result) == 2

    def test_different_files_not_deduped(self):
        from rag.steps.qdrant_retrieve import _temporal_dedup

        items = [
            RetrievedItem("a.mp4", "video", 0.9, timestamp=1.0),
            RetrievedItem("b.mp4", "video", 0.8, timestamp=2.0),
        ]
        result = _temporal_dedup(items)
        assert len(result) == 2

    def test_video_without_timestamp_passes_through(self):
        from rag.steps.qdrant_retrieve import _temporal_dedup

        item = RetrievedItem("v.mp4", "video", 0.9, timestamp=None)
        result = _temporal_dedup([item])
        assert len(result) == 1


# ---------------------------------------------------------------------------
# QueryExpansionStep
# ---------------------------------------------------------------------------

class TestQueryExpansionStep:
    def test_sets_expanded_query_on_success(self):
        from rag.steps.query_expander import QueryExpansionStep

        llm = MagicMock()
        llm.complete = AsyncMock(return_value="  beach, sun, vacation  ")
        ctx = asyncio.run(
            QueryExpansionStep(llm).run(RAGContext(query="summer trip"))
        )
        # strip() is applied
        assert ctx.expanded_query == "beach, sun, vacation"
        assert ctx.error is None

    def test_llm_failure_falls_back_to_original_query(self):
        from rag.steps.query_expander import QueryExpansionStep

        llm = MagicMock()
        llm.complete = AsyncMock(side_effect=RuntimeError("LLM timeout"))
        ctx = asyncio.run(
            QueryExpansionStep(llm).run(RAGContext(query="my query"))
        )
        # Non-fatal: falls back, no error propagated
        assert ctx.expanded_query == "my query"
        assert ctx.error is None


# ---------------------------------------------------------------------------
# LLMGenerateStep
# ---------------------------------------------------------------------------

class TestLLMGenerateStep:
    def test_empty_retrieved_returns_no_media_message(self):
        from rag.steps.llm_generate import LLMGenerateStep

        llm = MagicMock()
        ctx = asyncio.run(LLMGenerateStep(llm).run(RAGContext(query="q")))
        assert "No relevant media" in ctx.answer
        llm.complete.assert_not_called()

    def test_prefers_reranked_over_retrieved(self):
        from rag.steps.llm_generate import LLMGenerateStep

        llm = MagicMock()
        llm.complete = AsyncMock(return_value="answer")
        retrieved = [RetrievedItem("x.jpg", "image", 0.5)]
        reranked = [RetrievedItem("r.jpg", "image", 0.9)]
        ctx = RAGContext(query="q", retrieved=retrieved, reranked=reranked)
        ctx = asyncio.run(LLMGenerateStep(llm).run(ctx))
        messages_str = str(llm.complete.call_args.kwargs["messages"])
        assert "r.jpg" in messages_str
        assert "x.jpg" not in messages_str

    def test_uses_retrieved_when_reranked_empty(self):
        from rag.steps.llm_generate import LLMGenerateStep

        llm = MagicMock()
        llm.complete = AsyncMock(return_value="from retrieved")
        ctx = RAGContext(
            query="q", retrieved=[RetrievedItem("x.jpg", "image", 0.5)]
        )
        ctx = asyncio.run(LLMGenerateStep(llm).run(ctx))
        assert ctx.answer == "from retrieved"
        messages_str = str(llm.complete.call_args.kwargs["messages"])
        assert "x.jpg" in messages_str

    def test_llm_failure_sets_context_error(self):
        from rag.steps.llm_generate import LLMGenerateStep

        llm = MagicMock()
        llm.complete = AsyncMock(side_effect=RuntimeError("LLM down"))
        ctx = RAGContext(query="q", retrieved=[RetrievedItem("x.jpg", "image", 0.5)])
        ctx = asyncio.run(LLMGenerateStep(llm).run(ctx))
        assert "LLMGenerateStep failed" in ctx.error
        assert ctx.answer is None

    def test_context_string_includes_optional_fields(self):
        from rag.steps.llm_generate import _build_context

        items = [
            RetrievedItem(
                file_path="photo.jpg",
                file_type="image",
                similarity=0.95,
                caption="A sunny beach",
                timestamp=12.5,
                rerank_score=1.23,
            )
        ]
        ctx_str = _build_context(items)
        assert "photo.jpg" in ctx_str
        assert "Caption" in ctx_str
        assert "beach" in ctx_str
        assert "Timestamp" in ctx_str
        assert "Relevance score" in ctx_str

    def test_context_string_omits_none_fields(self):
        from rag.steps.llm_generate import _build_context

        items = [RetrievedItem("x.jpg", "image", 0.8)]
        ctx_str = _build_context(items)
        assert "x.jpg" in ctx_str
        assert "Caption" not in ctx_str
        assert "Timestamp" not in ctx_str
        assert "Relevance score" not in ctx_str
