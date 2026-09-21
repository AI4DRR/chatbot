"""Invitation-only local accounts (docs/architecture.md §8).

Lifecycle: administrator invites an e-mail → a random token goes out in the
setup link (only its SHA-256 is stored) → the invitee completes profile and
password within ``INVITATION_TTL_HOURS`` → the account is created, the
invitation consumed, a normal session opened. Resend replaces the token;
revoke closes the invitation. Public self-registration no longer exists.

Failure rule for e-mail: the invitation row is written first; if delivery
fails the row is deleted again and the caller gets a 502, so a stored
invitation always means a message went out.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

import psycopg
from psycopg.rows import DictRow

from app.db import auth as auth_db
from app.db import invitations as inv_db
from app.errors import AppError, ConflictError, NotFoundError, ValidationError
from app.integrations.mail import Mail, Mailer
from app.schemas.auth import AuthUser, InvitationInfo
from app.services import auth as auth_service

logger = logging.getLogger(__name__)

SETUP_PATH = "/account-setup"


@dataclass(frozen=True)
class IssuedInvitation:
    """A stored invitation plus the raw token, which exists only until the e-mail is built."""

    info: InvitationInfo
    token: str


def _status(row: DictRow, now: datetime) -> str:
    if row["consumed_at"] is not None:
        return "consumed"
    if row["revoked_at"] is not None:
        return "revoked"
    if row["expires_at"] <= now:
        return "expired"
    return "pending"


def _info(row: DictRow, now: datetime | None = None) -> InvitationInfo:
    now = now or datetime.now(UTC)
    return InvitationInfo(
        id=str(row["id"]),
        email=row["email"],
        created_at=row["created_at"],
        expires_at=row["expires_at"],
        status=_status(row, now),
        invited_by_email=row["invited_by_email"],
        grants_admin=bool(row["grants_admin"]),
    )


def setup_url(app_base_url: str, token: str) -> str:
    """The link in the e-mail. The token rides in the URL fragment, so it never reaches server access logs."""
    return f"{app_base_url}{SETUP_PATH}#token={token}"


def build_invitation_mail(to: str, link: str, ttl_hours: int) -> Mail:
    """The invitation message (plain text + HTML), one CTA, expiry stated."""
    text = (
        "You've been invited to AI4DRR Chatbot\n\n"
        "You have been invited to access the AI4DRR Chatbot, the UNDRR disaster-risk-reduction "
        "knowledge assistant.\n\n"
        "Complete your account setup using the link below:\n\n"
        f"{link}\n\n"
        f"This invitation is intended for your email address and will expire in {ttl_hours} hours.\n"
        "If you did not expect this invitation, you can ignore this message.\n"
    )
    button = (
        f'<a href="{link}" style="display:inline-block;background:#004f91;color:#fff;text-decoration:none;'
        'padding:12px 20px;border-radius:8px;font-weight:600">Set up my account</a>'
    )
    html = (
        '<div style="font-family:Segoe UI,system-ui,Arial,sans-serif;max-width:520px;margin:0 auto;'
        'color:#0d0d0d">'
        '<p style="font-size:20px;font-weight:700;margin:24px 0 8px">'
        "You've been invited to AI4DRR Chatbot</p>"
        "<p>You have been invited to access the <strong>AI4DRR Chatbot</strong>, the UNDRR "
        "disaster-risk-reduction knowledge assistant.</p>"
        "<p>Complete your account setup using the link below.</p>"
        f'<p style="margin:24px 0">{button}</p>'
        '<p style="color:#6f6f6f;font-size:13px">This invitation is intended for your email address and '
        f"will expire in {ttl_hours} hours. If you did not expect this invitation, you can ignore this "
        "message.</p></div>"
    )
    return Mail(to=to, subject="You've been invited to AI4DRR Chatbot", text=text, html=html)


# --- administrator actions --------------------------------------------------------------


def list_pending(conn: psycopg.Connection[DictRow]) -> list[InvitationInfo]:
    """Open invitations (pending and expired), newest first."""
    now = datetime.now(UTC)
    return [_info(r, now) for r in inv_db.list_open(conn)]


def _issue(
    conn: psycopg.Connection[DictRow],
    email: str,
    invited_by: UUID | None,
    ttl_hours: int,
    grants_admin: bool = False,
) -> IssuedInvitation:
    token = auth_service.new_token()
    row = inv_db.create(
        conn,
        email,
        auth_service.token_hash(token),
        invited_by,
        datetime.now(UTC) + timedelta(hours=ttl_hours),
        grants_admin,
    )
    return IssuedInvitation(info=_info(row), token=token)


def invite(
    conn: psycopg.Connection[DictRow], email: str, invited_by: AuthUser, ttl_hours: int
) -> IssuedInvitation:
    """Create an invitation for ``email`` (case-insensitive).

    Refused (409) when an account with that e-mail already exists or a
    pending, unexpired invitation is open — use resend for the latter. An
    expired or revoked earlier invitation does not block a new one.
    """
    email = email.strip()
    if auth_db.get_user_by_email(conn, email) is not None:
        raise ConflictError("An account with this email already exists")
    live = inv_db.find_live_for_email(conn, email)
    if live is not None:
        raise ConflictError("A pending invitation already exists for this email; resend it instead")
    issued = _issue(conn, email, UUID(invited_by.id), ttl_hours)
    logger.info("invitation created %s by %s", issued.info.id, invited_by.id)
    return issued


def resend(conn: psycopg.Connection[DictRow], invitation_id: str, ttl_hours: int) -> IssuedInvitation:
    """Replace an open invitation with a fresh token and expiry (the old token stops working)."""
    row = inv_db.get(conn, _parse_id(invitation_id))
    if row is None or row["consumed_at"] is not None or row["revoked_at"] is not None:
        raise NotFoundError("Invitation not found or no longer open")
    if auth_db.get_user_by_email(conn, row["email"]) is not None:
        raise ConflictError("An account with this email already exists")
    inv_db.revoke(conn, row["id"])
    issued = _issue(conn, row["email"], row["invited_by"], ttl_hours, bool(row["grants_admin"]))
    logger.info("invitation %s replaced by %s", row["id"], issued.info.id)
    return issued


def revoke(conn: psycopg.Connection[DictRow], invitation_id: str) -> InvitationInfo:
    """Close an open invitation; its token stops working immediately."""
    iid = _parse_id(invitation_id)
    if not inv_db.revoke(conn, iid):
        raise NotFoundError("Invitation not found or no longer open")
    row = inv_db.get(conn, iid)
    assert row is not None  # just updated
    logger.info("invitation revoked %s", iid)
    return _info(row)


def discard_unsent(conn: psycopg.Connection[DictRow], invitation_id: str) -> None:
    """The e-mail could not be sent: remove the row so no phantom invitation remains."""
    inv_db.delete(conn, UUID(invitation_id))


def send_invitation(mailer: Mailer, issued: IssuedInvitation, app_base_url: str, ttl_hours: int) -> None:
    """Deliver the message; raises ``IntegrationError``/``IntegrationConfigError`` on failure."""
    mailer.send(build_invitation_mail(issued.info.email, setup_url(app_base_url, issued.token), ttl_hours))


# --- operator bootstrap (CLI only) ---------------------------------------------------------


def issue_admin_bootstrap(conn: psycopg.Connection[DictRow], email: str, ttl_hours: int) -> IssuedInvitation:
    """An invitation whose account is created as an administrator (``python -m app.db.admin_ops bootstrap``).

    Solves the first-administrator problem without a public endpoint: the
    operator issues the setup link from the trusted CLI and the invitee
    completes the normal account-setup page. Any open invitation for the
    e-mail is revoked first, so running the command again simply replaces
    the link. Refused (409) when the account already exists — promote it
    instead. Never reachable through the management API.
    """
    email = email.strip()
    if auth_db.get_user_by_email(conn, email) is not None:
        raise ConflictError("An account with this email already exists")
    live = inv_db.find_live_for_email(conn, email)
    if live is not None:
        inv_db.revoke(conn, live["id"])
        logger.info("invitation %s revoked (replaced by administrator bootstrap)", live["id"])
    issued = _issue(conn, email, None, ttl_hours, grants_admin=True)
    logger.info("administrator bootstrap invitation created %s", issued.info.id)
    return issued


# --- invitee actions -------------------------------------------------------------------


class SetupTokenError(AppError):
    """The setup link is not usable (unknown, expired, revoked or already used)."""

    status_code = 410
    detail = "This invitation link is not valid"


def _live_row(row: DictRow | None, now: datetime) -> DictRow:
    if row is None:
        raise SetupTokenError("This invitation link is not valid")
    status = _status(row, now)
    if status != "pending":
        raise SetupTokenError(f"This invitation link is no longer valid ({status})")
    return row


def check_token(conn: psycopg.Connection[DictRow], token: str) -> InvitationInfo:
    """Validate a setup token (read-only); returns the invitation with its fixed e-mail."""
    row = _live_row(inv_db.get_by_token_hash(conn, auth_service.token_hash(token)), datetime.now(UTC))
    return _info(row)


def complete_setup(
    conn: psycopg.Connection[DictRow],
    token: str,
    password: str,
    confirm: str,
    first_name: str,
    last_name: str,
    department: str | None,
) -> AuthUser:
    """Create the account from a valid invitation and consume it — one transaction, row locked.

    The e-mail is the invitation's, never the client's. If an account with
    that e-mail appeared in the meantime the invitation is refused (409) and
    nothing is written. An invitation issued by the operator bootstrap
    (``grants_admin``) creates the account with ``is_admin = TRUE``.
    """
    auth_service.validate_new_password(password, confirm)
    first, last = first_name.strip(), last_name.strip()
    if not first or not last:
        raise ValidationError("First name and last name are required")
    now = datetime.now(UTC)
    row = _live_row(inv_db.get_by_token_hash_for_update(conn, auth_service.token_hash(token)), now)
    if auth_db.get_user_by_email(conn, row["email"]) is not None:
        raise ConflictError("An account with this email already exists")
    user_row = auth_db.create_local_user(
        conn,
        row["email"],
        auth_service.hash_password(password),
        first,
        last,
        (department or "").strip() or None,
        is_admin=bool(row["grants_admin"]),
    )
    inv_db.consume(conn, row["id"], user_row["id"])
    logger.info(
        "invitation %s consumed: account %s created%s",
        row["id"],
        user_row["id"],
        " (administrator)" if row["grants_admin"] else "",
    )
    return auth_service.current_user_from_row(user_row)


def _parse_id(value: str) -> UUID:
    try:
        return UUID(value)
    except ValueError:
        raise NotFoundError("Invitation not found") from None
