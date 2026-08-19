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
