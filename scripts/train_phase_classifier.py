#!/usr/bin/env python3
"""
Construction Phase Classifier
==============================
Trains a lightweight classifier on top of existing CLIP embeddings stored in
Qdrant. Labels are auto-assigned from inspection milestone dates.

Usage:
    pip install qdrant-client numpy pandas scikit-learn matplotlib joblib
    python scripts/train_phase_classifier.py [--eval-strategy STRATEGY]

Eval strategies:
    random    — stratified random 80/20 split (baseline, default)
    temporal  — within each phase, train on early frames, test on late frames
    boundary  — test only on frames within 14 days of a phase transition
    camera    — train on Pixel (Construction Timeline), test on DJI (Construction Phase)

    all       — run all four strategies and print a comparison table

Output:
    models/phase_classifier.joblib      — trained model + label encoder (from --eval-strategy or random)
    models/phase_classifier_report.txt  — classification report
    models/phase_classifier_cm.png      — confusion matrix heatmap
"""

import argparse
import os
import re
import sys
import time
from pathlib import Path

import joblib
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from qdrant_client import QdrantClient
from qdrant_client.models import Filter, FieldCondition, MatchText
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import classification_report, confusion_matrix, ConfusionMatrixDisplay
from sklearn.model_selection import train_test_split
from sklearn.preprocessing import LabelEncoder

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
QDRANT_HOST          = os.environ.get("QDRANT_HOST", "localhost")
QDRANT_PORT          = int(os.environ.get("QDRANT_PORT", 6333))
COLLECTION_NAME      = os.environ.get("QDRANT_COLLECTION_NAME", "media_vectors")
CONFIDENCE_THRESHOLD = 0.85
BOUNDARY_DAYS        = 14  # days around each phase transition to use as boundary test set

# Phase windows: (label, start_inclusive, end_inclusive)
# Boundaries derived from actual inspection approval dates.
#
# Phase 1: Site Mobilization     → Sep 1   – Oct 8, 2025
# Phase 2: Foundation            → Oct 9   – Nov 6, 2025   (Underground plumbing Oct 29 ✅, Footing/Steel Nov 6 ✅)
# Phase 3a: Rough Wall Framing   → Nov 7   – Jan 5, 2026   (Floor joists Nov 26 ✅, open studs / early sheathing)
# Phase 3b: Structural Closeout  → Jan 6   – Feb 2, 2026   (Shear wall Jan 6 first attempt → Feb 2 ✅, Roof framing/sheathing Feb 2 ✅)
# Phase 3c: MEP & Framing        → Feb 3   – Feb 19, 2026  (MEPS Feb 17 ✅, Framing final Feb 20 ✅)
# Phase 4: Exterior Finish       → Feb 20  – Mar 2, 2026   (Interior lath Feb 20 ✅, Exterior lath Feb 23 ✅, Insulation Feb 25 ✅, Drywall Mar 2 ✅)
# Phase 5: Final Completion      → Mar 3   – Dec 31, 2026
#
# Phase 3a/3b split at Jan 6 (Shear Wall first inspection): walls are substantially
# framed by this point — visually distinct from the open-stud early framing period.
PHASE_WINDOWS = [
    ("Phase 1: Site Mobilization",  "2025-09-01", "2025-10-08"),
    ("Phase 2: Foundation",         "2025-10-09", "2025-11-06"),
    ("Phase 3a: Rough Wall Framing","2025-11-07", "2026-01-05"),
    ("Phase 3b: Structural Closeout","2026-01-06", "2026-02-02"),
    ("Phase 3c: MEP & Framing",     "2026-02-03", "2026-02-19"),
    ("Phase 4: Exterior Finish",    "2026-02-20", "2026-03-02"),
    ("Phase 5: Final Completion",   "2026-03-03", "2026-12-31"),
]

OUTPUT_DIR = Path(__file__).parent.parent / "models"

# ---------------------------------------------------------------------------
# Date extraction helpers
# ---------------------------------------------------------------------------
# Pattern: PXL_YYYYMMDD_... or VID_YYYYMMDD_... or IMG_YYYYMMDD_...
_FILENAME_DATE_RE = re.compile(r'(?:PXL|VID|IMG|MVIMG|PANO)_(\d{8})_', re.IGNORECASE)
# DJI: DJI_YYYYMMDDHHMMSS_...
_DJI_DATE_RE = re.compile(r'DJI_(\d{8})', re.IGNORECASE)


def _date_from_filename(file_path: str) -> pd.Timestamp | None:
    name = file_path.split("/")[-1]
    m = _FILENAME_DATE_RE.search(name) or _DJI_DATE_RE.search(name)
    if m:
        try:
            return pd.Timestamp(m.group(1), tz="UTC")
        except Exception:
            pass
    return None


# ---------------------------------------------------------------------------
# Step 1 — Pull all vectors from Qdrant
# ---------------------------------------------------------------------------
def fetch_vectors(client: QdrantClient) -> pd.DataFrame:
    t0 = time.perf_counter()
    print(f"Connecting to Qdrant at {QDRANT_HOST}:{QDRANT_PORT} …")
    # Only train on verified construction media — curated folders that contain
    # nothing but construction photos/videos.  Pixel 9 monthly backups and other
    # mixed folders are intentionally excluded; they will be classified later
    # using the model trained here.
    construction_filter = Filter(should=[
        FieldCondition(key="file_path", match=MatchText(text="Construction Timeline")),
        FieldCondition(key="file_path", match=MatchText(text="Construction Phase")),
    ])
    rows = []
    offset = None

    while True:
        records, next_offset = client.scroll(
            collection_name=COLLECTION_NAME,
            scroll_filter=construction_filter,
            with_vectors=True,
            with_payload=True,
            limit=1000,
            offset=offset,
        )
        if not records:
            break

        for r in records:
            fp = r.payload.get("file_path", "")
            # Prefer date parsed from filename (immune to mtime/indexing-time pollution).
            # Fall back to payload created_at only if filename yields nothing.
            ts = _date_from_filename(fp)
            if ts is None:
                raw = r.payload.get("created_at")
                ts = pd.to_datetime(raw, errors="coerce", utc=True) if raw else None
            rows.append({
                "id":         r.id,
                "vector":     r.vector,
                "created_at": ts,
                "file_path":  fp,
                "file_type":  r.payload.get("file_type"),
            })

        print(f"  Fetched {len(rows):,} records …", end="\r")

        if next_offset is None:
            break
        offset = next_offset

    elapsed = time.perf_counter() - t0
    print(f"\nTotal records fetched: {len(rows):,}  ({elapsed:.1f}s)")
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Step 2 — Auto-label from inspection schedule
# ---------------------------------------------------------------------------
def assign_phases(created_at) -> list:
    """Return all matching phase labels (overlap window yields 2 labels)."""
    if pd.isna(created_at):
        return []
    return [
        phase
        for phase, start, end in PHASE_WINDOWS
        if pd.Timestamp(start, tz="UTC") <= created_at <= pd.Timestamp(end, tz="UTC")
    ]


def label_data(df: pd.DataFrame) -> pd.DataFrame:
    t0 = time.perf_counter()
    df["phases"] = df["created_at"].apply(assign_phases)
    labeled = (
        df[df["phases"].map(len) > 0]
        .explode("phases")
        .rename(columns={"phases": "phase"})
        .dropna(subset=["vector"])
    )
    elapsed = time.perf_counter() - t0
    print(f"\nLabel distribution:  ({elapsed:.2f}s)")
    print(labeled["phase"].value_counts().to_string())
    return labeled


# ---------------------------------------------------------------------------
# Step 3 — Split strategies
# ---------------------------------------------------------------------------
def split_random(labeled: pd.DataFrame):
    """Baseline: stratified random 80/20."""
    X = np.vstack(labeled["vector"].values)
    le = LabelEncoder()
    y = le.fit_transform(labeled["phase"])
    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.2, random_state=42, stratify=y
    )
    return X_train, X_test, y_train, y_test, le


def split_temporal(labeled: pd.DataFrame):
    """
    Within each phase, sort by date and hold out the last 20% of frames.
    Tests whether the model generalizes across time within a phase — early
    footage predicts late footage.
    """
    train_frames, test_frames = [], []
    for phase, group in labeled.groupby("phase"):
        group_sorted = group.sort_values("created_at")
        split_idx = int(len(group_sorted) * 0.8)
        if split_idx == 0 or split_idx == len(group_sorted):
            # Too few samples — fall back to including all in train
            train_frames.append(group_sorted)
            continue
        train_frames.append(group_sorted.iloc[:split_idx])
        test_frames.append(group_sorted.iloc[split_idx:])

    if not test_frames:
        print("  [temporal] Not enough samples per phase for temporal split — falling back to random.")
        return split_random(labeled)

    train_df = pd.concat(train_frames)
    test_df  = pd.concat(test_frames)

    le = LabelEncoder()
    le.fit(labeled["phase"])

    X_train = np.vstack(train_df["vector"].values)
    y_train = le.transform(train_df["phase"])
    X_test  = np.vstack(test_df["vector"].values)
    y_test  = le.transform(test_df["phase"])

    print(f"  [temporal] train={len(X_train):,}  test={len(X_test):,} "
          f"(last 20% of each phase by date)")
    return X_train, X_test, y_train, y_test, le


def split_boundary(labeled: pd.DataFrame):
    """
    Test set = frames within BOUNDARY_DAYS of any phase transition.
    Train set = everything else.
    Tests the hardest cases: visually ambiguous frames at phase edges.
    """
    boundary_mask = pd.Series(False, index=labeled.index)
    for _, _, end in PHASE_WINDOWS[:-1]:  # no boundary after the last phase
        end_ts = pd.Timestamp(end, tz="UTC")
        boundary_mask |= (
            (labeled["created_at"] >= end_ts - pd.Timedelta(days=BOUNDARY_DAYS)) &
            (labeled["created_at"] <= end_ts + pd.Timedelta(days=BOUNDARY_DAYS))
        )

    test_df  = labeled[boundary_mask]
    train_df = labeled[~boundary_mask]

    if len(test_df) < 10:
        print(f"  [boundary] Only {len(test_df)} boundary frames found "
              f"(need ≥10) — falling back to random.")
        return split_random(labeled)

    le = LabelEncoder()
    le.fit(labeled["phase"])

    # Ensure all classes are represented in train
    missing = set(le.classes_) - set(train_df["phase"].unique())
    if missing:
        print(f"  [boundary] Classes missing from train set: {missing} — falling back to random.")
        return split_random(labeled)

    X_train = np.vstack(train_df["vector"].values)
    y_train = le.transform(train_df["phase"])
    X_test  = np.vstack(test_df["vector"].values)
    y_test  = le.transform(test_df["phase"])

    print(f"  [boundary] train={len(X_train):,}  test={len(X_test):,} "
          f"(frames within ±{BOUNDARY_DAYS}d of phase transitions)")
    return X_train, X_test, y_train, y_test, le


def split_camera(labeled: pd.DataFrame):
    """
    Train on Pixel phone footage (Construction Timeline),
    test on DJI drone footage (Construction Phase).
    Tests cross-camera generalization — different angle, altitude, lens.
    """
    pixel_mask = labeled["file_path"].str.contains("Construction Timeline", na=False)
    dji_mask   = labeled["file_path"].str.contains("Construction Phase",    na=False)

    train_df = labeled[pixel_mask]
    test_df  = labeled[dji_mask]

    if len(train_df) < 10 or len(test_df) < 10:
        print(f"  [camera] Insufficient data — "
              f"Pixel={len(train_df):,}  DJI={len(test_df):,} — falling back to random.")
        return split_random(labeled)

    le = LabelEncoder()
    le.fit(labeled["phase"])

    # Ensure all test classes appear in training
    missing = set(test_df["phase"].unique()) - set(train_df["phase"].unique())
    if missing:
        print(f"  [camera] DJI has phases not in Pixel training set: {missing} — falling back to random.")
        return split_random(labeled)

    X_train = np.vstack(train_df["vector"].values)
    y_train = le.transform(train_df["phase"])
    X_test  = np.vstack(test_df["vector"].values)
    y_test  = le.transform(test_df["phase"])

    print(f"  [camera] train={len(X_train):,} (Pixel)  test={len(X_test):,} (DJI)")
    return X_train, X_test, y_train, y_test, le


SPLIT_STRATEGIES = {
    "random":   split_random,
    "temporal": split_temporal,
    "boundary": split_boundary,
    "camera":   split_camera,
}


# ---------------------------------------------------------------------------
# Step 4 — Train
# ---------------------------------------------------------------------------
def train(X_train, y_train):
    clf = RandomForestClassifier(
        n_estimators=200,
        max_depth=12,
        class_weight="balanced",
        random_state=42,
        n_jobs=-1,
    )
    print("Fitting Random Forest …")
    t0 = time.perf_counter()
    clf.fit(X_train, y_train)
    elapsed = time.perf_counter() - t0
    print(f"Fitting done  ({elapsed:.1f}s)")
    return clf


# ---------------------------------------------------------------------------
# Step 5 — Evaluate
# ---------------------------------------------------------------------------
def evaluate(clf, le, X_test, y_test, strategy: str, save_artifacts: bool = True) -> str:
    t0     = time.perf_counter()
    y_pred = clf.predict(X_test)

    # Only report on classes that actually appear in this test set —
    # e.g. the camera strategy DJI set may not cover all 5 phases.
    present_labels = np.unique(np.concatenate([y_test, y_pred]))
    present_names  = le.classes_[present_labels]

    report   = classification_report(y_test, y_pred, labels=present_labels, target_names=present_names)
    accuracy = (y_pred == y_test).mean()
    elapsed  = time.perf_counter() - t0
    print(f"\n[{strategy}] accuracy={accuracy:.1%}  inference={elapsed:.2f}s\n" + report)

    if len(present_labels) < len(le.classes_):
        missing = set(le.classes_) - set(present_names)
        print(f"  ℹ  Classes absent from this test set (not scored): {sorted(missing)}")

    if save_artifacts:
        cm = confusion_matrix(y_test, y_pred, labels=present_labels)
        fig, ax = plt.subplots(figsize=(9, 7))
        disp = ConfusionMatrixDisplay(confusion_matrix=cm, display_labels=present_names)
        disp.plot(ax=ax, xticks_rotation=30, colorbar=False)
        ax.set_title(f"Construction Phase Classifier — Confusion Matrix ({strategy})")
        plt.tight_layout()
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        cm_path = OUTPUT_DIR / f"phase_classifier_cm_{strategy}.png"
        plt.savefig(cm_path, dpi=150)
        plt.close()
        print(f"Confusion matrix saved → {cm_path}")

        probs    = clf.predict_proba(X_test)
        max_conf = probs.max(axis=1)
        auto     = (max_conf >= CONFIDENCE_THRESHOLD).sum()
        review   = (max_conf < CONFIDENCE_THRESHOLD).sum()
        print(f"\nAt {CONFIDENCE_THRESHOLD:.0%} threshold:")
        print(f"  Auto-labeled:       {auto:,} ({auto/len(X_test):.1%})")
        print(f"  Flagged for review: {review:,} ({review/len(X_test):.1%})")

    return report, accuracy


# ---------------------------------------------------------------------------
# Step 6 — Save
# ---------------------------------------------------------------------------
def save_model(clf, le, report: str, strategy: str):
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    model_path = OUTPUT_DIR / "phase_classifier.joblib"
    joblib.dump({"model": clf, "label_encoder": le}, model_path)
    print(f"Model saved → {model_path}")

    report_path = OUTPUT_DIR / "phase_classifier_report.txt"
    report_path.write_text(f"Eval strategy: {strategy}\n\n" + report)
    print(f"Report saved → {report_path}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--eval-strategy",
        choices=[*SPLIT_STRATEGIES.keys(), "all"],
        default="random",
        help="How to split train/test data (default: random)",
    )
    args = parser.parse_args()

    client  = QdrantClient(host=QDRANT_HOST, port=QDRANT_PORT)
    df      = fetch_vectors(client)
    if df.empty:
        print("No records found in Qdrant. Is the collection populated?")
        sys.exit(1)

    labeled = label_data(df)
    if labeled.empty:
        print("No records fall within any phase window. Check created_at timestamps.")
        sys.exit(1)

    # Drop phases with too few samples
    counts       = labeled["phase"].value_counts()
    valid_phases = counts[counts >= 5].index
    dropped      = counts[counts < 5]
    if not dropped.empty:
        print(f"\nDropping phases with < 5 samples: {dropped.to_dict()}")
        labeled = labeled[labeled["phase"].isin(valid_phases)]

    if labeled["phase"].nunique() < 2:
        print("Need at least 2 phases to train a classifier.")
        sys.exit(1)

    strategies = list(SPLIT_STRATEGIES.keys()) if args.eval_strategy == "all" else [args.eval_strategy]
    summary: list[tuple[str, float]] = []

    for strategy in strategies:
        t_strategy = time.perf_counter()
        print(f"\n{'='*60}")
        print(f"  Strategy: {strategy.upper()}")
        print(f"{'='*60}")

        X_train, X_test, y_train, y_test, le = SPLIT_STRATEGIES[strategy](labeled)
        print(f"  Training on {len(X_train):,} samples, {len(le.classes_)} classes, {X_train.shape[1]} dims")

        clf = train(X_train, y_train)

        is_primary = strategy == strategies[-1]  # save artifacts + model for the last strategy
        report, accuracy = evaluate(clf, le, X_test, y_test, strategy, save_artifacts=is_primary)
        summary.append((strategy, accuracy, time.perf_counter() - t_strategy))

        if is_primary:
            save_model(clf, le, report, strategy)

    if len(summary) > 1:
        print(f"\n{'='*60}")
        print("  GENERALIZATION SUMMARY")
        print(f"{'='*60}")
        print(f"  {'Strategy':<12}  {'Accuracy':>8}  {'vs random':>10}  {'Time':>8}")
        baseline = next((acc for s, acc, _ in summary if s == "random"), summary[0][1])
        for strat, acc, elapsed in summary:
            delta = acc - baseline
            sign  = "+" if delta >= 0 else ""
            print(f"  {strat:<12}  {acc:>7.1%}  {sign}{delta:>8.1%}  {elapsed:>6.1f}s")
        print()
        drop = min(acc for _, acc, _ in summary) - baseline
        if drop < -0.10:
            print("  ⚠  Accuracy drops >10% on a stricter strategy — model may be")
            print("     overfitting to temporal or camera-specific patterns.")
        else:
            print("  ✓  Accuracy is stable across strategies — model is generalizing.")

    print("\nDone. Next: run predict_phases.py to label your full Qdrant collection.")


if __name__ == "__main__":
    main()
