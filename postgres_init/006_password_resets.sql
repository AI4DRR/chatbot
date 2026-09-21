-- =========================
-- Password reset (2026-09-20)
-- Idempotent, like 002-005. "Forgot password" for local accounts: a random
-- token goes out in the reset link; only its SHA-256 is stored. One row per
-- request; a new request revokes the user's earlier open rows.
-- =========================

CREATE TABLE IF NOT EXISTS password_resets (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id       UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    token_hash    CHAR(64) UNIQUE NOT NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    expires_at    TIMESTAMPTZ NOT NULL,
    consumed_at   TIMESTAMPTZ,                                            -- set when the password was changed
    revoked_at    TIMESTAMPTZ                                             -- set when replaced by a newer request
);

CREATE INDEX IF NOT EXISTS idx_password_resets_user_live ON password_resets(user_id) WHERE consumed_at IS NULL AND revoked_at IS NULL;
