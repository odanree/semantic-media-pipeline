# Construction Phase Classifier — Implementation Plan

**Goal:** Train a lightweight classifier on top of existing Lumen CLIP embeddings to label
construction assets (photos/videos) by build phase — without retraining CLIP.

**Interview date:** Tuesday 2026-03-24 @ 8:00 AM

---

## Architecture Overview

```
Qdrant (768-dim CLIP vectors)
         │
         ▼
  Linear Probe / Random Forest / SVM
         │
         ▼
  Phase label + confidence score
         │
    ─────┴─────────────────────────
    ≥ 0.85            < 0.85
    Auto-label      Flag for human review
```

You are **not retraining CLIP**. You are attaching a thin classifier head to vectors already in
Qdrant. The ERI parallel: this is exactly what they do — take a massive, unstructured dataset
(salary surveys) and impose a structured taxonomy on top.

---

## Phase 0 — Define Labels

Labels sourced exactly from the [ADU Dashboard](https://adu-dashboard.vercel.app/) (`src/services/data.ts`, `src/App.tsx`).
Phase 7 (OHP — Overhead & Profit) is a budget line item, not a visual phase, so it is excluded from the classifier.
Project start date: **2025-10-08** (`PROJECT_START_DATE` in App.tsx).

Milestone dates are **inspection/completion dates** (end of phase), not start dates.
Phase 1 is the exception: the deposit date (10/08/2025) marks initial mobilization, but site
prep work began before the deposit was issued.

| Phase | Dashboard Label (`category`)   | Window Start           | Window End (inspection date)        |
|-------|--------------------------------|------------------------|-------------------------------------|
| 1     | Phase 1: Site Mobilization     | before 10/08/2025 (est. ~09/01/2025) | ~10/08/2025            |
| 2     | Phase 2: Foundation            | ~10/09/2025            | 11/06/2025 (Under-Slab Inspection)  |
| 3     | Phase 3: Rough MEP             | ~11/07/2025            | 02/12/2026 (Rough MEP Inspection) — overlaps Phase 4 |
| 4     | Phase 4: Framing               | ~02/12/2026            | 02/19/2026 (Framing / Dry-In)       |
| 5     | Phase 5: Exterior              | ~02/20/2026            | 03/02/2026 (Insulation & Drywall)   |
| 6     | Phase 6: Final Completion      | ~03/03/2026            | TBD (Final Inspection)              |

**Auto-labeling rule:** Milestone date = end of phase. Start of each phase = day after prior
phase's inspection. Assets in the Phase 3/4 overlap window get **dual labels** since both trades
were on-site. Assets outside all windows (before ~09/01/2025 est.) are excluded from training.

---

## Phase 0.5 — Expand Training Data from Google Photos

**Do not delete the Google Photos backup.** Photos taken within the construction timeframe
that were never exported to Lumen are unlabeled training data.
Auto-labeling by `created_at` will work the same way once they're ingested.

**Date window — construction only:**
- `start_date`: `2025-09-01` (estimated site prep start, before initial deposit)
- `end_date`: today / project completion — do NOT pull before 2025-09-01, those are unrelated personal photos

**Trigger ingest via the new API endpoint:**
```bash
curl -X POST http://localhost:8000/api/ingest/google-photos \
  -H "Content-Type: application/json" \
  -d '{"start_date": "2025-09-01", "end_date": "2026-03-22"}'
```
Re-run with an updated `end_date` as the project continues. Already-downloaded files are
skipped (idempotent by Google Photos item ID), so re-running never creates duplicates.

**Why this matters:** Every additional construction photo within a phase window is a free
labeled datapoint. More samples in the shorter windows (Site Mobilization, Framing) directly
reduces the class imbalance problem without needing SMOTE.

---

## Phase 1 — Data Extraction from Qdrant

```python
from qdrant_client import QdrantClient
import numpy as np
import pandas as pd

client = QdrantClient(host="localhost", port=6340)

# Pull all vectors + timestamps from the construction collection
records, _ = client.scroll(
    collection_name="media_vectors",
    with_vectors=True,
    with_payload=True,
    limit=100_000,
)

rows = []
for r in records:
    rows.append({
        "id":         r.id,
        "vector":     r.vector,          # 768-dim list
        "created_at": r.payload.get("created_at"),
        "file_path":  r.payload.get("file_path"),
        "file_type":  r.payload.get("file_type"),
    })

df = pd.DataFrame(rows)
df["created_at"] = pd.to_datetime(df["created_at"])
```

---

## Phase 2 — Auto-Label from Inspection Schedule

```python
# Milestone dates = END of each phase (inspection/completion dates from MILESTONE_DATA).
# Phase 1 started before the initial deposit — use estimated pre-work start.
# Category strings match data.ts exactly for dashboard alignment.
PHASE_WINDOWS = [
    ("Phase 1: Site Mobilization", "2025-09-01", "2025-10-08"),  # est. start; deposit = end marker
    ("Phase 2: Foundation",        "2025-10-09", "2025-11-06"),  # Under-Slab Inspection = end
    ("Phase 3: Rough MEP",         "2025-11-07", "2026-02-12"),  # Rough MEP Inspection = end; overlaps Phase 4
    ("Phase 4: Framing",           "2026-02-12", "2026-02-19"),  # starts mid-MEP; Framing Dry-In = end
    ("Phase 5: Exterior",          "2026-02-20", "2026-03-02"),  # Insulation & Drywall Inspections = end
    ("Phase 6: Final Completion",  "2026-03-03", "2026-12-31"),  # TBD — placeholder end
]

def assign_phases(created_at):
    """Return all matching phases (overlap window yields multiple labels)."""
    return [
        phase for phase, start, end in PHASE_WINDOWS
        if pd.Timestamp(start) <= created_at <= pd.Timestamp(end)
    ]

df["phases"] = df["created_at"].apply(assign_phases)
# Explode so overlapping assets appear once per label
labeled = df[df["phases"].map(len) > 0].explode("phases").rename(columns={"phases": "phase"})

# Stats
print(labeled["phase"].value_counts())
# Expect: Foundation >> Site Mobilization/Exterior >> Rough MEP ≈ Framing (overlap window)
# Class imbalance is real but not as extreme as pure window-length implies
```

---

## Phase 3 — Model Training

```python
from sklearn.ensemble import RandomForestClassifier
from sklearn.svm import SVC
from sklearn.model_selection import train_test_split
from sklearn.preprocessing import LabelEncoder
from sklearn.utils.class_weight import compute_class_weight
import numpy as np

X = np.vstack(labeled["vector"].values)   # shape: (n_samples, 768)
le = LabelEncoder()
y = le.fit_transform(labeled["phase"])

X_train, X_test, y_train, y_test = train_test_split(
    X, y, test_size=0.2, random_state=42, stratify=y
)

# --- Option A: Random Forest (handles 768 dims well with limited data) ---
clf = RandomForestClassifier(
    n_estimators=100,
    max_depth=10,
    class_weight="balanced",   # handles imbalance automatically
    random_state=42,
    n_jobs=-1,
)
clf.fit(X_train, y_train)

# --- Option B: SVM (excellent on high-dim, limited-label problems) ---
# from sklearn.svm import SVC
# clf = SVC(kernel="rbf", probability=True, class_weight="balanced")
# clf.fit(X_train, y_train)
```

**Interview talking point — why Random Forest / SVM over a neural network:**
> "I had ~92,000 labeled samples across 6 classes, but the labels are auto-generated from
> inspection windows — they're not perfect. Random Forest and SVM generalize better than a
> deep MLP when labels are noisy and you can't afford to overfit. They're also interpretable:
> I can inspect feature importances to see which embedding dimensions drive phase decisions."

---

## Phase 4 — Confidence Threshold + Human Review Flag

```python
# Predict with probability scores
probs = clf.predict_proba(X_test)          # shape: (n, n_classes)
max_confidence = probs.max(axis=1)
predicted_class = probs.argmax(axis=1)

CONFIDENCE_THRESHOLD = 0.85

results = pd.DataFrame({
    "predicted_phase": le.inverse_transform(predicted_class),
    "confidence":      max_confidence,
    "needs_review":    max_confidence < CONFIDENCE_THRESHOLD,
})

print(f"Auto-labeled:    {(~results['needs_review']).sum()}")
print(f"Flagged for review: {results['needs_review'].sum()}")
```

**Interview one-liner:**
> "I used a 0.85 probability threshold. Below that, the model isn't confident enough — it flags
> the asset for human review. That's not a weakness; it's a data integrity decision. You don't
> let a shaky prediction corrupt a production dataset."

---

## Phase 5 — Temporal Smoothing (Moving Average)

**Problem:** A single frame showing a close-up of a screw might score as "Final Completion"
while the surrounding 200 frames are clearly "Phase 4: Framing."

```python
import pandas as pd

# Assume results_df has columns: file_path, timestamp, phase_proba (array of 6)
# Group frames by video file, sort by timestamp, smooth probabilities

def smooth_predictions(video_df, window=15):
    """Apply moving average over per-frame probability vectors."""
    proba_matrix = np.vstack(video_df["phase_proba"].values)  # (n_frames, n_classes)
    smoothed = pd.DataFrame(proba_matrix).rolling(window=window, center=True, min_periods=1).mean().values
    video_df = video_df.copy()
    video_df["smoothed_phase"] = le.inverse_transform(smoothed.argmax(axis=1))
    return video_df

smoothed_results = (
    results_df
    .groupby("file_path", group_keys=False)
    .apply(smooth_predictions)
)
```

**ERI parallel to use in the interview:**
> "Just like you wouldn't let one outlier salary entry skew a whole job title's median, I don't
> let one noisy frame flip the phase classification of an entire video. You smooth over local
> noise to surface the true signal."

---

## Phase 6 — Handling Class Imbalance

**The problem:** "Phase 2: Foundation" spans 3+ months (thousands of frames). "Phase 1: Site
Mobilization" is only ~4 weeks (far fewer frames). Without correction, the model defaults to
predicting Foundation. Phase 3 and Phase 4 are less affected because they overlap — dual-labeling
the shared window naturally boosts both minority counts.

**Fix A — `class_weight="balanced"` (already in Phase 3 code):**
sklearn computes weights automatically: `weight = n_samples / (n_classes * n_samples_per_class)`

**Fix B — SMOTE (Synthetic Minority Over-sampling Technique):**
```python
from imblearn.over_sampling import SMOTE

sm = SMOTE(random_state=42)
X_resampled, y_resampled = sm.fit_resample(X_train, y_train)

# Then train on X_resampled, y_resampled
clf.fit(X_resampled, y_resampled)
```

SMOTE generates synthetic samples by interpolating between real minority-class vectors in
embedding space. Because CLIP vectors are normalized, these interpolations land in
semantically coherent regions.

**ERI parallel:**
> "Foundation dominated the dataset — 3 months of daily photos vs. a few weeks for Site
> Mobilization. What was interesting is that Rough MEP and Framing ran concurrently, so their
> samples were naturally balanced against each other. I used class weights for the global
> imbalance and SMOTE specifically for Site Mobilization, same logic as weighting a rare job
> title like 'Chief AI Officer' so it doesn't get drowned out by 'Software Engineer'."

---

## Phase 7 — Evaluation + Confusion Matrix

```python
from sklearn.metrics import classification_report, confusion_matrix, ConfusionMatrixDisplay
import matplotlib.pyplot as plt

y_pred = clf.predict(X_test)

print(classification_report(y_test, y_pred, target_names=le.classes_))

cm = confusion_matrix(y_test, y_pred)
disp = ConfusionMatrixDisplay(confusion_matrix=cm, display_labels=le.classes_)
disp.plot(cmap="Blues")
plt.title("Construction Phase Classifier — Confusion Matrix")
plt.tight_layout()
plt.savefig("docs/confusion_matrix.png", dpi=150)
plt.show()
```

**The failure mode to discuss proactively:**
> "The model's biggest confusion is between 'Phase 3: Rough MEP' and 'Phase 4: Framing' — both
> phases have exposed wood studs. To break the tie, I added a secondary signal: check for
> metallic glints (pipes, conduit, junction boxes). If MEP hardware is present, Rough MEP wins.
> That's a domain-knowledge override on top of the statistical model."

---

## Phase 8 — Secondary Feature: Metallic Glint Detection

```python
# Use a separate CLIP text query to score each asset
from PIL import Image
import torch
import clip

model, preprocess = clip.load("ViT-L/14")

metal_prompts = ["metal pipe", "electrical conduit", "wire junction box", "copper plumbing"]
wood_prompts  = ["wood stud", "framing lumber", "2x4 beam"]

# For ambiguous Phase 3: Rough MEP vs Phase 4: Framing predictions:
def tiebreak_rough_mep_vs_framing(image_path):
    image = preprocess(Image.open(image_path)).unsqueeze(0)
    texts = clip.tokenize(metal_prompts + wood_prompts)
    with torch.no_grad():
        logits, _ = model(image, texts)
        probs = logits.softmax(dim=-1).squeeze().tolist()
    metal_score = sum(probs[:len(metal_prompts)])
    wood_score  = sum(probs[len(metal_prompts):])
    return "Phase 3: Rough MEP" if metal_score > wood_score else "Phase 4: Framing"
```

---

## Interview Delivery Script

**Opening line (when they ask about ML experience):**
> "Beyond my work with the NBA Classifier, I built a Construction Phase Classifier for my home
> addition project. I'm leveraging the 768-dim CLIP embeddings from my Lumen pipeline to
> automatically categorize 11,000+ assets into phases like Foundation, Rough MEP, and Framing.
> The interesting problems were class imbalance — Rough MEP was only a 7-day window vs. 3 months
> for Foundation — and temporal smoothing so one noisy frame doesn't flip an entire video's
> classification."

---

## Definitions Cheatsheet

| Term | One-liner |
|---|---|
| **Precision** | Of everything I called "Framing," how many were actually Framing? (avoid false positives) |
| **Recall** | Of all real Framing assets, how many did I catch? (avoid false negatives) |
| **F1** | Harmonic mean of precision and recall. Use when both matter equally. |
| **SMOTE** | Generates synthetic minority samples by interpolating between real ones in embedding space. |
| **Linear Probe** | Freeze the backbone, train only the final layer. Fastest way to adapt a pretrained model. |
| **Class imbalance** | One label has far more samples than another. Model defaults to predicting the majority. |
| **Temporal smoothing** | Apply a rolling window over per-frame predictions to suppress one-off noise. |
| **Confusion matrix** | Grid: true labels × predicted labels. Off-diagonals = where the model is wrong. |

---

## Key Stats to Have Ready

- **Total assets:** ~11,029 (10,100 JPGs + ~929 MP4s) from the construction folder
- **Vector dimensions:** 768 (CLIP ViT-L/14)
- **Confidence threshold:** 0.85 (below = human review queue)
- **Smoothing window:** 15 frames (~0.5s at 30fps)
- **Model choice:** Random Forest (100 trees, max_depth=10, balanced weights)
- **ERI parallel:** Structured Timeline from raw surveys = Phase Timeline from raw embeddings
