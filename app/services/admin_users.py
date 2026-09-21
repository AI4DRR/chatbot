"""Chatbot Management user actions: view, disable, enable (docs/architecture.md §7).

Disabling is a suspension, never a deletion: the row, its chats and its
history stay; ``users.disabled_at`` is set and every ``auth_sessions`` row
of the user is deleted in the same transaction. From then on the account
cannot sign in locally or with Microsoft, cannot hold a session, and cannot
use a password-reset link. Enabling clears the flag and nothing else — old
sessions are not restored; the person signs in again.
"""

from __future__ import annotations

import logging
from uuid import UUID

import psycopg
from psycopg.rows import DictRow

from app.db import auth as auth_db
from app.errors import ConflictError, NotFoundError
from app.schemas.auth import AdminUser, AdminUserDetail, AuthUser

logger = logging.getLogger(__name__)


def _parse_id(value: str) -> UUID:
    try:
        return UUID(value)
    except ValueError:
        raise NotFoundError("User not found") from None


def _row_to_admin_user(row: DictRow) -> AdminUser:
    return AdminUser(
        id=str(row["id"]),
        email=row["email"],
        name=row["name"],
        first_name=row["first_name"],
        last_name=row["last_name"],
        department=row["department"],
        is_admin=bool(row["is_admin"]),
        has_password=row["password_hash"] is not None,
        entra_linked=row["entra_oid"] is not None,
        disabled=row["disabled_at"] is not None,
        disabled_at=row["disabled_at"],
        created_at=row["created_at"],
    )


def get_detail(conn: psycopg.Connection[DictRow], user_id: str) -> AdminUserDetail:
    """The View panel data: profile, status, methods and safe activity counts (404 if unknown)."""
    row = auth_db.get_user_by_id(conn, _parse_id(user_id))
    if row is None:
        raise NotFoundError("User not found")
    activity = auth_db.user_activity(conn, row["id"])
    base = _row_to_admin_user(row)
    return AdminUserDetail(
        **base.model_dump(),
        unit=row["unit"],
        updated_at=row["updated_at"],
        chat_sessions=int(activity["chat_sessions"]),
        last_chat_at=activity["last_chat_at"],
        signed_in_sessions=int(activity["signed_in_sessions"]),
        last_seen_at=activity["last_seen_at"],
    )


def disable(conn: psycopg.Connection[DictRow], user_id: str, actor: AuthUser) -> AdminUser:
    """Suspend an account and end its sessions — one transaction, row locked.

    Refused (409) for the caller's own account and for the last enabled
    administrator (the enabled administrator rows are locked first, so two
    concurrent disables cannot both pass the check). Already disabled → no-op.
    """
    uid = _parse_id(user_id)
    if uid == UUID(actor.id):
        raise ConflictError("You cannot disable your own account")
    row = auth_db.get_user_by_id_for_update(conn, uid)
    if row is None:
        raise NotFoundError("User not found")
    if row["disabled_at"] is not None:
        return _row_to_admin_user(row)
    if row["is_admin"] and auth_db.lock_enabled_admins(conn) <= 1:
        raise ConflictError("Cannot disable the last enabled administrator")
    updated = auth_db.set_disabled(conn, uid, True)
    assert updated is not None  # locked above
    ended = auth_db.delete_user_sessions(conn, uid)
    logger.info("user %s disabled by %s (%d session(s) ended)", uid, actor.id, ended)
    return _row_to_admin_user(updated)


def enable(conn: psycopg.Connection[DictRow], user_id: str, actor: AuthUser) -> AdminUser:
    """Lift a suspension; nothing else changes and no session is restored. Already enabled → no-op."""
    uid = _parse_id(user_id)
    row = auth_db.get_user_by_id_for_update(conn, uid)
    if row is None:
        raise NotFoundError("User not found")
    if row["disabled_at"] is None:
        return _row_to_admin_user(row)
    updated = auth_db.set_disabled(conn, uid, False)
    assert updated is not None
    logger.info("user %s enabled by %s", uid, actor.id)
    return _row_to_admin_user(updated)
