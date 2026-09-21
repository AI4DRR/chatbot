"""Application settings.

All environment access happens here, once, at startup. Modules receive a
``Settings`` instance (or the specific values they need) instead of calling
``os.getenv`` themselves. Variable names are the canonical ones documented in
``.env.example``; the legacy ``AZURE_OPENAI_KEY`` / ``AZURE_API_VERSION``
spellings used by the old ``api.py`` are deliberately not read here — that
compatibility decision belongs to the backend migration.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer, got {raw!r}") from exc


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be a number, got {raw!r}") from exc


@dataclass(frozen=True)
class Settings:
    """Immutable snapshot of configuration for one process."""

    # Runtime
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    log_level: str = "INFO"
    cors_allow_origins: tuple[str, ...] = ()  # empty = same-origin only

    # PostgreSQL (container-side URL is canonical; see .env.example)
    database_url: str | None = None

    # Azure OpenAI — canonical names for the backend migration
    azure_openai_endpoint: str | None = None
    azure_openai_api_key: str | None = None
    azure_openai_api_version: str = "2024-02-15-preview"
    azure_openai_chat_deployment: str | None = None

    # Azure AI Search
    azure_search_endpoint: str | None = None
    azure_search_key: str | None = None
    azure_search_index: str | None = None
    azure_search_semantic_config: str = "semantic-config"
    kb_top_k: int = 40  # candidate chunks asked from the index (semantic ranking allows up to 50)

    # Optional Azure AI Foundry (Bing grounding) for online retrieval
    azure_foundry_endpoint: str | None = None
    azure_foundry_api_key: str | None = None
    azure_foundry_deployment: str = "gpt-4o"
    azure_foundry_api_version: str | None = None

    # Cost tracking (USD per 1K tokens; 0 disables the estimate)
    azure_cost_input_per_1k: float = 0.0
    azure_cost_output_per_1k: float = 0.0

    # Admin dashboard
    admin_password: str = "change-me-in-production"

    # Authentication (see docs/architecture.md §6). The public URL the browser
    # uses for this app; Entra redirects and the CSRF origin check derive from it.
    app_base_url: str = "http://localhost:8084"
    # Microsoft sign-in (primary). Two shapes, never both:
    #   Azure AD B2C / Entra External ID: AZURE_B2C_TENANT_NAME + AZURE_B2C_POLICY (the sign-up/
    #     sign-in user flow) + AZURE_B2C_CLIENT_ID/SECRET, optional reset / profile policies,
    #     AZURE_B2C_REDIRECT_URI (the registered callback) and AZURE_B2C_LOGOUT_REDIRECT_URI;
    #   plain Entra ID tenant: ENTRA_TENANT_ID + ENTRA_CLIENT_ID/SECRET.
    # Without client id + secret + a tenant the Microsoft button is reported as not configured.
    entra_tenant_id: str | None = None
    entra_client_id: str | None = None
    entra_client_secret: str | None = None
    entra_redirect_path: str = "/api/auth/entra/callback"
    entra_redirect_uri: str | None = None  # full registered redirect URI; default APP_BASE_URL + path
    b2c_tenant_name: str | None = None
    b2c_policy: str | None = None
    b2c_password_reset_policy: str | None = None
    b2c_edit_profile_policy: str | None = None
    b2c_logout_redirect_uri: str | None = None
    # Local email/password sign-in (secondary, feature-flagged; off by default).
    local_auth_enabled: bool = False
    # Invitation-only local accounts: link validity and where the setup page lives.
    invitation_ttl_hours: int = 24
    password_reset_ttl_minutes: int = 60
    # Transactional e-mail (invitations). EMAIL_TRANSPORT: "smtp" | "file" (dev: .eml files) | "none".
    email_from: str = "AI4DRR Chatbot <no-reply@example.org>"
    email_transport: str = "none"
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_user: str | None = None
    smtp_pass: str | None = None
    smtp_secure: bool = False  # True = implicit TLS (SMTPS); False = STARTTLS when the server offers it
    mail_file_dir: str = "logs/mail"
    # Browser session cookie
    session_cookie_name: str = "ai4drr_session"
    session_cookie_secure: bool = True  # False only for plain-http development
    session_ttl_hours: int = 168

    # Frontend: static files served at "/" when the directory exists
    frontend_dir: str = "frontend"

    chat_debug: bool = False

    # Which answer pipeline /api/chat uses: "unconfigured" (refuses with 503),
    # "stub" (dev/test seam, never real answers) or "rag" (Azure AI Search +
    # Azure OpenAI, see app/chat/rag.py).
    chat_pipeline: str = "unconfigured"

    # RAG pipeline limits (conservative defaults; see .env.example)
    chat_kb_max_documents: int = 8  # distinct documents offered to the model (after grouping chunks)
    chat_history_messages: int = 6  # recent raw messages given to the model
    chat_kb_excerpt_chars: int = 6000  # per retrieved document (its grouped chunks)
    chat_kb_context_chars: int = 40000  # all excerpts together
    # gpt-5 reasons before answering; 4000 produced empty answers (502) on long turns
    chat_max_completion_tokens: int = 8000
    chat_completion_timeout_seconds: float = 90.0
    chat_search_timeout_seconds: float = 20.0
    # Optional gpt-5 reasoning control for the answer call ("minimal" | "low" | "medium" | "high").
    # Sent only when set; unset keeps the proven request shape. Validated on gpt-5 (Phase 3).
    chat_reasoning_effort: str | None = None

    # Internal LLM (query routing, subject, memory summarisation). Each connection
    # value falls back to the AZURE_OPENAI_* value when unset, so by default the
    # internal model is the same deployment as the answer model; a cheaper
    # deployment is a configuration change only (see app/chat/routing.py).
    internal_llm_endpoint: str | None = None
    internal_llm_api_key: str | None = None
    internal_llm_api_version: str | None = None
    internal_llm_deployment: str | None = None
    internal_llm_timeout_seconds: float = 30.0
    # small visible output, but gpt-5 reasons first (measured 0.7-2.8k per call)
    internal_llm_max_completion_tokens: int = 3000
    # Optional reasoning control / JSON mode for the internal calls; sent only when set (Phase 3).
    internal_llm_reasoning_effort: str | None = None
    internal_llm_json_mode: bool = False

    # Conversation memory (Phase 2): compact older messages into chat_sessions.memory_summary
    chat_memory_compact_after: int = 12  # only sessions with more messages than this are compacted
    chat_memory_compact_every: int = 6  # ...and only when this many messages are uncovered and out of window


ENTRA_CALLBACK_PATH = "/api/auth/entra/callback"


def entra_mode(settings: Settings) -> str | None:
    """``"b2c"`` (AZURE_B2C_TENANT_NAME set), ``"entra"`` (ENTRA_TENANT_ID set) or ``None``."""
    if settings.b2c_tenant_name:
        return "b2c"
    if settings.entra_tenant_id:
        return "entra"
    return None


def entra_configured(settings: Settings) -> bool:
    """True when a tenant (B2C or plain Entra) plus client id and secret are present."""
    return bool(entra_mode(settings) and settings.entra_client_id and settings.entra_client_secret)


def validate_entra_settings(settings: Settings) -> None:
    """Refuse half-configured Microsoft sign-in with a clear message (called at app creation).

    Nothing configured is fine (the button is shown disabled); a tenant without
    credentials, credentials without a tenant, both tenant shapes at once, or
    B2C without its sign-in policy are configuration errors.
    """
    has_creds = bool(settings.entra_client_id or settings.entra_client_secret)
    if settings.b2c_tenant_name and settings.entra_tenant_id:
        raise ValueError(
            "Microsoft sign-in: set either AZURE_B2C_TENANT_NAME (B2C) or ENTRA_TENANT_ID (plain Entra ID), "
            "not both"
        )
    mode = entra_mode(settings)
    if mode is None:
        if has_creds:
            raise ValueError(
                "Microsoft sign-in: a client id/secret is set but no tenant — set AZURE_B2C_TENANT_NAME "
                "(+ AZURE_B2C_POLICY) or ENTRA_TENANT_ID"
            )
        return
    if not (settings.entra_client_id and settings.entra_client_secret):
        prefix = "AZURE_B2C" if mode == "b2c" else "ENTRA"
        raise ValueError(
            f"Microsoft sign-in: {prefix}_CLIENT_ID and {prefix}_CLIENT_SECRET are both required"
        )
    if mode == "b2c" and not settings.b2c_policy:
        raise ValueError("Microsoft sign-in: AZURE_B2C_POLICY (the sign-up/sign-in user flow) is required")
    if not entra_redirect_uri(settings).lower().startswith(("https://", "http://localhost")):
        raise ValueError(
            "Microsoft sign-in: the redirect URI must be https (http is allowed for localhost only)"
        )


def entra_redirect_uri(settings: Settings) -> str:
    """The exact redirect URI sent to Microsoft — it must be registered on the app registration."""
    if settings.entra_redirect_uri:
        return settings.entra_redirect_uri
    return settings.app_base_url + settings.entra_redirect_path


def entra_redirect_path(settings: Settings) -> str:
    """The path of the redirect URI on this app (where the callback route must be served)."""
    from urllib.parse import urlsplit

    return urlsplit(entra_redirect_uri(settings)).path or ENTRA_CALLBACK_PATH


def b2c_authority(settings: Settings, policy: str) -> str:
    """The B2C authority for one user flow (MSAL derives every endpoint from its metadata)."""
    tenant = str(settings.b2c_tenant_name)
    return f"https://{tenant}.b2clogin.com/{tenant}.onmicrosoft.com/{policy}"


def b2c_logout_endpoint(settings: Settings) -> str:
    """The sign-in user flow's end-session endpoint (the shape its OpenID metadata advertises)."""
    return b2c_authority(settings, str(settings.b2c_policy)) + "/oauth2/v2.0/logout"


def entra_authority(settings: Settings, policy: str | None = None) -> str:
    """The authority to use: a B2C user flow, or the plain Entra tenant."""
    if entra_mode(settings) == "b2c":
        return b2c_authority(settings, policy or str(settings.b2c_policy))
    return f"https://login.microsoftonline.com/{settings.entra_tenant_id}"


def load_settings() -> Settings:
    """Build ``Settings`` from the process environment."""
    # Same-origin app: no CORS unless origins are configured explicitly (auth cookies make "*" unsafe).
    origins = os.getenv("CORS_ALLOW_ORIGINS", "")
    return Settings(
        api_host=os.getenv("API_HOST", "0.0.0.0"),
        api_port=_env_int("API_PORT", 8000),
        log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
        cors_allow_origins=tuple(o.strip() for o in origins.split(",") if o.strip()),
        database_url=os.getenv("DATABASE_URL") or None,
        azure_openai_endpoint=os.getenv("AZURE_OPENAI_ENDPOINT") or None,
        azure_openai_api_key=os.getenv("AZURE_OPENAI_API_KEY") or None,
        azure_openai_api_version=os.getenv("AZURE_OPENAI_API_VERSION", "2024-02-15-preview"),
        azure_openai_chat_deployment=os.getenv("AZURE_OPENAI_CHAT_DEPLOYMENT") or None,
        azure_search_endpoint=os.getenv("AZURE_SEARCH_ENDPOINT") or None,
        azure_search_key=os.getenv("AZURE_SEARCH_KEY") or None,
        azure_search_index=os.getenv("AZURE_SEARCH_INDEX") or None,
        azure_search_semantic_config=os.getenv("AZURE_SEARCH_SEMANTIC_CONFIG", "semantic-config"),
        kb_top_k=_env_int("KB_TOP_K", 40),
        azure_foundry_endpoint=os.getenv("AZURE_FOUNDRY_ENDPOINT") or None,
        azure_foundry_api_key=os.getenv("AZURE_FOUNDRY_API_KEY") or None,
        azure_foundry_deployment=os.getenv("AZURE_FOUNDRY_DEPLOYMENT", "gpt-4o"),
        azure_foundry_api_version=os.getenv("AZURE_FOUNDRY_API_VERSION") or None,
        azure_cost_input_per_1k=float(os.getenv("AZURE_COST_INPUT_PER_1K", "0") or 0),
        azure_cost_output_per_1k=float(os.getenv("AZURE_COST_OUTPUT_PER_1K", "0") or 0),
        admin_password=os.getenv("ADMIN_PASSWORD", "change-me-in-production"),
        app_base_url=(os.getenv("APP_BASE_URL") or "http://localhost:8084").rstrip("/"),
        entra_tenant_id=os.getenv("ENTRA_TENANT_ID") or None,
        # B2C deployments name the credentials AZURE_B2C_*; a plain tenant uses ENTRA_*. Same fields.
        entra_client_id=os.getenv("AZURE_B2C_CLIENT_ID") or os.getenv("ENTRA_CLIENT_ID") or None,
        entra_client_secret=os.getenv("AZURE_B2C_CLIENT_SECRET") or os.getenv("ENTRA_CLIENT_SECRET") or None,
        entra_redirect_path=os.getenv("ENTRA_REDIRECT_PATH") or "/api/auth/entra/callback",
        entra_redirect_uri=(os.getenv("AZURE_B2C_REDIRECT_URI") or "").strip() or None,
        b2c_tenant_name=(os.getenv("AZURE_B2C_TENANT_NAME") or "").strip() or None,
        b2c_policy=(os.getenv("AZURE_B2C_POLICY") or "").strip() or None,
        b2c_password_reset_policy=(os.getenv("AZURE_B2C_PASSWORD_RESET_POLICY") or "").strip() or None,
        b2c_edit_profile_policy=(os.getenv("AZURE_B2C_EDIT_PROFILE_POLICY") or "").strip() or None,
        b2c_logout_redirect_uri=(os.getenv("AZURE_B2C_LOGOUT_REDIRECT_URI") or "").strip() or None,
        local_auth_enabled=_env_bool("LOCAL_AUTH_ENABLED", False),
        invitation_ttl_hours=_env_int("INVITATION_TTL_HOURS", 24),
        password_reset_ttl_minutes=_env_int("PASSWORD_RESET_TTL_MINUTES", 60),
        email_from=os.getenv("EMAIL_FROM") or "AI4DRR Chatbot <no-reply@example.org>",
        email_transport=(os.getenv("EMAIL_TRANSPORT") or "none").strip().lower(),
        smtp_host=os.getenv("SMTP_HOST") or None,
        smtp_port=_env_int("SMTP_PORT", 587),
        smtp_user=os.getenv("SMTP_USER") or None,
        smtp_pass=os.getenv("SMTP_PASS") or None,
        smtp_secure=_env_bool("SMTP_SECURE", False),
        mail_file_dir=os.getenv("MAIL_FILE_DIR") or "logs/mail",
        session_cookie_name=os.getenv("SESSION_COOKIE_NAME") or "ai4drr_session",
        session_cookie_secure=_env_bool("SESSION_COOKIE_SECURE", True),
        session_ttl_hours=_env_int("SESSION_TTL_HOURS", 168),
        frontend_dir=os.getenv("FRONTEND_DIR", "frontend"),
        chat_debug=_env_bool("CHAT_DEBUG", False),
        chat_pipeline=os.getenv("CHAT_PIPELINE", "unconfigured").strip().lower(),
        chat_kb_max_documents=_env_int("CHAT_KB_MAX_DOCUMENTS", 8),
        chat_history_messages=_env_int("CHAT_HISTORY_MESSAGES", 6),
        chat_kb_excerpt_chars=_env_int("CHAT_KB_EXCERPT_CHARS", 6000),
        chat_kb_context_chars=_env_int("CHAT_KB_CONTEXT_CHARS", 40000),
        chat_max_completion_tokens=_env_int("CHAT_MAX_COMPLETION_TOKENS", 8000),
        chat_completion_timeout_seconds=_env_float("CHAT_COMPLETION_TIMEOUT_SECONDS", 90.0),
        chat_search_timeout_seconds=_env_float("CHAT_SEARCH_TIMEOUT_SECONDS", 20.0),
        chat_reasoning_effort=os.getenv("CHAT_REASONING_EFFORT", "").strip().lower() or None,
        internal_llm_endpoint=os.getenv("INTERNAL_LLM_ENDPOINT") or None,
        internal_llm_api_key=os.getenv("INTERNAL_LLM_API_KEY") or None,
        internal_llm_api_version=os.getenv("INTERNAL_LLM_API_VERSION") or None,
        internal_llm_deployment=os.getenv("INTERNAL_LLM_DEPLOYMENT") or None,
        internal_llm_timeout_seconds=_env_float("INTERNAL_LLM_TIMEOUT_SECONDS", 30.0),
        internal_llm_max_completion_tokens=_env_int("INTERNAL_LLM_MAX_COMPLETION_TOKENS", 3000),
        internal_llm_reasoning_effort=os.getenv("INTERNAL_LLM_REASONING_EFFORT", "").strip().lower() or None,
        internal_llm_json_mode=_env_bool("INTERNAL_LLM_JSON_MODE", False),
        chat_memory_compact_after=_env_int("CHAT_MEMORY_COMPACT_AFTER", 12),
        chat_memory_compact_every=_env_int("CHAT_MEMORY_COMPACT_EVERY", 6),
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Process-wide settings (cached). Tests call ``get_settings.cache_clear()``."""
    return load_settings()
