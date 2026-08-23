-- Conversation folders — per-user tree for organizing conversations.
--
-- The root folder 'All' is implicit and never stored: a folder row with
-- parent_id NULL is a direct child of 'All', and a conversation with
-- folder_id NULL lives in 'All'. Existing conversations are therefore
-- untouched by this migration — they all appear under 'All'.
--
-- Name uniqueness within a parent is enforced by the backend (a partial
-- unique index can't cover the NULL parent without NULLS NOT DISTINCT).
-- The backend also refuses to delete non-empty folders; the FK actions
-- below are only backstops.

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

ALTER TABLE conversation
    ADD COLUMN IF NOT EXISTS folder_id UUID REFERENCES conversation_folder(id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS idx_conversation_folder_id
    ON conversation(folder_id);
