"""
SQL Analysis: Query ingestion statistics and top detected themes
Analyzes the first N items in your media pipeline with Qdrant similarity scores
"""

import os
import psycopg2
from qdrant_client import QdrantClient
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Get database connection parameters from environment
DB_HOST = os.getenv("DATABASE_HOST", "lumen-postgres")
DB_PORT = os.getenv("DATABASE_PORT", "5432")
DB_NAME = os.getenv("DATABASE_NAME", "lumen")
DB_USER = os.getenv("DATABASE_USER", "postgres")
DB_PASSWORD = os.getenv("DATABASE_PASSWORD")

QDRANT_HOST = os.getenv("QDRANT_HOST", "lumen-qdrant")
QDRANT_PORT = os.getenv("QDRANT_PORT", "6333")

if not DB_PASSWORD:
    raise ValueError(
        "DATABASE_PASSWORD environment variable not set. "
        "Please check your .env file or set the variable in your environment."
    )

try:
    # Connect to PostgreSQL
    print(f"Connecting to PostgreSQL at {DB_HOST}:{DB_PORT}/{DB_NAME}...")
    pg_conn = psycopg2.connect(
        host=DB_HOST,
        port=DB_PORT,
        database=DB_NAME,
        user=DB_USER,
        password=DB_PASSWORD
    )
    print("✓ PostgreSQL connection established")
    
    # Connect to Qdrant
    print(f"Connecting to Qdrant at {QDRANT_HOST}:{QDRANT_PORT}...")
    q_client = QdrantClient(url=f"http://{QDRANT_HOST}:{QDRANT_PORT}")
    print("✓ Qdrant connection established")
    
except Exception as e:
    print(f"✗ Connection error: {e}")
    exit(1)


def get_ingestion_stats():
    """Get basic ingestion statistics"""
    with pg_conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM media_assets;")
        total_items = cur.fetchone()[0]
        return total_items


def get_top_tags(limit=10):
    """Get the most common detected objects in your media"""
    try:
        with pg_conn.cursor() as cur:
            # Extract detected_objects from JSONB metadata and count occurrences
            cur.execute("""
                SELECT jsonb_array_elements(metadata->'detected_objects') as label, COUNT(*) as count
                FROM media_assets 
                WHERE metadata->'detected_objects' IS NOT NULL
                GROUP BY label 
                ORDER BY COUNT(*) DESC 
                LIMIT %s;
            """, (limit,))
            return cur.fetchall()
    except Exception as e:
        print(f"Error querying detected objects: {e}")
        return []


def get_media_distribution():
    """Get distribution of media types"""
    try:
        with pg_conn.cursor() as cur:
            cur.execute("""
                SELECT media_type, COUNT(*) as count
                FROM media_assets
                GROUP BY media_type
                ORDER BY count DESC;
            """)
            return cur.fetchall()
    except Exception as e:
        print(f"Error querying media distribution: {e}")
        return []


def get_storage_stats():
    """Get storage statistics"""
    try:
        with pg_conn.cursor() as cur:
            # Total size of all media
            cur.execute("""
                SELECT 
                    COUNT(*) as total_files,
                    ROUND(SUM(file_size) / (1024.0 * 1024.0 * 1024.0), 2) as total_gb,
                    ROUND(AVG(file_size) / (1024.0 * 1024.0), 2) as avg_mb,
                    MAX(file_size) / (1024.0 * 1024.0 * 1024.0) as max_gb
                FROM media_assets;
            """)
            return cur.fetchone()
    except Exception as e:
        print(f"Error querying storage stats: {e}")
        return None


def get_qdrant_stats():
    """Get Qdrant collection statistics"""
    try:
        collections = q_client.get_collections()
        stats = []
        for collection in collections.collections:
            collection_info = q_client.get_collection(collection.name)
            stats.append({
                'name': collection.name,
                'vectors': collection_info.points_count,
                'dimension': collection_info.config.params.vectors.size if hasattr(collection_info.config.params.vectors, 'size') else 'unknown'
            })
        return stats
    except Exception as e:
        print(f"Error querying Qdrant stats: {e}")
        return []


def main():
    print("\n" + "="*70)
    print("📊 INGESTION PIPELINE ANALYSIS")
    print("="*70 + "\n")
    
    # Basic stats
    total = get_ingestion_stats()
    print(f"Total media items ingested: {total:,}")
    
    # Media type distribution
    print("\n--- Media Type Distribution ---")
    media_dist = get_media_distribution()
    if media_dist:
        for media_type, count in media_dist:
            percentage = (count / total * 100) if total > 0 else 0
            print(f"  {media_type}: {count:,} ({percentage:.1f}%)")
    else:
        print("  No media distribution data available")
    
    # Storage statistics
    print("\n--- Storage Statistics ---")
    storage = get_storage_stats()
    if storage:
        total_files, total_gb, avg_mb, max_gb = storage
        print(f"  Total files: {total_files:,}")
        print(f"  Total storage: {total_gb} GB")
        print(f"  Average file size: {avg_mb} MB")
        print(f"  Largest file: {max_gb:.3f} GB")
    else:
        print("  No storage data available")
    
    # Top detected themes/objects
    print("\n--- Top 10 Detected Objects ---")
    tags = get_top_tags(limit=10)
    if tags:
        for label, count in tags:
            percentage = (count / total * 100) if total > 0 else 0
            label_str = label if isinstance(label, str) else str(label)
            print(f"  {label_str}: {count:,} ({percentage:.1f}%)")
    else:
        print("  No detected objects found")
    
    # Qdrant statistics
    print("\n--- Vector Database Statistics ---")
    qdrant_stats = get_qdrant_stats()
    if qdrant_stats:
        for stat in qdrant_stats:
            print(f"  Collection: {stat['name']}")
            print(f"    Vector count: {stat['vectors']:,}")
            print(f"    Dimensions: {stat['dimension']}")
    else:
        print("  No Qdrant collections found")
    
    print("\n" + "="*70)
    print("Analysis complete!")
    print("="*70 + "\n")
    
    # Close connection
    pg_conn.close()


if __name__ == "__main__":
    main()
