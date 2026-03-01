-- ============================================================================
-- Lumen - Database Schema Initialization
-- Executed automatically by PostgreSQL init container
-- ============================================================================

-- Create extension for UUID support if not exists
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- ============================================================================
-- media_files table - Stores metadata about processed media
-- ============================================================================
CREATE TABLE IF NOT EXISTS media_files (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    file_hash VARCHAR(64) UNIQUE NOT NULL,
    file_path TEXT NOT NULL,
    file_type VARCHAR(10) NOT NULL,
    file_size_bytes VARCHAR(20),
    width VARCHAR(10),
    height VARCHAR(10),
    duration_secs VARCHAR(20),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    exif_data JSONB,
    qdrant_point_id UUID,
    processing_status VARCHAR(20) DEFAULT 'pending',
    error_message TEXT,
    processed_at TIMESTAMP WITH TIME ZONE
);

-- Create indexes for common queries
CREATE INDEX IF NOT EXISTS idx_media_files_file_hash 
    ON media_files(file_hash);

CREATE INDEX IF NOT EXISTS idx_media_files_processing_status 
    ON media_files(processing_status);

CREATE INDEX IF NOT EXISTS idx_media_files_file_type 
    ON media_files(file_type);

CREATE INDEX IF NOT EXISTS idx_media_files_created_at 
    ON media_files(created_at DESC);

CREATE INDEX IF NOT EXISTS idx_media_files_qdrant_point_id 
    ON media_files(qdrant_point_id);

-- ============================================================================
-- Grant permissions to application user
-- ============================================================================
GRANT ALL PRIVILEGES ON TABLE media_files TO lumen_user;

-- ============================================================================
-- Real-time notification triggers for dashboard & external systems
-- ============================================================================

-- Notify when media processing status changes (pending -> processing -> completed/failed)
CREATE OR REPLACE FUNCTION notify_processing_status() RETURNS trigger AS $$
BEGIN
  -- Only notify if status actually changed
  IF (OLD IS NULL) OR (OLD.processing_status IS DISTINCT FROM NEW.processing_status) THEN
    PERFORM pg_notify('media_processing', json_build_object(
      'id', NEW.id::text,
      'file_path', NEW.file_path,
      'file_type', NEW.file_type,
      'status', NEW.processing_status,
      'error_message', NEW.error_message,
      'processed_at', NEW.processed_at
    )::text);
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Notify when media is successfully embedded in Qdrant
CREATE OR REPLACE FUNCTION notify_vector_indexed() RETURNS trigger AS $$
BEGIN
  -- Only notify when qdrant_point_id is first set (embedding completed)
  IF (OLD.qdrant_point_id IS NULL) AND (NEW.qdrant_point_id IS NOT NULL) THEN
    PERFORM pg_notify('vector_indexed', json_build_object(
      'id', NEW.id::text,
      'file_path', NEW.file_path,
      'qdrant_point_id', NEW.qdrant_point_id::text,
      'vector_indexed_at', NOW()
    )::text);
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Attach triggers to media_files table
DROP TRIGGER IF EXISTS media_processing_trigger ON media_files;
CREATE TRIGGER media_processing_trigger
AFTER INSERT OR UPDATE ON media_files
FOR EACH ROW EXECUTE FUNCTION notify_processing_status();

DROP TRIGGER IF EXISTS media_vector_trigger ON media_files;
CREATE TRIGGER media_vector_trigger
AFTER UPDATE ON media_files
FOR EACH ROW EXECUTE FUNCTION notify_vector_indexed();
