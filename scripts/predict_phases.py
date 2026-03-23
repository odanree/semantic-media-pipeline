#!/usr/bin/env python3
"""
Apply the trained phase classifier to all construction-window vectors in Qdrant.
Writes phase + confidence back as payload fields.

Usage:
    python scripts/predict_phases.py [--dry-run]
"""

import argparse
import os
import re
import sys
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from qdrant_client import QdrantClient
from qdrant_client.models import PointIdsList, SetPayloadOperation, SetPayload, Filter, FieldCondition, MatchText

QDRANT_HOST     = os.environ.get("QDRANT_HOST", "localhost")
QDRANT_PORT     = int(os.environ.get("QDRANT_PORT", 6333))
COLLECTION_NAME = os.environ.get("QDRANT_COLLECTION_NAME", "media_vectors")
MODEL_PATH      = Path(__file__).parent.parent / "models" / "phase_classifier.joblib"
CONFIDENCE_THRESHOLD = 0.85

# Same windows as training — assets outside get phase=None
CONSTRUCTION_START = pd.Timestamp("2025-09-01", tz="UTC")
CONSTRUCTION_END   = pd.Timestamp("2026-12-31", tz="UTC")

# Filename date extraction — same logic as train_phase_classifier.py
_FILENAME_DATE_RE = re.compile(r'(?:PXL|VID|IMG|MVIMG|PANO)_(\d{8})_', re.IGNORECASE)
_DJI_DATE_RE      = re.compile(r'DJI_(\d{8})', re.IGNORECASE)


def _date_from_filename(file_path: str) -> pd.Timestamp | None:
    name = file_path.split("/")[-1]
    m = _FILENAME_DATE_RE.search(name) or _DJI_DATE_RE.search(name)
    if m:
        try:
            return pd.Timestamp(m.group(1), tz="UTC")
        except Exception:
            pass
    return None


def main(dry_run: bool = False):
    if not MODEL_PATH.exists():
        print(f"Model not found at {MODEL_PATH}. Run train_phase_classifier.py first.")
        sys.exit(1)

    bundle = joblib.load(MODEL_PATH)
    clf = bundle["model"]
    le  = bundle["label_encoder"]
    print(f"Loaded model: {len(le.classes_)} classes -> {list(le.classes_)}")

    client = QdrantClient(host=QDRANT_HOST, port=QDRANT_PORT)

    # Clear any previously written phase payloads from ALL records before re-labeling
    print("Clearing previous phase labels from all records …")
    clear_offset = None
    cleared = 0
    while True:
        records, next_clear = client.scroll(
            collection_name=COLLECTION_NAME,
            with_vectors=False,
            with_payload=False,
            limit=1000,
            offset=clear_offset,
        )
        if not records:
            break
        ids = [r.id for r in records]
        if not dry_run:
            client.delete_payload(
                collection_name=COLLECTION_NAME,
                keys=["construction_phase", "phase_confidence", "phase_needs_review"],
                points=ids,
            )
        cleared += len(ids)
        print(f"  Cleared {cleared:,} records …", end="\r")
        if next_clear is None:
            break
        clear_offset = next_clear
    print(f"\nCleared phase labels from {cleared:,} records.")

    # Pass 1 — collect IDs of construction assets using indexed file_path filter
    print("Pass 1: scanning file paths …")
    construction_ids = []
    offset = None
    path_filter = Filter(should=[
        FieldCondition(key="file_path", match=MatchText(text="Construction Timeline")),
        FieldCondition(key="file_path", match=MatchText(text="Construction Phase")),
        FieldCondition(key="file_path", match=MatchText(text="DJI 20251201")),
    ])
    while True:
        records, next_offset = client.scroll(
            collection_name=COLLECTION_NAME,
            scroll_filter=path_filter,
            with_vectors=False,
            with_payload=["file_path"],
            limit=1000,
            offset=offset,
        )
        if not records:
            break
        for r in records:
            fp = (r.payload or {}).get("file_path", "")
            ts = _date_from_filename(fp)
            if ts is not None and CONSTRUCTION_START <= ts <= CONSTRUCTION_END:
                construction_ids.append(r.id)
        print(f"  Found {len(construction_ids):,} construction assets …", end="\r")
        if next_offset is None:
            break
        offset = next_offset
    print(f"\nFound {len(construction_ids):,} construction assets to classify.")

    # Pass 2 — fetch vectors only for matched IDs, predict, write payloads
    print("Pass 2: classifying …")
    updated = 0
    skipped = 0
    BATCH = 1000
    for i in range(0, len(construction_ids), BATCH):
        chunk_ids = construction_ids[i:i + BATCH]
        records = client.retrieve(
            collection_name=COLLECTION_NAME,
            ids=chunk_ids,
            with_vectors=True,
            with_payload=False,
        )

        valid_records = [r for r in records if r.vector is not None]
        skipped += len(records) - len(valid_records)
        if not valid_records:
            continue

        X = np.array([r.vector for r in valid_records])
        probs_all = clf.predict_proba(X)
        idxs = probs_all.argmax(axis=1)
        confs   = [float(probs_all[j, idxs[j]]) for j in range(len(valid_records))]
        phases  = [le.classes_[k] for k in idxs]
        ids     = [r.id for r in valid_records]

        if not dry_run:
            # Group by phase → one set_payload call per phase class (6 max per batch)
            from collections import defaultdict
            phase_groups: dict = defaultdict(list)
            for pid, phase in zip(ids, phases):
                phase_groups[phase].append(pid)
            for phase, pids in phase_groups.items():
                client.set_payload(collection_name=COLLECTION_NAME,
                                   payload={"construction_phase": phase}, points=pids)
            # Confidence is unique per point — one batched call
            ops = [
                SetPayloadOperation(
                    set_payload=SetPayload(
                        payload={
                            "phase_confidence":   conf,
                            "phase_needs_review": conf < CONFIDENCE_THRESHOLD,
                        },
                        points=[pid],
                    )
                )
                for pid, conf in zip(ids, confs)
            ]
            client.batch_update_points(collection_name=COLLECTION_NAME, update_operations=ops)

        updated += len(valid_records)
        print(f"  Classified {updated:,} / {len(construction_ids):,} …", end="\r")

    print(f"\nDone. Updated: {updated:,}  Skipped (outside window): {skipped:,}")
    if dry_run:
        print("(dry-run — no payloads written)")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="Predict but don't write to Qdrant")
    args = parser.parse_args()
    main(dry_run=args.dry_run)
