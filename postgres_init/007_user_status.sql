-- =========================
-- Account status (2026-09-20)
-- Idempotent, like 002-006. A disabled account keeps every row it has (users,
-- chats, sessions history) but cannot sign in or hold a session. NULL = enabled,
-- so every existing user stays enabled after this migration.
-- =========================

ALTER TABLE users ADD COLUMN IF NOT EXISTS disabled_at TIMESTAMPTZ;
