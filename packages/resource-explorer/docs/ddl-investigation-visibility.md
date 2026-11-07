# DDL record: F56, `investigations.visibility`

**Status:** branch `claude/re-f56-visibility-axis`, not deployed. This is the deployment record for one column in RE's
shared registry and its one-time backfill (F56, owner's Option D, 2026-11-04: an investigation's visibility is its own
axis, `creator-only | shared`, no longer implied by its kind).

Nothing in this branch has run against the shared registry (localhost:5442). The column is added only by RE's own schema
init, the first time a process running this code opens the shared registry, and only after the owner's word for that
restart. Merging is not enough.

## The exact Postgres DDL

`ProjectRegistry._migrate_investigation_visibility` in `resource_explorer/registry.py` runs at the end of
`_init_schema`, after `_migrate_open_data_tables`, in its own short transaction, and only when `_get_table_columns`
says the column is missing:

```sql
BEGIN;
SET LOCAL lock_timeout = '10s';
ALTER TABLE investigations ADD COLUMN IF NOT EXISTS visibility TEXT NOT NULL DEFAULT 'shared';
UPDATE investigations SET visibility = 'creator-only'
 WHERE project_classification IN ('PersonalProject', 'Experiment');
COMMIT;
```

On SQLite the ALTER runs without `IF NOT EXISTS`. A fresh registry gets the column from the `CREATE TABLE` itself
(`visibility TEXT NOT NULL DEFAULT 'shared'`), so the migration returns before doing anything.

### Lock behaviour

A constant default: on Postgres 11 and later the ALTER is catalog-only, with no table rewrite. The ALTER and the
UPDATE both hold ACCESS EXCLUSIVE / ROW EXCLUSIVE on `investigations`, bounded at 10 s by `lock_timeout`. The table is
small (tens of rows on the shared registry), so the UPDATE is immediate. On a timeout startup raises RuntimeError
"Could not add investigations.visibility: another process holds a lock on investigations; stop other Resource Explorer
processes and restart." and a later start adds it.

## The backfill: nothing changes on day one

The UPDATE writes exactly the pre-F56 rule (`PRIVATE_CLASSIFICATIONS = (PersonalProject, Experiment)`), frozen in
`_VISIBILITY_BACKFILL_KINDS` so a later change to `DEFAULT_VISIBILITY_BY_KIND` cannot change what it writes. Every row
that was private before reads `creator-only`; every other row reads `shared`. Ownerless rows are backfilled by kind like
the rest; they stay visible to everyone (and refused by every publish), exactly as before, with their note now reading
"Set to creator-only, but created before RE recorded who owns an investigation".

It runs in the same transaction as the ALTER, so the column is never seen with a private row reading `shared`. It runs
**once**: when the column already exists, the migration does nothing, so it can never undo a person's later choice to
share a Personal project. (If two processes start at the same instant, the second's `IF NOT EXISTS` no-ops after the
first commits and its UPDATE re-applies the same backfill to the same rows; nobody can have made a choice in between.)

## Other rule changes that ride with it (no DDL)

- `project_classification` may be `''` (kind undecided) while `egeria_binding = 'local'`: an application rule in
  `create_investigation` and `apply_promotion_choice`, not a CHECK constraint (the column keeps `DEFAULT
  'StudyProject'`). Promotion requires a kind.
- Nothing else in the schema changes.

## Verification

- `tests/test_investigation_visibility_axis.py::test_the_backfill_marks_the_old_private_kinds_creator_only_once`:
  a registry with the column dropped (the pre-F56 shape) holding a PersonalProject, an Experiment, a StudyProject and
  a Task gains the column; the first two read creator-only, the others shared; a later edit to shared survives a
  second init.
- `tests/test_investigation_visibility_axis.py::test_a_fresh_registry_has_the_column_with_its_default`.

Not run against Postgres in this branch (no private Postgres was started; the shared one is off limits).

## Rollback

`ALTER TABLE investigations DROP COLUMN visibility;` and redeploy the previous code, which re-derives privacy from the
kind. Any investigation whose visibility was changed away from its kind's default after deploy reverts to the kind's
rule.
