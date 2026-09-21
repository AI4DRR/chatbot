# frontend/

The production frontend: the selected **V1 ChatGPT-style** design
(`templates/v1-sidebar-sources/`, copied here and wired to the API; the
template keeps its own untouched copy with mock data).

Served by the application service (`chatbot-app` in both compose files):
FastAPI mounts this directory at `/` when `frontend/index.html` exists
(`app/main.py`, `FRONTEND_DIR`), so the page and the API (`/api/*`) share one
origin. No build step; plain HTML/CSS/JS.

| File | Role |
|---|---|
| `index.html` | page shell: sign-in screen, history sidebar, chat, sources panel |
| `styles.css` | the V1 design plus a short "production additions" block at the end |
| `config.js` | `API_BASE` (same-origin `/api` by default; `/drr-chat/api` behind Drupal), UI copy |
| `identity.js` | server-side sessions: `GET /api/auth/config` (which methods exist), `GET /api/auth/me`, Entra redirect URL, local login/forgot, `POST /api/auth/logout`. The browser only holds an HttpOnly cookie; nothing about identity lives in `localStorage` |
| `markdown.js` | safe Markdown → HTML for answers; `[n]` → citation buttons; renders the pipeline's "not sourced" notice |
| `app.js` | sessions (list / open / new / rename), chat turns, sources panel, progress and error states |

API calls (JSON, session cookie, `credentials: same-origin`): `POST /login`
(creates a session lazily on the first message of a new chat), `GET /sessions`
(scoped server-side to the signed-in user), `POST /sessions/{id}/resume`, `GET
/sessions/{id}`, `PATCH /sessions/{id}/title` (first 7 words of the first
question, as the legacy frontend did), `POST /chat`. A 401 on any call returns
the page to the sign-in screen. The sign-in screen shows "Sign in with
Microsoft" (disabled until `AZURE_B2C_*` or `ENTRA_*` is configured) and, only when the server
reports `LOCAL_AUTH_ENABLED`, the local form with the forgot-password view
(`body[data-local-auth]`, the template's gating mechanism).

`admin/` holds the assets of the Chatbot Management pages (`admin.css`,
`admin-login.js`, `admin-users.js`); the pages themselves (`/admin/login`,
`/admin/users`) are served by `app/api/routes/admin.py` from `app/pages/admin/`
so that the management page never leaves the server without the admin guard
(`docs/architecture.md` §7).

`setup.js` drives `/account-setup` (served from `app/pages/setup.html`): it
reads the invitation token from the URL fragment and completes the account
through the API (§8 of `docs/architecture.md`). `reset.js` does the same for
`/reset-password` (`app/pages/reset.html`): token from the fragment,
`POST /api/auth/reset/check`, then `POST /api/auth/reset`; no session is
opened, the person signs in with the new password (§6).
