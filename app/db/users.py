"""Queries on the ``users`` table."""

from __future__ import annotations

import psycopg
from psycopg.rows import DictRow


def get_or_create_user(
    conn: psycopg.Connection[DictRow],
    email: str,
    name: str | None,
    department: str | None,
    unit: str | None,
) -> DictRow:
    """Insert the user or return the existing row, enriching non-null fields.

    On an email conflict only non-null incoming values overwrite the stored
    ones, so a later call with less information never blanks a field.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO users (email, name, department, unit)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (email) DO UPDATE SET
                name       = COALESCE(EXCLUDED.name,       users.name),
                department = COALESCE(EXCLUDED.department, users.department),
                unit       = COALESCE(EXCLUDED.unit,       users.unit),
                updated_at = CURRENT_TIMESTAMP
            RETURNING id, email, name, department, unit, created_at, updated_at
            """,
            (email, name, department, unit),
        )
        row = cur.fetchone()
    if row is None:  # RETURNING always yields one row; guard for the type checker
        raise RuntimeError("user upsert returned no row")
    return row
