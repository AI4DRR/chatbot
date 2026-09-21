"""Microsoft sign-in via MSAL: Azure AD B2C / Entra External ID user flows, or a plain Entra ID tenant.

MSAL does the protocol work (OpenID Connect authorization-code flow with
PKCE): ``initiate_auth_code_flow`` builds the authorization URL with ``state``,
``nonce`` and a PKCE verifier; ``acquire_token_by_auth_code_flow`` checks the
state, exchanges the code at the token endpoint and validates the id_token
(issuer, audience, nonce, signature) — ``id_token_claims`` is only returned
when that verified. Endpoints come from the authority's OpenID metadata, never
hard-coded. For B2C every user flow (sign-up/sign-in, password reset) is its
own authority, so one MSAL application is kept per policy, built lazily so a
metadata outage cannot stop the app from starting.

The client secret is used only as MSAL's ``client_credential``; it is never
logged, and nothing here formats settings into messages.
"""

from __future__ import annotations

import logging
import threading
from typing import Any, Protocol
from urllib.parse import urlencode

import msal

from app.config import (
    Settings,
    b2c_logout_endpoint,
    entra_authority,
    entra_configured,
    entra_mode,
    validate_entra_settings,
)
from app.errors import AuthenticationError, IntegrationConfigError

logger = logging.getLogger(__name__)

SCOPES: list[str] = []  # id_token only: MSAL adds openid/profile/offline_access itself
POLICY_SIGN_IN = "signin"
POLICY_RESET = "reset"
UNAVAILABLE = "Microsoft sign-in is temporarily unavailable; try again in a moment"


class EntraClient(Protocol):
    """What the auth routes need from MSAL."""

    def start(self, redirect_uri: str, policy: str = POLICY_SIGN_IN) -> dict[str, Any]:
        """Begin a flow: MSAL's flow dict (``auth_uri``, ``state``, PKCE verifier, ...) plus ``policy``."""
        ...

    def finish(self, flow: dict[str, Any], auth_response: dict[str, str]) -> dict[str, Any]:
        """Exchange the code; returns the validated id_token claims."""
        ...

    def logout_url(self, post_logout_redirect_uri: str) -> str | None:
        """Where to send the browser to end the identity provider's session, or ``None`` (plain Entra)."""
        ...

    def supports(self, policy: str) -> bool:
        """Whether a named flow (``signin`` / ``reset``) is configured."""
        ...


class MsalEntraClient:
    """The real client, built from the ``AZURE_B2C_*`` or ``ENTRA_*`` settings."""

    def __init__(self, settings: Settings) -> None:
        """Validate the configuration up front; MSAL applications are created on first use."""
        validate_entra_settings(settings)
        if not entra_configured(settings):
            raise IntegrationConfigError("Microsoft sign-in is not configured")
        self._settings = settings
        self._mode = entra_mode(settings)
        self._apps: dict[str, msal.ConfidentialClientApplication] = {}
        self._lock = threading.Lock()

    def _policy_name(self, policy: str) -> str | None:
        if self._mode != "b2c":
            return None
        if policy == POLICY_RESET:
            return self._settings.b2c_password_reset_policy
        return self._settings.b2c_policy

    def supports(self, policy: str) -> bool:
        """See ``EntraClient.supports``."""
        return policy == POLICY_SIGN_IN or (self._mode == "b2c" and bool(self._policy_name(policy)))

    def _app(self, policy: str) -> msal.ConfidentialClientApplication:
        name = self._policy_name(policy)
        if policy != POLICY_SIGN_IN and name is None:
            raise AuthenticationError("This Microsoft sign-in flow is not configured")
        key = name or "entra"
        with self._lock:
            app = self._apps.get(key)
            if app is None:
                try:
                    app = msal.ConfidentialClientApplication(
                        self._settings.entra_client_id,
                        authority=entra_authority(self._settings, name),
                        client_credential=self._settings.entra_client_secret,
                    )
                except Exception as exc:  # metadata unreachable, unknown authority, ...
                    logger.error(
                        "microsoft sign-in: authority for flow %s not usable: %s", policy, type(exc).__name__
                    )
                    raise AuthenticationError(UNAVAILABLE) from exc
                self._apps[key] = app
        return app

    def start(self, redirect_uri: str, policy: str = POLICY_SIGN_IN) -> dict[str, Any]:
        """See ``EntraClient.start``."""
        flow: dict[str, Any] = self._app(policy).initiate_auth_code_flow(SCOPES, redirect_uri=redirect_uri)
        flow["policy"] = policy
        return flow

    def finish(self, flow: dict[str, Any], auth_response: dict[str, str]) -> dict[str, Any]:
        """See ``EntraClient.finish``; MSAL error responses become ``AuthenticationError``."""
        policy = str(flow.get("policy") or POLICY_SIGN_IN)
        try:
            result: dict[str, Any] = self._app(policy).acquire_token_by_auth_code_flow(flow, auth_response)
        except ValueError as exc:  # state mismatch and similar
            raise AuthenticationError("Microsoft sign-in could not be completed (state mismatch)") from exc
        if "error" in result or "id_token_claims" not in result:
            logger.warning("microsoft token exchange failed: %s", result.get("error"))
            raise AuthenticationError("Microsoft sign-in could not be completed")
        claims: dict[str, Any] = result["id_token_claims"]
        return claims

    def logout_url(self, post_logout_redirect_uri: str) -> str | None:
        """B2C: the sign-in policy's end-session endpoint; plain Entra: none (local session only)."""
        if self._mode != "b2c":
            return None
        return (
            b2c_logout_endpoint(self._settings)
            + "?"
            + urlencode({"post_logout_redirect_uri": post_logout_redirect_uri})
        )


def build_entra_client(settings: Settings) -> EntraClient | None:
    """The real client when configured, else ``None`` (sign-in reported as not configured).

    Half-configured settings raise ``ValueError`` so the app refuses to start
    with a clear message instead of a button that cannot work.
    """
    validate_entra_settings(settings)
    return MsalEntraClient(settings) if entra_configured(settings) else None
