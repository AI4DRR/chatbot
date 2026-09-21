"""Real sessions and passwords against PostgreSQL (``TEST_DATABASE_URL``): the security-facing flows.

register → cookie → me → sessions/chat scoped to the owner → cross-user denied → logout invalidates.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from app.chat.stub import stub_pipeline
from app.config import Settings
from app.db.connection import connect
from app.db.migrate import apply_migrations
from app.errors import ConflictError, ForbiddenError
from app.integrations.mail import Mailer
from app.main import create_app
from app.schemas.auth import AuthUser

pytestmark = pytest.mark.db


@pytest.fixture(scope="module")
def db_url() -> str:
    url = os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL not set")
    apply_migrations(url)
    return url


@pytest.fixture
def emails(db_url: str) -> Iterator[list[str]]:
    created: list[str] = []
    yield created
    with connect(db_url) as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM invitations WHERE lower(email) = ANY(%s)", ([e.lower() for e in created],))
        cur.execute("DELETE FROM users WHERE lower(email) = ANY(%s)", ([e.lower() for e in created],))


def _client(db_url: str, **extra: object) -> TestClient:
    settings = Settings(
        database_url=db_url,
        log_level="WARNING",
        frontend_dir="none",
        local_auth_enabled=True,
        session_cookie_secure=False,  # TestClient speaks plain http
        session_ttl_hours=1,
    )
    if extra:
        from dataclasses import replace

        settings = replace(settings, **extra)  # type: ignore[arg-type]
    return TestClient(create_app(settings, chat_pipeline=stub_pipeline), raise_server_exceptions=False)


def _create_account(db_url: str, email: str, password: str = "correct horse battery") -> None:
    """Create a local account the way the invitation flow does (there is no public registration)."""
    from app.db import auth as auth_db
    from app.services import auth as auth_service

    with connect(db_url) as conn:
        auth_db.create_local_user(
            conn, email, auth_service.hash_password(password), "Ada", "Lovelace", "UNDRR"
        )


def _register(c: TestClient, email: str, password: str = "correct horse battery") -> dict[str, object]:
    """Create the account directly, then sign the client in with it."""
    _create_account(os.environ["TEST_DATABASE_URL"], email, password)
    resp = c.post("/api/auth/local/login", json={"email": email, "password": password})
    assert resp.status_code == 200, resp.text
    body: dict[str, object] = resp.json()
    return body


def test_register_login_me_logout(db_url: str, emails: list[str]) -> None:
    email = f"auth-{uuid4().hex[:10]}@example.org"
    emails.append(email)
    with _client(db_url) as c:
        user = _register(c, email)
        assert user["email"] == email and user["name"] == "Ada Lovelace" and user["has_password"] is True
        assert "ai4drr_session" in c.cookies
        # session cookie works
        me = c.get("/api/auth/me")
        assert me.status_code == 200 and me.json()["id"] == user["id"]
        # public registration is closed
        gone = c.post(
            "/api/auth/local/register",
            json={
                "email": email,
                "password": "x" * 12,
                "confirm_password": "x" * 12,
                "first_name": "A",
                "last_name": "B",
            },
        )
        assert gone.status_code == 404
        # logout invalidates server-side: the old cookie no longer works even if replayed
        old_cookie = c.cookies["ai4drr_session"]
        assert c.post("/api/auth/logout").status_code == 200
        assert c.get("/api/auth/me").status_code == 401
        c.cookies.set("ai4drr_session", old_cookie)
        assert c.get("/api/auth/me").status_code == 401
        c.cookies.delete("ai4drr_session")
        # login with wrong then right password
        bad = c.post("/api/auth/local/login", json={"email": email, "password": "wrong password"})
        assert bad.status_code == 401 and bad.json()["detail"] == "Invalid email or password"
        good = c.post(
            "/api/auth/local/login", json={"email": email.upper(), "password": "correct horse battery"}
        )
        assert good.status_code == 200 and c.get("/api/auth/me").status_code == 200
        # unknown email gets the same generic message (no enumeration)
        unknown = c.post(
            "/api/auth/local/login",
            json={"email": f"nobody-{uuid4().hex[:6]}@example.org", "password": "x" * 12},
        )
        assert unknown.status_code == 401 and unknown.json()["detail"] == "Invalid email or password"


def test_cookie_attributes(db_url: str, emails: list[str]) -> None:
    email = f"auth-{uuid4().hex[:10]}@example.org"
    emails.append(email)
    _create_account(db_url, email, "x" * 12)
    with _client(db_url) as c:
        resp = c.post("/api/auth/local/login", json={"email": email, "password": "x" * 12})
        cookie = resp.headers["set-cookie"].lower()
        assert (
            "httponly" in cookie
            and "samesite=lax" in cookie
            and "path=/" in cookie
            and "max-age=3600" in cookie
        )
        assert "secure" not in cookie  # session_cookie_secure=False for the http test client
    with _client(db_url, session_cookie_secure=True) as c:
        resp = c.post("/api/auth/local/login", json={"email": email, "password": "x" * 12})
        assert "secure" in resp.headers["set-cookie"].lower()


def test_ownership_is_enforced_across_users(db_url: str, emails: list[str]) -> None:
    a, b = (f"auth-{uuid4().hex[:10]}@example.org" for _ in range(2))
    emails.extend([a, b])
    with _client(db_url) as ca, _client(db_url) as cb:
        _register(ca, a)
        _register(cb, b)
        # A opens a session and chats (stub pipeline)
        session_id = ca.post("/api/login").json()["session_id"]
        assert ca.post("/api/chat", json={"message": "hello", "session_id": session_id}).status_code == 200
        assert [s["id"] for s in ca.get("/api/sessions").json()] == [session_id]
        assert ca.get(f"/api/sessions/{session_id}").status_code == 200
        assert ca.post(f"/api/sessions/{session_id}/resume").status_code == 200
        assert ca.patch(f"/api/sessions/{session_id}/title", json={"title": "mine"}).status_code == 200
        # B cannot see, resume, rename or write into A's session — and cannot tell it exists
        assert cb.get("/api/sessions").json() == []
        assert cb.get(f"/api/sessions/{session_id}").status_code == 404
        assert cb.post(f"/api/sessions/{session_id}/resume").status_code == 404
        assert cb.patch(f"/api/sessions/{session_id}/title", json={"title": "stolen"}).status_code == 404
        assert cb.post("/api/chat", json={"message": "hi", "session_id": session_id}).status_code == 404
        assert (
            cb.get("/api/sessions", params={"user_id": ca.get("/api/auth/me").json()["id"]}).status_code
            == 403
        )
        # nothing of B's leaked into A's session
        history = ca.get(f"/api/sessions/{session_id}").json()
        assert [m["content"] for m in history["messages"] if m["role"] == "user"] == ["hello"]
        assert history["title"] == "mine"
    # anonymous: nothing
    with _client(db_url) as anon:
        assert anon.get(f"/api/sessions/{session_id}").status_code == 401
        assert anon.post("/api/chat", json={"message": "hi", "session_id": session_id}).status_code == 401


def test_local_auth_disabled_blocks_login_even_with_an_account(db_url: str, emails: list[str]) -> None:
    email = f"auth-{uuid4().hex[:10]}@example.org"
    emails.append(email)
    with _client(db_url) as c:
        _register(c, email)
    with _client(db_url, local_auth_enabled=False) as c:
        resp = c.post("/api/auth/local/login", json={"email": email, "password": "correct horse battery"})
        assert resp.status_code == 403
        assert c.get("/api/auth/config").json() == {"entra_enabled": False, "local_auth_enabled": False}


def test_entra_identity_links_existing_email_and_survives_relogin(db_url: str, emails: list[str]) -> None:
    from app.services import auth as auth_service

    email = f"auth-{uuid4().hex[:10]}@example.org"
    emails.append(email)
    with _client(db_url) as c:
        local = _register(c, email)
    ident = auth_service.EntraIdentity(
        oid=f"oid-{uuid4().hex[:8]}", email=email.upper(), name="Ada L", first_name="Ada", last_name="L"
    )
    with connect(db_url) as conn:
        linked = auth_service.sign_in_entra(conn, ident)
        assert linked.id == local["id"] and linked.entra_linked is True and linked.has_password is True
        assert linked.first_name == "Ada"  # existing values kept
        again = auth_service.sign_in_entra(conn, ident)
        assert again.id == local["id"]
        fresh = auth_service.sign_in_entra(
            conn,
            auth_service.EntraIdentity(
                oid=f"oid-{uuid4().hex[:8]}", email=f"new-{email}", name="N", first_name="N", last_name="E"
            ),
        )
        emails.append(f"new-{email}")
        assert fresh.id != local["id"] and fresh.has_password is False and fresh.entra_linked is True


# --- administrators (real database) -------------------------------------------------


def test_migration_is_idempotent(db_url: str) -> None:
    apply_migrations(db_url)
    apply_migrations(db_url)  # second run: every statement is IF NOT EXISTS
    with connect(db_url) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT column_default FROM information_schema.columns "
            "WHERE table_name = 'users' AND column_name = 'is_admin'"
        )
        row = cur.fetchone()
        cur.execute("SELECT to_regclass('password_resets') AS t")
        table = cur.fetchone()
    assert row is not None and row["column_default"] == "false"
    assert table is not None and table["t"] == "password_resets"


def test_admin_promotion_and_access_matrix(db_url: str, emails: list[str]) -> None:
    from app.db import auth as auth_db
    from app.db.admin_ops import main as admin_ops

    normal, admin = (f"auth-{uuid4().hex[:10]}@example.org" for _ in range(2))
    emails.extend([normal, admin])
    with _client(db_url) as cn, _client(db_url) as ca:
        _register(cn, normal)
        _register(ca, admin)
        # new accounts are never admins; the management surface is closed to them
        assert ca.get("/api/auth/me").json()["is_admin"] is False
        assert ca.get("/api/admin/me").status_code == 403
        assert ca.get("/admin/users", follow_redirects=False).headers["location"] == "/admin/login?denied=1"
        # promotion is an operational step against the database (the CLI or plain SQL)
        with connect(db_url) as conn:
            assert auth_db.set_admin(conn, admin.upper(), True) is True
            assert auth_db.set_admin(conn, f"nobody-{uuid4().hex[:6]}@example.org", True) is False
        # the flag takes effect on the existing session (no re-login needed)
        assert ca.get("/api/auth/me").json()["is_admin"] is True
        assert ca.get("/api/admin/me").status_code == 200
        assert ca.get("/admin/users").status_code == 200
        users = ca.get("/api/admin/users").json()
        by_email = {u["email"]: u for u in users}
        assert by_email[admin]["is_admin"] is True and by_email[normal]["is_admin"] is False
        assert by_email[normal]["has_password"] is True and "password_hash" not in by_email[normal]
        # the normal user is still a normal user, and the chatbot works for both as before
        assert cn.get("/api/admin/users").status_code == 403
        assert cn.get("/admin/users", follow_redirects=False).status_code == 302
        sid = cn.post("/api/login").json()["session_id"]
        assert cn.post("/api/chat", json={"message": "hi", "session_id": sid}).status_code == 200
        assert (
            ca.get(f"/api/sessions/{sid}").status_code == 404
        )  # admin flag grants no access to others' chats
        # revoke via the CLI (uses DATABASE_URL from the environment)
        import os

        os.environ["DATABASE_URL"] = db_url
        try:
            assert admin_ops(["revoke", admin]) == 0
            assert admin_ops(["grant", f"nobody-{uuid4().hex[:6]}@example.org"]) == 1
            assert admin_ops(["list"]) == 0
        finally:
            os.environ.pop("DATABASE_URL", None)
        assert ca.get("/api/admin/me").status_code == 403


# --- invitation lifecycle (real database) ---------------------------------------------


def _admin_client(db_url: str, mailer: Mailer) -> tuple[TestClient, str]:
    """A signed-in administrator client using ``mailer``; returns the client and the admin's email."""
    from app.db import auth as auth_db

    email = f"auth-admin-{uuid4().hex[:8]}@example.org"
    _create_account(db_url, email)
    with connect(db_url) as conn:
        auth_db.set_admin(conn, email, True)
    settings = Settings(
        database_url=db_url,
        log_level="WARNING",
        frontend_dir="none",
        local_auth_enabled=True,
        session_cookie_secure=False,
        app_base_url="http://testserver",
        invitation_ttl_hours=24,
    )
    c = TestClient(
        create_app(settings, chat_pipeline=stub_pipeline, mailer=mailer), raise_server_exceptions=False
    )
    assert (
        c.post(
            "/api/auth/local/login", json={"email": email, "password": "correct horse battery"}
        ).status_code
        == 200
    )
    return c, email


def _token_from(link: str) -> str:
    return link.split("#token=", 1)[1]


def test_invitation_lifecycle_end_to_end(db_url: str, emails: list[str]) -> None:
    from tests.conftest import MemoryMailer

    mailer = MemoryMailer()
    admin, admin_email = _admin_client(db_url, mailer)
    emails.append(admin_email)
    invitee = f"Invitee-{uuid4().hex[:8]}@Example.org"
    emails.append(invitee)
    with admin:
        # invite
        resp = admin.post("/api/admin/invitations", json={"email": invitee})
        assert resp.status_code == 201, resp.text
        inv = resp.json()
        assert inv["status"] == "pending" and inv["invited_by_email"] == admin_email
        expires = datetime.fromisoformat(inv["expires_at"].replace("Z", "+00:00"))
        assert timedelta(hours=23, minutes=55) < expires - datetime.now(UTC) <= timedelta(hours=24)
        link = mailer.last_link()
        token = _token_from(link)
        # hashed at rest, never the raw token
        with connect(db_url) as conn, conn.cursor() as cur:
            cur.execute("SELECT token_hash FROM invitations WHERE id = %s", (inv["id"],))
            row = cur.fetchone()
            assert row is not None and row["token_hash"] != token and len(row["token_hash"]) == 64
            cur.execute("SELECT count(*) AS n FROM invitations WHERE token_hash = %s", (token,))
            assert cur.fetchone()["n"] == 0  # type: ignore[index]
        # listed as pending; duplicates refused cleanly (case-insensitive)
        assert [i["id"] for i in admin.get("/api/admin/invitations").json()] == [inv["id"]]
        dup = admin.post("/api/admin/invitations", json={"email": invitee.lower()})
        assert dup.status_code == 409 and "resend" in dup.json()["detail"]
        # inviting an existing account is refused
        existing = admin.post("/api/admin/invitations", json={"email": admin_email.upper()})
        assert existing.status_code == 409 and "already exists" in existing.json()["detail"]
        # resend: old token dead, new token + fresh expiry, second mail
        resent = admin.post(f"/api/admin/invitations/{inv['id']}/resend")
        assert resent.status_code == 200 and resent.json()["id"] != inv["id"]
        new_token = _token_from(mailer.last_link())
        assert new_token != token and len(mailer.sent) == 2
        pending = admin.get("/api/admin/invitations").json()
        assert [i["id"] for i in pending] == [resent.json()["id"]]

    # invitee: old token rejected, new token valid → account created, invitation consumed, session opened
    with _client(db_url) as invitee_client:
        assert invitee_client.post("/api/auth/setup/check", json={"token": token}).status_code == 410
        check = invitee_client.post("/api/auth/setup/check", json={"token": new_token})
        # EmailStr normalises the domain part on input; the local part is kept as typed
        assert check.status_code == 200 and check.json()["email"].lower() == invitee.lower()
        bad_pw = invitee_client.post(
            "/api/auth/setup",
            json={
                "token": new_token,
                "password": "short",
                "confirm_password": "short",
                "first_name": "F",
                "last_name": "L",
            },
        )
        assert bad_pw.status_code == 400  # nothing consumed
        assert invitee_client.post("/api/auth/setup/check", json={"token": new_token}).status_code == 200
        done = invitee_client.post(
            "/api/auth/setup",
            json={
                "token": new_token,
                "password": "correct horse battery",
                "confirm_password": "correct horse battery",
                "first_name": "Fatima",
                "last_name": "Haddad",
                "department": "UNDRR",
            },
        )
        assert done.status_code == 201, done.text
        user = done.json()
        assert user["email"].lower() == invitee.lower() and user["name"] == "Fatima Haddad"
        assert user["has_password"] is True
        assert user["is_admin"] is False
        assert invitee_client.get("/api/auth/me").status_code == 200  # signed in by the setup
        # token cannot be reused
        again = invitee_client.post(
            "/api/auth/setup",
            json={
                "token": new_token,
                "password": "x" * 12,
                "confirm_password": "x" * 12,
                "first_name": "A",
                "last_name": "B",
            },
        )
        assert again.status_code == 410 and "consumed" in again.json()["detail"]
        assert invitee_client.post("/api/auth/setup/check", json={"token": new_token}).status_code == 410
    # local login works afterwards (case-insensitive e-mail) and the chatbot is usable
    with _client(db_url) as c:
        assert (
            c.post(
                "/api/auth/local/login", json={"email": invitee.lower(), "password": "correct horse battery"}
            ).status_code
            == 200
        )
        sid = c.post("/api/login").json()["session_id"]
        assert c.post("/api/chat", json={"message": "hello", "session_id": sid}).status_code == 200
    # the consumed invitation left the pending list; the account exists exactly once
    admin2, admin2_email = _admin_client(db_url, mailer)
    emails.append(admin2_email)
    with admin2:
        assert admin2.get("/api/admin/invitations").json() == []
        assert (
            sum(1 for u in admin2.get("/api/admin/users").json() if u["email"].lower() == invitee.lower())
            == 1
        )


def test_invitation_revoke_expiry_and_mail_failure(db_url: str, emails: list[str]) -> None:
    from tests.conftest import MemoryMailer

    mailer = MemoryMailer()
    admin, admin_email = _admin_client(db_url, mailer)
    emails.append(admin_email)
    a, b, c_mail = (f"inv-{uuid4().hex[:8]}@example.org" for _ in range(3))
    emails.extend([a, b, c_mail])
    with admin:
        # revoke: token dead, gone from the list, can be re-invited
        inv_a = admin.post("/api/admin/invitations", json={"email": a}).json()
        token_a = _token_from(mailer.last_link())
        assert admin.post(f"/api/admin/invitations/{inv_a['id']}/revoke").json()["status"] == "revoked"
        assert admin.get("/api/admin/invitations").json() == []
        assert admin.post(f"/api/admin/invitations/{inv_a['id']}/revoke").status_code == 404
        assert admin.post(f"/api/admin/invitations/{inv_a['id']}/resend").status_code == 404
        with _client(db_url) as x:
            resp = x.post("/api/auth/setup/check", json={"token": token_a})
            assert resp.status_code == 410 and "revoked" in resp.json()["detail"]
        assert admin.post("/api/admin/invitations", json={"email": a}).status_code == 201
        # expiry: force the clock, the link is refused and the row shows as expired but can be re-sent
        inv_b = admin.post("/api/admin/invitations", json={"email": b}).json()
        token_b = _token_from(mailer.last_link())
        with connect(db_url) as conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE invitations SET expires_at = CURRENT_TIMESTAMP - interval '1 hour' WHERE id = %s",
                (inv_b["id"],),
            )
        with _client(db_url) as x:
            resp = x.post("/api/auth/setup/check", json={"token": token_b})
            assert resp.status_code == 410 and "expired" in resp.json()["detail"]
        listed = {i["id"]: i["status"] for i in admin.get("/api/admin/invitations").json()}
        assert listed[inv_b["id"]] == "expired"
        assert (
            admin.post("/api/admin/invitations", json={"email": b}).status_code == 201
        )  # expired does not block
        # garbage / unknown tokens
        with _client(db_url) as x:
            assert x.post("/api/auth/setup/check", json={"token": "n" * 40}).status_code == 410
            assert x.post("/api/auth/setup/check", json={"token": "short"}).status_code == 422
        # mail failure: no invitation row survives, admin gets 502
        mailer.fail = True
        before = admin.get("/api/admin/invitations").json()
        failed = admin.post("/api/admin/invitations", json={"email": c_mail})
        assert failed.status_code == 502
        assert admin.get("/api/admin/invitations").json() == before
        with connect(db_url) as conn, conn.cursor() as cur:
            cur.execute("SELECT count(*) AS n FROM invitations WHERE lower(email) = lower(%s)", (c_mail,))
            assert cur.fetchone()["n"] == 0  # type: ignore[index]
        mailer.fail = False
        assert admin.post("/api/admin/invitations", json={"email": c_mail}).status_code == 201
    with connect(db_url) as conn, conn.cursor() as cur:
        cur.execute(
            "DELETE FROM invitations WHERE lower(email) = ANY(%s)", ([e.lower() for e in (a, b, c_mail)],)
        )


# --- first-administrator bootstrap (make admin → app.db.admin_ops bootstrap) ----------------


def _bootstrap(db_url: str, email: str, capsys: pytest.CaptureFixture[str]) -> tuple[int, str]:
    """Run the CLI the way the container does (DATABASE_URL/APP_BASE_URL from the environment)."""
    from app.db.admin_ops import main as admin_ops

    os.environ["DATABASE_URL"] = db_url
    os.environ["APP_BASE_URL"] = "http://testserver"
    os.environ["LOCAL_AUTH_ENABLED"] = "true"  # bootstrap links create local passwords
    try:
        code = admin_ops(["bootstrap", email])
    finally:
        for k in ("DATABASE_URL", "APP_BASE_URL", "LOCAL_AUTH_ENABLED"):
            os.environ.pop(k, None)
    return code, capsys.readouterr().out


def test_admin_bootstrap_end_to_end(
    db_url: str, emails: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    first = f"Auth-Boot-{uuid4().hex[:8]}@Example.org"
    emails.append(first)
    # unknown address → a one-time setup link is printed; only the hash is stored, flagged grants_admin
    code, out = _bootstrap(db_url, first, capsys)
    assert code == 0 and "One-time administrator setup link" in out
    token_a = _token_from(out.split("http://testserver", 1)[1].split()[0])
    with connect(db_url) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT token_hash, grants_admin, invited_by FROM invitations WHERE lower(email) = lower(%s)",
            (first,),
        )
        rows = cur.fetchall()
    assert len(rows) == 1 and rows[0]["grants_admin"] is True and rows[0]["invited_by"] is None
    assert rows[0]["token_hash"] != token_a and token_a not in out.replace(token_a, "", 1)
    # running it again replaces the link: the old one stops working
    code, out = _bootstrap(db_url, first, capsys)
    assert code == 0
    token_b = _token_from(out.split("http://testserver", 1)[1].split()[0])
    assert token_b != token_a
    with _client(db_url) as c:
        resp = c.post("/api/auth/setup/check", json={"token": token_a})
        assert resp.status_code == 410 and "revoked" in resp.json()["detail"]
        check = c.post("/api/auth/setup/check", json={"token": token_b})
        assert check.status_code == 200 and check.json()["email"].lower() == first.lower()
        assert check.json()["grants_admin"] is True
        # the normal account-setup page creates the account — as an administrator
        done = c.post(
            "/api/auth/setup",
            json={
                "token": token_b,
                "password": "correct horse battery",
                "confirm_password": "correct horse battery",
                "first_name": "First",
                "last_name": "Admin",
            },
        )
        assert done.status_code == 201, done.text
        assert done.json()["is_admin"] is True and done.json()["has_password"] is True
        assert c.get("/api/admin/me").status_code == 200
        assert c.get("/api/admin/users").status_code == 200
        # public registration is still closed
        assert (
            c.post(
                "/api/auth/local/register",
                json={"email": "x@example.org", "password": "p" * 12, "confirm_password": "p" * 12},
            ).status_code
            == 404
        )
    # third run: the account exists and is an administrator → harmless
    code, out = _bootstrap(db_url, first, capsys)
    assert code == 0 and "already an administrator" in out and "account-setup" not in out
    with connect(db_url) as conn, conn.cursor() as cur:
        cur.execute("SELECT count(*) AS n FROM users WHERE lower(email) = lower(%s)", (first,))
        assert cur.fetchone()["n"] == 1  # type: ignore[index]
    # an existing non-admin is promoted in place (no invitation, no new account)
    second = f"auth-boot2-{uuid4().hex[:8]}@example.org"
    emails.append(second)
    _create_account(db_url, second)
    code, out = _bootstrap(db_url, second, capsys)
    assert code == 0 and "promoted" in out
    from tests.conftest import MemoryMailer

    mailer = MemoryMailer()
    settings = Settings(
        database_url=db_url,
        log_level="WARNING",
        frontend_dir="none",
        local_auth_enabled=True,
        session_cookie_secure=False,
        app_base_url="http://testserver",
    )
    third = f"auth-boot3-{uuid4().hex[:8]}@example.org"
    emails.append(third)
    with TestClient(create_app(settings, chat_pipeline=stub_pipeline, mailer=mailer)) as c:
        login = c.post("/api/auth/local/login", json={"email": second, "password": "correct horse battery"})
        assert login.status_code == 200 and login.json()["is_admin"] is True
        assert c.get("/api/admin/me").status_code == 200
        # a user this administrator invites the normal way is NOT an administrator
        inv = c.post("/api/admin/invitations", json={"email": third})
        assert inv.status_code == 201 and inv.json()["grants_admin"] is False
        with _client(db_url) as invitee:
            done = invitee.post(
                "/api/auth/setup",
                json={
                    "token": _token_from(mailer.last_link()),
                    "password": "correct horse battery",
                    "confirm_password": "correct horse battery",
                    "first_name": "Normal",
                    "last_name": "User",
                },
            )
            assert done.status_code == 201 and done.json()["is_admin"] is False
            assert invitee.get("/api/admin/me").status_code == 403
    with connect(db_url) as conn, conn.cursor() as cur:
        cur.execute("SELECT count(*) AS n FROM invitations WHERE lower(email) = lower(%s)", (second,))
        assert cur.fetchone()["n"] == 0  # type: ignore[index]  # promotion issued no invitation


# --- forgot / reset password (real database) ------------------------------------------------


def _mail_client(db_url: str, mailer: Mailer) -> TestClient:
    settings = Settings(
        database_url=db_url,
        log_level="WARNING",
        frontend_dir="none",
        local_auth_enabled=True,
        session_cookie_secure=False,
        app_base_url="http://testserver",
        password_reset_ttl_minutes=60,
    )
    return TestClient(
        create_app(settings, chat_pipeline=stub_pipeline, mailer=mailer), raise_server_exceptions=False
    )


def _login(c: TestClient, email: str, password: str) -> int:
    return c.post("/api/auth/local/login", json={"email": email, "password": password}).status_code


def test_password_reset_end_to_end(db_url: str, emails: list[str], caplog: pytest.LogCaptureFixture) -> None:
    from app.db import auth as auth_db
    from app.services import auth as auth_service
    from tests.conftest import MemoryMailer

    mailer = MemoryMailer()
    email = f"Auth-Reset-{uuid4().hex[:8]}@Example.org"
    emails.append(email)
    old_pw, new_pw = "correct horse battery", "new horse battery staple"
    _create_account(db_url, email, old_pw)
    with connect(db_url) as conn:
        auth_db.set_admin(conn, email, True)  # an administrator uses the very same mechanism
        auth_db.upsert_entra_user(conn, f"oid-{uuid4().hex[:8]}", email, None, None, None)  # Microsoft-linked
    generic = {"detail": "If an account with that email can reset its password, we've sent instructions."}
    with (
        _mail_client(db_url, mailer) as s1,
        _mail_client(db_url, mailer) as s2,
        _mail_client(db_url, mailer) as c,
    ):
        assert _login(s1, email, old_pw) == 200 and _login(s2, email, old_pw) == 200  # two live sessions
        # unknown e-mail: same answer, no mail, no row
        assert (
            c.post("/api/auth/local/forgot", json={"email": f"nobody-{uuid4().hex[:6]}@example.org"}).json()
            == generic
        )
        assert mailer.sent == []
        # known local account, e-mail in a different case: same answer, one mail, hash only
        with caplog.at_level("DEBUG"):
            resp = c.post("/api/auth/local/forgot", json={"email": email.upper()})
        assert resp.status_code == 202 and resp.json() == generic
        assert len(mailer.sent) == 1 and mailer.sent[0].to.lower() == email.lower()
        token_a = _token_from(mailer.last_link())
        assert token_a not in caplog.text
        with connect(db_url) as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT r.token_hash FROM password_resets r JOIN users u ON u.id = r.user_id "
                "WHERE lower(u.email) = lower(%s)",
                (email,),
            )
            hashes = [r["token_hash"] for r in cur.fetchall()]
        assert hashes and token_a not in hashes and all(len(h) == 64 for h in hashes)
        # a second request replaces the first link
        assert c.post("/api/auth/local/forgot", json={"email": email}).status_code == 202
        token_b = _token_from(mailer.last_link())
        assert token_b != token_a and len(mailer.sent) == 2
        first = c.post("/api/auth/reset/check", json={"token": token_a})
        assert first.status_code == 410 and "replaced" in first.json()["detail"]
        second = c.post("/api/auth/reset/check", json={"token": token_b})
        assert second.status_code == 200 and second.json()["email"].lower() == email.lower()
        # a bad password consumes nothing
        bad = c.post(
            "/api/auth/reset", json={"token": token_b, "password": "x" * 12, "confirm_password": "y" * 12}
        )
        assert bad.status_code == 400
        assert c.post("/api/auth/reset/check", json={"token": token_b}).status_code == 200
        assert s1.get("/api/auth/me").status_code == 200  # sessions untouched so far
        # the reset itself
        done = c.post(
            "/api/auth/reset", json={"token": token_b, "password": new_pw, "confirm_password": new_pw}
        )
        assert done.status_code == 204, done.text
        assert "set-cookie" not in done.headers
        # every earlier session is gone, the old password is dead, the new one works
        assert s1.get("/api/auth/me").status_code == 401 and s2.get("/api/auth/me").status_code == 401
        assert _login(c, email, old_pw) == 401
        assert _login(c, email, new_pw) == 200
        me = c.get("/api/auth/me").json()
        assert me["is_admin"] is True and me["entra_linked"] is True and me["has_password"] is True
        # one-time use
        again = c.post(
            "/api/auth/reset", json={"token": token_b, "password": new_pw, "confirm_password": new_pw}
        )
        assert again.status_code == 410 and "used" in again.json()["detail"]
        # expired link
        assert c.post("/api/auth/local/forgot", json={"email": email}).status_code == 202
        token_c = _token_from(mailer.last_link())
        with connect(db_url) as conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE password_resets SET expires_at = CURRENT_TIMESTAMP - interval '1 minute' "
                "WHERE token_hash = %s",
                (auth_service.token_hash(token_c),),
            )
        expired = c.post("/api/auth/reset/check", json={"token": token_c})
        assert expired.status_code == 410 and "expired" in expired.json()["detail"]
        # garbage token
        assert c.post("/api/auth/reset/check", json={"token": "n" * 40}).status_code == 410
    # mail failure: same generic answer, no row left behind
    broken = MemoryMailer(fail=True)
    with _mail_client(db_url, broken) as c:
        assert c.post("/api/auth/local/forgot", json={"email": email}).json() == generic
    with connect(db_url) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT count(*) AS n FROM password_resets r JOIN users u ON u.id = r.user_id "
            "WHERE lower(u.email) = lower(%s) AND r.consumed_at IS NULL AND r.revoked_at IS NULL "
            "AND r.expires_at > CURRENT_TIMESTAMP",
            (email,),
        )
        assert cur.fetchone()["n"] == 0  # type: ignore[index]
    # an Entra-only account (no password) never gets a mail
    entra_only = f"auth-entra-{uuid4().hex[:8]}@example.org"
    emails.append(entra_only)
    with connect(db_url) as conn:
        auth_db.upsert_entra_user(conn, f"oid-{uuid4().hex[:8]}", entra_only, "E O", "E", "O")
    with _mail_client(db_url, mailer) as c:
        before = len(mailer.sent)
        assert c.post("/api/auth/local/forgot", json={"email": entra_only}).json() == generic
        assert len(mailer.sent) == before
        # public registration is still closed
        assert (
            c.post("/api/auth/local/register", json={"email": entra_only, "password": "x" * 12}).status_code
            == 404
        )


# --- user management: disable / enable (real database) --------------------------------------


def test_disable_and_enable_end_to_end(db_url: str, emails: list[str]) -> None:
    from app.db import auth as auth_db
    from app.services import auth as auth_service
    from tests.conftest import MemoryMailer

    mailer = MemoryMailer()
    admin, admin_email = _admin_client(db_url, mailer)
    emails.append(admin_email)
    person = f"auth-dis-{uuid4().hex[:8]}@example.org"
    emails.append(person)
    _create_account(db_url, person)
    pw = "correct horse battery"
    with admin, _mail_client(db_url, mailer) as s1, _mail_client(db_url, mailer) as s2:
        assert _login(s1, person, pw) == 200 and _login(s2, person, pw) == 200
        chat_sid = s1.post("/api/login").json()["session_id"]  # a chat the person owns
        uid = s1.get("/api/auth/me").json()["id"]
        # a reset link issued BEFORE the suspension
        assert s1.post("/api/auth/local/forgot", json={"email": person}).status_code == 202
        pre_token = _token_from(mailer.last_link())
        # view: safe fields, real counts
        detail = admin.get(f"/api/admin/users/{uid}")
        assert detail.status_code == 200
        d = detail.json()
        assert d["email"] == person and d["disabled"] is False and d["has_password"] is True
        assert d["chat_sessions"] == 1 and d["signed_in_sessions"] == 2
        assert (
            "password_hash" not in detail.text and "argon2" not in detail.text and "token" not in detail.text
        )
        # disable → sessions gone immediately, local login refused with a clear message
        resp = admin.post(f"/api/admin/users/{uid}/disable")
        assert resp.status_code == 200 and resp.json()["disabled"] is True
        assert s1.get("/api/auth/me").status_code == 401 and s2.get("/api/auth/me").status_code == 401
        assert s1.get(f"/api/sessions/{chat_sid}").status_code == 401
        wrong = s1.post("/api/auth/local/login", json={"email": person, "password": "not the password"})
        assert wrong.status_code == 401 and wrong.json()["detail"] == "Invalid email or password"
        right = s1.post("/api/auth/local/login", json={"email": person, "password": pw})
        assert right.status_code == 403 and "disabled" in right.json()["detail"]
        assert "set-cookie" not in right.headers
        # password reset is not a way in: earlier link dead, new requests silent
        assert s1.post("/api/auth/reset/check", json={"token": pre_token}).status_code == 410
        sent = len(mailer.sent)
        assert s1.post("/api/auth/local/forgot", json={"email": person}).status_code == 202
        assert len(mailer.sent) == sent
        # Microsoft sign-in for the same account: identity accepted, session refused
        with connect(db_url) as conn, pytest.raises(ForbiddenError):
            auth_service.sign_in_entra(
                conn,
                auth_service.EntraIdentity(
                    oid=f"oid-{uuid4().hex[:8]}", email=person, name=None, first_name=None, last_name=None
                ),
            )
        with connect(db_url) as conn, conn.cursor() as cur:
            cur.execute("SELECT count(*) AS n FROM auth_sessions WHERE user_id = %s", (uid,))
            assert cur.fetchone()["n"] == 0  # type: ignore[index]
        # a session row that somehow survived would still be refused by the lookup
        with connect(db_url) as conn:
            zombie = auth_service.start_session(conn, uid, "local", 1)
        with _mail_client(db_url, mailer) as z:
            z.cookies.set("ai4drr_session", zombie)
            assert z.get("/api/auth/me").status_code == 401
        # listed as disabled; detail shows the timestamp; disabling again is a no-op
        listed = {u["id"]: u for u in admin.get("/api/admin/users").json()}
        assert listed[uid]["disabled"] is True and listed[uid]["disabled_at"]
        assert (
            admin.post(f"/api/admin/users/{uid}/disable").json()["disabled_at"] == listed[uid]["disabled_at"]
        )
        # data preserved: chat, password, name untouched
        with connect(db_url) as conn:
            row = auth_db.get_user_by_id(conn, UUID(uid))
            assert row is not None and row["password_hash"] and row["name"] == "Ada Lovelace"
        # enable → old sessions stay dead, login works, chat ownership intact
        assert admin.post(f"/api/admin/users/{uid}/enable").json()["disabled"] is False
        assert s1.get("/api/auth/me").status_code == 401
        assert _login(s1, person, pw) == 200
        assert s1.get(f"/api/sessions/{chat_sid}").status_code == 200
        assert admin.post(f"/api/admin/users/{uid}/enable").json()["disabled"] is False  # idempotent
        # guards: self, unknown, last administrator
        admin_id = admin.get("/api/admin/me").json()["id"]
        me_resp = admin.post(f"/api/admin/users/{admin_id}/disable")
        assert me_resp.status_code == 409 and "own account" in me_resp.json()["detail"]
        assert admin.post(f"/api/admin/users/{uuid4()}/disable").status_code == 404
        assert admin.get(f"/api/admin/users/{uuid4()}").status_code == 404
        # the person is promoted, then the admin disables them: fine (two enabled admins)…
        with connect(db_url) as conn:
            auth_db.set_admin(conn, person, True)
        assert admin.post(f"/api/admin/users/{uid}/disable").json()["disabled"] is True
        assert admin.post(f"/api/admin/users/{uid}/enable").json()["disabled"] is False
        # …but the service refuses to disable the last enabled administrator (checked in the row lock)
        from app.services import admin_users

        with connect(db_url) as conn, conn.cursor() as cur:
            cur.execute("SELECT count(*) AS n FROM users WHERE is_admin AND disabled_at IS NULL")
            enabled_admins = cur.fetchone()["n"]  # type: ignore[index]
        other_admins = [
            u for u in admin.get("/api/admin/users").json() if u["is_admin"] and not u["disabled"]
        ]
        assert len(other_admins) == enabled_admins
        with connect(db_url) as conn:
            # inside one transaction: disable every other enabled administrator, then the last one must fail;
            # rolled back afterwards so nothing on the shared database changes
            outsider = AuthUser(id=str(uuid4()), email="x@example.org", is_admin=True)
            for u in other_admins:
                if u["id"] != uid:
                    admin_users.disable(conn, u["id"], outsider)
            with pytest.raises(ConflictError, match="last enabled administrator"):
                admin_users.disable(conn, uid, outsider)
            conn.rollback()
        # a normal user is unaffected by all of this (403 on the management API)
        with _mail_client(db_url, mailer) as n:
            assert _login(n, person, pw) == 200
            with connect(db_url) as conn:
                auth_db.set_admin(conn, person, False)
            assert n.post(f"/api/admin/users/{admin_id}/disable").status_code == 403


# --- LOCAL_AUTH_ENABLED toggling never touches account data (real database) ------------------


def test_microsoft_only_mode_keeps_local_accounts_intact(db_url: str, emails: list[str]) -> None:
    from app.db import auth as auth_db
    from app.services import auth as auth_service
    from tests.conftest import MemoryMailer

    mailer = MemoryMailer()
    admin, admin_email = _admin_client(db_url, mailer)
    emails.append(admin_email)
    person = f"auth-flag-{uuid4().hex[:8]}@example.org"
    emails.append(person)
    pw = "correct horse battery"
    _create_account(db_url, person, pw)
    with admin:
        inv = admin.post("/api/admin/invitations", json={"email": f"pending-{person}"})
        assert inv.status_code == 201
        emails.append(f"pending-{person}")
        pending_id = inv.json()["id"]
        setup_token = _token_from(mailer.last_link())
    with connect(db_url) as conn:
        before = auth_db.get_user_by_email(conn, person)
    assert before is not None
    # flag off: local login, reset and invitation setup refused; nothing changes in the database
    off = Settings(
        database_url=db_url,
        log_level="WARNING",
        frontend_dir="none",
        local_auth_enabled=False,
        session_cookie_secure=False,
        app_base_url="http://testserver",
    )
    with TestClient(
        create_app(off, chat_pipeline=stub_pipeline, mailer=mailer), raise_server_exceptions=False
    ) as c:
        assert _login(c, person, pw) == 403
        assert c.post("/api/auth/local/forgot", json={"email": person}).status_code == 403
        assert c.post("/api/auth/setup/check", json={"token": setup_token}).status_code == 403
        assert c.post("/api/admin/invitations", json={"email": "x@example.org"}).status_code in (401, 403)
        # Microsoft sign-in for the same e-mail links the existing local row and works as usual
        with connect(db_url) as conn:
            linked = auth_service.sign_in_entra(
                conn,
                auth_service.EntraIdentity(
                    oid=f"oid-{uuid4().hex[:8]}", email=person, name=None, first_name=None, last_name=None
                ),
            )
        assert linked.id == str(before["id"]) and linked.has_password is True
        # the admin (signed in via the same session model) can still list and revoke, not resend/create
        with connect(db_url) as conn:
            auth_db.set_admin(conn, person, True)
        with connect(db_url) as conn:
            token = auth_service.start_session(conn, linked.id, "entra", 1)
        c.cookies.set("ai4drr_session", token)
        assert c.get("/api/admin/me").status_code == 200
        assert c.post(f"/api/admin/invitations/{pending_id}/resend").status_code == 403
        assert c.get("/api/admin/invitations").status_code == 200
        with connect(db_url) as conn:
            auth_db.set_admin(conn, person, False)
    with connect(db_url) as conn:
        after = auth_db.get_user_by_email(conn, person)
    assert after is not None
    assert after["password_hash"] == before["password_hash"] and after["disabled_at"] is None
    assert after["entra_oid"] is not None  # only the Microsoft link was added, by the sign-in itself
    # flag on again: local login works with the untouched password, the setup link is usable again
    with _mail_client(db_url, mailer) as c:
        assert _login(c, person, pw) == 200
        assert c.post("/api/auth/setup/check", json={"token": setup_token}).status_code == 200
    with connect(db_url) as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM invitations WHERE id = %s", (pending_id,))
