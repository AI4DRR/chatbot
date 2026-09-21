# UNDRR Chatbot — Standalone Frontend Assessment

**Date:** 2026-09-18
**Scope:** assessment / documentation only. No changes made under `datum-drr-chat`.
**Reference repo (read-only):** `/home/administrator/workspace/undrr/datum-drr-chat/` (HEAD `b39b36e`)
**Method:** static reading of source. The backend was **not running** on this host during the assessment (nothing on `:8084`; `:8000` is an unrelated service), so nothing below was verified against a live API. Items marked **[unverified]** are inferred from code or docs and need a live check.

Path shorthand below: `src/…`, `web/…` are relative to the reference repo.

---

## 1 Current Architecture

### 1.1 Components (evidence-based)

| Layer | What it is | Evidence |
|---|---|---|
| Frontend A — "DATUM DRR Chat" standalone page | Static HTML/CSS/JS served **by FastAPI itself** at `/` and `/index.html` | `src/api.py:143-152`, `web/templates/chat.html`, `web/templates/chat.js`, `web/templates/style.css` |
| Frontend B — Drupal embed widget | HTML block + JS + CSS meant to be pasted into a Drupal page; talks to the backend **only via the Drupal proxy** (`/drr-chat/api/*`) | `web/templates/drupal-embed.html` (mock Drupal page, script inlined, served at `/embed` by `src/api.py:167-170`), `web/templates/undrr-chatbot-script.js`, `web/templates/undrr-chatbot-style.css` |
| Frontend C — Admin dashboard | Static page + JS, password login, analytics charts | `web/templates/admin.html`, `admin.js`, `admin-style.css`; `src/api.py:155-194, 520-633` |
| Drupal module `drr_chat` | Reverse proxy from `/drr-chat/*` to FastAPI; injects the Drupal user identity on login/identify | `web/drupal/drr_chat/` (also zipped/tarred copies), `web/drupal/documentation.md` |
| Backend | FastAPI app, run under Uvicorn | `src/api.py`; `docker-compose.yml` → `uvicorn src.api:app --host 0.0.0.0 --port 8000 --reload`, host port **8084**; `docker-compose.prod.yml` → 4 workers, host port **8000** |
| Chat engine | Concept interpretation → KB retrieval → online retrieval → single LLM completion | `src/chat_service.py:64-216` |
| Persistence | PostgreSQL 16 (pgvector image, but vector column unused by the API path) — users, sessions, messages, memory | `postgres_init/001_init.sql`, `src/chat_db.py` |
| Knowledge base | Azure AI Search, semantic query on index `AZURE_SEARCH_INDEX` | `src/kb_search.py:33-38, 142-152` |
| LLM | Azure OpenAI chat completions (deployment `AZURE_OPENAI_CHAT_DEPLOYMENT`), **non-streaming** | `src/chat_service.py:597-600`, `src/api.py:85-102` |
| Online retrieval | Azure AI Foundry (Bing grounding) when configured, else Wikipedia/DuckDuckGo/WorldBank/USGS/NOAA fetchers | `src/online_retrieval.py`, `src/azure_foundry_retrieval.py`, `src/source_fetchers.py`, `.env.sample` |

### 1.2 Practical request flow — one chat turn

**Via Drupal (production intent):**

```
Browser (PreventionWeb page, logged-in Drupal user)
  │  POST https://<drupal-host>/drr-chat/api/chat   {message, session_id}
  ▼
Drupal route drr_chat.chat  (_user_is_logged_in: TRUE)          web/drupal/drr_chat/drr_chat.routing.yml
  │  ProxyController::handle → Guzzle POST {api_base_url}/api/chat, body passed through verbatim
  ▼                                                                 ProxyController.php:85-177
FastAPI POST /api/chat                                              src/api.py:305-397
  ├─ session lookup + last-message `topic` from Postgres            src/api.py:329-345
  ├─ ChatService.handle_chat_turn                                   src/chat_service.py:64-216
  │    ├─ interpret_concept (Azure OpenAI call)                     src/concept_interpreter.py
  │    ├─ query_kb (Azure AI Search, semantic)                      src/kb_search.py:142
  │    ├─ detect_intent + retrieve_online (Foundry or fetchers)     src/chat_service.py:162-171
  │    └─ _chat → Azure OpenAI chat.completions.create (no stream)  src/chat_service.py:453-611
  ├─ save user + assistant messages (metadata: mode, topic, sources) src/api.py:356-370
  └─ ChatResponse JSON
  ▲
Drupal re-wraps the JSON body with the upstream status code
  ▲
Browser renders `response` (marked + DOMPurify) and `sources`
```

**Direct (standalone page A):** identical minus the Drupal hop; the browser calls `{API_BASE}/api/*` directly (`web/templates/chat.js:54-64`).

Default `api_base_url` in the module config is `http://host.docker.internal:8000` (`web/drupal/drr_chat/config/install/drr_chat.settings.yml`) — i.e. Drupal-in-Docker reaching FastAPI on the Docker host. Production value **[unverified]**.

### 1.3 Things the docs claim that the code does not do

- `PROJECT_STATUS.md` says Phase 5 (REST API) / Phase 6 (Web UI) are "pending"; both exist. Treat `PROJECT_STATUS.md` and `README.md` as stale for architecture purposes.
- `docker-compose.prod.yml` health check hits `/api/health` (line 38) but the app only registers `/health` (`src/api.py:199`). **[unverified at runtime]** — as written, the prod health check would 404.
- `src/api.py:87-90` reads `AZURE_OPENAI_KEY` / `AZURE_API_VERSION`; `.env` and every other module use `AZURE_OPENAI_API_KEY` / `AZURE_OPENAI_API_VERSION`. The openai SDK falls back to the `AZURE_OPENAI_API_KEY` env var when `api_key=None`, so this likely works by accident. **[unverified]**

---

## 2 Existing Standalone

There **is** an existing standalone implementation: Frontend A.

| Aspect | Finding | Evidence |
|---|---|---|
| Location | `web/templates/chat.html`, `chat.js` (888 lines), `style.css` | — |
| How served | By the FastAPI process: `/` and `/index.html` return `chat.html`; `/static/*` mounts `web/templates` | `src/api.py:134-152` |
| How it reaches the backend | **Direct browser → FastAPI**, no intermediary. `API_BASE` = same origin, except on `localhost`/`127.0.0.1` where it forces `:8000` | `chat.js:54-64` |
| Auth | None. A self-declared login form (email, name, department, unit — all required client-side) → `POST /api/users/identify`. Whatever the user types is accepted and becomes the analytics identity. | `chat.html:19-38`, `chat.js:174-283` |
| Identity persistence | `localStorage`: `drr_chat_user` (user JSON), `drr_chat_session_id`, `drr_sidebar_collapsed` | `chat.js:17-18, 31-32, 110` |
| Deep link | `?session=<uuid>` opens that session after identify/restore; used by the sidebar "open in new window" link (`/?session=…`) | `chat.js:45, 250-253, 674` |
| Third-party libs | `marked@12`, `dompurify@3` from jsDelivr CDN | `chat.html:131-132` |
| Note | the dev-mode `:8000` hardcode conflicts with `docker-compose.yml` which publishes host port **8084** — on localhost the page served from 8084 would call 8000. **[unverified]** | `chat.js:59`, `docker-compose.yml:23` |

**Reusable behaviour from A** (the same logic also exists in B, near-duplicated): lazy session creation on first send, auto-title after first turn, sidebar session list with 10-item overflow, history replay through the same renderer, sources accordion. See §7.

`PreventionWebStuff/chat` is a saved copy of the live `preventionweb.net/chat` page (title "PreventionWeb chatbot | PreventionWeb"). It contains the widget markup/CSS but **no API call** — the assistant reply is hard-coded "Mock assistant HTML" (lorem-ipsum). It is a design reference, not a working integration.

---

## 3 Drupal Integration

Only what a standalone frontend needs to know.

### 3.1 Embedding
- Frontend B is a self-contained block: `<section id="undrr-chatbot">…` plus welcome screen and sidebar markup (`drupal-embed.html:915-1025`), CSS (`undrr-chatbot-style.css`) and one IIFE script (`undrr-chatbot-script.js`). The script bails out if the expected element IDs are missing (`undrr-chatbot-script.js:9`).
- `drupal-embed.html` is a **mock** of a Drupal page ("Drupal Mock – Chatbot POC") with the script inlined; `/embed` on FastAPI serves it, but the script calls `/drr-chat/api/*` on the same origin, which FastAPI does not serve — so `/embed` only works behind Drupal (or a rewriting proxy). **[unverified]**
- No `drupalSettings`, no `postMessage`, no iframe, no Drupal JS API is used by the widget. Its only dependency on Drupal is the URL prefix and the Drupal session cookie.

### 3.2 Module role — forwarding
- Routes: `/drr-chat/health`, `/drr-chat/api/{users/identify, login, chat, sessions, sessions/{id}, sessions/{id}/resume, sessions/{id}/title}` plus a **catch-all** `/drr-chat/{proxy_path}` for any method (`drr_chat.routing.yml`).
- Every route requires `_user_is_logged_in: TRUE` → anonymous requests get Drupal's normal 403 (**[unverified]** — could be a redirect to login depending on site config).
- Body is forwarded verbatim with `Content-Type: application/json`; query string forwarded; upstream JSON re-emitted with the upstream status code; non-JSON upstream bodies are wrapped as `{"raw": "..."}`; Guzzle timeout **60 s** (`ProxyController.php:143-165`).
- Errors from the proxy itself use `{"error": "..."}` (500 / 403), whereas FastAPI errors use `{"detail": "...", "status_code": N}` — a client behind Drupal must handle **both** shapes (`ProxyController.php:46, 79, 171`; `src/api.py:638-655`).

### 3.3 Auth / secrets / identity
- The **only** authentication in the whole system is Drupal's login gate on the proxy. FastAPI has no auth on any chat/session endpoint (see §6).
- For `login` and `users/identify` the proxy **ignores the client body** and sends the Drupal account's `email`, display name, and `field_department`/`field_unit` (or `department`/`unit`) if those profile fields exist, else `null` (`ProxyController.php:32-41, 43-83`). This is how department/unit reach analytics.
- No secrets reach the browser; `api_base_url` lives in Drupal config.
- `login` responses' `session_id` is also stored in the Drupal PHP session as `drr_chat_session` (`ProxyController.php:67-69`) — nothing reads it back. Unused today.

### 3.4 Session / state
- Drupal keeps no chat state beyond the unused value above. Chat session identity lives in the browser (`localStorage`) and in Postgres.
- The proxy does **not** check that a `session_id` or `user_id` in a request belongs to the logged-in Drupal user; it passes them through (`ProxyController.php:143-156`).

---

## 4 Uvicorn API Contract

Everything below is what the two chat frontends actually call (`chat.js`, `undrr-chatbot-script.js`). Base path `/api` (or `/drr-chat/api` through Drupal). All requests/responses are `application/json`. No auth headers, no cookies used by the frontends, no CSRF token.

### 4.1 Endpoints used by the chat UI

| Method & path | Request | Response (200) | Errors | Used by |
|---|---|---|---|---|
| `POST /api/users/identify` | `UserLogin` `{email (EmailStr, required), name?, department?, unit?}` | `{"user": {id, email, name, department, unit}}` | 400 invalid email, 422 pydantic, 500 | A on login; B on page load (empty body, Drupal fills it) |
| `POST /api/login` | same `UserLogin` | `{"user": {...}, "session_id": "<uuid>"}` — **creates a new session** titled `"New Chat Sessions"` every call | 400, 422, 500 | A & B: lazy session creation on first send (`chat.js:798-846`, `undrr-chatbot-script.js:340-357`) |
| `POST /api/chat` | `ChatRequest` `{message (min 1 char), session_id}` | `ChatResponse` — see 4.2 | 404 session not found, 400 `ValueError`, 422, 500 "Chat processing failed" | A & B |
| `GET /api/sessions?user_id=<uuid>` | query param | `SessionInfo[]` `{id, title, created_at, memory_turns, last_message_at}` ordered by recency | 500 | sidebar list |
| `GET /api/sessions/{id}` | — | `SessionHistory` `{session_id, title, created_at, messages[], memory_summary, memory_facts[]}`; `messages[]` = `MessageRecord` `{id, role: user\|assistant, content, created_at, token_input?, token_output?, cost_estimate?, sources[]}` ordered ASC | 404, 500 | open a past session |
| `PATCH /api/sessions/{id}/title` | `{title}` (trimmed, max 200) | `{success, session_id, title}` | 500 (no 404 — unknown id still returns success **[unverified]**) | auto-title after first turn |
| `POST /api/sessions/{id}/resume` | — | `{session_id, session_info: SessionInfo}` | 404 | **not called by either frontend** (proxied by Drupal anyway) |
| `GET /health` | — | `{status, database, timestamp}` | 500 | not called by frontends |

Models: `src/models.py`. Handlers: `src/api.py:228-517`.

### 4.2 `ChatResponse` shape (`src/models.py:58-67`)

```json
{
  "response": "<markdown text>",
  "session_id": "<uuid>",
  "message_id": "<uuid of the assistant row>",
  "token_usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
  "cost_usd": 0.0123,                // null if pricing env vars are 0/unset
  "mode": "SYNTHESIS" | "LIST" | "REFUSAL",
  "timestamp": "2026-09-18T11:58:40.000000",
  "sources": [{"title": "...", "url": "...", "type": "KB" | "WEB"}]
}
```

- `mode` values actually produced: `SYNTHESIS`, `LIST`, `REFUSAL` (`src/chat_service.py:143, 214`). The docstring's `DIRECT`, `KB_SYNTHESIS`, `ONLINE_SEARCH` are not emitted.
- `sources` = **all retrieved** KB chunks + online results deduped by URL, not only the ones the LLM cited (`src/chat_service.py:184-196`). Citations inside `response` are inline URLs in markdown, produced by prompt instruction (`src/chat_service.py:541`), not a structured field.
- `REFUSAL` turns return zero token usage, `cost_usd: null`, empty `sources`, and a fixed refusal text (`src/chat_service.py:132-143`).
- `timestamp` is naive UTC (`datetime.utcnow()`), no `Z` suffix.

### 4.3 Session / conversation identity
- `session_id` and `user.id` are Postgres UUIDs. The frontend must send `session_id` on every `/api/chat`; the backend derives continuity from the **`topic` in the last message's metadata** only (`src/api.py:339-345`).
- `memory_summary` / `memory_facts` are read on every turn (`src/api.py:337`) but **never written by the API path** — `update_session_memory` has no caller in `api.py`/`chat_service.py` (grep-verified). `memory_turns` therefore stays 0 for API-created sessions, and prior turns are **not** sent to the LLM. Conversation continuity via API is effectively "topic string only". **[behaviour unverified at runtime, but no code path exists]**
- No session-ownership check: any `session_id` is readable/writable by anyone who can reach the API (§6).

### 4.4 Feedback, streaming, cancellation
- **No feedback endpoint** (no thumbs up/down, no rating) anywhere in `src/api.py` or either frontend.
- **No streaming**: one blocking `chat.completions.create` (`src/chat_service.py:597-600`); the HTTP response is a single JSON body. Typical latency = concept LLM call + AI Search + online retrieval (+ optional Foundry call) + answer LLM call; the Drupal proxy caps this at **60 s**.
- No request cancellation / abort semantics.

### 4.5 Error shape
FastAPI: `{"detail": "<msg>", "status_code": N}` for `HTTPException` (`src/api.py:638-645`); 422 validation errors use FastAPI's default `{"detail": [...]}` list shape (**[unverified]** — the custom handler is registered only for `HTTPException`, and `RequestValidationError` is not an `HTTPException` subclass in FastAPI, so the default handler applies). Through Drupal, add `{"error": "..."}` (proxy failures) and `{"raw": "..."}` (non-JSON upstream).

### 4.6 Admin endpoints (for completeness, not part of the chat frontend)
`POST /api/admin/login {password}` → `{"token": "admin-session-authenticated"}`; all `/api/admin/analytics/*` take `?token=` and compare to that **constant string** (`src/api.py:52-60, 175-194`). Out of scope for the new frontend but relevant if the admin page is ever re-hosted.

---

## 5 Current Frontend Behavior

Both chat frontends (A `chat.js`, B `undrr-chatbot-script.js`) implement the same state machine with different DOM/CSS. Where they differ it is noted.

### 5.1 Required by the backend/API (must be preserved by any new frontend)

| Behaviour | Why it is required | Evidence |
|---|---|---|
| Obtain a `user.id` before anything else (`/users/identify`) | `GET /api/sessions` needs `user_id` | `chat.js:212`, `undrr-chatbot-script.js:597-616` |
| Create a session with `POST /api/login` **before** the first `/api/chat` | `/api/chat` 404s without a valid `session_id`; there is no `POST /api/sessions` (the `StartSessionRequest` model exists but no route) | `src/api.py:327-334`, `src/models.py:104-115` |
| Send `{message, session_id}` on every turn | request model | `src/models.py:52-55` |
| Persist `session_id` client-side across reloads | backend has no cookie/session mechanism | `localStorage` keys in both scripts |
| Replay history from `GET /api/sessions/{id}` including `messages[].sources` | only source of history | `chat.js:735-760` |
| Treat `response` as markdown and sanitize | backend returns markdown with inline URLs; both UIs use `marked` + `DOMPurify` | `chat.js:459-478`, `chat.html:131-132` |
| Handle `{"detail"}` errors (and `{"error"}`/`{"raw"}` behind Drupal) | error contract, §4.5 | `chat.js:427-430` |
| Behind Drupal: send `{}` to `/users/identify` and expect the identity to come back | proxy overrides body | `undrr-chatbot-script.js:598-602` |

### 5.2 UI-only behaviour (free to change in a redesign)

- Self-declared login modal with 4 required fields (A only; B has no login UI — identity comes from Drupal, with a "sign in to Drupal then refresh" fallback screen).
- Lazy session creation ("New Chat" clears the thread and sets `pendingNewSession`; the DB row is created on first send) — a UX choice to avoid empty sessions; the API would equally allow eager creation.
- Auto-title = first 7 words of the first user message + "…", then `PATCH …/title`. Pure client heuristic; the backend only stores what it is given. Sidebar shows `"New Chat"` for the server default title `"New Chat Sessions"`.
- Sidebar: newest-first list, 10 visible + "Show N more…", active highlight, collapse state in `localStorage`, auto-collapse under 768 px, "open in new window" (`/?session=` — A only).
- Sources accordion under each assistant bubble: sorted KB-first then A–Z, badges `KB`/`WEB`, count in the button label.
- Assistant markdown links are replaced by a small `↗` icon anchor with the URL as tooltip (A: `chat.js:466-478`).
- Loading placeholder ("⏳ Thinking…" in A; animated dots in B), Enter-to-send / Shift+Enter newline, textarea auto-resize capped at 220 px.
- B only: "engaged" layout switch after first message, sticky composer with scroll-position logic (`undrr-chatbot-script.js:280-335`), welcome screen with suggested prompts.
- Footer "Admin Dashboard" link (A).
- Token/cost fields from `ChatResponse` are received but **not displayed** in either chat UI.

---

## 6 Standalone Constraints (verified from code)

1. **CORS is wide open on FastAPI**: `allow_origins=["*"]`, `allow_credentials=True`, all methods/headers (`src/api.py:126-132`). A standalone frontend on any origin can call the API directly today. (Note: browsers reject `*` + credentials for credentialed requests, but the frontends send none, so plain `fetch` works.) The Drupal module's CORS rationale in `documentation.md` is not what the backend enforces.
2. **The backend has no authentication or authorization** on `/api/users/identify`, `/api/login`, `/api/chat`, `/api/sessions*`. Identity is whatever the caller claims; any `session_id` can be read or appended to; `?user_id=` lists anyone's sessions. Today the only gate is Drupal's `_user_is_logged_in` in front of the proxy. **A standalone frontend that bypasses Drupal removes the only auth in the system.** This is the primary constraint for phase planning.
3. **No secrets are needed in the browser** (all Azure/DB credentials are server-side), so a direct-to-API standalone page has no secret-leak problem — only the auth problem above.
4. **No cookies or server sessions** are used by FastAPI. State = `localStorage` on the client + Postgres on the server. Nothing to share across a Drupal ↔ standalone boundary except the UUIDs.
5. **API base URL is not configurable**; both scripts derive it from `window.location` (same origin, or `:8000` on localhost) with the Drupal prefix hard-coded in B (`undrr-chatbot-script.js:14-19`). Any standalone build needs an explicit config point.
6. **Long, blocking requests**: single JSON response after all LLM/search calls; 60 s cap when proxied through Drupal; no streaming or progress signal. UX must tolerate multi-second waits.
7. **Deployment assumptions**: dev = FastAPI on host `:8084` (`docker-compose.yml`), prod = `:8000` with 4 Uvicorn workers (`docker-compose.prod.yml`); Drupal reaches it via `host.docker.internal:8000` by default. Where the production Drupal, FastAPI and Postgres actually run, and whether the API is reachable from the public internet, is **[unverified]** — nothing in the repo says.
8. **Static assets are served by Uvicorn** via `StaticFiles` at `/static` (`src/api.py:134-138`). A new frontend can either be dropped into `web/templates` (zero backend change) or hosted elsewhere (needs only constraint 5 solved, given constraint 1).
9. **Third-party CDN dependencies** (`marked`, `dompurify`, `plotly` for admin) are loaded from jsDelivr at runtime — relevant if the UN hosting policy restricts external scripts. **[policy unverified]**
10. **Session-ownership and user-supplied identity** are backend gaps, not frontend ones; a new frontend cannot fix them, only avoid making them worse (e.g. by not exposing `?session=` deep links without auth).

---

## 7 Reusable Components / Behavior

Directly reusable (copy or port; no backend dependency beyond §4):

- **API client logic** — the six calls in §4.1 with their request/response handling, including lazy session creation and auto-title. Cleanest reference: `chat.js` `handleLogin`, `createSession`, `handleSendMessage`, `loadSessions`, `openSession`, `updateSessionTitle`.
- **Drupal-identity bootstrap** — `identifyDrupalUser()` / `initAuth()` in `undrr-chatbot-script.js:597-667`: POST `{}` to identify, fall back to a "sign in to Drupal" screen on failure.
- **Message renderer** — markdown → `marked` → `DOMPurify` → link-to-icon post-processing (`chat.js:459-478`) and the sources accordion (`chat.js:487-530`). Both UIs replay history through the same renderer, which is worth keeping.
- **Sidebar model** — list, overflow toggle, active item, collapse persistence (`chat.js:603-688`).
- **CSS** — `web/templates/style.css` (standalone look) and `undrr-chatbot-style.css` (PreventionWeb look, UNDRR blue `#004F91` visible in `PreventionWebStuff/chat` palette). The PreventionWeb page markup in `PreventionWebStuff/chat` is a usable visual reference for the widget's target context.
- **Pydantic models** (`src/models.py`) — can be transcribed 1:1 into TypeScript types for the new client.

Not reusable as-is:
- The two scripts are ~80 % duplicated (A vs B); a new frontend should have one API layer with the Drupal prefix and identity mode as configuration.
- `drupal-embed.html` (mock page) and `PreventionWebStuff/chat` (mock reply) are references, not code to ship.

Verified unknowns are in §8.

---

## 8 Unknowns / Items Requiring Confirmation

| # | Unknown | Why it matters | How to confirm |
|---|---|---|---|
| U1 | Production topology: where FastAPI runs, its public reachability, the real `api_base_url` in Drupal config, TLS termination | decides whether a standalone frontend can talk to the API directly at all, and from which origin | ask ops / inspect the deployed Drupal config and container hosting |
| U2 | Is the current `allow_origins=["*"]` intentional for production? | if it will be tightened, the standalone origin must be allow-listed | product/ops decision |
| U3 | Required auth model for the standalone frontend: Drupal session only, Azure Entra ID (README mentions it as optional), or none for an internal tool | drives Phase 2 in §9 and any backend work (out of scope here) | product decision |
| U4 | Whether the live PreventionWeb `/chat` page already loads `undrr-chatbot-script.js` and the `drr_chat` module is installed | the saved copy is a static mock, so integration status is unknown | check the live site / Drupal admin |
| U5 | Memory/continuity expectation: is the "topic-only continuity, memory never updated via API" behaviour (§4.3) known and accepted? | affects UX copy ("remembers context") and whether the new frontend should send history itself — which the API does not accept | confirm with backend owner |
| U6 | Prod health check path mismatch (`/api/health` vs `/health`) and env-var name mismatch (`AZURE_OPENAI_KEY`) | may mean prod compose has never been run as-is; affects deployment assumptions | run prod compose / check container health |
| U7 | UN hosting policy on CDN scripts (jsDelivr) | may require vendoring `marked`/`dompurify` | policy check |
| U8 | 422 validation error shape through Drupal (list `detail` wrapped by the proxy) | error handling in the client | one live request |
| U9 | Whether `PATCH …/title` on an unknown session really returns 200 and whether `/api/sessions` for an unknown `user_id` returns `[]` | edge-case handling | live requests |
| U10 | Whether department/unit profile fields exist on the Drupal user entity | analytics attribution when identity comes from Drupal | Drupal field config |

---

## 9 Proposed Implementation Phases

High-level only; each phase is derived from the constraints above and keeps the Python/Uvicorn backend unchanged.

**Phase 0 — Confirm unknowns (U1–U5 minimum).** Nothing below is safe to size until topology and auth model are known. Also get one live capture of each §4.1 response to pin the contract (replaces the [unverified] marks).

**Phase 1 — Contract layer.** Build the new frontend's API client against the §4 contract as a single module with two configurable inputs: base URL and identity mode (`direct` = self-declared identify, `drupal` = empty-body identify via `/drr-chat/api`). Transcribe `src/models.py` into types. Include both error shapes. No UI yet; can be validated against the existing backend as-is because CORS is open.

**Phase 2 — Standalone shell with existing UX parity.** Reproduce the §5.1 required behaviours and the §5.2 behaviours worth keeping (sidebar, sources, history replay, lazy session), served either from `web/templates` (zero backend change) or from a separate static host (needs only the base-URL config). Use the same markdown/sanitize pipeline. This is the safe baseline before any UX redesign.

**Phase 3 — Auth decision applied.** Depending on U3: (a) keep Drupal as the gate and ship the standalone as a Drupal-hosted asset using the `/drr-chat/api` prefix, or (b) put the standalone in front of the API directly and accept that the backend currently has no auth — which is only acceptable for a closed network — or (c) request a backend auth mechanism as separate work. This assessment does not include backend changes; the phase exists so the choice is explicit.

**Phase 4 — UX revision.** Only after Phase 2 parity: the redesign proper (welcome/suggested prompts, composer behaviour, sources presentation, engaged layout, mobile). Backend-facing behaviour is frozen by Phase 1, so this phase is UI-only. Any feature needing new API surface (feedback, streaming, "regenerate", per-message actions) is flagged as backend work and excluded.

**Phase 5 — Deployment & cut-over.** Decide hosting per U1, vendor or allow-list CDN scripts per U7, replace `chat.html`/embed script references, keep the old pages available until the new one is verified on the live Drupal page (U4).

Out of scope, as instructed: Azure AI Search replacement/migration, backend or Drupal changes, dependency changes.
