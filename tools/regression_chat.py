"""Chatbot flow regression harness (docs/chatbot-flow-improvement-plan.md, Task 5).

Host-side operator tool, not part of the image or the test suite. It drives a
running API through the public contract only, one fresh session per case,
and records what the plan's before/after comparison needs: status, latency,
tokens, cost, sources, citation numbers, the pipeline counters stored in the
assistant message metadata, and — for follow-up turns with a ``reference``
question — whether the answer shares a source with that question asked on
its own.

Run::

    python tools/regression_chat.py --api http://localhost:8084 \
        --database-url postgresql://undrr:undrrpassword@localhost:5434/undrr_chat \
        --label phase2c-baseline

    python tools/regression_chat.py --compare logs/regression/A.json logs/regression/B.json

The API requires a signed-in user and accounts are invitation-only, so the
harness needs ``--database-url``: it inserts one disposable local account per
case (argon2id hash, like the invitation flow), signs in with it
(``LOCAL_AUTH_ENABLED=true`` on the API under test), reads the metadata
counters and deletes the users afterwards. Nothing here prints secrets; the URL is used, never echoed.
"""

from __future__ import annotations

import argparse
import json
import re
import secrets
import sys
import time
import uuid
from datetime import UTC, datetime
from http.cookiejar import CookieJar
from pathlib import Path
from typing import Any
from urllib import error, request

CITATION = re.compile(r"\[(\d+)\]")
UNSOURCED_NOTICE = "_Not sourced from the UNDRR knowledge base._"  # app.chat.prompts.UNSOURCED_NOTICE
DEFAULT_CASES = Path(__file__).resolve().parent.parent / "tests" / "regression" / "cases.json"
DEFAULT_OUT_DIR = Path(__file__).resolve().parent.parent / "logs" / "regression"


# --- API client ------------------------------------------------------------------


# One cookie jar per case: the API authenticates with an HttpOnly session cookie (LOCAL_AUTH_ENABLED
# must be on for the harness, which registers a disposable local account per case).
_opener = request.build_opener(request.HTTPCookieProcessor(CookieJar()))


def _new_jar() -> None:
    global _opener  # module-level client state for an operator script
    _opener = request.build_opener(request.HTTPCookieProcessor(CookieJar()))


def _post(api: str, path: str, body: dict[str, Any], timeout: float) -> tuple[int, dict[str, Any], float]:
    data = json.dumps(body).encode()
    req = request.Request(api + path, data=data, headers={"Content-Type": "application/json"}, method="POST")
    started = time.perf_counter()
    try:
        with _opener.open(req, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode()) if resp.status != 204 else {}
            return resp.status, payload, time.perf_counter() - started
    except error.HTTPError as exc:
        try:
            payload = json.loads(exc.read().decode())
        except Exception:  # any body shape
            payload = {"detail": str(exc)}
        return exc.code, payload, time.perf_counter() - started


def _register(api: str, email: str, timeout: float, db: Db | None) -> None:
    """Create a disposable local account directly in the database (no public registration), sign in."""
    if db is None:
        raise SystemExit(
            "--database-url is required: accounts are invitation-only, the harness creates them in the DB"
        )
    _new_jar()
    password = secrets.token_urlsafe(18)
    db.create_local_account(email, password)
    status, body, _ = _post(api, "/api/auth/local/login", {"email": email, "password": password}, timeout)
    if status != 200:
        raise SystemExit(f"login failed: HTTP {status} {body} (is LOCAL_AUTH_ENABLED=true on the API?)")


def _new_session(api: str, email: str, timeout: float) -> str:
    status, body, _ = _post(api, "/api/login", {}, timeout)
    if status != 200:
        raise SystemExit(f"login failed: HTTP {status} {body}")
    return str(body["session_id"])


def _chat(api: str, session_id: str, message: str, timeout: float) -> dict[str, Any]:
    status, body, seconds = _post(api, "/api/chat", {"message": message, "session_id": session_id}, timeout)
    record: dict[str, Any] = {"message": message, "status": status, "latency_s": round(seconds, 2)}
    if status != 200:
        record["error"] = body.get("detail")
        return record
    text = body["response"]
    record.update(
        {
            "message_id": body["message_id"],
            "token_usage": body["token_usage"],
            "cost_usd": body["cost_usd"],
            "mode": body["mode"],
            "sources": [s["url"] for s in body["sources"]],
            "citations": sorted({int(n) for n in CITATION.findall(text)}),
            "notice": text.startswith(UNSOURCED_NOTICE),
            "response_excerpt": text[:300],
        }
    )
    return record


# --- optional database access ------------------------------------------------------


class Db:
    """Reads assistant metadata and deletes the disposable users; only used when a URL is given."""

    def __init__(self, url: str) -> None:
        """Connect; psycopg is imported here so the tool runs without it when no URL is given."""
        import psycopg

        self._conn = psycopg.connect(url)

    def stats(self, message_id: str) -> dict[str, int]:
        """The ``stats`` counters stored on one assistant message (empty if none)."""
        with self._conn.cursor() as cur:
            cur.execute("SELECT metadata->'stats' FROM chat_messages WHERE id = %s", (message_id,))
            row = cur.fetchone()
        return dict(row[0]) if row and row[0] else {}

    def details(self, message_id: str) -> dict[str, Any]:
        """Routing decision, per-call usage and memory outcome stored on the assistant message (Phase 2)."""
        with self._conn.cursor() as cur:
            cur.execute(
                "SELECT metadata->'routing', metadata->'usage', metadata->'memory' "
                "FROM chat_messages WHERE id = %s",
                (message_id,),
            )
            row = cur.fetchone()
        if not row:
            return {}
        out: dict[str, Any] = {}
        for key, value in zip(("routing", "usage", "memory"), row, strict=True):
            if value:
                out[key] = value
        return out

    def session_state(self, session_id: str) -> dict[str, Any]:
        """Active subject and memory summary/coverage of the session (Phase 2)."""
        with self._conn.cursor() as cur:
            cur.execute(
                "SELECT subject, memory_summary, memory_turns FROM chat_sessions WHERE id = %s", (session_id,)
            )
            row = cur.fetchone()
        if not row:
            return {}
        return {"subject": row[0], "memory_summary": row[1], "memory_turns": row[2]}

    def create_local_account(self, email: str, password: str) -> None:
        """Insert a user with an argon2id hash, exactly as the invitation flow would."""
        from app.services.auth import hash_password

        with self._conn.cursor() as cur:
            cur.execute(
                "INSERT INTO users (email, name, first_name, last_name, department, password_hash) "
                "VALUES (%s, %s, %s, %s, %s, %s)",
                (email, "Regression Harness", "Regression", "Harness", "UNDRR", hash_password(password)),
            )
        self._conn.commit()

    def delete_users(self, emails: list[str]) -> None:
        """Remove the disposable users (sessions and messages cascade)."""
        with self._conn.cursor() as cur:
            cur.execute("DELETE FROM users WHERE email = ANY(%s)", (emails,))
        self._conn.commit()

    def close(self) -> None:
        """Close the connection."""
        self._conn.close()


# --- expectations ------------------------------------------------------------------


def _check(turn_record: dict[str, Any], expect: dict[str, Any], earlier: list[dict[str, Any]]) -> list[str]:
    """Return the names of failed expectations (empty = all passed)."""
    failed: list[str] = []
    if turn_record["status"] != 200:
        return ["http_200"]
    sources = set(turn_record["sources"])
    # Grounding invariant (Phase 3): the notice is present exactly when nothing is cited.
    if turn_record.get("notice") is not (len(sources) == 0):
        failed.append("notice_iff_unsourced")
    if "sources_min" in expect and len(sources) < expect["sources_min"]:
        failed.append(f"sources_min={expect['sources_min']}")
    if "sources_max" in expect and len(sources) > expect["sources_max"]:
        failed.append(f"sources_max={expect['sources_max']}")
    if expect.get("shares_source_with_reference"):
        ref = set(turn_record.get("reference_sources") or [])
        if not (sources & ref):
            failed.append("shares_source_with_reference")
    if "no_shared_source_with_turn" in expect:
        other = set(earlier[expect["no_shared_source_with_turn"] - 1]["sources"])
        if sources & other:
            failed.append("no_shared_source_with_turn")
    routing = turn_record.get("routing") or {}
    if "topic_changed" in expect and routing and routing.get("topic_changed") is not expect["topic_changed"]:
        failed.append(f"topic_changed={str(expect['topic_changed']).lower()}")
    if expect.get("memory_summary_present") and not (turn_record.get("session") or {}).get("memory_summary"):
        failed.append("memory_summary_present")
    return failed


# --- run -------------------------------------------------------------------------------


def run(args: argparse.Namespace) -> Path:
    """Run every case, print one line per turn, save and return the artifact path."""
    cases = json.loads(Path(args.cases).read_text())["cases"]
    if args.only:
        cases = [c for c in cases if c["id"] in set(args.only)]
    db = Db(args.database_url) if args.database_url else None
    emails: list[str] = []
    results: list[dict[str, Any]] = []
    started = datetime.now(UTC)

    for case in cases:
        email = f"regression-{uuid.uuid4().hex[:12]}@example.org"
        emails.append(email)
        _register(args.api, email, args.timeout, db)
        session_id = _new_session(args.api, email, args.timeout)
        turns: list[dict[str, Any]] = []
        for turn in case["turns"]:
            record = _chat(args.api, session_id, turn["message"], args.timeout)
            if db and record.get("message_id"):
                record["stats"] = db.stats(record["message_id"])
                record.update(db.details(record["message_id"]))
                record["session"] = db.session_state(session_id)
            if turn.get("reference"):
                ref_session = _new_session(args.api, email, args.timeout)
                ref = _chat(args.api, ref_session, turn["reference"], args.timeout)
                record["reference"] = turn["reference"]
                record["reference_sources"] = ref.get("sources", [])
                record["reference_status"] = ref["status"]
            record["failed"] = _check(record, turn.get("expect", {}), turns)
            turns.append(record)
            print(_line(case["id"], len(turns), record), flush=True)
        results.append({"id": case["id"], "kind": case["kind"], "turns": turns})

    if db:
        db.delete_users(emails)
        db.close()

    artifact = {
        "label": args.label,
        "api": args.api,
        "started_at": started.isoformat(),
        "finished_at": datetime.now(UTC).isoformat(),
        "cases_file": str(args.cases),
        "summary": _summary(results),
        "cases": results,
    }
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = started.strftime("%Y%m%dT%H%M%SZ")
    path = out_dir / f"{stamp}-{args.label}.json"
    path.write_text(json.dumps(artifact, indent=2, ensure_ascii=False))
    print()
    print(_summary_text(artifact["summary"]))
    print(f"saved {path}")
    return path


def _line(case_id: str, n: int, r: dict[str, Any]) -> str:
    if r["status"] != 200:
        return f"{case_id:22} t{n}  HTTP {r['status']}  {r.get('error')}"
    st = r.get("stats") or {}
    tu = r["token_usage"]
    rt = r.get("routing") or {}
    flags = " FAIL:" + ",".join(r["failed"]) if r["failed"] else " ok"
    route = ""
    if rt:
        route = f" topic={'CHG' if rt.get('topic_changed') else 'same'}"
        if rt.get("fallback"):
            route += " FALLBACK"
        if rt.get("query") != r["message"]:
            route += f' q="{str(rt.get("query"))[:60]}"'
    mem = r.get("memory") or {}
    if mem:
        route += f" mem={'compacted' if mem.get('compacted') else 'skipped'}({mem.get('turns')})"
    return (
        f"{case_id:22} t{n}  {r['latency_s']:5.1f}s  tok={tu['total_tokens']:5d}  "
        f"cost={r['cost_usd'] if r['cost_usd'] is not None else '-':<9}  "
        f"retrieved={st.get('retrieved', '-'):>2} selected={st.get('selected', '-'):>2} "
        f"cited={len(r['sources']):>2}{flags}{route}"
    )


def _summary(results: list[dict[str, Any]]) -> dict[str, Any]:
    turns = [t for c in results for t in c["turns"] if t["status"] == 200]
    n = len(turns)
    if not n:
        return {"turns": 0}
    stats_turns = [t for t in turns if t.get("stats")]
    routed = [t for t in turns if t.get("routing")]
    return {
        "turns": n,
        "routing_fallbacks": sum(1 for t in routed if t["routing"].get("fallback")),
        "routing_rewrites": sum(1 for t in routed if t["routing"].get("query") != t["message"]),
        "routing_tokens": sum(
            ((t.get("usage") or {}).get("routing") or {}).get("total_tokens", 0) for t in turns
        ),
        "routing_latency_s_mean": round(
            sum(((t.get("usage") or {}).get("routing") or {}).get("latency_s", 0) for t in turns) / n, 2
        ),
        "routing_cost_usd": round(
            sum(((t.get("usage") or {}).get("routing") or {}).get("cost_usd", 0) for t in turns), 4
        ),
        "answer_latency_s_mean": round(
            sum(((t.get("usage") or {}).get("answer") or {}).get("latency_s", 0) for t in turns) / n, 2
        ),
        "memory_tokens": sum(
            ((t.get("usage") or {}).get("memory") or {}).get("total_tokens", 0) for t in turns
        ),
        "compactions": sum(1 for t in turns if (t.get("memory") or {}).get("compacted")),
        "failed_expectations": sum(1 for t in turns if t["failed"]),
        "errors": sum(1 for c in results for t in c["turns"] if t["status"] != 200),
        "latency_s_mean": round(sum(t["latency_s"] for t in turns) / n, 2),
        "latency_s_max": max(t["latency_s"] for t in turns),
        "total_tokens": sum(t["token_usage"]["total_tokens"] for t in turns),
        "prompt_tokens": sum(t["token_usage"]["prompt_tokens"] for t in turns),
        "cost_usd": round(sum(t["cost_usd"] or 0 for t in turns), 4),
        "turns_with_sources": sum(1 for t in turns if t["sources"]),
        "turns_with_notice": sum(1 for t in turns if t.get("notice")),
        "sources_mean": round(sum(len(t["sources"]) for t in turns) / n, 2),
        "selected_mean": (
            round(sum(t["stats"]["selected"] for t in stats_turns) / len(stats_turns), 2)
            if stats_turns
            else None
        ),
        "retrieved_mean": (
            round(sum(t["stats"]["retrieved"] for t in stats_turns) / len(stats_turns), 2)
            if stats_turns
            else None
        ),
    }


def _summary_text(s: dict[str, Any]) -> str:
    return (
        f"turns={s.get('turns')} errors={s.get('errors')} failed_expectations={s.get('failed_expectations')} "
        f"latency mean/max={s.get('latency_s_mean')}/{s.get('latency_s_max')}s "
        f"tokens={s.get('total_tokens')} (prompt {s.get('prompt_tokens')}) cost=${s.get('cost_usd')} "
        f"turns_with_sources={s.get('turns_with_sources')} sources_mean={s.get('sources_mean')} "
        f"selected_mean={s.get('selected_mean')} retrieved_mean={s.get('retrieved_mean')} "
        f"routing: rewrites={s.get('routing_rewrites')} fallbacks={s.get('routing_fallbacks')} "
        f"tokens={s.get('routing_tokens')} latency_mean={s.get('routing_latency_s_mean')}s "
        f"cost=${s.get('routing_cost_usd')} answer_latency_mean={s.get('answer_latency_s_mean')}s "
        f"memory: compactions={s.get('compactions')} tokens={s.get('memory_tokens')}"
    )


# --- compare -------------------------------------------------------------------------


def compare(a_path: str, b_path: str) -> None:
    """Print per-turn deltas between two saved artifacts, then both summaries."""
    a = json.loads(Path(a_path).read_text())
    b = json.loads(Path(b_path).read_text())
    print(f"A: {a['label']}  ({a['started_at']})")
    print(f"B: {b['label']}  ({b['started_at']})")
    print()
    header = f"{'case':22} {'t':>2}  {'lat A→B':>13}  {'tok A→B':>13}  {'sel A→B':>9}  {'cited A→B':>11}"
    print(header + "  expectations A→B")
    b_cases = {c["id"]: c for c in b["cases"]}
    for ca in a["cases"]:
        cb = b_cases.get(ca["id"])
        if not cb:
            continue
        for i, (ta, tb) in enumerate(zip(ca["turns"], cb["turns"], strict=False), start=1):
            if ta["status"] != 200 or tb["status"] != 200:
                print(f"{ca['id']:22} {i:>2}  HTTP {ta['status']} → {tb['status']}")
                continue
            sa, sb = ta.get("stats") or {}, tb.get("stats") or {}
            rb = tb.get("routing") or {}
            extra = ""
            if rb:
                extra = f"  B:topic={'CHG' if rb.get('topic_changed') else 'same'}"
                if rb.get("query") != tb["message"]:
                    extra += f' q="{str(rb.get("query"))[:50]}"'
            print(
                f"{ca['id']:22} {i:>2}  {ta['latency_s']:5.1f}→{tb['latency_s']:5.1f}s  "
                f"{ta['token_usage']['total_tokens']:5d}→{tb['token_usage']['total_tokens']:5d}  "
                f"{sa.get('selected', '-'):>3}→{sb.get('selected', '-'):<3}  "
                f"{len(ta['sources']):>4}→{len(tb['sources']):<4}  "
                f"{'ok' if not ta['failed'] else 'FAIL'}→{'ok' if not tb['failed'] else 'FAIL'}{extra}"
            )
    print()
    print("A:", _summary_text(a["summary"]))
    print("B:", _summary_text(b["summary"]))


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--api", default="http://localhost:8084")
    parser.add_argument("--cases", default=str(DEFAULT_CASES))
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR))
    parser.add_argument("--label", default="run")
    parser.add_argument(
        "--database-url", default=None, help="optional; enables metadata counters and cleanup"
    )
    parser.add_argument("--timeout", type=float, default=180.0)
    parser.add_argument("--only", nargs="*", help="case ids to run")
    parser.add_argument("--compare", nargs=2, metavar=("A", "B"), help="compare two saved artifacts and exit")
    args = parser.parse_args(argv)
    if args.compare:
        compare(*args.compare)
        return 0
    run(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
