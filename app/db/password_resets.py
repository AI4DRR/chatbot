"""Queries on the ``password_resets`` table. Tokens are handled hashed only."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

import psycopg
from psycopg.rows import DictRow

COLUMNS = (
    "r.id, r.user_id, r.token_hash, r.created_at, r.expires_at, r.consumed_at, r.revoked_at, "
    "u.email, u.password_hash IS NOT NULL AS has_password, u.disabled_at"
)
FROM = "password_resets r JOIN users u ON u.id = r.user_id"


def create(
    conn: psycopg.Connection[DictRow], user_id: UUID, token_hash: str, expires_at: datetime
) -> DictRow:
    """Insert one reset request and return it."""
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO password_resets (user_id, token_hash, expires_at) VALUES (%s, %s, %s) RETURNING id",
            (user_id, token_hash, expires_at),
        )
        row = cur.fetchone()
    if row is None:
        raise RuntimeError("password reset insert returned no row")
    got = get(conn, row["id"])
    if got is None:
        raise RuntimeError("password reset vanished after insert")
    return got


def get(conn: psycopg.Connection[DictRow], reset_id: UUID) -> DictRow | None:
    """One reset request by id."""
    with conn.cursor() as cur:
        cur.execute(f"SELECT {COLUMNS} FROM {FROM} WHERE r.id = %s", (reset_id,))
        return cur.fetchone()


def get_by_token_hash(conn: psycopg.Connection[DictRow], token_hash: str) -> DictRow | None:
    """The request behind a token hash (read-only check)."""
    with conn.cursor() as cur:
        cur.execute(f"SELECT {COLUMNS} FROM {FROM} WHERE r.token_hash = %s", (token_hash,))
        return cur.fetchone()


def get_by_token_hash_for_update(conn: psycopg.Connection[DictRow], token_hash: str) -> DictRow | None:
    """The request behind a token hash, locked for the rest of the transaction (reset completion)."""
    with conn.cursor() as cur:
        cur.execute(f"SELECT {COLUMNS} FROM {FROM} WHERE r.token_hash = %s FOR UPDATE OF r", (token_hash,))
        return cur.fetchone()


def revoke_open_for_user(conn: psycopg.Connection[DictRow], user_id: UUID) -> int:
    """Close every open request of a user (a new request replaces the old links); returns the count."""
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE password_resets SET revoked_at = CURRENT_TIMESTAMP
            WHERE user_id = %s AND consumed_at IS NULL AND revoked_at IS NULL
            """,
            (user_id,),
        )
        return cur.rowcount


def consume(conn: psycopg.Connection[DictRow], reset_id: UUID) -> None:
    """Record that the request changed the password."""
    with conn.cursor() as cur:
        cur.execute("UPDATE password_resets SET consumed_at = CURRENT_TIMESTAMP WHERE id = %s", (reset_id,))


def delete(conn: psycopg.Connection[DictRow], reset_id: UUID) -> None:
    """Remove a request whose e-mail could not be sent (nothing was ever delivered)."""
    with conn.cursor() as cur:
        cur.execute("DELETE FROM password_resets WHERE id = %s", (reset_id,))
