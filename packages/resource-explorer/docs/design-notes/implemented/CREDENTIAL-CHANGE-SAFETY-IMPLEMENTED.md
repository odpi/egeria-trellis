# Credential change safety — implemented (branch re/credential-change-safety)

Ruled by the design session of 2026-10-03, after the incident where the stored
user for laz_local_adventureworks became a wrong role through a path that left
no trace.

Import check (must resolve into the worktree):
`/Users/dwolfson/localGit/egeria-v6/trellis-re-credential-change-safety/packages/resource-explorer/resource_explorer/__init__.py`

## The four changes

1. **Activity row.** `ProjectRegistry.update_database_credentials` writes one
   `activity_log` row per real change: operation `credential_change`, intent
   `enrichment`, entity_type `database`, summary
   `Credentials for <slug> changed: user is now <user>`. The password, or any
   value of it, is never written. If user and plaintext password are both
   unchanged (the lazy re-encrypt in `_row_to_database`) the new ciphertext is
   still stored but no row is written and `credential_changed_at` is untouched.
2. **`databases.credential_changed_at`** (nullable TEXT), set by the same
   function to UTC ISO seconds. Existing rows stay NULL ("before this was
   recorded"). Exposed on `DatabaseEntity` and `DatabaseSummary`.
3. **Connect test before saving**, in `PATCH /api/databases/{slug}/credentials`
   (HTTP 400, detail = message, run via `asyncio.to_thread`) and the CLI
   `database update-credentials` (exit 1). New `resource_explorer/credential_check.py`
   reuses `surveyors/database/connection.database_connection` (new optional
   `connect_timeout`, 5 s here) against the registry row's own
   host/port/database_name. On failure nothing is stored, nothing is projected
   to the omsecrets file, no activity row. Error text is scrubbed of the
   supplied password and raised `from None`. No bypass flag or body field.
4. **Docs.** CLI docstring example is now
   `resource-explorer database update-credentials <slug> --user <role>`; the
   password is prompted (`prompt=True`, `hide_input=True`). No literal user or
   password remains in docstrings, help text or docs for update-credentials
   (other docs' `--user admin --password secret` examples are `database survey`
   and `survey-definition`, untouched).

## Migration (first start migrates the shared registry — PEER CHECK BEFORE ANY SERVE)

Idempotent; the column list is read first (`_get_table_columns`), same SQL on
both dialects (`_add_credential_changed_at_column`):

- SQLite and Postgres: `ALTER TABLE databases ADD COLUMN credential_changed_at TEXT DEFAULT NULL`
  (metadata-only on Postgres, no rewrite).

The first process that opens the shared registry on this code runs it. Ask
every live peer before serving this branch against the shared registry.

## Refusal wording

- Wrong credential (server answered FATAL / authentication):
  `Credential not saved: the server at <host:port> refused this credential (<scrubbed server text>)`
- Cannot reach (refused, timeout, DNS, anything not a server answer):
  `Credential not saved: could not reach <host:port> (<scrubbed text>)`
- Empty user or password: refused with `a user and a password are both required`.

## Tests

`tests/test_credential_change_safety.py` (new); stubbed connect in
`tests/test_database_update_credentials_route.py` and
`tests/test_list_tolerates_bad_credential.py` (both call the PATCH route).
Also run: `test_database_credential_encryption.py`, `test_registry.py`,
`test_curate_authors.py`. Connect is stubbed at `PostgreSQLConnection.connect`;
`psycopg2.connect` raises if reached; every registry is a temp SQLite file.

## Owner's gate (not verified)

After `update-credentials` for adventureworks the activity log shows the change
with the user name; a deliberately wrong user is refused before anything is
stored. NOT verified: any real database connect (no real service was touched).
