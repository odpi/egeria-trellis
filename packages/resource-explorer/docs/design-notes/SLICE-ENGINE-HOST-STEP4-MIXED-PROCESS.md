# SLICE — Engine host step 4: one mixed Egeria process, a native survey step chained to an RE step (2026-10-15)

From the design session for the PR/CI session's builder, on the owner's
word of 2026-10-15 ("start extensibility step 4"). Case 2 of
`DESIGN-RE-ENGINE-HOST-BY-EQUIVALENCE.md` (§4.1 "mixed processes", §5.4
"not in the slice"), in the style of its §5. Step 3 is proven: RE's
`re-database-staleness` on coco_pharma completed `survey-completed`,
report f21475b2, 266 annotations in Egeria matching RE's.

## Goal

The smallest proof that Egeria can run a process whose first step is a
native Java survey and whose second step is RE's, with Egeria evaluating
the guard between them and carrying the action targets, and RE reading
the two-step run back with no new read path for the native half.

**Not** the alternative of making RE's 127 existing
`GovActionProcessStep::*` dispatchable by Egeria. That would attach an
executor to every step of every survey definition and change who runs
RE's own surveys; it is case 3, a later slice. One new process touches
nothing that runs today.

## The process

`GovActionProcess::ResourceExplorer::PostgresSurveyThenDerived`, two steps:

| Step | Governance action type | Runs on | Produced guards used |
|---|---|---|---|
| 1 | the content pack's `PostgreSQLSurvey` type (the one RE's Run route already initiates through `EgeriaSurveyPort`) | Egeria's survey engine, Java | `survey-completed`, `survey-failed`, `survey-invalid` |
| 2 | `ResourceExplorerSurvey::re-database-staleness` (the step-3 type, already declared and proven) | RE's engine host | the same three |

Linked by one `NextGovernanceActionProcessStep` from step 1 to step 2
with `guard = survey-completed`, `mandatoryGuard = true`. No link for the
other guards: on `survey-failed` or `survey-invalid` the process ends
after step 1, which is Egeria's behaviour and the first negative case.

Why `re-database-staleness` and not `re-db-derived`: it is the step that
has run as an engine action live, and staleness is the natural second
step after a fresh native survey (it reads what the survey just
measured). `re-db-derived` follows in step 5 with the same wiring.

Type: the process is a plain `GovernanceActionProcess` for this slice.
ISSUE-150 records that Dr.Egeria's Create Governance Action Process
cannot create the `SurveyingActionProcess` subtype yet; the subtype is
one line to change when it can, and nothing in the slice depends on it.

## How targets and guards carry

What Egeria does (to be confirmed live by E2 below, read-only on an
existing native process run): the process's action targets are copied
to the first step's engine action; a survey action service records its
report as a new action target named `surveyReport` on completion; and
`NextGovernanceActionProcessStep` creates the next engine action with
the previous action's action targets, original and new, plus the
original request parameters, when the completion guard matches.

So step 2's engine action arrives with at least two action targets:

- the database asset the process was started on (the name the content
  pack uses for the survey's target, read in E2; the design assumes the
  survey's own target name, not a new one);
- `surveyReport`, the step-1 report's GUID, added by the native service.

RE's engine host reads both. The asset GUID maps to the entity slug as
step 3 does; the report GUID is passed to the step as `source_report`.
`re-database-staleness` reads that report's annotations back through the
existing `egeria_survey_reader.annotations_from_report` before deriving,
so the derivation is over the measurement that just happened and not
over an older stored inventory; the step records which report it read
in its own report's additionalProperties (`derived_from_report`).

If `surveyReport` is absent (the process was started at step 2, or the
native service did not add it), the step runs standalone, from the stored
snapshots as in step 3, and completes `survey-completed`. The writes
measure is omitted and its absence is recorded as "writes since statistics
reset: not measured · no survey report in this run". (Ruling 2026-10-15,
replacing "survey-invalid, nothing to derive from".) With one snapshot the
staleness rows say "not measured · one snapshot only" and the run still
completes: an absence honestly recorded is not a wrong input.
`survey-invalid` is only for a `surveyReport` that is present and wrong,
and its message names which: the wrong subject, not the previous step's
report, or more than one `surveyReport` target.

## 5.1 Pieces

| # | Piece | Where | Write? |
|---|---|---|---|
| M1 | **Process declaration generator.** Extends `scripts/generate_engine_host_declaration.py` with `--mixed-process`: emits a Dr.Egeria document creating the process, its two steps (step 1 referencing the content pack's `PostgreSQLSurvey` type by qualified name, step 2 referencing `ResourceExplorerSurvey::re-database-staleness`), and the one next-step link with its guard. Under `docs/dr-egeria/engine-host/processes/postgres-survey-then-derived.md`. Test: the document's step types, guard and order equal a registry entry `MIXED_PROCESSES["postgres_survey_then_derived"]`. If Dr.Egeria's Link Action to Action Executor or next-step command is unverified (ISSUE-150), E2 verifies them live before M1 is trusted | RE `scripts/`, docs | No; the owner runs the document |
| M2 | **Read-only probe** of one existing native `PostgreSQLSurvey` engine action and, if any exists, one process instance: the action-target names and whether `surveyReport` is added on completion; what a next engine action carries. Saved as fixtures. This settles the target name the design assumes | gate venv | Read-only |
| M3 | **Target reading in the handler.** `engine_action` handler reads all action targets, maps the asset target to the slug and passes `source_report` when a `surveyReport` target is present; the step host passes it to the step; `re-database-staleness` accepts it and records `derived_from_report`. Absent target → standalone `survey-completed` with writes "not measured" (ruling 2026-10-15); a wrong target → `survey-invalid` naming the mismatch | `run_queue.py`, `survey_step_host.py`, the library step | Egeria: claim, status, completion, the report, as step 3 |
| M4 | **Process grouping in Understanding.** A reader that groups engine actions by the process instance they belong to (the engine action's anchor or `GovernanceActionProcessInstance` relationship, confirmed in M2) and shows one run line: "Postgres survey then derived · step 1 PostgreSQLSurvey ✓ report a1b2 · step 2 re-database-staleness ✓ report c3d4 · started by <requester> 10-15". Each step's report opens as today. `derive_native_state` is unchanged for each action | `native_survey_run.py` reader, Understanding's survey history | Read-only |
| M5 | **Trigger.** The owner runs `initiate_gov_action_process("GovActionProcess::ResourceExplorer::PostgresSurveyThenDerived", action_targets=[coco_pharma database asset])` from Dr.Egeria, or RE's Run route gains the process as a choice beside `PostgreSQLSurvey` through `EgeriaSurveyPort` (same call shape) | Dr.Egeria or RE | Egeria write (the owner's) |

## 5.2 Tests that fail first

1. **Generated process equals registry.** Step types, order, guard and
   `mandatoryGuard` match `MIXED_PROCESSES[...]`; changing the guard fails.
2. **Targets carry.** Fake engine action for step 2 with two targets
   (asset, `surveyReport`): the handler maps the slug, passes
   `source_report`, the step reads the report through the existing
   reader (fake binds its real signature), and the step's report carries
   `derived_from_report` equal to the input GUID.
3. **Wrong report target.** A `surveyReport` of another subject, not the
   previous step's report, or two `surveyReport` targets: the step completes
   `survey-invalid`, the message naming which; no report is published.
   **Missing report target** is not a wrong one: the same action without
   `surveyReport` completes `survey-completed` standalone, staleness from
   the snapshots and writes "not measured · no survey report in this run"
   (with one snapshot, both absences; report present with no snapshots,
   writes measured and staleness "not measured").
4. **Step 2 is claimed only as step 2.** An approved action of RE's type
   whose process instance's previous action is not `COMPLETED` with
   `survey-completed` is not claimed (Egeria should never create it, so
   the test pins RE's own refusal if it ever does); recorded once.
5. **Grouping reader.** Two engine actions sharing one process instance,
   one native and one RE, render one run line with two steps and two
   report links; a lone action renders as today; an action whose process
   instance cannot be read renders as today with "process unknown".
6. **Failure stops the chain, honestly.** Fixture: step 1 `survey-failed`,
   no step-2 action exists. Understanding shows "step 2 not started ·
   step 1 failed", never "pending".
7. **Same identity.** Step 2 runs as `Daemon(ENGINE_HOST,
   requested_by=<requesterUserId>)`, the requester copied from the
   step-2 action (Egeria copies it from the process start), `Ownership`
   on the report names the requester, as in step 3.
8. **Off means off** and **private target not claimed**, as step 3, re-run
   against the process-created action.

## 5.3 Gate (live, the owner's write round)

The owner runs M1's document, then M5 on coco_pharma. Then:

- Egeria shows a process instance with two engine actions in order; the
  first `COMPLETED` with `survey-completed` and a `surveyReport` target;
  the second claimed by RE's engine user, `COMPLETED` with
  `survey-completed`, its own `surveyReport` target, `requesterUserId`
  the owner.
- RE's step-2 report carries `derived_from_report` = the step-1 report
  GUID, and its annotation count equals the `step_runs` row's.
- Understanding shows the one run line with both steps and both reports,
  read through the existing readers.
- **Negative, live:** the same process on a database whose native survey
  fails (a wrong credential on a throwaway registration): step 1
  `survey-failed`, no step 2, Understanding says "step 2 not started ·
  step 1 failed".
- **Negative, live:** `initiate_gov_action_type` on step 2's type alone
  with a `surveyReport` of another database (or not the previous step's):
  `survey-invalid`, the message naming the mismatch.
- **Standalone, live:** step 2's type alone with only the asset target:
  `survey-completed`; writes "not measured · no survey report in this run".

## 5.4 Not in the slice

- `re-db-derived` as step 2 (step 5, same wiring).
- The `SurveyingActionProcess` subtype (one line when Dr.Egeria can).
- RE's own survey definitions as Egeria-dispatchable processes (case 3).
- A guard other than the three standard ones (Q-M5: look for an existing
  valid-value set before inventing one).
- Request parameters beyond what the process start carries.
- Cancel of a process (Mandy: a cancel process is coming).

## Open points for the owner

- [x] Does the Run route offer the mixed process beside `PostgreSQLSurvey`
      in this slice, or is Dr.Egeria the only trigger until the gate passes?
      **Decision (project owner, 2026-10-15):** RE's Run can start the mixed
      process. Runnable only when the engine host is on, the process is
      declared in Egeria, and the native step's credentials are in place.
- [ ] On a `survey-invalid` from step 1 (nothing to survey), should the
      process still run step 2? The design says no: nothing was measured.

## Built (engine host step 4, 2026-10-15)

**M2, read-only probe** (fixtures `tests/fixtures/egeria_6_2/engine_action__*`, `process_instance__*`,
`survey_report__native_postgres_database__*`), on a native `PostgreSQLDatabase:CreateAndSurvey` run of
laz_local_usda. It agrees with the design:

- the native survey service does not add `surveyReport` to its own action. Its completion passes the report GUID as
  a new action target, and the NEXT action carries `surveyReport` plus the original asset target under the name the
  process was started with (`newAsset` there; RE starts this process with `serverToSurvey`, the name its own
  PostgreSQLSurvey run uses);
- actions are linked by `ControlFlow` relationships carrying `guard` and `mandatoryGuard` (end 1 is the earlier
  action). Every action is anchored (`Anchors`) to its `GovernanceActionProcessInstance`, and its `processName` is
  the instance's qualifiedName. The instance links to its first action by `ActionRequester`. One
  `get_all_related_elements(guid, graph_query_depth=1)` returns an action's targets, its report, and its
  neighbours' status and guards;
- `initiate_gov_action_process` returns the process-instance GUID (as the stored catalog-and-survey rows show).

**Found by M2, not in the design:** a native PostgreSQL survey reports no row count. Its table measurements carry
PostgreSQL's cumulative rows inserted, updated and deleted.

**Decision (design session, 2026-10-15):** report two measures, kept distinct, never one function with two meanings.
- *Database staleness* keeps its definition: the change between two stored row-count snapshots, over their interval.
  The step-1 report is provenance only (`derived_from_report`). With one snapshot it says "not measured · one
  snapshot only", with no substitute figure.
- *Writes since statistics reset* is the native report's cumulative inserted/updated/deleted per table: totals since
  PostgreSQL's statistics reset, not a rate and not a delta. It is its own annotation (Egeria's
  `ResourceMeasureAnnotation`, as the native survey uses for these counters; annotationType "Writes Since Statistics
  Reset", its own check name). It carries the as-of time and the reset caveat, and appears only when a report is
  present.
- The manifest declares both (`measures`; 0.2.0 adds the second). RE stores the second under
  `database_staleness:writes_since_statistics_reset`, so a reader of staleness never sees it.

**Where it lives.**

- M1: `mixed_process.MIXED_PROCESSES` and `generate_engine_host_declaration.py --mixed-process` produce
  `docs/dr-egeria/engine-host/processes/postgres-survey-then-derived.md`. A process step cannot point at a
  GovernanceActionType, so each step carries the executor of the type it stands for and names the type in its
  Additional Properties.
- M3: in `engine_host.py`, the `surveyReport` target is never an asset. A library step that declares the
  `source_report` input gets the report as plain data, read by the host through `annotations_from_report`. The
  SurveyReport and the outcome carry `derived_from_report`. A later process step whose previous step did not
  complete with the linking guard is left unclaimed.
- M4: `mixed_process.group_by_process` / `run_line_words`, read back by `native_survey_run.refresh_mixed_run` into
  the row's `declared.process_run`.
- M5: the `mixed_process` row in `technology_type_processes.yaml`, started by `submit_native_survey`.

Tests: `tests/test_engine_host_step4_mixed_process.py`.
