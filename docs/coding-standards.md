# Coding standards (Python 3.11)

Small toolchain, enforced in CI-style by `make check` (or `make docker-check`
when no local 3.11 exists). Configuration lives in `pyproject.toml`.

## Toolchain

| Concern | Tool | Why this one |
|---|---|---|
| Formatting | `ruff format` | one formatter, Black-compatible, no config debates |
| Linting + import order | `ruff check` (rules: pycodestyle, pyflakes, isort, pyupgrade, bugbear, naming, pydocstyle-google, annotations, no-print, logging, simplify, return) | replaces flake8 + isort + pyupgrade + several plugins with one binary |
| Typing | `mypy --strict` on `app/` and `tests/` | the codebase is small enough to start strict; per-module `ignore_missing_imports` for SDKs without stubs |
| Tests | `pytest` + `httpx` (FastAPI `TestClient`) | conventional; no plugins until needed |

Line length 110. Target `py311`; use 3.11 syntax (`X | None`, `match` where it reads better, `datetime.UTC`).

## Conventions

**Naming.** `snake_case` functions/variables/modules, `PascalCase` classes, `UPPER_CASE` module constants. Module names are nouns for what they contain (`sessions.py`, `azure_search.py`), not `utils`/`helpers`. Private helpers start with `_`.

**Imports.** Absolute (`from app.db.connection import connect`), never relative, never the legacy `try: from .x import … except ImportError:` dance. Standard library / third party / first party groups (ruff isort). No wildcard imports. No imports inside functions except to break a genuine cycle or defer an expensive optional dependency (document why).

**Typing.** Every function signature is fully annotated (parameters and return). Prefer precise types over `Any`; `Any` is allowed only where an SDK forces it and is explained in a comment. Use `TypedDict`/`dataclass` for structured dicts crossing a boundary (db → service). No `Optional[...]`; use `X | None`.

**Docstrings and comments.** Google style. Every public module, class and function has a one-line summary; add detail only when behaviour is not obvious from the signature. Comments explain *why* (a constraint, a legacy quirk, a decision), never *what* the next line does. No emoji, no banners, no commented-out code.

**Errors.** Raise `app.errors.AppError` subclasses from services/db/integrations (`NotFoundError`, `ValidationError`, or new ones added there). Routes do not catch exceptions to re-wrap them; `app.main` maps `AppError` and `HTTPException` to `{"detail","status_code"}` and logs unexpected exceptions once. Never `except Exception: pass`; catch the narrowest type you can name. Let SDK errors propagate unless the code can genuinely continue without the result (the chat pipeline may proceed without online sources — say so in the `except`).

**Logging.** `logger = logging.getLogger(__name__)` at module top. `logger.info` for lifecycle events, `logger.debug` for pipeline detail (what `debug_logger` used to print), `logger.warning` for degraded-but-continuing, `logger.error(..., exc_info=exc)` for failures. Lazy `%s` formatting, no f-strings in log calls, no `print`. Never log secrets, full prompts, or user messages at INFO.

**Configuration.** `app/config.py` is the only place that reads `os.environ`, at startup, into an immutable `Settings`. Functions receive the values they need (or the `Settings` object) as parameters. Canonical variable names are those in `.env.example`; new variables are added there first. No `load_dotenv()` in application code — the environment is provided by Compose.

**Database.** psycopg 3, synchronous, `with connect(url) as conn:` per request/unit of work; commit is implicit on success, rollback on exception. Parameterised queries only (`%s` placeholders) — never string-format SQL. Functions in `app/db` take `conn` as the first argument, return dicts/dataclasses, and contain no business rules. Schema changes go in numbered files under `postgres_init/` (a migration tool is a later decision).

**Async vs sync.** Route handlers that call blocking I/O (psycopg, Azure SDKs, `requests`, spaCy) are plain `def` so FastAPI runs them in the threadpool. Use `async def` only for handlers that await async clients end-to-end. Do not call blocking code inside `async def` — this was a defect in the legacy API (every route was `async def` around blocking calls). Threadpool-bound CPU work (spaCy) is acceptable at this scale; revisit if latency demands it.

**Resources.** Long-lived clients (Azure OpenAI, Azure Search, spaCy model) are created once in the lifespan and stored on `app.state`, exposed through dependencies in `app/api/deps.py`. Nothing heavy happens at import time.

**Tests.** `tests/test_<module>.py`, function names describe the behaviour (`test_health_without_database`). Unit tests build the app with `create_app(Settings(...))` and mock at the integration boundary (the function in `app.integrations`/`app.db`, not the SDK internals). No network, no real Azure in tests. Database-backed tests (when `app/db` is ported) run against a throwaway Postgres container and are marked `db`. Keep fixtures in `conftest.py`; no fixture magic beyond that.

**Dependencies.** Runtime deps stay in `requirements.txt` (inherited baseline; changing it is a migration-gate decision, see `docs/infrastructure-baseline.md` §6). Dev tooling in `requirements-dev.txt`. `requirements.resolved-*.txt` is reference only.

**Commits/PRs** (when the repo is initialised): small, one concern each; message says what and why.
