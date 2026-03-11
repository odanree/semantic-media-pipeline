"""
Retrieval quality evaluator — measures the Signal-to-Noise Ratio (SNR)
of the CLIP similarity search layer independently of the LLM generation layer.

Why this matters:
  - evaluate_rag.py measures LLM answer quality (faithfulness, relevancy).
  - THIS script measures whether the *retrieval* step is separating signal
    from noise at the vector level.
  - A high-RAGAS score on top of noisy retrieval is a false positive. This
    script catches retrieval degradation before it pollutes the LLM.

Metrics computed:
  SNR          — mean(top-k scores) / std(top-k scores)
                 High SNR = tight cluster of confident hits (signal dominates).
                 Low SNR  = wide, noisy score spread.

  Separation   — mean(returned scores) - threshold
                 How far above the noise floor the average result sits.
                 < 0.05 → retrieval is barely beating the cutoff.

  Score density per confidence band:
    weak    [threshold, 0.30)
    fair    [0.30, 0.40)
    strong  [0.40, 0.50)
    exact   [0.50, 1.00]

  Hit rate     — fraction of queries that return ≥ 1 result.
                 Failure here means the index is empty or threshold is too high.

  Paraphrase consistency (self-supervised):
    Each query is reworded and re-submitted. The Jaccard similarity of the two
    returned file-path sets is the consistency score. A stable semantic index
    should return largely the same results for equivalent queries.
    No ground-truth annotations required.

CI gates (configurable with --*):
  --min-snr         default 2.0   — fail if overall SNR is below this
  --min-separation  default 0.05  — fail if avg separation is below this
  --min-hit-rate    default 0.50  — fail if fewer than half queries return hits

Usage:
    python scripts/evaluate_retrieval.py
    python scripts/evaluate_retrieval.py --threshold 0.22 --limit 10
    python scripts/evaluate_retrieval.py --output scripts/retrieval_results.json
    python scripts/evaluate_retrieval.py --min-snr 1.5 --min-separation 0.03

Environment:
    API_BASE_URL   default http://localhost:8000
    API_KEY        forwarded as X-API-Key header if set
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import math
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import httpx

log = logging.getLogger(__name__)

API_BASE_URL = os.getenv("API_BASE_URL", "http://localhost:8000")
_API_KEY = os.getenv("API_KEY", "")

# Confidence bands for score density reporting
_BANDS = [
    ("exact",  0.50, 1.01),
    ("strong", 0.40, 0.50),
    ("fair",   0.30, 0.40),
]

# Simple paraphrase map — no LLM needed; hand-written synonyms are reproducible
# and deterministic, which is what you want in a CI gate.
_PARAPHRASES: Dict[str, str] = {
    "Show me videos recorded during sunset at the beach":
        "Beach videos shot at dusk with warm golden light",
    "Find images with a lot of text like signs or documents":
        "Photos containing written text, labels, or signage",
    "Find clips with high audio energy and speech":
        "Videos with loud dialogue and energetic sound",
    "Find blue-dominant images with low brightness":
        "Dark photos with mostly blue tones",
    "Find videos where someone is speaking outdoors":
        "Outdoor footage where a person is talking",
}


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

def _headers() -> Dict[str, str]:
    h = {"Content-Type": "application/json"}
    if _API_KEY:
        h["X-API-Key"] = _API_KEY
    return h


async def _search(
    query: str,
    client: httpx.AsyncClient,
    threshold: float,
    limit: int,
) -> List[Dict[str, Any]]:
    """Call POST /api/search and return the results list."""
    payload = {"query": query, "threshold": threshold, "limit": limit}
    try:
        resp = await client.post(
            f"{API_BASE_URL}/api/search",
            json=payload,
            headers=_headers(),
            timeout=30.0,
        )
        resp.raise_for_status()
        data = resp.json()
        # SearchResponse wraps results in a "results" key
        return data.get("results", data) if isinstance(data, dict) else data
    except httpx.HTTPStatusError as exc:
        log.warning("HTTP %s searching '%s': %s", exc.response.status_code, query[:40], exc)
        return []
    except httpx.RequestError as exc:
        log.error("Request error for '%s': %s", query[:40], exc)
        return []


# ---------------------------------------------------------------------------
# Per-query metrics
# ---------------------------------------------------------------------------

def _compute_query_metrics(
    results: List[Dict[str, Any]],
    threshold: float,
) -> Dict[str, Any]:
    """
    Compute SNR and distribution metrics for a single query's result set.

    SNR definition used here:
        SNR = mean(scores) / std(scores)  if std > 0
            = mean(scores) / ε            if all scores are identical

    Rationale: in a well-calibrated index, relevant results have tight,
    high scores (high mean, low std → high SNR). A noisy retrieval spreads
    scores widely across the 0-similarity spectrum (low mean, high std →
    low SNR). This mirrors signal processing SNR = signal_power / noise_power
    where std² is variance (power of the noise component).
    """
    if not results:
        return {
            "hit_count": 0,
            "mean_score": 0.0,
            "std_score": 0.0,
            "min_score": 0.0,
            "max_score": 0.0,
            "snr": 0.0,
            "separation": 0.0,
            "band_exact": 0,
            "band_strong": 0,
            "band_fair": 0,
            "band_weak": 0,
        }

    scores = [r["similarity"] for r in results]
    mean_s = sum(scores) / len(scores)
    variance = sum((s - mean_s) ** 2 for s in scores) / len(scores)
    std_s = math.sqrt(variance)
    snr = mean_s / (std_s if std_s > 1e-6 else 1e-6)

    bands = {name: sum(1 for s in scores if lo <= s < hi) for name, lo, hi in _BANDS}
    bands["weak"] = sum(1 for s in scores if threshold <= s < 0.30)

    return {
        "hit_count": len(scores),
        "mean_score": round(mean_s, 4),
        "std_score": round(std_s, 4),
        "min_score": round(min(scores), 4),
        "max_score": round(max(scores), 4),
        "snr": round(snr, 3),
        "separation": round(mean_s - threshold, 4),
        "band_exact": bands["exact"],
        "band_strong": bands["strong"],
        "band_fair": bands["fair"],
        "band_weak": bands["weak"],
    }


def _jaccard(set_a: set, set_b: set) -> float:
    if not set_a and not set_b:
        return 1.0
    union = set_a | set_b
    return len(set_a & set_b) / len(union)


# ---------------------------------------------------------------------------
# Full evaluation pass
# ---------------------------------------------------------------------------

async def run_evaluation(
    dataset: List[Dict],
    threshold: float,
    limit: int,
) -> List[Dict[str, Any]]:
    records = []
    async with httpx.AsyncClient() as client:
        for item in dataset:
            q = item["question"]
            log.info("Querying: %s", q[:60])

            t0 = time.perf_counter()
            results = await _search(q, client, threshold, limit)
            latency_ms = (time.perf_counter() - t0) * 1000

            metrics = _compute_query_metrics(results, threshold)

            # Paraphrase consistency (self-supervised — no ground truth needed)
            paraphrase = _PARAPHRASES.get(q)
            consistency: Optional[float] = None
            if paraphrase:
                para_results = await _search(paraphrase, client, threshold, limit)
                paths_orig = {r["file_path"] for r in results if "file_path" in r}
                paths_para = {r["file_path"] for r in para_results if "file_path" in r}
                consistency = round(_jaccard(paths_orig, paths_para), 3)

            records.append({
                "id": item["id"],
                "question": q,
                "latency_ms": round(latency_ms, 1),
                "paraphrase_consistency": consistency,
                **metrics,
            })

    return records


# ---------------------------------------------------------------------------
# Aggregate + CI gate
# ---------------------------------------------------------------------------

def _aggregate(records: List[Dict]) -> Dict[str, float]:
    n = len(records)
    if n == 0:
        return {}

    hit_rate = sum(1 for r in records if r["hit_count"] > 0) / n
    mean_snr = sum(r["snr"] for r in records) / n
    mean_sep = sum(r["separation"] for r in records) / n
    mean_score = sum(r["mean_score"] for r in records) / n
    mean_latency = sum(r["latency_ms"] for r in records) / n

    consistency_vals = [r["paraphrase_consistency"] for r in records if r["paraphrase_consistency"] is not None]
    mean_consistency = sum(consistency_vals) / len(consistency_vals) if consistency_vals else None

    return {
        "hit_rate": round(hit_rate, 3),
        "mean_snr": round(mean_snr, 3),
        "mean_separation": round(mean_sep, 4),
        "mean_score": round(mean_score, 4),
        "mean_latency_ms": round(mean_latency, 1),
        "mean_paraphrase_consistency": round(mean_consistency, 3) if mean_consistency is not None else None,
        "n_queries": n,
    }


def print_summary(
    records: List[Dict],
    agg: Dict[str, float],
    thresholds: Dict[str, float],
    retrieval_threshold: float,
) -> bool:
    print(f"\n{'='*66}")
    print(f"{'Query':<36} {'Hits':>4} {'Mean':>6} {'SNR':>6} {'Sep':>6} {'Cons':>6}")
    print(f"{'-'*66}")
    for r in records:
        cons = f"{r['paraphrase_consistency']:.2f}" if r["paraphrase_consistency"] is not None else "  n/a"
        print(
            f"{r['question'][:35]:<36} {r['hit_count']:>4} "
            f"{r['mean_score']:>6.3f} {r['snr']:>6.2f} {r['separation']:>6.3f} {cons:>6}"
        )

    print(f"\n{'='*66}")
    print(f"AGGREGATE  (threshold={retrieval_threshold}, n={agg['n_queries']})")
    print(f"  Hit rate:              {agg['hit_rate']:.1%}")
    print(f"  Mean similarity:       {agg['mean_score']:.4f}")
    print(f"  Mean SNR:              {agg['mean_snr']:.3f}")
    print(f"  Mean separation:       {agg['mean_separation']:.4f}  (above noise floor)")
    print(f"  Mean latency:          {agg['mean_latency_ms']:.0f} ms")
    if agg.get("mean_paraphrase_consistency") is not None:
        print(f"  Paraphrase consistency: {agg['mean_paraphrase_consistency']:.3f}  (Jaccard, 0–1)")

    score_note = (
        "  SNR interpretation: >5 = tight/precise, 2-5 = acceptable, "
        "<2 = noisy retrieval\n"
        "  Separation: how far avg score sits above the similarity threshold.\n"
        "  Paraphrase consistency: same results returned for semantically equivalent queries."
    )
    print(f"\n{score_note}")

    # CI gate
    gate_map = {
        "mean_snr":        ("min_snr",        "Mean SNR"),
        "mean_separation": ("min_separation", "Mean separation"),
        "hit_rate":        ("min_hit_rate",   "Hit rate"),
    }
    print(f"\n{'='*66}")
    print(f"{'Gate':<28} {'Value':>8} {'Threshold':>10} {'Status':>8}")
    print(f"{'-'*66}")
    all_pass = True
    for metric_key, (threshold_key, label) in gate_map.items():
        value = agg.get(metric_key, 0.0)
        gate = thresholds[threshold_key]
        ok = value >= gate
        if not ok:
            all_pass = False
        print(f"{label:<28} {value:>8.3f} {gate:>10.2f} {'✓ PASS' if ok else '✗ FAIL':>8}")
    print(f"{'='*66}")
    if all_pass:
        print("All retrieval CI gates passed.")
    else:
        print("One or more retrieval CI gates FAILED.")
    print()
    return all_pass


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Evaluate CLIP retrieval SNR")
    p.add_argument("--dataset",        default="scripts/eval_dataset.json")
    p.add_argument("--output",         default=None)
    p.add_argument("--threshold",      type=float, default=float(os.getenv("SEARCH_THRESHOLD", "0.22")))
    p.add_argument("--limit",          type=int,   default=10)
    p.add_argument("--min-snr",        type=float, default=2.0)
    p.add_argument("--min-separation", type=float, default=0.05)
    p.add_argument("--min-hit-rate",   type=float, default=0.50)
    return p.parse_args()


async def main_async(args: argparse.Namespace) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    dataset_path = Path(args.dataset)
    if not dataset_path.exists():
        log.error("Dataset not found: %s", dataset_path)
        return 1

    with open(dataset_path) as f:
        dataset = json.load(f)

    log.info(
        "Evaluating %d queries against %s (threshold=%.2f, limit=%d)",
        len(dataset), API_BASE_URL, args.threshold, args.limit,
    )

    records = await run_evaluation(dataset, args.threshold, args.limit)
    agg = _aggregate(records)

    thresholds = {
        "min_snr":        args.min_snr,
        "min_separation": args.min_separation,
        "min_hit_rate":   args.min_hit_rate,
    }

    all_pass = print_summary(records, agg, thresholds, args.threshold)

    if args.output:
        out = Path(args.output)
        out.parent.mkdir(parents=True, exist_ok=True)
        with open(out, "w") as f:
            json.dump({"aggregate": agg, "thresholds": thresholds, "queries": records}, f, indent=2)
        log.info("Results saved → %s", out)

    return 0 if all_pass else 1


def main():
    args = parse_args()
    sys.exit(asyncio.run(main_async(args)))


if __name__ == "__main__":
    main()
