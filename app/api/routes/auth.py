"""Authentication routes.

``/api/auth/*`` — sign-in with Azure Entra ID (primary), local email/password
(secondary, ``LOCAL_AUTH_ENABLED``) with forgot/reset password, session info
and logout, and the invitation account-setup endpoints. The browser session is an HttpOnly cookie
(see ``services.auth``). There is no public registration: local accounts are
created only by completing an administrator's invitation
(``services.invitations``).

``/api/users/identify`` and ``/api/login`` keep their legacy response shapes
but no longer accept an identity from the client: they act on the signed-in
user (``identify`` returns it; ``login`` opens a new chat session for it).
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import psycopg
from fastapi import APIRouter, Body, Depends, Query, Request, Response
from fastapi.responses import FileResponse, RedirectResponse
from psycopg.rows import DictRow

from app.api.deps import (
    CurrentUser,
    get_app_settings,
    get_db,
    get_entra_client,
    get_mailer,
    get_optional_user,
    get_session_token,
)
from app.config import Settings, entra_configured, entra_redirect_path, entra_redirect_uri
from app.db.connection import connect
from app.errors import AuthenticationError, ForbiddenError, NotAvailableError
from app.integrations.entra import POLICY_RESET, POLICY_SIGN_IN, EntraClient
from app.integrations.mail import Mailer
from app.schemas.auth import (
    AuthConfig,
    AuthUser,
    ForgotPassword,
    GenericMessage,
    IdentifyResponse,
    InvitationInfo,
    LocalLogin,
    LoginResponse,
    LogoutResponse,
    PasswordResetCheck,
    PasswordResetComplete,
    PasswordResetInfo,
    SetupCheck,
    SetupComplete,
    UserResponse,
)
from app.services import auth as auth_service
from app.services import invitations as invitation_service
from app.services import password_reset as reset_service
from app.services import sessions as session_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["Auth"])
PAGES = Path(__file__).resolve().parents[2] / "pages"

ENTRA_FLOW_COOKIE = "ai4drr_entra_flow"
ENTRA_FLOW_MAX_AGE = 600  # seconds to complete the Microsoft round trip


# --- cookies -----------------------------------------------------------------------


def _set_session_cookie(response: Response, settings: Settings, token: str) -> None:
    response.set_cookie(
        settings.session_cookie_name,
        token,
        max_age=settings.session_ttl_hours * 3600,
        httponly=True,
        secure=settings.session_cookie_secure,
        samesite="lax",
        path="/",
    )


def _clear_session_cookie(response: Response, settings: Settings) -> None:
    response.delete_cookie(settings.session_cookie_name, path="/")


def _user_response(user: AuthUser) -> UserResponse:
    return UserResponse(
        id=user.id, email=user.email, name=user.name, department=user.department, unit=user.unit
    )


LOCAL_AUTH_DISABLED = "Local sign-in is disabled on this deployment"
LOCAL_ACCOUNTS_DISABLED = (
    "Local accounts are disabled on this deployment (LOCAL_AUTH_ENABLED=false); people sign in with Microsoft"
)


def require_local_auth(settings: Settings = Depends(get_app_settings)) -> None:
    """Dependency placed before ``get_db`` so a disabled feature answers 403 before any database work."""
    if not settings.local_auth_enabled:
        raise ForbiddenError(LOCAL_AUTH_DISABLED)


def require_local_accounts(settings: Settings = Depends(get_app_settings)) -> None:
    """Invitation setup and inviting create local passwords: refused (403) in Microsoft-only mode."""
    if not settings.local_auth_enabled:
        raise ForbiddenError(LOCAL_ACCOUNTS_DISABLED)


def require_entra(client: EntraClient | None = Depends(get_entra_client)) -> EntraClient:
    """Dependency: the configured Entra client, or 501 before any database work."""
    if client is None:
        raise NotAvailableError("Microsoft sign-in is not configured on this deployment")
    return client


# --- public config / current user ----------------------------------------------------


@router.get("/auth/config", response_model=AuthConfig)
def auth_config(settings: Settings = Depends(get_app_settings)) -> AuthConfig:
    """Which sign-in methods are available (drives the sign-in screen)."""
    return AuthConfig(
        entra_enabled=entra_configured(settings), local_auth_enabled=settings.local_auth_enabled
    )


@router.get("/auth/me", response_model=AuthUser)
def me(user: CurrentUser) -> AuthUser:
    """The signed-in user (401 without a valid session)."""
    return user


@router.post("/auth/logout", response_model=LogoutResponse)
def logout(
    response: Response,
    token: str | None = Depends(get_session_token),
    settings: Settings = Depends(get_app_settings),
    user: AuthUser | None = Depends(get_optional_user),
    client: EntraClient | None = Depends(get_entra_client),
) -> LogoutResponse:
    """Invalidate the server-side session and clear the cookie (idempotent).

    A session opened with Microsoft (B2C) also gets the provider's end-session
    URL back so the browser can close that sign-in too and return to
    ``AZURE_B2C_LOGOUT_REDIRECT_URI`` (default: this app's root). Local
    sessions never leave the app.
    """
    method = None
    if token and settings.database_url:
        with connect(settings.database_url) as conn:
            method = auth_service.end_session(conn, token)
    _clear_session_cookie(response, settings)
    redirect = None
    if method == "entra" and client is not None:
        redirect = client.logout_url(settings.b2c_logout_redirect_uri or settings.app_base_url + "/")
    if user:
        logger.info("user %s signed out (%s)", user.id, method or "no session")
    return LogoutResponse(redirect=redirect)


# --- Entra ID ------------------------------------------------------------------------


def safe_next(value: str | None) -> str:
    """Where to send the browser after sign-in: a path on this site only (never another origin)."""
    if value and value.startswith("/") and not value.startswith("//") and "\\" not in value:
        return value
    return "/"


def with_auth_error(target: str, code: str) -> str:
    """The sign-in page with a non-secret reason the front end turns into a message."""
    return f"{target}{'&' if '?' in target else '?'}auth_error={code}"


@router.get("/auth/entra/login")
def entra_login(
    next: str | None = Query(default=None, alias="next"),
    policy: str = Query(default=POLICY_SIGN_IN, pattern="^(signin|reset)$"),
    settings: Settings = Depends(get_app_settings),
    client: EntraClient = Depends(require_entra),
) -> Response:
    """Start the Microsoft sign-in (or, on B2C, the password-reset user flow): redirect to Microsoft.

    The MSAL flow (state, PKCE verifier, nonce, which policy) lives in a
    short-lived HttpOnly cookie scoped to the callback path; the redirect URI
    is the one registered on the app registration (``entra_redirect_uri``).
    """
    if not client.supports(policy):
        raise NotAvailableError("This Microsoft sign-in flow is not configured on this deployment")
    flow = client.start(entra_redirect_uri(settings), policy)
    flow["next"] = safe_next(next)
    response = RedirectResponse(flow["auth_uri"], status_code=302)
    response.set_cookie(
        ENTRA_FLOW_COOKIE,
        json.dumps(flow),
        max_age=ENTRA_FLOW_MAX_AGE,
        httponly=True,
        secure=settings.session_cookie_secure,
        samesite="lax",
        path=entra_redirect_path(settings),
    )
    return response


B2C_FORGOT_PASSWORD = "AADB2C90118"  # the person chose "Forgot your password?" on the B2C page
B2C_CANCELLED = "AADB2C90091"  # the person cancelled the B2C page


@router.get("/auth/entra/callback")
def entra_callback(
    request: Request,
    settings: Settings = Depends(get_app_settings),
    client: EntraClient = Depends(require_entra),
    conn: psycopg.Connection[DictRow] = Depends(get_db),
) -> Response:
    """Finish the Microsoft sign-in: validate code + state, map the identity, open a session.

    Anything that is not a success sends the browser back to the sign-in
    page with ``?auth_error=cancelled|failed|disabled`` (never an error JSON
    in the address bar). B2C's "forgot password" answer starts the reset user
    flow when one is configured. The identity never grants ``is_admin`` — that
    flag lives on the local ``users`` row only.
    """
    raw = request.cookies.get(ENTRA_FLOW_COOKIE)
    flow: dict[str, Any] = {}
    if raw:
        try:
            flow = json.loads(raw)
        except ValueError:
            flow = {}
    target = safe_next(flow.get("next"))
    callback_path = entra_redirect_path(settings)
    params = {k: v for k, v in request.query_params.items()}

    def back(code: str) -> Response:
        out = RedirectResponse(with_auth_error(target, code), status_code=302)
        out.delete_cookie(ENTRA_FLOW_COOKIE, path=callback_path)
        return out

    if "error" in params:
        description = params.get("error_description", "")
        if B2C_FORGOT_PASSWORD in description and client.supports(POLICY_RESET):
            logger.info("microsoft sign-in: password reset requested, starting the reset flow")
            out = RedirectResponse(
                f"/api/auth/entra/login?policy={POLICY_RESET}&next={target}", status_code=302
            )
            out.delete_cookie(ENTRA_FLOW_COOKIE, path=callback_path)
            return out
        if B2C_CANCELLED in description:
            logger.info("microsoft sign-in cancelled by the person")
            return back("cancelled")
        logger.warning("microsoft sign-in declined or failed: %s", params.get("error"))
        return back("failed")
    if not flow:
        logger.warning("microsoft sign-in callback without a flow cookie")
        return back("failed")
    try:
        claims = client.finish(flow, params)
        identity = auth_service.identity_from_claims(claims)
        user = auth_service.sign_in_entra(conn, identity)
    except AuthenticationError as exc:
        logger.warning("microsoft sign-in not completed: %s", exc.detail)
        return back("failed")
    except ForbiddenError:
        return back("disabled")
    token = auth_service.start_session(conn, user.id, "entra", settings.session_ttl_hours)
    response = RedirectResponse(target, status_code=302)
    _set_session_cookie(response, settings, token)
    response.delete_cookie(ENTRA_FLOW_COOKIE, path=callback_path)
    return response


# --- local email / password (feature-flagged) -------------------------------------------


@router.post("/auth/local/login", response_model=AuthUser)
def local_login(
    body: LocalLogin,
    response: Response,
    _flag: None = Depends(require_local_auth),
    settings: Settings = Depends(get_app_settings),
    conn: psycopg.Connection[DictRow] = Depends(get_db),
) -> AuthUser:
    """Verify email + password and open a browser session."""
    user = auth_service.authenticate_local(conn, body.email, body.password)
    token = auth_service.start_session(conn, user.id, "local", settings.session_ttl_hours)
    _set_session_cookie(response, settings, token)
    logger.info("local sign-in: user %s", user.id)
    return user


@router.post("/auth/local/forgot", response_model=GenericMessage, status_code=202)
def local_forgot(
    body: ForgotPassword,
    _flag: None = Depends(require_local_auth),
    settings: Settings = Depends(get_app_settings),
    mailer: Mailer = Depends(get_mailer),
    conn: psycopg.Connection[DictRow] = Depends(get_db),
) -> GenericMessage:
    """Request a password-reset e-mail. The answer never says whether the account exists."""
    reset_service.request_reset(
        conn, mailer, body.email, settings.app_base_url, settings.password_reset_ttl_minutes
    )
    return GenericMessage(detail=reset_service.GENERIC_RESPONSE)


@router.post("/auth/reset/check", response_model=PasswordResetInfo)
def reset_check(
    body: PasswordResetCheck,
    _flag: None = Depends(require_local_auth),
    conn: psycopg.Connection[DictRow] = Depends(get_db),
) -> PasswordResetInfo:
    """Is this reset link usable? Returns the account e-mail or 410 with the reason."""
    return reset_service.check_token(conn, body.token)


@router.post("/auth/reset", status_code=204, response_class=Response)
def reset_complete(
    body: PasswordResetComplete,
    _flag: None = Depends(require_local_auth),
    conn: psycopg.Connection[DictRow] = Depends(get_db),
) -> Response:
    """Set the new password, consume the link and end every existing session of the account.

    No session is opened here: the person signs in with the new password.
    """
    reset_service.complete_reset(conn, body.token, body.password, body.confirm_password)
    return Response(status_code=204)


# --- invitation account setup (public, token-gated; the token arrives in the JSON body) -----


@router.post("/auth/setup/check", response_model=InvitationInfo)
def setup_check(
    body: SetupCheck,
    _flag: None = Depends(require_local_accounts),
    conn: psycopg.Connection[DictRow] = Depends(get_db),
) -> InvitationInfo:
    """Is this setup link usable? Returns the invitation (fixed e-mail) or 410 with the reason."""
    return invitation_service.check_token(conn, body.token)


@router.post("/auth/setup", response_model=AuthUser, status_code=201)
def setup_complete(
    body: SetupComplete,
    response: Response,
    _flag: None = Depends(require_local_accounts),
    settings: Settings = Depends(get_app_settings),
    conn: psycopg.Connection[DictRow] = Depends(get_db),
) -> AuthUser:
    """Create the invited account, consume the invitation and open a normal session — one transaction."""
    user = invitation_service.complete_setup(
        conn,
        body.token,
        body.password,
        body.confirm_password,
        body.first_name,
        body.last_name,
        body.department,
    )
    token = auth_service.start_session(conn, user.id, "local", settings.session_ttl_hours)
    _set_session_cookie(response, settings, token)
    return user


pages = APIRouter(tags=["Auth pages"], include_in_schema=False)


@pages.get(invitation_service.SETUP_PATH)
def setup_page() -> Response:
    """The account-setup page (the token is in the URL fragment; the page validates it via the API)."""
    return FileResponse(PAGES / "setup.html", media_type="text/html")


@pages.get(reset_service.RESET_PATH)
def reset_page() -> Response:
    """The reset-password page (same pattern: token in the fragment, validated via the API)."""
    return FileResponse(PAGES / "reset.html", media_type="text/html")


# --- legacy identity endpoints, now bound to the signed-in user ----------------------------


@router.post("/users/identify", response_model=IdentifyResponse)
def identify_user(user: CurrentUser, body: dict[str, Any] | None = Body(default=None)) -> IdentifyResponse:
    """The signed-in user in the legacy shape. Any body is accepted for compatibility and ignored."""
    return IdentifyResponse(user=_user_response(user))


@router.post("/login", response_model=LoginResponse)
def login(
    user: CurrentUser,
    body: dict[str, Any] | None = Body(default=None),
    conn: psycopg.Connection[DictRow] = Depends(get_db),
) -> LoginResponse:
    """Open a new chat session for the signed-in user (legacy shape; body ignored)."""
    session_id = session_service.create_session_for_user(conn, user.id)
    logger.info("user %s opened session %s", user.id, session_id)
    return LoginResponse(user=_user_response(user), session_id=session_id)
