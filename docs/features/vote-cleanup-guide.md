# Vote Cleanup Guide

**When to use:** Before deploying vote observability system to ensure old votes (without search_query context) don't pollute training data.

---

## Why Clear Votes?

**Old votes (before vote observability system):**
- Only have `user_vote: 1 | -1` in Qdrant payload
- No `search_query` metadata in VoteEvent table
- Work for ranking, but can't be used for CLIP fine-tuning training data
- Missing lineage/batch context

**New votes (after system deployment):**
- Have `user_vote: 1 | -1` in Qdrant (ranking)
- Have full search_query context in VoteEvent table (training data)
- Support batch_id lineage for bulk upvotes
- Ready for export as labeled training data

**Recommendation:** Clear both before going live with observability system.

---

## Quick Start

### Dry Run (See What Would Be Cleared)
```bash
python scripts/clear_votes.py --project=lumen1 --dry-run

# Output:
# SUMMARY
# ✅ Qdrant: Cleared 0 user_vote payloads from 0 points
# ✅ PostgreSQL: Deleted 0 rows from vote_events table
#
# [DRY RUN] No actual changes were made
```

### Clear Votes (With Confirmation)
```bash
python scripts/clear_votes.py --project=lumen1

# Prompts:
# ⚠️  WARNING: This will clear all votes!
#    - Qdrant: user_vote payloads will be deleted
#    - PostgreSQL: vote_events table will be truncated
#
# Are you sure? Type 'yes' to confirm: yes
```

### Clear Without Confirmation (For Scripts/CI)
```bash
python scripts/clear_votes.py --project=lumen1 --confirm
```

---

## Usage

### Basic Usage
```bash
# Clear both Qdrant and PostgreSQL (lumen1)
python scripts/clear_votes.py --project=lumen1 --confirm

# Clear votes for lumen2 project
python scripts/clear_votes.py --project=lumen2 --confirm
```

### Options

| Flag | Purpose |
|------|---------|
| `--project {lumen1,lumen2}` | Project to clear (default: lumen1) |
| `--dry-run` | Show what would be cleared without making changes |
| `--confirm` | Skip confirmation prompt |
| `--qdrant-only` | Only clear Qdrant votes (skip PostgreSQL) |
| `--postgres-only` | Only clear PostgreSQL votes (skip Qdrant) |

### Examples

```bash
# Dry run for lumen2
python scripts/clear_votes.py --project=lumen2 --dry-run

# Clear only Qdrant (keep database records)
python scripts/clear_votes.py --project=lumen1 --qdrant-only --confirm

# Clear only PostgreSQL (keep Qdrant votes)
python scripts/clear_votes.py --project=lumen1 --postgres-only --confirm
```

---

## Step-by-Step: Deploy Vote Observability

### 1. Backup Current State (Optional)
```bash
# Export votes to JSON for record-keeping
psql -U lumen_user -d lumen -c "SELECT * FROM vote_events" > votes_backup.json
```

### 2. Dry Run
```bash
python scripts/clear_votes.py --project=lumen1 --dry-run
```

### 3. Apply Migration
```bash
# Create vote_events table
psql -U lumen_user -d lumen < scripts/migrate_add_vote_events.sql
```

### 4. Clear Old Votes
```bash
python scripts/clear_votes.py --project=lumen1 --confirm
```

### 5. Rebuild Containers
```bash
docker-compose up -d --build
```

### 6. Verify Clean State
```bash
# Check Qdrant is clean
curl http://localhost:8000/api/search-status

# Check PostgreSQL is empty
curl http://localhost:8000/api/votes/stats
# → { total_votes: 0 }

# Test manual upvote
curl -X POST http://localhost:8000/api/vote \
  -d '{"file_path":"test.mp4","vote":1,"search_query":"test"}' | jq .batch_id
```

---

## Script Behavior

### What It Clears

**Qdrant (media_vectors collection):**
- Removes all `user_vote` payload keys
- Leaves all other payloads intact (file_path, timestamp, embeddings, etc.)
- Points remain indexed and searchable

**PostgreSQL (vote_events table):**
- Truncates entire table (deletes all rows)
- Preserves table schema and indexes
- No data loss if backed up first

### What It Preserves

- Media vectors (embeddings)
- File metadata
- Search results
- Audio segments and timestamps
- Construction phase labels
- Custom labels

### Output Example

```
================================================================================
VOTE CLEANUP SCRIPT
================================================================================
Project: lumen1
Qdrant: qdrant:6333
Dry run: False

================================================================================
QDRANT: Clearing user_vote payloads
================================================================================
Host: qdrant:6333
Collection: media_vectors
Dry run: False

Scanning for points with user_vote payload...
  • Scanned 50 points with user_vote...
  • Scanned 100 points with user_vote...

✓ Found 156 points with user_vote payload

Clearing payloads in batches of 1000...
  • Cleared 156 payloads (156/156)

✅ Successfully cleared 156 user_vote payloads from Qdrant

================================================================================
POSTGRESQL: Clearing vote_events table
================================================================================
Database: lumen
Dry run: False

Found 42 rows in vote_events table

Truncating vote_events table...
✅ Successfully truncated vote_events table (42 rows deleted)

================================================================================
SUMMARY
================================================================================
✅ Qdrant: Cleared 156 user_vote payloads from 156 points
✅ PostgreSQL: Deleted 42 rows from vote_events table
```

---

## Rollback (If Needed)

### Restore Qdrant Votes
If you cleared Qdrant by accident:
1. Votes are just ranking signals — they rebuild as users interact with system
2. No data loss; user votes will recreate as they click again
3. Or restore from backup if available

### Restore PostgreSQL Votes
If you cleared database by accident:
1. If backup exists: `psql -U lumen_user -d lumen < votes_backup.sql`
2. Otherwise: votes will recreate as new votes are cast
3. Historical records lost but system functional

---

## Troubleshooting

### "Collection not found"
```
❌ Qdrant: Collection 'media_vectors' not found
```

**Solution:** Check Qdrant is running and collection name is correct
```bash
docker-compose ps  # Verify qdrant is up
curl http://localhost:6333/collections  # Check collection name
```

### "Table vote_events does not exist"
```
⚠️  PostgreSQL: Table 'vote_events' does not exist
```

**Solution:** Run migration first
```bash
psql -U lumen_user -d lumen < scripts/migrate_add_vote_events.sql
```

### Connection timeout
```
❌ Qdrant error: Connection refused
```

**Solution:** Verify services are running
```bash
docker-compose up -d  # Start/restart services
sleep 5  # Wait for startup
python scripts/clear_votes.py --project=lumen1 --dry-run
```

---

## Automation

### In CI/CD Pipeline
```yaml
# .github/workflows/deploy.yml
- name: Clear old votes
  run: |
    python scripts/clear_votes.py --project=lumen1 --confirm
  env:
    QDRANT_HOST: qdrant
    QDRANT_PORT: 6333
    DATABASE_ASYNC_URL: postgresql+asyncpg://...
```

### Docker Entrypoint
```dockerfile
# In api/Dockerfile
RUN chmod +x /app/scripts/clear_votes.py
CMD ["python", "scripts/clear_votes.py", "--project=lumen1", "--confirm"]
```

---

## Safety Checklist

- [ ] Run `--dry-run` first to see what will be cleared
- [ ] Backup votes if needed: `psql -c "SELECT * FROM vote_events"`
- [ ] Verify Qdrant/PostgreSQL are running before clearing
- [ ] Use `--confirm` only in automated contexts
- [ ] Test /api/vote endpoint works after clearing
- [ ] Verify /api/votes/stats returns 0 votes after clearing

---

## Related Docs

- [Vote Observability System](vote-observability-bulk-upvote.md) — Full system documentation
- [Migration Script](../scripts/migrate_add_vote_events.sql) — Creates vote_events table
