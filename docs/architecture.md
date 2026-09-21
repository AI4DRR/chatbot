# Architecture and project structure

**Status:** structure established 2026-09-19; backend migration in progress.
Implemented: `app.main`, `app.config`, `app.log`, `app.errors`, `app.db.{connection,
users,sessions,messages}`, `app.services.sessions`, `app.schemas.{health,auth,
sessions,chat.SourceItem}` and the routes `/health`, `/api/users/identify`,
`/api/login`, `/api/sessions*` (phase 1, 2026-09-19) and the `/api/chat`
lifecycle — validate, store question, call the pipeline boundary, store answer +
metadata, return `ChatResponse` (phase 2A, 2026-09-19), and the minimal
grounded pipeline `CHAT_PIPELINE=rag` (phase 2C, 2026-09-20): one Azure AI
Search semantic query on the current question, one Azure OpenAI completion over
the retrieved excerpts (grouped per document) plus recent raw history, `[n]`
citations resolved to a cited-only `sources` list, `mode="SYNTHESIS"`. The
production default still refuses with 503 (`unconfigured_pipeline`) and
`CHAT_PIPELINE=stub` selects an unmistakably non-AI stub for dev/test. The
admin routes and the rest of the legacy pipeline (online retrieval, intent,
entities, LIST mode, memory) are not ported.

Phase 1 of `docs/chatbot-flow-improvement-plan.md` (2026-09-20) split the
retrieval candidate count from the document cap — `KB_TOP_K` (now 40) is what
the index is asked for, `CHAT_KB_MAX_DOCUMENTS` (8) how many distinct
documents reach the model after chunk grouping — and added the regression
harness `tools/regression_chat.py` (host-side tool; cases in
`tests/regression/cases.json`; artifacts under `logs/regression/`). The
pipeline's counters (`retrieved/selected/history/cited`) are stored in the
assistant message `metadata.stats` for that harness; nothing reads them back.

Phase 2 (2026-09-20) added the conversation layer on an **internal model**
(`INTERNAL_LLM_*`, every value defaulting to `AZURE_OPENAI_*`, so the same
GPT-5 deployment until pointed elsewhere): `app.chat.routing` turns the
question plus recent context into the search query and decides whether the
active subject changed (`chat_sessions.subject`, written only then);
`app.chat.memory` folds messages older than the raw window into
`chat_sessions.memory_summary` / `memory_turns` when `services.sessions.
MemoryPolicy` says compaction is due. The raw window is every message the
summary does not cover (≥ `CHAT_HISTORY_MESSAGES`, ≤ `CHAT_MEMORY_COMPACT_AFTER`).
Routing and compaction *degrade* (question as typed / no compaction, logged,
`metadata.routing.fallback`) rather than fail the turn. `token_usage` and
`cost_usd` are turn totals; `metadata.usage` holds the per-call breakdown.

Phase 3 (2026-09-20): an answer that cites no excerpt is prefixed by the
pipeline with `prompts.UNSOURCED_NOTICE` (a coverage fact, not a quality
verdict); optional `CHAT_REASONING_EFFORT` / `INTERNAL_LLM_REASONING_EFFORT` /
`INTERNAL_LLM_JSON_MODE` are sent only when set (validated on gpt-5; the
operating point in `.env` is `low`/`low`, which halved turn latency).

`/api/chat` runs **two short transactions with no connection held across the
Azure calls**: read (validate session, context, recent history) → close →
pipeline → open → write (question, answer + metadata, session `updated_at`) →
commit. A pipeline failure therefore persists nothing; a write failure rolls
back the whole turn.

## 1 Layout

```
chatbot/
├── app/                      Python package (uvicorn app.main:app)
│   ├── main.py               create_app(): settings, logging, CORS, error handlers, routers, static frontend
│   ├── config.py             Settings dataclass; the ONLY place that reads os.environ
│   ├── log.py                logging configuration (stdlib logging)
│   ├── errors.py             AppError hierarchy → JSON {"detail","status_code"}
│   ├── api/
│   │   ├── deps.py           FastAPI dependencies (settings, db connection, admin token…)
│   │   └── routes/           one APIRouter per resource: health | auth | chat | sessions | admin
│   ├── schemas/              pydantic request/response models = the public API contract
│   ├── db/                   psycopg 3 access: connection.py | users | sessions | messages | analytics
│   ├── services/             sessions (login/identify/history/save_turn) | memory
│   ├── chat/                 RAG pipeline: pipeline | query_analysis | intent | entities | ranking | prompts | sources
│   └── integrations/         azure_openai | azure_search | online/{retrieval,fetchers,trusted_sources,foundry}
├── frontend/                 production frontend (static; served at "/" by FastAPI when index.html exists)
├── templates/                V1/V2 UX comparison templates — design only, never served or shipped
├── tests/                    pytest; conftest builds the app with test Settings (no DB, no Azure); regression/cases.json for tools/regression_chat.py
├── tools/                    host-side operator tools (regression_chat.py); not in the image
├── postgres_init/            001_init.sql — schema, unchanged from the legacy project
├── docs/                     this file, coding-standards.md, infrastructure-baseline.md, standalone-frontend-assessment.md
├── Dockerfile, docker-compose.yml (dev), docker-compose.prod.yml, .dockerignore
├── requirements.txt          inherited runtime baseline (unpinned; migration gate — do not edit casually)
├── requirements-dev.txt      ruff, mypy, pytest, httpx (+ runtime)
├── requirements.resolved-2026-09-19.txt   reference-only pip freeze of one build
├── pyproject.toml            ruff / mypy / pytest configuration
└── Makefile                  help | up | down | ps | logs | ssh | admin  [ENV=dev|prod]  + format | lint | typecheck | test | check | docker-check
```

## 2 Boundaries (what may import what)

```
api.routes ──▶ schemas, services, chat, api.deps, errors
services   ──▶ db, schemas (data shapes), errors
chat       ──▶ integrations, schemas (TokenUsage/SourceItem), errors
integrations ─▶ config (Settings passed in), errors
db         ──▶ nothing in app except errors
main       ──▶ everything (composition root)
```

- **Routes** are thin: parse (schema) → call one service/pipeline function → return a schema. No SQL, no Azure SDK, no business rules.
- **Schemas** are the contract. Names and shapes stay compatible with the legacy API while the frontend is migrated (`docs/standalone-frontend-assessment.md` §4 lists the exact shapes).
- **Services** own the session/user/memory rules that were in `session_manager.py`; they receive a connection and return plain data.
- **Chat** is the pipeline that was `ChatService.handle_chat_turn`. It is pure orchestration over `integrations`; it must stay importable without a database.
- **Integrations** wrap one external system each. They take values from `Settings`, never read `os.environ`, and never know about HTTP routes.
- **db** is the only module with SQL. One connection per request, `with connect(url) as conn:` (commit on success, rollback on error).
- **config** is the only module that reads the environment. Everything else receives `Settings` or explicit values.
- `app.errors` is the only cross-cutting exception vocabulary; `main.py` maps it to HTTP.

Deliberately **not** introduced: repository interfaces, service factories, dependency-injection containers, an ORM, async database access. The legacy code has exactly one implementation of each thing; abstractions come only when a second implementation exists (e.g. a test double that a plain function argument cannot provide).

## 3 Migration map — legacy `datum-drr-chat/src` → `app/`

| Legacy module (lines) | Destination | Notes |
|---|---|---|
| `api.py` (676) — lifespan, CORS, static mount, error handlers | `app/main.py` | done (structure); routers added as ported |
| `api.py` — `/health` | `app/api/routes/health.py` | done |
| `api.py` — `/api/users/identify`, `/api/login` | `app/api/routes/auth.py` | done; contract: `{"user":{…}}`, `{"user":{…},"session_id"}` |
| `api.py` — `/api/chat` | `app/api/routes/chat.py` | done: read transaction (session, topic from the last message's stored metadata, recent history) → pipeline with no connection open → write transaction (`services.sessions.save_turn`: question, answer, `updated_at`); a failed pipeline stores nothing |
| `api.py` — `/api/sessions*` | `app/api/routes/sessions.py` | done, incl. `/resume` (unused by frontends) and `PATCH /title`; unknown/malformed ids → 404 (legacy: 200 / 500), `last_message_at` populated (legacy: always null) |
| `api.py` — admin login + `/api/admin/analytics/*` | `app/api/routes/admin.py` + `deps.require_admin_token` | keep the constant-token behaviour only until a real auth decision |
| `models.py` (133) | `app/schemas/{auth,chat,sessions,admin,health}.py` | split by resource; shapes unchanged. auth, sessions, health and `SourceItem` done; chat request/response and admin pending |
| `chat_db.py` (388) | `app/db/{users,sessions,messages,analytics}.py` | `_connect` → `db/connection.py`, users, sessions (create/get/list/title), messages (read) done; message/memory/context writes with `/api/chat`; analytics with admin |
| `session_manager.py` (318) | `app/services/sessions.py` | done: identify/login/list/history/title/resume, `get_session_context` (+ `MemoryPolicy`, Phase 2), `save_turn` / `save_assistant_message` (metadata `{mode, topic?, sources?, stats?, routing?, usage?, memory?}` on the assistant row only), subject and memory writes |
| `memory_manager.py` (132) | `app/chat/memory.py` | replaced: one internal-model summarisation when due (Phase 2); no facts extraction, no file store |
| `chat_service.py` (611) — `handle_chat_turn` | `app/chat/pipeline.py` + `app/chat/rag.py` | boundary + selection done; `rag` implements the minimal flow (retrieve → `context` → `prompts` → complete → `citations`); steps 2–9 (concept rewrite, intent, LIST mode, regulated-advice refusal, online retrieval, memory) not ported; step 13 cost formula in `app/chat/cost.py` |
| `chat_service.py` — static helpers (`is_topic_change`, `is_followup`, `should_force_list`, `is_regulated_advice`, `is_format_only_request`, `build_effective_query`) | `app/chat/routing.py` | replaced by one internal-model call per turn (Phase 2); the heuristics are not ported |
| `chat_service.py` — `diversify_kb_chunks` | `app/chat/ranking.py` | |
| `chat_service.py` — `_chat` (prompt assembly + completion call) | `app/chat/prompts.py` (text) + `app/integrations/azure_openai.py` (`complete_chat`) | done (SYNTHESIS prompt only; excerpts are numbered, the model cites `[n]` instead of pasting URLs) |
| `chat_service.py` — source list building (step 11) | `app/chat/citations.py` | done: cited-only sources, renumbered by first appearance so `[k]` ↔ `sources[k-1]` |
| `concept_interpreter.py` (73) | `app/integrations/azure_openai.py` (`interpret_concept`) + prompt text in `app/chat/prompts.py` | builds its own client today; use the shared factory |
| `kb_search.py` (223) | `app/integrations/azure_search.py` | `query_kb` done (semantic query, selected fields); recency/news/event filters, scoring profiles and date sort not ported; file logging dropped |
| `intent_detector.py` (422) | `app/chat/intent.py` | pure |
| `entity_extractor.py` (301) | `app/chat/entities.py` | spaCy model load once at startup (lifespan), not at import |
| `online_retrieval.py` (724) | `app/integrations/online/retrieval.py` | orchestrator; semantic validation uses the OpenAI client — pass it in |
| `source_fetchers.py` (695) | `app/integrations/online/fetchers.py` | requests-based; add timeouts if missing |
| `trusted_sources.py` (385) | `app/integrations/online/trusted_sources.py` | pure data + scoring |
| `azure_foundry_retrieval.py` (200) | `app/integrations/online/foundry.py` | |
| `debug_logger.py` (79) | removed → `logging.getLogger(__name__).debug` | `CHAT_DEBUG` maps to `LOG_LEVEL=DEBUG` |
| `chat_cli.py` (1146) | `tools/chat_cli.py` (later) | mostly duplicates of `chat_service`; keep only the REPL loop over the pipeline |
| `dashboard.py` (350, Streamlit) | `tools/dashboard.py` (later) | host-side tool; not part of the image contract (streamlit/pandas/plotly stay in requirements only because of the inherited baseline) |
| `web/templates/*` (legacy UI) | not migrated | replaced by `frontend/` once V1/V2 is chosen |
| `web/drupal/drr_chat` | not migrated | Drupal proxy module lives with Drupal, not here |

Migration order that keeps every step testable: `schemas` → `db` (+ tests against a throwaway Postgres) → `services.sessions` + routes `auth`/`sessions` (contract tests with a real DB, no Azure) → `integrations` (each behind a small function; tests mock the SDK) → `chat` pipeline → route `chat` → `admin` → tools.

## 4 Frontend location decision

- **Production frontend lives in `frontend/`** at the repo root and is copied into the image (`COPY frontend/ /data/frontend/`). FastAPI mounts it at `/` (`html=True`) when `frontend/index.html` exists — the same single-container model the legacy project used, so the Drupal proxy and deployment assumptions do not change.
- The mount is plain `StaticFiles`; moving the frontend behind a reverse proxy or CDN later requires no backend change.
- **Selected and wired (2026-09-20): V1 ChatGPT-style.** `templates/v1-sidebar-sources/` was copied into `frontend/` and connected to the API (`frontend/README.md` lists the files and calls); the template keeps its own mock-data copy, and `templates/v2-claude-web/` stays as design history. Identity is a **development mechanism** (`frontend/identity.js`: email/name → `POST /api/users/identify`, `localStorage`), isolated so Entra ID / local accounts can replace that one module.
- The application service is named **`chatbot-app`** in both compose files (renamed from `poc-undrr-chatbot`; container names unchanged): browser → `chatbot-app` → `/` frontend + `/api/*` FastAPI → Postgres / Azure AI Search / Azure OpenAI.
- The API base URL for the frontend will be same-origin by default (`/api/...`), with the Drupal-proxied prefix (`/drr-chat/api/...`) as a build-time/config option — see the assessment §6.5.

## 5 Docker alignment

`Dockerfile` copies `app/` and `frontend/`; both compose files run `uvicorn app.main:app`; dev bind-mounts `./app` and `./frontend`. Tests, `pyproject.toml`, `requirements-dev.txt`, `Makefile`, `docs/` and `templates/` are excluded from the build context. Nothing else in the infrastructure changed (see `docs/infrastructure-baseline.md` §6).


## 6 Authentication (2026-09-20)

**Mechanism.** Server-side browser sessions: `POST` sign-in endpoints set an
`HttpOnly; SameSite=Lax; Secure` cookie (`ai4drr_session`) holding a random
256-bit token; only its SHA-256 is stored in `auth_sessions` with the user id,
method (`entra` | `local`) and expiry. Every user-scoped route depends on
`api.deps.get_current_user` (401 without a live session) and checks ownership
with `require_owned_session` (another user's session answers 404 — its
existence is not revealed). `GET /api/sessions` lists the signed-in user's
sessions; a `user_id` query that is not the caller's is 403. Logout deletes
the row, so a replayed cookie is dead immediately. No JWTs, no signing keys.

**CSRF.** SameSite=Lax keeps the cookie off cross-site POSTs; in addition
`app.main` refuses any unsafe `/api/*` request whose `Origin` header is neither
the app's own origin (`APP_BASE_URL` / the request host) nor a configured CORS
origin. CORS middleware is added only when `CORS_ALLOW_ORIGINS` is set.

**Microsoft sign-in (primary): Azure AD B2C / Entra External ID, or a plain
Entra ID tenant.** `app/integrations/entra.py` wraps MSAL's confidential-client
authorization-code flow with PKCE (`initiate_auth_code_flow` → `state`,
`nonce`, verifier; `acquire_token_by_auth_code_flow` → validated
`id_token_claims`). Configuration (`app/config.py`): `AZURE_B2C_TENANT_NAME` +
`AZURE_B2C_POLICY` (the sign-up/sign-in user flow) + `AZURE_B2C_CLIENT_ID/SECRET`
select B2C — authority `https://<tenant>.b2clogin.com/<tenant>.onmicrosoft.com/<policy>`,
every endpoint taken from that policy's OpenID metadata; `ENTRA_TENANT_ID` +
`ENTRA_CLIENT_ID/SECRET` select a plain tenant. Both at once, credentials
without a tenant, a tenant without credentials, B2C without a policy, or a
non-https redirect (localhost excepted) refuse to start with a clear
`ValueError`. `AZURE_B2C_REDIRECT_URI` is the exact redirect URI sent to
Microsoft (default `APP_BASE_URL/api/auth/entra/callback`); it must be
registered on the app registration, and when its path differs from the
canonical `/api/auth/entra/callback` the same handler is served there too.
Startup logs mode, policy and redirect URI (never the secret) and warns when
the redirect URI is not on `APP_BASE_URL`. MSAL applications are created
lazily per policy, so a metadata outage cannot stop the app.

`GET /api/auth/entra/login[?next=/path&policy=signin|reset]` stores the flow
(state, verifier, nonce, policy, `next`) in a 10-minute HttpOnly cookie scoped
to the callback path and redirects to Microsoft. `GET /api/auth/entra/callback`
maps the claims (`oid`; `email`, else B2C's `emails[0]`, else
`preferred_username`; `given_name`/`family_name`, name assembled when the flow
issues none) with `services.auth.identity_from_claims`, links or creates the
`users` row (`entra_oid` first, then e-mail — `sign_in_entra`), refuses a
disabled account (403 → no session) and opens the normal session cookie. Every
non-success returns the browser to the sign-in page with
`?auth_error=cancelled|failed|disabled` (B2C `AADB2C90091` = cancelled; a
missing flow cookie, provider error, state mismatch, unusable identity =
failed); the front ends show a plain sentence and clean the URL. B2C's
"Forgot your password?" answer (`AADB2C90118`) starts the
`AZURE_B2C_PASSWORD_RESET_POLICY` user flow through the same login route and
callback; that flow only resets Microsoft identities — local accounts use the
app's own reset e-mail (below). `AZURE_B2C_EDIT_PROFILE_POLICY` is read but
not exposed (the product edits no profile fields). `is_admin` never comes from
the identity. Unconfigured → `entra_enabled=false` and 501 on the routes.

**Logout.** `POST /api/auth/logout` deletes the session row and clears the
cookie for everyone and answers `{signed_out, redirect}`: for a session opened
with Microsoft on B2C, `redirect` is the sign-in policy's end-session endpoint
with `post_logout_redirect_uri=AZURE_B2C_LOGOUT_REDIRECT_URI` (default
`APP_BASE_URL/`, must be registered too) and the front end navigates there;
local sessions get `null` and stay in the app. Coming back lands on the
sign-in screen without a cookie — no loop.

**Local email/password (secondary, `LOCAL_AUTH_ENABLED`).** argon2id via
`argon2-cffi`. With the flag off (Microsoft-only mode) the UI hides the local
form, the Forgot-password link and the reset view (`body[data-local-auth]`
from `GET /api/auth/config`), and the API refuses with 403 — `require_local_auth`
on `POST /api/auth/local/{login,forgot}` and `/api/auth/reset{,/check}`;
`require_local_accounts` on `/api/auth/setup{,/check}`, `POST
/api/admin/invitations` and `…/resend` (listing and revoking stay; the
management page greys *Invite* and hides *Resend*); `make admin` promotes
existing accounts only. Nothing is deleted or migrated: local rows, password
hashes and open invitations are kept and usable again once the flag is on. **There is no
public registration** (the former `/api/auth/local/register` route was
removed on 2026-09-20): local accounts come only from the invitation flow (§8).

**Forgot / reset password (`app/services/password_reset.py`,
`postgres_init/006_password_resets.sql`).** `POST /api/auth/local/forgot
{email}` always answers 202 with the same sentence ("If an account with that
email can reset its password, we've sent instructions."): an unknown address,
a Microsoft-only account (no `password_hash`) and even a failed e-mail send
are indistinguishable to the caller (the failure is logged with the user id
and reason only). For a local account with a password, every open request of
that user is revoked and a new row `password_resets(user_id, token_hash,
expires_at, consumed_at, revoked_at)` is written — token `secrets.token_urlsafe(32)`,
SHA-256 at rest, `PASSWORD_RESET_TTL_MINUTES` (60) — and the message goes
through the same `Mailer` as invitations, with one "Reset my password" CTA
to `APP_BASE_URL/reset-password#token=…` (fragment, never in access logs)
and the expiry stated. The page (`app/pages/reset.html`, `frontend/reset.js`)
validates the token with `POST /api/auth/reset/check` (200 with the account
e-mail, or 410 with the reason: expired / replaced by a newer request / used)
and submits `POST /api/auth/reset {token, password, confirm_password}` (the
normal password policy; 204). Completion locks the row, replaces the hash,
consumes the token, revokes any other open request and **deletes every
`auth_sessions` row of the user** — one transaction — and opens no session:
the person signs in with the new password. `is_admin` and `entra_oid` are
untouched; administrators use the same mechanism. A token is usable exactly
once; expired, replaced, used or unknown tokens never change a password.

**Schema.** `postgres_init/002_auth.sql` (idempotent; also applied at API
start by `app.db.migrate` under an advisory lock so existing databases get
it): `users` + `first_name`, `last_name`, `password_hash`, `entra_oid`
(unique partial index), unique `lower(email)`; new table `auth_sessions`.
`name` is still written (`first last`) so every existing consumer works.

**Legacy endpoints.** `POST /api/users/identify` returns the signed-in user
and `POST /api/login` opens a session for the signed-in user; both ignore any
body. Client-supplied identity is no longer trusted anywhere.


## 7 Administrators / Chatbot Management (2026-09-20)

An administrator is an ordinary user with `users.is_admin = TRUE`
(`postgres_init/003_admin.sql`, idempotent, applied at API start like 002).
Same sign-in methods, same session cookie, same `users` and `auth_sessions`
— no admin table, credential or cookie. `api.deps.get_current_admin` is the
guard: 401 without a session, 403 for a signed-in non-admin.

- `GET /api/admin/me`, `GET /api/admin/users` (the management page's Active
  list; real user records, no password hashes) — both behind the guard.
- `GET /admin/login` — the doorway (public HTML; `app/pages/admin/login.html`,
  assets under `frontend/admin/`). Microsoft (with `?next=/admin/login` so
  the Entra callback returns there) or local sign-in exactly as on the chatbot;
  afterwards the page asks `GET /api/admin/me` and only a 200 leads on. A
  non-admin sees an explicit "Access denied" state (their normal session stays
  valid for the chatbot). The local section follows `LOCAL_AUTH_ENABLED`.
- `GET /admin/users` — served only to administrators: anonymous → 302 to
  `/admin/login`, signed-in non-admin → 302 to `/admin/login?denied=1`. The
  HTML lives in `app/pages/admin/` (outside the static mount), so the URL alone
  yields nothing; the data comes from the guarded API anyway.
- **Promotion is an operational step**, never an API:
  `docker compose exec chatbot-app python -m app.db.admin_ops grant <email>`
  (`revoke`, `list`), or `UPDATE users SET is_admin = TRUE WHERE lower(email)
  = lower('<email>')`. The user must already exist (sign in once first). The
  flag takes effect on the next request — no re-login.
- **First administrator — `make admin EMAIL=<address> [ENV=dev|prod]`**
  (`python -m app.db.admin_ops bootstrap <email> [--send]` inside the app
  container). An existing user is promoted in place; an existing
  administrator is reported and nothing changes. An unknown address gets an
  *administrator bootstrap invitation* (`invitations.grants_admin = TRUE`,
  `postgres_init/005_admin_bootstrap.sql`, `invited_by` NULL): the one-time
  setup link is printed to the operator's terminal — never stored raw, never
  logged — or, with `SEND=1` / `--send`, e-mailed through `EMAIL_TRANSPORT`
  (a failed send deletes the row again, like the API). The person completes
  the normal `/account-setup` page and the account is created with
  `is_admin = TRUE`; from then on it is an ordinary row in `users` /
  `auth_sessions`. Running the command again replaces an unused link (the
  old token is revoked). Only the CLI can issue such invitations; the
  management API always issues `grants_admin = FALSE`, and *Resend* keeps the
  flag of the invitation it replaces. No password is ever invented or
  printed; public registration stays closed.
- **View / Disable / Enable (`app/services/admin_users.py`,
  `postgres_init/007_user_status.sql`).** `users.disabled_at` (NULL = enabled;
  every existing row stays enabled). `GET /api/admin/users/{id}` is the View
  panel: profile, status, administrator flag, which sign-in methods exist
  (password set / Microsoft linked — booleans only), created/updated,
  chat count and last chat activity, live sign-in count and last seen. Never a
  password hash, Entra object id, session or token hash. `POST
  /api/admin/users/{id}/disable` locks the row, sets `disabled_at` and deletes
  every `auth_sessions` row of the user in one transaction — a suspension, not
  a deletion: chats, password, Microsoft linkage and `is_admin` stay. From then
  on the session lookup refuses the account even if a session row survived,
  local sign-in answers 403 "This account has been disabled…" only after the
  password verified (a wrong password still gets the generic 401), a
  successful Microsoft sign-in is refused at the callback (403, no session),
  "forgot password" stays silent (no mail; same generic answer), reset links
  issued earlier answer 410, and invitations/setup cannot re-create the
  account (409, as for any existing e-mail). `POST …/enable` clears the flag
  and nothing else; old sessions are not restored. Both are idempotent.
  Guards: 409 for the caller's own account (checked before any database work)
  and 409 "Cannot disable the last enabled administrator" (the enabled
  administrator rows are locked `FOR UPDATE` first, so two concurrent disables
  cannot both pass); unknown or malformed ids → 404. `make admin` still
  promotes a disabled user but says so. No delete, no e-mail on status changes.


## 8 Invitations and account setup (2026-09-20)

**Lifecycle.** Administrator → *Invite User* (e-mail only) → the invitee
receives one message with a setup link → within 24 h they complete first
name, last name, optional department and a password → the account is created
and Active, the invitation consumed, a normal session opened. No public
registration, no approval queue.

**Storage** (`postgres_init/004_invitations.sql` + `005_admin_bootstrap.sql`,
idempotent, applied at API start): `invitations(id, email, token_hash,
invited_by, created_at, expires_at, consumed_at, revoked_at,
accepted_user_id, grants_admin)` — `grants_admin` is only ever TRUE for the
operator bootstrap (§7). The raw token —
`secrets.token_urlsafe(32)` — exists only in the e-mail; the database keeps
its SHA-256. E-mails are treated case-insensitively.

**Rules** (`app/services/invitations.py`): inviting an address that already
has an account → 409; an open, unexpired invitation → 409 ("resend it
instead"); an expired or revoked one does not block a new invitation.
*Resend* revokes the old invitation, issues a new token and expiry and sends
a fresh message (the old link stops working). *Revoke* closes it. The setup
link is `APP_BASE_URL/account-setup#token=…` — the token rides in the URL
fragment, so it never appears in server access logs; the page sends it in a
JSON body to `POST /api/auth/setup/check` (410 with the reason when unusable:
unknown, expired, revoked, consumed) and `POST /api/auth/setup`, which
creates the user, consumes the invitation (row locked, one transaction) and
sets the session cookie. Completion refuses (409) if an account with that
e-mail appeared in the meantime.

**E-mail** (`app/integrations/mail.py`): a `Mailer` seam with `SmtpMailer`
(`EMAIL_TRANSPORT=smtp`, STARTTLS or implicit TLS, `SMTP_*`), `FileMailer`
(`file`: `.eml` files under `MAIL_FILE_DIR`, for development) and `NoMailer`
(`none`: inviting answers 503). The message identifies AI4DRR Chatbot, has a
single "Set up my account" CTA and states the 24-hour expiry. **Failure
rule:** the invitation row is written first; if sending fails the row is
deleted again in the same request and the administrator gets 502 — a stored
invitation always means a message went out. Logs record recipient domain,
subject and outcome only, never bodies or tokens.

**Endpoints** (admin guard): `GET /api/admin/invitations` (open ones, with
`pending`/`expired` status), `POST /api/admin/invitations` `{email}`,
`POST /api/admin/invitations/{id}/resend`, `POST …/{id}/revoke`. Public:
`GET /account-setup` (page), `POST /api/auth/setup/check`, `POST /api/auth/setup`.

**Not implemented (on purpose):** account disable/enable, profile view, e-mail verification for Microsoft identities (Entra sign-in is
unchanged: a valid Microsoft identity maps to a user row but grants nothing
beyond the normal chatbot, and never `is_admin`).
