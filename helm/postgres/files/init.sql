-- ASAP database schema
-- Executed exactly once by the Postgres Docker entrypoint when the data
-- directory is empty (i.e. fresh PersistentVolume). All statements use
-- CREATE IF NOT EXISTS so re-runs are safe.

-- ---------------------------------------------------------------------------
-- asap_user
-- keycloak_id: the 'sub' UUID claim from Keycloak — stable unique identifier.
-- email/full_name: synced from the JWT on every login.
-- role: 'admin' | 'user' — derived from the asap_roles JWT claim on login.
-- status: 'active' | 'inactive' — controlled by admins in the ASAP UI.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS asap_user (
    id                UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    keycloak_id       VARCHAR(255) NOT NULL UNIQUE,
    email             VARCHAR(255) NOT NULL,
    full_name         VARCHAR(255) NOT NULL,
    role              VARCHAR(20)  NOT NULL DEFAULT 'user'
                          CHECK (role IN ('admin', 'user')),
    status            VARCHAR(20)  NOT NULL DEFAULT 'active'
                          CHECK (status IN ('active', 'inactive')),
    -- Nullable FK to system_prompt — added via ALTER TABLE below after
    -- system_prompt is created (avoids forward-reference error).
    system_prompt_id  UUID,
    created_at        TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at        TIMESTAMPTZ  NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_asap_user_keycloak_id ON asap_user(keycloak_id);

-- ---------------------------------------------------------------------------
-- tag  — controlled vocabulary for filtering prompts
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS tag (
    id          UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    name        VARCHAR(50)  NOT NULL UNIQUE,
    description VARCHAR(200)
);

-- ---------------------------------------------------------------------------
-- user_prompt  — reusable prompts created by users
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS user_prompt (
    id                UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    creating_user_id  UUID         NOT NULL REFERENCES asap_user(id),
    title             VARCHAR(100) NOT NULL,
    prompt_text       TEXT         NOT NULL,
    create_ts         TIMESTAMPTZ  NOT NULL DEFAULT now(),
    modified_ts       TIMESTAMPTZ,
    global            BOOLEAN      NOT NULL DEFAULT false
);

CREATE INDEX IF NOT EXISTS idx_user_prompt_creating_user
    ON user_prompt(creating_user_id);
CREATE INDEX IF NOT EXISTS idx_user_prompt_global
    ON user_prompt("global");

-- ---------------------------------------------------------------------------
-- user_prompt_tag  — many-to-many: user_prompt ↔ tag
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS user_prompt_tag (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_prompt_id  UUID NOT NULL REFERENCES user_prompt(id) ON DELETE CASCADE,
    tag_id          UUID NOT NULL REFERENCES tag(id),
    UNIQUE (user_prompt_id, tag_id)
);

CREATE INDEX IF NOT EXISTS idx_user_prompt_tag_prompt
    ON user_prompt_tag(user_prompt_id);
CREATE INDEX IF NOT EXISTS idx_user_prompt_tag_tag
    ON user_prompt_tag(tag_id);

-- ---------------------------------------------------------------------------
-- system_prompt  — system prompts; all are implicitly global.
-- Soft-deleted by setting expiration_ts to the current timestamp.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS system_prompt (
    id                UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    creating_user_id  UUID         NOT NULL REFERENCES asap_user(id),
    title             VARCHAR(100) NOT NULL,
    description       VARCHAR(1000),
    prompt_text       TEXT         NOT NULL,
    is_default        BOOLEAN      NOT NULL DEFAULT false,
    create_ts         TIMESTAMPTZ  NOT NULL DEFAULT now(),
    modified_ts       TIMESTAMPTZ,
    expiration_ts     TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_system_prompt_creating_user
    ON system_prompt(creating_user_id);

-- FK from asap_user to system_prompt — defined here (after system_prompt) to
-- avoid a forward-reference error. SET NULL ensures deleting a system_prompt
-- does not cascade to deleting the user.
ALTER TABLE asap_user
    ADD CONSTRAINT fk_asap_user_system_prompt
    FOREIGN KEY (system_prompt_id)
    REFERENCES system_prompt(id)
    ON DELETE SET NULL;

-- ---------------------------------------------------------------------------
-- system_prompt_tag  — many-to-many: system_prompt ↔ tag
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS system_prompt_tag (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    system_prompt_id  UUID NOT NULL REFERENCES system_prompt(id) ON DELETE CASCADE,
    tag_id            UUID NOT NULL REFERENCES tag(id),
    UNIQUE (system_prompt_id, tag_id)
);

CREATE INDEX IF NOT EXISTS idx_system_prompt_tag_prompt
    ON system_prompt_tag(system_prompt_id);
CREATE INDEX IF NOT EXISTS idx_system_prompt_tag_tag
    ON system_prompt_tag(tag_id);

-- ---------------------------------------------------------------------------
-- conversation_folder — per-user tree for organizing conversations.
-- The root folder 'All' is implicit and never stored: parent_id NULL means
-- a direct child of 'All'. Name uniqueness within a parent is enforced by
-- the backend; the FK actions are backstops (the backend refuses to delete
-- non-empty folders).
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS conversation_folder (
    id         UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id    UUID         NOT NULL REFERENCES asap_user(id) ON DELETE CASCADE,
    parent_id  UUID         REFERENCES conversation_folder(id) ON DELETE RESTRICT,
    name       VARCHAR(100) NOT NULL,
    created_ts TIMESTAMPTZ  NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_conversation_folder_user
    ON conversation_folder(user_id);
CREATE INDEX IF NOT EXISTS idx_conversation_folder_parent
    ON conversation_folder(parent_id);

-- ---------------------------------------------------------------------------
-- conversation
-- last_prompt_tokens: prompt token count from the most recent LLM call,
--   used to compute context-usage percentage in the UI. NULL until the
--   first turn completes or if the inference server does not report usage.
-- folder_id: NULL = the conversation lives in the implicit root folder 'All'.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS conversation (
    id                  UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id             UUID         NOT NULL REFERENCES asap_user(id),
    title               VARCHAR(100),
    last_prompt_tokens  INTEGER,
    folder_id           UUID         REFERENCES conversation_folder(id) ON DELETE SET NULL,
    created_ts          TIMESTAMPTZ  NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_conversation_user
    ON conversation(user_id);
CREATE INDEX IF NOT EXISTS idx_conversation_folder_id
    ON conversation(folder_id);

-- ---------------------------------------------------------------------------
-- conversation_message
-- role: 'user' | 'assistant' | 'tool_use' | 'tool_result'
-- reasoning: captured when the LLM emits reasoning tokens (e.g. delta.reasoning
--            from LM Studio, delta.reasoning_content from o-series). Stored
--            separately from message so the main content stays clean.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS conversation_message (
    id               UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    conversation_id  UUID        NOT NULL REFERENCES conversation(id) ON DELETE CASCADE,
    role             VARCHAR(20) NOT NULL,
    message          TEXT        NOT NULL,
    reasoning        TEXT,
    created_ts       TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_conversation_message_conversation
    ON conversation_message(conversation_id);

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

-- Collection metadata carried on the job row (the worker, not the backend,
-- creates the Neo4j DocumentCollection node — MCP access is read-only).
ALTER TABLE document_processing_job
    ADD COLUMN IF NOT EXISTS collection_title       VARCHAR(100)  NOT NULL DEFAULT '',
    ADD COLUMN IF NOT EXISTS collection_description VARCHAR(1000) NOT NULL DEFAULT '',
    ADD COLUMN IF NOT EXISTS visibility             VARCHAR(10)   NOT NULL DEFAULT 'private'
        CHECK (visibility IN ('private', 'shared')),
    ADD COLUMN IF NOT EXISTS source_location        VARCHAR(512)  NOT NULL DEFAULT '';
-- The distilled search query used for RAG retrieval on a user message.
-- NULL when no distillation ran (RAG off, gate closed before distillation,
-- or the distiller fell back to the raw question).
ALTER TABLE conversation_message
    ADD COLUMN IF NOT EXISTS retrieval_query TEXT;
-- Knowledge-graph extraction artifacts (kggenerator package).
-- One current artifact per collection: the LLM-extracted, within-collection-
-- merged entity/relation graph, checkpointed between pipeline phases so
-- resolve/load re-run without repeating the expensive LLM extraction.
-- status: extracting | extracted | failed
CREATE TABLE IF NOT EXISTS kg_artifact (
    collection_uri   TEXT PRIMARY KEY,
    collection_title TEXT NOT NULL,
    run_id           TEXT NOT NULL,
    status           TEXT NOT NULL,
    surface          JSONB,
    graph            JSONB,
    stats            JSONB,
    error            TEXT,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Cross-collection entity-resolution output (kggenerator resolve phase).
-- One row per resolve run: the merged knowledge graph plus the full
-- decision log (auto-merges, LLM adjudications, vetoes, near-misses) for
-- audit. The load phase reads the most recent row.
CREATE TABLE IF NOT EXISTS kg_resolution (
    id         SERIAL PRIMARY KEY,
    run_id     TEXT NOT NULL,
    graph      JSONB NOT NULL,
    decisions  JSONB NOT NULL,
    stats      JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
