# Enumeration floor + collector honesty — implemented

**Coordinator brief:** Phase 1b, next after slice 17c
(`re/coordinator-brief-phase-1b`).
**Replying to:** the Decision recorded in
`SLICE-17B-RENDER-BOUND-LEVEL-GATE-IMPLEMENTED.md` (design
session, 2026-09-26, citing security-model.md §2.1/§3.4), and the
collector-honesty ruling recorded in
`SLICE-17C-RENDERABLE-ANSWER-GATE-IMPLEMENTED.md`'s "Live
gate follow-ups" section (same date).
**PR:** #TBD (`re/enumeration-floor-and-collector-honesty`).

## Part 1: the enumeration floor

### What was wrong

`coco_pharma`'s credential banner and its size answer disagreed on totals
(61 vs. 56 tables) because `get_schema_info()` enumerated schemas from
`information_schema.schemata` — privilege-filtered by Postgres to schemas
the connected role owns or holds *any* grant on. A schema with zero
privilege at all (not even `USAGE`) never appears there, so its tables
were never even attempted, not merely under-counted. `get_credential_capability()`'s
own docstring already documents hitting and fixing the identical gap for
itself (an 8-vs-6 schema undercount) by reading `pg_namespace` directly
instead — a fix that was never carried back to `get_schema_info()`'s own
enumeration.

### The fix

1. **`PostgreSQLConnection._enumerate_relations()`** (new) — the single
   unprivileged floor read (`pg_namespace`/`pg_class`, exactly the queries
   `get_credential_capability()` already used), now shared by both
   consumers. `get_credential_capability()` was refactored to call it
   rather than duplicating the same two queries.
2. **`get_schema_info()`** now runs a second pass after its existing
   privileged enumeration: any schema the floor sees that the privileged
   list missed entirely gets its tables read via the SAME machinery
   `_catalog_only_fallback()` already uses to recover a table
   `information_schema` couldn't see *within* an already-known schema
   (`_catalog_table_summary()` + `_catalog_columns_for_table()` — both
   pure `pg_class`/`pg_attribute` catalog-metadata reads, not
   privilege-filtered). Each such table is tagged `source:
   "catalog_fallback"`, the marker `database_rows_from_survey_data()`
   (`result_materializer.py`) already recognizes and stores as
   `STATE_CATALOG_ESTIMATE` — no downstream change needed for per-table
   honesty. The schema itself is tagged `access: "no_usage"` — not yet
   rendered anywhere (no per-schema view exists until slice 22), but
   present so that view can tell the two cases apart without re-deriving
   it.
3. **`_schema_inventory_results`** (`survey_definition_adapter.py`) now
   reports `base_table_count`/`view_count`/`materialized_view_count`/
   `foreign_table_count` alongside the unchanged `table_count` — relation
   kinds named separately, never blended into one count.
   `base_table_count` is the field kept stable for whatever already reads
   `table_count` as if it meant "ordinary tables only"; `table_count`
   itself keeps its original meaning (every relation kind combined), so
   neither meaning silently changed under an existing caller.

### Live verification (`coco_pharma`, `localhost_docker_coco_pharma`)

Before: `get_schema_info()` → 6 schemas, 56 tables (missing `demo`,
`demo_auth` entirely). `get_credential_capability()` → 8 schemas, 61
tables.

After: `get_schema_info()` → 8 schemas, 61 tables — `demo` (1 table) and
`demo_auth` (4 tables) present with `access: "no_usage"` and their real
table names/kinds. Totals now agree with `get_credential_capability()`'s
by construction (both read the same floor).

### Explicitly NOT done here

- **No per-schema UI** renders the `access: "no_usage"` marker yet — that
  is slice 22's own scope (the schema/table listing view). This PR only
  ensures the data reaches storage rather than vanishing; how it's
  presented is deliberately out of scope.
- **`database_rows_from_survey_data`'s schema-level `state` field** stays
  `STATE_MEASURED` uniformly for every schema, including a new
  `no_usage` one — a pre-existing simplification (schema-level state was
  never differentiated before this PR either), not made worse by this
  change. The TABLE-level honesty (`STATE_CATALOG_ESTIMATE` via the
  existing `source: "catalog_fallback"` marker) is what actually carries
  the caveat through to `_schema_inventory_results`.
- **A real survey re-run** is needed before `coco_pharma`'s stored detail
  rows reflect the new totals — this PR fixes the collector, not the
  already-stored data from before the fix (same caveat slice 17c's
  `privilege_audit` fix carried).

## Part 2: collector honesty

### What was wrong, generalized

The design session's own framing: `get_privilege_audit()`'s roles bug
(slice 17c) was invisible for weeks not because it was a rare edge case,
but because the collector's own `try/except Exception: roles = []` turned
a genuine exception into an empty result, indistinguishable from a real
empty one. That specific field got a reader-side floor (an empty `roles`
list can never be a legitimate zero), but the same failure mode can
recur invisibly in any OTHER collector where the empty default IS
sometimes legitimate — the floor has nothing to grab onto there.

**Ruling:** a collector that catches an exception records it on its own
section (`_errors`), never only the empty default; a reader renders a
recorded error as "Collection failed (`<field>`): `<reason>` — re-run.",
never silently as if the empty/zero were measured.

### What was converted

A shared, minimal convention: any collector method that returns a `dict`
now attaches `_errors: {field_name: str(exc)}` for each of its own
try/except blocks that actually caught something, omitting the key
entirely when nothing failed (the same "stay silent when there is nothing
to caveat" contract `_credential_scope_status` already follows elsewhere).

Converted (`connection.py`):

- `get_credential_capability()` (via `_enumerate_relations()`; also
  `connected_as`, `stats_role`)
- `get_schema_info()` (`schemas`, `enumeration_floor`, and
  per-zero-privilege-schema catalog reads)
- `get_privilege_audit()` (`roles`, `table_grants`, `default_acl`)
- `get_replication_status()` (`is_in_recovery`, `replicas`)
- `get_wal_archiving_status()` (`archive_mode`, `archiver_stats`)
- `get_backup_tool_signals()` (`detected_extensions`)
- `get_clustering_info()` (`citus_detected`)
- `get_external_dependencies()` (`extensions`, `foreign_servers`,
  `foreign_tables`, `publications`, `subscriptions`)

Two new helpers (`survey_definition_adapter.py`):

- `_merge_collector_errors(*sections)` — combines every `_errors` sub-dict
  several independently-collected sections carry into one dict, since
  `_db_resilience_headline` builds one sentence from four separately
  fetched sections.
- `_collection_failed_headline(errors)` — the shared "Collection failed
  (`<field>`): `<reason>` — re-run." renderer.

All four operations-family headline readers now check for a recorded
error before computing their normal sentence:

- `_db_resilience_headline` — checks all four of its sub-sections via
  `_merge_collector_errors`; ANY of the four failing fails the whole
  headline (deliberately coarse — see "Explicitly NOT done here").
- `_db_external_dependencies_headline`
- `_db_privilege_audit_headline` — a recorded `roles` error now wins over
  the pre-existing "empty roles → honest-absence `None`" floor, so a
  caller that CAN say why roles is empty says so, rather than falling
  back to the generic no-summary-reader state a plain empty list gets.

### Explicitly NOT done here

- **`get_table_activity()`/`get_stats_reset()`** (feeding
  `db_activity_signals`) were NOT converted. Their return types are
  `list[dict]`/`str`, not `dict` — there is no natural place to attach an
  `_errors` key without changing their contract, and their only two
  callers (`_survey_operations` in `database_surveyor.py`, and
  `get_statistics()` in this same file) combine them into a larger dict
  built at the CALL SITE, not inside these methods. Converting them
  properly means deciding, at the call site, whether a failure here
  should fail only the `activity_signals` section (current behavior,
  preserved) or something coarser — a design question, not a
  drop-in change, and lower priority than the four analyses above since
  `db_activity_signals` was never silently empty to begin with (it has
  two real scalar fields, `stats_reset`/`table_count`, so a failure there
  already surfaces as a thinner-than-ideal sentence, not a confident wrong
  one).
- **`_get_schema_descriptions`, `_catalog_columns_for_table`,
  `_catalog_only_fallback`, `get_column_stats`, `get_index_stats`** — five
  of the six remaining sites from the slice 17c inventory, all best-effort
  enrichments where an empty result was always a legitimate degrade path.
  Left unconverted as lower-priority, not silently dropped.
  **Correction to this list's own first draft:** it originally also named
  `_get_table_row_stats` here, and claimed `get_statistics()` (which it
  feeds) has no analysis reader at all. Both turned out wrong on closer
  inspection during this PR's own live gate — see Part 3 below and the
  corresponding `Backlog.md` entry: `_get_table_row_stats` DID need
  converting, for a real bug, and `get_statistics()`'s output IS consumed
  (by `_store_results`'s row/size enrichment), just not by an
  `DATABASE_ANALYSIS_RESULTS_MAP` reader.
- **Per-field composition in `_db_resilience_headline`** — when one of
  its four sections fails, the WHOLE headline renders the failure rather
  than splicing a caveat onto the sections that did succeed. Simpler and
  correctly conservative (never shows a wrong number), but a future
  refinement could show the three good sections with a footnote on the
  fourth instead.
- **`privilege_audit`'s `default_acl` field** still has no honest-absence
  floor of its own (only `roles` does) — `default_acl` empty IS a
  legitimate real state (no default-ACL rows at all is common), so no
  floor was warranted there; recording its own `_errors` entry (done) is
  sufficient without also gating on emptiness.

## Part 3: two bugs found live while gating Part 1

Both surfaced re-verifying the enumeration floor on `coco_pharma`
(2026-09-26), both fixed in this PR rather than deferred, since both
directly undermine what "How big is this database" is supposed to mean —
the same honesty class Parts 1 and 2 exist to establish, found one level
further down the pipeline than either originally looked.

### 3a. `_get_table_row_stats()` treated "never analyzed" as a real zero

The owner's gate re-run showed "How big is this database" dropping from
3,526 total rows (7 tables) to 0 (53+ tables reading as "measured"). Cause:
`n_live_tup` (`pg_stat_user_tables`) is maintained by incremental DML
tracking, not only by `ANALYZE` — but a plain SQL dump/restore carries
table DATA, not this runtime counter, so a freshly-restored table reads
`n_live_tup = 0` indistinguishable from one that is genuinely empty. Live:
every table in `coco_ods`/`coco_sus` showed `n_live_tup = 0` with
`last_analyze`/`last_autoanalyze` both `NULL` — tables named `orders`,
`customers`, `order_details`, plainly not empty ones.

**Fix:** `_get_table_row_stats()` now returns `row_count: None` (not `0`)
when `n_live_tup` is zero AND no `ANALYZE` has ever run. A real nonzero
count is still trusted regardless of `ANALYZE` history, since DML tracking
alone would have produced it — only the ambiguous zero case is gated.
Re-verified live end-to-end: `total_row_count` back to exactly 3,526,
from the 7 tables that do have real `ANALYZE`-backed stats (hand-summed:
9+53+51+53+114+3195+51 = 3,526, matching a prior known-good reading
exactly).

**Noticed, not fixed:** those same 7 tables carry `state:
catalog_estimate` (their SCHEMA was found via the enumeration floor, not
`information_schema`) even though their `row_count` is a real,
`ANALYZE`d measurement, not an estimate — "N of M row count(s) are
catalog estimates, not exact" will currently describe these 7 as inexact
when they aren't. `state` says HOW a table was discovered; it doesn't say
whether the NUMBER is exact — logged in `Backlog.md`, not fixed here.

### 3b. Any per-card analysis run without `"statistics"` clobbered every table's row_count/size_bytes to zero

Re-verifying 3a's fix, running `schema_inventory` (steps `["schema",
"views"]`, no `"statistics"`) right after `row_count_snapshot` flipped the
just-fixed `row_count: None` straight back to `0` for the same tables.
Cause: `_store_results` enriches every table's `row_count`/`size_bytes`
from `results["statistics"]` unconditionally, on every survey run — when
the run's own requested steps never included `"statistics"` (true of
`schema_inventory` AND `db_activity_signals`, per
`DATABASE_SURVEYOR_STEP_MAP`), `statistics` is `{}`, and the old code's
bare `else: table["row_count"] = 0` asserted a zero for every table this
run never looked at, clobbering whatever a PRIOR `row_count_snapshot` run
had correctly measured.

**Fix:** `_store_results` now reads back whatever is already stored for
the database before enriching, and a table this run collected no fresh
statistics for keeps its PRIOR value (or `None`, never a fabricated `0`,
if nothing was ever stored). A run that DOES fetch fresh statistics still
overwrites normally — this only stops a statistics-free run from
asserting a value it never measured.

**This is the honest stopgap, not the real fix.** The underlying cause is
one row per table getting overwritten by every survey run instead of
survey rows keyed `(slug, surveyed_at, source)` per design rule D — the
structured-tables rework (stream 3) is where that actually gets fixed.
Preserving prior values here prevents the visible symptom (the gate's own
numbers flipping depending on which button was clicked last) without
touching that larger design.

## Tests

- `test_schema_enumeration_floor.py` (new, 8 tests): `_enumerate_relations()`
  returns the raw floor data and raises rather than defaulting;
  `get_credential_capability()` uses the shared floor and records
  `_errors` on failure; `get_schema_info()` fills in zero-privilege
  schemas with their real tables, never duplicates an already-visible
  schema, records `_errors` when the floor pass itself fails, and agrees
  with `get_credential_capability()` on `table_total` end-to-end.
- `test_postgres_catalog_fallback.py` (4 new tests, in a new
  `TestSchemaInventoryResultsNameRelationKindsSeparately` class):
  `base_table_count`/`view_count`/`materialized_view_count`/
  `foreign_table_count` are each counted correctly against a mixed-kind
  fixture and an all-base-tables fixture.
- `test_collector_honesty.py` (new, 16 tests): each converted collector
  records the right `_errors` entry on a forced failure and stays clean
  (no `_errors` key) when nothing fails; `_merge_collector_errors`/
  `_collection_failed_headline` unit-tested directly; each of the three
  updated headline readers renders "Collection failed" on a forced
  section failure and its normal sentence otherwise; the
  `privilege_audit` case specifically confirms a recorded error wins over
  the pre-existing empty-roles honest-absence floor.
- `test_table_row_stats_never_analyzed.py` (new, 4 tests): a zero
  `n_live_tup` with no `ANALYZE` history returns `row_count: None`; a
  zero with real `ANALYZE` history is still a real `0`; a nonzero count is
  trusted regardless of `ANALYZE` history; several tables in one call are
  judged independently.
- Confirmed directly against the real `coco_pharma` registry (not a
  fixture): every converted collector runs clean (`_errors` is `None`)
  against the live connection — the new error-recording paths are not
  spuriously firing on real, working queries. Also confirmed the
  never-analyzed fix end-to-end: re-ran `schema_inventory`/
  `row_count_snapshot` for real against `coco_pharma`
  (`run_database_analysis`, the same function the Run button calls) both
  before and after the fix — before: 0 total rows across 53+ tables read
  as "measured"; after: 3,526 total rows across the 7 tables that
  genuinely have `ANALYZE`-backed stats, matching a prior known-good
  reading exactly.
- `test_store_results_preserves_prior_stats.py` (new, 4 tests, full
  `DatabaseSurveyor.survey()` end-to-end against a fake connection, real
  registry writes): a `row_count`/`size_bytes` a `row_count_snapshot`-shaped
  run measured survives a later `schema_inventory`-shaped run (no
  `"statistics"` requested); the same for a `db_activity_signals`-shaped
  run (`"operations"`, not `"statistics"`); a table never measured at all
  is `None`, never a fabricated `0`; a run that DOES fetch fresh statistics
  still overwrites normally, confirming the fix doesn't freeze values
  forever.
- Confirmed directly against the real `coco_pharma` registry (not a
  fixture): every converted collector runs clean (`_errors` is `None`)
  against the live connection — the new error-recording paths are not
  spuriously firing on real, working queries. Also confirmed both Part 3
  fixes end-to-end, live: ran `row_count_snapshot` (→ `total_row_count`
  3,526, three specific tables `None`), then `schema_inventory` (→ those
  three tables STILL `None`, not clobbered), then `db_activity_signals` (→
  still `None`, `total_row_count` still 3,526) — the exact clobber-then-fix
  sequence reproduced and confirmed closed.
- Full suite: 290 passed (pre-3a/3b run) across every test file touching
  the statistics/row-stats/schema-enumeration pipeline
  (`test_postgres_column_profile.py`, `test_postgres_operations_step.py`,
  `test_schema_containment_grain.py`,
  `test_postgres_schema_and_stats_extension.py`,
  `test_credential_capability_step.py`, `test_database_surveyor_steps.py`,
  `test_postgres_catalog_fallback.py`, `test_postgres_nested_columns.py`,
  plus this PR's own test files). Whole-suite run including 3a/3b: [pending
  — recorded once the background run finishes].

## Live signed-in gate

**Rounds 1–3 (owner, 8811, 2026-09-26): two real bugs found and fixed,
plus one stale-process false alarm.**

- **Round 1 — totals apparently 56, not 61.** Traced to the owner's
  screenshot having come from **port 8810** (still serving pre-fix code at
  that moment), not 8811. Confirmed by direct reproduction against 8811's
  own code — not a code bug.
- **Round 2 — rows dropped to 0.** Real bug — see Part 3a above
  (`_get_table_row_stats()`'s never-analyzed handling). Fixed, re-verified
  live: `total_row_count` back to exactly 3,526.
- **Round 3 — a SECOND owner screenshot from what was provably 8811 again
  showed 56 tables and pre-17c wording on one row.** This one WAS a stale
  process, this time confirmed rather than assumed: the local clone's
  `git log -1` showed `066c4850`, not the `5c7a1055` the branch had
  actually reached after a GitHub-side merge-from-main — the running
  8811 process had never been restarted after that merge. Fetched,
  fast-forwarded the clone to `5c7a1055` (confirmed via `git log -1`
  before restarting), killed the stale process, started a genuinely fresh
  one, and re-verified: activity row rendered correctly
  ("0 writes and 3 reads since statistics collection began (never
  reset).", the #300 wording); "How big" gave 61 tables, matching the
  probe. **The re-verification itself then surfaced Part 3b** (the
  `_store_results` clobber) when a follow-up `schema_inventory` run
  flipped the just-corrected row counts back to `0` — also fixed, also
  re-verified live end-to-end (see Part 3b and the Tests section above).

**Confirmed via direct reproduction, all in one place, on `5c7a1055` +
both Part 3 fixes:** `get_schema_info()`/credential-probe totals agree at
8 schemas / 61 tables; `demo`/`demo_auth` present with real tables tagged
`access: "no_usage"`; `row_count_snapshot` → `schema_inventory` →
`db_activity_signals` run in sequence all leave `total_row_count` at
3,526 and the three never-analyzed forecast tables at `row_count: None`
throughout — the exact sequence a `git branch --contains`-style owner gate
would exercise by clicking Run buttons in any order.

**Still needed:** the owner's own screen confirmation on the actual
fixed/rebased/restarted process (this session cannot see the browser); and
a forced collector failure rendering "Collection failed (<field>):
<reason> — re-run." — **not independently live-verified**: the `surveyor`
credential has no `CREATEDB`/`CREATEROLE`/superuser rights, so there is no
way available to this session to manufacture a genuine live per-query
Postgres failure without touching real data or guessing at elevated
credentials. Covered instead by 16 tests in `test_collector_honesty.py`
exercising the real collector methods against a connection whose
`execute_query` is swapped to raise on a matched query — faithfully
triggering each collector's own `try`/`except` and confirming both
`_errors` recording and each headline reader's "Collection failed"
rendering. If a scratch Postgres object becomes available, this item
should still be confirmed live.

Also noticed, not fixed in this PR (logged in `Backlog.md`): the 7
`ANALYZE`d-but-catalog-discovered tables all carry `state:
catalog_estimate` even though their `row_count` is a real measurement, not
an estimate — the headline wording will currently describe them as
inexact when they aren't. A real ambiguity between "how the table was
discovered" and "how exact its number is," worth its own pass.

Whoever runs this: append the outcome here, one sentence per screen, per
the coordinator brief's own gate convention.
