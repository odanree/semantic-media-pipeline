#!/usr/bin/env python
"""Query ingestion statistics from PostgreSQL and Qdrant"""
import os
import psycopg2
from qdrant_client import QdrantClient
import json
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# Get credentials from environment variables (secure & git-safe)
DB_HOST = os.getenv("DATABASE_HOST", "localhost")
DB_PORT = os.getenv("DATABASE_PORT", "5432")
DB_NAME = os.getenv("DATABASE_NAME", "lumen")
DB_USER = os.getenv("DATABASE_USER", "lumen_user")
DB_PASSWORD = os.getenv("DATABASE_PASSWORD")
QDRANT_HOST = os.getenv("QDRANT_HOST", "localhost")
QDRANT_PORT = os.getenv("QDRANT_PORT", "6333")

# Validate required credentials
if not DB_PASSWORD:
    raise ValueError("DB_PASSWORD environment variable not set. Check your .env file.")

# Connect to the databases
pg_conn = psycopg2.connect(
    host=DB_HOST,
    port=DB_PORT,
    dbname=DB_NAME,
    user=DB_USER,
    password=DB_PASSWORD
)
q_client = QdrantClient(f"http://{QDRANT_HOST}:{QDRANT_PORT}")

def get_media_stats():
    """Get overall media ingestion statistics"""
    with pg_conn.cursor() as cur:
        # Get total media files
        cur.execute("SELECT COUNT(*) FROM media_files;")
        total_files = cur.fetchone()[0]
        
        # Get file type breakdown
        cur.execute("""
            SELECT file_type, COUNT(*) as count
            FROM media_files
            GROUP BY file_type
            ORDER BY count DESC;
        """)
        file_types = cur.fetchall()
        
        # Get embeddings stats
        cur.execute("SELECT COUNT(*) FROM media_embeddings;")
        total_embeddings = cur.fetchone()[0]
        
        return {
            'total_files': total_files,
            'file_types': file_types,
            'total_embeddings': total_embeddings
        }

def get_qdrant_stats():
    """Get Qdrant vector database statistics"""
    try:
        collections = q_client.get_collections()
        return {
            'collections': collections.collections if collections.collections else [],
            'collection_count': len(collections.collections) if collections.collections else 0
        }
    except Exception as e:
        return {'error': str(e)}

def get_recent_files(limit=5):
    """Get recently ingested files"""
    with pg_conn.cursor() as cur:
        cur.execute("""
            SELECT filename, file_type, created_at
            FROM media_files
            ORDER BY created_at DESC
            LIMIT %s;
        """, (limit,))
        return cur.fetchall()

if __name__ == '__main__':
    print("\n" + "="*70)
    print("INGESTION STATISTICS")
    print("="*70)
    
    try:
        # Get PostgreSQL stats
        stats = get_media_stats()
        print(f"\n✓ PostgreSQL Media Statistics:")
        print(f"  Total Files Ingested: {stats['total_files']:,}")
        print(f"  Total Embeddings Generated: {stats['total_embeddings']:,}")
        print(f"\n  File Type Breakdown:")
        for file_type, count in stats['file_types']:
            print(f"    - {file_type}: {count:,}")
        
        # Get Qdrant stats
        q_stats = get_qdrant_stats()
        print(f"\n✓ Qdrant Vector Database:")
        print(f"  Collections: {q_stats['collection_count']}")
        if q_stats['collection_count'] > 0:
            for collection in q_stats['collections']:
                print(f"    - {collection.name}: {collection.points_count} vectors")
        
        # Get recent files
        recent = get_recent_files()
        print(f"\n✓ Recent Ingestions (last 5 files):")
        for filename, file_type, created_at in recent:
            print(f"    - {filename} ({file_type}) @ {created_at}")
        
        print("\n" + "="*70)
        
    except Exception as e:
        print(f"✗ Error: {e}")
    finally:
        pg_conn.close()
