-- =========================
-- Administrators (2026-09-20)
-- Idempotent, like 002: applied at initdb and at every API start.
-- Administrators are ordinary users with this flag; same sessions, same sign-in.
-- Existing rows stay non-admin. Promotion is an operational step
-- (docs/architecture.md §7), never an API call.
-- =========================

ALTER TABLE users ADD COLUMN IF NOT EXISTS is_admin BOOLEAN NOT NULL DEFAULT FALSE;
