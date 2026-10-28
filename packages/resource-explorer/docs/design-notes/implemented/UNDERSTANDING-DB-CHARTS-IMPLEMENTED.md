# Understanding for databases: backend chart routes rebuilt on structured rows

Slice: REPLY-DESIGNER-CURATE-AND-UNDERSTANDING-ALL-KINDS.md, "Two things to fix
before anything is drawn" item 2 and section 3/4. Backend only; no /next UI.

Module resolved to the worktree before any test:
`/Users/dwolfson/localGit/egeria-v6/trellis-re-understanding-db-charts/packages/resource-explorer/resource_explorer/__init__.py`

Tests ran with `PGVECTOR_PORT=1` and `REGISTRY_DATABASE_URL` pointing at a temp
SQLite file; the test registries are per-test SQLite files. Shared Postgres
(localhost:5442) was never contacted.

## Where the code is

- `resource_explorer/db_chart_data.py` (new): all logic. Routes in
  `web/routes/stats.py` are thin wrappers.
- `section_state()` there is the logic `get_database_diff._tables_for` carried
  inline; the diff route now calls it, so diff and charts cannot disagree about
  what a run measured. Diff behaviour is unchanged (existing + new diff tests).
- Reads `database_tables` / `database_columns` / `database_schemas` plus
  `database_survey_coverage`. Never reads `survey_data`, and no longer loads
  every historical blob (`get_database_surveys` fetched all of them).

## State values

`state` is one of:

- `measured`: measured, nothing caveated.
- `partial`: measured with a stated limit. `reasons` lists which:
  `credential_scope`, `row_counts_not_established`, `sizes_not_established`,
  `type_not_recorded`, `columns_<state>`, `runs_without_table_measurement`,
  `row_counts_missing_in_some_runs`.
- `not_measured`: not measured. `reasons` carries the coverage state
  (`never_surveyed`, `not_materialized`, `not_permitted`, `not_collected`,
  `not_supported`, `not_measured`, ...) or `row_counts_not_established` /
  `no_run_measured_tables`. Never zeros.

A section that ran and found nothing (coverage `empty`) is `measured` with zero:
the only case that licenses "none".

Every response: `database`, `run` ({run_id, surveyed_at, source, surveyed_as}
or null), `state`, `reasons`, `notes`, `scope`, `figure` (Plotly {data,layout}
or null when nothing to draw).

`scope` is the credential-scope mark: `{checked, partial, fraction,
schema_fraction, connected_as, by_container{schema:{state,...}}, probe_note}`.
It reuses `_credential_scope_status`.

## Old vs new, per route

All old fields are kept (additive) except where noted.

### GET /api/stats/databases/{slug}/schema_distribution
- Old: `{schemas, table_counts, column_counts}` from the blob; 404 if never
  surveyed; `{}`-shaped empties otherwise.
- New: same three arrays (from rows; `column_counts` entries are `null`, not 0,
  when the run did not measure columns, and the state is then `partial`) plus
  `schema_scope` (per-schema `structure_only` etc. when the probe says so),
  envelope fields, and a two-panel figure (tables | columns, not one grouped
  chart).
- Changed: never-surveyed is now 200 + `not_measured`, not 404.

### GET /api/stats/databases/{slug}/table_sizes?limit=&measure=rows|size
- Old: `{tables, row_counts, sizes_mb}`; `row_count` defaulted to 0; unestablished
  tables ranked as 0 and fell out of the top N silently.
- New: same three arrays, aligned, ranked by the chosen `measure` among tables
  where it is established (`measure` defaults to `rows` and never switches on its
  own; `size` is an explicit second view). Plus `measure`, `ranked_count`,
  `truncated_by_limit`, `table_count_total`, `views_excluded`,
  `not_established_count`, `not_established_tables` (named), `provenance`.
  Mixed counts: state `partial`, reason `row_counts_not_established`. None
  established: state `not_measured` (not an all-zero chart).
- Decision made here (brief silent): views are excluded from the row-count
  ranking and reported in `views_excluded`, not counted as "not established",
  because a view has no row count to establish.

### GET /api/stats/databases/{slug}/column_types
- Old: `{types, counts}`; missing type bucketed as `"unknown"`.
- New: same arrays over real types only; the missing-type count is its own field
  `type_not_recorded` (NULL and '' both), `column_count_total`, state `partial`
  with reason `type_not_recorded` when any. A type literally named "unknown" is
  still a real type.

### GET /api/stats/databases/{slug}/survey_history?limit=
- Old: `{dates (day only), schema_counts, table_counts, column_counts}` from the
  summary columns, defaulting to 0.
- New: `points[]` one per run, oldest first: `{run, schema_count, table_count,
  column_count, states{schemas,tables,columns}}`; legacy arrays are kept but
  `dates` are full timestamps (two runs on one day = two points) and unmeasured
  counts are `null` (a gap), never 0. `diff_route` points at
  `/api/databases/{slug}/diff`. A measured-and-empty run is a real 0.
- The run id is the survey row id; `database_surveys` rows are keyed by
  (surveyed_at, source), which is what the structured rows join on.

### New, cheap, for the owner's wishes: GET /api/stats/databases/{slug}/table_growth?top=&limit=
Established row count per run for the latest run's `top` largest tables
(`runs[]`, `series[{table,row_counts[]}]`, `null` where not established or the
table did not exist), plus figure. Same envelope.

## Consumers

- /next `understanding.js`: does not call these routes (its note says so; every
  database tile says "not wired up yet"). Nothing to update. It is the UI slice.
- Classic `index.html` (`showDbChart`/`_renderDbChart`, `loadDbSurveyHistoryChart`):
  old fields still present. One behaviour would have regressed: when no row count
  is established the route now returns no ranked tables where Classic used to
  receive zeros and switch to size. Updated: Classic refetches
  `table_sizes?measure=size` in that case (pinned by a test that reads the
  source). Classic's mixed-counts view now lists only established tables, with
  no sentence about the rest; the sentence is for the UI slice (data is in
  `not_established_count`). Survey history: `null` y-values draw as gaps in
  Plotly; dates are full timestamps and render on a date axis.

## Evidence

Tests: `tests/test_understanding_db_charts.py` (26 tests). Against the
pre-change `stats.py` (origin/main) 23 of 26 fail; the 3 that pass are the diff
route (unchanged by design) and two that do not touch the old shapes. With the
change, 26 pass; with `test_db_fs_structured_tables.py`,
`test_understanding_chart_honesty.py`, `test_next_understanding_pane.py` and
`test_database_survey_history_invalid_view.py`, 102 pass.
Known negatives: a real zero stays zero; a type literally named "unknown" stays a
type; a measured-empty run is a real 0; views are not "unestablished".

## Could not verify

- Nothing ran against a live database or the shared Postgres (forbidden here).
  All fixtures are hand-built SQLite rows, not a real survey's output.
- The Classic JS edit is checked by a source-text test, not in a browser.
- Credential scope: the probe is the latest on record, which may not be the run
  the chart reads; the response says so (`probe_note`) but cannot say which run.
- Postgres dialect: SQL is plain GROUP BY/COUNT through the registry wrapper,
  exercised on SQLite only.
- Legacy runs never back-filled show `not_materialized` / null in history even
  though their summary columns hold numbers: deliberate (those columns default 0,
  and that is the defect), but it means an un-backfilled database has an empty
  history line until `scripts/backfill_structured_tables.py` runs.

## Missing for the owner's wishes

- Growth in rows per table: data served by `table_growth`. Missing: a UI;
  per-run `size_bytes` series; growth-rate (delta/day) fields; the separate
  `db_change_rates` activity series (section 3 chart 6) is not touched here.
- Schema changes between runs: only the existing diff route, which compares the
  latest two runs and returns table names. Missing: arbitrary run pair; added /
  removed columns and schemas (names); per-run-pair change history.
