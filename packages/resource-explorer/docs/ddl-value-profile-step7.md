# DDL record: step 7, nineteen columns on `database_value_profiles` (claude/re-quality-7-sampled-text)

**Status:** branch, not deployed, **proposed**. This is the deployment record for nineteen additive nullable columns in
RE's shared registry. **Decision needed (project owner):** approve the columns below, to be applied at the next 8813
restart after merge. The coordinator's brief of 2026-10-29 names the DDL as "nullable columns on `database_value_profiles`,
with a DDL record and rollback ... ADD COLUMN IF NOT EXISTS under a lock_timeout" for port step 7 ("Please start the other
testgen steps"). Merging is not enough: nothing in this branch has run against the shared registry (localhost:5442); the
columns are added by RE's own schema init the first time a process running this code opens it, which is the restart.

## The exact Postgres DDL

`ProjectRegistry._migrate_value_profile_columns` in `resource_explorer/registry.py` runs these at the end of `_init_schema`,
each in its own short transaction, and each only when `_get_table_columns` says the column is missing.
`VALUE_PROFILE_COLUMNS` lists these nineteen after step 6's:

```sql
BEGIN; SET LOCAL lock_timeout = '10s'; ALTER TABLE database_value_profiles ADD COLUMN IF NOT EXISTS distinct_std_value_ct BIGINT; COMMIT;
BEGIN; SET LOCAL lock_timeout = '10s'; ALTER TABLE database_value_profiles ADD COLUMN IF NOT EXISTS min_text TEXT; COMMIT;
BEGIN; SET LOCAL lock_timeout = '10s'; ALTER TABLE database_value_profiles ADD COLUMN IF NOT EXISTS max_text TEXT; COMMIT;
BEGIN; SET LOCAL lock_timeout = '10s'; ALTER TABLE database_value_profiles ADD COLUMN IF NOT EXISTS sample_value_ct BIGINT; COMMIT;
BEGIN; SET LOCAL lock_timeout = '10s'; ALTER TABLE database_value_profiles ADD COLUMN IF NOT EXISTS sample_pattern_value_ct BIGINT; COMMIT;
BEGIN; SET LOCAL lock_timeout = '10s'; ALTER TABLE database_value_profiles ADD COLUMN IF NOT EXISTS lead_space_ct BIGINT; COMMIT;
BEGIN; SET LOCAL lock_timeout = '10s'; ALTER TABLE database_value_profiles ADD COLUMN IF NOT EXISTS quoted_value_ct BIGINT; COMMIT;
BEGIN; SET LOCAL lock_timeout = '10s'; ALTER TABLE database_value_profiles ADD COLUMN IF NOT EXISTS includes_digit_ct BIGINT; COMMIT;
BEGIN; SET LOCAL lock_timeout = '10s'; ALTER TABLE database_value_profiles ADD COLUMN IF NOT EXISTS upper_case_ct BIGINT; COMMIT;
BEGIN; SET LOCAL lock_timeout = '10s'; ALTER TABLE database_value_profiles ADD COLUMN IF NOT EXISTS lower_case_ct BIGINT; COMMIT;
BEGIN; SET LOCAL lock_timeout = '10s'; ALTER TABLE database_value_profiles ADD COLUMN IF NOT EXISTS non_alpha_ct BIGINT; COMMIT;
BEGIN; SET LOCAL lock_timeout = '10s'; ALTER TABLE database_value_profiles ADD COLUMN IF NOT EXISTS non_printing_ct BIGINT; COMMIT;
BEGIN; SET LOCAL lock_timeout = '10s'; ALTER TABLE database_value_profiles ADD COLUMN IF NOT EXISTS numeric_ct BIGINT; COMMIT;
BEGIN; SET LOCAL lock_timeout = '10s'; ALTER TABLE database_value_profiles ADD COLUMN IF NOT EXISTS date_ct BIGINT; COMMIT;
BEGIN; SET LOCAL lock_timeout = '10s'; ALTER TABLE database_value_profiles ADD COLUMN IF NOT EXISTS embedded_space_ct BIGINT; COMMIT;
BEGIN; SET LOCAL lock_timeout = '10s'; ALTER TABLE database_value_profiles ADD COLUMN IF NOT EXISTS avg_embedded_spaces DOUBLE PRECISION; COMMIT;
BEGIN; SET LOCAL lock_timeout = '10s'; ALTER TABLE database_value_profiles ADD COLUMN IF NOT EXISTS std_pattern_match TEXT; COMMIT;
BEGIN; SET LOCAL lock_timeout = '10s'; ALTER TABLE database_value_profiles ADD COLUMN IF NOT EXISTS distinct_pattern_ct BIGINT; COMMIT;
BEGIN; SET LOCAL lock_timeout = '10s'; ALTER TABLE database_value_profiles ADD COLUMN IF NOT EXISTS frequent_patterns TEXT; COMMIT;
```

On SQLite the same statements run without `IF NOT EXISTS`. A fresh registry gets all nineteen from the `CREATE TABLE`
itself (`... DEFAULT NULL`), so the ALTERs are then no-ops.

### Lock behaviour

Each column is nullable with no default: on Postgres 11 and later a catalog-only change, with no table rewrite. Each still
takes ACCESS EXCLUSIVE on its table, bounded at 10 s, one column at a time, exactly as profile 1b's and step 6's. On a
timeout startup raises RuntimeError "Could not add database_value_profiles.<column>: another process holds a lock ...; stop
other Resource Explorer processes and restart.", and a later start adds the rest.

## What each column is, and what NULL means

Every column is a measure of a TEXT column (`general_type = 'A'`). NULL means **not measured**, never 0: a row profiled
before the column existed, a column that is not text, a text column with no value, or a text column whose read did not
finish (the run summary's `text_shape_not_measured` names it and says why).

| Column | Taken | Meaning |
|---|---|---|
| `distinct_std_value_ct`, `min_text`, `max_text` | whole table, in the batched scan | distinct count after upper-casing and dropping spaces, quotes, commas, dots and hyphens; the first and last value (100 characters). Not sampled |
| `lead_space_ct`, `quoted_value_ct`, `includes_digit_ct`, `upper_case_ct`, `lower_case_ct`, `non_alpha_ct`, `non_printing_ct`, `numeric_ct`, `date_ct`, `embedded_space_ct` | sampled | counts of values with that character shape. **Lifted to the whole-table scale** as TestGen lifts them, so a `0` means "none in the sample", never "none in the table", when `sample_ratio` is set |
| `avg_embedded_spaces`, `std_pattern_match`, `distinct_pattern_ct` | sampled | stored as measured on the sample |
| `frequent_patterns` | sampled | JSON text `{"values":[{"value":"AAA-NN","ct":812},...]}`, the five most frequent patterns, counts lifted |
| `sample_value_ct`, `sample_pattern_value_ct` | sampled | how many values the sample held, un-lifted: the witness that the text shape was read, and what a reader needs to undo the lift |

**`sample_ratio`** (existing, REAL) is set on a text column read on a sample: the table's rows over the sample's size,
by TestGen's percent rule (a table of 100,000 rows or fewer is read whole and keeps NULL). It is NULL on every other column.

Selection rules read these as TestGen's do: NULL on a column that is not text is upstream's "no" (a real "no" here, so a
number column is not flooded with `not_established`); NULL on a text column with values means the text shape was not
read, and the rule reads `not_established`, naming "its sampled text shape was not measured". `std_pattern_match` NULL
after a successful read means "no standard pattern", which is a value.

The published profile annotation carries `sampleRatio` (it already had the field, always empty), and not these measures,
so no published measure's content hash changes except a text column that was read on a sample, which re-publishes once.

## Rollback

The columns are unread by anything before this branch. To remove: `ALTER TABLE database_value_profiles DROP COLUMN distinct_std_value_ct, DROP COLUMN min_text, DROP COLUMN max_text, DROP COLUMN sample_value_ct, DROP COLUMN sample_pattern_value_ct, DROP COLUMN lead_space_ct, DROP COLUMN quoted_value_ct, DROP COLUMN includes_digit_ct, DROP COLUMN upper_case_ct, DROP COLUMN lower_case_ct, DROP COLUMN non_alpha_ct, DROP COLUMN non_printing_ct, DROP COLUMN numeric_ct, DROP COLUMN date_ct, DROP COLUMN embedded_space_ct, DROP COLUMN avg_embedded_spaces, DROP COLUMN std_pattern_match, DROP COLUMN distinct_pattern_ct, DROP COLUMN frequent_patterns;` after
stopping RE. The earlier code ignores them.
