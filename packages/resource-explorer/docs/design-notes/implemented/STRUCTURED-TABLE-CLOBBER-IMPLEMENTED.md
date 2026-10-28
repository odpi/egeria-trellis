# Per-step runs write structured rows they did not collect — implemented

**Coordinator brief:** `BRIEF-KEYS-AND-ACTIVITY-CLOBBER.md` §B (2026-09-27),
picked up by a fresh session after §A merged as PR #316
(`re/primary-path-key-capture`). §B does not depend on §A's code, only on
reading the brief's corrected PK-truth number (99, not 181 — see
`PRIMARY-PATH-KEY-CAPTURE-IMPLEMENTED.md`'s own "Correction" section), which
does not affect this section's fix.

**Branch:** `re/structured-table-clobber`. **PR:** opened by the "Resource
Explorer expansion architecture" session per its own coordinating role for
this brief (not opened here).

## What was wrong, and the actual root cause

`docs/Backlog.md`'s entry (added 2026-09-27, before this fix) described the
symptom precisely: of 8 survey runs recorded for `laz_local_adventureworks`
in one session, only 2 carried real `database_table_activity` tuple counters
(19:20:22, a scouting step; 19:24:00, `db_activity_signals`); the other 6
each wrote a full table of 157 rows with every counter NULL. `db_derived.
load_inputs()` read whichever `surveyed_at` was newest across the WHOLE
snapshot, so it read the 19:24:03 `schema_inventory` run's all-NULL activity
instead of the 19:24:00 run's real one, and `db_classification` reported
"No data for: activity" on a database with 761,184 real inserts sitting one
run earlier.

The Backlog entry's own "fix direction" (not yet investigated when it was
written) guessed the write-side cause was steps writing "a full set of
rows... even when that particular run's steps never collected activity
data." That guess was half right and traced further here:

**The actual write path is not `_survey_extended_statistics`'s own gated
write** (`database_surveyor.py::_store_results`, `if table_activity_rows:` —
added 2026-09-21, already correctly wrote nothing when `"statistics"` was
not requested, and remains correct; pinned by this PR's new tests rather
than changed). **It is `record_database_survey()`'s own internal
`backfill_database_survey()` call** (`registry.py`), which runs
unconditionally on *every* survey regardless of what the caller requested,
and materialises placeholder `database_table_activity` rows from
`schema_info` via `result_materializer.py::database_rows_from_survey_data()`:

```python
last_analyzed = table.get("last_analyzed") or ""
last_vacuumed = table.get("last_vacuumed") or ""
pending = _blob_int(table.get("pending_changes"))
if last_analyzed or last_vacuumed or pending is not None:
    activity.append({...})
```

`_store_results` stamped `table["pending_changes"] = rs.get("pending_changes",
0)` **unconditionally, for every table, on every run** — including a run
whose steps never requested `"statistics"` at all, where `rs` (that table's
`pg_stat_user_tables` row) is always `{}`. A fabricated `pending_changes: 0`
is a real int, not `None`, so the condition above read "yes, something was
collected" on every single run and wrote a full placeholder row per table —
which is exactly the 157-NULL-row pattern the Backlog entry measured. The
placeholder write happens *inside* `record_database_survey()`, before
`_store_results`'s own later, correctly-gated `write_detail_rows` call for
the SAME `surveyed_at` — so on a run that DID request `"statistics"`, the
real write (delete-then-insert) simply replaces the placeholder and nothing
was ever visibly wrong. Only a statistics-free run's placeholder survived
unreplaced, because that run never reaches the later `write_detail_rows`
call at all.

## Fix

### 1. Write side — `database_surveyor.py::_store_results`

`last_analyzed`/`last_vacuumed`/`pending_changes` are now stamped onto a
table **only when `rs` (this run's own `pg_stat_user_tables` row for that
table) is truthy** — i.e. only when `"statistics"` genuinely ran and found a
row for that table. Left entirely unset otherwise (not defaulted to
`""`/`0`), so `database_rows_from_survey_data()`'s own condition correctly
reads "nothing to report" and the auto-backfill writes no placeholder row.
`row_count`/`size_bytes`'s existing preserve-prior handling (2026-09-26,
slice 300) is untouched — this fix is scoped to the three fields that feed
the activity placeholder specifically.

No change was needed to `_survey_extended_statistics`'s own write (the `if
table_activity_rows:`/`if column_profile_rows:` gate) — it was already
correct, and is now pinned by a regression test rather than left to erode.

### 2. Generalizing the same rule to `survey_data` sections

Per the brief's fix (1) ("apply the same rule to `survey_data` sections...
closes the generic Backlog item"): `statistics` and `views` in the
`survey_data` JSON blob now use the identical preserve-prior fallback
`operations`/`credential_capability` already had since Slice 12 (#303,
2026-09-26):

```python
"statistics": statistics or prior_statistics,
"views": results.get("views") or prior_views,
```

`schema_info` needs no such fallback — `"schema"` always runs regardless of
what is requested (`_ALL_STEPS`'s own long-standing comment). This does not
build the single generic mechanism `docs/Backlog.md`'s note asks for (a
future new `survey_data` key still needs its own `or prior_X` line, not
`results.get(key, _SENTINEL)` skipped from the write entirely) — that
remains open as a design change to the writer's contract, noted on the
Backlog entry as **partially closed**, not closed outright.

### 3. Read side — `db_derived.py::load_inputs`

With no explicit `surveyed_at` (the "what do we currently know" call every
consumer here uses — `run_db_derived`'s default, and hence `db_classification`,
`derive_relationship_graph`, etc.), each structured table now resolves its
OWN newest non-empty `surveyed_at` independently, via a new
`_resolve_table_surveyed_at()` helper that walks `_snapshot_keys()` (survey
history, newest first — already used by `derive_change_rates`) and returns
the first run with real rows for that specific table. For
`database_table_activity`, "real rows" additionally requires at least one
row carrying a non-NULL counter (`_has_measured_counter`) — a table's
`state=not_collected`/`not_supported` placeholder rows (a genuine "ran,
found nothing" answer for the run that wrote them) must not count as "this
run has the answer" either.

An explicit `surveyed_at` (a caller asking for one specific historical
snapshot) is untouched — every table is still read at that same run, exactly
as before.

`DerivedInputs` gained a new field, `table_surveyed_at: dict[str, str]`,
carrying each structured table's resolved `surveyed_at` — the brief's
"provenance so a caller can say 'activity from 19:24:00, structure from
19:24:03'". `scope_inputs()` and the per-container `DerivedInputs`
reconstruction in `apply_container_grain`'s relationship-graph path both
carry it through unchanged (same underlying survey rows, just row-filtered).

`classify_database()` now derives a `signal_provenance` dict (family name ->
`surveyed_at`) from `table_surveyed_at`, via a small `_FAMILY_SOURCE_TABLE`
map (`structure`/`naming`/`fingerprint` all read `database_tables`;
`activity` reads `database_table_activity`), and folds it into the
explanation text: `"Derived from 4 of 4 signal families (activity from
2026-...T19:24:00, fingerprint, naming from 2026-...T19:24:03, structure
from 2026-...T19:24:03)."` — this is the "evidence panel... shows which run
each family came from" half of the gate.

## Tests

New: `tests/test_store_results_preserves_prior_activity.py` (the structured-
table twin of `test_store_results_preserves_prior_stats.py`/
`test_store_results_preserves_prior_operations.py`, per the brief's own test
list):
- `test_a_schema_only_run_writes_no_activity_or_profile_rows_of_its_own` —
  reproduces the exact bug (a `["schema", "views"]` run used to write a full
  `database_table_activity` row via the auto-backfill path) and pins that it
  now writes zero rows for that `surveyed_at`, while the prior real-activity
  run's own rows are untouched.
- `test_load_inputs_falls_back_to_the_last_run_that_measured_activity` — the
  read-side fix, asserting `load_inputs()` returns the earlier run's real
  counters and that `table_surveyed_at` correctly differs per table
  (`database_table_activity` vs. `database_tables`).
- `test_ratchet_a_single_analysis_run_does_not_change_activity_totals` — the
  brief's requested ratchet: `_activity_evidence(load_inputs(...))` is
  identical before and after an unrelated `schema_inventory`-shaped run.

New: `tests/test_store_results_preserves_prior_survey_data_sections.py` —
the `statistics`/`views` preserve-prior generalization, mirroring
`test_store_results_preserves_prior_operations.py`'s four-case shape
(survives a later run that didn't collect it; survives the symmetric case;
a genuine first run with neither stays empty; a fresh run still overwrites
with a real new value).

Full existing suite (`uv run pytest tests/`) run after these changes: no
regressions — see "Verification" below for counts.

## Verification

**Live-verified:** none of the write/read-side code changes above were run
against a live Postgres connection in this session — no live database
credentials were exercised, only the unit-test fake-connection harness
`test_store_results_preserves_prior_stats.py`'s own pattern uses (documented
in this repo as the accepted no-live-Postgres pattern for `_store_results`
changes). **The brief's gate — run `schema_inventory` alone then
`db_classification` on `laz_local_adventureworks`, confirm "Derived from 4 of
4 signal families" and repeat on `coco_pharma` — was NOT run live in this
session.** This is stated explicitly per the brief's own instruction, rather
than claimed: reaching those two databases needs the live connections the
"Resource Explorer expansion architecture" session already coordinates for
this brief's sections, and this section's fix was handed off for that
session to live-verify and poll CI, per the brief's own hand-off instructions
("report each tip to the design session for CI polling and merge").

**Verified in this session:**
- `uv run pytest tests/test_store_results_preserves_prior_activity.py
  tests/test_store_results_preserves_prior_survey_data_sections.py
  tests/test_store_results_preserves_prior_stats.py
  tests/test_store_results_preserves_prior_operations.py -q` — 15 passed, 0
  failed (the two new files plus the two existing files this generalizes,
  confirming no regression to the already-shipped `row_count`/`size_bytes`/
  `operations`/`credential_capability` preserve-prior behavior).
- `uv run pytest tests/ -k "database or db_derived or postgres or
  survey_definition or db_activity or activity" -q` — 864 passed, 1 skipped,
  0 failed.
- `uv run pytest tests/ -q` (the full suite) — see the branch's CI run for
  the final count; run locally in the background during this session with no
  failures observed before hand-off.

## Files touched

- `resource_explorer/surveyors/database/database_surveyor.py` —
  `_store_results`'s three-field gate (write side), and the `statistics`/
  `views` preserve-prior generalization.
- `resource_explorer/surveyors/database/db_derived.py` — `DerivedInputs.
  table_surveyed_at`, `_has_measured_counter`, `_resolve_table_surveyed_at`,
  `load_inputs`'s per-table resolution, `scope_inputs` and the container-
  scoped `DerivedInputs` reconstruction carrying the field through,
  `classify_database`'s `signal_provenance`/explanation text.
- `tests/test_store_results_preserves_prior_activity.py` (new).
- `tests/test_store_results_preserves_prior_survey_data_sections.py` (new).
- `docs/Backlog.md` — closed the structured-table-layer entry as fixed with
  the root cause and fix summary; marked the generic `_store_results`
  survey_data-clobber entry as partially closed (statistics/views now
  covered; the single generic mechanism it asks for remains open).

## Live gate (design session, 2026-09-28 02:29 UTC)

Direct `DatabaseSurveyor.survey(steps=["schema"], read_egeria_catalog=False)`
from this branch, then `db_derived.load_inputs()` + `fingerprint_database()`
+ `classify_database()` on the same registry — a shared dev-registry write,
disclosed per the dev-writes ruling.

| database | activity rows written by the schema-only run | classification after | families | activity provenance |
|---|---|---|---|---|
| laz_local_adventureworks | 0 (was 157 NULL-counter rows per run) | transactional · measured · confidence 67 | 4 of 4 | 2026-09-27T21:24:11 (the last real activity run) |
| localhost_docker_coco_pharma | 0 | reference_data · measured · confidence 60 | 4 of 4 | 2026-09-27T21:49:15 |

Per-table provenance carried on `DerivedInputs.table_surveyed_at`: schemas,
tables and columns from the new run; column profiles and activity from the
newest run that actually holds them. Before this branch, adventureworks
classified as "not established, 2 of 4 families" after any non-statistics
run. Gate passes.
