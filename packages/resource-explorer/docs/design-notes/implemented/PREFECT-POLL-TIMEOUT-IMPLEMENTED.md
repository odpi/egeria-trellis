# Prefect step dispatch: bounded poll and no-worker fail-fast — implemented

**Found:** 2026-10-02, by the PR/CI session reading the code. **Branch:** `re/prefect-poll-timeout`,
off `origin/main` `ad46f8fd`.

## Root cause

`resource_explorer/surveyors/prefect_adapter.py`, `_run_prefect_step_api` (the poll was `while True`
with an unconditional `await asyncio.sleep(1.0)`, around line 276 before this change). The function
creates a flow run from deployment `RE Survey Flow/re-survey-step-deployment` and polls its state
until Completed / Failed / Cancelled. A run whose pool has no online worker never leaves
`Scheduled`, so the loop never ends: the survey hangs in the web server's worker thread with no
error and no record. The owner's `resource-explorer-pool` had no online worker for days until
`make prefect-up` was run on 2026-10-02. The local-loop path in `survey_definition_executor.py`
reaches this through `run_prefect_step` for any `executes_at: prefect` step.

## Layer 1 — fail fast

After fetching the deployment and before `create_flow_run_from_deployment`, `_run_prefect_step_api`
reads `client.read_workers_for_work_pool(<pool>)` (read-only; same client path and call as
`bootstrap.check_prefect_pool_workers`, via `re_prefect_client()`). The pool is the deployment's own
`work_pool_name` (what the run will really be queued on), falling back to `config.prefect.work_pool`.
At least one worker with `status == ONLINE` is required (the bootstrap monitor counts *registered*
workers, which includes OFFLINE ones; that is not enough here). Otherwise no flow run is created
and `RuntimeError("no online Prefect worker for pool <name>; run `make prefect-up`")` is raised.

Policy was not invented: `run_prefect_step` already falls back to a local in-process run on any
non-cancel exception, recording the cause in `dispatch_info["dispatch_failed"]`, which the executor
persists as `step_runs.executor='local'` plus `dispatch_failed` and shows as "ran locally: Prefect
dispatch failed — <cause>". So with no online worker the step runs locally and the honest cause is
recorded; nothing changed about *which* steps may fall back.

## Layer 2 — bounded poll

The loop now has a deadline: `clock() + timeout_seconds`, checked after each state read (so a run
that reaches a terminal state in time behaves exactly as before). On expiry:

1. best-effort cancel (`set_flow_run_state(Cancelled(), force=True)`, same call as the Admin cancel
   route) so no Scheduled run is left behind;
2. `PrefectFlowRunTimeout` is raised: `timed out after N s waiting for Prefect flow run <id> (state
   <state>); the run was cancelled` (or `; cancel attempt FAILED (<error>) - the run may still be
   live`). It carries `.flow_run_id`.

`run_prefect_step` re-raises it exactly like `PrefectFlowRunCancelled` (no local fallback: the run
may still be live and a local re-run would duplicate the work). The executor catches it and appends
an honest `status: "error"` step report (with `flow_run_id` and `detail`) plus an `errors` entry,
then continues with the remaining steps.

**Not a pass, no findings:** the step's output, `_record_cost` (the `step_runs` row) and
`step_outputs` are only written after `run_prefect_step` returns; on a timeout none of them happen,
so there is no cost row and no finding rows for the step. Pinned by a test that counts
`_record_cost` calls (only the other step's).

Injectable for tests: `_run_prefect_step_api(..., timeout_seconds=, poll_interval=, clock=, sleep=)`.

## The default and its basis

`PrefectConfig.step_timeout_seconds`, env `PREFECT_STEP_TIMEOUT_SECONDS`, must be > 0, default
**1200 s (20 min)** (`config.DEFAULT_PREFECT_STEP_TIMEOUT_SECONDS`). Basis: the slowest measured
Prefect-eligible step is `secret_scan` (`repo_secret_scan`), median 330.8 s (n=1) in
`docs/funnel-cost-measured.md`, declared compute "minutes" (the PR/CI session quoted 277 s / median
331 s; the checked-in doc has only the 330.8 s single run). 1200 s is about 3.6x that median:
generous for a slower repo, still bounded. It is a per-step ceiling, not a per-survey one, and is
one number for all steps (no per-step catalog lookup).

## What happens to a timed-out run

Cancelled in Prefect if the cancel call succeeded (the message says so); if not, the message says
the run may still be live and gives its id so a person can find and cancel it (Admin "Prefect"
panel). The step is an error in the survey; the survey continues.

## Tests

`tests/test_prefect_poll_timeout.py` (14 tests; Prefect client stubbed, fake clock, SQLite temp
registry, `PGVECTOR_PORT=1`). On `origin/main` sources: 13 fail and the 14th (no online worker,
run through `run_prefect_step`) never returns — it reproduces the hang.

## Not verified

- Never run against a real Prefect server, a real worker pool or a really hung run (forbidden for
  this task). The `status == ONLINE` comparison and the `Cancelled()` cancel of a `Scheduled` run
  rest on the installed Prefect 3.8.1 schemas and the stub, not on a live check.
- `read_workers_for_work_pool` returning the `status` field was checked against the stub only.
- The whole-definition path (`_run_via_prefect` -> `re_survey_definition_flow`) is a separate
  Prefect wait and was not examined or changed here.
- The 1200 s default rests on one measured run (n=1).
