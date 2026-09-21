"""FastAPI dependencies shared by routes."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager
from typing import Annotated

import psycopg
from fastapi import Depends, Request
from psycopg.rows import DictRow

from app.chat.pipeline import ChatPipeline
from app.config import Settings
from app.db import sessions as sessions_db
from app.db.connection import connect
from app.errors import AuthenticationError, DatabaseUnavailableError, ForbiddenError, NotFoundError
from app.integrations.entra import EntraClient
from app.integrations.mail import Mailer
from app.schemas.auth import AuthUser
from app.services import auth as auth_service
from app.services.sessions import parse_session_id


def get_app_settings(request: Request) -> Settings:
    """Settings attached to the application at creation time."""
    settings: Settings = request.app.state.settings
    return settings


def get_db(settings: Annotated[Settings, Depends(get_app_settings)]) -> Iterator[psycopg.Connection[DictRow]]:
    """One connection for the request: commit when the handler returns, rollback if it raises."""
    if not settings.database_url:
        raise DatabaseUnavailableError()
    with connect(settings.database_url) as conn:
        yield conn


DbConnector = Callable[[], AbstractContextManager[psycopg.Connection[DictRow]]]


def get_db_connector(settings: Annotated[Settings, Depends(get_app_settings)]) -> DbConnector:
    """A factory of short connections for handlers that must not hold one across external calls.

    Each ``with connector() as conn:`` block is its own transaction (commit on
    exit, rollback on error). ``/api/chat`` uses two: read before the
    pipeline, write after it.
    """
    if not settings.database_url:
        raise DatabaseUnavailableError()
    url = settings.database_url
    return lambda: connect(url)


def get_chat_pipeline(request: Request) -> ChatPipeline:
    """The answer pipeline chosen at app creation (``create_app(chat_pipeline=...)`` or ``CHAT_PIPELINE``)."""
    pipeline: ChatPipeline = request.app.state.chat_pipeline
    return pipeline


# --- authentication ---------------------------------------------------------------


def get_mailer(request: Request) -> Mailer:
    """The transactional-mail transport built at app creation (``EMAIL_TRANSPORT``)."""
    mailer: Mailer = request.app.state.mailer
    return mailer


def get_entra_client(request: Request) -> EntraClient | None:
    """The Entra client built at app creation (``None`` when not configured)."""
    client: EntraClient | None = request.app.state.entra_client
    return client


def get_session_token(
    request: Request, settings: Annotated[Settings, Depends(get_app_settings)]
) -> str | None:
    """The raw session cookie value, if any."""
    return request.cookies.get(settings.session_cookie_name)


def get_optional_user(
    token: Annotated[str | None, Depends(get_session_token)],
    settings: Annotated[Settings, Depends(get_app_settings)],
) -> AuthUser | None:
    """The signed-in user, or ``None``. One short connection; the request's own db work opens its own."""
    if not token or not settings.database_url:
        return None
    with connect(settings.database_url) as conn:
        return auth_service.resolve_session(conn, token)


def get_current_user(user: Annotated[AuthUser | None, Depends(get_optional_user)]) -> AuthUser:
    """The signed-in user; 401 otherwise. Every user-scoped route depends on this."""
    if user is None:
        raise AuthenticationError()
    return user


CurrentUser = Annotated[AuthUser, Depends(get_current_user)]


def get_current_admin(user: CurrentUser) -> AuthUser:
    """The signed-in user if ``users.is_admin``; 401 without a session, 403 for everyone else.

    Same session, same user row — an administrator is an ordinary user with the
    flag set (``postgres_init/003_admin.sql``). Every ``/api/admin/*`` route and
    the management page depend on this.
    """
    if not user.is_admin:
        raise ForbiddenError("Administrator access required")
    return user


CurrentAdmin = Annotated[AuthUser, Depends(get_current_admin)]


def require_owned_session(conn: psycopg.Connection[DictRow], session_id: str, user: AuthUser) -> DictRow:
    """The session row if it belongs to ``user``; otherwise 404 (existence is not revealed)."""
    row = sessions_db.get_session(conn, parse_session_id(session_id))
    if row is None or str(row["user_id"]) != user.id:
        raise NotFoundError(f"Session {session_id} not found")
    return row
