# Test Scripts for Lumen Ingest Pipeline

## `test_single_file.py` - Test a Single File

Manually inject a single file into the ingest pipeline for debugging and validation.

### Quick Start

**PowerShell (Windows)**:
```powershell
.\scripts\test-single-file.ps1 "J:\Pixel 9 Aug to Sept 2025\PXL_20250910_020850495.mp4"
```

**Bash**:
```bash
docker exec lumen-worker python scripts/test_single_file.py /mnt/source/Pixel\ 9\ Aug\ to\ Sept\ 2025/PXL_20250910_020850495.mp4
```

### What It Does

1. **Validates the file** exists and is readable
2. **Detects file type** (image or video) from extension
3. **Enqueues `ingest_media` task** which:
   - Computes file hash
   - Checks for duplicates
   - Creates database record
   - Dispatches to `process_video` or `process_image`
4. **Monitors task progress** in real-time, showing:
   - Task state transitions (PENDING → SUCCESS/FAILURE)
   - Elapsed time for each stage
   - Any errors or retry attempts
5. **Monitors child processing task** to completion

### Example Output

**Success**:
```
================================================================================
TEST: Single File Ingest
================================================================================
File: /mnt/source/Pixel 9 Aug to Sept 2025/PXL_20250910_020850495.mp4
Exists: True
File Type: video
File Size: 45.23 MB

Enqueueing ingest_media task...
Ingest Task ID: abc123def456

[0.5s] State: PENDING
[1.2s] State: SUCCESS
================================================================================
✓ Ingest Task SUCCEEDED in 1.2s
================================================================================
Result: {'status': 'dispatched', 'task_id': 'xyz789', ...}

Processing Task ID: xyz789

Waiting for process_video task...
[0.0s] State: PENDING
[1.5s] State: STARTED
[5.2s] State: SUCCESS
================================================================================
✓ Process VIDEO Task SUCCEEDED in 5.2s
================================================================================
Result: {'status': 'success', 'frames_extracted': 62, 'vectors_indexed': 62}
```

**Failure with FFmpeg Timeout Info**:
```
[45.3s] State: FAILURE
================================================================================
✗ Process VIDEO Task FAILED in 45.3s
================================================================================
Exception: FFmpeg timeout (1200s) extracting frames from /mnt/source/...
(video duration: 125.4s) — Set FFMPEG_TIMEOUT env var to increase limit

Traceback:
  File "/app/ingest/ffmpeg.py", line 130, in extract_keyframes
    raise FFmpegError(f"FFmpeg timeout: {timeout}s...")
```

### Use Cases

**1. Test FFmpeg Timeout Fix**
- Pick a longer video that was timing out before
- Monitor logs for timeout calculation:
  ```
  [FFmpeg] Duration: 125.4s | Base timeout: 1200s | Computed: 1388s | Final: 1388s
  ```

**2. Debug a Specific File**
- Find a file causing issues in logs
- Test it in isolation to see exact error
- Compare success vs. failure metrics

**3. Validate Production Fix**
- Before deploying, test the previously-failing file
- Confirm it now succeeds with new timeout logic

**4. Monitor Real-Time Processing**
- Watch frame extraction, embedding, and vectoring progress
- See timing for each stage

### Kill Hanging Test

If a test is stuck:
```powershell
# Press Ctrl+C to interrupt gracefully
# Or kill from another terminal:
docker exec lumen-worker pkill -f test_single_file.py
```

### Environment Variables

- `FFMPEG_TIMEOUT` - FFmpeg subprocess timeout in seconds (default: 1200)
- `KEYFRAME_FPS` - Frames per second to extract (default: 0.5)
- `KEYFRAME_RESOLUTION` - Frame resolution for CLIP (default: 224)
- `EMBEDDING_BATCH_SIZE` - Batch size for embedding (default: 32)

Override in `.env`:
```dotenv
FFMPEG_TIMEOUT=1800
```

Then rebuild worker:
```powershell
docker-compose up -d --build worker
```

### Debugging Tips

1. **Watch all worker logs** while test runs:
   ```powershell
   docker logs -f lumen-worker | Select-String "\[FFmpeg\]"
   ```

2. **Monitor task queue** in another terminal:
   ```powershell
   docker exec lumen-redis redis-cli LLEN celery
   ```

3. **Check database record** after test:
   ```powershell
   docker exec lumen-postgres psql -U lumen_user -d lumen -c "SELECT id, file_path, processing_status FROM media_files ORDER BY created_at DESC LIMIT 1;"
   ```

4. **View task details in Flower**:
   - Open http://localhost:5555
   - Look for the ingest_media and process_video tasks
   - Check "Result" tab for detailed output
