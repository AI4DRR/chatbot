"""LOCAL_AUTH_ENABLED=false — Microsoft-only mode: parsing, server-side blocks, UI gating, invitations."""

from __future__ import annotations

import re
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_db
from app.config import Settings, load_settings
from app.main import create_app
from tests.conftest import TEST_USER, MemoryMailer, sign_in
from tests.test_auth import ENTRA, FakeEntra

ROOT = Path(__file__).resolve().parents[1]
ADMIN = TEST_USER.model_copy(update={"id": str(uuid4()), "email": "admin@example.org", "is_admin": True})


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("false", False),
        ("False", False),
        ("0", False),
        ("no", False),
        ("off", False),
        ("", False),
        ("true", True),
        ("TRUE", True),
        ("1", True),
        ("yes", True),
        ("on", True),
    ],
)
def test_flag_is_parsed_from_the_environment(
    monkeypatch: pytest.MonkeyPatch, raw: str, expected: bool
) -> None:
    monkeypatch.setenv("LOCAL_AUTH_ENABLED", raw)
    assert load_settings().local_auth_enabled is expected


def test_flag_defaults_to_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LOCAL_AUTH_ENABLED", raising=False)
    assert load_settings().local_auth_enabled is False


def _app(local: bool, user: object | None = None) -> TestClient:
    settings = replace(
        ENTRA, frontend_dir="none", local_auth_enabled=local, app_base_url="https://chat.example.org"
    )
    app = create_app(settings, entra_client=FakeEntra(), mailer=MemoryMailer())
    if user is not None:
        sign_in(app, user)  # type: ignore[arg-type]
    app.dependency_overrides[get_db] = lambda: object()
    return TestClient(app, raise_server_exceptions=False)


def test_config_reports_microsoft_only_and_local_endpoints_are_blocked() -> None:
    setup = {
        "token": "t" * 32,
        "password": "x" * 12,
        "confirm_password": "x" * 12,
        "first_name": "A",
        "last_name": "B",
    }
    with _app(False) as c:
        cfg = c.get("/api/auth/config").json()
        assert cfg == {"entra_enabled": True, "local_auth_enabled": False}
        blocked = {
            "/api/auth/local/login": {"email": "a@example.org", "password": "p" * 12},
            "/api/auth/local/forgot": {"email": "a@example.org"},
            "/api/auth/reset/check": {"token": "t" * 32},
            "/api/auth/reset": {"token": "t" * 32, "password": "x" * 12, "confirm_password": "x" * 12},
            "/api/auth/setup/check": {"token": "t" * 32},
            "/api/auth/setup": setup,
        }
        for path, body in blocked.items():
            resp = c.post(path, json=body)
            assert resp.status_code == 403, path
            assert "disabled" in resp.json()["detail"], path
        assert c.post("/api/auth/local/register", json=setup).status_code == 404
        # Microsoft stays available and prominent
        assert c.get("/api/auth/entra/login", follow_redirects=False).status_code == 302
        # the pages still exist; they show the server's message
        assert c.get("/account-setup").status_code == 200 and c.get("/reset-password").status_code == 200


def test_admin_cannot_invite_or_resend_in_microsoft_only_mode_but_can_list_and_revoke(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services import invitations

    monkeypatch.setattr(invitations, "list_pending", lambda conn: [])
    revoked: list[str] = []

    def revoke(conn: object, iid: str) -> object:
        revoked.append(iid)
        raise RuntimeError("stub")  # the route reached the service: that is what matters here

    monkeypatch.setattr(invitations, "revoke", revoke)
    monkeypatch.setattr(invitations, "invite", lambda *a: pytest.fail("no invitation may be created"))
    monkeypatch.setattr(invitations, "resend", lambda *a: pytest.fail("no invitation may be re-sent"))
    with _app(False, ADMIN) as c:
        create = c.post("/api/admin/invitations", json={"email": "new@example.org"})
        assert create.status_code == 403 and "LOCAL_AUTH_ENABLED=false" in create.json()["detail"]
        assert c.post(f"/api/admin/invitations/{uuid4()}/resend").status_code == 403
        assert c.get("/api/admin/invitations").status_code == 200
        c.post(f"/api/admin/invitations/{uuid4()}/revoke")  # reaches the service (stub raises afterwards)
        assert len(revoked) == 1
        # Microsoft admin doorway is untouched: guard still by users.is_admin
        assert c.get("/api/admin/me").status_code == 200
    with _app(False, TEST_USER) as c:
        assert c.get("/api/admin/me").status_code == 403


def test_admin_bootstrap_refuses_to_issue_a_local_invitation_in_microsoft_only_mode(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from app.db import admin_ops
    from app.db import auth as auth_db
    from app.services import invitations

    monkeypatch.setattr(auth_db, "get_user_by_email", lambda conn, email: None)
    monkeypatch.setattr(invitations, "issue_admin_bootstrap", lambda *a: pytest.fail("must not issue"))
    code = admin_ops.bootstrap(object(), Settings(local_auth_enabled=False), "ops@example.org", send=False)  # type: ignore[arg-type]
    out = capsys.readouterr().out
    assert code == 1 and "sign in with Microsoft once" in out and "account-setup" not in out
    # an existing Microsoft user is still promoted
    granted: list[str] = []
    monkeypatch.setattr(
        auth_db,
        "get_user_by_email",
        lambda conn, email: {"email": email, "is_admin": False, "disabled_at": None},
    )
    monkeypatch.setattr(auth_db, "set_admin", lambda conn, email, flag: granted.append(email))
    code = admin_ops.bootstrap(object(), Settings(local_auth_enabled=False), "ops@example.org", send=False)  # type: ignore[arg-type]
    assert code == 0
    assert granted == ["ops@example.org"]


def test_everything_local_is_marked_for_ui_gating() -> None:
    """Sign-in screens hide the local form, its Forgot link and the reset view via body[data-local-auth]."""
    css = (ROOT / "frontend" / "styles.css").read_text()
    assert '[data-local-auth="off"] [data-local-auth-only] { display: none !important; }' in css
    for page in (ROOT / "frontend" / "index.html", ROOT / "app" / "pages" / "admin" / "login.html"):
        html = page.read_text()
        form = re.search(r'<form class="auth-form" id="localForm"([^>]*)>(.*?)</form>', html, re.S)
        assert form and "data-local-auth-only" in form.group(1), page.name
        assert "Forgot password?" in form.group(2), page.name  # the link lives inside the gated form
        assert (
            'id="ssoBtn"' in html
            and "data-local-auth-only" not in html.split('id="ssoBtn"')[0].rsplit("<", 1)[-1]
        )
    index = (ROOT / "frontend" / "index.html").read_text()
    assert re.search(r'id="authReset" data-local-auth-only', index)
    js = (ROOT / "frontend" / "app.js").read_text()
    assert "document.body.dataset.localAuth = cfg.local_auth_enabled ? 'on' : 'off'" in js
    admin_js = (ROOT / "frontend" / "admin" / "admin-users.js").read_text()
    assert "$('inviteBtn').disabled = true" in admin_js and "local_auth_enabled" in admin_js


def test_flag_on_restores_local_endpoints(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services import password_reset

    monkeypatch.setattr(password_reset, "request_reset", lambda *a: None)
    with _app(True) as c:
        assert c.get("/api/auth/config").json()["local_auth_enabled"] is True
        assert c.post("/api/auth/local/forgot", json={"email": "a@example.org"}).status_code == 202
