# DESIGN — Quality profile 1b: the whole-table numeric, length, boolean and date-days measures, then B1b (2026-10-28)

**Status:** built on branch `claude/re-quality-profile-1b-on-q5`, stacked on Q5's DataQualityRule publish
(`claude/re-quality-q5-dqrule-publish`; §4.6). This is Q4 port order step 3
(`DESIGN-QUALITY-Q4-PORT-PLAN-AND-CALIBRATION.md` §5 row 3, the B1b row of §4). It carries the DDL
(`../ddl-value-profile-1b.md`) and is not deployed: nothing has run against the shared registry.

No write to Egeria. Every test runs against a temp SQLite registry and a fake target connection; the Postgres-tier
migration test is written for the gate's own test schema and skipped here. Calibration against a TestGen run was not
done (§6).

## 1. The questions this step inherited, and their answers

The project owner said all earlier questions were answered (relayed by the coordinator, 2026-10-28: "I answered all
the questions previously - we should be able to move forward?"). The records searched: the design notes, the `RULING-*`
files, and the Architect Session's transcript (whose last activity is 2026-10-25, before the B1a questions existed
on 2026-10-27).

| Question | Answer | Source |
|---|---|---|
| B1a §7 Q1: a `measure_text` column with this step's DDL? | **Yes, built** (`database_quality_results.measure_text`) | **Assumed: owner to confirm.** No recorded answer; the B1a note's recommendation |
| B1a §7 Q2: session time zone UTC for both the profile read and the check run? | **Yes, built** (`SET LOCAL TIME ZONE 'UTC'` in `begin_read`, which both use) | **Assumed: owner to confirm.** No recorded answer; the B1a note's recommendation |
| B1a §7 Q3: opt-in per table, or per table group as TestGen? | **Per table**, unchanged | **Recorded.** Q4 §7 question 2 ("opt-in per table, from the table's card"), **Decision (project owner, 2026-10-26):** all seven §7 recommendations accepted |
| Q6 §5: `before_5yr_date_ct` with profile 1b? | **Measured** (a nullable column and its vendored line) | The coordinator's brief for this step names it. The recommendation's second half (port the date CASE's later branches with it) is **not built**: see §5.2 and question 1 |

## 2. What was built

| Piece | Where |
|---|---|
| Thirteen measure lines from upstream's `project_profiling_query.sql`, verbatim, under upstream's flag names (`is_type_N`, `is_N_decimal`, `is_N_exact_sum`, `is_type_B`, and in the existing `is_type_A` and `is_type_D` blocks) | `configdata/quality/testgen/postgresql/profile_measures.sql` |
| Upstream's `logical_type` and `is_decimal` CASEs, verbatim, and their Python port, pinned | `configdata/.../postgresql/numeric_chars.sql`, `quality/general_type.py` |
| One `information_schema.columns` read per profile run for those two CASEs (the stored inventory keeps no `numeric_scale`) | `value_profile.read_numeric_chars`, `ColumnChars` |
| `{COL_NUM}` / `{COLUMN_NUM}`: the column, read through `::NUMERIC` for money, as upstream's Postgres flavour | `catalogue.column_num` |
| Twelve types vendored unchanged: `Match_Min_Val`, `Match_Max_Val`, `Match_Sum`, `Match_Stdev`, `Match_Zero_Ct`, `Match_Fractional_Sum`, `Match_Min_Length`, `Match_Max_Length`, `Match_Total_Length`, `Match_True_Ct`, `Match_Distinct_Date_Ct`, `Daily_Record_Ct` | `configdata/.../test_types/`, `catalogue.B1B_TEST_TYPES` |
| Their selection rules, each pinned to its fragment's SHA-256 | `catalogue.PREDICATES` |
| Match_Sum's and Match_Total_Length's defaults (expressions), pinned | `catalogue.DEFAULT_PORTS` |
| The five conditions that are an expression, not one placeholder, pinned; `NOT BETWEEN` (`Between`, ODCS `mustBeBetween`) | `catalogue.CONDITION_PORTS` |
| Exact comparison for the two NUMERIC sums (Python `Decimal`, Postgres's half-away-from-zero rounding) | `catalogue.as_decimal`, `SumTolerance`, `run_checks._conclude` |
| Match_Sum's `COUNT(column)`, which its tolerance reads, measured beside the sum in the same SELECT | `catalogue.CONDITION_AGGREGATES`, `run_checks._select` |
| `measure_text` written for the date matches (TO_CHAR text) and the exact sums | `run_checks`, DDL |
| `SET LOCAL TIME ZONE 'UTC'` in every profile read and check run | `value_profile.begin_read` |
| Thirteen nullable profile columns and `measure_text`, by the existing additive migration | `registry.VALUE_PROFILE_COLUMNS`, `../ddl-value-profile-1b.md` |
| The table card's tooltip and the review's `table_comparison.test_types` name B1b too | `quality-checks.js`, `quality_review.table_comparison_types` |
| The TestGen-only import reads `baseline_sum`, `baseline_sd`, `upper_tolerance` (sums kept as text) | `testgen_import.PARAM_COLUMNS` |
| `sync_quality_catalog.py --check` covers `numeric_chars.sql`; in step with the clone at `e8a5a906` (`testgen/` identical to `3606859c`) | `scripts/` |

Tests: `tests/test_quality_profile_1b.py`.

Eleven of the twelve are Table Comparison types, proposed only for an opted-in table, as B1a. `Daily_Record_Ct` is in
TestGen's **Standard** set, so it is proposed for every table without an opt-in, as TestGen generates it for every
table group.

## 3. The measures, as upstream takes them

| general_type | Measures added | Upstream flag |
|---|---|---|
| A | `min_length`, `max_length`, `avg_length` (mean over non-empty values: `NULLIF(LENGTH, 0)`) | `is_type_A`, whole-table block |
| N | `zero_value_ct`, `min_value`, `max_value`, `avg_value`, `stdev_value` | `is_type_N` |
| N, decimal | `fractional_sum` | `is_N_decimal` |
| N, integer or decimal | `sum_value` (NULL when a value reaches 1e20 or there are 1e10 values, as upstream) | `is_N_exact_sum` |
| B | `boolean_true_ct` | `is_type_B` |
| D | `before_5yr_date_ct`, `date_days_present` | `is_type_D` |

Left out of upstream's blocks, on purpose: `min_value_over_0` and the three percentiles (`is_type_N`; nothing in
B1b reads them, and the percentiles need a subquery the one-SELECT design does not have; they belong with family C),
the D/N lengths (`is_type_DN`; no rule reads them), `before_1yr`/`before_100yr`/`within_*`/`distant_future`
(`is_type_D`; no rule in B1b reads them), `date_weeks_present`/`date_months_present` (only the date CASE's later
branches read them, §5.2), and `epoch_sum` (§4.1).

`COLUMNS_PER_QUERY` drops from 200 to 150: a decimal number now contributes ten measures, and 200 of them would pass
Postgres's 1664-entry target list. A table with more than 150 columns is read in one more scan than before.

## 4. Where the plan did not hold against the code

Q4 §5 row 3 says "13 measures, 13 nullable columns" and "then B1b". Five things needed more than that.

### 4.1 Match_Epoch_Sum is not part of this step

Its baseline, `epoch_sum`, is taken upstream only under `is_epoch_summed = is_hashed and general_type == "D"`, where
`is_hashed` means the column's general type is in the run's hashed set: the value-hash machinery Q4 puts at step 8
("hashes are whole-table and off by default in TestGen too"). Its measure, `SUM({EPOCH_SUM_TERM})`, is built by the
same flavour code as the hashes (`utc_timestamp`, `hashable_condition`). So `epoch_sum` and Match_Epoch_Sum move to
step 8, and this step has **twelve** types and **thirteen** measures: the thirteenth is `before_5yr_date_ct`, which
Q6 deferred to here.

### 4.2 Two flags read the target's catalog, which RE's inventory does not keep

`is_N_decimal` and `is_N_exact_sum` read upstream's `is_decimal` and `logical_type`, which come from
`information_schema.columns.numeric_scale`. RE's `database_columns` keeps `data_type`, `base_type` and
`column_size`, and a column surveyed through Egeria may carry only the type name. So the profile reads
`numeric_scale` from the target, once per run, beside the existing pg_class read. When that read fails,
`fractional_sum` and `sum_value` are not taken for any column (NULL), the batch records
`numeric_chars: not_read`, and everything else is measured as before.

### 4.3 Two sums are exact, and the results table is not

`sum_value` is `NUMERIC(38,8)` and Match_Sum compares it within a tolerance measured in hundred-millionths;
`fractional_sum` is `NUMERIC(38,5)` and Match_Fractional_Sum compares it with `ROUND(baseline, 5)`. Through a float
either would fail or pass on rounding. So both profile columns are TEXT (the decimal as the target printed it), the
parameters stay text as upstream's do, and the run compares `Decimal`s with Postgres's rounding (half away from
zero). The result's `measure` holds the nearest float for charts; `measure_text` holds the value compared.

### 4.4 Conditions that are expressions, one of which reads the table

`CAST({BASELINE_VALUE} AS DOUBLE PRECISION)`, `ROUND({BASELINE_SUM}, 5)` and the two `NOT BETWEEN` ranges are ported as
`CONDITION_PORTS`, each pinned to its vendored text, as the selection rules are. Match_Sum's range reads
`COUNT({COLUMN_NAME})` inside the condition: TestGen evaluates it in the same statement. RE evaluates conditions in
Python, so the count is measured as a second aggregate in the same SELECT, and the bounds are computed once it is
back (`SumTolerance.between`). Until then `threshold_values_json` is NULL, never the baseline and tolerance (which
would read as bounds).

### 4.5 `IS NOT NULL` on a measure an old profile never took

Four B1b fragments read a 1b measure with `IS NOT NULL` (`fractional_sum`, `stdev_value`, `date_days_present`, and
`sum_value` in Match_Sum's OR). Upstream's NULL there means "not applicable" (a float column has no exact sum). On a
row profiled before this step every 1b measure is NULL, and a straight port would read that as "no": a silent false.
Each B1b rule therefore asks first whether the row could carry 1b measures (`_profiled_by_1b`: it was profiled, and
the witness measure 1b always takes for a column with values is present). If not, the rule is NULL, so the proposal
is `not_established` and names the missing measure. On a row profiled by 1b this changes nothing: the rule reads
exactly upstream's fragment.

### 4.6 Stacked on Q5: what a B1b check publishes as a DataQualityRule

Q5 publishes each accepted check as a DataQualityRule with `thresholdValues: [str(threshold(...))]`, and when a rule
is edited in Egeria, writes `thresholdValues[0]` back into the parameter `threshold_key` names. Both assumed a
condition is one placeholder. For the five B1b expression conditions:

- `threshold_key` names the baseline each is built around (`baseline_value`, `baseline_sum`, `baseline_sd`);
- the rule publishes that baseline (`catalogue.published_threshold`), not the computed limit: Match_Sum's bounds
  depend on the run's count, and a published range's first bound written back into the baseline would move it;
- a range's tolerance rides in `additionalProperties.upperTolerance` (beside `baselineSum` / `baselineSd`), and an
  edit in Egeria changes the baseline only.

Every other type publishes exactly what Q5 publishes. An ODCS reader sees `mustBeBetween` with one value (the
baseline) and the tolerance in additional properties, not two bounds. Question 4.

## 5. Not done here

### 5.1 Calibration and timing

- **Calibration evidence** (Q4 §6.4) is not run: it needs the static coco_pharma copy and a TestGen run with a
  Table Comparison table group. Expected asymmetries to list there once: the opt-in granularity (table vs table
  group); Match_Epoch_Sum not ported (§4.1); a column whose scale RE could not read (§4.2).
- **Added profile time** (Q4 §5.2) is not measured. The heavy ones are, as Q4 said, `STDDEV` and
  `COUNT(DISTINCT date)` per column, all in the existing one scan per chunk; plus one more scan per 150 columns
  (§3).

### 5.2 `before_5yr_date_ct` is measured, and no rule reads it yet

Q6 §5 recommended adding it "with profile 1b, together with the later branches, so the CASE is then ported whole".
The later branches of the date CASE (Transactional Date and its Wk/Mo/Qtr forms, 'Date Stamp', 'DateTime Stamp')
also read `date_weeks_present` and `date_months_present`, which are not among Q4's 1b measures. The coordinator's
brief named `before_5yr_date_ct`; it is measured and stored, and nothing reads it until those branches are ported
(family C, step 6, which Q6 §5 already says the later branches belong with). Question 1.

### 5.3 The 1b measures are not published

The published profile annotation carries slice 1's counts only (`PUBLISHED_COUNTS`). Adding thirteen measures to
`value_count` would change every published column's content hash, so the next publish would re-create every
unchanged column once; and several are not counts at all. Question 2.

### 5.4 Other repositories

The compare harness's type list and `export_testgen.py`'s dropped definition id are unchanged
(`DESIGN-QUALITY-B1A-BASELINE-MATCH.md` §6); both are in `egeria-workspaces-fs`.

## 6. Questions for the project owner

1. **`before_5yr_date_ct` alone, or with the date CASE's later branches?** It is measured; no rule reads it yet.
   *Recommendation:* leave it measured and port the later branches with family C (step 6), adding
   `date_weeks_present` and `date_months_present` then, so the date CASE is ported whole in one place.
2. **Publish the 1b measures?** *Recommendation:* not now. When a consumer outside RE needs them, publish them in a
   separate property (not `value_count`), with a shape-version bump so the one-time re-publish is deliberate.
3. **Confirm the two assumed answers** (§1): `measure_text`, and UTC for both reads. The deployment record lists what
   UTC changes on a target whose surveyor account was not already on UTC (`../ddl-value-profile-1b.md`).
4. **A NOT BETWEEN rule in Egeria: baseline and tolerance, or two bounds?** (§4.6) *Recommendation:* keep the
   baseline and tolerance, which a person can edit and RE can run; two bounds would be exact only for Match_Stdev,
   and Match_Sum's would be stale the moment the row count moved.
