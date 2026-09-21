"""Schemas for POST /api/chat (legacy shapes preserved).

``SourceItem`` is also reused by session history, which replays the sources
stored in ``chat_messages.metadata``.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class SourceItem(BaseModel):
    """One cited source, as stored in message metadata and returned to clients."""

    title: str = Field(description="Source title, or the URL when no title is known")
    url: str
    type: str = Field(description="'KB' for knowledge base, 'WEB' for online retrieval")


class HistoryMessage(BaseModel):
    """One earlier turn of the session, as stored (internal shape, not a response model)."""

    role: str = Field(description="'user' or 'assistant'")
    content: str


class TokenUsage(BaseModel):
    """Token counts of one completion call."""

    prompt_tokens: int
    completion_tokens: int
    total_tokens: int


class ChatRequest(BaseModel):
    """Request body for POST /api/chat."""

    message: str = Field(min_length=1)
    session_id: str


class ChatResponse(BaseModel):
    """Response of POST /api/chat."""

    response: str = Field(description="Assistant text (markdown)")
    session_id: str
    message_id: str = Field(description="UUID of the stored assistant message")
    token_usage: TokenUsage
    cost_usd: float | None = Field(default=None, description="None when pricing is not configured")
    mode: str = Field(description="SYNTHESIS, LIST or REFUSAL from the real pipeline")
    timestamp: datetime
    sources: list[SourceItem] = Field(default_factory=list)
