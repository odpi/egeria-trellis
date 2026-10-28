# reproject-secrets: implemented 2026-10-02

Ruled by the 2026-10-02 design session (top of the small bucket). Closes follow-up 1 of the Backlog entry
"Egeria's projected secrets file doesn't survive a redeploy". The native-survey Run precondition
(`native_survey_run.credentials_state`) is unchanged.

## What it projects

For each registered database, one collection `<slug>::PostgreSQL Secret` in the file at
`EGERIA_SECRETS_STORE_LOCAL_PATH` (same shape `omsecrets_store.write_credential` always wrote:
`displayName`, `refreshTimeInterval: 60`, `secrets.userId`, `secrets.clearPassword`). The values come from the
registry through `ProjectRegistry.get_database` -> `credential_crypto.decrypt_db_password`, the path the local
survey runner uses to connect. Code: `resource_explorer/omsecrets_reproject.py`.

A database with no stored credential (empty user or password) is skipped. The skip is printed by the CLI and
logged at INFO; it is never written as an empty or default collection and never gets an activity row.

## When it runs

- CLI: `resource-explorer database reproject-secrets <slug>` or `--all` (exactly one). Fills missing collections
  and also rewrites one whose value differs from the registry. Exit 1 if any database errored or the path is unset.
- Automatically, via `heal_missing()`, which fills only collections that are ABSENT (never overwrites an existing
  one, which an operator may have edited): at web startup (`web/app.py` lifespan) and at the top of each
  `egeria_resync._loop` pass, before the scan and outside any lock. No network call, never raises.
- Not at all when `EGERIA_SECRETS_STORE_LOCAL_PATH` is unset: one debug line, once.
- A second startup with everything present writes nothing (file untouched, no rows).

## Activity rows

`operation="project_secrets"`, `intent="enrichment"`, `entity_type="database"`, `entity_slug=<slug>`.
One row per collection written (`status=ok`), one per failure (`status=error`: decrypt failure, unwritable
directory, unreadable existing file). None for a skip or an already-present collection. The automatic path
records a persistent failure once per process rather than every 10-minute pass. Rows, logs and exceptions carry
no credential (error text is RE's fixed decrypt message, or the OS `strerror`, or a class name).

## File handling

- One atomic write per pass: temp file in the same directory, fsync, `os.replace`. Applied in
  `omsecrets_store._save`, so `write_credential` (update-credentials) is atomic too.
- Other collections already in the file are preserved. A file that exists but does not parse is NOT overwritten
  (every would-be write becomes an error row), because `_load` treats it as empty.
- Permissions: the previous writer never set a mode, so this matches it: an existing file keeps its mode, a new
  file gets the umask default (not 0600). The file is bind-mounted into the engine container, whose user must
  still read it; a 0600 file owned by the host user could silently reintroduce the failure being fixed.
- A decrypt failure for one database does not stop the others.

## Tests

`tests/test_omsecrets_reproject.py` (14): deleted file recreated with structure plus decrypt round-trip;
skip said, not written; other collections survive; idempotent (byte-identical, same mtime); one row per written
collection; auto does not overwrite but the CLI reconciles; unwritable directory; unconfigured path; one bad
credential among three; unreadable file kept; atomic write and mode kept; lifespan startup hook; resync loop;
CLI `<slug>`, `--all`, usage and unknown-slug exits.

## Owner's one-line gate (not verified)

After a restart of 8810, the file exists with both collections, and Run on adventureworks's Egeria survey no
longer refuses. Not verified against the real secrets directory or a real Egeria.
