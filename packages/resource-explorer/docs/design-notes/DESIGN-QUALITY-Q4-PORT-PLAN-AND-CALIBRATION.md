# DESIGN — Quality slice Q4: the port plan on the TestGen inventory, and the calibration harness (2026-10-26)

**Status:** design only. No code, no live runs, no writes to Egeria or the registry were made to
write it. Builds on `DESIGN-QUALITY-AT-SCALE-AND-TESTGEN.md` (the slice table, §3),
`INVENTORY-TESTGEN-TEST-TYPES-MAPPED.md` (the 89 types) and `DESIGN-VALUE-PROFILE-AND-QUALITY-CHECKS.md`
(slice 1 as built). Sources read: RE's vendored catalogue (`configdata/quality/testgen/`, TestGen
5.134.0 at `3606859c`), the quality steps (`surveyors/database/quality/`), the registry DDL, TestGen's
`dbsetup_test_types/*.yaml` and Postgres profiling SQL in the owner's clone, the TestGen evaluation
(`dataops-testgen/TRELLIS-EVALUATION.md`) and the compare harness in `egeria-workspaces-fs`
(`compose-configs/optional-associated-runtimes/testgen/compare/compare_re_testgen.py`).

## 1. Which Q4 this is

The record names Q4 two ways:

- the 2026-10-24 coordinator handoff: "the TestGen test-type inventory, design task, no code";
- the 2026-10-21 slice table (`DESIGN-QUALITY-AT-SCALE-AND-TESTGEN.md` §3): the inventory **plus**
  the compare harness documented, a calibration report, and TestGen-only tests imported as
  `origin=testgen` proposals.

The inventory was written on 2026-10-21 and is not redone here. **This note takes Q4 to be the plan
built on it, plus the design of the compare harness**: which families RE ports next and in what
order, what each needs, what stays out, and how a ported check is calibrated against TestGen's
result on the same table. The calibration report on coco_pharma and the accepted TestGen-only
proposal in the 10-21 gate are build-and-run work; this note specifies them and leaves them to the
slices that build the pieces (§6.5).

## 2. Rulings this note works inside

**Decision (project owner, 2026-10-19):** D1, TestGen's catalogue is vendored as data and RE
executes it; TestGen's engine, metadata database, scheduler and UI are not run as a service.

**Decision (project owner, 2026-10-19):** D2, RE is not the coordinator; Egeria coordinates through
a Survey Definition, or RE's executor runs the steps.

**Decision (project owner, 2026-10-19):** D3, checks are proposed in RE and accepted into Egeria as
`DataQualityRule`s.

**Decision (project owner, 2026-10-21):** TestGen's live runtime is used for calibration only:
nothing publishes from it and it runs on no schedule. Its extra checks and user-defined rules are a
backlog stage, decided after the inventory.

**Decision (project owner, 2026-10-21):** large tables are not refused. Whole-table counts as TestGen
reads them; sampling only for the measures TestGen samples, with `sample_ratio` recorded and shown.

**Decision (project owner, 2026-10-21):** the value-profile time budget is 5 minutes per table. A
table that exceeds it is "profile incomplete · exceeded 5 min · counts not measured" and gets no
proposals.

Q5 (`DataQualityRule` publish through the generic governance-definition API) and Q6 (Recency) keep
their places. Nothing below moves them.

## 3. What RE already stores that later families lean on

The inventory says B1 "needs run history". RE keeps more history than that phrase suggests:

| Store | What it holds | Shape |
|---|---|---|
| `database_value_profiles` | every profile run, per column: `record_ct`, `value_ct`, `distinct_value_ct`, `null_value_ct`, `zero_length_ct`, `filled_value_ct`, `min_date`, `max_date`, `future_date_ct`, `general_type`, `sample_ratio`, `state`, `reason` | one batch per `surveyed_at`, unique on (database, `surveyed_at`, source, schema, table, column); never overwritten |
| `database_quality_proposals` | each propose run, with `profile_surveyed_at`, `params_json`, `origin` (`vendored` or `re`) and `catalogue_ref` | one batch per run; the latest batch wins, nothing deleted |
| `database_quality_verdicts` | accept, reject and retire, with `params_json` **as accepted** | append-only, latest per `check_key` wins |
| `database_quality_results` | one row per accepted check per run: `measure`, `status`, threshold applied, `baseline_profile_surveyed_at`, `rule_source`, `rule_guid` | one row per (run, `check_key`): a time series per check |
| `db_change_rates`, `schema_diff`, `database_table_activity` | rows inserted, updated, deleted per table between stored surveys; column drift; table activity | differenced between two stored survey snapshots; "insufficient history" when only one exists |

So RE has three kinds of history today: a **frozen baseline** (the accepted `params_json`, with the
profile run it came from), a **profile series** (every profile batch), and a **result series**
(every run's measure per check). Section 4 shows that most of B1 needs only the first.

## 4. A correction to the inventory: B1 is mostly a frozen baseline, not history

Read type by type, 20 of B1's 25 types are TestGen's **Table Comparison** generation set (`Match_*`).
Each takes a baseline from the profile when the test is generated (`default_parm_values`, for
example `baseline_ct = null_value_ct`), measures the same thing over the whole table at run time,
and fails when they differ. That is exactly slice 1's accept-time baseline: the verdict stores
`params_json` as accepted, and the run step reads it and records `baseline_profile_surveyed_at`. No
prior-run comparison is involved. What separates the 25 is **which profile measure the baseline
comes from**:

| Sub-family | Types | Baseline from | Needs |
|---|---|---|---|
| **B1a**, measures RE already profiles (6) | `Match_Distinct_Ct`, `Match_Null_Ct`, `Match_Min_Date`, `Match_Max_Date`, `Match_Zero_Length_Ct`, `Match_Row_Ct` | `distinct_value_ct`, `null_value_ct`, `min_date`, `max_date`, `zero_length_ct`, `record_ct` | nothing new. `Match_Row_Ct`'s selection is a generator template (`gen_Match_Row_Ct.sql`), ported as a pinned Python rule the way `Row_Ct_Pct` was |
| **B1b**, new whole-table measures (13) | `Match_Min_Val`, `Match_Max_Val`, `Match_Sum`, `Match_Stdev`, `Match_Zero_Ct`, `Match_Fractional_Sum`, `Match_Min_Length`, `Match_Max_Length`, `Match_Total_Length`, `Match_True_Ct`, `Match_Distinct_Date_Ct`, `Match_Epoch_Sum`, `Daily_Record_Ct` | `min_value`, `max_value`, `sum_value`, `avg_value`, `stdev_value`, `zero_value_ct`, `fractional_sum`, `min_length`, `max_length`, `avg_length`, `boolean_true_ct`, `date_days_present`, `epoch_sum` | the profile's numeric, length, boolean and date-days measures; all are in TestGen's `whole_table_measures` block, never sampled. `Daily_Record_Ct` is Standard-set, not a match at all: it reads `date_days_present` and the date span |
| **B1c**, sampled measures (3) | `Match_Lead_Space_Ct`, `Match_Upper_Case_Ct`, `Match_Non_Printing_Ct` | `lead_space_ct`, `upper_case_ct`, `non_printing_ct` | TestGen computes these in its `sampleable_measures` block (`TABLESAMPLE BERNOULLI … REPEATABLE (64)`), so they belong with family D and profile slice 2 |
| **B1d**, a true series (3) | `Freshness_Trend`, `Volume_Trend`, `Table_Freshness` | TestGen's Monitor set: a lookback over prior results with SARIMAX-predicted bounds, or a fingerprint `custom_query` | the result series RE already keeps, plus a bound rule that is not SARIMAX (§5, open question 4) |

Two consequences. B1a is portable now with no new measurement, the same as B0. And the "compare to
the prior run" capability the inventory names as one unlock is really only B1d's three. The
inventory's table stays as written; this section is the reading the port order uses. A one-line
pointer from the inventory to this section is part of this change's follow-up, not this note.

**Selection is not the obstacle for Table Comparison types; volume is.** Their selection rules are
broad (`Match_Null_Ct`: every column with `record_ct > 0`), and TestGen does not generate them in
its Standard set: a person opts a table group into Table Comparison. Proposing all six B1a types on
every column would put six more rows per column into Curate. See open question 2.

## 5. Port order

Each step is a slice of its own with its own gate. Slice names after Q6 are proposals for the owner
to number.

| Order | What | Types | New data | DDL | Cost at run time |
|---|---|---|---|---|---|
| 1 | **Q6** as planned: Recency, with the date branch of the functional-type inference ported as data; `Future_Date_1Y` (B0) rides with it | 2 | `before_20yr_date_ct` (and `before_5yr_date_ct` if the whole date branch is ported); both whole-table | two nullable columns on `database_value_profiles` | two more aggregate expressions per date column in the existing per-table read |
| 2 | **Baseline match, measures RE has** (B1a) as an opt-in set per table | 6 | none | none | one more aggregate per check in the existing per-table SELECT |
| 3 | **Profile 1b**: the whole-table numeric, length, boolean and date-days measures, then B1b | 13 | 13 measures | 13 nullable columns | numeric `STDDEV` and `COUNT(DISTINCT date)` are the heavy ones; all inside the 5-minute budget or "profile incomplete" |
| 4 | **Dupe_Rows** (B0) | 1 | none | none | a `GROUP BY` over every column of the table, the most expensive check RE would run (§5.2) |
| 5 | **Schema drift as checks** (G) | 4 | none: reads `schema_diff` and `db_change_rates` | none | zero-fetch |
| 6 | **Functional-type inference, the rest** (C) | 19 | `functional_data_type`, `functional_table_type` and the measures the rules read | one or two columns | inference is post-profile SQL over the profile, zero-fetch |
| 7 | **Profile slice 2, sampled text shape** (D, B1c) | 8 | patterns, placeholder and character counts, sampled, `sample_ratio` set | several columns | one `TABLESAMPLE` read per text column |
| 8 | **Monitors** (B1d) and **hashes** (B2) | 12 | the result series (have); `multiset_hash`, `keyed_hash`, `frequent_values`, `frequent_patterns` | hash columns; a frequent-values child table | hashes are whole-table and off by default in TestGen too |

Row 1 is built (2026-10-26): `DESIGN-QUALITY-Q6-RECENCY.md` records what was ported from which TestGen file,
and §2 there corrects §5.1's reason for the order (Historical Date cannot pre-empt Future or Schedule Date; the
statements around the CASE can).

Row 2 is built (2026-10-27), with the TestGen-only import of §6.5: `DESIGN-QUALITY-B1A-BASELINE-MATCH.md`. §4 there
records where "no new measure, no DDL" needed more than this table says: the two date matches measure text, which
the results table's numeric `measure` column cannot hold as text, and all six carry upstream's `null_measure_fails`.

Row 3 is built (2026-10-28), not deployed: `DESIGN-QUALITY-PROFILE-1B.md`, DDL record `../ddl-value-profile-1b.md`. It
has twelve types, not thirteen: Match_Epoch_Sum's `epoch_sum` is taken upstream only for a hashed column, so it moves
to row 8. Its thirteenth measure is `before_5yr_date_ct`, deferred from row 1. It also adds
`database_quality_results.measure_text` and UTC for both reads (B1a's questions 1 and 2, built on their
recommendations, owner to confirm).

**Out of scope for proposals:** family H (5, manual-only in TestGen), family E (8, cross-table and
person-authored), family F (4, custom SQL). They need a check editor, not a selection rule, and F
is the owner's "define your own rules" backlog stage. F carries an extra warning from RE's own
record: validating person-authored SQL text did not hold as a read-only guard through three review
rounds, so F needs an execution boundary (a read-only role and transaction), not a text checker.

### 5.1 Why this order

- **Q6 first** because it is already slotted and its one dependency is small. Recency's selection
  excludes `functional_data_type IN ('Future Date', 'Schedule Date')`. In TestGen's
  `functional_datatype.sql` those two come from `future_date_ct / value_ct` (≥ 95 % and ≥ 50 %), both
  of which RE measures, but the CASE is ordered and `'Historical Date'` (`before_20yr_date_ct /
  value_ct ≥ 75 %`) is decided first. Porting only the two branches Recency names would label a
  mostly-historical column differently from TestGen, and calibration would catch it as a selection
  disagreement. So Q6 ports the **date branch in its order**, as data, with the measure it reads.
  The Q6 builder reads the whole 629-line file for any earlier statement that sets a date column's
  type by name before this CASE.
- **`Future_Date_1Y` with Q6** because it is the same columns, the same dimension (Timeliness), and
  its selection reads only `general_type`, `future_date_ct` and `max_date`. Alone it is too small to
  carry a gate.
- **B1a second** because it is free: no measure, no DDL, and the baseline mechanism exists. It is
  also the first family that tests the run step against data that moves (§6.3).
- **Profile 1b before C** because nine of C's types (`Min_Val`, `Avg_Shift`, `Incr_Avg_Shift`,
  `Outlier_Pct_*`, `Variability_*`, `Alpha_Trunc`, `Dec_Trunc`) read the numeric and length measures
  1b adds. Doing 1b first means C costs inference only.
- **Dupe_Rows apart from B0** because it is a QUERY type: its generator emits one test per table with
  every column as the group key, and its run is its own statement, not an aggregate in the per-table
  SELECT. RE's run step executes CAT conditions today, so this is a second execution path.
- **G early** because it is zero-fetch over analyses RE already runs; it is cheap to build but needs
  the result vocabulary for "column retyped" decided, so it is fifth rather than first.

### 5.2 Cost notes

- **Per-table budget.** Every new measure joins the existing one-transaction-per-table read, under
  the 5-minute budget. A slice that adds measures states the expected added time on the largest
  coco_pharma and adventureworks tables, from a local static copy, before its gate.
- **Dupe_Rows.** `GROUP BY` every column is a sort or hash over the whole table, usually larger than
  the value-profile read. It cannot sit inside the profile budget. Proposal: Dupe_Rows has its own
  per-table run budget, its result is `not_measured · exceeded N min` when it runs out, and columns
  of `general_type X` (no equality, for example `json`) are left out of the group key with the
  exclusion named on the result. See open question 3.
- **DDL.** Steps 1, 3, 6, 7 and 8 add nullable columns to `database_value_profiles`. Each PR carries
  the DDL deployment record (what changes, applied at the next 8813 restart, what reads NULL in old
  rows). Old profile rows keep NULL in the new columns, and the selection rules already return
  `not_established` on a NULL input, so an old profile never proposes a new check by accident.
- **Sampling.** Only step 7 samples. It uses TestGen's percent rule and the same `REPEATABLE (64)`
  seed, which matters for calibration (§6.3).

## 6. The compare harness: calibrating a ported check against TestGen

### 6.1 Where it lives and who runs it

The harness stays where it is, in `egeria-workspaces-fs` beside TestGen's compose files: a person
runs `seed_examples.py`, TestGen's profile, generate and test, `export_testgen.py`, then
`compare_re_testgen.py`. It reads both sides and writes files. It is outside RE, and it never runs
from RE's scheduler or an engine action (D2, and the calibration-only ruling). TestGen keeps its
`surveyor` credential in its own runtime.

### 6.2 Inputs

1. **TestGen's side:** the export JSON for one project (= one database): profile results, test
   definitions with parameters, and test results, each keyed to RE's `check_key`
   (`schema.table.column::Test_Type`, or `schema.table::Test_Type`).
2. **RE's side:** the latest batch per database of `database_value_profiles`,
   `database_quality_proposals` and `database_quality_results`, read-only.
3. **Run facts, new:** both sides' read times (TestGen's profile and test run times; RE's
   `surveyed_at` for each table), TestGen's running version, RE's vendored commit from
   `PROVENANCE.md`, and RE's git SHA. A version mismatch between TestGen's runtime and the vendored
   commit is reported as **catalogue skew** at the top of the report, because then a disagreement
   may be TestGen's change rather than RE's port.
4. **Types:** the compared set is read from RE's vendored `test_types/` directory (every type RE
   has ported), not the hard-coded seven the script carries today. `--types` stays for narrowing.

### 6.3 The match rule, by layer

The harness already compares in three layers and keeps absence apart from agreement (`re_only`,
`testgen_only`, `not_comparable`). This note fixes the rule each layer applies:

| Layer | Compared | Match when |
|---|---|---|
| Profile | each measure a ported type reads, per column | equal under the tolerance below |
| Selection | proposed by RE vs generated by TestGen, per `check_key` | both or neither. RE's `not_established` against a TestGen test is "RE could not decide", reported apart from "RE said no" |
| Parameters | RE's default `params_json` vs TestGen's baseline and threshold | equal under the tolerance below |
| Result | RE's `status` vs TestGen's `result_status` (`Passed`→pass, `Failed`→fail, `Warning`→warning, `Error`→error, `Log`→logged) | equal. TestGen's `result_code = -1` (training) and RE's `not_established` and `not_measured` are `not_comparable`, never agreement |

**Tolerance.**

- **Counts and dates: exact.** Both read the whole table, so on the same data they must agree to the
  row. Dates compare to the second, as the script does now.
- **Floating measures** (`avg_*`, `stdev_value`, `fractional_sum`, Cohen's H thresholds): relative
  difference ≤ 1e-9, since both compute in Postgres but may cast differently.
- **Sampled measures** (step 7 onward): exact as well, on the same physical table, because both use
  `TABLESAMPLE BERNOULLI (p) REPEATABLE (64)` with the same `p`. If RE's `sample_ratio` differs from
  TestGen's percent, the row is `not_comparable · different sample`, never a disagreement.

**Data that moves.** The two engines read at different times. On coco_pharma the live simulator adds
rows every few minutes, so two profiles taken an hour apart differ in counts and that difference is
not a finding. Therefore:

- **Evidence of record comes from a static database:** a throwaway copy restored from one dump,
  with no writer, profiled by both sides. On it, every disagreement under the rule above is a
  finding about RE's port.
- A run against a live database is allowed and labelled **indicative** at the top of the report.
  Where `record_ct` differs between the two sides for a table, every count-based row of that table
  is `not_comparable · data moved between reads` instead of a disagreement. Counts are never
  silently scaled to fit.

### 6.4 Where the evidence lands

- The harness's output (`report.md`, `profile.csv`, `checks.csv`, `results.csv`) stays in its
  `out/` directory; that is a working copy.
- **Evidence of record** is committed with the slice that ports the types:
  `docs/design-notes/evidence/CALIBRATION-<slice>-<database>-<YYYY-MM-DD>.md`, holding the run facts
  from §6.2, the per-layer counts (agree, disagree, `not_comparable`, one side only), and **every**
  disagreement with its explanation: a fix in the port, an intended difference with its reason, or
  TestGen's own behaviour. The CSVs are attached only when they are small.
- No registry table and no Egeria write. Calibration is about RE's port, not the surveyed database,
  so it is not a survey result and has nothing to publish.
- **A port's calibration gate:** on the static copy, zero unexplained disagreements at every layer
  for the types the slice adds. Expected asymmetries are listed once in the evidence file and not
  counted, for example `Row_Ct_Pct`, which TestGen never generates because it is manual-only there,
  and the Table Comparison types, which TestGen generates only for an opted-in table group.

### 6.5 TestGen-only tests as proposals

`DESIGN-QUALITY-AT-SCALE-AND-TESTGEN.md` §2.4 says a TestGen test RE did not propose becomes a
proposal with `origin=testgen`, never a verdict. Fixed here:

- **Only for a type RE has vendored.** RE runs every result itself, so a proposal for a type it
  cannot execute would be a check that can never run. A TestGen-only test of an unported type is a
  row in the calibration evidence, which feeds the port order.
- **The import is RE's, not the harness's.** The harness stays read-only on both sides. An RE CLI
  command reads the export JSON and writes proposal rows through RE's own registry code: `origin`
  `testgen` (a third value beside `vendored` and `re`), `catalogue_ref` the vendored type, and
  `reason` naming TestGen's test definition id and run.
- **Verdicts win.** A `check_key` with a live rejected verdict is not imported; one with a live
  accepted verdict is a match, not a new row. Parameters come from TestGen's test definition.
- **The 10-21 gate** (one TestGen-only proposal accepted in Curate and run by RE) belongs to the
  slice that builds the import. The natural first case is B1a, step 2: TestGen generates
  `Match_*` for an opted-in table group, RE has ported the type, and a person accepts one.

## 7. Questions for the project owner

**Decision (project owner, 2026-10-26):** all seven recommendations below are accepted as written.

1. **Is this the Q4 you meant?** The plan on the inventory plus the harness design, with the coco
   calibration report and the TestGen-only import built in the slices that port types (§1, §6.5).
   *Recommendation:* yes. The inventory is written; the build-and-run parts of the 10-21 gate depend
   on pieces that do not exist yet (the import command, a second ported family).
2. **Table Comparison types: opt-in per table, or proposed everywhere?** TestGen generates them only
   for a table group a person opted in. Proposing six B1a types on every column floods Curate.
   *Recommendation:* opt-in per table, from the table's card ("watch this table for drift"), and
   proposals only for opted-in tables. This keeps TestGen's own default.
3. **Dupe_Rows: its own run budget, and how long?** Its `GROUP BY` every column can exceed the
   profile's 5 minutes on a large table. *Recommendation:* its own per-table budget of 5 minutes,
   the same number for one rule, with `not_measured · exceeded 5 min` when it runs out, and
   `general_type X` columns left out of the key and named.
4. **Monitors without SARIMAX?** `Volume_Trend` and `Freshness_Trend` predict bounds with SARIMAX
   from TestGen's history. *Recommendation:* defer B1d past step 8, and when it comes, use a simple
   rule over RE's result series (the last N results, min/max with a stated margin), recorded as the
   rule on the result, not called a prediction. Adding statsmodels to RE for three types is not worth
   it now.
5. **Evidence of record on a static copy?** *Recommendation:* yes: a throwaway restore of
   coco_pharma and of adventureworks, with no writer, kept only for the calibration run. Live runs
   are indicative.
6. **Who builds the TestGen-only import?** *Recommendation:* the slice that ports B1a (step 2), since
   that is the first family TestGen generates and RE can run, and the 10-21 gate's "accepted in
   Curate and run by RE" is provable there.
7. **F, custom SQL: confirm it is out until a check editor exists, with an execution boundary rather
   than a SQL text check.** *Recommendation:* confirm. It is the owner's backlog stage and needs its
   own design.
