#!/usr/bin/env python3
"""
R2 Cleanup: keep only the files that are indexed in the DB (done + qdrant_point_id set).
Deletes everything else from R2 pexels-demo/ prefix.

Run inside the API container (has DB + R2 env vars):
  docker exec lumen-api python /tmp/r2_cleanup_indexed_only.py [--dry-run]
"""
import os
import sys

import boto3
from botocore.config import Config
import psycopg2
from urllib.parse import urlparse

DRY_RUN = "--dry-run" in sys.argv

# ---------------------------------------------------------------------------
# R2 client
# ---------------------------------------------------------------------------
S3_ENDPOINT_URL = os.environ["S3_ENDPOINT_URL"]
S3_BUCKET       = os.environ["S3_BUCKET"]
S3_ACCESS_KEY   = os.environ["S3_ACCESS_KEY"]
S3_SECRET_KEY   = os.environ["S3_SECRET_KEY"]

s3 = boto3.client(
    "s3",
    endpoint_url=S3_ENDPOINT_URL,
    aws_access_key_id=S3_ACCESS_KEY,
    aws_secret_access_key=S3_SECRET_KEY,
    region_name="auto",
    config=Config(signature_version="s3v4"),
)

# ---------------------------------------------------------------------------
# Get indexed file paths from DB
# ---------------------------------------------------------------------------
DATABASE_URL = os.environ["DATABASE_URL"]
conn = psycopg2.connect(DATABASE_URL)
cur = conn.cursor()
cur.execute("""
    SELECT file_path FROM media_files
    WHERE processing_status = 'done'
      AND qdrant_point_id IS NOT NULL
""")
# file_path stored as "pexels-demo/pexels-search-12345.mp4"
indexed_keys = {row[0] for row in cur.fetchall()}
conn.close()
print(f"Indexed in DB: {len(indexed_keys)} files")

# ---------------------------------------------------------------------------
# List all R2 objects under pexels-demo/
# ---------------------------------------------------------------------------
all_objects = []
paginator = s3.get_paginator("list_objects_v2")
for page in paginator.paginate(Bucket=S3_BUCKET, Prefix="pexels-demo/"):
    for obj in page.get("Contents", []):
        all_objects.append((obj["Key"], obj["Size"]))

print(f"R2 total objects: {len(all_objects)}")

to_delete = [(k, sz) for k, sz in all_objects if k not in indexed_keys]
to_keep   = [(k, sz) for k, sz in all_objects if k in indexed_keys]
freed_bytes = sum(sz for _, sz in to_delete)

print(f"Keeping : {len(to_keep)}")
print(f"Deleting: {len(to_delete)}  ({freed_bytes / 1e9:.2f} GB freed)")
if DRY_RUN:
    print("[DRY RUN — no changes]\n")
print()

deleted = 0
for key, size in to_delete:
    print(f"  DEL  {key}  ({size / 1e6:.1f} MB)", end="", flush=True)
    if DRY_RUN:
        print("  [DRY]")
    else:
        try:
            s3.delete_object(Bucket=S3_BUCKET, Key=key)
            print("  OK")
            deleted += 1
        except Exception as e:
            print(f"  ERROR: {e}")

if not DRY_RUN:
    print(f"\nDeleted {deleted} objects, freed {freed_bytes / 1e9:.2f} GB.")
else:
    print(f"\nWould delete {len(to_delete)} objects, free {freed_bytes / 1e9:.2f} GB.")
