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
