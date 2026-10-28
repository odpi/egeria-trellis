"""Engine host, step 4: one mixed Egeria process, a native survey step chained to an RE step.

Design: `docs/design-notes/SLICE-ENGINE-HOST-STEP4-MIXED-PROCESS.md` (case 2 of
`DESIGN-RE-ENGINE-HOST-BY-EQUIVALENCE.md`, §4.1 "mixed processes").

`GovActionProcess::ResourceExplorer::PostgresSurveyThenDerived` has two steps: Egeria's own PostgreSQL survey
(engine `PostgreSQLSurvey`, Java) and then RE's `re-database-staleness` (RE's engine host), linked by one
`NextGovernanceActionProcessStep` with guard `survey-completed`, mandatory. Egeria evaluates the guard and carries
the action targets; RE runs step 2 exactly as it runs any engine action (`engine_host.py`), reading the step-1
report it is handed as the `surveyReport` action target.

This module holds:

* `MIXED_PROCESSES`, the one registry entry the declaration generator (`scripts/generate_engine_host_declaration.py
  --mixed-process`) renders from, and its test holds the committed document to;
* the reader of an engine action's neighbourhood (`read_action_graph`, one `get_all_related_elements` call) that the
  engine host uses to refuse a step 2 whose previous step did not complete with the link's guard (§5.2 test 4);
* the reader of one process run (`read_process_run`) and the grouping of engine actions into one run line per
  process instance (`group_by_process`, `run_line_words`): M4, what Understanding shows.

Shapes are the ones captured live (read-only) on 2026-10-15, M2: `tests/fixtures/egeria_6_2/engine_action__*` and
`process_instance__*`. What they settled:

* every engine action of a process run carries an `Anchors` classification whose anchor is the
  `GovernanceActionProcessInstance`, and a `processName` that is the instance's qualifiedName
  (`<process qualifiedName>@<start ms>::<uuid>`);
* actions are linked by `ControlFlow` relationships carrying `guard` and `mandatoryGuard` (end 1 = the earlier
  action); the instance links to the FIRST action by `ActionRequester`;
* the native survey service does NOT add a `surveyReport` target to its own action: it passes the report GUID as a new
  action target to the NEXT action, which carries `surveyReport` and the original asset target under the name the
  process was started with (`newAsset` in the native create-and-survey process).
"""
from __future__ import annotations

import functools
import logging
from dataclasses import dataclass, field

from resource_explorer.step_outcome import TRIGGERED_TWICE_SENTENCE

log = logging.getLogger(__name__)

#: The kind `technology_type_processes.yaml` lists a mixed process under.
KIND_MIXED_PROCESS = "mixed_process"

#: The survey guard a mixed process chains on, and the action-target name a native survey hands the next step.
SURVEY_COMPLETED = "survey-completed"
SURVEY_REPORT_TARGET = "surveyReport"

#: The routing guard RE's definition steps complete with beside their survey guard (`step_outcome.ROUTING_GUARD`).
_ROUTING_GUARD = "Any"

#: Egeria's terminal "it worked" word for an engine action, and its "still going" words.
_DONE = "COMPLETED"
_ACTIVE = frozenset({"REQUESTED", "APPROVED", "WAITING", "ACTIVATING", "IN_PROGRESS"})
#: The statuses an engine action's completion leaves it in (`record_completion_status`): ACTIONED -> COMPLETED,
#: INVALID, FAILED. Egeria starts the next steps from any of them when a link's guard matches.
_ENDED = frozenset({_DONE, "INVALID", "FAILED"})

#: An instance is read by following ControlFlow from its first action; a run longer than this is not one RE declared.
MAX_STEPS = 20


@dataclass(frozen=True)
class ProcessStep:
    number: int
    qualified_name: str              # the GovernanceActionProcessStep
    display_name: str
    description: str
    #: The GovernanceActionType this step stands for (the content pack's, or RE's). A process step cannot point at a
    #: type in Egeria; it carries the type's executor instead (`governance_engine` + `request_type`), and names the
    #: type in its Additional Properties.
    governance_action_type: str
    governance_engine: str
    request_type: str
    runs_on: str                     # "egeria" (a Java engine host) | "resource-explorer" (RE's engine host)
    label: str                       # what the run line calls the step

    @property
    def produced_guards(self) -> tuple[str, ...]:
        from resource_explorer.engine_host import SURVEY_GUARDS

        return SURVEY_GUARDS


@dataclass(frozen=True)
class ProcessLink:
    from_step: int
    to_step: int
    guard: str
    mandatory: bool


@dataclass(frozen=True)
class MixedProcess:
    key: str
    qualified_name: str
    display_name: str
    #: The run line's name for the process ("Postgres survey then derived").
    short_name: str
    description: str
    entity_type: str
    steps: tuple[ProcessStep, ...]
    links: tuple[ProcessLink, ...]

    def step(self, number: int) -> ProcessStep:
        return next(s for s in self.steps if s.number == number)

    @property
    def first_step(self) -> ProcessStep:
        return self.step(min(s.number for s in self.steps))

    @property
    def re_steps(self) -> tuple[ProcessStep, ...]:
        return tuple(s for s in self.steps if s.runs_on == "resource-explorer")


def _postgres_survey_then_derived() -> MixedProcess:
    from resource_explorer import engine_host as eh

    qn = "GovActionProcess::ResourceExplorer::PostgresSurveyThenDerived"
    step_qn = "GovActionProcessStep::ResourceExplorer::PostgresSurveyThenDerived::"
    derive_rt = "re-database-staleness"
    return MixedProcess(
        key="postgres_survey_then_derived",
        qualified_name=qn,
        display_name="Postgres survey then derived",
        short_name="Postgres survey then derived",
        description=("Egeria's own PostgreSQL survey of the database, then Resource Explorer's database staleness "
                     "derived from the report that survey just wrote. Step 2 runs only when step 1 completes "
                     "survey-completed."),
        entity_type="database",
        steps=(
            ProcessStep(1, step_qn + "1-PostgreSQLSurvey", "Postgres survey then derived — 1 PostgreSQL survey",
                        "Egeria's native PostgreSQL database survey (Java, the content pack's PostgreSQLSurvey "
                        "engine).",
                        # The content pack's type, as RE's own Run initiates it (technology_type_processes.yaml);
                        # its executor read live (M2, 2026-10-15): engine PostgreSQLSurvey, survey-postgres-database.
                        governance_action_type="PostgreSQLSurvey::survey-postgres-database",
                        governance_engine="PostgreSQLSurvey", request_type="survey-postgres-database",
                        runs_on="egeria", label="PostgreSQLSurvey"),
            ProcessStep(2, step_qn + "2-" + derive_rt, "Postgres survey then derived — 2 database staleness",
                        "Resource Explorer's database staleness, derived from step 1's survey report (the "
                        "surveyReport action target). Run by Resource Explorer's engine host.",
                        governance_action_type=eh.governance_action_type_qn_for(derive_rt),
                        governance_engine=eh.ENGINE_QUALIFIED_NAME, request_type=derive_rt,
                        runs_on="resource-explorer", label=derive_rt),
        ),
        links=(ProcessLink(1, 2, SURVEY_COMPLETED, True),),
    )


#: The mixed processes RE declares. One in this slice (§5.4: re-db-derived as step 2 is step 5).
MIXED_PROCESSES: dict[str, MixedProcess] = {"postgres_survey_then_derived": _postgres_survey_then_derived()}


def by_qualified_name(qualified_name: str) -> MixedProcess | None:
    return next((p for p in MIXED_PROCESSES.values() if p.qualified_name == qualified_name), None)


# ── RE's own Survey Definitions as Egeria processes (engine host case 3) ────────
#
# SLICE-ENGINE-HOST-CASE3-RE-DEFINITIONS-DISPATCHED-BY-EGERIA.md: a Survey Definition RE publishes
# (`GovActionProcess::<group>` with its `GovActionProcessStep::<group>::<step key>` steps, chained by authored
# `NextGovernanceActionProcessStep` links) is run by Egeria, one engine action per step, each claimed by RE's engine
# host. The same reader and run line serve it as a mixed process: its steps are read from the definition's own
# document (`survey_definition_docs`), every one served by RE's engine under the request type of its step key.

#: Case 3 (H1): the same process step started twice in one process run (duplicate NextGovernanceActionProcessStep
#: links each fire). RE's engine host completes the second survey-invalid with this, never runs it, and emits no
#: routing guard, so it starts nothing. Readers skip such an action: it is not a step of the run.


def is_duplicate_trigger(view: EngineActionView) -> bool:
    """An action RE's engine host refused as a second start of a step already run in the same process run."""
    return TRIGGERED_TWICE_SENTENCE in (view.message or "")


#: The kind the native-surveys pane lists an RE definition's Egeria-coordinated runs under.
KIND_RE_DEFINITION = "re_definition"

DEFINITION_PREFIX = "GovActionProcess::"


def definition_qn_for_instance_name(process_name: str) -> str:
    """The RE Survey Definition (`GovActionProcess::<group>`) an engine action's `processName`
    (`<process QN>@<start>::<uuid>`) belongs to, or "" when it is not one RE authored (a mixed process, a content
    pack's process, or an action started alone)."""
    qn = (process_name or "").split("@", 1)[0]
    if not qn.startswith(DEFINITION_PREFIX) or by_qualified_name(qn) is not None:
        return ""
    return qn if definition_process(qn) is not None else ""


def definition_process(qualified_name: str) -> MixedProcess | None:
    """An RE Survey Definition as a declared process: its steps in document order, each run by RE's engine host
    under its step key's request type, and its authored links. None when no document declares it. Cached: the
    documents are the deployed definitions and change only with a deploy."""
    return _definition_process(qualified_name or "")


@functools.lru_cache(maxsize=64)
def _definition_process(qualified_name: str) -> MixedProcess | None:
    from resource_explorer import engine_host as eh
    from resource_explorer.surveyors.survey_definition_docs import documented_definitions

    if not (qualified_name or "").startswith(DEFINITION_PREFIX):
        return None
    group = qualified_name[len(DEFINITION_PREFIX):]
    doc = documented_definitions().get(group)
    if doc is None:
        return None
    numbers = {key: i + 1 for i, key in enumerate(doc.steps)}
    steps = tuple(
        ProcessStep(numbers[key], (doc.step_info.get(key) or {}).get("qualified_name")
                    or f"GovActionProcessStep::{group}::{key}",
                    (doc.step_info.get(key) or {}).get("display_name") or key, doc.descriptions.get(key, ""),
                    governance_action_type=eh.governance_action_type_qn(key),
                    governance_engine=eh.ENGINE_QUALIFIED_NAME, request_type=eh.request_type_for_step(key),
                    runs_on="resource-explorer", label=key)
        for key in doc.steps)
    links = tuple(ProcessLink(numbers[a], numbers[b], guard, False)
                  for a, b, guard in doc.links if a in numbers and b in numbers)
    return MixedProcess(key=group, qualified_name=qualified_name, display_name=doc.display_name or group,
                        short_name=doc.display_name or group, description=doc.description,
                        entity_type=doc.resource_type, steps=steps, links=links)


def declared_process(qualified_name: str) -> MixedProcess | None:
    """A process RE declares, by qualifiedName: a mixed process, else one of RE's Survey Definitions."""
    return by_qualified_name(qualified_name) or definition_process(qualified_name)


def expected_steps_record(qualified_name: str) -> dict:
    """What a run stores of its definition AT SUBMISSION (`declared.expected_steps`): the request types of its steps
    in order, as the definition declares them now. A run is graded against this, never against the definition as it
    is later (a republished definition with more steps must not make a finished run look unfinished). {} when the
    process is not one RE declares. The one place that decides the list: every writer of a definition run row uses it."""
    process = declared_process(qualified_name)
    return {"expected_steps": [st.request_type for st in process.steps]} if process else {}


def process_for_instance_name(process_name: str) -> MixedProcess | None:
    """The declared process an instance's qualifiedName (`<process QN>@<start>::<uuid>`) belongs to."""
    return declared_process((process_name or "").split("@", 1)[0])


#: The relationship from a GovernanceActionProcessStep to the next step(s) of its process (end 1 = this step).
NEXT_STEP_RELATIONSHIP = "NextGovernanceActionProcessStep"


def parse_next_step_guards(answer) -> list[str]:
    """Pure: the guards of a process step's outgoing `NextGovernanceActionProcessStep` links, from a
    `get_all_related_elements(step guid, graph_query_depth=1)` answer ("" for a link with no guard, which Egeria
    always follows). [] when the step has no next step (the last step of its process)."""
    if not isinstance(answer, dict):
        raise ProcessUnreadable("Egeria answered the process step's relationships with "
                                + " ".join(str(answer).split())[:200])
    out = []
    for entry in answer.get("elementList") or []:
        if ((entry.get("type") or {}).get("typeName")) != NEXT_STEP_RELATIONSHIP or entry.get("elementAtEnd1"):
            continue                                   # a link FROM the previous step to this one
        out.append(str(_rel_props(entry).get("guard") or ""))
    return out


def read_next_step_guards(expert, step_guid: str) -> list[str]:
    """The guards a process step's next-step links take, read live (read-only). Egeria's exact "No elements found"
    is a step with no relationships at all, so no next step."""
    from resource_explorer.native_survey_run import is_exact_absent

    answer = expert.get_all_related_elements(step_guid, graph_query_depth=1)
    if is_exact_absent(answer):
        return []
    return parse_next_step_guards(answer)


def source_report_steps() -> frozenset[str]:
    """The request types of RE steps that derive from the previous step's report (`surveyReport` target)."""
    return frozenset(s.request_type for p in MIXED_PROCESSES.values() for s in p.re_steps)


# ── reading an engine action's neighbourhood (M2 shapes) ───────────────────────

class ProcessUnreadable(RuntimeError):
    """Egeria's answer for an engine action or process instance was not an element graph."""


@dataclass
class Neighbour:
    guid: str
    guard: str = ""
    mandatory: bool = False
    status: str = ""
    completion_guards: list[str] = field(default_factory=list)
    request_type: str = ""


@dataclass
class EngineActionView:
    """One engine action as `get_all_related_elements(guid, graph_query_depth=1)` shows it."""
    guid: str
    status: str = ""
    request_type: str = ""
    requester: str = ""
    process_name: str = ""
    step_name: str = ""
    #: The GovernanceActionProcessInstance it is anchored to; "" for an action started alone.
    instance_guid: str = ""
    completion_guards: list[str] = field(default_factory=list)
    message: str = ""
    report_guids: list[str] = field(default_factory=list)
    targets: list[tuple[str, str]] = field(default_factory=list)
    previous: list[Neighbour] = field(default_factory=list)
    next: list[Neighbour] = field(default_factory=list)
    #: Set when the action's process could not be read: it renders alone, with "process unknown".
    unreadable: str = ""

    @property
    def report_guid(self) -> str:
        return self.report_guids[0] if len(set(self.report_guids)) == 1 else ""

    @property
    def process_ended(self) -> bool:
        """RE's engine host said the process ended at this step (no authored link took its guards), read from the FULL
        completion message: the note is at its end, past what a stored step keeps of the message."""
        from resource_explorer.step_outcome import PROCESS_ENDED_PREFIX

        return PROCESS_ENDED_PREFIX in (self.message or "")

    @property
    def succeeded(self) -> bool:
        return self.status == _DONE and SURVEY_COMPLETED in self.completion_guards

    @property
    def active(self) -> bool:
        return self.status in _ACTIVE


def _value(entry):
    """One property value from a propertyValueMap entry: primitive, enum, array or map."""
    if not isinstance(entry, dict):
        return entry
    if "primitiveValue" in entry:
        return entry["primitiveValue"]
    if "symbolicName" in entry:
        return entry["symbolicName"]
    if "arrayValues" in entry:
        inner = (entry.get("arrayValues") or {}).get("propertyValueMap") or {}
        return [_value(inner[k]) for k in sorted(inner, key=lambda k: int(k) if str(k).isdigit() else 0)]
    if "mapValues" in entry:
        inner = (entry.get("mapValues") or {}).get("propertyValueMap") or {}
        return {k: _value(v) for k, v in inner.items()}
    return None


def _props(element: dict) -> dict:
    pvm = ((element or {}).get("elementProperties") or {}).get("propertyValueMap") or {}
    return {k: _value(v) for k, v in pvm.items()}


def _rel_props(entry: dict) -> dict:
    rp = (entry or {}).get("relationshipProperties") or {}
    return dict(rp.get("propertiesAsStrings") or {})


def _guards(value) -> list[str]:
    if isinstance(value, list):
        return [str(v) for v in value if v not in (None, "")]
    if isinstance(value, dict):
        return [str(v) for _k, v in sorted(value.items()) if v not in (None, "")]
    return []


def instance_of(element: dict) -> str:
    """The GovernanceActionProcessInstance an engine action is anchored to, or ""."""
    for c in (element or {}).get("classifications") or []:
        if c.get("classificationName") != "Anchors":
            continue
        props = ((c.get("classificationProperties") or {}).get("propertiesAsStrings") or {})
        if props.get("anchorTypeName") == "GovernanceActionProcessInstance":
            return str(props.get("anchorGUID") or "")
    return ""


def parse_action_graph(answer) -> EngineActionView:
    """Pure: one engine action from a `get_all_related_elements(guid, graph_query_depth=1)` answer."""
    if not isinstance(answer, dict) or not isinstance(answer.get("startingElement"), dict):
        raise ProcessUnreadable("Egeria answered the engine action's relationships with "
                                + " ".join(str(answer).split())[:200])
    start = answer["startingElement"]
    p = _props(start)
    view = EngineActionView(
        guid=str(start.get("elementGUID") or ""), status=str(p.get("activityStatus") or ""),
        request_type=str(p.get("requestType") or ""), requester=str(p.get("requesterUserId") or ""),
        process_name=str(p.get("processName") or ""), step_name=str(p.get("processStepName") or ""),
        instance_guid=instance_of(start), completion_guards=_guards(p.get("completionGuards")),
        message=str(p.get("completionMessage") or ""))
    for entry in answer.get("elementList") or []:
        rel = ((entry.get("type") or {}).get("typeName")) or ""
        other = entry.get("element") or {}
        guid = str(other.get("elementGUID") or "")
        if not guid:
            continue
        rp = _rel_props(entry)
        if rel == "ActionTarget" and not entry.get("elementAtEnd1"):
            view.targets.append((str(rp.get("actionTargetName") or ""), guid))
        elif rel == "ReportOriginator" and not entry.get("elementAtEnd1"):
            view.report_guids.append(guid)
        elif rel == "ControlFlow":
            op = _props(other)
            n = Neighbour(guid=guid, guard=str(rp.get("guard") or ""),
                          mandatory=str(rp.get("mandatoryGuard") or "").lower() == "true",
                          status=str(op.get("activityStatus") or ""),
                          completion_guards=_guards(op.get("completionGuards")),
                          request_type=str(op.get("requestType") or ""))
            (view.previous if entry.get("elementAtEnd1") else view.next).append(n)
    return view


def read_action_graph(expert, guid: str) -> EngineActionView:
    """One engine action and its neighbours (`MetadataExpert.get_all_related_elements`, read-only). Raises
    `ProcessUnreadable` for any answer that is not an element graph, so an unreadable action is never a lone one.
    Egeria's exact "No elements found" (an action with no relationship at all, so no previous step) is a lone action."""
    from resource_explorer.native_survey_run import is_exact_absent

    answer = expert.get_all_related_elements(guid, graph_query_depth=1)
    if is_exact_absent(answer):
        return EngineActionView(guid=guid)
    return parse_action_graph(answer)


def read_action_views(expert, guids: list[str]) -> list[EngineActionView]:
    """Each action's view; one that cannot be read is kept, marked `unreadable`, so it still renders (alone, with
    "process unknown") rather than disappearing."""
    out = []
    for guid in guids:
        try:
            out.append(read_action_graph(expert, guid))
        except Exception as exc:  # noqa: BLE001 -- shown as "process unknown", never dropped
            out.append(EngineActionView(guid=guid, unreadable=" ".join(f"{type(exc).__name__}: {exc}".split())[:200]))
    return out


def previous_step_refusal(view: EngineActionView) -> str:
    """Why an action of RE's must not run as a later process step, or "". An action with no previous action (started
    alone, or the first step) is fine. Otherwise EVERY previous action must have ENDED (COMPLETED, INVALID or FAILED:
    Egeria creates the next action from the completion, whatever its status) with the link's guard among its
    completion guards: Egeria should never create the action if not, so this pins RE's own refusal. Case 3 (review
    M1): an RE definition's step that ends INVALID or FAILED still emits `Any`, and the step after it runs."""
    for prev in view.previous:
        if prev.status not in _ENDED or (prev.guard and prev.guard not in prev.completion_guards):
            return (f"previous step {prev.guid} is {prev.status or 'of unknown status'} with "
                    f"{', '.join(prev.completion_guards) or 'no guard'}, not {prev.guard or 'a completion'}")
    return ""


def read_process_run(expert, instance_guid: str) -> list[EngineActionView]:
    """Every engine action of one process instance, in run order: the first (ActionRequester from the instance),
    then each ControlFlow successor. Raises `ProcessUnreadable` when the instance or an action cannot be read."""
    from resource_explorer.native_survey_run import is_exact_absent

    answer = expert.get_all_related_elements(instance_guid, graph_query_depth=1)
    if is_exact_absent(answer):
        return []                     # the instance has no action linked yet
    if isinstance(answer, str):
        raise ProcessUnreadable(f"process instance {instance_guid}: {answer}")
    if not isinstance(answer, dict):
        raise ProcessUnreadable(f"process instance {instance_guid}: Egeria answered {type(answer).__name__}")
    queue = [str((e.get("element") or {}).get("elementGUID") or "") for e in answer.get("elementList") or []
             if ((e.get("type") or {}).get("typeName")) == "ActionRequester"
             and ((e.get("element") or {}).get("type") or {}).get("typeName") == "EngineAction"]
    seen: dict[str, EngineActionView] = {}
    while queue and len(seen) < MAX_STEPS:
        guid = queue.pop(0)
        if not guid or guid in seen:
            continue
        view = read_action_graph(expert, guid)
        seen[guid] = view
        queue.extend(n.guid for n in view.next)
    return list(seen.values())


# ── grouping into run lines (M4) ───────────────────────────────────────────────

@dataclass
class RunLine:
    """One run as Understanding shows it: the actions of one process instance, or one lone action."""
    instance_guid: str
    process_name: str
    steps: list[EngineActionView]
    note: str = ""

    @property
    def process(self) -> MixedProcess | None:
        return process_for_instance_name(self.process_name)

    @property
    def requester(self) -> str:
        return next((s.requester for s in self.steps if s.requester), "")


def _order(steps: list[EngineActionView]) -> list[EngineActionView]:
    """Run order inside one instance: an action after every action it follows (ControlFlow)."""
    by_guid = {s.guid: s for s in steps}

    def depth(s, seen=()):
        prev = [by_guid[p.guid] for p in s.previous if p.guid in by_guid and p.guid not in seen]
        return 1 + max((depth(p, (*seen, s.guid)) for p in prev), default=0)

    return sorted(steps, key=lambda s: (depth(s), s.guid))


def group_by_process(views: list[EngineActionView], *, duplicates: frozenset[str] | set[str] = frozenset()
                     ) -> list[RunLine]:
    """Engine actions grouped by the process instance they are anchored to: one `RunLine` per instance with its
    actions in run order; an action with no instance is a line of its own; an action whose process could not be
    read (`unreadable`) is a line of its own with the note "process unknown". Input order of first appearance.

    `duplicates`: the actions RE's own runs rows mark as refused second starts (`duplicate_trigger`), skipped by that
    flag; the completion text is the fallback for an action RE did not persist."""
    lines: dict[str, RunLine] = {}
    out: list[RunLine] = []
    for v in views:
        if v.guid in duplicates or is_duplicate_trigger(v):
            continue        # a refused second start of a step: not a step of the run (no false failure, no extra step)
        if v.unreadable:
            out.append(RunLine("", v.process_name, [v], note="process unknown"))
            continue
        if not v.instance_guid:
            out.append(RunLine("", "", [v]))
            continue
        line = lines.get(v.instance_guid)
        if line is None:
            line = lines[v.instance_guid] = RunLine(v.instance_guid, v.process_name, [])
            out.append(line)
        line.steps.append(v)
    for line in out:
        line.steps = _order(line.steps)
    return out


SURVEY_INVALID = "survey-invalid"


def is_refused_step(step: dict) -> bool:
    """A stored step (status, guards) that RE's engine host REFUSED: the launcher said it cannot run here (completed
    `survey-invalid`, never `survey-completed`). A refusal is not a failure; `survey-failed` and any other end is."""
    guards = step.get("guards") or []
    return (step.get("status") in (_DONE, "INVALID") and SURVEY_INVALID in guards
            and SURVEY_COMPLETED not in guards)


def _step_words(n: int, label: str, view: EngineActionView | None, *, refusal_ok: bool = False) -> str:
    if view is None:
        return f"step {n} {label}"
    if refusal_ok and view.status in (_DONE, "INVALID") and SURVEY_INVALID in view.completion_guards \
            and SURVEY_COMPLETED not in view.completion_guards:
        return f"step {n} {label} ○ not run"        # ⊘ is cancelled (glyphs.js); a refusal is nothing failed
    if view.status == _DONE:
        rep = view.report_guid
        # The routing guard an RE definition's step also completes with (case 3) is not news to a reader.
        other = [g for g in view.completion_guards if g not in (SURVEY_COMPLETED, _ROUTING_GUARD)]
        return (f"step {n} {label} ✓" + (f" {', '.join(other)}" if other else "")
                + (f" report {rep[:8]}" if rep else (" (no report linked)" if view.succeeded else "")))
    if view.active:
        if view.status in ("APPROVED", "REQUESTED"):
            return f"step {n} {label} not claimed yet"
        return f"step {n} {label} {'running' if view.status == 'IN_PROGRESS' else 'waiting'}"
    guard = ", ".join(g for g in view.completion_guards if g != _ROUTING_GUARD)
    if refusal_ok and view.status in (_DONE, "INVALID") and SURVEY_INVALID in view.completion_guards \
            and SURVEY_COMPLETED not in view.completion_guards:
        return f"step {n} {label} ○ not run"         # not a failure: the launcher said it cannot run here
    return f"step {n} {label} ✗ {guard or view.status or 'status unknown'}"


#: Case 3, C4: where a definition's Egeria-coordinated run was started, for its run line.
STARTED_IN_EGERIA = "egeria"     # initiated in Egeria (Dr.Egeria, or another process): RE only ran its steps
STARTED_IN_RE = "re"             # initiated from RE ("coordinated by Egeria"), then coordinated by Egeria
STARTED_IN_RE_ONLY = "re-only"   # run by RE alone (RE's own executor), not through an Egeria process
#: Who coordinated the run, then who started it. The time is the reader's local time, added on the page from the
#: row's own stored submission time (never a UTC date in the words).
_STARTED_WORDS = {
    STARTED_IN_EGERIA: ("coordinated by Egeria", "started in Egeria by"),
    STARTED_IN_RE: ("coordinated by Egeria", "started by"),
    STARTED_IN_RE_ONLY: ("run by RE", "started by"),
}


def run_line_words(line: RunLine, *, started: str = "", started_in: str = "", started_by: str = "",
                   expected_steps: list[str] | None = None) -> str:
    """The run line: "Postgres survey then derived · step 1 PostgreSQLSurvey ✓ report cb92eedc · step 2
    re-database-staleness ✓ report 1a2b3c4d · started by peterprofile 10-15". A step Egeria never created after a
    failed step says "step 2 not started · step 1 failed", never "pending". `started_in` (case 3) says where the run
    was started ("started in Egeria by <requester>", "started in RE by <person>"); `started_by` names the person when
    RE recorded who pressed, else the actions' requester.

    `expected_steps` is the run's own stored step list (`declared.expected_steps`, request types in order): the line
    walks THAT list, the one `derive_mixed_state` grades against, never the definition as it is now. None (a row
    stored before the list existed) walks the steps the run has, plus the one a failed or active step is waited
    on for; no "not started yet" is ever said of a step the run never had."""
    process = line.process
    tail = []
    who = started_by or line.requester
    coordinated, started_word = _STARTED_WORDS.get(started_in, ("", "started by"))
    if coordinated:
        tail.append(coordinated)
    if who:
        tail.append(f"{started_word} {who}" + (f" {started}" if started else ""))
    if process is None:
        parts = [line.process_name.split("@", 1)[0] or (line.steps[0].request_type if line.steps else "engine action")]
        parts += [_step_words(i + 1, s.request_type, s) for i, s in enumerate(line.steps)]
        if line.note:
            parts.append(line.note)
        return " · ".join(parts + tail)
    parts = [process.short_name]
    by_request = {s.request_type: s for s in line.steps}
    declared_by_type = {st.request_type: st for st in process.steps}
    if expected_steps:
        walk = [(i + 1, rt, declared_by_type[rt].label if rt in declared_by_type else rt)
                for i, rt in enumerate(expected_steps)]
    else:
        walk = [(st.number, st.request_type, st.label) for st in sorted(process.steps, key=lambda s: s.number)]
    previous: EngineActionView | None = None
    previous_number = 0
    for number, request_type, label in walk:
        view = by_request.get(request_type)
        if (view is None and previous is not None and not previous.active and not previous.succeeded
                and not _always_follows(process, previous_number, number, previous)):
            parts.append(f"step {number} not started · step {previous_number} failed")
            break
        if view is None and previous is not None and previous.active:
            parts.append(f"step {number} {label} waits for step {previous_number}")
            break
        if view is None:
            if expected_steps:      # a step this run's own list has and it has not reached yet
                parts.append(f"step {number} {label} not started yet")
            break                   # legacy row: it never had this step, so nothing is said of it
        parts.append(_step_words(number, label, view, refusal_ok=_is_definition(process)))
        previous, previous_number = view, number
    if line.note:
        parts.append(line.note)
    return " · ".join(parts + tail)


def _is_definition(process: MixedProcess) -> bool:
    """An RE Survey Definition run as a process (its refused steps are not a failure of the run)."""
    return process.qualified_name.startswith(DEFINITION_PREFIX) and by_qualified_name(process.qualified_name) is None


def _always_follows(process: MixedProcess, from_step: int, to_step: int, previous: EngineActionView) -> bool:
    """Whether Egeria starts `to_step` after `from_step` whatever `from_step` produced: an RE definition's `Any` link,
    which its steps always emit (case 3, review M1), or a link with no guard. Then a step after a failed one is still
    to come, never "not started"."""
    for link in process.links:
        if link.from_step == from_step and link.to_step == to_step:
            return link.guard in ("", _ROUTING_GUARD) and (not link.guard or link.guard in previous.completion_guards)
    return False


def modernise_words(words: str) -> str:
    """A run line stored before 2026-10-16 says "⊘ refused" for a step the launcher would not run. ⊘ is "cancelled"
    in the glyph table and a refusal is neither a failure nor a cancel: it is "○ not run" (designer Q7)."""
    return (words or "").replace(" ⊘ refused", " ○ not run")


def trim_words_to_steps(words: str, graded: int) -> str:
    """A run line stored before the fix: drop a "step N ... not started yet" / "waits for step M" part for a step
    beyond the `graded` steps the run is graded on (the current definition's later steps, which this run never had)."""
    import re

    keep = [p for p in (words or "").split(" · ")
            if not ((m := re.match(r"step (\d+) .*(not started yet|waits for step \d+)$", p)) and int(m.group(1)) > graded)]
    return " · ".join(keep)


def run_line_dict(line: RunLine, *, started: str = "", started_in: str = "", started_by: str = "",
                  expected_steps: list[str] | None = None) -> dict:
    """The run line for a page: its words and each step's action, status, guards and report."""
    return {"instance_guid": line.instance_guid, "process_name": line.process_name, "note": line.note,
            "words": run_line_words(line, started=started, started_in=started_in, started_by=started_by,
                                    expected_steps=expected_steps),
            "steps": [{"action_guid": s.guid, "request_type": s.request_type, "status": s.status,
                       "guards": list(s.completion_guards), "report_guid": s.report_guid,
                       "message": s.message[:500],
                       # From the FULL message, before it is cut: the engine host appends this note at its end.
                       "process_ended": s.process_ended} for s in line.steps]}
