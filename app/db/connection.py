"""Connection handling for PostgreSQL."""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager

import psycopg
from psycopg.rows import DictRow, dict_row

logger = logging.getLogger(__name__)

CONNECT_TIMEOUT_SECONDS = 5


@contextmanager
def connect(database_url: str) -> Iterator[psycopg.Connection[DictRow]]:
    """Open a connection for one unit of work.

    Rows come back as dicts. Leaving the block commits; an exception rolls
    back and closes. Callers should keep the block short — one request's work.
    """
    with psycopg.connect(database_url, row_factory=dict_row, connect_timeout=CONNECT_TIMEOUT_SECONDS) as conn:
        yield conn


def check_database(database_url: str | None) -> bool:
    """True if a trivial query succeeds. Never raises — used by the health check."""
    if not database_url:
        return False
    try:
        with connect(database_url) as conn, conn.cursor() as cur:
            cur.execute("SELECT 1")
            cur.fetchone()
        return True
    except psycopg.Error as exc:
        logger.warning("database health probe failed: %s", exc)
        return False
