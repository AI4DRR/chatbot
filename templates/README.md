# UX templates

Static, browser-previewable UX/UI concepts for the standalone UNDRR Chatbot
frontend. Mock data only — no API, Drupal or auth integration, no build step,
no dependencies. Both concepts share the same mocked DRR conversation, history
and five sources so they can be compared side by side.

**Status:** comparison candidates only — no selection has been made, and
nothing here is served or shipped. The product name also differs between the
two (v1 says *AI4DRR Chatbot*, v2 says *UNDRR Chatbot*); neither wording is a
decision. When a template is chosen it is copied into `frontend/` and adapted
to the API contract (`docs/architecture.md` §4); this directory stays as design
history.

| Folder | Concept | Preview |
|---|---|---|
| `v1-sidebar-sources/` | **ChatGPT-style** — product identity `AI4DRR Chatbot` (text wordmark, no avatar mark); persistent left history sidebar, wide central thread, bottom composer, M365 Copilot-inspired Sources panel docked on the right; flat source list with KB/Web filter. | `templates/v1-sidebar-sources/index.html` |
| `v2-claude-web/` | **Calm workspace (Claude Web-inspired)** — warm-neutral canvas, serif greeting, icon rail with a hover/pin Recents flyout, document-like answers without bubbles, floating rounded composer with mock attach/scope controls, citation quick-inspect **popover** (bottom sheet on mobile) plus a Sources panel **grouped per answer**. | `templates/v2-claude-web/index.html` |

## Preview

Open either `index.html` directly — `file://` works, nothing is fetched:

```
xdg-open templates/v1-sidebar-sources/index.html   # Linux (macOS: open …)
xdg-open templates/v2-claude-web/index.html
```

or serve the folder for `http://` URLs:

```
python3 -m http.server 8090 --directory templates
# → http://localhost:8090/v1-sidebar-sources/
# → http://localhost:8090/v2-claude-web/
```

Each page has a **Preview state** control (bottom-right) that switches between
static states: welcome / new chat · active conversation · citation or sources
open · loading/thinking · error presentation. V1 additionally has mock
**login** entry states (login · signing in · reset password): "Sign in with
Microsoft" (organizational SSO, Azure Entra ID target) plus, below an "or"
separator, a local email + password form with a "Forgot password?" link.
**Access is by invitation only — there is no self-registration** (the earlier
"Create account" view was removed on 2026-09-20); accounts are created through
the administrators' invitation flow below. The local form, separator and reset
view exist only while local auth is enabled: `MOCK.config.LOCAL_AUTH_ENABLED`
in `mock-data.js`, with a **Local auth** checkbox in the preview bar to flip it
at runtime. No real authentication is performed, nothing is stored or sent, and
form checks are client-side only. Typing in the composer runs a mock
send → thinking → canned answer.

V1 also carries the **invitation-flow design templates** (mock data; the real
flow is implemented and served from `app/pages/` — these stay as design
references):

- `admin-users.html` (+ `admin.css`, `admin.js`) — **Chatbot Management → Users**:
  Active / Pending tabs with counts, "+ Invite User" (email only; the invitee
  completes their own profile and password from a secure link that expires in
  24 h), Active rows Name · Email · Department · View / Disable, Pending rows
  Email · Invited · Expires · Resend invitation / Revoke. `?tab=pending` opens
  the second tab. No roles or permissions.
- `admin-login.html` — the **Chatbot Management doorway** (design target
  `/admin/login`): AI4DRR Chatbot · Chatbot Management · "Admin sign in" ·
  Sign in with Microsoft · separator · Email address · Password · Forgot
  password? · Sign in · "Only authorized Chatbot administrators can access
  Chatbot Management." · Back to AI4DRR Chatbot. Same methods and, later, the
  same `users` / `auth_sessions` as the normal sign-in — a separate entry page,
  not a separate authentication system. `?local=off` previews the local
  section hidden (what `GET /api/auth/config` will drive). Admin authorisation
  (`users.is_admin`, `/api/admin/*`) is a later backend task; the template
  enforces nothing and simply opens `admin-users.html`. Both admin pages are
  now also **served for real** (`/admin/login`, `/admin/users` — see
  `docs/architecture.md` §7); the served copies live in `app/pages/admin/` +
  `frontend/admin/`, these template files stay as design references.
- `account-setup.html` — the screen an invited user reaches from the link:
  Email (fixed by the invitation), First name*, Last name*, Department
  (optional), Password*, Confirm password*, "Complete account setup", then the
  success state ("Account setup complete · Your AI4DRR Chatbot account is
  ready · Continue to AI4DRR Chatbot"). `?state=success` and `?state=invalid`
  preview the other states.

Lifecycle represented: pending invitation → invited user opens the setup link
→ supplies profile and password → account becomes Active. Invitation e-mail
concept (not sent by anything): "You've been invited to AI4DRR Chatbot" /
"Complete your account setup using the link below." / [Set up my account] /
"This invitation is intended for your email address and will expire in 24 hours."
A valid Microsoft identity does not imply enrolment: the design assumes the
administrators control who has access.

## Files (same structure in both)

- `index.html` — page shell and panels (V1 also: `admin-login.html`, `admin-users.html`, `account-setup.html`, `admin.css`, `admin.js`)
- `styles.css` — design tokens, layout, components, responsive rules
- `app.js` — rendering from mock data, panel toggles, citation interactions, preview states
- `mock-data.js` — mocked conversation, history, sources, loading steps, error

## Shared layout model

The page (body) is the single vertical scroll. Side panels are
`position: fixed` and reserve space via the main column's margins on desktop
(a docked Sources panel reflows the conversation without growing the document
width); below 900 px they become drawers over a scrim. The header is
`position: sticky; top: 0` and the composer `position: sticky; bottom: 0` at
the end of the column, so scrolling to the bottom always reveals the final
answer above it. Only long secondary lists (history, sources) scroll
internally, with quiet scrollbars. Both concepts were checked for zero
horizontal overflow at 1440×900 and 500×860.

## What differs

| | v1 | v2 |
|---|---|---|
| Navigation | Always-visible 280px sidebar, collapses to a 60px rail | 64px icon rail; Recents flyout on hover/click, pin to dock |
| Message style | User bubble right, assistant with avatar | User in a soft card left, assistant as plain document text with serif headings |
| Citation | `[n]` chip → opens panel, highlights card | superscript `n` → popover with snippet + "Open source" / "Show in Sources"; per-answer domain pills |
| Sources panel | Flat list, All / KB / Web filter | Grouped by answer, no filter |
| Composer | Rounded box, send at right | Floating card, mock attach `+` and "Knowledge base + web" scope, round send |
| Welcome | Greeting + 4 prompt cards, composer at bottom | Serif greeting, composer mid-page, prompt starter chips beneath |
