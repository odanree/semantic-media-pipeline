-- ============================================================================
-- Migration: Add worker observability columns to media_files
-- Applied when upgrading from schema predating PR #10
-- Safe to run multiple times (uses ADD COLUMN IF NOT EXISTS)
-- ============================================================================

ALTER TABLE media_files ADD COLUMN IF NOT EXISTS embedding_started_at TIMESTAMP;
ALTER TABLE media_files ADD COLUMN IF NOT EXISTS worker_id VARCHAR(100);
ALTER TABLE media_files ADD COLUMN IF NOT EXISTS frame_cache_hit BOOLEAN DEFAULT FALSE;
ALTER TABLE media_files ADD COLUMN IF NOT EXISTS embedding_ms INTEGER;
