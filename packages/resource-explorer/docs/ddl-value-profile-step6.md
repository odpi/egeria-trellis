# DDL record: step 6, five columns on `database_value_profiles` (claude/re-quality-6-functional-type)

**Status:** branch, not deployed. This is the deployment record for five additive nullable columns in RE's shared
registry (two approved first, three percentile columns approved after). **Decision (project owner, 2026-10-29):** "keep TestGen's cumulative, and publish the 19 as rules, ok - add the columns",
which approves the three percentile columns below. Earlier the same day: "Approve, start now" for quality step 6, with the DDL approved as
"nullable functional_data_type, plus functional_table_type if stored per table, on database_value_profiles, applied at
the next 8813 restart after merge." Merging is not enough: nothing in this branch has run against the shared registry
(localhost:5442); the columns are added by RE's own schema init the first time a process running this code opens it,
which is the restart the owner named.

## The exact Postgres DDL

`ProjectRegistry._migrate_value_profile_columns` in `resource_explorer/registry.py` runs these at the end of
`_init_schema`, each in its own short transaction, and each only when `_get_table_columns` says the column is missing.
`VALUE_PROFILE_COLUMNS` now lists twenty entries; these five are last:

```sql
BEGIN; SET LOCAL lock_timeout = '10s'; ALTER TABLE database_value_profiles ADD COLUMN IF NOT EXISTS functional_data_type TEXT; COMMIT;
BEGIN; SET LOCAL lock_timeout = '10s'; ALTER TABLE database_value_profiles ADD COLUMN IF NOT EXISTS functional_table_type TEXT; COMMIT;
BEGIN; SET LOCAL lock_timeout = '10s'; ALTER TABLE database_value_profiles ADD COLUMN IF NOT EXISTS percentile_25 DOUBLE PRECISION; COMMIT;
BEGIN; SET LOCAL lock_timeout = '10s'; ALTER TABLE database_value_profiles ADD COLUMN IF NOT EXISTS percentile_50 DOUBLE PRECISION; COMMIT;
BEGIN; SET LOCAL lock_timeout = '10s'; ALTER TABLE database_value_profiles ADD COLUMN IF NOT EXISTS percentile_75 DOUBLE PRECISION; COMMIT;
```

On SQLite the same statements run without `IF NOT EXISTS`. A fresh registry gets both from the `CREATE TABLE` itself
(`... DEFAULT NULL`), so the ALTERs are then no-ops.

**Why `functional_table_type` is stored.** Upstream stores it per column row (`profile_results.functional_table_type`,
the same value on every column of a table), and the table period (cumulative or window) is read from the table's whole
profile history, which a later profile run can change. Storing it with the profile that concluded it is the record of
what that run saw. It is the same value on every column row of a table, as upstream's.

### Lock behaviour

Each column is nullable with no default: on Postgres 11 and later a catalog-only change, with no table rewrite. Each
still takes ACCESS EXCLUSIVE on its table, bounded at 10 s, one column at a time, exactly as profile 1b's. On a timeout
startup raises RuntimeError "Could not add database_value_profiles.<column>: another process holds a lock ...; stop other
Resource Explorer processes and restart.", and a later start adds the rest.

## What NULL means, and what reads it

- `functional_data_type` NULL: **type not inferred.** Either the row was profiled before the column existed, or the
  stored measures cannot settle one label (every text column, until profile slice 2 takes the text-shape measures; a
  number that might be a Measurement Spike). It is never a default type, and never 'UNKNOWN' (upstream's own word for a
  column nothing labelled, which RE reserves for a column its measures positively leave unlabelled).
- `functional_table_type` NULL: **table type not inferred.** A table with no measured profile run has no
  period; one whose columns' types are not all settled has a period but no type, and then holds the period alone
  (`'cumulative'` or `'window'`), which is all the selection rules read (`LIKE '%cumulative%'`, `NOT LIKE '%window%'`).
- Every selection rule that reads either column is three-valued: NULL makes it `not_established` with "type not
  inferred · <why>", unless the labels the stored measures still allow already settle the rule (a numeric column is a real
  "no" for a text rule). `propose` recomputes the allowed labels from the stored measures for a NULL row; the stored
  label, when present, is used as it stands.
- A row from before the columns existed reads NULL in both. `propose` reports it as "profiled before type inference
  existed: run the value profile again" when the measures now settle a label, and as the usual "type not inferred" reason
  otherwise.
- **The published profile annotation does not carry either column**, so no published measure's content hash changes and
  nothing re-publishes.

## The percentile columns

`percentile_25`, `percentile_50` and `percentile_75` are upstream's `PERCENTILE_CONT(0.25|0.50|0.75)` of a number column,
the inputs of Measurement Spike. `DOUBLE PRECISION`, like the other number measures. NULL means **not measured**, never 0:
a profile from before the columns, a column that is not a number, a table that ran out of its percentile budget (its own
5 minutes, `ProfileBounds.percentile_budget_seconds`, separate from the profile's), or no privilege. A NULL percentile
leaves Measurement Spike to the old bound or `type not inferred`; it is never read as "not a spike". The published
profile annotation does not carry them.

## Rollback

The columns are unread by anything before this branch. To remove: `ALTER TABLE database_value_profiles DROP COLUMN
functional_data_type, DROP COLUMN functional_table_type, DROP COLUMN percentile_25, DROP COLUMN percentile_50, DROP COLUMN percentile_75;` after stopping RE. The earlier code ignores them.
