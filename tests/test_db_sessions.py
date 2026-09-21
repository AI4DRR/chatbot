"""End-to-end against a real PostgreSQL: FastAPI → service → db → schema.

Runs only with ``TEST_DATABASE_URL`` set (see conftest). Every record it creates
belongs to one disposable user with a unique email, deleted at the end
(``chat_sessions`` / ``chat_messages`` cascade from ``users``).
"""

from __future__ import annotations

import os
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from psycopg.types.json import Json

from app.chat.pipeline import ChatTurnInput, ChatTurnResult
from app.chat.stub import STUB_MODE, STUB_PREFIX, stub_pipeline
from app.config import Settings
from app.db.connection import connect
from app.main import create_app
from app.schemas.auth import AuthUser
from tests.conftest import sign_in

pytestmark = pytest.mark.db


def test_identify_login_list_resume_title_history(pg_client: TestClient, pg_user: AuthUser) -> None:
    # identify: returns the signed-in user; the body is ignored (identity is server-side now)
    r = pg_client.post("/api/users/identify", json={"email": "someone-else@example.org", "name": "X"})
    assert r.status_code == 200, r.text
    user = r.json()["user"]
    assert user["id"] == pg_user.id and user["email"] == pg_user.email and user["name"] == "Smoke"
    assert pg_client.get("/api/sessions").json() == []

    # login: new session for the signed-in user with the legacy default title
    r = pg_client.post("/api/login", json={"email": "someone-else@example.org"})
    assert r.status_code == 200, r.text
    assert r.json()["user"]["id"] == user["id"]
    session_id = r.json()["session_id"]

    # list: one session, newest first, last_message_at populated
    sessions = pg_client.get("/api/sessions", params={"user_id": user["id"]}).json()
    assert [s["id"] for s in sessions] == [session_id]
    assert sessions[0]["title"] == "New Chat Sessions"
    assert sessions[0]["memory_turns"] == 0
    assert sessions[0]["last_message_at"] is not None

    # resume
    r = pg_client.post(f"/api/sessions/{session_id}/resume")
    assert r.status_code == 200 and r.json()["session_info"]["id"] == session_id

    # title: trimmed and stored; the trigger bumps updated_at
    r = pg_client.patch(f"/api/sessions/{session_id}/title", json={"title": "  Floods in Asia  "})
    assert r.json() == {"success": True, "session_id": session_id, "title": "Floods in Asia"}
    assert pg_client.get(f"/api/sessions/{session_id}").json()["title"] == "Floods in Asia"

    # history: empty, then with rows written the way /api/chat will write them
    history = pg_client.get(f"/api/sessions/{session_id}").json()
    assert history["messages"] == [] and history["memory_facts"] == [] and history["memory_summary"] is None
    with connect(os.environ["TEST_DATABASE_URL"]) as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO chat_messages (session_id, role, content) VALUES (%s, 'user', 'What is DRR?')",
            (session_id,),
        )
        cur.execute(
            """
            INSERT INTO chat_messages
                (session_id, role, content, metadata, token_input, token_output, cost_estimate)
            VALUES (%s, 'assistant', 'Disaster risk reduction is...', %s, 120, 40, 0.00042)
            """,
            (
                session_id,
                Json({"mode": "SYNTHESIS", "sources": [{"title": "PW", "url": "https://p", "type": "KB"}]}),
            ),
        )
    history = pg_client.get(f"/api/sessions/{session_id}").json()
    assert [m["role"] for m in history["messages"]] == ["user", "assistant"]
    assert history["messages"][1]["sources"] == [{"title": "PW", "url": "https://p", "type": "KB"}]
    assert history["messages"][1]["cost_estimate"] == pytest.approx(0.00042)
    assert history["messages"][1]["token_input"] == 120

    # a second login orders newest-created first
    second = pg_client.post("/api/login").json()["session_id"]
    assert [s["id"] for s in pg_client.get("/api/sessions").json()] == [second, session_id]


def test_not_found_paths_against_real_db(pg_client: TestClient) -> None:
    missing = uuid4()
    assert pg_client.get(f"/api/sessions/{missing}").status_code == 404
    assert pg_client.post(f"/api/sessions/{missing}/resume").status_code == 404
    assert pg_client.patch(f"/api/sessions/{missing}/title", json={"title": "x"}).status_code == 404
    assert pg_client.get("/api/sessions", params={"user_id": str(uuid4())}).status_code == 403  # not yours
    assert pg_client.get("/health").json()["database"] == "ok"


def _pg_app_with(pipeline: object, user: AuthUser) -> TestClient:
    """A client with its own pipeline, signed in as ``user``."""
    url = os.environ["TEST_DATABASE_URL"]
    settings = Settings(database_url=url, log_level="WARNING", frontend_dir="none")
    app = create_app(settings, chat_pipeline=pipeline)  # type: ignore[arg-type]
    sign_in(app, user)
    return TestClient(app, raise_server_exceptions=False)


def test_chat_lifecycle_with_stub_pipeline(pg_client: TestClient, pg_user: AuthUser) -> None:
    """FastAPI → service → db → PostgreSQL for a turn, using the explicit non-AI stub."""
    session_id = pg_client.post("/api/login").json()["session_id"]
    with _pg_app_with(stub_pipeline, pg_user) as chat_client:
        r1 = chat_client.post("/api/chat", json={"message": "What is DRR?", "session_id": session_id})
        assert r1.status_code == 200, r1.text
        b1 = r1.json()
        assert b1["response"].startswith(STUB_PREFIX) and b1["mode"] == STUB_MODE and b1["cost_usd"] is None
        assert b1["sources"][0]["url"] == "stub://no-retrieval"

        # second turn: the topic stored on turn 1 is read back and reaches the pipeline
        r2 = chat_client.post("/api/chat", json={"message": "and floods?", "session_id": session_id})
        assert r2.status_code == 200, r2.text
        assert "(topic so far: What is DRR?)" in r2.json()["response"]

    history = pg_client.get(f"/api/sessions/{session_id}").json()
    roles = [m["role"] for m in history["messages"]]
    assert roles == ["user", "assistant", "user", "assistant"]
    assert [m["content"] for m in history["messages"]][::2] == ["What is DRR?", "and floods?"]
    first_answer = history["messages"][1]
    assert first_answer["id"] == b1["message_id"]
    assert first_answer["sources"] == [
        {"title": "Stub source (no retrieval performed)", "url": "stub://no-retrieval", "type": "KB"}
    ]
    assert first_answer["token_input"] == b1["token_usage"]["prompt_tokens"]
    assert first_answer["cost_estimate"] is None
    assert history["messages"][0]["sources"] == []  # user messages carry no metadata
    created = [m["created_at"] for m in history["messages"]]
    assert created == sorted(created) and len(set(created)) == 4  # distinct, ascending timestamps
    with connect(os.environ["TEST_DATABASE_URL"]) as conn, conn.cursor() as cur:
        cur.execute("SELECT metadata FROM chat_messages WHERE id = %s", (b1["message_id"],))
        row = cur.fetchone()
        assert (
            row is not None
            and row["metadata"]["topic"] == "What is DRR?"
            and row["metadata"]["mode"] == STUB_MODE
        )


def test_chat_pipeline_failure_persists_nothing(pg_client: TestClient, pg_user: AuthUser) -> None:
    session_id = pg_client.post("/api/login").json()["session_id"]
    before = _last_message_at(pg_client, session_id)

    def exploding(turn: ChatTurnInput) -> ChatTurnResult:
        raise RuntimeError("no AI here")

    with _pg_app_with(exploding, pg_user) as chat_client:
        r = chat_client.post("/api/chat", json={"message": "hi", "session_id": session_id})
    assert r.status_code == 500
    assert pg_client.get(f"/api/sessions/{session_id}").json()["messages"] == []
    # the session row was not touched either
    assert _last_message_at(pg_client, session_id) == before


def _last_message_at(pg_client: TestClient, session_id: str) -> str:
    sessions = pg_client.get("/api/sessions").json()
    return str(next(s["last_message_at"] for s in sessions if s["id"] == session_id))


def test_chat_turn_reads_history_and_bumps_updated_at(pg_client: TestClient, pg_user: AuthUser) -> None:
    """Turn 2 sees turn 1 as raw history; each stored turn moves the session's ``last_message_at``."""
    session_id = pg_client.post("/api/login").json()["session_id"]
    seen: list[list[tuple[str, str]]] = []

    def echoing(turn: ChatTurnInput) -> ChatTurnResult:
        seen.append([(m.role, m.content) for m in turn.history])
        return stub_pipeline(turn)

    t0 = _last_message_at(pg_client, session_id)
    with _pg_app_with(echoing, pg_user) as chat_client:
        assert (
            chat_client.post("/api/chat", json={"message": "first", "session_id": session_id}).status_code
            == 200
        )
        t1 = _last_message_at(pg_client, session_id)
        assert (
            chat_client.post("/api/chat", json={"message": "second", "session_id": session_id}).status_code
            == 200
        )
        t2 = _last_message_at(pg_client, session_id)
    assert seen[0] == []
    assert seen[1][0] == ("user", "first") and seen[1][1][0] == "assistant" and len(seen[1]) == 2
    assert t0 < t1 < t2


def test_chat_unconfigured_pipeline_is_503_against_real_db(pg_client: TestClient, pg_user: AuthUser) -> None:
    """``pg_client`` was built without CHAT_PIPELINE → the production default."""
    session_id = pg_client.post("/api/login").json()["session_id"]
    r = pg_client.post("/api/chat", json={"message": "hi", "session_id": session_id})
    assert r.status_code == 503
    assert pg_client.get(f"/api/sessions/{session_id}").json()["messages"] == []


def test_chat_turn_persists_subject_and_memory_with_the_turn(
    pg_client: TestClient, pg_user: AuthUser
) -> None:
    """Phase 2: subject / memory_summary / memory_turns are written with the turn, never on failure."""
    from dataclasses import replace

    from app.chat.memory import MemoryUpdate

    session_id = pg_client.post("/api/login").json()["session_id"]

    def with_updates(turn: ChatTurnInput) -> ChatTurnResult:
        base = stub_pipeline(turn)
        return replace(
            base,
            subject="Urban flood risk",
            memory=MemoryUpdate(summary="They discussed floods.", turns=2),
            details={"routing": {"query": "urban flood risk", "topic_changed": True, "fallback": False}},
        )

    with _pg_app_with(with_updates, pg_user) as chat_client:
        r = chat_client.post("/api/chat", json={"message": "floods?", "session_id": session_id})
        assert r.status_code == 200, r.text
    with connect(os.environ["TEST_DATABASE_URL"]) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT subject, memory_summary, memory_turns FROM chat_sessions WHERE id = %s", (session_id,)
        )
        row = cur.fetchone()
        assert row is not None
        assert (row["subject"], row["memory_summary"], row["memory_turns"]) == (
            "Urban flood risk",
            "They discussed floods.",
            2,
        )
        cur.execute("SELECT metadata FROM chat_messages WHERE id = %s", (r.json()["message_id"],))
        meta = cur.fetchone()
        assert meta is not None and meta["metadata"]["routing"]["query"] == "urban flood risk"

    # the next turn reads them back into the pipeline input
    seen: list[ChatTurnInput] = []

    def observe(turn: ChatTurnInput) -> ChatTurnResult:
        seen.append(turn)
        return stub_pipeline(turn)

    with _pg_app_with(observe, pg_user) as chat_client:
        assert (
            chat_client.post("/api/chat", json={"message": "more", "session_id": session_id}).status_code
            == 200
        )
    assert seen[0].subject == "Urban flood risk"
    assert seen[0].memory_summary == "They discussed floods." and seen[0].memory_turns == 2

    # a failing turn changes none of it
    def exploding(turn: ChatTurnInput) -> ChatTurnResult:
        raise RuntimeError("no AI here")

    with _pg_app_with(exploding, pg_user) as chat_client:
        assert (
            chat_client.post("/api/chat", json={"message": "x", "session_id": session_id}).status_code == 500
        )
    with connect(os.environ["TEST_DATABASE_URL"]) as conn, conn.cursor() as cur:
        cur.execute("SELECT subject, memory_turns FROM chat_sessions WHERE id = %s", (session_id,))
        row = cur.fetchone()
        assert row is not None and (row["subject"], row["memory_turns"]) == ("Urban flood risk", 2)
