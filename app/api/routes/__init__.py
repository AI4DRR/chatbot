"""Routers, one module per resource.

Planned modules and their legacy origin (old ``src/api.py`` sections):

- ``health``    GET /health                                  (implemented)
- ``auth``      POST /api/users/identify, POST /api/login    (implemented)
- ``sessions``  GET /api/sessions, GET|POST|PATCH /api/sessions/{id}[/resume|/title]  (implemented)
- ``chat``      POST /api/chat  (lifecycle implemented; answers need the pipeline port)
- ``admin``     POST /api/admin/login, GET /api/admin/analytics/*
"""
