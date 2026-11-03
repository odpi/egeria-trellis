# Calibration run, adventureworks, static restore (2026-10-29)

*Kept in the repo as the record of the run. The row-by-row CSV files (profile, checks, results; 1 to 2 MB each) are not kept here. They can be produced again by running the harness `compare_re_testgen.py` from `egeria-workspaces-fs` on a restore of the same database.*

**Indicative-grade caveat: none. This is the "evidence of record" grade of plan section 6.3: a throwaway restore, one dump, no writer.**

## Run facts (plan 6.2 item 3)

- Data: `pg_dump -Fc` of adventureworks on localhost:5432, taken once, restored into a throwaway Postgres 17.11 on 127.0.0.1:55438. No writer touched it.
- TestGen: 5.134.0 (`datakitchen/dataops-testgen:v5.134.0`), the release the vendored catalog was copied from (`PROVENANCE.md`, commit `3606859c`). **No catalog skew.** Its metadata lives in a `testgen` database on the same throwaway, not on :5442. One project, 10 table groups (one per schema), each profiled, test-generated and test-run: 40 jobs, 0 failed.
- RE: `origin/main` 6e9220f1 (steps 7, 7b and 8 merged). A throwaway SQLite registry. RE profile 2026-10-30T01:26Z (hashes on); proposals, acceptance of every proposed check as proposed, and `run_quality_checks` at 01:56Z (1,383 checks run in 3.05 s).
- Harness: `compare_re_testgen.py` from `egeria-workspaces-fs`, unmodified, with `--types` set to all 68 vendored types. The seeder and export scripts are the unmodified ones; only `examples.yaml` was mine (one project, the throwaway host).
- No Egeria, no :5442, no 8813 write.
- TestGen defaults in force: `profile_use_sampling = N` on all table groups (sampling off); Standard test suites only; the Monitor and Table Comparison sets were not generated.

## Profile layer (1,236 RE rows, 1,224 TestGen rows)

| Coverage | Rows |
|---|---|
| both | 456 |
| RE not applicable (the 87 views; RE reads tables only) | 768 |
| RE only (12 rows, view columns TestGen did not profile) | 12 |

Of the 456 columns both measured, **44 differ, all explained:**

- 40 `distinct_value_ct`: every one is a column of RE general type X (uuid, xml and the like). RE leaves it NULL ("not measured", never 0); TestGen counts it. Intended.
- 4 `min_date` + 4 `max_date`: the same date; TestGen writes `1951-10-17T00:00:00`, RE `1951-10-17`. A harness comparison artifact (its `same()` trims to 19 characters after replacing T), not a value difference.

Every count measure (`record_ct`, `value_ct`, `null_value_ct`, `zero_length_ct`, `filled_value_ct`, `future_date_ct`) agreed on all 456.

## Selection layer (3,924 check keys over the 68 vendored types)

| Result | Checks |
|---|---|
| both, RE proposed | 1,150 |
| both, RE `not_established` ("could not decide") | 180 |
| TestGen only, on a view | 2,076 |
| TestGen only, on a table | 32 |
| RE only, proposed | 233 |
| RE only, `not_established` (TestGen generated none either) | 253 |

Explained:

- **2,076 TestGen-only on views**: RE profiles tables only (87 views in this database). Intended.
- **RE only, proposed (233)**: Row_Ct_Pct 68 (manual-only in TestGen), Schema_Drift 68 (Monitor set; TestGen's Standard suite does not generate it), Required 1 (see below), **Valid_Characters 96** (see finding 1).
- **253 RE only, not_established**: RE says "could not decide" and TestGen generated nothing; neither side has a test, so this is not a disagreement.
- **180 both, RE could not decide.** TestGen generated a test; RE proposed no check and said why. Causes, from RE's own reasons:
  - `frequent_patterns` not measured: Alpha_Trunc 23, Pattern_Match 2
  - an input the profile did not measure: Alpha_Trunc 21, LOV_Match 21
  - `date_months_present` / `date_weeks_present` not taken: Monthly_Rec_Ct 13, Weekly_Rec_Ct 12, Distinct_Date_Ct 7
  - `distinct_value_ct` not measured (general type X): Unique 16, Unique_Pct 29 (+ "Constant" unresolved)
  - "type not inferred · undetermined": Outlier_Pct_Above 14, Outlier_Pct_Below 14

  These are gaps in what the profile takes, said as absence, not as a pass. They are port work (or, for type X, intended).
- **32 TestGen-only on tables**: Distinct_Value_Ct 31 (all columns with exactly one distinct value; TestGen's `gen_funny_cat_tests/gen_Distinct_Value_Ct.sql`, the "constants that change" generator over a five-run history; recorded as not ported in `PROVENANCE.md`) and Required_No_Placeholder 1 (see finding 2).

## Parameters (threshold_value only; not every row explained)

Equal on all of: Avg_Shift, Daily_Record_Ct, Dec_Trunc, Email_Format, Future_Date, Incr_Avg_Shift, Min_Date, Min_Val, Missing_Pct, Required, Required_No_Placeholder, Variability_Increase, Variability_Decrease and on most of Unique and Unique_Pct (197 of 228, 34 of 50).

Differ, **not yet explained**: Alpha_Trunc 48, Dupe_Rows 68, LOV_Match 23, Monthly_Rec_Ct 13, Weekly_Rec_Ct 12, Pattern_Match 2, Outlier_Pct_* 14 each, Distinct_Date_Ct 7, Unique 16, Unique_Pct 31. The harness compares only `threshold_value`; several of these types keep their limit under another key in RE's `params_json` (for example Dupe_Rows' `skip_errors`), and the 180 RE-`not_established` rows carry no RE parameter. This layer needs a per-type comparison before it can be called agreed.

## Result layer (1,145 agree, 5 disagree, 2,521 not comparable)

- **1,145 agree** on status, across 19 types (Required 292, Unique_Pct 197, Future_Date 106, Min_Date 83, Dupe_Rows 63, Required_No_Placeholder 60, Min_Val 58, Missing_Pct 44, and the rest).
- **5 disagree, all Dupe_Rows**: TestGen `error` ("could not identify an equality operator for type xml") on humanresources.jobcandidate, person.person, production.illustration, production.productmodel and sales.store; RE `pass` with the xml column left out of the key and named. Intended (the owner's Q4 ruling 3: general type X columns are left out of the group key).
- **2,521 not comparable:** 2,076 TestGen results on views (RE ran none); 212 TestGen results on tables where RE proposed no check (the 180 above plus the 32); 233 RE results with no TestGen test (Row_Ct_Pct, Schema_Drift, Valid_Characters, Required).

## Findings that are not yet explained away

1. **Valid_Characters: RE proposes 96, TestGen generates none.** Upstream the type is `active: N` (as is Valid_Month); TestGen's generator skips inactive types. RE's selection reads only `general_type = 'A'`, so it proposes an upstream-inactive type for every text column. Either RE should not propose a type that is inactive upstream, or it should say that it is. A real divergence of the step 7 port.
2. **Sampled tables.** TestGen's table groups had sampling off, so `sample_ratio` is NULL there; RE samples the text shape of tables over 100,000 rows (salesorderdetail, transactionhistory) and sets `sample_ratio`. Required and Required_No_Placeholder both read `sample_ratio IS NULL`, so transactionhistory.transactiontype is Required in RE and Required_No_Placeholder in TestGen. An intended consequence of keeping TestGen's sampling constants when TestGen's own default is off; to confirm with a TestGen group that has sampling on.
3. **The 180 "could not decide" rows** are the port's remaining profile gaps (listed above); none is a false pass.

## What was not covered

- Table Comparison types (hashes, Match_*): TestGen generates them only for an opted-in group; none was opted in on either side.
- Monitor types: not generated (B1d is design only).
- coco_pharma: not dumped (the credential RE holds for :5442 has no SELECT on its tables).
- Hash values: not compared against TestGen (TestGen never generated them here).
- Result measures were not compared value for value, only status.

Raw data beside this file: `calibration-adventureworks-{profile,checks,results}-2026-10-29.csv`.
