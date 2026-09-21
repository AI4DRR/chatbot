"""Schemas for POST /api/users/identify and POST /api/login.

Shapes are byte-compatible with the legacy API (``docs/standalone-frontend-assessment.md`` §4.1).
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, EmailStr, Field


class UserLogin(BaseModel):
    """Request body shared by identify and login."""

    email: EmailStr
    name: str | None = None
    department: str | None = Field(default=None, description="UN department, e.g. UNDRR")
    unit: str | None = Field(default=None, description="Unit / office, e.g. ROAP")


class UserResponse(BaseModel):
    """The stored user record."""

    id: str = Field(description="User UUID")
    email: str
    name: str | None = None
    department: str | None = None
    unit: str | None = None


class IdentifyResponse(BaseModel):
    """``{"user": {...}}``."""

    user: UserResponse


class LoginResponse(BaseModel):
    """``{"user": {...}, "session_id": "<uuid>"}`` — login always creates a session."""

    user: UserResponse
    session_id: str


# --- Authentication (2026-09-20) -------------------------------------------------------


class AuthUser(BaseModel):
    """The signed-in user as ``GET /api/auth/me`` returns it (never the password hash or the oid)."""

    id: str
    email: str
    name: str | None = None
    first_name: str | None = None
    last_name: str | None = None
    department: str | None = None
    unit: str | None = None
    has_password: bool = False
    entra_linked: bool = False
    is_admin: bool = False


class AdminUser(BaseModel):
    """One row of the Chatbot Management users list."""

    id: str
    email: str
    name: str | None = None
    first_name: str | None = None
    last_name: str | None = None
    department: str | None = None
    is_admin: bool
    has_password: bool
    entra_linked: bool
    disabled: bool = False
    disabled_at: datetime | None = None
    created_at: datetime


class AdminUserDetail(AdminUser):
    """The View panel: the list row plus safe timestamps and activity counts (never any secret)."""

    unit: str | None = None
    updated_at: datetime
    chat_sessions: int = 0
    last_chat_at: datetime | None = None
    signed_in_sessions: int = 0
    last_seen_at: datetime | None = None


class AuthConfig(BaseModel):
    """Which sign-in methods this deployment offers (public)."""

    entra_enabled: bool
    local_auth_enabled: bool


class LogoutResponse(BaseModel):
    """POST /api/auth/logout — the app session is gone; ``redirect`` ends the Microsoft (B2C) session too."""

    signed_out: bool = True
    redirect: str | None = None


class LocalLogin(BaseModel):
    """POST /api/auth/local/login."""

    email: EmailStr
    password: str = Field(min_length=1, max_length=256)


class InvitationCreate(BaseModel):
    """POST /api/admin/invitations — administrators control access; only the e-mail is asked for."""

    email: EmailStr


class InvitationInfo(BaseModel):
    """One invitation as the management page lists it (never the token or its hash)."""

    id: str
    email: str
    created_at: datetime
    expires_at: datetime
    status: str = Field(description="pending | expired | revoked | consumed")
    invited_by_email: str | None = None
    grants_admin: bool = Field(
        default=False, description="Issued by the operator CLI: the account becomes admin"
    )


class SetupCheck(BaseModel):
    """POST /api/auth/setup/check — is this setup link usable, and for which e-mail?"""

    token: str = Field(min_length=16, max_length=256)


class SetupComplete(BaseModel):
    """POST /api/auth/setup — the invitee's profile and password; the e-mail comes from the invitation."""

    token: str = Field(min_length=16, max_length=256)
    password: str = Field(min_length=1, max_length=256)
    confirm_password: str = Field(min_length=1, max_length=256)
    first_name: str = Field(min_length=1, max_length=255)
    last_name: str = Field(min_length=1, max_length=255)
    department: str | None = Field(default=None, max_length=255)


class ForgotPassword(BaseModel):
    """POST /api/auth/local/forgot — request a password-reset e-mail (the answer is always generic)."""

    email: EmailStr


class GenericMessage(BaseModel):
    """A response whose only content is a sentence for the person (``detail`` like the error shape)."""

    detail: str


class PasswordResetInfo(BaseModel):
    """POST /api/auth/reset/check — the account a usable reset link belongs to."""

    email: str
    expires_at: datetime


class PasswordResetCheck(BaseModel):
    """POST /api/auth/reset/check — is this reset link usable?"""

    token: str = Field(min_length=16, max_length=256)


class PasswordResetComplete(BaseModel):
    """POST /api/auth/reset — the new password for the account behind the token."""

    token: str = Field(min_length=16, max_length=256)
    password: str = Field(min_length=1, max_length=256)
    confirm_password: str = Field(min_length=1, max_length=256)
