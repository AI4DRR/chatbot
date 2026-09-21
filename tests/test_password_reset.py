"""Forgot / reset password without a database: mail, generic answers, token rules, page."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_db
from app.config import Settings
from app.db import auth as auth_db
from app.db import password_resets as reset_db
from app.errors import ValidationError
from app.main import create_app
from app.services import password_reset
from tests.conftest import MemoryMailer


def _app(mailer: Any = None) -> TestClient:
    app = create_app(
        Settings(frontend_dir="none", app_base_url="https://chat.example.org", local_auth_enabled=True),
        mailer=mailer or MemoryMailer(),
    )
    app.dependency_overrides[get_db] = lambda: object()
    return TestClient(app, raise_server_exceptions=False)


def _row(**over: Any) -> dict[str, Any]:
    now = datetime.now(UTC)
    base: dict[str, Any] = {
        "id": uuid4(),
        "user_id": uuid4(),
        "token_hash": "h" * 64,
        "created_at": now,
        "expires_at": now + timedelta(minutes=60),
        "consumed_at": None,
        "revoked_at": None,
        "email": "person@example.org",
        "has_password": True,
        "disabled_at": None,
    }
    base.update(over)
    return base


# --- mail ----------------------------------------------------------------------------


def test_reset_link_keeps_the_token_in_the_fragment() -> None:
    assert (
        password_reset.reset_url("https://chat.example.org", "tok-1")
        == "https://chat.example.org/reset-password#token=tok-1"
    )


def test_reset_mail_identifies_the_app_has_one_cta_and_states_expiry() -> None:
    m = password_reset.build_reset_mail("p@example.org", "https://x/reset-password#token=abc", 60)
    assert m.to == "p@example.org" and m.subject == "Reset your AI4DRR Chatbot password"
    assert m.text.count("https://x/reset-password#token=abc") == 1
    assert "expires in 60 minutes" in m.text and "can be used once" in m.text
    assert m.html and m.html.count("href=") == 1 and "Reset my password" in m.html


# --- request: generic outcome, no enumeration ------------------------------------------------


def _stub_db(monkeypatch: pytest.MonkeyPatch, user: dict[str, Any] | None) -> dict[str, list[Any]]:
    calls: dict[str, list[Any]] = {"revoked": [], "created": [], "deleted": []}
    monkeypatch.setattr(auth_db, "get_user_by_email", lambda conn, email: user)

    def revoke(conn: Any, uid: Any) -> int:
        calls["revoked"].append(uid)
        return 0

    monkeypatch.setattr(reset_db, "revoke_open_for_user", revoke)

    def create(conn: Any, uid: Any, token_hash: str, expires_at: datetime) -> dict[str, Any]:
        calls["created"].append(token_hash)
        return _row(id=uuid4(), user_id=uid, token_hash=token_hash, expires_at=expires_at)

    monkeypatch.setattr(reset_db, "create", create)
    monkeypatch.setattr(reset_db, "delete", lambda conn, rid: calls["deleted"].append(rid))
    return calls


def test_request_for_a_local_account_stores_a_hash_and_mails_the_link(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    uid = uuid4()
    calls = _stub_db(
        monkeypatch,
        {"id": uid, "email": "Person@example.org", "password_hash": "argon2", "disabled_at": None},
    )
    mailer = MemoryMailer()
    with caplog.at_level("DEBUG"):
        password_reset.request_reset(object(), mailer, "person@EXAMPLE.org", "https://chat.example.org", 60)  # type: ignore[arg-type]
    assert calls["revoked"] == [uid] and len(calls["created"]) == 1 and calls["deleted"] == []
    assert len(mailer.sent) == 1 and mailer.sent[0].to == "Person@example.org"
    token = mailer.last_link().split("#token=", 1)[1]
    assert len(token) >= 40 and calls["created"][0] != token and len(calls["created"][0]) == 64
    assert token not in caplog.text


@pytest.mark.parametrize(
    "user",
    [None, {"id": uuid4(), "email": "entra@example.org", "password_hash": None, "disabled_at": None}],
    ids=["unknown-email", "entra-only"],
)
def test_request_for_unknown_or_passwordless_account_sends_nothing(
    monkeypatch: pytest.MonkeyPatch, user: dict[str, Any] | None, caplog: pytest.LogCaptureFixture
) -> None:
    calls = _stub_db(monkeypatch, user)
    mailer = MemoryMailer()
    with caplog.at_level("INFO"):
        password_reset.request_reset(object(), mailer, "entra@example.org", "https://x", 60)  # type: ignore[arg-type]
    assert mailer.sent == [] and calls["created"] == [] and calls["revoked"] == []
    assert "unknown, password-less or disabled" in caplog.text and "entra@example.org" not in caplog.text


def test_request_mail_failure_discards_the_row_and_stays_silent(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    uid = uuid4()
    calls = _stub_db(
        monkeypatch, {"id": uid, "email": "p@example.org", "password_hash": "argon2", "disabled_at": None}
    )
    with caplog.at_level("ERROR"):
        password_reset.request_reset(object(), MemoryMailer(fail=True), "p@example.org", "https://x", 60)  # type: ignore[arg-type]
    assert len(calls["created"]) == 1 and len(calls["deleted"]) == 1
    assert "not sent" in caplog.text


def test_forgot_endpoint_answers_identically_for_every_outcome(monkeypatch: pytest.MonkeyPatch) -> None:
    bodies = []
    for user, mailer in (
        (
            {"id": uuid4(), "email": "p@example.org", "password_hash": "argon2", "disabled_at": None},
            MemoryMailer(),
        ),
        (None, MemoryMailer()),
        (
            {"id": uuid4(), "email": "p@example.org", "password_hash": None, "disabled_at": None},
            MemoryMailer(),
        ),
        (
            {"id": uuid4(), "email": "p@example.org", "password_hash": "argon2", "disabled_at": None},
            MemoryMailer(fail=True),
        ),
    ):
        _stub_db(monkeypatch, user)
        with _app(mailer) as c:
            resp = c.post("/api/auth/local/forgot", json={"email": "p@example.org"})
        bodies.append((resp.status_code, resp.json()))
    assert len(set(map(str, bodies))) == 1 and bodies[0][0] == 202
    assert bodies[0][1]["detail"] == password_reset.GENERIC_RESPONSE


# --- token rules ------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("row", "reason"),
    [
        (None, "not valid"),
        (_row(consumed_at=datetime.now(UTC)), "used"),
        (_row(revoked_at=datetime.now(UTC)), "replaced"),
        (_row(expires_at=datetime.now(UTC) - timedelta(seconds=1)), "expired"),
        (_row(has_password=False), "not valid"),
    ],
    ids=["unknown", "used", "replaced", "expired", "password-less"],
)
def test_unusable_tokens_are_410_with_the_reason(
    monkeypatch: pytest.MonkeyPatch, row: dict[str, Any] | None, reason: str
) -> None:
    monkeypatch.setattr(reset_db, "get_by_token_hash", lambda conn, h: row)
    monkeypatch.setattr(reset_db, "get_by_token_hash_for_update", lambda conn, h: row)
    with _app() as c:
        check = c.post("/api/auth/reset/check", json={"token": "t" * 32})
        done = c.post(
            "/api/auth/reset", json={"token": "t" * 32, "password": "x" * 12, "confirm_password": "x" * 12}
        )
    assert check.status_code == 410 and reason in check.json()["detail"]
    assert done.status_code == 410 and reason in done.json()["detail"]


def test_check_returns_the_account_email(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(reset_db, "get_by_token_hash", lambda conn, h: _row(email="Who@example.org"))
    with _app() as c:
        resp = c.post("/api/auth/reset/check", json={"token": "t" * 32})
    assert resp.status_code == 200 and resp.json()["email"] == "Who@example.org"
    assert "token" not in resp.text and "hash" not in resp.text


def test_complete_validates_the_password_before_touching_the_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        reset_db, "get_by_token_hash_for_update", lambda conn, h: pytest.fail("token read before validation")
    )
    with pytest.raises(ValidationError, match="do not match"):
        password_reset.complete_reset(object(), "t" * 32, "x" * 12, "y" * 12)  # type: ignore[arg-type]
    with _app() as c:
        resp = c.post(
            "/api/auth/reset", json={"token": "t" * 32, "password": "short", "confirm_password": "short"}
        )
    assert resp.status_code == 400 and "at least 8" in resp.json()["detail"]


def test_complete_sets_password_consumes_and_ends_sessions(monkeypatch: pytest.MonkeyPatch) -> None:
    row = _row()
    events: list[str] = []

    def set_password(conn: Any, uid: Any, h: str) -> bool:
        events.append(f"password {uid}")
        return True

    def revoke(conn: Any, uid: Any) -> int:
        events.append("revoke-others")
        return 0

    def end_sessions(conn: Any, uid: Any) -> int:
        events.append("sessions")
        return 2

    monkeypatch.setattr(reset_db, "get_by_token_hash_for_update", lambda conn, h: row)
    monkeypatch.setattr(auth_db, "set_password", set_password)
    monkeypatch.setattr(reset_db, "consume", lambda conn, rid: events.append(f"consume {rid}"))
    monkeypatch.setattr(reset_db, "revoke_open_for_user", revoke)
    monkeypatch.setattr(auth_db, "delete_user_sessions", end_sessions)
    with _app() as c:
        resp = c.post(
            "/api/auth/reset", json={"token": "t" * 32, "password": "x" * 12, "confirm_password": "x" * 12}
        )
    assert resp.status_code == 204 and "set-cookie" not in resp.headers  # no session is opened
    assert events == [f"password {row['user_id']}", f"consume {row['id']}", "revoke-others", "sessions"]


def test_reset_page_is_served() -> None:
    with _app() as c:
        resp = c.get("/reset-password")
    assert resp.status_code == 200 and "Choose a new password" in resp.text and "/reset.js" in resp.text
