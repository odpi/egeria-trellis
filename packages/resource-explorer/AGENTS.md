# CLAUDE.md — Resource Explorer

This file provides guidance to Claude Code when working in this repository.

## What this project is

**Resource Explorer** is a tool for discovering, understanding, and cataloging information resources — Git repositories, PostgreSQL databases, file systems, and other data assets — using Egeria as the central metadata catalog and governance platform.

It is the successor to [Project Explorer](https://github.com/LF-AI/project-explorer), extended with:
- Egeria-first architecture: surveys write to Egeria as the catalog of record
- Unified activity log across all resource types
- Intent-based UI (Scouting / Discovery / Assessment / Analysis / Enrichment / Understanding / Curate)
- Persona-aware analysis menu (DBA, data scientist, steward, security)
- RequestForAction lifecycle management for human-provided context
- Temporal analysis: track how resources change over time

**Target users:** Data engineers, data stewards, DBAs, AI engineers, and security practitioners who need to understand and catalog information resources. Egeria is required — for the core survey/catalog workflow, and since 2026-09-04 to sign in at all, since it is the identity provider (`docs/runtime-architecture-plan.md` §4). RAG-based querying still needs no Egeria of its own, but reaching it needs a session and a session needs Egeria.

**Design reference:** `docs/survey-model.md` — read this before making architectural changes.

## Package name

The Python package is `resource_explorer` (snake_case). The CLI entry point is `resource-explorer`.

All imports use `from resource_explorer.X import Y`. The package was ported from `explorer` in Project Explorer; there should be no remaining `from explorer.` imports anywhere.

## Tech Stack

| Component | Package | Notes |
|---|---|---|
| Agent framework | `beeai-framework[rag]` | `RequirementAgent` with `@tool`-decorated functions |
| Agent runtime | `agentstack-sdk` | A2A server |
| Vector store | `pgvector` (PostgreSQL) | Multi-tenant via one table per collection, `resource_explorer` schema — shared with Egeria Advisor's `egeria_advisor` database |
| Registry | PostgreSQL (default) / SQLite (fallback) | Same shared Postgres database, `resource_explorer` schema; `REGISTRY_DATABASE_URL` overrides to SQLite |
| Document parsing | `docling` | PDF, web, DOCX, Markdown |
| Embeddings | `sentence-transformers` | `all-MiniLM-L6-v2`, 384-dim, MPS on Apple Silicon |
| LLM default | `ollama` | Metal GPU on Apple Silicon; pluggable |
| LLM tracing | `openinference-instrumentation-beeai` | → Arize Phoenix at localhost:6006 |
| Experiment tracking | `mlflow` | Background thread, non-blocking |
| CLI | `typer` + `rich` | |
| Web UI | `fastapi` + `uvicorn` + Tailwind + Plotly.js | Single-page HTML frontend |
| TUI | `textual` | Full-screen terminal UI |
| Egeria | `pyegeria` | Survey, catalog, RFA management |
| Database surveying | `psycopg2-binary` | PostgreSQL introspection |

## Setup

A worktree has no .env; RE defaults its registry to the shared Postgres. Set REGISTRY_DATABASE_URL to a temp SQLite file before any command or test that opens the registry; the guard will fail you otherwise.

Live Egeria tests are opt-in: a default run never contacts Egeria. Reads need `--live-egeria-reads` (or `RE_LIVE_EGERIA_READS=1`) after a peer round; writes also need `--live-egeria-writes` AND a non-empty `RE_LIVE_EGERIA_WRITES_CLEARED=<who>/<UTC time>` (the flag alone skips the tier). Another session's done is a statement about itself, never a clearance from the others; the peer round is a question to every live peer, and the answer set is what goes in the variable.

```bash
uv sync
uv sync --extra dev --extra phoenix

cp .env.example .env
# Edit .env: set GITHUB_TOKEN, LLM_BACKEND, Egeria connection, etc.
# pgvector/registry defaults already point at the shared egeria_advisor
# Postgres instance — no .env entries needed in a Trellis checkout.
```

External services:
- **pgvector** — shared Postgres instance (`egeria-shared-postgres`, port 5442) in a Trellis checkout, normally already running (managed by `egeria-workspaces-fs`'s `compose-configs/shared-infra/shared-infra.yaml`); a standalone `pgvector/pgvector:pg17` container for a from-scratch environment
- **Ollama** at `localhost:11434` — `ollama pull llama3.1:8b`
- **Egeria** at `EGERIA_PLATFORM_URL` — **required**, for survey/catalog *and* for login (it is the identity provider). `TRELLIS_ANONYMOUS_READ=true` is a dev-box override, not a supported mode
- **Arize Phoenix** (optional) — `python -m phoenix.server.main` → `localhost:6006`
- **MLflow** (optional) — `mlflow server --port 5025` → `localhost:5025`
- **Kroki** (optional) at `KROKI_URL`, default `http://localhost:6002` — renders mermaid diagrams server-side (`POST /api/diagrams/mermaid`, see `web/routes/diagrams.py`); only used for the database ER-diagram view, nothing else depends on it. Same shared `egeria-shared-kroki` container as the rest of an egeria-v6 checkout (`egeria-workspaces-fs`'s `compose-configs/shared-infra/shared-infra.yaml`), reached via its published host port `6002` (not Kroki's own default `8000` — that collides with common local dev servers like `mkdocs serve`) — **not** the container-name URL (`http://egeria-shared-kroki:8000`) other, containerized Egeria services use, since resource-explorer runs as a bare host process and isn't on the `egeria_network` docker network. If Egeria/Kroki run on a different host than resource-explorer (a remote/production deployment, not a single-box dev checkout), point `KROKI_URL` at that host's published Kroki port instead — Kroki has no built-in auth, so a remote deployment should sit behind a firewall/VPN/reverse-proxy, not be exposed directly to the internet. See `docs/kroki-diagram-rendering.md` for the full picture (network topology, failure behavior, troubleshooting).
- **Prefect** (optional, **on by default as of 2026-09-19** — project owner decision, `PLAN-PREFECT-OR-ALTERNATIVE.md` §5 phase 3, once a live end-to-end run through Prefect verified cleanly; was off 2026-09-04 – 2026-09-19 after an earlier default-on period (2026-08-26 – 2026-09-04) caused 13 orphaned `prefect.server.api.server:create_app` subprocess servers to leak — root-caused and fixed at `prefect_adapter.py`'s import-time guard before the default was flipped back on, see `PrefectConfig.enabled`'s docstring in `config.py`) at `PREFECT_API_URL`/`PREFECT_UI_URL`, default `http://localhost:4200` — executes survey steps declaring `executes_at: prefect` (and, if `PREFECT_ROUTE_LOCAL_STEPS=true`, plain `executes_at: resource-explorer` steps too — this stays **off** by default, its per-step overhead is real but still unmeasured), giving those steps real flow-run state, per-task logs, and cancellation — none of which the plain local `threading.Thread` execution path has. **Applies only to locally-run survey steps**; `executes_at: egeria` steps are coordinated by Egeria itself and this gives no additional visibility into those (a separate, deferred problem — see `docs/Backlog.md`). With no Prefect server reachable at `PREFECT_API_URL`, `run_prefect_step` degrades to running the step locally — a checkout with no Prefect server keeps working. **Topology as of 2026-09-19**: the server runs in the existing `egeria-optional-prefect-server` container (`egeria-workspaces`' `optional-associated-runtimes/prefect`, `restart: always`, survives reboots) on a dedicated `resource-explorer-pool` work pool (kept separate from that repo's own `egeria-pool`); the **worker runs as a bare host process** (`uv run --package resource-explorer`, so `resource_explorer` is importable without building a Trellis-owned image) and is the one piece that does NOT survive a reboot — nothing auto-starts it yet (no launchd/systemd unit). Bring it back up with `make prefect-up` (idempotent — detects the container server is already up and only (re)creates the pool/deployment/worker):
  ```bash
  make prefect-up      # server + work pool + deployment + worker, whatever's missing
  make prefect-down    # stops exactly what prefect-up started
  ```
  `PREFECT_ENABLED=true` with no server running still runs steps correctly via the local fallback, but do not rely on that as a reason to leave a server down: with no server, Prefect's own client (not RE's fallback) can start an ephemeral in-process subprocess server that nothing shuts down — see the guard in `surveyors/prefect_adapter.py` and `resource_explorer/__init__.py`, which force `PREFECT_SERVER_EPHEMERAL_ENABLED=false` regardless. Flow-run status is visible in Prefect's own UI at `PREFECT_UI_URL`, and via the Admin "⚡ Prefect" panel in RE's own UI (`GET /api/prefect/status`, `/flow-runs`; `POST /api/prefect/flow-runs/{id}/cancel`).

## Eight User Intents

These are the canonical intent labels used throughout the codebase for routing, filtering, and analysis tagging. They appear in the UI's `#intent-nav` primary navigation, the activity log schema, the analysis catalog (`configdata/analysis_catalog.yaml`), and the query router.

Superseded the original four (Scouting/Assessment/Discovery/Enrichment) in the intent-based web UI rewrite — Analysis, Understanding, and Curate were added to give structural/quantitative work, data visualization, and discoverability/reuse-readiness work their own homes instead of being folded into Assessment or a sidebar. Automate (8th, 2026-08-13) was added for sustained *machine* attention — recurring, subscription-driven watching for change — parallel to Curate's sustained *human* attention; see `docs/discovery-automate-project-context-plan.md` Part 4.

| Intent | What the user is doing | Speed | Output |
|--------|----------------------|-------|--------|
| **Scouting** | Broad inventory across many resources | Fast | Summary view |
| **Discovery** | Find resources by what surveys revealed, launch Egeria Survey Definitions | Fast | Search results / survey launch |
| **Assessment** | Scored evaluation of a specific resource against criteria | Slow / async | Detailed annotations |
| **Analysis** | Structural/quantitative analysis (not scored) — dependencies, profiling, API extraction | Fast–minutes | Structured data |
| **Enrichment** | Provide human context (Context form) — facts about the resource: environment, ownership, sensitivity | Human-paced | Egeria asset properties |
| **Understanding** | Visualize trends over time — charts (stars, commits, schema, etc.) | Fast | Charts |
| **Curate** | Make a resource easier to find and more trustworthy to reuse — search tags, resource-level feedback, curator notes | Human-paced | `resource_tags`/`resource_feedback`/`resource_curator_notes` |
| **Automate** | Subscribe to an analysis; get notified (via RFA) when it changes on a future scheduled run | Recurring/async | `notification_subscriptions` + RFA |

**Enrichment vs. Curate** — easy to conflate, deliberately distinct: Enrichment records *facts about* the resource (a one-time/periodic form); Curate is *ongoing curatorial work* to make the resource discoverable and reusable (tags, feedback, running commentary). Digital-product evaluation, sample-dataset creation, and a dedicated quality-remediation workflow were named as Curate capabilities but are NOT built — each needs its own design pass; see `docs/feedback-and-curation.md (§8)`.

**Automate is local-first by explicit decision (2026-08-13).** Egeria's own Notification Manager (`NotificationType` + `Link Monitored Resource` + `Link Notification Subscriber`) is the eventual catalog of record, but "Create Notification Type" has no dedicated pyegeria method — it wraps the same generic, untested-for-this-type `create_governance_definition()` body construction rule 12 warns against. `resource_explorer/notification_detector.py` (change detection) and `scheduler.py`'s `_check_subscriptions()` (delivery via RFA) are real and local; `notification_subscriptions.egeria_notification_type_guid`/`_qualified_name` stay empty until `docs/automate-notification-manager-pyegeria-spec.md`'s proposed convenience API exists.

**System/catalog configuration is not an intent.** Annotation Types registry, resource Groups, and the Schedules overview are reachable from the header's **⚙ Admin** button (same pattern as 📋 Activity — decoupled from `#intent-nav`/`currentNavIntent`), not from one of the eight intents — they configure how the system behaves, not something a user does to curate a specific resource.

**Scheduling vs. monitoring schedules are two different surfaces on purpose.** Setting/changing a cadence for a specific analysis is per-resource and lives as a "⏱ Schedule" action directly on each analysis card in Assessment/Analysis/Discovery. The global, read-mostly Schedules overview (`GET /api/schedules/`) — what's scheduled, whether the last run succeeded, drill into errors, remove stale schedules — moved from Admin into **Automate's own "⏱ Schedules" sub-tab** (2026-08-13, alongside its "🔔 Subscriptions" sub-tab) rather than staying a separate Admin page, since a subscription's only real prerequisite is an active schedule for the same `analysis_id` — the two views living together is the point. It is still not a duplicate editor; both it and the per-card action hit the same `schedules.py`/`resource_schedules` backend. The scheduler (`scheduler.py`) writes a real `ActivityEntry` for every run it executes, success or failure, and records the outcome on the schedule row itself (`last_run_status`/`last_run_activity_id`) — this was a real gap before (only logged to Python's own logger, invisible from the UI, violating rule 16 below), not a deliberate omission. Automate's subscriptions ride on top of this same scheduler — a subscription with no active schedule for its `analysis_id` never fires, since detection only runs off scheduled completions (see rule 17).

RFA (RequestForAction) is **not** one of the eight intents either — it's a persistent drawer (`#rfa-drawer`, reachable from `#intent-nav` alongside Chat) with local-only defer/reassign/complete response actions, independent of whichever intent tab is active. See `resource_explorer/registry.py`'s `rfa_actions` table docstring for why this is a stepping stone toward real Egeria ToDo actions, not that integration itself, and `docs/egeria-integration.md (§11)` for the confirmed (not assumed) design of what that integration would take. Automate's own notifications are delivered as RFAs (a new `rfa_operation`-less `log_rfa()` call from `scheduler.py`), reusing this same drawer rather than inventing a separate notification UI.

## Architecture

**Single source: [`docs/Architecture.md`](docs/Architecture.md).** Read it before making
architectural changes.

It used to be duplicated here, and on 2026-08-30 both copies were found stale in the same two
places — both called the registry SQLite when it has been PostgreSQL by default for weeks, and
both listed seven intents after Automate made eight. Two copies did not give two chances to be
right; they gave one bug two homes. This section is now a pointer so there is one place to fix.

What that document covers: the survey and query layers, storage and the six table families,
the Survey Definition / step / analysis distinction, the two coordinators (Egeria or Prefect —
never RE itself), compiled context, and why absence is a first-class result.

For how this package sits in the workspace — the two apps, the six shared libraries, and the
one Postgres they share — see [`docs/trellis-architecture.md`](../../docs/trellis-architecture.md).

The rules for *working on* this code stay here: the tech stack above, the eight intents below,
and the numbered design rules.

## What's New vs. Project Explorer

| Area | Project Explorer | Resource Explorer |
|------|-----------------|-------------------|
| Activity log | In-memory, lost on reload | Persistent SQLite `activity_log` table |
| Survey source display | Not differentiated | ☁ Egeria / 🏠 Local / ⏳ Pending per value |
| UI shell | Project-centric sidebar | Resource-type sidebar + intent tab strip |
| Analysis menu | Fixed survey actions | Unified menu (Egeria + local) with intent/persona filters |
| RFA management | Created but not tracked | Full lifecycle: create → assign → respond → close |
| Temporal tracking | Single snapshot | History of runs; "vs. last run" diffs |
| Package name | `explorer` | `resource_explorer` |
| CLI command | `project-explorer` | `resource-explorer` |

## Key Design Rules (inherited from Project Explorer + new)

1. Classify intent before touching the vector store — statistical queries never hit pgvector
2. Min retrieval score = 0.30 — below this, say "I don't have enough information"
3. Query cache is the highest-ROI latency win — implement before optimizing retrieval
4. Observability (MLflow, Phoenix) runs in background threads — never block the response
5. Incremental indexing for repos — commit-diff based
6. Chunk size is content-specific — code ≠ prose ≠ examples
7. Use single-quoted YAML strings for regex patterns containing backslashes
8. A2A `Server` supports exactly one agent per instance
9. *(Historical — Milvus backend removed, migrated to pgvector; kept for context, not renumbered.)* Milvus `VARCHAR` `max_length` was a UTF-8 byte limit — truncated via `encoded[:max_bytes].decode('utf-8', errors='ignore')`. pgvector's `TEXT`/`JSONB` columns have no equivalent length constraint, so this truncation step no longer applies.
10. BeeAI `FunctionTool` objects have no `.func` attribute — use `_raw()` helpers for fallback calls
11. `AssetMaker` constructor: `(view_server, platform_url, user_id, user_password)` — view_server first
12. `EgeriaPublisher` annotation class names: `RequestForActionProperties` (no "Annotation" suffix), `QualityAnnotationProperties` (not "QualityScore")
13. `pg_description` requires schema-qualified `(schema.table)::regclass` form for `obj_description()` and `col_description()`
14. `server_connection()` in `connection.py` connects to the `postgres` system DB for listing databases
15. The `executes_at: egeria-adaptive` handlers (`_run_egeria_adaptive` in both `surveyors/database/survey_definition_adapter.py` and `surveyors/filesystem/survey_definition_adapter.py`) must run the local scan immediately after triggering Egeria's native survey — Egeria surveys are async and produce no immediate schema data. This constraint originally lived on `HybridDatabaseSurveyor` (`surveyors/database/hybrid_database_surveyor.py`) directly; as of 2026-09-20 (`docs/design-notes/EXECUTION-MODES-HYBRID-CLARIFICATION.md`) that class's strategy logic is reachable via `executes_at: egeria-adaptive` routing, which is now the web/CLI survey path — `HybridDatabaseSurveyor`/`run_hybrid_filesystem_survey` still exist and the new handlers delegate to them, but the rule now belongs to the handlers as the thing callers actually reach.
16. Activity log entries must be written for ALL operations — scouting, survey, catalog, publish, RFA — not just for Egeria publishes
17. The eight intent labels are canonical (2026-08-13, was seven): `scouting`, `discovery`, `assessment`, `analysis`, `enrichment`, `understanding`, `curate`, `automate` — use these exact strings in the activity log schema, UI filters, and analysis catalog tags. `automate` intentionally has zero entries in the analysis catalog (served by `automate.py`'s own `notification_subscriptions` table, not the catalog) — that's by design, not a gap. **`enrichment` used to be in that "zero entries" list too and no longer is (2026-09-29, ENRICHMENT-E1-CONTEXT-TAB):** Enrichment now holds the human-input-prerequisite analyses — `preliminary_fit` (retagged from `discovery`, `requires_input: lens`) plus three stub registrations (`doc_source_ingestion`, `doc_evidence_check`, `semantic_suggestions`, none implemented yet) — the project owner's amendment to `REPLY-DESIGNER-ENRICHMENT-STAGE-IA.md` §0.2: an analysis whose prerequisite is a human input, not a survey read, runs at this stage. `context.py` still serves Enrichment's Context tab (judgements/observations/human questions); the catalog now ALSO carries this one analysis class for it. **`discovery` used to be in that list and no longer is (2026-08-20.)** Discovery is not only "where you launch a survey": it is the tier that *derives* early-headlights signals from data Scouting has already collected, with zero new fetch, to decide whether the expensive tiers are worth paying for. `license_classification`, `maturity` and `repo_conventions` were retagged from `assessment` to `discovery` on that basis — all three declare `requires_resources={}` and read `project_stats`/the file inventory rather than fetching anything. The distinguishing axis between stages is **does this collect, or does it reason over what is already collected** — not evaluative-vs-structural, and not where the run was started from. **Zero-fetch is the strong default, not a law** (2026-08-22): it is a proxy for *cheap enough to gate the expensive tiers*, and where an analysis is cheap by measurement but does need a fetch, the measurement wins. `architecture_recovery` was tried as that exception on the strength of `docs/architecture-recovery-phase1-findings.md` §3's "~5s per repo" — and profiling the actual route (2026-08-30, `docs/Backlog.md` "costs 110s to fetch and 5.9s to run") confirmed that figure rather than overturning it: compute really is 5.9s, `run_time: fast` **stays**, and the other ~92s of the ~110s wait is `git_clone_root`'s treeless clone lazily fetching from the remote during co-change's history walk — acquisition, not tiering. Of that entry's two candidate fixes, one is **solved** (dwolfson-59, same day, `re/deferred-cleanup-followups` commit `63e7ec6`): `git log --name-only`'s default *inexact* rename detection scores blob-content similarity, which is exactly what defeats `--filter=blob:none` — fixed with `--find-renames=100%`, since an exact rename compares blob OIDs already in the tree. Post-fix, acquisition dominated the remaining cost rather than the reverse (86%/61% of two repos' totals). The other candidate fix — cache the acquired roots across runs — is also **done**, same day: `SourceCache` (`github/source_cache.py`) keys `zipball_root`/`git_clone_root` on (repo, commit SHA), shared across `SurveyOrchestrator.run()` calls rather than only within one; `_default_branch_sha` now delegates to `GitHubClient.get_latest_commit_sha()` rather than reimplementing it (built by dwolfson-59, reviewed by S1 — see `docs/Backlog.md`'s entry for the review findings, including one real regression it caught). Measured: acquisition 22.64s cold → 1.28s warm; the full route for `egeria_python_git` went 110.5s → 30s → **14.4s**. **Separately, the same day, `architecture_recovery` was re-tiered from `discovery` to `analysis` anyway** (a direct decision from the project owner: "architecture recovery is an analysis step and belongs there") — a judgement on other grounds than cost, which the Backlog entry had explicitly left open rather than resolved by the profiling. Both changes reached the catalog from different sessions the same day; `analysis_catalog.yaml`'s entry records both so neither reads as having overridden the other. A third, related ruling landed the same day: `availability` (whether a compiler may run an analysis inline) no longer *derives* from `run_time` (whether it's cheap to compute) — they came apart exactly here, since `architecture_recovery` is honestly fast to compute and must still never run inline, because it downloads two artifacts first. `availability` is now its own declared field on `AnalysisCatalogEntry`, defaulting to `queued`; `architecture_recovery` is `fast` and `queued` together, which is the case `test_a_fast_analysis_may_legitimately_be_queued` (`test_catalog_invariants.py`) pins so nobody "fixes" it back into a derivation. An analysis that fetches AND is expensive belongs in Analysis or Assessment, same as any other; `tests/test_analysis_catalog_reader.py` still carries a `DISCOVERY_FETCHES_ANYWAY` exception set by name (currently `repo_classification`, `architecture_doc_lens`) for the entries that stay in Discovery despite fetching, so a new fetching Discovery entry still fails the check until someone adds it deliberately. Assessment keeps what genuinely evaluates against criteria (`security_scan`, `documentation_coverage`, `ci_quality`, `security_features`). This matters more as steps grow into the dozens: Discovery is the cheap tier that gates the expensive ones. `automate` is sustained *machine* attention (recurring watch-for-changes via local subscriptions, `scheduler.py`-driven detection, RFA delivery), parallel to `curate`'s sustained *human* attention — see `docs/discovery-automate-project-context-plan.md` Part 4 and `docs/automate-notification-manager-pyegeria-spec.md` for the real-Egeria-NotificationType follow-up this doesn't attempt yet.

## Testing

```bash
uv run pytest tests/ -v
uv run pytest --cov=resource_explorer --cov-report=html
```

## Code Style

```bash
uv run black resource_explorer/
uv run ruff check resource_explorer/
uv run mypy resource_explorer/
```
