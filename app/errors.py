"""Application-level exceptions and their HTTP mapping.

Domain and data-access code raises these (never ``HTTPException``); the API
layer converts them to responses in ``app.main`` using the same JSON error
shape the legacy API produced: ``{"detail": str, "status_code": int}``.
"""

from __future__ import annotations


class AppError(Exception):
    """Base class for errors the API knows how to report."""

    status_code = 500
    detail = "Internal server error"

    def __init__(self, detail: str | None = None) -> None:
        """Use the class default detail unless a specific message is given."""
        super().__init__(detail or self.detail)
        if detail is not None:
            self.detail = detail


class NotFoundError(AppError):
    """A referenced resource (session, user) does not exist."""

    status_code = 404
    detail = "Not found"


class ValidationError(AppError):
    """Input that passed schema validation but is semantically invalid."""

    status_code = 400
    detail = "Invalid request"


class DatabaseUnavailableError(AppError):
    """The request needs PostgreSQL but no ``DATABASE_URL`` is configured."""

    status_code = 503
    detail = "Database not configured"


class ChatPipelineUnavailableError(AppError):
    """``/api/chat`` was called but no answer pipeline is configured."""

    status_code = 503
    detail = "Chat pipeline not available: AI integrations are not implemented yet"


class IntegrationConfigError(AppError):
    """An external service is needed but its configuration is incomplete.

    ``detail`` names the missing environment variables, never their values.
    """

    status_code = 503
    detail = "External service not configured"


class IntegrationError(AppError):
    """An external service call failed.

    ``kind`` classifies the failure far enough to know where to look —
    ``auth``, ``network``, ``deployment``, ``index``, ``api_version``,
    ``request`` or ``upstream`` — without exposing the provider response.
    """

    status_code = 502
    detail = "External service call failed"

    def __init__(self, kind: str, detail: str | None = None) -> None:
        """Record the failure class alongside the safe, human-readable detail."""
        super().__init__(detail)
        self.kind = kind


class AuthenticationError(AppError):
    """No valid browser session, or credentials that do not verify."""

    status_code = 401
    detail = "Not authenticated"


class ForbiddenError(AppError):
    """Authenticated, but not allowed (e.g. a feature that is switched off, or a bad Origin)."""

    status_code = 403
    detail = "Forbidden"


class ConflictError(AppError):
    """The request conflicts with existing data (e.g. an email that is already registered)."""

    status_code = 409
    detail = "Conflict"


class NotAvailableError(AppError):
    """A feature this deployment does not provide (never faked as success)."""

    status_code = 501
    detail = "Not available in this deployment"
