# Vendored catalogue: DataKitchen DataOps TestGen test types (Postgres, slice 1, Q6, B1a, profile 1b, Dupe_Rows, the schema-drift checks, steps 6, 7 and 8)

Data only. No TestGen code is vendored and TestGen is never run: Resource Explorer reads these files
and executes the checks with its own steps (`surveyors/database/quality/`). The engine and analysis names
Resource Explorer shows are its own ("value profile", "quality checks"); Apache-2.0 grants no trademark,
so the upstream product name appears only here, in `NOTICE`, in the developer docs and, since 2026-10-30 (owner's ruling), in
detailed descriptions: a proposal row's title and a published rule's `description` may say that a check implements, or is
inspired by, a TestGen test type. Never in a headline, a row's words or a rule's name.

- **Upstream repository:** https://github.com/DataKitchen/dataops-testgen
- **Copied from:** the owner's fork, https://github.com/dwolfson/dataops-testgen (checkout
  `~/localGit/egeria-v6/dataops-testgen`). The fork's `testgen/` tree is identical to upstream at the
  commit below.
- **Commit:** `3606859cf8bb4f809d2b19acc51462420829c4ab` ("release: 5.92.2 -> 5.134.0", 2026-10-14),
  **release 5.134.0**.
- **Copied:** 2026-10-20 (slice 1); 2026-10-26 (Q6: Recency, Future_Date_1Y, `before_20yr_date_ct`, the
  templated functions and the date branch of the functional-type inference), from the same commit. The clone's
  HEAD was `e8a5a906`, one docs commit on top of `3606859c`; its `testgen/` tree is identical to `3606859c`.
  2026-10-27 (B1a: the six Table Comparison types on measures RE already profiles, and `gen_Match_Row_Ct.sql`),
  from the same clone and commit, `testgen/` again identical to `3606859c`.
  2026-10-28 (profile 1b: thirteen whole-table measures, the twelve B1b types that read them, and the `logical_type`
  and `is_decimal` CASEs), from the same clone and commit, `testgen/` again identical to `3606859c`.
  2026-10-29 (Q4 steps 4 and 5: Dupe_Rows, Match_Column_Ct, Match_Column_Type, Schema_Conformance and Schema_Drift,
  and the three generation templates they use), from the same clone and commit, `testgen/` again identical to
  `3606859c` (`git diff --stat 3606859c HEAD -- testgen/` is empty).
  2026-10-29 (Q4 step 7: the sampled text-shape measures and the seven types that read them), from the same clone and
  commit, `testgen/` again identical to `3606859c`.
  2026-10-29 (Q4 step 8: the value-hash and frequent-value types and the frequency analysis), from the same clone and
  commit, `testgen/` again identical to `3606859c`.
- **License:** Apache-2.0 (`LICENSE`, verbatim). `NOTICE` is DataKitchen's, verbatim.

## Files

| Vendored file | Upstream path | How |
|---|---|---|
| `test_types/Missing_Pct.yaml` | `testgen/template/dbsetup_test_types/test_types_Missing_Pct.yaml` | unchanged |
| `test_types/Required.yaml` | `testgen/template/dbsetup_test_types/test_types_Required.yaml` | unchanged |
| `test_types/Required_No_Placeholder.yaml` | `testgen/template/dbsetup_test_types/test_types_Required_No_Placeholder.yaml` | unchanged |
| `test_types/Unique.yaml` | `testgen/template/dbsetup_test_types/test_types_Unique.yaml` | unchanged |
| `test_types/Future_Date.yaml` | `testgen/template/dbsetup_test_types/test_types_Future_Date.yaml` | unchanged |
| `test_types/Min_Date.yaml` | `testgen/template/dbsetup_test_types/test_types_Min_Date.yaml` | unchanged |
| `test_types/Row_Ct_Pct.yaml` | `testgen/template/dbsetup_test_types/test_types_Row_Ct_Pct.yaml` | unchanged |
| `test_types/Recency.yaml` | `testgen/template/dbsetup_test_types/test_types_Recency.yaml` | unchanged (Q6) |
| `test_types/Future_Date_1Y.yaml` | `testgen/template/dbsetup_test_types/test_types_Future_Date_1Y.yaml` | unchanged (Q6) |
| `test_types/Match_Row_Ct.yaml` | `testgen/template/dbsetup_test_types/test_types_Match_Row_Ct.yaml` | unchanged (B1a) |
| `test_types/Match_Distinct_Ct.yaml` | `testgen/template/dbsetup_test_types/test_types_Match_Distinct_Ct.yaml` | unchanged (B1a) |
| `test_types/Match_Null_Ct.yaml` | `testgen/template/dbsetup_test_types/test_types_Match_Null_Ct.yaml` | unchanged (B1a) |
| `test_types/Match_Zero_Length_Ct.yaml` | `testgen/template/dbsetup_test_types/test_types_Match_Zero_Length_Ct.yaml` | unchanged (B1a) |
| `test_types/Match_Min_Date.yaml` | `testgen/template/dbsetup_test_types/test_types_Match_Min_Date.yaml` | unchanged (B1a) |
| `test_types/Match_Max_Date.yaml` | `testgen/template/dbsetup_test_types/test_types_Match_Max_Date.yaml` | unchanged (B1a) |
| `test_types/Match_Min_Val.yaml` | `testgen/template/dbsetup_test_types/test_types_Match_Min_Val.yaml` | unchanged (profile 1b) |
| `test_types/Match_Max_Val.yaml` | `testgen/template/dbsetup_test_types/test_types_Match_Max_Val.yaml` | unchanged (profile 1b) |
| `test_types/Match_Sum.yaml` | `testgen/template/dbsetup_test_types/test_types_Match_Sum.yaml` | unchanged (profile 1b) |
| `test_types/Match_Stdev.yaml` | `testgen/template/dbsetup_test_types/test_types_Match_Stdev.yaml` | unchanged (profile 1b) |
| `test_types/Match_Zero_Ct.yaml` | `testgen/template/dbsetup_test_types/test_types_Match_Zero_Ct.yaml` | unchanged (profile 1b) |
| `test_types/Match_Fractional_Sum.yaml` | `testgen/template/dbsetup_test_types/test_types_Match_Fractional_Sum.yaml` | unchanged (profile 1b) |
| `test_types/Match_Min_Length.yaml` | `testgen/template/dbsetup_test_types/test_types_Match_Min_Length.yaml` | unchanged (profile 1b) |
| `test_types/Match_Max_Length.yaml` | `testgen/template/dbsetup_test_types/test_types_Match_Max_Length.yaml` | unchanged (profile 1b) |
| `test_types/Match_Total_Length.yaml` | `testgen/template/dbsetup_test_types/test_types_Match_Total_Length.yaml` | unchanged (profile 1b) |
| `test_types/Match_True_Ct.yaml` | `testgen/template/dbsetup_test_types/test_types_Match_True_Ct.yaml` | unchanged (profile 1b) |
| `test_types/Match_Distinct_Date_Ct.yaml` | `testgen/template/dbsetup_test_types/test_types_Match_Distinct_Date_Ct.yaml` | unchanged (profile 1b) |
| `test_types/Daily_Record_Ct.yaml` | `testgen/template/dbsetup_test_types/test_types_Daily_Record_Ct.yaml` | unchanged (profile 1b) |
| `test_types/Dupe_Rows.yaml` | `testgen/template/dbsetup_test_types/test_types_Dupe_Rows.yaml` | unchanged (steps 4 and 5): a QUERY type; its Postgres `test_templates` entry supplies the grouping subselect RE runs |
| `test_types/Match_Column_Ct.yaml` | `testgen/template/dbsetup_test_types/test_types_Match_Column_Ct.yaml` | unchanged (steps 4 and 5): a METADATA type, Table Comparison set |
| `test_types/Match_Column_Type.yaml` | `testgen/template/dbsetup_test_types/test_types_Match_Column_Type.yaml` | unchanged (steps 4 and 5): a METADATA type, Table Comparison set |
| `test_types/Schema_Conformance.yaml` | `testgen/template/dbsetup_test_types/test_types_Schema_Conformance.yaml` | unchanged (steps 4 and 5): a METADATA type with no selection rule: vendored, never proposed, never concludes |
| `test_types/Schema_Drift.yaml` | `testgen/template/dbsetup_test_types/test_types_Schema_Drift.yaml` | unchanged (steps 4 and 5): a METADATA type, Monitor set |
| `gen_query_tests/gen_Match_Row_Ct.sql` | `testgen/template/gen_query_tests/gen_Match_Row_Ct.sql` | unchanged (B1a): Match_Row_Ct's selection is this template, not a fragment; its port is pinned to the file's hash |
| `gen_query_tests/gen_Dupe_Rows.sql` | `testgen/template/gen_query_tests/gen_Dupe_Rows.sql` | unchanged (steps 4 and 5): Dupe_Rows' selection is this template; its port is pinned to the file's hash |
| `gen_query_tests/gen_Match_Column_Ct.sql` | `testgen/template/gen_query_tests/gen_Match_Column_Ct.sql` | unchanged (steps 4 and 5): same |
| `gen_query_tests/gen_Schema_Drift.sql` | `testgen/template/gen_query_tests/gen_Schema_Drift.sql` | unchanged (steps 4 and 5): same |
| `postgresql/profile_measures.sql` | `testgen/template/flavors/postgresql/profiling/project_profiling_query.sql` | subset: the measure lines for `record_ct`, `value_ct`, `distinct_value_ct`, `null_value_ct`, `zero_length_ct`, `filled_value_ct`, `min_date`, `max_date`, `future_date_ct`, (Q6) `before_20yr_date_ct` and (profile 1b) `min_length`, `max_length`, `avg_length`, `before_5yr_date_ct`, `date_days_present`, `zero_value_ct`, `min_value`, `max_value`, `avg_value`, `stdev_value`, `fractional_sum`, `sum_value` and `boolean_true_ct`, and (step 7, flag `is_A_text`) `zero_value_ct` as a text column's regex form, `distinct_std_value_ct`, `min_text` and `max_text`, each verbatim, under upstream's `TG-IF` flag names; a header comment added |
| `postgresql/numeric_chars.sql` | `testgen/template/flavors/postgresql/data_chars/get_schema_ddf.sql` | subset (profile 1b): the `logical_type` CASE (upstream lines 28-49) and the `is_decimal` CASE (67-71), verbatim, each headed by its line range; a header comment added |
| `postgresql/templated_functions.yaml` | `testgen/template/flavors/postgresql/profiling/templated_functions.yaml` | subset (Q6, step 6, step 7): `DATEDIFF_DAY`, `DATEDIFF_MONTH`, `DATEDIFF_WEEK`, and (step 7) `IS_NUM` and `IS_DATE`, verbatim; a header comment added |
| `profiling/functional_datatype_date.sql` | `testgen/template/profiling/functional_datatype.sql` | subset (Q6): the eight statements that can label a date column (upstream lines 18-25, 28-36, 39-43, 45-49, 72-116, 136-142, 228-262, 356-366), verbatim and in upstream order, each headed by its line range; a header comment added |
| `profiling/functional_datatype.sql` | `testgen/template/profiling/functional_datatype.sql` | unchanged (step 6): the whole 629-line file, which `quality/functional_infer.py` ports in upstream order; pinned by SHA-256 |
| `profiling/functional_tabletype_stage.sql` | `testgen/template/profiling/functional_tabletype_stage.sql` | unchanged (step 6): the table period (cumulative or window) and table type; ported in `functional_infer.table_period` and `table_kind`; pinned by SHA-256 |
| `test_types/{Alpha_Trunc,Avg_Shift,Dec_Trunc,Distinct_Date_Ct,Distinct_Value_Ct,Incr_Avg_Shift,LOV_Match,Min_Val,Monthly_Rec_Ct,Outlier_Pct_Above,Outlier_Pct_Below,Pattern_Match,Unique_Pct,Valid_Month,Valid_US_Zip,Valid_US_Zip3,Variability_Decrease,Variability_Increase,Weekly_Rec_Ct}.yaml` | `testgen/template/dbsetup_test_types/test_types_<Name>.yaml` | unchanged (step 6) |
| `test_types/{Email_Format,Street_Addr_Pattern,US_State,Valid_Characters,Match_Lead_Space_Ct,Match_Upper_Case_Ct,Match_Non_Printing_Ct}.yaml` | `testgen/template/dbsetup_test_types/test_types_<Name>.yaml` | unchanged (step 7) |
| `postgresql/text_shape.sql` | `testgen/template/flavors/postgresql/profiling/project_profiling_query.sql` | subset (step 7): four blocks, each verbatim under an `-- upstream lines A-B` header: the sampled and unsampled `sampleable_rows`/`pattern_rows` CTEs (lines 26-33, 36-43), `sampleable_measures` (46-100) and the pattern CTEs (101-142). Assembled into one statement per text column (`quality/text_shape.py`); pinned block by block by SHA-256 |
| `test_types/{Match_Hash_Text,Match_Hash_Num,Match_Hash_Date,Match_Keyed_Hash_Text,Match_Keyed_Hash_Num,Match_Keyed_Hash_Date,Match_Epoch_Sum}.yaml` | `testgen/template/dbsetup_test_types/test_types_<Name>.yaml` | unchanged (step 8): CAT, Table Comparison; their measure is `SUM({MULTISET_HASH_TERM})`, `SUM({KEYED_HASH_TERM})` or `SUM({EPOCH_SUM_TERM})`, which RE builds in `quality/value_hashes.py` |
| `test_types/{Match_Frequent_Values,Match_Frequent_Patterns,Match_Distinct_Hash}.yaml` | `testgen/template/dbsetup_test_types/test_types_<Name>.yaml` | unchanged (step 8): QUERY, Table Comparison; the Postgres `test_templates` entry is what `quality/query_types.py` runs |
| `postgresql/project_secondary_profiling_query.sql` | `testgen/template/flavors/postgresql/profiling/project_secondary_profiling_query.sql` | unchanged (step 8): the frequency analysis of a text column; byte-identical, pinned by SHA-256 (`quality/frequent_values.py`) |
| `postgresql/percentiles.sql` | `testgen/template/flavors/postgresql/profiling/project_profiling_query.sql` | subset (step 6): the three PERCENTILE_CONT lines of the `is_N_exact_pctile` block (upstream lines 242-244), verbatim; a header comment added. Run as a statement of their own per table (`quality/percentiles.py`) |
| `postgresql/general_type.sql` | `testgen/template/flavors/postgresql/data_chars/get_schema_ddf.sql` | subset: the `general_type` CASE, verbatim; a header comment added |
| `LICENSE`, `NOTICE` | `LICENSE`, `NOTICE` | unchanged |

Slice 1 is seven test types (`docs/design-notes/DESIGN-VALUE-PROFILE-AND-QUALITY-CHECKS.md`). Q6 adds
Recency, whose selection rule reads `functional_data_type`, and Future_Date_1Y
(`docs/design-notes/DESIGN-QUALITY-Q6-RECENCY.md`). B1a adds the six Table Comparison types whose baseline is
a measure the value profile already takes, proposed only for a table a person opted in
(`docs/design-notes/DESIGN-QUALITY-B1A-BASELINE-MATCH.md`). Profile 1b adds the whole-table numeric, length, boolean
and date-days measures and the eleven Table Comparison types that read them, plus Daily_Record_Ct (Standard set);
Match_Epoch_Sum is not vendored, because upstream takes `epoch_sum` only for a hashed column
(`docs/design-notes/DESIGN-QUALITY-PROFILE-1B.md`).

Steps 4 and 5 add Dupe_Rows (`docs/design-notes/DESIGN-QUALITY-STEPS-4-5-DUPE-ROWS-AND-DRIFT.md`) and the four
schema-drift checks. Their upstream `run_type` is QUERY or METADATA, so they carry no `cat_test_conditions` entry: what
runs is RE's own (`quality/dupe_rows.py`, `quality/schema_drift.py`), and `scripts/sync_quality_catalog.py --check`
compares their `run_type` and Postgres `test_templates` entry instead.

Step 6 adds the nineteen types of family C (`docs/design-notes/DESIGN-QUALITY-STEP-6-FUNCTIONAL-TYPE-INFERENCE.md`)
and the whole functional-type inference. Distinct_Value_Ct also names `gen_Distinct_Value_Ct.sql` (upstream
`gen_funny_cat_tests/`, the constants-that-change generator); it is not vendored, because RE selects by the type's
`selection_criteria` and the generator is a different upstream mechanism.

Step 7 adds the sampled text-shape measures and seven types that read them: the four Standard-set types whose
selection reads `std_pattern_match`, a text column's average length and spaces, or its general type
(Email_Format, Street_Addr_Pattern, US_State, Valid_Characters), and the three Table Comparison types of B1c
(`docs/design-notes/DESIGN-QUALITY-STEP-7-SAMPLED-TEXT-SHAPE.md`).

Step 8 adds the value hashes (written in RE's own `quality/value_hashes.py` from TestGen's `value_hash.py` reference and its
flavor services, which are Python code, not data: the four source files and `infer_key_columns.sql` are pinned by SHA-256 in
`value_hashes.UPSTREAM_SHA256` and compared with the clone by `scripts/sync_quality_catalog.py`), the seven CAT hash types,
the frequency analysis and the three QUERY types that compare a column's baseline text
(`docs/design-notes/DESIGN-QUALITY-STEP-8-HASHES-AND-FREQUENT-VALUES.md`).

## How the catalogue is used

- **Target-side SQL runs verbatim.** Each YAML's Postgres `cat_test_conditions` entry (`measure`,
  `test_operator`, `test_condition`) is what `run_quality_checks` executes against the surveyed database,
  with every parameter validated by type before it is substituted.
- **Selection rules run as Python.** `selection_criteria` is Postgres SQL over TestGen's own
  `profile_results`; Resource Explorer's registry may be SQLite. Each rule is ported to a Python
  predicate pinned to the SHA-256 of the fragment it came from (`quality/catalogue.py`), and a test fails
  when the vendored fragment no longer matches its pin. The `general_type` CASE is ported the same way, and
  so are the date statements of the functional-type inference (`quality/functional_type.py`) and
  Recency's default threshold, which is an expression rather than a value. Profile 1b's two numeric CASEs
  (`quality/general_type.py`), Match_Sum's and Match_Total_Length's defaults, and the five B1b conditions that are
  an expression rather than one placeholder (`catalogue.CONDITION_PORTS`) are pinned the same way.

## Updating

`scripts/sync_quality_catalog.py --check --upstream <testgen checkout>` diffs this copy against an
upstream checkout (each YAML's `selection_criteria`, `default_parm_columns`, `default_parm_values` and
Postgres `cat_test_conditions`, the profile SQL subset, the generation templates, and new test types) and writes
nothing.
Adopting an upstream change is a deliberate commit: copy the file, update the commit above, and re-port
any predicate whose pin the tests then report.
