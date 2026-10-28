# postgres_schema_and_stats extension: implemented

**Replies to:** `COORDINATOR-BRIEF-MULTI-RESOURCE.md`, Phase 1 slice #7 —
*"Extend `postgres_schema_and_stats` to read `pg_stats`, `pg_stat_user_tables`
tuple counters, indexes; add the engine capability declaration on
`DatabaseConnection` (design §5.1)."*

**Implements:** `multi-resource-questions-design.md` §5.1 (capability
declaration, catalog-first extraction, the two `pg_stats` absence modes) and
§5.7 (the `postgres_schema_and_stats` step's extended scope: `SchemaAnalysis`,
`ResourceMeasure`, and column profiling).

**Branch:** `re/postgres-schema-stats-extension`, worktree
`.claude/worktrees/agent-a0b73d92170885e2d`.

**Tests:** full suite green (see below); 11 new tests in
`tests/test_postgres_schema_and_stats_extension.py`, plus one existing test
fixture (`tests/test_database_surveyor_steps.py`) updated to declare
capabilities explicitly rather than rely on `MagicMock` auto-attributes.

---

## What was built

### 1. `EngineCapabilities` on `DatabaseConnection`

`resource_explorer/surveyors/database/connection.py` gains a frozen
`EngineCapabilities` dataclass with seven boolean fields — `column_stats`,
`tuple_counters`, `index_stats`, `replication_status`, `query_stats`,
`resilience`, `external_dependencies` — matching design §5.1's `supports: {…}`
wording. `DatabaseConnection.capabilities` is a non-abstract property
defaulting to `NO_CAPABILITIES` (all `False`); `PostgreSQLConnection`
overrides it to declare `column_stats=True, tuple_counters=True,
index_stats=True`.

The other four capabilities stay `False` on Postgres too, deliberately. This
build reads none of `pg_stat_replication`, `pg_stat_statements`, backup
evidence or FDWs/publications — those are Phase 1 slices 8/9's job
(`postgres_operations`, `db_derived`). Declaring them `True` ahead of any code
that reads them would recreate the exact bug this field exists to prevent:
"not yet implemented" rendering identically to "measured, and there was
nothing".

### 2. Three new query methods on `PostgreSQLConnection`

- `get_column_stats()` — per-column `pg_stats`: `null_frac`, `n_distinct`,
  `avg_width`, `correlation`, `most_common_vals`, `most_common_freqs`,
  `histogram_bounds` (as text, parsed by the caller — see below).
- `get_table_activity()` — per-table `pg_stat_user_tables`: tuple counters
  (`n_tup_ins/upd/del/hot_upd`), `n_live_tup`/`n_dead_tup`, `seq_scan`/
  `idx_scan`, and all four vacuum/analyze timestamps separately (not
  `GREATEST()`-collapsed, unlike the pre-existing `_get_table_row_stats()`
  that only feeds the schema-info display).
- `get_index_stats()` — `pg_stat_user_indexes` joined to `pg_index` for
  `idx_scan`/`idx_tup_read`/`idx_tup_fetch`, `is_unique`, `is_primary`, and
  index size.
- `get_stats_reset()` — `pg_stat_database.stats_reset` for the current
  database, so a change comparator (design §9.1, Phase 1 slice 14) can tell a
  real rate from the negative delta a counter reset produces.

All four are additive to `get_statistics()`'s returned dict (`column_stats`,
`table_activity`, `index_stats`, `stats_reset` keys) — the existing
`database_size`/`table_stats`/`row_stats` keys and their consumers are
untouched.

### 3. `DatabaseSurveyor._survey_extended_statistics()`

New method, called from the existing `"statistics"` step (no new step key —
design §5.7 assigns this scope to `postgres_schema_and_stats` itself, not a
separate step). It:

- Walks the column/table catalog from `schema_info` (not the raw query
  results) so a column or table the catalog knows about but a statistics
  query has no row for is recognised as *absent from statistics*, not simply
  missing from an output list.
- For each table: if `capabilities.tuple_counters`, looks up its
  `pg_stat_user_tables` row; present → `STATE_MEASURED` row with the fields
  above; absent → `STATE_NOT_COLLECTED` (a real but rare Postgres state: the
  statistics collector has not yet accumulated a row for a brand-new table).
  If the capability itself is absent → `STATE_NOT_SUPPORTED`, and the
  `pg_stat_user_tables` query is not even attempted for that connection.
- For each column: the same three-way split against `pg_stats`, with
  `STATE_NOT_COLLECTED` meaning "`ANALYZE` has never run on this table" —
  design §5.1's second absence mode, named explicitly so it cannot be read as
  "no values".
- For indexes: one `ResourceMeasureAnnotation` per index when
  `capabilities.index_stats`; `idx_scan == 0` on a **non-primary-key** index
  additionally raises a `RequestForActionAnnotation` "unused index candidate"
  (a primary key with zero scans is not flagged — PKs are frequently
  maintained for uniqueness rather than queried directly, and flagging every
  quiet one would be noise, not a finding).
- Emits one honest `ResourceMeasureAnnotation` (confidence 0, explanation
  naming the missing capability) per absent capability, so a database on a
  future non-Postgres engine reads "not established" rather than silence.

Rows are written to the existing `database_column_profiles` and
`database_table_activity` structured tables (already created by stream 3,
`re/db-fs-structured-tables` — their columns already matched this slice's
needs exactly; `DB-FS-STRUCTURED-TABLES-IMPLEMENTED.md`'s own "Scoped out"
section names this as the follow-up: *"The columns are here and empty;
nothing in this branch populates them."*) via
`ProjectRegistry.write_detail_rows()`, the same generic path
`result_materializer.py` uses for native-survey read-back. No new table was
created.

### 4. A real bug found and fixed: two write paths, two timestamps

`ProjectRegistry.record_database_survey()` already calls
`result_materializer.backfill_database_survey()` internally, writing a
*thinner* `database_table_activity` row (only `last_vacuum`/`last_analyze`/
`pending_changes`, from the enriched `schema_info` blob) and a
`STATE_NOT_MEASURED` placeholder for `database_column_profiles`. Both used a
`surveyed_at` generated fresh inside `record_database_survey()`, independent
of the timestamp `DatabaseSurveyor.survey()` had already stamped on its own
`results` dict.

`DatabaseSurveyor._store_results()` calls `record_database_survey()` *and*
this slice's own richer write to the same two tables — with two different
timestamps, `query_detail_rows()`'s "most recent run wins" lookup could and,
in this slice's own tests, silently did pick whichever write happened to
generate the later ISO string, which was the older/thinner backfill row, not
this slice's real tuple counters. A confident-looking `database_table_activity`
row with real-looking `pending_changes=0` and everything else `None` was the
symptom — exactly the "measured, and there was nothing" collapse the
`find-absence-as-answer` skill exists to catch, produced by a timestamp race
rather than by a missing state check.

Fixed narrowly: `record_database_survey()` gained an optional `surveyed_at`
parameter (defaults to "now", unchanged for every other caller), and
`DatabaseSurveyor._store_results()` now passes its own `results["surveyed_at"]`
explicitly. Both writes for one survey run land under the same
`(slug, surveyed_at, source)` key, and — because this slice's write runs
second — its richer rows correctly supersede the backfill's placeholder ones,
per `write_detail_rows()`'s documented delete-then-insert-per-key semantics.
This is a one-parameter addition to a method other streams also call, not a
restructuring of `registry.py`'s owned DDL/write-path section.

---

## Absence discipline — the three states, and how each is tested

| State | Meaning | Test |
|---|---|---|
| `STATE_MEASURED` | Real value from `pg_stats`/`pg_stat_user_tables`/`pg_stat_user_indexes` | `TestNormalCase` |
| `STATE_NOT_COLLECTED` | In the catalog, but `ANALYZE` has never populated a `pg_stats` row for this column (or the stats collector has no row yet for this table) | `TestStatsNeverCollected` (both "never collected" and "distinct from a real measured zero") |
| `STATE_NOT_SUPPORTED` | The connection's `EngineCapabilities` declaration says this engine cannot provide it at all | `TestCapabilityNotSupported` |

A fourth combination — index usage, used vs. unused — is `TestIndexUsageDetection`:
a scanned index produces a plain measurement and no RFA; an unscanned
non-primary index raises one; an unscanned primary key does not (see above);
and an absent `index_stats` capability produces the same honest
"not established" annotation as the other two capabilities, with zero
per-index annotations (the surveyor does not fabricate index data it was
told the engine cannot supply).

---

## Scoped out

- **No `database_indexes` structured table.** Design §5.1's table lists
  indexes as part of this step's catalog sources, but the ten tables stream 3
  built (`DB-FS-STRUCTURED-TABLES-IMPLEMENTED.md`) do not include one, and the
  brief for this slice is explicit: *"do NOT invent a new table … or add a
  narrowly-scoped new method if none exists for these specific fields."*
  Index findings are surfaced as `ResourceMeasureAnnotation`/
  `RequestForActionAnnotation` only, not as queryable rows. Logged to
  `docs/Backlog.md` as a candidate for a future slice (a `database_indexes`
  table, or folding index summary counts onto `database_tables`).
- **`ResourceProfile` as its own annotation type.** Design §5.7 lists
  `SchemaAnalysis, ResourceMeasure (all four Relational*Metric sets),
  ResourceProfile (frequent values)` as this step's output. RE's
  `AnnotationType` enum (`survey_report.py`) has no `RESOURCE_PROFILE`
  member — only Egeria's own native survey has a distinct "frequent values"
  annotation shape (`ANN_COLUMN_VALUES` in `result_materializer.py`).
  Frequent values are carried in this slice as `resource_properties` on the
  same `ResourceMeasureAnnotation` used for the rest of a column's profile,
  which is a real but honest scope reduction rather than a silent
  substitution — adding a new `AnnotationType` member is a larger, catalog-
  and-publisher-wide decision this slice's Sonnet scope does not extend to.
- **`RelationalDatabaseMetric`/`RelationalSchemaMetric` key-name parity.**
  `result_materializer.py`'s docstring names these as the vocabulary a
  local publish should eventually share with Egeria's native
  `PostgresDatabaseStatsExtractor.java`. This slice's annotations use
  RE's own descriptive `resource_properties` keys (`null_frac`, `n_distinct`,
  `idx_scan`, …) rather than the native metric key names
  (`numberOfDistinctValues`, etc.) — full parity would mean every existing
  local annotation across this file adopting the native vocabulary at once,
  which is a larger, cross-cutting change than "extend one step's scope"
  and was judged out of bounds for a Sonnet-scoped slice. Flagged for the
  coordinator to decide whether a parity pass belongs to a later slice.
- **Column/table catalog writes.** `database_schemas`/`database_tables`/
  `database_columns` are still populated only by the backfill and native-
  survey paths, not by a live local survey — this slice only extends what
  the *statistics* half of `postgres_schema_and_stats` writes
  (`database_column_profiles`, `database_table_activity`), matching exactly
  what `DB-FS-STRUCTURED-TABLES-IMPLEMENTED.md`'s "Scoped out" section named
  as this slice's job. Wiring the schema/table/column catalog itself into the
  live path is a separate, larger change and was left alone.
- **`postgres_operations`'s capabilities** (`replication_status`,
  `query_stats`, `resilience`, `external_dependencies`) are declared but
  `False` everywhere, as described above — Phase 1 slices 8/9's job.

## What could not be tested

- **Anything against a live Postgres.** All tests use a duck-typed
  `_FakeConnection` standing in for `PostgreSQLConnection`, configured with
  hand-built `pg_stats`/`pg_stat_user_tables`/`pg_stat_user_indexes`-shaped
  dicts rather than a real query result. The four new SQL queries
  (`get_column_stats`, `get_table_activity`, `get_index_stats`,
  `get_stats_reset`) were written against Postgres's documented catalog view
  columns but were not run against a live database — the coordinator brief's
  live-Postgres re-catalogue-and-probe work is Phase 1 slice 6, and this
  slice's own gate is "3" (the structured-tables stream), not a live
  platform. Recommend a live smoke test against `coco_ods` (already
  re-cataloged per slice 6, if that has landed) before this is relied on for
  a real column-profiling answer.
- **A real ANALYZE-never-run table on live Postgres.** The "stats never
  collected" test constructs the absence directly (an empty `column_stats`
  list for a cataloged column) rather than observing it by creating a real
  unanalyzed table and reading `pg_stats` against it. The SQL query itself —
  `SELECT … FROM pg_stats WHERE …` simply returning no row for an unanalyzed
  column — is standard, well-documented Postgres behavior, but was not
  independently reproduced here.
- **A genuinely different (non-Postgres) engine's capability declaration.**
  `EngineCapabilities(index_stats=False, …)` is exercised directly on
  `_FakeConnection`, not through a second real `DatabaseConnection`
  subclass — none exists in this codebase yet (DuckDB is design §5.1's
  named second engine, not yet implemented).
