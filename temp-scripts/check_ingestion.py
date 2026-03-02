#!/usr/bin/env python
"""Check ingestion status from database"""
import psycopg2

try:
    # Connect to PostgreSQL
    conn = psycopg2.connect(
        host="localhost",
        port=5432,
        user="lumen_user",
        password="REDACTED_DB_PASSWORD",
        database="lumen"
    )
    cur = conn.cursor()
    
    print("\n" + "="*70)
    print("DATABASE INGESTION SUMMARY")
    print("="*70)
    
    # Get media file counts
    cur.execute("SELECT COUNT(*) FROM media_files;")
    media_count = cur.fetchone()[0]
    print(f"\n✓ Media Files Ingested: {media_count:,}")
    
    # Get embeddings count
    cur.execute("SELECT COUNT(*) FROM media_embeddings;")
    embed_count = cur.fetchone()[0]
    print(f"✓ Embeddings Generated: {embed_count:,}")
    
    # Get file type breakdown
    cur.execute("""
        SELECT file_type, COUNT(*) as count
        FROM media_files
        GROUP BY file_type
        ORDER BY count DESC;
    """)
    print(f"\n✓ File Types:")
    for file_type, count in cur.fetchall():
        print(f"  - {file_type}: {count:,}")
    
    # Get recent ingested files
    cur.execute("""
        SELECT filename, file_type, created_at
        FROM media_files
        ORDER BY created_at DESC
        LIMIT 5;
    """)
    print(f"\n✓ Recently Ingested Files:")
    for filename, file_type, created_at in cur.fetchall():
        print(f"  - {filename} ({file_type}) @ {created_at}")
    
    print("\n" + "="*70)
    
    cur.close()
    conn.close()
except Exception as e:
    print(f"Error: {e}")
