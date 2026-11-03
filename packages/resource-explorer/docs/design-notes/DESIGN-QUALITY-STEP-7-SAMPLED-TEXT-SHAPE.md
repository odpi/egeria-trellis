# DESIGN — Quality port step 7: profile slice 2, the sampled text shape (D, B1c) (2026-10-29)

Builds Q4 §5 row 7 (`DESIGN-QUALITY-Q4-PORT-PLAN-AND-CALIBRATION.md`), inside the owner's §7 answers of 2026-10-26.
**Decision (project owner, 2026-10-29):** "Please start the other testgen steps." DDL record:
`../ddl-value-profile-step7.md` (nineteen nullable columns, **proposed, not yet approved**). Code: `quality/text_shape.py`.

## 1. Ported from which TestGen file

TestGen 5.134.0, commit `3606859c` (the clone's `testgen/` tree is identical to it).

| What | Upstream | In RE |
|---|---|---|
| Sampled read of a text column: `sampleable_rows` / `pattern_rows` (with and without sampling), `sampleable_measures`, the pattern CTEs | `flavors/postgresql/profiling/project_profiling_query.sql` lines 26-33, 36-43, 46-100, 101-142 | `postgresql/text_shape.sql`, four blocks verbatim, each pinned by SHA-256 (`text_shape.TEXT_SHAPE_SHA256`) and checked by `scripts/sync_quality_catalog.py` |
| Four whole-table text measures (`zero_value_ct` as a text column's regex, `distinct_std_value_ct`, `min_text`, `max_text`) | same file, lines 16-21 (`whole_table_measures`) | `profile_measures.sql`, flag `is_A_text`, in the batched whole-table scan |
| `IS_NUM`, `IS_DATE` | `flavors/postgresql/profiling/templated_functions.yaml` | `templated_functions.yaml`, verbatim |
| The percent rule | `commands/queries/profiling_query.py` `calculate_sampling_params` | `text_shape.sampling_for` |
| Lifting a sample's counts to the table's scale | `common/models/profile_result.py` `calibrate_sampled_counts`, `_lift`, `_lift_partition` | `text_shape.lift_sampled_counts` |
| `frequent_patterns` from the five pattern slots | `common/profile_frequency.py` `build_frequent_patterns` | `text_shape.frequent_patterns` |
| Seven test types | `dbsetup_test_types/test_types_<Name>.yaml` | `test_types/`, unchanged; selection ported as pinned Python (`catalogue.PREDICATES`) |

**Seven types, not eight.** The plan's "8" is not reproduced by the clone: the types whose selection reads a sampled
text-shape measure and that RE had not ported are Email_Format, Street_Addr_Pattern (`std_pattern_match`, `avg_embedded_spaces`),
US_State and Valid_Characters (family D, Standard set) and the three B1c Match types (Match_Lead_Space_Ct,
Match_Upper_Case_Ct, Match_Non_Printing_Ct). The remaining unported types have no selection rule (Text_Length_Range,
Max_Date, Row_Ct, LOV_All...), are `custom`/query (Constant, Condition_Flag, Combo_Match...), are hash/frequent types
(step 8), or are monitors (deferred). If the owner's count of 8 included a specific type, name it.

## 2. What the slice does

For each measured text column with values, after the table's main read and the percentile statement, **one** statement
(vendored blocks, assembled) reads the column on `TABLESAMPLE BERNOULLI (p) REPEATABLE (64)` when the table is above the
sampling floor, else on the whole column. One read-only transaction per statement, ended at once, all of a table's text
columns inside one budget of their own (`ProfileBounds.text_shape_budget_seconds`, 5 minutes, like the percentiles'), so a
table whose text columns are too slow keeps every other measure.

**The percent rule is TestGen's.** A table of 100,000 rows or fewer is read whole. Above that the sample is 30 % of the
rows, never fewer than 100,000 nor more than 999,000 rows; `sample_ratio` = rows / sample size. TestGen's table-group
defaults are the constants (`SAMPLE_PERCENT`, `SAMPLE_MIN_COUNT`, `SAMPLE_MAX_COUNT`).

**The counts are lifted, and say so.** TestGen multiplies each sampled count by `value_ct / sample_value_ct`; the three
letter-case counts are shared out by largest remainder so they plus the mixed-case values add up to `value_ct`; the top
patterns' counts are lifted by their own sample size. RE does the same, so a calibration compares like with like (§6.3
of the plan: exact on the same physical table). It also keeps `sample_value_ct` and `sample_pattern_value_ct`, the
un-lifted sizes, which TestGen drops, so a reader can recover the raw sample counts.

## 3. Find-absence-as-answer: how a sampled or missing result reads

- **A sampled result is an estimate and says so.** A text column read on a sample carries `sample_ratio`; the published
  annotation's `sampleRatio` (always empty before) now carries it. A `0` in a lifted count means "none in the sample". The
  three B1c types are **not** proposed on a sampled column (`sample_ratio IS NULL` is part of upstream's rule): a sampled
  estimate is never an accepted baseline.
- **NULL is "not measured", never 0.** A statement that failed or ran out of budget leaves its column's text-shape measures
  NULL and the run summary's `text_shape_not_measured` names the column and the reason (`text shape not measured · <Postgres's
  words>` or `exceeded 5 min · text shape not measured`). A sample that held no values while the table did gives NULL counts.
- **`sample_value_ct` is the witness.** The statement always returns it, so a text column with values and a NULL witness is
  one that was never read (profiled before step 7, or its read did not finish): a rule that reads a text-shape measure
  reads `not_established`, with the words "its sampled text shape was not measured (profiled before text shape was taken,
  or this column's read did not finish): run the value profile again". It is never a silent "no".
- **A real "no" is a "no".** A column that is not text has no text shape upstream either, so Email_Format on a number is
  `False`, not `not_established`. `std_pattern_match` NULL after a successful read is upstream's "no standard pattern"
  (False); before a read it is unknown.
- Valid_Characters reads no measure upstream (`general_type = 'A'`). A column that was not profiled is not proposed on its
  type alone: it reads `not_established · not profiled`, as every other rule does.

## 4. Departures from upstream

1. **`zero_length_ct` and `filled_value_ct` stay whole-table.** Upstream samples them too. RE's slice 1 takes them over
   the whole table and publishes them; changing that would make large tables' published counts estimates and re-hash them.
   The two sampled copies in the vendored block are discarded. Consequence: on a sampled table RE's values for these two are
   exact where TestGen's are estimates, and the `sample_ratio IS NULL` guards of Required / Required_No_Placeholder still
   trip, as upstream. Calibration reports them `not_comparable · different acquisition` on a sampled table.
2. **`sample_ratio` is per text column, not per table.** Upstream stamps every column row of a sampled table. In RE the
   non-text columns of a sampled table are read whole, so stamping them would overstate; only a column read on a sample
   carries it. The selection guards (`sample_ratio IS NULL`) read the same on text columns, which is where they matter.
3. **The final SELECT omits `whole_table_measures`.** RE's batched scan reads those measures for 150 columns per scan;
   repeating a whole-table scan per text column would triple the cost. Same values.
4. **Separate budget and statement.** Upstream runs one statement per column; RE runs one per text column inside its own
   5-minute budget, after the main read, so a slow text column cannot discard the counts.
5. **`frequent_patterns` is stored as JSON text** (`{"values":[{"value":"AAA-NN","ct":812},...]}`, TestGen's shape) in a TEXT
   column (the registry may be SQLite). The step 8 brief lists `frequent_patterns`; upstream builds it in the same wide row
   as the sampled measures, so it is here. Step 8 adds `frequent_values` and the hashes.
6. **UTF-8 only.** The vendored non-printing-character test uses `E' ...'` literals; on a SQL_ASCII target Postgres
   refuses the statement. That is reported as a `text_shape_not_measured` gap for the table, not a crash.

## 5. Cost

**No cost numbers, and none claimed.** Nothing was run against a surveyed database. What was done: the vendored statement
was run on a private throwaway Postgres 16 cluster (not :5442, not a surveyed database) over a 1,000-row table, to prove it
runs and that the lift behaves; that says nothing about time. Expected shape, from the SQL: the batched scan gains
`COUNT(DISTINCT UPPER(TRANSLATE(...)))` per text column (a second distinct count over each text column, sort or hash on the
whole column); each text column then costs one more scan of the table (a sample scan on a table over 100,000 rows: a
Bernoulli sample still reads every page, so it is a full scan with row-skipping, plus the pattern aggregation). The §11
static-restore timing plan of the step 6 note applies unchanged and extends to this statement; the owner schedules it.

## 6. What this does not do (stated, so it is not read as done)

**The text branches of the functional-type inference are not ported over the new measures.** Step 6 left every text
column's `functional_data_type` "not inferred" because upstream's text branches read `min_text`, `numeric_ct`,
`avg_embedded_spaces`, `frequent_patterns`, `std_pattern_match` and others. Those are now profiled, but step 6's text
branches (about fifteen hand-approximated points in `functional_infer.py`) still treat them as unknown, so those columns still
read "type not inferred · reads …, which the profile takes but this inference does not read yet". Types that need a text
column's functional type (Alpha_Trunc, Pattern_Match, LOV_Match, Distinct_Value_Ct on text, Valid_Month, Valid_US_Zip,
Monthly/Weekly_Rec_Ct) therefore stay `not_established` as before. The seven new types do not need it. **Recommendation:** a
follow-on step ("step 7b") that ports those branches over the stored measures, with the calibration harness's functional-type
layer beside it. It is a separate review: it changes labels, and through them step 6's selections.

## 7. Calibration rows

Per text column, on a static restore of one dump: profile layer (every measure here, exact, lifted counts compared to
TestGen's lifted counts; `sample_ratio` equal or `not_comparable · different sample`); selection layer (the seven types,
both or neither); parameters (`baseline_ct` = the measure for the three Match types; thresholds 0 / 75). Not
comparable by design: `zero_length_ct`, `filled_value_ct` on a sampled table (departure 1). **Not run:** the harness is
outside RE.

## 8. Tests

`tests/test_quality_step_7_text_shape.py` (31): pins, sampling rule, assembled statement, lifting, what the profile stores,
sampled ratio, failure and budget, the seven selection rules, defaults and renders, the DDL record. Existing tests updated for
expected reasons: proposal counts 14→15 (Valid_Characters on the text column), parse-test alias count, the ported-set equality,
the fake upstream in the sync test, step 6's reason wording, the 1b DDL filter, the timeout assertion that allows the text
shape's own 5 minutes. sqlite only, `PGVECTOR_PORT=1`; nothing reaches :5442 or Egeria.

## 9. Questions for the project owner

1. **Approve the nineteen columns** (`ddl-value-profile-step7.md`), applied at the next 8813 restart after merge.
   Recommendation: approve.
2. **Sampling settings.** The constants are TestGen's defaults (30 %, floor 100,000, cap 999,000), the same for every
   database. Recommendation: keep them as constants now; make them a per-database setting only if a calibration shows a
   need.
3. **Step 7b** (§6): port the text branches of the inference over these measures. Recommendation: yes, next, before step 8
   since it unblocks eight step 6 types for text columns.
4. **The count of eight** (§1): name the eighth type if one was meant.
