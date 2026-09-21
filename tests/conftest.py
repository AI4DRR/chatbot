"""Shared fixtures. Unit tests never touch a real database or Azure service.

Database-backed tests are marked ``db`` and run only when ``TEST_DATABASE_URL``
is set (see ``tests/test_db_sessions.py``).
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from uuid import UUID

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.deps import get_db, get_db_connector, get_optional_user
from app.chat.pipeline import ChatTurnInput, ChatTurnResult
from app.config import Settings
from app.integrations.mail import Mail
from app.main import create_app
from app.schemas.auth import AuthUser
from app.schemas.chat import SourceItem, TokenUsage


class MemoryMailer:
    """Test transport: records messages; ``fail`` makes every send raise like a broken SMTP server."""

    def __init__(self, fail: bool = False) -> None:
        self.sent: list[Mail] = []
        self.fail = fail

    def send(self, mail: Mail) -> None:
        if self.fail:
            from app.errors import IntegrationError

            raise IntegrationError("mail", "The invitation e-mail could not be sent")
        self.sent.append(mail)

    def last_link(self) -> str:
        """The setup link of the newest message (tests read the token from it)."""
        import re

        m = re.search(r"https?://\S+/(?:account-setup|reset-password)#token=(\S+)", self.sent[-1].text)
        assert m, "no setup/reset link in the mail"
        return m.group(0)


# The signed-in user every authenticated test client impersonates (see ``sign_in``).
TEST_USER_ID = UUID("11111111-1111-4111-8111-111111111111")
TEST_USER = AuthUser(
    id=str(TEST_USER_ID),
    email="tester@example.org",
    name="Test User",
    first_name="Test",
    last_name="User",
    department="UNDRR",
    has_password=True,
)


def sign_in(app: FastAPI, user: AuthUser | None = TEST_USER) -> None:
    """Make every request to ``app`` look signed in as ``user`` (``None`` = anonymous)."""
    app.dependency_overrides[get_optional_user] = lambda: user


class FakeConn:
    """Stands in for a psycopg connection; the db functions are monkeypatched around it.

    ``events`` records every ``open`` / ``close`` of the fake connector (and
    anything a test appends), so transaction ordering can be asserted.
    """

    def __init__(self) -> None:
        self.events: list[str] = []
        self.open_count = 0


class RecordingPipeline:
    """A ``ChatPipeline`` that records its inputs and returns a preset result (or raises)."""

    def __init__(self) -> None:
        self.calls: list[ChatTurnInput] = []
        self.error: Exception | None = None
        self.result = ChatTurnResult(
            response_text="canned answer",
            token_usage=TokenUsage(prompt_tokens=12, completion_tokens=7, total_tokens=19),
            cost_usd=0.00042,
            mode="SYNTHESIS",
            topic="floods asia",
            sources=[SourceItem(title="PW", url="https://p", type="KB")],
        )

    def __call__(self, turn: ChatTurnInput) -> ChatTurnResult:
        self.calls.append(turn)
        if self.error is not None:
            raise self.error
        return self.result


@pytest.fixture
def settings() -> Settings:
    """Settings with no database and no external services configured."""
    return Settings(database_url=None, log_level="WARNING", frontend_dir="does-not-exist")


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    """Signed-in client with no database and no external services."""
    app = create_app(settings)
    sign_in(app)
    with TestClient(app) as c:
        yield c


@pytest.fixture
def anon_client(settings: Settings) -> Iterator[TestClient]:
    """The same app with nobody signed in."""
    with TestClient(create_app(settings), raise_server_exceptions=False) as c:
        yield c


@pytest.fixture
def fake_conn() -> FakeConn:
    return FakeConn()


@pytest.fixture
def pipeline() -> RecordingPipeline:
    return RecordingPipeline()


@pytest.fixture
def db_client(settings: Settings, fake_conn: FakeConn, pipeline: RecordingPipeline) -> Iterator[TestClient]:
    """Client whose ``get_db`` yields ``fake_conn`` and whose chat pipeline is ``pipeline``.

    Server exceptions are returned as responses (not re-raised) so the error
    boundary can be asserted on.
    """
    app = create_app(settings, chat_pipeline=pipeline)

    def _fake_get_db() -> Iterator[FakeConn]:
        yield fake_conn

    @contextmanager
    def _fake_connection() -> Iterator[FakeConn]:
        fake_conn.open_count += 1
        fake_conn.events.append("open")
        try:
            yield fake_conn
        finally:
            fake_conn.open_count -= 1
            fake_conn.events.append("close")

    app.dependency_overrides[get_db] = _fake_get_db
    app.dependency_overrides[get_db_connector] = lambda: _fake_connection
    sign_in(app)
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


@pytest.fixture
def pg_user() -> Iterator[AuthUser]:
    """A real, disposable user row at ``TEST_DATABASE_URL`` (deleted afterwards; sessions cascade)."""
    url = os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL not set")
    from uuid import uuid4

    from app.db.connection import connect

    email = f"aicollab-smoke-{uuid4().hex[:12]}@example.org"
    with connect(url) as conn, conn.cursor() as cur:
        cur.execute("INSERT INTO users (email, name) VALUES (%s, %s) RETURNING id", (email, "Smoke"))
        row = cur.fetchone()
        assert row is not None
        user_id = str(row["id"])
    yield AuthUser(id=user_id, email=email, name="Smoke")
    with connect(url) as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM users WHERE email = %s", (email,))


@pytest.fixture
def pg_client(pg_user: AuthUser) -> Iterator[TestClient]:
    """Client wired to the PostgreSQL at ``TEST_DATABASE_URL``, signed in as ``pg_user`` (data-path tests).

    Real cookie sessions are exercised in ``tests/test_db_auth.py``.
    """
    url = os.environ["TEST_DATABASE_URL"]
    app = create_app(Settings(database_url=url, log_level="WARNING", frontend_dir="none"))
    sign_in(app, pg_user)
    with TestClient(app) as c:
        yield c
