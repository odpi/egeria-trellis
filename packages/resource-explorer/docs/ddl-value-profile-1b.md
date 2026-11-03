# DDL record: profile 1b, thirteen columns on `database_value_profiles` and `database_quality_results.measure_text` (claude/re-quality-profile-1b)

**Status:** branch, not deployed. This is the deployment record for fourteen additive columns in RE's shared
registry. Q4 port order step 3 (`design-notes/DESIGN-QUALITY-Q4-PORT-PLAN-AND-CALIBRATION.md` §5, row 3: "13 nullable
columns") plus `measure_text`, which `design-notes/DESIGN-QUALITY-B1A-BASELINE-MATCH.md` §7 question 1 recommended
adding with this step's DDL. That question has no recorded answer; the column is built on the recommendation,
**assumed: owner to confirm** (`design-notes/DESIGN-QUALITY-PROFILE-1B.md` §1).

Nothing in this branch has run against the shared registry (localhost:5442). The columns are added only by RE's own
schema init, the first time a process running this code opens the shared registry, and only after the owner's word
for that restart. Merging is not enough.

## The exact Postgres DDL

`ProjectRegistry._migrate_value_profile_columns` in `resource_explorer/registry.py` runs these at the end of
`_init_schema`, after the main init transaction has committed, each in its own short transaction, and each only when
`_get_table_columns` says the column is missing. `VALUE_PROFILE_COLUMNS` now lists fifteen entries: Q6's
`before_20yr_date_ct`, unchanged, then these fourteen, in this order:

```sql
BEGIN; SET LOCAL lock_timeout = '10s'; ALTER TABLE database_value_profiles ADD COLUMN IF NOT EXISTS before_5yr_date_ct BIGINT; COMMIT;
BEGIN; SET LOCAL lock_timeout = '10s'; ALTER TABLE database_value_profiles ADD COLUMN IF NOT EXISTS date_days_present BIGINT; COMMIT;
BEGIN; SET LOCAL lock_timeout = '10s'; ALTER TABLE database_value_profiles ADD COLUMN IF NOT EXISTS min_length BIGINT; COMMIT;
BEGIN; SET LOCAL lock_timeout = '10s'; ALTER TABLE database_value_profiles ADD COLUMN IF NOT EXISTS max_length BIGINT; COMMIT;
BEGIN; SET LOCAL lock_timeout = '10s'; ALTER TABLE database_value_profiles ADD COLUMN IF NOT EXISTS avg_length DOUBLE PRECISION; COMMIT;
BEGIN; SET LOCAL lock_timeout = '10s'; ALTER TABLE database_value_profiles ADD COLUMN IF NOT EXISTS zero_value_ct BIGINT; COMMIT;
BEGIN; SET LOCAL lock_timeout = '10s'; ALTER TABLE database_value_profiles ADD COLUMN IF NOT EXISTS min_value DOUBLE PRECISION; COMMIT;
BEGIN; SET LOCAL lock_timeout = '10s'; ALTER TABLE database_value_profiles ADD COLUMN IF NOT EXISTS max_value DOUBLE PRECISION; COMMIT;
BEGIN; SET LOCAL lock_timeout = '10s'; ALTER TABLE database_value_profiles ADD COLUMN IF NOT EXISTS avg_value DOUBLE PRECISION; COMMIT;
BEGIN; SET LOCAL lock_timeout = '10s'; ALTER TABLE database_value_profiles ADD COLUMN IF NOT EXISTS stdev_value DOUBLE PRECISION; COMMIT;
BEGIN; SET LOCAL lock_timeout = '10s'; ALTER TABLE database_value_profiles ADD COLUMN IF NOT EXISTS fractional_sum TEXT; COMMIT;
BEGIN; SET LOCAL lock_timeout = '10s'; ALTER TABLE database_value_profiles ADD COLUMN IF NOT EXISTS sum_value TEXT; COMMIT;
BEGIN; SET LOCAL lock_timeout = '10s'; ALTER TABLE database_value_profiles ADD COLUMN IF NOT EXISTS boolean_true_ct BIGINT; COMMIT;
BEGIN; SET LOCAL lock_timeout = '10s'; ALTER TABLE database_quality_results ADD COLUMN IF NOT EXISTS measure_text TEXT; COMMIT;
```

On SQLite the same statements run without `IF NOT EXISTS`. A fresh registry gets every column from the `CREATE TABLE`
itself (each `... DEFAULT NULL`), so the ALTERs are then no-ops.

**Why two sums are TEXT.** `fractional_sum` and `sum_value` are upstream's `NUMERIC(38,5)` and `NUMERIC(38,8)` sums,
which Match_Fractional_Sum and Match_Sum compare exactly. A `DOUBLE PRECISION` column would round them, and SQLite's
NUMERIC affinity would too, so both hold the decimal exactly as the target printed it (`'4820.00000000'`), and RE
compares it as a Python `Decimal`. `measure_text` is TEXT for the same reason and for the B1a date matches' TO_CHAR
text.

### Lock behaviour

Each column is nullable with no default: on Postgres 11 and later a catalog-only change, with no table rewrite. Each
still takes ACCESS EXCLUSIVE on its table, bounded at 10 s, one column at a time. A profile read or a Curate quality
page queues behind each for at most that long. On a timeout startup raises RuntimeError "Could not add
database_value_profiles.<column>: another process holds a lock …; stop other Resource Explorer processes and
restart.", and a later start adds the rest (each ALTER is independent and IF NOT EXISTS).

## What reads NULL in old rows

Every row profiled before these columns existed reads NULL in all thirteen: **not measured**, never 0. New rows hold
NULL too wherever upstream takes no measure: a length on a non-text column, a numeric measure on a non-number,
`fractional_sum` on a column that is not decimal, `sum_value` on a float column, `boolean_true_ct` on a non-boolean,
and every measure of a column that was not profiled.

What that does downstream:

- **The B1b selection rules** on an old row are `not_established`, naming the measure the profile did not take,
  never "no". Several upstream fragments read a 1b measure with `IS NOT NULL`, where NULL means "not applicable", so
  each port first asks whether the row was profiled by 1b at all (`catalogue._profiled_by_1b`: the general_type's
  witness measure, which 1b always takes for a column with values, is NULL). The next profile run measures them.
- **Every earlier rule** is unaffected: none reads these columns.
- **`database_quality_results.measure_text`** is NULL on every result written before it existed. Readers fall back to
  `measure`: the result card and the annotation still render a B1a date match from `measure` (seconds from 1970, read
  back as its TO_CHAR text), as they did before.
- **The published profile annotation** does not carry any of the thirteen (`value_profile.PUBLISHED_COUNTS` is
  slice 1's set, unchanged), so no published measure's content hash changes and nothing re-publishes.

## Not DDL, but deployed with it: the session time zone

`value_profile.begin_read`, shared by the value profile and the check run, now also runs `SET LOCAL TIME ZONE 'UTC'`
in each read-only transaction (`DESIGN-QUALITY-B1A-BASELINE-MATCH.md` §7 question 2, **assumed: owner to confirm**).
It changes nothing on a target whose surveyor account's zone is already UTC. Where it was not:

- a `timestamptz` column's `min_date`/`max_date` are now printed in UTC, so the first profile after deployment can
  show them move by the zone's offset (and the B1a drift reason will say so);
- `future_date_ct` and `before_*_date_ct` compare against `{RUN_DATE}` (UTC text) correctly for `timestamptz`, where
  before they compared it as the account's local time;
- a B1a `Match_Min_Date` / `Match_Max_Date` accepted before this on a `timestamptz` column will fail once by the
  offset, and should be re-accepted from the new profile.

Before the restart, `SHOW TimeZone` as each surveyed database's surveyor account says whether any of this applies.

## Verification

- `tests/test_quality_profile_1b.py::test_the_ddl_is_fourteen_nullable_columns_and_the_create_table_matches_it`: a
  fresh SQLite registry has all fourteen with the declared types, and a second init is a no-op.
- `tests/test_quality_profile_1b.py::test_an_existing_registry_gains_the_columns_and_old_rows_read_not_measured`: a
  registry with the fourteen dropped (a pre-1b shape) and a stored row gains them; the row reads NULL in every 1b
  measure and its B1b rules are not_established.
- `tests/test_quality_profile_1b.py::test_the_migration_adds_nullable_columns_on_postgres_and_is_idempotent`: the
  Postgres tier, on the throwaway test schema (`pg_registry`) only. The columns are dropped and re-added by a fresh
  init, each with its declared type, nullable, no default; a second init changes nothing. **Not run on this branch**:
  the builder ran with the pgvector Postgres pointed at a closed port, so the tier skipped. It is for the gate's
  own test schema.
