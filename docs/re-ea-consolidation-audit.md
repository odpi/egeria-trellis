# RE ↔ EA consolidation audit: beyond ingestion

> **Status (2026-11-19, 6.3 Tech Preview):** a completed audit (August 2026). Its extractions that went ahead are `trellis-querycache`, `trellis-auth`, `trellis-feedback`, `trellis-ui` and `trellis-accel`; the verdicts themselves are not maintained.

## Why this doc exists

`docs/ingestion-pipeline-audit.md` did the audit-before-extract pass for the ingestion
pipeline specifically. This doc does the same thing for the rest of Resource Explorer (RE)
and Egeria Advisor (EA) — the query/LLM layer, persistence, agent framework, feedback,
observability, and web app structure — following the same method and the same three
verdicts:

- **(a) near-identical/copy-paste** — extract as-is
- **(b) diverged for a confirmed reason, or diverged in maturity** — port the more mature
  side's approach, or extract the shared primitive underneath a still-separate schema/content
- **(c) genuinely different problems** — leave alone

Two investigations fed this doc: one across the LLM client, query cache, intent
classification/routing, collection router, and prompt templates; one across the
registry/persistence layer, agent/tool framework, feedback/analytics, MLflow/Phoenix
observability, and web app structure. Ingestion, embeddings, and code-symbol extraction are
out of scope here — see the ingestion audit for those.

---

## Real extraction candidates

### 1. Query cache — (a), and it fixes a live bug in EA

| | RE `query_cache.py` | EA `query_cache.py` |
|---|---|---|
| Lines | 124 | 169 |
| Eviction | `OrderedDict` with `move_to_end()` on both get and set — genuine LRU | plain `dict`, no reordering on access — **FIFO eviction of the oldest insertion, despite being named and documented as LRU throughout** |
| Extras | TTL, optional Redis backend (`CACHE__BACKEND=redis`), `invalidate_project()` for project-scoped busting | hit/miss counters, `get_stats()`/`most_popular` telemetry |

**Verdict: (a) — extract as-is, RE's design as the base.** Same shape (hash `(query + params)` →
cached response, evict at capacity), and EA's version has a real, currently-shipping bug: it
isn't LRU. A shared `QueryCache` built from RE's TTL/Redis/invalidation design, with EA's
stats/`most_popular` reporting layered on top, gives EA a correctness fix for free rather than
costing it a feature.

### 2. Agent base class — (a), copy-pasted, not convergent

| | RE `agents/base.py` | EA `agents/base.py` |
|---|---|---|
| Lines | 200 | 83 |
| Class | `BaseExplorerAgent(ABC)` | `BaseAdvisorAgent(ABC)` |

Both hand-roll the same BeeAI `RequirementAgent` construction (`_build_agent()`) and the same
"sync caller in an async context → spawn a thread with a fresh event loop" workaround
(`_run_agent()`) — down to matching inline comments explaining why BeeAI needs a fresh event
loop in a thread. This is copy-pasted logic, not two teams independently arriving at the same
idiom.

**Verdict: (a) — extract as-is**, at the same tier as `trellis-microflow`: a shared
`BeeAIAgentRunner`/`BaseAgent` mixin covering `_build_agent()`/`_run_agent()`. App-specific
pieces stay put — RE's slug-inference/clarification helpers, and EA's separate, apparently-dead
"legacy... not using BeeAI" `BaseAgent` scaffolding (worth a quick check on whether that second
EA class is still referenced anywhere, independent of this extraction).

### 3. Registry connection-management pattern — (b), extract the primitive, not the schema

RE's `registry.py` (5235 lines, ~40 tables) has a mature dual-backend abstraction:
`ConnectionWrapper` translates SQLite↔Postgres placeholders/DDL, a SQLAlchemy engine handles
pooling (`pool_pre_ping=True, pool_recycle=1800`), and same-transaction column introspection
works around a real Postgres visibility bug (documented in the code). RE already reuses this
consistently — `registry.py`, `observability/metrics_collector.py`, and `feedback_store.py` all
build on it.

EA's `db_consolidated.py` (`ConsolidatedDBManager`, 283 lines) reinvents a narrower version of
the same idea: Postgres-only, raw `psycopg2.pool.ThreadedConnectionPool`, one big DDL string run
once at `connect()`, plus a stack of `ALTER TABLE ADD COLUMN IF NOT EXISTS` migrations. EA's own
`metrics_collector.py` (531 lines, richer schema — audit/policy/perspective columns, `psutil`
system metrics) builds on `ConsolidatedDBManager`.

**Verdict: (b) — the schemas stay separate** (RE's is project/resource state, EA's is
metrics/audit/symbol tables — genuinely different data, not duplicated), **but the connection-
management primitive underneath should move to EA.** This is the same shape as the
Java-symbol-extraction finding in the ingestion audit: one app's implementation is simply more
mature and portable, and the other never adopted it. Not urgent — EA's Postgres-only assumption
holds today — but worth doing before EA needs a SQLite fallback path RE already solved.

---

## Adopt-the-other's-approach (not extraction — port the more mature implementation)

### 4. Phoenix/OTel tracing — EA has none

RE's `observability/phoenix_client.py` (47 lines) is a real, hardened Arize Phoenix/OTel setup:
`openinference.instrumentation.beeai` + `BatchSpanProcessor`, a documented reachability
pre-check that avoids a measured 7.89-second dead-collector penalty, non-blocking export.
`grep` for phoenix/Phoenix across `advisor/` returns nothing — EA has zero tracing beyond
MLflow. Since RE's client is config-driven and BeeAI-specific (both apps use BeeAI), it's
plausibly adoptable by EA close to verbatim, not a rewrite.

### 5. MLflow tracking — RE's is a stub next to EA's mature implementation

RE's `observability/mlflow_tracking.py` is 34 lines (`log_query()`, reachability-gated,
best-effort, exceptions swallowed). EA's `mlflow_tracking.py` is 742 lines — full
experiment/run structuring, a background daemon thread, agent-level tracking. Not urgent for
RE today, but EA's is the reference to build toward if RE's MLflow needs grow.

### 6. RAG-answer feedback — EA's is the mature reference

RE's `observability/feedback_collector.py` (21 lines) is a thumbs-up/down CLI prompt. EA's
`feedback_collector.py` (652 lines) is a full `FeedbackEntry` system — star rating, category,
perspective, routing agent, sentiment — feeding `analytics.py`/`enhanced_analytics.py`/
`sentiment_analysis.py`. (This is distinct from RE's separate `feedback_store.py` — product
bug-report/triage, no EA equivalent at all, see item 8 below; not the same concept.) If RE ever
wants richer RAG-answer feedback, EA's is what to port from, not build fresh.

### 7. EA's `web/app.py` should refactor toward RE's router-per-domain pattern

RE's `web/app.py` (106 lines) is a clean factory: `lifespan` context manager, 20
`include_router()` calls to one file per domain in `web/routes/`, and a deliberate `/health`
(pure liveness) vs. `/health/ready` (DB-exercising) split — fixing a real prior incident where
liveness lied about DB reachability. EA's `web/app.py` is 1924 lines — ~50+ endpoints decorated
directly on `app` (including streaming SSE and 25+ plan/draft CRUD endpoints), with only
`admin.py` actually extracted as a router.

**Not a shared-package move** — the route sets don't overlap — but a real, mechanical,
EA-internal refactor toward RE's proven structure. Auth is a separate, one-way gap noted here
rather than folded in: EA has full JWT + Portal SSO (`advisor/auth.py`, 255 lines); RE has only
a narrow admin-token gate for one feature (`web/admin_auth.py`), explicitly documented as not a
general auth system. Not a deficiency in RE (no multi-user concept yet), and not something to
port mechanically if RE ever needs it — EA's JWT claims are Egeria-credential-shaped and
EA-specific, a reference to design from, not a module to import.

### 8. LLM client — asymmetric capability, no urgent action

RE's `llm_client.py` (123 lines) is a clean `LLMBackend` Protocol with three interchangeable
providers (Ollama/OpenAI/Anthropic). EA's `llm_client.py` (508 lines) is Ollama-only but has
real capabilities RE lacks: a `_ModelOverrideClient` wrapper so `get_planning_llm()` pins a
second, slower model (`qwen2.5-coder:32b`) for LGCI planning while `get_ollama_client()` stays
on the fast Q&A model (`llama3.1:8b`), plus a thread-local streaming-callback shim so any
`generate()` call transparently streams when a caller installed a hook. Only the low-level
Ollama HTTP boilerplate duplicates in spirit. **Verdict: (c) for now** — RE has no multi-model
need today and EA has no multi-provider need today; each app's design fits its current problem.
Flagged as the reference to reach for if either need arises, not something to build ahead of it.

---

## Confirmed non-duplicates — stay separate

Reconfirms the dynamic-vs-fixed-catalog architecture split the ingestion audit already found,
in three more places:

- **Query intent classification/routing.** RE: one flat regex classifier, 10 intents, no LLM
  fallback (`query_processor.py`, 48 lines). EA: a priority-tiered pattern system
  (`query_processor.py`, 674 lines, CRITICAL→FALLBACK tiers, runtime-extensible, MLflow-wired)
  plus a distinct LLM fallback for ambiguous cases (`llm_intent_classifier.py`, 99 lines, 6
  categories with documented anti-conflation rules). This tracks EA's larger routing surface
  (10+ specialist agents — commands, CLI, reports, code-intel, plan lifecycle) against RE's
  narrower repo/db/filesystem survey scope. The one shareable idea is a pattern ("priority-
  ordered regex classifier with LLM fallback on ambiguity"), not a ready-made implementation.
- **Collection routing.** RE: intent→content-type lookup intersected with whichever dynamic
  per-project collections exist (`collection_router.py`, 57 lines). EA: a real scoring engine
  over its small fixed catalog — explicit mention, domain-term overlap, intent-keyword
  boosting, policy boosts, feedback-adjustment multipliers, an informational-query guard, and
  fallback expansion (`collection_router.py`, 400 lines). Same filename and class name, same
  divergence the ingestion audit already confirmed for the collection *model* itself.
- **Prompt templates.** Both are pure string content keyed to each app's own agent/collection
  taxonomy — no logic to extract, domain-specific by nature.

Also genuinely distinct, not duplicates despite matching filenames:

- **`feedback_store.py` (RE only).** Product bug-report/triage (bug/confusing/suggestion/
  praise), ported from Egeria Workspaces Portal, with its own admin-auth gate. No EA concept
  maps to this.
- **Auth.** EA's JWT/Portal-SSO system and RE's narrow admin-token gate solve different
  problems at different scopes — not comparable, not a gap.
- **Specialist agent rosters.** RE's are RAG-routing specialists (stats/code/doc/health/compare/
  examples); EA's are command/report/plan specialists (Dr.Egeria, CLI, governance plans, report
  specs). Filenames that coincide (`doc_agent.py`, `examples_agent.py`) implement different
  domains on inspection.
- **`@tool` function content.** Both projects independently document the same BeeAI gotcha
  (`FunctionTool` has no `.func`, use `_raw()`) in their own CLAUDE.md — shared *pattern
  knowledge*, already captured in writing on both sides, not shared *code*. The tool functions
  themselves are domain-specific (RE: project/database/filesystem search; EA: Egeria/pyegeria
  symbol/template search).

---

## Not an RE/EA question — flagged for EA to check independently

EA appears to carry **three parallel-looking classifiers**: `query_processor.py` (674 lines),
`query_classifier.py` (487 lines), and `llm_intent_classifier.py` (99 lines).
`query_classifier.py`'s `QueryType`/`QueryTopic` dataclass system looks structurally redundant
with `query_processor.py`'s own pattern system — worth a standalone check on whether it's still
live/called anywhere, independent of anything RE-related. Not investigated further here since
it isn't a consolidation question.

---

## Recommendation

1. **Extract the query cache (item 1)** — highest confidence, fixes a real bug in EA, lowest
   risk of the three extraction candidates.
2. **Extract the BeeAI agent base/runner (item 2)** — near-identical duplication with a clear
   boundary; same tier as `trellis-microflow`.
3. **Extract the registry connection-management primitive, EA adopts it (item 3)** — schemas
   stay separate; only the connection/DDL-provisioning mechanism moves.
4. **Items 4–8 are "port when needed," not scheduled work** — real gaps, but each is a
   maturity asymmetry the less-mature side can reach for later rather than a duplication
   costing anything today.
5. **Don't touch** the confirmed non-duplicates — each is a reasonable divergence tracking a
   real architectural difference between the two apps.

## Follow-ups (not part of this pass)

- Check whether EA's `query_classifier.py` is still live, independent of this audit.
- Check whether EA's second, "legacy... not using BeeAI" `BaseAgent` class (distinct from
  `BaseAdvisorAgent`) is still referenced anywhere, before or alongside item 2's extraction.
- `advisor/code_symbol_store.py` is still imported by `ingest_to_milvus.py`, which is likely
  dead code per the ingestion audit's Java-symbol-extraction findings — worth confirming
  whether that import path is reachable at all.
