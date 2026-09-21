-- =========================
-- Extensions
-- =========================
CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE EXTENSION IF NOT EXISTS vector;

-- =========================
-- Users
-- =========================
CREATE TABLE users (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email           VARCHAR(255) UNIQUE NOT NULL,
    name            VARCHAR(255),
    department      VARCHAR(255),      -- UN Department (e.g., UNDRR)
    unit            VARCHAR(255),      -- Unit/Office (e.g., ROAP)
    role_type       INTEGER NOT NULL DEFAULT 0, -- 0=user, 1=admin
    created_at      TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- =========================
-- Chat Sessions
-- =========================
CREATE TABLE chat_sessions (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         UUID NOT NULL
                        REFERENCES users(id)
                        ON DELETE CASCADE
                        ON UPDATE CASCADE,
    title           VARCHAR(255),
    subject         TEXT,
    previous_answer TEXT,
    is_active       BOOLEAN NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

ALTER TABLE chat_sessions
ADD COLUMN memory_summary TEXT,
ADD COLUMN memory_facts JSONB NOT NULL DEFAULT '[]',
ADD COLUMN memory_turns INTEGER NOT NULL DEFAULT 0;

-- =========================
-- Chat Messages
-- =========================
CREATE TABLE chat_messages (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id      UUID NOT NULL
                        REFERENCES chat_sessions(id)
                        ON DELETE CASCADE
                        ON UPDATE CASCADE,
    role            VARCHAR(20) NOT NULL,
    content         TEXT NOT NULL,
    metadata        JSONB,
    token_input     INTEGER,
    token_output    INTEGER,
    cost_estimate   NUMERIC(12,6),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

ALTER TABLE chat_messages
ADD CONSTRAINT chk_chat_messages_role
CHECK (role IN ('user','assistant','system'));

-- =========================
-- Context Chunks (RAG audit)
-- =========================
CREATE TABLE chat_context_chunks (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    message_id      UUID NOT NULL
                        REFERENCES chat_messages(id)
                        ON DELETE CASCADE
                        ON UPDATE CASCADE,
    source          TEXT,
    chunk_id        TEXT,
    similarity      DOUBLE PRECISION,
    metadata        JSONB
);

-- =========================
-- Indexes
-- =========================

-- Sidebar session listing
CREATE INDEX idx_chat_sessions_user_updated
ON chat_sessions(user_id, updated_at DESC);

-- Thread retrieval
CREATE INDEX idx_chat_messages_session_created
ON chat_messages(session_id, created_at ASC);

CREATE INDEX idx_chat_messages_session_role
ON chat_messages(session_id, role);

-- JSONB analytics future-proof
CREATE INDEX idx_chat_messages_metadata
ON chat_messages USING GIN (metadata);

-- Cost dashboard: time-range queries on assistant messages
CREATE INDEX idx_chat_messages_role_created
ON chat_messages(role, created_at DESC);

-- Cost dashboard: token/cost aggregations by session
CREATE INDEX idx_chat_messages_cost
ON chat_messages(session_id, cost_estimate, token_input, token_output)
WHERE role = 'assistant';

-- Cost dashboard: department/unit grouping via sessions→users
CREATE INDEX idx_chat_sessions_user_id
ON chat_sessions(user_id);

-- Cost dashboard: LIST vs SYNTHESIS mode breakdown
CREATE INDEX idx_chat_messages_metadata_mode
ON chat_messages((metadata->>'mode'))
WHERE role = 'assistant';

-- =========================
-- updated_at Auto Trigger
-- =========================
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
   NEW.updated_at = CURRENT_TIMESTAMP;
   RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER update_users_updated_at
BEFORE UPDATE ON users
FOR EACH ROW
EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER update_sessions_updated_at
BEFORE UPDATE ON chat_sessions
FOR EACH ROW
EXECUTE FUNCTION update_updated_at_column();