-- =========================
-- Administrator bootstrap (2026-09-20)
-- Idempotent, like 002-004. An invitation may carry the administrator flag:
-- the account it produces is created with users.is_admin = TRUE. Only the
-- operator CLI (python -m app.db.admin_ops bootstrap) issues such invitations;
-- the management API never does.
-- =========================

ALTER TABLE invitations ADD COLUMN IF NOT EXISTS grants_admin BOOLEAN NOT NULL DEFAULT FALSE;
