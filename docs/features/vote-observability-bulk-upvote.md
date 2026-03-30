# Vote Observability & Bulk Upvote System

**Overview:** Track vote lineage with batch IDs, bulk upvote similar search results, and query statistics on how one upvote generated labeled training data.

---

## Problem Statement

When building a CLIP fine-tuning dataset:
- Manual labeling is tedious (1 label per frame)
- Text-to-image matching misses semantically similar frames (e.g., "labubu" query doesn't find visually identical frame from different angle)
- Need to track: "I upvoted 1 frame, how many did that generate downstream?"

**Solution:** Leverage visual similarity to bulk-label semantically coherent clusters, with full observability of the cascade.

---

## Architecture

### Vote Batch Lineage

```
User searches "labubu"
         ↓
Finds & upvotes PXL_20250919_035230257.mp4
         ↓ (creates batch_id: ABC-123)
         ├─ Vote logged: vote_source="manual", batch_id=ABC-123
         │
User runs similar(PXL_20250919_035230257.mp4)
         ↓
Gets 20 results at 90%+ similarity
         ↓
Bulk upvotes all 20 (inherits batch_id: ABC-123)
         ↓
         ├─ Vote #2 logged: vote_source="bulk_upvote", batch_id=ABC-123, search_query="labubu"
         ├─ Vote #3 logged: vote_source="bulk_upvote", batch_id=ABC-123, search_query="labubu"
         ├─ ... (20 total)
         │
Database now contains:
  - 1 seed vote (manual)
  - 20 cascading votes (bulk)
  - All grouped by batch_id=ABC-123 for queries
```

### Data Model

**vote_events table:**
```sql
id                      UUID (primary key)
batch_id                UUID (seed or inherited — groups related votes)
triggered_by_batch_id   UUID (NULL for seed votes, batch_id of triggering vote for cascades)
file_path               TEXT (indexed)
audio_segment_index     INTEGER (indexed)
vote                    INTEGER (1, -1, 0)
vote_source             VARCHAR(20) ('manual' | 'bulk_upvote' | 'auto_label')
search_query            VARCHAR(512) (e.g., "labubu" — inherited by bulk votes)
similarity_score        FLOAT (for bulk votes: similarity to seed frame, e.g., 0.95)
timestamp               TIMESTAMP
cascaded_count          INTEGER (set on seed vote: how many bulk votes resulted)
cascade_threshold       FLOAT (min similarity for bulk, e.g., 0.90)
```

**Indexes:**
- `(batch_id)` — fast lookup of all votes in a batch
- `(search_query)` — group by query for statistics
- `(timestamp)` — time-series queries
- `(batch_id, vote_source)` — separate seed from cascades
- `(file_path, audio_segment_index)` — verify what was upvoted

---

## API Endpoints

### POST /api/vote (Enhanced)

**Single manual upvote or clear vote.**

```json
{
  "file_path": "/mnt/source/Pixel 9 Oct 2025/PXL_20250919_035230257.mp4",
  "audio_segment_index": 0,
  "vote": 1,
  "search_query": "labubu",
  "batch_id": null
}
```

**Response:**
```json
{
  "patched": 1,
  "file_path": "/mnt/source/Pixel 9 Oct 2025/PXL_20250919_035230257.mp4",
  "audio_segment_index": 0,
  "vote": 1,
  "batch_id": "550e8400-e29b-41d4-a716-446655440000"
}
```

**Notes:**
- If `batch_id` is NULL → creates new batch (seed vote)
- If `batch_id` is provided → inherits batch (bulk vote, cascading)
- `search_query` is captured for training data generation (e.g., auto-label="labubu")
- Logged asynchronously (non-blocking)

---

### POST /api/vote/bulk (New)

**Bulk upvote multiple results from similar search.**

```json
{
  "file_paths": [
    "/mnt/source/Pre-Dec 2025/PXL_20250919_035130152.mp4",
    "/mnt/source/Pixel 9 Nov 2025/PXL_20250919_035148731.mp4",
    "/mnt/source/Pixel 9 July 2025/PXL_20250716_002531515.mp4"
  ],
  "audio_segment_indices": [0, 0, 0],
  "vote": 1,
  "search_query": "labubu",
  "batch_id": "550e8400-e29b-41d4-a716-446655440000"
}
```

**Response:**
```json
{
  "batch_id": "550e8400-e29b-41d4-a716-446655440000",
  "total_patched": 20,
  "breakdown": {
    "/mnt/source/Pre-Dec 2025/PXL_20250919_035130152.mp4": 3,
    "/mnt/source/Pixel 9 Nov 2025/PXL_20250919_035148731.mp4": 5,
    ...
  },
  "search_query": "labubu"
}
```

**Behavior:**
- Inherits `batch_id` from seed vote → all cascading votes grouped together
- Inherits `search_query` → all bulk votes labeled with context ("labubu")
- Continues on error per file (one failure doesn't stop batch)
- Each vote logged with `vote_source="bulk_upvote"`

---

### GET /api/votes/batch/{batch_id}

**Get statistics for a single upvote batch.**

```bash
curl http://localhost:8000/api/votes/batch/550e8400-e29b-41d4-a716-446655440000
```

**Response:**
```json
{
  "batch_id": "550e8400-e29b-41d4-a716-446655440000",
  "total_votes": 21,
  "seed_votes": 1,
  "cascaded_votes": 20,
  "breakdown": {
    "by_query": {
      "labubu": 21
    },
    "by_source": {
      "manual": 1,
      "bulk_upvote": 20
    }
  }
}
```

**Use case:** After upvoting, check impact: "My 1 upvote generated 20 labeled frames."

---

### GET /api/votes/stats?days=7&query=labubu

**Get aggregate vote statistics.**

Query parameters:
- `days` (int, default 7): lookback window
- `query` (str, optional): filter by search_query

**Response:**
```json
{
  "period_days": 7,
  "total_votes": 500,
  "total_batches": 50,
  "avg_cascade_ratio": 10.0,
  "top_queries": [
    {
      "query": "labubu",
      "batches": 8,
      "votes": 160,
      "avg_cascade": 20.0
    },
    {
      "query": "toy",
      "batches": 5,
      "votes": 100,
      "avg_cascade": 20.0
    },
    {
      "query": "construction",
      "batches": 10,
      "votes": 140,
      "avg_cascade": 14.0
    }
  ]
}
```

**Interpretation:**
- **avg_cascade_ratio**: on average, 1 seed upvote → 10 labeled frames
- **top_queries**: "labubu" generated 160 labeled frames across 8 batches (most efficient)
- Use to identify which queries are generating the most training data

---

## Frontend Integration

### Complete Workflow

```typescript
// Step 1: Search for content
const searchRes = await fetch('/api/search', {
  method: 'POST',
  body: JSON.stringify({ query: 'labubu', limit: 20 })
})

const goodFrame = searchRes.results[0]
console.log('Found:', goodFrame.file_path, 'at', goodFrame.similarity)

// Step 2: Upvote the best example (seed vote)
const voteRes = await fetch('/api/vote', {
  method: 'POST',
  body: JSON.stringify({
    file_path: goodFrame.file_path,
    audio_segment_index: goodFrame.audio_segment_index,
    vote: 1,
    search_query: 'labubu'
    // batch_id not provided → creates new batch
  })
})

const batchId = voteRes.batch_id
console.log('Seed vote created, batch_id:', batchId)

// Step 3: Run similar search on seed frame
const similarRes = await fetch('/api/similar', {
  method: 'POST',
  body: JSON.stringify({
    file_path: goodFrame.file_path,
    timestamp: goodFrame.timestamp,
    limit: 100  // Get all similar frames
  })
})

// Filter to 90%+ similarity
const similarFrames = similarRes.results.filter(r => r.best_similarity >= 0.90)
console.log(`Found ${similarFrames.length} frames at 90%+ similarity`)

// Step 4: Bulk upvote all similar frames
const bulkRes = await fetch('/api/vote/bulk', {
  method: 'POST',
  body: JSON.stringify({
    file_paths: similarFrames.map(r => r.file_path),
    audio_segment_indices: similarFrames.map(r => r.audio_segment_index),
    vote: 1,
    search_query: 'labubu',
    batch_id: batchId  // ← Inherit from seed
  })
})

console.log(`Bulk upvoted ${bulkRes.total_patched} frames`)

// Step 5: Check impact
const statsRes = await fetch(`/api/votes/batch/${batchId}`)
console.log(`1 upvote → ${statsRes.cascaded_votes} labeled frames`)
```

### UI Suggestions

After upvoting, show:
```
✓ Upvoted PXL_20250919_035230257.mp4 for "labubu"

Suggested next action:
  1. Open Similar Search for this frame
  2. Review results (should all be visually identical)
  3. Click "Bulk Upvote All 90%+" button

[Open Similar] [Bulk Upvote 20 Results] [View Stats]
```

Stats panel:
```
Vote Statistics
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
This Week:
  • Total batches: 12
  • Total labeled frames: 156
  • Top query: "labubu" (8 batches, 160 frames)
  • Avg cascade: 13 frames per batch

[Detailed Stats] [Export for Training]
```

---

## Database Migration

### SQL Setup

```sql
-- Run once to create vote_events table
CREATE TABLE vote_events (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    batch_id UUID NOT NULL,
    triggered_by_batch_id UUID,
    file_path TEXT NOT NULL,
    audio_segment_index INTEGER,
    vote INTEGER NOT NULL,
    vote_source VARCHAR(20) NOT NULL DEFAULT 'manual',
    search_query VARCHAR(512),
    similarity_score FLOAT,
    timestamp TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP NOT NULL,
    cascaded_count INTEGER DEFAULT 0,
    cascade_threshold FLOAT
);

CREATE INDEX idx_vote_batch_id ON vote_events(batch_id);
CREATE INDEX idx_vote_triggered_by ON vote_events(triggered_by_batch_id);
CREATE INDEX idx_vote_file_path ON vote_events(file_path);
CREATE INDEX idx_vote_audio_segment ON vote_events(audio_segment_index);
CREATE INDEX idx_vote_source ON vote_events(vote_source);
CREATE INDEX idx_vote_query ON vote_events(search_query);
CREATE INDEX idx_vote_timestamp ON vote_events(timestamp);
CREATE INDEX idx_vote_batch_source ON vote_events(batch_id, vote_source);
CREATE INDEX idx_vote_query_source ON vote_events(search_query, vote_source);
CREATE INDEX idx_vote_batch_timestamp ON vote_events(batch_id, timestamp);
```

### Apply Migration

```bash
# Via psql
psql -U lumen_user -d lumen < scripts/migrate_add_vote_events.sql

# Or via Python (in api/main.py startup)
from sqlalchemy import text
from db.models import Base, get_async_engine

async def init_db():
    engine = await get_async_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

# In main.py startup event:
@app.on_event("startup")
async def startup():
    await init_db()
```

---

## Training Data Generation

### Extract Labeled Frames from Votes

```python
from sqlalchemy import select
from db.models import VoteEvent, get_async_engine
from datetime import datetime, timedelta
import json

async def export_training_data(search_query: str = "labubu", min_votes: int = 1):
    """
    Export all upvoted frames for a query as CLIP fine-tuning dataset.

    Usage:
      dataset = await export_training_data("labubu")
      # → [
      #     {"image_path": "/mnt/source/...", "text": "labubu", "vote_count": 3},
      #     ...
      #   ]
    """
    engine = await get_async_engine()
    async with engine.begin() as conn:
        result = await conn.execute(
            select(
                VoteEvent.file_path,
                VoteEvent.audio_segment_index,
                VoteEvent.search_query,
                func.count(VoteEvent.id).label('vote_count')
            ).where(
                (VoteEvent.vote == 1) &
                (VoteEvent.search_query == search_query)
            ).group_by(
                VoteEvent.file_path,
                VoteEvent.audio_segment_index,
                VoteEvent.search_query
            ).having(
                func.count(VoteEvent.id) >= min_votes
            )
        )

        training_data = []
        for row in result.fetchall():
            training_data.append({
                "file_path": row.file_path,
                "audio_segment_index": row.audio_segment_index,
                "text": row.search_query,
                "vote_count": row.vote_count,  # Confidence signal
            })

        return training_data

# Export for CLIP fine-tuning
dataset = await export_training_data("labubu")
with open("training_labubu.json", "w") as f:
    json.dump(dataset, f)

# Then use with:
# CLIP fine-tuning loader → reads {"image_path": ..., "text": "labubu"}
# Votes become weak supervision (frames with 3 votes = high confidence)
```

---

## Example: Accelerated Labeling Session

### Scenario
You want to build training data for 3 concepts: "labubu" (toys), "construction", "bedroom"

### Timeline

```
Time     Action                                  Result
─────────────────────────────────────────────────────────
0:00     Search "labubu"                        23 results (0.28 similarity avg)
         Upvote best: PXL_20250919_035230257   batch_id=ABC

0:01     Similar(PXL_20250919_035230257)       20 results at 0.95+ similarity
         Bulk upvote all                        21 frames labeled "labubu"

0:02     Search "construction"                  18 results
         Upvote best: PXL_20251017_233741      batch_id=DEF

0:03     Similar(PXL_20251017_233741)          15 results at 0.92+ similarity
         Bulk upvote all                        16 frames labeled "construction"

0:04     Search "bedroom"                       12 results
         Upvote best: PXL_20251111_000456      batch_id=GHI

0:05     Similar(PXL_20251111_000456)          18 results at 0.88+ similarity
         Bulk upvote all                        19 frames labeled "bedroom"

Total elapsed: 5 minutes
Training dataset: 56 labeled frames (21 + 16 + 19)
Manual effort: 3 searches + 3 upvotes + 3 bulk actions = ~1 minute of clicks

Query stats after:
  curl /api/votes/stats?days=1
  → {
      "total_votes": 56,
      "total_batches": 3,
      "top_queries": [
        {"query": "labubu", "votes": 21, "avg_cascade": 21},
        {"query": "construction", "votes": 16, "avg_cascade": 16},
        {"query": "bedroom", "votes": 19, "avg_cascade": 19}
      ]
    }
```

---

## Reference: Common Queries

### "How many frames did I label today?"
```sql
SELECT COUNT(*) as frame_count
FROM vote_events
WHERE vote = 1
  AND timestamp > NOW() - INTERVAL '1 day'
  AND vote_source IN ('manual', 'bulk_upvote');
```

### "What queries generated the most training data this week?"
```sql
SELECT
  search_query,
  COUNT(DISTINCT batch_id) as batch_count,
  COUNT(*) as total_frames,
  ROUND(COUNT(*)::float / COUNT(DISTINCT batch_id), 1) as avg_cascade
FROM vote_events
WHERE vote = 1
  AND timestamp > NOW() - INTERVAL '7 days'
GROUP BY search_query
ORDER BY total_frames DESC;
```

### "Get all labeled frames for 'labubu' to export for fine-tuning"
```sql
SELECT DISTINCT
  file_path,
  audio_segment_index,
  search_query
FROM vote_events
WHERE vote = 1
  AND search_query = 'labubu'
  AND vote_source IN ('manual', 'bulk_upvote')
ORDER BY timestamp DESC;
```

### "Which batches were most efficient (generated most frames)?"
```sql
SELECT
  batch_id,
  search_query,
  COUNT(*) as cascaded_frames,
  MIN(timestamp) as created_at
FROM vote_events
WHERE vote_source = 'bulk_upvote'
GROUP BY batch_id, search_query
ORDER BY cascaded_frames DESC
LIMIT 10;
```

---

## Performance Considerations

### Async Logging
- Vote endpoint returns immediately (batch_id generated)
- VoteEvent logging happens async (non-blocking)
- If logging fails, vote still persists in Qdrant (vote endpoint succeeds)

### Indexing
- `batch_id` indexed for fast batch statistics
- `search_query` indexed for query aggregation
- `timestamp` indexed for time-series queries
- Composite indexes for common joins

### Bulk Insert Optimization
```python
# When importing training data, batch inserts:
async def bulk_import_votes(votes_list: List[dict]):
    engine = await get_async_engine()
    async with engine.begin() as conn:
        await conn.execute(
            insert(VoteEvent),
            votes_list  # Single INSERT with 1000+ rows
        )
```

---

## Troubleshooting

### "batch_id not found" when calling /vote/bulk
- Check that the manual /vote call returned successfully
- Verify batch_id was copied correctly
- Batch IDs are UUIDs, should be valid UUID format

### Bulk vote partial failures
- Endpoint continues on errors per file
- Check response `breakdown` for which files succeeded
- Re-run bulk_vote for failed file_paths

### Stats queries return 0 or unexpected numbers
- Verify migration was applied (`SELECT COUNT(*) FROM vote_events;` should return >0)
- Check timestamp filters (votes very recent won't show in 7-day window if system time is wrong)
- Ensure search_query is exact match (case-sensitive)

---

## Future Enhancements

- [ ] Auto-label query aggregation (group "toy", "action figure", "doll" → "toy")
- [ ] Negative votes as hard negatives for fine-tuning
- [ ] Cascade visualization (UI showing "1 upvote → 20 frames → used in training")
- [ ] Fine-tuning integration (auto-export → CLIP training pipeline)
- [ ] Confidence weighting (frames with multiple votes = higher weight)
