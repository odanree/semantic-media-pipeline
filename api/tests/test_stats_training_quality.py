"""
Tests for the training-readiness source split and the new eval-set readiness
endpoint introduced for label-quality observability.

Verifies that:
  - /api/stats/training breaks positives/negatives out by source
    (direct = manual+bulk_upvote, cascade = worker-propagated)
  - tier_direct is computed off direct positives only
  - /api/stats/eval-set returns curated-set tiers vs _EVAL_GOALS
"""
from unittest.mock import MagicMock


# ── /api/stats/training source split ────────────────────────────────────────

def test_training_response_includes_source_split_keys(client, mock_db_session):
    # No rows — totals all zero, but the split keys must still be present so
    # the frontend can render zero-state without conditional plumbing.
    mock_db_session.execute.return_value.fetchall.return_value = []
    body = client.get("/api/stats/training").json()
    assert "positives_direct"  in body["totals"]
    assert "positives_cascade" in body["totals"]
    assert "negatives_direct"  in body["totals"]
    assert "negatives_cascade" in body["totals"]
    assert "ratio_direct"      in body["totals"]
    assert "positives_direct"  in body["tiers"]
    assert "ratio_direct"      in body["tiers"]


def test_training_per_query_split_sums_to_total(client, mock_db_session):
    # row shape: (query, pos_direct, pos_cascade, neg_direct, neg_cascade)
    mock_db_session.execute.return_value.fetchall.return_value = [
        ("cat", 100, 900, 20, 0),     # heavily cascade-driven
        ("dog",  50,  50, 30, 0),     # balanced
    ]
    body = client.get("/api/stats/training").json()
    assert body["totals"]["positives_direct"]  == 150
    assert body["totals"]["positives_cascade"] == 950
    assert body["totals"]["positives"]         == 1100
    assert body["totals"]["negatives"]         == 50
    # Per-query record carries the split too
    q_cat = next(q for q in body["queries"] if q["query"] == "cat")
    assert q_cat["positives_direct"]  == 100
    assert q_cat["positives_cascade"] == 900


def test_training_tier_direct_reflects_only_human_labels(client, mock_db_session):
    # 4_999 direct positives (under 5K "good" threshold) plus 100_000 cascade.
    # Naive `tier` would say "best"; `tier_direct` must say "needs_data".
    mock_db_session.execute.return_value.fetchall.return_value = [
        ("cat", 4_999, 100_000, 1_000, 0),
    ]
    body = client.get("/api/stats/training").json()
    assert body["tiers"]["positives"]        == "best"
    assert body["tiers"]["positives_direct"] == "needs_data"


# ── /api/stats/eval-set ─────────────────────────────────────────────────────

def test_eval_set_readiness_empty(client, mock_db_session):
    mock_db_session.execute.return_value.fetchall.return_value = []
    body = client.get("/api/stats/eval-set").json()
    assert body["totals"] == {"queries": 0, "positives": 0, "negatives": 0, "total": 0}
    assert body["tiers"]["queries"] == "needs_data"
    assert "goals" in body and "positives" in body["goals"]


def test_eval_set_readiness_with_curated_labels(client, mock_db_session):
    mock_db_session.execute.return_value.fetchall.return_value = [
        ("cat", 60, 40),
        ("dog", 50, 50),
    ]
    body = client.get("/api/stats/eval-set").json()
    assert body["totals"]["queries"]   == 2
    assert body["totals"]["positives"] == 110
    assert body["totals"]["negatives"] == 90
    assert body["totals"]["total"]     == 200
    # Per-query records carry totals
    assert body["queries"][0]["total"] == 100


def test_eval_set_readiness_smaller_goals_than_training(client, mock_db_session):
    """100 positives is 'good' in the eval set but 'needs_data' for training."""
    mock_db_session.execute.return_value.fetchall.return_value = [("cat", 100, 0)]
    eval_body = client.get("/api/stats/eval-set").json()
    assert eval_body["tiers"]["positives"] == "good"  # eval threshold 100/500/2000
