# Survey Definitions — Usage Guide

**Status:** Implemented and validated end-to-end against a live Egeria server (2026-07-07/08) — both a single-step and a two-step chained PostgreSQL Survey Definition were authored, fetched, executed, and their `SurveyReport`s confirmed visible in Egeria, including a step correctly skipped (`executes_at: egeria`). See Current Limitations below for what's still unconfirmed (repo/filesystem Technology Type strings, branching).
**Design record:** `docs/egeria-integration.md`, section 6 — read that first for the *why* behind every design choice referenced here (no new Dr.Egeria commands, narrowest-action publishing, Technology-Type-driven discovery). This document is the *how to use it* companion — it doesn't re-derive those decisions, just points back to them.

---

## What a Survey Definition is

A Survey Definition is an Egeria `GovernanceActionProcess` with one `GovernanceActionType`/`GovernanceActionProcessStep` per analysis step, chained in order via `Link First Process Step`/`Link Next Process Step`. It's authored entirely *outside* Resource Explorer (RE) — in Egeria Advisor, as a Dr.Egeria plan — and declares, per step, where that step runs (`executes_at`), what resource technology type it applies to (`supported_technology_type`), and — for steps RE itself runs — which RE analysis step to dispatch to (`re_analysis_step`).

RE never authors Survey Definitions. It only reads one already in Egeria's catalog, runs whichever of its own steps are tagged for it, and publishes results back.

## Authoring one

Each step is a normal `Create Governance Action Process Step` Dr.Egeria command, and the containing survey is a `Create Governance Action Process` — no new commands exist for this, by design (see design doc §6.1). The RE-specific information lives entirely in the existing `Additional Properties` dictionary attribute:

| Key | Meaning | Example |
|---|---|---|
| `executes_at` | Where this step runs. Open-ended — not a fixed enum. | `resource-explorer`, `egeria`, or any other engine name (e.g. `airflow`) |
| `supported_technology_type` | Which resource technology type this step/survey applies to | `PostgreSQL Database` |
| `re_analysis_step` | For `executes_at: resource-explorer` steps only — which RE analysis step to run | `postgres_schema_and_stats` |

**Minimal example** — a one-step PostgreSQL Survey Definition (adapted from design doc §6.3, trimmed to a single step for clarity):

```markdown
## Create Governance Action Process Step
> A description of a call to perform a step in a governance action process. This acts as a template when creating the appropriate engine action instance.

### Display Name
Simple Postgres Survey — Schema and Stats

### Qualified Name
GovActionProcessStep::SimplePostgresSurvey::SchemaAndStats

### Additional Properties
| Parameter Name | Parameter Value |
|---|---|
| executes_at | resource-explorer |
| supported_technology_type | PostgreSQL Database |
| re_analysis_step | postgres_schema_and_stats |

___

## Create Governance Action Process
### Display Name
Simple Postgres Survey

### Qualified Name
GovActionProcess::SimplePostgresSurvey

### Additional Properties
| Parameter Name | Parameter Value |
|---|---|
| supported_technology_type | PostgreSQL Database |

___

## Link First Process Step
### Governance Action Process
GovActionProcess::SimplePostgresSurvey

### Governance Action Process Step
GovActionProcessStep::SimplePostgresSurvey::SchemaAndStats
```

**Chained example** — a two-step PostgreSQL Survey Definition executing both schema inventory and static SQL analysis/lineage:

```markdown
## Create Governance Action Process Step
### Display Name
Postgres Survey Step 1 — Schema and Stats

### Qualified Name
GovActionProcessStep::PostgresFullSurvey::SchemaAndStats

### Additional Properties
| Parameter Name | Parameter Value |
|---|---|
| executes_at | resource-explorer |
| supported_technology_type | PostgreSQL Database |
| re_analysis_step | postgres_schema_and_stats |

___

## Create Governance Action Process Step
### Display Name
Postgres Survey Step 2 — SQL static analysis

### Qualified Name
GovActionProcessStep::PostgresFullSurvey::SqlAnalysis

### Additional Properties
| Parameter Name | Parameter Value |
|---|---|
| executes_at | resource-explorer |
| supported_technology_type | PostgreSQL Database |
| re_analysis_step | sql_analysis |

___

## Create Governance Action Process
### Display Name
Postgres Full Survey

### Qualified Name
GovActionProcess::PostgresFullSurvey

### Additional Properties
| Parameter Name | Parameter Value |
|---|---|
| supported_technology_type | PostgreSQL Database |

___

## Link First Process Step
### Governance Action Process
GovActionProcess::PostgresFullSurvey

### Governance Action Process Step
GovActionProcessStep::PostgresFullSurvey::SchemaAndStats

___

## Link Next Process Step
### Governance Action Process Step
GovActionProcessStep::PostgresFullSurvey::SchemaAndStats

### Next Governance Action Process Step
GovActionProcessStep::PostgresFullSurvey::SqlAnalysis

### Guard
Any
```

Note the entity type: a step that participates in a `Link First/Next Process Step` chain must be created with **`Create Governance Action Process Step`** (qualified-name prefix `GovActionProcessStep`), not `Create Governance Action Type` (prefix `GovActionType`) — the link commands' "Governance Action Process Step" reference attribute is typed specifically to `GovernanceActionProcessStep` elements, confirmed against the real compact command spec (`egeria-python/md_processing/data/compact_commands/commands_action_author.json`). `Create Governance Action Type` is for a standalone action template that's never chained into a process at all — not this case.

### `re_analysis_step` values RE recognizes today

Each resource type has a small, fixed set of `re_analysis_step` keys RE knows how to run — confirmed directly from the adapter source (`*/survey_definition_adapter.py`), not aspirational:

| Resource type | `supported_technology_type` used for discovery | `re_analysis_step` values recognized |
|---|---|---|
| Database | `PostgreSQL Database` | `postgres_schema_and_stats`, `sql_analysis` |
| Repo | `Git Repository` | `repo_file_structure`, `repo_file_size`, `repo_language`, `repo_health`, `repo_dependency`, `repo_documentation`, `repo_security`, `repo_api_structure`, `repo_data_profiling`, `repo_file_classification` |
| Filesystem | `File System Directory` | `filesystem_inventory` |

Any `re_analysis_step` value outside this list is reported as an error at run time (see "What happens on a run" below) — it's a data-quality signal, not silently ignored. Repo is the richest case: each key maps 1:1 to one of the 10 existing sub-surveyors, so a repo Survey Definition can pick and chain any subset of them, in any order, rather than always running all 10.

## Running one

Three CLI commands, one per resource type, all thin wrappers around the same shared executor:

```bash
# Repos (top-level command, not under a resource-type sub-app)
project-explorer survey-definition <slug> [--survey-definition NAME] [--refresh-definition]

# Databases
project-explorer database survey-definition <slug> --user U --password P \
    [--survey-definition NAME] [--refresh-definition]

# Filesystems
project-explorer filesystem survey-definition <slug> \
    [--survey-definition NAME] [--refresh-definition]
```

Real examples:

```bash
project-explorer database survey-definition my-postgres --user admin --password secret
project-explorer database survey-definition my-postgres -s PostgreSQLStandardSurvey --user admin --password secret
project-explorer filesystem survey-definition my-fileshare
project-explorer survey-definition myproject
```

**Finding the right Survey Definition** — if `--survey-definition`/`-s` is omitted, RE looks up candidate `GovernanceActionProcess` elements by the resource's Technology Type (the same `get_tech_type_detail`-based lookup Egeria's own database surveyor already used, generalized — design doc §6.1):
- **Zero candidates** → clear error; author one first.
- **Exactly one** → used automatically, and its GUID is cached (`survey_definition_cache` registry table) so future runs skip the lookup.
- **More than one** → error listing every candidate's qualified name; disambiguate with `--survey-definition`.

**`--refresh-definition`** bypasses the cached GUID and re-resolves from scratch — use this if the Survey Definition was re-authored, renamed, or you registered a second one and want RE to notice.

## What happens on a run

The executor walks the Survey Definition's steps in order. For each step:

- **`executes_at: resource-explorer`** — dispatched to the matching local surveyor (per the table above). Success or failure is per-step: one step failing doesn't abort the run, it's recorded in the result's `errors` list and the run continues (mirrors the existing `SurveyOrchestrator` per-surveyor try/except pattern).
- **`executes_at: egeria`** — skipped. That's Egeria's own native survey machinery's responsibility, not RE's; the CLI reports it as `skipped_egeria`, not an error.
- **Any other value** (e.g. `airflow`, or a typo) — skipped, but surfaced as an error. RE doesn't silently drop steps it doesn't understand.

After the dispatch loop, if any RE step produced results, they're published back to Egeria — but **narrowly**: only a `SurveyReport` + annotations are created. This deliberately does **not** catalog the resource or trigger Egeria's own native survey as a side effect (unlike the older `database survey --egeria`/`publish_local_survey` path, which does both). The resource must already be cataloged in Egeria — if it isn't, the run fails clearly rather than silently auto-cataloging it.

For a database, the report carries the annotations of every RE step that ran: the schema inventory, statistics, views, operations and credential capability, and (since 2026-10-19) the column-profiling steps' own annotations. That means one `DataClassMatch` and one `ColumnValueSample` per column checked, `ReferenceDataMatch` for low-cardinality columns, the nested JSON/XML column profiles, and their requests for action. Before 2026-10-19 the column-profiling annotations were built but never published. A run of the same Survey Definition adds them; earlier reports are not changed. The user guide's "What a database's survey report carries" lists the verdicts.

## Current limitations

- **Linear sequences only.** A Survey Definition where any step has more than one outgoing `Link Next Process Step` (guard-based branching) is rejected with `UnsupportedSurveyDefinitionError` naming the step and branch count. Branching support is a known future extension, not yet built.
- **Fixed, hardcoded step vocabulary.** The `re_analysis_step` → surveyor mapping (the table above) is a small Python dict per resource type, not a catalogable, extensible registry. Adding a new recognized step today means a code change to the relevant adapter module. Building a real "publish RE's own analysis steps as catalogable Egeria elements" mechanism is a separate, deferred piece of work (see `docs/Backlog.md`, "Analysis-step inventory and registration").
- **Technology Type string for repo (`Git Repository`) is still a best guess, not yet confirmed live.** `PostgreSQL Database` and, as of 2026-07-13, filesystem's `File System Directory` have both been confirmed against a real Egeria server (`EgeriaTechTypeCatalog.get_tech_type_detail`) — filesystem's previous guess (`File Folder`) was wrong, see `docs/filesystem-and-database-surveying.md (§5)` §1. If discovery finds zero candidates unexpectedly for a repo Survey Definition, checking the real string Egeria uses for that technology type is the first thing to try.
- **`GovernanceActionProcess` graph shape: fully confirmed live, including chaining.** The first version of this reader assumed a shape (relationship items carrying a `relationshipHeader`/`type`/`typeName`) that turned out to be completely wrong. The real response is a genuine graph representation: `governanceActionProcess` (the process) and `firstProcessStep`/`nextProcessSteps` (a flat node list — `nextProcessSteps` items skip the `{"element": ...}` wrapper that `firstProcessStep` uses) describe the *nodes*, while a separate `processStepLinks` list (each entry `{"previousProcessStep": {"guid": ...}, "nextProcessStep": {"guid": ...}, "guard": ..., "mandatoryGuard": ...}`) describes the *edges* between them by GUID. Step properties live under `processStepProperties` (not `properties`, which only the process itself uses). The reader builds a node index + edge index from this and walks it from the first step, rejecting a guid with more than one outgoing edge as unsupported branching. Confirmed against both a single-step and a two-step chained Survey Definition, including a step correctly skipped as `executes_at: egeria`.
- **Branching (guard-based) Survey Definitions are still untested live** — the reader raises `UnsupportedSurveyDefinitionError` when a step's guid has more than one outgoing edge in `processStepLinks`, per design, but this rejection path itself hasn't been exercised against a real branching Survey Definition yet, only in unit tests with canned JSON.

## Architecture (implementation summary)

Full rationale lives in the design doc (§6); this is just an orientation map:

- **`resource_explorer/surveyors/survey_definition_reader.py`** — resource-type-agnostic. Only knows `GovernanceActionProcess`/`GovernanceActionType` shapes: fetches the graph via pyegeria's `GovernanceOfficer`, finds candidates via `AutomatedCuration.get_tech_type_detail`, parses into `SurveyDefinition`/`SurveyStep` dataclasses. The graph-to-dataclass parsing (`_parse_graph`) is side-effect-free and unit-tested against canned JSON (`tests/test_survey_definition_reader.py`) — no live server needed for that part.
- **`resource_explorer/surveyors/survey_definition_executor.py`** — the generic dispatch loop (`SurveyDefinitionExecutor.run`) plus the `ResourceTypeAdapter` plugin interface. Has no resource-type-specific code itself; adapters register themselves into a small module-level registry.
- **Adapters**, one per resource type, each wiring `re_analysis_steps` to existing surveyor code and a narrow `publish` callback:
  - `resource_explorer/surveyors/database/survey_definition_adapter.py` — publishes via a new `EgeriaDatabaseSurveyor.publish_step_annotations` method (narrower than the pre-existing `publish_local_survey`, which also triggers a native Egeria survey as a side effect).
  - `resource_explorer/surveyors/repo_survey_definition_adapter.py` — publishes via the existing, already-narrow `EgeriaPublisher.publish`, unmodified.
  - `resource_explorer/surveyors/filesystem/survey_definition_adapter.py` — publishes via a new `EgeriaFileSystemSurveyor.publish_step_annotations` method (narrower than `catalog_and_survey`, which also auto-catalogs the filesystem root and every data file it finds).
- **`resource_explorer/registry.py`** — new `survey_definition_cache` table (`get_survey_definition_guid`/`set_survey_definition_guid`) caches the resolved `GovernanceActionProcess` GUID per resource + technology type.
- **CLI** — `resource_explorer/cli/main.py`: `survey-definition` (top-level, repos), `database survey-definition`, `filesystem survey-definition`.
