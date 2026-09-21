"""Authentication without a database: contract, feature flag, Entra flow (fake client), CSRF origin check.

Real cookies/sessions/passwords against PostgreSQL are in ``tests/test_db_auth.py``.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_db
from app.config import Settings
from app.errors import AuthenticationError, ValidationError
from app.main import create_app
from app.services import auth as auth_service
from tests.conftest import TEST_USER, sign_in

ENTRA = Settings(
    frontend_dir="none",
    entra_tenant_id="tenant",
    entra_client_id="client",
    entra_client_secret="not-a-real-secret",
)


class FakeEntra:
    """Stands in for MSAL: records the redirect_uri, returns a fixed auth URL and claims."""

    def __init__(self, claims: dict[str, Any] | None = None, fail: bool = False) -> None:
        self.redirect_uris: list[str] = []
        self.finished: list[tuple[dict[str, Any], dict[str, str]]] = []
        self.claims = claims or {
            "oid": "0000-oid",
            "preferred_username": "amina@undrr.example",
            "name": "Amina Mwangi",
            "given_name": "Amina",
            "family_name": "Mwangi",
        }
        self.fail = fail

    def start(self, redirect_uri: str, policy: str = "signin") -> dict[str, Any]:
        self.redirect_uris.append(redirect_uri)
        return {
            "auth_uri": f"https://login.microsoftonline.com/x/authorize?state=abc&p={policy}",
            "state": "abc",
            "code_verifier": "v",
            "policy": policy,
        }

    def logout_url(self, post_logout_redirect_uri: str) -> str | None:
        return (
            "https://login.microsoftonline.com/x/logout?post_logout_redirect_uri=" + post_logout_redirect_uri
        )

    def supports(self, policy: str) -> bool:
        return policy in ("signin", "reset")

    def finish(self, flow: dict[str, Any], auth_response: dict[str, str]) -> dict[str, Any]:
        self.finished.append((flow, auth_response))
        if self.fail:
            raise AuthenticationError("Microsoft sign-in could not be completed")
        return self.claims


# --- config / me / logout --------------------------------------------------------------


def test_auth_config_reports_methods() -> None:
    with TestClient(create_app(Settings(frontend_dir="none"))) as c:
        assert c.get("/api/auth/config").json() == {"entra_enabled": False, "local_auth_enabled": False}
    with TestClient(create_app(replace(ENTRA, local_auth_enabled=True), entra_client=FakeEntra())) as c:
        assert c.get("/api/auth/config").json() == {"entra_enabled": True, "local_auth_enabled": True}


def test_me_requires_session_and_returns_user(anon_client: TestClient, client: TestClient) -> None:
    assert anon_client.get("/api/auth/me").status_code == 401
    body = client.get("/api/auth/me").json()
    assert body["email"] == TEST_USER.email and body["has_password"] is True
    assert "password_hash" not in body and "entra_oid" not in body


def test_logout_without_session_is_idempotent_and_clears_cookie(anon_client: TestClient) -> None:
    resp = anon_client.post("/api/auth/logout")
    assert resp.status_code == 200 and resp.json() == {"signed_out": True, "redirect": None}
    assert 'ai4drr_session=""' in resp.headers.get(
        "set-cookie", ""
    ) or "ai4drr_session=;" in resp.headers.get("set-cookie", "")


# --- protected routes ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("get", "/api/sessions"),
        ("get", "/api/sessions/11111111-1111-4111-8111-111111111111"),
        ("post", "/api/sessions/11111111-1111-4111-8111-111111111111/resume"),
        ("patch", "/api/sessions/11111111-1111-4111-8111-111111111111/title"),
        ("post", "/api/chat"),
        ("post", "/api/login"),
        ("post", "/api/users/identify"),
    ],
)
def test_user_scoped_routes_reject_anonymous(anon_client: TestClient, method: str, path: str) -> None:
    resp = anon_client.request(method.upper(), path, json={"title": "t", "message": "m", "session_id": "s"})
    assert resp.status_code == 401, path
    assert resp.json() == {"detail": "Not authenticated", "status_code": 401}


def test_health_and_auth_config_stay_public(anon_client: TestClient) -> None:
    assert anon_client.get("/health").status_code == 200
    assert anon_client.get("/api/auth/config").status_code == 200


# --- local auth feature flag ---------------------------------------------------------


def test_local_auth_endpoints_are_forbidden_when_disabled(anon_client: TestClient) -> None:
    creds = {"email": "a@example.org", "password": "correct horse battery"}
    reset = {"token": "t" * 32, "password": "x" * 12, "confirm_password": "x" * 12}
    for path, body in (
        ("local/login", creds),
        ("local/forgot", {"email": "a@example.org"}),
        ("reset/check", {"token": "t" * 32}),
        ("reset", reset),
    ):
        resp = anon_client.post(f"/api/auth/{path}", json=body)
        assert resp.status_code == 403, path
        assert resp.json()["detail"] == "Local sign-in is disabled on this deployment"


def test_public_registration_is_closed(settings: Settings) -> None:
    """No self-registration whether the local flag is on or off: the route no longer exists."""
    body = {
        "email": "a@example.org",
        "password": "x" * 12,
        "confirm_password": "x" * 12,
        "first_name": "A",
        "last_name": "B",
    }
    with TestClient(create_app(Settings(frontend_dir="none", local_auth_enabled=True))) as c:
        assert c.post("/api/auth/local/register", json=body).status_code == 404
    with TestClient(create_app(Settings(frontend_dir="none"))) as c:
        assert c.post("/api/auth/local/register", json=body).status_code == 404


def test_forgot_password_is_generic_and_needs_no_account(monkeypatch: pytest.MonkeyPatch) -> None:
    """The public answer is one fixed sentence (see tests/test_password_reset.py for the lifecycle)."""
    from app.services import password_reset

    seen: list[str] = []
    monkeypatch.setattr(password_reset, "request_reset", lambda conn, m, email, base, ttl: seen.append(email))
    app = create_app(Settings(frontend_dir="none", local_auth_enabled=True))
    app.dependency_overrides[get_db] = lambda: object()
    with TestClient(app) as c:
        resp = c.post("/api/auth/local/forgot", json={"email": "Nobody@Example.org"})
    assert resp.status_code == 202
    assert resp.json() == {"detail": password_reset.GENERIC_RESPONSE}
    assert seen == ["Nobody@example.org"]


def test_register_validation_rules() -> None:
    with pytest.raises(ValidationError, match="at least 8"):
        auth_service.validate_new_password("short", "short")
    with pytest.raises(ValidationError, match="do not match"):
        auth_service.validate_new_password("long enough password", "different password")
    auth_service.validate_new_password("long enough password", "long enough password")


def test_password_hashing_roundtrip_and_missing_hash() -> None:
    h = auth_service.hash_password("s3cret-passphrase")
    assert h.startswith("$argon2id$") and auth_service.verify_password("s3cret-passphrase", h)
    assert not auth_service.verify_password("wrong", h)
    assert not auth_service.verify_password("anything", None)


def test_session_token_is_random_and_only_its_hash_is_stored() -> None:
    a, b = auth_service.new_token(), auth_service.new_token()
    assert a != b and len(a) >= 40
    assert auth_service.token_hash(a) != a and len(auth_service.token_hash(a)) == 64


# --- Entra flow with a fake client -------------------------------------------------------


def _entra_app(fake: FakeEntra, ttl_hours: int = 168) -> TestClient:
    settings = replace(
        ENTRA,
        app_base_url="https://chat.example.org",
        session_cookie_secure=True,
        session_ttl_hours=ttl_hours,
    )
    app = create_app(settings, entra_client=fake)
    app.dependency_overrides[get_db] = lambda: object()
    return TestClient(app, raise_server_exceptions=False)


def test_entra_login_redirects_and_sets_flow_cookie() -> None:
    fake = FakeEntra()
    with _entra_app(fake) as c:
        resp = c.get("/api/auth/entra/login", follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers["location"].startswith("https://login.microsoftonline.com/")
    assert fake.redirect_uris == ["https://chat.example.org/api/auth/entra/callback"]
    cookie = resp.headers["set-cookie"]
    assert (
        "ai4drr_entra_flow=" in cookie
        and "HttpOnly" in cookie
        and "Secure" in cookie
        and "SameSite=lax" in cookie.lower().replace("samesite=lax", "SameSite=lax")
    )


def test_entra_not_configured_is_501() -> None:
    with TestClient(create_app(Settings(frontend_dir="none"))) as c:
        assert c.get("/api/auth/entra/login", follow_redirects=False).status_code == 501
        assert c.get("/api/auth/entra/callback?code=x&state=y").status_code == 501


def test_entra_callback_without_flow_cookie_returns_to_sign_in() -> None:
    with _entra_app(FakeEntra()) as c:
        resp = c.get("/api/auth/entra/callback?code=abc&state=abc", follow_redirects=False)
    assert resp.status_code == 302 and resp.headers["location"] == "/?auth_error=failed"
    assert "ai4drr_session=" not in resp.headers.get("set-cookie", "")


def test_entra_callback_error_from_provider_returns_to_sign_in() -> None:
    with _entra_app(FakeEntra()) as c:
        c.cookies.set("ai4drr_entra_flow", '{"state": "abc", "next": "/admin/login"}')
        resp = c.get("/api/auth/entra/callback?error=access_denied&state=abc", follow_redirects=False)
    assert resp.status_code == 302 and resp.headers["location"] == "/admin/login?auth_error=failed"


def test_entra_callback_signs_in_and_sets_session_cookie(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeEntra()
    signed_in: list[Any] = []
    started: list[tuple[str, str, int]] = []

    def fake_sign_in(conn: Any, identity: auth_service.EntraIdentity) -> Any:
        signed_in.append(identity)
        return TEST_USER

    def fake_start(conn: Any, user_id: str, method: str, ttl: int) -> str:
        started.append((user_id, method, ttl))
        return "cookie-token-value"

    monkeypatch.setattr(auth_service, "sign_in_entra", fake_sign_in)
    monkeypatch.setattr(auth_service, "start_session", fake_start)
    with _entra_app(fake, ttl_hours=12) as c:
        c.cookies.set("ai4drr_entra_flow", '{"state": "abc", "code_verifier": "v"}')
        resp = c.get("/api/auth/entra/callback?code=the-code&state=abc", follow_redirects=False)
    assert resp.status_code == 302 and resp.headers["location"] == "/"
    assert fake.finished[0][1] == {"code": "the-code", "state": "abc"}
    ident = signed_in[0]
    assert (ident.oid, ident.email, ident.first_name, ident.last_name) == (
        "0000-oid",
        "amina@undrr.example",
        "Amina",
        "Mwangi",
    )
    assert started == [(TEST_USER.id, "entra", 12)]
    cookies = resp.headers.get_list("set-cookie")
    session = next(h for h in cookies if h.startswith("ai4drr_session="))
    assert "cookie-token-value" in session and "HttpOnly" in session and "Secure" in session
    assert "samesite=lax" in session.lower() and "Max-Age=43200" in session
    assert any(
        h.startswith("ai4drr_entra_flow=") and ("Max-Age=0" in h or 'ai4drr_entra_flow=""' in h)
        for h in cookies
    )


def test_entra_identity_mapping_rules() -> None:
    claims: dict[str, object] = {
        "oid": "o",
        "email": "E@x.org",
        "preferred_username": "upn@x.org",
        "name": "N",
    }
    ident = auth_service.identity_from_claims(claims)
    assert ident.email == "E@x.org" and ident.name == "N" and ident.first_name is None
    assert (
        auth_service.identity_from_claims({"oid": "o", "preferred_username": "upn@x.org"}).email
        == "upn@x.org"
    )
    with pytest.raises(AuthenticationError):
        auth_service.identity_from_claims({"oid": "o", "preferred_username": "no-at-sign"})
    with pytest.raises(AuthenticationError):
        auth_service.identity_from_claims({"email": "a@b.c"})  # no oid


# --- CSRF: foreign Origin on unsafe /api requests ------------------------------------------


def test_foreign_origin_is_refused_on_unsafe_requests(settings: Settings) -> None:
    app = create_app(Settings(frontend_dir="none", app_base_url="https://chat.example.org"))
    sign_in(app)
    with TestClient(app, raise_server_exceptions=False) as c:
        evil = c.post("/api/auth/logout", headers={"Origin": "https://evil.example"})
        assert evil.status_code == 403 and evil.json()["detail"] == "Cross-origin request refused"
        own = c.post("/api/auth/logout", headers={"Origin": "https://chat.example.org"})
        assert own.status_code == 200
        same_host = c.post("/api/auth/logout", headers={"Origin": "http://testserver"})
        assert same_host.status_code == 200  # the request's own host is always trusted
        no_origin = c.post("/api/auth/logout")
        assert no_origin.status_code == 200  # non-browser clients send no Origin
        read = c.get("/api/auth/me", headers={"Origin": "https://evil.example"})
        assert read.status_code == 200  # safe methods are not gated (cookies are SameSite=Lax anyway)


def test_cors_is_off_unless_configured() -> None:
    with TestClient(create_app(Settings(frontend_dir="none"))) as c:
        resp = c.options(
            "/api/auth/config",
            headers={"Origin": "https://other.example", "Access-Control-Request-Method": "GET"},
        )
        assert "access-control-allow-origin" not in {k.lower() for k in resp.headers}
    with TestClient(
        create_app(Settings(frontend_dir="none", cors_allow_origins=("https://other.example",)))
    ) as c:
        resp = c.options(
            "/api/auth/config",
            headers={"Origin": "https://other.example", "Access-Control-Request-Method": "GET"},
        )
        assert resp.headers.get("access-control-allow-origin") == "https://other.example"
