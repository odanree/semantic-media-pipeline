#!/usr/bin/env python3
"""Delete most recently uploaded R2 files until bucket is at or under 9 GB.
Run inside lumen-api container (has S3_* env vars):
  docker exec lumen-api python /tmp/r2_trim_to_9gb.py
"""
import os
import boto3
from botocore.config import Config

TARGET = 9_000_000_000  # 9 GB

s3 = boto3.client(
    "s3",
    endpoint_url=os.environ["S3_ENDPOINT_URL"],
    aws_access_key_id=os.environ["S3_ACCESS_KEY"],
    aws_secret_access_key=os.environ["S3_SECRET_KEY"],
    region_name="auto",
    config=Config(signature_version="s3v4"),
)

objs = []
paginator = s3.get_paginator("list_objects_v2")
for page in paginator.paginate(Bucket=os.environ["S3_BUCKET"], Prefix="pexels-demo/"):
    for obj in page.get("Contents", []):
        objs.append((obj["Key"], obj["Size"], obj["LastModified"]))

total = sum(s for _, s, _ in objs)
print(f"Before: {len(objs)} files, {total / 1e9:.3f} GB")

if total <= TARGET:
    print("Already under 9 GB, nothing to do.")
    raise SystemExit(0)

# Sort newest first — delete most recently uploaded (sports tail added last)
objs.sort(key=lambda x: x[2], reverse=True)

deleted = 0
for key, size, ts in objs:
    if total <= TARGET:
        break
    s3.delete_object(Bucket=os.environ["S3_BUCKET"], Key=key)
    total -= size
    deleted += 1
    name = key.split("/")[-1]
    print(f"  DEL  {name}  ({size / 1e6:.1f} MB)  now={total / 1e9:.3f} GB")

print(f"\nDone. Deleted {deleted} files, final size {total / 1e9:.3f} GB")
