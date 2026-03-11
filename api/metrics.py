"""
Prometheus instrument registry — single source of truth for all metrics.

Import this module to get named instruments. Never create Counter/Histogram
instances in individual routers or tasks — always get them from here.

Usage:
    from metrics import METRICS
    METRICS.embedding_latency.observe(elapsed)
    METRICS.llm_tokens.labels(provider="openai", model="gpt-4o-mini").inc(token_count)
"""

from dataclasses import dataclass

from prometheus_client import Counter, Gauge, Histogram

_LATENCY_BUCKETS = (0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0)


@dataclass(frozen=True)
class LumenMetrics:
    # --- Worker: embedding/ingest ---
    embedding_latency: Histogram
    tasks_total: Counter
    frame_cache_hits: Counter

    # --- API: retrieval ---
    retrieval_latency: Histogram
    retrieval_results: Histogram

    # --- API: RAG / LLM ---
    llm_latency: Histogram
    llm_tokens: Counter

    # --- API: agent ---
    agent_steps: Counter
    agent_latency: Histogram

    # --- Queue depth (set by beat/monitor task if added) ---
    queue_depth: Gauge


def _build() -> LumenMetrics:
    return LumenMetrics(
        embedding_latency=Histogram(
            "lumen_embedding_latency_seconds",
            "CLIP inference wall time per file",
            ["model_version", "device"],
            buckets=_LATENCY_BUCKETS,
        ),
        tasks_total=Counter(
            "lumen_tasks_total",
            "Celery tasks processed",
            ["status", "media_type"],
        ),
        frame_cache_hits=Counter(
            "lumen_frame_cache_hits_total",
            "Video frames served from cache vs re-extracted",
            ["hit"],
        ),
        retrieval_latency=Histogram(
            "lumen_retrieval_latency_seconds",
            "Qdrant query wall time",
            ["endpoint"],
            buckets=_LATENCY_BUCKETS,
        ),
        retrieval_results=Histogram(
            "lumen_retrieval_results",
            "Number of results returned per query",
            ["endpoint"],
            buckets=(0, 1, 2, 5, 10, 20, 50),
        ),
        llm_latency=Histogram(
            "lumen_llm_latency_seconds",
            "LLM call wall time",
            ["provider", "model"],
            buckets=_LATENCY_BUCKETS,
        ),
        llm_tokens=Counter(
            "lumen_llm_tokens_total",
            "LLM tokens consumed",
            ["provider", "model", "direction"],  # direction: prompt | completion
        ),
        agent_steps=Counter(
            "lumen_agent_steps_total",
            "Agent node executions",
            ["node", "status"],
        ),
        agent_latency=Histogram(
            "lumen_agent_latency_seconds",
            "End-to-end agent query latency",
            buckets=_LATENCY_BUCKETS,
        ),
        queue_depth=Gauge(
            "lumen_queue_depth",
            "Approximate Celery task queue depth",
            ["queue"],
        ),
    )


# Single shared instance — import this everywhere
METRICS = _build()
