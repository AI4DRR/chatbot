"""HTTP layer.

``app.api.routes.*`` hold one ``APIRouter`` per resource, mirroring the
sections of the legacy ``api.py``. Routes are thin: validate with a schema,
call a service, map the result to a response schema. They never build SQL or
call Azure directly. ``app.api.deps`` provides FastAPI dependencies (settings,
database connection, admin token check when ported).
"""
