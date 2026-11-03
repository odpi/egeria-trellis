# DDL record: step 8, seven columns on `database_value_profiles` and one new table (claude/re-quality-8-hashes-resumed)

**Status:** branch, not deployed, **not approved**. **Decision needed (project owner):** approve the seven columns and the one
child table below, applied at the next 8813 restart after merge. The coordinator's brief names "hash columns plus a
frequent-values child table". Nothing here has run against the shared registry (localhost:5442).

## The exact Postgres DDL

`ProjectRegistry._migrate_value_profile_columns` runs the ALTERs, each in its own short transaction (nullable, no default: a
catalog-only change; ACCESS EXCLUSIVE bounded at 10 s, as steps 1b, 6 and 7). The table is `CREATE TABLE IF NOT EXISTS` in
RE's schema init, with its index.

```sql
BEGIN; SET LOCAL lock_timeout = '10s'; ALTER TABLE database_value_profiles ADD COLUMN IF NOT EXISTS multiset_hash TEXT; COMMIT;
BEGIN; SET LOCAL lock_timeout = '10s'; ALTER TABLE database_value_profiles ADD COLUMN IF NOT EXISTS epoch_sum TEXT; COMMIT;
BEGIN; SET LOCAL lock_timeout = '10s'; ALTER TABLE database_value_profiles ADD COLUMN IF NOT EXISTS keyed_hash TEXT; COMMIT;
BEGIN; SET LOCAL lock_timeout = '10s'; ALTER TABLE database_value_profiles ADD COLUMN IF NOT EXISTS key_column_name TEXT; COMMIT;
BEGIN; SET LOCAL lock_timeout = '10s'; ALTER TABLE database_value_profiles ADD COLUMN IF NOT EXISTS hash_state TEXT; COMMIT;
BEGIN; SET LOCAL lock_timeout = '10s'; ALTER TABLE database_value_profiles ADD COLUMN IF NOT EXISTS distinct_value_hash TEXT; COMMIT;
BEGIN; SET LOCAL lock_timeout = '10s'; ALTER TABLE database_value_profiles ADD COLUMN IF NOT EXISTS frequency_state TEXT; COMMIT;

CREATE TABLE IF NOT EXISTS database_value_profile_frequent_values (
    id SERIAL PRIMARY KEY, database_slug TEXT NOT NULL, surveyed_at TEXT NOT NULL, source TEXT NOT NULL DEFAULT 'local',
    schema_name TEXT NOT NULL, table_name TEXT NOT NULL, column_name TEXT NOT NULL, value_rank INTEGER NOT NULL,
    value TEXT, value_ct BIGINT, other_distinct_ct BIGINT, state TEXT NOT NULL DEFAULT 'measured', reason TEXT DEFAULT '',
    UNIQUE (database_slug, surveyed_at, source, schema_name, table_name, column_name, value_rank),
    FOREIGN KEY (database_slug) REFERENCES databases(slug));
CREATE INDEX IF NOT EXISTS idx_db_value_freq_slug ON database_value_profile_frequent_values(database_slug, surveyed_at);
```

(The Postgres form of `id` is whatever RE's schema init generates for its other detail tables; the table is created by the same
code path as `database_value_profiles`.) On SQLite the same, without `IF NOT EXISTS` on the ALTERs. A fresh registry gets the
columns from the CREATE TABLE.

## What each is, and what NULL means

- `multiset_hash`, `epoch_sum`, `keyed_hash`: sums of 56-bit hashes (or epoch seconds) as **exact decimal text**; they pass 64 bits,
  so never a BIGINT or float. `key_column_name`: the table's inferred key. `hash_state`: NULL (hashes off, the default, or a row
  from before), `taken`, `not_measured` (the read failed or ran out of its budget), `keyed_not_measured` (the multiset hash was read, the keyed hash was not). NULL in a hash after `taken` means nothing was
  hashable. Hashes are **off by default** (`VALUE_PROFILE_HASHES`).
- `frequency_state`: NULL (the column was not eligible, or the row is from before), `read`, `not_measured`. `distinct_value_hash`:
  MD5 of the sorted distinct values, NULL when there are more than 70 or the column holds only blanks.
- The child table: one row per frequent value of a `read` column, rank 1 first; a row with `value` NULL is the "other" row (every
  value past the tenth: `value_ct` its count, `other_distinct_ct` how many distinct values). A `read` column with no child row held
  only blank values. Counts are lifted to the table's scale for a column read on a sample (`sample_ratio` set on its profile row).
- The published profile annotation carries none of these, so no published content hash changes.

## Rollback

`ALTER TABLE database_value_profiles DROP COLUMN multiset_hash, DROP COLUMN epoch_sum, DROP COLUMN keyed_hash, DROP COLUMN
key_column_name, DROP COLUMN hash_state, DROP COLUMN distinct_value_hash, DROP COLUMN frequency_state;
DROP TABLE database_value_profile_frequent_values;` after stopping RE. Earlier code ignores them.
