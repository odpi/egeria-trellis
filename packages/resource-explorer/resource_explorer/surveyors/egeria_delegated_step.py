"""
Case 4 of docs/re-as-engine-host-plan.md: delegate a single survey step to
a real Egeria governance/survey action service instead of running it
locally. RE stays the orchestrator (SurveyOrchestrator.run(steps=[...]))
but one step's actual work happens on a real Egeria engine -- the mirror
image of case 2 (RE claiming steps *from* an Egeria-orchestrated process).

Deliberately uses only the requester-side pyegeria surface that already
exists -- AutomatedCuration.initiate_engine_action()/initiate_gov_action_
type() to trigger, MetadataExpert.get_metadata_element_by_guid() to poll
(the same generic-element-read pattern already live-verified in
rfa_egeria_sync.py for ToDo status) -- no new pyegeria client work needed
for this case, confirmed in the plan doc's gap analysis (that's case 2's
problem, not case 4's).

**ISSUE-50 is FIXED (pyegeria 6.0.18.4+, confirmed 2026-08-27).**
initiate_engine_action() used to 404: it posted to a URL missing the
governance engine's name as a required path segment, and had no parameter
to supply one. It now takes `governance_engine_name` as its first required
argument and builds the URL the real Java route
(/governance-engines/{governanceEngineName}/engine-actions/initiate)
expects. initiate_and_wait() below passes it, so the direct path is the
preferred one again: it needs no pre-authored GovernanceActionType per
delegated step, which was the whole cost of the workaround.

**The workaround, kept and still valid, live-verified 2026-08-17** against
a real platform: AutomatedCuration.initiate_gov_action_type() hits POST .../
governance-action-types/initiate -- the same flat URL shape as
initiate_gov_action_process (no engine-name path segment at all), since a
GovernanceActionType already carries its own GovernanceActionExecutor
link to a specific engine + requestType, resolved server-side from the
metadata rather than passed by the caller. This sidesteps ISSUE-50
entirely. Confirmed via docs/dr-egeria/re-delegated-step-probe.md
(authored a GovernanceActionType + Link Action to Action Executor to the
real Stewardship engine's write-to-audit-log service, confirming Link
Action to Action Executor's own executor GUID matched the real Stewardship
engine's GUID from the Java source) followed by a real
EgeriaDelegatedStepSurveyor.run() call reaching actionStatus COMPLETED
with a real completion message. initiate_action_type_and_wait() remains
correct and is the right choice when a GovernanceActionType already exists
-- it carries its own executor link, so the engine is resolved server-side
from metadata rather than named by the caller. It is no longer the only
viable path.

**A second, real bug was found and fixed during that same live pass --
this one RE's own, not pyegeria's**: _poll_action_status() originally read
the wrong wire property key ("actionStatus", derived from
EngineActionElement.java's getActionStatus() bean getter) instead of the
real key, "activityStatus" (confirmed by inspecting a live EngineAction's
raw elementProperties.propertyValueMap -- same key ToDo uses for the same
ActivityStatus enum type). The wrong key always read back "UNKNOWN", which
isn't in _ACTIVE_STATUSES, so polling stopped after exactly one iteration
and reported a false-terminal result even while the action was still
genuinely in flight. Fixed here directly (not filed as a PYEGERIA_ISSUES.md
entry -- this was never pyegeria's bug).

Two triggering primitives, one polling helper, one surveyor wrapper:
  initiate_and_wait()             -- direct engine-action trigger. Needs
                                      governance_engine_name; no
                                      pre-authored element required.
  initiate_action_type_and_wait() -- GovernanceActionType-mediated
                                      trigger. Engine resolved server-side;
                                      requires a
                                      pre-authored GovernanceActionType
                                      (Dr.Egeria "Create Governance Action
                                      Type" + "Link Action to Action
                                      Executor") per delegated step.
  EgeriaDelegatedStepSurveyor     -- a BaseSurveyor-shaped wrapper around
                                      either, so a specific
                                      SurveyOrchestrator step can delegate
                                      its work by construction kwargs
                                      alone (StepInfo.static_kwargs'
                                      existing convention). Pass exactly
                                      one of request_type (direct path) or
                                      action_type_qualified_name
                                      (workaround path) -- the latter is
                                      what actually works today.
"""
from __future__ import annotations

import logging
import time
from typing import Any

from resource_explorer.surveyors.base_surveyor import BaseSurveyor
from resource_explorer.surveyors.survey_report import (
    Annotation,
    RequestForActionAnnotation,
    ResourceMeasureAnnotation,
)

log = logging.getLogger(__name__)

# ActivityStatus symbolic names (Egeria's EngineActionElement "activityStatus"
# wire property -- see _poll_action_status's docstring for the real vs.
# Java-bean-getter-name confusion this key had during development)
# that mean "still running" -- anything else is terminal. Confirmed against
# GovernanceActionStatus.java's enum table (docs/re-as-engine-host-plan.md's
# research): REQUESTED/APPROVED/WAITING/ACTIVATING/IN_PROGRESS precede a
# terminal ACTIONED(->COMPLETED)/INVALID/IGNORED outcome.
_ACTIVE_STATUSES = {"REQUESTED", "APPROVED", "WAITING", "ACTIVATING", "IN_PROGRESS"}
# Terminal statuses that count as success for annotation purposes. Egeria's
# own "ACTIONED" maps to ActivityStatus.COMPLETED per the enum table, but
# both symbolic names are accepted defensively since which one actually
# appears on the wire wasn't live-verified as part of this build.
_SUCCESS_STATUSES = {"COMPLETED", "ACTIONED"}

DEFAULT_POLL_INTERVAL_SECONDS = 5.0
DEFAULT_TIMEOUT_SECONDS = 300.0


class EgeriaEngineActionTimeoutError(RuntimeError):
    """Raised when a delegated engine action does not reach a terminal
    status within the configured timeout. The action itself is left
    running in Egeria -- this only means RE gave up waiting for it; the
    action is not cancelled."""


def _get_clients():
    """Construct (AutomatedCuration, MetadataExpert) clients, bearer-tokened
    -- mirrors rfa_egeria_sync._get_clients()'s construction pattern exactly
    (same env-var-driven connection helper, same create_egeria_bearer_token
    dance), kept as its own local helper rather than shared since these two
    modules otherwise have no coupling."""
    from pyegeria import AutomatedCuration, MetadataExpert

    from resource_explorer.egeria_clients import current_principal, egeria_client

    # Who (Brief I): the survey's own identity — the signed-in Caller from a route, the queued
    # run's Daemon(RUN_QUEUE, requested_by) in the worker, Daemon(SCHEDULER) from a scheduled survey.
    clients = egeria_client(current_principal(), purpose="egeria delegated step")
    return clients.of(AutomatedCuration), clients.of(MetadataExpert)


def _poll_action_status(
    metadata_expert, engine_action_guid: str, poll_interval: float, timeout: float
) -> tuple[str, str]:
    """Poll get_metadata_element_by_guid() until the engine action reaches
    a terminal ActivityStatus, or raise EgeriaEngineActionTimeoutError.
    Returns (final_status, completion_message).

    Uses the raw typed elementProperties.propertyValueMap shape (same as
    rfa_egeria_sync's pre-update_asset ToDo verification) rather than a
    flattened report-spec read -- get_metadata_element_by_guid is a single-
    element point lookup with no pagination concerns, the simplest correct
    tool for polling one known GUID.

    Live-verified 2026-08-17 against a real EngineAction element: the wire
    property key is "activityStatus" (same key ToDo uses for the same
    ActivityStatus enum type) -- NOT "actionStatus", despite that being the
    Java bean's field/getter name on EngineActionElement.java
    (getActionStatus()). A first version of this function used the wrong
    key, read back "UNKNOWN" on every poll (a key that doesn't exist in
    _ACTIVE_STATUSES, so it was misread as an immediate terminal state),
    and returned after one iteration on a request that was, per
    completionGuards/completionTime in the raw response, still genuinely
    in flight at that moment. Fixed here, not filed as a pyegeria issue --
    this was RE's own property-key mistake, not pyegeria's.
    """
    deadline = time.monotonic() + timeout
    status = "UNKNOWN"
    while True:
        element = metadata_expert.get_metadata_element_by_guid(engine_action_guid)
        prop_map: dict[str, Any] = {}
        if isinstance(element, dict):
            prop_map = (element.get("elementProperties") or {}).get("propertyValueMap") or {}
        status = (prop_map.get("activityStatus") or {}).get("symbolicName", "UNKNOWN")

        if status not in _ACTIVE_STATUSES:
            completion_message = (prop_map.get("completionMessage") or {}).get("primitiveValue", "")
            return status, completion_message

        if time.monotonic() >= deadline:
            raise EgeriaEngineActionTimeoutError(
                f"Engine action {engine_action_guid} still {status} after {timeout}s"
            )
        time.sleep(poll_interval)


def initiate_and_wait(
    *,
    governance_engine_name: str,
    qualified_name: str,
    display_name: str,
    description: str,
    request_type: str,
    domain_identifier: int = 0,
    request_parameters: dict[str, str] | None = None,
    action_targets: list[dict] | None = None,
    request_source_guids: list[str] | None = None,
    received_guards: list[str] | None = None,
    poll_interval: float = DEFAULT_POLL_INTERVAL_SECONDS,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> tuple[str, str, str]:
    """Trigger a single Egeria governance/survey action service via
    AutomatedCuration.initiate_engine_action() and block until it reaches
    a terminal status, or the timeout elapses.

    **ISSUE-50 is fixed** (pyegeria 6.0.18.4+, confirmed 2026-08-27).
    initiate_engine_action() used to 404 because it posted to a URL missing
    the governance engine's name and had no parameter to supply one; it now
    takes `governance_engine_name` as its first required argument and builds
    `.../governance-engines/{name}/engine-actions/initiate`, which is the
    shape AutomatedCurationResource.java expects.

    This is the preferred path again. Unlike initiate_action_type_and_wait()
    it needs no pre-authored GovernanceActionType per delegated step -- that
    authoring overhead existed only to route around the bug.

    Returns (engine_action_guid, final_status, completion_message).
    Raises EgeriaEngineActionTimeoutError on timeout, or a plain exception
    if initiation itself fails (bad request_type, connection failure, ...) --
    callers that want "never raise" semantics (e.g. a BaseSurveyor) must
    catch both explicitly; this primitive itself stays honest about
    failure rather than swallowing it.
    """
    automated_curation, metadata_expert = _get_clients()
    guid = automated_curation.initiate_engine_action(
        governance_engine_name=governance_engine_name,
        qualified_name=qualified_name,
        domain_identifier=domain_identifier,
        display_name=display_name,
        description=description,
        request_source_guids=request_source_guids or [],
        action_targets=action_targets or [],
        received_guards=received_guards or [],
        request_type=request_type,
        request_parameters=request_parameters or {},
    )
    if not guid or guid == "Action not initiated":
        raise RuntimeError(
            f"Egeria did not initiate an engine action for request_type={request_type!r} "
            f"on engine {governance_engine_name!r} (qualified_name={qualified_name!r})"
        )
    status, completion_message = _poll_action_status(metadata_expert, guid, poll_interval, timeout)
    return guid, status, completion_message


def initiate_action_type_and_wait(
    *,
    action_type_qualified_name: str,
    request_parameters: dict[str, str] | None = None,
    action_targets: list[dict] | None = None,
    request_source_guids: list[str] | None = None,
    poll_interval: float = DEFAULT_POLL_INTERVAL_SECONDS,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> tuple[str, str, str]:
    """Trigger a pre-authored GovernanceActionType (which already carries
    its own GovernanceActionExecutor link to a specific engine +
    requestType) via AutomatedCuration.initiate_gov_action_type(), and
    block until the resulting engine action reaches a terminal status, or
    the timeout elapses.

    Originally the ISSUE-50 workaround; now simply the other valid path.
    Prefer it when the GovernanceActionType already exists, since it carries
    its own executor link and the engine is resolved server-side. Requires the
    GovernanceActionType to already exist in Egeria (Dr.Egeria "Create
    Governance Action Type" + "Link Action to Action Executor"); this
    function does no authoring itself.

    Returns (engine_action_guid, final_status, completion_message). Same
    raise/never-raise contract as initiate_and_wait().
    """
    automated_curation, metadata_expert = _get_clients()
    guid = automated_curation.initiate_gov_action_type(
        action_type_qualified_name=action_type_qualified_name,
        request_source_guids=request_source_guids or [],
        action_targets=action_targets or [],
        request_parameters=request_parameters or {},
    )
    if not guid or guid == "Action not initiated":
        raise RuntimeError(
            f"Egeria did not initiate an engine action for "
            f"action_type_qualified_name={action_type_qualified_name!r}"
        )
    status, completion_message = _poll_action_status(metadata_expert, guid, poll_interval, timeout)
    return guid, status, completion_message


class EgeriaDelegatedStepSurveyor(BaseSurveyor):
    """A SurveyOrchestrator step whose actual work happens on a real Egeria
    governance/survey engine instead of locally in RE. Parameterized
    entirely via constructor kwargs, matching StepInfo.static_kwargs'
    existing convention -- registering a real delegated step in
    STEP_REGISTRY is just naming the target, no new surveyor subclass
    needed per delegated step. Never raises; failures (including a
    timeout) become a RequestForActionAnnotation via BaseSurveyor._warn,
    matching every other surveyor's contract.

    Pass exactly one of:
      request_type               -- direct AutomatedCuration.
                                     initiate_engine_action() path. Needs
                                     governance_engine_name alongside it
                                     (the engine is a URL path segment).
                                     Preferred: no per-step Egeria
                                     authoring required.
      action_type_qualified_name -- AutomatedCuration.
                                     initiate_gov_action_type() workaround
                                     path, via a pre-authored
                                     GovernanceActionType. The one that
                                     actually works today; requires
                                     authoring that GovernanceActionType in
                                     Egeria first (Dr.Egeria "Create
                                     Governance Action Type" + "Link Action
                                     to Action Executor").
    """

    def __init__(
        self,
        project,
        registry,
        *,
        request_type: str | None = None,
        governance_engine_name: str | None = None,
        action_type_qualified_name: str | None = None,
        display_name: str = "",
        description: str = "",
        request_parameters: dict[str, str] | None = None,
        action_targets: list[dict] | None = None,
        poll_interval: float = DEFAULT_POLL_INTERVAL_SECONDS,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        if bool(request_type) == bool(action_type_qualified_name):
            raise ValueError(
                "EgeriaDelegatedStepSurveyor needs exactly one of "
                "request_type or action_type_qualified_name, not both/neither"
            )
        if request_type and not governance_engine_name:
            # The engine name is a path segment on the initiate endpoint, so a
            # request_type alone cannot address anything. Refused here rather
            # than at the HTTP layer, where it surfaced as a bare 404 for two
            # months (ISSUE-50) and read as a pyegeria bug rather than a
            # missing argument.
            raise ValueError(
                "EgeriaDelegatedStepSurveyor needs governance_engine_name "
                "alongside request_type -- the engine name is part of the "
                "initiate URL, not a body field"
            )
        super().__init__(project, registry)
        self._governance_engine_name = governance_engine_name
        self._request_type = request_type
        self._action_type_qualified_name = action_type_qualified_name
        target = request_type or action_type_qualified_name
        self._display_name = display_name or f"RE-delegated: {target}"
        self._description = description
        self._request_parameters = request_parameters or {}
        self._action_targets = action_targets or []
        self._poll_interval = poll_interval
        self._timeout = timeout

    @property
    def step_name(self) -> str:
        target = self._request_type or self._action_type_qualified_name
        return f"EgeriaDelegatedStep[{target}]"

    def run(self) -> list[Annotation]:
        results: list[Annotation] = []
        try:
            if self._action_type_qualified_name:
                guid, status, completion_message = initiate_action_type_and_wait(
                    action_type_qualified_name=self._action_type_qualified_name,
                    request_parameters=self._request_parameters,
                    action_targets=self._action_targets,
                    poll_interval=self._poll_interval,
                    timeout=self._timeout,
                )
            else:
                qualified_name = (
                    f"EngineAction::{self.project.slug}::{self._request_type}::{int(time.time())}"
                )
                guid, status, completion_message = initiate_and_wait(
                    governance_engine_name=self._governance_engine_name,
                    qualified_name=qualified_name,
                    display_name=self._display_name,
                    description=self._description
                    or f"Delegated from Resource Explorer for {self.project.slug}",
                    request_type=self._request_type,
                    request_parameters=self._request_parameters,
                    action_targets=self._action_targets,
                    poll_interval=self._poll_interval,
                    timeout=self._timeout,
                )
        except EgeriaEngineActionTimeoutError as exc:
            self._warn(results, str(exc))
            return results
        except Exception as exc:
            target = self._request_type or self._action_type_qualified_name
            self._warn(results, f"Could not delegate {target} to Egeria: {exc}")
            return results

        target = self._request_type or self._action_type_qualified_name
        if status in _SUCCESS_STATUSES:
            results.append(
                ResourceMeasureAnnotation(
                    check_name="delegated_action",
                    item_key=self.step_name,
                    summary=f"Delegated Egeria action ({target}) completed: {status}",
                    analysis_step=self.step_name,
                    source="egeria",
                    resource_properties={
                        "engine_action_guid": guid,
                        "final_status": status,
                        "delegated_to": target,
                    },
                )
            )
        else:
            results.append(
                RequestForActionAnnotation(
                    check_name="delegated_action",
                    item_key=self.step_name,
                    summary=(
                        f"Delegated Egeria action ({target}) did not "
                        f"complete successfully: {status}"
                    ),
                    analysis_step=self.step_name,
                    action_requested="Review the engine action in Egeria",
                    action_target_name=guid,
                    explanation=completion_message or f"Final status: {status}",
                    confidence=50,
                    source="egeria",
                )
            )
        return results
