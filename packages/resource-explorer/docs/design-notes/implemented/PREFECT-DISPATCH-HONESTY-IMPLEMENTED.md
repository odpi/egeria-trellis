# Prefect dispatch honesty — implemented

**Scope:** four parts approved by the architecture session on 2026-09-28 — (1) fix the
`get_client()` ambient-settings bug in every call site, (2) a ratchet test against it recurring,
(3) a startup reachability check, (4) `step_runs.executor` honesty plus a root-cause of the
`_any_step_needs_prerequisites` "still runs locally" question.

**Branch:** `re/prefect-dispatch-honesty`, off `origin/main` (`df00aa1a`, confirmed the current tip
before starting — `origin/main` had moved from `3de3b1fa` to `df00aa1a`, #333/step_runs-snapshot,
between the worktree being created and work starting). Built in an isolated worktree
(`/Users/dwolfson/localGit/egeria-v6/trellis-re-prefect-honesty`), never in the shared checkout.
Confirmed no file overlap with the concurrently-landing `re/prefect-overhead-profile`
(`git merge-tree --write-tree origin/re/prefect-overhead-profile origin/main` — clean, that branch
only touches docs/a CSV, not the Python files this branch changes).

**PR:** not opened by this session — the PR/CI session batches these; tip reported there, not here.

## Why some of today's Prefect evidence was real and some wasn't

This is the answer the coordinating session's investigation asked to have stated explicitly,
because it explains a day's worth of confusing, contradictory reports.

`_run_prefect_step_api()` (the per-step REST-dispatch path) called `prefect.client.get_client()`
with no way to point it at RE's own configured server. On the *installed* Prefect version (3.8.1)
this is worse than the brief assumed: `get_client()` takes **no `api=` argument at all** — it
resolves the server address itself, from `PREFECT_API_URL.value()`, which reads Prefect's own
ambient settings machinery, never `resource_explorer.config`'s `PrefectConfig.api_url`. A live
process with `PREFECT_API_URL` correctly exported in its OS environment would still see
`get_client()` succeed only because Prefect's *own* settings resolution happened to see the same
env var — coincidence, not causation. A process where the env var was never exported (a plain
`uv run python3 -c ...` invocation, or the web server started a different way) had `get_client()`
raise `ValueError: No Prefect API URL provided`, which `run_prefect_step`'s existing broad
`except Exception` (deliberately broad — see its own docstring, the UnboundLocalError incident)
caught and logged as an ordinary "API unreachable, falling back to local" — **while
`step_runs.executor` had already been written as `'prefect'`**, because it was set when the code
*entered* the Prefect-dispatch branch (`step_cost_observer.observe(..., executor="prefect", ...)`
called before `run_prefect_step` was even invoked), not when dispatch actually succeeded.

So: whenever `PREFECT_API_URL` happened to be set in a process's environment *and* Prefect's own
ambient resolution picked it up, dispatch worked and the `step_runs` row was honest by accident.
Whenever it wasn't, dispatch silently fell back to local execution and the row still said
`'prefect'` — indistinguishable from a real dispatch without checking Prefect's own API. That is
exactly why two genuine Prefect flow-runs happened at ~03:09–03:13Z today (a script/environment
where `PREFECT_API_URL` was exported before Prefect's own first import) while this afternoon's
runs through the running web server did not — both were labelled `executor='prefect'` in
`step_runs`, and only one of them was true.

**A second, distinct instance of the identical shape** was found live verifying this fix (see
"Live gate" below): the *whole-definition* Prefect path (`_run_via_prefect` →
`re_survey_definition_flow`, a real `@flow` invoked directly in-process) failed with the exact same
`"No Prefect API URL provided"` message — not from any of RE's own REST calls (those all now go
through the fix below), but from **Prefect's own flow/task engine**, which constructs its own
internal API client to record flow-run/task-run state whenever a `@flow`/`@task` actually executes.
That construction is Prefect's code, not RE's, so wrapping RE's own client calls does not reach it.
Fixed at `resource_explorer/__init__.py` — see part (1) below.

## (1) Every `get_client()` call site fixed

`grep -rn "get_client(" resource_explorer/` found three real call sites (the fourth hit was a
comment): `surveyors/prefect_adapter.py::_run_prefect_step_api` and three in
`web/routes/prefect_status.py` (`_reachable`, `_list_flow_runs`, `_cancel`).

Added `resource_explorer.surveyors.prefect_adapter.re_prefect_client()` — constructs
`prefect.client.orchestration.PrefectClient(config.prefect.api_url)` **directly**, bypassing
`get_client()`'s convenience wrapper entirely. `PrefectClient.__init__` takes `api` as a required
positional argument and does no ambient-settings resolution of its own, which is exactly the
property this fix needs. All four call sites now go through it.

**Second fix, found live (see "Why some evidence wasn't real" above):** `resource_explorer/__init__.py`
now also sets `os.environ.setdefault("PREFECT_API_URL", config.prefect.api_url)` at package import
— the same place, same reasoning, as the existing `PREFECT_SERVER_EPHEMERAL_ENABLED` guard already
there ("more than one module imports `prefect` directly... this is the one place guaranteed to run
before any of them"). This makes Prefect's own ambient settings agree with RE's configured URL, so
`@flow`/`@task` invocations Prefect's own engine handles internally see the same server RE's
explicit client does. `setdefault()` — an operator's own `PREFECT_API_URL` is never overridden.

**Why this has to be at import time, specifically, and not just "early":** Prefect 3.8.1 freezes
its settings at first import — `prefect.context` builds a `GLOBAL_SETTINGS_CONTEXT` once, reading
the environment as it stands at that moment, and nothing set afterward reaches that frozen field
(this repo's own `tests/conftest.py`, the `ephemeral_prefect` fixture's docstring, already
documents the same mechanism for the exact same reason). `resource_explorer/__init__.py` is the
one place in this package guaranteed to run before *any* other module's `import prefect` —
`web/routes/prefect_status.py` imports `prefect.client.orchestration` directly, independent of
`prefect_adapter.py`'s own import order, so a guard placed anywhere else could lose the race
depending on which module happens to import `prefect` first. Anywhere later — a function body, a
lazy import inside `re_prefect_client()` itself — would be "early" in wall-clock terms but wrong in
the one sense that matters: Prefect may have already read (and frozen) the empty value by the time
that code ran. This is also why the fix is accepted specifically for living next to the existing
`PREFECT_SERVER_EPHEMERAL_ENABLED` guard (the same freezing behavior, the same fix location, the
same reasoning already reviewed and trusted here) rather than as a new, independent mechanism, and
why the ratchet test in part (2) matters beyond just `get_client()` itself: it is what keeps this
guard's precondition true going forward — a future `get_client()` call site anywhere in the package
would still read the correct, agreed-upon `PREFECT_API_URL` because of this fix, but the ratchet is
what stops a new call site from silently reintroducing a path that doesn't go through
`re_prefect_client()` and could drift from it.

## (2) Ratchet test

`tests/test_no_ambient_prefect_client.py` — an `ast` walk over `resource_explorer/`, following
`test_no_silent_success.py`'s source-scan shape rather than inventing a new one. Fails on **any**
call to a function/attribute named `get_client`, anywhere, except inside
`prefect_adapter.py::re_prefect_client()` itself (which is checked separately to never call
`get_client()` under a different name). No baseline JSON: unlike `test_no_silent_success.py`'s 112
pre-existing sites, this repo has zero legitimate `get_client(` call sites today — a hard
zero-tolerance assertion, not debt to grandfather in.

## (3) Startup reachability check

`prefect_adapter.log_prefect_reachability_at_startup()` (sync, for `worker.py`'s `run_worker`) and
`alog_prefect_reachability_at_startup()` (async, for `web/app.py`'s FastAPI `lifespan`, which
already owns a running event loop) — opens a client via `re_prefect_client()`, calls
`api_healthcheck()`, and logs either:

```
Prefect API reachable at http://localhost:4200/api
```

or a loud warning naming the configured URL, the failure detail, and what it means
(`step_runs.executor` will correctly say `'local'` with a `dispatch_failed` reason, not silently
mislabel). Skips the check entirely when `config.prefect.enabled` is False. Never raises — an
outage at startup must not block the web or worker role from serving.

Wired into `web/app.py::_lifespan` (before the embedded-worker thread starts) and
`worker.py::run_worker` (before `_reconcile_orphaned_runs`, alongside the existing
draft-zone/private-zone bootstrap log lines this was modelled on). Verified locally: logs
`Prefect API reachable at http://localhost:4200/api` against the real dev server.

## (4) Dispatch honesty

**`step_runs` schema — additive only**, per the architecture session's instruction: existing
`executor`/`executor_ref` keep their exact prior meaning and every existing row is untouched. Two
new nullable columns, added the same way every other post-hoc column in this table was
(`_step_run_cols` / `ALTER TABLE ... ADD COLUMN`):

- `flow_run_id TEXT DEFAULT ''` — the real Prefect flow-run id, populated only when a dispatch
  genuinely went through Prefect. Checkable against `POST /api/flow_runs/filter` directly.
- `dispatch_failed TEXT DEFAULT ''` — non-empty only when a Prefect dispatch was attempted and fell
  back to local execution; the exception text `run_prefect_step` caught.

**`run_prefect_step()`** (`prefect_adapter.py`) gained an optional `dispatch_info: dict | None`
kwarg it fills in-place with the truth — `{"engine": "prefect"|"local", "flow_run_id": str,
"dispatch_failed": str}` — after dispatch actually resolves, one way or the other. Existing
callers/tests that ignore it see byte-identical return values (the function's actual return value
is unchanged; only the new kwarg is additive). `_run_prefect_step_api()` now returns
`(result, flow_run_id)` instead of bare `result`, so the id is available to fill `dispatch_info`.

**`survey_definition_executor.py`'s per-step Prefect branch** no longer bakes `executor="prefect"`
into `step_cost_observer.observe(...)` before dispatch is attempted. It opens with the honest
default (`executor="local"`), calls `run_prefect_step(..., dispatch_info=...)`, and corrects the
`Observation` (`executor`, `source`, `executor_ref`, `flow_run_id`, `dispatch_failed`) to the real
outcome before `_record_cost` persists the row. The `steps_report` entry's `engine` field reflects
the same truth, and carries a `detail: "ran locally: Prefect dispatch failed — <reason>"` when a
fallback happened — the UI contract below reads this field.

**UI pane line** (`app.js::launchSurvey`, the "Launched ... it runs asynchronously" note — found by
grepping for that exact line per the architecture session's pointer, not guessed at a Questions-row
location): after `pollActivity` resolves, it now parses the finished activity entry's `detail.steps`
(the same JSON shape `openRunsList` already reads) for any entry whose `detail` starts with `"ran
locally: Prefect dispatch failed"`, and appends those lines under the existing note in a warning
tone. A run where nothing fell back looks exactly as it did before this change.

**The whole-definition orchestration path (`_run_via_prefect` → `prefect/flows.py::
run_planned_step_task`) was checked for the same silent-baking shape and does NOT have it**:
`run_planned_step_task` only ever executes by genuinely running as a Prefect task inside a live
flow (no REST-dispatch-then-fallback branch the way `run_prefect_step` has) — reaching that line at
all is proof the task is real, so `executor="prefect"` there was always trustworthy. Its
`flow_run_id` was simply never recorded; added via `prefect.runtime.flow_run.id`, read inside the
task (returns `None` gracefully outside a flow context), for the same "checkable against Prefect's
own API" completeness the per-step path now has, not because it was dishonest.

### Root cause: `_any_step_needs_prerequisites` and "still runs locally"

Investigated per the brief's fourth bullet. **The structural half of this was already fixed**, by
`PREFECT-PREREQUISITE-RESOLUTION-IMPLEMENTED.md` (2026-09-27/28, landed on `main` before this
branch started): `_any_step_needs_prerequisites` asks `prerequisite_resolver.resolve()`, which
correctly reports `SATISFIED` from stored data via `step_preconditions.unmet()` (any row of any
age); `build_plan()`'s own structural check used to disagree — it only knew whether a producer was
*authored into the definition*, not whether stored data already answered the question — and that
doc fixed it by threading `registry`/`entity` through so `build_plan` also consults a
(stricter, 24h-windowed) `step_preconditions.fresh_hit()` before raising. Confirmed by reading that
fix and by two new regression tests here
(`tests/test_survey_definition_executor.py::TestFreshStoredAnswerActuallyReachesPrefect`) that seed
a **real, fresh `database_tables` row through a real `ProjectRegistry`** (not a mock) and assert
`re_survey_definition_flow` is actually called and the local per-step runner is not — the positive
case — with a negative control (no stored row at all) confirming the same test would have caught a
regression: without the seed, the resolver correctly reports non-SATISFIED, the auto-run producer
fires on the local loop, and `re_survey_definition_flow` is never reached.

**So why did it still look like it "ran locally" today?** Not `_any_step_needs_prerequisites` at
all — the live gate below is the direct demonstration: with fresh data (SATISFIED, routed to
Prefect structurally, exactly as designed), the run *still* silently fell back to the local loop,
for the reason stated above — `re_survey_definition_flow`'s own internal Prefect-engine client hit
the exact same ambient-`PREFECT_API_URL` bug this section's part (1) fixes. The question conflated
two independently-diagnosable failures that happened to produce the same symptom: a structural
routing bug (already fixed, now regression-tested) and a client-configuration bug (fixed here).
Once both are fixed, a fresh stored answer both *decides* to route to Prefect and *actually reaches
it* — demonstrated live below.

## Live gate (real shared registry, real Prefect server, `laz_local_adventureworks`)

Per the architecture session's explicit answer (2026-09-28): the dev Prefect server, shared
registry and `laz_local_adventureworks` database are ordinary shared resources here — no
additional caution beyond recording run ids/timestamps. All runs below were made **in-process**,
importing this worktree's own code directly against the real shared registry
(`REGISTRY_DATABASE_URL` default) and the real Prefect API server at `http://localhost:4200/api`
— the same technique `PREFECT-PREREQUISITE-RESOLUTION-IMPLEMENTED.md` used and for the same reason:
the deployed worker and the run-queue leader (port 8810) both still run `main`'s installed code, so
exercising the fix through them would exercise the OLD, unfixed client construction. Signed in as
`erinoverview` (`printf 'secret\n' | uv run resource-explorer login --user erinoverview`) before
starting, signed out (`resource-explorer logout`) afterward — and confirmed the two
`test_cli_workflow_commands.py::TestRunsCommands` failures a first full-suite run showed
(`test_enqueue_queues_arbitrary_work`, `test_enqueue_can_attribute_to_a_user`) were exactly the
documented cached-session gotcha (`docs/Backlog.md`), not a regression: reproduced by re-running
those two tests alone with the session still cached (failed), then again after `logout` (passed).

**`PREFECT_API_URL` deliberately not exported in the shell** for every run below — the exact
scenario the bug needed, and the one a real running server process is actually in.

**Analysis run, `engine_override="prefect"`** (per-step REST dispatch — `_any_step_needs_prerequisites`
found real work to do at the time, so this used the per-step path rather than the whole-definition
one): 4 real flow-runs created and completed, confirmed independently against Prefect's own
`POST /api/flow_runs/filter` (not merely trusted from `step_runs`):

| flow-run id (Prefect API) | name | state | tag |
|---|---|---|---|
| `6e3ed6b6-8f18-4840-a988-8ff23d90d944` | abstract-crocodile | COMPLETED | `step:postgres_column_profile` |
| `6a88f563-c568-4a57-9277-209ab4afe694` | daffodil-mole | COMPLETED | `step:postgres_nested_columns` |
| `daa4a3ef-4b0a-40d2-a8cd-62b1e6db6d18` | wooden-lizard | COMPLETED | `step:db_derived` |
| `5c35bc04-1a5e-49aa-a6d1-b59004c0cc30` | eccentric-macaque | COMPLETED | `step:postgres_operations` |

`step_runs` for this run's `surveyed_at=2026-09-28T15:44:57.071334`: all four rows `executor='prefect'`
with `flow_run_id` matching the table above exactly.

**Default-engine run after a Scouting-equivalent refresh** (`engine_override=None`, config default
— `PREFECT_ENABLED=true`): ran a local refresh first (`engine_override="resource-explorer"`, fast,
no errors, `surveyed_at=2026-09-28T21:57:47`) to give the resolver fresh stored data, matching the
brief's "after a Scouting run" shape. The first default-engine attempt (before the part-(1) `__init__.py`
fix landed) reproduced the whole-definition-path bug directly: logged `"Prefect orchestration
unavailable for GovActionProcess::DatabaseAnalysisSurvey (No Prefect API URL provided...)"` and fell
back honestly (`step_runs.executor='local'`, correct given the fallback — not the dishonesty bug,
just the underlying unreachability). After the `__init__.py` fix, the same call went through
Prefect's real flow/task engine end to end: `Flow run 'fabulous-kittiwake' - Finished in state
Completed()`, confirmed against the API as flow-run `43feb464-55fa-4783-a277-c3031caa7da8`
(COMPLETED), and `step_runs` for `surveyed_at=2026-09-28T22:01:13.737500` carries that exact id on
all four rows with `executor='prefect'`.

## Cost table, re-taken with today's real rows

`step_runs.metrics.wall_ms`, same four steps (`postgres_operations`, `db_derived`,
`postgres_nested_columns`, `postgres_column_profile`), same entity, same day — local-loop numbers
already existed from earlier today; the Prefect numbers below are new, from the runs above.

| run (`surveyed_at`) | engine | flow_run_id verified? | total wall_ms (4 steps) |
|---|---|---|---|
| 2026-09-28T21:57:47 | local (forced) | n/a | 10,977 |
| 2026-09-28T21:58:14 | local (Prefect unreachable, honest fallback — pre-`__init__.py`-fix) | n/a | 10,907 |
| 2026-09-28T15:03:15 | local | n/a | 7,767 |
| 2026-09-28T14:51:24 | local | n/a | 12,184 |
| **2026-09-28T22:01:13** | **prefect, whole-definition (post-fix)** | **yes — `43feb464…`** | **13,534** |
| 2026-09-28T21:59:58 | prefect, whole-definition (post-`__init__.py`-fix, pre-`flow_run_id`-threading fix) | flow-run confirmed via log (`sophisticated-wolf`/`93453622…`) but not yet threaded into `step_runs` at that point | 12,704 |
| 2026-09-28T15:44:57 | prefect, **per-step REST dispatch** | yes — 4 distinct ids, table above | 47,000 |

Reading this honestly, same as `step_cost_observer`'s own stated policy — reports, never corrects:

- **Whole-definition Prefect orchestration** (in-process task engine, one flow, real flow-run/task-run
  state) costs roughly **+2,600–2,900 ms over local for this four-step chain (~24–27%)** — consistent
  with `re/prefect-overhead-profile`'s separately-measured per-step overhead finding, not contradicting
  it.
- **Per-step REST dispatch** (`_any_step_needs_prerequisites` routing each step individually through
  `create_flow_run_from_deployment` + 1-second-granularity polling) costs **roughly 3–4x local** —
  expected: each step pays a full HTTP round-trip to create the flow-run plus `read_flow_run` polling
  at 1s resolution, on top of whatever a deployed worker itself takes to pick it up and run it. This is
  the SAME mechanism this section's part (1) fixed for correctness, not for speed — it was never
  claimed to be the cheap path, and today's numbers confirm it isn't one.
- Every **local** number here is honest (no fallback ambiguity possible for a step that was never
  routed to Prefect at all) and forms the true baseline the two Prefect costs above are measured
  against.
- Two OLDER `step_runs` rows exist from earlier today (`surveyed_at=2026-09-28T15:04:28` and
  `2026-09-28T03:13:57`) with `executor='prefect'` and **empty `flow_run_id`** — from before this
  fix existed. Per the "why some evidence wasn't real" section above, these are **not** trustworthy
  evidence of a real Prefect dispatch and are deliberately excluded from the cost table rather than
  averaged in — the whole point of `flow_run_id` is to make exactly this distinction checkable
  instead of assumed.

## Tests

New:
- `tests/test_no_ambient_prefect_client.py` — the (2) ratchet test, two tests (zero-tolerance scan;
  the designated helper itself never calls `get_client()`).
- `tests/test_survey_definition_executor.py::TestFreshStoredAnswerActuallyReachesPrefect` — two
  tests (positive: fresh stored answer routes to the real `re_survey_definition_flow` call, not the
  local loop; negative control: no stored answer routes to the local loop instead, via a real
  `ProjectRegistry`/`database_tables` seed, not a mock).

Changed:
- `tests/test_prefect_dispatch.py` — `_run_prefect_step_api`'s mock now returns the new
  `(result, flow_run_id)` tuple; the API-path test asserts `dispatch_info` is filled correctly.
- `tests/test_prefect_integration.py` — patches `re_prefect_client` instead of the now-removed
  `get_client` name.
- `tests/test_prefect_status_routes.py` — same rename, all six patch targets.
- `tests/test_survey_definition_executor.py::TestEngineOverride._run` — its `run_prefect_step` mock
  now fills `dispatch_info` (a genuine dispatch), since the engine label in `steps_report` now
  reflects dispatch honesty rather than being baked in before the call.
- `tests/no_silent_success_baseline.json` — one entry removed
  (`prefect_adapter.py::run_prefect_step`): its `except Exception` handler is no longer log-only
  (it now also fills `dispatch_info`), so the ratchet's own detector correctly stops flagging it —
  this is the fix working as intended, not a suppression.

## Full suite

`uv run pytest tests/ -q -rf` (clean environment, no cached CLI session):

**6686 passed, 0 failed, 103 skipped** — two more than main's baseline of 6684/6684 (`+2` net from
the two new `TestFreshStoredAnswerActuallyReachesPrefect` tests; every other file's pass count is
unchanged). A first run while a CLI session was still cached showed 2 failures in
`test_cli_workflow_commands.py::TestRunsCommands` — confirmed to be the documented cached-session
gotcha (`docs/Backlog.md`), not a regression from this branch, by reproducing both ways.

## Files touched

- `resource_explorer/__init__.py` — `PREFECT_API_URL` ambient-settings guard.
- `resource_explorer/surveyors/prefect_adapter.py` — `re_prefect_client()`,
  `_run_prefect_step_api()` returns `(result, flow_run_id)`, `run_prefect_step()`'s `dispatch_info`
  kwarg, startup-reachability functions.
- `resource_explorer/web/routes/prefect_status.py` — `get_client()` → `re_prefect_client()`, all
  three call sites.
- `resource_explorer/web/app.py` — startup check wired into `_lifespan`.
- `resource_explorer/worker.py` — startup check wired into `run_worker`.
- `resource_explorer/registry.py` — `step_runs` schema: `flow_run_id`/`dispatch_failed` columns
  (additive), `record_step_run()` signature.
- `resource_explorer/surveyors/step_cost_observer.py` — `Observation.flow_run_id`/`dispatch_failed`,
  threaded into `record()`.
- `resource_explorer/surveyors/survey_definition_executor.py` — per-step Prefect branch corrects
  the `Observation`/`steps_report` entry to the real dispatch outcome after the call.
- `resource_explorer/prefect/flows.py` — `run_planned_step_task` records its real `flow_run_id`.
- `resource_explorer/web/static/next/app.js` — `launchSurvey`'s pane line surfaces a dispatch
  fallback.
- `tests/test_no_ambient_prefect_client.py` (new), `tests/test_survey_definition_executor.py`,
  `tests/test_prefect_dispatch.py`, `tests/test_prefect_integration.py`,
  `tests/test_prefect_status_routes.py`, `tests/no_silent_success_baseline.json` — see Tests above.
