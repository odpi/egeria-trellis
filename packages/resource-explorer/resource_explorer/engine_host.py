"""RE as an Egeria engine host: declare, discover and claim (E1, E3), run and complete (E4), restart (E5).

Design: `docs/design-notes/DESIGN-RE-ENGINE-HOST-BY-EQUIVALENCE.md`. RE's Python engine appears to Egeria
as a `SurveyActionEngine` named `ResourceExplorerSurvey`; each admitted RE step is one request type on it.
Egeria creates an engine action for that engine; RE discovers it, claims it, and queues it (step 1). The
queue runs it with `run_engine_action` (E4): IN_PROGRESS, the step through the synthetic-step executor, a
native-shaped SurveyReport (`ReportSubject` + `ReportOriginator`) through the database survey publisher, the
outcome stored on the run row, then `record_completion_status` with a `SurveyActionGuard` guard and a
`surveyReport` action target. The run also writes the native-survey row the Understanding pane lists
(`ResourceExplorerSurvey::re-db-derived`, read back like a PostgreSQLSurvey run). At start, and then every
`COMPLETION_RETRY_SECONDS`, `EngineHost.restart_pass` (E5) queues RE's claimed actions that have no run and
completes, from the stored outcome, those whose run ended without telling Egeria. An action not yet due
(`requestedStartTime` in the future) is left unclaimed until it is.

**Discovery is option A (§3.1).** The View Server has no per-engine "approved" listing yet (Q-M1: Mandy will
add one), so the sweep reads the whole server's active list, `AutomatedCuration.get_active_engine_actions`,
and filters client-side, every `SWEEP_INTERVAL_SECONDS` (5 s, the native engine host's own sweep interval).
`find_engine_actions` was considered and not used: it is a search-string asset search (EngineAction is an
Asset in 6.2) that cannot filter on status or executor, so it would return every terminal action too.
`get_engine_actions` no longer exists (pyegeria 6.2.3, ISSUE-151).

**What is claimed.** An action is claimed only when all of these hold; anything else is left for its owner,
exactly as native leaves it:

* its `governanceEngineGUID` is RE's engine (resolved once by qualified name, `ENGINE_QUALIFIED_NAME`);
* its `actionStatus` is `APPROVED` and its `processingEngineUserId` is empty (Egeria's own claim gate, C5);
* its `requestType` is one RE declares (`supported_request_types()`, the same function the declaration
  generator uses): a built-in step's (`ENGINE_HOST_STEPS`), or an installed Analytic Library step's, read from its
  manifest through `survey_step_host.library_steps()` (step 3). An unknown type, including a library step's whose
  package is not installed here, is left unclaimed and logged once (§3.3; Q-M7 is still open);
* none of its targets is a database with a private owner (`registry.private_owner_for_entity`). The engine
  host is unattended, so it is a daemon; private work never runs as the service account (owner, Q-O3). The
  owner runs such work attended from RE. Claiming and then refusing would write a FAILED action into a
  private context (§3.5).

An action with no target, or with a target that is not a database RE has registered
(`registry.database_slug_for_asset_guid`) and no private one, IS claimed, and then completed `survey-invalid`
with the reason: "not a database registered in Resource Explorer" for a target RE knows as a repo or file
system, else "not registered in Resource Explorer" (owner rulings, 2026-10-14; native claims such an action
too, NO_TARGET_ASSET). An action RE already holds whose target has since gone private is completed once,
survey-invalid, "target is now private to its owner".

**Who RE acts as.** Owner, 2026-10-14: "when RE accepts an action or process for execution, it is acting as a
daemon - there is no relationship to logged in users." Every Egeria call here runs under RE's existing
service account: the listing and the engine lookup as `Daemon(ENGINE_HOST)`, each claim as
`Daemon(ENGINE_HOST, requested_by=requesterUserId)`. `requesterUserId` is recorded on the `runs` row and is
never used as a credential. Native does the same: the engine user claims and writes; the requester is a
stored string (design note, "Native does the same").

**A refused claim is ordinary.** Another host claiming first is `OMAG-GENERIC-HANDLERS-403-003`; it is skipped,
nothing is written and nothing is logged as an error (§3.3 step 2).

**Off means off.** With `RE_ENGINE_HOST_ENABLED` unset or false (the default), the worker starts no loop and
`EngineHost.sweep_once()` returns before any client is built.

**Where E4 hooks in.** Each claim enqueues one `runs` row of kind `engine_action` (`RUN_KIND`) whose
`target` carries everything the handler needs (`_row_target`). `run_queue.HANDLERS["engine_action"]` runs it
as `Daemon(ENGINE_HOST, requested_by=...)` (`run_queue._run_as_engine_host`), not `_run_as_requester`'s
`RUN_QUEUE`. Such a row is never cancellable from RE (`run_queue.UNCANCELLABLE_KINDS`).

**Library steps (step 3).** A library request type runs its step through the same synthetic-step executor (the
step host, `survey_step_host.SurveyStepHost`, stores its annotations in `step_annotations`), and the report carries
those annotations (`LibraryStep.report_annotations`) through the same `_publish`. When none of its results is a
derivation, the run completes with the guard the manifest declares for nothing to derive; with a guard other than
survey-completed no report is written (`NothingToPublish`), as db_derived writes none when it is skipped.

**Mixed processes (step 4, `mixed_process.py`).** An action Egeria creates as a later step of a process (a native
survey, then RE's step) carries the previous step's report as the `surveyReport` action target. That target is never
an asset. A library step that declares the `source_report` input is handed the report as plain data, read here, and
records `derived_from_report`. Without the target the step runs standalone and completes survey-completed, its
report-based measure recorded as "not measured · no survey report in this run"; survey-invalid is only for a
surveyReport that is present and wrong (another subject, not the predecessor's, or more than one), naming which. An action whose previous step did not complete with the linking guard is left unclaimed.

**RE's own Survey Definitions (case 3, SLICE-ENGINE-HOST-CASE3-RE-DEFINITIONS-DISPATCHED-BY-EGERIA.md).** Every step
key of RE's step registries is a request type (`registry_steps`, `re-<step key>`), so Egeria can run a published
definition one engine action per step. A database step runs through the same synthetic-step executor with the
database registration's stored credential, after the launcher's capability gate (a shortfall completes
survey-invalid with the launcher's own reason; nothing runs); a repository step is claimed and completed survey-invalid
naming why (`out_of_scope_reason`). A definition's step completes with `step_outcome.completion_guards`: its survey
guard, plus the `Any` its authored next-step links carry, whatever the outcome (Egeria matches link guards
exactly; RE's executor follows `Any` always); when no authored link takes the guards, the run says "process ended
after <step>: guard <g> has no next step". A second action for the same step of the same run (duplicate next-step
links) is completed survey-invalid without running (`TRIGGERED_TWICE_SENTENCE`). The `surveyReport` targets Egeria carries forward are not a definition step's input. One native-survey row is
written per process instance (`_record_submission`), read back as one run line (`mixed_process.definition_process`).
Every engine-action row records `started_by: egeria`; RE's own Run records `re`.

**Completion only from proof.** The outcome (guard, status, message, report GUID, annotation count) is
written to the row's `target.outcome` before Egeria is told; `target.completion_recorded_at` marks that
Egeria accepted it. No DDL: both ride in the existing `runs.target` JSON.
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
from dataclasses import dataclass, field
from datetime import UTC
from typing import Any

from resource_explorer.step_outcome import SURVEY_GUARDS, TRIGGERED_TWICE_SENTENCE

log = logging.getLogger(__name__)

#: The engine element (C1). Native engines are named by a plain qualified name (`PostgreSQLSurvey`).
ENGINE_QUALIFIED_NAME = "ResourceExplorerSurvey"
ENGINE_DISPLAY_NAME = "Resource Explorer Survey Engine"
ENGINE_TYPE_NAME = "SurveyActionEngine"           # Q-M2: right for surveys
SERVICE_TYPE_NAME = "SurveyActionService"         # owner, 2026-10-14: a SurveyActionEngine's services

#: `SurveyActionGuard`'s standard guards (C9). Mandy (Q-M5): use the standard guards.
#: Defined once in `step_outcome` (case 3: the guard mapping lives beside them) and re-exported here.
SURVEY_COMPLETED, SURVEY_INVALID, SURVEY_FAILED = SURVEY_GUARDS

#: Each guard's completion status, as `SurveyActionGuard` pairs them (`SurveyActionGuard.java:18-20`):
#: ACTIONED, INVALID, FAILED (Egeria's `CompletionStatus` names, as `record_completion_status` takes them).
GUARD_STATUS = {SURVEY_COMPLETED: "ACTIONED", SURVEY_INVALID: "INVALID", SURVEY_FAILED: "FAILED"}

#: The action target a survey's completion adds, carrying the report GUID (`SurveyActionTarget.SURVEY_REPORT`,
#: `SurveyActionServiceHandler.java`): native always adds it; RE adds it whenever a report exists.
SURVEY_REPORT_TARGET = "surveyReport"

#: Owner rulings, 2026-10-14: an action whose target is not a database RE has registered is claimed and
#: completed `survey-invalid` with this reason ("the supplied information (such as the asset) is invalid for
#: this type of survey", `SurveyActionGuard.SURVEY_INVALID`). A target RE knows as another kind of resource
#: says so instead.
NOT_REGISTERED_SENTENCE = "not registered in Resource Explorer"
NOT_A_DATABASE_SENTENCE = "not a database registered in Resource Explorer"

#: Case 3 (H1): the same process step started twice in one process run (duplicate NextGovernanceActionProcessStep
#: links each fire). The second is completed survey-invalid with this (`step_outcome.TRIGGERED_TWICE_SENTENCE`,
#: imported above), never run, and with no routing guard.

#: An action RE already claimed whose target has since joined a private investigation: completed once,
#: survey-invalid, never run as the service account (owner, Q-O3).
NOW_PRIVATE_SENTENCE = "target is now private to its owner"

#: How long an `engine_action` row may stay queued for a request type no worker here can run (an Analytic Library
#: step whose package is not installed where the queue runs) before the restart pass completes it survey-failed.
#: Workers that cannot run it leave it queued (`run_queue._engine_request_types_here`).
#: An hour, from `enqueued_at`, judged by the leader's own request types: RE keeps no per-worker record of which
#: request types each live worker can run (that would need a table), so the bound is long enough for a worker
#: with the package to be started, and short enough that Egeria is not left holding a claimed action for a day.
UNRUNNABLE_QUEUED_SECONDS = 3600

#: How often the running engine host repeats the completion pass (`restart_pass`, idempotent): a completion
#: that timed out, or a run whose worker died, is told to Egeria within this long, not only at the next start.
COMPLETION_RETRY_SECONDS = 60

#: Native's words for an action with no asset target (`SurveyActionErrorCode.NO_TARGET_ASSET`,
#: OMES-SURVEY-ACTION-400-003). Native claims such an action and then only writes its audit log, leaving
#: it claimed for ever (`GovernanceEngineHandler.executeEngineAction`'s catch); RE completes it instead.
NO_TARGET_SENTENCE = ("no asset action target was supplied, so the survey was not run "
                      "(native: OMES-SURVEY-ACTION-400-003 NO_TARGET_ASSET)")

#: Engine host step 4 (SLICE-ENGINE-HOST-STEP4-MIXED-PROCESS.md): a library step that declares the `source_report`
#: input derives from the report the previous process step wrote, which Egeria hands it as the `surveyReport` action
#: target. Without that target the step runs standalone (design ruling, 2026-10-15) and records the absence of the
#: report-based measure; survey-invalid is only for a report that is present and wrong.

#: The input a library step's manifest declares to be handed the previous step's report (trellis_analytics).
SOURCE_REPORT_INPUT = "source_report"

#: The RE steps the engine's own declaration document declares (engine-host/resource-explorer-survey-engine.md).
#: One zero-fetch step in the first slice (§5). Since case 3 every other registry step is declared by
#: engine-host/steps/ (`registry_steps`), so a step is never declared twice: re-running a document repeats its
#: "Link Action to Action Executor", which is not safe to repeat.
ENGINE_HOST_STEPS = ("db_derived",)

#: The resource kind every admitted step targets (all are database steps today).
ENTITY_TYPE = "database"

#: The `runs` kind a claim enqueues (registry.RUN_KINDS).
RUN_KIND = "engine_action"

#: Case 3, decision 5: who coordinates a run, recorded on its `runs` row's target (and on a process run's native
#: survey row, `declared.started_by`). "egeria": Egeria walks the process and RE runs the steps it is handed;
#: "re": RE's own executor runs the definition and creates no engine action.
STARTED_BY_EGERIA = "egeria"
STARTED_BY_RE = "re"

ENABLE_ENV = "RE_ENGINE_HOST_ENABLED"

#: The native engine host's sweep interval (`EngineConfigurationRefreshThread.java:51`, 5000 ms).
SWEEP_INTERVAL_SECONDS = 5

#: How long `EngineHostLoop.stop()` waits for an in-flight sweep.
STOP_JOIN_SECONDS = 10.0

#: Page size for the active-actions listing (native pages 10 at a time; a larger page is fewer round trips).
PAGE_SIZE = 100

#: Egeria's "another claimant already holds this action" (C5; live-confirmed in ISSUE-78).
CLAIM_REFUSED_MESSAGE_ID = "OMAG-GENERIC-HANDLERS-403-003"

_GUID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")


def engine_host_enabled() -> bool:
    """`RE_ENGINE_HOST_ENABLED` is on. Off (the default) means no loop and no Egeria call."""
    raw = os.environ.get(ENABLE_ENV)
    if raw is None:
        return False
    return raw.strip().lower() in ("1", "true", "yes", "on")


# ── the declared names: ONE source for the generator (E1) and the sweep (E3) ──

def request_type_for_step(step_key: str) -> str:
    """`db_derived` -> `re-db-derived`: the request type RE's engine advertises for a step."""
    return "re-" + step_key.replace("_", "-")


def governance_action_type_qn(step_key: str) -> str:
    """`ResourceExplorerSurvey::re-db-derived`, the shape of native `PostgreSQLSurvey::survey-postgres-database`."""
    return governance_action_type_qn_for(request_type_for_step(step_key))


def governance_action_type_qn_for(request_type: str) -> str:
    """The GovernanceActionType of a request type: `ResourceExplorerSurvey::<request type>`. A library step declares
    its request type in its manifest, so its type is named from that, never re-derived from the step name."""
    return f"{ENGINE_QUALIFIED_NAME}::{request_type}"


def service_qualified_name(step_key: str) -> str:
    return f"{SERVICE_TYPE_NAME}::{ENGINE_QUALIFIED_NAME}::{step_key}"


def declared_steps() -> dict:
    """{step_key: StepInfo} for every admitted step, read from DATABASE_STEP_REGISTRY. A step named in
    ENGINE_HOST_STEPS but missing from the registry is a KeyError, not a silent omission."""
    from resource_explorer.surveyors.database.survey_definition_adapter import (
        DATABASE_STEP_REGISTRY,
    )

    return {key: DATABASE_STEP_REGISTRY[key] for key in ENGINE_HOST_STEPS}


#: Engine host case 3 (SLICE-ENGINE-HOST-CASE3-RE-DEFINITIONS-DISPATCHED-BY-EGERIA.md, decision 1): one
#: SurveyActionService per STEP KEY of RE's step registries, never per definition. A step shared by five definitions
#: is one request type; the definition is the process, the step is the service.
REGISTRY_ENTITY_TYPES = ("database", "repo")

#: Repository steps whose fetch or compute is out of this slice (§5.4): "steps fetch sources or call models".
_HEAVY_FETCH = frozenset({"api_heavy", "download"})
_HEAVY_COMPUTE = frozenset({"high"})


@dataclass(frozen=True)
class RegistryStep:
    step_key: str
    entity_type: str            # "database" | "repo"
    info: Any                   # the registry's StepInfo

    @property
    def request_type(self) -> str:
        return request_type_for_step(self.step_key)


def registry_steps() -> dict[str, RegistryStep]:
    """{step_key: RegistryStep} for every BUILT-IN key of DATABASE_STEP_REGISTRY and the repository STEP_REGISTRY, in
    that order. Analytic Library steps `survey_step_host.attach` added to a registry are left out: they are declared
    and claimed under their manifests' own request types (`library_steps`). A key in both registries is a ValueError:
    one request type cannot dispatch to two steps."""
    from resource_explorer.surveyors.database.survey_definition_adapter import (
        DATABASE_STEP_REGISTRY,
    )
    from resource_explorer.surveyors.repo_survey_definition_adapter import STEP_REGISTRY
    from resource_explorer.surveyors.survey_step_host import attached_user_steps

    out: dict[str, RegistryStep] = {}
    for entity_type, registry in (("database", DATABASE_STEP_REGISTRY), ("repo", STEP_REGISTRY)):
        user = attached_user_steps(entity_type)
        for key, info in registry.items():
            if key in user:
                continue
            if key in out:
                raise ValueError(f"step key {key!r} is in both the {out[key].entity_type} and the {entity_type} "
                                 "step registries; its request type would dispatch to two steps")
            out[key] = RegistryStep(key, entity_type, info)
    return out


def out_of_scope_reason(step_key: str) -> str:
    """Why RE's engine host does not run this registry step when Egeria dispatches it, or "" when it does. The
    action is still claimed and then completed survey-invalid with this sentence (owner, 2026-10-15: "dispatch of an
    out-of-scope step must complete survey-invalid with a sentence naming why, not run").

    Database steps run. Repository steps do not yet: those that fetch sources or call models are out of the slice
    (§5.4: the engine host's worker has no fetch budget like the queue's), and the rest because the engine host
    resolves and publishes database targets only so far (its report path is the database survey publisher)."""
    step = registry_steps().get(step_key)
    if step is None or step.entity_type == "database":
        return ""
    fetch = getattr(step.info, "fetch_cost", "none") or "none"
    compute = getattr(step.info, "compute_cost", "low") or "low"
    if fetch in _HEAVY_FETCH or compute in _HEAVY_COMPUTE:
        return (f"repository step {step_key} fetches sources or calls models (fetch cost {fetch}, compute cost "
                f"{compute}); Resource Explorer's engine host does not run such steps yet. Run its definition "
                "from Resource Explorer's own Run")
    return (f"repository step {step_key} is not run by Resource Explorer's engine host yet: it runs database steps "
            "only so far. Run its definition from Resource Explorer's own Run")


def library_steps() -> dict:
    """{request type: survey_step_host.LibraryStep} for the installed Analytic Library steps RE's engine runs."""
    from resource_explorer.surveyors.survey_step_host import library_steps as _library_steps

    return _library_steps(ENTITY_TYPE)


def supported_request_types() -> dict[str, str]:
    """{request type: step key} RE's engine can run: the admitted built-in steps (`ENGINE_HOST_STEPS`), then the
    installed library steps. A library step can take neither the name nor the `re-<name>` request type of ANY
    built-in step in DATABASE_STEP_REGISTRY (admitted to the engine host or not): `survey_step_host._admit`
    refuses it, with the reason in `library_step_refusals()`."""
    out = {request_type_for_step(key): key for key in declared_steps()}
    # Case 3: every registry step is a request type RE's engine claims; an out-of-scope one is then completed
    # survey-invalid with its reason (`out_of_scope_reason`), never left unclaimed as unknown.
    for key in registry_steps():
        out.setdefault(request_type_for_step(key), key)
    for request_type, step in library_steps().items():
        out.setdefault(request_type, step.step_key)
    return out


def is_claim_refused(exc: BaseException) -> bool:
    """Egeria refused a claim because another claimant holds the action (403-003): an ordinary outcome."""
    for e in (exc, getattr(exc, "__cause__", None)):
        if e is None:
            continue
        info = getattr(e, "additional_info", None) or {}
        ids = (str(info.get("exceptionErrorMessageId") or ""), str(getattr(e, "response_egeria_msg_id", "") or ""))
        if CLAIM_REFUSED_MESSAGE_ID in ids or CLAIM_REFUSED_MESSAGE_ID in str(e):
            return True
    return False


# ── reading one EngineActionElement ────────────────────────────────────────────

def _daemon_user() -> str:
    """RE's service-account userId: the processingEngineUserId Egeria records on RE's claims."""
    from resource_explorer.egeria_clients import _daemon_credential

    return (_daemon_credential()[0] or "").strip()


def _guid(action: dict) -> str:
    return str(((action or {}).get("elementHeader") or {}).get("guid") or "")


def _status(action: dict) -> str:
    status = action.get("actionStatus")
    if isinstance(status, dict):          # tolerate an enum serialised as an object
        status = status.get("name") or status.get("symbolicName") or ""
    return str(status or "").upper()


def _targets(action: dict) -> list[dict]:
    out = []
    for t in action.get("actionTargetElements") or []:
        if isinstance(t, dict) and t.get("actionTargetGUID"):
            out.append({"name": str(t.get("actionTargetName") or ""), "guid": str(t["actionTargetGUID"])})
    return out


@dataclass
class SweepResult:
    """What one sweep did. `skipped` maps a reason to the action GUIDs left unclaimed for it."""

    claimed: list[str] = field(default_factory=list)
    adopted: list[str] = field(default_factory=list)
    refused: list[str] = field(default_factory=list)
    skipped: dict[str, list[str]] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    run_ids: list[str] = field(default_factory=list)
    note: str = ""

    def skip(self, reason: str, guid: str) -> None:
        self.skipped.setdefault(reason, []).append(guid)


@dataclass
class RestartResult:
    """What one restart pass did, by engine action GUID."""

    adopted: list[str] = field(default_factory=list)
    left_running: list[str] = field(default_factory=list)
    pending: list[str] = field(default_factory=list)
    completed: list[str] = field(default_factory=list)
    #: Completed in Egeria, but the run row's marker could not be written.
    unmarked: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    note: str = ""


#: An action of RE's whose previous process step did not complete with the guard that links them: Egeria should never
#: create it, so RE refuses it (left unclaimed, logged once; SLICE step 4, §5.2 test 4).
PREVIOUS_STEP_NOT_COMPLETED = "previous_step_not_completed"

#: Skip reasons that are not RE's business (another engine's work, not yet approved, claimed by someone
#: else, or already queued here): silent. The rest are logged once per action.
_SILENT = frozenset({"other_engine", "not_approved", "already_claimed", "already_queued", "not_due",
                     "completed_now_private"})

#: The `_screen` verdict for an action RE's own user already claimed in Egeria but RE has no runs row for:
#: queue it without claiming again.
ADOPT = "adopt"


class EngineHost:
    """One engine host: resolves RE's engine and sweeps for work to claim.

    Belongs to ONE thread. It owns an event loop for that thread, as `native_survey_run.EgeriaSurveyPort`
    does, because a pyegeria client binds its HTTP pool to the loop that first drives it (ISSUE-96); and it
    keeps one `EgeriaClients` per identity for its life so a 5-second sweep reuses the daemon token instead
    of minting one per pass."""

    def __init__(self, registry=None) -> None:
        self._registry = registry
        self._engine_guid: str | None = None
        self._clients: dict[tuple, Any] = {}
        self._reported: set[tuple[str, str]] = set()
        self._loop = None

    # -- plumbing --------------------------------------------------------------

    @property
    def registry(self):
        if self._registry is None:
            from resource_explorer.registry import ProjectRegistry

            self._registry = ProjectRegistry()
        return self._registry

    def _enter(self) -> None:
        import asyncio

        if self._loop is None:
            self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)

    def close(self) -> None:
        import asyncio

        if self._loop is not None:
            asyncio.set_event_loop(self._loop)
        for clients in self._clients.values():
            # pyegeria's own close (`close_session`, base server client), on this host's loop, before it closes.
            for client in list(getattr(clients, "_clients", {}).values()):
                close = getattr(client, "close_session", None)
                if callable(close):
                    try:
                        close()
                    except Exception:  # noqa: BLE001 - tidy-up, never the answer
                        pass
        self._clients.clear()
        if self._loop is not None:
            self._loop.close()
            self._loop = None
            asyncio.set_event_loop(None)

    def _client(self, cls):
        """The pyegeria client of class `cls` for the identity this code is acting as."""
        from resource_explorer.egeria_clients import current_principal, egeria_client, identity_key

        identity = current_principal()
        key = identity_key(identity)
        clients = self._clients.get(key)
        if clients is None:
            # Not scope-shared: these clients are bound to this host's own event loop.
            clients = egeria_client(identity, purpose="engine host", shared=False)
            self._clients[key] = clients
        return clients.of(cls)

    def _report_once(self, reason: str, guid: str, message: str, *args, level: int = logging.INFO) -> None:
        if (reason, guid) in self._reported:
            return
        self._reported.add((reason, guid))
        log.log(level, message, *args)

    # -- the engine ------------------------------------------------------------

    def resolve_engine_guid(self) -> str | None:
        """RE's engine GUID, by its qualified name. None when Egeria has no such element (the owner has not
        run the declaration yet) or the answer was not a GUID. Cached once found (config refresh, C12, is not
        in this slice)."""
        if self._engine_guid:
            return self._engine_guid
        from pyegeria import MetadataExpert

        answer = self._client(MetadataExpert).get_metadata_guid_by_unique_name(ENGINE_QUALIFIED_NAME)
        if isinstance(answer, str) and _GUID_RE.match(answer.strip()):
            self._engine_guid = answer.strip()
            log.info("engine host: engine %s is %s", ENGINE_QUALIFIED_NAME, self._engine_guid)
            return self._engine_guid
        self._report_once("no_engine", ENGINE_QUALIFIED_NAME,
                          "engine host: no engine %r in Egeria (answer %r); nothing is claimed until the "
                          "owner runs docs/dr-egeria/engine-host/", ENGINE_QUALIFIED_NAME,
                          " ".join(str(answer).split())[:120], level=logging.WARNING)
        return None

    # -- the sweep -------------------------------------------------------------

    def _active_actions(self) -> list[dict]:
        from pyegeria import AutomatedCuration

        curation = self._client(AutomatedCuration)
        out: list[dict] = []
        start = 0
        while True:
            page = curation.get_active_engine_actions(start_from=start, page_size=PAGE_SIZE)
            if isinstance(page, str):         # pyegeria's "No elements found": the end of the list
                return out
            if not isinstance(page, list):
                raise RuntimeError(f"Egeria answered the active engine-action listing with {type(page).__name__}")
            out.extend(a for a in page if isinstance(a, dict))
            if len(page) < PAGE_SIZE:
                return out
            start += PAGE_SIZE

    def _screen(self, action: dict, engine_guid: str, supported: dict[str, str]) -> tuple[str, dict]:
        """(reason, facts): reason "" means claim it. Facts carry what the runs row needs."""
        if str(action.get("governanceEngineGUID") or "") != engine_guid:
            return "other_engine", {}
        claimant = str(action.get("processingEngineUserId") or "").strip()
        adopt = False
        if claimant:
            # Ours with no row: a claim that raised after Egeria committed it, or whose row could not be
            # written. Anyone else's claim (another engine host, a person) is never ours to take.
            if claimant.casefold() != _daemon_user().casefold():
                return "already_claimed", {}
            if self.registry.engine_action_run_exists(_guid(action)):
                return "already_queued", {}
            adopt = True
        elif _status(action) != "APPROVED":
            return "not_approved", {}
        elif _not_yet_due(action):
            # Left unclaimed until its requested start time (native claims and then waits, C6; RE does not
            # hold a claimed action it cannot start yet).
            return "not_due", {}
        request_type = str(action.get("requestType") or "")
        step_key = supported.get(request_type)
        if not step_key:
            return "unknown_request_type", {"request_type": request_type}
        all_targets = _targets(action)
        # The previous step's report (engine host step 4): carried by Egeria as the `surveyReport` target, never an
        # asset to survey. Every other target is an asset, whatever name the process was started with.
        reports = [t["guid"] for t in all_targets if t["name"] == SURVEY_REPORT_TARGET]
        # Egeria hands a later process step the previous action's targets AND the completion's new ones
        # (`EngineActionHandler.prepareEngineActionFromProcessStep`), so the same asset can arrive twice: one asset.
        targets = _unique_targets([t for t in all_targets if t["name"] != SURVEY_REPORT_TARGET])
        # A later step of a process runs only after its previous step completed with the linking guard, and the
        # report it is handed must be that previous step's own (ReportOriginator).
        from resource_explorer import mixed_process

        why, predecessor_reports, instance = self._previous_step(_guid(action),
                                                                 str(action.get("requesterUserId") or "").strip())
        if why and not adopt:
            return PREVIOUS_STEP_NOT_COMPLETED, {"request_type": request_type, "why": why}
        report_refusal = ""
        # Only a step that derives from the previous step's report is handed one, so only such a step can be handed
        # a wrong one. A step of an RE definition (case 3) takes no report: in an N-step process Egeria carries every
        # earlier step's surveyReport forward (step 3 arrives with two), and they are not its input.
        # Whether a step consumes a report depends on the PROCESS it runs in, not only its request type: inside a
        # declared RE Survey Definition no step takes one, so it runs standalone (owner, 2026-10-16). If the process
        # cannot be told, the request type alone decides, as before (ambiguity is refused, never silently dropped).
        in_definition = bool(mixed_process.definition_qn_for_instance_name(str(action.get("processName") or "")))
        if _consumes_source_report(request_type, step_key) and not in_definition:
            if len(set(reports)) > 1:
                report_refusal = (f"{len(set(reports))} {SURVEY_REPORT_TARGET} action targets "
                                  f"({', '.join(sorted(set(reports)))}); not choosing one")
            elif reports and predecessor_reports is not None and reports[0] not in predecessor_reports:
                report_refusal = (f"{SURVEY_REPORT_TARGET} {reports[0]} is not the report of the previous process "
                                  f"step ({', '.join(sorted(predecessor_reports)) or 'it linked none'})")
        else:
            reports = []
        slugs = [self.registry.database_slug_for_asset_guid(t["guid"]) for t in targets]
        # Private first: if ANY target is a resource in a private investigation the action is left unclaimed,
        # whatever else it names (owner, Q-O3; claiming and refusing would write a FAILED action into a
        # private context, §3.5). A repository target counts too (case 3 claims repository request types).
        for t, slug in zip(targets, slugs):
            kind, private_slug = (ENTITY_TYPE, slug) if slug else ("repo", self._repo_slug(t["guid"]))
            if private_slug and self.registry.private_owner_for_entity(kind, private_slug):
                # `ours`: RE already holds the claim (it was claimed before the target went private).
                return "private_target", {"slug": private_slug, "ours": adopt}
        facts = {"request_type": request_type, "step_key": step_key, "targets": all_targets, "slug": "",
                 "asset_guid": "", "refusal": "", "other_targets": [], "source_report": reports[0] if reports else "",
                 # Case 3: which process step of which RE definition this action is (`_process_facts`), and the
                 # guards its authored next-step links take (None when it is no definition's step).
                 **self._process_facts(action, instance)}
        scope = out_of_scope_reason(step_key) if step_key in registry_steps() else ""
        twice = self._triggered_twice(_guid(action), facts)
        if twice:
            # Case 3 (H1): Egeria started this step a second time in the same run (a duplicate next-step link). It is
            # claimed and completed survey-invalid without running; the first one ran.
            facts["refusal"] = twice
            facts["duplicate_trigger"] = True
        elif report_refusal:
            # Claimed, then completed survey-invalid: the supplied information is invalid for this survey.
            facts["refusal"] = report_refusal
        elif scope:
            # Claimed, then completed survey-invalid naming why (owner, 2026-10-15); never run.
            facts["refusal"] = scope
        elif not targets:
            facts["refusal"] = NO_TARGET_SENTENCE
        elif not all(slugs):
            # Owner rulings, 2026-10-14: claimed, then completed survey-invalid with the reason (step 1 left it
            # unclaimed). An unregistered target cannot be private in RE (privacy is a registered resource's).
            words = []
            for t, slug in zip(targets, slugs):
                if not slug:
                    kind = self.registry.resource_kind_for_asset_guid(t["guid"])
                    words.append(f"{t['guid']}: " + (f"{NOT_A_DATABASE_SENTENCE} (it is registered as a {kind})"
                                                     if kind else NOT_REGISTERED_SENTENCE))
            facts["refusal"] = "; ".join(words)
        else:
            facts["slug"], facts["asset_guid"] = slugs[0], targets[0]["guid"]
            # Several database targets: the first is surveyed and the completion message names the rest.
            facts["other_targets"] = [t["guid"] for t in targets[1:]]
        return (ADOPT if adopt else ""), facts

    def _previous_step(self, guid: str, requester: str) -> tuple[str, set[str] | None, str]:
        """(why this action must not run yet as a later process step or "", the reports its previous steps linked by
        ReportOriginator, or None when it has no previous step, the GovernanceActionProcessInstance it is anchored to
        or ""). Read as the engine host for the action's requester, as the claim is made. Raises when unreadable (the
        sweep fails closed)."""
        from pyegeria import MetadataExpert

        from resource_explorer import mixed_process
        from resource_explorer.egeria_clients import Daemon, DaemonReason, acting_as

        with acting_as(Daemon(DaemonReason.ENGINE_HOST, requested_by=requester or None)):
            expert = self._client(MetadataExpert)
            view = mixed_process.read_action_graph(expert, guid)
            if not view.previous:
                return "", None, view.instance_guid
            reports: set[str] = set()
            for prev in view.previous:
                reports.update(mixed_process.read_action_graph(expert, prev.guid).report_guids)
        return mixed_process.previous_step_refusal(view), reports, view.instance_guid

    def _triggered_twice(self, guid: str, facts: dict) -> str:
        """Why this action is a second start of a step RE already holds in the same process run, or "". Keyed on the
        process instance and process step, never on the request type: one step key in two definitions, or in two
        runs, is two steps."""
        if not facts.get("definition"):
            return ""
        lookup = getattr(self.registry, "engine_action_runs_for_process_step", None)
        if not callable(lookup):
            return ""
        first = [r for r in lookup(facts.get("process_instance_guid", ""), facts.get("process_step_guid", ""))
                 if _row_target_json(r).get("engine_action_guid") != guid]
        if not first:
            return ""
        return (f"{TRIGGERED_TWICE_SENTENCE} (engine action "
                f"{_row_target_json(first[0]).get('engine_action_guid') or first[0].get('id')})")

    def _repo_slug(self, guid: str) -> str:
        """The registered repository whose Egeria Asset is `guid`, or "" (a registry without the lookup: "")."""
        lookup = getattr(self.registry, "repo_slug_for_asset_guid", None)
        return lookup(guid) if callable(lookup) else ""

    def _process_facts(self, action: dict, instance: str) -> dict:
        """Case 3: which step of which RE Survey Definition this action is, read from the action itself
        (`processName`, `processStepGUID`, `processStepName`) and, for a definition's step, the guards its authored
        next-step links take, read live from the step (`mixed_process.read_next_step_guards`). `next_guards` is None
        for an action that is no RE definition's step, or whose links could not be read (then the run cannot say
        whether its completion continues the process, and says so)."""
        from resource_explorer import mixed_process

        process_name = str(action.get("processName") or "")
        step_guid = str(action.get("processStepGUID") or "")
        definition = mixed_process.definition_qn_for_instance_name(process_name)
        facts = {"process_name": process_name, "process_step_guid": step_guid,
                 "process_step_name": str(action.get("processStepName") or ""),
                 "definition": definition, "process_instance_guid": instance or "",
                 "next_guards": None, "next_guards_error": ""}
        if not definition or not step_guid:
            return facts
        from pyegeria import MetadataExpert

        from resource_explorer.egeria_clients import Daemon, DaemonReason, acting_as

        requester = str(action.get("requesterUserId") or "").strip()
        try:
            with acting_as(Daemon(DaemonReason.ENGINE_HOST, requested_by=requester or None)):
                facts["next_guards"] = mixed_process.read_next_step_guards(self._client(MetadataExpert), step_guid)
        except Exception as exc:  # noqa: BLE001 -- recorded: the run then says it could not tell, never guesses
            facts["next_guards_error"] = _short(exc, 200)
        return facts

    def sweep_once(self) -> SweepResult:
        """One pass: list, filter, claim, enqueue. Never raises for an Egeria answer; returns what it did."""
        result = SweepResult()
        if not engine_host_enabled():
            result.note = f"{ENABLE_ENV} is off"
            return result
        from resource_explorer.egeria_clients import Daemon, DaemonReason, acting_as

        self._enter()
        supported = supported_request_types()
        with acting_as(Daemon(DaemonReason.ENGINE_HOST)):
            engine_guid = self.resolve_engine_guid()
            if not engine_guid:
                result.note = "engine not declared in Egeria"
                return result
            actions = self._active_actions()

        for action in actions:
            guid = _guid(action)
            if not guid:
                continue
            try:
                reason, facts = self._screen(action, engine_guid, supported)
            except Exception as exc:  # noqa: BLE001 -- fail closed: an unreadable check claims nothing
                reason, facts = "screen_failed", {"error": f"{type(exc).__name__}: {exc}"}
            if reason == ADOPT:
                self._enqueue(action, guid, facts, engine_guid, result, adopted=True)
                continue
            if reason == "private_target" and facts.get("ours"):
                recorded, _error = self._complete_now_private(action, guid)
                result.skip("completed_now_private" if recorded else "private_target", guid)
                continue
            if reason:
                result.skip(reason, guid)
                if reason not in _SILENT:
                    self._report_once(reason, guid, "engine host: left engine action %s unclaimed (%s: %s)",
                                      guid, reason, facts, level=logging.WARNING
                                      if reason in ("unknown_request_type", "screen_failed") else logging.INFO)
                continue
            self._claim(action, guid, facts, engine_guid, result)
        return result

    # -- E5: the restart pass (§3.4) ------------------------------------------------

    def _claimed_actions(self, engine_guid: str) -> list[dict]:
        """The actions RE's user has claimed for RE's engine that Egeria still holds active (WAITING,
        ACTIVATING, IN_PROGRESS): native's restart listing (C11), the one per-engine listing the View Server
        has."""
        from pyegeria import AutomatedCuration

        curation = self._client(AutomatedCuration)
        out: list[dict] = []
        start = 0
        while True:
            page = curation.get_active_claimed_engine_actions(engine_guid, start_from=start, page_size=PAGE_SIZE)
            if isinstance(page, str):         # pyegeria's "No elements found": the end of the list
                return out
            if not isinstance(page, list):
                raise RuntimeError("Egeria answered the claimed engine-action listing with "
                                   f"{type(page).__name__}")
            out.extend(a for a in page if isinstance(a, dict))
            if len(page) < PAGE_SIZE:
                return out
            start += PAGE_SIZE

    def _complete_now_private(self, action: dict, guid: str) -> tuple[bool, str]:
        """An action RE already claimed whose target is now in a private investigation: completed once,
        survey-invalid, as the engine host for its requester; nothing is run. Egeria then stops listing it."""
        from pyegeria import AutomatedCuration

        from resource_explorer.egeria_clients import Daemon, DaemonReason, acting_as

        requester = str(action.get("requesterUserId") or "").strip()
        outcome = _outcome(SURVEY_INVALID, f"Resource Explorer did not run {action.get('requestType')}: "
                           f"{NOW_PRIVATE_SENTENCE}")
        with acting_as(Daemon(DaemonReason.ENGINE_HOST, requested_by=requester or None)):
            recorded, error = _record_completion(self._client(AutomatedCuration), guid, outcome)
        if recorded:
            log.info("engine host: completed engine action %s survey-invalid (%s)", guid, NOW_PRIVATE_SENTENCE)
        else:
            self._report_once("now_private_unrecorded", guid, "engine host: engine action %s (%s) could not be "
                              "completed yet: %s", guid, NOW_PRIVATE_SENTENCE, error, level=logging.WARNING)
        return recorded, error

    def restart_pass(self) -> RestartResult:
        """At engine-host start, before the first sweep (§3.4), and then every `COMPLETION_RETRY_SECONDS`
        while it runs (idempotent). For each action RE's user still holds:

        * no runs row: queue it once (the sweep's own adoption, `_screen` -> ADOPT -> `_enqueue`), or, when its
          target is now private, complete it once survey-invalid;
        * a row queued or claimed: leave it, the queue runs it;
        * a row running (under a live process, or awaiting the queue reconciler): leave it;
        * a row that cannot be read: leave it, and say so;
        * a terminal row whose completion was never recorded in Egeria: complete it from the row's stored
          outcome, never by running the step again. A row that stored no outcome completes survey-failed with
          the row's own words (a departure from §3.4's "re-run": see `_outcome_from_ended_row`).

        Each action is handled on its own: one that raises is counted in `errors` and the pass goes on."""
        result = RestartResult()
        if not engine_host_enabled():
            result.note = f"{ENABLE_ENV} is off"
            return result
        from resource_explorer.egeria_clients import Daemon, DaemonReason, acting_as

        self._enter()
        supported = supported_request_types()
        with acting_as(Daemon(DaemonReason.ENGINE_HOST)):
            engine_guid = self.resolve_engine_guid()
            if not engine_guid:
                result.note = "engine not declared in Egeria"
                return result
            claimed = self._claimed_actions(engine_guid)

        for action in claimed:
            guid = _guid(action)
            if not guid:
                continue
            try:
                self._restart_one(action, guid, engine_guid, supported, result)
            except Exception as exc:  # noqa: BLE001 -- one action, never the whole pass
                result.errors.append(guid)
                log.warning("engine host restart: engine action %s left as it is: %s", guid, _short(exc))
        return result

    def _restart_one(self, action: dict, guid: str, engine_guid: str, supported: dict, result: RestartResult) -> None:
        from resource_explorer.egeria_clients import Daemon, DaemonReason, acting_as

        rows = self.registry.engine_action_runs(guid)
        if not rows:
            sweep = SweepResult()
            try:
                reason, facts = self._screen(action, engine_guid, supported)
            except Exception as exc:  # noqa: BLE001 -- fail closed
                reason, facts = "screen_failed", {"error": _short(exc)}
            if reason == ADOPT:
                self._enqueue(action, guid, facts, engine_guid, sweep, adopted=True)
                result.adopted.extend(sweep.adopted)
                result.errors.extend(sweep.errors)
            elif reason == "private_target" and facts.get("ours"):
                recorded, _error = self._complete_now_private(action, guid)
                (result.completed if recorded else result.errors).append(guid)
            else:
                result.errors.append(guid)
                self._report_once("restart_not_queued", guid, "engine host restart: engine action %s is claimed "
                                  "by RE in Egeria with no run, and was not queued (%s: %s)", guid, reason, facts,
                                  level=logging.WARNING)
            return
        row = rows[0]
        if row.get("target_unreadable"):
            result.errors.append(guid)
            self._report_once("unreadable_row", guid, "engine host restart: run %s of engine action %s has an "
                              "unreadable target; left alone", row["id"], guid, level=logging.WARNING)
            return
        state = row.get("state") or ""
        if state == "queued" and self._complete_unrunnable(row, guid, supported, result):
            return
        if state in ("queued", "claimed"):
            result.pending.append(guid)
            return
        if state == "running":
            from resource_explorer.run_reconciler import _is_alive

            try:
                runner = json.loads(row.get("runner") or "{}") or {}
            except (TypeError, ValueError):
                runner = {}
            if _is_alive(runner) is True:
                result.left_running.append(guid)
            else:
                result.pending.append(guid)
                log.info("engine host restart: run %s of engine action %s is 'running' with no live process; "
                         "left for the queue reconciler", row["id"], guid)
            return
        target = _row_target_json(row)
        if target.get("completion_recorded_at"):
            result.pending.append(guid)
            self._report_once("recorded_still_active", guid, "engine host restart: engine action %s is still "
                              "active in Egeria although run %s recorded its completion at %s; left alone", guid,
                              row["id"], target["completion_recorded_at"], level=logging.WARNING)
            return
        stored = target.get("outcome")
        if stored is not None and not (isinstance(stored, dict) and stored.get("guard") in GUARD_STATUS):
            result.errors.append(guid)
            self._report_once("bad_outcome", guid, "engine host restart: run %s of engine action %s stores an "
                              "outcome that is not one RE writes (%r); left alone", row["id"], guid,
                              str(stored)[:120], level=logging.WARNING)
            return
        outcome = stored or _outcome_from_ended_row(row)
        with acting_as(Daemon(DaemonReason.ENGINE_HOST, requested_by=(row.get("requested_by") or None))):
            from pyegeria import AutomatedCuration

            recorded, error = _record_completion(self._client(AutomatedCuration), guid, outcome)
        if not recorded:
            result.errors.append(guid)
            log.warning("engine host restart: completion of engine action %s from run %s not recorded: %s",
                        guid, row["id"], error)
            return
        try:
            self.registry.merge_run_target(row["id"], {"outcome": outcome, "completion_recorded_at": _now_iso()})
        except Exception as exc:  # noqa: BLE001 -- Egeria has it, and stops listing the action
            result.unmarked.append(guid)
            log.warning("engine host restart: completed engine action %s but could not mark run %s (%s)",
                        guid, row["id"], _short(exc))
        result.completed.append(guid)
        log.info("engine host restart: completed engine action %s from run %s (%s)", guid, row["id"],
                 outcome.get("guard"))

    def _complete_unrunnable(self, row: dict, guid: str, supported: dict, result: RestartResult) -> bool:
        """A queued row for a request type this host cannot run, older than UNRUNNABLE_QUEUED_SECONDS: no worker
        has taken it, so it is failed (only while still queued) and completed survey-failed with why. True when
        handled here."""
        from datetime import datetime

        target = _row_target_json(row)
        request_type = target.get("request_type") or ""
        if not request_type or request_type in supported:
            return False
        try:
            queued_at = datetime.fromisoformat(str(row.get("enqueued_at") or "").replace("Z", "+00:00"))
            if queued_at.tzinfo is None:
                queued_at = queued_at.replace(tzinfo=UTC)
            waited = (datetime.now(UTC) - queued_at).total_seconds()
        except (TypeError, ValueError):
            return False
        if waited < UNRUNNABLE_QUEUED_SECONDS:
            return False
        why = (f"step {target.get('step_key')!r} ({request_type}) is not installed on any worker that took up "
               f"this run within {UNRUNNABLE_QUEUED_SECONDS}s; install its Analytic Library package where Resource "
               "Explorer's worker runs")
        if not self.registry.fail_queued_run(row["id"], why):
            return False                      # a worker claimed it meanwhile: it runs
        outcome = _outcome(SURVEY_FAILED, f"Resource Explorer did not run {request_type}: {why}")
        self.registry.merge_run_target(row["id"], {"outcome": outcome})
        from pyegeria import AutomatedCuration

        from resource_explorer.egeria_clients import Daemon, DaemonReason, acting_as

        with acting_as(Daemon(DaemonReason.ENGINE_HOST, requested_by=(row.get("requested_by") or None))):
            recorded, error = _record_completion(self._client(AutomatedCuration), guid, outcome)
        if not recorded:
            result.errors.append(guid)        # the next pass completes it from the stored outcome
            log.warning("engine host restart: engine action %s (unrunnable here) not completed: %s", guid, error)
            return True
        self.registry.merge_run_target(row["id"], {"completion_recorded_at": _now_iso()})
        result.completed.append(guid)
        log.warning("engine host restart: completed engine action %s survey-failed: %s", guid, why)
        return True

    def _claim(self, action: dict, guid: str, facts: dict, engine_guid: str, result: SweepResult) -> None:
        from pyegeria import AutomatedCuration

        from resource_explorer.egeria_clients import Daemon, DaemonReason, acting_as

        requester = str(action.get("requesterUserId") or "").strip()
        with acting_as(Daemon(DaemonReason.ENGINE_HOST, requested_by=requester or None)):
            try:
                self._client(AutomatedCuration).claim_engine_action(guid)
            except Exception as exc:  # noqa: BLE001 -- classified: a lost race is ordinary, the rest is logged
                if is_claim_refused(exc):
                    result.refused.append(guid)
                    log.info("engine host: engine action %s was claimed by another host first", guid)
                    return
                from resource_explorer.secret_redaction import scrub_text

                result.errors.append(guid)
                log.warning("engine host: claim of engine action %s raised; outcome unknown (Egeria may have "
                            "recorded it: if so the next sweep adopts it, if not it claims again): %s",
                            guid, scrub_text(" ".join(str(exc).split()))[:400])
                return
        result.claimed.append(guid)
        self._enqueue(action, guid, facts, engine_guid, result, adopted=False)

    def _enqueue(self, action: dict, guid: str, facts: dict, engine_guid: str, result: SweepResult, *,
                 adopted: bool) -> None:
        requester = str(action.get("requesterUserId") or "").strip()
        try:
            run_id = self.registry.enqueue_run(RUN_KIND, _row_target(action, guid, facts, engine_guid),
                                               requested_by=requester)
        except Exception as exc:  # noqa: BLE001 -- the action is RE's in Egeria with no row; say so loudly
            log.error("engine host: engine action %s is claimed by RE in Egeria but could not be queued "
                      "(%s: %s); the next sweep adopts it", guid, type(exc).__name__, exc)
            result.errors.append(guid)
            return
        if adopted:
            result.adopted.append(guid)
        result.run_ids.append(run_id)
        log.info("engine host: %s engine action %s (%s on %s %s) for %s; queued run %s%s",
                 "adopted RE's own earlier claim of" if adopted else "claimed", guid, facts["request_type"],
                 ENTITY_TYPE, facts["slug"] or "-", requester or "nobody recorded", run_id,
                 f" (to be completed: {facts['refusal']})" if facts.get("refusal") else "")


def _unique_targets(targets: list[dict]) -> list[dict]:
    """The asset targets once each, by GUID, in the order Egeria listed them."""
    seen: set[str] = set()
    out = []
    for t in targets:
        if t["guid"] not in seen:
            seen.add(t["guid"])
            out.append(t)
    return out


def _consumes_source_report(request_type: str, step_key: str) -> bool:
    """Whether this request type's step derives from the previous step's report (an Analytic Library step that
    declares the `source_report` input, engine host step 4). Built-in registry steps take none."""
    if step_key in registry_steps() or step_key in ENGINE_HOST_STEPS:
        return False
    library = library_steps().get(request_type)
    return library is not None and SOURCE_REPORT_INPUT in (getattr(library.manifest, "inputs", None) or [])


def _not_yet_due(action: dict) -> bool:
    """The action's `requestedStartTime` (epoch ms) is still in the future. Missing or unreadable is due."""
    import time

    value = action.get("requestedStartTime")
    if isinstance(value, dict):
        value = value.get("primitiveValue")
    if isinstance(value, bool) or value in (None, ""):
        return False
    try:
        ms = float(value)
    except (TypeError, ValueError):
        return False
    return ms > time.time() * 1000


def _row_target(action: dict, guid: str, facts: dict, engine_guid: str) -> dict:
    """The `runs.target` of an `engine_action` row: what E4 needs, read once from the claimed action."""
    return {
        "engine_action_guid": guid,
        "governance_engine_guid": engine_guid,
        "request_type": facts["request_type"],
        "step_key": facts["step_key"],
        "entity_type": ENTITY_TYPE,
        "slug": facts["slug"],
        # The database asset the SurveyReport hangs off (ReportSubject), as native uses the action target.
        "asset_guid": facts.get("asset_guid", ""),
        # Set when the action is claimed only to be completed with a reason (no target, or not registered).
        "refusal": facts.get("refusal", ""),
        "action_targets": facts["targets"],
        # The previous process step's report (the `surveyReport` target), handed to a step that derives from it.
        "source_report": facts.get("source_report", ""),
        # Database targets after the first: not surveyed, named in the completion message.
        "other_targets": facts.get("other_targets", []),
        "requester_user_id": str(action.get("requesterUserId") or ""),
        # Epoch ms as Egeria sent it. Recorded only: the sweep leaves an action unclaimed until it is due
        # (`_not_yet_due`), so a queued row never waits for its start time.
        "requested_start_time": action.get("requestedStartTime"),
        "request_parameters": action.get("requestParameters") or {},
        # Case 3, decision 5: who coordinates this run. An engine action is always Egeria's; RE's own Run records
        # "re" on its survey_definition_run row. No DDL: it rides in the target JSON.
        "started_by": STARTED_BY_EGERIA,
        # Case 3: the process step this action is, and its next steps' guards (`EngineHost._process_facts`).
        "process_name": facts.get("process_name", ""),
        "process_step_guid": facts.get("process_step_guid", ""),
        "process_step_name": facts.get("process_step_name", ""),
        "definition": facts.get("definition", ""),
        "process_instance_guid": facts.get("process_instance_guid", ""),
        "next_guards": facts.get("next_guards"),
        "next_guards_error": facts.get("next_guards_error", ""),
        # A refused second start of a step already run in this process run (review round 2): no routing guard.
        "duplicate_trigger": bool(facts.get("duplicate_trigger")),
    }


# ── E4: running a claimed engine action ─────────────────────────────────────────

def _now_iso() -> str:
    from datetime import datetime

    return datetime.now(UTC).replace(tzinfo=None).isoformat()


def _row_target_json(row: dict) -> dict:
    try:
        target = json.loads(row.get("target") or "{}")
    except (TypeError, ValueError):
        return {}
    return target if isinstance(target, dict) else {}


def _short(exc: BaseException, limit: int = 400) -> str:
    from resource_explorer.secret_redaction import scrub_text

    return scrub_text(" ".join(f"{type(exc).__name__}: {exc}".split()))[:limit]


def _outcome(guard: str, message: str, *, report_guid: str = "", annotation_count: int | None = None,
             **extra) -> dict:
    """The outcome stored on the run row BEFORE Egeria is told, and the only thing a completion is made from."""
    if guard not in GUARD_STATUS:
        raise ValueError(f"{guard!r} is not one of the declared guards {SURVEY_GUARDS}")
    return {"guard": guard, "status": GUARD_STATUS[guard], "message": message, "report_guid": report_guid,
            "annotation_count": annotation_count, "decided_at": _now_iso(), **extra}


def _outcome_from_ended_row(row: dict) -> dict:
    """A terminal row that stored no outcome: the run ended (reconciled, or crashed) before it decided one.
    Completed survey-failed with the row's own words; never re-run (E5)."""
    why = (row.get("error") or "").strip() or f"the run ended '{row.get('state')}' without recording an outcome"
    return _outcome(SURVEY_FAILED, f"Resource Explorer's run {row.get('id')} ended before it finished: {why}")


def _record_completion(curation, guid: str, outcome: dict) -> tuple[bool, str]:
    """`record_completion_status` from a stored outcome. (recorded, error). The `surveyReport` action target
    is added only when a report exists. Egeria ignores a completion on an action that is no longer active
    (no else branch in `EngineActionHandler.recordCompletionStatus`), so a repeat is harmless."""
    report_guid = outcome.get("report_guid") or ""
    try:
        curation.record_completion_status(
            # Case 3: a definition step's guards (its survey guard, and the routing guard when the process goes on).
            guid, outcome["status"], output_guards=list(outcome.get("guards") or [outcome["guard"]]),
            new_action_targets=([{"actionTargetName": SURVEY_REPORT_TARGET, "actionTargetGUID": report_guid}]
                                if report_guid else None),
            completion_message=outcome.get("message") or "")
    except Exception as exc:  # noqa: BLE001 -- returned: the run says so and E5 sends it at the next start
        return False, _short(exc)
    return True, ""


class EngineHostPublishFailed(RuntimeError):
    """Raised by the engine host's publisher to the synthetic-step executor when the report did not land as a
    complete, linked report: the executor's run words then say "publish failed · <these words>"."""


def _decide(registry, target: dict) -> dict:
    """Run the step and publish its report; return the outcome to store. Never raises for a step or Egeria
    answer: each becomes a guard with its words. Runs as the current principal (the engine host daemon)."""
    outcome = _decide_one(registry, target)
    others = [g for g in target.get("other_targets") or [] if g]
    if others:
        outcome["message"] = (f"{outcome['message'].rstrip('.')}. Only the first database target was surveyed; "
                              f"{len(others)} other target(s) not surveyed: {', '.join(others)}.")
    _route(outcome, target)
    return outcome


def _route(outcome: dict, target: dict) -> None:
    """Case 3, decision 3: a step of an RE definition completes with the guards `step_outcome.completion_guards`
    maps its survey guard to (its survey guard and the routing guard its authored links carry), stored
    on the outcome as `guards`. When the step's authored next-step links take none of those guards, the process ends
    here and the run says so (`process_note`, also in the completion message); Egeria is told the guard all the same.
    An action that is no definition's step completes with its one survey guard, as before."""
    if not target.get("definition"):
        return
    from resource_explorer import step_outcome

    if target.get("duplicate_trigger"):
        # Only its survey guard: emitting the routing guard would start the next step a second time too.
        outcome["guards"] = [outcome["guard"]]
        return
    step_key = target.get("step_key") or ""
    guards = list(step_outcome.completion_guards(step_key, outcome["guard"]))
    outcome["guards"] = guards
    next_guards = target.get("next_guards")
    note = ""
    if next_guards is None:
        if target.get("next_guards_error"):
            note = (f"could not tell whether the process continues after {step_key}: its next steps could not be "
                    f"read ({target['next_guards_error']})")
    elif next_guards and not any(g == "" or g in guards for g in next_guards):
        # A link with no guard (Egeria's null) always fires; any other must equal an output guard.
        note = f"{step_outcome.PROCESS_ENDED_PREFIX} {step_key}: guard {outcome['guard']} has no next step"
    if note:
        outcome["process_note"] = note
        outcome["message"] = f"{outcome['message'].rstrip('.')} · {note}"


def _capability_refusal(registry, slug: str, step: RegistryStep) -> str:
    """The launcher's reason when the database's stored credential lacks the capability `step` declares, else "".
    Read back from the stored `credential_capability` probe, exactly as the launcher gate reads it; "never probed"
    blocks nothing, as there."""
    if not (getattr(step.info, "requires_capability", "") or ""):
        return ""
    from resource_explorer.surveyors import credential_capability, prerequisite_resolver

    entity = registry.get_database(slug, allow_unreadable=True)
    if entity is None:
        return ""
    probe = credential_capability.stored_probe(registry, entity)
    return prerequisite_resolver.capability_reason_detail(step.step_key, step.info, probe)


#: Review L4: the registration's stored password cannot be decrypted here; the step is not run with an empty one.
CREDENTIAL_UNREADABLE_SENTENCE = ("stored credential unreadable · re-enter it on the database's registration "
                                  "(Resource Explorer cannot decrypt it here)")


def _credential_unreadable(registry, slug: str) -> bool:
    entity = registry.get_database(slug, allow_unreadable=True)
    return entity is not None and getattr(entity, "credential_status", "") == "unreadable"


def _stored_credentials(registry, slug: str) -> dict:
    """The database registration's own stored credential, as RE's queue uses it (decision 4): {db_user, db_pwd} for
    the step runner, or {} when none is stored (the step then fails in its own words)."""
    entity = registry.get_database(slug, allow_unreadable=True)
    user = (getattr(entity, "db_user", "") or "") if entity is not None else ""
    pwd = (getattr(entity, "db_password", "") or "") if entity is not None else ""
    return {"db_user": user, "db_pwd": pwd} if (user or pwd) else {}


def _step_output_annotations(entity, step_outputs: list, registry) -> list:
    """The annotations RE's own database publish makes from a run's step outputs (schema inventory, statistics,
    views, operations, credential capability), for the engine host's native-shaped report. Nothing for a step whose
    outputs carry none of these (db_derived brings its own `annotations`)."""
    from resource_explorer.surveyors.database.egeria_database_surveyor import (
        annotations_from_step_outputs,
    )

    return annotations_from_step_outputs(entity, [o for o in step_outputs if isinstance(o, dict)], registry)


def _decide_one(registry, target: dict) -> dict:
    refusal = (target.get("refusal") or "").strip()
    if refusal:
        # Owner ruling, 2026-10-14: survey-invalid ("the supplied information (such as the asset) is invalid").
        return _outcome(SURVEY_INVALID, f"Resource Explorer did not run {target.get('request_type')}: {refusal}")
    slug, step_key = target.get("slug") or "", target.get("step_key") or ""
    request_type = target.get("request_type") or ""
    library = None
    runner_kwargs: dict = {}
    builtin = registry_steps().get(step_key)
    if builtin is not None and step_key not in declared_steps():
        # Case 3: a registry step Egeria dispatched as a step of an RE definition (or alone). Out-of-scope steps were
        # refused at the claim; this says so again if the scope changed between claim and run.
        scope = out_of_scope_reason(step_key)
        if scope:
            return _outcome(SURVEY_INVALID, f"Resource Explorer did not run {request_type}: {scope}")
        refusal = _capability_refusal(registry, slug, builtin)
        if refusal:
            # Decision 4: the launcher's own gate, with its own sentence, and nothing run or published.
            return _outcome(SURVEY_INVALID, f"Resource Explorer did not run {request_type} on {slug}: {refusal}")
        if _credential_unreadable(registry, slug):
            # Review L4: never a driver error with an empty password; the stored credential is what is wrong.
            return _outcome(SURVEY_INVALID, f"Resource Explorer did not run {request_type} on {slug}: "
                            f"{CREDENTIAL_UNREADABLE_SENTENCE}")
        runner_kwargs.update(_stored_credentials(registry, slug))
    elif step_key not in declared_steps():
        library = library_steps().get(request_type)
        if library is None or library.step_key != step_key:
            return _outcome(SURVEY_FAILED, f"step {step_key!r} ({request_type}) is not one Resource Explorer's "
                            "engine runs here" + ("" if library else "; if it is an Analytic Library step, its "
                                                  "package is not installed where the engine host runs"))
    report_extra: dict = {}
    if library is not None and SOURCE_REPORT_INPUT in (getattr(library.manifest, "inputs", None) or []):
        source_guid = (target.get("source_report") or "").strip()
        try:
            # No surveyReport target is a standalone run (design ruling, 2026-10-15): the step runs from what it has
            # and records the absence of the report-based measure. survey-invalid is only for a report that is
            # present and wrong.
            if source_guid:
                runner_kwargs[SOURCE_REPORT_INPUT] = _read_source_report(source_guid, target.get("asset_guid") or "")
        except SourceReportRefused as exc:
            # Readable, and not this database's survey report: the supplied information is invalid.
            return _outcome(SURVEY_INVALID, f"{request_type} on {slug}: the surveyReport action target "
                            f"{source_guid} {exc}")
        except Exception as exc:  # noqa: BLE001 -- the input could not be read: the survey failed, in its words
            return _outcome(SURVEY_FAILED, f"{request_type} on {slug}: the surveyReport action target "
                            f"{source_guid} could not be read: {_short(exc)}", derived_from_report=source_guid)
        if source_guid:
            report_extra["derived_from_report"] = source_guid

    from resource_explorer.surveyors import result_status
    from resource_explorer.surveyors.survey_definition_executor import NothingToPublish, SurveyDefinitionExecutor

    published: dict = {}

    def publisher(entity, step_outputs, surveyed_at, _registry):
        """The executor's publish step for this run: ONE native-shaped report, linked to the action. The
        executor records what this returns (the report GUID) or raises as the run's publish words."""
        if library is None:
            annotations = [a for out in step_outputs if isinstance(out, dict)
                           for a in (out.get("annotations") or [])]
            # Case 3: a credentialed database step's results become annotations exactly as RE's own database
            # publish makes them (`annotations_from_step_outputs`, the code `publish_step_annotations` runs).
            annotations += _step_output_annotations(entity, step_outputs, _registry)
        else:
            records = [r for out in step_outputs if isinstance(out, dict)
                       for r in (out.get("step_annotations") or [])]
            if not library.derived(records):
                why = (f"{step_key} on {slug}: nothing to derive ({library.nothing_to_derive_reason(records)}): "
                       "an absence, not a finding")
                if library.nothing_to_derive_guard != SURVEY_COMPLETED:
                    published["outcome"] = _outcome(library.nothing_to_derive_guard, why,
                                                    annotation_count=len(records), **report_extra)
                    raise NothingToPublish(why)
            annotations = library.report_annotations(records)
        published["outcome"] = _publish(registry, target, annotations, extra=report_extra)
        if published["outcome"]["guard"] != SURVEY_COMPLETED:
            raise EngineHostPublishFailed(published["outcome"]["message"])
        return published["outcome"]["report_guid"]

    from resource_explorer.secret_redaction import redacting_logs
    from resource_explorer.surveyors.survey_definition_executor import _redactable

    try:
        # The stored password, when one is used, is kept out of every log line about the run's own failures.
        with redacting_logs(_redactable(runner_kwargs.get("db_pwd"), "")):
            ran = SurveyDefinitionExecutor(registry).run_synthetic_step(
                entity_type=ENTITY_TYPE, slug=slug, re_analysis_step=step_key, executes_at="resource-explorer",
                # In this process, never Prefect (Prefect-only steps are not in the slice, §5.4).
                engine_override="resource-explorer", publisher=publisher,
                display_name=f"{request_type} (engine action {target.get('engine_action_guid')})", **runner_kwargs)
    except Exception as exc:  # noqa: BLE001 -- the step could not run: survey-failed with its words
        return published.get("outcome") or _outcome(SURVEY_FAILED, f"{step_key} on {slug} could not run: "
                                                    f"{_short(exc)}")
    if "outcome" in published:
        return published["outcome"]
    report = next((r for r in ran.get("steps") or [] if r.get("re_analysis_step") == step_key), {})
    status = report.get("status") or ""
    if status == result_status.SKIPPED_BY_DESIGN:
        # db_derived's precondition: no stored schema inventory. "an absence, not a finding" (survey-invalid:
        # "the supplied information (such as the asset) is invalid for this type of survey").
        return _outcome(SURVEY_INVALID, f"{step_key} did not run on {slug}: "
                        f"{report.get('detail') or 'its precondition is not met'}"
                        + ("" if library else " (no stored schema inventory to derive from: an absence, not a "
                           "finding)"))
    errors = "; ".join(str(e) for e in ran.get("errors") or []) or f"step status {status or 'unknown'}"
    if status == "ok":
        errors = f"the step produced no output to publish ({errors})"
    return _outcome(SURVEY_FAILED, f"{step_key} failed on {slug}: {errors}"[:1500])


class SourceReportRefused(ValueError):
    """The surveyReport target was read and is not a survey report of the action's database."""


def _report_subjects(result: dict) -> set[str]:
    """The assets a report is about: its ReportSubject ends and its anchor (live shape, M2)."""
    out = {str(((e.get("relatedElement") or {}).get("elementHeader") or {}).get("guid") or "")
           for e in result.get("reportSubjects") or [] if isinstance(e, dict)}
    anchor = (((result.get("elementHeader") or {}).get("anchor") or {}).get("classificationProperties") or {})
    out.add(str(anchor.get("anchorGUID") or ""))
    return {g for g in out if g}


def _read_source_report(report_guid: str, asset_guid: str) -> dict:
    """The previous step's SurveyReport as plain data for a step that derives from it: {guid, at, annotations}, the
    annotations through the existing reader (`egeria_survey_reader.annotations_from_report`). Read as the current
    principal (the engine host daemon); the step's own code never touches Egeria. Fails closed: the element must
    say it is a SurveyReport, and its subject (ReportSubject or anchor) must be `asset_guid`, else
    `SourceReportRefused` (another database's counters must never be published as this one's)."""
    from pyegeria import AssetMaker

    from resource_explorer.egeria_clients import current_principal, egeria_client
    from resource_explorer.surveyors.egeria_survey_reader import annotations_from_report

    assets = egeria_client(current_principal(), purpose="engine host", shared=False).of(AssetMaker)
    try:
        result = assets.get_asset_by_guid(report_guid, body={"class": "GetRequestBody", "graphQueryDepth": 1},
                                          output_format="JSON")
    finally:
        _close_quietly(assets)
    if not isinstance(result, dict):
        raise RuntimeError(f"Egeria answered {' '.join(str(result).split())[:160]!r}, not a SurveyReport element")
    type_name = (((result.get("elementHeader") or {}).get("type") or {}).get("typeName")) or ""
    if type_name != "SurveyReport":
        raise SourceReportRefused(f"is {'a ' + type_name if type_name else 'an element of no stated type'}, "
                                  "not a SurveyReport")
    subjects = _report_subjects(result)
    if not asset_guid or asset_guid not in subjects:
        raise SourceReportRefused(f"is a survey report of {', '.join(sorted(subjects)) or 'no stated asset'}, "
                                  f"not of this action's database {asset_guid or '(none)'}")
    props = result.get("properties") or {}
    return {"guid": report_guid, "at": str(props.get("completionTime") or props.get("createdTime") or ""),
            "annotations": annotations_from_report(result)}


def _publish(registry, target: dict, annotations: list, extra: dict | None = None) -> dict:
    """Publish the report through the database survey publisher; the outcome follows the proof. `extra` lands in the
    report's additionalProperties and on the outcome (`derived_from_report`, engine host step 4)."""
    extra_props = dict(extra or {})
    from resource_explorer.egeria_clients import current_principal
    from resource_explorer.surveyors.database.egeria_database_surveyor import EgeriaDatabaseSurveyor

    slug, request_type = target.get("slug") or "", target.get("request_type") or ""
    guid = target.get("engine_action_guid") or ""
    identity = current_principal()
    try:
        entity = registry.get_database(slug, allow_unreadable=True)     # never connects: no credential needed
        if entity is None:
            return _outcome(SURVEY_FAILED, f"database {slug!r} is no longer registered in Resource Explorer")
        pub = EgeriaDatabaseSurveyor(identity=identity).publish_engine_action_report(
            entity, annotations, asset_guid=target.get("asset_guid") or entity.egeria_asset_guid,
            request_type=request_type, engine_action_guid=guid, engine_user=identity.client_user or "",
            requester=(identity.requested_by or "").strip(), registry=registry, extra_properties=extra_props)
    except Exception as exc:  # noqa: BLE001 -- a publish that raised is a failed survey, said in its words
        return _outcome(SURVEY_FAILED, f"the survey report for {slug} was not published: {_short(exc)}",
                        annotation_count=len(annotations), **extra_props)
    report_guid = pub.get("report_guid") or ""
    sent, landed = len(annotations), pub.get("annotations_in_egeria")
    extra = {"annotations_in_egeria": landed, "qualified_name": pub.get("qualified_name", ""),
             "ownership": (pub.get("governance") or {}).get("ownership"), **extra_props}
    if not report_guid:
        return _outcome(SURVEY_FAILED, "the survey report for "
                        f"{slug} was not published: {pub.get('report_error') or 'Egeria gave no report GUID'}",
                        annotation_count=sent, **extra)
    if pub.get("originator_error"):
        return _outcome(SURVEY_FAILED, f"survey report {report_guid} was published but not linked to this "
                        f"engine action (ReportOriginator): {pub['originator_error']}",
                        report_guid=report_guid, annotation_count=sent, **extra)
    problem = pub.get("annotation_error") or pub.get("report_error") or ""
    if landed is None or landed < sent or problem:
        told = "could not be counted" if landed is None else f"{landed} of {sent} reached Egeria"
        return _outcome(SURVEY_FAILED, f"survey report {report_guid} is incomplete: its annotations {told}"
                        + (f" ({problem})" if problem else ""),
                        report_guid=report_guid, annotation_count=sent, **extra)
    governance = "" if extra["ownership"] else " (Ownership was not recorded on the report)"
    return _outcome(SURVEY_COMPLETED, f"Resource Explorer ran {request_type} on {slug}: survey report "
                    f"{report_guid} with {sent} annotation(s){governance}.",
                    report_guid=report_guid, annotation_count=sent, **extra)


def run_engine_action(registry, run_id: str, target: dict) -> tuple[str, str]:
    """E4: run one claimed engine action to a completion recorded in Egeria. (run state, error).

    Runs as the current principal, which `run_queue` sets to `Daemon(ENGINE_HOST, requested_by=<requester>)`
    (`run_queue._run_as_engine_host`), never the requester and never `RUN_QUEUE`. In order:

    1. IN_PROGRESS on the action (C7). A failed status update is logged and the run goes on.
    2. The outcome: the step through the synthetic-step executor, then the SurveyReport (`_decide`).
    3. The outcome stored on the run row (`target.outcome`: guard, status, message, report GUID, annotation
       count). If it cannot be stored, nothing is sent to Egeria and the run fails: E5 completes it later.
    4. `record_completion_status` from that stored outcome, then `target.completion_recorded_at`.

    No R21 OpenLineage event is emitted for this work: Egeria reports its lineage from the action's status
    changes (Q-M6)."""
    from resource_explorer.observability import openlineage

    guid = (target or {}).get("engine_action_guid") or ""
    if registry is None or not run_id or not guid:
        return "failed", "engine_action run with no registry, run id or engine action GUID; nothing was sent"
    with openlineage.suppressed():
        from resource_explorer.catalogue_commit import run_with_loop

        return run_with_loop(_run_engine_action, registry, run_id, target)


def _run_engine_action(registry, run_id: str, target: dict) -> tuple[str, str]:
    from pyegeria import AutomatedCuration

    from resource_explorer.egeria_clients import current_principal, egeria_client

    principal = current_principal()
    # Not scope-shared: this client is closed when the run ends (it is bound to this run's event loop).
    curation = egeria_client(principal, purpose="engine host", shared=False).of(AutomatedCuration)
    try:
        return _run_with(curation, registry, run_id, target, principal)
    finally:
        _close_quietly(curation)


def _close_quietly(client) -> None:
    """pyegeria's own close (`close_session`) of a client this run built. Tidy-up, never the answer."""
    close = getattr(client, "close_session", None)
    if callable(close):
        try:
            close()
        except Exception:  # noqa: BLE001 - tidy-up, never the answer
            pass


def _record_submission(registry, target: dict, principal) -> str:
    """The native-survey row the Understanding pane lists (`native_survey_run.native_survey_rows`), written
    once per engine action: the existing read-back then fills Egeria's status and the report through
    `report_guid_for_action`, as for a PostgreSQLSurvey run. "" when written or already there, else why not."""
    slug, guid = target.get("slug") or "", target["engine_action_guid"]
    if not slug:
        return ""
    process_qn = governance_action_type_qn_for(target.get("request_type") or
                                               request_type_for_step(target.get("step_key") or ""))
    extra: dict = {}
    if target.get("definition") and target.get("process_instance_guid"):
        # Case 3 (C4): a step of an RE definition Egeria coordinates is ONE run of the definition, listed once per
        # process instance and read back step by step (`native_survey_run.refresh_mixed_run`). A run started from
        # RE's "coordinated by Egeria" control already has that row (its GUID is the same instance); one started in
        # Egeria gets it here, from its first step RE runs.
        from resource_explorer import mixed_process

        process_qn, guid = target["definition"], target["process_instance_guid"]
        # Who coordinates it, from the row the claim wrote (`_row_target`), onto the run line Understanding reads.
        extra = {"started_by": target.get("started_by") or STARTED_BY_EGERIA,
                 "started_in": mixed_process.STARTED_IN_EGERIA}
    try:
        if extra:
            # One row per process instance, whoever writes first (review L5): a row the Run press wrote is kept.
            registry.record_native_survey_submission_once(ENTITY_TYPE, slug, process_qn, _now_iso(),
                                                          engine_action_guid=guid,
                                                          submitted_by=(principal.requested_by or "").strip(),
                                                          started_as="engine-host",
                                                          declared_extra=mixed_process.expected_steps_record(process_qn),
                                                          **extra)
            return ""
        if any(r.get("engine_action_guid") == guid
               for r in registry.list_native_survey_runs(ENTITY_TYPE, slug, process_qn)):
            return ""
        registry.record_native_survey_submission(ENTITY_TYPE, slug, process_qn, _now_iso(),
                                                 engine_action_guid=guid,
                                                 submitted_by=(principal.requested_by or "").strip(),
                                                 started_as="engine-host")
    except Exception as exc:  # noqa: BLE001 -- the listing row, not the work: recorded on the outcome
        why = _short(exc)
        log.warning("engine host: no native-survey row for engine action %s (%s)", guid, why)
        return why
    return ""


def _run_with(curation, registry, run_id: str, target: dict, principal) -> tuple[str, str]:
    guid = target["engine_action_guid"]
    status_error = ""
    try:
        curation.update_engine_action_status(guid, "IN_PROGRESS")
    except Exception as exc:  # noqa: BLE001 -- a status line, not the work: recorded on the outcome, run goes on
        status_error = _short(exc)
        log.warning("engine host: could not set engine action %s IN_PROGRESS (%s); running it anyway",
                    guid, status_error)
    listing_error = _record_submission(registry, target, principal)
    outcome = _decide(registry, target)
    if status_error:
        outcome["in_progress_error"] = status_error
    if listing_error:
        outcome["listing_error"] = listing_error
    registry.merge_run_target(run_id, {"outcome": outcome})      # raises: nothing is sent unstored
    recorded, error = _record_completion(curation, guid, outcome)
    log.info("engine host: engine action %s ran: %s (%s)%s", guid, outcome["guard"], outcome["message"],
             "" if recorded else f"; completion NOT recorded in Egeria: {error}")
    if not recorded:
        return "failed", (f"{outcome['guard']}: {outcome['message']} · completion not recorded in Egeria "
                          f"({error}); the engine host sends it within {COMPLETION_RETRY_SECONDS}s")[:1500]
    marker_note = ""
    try:
        registry.merge_run_target(run_id, {"completion_recorded_at": _now_iso()})
    except Exception as exc:  # noqa: BLE001 -- Egeria has the completion: the run says so, never "failed" for it
        marker_note = (f"completion recorded in Egeria; RE's completion marker on this run was not written "
                       f"({_short(exc, 200)})")
        log.warning("engine host: engine action %s completed in Egeria, but run %s's marker was not written: %s",
                    guid, run_id, marker_note)
    if outcome["guard"] == SURVEY_COMPLETED:
        return "succeeded", marker_note
    return "failed", " · ".join(x for x in (f"{outcome['guard']}: {outcome['message']}", marker_note) if x)[:1500]


# ── the worker loop ────────────────────────────────────────────────────────────

class EngineHostLoop:
    """The worker's engine-host thread: one `EngineHost`, swept every SWEEP_INTERVAL_SECONDS. Started by the
    worker's leader-elected supervisor only when RE_ENGINE_HOST_ENABLED is on (worker.loop_specs)."""

    def __init__(self, interval: float = SWEEP_INTERVAL_SECONDS,
                 completion_retry_interval: float = COMPLETION_RETRY_SECONDS) -> None:
        self.interval = interval
        self.completion_retry_interval = completion_retry_interval
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def _run(self) -> None:
        import time

        host = EngineHost()
        try:
            last_pass = None
            while not self._stop.is_set():
                if last_pass is None or time.monotonic() - last_pass >= self.completion_retry_interval:
                    # E5: before the first sweep (§3.4), then every completion_retry_interval: a completion that
                    # timed out, or a run whose worker died, reaches Egeria without waiting for a restart.
                    last_pass = time.monotonic()
                    self._guarded(lambda: host.restart_pass(), "completion pass")
                self._guarded(lambda: host.sweep_once(), "sweep")
                if self._stop.wait(self.interval):
                    return
        finally:
            host.close()

    @staticmethod
    def _guarded(fn, what: str) -> None:
        try:
            fn()
        except Exception:  # noqa: BLE001 -- one bad pass must not end the loop
            import traceback

            from resource_explorer.secret_redaction import scrub_text

            log.error("engine host: %s failed; continuing\n%s", what, scrub_text(traceback.format_exc()))

    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="re-engine-host", daemon=True)
        self._thread.start()
        log.info("engine host: sweep started every %ss for engine %s (request types %s)", self.interval,
                 ENGINE_QUALIFIED_NAME, sorted(supported_request_types()))

    def stop(self, timeout: float = STOP_JOIN_SECONDS) -> None:
        self._stop.set()
        thread, self._thread = self._thread, None
        if thread is not None:
            # Bounded: a sweep blocked in an Egeria call must not hold shutdown open. It is a daemon thread.
            thread.join(timeout=timeout)
            if thread.is_alive():
                log.warning("engine host: sweep thread still running after %ss; it goes with the process", timeout)


_loop = EngineHostLoop()


def start_engine_host() -> None:
    if not engine_host_enabled():
        log.info("engine host: not started (%s is off)", ENABLE_ENV)
        return
    _loop.start()


def stop_engine_host() -> None:
    _loop.stop()
