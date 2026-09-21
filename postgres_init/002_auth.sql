-- =========================
-- Authentication (2026-09-20)
-- Idempotent: runs at initdb for fresh databases and again at every API
-- start (app/db/migrate.py) so existing databases pick it up. Adds to the
-- existing users table instead of replacing it: email stays the identity key.
-- =========================

ALTER TABLE users ADD COLUMN IF NOT EXISTS first_name    VARCHAR(255);
ALTER TABLE users ADD COLUMN IF NOT EXISTS last_name     VARCHAR(255);
ALTER TABLE users ADD COLUMN IF NOT EXISTS password_hash TEXT;             -- argon2id; NULL = no local password
ALTER TABLE users ADD COLUMN IF NOT EXISTS entra_oid     VARCHAR(64);      -- Entra ID object id; NULL = never signed in with Entra

CREATE UNIQUE INDEX IF NOT EXISTS idx_users_entra_oid ON users(entra_oid) WHERE entra_oid IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS idx_users_email_lower ON users(lower(email));

-- Server-side browser sessions. The cookie carries a random token; only its
-- SHA-256 is stored, so a database read cannot be replayed as a cookie.
CREATE TABLE IF NOT EXISTS auth_sessions (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    token_hash      CHAR(64) UNIQUE NOT NULL,
    user_id         UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE ON UPDATE CASCADE,
    method          VARCHAR(16) NOT NULL,                                   -- 'entra' | 'local'
    created_at      TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    expires_at      TIMESTAMPTZ NOT NULL,
    last_seen_at    TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_auth_sessions_user ON auth_sessions(user_id);
CREATE INDEX IF NOT EXISTS idx_auth_sessions_expires ON auth_sessions(expires_at);
