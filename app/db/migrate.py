"""Apply the idempotent auth migration at startup.

``postgres_init/`` only runs when a database is first created, so an existing
database would never see ``002_auth.sql`` / ``003_admin.sql``. The same files
are executed here on every start under an advisory lock (several uvicorn
workers start at once); every statement is ``IF NOT EXISTS``, so repeated runs
are no-ops.
"""

from __future__ import annotations

import logging
from pathlib import Path

import psycopg

from app.db.connection import connect

logger = logging.getLogger(__name__)

_SQL_DIR = Path(__file__).resolve().parents[2] / "postgres_init"
MIGRATIONS = [
    _SQL_DIR / "002_auth.sql",
    _SQL_DIR / "003_admin.sql",
    _SQL_DIR / "004_invitations.sql",
    _SQL_DIR / "005_admin_bootstrap.sql",
    _SQL_DIR / "006_password_resets.sql",
    _SQL_DIR / "007_user_status.sql",
]
LOCK_KEY = 0x41493444  # arbitrary constant: "AI4D"


def apply_migrations(database_url: str) -> None:
    """Run every migration file in order; raises on failure so startup fails loudly."""
    missing = [p.name for p in MIGRATIONS if not p.is_file()]
    if missing:  # a packaging error, not a database problem: refuse to start on an incomplete schema
        raise RuntimeError(f"migration file(s) missing from {_SQL_DIR}: {', '.join(missing)}")
    with connect(database_url) as conn, conn.cursor() as cur:
        cur.execute("SELECT pg_advisory_lock(%s)", (LOCK_KEY,))
        try:
            for path in MIGRATIONS:
                cur.execute(path.read_text())
                logger.info("migration applied: %s", path.name)
        finally:
            cur.execute("SELECT pg_advisory_unlock(%s)", (LOCK_KEY,))


def try_apply_migrations(database_url: str | None) -> bool:
    """Startup hook. No database configured → nothing to do.

    A failure is logged at ERROR and the app still starts: ``/health`` is the
    place that reports an unreachable database, and the next start retries.
    Returns whether the migration was applied.
    """
    if not database_url:
        return False
    try:
        apply_migrations(database_url)
    except psycopg.Error as exc:
        logger.error("auth migration not applied (database unreachable or refused): %s", exc)
        return False
    return True
