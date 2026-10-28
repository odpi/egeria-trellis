# Prefect default for whole-definition runs — implemented

**Scope:** the payoff of `re/prefect-dispatch-honesty` (real end-to-end Prefect dispatch, live
gate: `PREFECT-DISPATCH-HONESTY-IMPLEMENTED.md`). Project owner decision (via the
architecture session, 2026-09-28): Prefect becomes the DEFAULT engine for whole-definition runs —
the measured +24-27% overhead for a four-step chain buys real run history, retries, and UI
visibility. The per-step REST dispatch path (`run_prefect_step`, ~3-4x local from 1s-granularity
polling) is a separate, deliberately deferred cost tradeoff and stays opt-in
(`PREFECT_ROUTE_LOCAL_STEPS`, untouched by this change).

**Branch:** `re/prefect-default-whole-definition`, built directly on top of
`re/prefect-dispatch-honesty` (not yet merged at the time this branch was cut — rebased onto a
fresh `origin/main` fetch first) in an isolated worktree
(`/Users/dwolfson/localGit/egeria-v6/trellis-re-prefect-default`), never in the shared checkout.
Confirmed via the "Resource Explorer expansion architecture" peer session that no other branch was
touching `survey_definition_executor.py` or `bootstrap.py` tonight besides dispatch-honesty itself.

**PR:** not opened by this session — the PR/CI session batches these and merges it *after*
dispatch-honesty; tip reported there, not here.

## What changed

### 1. The whole-definition engine decision (`survey_definition_executor.py`)

`_prefect_orchestration_enabled(engine_override)` already read `config.prefect.enabled` (default
`True` since 2026-09-19) for the `engine_override is None` case — so the *structural* gate for
attempting `_run_via_prefect` was already Prefect-first once dispatch-honesty's `get_client()` fix
landed. What was missing, and what the peer session's hard requirements named explicitly, was:

- a **live reachability check** before the default case attempts Prefect at all, so an unreachable
  server never gets a silent whole-definition fallback (the old behaviour: `_run_via_prefect`'s
  broad `try/except` swallowed the failure and only logged a warning — correct as a *last-resort*
  guard, wrong as the *only* signal a default run gets when Prefect simply isn't there);
- an **honest reason surfaced to the caller** either way, on the same field the `/next` pane already
  reads.

The gate now reads, in `SurveyDefinitionExecutor._execute` (right before the point that used to call
`_run_via_prefect` unconditionally on `_prefect_orchestration_enabled(...) and
_all_steps_prefect_runnable(...) and not needs_prerequisites`):

- `engine_override is None` (the default): additionally calls
  `prefect_adapter.check_prefect_reachable_sync()` (a new sync wrapper around the existing
  `_check_prefect_reachable_async` the startup log check already used — same nested-event-loop
  handling `run_prefect_step` itself uses). If unreachable, Prefect is **not attempted**, the local
  loop runs as before, and `engine_note = "Prefect API unreachable at <url>: ran locally"`.
- `engine_override="prefect"` (explicit): **unaffected** — still always attempts, still degrades via
  `_run_via_prefect`'s own try/except on failure, no reachability precheck (the user asked
  explicitly; this gate only narrows the *unforced default*).
- `engine_override="resource-explorer"` (the escape hatch): **unaffected** — `_prefect_orchestration_enabled`
  already returns `False` for this value, so Prefect (and the new reachability check) is never
  consulted at all.
- On a successful whole-definition Prefect run, `engine_note = "running via Prefect (flow-run
  <id>)"` — the flow_run_id comes from `prefect/flows.py::run_planned_step_task`'s result dict,
  which now carries `"flow_run_id"` (previously only stamped onto the `step_runs` `Observation`,
  not returned) so `_run_via_prefect`'s report (and therefore `steps_report`) carries it through to
  the top level.

`engine_note` is threaded into both the function's return dict and the `detail` JSON
`log_survey()` persists to the activity log — the same `detail` the `/next` pane's `launchSurvey()`
already parses for per-step dispatch-honesty fallback lines.

### 2. The pane's status line (`web/static/next/app.js`)

`launchSurvey()`'s post-run handling already parsed `finishedEntry.detail` for per-step fallback
lines (dispatch-honesty, 2026-09-28). It now also reads `d.engine_note` and renders it directly —
success ("running via Prefect (flow-run …)", styled as `text-state-ok`) and fallback ("Prefect API
unreachable …: ran locally", styled as `text-state-warn`) both land on the same line, so a default
run's engine choice is never invisible, in either direction — the gate explicitly asked that the
success case, not just the failure case dispatch-honesty added, show something meaningful.

### 3. Pool-worker liveness (`bootstrap.py`)

Added `check_prefect_pool_workers(pool=None)` and a `PrefectPoolStatus` dataclass, following the
exact tri-state/fail-open convention the Dr.Egeria batch canary checks in this module already use
(`reachable`/`last_check_error` distinguish "API unreachable" from "reachable, zero workers" —
conflating those was the mechanism of the 2026-08-19 incident this module's docstring already
describes for the batch case, and the risk named for tonight's `prefect_up.sh`-wrong-pool incident
is the same shape). Queries `PrefectClient.read_workers_for_work_pool(pool)` — the same data
`work_pools/{pool}/workers/filter` returns, reached through `prefect_adapter.re_prefect_client()`
rather than a bare `httpx` call so it shares the one place RE constructs a Prefect client pointed at
its own configured URL. `pool` defaults to `config.prefect.work_pool`.

Wired into the existing periodic monitor: `_loop()` (the `resource-explorer-bootstrap` thread,
`worker.py`'s `LoopSpec(name="bootstrap-monitor", ...)`) now calls `check_prefect_pool_workers()`
every pass alongside `check_and_heal()`, and `get_status()` returns a `prefect_pool` block
(`reachable`, `worker_count`, `last_heartbeat`, `last_checked_at`, `last_check_error`) alongside the
existing `batches`/`egeria_reachable` fields the admin banner already reads. A pool with zero
workers logs a WARNING naming the exact failure mode this exists to catch: "the API is reachable but
nothing will pick up dispatched work" — reported *before* a run hits it, not discovered by one.

### 4. `step_runs` snapshot — first default-engine day

`step_runs-2026-09-28-prefect-default-day.csv` — same convention PR #333
established (`docs/design-notes/step_runs-YYYY-MM-DD.csv`). Contains the real rows already in the
shared registry for the two genuine (flow_run_id-verified) Prefect-path runs
`PREFECT-DISPATCH-HONESTY-IMPLEMENTED.md`'s live gate recorded — pulled directly from
`step_runs` by `surveyed_at`, not re-typed:

- `surveyed_at=2026-09-28T15:44:57.071334` — per-step REST dispatch (`engine_override="prefect"`),
  4 rows, `executor='prefect'`, each with a distinct verified `flow_run_id` (see the dispatch-honesty
  doc's table). Total wall_ms for the four steps: 47,000 (~3-4x local).
- `surveyed_at=2026-09-28T22:01:13.737500` — whole-definition Prefect orchestration
  (`engine_override=None`, pre-this-change but post-dispatch-honesty-fix — the same flow/task engine
  path this change makes the default), 4 rows, `executor='prefect'`, single shared
  `flow_run_id=43feb464-55fa-4783-a277-c3031caa7da8` across all four (one flow run, four tasks).
  Total wall_ms: 13,534 (~+24-27% over the ~10,000-11,000ms local baseline this same doc's cost
  table records for the identical four steps).

Marked here as the first-default-engine-day baseline for future overhead tracking, now that this
change makes the whole-definition Prefect path the one a default run actually takes.

## Live gate

Per the coordinating peer session's stated gate: one default-engine Analysis run from the pane on
`laz_local_adventureworks` must create a real Prefect flow_run, confirmed against Prefect's own API
(not merely `step_runs.executor`), and the pane's status line must name it on both success and
fallback.

Run technique: same as dispatch-honesty's own live gate (in-process, importing this worktree's code
directly against the real shared registry and the real dev Prefect server at
`http://localhost:4200/api`, `PREFECT_API_URL` deliberately **not** exported — the worker/web-server
processes at port 8810 still run `main`'s installed code, so exercising the change through them
would exercise the pre-change behaviour). Signed in as `erinoverview`
(`printf 'secret\n' | uv run resource-explorer login --user erinoverview`), signed out afterward.

```
result = run_survey_definition(
    "database", "laz_local_adventureworks", engine_override=None,
    survey_definition_ref="GovActionProcess::DatabaseAnalysisSurvey",
)
```

Result:

- `engine_note = "running via Prefect (flow-run 86c3e53f-3c1b-484c-8771-25df0089301f)"`
- `errors = []`
- All 4 `steps_report` entries: `status="ok"`, `engine="prefect"`,
  `flow_run_id="86c3e53f-3c1b-484c-8771-25df0089301f"`
- `step_runs` rows for `surveyed_at=2026-09-28T22:46:28.347916`: all four `executor='prefect'` with
  the same `flow_run_id`, matching the returned report exactly.
- Confirmed independently against Prefect's own API:
  `GET /api/flow_runs/86c3e53f-3c1b-484c-8771-25df0089301f` → `state_type: "COMPLETED"`,
  `name: "strange-ammonite"` — the id was not merely echoed back from RE's own report, it is a real,
  completed flow-run Prefect's own server has a record of.
- Pool-worker liveness cross-checked the same way: `POST /api/work_pools/resource-explorer-pool/workers/filter`
  showed one `status: "ONLINE"` worker with a fresh heartbeat at the time of the run —
  `check_prefect_pool_workers()` reads this exact endpoint's data via the Python client.

**What this run does NOT verify, stated explicitly per the task's own instruction:**

- **The pane's status line was not clicked through in a live browser.** The code path was traced
  end to end instead: `engine_note` is the exact same local variable written into both the
  function's return dict (verified above) and the `detail` JSON `log_survey()` persists
  (`json.dumps({..., "engine_note": engine_note})`) — the same `detail` field `app.js`'s
  `launchSurvey()` already parses for the per-step dispatch-honesty fallback line, now also reading
  `d.engine_note`. This is the same mechanism, not a separate one, but it was not confirmed by
  loading `/next` in a browser and watching the note render, because the shared checkout's dev
  server at port 8810 runs `main`'s installed code (pre-this-change) and this worktree has no
  server of its own started for this pass.
- **The unreachable-fallback branch's exact message was unit-tested, not live-gated** — reproducing
  "Prefect reachable and configured, but this run's default path can't reach it" live would require
  either stopping the shared dev Prefect server (disruptive to any other concurrent user) or
  reconstructing the exact no-`PREFECT_API_URL`-exported condition the dispatch-honesty doc used for
  its own pre-fix reproduction; `TestEngineOverrideNoneNowDefaultsToPrefect::
  test_unreachable_prefect_falls_back_local_with_named_reason` covers it at the unit level instead.

## Tests

`uv run pytest tests/ -q -rf` (full suite, no `-k`, no deselects): **6749 passed, 103 skipped, 0
failed**, 757.30s. The trailing "Logging error"/`ValueError: I/O operation on closed file` lines
after the summary are teardown noise from an ephemeral Prefect subprocess server shutting down
after the suite finished — not a test failure, and not new (the same class of noise `prefect_adapter.py`'s
own import-time guard exists to prevent *leaking*, not to prevent this harmless log-write-after-close).

New:
- `tests/test_prefect_default_whole_definition.py` — 4 tests: `engine_override=None` attempts and
  names a reachable Prefect success; `engine_override=None` falls back local with a named reason
  when unreachable (never silently); `engine_override="resource-explorer"` still forces local and
  never even checks reachability (escape hatch unchanged); `PrefectConfig.route_local_steps`'s own
  declared default is still `False` (regression guard against flipping the wrong flag).
- `tests/test_bootstrap_prefect_pool_liveness.py` — 3 tests: a reachable pool with workers reports
  count/heartbeat and lands in `get_status()`'s `prefect_pool` block; a reachable-but-empty pool is
  reported as reachable with `worker_count=0` (not conflated with unreachable); an unreachable
  Prefect API reports `reachable=False`, never a false "0 workers".

## Files changed

- `resource_explorer/surveyors/survey_definition_executor.py` — the whole-definition engine
  decision gate, `engine_note` threading into the result dict and activity-log detail.
- `resource_explorer/surveyors/prefect_adapter.py` — `check_prefect_reachable_sync()`.
- `resource_explorer/prefect/flows.py` — `run_planned_step_task` result now carries `flow_run_id`.
- `resource_explorer/bootstrap.py` — `PrefectPoolStatus`, `check_prefect_pool_workers()`, wired into
  `_loop()` and `get_status()`.
- `resource_explorer/web/static/next/app.js` — `launchSurvey()` renders `engine_note`.
- `tests/test_prefect_default_whole_definition.py`, `tests/test_bootstrap_prefect_pool_liveness.py`
  — new.
- `step_runs-2026-09-28-prefect-default-day.csv` — new, real rows pulled from the
  shared registry.
