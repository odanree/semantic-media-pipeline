# Vote Observability: Quick Reference

**See also:** [vote-observability-bulk-upvote.md](vote-observability-bulk-upvote.md) for full documentation.

---

## Workflow (Copy-Paste Ready)

### Step 1: Manual Upvote (Get Batch ID)
```bash
curl -X POST http://localhost:8000/api/vote \
  -H "Content-Type: application/json" \
  -d '{
    "file_path": "/mnt/source/Pixel 9 Oct 2025/PXL_20250919_035230257.mp4",
    "audio_segment_index": 0,
    "vote": 1,
    "search_query": "labubu"
  }' | jq -r '.batch_id'

# Output: 550e8400-e29b-41d4-a716-446655440000
# Save this for step 3!
```

### Step 2: Similar Search (Get Results to Bulk Vote)
```bash
curl -X POST http://localhost:8000/api/similar \
  -H "Content-Type: application/json" \
  -d '{
    "file_path": "/mnt/source/Pixel 9 Oct 2025/PXL_20250919_035230257.mp4",
    "timestamp": 6,
    "limit": 100
  }' | jq '.results[] | select(.best_similarity >= 0.90) | {file_path, audio_segment_index}'

# Output:
# {
#   "file_path": "/mnt/source/Pre-Dec 2025/PXL_20250919_035130152.mp4",
#   "audio_segment_index": 0
# }
# {
#   "file_path": "/mnt/source/Pixel 9 Nov 2025/PXL_20250919_035148731.mp4",
#   "audio_segment_index": 0
# }
# ... (20 results)
```

### Step 3: Bulk Upvote (Use Batch ID from Step 1)
```bash
curl -X POST http://localhost:8000/api/vote/bulk \
  -H "Content-Type: application/json" \
  -d '{
    "file_paths": [
      "/mnt/source/Pre-Dec 2025/PXL_20250919_035130152.mp4",
      "/mnt/source/Pixel 9 Nov 2025/PXL_20250919_035148731.mp4",
      "/mnt/source/Pixel 9 July 2025/PXL_20250716_002531515.mp4"
    ],
    "audio_segment_indices": [0, 0, 0],
    "vote": 1,
    "search_query": "labubu",
    "batch_id": "550e8400-e29b-41d4-a716-446655440000"
  }'

# Output:
# {
#   "batch_id": "550e8400-e29b-41d4-a716-446655440000",
#   "total_patched": 20,
#   "breakdown": { ... },
#   "search_query": "labubu"
# }
```

### Step 4: Check Stats
```bash
# Single batch stats
curl http://localhost:8000/api/votes/batch/550e8400-e29b-41d4-a716-446655440000 | jq

# Output: 1 upvote → 20 labeled frames

# Overall stats (last 7 days)
curl http://localhost:8000/api/votes/stats?days=7 | jq

# Output: what queries generated the most training data
```

---

## API Reference

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/api/vote` | POST | Single upvote (seed) or inherit batch (bulk). Returns `batch_id`. |
| `/api/vote/bulk` | POST | Bulk upvote multiple files, inheriting batch_id from seed. |
| `/api/votes/batch/{batch_id}` | GET | Stats for one batch (seed votes + cascades). |
| `/api/votes/stats?days=7&query=labubu` | GET | Aggregate stats across all batches. |

---

## Database Schema (Key Fields)

```
vote_events
├── batch_id           UUID (groups seed + cascades)
├── vote_source        VARCHAR ('manual' or 'bulk_upvote')
├── search_query       VARCHAR (e.g., 'labubu')
├── file_path          TEXT
├── audio_segment_idx  INT
├── vote               INT (1, -1, 0)
└── timestamp          TIMESTAMP
```

---

## Common Queries (SQL)

### Total labeled frames this week
```sql
SELECT COUNT(*) FROM vote_events
WHERE vote = 1 AND timestamp > NOW() - INTERVAL '7 days';
```

### Frames labeled for "labubu"
```sql
SELECT DISTINCT file_path FROM vote_events
WHERE vote = 1 AND search_query = 'labubu';
```

### Efficiency: batches and frames
```sql
SELECT search_query, COUNT(DISTINCT batch_id) as batches, COUNT(*) as frames
FROM vote_events WHERE vote = 1
GROUP BY search_query ORDER BY frames DESC;
```

### Export training data for CLIP fine-tuning
```sql
SELECT file_path, audio_segment_index, search_query
FROM vote_events
WHERE vote = 1 AND search_query = 'labubu'
GROUP BY file_path, audio_segment_index, search_query;
```

---

## Setup (One-Time)

1. **Create table:**
   ```bash
   psql -U lumen_user -d lumen < scripts/migrate_add_vote_events.sql
   ```

2. **Rebuild containers:**
   ```bash
   docker-compose up -d --build
   ```

3. **Verify:**
   ```bash
   curl http://localhost:8000/api/search-status
   ```

---

## Troubleshooting

| Issue | Solution |
|-------|----------|
| `batch_id not found` | Check batch_id from /vote response matches /vote/bulk input |
| `Scene not found` | File may not be indexed; run ingest first |
| Stats show 0 | Run migration first (`psql < migrate_add_vote_events.sql`) |
| Bulk vote partial failure | Check response breakdown; retry failed files |

---

## Example: 5-Minute Labeling Session

```bash
# 1. Find & upvote
curl http://localhost:8000/api/search -d '{"query":"labubu"}' \
  | jq '.results[0] | {file_path, audio_segment_index}' > seed.json

SEED_FILE=$(jq -r .file_path seed.json)
SEED_INDEX=$(jq -r .audio_segment_index seed.json)

BATCH=$(curl -X POST http://localhost:8000/api/vote \
  -d "{\"file_path\":\"$SEED_FILE\",\"audio_segment_index\":$SEED_INDEX,\"vote\":1,\"search_query\":\"labubu\"}" \
  | jq -r '.batch_id')

echo "Batch ID: $BATCH"

# 2. Get similar
curl http://localhost:8000/api/similar \
  -d "{\"file_path\":\"$SEED_FILE\",\"timestamp\":0}" \
  | jq '.results[] | select(.best_similarity >= 0.90) | {file_path, audio_segment_index}' \
  > similar.json

# 3. Bulk upvote
FILES=$(jq -r '.file_path' similar.json | jq -R . | jq -s .)
INDICES=$(jq -r '.audio_segment_index' similar.json | jq -s .)

curl -X POST http://localhost:8000/api/vote/bulk \
  -d "{\"file_paths\":$FILES,\"audio_segment_indices\":$INDICES,\"vote\":1,\"search_query\":\"labubu\",\"batch_id\":\"$BATCH\"}" \
  | jq '.total_patched'

# 4. Check stats
curl "http://localhost:8000/api/votes/batch/$BATCH" | jq '.total_votes'
```

---

## Data Flow Diagram

```
User
  │
  ├─→ Search "labubu"
  │   └─→ Results (25 frames)
  │       └─→ [UPVOTE BEST] → /api/vote
  │           └─→ batch_id = ABC-123 (stored in DB)
  │
  ├─→ Similar Search (from upvoted frame)
  │   └─→ Similar Results (90%+: 20 frames)
  │       └─→ [BULK UPVOTE ALL] → /api/vote/bulk
  │           ├─→ batch_id: ABC-123 (inherited)
  │           └─→ 20 votes logged with vote_source="bulk_upvote"
  │
  ├─→ Query Stats
  │   └─→ /api/votes/batch/ABC-123
  │       └─→ { total_votes: 21, cascaded: 20 }
  │
  └─→ Export for Training
      └─→ SELECT * FROM vote_events WHERE batch_id='ABC-123'
          └─→ 21 labeled frames for CLIP fine-tuning
```

---

## Key Metrics

- **Cascade Ratio**: avg votes per batch (1 seed → N cascades)
- **Label Velocity**: votes per day (frame labeling speed)
- **Query Coverage**: distinct search_queries that generated labels
- **Training Data Size**: total frames ready for fine-tuning

**Example:**
```
Weekly Stats:
  • 50 batches created
  • 500 total votes
  • Avg cascade: 10 frames/batch
  • Top query: "labubu" (100 frames)
  • Training data ready: 500 frames
```
