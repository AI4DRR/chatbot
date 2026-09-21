"""GET/POST/PATCH /api/sessions*."""

from __future__ import annotations

import logging

import psycopg
from fastapi import APIRouter, Depends, Query
from psycopg.rows import DictRow

from app.api.deps import CurrentUser, get_db, require_owned_session
from app.errors import ForbiddenError
from app.schemas.sessions import (
    ResumeSessionResponse,
    SessionHistory,
    SessionInfo,
    SessionTitleUpdate,
    SessionTitleUpdateResponse,
)
from app.services import sessions as session_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/sessions", tags=["Sessions"])


@router.get("", response_model=list[SessionInfo])
def list_sessions(
    user: CurrentUser,
    user_id: str | None = Query(default=None, description="Legacy; must be the signed-in user's id if given"),
    conn: psycopg.Connection[DictRow] = Depends(get_db),
) -> list[SessionInfo]:
    """The signed-in user's sessions, newest created first."""
    if user_id is not None and user_id != user.id:
        raise ForbiddenError("Sessions of another user are not accessible")
    return session_service.list_user_sessions(conn, user.id)


@router.get("/{session_id}", response_model=SessionHistory)
def get_session(
    session_id: str, user: CurrentUser, conn: psycopg.Connection[DictRow] = Depends(get_db)
) -> SessionHistory:
    """Full history of one of the signed-in user's sessions."""
    require_owned_session(conn, session_id, user)
    return session_service.get_session_history(conn, session_id)


@router.post("/{session_id}/resume", response_model=ResumeSessionResponse)
def resume_session(
    session_id: str, user: CurrentUser, conn: psycopg.Connection[DictRow] = Depends(get_db)
) -> ResumeSessionResponse:
    """Confirm one of the signed-in user's sessions exists before the client continues it."""
    require_owned_session(conn, session_id, user)
    info = session_service.resume_session(conn, session_id)
    return ResumeSessionResponse(session_id=info.id, session_info=info)


@router.patch("/{session_id}/title", response_model=SessionTitleUpdateResponse)
def update_session_title(
    session_id: str,
    body: SessionTitleUpdate,
    user: CurrentUser,
    conn: psycopg.Connection[DictRow] = Depends(get_db),
) -> SessionTitleUpdateResponse:
    """Set the display title (trimmed, at most 200 characters) of one of the signed-in user's sessions."""
    require_owned_session(conn, session_id, user)
    stored = session_service.update_session_title(conn, session_id, body.title)
    logger.info("session %s retitled", session_id)
    return SessionTitleUpdateResponse(success=True, session_id=session_id, title=stored)
