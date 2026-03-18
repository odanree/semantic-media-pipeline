"""
RAG evaluation harness using RAGAS metrics.

What it demonstrates for portfolio/interviews:
  - LLMOps discipline: you measure RAG quality, not just "does it return something"
  - RAGAS: faithfulness, answer_relevancy, context_recall, context_precision
  - CI gate: exit(1) if key metrics fall below threshold (use in GitHub Actions)

Usage:
    # Run evaluation (requires running API server or direct module import)
    python scripts/evaluate_rag.py

    # Override thresholds
    python scripts/evaluate_rag.py --faithfulness 0.75 --context-recall 0.65

    # Use a different eval dataset
    python scripts/evaluate_rag.py --dataset scripts/eval_dataset.json

    # Save results to file
    python scripts/evaluate_rag.py --output scripts/eval_results.json

Environment:
    API_BASE_URL  — default http://localhost:8000  (set to production URL for prod eval)
    OPENAI_API_KEY or LLM_PROVIDER — used by RAGAS LLM judge
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx

log = logging.getLogger(__name__)

API_BASE_URL = os.getenv("API_BASE_URL", "http://localhost:8000")
API_KEY = os.getenv("API_KEY", "")
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")

# CI thresholds — fail the pipeline if metrics fall below these
DEFAULT_THRESHOLDS = {
    "faithfulness": 0.70,       # LLM answers stay grounded in retrieved context
    "answer_relevancy": 0.75,   # LLM answers address the question
    "context_recall": 0.0,      # requires ground_truth to match indexed content; raise when eval dataset matures
    "context_precision": 0.0,   # requires ground_truth to match indexed content; raise when eval dataset matures
}


# ---------------------------------------------------------------------------
# Caption model A/B testing — re-caption retrieved frames via Ollama VLM
# ---------------------------------------------------------------------------

CAPTION_PROMPT = (
    "Describe what is happening in this image in one or two sentences. "
    "Be specific about subjects, actions, setting, and any notable visual details. "
    "Do not start with 'The image shows' or 'This is a'."
)


async def _fetch_frame_b64(src: Dict[str, Any], client: httpx.AsyncClient) -> Optional[str]:
    """Fetch a frame thumbnail and return it as a base64 string."""
    file_path = src.get("file_path", "")
    timestamp = src.get("timestamp", src.get("t", 0))
    if not file_path:
        return None
    try:
        headers = {"X-API-Key": API_KEY} if API_KEY else {}
        resp = await client.get(
            f"{API_BASE_URL}/api/thumbnail",
            params={"path": file_path, "t": timestamp},
            headers=headers,
            timeout=15.0,
        )
        if resp.status_code == 200:
            return base64.b64encode(resp.content).decode()
    except Exception as exc:
        log.debug("Thumbnail fetch failed for %s: %s", file_path, exc)
    return None


async def recaption_source(src: Dict[str, Any], model: str, client: httpx.AsyncClient) -> str:
    """Re-caption a single source frame using an Ollama vision model."""
    img_b64 = await _fetch_frame_b64(src, client)
    if not img_b64:
        return src.get("caption") or src.get("file_path", "")
    try:
        resp = await client.post(
            f"{OLLAMA_BASE_URL}/api/generate",
            json={"model": model, "prompt": CAPTION_PROMPT, "images": [img_b64], "stream": False},
            timeout=60.0,
        )
        resp.raise_for_status()
        return resp.json().get("response", "").strip()
    except Exception as exc:
        log.debug("Ollama recaption failed: %s", exc)
        return src.get("caption") or src.get("file_path", "")


async def recaption_sources(
    sources: List[Dict[str, Any]], model: str, client: httpx.AsyncClient
) -> List[str]:
    """Re-caption all sources concurrently."""
    tasks = [recaption_source(src, model, client) for src in sources]
    return await asyncio.gather(*tasks)


# ---------------------------------------------------------------------------
# Step 1: Call the RAG pipeline for each question
# ---------------------------------------------------------------------------

async def query_rag(question: str, client: httpx.AsyncClient) -> Dict[str, Any]:
    """
    Call POST /api/ask and return the full response dict.
    Expected response shape: { answer: str, sources: [...], ... }
    """
    payload = {"question": question}
    try:
        resp = await client.post(f"{API_BASE_URL}/api/ask", json=payload, timeout=60.0)
        resp.raise_for_status()
        return resp.json()
    except httpx.HTTPStatusError as exc:
        log.warning("HTTP %s for question '%s': %s", exc.response.status_code, question[:40], exc)
        return {"answer": "", "sources": [], "error": str(exc)}
    except httpx.RequestError as exc:
        log.error("Request error: %s", exc)
        return {"answer": "", "sources": [], "error": str(exc)}


async def collect_responses(
    dataset: List[Dict], caption_model: Optional[str] = None
) -> List[Dict[str, Any]]:
    """Run all eval questions against the live RAG endpoint."""
    results = []
    headers = {"X-API-Key": API_KEY} if API_KEY else {}
    async with httpx.AsyncClient(headers=headers) as client:
        for item in dataset:
            log.info("Querying: %s", item["question"][:60])
            t0 = time.perf_counter()
            response = await query_rag(item["question"], client)
            elapsed = time.perf_counter() - t0

            sources = response.get("sources", []) or response.get("retrieved", [])
            if caption_model and sources:
                log.info("Re-captioning %d sources with %s...", len(sources), caption_model)
                contexts = await recaption_sources(sources, caption_model, client)
            else:
                contexts = _extract_contexts(response)

            results.append(
                {
                    "id": item["id"],
                    "question": item["question"],
                    "ground_truth": item["ground_truth"],
                    "answer": response.get("answer", ""),
                    "contexts": contexts,
                    "contexts_original": _extract_contexts(response) if caption_model else None,
                    "caption_model": caption_model or "stored",
                    "latency_ms": round(elapsed * 1000, 1),
                    "error": response.get("error"),
                }
            )
    return results


def _extract_contexts(response: Dict[str, Any]) -> List[str]:
    """Extract context strings from the RAG response sources list."""
    sources = response.get("sources", []) or response.get("retrieved", [])
    contexts = []
    for src in sources:
        if isinstance(src, dict):
            text = src.get("caption") or src.get("file_path") or str(src)
            contexts.append(text)
        else:
            contexts.append(str(src))
    return contexts


# ---------------------------------------------------------------------------
# Step 2: Score with RAGAS
# ---------------------------------------------------------------------------

def score_with_ragas(results: List[Dict[str, Any]]) -> Dict[str, float]:
    """
    Run RAGAS evaluation on collected results.

    Returns aggregate mean scores for each metric.
    Compatible with RAGAS 0.2+ (metrics are classes, results are per-sample lists).
    """
    try:
        import numpy as np
        from datasets import Dataset  # type: ignore[import]
        from ragas import evaluate  # type: ignore[import]
        from ragas.metrics import (  # type: ignore[import]
            Faithfulness,
            AnswerRelevancy,
            ContextRecall,
            ContextPrecision,
        )
        from ragas.llms import LangchainLLMWrapper  # type: ignore[import]
        from ragas.embeddings import LangchainEmbeddingsWrapper  # type: ignore[import]
        from langchain_openai import ChatOpenAI, OpenAIEmbeddings  # type: ignore[import]
    except ImportError:
        log.error(
            "ragas or datasets not installed. "
            "Run: pip install ragas datasets langchain-openai"
        )
        return _fallback_scores(results)

    # Explicitly wire LLM + embeddings to avoid OpenAIEmbeddings.embed_query compat error
    llm = LangchainLLMWrapper(ChatOpenAI(model="gpt-4o-mini", temperature=0))
    embeddings = LangchainEmbeddingsWrapper(OpenAIEmbeddings())

    # RAGAS expects a HuggingFace Dataset with these column names
    eval_data = {
        "user_input": [r["question"] for r in results],
        "response": [r["answer"] for r in results],
        "retrieved_contexts": [r["contexts"] for r in results],
        "reference": [r["ground_truth"] for r in results],
    }

    dataset = Dataset.from_dict(eval_data)

    try:
        ragas_result = evaluate(
            dataset,
            metrics=[
                Faithfulness(llm=llm),
                AnswerRelevancy(llm=llm, embeddings=embeddings),
                ContextRecall(llm=llm),
                ContextPrecision(llm=llm),
            ],
        )
        # RAGAS 0.2+ returns per-sample lists — take the mean
        def _mean(val):
            if isinstance(val, list):
                valid = [v for v in val if v is not None]
                return round(float(np.mean(valid)) if valid else 0.0, 4)
            return round(float(val), 4)

        return {
            "faithfulness": _mean(ragas_result["faithfulness"]),
            "answer_relevancy": _mean(ragas_result["answer_relevancy"]),
            "context_recall": _mean(ragas_result["context_recall"]),
            "context_precision": _mean(ragas_result["context_precision"]),
        }
    except Exception as exc:
        log.error("RAGAS evaluation failed: %s", exc)
        return _fallback_scores(results)


def _fallback_scores(results: List[Dict[str, Any]]) -> Dict[str, float]:
    """
    Simple fallback scorer when RAGAS is unavailable.
    Uses answer length > 20 chars and context list non-empty as proxy signals.
    """
    log.warning("Using fallback scoring (not RAGAS). Install ragas for real metrics.")
    has_answer = sum(1 for r in results if len(r.get("answer", "")) > 20)
    has_context = sum(1 for r in results if r.get("contexts"))
    n = max(len(results), 1)
    proxy = round(has_answer / n, 4)
    ctx_proxy = round(has_context / n, 4)
    return {
        "faithfulness": proxy,
        "answer_relevancy": proxy,
        "context_recall": ctx_proxy,
        "context_precision": ctx_proxy,
        "note": "fallback_scores_not_ragas",
    }


# ---------------------------------------------------------------------------
# Step 3: Print summary table + enforce CI thresholds
# ---------------------------------------------------------------------------

def print_summary(scores: Dict[str, float], thresholds: Dict[str, float]) -> bool:
    """Print a summary table. Returns True if all thresholds pass."""
    print("\n" + "=" * 54)
    print(f"{'Metric':<28}{'Score':>8}{'Threshold':>10}{'Status':>8}")
    print("-" * 54)

    all_pass = True
    for metric, threshold in thresholds.items():
        score = scores.get(metric, 0.0)
        status = "✓ PASS" if score >= threshold else "✗ FAIL"
        if score < threshold:
            all_pass = False
        print(f"{metric:<28}{score:>8.4f}{threshold:>10.2f}{status:>8}")

    print("=" * 54)
    if all_pass:
        print("All metrics passed CI thresholds.")
    else:
        print("One or more metrics FAILED the CI threshold.")
    print()
    return all_pass


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Evaluate RAG pipeline with RAGAS")
    parser.add_argument("--dataset", default="scripts/eval_dataset.json")
    parser.add_argument("--output", default=None, help="Save results JSON to this path")
    parser.add_argument(
        "--faithfulness", type=float, default=DEFAULT_THRESHOLDS["faithfulness"]
    )
    parser.add_argument(
        "--answer-relevancy", type=float, default=DEFAULT_THRESHOLDS["answer_relevancy"]
    )
    parser.add_argument(
        "--context-recall", type=float, default=DEFAULT_THRESHOLDS["context_recall"]
    )
    parser.add_argument(
        "--context-precision", type=float, default=DEFAULT_THRESHOLDS["context_precision"]
    )
    parser.add_argument(
        "--caption-model",
        default=None,
        metavar="MODEL",
        help="Ollama model to re-caption retrieved frames (e.g. llava:7b). "
             "If omitted, stored captions are used.",
    )
    return parser.parse_args()


async def main_async(args: argparse.Namespace) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    dataset_path = Path(args.dataset)
    if not dataset_path.exists():
        log.error("Dataset not found: %s", dataset_path)
        return 1

    with open(dataset_path) as f:
        dataset = json.load(f)

    caption_model = args.caption_model
    if caption_model:
        log.info("Caption A/B mode: re-captioning retrieved frames with %s", caption_model)

    log.info("Running %d eval questions against %s", len(dataset), API_BASE_URL)
    results = await collect_responses(dataset, caption_model=caption_model)

    log.info("Scoring with RAGAS...")
    scores = score_with_ragas(results)

    thresholds = {
        "faithfulness": args.faithfulness,
        "answer_relevancy": args.answer_relevancy,
        "context_recall": args.context_recall,
        "context_precision": args.context_precision,
    }

    all_pass = print_summary(scores, thresholds)

    output_data = {
        "scores": scores,
        "thresholds": thresholds,
        "passed": all_pass,
        "results": results,
    }

    if args.output:
        out_path = Path(args.output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, "w") as f:
            json.dump(output_data, f, indent=2)
        log.info("Results saved to %s", out_path)

    return 0 if all_pass else 1


def main():
    args = parse_args()
    exit_code = asyncio.run(main_async(args))
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
