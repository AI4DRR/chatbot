"""Queries on the ``chat_messages`` table."""

from __future__ import annotations

from typing import Any
from uuid import UUID

import psycopg
from psycopg.rows import DictRow
from psycopg.types.json import Json


def get_session_messages(conn: psycopg.Connection[DictRow], session_id: UUID) -> list[DictRow]:
    """Every message of a session, oldest first."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, role, content, metadata, token_input, token_output, cost_estimate, created_at
            FROM chat_messages
            WHERE session_id = %s
            ORDER BY created_at ASC
            """,
            (session_id,),
        )
        return cur.fetchall()


def count_messages(conn: psycopg.Connection[DictRow], session_id: UUID) -> int:
    """How many messages the session holds."""
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) AS n FROM chat_messages WHERE session_id = %s", (session_id,))
        row = cur.fetchone()
    return int(row["n"]) if row else 0


def get_messages_slice(
    conn: psycopg.Connection[DictRow], session_id: UUID, offset: int, limit: int
) -> list[DictRow]:
    """Messages ``offset`` .. ``offset+limit`` in chronological order (oldest first)."""
    if limit <= 0:
        return []
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, role, content, metadata, created_at
            FROM chat_messages
            WHERE session_id = %s
            ORDER BY created_at ASC
            OFFSET %s LIMIT %s
            """,
            (session_id, offset, limit),
        )
        return cur.fetchall()


def get_recent_messages(conn: psycopg.Connection[DictRow], session_id: UUID, limit: int) -> list[DictRow]:
    """The newest ``limit`` messages of a session, returned oldest first."""
    if limit <= 0:
        return []
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, role, content, metadata, created_at
            FROM chat_messages
            WHERE session_id = %s
            ORDER BY created_at DESC
            LIMIT %s
            """,
            (session_id, limit),
        )
        rows = cur.fetchall()
    rows.reverse()
    return rows


def get_last_message(conn: psycopg.Connection[DictRow], session_id: UUID) -> DictRow | None:
    """The newest message of a session (its metadata carries the current topic), or ``None``."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, role, metadata, created_at
            FROM chat_messages
            WHERE session_id = %s
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (session_id,),
        )
        return cur.fetchone()


def save_message(
    conn: psycopg.Connection[DictRow],
    session_id: UUID,
    role: str,
    content: str,
    metadata: dict[str, Any] | None,
    token_input: int | None = None,
    token_output: int | None = None,
    cost_estimate: float | None = None,
) -> DictRow:
    """Insert one message and return its id and timestamp.

    ``created_at`` is ``clock_timestamp()`` rather than the column default
    (``CURRENT_TIMESTAMP`` = transaction start): both messages of a turn are
    written in one transaction and history is ordered by ``created_at``, so
    they must not share a timestamp.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO chat_messages
                (session_id, role, content, metadata, token_input, token_output, cost_estimate, created_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, clock_timestamp())
            RETURNING id, created_at
            """,
            (
                session_id,
                role,
                content,
                Json(metadata) if metadata is not None else None,
                token_input,
                token_output,
                cost_estimate,
            ),
        )
        row = cur.fetchone()
    if row is None:
        raise RuntimeError("message insert returned no row")
    return row
