# Brief — primary-path keys and the structured-table clobber (2026-09-27)

Hand-off for a fresh coordinator session. Two correctness defects found on
the first AdventureWorks pass (`laz_local_adventureworks`, native Postgres
on `localhost:5432`, loaded 2026-09-27) and verified with `psql` against the
database. Both feed Slice 22 (the per-schema inventory view) and every
derived database card, so they land **before** Slice 22 merges.

Companion small fixes (#1 activity-headline key, #4 scouting run status,
#5–#7 wording) are on `re/adventureworks-correctness` and are not repeated
here.

Rules that apply: one worktree, one PR, one `*-IMPLEMENTED.md`; DCO
sign-off on every commit; the serving checkout at
`~/localGit/egeria-v6/trellis` is only ever fast-forwarded; gate on **both**
`localhost_docker_coco_pharma` and `laz_local_adventureworks`.

---

## A. Primary-path PK/FK capture drops keys

### Evidence

Truth from `pg_constraint` on `adventureworks` versus rows stored in
`database_columns` for any of the eight runs on 2026-09-27:

| | truth | stored |
|---|---|---|
| FK columns `(table, column)` | 91 | 71 |
| PK columns | 99 (see correction) | 99 |

**Correction 2026-09-28 (design session):** the PK figure of 181 was wrong.
That count came from `pg_constraint` across *all* schemas, and since
PostgreSQL 14 the system catalogs carry primary keys: 82 of the 181 were
`pg_catalog` tables. Restricted to user schemas the truth is 99, which the
old path already captured. PK capture was never broken; only FK capture
was. The rest of this section stands.

Every one of the 20 missing FK columns points at a heavily referenced
table (`person.businessentity` ×5, `production.product` ×4,
`person.countryregion` ×2, `person.stateprovince`, `person.address` for
`billtoaddressid`/`shiptoaddressid`, …):

```
humanresources.employee.businessentityid
person.stateprovince.territoryid
production.document.owner
purchasing.productvendor.productid
purchasing.productvendor.unitmeasurecode
purchasing.purchaseorderdetail.productid
purchasing.purchaseorderheader.employeeid
purchasing.vendor.businessentityid
sales.countryregioncurrency.countryregioncode
sales.customer.personid
sales.personcreditcard.businessentityid
sales.salesorderheader.billtoaddressid
sales.salesorderheader.shipmethodid
sales.salesorderheader.shiptoaddressid
sales.salesperson.businessentityid
sales.salestaxrate.stateprovinceid
sales.salesterritory.countryregioncode
sales.shoppingcartitem.productid
sales.specialofferproduct.productid
sales.store.businessentityid
```

Downstream effects already visible on the Discovery tab: Relationship
Graph reports 71 edges / 8 components / largest 24 (undercounts); Table
Grain says keys captured 68 of 68, which is right only because every
AdventureWorks table happens to keep at least one PK column through the
loss.

### Where

`resource_explorer/surveyors/database/connection.py`:

- `_get_tables_for_schema()` ~lines 620–710 builds `pk_lookup` (keyed by
  `table_name`) and `fk_lookup` (keyed by `(table_name, column_name)`) from
  `information_schema` queries. The FK query joins
  `constraint_column_usage`, which is not keyed per column and multiplies
  or collapses rows when the referenced table has several referencing
  constraints. The PK query has the same shape.
- `_catalog_keys_for_schema()` ~lines 804–850 is the **catalog-only
  fallback** added in Slice 21b. It reads `pg_constraint` / `pg_index`
  directly under identity A and is correct. On coco_pharma it took PK
  coverage from 3 to 21 of 58 tables with no extra privilege.

### Fix

1. Promote the fallback's `pg_constraint`/`pg_index` queries to the
   primary path: `_get_tables_for_schema()` calls
   `_catalog_keys_for_schema()` (or a shared helper) instead of the
   `information_schema` queries. Key both lookups by
   `(schema, table, column)`; a composite FK contributes one entry per
   column; a column that carries two FKs (rare, legal) keeps a list.
2. Delete the `information_schema` PK/FK queries once nothing calls them.
3. Re-survey both databases; store nothing new for coco_pharma beyond the
   run itself.
4. Decide and document `_catalog_keys_for_schema()`'s failure path. Its
   signature is `tuple[dict | None, dict | None]` — it can fail. Once it is
   the primary path, a `(None, None)` return must not silently collapse to
   zero keys (a regression from today's `information_schema`-first
   behavior). Either the primary path falls back to the old
   `information_schema` queries on `None` before they're deleted (drop step
   2), or the caller surfaces the failure (e.g. keys reported
   "not_established" for that schema) rather than reporting zero keys as if
   they were measured. Pick one and say which in the IMPLEMENTED doc.

### Tests

- Unit: a fake `execute_query` returning `pg_constraint` rows for a
  referenced table with three referencing constraints, one composite FK,
  and one column with two FKs; assert 91-style counts, not 71.
- Live check written into the IMPLEMENTED doc as numbers:

```bash
/Applications/Postgres.app/Contents/Versions/16/bin/psql -p 5432 -U dwolfson -d adventureworks -Atc "select count(distinct (conrelid, k)) from pg_constraint, unnest(conkey) k where contype='f'; select count(*) from pg_constraint, unnest(conkey) where contype='p';"
```

must equal the stored `database_columns` counts for the new run (add
`n.nspname not in ('pg_catalog','information_schema')` via a join to
`pg_class`/`pg_namespace`; without it the PK count includes the system
catalogs).

### Gate

Discovery on `laz_local_adventureworks`: Relationship Graph edge count ≥ 86
(distinct table pairs; 90 constraints) and components fewer than 8; the
per-schema line on the model card names no schema as "not established
(keys_not_captured)". coco_pharma unchanged or better.

---

## B. Per-step runs write structured rows they did not collect

### Evidence

`database_table_activity` for `laz_local_adventureworks`, eight runs, all
157 rows each:

| surveyed_at | step that ran | rows_inserted sum | reads sum | NULL counters |
|---|---|---|---|---|
| 19:20:22 | scouting step 1 (schema+stats) | 761,184 | 861 | 87 (views) |
| 19:20:31 | scouting step 2 | NULL | 0 | 157 |
| 19:20:32 | scouting step 3 | NULL | 0 | 157 |
| 19:23:52 | credential_capability | NULL | 0 | 157 |
| 19:23:55 | db_resilience | NULL | 0 | 157 |
| 19:23:57 | row_count_snapshot | NULL | 0 | 157 |
| 19:24:00 | db_activity_signals | 761,184 | 861 | 87 |
| 19:24:03 | schema_inventory | NULL | 0 | 157 |

`db_derived.load_inputs()` (`db_derived.py` ~line 290) reads every
structured table at the **latest** `surveyed_at`. After the owner's last
run (19:24:03, no activity collected) `db_classification` reported
"No data for: activity" on a database with 761k inserts, and
"Derived from 2 of 4 signal families".

This is the same class as the three `_store_results` incidents in
`survey_data` (row_count/size_bytes; operations/credential_capability;
Slice 12's empty operations section), now one layer down in the
structured tables. `docs/Backlog.md` already carries the generic
`_store_results` item; this brief makes it concrete.

### Where

- `resource_explorer/surveyors/database/result_materializer.py` (or
  wherever `database_table_activity` / `database_columns` /
  `database_column_profiles` rows are written per run — grep
  `database_table_activity` in `registry.py` and the surveyor) writes a
  row per table regardless of whether the run's steps produced the
  section.
- `db_derived.load_inputs()` picks one `surveyed_at` for every table.

### Fix

1. **Write only what was collected.** A run writes rows to a structured
   table only when one of its steps produced that section. No section →
   no rows for that `surveyed_at`. Apply the same rule to `survey_data`
   sections (closes the generic Backlog item): merge-in this run's
   sections over the prior row's, never replace with empties.
2. **Read per table, newest non-empty.** `load_inputs()` resolves
   `surveyed_at` per structured table: the newest run for the slug that
   has rows in that table (and, for activity, at least one non-NULL
   counter). Carry each table's `surveyed_at` on `DerivedInputs` so
   provenance can say "activity from 19:24:00, structure from 19:24:03".
3. Back-fill is not required; the old NULL rows become harmless once the
   reader skips them. Optionally delete NULL-only activity rows in a
   migration and say so.

### Tests

- `test_store_results_preserves_prior_stats.py` and
  `test_store_results_preserves_prior_operations.py` already cover the
  `survey_data` layer; add the structured-table twin: run a step that
  collects only `schema_info`, assert no `database_table_activity` rows
  for that `surveyed_at`, and assert `load_inputs()` still returns the
  earlier run's activity.
- A ratchet: after any single-analysis run on a fixture, `load_inputs()`
  must return the same activity totals as before the run.

### Gate

On `laz_local_adventureworks`: run `schema_inventory` alone, then
`db_classification`. The classification card must say "Derived from 4 of
4 signal families", name activity as measured (writes 761,184+), and the
evidence panel must show which run each family came from. Repeat on
coco_pharma; nothing regresses.

---

## Order and size

A first (it changes what B reads), then B. Both are Opus-sized. Neither
touches `app.js`. Expect two PRs; report each tip to the design session
for CI polling and merge.

**This section predates C, D, and E** (added below the same day, after this
section was written) and says nothing about where they fit. They are
independent of A/B and of each other — no ordering constraint connects
them — so they can be picked up in any order, by any session, in parallel
with the A→B work. Treat "two PRs" above as scoped to A/B only.

---

## C. The gap guard tests nothing (added 2026-09-27)

### Evidence

`resource_explorer/configdata/question_catalog.yaml` on main 3d7a8d2c: 201
entries, 139 with `analysis_ids: []`, and not one entry whose gap text
names a registered analysis id. Three CSV rows claimed gaps the code had
closed (column profiling, nested-column profiling, filesystem
reachability); the guard passed on all three. They were corrected by hand
on 2026-09-27 (`re/questions-false-gaps-and-hygiene`), which is the point:
a guard that is green while the rows are false is not a guard.

Two blind spots:

1. A prose `GAP:` that names no id cannot be checked against anything.
2. Several capabilities are **steps**, not analyses (`postgres_column_profile`,
   `postgres_nested_columns`, `resource_reachability`). A row naming a step
   reads as a gap forever because the guard only consults
   `analysis_catalog.yaml`.

### Fix

- The guard resolves every id-like token in an `Answering Analysis` note
  (`[a-z][a-z0-9_]+` containing an underscore) against the union of:
  analysis ids (`analysis_catalog.yaml`), step ids (the adapter's step
  registry, `survey_definition_adapter.py` `StepInfo` map), and the
  filesystem/repo step registries. A `GAP:` note that names a **registered**
  id fails the build with the message "this gap names something that
  exists: re-word as PARTIAL or name the missing piece".
- A `PARTIAL:` note must name at least one registered id, or it fails.
- **The naive regex is a false-positive risk, not a detail to skip.**
  Matching *any* underscored lowercase token in the note's prose means an
  incidental word choice (e.g. a future `GAP:` note that happens to say
  `not_collected` or `primary_key` in passing, not as an id reference)
  can coincidentally collide with a real analysis/step id and fail the
  build for the wrong reason — a guard that cries wolf gets routed around,
  which is the same failure this brief opened by describing. Require some
  marking that distinguishes a real id reference from incidental prose
  (e.g. backticks: `` `postgres_column_profile` ``) before checking it
  against the registry, rather than scanning free text for anything
  underscore-shaped. If backtick-marking is judged not worth the churn on
  existing rows, say so explicitly and accept the tradeoff in the
  IMPLEMENTED doc rather than leaving it unconsidered.
- Report, per resource type, how many rows are GAP / PARTIAL / answered, and
  write the table into the IMPLEMENTED doc so the count is visible.

### Tests

The three corrected rows, restored to their old wording in a fixture, must
fail the new guard; the current CSV must pass. Add one step-named gap and
one prose-only gap to the fixture and assert the reported reason for each.

---

## D. `_renders_text` mirrors `readEnvelope` with no cross-implementation test

### Evidence

`facts.py` `_renders_text` is a Python mirror of the JavaScript envelope
reader in `web/static/next/app.js`. Its own comment admits there is no
shared source. It drifted on the day it was written and again in Slice 21a
(the Level column shipped; `_headline_for` never read it; container-level
questions ticked ✓ on resource-level sentences for two days, caught at a
live gate, not by a test). Every existing test asserts the Python side
alone.

### Fix

The repo already runs `node` from pytest in
`tests/test_next_enrichment_persistence.py` and
`tests/test_next_journal_fidelity.py`. Reuse that pattern: a test that
feeds the same corpus of fact envelopes (measured, nothing-found,
not-established, no-reader, credential-scoped, each with and without a
headline, at each Level) to both `_renders_text` and `readEnvelope`, and
asserts the two agree on renders-or-not and on the level verdict. Export
`readEnvelope` from app.js in the same way the journal test exports its
target, or split it into a small module both import.

### Gate

None on screen; this is tests-first. The IMPLEMENTED doc lists the corpus
and the count of cases.

---

## E. The Database Analysis Survey fails on the default engine (added 2026-09-27)

### Evidence

Enqueued `survey_definition_run` for `GovActionProcess::DatabaseAnalysisSurvey`
on `laz_local_adventureworks` (run 997e93b3, 2026-09-27 21:19 UTC) with no
engine override. It failed in under two seconds:

> step 'postgres_column_profile' needs 'has_schema_inventory', produced by
> 'postgres_schema_and_stats', but 'postgres_schema_and_stats' is not one of
> this definition's own steps. Prefect can only schedule steps this
> definition authored — add 'postgres_schema_and_stats' to the definition,
> or run this definition through the local execution loop, which can
> auto-run or propose an out-of-definition producer.

**Same definition, `engine_override: "resource-explorer"`** (run 7a98803a,
21:24 UTC): succeeded in 8 seconds, wrote three survey rows and the first
Analysis-tier `step_runs` rows on any database (`postgres_column_profile`,
`postgres_nested_columns`, `db_derived`, `postgres_operations`, executor
`local`, `demanded_by` empty because the run was enqueued directly, not
from a question card). So the local loop's auto-run of out-of-definition
producers works; the Prefect path, which is the default when
`engine_override` is None, does not. A person clicking Run on the Analysis
definition gets the failure.

Observation for whoever takes this: `database_column_profiles` shows the
same shape before and after the profile step ran (1,236 rows per run, 468
with `null_fraction`/`distinct_count` and state `measured`, 768 with
`stats_basis` `not_collected`, at 19:24, 20:59 and both 21:24 runs). The
468 come from `pg_stats` read by `postgres_schema_and_stats`; whether the
profile step added sampling for anything, or wrote at all, is not visible
in the rows. Check before assuming the step's output is stored.

The Scouting definition had run on that database twenty minutes earlier and
its schema inventory was in the registry. The prerequisite resolver did not
look. Design §19.5 ("a fresh Egeria answer satisfies the prerequisite
resolver", slice 25) is the principle; this is its local form: **a fresh
stored answer satisfies the prerequisite**.

### Fix

- Default the definition run to the engine that can satisfy it, or make the
  Prefect path do what the local loop does.
- The resolver checks the registry for a prior result of the producing step
  on this slug within a freshness window (§5.1a) before declaring the
  prerequisite unmet; a hit satisfies it and is recorded as the input's
  provenance ("schema inventory from 20:59:29").
- If no fresh result exists, the Prefect path proposes running the
  producing definition first (a named next step in the run's error and in
  the pane), never a bare failure.
- The definition documents declare `requires:` alongside `produces:` so the
  generator can list unmet-by-design prerequisites at authoring time.

### Gate

On `laz_local_adventureworks`: run Scouting, then Analysis from the pane,
default engine. Analysis succeeds, the Analysis-tier `step_runs` rows
appear (`postgres_column_profile`, `postgres_nested_columns`,
`db_derived`), and the column-profile questions answer. Then run Analysis
alone on a never-scouted database and read the proposed next step instead
of the error above.
