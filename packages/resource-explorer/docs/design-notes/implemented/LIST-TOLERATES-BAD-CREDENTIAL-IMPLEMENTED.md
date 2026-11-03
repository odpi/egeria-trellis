# List reads tolerate one unreadable credential — implemented

Incident 2026-10-02: two rows (db_a, db_b) whose `db_password` was encrypted
under a test key made `ProjectRegistry().list_databases()` raise
`ValueError: db_password could not be decrypted ...` for EVERY database (the
/next DBs tab, GET /api/databases, the Find dialog's registered dimming, the
Understanding and Curate lists, the scheduler's database pass).

Ruling: a list never fails on one bad row; the bad row renders as
"credential unreadable · re-enter credentials" and the rest list normally.

## The marker

`DatabaseEntity.credential_status` (derived on read, never stored):

| value | meaning |
|---|---|
| `"ok"` | a password is stored and decrypted |
| `"none"` | no password stored |
| `"unreadable"` | a password is stored but cannot be decrypted (wrong/rotated key); `db_password` is `""` |

The API (`DatabaseSummary`) carries `credential_status` and `credential_reason`
(`"credential unreadable · re-enter credentials"` when unreadable, else `""`).
`DiscoveredDatabase` (Find dialog candidates) carries `credential_status` for
already-registered rows (`None` for unregistered candidates). Never the
password, the ciphertext or the exception text (it names the key env vars).
One WARNING per slug per process, naming the slug and the exception class only.

## Which reads tolerate, which refuse

Tolerate (row returned, marker set, secret empty): `list_databases` (all
filters), `list_databases_in_group`, `get_database(slug, allow_unreadable=True)`,
`database_exists`.

Refuse, for THAT database only: `get_database(slug)` (default) raises
`CredentialUnreadableError` (a `ValueError` subclass; message
`credential unreadable: re-enter credentials for <slug>`). Routes that may
connect translate it: `POST /{slug}/survey`, `POST /{slug}/analyses/{id}/run`
and `POST /{slug}/publish` return 409 with that message. The scheduler's
database pass returns it as that schedule's error (db_derived analyses, which
need no credential, still run). Nothing connects with an empty password.

Call sites that only need existence/metadata now pass `allow_unreadable=True`:
GET/DELETE/PATCH-credentials and the read-only database routes, projects.py
group assignment and entity-name lookup, stats.py, db_servers.py add-database,
automate.py/schedules.py lookups, agents/tools.py, survey_meta_agent.py,
cli group-assign existence check. PATCH `/{slug}/credentials` works on an
unreadable row, which is how the user re-enters credentials and heals it.

## UI wording

The marker text is a single constant (`static/next/credential.js`):
`credential unreadable · re-enter credentials`, read from the response only.
- DBs sidebar row: marker under the name.
- Selected database header: a banner line, plus "surveys and runs are disabled
  until the credential is re-entered".
- Survey definition "run/re-run", "Plan a run…" and the enrichment-analysis
  "Run" buttons: `disabled`, title = the marker text; `planSurveyRun` also
  guards.
- Find dialog: a registered candidate whose credential is unreadable shows the
  marker beside "already registered" (its checkbox is already disabled).
No new Tailwind classes; `tailwind-next.css` is unchanged (rebuilt, no diff).

## Other lists that decrypt per row

Only `databases.db_password` is encrypted at rest (`encrypt_db_password` is
called only from `register_database` and `update_database_credentials`).
`db_servers`, `filesystems`, and every `egeria_password` column are stored
as-is and never decrypted, so no other list has this shape. Left alone:
CLI commands and the survey/Run helpers (`native_survey_run`, surveyor
entry points, `cli/main.py` survey commands) keep the strict `get_database`,
so they fail honestly for the bad slug; the CLI surfaces the ValueError
rather than a formatted message.

## Not verified

No real browser, no real Postgres (all tests use a temp SQLite registry passed
to the constructor; nothing opened localhost:5442). The survey route's refusal
was tested before any connection; a connect with a real database was not run.
