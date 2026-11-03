# DDL record: Files build step 6, `filesystem_walk_files.accessed_at`

**Status:** branch `claude/re-files-steps-6-8`, not deployed. This is the deployment record for one additive column in
RE's shared registry, needed by `fs_activity` (Files brief F8: "which files has nobody touched for a long time, and which
are hot?"). The walk already read each file's access time from its stat, but kept only the folder's latest; the per-file
rows kept per run (Files build step 1) did not carry it.

Nothing in this branch has run against the shared registry (localhost:5442). The column is added only by RE's own schema
init, the first time a process running this code opens the shared registry, and only after the owner's word for that
restart. Merging is not enough.

## The exact Postgres DDL

`ProjectRegistry._migrate_walk_file_columns` in `resource_explorer/registry.py` runs this at the end of `_init_schema`,
after `_migrate_value_profile_columns`, in its own short transaction, and only when `_get_table_columns` says the column
is missing:

```sql
BEGIN; SET LOCAL lock_timeout = '10s'; ALTER TABLE filesystem_walk_files ADD COLUMN IF NOT EXISTS accessed_at TEXT; COMMIT;
```

On SQLite the same statement runs without `IF NOT EXISTS`. A fresh registry gets the column from the `CREATE TABLE`
itself (`accessed_at TEXT`, nullable, no default), so the ALTER is then a no-op.

Same helper and shape as `docs/ddl-artifact-columns.md` and `docs/ddl-value-profile-q6.md`
(`_add_nullable_text_column`).

### Lock behaviour

Nullable with no default: on Postgres 11 and later a catalog-only change, with no table rewrite. It still takes ACCESS
EXCLUSIVE on `filesystem_walk_files`, bounded at 10 s. A walk storing its rows, or a card reading them, queues behind it
for at most that long. On a timeout startup raises RuntimeError "Could not add filesystem_walk_files.accessed_at: another
process holds a lock …; stop other Resource Explorer processes and restart." and a later start adds it.

Live state 2026-10-20 (brief §1): no file system is registered on the shared registry, so the table is expected to be
empty or near it.

## What reads NULL in old rows

Every row walked before the column existed reads `accessed_at = NULL`: **access time not recorded on this walk**, never
"never accessed". `fs_activity`'s access measure is then `not_established` with exactly those words; its modification
measure is unaffected. The next walk records it.

The value is the stat's access time taken BEFORE the walk opens the file to hash it, so this walk's own read is never
this walk's access time. The NEXT walk can see it on a mount that updates access times; `fs_activity` counts a file the
previous walk hashed, last read at or after that walk began, as "possibly Resource Explorer's own read", never as hot.

Nothing publishes it: the walk's publish is counts only, and `fs_activity`'s results name no file.

## Verification

- `tests/test_files_derived_steps_host.py::test_a_fresh_registry_has_accessed_at_and_a_second_init_is_a_no_op`
- `tests/test_files_derived_steps_host.py::test_an_existing_registry_gains_accessed_at_and_its_old_rows_read_not_recorded`:
  a registry with the column dropped (the pre-step-6 shape) and a stored row gains the column, and the row reads NULL.
- `tests/test_files_derived_steps_host.py::test_the_walk_keeps_each_files_access_time_and_its_own_wall_time`
- trellis-analytics `tests/test_files_derived_steps.py::test_activity_access_times_not_recorded_on_this_walk`

Not run against Postgres in this branch (no private Postgres was started; the shared one is off limits).

## No other DDL

The walk's wall time and machine for F12's price line ride in the existing `survey_data` JSON (`walk_limits.walk_seconds`,
`walk_limits.machine`). The new run kind `filesystem_analysis_run` is a Python tuple entry: `runs.kind` has no CHECK
constraint. The derived steps' results go in the existing `step_annotations` table.
