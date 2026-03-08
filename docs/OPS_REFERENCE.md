# Lumen Ops Reference — Health & Progress Commands

All HTTP examples target Windows-local `localhost`. The Mac worker hits
the same API via its `windows-pc:8000` extra-hosts entry.

---

## 1. API Endpoints (curl / PowerShell)

### 1.1 Pipeline health at a glance

```powershell
# PowerShell (pretty-printed)
Invoke-RestMethod http://localhost:8000/api/stats/summary | ConvertTo-Json -Depth 5

# Just status counts
Invoke-RestMethod http://localhost:8000/api/stats/summary | Select-Object -ExpandProperty by_status

# Qdrant drift (vectors vs DB done count)
(Invoke-RestMethod http://localhost:8000/api/stats/summary).qdrant

# Top errors only
(Invoke-RestMethod http://localhost:8000/api/stats/summary).top_errors
```

```bash
# curl (Linux/Mac)
curl -s http://localhost:8000/api/stats/summary | python3 -m json.tool

# Or on Mac pointing at Windows:
curl -s http://windows-pc:8000/api/stats/summary | python3 -m json.tool
```

**Response fields:**
| Field | Meaning |
|---|---|
| `by_status.done` | Files fully processed |
| `by_status.pending` | Queued, not yet picked up |
| `by_status.processing` | Currently in a worker |
| `by_status.error` | Failed (see `top_errors`) |
| `stuck_processing` | In 'processing' > 2h with no `processed_at` — likely stalled |
| `qdrant.vector_count` | Points in Qdrant (> `db_done_count` is normal for video) |
| `qdrant.drift` | `vector_count - db_done_count`; negative = DB gaps |
| `top_errors` | Top-10 error messages with occurrence counts |

---

### 1.2 Processing time & throughput

```powershell
# Default: last 30 days, 20 slowest files
Invoke-RestMethod http://localhost:8000/api/stats/processing | ConvertTo-Json -Depth 5

# Last 24 hours only
Invoke-RestMethod "http://localhost:8000/api/stats/processing?hours=24" | ConvertTo-Json -Depth 5

# Timing stats only (avg/median/p95)
(Invoke-RestMethod http://localhost:8000/api/stats/processing).timing

# Hourly throughput buckets
(Invoke-RestMethod http://localhost:8000/api/stats/processing).hourly_throughput | Format-Table

# Re-index sessions (bursts separated by >10 min idle gaps)
(Invoke-RestMethod http://localhost:8000/api/stats/processing).indexing_sessions

# 50 slowest files
Invoke-RestMethod "http://localhost:8000/api/stats/processing?limit=50" |
    Select-Object -ExpandProperty slowest_files | Format-Table
```

**Response fields:**
| Field | Meaning |
|---|---|
| `timing.avg_secs` | Mean seconds per file |
| `timing.median_secs` | Median (more robust than avg) |
| `timing.p95_secs` | 95th percentile — worst-case normal |
| `timing.max_secs` | Absolute slowest file in window |
| `hourly_throughput` | Files completed per hour (videos + images split) |
| `indexing_sessions` | Auto-detected processing bursts with start/end/count |

---

### 1.3 Component health check

```powershell
Invoke-RestMethod http://localhost:8000/api/health | ConvertTo-Json
```

Returns: `status` (healthy/unhealthy), plus per-component: `qdrant`, `postgres`, `redis`.

---

## 2. SQL Queries (direct Postgres)

Connect:
```powershell
# From Windows host
docker exec -it lumen-postgres psql -U lumen_user -d lumen
```

```bash
# From Mac (via SSH or docker on Windows PC)
docker exec -it lumen-postgres psql -U lumen_user -d lumen
```

---

### 2.1 Progress overview

```sql
-- Overall status breakdown
SELECT processing_status, COUNT(*) AS n,
       ROUND(100.0 * COUNT(*) / SUM(COUNT(*)) OVER (), 1) AS pct
FROM media_files
GROUP BY processing_status
ORDER BY n DESC;

-- ETA estimate: tasks/hr based on last 1 hour
WITH rate AS (
  SELECT COUNT(*) AS completed_last_hr
  FROM media_files
  WHERE processed_at >= NOW() - INTERVAL '1 hour'
)
SELECT
  (SELECT COUNT(*) FROM media_files WHERE processing_status = 'pending') AS pending,
  rate.completed_last_hr                                                  AS tasks_per_hr,
  ROUND(
    (SELECT COUNT(*) FROM media_files WHERE processing_status = 'pending')::numeric
    / NULLIF(rate.completed_last_hr, 0), 1
  )                                                                       AS est_hours_remaining
FROM rate;
```

---

### 2.2 Per-worker throughput

```sql
-- Tasks completed per worker in the last hour
SELECT worker_id,
       COUNT(*)                          AS completed,
       ROUND(AVG(embedding_ms::numeric)/1000, 2) AS avg_embed_secs
FROM media_files
WHERE processing_status = 'done'
  AND processed_at >= NOW() - INTERVAL '1 hour'
  AND worker_id IS NOT NULL
GROUP BY worker_id
ORDER BY completed DESC;

-- All-time per-worker totals
SELECT worker_id, COUNT(*) AS total_done
FROM media_files
WHERE processing_status = 'done'
GROUP BY worker_id
ORDER BY total_done DESC;
```

---

### 2.3 Error analysis

```sql
-- All current errors grouped by message prefix
SELECT LEFT(error_message, 100) AS error, COUNT(*) AS n
FROM media_files
WHERE processing_status = 'error'
GROUP BY LEFT(error_message, 100)
ORDER BY n DESC;

-- Files errored in the last 30 minutes
SELECT file_path, file_type, error_message, processed_at
FROM media_files
WHERE processing_status = 'error'
  AND processed_at >= NOW() - INTERVAL '30 minutes'
ORDER BY processed_at DESC
LIMIT 20;

-- Retry-candidate errors (reset to pending):
-- PREVIEW first — don't run the UPDATE without reviewing:
SELECT id, file_path, error_message
FROM media_files
WHERE processing_status = 'error'
  AND error_message LIKE '%INVALID_ARGUMENT%';

-- Then reset if confirmed:
UPDATE media_files
SET processing_status = 'pending', error_message = NULL
WHERE processing_status = 'error'
  AND error_message LIKE '%INVALID_ARGUMENT%';
```

---

### 2.4 Stuck / stalled files

```sql
-- Files stuck in 'processing' for > 2 hours
SELECT id, file_path, file_type, created_at,
       EXTRACT(EPOCH FROM NOW() - created_at)/3600 AS stuck_hrs
FROM media_files
WHERE processing_status = 'processing'
  AND processed_at IS NULL
  AND created_at < NOW() - INTERVAL '2 hours'
ORDER BY stuck_hrs DESC;

-- Force-reset stuck files back to pending:
UPDATE media_files
SET processing_status = 'pending'
WHERE processing_status = 'processing'
  AND processed_at IS NULL
  AND created_at < NOW() - INTERVAL '2 hours';
```

---

### 2.5 Frame cache hit rate

```sql
-- Cache hit ratio for video files
SELECT
  frame_cache_hit,
  COUNT(*)                                                AS n,
  ROUND(100.0 * COUNT(*) / SUM(COUNT(*)) OVER (), 1)     AS pct
FROM media_files
WHERE file_type = 'video'
  AND processing_status = 'done'
GROUP BY frame_cache_hit;

-- Cache hits in the last hour (should rise as re-index replays same files)
SELECT frame_cache_hit, COUNT(*) AS n
FROM media_files
WHERE file_type = 'video'
  AND processed_at >= NOW() - INTERVAL '1 hour'
GROUP BY frame_cache_hit;
```

---

### 2.6 Embedding timing (CLIP performance)

```sql
-- Embedding latency stats across both workers
SELECT
  worker_id,
  COUNT(*)                             AS n,
  ROUND(AVG(embedding_ms))             AS avg_ms,
  ROUND(PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY embedding_ms)) AS median_ms,
  ROUND(PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY embedding_ms)) AS p95_ms,
  MAX(embedding_ms)                    AS max_ms
FROM media_files
WHERE processing_status = 'done'
  AND embedding_ms IS NOT NULL
GROUP BY worker_id
ORDER BY avg_ms;

-- Slowest 10 individual embeds
SELECT file_path, file_type, worker_id, embedding_ms
FROM media_files
WHERE embedding_ms IS NOT NULL
ORDER BY embedding_ms DESC
LIMIT 10;
```

---

### 2.7 Ingest / discovery health

```sql
-- Files discovered in the last 24 hours
SELECT DATE_TRUNC('hour', created_at) AS hour, COUNT(*) AS discovered
FROM media_files
WHERE created_at >= NOW() - INTERVAL '24 hours'
GROUP BY 1 ORDER BY 1;

-- Files with no file type set (ingestion bug indicator)
SELECT COUNT(*) FROM media_files WHERE file_type IS NULL;

-- Duplicate paths (should be 0 — file_path has a UNIQUE constraint)
SELECT file_path, COUNT(*) FROM media_files GROUP BY file_path HAVING COUNT(*) > 1;
```

---

## 3. Celery / Flower

```powershell
# Current active tasks across all workers
curl http://localhost:5555/api/tasks?state=ACTIVE | python3 -m json.tool

# Pending queue depth
curl http://localhost:5555/api/queues/length | python3 -m json.tool

# Worker list + concurrency
curl http://localhost:5555/api/workers | python3 -m json.tool

# Purge ALL pending tasks (destructive — forces re-queue via /api/ingest)
curl -X DELETE http://localhost:5555/api/queues/celery
```

Flower UI: http://localhost:5555  
Mac Flower UI: http://localhost:5556

---

## 4. Qdrant direct

```powershell
# Collection info (point count, vector config)
Invoke-RestMethod http://localhost:6333/collections/media_vectors | ConvertTo-Json -Depth 5

# Cluster health
Invoke-RestMethod http://localhost:6333/cluster | ConvertTo-Json

# Collection exists check
(Invoke-RestMethod http://localhost:6333/collections).result.collections |
    Select-Object name, status
```

---

## 5. One-liner progress dashboard (PowerShell)

```powershell
# Run repeatedly to monitor — Ctrl+C to stop
while ($true) {
    $s = Invoke-RestMethod http://localhost:8000/api/stats/summary
    $done    = $s.by_status.done    ?? 0
    $pending = $s.by_status.pending ?? 0
    $error   = $s.by_status.error   ?? 0
    $proc    = $s.by_status.processing ?? 0
    $total   = $s.total_files
    $pct     = if ($total) { [math]::Round(100*$done/$total,1) } else { 0 }
    Write-Host "$(Get-Date -f 'HH:mm:ss')  done=$done ($pct%)  pending=$pending  processing=$proc  error=$error  total=$total"
    Start-Sleep 30
}
```
