# AdventureWorks correctness fixes — implemented

**Coordinator brief:** owner registered a real, densely-connected
PostgreSQL sample database (`adventureworks`, slug `laz_local_adventureworks`,
68 tables, dense FKs, full comments, superuser credential) and verified
Resource Explorer's derived analyses against ground truth via direct
`psql` — seven findings came back. Given this session's already-large
length, scope was explicitly narrowed by the coordinator to: #1 and #4
(must-fix), #5/#6/#7 (fix "if cheap"), with #2 and #3 explicitly deferred
to a fresh session as `docs/Backlog.md` entries rather than attempted here
("a half-ported key query on the primary path is worse than the current
known undercount").

**Branch:** `re/adventureworks-correctness`, off `main` (`832485e8`).
**PR:** #TBD.

## #1 — `_db_activity_signals_headline` summed the wrong column names

`survey_definition_adapter.py`'s write-count sum read `n_tup_ins`/
`n_tup_upd`/`n_tup_del` — the RAW `pg_stat_user_tables` column names —
but `connection.py`'s own `_survey_operations()` renames those to
`rows_inserted`/`rows_updated`/`rows_deleted` before storing (only
`seq_scan`/`idx_scan` keep their raw names, which is why the read-count
half of the headline was always right and the write-count half silently
summed three keys that are never present, always defaulting to 0).
Reported "0 writes and 861 reads" for a database with 761,184 inserts and
1,435 updates.

Fixed to read the stored field names. Two existing test fixtures in
`test_slice17c_renderable_answer_gate.py` were using the wrong (raw) key
names themselves, which meant they validated the bug as correct; fixed
alongside a new regression test,
`test_activity_signals_headline_does_not_read_the_raw_pg_stat_column_names`.

## #4 — the Survey pane never refreshed after an async run completed

A Scouting Survey Definition run (`a1beaffe`) genuinely completed —
three survey rows written, 19:20:18→19:20:33 — but the Survey pane kept
reading "never run" and the launch note stayed on "Launched … runs
asynchronously" forever, because `launchSurvey()` set that note once and
never re-fetched anything.

Fixed by having `launchSurvey()` watch the run the same way the
per-analysis run button already does (`pollActivity(res.activity_id,
{})`, treating a poll timeout as "stopped watching", not "failed" — the
same distinction the existing per-analysis handler draws), then reloading
the whole Survey pane if the user is still on it. New
`tests/test_next_survey_pane_refresh.py` (3 tests, static-source
assertions, no browser — same pattern `test_next_db_server_discovery.py`
established) checks the poll call, the timeout-is-not-failure branch, and
the pane reload.

## #5 — the grain "timed count" implied a stronger signal than it had

The resource-level headline said "68 carry a time interval" without
saying which BASIS produced that count. On AdventureWorks the heuristic
that actually fired for nearly all 68 was a column-NAME match (every
table has a `modifieddate` column); only 6 tables genuinely had a date
column in the primary key (`primary_key_date` basis, the strong,
schema-declared signal). The wording read as "68 have a date in the
key" when the truth was "68 have some naming-only evidence of a period."

`determine_grain()` already computed `interval_bases` (a count per
basis) — it just wasn't surfaced in the resource-level headline text.
Fixed `_grain_determination_headline` to report the split explicitly:
all-key-basis says so plainly; a mix names both counts; naming-only
names itself as lower confidence. Two new tests in
`test_db_derived_step.py::TestSlice21bHeadlines`.

## #6 — view-only schemas were indistinguishable from empty schemas

AdventureWorks's five shortcut schemas (`hr`/`pe`/`pr`/`pu`/`sa`) hold
only views over tables that live in another schema — genuinely zero base
tables, not a schema nobody populated. Views' `row_count` is never
measured, so before this fix they fell through the same `row_total is
None` path a truly empty schema takes, rendering "6 table(s) · 0
row(s) — empty" — identical text to an actually-empty schema, exactly
the ambiguity Slice 22's usability gate ("see which schema holds the data
at a glance") exists to close.

Added a `views_only` classification in `_schema_inventory_container_rows`
(checked before the `empty` branch: `table_count > 0` and every row's
`table_type != "BASE TABLE"`), rendered as `"{schema} {n} view(s) · no
base tables"` in the container headline and `"views only, no base
tables"` in the per-schema evidence-table note.

Also fixed the related counting bug the coordinator flagged alongside
it: `_schema_inventory_results`'s `schemas_with_tables` counted any
schema with a ROW in `database_tables` at all, which counted a view-only
schema as "with tables" — reported 10, truth 5 base-table-bearing
schemas for AdventureWorks. Now counts only schemas with at least one
`table_type == "BASE TABLE"` row.

New tests: `test_schema_inventory_container_headline.py::
TestViewOnlySchemasAreDistinctFromEmpty` (2 tests) and
`test_schema_inventory_results_evidence_fields.py::
test_a_schema_of_only_views_does_not_count_as_with_tables`.

## #7 — a real "no similar database found" finding was reported as missing data

`db_fingerprint` genuinely measured AdventureWorks against 2 comparable
databases and found nothing above the 30% reportable-Jaccard threshold —
a real, negative finding (`fingerprint_database()`'s own words: "no known
database resembles this one"). But `_fingerprint_evidence()` returned
`None` whenever `best_similarity is None`, which is also what "there were
no comparable databases to check against at all" produces — the two
genuinely different causes were conflated, so `classify_database()` put
"fingerprint" in its `missing` list and reported "No data for:
fingerprint" for a family that had, in fact, contributed a real
(zero-strength) measurement.

Fixed `_fingerprint_evidence()` to only return `None` when there were no
comparable databases at all (`not fingerprint.get("comparable_databases")`);
when comparable databases exist but nothing matched, it now returns a
real evidence dict (`KIND_COPY: 0.0`) that counts as available signal.
New regression test,
`TestClassification::test_a_measured_no_match_fingerprint_is_not_reported_
as_missing`, feeds `classify_database()` exactly this fingerprint shape
and asserts `"fingerprint"` is in `signals_used`, not `signals_missing`.

## Deferred, NOT attempted here — see `docs/Backlog.md`

- **#2**: `database_table_activity` rows are clobbered at the
  structured-table layer by a multi-step survey run's later steps (only
  2 of 8 recorded runs for `laz_local_adventureworks` carry real
  counters; the other 6 each overwrote 157 rows with NULLs). Same class
  of bug as the already-logged `_store_results` survey_data-blob
  clobber, one layer down.
- **#3**: the PRIMARY-path PK/FK queries in `connection.py`
  (`_get_tables_for_schema`) drop keys for columns referenced/declared
  from many places (71/91 FK columns, 99/181 PK columns captured) — the
  classic `constraint_column_usage` join-multiplicity problem. Slice
  21b's `_catalog_keys_for_schema` (the FALLBACK path) already reads
  `pg_constraint`/`pg_index` correctly with `WITH ORDINALITY`; the fix is
  to promote that one implementation to the primary path too.

Both are documented in full technical detail (exact numbers, affected
columns, fix direction, why not attempted tonight) as their own
`docs/Backlog.md` entries for a fresh-session hand-off.

## Tests

- Targeted runs across the touched files: all green (see individual
  sections above).
- Full suite: see PR / commit message for the final count.

## Report

Branch `re/adventureworks-correctness`, tip: see PR. Gate will be on both
`coco_pharma` and `adventureworks`, per the coordinator's own words,
"plus a Scouting-tab check on adventureworks (writes non-zero, scan shows
ran)."
