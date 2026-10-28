# Prefect never receives a database password (implemented)

Stacked on `re/parity-g2-db-registration-credentials` (G2). Owner, 2026-10-07: dispatch the fix so the
preferred Prefect path is exercised.

## Phase 1 - where a password could enter Prefect, and what Prefect keeps

Found by reading `surveyors/prefect_adapter.py`, `prefect/flows.py`, `surveyors/survey_definition_executor.py`,
`scripts/prefect_up.sh`, `cli/main.py` (deployment). Nothing was run against a real Prefect.

| Place | Carried the password before? | Persisted by Prefect? |
|---|---|---|
| Flow-run `parameters` of the REST dispatch (`_run_prefect_step_api`, deployment `RE Survey Flow`): `runner_kwargs.db_user/db_pwd` | yes (stored and override) | YES: flow-run parameters are stored in Prefect's database and shown in its UI |
| Flow-run parameters of the whole-definition flow (`_run_via_prefect` -> `re_survey_definition_flow(runner_kwargs=...)`). It is called in RE's own process, but Prefect's engine still records a flow run (parameters included) when an API is configured | yes | YES, same as above |
| Task inputs (`run_surveyor_step_task`, `run_planned_step_task`) | yes, same dict | Prefect 3 stores only dependency references for task inputs, not values; not relied on - the dict is clean now |
| Flow result (`re_survey_flow` has `persist_result=True`) | only if a step output or error echoed it | YES (result storage). Outputs and raised errors are now scrubbed in the task |
| Failed-state message / traceback | only if a driver error echoed the password | YES (state message, run logs). Exceptions are now re-raised scrubbed, with no chained original |
| Run logs shipped to the Prefect API | only via an echoing error line | YES. `redacting_logs` now wraps the task body too (it already wrapped RE's run) |
| Deployment parameters (`prefect deploy`) | no, only the flow signature | yes, but there is nothing secret in it |
| Worker environment | not the target database password; it inherits RE's own registry/DB settings from the shell/.env | the process environment is not persisted by Prefect |
| Soda / Great Expectations tasks | no: they already read `entity.db_user/db_password` from the registry inside the worker (and so silently IGNORED an override) | n/a |

The worker is a host process, same checkout, same shared registry, so it can read the stored credential
itself. It cannot read RE's process memory.

## Phase 2 - what was built

* `resource_explorer/credential_handoff.py`
  * `split_credentials(entity, runner_kwargs, credential_scope)`: strips `db_user/db_pwd`; a credential equal to
    the stored one is simply dropped (stored), anything else, or anything with `credential_scope`, is an override.
  * `resolve_credentials(entity, credential_ref)`: where the step runs; the override behind the reference, else
    the stored one. In memory only.
  * `put/get/revoke`: the in-memory override store: Fernet-encrypted under a per-process key never written down,
    random `cred-...` reference, 1 h TTL, revoked by the executor in a `finally` when the run ends.
  * `assert_no_credentials(params, known_secrets)`: raises `CredentialLeakError` (names the key, never a value)
    for key names password/passwd/pwd/secret/token/api_key, `db_user/db_pwd/db_password`, DSNs carrying a
    password, or a value containing a known password. `credential_ref` is allowed.
* `prefect_adapter.py`: guard before `create_flow_run_from_deployment`, at the top of `run_prefect_step` (outside
  the broad `except`, so it cannot become a quiet local fallback) and before the in-process flow call;
  `run_prefect_step(..., credential_ref=)`; `_run_step_in_process_flow`.
* `prefect/flows.py`: `run_surveyor_step_task(credential_ref=)` resolves credentials inside the task, redacts logs,
  scrubs output and exceptions; Soda/GE now use the resolved credential (so an override applies to them);
  new flow `re_survey_step_in_process_flow`; `credential_ref` threaded through `re_survey_definition_flow` and
  `run_planned_step_task`.
* `survey_definition_executor.py`: per-step and whole-definition crossings strip credentials; G2's "not run, not
  sent to Prefect" branch is removed and the `not credential_scope` condition on whole-definition Prefect is gone.

### B: which design, and why

Chosen: **opaque reference in RE's memory (i), valid only for flows that run in RE's process, plus (ii)** for the
per-step case. A Prefect worker is a different process, so a reference in memory cannot reach it.

* whole-definition: the flow already runs in-process, so (i) works in full: parameters carry only the reference.
* per-step override: `re_survey_step_in_process_flow` runs in RE's process (Prefect records state, logs and a
  flow-run id) with only the reference; if Prefect is unreachable it degrades to the local call and says so.
* per-step STORED: dispatched to the real worker over REST with no credential at all.

An override is therefore never executed by a Prefect worker.

### DDL deliberately NOT built

To let a **worker** run an override step you would need a shared store both processes can read, e.g.
`run_credential_handoff(ref TEXT PRIMARY KEY, ciphertext TEXT NOT NULL, created_at TIMESTAMPTZ NOT NULL,
expires_at TIMESTAMPTZ NOT NULL, consumed_at TIMESTAMPTZ)` with delete-on-first-read. Trade-off: the override
would then touch RE's registry (encrypted, short-lived) which G2's "session memory only" rule currently forbids,
and the encryption key (`credential_crypto`) would have to be shared with the worker. Not needed for the
preferred path to run; left for the owner to decide.

## Not verified here

No real Prefect server, worker, or live page was run. Tests use a fake Prefect that records every parameter and
runs the real task bodies. Whether `re_survey_step_in_process_flow` records a run against a live server in the
live RE process (ambient `PREFECT_API_URL`, see `re_prefect_client`) is unproven; the unreachable case degrades
locally.

Tests: `tests/test_prefect_no_password_params.py` (and the two G2 tests that encoded the old skip, updated).
