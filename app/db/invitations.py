"""Queries on the ``invitations`` table. Tokens are handled hashed only."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

import psycopg
from psycopg.rows import DictRow

COLUMNS = (
    "i.id, i.email, i.token_hash, i.invited_by, i.created_at, i.expires_at, i.consumed_at, i.revoked_at, "
    "i.accepted_user_id, i.grants_admin, u.email AS invited_by_email"
)
FROM = "invitations i LEFT JOIN users u ON u.id = i.invited_by"


def create(
    conn: psycopg.Connection[DictRow],
    email: str,
    token_hash: str,
    invited_by: UUID | None,
    expires_at: datetime,
    grants_admin: bool = False,
) -> DictRow:
    """Insert one invitation and return it (``grants_admin``: the account it produces is an administrator)."""
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO invitations (email, token_hash, invited_by, expires_at, grants_admin)
            VALUES (%s, %s, %s, %s, %s)
            RETURNING id
            """,
            (email, token_hash, invited_by, expires_at, grants_admin),
        )
        row = cur.fetchone()
    if row is None:
        raise RuntimeError("invitation insert returned no row")
    got = get(conn, row["id"])
    if got is None:
        raise RuntimeError("invitation vanished after insert")
    return got


def get(conn: psycopg.Connection[DictRow], invitation_id: UUID) -> DictRow | None:
    """One invitation by id."""
    with conn.cursor() as cur:
        cur.execute(f"SELECT {COLUMNS} FROM {FROM} WHERE i.id = %s", (invitation_id,))
        return cur.fetchone()


def get_by_token_hash_for_update(conn: psycopg.Connection[DictRow], token_hash: str) -> DictRow | None:
    """The invitation behind a token hash, locked for the rest of the transaction (setup completion)."""
    with conn.cursor() as cur:
        cur.execute(
            f"SELECT {COLUMNS} FROM {FROM} WHERE i.token_hash = %s FOR UPDATE OF i",
            (token_hash,),
        )
        return cur.fetchone()


def get_by_token_hash(conn: psycopg.Connection[DictRow], token_hash: str) -> DictRow | None:
    """The invitation behind a token hash (read-only check)."""
    with conn.cursor() as cur:
        cur.execute(f"SELECT {COLUMNS} FROM {FROM} WHERE i.token_hash = %s", (token_hash,))
        return cur.fetchone()


def find_live_for_email(conn: psycopg.Connection[DictRow], email: str) -> DictRow | None:
    """An unconsumed, unrevoked, unexpired invitation for the email (case-insensitive), if any."""
    with conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT {COLUMNS} FROM {FROM}
            WHERE lower(i.email) = lower(%s) AND i.consumed_at IS NULL AND i.revoked_at IS NULL
              AND i.expires_at > CURRENT_TIMESTAMP
            ORDER BY i.created_at DESC LIMIT 1
            """,
            (email,),
        )
        return cur.fetchone()


def list_open(conn: psycopg.Connection[DictRow]) -> list[DictRow]:
    """Invitations not yet consumed or revoked (expired ones included, for the Pending list), newest first."""
    with conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT {COLUMNS} FROM {FROM}
            WHERE i.consumed_at IS NULL AND i.revoked_at IS NULL
            ORDER BY i.created_at DESC
            """
        )
        return cur.fetchall()


def revoke(conn: psycopg.Connection[DictRow], invitation_id: UUID) -> bool:
    """Mark an open invitation revoked; ``False`` if it was not open."""
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE invitations SET revoked_at = CURRENT_TIMESTAMP
            WHERE id = %s AND consumed_at IS NULL AND revoked_at IS NULL
            """,
            (invitation_id,),
        )
        return cur.rowcount == 1


def delete(conn: psycopg.Connection[DictRow], invitation_id: UUID) -> None:
    """Remove an invitation whose e-mail could not be sent (nothing was ever delivered)."""
    with conn.cursor() as cur:
        cur.execute("DELETE FROM invitations WHERE id = %s", (invitation_id,))


def consume(conn: psycopg.Connection[DictRow], invitation_id: UUID, user_id: UUID) -> None:
    """Record that the invitation produced an account."""
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE invitations SET consumed_at = CURRENT_TIMESTAMP, accepted_user_id = %s WHERE id = %s",
            (user_id, invitation_id),
        )
