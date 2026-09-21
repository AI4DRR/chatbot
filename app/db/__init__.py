"""PostgreSQL access (psycopg 3, synchronous).

- ``connection``  connection factory / context manager and the health probe   (implemented)
- ``users``       get_or_create_user                                          (implemented)
- ``sessions``    create/get/list sessions, update title                       (implemented);
                  memory and context updates arrive with /api/chat
- ``messages``    get_session_messages, get_last_message, save_message (implemented);
                  save_context_chunk pending
- ``analytics``   get_usage_* aggregations for the admin dashboard

Rules: one connection per request (``with connect(url) as conn:``), commit on
success, rollback on error (psycopg's context manager does both). No SQL
outside this package; callers receive plain dicts / dataclasses, never
cursors. Schema lives in ``postgres_init/001_init.sql``.
"""
