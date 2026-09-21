"""Authentication rules: passwords, browser sessions, identity mapping.

Mechanism (docs/architecture.md §6): a random opaque token in an HttpOnly
cookie, looked up in ``auth_sessions`` by its SHA-256. No JWT, no signing
key to rotate, immediate server-side invalidation on logout. Passwords use
argon2id via ``argon2-cffi`` (never home-grown). Entra identities are
mapped to the existing ``users`` row by object id, then by email.
"""

from __future__ import annotations

import hashlib
import logging
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

import psycopg
from argon2 import PasswordHasher
from argon2.exceptions import VerificationError, VerifyMismatchError
from psycopg.rows import DictRow

from app.db import auth as auth_db
from app.errors import AuthenticationError, ForbiddenError, ValidationError
from app.schemas.auth import AdminUser, AuthUser

logger = logging.getLogger(__name__)

PASSWORD_MIN_LENGTH = 8
PASSWORD_MAX_LENGTH = 256
TOKEN_BYTES = 32

_hasher = PasswordHasher()  # argon2id, library defaults (RFC 9106 second recommended parameters)


# --- passwords -----------------------------------------------------------------------


def hash_password(password: str) -> str:
    """argon2id hash for storage."""
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str | None) -> bool:
    """Constant-time-ish verification; ``False`` for users without a local password."""
    if not password_hash:
        # Still spend a hash's worth of time so a missing password is not observable by timing.
        _hasher.hash(password)
        return False
    try:
        return _hasher.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError):
        return False


def validate_new_password(password: str, confirm: str) -> None:
    """Length bounds and confirmation match; raises ``ValidationError``."""
    if len(password) < PASSWORD_MIN_LENGTH:
        raise ValidationError(f"Password must be at least {PASSWORD_MIN_LENGTH} characters")
    if len(password) > PASSWORD_MAX_LENGTH:
        raise ValidationError(f"Password must be at most {PASSWORD_MAX_LENGTH} characters")
    if password != confirm:
        raise ValidationError("Passwords do not match")


# --- session tokens ------------------------------------------------------------------


def new_token() -> str:
    """The cookie value: 256 bits from the OS CSPRNG, URL-safe."""
    return secrets.token_urlsafe(TOKEN_BYTES)


def token_hash(token: str) -> str:
    """What is stored: the SHA-256 of the cookie value."""
    return hashlib.sha256(token.encode()).hexdigest()


def _user(row: DictRow) -> AuthUser:
    return AuthUser(
        id=str(row["id"]),
        email=row["email"],
        name=row["name"],
        first_name=row["first_name"],
        last_name=row["last_name"],
        department=row["department"],
        unit=row["unit"],
        has_password=row["password_hash"] is not None,
        entra_linked=row["entra_oid"] is not None,
        is_admin=bool(row["is_admin"]),
    )


def start_session(conn: psycopg.Connection[DictRow], user_id: str, method: str, ttl_hours: int) -> str:
    """Create a browser session; returns the cookie token (the only time it exists in clear)."""
    token = new_token()
    expires = datetime.now(UTC) + timedelta(hours=ttl_hours)
    auth_db.create_session(conn, token_hash(token), UUID(user_id), method, expires)
    return token


def resolve_session(conn: psycopg.Connection[DictRow], token: str | None) -> AuthUser | None:
    """The user behind a cookie token, or ``None`` (missing, unknown, expired)."""
    if not token:
        return None
    row = auth_db.get_live_session_user(conn, token_hash(token))
    return _user(row) if row is not None else None


def end_session(conn: psycopg.Connection[DictRow], token: str | None) -> str | None:
    """Invalidate the session behind a cookie token; returns its sign-in method (``None`` when unknown)."""
    if not token:
        return None
    return auth_db.delete_session(conn, token_hash(token))


# --- local accounts ------------------------------------------------------------------
# Accounts are created only by ``services.invitations.complete_setup``; there is no public registration.


DISABLED_MESSAGE = "This account has been disabled. Contact an AI4DRR Chatbot administrator."


def is_disabled(row: DictRow) -> bool:
    """A suspended account (``users.disabled_at`` set) can neither sign in nor hold a session."""
    return row["disabled_at"] is not None


def authenticate_local(conn: psycopg.Connection[DictRow], email: str, password: str) -> AuthUser:
    """Verify email + password; raises ``AuthenticationError`` with one generic message.

    A disabled account is told so (403) only after the password verified, so
    the status is never revealed to someone who does not hold the credentials.
    """
    row = auth_db.get_user_by_email(conn, email)
    if row is None or not verify_password(password, row["password_hash"]):
        raise AuthenticationError("Invalid email or password")
    if is_disabled(row):
        logger.info("local sign-in refused: user %s is disabled", row["id"])
        raise ForbiddenError(DISABLED_MESSAGE)
    return _user(row)


# --- Entra ID ------------------------------------------------------------------------


@dataclass(frozen=True)
class EntraIdentity:
    """The claims this app uses from an Entra ID token."""

    oid: str
    email: str
    name: str | None
    first_name: str | None
    last_name: str | None


def identity_from_claims(claims: dict[str, object]) -> EntraIdentity:
    """Map validated id_token claims to the fields the users table keeps.

    ``oid`` is the stable identifier (B2C and Entra alike). The email is the
    ``email`` claim when present, else the first of B2C's ``emails`` list,
    else ``preferred_username`` (the UPN). B2C user flows issue no ``name``,
    so it is assembled from given/family name. Missing oid or email → refused.
    """
    oid = str(claims.get("oid") or "")
    emails = claims.get("emails")
    from_list = emails[0] if isinstance(emails, list) and emails else None
    email = str(claims.get("email") or from_list or claims.get("preferred_username") or "").strip()
    if not oid or "@" not in email:
        raise AuthenticationError("The Microsoft account did not provide a usable identity (oid/email)")
    first = str(claims["given_name"]).strip() if claims.get("given_name") else None
    last = str(claims["family_name"]).strip() if claims.get("family_name") else None
    name = (
        str(claims["name"]).strip() if claims.get("name") else " ".join(x for x in (first, last) if x) or None
    )
    return EntraIdentity(oid=oid, email=email, name=name, first_name=first, last_name=last)


def sign_in_entra(conn: psycopg.Connection[DictRow], identity: EntraIdentity) -> AuthUser:
    """Link or create the user for an Entra identity; a disabled account is refused (403), no session."""
    row = auth_db.upsert_entra_user(
        conn, identity.oid, identity.email, identity.name, identity.first_name, identity.last_name
    )
    if is_disabled(row):
        logger.info("entra sign-in refused: user %s is disabled", row["id"])
        raise ForbiddenError(DISABLED_MESSAGE)
    logger.info("entra sign-in: user %s", row["id"])
    return _user(row)


def current_user_from_row(row: DictRow) -> AuthUser:
    """Public wrapper used by the API dependency."""
    return _user(row)


# --- administrators ------------------------------------------------------------------


def list_admin_users(conn: psycopg.Connection[DictRow]) -> list[AdminUser]:
    """The management page's user list."""
    return [
        AdminUser(
            id=str(r["id"]),
            email=r["email"],
            name=r["name"],
            first_name=r["first_name"],
            last_name=r["last_name"],
            department=r["department"],
            is_admin=bool(r["is_admin"]),
            has_password=bool(r["has_password"]),
            entra_linked=bool(r["entra_linked"]),
            disabled=r["disabled_at"] is not None,
            disabled_at=r["disabled_at"],
            created_at=r["created_at"],
        )
        for r in auth_db.list_users(conn)
    ]
