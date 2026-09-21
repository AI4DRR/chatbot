# Infrastructure baseline — copied from `datum-drr-chat`

**Date:** 2026-09-19
**Source of truth (read-only):** `/home/administrator/workspace/undrr/datum-drr-chat/` @ `b39b36e`
**Rule applied:** compatibility first — copy the existing runtime as-is; no upgrades, no normalisation. Inconsistencies are listed, not fixed.

> **How to read this document.** It is a dated log, not a description of the
> current tree. §1 and §3 describe the *legacy* project and are still accurate.
> §2, §5 and §6 record the copy and the first cleanup while the placeholder
> was `src/api.py` + `web/`; those paths no longer exist. **§7 is the current
> state** — `app/` + `frontend/`, `uvicorn app.main:app`. Layout and package
> details live in `docs/architecture.md`.

## 1 Exact baseline found in the old project

| Item | Value | Where |
|---|---|---|
| Base image | `python:3.11-slim` | `Dockerfile:2` |
| Workdir | `/data` | `Dockerfile:4` |
| OS packages | `build-essential curl git` | `Dockerfile:10-13` |
| Python deps | `pip install -r requirements.txt` (no lock file, mostly unpinned — see below) | `Dockerfile:16` |
| NLP model | spaCy `en_core_web_md`, downloaded at build time and import-checked | `Dockerfile:19-20` |
| Dirs created in image | `/data/chat_session`, `/data/logs` | `Dockerfile:23` |
| Exposed port | 8000 | `Dockerfile:26` |
| Image `CMD` | `python src/chat_cli.py` (CLI — overridden by both compose files) | `Dockerfile:29` |
| Dev compose | `version: '3.8'`; service `poc-undrr-chatbot`, image `undrr-chatbot:dev`, `build: .`, container `preventionweb-container`; `env_file: .env`; extra env `PYTHONUNBUFFERED=1`, `DATABASE_URL=postgresql://undrr:undrrpassword@postgres:5432/undrr_chat`; bind mounts `./src ./web ./chat_session ./logs`; ports `8084:8000`; `stdin_open`/`tty`; command `uvicorn src.api:app --host 0.0.0.0 --port 8000 --reload`; `depends_on: postgres` (no condition); network `undrr-network` (bridge) | `docker-compose.yml` |
| Dev Postgres | `pgvector/pgvector:pg16`, container `undrr-postgres`, user/pw/db `undrr / undrrpassword / undrr_chat`, bind volume `./postgres_data`, init `./postgres_init` → `/docker-entrypoint-initdb.d`, ports `5432:5432` | `docker-compose.yml:33-47` |
| Prod compose | image `undrr-chatbot:latest`, container `undrr-chatbot-prod`; `env_file: .env` only (DATABASE_URL expected from `.env`); mounts `./chat_session ./logs` only; ports `8000:8000`; `restart: always`; healthcheck `curl -f http://localhost:8000/api/health` 30s/10s/3 retries/start 40s; command `uvicorn src.api:app --host 0.0.0.0 --port 8000 --workers 4`; `depends_on: postgres: condition: service_healthy`; deploy limits cpus 2 / mem 2G, reservations 1 / 1G | `docker-compose.prod.yml` |
| Prod Postgres | `pgvector/pgvector:pg16`, container `undrr-postgres-prod`, creds from `${DB_USER:-undrr}` `${DB_PASSWORD:-undrrpassword}` `${DB_NAME:-undrr_chat}`, named volume `postgres_data_prod` (local driver), same init dir, healthcheck `pg_isready -U ${DB_USER:-undrr}` 10s/5s/5, no host port, limits cpus 1 / 1G, reservations 0.5 / 512M | `docker-compose.prod.yml:51-83` |
| Actual PG data on disk | `postgres_data/PG_VERSION` = **16** (matches the image tag) | `postgres_data/PG_VERSION` |
| DB schema | `CREATE EXTENSION pgcrypto, vector`; tables `users`, `chat_sessions` (+ `memory_summary/facts/turns`), `chat_messages`, `chat_context_chunks`; indexes; `updated_at` trigger | `postgres_init/001_init.sql` |
| Env variables (names) | `DATABASE_URL, DB_USER, DB_PASSWORD, DB_NAME, AZURE_OPENAI_ENDPOINT, AZURE_OPENAI_API_KEY, AZURE_OPENAI_API_VERSION, AZURE_OPENAI_CHAT_DEPLOYMENT, AZURE_SEARCH_ENDPOINT, AZURE_SEARCH_KEY, AZURE_SEARCH_INDEX, KB_TOP_K, CHAT_DEBUG, AZURE_SEARCH_SEMANTIC_CONFIG, CHAT_SESSION_DIR, AI_SEARCH_LOG_PATH, AZURE_COST_INPUT_PER_1K, AZURE_COST_OUTPUT_PER_1K, ADMIN_PASSWORD, API_HOST, API_PORT, API_RELOAD, LOG_LEVEL, AZURE_FOUNDRY_ENDPOINT, AZURE_FOUNDRY_API_KEY, AZURE_FOUNDRY_DEPLOYMENT, AZURE_FOUNDRY_API_VERSION` | `.env.sample` (the real `.env` was not read for values, only key names) |
| `.dockerignore` | none | — |
| Compose tooling on this host | Docker Compose v5.5.1 | `docker compose version` |

`requirements.txt` (verbatim): `openai`, `azure-search-documents`, `azure-core`, `numpy`, `requests`, `beautifulsoup4`, `spacy>=3.5.0`, `psycopg[binary]>=3.2.0`, `streamlit>=1.32.0`, `pandas>=2.0.0`, `plotly>=5.20.0`, `python-dotenv>=1.0.0`, `fastapi>=0.109.0`, `uvicorn[standard]>=0.27.0`, `pydantic>=2.0.0`, `pydantic-extra-types>=2.0.0`, `email-validator>=2.0.0`. Only lower bounds; no exact versions are recorded anywhere in the old project, and no previously built image exists on this host to read `pip freeze` from.

## 2 Files created in the new project

> *Superseded by §7:* `src/api.py` and `web/templates/.gitkeep` below were replaced by `app/` and `frontend/`.

| New file | Origin | Verbatim / adapted |
|---|---|---|
| `Dockerfile` | `datum-drr-chat/Dockerfile` | **verbatim** (md5 `7790c8d7…`) |
| `requirements.txt` | `datum-drr-chat/requirements.txt` | **verbatim** |
| `postgres_init/001_init.sql` | `datum-drr-chat/postgres_init/001_init.sql` | **verbatim** |
| `.gitignore` | `datum-drr-chat/.gitignore` | **verbatim** (ignores `.env`, `chat_session/`, `*.log`, `postgres_data/`) |
| `.env.example` | `datum-drr-chat/.env.sample` | **verbatim content**, renamed to `.env.example`; contains only placeholder values (`***`, `[RESOURCE NAME]`) |
| `docker-compose.yml` | `datum-drr-chat/docker-compose.yml` | **adapted: container names only** — `preventionweb-container` → `undrr-chatbot-dev`, `undrr-postgres` → `undrr-chatbot-postgres`, so both projects can exist on one host without a name clash. Everything else identical. |
| `docker-compose.prod.yml` | `datum-drr-chat/docker-compose.prod.yml` | **adapted: one container name** — `undrr-postgres-prod` → `undrr-chatbot-postgres-prod`. Everything else identical (including the `/api/health` healthcheck, see §4). |
| `src/api.py` | new | **placeholder** — minimal FastAPI app exposing only `GET /health` with the old response shape (`status`, `database`, `timestamp`) and the same psycopg `SELECT 1` check. Exists solely so `uvicorn src.api:app` in both compose files has a target. Not the backend. |
| `web/templates/.gitkeep`, `logs/.gitkeep`, `chat_session/.gitkeep` | new | path placeholders for the bind mounts (`./web`, `./logs`, `./chat_session`) |
| `docs/infrastructure-baseline.md` | new | this document |

Untouched: `templates/` (V1/V2 UX work), `docs/standalone-frontend-assessment.md`, and everything under `datum-drr-chat/`.

## 3 Inconsistencies found in the old baseline (reported, not resolved)

1. **Production image has no application code.** `Dockerfile` copies only `requirements.txt`; `src/` and `web/` reach the container solely through the dev compose bind mounts. `docker-compose.prod.yml` mounts only `chat_session` and `logs`, so `uvicorn src.api:app` cannot find `src` in a prod container built from this Dockerfile. Either prod was never run as written, or code was mounted/copied out-of-band.
2. **Prod healthcheck path.** It probes `/api/health`, but the API registers `/health` (`src/api.py:199`). As written the prod container would be reported unhealthy and `depends_on … service_healthy` would never be satisfied for anything depending on it.
3. **Env var names.** `src/api.py:87-89` reads `AZURE_OPENAI_KEY` and `AZURE_API_VERSION`; `.env.sample` and every other module use `AZURE_OPENAI_API_KEY` / `AZURE_OPENAI_API_VERSION`. Likely works only through the openai SDK's own env fallback.
4. **`version: '3.8'`** in both compose files is obsolete for Compose v2+ (warning on every command; ignored).
5. **Image `CMD` vs compose `command`.** The image defaults to the CLI (`chat_cli.py`); the API only runs because compose overrides the command.
6. **Dev DB port 5432 is published to the host.** On this machine port 5432 is already used by another project's container (`dts-db`), so `docker compose up` of the dev stack will fail on port binding until that is stopped or the mapping is changed. Kept as-is per the copy-faithfully rule.
7. **Unpinned dependencies.** With only lower bounds, a build today resolves to whatever is current on PyPI (e.g. `openai`, `fastapi`, `pydantic`, `numpy` may be newer majors than the old project last ran with). Reproducibility of the *old* runtime cannot be guaranteed from these files alone.
8. **Two `DATABASE_URL` conventions.** Dev compose hardcodes the in-network URL (`@postgres:5432`); `.env.sample` documents a `localhost` URL for the host-side Streamlit dashboard; prod expects `.env` to carry the in-network one.
9. **No `.dockerignore`** — a build sends the whole project directory (including `postgres_data/` in the old project) as context.

## 4 Deliberately deferred

- Fixing any item in §3 (they are the "modernise later" phase).
- Pinning dependency versions / adding a lock file.
- Adding a `.dockerignore`, copying `src/` and `web/` into the image, `COPY` of the frontend templates.
- Migrating the backend (`chat_service`, `kb_search`, `session_manager`, … ) — `src/api.py` is a placeholder only.
- Serving `templates/` from the container; wiring the new frontend to the API.
- Any change to Python / Postgres / pgvector versions.

## 5 Validation performed (no credentials needed)

- `docker compose -f docker-compose.yml config` and `-f docker-compose.prod.yml config` (with a temporary `.env` copied from `.env.example`, removed afterwards): both render; only the expected obsolete-`version` warning. Rendered dev service shows image `undrr-chatbot:dev`, port `8084→8000`, the uvicorn `--reload` command, DB `pgvector/pgvector:pg16` on `5432`; prod shows `undrr-chatbot:latest`, port `8000`, 4 workers, healthchecks and resource limits as copied.
- `docker build -t undrr-chatbot:dev .` from the copied Dockerfile: **succeeds** (image 2.04 GB). Versions that resolved on 2026-09-19 with the unpinned requirements: Python 3.11.16, openai 3.16.2, fastapi 0.141.1, uvicorn 0.53.0, pydantic 2.13.5, psycopg 3.3.6, spacy 3.8.16 + en-core-web-md 3.8.0, numpy 2.4.6, azure-search-documents 12.0.0, streamlit 1.64.0, pandas 3.0.6. These are current releases, not necessarily what the old project last ran with (see §3.7) — in particular `openai` 3.x and `pandas` 3.x are major versions newer than the code was likely written against.
- Smoke run of the built image with the placeholder mounted (`docker run … -v ./src:/data/src -p 8099:8000 undrr-chatbot:dev uvicorn src.api:app`): `GET /health` → `{"status":"ok","database":"error",…}` (no DB attached), `GET /api/health` → 404 — which demonstrates inconsistency §3.2 concretely. Container stopped afterwards.
- Checksums: `Dockerfile`, `requirements.txt`, `postgres_init/001_init.sql`, `.gitignore`, `.env.example` are byte-identical to their sources (`cmp`).
- Not run: `docker compose up` (would bind host port 5432, already in use; and requires Azure credentials for anything beyond `/health`).


---

## 6 Conservative cleanup (2026-09-19, same day) — status of §3 items

> *Partly superseded by §7:* the decisions here still stand, but every `src/`/`web/` path in this section now reads `app/`/`frontend/` (Dockerfile `COPY`, compose bind mounts, `.dockerignore`, image `CMD`).

Applied in the new project only; `datum-drr-chat/` unchanged. Each change is listed with why it is behaviour-preserving.

| § | Item | Change | Why safe |
|---|---|---|---|
| 3.1 | Prod image had no app code | `Dockerfile`: `COPY src/ /data/src/` and `COPY web/ /data/web/` after the pip layer (cache-friendly). | Same base, same deps, same paths the compose command already expects. Dev compose still bind-mounts `./src`/`./web` over these paths, so dev behaviour is unchanged; prod now works without mounts. |
| 3.5 | Image `CMD` pointed at the CLI (`src/chat_cli.py`, which does not exist here) | `CMD ["uvicorn","src.api:app","--host","0.0.0.0","--port","8000"]` | Both compose files override `command`, so nothing they run changes; a bare `docker run` now starts the API instead of failing. |
| 3.2 | Prod healthcheck `/api/health` vs real `/health` | Healthcheck → `http://localhost:8000/health`. The endpoint stays `/health` (what the old API and the placeholder serve). | One coherent path; validated in-container (see below). |
| 3.4 | `version: '3.8'` | Removed from both compose files. | Compose v2+ ignores it with a warning; the rendered config is identical. |
| 3.9 | No `.dockerignore` | Added: excludes `.git`, `.env*` (except `.env.example`), `postgres_data/`, `chat_session/`, `logs/`, caches, editor files, `docs/`, `templates/`, compose files, the resolved-freeze. Keeps `requirements.txt`, `src/`, `web/`. | Nothing the Dockerfile `COPY`s is excluded; verified by listing `/data` in the built image. |
| 3.6 | Dev Postgres published on host `5432` (collides with `dts-db`) | Host port publication removed from the dev compose. Added the same `pg_isready` healthcheck the prod file has, and `depends_on: postgres: condition: service_healthy`. A comment shows how to re-publish on `5433` via a local `docker-compose.override.yml` for host-side tools. | Containers talk over the Compose network (`postgres:5432`), unchanged. The healthcheck only delays API start until the DB accepts connections (prod already did this). `dts-db` and other containers untouched. |
| 3.8 | Two `DATABASE_URL` conventions | `.env.example` now carries the canonical container-side URL (`@postgres:5432`), with the host-side URL as a comment. Dev compose keeps its explicit `DATABASE_URL`; prod reads it from `.env`. | Same value the containers always used; only the example default changed. |
| 3.3 | Azure OpenAI env-name mismatch | **Not changed.** Canonical names for the backend migration: `AZURE_OPENAI_API_KEY`, `AZURE_OPENAI_API_VERSION`, `AZURE_OPENAI_ENDPOINT`, `AZURE_OPENAI_CHAT_DEPLOYMENT` (what `.env.sample`, `chat_cli.py`, `concept_interpreter.py`, `online_retrieval.py` use). The outlier is `src/api.py:87-89` in the old project (`AZURE_OPENAI_KEY`, `AZURE_API_VERSION`) — to be aligned when that file is ported, with a decision then on whether to accept the old names as fallbacks. | — |
| 3.7 | Unpinned dependencies | **Not pinned.** `requirements.txt` is still the verbatim copy. The versions that resolved on 2026-09-19 are captured for reference in `requirements.resolved-2026-09-19.txt` (from `pip freeze` in the built image). **Migration gate:** before the backend is ported, decide whether to (a) pin to versions the old code is known to work with, or (b) test the old code against today's resolution (notably `openai` 3.x, `pandas` 3.x, `numpy` 2.x). Do not make the freeze canonical without those tests. | — |
| — | Placeholder `src/api.py` | Unchanged; still `/health` only, clearly marked temporary. | — |

### Validation after cleanup
- `docker compose config` (dev and prod, temporary `.env` from `.env.example`, removed afterwards): both render with **no warnings**; dev shows the healthcheck + `service_healthy` dependency and no published DB port; prod shows the `/health` healthcheck.
- Clean build `docker build --no-cache -t undrr-chatbot:latest .`: succeeds. Image contents without any mount: `/data/{requirements.txt,src,web,chat_session,logs}`; `python -c "import src.api"` inside the plain image → OK.
- Isolated smoke stack from the **prod** compose file (project `chatbot-smoke`, override: API on host `8098`, distinct container names, named volume): Postgres healthy; API container reached `healthy` via its own healthcheck (in-container `curl -f http://localhost:8000/health` → 200); `GET http://localhost:8098/health` → `{"status":"ok","database":"ok"}`; extensions `vector 0.8.6`, `pgcrypto 1.3`; tables `users, chat_sessions, chat_messages, chat_context_chunks`; server `PostgreSQL 16.15`; API container mounts only `/data/chat_session` and `/data/logs` (no `src`/`web` bind) — i.e. the prod-style image starts `src.api:app` on its own.
- Teardown: `compose -p chatbot-smoke down -v` (containers, network, volume removed); temporary `.env` deleted; a root-owned `src/__pycache__` left by the earlier bind-mounted smoke run was removed via a container; `.dockerignore` patterns made recursive (`**/__pycache__/`) after the first build showed a `.pyc` in the image; rebuilt and re-listed clean. No other project's containers were touched (`dts-db`, `undrr-d-dts-webapp`, `z3r0101-*` still running as before).

### Remaining deferred risks before backend migration
1. Dependency compatibility (§3.7 gate) — the single biggest risk; today's resolution is several majors ahead of what the old code was likely written against.
2. Env-name alignment for `api.py` (§3.3).
3. `chat_cli.py` / Streamlit dashboard / online-retrieval fetchers are not part of the image contract yet; when ported, confirm they need nothing beyond `src/` and `web/`.
4. Prod still runs as root in the container and with `--workers 4` on a 2-CPU limit — unchanged from the old baseline, to be revisited in the modernisation phase.
5. Postgres credentials are compose defaults (`undrr/undrrpassword`); prod must set `DB_*` in `.env`.


## 7 Package structure alignment (2026-09-19) — current state

The placeholder `src/api.py` and `web/` were replaced by the `app/` package and `frontend/` directory described in `docs/architecture.md`. Dockerfile now copies `app/` and `frontend/`; both compose files run `uvicorn app.main:app`; dev bind-mounts `./app` and `./frontend`; `.dockerignore` additionally excludes `tests/`, `pyproject.toml`, `requirements-dev.txt`, `Makefile` and tool caches. Re-validated: compose config (dev/prod, no warnings), `--no-cache` build, `import app.main` in the plain image, and the same isolated prod-style smoke stack (`/health` → `database: ok`, container healthy, `/api/health` → `{"detail":"Not Found","status_code":404}`, no `app`/`frontend` bind mounts). `requirements.txt` unchanged.


## 6 Frontend integration (2026-09-20)

The FastAPI service in both compose files was renamed `poc-undrr-chatbot` → `chatbot-app` (the approved application-service name); image names, container names (`undrr-chatbot-dev` / `undrr-chatbot-prod`), ports, mounts, healthchecks and commands are unchanged, and `docker compose config` renders both files. `frontend/` now holds the wired V1 frontend served at `/` by the same service (see `docs/architecture.md` §4).
