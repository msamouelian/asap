-- Migration 001: RAG document collections (Phase 1 foundations).
-- Apply to a LIVE database:  psql -U asap -d postgres -f 001_document_collections.sql
-- Fresh installs get the same DDL from init.sql. All statements idempotent.

-- ---------------------------------------------------------------------------
-- user_document_collection — collection enabled for ALL of a user's
-- conversations. document_collection_id references the Neo4j
-- DocumentCollection node's id attribute (no FK — cross-store reference).
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS user_document_collection (
    id                      UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id                 UUID        NOT NULL REFERENCES asap_user(id) ON DELETE CASCADE,
    document_collection_id  UUID        NOT NULL,
    created_ts              TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (user_id, document_collection_id)
);

CREATE INDEX IF NOT EXISTS idx_user_document_collection_user
    ON user_document_collection(user_id);
CREATE INDEX IF NOT EXISTS idx_user_document_collection_collection
    ON user_document_collection(document_collection_id);

-- ---------------------------------------------------------------------------
-- conversation_document_collection — collection enabled for one conversation.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS conversation_document_collection (
    id                      UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    conversation_id         UUID        NOT NULL REFERENCES conversation(id) ON DELETE CASCADE,
    document_collection_id  UUID        NOT NULL,
    created_ts              TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (conversation_id, document_collection_id)
);

CREATE INDEX IF NOT EXISTS idx_conversation_document_collection_conversation
    ON conversation_document_collection(conversation_id);
CREATE INDEX IF NOT EXISTS idx_conversation_document_collection_collection
    ON conversation_document_collection(document_collection_id);

-- ---------------------------------------------------------------------------
-- conversation_message_document_chunk — which Neo4j DocumentChunk nodes were
-- retrieved (RAG) for a given user message. Chunk TEXT is not duplicated
-- here; rehydrating a conversation fetches the text from Neo4j by id.
-- retrieval_order preserves the injection order for faithful reconstruction.
-- A collection whose chunks appear here can no longer be hard-deleted.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS conversation_message_document_chunk (
    id                       UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    conversation_message_id  UUID        NOT NULL REFERENCES conversation_message(id) ON DELETE CASCADE,
    document_chunk_id        UUID        NOT NULL,
    retrieval_order          INTEGER     NOT NULL DEFAULT 0,
    UNIQUE (conversation_message_id, document_chunk_id)
);

CREATE INDEX IF NOT EXISTS idx_cm_document_chunk_message
    ON conversation_message_document_chunk(conversation_message_id);
CREATE INDEX IF NOT EXISTS idx_cm_document_chunk_chunk
    ON conversation_message_document_chunk(document_chunk_id);

-- ---------------------------------------------------------------------------
-- document_processing_job — one row per collection ingestion job, updated by
-- the docling worker as it progresses; polled by the UI.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS document_processing_job (
    id                      UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id                 UUID        NOT NULL REFERENCES asap_user(id),
    document_collection_id  UUID        NOT NULL,
    status                  VARCHAR(20) NOT NULL DEFAULT 'pending'
                                CHECK (status IN ('pending', 'running', 'completed', 'failed')),
    total_documents         INTEGER     NOT NULL DEFAULT 0,
    processed_documents     INTEGER     NOT NULL DEFAULT 0,
    total_chunks            INTEGER     NOT NULL DEFAULT 0,
    current_document        VARCHAR(512),
    error                   TEXT,
    created_ts              TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_ts              TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_ts            TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_document_processing_job_user
    ON document_processing_job(user_id);

-- One in-flight job per user, enforced by the database rather than app code.
CREATE UNIQUE INDEX IF NOT EXISTS uq_document_processing_job_active_user
    ON document_processing_job(user_id)
    WHERE status IN ('pending', 'running');
