"""Application factory.

``create_app()`` wires settings, logging, middleware, error handlers, routers
and the static frontend. ``app`` at module level is what Uvicorn imports
(``uvicorn app.main:app``).
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api.routes import admin, auth, chat, health, sessions
from app.chat.pipeline import ChatPipeline, select_pipeline
from app.config import (
    ENTRA_CALLBACK_PATH,
    Settings,
    entra_configured,
    entra_mode,
    entra_redirect_path,
    entra_redirect_uri,
    get_settings,
)
from app.db.migrate import try_apply_migrations
from app.errors import AppError
from app.integrations.entra import EntraClient, build_entra_client
from app.integrations.mail import Mailer, build_mailer
from app.log import configure_logging

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Startup / shutdown hooks. External clients will be created here later."""
    settings: Settings = app.state.settings
    logger.info("starting UNDRR Chatbot API (db configured: %s)", settings.database_url is not None)
    try_apply_migrations(settings.database_url)
    logger.info(
        "auth: microsoft=%s local=%s cookie_secure=%s base_url=%s mail=%s",
        (entra_mode(settings) or "off") if entra_configured(settings) else "off",
        settings.local_auth_enabled,
        settings.session_cookie_secure,
        settings.app_base_url,
        settings.email_transport,
    )
    if entra_configured(settings):
        redirect = entra_redirect_uri(settings)
        logger.info("microsoft sign-in: policy=%s redirect_uri=%s", settings.b2c_policy or "-", redirect)
        if _origin_of(redirect) != _origin_of(settings.app_base_url):
            logger.warning(
                "microsoft sign-in: the redirect URI (%s) is not on APP_BASE_URL (%s) — Microsoft will send "
                "the browser there, not to this app, unless a proxy maps it",
                redirect,
                settings.app_base_url,
            )
    yield
    logger.info("shutting down UNDRR Chatbot API")


def _error_body(status_code: int, detail: object) -> JSONResponse:
    return JSONResponse(status_code=status_code, content={"detail": detail, "status_code": status_code})


def _origin_of(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}".lower()


def create_app(
    settings: Settings | None = None,
    chat_pipeline: ChatPipeline | None = None,
    entra_client: EntraClient | None = None,
    mailer: Mailer | None = None,
) -> FastAPI:
    """Build the FastAPI application.

    Tests pass their own ``Settings`` and, for ``/api/chat``, their own
    ``chat_pipeline`` (and an ``entra_client`` fake); otherwise the pipeline
    is chosen from ``CHAT_PIPELINE`` and the Entra client from ``ENTRA_*``.
    """
    settings = settings or get_settings()
    configure_logging(settings.log_level)
    pipeline = chat_pipeline or select_pipeline(settings)
    entra = entra_client or build_entra_client(settings)
    mail = mailer or build_mailer(settings)

    app = FastAPI(
        title="UNDRR Chatbot API",
        description="AI4DRR Chatbot backend — disaster risk reduction knowledge assistant.",
        version="0.1.0",
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.chat_pipeline = pipeline
    app.state.entra_client = entra
    app.state.mailer = mail

    # Same-origin app: CORS only when origins are configured explicitly. With
    # cookie authentication a wildcard would be unsafe, so it is never assumed.
    if settings.cors_allow_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=list(settings.cors_allow_origins),
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    # CSRF: the session cookie is SameSite=Lax, which already keeps it off
    # cross-site POSTs in current browsers; as a second layer every unsafe
    # request to /api that carries an Origin header must come from this app's
    # own origin (or a configured CORS origin).
    trusted_origins = {_origin_of(settings.app_base_url), *(o.lower() for o in settings.cors_allow_origins)}

    @app.middleware("http")
    async def _reject_foreign_origins(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        if request.method in {"POST", "PUT", "PATCH", "DELETE"} and request.url.path.startswith("/api/"):
            origin = request.headers.get("origin")
            own = f"{request.url.scheme}://{request.headers.get('host', '')}".lower()
            if origin and origin.lower() not in trusted_origins and origin.lower() != own:
                return _error_body(403, "Cross-origin request refused")
        return await call_next(request)

    # --- error boundary: one JSON shape for every error --------------------
    @app.exception_handler(AppError)
    async def _app_error(_: Request, exc: AppError) -> JSONResponse:
        return _error_body(exc.status_code, exc.detail)

    # Registered on Starlette's base class so router 404/405s and FastAPI's
    # HTTPException (a subclass) both get the same shape.
    @app.exception_handler(StarletteHTTPException)
    async def _http_error(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        return _error_body(exc.status_code, exc.detail)

    @app.exception_handler(Exception)
    async def _unhandled(_: Request, exc: Exception) -> JSONResponse:
        logger.error("unhandled error: %s", exc, exc_info=exc)
        return _error_body(500, "Internal server error")

    # --- routers -------------------------------------------------------------
    app.include_router(health.router)
    app.include_router(auth.router)
    app.include_router(sessions.router)
    app.include_router(chat.router)
    app.include_router(admin.api)
    app.include_router(admin.pages)  # /admin/login, /admin/users — before the static mount
    app.include_router(auth.pages)  # /account-setup, /reset-password
    # The Microsoft callback is canonical at /api/auth/entra/callback. When the registered redirect
    # URI (AZURE_B2C_REDIRECT_URI) uses another path on this app, serve the same handler there too,
    # so one registration works without a second implementation.
    callback_path = entra_redirect_path(settings)
    if callback_path != ENTRA_CALLBACK_PATH:
        app.add_api_route(callback_path, auth.entra_callback, methods=["GET"], include_in_schema=False)

    # --- static frontend -------------------------------------------------------
    frontend = Path(settings.frontend_dir)
    if (frontend / "index.html").is_file():
        app.mount("/", StaticFiles(directory=str(frontend), html=True), name="frontend")
        logger.info("serving frontend from %s", frontend.resolve())

    return app


app = create_app()
