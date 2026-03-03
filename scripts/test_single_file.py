#!/usr/bin/env python
"""
Test script: Inject a single file into the ingest pipeline.

Usage:
    python scripts/test_single_file.py /path/to/video.mp4
    python scripts/test_single_file.py /mnt/source/Pixel\ 9\ Aug\ to\ Sept\ 2025/PXL_20250910_020850495.mp4
"""

import os
import sys
import time
from pathlib import Path

# For testing: Import celery app the same way the worker does
os.chdir(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# Import celery_app from worker directory
from worker.celery_app import app as celery_app
from worker.tasks import ingest_media


def test_single_file(file_path: str) -> None:
    """
    Enqueue a single file for processing and monitor progress.
    
    Args:
        file_path: Full path to media file (image or video)
    """
    file_path = os.path.expanduser(file_path)
    
    print(f"\n{'='*80}")
    print(f"TEST: Single File Ingest")
    print(f"{'='*80}")
    print(f"File: {file_path}")
    print(f"Exists: {os.path.isfile(file_path)}")
    
    if not os.path.isfile(file_path):
        print(f"ERROR: File not found: {file_path}")
        sys.exit(1)
    
    # Determine file type
    ext = Path(file_path).suffix.lower()
    video_exts = {".mp4", ".mov", ".avi", ".mkv", ".flv", ".wmv", ".webm", ".m4v"}
    image_exts = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".heic", ".raw", ".webp"}
    
    if ext in video_exts:
        file_type = "video"
    elif ext in image_exts:
        file_type = "image"
    else:
        print(f"ERROR: Unknown file type: {ext}")
        sys.exit(1)
    
    print(f"File Type: {file_type}")
    print(f"File Size: {os.path.getsize(file_path) / 1024 / 1024:.2f} MB")
    print()
    
    # Enqueue ingest task via Celery app (using task name, like the API does)
    print("Enqueueing ingest_media task...")
    task = celery_app.send_task('tasks.ingest_media', args=(file_path, file_type))
    print(f"Ingest Task ID: {task.id}")
    print()
    
    # Monitor progress
    print("Waiting for ingest to complete...")
    start_time = time.time()
    last_state = None
    
    while True:
        try:
            state = task.state
            
            # Print state changes
            if state != last_state:
                elapsed = time.time() - start_time
                print(f"[{elapsed:.1f}s] State: {state}")
                last_state = state
            
            if state == "SUCCESS":
                result = task.result
                elapsed = time.time() - start_time
                print(f"\n{'='*80}")
                print(f"✓ Ingest Task SUCCEEDED in {elapsed:.1f}s")
                print(f"{'='*80}")
                print(f"Result: {result}")
                print()
                
                # If ingest succeeded, monitor the process_video/process_image task
                if result.get("status") == "dispatched":
                    print(f"Processing Task ID: {result.get('task_id')}")
                    print("\nWaiting for process_{file_type} task...")
                    monitor_child_task(result.get("task_id"), file_type)
                
                break
            
            elif state == "FAILURE":
                elapsed = time.time() - start_time
                print(f"\n{'='*80}")
                print(f"✗ Ingest Task FAILED in {elapsed:.1f}s")
                print(f"{'='*80}")
                print(f"Exception: {task.result}")
                print()
                sys.exit(1)
            
            elif state == "RETRY":
                elapsed = time.time() - start_time
                info = task.info
                print(f"[{elapsed:.1f}s] Retry: {info}")
            
            time.sleep(1)
        
        except KeyboardInterrupt:
            print("\n\nInterrupted by user")
            sys.exit(0)


def monitor_child_task(task_id: str, file_type: str) -> None:
    """Monitor the child process_video or process_image task."""
    from celery.result import AsyncResult
    
    task = AsyncResult(task_id, app=celery_app)
    start_time = time.time()
    last_state = None
    
    while True:
        try:
            state = task.state
            
            if state != last_state:
                elapsed = time.time() - start_time
                print(f"[{elapsed:.1f}s] State: {state}")
                last_state = state
            
            if state == "SUCCESS":
                result = task.result
                elapsed = time.time() - start_time
                print(f"\n{'='*80}")
                print(f"✓ Process {file_type.upper()} Task SUCCEEDED in {elapsed:.1f}s")
                print(f"{'='*80}")
                print(f"Result: {result}")
                print()
                break
            
            elif state == "FAILURE":
                elapsed = time.time() - start_time
                print(f"\n{'='*80}")
                print(f"✗ Process {file_type.upper()} Task FAILED in {elapsed:.1f}s")
                print(f"{'='*80}")
                print(f"Exception: {task.result}")
                print()
                
                # Print traceback if available
                try:
                    tb = task.traceback
                    if tb:
                        print(f"Traceback:\n{tb}")
                except:
                    pass
                
                sys.exit(1)
            
            elif state == "RETRY":
                elapsed = time.time() - start_time
                info = task.info
                print(f"[{elapsed:.1f}s] Retry: {info}")
            
            time.sleep(1)
        
        except KeyboardInterrupt:
            print("\n\nInterrupted by user")
            sys.exit(0)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    
    file_path = sys.argv[1]
    test_single_file(file_path)
