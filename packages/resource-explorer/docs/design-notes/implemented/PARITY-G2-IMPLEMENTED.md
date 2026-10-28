# Parity slice G2 — database registration, credentials, generic Curate — implemented

Branch `re/parity-g2-db-registration-credentials`. Brief: `BRIEF-PARITY-G1-G3-TO-ALPHA.md`, section G2.
Inventory ids close as below (DONE / retired / done-by-design), so the next inventory run reads them.

Import check (must resolve into the worktree):
`/Users/dwolfson/localGit/egeria-v6/trellis-re-parity-g2/packages/resource-explorer/resource_explorer/__init__.py`

## Credential safety, the first rule

* No password appears in any response, activity row, run row, log line, URL, error text or committed file.
  Tests use `test-password-not-real` and grep the stored rows, the responses, `caplog` and the DOM for it.
* A credential typed for one run is **browser memory only** (`next/run-credential.js`, a module-level Map; not
  localStorage, sessionStorage, a URL or markup — the field value is set as a property).
* The server does not keep it either. The run queue persists its payload in the registry, so a run that carries a
  password is **not enqueued**: `POST /api/survey-definitions/{type}/{slug}/run` with `db_pwd` starts the run in this
  process (`workflows.survey_definition.start_in_process`), forces RE's own engine (a Prefect flow would keep the
  password as a flow parameter), and returns `run_id: null` and `ran_as`. This also closes a pre-existing path:
  Classic's run modal posted `db_pwd` and it was written into the queue row.
* `secret_redaction.scrub` / `redacting_logs`: a driver error that echoes the password it was given is scrubbed from
  step rows, the result, the activity detail and every log line for the length of the run (any run, override or stored).

* **Prefect never receives an override.** Prefect keeps a flow run's parameters. With a credential override, a step
  that would dispatch to Prefect (`executes_at: prefect`, or the Prefect-only soda / great-expectations steps) is not
  run: its row says "not run · this run's credentials are not sent to Prefect (they are never stored)" and the run
  counts it as unfinished; whole-definition Prefect is refused too (`survey_definition_executor._execute`).
* **Redaction covers every logger and handler** (`secret_redaction`): done through the log record factory, so
  non-propagating loggers, handlers added mid-run and overlapping runs are covered, and percent-, form-, repr- and
  JSON-encoded spellings (a DSN) are scrubbed too.
* **The in-process run is the caller's and reconcilable.** `start_in_process` runs under `contextvars.copy_context()`
  (as the chat turn does), so Egeria publish and private-zone reads see the signed-in user; the activity row carries
  `_runner` (pid and start time), which the existing `run_reconciler.reconcile` reads: no further change needed.

## Backlog row (not this slice)

* **Stored password reaches Prefect flow-run parameters for soda/GE steps on main; own slice.** `runner_kwargs` is
  filled from `entity.db_password` in `SurveyDefinitionExecutor.run`, and `prefect_adapter` puts `runner_kwargs` into
  the flow-run `parameters`. Unchanged here.

## Rows

| PI | State | What and where |
|---|---|---|
| PI-015 | DONE | `POST /api/databases/_test-connection` (`credential_check.probe_database_connection`, one sentence, stores nothing); `next/db-register.js` panel under Find databases → Saved sources → "Register one database…"; Register enabled only after a pass for the values on screen; writes through the existing `POST /api/databases/register`, which now leaves a `register` activity row naming who; row afterwards is read back from `GET /api/databases/{slug}/registration`: "saved · you · just now". |
| PI-016 | DONE | Run dialog (`planSurveyRun`): "Use different credentials for this run" (user, password, "remember for this session"); step rows say "ran as <user> (this run)" (`ran_as` stamped by the executor only on steps that ran RE-side) and "answered by …". |
| PI-021 | DONE | `next/credential-change.js`, reached from the database header ("Change credentials…") and from each registered database under a saved server. Uses the existing `PATCH /api/databases/{slug}/credentials` (connect test before save, activity row, `credential_changed_at`, omsecrets projection); then reads `GET /api/databases/{slug}/credential-drift` and prints "in registry · in .omsecrets · in sync", or says drift / not checked. No DDL: `credential_changed_at` already exists. |
| PI-018 | DONE | "Try Egeria first (hybrid)" in the run dialog, enabled only when the resource's publish-state line proves it cataloged (`is_published`); otherwise disabled with "not cataloged in Egeria · catalog it on Curate first". Unticked or disabled sends `force_custom` (reaches the `egeria-adaptive` handler); every step row says which source answered (`answered_by`). |
| PI-019 | DONE | A database definition whose last run failed with the executor's not-cataloged sentence shows "Catalog now →" (routes to the Curate stage, whose Catalog button is the only write) and, once `is_published`, "Retry →" on the same row; Retry opens the run plan. No automatic retry. |
| PI-020 | DONE | `launchSurvey` shows the first sentence of the first failure on the launch note at once; the note is drawn from `state.launchNote`, so the pane reload does not wipe it. |
| PI-014 | DONE | Saved-server rows show the Egeria connection (URL, view server, user, host; no password exists in the summary) and, per database, "surveyed <ago>" or "never surveyed". Read only. |
| PI-017 | retired | Classic's whole-database survey modal. A database survey is a Survey Definition run (PI-043, done); the credential override and hybrid switch live in that run's dialog. |
| PI-029 | retired | Per-row group assignment in the sidebar. Controls live on the resource (the resource-controls ruling); group assignment is DONE on the resource and in Admin → Groups. |
| PI-027 | done by design | New notes are journal entries ("Save entry", permanent, signed). The Classic notes list stays read-and-delete for old unsigned notes. Recorded, not built. |

## Decisions the brief did not settle (flagged for the owner)

1. The Find dialog's three tabs are pinned by an existing test, so "Register one database…" is a button beside
   "+ Register a server" on the Saved sources tab rather than a fourth tab.
2. "Admin → servers" is not a separate screen in /next; the saved-server rows in Find databases are that view, and
   the header is the other door. The resource menu (hide / remove) is pinned to two items, so it was not extended.
3. A database not cataloged in Egeria runs with `force_custom` (local scan only), because the disabled hybrid switch
   must mean what it shows. Before, an `egeria-adaptive` step could catalog such a database on demand.
4. The drift check compares collection presence only, as `ProjectRegistry.check_credential_drift` does; value-level
   drift is still the gap its docstring names.
5. The test-then-register rule is enforced in the form; the register route itself is unchanged and does not require
   a prior test.

## Tests

`tests/test_parity_g2_registration.py`, `tests/test_parity_g2_override_run.py`, `tests/test_secret_redaction.py`;
`frontend-build/test-harness/g2-register-one-database.test.mjs`, `g2-run-credentials-and-hybrid.test.mjs`,
`g2-change-credentials.test.mjs`. Not run: the whole pytest suite, the live page, any real database or Egeria.
