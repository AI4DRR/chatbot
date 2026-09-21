"""Pydantic models that define the public API contract.

These are the models from the legacy ``src/models.py`` split by resource:

- ``health``    HealthResponse                                   (implemented)
- ``auth``      UserLogin, UserResponse (+ Identify/Login envelopes)  (implemented)
- ``sessions``  SessionInfo, MessageRecord, SessionHistory, SessionTitleUpdate (+ envelopes)  (implemented)
- ``chat``      ChatRequest, ChatResponse, TokenUsage, SourceItem          (implemented)
- ``admin``     AdminLogin (+ analytics envelopes)

Field names and shapes must stay byte-compatible with the old API while the
frontends are migrated (see docs/standalone-frontend-assessment.md §4).
"""
