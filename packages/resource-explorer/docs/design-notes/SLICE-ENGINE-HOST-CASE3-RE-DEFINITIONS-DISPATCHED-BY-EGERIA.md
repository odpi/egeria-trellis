# SLICE — Engine host case 3: Egeria runs RE's own survey definitions (2026-10-15)

From the design session for the PR/CI session's builder, on the owner's
word of 2026-10-15. Case 3 of `DESIGN-RE-ENGINE-HOST-BY-EQUIVALENCE.md`;
in the style of its §5. Builds on step 4 (`re/engine-host-step4`): the
process-instance and ControlFlow reading, the `surveyReport` target
carry, and the Understanding grouping reader are reused, so this slice
starts after step 4 merges.

## Goal

An RE survey definition that already exists in Egeria as a process
(`GovActionProcess::<group>` with its `GovActionProcessStep::<group>::<step>`
elements and authored `NextGovernanceActionProcessStep` guards) becomes
dispatchable by Egeria: a person or a process starts it in Egeria, Egeria
creates one engine action per step as the guards fire, RE's engine host
claims and runs each, and Understanding reads the run with the same words
as an RE-coordinated run. Nothing about how RE runs its own surveys from
its own Run button changes.

## What exists (read before building)

- The definitions are published by `dr_egeria_survey_publisher.py`
  (qualified names at `:61` and `:65`), read back by
  `survey_definition_reader.py` (guards "read, not assumed",
  `survey_definition_docs.py:19`), and checked by
  `survey_definition_reconciler.py`. 127 step elements exist, none with
  a `GovernanceActionExecutor`.
- `step_outcome.py` maps a step's outcomes to the guard values the
  authored links carry.
- Steps are shared across definitions (the same `repo_git_statistics`
  appears in several processes), and every step key is in one registry
  (`DATABASE_STEP_REGISTRY`, the repository registry) with
  `executes_at`, `fetch_cost`, `compute_cost`, `requires_capability` and
  `produces`.
- The engine host (steps 3 and 4) dispatches on request type through
  the library-step host and runs as `Daemon(ENGINE_HOST,
  requested_by=requesterUserId)`.

## Decisions in this slice

1. **One SurveyActionService per step key, not per definition.** The
   request type is `re-<step_key>` (for example `re-repo_git_statistics`,
   `re-postgres_schema_and_stats`). A step key shared by five definitions
   is one service with one request type; the definition is the process,
   the step is the service. Request types therefore come straight from
   the registries, and the generator fails its test if a registry key has
   no service or a service has no registry key.
2. **The executor is part of the published definition, never a manual
   link.** `dr_egeria_survey_publisher` emits, for every
   `GovActionProcessStep`, its `GovernanceActionExecutor` to RE's engine
   with `requestType = re-<step_key>` and the step's request parameters
   (the authored step options). Declarations are idempotent by qualified
   name through Dr.Egeria; the reconciler gains one check per step,
   "exactly one executor, to RE's engine, with the registry's request
   type", reported as drift and never repaired silently. `executes_at:
   resource-explorer` and "executor is RE's engine" must agree; a
   mismatch is a drift finding (design §4.1).
3. **Guards stay authored.** Each step's `producedGuards` on its
   `GovernanceActionType` are generated from `step_outcome.py`'s mapping
   for that step; the next-step links are unchanged. The engine host
   completes with the guard the step's outcome maps to; a guard that no
   authored link consumes ends the process, and RE says so (negative 4).
4. **Credentialed steps run under RE's own resource credentials**, as
   RE's queue does today (the database registration's credential key),
   under the service account for Egeria calls. `requires_capability` is
   honoured exactly as the launcher gate honours it: a step whose
   capability the stored credential lacks completes `survey-invalid` with
   the launcher's own sentence, never with a guess.
5. **No double running.** An RE-started run (the Run button) goes through
   RE's executor as today and creates no engine actions. An
   Egeria-started run creates engine actions RE claims. The `runs` row
   records which (`started_by: re | egeria`), and Understanding labels
   it. A definition is never run both ways for one press.

## 5.1 Pieces

| # | Piece | Where | Write? |
|---|---|---|---|
| C1 | **Service and type generation from the registries.** Extend `generate_engine_host_declaration.py`: one `SurveyActionService`, one `SupportedGovernanceService{requestType: re-<step_key>}` and one `GovernanceActionType ResourceExplorerSurvey::re-<step_key>` with `producedGuards` from `step_outcome.py`, per registry key. Test: registry keys and generated request types are equal sets; guards equal the mapping | RE `scripts/`, `docs/dr-egeria/engine-host/steps/` | No; the owner runs the document |
| C2 | **Executor in the published definition.** `dr_egeria_survey_publisher` emits the executor per step; the reconciler checks it (decision 2). Test: a published definition's document contains one executor per step; the reconciler reports a missing or wrong executor as drift, with a fixture from a live read of one step element | publisher, reconciler | No; republishing is the owner's Dr.Egeria run |
| C3 | **Request type to step dispatch.** The engine host maps `re-<step_key>` to the registry entry and runs the step through the same path the queue uses (`run_synthetic_step` or the step's adapter), with the resource resolved from the action targets as in step 4, and `started_by: egeria` on the `runs` row | `engine_host.py`, `run_queue.py` | Egeria: claim, status, completion, the report |
| C4 | **Understanding.** The step-4 grouping reader shows an N-step run with the definition's name, each step's state and report, and "started in Egeria by <requester>" or "started in RE by <person>"; `derive_native_state` unchanged | reader | Read-only |
| C5 | **Trigger.** The owner starts one definition from Dr.Egeria (`initiate_gov_action_process("GovActionProcess::DatabaseAnalysisSurvey", action_targets=[coco_pharma])`) | Dr.Egeria | Egeria write (the owner's) |

## 5.2 Tests that fail first

1. **Request types equal registry keys**, both registries; guards equal
   `step_outcome.py`'s mapping per step.
2. **Executor per step in the document**; the reconciler flags a step
   with none, with two, or with the wrong request type.
3. **Dispatch by request type** runs the registry step with the resolved
   resource; an unknown request type is not claimed and recorded once.
4. **Guard with no consumer.** A step completes with a guard no authored
   link consumes: RE records "process ended after <step>: guard <g> has no
   next step" on the run, Egeria is told the guard, nothing else runs.
5. **Credential capability.** A step whose `requires_capability` the
   stored credential lacks completes `survey-invalid` with the launcher's
   sentence and publishes no report.
6. **No double running.** A definition started in RE creates no engine
   action; one started in Egeria creates no RE-coordinated run; the
   `runs` row carries `started_by`.
7. **Shared step, two definitions.** The same step key dispatched from
   two processes runs twice, once per process instance, with each report
   linked to its own action.
8. **Mid-run restart** (as step 3's test 7) on a three-step instance:
   the completed steps are not re-run, the running one is recovered, the
   rest wait for Egeria.
9. **Private resource not claimed; off means off**, as before.

## 5.3 Gate (live, the owner's write round)

The owner runs C1's document and republishes one definition with C2
(`DatabaseAnalysisSurvey`), then C5 on coco_pharma. Then:

- Egeria shows one process instance with one engine action per step in
  the authored order, each claimed by RE's engine user and `COMPLETED`
  with the guard the authored link expected.
- Each step's report is linked by `ReportOriginator`; Understanding
  shows the run as one line with every step, "started in Egeria by
  <owner>", and the same per-step words an RE-started run shows.
- **Negative, live:** the same definition with the stored credential
  reduced below `postgres_operations`' capability: that step
  `survey-invalid` with the launcher's sentence, the process ends there,
  Understanding says which step and why.

## 5.4 Not in the slice

- Repository definitions whose steps fetch sources or call models
  (`fetch_cost` api_heavy or compute_cost high): the first live run is a
  database definition; repository definitions follow once the engine
  host's worker has the fetch budget the queue applies today.
- Prefect-routed steps.
- Steps that need a signed-in person (private work).
- Changing any authored guard.

## Owner's answers (2026-10-15)

- [x] First live gate: `DatabaseAnalysisSurvey`. The owner allows a
      smaller definition if it makes the gate clearer; the design keeps
      `DatabaseAnalysisSurvey`, because it is the one whose credentialed
      step proves the capability gate (negative, live) and whose steps
      are already proven individually.
- [x] The Run button gains a second control now, labelled **"coordinated
      by Egeria"** (never "run in Egeria"), beside the RE run; the owner
      expects it to be temporary. It calls `initiate_gov_action_process`
      through `EgeriaSurveyPort`, records `started_by: egeria` on the run,
      and is hidden when the engine host is off (the sentence says why).

## Built (engine host case 3, 2026-10-15)

**Found while building, not in the slice.**

- **Egeria matches a link's guard exactly.** `EngineActionHandler.initiateNextEngineActions` (egeria `af400039c2`)
  starts the next step only when an output guard EQUALS the link's guard (or the link has none). RE's generated links
  carry `Any`, which RE's own executor reads as "always"; Egeria does not. So a definition step completes with its
  survey guard AND `Any` (`step_outcome.guard_mapping`), whatever its outcome (review M1): an Egeria-coordinated run
  continues after an invalid or failed step exactly as RE's executor does, so both read the same. Only an authored
  link with another guard can go untaken; then the run says the process ended there (negative 4). For the gate's
  credential negative this means `postgres_operations` is `survey-invalid` and is the last step anyway.
- **Duplicate next-step links start a step twice.** `NextGovernanceActionProcessStep` is multi-link and Dr.Egeria's
  Link Next only adds, so a republish leaves duplicates, and with every step emitting `Any` each duplicate starts the
  next step again (2, 4, 8 actions down the chain). Every generated step now declares `Ignore Multiple Triggers`
  true, and the engine host completes a second action for the same process instance and step `survey-invalid`
  "triggered twice in this run; the first ran", without running it and with no `Any`, so it starts nothing; the run
  line skips such an action.
  - *Consequence:* `Ignore Multiple Triggers` on every step would break a future JOIN step (one with two
    predecessors that must each start it): Egeria would start it once, from whichever predecessor finished first. No
    definition has one today (all 13 documents are linear chains, checked 2026-10-15). A definition that adds a join
    must stop the generator writing the flag on that step, and the engine host's twice-refusal must then key on the
    predecessor as well.
- A step after an INVALID or FAILED one is claimed: the previous-step check accepts any ENDED status (COMPLETED,
  INVALID, FAILED) whose completion guards include the link's guard, and refuses only a step that has not ended.
- The once-only run-line insert takes `pg_advisory_xact_lock(hashtext(<instance GUID>))` first on Postgres (the
  engine-action index is not unique, and a unique one would be DDL); SQLite serialises writers itself.
- **Report targets accumulate.** Egeria hands a later step the previous action's targets plus the completion's new
  ones (`prepareEngineActionFromProcessStep`), so step 3 arrives with two `surveyReport` targets. Only a step that
  derives from a report (a library step with `source_report`) is checked against them; a definition step ignores
  them, and the asset Egeria lists twice is one target.
- A built-in database step's results become annotations through the code RE's own database publish runs
  (`egeria_database_surveyor.build_step_annotations`); outputs that publish has no annotation form for (the column
  profile's values) are not on the engine host's report either.

**Where it lives.**

- C1: `engine_host.registry_steps` (built-in keys of both registries; attached library steps excluded) and
  `generate_engine_host_declaration.render_registry_steps` produce `docs/dr-egeria/engine-host/steps/database-steps.md`
  and `repo-steps.md` (each with its pyegeria steps file). `db_derived` stays in the engine's own document, so no step
  is declared twice.
- C2: `dr_egeria_survey_publisher` writes `### Produced Guards` and one `Link Action to Action Executor` per step RE
  runs; the 13 definition documents are regenerated. `survey_definition_reconciler.diff_executors` (pure) and
  `SurveyDefinitionReader.executor_drift` (live, report-only) check one executor per step; the database links script
  prints the drift and never writes it.
- C3: `engine_host._screen` / `_decide_one` / `_route`; the capability sentence is
  `prerequisite_resolver.capability_reason_detail` (the reason only). Repository steps: `out_of_scope_reason`.
- C4: `mixed_process.definition_process` reads a definition as a declared process; `run_line_words` says "started in
  Egeria by <requester>" or "started in RE by <person>". The control is `native_survey_run.coordinate_control` (on
  each candidate row) and the native-surveys Run route with the definition's qualifiedName.
- `started_by`: no DDL. `runs.target.started_by` ("egeria" on engine-action rows, "re" on RE's own definition runs)
  and `step_runs.declared.started_by` / `started_in` on the process run's native-survey row.

Tests: `tests/test_engine_host_case3.py`, harness `coordinated-by-egeria.test.mjs`.
