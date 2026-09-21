"""Admin authorisation without a database: the guard, the pages, the API and the sign-in return path."""

from __future__ import annotations

from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_db
from app.api.routes.auth import safe_next
from app.config import Settings
from app.main import create_app
from app.services import auth as auth_service
from tests.conftest import TEST_USER, sign_in

ADMIN = TEST_USER.model_copy(
    update={"id": "22222222-2222-4222-8222-222222222222", "email": "admin@example.org", "is_admin": True}
)


def _client(user: object | None) -> TestClient:
    app = create_app(Settings(frontend_dir="none", local_auth_enabled=True))
    if user is not None:
        sign_in(app, user)  # type: ignore[arg-type]
    else:
        sign_in(app, None)
    app.dependency_overrides[get_db] = lambda: object()
    return TestClient(app, raise_server_exceptions=False)


UID = "33333333-3333-4333-8333-333333333333"
GUARDED = [
    ("get", "/api/admin/me"),
    ("get", "/api/admin/users"),
    ("get", f"/api/admin/users/{UID}"),
    ("post", f"/api/admin/users/{UID}/disable"),
    ("post", f"/api/admin/users/{UID}/enable"),
]


@pytest.mark.parametrize(("method", "path"), GUARDED)
def test_admin_api_is_401_without_session(method: str, path: str) -> None:
    with _client(None) as c:
        resp = c.request(method.upper(), path)
    assert resp.status_code == 401 and resp.json()["detail"] == "Not authenticated"


@pytest.mark.parametrize(("method", "path"), GUARDED)
def test_admin_api_is_403_for_a_normal_user(method: str, path: str) -> None:
    with _client(TEST_USER) as c:
        resp = c.request(method.upper(), path)
    assert resp.status_code == 403 and resp.json()["detail"] == "Administrator access required"


def test_admin_api_allows_an_admin(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(auth_service, "list_admin_users", lambda conn: [])
    with _client(ADMIN) as c:
        me = c.get("/api/admin/me")
        assert (
            me.status_code == 200
            and me.json()["email"] == "admin@example.org"
            and me.json()["is_admin"] is True
        )
        assert c.get("/api/admin/users").status_code == 200


def test_me_exposes_is_admin_flag() -> None:
    with _client(TEST_USER) as c:
        assert c.get("/api/auth/me").json()["is_admin"] is False
    with _client(ADMIN) as c:
        assert c.get("/api/auth/me").json()["is_admin"] is True


# --- pages -----------------------------------------------------------------------


def test_admin_login_page_is_public() -> None:
    with _client(None) as c:
        resp = c.get("/admin/login")
    assert (
        resp.status_code == 200
        and "Admin sign in" in resp.text
        and "text/html" in resp.headers["content-type"]
    )


def test_admin_users_page_redirects_anonymous_and_normal_users() -> None:
    with _client(None) as c:
        resp = c.get("/admin/users", follow_redirects=False)
        assert resp.status_code == 302 and resp.headers["location"] == "/admin/login"
        assert c.get("/admin", follow_redirects=False).headers["location"] == "/admin/users"
    with _client(TEST_USER) as c:
        resp = c.get("/admin/users", follow_redirects=False)
        assert resp.status_code == 302 and resp.headers["location"] == "/admin/login?denied=1"


def test_admin_users_page_is_served_to_admins() -> None:
    with _client(ADMIN) as c:
        resp = c.get("/admin/users")
    assert resp.status_code == 200 and "Chatbot Management" in resp.text and "/api/admin/users" in resp.text


def test_admin_page_html_is_not_reachable_through_the_static_mount() -> None:
    """The management HTML lives in app/pages, not under the static root, so the guard cannot be bypassed."""
    from app.api.routes.admin import PAGES

    assert "/app/pages/" in str(PAGES) and "/frontend/" not in str(PAGES)
    assert (PAGES / "users.html").is_file() and (PAGES / "login.html").is_file()


# --- sign-in return path -------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, "/"),
        ("", "/"),
        ("/admin/login", "/admin/login"),
        ("/", "/"),
        ("//evil.example/", "/"),
        ("https://evil.example/", "/"),
        ("/\\evil.example", "/"),
        ("admin/login", "/"),
    ],
)
def test_safe_next_only_accepts_site_relative_paths(value: str | None, expected: str) -> None:
    assert safe_next(value) == expected


def test_entra_login_carries_next_in_the_flow() -> None:
    from tests.test_auth import ENTRA, FakeEntra

    fake = FakeEntra()
    app = create_app(replace(ENTRA, app_base_url="https://chat.example.org"), entra_client=fake)
    app.dependency_overrides[get_db] = lambda: object()
    with TestClient(app) as c:
        resp = c.get("/api/auth/entra/login", params={"next": "/admin/login"}, follow_redirects=False)
        assert resp.status_code == 302
        assert '"next": "/admin/login"' in resp.headers["set-cookie"].replace("\\054", ",").replace(
            '\\"', '"'
        )
        evil = c.get(
            "/api/auth/entra/login", params={"next": "https://evil.example/"}, follow_redirects=False
        )
        assert '"next": "/"' in evil.headers["set-cookie"].replace('\\"', '"')


# --- user management (view / disable / enable) without a database ------------------------


def _user_row(**over: object) -> dict[str, object]:
    from datetime import UTC, datetime
    from uuid import UUID

    now = datetime.now(UTC)
    row: dict[str, object] = {
        "id": UUID(UID),
        "email": "person@example.org",
        "name": "Some Person",
        "first_name": "Some",
        "last_name": "Person",
        "department": "UNDRR",
        "unit": None,
        "role_type": None,
        "password_hash": "$argon2id$secret",
        "entra_oid": "oid-secret",
        "is_admin": False,
        "disabled_at": None,
        "created_at": now,
        "updated_at": now,
    }
    row.update(over)
    return row


def test_user_detail_shows_safe_fields_only(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.db import auth as auth_db

    monkeypatch.setattr(auth_db, "get_user_by_id", lambda conn, uid: _user_row())
    monkeypatch.setattr(
        auth_db,
        "user_activity",
        lambda conn, uid: {
            "chat_sessions": 3,
            "last_chat_at": None,
            "signed_in_sessions": 1,
            "last_seen_at": None,
        },
    )
    with _client(ADMIN) as c:
        resp = c.get(f"/api/admin/users/{UID}")
    assert resp.status_code == 200
    body = resp.json()
    assert (
        body["email"] == "person@example.org"
        and body["has_password"] is True
        and body["entra_linked"] is True
    )
    assert body["disabled"] is False and body["chat_sessions"] == 3 and body["signed_in_sessions"] == 1
    for secret in ("argon2", "oid-secret", "password_hash", "entra_oid", "token"):
        assert secret not in resp.text


def test_user_detail_unknown_or_malformed_id_is_404(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.db import auth as auth_db

    monkeypatch.setattr(auth_db, "get_user_by_id", lambda conn, uid: None)
    monkeypatch.setattr(auth_db, "get_user_by_id_for_update", lambda conn, uid: None)
    with _client(ADMIN) as c:
        assert c.get(f"/api/admin/users/{UID}").status_code == 404
        assert c.get("/api/admin/users/not-a-uuid").status_code == 404
        assert c.post("/api/admin/users/not-a-uuid/disable").status_code == 404
        assert c.post(f"/api/admin/users/{UID}/enable").status_code == 404


def test_self_disable_is_refused_before_any_database_work(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.db import auth as auth_db

    monkeypatch.setattr(
        auth_db, "get_user_by_id_for_update", lambda conn, uid: pytest.fail("row read despite self-disable")
    )
    with _client(ADMIN) as c:
        resp = c.post(f"/api/admin/users/{ADMIN.id}/disable")
    assert resp.status_code == 409 and resp.json()["detail"] == "You cannot disable your own account"


def test_last_enabled_admin_cannot_be_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.db import auth as auth_db

    monkeypatch.setattr(auth_db, "get_user_by_id_for_update", lambda conn, uid: _user_row(is_admin=True))
    monkeypatch.setattr(auth_db, "lock_enabled_admins", lambda conn: 1)
    monkeypatch.setattr(auth_db, "set_disabled", lambda conn, uid, d: pytest.fail("must not disable"))
    with _client(ADMIN) as c:
        resp = c.post(f"/api/admin/users/{UID}/disable")
    assert resp.status_code == 409 and "last enabled administrator" in resp.json()["detail"]


def test_disable_sets_status_and_ends_sessions_and_is_idempotent(monkeypatch: pytest.MonkeyPatch) -> None:
    from datetime import UTC, datetime

    from app.db import auth as auth_db

    events: list[str] = []
    state = _user_row()

    def set_disabled(conn: object, uid: object, disabled: bool) -> dict[str, object]:
        events.append(f"set {disabled}")
        state["disabled_at"] = datetime.now(UTC) if disabled else None
        return state

    def end_sessions(conn: object, uid: object) -> int:
        events.append("sessions")
        return 2

    monkeypatch.setattr(auth_db, "get_user_by_id_for_update", lambda conn, uid: state)
    monkeypatch.setattr(auth_db, "lock_enabled_admins", lambda conn: 2)
    monkeypatch.setattr(auth_db, "set_disabled", set_disabled)
    monkeypatch.setattr(auth_db, "delete_user_sessions", end_sessions)
    with _client(ADMIN) as c:
        first = c.post(f"/api/admin/users/{UID}/disable")
        assert first.status_code == 200 and first.json()["disabled"] is True and first.json()["disabled_at"]
        assert events == ["set True", "sessions"]
        again = c.post(f"/api/admin/users/{UID}/disable")  # no-op, no second session purge
        assert (
            again.status_code == 200
            and again.json()["disabled"] is True
            and events == ["set True", "sessions"]
        )
        enabled = c.post(f"/api/admin/users/{UID}/enable")
        assert enabled.status_code == 200 and enabled.json()["disabled"] is False
        assert events == ["set True", "sessions", "set False"]  # enable restores no session
        assert c.post(f"/api/admin/users/{UID}/enable").status_code == 200 and events[-1] == "set False"
        assert events.count("set False") == 1
