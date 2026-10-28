# The survey model — microflows, surveys, funnel phases, and what a result is

**Status:** consolidated design and reference. Current as of 2026-09-02.
**Read this before making architectural changes to how surveys are defined,
composed, or presented.** For how a survey is *executed and dispatched*, see
`survey-execution.md`. For authoring a definition in Egeria, see
`survey-definitions.md`. For the catalogue of individual surveyors, see
`surveyor-reference.md`.

> **This document consolidates ten.** Part I is the original activity design
> (D1–D12), which several other documents cite by decision number, so the
> numbering is preserved. Part II merges the unifying microflow/funnel model,
> composition, guard evaluation, question context, cost tiers, catalogue
> completion, and the results surfaces. Part III is the settled register.

---

## The model in four words

Everything below is one shape, and naming it removed several apparent
disagreements between documents that turned out to be the same idea at
different altitudes.

- **Microflow** — the atomic unit of work. Self-contained: it acquires whatever
  shared resource it needs, performs whatever writes or refreshes make its data
  current, reads what it just ensured, and emits annotations. One `StepInfo`,
  one Egeria-visible step (an `EmbeddedProcess` from Egeria's point of view).
- **Survey** — a named, *ordered composition* of microflows. The same microflow
  may appear in more than one survey. A user picks the survey that matches the
  need — *"just re-check git statistics"* versus *"complete refresh"* — rather
  than a fixed one-size bundle.
- **Funnel phase** — Scouting, Discovery, Analysis, Assessment, Enrichment,
  Understanding, Curate, Automate. A phase offers the surveys whose annotations
  answer that phase's questions. **The phase does not hardcode which surveys
  belong to it; the `ScopedBy` question graph does**, so the mapping is
  configuration rather than code.
- **Result** — what a person looks at, which is *not* the same as the step that
  produced it (see Part II §3).

---

## Strategic Decision: Project Split

The work described in this document has grown beyond the original Project Explorer scope. Project Explorer was designed as a general-purpose, multi-agent RAG system for exploring GitHub repositories — useful to technical users who may have no interest in Egeria.

The Egeria-focused survey and annotation capabilities being designed here represent a distinct product with a different target user, different primary data model, and different design constraints. Continuing to build both inside the same repository risks constraining the new work to design decisions that were correct for the old use case but are wrong for the new one.

**Proposal:** Keep Project Explorer as-is (it has independent value for non-Egeria users) and create a new repository for the Egeria-focused tool. The working name is **Egeria Resource Explorer** — reflecting the focus on exploring and understanding information resources through the lens of Egeria's metadata catalog. Code that is clearly general-purpose (embeddings, LLM client, RAG infrastructure) can be shared or extracted into a common library over time.

**This document is written as a design foundation for the new Egeria Resource Explorer project.**

**Working name:** "Resource Explorer" — a shorter form that implies the broader scope (databases, file systems, Git repos) without tying the name to a single platform. Can be changed before launch if a better name emerges.

**Migration approach:** Resource Explorer is a superset, not a fork. The underlying surveying code, Egeria integration modules, FastAPI routes, and RAG infrastructure all transfer with little or no change. The main new work is the UI shell — the activity log, analysis menu, and survey source display require a rethink of the frontend information architecture. Incremental porting: move modules one at a time, replace the UI shell last, and keep Project Explorer functional throughout.

---

## Background and Motivation

### What the tool is trying to do

Egeria Resource Explorer is a tool for discovering, understanding, and cataloging information resources — databases, file systems, Git repositories, and other data assets — using Egeria as the central metadata catalog and governance platform. It is not primarily an AI chat interface (that is Project Explorer's identity); it is a scouting, surveying, and context-gathering tool that produces structured metadata.

Four distinct user intents drive the design. The names below are the canonical intent labels used throughout this document for routing, filtering, and analysis tagging — modeled after the intent classification pattern in egeria-advisor (where `LIVE_DATA`, `CODE_HELP`, `CONCEPT`, `WRITE_COMMAND` route queries to different handlers).

> **Update (intent-shell rewrite):** the web UI now implements **seven** intents, not four — Scouting/Assessment/Discovery/Enrichment below plus **Analysis** (structural/quantitative work split out of Assessment — dependency scans, data profiling, API extraction: real work, but not a *scored evaluation* the way Assessment is), **Understanding** (chart-based trend visualization, previously a sidebar fixture with no intent of its own), and **Curate** (catalog maintenance — annotation type registry, resource groups, analysis schedules). The four intents' original rationale below is still valid for what it covers; treat this as an addition, not a correction. See `CLAUDE.md`'s "Seven User Intents" table for the canonical current list, and `resource_explorer/configdata/analysis_catalog.yaml`'s header comment for the reclassification this drove in the analysis catalog.

> **Update (Scouting/Analysis/Assessment boundary fix):** for repos, Scouting's "broad inventory" below used to actually render the full deep survey report — file types, dependencies, data profiling, Egeria survey history — despite this design doc always having said Scouting should be fast/broad, not deep. That's fixed: Scouting now shows a light, GitHub-API-only overview (description, language, stars/forks/contributors, last-pushed, plus a survey/publish lifecycle badge) with a link out to the unchanged deep report. Analysis/Assessment cards also gained what they never had — a real per-analysis results view (`GET .../analyses/{id}/results`) and a per-analysis trend chart (`.../trend`), instead of "Run" only producing a toast pointing back at the deep report. See `docs/user-guide.md`'s Scouting/Assessment/Analysis sections for the current user-facing behavior.

**Scouting** is about breadth. Given a newly-discovered server or file system, a data engineer or DevOps practitioner wants to quickly understand what's there: how many databases, how many rows, who manages it, when it was last updated, is it actively used or a candidate for decommissioning? Scouting works across many resources at once and delivers results fast. The output is a broad inventory, not deep analysis.

**Assessment** is about depth, applied selectively. After scouting identifies something worth understanding, a data steward, data scientist, AI engineer, or security specialist runs assessments to understand the risks, quality, completeness, and lineage of a specific resource. Assessments can be shallow or deep, broad or focused (security-only, quality-only, lineage-only), and are often run incrementally — first a quick Egeria native survey to see what annotations it produces, then targeted custom analyses to answer specific questions. Assessments are also temporal: running the same analysis repeatedly and tracking how things change over time is as valuable as the initial result.

**Discovery** is about retrieval. Business users, data scientists, and AI engineers want to find data relevant to their needs. They search and filter by what assessments have revealed: "find all databases with a SecurityRFA," "find all tables with >1M rows and a data quality annotation." Discovery is the payoff for all the scouting and assessment work — the catalog becomes useful only when it can be searched by what it knows.

**Enrichment** is not automated at all. Many critical facts about a resource cannot be derived from scanning: Is this database in a production system or a research sandbox? Who is the responsible DBA? What organization owns it? Where is it geographically located? Is it backed up? Egeria's `RequestForAction` annotation is the right primitive here — a structured prompt for a human to supply a specific piece of information, tracked until answered and then promoted into the catalog as permanent metadata. This requires a UI for managing open RFAs, assigning them to people, recording answers, and closing them.

Intent and persona are cross-cutting in the analysis menu (D4): a DBA performing an Assessment sees different recommended analyses than a data scientist performing Discovery. Persona is distinct from intent — a persona (DBA, data scientist, steward, security engineer) represents who the user is; intent represents what they are trying to accomplish right now. In the current UI, persona ("perspective") is multi-select/concurrent (you can hold more than one at once) while intent is exclusive-select — see `resource_explorer/surveyors/analysis_catalog_reader.py`'s `list_perspectives()`.


### What we are currently doing (and the problems)

Project Explorer currently supports Git repository surveys and PostgreSQL database surveys. Three structural problems have emerged:

1. **The activity log is shallow and ephemeral.** In-memory only, lost on page reload, coarse summaries rather than structured Egeria output.

2. **Survey panels do not distinguish data sources.** Local Python/SQL scan results and Egeria native annotation results look identical in the UI.

3. **"Survey" conflates two different activities.** Structural inventory (fast, always available, tells you what exists) and governance assessment (slower, on-demand, tells you what it means) are treated as one operation.

---

## Core Design Decisions

### D1 — Organize by Analysis and Intent, Not by Source [Revised]

The original framing split annotations by *who runs them* (our code vs. Egeria vs. third-party tools). This is the wrong axis. The same row count result means the same thing regardless of whether it came from a local `pg_stat_user_tables` query or an Egeria native survey. Results from multiple sources should be merged, not kept in separate silos.

The right axes are **what analysis was done** and **what intent drove it**.

**Analysis** is the unit of work: a specific examination of a specific resource that produces a specific set of annotations. An analysis has a name, a description, a list of annotation types it produces, and optionally a source (Egeria governance action, local SQL, external tool). The same analysis may be available from multiple sources — when that happens, we prefer Egeria's result but show the local result when Egeria is unavailable.

**Intent** is the reason the analysis was run. Seven intents in the current implementation (see the Background section's update note) — the original four, plus Analysis/Understanding/Curate:
- **Scouting** — broad, fast, run across many resources at once; produces a summary view
- **Discovery** — produces annotations structured to support search and retrieval; launches Egeria Survey Definitions
- **Assessment** — deep, targeted, run on specific resources; scored evaluation against criteria; produces detailed annotations
- **Analysis** — structural/quantitative work on a specific resource (dependencies, data profiling, API extraction) — not a scored evaluation, so kept distinct from Assessment
- **Enrichment** — captures human-provided context via the Context form; writes it to Egeria as structured properties
- **Understanding** — chart-based visualization of trends over time (stars, commits, schema, table sizes, etc.)
- **Curate** — catalog maintenance: annotation type registry, resource groups, analysis schedules

RFA (RequestForAction) response handling — assigning, deferring, completing — is **not** one of the seven; it's a persistent drawer independent of the active intent (see D5-equivalent in the current UI, `#rfa-drawer` in `index.html`).

**In almost all cases, analysis results should be stored in Egeria**, even when the analysis itself was run locally. Egeria is the catalog of record. Local SQLite is a cache and queue — useful for offline work and fast reads, but not the authoritative store.

**Change over time is a first-class concern.** The same analysis run at regular intervals produces a time series. Row count trends, schema drift, security posture changes — these are as important as the initial snapshot. Design the annotation store and activity log from the start to support temporal queries. A scheduler (likely Airflow in production deployments) is a future component that will drive regular-cadence runs.

---

### D2 — Activity Log Persistence [Proposed]

Persist the activity log to a database so history survives page reloads and sessions. All operations — scouting runs, survey triggers, catalog operations, RFA creation — write to this log.

SQLite is acceptable for the initial implementation. The long-term home is PostgreSQL: the primary data sources being surveyed are often Postgres instances, and consolidating app storage there as well simplifies deployment. Migration path: SQLite → Postgres via Alembic or a direct schema port; keep the API layer stable so the frontend doesn't change.

Retention policy: configurable via `.env` (default: keep indefinitely up to a row limit, e.g., 10,000 entries).

---

### D3 — Unified Activity Item Schema [Revised]

Every operation that touches a resource produces one or more activity items. The schema covers all resource types uniformly:

```
ActivityEntry {
  id:              uuid
  ts:              ISO timestamp
  operation:       'scout' | 'survey' | 'catalog' | 'publish' | 'discover' | 'rfa' | 'refresh'
  intent:          'scouting' | 'discovery' | 'assessment' | 'analysis' | 'enrichment' | 'understanding' | 'curate'
  entity_type:     'repo' | 'database' | 'server' | 'filesystem' | 'file'
  entity_slug:     internal slug
  entity_name:     display name
  entity_location: URL, server:port, file path, etc.
  status:          'running' | 'ok' | 'error' | 'pending'
  summary:         one-line result
  detail:          longer text, error message, etc.

  items: [          # cataloged Egeria objects created/updated
    {
      kind:           Egeria open type name (e.g. "PostgreSQLServer", "SurveyReport")
      display_name:   human-readable name
      qualified_name: Egeria qualifiedName
      guid:           Egeria element GUID
      location:       URL / connection string / path
    }
  ]

  annotations: [    # analysis results produced
    {
      analysis_name:    name of the analysis that was run
      annotation_type:  Egeria annotation subtype
      count:            number of annotations of this type
      status:           'local' | 'in-egeria' | 'pending-egeria'
      summary:          brief description of findings
    }
  ]
}
```

---

### D4 — Survey and Analysis Menu [Revised]

Both Egeria-provided surveys and our own local analyses should be organized and presented in the same way. The user sees a unified menu of "available analyses" for a given resource, regardless of where the analysis logic lives. Each entry shows:

- Name and description
- What annotation types it produces
- Typical run time (fast / minutes / async)
- Last run date and result count (if previously run)
- Recommended badge for high-value analyses by persona

Egeria's `AutomatedCuration.find_technology_types()` and `get_tech_type_detail()` are the source for Egeria-provided analyses. Our own analyses are registered in a local catalog using the same schema. When Egeria and our own tool can both run the same analysis (e.g., row counts), we show one entry with a note about the source preference.

The menu should support:
- **Filtering by intent** — scouting / discovery / assessment / analysis / enrichment / understanding / curate
- **Filtering by persona** — DBA (performance, security, index health), data scientist (quality, profiling, growth), steward (lineage, classification, RFA status), security (privilege audit, sensitive data)
- **Search** across analysis names and descriptions
- **"Run all scouting analyses"** as a single action — the primary entry point for new resources

Separate catalog and survey actions: cataloging (registering an asset in Egeria) happens once; surveys can be run many times with different intents. These should be distinct UI actions.

**Resolved:** `get_tech_type_detail()`'s real shape was prototyped and is now wrapped by `EgeriaTechTypeCatalog.get_produced_annotation_types()` (`resource_explorer/surveyors/egeria_tech_type_catalog.py`), which deduplicates `producedAnnotationType` entries out of `resourceList`/`governanceActionProcesses`. It's consumed by both `survey_definitions.py` (Discovery) and `analysis_catalog_reader.py`'s live-Egeria merge (Assessment/Analysis) — fail-soft, so Egeria being unreachable falls back to the local catalog rather than blocking the menu.

---

### D5 — Async Survey Result Retrieval [Partially Implemented, 2026-09-19]

Egeria native surveys are asynchronous. We receive a `survey_action_guid` (engine action guid) when the survey is triggered; annotations appear later. Egeria has a notification framework but no webhook capability today.

**What's built (2026-09-19):** for a Survey Definition step tagged `executes_at="egeria"`, the database and filesystem adapters' `_trigger_egeria_native_survey` handlers (`resource_explorer/surveyors/database/survey_definition_adapter.py`, `.../filesystem/survey_definition_adapter.py`) no longer fire-and-forget. Each now:

1. records the wall-clock time just before triggering,
2. triggers Egeria's native survey (`trigger_survey_by_guid`),
3. **synchronously polls** the resulting engine action to a terminal `activityStatus`, reusing `egeria_delegated_step._poll_action_status`'s live-verified polling logic (interval/timeout, terminal-state detection),
4. identifies the *specific* new `SurveyReport` this trigger produced (see the attribution mechanism below), and
5. reads back that report's real annotations and converts them into RE's own `Annotation` dataclass shape.

The step now reports `"status": "ok"` with real output, or a specific error (`EgeriaEngineActionTimeoutError` on poll timeout, `SurveyReportAttributionError` on an unresolvable report) — never the previous unconditional `"triggered"` regardless of outcome. `survey_definition_executor.py`'s `other_engine_handlers` dispatch (~line 473) now reports the handler's own `status` when it sets one, rather than hardcoding `"triggered"`; a handler that still only fires-and-forgets (no `status` key) keeps the old wording.

The shared poll/resolve/convert logic lives in `resource_explorer/surveyors/egeria_async_survey_result.py`, used identically by both the database and filesystem adapters.

**The report-attribution problem, and how it's solved:** after triggering, a resource's asset can have multiple `SurveyReport`s linked via `ReportSubject` (an old run, a concurrent unrelated survey, this trigger's own). No direct EngineAction→SurveyReport relationship was found in Egeria's metadata graph during this build (investigated via `AssetMaker.get_asset_by_guid` graph-walk output on a completed engine action) — if one is found later, querying it directly would be simpler and should replace the mechanism below.

In its absence, the report this trigger produced is identified by timestamp: take the single `SurveyReport` whose `surveyed_at` is strictly after the recorded pre-trigger time. Not "the newest report" (a concurrent unrelated survey could be newer) and not "the first result" (ordering isn't guaranteed). If that narrows to zero or more than one candidate, `SurveyReportAttributionError` is raised — reported as an explicit ambiguity, never guessed past. See `resource_explorer/surveyors/egeria_async_survey_result.py`'s module docstring and `EGERIA-ASYNC-RESULT-RETRIEVAL-IMPLEMENTED.md` for the full design and open questions (including whether these converted annotations should also be re-published to Egeria under RE's own SurveyReport, which this build deliberately left undecided).

**Not changed by this build:** the general activity-log "pending" / "Check for results" polling UI described below, which is a different, broader mechanism (any survey trigger, not just `executes_at="egeria"` steps) and is unaffected by this work.

**Approach for the general activity-log case:** Polling. Each pending activity log entry shows a "Check for results" button; the UI also polls automatically every 30 seconds for any entries with `status='pending'` when the Activity panel is open.

**To investigate:** Egeria's notification/event framework and whether it can be used to push completion events to us without polling. Also open: whether a direct EngineAction→SurveyReport relationship exists that this build didn't find, and whether the `other_engine_handlers["egeria"]` step's now-synchronous multi-minute wait should route through Prefect for cancellation/visibility the way `executes_at="resource-explorer"`/`"prefect"` steps already can (investigated during this build: `other_engine_handlers` steps are never routed to Prefect today — `survey_definition_executor._use_prefect` only recognizes `"prefect"` and `"resource-explorer"` — so this wait now runs synchronously in whatever thread executes the Survey Definition, with no separate cancellation/visibility beyond the poll's own internal timeout; no changes made here since no problem from this was confirmed, but see the design-notes doc).

---

### D6 — Analysis Catalog and Context Gathering [Revised]

The set of analyses available for a resource type is organized at two levels:

**Level 1 — Analysis catalog:** A registry of all analyses available for a given resource type, each with a name, description, intent tags, persona tags, annotation types produced, and implementation (Egeria governance action name, or local Python/SQL module). Generic analyses work across all RDBMS; database-specific analyses (e.g., PostgreSQL system table queries) are tagged to their technology type.

Example analyses for PostgreSQL databases:

| Analysis | Intent | Persona | Source | Annotations produced |
|----------|--------|---------|--------|----------------------|
| Schema inventory | Scouting | All | Local / Egeria | SchemaAnalysis, ResourceMeasure |
| Row count snapshot | Scouting | All | Local / Egeria | ResourceMeasure |
| Index health | Assessment | DBA | Local SQL | RequestForAction, DataClass |
| Table growth rate | Assessment | DBA, data scientist | Local SQL | ResourceMeasure (time series) |
| Privilege audit | Assessment | Security | Local SQL | DataClass, RequestForAction |
| Data profiling | Assessment | Data scientist | Egeria | SchemaAnalysis, DataClass |
| Sensitive data classification | Discovery | Steward, security | Egeria | Classification, RequestForAction |
| Constraint coverage | Assessment | DBA, steward | Local SQL | RequestForAction |
| Environment / ownership form | Enrichment | Steward | UI form | (writes to Egeria asset properties) |

**Level 2 — Questions and perspectives:** Different personas have different questions they want answered. Egeria supports the concept of perspectives and associated questions. We should align our analysis catalog with these so that "run all DBA assessment analyses" maps to a specific, documented set of analyses with a known set of output annotation types.

**Context gathering — forms and questionnaires:** Some critical metadata cannot be derived by scanning. Environment (production vs. sandbox), organizational ownership, geographic location, responsible steward, backup status, sensitivity classification — these require human input. We need:
- Structured forms for common context questions, tailored by resource type
- Free-form fields for notes
- Integration with Egeria's `RequestForAction` annotations as the tracking mechanism
- A UI panel for open RFAs: view all open questions across all resources, assign to people, record answers, close when resolved

**Advanced Egeria concepts to leverage:**
- **DataScope** — bounds the data in space (geographic bounding box) and time (collection start/end). Relevant for assets that represent subsets of a larger dataset.
- **DataLens** — defines the scope of data for a project, team, or business capability. DataScope on an asset can be matched against a DataLens to determine relevance.
- **DataGrain** — captures the granularity of data (e.g., daily snapshots, per-transaction). Important for time-series data and for understanding how to aggregate or join datasets.

These should be surfaceable in the context-gathering forms and in the survey report display.

References:
- egeria-advisor intent/routing patterns: `/Users/dwolfson/localGit/egeria-v6/egeria-advisor/advisor/llm_intent_classifier.py`, `query_processor.py`
- https://egeria-project.org/concepts/data-lens
- https://egeria-project.org/types/6/0626-Data-Grain-Discovery

---

### D7 — Resource Types: Scope and Priority [Decided]

The framework must be generic across resource types from the start. Resource-type-specific logic lives in pluggable modules; the activity log, analysis catalog, and UI shell are shared.

**Priority order:**
1. PostgreSQL databases *(current, partially implemented)*
2. File systems and files *(next — structurally similar to Git repo surveys)*
3. Git repositories *(current, to be brought into the new architecture)*
4. Other RDBMS (MySQL, SQLite, DuckDB) *(medium term)*
5. Object storage (S3, GCS, Azure Blob) *(medium term)*
6. Streaming (Kafka topics) *(longer term)*
7. REST APIs, GraphQL endpoints *(longer term)*

---

### D8 — RequestForAction Lifecycle [New]

RFAs are currently created by surveyors but have no management UI. They represent open questions that require human answers to complete the catalog. A complete RFA workflow:

1. **Creation** — any analysis or context-gathering form can create an RFA with a specific question, the resource it relates to, and the persona best suited to answer it
2. **Assignment** — RFAs can be assigned to a named person or role
3. **Response** — the assignee provides an answer through the UI; the answer is written back to Egeria as a structured metadata property on the asset
4. **Closure** — the RFA is marked resolved; the answer becomes part of the permanent catalog record
5. **Escalation** — overdue RFAs are surfaced in a "pending context" view

The RFA panel is a cross-resource view: all open questions across all surveyed resources, filterable by persona, resource type, age, and assignee.

---

### D9 — Temporal Analysis and Scheduling [New]

Running the same analysis repeatedly and comparing results over time is as important as the initial snapshot. The activity log and annotation store must support this from the start.

**Near-term:** Each survey run is stored with a timestamp; the UI can show a history of runs and highlight changes (schema columns added/removed, row count change, new RFAs opened). A simple "changes since last run" diff view is the minimum.

**Medium-term:** User-configurable schedules for specific analyses on specific resources (e.g., "run row count snapshot on this database daily"). Stored as schedule records in the database, executed by a background thread or task queue.

**Long-term:** Integration with Airflow or another scheduler for production-grade cadence management. Survey results fed into a time-series store for trending and anomaly detection.

---

### D10 — App Storage Migration Path [New]

Current app storage: SQLite (`registry.db`). This is appropriate for a single-developer tool but will become a constraint as the tool is used by teams.

Migration path: SQLite → PostgreSQL. The registry, activity log, analysis results, and RFA store all move. Key considerations:
- Keep the registry abstraction layer (`ProjectRegistry` class) as the migration boundary — no direct SQLite calls outside the registry
- Alembic for schema migrations
- Connection pooling (asyncpg or psycopg3) for the async FastAPI routes
- SQLite remains available for local/offline deployments via a config flag

*(Historical — completed. Registry now runs on the shared Postgres instance by default, SQLite as an explicit fallback via `REGISTRY_DATABASE_URL`. Kept for context, not renumbered.)*

---

### D11 — Analysis-Kind Extensibility Registry [Decided]

Phase B (per-analysis results + trend charts) shipped 5 analysis kinds, each requiring hand-touched changes across ~7 places: `analysis_catalog.yaml`, a catalog-id → step-key map, the repo survey adapter's per-step runner closures, `SurveyOrchestrator`'s hardcoded surveyor-construction dict, a bespoke registry table + its own `upsert`/`query`/`query_history` trio, a catalog-id → (results reader, trend reader) map, and a frontend render branch. Adding a 6th kind meant touching all seven. Separately, `SecuritySurveyor` was misleadingly named for what it actually did (3 hygiene-artifact checks, not real security scanning) — more security-family kinds are coming (secret scanning, CVE/dependency-vulnerability checks, SAST, branch-protection audit), and the naming needed to leave room for that.

**Decision:**
- A two-tier registry in `repo_survey_definition_adapter.py` — `StepInfo` (one entry per `SurveyOrchestrator` step key: surveyor class, description, annotation types, any special construction kwargs) and `AnalysisKind` (one entry per `analysis_catalog.yaml` id: which step key(s) it runs, an optional `family` tag for grouping related kinds, and an optional `AnalysisKindResults` for kinds with a results view). Every other lookup — the adapter's step runners, `SurveyOrchestrator.all_surveyors`, the catalog-id → step-keys map, the catalog-id → readers map — is now a thin derived view computed from these two registries, not independently maintained.
- Two generic result-storage tables, `project_analysis_findings` and `project_analysis_metrics`, each with a `kind` discriminator column, replace what were four per-kind tables (the four reduced to exactly two real shapes: "list of typed findings" and "aggregate snapshot metric(s)"). A one-time forward migration copies old data across; the old tables are kept, deprecated, not dropped (soak period).
- Frontend: `_renderAnalysisResultsContent()` dispatches on a small `render` tag (`findings_list` | `metrics` | `custom`) instead of one hardcoded branch per kind. A new kind using either of the first two modes needs zero new frontend code — only a genuinely bespoke shape (dependency-by-ecosystem, for instance) needs the `custom` escape hatch.
- `SecuritySurveyor` → `SecurityHygieneSurveyor` (file and class rename only — `analysis_catalog.yaml`'s `security_scan` id and the `repo_security` step key are untouched, so no data/schedule migration). `family="security"` is reserved on its `AnalysisKind` entry as the convention future security-family kinds will share, so "all security findings" is one `kind LIKE 'security%'` query away later rather than a UNION across separate tables — not built yet, just reserved.

Net effect: adding a new plain analysis kind is one new surveyor class + one new `StepInfo` entry + one new `AnalysisKind` entry, not a seven-file change.

---

### D12 — Repo Discovery, Disposition Lifecycle, and Working Set [Decided]

"Which repos should we even be surveying" was entirely unaddressed — registration was one-URL-at-a-time via the CLI, with no way to search broadly, no memory of what was previously considered, and no distinction between "the canonical judgment on this resource" and "do I want to see it in my list right now."

**Decision:**
- **General search, not org-only.** `GitHubClient.search_repos()` (keyword/stars/language/license/pushed-after/org/topic, translated server-side into GitHub's qualifier-string query language) replaced an earlier org-only `list_org_repos()`. Archived repos and forks excluded by default.
- **Named discovery sources**, two types: `search` (a saved set of the above filters) and `list` (a manually-curated set of `github_url`s). The `list` type exists because a single `org:` search qualifier only fits foundations whose projects live under one umbrella org (CNCF, Apache, PSF) — it doesn't fit Eclipse (450+ projects across hundreds of distinct orgs) or LF AI & Data (a curated member list spanning unrelated orgs; Egeria itself is `odpi/egeria`, nothing to do with the `lfai` org's own governance repos). Auto-fetching a foundation's own structured list (a `landscape.yml`, an API) is real per-source integration work, deliberately not built — `list` sources are hand-populated for now.
- **Repo triage disposition** — `undecided` (default) → `tracking` / `investigating` → `abandoned` or `ignored` — keyed by `github_url` (not project slug) so it covers both an already-registered repo and a search candidate that was never imported at all. `ignored` (passed on early) and `abandoned` (went further, then decided against it) are kept as distinct terminal states rather than collapsed into one, specifically so the *history* — a separate, append-only `repo_disposition_history` table — reads honestly. The current-state table is upsert-only (one row per `github_url`); the history table is what backs a real timeline view.
- **Personal working set**, a second and deliberately *separate* hide/show axis from disposition — "do I want this in front of me right now" (a view preference) vs. "what's the canonical judgment on this resource" (disposition, in principle visible/meaningful to anyone). There is no per-person auth in this codebase today (one fixed Egeria service-account identity, not a login system), so working-set state is currently global, exactly like disposition's own `decided_by` field is currently just free text — but it's modeled as its own table specifically so a `user_id` column can be added later without touching disposition's schema.
- **Scouting becomes a 4-tab sub-workflow for repos** (Discover → Survey → Scouting Analysis → Disposition) — repo-facet-specific, DB/FS untouched. "Scouting Analysis" is a navigation shortcut to the real top-level Analysis intent, not a duplicated content pane; it's flagged as a likely future rename (to something like "Insights") since every survey is itself already a form of analysis, which makes "Analysis" as a name do double duty today.

---

### D13 — Disposition Recommendation Scoring [Designed, Not Built]

D12's disposition lifecycle deliberately left "a future recommendation step, scored against user-defined criteria, to help decide disposition" unscoped — noted only so D11's `AnalysisKind`-style extensibility would have an obvious home for it later. This entry does the design pass; it does not implement anything.

**The constraint that shapes the design:** disposition is keyed by `github_url`, not project slug, specifically so it can apply to a search candidate that was *never imported* — the actual "should I even bother registering this" moment D10/D12 exist to serve. A recommendation step that only works after a repo is registered and surveyed would miss that moment entirely. This rules out building it as a real `AnalysisKind` (D11's registry) as the primary mechanism: `SurveyOrchestrator` only ever runs against a registered `Project` with a local clone — an unregistered `DiscoveredRepo` has neither.

**Decision: two tiers, not one.**

- **Tier 1 — search-time scoring, computed from fields the discovery search already returns** (stars, forks, language, license, last-pushed date, already covers both `already_registered` and never-imported candidates uniformly, since `DiscoveredRepo` carries the same fields either way). No clone, no survey run, no new backend call — a pure function over data already in hand at `POST /api/discovery/search`/`/sources/{slug}/run`'s response, computed server-side and attached to each `DiscoveredRepo` as `recommendation_score` (0–100) + `recommendation_reasons` (short strings, e.g. "no commits in 400+ days", "no OSI license detected"). This is the tier that actually answers "should I even bother" — before any registration decision.
- **Tier 2 — an optional deepen-after-import step**, real `AnalysisKind` machinery (D11), that folds in signals only a survey can produce (`repository_health`'s quality score, `security_scan` findings) to refine the Tier 1 score once a repo has actually been registered and surveyed. This is a natural `AnalysisKind` entry (`family="scoring"` or similar) — reserved as an extension point, not built now, exactly like D11's `family="security"` reservation for not-yet-built security-family kinds.

**User-defined criteria, not a hardcoded formula.** A small weighted-criteria shape (e.g. `{min_stars, min_contributors, max_days_since_push, license_allowlist, language_allowlist}` → weights) — global default, with an optional per-discovery-source override (the same extension pattern D1's `fetch_kind`/`fetch_url` used: a new optional field on `DiscoverySourceConfig`, defaulting to the global set when absent). Different discovery sources plausibly want different weighting — an internal-enterprise-repos `list` source cares about internal activity signals, not GitHub stars, which a public-foundation `search` source cares about a great deal.

**Suggestion, never an auto-write.** Matches this codebase's standing convention (disposition, publish, working-set toggles — every state change is a deliberate human action, never an inferred side effect): the score and its reasons render as a badge/tooltip next to each search result and the existing disposition control, informing the Track/Investigate/Ignore decision — the recommendation step never sets a disposition itself. `decided_by`/`reason` on an actual disposition change can optionally reference the score that informed it (e.g. `reason: "score 82 — active, permissive license, matches team's Python stack"`), but that's the human's own free-text note, not something the scorer writes.

**Not scoped by this entry:** the exact criteria-weighting formula, whether Tier 1 criteria live in a new `configdata/*.json` file (matching `foundation_prefilters.json`'s existing precedent) or a new `app_settings` key (D1's existing generic key-value table already fits this without a schema change), and the UI placement of the score badge relative to the existing disposition control. These are implementation-time decisions once this actually gets built, not design decisions to lock in now.

---

## Implementation Phases

### Phase 0 — Project Foundation

**Scope:** Establish the new Egeria Resource Explorer project.

**Tasks:**
- Create new GitHub repository under the LF AI org (or personal for now)
- Define the project structure, tech stack, and packaging (`pyproject.toml`, `uv`)
- Port the database surveying code (the most mature new capability) as the first module
- Port the web UI shell (FastAPI + index.html) with a clean separation from the RAG/chat infrastructure
- Set up the shared resource type abstraction so PostgreSQL is the first implementation of a generic interface
- Decide on SQLite vs. PostgreSQL for the new project's own storage

**Deferred to the new project:** All phases below. Project Explorer continues to receive maintenance but new Egeria-focused features go into the new repo.

**Open for discussion:** Timing — should we finish the current iteration of Project Explorer's database surveying (activity log, source clarity) before splitting, or split now and carry the work forward in the new repo?

---

### Phase 1 — Survey Source Clarity

**Goal:** Users always know where data in a survey panel came from.

**What this means in practice:**
- Every value in the survey panel carries a source indicator: `☁` for Egeria annotations, `🏠` for local scan, `⏳` for pending async result
- Section headers clearly label the analysis that produced them (e.g., "Schema inventory — local scan, 2026-06-09 14:32")
- Egeria annotations and local scan results are shown in separate sections, not intermixed
- Pending Egeria results show a placeholder with the survey action GUID and a "Check for results" button

**Effort:** Small — primarily frontend

---

### Phase 2 — Persistent Unified Activity Log

**Goal:** All operations across all resource types are recorded in a persistent, structured log accessible after page reload and filterable by intent, resource type, and status.

**Schema:** As defined in D3 above.

**Key behaviors:**
- Every scout/survey/catalog/publish/RFA operation writes an entry
- Pending Egeria surveys show in the log with status `pending`; polling updates them
- Repo publishes and database catalogs use the same entry structure
- Filter by entity type (All / Repos / Databases / Files), intent (Scouting / Discovery / Assessment / Analysis / Enrichment / Understanding / Curate), and status
- Retroactively populate from `project_egeria_surveys` and `database_surveys` on first run

**Effort:** Medium

---

### Phase 3 — Annotation Detail and RFA Panel

**Goal:** The activity log and survey panels show full annotation detail; open RFAs are manageable.

**Activity log:** Each entry expands to show the items table (D3 schema) and a per-annotation-type breakdown with counts, summaries, and GUIDs. For Egeria surveys, this is fetched from Egeria once available. For local scans, this is populated immediately from the survey result.

**RFA panel:** A cross-resource view of all open RequestForAction annotations. Sortable by resource, age, persona, and assigned person. Inline form to record an answer; answer written back to Egeria on submit.

> **Status:** built as a persistent `#rfa-drawer` (reachable from anywhere, not a per-resource tab) with real defer/reassign/complete/reopen response actions — see `rfa_actions` table and `PATCH /api/activity/rfas/{rfa_id}`. **Not yet accurate to this doc's original phrasing:** response actions and the free-text "record answer" form are both local-only right now (SQLite `rfa_actions` table / activity log), not written back to Egeria as `RequestForActionProperties` updates or native `ToDo` actions — that write-back is a real, tracked follow-up, not this doc's original design realized. Don't assume "answered" or "completed" here means Egeria's own record changed.

**Effort:** Medium

---

### Phase 4 — Analysis Catalog and Survey Menu

**Goal:** Users see a unified, organized menu of available analyses for any resource, drawn from both Egeria and our own analysis catalog.

**Prerequisites:**
- ~~Prototype `find_technology_types` and `get_tech_type_detail` against the live Egeria instance and document what they return~~ — done, see D1's "Resolved" note above and `egeria_tech_type_catalog.py`
- ~~Define the local analysis catalog schema (mirrors the Egeria output format)~~ — done, `configdata/analysis_catalog.yaml` + `analysis_catalog_reader.py`

**What ships:**
- Analysis menu per resource with intent and persona filters
- "Run all scouting analyses" as a one-click entry point
- Catalog and survey as separate actions (catalog once; survey many times)
- Survey history with change highlights vs. prior run

**Effort:** Medium-large

---

### Phase 5 — Context Gathering and Forms

**Goal:** Structured collection of human-provided metadata; RFA creation from forms; answers written to Egeria.

**What ships:**
- Per-resource context forms: environment, owner, location, steward, sensitivity, DataScope fields, DataGrain fields
- Free-form notes field
- Auto-creation of RFAs for unanswered fields
- Form answers written to Egeria as asset properties
- DataLens matching: show which DataLenses this resource's DataScope overlaps

**Effort:** Large

---

### Phase 6 — Temporal Analysis and Scheduling

**Goal:** Track how resources change over time; schedule recurring analyses.

**What ships:**
- Change summary in survey panel: "vs. last run — 3 new columns, row count +12%"
- Per-resource analysis schedule configuration
- Background scheduler (thread-based initially, Airflow integration later)
- Time-series chart for key metrics (row counts, schema changes, annotation counts) in the survey panel

**Effort:** Large

---

## Open Questions

| # | Question | Status |
|---|----------|--------|
| Q1 | What does `get_tech_type_detail("PostgreSQL Server")` return from our Egeria instance? | Open — needs live API call |
| Q2 | Typical completion time for an Egeria PostgreSQL database survey? | Open — needs observation |
| Q3 | Does Egeria's notification framework support survey completion events? | Open — needs investigation |
| Q4 | Are qualified names returned by `create_postgres_*_element_from_template()` or fetched separately? | Open — needs pyegeria test |
| Q5 | What annotation types does the Egeria PostgreSQL survey produce for our schema? | Open — needs live test |
| Q6 | New repo: personal or LF AI org? Separate package or monorepo with Project Explorer? | Open — strategic decision |
| Q7 | Should the new project's own storage be SQLite (simpler) or PostgreSQL from day one? | Open |
| Q8 | Which Egeria DataLens, DataScope, DataGrain APIs are available in current pyegeria? | Open — needs pyegeria review |

---

## Explicitly Out of Scope (for this document)

- Changes to Project Explorer's RAG pipeline, pgvector store, or chat interface
- Multi-user authentication and per-user access control
- Real-time collaborative annotation
- TUI for the new tool (web UI first)
- Non-PostgreSQL RDBMS support in the initial implementation
- Airflow integration (scheduled analyses use a simpler background thread initially)

---

*Last revised: 2026-06-09. Update status field when moving from design to implementation.*

---

# Part II — Composition, guards, cost, and results

*Merged 2026-09-02 from nine notes. Each section names its source so a
git-history search still finds the original.*

## 1. Three names that keep collapsing into one

*(from `survey-composition-and-topic-summary-design.md`)*

| Concept | What it is | Where it lives |
|---|---|---|
| **Survey Definition** | a named, ordered graph of steps | Egeria, as a `GovernanceActionProcess` |
| **Step** (microflow) | one unit of work | `surveyors/sub_surveyors/` |
| **Analysis** | a catalogue entry describing a result a person looks at | `configdata/analysis_catalog.yaml` |

The distinction is written down and still collapses in practice, because
**almost every analysis maps to exactly one step, and every step is surfaced as
its own analysis card.** `AnalysisKind` accepts a *list* of steps and the
convention says an analysis "occasionally maps to several" — but **0 of 27
currently do.** When a 1:1:1 mapping is the only mapping anyone has seen, three
words start to feel like one thing with three names.

**That collapse produced a wrong recommendation on 2026-08-31.** Reviewing
security, a pass found `security_scan`'s three checks were each a weaker
duplicate of a check another analysis performs, and concluded the *step* should
be retired. Wrong layer: the step is cheap — fast, inline, reads stored data,
fetches nothing — and its output is consumed by `foss_scorecard`. What was wrong
is that it was *also* a top-level analysis card competing with better answers to
the same question.

> **The duplication was in presentation, not computation, and the fix for
> duplicated presentation is composition rather than deletion.**

This is the single most useful thing in this document for anyone about to delete
a surveyor: check whether the redundancy is in what is *computed* or in what is
*shown*.

## 2. Guard evaluation

*(from `survey-guard-evaluation-design.md`)*

Egeria's guard semantics — including the mandatory-guard join — were **verified
against the Java source**, not inferred. Guard evaluation exists for the Prefect
whole-definition path. Two gaps remain, and both are about the fallback rather
than the mechanism:

1. **The local fallback loop ignores the step graph.** `SurveyDefinitionExecutor.run()`
   — the path that must keep working whenever Prefect is off or unreachable —
   walks the flat step list rather than the graph, so guards have no effect there.
2. **No RE step runner emits a guard.** Every adapter returns
   `{"annotations": [...]}` with no `"guard"` key, so even the built Prefect
   guard-checker has nothing to evaluate for any step that exists today.

The second is the load-bearing one: a guard mechanism with no producers is
indistinguishable from no guard mechanism, and looks built.

## 3. Cost tiers

*(from `step-cost-tiers-plan.md`, implemented 2026-08-20)*

Surveys are composed **by budget rather than by enumeration**. Each step declares
what it costs to *acquire* its material and what it costs to *compute* over it,
on two independent axes, so a caller can ask for "everything under this fetch
budget" instead of naming steps.

Across 40 steps: `fetch_cost` is `none` for 21, `download` for 13, `api`/`api_heavy`
for 6; `compute_cost` is `low` for 34, `medium` for 3, `high` for 3.

**The axes must stay independent** — they come apart. An analysis can be cheap to
compute and still require a download, which is why *availability* (may this run
inline?) is declared rather than derived from *run time*. Declared costs are also
**observed**: a step claiming `fetch_cost: none` that opens a connection is
flagged, because an under-declared step silently breaks the guarantee that a
cheap tier is cheap.

## 4. Questions as the composition key

*(from `survey-question-context-plan.md`, D1–D3 built)*

A funnel phase does not hold a list of surveys. It holds a set of **questions**,
and the surveys reachable from those questions are what it offers. The binding is
the `ScopedBy` graph in Egeria, so changing which surveys a phase offers is
configuration, not a code change.

This is what makes the phase boundaries defensible: a survey belongs to Discovery
because it answers a Discovery question, not because someone filed it there.

## 5. Catalogue completion and how new surveys get authored

*(from `repo-survey-catalog-completion-plan.md`, `microflow-embedded-process-plan.md`)*

- **Steps are reused across surveys** — the same microflow appears in several
  survey types, and single-step survey types are a legitimate degenerate case
  rather than a workaround.
- **Survey types are generated from a CSV** rather than hand-authored one by one,
  with ad-hoc authoring available through Egeria Advisor.
- **Creating a new step or analysis is developer work**, and that is an explicit
  non-goal to make user-facing for now.
- **Resource type is a many-to-many dimension on `AnalysisKind`**, not a
  partition — the same analysis can apply to repositories and file systems.

## 6. Results surfaces

*(from `survey-results-dashboard-plan.md`, `survey-tab-unification-plan.md`)*

The per-phase sub-tab shape converged independently on: one tab to select and run
surveys, one to visualise results, one for questions, one for disposition — with
Scouting additionally carrying Search, because candidates are not registered
resources yet and nothing else needs it.

Tab unification is built and live-verified. The results dashboard is designed and
not built.

---

# Part III — Settled — do not reopen without re-measuring

| Question | Settled | On what basis |
|---|---|---|
| Is an analysis the same thing as a step? | **No** | 0 of 27 analyses map to more than one step, which is why they *feel* identical — the model allows many |
| Retire a step whose checks duplicate another analysis? | **No** | The duplication was in presentation; the step is cheap and its output feeds `foss_scorecard` |
| Does a funnel phase own a list of surveys? | **No** | It owns questions; the `ScopedBy` graph resolves surveys |
| Compose surveys by enumerating steps? | **No** — by budget | Cost tiers, implemented 2026-08-20 |
| Derive "may run inline" from "is fast"? | **No** | They come apart; availability is declared separately |
| Trust a step's declared cost? | **No** — observe it | An under-declared step silently breaks the cheap-tier guarantee |
| Is guard evaluation built? | **Partly** — and not usefully | Prefect path only, and no step emits a guard, so there is nothing to evaluate |
| Should users author new steps or analyses? | **No** — developer work | Explicit non-goal for now |
| Is resource type a partition on analyses? | **No** — many-to-many | The same analysis serves several resource types |
