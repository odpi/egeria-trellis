# postgres_operations step: implemented

**Replies to:** `COORDINATOR-BRIEF-MULTI-RESOURCE.md`, Phase 1 slice #8 —
*"`postgres_operations` step: privileges, activity signals, resilience,
external dependencies (design §5.5, §5.7)."* Gate: slices 3 (structured
tables, merged as PR #180) and 7 (`postgres_schema_and_stats` extension,
PR #191, **still open as of this writing** — see "Branch and PR stacking"
below).

**Implements:** `multi-resource-questions-design.md` §5.1 (capability
declarations extended for replication/resilience/external-dependencies/
privileges), §5.4 (`db_external_dependencies`, `privilege_audit` row), §5.5
(operational resilience, the `db_resilience` MIXED analysis), §5.7 (the
`postgres_operations` step itself: folds `privilege_audit`,
`db_activity_signals`, `db_resilience`, `db_external_dependencies`; reads
`pg_stat_*`, `pg_roles`, `pg_settings`; produces `ResourceMeasure`,
`ResourcePhysicalStatus`, and an RFA on PUBLIC grants).

**Branch and PR stacking:** `re/postgres-operations-step`, branched from
`re/postgres-schema-stats-extension` (PR #191) rather than from `main`,
because slice 8's gate includes slice 7 and #191 had not merged when this
slice started. This PR is opened against `main` and will therefore show
both #191's diff and this slice's diff until #191 merges — **merge #191
first**, then this one; once #191 lands, this branch's diff against `main`
reduces to slice 8's changes only. If a rebase is needed after #191 merges,
that is a mechanical `git rebase origin/main` on this branch, not a redesign.

**Tests:** full suite green (see below) except one pre-existing, unrelated
failure confirmed present on the base branch before this slice's changes
(see "What could not be tested"). 13 new tests in
`tests/test_postgres_operations_step.py`; two existing tests updated because
they pinned exactly the "not yet implemented"/"aspirational" states this
slice supersedes (`tests/test_postgres_schema_and_stats_extension.py::
TestEngineCapabilitiesDeclaration`, `tests/test_database_surveyor_steps.py::
TestDatabaseAnalysisStepMap`).

---

## What was built

### 1. `EngineCapabilities` gains a fifth boolean: `privileges`

`resource_explorer/surveyors/database/connection.py`'s frozen
`EngineCapabilities` dataclass (added by PR #191) already declared
`replication_status`, `resilience` and `external_dependencies` fields —
correctly left `False` on every engine, since nothing read
`pg_stat_replication`/backup evidence/FDWs yet. This slice is what makes
those three real: `PostgreSQLConnection.capabilities` now declares them
`True`, alongside a new `privileges` field (not part of #191's original
declaration) for `pg_roles`/`role_table_grants`/`pg_default_acl`. Only
`query_stats` (`pg_stat_statements`) stays `False` — nothing in this build
reads it; design §5.1 lists it as a `db_classification` input for a later
slice (Phase 1 slice 9, `db_derived`).

### 2. Six new query methods on `PostgreSQLConnection`

Each follows the same never-raise, empty-on-failure contract as slice 7's
`get_column_stats()`/`get_table_activity()`:

- `get_privilege_audit()` — `pg_roles` (superuser/createrole/createdb/
  login/replication/bypassrls flags), `information_schema.role_table_grants`,
  `pg_default_acl`.
- `get_replication_status()` — `pg_is_in_recovery()`, and `pg_stat_replication`
  (application name, client address, state, sync state, replay lag in
  seconds via `EXTRACT(EPOCH FROM replay_lag)`).
- `get_wal_archiving_status()` — `SHOW archive_mode`, plus `pg_stat_archiver`
  (archived/failed counts, last archived/failed times).
- `get_backup_tool_signals()` — `pg_extension` names matched against known
  backup-tool markers (pgBackRest, wal-g, barman). Explicitly a **partial**
  signal (design §5.5): detecting the extension proves the tooling is
  installed, not that a backup is configured or succeeding.
- `get_clustering_info()` — Citus catalogs (`pg_extension WHERE extname =
  'citus'`) when present. Also explicitly partial — see "Scoped out" below.
- `get_external_dependencies()` — `pg_extension`, `pg_foreign_server` (joined
  to `pg_foreign_data_wrapper`), `pg_foreign_table`, `pg_publication`,
  `pg_subscription`.

### 3. `DatabaseSurveyor._survey_operations()` / `_create_operations_annotations()`

Split the same way slice 7 splits fetch from annotation-building
(`_survey_extended_statistics` vs. the statistics annotation methods):
`_survey_operations(conn, capabilities, schema_info)` does only the fetch,
returning a dict with four keys (`privilege_audit`, `activity_signals`,
`resilience`, `external_dependencies`), each `None` when its capability is
absent — never an empty dict standing in for "not measured".
`_create_operations_annotations(operations_info)` is a **pure function** of
that dict (no `conn` argument), so it can be called from two places:

- `DatabaseSurveyor.survey(steps=["operations"])` — the local/CLI/web path.
- `EgeriaDatabaseSurveyor.publish_step_annotations(..., operations=...)` —
  the Survey-Definition publish path, which now accepts an `operations`
  parameter alongside `views` and folds its annotations in the same way.

`db_activity_signals` reuses slice 7's `get_table_activity()`/
`get_stats_reset()` directly rather than querying `pg_stat_user_tables` a
second way — verified by construction in
`TestDbActivitySignalsReusesSliceSevenData.test_rolls_up_table_activity_
without_requerying`, which asserts the surveyor's stored activity rows are
byte-identical to what the fake connection returned.

### 4. A new step, opt-in rather than folded into the default survey

`DatabaseSurveyor._ALL_STEPS` stays `("schema", "statistics", "views")` —
`"operations"` is deliberately **not** added to it. Design §5.7 prices
`postgres_operations` at "api / low" cost, separate from
`postgres_schema_and_stats`'s own cost; folding it into the default
`survey()` call would make every existing full survey silently pay for six
new queries. `"operations"` only runs when explicitly requested:

- `DATABASE_ANALYSIS_STEP_MAP` now maps `privilege_audit`,
  `db_activity_signals`, `db_resilience` and `db_external_dependencies` each
  to `["schema", "operations"]` — `privilege_audit`'s old mapping
  (`["schema", "statistics", "views"]`, the full survey, because it had no
  dedicated check) is retired.
- A new adapter entry point, `_run_postgres_operations` in
  `survey_definition_adapter.py`, registers `"postgres_operations"` as a
  `re_analysis_step` calling `DatabaseSurveyor.survey(steps=["operations"])`
  — the Survey-Definition-driven path design §5.7's table describes.

`"schema"` still always runs alongside `"operations"` (the pre-existing
`DatabaseSurveyor` invariant — every step besides `"schema"` reads from
`schema_info`), which is where `db_activity_signals`' table-count context
(used to distinguish "no tables" from "tables exist, stats not yet
accumulated" — see the absence-discipline section below) comes from.

### 5. `privilege_audit` gets its real, dedicated check

The coordinator brief's own Phase 0 note — *"privilege_audit has no
dedicated check today (confirmed — database-only, aspirational)"* — is now
false. It reads `pg_roles`/`role_table_grants`/`pg_default_acl` for real and
raises a `RequestForActionAnnotation` per table carrying a PUBLIC grant
(grouped by table, listing every PUBLIC-granted privilege on it in one
item), never firing when there are none.
`analysis_catalog.yaml`'s `privilege_audit` entry, `annotation_types` and
description were updated to match (was `["DataClassAnnotation",
"RequestForActionAnnotation"]`, describing work the step never did; now
`["ResourceMeasureAnnotation", "RequestForActionAnnotation"]`).

### 6. A new annotation type: `ResourcePhysicalStatusAnnotation`

`db_resilience` is the first RE analysis to produce Egeria's real
`ResourcePhysicalStatusAnnotationProperties` type — one of the twelve types
`docs/egeria-integration.md` §2 flagged as unused, and the task description
for this slice named it by name in design §5.7's own table
("Produces ResourceMeasure, ResourcePhysicalStatus, RFA on PUBLIC grants"),
so it did not yet exist in `survey_report.py` and had to be added:

- `AnnotationType.RESOURCE_PHYSICAL_STATUS = "ResourcePhysicalStatusAnnotation"`
- `ResourcePhysicalStatusAnnotation` dataclass, carrying a generic
  `physical_properties: dict` bag rather than Egeria's real fixed fields
  (`resourceCreateTime`, `resourceUpdateTime`, `resourceLastAccessedTime`,
  `size`, `encodingType`) — those describe a filesystem-shaped resource and
  do not fit this finding (replication role, replay lag, archiving status,
  backup/clustering signals). A future filesystem use of this same type
  (design §6, Phase 2) is the one expected to populate the four named
  fields for real.
- `ANNOTATION_TYPES_REGISTRY` entry, and `annotation_props.py`'s
  `build_annotation_props` maps it to `ResourcePhysicalStatusAnnotationProperties`
  with `physical_properties` published under `additionalProperties` (the
  same fallback convention already used for any not-yet-natively-typed
  field on a registered subtype) rather than fabricating values for the
  real type's fixed fields.

This is a genuinely new, cross-cutting addition — not scoped to database
surveying alone — so it is called out here explicitly rather than buried in
the `db_resilience` section above.

---

## Absence discipline — the states, and how each is tested

Every one of the four folded analyses distinguishes "this connection's
capability declaration says the engine can't do this" (STATE_NOT_SUPPORTED
in registry.py's vocabulary) from "measured, and there was nothing" —
matching slice 7's pattern exactly (a `ResourceMeasureAnnotation` at
confidence 0, `resource_properties={"capability": ..., "supported": False}`)
for every section:

| Section | Capability-absent | Genuinely measured, real "none" | Extra absence mode |
|---|---|---|---|
| `privilege_audit` | `privileges=False` → confidence-0 measure, no RFA | 0 roles/grants is a real answer | — |
| `db_activity_signals` | `tuple_counters=False` → confidence-0 measure | 0 tables in the catalog *and* 0 activity rows is real | tables exist in the catalog but `pg_stat_user_tables` has no row for any of them yet ("not yet accumulated", confidence 0) — distinguished using the table count carried alongside the activity rows, same "run ANALYZE"-shaped reasoning slice 7 applies per-column, applied database-wide here |
| `db_resilience` | `resilience=False` → confidence-0 measure, no `ResourcePhysicalStatusAnnotation` at all | **standalone, no replicas** (`pg_is_in_recovery()=false`, zero rows in `pg_stat_replication`) is a real, positive finding — rendered at confidence 100 with `role: "standalone"`, not as an error or an omission | `is_in_recovery` itself unreadable → `role: "unknown"`, confidence 0 |
| `db_external_dependencies` | `external_dependencies=False` → confidence-0 measure | 0 extensions/FDWs/publications is real | `pg_subscription`'s per-item permission case — see "Scoped out" |

`db_resilience`'s MIXED envelope (design §5.5: "the envelope must say which
half answered") is carried on the single `ResourcePhysicalStatusAnnotation`
itself: `physical_properties` includes `last_backup_date: null`,
`restore_test_date: null` and an explicit `enrichment_pending: [...]` list
naming exactly which facts are not machine-observable, and `explanation`
states in prose which half is catalog-measured and which is genuinely
pending — never silently omitted, never rendered as zero.
`TestDbResilienceThreeAbsenceStates` in
`tests/test_postgres_operations_step.py` covers all three states named in
the task (capability-absent, standalone-positive, replica-with-lag), plus a
fourth test asserting the MIXED envelope's wording is present.

---

## Scoped out

- **Patroni-via-REST clustering detection.** Design §5.5 marks this
  "partly" observable, via a *live REST call* to a Patroni instance — a
  different class of dependency than a catalog read (a reachable HTTP
  endpoint, a different credential, a different failure mode) and a bigger
  scope-of-fetch decision than this slice's connection-layer catalog reads.
  `get_clustering_info()` covers Citus only (a real Postgres extension,
  visible from `pg_extension`) and is explicit in its docstring that
  Patroni and managed-service HA are out of scope here — logged to
  `docs/Backlog.md` below rather than half-built.
- **`pg_subscription` per-item absence.** `get_external_dependencies()`
  swallows a permission error on `pg_subscription` (superuser/owner-only on
  the subscriber database) to an empty list via the same try/except as
  every other read in that method — so "no subscriptions" and "not
  permitted to see pg_subscription" are not distinguished per-item, only at
  the whole-`external_dependencies`-capability level. A finer per-item
  state would need its own capability flag or exception-type
  discrimination; not attempted here, noted for a future pass.
- **`db_classification`/`db_derived`** (Phase 1 slice 9) — reads
  `pg_stat_statements` (`query_stats` capability, still `False`) and does
  its own zero-fetch reasoning over what this and slice 7 stored. Not this
  slice's job.
- **`data_class_match` × `privilege_audit`** (design §5.4/§5.6's sensitive-
  exposure composite) — needs column-level data classification, which is
  Phase 1 slice 10's job. `privilege_audit` here produces the privilege
  half only.

---

## A generated file needed regenerating, not hand-editing

Adding `db_activity_signals`/`db_resilience`/`db_external_dependencies` to
`analysis_catalog.yaml` made `resource_explorer/configdata/question_catalog.yaml`
stale: `scripts/csv_to_question_catalog_yaml.py` resolves each CSV row's
"Answering Analysis" free text against the *live* set of ids in
`analysis_catalog.yaml`, and `docs/dr-egeria/resource_questions.csv` (stream
4's authored content, not touched here) already named these three ids in
its `GAP: <id> (proposed) — ...` notes, anticipating this slice. Once the
ids existed, `test_question_catalog_generator_guard.py` and
`test_question_catalog_multi_type.py` correctly failed — the committed YAML
no longer matched what the CSV generates. Per that guard test's own error
message ("never the YAML by hand"), the fix was to re-run the generator:

```
python scripts/csv_to_question_catalog_yaml.py docs/dr-egeria/resource_questions.csv \
    --output resource_explorer/configdata/question_catalog.yaml
```

This is a mechanical regeneration of a derived file (both source
files — the CSV and `analysis_catalog.yaml` — are unchanged by it), not a
hand-edit of authored question content, so it does not cross the "don't
touch the question catalog files" boundary this slice otherwise respects;
`docs/dr-egeria/resource_questions.csv` itself was not edited.

**Left as-is, not fixed here:** the regenerated rows' `note` text still
reads `'GAP: db_activity_signals (proposed) — ...'` even though
`analysis_ids` is now populated — the CSV's own prose (`kind: gap`, "GAP:
... (proposed)", "none of these catalogs is read today") is now stale
relative to the analysis existing, but rewording it is the CSV owner's
call (stream 4), not this slice's to make unilaterally. Flagged here for
whoever next edits that CSV, and in `docs/Backlog.md`.

## What could not be tested

No live Postgres is available in this environment — same limitation slice
7's own write-up notes. Every new query method
(`get_privilege_audit`/`get_replication_status`/`get_wal_archiving_status`/
`get_backup_tool_signals`/`get_clustering_info`/`get_external_dependencies`)
is exercised only through `tests/test_postgres_operations_step.py`'s
duck-typed `_FakeOpsConnection`, which characterizes
`DatabaseSurveyor._survey_operations`/`_create_operations_annotations`'s
logic end to end but cannot confirm the six SQL statements themselves parse
and return the expected shape against a real server (e.g. that
`EXTRACT(EPOCH FROM replay_lag)` behaves as expected against an actual
`pg_stat_replication` row, or that `pg_default_acl`'s join is correct
against a database with real default ACLs configured). A follow-up against
a reachable Postgres primary/replica pair (or `coco_ods`, once step 6's
re-cataloguing work applies) would close this gap — logged to
`docs/Backlog.md`.

**Full test suite:** green except one pre-existing, unrelated failure —
`tests/test_egeria_live_smoke.py::TestAuthoredDefinitionsMatchTheirSource::
test_definition_exists_and_matches_the_csv[RepoAnalysisSurvey]` — confirmed
present on the base branch (`re/postgres-schema-stats-extension`, before any
of this slice's changes) by stashing this slice's diff and re-running that
one test in isolation. It needs a live Egeria/network resource this
environment does not have and is unrelated to database surveying.
