-- Migration: Add vote_events table for observability and lineage tracking
--
-- This table tracks all user votes (manual upvotes, bulk cascades)
-- with batch_id linking related votes together for observability queries.

CREATE TABLE IF NOT EXISTS vote_events (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    -- Batch grouping: links seed upvote to all cascading bulk votes
    batch_id UUID NOT NULL,
    INDEX idx_vote_batch_id (batch_id),

    -- Lineage: which batch triggered this one (NULL for seed/manual votes)
    triggered_by_batch_id UUID,
    INDEX idx_vote_triggered_by (triggered_by_batch_id),

    -- Vote details
    file_path TEXT NOT NULL,
    INDEX idx_vote_file_path (file_path),

    audio_segment_index INTEGER,
    INDEX idx_vote_audio_segment (audio_segment_index),

    vote INTEGER NOT NULL,  -- 1, -1, 0

    -- Vote source: how the vote was created
    vote_source VARCHAR(20) NOT NULL DEFAULT 'manual',
    INDEX idx_vote_source (vote_source),
    -- Values: 'manual' (user clicked), 'bulk_upvote' (cascade from similar search), 'auto_label' (reserved)

    -- Context: what query led to the vote
    search_query VARCHAR(512),
    INDEX idx_vote_query (search_query),

    -- For bulk votes: similarity score to seed (if applicable)
    similarity_score FLOAT,

    -- Metadata
    timestamp TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP NOT NULL,
    INDEX idx_vote_timestamp (timestamp),

    -- Stats rollup on seed votes
    -- When user manually upvotes then bulk votes all similar, seed vote records the cascade
    cascaded_count INTEGER DEFAULT 0,  -- How many bulk votes resulted from this seed
    cascade_threshold FLOAT  -- Min similarity for bulk vote (e.g., 0.90)
);

-- Composite indexes for common queries
CREATE INDEX idx_vote_batch_source ON vote_events(batch_id, vote_source);
CREATE INDEX idx_vote_query_source ON vote_events(search_query, vote_source);
CREATE INDEX idx_vote_batch_timestamp ON vote_events(batch_id, timestamp);
