"""POST /api/users/identify and POST /api/login: bound to the signed-in user (fake db layer)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from app.db import sessions as sessions_db
from tests.conftest import TEST_USER, TEST_USER_ID

NOW = datetime(2026, 9, 19, 12, 0, tzinfo=UTC)


def test_identify_returns_the_signed_in_user_and_ignores_the_body(db_client: TestClient) -> None:
    resp = db_client.post("/api/users/identify", json={"email": "someone-else@example.org", "name": "X"})
    assert resp.status_code == 200
    assert resp.json() == {
        "user": {
            "id": TEST_USER.id,
            "email": "tester@example.org",
            "name": "Test User",
            "department": "UNDRR",
            "unit": None,
        }
    }
    assert db_client.post("/api/users/identify").status_code == 200  # body optional


def test_identify_requires_a_session(anon_client: TestClient) -> None:
    resp = anon_client.post("/api/users/identify", json={"email": "a@example.org"})
    assert resp.status_code == 401
    assert resp.json() == {"detail": "Not authenticated", "status_code": 401}


def test_login_creates_session_for_the_signed_in_user(
    db_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    created: list[tuple[UUID, str | None]] = []
    session_id = uuid4()

    def fake_create(conn: Any, user_id: UUID, title: str | None) -> dict[str, Any]:
        created.append((user_id, title))
        return {"id": session_id, "user_id": user_id, "title": title, "created_at": NOW, "updated_at": NOW}

    monkeypatch.setattr(sessions_db, "create_session", fake_create)
    resp = db_client.post("/api/login", json={"email": "other@example.org", "department": "X"})
    assert resp.status_code == 200
    body = resp.json()
    assert set(body) == {"user", "session_id"}
    assert body["session_id"] == str(session_id)
    assert body["user"]["email"] == "tester@example.org" and body["user"]["department"] == "UNDRR"
    assert created == [(TEST_USER_ID, "New Chat Sessions")]


def test_login_requires_a_session(anon_client: TestClient) -> None:
    assert anon_client.post("/api/login", json={"email": "a@example.org"}).status_code == 401


def test_db_endpoints_report_503_without_database(client: TestClient) -> None:
    """``client`` is signed in but has no DATABASE_URL, so get_db refuses."""
    resp = client.post("/api/login", json={"email": "a@example.org"})
    assert resp.status_code == 503
    assert resp.json() == {"detail": "Database not configured", "status_code": 503}
