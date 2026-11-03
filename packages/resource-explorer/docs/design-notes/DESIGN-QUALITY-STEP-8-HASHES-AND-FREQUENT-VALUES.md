# DESIGN — Quality port step 8: value hashes, frequent values, and the text inference they finish (2026-10-29)

Builds Q4 §5 row 8 (hashes, B2, `Match_Epoch_Sum`) and the frequent-values half the brief names, on step 7b's tip `3ae2d829`.
DDL record (proposed, not approved): `../ddl-value-profile-step8.md`. **B1d monitors are not built** (owner, Q4 §7 Q4); §8 says what
step 8 leaves them.

## 1. Ported from where

TestGen 5.134.0, commit `3606859c` (the clone's `testgen/` tree is identical to it). Hashes are **Python in TestGen**, not data, so RE
writes its own Postgres port (`quality/value_hashes.py`) from them and pins the sources:

| What | Upstream | In RE |
|---|---|---|
| The hash definitions (canonical text, 56-bit MD5, multiset / keyed / epoch sum) | `common/value_hash.py` (the reference implementation) | the contract the SQL is checked against |
| The Postgres SQL builders | `common/database/flavor/flavor_service.py`, `redshift_flavor_service.py`, `postgresql_flavor_service.py` | `value_hashes.multiset_hash_term`, `epoch_sum_term`, `keyed_hash_term`, `hashable_condition`, `canonical_text` |
| A table's key column | `template/profiling/infer_key_columns.sql` | `value_hashes.infer_key_columns` |
| Seven hash types | `test_types_Match_{Hash,Keyed_Hash}_{Text,Num,Date}.yaml`, `Match_Epoch_Sum.yaml` | vendored unchanged; CAT |
| Frequency analysis | `flavors/postgresql/profiling/project_secondary_profiling_query.sql`, `secondary_profiling_columns.sql`, `profile_frequency.py`, `calibrate_frequent_values` | the file **byte-identical**; `quality/frequent_values.py` |
| Three QUERY types | `test_types_Match_{Frequent_Values,Frequent_Patterns,Distinct_Hash}.yaml` | vendored unchanged; their Postgres `test_templates` run by `quality/query_types.py` |
| `datatype_suggestions.sql` (the A branch, first pass), the Code / Category / ID / Attribute / Description CASE, Measurement Pct for Code | `profiling/datatype_suggestions.sql`, `functional_datatype.sql` 412-448, 583-602 | `functional_infer._suggestion_is_varchar`, `_code_category_results`, `_measurement_pct_code` |

Pins: the four Python sources and `infer_key_columns.sql` by SHA-256 (`value_hashes.UPSTREAM_SHA256`), the secondary query by
SHA-256 and byte comparison (`frequent_values.SECONDARY_SHA256`); `scripts/sync_quality_catalog.py` checks all of them against the clone.

**Verified on a real Postgres.** The generated hash SQL equals TestGen's reference implementation for text, `char(n)` (trimmed),
integers, scale-2 and unbounded numerics (including 10^31), `double precision` and `real` (the exact binary split), `money`, `date`,
`timestamp` and `timestamptz` (UTC), NULLs, and the keyed form with a NULL key or value (`tests/test_quality_step_8_hashes.py`, tier
`RE_HASH_TEST_PG_DSN`, run on a private throwaway cluster, never the shared registry). The frequency statement and the three QUERY
templates were run the same way, sampled and not (`test_quality_step_8b_frequent_values.py`). That proves the SQL; it says nothing about time.

## 2. Hashes, off by default

TestGen takes them only for a table group a person opts into (`profile_table_comparison`, default N). RE: `VALUE_PROFILE_HASHES`
(`ValueProfileSettings.value_hashes`, default **false**). When on, each text, number or date column gets `multiset_hash` (a date also
`epoch_sum`) in statements of its own under a budget of its own (`ProfileBounds.hash_budget_seconds`, 5 min), then each table's key is
inferred from the rows just measured and every other hashed column's `keyed_hash` is taken against it. A hash is an exact whole number
far past 64 bits, stored and compared as **exact decimal text**, never a float or a BIGINT.

Find-absence-as-answer: `hash_state` is NULL when hashes were not asked for, `taken`, or `not_measured`. A selection rule reads NULL
as **not established** with the words "value hashes were not taken (they are off by default: set VALUE_PROFILE_HASHES=true ...)" or "this
column's hash read did not finish", never a silent no. After `taken`, a NULL hash is upstream's "no" (nothing hashable; or, for a keyed
hash, the table has no key or this column is the key).

## 3. Frequent values and the child table

For each text column with 2-70 distinct values of at most 70 characters: one statement (the vendored file, its sampling block kept
only when the table is sampled), under `ProfileBounds.frequency_budget_seconds`. The top ten values go to the child table
`database_value_profile_frequent_values` (the "other" row has a NULL value), the MD5 of the sorted distinct values to
`distinct_value_hash`, and `frequency_state` says `read` or `not_measured`. Counts on a sample are lifted as TestGen lifts them.
Unlike hashes, the analysis is on for every profile, as TestGen's is.

## 4. The three QUERY types and the run

Match_Frequent_Values, Match_Frequent_Patterns, Match_Distinct_Hash run the vendored `test_templates` statement (a second execution
path beside Dupe_Rows'), in a read-only transaction inside the QUERY budget; `result_code` 1 passes, 0 fails at the check's severity,
NULL is "baseline is missing". A cancelled read is `not_measured · exceeded N min`, never a pass. An edit made to the published rule in
Egeria does not reach these three (the baseline is long text; the rule's threshold field is a number). The seven CAT hash types run in
the per-table SELECT; their measure is `SUM(<term>)` built per column from the stored catalog type, and the baseline is compared as an
exact whole number (`Decimal`), pinned to the vendored `test_condition`.

## 5. Plain-text Code / Category / ID / Attribute / Description: closed

Step 7b said this statement waits on `datatype_suggestion`, which needs frequent values and TestGen's catalog `column_type`. Both are
now known: TestGen's Postgres `column_type` is `varchar(n)` (`text` and an unsized `character varying` are `varchar(65535)`) or
`char(n)`, derived from `information_schema` exactly as RE's inventory holds it; and the frequent values are in the profile. The first
pass of `datatype_suggestions.sql` for a text column is therefore evaluable: a table of 500 rows or fewer, or a name ending in "id",
keeps `column_type`; above that, text that is all numbers or dates (and has the right frequent values) is suggested DECIMAL, INTEGER,
BIGINT, TIMESTAMP or DATE, else a VARCHAR. The Code statement fires only on a VARCHAR. It is ported with that, so a plain text column
now gets its label (`Description` for free text, `Attribute`, `Code`, `Category`, `ID`, `Measurement Text`) and, via the 583-602
statement, `Measurement Pct` for a percentage code. Quirk preserved: a `char(n)` column is never a VARCHAR suggestion unless the table
is over 500 rows, so it keeps no Code/Category label.

A column eligible for the frequency analysis whose analysis is not in the row (profiled before this step, or its read failed) leaves the
varchar question **unknown** when a frequent value would decide it, and the reason names `frequent_values`.

## 6. Departures

1. **The hash SQL is RE's port, not TestGen's code.** Checked against the reference, pinned to the sources (§1).
2. **Hashes are one statement per 40 columns, not one scan per column** (upstream runs a query per column); same values.
3. **`hash_state` has a value upstream does not.** `keyed_not_measured`: the column's multiset hash was read but its keyed hash was
   not (the keyed statement failed or ran out of budget); `keyed_hash` stays NULL, the key is set, the run summary's
   `hashes_not_measured` names it, and the keyed types read `not_established` for it.
4. **Frequent-values counts and `distinct_value_hash` are stored once per profile batch**, in the child table, not as JSON on the profile row.
5. **The three QUERY types ignore a rule edited in Egeria** (§4).

## 7. Cost

None measured. Expected shape: hashes add a whole-table scan with an MD5 per value per hashed column (the heaviest thing the profile can
do, hence off); the frequency analysis adds one grouped scan per eligible text column (sampled above 100,000 rows). The static-restore
timing plan of the step 6 note §11 applies and extends to both; the owner schedules it.

## 8. B1d (monitors): what step 8 leaves them

Nothing. B1d (Freshness_Trend, Volume_Trend, Table_Freshness) needs a series, and RE already keeps one: every profile batch
(`database_value_profiles`, with `record_ct` and `max_date` per run) and every check's result per run
(`database_quality_results`). Step 8 adds neither a new series nor anything a stated bound rule would read. The stated rule is still
to be written; nothing here stands in its way.

## 9. Tests

`test_quality_step_8_hashes.py` (hash SQL, key inference, the profile with and without hashes, the seven types, the DDL record, with
a real-Postgres tier), `test_quality_step_8b_frequent_values.py` (the frequency analysis, the child table, selection and defaults,
the QUERY run), and new cases in `test_quality_step_7b_text_inference.py` for the Code / Category statement. sqlite only; nothing reaches
:5442 or Egeria.

## 10. Questions for the project owner

1. **Approve the DDL** (seven columns and one table). Recommendation: approve.
2. **Hashes default off** (a database-wide setting `VALUE_PROFILE_HASHES`). Recommendation: keep it off; a per-table opt-in later if
   wanted (the Table Comparison watch already exists and could drive it).
3. **The frequency analysis is on for every profile** (as TestGen's). Recommendation: yes; it is bounded by its own 5 minutes per table.
