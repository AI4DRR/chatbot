# Chatbot flow improvement plan (assessment — nothing here is implemented)

**Status:** assessment written 2026-09-20 against the Phase 2C backend
(`CHAT_PIPELINE=rag`, see `docs/architecture.md`); revised the same day after
two review rounds (§8), organised into **three development phases** (§6),
and **all three phases implemented and measured on 2026-09-20** (§8 Rounds
4–6). The chatbot flow is ready for frontend integration. The five detailed tasks (§1–§5) keep their technical content and are
mapped under the phases; §6 is the single execution path; §7 lists what
already exists so it is not rebuilt.

| Development phase | Purpose | Tasks | Exit condition |
|---|---|---|---|
| **Phase 1 — Retrieval & Baseline** — **done 2026-09-20** (§6, §8 Round 4) | measure current behaviour, then get more and more diverse KB evidence to the model | Task 5 (harness + baseline artifact), Task 1 (candidate diversity) | better KB evidence reaches GPT-5 without breaking Phase 2C behaviour or the citation contract — **met** |
| **Phase 2 — Intelligent Conversation Flow** — **done 2026-09-20** (§6, §8 Round 5) | one coherent semantic layer: internal LLM, query understanding, active subject, recent context, memory | Task 2 (internal-LLM routing + subject), Task 3 (memory) | natural follow-ups, meaningful topic changes and longer conversations work with no linguistic rules — **met** |
| **Phase 3 — Grounding & Final Tuning** — **done 2026-09-20** (§6, §8 Round 6) | harden answer provenance, then measure and tune the whole flow | Task 4 (unsupported-answer behaviour), Task 5 final run + tuning | stable flow ready for frontend integration — **met** |

## 0 Baseline: what Phase 2C does today

One request to `POST /api/chat` (`app/api/routes/chat.py`) is:

```
read tx    get_session_context(conn, session_id, CHAT_HISTORY_MESSAGES=6)
           → memory_summary / memory_facts (read, never written), topic of the
             newest message (always None from rag), newest 6 raw messages
close
pipeline   RagPipeline.__call__  (app/chat/rag.py)
           1. azure_search.query_kb(question, top=KB_TOP_K=15, semantic)      — current question only
           2. context.select_documents(hits, max_documents=15,
                per-doc CHAT_KB_EXCERPT_CHARS=6000, total CHAT_KB_CONTEXT_CHARS=40000)
                — drop chunks without content/url, group chunks by URL in ranker
                  order, ≤ CHUNKS_PER_DOCUMENT=3 chunks per document, number 1..n
           3. context.select_history(history, 6 messages, 4000 chars each)
           4. prompts.build_messages: SYSTEM_PROMPT · numbered excerpts (or
              NO_EXCERPTS_NOTE) · raw history · question
           5. azure_openai.complete_chat (gpt-5, max_completion_tokens=4000)
           6. citations.resolve_citations: [n] renumbered by first appearance,
              unoffered numbers removed; sources = cited documents only
           7. cost.estimate_cost; mode="SYNTHESIS"; topic=None
write tx   save_turn: touch updated_at, insert user + assistant (metadata {mode, sources})
```

Observed on the real index during the Phase 2C smoke: `KB_TOP_K=15` hits for
"four priorities of the Sendai Framework" were 13 chunks of **one** document
plus one event page; after grouping only 2 documents reached the model, and it
correctly answered from general knowledge with no citation. A second question
(EW4All pillars) gave 7 documents, 5 cited. Latency 13–25 s per turn, almost
all in the two Azure calls.

Log line per turn: `rag turn: retrieved=N selected=N history=N cited=N tokens=N`
(`app/chat/rag.py`) — the regression set in Task 5 can read these directly.

Unused columns that the plan reuses (no schema change anywhere):
`chat_sessions.subject TEXT` (never read or written by either backend),
`chat_sessions.memory_summary / memory_facts / memory_turns` (read, never
written). `chat_sessions.title` is set once by the frontend after the first
turn (`PATCH /api/sessions/{id}/title`, first 7 words of the question) and
otherwise stays as the user left it.

## 1 Better retrieval (candidate set, per-document grouping) — Phase 1

**Current behaviour / code.** `query_kb` asks for `top=KB_TOP_K` (15) hits.
Grouping by document, the per-document chunk cap (3) and the two character
budgets are **already implemented** in `context.select_documents`
(`app/chat/context.py`). What is *not* done: the candidate set is the same
size as the document cap, so when one document contributes 13 of 15 hits the
model sees 2–3 documents even though the index almost certainly holds more
relevant ones just below rank 15.

**Minimal change.** Separate the two numbers that Phase 2C currently ties
together:

- `KB_TOP_K` becomes the *candidate* count passed to Azure (raise the default
  to 30–40; Azure AI Search semantic ranking accepts `top` up to 50).
- a new `CHAT_KB_MAX_DOCUMENTS` (default 8) becomes `max_documents` in
  `select_documents` instead of `kb_top_k` (`build_rag_pipeline`, one line).

Nothing else changes: grouping, the 3-chunk cap and the 6000/40000 character
budgets still bound the *maximum* prompt. **Correction from implementation
(§8 Round 4):** the average prompt does grow, because turns that used to
offer 1–4 documents now offer up to 8 — +36 % prompt tokens and +11 % latency
over the regression set. That is the intended effect (more evidence), not a
leak; the ceiling is unchanged. Optional: also skip chunks whose
`@search.rerankerScore` is below a floor (e.g. 1.0) — `KbDocument` already
carries the score, so it is one condition in `select_documents`; leave it off
by default until Task 5 shows it helps.

**Risks / trade-offs.** A bigger candidate set can pull in weaker documents
that then compete for the fixed budget; the per-document cap (3 chunks) and
ranker order keep the best document first, so the risk is dilution, not loss.
Semantic ranking beyond `top=50` is not available; do not go there.

**Dependencies / order.** Phase 1, step 1.2 — immediately after the
baseline artifact is saved (step 1.1), because everything after it is
measured against retrieval quality (see §5 and §6).

**Non-goals.** No hybrid/vector query, no reranking model, no query
expansion, no recency filters (the legacy `kb_search.py` filters stay
unported), no change to the index.

**Acceptance.** On the Task 5 set: mean distinct documents offered to the
model rises (baseline ≈ 2–7); "Sendai four priorities" offers ≥ 4 distinct
documents and is answered with ≥ 1 citation; p95 search latency increase
< 200 ms; prompt tokens do not increase beyond the existing budget.

**Result (2026-09-20):** implemented — `KB_TOP_K` default 40 (candidates),
new `CHAT_KB_MAX_DOCUMENTS` default 8 (`app/config.py`, `build_rag_pipeline`).
Mean documents offered 5.69 → 7.06; "Sendai four priorities" 1 → 6 documents,
0 → 2 citations; EW4All 3 → 5 citations; turns with sources 11/16 → 14/16.
Search latency itself unchanged (sub-second); end-to-end latency +11 % and
tokens +27 % from the larger prompts (see correction above). Artifacts:
`logs/regression/20260920T005401Z-phase2c-baseline.json` and
`…T010611Z-phase1-retrieval.json`.

## 2 Semantic query understanding (internal LLM before retrieval) — Phase 2

**Requirement (clarified after review).** Follow-up / context understanding
is not to be a hand-written linguistic rule. An **internal LLM** step, with
its **own logical configuration** (initially the same GPT-5 deployment),
produces a retrieval-ready query from the question plus recent context and
tells the pipeline whether the active subject changed. The same internal
configuration later serves memory summarisation (Task 3). Ryan accepts the
extra latency/cost for now; the design must let the internal model be
swapped by configuration alone.

**Current behaviour / code.** Retrieval uses `turn.question` verbatim
(`RagPipeline.__call__`). The model sees the last 6 raw messages, so it can
answer "How about financing?" coherently, but the excerpts were retrieved
for the words "How about financing?" alone. `ChatTurnInput.current_topic` is
read from the last assistant message's `metadata.topic`, which rag never
writes, so it is always `None`. `chat_sessions.subject` exists and is unused.

### 2.1 Architecture: one internal call per turn — assessed against the alternatives

| Option | Semantic? | Extra calls / turn | Verdict |
|---|---|---|---|
| A. Heuristic gate, LLM only when the gate fires | partly | 0–1 | Rejected by the requirement: the heuristic *is* the intelligence layer, and its misses are exactly the turns that needed the model. |
| B. Always concatenate last user message + question, no LLM | no | 0 | Simplest, but misroutes topic switches and "understands" nothing; this is the behaviour the internal step replaces. |
| C. **One internal call per turn, then retrieval, then the answer call** | yes | 1 | **Recommended.** Uniform code path, one structured contract, no linguistic rules. |
| D. As C, but skip the internal call when the session has no history | yes | 0 on turn 1, 1 after | Saves one call on every session's first turn at the price of a special case and no LLM subject at turn 1 (the question text seeds it instead). Reasonable later optimisation; not the first cut. |
| E. As C, plus a speculative search on the raw question run in parallel with the internal call, re-searching only if the routed query differs | yes | 1 (+ a search) | Hides the routing latency on standalone turns. Adds concurrency and a second search path; defer until Task 5 numbers say the latency matters. |
| F. Fold routing into the answer call (ask the answer model to emit the query it wanted) | yes | 0 | Impossible in one pass: retrieval must precede the answer. Two passes of the main model *is* C. |

C is the cleanest design given the requirement. It is not an agent or a
planner: a fixed prompt, a fixed three-field JSON output, no tools, no
loops, no taxonomy. The one structural gate worth keeping is *not*
linguistic: the call always runs, and the code never inspects the question
text itself.

### 2.2 Output contract — three fields, each with a consumer

```json
{"query": "financing of early warning systems under EW4All",
 "topic_changed": false,
 "subject": null}
```

| Field | Type | Who consumes it | Why it exists |
|---|---|---|---|
| `query` | string, non-empty | `azure_search.query_kb` | The retrieval text. Equals (or lightly normalises) the question when it stands alone; is rewritten from context when it does not. |
| `topic_changed` | bool | `save_turn` | `true` → the pipeline's returned `subject` is written to `chat_sessions.subject` in the write transaction. `false` → nothing is written. This is the "do not update the subject every turn" rule, enforced by the contract rather than by a schedule. |
| `subject` | string ≤ 80 chars, or null | `save_turn` (only when `topic_changed`) | The new active subject, produced in the same response so no second call is needed. Must be null when `topic_changed` is false; the pipeline ignores it otherwise. |

Deliberately **not** in the contract:

- `context_used` — proposed in the brief as "whether conversation context
  was required". No code would branch on it; for evaluation it is derivable
  (`query != question`, logged as `rewritten=1`). Dropped.
- `needs_retrieval` / intent labels — always retrieve; the knowledge base is
  the product, and Task 4's notice handles the uncited case.
- `title` — the session title is not the routing step's business (§2.3).
- confidence scores, reasons, entities — no consumer.

Validation: parsed into a pydantic model (`RoutingResult`); unknown fields
ignored, missing/invalid → treated as a routing failure (§2.5). The prompt
requests JSON only; on Azure OpenAI, `response_format={"type":
"json_object"}` is worth one probe in the smoke — it is not in the proven
parameter set, so the parser must not depend on it.

Prompt inputs: the question, the last `CHAT_HISTORY_MESSAGES` raw messages
(the same window the answer call gets — one notion of "recent"), the current
`subject` if any. Instructions: produce a standalone search query for a
disaster-risk-reduction knowledge base; if the question continues the
current subject, resolve references from the conversation; if it starts a
new subject, say so and name it in ≤ 8 words; do not answer the question.

### 2.3 Active subject vs session title

Two things, two columns, two cadences — both already in the schema:

| | Column | Written by | When | Cadence |
|---|---|---|---|---|
| **Session title** | `chat_sessions.title` | frontend (`PATCH /title`, first 7 words) — as today | once, after turn 1 | stable; the user may rename it |
| **Active subject** | `chat_sessions.subject` (unused today) | backend, `save_turn` | when `topic_changed` is true | changes with the conversation |

- The backend does **not** generate titles. The existing frontend rule is
  adequate and stable by construction; introducing an LLM title would add a
  call and a second writer to a user-editable field. One optional
  refinement, only if the new frontend wants it: at turn 1, if `title` is
  still the default `New Chat Sessions`, seed it from the routing result's
  `subject` in the same write transaction — one `UPDATE … WHERE title =
  'New Chat Sessions'`, never afterwards. Off by default.
- The subject is read in the read transaction into `SessionContext.subject`
  and given to the internal call as "current subject". It replaces the
  never-populated `current_topic`/`metadata.topic` path, which is left in
  place but no longer consulted (removing it is a cleanup for later, not
  part of this task).
- The subject is also what Task 3's summariser is told the conversation is
  currently about, and what the sidebar could show under the title later —
  no API change now; `SessionInfo` may gain an optional `subject` field when
  a frontend asks for it.

### 2.4 One internal configuration, several narrow tasks — coupling assessed

`INTERNAL_LLM_*` settings (`ENDPOINT`, `API_KEY`, `API_VERSION`,
`DEPLOYMENT`, `TIMEOUT_SECONDS`, `MAX_COMPLETION_TOKENS`), each defaulting
to the corresponding `AZURE_OPENAI_*` value when unset — so today the
internal model **is** the GPT-5 deployment with zero extra configuration,
and a cheaper deployment is a `.env` change. `build_internal_client(settings)`
sits beside `build_azure_openai_client` in `app/integrations/azure_openai.py`.

What shares the config: query routing (this task), memory summarisation
(Task 3), and — if the title seed is ever wanted — nothing more. What does
*not* share: prompts, output contracts, token caps, and the functions that
call it. The coupling that would be a mistake is one prompt doing several
jobs in one call (e.g. routing that also returns a memory summary): the
jobs have different cadences (every turn vs every ~6 turns) and different
failure semantics (§2.5). Keeping "one client, N single-purpose functions"
avoids it. Two real couplings to note:

- *Capacity:* while the internal model is the same deployment as the answer
  model, each turn makes two calls against one TPM quota. Watch 429s in the
  smoke; the swap to a separate deployment removes this.
- *Model swap asymmetry:* a smaller model that is fine for routing may be
  weaker at summarising. If that ever shows in the regression set, add
  `INTERNAL_LLM_DEPLOYMENT_SUMMARY` as a per-task override. Not now.

Provider swap (an OpenAI-platform model rather than Azure): the client
factory gains an `INTERNAL_LLM_PROVIDER=azure|openai` switch choosing
`AzureOpenAI` vs `OpenAI` from the same SDK; the calling code is unchanged
because it already depends on a `complete(messages) -> ChatCompletion`
function, not on a client class. Not built until needed.

### 2.5 Transactions and failure semantics

The internal call is one more external call in the pipeline phase; the
read → external calls → short write rule is unchanged:

```
read tx    session (incl. subject), recent history            — as today
close
pipeline   1. internal routing call  → RoutingResult (or fallback)
           2. search(query)
           3. answer call
           4. citations; result carries subject_update when topic_changed
write tx   user + assistant + updated_at (+ subject when provided)  — one transaction
```

- **Routing failure** (timeout, SDK error, unparsable JSON): degrade, do
  not fail the turn. Fallback = Phase 2C behaviour exactly: `query =
  question`, `topic_changed = false`. Logged at WARNING and recorded as
  `metadata.routing = "fallback"` on the assistant row so it is visible in
  history and countable by the regression harness. Rationale: the routing
  step improves retrieval; its absence yields the answer the system gave
  yesterday, which is still cited and still honest (Task 4). Failing a
  whole turn because an optional improvement timed out is the wrong
  trade. The answer call and the search keep their existing "fail the
  turn, persist nothing" semantics.
- **Subject persistence** happens only in the turn's own write transaction,
  so a failed answer never updates the subject — the stored subject always
  reflects a persisted turn.
- **Turn 1** (no history): the call still runs; it returns the normalised
  question as `query`, `topic_changed = true`, and the initial subject.
- Nothing runs after the write; nothing is deferred.

### 2.6 Cost and latency with GPT-5 first

Per turn today: search (~0.2 s) + answer (10–25 s, 1.5–7.5k tokens). The
routing call adds: input ≈ 300 (system) + up to ~3k (history window) + the
question; visible output ≈ 40 tokens; **hidden reasoning tokens** on gpt-5
— the Phase 2C probe showed 128 completion tokens was not enough to get any
visible text, so `INTERNAL_LLM_MAX_COMPLETION_TOKENS` starts at ~800 and the
budget is measured in the smoke. Expect **+3–8 s and roughly +25–40 % of
the turn's tokens** while the internal model is GPT-5. Two configuration
levers, both to be *probed* before relying on them (neither is in the proven
parameter set): `reasoning_effort="minimal"` for gpt-5 on Azure, which
should cut the hidden tokens and most of the latency, and JSON response
format. When a cheaper deployment exists, the swap is the `INTERNAL_LLM_*`
values only.

`ChatResponse.token_usage` and `cost_usd` become **turn totals** (answer +
internal calls) so the admin cost figures stay truthful; the assistant
`metadata` carries the per-call breakdown (`{"answer": …, "routing": …}`).
The response shape is unchanged.

### 2.7 Test strategy — properties, not strings

- *Unit (fake internal client, no network):* contract parsing and
  validation (`RoutingResult`), fallback on malformed JSON / timeout /
  SDK error with `metadata.routing = "fallback"`, `query` reaching the
  search function, `topic_changed` driving exactly one subject write and
  `false` driving none, subject ignored when `topic_changed` is false, turn
  totals = answer + routing tokens. Prompt assertions are structural only
  ("contains the question", "contains the last N messages", "contains the
  current subject") — never full-text equality.
- *Regression (real model, Task 5):* property checks that do not depend on
  the model's wording — a follow-up's routed query retrieves ≥ 1 document
  in common with the fully-specified reference question; a standalone
  question's routed query retrieves the same top document as the raw
  question; topic-switch cases record `topic_changed = true` and a changed
  `chat_sessions.subject`; continuation cases record `false`; fallback
  count = 0 in a healthy run. Human reads the routed queries in the table.
- *Contract test for model swaps:* the same regression run is the
  acceptance test for a cheaper internal model — no separate suite.

**Risks / trade-offs.** One more external dependency in the hot path
(mitigated by the degrade rule); latency and cost as in §2.6; a routing
model that over-rewrites standalone questions (mitigated by the "same top
document" property check, and by the prompt telling it to keep standalone
questions as they are). Non-determinism: routed queries vary between runs,
which is why the acceptance criteria are retrieval-overlap properties.

**Dependencies / order.** Phase 2, steps 2.1–2.3 — after Phase 1 (the
candidate set that the routed query searches); Task 3 follows in the same
phase and reuses the internal configuration and the subject.

**Non-goals.** No agent, planner, tool use or multi-step reasoning; no
intent taxonomy; no hand-written linguistic rules anywhere in the path; no
LLM-generated session titles (title seed is an optional, off-by-default
refinement); no per-turn subject writes; no schema change.

**Acceptance.** Task 5 follow-up cases: routed query shares ≥ 1 retrieved
document with the reference question; standalone cases: same top document
as the raw question; topic-switch cases: `topic_changed = true` and
`chat_sessions.subject` updated in the same transaction as the turn;
continuation cases leave `subject` untouched; routing fallback rate 0 in a
healthy run and the turn still answers when the internal call is forced to
fail (unit test); swapping `INTERNAL_LLM_DEPLOYMENT` requires no code
change (settings test).

## 3 Simple conversation memory (raw window + compacted summary) — Phase 2

**Current behaviour / code.** Raw window only: `get_recent_messages(limit=6)`
→ `ChatTurnInput.history`, truncated to 4000 chars each. Older turns fall out
of the window and are forgotten. The `chat_sessions` columns `memory_summary`
(TEXT), `memory_facts` (JSONB, default `[]`) and `memory_turns` (INTEGER,
default 0) exist, are **read** into `SessionContext` and returned by
`GET /api/sessions/{id}` and the sidebar, but are **never written** by this
backend — nor by the legacy API (`api.py` read them and never called
`update_session_memory`). `RagPipeline` ignores `memory_summary`/`memory_facts`.

**Minimal change — reuse the columns, no schema change, same transaction
shape as every turn.** The flow is stated once, unambiguously:

```
read tx    as today, plus: total message count of the session, and — only
           when compaction is due — the messages older than the raw window
           that the current summary does not yet cover
close
pipeline   if compaction is due: ONE short completion on the INTERNAL model
           (Task 2's configuration) turns (existing memory_summary + those
           older messages + the current subject) into a new summary ≤ 120
           words; then routing, search and the answer completion, with the
           summary (new or existing) as an extra system message
write tx   save_turn as today, plus UPDATE memory_summary / memory_turns
           when the pipeline produced a new summary — same transaction as
           the user + assistant rows
```

- *Due* is decided from read data only: `total_messages >
  CHAT_MEMORY_COMPACT_AFTER` (e.g. 12) **and** `total_messages −
  memory_turns ≥ CHAT_MEMORY_COMPACT_EVERY` (e.g. 6). `memory_turns` records
  the number of messages the stored summary covers.
- The pipeline receives the older messages in `ChatTurnInput` (a new
  optional field) and returns the new summary in `ChatTurnResult` (optional
  `memory_update`); `save_turn` writes it. No DB access from the pipeline,
  no work after the write, nothing deferred to the next turn.
- Prompt: a third system message `CONVERSATION SO FAR (context only, not a
  source of facts): <memory_summary>` when non-empty — the legacy `_chat`
  had exactly this block.
- `memory_facts` stays `[]`; no profile memory.

**Risks / trade-offs.** One extra completion every ~6 turns on long sessions
(bounded; logged), on the internal model — so it becomes cheap as soon as
that model is swapped. A failed summarisation is skipped for that turn
(logged; the turn still answers; due-ness is recomputed next turn, so it
retries naturally) — the same degrade rule as routing (§2.5). Summaries
drift; they are advisory context only and the
prompt says so. Reusing `memory_turns` changes its meaning from "turns
recorded" to "messages covered by the summary" — it is only displayed as a
number in the sidebar today; note it in `docs/architecture.md`. The
compaction turn is slower by one completion; acceptable at ≤ 1 in 6 turns.

**Dependencies / order.** Phase 2, step 2.4 — after Task 2 (reuses
`INTERNAL_LLM_*` and the active subject) and after Phase 1, so the
long-conversation regression case measures memory on a stable retrieval path.

**Non-goals.** No user-profile memory, no facts extraction, no vector memory,
no cross-session memory, no schema change, no `memory_facts` writes, no
summarisation on every turn, no background jobs.

**Acceptance.** A 16–20-turn Task 5 script (added with this task): after
compaction the session row has a non-empty `memory_summary` and
`memory_turns` > 0, written in the same transaction as that turn's messages
(a failed answer leaves both untouched); a question at the end referring to
turn 2's topic is answered consistently (human check); extra completions
over the run ≤ ⌈(turns − 6)/6⌉; sessions with ≤ 12 messages make zero extra
calls and their row stays untouched.

## 4 Grounding and citations (unsupported-answer behaviour) — Phase 3

**Current behaviour / code.** The prompt (`prompts.SYSTEM_PROMPT`) tells the
model to cite `[n]`, and "if the excerpts do not contain relevant
information, say so explicitly and then answer from general knowledge
WITHOUT citations". `citations.resolve_citations` keeps `sources[]` to cited
documents only and strips dangling numbers. Observed: the model *did* follow
this (Sendai case: "The knowledge base excerpts provided don't list the
priorities explicitly. From general knowledge, …", `sources=[]`). So the
behaviour Ryan asks for already exists in spirit; what is missing is that
(a) it relies on the model choosing to say so, in its own words, and (b) a
confident uncited answer and a cited one look the same to the frontend
except by reading the text.

**What is *not* proposed.** A grounded / partial / ungrounded verdict.
One citation does not establish support for the whole answer, and deciding
"partial" honestly would need per-claim checking — an LLM judge or a
scoring framework, both out of scope. Detecting a marker phrase the model
was asked to write is brittle for the same reason. So no verdict field.

**Minimal change — one fact, stated deterministically, plus a stronger
prompt rule.**

1. *The signal is what already exists:* `sources[]` is cited-only, so
   `sources == []` means exactly "no UNDRR knowledge-base document is cited
   in this answer". That is a fact the pipeline knows for certain, not a
   judgement. The frontend already receives it; no new field is required.
   (If a flag is preferred for rendering, `metadata.cited = len(sources)`
   on the assistant row is a two-line addition — optional.)
2. *Make the fact visible in the answer without trusting the model to
   phrase it:* when `cited == 0`, the pipeline itself prepends a fixed
   line — `_Not sourced from the UNDRR knowledge base._` — to the answer
   text (`citations.resolve_citations` already knows `cited`; this is one
   conditional in `RagPipeline.__call__`). Deterministic, identical every
   time, and the frontend can style it as a banner if it wants to.
3. *Stronger prompt rule* for the partially-covered case, where no
   machine signal is claimed: "Cite only claims the excerpts support. If
   the excerpts cover part of the question, answer the covered part with
   citations and state plainly which part is general information rather
   than UNDRR knowledge-base content. Never present general knowledge as if
   it came from the excerpts. Do not write your own 'not covered' preamble;
   it is added for you when nothing is cited." The last sentence stops the
   model duplicating the pipeline's notice.

**Risks / trade-offs.** `cited == 0` is a *coverage* fact, not a quality
one: an answer can cite one excerpt and still stray beyond it. That case is
governed by the prompt rule only, and Task 5's human read of the table is
how it is checked. The notice line is part of `response` text, so it is
stored in `chat_messages.content` and replayed by history — intended, since
the provenance should travel with the answer.

**Dependencies / order.** Phase 3, step 3.1 — after Phase 1 (more documents
offered → fewer uncited answers for questions the KB does cover) and Phase 2
(judged on the final regression run); independent of Tasks 2–3 in code.

**Non-goals.** No per-claim attribution checking, no reranker threshold as
a hard gate, no refusal mode, no LLM judge, no three-level verdict, no
change to how `sources[]` is built.

**Acceptance.** Task 5 unsupported / poor-KB cases return `sources=[]` and
a `response` beginning with the fixed notice; good-KB cases return ≥ 1
source and no notice; no answer ever contains a `[n]` that is not in
`sources[]` (already enforced by `resolve_citations`; keep the test); the
notice is inserted by the pipeline, not by the model (unit test with a fake
completion that contains no citations and no preamble).

**Result (2026-09-20):** implemented exactly so — `prompts.UNSOURCED_NOTICE`
(`_Not sourced from the UNDRR knowledge base._`) prepended in
`RagPipeline.__call__` whenever `resolved.cited` is empty; the prompt's
"say so" rule replaced by the cite-only-supported / partial-coverage rule
and "do not write your own preamble". The harness checks the invariant
*notice ⇔ no sources* on every turn; it held on all turns of every Phase 3
run. No verdict field, no scoring, no LLM judge.

## 5 Small regression set (~10 cases, before/after data) — Phase 1 baseline, Phase 3 final run

**Current behaviour / code.** No end-to-end evaluation exists. Unit tests
cover context selection, citations and orchestration with fakes; the db-marked
tests cover persistence with the stub pipeline. The only real-Azure runs were
the three manual smoke turns in the Phase 2C report.

**Minimal change.** A script `tools/regression_chat.py` (host-side tool, not
part of the image, like the legacy dashboard) that:

- reads `tests/regression/cases.json` — **10 cases** aimed at what Tasks 1,
  2 and 4 change (retrieval diversity, citations, follow-ups, topic switch,
  poor/unsupported retrieval). A case is a *script* (a list of turns) so
  follow-ups are expressible:

  | # | kind | example turns |
  |---|---|---|
  | 1 | direct, good KB | "What is the Early Warnings for All initiative?" |
  | 2 | direct, list | "List the four priorities for action of the Sendai Framework" |
  | 3 | direct, compare | "Compare the Sendai Framework with the Hyogo Framework" |
  | 4 | direct, poor KB | "What is the population of Nepal?" (not DRR content) |
  | 5 | unsupported | "Who is the current UNDRR head of ICT?" |
  | 6 | contextual follow-up | EW4All → "How about financing?" |
  | 7 | vague follow-up | Sendai priorities → "Which one matters most for cities?" |
  | 8 | short follow-up | GAR report → "When was it published?" |
  | 9 | topic switch | floods in Asia → "Now tell me about drought in the Sahel" |
  | 10 | switch then return | drought → "Back to the floods — what warning systems exist?" |

  Deferred until the feature they measure is under work: a 16–20-turn
  long-conversation case (added with Task 3), a non-English case and an
  out-of-scope/chit-chat case (added if and when those behaviours are
  tuned). The baseline is evidence for the next four tasks, not a suite.

- runs each script against a running API (`API_BASE`), creating a
  disposable user/session per case (same convention as
  `tests/test_db_sessions.py`, cleaned up afterwards);
- records per turn: HTTP status, latency, `token_usage`, `cost_usd`,
  `sources` (count + URLs), citation numbers found in the text, whether the
  Task 4 notice line is present, the routing result (`query`,
  `topic_changed`, fallback or not) and its tokens/latency, and the
  pipeline counters (`retrieved/selected/history/cited`) — either by reading the container log or, cheaper, by having the
  pipeline put those counters into the assistant `metadata` (a two-line
  change in the metadata dict; recommended, it also serves the admin
  dashboard later);
- writes `logs/regression/<timestamp>.json` plus a one-screen table;
  `--compare <a.json> <b.json>` prints deltas per case (latency, tokens,
  cost, documents offered, sources cited, notice present).

Expected-value fields in `cases.json` are deliberately loose: "expects ≥ 1
source", "expects `sources == []` and the notice", "expects a source shared
with turn 1" — graded automatically. Answer *quality* is read by a human
from the table; no LLM grader.

**Risks / trade-offs.** Real Azure calls: ~10 cases × ~1.5 turns × ~15 s ≈
4 min and a few cents per run; gpt-5 is not deterministic, so compare
trends and the automatic fields, not exact text. The KB changes over time,
so "poor KB" cases may become "good KB" — re-check expectations when the
index is rebuilt.

**Dependencies / order.** Built **first**, at the start of Phase 1, so the
baseline captures Phase 2C as is; re-run at the end of each phase; the
final run and tuning pass is Phase 3's last step. Only the notice column
and the metadata counters depend on Tasks 4 and 1.

**Non-goals.** No CI integration (needs secrets and minutes of Azure time),
no LLM-judged scores, no load testing, no golden answers, no long-memory or
multilingual cases in the first baseline.

**Acceptance.** `python tools/regression_chat.py` completes against the dev
stack, produces the JSON + table, and `--compare` of two runs prints a
per-case delta; a **baseline artifact** of Phase 2C is saved under
`logs/regression/` (this project has no git repository; nothing here
assumes one) before Task 1 starts.

**Result (2026-09-20):** built as described (`tools/regression_chat.py`,
`tests/regression/cases.json`, 10 cases / 16 turns; the pipeline counters
are stored in `chat_messages.metadata.stats`). Baseline saved before Task 1.
Two expectations turned out to be model judgements rather than retrieval
facts: `direct-poor-kb` (population of Nepal) cited KB documents in the
baseline and none after; `unsupported` (head of ICT) cited none in the
baseline and one after. Both belong to Phase 3 (unsupported-answer
behaviour); the expectations are left as written so Phase 3 has something
to fix. Follow-up cases fail `shares_source_with_reference` in both runs,
as expected before Phase 2.

## 6 Development phases and execution path

Three phases, in order. Each phase is one coherent change set; individual
features are tasks *within* a phase, never phases of their own. The
per-task rationale, alternatives and tests stay in §1–§5; this section is
the only place that states order.

### Phase 1 — Retrieval & Baseline — done 2026-09-20

*Purpose:* establish measurable current behaviour, then improve retrieval
quality and diversity before touching conversation intelligence.

*Outcome:* steps 1.1–1.3 complete; exit condition met (Sendai case 1 → 6
documents, 0 → 2 citations; mean documents offered 5.69 → 7.06; no change
to the citation contract, response shape or transaction rule; 101 unit
tests + 6 db tests pass). Cost of the extra evidence: +27 % tokens, +11 %
latency over the set — a Phase 3 tuning input, see §1 and §8 Round 4.

| Step | Task | What is done |
|---|---|---|
| 1.1 | Task 5 (§5) | regression harness `tools/regression_chat.py` + `tests/regression/cases.json` (~10 cases); run it against Phase 2C and **save the baseline artifact** under `logs/regression/` (no git repository exists; nothing assumes one) |
| 1.2 | Task 1 (§1) | split candidate count from document cap: `KB_TOP_K` → candidates (30–40), new `CHAT_KB_MAX_DOCUMENTS` (≈8); grouping / 3-chunk cap / char budgets already exist, so one document can no longer dominate what the model sees |
| 1.3 | Task 5 | re-run; compare before/after (documents offered, sources cited, latency, tokens, cost) |

*Preserved:* the citation contract (`[n]` renumbered, `sources[]`
cited-only), `ChatResponse` shape, the read → external calls → short write
rule.

*Exit condition:* better and more diverse KB evidence reaches GPT-5 on the
baseline cases (Task 1 acceptance: "Sendai four priorities" offers ≥ 4
distinct documents and is answered with ≥ 1 citation) with no regression on
the other cases and no change to Phase 2C behaviour otherwise.

### Phase 2 — Intelligent Conversation Flow — done 2026-09-20

*Purpose:* the semantic conversation layer as **one** development phase —
internal model, query understanding, active subject, recent context and
memory together — not separate mini-phases for intent, topic and memory.

*Outcome:* steps 2.1–2.5 complete; exit condition met on the regression set
(all three follow-up overlap properties pass, 0/3 before; topic switches and
returns detected with fully resolved queries; the 9-turn conversation was
compacted at turn 8 and turn 9 recalled turn-2 content from the summary).
Like-for-like on the 16 Phase 1 turns: latency +7.4 s, tokens per turn flat,
cost +6 %. Two implementation findings changed defaults (§8 Round 5):
`INTERNAL_LLM_MAX_COMPLETION_TOKENS` 800 → 3000 and
`CHAT_MAX_COMPLETION_TOKENS` 4000 → 8000, both because gpt-5's hidden
reasoning exhausted the cap and returned no text.

| Step | Task | What is done |
|---|---|---|
| 2.1 | Task 2 (§2.4) | `INTERNAL_LLM_*` logical configuration, each value defaulting to `AZURE_OPENAI_*`, so it is the confirmed GPT-5 deployment today and a `.env` change later; `build_internal_client` |
| 2.2 | Task 2 (§2.1–2.2) | internal routing call on every user turn → `{"query", "topic_changed", "subject"}`; no word-count / opener-list classifier anywhere in the path |
| 2.3 | Task 2 (§2.3, §2.5) | `chat_sessions.subject` read into context, written in the turn's write transaction only when `topic_changed`; routing failure degrades to the original question (Phase 2C behaviour), logged and marked `metadata.routing="fallback"` |
| 2.4 | Task 3 (§3) | recent raw window kept; compaction of older messages into `memory_summary` / `memory_turns` on the internal model when due, persisted in the same short write as the turn; summary and subject given to the prompts |
| 2.5 | Task 5 | add the long-conversation case; re-run the set; record routing tokens/latency |

*Preserved:* short DB transactions, no partial persistence (search and
answer still fail the turn and persist nothing; routing and summarisation
degrade instead), `ChatResponse` shape (`token_usage`/`cost_usd` become
turn totals with a per-call breakdown in metadata).

*Exit condition:* natural follow-ups, meaningful topic changes and longer
conversations work without brittle linguistic rules — Task 2 and Task 3
acceptance criteria met on the regression set (retrieval-overlap
properties, `subject` and `memory_summary` persisted only with successful
turns, fallback rate 0 in a healthy run).

### Phase 3 — Grounding & Final Tuning — done 2026-09-20

*Purpose:* harden answer grounding once retrieval and conversational
intelligence are stable, then measure and tune the whole flow.

*Outcome:* see §8 Round 6. Grounding notice implemented (3.1); probes of
`reasoning_effort` and JSON mode on the real gpt-5 deployment (3.3): both
accepted; `reasoning_effort` is the lever — answer call 27–44 s → 13–21 s
at `low` with citations preserved, routing ~9 s → ~3.6 s at `low`
(`minimal` is ~2 s but misjudges a return to an earlier subject); JSON mode
gives nothing over the tolerant parser. Operating point set in `.env`:
`CHAT_REASONING_EFFORT=low`, `INTERNAL_LLM_REASONING_EFFORT=low`. Retrieval
limits, budgets, caps and compaction thresholds left as measured in Phases
1–2 (no evidence justified changing them).

| Step | Task | What is done |
|---|---|---|
| 3.1 | Task 4 (§4) | deterministic notice line prepended by the pipeline when nothing is cited (`sources == []`); stronger prompt rule for partially covered questions; no semantic confidence scores, no verdict field, no LLM judge |
| 3.2 | Task 5 | final regression run over follow-ups, topic switches, memory, weak retrieval and unsupported questions; compare with the Phase 1 baseline artifact |
| 3.3 | tuning | set `KB_TOP_K`, `CHAT_KB_MAX_DOCUMENTS`, compaction thresholds, `INTERNAL_LLM_MAX_COMPLETION_TOKENS` and the prompts from the measured results; probe `reasoning_effort=minimal` / JSON response format for the internal call (§2.6) |
| 3.4 | verification | record latency / token / cost impact per phase in the artifact; confirm that replacing the internal model is `INTERNAL_LLM_*` configuration only (settings test, one smoke run with the values pointed elsewhere if a second deployment exists) |

*Preserved:* cited-only `sources[]`, the response shape, the transaction
rule.

*Exit condition:* stable chatbot flow ready for frontend integration.

### Overall flow

```
Phase 1  Baseline + Retrieval
→ Phase 2  Internal LLM + Context + Subject + Memory
→ Phase 3  Grounding + Regression + Tuning
→ Frontend integration (out of scope here; see docs/architecture.md §4)
```

*Why this order (kept from the earlier sequence assessment):* the harness
comes first because a baseline cannot be taken retroactively; retrieval
(Task 1) is the largest quality lever with the smallest change and the
routed query of Phase 2 searches the candidate set it produces; memory
reuses the internal configuration and the subject that Task 2 introduces,
so the two belong in one phase; grounding is a presentation/prompt change
that benefits from more documents being offered (fewer uncited answers for
covered questions) and is best judged on the final regression run, which is
also when the new settings are tuned from evidence rather than defaults.
None of the phases needs a schema change, a new dependency or a change to
the read → external calls → short write boundary.

## 7 Already implemented (do not rebuild)

| Item in the brief | Status in Phase 2C | Where |
|---|---|---|
| grouping chunks by document | done | `context.select_documents` |
| small per-document chunk cap | done (`CHUNKS_PER_DOCUMENT = 3`) | `app/chat/context.py` |
| fixed context budget | done (per-doc 6000 chars, total 40000) | `CHAT_KB_EXCERPT_CHARS`, `CHAT_KB_CONTEXT_CHARS` |
| larger candidate set | **not** done — `top` = `max_documents` = `KB_TOP_K` | Task 1 |
| recent raw messages retained | done (6, per-message 4000 chars) | `CHAT_HISTORY_MESSAGES`, `select_history` |
| session summary compaction | not done; columns exist, read, never written | Task 3 |
| cited-only `sources[]` | done | `citations.resolve_citations`, `sources_for` |
| "say so when KB is silent" | done as prompt prose, at the model's discretion; `sources == []` is already the machine-readable fact | Task 4 (deterministic notice + stronger rule) |
| follow-up / context understanding | not done; `current_topic` plumbing exists but is always `None` | Task 2 (internal LLM) |
| active subject | `chat_sessions.subject` exists, never used | Task 2 |
| separate internal-model configuration | not done | Task 2 (`INTERNAL_LLM_*`, defaults to `AZURE_OPENAI_*`) |
| regression harness | not done; per-turn counters already logged | Task 5 |
| read → external calls → short write | done; Task 3 keeps it (summary written in the turn's own write tx) | `app/api/routes/chat.py` |
| retrieval failure / empty answer → 502, no partial persistence | done | Phase 2C transaction rule |

Non-goals across all five tasks, restated once: no online retrieval, no
intent/entity/spaCy, no LIST/REFUSAL modes, no user-profile memory, no schema
changes, no new dependencies, no change to the read → pipeline → write
transaction boundary.

## 8 Review log (2026-09-20)

Objections raised in review, and what this revision did with them:

| # | Objection | Decision | Why |
|---|---|---|---|
| 1 | Task 2's classifier (word count, cue words, pronouns, capitalisation, content-word length) is a brittle mini-NLP layer | **Accepted** | Two signals (≤ 6 words, or a fixed opener list) capture the observed failure ("How about financing?"); the other three signals only add ways to be wrong. Misclassification cannot change what the model is asked, only the search text, so the cheap rule is safe to start from and the regression set says whether it needs more. |
| 2 | Task 3 stated compaction "after the write" and "in the next turn's write phase" — ambiguous | **Accepted** | Restated as one flow: due-ness decided from read data, summary produced in the pipeline outside any transaction, persisted in the same short write as the turn's messages. Simpler than the deferred-to-next-turn plumbing and keeps the Phase 2C rule intact. |
| 3 | `cited ≥ 1` is not "grounded"; marker-phrase detection is brittle; don't expose a verdict we cannot back | **Accepted, with one retained element** | The three-level verdict is dropped. What stays is the one deterministic fact the pipeline does know — `cited == 0` — surfaced as a fixed notice line the *pipeline* prepends (not a phrase the model is trusted to write) and as the existing `sources == []`. Partial coverage is a prompt rule only, with no machine claim. |
| 4 | 16-turn, Spanish and chit-chat cases are premature for the first baseline | **Accepted** | Baseline cut to 10 cases on what Tasks 1/2/4 change; long-conversation case is added with Task 3, the others when those behaviours are tuned. |
| 5 | Say "save baseline artifact", not "commit" | **Accepted** | Wording fixed; the document no longer assumes any git state. |
| — | Proposed sequence: harness → retrieval → follow-up → memory → unsupported-answer → final regression/tuning | **Accepted** | Identical to the original order with a final re-run/tuning step appended, which is an improvement: the new settings should be set from measurements, not defaults. (Now expressed as the three phases in §6.) |

### Round 2 — design adjustment (internal LLM replaces the heuristic)

Ryan's clarified requirement: no hand-written linguistic rules as the
intelligence layer; an internal LLM with its own configuration does query /
context understanding, initially on the same GPT-5 deployment. Assessed
against the seven questions raised:

| # | Question | Position taken | Why |
|---|---|---|---|
| 1 | Is one internal call per turn the cleanest design? | **Yes** (§2.1 option C). | Every cheaper alternative either reintroduces a heuristic gate (A), gives up semantics (B), or adds concurrency for a latency win nobody has measured yet (E). D (skip on turn 1) is the only saving worth revisiting, later. |
| 2 | Smallest useful output contract | **Three fields:** `query`, `topic_changed`, `subject` (null unless changed). | Each has exactly one consumer. `context_used` (from the brief) was dropped: nothing branches on it and it is derivable for logs. No intent labels, no `needs_retrieval`, no title. |
| 3 | Subject vs title | **Two columns, two writers, two cadences:** `title` stays the frontend's (set once); `subject` — the existing unused column — is written by the backend only when `topic_changed`. | No schema change; no LLM titles; the "not every turn" rule is enforced by the contract, not a timer. Optional off-by-default title seed at turn 1. |
| 4 | One internal config for routing, subject, summarisation | **Fine — one client, N single-purpose functions.** The coupling to avoid is one prompt doing several jobs in one call. | Different cadences and failure semantics per task. Two real couplings named: shared TPM quota while the deployment is shared; possible per-task deployment override later. |
| 5 | Transactions / failure | **Routing degrades to Phase 2C behaviour** (query = question, no subject change), logged and marked in metadata; search and answer keep "fail the turn, persist nothing"; subject is written only in the turn's own write transaction. | An optional improvement must not fail the turn; the fallback is yesterday's well-tested path. The two-transaction rule is untouched. |
| 6 | Tests | **Properties, not strings:** fakes for the contract/fallback/persistence, retrieval-overlap and `topic_changed` properties in the regression set, structural prompt assertions only. | Model output wording is not stable; document overlap and persisted state are. |
| 7 | Cost / latency | **+3–8 s and +25–40 % tokens per turn on GPT-5**, measured in the smoke; two probes (`reasoning_effort=minimal`, JSON response format) may cut most of it; model swap is `INTERNAL_LLM_*` only, provider swap a factory switch. `token_usage`/`cost_usd` become turn totals with a per-call breakdown in metadata. | Honest accounting for the admin dashboard; the response shape does not change. |

Where this revision disagrees with the brief: (a) no separate "context was
required" field; (b) the backend does not generate session titles; (c) the
internal call always runs — no gating of any kind — because a gate that
reads the question text is the heuristic being removed.

### Round 3 — organisation into three development phases

No design change. The execution path was consolidated into §6 as three
phases (Retrieval & Baseline → Intelligent Conversation Flow → Grounding &
Final Tuning) with purpose, steps, preserved contracts and exit conditions;
the former "sequence assessment" text was folded into §6's closing
rationale so ordering is stated in one place. Tasks 1–5 keep their detail
and are tagged with their phase in their headings; the phase map in the
introduction gives the same view in one table.

### Round 4 — Phase 1 implemented (2026-09-20)

Implementation findings to review before Phase 2:

1. **"No extra tokens" was wrong.** §1 said a larger candidate set costs no
   tokens because the budgets cap the prompt. The cap is unchanged, but the
   *average* prompt grew 36 % (83.6k → 113.8k prompt tokens over 16 turns)
   because most turns now reach the 8-document cap where they used to offer
   1–4 documents. End-to-end latency +11 % (30.2 → 33.5 s mean), cost
   +13 % ($0.46 → $0.52 for the set). This is the evidence Ryan asked for;
   whether 8 documents / 40 000 chars is the right operating point is a
   Phase 3 tuning decision (`CHAT_KB_MAX_DOCUMENTS`, `CHAT_KB_CONTEXT_CHARS`).
2. **Latency is dominated by the answer call**, not search: 15–55 s per
   turn on gpt-5 with 5–14k-token prompts. Phase 2 adds a routing call on
   top; §2.6's +3–8 s estimate stands, but the absolute numbers mean the
   frontend must be designed for 30–60 s turns unless Phase 3 tuning (or a
   faster deployment) brings them down.
3. **Two regression expectations are model judgements** (`direct-poor-kb`,
   `unsupported`) and flipped between runs on retrieval changes alone.
   They stay in the set as Phase 3 targets; they should not be read as
   Phase 1 regressions.
4. **`.env` still sets `KB_TOP_K=15`.** The code default is now 40 and
   `.env.example` documents it, but the deployed `.env` was not edited (the
   Phase 1 run injected `KB_TOP_K=40` / `CHAT_KB_MAX_DOCUMENTS=8` through a
   temporary compose override). Ryan should update `.env` — or delete the
   line to take the default — before the next deployment.
5. **Artifacts live under `logs/regression/`**, which is excluded from the
   Docker build context (`.dockerignore`) but not from `.gitignore`; if a
   git repository is created later, decide then whether the baseline JSON
   files are tracked or ignored.

### Round 5 — Phase 2 implemented (2026-09-20)

What was built follows §2 and §3 as revised, with these implementation
findings and one deliberate deviation:

1. **gpt-5 reasoning vs. completion caps (two defaults changed).** The
   first Phase 2 run used `INTERNAL_LLM_MAX_COMPLETION_TOKENS=800`; 8 of 25
   routing calls and both compactions returned no visible text
   (`finish_reason=length`) and degraded as designed — every turn still
   answered, `metadata.routing.fallback=true`, no errors. Successful routing
   calls measured 0.7–2.8k tokens, so the default is now **3000** (1 fallback
   in 25 afterwards). The same run exposed the *answer* cap: two long
   answers ("compare", "back to the floods") hit `CHAT_MAX_COMPLETION_TOKENS
   =4000` and returned 502 — a pre-existing Phase 2C exposure that larger
   prompts made visible. Default now **8000**; both cases pass on re-run.
   A cap is a maximum, so neither change alters the token/latency
   trade-off Ryan deferred to Phase 3; `reasoning_effort=minimal` (§2.6)
   remains the Phase 3 lever to actually cut these tokens.
2. **Raw window = every uncovered message (deviation from "window kept
   exactly as is").** With a fixed 6-message window, messages between the
   summary's coverage and the window would be invisible for up to
   `COMPACT_EVERY − 1` messages between compactions. The read step now
   loads all messages the summary does not cover — at least
   `CHAT_HISTORY_MESSAGES` (6), at most `CHAT_MEMORY_COMPACT_AFTER` (12) —
   so nothing falls through; the pipeline no longer re-truncates the
   window. Cost: up to 6 extra raw messages on some turns. Due rule:
   `total > COMPACT_AFTER and total − window − memory_turns ≥ COMPACT_EVERY`
   (`services.sessions.MemoryPolicy`).
3. **Compaction runs inside the same turn** (as revised in Round 1): the
   read step returns the older messages when due, the pipeline summarises
   on the internal model before retrieval, the write step persists
   `memory_summary`/`memory_turns` with the turn. The answer call already
   sees the new summary on the compaction turn.
4. **Routing quality on gpt-5:** rewrites are precise and often *more*
   specific than the reference question ("How about financing?" →
   "Financing of the Early Warnings for All initiative (EW4All): …"); on the
   memory case, turn 9's query was rebuilt from the summary ("Which example
   cities were cited … (Lisbon, Matosinhos, …)"). Standalone questions are
   mostly kept or lightly normalised. One judgement call to watch: "Back to
   the floods" was classified `topic_changed=true` with a new subject — a
   return is a change *from the current subject*, which is defensible, but
   it means the subject history is not kept (only the current one is).
5. **Cost accounting:** `token_usage`/`cost_usd` are turn totals;
   `metadata.usage` carries the per-call breakdown (tokens, `latency_s`,
   `cost_usd` for answer / routing / memory, and search latency). Measured
   on gpt-5 for both roles (6-turn sample, artifact
   `…T023354Z-phase2-percall-timing.json`): **routing 7–13 s and
   $0.003–0.009 per turn (mean 9.2 s, 1.0k tokens, $0.0057) versus the
   answer call 10–35 s and $0.008–0.036 (mean 29.3 s)**; search 0.4–2.3 s.
   Routing is ~23 % of turn latency and ~17 % of turn cost today — the
   incremental price of intelligent routing on gpt-5, and the number a
   cheaper `INTERNAL_LLM_DEPLOYMENT` is measured against. The compaction
   turn adds one ~2.4k-token call.
6. **Artifacts:** `logs/regression/20260920T014117Z-phase2-conversation.json`
   (cap 800 — kept as the evidence for finding 1),
   `…T020213Z-phase2-conversation-cap3000.json` (the Phase 2 result),
   `…T022402Z-phase2-rerun-failed-cases-cap8000.json` (the two 502 cases
   re-run). `.env` still untouched (`KB_TOP_K=15` there; all Phase 1/2
   settings were injected through the temporary compose override).

### Round 6 — Phase 3 implemented (2026-09-20)

1. **Grounding (Task 4)** as designed: pipeline-prepended notice when
   nothing is cited; prompt rule for partial coverage; no verdict field.
   Invariant *notice ⇔ no sources* checked by the harness on every turn of
   every Phase 3 run: no violation.
2. **Probes on the real gpt-5 deployment** (single calls, then full runs):
   `reasoning_effort` accepted (`minimal`/`low`), `response_format=json_object`
   accepted. Routing: baseline ~9 s / ~560 completion tokens (512 hidden
   reasoning) → `minimal` ~2 s / ~50 → `low` ~3.6 s / ~160, all producing
   equivalent rewrites; JSON mode alone ~8 s (no gain), and no gain over the
   tolerant parser. Answer call on two real questions: baseline 27–44 s →
   `low` 13–21 s → `minimal` 11–12 s, citations preserved (4→4→6, 3→3→3).
   Both wired as **opt-in settings** (`CHAT_REASONING_EFFORT`,
   `INTERNAL_LLM_REASONING_EFFORT`, `INTERNAL_LLM_JSON_MODE`), sent only when
   set; the proven request shape stays the default, and a deployment that
   rejects them answers 400 → `IntegrationError("request")` (routing
   degrades; the answer call fails the turn visibly).
3. **Kept:** routing `low`, answer `low` (in `.env`). **Rejected:** routing
   `minimal` — 2 s faster but classified "Back to the floods" as `same` 3/3
   even after the prompt was clarified to count a return to an earlier
   subject as a change (that clarification is kept); at `low` the return is
   `topic_changed=true` 3/3 with a fully resolved query. **Rejected:** JSON
   mode (no measurable benefit). **Not changed** (no evidence justified it):
   `KB_TOP_K=40`, `CHAT_KB_MAX_DOCUMENTS=8`, the char budgets, the
   completion caps, `CHAT_MEMORY_COMPACT_AFTER/EVERY`, the reranker-score
   floor idea (never built).
4. **Final regression** (`logs/regression/20260920T032351Z-phase3-final-low-low.json`,
   11 cases / 25 turns, 0 errors, 0 routing fallbacks, 1 compaction):
   like-for-like on the 16 Phase 1 turns — latency **33.5 s (P1) → 40.9 s
   (P2) → 18.6 s (P3)**, tokens/turn 9.5k → 9.3k → 7.8k, cost $0.52 → $0.56
   → $0.31, sources/turn 3.0 → 2.9 → 2.8, turns with sources 14/16 in all
   three. Long conversation: 51.6 s → 21.8 s per turn, $0.435 → $0.208; turn
   9 still recalls the example cities from the compacted summary. Per call:
   routing 4.9 s / 1.0k tokens / $0.003 per turn; answer 14.0 s; search
   < 1 s — routing is now ~25 % of a 20 s turn and ~15 % of its cost.
   Failed expectations: 2, both model-judgement cases: `direct-list`
   (Sendai) cited 0 this run — 0/1/2 citations across four runs at `low`,
   and when uncited the notice appears, which is the Phase 3 contract;
   `direct-poor-kb` (population of Nepal) cited one KB document. Neither
   is a regression of the flow; both are what the notice exists for.
5. **Configuration-only internal model swap** re-verified: the unit tests
   cover own-values vs fallback; nothing in the pipeline references the
   model; `INTERNAL_LLM_REASONING_EFFORT` is a per-role setting, so a
   non-reasoning replacement simply leaves it unset.
6. **Known limitations before frontend integration:** turns are still
   10–30 s on gpt-5 (the answer call dominates; a smaller/faster internal
   deployment would shave ~5 s more); only the current subject is kept, no
   subject history; the notice is a coverage fact, not a quality judgement
   — an answer can cite one excerpt and still add unsupported prose, which
   only the prompt rule governs; `sources[]` remains cited-only, so a reader
   who wants "what was retrieved" has `metadata.stats` only; the regression
   set is 11 scripted cases with loose properties and a human read of the
   table, not a quality benchmark.
