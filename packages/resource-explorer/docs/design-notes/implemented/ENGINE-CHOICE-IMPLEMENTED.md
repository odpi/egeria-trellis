# ENGINE-CHOICE-IMPLEMENTED — per-run engine override for `resource-explorer`-tagged steps

**Status:** built, 2026-09-19. Branch `re/engine-choice-ui`, worktree `wt-engine-choice`,
branched from `main`. This is "Part A" of a two-part scope: letting a caller choose, per
invocation, whether a Survey Definition's `executes_at: resource-explorer` steps run in-process
or via Prefect — the pair that is genuinely interchangeable (same Python function, same result,
different runner). "Part B" — `executes_at: egeria`, a different and not-yet-fully-wired
execution path — is explicitly out of scope here and untouched by this change.

Background reading: `PLAN-PREFECT-OR-ALTERNATIVE.md`, `PLAN-EXECUTION-MODES-VERIFICATION.md`.

## What changed

1. **`SurveyDefinitionExecutor.run(..., engine_override: str | None = None)`**
   (`resource_explorer/surveyors/survey_definition_executor.py`) — a purely local parameter,
   validated up front (`ValueError` for anything other than `None` / `"resource-explorer"` /
   `"prefect"`) and threaded through to two places for the duration of that one call:
   - `_use_prefect(step)`, the per-step predicate the local dispatch loop already used to decide
     which engine runs a `resource-explorer`-tagged step (config-driven:
     `prefect.enabled and prefect.route_local_steps`). `engine_override` now short-circuits that
     check when set, in either direction.
   - `_prefect_orchestration_enabled(engine_override)`, the gate for handing the WHOLE definition
     to Prefect in one flow (`_run_via_prefect`) rather than sequencing it in the local loop. This
     needed the same override — otherwise `engine_override="resource-explorer"` could still be
     defeated by `config.prefect.enabled=True` sweeping the whole definition into Prefect before
     the per-step predicate ever ran.

   Nothing here mutates `get_config()`'s return value or any module/global state — every read of
   the override is a plain function argument or closure capture scoped to this one call, so two
   concurrent requests with different `engine_override` values cannot interfere with each other.

2. **`run_survey_definition(..., engine_override: str | None = None)`** (same file) — spelled out
   explicitly in the signature (rather than left implicit in `**kwargs`) purely so it's
   discoverable from the CLI/API call sites; behaviourally it was already going to reach
   `executor.run` correctly via `**kwargs` either way.

3. **CLI** (`resource_explorer/cli/main.py`) — added `--engine {resource-explorer,prefect}` to
   all three `survey-definition` subcommands (`resource-explorer survey-definition`,
   `resource-explorer database survey-definition`, `resource-explorer filesystem
   survey-definition`), not only the top-level one the task named — the other two call the exact
   same `run_survey_definition`, and leaving them without the option while the top-level command
   had it would have been an arbitrary, confusing asymmetry. Each validates the value itself
   (a friendly `console.print` + `typer.Exit(1)`, matching this file's existing error-handling
   style) before ever calling into the executor, so a typo reads as a clean CLI error rather than
   an uncaught `ValueError` traceback — the executor's own `ValueError` is still there as the
   defense-in-depth backstop for any other caller.

4. **API** (`resource_explorer/web/routes/survey_definitions.py`,
   `resource_explorer/workflows/survey_definition.py`) — added an optional `engine` field to
   `SurveyDefinitionRunRequest` (the POST `/api/survey-definitions/{entity_type}/{slug}/run` body)
   and an `engine_override` field to `SurveyDefinitionRunParams` (the plain dataclass that
   round-trips through the run queue's JSON `target` column — see design decision below). The
   route validates `body.engine` before enqueueing and raises `HTTPException(400)` on an
   unrecognized value, for the same "fail loudly, not silently" reason as the CLI.

   **This single route already covers repo, database, and filesystem** — `run_survey_definition_route`
   is generic over `entity_type` and is the only HTTP entry point found that calls
   `run_survey_definition` (via `workflows/survey_definition.py`'s `run_definition`/
   `execute_and_record_definition`). There is no separate per-resource-type route to duplicate this
   into.

5. **Guardrail** — deliberately did NOT add a new reachability check. `GET /api/prefect/status`
   (`resource_explorer/web/routes/prefect_status.py`) already exists, already answers exactly
   "is Prefect enabled and reachable right now", and already backs the Admin "⚡ Prefect" panel.
   The UI (below) calls this existing endpoint to decide whether to show the engine selector at
   all. The API route itself does **not** gate on reachability — it accepts `engine="prefect"`
   unconditionally and lets it degrade the way `run_prefect_step` already does when no server is
   reachable (falls back to running the step locally in-process; see `prefect_adapter.py`). This
   was a deliberate choice (see below) rather than an oversight.

6. **UI** (`resource_explorer/web/static/index.html`) — the Run Survey Definition modal
   (`#survey-def-run-modal`, driven by `showSurveyDefinitionRunModal`/`submitSurveyDefinitionRun`)
   is the one clear "run this Survey Definition" entry point found in the frontend (confirmed via
   grep for `run_survey_definition`/`/run` call sites — the other one,
   `runSurveyNow()` at line ~7901, is a single-click "run now" quick action on the Automate
   surveys table with no modal at all; adding a dropdown there would mean a new UI surface for a
   button whose whole point is "no dialog", so it was left alone). Added a `<select>` (`Use
   configured default` / `Local (resource-explorer)` / `Prefect (retries, logs, cancellation)`),
   hidden by default and shown only when a fetch of `/api/prefect/status` (done when the modal
   opens) reports `enabled && reachable`. The selected value (or `null` if left on "Use configured
   default") is sent as `engine` in the POST body.

## Design decisions not fully specified above

- **`engine_override` only affects steps tagged `executes_at="resource-explorer"`.** A step
  already forced to Prefect by its own declaration (`executes_at="prefect"`, or one of the
  `PREFECT_ONLY_STEPS` — `soda_data_quality`, `great_expectations_validation` — which have no
  local implementation at all) is left alone by `engine_override="resource-explorer"`: there is
  nothing local to force it onto, and silently no-oping or erroring on that combination seemed
  worse than simply defining the override's scope to match what is genuinely interchangeable —
  which is exactly the `resource-explorer`/`route_local_steps` pair the task described as "the
  same Python function, same result, different runner." `executes_at="egeria"` steps never enter
  either of `_use_prefect`'s `True` branches at all, in either override direction — this is the
  hard boundary with Part B and is covered explicitly by
  `test_egeria_step_is_never_forced_through_either_override_value`.

- **`engine_override="prefect"` also forces whole-definition Prefect orchestration on, bypassing
  `config.prefect.enabled`.** The alternative — only overriding the per-step predicate, leaving
  `_prefect_orchestration_enabled` reading the global config — would have made `engine_override`
  ineffective on any definition that reaches `_run_via_prefect` today only when
  `config.prefect.enabled` happens to already be true, and would have left the two mechanisms able
  to disagree about the same run. Symmetrically, `engine_override="resource-explorer"` forces
  whole-definition orchestration OFF regardless of config, so an override request for "just run
  this locally" can't be defeated by the global flag sweeping the whole definition into a Prefect
  flow before the per-step predicate ever runs. If the resulting Prefect dispatch fails (no
  server, wrong version, etc.), the existing fallback paths — `_run_via_prefect`'s own
  try/except-then-`None`, and `run_prefect_step`'s local fallback per step — already handle that
  gracefully; `engine_override="prefect"` does not need its own new failure handling.

- **The API accepts `engine="prefect"` even when Prefect is disabled/unreachable, rather than
  rejecting it server-side.** Two options were available: reject with a 4xx if
  `!config.prefect.enabled`, or accept and let it degrade. Chose the latter because (a) the UI
  already hides the control unless `/api/prefect/status` says `enabled && reachable`, so a normal
  user never sees this path; (b) the CLI and a direct API caller are still free to ask for
  Prefect explicitly (e.g. to test a specific worker) and get the same graceful "ran locally
  anyway" behaviour `route_local_steps` already relies on elsewhere, rather than a hard error for
  a state that isn't actually broken; (c) it keeps one reachability check
  (`GET /api/prefect/status`) as the source of truth for "should this be exposed", rather than
  duplicating that logic into a second, possibly-diverging gate inside the run route.

- **`--engine` was added to all three CLI `survey-definition` subcommands**, not only the
  top-level `repo` one the task named explicitly — see point 3 above.

- **The run-queue round-trip.** `SurveyDefinitionRunParams` is a plain dataclass (not the route's
  pydantic model) precisely so it can serialize through the `runs.target` JSON column and come
  back out unchanged for a queued run (`run_queue.py`'s `_handle_survey_definition_run`). The new
  field is named `engine_override` there (matching the executor's own parameter name) rather than
  `engine` (the HTTP request body's shorter name) — the route's `_params()` function is the one
  translation point between the two names, so a background-queued run gets exactly the same
  behaviour as a synchronous one.

## What this does NOT cover (Part B, and other explicitly out-of-scope items)

- **`executes_at="egeria"` steps** — coordinated by Egeria's own engine host, a fundamentally
  different (and, per `PLAN-EXECUTION-MODES-VERIFICATION.md`, still async/not-fully-wired)
  execution path. `engine_override` never touches them, in either direction — this is guarded by
  a dedicated test (`test_egeria_step_is_never_forced_through_either_override_value`) and is the
  hard line between this task and Part B.
- **No new reachability/health-check logic.** Reused the existing `GET /api/prefect/status`
  rather than inventing a second one, per the task's own guidance to investigate existing patterns
  first.
- **The `runSurveyNow()` "run now" quick action** in the Automate surveys table has no engine
  selector — it is a single-click action with no modal, and adding one would be a bigger UI change
  than "add the one control" calls for. It still accepts the config-driven default behaviour
  unchanged (no `engine` sent means `engine_override=None` end to end).
- **No change to `PREFECT_ROUTE_LOCAL_STEPS`'s own default**, or to any other global Prefect
  config — this feature is additive and does not change what happens when nobody asks for an
  override.

## Testing

`tests/test_survey_definition_executor.py`:

- `TestEngineOverride` — the per-step predicate, exercised through the real `SurveyDefinitionExecutor.run()`
  (not a mirrored copy) with `_prefect_orchestration_enabled` forced off to isolate the local loop:
  - `test_prefect_override_forces_a_resource_explorer_step_to_prefect_regardless_of_config` (a)
  - `test_resource_explorer_override_forces_a_step_local_regardless_of_config` (b)
  - `test_none_preserves_existing_config_driven_behavior_prefect_side` /
    `..._local_side` (c) — the regression guard
  - `test_invalid_engine_override_raises_value_error` (d)
  - `test_egeria_step_is_never_forced_through_either_override_value` (e)
- `TestEngineOverrideAndWholeDefinitionOrchestration` — `_prefect_orchestration_enabled` itself,
  confirming the override reaches the whole-definition gate in both directions, independent of
  `config.prefect.enabled`.

Ran `tests/test_prefect_dispatch.py` first (unmodified) to pin the exact pre-existing behaviour of
`_use_prefect` before adding anything, per the task's instruction — all pre-existing tests there
still pass unchanged, confirming `engine_override=None`'s behaviour is bit-for-bit what it was
before this feature.

Full suite: `uv run pytest tests/ -q` — **5088 passed, 103 skipped, 0 failed** after this change.

One collateral fix along the way: two pre-existing tests
(`tests/test_execution_modes_path_a_end_to_end.py`,
`tests/test_prefect_orchestration_respects_engine_routing.py` — landed by a concurrent session the
same day, for the mixed-engine whole-definition bug described in `_all_steps_prefect_runnable`'s
own docstring) monkeypatched `_prefect_orchestration_enabled` with a zero-argument lambda. Adding
the `engine_override` parameter to that function's call site broke both
(`TypeError: <lambda>() takes 0 positional arguments but 1 was given`) — fixed by giving each lambda
an `engine_override=None` parameter it ignores, which is all either test needs (neither is testing
this feature). Confirmed both pass again, and confirmed all 4 initially-failing tests were exactly
these two files' four tests — nothing else in the suite was affected.
