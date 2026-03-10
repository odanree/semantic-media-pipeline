# Plan: Scene-Based Result Grouping (Temporal Deduplication)

## Problem

A 2-hour video indexed at 0.5 FPS produces ~3,600 Qdrant points. A single search
query can return 20 results that are all from the same file — different timestamps,
effectively identical scenes seconds apart. This wastes the result limit and buries
other files entirely.

**Confirmed scope:**
- Zero dedup logic exists anywhere today (API, frontend, worker)
- limit currently means "number of frames", not "number of distinct scenes"
- Both /api/search and /api/ask are affected (RAG context gets flooded too)

---

## Decisions Made

| Question | Decision |
|---|---|
| Granularity | One result per **scene** — 30s temporal window, multiple scenes per file allowed |
| Where | **API layer only** — fixes both /api/search output and /api/ask RAG context |
| Compat | **Breaking default** — limit now means "number of scenes", not frames |

---

## Core Algorithm: Two-Layer Deduplication

The original plan used a single Python pass with a 30s window. That was wrong on
both counts — the window is too broad, and doing it purely in Python requires an
expensive over-fetch. The right approach uses **two layers** that solve different
problems.

---

### Layer 1 — Qdrant Native `search_groups` (per-file, DB-level)

Qdrant's Grouping API groups results by a payload field and returns the top-scoring
frame(s) per group — done entirely inside the vector DB before any data leaves the
server.

```python
from qdrant_client.models import SearchParams

groups_result = qdrant_client.search_groups(
    collection_name=QDRANT_COLLECTION_NAME,
    query_vector=query_vector,
    group_by="file_path",       # one group per file
    limit=body.limit,           # number of distinct files to return
    group_size=GROUP_SIZE,      # frames per file to return for Layer 2
    score_threshold=body.threshold,
    with_payload=True,
)
# groups_result.groups: list of GroupsResult, each with .id and .hits
```

**`GROUP_SIZE`** (env var, default `3`): how many candidate frames per file are
forwarded to Layer 2. Setting it to 1 would give exactly one result per file —
useful as an opt-in "file mode".

**What this handles**: eliminates the over-fetch problem entirely. Qdrant does the
group work; we only receive `limit × GROUP_SIZE` frames total.

---

### Layer 2 — Python 5-Second Temporal Windowing (per-file, event-level)

Within each group (file), the `GROUP_SIZE` candidate frames may still be from the
same 2-second stretch of footage — visually identical. Layer 2 collapses those.

```
Input:  GROUP_SIZE frames for one file, sorted by score desc
Output: frames where no two are within EVENT_WINDOW_SECONDS of each other

For each frame (best score first):
  bucket = int(timestamp // EVENT_WINDOW_SECONDS)
  if bucket already seen for this file → drop
  else → keep as event representative
```

**`EVENT_WINDOW_SECONDS`** (env var, default `5`): the "same event" threshold.

**Why 5s?** At 0.5 FPS there are 2–3 frames per 5s clip. Those frames capture
the same moment from nearly identical angles — returning all of them is noise.
5s is tight enough to collapse near-duplicates without merging genuinely distinct
scenes.

**Why not 30s at this layer?** 30s is a valid "scene segment" concept but it
belongs to a future clustering feature, not basic dedup. Conflating the two was
the error in the original plan.

---

### Combined Flow

```
User: limit=10, threshold=0.25

→ Qdrant search_groups(group_by="file_path", limit=10, group_size=3)
    Returns: up to 10 files × 3 frames = 30 frames total

→ Python 5s windowing per file
    Each file's 3 candidates → collapse adjacent frames → 1–3 distinct events

→ Flatten groups → sort by best-frame score desc → return top 10
```

**Worst case**: one file dominates the top 10 groups and all its candidate frames
collapse to 1 event each. Result: 10 results, one per file. This is correct.

**Best case**: 10 files, each with 3 distinct events → up to 30 results available,
return top 10 by score.

---

## File Changes

### 1. `api/routers/search.py`

**Changes:**
- Add `EVENT_WINDOW_SECONDS` and `SEARCH_GROUP_SIZE` env var reads at module level
- Replace `qdrant_client.query_points()` call with `qdrant_client.search_groups()`
- Add `_event_deduplicate(hits, window_s)` pure function (Layer 2)
- Add flatten + re-sort logic after group dedup
- Add `scenes_collapsed` and `raw_frame_count` to `SearchResponse`
- Add `scene_window_start` / `scene_window_end` to `SearchResult`

**Updated SearchResult** — add one field:
```python
class SearchResult(BaseModel):
    file_path: str
    file_type: str
    similarity: float
    frame_index: Optional[int] = None
    timestamp: Optional[float] = None
    scene_window_start: Optional[float] = None  # NEW: window start (timestamp rounded down)
    scene_window_end: Optional[float] = None    # NEW: window end (start + 30s)
```

**Updated SearchResponse** — add metadata:
```python
class SearchResponse(BaseModel):
    query: str
    results: list
    count: int
    execution_time_ms: float
    scenes_collapsed: int   # NEW: frames dropped by windowing
    raw_frame_count: int    # NEW: frames Qdrant returned before dedup
```

### 2. `api/routers/ask.py`

**Changes:**
- Import and reuse `_window_deduplicate` from `routers.search`
- Apply same over-fetch + dedup before `_build_context()`
- `_build_context()` already handles timestamps correctly — no changes needed there
- Update `AskResponse` to include `scenes_collapsed` for transparency

### 3. `api/tests/test_search.py`

Add test cases for:
- Single video, 5 frames within one 30s window → collapsed to 1 result
- Single video, 3 frames across 3 separate windows → 3 results kept
- Mixed: 2 images + 1 video (3 frames, 1 window) → 3 results (2 images + 1 scene)
- `scenes_collapsed` field equals (raw count - returned count)
- `scene_window_start` / `scene_window_end` are set on video results
- Images (no timestamp) are never collapsed
- Over-fetch: limit=2 still returns correct top-2 scenes from a large pool

### 4. `api/tests/test_ask.py`

Add test case:
- Multiple frames from same scene → LLM context shows 1 entry, not N

---

## Implementation Sketches

### Layer 1 — Qdrant `search_groups` call

```python
GROUP_SIZE = int(os.getenv("SEARCH_GROUP_SIZE", "3"))
EVENT_WINDOW_S = float(os.getenv("EVENT_WINDOW_SECONDS", "5"))

groups_result = qdrant_client.search_groups(
    collection_name=QDRANT_COLLECTION_NAME,
    query_vector=query_vector,
    group_by="file_path",
    limit=body.limit,         # number of distinct files
    group_size=GROUP_SIZE,    # candidates per file for Layer 2
    score_threshold=body.threshold,
    with_payload=True,
)
```

### Layer 2 — 5s event windowing per group

```python
def _event_deduplicate(hits: list, window_s: float = 5.0) -> list:
    """
    From a single file's candidate frames, keep only one frame per EVENT_WINDOW_SECONDS.
    The representative for each bucket is the highest-scoring frame in that window.

    Hits are explicitly sorted by score descending before the bucket walk so that
    the first frame to claim a bucket is always the AI's most confident match —
    regardless of whether Qdrant already sorted the group internally.
    Images (timestamp=None) are always kept.
    """
    # Explicit sort: highest-confidence frame wins each 5s bucket.
    hits = sorted(hits, key=lambda h: h.score, reverse=True)

    seen_buckets: set[int] = set()
    results = []
    for hit in hits:
        ts = hit.payload.get("timestamp")
        if ts is None:                          # image — no temporal axis
            results.append(hit)
            continue
        bucket = int(ts // window_s)
        if bucket in seen_buckets:
            continue
        seen_buckets.add(bucket)
        results.append(hit)
    return results


# Flatten groups after dedup
all_results = []
for group in groups_result.groups:
    best_frames = _event_deduplicate(group.hits, window_s=EVENT_WINDOW_S)
    all_results.extend(best_frames)

# Re-sort by score across files, return top limit
all_results.sort(key=lambda p: p.score, reverse=True)
return all_results[:body.limit]
```

**Time complexity**: O(G × K) where G = groups returned, K = GROUP_SIZE — tiny.
**No over-fetch**: Qdrant handles group limiting internally.

---

## Environment Variables (New)

| Variable | Default | Purpose |
|---|---|---|
| `EVENT_WINDOW_SECONDS` | `5` | Layer 2: "same event" temporal window |
| `SEARCH_GROUP_SIZE` | `3` | Layer 1: candidate frames per file from Qdrant |

Both live in `.env` and `docker-compose.yml` environment blocks.

**Removed**: `SCENE_WINDOW_SECONDS=30` and `SEARCH_OVERFETCH_MULTIPLIER=5` — replaced
by the two-layer approach. No more over-fetch.

## A/B Comparison: `dedup` Query Parameter

For case study screenshots and before/after validation, both views must be
accessible in the same browser session without any container restart.

**Approach: opt-out query param on `/api/search`**

```
POST /api/search { "query": "sunset", "limit": 20, "dedup": true }   ← grouped (default)
POST /api/search { "query": "sunset", "limit": 20, "dedup": false }  ← raw frames
```

`dedup=false` bypasses both Layer 1 (`search_groups`) and Layer 2 (event
windowing) and falls back to the original `query_points` call. Zero logic
change to the rest of the response — same schema, same fields, just
`scenes_collapsed=0` and `raw_frame_count=count`.

**Why not ENV var?** ENV applies at deploy time and requires a container
restart to switch. You can't take before/after screenshots in the same
session. Query param flips state per-request — flip, screenshot, flip back.

**Why not frontend-only toggle?** Frontend grouping wouldn't fix the RAG
context in `/api/ask`, so the before/after wouldn't be a true comparison
of the system.

### `SearchRequest` model update

```python
class SearchRequest(BaseModel):
    query: str
    limit: int = 20
    threshold: float = 0.25
    dedup: bool = True   # NEW: False = raw frames (A/B / debug mode)
```

### Frontend toggle (minimal)

One button in the search bar area — no new page, no routing change:

```tsx
// In SearchBar or results header
<button onClick={() => setDedup(d => !d)}>
  {dedup ? "Grouped scenes" : "Raw frames"}
</button>
// Passes dedup flag to POST /api/search body
```

The toggle state is local (not persisted) — intentional, so it resets on
page refresh and the default is always the polished grouped view.

### Screenshot workflow

1. Search any query with default (`dedup=true`) → screenshot "After"
2. Click toggle → same query re-fires with `dedup=false` → screenshot "Before"
3. Same results set, same similarity scores — only the grouping differs

---

No Qdrant schema changes. No re-indexing required. No worker changes.
This is a pure API-layer read-path change.

**Deploy sequence:**
1. Merge this branch
2. Deploy API container (rolling restart, zero downtime)
3. Done — existing indexed media immediately benefits

---

## Edge Cases

| Case | Behaviour |
|---|---|
| Video shorter than 5s (e.g. 3s clip) | All frames in same bucket → 1 result (correct) |
| Image results | `timestamp=None` → never deduplicated, always passed through |
| `limit` > distinct files in Qdrant | Returns fewer than `limit` (correct — no padding) |
| Two frames in same file, 6s apart | Both kept (6 > 5s window) |
| Two frames in same file, 3s apart | Only higher-scoring one kept (3 < 5s window) |
| `GROUP_SIZE=1` | One result per file ("file mode") — skips Layer 2 |
| `EVENT_WINDOW_SECONDS=0` | No Layer 2 dedup — returns raw group hits |

---

## What This Unlocks

1. `limit=10` reliably returns 10 *distinct scenes/files*, not 10 frames from 1 video
2. RAG context in `/api/ask` references diverse content → better LLM answers
3. Frontend grid shows varied thumbnails without repeated file names
4. Sets up Phase 2: `scene_window_start/end` enables the frontend to show a
   "play scene" button that seeks directly to the matched moment

---

## Open Questions / Refinements

- Should `group_by_file=true` be a separate opt-in param on top of scene windowing?
- Should `SCENE_WINDOW_SECONDS` be overridable per-request (query param)?
- For the frontend: show a "N more frames in this scene" expand affordance?
- Rate limit for /api/search may need adjustment if over-fetch increases Qdrant load
