"""Chatbot Management: the admin API surface and the two admin pages.

Authorisation is the ``get_current_admin`` dependency (``api.deps``): the normal
session cookie plus ``users.is_admin``. There is no admin user table, cookie or
password system. The management page's HTML lives in ``app/pages/admin`` —
outside the static mount — so it is only ever served through the guard.
"""

from __future__ import annotations

import logging
from pathlib import Path

import psycopg
from fastapi import APIRouter, Depends, Query
from fastapi.responses import FileResponse, RedirectResponse, Response
from psycopg.rows import DictRow

from app.api.deps import CurrentAdmin, get_app_settings, get_db, get_mailer, get_optional_user
from app.api.routes.auth import require_local_accounts
from app.config import Settings
from app.errors import AppError
from app.integrations.mail import Mailer
from app.schemas.auth import AdminUser, AdminUserDetail, AuthUser, InvitationCreate, InvitationInfo
from app.services import admin_users as user_service
from app.services import auth as auth_service
from app.services import invitations as invitation_service

logger = logging.getLogger(__name__)

PAGES = Path(__file__).resolve().parents[2] / "pages" / "admin"

api = APIRouter(prefix="/api/admin", tags=["Admin"])
pages = APIRouter(tags=["Admin pages"], include_in_schema=False)


# --- protected API ---------------------------------------------------------------


@api.get("/me", response_model=AuthUser)
def admin_me(admin: CurrentAdmin) -> AuthUser:
    """The signed-in administrator (401 without a session, 403 for a non-admin)."""
    return admin


@api.get("/users", response_model=list[AdminUser])
def admin_users(admin: CurrentAdmin, conn: psycopg.Connection[DictRow] = Depends(get_db)) -> list[AdminUser]:
    """Every user account (enabled and disabled) — the Users list of the management page."""
    return auth_service.list_admin_users(conn)


@api.get("/users/{user_id}", response_model=AdminUserDetail)
def admin_user_detail(
    user_id: str, admin: CurrentAdmin, conn: psycopg.Connection[DictRow] = Depends(get_db)
) -> AdminUserDetail:
    """The View panel: profile, status, sign-in methods, safe timestamps, activity counts (404 if unknown)."""
    return user_service.get_detail(conn, user_id)


@api.post("/users/{user_id}/disable", response_model=AdminUser)
def admin_user_disable(
    user_id: str, admin: CurrentAdmin, conn: psycopg.Connection[DictRow] = Depends(get_db)
) -> AdminUser:
    """Suspend the account and end all its sessions (409 for yourself or the last enabled administrator)."""
    return user_service.disable(conn, user_id, admin)


@api.post("/users/{user_id}/enable", response_model=AdminUser)
def admin_user_enable(
    user_id: str, admin: CurrentAdmin, conn: psycopg.Connection[DictRow] = Depends(get_db)
) -> AdminUser:
    """Lift the suspension; the person signs in again (no session is restored)."""
    return user_service.enable(conn, user_id, admin)


# --- invitations -------------------------------------------------------------------


def _issue_and_send(
    conn: psycopg.Connection[DictRow],
    issued: invitation_service.IssuedInvitation,
    mailer: Mailer,
    settings: Settings,
) -> InvitationInfo:
    """Send the e-mail for a just-created invitation; on failure remove it again and report 502/503.

    The row was written in the request's transaction, which is still open:
    deleting it here means a failed send leaves no trace of an invitation
    that never reached anyone.
    """
    try:
        invitation_service.send_invitation(
            mailer, issued, settings.app_base_url, settings.invitation_ttl_hours
        )
    except AppError:
        invitation_service.discard_unsent(conn, issued.info.id)
        raise
    return issued.info


@api.get("/invitations", response_model=list[InvitationInfo])
def list_invitations(
    admin: CurrentAdmin, conn: psycopg.Connection[DictRow] = Depends(get_db)
) -> list[InvitationInfo]:
    """Open invitations (pending and expired), newest first — the Pending list."""
    return invitation_service.list_pending(conn)


@api.post("/invitations", response_model=InvitationInfo, status_code=201)
def create_invitation(
    body: InvitationCreate,
    admin: CurrentAdmin,
    _flag: None = Depends(require_local_accounts),
    settings: Settings = Depends(get_app_settings),
    mailer: Mailer = Depends(get_mailer),
    conn: psycopg.Connection[DictRow] = Depends(get_db),
) -> InvitationInfo:
    """Invite an e-mail address: store it, send the setup link (24 h). 403 in Microsoft-only mode."""
    issued = invitation_service.invite(conn, body.email, admin, settings.invitation_ttl_hours)
    return _issue_and_send(conn, issued, mailer, settings)


@api.post("/invitations/{invitation_id}/resend", response_model=InvitationInfo)
def resend_invitation(
    invitation_id: str,
    admin: CurrentAdmin,
    _flag: None = Depends(require_local_accounts),
    settings: Settings = Depends(get_app_settings),
    mailer: Mailer = Depends(get_mailer),
    conn: psycopg.Connection[DictRow] = Depends(get_db),
) -> InvitationInfo:
    """Replace the token and expiry and send a fresh e-mail; the old link stops working."""
    issued = invitation_service.resend(conn, invitation_id, settings.invitation_ttl_hours)
    return _issue_and_send(conn, issued, mailer, settings)


@api.post("/invitations/{invitation_id}/revoke", response_model=InvitationInfo)
def revoke_invitation(
    invitation_id: str, admin: CurrentAdmin, conn: psycopg.Connection[DictRow] = Depends(get_db)
) -> InvitationInfo:
    """Close an open invitation."""
    return invitation_service.revoke(conn, invitation_id)


# --- pages -----------------------------------------------------------------------


@pages.get("/admin/login")
def admin_login_page() -> Response:
    """The management doorway: public HTML; the page itself asks the API who is signed in."""
    return FileResponse(PAGES / "login.html", media_type="text/html")


@pages.get("/admin/users")
def admin_users_page(
    user: AuthUser | None = Depends(get_optional_user), denied: bool = Query(default=False)
) -> Response:
    """The management page — served only to administrators.

    Not signed in → the doorway; signed in but not an administrator → the
    doorway in its access-denied state. Knowing the URL yields nothing.
    """
    if user is None:
        return RedirectResponse("/admin/login", status_code=302)
    if not user.is_admin:
        return RedirectResponse("/admin/login?denied=1", status_code=302)
    return FileResponse(PAGES / "users.html", media_type="text/html")


@pages.get("/admin")
def admin_root() -> Response:
    """Convenience: /admin → the management page (which applies the guard)."""
    return RedirectResponse("/admin/users", status_code=302)
