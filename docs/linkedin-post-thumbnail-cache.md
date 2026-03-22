# LinkedIn Post: Thumbnail API — From 2.9s to 85ms

---

**Draft**

I found a 2.9-second API call hiding in my production audit logs — and fixed it in 15 minutes with two layers of caching.

**The problem:**
My `/api/thumbnail` endpoint was spawning an FFmpeg subprocess on every single request. No caching anywhere. 1,066 production calls, avg 2.94s, P95 4.87s. Every time the browser loaded the thumbnail grid, users were waiting.

**The diagnosis:**
I added an `audit_logs` table early in the project — every request logs its endpoint and `response_ms`. A quick SQL query with `PERCENTILE_CONT(0.95)` surfaced the offender immediately:

```sql
SELECT endpoint, COUNT(*) as calls,
  ROUND(AVG(response_ms)::numeric, 0) AS avg_ms,
  ROUND(PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY response_ms)::numeric, 0) AS p95_ms
FROM audit_logs
WHERE endpoint != '/metrics'
GROUP BY endpoint
ORDER BY avg_ms DESC;
```

**The fix — two layers:**

1. **Cloudflare Cache Rule** — one config change. Every thumbnail URL gets `Cache-Control: public, max-age=86400`. Cloudflare stores it at the edge and serves repeat requests without ever touching my server. Zero lines of code.

2. **In-process LRU** — for cold misses that do reach origin. An `OrderedDict` keyed on `(path, round(t, 1))`, capped at 500 entries, protected by a `threading.Lock`. No Redis, no external dependency, pure Python stdlib.

**The results (stress test: 20 sequential requests):**

- Request 1 (cold): 2,030ms — FFmpeg runs, Cloudflare stores it
- Requests 2–20: ~85ms — Cloudflare edge cache HIT, origin never called

In audit_logs: 18 of 20 requests produced *zero* origin log entries. They never touched my server.

Before → After on origin:
- Avg: 2,938ms → 868ms
- P95: 4,872ms → 892ms

**The lesson:**
Measure first. The audit log told me exactly where the problem was. The fix was obvious once I knew where to look. And the `X-Cache: HIT/MISS` response header gave me instant confirmation it was working.

Caching doesn't require Redis. Sometimes an `OrderedDict` and a Cloudflare rule are all you need.

---

#Python #FastAPI #WebPerformance #BackendEngineering #Cloudflare #SoftwareEngineering
