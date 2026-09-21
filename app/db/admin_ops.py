"""Operational commands for administrators: grant/revoke the flag, and bootstrap the first one.

There is deliberately no API for any of this. Run it inside the API container,
where ``DATABASE_URL`` (and ``APP_BASE_URL``) are configured::

    docker compose exec chatbot-app python -m app.db.admin_ops grant someone@example.org
    docker compose exec chatbot-app python -m app.db.admin_ops revoke someone@example.org
    docker compose exec chatbot-app python -m app.db.admin_ops list
    docker compose exec chatbot-app python -m app.db.admin_ops bootstrap someone@example.org [--send]

``grant``/``revoke`` change an existing user (they sign in once first).
``bootstrap`` (what ``make admin EMAIL=…`` runs) solves the first-administrator
problem: an existing user is promoted; a missing one gets an invitation whose
account is created as an administrator through the normal account-setup
page. The one-time setup link is printed to the operator's terminal (never
stored raw, never logged) or, with ``--send``, e-mailed through the configured
transport. Public registration stays closed; no password is invented.
"""

from __future__ import annotations

import argparse
import logging
import sys
from collections.abc import Callable

import psycopg
from psycopg.rows import DictRow
from pydantic import EmailStr, TypeAdapter, ValidationError

from app.config import Settings, load_settings
from app.db import auth as auth_db
from app.db.connection import connect
from app.errors import AppError
from app.integrations.mail import Mailer, build_mailer
from app.log import configure_logging
from app.services import invitations

logger = logging.getLogger(__name__)

_EMAIL = TypeAdapter(EmailStr)


def _say(text: str) -> None:
    """Operator-facing output (stdout, not the log: it may carry the one-time setup link)."""
    sys.stdout.write(text + "\n")
    sys.stdout.flush()


def _normalise_email(value: str) -> str | None:
    try:
        return str(_EMAIL.validate_python(value.strip()))
    except ValidationError:
        return None


def bootstrap(
    conn: psycopg.Connection[DictRow],
    settings: Settings,
    email: str,
    send: bool,
    mailer_factory: Callable[[Settings], Mailer] = build_mailer,
) -> int:
    """Make ``email`` an administrator; returns the process exit code.

    Existing user → promoted (already an administrator → nothing to do).
    No user → administrator bootstrap invitation; the setup link is printed,
    or sent with ``send`` (a failed send removes the invitation again, like
    the API). Safe to repeat: a second run replaces an unused link.
    """
    user = auth_db.get_user_by_email(conn, email)
    if user is not None:
        if user["is_admin"]:
            _say(f"{user['email']} is already an administrator. Nothing to do.")
            return 0
        auth_db.set_admin(conn, email, True)
        logger.info("%s: administrator flag granted (bootstrap)", email)
        _say(f"{user['email']} exists and has been promoted to administrator.")
        if user["disabled_at"] is not None:
            _say("Note: this account is disabled; enable it in Chatbot Management before it can sign in.")
        return 0

    if not settings.local_auth_enabled:
        _say(f"No account for {email} yet, and local accounts are disabled (LOCAL_AUTH_ENABLED=false).")
        _say("Ask the person to sign in with Microsoft once, then run this command again to promote them.")
        return 1
    try:
        issued = invitations.issue_admin_bootstrap(conn, email, settings.invitation_ttl_hours)
    except AppError as exc:
        _say(f"Refused: {exc.detail}")
        return 1
    conn.commit()  # the row exists whatever happens to delivery below (mirrors the API's failure rule)
    link = invitations.setup_url(settings.app_base_url, issued.token)
    expires = issued.info.expires_at.strftime("%Y-%m-%d %H:%M UTC")
    if send:
        try:
            invitations.send_invitation(
                mailer_factory(settings), issued, settings.app_base_url, settings.invitation_ttl_hours
            )
        except AppError as exc:
            invitations.discard_unsent(conn, issued.info.id)
            conn.commit()
            _say(f"The setup e-mail could not be sent ({exc.detail}); no invitation was kept.")
            _say("Fix EMAIL_TRANSPORT/SMTP_* or run again without --send to get the link here.")
            return 1
        _say(f"Administrator setup e-mail sent to {issued.info.email} (expires {expires}).")
        _say("The account becomes an administrator once they complete the account-setup page.")
        return 0
    _say(f"No account for {issued.info.email} yet. One-time administrator setup link (expires {expires}):")
    _say("")
    _say(f"  {link}")
    _say("")
    _say("Give this link to the person only. It is shown once and is not stored or logged anywhere;")
    _say("run the command again to replace it. Completing the page creates the account as administrator.")
    return 0


def main(argv: list[str] | None = None) -> int:
    """CLI entry point; returns a process exit code."""
    parser = argparse.ArgumentParser(
        description="Administrator operations (no API: run inside the container)."
    )
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("grant", "revoke"):
        sub.add_parser(name, help=f"{name} the administrator flag of an existing user").add_argument("email")
    sub.add_parser("list", help="list administrators")
    boot = sub.add_parser("bootstrap", help="promote an existing user, or issue an administrator setup link")
    boot.add_argument("email")
    boot.add_argument(
        "--send", action="store_true", help="e-mail the link (EMAIL_TRANSPORT) instead of printing it"
    )
    args = parser.parse_args(argv)

    settings = load_settings()
    configure_logging(settings.log_level)
    if not settings.database_url:
        logger.error("DATABASE_URL is not configured")
        return 2
    if args.command == "list":
        with connect(settings.database_url) as conn:
            admins = [u["email"] for u in auth_db.list_users(conn) if u["is_admin"]]
        logger.info("administrators: %s", ", ".join(admins) if admins else "(none)")
        return 0
    email = _normalise_email(args.email)
    if email is None:
        _say(f"'{args.email}' is not a valid e-mail address.")
        return 2
    with connect(settings.database_url) as conn:
        if args.command == "bootstrap":
            return bootstrap(conn, settings, email, args.send)
        changed = auth_db.set_admin(conn, email, args.command == "grant")
    if not changed:
        logger.error("no user with email %s (they must sign in once before promotion)", email)
        return 1
    logger.info("%s: administrator flag %s", email, "granted" if args.command == "grant" else "revoked")
    return 0


if __name__ == "__main__":
    sys.exit(main())
