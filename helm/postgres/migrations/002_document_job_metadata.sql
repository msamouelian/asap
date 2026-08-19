-- Migration 002: collection metadata on document_processing_job.
-- The backend cannot write to Neo4j (MCP is read-only by design), so the
-- worker creates the DocumentCollection node. The metadata the user supplied
-- at upload time therefore travels via the job row.

ALTER TABLE document_processing_job
    ADD COLUMN IF NOT EXISTS collection_title       VARCHAR(100)  NOT NULL DEFAULT '',
    ADD COLUMN IF NOT EXISTS collection_description VARCHAR(1000) NOT NULL DEFAULT '',
    ADD COLUMN IF NOT EXISTS visibility             VARCHAR(10)   NOT NULL DEFAULT 'private',
    ADD COLUMN IF NOT EXISTS source_location        VARCHAR(512)  NOT NULL DEFAULT '';

-- Postgres cannot add a CHECK with IF NOT EXISTS; guard via catalog lookup.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'chk_document_processing_job_visibility'
    ) THEN
        ALTER TABLE document_processing_job
            ADD CONSTRAINT chk_document_processing_job_visibility
            CHECK (visibility IN ('private', 'shared'));
    END IF;
END $$;
