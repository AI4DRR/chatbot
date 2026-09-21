"""Forgot / reset password for local accounts (docs/architecture.md §6).

Lifecycle: the person enters their e-mail → if a local account with a
password exists, a random token goes out in a reset link (only its SHA-256
is stored; ``PASSWORD_RESET_TTL_MINUTES``) → the person chooses a new
password on the reset page → the token is consumed, the password replaced
and every existing session of the user invalidated, in one transaction.

The public answer to a request is always the same, whatever the e-mail:
unknown addresses, Microsoft-only accounts and even a failed e-mail send are
indistinguishable to the caller (the failure is logged, with safe details
only). A new request replaces the user's earlier open links.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from uuid import UUID

import psycopg
from psycopg.rows import DictRow

from app.db import auth as auth_db
from app.db import password_resets as reset_db
from app.errors import AppError
from app.integrations.mail import Mail, Mailer
from app.schemas.auth import PasswordResetInfo
from app.services import auth as auth_service

logger = logging.getLogger(__name__)

RESET_PATH = "/reset-password"
GENERIC_RESPONSE = "If an account with that email can reset its password, we've sent instructions."


def reset_url(app_base_url: str, token: str) -> str:
    """The link in the e-mail; the token rides in the URL fragment, so it never reaches access logs."""
    return f"{app_base_url}{RESET_PATH}#token={token}"


def build_reset_mail(to: str, link: str, ttl_minutes: int) -> Mail:
    """The reset message (plain text + HTML), one CTA, expiry stated."""
    text = (
        "Reset your AI4DRR Chatbot password\n\n"
        "We received a request to reset the password of your AI4DRR Chatbot account.\n\n"
        "Choose a new password using the link below:\n\n"
        f"{link}\n\n"
        f"The link expires in {ttl_minutes} minutes and can be used once.\n"
        "If you did not request a password reset, you can ignore this message; your password stays "
        "unchanged.\n"
    )
    button = (
        f'<a href="{link}" style="display:inline-block;background:#004f91;color:#fff;text-decoration:none;'
        'padding:12px 20px;border-radius:8px;font-weight:600">Reset my password</a>'
    )
    html = (
        '<div style="font-family:Segoe UI,system-ui,Arial,sans-serif;max-width:520px;margin:0 auto;'
        'color:#0d0d0d">'
        '<p style="font-size:20px;font-weight:700;margin:24px 0 8px">Reset your AI4DRR Chatbot password</p>'
        "<p>We received a request to reset the password of your <strong>AI4DRR Chatbot</strong> account.</p>"
        "<p>Choose a new password using the link below.</p>"
        f'<p style="margin:24px 0">{button}</p>'
        f'<p style="color:#6f6f6f;font-size:13px">The link expires in {ttl_minutes} minutes and can be used '
        "once. If you did not request a password reset, you can ignore this message; your password stays "
        "unchanged.</p></div>"
    )
    return Mail(to=to, subject="Reset your AI4DRR Chatbot password", text=text, html=html)


def _domain(email: str) -> str:
    return email.rsplit("@", 1)[-1].lower() if "@" in email else "?"


# --- request ---------------------------------------------------------------------------


def request_reset(
    conn: psycopg.Connection[DictRow], mailer: Mailer, email: str, app_base_url: str, ttl_minutes: int
) -> None:
    """Handle "forgot password" for ``email``; never raises for reasons that would reveal the account.

    Local account with a password → earlier open requests revoked, a new
    token stored (hashed) and mailed. Anything else → nothing stored, nothing
    sent. A failed send removes the row again and is logged; the caller's
    answer is the same in every case.
    """
    email = email.strip()
    row = auth_db.get_user_by_email(conn, email)
    if row is None or row["password_hash"] is None or row["disabled_at"] is not None:
        # unknown, Microsoft-only or disabled: same silence, same answer for the caller
        logger.info(
            "password reset requested for an unknown, password-less or disabled account (domain %s)",
            _domain(email),
        )
        return
    user_id = UUID(str(row["id"]))
    reset_db.revoke_open_for_user(conn, user_id)
    token = auth_service.new_token()
    reset = reset_db.create(
        conn, user_id, auth_service.token_hash(token), datetime.now(UTC) + timedelta(minutes=ttl_minutes)
    )
    try:
        mailer.send(build_reset_mail(row["email"], reset_url(app_base_url, token), ttl_minutes))
    except AppError as exc:
        reset_db.delete(conn, reset["id"])
        logger.error(
            "password reset e-mail for user %s not sent (%s); request discarded", user_id, exc.detail
        )
        return
    logger.info("password reset e-mail sent: user %s, request %s", user_id, reset["id"])


# --- reset ----------------------------------------------------------------------------


class ResetTokenError(AppError):
    """The reset link is not usable (unknown, expired, replaced or already used)."""

    status_code = 410
    detail = "This password reset link is not valid"


def _status(row: DictRow, now: datetime) -> str:
    if row["consumed_at"] is not None:
        return "used"
    if row["revoked_at"] is not None:
        return "replaced by a newer request"
    if row["expires_at"] <= now:
        return "expired"
    return "pending"


def _live_row(row: DictRow | None, now: datetime) -> DictRow:
    if row is None:
        raise ResetTokenError("This password reset link is not valid")
    status = _status(row, now)
    if status != "pending":
        raise ResetTokenError(f"This password reset link is no longer valid ({status})")
    if not row["has_password"] or row["disabled_at"] is not None:
        # no local password any more, or the account was disabled after the link went out
        raise ResetTokenError("This password reset link is not valid")
    return row


def check_token(conn: psycopg.Connection[DictRow], token: str) -> PasswordResetInfo:
    """Validate a reset token (read-only); returns the account e-mail it belongs to."""
    row = _live_row(reset_db.get_by_token_hash(conn, auth_service.token_hash(token)), datetime.now(UTC))
    return PasswordResetInfo(email=row["email"], expires_at=row["expires_at"])


def complete_reset(conn: psycopg.Connection[DictRow], token: str, password: str, confirm: str) -> str:
    """Set the new password, consume the token and end every session of the user — one transaction.

    Returns the account e-mail. The row is locked, so a token can succeed
    exactly once. The administrator flag and any Microsoft linkage of the
    account are untouched.
    """
    auth_service.validate_new_password(password, confirm)
    now = datetime.now(UTC)
    row = _live_row(reset_db.get_by_token_hash_for_update(conn, auth_service.token_hash(token)), now)
    user_id = UUID(str(row["user_id"]))
    if not auth_db.set_password(conn, user_id, auth_service.hash_password(password)):
        raise ResetTokenError("This password reset link is not valid")
    reset_db.consume(conn, row["id"])
    reset_db.revoke_open_for_user(conn, user_id)
    ended = auth_db.delete_user_sessions(conn, user_id)
    logger.info("password reset completed: user %s, %d session(s) ended", user_id, ended)
    return str(row["email"])
