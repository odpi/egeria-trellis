# DDL record: quality slice Q6, `database_value_profiles.before_20yr_date_ct` (re/quality-q6-recency)

**Status:** merged to main (PR #880, 2026-10-27). The column is added by RE's schema init the first time a process
running that code opens a registry. This is the deployment record for one additive column in RE's shared
registry. **Decision (project owner, 2026-10-26):** the seven recommendations of
`DESIGN-QUALITY-Q4-PORT-PLAN-AND-CALIBRATION.md` §7 are accepted, and its port order (§5, row 1) gives Q6
"two nullable columns on `database_value_profiles`". This slice adds one of the two (`before_20yr_date_ct`);
`before_5yr_date_ct` is deferred (`design-notes/DESIGN-QUALITY-Q6-RECENCY.md` §4).

Nothing in this branch has run against the shared registry (localhost:5442). The column is added only by RE's
own schema init, the first time a process running this code opens the shared registry, and only after the
owner's word for that restart. Merging is not enough.

## The exact Postgres DDL

`ProjectRegistry._migrate_value_profile_columns` in `resource_explorer/registry.py` runs this at the end of
`_init_schema`, after the main init transaction has committed and after `_migrate_profile_proof_columns`, in its
own short transaction, and only when `_get_table_columns` says the column is missing:

```sql
BEGIN; SET LOCAL lock_timeout = '10s'; ALTER TABLE database_value_profiles ADD COLUMN IF NOT EXISTS before_20yr_date_ct BIGINT; COMMIT;
```

On SQLite the same statement runs without `IF NOT EXISTS`. A fresh registry gets the column from the
`CREATE TABLE` itself (`before_20yr_date_ct BIGINT DEFAULT NULL`), so the ALTER is then a no-op.

It is the same helper and shape as the artifacts columns (`docs/ddl-artifact-columns.md`), with one change:
`_add_nullable_text_column` takes a `sql_type` (default `TEXT`, so its existing callers are unchanged) because
this column is a count.

### Lock behaviour

Nullable with no default: on Postgres 11 and later a catalog-only change, with no table rewrite. It still takes
ACCESS EXCLUSIVE on `database_value_profiles`, bounded at 10 s. A profile read or a Curate quality page queues
behind it for at most that long. On a timeout startup raises RuntimeError "Could not add
database_value_profiles.before_20yr_date_ct: another process holds a lock …; stop other Resource Explorer
processes and restart." and a later start adds it.

## What reads NULL in old rows

Every row profiled before the column existed reads `before_20yr_date_ct = NULL`: **not measured**, never 0.
New rows hold NULL too for any column that is not a date (the measure is taken only in the `is_type_D` block)
and for any column of a table that was not profiled. On a measured empty table it is NULL as well (`SUM` over no
rows).

What that does downstream:

- **Recency's selection** on an old profile row is `not_established`, reason "its selection rule reads
  before_20yr_date_ct, which the profile did not measure", unless one of its other terms already says no (not a
  date, stale, `filedate`). It is never read as "not a Future Date". The next profile run measures it.
- **Every other rule** is unaffected: none reads the column.
- **The published profile annotation** does not carry it: it is relative to the profile's own instant
  (`RELATIVE_TO_NOW_MEASURES`), like `future_date_ct`, so it is left out of `value_count` and out of the profile
  publish reshape's content hash. Adding it does not make any unchanged measure re-publish.

## Verification

- `tests/test_quality_q6_recency.py::test_fresh_sqlite_has_the_column_and_a_second_init_is_a_no_op`
- `tests/test_quality_q6_recency.py::test_an_existing_sqlite_registry_gains_the_column_and_its_rows_read_not_measured`:
  a registry with the column dropped (a pre-Q6 shape) and a stored row gains a `BIGINT` column and the row
  reads NULL.
- `tests/test_quality_q6_recency.py::test_the_migration_adds_a_nullable_bigint_on_postgres_and_is_idempotent`:
  on the throwaway test schema (`pg_registry`), the column is dropped, re-added by a fresh init as
  `bigint`, nullable, no default, and a second init changes nothing. Run for this branch against a private
  Postgres 16 started for the purpose (localhost:55432, `PGVECTOR_PORT=55432`), not the shared one; the
  server's DDL log shows the `ADD COLUMN IF NOT EXISTS ... BIGINT` above.
