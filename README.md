# AI4DRR Chatbot

## Introduction

The AI4DRR Chatbot is the standalone successor of the UNDRR disaster-risk-reduction
knowledge assistant: one FastAPI application that serves the browser frontend at `/`
and the JSON API under `/api/*`, backed by PostgreSQL. It answers questions from the
UNDRR knowledge base (Azure AI Search index) with Azure OpenAI, keeps conversations
per user, and is closed to the public: people sign in with Microsoft or with an
invitation-only local account, and administrators manage users from a small
Chatbot Management page. The legacy proof-of-concept in `../datum-drr-chat` is a
read-only reference and is not used at runtime.

```
  Browser
     |
     v
  AI4DRR Chatbot  ──  one container: FastAPI (/api/*) + static frontend (/)
     |
     +-- PostgreSQL          users, sessions, conversations, invitations   (required)
     +-- Azure AI Search     UNDRR knowledge base, semantic retrieval      (required for answers)
     +-- Azure OpenAI        answer + internal routing/memory model         (required for answers)
     +-- Microsoft Entra ID  "Sign in with Microsoft"                       (optional, ENTRA_*)
     +-- SMTP                invitation e-mails                             (optional, EMAIL_TRANSPORT)
```

Main capabilities today:

- **Grounded answers (RAG)** — the question is routed and rewritten by an internal
  model call, one semantic query goes to Azure AI Search, up to eight documents are
  offered to Azure OpenAI, and the answer carries numbered `[n]` citations with a
  sources list. When nothing usable was retrieved the answer says so explicitly.
- **Conversations** — sessions per user with titles, resume, history and a rolling
  memory summary for long conversations. Users only ever see their own sessions.
- **Authentication** — Microsoft Entra ID (OpenID Connect, when configured) and local
  e-mail/password accounts (`LOCAL_AUTH_ENABLED`) with e-mail password reset, both on
  the same server-side session cookie. There is no public self-registration.
- **Invitation-only local accounts** — an administrator invites an e-mail address;
  the person completes their account from a 24-hour one-time link.
- **Chatbot Management** — `/admin`: users list with View / Disable / Enable, invite /
  resend / revoke, restricted to users with the administrator flag; the first
  administrator is created with `make admin`.

Internals (package layout, boundaries, auth and invitation design) live in
`docs/architecture.md`; conventions in `docs/coding-standards.md`; the Docker/Postgres
baseline in `docs/infrastructure-baseline.md`; the UX templates in `templates/README.md`.

## Prerequisites

- **Docker** with the Compose plugin (`docker compose`, v2) and **GNU Make**. Nothing
  else is installed on the host: Python 3.11, dependencies and the spaCy model live in
  the image.
- **PostgreSQL** is provided by Compose (pgvector image); no external database is needed.
- **Azure** — to get real answers: an Azure AI Search index of the UNDRR knowledge base
  (`AZURE_SEARCH_ENDPOINT`, `AZURE_SEARCH_KEY`, `AZURE_SEARCH_INDEX`) and an Azure OpenAI
  chat deployment (`AZURE_OPENAI_ENDPOINT`, `AZURE_OPENAI_API_KEY`,
  `AZURE_OPENAI_CHAT_DEPLOYMENT`), with `CHAT_PIPELINE=rag`. Without them the app runs,
  sign-in and management work, and `POST /api/chat` answers 503 (`CHAT_PIPELINE=stub`
  echoes the question for UI work).
- **Microsoft sign-in (Azure AD B2C / Entra External ID)** — only if the Microsoft
  button should work: a B2C tenant with a sign-up/sign-in user flow and an app
  registration (web platform, client secret) whose redirect URIs include the
  callback **and** the logout return URL. Dev: `http://localhost:8084/api/auth/entra/callback`
  and `http://localhost:8084/`; production: `https://<host>/api/auth/entra/callback`
  and `https://<host>/`. Variables: `AZURE_B2C_TENANT_NAME`, `AZURE_B2C_POLICY`,
  `AZURE_B2C_CLIENT_ID`, `AZURE_B2C_CLIENT_SECRET`, `AZURE_B2C_REDIRECT_URI`,
  `AZURE_B2C_LOGOUT_REDIRECT_URI`, optional `AZURE_B2C_PASSWORD_RESET_POLICY`. A plain
  Entra ID tenant works with `ENTRA_TENANT_ID` / `ENTRA_CLIENT_ID` / `ENTRA_CLIENT_SECRET`
  instead. Leave everything empty and the button is shown disabled; a half-set
  configuration refuses to start with the reason.
- **SMTP** — only for sending invitation and password-reset e-mails: `EMAIL_TRANSPORT=smtp` with
  `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASS`, `SMTP_SECURE` and `EMAIL_FROM`.
  In development `EMAIL_TRANSPORT=file` writes each message as an `.eml` file under
  `logs/mail` instead. The default `none` refuses to send (inviting answers 503).
- A `.env` file at the project root (next section). It holds secrets — never commit
  it or paste its values anywhere.

## Setup

1. **Configure** — copy the template and fill it in:

   ```
   cp .env.example .env
   ```

   Every variable is documented in `.env.example`. The ones that matter first:
   `DATABASE_URL` (keep the Compose value with host `postgres`), the `AZURE_*` values
   and `CHAT_PIPELINE=rag`, `APP_BASE_URL` (the URL users open — it is also what the
   invitation links and the Entra redirect are built from), `LOCAL_AUTH_ENABLED`
   (`true` allows local email/password accounts; `false` = Microsoft-only, see below),
   `SESSION_COOKIE_SECURE=false` for plain-http development only, and the `EMAIL_*` /
   `SMTP_*` block for invitation and reset e-mails.

   **Microsoft-only deployment**: `LOCAL_AUTH_ENABLED=false` together with the
   `AZURE_B2C_*` block. The local form and "Forgot password?" disappear from both
   sign-in screens and the API refuses local login, forgot/reset, invitation setup and
   inviting (403) — people sign in with Microsoft and appear under Users; existing
   local accounts and their passwords are kept and work again when the flag is set
   back to `true`. Administrators are still whoever has `is_admin` on their local row.

2. **Start development**:

   ```
   make up          # builds the image and starts app + Postgres in the background
   make ps          # both containers up, postgres "healthy"
   ```

3. **Open** http://localhost:8084 (development publishes port 8084 → 8000 in the
   container; production publishes 8000). `curl localhost:8084/health` answers
   `{"status":"ok","database":"ok",…}` when the database is reachable.

4. **Bootstrap the first administrator**:

   ```
   make admin EMAIL=user@example.org
   ```

   If no account exists for that address, the command prints a one-time
   administrator setup link (valid `INVITATION_TTL_HOURS`, 24 h by default). Open it,
   fill in first name, last name and a password: the account is created as an
   administrator and signed in. If the account already exists it is promoted; if it
   is already an administrator nothing changes. Public registration does not exist
   — this command and administrators' invitations are the only ways in. Add `SEND=1`
   to e-mail the link instead of printing it (requires a working `EMAIL_TRANSPORT`).
   With `LOCAL_AUTH_ENABLED=false` no link is issued: sign in with Microsoft once, then
   run the command to be promoted.

5. **Common commands** (`make` or `make help` prints them all):

   | Command | Does |
   |---|---|
   | `make up` | `docker compose up --build -d` for the selected environment |
   | `make down` | stop and remove the containers (data volumes are kept) |
   | `make ps` | container status |
   | `make logs` | follow the application log |
   | `make ssh` | shell inside the running app container |
   | `make admin EMAIL=…` | promote / bootstrap an administrator |
   | `make docker-check` | ruff, mypy and the test suite inside the project image |

   `ENV=dev` is the default for every target.

6. **Production** — always explicit, never the default:

   ```
   make up ENV=prod
   make ps ENV=prod
   make admin ENV=prod EMAIL=user@example.org
   ```

   `ENV=prod` uses `docker-compose.prod.yml` (image code, no bind mounts, four
   workers, restart policy, health check, named data volume, port 8000) under its own
   Compose project `chatbot-prod`, so it can never act on the development containers.
   Any other `ENV` value is refused. `make admin ENV=prod` shows the target
   environment and asks you to type `prod` before it runs (`YES=1` skips the prompt
   for scripted use). Set `APP_BASE_URL` to the public https URL and keep
   `SESSION_COOKIE_SECURE=true` in production.

## Lifecycle

**Startup.** `make up` builds the image and starts Postgres first; the app container
waits for its health check, then the API applies the idempotent schema migrations in
`postgres_init/` (auth, administrators, invitations, admin bootstrap) under a lock,
logs its auth/mail configuration and serves the frontend at `/` and the API at
`/api/*`. `GET /health` reports the database state.

**Authentication.** `GET /api/auth/config` tells the frontend which methods exist.
Microsoft sign-in (`/api/auth/entra/login` → the B2C user flow → the registered
callback) maps the Microsoft identity to a user row (matched by object id, then
e-mail); a cancelled or failed Microsoft flow returns to the sign-in screen with a
message. Local sign-in (`/api/auth/local/login`) checks the argon2 password hash. Both open the same HttpOnly session cookie, stored hashed in
`auth_sessions`; `POST /api/auth/logout` invalidates it. "Forgot password?" e-mails a
one-time link to `/reset-password`; setting a new password there signs the account
out everywhere. Public self-registration is closed. Administrators are ordinary users with `users.is_admin = TRUE` — same sign-in,
same cookie, plus access to `/admin/*` and `/api/admin/*`.

**First administrator.** `make admin EMAIL=…` → one-time setup link → `/account-setup`
→ account created with the administrator flag. Run it again to replace an unused link
(the old one stops working) or to promote an existing user.

**Disabling a user.** Administrator → *Disable* on `/admin/users` (confirmation) →
the account is suspended: signed out everywhere at once, local and Microsoft
sign-in refused, reset links useless; chats and profile are kept. *Enable* lifts
it and the person signs in again (old sessions stay dead). You cannot disable
your own account or the last enabled administrator.

**Inviting a user.** Administrator opens `/admin/users` → *Invite User* (e-mail only)
→ an invitation is stored (token hashed) and the e-mail is sent through
`EMAIL_TRANSPORT` → the person opens the 24-hour link → `/account-setup` (fixed
e-mail, name, password) → account Active, signed in → from then on normal local
login. *Resend* issues a fresh link and revokes the old one; *Revoke* closes it. If
the e-mail cannot be sent the invitation is discarded and the administrator sees the
error — a listed invitation always means a message went out.

**A chat turn.** Signed-in user → `POST /api/chat` with the session → the internal
model decides whether the topic changed and rewrites the question into a search
query → Azure AI Search returns candidate chunks, grouped into documents → Azure
OpenAI answers from those excerpts with `[n]` citations (or states that nothing was
found) → the turn, citations and token usage are stored with the session; long
sessions are periodically folded into a memory summary.

**Shutdown.** `make down` (or `make down ENV=prod`). Development data stays in
`./postgres_data`, production data in the named volume.

## Troubleshooting

- **Containers not running** — `make ps`; start with `make up`. If the app container
  restarts in a loop, `make logs` shows why (usually `.env`).
- **http://localhost:8084 not reachable** — `make ps` must show `undrr-chatbot-dev`
  with `0.0.0.0:8084->8000`; another process on 8084, or `ENV=prod` (port 8000), are
  the usual causes. `curl localhost:8084/health` distinguishes "app down" from
  "database down" (`"database":"error"`).
- **No account / cannot get into `/admin`** — `make admin EMAIL=…`. There is no
  registration form; a user who signed in with Microsoft before being promoted is
  simply promoted by the same command.
- **"Access denied" on `/admin/login`** — the account exists but is not an
  administrator: `make admin EMAIL=…` (or inside the container
  `python -m app.db.admin_ops list|grant|revoke <email>`). The flag takes effect on
  the next request; no re-login needed.
- **"This account has been disabled"** — an administrator suspended it; another
  administrator enables it again from *View* or the row on `/admin/users`. Cannot
  disable someone? You cannot disable yourself or the last enabled administrator.
- **Invite button greyed / inviting, setup or local login answer 403 "disabled"** —
  `LOCAL_AUTH_ENABLED=false` (Microsoft-only mode). That is intended; set it to `true`
  and `make up` if local accounts should be allowed.
- **Inviting fails with 503** — `EMAIL_TRANSPORT` is `none`; set `smtp` (or `file` in
  development) and restart. **502** — the SMTP server refused: check `SMTP_HOST`,
  `SMTP_PORT`, `SMTP_SECURE` (`false` = STARTTLS on 587, `true` = implicit TLS on 465),
  `SMTP_USER`/`SMTP_PASS` and `EMAIL_FROM`; the log shows recipient domain and
  outcome only. The link in the e-mail is built from `APP_BASE_URL` — if it points at
  `localhost` the recipient cannot open it.
- **Invitation link expired, lost or "no longer valid"** — administrators use
  *Resend* on `/admin/users` (a new 24-hour link; the old one is revoked). For the
  first administrator, run `make admin EMAIL=…` again.
- **Forgotten password / reset link not received** — "Forgot password?" on the
  sign-in screen sends a one-time link (valid `PASSWORD_RESET_TTL_MINUTES`, 60 by
  default) only to local accounts that have a password; the on-screen answer is the
  same whether or not the address exists. No e-mail means the address is unknown,
  the account signs in with Microsoft only, or sending failed — the same
  `EMAIL_TRANSPORT` / `SMTP_*` / `APP_BASE_URL` checks as for invitations apply
  and `make logs` shows "not sent". Request again to get a fresh link (the previous
  one stops working); after a reset every other session of that account is signed out.
- **Microsoft button disabled ("not configured")** — set the `AZURE_B2C_*` block (or
  the `ENTRA_*` trio) and restart (`make up` recreates the container so it reads the
  new `.env`). The app refuses to start on a half-set block and says which variable.
- **Microsoft sign-in lands on another site or shows "AADB2C90006 … redirect URI …
  not registered"** — `AZURE_B2C_REDIRECT_URI` must be exactly one of the app
  registration's redirect URIs *and* be on this app: for dev
  `http://localhost:8084/api/auth/entra/callback`. `make logs` prints the redirect
  URI in use at startup and warns when it is not on `APP_BASE_URL`. "Policy not
  found" / metadata errors → check `AZURE_B2C_TENANT_NAME` and the exact user-flow
  name in `AZURE_B2C_POLICY`. "Sign-in could not be completed" after returning →
  usually the client secret (expired/wrong) — see `make logs`.
- **Sign-out returns to the wrong page** — `AZURE_B2C_LOGOUT_REDIRECT_URI` (default
  `APP_BASE_URL/`) must also be registered as a redirect URI.
- **Which password reset?** "Forgot password?" on the sign-in screen resets *local*
  accounts by e-mail; the "Forgot your password?" link on Microsoft's page runs the
  B2C reset user flow for Microsoft identities. They never touch each other.
- **Chat answers 503** — `CHAT_PIPELINE` is `unconfigured`; set `rag` with the
  `AZURE_SEARCH_*` / `AZURE_OPENAI_*` values (502 means Azure refused the request —
  check the endpoint, key, index and deployment names in the log).
- **Database / migration problems** — `make logs` shows `migration applied: …` lines
  at startup; an error there means Postgres was unreachable or a schema file is
  missing from the image. `make ssh` then `python -m app.db.admin_ops list` is a
  quick connectivity probe (it fails with the database error if the URL is wrong),
  and `docker compose exec postgres psql -U undrr -d undrr_chat -c '\dt'` lists the
  tables. `DATABASE_URL` must use host `postgres` (the Compose service), never
  `localhost`.
- **Diagnostics** — `make ps`, `make logs`, `make ssh`, `curl localhost:8084/health`,
  `make docker-check` (lint, types, tests).

Known limits: there is no account deletion (disable is a suspension that keeps
everything); e-mail delivery through a live SMTP provider has not been verified from
this repository; the Microsoft (B2C) round trip has been verified up to Microsoft's
sign-in page — completing it needs a registered localhost redirect URI (see above).

## Develop

```
make docker-check               # ruff format --check, ruff check, mypy, pytest — inside the 3.11 image
# or, with a local Python 3.11: make install && make check
```

See `docs/coding-standards.md` for the toolchain and conventions.
