#!/usr/bin/env python3
"""
Construction Phase Classifier
==============================
Trains a lightweight classifier on top of existing CLIP embeddings stored in
Qdrant. Labels are auto-assigned from inspection milestone dates.

Usage:
    pip install qdrant-client numpy pandas scikit-learn matplotlib joblib
    python scripts/train_phase_classifier.py

Output:
    models/phase_classifier.joblib   — trained model + label encoder
    models/phase_classifier_report.txt — classification report
    models/phase_classifier_cm.png   — confusion matrix heatmap
"""

import os
import re
import sys
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
from sklearn.svm import SVC

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
QDRANT_HOST       = os.environ.get("QDRANT_HOST", "localhost")
QDRANT_PORT       = int(os.environ.get("QDRANT_PORT", 6333))
COLLECTION_NAME   = os.environ.get("QDRANT_COLLECTION_NAME", "media_vectors")
CONFIDENCE_THRESHOLD = 0.85

# Phase windows: (label, start_inclusive, end_inclusive)
# Dates are inspection/completion dates = END of each phase.
# Phase 3/4 overlap: both trades on-site simultaneously Feb 12–19 2026.
PHASE_WINDOWS = [
    ("Phase 1: Site Mobilization",  "2025-09-01", "2025-10-08"),
    ("Phase 2: Foundation",         "2025-10-09", "2025-11-06"),
    ("Phase 3: Rough MEP & Framing","2025-11-07", "2026-02-19"),  # MEP + framing done in tandem
    ("Phase 4: Exterior",           "2026-02-20", "2026-03-02"),
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
    batch = 0

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

        batch += 1
        print(f"  Fetched {len(rows):,} records …", end="\r")

        if next_offset is None:
            break
        offset = next_offset

    print(f"\nTotal records fetched: {len(rows):,}")
    df = pd.DataFrame(rows)
    return df


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
    df["phases"] = df["created_at"].apply(assign_phases)
    labeled = (
        df[df["phases"].map(len) > 0]
        .explode("phases")
        .rename(columns={"phases": "phase"})
        .dropna(subset=["vector"])
    )
    print("\nLabel distribution:")
    print(labeled["phase"].value_counts().to_string())
    return labeled


# ---------------------------------------------------------------------------
# Step 3 — Train
# ---------------------------------------------------------------------------
def train(labeled: pd.DataFrame):
    X = np.vstack(labeled["vector"].values)   # (n_samples, 768)
    le = LabelEncoder()
    y = le.fit_transform(labeled["phase"])

    print(f"\nTraining on {len(X):,} samples, {len(le.classes_)} classes, {X.shape[1]} dims")

    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.2, random_state=42, stratify=y
    )

    # Random Forest — handles 768-dim well with limited/noisy labels
    clf = RandomForestClassifier(
        n_estimators=200,
        max_depth=12,
        class_weight="balanced",
        random_state=42,
        n_jobs=-1,
    )
    print("Fitting Random Forest …")
    clf.fit(X_train, y_train)

    return clf, le, X_test, y_test


# ---------------------------------------------------------------------------
# Step 4 — Evaluate
# ---------------------------------------------------------------------------
def evaluate(clf, le, X_test, y_test) -> str:
    y_pred = clf.predict(X_test)
    report = classification_report(y_test, y_pred, target_names=le.classes_)
    print("\n" + report)

    # Confusion matrix
    cm = confusion_matrix(y_test, y_pred)
    fig, ax = plt.subplots(figsize=(9, 7))
    disp = ConfusionMatrixDisplay(confusion_matrix=cm, display_labels=le.classes_)
    disp.plot(ax=ax, xticks_rotation=30, colorbar=False)
    ax.set_title("Construction Phase Classifier — Confusion Matrix")
    plt.tight_layout()

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    cm_path = OUTPUT_DIR / "phase_classifier_cm.png"
    plt.savefig(cm_path, dpi=150)
    print(f"Confusion matrix saved → {cm_path}")

    # Confidence distribution
    probs = clf.predict_proba(X_test)
    max_conf = probs.max(axis=1)
    auto = (max_conf >= CONFIDENCE_THRESHOLD).sum()
    review = (max_conf < CONFIDENCE_THRESHOLD).sum()
    print(f"\nAt {CONFIDENCE_THRESHOLD:.0%} threshold:")
    print(f"  Auto-labeled:      {auto:,} ({auto/len(X_test):.1%})")
    print(f"  Flagged for review: {review:,} ({review/len(X_test):.1%})")

    return report


# ---------------------------------------------------------------------------
# Step 5 — Save
# ---------------------------------------------------------------------------
def save_model(clf, le, report: str):
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    model_path = OUTPUT_DIR / "phase_classifier.joblib"
    joblib.dump({"model": clf, "label_encoder": le}, model_path)
    print(f"Model saved → {model_path}")

    report_path = OUTPUT_DIR / "phase_classifier_report.txt"
    report_path.write_text(report)
    print(f"Report saved → {report_path}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    client = QdrantClient(host=QDRANT_HOST, port=QDRANT_PORT)

    df = fetch_vectors(client)
    if df.empty:
        print("No records found in Qdrant. Is the collection populated?")
        sys.exit(1)

    labeled = label_data(df)
    if labeled.empty:
        print("No records fall within any phase window. Check created_at timestamps.")
        sys.exit(1)

    # Require at least 2 samples per class for stratified split
    counts = labeled["phase"].value_counts()
    valid_phases = counts[counts >= 5].index
    dropped = counts[counts < 5]
    if not dropped.empty:
        print(f"\nDropping phases with < 5 samples: {dropped.to_dict()}")
        labeled = labeled[labeled["phase"].isin(valid_phases)]

    if labeled["phase"].nunique() < 2:
        print("Need at least 2 phases to train a classifier.")
        sys.exit(1)

    clf, le, X_test, y_test = train(labeled)
    report = evaluate(clf, le, X_test, y_test)
    save_model(clf, le, report)

    print("\nDone. Next: run predict_phases.py to label your full Qdrant collection.")


if __name__ == "__main__":
    main()
