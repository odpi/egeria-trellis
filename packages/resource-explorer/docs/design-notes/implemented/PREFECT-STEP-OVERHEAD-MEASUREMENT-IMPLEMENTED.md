# Prefect per-step overhead — measured, not estimated

**Brief:** the project owner's Prefect-as-default gate (routed via the "Resource Explorer
expansion architecture" session), asking where Prefect's per-step overhead actually goes, on a
real Analysis run of `laz_local_adventureworks`, Prefect-routed vs local-routed.

**Branch:** `re/prefect-overhead-profile`, off `origin/main` (`3de3b1fa`). Built in an isolated
worktree (`~/localGit/egeria-v6/trellis-re-prefect-overhead`), not in the shared checkout — per
this repo's worktree convention. No code in `flows.py` or `survey_definition_executor.py` was
changed; this is measurement only, against data other sessions had already produced live against
the shared Postgres registry and the shared Prefect server, plus direct queries against Prefect's
own REST API (`GET/POST /api/flow_runs/filter`, `/api/task_runs/filter`, `/api/deployments/filter`,
`/api/flows/filter` at `PREFECT_API_URL`).

## Zero new runs were enqueued for this measurement

The "Resource-explorer PR/CI merge" session had already produced exactly the paired samples this
brief asked for, against current `main` (`3de3b1fa`), post `#327` (prerequisite resolution,
merge `15d01c21`, ~14:44Z) and post `#324` (column-profile savepoint fix) — both directly relevant
preconditions. Per that session's instruction, no new survey was enqueued against the shared
registry/database for this doc; all numbers below come from those three runs' `step_runs` rows and
from Prefect's own API, queried read-only.

| Run ID | `surveyed_at` | Engine | `step_runs.executor` (all 4 rows) |
|---|---|---|---|
| `c3738802-c8c7-4f46-957e-c8ce1dcbdc7a` | 2026-09-28T14:51:24.622950Z | default (no override) | `local` |
| `660e15d8-fbc3-4a62-b4b4-864573f376e9` | 2026-09-28T15:03:15.489221Z | default (no override) | `local` |
| `85d9042f-7930-4d96-9b53-744a97014423` | 2026-09-28T15:04:28.995989Z | `engine_override="prefect"` (forced) | `prefect` |

All three: `GovActionProcess::DatabaseAnalysisSurvey` on `laz_local_adventureworks`
(`postgres_column_profile` → `postgres_nested_columns` → `db_derived` → `postgres_operations`).

## New deliverable first: which path actually ran today, and is the `executor` label truthful

The peer design session asked this before the cost table, because the premise the brief was
written against (Prefect routes whole-definition runs to a process-pool worker) turned out not to
hold, and a second question rode in on top of it: does `step_runs.executor='prefect'` mean Prefect
actually ran the step?

**Finding: no. For every run made today (post `#327`), `executor` records which code branch was
*entered*, not what actually executed. All three runs above — including the one labelled
`prefect` — ran through the exact same in-process fallback mechanism, with zero footprint in
Prefect's own state.**

### The three candidate paths, and what each requires

1. **In-process flow, Prefect tracking it for real** — `SurveyDefinitionExecutor._run_via_prefect`
   calls `resource_explorer.prefect.flows.re_survey_definition_flow(...)` as a **plain Python
   function call**, inside the calling process (the RE web/CLI process) — not via
   `create_flow_run_from_deployment`. Calling an `@flow`-decorated function directly still talks to
   the Prefect API to open a real flow-run and task-runs (Prefect's engine intercepts the call), so
   this path *does* produce real `flow_runs`/`task_runs` records — it just never touches the
   `resource-explorer-pool` worker or a subprocess, because nothing dispatches it to a work pool.
   Gated on three conditions in `run()` (lines ~413–420): `_prefect_orchestration_enabled`,
   `_all_steps_prefect_runnable`, and **not** `_any_step_needs_prerequisites`.
2. **Deployment + worker, via `run_prefect_step`** — the older, per-step path
   (`prefect_adapter.py`), used inside the local loop when `_use_prefect(step)` is `True` for an
   individual step. This one genuinely dispatches over the network:
   `create_flow_run_from_deployment` against the `re-survey-step-deployment` deployment (flow "RE
   Survey Flow", work pool `resource-explorer-pool`), then polls `read_flow_run` every
   `asyncio.sleep(1.0)` until terminal. This is the only place in the codebase with a poll loop for
   Prefect completion — not `survey_definition_executor.py:778`, which is an unrelated
   `egeria-adaptive` poll for a different engine entirely (that line number, cited in the original
   brief, has shifted meaning since; grepping for `poll` in this file today finds nothing Prefect
   related — the real poll is `prefect_adapter.py:137`).
3. **Local loop, plain in-process call** — `runner(entity, self.registry, **runner_kwargs)`, no
   Prefect involvement of any kind.

### What the evidence shows

Queried `POST /api/flow_runs/filter` for all of 2026-09-28: **exactly two flow-runs exist for the
entire day**, both for flow `RE Survey Definition Flow` (id `5323dbb4-...`), both at ~03:09–03:14Z
— `colorful-galago` and `brown-beetle` — hours before `#327` merged. **Zero flow-runs exist after
`#327` landed**, for either `RE Survey Definition Flow` or `RE Survey Flow`. The `re-survey-step-
deployment` deployment is present and `READY` on `resource-explorer-pool` (confirmed via
`/api/deployments/filter`), so path 2 not running is not "deployment missing" — dispatch is
attempted and something inside `create_flow_run_from_deployment`/the client bridge fails, caught by
`run_prefect_step`'s broad `except Exception` (see its own docstring: this exact swallow-and-
fall-back shape has bitten this file before — the `UnboundLocalError` incident it already
documents), logging a warning invisible to `step_runs` and falling back to
`run_surveyor_step_task.fn(...)` — a bare function call, same as path 3.

So, for today's three runs:

| Run | Gate 3 (`_any_step_needs_prerequisites`) | Path actually taken | Real Prefect footprint |
|---|---|---|---|
| `c3738802` (default) | Not entered via path 1 — `route_local_steps=false` means default-engine `resource-explorer` steps never reach `_use_prefect`'s `True` branch either | Path 3 (local loop, plain call) | None |
| `660e15d8` (default) | Same as above | Path 3 | None |
| `85d9042f` (`engine_override="prefect"`) | Local loop ran (path 1 not taken — plan `pending_steps` was non-empty), so gate 3 must have evaluated `True` for at least one step this run; `engine_override="prefect"` then forced `_use_prefect(step)=True` per step | Attempted path 2, **silently fell back to path 3** per step (0 flow-runs created) | **None** — despite `executor='prefect'` on all 4 `step_runs` rows |

**The `executor` label is not truthful for `85d9042f`.** It is set unconditionally by which
`if use_prefect:` branch `survey_definition_executor.py` entered (line 661–665:
`step_cost_observer.observe(..., executor="prefect", source="prefect")` wraps the *call* to
`run_prefect_step`, not its outcome), and `run_prefect_step`'s fallback returns a normal
successful result with no exception — so the label is written as if real dispatch happened, even
when it silently didn't. This is a real, live gap; logged in `Backlog.md` below, not fixed here
(out of scope for a measurement-only task, and the project owner's own framing of this brief asked
for numbers first).

**Path 1 (real Prefect tracking) has not run successfully since before `#327` merged.** The only
genuine Prefect-tracked evidence available today is the pre-`#327` `brown-beetle` flow-run —
included below as a labeled reference sample for what path 1's overhead shape looks like, **not**
as a comparable number against the post-`#327` local numbers (different code, different day-part,
per the PR/CI session's explicit instruction not to treat the ~03:13Z rows as comparable to
today's post-fix ones).

## Per-step cost table

Only two paths genuinely ran today: **path 3 (local loop)**, three times, and (for
context/reference only) **path 1 (real Prefect in-process-flow tracking)**, twice, pre-`#327`.
Path 2 (deployment + worker) has not completed successfully on this checkout since the routing
regression above — there is nothing to measure on it beyond "it starts, fails inside the client
bridge, and falls back," which produces no Prefect-side timestamps at all.

### Path 3 — local loop (today, post-`#327`, all three runs)

`step_runs.metrics.wall_ms` is pure step-body time — `step_cost_observer.observe()` wraps exactly
the `runner(...)` call, nothing else. There is no Prefect API round trip, no state-transition
overhead, and no queue wait on this path by construction; the only "extra" cost is what the local
loop does per step outside the timed body (guard check, prerequisite resolver call, `_step_info`
lookup) — visible only as the gap between the sum of step `wall_ms` and the run's total wall clock
(`surveyed_at` → the run's `activity_log` completion timestamp).

| Step | `c3738802` wall_ms | `660e15d8` wall_ms | `85d9042f` wall_ms (labelled `prefect`, actually path 3) |
|---|---:|---:|---:|
| postgres_column_profile | 3479.3 | 2430.3 | 3518.7 |
| postgres_nested_columns | 3570.4 | 2969.9 | 2048.3 |
| db_derived | 3455.1 | 2848.7 | 3355.0 |
| postgres_operations | 1679.3 | 1518.1 | 1380.8 |
| **Sum of step bodies** | **12,184.1 ms** | **9,767.0 ms** | **10,302.8 ms** |
| Run total (surveyed_at → activity_log completion) | 14,577 ms | 11,369 ms | 10,915 ms |
| **Per-run non-body overhead** | **2,393 ms** (≈ 598 ms/step) | **1,602 ms** (≈ 401 ms/step) | **612 ms** (≈ 153 ms/step) |

The non-body overhead is dispatch-attempt-and-fallback cost (`85d9042f` tried real Prefect dispatch
per step, ate the failure, then ran locally) mixed with ordinary per-step bookkeeping
(`_any_step_needs_prerequisites`'s resolver calls, guard checks, `_step_info` lookups, publish and
activity-log writes) and Postgres connection/cache warmth on `laz_local_adventureworks` — three
single-trial runs on different literal data is not enough to cleanly separate those, and the
numbers move in the *opposite* direction from what a fixed per-step Prefect-attempt tax would
predict (the run that attempted-and-failed Prefect dispatch four times has the **smallest**
non-body overhead, not the largest). No reliable per-step "attempted-dispatch tax" is extractable
from n=1 samples at this noise level; more trials would be needed before drawing a number from it.

### Path 1 — real in-process flow, Prefect tracking (reference only, pre-`#327`, `brown-beetle` flow-run)

Queried `POST /api/task_runs/filter` for flow-run `35ea2439-ea47-4f5f-bafc-b75a0fa6d0e7`
(`re_survey_definition_flow`, 4 `Run Planned Step` tasks, topological order matches the flow-run's
own `plan` parameter). Matched to the same-timestamp `step_runs` rows (ids 168–171,
`surveyed_at=2026-09-28T03:13:57.661456Z`, `executor='prefect'` — these DID have a real flow-run
behind them, unlike `85d9042f`).

| Step (task order) | Prefect task `total_run_time` | `step_runs.wall_ms` (pure body) | Task overhead (bookkeeping inside the task, not body) | Gap before this task started (queue/API wait after the previous task's Completed state) |
|---|---:|---:|---:|---:|
| postgres_column_profile | 5,192 ms | 4,820.3 ms | 372 ms | — (first task; flow start → first task created/started ≈ 70 ms) |
| postgres_nested_columns | 4,638 ms | 4,263.0 ms | 375 ms | 869 ms |
| db_derived | 2,073 ms | 1,502.1 ms | 571 ms | 55 ms |
| postgres_operations | 3,262 ms | 2,751.2 ms | 511 ms | 42 ms |
| **Sum** | **15,165 ms** | **13,336.6 ms** | **1,829 ms** | **966 ms** |
| Flow-run `total_run_time` | **17,007 ms** | | | |

Flow total minus (sum of task run-times) = 1,842 ms of flow-level overhead not attributable to any
single task — consistent with the 966 ms of inter-task gaps plus ~800 ms of flow-start/finish
bookkeeping. The per-task "bookkeeping, not body" cost (372–571 ms) and the inter-task gap
(42–869 ms, once notably under a second, once notably over) are exactly what the brief's item (b)
("per-task Prefect API round trips — task-run create, Pending→Running→Completed state transitions,
log shipping") describes, on the one path where that mechanism is actually engaged. Caveat: this
is a single flow-run from before `#327`'s routing/prerequisite fix, on a different code path than
today's local numbers above, offered only to characterize path 1's overhead shape (roughly
**400–900 ms per step**, split between per-task bookkeeping and inter-task queue/API wait) — not
combined with the path-3 table into one number.

### Item (a) — flow-run process spawn: does not apply to the whole-definition flow

`re_survey_definition_flow(...)` is called as a bare Python function from
`SurveyDefinitionExecutor._run_via_prefect`, inside the same process already running the survey
(the RE web or CLI process) — never via `create_flow_run_from_deployment`. There is no `uv run` /
package-import subprocess spawn per flow, on either path 1 or path 3. The brief's framing ("each
step as a `.submit()` task on a process work pool") describes path 2 (the per-step deployment
path), not the whole-definition flow this brief's own paragraph 1 names as the thing to measure —
worth flagging back to the design session as a premise correction, separate from the routing/label
findings above.

### Item (c) — `persist_result` inheritance: confirmed, does not inherit, and is not currently a live contributor

Read both flow definitions in `resource_explorer/prefect/flows.py`:

- `re_survey_flow` (the older, single-step flow, line 154): `@flow(name="RE Survey Flow",
  persist_result=True)` — explicit. Its one task, `run_surveyor_step_task` (line 16), does not set
  its own `persist_result`, so it inherits the flow's `True`.
- `re_survey_definition_flow` (the current whole-definition flow, line 304): `@flow(name="RE Survey
  Definition Flow")` — **no `persist_result` argument at all**. Its task, `run_planned_step_task`
  (line 228), likewise sets none.

Flows do not inherit settings from each other — only a task inherits from its *own* flow. So
`re_survey_definition_flow`'s tasks do **not** inherit `True` from `re_survey_flow`; they get
Prefect's own default (`persist_result=None`, which resolves to `False` in a standard OSS
deployment with no explicit `PREFECT_RESULTS_PERSIST_BY_DEFAULT` override — not set anywhere in
this repo's `.env`/`config.py`). **`persist_result` is already effectively off for the flow that
matters for whole-definition Analysis runs.** It is only `True` on `re_survey_flow`, which backs
path 2 (`run_prefect_step`'s deployment) — the path that, per the finding above, has not completed
a real dispatch since `#327` landed, so its `persist_result=True` cost is not currently being paid
by anything either.

## Recommendation

**The project owner's hypothesis (persist_result, log shipping/batching, a warm worker, a shorter
poll interval) targets path 2 — the per-step deployment path — and none of that path's overhead is
actually being paid today, because path 2 is not completing a real dispatch at all; every run
falls back to a bare in-process call before any of those four costs are incurred.** The measured
numbers say the ordering should be:

1. **Fix the routing/label gap first — before optimizing anything.** Right now the codebase cannot
   tell you which of three paths a given run took: `executor='prefect'` in `step_runs` is written
   whenever `_use_prefect()` returns `True`, not when Prefect actually ran the step, and path 1
   (the flow this brief was written to measure) has not been reachable since `#327`'s prerequisite
   gate started returning `True` for this definition. Chasing "≤0.5s overhead per step" against
   numbers that don't reflect what ran is not measuring the thing the project owner's
   Prefect-as-default decision depends on. This is the highest-leverage single fix, because it is
   what makes every other number in this doc (and every future one) trustworthy.
2. **Once path 1 is reachable again, its own overhead (400–900 ms/step, from the one real sample
   available) is already close to the ≤0.5s/step target** — well below the 8–10s/step overhead the
   September 18 `repo_arch_coupling` measurement found on the OLD per-step deployment path
   (`8ba66c71`, referenced in `Backlog.md`). The mechanism that's actually cheap (in-process flow,
   real tracking) is the one currently broken; the mechanism the original hypothesis assumed is
   expensive (deployment + worker + persist_result) is the one that's currently a no-op. **Widening
   Prefect adoption should target path 1's overhead profile, not path 2's**, since path 2's
   per-task network round trip (`create_flow_run_from_deployment` + 1 Hz poll) is structurally
   the more expensive of the two regardless of `persist_result`.
3. Of the four candidate fixes named in the brief, only two are still live once path 1 is the
   target: (i) `persist_result=False` is **already the effective state** for path 1's task
   (nothing to change); (ii) a warm worker matters for path 2 specifically, not path 1 (path 1 runs
   in-process and touches no work-pool worker at all) — so bringing the `resource-explorer-pool`
   worker up (the prerequisite this task started from) helps path 2 once its routing bug is fixed,
   but does nothing for path 1 today. (iii) Log shipping/batching and (iv) the poll interval are
   both path-2-specific (`asyncio.sleep(1.0)` in `prefect_adapter.py:137`); worth doing once path 2
   is confirmed to be dispatching for real, not before.
4. **Concrete target:** path 1's ~400–900 ms/step overhead is close enough to ≤0.5s/step that the
   fix effort belongs on "make path 1 reachable again" (the `_any_step_needs_prerequisites` /
   routing gap) rather than on trimming an already-small per-task cost further.

## Backlog entries to log (not fixed here — measurement task)

- `step_runs.executor='prefect'` is written on dispatch *attempt*, not dispatch *success* —
  `run_prefect_step`'s silent fallback (already documented in its own file for a different bug,
  the `UnboundLocalError` incident) makes this label untrustworthy for any run after `#327`. Needs
  either propagating the fallback outcome into the recorded `executor`, or raising instead of
  falling back when the *whole-definition* prerequisite gate forced the per-step path (as opposed
  to per-step `route_local_steps`, an intentional graceful-degradation case).
- `_any_step_needs_prerequisites` now returns `True` for `DatabaseAnalysisSurvey` on
  `laz_local_adventureworks` at least some of the time even when
  `PREFECT-PREREQUISITE-RESOLUTION-IMPLEMENTED.md`'s own fix describes the stored-data case as
  correctly resolving to `SATISFIED` — not root-caused here (out of scope), but real: it is why
  `_run_via_prefect` was never entered on any of today's three runs.

## What was verified

- Read `resource_explorer/prefect/flows.py`, `resource_explorer/surveyors/survey_definition_
  executor.py`, `resource_explorer/surveyors/prefect_adapter.py`, and `resource_explorer/config.py`
  in full for the routing/gating/`persist_result` claims above.
- Queried the shared Prefect server directly (`POST /api/flow_runs/filter`,
  `/api/task_runs/filter`, `/api/flows/filter`, `/api/deployments/filter` — same pattern
  `web/routes/prefect_status.py` uses for `/api/prefect/status`) for all of 2026-09-28.
- Queried the shared registry (`resource_explorer.step_runs`, `resource_explorer.activity_log`)
  directly via `ProjectRegistry()`'s own engine for the three runs' step-level and run-level
  timestamps.
- No new survey was enqueued; no code in the hot path was changed; no instrumentation was added
  (existing `step_runs.metrics.wall_ms` plus Prefect's own REST timestamps covered everything
  needed). Per the coordination brief, since no instrumentation was added, the full test suite was
  not re-run for this change — only this doc and (if pushed alongside it) the prerequisite worktree
  fix are new.

## Status

Doc complete. Pushing `re/prefect-overhead-profile` to `origin` (no PR opened — the
"Resource-explorer PR/CI merge" session batches these). Tip reported to that session and to the
"Resource Explorer expansion architecture" design session.
