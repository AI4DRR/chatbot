"""POST /api/chat lifecycle against a fake db layer and a recording pipeline.

The route opens two short connections through ``get_db_connector`` (read,
then write) with the pipeline in between; ``FakeConn.events`` records the
``open``/``close`` boundaries and the fakes below record the db calls.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient

from app.chat.pipeline import unconfigured_pipeline
from app.config import Settings
from app.db import messages as messages_db
from app.db import sessions as sessions_db
from app.main import create_app
from tests.conftest import TEST_USER_ID, FakeConn, RecordingPipeline

SESSION_ID = uuid4()
T0 = datetime(2026, 9, 19, 12, 0, tzinfo=UTC)


@pytest.fixture
def session_row(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    row: dict[str, Any] = {
        "id": SESSION_ID,
        "user_id": TEST_USER_ID,
        "title": "New Chat Sessions",
        "created_at": T0,
        "updated_at": T0,
        "memory_summary": None,
        "memory_facts": [],
        "memory_turns": 0,
    }
    row["subject"] = None
    monkeypatch.setattr(sessions_db, "get_session", lambda conn, sid: row if sid == SESSION_ID else None)
    monkeypatch.setattr(sessions_db, "touch_session", lambda conn, sid: sid == SESSION_ID)
    return row


@pytest.fixture(autouse=True)
def session_writes(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, Any]]:
    """Records ``update_subject`` / ``update_memory`` calls (Phase 2 persistence)."""
    calls: list[tuple[str, Any]] = []

    def fake_subject(conn: Any, sid: UUID, subject: str) -> bool:
        calls.append(("subject", subject))
        return True

    def fake_memory(conn: Any, sid: UUID, summary: str, turns: int) -> bool:
        calls.append(("memory", (summary, turns)))
        return True

    monkeypatch.setattr(sessions_db, "update_subject", fake_subject)
    monkeypatch.setattr(sessions_db, "update_memory", fake_memory)
    return calls


@pytest.fixture(autouse=True)
def recent(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """The session's messages (oldest first): empty unless a test appends rows.

    Backs ``count_messages``, ``get_recent_messages`` and ``get_messages_slice``.
    """
    rows: list[dict[str, Any]] = []
    monkeypatch.setattr(messages_db, "count_messages", lambda conn, sid: len(rows))
    monkeypatch.setattr(
        messages_db, "get_recent_messages", lambda conn, sid, limit: rows[-limit:] if limit else []
    )
    monkeypatch.setattr(
        messages_db, "get_messages_slice", lambda conn, sid, offset, limit: rows[offset : offset + limit]
    )
    return rows


@pytest.fixture
def last_message(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Mutable holder: ``store["row"]`` is what ``get_last_message`` returns."""
    store: dict[str, Any] = {"row": None}
    monkeypatch.setattr(messages_db, "get_last_message", lambda conn, sid: store["row"])
    return store


@pytest.fixture
def saved(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Every ``save_message`` call, in order, as keyword dicts."""
    calls: list[dict[str, Any]] = []

    def fake_save(
        conn: Any,
        session_id: UUID,
        role: str,
        content: str,
        metadata: dict[str, Any] | None,
        token_input: int | None = None,
        token_output: int | None = None,
        cost_estimate: float | None = None,
    ) -> dict[str, Any]:
        calls.append(
            {
                "session_id": session_id,
                "role": role,
                "content": content,
                "metadata": metadata,
                "token_input": token_input,
                "token_output": token_output,
                "cost_estimate": cost_estimate,
            }
        )
        return {"id": uuid4(), "created_at": T0}

    monkeypatch.setattr(messages_db, "save_message", fake_save)
    return calls


def _post(client: TestClient, message: str = "What causes floods?") -> Any:
    return client.post("/api/chat", json={"message": message, "session_id": str(SESSION_ID)})


def test_chat_contract_and_persistence(
    db_client: TestClient,
    pipeline: RecordingPipeline,
    session_row: dict[str, Any],
    last_message: dict[str, Any],
    saved: list[dict[str, Any]],
) -> None:
    resp = _post(db_client)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert set(body) == {
        "response",
        "session_id",
        "message_id",
        "token_usage",
        "cost_usd",
        "mode",
        "timestamp",
        "sources",
    }
    assert body["response"] == "canned answer"
    assert body["session_id"] == str(SESSION_ID)
    assert body["token_usage"] == {"prompt_tokens": 12, "completion_tokens": 7, "total_tokens": 19}
    assert body["cost_usd"] == pytest.approx(0.00042)
    assert body["mode"] == "SYNTHESIS"
    assert body["sources"] == [{"title": "PW", "url": "https://p", "type": "KB"}]
    assert body["timestamp"].endswith("Z")

    # persistence: user first (no metadata), then assistant with the legacy metadata layout
    assert [c["role"] for c in saved] == ["user", "assistant"]
    user, assistant = saved
    assert user["content"] == "What causes floods?" and user["metadata"] is None
    assert user["token_input"] is None and user["cost_estimate"] is None
    assert assistant["content"] == "canned answer"
    assert assistant["metadata"] == {
        "mode": "SYNTHESIS",
        "topic": "floods asia",
        "sources": [{"title": "PW", "url": "https://p", "type": "KB"}],
    }
    assert (assistant["token_input"], assistant["token_output"]) == (12, 7)
    assert assistant["cost_estimate"] == pytest.approx(0.00042)
    assert all(c["session_id"] == SESSION_ID for c in saved)

    # the pipeline saw a fresh session: no topic, empty memory, no history
    assert len(pipeline.calls) == 1
    turn = pipeline.calls[0]
    assert (turn.question, turn.current_topic, turn.memory_summary, turn.memory_facts, turn.history) == (
        "What causes floods?",
        None,
        "",
        [],
        [],
    )


def test_chat_stores_pipeline_stats_in_metadata(
    db_client: TestClient,
    pipeline: RecordingPipeline,
    session_row: dict[str, Any],
    last_message: dict[str, Any],
    saved: list[dict[str, Any]],
) -> None:
    from dataclasses import replace

    pipeline.result = replace(
        pipeline.result, stats={"retrieved": 15, "selected": 7, "history": 0, "cited": 1}
    )
    assert _post(db_client).status_code == 200
    assert saved[1]["metadata"]["stats"] == {"retrieved": 15, "selected": 7, "history": 0, "cited": 1}
    assert set(saved[1]["metadata"]) == {"mode", "topic", "sources", "stats"}


def test_chat_transaction_boundary_read_pipeline_write(
    db_client: TestClient,
    fake_conn: FakeConn,
    pipeline: RecordingPipeline,
    session_row: dict[str, Any],
    last_message: dict[str, Any],
    recent: list[dict[str, Any]],
    saved: list[dict[str, Any]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Read → close → pipeline (no connection open) → open → write → close."""

    def observed(turn: Any) -> Any:
        fake_conn.events.append(f"pipeline(open_count={fake_conn.open_count})")
        return pipeline(turn)

    db_client.app.state.chat_pipeline = observed  # type: ignore[attr-defined]

    touched: list[UUID] = []

    def record_touch(conn: Any, sid: UUID) -> bool:
        touched.append(sid)
        return True

    monkeypatch.setattr(sessions_db, "touch_session", record_touch)

    assert _post(db_client).status_code == 200
    assert fake_conn.events == ["open", "close", "pipeline(open_count=0)", "open", "close"]
    assert touched == [SESSION_ID]
    assert [c["role"] for c in saved] == ["user", "assistant"]


def test_chat_passes_recent_history_to_pipeline(
    db_client: TestClient,
    pipeline: RecordingPipeline,
    session_row: dict[str, Any],
    last_message: dict[str, Any],
    recent: list[dict[str, Any]],
    saved: list[dict[str, Any]],
) -> None:
    recent.extend(
        [
            {"id": uuid4(), "role": "user", "content": "q1", "metadata": None, "created_at": T0},
            {
                "id": uuid4(),
                "role": "assistant",
                "content": "a1",
                "metadata": {"mode": "SYNTHESIS"},
                "created_at": T0,
            },
        ]
    )
    assert _post(db_client, "q2").status_code == 200
    turn = pipeline.calls[0]
    assert [(m.role, m.content) for m in turn.history] == [("user", "q1"), ("assistant", "a1")]


def test_chat_passes_topic_and_memory_to_pipeline(
    db_client: TestClient,
    pipeline: RecordingPipeline,
    session_row: dict[str, Any],
    last_message: dict[str, Any],
    saved: list[dict[str, Any]],
) -> None:
    session_row["memory_summary"] = "user studies floods"
    session_row["memory_facts"] = ["lives in Manila"]
    last_message["row"] = {
        "id": uuid4(),
        "role": "assistant",
        "metadata": {"mode": "LIST", "topic": "floods"},
    }
    assert _post(db_client, "and typhoons?").status_code == 200
    turn = pipeline.calls[0]
    assert turn.current_topic == "floods"
    assert turn.memory_summary == "user studies floods"
    assert turn.memory_facts == ["lives in Manila"]


def test_chat_metadata_omits_empty_topic_and_sources(
    db_client: TestClient,
    pipeline: RecordingPipeline,
    session_row: dict[str, Any],
    last_message: dict[str, Any],
    saved: list[dict[str, Any]],
) -> None:
    """A REFUSAL-style result: zero usage, no cost, no sources, topic unchanged."""
    from app.chat.pipeline import ChatTurnResult
    from app.schemas.chat import TokenUsage

    pipeline.result = ChatTurnResult(
        response_text="I can't provide personal advice.",
        token_usage=TokenUsage(prompt_tokens=0, completion_tokens=0, total_tokens=0),
        cost_usd=None,
        mode="REFUSAL",
        topic=None,
        sources=[],
    )
    body = _post(db_client, "should I sue?").json()
    assert body["mode"] == "REFUSAL" and body["cost_usd"] is None and body["sources"] == []
    assert saved[1]["metadata"] == {"mode": "REFUSAL"}
    assert saved[1]["cost_estimate"] is None


def test_chat_unknown_session_is_404_and_stores_nothing(
    db_client: TestClient,
    pipeline: RecordingPipeline,
    session_row: dict[str, Any],
    saved: list[dict[str, Any]],
) -> None:
    other = uuid4()
    resp = db_client.post("/api/chat", json={"message": "hi", "session_id": str(other)})
    assert resp.status_code == 404
    assert resp.json() == {"detail": f"Session {other} not found", "status_code": 404}
    assert saved == [] and pipeline.calls == []


def test_chat_validation_errors(db_client: TestClient, saved: list[dict[str, Any]]) -> None:
    assert db_client.post("/api/chat", json={"message": "", "session_id": str(SESSION_ID)}).status_code == 422
    assert db_client.post("/api/chat", json={"session_id": str(SESSION_ID)}).status_code == 422
    assert db_client.post("/api/chat", json={"message": "hi"}).status_code == 422
    assert saved == []


def test_chat_pipeline_failure_is_500(
    db_client: TestClient,
    pipeline: RecordingPipeline,
    session_row: dict[str, Any],
    last_message: dict[str, Any],
    saved: list[dict[str, Any]],
) -> None:
    pipeline.error = RuntimeError("upstream exploded")
    resp = _post(db_client)
    assert resp.status_code == 500
    assert resp.json() == {"detail": "Internal server error", "status_code": 500}
    # the write transaction never starts: no partial turn
    assert saved == []


def test_chat_integration_failure_is_502_and_persists_nothing(
    db_client: TestClient,
    fake_conn: FakeConn,
    pipeline: RecordingPipeline,
    session_row: dict[str, Any],
    last_message: dict[str, Any],
    saved: list[dict[str, Any]],
) -> None:
    from app.errors import IntegrationError

    pipeline.error = IntegrationError("network", "Azure OpenAI endpoint unreachable or timed out")
    resp = _post(db_client)
    assert resp.status_code == 502
    assert resp.json() == {"detail": "Azure OpenAI endpoint unreachable or timed out", "status_code": 502}
    assert saved == []
    assert fake_conn.events == ["open", "close"]  # only the read transaction ran


def test_chat_session_deleted_between_read_and_write_is_404(
    db_client: TestClient,
    pipeline: RecordingPipeline,
    session_row: dict[str, Any],
    last_message: dict[str, Any],
    saved: list[dict[str, Any]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sessions_db, "touch_session", lambda conn, sid: False)
    resp = _post(db_client)
    assert resp.status_code == 404
    assert saved == [] and len(pipeline.calls) == 1


def test_chat_db_failure_is_500(
    db_client: TestClient,
    pipeline: RecordingPipeline,
    session_row: dict[str, Any],
    last_message: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def broken_save(*args: Any, **kwargs: Any) -> Any:
        raise psycopg.OperationalError("connection lost")

    monkeypatch.setattr(messages_db, "save_message", broken_save)
    resp = _post(db_client)
    assert resp.status_code == 500
    assert resp.json()["status_code"] == 500
    assert len(pipeline.calls) == 1  # the failure is in the write phase; the real connection rolls it back


def test_chat_read_failure_is_500_and_skips_pipeline(
    db_client: TestClient,
    pipeline: RecordingPipeline,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def broken_get(*args: Any, **kwargs: Any) -> Any:
        raise psycopg.OperationalError("connection lost")

    monkeypatch.setattr(sessions_db, "get_session", broken_get)
    resp = _post(db_client)
    assert resp.status_code == 500
    assert pipeline.calls == []


def test_chat_without_pipeline_is_503(
    settings: Settings, session_row: dict[str, Any], last_message: dict[str, Any], saved: list[dict[str, Any]]
) -> None:
    """The production default: nothing configured → explicit 503, never a fake answer."""
    from contextlib import nullcontext

    from app.api.deps import get_db_connector
    from tests.conftest import sign_in

    app = create_app(settings, chat_pipeline=unconfigured_pipeline)
    app.dependency_overrides[get_db_connector] = lambda: lambda: nullcontext(object())
    sign_in(app)
    with TestClient(app) as c:
        resp = _post(c)
    assert resp.status_code == 503
    assert resp.json() == {
        "detail": "Chat pipeline not available: AI integrations are not implemented yet",
        "status_code": 503,
    }
    assert saved == []  # the write transaction never starts


def test_chat_without_database_is_503(client: TestClient) -> None:
    resp = client.post("/api/chat", json={"message": "hi", "session_id": str(SESSION_ID)})
    assert resp.status_code == 503
    assert resp.json()["detail"] == "Database not configured"


def _msg(role: str, content: str) -> dict[str, Any]:
    return {"id": uuid4(), "role": role, "content": content, "metadata": None, "created_at": T0}


def test_chat_persists_subject_and_memory_only_when_the_pipeline_provides_them(
    db_client: TestClient,
    pipeline: RecordingPipeline,
    session_row: dict[str, Any],
    last_message: dict[str, Any],
    saved: list[dict[str, Any]],
    session_writes: list[tuple[str, Any]],
) -> None:
    from dataclasses import replace

    from app.chat.memory import MemoryUpdate

    assert _post(db_client).status_code == 200
    assert session_writes == []  # default result: nothing to persist

    pipeline.result = replace(
        pipeline.result,
        subject="Urban flood risk",
        memory=MemoryUpdate(summary="They discussed floods.", turns=7),
        details={"routing": {"query": "q", "topic_changed": True, "fallback": False}},
    )
    assert _post(db_client).status_code == 200
    assert session_writes == [("subject", "Urban flood risk"), ("memory", ("They discussed floods.", 7))]
    assert saved[-1]["metadata"]["routing"] == {"query": "q", "topic_changed": True, "fallback": False}


def test_chat_history_window_grows_to_cover_uncovered_messages(
    db_client: TestClient,
    pipeline: RecordingPipeline,
    session_row: dict[str, Any],
    last_message: dict[str, Any],
    recent: list[dict[str, Any]],
    saved: list[dict[str, Any]],
) -> None:
    """window=6, compact_after=12: a 10-message session sends all 10 (nothing falls between)."""
    session_row["memory_turns"] = 0
    recent.extend(_msg("user" if i % 2 == 0 else "assistant", f"m{i}") for i in range(10))
    assert _post(db_client).status_code == 200
    turn = pipeline.calls[0]
    assert [m.content for m in turn.history] == [f"m{i}" for i in range(10)]
    assert turn.older_messages == [] and turn.memory_turns == 0


def test_chat_compaction_due_splits_older_from_window(
    db_client: TestClient,
    pipeline: RecordingPipeline,
    session_row: dict[str, Any],
    last_message: dict[str, Any],
    recent: list[dict[str, Any]],
    saved: list[dict[str, Any]],
) -> None:
    """14 messages, none covered: 14 - 6 - 0 >= 6 → compact m0..m7, window m8..m13."""
    session_row["memory_turns"] = 0
    session_row["memory_summary"] = "so far"
    session_row["subject"] = "Floods"
    recent.extend(_msg("user" if i % 2 == 0 else "assistant", f"m{i}") for i in range(14))
    assert _post(db_client).status_code == 200
    turn = pipeline.calls[0]
    assert [m.content for m in turn.older_messages] == [f"m{i}" for i in range(8)]
    assert [m.content for m in turn.history] == [f"m{i}" for i in range(8, 14)]
    assert (turn.subject, turn.memory_summary, turn.memory_turns) == ("Floods", "so far", 0)


def test_chat_compaction_respects_covered_messages(
    db_client: TestClient,
    pipeline: RecordingPipeline,
    session_row: dict[str, Any],
    last_message: dict[str, Any],
    recent: list[dict[str, Any]],
    saved: list[dict[str, Any]],
) -> None:
    """16 messages with 8 covered: 16 - 6 - 8 = 2 < 6 → not due; window = the 8 uncovered."""
    session_row["memory_turns"] = 8
    recent.extend(_msg("user" if i % 2 == 0 else "assistant", f"m{i}") for i in range(16))
    assert _post(db_client).status_code == 200
    turn = pipeline.calls[0]
    assert turn.older_messages == []
    assert [m.content for m in turn.history] == [f"m{i}" for i in range(8, 16)]
