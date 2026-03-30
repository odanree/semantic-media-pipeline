# [STAR] Vote Label Architecture & SearchContext: Eliminating Prop Drilling Through React Context

## ⭐ Top 3 Behaviors Demonstrated

1. **Context over Prop Drilling**: Identified that threading `searchQuery` through components that don't own it is a coupling smell — replaced with React Context so consumers read directly from the source of truth
2. **Append-Only Semantic Index**: Designed `vote_label` as a permanent `{query: score}` dict in Qdrant — separating the *active ranking signal* (`user_vote`) from the *accumulated knowledge index* (`vote_label`), ensuring label history survives vote toggles
3. **Proportional Signal Design**: Made re-ranking proportional to cascade similarity score rather than flat — frames cascaded at 0.91 similarity get a weaker boost than those at 0.99, preserving the semantic meaning of the original score

---

## Situation

After implementing the vote observability system, the `search_query` from the user's active search needed to reach the `/api/vote` payload so the backend could store it as a `vote_label` in Qdrant — enabling frames to be tagged with the search term that triggered the upvote (e.g. `{"labubu": 0.93}`).

The initial solution threaded `searchQuery` as a prop:

```
page.tsx  →  ResultGrid (searchQuery prop)  →  useVotes({ searchQuery })
```

**Problem 1: Prop drilling**
`ResultGrid` has no use for `searchQuery` itself. It existed purely as a pass-through. Any component between the query source and the consumer would need to add this prop — a symptom of the wrong abstraction layer owning the data.

**Problem 2: vote_label was a plain string**
The first implementation stored `vote_label: "labubu"` — a single overwritable string. Upvoting a frame for "monstars" after "labubu" would silently erase the previous label. Accumulated knowledge from past votes was destroyed on each new upvote.

**Problem 3: vote_label deleted on vote clear**
Clearing `user_vote` (toggle off) also deleted `vote_label`, treating them as a single unit. But they represent different things: the active ranking signal vs. the permanent semantic index.

**Problem 4: Flat re-rank boost**
The re-ranker applied `+0.08` uniformly to all upvoted frames regardless of whether the cascade similarity was 0.91 or 0.99. A frame barely above the 90% threshold was boosted identically to one at 99% — losing the signal from the original similarity score.

---

## Task

Design the architecture so that:

1. **`searchQuery` flows to `useVotes` without passing through intermediate components** — the hook should read it from a shared context
2. **`vote_label` accumulates over time** — append-only dict `{query: score}`, never overwrites existing entries, takes max if same query is re-applied
3. **Clearing a vote preserves `vote_label`** — `user_vote` and `vote_label` have independent lifecycles
4. **Re-rank boost is proportional to cascade similarity score** — frames with higher visual similarity to the source get a stronger boost

---

## Action

### Fix 1: React Context for Search Query

Created a minimal context that holds the current search query and is readable from anywhere in the tree:

```typescript
// frontend/context/SearchContext.tsx
'use client'
import { createContext, useContext } from 'react'

const SearchContext = createContext<string>('')

export const SearchProvider = SearchContext.Provider

export function useSearchQuery(): string {
  return useContext(SearchContext)
}
```

`page.tsx` wraps its return in `<SearchProvider value={query}>`. `useVotes` calls `useSearchQuery()` directly:

```typescript
// frontend/hooks/useVotes.ts (after)
export function useVotes(options: UseVotesOptions = {}) {
  const searchQuery = useSearchQuery()   // ← reads from context, no prop needed
  // ...
  await api.persist(filePath, audioSegmentIndex, voteValue, searchQuery || undefined)
}
```

**Before vs. After:**
```
Before:
  page.tsx  →[searchQuery prop]→  ResultGrid  →[searchQuery prop]→  useVotes

After:
  page.tsx wraps in <SearchProvider value={query}>
  useVotes calls useSearchQuery() — ResultGrid knows nothing
```

`ResultGrid`'s props interface lost `searchQuery` entirely. No intermediate component needed to know the query existed.

### Fix 2: vote_label as append-only `{query: score}` dict

**Before (plain string — overwrites):**
```python
# Qdrant payload after "labubu" upvote, then "monstars" cascade
{"user_vote": 1, "vote_label": "monstars"}  # ❌ "labubu" erased
```

**After (dict — accumulates):**
```python
# Same sequence
{"user_vote": 1, "vote_label": {"labubu": 1.0, "monstars": 0.93}}  # ✅ both preserved
```

Manual upvote sets the query key to `1.0` (exact match). Cascade sets it to `r.score` (similarity to source frame). If a query is re-applied, the higher score wins:

```python
# worker/tasks.py — cascade_votes task
current_labels: dict = (r.payload or {}).get("vote_label") or {}
updated_labels[search_query] = max(
    float(updated_labels.get(search_query, 0.0)),
    float(r.score),
)
```

Qdrant supports filtering on nested dict fields: `FieldCondition(key="vote_label.labubu", range=Range(gt=0))` finds all frames ever tagged for "labubu".

### Fix 3: Independent lifecycles for user_vote and vote_label

| Action | `user_vote` | `vote_label` |
|--------|------------|-------------|
| Upvote "labubu" | `1` | `{"labubu": 1.0}` |
| Cascade at 0.93 similarity | `1` | `{"labubu": 0.93}` |
| Upvote "monstars" later | `1` | `{"labubu": 0.93, "monstars": 1.0}` |
| **Toggle vote off** | *(deleted)* | `{"labubu": 0.93, "monstars": 1.0}` ← **untouched** |
| Re-upvote "labubu" | `1` | `{"labubu": 1.0, "monstars": 1.0}` |

```python
# api/routers/search.py — set_vote, vote == 0
qdrant_client.delete_payload(
    collection_name=QDRANT_COLLECTION_NAME,
    keys=["user_vote"],        # ← only user_vote deleted
    points=points_selector,    # vote_label preserved
)
```

`vote_label` is a growing semantic index. `user_vote` is the current on/off ranking switch.

### Fix 4: Proportional re-rank boost

**Before (flat):**
```python
if vote == 1:
    p.score = min(1.0, float(p.score) + VOTE_BOOST)  # +0.08 regardless of origin
```

**After (proportional to query match and cascade similarity):**
```python
def _apply_vote_adjustment(points: list, query: Optional[str] = None) -> list:
    for p in points:
        vote = p.payload.get("user_vote")
        if vote == 1:
            vote_label = p.payload.get("vote_label") or {}
            if query and query in vote_label:
                boost = VOTE_BOOST * float(vote_label[query])  # e.g. 0.08 × 0.93
            else:
                boost = VOTE_BOOST * 0.5  # liked for different query — half boost
            p.score = min(1.0, float(p.score) + boost)
```

A frame cascaded at 0.99 similarity for the active query gets `0.08 × 0.99 = 0.079` boost. One at 0.91 gets `0.08 × 0.91 = 0.073`. A frame liked under a different query gets `0.08 × 0.5 = 0.04` — still surfaced, but deprioritized relative to on-query matches.

---

## Boundary Insights

### 1. Data Ownership Boundary (Context vs. Props)

The prop-drilling smell surfaces when a component becomes a courier for data it doesn't consume:

```
page.tsx          owns query state
  ↓ (prop)
ResultGrid        doesn't use query — passes it along
  ↓ (prop)
useVotes          actually uses query
```

The fix: identify who *owns* the data (page.tsx) and who *consumes* it (useVotes). Skip all intermediaries with a context. The boundary is between the owner and consumer — everything between is irrelevant.

### 2. Signal vs. Index Boundary

`user_vote` and `vote_label` look related but serve different concerns at different timescales:

```
user_vote  →  ephemeral ranking signal  →  changes often (toggle)
vote_label →  permanent knowledge index →  only grows, never shrinks
```

Deleting both together at vote-clear time treats them as the same entity. Recognizing the boundary — transient signal vs. durable index — is what preserved label history across vote toggles.

### 3. Score Boundary: ANN Similarity vs. Re-rank Boost

The cascade task uses `query_points(score_threshold=0.9)` to find visually similar frames. Those scores (0.91–1.0) represent semantic visual distance from the source frame. Storing them in `vote_label` and reusing them as re-rank weights closes a feedback loop: the original vector similarity score becomes the magnitude of the future ranking adjustment.

```
query_points() score  →  stored in vote_label[query]
                       →  re-ranker applies VOTE_BOOST × vote_label[query]
```

This avoids designing a separate "cascade weight" field — the information was already there in the search result.

---

## Result

✅ **No prop drilling**: `ResultGrid` has no knowledge of `searchQuery` — context handles it transparently
✅ **Permanent label history**: `vote_label` survives vote toggles, accumulates across sessions
✅ **Correct lineage on re-trigger**: toggling a vote off then back on correctly re-fires cascade with `search_query` populated (confirmed via Postgres `vote_events` table)
✅ **Proportional ranking**: frames cascaded at higher similarity rank more strongly for the matching query
✅ **Qdrant filterable**: `vote_label.labubu` queryable as a nested field for future dataset export

### Verification (from Postgres vote_events)

```sql
-- Before fix: search_query NULL on all 3 votes
SELECT vote_source, search_query, cascaded_count FROM vote_events;
 manual | NULL | 0   ← worker had old code, query not sent
 manual | NULL | 0
 manual | NULL | 0

-- After fix (re-triggered votes)
 manual | labubu | 47  ← cascaded_count updated by cascade_votes task
 manual | labubu | 23
 manual | labubu | 31
```

---

## Interview Talking Points

1. **Prop Drilling Recognition**: Identified the coupling smell when `ResultGrid` received a prop purely to forward it — an intermediate component as a courier is a signal the data belongs in a shared context
2. **Append-Only Data Design**: Designed `vote_label` as an immutable-growing dict rather than a mutable string — treated it like an event log, not a field. New entries are added; old ones are never modified
3. **Two Invariants, One Payload**: Distinguished `user_vote` (transient) from `vote_label` (permanent) within the same Qdrant payload, each with its own lifecycle and deletion semantics
4. **Feedback Loop Reuse**: Avoided designing a separate "cascade weight" field by recognizing that `query_points()` scores were already the right signal — stored them in `vote_label[query]` and reused them downstream in the re-ranker
5. **Debugged Via Lineage**: Traced a real production failure (`cascade_votes` rejected by old worker + NULL `search_query` in vote_events) back to two root causes — stale container code and missing context wire-up — using Postgres VoteEvent rows and Celery worker logs as the diagnostic trail

**Example answerable question**: *"What would you do if the vote label dict grew unbounded for a frequently searched frame?"*
**Answer**: Cap at N entries by score — `sorted(labels.items(), key=lambda x: x[1], reverse=True)[:20]` — keeping only the highest-confidence query associations. Or separate "active labels" (top-k) from "archive" (all history in Postgres via VoteEvents).
