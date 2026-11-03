# DESIGN — RE as an engine host, by equivalence (2026-10-13)

*(Status refreshed 2026-10-28: parts of this have since been built. The "Done" markers in the body record
them, for example engine host step 3, 2026-10-14. The original status line is kept below.)*

**Status: design note for the owner and the Egeria leads. Nothing here is built
and nothing is decided that belongs to them.** It restates a standing intent
and turns it into contracts, a gap list and one slice.

The owner's standing intent, restated on 2026-10-07 in the walking-skeleton
brief (`docs/extensibility-walking-skeleton-brief.md` §2c′, "Engine host:
later, and by equivalence"): **RE's Python engine is to appear to Egeria and
behave like a native engine host. Egeria's engine host is Java; RE's is Python.**
Until then, the door is kept open in two ways: steps are declared in the
governance-action family, and steps run only through RE's queue.

**Prior art this note builds on, and does not repeat:**

- `docs/survey-execution.md` §4 (target model: RE as an engine host), §7 (the
  four execution permutations; case 2 is "RE claims steps from an
  Egeria-orchestrated process"), §10 (registering a step as a request type).
  This note is an update of that design: what each part is now, and the slice.
- The original `re-as-engine-host-plan.md`. It was folded into
  `survey-execution.md` by 92575b84, and its last full text is
  `git show 92575b84^:packages/resource-explorer/docs/re-as-engine-host-plan.md`.
  It went **on hold on 2026-08-17** (e4ef1f04) because native surveys failed
  with a null connector on template-created assets.
- egeria-python `PYEGERIA_ISSUES.md` ISSUE-78. The claim trio shipped in
  pyegeria 6.1.5, and live arbitration was confirmed with
  `OMAG-GENERIC-HANDLERS-403-003`.

**Source pins.**

- Egeria Java: `/Users/dwolfson/localGit/egeria-v6/egeria` at `af400039c2`
  (2026-10-06). Paths below are relative to `open-metadata-implementation/`.
- pyegeria: 6.2.2, the version both the gate venv and the egeria-python
  checkout `adea1454` carry.
- RE: `origin/main` `fd94b34e`.
- Live probes were read-only, against `qs-view-server` on 2026-10-13. The
  scripts are kept in the session scratchpad: `probe_enginehost*.py`.

---

## 0. Summary

1. Egeria's engine host contract has five parts:
   - Registration: an engine element, its services, and request types.
   - Discovery: an event, plus a 5-second sweep.
   - Claim: first claim wins, enforced by the server.
   - Completion: a status, guards and new targets, made by the same user that claimed.
   - Restart: re-run what this engine's user had claimed.

   Nothing in that contract requires the claimant to be a JVM.
2. **The native engine host itself polls.** It sweeps every 5 s and works with
   no Kafka at all, so a polling RE is behaving natively, not degrading. The
   sweep is `threads/EngineConfigurationRefreshThread.java:51`, and the
   no-Kafka design is stated at `:168-186`.
3. **One gap is new since September.** On 2026-10-05 Egeria added a per-engine
   listing of "approved, nobody has claimed". That listing is exactly the
   discovery primitive RE lacked:
   - the call is `GovernanceEngineHandler.startMissedEngineActions`;
   - the route is `GovernanceContextResource.java:89`.

   But it exists only on the metadata server's `governance-context-service`.
   The View Server's `automated-curation` service does not have it, and that
   is RE's only channel to Egeria. This is the first question for Mandy (Q-M1).
4. RE already has most of the parts:
   - It has a claim-before-run queue with heartbeat and reconciliation
     (`run_queue.py`, `registry.claim_next_run`).
   - Its daemon identity records who requested the work, which is how Egeria's
     own engines record it.
   - It has a guard-aware DAG and Prefect execution.
   - It triggers engine actions and reads them back
     (`native_survey_run.py`, `egeria_delegated_step.py`).

   What it lacks:
   - registration as an engine;
   - a loop that claims and completes;
   - the `ReportOriginator` link from its SurveyReport to the engine action.
5. Today, in the running Egeria:
   - 23 engines are registered, and none of them is RE's.
   - RE's 13 Survey Definitions and 127 steps are in Egeria, but **no RE step
     has a `GovernanceActionExecutor`**. Egeria therefore cannot dispatch a
     single RE step: if someone initiated `GovActionProcess::DatabaseScoutingSurvey`
     in Egeria, it would have no executor to run.
6. **Identity.** An engine host runs unattended, so it is a daemon by
   definition. Under the private-investigation rule (#640 merged, #644 pending),
   private work never runs as the service account. The engine host therefore
   cannot run work whose targets are in a private investigation. Native Egeria
   already does the same thing a different way: its sweep omits any engine
   action the engine's user cannot read in full
   (`GovernanceEngineHandler.java:543-544`). What RE should show the owner in
   that case is the owner's call (Q-O3).
7. **The proposed slice** is one existing, zero-fetch RE step (`db_derived`),
   declared as a request type on an RE engine and dispatched by Egeria.
   - RE claims the action, runs it through its queue, publishes a SurveyReport
     linked to the action, and completes with `survey-completed`.
   - The proof is that RE's Understanding read-back cannot tell the result from
     a PostgreSQLSurvey run.

---

## 1. What "behave like a native engine host" means

### 1.1 The contracts

For each contract: the property Egeria relies on, then its source. A claim
without a source is labelled **assumption**.

**C1. Registration: the engine.**
- What Egeria relies on: a `GovernanceEngine` element of a subtype that says
  what it runs. Live subtypes are `GovernanceActionEngine`,
  `SurveyActionEngine` and `WatchdogActionEngine`. Each engine has its own
  `userId`, and that user is the engine's `processingEngineUserId` on every
  action it claims.
- Source:
  - Live probe: the 23 engines with their subtypes, and `processingEngineUserId`
    values such as `postgresqlsurveyengine` and `stewardshipengine`.
  - `open-metadata-resources/open-metadata-archives/core-content-pack/.../GovernanceEngineDefinition.java:84-88`
    (`POSTGRES_SURVEY_ENGINE`, userId `postgresqlsurveyengine`), `:280`, `:352`.

**C2. Registration: request types.**
- What Egeria relies on: a `GovernanceService` linked to the engine by
  `SupportedGovernanceService`, a MULTI_LINK relationship with properties
  `requestType`, `serviceRequestType`, `requestParameters` and
  `generateConnectorActivityReports`. The engine host builds a lookup from
  request type to service from these links.
- Source:
  - Live: `PostgreSQLSurvey` → `survey-postgres-database` and
    `survey-postgres-server`, both with `generateConnectorActivityReports=true`.
  - `GovernanceEngineHandler.java:633`, `:674` (`governanceServiceLookupTable`).
  - `survey-execution.md` §10.

**C3. Dispatch target.**
- What Egeria relies on: a `GovernanceActionType` or
  `GovernanceActionProcessStep` reaches an engine through
  `GovernanceActionExecutor{requestType}`. Steps are chained by
  `NextGovernanceActionProcessStep{guard, mandatoryGuard}`. A step carries
  `producedGuards`, `waitTime` and `ignoreMultipleTriggers`.
- **Caveat on `waitTime` units.** The property is documented in minutes,
  but the code multiplies by 1000 L, which gives seconds
  (`OpenMetadataProperty.java:2771`; `EngineActionHandler.java:422`, `:997`).
- Source:
  - Live: `PostgreSQLSurvey::survey-postgres-database` has one executor link
    with `requestType=survey-postgres-database`.
  - `EngineActionHandler.java`:
    - `:1158` (mandatory guards read);
    - `:1328` (a next step fires only when all mandatory guards have been received);
    - `:966`, `:1812` (`ignoreMultipleTriggers`).

**C4. Discovery.**
- What Egeria relies on, in two paths:
  - (a) An event on the out topic, handled by `OpenGovernanceOutTopicListener`.
  - (b) A sweep every **5 s** of "approved for this engine, not claimed",
    paged 10 at a time. The sweep is the reliable path, and no Kafka is needed.
- The sweep skips any engine action the engine's user cannot read in full.
- Source:
  - `engine-host-services-registration/.../listener/OpenGovernanceOutTopicListener.java:24`.
  - `threads/EngineConfigurationRefreshThread.java:51` (5000 ms) and
    `:168-186` (the design rationale).
  - `GovernanceEngineHandler.java:536-600`.
  - The listing: `gaf-metadata-spring/.../GovernanceContextResource.java:89`
    (`/governance-engines/{guid}/approved-engine-actions`), added by
    Mandy Chessell on 2026-10-05 (`cb46831ca4`, `c1de0440bc`).

**C5. Claim.**
- What Egeria relies on:
  - A claim succeeds only if the status is `APPROVED` and
    `processingEngineUserId` is null.
  - The claim sets the status to **`ACTIVATING`** and records the claimant as
    the processing user. The host later moves the action to `IN_PROGRESS`.
  - A second claimant is refused: first claim wins.
  - The native host re-reads the action, checks that it names this engine,
    claims it, and treats "already claimed" as a normal outcome.
  - **Caveat:** arbitration is a read-check-write under a JVM-local lock in
    the metadata access server. No version check was found, so two metadata
    servers on one repository are not protected.
- Source:
  - `common-services/generic-handlers/.../EngineActionHandler.java`:
    - `:2266`, `:2338` (the claim gate);
    - `:2353` (status set to ACTIVATING);
    - `:44`, `:2281` (`claimLock`).
  - `EngineActionBuilder.java:381-404` (the claimant recorded).
  - `GovernanceEngineHandler.java:713-731`.
  - Live 403-003 (ISSUE-78; `survey-execution.md` §4.3).

**C6. Run.**
- What Egeria relies on:
  - The service receives the request type, `requesterUserId`,
    `requestedStartTime`, `requestParameters`, `requestSourceElements` and
    `actionTargetElements`.
  - A future start time is honoured by waiting **after** the claim.
  - Per-target progress goes through `update_action_target_status`.
  - A **survey** engine needs an Asset action target. With several targets it
    uses the one named `newAsset`; with none it fails with `NO_TARGET_ASSET`.
  - The registered default `requestParameters` are merged under the action's
    own, and the action's values win.
- Source:
  - `GovernanceEngineHandler.java:733-739`.
  - `admin/GovernanceServiceHandler.java:138-174`: the claim is made, then the
    status goes to WAITING, then the host sleeps until the start time, then
    the status goes to IN_PROGRESS.
  - `engine-services/survey-action/.../handlers/SurveyActionEngineHandler.java:126-158`, `:227-257`.
  - `admin/GovernanceServiceCache.java:157-181`.
  - The view route: `AutomatedCurationResource.java:562`.

**C7. Status while running.**
- What Egeria relies on: a status update is accepted only from the claimant,
  or for an unclaimed APPROVED action. Statuses are those of `ActivityStatus`:
  `REQUESTED`, `APPROVED`, `WAITING`, `ACTIVATING`, `IN_PROGRESS`, `PAUSED`,
  `FOR_INFO`, `COMPLETED`, `INVALID`, `IGNORED`, `FAILED`, `CANCELLED`,
  `ABANDONED`, `OTHER`.
- Source:
  - `EngineActionHandler.java:2581-2639`.
  - `frameworks/open-metadata-framework/.../enums/ActivityStatus.java:26-91`.

**C8. Completion.**
- What Egeria relies on:
  - `recordCompletionStatus` takes the status, output guards, new action
    targets and a message.
  - Only the claimant may complete.
  - **Egeria then starts the next steps server-side.** The claimant does no
    chaining.
- Source:
  - `EngineActionHandler.java:2685`, `:2743` (same user), `:2845`
    (`initiateNextEngineActions`).
  - The view route: `AutomatedCurationResource.java:591`.

**C9. Survey output.**
- What Egeria relies on:
  - The survey's standard guards are:
    - `survey-completed` → ACTIONED;
    - `survey-invalid` → INVALID;
    - `survey-failed` → FAILED.
  - The `SurveyReport` hangs off the asset by `ReportSubject` and off the
    engine action by `ReportOriginator`. Annotations are linked to the report.
  - The native report has these properties:
    - `qualifiedName` `SurveyReport::<requestType>::<assetGUID>::<time>`;
    - `authors = [engineUserId]`;
    - `purpose` = the request type;
    - it is anchored on the asset.
  - A service that sets no status completes with `survey-completed`. An
    exception completes with `survey-failed`.
  - The handler always adds a `surveyReport` action target carrying the
    report GUID, and writes the completion message onto the report.
- Source:
  - `frameworks/open-survey-framework/.../controls/SurveyActionGuard.java:18-20`.
  - `frameworks/open-survey-framework/.../AnnotationStore.java:67-150`
    (`ReportOriginator` at `:141-148`).
  - `SurveyActionEngineHandler.java:295`, `:311`.
  - `engine-services/survey-action/.../handlers/SurveyActionServiceHandler.java:158-167`,
    `:186-195`, `:217-231`.
  - Live: 10 completed `survey-postgres-database` actions with
    `completionGuards={0=survey-completed}`.
  - `ReportOriginator`: found live on 2026-09-30 (`native_survey_run.py:30-35`),
    and read by Egeria's own `HarvestSurveysCatalogTargetProcessor.java:243`.

**C10. Cancel.**
- What Egeria relies on: a `CANCELLED` action claimed by this engine's user is
  stopped by the host.
- Source: `GovernanceEngineHandler.java:741-745`.

**C11. Restart.**
- What Egeria relies on: at start the host lists the actions this engine's user
  has claimed that are still active (WAITING, ACTIVATING or IN_PROGRESS), and
  **re-runs each one from the start**. A survey's re-run creates a new
  SurveyReport. The listing is per engine and per claimant, and it is the only
  per-engine listing the View Server exposes.
- **Egeria has no lease, heartbeat or reclaim.** An action claimed by a user
  that never returns stays claimed. RE's heartbeat and reconciler protect only
  RE's own `runs` rows.
- Source:
  - `GovernanceEngineHandler.java:487-533`, `:806-829`.
  - `EngineActionHandler.java:3611-3690` (the restart query).
  - The view route: `AutomatedCurationResource.java:529`.

**C12. Configuration refresh.**
- What Egeria relies on: the host reloads the engine's definition and services
  on a cycle throttled to **10 minutes**, on events about the engine and its
  `SupportedGovernanceService` links, and on request.
- Source:
  - `GovernanceEngineHandler.java:62`, `:217-228`.
  - `listener/OpenGovernanceOutTopicListener.java:75-141`.
  - `EngineConfigurationRefreshThread.java:187-215`.
  - `engine-host-services-server/.../EngineHostInstance.java:80-140`.
  - pyegeria `RuntimeManager.refresh_gov_eng_config`.

**C13. Identity on the action.**
- What Egeria relies on:
  - `requesterUserId` is the person who asked.
  - `processingEngineUserId` is the engine's own user.
  - The person is recorded and is not the one who runs the work.
- The engine user comes from the engine host configuration's
  `EngineConfig.engineUserId`, falling back to the server's own user. It is
  used for every call the engine makes, including the survey's metadata
  writes.
- Source:
  - Live, for example `PostgreSQLSurvey` actions with
    `requesterUserId=peterprofile`, `processingEngineUserId=postgresqlsurveyengine`.
  - `admin-services/.../properties/EngineConfig.java:27-29`.
  - `GovernanceEngineHandler.java:98-102`.
  - `SurveyActionEngineHandler.java:302-344`.
  - `requesterUserId` is carried along a process: `EngineActionHandler.java:832`,
    `:2810-2813`.

**Not contracts, but visible to Egeria users:**

- connector activity reports (the `generateConnectorActivityReports` flag);
- an engine host summary (`ServerOps.get_governance_engine_summaries`), which
  a Python process cannot appear in.

Section 6 asks whether equivalence must extend to these (Q-M4).

### 1.2 What Egeria can and cannot tell apart

If RE honours C1–C13, an engine action for RE's engine looks like any other in
three ways:

- **Lifecycle.** It moves REQUESTED → APPROVED → IN_PROGRESS → COMPLETED or
  FAILED, under a named engine user.
- **Guards.** They come from the step's declared `producedGuards`.
- **Survey report.** It is linked by `ReportOriginator`.

**What Egeria could still tell:**

- There is no OMAG Engine Host server whose configuration lists RE's engine.
  RE's engine must appear in **zero** Java engine host configurations, or a
  Java host races RE for the claim and fails to resolve a connector that does
  not exist. The September plan made this a deployment rule (`survey-execution.md`
  §7, case 2).
- The `GovernanceService`'s connector provider class names nothing a JVM could
  load.
- No connector activity reports.
- RE's engine is absent from every engine host summary.

**What Egeria cannot tell, beyond the action itself:** OpenLineage. Egeria
derives its run events from engine-action status changes in an integration
connector, not from the engine host (§4.2).

**The one external seam is the REST protocol.** Egeria has no notion of a
non-Java or "remote" governance service. Every native service is a Java
connector built by `ConnectorBroker` (`admin/GovernanceServiceCache.java:220-222`).
Egeria's docs use "remote engine host" only for a host connected to a remote
metadata server (egeria-docs `patterns/active-governance/overview.md:174`,
`:186`), and say that several hosts may run the same engine "for horizontal
scalability" (`concepts/engine-host.md:12`).

Whether any of these matter is Q-M4 and Q-M5.

### 1.3 Live state (read-only probes, 2026-10-13)

| What | Found |
|---|---|
| Governance engines | 23. All are Egeria's content-pack engines, for example `PostgreSQLSurvey`, `FileSurvey`, `EgeriaGovernance`, `Stewardship`, `EgeriaWatchdog`. **None is RE's.** |
| RE Survey Definitions in Egeria | 13 `GovActionProcess::*` (3 database, 10 repo) |
| RE steps in Egeria | 127 `GovActionProcessStep::*`. `executes_at` / `re_analysis_step` travel only in `additionalProperties`. **`GovernanceActionExecutor` on `...DatabaseScoutingSurvey::postgres_schema_and_stats`: none.** Its `NextGovernanceActionProcessStep` link is present. |
| GovernanceActionTypes for RE's surveys | none. RE triggers Egeria's own `PostgreSQLSurvey::survey-postgres-database` (`configdata/technology_type_processes.yaml:54`, kind `survey_existing`) |
| Engine actions | 871 in total, 840 of them `EgeriaGovernance` OpenLineage/zone housekeeping. RE-initiated database work: `survey-postgres-database` 10 COMPLETED and 3 FAILED; `survey-postgres-server` 5 FAILED and 2 CANCELLED; `PostgreSQLGovernance` catalog/create 13 COMPLETED; `Stewardship print-survey-report` 5 COMPLETED. Requesters are `erinoverview` (daemon) and `peterprofile` (a signed-in person) |
| Active now | 1 (`EgeriaWatchdog award-karma-points`, IN_PROGRESS) |
| Defect seen | pyegeria `AutomatedCuration.get_engine_actions()` returns 404: there is no `GET /engine-actions` on the View Server (`AutomatedCurationResource.java` maps only `/engine-actions/active`, `:435`). This is not in `PYEGERIA_ISSUES.md`, and logging it is the owner's call under the standing policy. |

The hold of 2026-08-17 ("connector is null" on native surveys of
template-created assets) no longer describes the database path: 10 native
database surveys have completed. Whether the hold is lifted is the owner's
call (Q-O1).

---

## 2. What RE has today, per contract, and the gaps

| Contract | What exists in RE (file:line, `fd94b34e`) | Gap |
|---|---|---|
| **C1 Engine element** | Nothing registers RE as an engine. The 2026-08-17 probe authored a `GovernanceActionType` against Egeria's `Stewardship` engine (`docs/dr-egeria/re-delegated-step-probe.md`) | An RE engine element (subtype: Q-M2), its engine user (Q-M3, Q-O2), and the Dr.Egeria commands to author it: pyegeria has no `create_governance_engine` or `create_governance_service`, and the generic element create is the route (`survey-execution.md` §10; pyegeria `AssetMaker.link_supported_governance_service`, `omvs/asset_maker.py:1697`) |
| **C2 Request types** | `re_analysis_step` and `executes_at` as `additionalProperties` on each step (`docs/dr-egeria/survey-definitions*/`, generated by `scripts/generate_*_survey_definition.py`). The step registries `STEP_REGISTRY` (`surveyors/repo_survey_definition_adapter.py:442`) and `DATABASE_STEP_REGISTRY` (`surveyors/database/survey_definition_adapter.py:546`) are the natural set of request types. `StepManifest` (`packages/trellis-analytics/src/trellis_analytics/manifest.py:43-47`) is the user-step declaration | One `SupportedGovernanceService` per step key, generated from the registry (and from a `StepManifest` for user steps), never hand-written |
| **C3 Dispatch target** | `SurveyStep` already reads `produced_guards`, `wait_time` and `ignore_multiple_triggers` (`surveyors/survey_definition_reader.py:215-230`). `StepLink` carries `guard` and `mandatory_guard` (`:190-212`). The plan is a DAG from links (`surveyors/survey_execution_plan.py:1-30`) | The generator writes no `Link Action to Action Executor` for RE steps, and no `producedGuards` |
| **C4 Discovery** | `native_survey_run.EgeriaSurveyPort` reads engine actions by GUID (`native_survey_run.py:385-410`). The scheduler sweeps native surveys (`scheduler.py:190`) | No loop asks "what is approved for RE's engine" (§3 options) |
| **C5 Claim** | RE's own queue claims before running: `registry.claim_next_run` (`registry.py:4416`, `FOR UPDATE SKIP LOCKED` at `:4467`), `run_queue.claim_and_execute_once` (`run_queue.py:613`). pyegeria `claim_engine_action` (`omvs/automated_curation.py:2378`) is live-verified (ISSUE-78) | No call site. Two claims (Egeria's and RE's queue) must be ordered (§3.3) |
| **C6 Run** | Handlers by kind (`run_queue.py:344-355`). A single step runs with no definition through `SurveyDefinitionExecutor.run_synthetic_step` (`surveyors/survey_definition_executor.py:1505`). Prefect whole-definition flow (`prefect/flows.py:413`) and per-step task (`:301`) | A `runs` kind for an engine action. Mapping the action target (an asset GUID) to RE's entity slug |
| **C7 Status** | `step_runs` already stores `engine_action_guid`, `engine_action_status`, `engine_action_message` and read times (`registry.py:2523-2531`). These are RE as requester, read back | Writing status as the claimant (`update_engine_action_status`, pyegeria `:2422`) |
| **C8 Completion** | Guards are produced and evaluated locally (`survey_definition_executor.py:643-709`; `docs/survey-guard-evaluation-design.md`) | `record_completion_status` (pyegeria `:2570`) with guards from the step's declared set. Never complete without a stored SurveyReport GUID ("status words derive from proof rows") |
| **C9 Survey output** | `EgeriaPublisher._create_survey_report` anchors by `ReportSubject` (`surveyors/egeria_publisher.py:1184-1210`). The reader finds reports by `ReportOriginator` (`native_survey_run.py:396-410`) | RE's report carries no `ReportOriginator` link to an engine action, so a report RE wrote as an engine would be invisible to its own read-back (`native_survey_run.report_guid_for_action`). Its qualified name is repo-shaped (`SurveyReport::GitHubRepo::…`, `:1186`) |
| **C10 Cancel** | `EgeriaSurveyPort.cancel` (`native_survey_run.py:389-395`) as requester. Prefect flow runs can be cancelled | Honour CANCELLED on an action RE claimed: stop the run or its Prefect flow |
| **C11 Restart** | `run_reconciler.py` (pid-based, `interrupted` never `error`) and the heartbeat (`run_queue.py:371-395`). Both are RE-side only | At worker start: `get_active_claimed_engine_actions(re_engine_guid)` (pyegeria `:2619`), then re-run, matching Egeria (`:806-812`). Reconciling with RE's own `runs` row (§3.4) |
| **C12 Config refresh** | `survey_definitions` cache warm and the drift scan (`survey-execution.md` §1.3) | Re-read RE's engine definition: which request types RE has declared versus installed. This is the `drift` the skeleton names |
| **C13 Identity** | `Daemon(reason, requested_by)` with a closed `DaemonReason` (`egeria_clients.py:124-138`, `:255`). Queued work is committed by the daemon with `Ownership` set to `requested_by` "as Egeria's engine hosts commit theirs" (`egeria_identity.py:21-47`; `run_queue.py:399-430`) | A new `DaemonReason` member (proposed `ENGINE_HOST`), and an engine user that the claim, status updates and completion all share (§3.5) |

---

## 3. How RE would claim work

### 3.1 Discovery options

| Option | What | Equivalence | Cost and risk | Channel |
|---|---|---|---|---|
| **A. Poll the View Server's `GET /engine-actions/active`, filtered client-side** | Whole-server active list (pyegeria `get_active_engine_actions`, `:2785`), kept if `executorEngineGUID == re_engine_guid` and `activityStatus == APPROVED` and `processingEngineUserId` is empty | Same semantics as the native sweep; a wider fetch | Grows with every active action on the server (840 housekeeping actions today are terminal and drop out; a busy server is wider). Correct because claim arbitrates (C5). **Buildable now.** | View Server only |
| **B. A per-engine "approved" listing on the View Server** | The same call the native sweep uses, `approved-engine-actions` (C4), exposed through `automated-curation` | Identical to native | Needs Mandy (Q-M1) and a pyegeria wrapper | View Server only |
| **C. Call `governance-context-service` on the metadata server directly** | Native's own client (`OpenGovernanceClientBase.java:481-490`) | Identical | Breaks RE's single channel (pyegeria through the View Server; `project_egeria_resource_explorer_redesign`), and the `users/{userId}` path form | Second channel (not recommended) |
| **D. Consume the out topic** | Kafka `OpenGovernanceOutTopicListener` equivalent | Native's fast path | pyegeria has no Kafka client (`survey-execution.md` §4.2). Latency only: native itself relies on the sweep for correctness (C4) | New dependency |

**Recommendation:** build A now, ask for B, and add D only if latency matters.
The native host's own design says a sweep-only host "is slower to react, by at
most the interval below, but it works"
(`EngineConfigurationRefreshThread.java:177-182`). RE's interval should match
it (5 s) or be a stated deviation.

### 3.2 Where the loop lives

The claim loop belongs in the **worker role**, beside the scheduler and the
outbox drain (`docs/runtime-architecture-plan.md` §2). It is guarded by the
existing advisory-lock leader election, so two workers never both claim. It
never runs a step inline.

A claimed engine action becomes a `runs` row (new kind `engine_action`)
carrying `engine_action_guid`, `request_type`, the action targets and
`requested_by = requesterUserId`. It is executed by `run_queue` like every other
kind. This is the skeleton's "queue-only execution" rule, so an Egeria dispatch
lands on the same path as a person's press.

**Prefect stays the executor whenever it is enabled.** For one step, the
`engine_action` handler calls `run_synthetic_step` with `executes_at`
`resource-explorer` (or `prefect` for `PREFECT_ONLY_STEPS`), so
`run_prefect_step` dispatches it exactly as today.

When Egeria coordinates, Egeria walks the process graph and RE runs leaves.
**RE never sequences an Egeria-coordinated process**, which keeps "two
coordinators, neither of which is RE" (`docs/Architecture.md`, "Two
coordinators").

### 3.3 Ordering the two claims

Egeria's claim (C5) and RE's queue claim are two arbitrations. The proposed
order mirrors native, which claims first and then waits for the start time
(C6):

1. The loop sees an APPROVED action and checks that RE can run the request type
   and has a step for it.
2. It claims in Egeria, which sets the action to `ACTIVATING`. A 403-003
   (another claimant) is an ordinary outcome: skip it, and write nothing.
3. It inserts the `runs` row, with `not_before = requestedStartTime` when that
   time is in the future. In that case it sets `WAITING`, as native does after
   its claim (C6).
4. It sets `IN_PROGRESS` when the worker picks the row up. That status change
   is also what the native OpenLineage connector reads as START (§4.2).

If step 3 fails after step 2, the action is RE's and has no row. Step 4 of
§3.4's restart pass finds it (claimed in Egeria, no row) and runs it, as
native does.

**Unknown request type.** An action for RE's engine whose request type RE does
not support is left unclaimed. Whether native fails it or leaves it is
**assumption to verify**: `GovernanceEngineHandler` looks up
`governanceServiceLookupTable` (`:633`), and its null path is not yet read.

### 3.4 Restart

At worker start, before the first sweep:

1. List `active-claimed` for RE's engine.
2. For each action, look for its `runs` row:
   - a row that is running under a live process: leave it;
   - a row that is `interrupted`, or no row: re-run;
   - a terminal row whose completion was never recorded: record it from the row.

Re-run is native behaviour (C11). The non-idempotent part is completion.
`recordCompletionStatus` from a second attempt is refused once the action is
terminal, and RE reads that refusal as "already recorded", never as a failure
to retry.

The incident rule "a drain must never retry a destructive write" (2026-10-06)
applies to anything a step writes: steps admitted to the engine host are
read-only on the resource (§4.3).

### 3.5 Identity: who the engine runs as

**Native.** Each engine has its own userId (C1). The person who asked is
`requesterUserId`, and the engine user claims, writes and completes (C13). This
is the same shape as RE's queued work today: `Daemon(RUN_QUEUE, requested_by)`,
with `Ownership` stamped to the requester (`egeria_identity.py:30-47`).

**Proposed.**
- A new `DaemonReason.ENGINE_HOST`.
- Every engine-action write runs as `Daemon(ENGINE_HOST,
  requested_by=requesterUserId)`.
- One Egeria user is the claimant, and must be, since completion is checked
  against the claimant (`EngineActionHandler.java:2743`).

Which user that is belongs to the owner and Mandy (Q-O2, Q-M3). *Settled
2026-10-14: (i), as a daemon with no relationship to signed-in users; see the
Q-M3 decisions below.* The two candidates were:

- **(i)** RE's existing daemon account. No new account, but the engine's
  actions and RE's other daemon writes share an identity.
- **(ii)** A dedicated engine user (for example `resourceexplorerengine`),
  mirroring `postgresqlsurveyengine`. This needs a UserIdentity, a token source
  and zone grants.

**The private-investigation rule meets an unattended host.**

#640 (merged) and #644 (pending) settle that a private investigation's Egeria
work runs as its owner, never as the service account. RE's daemon "cannot see a
private investigation" and the row waits with the words
`owner_waiting_words(owner)` (`workflows/egeria_work_access.py:67-70`;
`egeria_outbox.py:158-166`, `:1011-1035`). Survey artifacts for a resource in a
private investigation are zoned to the owner by `private_owner_for_entity`
(`registry.py:10583`), which is deliberately unscoped.

An engine host is unattended, so it is a daemon in exactly the sense that rule
excludes. Three facts follow:

1. **Native already hides such work from the engine.** The sweep omits "any
   engine action this engine's user cannot read in full"
   (`GovernanceEngineHandler.java:543-544`). If the action's targets sit in an
   owner's private zone, RE's engine user (either candidate) cannot read them,
   so under option B RE never sees the action, as native would not. Under
   option A the whole-server list may include it; RE must apply the same rule
   itself and leave it unclaimed.
2. **RE must not claim and then refuse.** A claimed action can only end in a
   completion status. Claiming in order to fail with "owner required" writes a
   FAILED action under the engine's user into a private context. Leaving it
   unclaimed is the native shape.
3. **The owner still needs to see something.** An unclaimed APPROVED action
   sits indefinitely. RE could show it on the owner's screen as "waiting for
   you" and run it attended under the owner's identity (the #640 inline path),
   but that is a second execution path for the same action and a departure
   from native. Whether to offer it, or to say that private investigations are
   not served by the engine host, is Q-O3.

The 2026-10-05 discussion with the leads (`DISCUSSION-EGERIA-LEADS-2026-10-05.md`
§7) already found a zone locking out `PostgreSQLSurvey`'s engine user. The same
question covers RE's engine user (Q-M3).

---

## 4. Relationships

### 4.1 To the Egeria-native surveys RE triggers today

Today RE is a **requester**:

- `native_survey_run.submit_native_survey` (`:904`) calls
  `initiate_gov_action_type` (`:359`).
- The read-back finds the report by `ReportOriginator`, and
  `derive_native_state` (`:528`) turns persisted proof into words.
- `egeria-adaptive` chooses between reuse, local and native per database
  (`surveyors/database/survey_definition_adapter.py:309`).
- Case 4, delegating one step to a Java engine, is built
  (`surveyors/egeria_delegated_step.py`).

As an engine host, RE becomes a **provider** on the same protocol. Three
consequences:

- **One read-back for both.** An RE-claimed action is an `EngineAction` with a
  `ReportOriginator` report, so `derive_native_state` reads it unchanged. That
  is the equivalence test (§5).
- **`executes_at` becomes catalog data** (`survey-execution.md` §4.4). Who runs
  a step is the engine named by its `GovernanceActionExecutor`. `executes_at`
  remains the authored fallback for RE-coordinated runs, and the generator
  must keep the two in agreement: `executes_at: resource-explorer` implies an
  executor link to RE's engine. A mismatch is a drift finding, never repaired
  silently.
- **Mixed processes become possible** (case 2): an RE step and a
  `PostgreSQLSurvey` step in one Egeria process, chained by guards that Egeria
  evaluates. That is the point of the work, and it is not in the first slice.

### 4.2 To R21 OpenLineage run events

The builder on `re/openlineage-run-events` has this work uncommitted at the
time of writing:

- `observability/openlineage.py`;
- emission from `step_cost_observer.observe()`;
- `run.runId` a fresh UUID per step run, stored at
  `step_runs.metrics.openlineage.run_id`;
- job `resource-explorer/<survey|analysis>.<step_key>`;
- off by default;
- HTTP to an Integration Daemon's `open-lineage-events/publish-event`.

**What Egeria does natively.** The engine host itself emits no OpenLineage:
there is no match for "openlineage" in `engine-host-services`,
`engine-services` or `gaf-metadata-management`.

The events come instead from an **integration connector** in an Integration
Daemon, `GovernanceActionOpenLineageIntegrationConnector`
(`adapters/open-connectors/integration-connectors/openlineage-integration-connectors/.../GovernanceActionOpenLineageIntegrationConnector.java`).
It listens for EngineAction status changes (`:134-159`) and maps them:

| EngineAction status | OpenLineage event |
|---|---|
| IN_PROGRESS | START |
| COMPLETED | COMPLETE |
| FAILED | FAIL |
| INVALID | ABORT |

The mapping is at `:228-243`. The rest of the event:

- **Job name:** `processStepName`, else `<executorEngineName>::<requestType>` (`:248-255`).
- **Namespace:** `GovernanceActions` by default (`:49`, `:88-93`).
- **runId:** derived from the engine action GUID (`:264`).
- **Parent run:** the process (`:266-287`).
- **Facet:** a custom `egeria_governanceAction` run facet
  (`OpenLineageEgeriaGovernanceActionRunFacet.java:35-41`).
- **Datasets:** the action targets (`:315-330`).

**Consequence.** If RE drives the same status transitions over REST, which C5,
C7 and C8 require anyway, then wherever that connector is deployed Egeria
produces the native OpenLineage run for an RE-claimed action with no RE code.
That is equivalence for free.

R21 should therefore **not** emit a second, differently named run for a step
RE runs *because Egeria dispatched it*. The proposal:

- **RE-coordinated runs:** R21 emits as built, under namespace
  `resource-explorer`.
- **Egeria-dispatched runs:** R21 is off by default. If both are on, R21
  stamps its event with the engine action's runId (or a parent run facet
  naming it), so a consumer sees one run.
- **Proposed job name:** the request type under RE's engine, so the native job
  name `<engine>::<requestType>` and RE's agree.

Whether that pairing is right is Q-M6.

### 4.3 To the walking skeleton's user steps

The skeleton's three levels (brief §2c′) map onto the engine host directly:

| Skeleton level | Engine host |
|---|---|
| 1. The code (`run(inputs) -> results`) | Unchanged. It never sees Egeria |
| 2. The wrapper (`SurveyStep` / RE's `SurveyStepHost`, declared by `StepManifest`) | **This is the governance service.** The manifest's name becomes the request type, its `annotation_type` the annotations the report carries, and its declared outcomes the `producedGuards`. The generator emits the `SupportedGovernanceService` and `GovernanceActionType` beside the catalog entry (brief §2c, "Registered in Egeria by a Dr.Egeria document") |
| 3. The survey (Egeria Survey Definition) | Already Egeria's. The engine host only makes its RE steps dispatchable |

**Read-only on the resource** (`StepManifest.credential_tier:
Literal["read_only"]`, `manifest.py:47`) is what makes unattended execution
safe enough to consider. The host refuses a request type whose manifest
declares anything else, with a sentence naming the tier. The first slice
avoids even that by choosing a step that opens no connection.

Steps are not credential-free in general. A database step needs a connection,
and the engine host has no person's token. That is the same gap as queued runs
today (`egeria_identity.py:30-47`). It is solved the same way: RE's stored
connection at read-only tier, never an Egeria token at rest.

---

## 5. Walking-skeleton slice: one RE step, dispatched by Egeria

**Goal: the smallest end-to-end proof of equivalence.**

1. Egeria creates an engine action for RE's engine.
2. RE claims it, runs it through its queue, publishes a SurveyReport linked to
   it, and completes it with a declared guard.
3. RE's own Understanding read-back, which today reads `PostgreSQLSurvey`
   actions, reads it with no new code path.

**The step: `db_derived`** (`surveyors/database/survey_definition_adapter.py:617-638`).

- It is already in `GovActionProcess::DatabaseAnalysisSurvey`.
- It is "zero-fetch derivation over already-stored rows". It never opens a
  connection, so no credential question arises in the first slice.
- It has two natural outcomes:
  - with a stored schema inventory it measures;
  - without one it returns `not_measured`, "an absence, not a finding".

  These map onto `survey-completed` and `survey-invalid` (C9) without
  inventing a guard. The mapping is a proposal (Q-M5).

### 5.1 Pieces

| # | Piece | Where | Write? |
|---|---|---|---|
| E1 | **Declaration generator.** Emits a Dr.Egeria document: the RE engine (`ResourceExplorerSurvey`, subtype per Q-M2), one `GovernanceService` for the step, `SupportedGovernanceService{requestType: "re-db-derived"}`, and a `GovernanceActionType` `ResourceExplorerSurvey::re-db-derived` with `GovernanceActionExecutor` to the engine and `producedGuards [survey-completed, survey-invalid, survey-failed]`. Generated from `DATABASE_STEP_REGISTRY`, with a test that the document equals the registry entry | RE `scripts/`, document under `docs/dr-egeria/engine-host/` | No. The owner runs the document (an Egeria write) |
| E2 | **pyegeria probe** of the six calls with real signatures, read-only where possible: `get_active_engine_actions`, `claim_engine_action`, `update_engine_action_status`, `record_completion_status`, `get_active_claimed_engine_actions`, `link`-by-generic-create for `ReportOriginator` | gate venv | Read-only, except `claim` on the slice's own action |
| E3 | **`engine_host.py` sweep** (option A): filter, claim, insert an `engine_action` `runs` row. New `DaemonReason.ENGINE_HOST`. Worker-only, behind `RE_ENGINE_HOST_ENABLED=false` by default, with the engine GUID resolved by qualified name | RE `resource_explorer/` | Egeria: claim and status only |
| E4 | **`engine_action` handler:** action target GUID → entity slug (RE's registry has the asset GUID per database). `run_synthetic_step`. Publish the SurveyReport shaped like native (C9): `ReportSubject` **and `ReportOriginator`**, qualifiedName `SurveyReport::<requestType>::<assetGUID>::<time>`, `purpose` = the request type, `authors` = the engine user. `record_completion_status` with the guard and a `surveyReport` new action target carrying the report GUID, only after the report GUID and annotation count are stored | `run_queue.py`, `egeria_publisher` (report shape and link) | Egeria writes as `ENGINE_HOST` |
| E5 | **Restart pass** (§3.4) | `engine_host.py` | As E3 |
| E6 | **Trigger:** the owner runs `initiate_gov_action_type("ResourceExplorerSurvey::re-db-derived", action_targets=[coco_pharma database asset])` from Dr.Egeria. RE's existing Run route could also do it through `EgeriaSurveyPort`, since it is the same call it makes for `PostgreSQLSurvey` | — | Egeria write (the owner's) |

### 5.2 Tests that fail first

Each write path gets one end-to-end test that fakes only the pyegeria client
boundary, with fakes that bind real signatures (standing rules).

1. **A refused claim is ordinary.** The fake's `claim_engine_action` raises
   `PyegeriaUnauthorizedException` carrying `OMAG-GENERIC-HANDLERS-403-003`.
   The sweep writes no `runs` row, does not log at error, and the next sweep
   continues.
2. **Only RE's approved work is claimed.** In one sweep:
   - an action for `PostgreSQLSurvey`: not claimed;
   - an action for RE's engine that is `REQUESTED`: not claimed;
   - one with an unknown request type: not claimed, recorded once;
   - one already claimed (`processingEngineUserId` set): not claimed;
   - one approved action with a known type: claimed exactly once.
3. **Completion only from proof.** A run whose report publish failed never
   calls `record_completion_status` with `survey-completed`. It completes
   `survey-failed` with the publish error's words, and the report GUID column
   stays empty.
4. **Same identity end to end.** Claim, status update, completion and every
   publish call are made under one `Daemon(ENGINE_HOST,
   requested_by=<requesterUserId>)`. `Ownership` on the report names the
   requester.
5. **The guard is declared.** The completion's guard is a member of the
   generated `producedGuards`. With no stored schema inventory, the outcome is
   `survey-invalid`, never `survey-completed`.
6. **`ReportOriginator` round trip.** After E4, `native_survey_run.EgeriaSurveyPort.report_guid_for_action(engine_action_guid)`
   (the existing reader) returns the report, and `derive_native_state` says
   `complete`.
7. **Restart re-runs.** An action in `active-claimed` with no `runs` row is run
   once. An action with a running row under a live pid is left alone. A
   terminal row with completion unrecorded is completed from the row, and not
   re-run.
8. **Private target.** An action whose target resource has a private owner
   (`private_owner_for_entity` non-empty) is not claimed under option A. The
   words shown depend on Q-O3. The test pins "not claimed" only.
9. **Generated declaration equals registry.** The Dr.Egeria document's request
   type, guards and step key match `DATABASE_STEP_REGISTRY["db_derived"]`.
   Changing one fails the test.
10. **Off means off.** With `RE_ENGINE_HOST_ENABLED` unset, no Egeria call
    is made by the sweep.

### 5.3 Gate (live, the owner's write round)

The owner authors E1's document and triggers E6 on `coco_pharma`. Then:

- The EngineAction reads `COMPLETED`, `completionGuards={0=survey-completed}`,
  `processingEngineUserId` = RE's engine user, `requesterUserId` = the owner.
- `ReportOriginator` links to a `SurveyReport` with `ReportSubject` to
  coco_pharma's database asset, and the annotation count equals the
  `step_runs` row's.
- RE's Understanding shows the run with the same words as a `PostgreSQLSurvey`
  run.
- **Negative:** the same trigger on a database with no stored inventory reads
  `survey-invalid`.
- The engine host's flag set to off, with the trigger repeated: the action
  stays `APPROVED`, which is what native does with no host running.

### 5.4 Not in the slice

- A mixed Egeria process (an RE step chained to a native one; case 2 proper).
- Option B or D.
- Prefect-only steps.
- Steps that open a connection.
- User steps from `trellis-analytics`, which follow once `SurveyStepHost`
  exists.
- Cancel (C10).
- Config refresh (C12) beyond reading the engine GUID at start.

---

## 6. Open questions

Each question is marked with whose it is. None is decided here.

### For the owner

- **Q-O1. The hold.** The 2026-08-17 hold (e4ef1f04) cited native surveys
  failing on template-created assets. Today 10 native database surveys have
  completed. Is the engine-host line of work off hold, and is this slice its
  first step after the Alpha demo, or later?
- **Q-O2. The engine user.** Should RE's engine claim as RE's existing daemon
  account, or as a dedicated engine user like Egeria's per-engine users (§3.5)?
  It needs Mandy's view on zones (Q-M3).
- **Q-O3. Private investigations and an unattended host.** The engine host is
  a daemon. Under #640 and #644 it cannot do private work. The proposal is to
  leave such actions unclaimed, which is the native shape. Should RE also offer
  the owner an attended run as themselves, which is a second path for the same
  action? Or should the screen say "private investigations are not served by
  the engine host"? What should the words be?
- **Q-O4. Requested start in the future.** Native claims first and then waits.
  The proposal is to claim and hold a `runs` row with `not_before`. Is a
  claimed-but-waiting action, visible in Egeria as RE's, acceptable?
- **Q-O5. Logging the pyegeria 404.** `AutomatedCuration.get_engine_actions()`
  calls a route the View Server does not have (§1.3). Log it in
  `PYEGERIA_ISSUES.md`?

### For the Egeria leads (Mandy)

- **Q-M1. The approved listing on the View Server.** The 2026-10-05 per-engine
  `approved-engine-actions` (`GovernanceContextResource.java:89`) is exactly
  what a non-Java host needs. Could `automated-curation` expose it, so an
  external host is not left with the whole-server active list?
- **Q-M2. Engine subtype.** For a Python host whose steps produce survey
  reports and annotations, is `SurveyActionEngine` the right element? And is a
  `GovernanceService` element whose connector provider class nothing can load
  acceptable, or is there a better-shaped declaration for a service Egeria does
  not run? This continues the walking-skeleton brief's §8 question about
  `GovernanceActionType` for a step Egeria does not run.
- **Q-M3. Zones for engine users.** Which zones may an external engine's user
  read and write under the Coco security model? Can an engine user ever be
  granted a person's private zone, or is "the engine cannot see it" the
  intended answer? This continues `DISCUSSION-EGERIA-LEADS-2026-10-05.md` §7.
- **Q-M4. What else makes a host "native".** Does equivalence need anything
  beyond the engine-action protocol? Candidates:
  - connector activity reports;
  - appearing in an engine host summary;
  - honouring `refresh-config`;
  - a guarantee that no Java engine host configuration names RE's engine.

  Is there a supported way to say "this engine is hosted outside Egeria"?
- **Q-M5. Guards.** Should RE's survey steps use `SurveyActionGuard`'s
  `survey-completed`, `survey-invalid` and `survey-failed` only? Or may a step
  declare its own guards (for example from the envelope's MEASURED,
  NOT_ESTABLISHED and NOT_APPLICABLE)? And is `survey-invalid` right for
  "nothing to derive from"?
- **Q-M6. OpenLineage from engine actions.** Egeria's
  `GovernanceActionOpenLineageIntegrationConnector` derives run events from
  engine-action status changes (§4.2).
  - Is it the intended source of lineage for *any* engine host, including an
    external one?
  - Should RE's own R21 events stay out of the way for Egeria-dispatched runs,
    or join them by runId?
  - Is it deployed in the quickstart's Integration Daemon? This was not
    probed.
- **Q-M7. Unknown request type.** What does a native host do with an action
  for its engine whose request type has no service: fail it, or leave it? RE
  should do the same (§3.3, assumption to verify).
- **Q-M8. Stuck claims.** Egeria has no lease or reclaim (C11). If an
  external host's user disappears, its claimed actions stay
  ACTIVATING or IN_PROGRESS forever. Is that accepted for native hosts too, or
  is there an intended operator path, such as a status update by an
  administrator? Separately, claim arbitration is JVM-local (C5): is a
  multi-instance metadata server a supported topology for engine-action
  claiming?

## Owner decisions (2026-10-13)

**Decision (project owner, 2026-10-13):** Q-O1: the 2026-08-17 hold is lifted. The hold was Egeria's "connector is null" failure on template-created assets (egeria-python ISSUE-51). It no longer affects databases (10 native database surveys have completed). Folder surveys are not yet re-verified.

**Decision (project owner, 2026-10-13):** Q-O2: the engine runs as RE's **existing service account** (the `EGERIA_USER_ID` daemon), not a dedicated engine user.

**Decision (project owner, 2026-10-13):** Q-O3: "run as yourself if in private mode". An action on a resource in a private investigation is left unclaimed by the unattended host, and the owner is offered an attended run under their own identity.

**Decision (project owner, 2026-10-13):** Q-O4: yes. RE may claim an action whose start time is in the future and hold it, visibly as RE's in Egeria.

**Q-O5 resolved (2026-10-13):** logged and fixed in egeria-python as ISSUE-151 (d73b162f, PR odpi/egeria-python#452, not yet merged or released). The View Server has no list-all engine-actions endpoint, so `AutomatedCuration.get_engine_actions` is removed in 6.2.3. The slice uses `find_engine_actions("*")` for a list-all (EngineAction is an Asset in 6.2) and `get_active_engine_actions()` for live actions; never `get_engine_actions`.

## Answers from the Egeria leads (Mandy, via the project owner, 2026-10-14)

Recorded from a discussion with the owner. These are the Mandy-note items 19–26 (Q-M1…Q-M8 above).

- **Q-M1 / item 19, approved-actions listing on the View Server: yes.** Mandy will expose more of the internal engine-host APIs as View Server REST calls. It will take a few days; other fixes and features come first. Until then RE polls `get_active_engine_actions` / `find_engine_actions` and filters client-side (option A).
- **Q-M2 / item 20, engine subtype: `SurveyActionEngine` is correct for surveys.** RE may also expose services through a `GovernanceActionEngine` (general actions) or a `WatchdogActionEngine` (monitoring).
  - Refs: https://egeria-project.org/concepts/survey-action-engine/ , https://egeria-project.org/types/4/0461-Governance-Engines/ , https://egeria-project.org/types/4/0462-Governance-Action-Processes/
  - **RE must create:** a service definition for each service, and the `GovernanceActionExecutor` relationship to the engine element. The services of a `SurveyActionEngine` are **`SurveyActionService`** elements (project owner, 2026-10-14). A `GovernanceActionEngine` has `GovernanceActionService`s, and a `WatchdogActionEngine` has its watchdog services.
  - **requestType:** what the engine advertises, and what the engine is passed to execute. RE dispatches on the request type to choose the action to invoke.
  - **Consequence:** the provisional 2c declaration (`docs/dr-egeria/analytic-functions/database_staleness.md`) and egeria-python ISSUE-150 move from "provisional" to this shape.
    - **Done (engine host step 3, 2026-10-14):** the provisional document is retired. Each library step's declaration is generated from its manifest by `scripts/generate_engine_host_declaration.py` into `docs/dr-egeria/engine-host/library/<step>.md` (one SurveyActionService, one SupportedGovernanceService with the manifest's `request_type`, one GovernanceActionType `ResourceExplorerSurvey::<request type>` with the manifest fields as Additional Properties, and its GovernanceActionExecutor). The engine host claims library request types through `survey_step_host.library_steps()` and completes with the manifest's `nothing_to_derive_guard` when the step has nothing to derive.
    - **The step boundary (review, 2026-10-14):** a library step reads RE's stored rows only through RE's typed read methods (`survey_step_host.STEP_ODS_METHODS`, parameterised SQL RE owns; no `query`, no SQL text from the step), run by a separate thread on a connection that cannot write, and its code runs with no Egeria identity. Arbitrary SQL from steps needs a read-only database role: a follow-up for the owner (ops/DDL). Steps remain trusted in-process code; the thread list and the call stack still reach RE internals, so this is not a sandbox.
- **Q-M3 / item 21, zones and identity: up to us.** "Generally a GovernanceProcess or Action would execute as the end user who initiated it. If peterprofile initiates a survey, he is the user." Whether results land in a default (public) zone or not is RE's choice.
  - **Decision (project owner, 2026-10-14):** "run as the end user unless this was an automated action and didn't know who the end user was".
    - It restates an earlier owner decision and agrees with Mandy.
    - Q-O2's "service account" applies only to truly automated work with no known person.
    - Private-investigation work runs as its owner.
    - **Superseded for the engine host, same day.** The mechanism question above no longer arises.
  - **Decision (project owner, 2026-10-14):** "when RE accepts an action or process for execution, it is acting as a daemon - there is no relationship to logged in users."
    - The end-user rule covers work started *inside* RE by a signed-in person (a press, the drain of their write): it runs as that person.
    - Work RE *accepts* from Egeria as an engine host runs as `Daemon(ENGINE_HOST, requested_by=requesterUserId)` under RE's service account (Q-O2). `requesterUserId` is recorded, shown, and stamped as `Ownership`; it is never used as a credential.
    - Private resources: unchanged from Q-O3. The engine host leaves such actions unclaimed; the owner runs attended work from RE.
  - **Native does the same (source check, Egeria main `003f6f65`, 2026-10-14).** Paths under `open-metadata-implementation/`:
    - `GovernanceEngineHandler.java:98-101` sets `engineUserId` from the engine config; `:706-720` claims with it; `:736`, `:814` pass `requesterUserId` only into `runGovernanceService(...)`.
    - `SurveyActionEngineHandler.java:302`, `:316`, `:331`: the AnnotationStore, SurveyAssetStore and SurveyContext (its `connectorUserId`, "userId to use when issuing open metadata requests") are all built with `engineUserId`; `requesterUserId` is a stored field with a getter (`SurveyContext.java:33,112,165`), used nowhere else.
    - `GovernanceServiceHandler.java:155,173,245`: status and completion are written as the engine host.
    - No token or credential for the requester travels with the engine action (`EngineActionElement` carries only the `requesterUserId` string), and there is no on-behalf-of mechanism.
    - Consequence: Egeria's zone and element checks are evaluated against the engine user, so RE's service account needs read/write on the zones, assets and annotation types it touches. Not yet traced: the server-side security connector's call path (expected to use the REST caller's userId).
- **Q-M4 / item 22, being "native":** whatever makes sense. Telling Egeria status and completion of engine actions is what makes RE visible. Refresh-config may make sense. Record where the engine host executes in a description field on the engine element.
- **Q-M5 / item 23, guards:** not mandatory, but best practice is to use the standard guards for consistency. Look for an existing valid-value set for "nothing to derive from"-style outcomes before inventing one.
- **Q-M6 / item 24, lineage:** nothing explicit is needed now. Updating engine-action status is enough, and Egeria reports the lineage. RE's own R21 OpenLineage events stay off for Egeria-dispatched work, with no join for now.
- **Q-M7 / item 25, a request type with no service:** not discussed. RE will test what a native host does and match it.
- **Q-M8 / item 26, claims:** claim arbitration works in a heterogeneous distributed environment today. A cancel *process* will be added alongside cancel engine action. No lease or reclaim was discussed; stuck claims are recovered by cancel.
