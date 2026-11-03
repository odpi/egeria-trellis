# DESIGN — Quality steps 4 and 5: Dupe_Rows, and the schema-drift checks (2026-10-29)

**Status:** built on branch `claude/re-quality-4-5-746d01`, awaiting the gate. Steps 4 and 5 of the port order in
`DESIGN-QUALITY-Q4-PORT-PLAN-AND-CALIBRATION.md` (§5 rows 4 and 5, §5.1, §5.2, §6, §7).

**Decision (project owner, 2026-10-29):** "yes - do step 4 and 5".

**Decision (project owner, 2026-10-26):** all seven §7 recommendations of the Q4 note accepted. Those that bear here:
question 3 (Dupe_Rows has its own per-table budget of 5 minutes; `not_measured · exceeded 5 min` when it runs out;
`general_type` X columns are left out of the key and named) and question 5 (calibration evidence of record comes from a
static copy, never from a live database).

No DDL. No write to Egeria, to the shared registry or to a surveyed database: every test runs against a temp SQLite
registry and a fake or SQLite fixture connection. Nothing in this slice was measured against coco_pharma or
adventureworks (§6).

## 1. What was ported, and from where

Everything comes from the owner's TestGen clone, `~/localGit/egeria-v6/dataops-testgen`, HEAD `e8a5a906`, one docs commit on top
of the vendored commit **`3606859c`** (release 5.134.0); `git diff --stat 3606859c HEAD -- testgen/` is empty.

| Vendored file | Upstream file | What |
|---|---|---|
| `test_types/Dupe_Rows.yaml` | `testgen/template/dbsetup_test_types/test_types_Dupe_Rows.yaml` | unchanged. `run_type: QUERY`, Standard set |
| `test_types/Match_Column_Ct.yaml` | `.../test_types_Match_Column_Ct.yaml` | unchanged. `METADATA`, Table Comparison set |
| `test_types/Match_Column_Type.yaml` | `.../test_types_Match_Column_Type.yaml` | unchanged. `METADATA`, Table Comparison set |
| `test_types/Schema_Conformance.yaml` | `.../test_types_Schema_Conformance.yaml` | unchanged. `METADATA`, no selection rule, no generation template |
| `test_types/Schema_Drift.yaml` | `.../test_types_Schema_Drift.yaml` | unchanged. `METADATA`, scope `tablegroup`, Monitor set |
| `gen_query_tests/gen_Dupe_Rows.sql` | `testgen/template/gen_query_tests/gen_Dupe_Rows.sql` | unchanged. Dupe_Rows' selection: one test per profiled table, `groupby_names` = every column by position |
| `gen_query_tests/gen_Match_Column_Ct.sql` | same directory | unchanged. `threshold_value` = the table's column count from the column catalog |
| `gen_query_tests/gen_Schema_Drift.sql` | same directory | unchanged. One test per schema |

Pinned the way Q6 and B1a pin theirs: the three template types' predicates to their template's SHA-256, Match_Column_Type's to its
`selection_criteria` hash, Dupe_Rows' run statement to the SHA-256 of the vendored Postgres `test_templates` entry's grouping
subselect (`dupe_rows.GROUPING_SHA256`). `scripts/sync_quality_catalog.py --check` now also compares `run_type` and the
Postgres `test_templates` entry of a QUERY or METADATA type; against the clone it reports "in step with upstream".

The five types have no `cat_test_conditions` entry (that is what QUERY and METADATA mean upstream), so `TestType` gains a
`run_type` and empty `measure`, `test_operator` and `test_condition` for them. `run_quality_checks` now has three paths by
`run_type`: **CAT** (an aggregate in the per-table SELECT, as before), **QUERY** (Dupe_Rows) and **METADATA** (the drift
checks: no connection).

## 2. Step 4: Dupe_Rows

- **A second execution path.** Its statement is RE's select list over the vendored grouping subselect, verbatim:

  ```sql
  SELECT COALESCE(SUM(record_ct), 0) AS rows_in_groups, COUNT(*) AS group_ct,
         (SELECT COUNT(*) FROM "s"."t") AS table_ct
  FROM ( SELECT <key>, COUNT(*) as record_ct FROM "s"."t" WHERE 1=1 GROUP BY <key> HAVING COUNT(*) > 1 ) test
  ```

  Upstream returns `SUM(record_ct)` as its measure and fails on `COUNT(*) > skip_errors`; both are kept (the rows are the
  measure, the groups are what the threshold counts). `table_ct` is RE's addition so the result can say what it counted
  against the table's rows. It is a scalar subselect in the same statement, one transaction and one timeout.
- **The key** is every column in the stored inventory's order, less the columns of general_type X (owner, question 3), which
  are kept in `excluded_columns` and named in the result's sentence: "…; left out of the group key, no equality: payload
  (general type X)". Upstream does not exclude any column. A table whose every column is X has no key: `not_established`,
  never a pass.
- **Its own budget**: `ProfileBounds.dupe_rows_budget_seconds`, 300 s, per table. The statement's timeout is the budget, so
  Postgres cancels what is not done. A cancellation, or any error that arrives with the clock past the budget, is
  `not_measured` with "exceeded 5 min · duplicates not counted"; nothing partial is kept, no measure is written. A
  privilege error is `not_permitted` first, as the profile classifies. The clock is injectable (`run_quality_checks(clock=)`);
  the tests fake it.
- **Words.** No duplicates: "no duplicate rows: 0 duplicate row groups in 1000 rows". Duplicates: "2 duplicate row groups
  holding 5 of 1000 rows (fails when duplicate row groups > 0)". Could not run: the `not_measured` sentence above. These are
  three different states, not one number.
- **Validation at run time.** Each key column must still be in the stored inventory (identifiers are quoted; nothing from
  a person or Egeria reaches the statement unless it names a column RE holds); `skip_errors` a whole number ≥ 0; a column
  that left the inventory makes the check `not_established` and runs nothing.
- **Not an opt-in.** Dupe_Rows is TestGen's Standard set, as the Q4 plan puts it (a B0 type); it is proposed for every
  profiled table, like Row_Ct_Pct. A table that was not profiled (budget, privilege, view) gets no proposal, per the
  owner's 2026-10-21 rule: the rule reads "unknown" and propose reports `not_established` with the profile's reason.

## 3. Step 5: the drift checks read the stored surveys

TestGen runs these over its own `data_column_chars` and `data_structure_log`. RE has neither; it has stored surveys.
No check here opens a connection (the tests pass a connection factory that fails if called).

| Type | Reads | Result |
|---|---|---|
| Schema_Drift | `db_derived.derive_schema_diff` (the column half of `schema_diff`: added, dropped, retyped between the two newest surveys, verbatim) and the two surveys' table lists (the table half `db_change_rates` reads as `schema_churn`) | per table: pass with measure 0, or fail with the count of changes |
| Match_Column_Ct | the newest survey's column count for the table, against `threshold_value` taken at proposal | pass when equal, fail otherwise (`<>`, as upstream) |
| Match_Column_Type | the newest survey's column, its `general_type` (the vendored CASE, `general_type.py`), against `baseline_value` | pass when equal, fail otherwise |
| Schema_Conformance | a declared schema (an ODCS contract) | always `not_established`: RE holds none |

**A first survey is not "no drift".** With one stored survey, or none since the baseline, every check answers
`not_established` with a sentence saying so. Match_Column_Ct and Match_Column_Type keep `baseline_survey_at`, the stamp of the
survey the baseline was taken from; if the newest survey is not later than that, there is nothing to compare, and the
result says "No survey has been stored since the baseline (…)". A passing result names the survey it compared with.

Surveys are chosen as `derive_schema_diff` chooses them (`db_derived._snapshot_keys`), so a survey marked invalid is never a
baseline or a comparison.

## 4. The words for the drift cases — PROPOSED, the project owner decides

The sentences live in one place, `schema_drift.WORDS`, so a ruling is one edit. Result states are RE's existing ones
(`pass`, `fail` at the check's severity, `not_established`); the Curate and annotation vocabulary is unchanged
(`passed`, `failed`, `not_established`).

| Drift case | Result state | Sentence |
|---|---|---|
| Only one survey stored | `not_established` | Only one survey of this database is stored, so there is nothing to compare it with. This is not a finding that the schema is unchanged. |
| No survey since the baseline (Match_Column_*) | `not_established` | No survey has been stored since the baseline (*stamp*), so there is nothing to compare with yet. This is not a finding that the table is unchanged. |
| No change between the two newest surveys | `pass` | No column was added, dropped or retyped between the surveys of *old* and *new*. |
| Column added | `fail` | *n* column(s) added since *old*: *names*. |
| Column dropped | `fail` | *n* column(s) dropped since *old*: *names*. |
| **Column retyped** (any change of the stored database type) | `fail` | *n* column(s) retyped since *old*: price (integer → numeric). |
| Table added | `fail` | Table added since *old*: it has *n* columns. |
| Table dropped | `fail` | Table dropped since *old*: it had *n* columns. |
| Table in neither survey | `not_established` | The table is in neither of the two newest surveys (*old*, *new*), so its drift is not known. |
| Match_Column_Ct equal / differs / table gone | `pass` / `fail` / `fail` | *n* columns, as at the baseline… / *n* columns in the survey of *new*, *b* at the baseline. / The table is not in the survey of *new* (0 columns); *b* at the baseline. |
| Match_Column_Type same / changed / column gone | `pass` / `fail` / `fail` | General type *g* as at the baseline… A change of database type inside the same general type is Schema_Drift's to report. / General type *g* in the survey of *new*, *b* at the baseline. / The column is not in the survey of *new*… |
| Schema_Conformance | `not_established` | Resource Explorer holds no declared schema (a data contract) for this table, so there is nothing to conform to. |

"Retyped" is `schema_diff`'s own word and arrow (`columns_retyped`, "old → new"), used on the screen already. Choices for the
owner: (a) a table added or dropped fails (TestGen flags both with result code 0); the alternative is `warning` or
`logged`. (b) A column retyped fails at the check's severity (Fail by default, as upstream); a retype inside one general type
(integer → bigint) is a drift finding for Schema_Drift and a pass for Match_Column_Type.

## 5. Departures from TestGen and from the plan, with the reason

1. **Schema_Drift is one check per table, not one per schema.** Upstream's definition is `tablegroup` scope, but its result
   rows are per table (`GROUP BY dsl.table_name`). A schema-scope check needs a key shape (`schema::Type`) that Curate, the
   run step and the publish all assume has a table; per table keeps all of them as they are. Cost: one more row per table in
   Curate (Row_Ct_Pct and Dupe_Rows already are). Alternative if the owner prefers one per database: a roll-up in Curate,
   not a different check.
2. **Schema_Drift's window is the two newest stored surveys**, not "since the previous test run" (`prev_test.last_run_time`).
   RE has no test-run clock for it; its drift is differenced between snapshots, as `schema_diff` already does.
3. **Match_Column_Ct and Match_Column_Type are opt-in per table** (the Table Comparison watch of B1a). The brief says §7.2
   covers "Table Comparison types, not these"; but both vendored files say `generation_sets: Table Comparison`, and a
   column-type check on every column of every table is exactly the flood §7.2 was written to prevent. They ride the existing
   flag with no code of their own. If the owner wants them proposed everywhere, the change is one line (`is_table_comparison`).
4. **Their baselines come from the stored survey**, not a TestGen profile run: the count is `database_columns` rows for the
   table in the newest survey; `baseline_value` is the profile's `general_type`, as upstream's `default_parm_values`.
5. **Match_Column_Type is also withheld from a table RE could not profile** (`state` not measured). Upstream selects a
   non-X column whose profile read failed; the owner's 2026-10-21 rule is that an incomplete table gets no proposals, and it
   wins.
6. **Schema_Conformance is vendored but inert.** It compares with a declared schema from an ODCS contract, which upstream
   generates from nothing (no selection rule, no template) and which RE does not hold. It is never proposed
   (`TestType.manual_only`) and, if a verdict names it, always `not_established` with the sentence above. It becomes
   useful when RE can hold a declared schema; that needs its own design.
7. **Not published as DataQualityRules yet.** `published_threshold` refuses a QUERY or METADATA type with a sentence, so the
   Q5 publish skips these with `unverified` and the reason rather than writing a rule with a guessed threshold. Dupe_Rows could
   publish (`mustBeLessOrEqualTo` on `skip_errors`); it is left for the owner to ask for.
8. **TestGen-only import** of a Dupe_Rows or drift definition is not built: it lands as `not_established` with that sentence,
   rather than carrying parameters nothing can run.
9. **Nothing in `db_change_rates` beyond the table lists is read.** The brief names it; the row-activity half has no bearing
   on a schema check.

## 6. Cost, and calibration

**Cost (Q4 §5.2): no measurement.** There is no local static copy of coco_pharma or adventureworks on this machine, and the
rules forbid measuring on a live database, so no number is claimed. By construction: the drift checks cost no database time
(registry reads). Dupe_Rows is a hash or sort over the whole table, the most expensive read RE would run, and it is bounded
by its own 300 s per table; a table too large for it ends `not_measured · exceeded 5 min`, which is the designed outcome, not a
failure. The expected time on the largest tables is to be measured on the static restore of recommendation 5 before the gate
can be called closed.

**Calibration (Q4 §6).** The compare harness lives in `egeria-workspaces-fs` and is outside this repo; it reads the compared
set from RE's vendored `test_types/` (§6.2 item 4), so the five are picked up without a change here, but no harness run was
made (no TestGen runtime, no static copy). Per type:

| Type | Calibratable | Note |
|---|---|---|
| Dupe_Rows | yes, on a static copy | measure = rows in duplicate groups, exact. The key differs by design where a table has an X column: that row is an expected asymmetry, listed once, not counted |
| Match_Column_Ct | partly | TestGen's baseline is its profile run's column count, RE's is the survey's; equal on the same static copy. Result layer only after a second survey |
| Match_Column_Type | partly | same; `general_type` is the same CASE |
| Schema_Drift | no | TestGen's result is a window since its previous test run over its own `data_structure_log`; RE's is two surveys. Different windows cannot be compared row for row |
| Schema_Conformance | no | TestGen never generates it and RE cannot run it |

What the tests do check against TestGen is equivalence on a fixture: TestGen's own Postgres statement for Dupe_Rows, filled
with the vendored template, and RE's statement run on the same SQLite fixture table give the same rows-in-groups and the same
verdict (`test_re_s_statement_counts_what_testgen_s_counts_on_a_fixture`).

## 7. Tests

`tests/test_quality_steps_4_5_dupe_rows_and_drift.py` (58): the vendored entries and pins; selection in three-valued logic;
the key, the X exclusion and its naming; TestGen equivalence on a fixture; budget exhaustion with a faked clock (cancelled,
late error, privilege error first, zero budget, the minutes in the words); the run through `run_quality_checks` (its own
statement, not the per-table SELECT; a vanished key column; bad parameters; a view); a first survey, each drift case,
the dotted-name case, an invalid survey, the baseline checks, Schema_Conformance, and that no drift check opens a connection.
Three existing test files changed to account for the new types: the ported-set test, the "every type has a condition" test
(skips QUERY and METADATA), the proposal counts (8 → 12 on the fixture: Dupe_Rows and Schema_Drift on the two profiled
tables), and the TestGen-only import example (its "unported" type was Dupe_Rows; now Table_Freshness).

## 8. Open

- The words in §4, and the two choices after the table.
- Whether Match_Column_* stay opt-in (departure 3).
- Per-table or per-database Schema_Drift (departure 1).
- The Dupe_Rows time on the largest tables, from the static copy.
- Publishing Dupe_Rows as a DataQualityRule (departure 7).
