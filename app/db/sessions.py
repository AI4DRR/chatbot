"""Queries on the ``chat_sessions`` table."""

from __future__ import annotations

from uuid import UUID

import psycopg
from psycopg.rows import DictRow


def create_session(conn: psycopg.Connection[DictRow], user_id: UUID, title: str | None) -> DictRow:
    """Insert a session for ``user_id`` and return the new row."""
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO chat_sessions (user_id, title)
            VALUES (%s, %s)
            RETURNING id, user_id, title, subject, created_at, updated_at,
                      memory_summary, memory_facts, memory_turns
            """,
            (user_id, title),
        )
        row = cur.fetchone()
    if row is None:
        raise RuntimeError("session insert returned no row")
    return row


def get_session(conn: psycopg.Connection[DictRow], session_id: UUID) -> DictRow | None:
    """The session row, or ``None`` when it does not exist."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, user_id, title, subject, created_at, updated_at,
                   memory_summary, memory_facts, memory_turns
            FROM chat_sessions
            WHERE id = %s
            """,
            (session_id,),
        )
        return cur.fetchone()


def list_sessions(conn: psycopg.Connection[DictRow], user_id: UUID) -> list[DictRow]:
    """All sessions of a user, newest created first (legacy ordering)."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, title, created_at, updated_at, memory_turns
            FROM chat_sessions
            WHERE user_id = %s
            ORDER BY created_at DESC
            """,
            (user_id,),
        )
        return cur.fetchall()


def update_session_title(conn: psycopg.Connection[DictRow], session_id: UUID, title: str) -> bool:
    """Set the title. Returns ``False`` when no session has that id."""
    with conn.cursor() as cur:
        cur.execute("UPDATE chat_sessions SET title = %s WHERE id = %s", (title, session_id))
        return cur.rowcount == 1


def touch_session(conn: psycopg.Connection[DictRow], session_id: UUID) -> bool:
    """Bump ``updated_at`` (the sidebar's ``last_message_at``). ``False`` when no session has that id."""
    with conn.cursor() as cur:
        cur.execute("UPDATE chat_sessions SET updated_at = CURRENT_TIMESTAMP WHERE id = %s", (session_id,))
        return cur.rowcount == 1


def update_subject(conn: psycopg.Connection[DictRow], session_id: UUID, subject: str) -> bool:
    """Set the active subject (Phase 2). ``False`` when no session has that id."""
    with conn.cursor() as cur:
        cur.execute("UPDATE chat_sessions SET subject = %s WHERE id = %s", (subject, session_id))
        return cur.rowcount == 1


def update_memory(conn: psycopg.Connection[DictRow], session_id: UUID, summary: str, turns: int) -> bool:
    """Store the compacted conversation summary and how many messages it covers."""
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE chat_sessions SET memory_summary = %s, memory_turns = %s WHERE id = %s",
            (summary, turns, session_id),
        )
        return cur.rowcount == 1
