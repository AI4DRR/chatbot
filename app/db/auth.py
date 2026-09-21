"""Queries for authentication: user lookup/creation by credential, browser sessions."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

import psycopg
from psycopg.rows import DictRow

USER_COLUMNS = (
    "id, email, name, first_name, last_name, department, unit, role_type, password_hash, entra_oid, "
    "is_admin, disabled_at, created_at, updated_at"
)


def get_user_by_id(conn: psycopg.Connection[DictRow], user_id: UUID) -> DictRow | None:
    """The user row, or ``None``."""
    with conn.cursor() as cur:
        cur.execute(f"SELECT {USER_COLUMNS} FROM users WHERE id = %s", (user_id,))
        return cur.fetchone()


def get_user_by_email(conn: psycopg.Connection[DictRow], email: str) -> DictRow | None:
    """Case-insensitive lookup by email."""
    with conn.cursor() as cur:
        cur.execute(f"SELECT {USER_COLUMNS} FROM users WHERE lower(email) = lower(%s)", (email,))
        return cur.fetchone()


def get_user_by_entra_oid(conn: psycopg.Connection[DictRow], oid: str) -> DictRow | None:
    """The user linked to an Entra object id, or ``None``."""
    with conn.cursor() as cur:
        cur.execute(f"SELECT {USER_COLUMNS} FROM users WHERE entra_oid = %s", (oid,))
        return cur.fetchone()


def create_local_user(
    conn: psycopg.Connection[DictRow],
    email: str,
    password_hash: str,
    first_name: str,
    last_name: str,
    department: str | None,
    is_admin: bool = False,
) -> DictRow:
    """Insert a user with a local password. Raises ``psycopg.errors.UniqueViolation`` if the email exists."""
    with conn.cursor() as cur:
        cur.execute(
            f"""
            INSERT INTO users (email, name, first_name, last_name, department, password_hash, is_admin)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            RETURNING {USER_COLUMNS}
            """,
            (
                email,
                f"{first_name} {last_name}".strip(),
                first_name,
                last_name,
                department,
                password_hash,
                is_admin,
            ),
        )
        row = cur.fetchone()
    if row is None:
        raise RuntimeError("user insert returned no row")
    return row


def upsert_entra_user(
    conn: psycopg.Connection[DictRow],
    oid: str,
    email: str,
    name: str | None,
    first_name: str | None,
    last_name: str | None,
) -> DictRow:
    """Link or create the user for an Entra identity.

    Matched by ``entra_oid`` first; otherwise by email (an existing local or
    legacy user signing in with Entra for the first time gets linked). Name
    fields are filled only where empty, so a user's own edits are kept.
    """
    with conn.cursor() as cur:
        cur.execute(
            f"""
            UPDATE users SET
                entra_oid  = %(oid)s,
                name       = COALESCE(users.name, %(name)s),
                first_name = COALESCE(users.first_name, %(first_name)s),
                last_name  = COALESCE(users.last_name, %(last_name)s),
                updated_at = CURRENT_TIMESTAMP
            WHERE entra_oid = %(oid)s OR (entra_oid IS NULL AND lower(email) = lower(%(email)s))
            RETURNING {USER_COLUMNS}
            """,
            {"oid": oid, "email": email, "name": name, "first_name": first_name, "last_name": last_name},
        )
        row = cur.fetchone()
        if row is not None:
            return row
        cur.execute(
            f"""
            INSERT INTO users (email, name, first_name, last_name, entra_oid)
            VALUES (%s, %s, %s, %s, %s)
            RETURNING {USER_COLUMNS}
            """,
            (email, name, first_name, last_name, oid),
        )
        row = cur.fetchone()
    if row is None:
        raise RuntimeError("user insert returned no row")
    return row


# --- browser sessions -------------------------------------------------------------


def create_session(
    conn: psycopg.Connection[DictRow], token_hash: str, user_id: UUID, method: str, expires_at: datetime
) -> None:
    """Record a new browser session."""
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO auth_sessions (token_hash, user_id, method, expires_at) VALUES (%s, %s, %s, %s)",
            (token_hash, user_id, method, expires_at),
        )


def get_live_session_user(conn: psycopg.Connection[DictRow], token_hash: str) -> DictRow | None:
    """The user of an unexpired session (touching ``last_seen_at``), or ``None``."""
    user_columns = ", ".join("u." + c.strip() for c in USER_COLUMNS.split(","))
    with conn.cursor() as cur:
        cur.execute(
            f"""
            UPDATE auth_sessions s SET last_seen_at = CURRENT_TIMESTAMP
            FROM users u
            WHERE s.token_hash = %s AND s.expires_at > CURRENT_TIMESTAMP AND u.id = s.user_id
              AND u.disabled_at IS NULL
            RETURNING {user_columns}, s.method AS session_method
            """,
            (token_hash,),
        )
        return cur.fetchone()


def delete_session(conn: psycopg.Connection[DictRow], token_hash: str) -> str | None:
    """Invalidate one session; returns its sign-in method (``entra`` / ``local``), ``None`` if unknown."""
    with conn.cursor() as cur:
        cur.execute("DELETE FROM auth_sessions WHERE token_hash = %s RETURNING method", (token_hash,))
        row = cur.fetchone()
    return str(row["method"]) if row else None


def delete_user_sessions(conn: psycopg.Connection[DictRow], user_id: UUID) -> int:
    """Invalidate every session of a user (e.g. after a password change)."""
    with conn.cursor() as cur:
        cur.execute("DELETE FROM auth_sessions WHERE user_id = %s", (user_id,))
        return cur.rowcount


def purge_expired_sessions(conn: psycopg.Connection[DictRow]) -> int:
    """Housekeeping: drop expired rows."""
    with conn.cursor() as cur:
        cur.execute("DELETE FROM auth_sessions WHERE expires_at <= CURRENT_TIMESTAMP")
        return cur.rowcount


# --- administrators -----------------------------------------------------------------


def list_users(conn: psycopg.Connection[DictRow]) -> list[DictRow]:
    """Every user, newest first (the management page's Active list)."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, email, name, first_name, last_name, department, is_admin, disabled_at,
                   password_hash IS NOT NULL AS has_password, entra_oid IS NOT NULL AS entra_linked,
                   created_at, updated_at
            FROM users
            ORDER BY created_at DESC
            """
        )
        return cur.fetchall()


def get_user_by_id_for_update(conn: psycopg.Connection[DictRow], user_id: UUID) -> DictRow | None:
    """The user row locked for the rest of the transaction (status changes)."""
    with conn.cursor() as cur:
        cur.execute(f"SELECT {USER_COLUMNS} FROM users WHERE id = %s FOR UPDATE", (user_id,))
        return cur.fetchone()


def lock_enabled_admins(conn: psycopg.Connection[DictRow]) -> int:
    """Lock every enabled administrator row and return how many there are (last-admin guard)."""
    with conn.cursor() as cur:
        cur.execute("SELECT id FROM users WHERE is_admin = TRUE AND disabled_at IS NULL FOR UPDATE")
        return len(cur.fetchall())


def set_disabled(conn: psycopg.Connection[DictRow], user_id: UUID, disabled: bool) -> DictRow | None:
    """Suspend (``disabled_at = now``) or re-enable (``NULL``) an account; returns the row, or ``None``."""
    with conn.cursor() as cur:
        cur.execute(
            f"""
            UPDATE users
            SET disabled_at = CASE WHEN %s THEN COALESCE(disabled_at, CURRENT_TIMESTAMP) ELSE NULL END,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = %s
            RETURNING {USER_COLUMNS}
            """,
            (disabled, user_id),
        )
        return cur.fetchone()


def user_activity(conn: psycopg.Connection[DictRow], user_id: UUID) -> DictRow:
    """Safe operational counts for the management view: chats, last chat activity, live sign-ins."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT
              (SELECT count(*) FROM chat_sessions WHERE user_id = %(id)s) AS chat_sessions,
              (SELECT max(updated_at) FROM chat_sessions WHERE user_id = %(id)s) AS last_chat_at,
              (SELECT count(*) FROM auth_sessions WHERE user_id = %(id)s AND expires_at > CURRENT_TIMESTAMP)
                AS signed_in_sessions,
              (SELECT max(last_seen_at) FROM auth_sessions WHERE user_id = %(id)s) AS last_seen_at
            """,
            {"id": user_id},
        )
        row = cur.fetchone()
    if row is None:
        raise RuntimeError("activity query returned no row")
    return row


def set_password(conn: psycopg.Connection[DictRow], user_id: UUID, password_hash: str) -> bool:
    """Replace a user's local password hash; ``False`` if no such user."""
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE users SET password_hash = %s, updated_at = CURRENT_TIMESTAMP WHERE id = %s",
            (password_hash, user_id),
        )
        return cur.rowcount == 1


def set_admin(conn: psycopg.Connection[DictRow], email: str, is_admin: bool) -> bool:
    """Grant or revoke the administrator flag for an existing user; ``False`` if no such email."""
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE users SET is_admin = %s, updated_at = CURRENT_TIMESTAMP WHERE lower(email) = lower(%s)",
            (is_admin, email),
        )
        return cur.rowcount == 1
