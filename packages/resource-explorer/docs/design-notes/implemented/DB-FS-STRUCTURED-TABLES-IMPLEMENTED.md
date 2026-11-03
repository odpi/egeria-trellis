# DB/FS structured tables and the read-back materialiser: implemented

**Replies to:** `COORDINATOR-BRIEF-MULTI-RESOURCE.md`, stream 3 — *"Structured
tables and the read-back materialiser … all keyed `(slug, surveyed_at,
source)`; a materialiser that turns native annotations read back through
`egeria_survey_reader.py` into the same rows with `source='egeria'`; a
back-fill from existing `survey_data` blobs."*

**Implements:** `multi-resource-questions-design.md` §5.7 (database structured
storage), §6 / §6.3 (the filesystem equivalents), §3 rule D (local copy of
every result, keyed by source), §2.2 (native annotation types and metric keys
are copied, not invented), §5.1 and §5.8 (absence modes, sampling provenance),
and §13 Phase 0 item 5.

**Branch:** `re/db-fs-structured-tables`, worktree `.claude/worktrees/wt-tables`.
Nothing was written in the main checkout.

**Tests:** full suite green, 0 failed. 51 new tests in
`tests/test_db_fs_structured_tables.py`.

---

## A note on the brief itself

The brief names three documents as "already committed on `main`". Two are;
`COORDINATOR-BRIEF-MULTI-RESOURCE.md` is not. It exists only on
`re/multi-resource-design` (commit `b7a7b537`, written after PR #177 merged
`3b195d88`). This branch was cut from `main`, so the brief was read out of
that commit rather than from the worktree. Worth landing on `main`, or every
stream cut from `main` will hit the same thing.

---

## What was built

### 1. Twelve tables, not ten

The ten named in the brief, in `registry.py`'s DB/FS DDL section:
`database_schemas`, `database_tables`, `database_columns`,
`database_column_profiles`, `database_table_activity`, `database_grants`,
`database_sql_objects`, `database_settings`, `filesystem_entries`,
`filesystem_data_files`. All keyed `(slug, surveyed_at, source)` with a
`UNIQUE` constraint on that key plus the object's identity within it.

**Plus two that were not asked for: `database_survey_coverage` and
`filesystem_survey_coverage`.** They are the reason the absence rule is
implementable at all, and they are the one addition here that a reviewer
should push back on if they disagree.

The rule says "couldn't get this" and "got this, it's empty" must not share a
row shape. A per-row `state` column handles that *for rows that exist*. It
cannot handle the case that actually matters: an empty `database_grants` for a
given run is ambiguous between three different answers — this survey never
looked at grants, the survey user could not read `pg_roles`, and there
genuinely are no grants — and in all three cases there is no row to carry a
state. One row per section per run makes the distinction a stored fact rather
than an inference from emptiness.

Vocabulary, all in `registry.py` beside the constants: `measured`, `empty`,
`not_permitted`, `not_collected`, `not_supported`, `not_measured`. `empty` is
the only one that licenses a consumer to render "none".

These started as **one** polymorphic table keyed `(resource_type,
resource_slug)`, which the full suite rejected — `tests/test_no_orphaned_
slugs.py` discovers slug-bearing columns from the live schema and requires
each to carry a foreign key or a justified exemption, and a polymorphic slug
cannot have one. That test was right. Coverage describes a survey *of* a
resource, so when the resource is deleted these rows are debris rather than
history that outlives it, which is precisely the case its allowlist is not
for. Two typed tables get a real FK and need no exemption. Adding `dataset`
and `model` later means two more tables; `_COVERAGE_TABLES` is the one place
that changes, and an unknown resource type raises rather than writing
coverage nowhere.

### 2. Conventions followed

Matched to what `registry.py` already does rather than invented: DDL inline in
`_init_schema()`, SQLite dialect throughout (`PostgresCursorWrapper.
_translate_sql` rewrites `?` and `INTEGER PRIMARY KEY AUTOINCREMENT` on the
way out), JSON as `TEXT`, timestamps as ISO-8601 `TEXT`, booleans as
`INTEGER`, migrations as `_get_table_columns` + `ALTER TABLE ADD COLUMN`.

`_DB_FS_DETAIL_TABLE_MIGRATIONS` is deliberately empty at introduction. It is
the seam so a column added later lands on existing registries the same way
`database_surveys.source` did, rather than being silently absent on any
registry created before it.

One deviation worth naming: rather than ten near-identical writer/reader
pairs, there is one generic pair (`write_detail_rows` / `query_detail_rows`)
driven by `_DETAIL_TABLE_SPECS`, which is **parsed from the DDL** at import.
A column added to a `CREATE TABLE` is picked up with no second edit. The
alternative — a hand-maintained column list per table — goes stale silently:
the INSERT keeps working and the new column just never receives a value.

### 3. The materialiser

`surveyors/result_materializer.py`. Two inputs, one row shape: native Egeria
annotations, and RE's own `survey_data` blobs.

Annotation types and metric keys were **read from the Egeria Java checkout on
2026-09-20**, not recalled:

- `SurveyDatabaseAnnotationType` / `SurveyFolderAnnotationType` — the
  `annotationType` strings, which are human-readable sentences ("Capture
  Database Table Measurements"), not enum names.
- `RelationalDatabaseMetric`, `RelationalSchemaMetric`, `RelationalTableMetric`,
  `RelationalColumnMetric`, `FileDirectoryMetric`, `FileMetric` — the
  `resourceProperties` keys.
- `PostgresDatabaseStatsExtractor.java` — which annotation carries which metric
  set, the qualified-name nesting this module has to reverse
  (`database.schema.table.column`), and the detail that **every value arrives
  as a string**, because the extractor writes `Long.toString(...)` into a
  `Map<String,String>`.

`egeria_survey_reader.py` was extended to carry the payload through. It
previously dropped `resourceProperties` and every `profile*` field entirely —
a native survey's numbers were read back as prose in `summary` and nothing
else, which is precisely why native results could not be queried like local
ones.

**How a failed engine action is represented — added 2026-09-21, after live
verification surfaced the question.** `materialize_database_report`/
`materialize_filesystem_report` take resolved `annotations`, not the raw poll
result — `poll_trigger_and_retrieve_annotations`'s `final_status`/
`completion_message` never reach either function. When a native survey
section comes back empty, `_native_absence_note` already writes an honest,
generic ambiguity note onto `database_survey_coverage` (citing Egeria's own
Postgres connector docs: missing may mean "no permission", not "none exist").
**What this does NOT yet do** is distinguish that generic ambiguity from a
more specific, stronger signal: the engine action itself terminating as
`INVALID`/failed rather than completing and finding nothing. Both currently
collapse into the same coverage note if a caller passed empty annotations
either way.

This is filed as a Backlog item ("native-result materializers conflate
engine-action failure with genuine empty result") rather than fixed here,
because **neither function has a call site yet** — confirmed by grep,
2026-09-21 — so it is not live-wrong today. It becomes load-bearing the
moment Phase 1 wires a real native-survey caller into either function; that
caller should thread `final_status` through and give a failed engine action
its own coverage note, distinct from "ran fine, found nothing."

### 4. The back-fill — a script, not a migration step

`scripts/backfill_structured_tables.py`. The brief left the choice open; the
reasoning is in the script's own docstring, in short:

`_init_schema()` runs on *every* `ProjectRegistry(...)` construction, which in
the web app is most request paths. Decoding every historical blob there is
bounded by survey history rather than by startup, and gating it on "have I run
yet" means inventing a migration-version table this registry deliberately does
not have — its entire migration idiom is per-column `ALTER TABLE`, with no
notion of a data migration. Run explicitly, it also reports per-survey counts
and supports `--dry-run`, which a silent init-time step would not.

Verified end to end against a seeded SQLite registry: dry-run reports without
writing, the real run populates, and re-running is a no-op because
`write_detail_rows` replaces rather than appends.

### 5. New surveys do not need the back-fill

`record_database_survey` and `add_filesystem_survey` now materialise rows from
the same blob they are already handed. Every database survey path — local,
hybrid, and the `egeria-adaptive` handler — funnels through those two methods,
so one seam covers all three and no surveyor has to remember. A failure there
is logged, not raised: a conversion bug must not cost the survey result that
was just recorded, and the blob is still the raw record.

This was not in the brief's scope list. Without it, rows would only ever exist
for runs someone remembered to back-fill, and `get_database_diff` would depend
on that forever. It touches no file another stream owns.

### 6. Removal had to learn about the new children

`remove_database` deleted `database_surveys` and then `databases`; `remove_
filesystem` did the same pair. Every one of the new tables carries a real FK
to the same parent, so the first attempt to remove a surveyed database would
have failed outright with a foreign-key violation — on Postgres, and on
SQLite too, since `_conn()` sets `foreign_keys=ON`.

This is louder than stranding rows but just as broken, and it is not
something the new tests would have caught: it was the full suite, via
`test_integration_registry_pg.py`'s `TestRemoveCannotForgetANewTable`, which
exists because "remove() hand-lists its child DELETEs and twice nobody
remembered". Both removal methods now iterate `_DETAIL_TABLE_SPECS` rather
than hand-listing, so a table added later is covered without a second edit,
and `TestRemovalCleansUpChildren` pins it.

### 7. `get_database_diff` reads rows

`web/routes/databases.py`. The design doc cites this function by line number as
the thing that re-parses JSON; it no longer does.

It also had a live instance of the absence bug. The old `_table_set` caught
every exception and returned an empty set, so an unparseable blob, a blob in an
older shape, and a database with genuinely no tables all produced the same
answer: "±0 tables, none added, none removed" — a confident wrong answer with
no error anywhere. Two runs are now diffed only when both were genuinely
measured; otherwise the response says `table_diff_state: "not_comparable"`
with a note naming the back-fill script.

`index.html`'s diff banner renders that note. Per this repo's "verify the
surface the user reads" rule, the honest state had to reach the screen, not
just the JSON.

---

## Designer feedback, folded in before commit

Relayed by the coordinator mid-build, from a designer review of these exact
tables. All three landed in the schema rather than as a follow-up PR:

- **`stats_source` and `stats_computed_at`** on `database_column_profiles` and
  `filesystem_data_files`. `sample_strategy` says how much was looked at; it
  does not say *whose* numbers these are or *as of when*. A Postgres profile
  can come from the database's own `pg_stats` — whole table, not ours,
  possibly months older than the survey reporting it. Without these, a card
  shows a three-month-old null fraction beside a two-minute-old row count and
  says nothing about the difference. This also makes the required "no
  statistics collected, run ANALYZE" state exact rather than special-cased:
  it is simply `stats_source = database` with `stats_computed_at` NULL.
- **`stats_reset`** on `database_table_activity`. `pg_stat_user_tables`
  counters are cumulative since the last reset, and a change rate is the
  difference between two snapshots. A reset, failover or restore between
  snapshots makes that difference negative, and a small-multiples chart would
  faithfully draw "−40,000 inserts". Storing the reset timestamp per snapshot
  lets a comparator report "counters reset, no rate available" instead. The
  native survey does report it, at database level
  (`RelationalDatabaseMetric.LAST_STATISTICS_RESET`), so it is carried onto
  every activity row. `test_a_moved_stats_reset_is_detectable_between_two_
  snapshots` pins the case.

Note this replaced a field of mine called `stats_basis`, which conflated
"which engine ran the survey" (already in `source`) with "whose numbers these
are". The designer's split is the right one, and the consequence is that a
native survey's column statistics are attributed to
`stats_source = database`, not to Egeria — Egeria transported them out of
`pg_stats`, it did not compute them.

---

## Absence is a result — what was done, concretely

Applied to the materialiser and back-fill as the brief asked:

| Situation | How it renders |
|---|---|
| Native survey reports no schemas | `empty` + coverage detail stating Egeria's own docs treat this as possibly a permissions failure, not resolvable from the report |
| `pg_stats` never populated | `stats_source = database`, `stats_computed_at` NULL |
| Old blob never carried grants / settings / profiles | `not_measured` with a detail naming why |
| `survey-folder` without `-and-files` | entries `not_measured`, detail naming the variant needed — not "empty directory" |
| File found but unreadable | an entry row with `state = not_permitted`, not an omission |
| Measured zero | `0` in the column, `state = measured` |
| Never measured | `NULL` in the column |
| Section nothing claimed to have tried | absent from coverage entirely — weaker than `not_measured` |
| Coverage asked for an unsupported resource type | raises, rather than writing coverage nowhere |

**The guards were verified by deliberate mutation, not by green.** Three
mutations were introduced one at a time and the suite re-run:

1. `_as_int` returning `0` instead of `None` for an absent metric → 2 tests
   fail.
2. `get_database_diff`'s `comparable` forced to `True` → 1 test fails.
3. (The first mutation initially caught only one test; a second assertion was
   added so the dedicated absence test catches it directly rather than relying
   on a neighbour.)

All mutations reverted; suite green.

---

## Scoped out

- **Probe 9 verification.** The brief says the FS/DB column set should be
  checked against probe 9's dump before this merges. Probe 9 is stream 1's and
  did not exist when this finished. **This is the one open verification item**
  — see below.
- **Reading `pg_stats`, tuple counters and indexes.** Design §5.7 assigns that
  to the `postgres_schema_and_stats` extension, which the coordinator brief
  makes Phase 1 item 7 (Sonnet, gated on this stream). The columns are here and
  empty; nothing in this branch populates them.
- **Grants and settings collection.** `database_grants` and `database_settings`
  exist and are never written by this branch. `privilege_audit` runs as its own
  analysis, and `postgres_operations` (Phase 1 item 8) is what fills them.
- **Change comparators.** `stats_reset` is stored so `schema_diff` / `row_drift`
  (design §9.1, Phase 1 item 14) can use it. Computing rates is not this
  stream's job.
- **`database_sql_objects` dependency and lineage columns.** Populated as NULL
  by the native path; RE's existing `sql_analysis` is what fills
  `depends_on_json` / `column_lineage_json`.

## What could not be tested

- **Anything against a live Egeria platform.** No live read or write was
  performed, per the task's instruction to check with the coordinator first.
  The native-annotation fixtures are built to the shape read out of the Java
  source, so they pin *the shape this code was written against* — they do not
  prove the live platform emits it.
- **The two live `database_surveys` rows.** The back-fill was verified against
  a seeded SQLite registry, not against the real Postgres registry's `coco_ods`
  and `coco_pharma` rows. Running it there is a shared write and wants the
  `coordinate-shared-writes` peer check first.
- **The diff banner in a browser.** The route is covered by tests through
  `TestClient`; the rendered banner was not opened in a running app.
- **Postgres-backed schema creation.** The new DDL was exercised on SQLite.
  It uses only constructs `_translate_sql` already handles and that existing
  tables already use, but the Postgres path was not run.

---

## For the coordinator

**Update, 2026-09-21 — item 2 done, item 1 still open.**

1. **Probe 9's dump against the materialiser — still open.** Checked directly:
   neither `coco_ods` nor `coco_pharma` currently has a stored *successful*
   native (`source='egeria'`) survey report to dump — `coco_ods`'s one native
   attempt terminated `INVALID` (`OPEN-SURVEY-0009`, Backlog.md), and
   `coco_pharma` has none on record at all. Triggering a fresh native survey
   to get a real dump is a new live write beyond the peer clearance already
   obtained for the read + back-fill; needs its own round with the project
   owner before it happens.
2. **Running the back-fill against the real registry — done.** Peer-checked
   (five sessions cleared, `coordinate-shared-writes`), run once against the
   live shared registry: 24 surveys processed, 4912 rows written for
   `coco_ods`/`coco_pharma`. Verified independently by querying the written
   rows directly, not by trusting the script's own report. Surfaced and fixed
   a real migration gap along the way (see `re/backfill-migration-fix`):
   `database_column_profiles`/`database_table_activity`/`filesystem_data_files`
   had already been created in the shared Postgres before the designer's
   `stats_source`/`stats_computed_at`/`stats_reset` columns existed, and
   `CREATE TABLE IF NOT EXISTS` is a no-op against an existing table — the
   migration list had entries for all three but with empty column tuples.
   Also chased a design-review concern about `coco_ods`'s back-filled
   `table_count=0` row all the way to ground truth (a live, read-only
   `psycopg2` query confirms it genuinely has zero tables) — the row is
   correct; see Backlog.md for the real, forward-looking gap that
   investigation did surface (the materialiser's `final_status` handling,
   documented above in §3), and the follow-up "correct number, wrong label"
   note on the historical back-fill's state labelling.
