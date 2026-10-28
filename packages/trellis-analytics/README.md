# trellis-analytics (the Analytic Library)

**What it is.** A small Python library of analyses that Resource Explorer (RE) and the Egeria Portal can run.
It holds two kinds of analysis:

- a **routine** reads what is already stored and counts or summarises it (for example `curation_coverage`);
- a **step** is a piece of a survey. It measures one thing per scope (for example one result per table) and
  RE runs it beside its own built-in steps.

Both return their result with an **envelope**: a state word that says how to read the value. There are four,
and no fifth:

| State | Means |
|---|---|
| `measured` | It looked and found a value. |
| `nothing_found` | It looked and there was nothing. This is an answer. |
| `not_established` | It could not look, or could not tell. A reason is required. |
| `not_applicable` | The question does not apply here. A reason is required. |

`nothing_found` and `not_established` are never the same word, so "zero" cannot be shown where the truth is "we
could not look".

For people who use the results, not write them: see the "Analytic Library" section of the RE
[user guide](../resource-explorer/docs/user-guide.md). Design background:
[`docs/extensibility-walking-skeleton-brief.md`](../../docs/extensibility-walking-skeleton-brief.md) and
[`DESIGN-ANALYTIC-EXTENSIBILITY-WORKING-ASSUMPTIONS.md`](../resource-explorer/docs/design-notes/DESIGN-ANALYTIC-EXTENSIBILITY-WORKING-ASSUMPTIONS.md).

## What ships

| Module | What it does |
|---|---|
| `trellis_analytics.envelope` | `Envelope` and the four states. |
| `trellis_analytics.manifest` | `RoutineManifest` and `StepManifest`, the declaration of an analysis (subclasses of pyegeria's `AnalyticFunctionSpec`). |
| `trellis_analytics.discovery` | `discover()` finds installed manifests; `register()` puts them in pyegeria's analytic registry. |
| `trellis_analytics.readers.ods` | Read-only access to RE's registry (a SQLite file or Postgres). |
| `trellis_analytics.routines.curation_coverage` | Published, not yet published and blocked, counted from RE's curation record. |
| `trellis_analytics.routines.context_pack` | Packages what Egeria holds about one database for one kind of consumer, each fact with a state word and provenance. |
| `trellis_analytics.portal` | The Portal tile for `curation_coverage`. |
| `trellis_analytics.step` | `SurveyStep`, the wrapper that runs a user's step code (see below). |
| `trellis_analytics.declare` | `manifest_properties`: what a step's Egeria declaration says about it, from its manifest. The declaration itself is RE's engine host's (see "Dispatched by Egeria" below). |

## Writing a routine

A routine is a plain function that takes a reader and what to measure, and returns a result that carries an
`Envelope`. There is no executor: you call it.

```python
from trellis_analytics.readers.ods import OdsReader
from trellis_analytics.routines import curation_coverage

result = curation_coverage(OdsReader("postgresql://..."), "database", "coco_pharma")
print(result.envelope.result_state.words, "·", result.summary)
print(result.to_dict())
```

To make it discoverable, give it a **manifest** and list the manifest as an entry point. `manifests.py` in this
package does it for its own routines:

```python
from trellis_analytics.manifest import RoutineManifest

CURATION_COVERAGE = RoutineManifest(
    name="curation_coverage",
    function="trellis_analytics.routines.curation_coverage.curation_coverage",
    description="Published, not yet published and blocked, counted from the curation record.",
    returns="CoverageResult (counts with the row ids they count, plus an Envelope)",
    source="ods",              # "ods", "egeria" or "resource": where the data comes from (required)
    version="0.1.0",           # required: a result is stamped with it
    resource_types=["database"],
    family="Curation",
    tags=["curation", "coverage"],
)
```

```toml
# pyproject.toml of the package that ships it
[project.entry-points."trellis.analytics"]
curation_coverage = "trellis_analytics.manifests:CURATION_COVERAGE"
```

**Installation is registration.** Install the package and `discover()` finds the entry point. A broken entry
point raises, naming it; it is never skipped silently.

Optional manifest fields `purposes` (investigation purposes it serves) and `questions` (question ids such as
`Q-0041`, never wording) are checked at load against Resource Explorer's lists. They are suggestive: an unknown
value is logged as a warning, naming it, and kept.

## Writing a step

A step has three levels:

1. **The code**: `run(inputs) -> list[dict]`. It owns the measurement and the reason.
2. **The wrapper**: `SurveyStep(manifest)`, in this package. It enforces the rules below.
3. **The survey**: an Egeria Survey Definition that names the step.

Each result the code returns is a plain dict. `scope` and `state` are required; a state other than `measured`
or `nothing_found` also needs a `reason`.

```python
def run(inputs):
    rows = inputs.ods.row_count_snapshots(inputs.slug, limit=2)
    if not rows:
        return [{"scope": inputs.slug, "state": "not_established",
                 "reason": "no survey has measured row counts for this database yet"}]
    return [{"scope": "public.orders", "state": "measured",
             "value": {"rowsChanged": 30}, "summary": "+30 rows"}]
```

Its manifest is a `StepManifest`:

```python
MANIFEST = StepManifest(
    name="database_staleness", display_name="Database Staleness",
    function="database_staleness.step.run", description="Rows changed per table since the last survey.",
    returns="one ResourceMeasureAnnotation per table, each with its envelope",
    source="ods", version="0.1.0", resource_types=["database"],
    credential_tier="none",       # "none" or "read_only"; anything higher is refused
    inputs=["ods"],               # what the wrapper hands the code: "resource", "ods", "connection"
    stage="analysis",             # the stage its results belong to
    questions=["Q-0041"], purposes=["Maintain"],
    request_type="re-database-staleness",       # what RE's engine host advertises to Egeria (default re-<name>)
    nothing_to_derive_guard="survey-invalid",   # survey-completed, survey-invalid or survey-failed
)
```

What the wrapper guarantees, so the code does not have to:

- **Only the declared inputs.** An undeclared input is not handed over.
- **No password, ever.** A `connection`, if declared, is opened by the host at the declared tier and passed as
  an object.
- **Read-only at most** on the resource. A higher `credential_tier` is refused when the manifest is built.
- **An envelope on every result.** A result with no state, an unknown state, or no reason where one is required
  is refused, and **nothing from that run is stored**: a partial store would read as the whole answer.

A complete example, installable on its own, is `examples/steps/database_staleness/`. It registers itself
through the same `trellis.analytics` entry point.

To tell RE and Egeria about an installed step, run the two generators from the repo root. The first writes the
catalog entry into `analysis_catalog.yaml` (between its GENERATED markers); the second writes the step's Egeria
declaration into `packages/resource-explorer/docs/dr-egeria/engine-host/library/<step>.md` (see below). Neither
talks to Egeria; running the declaration is a separate, deliberate step.

```bash
uv run --with-editable packages/trellis-analytics/examples/steps/database_staleness \
    python packages/resource-explorer/scripts/generate_external_step_entries.py
uv run --with-editable packages/trellis-analytics/examples/steps/database_staleness \
    python packages/resource-explorer/scripts/generate_engine_host_declaration.py
```

### Dispatched by Egeria

RE's engine host offers each installed step to Egeria as a service of its `SurveyActionEngine`
`ResourceExplorerSurvey`. The step's declaration (generated from its manifest) is one `SurveyActionService`, one
`SupportedGovernanceService` link carrying the manifest's `request_type`, and one `GovernanceActionType`
`ResourceExplorerSurvey::<request type>` with a `GovernanceActionExecutor` to the engine; the manifest's fields ride
in its Additional Properties. The owner runs it with `resource-explorer dr-egeria run engine-host/library/<step>.md`.
When Egeria starts that type on a database, RE claims the engine action, runs the step, publishes its results as
annotations on a SurveyReport and completes the action. If none of the step's results is `measured` or
`nothing_found`, it completes with the manifest's `nothing_to_derive_guard` (no report unless that is
`survey-completed`). A step is not offered if it takes a built-in RE step's name or request type, declares
`connection`, or emits anything but `ResourceMeasureAnnotation`.

### What a step can and cannot reach

A step is in-process, trusted code. RE's host enforces three things:

- **No connection to the resource.** A step that declares `connection` is refused.
- **Reads only through RE's typed methods.** `inputs.ods` offers `row_count_snapshots`, `commit_proofs`,
  `catalogue_scope_events`, `resource_scope_events` and `outbox_status_counts`, each a parameterised SELECT that RE
  owns. A step cannot send SQL: there is no `query`. Each read runs in a separate thread on a connection that cannot
  write (one statement per rolled-back READ ONLY transaction on Postgres; read-only mode, `query_only` and a read-only
  authorizer on SQLite), and the step holds no connection, registry object or database URL. A step that needs a read
  RE does not offer asks for a new typed method; arbitrary SQL from steps would need a read-only database role, a
  follow-up for the owner (ops/DDL).
- **No Egeria identity.** The step's code cannot act in Egeria; the host publishes its results afterwards.

This narrows what a step is handed; it is **not a sandbox**. The thread list, the call stack and the process
environment still reach Resource Explorer's internals, so install only steps you would run as Resource Explorer itself.

## How it is run

The same routine can run in three places. None counts anything of its own; each shows what the routine returned.

| Host | How |
|---|---|
| **Script** | `python examples/curation_coverage.py <ods-url> <database-slug>` |
| **Notebook** | `examples/curation_coverage.ipynb`, run with papermill and the parameters `slug` and `ods_url`: `papermill examples/curation_coverage.ipynb out.ipynb -p slug coco_pharma -p ods_url <url>`. An empty `ods_url` reads `REGISTRY_DATABASE_URL`. |
| **Portal tile** | `trellis_analytics.portal.curation_coverage_tile`, shown as **Trellis - Curation Coverage** in the Egeria Portal. Load its spec with `PYEGERIA_REPORT_SPEC_MODULES=trellis_analytics.portal:report_specs`. `examples/curation_coverage_tile.md` is the same spec as a Dr.Egeria report-spec document. |

A **step** is run by RE itself: RE's `survey_step_host` attaches installed steps beside the built-in ones, so the
executor runs them the same way and records their cost. The step's annotations are kept, with their envelope, in
RE's own table. When Egeria dispatches the step to RE's engine host, they are also published as annotations on
the action's SurveyReport.

Resource Explorer's Curate page uses `curation_coverage` directly for its **By analysis** view of a database.

## Install and test

Run `pytest` from this directory in an environment with the package's dependencies installed (`pyproject.toml` sets
`pythonpath = ["src"]` and `testpaths = ["tests"]`).

`curation_coverage` counts against RE's own node set, so it needs the optional `re` extra; without it the
result is `not_applicable`, with a reason. The `postgres` extra adds `psycopg2-binary`; `dev` adds papermill
and ipykernel for the notebook test. It needs pyegeria 6.2.3 or later.
