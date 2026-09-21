"""Azure AD B2C / Entra External ID: configuration, authority, flows, callback outcomes, logout."""

from __future__ import annotations

import logging
from dataclasses import replace
from typing import Any, ClassVar

import msal
import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_db
from app.config import (
    Settings,
    b2c_logout_endpoint,
    entra_authority,
    entra_configured,
    entra_mode,
    entra_redirect_path,
    entra_redirect_uri,
    validate_entra_settings,
)
from app.errors import ForbiddenError
from app.integrations.entra import MsalEntraClient, build_entra_client
from app.main import create_app
from app.services import auth as auth_service
from tests.conftest import TEST_USER
from tests.test_auth import FakeEntra

SECRET = "not-a-real-secret-value-xyz"
B2C = Settings(
    frontend_dir="none",
    app_base_url="http://localhost:8084",
    session_cookie_secure=False,
    b2c_tenant_name="unb2c",
    b2c_policy="B2C_1_UN_UNDRR_SIGNUP_SIGNIN",
    b2c_password_reset_policy="B2C_1_UN_UNDRR_PASSWORD_RESET",
    entra_client_id="00000000-0000-4000-8000-000000000001",
    entra_client_secret=SECRET,
    entra_redirect_uri="http://localhost:8084/api/auth/entra/callback",
)


# --- configuration --------------------------------------------------------------------


def test_b2c_settings_are_read_from_azure_b2c_names(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.config import load_settings

    for k in ("ENTRA_TENANT_ID", "ENTRA_CLIENT_ID", "ENTRA_CLIENT_SECRET"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("AZURE_B2C_TENANT_NAME", "unb2c")
    monkeypatch.setenv("AZURE_B2C_POLICY", "B2C_1_susi")
    monkeypatch.setenv("AZURE_B2C_PASSWORD_RESET_POLICY", "B2C_1_reset")
    monkeypatch.setenv("AZURE_B2C_EDIT_PROFILE_POLICY", "B2C_1_edit")
    monkeypatch.setenv("AZURE_B2C_CLIENT_ID", "cid")
    monkeypatch.setenv("AZURE_B2C_CLIENT_SECRET", SECRET)
    monkeypatch.setenv("AZURE_B2C_REDIRECT_URI", "https://chat.example.org/login/azure-callback ")
    monkeypatch.setenv("AZURE_B2C_LOGOUT_REDIRECT_URI", "https://chat.example.org/")
    s = load_settings()
    assert entra_mode(s) == "b2c" and entra_configured(s)
    assert (s.entra_client_id, s.entra_client_secret) == ("cid", SECRET)
    assert (s.b2c_policy, s.b2c_password_reset_policy, s.b2c_edit_profile_policy) == (
        "B2C_1_susi",
        "B2C_1_reset",
        "B2C_1_edit",
    )
    assert entra_redirect_uri(s) == "https://chat.example.org/login/azure-callback"
    assert entra_redirect_path(s) == "/login/azure-callback"
    assert s.b2c_logout_redirect_uri == "https://chat.example.org/"


def test_plain_entra_settings_still_work() -> None:
    s = Settings(
        entra_tenant_id="t", entra_client_id="c", entra_client_secret=SECRET, app_base_url="https://x"
    )
    assert entra_mode(s) == "entra" and entra_configured(s)
    validate_entra_settings(s)
    assert entra_authority(s) == "https://login.microsoftonline.com/t"
    assert entra_redirect_uri(s) == "https://x/api/auth/entra/callback"


@pytest.mark.parametrize(
    ("settings", "message"),
    [
        (replace(B2C, entra_tenant_id="t"), "not both"),
        (Settings(entra_client_id="c", entra_client_secret=SECRET), "no tenant"),
        (replace(B2C, entra_client_secret=None), "AZURE_B2C_CLIENT_ID and AZURE_B2C_CLIENT_SECRET"),
        (Settings(entra_tenant_id="t", entra_client_id="c"), "ENTRA_CLIENT_ID and ENTRA_CLIENT_SECRET"),
        (replace(B2C, b2c_policy=None), "AZURE_B2C_POLICY"),
        (replace(B2C, entra_redirect_uri="http://data.example.org/login/azure-callback"), "https"),
    ],
    ids=["both-tenants", "creds-no-tenant", "b2c-no-secret", "entra-no-secret", "no-policy", "http-redirect"],
)
def test_half_configured_microsoft_sign_in_is_refused(settings: Settings, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        validate_entra_settings(settings)
    with pytest.raises(ValueError):
        create_app(replace(settings, frontend_dir="none"))


def test_unconfigured_is_fine_and_reports_disabled() -> None:
    s = Settings(frontend_dir="none")
    validate_entra_settings(s)
    assert build_entra_client(s) is None
    with TestClient(create_app(s)) as c:
        assert c.get("/api/auth/config").json()["entra_enabled"] is False


def test_b2c_authority_and_logout_endpoint_shapes() -> None:
    assert (
        entra_authority(B2C)
        == "https://unb2c.b2clogin.com/unb2c.onmicrosoft.com/B2C_1_UN_UNDRR_SIGNUP_SIGNIN"
    )
    assert entra_authority(B2C, "B2C_1_UN_UNDRR_PASSWORD_RESET").endswith("/B2C_1_UN_UNDRR_PASSWORD_RESET")
    assert b2c_logout_endpoint(B2C) == (
        "https://unb2c.b2clogin.com/unb2c.onmicrosoft.com/B2C_1_UN_UNDRR_SIGNUP_SIGNIN/oauth2/v2.0/logout"
    )


# --- the MSAL client (MSAL itself replaced: no network) -------------------------------------


class FakeMsalApp:
    built: ClassVar[list[dict[str, Any]]] = []

    def __init__(self, client_id: str, authority: str, client_credential: str) -> None:
        FakeMsalApp.built.append(
            {"client_id": client_id, "authority": authority, "secret": client_credential}
        )
        self.authority_url = authority

    def initiate_auth_code_flow(self, scopes: list[str], redirect_uri: str) -> dict[str, Any]:
        return {
            "auth_uri": self.authority_url + "/oauth2/v2.0/authorize?state=s1&redirect_uri=" + redirect_uri,
            "state": "s1",
            "code_verifier": "pkce",
            "nonce": "n",
            "redirect_uri": redirect_uri,
        }

    def acquire_token_by_auth_code_flow(
        self, flow: dict[str, Any], auth_response: dict[str, str]
    ) -> dict[str, Any]:
        if auth_response.get("state") != flow["state"]:
            raise ValueError("state mismatch")
        if auth_response.get("code") == "bad":
            return {"error": "invalid_grant"}
        return {"id_token_claims": {"oid": "b2c-oid", "emails": ["Person@Example.org"], "given_name": "P"}}


@pytest.fixture
def fake_msal(monkeypatch: pytest.MonkeyPatch) -> type[FakeMsalApp]:
    FakeMsalApp.built = []
    monkeypatch.setattr(msal, "ConfidentialClientApplication", FakeMsalApp)
    return FakeMsalApp


def test_msal_client_uses_one_authority_per_policy_and_never_logs_the_secret(
    fake_msal: type[FakeMsalApp], caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.DEBUG):
        client = MsalEntraClient(B2C)
        flow = client.start("http://localhost:8084/api/auth/entra/callback")
        reset = client.start("http://localhost:8084/api/auth/entra/callback", "reset")
    assert (
        flow["policy"] == "signin"
        and "B2C_1_UN_UNDRR_SIGNUP_SIGNIN/oauth2/v2.0/authorize" in flow["auth_uri"]
    )
    assert "redirect_uri=http://localhost:8084/api/auth/entra/callback" in flow["auth_uri"]
    assert "B2C_1_UN_UNDRR_PASSWORD_RESET" in reset["auth_uri"]
    assert [b["authority"] for b in fake_msal.built] == [
        entra_authority(B2C),
        entra_authority(B2C, "B2C_1_UN_UNDRR_PASSWORD_RESET"),
    ]
    assert all(b["secret"] == SECRET and b["client_id"] == B2C.entra_client_id for b in fake_msal.built)
    assert SECRET not in caplog.text
    claims = client.finish(flow, {"code": "ok", "state": "s1"})
    assert claims["oid"] == "b2c-oid"
    assert client.supports("signin") and client.supports("reset")
    logout = client.logout_url("http://localhost:8084/")
    assert logout is not None and logout.startswith(b2c_logout_endpoint(B2C) + "?post_logout_redirect_uri=")


def test_msal_client_reports_state_mismatch_and_exchange_failures(fake_msal: type[FakeMsalApp]) -> None:
    from app.errors import AuthenticationError

    client = MsalEntraClient(B2C)
    flow = client.start("http://localhost:8084/api/auth/entra/callback")
    with pytest.raises(AuthenticationError, match="state"):
        client.finish(flow, {"code": "ok", "state": "other"})
    with pytest.raises(AuthenticationError):
        client.finish(flow, {"code": "bad", "state": "s1"})
    plain = MsalEntraClient(Settings(entra_tenant_id="t", entra_client_id="c", entra_client_secret=SECRET))
    assert plain.logout_url("https://x/") is None and not plain.supports("reset")


def test_b2c_reset_policy_missing_is_not_supported(fake_msal: type[FakeMsalApp]) -> None:
    client = MsalEntraClient(replace(B2C, b2c_password_reset_policy=None))
    assert not client.supports("reset")


# --- identity mapping ---------------------------------------------------------------------


def test_b2c_claims_map_to_identity() -> None:
    ident = auth_service.identity_from_claims(
        {
            "oid": "o1",
            "emails": ["Ada@Example.org"],
            "given_name": "Ada",
            "family_name": "L",
            "tfp": "B2C_1_x",
        }
    )
    assert (ident.oid, ident.email, ident.name, ident.first_name, ident.last_name) == (
        "o1",
        "Ada@Example.org",
        "Ada L",
        "Ada",
        "L",
    )
    with pytest.raises(Exception, match="usable identity"):
        auth_service.identity_from_claims({"oid": "o1", "emails": []})


# --- routes with the fake client ----------------------------------------------------------------


def _app(fake: FakeEntra, settings: Settings = B2C) -> TestClient:
    app = create_app(settings, entra_client=fake)
    app.dependency_overrides[get_db] = lambda: object()
    return TestClient(app, raise_server_exceptions=False)


def test_login_uses_the_registered_redirect_uri_and_policy() -> None:
    fake = FakeEntra()
    with _app(fake, replace(B2C, entra_redirect_uri="http://localhost:8084/login/azure-callback")) as c:
        resp = c.get("/api/auth/entra/login", follow_redirects=False)
        assert resp.status_code == 302 and "p=signin" in resp.headers["location"]
        assert fake.redirect_uris == ["http://localhost:8084/login/azure-callback"]
        assert "Path=/login/azure-callback" in resp.headers["set-cookie"]
        # the registered path is served by the same callback handler
        assert (
            c.get(
                "/login/azure-callback?error=access_denied&error_description=AADB2C90091",
                follow_redirects=False,
            ).headers["location"]
            == "/?auth_error=cancelled"
        )
        reset = c.get("/api/auth/entra/login?policy=reset", follow_redirects=False)
        assert reset.status_code == 302 and "p=reset" in reset.headers["location"]
        assert c.get("/api/auth/entra/login?policy=other").status_code == 422


def test_callback_forgot_password_starts_the_reset_flow_and_cancel_returns_cleanly() -> None:
    with _app(FakeEntra()) as c:
        c.cookies.set("ai4drr_entra_flow", '{"state": "abc", "next": "/admin/login"}')
        resp = c.get(
            "/api/auth/entra/callback?error=access_denied&error_description=AADB2C90118%3A+forgot",
            follow_redirects=False,
        )
        assert resp.status_code == 302
        assert resp.headers["location"] == "/api/auth/entra/login?policy=reset&next=/admin/login"
        c.cookies.set("ai4drr_entra_flow", '{"state": "abc", "next": "/"}')
        resp = c.get(
            "/api/auth/entra/callback?error=access_denied&error_description=AADB2C90091%3A+cancelled",
            follow_redirects=False,
        )
        assert resp.headers["location"] == "/?auth_error=cancelled"
        assert "ai4drr_session=" not in resp.headers.get("set-cookie", "")


def test_callback_success_maps_user_opens_session_and_never_grants_admin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakeEntra(
        claims={
            "oid": "b2c-oid",
            "emails": ["amina@undrr.example"],
            "given_name": "Amina",
            "family_name": "M",
        }
    )
    seen: list[Any] = []

    def sign_in(conn: Any, ident: Any) -> Any:
        seen.append(ident)
        return TEST_USER

    monkeypatch.setattr(auth_service, "sign_in_entra", sign_in)
    monkeypatch.setattr(auth_service, "start_session", lambda conn, uid, method, ttl: "cookie-value")
    with _app(fake) as c:
        c.cookies.set(
            "ai4drr_entra_flow", '{"state": "abc", "code_verifier": "v", "policy": "signin", "next": "/"}'
        )
        resp = c.get("/api/auth/entra/callback?code=the-code&state=abc", follow_redirects=False)
        assert resp.status_code == 302 and resp.headers["location"] == "/"
        assert "ai4drr_session=cookie-value" in resp.headers["set-cookie"]
        assert seen[0].email == "amina@undrr.example" and seen[0].name == "Amina M"
    # the identity carries no authorisation at all: is_admin exists only on the local users row
    assert not hasattr(seen[0], "is_admin") and TEST_USER.is_admin is False


def test_callback_disabled_account_opens_no_session(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(conn: Any, ident: Any) -> Any:
        raise ForbiddenError(auth_service.DISABLED_MESSAGE)

    monkeypatch.setattr(auth_service, "sign_in_entra", refuse)
    monkeypatch.setattr(
        auth_service, "start_session", lambda *a: pytest.fail("no session for a disabled account")
    )
    with _app(FakeEntra()) as c:
        c.cookies.set("ai4drr_entra_flow", '{"state": "abc", "policy": "signin", "next": "/"}')
        resp = c.get("/api/auth/entra/callback?code=the-code&state=abc", follow_redirects=False)
    assert resp.status_code == 302 and resp.headers["location"] == "/?auth_error=disabled"
    assert "ai4drr_session=" not in resp.headers.get("set-cookie", "")


def test_callback_exchange_failure_returns_to_sign_in(monkeypatch: pytest.MonkeyPatch) -> None:
    with _app(FakeEntra(fail=True)) as c:
        c.cookies.set("ai4drr_entra_flow", '{"state": "abc", "policy": "signin", "next": "/"}')
        resp = c.get("/api/auth/entra/callback?code=the-code&state=abc", follow_redirects=False)
    assert resp.headers["location"] == "/?auth_error=failed"


def test_logout_returns_the_b2c_end_session_url_only_for_microsoft_sessions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ended: list[str] = []

    def end(conn: Any, token: str | None) -> str | None:
        ended.append(str(token))
        return "entra" if token == "ms" else "local" if token == "lo" else None

    monkeypatch.setattr(auth_service, "end_session", end)
    settings = replace(
        B2C, database_url="postgresql://unused/db", b2c_logout_redirect_uri="http://localhost:8084/"
    )
    monkeypatch.setattr(
        "app.api.routes.auth.connect", lambda url: __import__("contextlib").nullcontext(object())
    )
    monkeypatch.setattr("app.api.deps.connect", lambda url: __import__("contextlib").nullcontext(object()))
    monkeypatch.setattr(auth_service, "resolve_session", lambda conn, token: TEST_USER)
    with _app(FakeEntra(), settings) as c:
        c.cookies.set("ai4drr_session", "ms")
        ms = c.post("/api/auth/logout")
        c.cookies.set("ai4drr_session", "lo")
        lo = c.post("/api/auth/logout")
    assert ms.status_code == 200
    assert (
        ms.json()["redirect"]
        == "https://login.microsoftonline.com/x/logout?post_logout_redirect_uri=http://localhost:8084/"
    )
    assert lo.json()["redirect"] is None
    assert ended == ["ms", "lo"]
    assert 'ai4drr_session=""' in ms.headers["set-cookie"] or "Max-Age=0" in ms.headers["set-cookie"]


def test_startup_log_mentions_policy_and_redirect_but_no_secret(capfd: pytest.CaptureFixture[str]) -> None:
    with TestClient(create_app(B2C, entra_client=FakeEntra())):
        pass
    err = capfd.readouterr().err  # configure_logging owns the root handler (stderr)
    assert "microsoft=b2c" in err and "B2C_1_UN_UNDRR_SIGNUP_SIGNIN" in err
    assert "redirect_uri=http://localhost:8084/api/auth/entra/callback" in err
    assert SECRET not in err
