#!/usr/bin/env python3
"""Find DB entries whose R2 file no longer exists, then delete them from DB + Qdrant."""
import os, sys, boto3, psycopg2
from botocore.config import Config

DRY_RUN = "--dry-run" in sys.argv

s3 = boto3.client("s3",
    endpoint_url=os.getenv("S3_ENDPOINT_URL"),
    aws_access_key_id=os.getenv("S3_ACCESS_KEY"),
    aws_secret_access_key=os.getenv("S3_SECRET_KEY"),
    region_name="auto",
    config=Config(signature_version="s3v4"),
)
bucket = os.getenv("S3_BUCKET")
r2_keys = set()
paginator = s3.get_paginator("list_objects_v2")
for page in paginator.paginate(Bucket=bucket, Prefix="pexels-demo/"):
    for obj in page.get("Contents", []):
        r2_keys.add(obj["Key"])

conn = psycopg2.connect(
    host=os.getenv("DATABASE_HOST", os.getenv("DB_HOST", "lumen-postgres")),
    dbname=os.getenv("DATABASE_NAME", os.getenv("DB_NAME", "lumen")),
    user=os.getenv("DATABASE_USER", os.getenv("DB_USER", "lumen_user")),
    password=os.getenv("DATABASE_PASSWORD", os.getenv("DB_PASSWORD")),
)
cur = conn.cursor()
cur.execute("SELECT id, file_path FROM media_files")
rows = cur.fetchall()

orphans = [(row[0], row[1]) for row in rows if row[1] not in r2_keys]
print(f"DB: {len(rows)}  R2: {len(r2_keys)}  Orphans: {len(orphans)}")
for oid, fpath in orphans:
    print(f"  {oid}  {fpath}")

if orphans and not DRY_RUN:
    ids = [o[0] for o in orphans]
    placeholders = ",".join(["%s"] * len(ids))
    cur.execute(f"DELETE FROM media_files WHERE id IN ({placeholders})", ids)
    conn.commit()
    print(f"Deleted {len(ids)} orphan DB records.")
elif orphans and DRY_RUN:
    print("[DRY RUN] Would delete the above records.")

conn.close()
