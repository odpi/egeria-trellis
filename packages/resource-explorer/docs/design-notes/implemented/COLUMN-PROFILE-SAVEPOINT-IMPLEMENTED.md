# `postgres_column_profile`'s aborted-transaction cascade — implemented (§F)

**Dispatch:** Section F of the ongoing multi-session brief work, from the
"Resource Explorer expansion architecture" coordinator session, following
directly from a defect found while live-verifying §E
(`PREFECT-PREREQUISITE-RESOLUTION-IMPLEMENTED.md`) and
flagged as background task `task_3ce22015`.

**Branch:** `re/column-profile-savepoint-fix`, off `origin/main` (`9bda025d`,
confirmed unmoved at dispatch). Built in an isolated worktree
(`/Users/dwolfson/localGit/egeria-v6/worktrees/column-profile-fix`), not
stacked on the §E branch, per the coordinator's instructions.

**PR:** not opened by this session — reported to the coordinator, no PR
tonight.

## The defect

`postgres_column_profile` samples column values with `TABLESAMPLE`, one
column at a time, all on the same database connection and the same
(implicit, `autocommit=False`) transaction. `TABLESAMPLE` is only valid
against a plain table or a materialized view — Postgres rejects it outright
on a view or a foreign table. On `laz_local_adventureworks` one such
rejection (a view, `hr.jc` — an abbreviated-schema view mirror this
AdventureWorks load ships alongside the real `humanresources` schema) was a
real SQL error, and `sample_column_values`'s own `try`/`except` caught it
correctly and reported it as not-sampled. But Postgres does not merely fail
the ONE statement that errored — it aborts the WHOLE transaction, and every
subsequent statement on that connection fails too, with "current
transaction is aborted, commands ignored until end of transaction block",
until something issues a `ROLLBACK`. Nothing did. Roughly 40 columns
sampled after the first rejection — across unrelated tables — failed the
identical way, silently (each individually caught and logged), while the
step's own report still said `status: "ok"`.

## Fix

1. **Each query runs inside its own SAVEPOINT.** `DatabaseConnection` gained
   `execute_query_isolated()` — default implementation just calls
   `execute_query` (right for an engine with no such cascading-abort
   behaviour, or one nothing has taught this base class about yet).
   `PostgreSQLConnection` overrides it: `SAVEPOINT` before the query,
   `RELEASE SAVEPOINT` on success, `ROLLBACK TO SAVEPOINT` (never a bare
   `ROLLBACK`, which would undo the whole transaction) on failure, so the
   surrounding transaction is usable again for the very next call.
   `column_profile_step.py::sample_column_values` now calls
   `execute_query_isolated` instead of `execute_query`.
2. **A view or foreign table's columns are never offered to `TABLESAMPLE`
   at all.** `run_column_profile` reads each table's declared `type` (as
   `get_schema_info()` reports it — `information_schema.tables.table_type`,
   or the catalog fallback's own mapping) before sampling. `VIEW`/
   `FOREIGN`/`FOREIGN TABLE` skip sampling entirely and get a new state,
   `registry.STATE_NOT_APPLICABLE` — distinct from `STATE_NOT_COLLECTED`
   ("nothing has read this yet, but could") because there is no version of
   this engine, capability, or budget under which the measurement could
   ever be taken. `MATERIALIZED VIEW` is deliberately NOT in that set — it
   is a real, `TABLESAMPLE`-able relation and is sampled exactly like a
   base table (item 2's own requirement).
3. **The step's own status stops saying "ok" when any column actually
   errored.** `SampleProvenance` gained an `errored: bool` field, set only
   by a genuine query-failure exception (not by the pre-existing, honest
   not-sampled reasons — `catalog_stats_only`, budget exhausted, or the new
   `not_applicable` skip). `run_column_profile` counts `sampled` /
   `skipped_not_applicable` / `errored` per column (from the PRIMARY
   `data_class_match` sample's provenance — the same one `_profile_row`
   already bases its `state` on — so a column that also took a second
   `distinct=True` sample for `reference_data_match` is not double-counted;
   a failure on that SECOND sample still counts toward `errored`/
   `first_error` even when the first one succeeded) and returns
   `status: "ok"` or `"partial"`, plus `counts` and `first_error` (the
   first genuine error's text), on its own output dict.

   **Scoping call, flagged to the coordinator and approved before writing
   any code**: this "partial" status is surfaced at the `run_column_profile`
   surveyor-level return dict (and passes through `_run_postgres_column_
   profile`'s own returned dict unchanged — `DatabaseSurveyor.survey()`
   already assigns `results["column_profile"] = profile_result` verbatim,
   so no extra plumbing was needed there). It is **not** wired into the
   shared dispatch-level `status: "ok"` that `survey_definition_executor.py`
   and `prefect/flows.py::run_planned_step_task` hardcode for every step's
   report entry — that field is used by every step in the codebase, not
   just this one, and changing its semantics is a cross-cutting concern
   this narrow, single-step, non-stacked branch should not absorb tonight.
   The coordinator is carrying this item to `Backlog.md`.

## Live verification

**`laz_local_adventureworks`** (real shared registry, real database,
`_run_postgres_column_profile` called directly with stored credentials):

```
status: ok
counts: {'sampled': 468, 'skipped_not_applicable': 768, 'errored': 0}
first_error: ''
"current transaction is aborted" warnings in the run log: 0   (was ~40)
```

**The "measured exceeds 468" part of the gate does not hold on this
database, and I verified why rather than assume a problem.** Reading the
schema directly: `laz_local_adventureworks` has 87 `VIEW` relations (768
columns total), 68 `BASE TABLE` relations (456 columns), 2
`MATERIALIZED VIEW` relations (12 columns). 456 + 12 = 468 exactly, and 768
is exactly the old "not_collected" count. Every single one of the 768
previously-uncollected columns belongs to a view — this AdventureWorks load
ships abbreviated-schema view mirrors (`hr`, `pe`, `sa`, `pr`, …) alongside
the real schemas, which is what the cascade's victim list in §E's live
verification was actually showing (`hr.jc.resume`, `pe.p.demographics`,
`sa.s.demographics`, `pr.i.diagram`, …: all views, not base tables) — and
every base-table-or-materialized-view column was already being sampled
successfully before this fix, cascade or not. There is no column on this
particular database that the bug was silently costing a real measurement;
its whole blast radius here happened to land entirely on relations that
were never going to be sampled anyway. Forcing a bigger number by sampling
a view (or a different database chosen to make the number move) would
defeat the fix itself. **The aborted-warnings requirement (~40 → 0) is met
exactly and is the load-bearing part of this gate**; the coordinator was
notified of this finding before this doc was written and had not objected
by the time this was pushed.

**`localhost_docker_coco_pharma`** (also real, different failure shape,
confirming isolation under a DIFFERENT kind of error than the view case):

```
status: partial
counts: {'sampled': 32, 'skipped_not_applicable': 18, 'errored': 429}
first_error: 'the sample query failed: permission denied for table coco_locations'
"current transaction is aborted" warnings: 0
```

A real, pre-existing, unrelated credential-scoping gap (this credential can
only read 32 of 479 sampled-or-attempted columns) is now surfaced honestly
as `status: "partial"` instead of the pre-fix behaviour, which would have
silently reported some subset of these as `not_collected` under `status:
"ok"` — masking a much larger visibility gap than the view-only case ever
did. No regression: nothing errors worse than before, and the SAVEPOINT
isolation demonstrably works under a completely different Postgres error
(`permission denied` rather than a rejected `TABLESAMPLE`) without any
special-casing for which kind of error it is.

**This is the better proof of the fix, not a consolation result**: 0
aborted-transaction warnings under a real, substantial error rate (429 of
479 columns) is a harder demonstration of isolation than
`laz_local_adventureworks`'s single-cause, all-views cascade, and the
`status: "partial"` this run now returns is itself a real improvement —
before this fix, this credential's 429-of-479 coverage gap would have been
invisible behind `status: "ok"`.

## Tests

`tests/test_postgres_column_profile.py` (142 → passing, existing +30 new):
- Two existing fake connections (`_FakeSamplingConnection` here and in
  `tests/test_postgres_nested_columns.py`) gained `execute_query_isolated`
  (proxying to `execute_query`, matching the base class's own default) —
  17 pre-existing tests broke on the rename until this was added; fixed,
  not skipped.
- `TestOneColumnsFailureDoesNotCascade` — a new `_FakeIsolatingConnection`
  simulates a connection whose isolation actually works (the contract the
  real SAVEPOINT provides): poisons one column, asserts the NEXT column on
  the same connection still samples successfully, asserts `status` becomes
  `"partial"` with `counts.errored == 1` and `first_error` naming the
  failure, and asserts a run with no poisoned column stays `"ok"`.
- `TestViewsAndMaterializedViews` — a view's column never reaches
  `TABLESAMPLE` at all (asserted against the recorded SQL), gets
  `STATE_NOT_APPLICABLE`, counts as `skipped_not_applicable`, and keeps
  `status: "ok"` (skipping a view is not a failure); a materialized view's
  column IS `TABLESAMPLE`-d and gets `STATE_MEASURED`.
- `TestExecuteQueryIsolatedSavepoints` — `PostgreSQLConnection.
  execute_query_isolated`'s actual SQL shape against a fake cursor (no live
  Postgres in this environment, same technique this file already uses for
  asserting `TABLESAMPLE` shape): success issues `SAVEPOINT` then `RELEASE
  SAVEPOINT`; failure issues `SAVEPOINT` then `ROLLBACK TO SAVEPOINT` (never
  a bare `ROLLBACK`) and re-raises; plain `execute_query` is unchanged
  (issues no savepoint commands at all); "not connected" raises the same
  error both methods already shared.
- `TestExecuteQueryIsolatedDefaultsToExecuteQuery` — the base
  `DatabaseConnection.execute_query_isolated` just calls `execute_query` for
  an engine that hasn't been taught real isolation.

Full suite (`uv run pytest tests/ -q`, run after this change): **6615
passed, 103 skipped, 0 failed** in 794.7s. Not even the three pre-existing,
order-dependent flaky tests tracked from Section E's verification
(`tests/test_survey_definitions_routes.py::TestListCandidates`, unrelated to
this branch's files) showed up this run — consistent with them being
order-dependent rather than deterministic.

## Files touched

- `resource_explorer/registry.py` — `STATE_NOT_APPLICABLE`, added to
  `STATES_WITHOUT_A_MEASUREMENT`.
- `resource_explorer/surveyors/database/connection.py` —
  `DatabaseConnection.execute_query_isolated` (default) and
  `PostgreSQLConnection.execute_query_isolated` (real SAVEPOINT).
- `resource_explorer/surveyors/database/sampling.py` — `SampleProvenance.
  errored`/`not_applicable`; `not_sampled_provenance()` takes both.
- `resource_explorer/surveyors/database/column_profile_step.py` —
  `NOT_SAMPLABLE_TABLE_TYPES`, `STEP_STATUS_OK`/`STEP_STATUS_PARTIAL`; the
  view/foreign-table pre-check; per-column outcome counting; `status`/
  `counts`/`first_error` on the returned dict; `sample_column_values` uses
  `execute_query_isolated`.
- `tests/test_postgres_column_profile.py`,
  `tests/test_postgres_nested_columns.py` — see Tests above.
