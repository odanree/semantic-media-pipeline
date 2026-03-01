#!/bin/bash
# Quick analysis script - runs inside Docker Compose network
docker run --rm --network lumen_lumen-net \
  -e DATABASE_HOST=lumen-postgres \
  -e DATABASE_PORT=5432 \
  -e DATABASE_NAME=lumen \
  -e DATABASE_USER=lumen_user \
  -e DATABASE_PASSWORD=lumen_secure_password_2026 \
  -e QDRANT_HOST=lumen-qdrant \
  -e QDRANT_PORT=6333 \
  python:3.10-slim bash -c \
  "pip install -q psycopg2-binary qdrant-client 2>/dev/null && python -c '
import os, psycopg2
from qdrant_client import QdrantClient

pg_conn = psycopg2.connect(
    host=os.getenv(\"DATABASE_HOST\"), port=int(os.getenv(\"DATABASE_PORT\", \"5432\")),
    database=os.getenv(\"DATABASE_NAME\"), user=os.getenv(\"DATABASE_USER\"),
    password=os.getenv(\"DATABASE_PASSWORD\")
)

print(\"\n\" + \"=\"*75)
print(\"📊 INGESTION PIPELINE STATUS\")
print(\"=\"*75)

with pg_conn.cursor() as cur:
    cur.execute(\"SELECT COUNT(id) FROM media_files;\")
    total = cur.fetchone()[0]
    
    cur.execute(\"SELECT COUNT(id) FROM media_files WHERE processing_status='"'"'completed'"'"';\")
    completed = cur.fetchone()[0]
    
    cur.execute(\"SELECT COUNT(id) FROM media_files WHERE processing_status='"'"'processing'"'"';\")
    processing = cur.fetchone()[0]
    
    cur.execute(\"SELECT COUNT(id) FROM media_files WHERE qdrant_point_id IS NOT NULL;\")
    embedded = cur.fetchone()[0]
    
    print(f\"\n📦 INDEXED ITEMS: {total:,}\")
    print(f\"\n  Status:\")
    print(f\"    ✓ Completed:  {completed:6,} ({completed*100/total if total else 0:.1f}%)\")
    print(f\"    ⏳ Processing: {processing:6,} ({processing*100/total if total else 0:.1f}%)\")
    print(f\"\n  Vectors:\")
    print(f\"    🔢 Embedded: {embedded:,} ({embedded*100/total if total else 0:.1f}%)\")
    print(f\"    ⏳ Pending:  {total-embedded:,} ({(total-embedded)*100/total if total else 0:.1f}%)\")
    
    cur.execute(\"SELECT file_type, COUNT(id) as cnt FROM media_files GROUP BY file_type ORDER BY cnt DESC;\")
    print(f\"\n  File Types:\")
    for ftype, cnt in cur.fetchall():
        print(f\"    • {ftype}: {cnt:,}\")

pg_conn.close()

try:
    q = QdrantClient(url=f\"http://{os.getenv(\"QDRANT_HOST\")}:{os.getenv(\"QDRANT_PORT\")}\")
    cols = q.get_collections()
    print(f\"\n  Qdrant: {len(cols.collections)} collections\")
except:
    print(f\"\n  Qdrant: Connecting...\")

print(\"=\"*75 + \"\n\")
'"'"
