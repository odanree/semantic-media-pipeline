@"
import os, psycopg2
from qdrant_client import QdrantClient

pg_conn = psycopg2.connect(
    host='lumen-postgres', port=5432,
    database='lumen', user='lumen_user',
    password='lumen_secure_password_2026'
)

print('\n' + '='*75)
print('📊 INGESTION PIPELINE STATUS')
print('='*75)

with pg_conn.cursor() as cur:
    cur.execute('SELECT COUNT(id) FROM media_files;')
    total = cur.fetchone()[0]
    
    cur.execute("SELECT COUNT(id) FROM media_files WHERE processing_status='completed';")
    completed = cur.fetchone()[0]
    
    cur.execute("SELECT COUNT(id) FROM media_files WHERE processing_status='processing';")
    processing = cur.fetchone()[0]
    
    cur.execute('SELECT COUNT(id) FROM media_files WHERE qdrant_point_id IS NOT NULL;')
    embedded = cur.fetchone()[0]
    
    print(f'\n📦 INDEXED ITEMS: {total:,}')
    print(f'\n  Status:')
    print(f'    ✓ Completed:  {completed:6,} ({completed*100/total if total else 0:.1f}%)')
    print(f'    ⏳ Processing: {processing:6,} ({processing*100/total if total else 0:.1f}%)')
    print(f'\n  Vectors:')
    print(f'    🔢 Embedded: {embedded:,} ({embedded*100/total if total else 0:.1f}%)')
    print(f'    ⏳ Pending:  {total-embedded:,} ({(total-embedded)*100/total if total else 0:.1f}%)')
    
    cur.execute('SELECT file_type, COUNT(id) as cnt FROM media_files GROUP BY file_type ORDER BY cnt DESC;')
    print(f'\n  File Types:')
    for ftype, cnt in cur.fetchall():
        print(f'    • {ftype}: {cnt:,}')

pg_conn.close()

try:
    q = QdrantClient(url='http://lumen-qdrant:6333')
    cols = q.get_collections()
    print(f'\n  Qdrant: {len(cols.collections)} collections')
except:
    print(f'\n  Qdrant: (not accessible from this context)')

print('='*75 + '\n')
"@ | docker run --rm --network semantic-media-pipeline_lumen-net -i python:3.10-slim bash -c "pip install -q psycopg2-binary qdrant-client 2>/dev/null && python"
