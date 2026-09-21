"""Schemas for the /api/sessions endpoints (legacy shapes preserved)."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from app.schemas.chat import SourceItem


class SessionInfo(BaseModel):
    """Summary of one chat session (sidebar entry)."""

    id: str = Field(description="Session UUID")
    title: str | None = None
    created_at: datetime
    memory_turns: int = Field(description="Turns recorded in conversation memory")
    last_message_at: datetime | None = Field(default=None, description="Session ``updated_at``")


class MessageRecord(BaseModel):
    """One message in a session's history."""

    id: str = Field(description="Message UUID")
    role: str = Field(description="'user' or 'assistant'")
    content: str
    created_at: datetime
    token_input: int | None = None
    token_output: int | None = None
    cost_estimate: float | None = None
    sources: list[SourceItem] = Field(default_factory=list)


class SessionHistory(BaseModel):
    """Full conversation history for GET /api/sessions/{id}."""

    session_id: str
    title: str | None = None
    created_at: datetime
    messages: list[MessageRecord] = Field(description="Ordered by created_at ASC")
    memory_summary: str | None = None
    memory_facts: list[str] = Field(default_factory=list)


class SessionTitleUpdate(BaseModel):
    """Request body for PATCH /api/sessions/{id}/title."""

    title: str


class SessionTitleUpdateResponse(BaseModel):
    """``{"success": true, "session_id": ..., "title": ...}``."""

    success: bool
    session_id: str
    title: str


class ResumeSessionResponse(BaseModel):
    """``{"session_id": ..., "session_info": {...}}``."""

    session_id: str
    session_info: SessionInfo
