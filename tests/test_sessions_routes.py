"""/api/sessions endpoints against a fake db layer."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from app.db import messages as messages_db
from app.db import sessions as sessions_db
from tests.conftest import TEST_USER_ID

USER_ID = TEST_USER_ID
SESSION_ID = uuid4()
T0 = datetime(2026, 9, 19, 12, 0, tzinfo=UTC)
T1 = datetime(2026, 9, 19, 12, 5, tzinfo=UTC)


def _session_row(**overrides: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "id": SESSION_ID,
        "user_id": USER_ID,
        "title": "New Chat Sessions",
        "created_at": T0,
        "updated_at": T1,
        "memory_summary": None,
        "memory_facts": [],
        "memory_turns": 0,
    }
    row.update(overrides)
    return row


@pytest.fixture
def one_session(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    row = _session_row()

    def fake_get(conn: Any, session_id: UUID) -> dict[str, Any] | None:
        return row if session_id == SESSION_ID else None

    monkeypatch.setattr(sessions_db, "get_session", fake_get)
    return row


# --- GET /api/sessions ----------------------------------------------------------


def test_list_sessions_shape_and_order(db_client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    rows: list[dict[str, Any]] = [
        {"id": SESSION_ID, "title": "Second", "created_at": T1, "updated_at": T1, "memory_turns": 2},
        {"id": uuid4(), "title": None, "created_at": T0, "updated_at": T0, "memory_turns": 0},
    ]
    seen: list[UUID] = []

    def fake_list(conn: Any, user_id: UUID) -> list[dict[str, Any]]:
        seen.append(user_id)
        return rows

    monkeypatch.setattr(sessions_db, "list_sessions", fake_list)
    resp = db_client.get("/api/sessions", params={"user_id": str(USER_ID)})
    assert resp.status_code == 200
    body = resp.json()
    assert [s["title"] for s in body] == ["Second", None]
    assert set(body[0]) == {"id", "title", "created_at", "memory_turns", "last_message_at"}
    assert body[0]["last_message_at"] == "2026-09-19T12:05:00Z"
    assert seen == [USER_ID]


def test_list_sessions_uses_the_signed_in_user(
    db_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``user_id`` is optional now; when omitted the signed-in user's sessions are listed."""
    seen: list[UUID] = []

    def fake_list(conn: Any, uid: UUID) -> list[dict[str, Any]]:
        seen.append(uid)
        return []

    monkeypatch.setattr(sessions_db, "list_sessions", fake_list)
    assert db_client.get("/api/sessions").status_code == 200
    assert seen == [USER_ID]


def test_list_sessions_rejects_another_users_id(
    db_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sessions_db, "list_sessions", lambda conn, uid: [])
    resp = db_client.get("/api/sessions", params={"user_id": str(uuid4())})
    assert resp.status_code == 403
    assert resp.json()["detail"] == "Sessions of another user are not accessible"
    assert db_client.get("/api/sessions", params={"user_id": "nope"}).status_code == 403


# --- GET /api/sessions/{id} -----------------------------------------------------


def test_get_session_history(
    db_client: TestClient, one_session: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    one_session["memory_summary"] = "about floods"
    one_session["memory_facts"] = ["fact 1"]
    messages = [
        {
            "id": uuid4(),
            "role": "user",
            "content": "hi",
            "metadata": None,
            "token_input": None,
            "token_output": None,
            "cost_estimate": None,
            "created_at": T0,
        },
        {
            "id": uuid4(),
            "role": "assistant",
            "content": "hello",
            "metadata": {"mode": "SYNTHESIS", "sources": [{"title": "t", "url": "https://x", "type": "KB"}]},
            "token_input": 10,
            "token_output": 5,
            "cost_estimate": Decimal("0.001234"),
            "created_at": T1,
        },
    ]
    monkeypatch.setattr(messages_db, "get_session_messages", lambda conn, sid: messages)
    resp = db_client.get(f"/api/sessions/{SESSION_ID}")
    assert resp.status_code == 200
    body = resp.json()
    assert set(body) == {"session_id", "title", "created_at", "messages", "memory_summary", "memory_facts"}
    assert body["session_id"] == str(SESSION_ID)
    assert body["memory_summary"] == "about floods"
    assert body["memory_facts"] == ["fact 1"]
    assert [m["role"] for m in body["messages"]] == ["user", "assistant"]
    user_msg, assistant_msg = body["messages"]
    assert user_msg["sources"] == [] and user_msg["cost_estimate"] is None
    assert assistant_msg["sources"] == [{"title": "t", "url": "https://x", "type": "KB"}]
    assert assistant_msg["cost_estimate"] == pytest.approx(0.001234)
    assert assistant_msg["token_input"] == 10


def test_get_session_unknown_is_404(db_client: TestClient, one_session: dict[str, Any]) -> None:
    other = uuid4()
    resp = db_client.get(f"/api/sessions/{other}")
    assert resp.status_code == 404
    assert resp.json() == {"detail": f"Session {other} not found", "status_code": 404}


def test_get_session_malformed_id_is_404(db_client: TestClient, one_session: dict[str, Any]) -> None:
    resp = db_client.get("/api/sessions/not-a-uuid")
    assert resp.status_code == 404
    assert resp.json()["detail"] == "Session not-a-uuid not found"


# --- POST /api/sessions/{id}/resume ---------------------------------------------


def test_resume_session(db_client: TestClient, one_session: dict[str, Any]) -> None:
    resp = db_client.post(f"/api/sessions/{SESSION_ID}/resume")
    assert resp.status_code == 200
    body = resp.json()
    assert set(body) == {"session_id", "session_info"}
    assert body["session_id"] == str(SESSION_ID)
    assert body["session_info"] == {
        "id": str(SESSION_ID),
        "title": "New Chat Sessions",
        "created_at": "2026-09-19T12:00:00Z",
        "memory_turns": 0,
        "last_message_at": "2026-09-19T12:05:00Z",
    }


def test_resume_unknown_is_404(db_client: TestClient, one_session: dict[str, Any]) -> None:
    assert db_client.post(f"/api/sessions/{uuid4()}/resume").status_code == 404


# --- PATCH /api/sessions/{id}/title ---------------------------------------------


def test_update_title_trims_and_truncates(
    db_client: TestClient, one_session: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    stored: list[tuple[UUID, str]] = []

    def fake_update(conn: Any, session_id: UUID, title: str) -> bool:
        stored.append((session_id, title))
        return session_id == SESSION_ID

    monkeypatch.setattr(sessions_db, "update_session_title", fake_update)
    resp = db_client.patch(f"/api/sessions/{SESSION_ID}/title", json={"title": "  " + "x" * 250 + "  "})
    assert resp.status_code == 200
    assert resp.json() == {"success": True, "session_id": str(SESSION_ID), "title": "x" * 200}
    assert stored == [(SESSION_ID, "x" * 200)]


def test_update_title_unknown_is_404(
    db_client: TestClient, one_session: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sessions_db, "update_session_title", lambda conn, sid, title: False)
    resp = db_client.patch(f"/api/sessions/{uuid4()}/title", json={"title": "t"})
    assert resp.status_code == 404


def test_update_title_requires_body(db_client: TestClient) -> None:
    assert db_client.patch(f"/api/sessions/{SESSION_ID}/title", json={}).status_code == 422
