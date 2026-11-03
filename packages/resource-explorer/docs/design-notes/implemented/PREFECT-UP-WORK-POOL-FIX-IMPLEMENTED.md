# `prefect_up.sh` work-pool mismatch — implemented

**Found:** re-taking the Database Analysis Survey on `laz_local_adventureworks` (coordinator
session, 2026-09-28) — the run succeeded on the default (Prefect) engine per Section E's fix
(`#327`), but every `step_runs` row recorded `executor='local'`, and Prefect's own `flow_runs` list
showed no new flow run at all. Traced to the bare-host worker for `resource-explorer-pool` being
OFFLINE since 2026-09-26 (matches this repo's own documented risk that this worker does not survive
a reboot and nothing auto-starts it). Running the documented recovery command, `make prefect-up`,
reported success but started a worker on the WRONG pool.
**Branch:** `re/prefect-up-work-pool-fix`, off `origin/main` `3de3b1fa`.
**Queued item:** this was the named blocker for the "Prefect overhead profile" measurement the
project owner requested next — that measurement needs a genuinely Prefect-executed run to compare
against the local-execution numbers `step_runs.csv` (run `c3738802-c8c7-4f46-957e-c8ce1dcbdc7a`,
2026-09-28) already carries.

## Root cause

`packages/resource-explorer/scripts/prefect_up.sh`:

```bash
WORK_POOL="${PREFECT_WORK_POOL:-default-agent-pool}"
```

`.env` sets `PREFECT_WORK_POOL=resource-explorer-pool`, but `.env` is loaded only by Python's
`python-dotenv` inside the app process (`resource_explorer/config.py` and friends) — this is a bash
script, and bash never sources `.env` on its own. So `$PREFECT_WORK_POOL` was always unset in the
shell that runs `prefect_up.sh`, and the script silently fell through to its own hardcoded default,
`default-agent-pool` — a different pool than the one `.env` configures and the one the deployment
(`re-survey-step-deployment`) actually targets. The script's own `✓ Worker up` success message gave
no indication it had started a worker for the wrong pool; `resource-explorer-pool` stayed exactly
as offline as before.

## Fix

Added an `_env_var()` helper that greps the value straight out of `.env` when the shell doesn't
already have it set, applied to both `WORK_POOL` and (for the same reason, even though it happened
to already match by coincidence) `API_URL`:

```bash
_env_var() {
  [ -f ".env" ] || return 0
  grep -E "^$1=" .env | tail -1 | cut -d= -f2-
}

API_URL="${PREFECT_API_URL:-$(_env_var PREFECT_API_URL)}"
API_URL="${API_URL:-http://localhost:4200/api}"
WORK_POOL="${PREFECT_WORK_POOL:-$(_env_var PREFECT_WORK_POOL)}"
WORK_POOL="${WORK_POOL:-default-agent-pool}"
```

An explicit `PREFECT_WORK_POOL=... make prefect-up` (or any pre-set shell env var) still wins, same
as before — this only changes what happens when nothing is already set, which was previously
"silently use the wrong pool" and is now "read what `.env` actually says."

## Not fixed here (flagged in Backlog.md, not attempted)

The companion Backlog entries (pushed earlier, `re/coordinator-backlog-patch`) cover two related
items this branch does not attempt:

1. A `survey_definition_run` routed to Prefect still silently falls back to local execution when
   the work pool has no live worker at all, with `executor='local'` as the only visible signal —
   this branch fixes the RECOVERY script, not that silent-fallback behavior itself.
2. `resource_explorer.bootstrap`'s existing periodic monitor (already reports draft-zone/
   private-zone status) would be a natural place to also report `resource-explorer-pool` worker
   liveness, so an offline worker is a monitored condition rather than something discovered only by
   chasing an unexpected `executor='local'` on a run.

## Verification

Live, not just read: stopped the stray `default-agent-pool` worker this session had started while
diagnosing (`scripts/prefect_down.sh`, then a manual `kill -9` for the process the pidfile-based
stop didn't catch), copied `.env` into this worktree (gitignored, not otherwise present), and ran
`bash packages/resource-explorer/scripts/prefect_up.sh` directly:

```
✓ Deployed
→ Starting worker for pool 'resource-explorer-pool' (log: .prefect-run/worker.log)…
✓ Worker up (pid 435)
```

Confirmed against Prefect's own API (`POST /api/work_pools/resource-explorer-pool/workers/filter`):
a new `ProcessWorker` is `ONLINE` for `resource-explorer-pool` as of this run; the old worker stays
listed but `OFFLINE`, harmless. `prefect work-pool inspect resource-explorer-pool` (with
`PREFECT_API_URL` exported so it talks to the real server, not an ephemeral one) reports
`status=WorkPoolStatus.READY`.

**Then the stronger check the design session asked for**: one real Analysis run whose `step_runs`
rows carry `executor='prefect'`. First tried the bare default engine (no `engine_override`) —
`db_derived`/`postgres_column_profile`/`postgres_nested_columns`/`postgres_operations` all came
back `executor='local'` again, even with the worker online. Traced this before assuming the fix
hadn't worked: these four steps are declared `executes_at: resource-explorer` in
`database-survey-definition-analysis.md` (checked directly, not assumed), and
`survey_definition_executor.py`'s own routing (`_prefect_orchestration_enabled`, ~line 507) only
sends an `executes_at: resource-explorer` step through Prefect when either `engine_override ==
"prefect"` is passed explicitly for that run, or `route_local_steps` is true — and `.env` sets
`PREFECT_ROUTE_LOCAL_STEPS=false` (matching the config default). So local execution on the bare
default engine is CORRECT, by-design behavior for these particular steps, not a sign the worker fix
didn't work — the earlier IMPLEMENTED doc for Section E used `engine_override=None` in its own
prose but must have had `route_local_steps` true (or `engine_override="prefect"`) in the process
that produced its `executor='prefect'` numbers; not re-verified here, flagged as a documentation gap
in that doc rather than assumed.

Re-ran with `engine_override: "prefect"` passed explicitly (run
`85d9042f-7930-4d96-9b53-744a97014423`) — succeeded, and all four `step_runs` rows carry
`executor='prefect'`, `source='prefect'`, surveyed_at `2026-09-28T15:04:28.995989`. **Correction,
after the PR/CI session checked independently and the overhead-profiling agent confirmed
(`re/prefect-overhead-profile`, `eb5945ca`): this is NOT proof of end-to-end Prefect dispatch.**
Prefect's own `flow_runs` list (`POST /api/flow_runs/filter`, start_time after
`2026-09-28T00:00Z`) shows exactly 2 flow runs all day, both from ~03:09–03:13Z — hours before this
worker existed. `85d9042f` never dispatched through Prefect at all; `run_prefect_step`'s existing
broad `except Exception` silently swallows a failed dispatch and falls back to an in-process call,
and `step_runs.executor='prefect'` is written when the code *enters* the Prefect-dispatch branch,
not when dispatch *succeeds* — so the label is not truthful about which path actually ran (full
root cause and a proposed fix: the "Prefect dispatch honesty" item now queued behind this branch,
and the companion Backlog entry above on the silent-fallback problem).

**What this branch's fix is and is not proven to do**: the work-pool routing bug (wrong pool
read by `prefect_up.sh`) is fixed and directly verified — a real `ProcessWorker` is `ONLINE`
for `resource-explorer-pool`, confirmed against Prefect's own API, independent of any survey run.
What is NOT verified is that a Prefect-routed run now actually reaches that worker end-to-end;
`85d9042f` does not show that, because of the separate dispatch-honesty bug above. Fixing that
bug and re-running is what will produce the first evidence either way — this branch does not
attempt that fix, and the Prefect-vs-local parity measurement stays blocked until it lands.

No Python code changed — this is a bash-only fix, and this repo has no existing test coverage for
its infra scripts (`prefect_up.sh`/`prefect_down.sh`), so none was added here; the live verification
above is the evidence.

## Status

Pushed, tip reported to "Resource-explorer PR/CI merge". No PR opened by this session per the
standing convention — that session batches PRs.
