# DDL record: W42, `file_systems.access_key_id` keeps only the key id's tail

**Status:** branch `claude/re-overnight-batch-49ba3c`, not deployed. This is the deployment record for one DATA migration
(no schema change) in RE's shared registry: `file_systems.access_key_id` keeps only the last four characters of an S3
access key id, as `…XXXX`, and the whole id never lands in the registry again. The secret already did not (it is held in
the `.omsecrets` store, named by `credential_ref`).

Nothing in this branch has run against the shared registry (localhost:5442). The UPDATE runs only by RE's own schema init,
the first time a process running this code opens the shared registry, after the owner's word for that restart. Merging is
not enough.

## The exact statement

`ProjectRegistry._migrate_file_systems_key_tail` in `resource_explorer/registry.py` runs this at the end of `_init_schema`,
after `_migrate_investigation_visibility`, in its own short transaction (Postgres: `SET LOCAL lock_timeout = '10s'` first):

```sql
UPDATE file_systems
   SET access_key_id = CASE WHEN length(access_key_id) > 4
                            THEN '…' || substr(access_key_id, length(access_key_id) - 3)
                            ELSE '' END
 WHERE access_key_id <> '' AND access_key_id NOT LIKE '…%';
```

Same statement on SQLite and Postgres (`length`, `substr`, `||` and `LIKE` behave alike for this). No column is added,
dropped or retyped; `FILE_SYSTEMS_REMOTE_REACH_COLUMNS` is unchanged.

### What it touches

Only rows whose `access_key_id` holds a whole id (written before W42): `credential_kind = 'secret_ref'` rows registered
through the S3 form. Live state 2026-11-04: one such file system is known (`dl_minio_datalake`); its reach line already
read "key …XXXX (S3 reader)" from `s3.key_tail`, so nothing a person reads changes. A row already holding a tail (starts
with "…") or nothing is left alone; an id of four characters or fewer — not a real key id — is cleared rather than kept
whole. Idempotent: a second init changes nothing (the WHERE excludes tailed rows).

### Lock behaviour

A row UPDATE under the default ROW EXCLUSIVE lock, bounded by the 10 s `lock_timeout` on Postgres; a reader of
`file_systems` is not blocked. The table is tiny (a handful of rows).

## What writes it from now on

`web/routes/filesystems.py::_register_s3` stores `key_tail(settings.access_key_id)` — the same function the reach line
reads with (`S3Settings.credential_name`), so what is shown and what is stored come from one place. The whole id and the
secret go to the secrets store once, by reference (`store_key`). The register form says so: "The key is sent once and held
in the secrets store by reference; the registry keeps only the last four characters of its id (…XXXX), never the id or
the secret."

`S3Settings.access_key_id` now carries the tail when read from a row and the whole id only while a form's key is being
checked before Register (`with_key_id`); `key_tail` of a tail is the same tail, so every reader is unchanged.

## Verification

- `tests/test_s3_reach_routes.py::test_register_holds_the_key_by_reference_and_never_in_the_registry`: a 20-character id
  registers; the row holds `…W7QX` (a tail of four) and the whole id is in no column of the row.
- `tests/test_s3_reach_routes.py::test_w42_existing_rows_keep_only_the_key_tail_after_the_migration`: a whole id is
  tailed by the migration, a tail and an empty value are left alone, a 4-character id is cleared; a second run is a no-op;
  the reach line reads the same tail.
- `tests/test_s3_reach_routes.py::test_the_form_is_served_with_its_s3_fields_and_words`: the form's words.

Not run against Postgres in this branch (no private Postgres was started; the shared one is off limits).

## No other DDL
