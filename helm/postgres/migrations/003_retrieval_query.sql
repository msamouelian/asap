-- The distilled search query used for RAG retrieval on a user message.
-- NULL when no distillation ran (RAG off, gate closed before distillation,
-- or the distiller fell back to the raw question).
ALTER TABLE conversation_message
    ADD COLUMN IF NOT EXISTS retrieval_query TEXT;
