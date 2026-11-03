# Resource Explorer — Architecture

**Status:** live. Module map, flows and key decisions; the zone passage was corrected 2026-10-14.

**Last revised:** 2026-08-30. Supersedes the 2026-06-10 revision, which described a
SQLite-only registry, ten sub-surveyors and eleven web routes — the counts are now 51 tables,
33 sub-surveyors and 25 route modules.

This document describes **shape**: layers, boundaries, and how work flows through them. It
deliberately does not enumerate every module, because the previous revision did and that is
precisely what went stale — 157 modules existed that it did not mention. Directories and
responsibilities change slowly; file lists change weekly.

- Conventions and rules for *working on* this code: [`../CLAUDE.md`](../CLAUDE.md)
- Getting it running: [the workspace quickstart](../../../QUICKSTART.md)
- What is being worked on: [`Backlog.md`](Backlog.md)
- The process and threading model — every daemon thread, per-request thread and
  `ThreadPoolExecutor` in the current one-process design, plus the target multi-role topology
  from `docs/runtime-architecture-plan.md`: [`process-model.md`](process-model.md)

---

## Overview

Resource Explorer discovers, understands and catalogs information resources — Git
repositories, PostgreSQL databases and file systems — using [Egeria](https://egeria-project.org/)
as the catalog of record.

**Egeria-first, and Egeria is now a hard dependency for *access*.** Every survey result that can
be written to Egeria should be. Local storage is a cache and a queue, so the tool keeps working
while Egeria is briefly unreachable and reconciles afterwards.

**But Egeria is also the identity provider** (2026-09-04, `docs/runtime-architecture-plan.md`
§4): RE requires login, and the only way to sign in is an Egeria user id and password exchanged
for an Egeria bearer token. The older claim that "RAG-based querying needs no Egeria at all" is
retired — the querying itself still needs none, but reaching it requires a session, and a session
requires Egeria. `TRELLIS_ANONYMOUS_READ=true` is the one remaining way to run without one and is
a dev-box override, not a supported mode.

Two layers:

1. **Survey layer** — collects structure and metadata from resources, writes locally, and
   publishes to Egeria.
2. **Query layer** — answers natural-language questions from survey metadata, RAG over
   pgvector, and specialist agents.

---

## Storage

**The registry is PostgreSQL by default**, in a named `resource_explorer` schema inside the
`egeria_advisor` database shared with Egeria Advisor. SQLite remains a supported fallback via
`REGISTRY_DATABASE_URL` — used by tests and single-user setups. Both are driven through the
same `ProjectRegistry` API; nothing above it knows which is underneath.

*(The previous revision claimed "all persistent state lives in a single SQLite file". That
stopped being true when the shared Postgres instance arrived, and the claim survived because
nothing tested it.)*

Vector data lives in the same Postgres via pgvector, in per-resource collections named
`{slug}_{collection_type}`, created lazily on first insert.

### Table families

51 tables, which group into six families rather than needing individual description:

| Family | Examples | Shape |
|---|---|---|
| **Resource identity** | `projects`, `databases`, `file_systems`, `db_servers`, `project_aliases`, `project_groups` | One row per registered resource |
| **Collected facts** | `project_stats`, `project_commits`, `project_file_inventory`, `project_code_symbols`, `project_dependencies` | Refreshed in place or appended per run |
| **Analysis results** | `project_analysis_findings`, `project_analysis_metrics`, `project_file_type_counts` | Append-only, `surveyed_at`-stamped, latest-batch reads |
| **Human judgement** | `resource_context`, `resource_tags`, `resource_feedback`, `resource_curator_notes`, `repo_dispositions`, `investigations` | What a person decided, never inferred |
| **Machine attention** | `resource_schedules`, `notification_subscriptions`, `rfa_actions` | Recurring work and its delivery |
| **Egeria linkage** | `project_egeria_surveys`, `entity_egeria_project_context`, `egeria_linkage_status`, `survey_definition_cache` | What is published, and what Egeria said |

Two properties matter more than the list:

- **Analysis results are append-only.** A survey run does not overwrite its predecessor; it
  adds a `surveyed_at`-stamped batch. History is the point — trends, and "what changed since
  last time", both depend on it.
- **Collected facts and analysis results are different tables on purpose.** Most analyses do
  not write to `project_analysis_findings`; they read `project_stats` or the file inventory and
  report over it. Assuming the findings table is where results live is a mistake that has been
  made twice, most recently by the context compiler.

---

## Directory map

```
resource_explorer/
├── registry.py             # every table, every read/write. Postgres or SQLite.
├── config.py               # Pydantic settings
│
│   # process roles — see docs/process-model.md
├── worker.py               # the `worker` role: owns every background loop, runs the two startup
│                           #   one-shots, and is what `web --embed-worker` runs in a daemon thread
├── leader_election.py      # pg_try_advisory_lock per loop, so exactly one process runs each
├── concurrency.py          # THE one bounded thread pool per process for sync-pyegeria bridging
├── scheduler.py            # the 15-min loop: due schedules, subscriptions, RFA reconcile, outbox drain
├── bootstrap.py            # 10-min loop: heal Dr.Egeria definitions wiped by an Egeria reset
├── egeria_resync.py        # 10-min loop: clear stale Egeria pointers left by a store wipe
├── run_reconciler.py       # resolve activity rows and `runs` rows a dead process left claimed
├── run_queue.py            # the Postgres run queue: claim (SKIP LOCKED), heartbeat, execute, finish
├── a2a_role.py             # the `a2a` role: all seven agents on ONE port, path-routed, card per agent
├── a2a_auth.py             # bearer auth + THE identity ContextVar (`current_caller`) every door sets
├── agentstack_server.py    # the seven agent definitions themselves (a2a_role mounts them)
│
│   # identity — see "Who a call runs as" below
├── auth.py                 # thin adapter over trellis-auth: policy, secrets, token mint/exchange
├── egeria_identity.py      # per-request pyegeria clients, Ownership, ZoneMembership, the (configured-only) draft zone
├── cli/session.py          # `resource-explorer login`'s cached session (trellis_auth.session_file)
│
│   # the workflows — one module per unit of work, FastAPI-free
├── workflows/
│   ├── analysis.py         # one analysis run, and a "run all <stage>" batch, incl. auto-publish
│   ├── scouting.py         # the coarse scan, plus "has anything actually been measured here"
│   ├── discovery.py        # GitHub search, list-load, org expansion, disposition, saved sources
│   ├── survey_definition.py# execute an authored Survey Definition and record its outcome
│   └── curate.py           # materialize an accepted component/blueprint verdict into Egeria
│
├── facts.py                # FactLayer: reads what is known, never runs anything
├── step_outcome.py         # recovered / partial / no_signal / unverified / regression
│   surveyors/result_status.py   # measured / nothing_found / not_established / never_run / …
│
├── rag_system.py           # query orchestrator
├── query_processor.py      # intent classification + agent routing
├── collection_router.py    # which pgvector collections a query needs
├── context_compile.py      # question → ContextSpec → packed context + manifest
│
├── github/                 # API client, stats fetcher, org importer, source cache
├── ingestion/              # repo → chunks → pgvector; incremental re-index
├── agents/                 # BeeAI specialist agents (stats, code, doc, health, …)
├── surveyors/              # the survey layer — see below
├── web/                    # FastAPI app, 26 route modules, single-page UI (starts no loops,
│                           #   spawns no background threads — routes enqueue onto the run queue)
├── cli/                    # Typer CLI — including `web` and `worker`
└── observability/          # MLflow, Phoenix, metrics
```

**Process roles, one codebase.** `resource-explorer web` serves HTTP; `resource-explorer
worker` owns the background loops; `resource-explorer serve` is the `a2a` role. `web
--embed-worker` (the default, so `make dev` stays one command) runs the worker role in-process.
Which process actually runs a given loop is decided by a Postgres advisory lock, not by startup
order, so a second RE process against the same registry stands by rather than double-firing.
`make ps` lists every trellis process and container, `re-a2a` among them.
Full detail, including the advisory keys: [`process-model.md`](process-model.md).

**The A2A surface is one port and requires a token.** `resource-explorer serve --port 8090` hosts
all seven agents (orchestrator, stats, code, docs, health, compare, integration) on a single
service: `/agents/<name>` for each, `/` for the orchestrator as the default, an A2A agent card per
agent at its own well-known path, and `/.well-known/agents.json` as an index of all of them for a
Portal or external orchestrator to discover. Every agent call needs
`Authorization: Bearer <token>` — either a trellis app JWT or a raw Egeria bearer token validated
once against the view server and cached for the token's own lifetime. This replaced seven
unauthenticated servers on ports 8080-8086. See [`a2a.md`](a2a.md).

**Work is queued, not threaded.** A route that starts a survey writes a row to the `runs` table
and returns; a `worker` process claims it (`SELECT … FOR UPDATE SKIP LOCKED`), heartbeats every
30s while it runs, and writes the terminal state. The browser's contract is unchanged — it still
polls `GET /api/activity/{id}` and reads the result out of that entry's `detail`; the queue row
carries that activity id in `result_ref`. `GET /api/runs?state=queued` is the queue's own view,
and the way to tell "nothing is draining the queue" from "this survey is slow".

**`workflows/` is what makes the CLI a peer of the web tier.** Each module is a plain function
over explicit arguments returning a result dataclass, with no FastAPI anywhere
(`tests/test_workflows.py` pins that by parsing the imports). The route, the Typer command and
the queue handler are three callers of one function rather than three implementations — so
`resource-explorer analysis run <project>` and clicking Run do the same thing by construction.

Shared libraries live outside this package and are imported: `trellis-vectorstore`,
`trellis-context`, `trellis-artifact-tree`, `trellis-microflow`, `trellis-querycache`,
`trellis-auth`. See the [workspace architecture](../../../docs/trellis-architecture.md).

---

## The survey layer

### Three concepts, deliberately distinct

Conflating these has caused real bugs, so they are named precisely:

| Concept | What it is | Where it lives |
|---|---|---|
| **Survey Definition** | A named, ordered graph of steps — "run these, in this order" | Egeria, as a `GovernanceActionProcess`; mirrored locally in authored Dr.Egeria documents |
| **Step** (microflow) | One unit of work — "classify the languages" | `surveyors/sub_surveyors/`, 33 of them, keyed by a step name like `repo_health` |
| **Analysis** | A catalog entry describing a result a person can look at | `configdata/analysis_catalog.yaml`, tagged by intent |

An analysis usually maps to one step, occasionally to several, and four steps have no analysis
at all. They are not synonyms and the mapping is explicit
(`REPO_ANALYSIS_STEP_MAP`, `REPO_ANALYSIS_RESULTS_MAP`).

### Flow

Two paths, and confusing them is easy because only one of them is named after the thing it does.

**Repo steps** run through `SurveyOrchestrator`:

```
SurveyOrchestrator.run(slug, steps=None)
  → resolves steps (all, or a named subset)
  → acquires shared resources (zipball, git clone) via trellis-microflow
  → runs each step → Annotation objects
  → writes results to the step's own table
  → writes ONE activity_log entry
  → optionally publishes to Egeria
```

`steps=None` runs everything; a list runs exactly those. That parameter is what lets a single
analysis or a scheduled bundle share one execution path.

**A whole Survey Definition, on any resource type**, runs through
`surveyors/survey_definition_executor.py` instead — a generic dispatch loop over the step
graph, plus a `ResourceTypeAdapter` registered per resource type
(`repo_survey_definition_adapter.py`, `database/survey_definition_adapter.py`,
`filesystem/survey_definition_adapter.py`). That adapter layer is what makes Survey Definitions
executable uniformly across repos, databases and filesystems; `SurveyOrchestrator` is the repo
adapter's own machinery, not the general path.

### Resource acquisition

**Acquired once per commit, not once per run.** A zipball or treeless clone is cached on disk
by `github/source_cache.py`, keyed on `(repo, commit SHA)`, so a re-survey of unchanged code
pays nothing — and the key is what makes a stale hit impossible. Within a single run, steps
also share one acquisition rather than each taking its own; that part has been true since
2026-08-20 and is not what made surveys fast.

The 110.5s → 14.4s measured on `egeria_python_git` came from two unrelated fixes, and it is
worth knowing which, because the obvious explanation is the wrong one:

- **`--find-renames=100%`** in `arch_recovery/cochange.py` — about 92s of it. `git log
  --name-only`'s default *inexact* rename detection scores blob-content similarity, and the
  history root is a `--filter=blob:none` clone, so every comparison was a lazy fetch: 86
  upload-pack round trips inside one `git log`. Exact-rename detection compares blob OIDs
  already in the tree. 41s → 0.03s on a cold clone.
- **`SourceCache`** — acquisition 22.6s cold → 1.3s warm.

### Two coordinators, neither of which is RE

RE is not a workflow engine. Either **Egeria coordinates** and RE executes leaf steps as an
engine host, or **RE coordinates** and hands the graph to **Prefect**. Each step declares
`executes_at: resource-explorer | prefect | egeria`. With Prefect unreachable, steps fall back
to running in-process — safe, one connection attempt slower.

A step marked `executes_at: egeria` is **not skipped** while RE coordinates. The adapter's
`other_engine_handlers` lets RE actively trigger Egeria's own native survey engine for that one
step and carry on coordinating the rest of the graph locally — both the database and filesystem
adapters do this. That mechanism is also the extension point a fourth `executes_at` value,
`egeria-adaptive`, is registered under (2026-09-20 — folded in per
`docs/design-notes/EXECUTION-MODES-HYBRID-CLARIFICATION.md`, superseding the original plan's
`egeria-hybrid` name, which collided with this codebase's existing, different sense of "hybrid":
one Survey Definition whose *steps* run on a mix of engines, already covered by the paragraph
above). `egeria-adaptive`'s handler (`_run_egeria_adaptive` in both the database and filesystem
`survey_definition_adapter.py`) is a strategy selector, not a step runner: it reuses an existing
Egeria survey when one exists (cache-or-run, database only — there is no equivalent cache lookup
for filesystems yet), otherwise runs the local scan and publishes it into Egeria, falling through
to triggering Egeria's own native survey (with catalog-on-demand, unlike the plain `egeria`
handler above) when there is no usable local result, and degrading to a local-only survey on any
failure. Unlike `egeria`, its `source` (`egeria` / `egeria-custom` / `custom` / `error`) is a
first-class field on the step's own `steps_report` entry, not only inside the handler's own
return value. The web/CLI "survey this database/filesystem" routes now build a one-step synthetic
Survey Definition tagged `executes_at: egeria-adaptive` and run it through
`SurveyDefinitionExecutor.run_synthetic_step` instead of calling `HybridDatabaseSurveyor`/
`run_hybrid_filesystem_survey` directly — those two still exist and still do the actual work (the
new handlers delegate to them; see their own docstrings for why they were not rewritten into
shims calling back into the new handler — their characterization tests pin private instance state
a rewrite would sever), but are no longer reachable from a web/CLI request except through this
path. This closes only the backend-routing half of `Backlog.md`'s "Unify survey launching" — the
legacy per-resource-type UI buttons/modals it also names are untouched here (out of scope; a
concurrent UI task on a separate branch covers `executes_at` visibility).

### Publishing

`EgeriaPublisher` finds or creates the repository's own `Asset` by exact `qualifiedName`
(`GitHubRepository::<github_url>`, linked to a singleton `SourceControlLibrary` for GitHub
itself via `CapabilityAssetUse` — corrected 2026-09-14, see the module's own docstring for
the type error this replaced), then attaches a `SurveyReport` and one annotation per result.
The exact-match search is load-bearing: a prefix match once made two sibling repos share an
asset, and each other's findings.

---

## The query layer

```
Query (web / CLI / A2A)
  → QueryCache                        hit → return
  → QueryProcessor.classify()
      ├── survey_meta → answered from the registry, no vector search
      ├── statistical / comparison / examples / code_search / conceptual / health → specialist agent
      └── general → RAG (CollectionRouter → pgvector → LLM)
  → optionally: compiled evidence (see below)
  → LLM generation
  → async: tracing, metrics, cache store
```

`survey_meta` is evaluated first and never touches pgvector — "when was this last surveyed"
is a registry read, and routing it through retrieval would be slower and worse.

### Compiled context

A newer path, and the one that changes answer quality most. `context_compile.py` turns a
question into a `ContextSpec`: the question catalog already maps Purpose and Perspective to
questions and to the analyses that answer them, so those analyses become **sections**, stored
results become **candidates**, and `trellis-context` packs what fits a character budget.

What it returns alongside the text matters as much as the text:

- **manifest** — what was packed and at which rung, what was dropped for budget, and what is
  **missing**
- **derivation** — why each section is there: this Purpose ranked that question, which
  dispatches this analysis

Sections resolve through the **fact layer**, not raw results, so a gap distinguishes *never
ran* from *ran and found nothing*. Nothing is executed to fill a gap — a compile never blocks
on a survey.

### Model tiers and the RAG context budget

See [`docs/runtime-architecture-plan.md`](../../../docs/runtime-architecture-plan.md) §5 (repo root) for the full design and the measurements behind it
(prompt tokens, not model size, is the lever for time-to-first-token). `EXPLORER_MODEL_TIER`
(`dev` default / `demo-gpu` / `demo-cpu`) resolves, per machine profile, the Ollama model,
Ollama's `num_ctx` ceiling, and the RAG retrieval context budget together —
`resource_explorer/config.py`'s `TIER_PRESETS`, mirroring Egeria Advisor's
`advisor/config.py` (same tier names, same `num_ctx`/budget values) so the two apps converge
rather than drift. `dev` keeps today's unbounded behaviour (`num_ctx=32768`, no RAG budget); the
demo tiers cap `num_ctx` at 8192 and the RAG context budget at ~2000 tokens. An explicit
`LLM__OLLAMA__MODEL` still wins over the tier's model. `num_ctx` is threaded through every
Ollama call RE makes — `llm_client.py`'s `OllamaBackend` (the raw `ollama` client's `options`)
and `agents/base.py`'s BeeAI `RequirementAgent` path (an `OllamaChatModel` built with
`settings={"num_ctx": ...}`, since BeeAI talks to Ollama over its OpenAI-compatible endpoint,
not the native one). The RAG context budget is applied in
`prompt_templates.py::build_context()`, used everywhere retrieved chunks are joined into a
prompt (`rag_system.py`, `agents/doc_agent.py`, `agents/code_agent.py`) — it keeps the
highest-ranked chunks and stops adding once the (approximate, 4 chars/token) estimate would
exceed the budget.

---

## Who a call runs as

`docs/runtime-architecture-plan.md` §4, landed 2026-09-04. Three doors, one identity, one place
that decides what an Egeria write is attributed to.

**Login is required.** `trellis_auth.LoginRequiredMiddleware`, installed in `web/app.py` under the
policy `resource_explorer/auth.py` resolves (`TRELLIS_*` then `EXPLORER_*`, the app-specific one
winning). Public: `/health`, `/health/ready`, `/static/`, the SPA shell, the five `/api/auth/*`
routes, and `/.well-known/` — the A2A agent cards must be readable before a client holds the
credential they describe. Middleware order, measured rather than assumed, is
**CORS → login gate → identity → routes**: a cross-origin 401 keeps its CORS headers (without
them the browser reports an opaque network error and the login form never appears), and a
rejected request never sets an identity.

**Two ways in, one token.** The login form exchanges an Egeria user id and password for a bearer
token exactly once and forgets the password; the Portal hands over the token it already holds.
Either way RE mints its own HS256 session JWT carrying that bearer token — never a password — and
caps its expiry at the Egeria token's own. Sessions therefore last as long as Egeria's tokens do
(one hour by default, and every token dies when the platform restarts); refresh is a re-login.

**One identity mechanism.** `a2a_auth.current_caller` is a ContextVar, and all three doors set the
same one:

| Door | Sets it in |
|---|---|
| Browser / HTTP | `web/app.py::_identity_middleware` |
| A2A agent traffic | `a2a_auth.A2AAuthMiddleware` |
| CLI | `cli/session.activate()`, from the cached login |

Everything downstream asks `a2a_auth.caller()`. Nothing has to know which door a call came
through, and there is deliberately no second, web-shaped identity type.

**Per-request Egeria clients.** `egeria_identity.apply_identity` is the single place a pyegeria
client is authenticated. A signed-in person's client reuses *their* bearer token
(`set_bearer_token`), so Egeria's own provenance records the person rather than `erinoverview`.
The **worker role's own loops** — bootstrap heal, resync, outbox drain — keep the service account,
which is the correct attribution for them and the only legitimate use of it.

**Ownership is curation by default.** Everything RE publishes gets Egeria's `Ownership`
classification (`0445`) with `owner` = the requesting user and `ownerTypeName = "UserIdentity"`.
The owner may accept, reject, promote and delete with no separate grant; a `curator`/`admin` role
claim curates across resources it does not own; everyone else gets 403. Enforced in
`workflows/curate.py`, not in the routes, so the CLI and A2A inherit it.

**Zones: no default draft zone (owner ruling 2026-10-08).** DRAFT is a content status, not a zone.
Published assets (survey reports, the library element, materialized components and blueprints) get
**no RE-imposed zone**. The 2026-09-04 "one draft zone per app, `resource-explorer-draft`" default was
retired; a draft zone exists only when a deployment sets `EXPLORER_DRAFT_ZONE`, and only then does the
worker create it (once at startup, leader-elected; `worker.py::_ensure_draft_zone`). Private-investigation
work is zoned `[resource-explorer-private, <owner>]`. Curate-accept promotes into the deployment's
CONFIGURED publish zones (`EXPLORER_PUBLISH_ZONES`); **configured-only (owner, 2026-10-07): there is no
default publish zone.** With none configured, accept writes no zone at all (and clears RE's draft
`ZoneMembership` if one was configured), and the verdict row reads "accepted · zones left to Egeria ·
everyone visible" only after a read-back found none. (A database catalogue commit writes the publish
zone only when `EXPLORER_PUBLISH_ZONES` is set, last; unverified for a second commit.)

*History.* "Everything, no exceptions" was a correction, made against the live platform on 2026-09-04. A
materialized component was at first written straight into the publish zones, on the reasoning that
it *is* the accepted outcome — and the accept path's promotion then failed with
`OMAG-SERVER-SECURITY-403-005: not authorized to change the zone membership ... from
[egeria-runtime] to [egeria-runtime]`. Egeria's security connector treats a no-op zone change as an
error, so a second code path with its own rule produced a permissions failure on a correct
operation. One rule removes the special case and makes the transition observable:
`promote_to_publish_zones` reports `from_zones` alongside `zones`, so "promoted" can be told from
"was already there".

**Trellis-side scoping.** `resource_working_set`, `entity_egeria_project_context` and
`conversation_history` carry `user_id`, resolved *in the registry layer* rather than by each
route — a route that has to remember to scope is a route that will not. `''` is the shared/legacy
bucket, and a read takes the caller's own row falling back to it, so nothing written before this
existed disappeared.

**Known gaps, named rather than hidden.** A queued run carries `requested_by` but no token — a
one-hour credential does not survive a queue — so the worker publishes as the service account with
`Ownership` set to the requester (`run_queue._run_as_requester`). And curator rights read the JWT
`role` claim; the plan's destination is Egeria `PersonRoleAppointment`s.

---

## Cross-cutting: absence is a first-class result

The single most common bug class in this codebase is **a result that looks like an answer and
is not**. Three vocabularies exist to prevent it, and they are not interchangeable:

- `surveyors/result_status.py` — `measured` / `nothing_found` / `not_established` /
  `never_run`, plus `skipped_by_design` and `misgrouped` for results that are absent on
  purpose rather than by failure
- `step_outcome.py` — `recovered` / `partial` / `no_signal` / `unverified` / `regression`,
  where `CONCLUSIVE` deliberately excludes `unverified`
- The compile manifest's **gaps**, which name analyses with no stored result

A zero from an analysis that never ran and a zero from one that ran and found nothing are
different facts. Surfaces that flatten them produce confident wrong answers, which is worse
than an error because nothing looks broken.

---

## The eight intents

`scouting` · `discovery` · `assessment` · `analysis` · `enrichment` · `understanding` ·
`curate` · `automate`

These are canonical strings, used in the activity log, UI filters, analysis catalog tags and
query routing. The axis that separates the early stages is **does this collect, or does it
reason over what is already collected** — not evaluative-versus-structural. See `CLAUDE.md`
rule 17, which carries the full reasoning and its exceptions.

---

## The activity log

Every scout, survey, catalog, publish and RFA operation writes an entry. It is the audit
trail and the UI's history view, and it is also load-bearing for correctness: the
"was this ever published?" check that distinguishes *lost an asset* from *never catalogued*
reads it rather than guessing.

---

## Key design rules

The full numbered list lives in [`../CLAUDE.md`](../CLAUDE.md) and is not duplicated here —
one copy, deliberately. The ones with the most architectural reach:

- Classify intent before touching the vector store (rule 1)
- Activity log entries for **all** operations, not just Egeria publishes (rule 16)
- The eight intent labels are canonical (rule 17)
- `HybridDatabaseSurveyor` must run the local scan immediately after triggering Egeria's
  async native survey (rule 15)

---

## Runtime neighbours

Three hand-authored component outlines (written 2026-09-02) used to sit beside this document as
`egeria.md`, `trellis.md` and `workspaces.md`. They are folded in here, trimmed to what is still
true. They are descriptive notes, **not** the scoring fixtures: the pre-registered ground-truth
partitions used to score architecture recovery are separate, and richer, in
`../tests/fixtures/architecture-ground-truth/` (which has its own `trellis.md`, `egeria.md` and
`egeria-workspaces.md`; those are different files).

### Egeria

The catalog of record (see Overview). An outline of its runtime and deployment components, from
the maintainer's notes; Egeria is an external project and this list was not re-derived from its
code.

- **egeria-platform**: the runtime that hosts the OMAG servers.
- **egeria-servers**: metadata servers, integration daemons, engine hosts and view servers.
  RE talks to the view server (through pyegeria), which is the single channel.
- **connectors**: many, each a component.
- **deployment components**: docker, build, swagger and similar, startup configuration.
- **conformance test suite**.
- **samples**: http clients, sample clients, reports, utilities.
- **content packs**: pre-built loadable archives of metadata that define useful content.

### Trellis

The workspace this package lives in (`packages/`: `resource-explorer`, `egeria-advisor` and the
shared `trellis-*` libraries; the library list is under the Directory map). Resource Explorer's
runtime components, checked against the code on 2026-10-15:

| Component | Where | What it is |
|---|---|---|
| web backend | `resource_explorer/web/` | FastAPI services behind the front-end |
| web front-end | `frontend-build/` (build inputs) | JavaScript single-page applications |
| CLI | `resource_explorer/cli/` | Typer terminal interface, including the `web`, `worker` and `serve` roles |
| TUI | `resource_explorer/tui/` | Textual terminal interface |
| RAG ingestion | `resource_explorer/ingestion/` | repo to chunks to pgvector |
| agents | `resource_explorer/agents/` | specialist agents (stats, code, doc, health, compare, dependency, examples, conversation, integration, survey-meta) |
| observability | `resource_explorer/observability/` | MLflow tracking, optional Phoenix client, metrics, feedback collection |
| Prefect flows | `resource_explorer/prefect/flows.py` | Prefect flow and task definitions used when a step declares `executes_at: prefect` (see "Two coordinators") |
| surveyors | `resource_explorer/surveyors/` | the survey steps (the survey layer above) |
| utility scripts | `scripts/` (package root) | operational scripts |

Two corrections to the old outline: its `utility_scripts` directory does not exist (the scripts
are in `scripts/`), and its file globs were relative to `resource-explorer/` rather than to the
repository root.

### Egeria Workspaces

A separate repository (`egeria-workspaces`) that provides the Docker environments Egeria and
RE run in. Container names below were checked against its compose files on 2026-10-15; the
descriptions are the maintainer's.

- **QuickStart** (preconfigured sample environment): `quickstart-egeria-main` (one Java runtime
  hosting several Egeria servers, not standalone processes), `quickstart-egeria-watchdog`,
  `quickstart-jupyter-work-full`, `quickstart-web-server` (Apache), `quickstart-pyegeria-web`
  (a FastAPI module serving both MCP commands and the Egeria Portal, whose single-page apps have
  Python backends; MCP executes reports and Dr.Egeria commands), `quickstart-my-profile` (a
  Textual app for managing the environment) and `obsidian-quickstart` (an Obsidian client over
  the Coco Workbooks that can issue Dr.Egeria MCP calls).
- **Freshstart** (unconfigured, ready for customization): `freshstart-egeria-main`,
  `freshstart-jupyter-work-full`, `freshstart-web-server`, `freshstart-pyegeria-web`. Same shapes
  as QuickStart with nothing pre-configured.
- **Shared infrastructure**: `egeria-shared-kafka`, `egeria-shared-kroki` and
  `egeria-shared-kroki-mermaid` (mermaid rendering helpers), `egeria-shared-openlineage-proxy-backend`
  (common OpenLineage proxy) and `egeria-shared-postgres` (pgvector-enabled Postgres serving all
  runtimes; host port 5442 per [`../../../docs/deploying-trellis-with-quickstart.md`](../../../docs/deploying-trellis-with-quickstart.md)).
- **Optional runtimes** (`compose-configs/optional-associated-runtimes/`): Airflow with Marquez
  (OpenLineage demos), Dagster, Prefect, Apache Atlas, MLflow with MinIO, Milvus, DuckDB
  federation, Superset (presents survey results), Unity Catalog (two deployments, Postgres-backed
  and native), Ollama (local models; no dedicated README yet), Delta Lake with Spark, MinIO and a
  Hive metastore, and a `trellis` directory for the Trellis containers (`trellis-re-web`,
  `trellis-re-worker`, `trellis-ea-web`).
- **Directories**: `coco-workbooks` (samples and tutorials in Jupyter and Dr.Egeria),
  `exchange-freshstart` and `exchange-quickstart` (shared folders), `obsidian-plugins` (shared
  source for the Dr.Egeria Obsidian plug-ins), `portal-docs` (environment and Portal app docs,
  launched from the Portal), `runtime-volumes` (runtime state kept outside the containers),
  `templates` (Dr.Egeria template files) and `work` (unmanaged local user work). The old outline
  says `workbooks` is to be deprecated; the directory still exists.
