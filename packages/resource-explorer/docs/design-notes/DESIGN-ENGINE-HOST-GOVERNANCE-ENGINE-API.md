# Engine host on the Governance Engine view service

*(Status refreshed 2026-10-28: built on main 2026-10-17 (commit `8a647461`, "Governance Engine view service, per-target status, Cancel"). The original status line is kept below.)*

Status: **design note, not built; owner answered Q1–Q3 2026-10-17, Q4 (designer words) open.** Written 2026-10-17 (ops note item 3). The build
goes to one Opus builder; review is the full multi-round tier (it changes an Egeria write path).

Builds on `DESIGN-RE-ENGINE-HOST-BY-EQUIVALENCE.md` (the engine host's contract with Egeria) and
`SLICE-ENGINE-HOST-CASE3-RE-DEFINITIONS-DISPATCHED-BY-EGERIA.md` (RE's own definitions dispatched by Egeria).
Nothing here changes what RE claims, how a step runs, or the guards it completes with.

## 1. Why now

Egeria odpi/egeria#9371 moved the engine-action operations an external engine host uses (claim, status update,
active-claimed listing, completion) from the Automated Curation view service to a new **Governance Engine** view
service (`/api/open-metadata/governance-engine`). It also added the per-engine listing that the equivalence design
waited for (Q-M1): `GET …/governance-engines/{guid}/engine-actions/approved`. pyegeria 6.2.5 (odpi/egeria-python#460,
ISSUE-153) ships a `GovernanceEngine` client for all of it. The quickstart View Server has the service since the
2026-10-16 redeploy, and the live 8-step coordinated Database Analysis Survey passed on it that evening.

RE's engine host (`resource_explorer/engine_host.py`) still:

* **discovers** by reading the whole server's active list (`AutomatedCuration.get_active_engine_actions`) every 5 s
  and filtering client-side (option A, §3.1 of the equivalence design), because no per-engine listing existed;
* **claims, sets IN_PROGRESS, lists its claimed actions and completes** through `AutomatedCuration`'s wrappers. In
  6.2.5 these are deprecated and log a warning on every call.
* reports **no per-target status**: Egeria shows an action target as untouched for the whole run;
* offers **no way to stop** a coordinated run from RE. The definition card can start one (#720) but not cancel it.

## 2. The four changes

### 2.1 Client: `GovernanceEngine` for the five engine-action calls

| Call | Today | After |
|---|---|---|
| claim | `AutomatedCuration.claim_engine_action` | `GovernanceEngine.claim_engine_action` |
| IN_PROGRESS | `AutomatedCuration.update_engine_action_status` | `GovernanceEngine.update_engine_action_status` |
| claimed listing (restart, E5) | `AutomatedCuration.get_active_claimed_engine_actions` | `GovernanceEngine.get_active_claimed_engine_actions` |
| completion | `AutomatedCuration.record_completion_status` | `GovernanceEngine.record_completion_status` |
| discovery | `AutomatedCuration.get_active_engine_actions` | `GovernanceEngine.get_approved_engine_actions` (2.2) |

* `legacy_fallback=False`. RE's floor is already pyegeria 6.2.5 and Egeria main has #9371. A 404 means the View
  Server lacks the service, and RE must say so: log once per sweep reason (`_report_once`), as an unresolved
  engine is reported today. RE must never fall back silently to a URL family it was not built for.
* One client per run as today (`egeria_client(principal, …, shared=False).of(GovernanceEngine)`), closed at run end.
  The sweep's client stays the daemon's shared one.
* `cancel_engine_action` / `cancel_governance_action_process` / `initiate_gov_action_process` did **not** move. They
  stay on `AutomatedCuration`, and #9371 left them there.

### 2.2 Discovery: the approved listing replaces the server-wide scan

`_active_actions()` becomes a paged read of `get_approved_engine_actions(engine_guid)`. Egeria returns only actions
for RE's engine that are APPROVED and unclaimed. The sweep's cost no longer grows with every other engine's
work on the server.

**`_screen` keeps every check it has** (engine GUID, status, claimant, request type, not-yet-due, privacy, previous
step). The server's filter is an optimisation, not the gate. One screening function stays the single source for
what RE claims, so a listing that ever returns more than it should changes nothing.

**Adoption moves wholly to the restart pass.** Today the sweep also adopts an action RE's user has claimed but has
no `runs` row for (a claim that raised after Egeria committed it). An approved-only listing never shows a claimed
action, so the sweep can no longer see these. `restart_pass` already does the same adoption from
`get_active_claimed_engine_actions` (`_restart_one`, `ADOPT`). The visible cost is latency: an orphaned claim is
picked up within `COMPLETION_RETRY_SECONDS` (60 s) instead of within 5 s. That path is rare (a claim that raised),
so 60 s is acceptable. The sweep's adopt branch is removed, not left as dead code.

The sweep interval stays 5 s, matching native.

### 2.3 Progress: IN_PROGRESS on the action, and a status per action target

* The action: `update_engine_action_status(guid, "IN_PROGRESS")` at run start, as today, on the new client.
  Egeria's engine-action status is an enum. There is no percentage, so "progress" for the action means this one
  transition. A failed update is logged and the run goes on, as today.
* **Each action target RE works on** (new): `update_action_target_status(<ActionTarget relationship GUID>, "IN_PROGRESS", start_date=now)` (§4 Q1)
  when the step starts on it, and the final status with `completion_date` and `completion_message` when the step
  ends. The target status is an `ActivityStatus` (`ActionTargetStatusRequestBody.status`), not a `CompletionStatus`,
  so the completion status is mapped (one helper, `engine_host.target_activity_status`): ACTIONED → `COMPLETED`,
  INVALID → `INVALID` (survey-invalid: refused, out of scope, not registered), FAILED → `FAILED`, and a run Egeria
  cancelled → `CANCELLED`. Every completion path sends it (the run, the restart pass's completions, an unrunnable
  queued row), from the stored outcome.
  * Only asset targets RE surveys. The `surveyReport` target (a predecessor's report, step 4) is read, not worked on,
    so its status is left alone.
  * The message comes from the **same stored outcome** the completion is built from (`target.outcome`), never
    re-derived (builder rule 1). One routine renders it for the target status and the completion message.
  * A failed target-status update is logged and recorded on the run row (`target.target_status_errors`). It never
    blocks the completion. The restart pass does **not** retry it: a target status is a courtesy to Egeria's
    viewers, and the action's completion is the record.

### 2.4 Cancel on the definition card

The definition card gains **Cancel** next to the run line while a coordinated run of that definition is running.
* **What it calls:** `AutomatedCuration.cancel_governance_action_process(<process instance GUID>)`, with the instance
  GUID the run stored when RE started it (`initiate_process_run`'s return, the GUID `coordinated_last_runs` already
  matches on). It is offered only for an instance RE started and holds a GUID for. A run started elsewhere
  (another tool, Egeria's own UI) shows no Cancel, for admins too (owner, Q3).
* **Who it runs as:** the signed-in person who presses it (owner rule, 2026-10-14: actions run as the initiating
  user; the service account only when automated with no known user). Who may press it: the run's starter or an
  admin. Anyone else sees no button.
* **What the engine host does with a cancelled action it is running:** the step keeps running to its end. Python
  gives no safe preemption of a step mid-SQL, and native does the same. Before publishing, `_run_with` re-reads the
  action. If Egeria says `CANCELLED`, it publishes no report, sends no completion (Egeria ignores one on a terminal
  action anyway), sets each worked target `INVALID` "cancelled in Egeria", and stores outcome `cancelled`.
* **What the screen says:** the press writes a proof row (who, when, instance GUID, Egeria's answer). The run line
  says "cancelling…" from that row. It says **cancelled** (the existing ⊘ glyph) only when a read of the
  instance's actions shows Egeria recorded `CANCELLED` (status words derive from proof rows). A refused cancel
  (already finished, not permitted) shows Egeria's reason on the line in place and changes nothing else.
* Confirm on press, naming what stops ("Stop this run? Steps already finished keep their reports."). The final
  wording is the designer's.

## 3. Tests the builder must write (beyond the touched files and harness)

1. **End-to-end per write path** (builder rule 2): sweep → claim → run → target statuses → completion, faking only
   the `GovernanceEngine`/`AutomatedCuration` clients and the outbox boundary. The fake records every call, and the
   test asserts the next read (the run row, the run line) shows the expected state. One test each for
   completed, survey-invalid, failed, and cancelled-mid-run.
2. **Fakes bind the real signatures** (`inspect.signature(GovernanceEngine.<method>).bind(...)` in the fake), so a
   renamed keyword fails the test instead of passing through a loose `**kwargs`.
3. **Fail on purpose:** remove the `_screen` engine-GUID check and prove a test fails, even though the fake listing
   only returns RE's engine. This proves the client-side gate is still pinned.
4. **Adoption:** an action claimed by RE's user with no row is adopted by `restart_pass` and never by the sweep.
5. **No AutomatedCuration engine-action calls left:** a test greps `engine_host.py` for the five old method names on
   `AutomatedCuration`, so a regression fails on import paths and not only on a deprecation warning.
6. **Live read-only probe before the PR** (peer round first): `get_approved_engine_actions(<RE engine GUID>)` against
   the quickstart, to confirm the response shape (`elements`, `elementHeader.guid`, `actionTargetElements`).

## 4. Questions and answers

**Decision (project owner, 2026-10-17):** "action_target_guid" (Q1); "run's starter and admin" (Q2); "no" (Q3).

* **Q1. `update_action_target_status` takes `action_target_guid`, and that is the ActionTarget *relationship's*
  GUID, not the target element's.** Egeria source (checkout af400039c2):
  `EngineActionHandler.updateActionTargetStatus` (generic-handlers, line 3872) loads the value with
  `repositoryHandler.getRelationshipByGUID(…, ACTION_TARGET_RELATIONSHIP …)` and sets `activityStatus`, `startTime`
  and `completionTime` on that relationship. Its Javadoc says "unique identifier of the target element", but the
  code reads a relationship. Native's survey engine passes `actionTargetElement.getActionTargetRelationshipGUID()`
  (`SurveyActionEngineHandler`, line 235). RE's `_targets()` reads `actionTargetElements[].actionTargetGUID`, and
  live matching shows that field is the **element** GUID (it resolves to the registered database). So the builder
  must carry the relationship GUID separately. The live read-only probe (§3 test 6) names the JSON field the
  View Server uses for it (framework bean: `actionTargetRelationshipGUID`). If the listing does not carry it, stop
  and report: that is a pyegeria/Egeria gap for the owner, not something to work around.
* **Q2. Cancel: the run's starter and admins only.** Everyone else sees no button.
* **Q3. No Cancel for a coordinated run RE did not start**, admins included.
* **Q4, designer (open).** Words for "cancelling…", the refused-cancel line, and the confirm. The builder uses
  placeholders until they arrive.

## 5. Not in this slice

* Swapping the Analysis/native-survey cancels (`cancel_engine_action` for a native run) — unchanged.
* Per-run elevated credential (design queue, separate note).
* Any change to which request types RE declares or how steps execute.
* Retrying failed target-status updates.

## 6. Live proof after merge (needs the owner's direct word, an Egeria write)

One coordinated Database Scouting Scan on coco_pharma (3 steps, executors declared 2026-10-17). Expected: 3 claims
via the approved listing, IN_PROGRESS on each, target status IN_PROGRESS then COMPLETED or INVALID per step, and
completions. No deprecation warnings in 8813's log for engine-action calls. Then a second run cancelled from the
card mid-step 2. Expected: step 2 publishes nothing, step 3 is never dispatched, and the line reads ⊘ cancelled from
Egeria's read-back.
