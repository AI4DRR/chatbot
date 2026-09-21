"""Invitations without a database: mail content, token handling, guard, e-mail failure rule, setup page."""

from __future__ import annotations

import smtplib
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_db
from app.config import Settings
from app.db import auth as auth_db
from app.errors import IntegrationConfigError
from app.integrations.mail import FileMailer, Mail, NoMailer, build_mailer
from app.main import create_app
from app.schemas.auth import InvitationInfo
from app.services import auth as auth_service
from app.services import invitations
from app.services.invitations import IssuedInvitation
from tests.conftest import TEST_USER, MemoryMailer, sign_in

ADMIN = TEST_USER.model_copy(update={"id": str(uuid4()), "email": "admin@example.org", "is_admin": True})


def _info(email: str = "invitee@example.org", **over: Any) -> InvitationInfo:
    now = datetime.now(UTC)
    base: dict[str, Any] = {
        "id": str(uuid4()),
        "email": email,
        "created_at": now,
        "expires_at": now + timedelta(hours=24),
        "status": "pending",
        "invited_by_email": "admin@example.org",
    }
    base.update(over)
    return InvitationInfo(**base)


def _app(user: Any, mailer: Any) -> TestClient:
    app = create_app(
        Settings(frontend_dir="none", app_base_url="https://chat.example.org", local_auth_enabled=True),
        mailer=mailer,
    )
    sign_in(app, user)
    app.dependency_overrides[get_db] = lambda: object()
    return TestClient(app, raise_server_exceptions=False)


# --- mail content and link -------------------------------------------------------------


def test_setup_link_keeps_the_token_in_the_fragment() -> None:
    link = invitations.setup_url("https://chat.example.org", "tok-123")
    assert link == "https://chat.example.org/account-setup#token=tok-123"


def test_invitation_mail_identifies_the_app_has_one_cta_and_states_expiry() -> None:
    m = invitations.build_invitation_mail("invitee@example.org", "https://x/account-setup#token=abc", 24)
    assert m.to == "invitee@example.org" and m.subject == "You've been invited to AI4DRR Chatbot"
    assert "You have been invited to access the AI4DRR Chatbot" in m.text
    assert "Complete your account setup using the link below" in m.text
    assert m.text.count("https://x/account-setup#token=abc") == 1
    assert "expire in 24 hours" in m.text
    assert m.html and m.html.count("href=") == 1 and "Set up my account" in m.html


def test_mailer_selection_and_none_transport() -> None:
    assert isinstance(build_mailer(Settings(email_transport="none")), NoMailer)
    with pytest.raises(IntegrationConfigError):
        NoMailer().send(Mail(to="a@b.c", subject="s", text="t"))
    with pytest.raises(ValueError, match="EMAIL_TRANSPORT"):
        build_mailer(Settings(email_transport="pigeon"))
    with pytest.raises(IntegrationConfigError, match="SMTP_HOST"):
        build_mailer(Settings(email_transport="smtp"))


def test_file_mailer_writes_an_eml(tmp_path: Any) -> None:
    FileMailer(str(tmp_path)).send(Mail(to="a@example.org", subject="Hello", text="body", html="<p>body</p>"))
    files = list(tmp_path.glob("*.eml"))
    assert len(files) == 1
    raw = files[0].read_text()
    assert "Subject: Hello" in raw and "To: a@example.org" in raw and "text/html" in raw


def test_smtp_mailer_uses_starttls_login_and_reports_failures(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    class FakeSmtp:
        def __init__(self, host: str, port: int, timeout: float) -> None:
            calls.append(f"connect {host}:{port}")

        def __enter__(self) -> FakeSmtp:
            return self

        def __exit__(self, *a: object) -> None:
            calls.append("quit")

        def ehlo(self) -> None:
            calls.append("ehlo")

        def has_extn(self, name: str) -> bool:
            return name == "starttls"

        def starttls(self, context: object = None) -> None:
            calls.append("starttls")

        def login(self, user: str, password: str) -> None:
            calls.append(f"login {user}")  # never the password

        def send_message(self, msg: object) -> None:
            calls.append("send")

    monkeypatch.setattr(smtplib, "SMTP", FakeSmtp)
    settings = Settings(
        email_transport="smtp", smtp_host="smtp.example.org", smtp_port=587, smtp_user="u", smtp_pass="p"
    )
    build_mailer(settings).send(Mail(to="a@example.org", subject="s", text="t"))
    assert calls == ["connect smtp.example.org:587", "ehlo", "starttls", "ehlo", "login u", "send", "quit"]

    class BrokenSmtp(FakeSmtp):
        def send_message(self, msg: object) -> None:
            raise smtplib.SMTPException("boom")

    monkeypatch.setattr(smtplib, "SMTP", BrokenSmtp)
    from app.errors import IntegrationError

    with pytest.raises(IntegrationError) as info:
        build_mailer(settings).send(Mail(to="a@example.org", subject="s", text="t"))
    assert info.value.kind == "mail" and info.value.status_code == 502


# --- guard --------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("get", "/api/admin/invitations"),
        ("post", "/api/admin/invitations"),
        ("post", f"/api/admin/invitations/{uuid4()}/resend"),
        ("post", f"/api/admin/invitations/{uuid4()}/revoke"),
    ],
)
def test_invitation_endpoints_require_an_admin(method: str, path: str) -> None:
    with _app(None, MemoryMailer()) as c:
        assert c.request(method.upper(), path, json={"email": "a@example.org"}).status_code == 401
    with _app(TEST_USER, MemoryMailer()) as c:
        assert c.request(method.upper(), path, json={"email": "a@example.org"}).status_code == 403


# --- invite: happy path, duplicates, e-mail failure rule ----------------------------------------


def test_invite_stores_then_sends_and_never_returns_the_token(monkeypatch: pytest.MonkeyPatch) -> None:
    mailer = MemoryMailer()
    created: list[tuple[str, str]] = []

    def fake_invite(conn: Any, email: str, admin: Any, ttl: int) -> IssuedInvitation:
        created.append((email, admin.id))
        return IssuedInvitation(info=_info(email), token="raw-token-value")

    monkeypatch.setattr(invitations, "invite", fake_invite)
    with _app(ADMIN, mailer) as c:
        resp = c.post("/api/admin/invitations", json={"email": "Invitee@Example.org"})
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert created == [("Invitee@example.org", ADMIN.id)]  # EmailStr normalises the domain
    assert set(body) == {
        "id",
        "email",
        "created_at",
        "expires_at",
        "status",
        "invited_by_email",
        "grants_admin",
    }
    assert body["grants_admin"] is False  # the API never issues administrator invitations
    assert "raw-token-value" not in resp.text  # the token exists only in the e-mail
    assert len(mailer.sent) == 1 and mailer.sent[0].to == "Invitee@example.org"
    assert "https://chat.example.org/account-setup#token=raw-token-value" in mailer.sent[0].text


def test_invite_email_failure_discards_the_invitation_and_reports_502(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    discarded: list[str] = []
    info = _info()
    monkeypatch.setattr(
        invitations, "invite", lambda conn, email, admin, ttl: IssuedInvitation(info, "t" * 32)
    )
    monkeypatch.setattr(invitations, "discard_unsent", lambda conn, iid: discarded.append(iid))
    with _app(ADMIN, MemoryMailer(fail=True)) as c:
        resp = c.post("/api/admin/invitations", json={"email": "invitee@example.org"})
    assert resp.status_code == 502 and "could not be sent" in resp.json()["detail"]
    assert discarded == [info.id]


def test_invite_without_mail_transport_is_503_and_discards(monkeypatch: pytest.MonkeyPatch) -> None:
    discarded: list[str] = []
    info = _info()
    monkeypatch.setattr(
        invitations, "invite", lambda conn, email, admin, ttl: IssuedInvitation(info, "t" * 32)
    )
    monkeypatch.setattr(invitations, "discard_unsent", lambda conn, iid: discarded.append(iid))
    with _app(ADMIN, NoMailer()) as c:
        resp = c.post("/api/admin/invitations", json={"email": "invitee@example.org"})
    assert resp.status_code == 503 and discarded == [info.id]


def test_resend_sends_a_fresh_link_and_revoke_needs_no_mail(monkeypatch: pytest.MonkeyPatch) -> None:
    mailer = MemoryMailer()
    info = _info()
    monkeypatch.setattr(
        invitations, "resend", lambda conn, iid, ttl: IssuedInvitation(info, "fresh-token-xyz")
    )
    monkeypatch.setattr(invitations, "revoke", lambda conn, iid: _info(status="revoked"))
    with _app(ADMIN, mailer) as c:
        assert c.post(f"/api/admin/invitations/{info.id}/resend").status_code == 200
        assert "fresh-token-xyz" in mailer.sent[-1].text
        assert c.post(f"/api/admin/invitations/{info.id}/revoke").json()["status"] == "revoked"
    assert len(mailer.sent) == 1


# --- setup endpoints and page ------------------------------------------------------------


def test_setup_check_and_complete_report_token_problems_as_410(monkeypatch: pytest.MonkeyPatch) -> None:
    def bad(conn: Any, token: str) -> Any:
        raise invitations.SetupTokenError("This invitation link is no longer valid (expired)")

    monkeypatch.setattr(invitations, "check_token", bad)
    with _app(None, MemoryMailer()) as c:
        resp = c.post("/api/auth/setup/check", json={"token": "x" * 32})
    assert resp.status_code == 410 and "expired" in resp.json()["detail"]


def test_setup_complete_opens_a_session(monkeypatch: pytest.MonkeyPatch) -> None:
    started: list[tuple[str, str]] = []
    monkeypatch.setattr(invitations, "complete_setup", lambda conn, *a: TEST_USER)

    def fake_start(conn: Any, uid: str, method: str, ttl: int) -> str:
        started.append((uid, method))
        return "cookie-value"

    monkeypatch.setattr(auth_service, "start_session", fake_start)
    with _app(None, MemoryMailer()) as c:
        resp = c.post(
            "/api/auth/setup",
            json={
                "token": "x" * 32,
                "password": "p" * 12,
                "confirm_password": "p" * 12,
                "first_name": "A",
                "last_name": "B",
            },
        )
    assert resp.status_code == 201 and resp.json()["email"] == TEST_USER.email
    assert started == [(TEST_USER.id, "local")]
    assert (
        "ai4drr_session=cookie-value" in resp.headers["set-cookie"]
        and "HttpOnly" in resp.headers["set-cookie"]
    )


def test_setup_page_is_public_and_reads_the_fragment() -> None:
    with _app(None, MemoryMailer()) as c:
        resp = c.get("/account-setup")
    assert resp.status_code == 200 and "Set up your account" in resp.text and "/setup.js" in resp.text


# --- operator bootstrap CLI (no database: the service calls are stubbed) --------------------


def test_bootstrap_cli_rejects_bad_email_and_needs_a_database(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.db import admin_ops

    monkeypatch.setattr(admin_ops, "load_settings", lambda: Settings(database_url="postgresql://x/y"))
    assert admin_ops.main(["bootstrap", "not-an-address"]) == 2
    assert admin_ops.main(["grant", "@nope"]) == 2
    monkeypatch.setattr(admin_ops, "load_settings", lambda: Settings())
    assert admin_ops.main(["bootstrap", "ops@example.org"]) == 2  # no DATABASE_URL
    with pytest.raises(SystemExit):
        admin_ops.main(["bootstrap"])  # e-mail argument is required


def test_bootstrap_prints_the_link_once_and_never_logs_it(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], caplog: pytest.LogCaptureFixture
) -> None:
    from app.db import admin_ops

    class Conn:
        commits = 0

        def commit(self) -> None:
            self.commits += 1

    monkeypatch.setattr(auth_db, "get_user_by_email", lambda conn, email: None)
    monkeypatch.setattr(
        invitations,
        "issue_admin_bootstrap",
        lambda conn, email, ttl: IssuedInvitation(_info(email, grants_admin=True), "boot-token-abc"),
    )
    settings = Settings(app_base_url="https://chat.example.org", local_auth_enabled=True)
    conn = Conn()
    with caplog.at_level("DEBUG"):
        assert admin_ops.bootstrap(conn, settings, "ops@example.org", send=False) == 0  # type: ignore[arg-type]
    out = capsys.readouterr().out
    assert "https://chat.example.org/account-setup#token=boot-token-abc" in out
    assert "administrator" in out and conn.commits == 1
    assert "boot-token-abc" not in caplog.text


def test_bootstrap_send_uses_the_mailer_and_discards_on_failure(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from app.db import admin_ops

    class Conn:
        def commit(self) -> None:
            pass

    discarded: list[str] = []
    info = _info("ops@example.org", grants_admin=True)
    monkeypatch.setattr(auth_db, "get_user_by_email", lambda conn, email: None)
    monkeypatch.setattr(
        invitations,
        "issue_admin_bootstrap",
        lambda conn, email, ttl: IssuedInvitation(info, "boot-token-abc"),
    )
    monkeypatch.setattr(invitations, "discard_unsent", lambda conn, iid: discarded.append(iid))
    settings = Settings(app_base_url="https://chat.example.org", local_auth_enabled=True)
    ok = MemoryMailer()
    code = admin_ops.bootstrap(Conn(), settings, "ops@example.org", send=True, mailer_factory=lambda s: ok)  # type: ignore[arg-type]
    out = capsys.readouterr().out
    assert code == 0 and len(ok.sent) == 1 and ok.sent[0].to == "ops@example.org"
    assert "boot-token-abc" not in out  # sent, therefore not printed
    bad = MemoryMailer(fail=True)
    code = admin_ops.bootstrap(Conn(), settings, "ops@example.org", send=True, mailer_factory=lambda s: bad)  # type: ignore[arg-type]
    assert code == 1 and discarded == [info.id]
    assert "could not be sent" in capsys.readouterr().out


def test_bootstrap_promotes_an_existing_user_and_is_idempotent(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from app.db import admin_ops

    granted: list[tuple[str, bool]] = []
    user = {"email": "Ops@example.org", "is_admin": False, "disabled_at": None}
    monkeypatch.setattr(auth_db, "get_user_by_email", lambda conn, email: user)
    monkeypatch.setattr(auth_db, "set_admin", lambda conn, email, flag: granted.append((email, flag)))
    monkeypatch.setattr(
        invitations, "issue_admin_bootstrap", lambda *a: pytest.fail("no invitation for an existing user")
    )
    settings = Settings()
    assert admin_ops.bootstrap(object(), settings, "ops@example.org", send=False) == 0  # type: ignore[arg-type]
    assert granted == [("ops@example.org", True)] and "promoted" in capsys.readouterr().out
    user["is_admin"] = True
    assert admin_ops.bootstrap(object(), settings, "ops@example.org", send=False) == 0  # type: ignore[arg-type]
    assert granted == [("ops@example.org", True)] and "already an administrator" in capsys.readouterr().out
