"""Application services: the logic between routes and storage.

- ``sessions``  login / identify, session listing, history assembly, title,
                resume, session context + user/assistant message persistence (implemented)
                (legacy session_manager.py, unchanged responsibilities)
- ``memory``    conversation memory summary/facts (legacy memory_manager.py);
                note the legacy API path never wrote memory — see
                docs/standalone-frontend-assessment.md §4.3

Services take a database connection (or repository functions) and settings
as parameters; they do not read the environment or build HTTP responses.
"""
