-- Migration: add model_version column to media_files
-- Run once against the lumen database.
--
-- Purpose: Record which CLIP model produced each embedding vector.
-- This is critical for detecting embedding drift — if you upgrade from
-- clip-ViT-B-32 (512-dim) to clip-ViT-L-14 (768-dim), rows with the old
-- model_version must be re-indexed before semantic search works correctly.
--
-- Usage (from host):
--   docker exec -i lumen-postgres psql -U lumen_user -d lumen < scripts/migrate_add_model_version.sql
--
-- Idempotent: safe to run multiple times (column is only added if absent).

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'media_files' AND column_name = 'model_version'
    ) THEN
        ALTER TABLE media_files ADD COLUMN model_version VARCHAR(100);
        CREATE INDEX idx_model_version ON media_files (model_version);
        RAISE NOTICE 'Added model_version column and index.';
    ELSE
        RAISE NOTICE 'model_version column already exists — skipping.';
    END IF;
END $$;

-- Backfill existing rows with a placeholder so they're identifiable as
-- "indexed before model tracking was added" rather than NULL (unknown).
UPDATE media_files
SET model_version = 'pre-tracking'
WHERE model_version IS NULL AND processing_status = 'done';

-- Verify
SELECT
    model_version,
    COUNT(*) AS count,
    MIN(processed_at) AS earliest,
    MAX(processed_at) AS latest
FROM media_files
WHERE processing_status = 'done'
GROUP BY model_version
ORDER BY count DESC;
