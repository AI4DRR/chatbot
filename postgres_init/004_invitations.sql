-- =========================
-- Invitations (2026-09-20)
-- Idempotent, like 002/003. Invitation-only local accounts: an administrator
-- invites an email; the invitee completes profile + password from a link that
-- carries a random token. Only the token's SHA-256 is stored.
-- =========================

CREATE TABLE IF NOT EXISTS invitations (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email             VARCHAR(255) NOT NULL,
    token_hash        CHAR(64) UNIQUE NOT NULL,
    invited_by        UUID REFERENCES users(id) ON DELETE SET NULL,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    expires_at        TIMESTAMPTZ NOT NULL,
    consumed_at       TIMESTAMPTZ,                                      -- set when the account was created
    revoked_at        TIMESTAMPTZ,                                      -- set by revoke / replaced by resend
    accepted_user_id  UUID REFERENCES users(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_invitations_email_lower ON invitations(lower(email));
CREATE INDEX IF NOT EXISTS idx_invitations_live ON invitations(expires_at) WHERE consumed_at IS NULL AND revoked_at IS NULL;
