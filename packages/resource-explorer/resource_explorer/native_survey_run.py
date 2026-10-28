"""Launching an Egeria-native survey from RE, and reading it back.

Design brief: `BRIEF-NATIVE-EGERIA-SURVEY-LAUNCH.md` (the
authoritative source for the slice and its six-point gate). Implementation
notes: `NATIVE-EGERIA-SURVEY-LAUNCH-IMPLEMENTED.md`.

What this is: a *direct* pyegeria submission of the survey Egeria's own survey
action engine already knows for a resource's technology type
(`configdata/technology_type_processes.yaml`, kind `survey_existing`), followed
by a persisted, read-only read-back of what Egeria says happened. **Prefect is
not in this path** and nothing here touches `step_runs.flow_run_id` /
`dispatch_failed` -- the whole-definition Prefect run is a different mechanism
with its own columns.

The rule the whole module is built around: **every status word on a row is
derived from persisted proof, never from which branch ran.**
`derive_native_state` is the one function that turns rows into words, and it is
pure so a table test can walk every combination:

    submitted to Egeria   the engine-action GUID is stored on the step_runs row
    running               Egeria's own status, read back, with the read time
    complete              status COMPLETED *and* a stored report GUID *and* the
                          report's annotations stored, count matching the count
                          recorded when they were read
    <Egeria's word>       Egeria's own status word and message when it failed;
                          never "complete", never blank

Two facts about Egeria found live (2026-09-30, read-only) that shape this:

* **An engine action is linked to the report it produced by a
  `ReportOriginator` relationship** (EngineAction -> SurveyReport). The earlier
  timestamp attribution in `egeria_async_survey_result.py` -- and its note that
  "no direct EngineAction-to-report relationship was found" -- predates this.
  The report is found by that relationship, never by "newest after time T", so
  two overlapping runs cannot be confused.
* **A native survey is slow** (a 157-table database took ~14 minutes), so this
  never blocks: submit returns as soon as Egeria accepts, and the read-back is a
  separate, repeatable step driven by the UI's poll and by a sweep.

Identity: nothing here reads the caller from a ContextVar. `submitted_by` is
passed in by the route, and the sweep passes none. Anything run in a thread
must pass identity explicitly (reference: ContextVar identity in threads).
"""
from __future__ import annotations

import contextlib
import functools
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterator, Protocol

from resource_explorer.mixed_process import KIND_MIXED_PROCESS, KIND_RE_DEFINITION
from resource_explorer.registry import NATIVE_SURVEY_STEP_PREFIX
from resource_explorer.surveyors.technology_type_processes import (
    KIND_CATALOG_AND_SURVEY,
    KIND_DELETE,
    KIND_SURVEY_EXISTING,
    NativeProcess,
    get_native_processes,
)

log = logging.getLogger(__name__)

# Egeria's ActivityStatus symbolic names (same sets `egeria_delegated_step`
# documents from GovernanceActionStatus.java's enum table). Imported rather than
# re-declared so there is one list of "still going" words.
from resource_explorer.surveyors.egeria_delegated_step import (  # noqa: E402
    _ACTIVE_STATUSES as ACTIVE_STATUSES,
    _SUCCESS_STATUSES as SUCCESS_STATUSES,
)

#: States (see `derive_native_state`). Kept as plain strings: they travel to the
#: frontend, which maps each to a glyph in `next/glyphs.js`.
NOT_RUN = "not_run"
SUBMIT_FAILED = "submit_failed"
SUBMITTED = "submitted"
RUNNING = "running"
AWAITING_REPORT = "awaiting_report"
REPORT_INCOMPLETE = "report_incomplete"
COMPLETE = "complete"
#: A definition run in which every step ended and some were REFUSED (survey-invalid: the launcher said the step cannot run
#: here, e.g. the credential cannot read what it needs). Not a failure of the run; red is for a step that failed.
PARTIAL = "partial"
FAILED = "failed"
UNREADABLE = "unreadable"
CANNOT_RUN = "cannot_run"


#: Egeria's "not started yet" words; IN_PROGRESS is the one that means the engine has the action.
#: Waiting to start and running are two states, never one.
WAITING_STATUSES = frozenset({"REQUESTED", "APPROVED", "WAITING", "ACTIVATING"})

#: A survey action still active this long after it was REQUESTED is flagged "stalled?". Measured from the
#: request (the stall limit is unchanged; Egeria's start time, when stored, only refines the display). A native survey takes minutes, so
#: an action still waiting after an hour has not been picked up, and one still running after six hours,
#: counting the wait, is far beyond any survey seen. Flag only: RE never cancels or retries on this.
STALL_AFTER_WAITING_S = 60 * 60
STALL_AFTER_RUNNING_S = 6 * 60 * 60


class NativeSurveyError(RuntimeError):
    """A submission that did not produce an engine action."""


class NativeSurveyCannotRun(NativeSurveyError):
    """RE knows why this survey cannot be run for this resource. The message is
    what the row shows -- it is a reason, not an error."""


class NativeSurveyStale(NativeSurveyCannotRun):
    """The Egeria element RE had stored a pointer to no longer exists (Egeria answered that it
    does not -- a failed read is never this). A registration that existed and is gone, said
    plainly; the row offers to register the server again."""


class NativeStatusUnreadable(NativeSurveyError):
    """Egeria's current status could not be read just now, so nothing is cancelled (fail closed). Route: 502."""


#: How a run was started, stamped on its step_runs row (`declared` JSON; no column). Only an action started by
#: `initiate_gov_action_type` is a single engine action that `cancelEngineAction` may be pointed at; a
#: governance-action PROCESS returns a process-instance GUID, which is not an engine action.
STARTED_AS_ACTION_TYPE = "gov_action_type"
STARTED_AS_PROCESS = "process"


def cancellable_kind(run: dict) -> bool:
    """ALLOWLIST: may this row's GUID be handed to Egeria's engine-action cancel? Yes only for a single-action
    survey: the row is stamped as started by a governance action type. Anything else -- a process instance, a registration, an unknown
    process name -- is no."""
    import json

    from resource_explorer.surveyors.technology_type_processes import kind_of

    raw = run.get("declared")
    try:
        declared = raw if isinstance(raw, dict) else json.loads(raw or "{}")
    except (ValueError, TypeError):
        declared = {}
    how = declared.get("started_as") if isinstance(declared, dict) else None
    process_qn = run.get("executor_ref") or (run.get("step_key") or "")[len(NATIVE_SURVEY_STEP_PREFIX):]
    kind = kind_of(process_qn)
    if kind == KIND_CATALOG_AND_SURVEY:
        return False
    # No name fallback: an unstamped (old) row is refused outright. It can be cancelled in Egeria directly.
    return how == STARTED_AS_ACTION_TYPE


def cancel_offered(run: dict) -> bool:
    """The row's `cancellable`: the allowlist AND Egeria's last read succeeded and said waiting or running."""
    return bool(run.get("engine_action_guid")) and cancellable_kind(run) \
        and (run.get("engine_action_status") or "") in ACTIVE_STATUSES \
        and not (run.get("engine_action_read_error") or "")


class NativeActionNotOurs(NativeSurveyError):
    """The engine action GUID is not one RE recorded for this resource. Nothing was sent to Egeria."""


class NativeSurveyBusy(NativeSurveyError):
    """The same survey is already in flight on this resource."""


# ── the port: everything that talks to Egeria ────────────────────────────────


@dataclass(frozen=True)
class ActionRead:
    status: str
    message: str
    #: Egeria's own `requestedStartTime` / `startTime` as ISO UTC (naive), or None when it did not state one.
    requested_at: str | None = None
    started_at: str | None = None


def epoch_ms_to_iso(value) -> str | None:
    """Egeria's epoch-millisecond time (a bare number, or a property value carrying `primitiveValue`) as the
    registry's naive-UTC ISO spelling. Missing, zero or unreadable is None, never a made-up time."""
    if isinstance(value, dict):
        value = value.get("primitiveValue")
    if isinstance(value, bool) or value in (None, ""):
        return None
    try:
        ms = float(value)
    except (TypeError, ValueError):
        return None
    if ms < 1e11:                 # below 1e11 is seconds (or nonsense), not epoch milliseconds: unknown, not 1970
        return None
    try:
        return datetime.fromtimestamp(ms / 1000, timezone.utc).replace(tzinfo=None).isoformat()
    except (OverflowError, OSError, ValueError):
        return None


@dataclass(frozen=True)
class ReportRead:
    guid: str
    at: str
    originator_guid: str
    annotations: list[dict]


class EgeriaSurveyPort(Protocol):
    """The whole Egeria surface this feature uses. Reads are read-only; the one
    write is `initiate`. Every method raises on a transport/API failure -- a
    failure must never be returned as an empty answer (find-absence-as-answer)."""

    def asset_exists(self, guid: str) -> bool: ...
    def initiate(self, process_qualified_name: str, action_target_name: str,
                 target_guid: str) -> str: ...
    def read_action(self, engine_action_guid: str) -> ActionRead: ...
    def cancel(self, engine_action_guid: str) -> None: ...
    def report_guid_for_action(self, engine_action_guid: str) -> str | None: ...
    def read_report(self, report_guid: str) -> ReportRead: ...


def _normalise_answer(text: str) -> str:
    return text.strip().rstrip(".").strip().lower()


@functools.cache
def absent_answers() -> frozenset[str]:
    """pyegeria's OWN "nothing there" strings, taken from its constants so they cannot drift: a by-name
    or by-GUID miss answers NO_ELEMENTS_FOUND ("No elements found", plural); NO_ELEMENT_FOUND (singular)
    is its docstring's spelling. Both are accepted, exactly (case-insensitive, trailing period stripped).

    LAZY on purpose: importing pyegeria runs `pyegeria.view`'s `nest_asyncio.apply()`, which raises
    "Can't patch loop of type uvloop.Loop" when the import happens under uvicorn's running uvloop.
    Nothing reachable from `resource_explorer.web.app` may import pyegeria at module load."""
    from pyegeria.core._globals import NO_ELEMENT_FOUND, NO_ELEMENTS_FOUND

    return frozenset(_normalise_answer(c) for c in (NO_ELEMENTS_FOUND, NO_ELEMENT_FOUND))



def is_exact_absent(res: Any) -> bool:
    """Egeria's exact non-raising "nothing there" answer (see `absent_answers`). A string that only
    CONTAINS "not found" (a user, a type, an index) is a different sentence and is never absence.
    Mirrors `egeria_absence.is_absent` (re/blueprint-container-shape, #556) for string answers; switch to
    that shared helper once #556 is merged."""
    return isinstance(res, str) and _normalise_answer(res) in absent_answers()


def _iso(value: Any) -> str:
    """Egeria's timestamp (ISO string or epoch millis) as an ISO string; '' when
    it is neither. Not guessing: an unparseable value stays absent."""
    if value in (None, "") or isinstance(value, bool):
        return ""
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(value / 1000.0, tz=timezone.utc).isoformat()
        except (ValueError, OverflowError, OSError):
            return ""
    return str(value)


class PyegeriaSurveyPort:
    """The real port. Clients are built lazily, once per instance, with the same
    env-driven connection RE's other Egeria writers use.

    **One event loop per instance, not per call.** pyegeria's sync wrappers call
    `asyncio.get_event_loop()`, which a worker thread does not have, so this
    gives the thread one -- the same shape the publish routes use. But a pyegeria
    client caches an HTTP client bound to the loop it first ran on, so a loop
    created and closed around each call makes the SECOND call on the same client
    fail with "Event loop is closed" (found live, 2026-09-30, the first time a
    real port made two calls). The loop therefore lives as long as the port and
    is closed by `close()` / leaving `port_session`. A port belongs to ONE
    thread; the routes build one per request and the sweep one per pass.
    Never use a port from a thread that already runs an event loop."""

    def __init__(self, identity=None) -> None:
        self._curation = None
        self._expert = None
        self._assets = None
        self._loop = None
        self._identity = identity

    def _enter(self) -> None:
        import asyncio

        if self._loop is None:
            self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)

    def close(self) -> None:
        import asyncio

        if self._loop is not None:
            self._loop.close()
            self._loop = None
            asyncio.set_event_loop(None)

    def principal(self):
        """Who this port acts as (Brief I): the identity it was given, else `current_principal()`
        in the port's own thread — the signed-in Caller on a route, `Daemon(SCHEDULER)` in the
        sweep. Resolved once; a port belongs to one thread."""
        if self._identity is None:
            from resource_explorer.egeria_clients import current_principal

            self._identity = current_principal()
        return self._identity

    def _client(self, cls):
        from resource_explorer.egeria_clients import egeria_client

        # Not scope-shared: the port's clients are bound to the port's own event loop.
        return egeria_client(self.principal(), purpose="native survey", shared=False).of(cls)

    def _get_curation(self):
        if self._curation is None:
            from pyegeria import AutomatedCuration
            self._curation = self._client(AutomatedCuration)
        return self._curation

    def _get_expert(self):
        if self._expert is None:
            from pyegeria import MetadataExpert
            self._expert = self._client(MetadataExpert)
        return self._expert

    def _get_assets(self):
        if self._assets is None:
            from pyegeria import AssetMaker
            self._assets = self._client(AssetMaker)
        return self._assets

    @staticmethod
    def _is_not_found(exc: Exception) -> bool:
        """Gone = a typed PyegeriaNotFoundException, or a PyegeriaAPIException whose related_http_code is
        exactly 404 (the code field, never the message text: a GUID containing "404" inside a timeout
        sentence is NOT gone). Everything else -- unauthorized, transport, timeouts -- is unreadable.

        The one shared classifier, `egeria_absence`: a typed 404 whose Egeria message id is
        OMAG-MULTI-TENANT-404-* is a view server that is down or not known (live probe, 2026-10-13), NOT
        a gone element. Read as gone, it labelled every pointer "gone", raised NativeSurveyStale and let
        registration clear the server pointer while the server was merely down."""
        from resource_explorer.egeria_absence import lookup_exception_is_absent

        return lookup_exception_is_absent(exc)

    def asset_exists(self, guid: str) -> bool:
        from resource_explorer.catalogue_gateway import _guid_of

        self._enter()
        try:
            res = self._get_expert().get_metadata_element_by_guid(guid)
        except Exception as exc:  # noqa: BLE001 -- classified below
            from resource_explorer.egeria_absence import is_not_visible

            # A typed not-found, or Egeria's 400-010 "not visible" (a soft delete) on THIS pointer's own GUID.
            if self._is_not_found(exc) or is_not_visible(exc):
                return False
            raise
        if is_exact_absent(res):
            return False
        if isinstance(res, dict) and _guid_of(res):
            return True
        raise NativeSurveyError("Egeria answered a read of " + guid + " with something other than an element: "
                                + " ".join(str(res).split())[:300])

    def initiate(self, process_qualified_name: str, action_target_name: str,
                 target_guid: str) -> str:
        self._enter()
        guid = self._get_curation().initiate_gov_action_type(
            action_type_qualified_name=process_qualified_name,
            request_source_guids=[],
            action_targets=[{
                "class": "NewActionTarget",
                "actionTargetName": action_target_name,
                "actionTargetGUID": target_guid.strip(),
            }],
        )
        if not guid or guid == "Action not initiated":
            raise NativeSurveyError(
                f"Egeria did not initiate {process_qualified_name!r} "
                f"(it answered {guid!r})")
        return guid

    def read_action(self, engine_action_guid: str) -> ActionRead:
        self._enter()
        element = self._get_expert().get_metadata_element_by_guid(engine_action_guid)
        props = ((element or {}).get("elementProperties") or {}).get("propertyValueMap") or {}
        status = (props.get("activityStatus") or {}).get("symbolicName") or ""
        if not status:
            # Not "UNKNOWN": a response with no status is a read that did not
            # establish one, and the caller records it as such.
            raise NativeSurveyError(
                "Egeria's engine-action element carried no activityStatus")
        message = (props.get("completionMessage") or {}).get("primitiveValue") or ""
        return ActionRead(status=status, message=message,
                          requested_at=epoch_ms_to_iso(props.get("requestedStartTime")),
                          started_at=epoch_ms_to_iso(props.get("startTime")))

    def cancel(self, engine_action_guid: str) -> None:
        """Ask Egeria to cancel an engine action (pyegeria 6.2.1
        `AutomatedCuration.cancel_engine_action(engine_action_guid) -> None`). A WRITE, made as the port's
        principal (the signed-in Caller on a route). Egeria marks the action CANCELLED; whether the engine host
        stops work already running is not verified. Whether it took effect is read back by the caller."""
        self._enter()
        self._get_curation().cancel_engine_action(engine_action_guid)

    def report_guid_for_action(self, engine_action_guid: str) -> str | None:
        self._enter()
        result = self._get_expert().get_related_metadata_elements(
            engine_action_guid, "ReportOriginator",
            body={"class": "GetRequestBody"}, starting_at_end=0)
        if result is None:
            # No answer is not "no report yet" (pyegeria ISSUE-134: up to 6.2.1 a swallowed failure was None).
            raise NativeSurveyError(
                f"Egeria gave no answer for engine action {engine_action_guid}'s report (the client returned nothing)")
        if not isinstance(result, dict):
            return None  # pyegeria's "No element found" string: no report yet
        guids = [
            (entry.get("element") or {}).get("elementGUID")
            for entry in result.get("elementList") or []
        ]
        guids = [g for g in guids if g]
        if not guids:
            return None
        if len(set(guids)) > 1:
            # A survey action produces one report. More than one is something
            # to look at, not something to pick from.
            raise NativeSurveyError(
                f"Egeria links {len(set(guids))} reports to engine action "
                f"{engine_action_guid} ({sorted(set(guids))}); not choosing one")
        return guids[0]

    def process_declared(self, qualified_name: str) -> bool:
        """Is a governance action process with this qualifiedName in Egeria? Read-only. True/False only for an
        element or Egeria's exact "nothing there"; anything else raises (never "not declared" by inference)."""
        from resource_explorer.catalogue_gateway import _guid_of

        self._enter()
        body = {"class": "UniqueNameRequestBody", "name": qualified_name, "namePropertyName": "qualifiedName",
                "forLineage": False, "forDuplicateProcessing": False}
        try:
            res = self._get_expert().get_metadata_element_by_unique_name(
                name=qualified_name, property_name="qualifiedName", body=body)
        except Exception as exc:  # noqa: BLE001 -- only a TYPED not-found is absence
            if self._is_not_found(exc):
                return False
            raise
        if is_exact_absent(res):
            return False
        if isinstance(res, dict) and _guid_of(res):
            return True
        raise NativeSurveyError(f"Egeria answered a lookup of {qualified_name} with something other than an "
                                "element: " + " ".join(str(res).split())[:300])

    def initiate_process_run(self, process_qualified_name: str, action_target_name: str, target_guid: str) -> str:
        """Start a governance action process on one asset (pyegeria 6.2.3
        `AutomatedCuration.initiate_gov_action_process(action_type_qualified_name, ..., action_targets=[...])`).
        A WRITE, as the port's principal. Returns the GovernanceActionProcessInstance GUID (live: the
        catalog-and-survey runs store exactly that GUID)."""
        self._enter()
        guid = self._get_curation().initiate_gov_action_process(
            action_type_qualified_name=process_qualified_name,
            action_targets=[{"class": "NewActionTarget", "actionTargetName": action_target_name,
                             "actionTargetGUID": target_guid.strip()}])
        if not guid or guid == "Action not initiated":
            raise NativeSurveyError(f"Egeria did not initiate {process_qualified_name!r} (it answered {guid!r})")
        return guid

    def read_process_run(self, instance_guid: str) -> list:
        """The engine actions of one process instance, in run order (`mixed_process.read_process_run`). Read-only."""
        from resource_explorer import mixed_process

        self._enter()
        return mixed_process.read_process_run(self._get_expert(), instance_guid)

    def read_report(self, report_guid: str) -> ReportRead:
        from resource_explorer.surveyors.egeria_survey_reader import annotations_from_report

        self._enter()
        result = self._get_assets().get_asset_by_guid(
            report_guid,
            body={"class": "GetRequestBody", "graphQueryDepth": 1},
            output_format="JSON")
        if not isinstance(result, dict):
            raise NativeSurveyError(
                f"Egeria returned no SurveyReport element for {report_guid}")
        props = result.get("properties") or {}
        header_versions = (result.get("elementHeader") or {}).get("versions") or {}
        at = (_iso(props.get("completionTime")) or _iso(props.get("createdTime"))
              or _iso(header_versions.get("createTime")))
        originator = ((result.get("reportOriginator") or {}).get("relatedElement") or {}) \
            .get("elementHeader", {}).get("guid", "")
        annotations = []
        for raw in annotations_from_report(result):
            annotations.append({
                "guid": raw.get("guid", ""),
                "annotation_type": raw.get("annotation_type", ""),
                "analysis_step": raw.get("analysis_step", ""),
                "summary": raw.get("summary", ""),
                "explanation": raw.get("explanation", ""),
                "confidence": raw.get("confidence"),
                # Everything else the wire carried, verbatim: the measurement
                # payload is not RE's to summarise away.
                "detail": {k: v for k, v in raw.items() if k not in (
                    "guid", "annotation_type", "analysis_step", "summary",
                    "explanation", "confidence")},
            })
        return ReportRead(guid=report_guid, at=at, originator_guid=originator,
                          annotations=annotations)


@contextlib.contextmanager
def port_session(port):
    """Yield `port`, closing it (its event loop) afterwards when it has a
    `close`. The one way a real port should be used."""
    try:
        yield port
    finally:
        close = getattr(port, "close", None)
        if callable(close):
            close()


# ── the one place proof rows become words ───────────────────────────────────


def _now() -> str:
    """UTC, ISO, naive -- the registry's own timestamp spelling (the frontend's
    `whenMs` reads a naive stamp as UTC)."""
    return datetime.now(timezone.utc).replace(tzinfo=None).isoformat()


def _age_seconds(then: str, now: datetime | None) -> float | None:
    """Seconds from a stored naive-UTC stamp to `now` (naive UTC), or None when the stamp is unreadable."""
    try:
        t = datetime.fromisoformat(str(then).replace("Z", ""))
    except ValueError:
        return None
    if t.tzinfo is not None:
        t = t.astimezone(timezone.utc).replace(tzinfo=None)
    n = now or datetime.now(timezone.utc).replace(tzinfo=None)
    return max(0.0, (n - t).total_seconds())


def _gap_seconds(earlier: str | None, later: str | None) -> float | None:
    """Seconds from one stored naive-UTC stamp to a later one, or None when either is missing or unreadable."""
    if not earlier or not later:
        return None
    try:
        a = datetime.fromisoformat(str(earlier).replace("Z", ""))
        b = datetime.fromisoformat(str(later).replace("Z", ""))
    except ValueError:
        return None
    return max(0.0, (b.replace(tzinfo=None) - a.replace(tzinfo=None)).total_seconds())


def _stall(out: dict, now: datetime | None, requested_at: str = "", started_at: str = "") -> None:
    """Fill `phase`, `requested_age_s`, `stall_after_s` and `stalled` for an action that is still active.
    From the request time and Egeria's last-read status only; unknown stays unknown (None), never False."""
    status = out["egeria_status"]
    if status in WAITING_STATUSES:
        out["phase"] = "waiting"
    elif status in ACTIVE_STATUSES:
        out["phase"] = "running"
    else:
        out["phase"] = ""            # submitted, no status read yet: not known to be either
    age = _age_seconds(out["submitted_at"], now)
    out["requested_age_s"] = age
    # Egeria's own times, when stored: how long it queued before starting, and how long it has run since.
    # Unknown stays None (a row from before the columns, or an action Egeria has not started).
    out["queued_s"] = out["running_s"] = None
    out["requested_at"], out["started_at"] = requested_at or "", started_at or ""
    if started_at and out["phase"] == "running":
        out["running_s"] = _age_seconds(started_at, now)
        out["queued_s"] = _gap_seconds(requested_at, started_at)
    after = STALL_AFTER_RUNNING_S if out["phase"] == "running" else STALL_AFTER_WAITING_S
    out["stall_after_s"] = after
    out["stalled"] = None if age is None else age > after


def derive_native_state(run: dict | None, *, stored_annotation_count: int | None = None,
                        now: datetime | None = None) -> dict:
    """The row's status, computed from persisted proof and nothing else.

    `run` is the newest `step_runs` row for (resource, process), or None.
    `stored_annotation_count` is how many of that report's annotations are
    actually in `native_survey_annotations` -- passed in (not read here) so this
    stays pure, and compared to the count recorded when the report was read so a
    lost row cannot leave a row saying "complete".

    Returns {state, egeria_status, message, submitted_at, read_at, report_at,
    report_guid, engine_action_guid, annotation_count, error}. `state` is what
    the frontend keys its glyph and wording on. Every field a state does not
    have proof for is left empty, never filled with a plausible default.
    """
    out = {
        "state": NOT_RUN, "egeria_status": "", "message": "", "submitted_at": "",
        "read_at": "", "report_at": "", "report_guid": "", "engine_action_guid": "",
        "annotation_count": None, "error": "",
        "phase": "", "requested_age_s": None, "stall_after_s": None, "stalled": False,
        "requested_at": "", "started_at": "", "queued_s": None, "running_s": None,
    }
    if not run:
        return out

    guid = run.get("engine_action_guid") or ""
    out["submitted_at"] = run.get("surveyed_at") or ""
    if not guid:
        error = run.get("submit_error") or ""
        if error:
            out.update(state=SUBMIT_FAILED, error=error)
        # else: a row with neither a GUID nor a recorded failure proves
        # nothing, and stays "not run".
        return out

    status = run.get("engine_action_status") or ""
    read_error = run.get("engine_action_read_error") or ""
    out.update(
        engine_action_guid=guid, egeria_status=status,
        message=run.get("engine_action_message") or "",
        read_at=run.get("engine_action_read_at") or "",
        error=read_error,
    )

    if not status:
        out["state"] = UNREADABLE if read_error else SUBMITTED
        if out["state"] == SUBMITTED:
            _stall(out, now)
        return out

    if status in ACTIVE_STATUSES:
        out["state"] = RUNNING
        _stall(out, now, run.get("engine_action_requested_at") or "", run.get("engine_action_started_at") or "")
        return out

    if status in SUCCESS_STATUSES:
        report_guid = run.get("survey_report_guid") or ""
        recorded = run.get("report_annotation_count")
        out.update(report_guid=report_guid, report_at=run.get("survey_report_at") or "",
                   annotation_count=recorded)
        if not (report_guid and run.get("report_read_at") and recorded is not None):
            out["state"] = AWAITING_REPORT
        elif stored_annotation_count != recorded:
            out["state"] = REPORT_INCOMPLETE
        else:
            out["state"] = COMPLETE
        return out

    # Any other terminal word -- FAILED, INVALID, IGNORED, CANCELLED, ... -- is
    # shown as Egeria said it. An action that failed without saying why still
    # says so, rather than rendering a blank.
    out["state"] = FAILED
    if not out["message"]:
        out["message"] = ("cancelled in Egeria" if status == "CANCELLED"
                          else "Egeria gave no message with this status")
    return out


# ── the rows a resource shows ───────────────────────────────────────────────

_NEEDS_CATALOGUING = (
    "Not published to Egeria yet -- the survey acts on the resource's Egeria "
    "asset, and this resource has none. Publish it to Egeria first.")


def _resource_entity(registry, entity_type: str, slug: str):
    if entity_type == "database":
        return registry.get_database(slug)
    if entity_type == "filesystem":
        return registry.get_filesystem(slug)
    return None


def cannot_run_reason(process: NativeProcess, entity, *, registry=None, technology_type: str = "") -> str:
    """Why RE cannot run this process for this resource, or '' when it can.
    Kind-based and registry-only: no Egeria call, so it is safe on a list read.

    A database that was never registered with Egeria is not an error: it gets the neutral
    "Egeria has not been given this database" sentence (see `row_is_neutral`)."""
    from resource_explorer import catalog_and_survey as cas

    if process.kind == KIND_CATALOG_AND_SURVEY:
        # Not run through `submit_native_survey`: registering is its own press (register_with_egeria).
        return cas.wiring_reason("database" if hasattr(entity, "db_type") else "filesystem",
                                 technology_type, entity) if entity is not None else "This resource is not registered."
    if entity is not None and hasattr(entity, "db_type") and is_postgres_only(process) and not _is_postgres(entity):
        return NOT_POSTGRES
    if process.kind == KIND_MIXED_PROCESS:
        return _mixed_cannot_run_reason(process, entity)
    if process.kind == KIND_RE_DEFINITION:
        return _definition_cannot_run_reason(process, entity)
    if process.kind != KIND_SURVEY_EXISTING:
        return f"RE does not run processes of kind {process.kind!r}."
    from resource_explorer import engine_host

    if process.qualified_name.startswith(engine_host.ENGINE_QUALIFIED_NAME + "::") \
            and not engine_host.engine_host_enabled():
        # RE's own engine serves this action; with the engine host off nothing would, and the action would sit
        # APPROVED while the row polls. Still shown, not runnable.
        return ENGINE_HOST_OFF
    if entity is None:
        return "This resource is not registered."
    if process.target == "server":
        if registry is None or not cas.server_pointer(registry, entity):
            return cas.SERVER_NOT_REGISTERED_WORDS
        return _missing_credentials_reason(entity)
    if not (getattr(entity, "egeria_asset_guid", "") or "").strip():
        return cas.NOT_REGISTERED_WORDS if hasattr(entity, "db_type") else _NEEDS_CATALOGUING
    if not process.needs_credentials:
        return ""
    return _missing_credentials_reason(entity)


#: Why an RE engine-host process (`ResourceExplorerSurvey::...`) cannot run: RE_ENGINE_HOST_ENABLED is off.
ENGINE_HOST_OFF = "engine host off"

#: Why a mixed process (engine host step 4) cannot run: Egeria has no process by that qualifiedName. Known only after
#: a read (the /check press, or Run itself); the registry-only listing never claims it.
PROCESS_NOT_DECLARED = "process not declared in Egeria · run its document in engine-host/processes/"


#: Processes whose (first) step is Egeria's PostgreSQL survey: offered for a PostgreSQL database only.
NOT_POSTGRES = "not a PostgreSQL database"


def is_postgres_only(process: NativeProcess) -> bool:
    return process.kind == KIND_MIXED_PROCESS or process.qualified_name.startswith("PostgreSQLSurvey::")


def _is_postgres(entity) -> bool:
    return str(getattr(entity, "db_type", "") or "").strip().lower() in ("postgresql", "postgres")


def _mixed_cannot_run_reason(process: NativeProcess, entity) -> str:
    """A mixed process (`mixed_process.MIXED_PROCESSES`) runs only when RE's engine host is on (it serves the RE
    step), the database has an Egeria asset, RE's engine runs every RE step here, and the native step's
    precondition holds: Egeria's survey engine has the database's credentials, as for PostgreSQLSurvey.
    Registry-only; whether Egeria has the process declared is a read (`PROCESS_NOT_DECLARED`)."""
    from resource_explorer import catalog_and_survey as cas
    from resource_explorer import engine_host, mixed_process

    declared = mixed_process.by_qualified_name(process.qualified_name)
    if declared is None:
        return f"{process.qualified_name} is not a process Resource Explorer declares."
    if not engine_host.engine_host_enabled():
        return ENGINE_HOST_OFF
    if entity is None:
        return "This resource is not registered."
    if not (getattr(entity, "egeria_asset_guid", "") or "").strip():
        return cas.NOT_REGISTERED_WORDS if hasattr(entity, "db_type") else _NEEDS_CATALOGUING
    supported = engine_host.supported_request_types()
    for step in declared.re_steps:
        if step.request_type not in supported:
            return f"step {step.number} ({step.request_type}) is not installed where the engine host runs"
    return _missing_credentials_reason(entity) if process.needs_credentials else ""


#: The row kinds whose GUID is a GovernanceActionProcessInstance read back as a run line (`refresh_mixed_run`).
_PROCESS_KINDS = frozenset({KIND_MIXED_PROCESS, KIND_RE_DEFINITION})


def is_process_run(process_qn: str) -> bool:
    """A row of a process RE declares (a mixed process, or one of RE's own Survey Definitions coordinated by
    Egeria, case 3): its GUID is a process instance, read back step by step."""
    from resource_explorer import mixed_process
    from resource_explorer.surveyors.technology_type_processes import kind_of

    return kind_of(process_qn) == KIND_MIXED_PROCESS or mixed_process.definition_process(process_qn) is not None


#: Case 3: the definitions coordinated by Egeria are database ones for now (`engine_host.out_of_scope_reason`).
NOT_FOR_THIS_KIND = "not for {kind}s yet · Resource Explorer's engine host runs database steps only"


def _definition_cannot_run_reason(process: NativeProcess, entity) -> str:
    """Why one of RE's Survey Definitions cannot be started "coordinated by Egeria" for this resource, or "".
    Registry-only; whether Egeria has the definition is read at the press (`PROCESS_NOT_DECLARED`)."""
    from resource_explorer import catalog_and_survey as cas
    from resource_explorer import engine_host, mixed_process

    declared = mixed_process.definition_process(process.qualified_name)
    if declared is None:
        return f"{process.qualified_name} is not a Survey Definition Resource Explorer declares."
    if not engine_host.engine_host_enabled():
        return ENGINE_HOST_OFF
    if declared.entity_type != engine_host.ENTITY_TYPE:
        return NOT_FOR_THIS_KIND.format(kind=declared.entity_type)
    if entity is None:
        return "This resource is not registered."
    if not (getattr(entity, "egeria_asset_guid", "") or "").strip():
        return cas.NOT_REGISTERED_WORDS if hasattr(entity, "db_type") else _NEEDS_CATALOGUING
    return ""


def _definition_processes(entity_type: str) -> list[NativeProcess]:
    """RE's own Survey Definitions for this kind of resource as processes Egeria can coordinate (case 3). Started
    with the asset as the `newAsset` target, the name a native survey takes when it is given several."""
    from resource_explorer.surveyors.survey_definition_docs import documented_definitions

    try:
        docs = documented_definitions()
    except Exception as exc:  # noqa: BLE001 - the configured rows still list
        log.warning("Survey Definitions not listed as Egeria processes: %s", exc)
        return []
    return [NativeProcess(qualified_name=f"GovActionProcess::{group}", display_name=doc.display_name or group,
                          kind=KIND_RE_DEFINITION, description=doc.description, action_target_name="newAsset",
                          target="asset", needs_credentials=False)
            for group, doc in sorted(docs.items()) if doc.resource_type == entity_type]


#: The label of the second control beside a Survey Definition's Run (owner, 2026-10-15): never "run in Egeria".
COORDINATE_LABEL = "coordinated by Egeria"


#: How long a "can Egeria dispatch every step" answer is kept (seconds). It is an Egeria read per step, so one
#: candidates call must not repeat it; a failed read is kept only briefly.
DISPATCH_TTL_SECONDS = 60.0
DISPATCH_FAILED_TTL_SECONDS = 10.0
COULD_NOT_CHECK_DISPATCH = "could not check with Egeria"
_dispatch_cache: dict[str, tuple[float, str]] = {}


def _executor_reader():
    """The reader that checks executors (one place, so a test fakes only the Egeria boundary)."""
    from resource_explorer.surveyors.survey_definition_reader import SurveyDefinitionReader

    return SurveyDefinitionReader()


def definition_dispatch_reason(process_qn: str, *, now: float | None = None) -> str:
    """"" when Egeria can dispatch every step of this definition (each step has its executor to RE's engine), else
    a short reason. One source with `scripts/reconcile_database_survey_definition_links.py`:
    `SurveyDefinitionReader.executor_drift`. A failed read is never "dispatchable": it says it could not check."""
    import time

    t = time.monotonic() if now is None else now
    hit = _dispatch_cache.get(process_qn)
    if hit and hit[0] > t:
        return hit[1]
    try:
        reader = _executor_reader()
        guid = reader.find_process_guid_by_name(process_qn)
        if not guid:
            reason, ttl = PROCESS_NOT_DECLARED, DISPATCH_FAILED_TTL_SECONDS
        else:
            drift = reader.executor_drift(guid)
            reason, ttl = "", DISPATCH_TTL_SECONDS
            if drift:
                first = drift[0]
                try:
                    names = [st.qualified_name for st in reader.fetch(guid).steps]
                    n = names.index(first.step_qualified_name) + 1
                    where = f"step {n}"
                except Exception:  # noqa: BLE001 - the step's key names it as well
                    where = f"step {first.step_key}"
                if any(d.problem == "unreadable" for d in drift):
                    reason, ttl = COULD_NOT_CHECK_DISPATCH, DISPATCH_FAILED_TTL_SECONDS
                else:
                    what = ("has no executor in Egeria" if first.problem == "missing"
                            else f"has the wrong executor in Egeria ({first.problem.replace('_', ' ')})")
                    reason = f"Egeria can't start this · {where} {what}"
    except Exception as exc:  # noqa: BLE001 - "could not check" is its own answer, never "dispatchable"
        log.warning("executors of %s not checked: %s", process_qn, exc)
        reason, ttl = COULD_NOT_CHECK_DISPATCH, DISPATCH_FAILED_TTL_SECONDS
    _dispatch_cache[process_qn] = (t + ttl, reason)
    return reason


def coordinate_control(registry, entity_type: str, slug: str, process_qn: str) -> dict | None:
    """The "coordinated by Egeria" control for one Survey Definition on one resource, from the registry only:
    `available`, `why_not` (the short sentence shown, once for the pane, when it cannot be offered), and the state of
    the latest run Egeria coordinated, from its persisted row (`derive_mixed_state`), so the control shows "started"
    from proof. None where the control does not belong at all (review L6): a definition RE does not declare, or one
    for a kind of resource the engine host does not run (repositories), so no row of those carries it."""
    from resource_explorer import engine_host, mixed_process

    declared = mixed_process.definition_process(process_qn)
    if declared is None or declared.entity_type != engine_host.ENTITY_TYPE or entity_type != declared.entity_type:
        return None
    out = {"label": COORDINATE_LABEL, "available": False, "why_not": "", "state": "", "line": ""}
    entity = _resource_entity(registry, entity_type, slug)
    process = NativeProcess(qualified_name=process_qn, display_name=declared.display_name, kind=KIND_RE_DEFINITION,
                            description="", action_target_name="newAsset", target="asset", needs_credentials=False)
    out["why_not"] = _definition_cannot_run_reason(process, entity) or definition_dispatch_reason(process_qn)
    out["available"] = not out["why_not"]
    runs = registry.list_native_survey_runs(entity_type, slug, process_qn) if entity is not None else []
    if runs:
        derived = derive_mixed_state(runs[0])
        out["state"], out["line"] = derived["state"], derived.get("line") or ""
    return out


def row_is_neutral(reason: str) -> bool:
    """A reason that is a plain statement of 'Egeria was never given this', not a fault."""
    from resource_explorer import catalog_and_survey as cas

    return reason in (cas.NOT_REGISTERED_WORDS, cas.SERVER_NOT_REGISTERED_WORDS)


NO_CREDENTIALS = "Egeria has no credentials for this database · re-project secrets"
UNCONFIRMED_CREDENTIALS = "can't confirm Egeria has credentials · secrets path not configured"

#: The three credential states -- exactly these, one word each.
PRESENT = "present"
ABSENT = "absent"
NOT_CONFIGURED = "not-configured"


def credentials_state(entity) -> str:
    """Whether the projected `.omsecrets` file holds this database's credentials.

    The survey engine reads them from that file by collection name. When it is
    gone (a redeploy reset the secrets directory on 2026-09-29) the JDBC
    connector has no user, falls back to the container's OS user, and every
    survey fails minutes later with `role "default" does not exist`.

    * `present` -- path configured and the collection is in the file.
    * `absent` -- path configured, but the file or the collection is missing:
      Run is refused with `NO_CREDENTIALS`.
    * `not-configured` -- `EGERIA_SECRETS_STORE_LOCAL_PATH` unset. RE has no
      host-visible path and has established nothing either way (CI, a remote
      engine host, secrets managed elsewhere), so Run stays available and the
      row says it cannot confirm (`UNCONFIRMED_CREDENTIALS`).

    Only a database has a PostgreSQL credential collection; anything else is
    reported `present` (nothing to check)."""
    if not hasattr(entity, "db_type"):
        return PRESENT
    from resource_explorer import omsecrets_store

    path = omsecrets_store.local_path()
    if not path:
        return NOT_CONFIGURED
    if omsecrets_store.has_collection(
            omsecrets_store.secrets_collection_name(entity.slug), path=path):
        return PRESENT
    return ABSENT


def _missing_credentials_reason(entity) -> str:
    return NO_CREDENTIALS if credentials_state(entity) == ABSENT else ""


def _library_processes(entity_type: str, configured: list[NativeProcess]) -> list[NativeProcess]:
    """The engine host's Analytic Library steps as processes (engine host, step 3), listed beside the configured
    RE engine-host process (`ResourceExplorerSurvey::re-db-derived`) and only for a technology type that lists
    one: a library step is read back and run exactly like it. Never fails the listing."""
    from resource_explorer import engine_host

    prefix = engine_host.ENGINE_QUALIFIED_NAME + "::"
    template = next((p for p in configured if p.qualified_name.startswith(prefix)), None)
    if template is None or entity_type != engine_host.ENTITY_TYPE:
        return []
    try:
        steps = engine_host.library_steps()
    except Exception as exc:  # noqa: BLE001 - the configured rows still list
        log.warning("library steps not listed: %s", exc)
        return []
    have = {p.qualified_name for p in configured}
    out = []
    for request_type, step in sorted(steps.items()):
        qn = engine_host.governance_action_type_qn_for(request_type)
        if qn in have:
            continue
        name = getattr(step.manifest, "display_name", "") or step.step_key
        out.append(NativeProcess(qualified_name=qn, display_name=f"Resource Explorer: {name}",
                                 kind=template.kind, description=step.description,
                                 action_target_name=template.action_target_name, target=template.target,
                                 needs_credentials=False))
    return out


def _processes_for_rows(entity_type: str, technology_type: str) -> list[NativeProcess]:
    """The resource's own processes, then (for a kind with a separate server element) the server's
    survey. The server's is listed only when the config says the kind has one."""
    from resource_explorer.surveyors.technology_type_processes import (
        get_wiring, server_has_separate_element)

    procs = list(get_native_processes(entity_type, technology_type))
    procs += _library_processes(entity_type, procs)
    procs += _definition_processes(entity_type)
    if server_has_separate_element(entity_type, technology_type):
        st = get_wiring(entity_type, technology_type).server_technology_type
        procs += [p for p in get_native_processes(entity_type, st) if p.target == "server"]
    return procs


def native_survey_rows(registry, entity_type: str, slug: str, technology_type: str, *,
                       pointers: dict | None = None, declared: dict | None = None) -> list[dict]:
    """One row per native survey process known for this resource's technology
    type: what it is, whether RE can run it (and if not, why), and the state
    derived from the newest persisted run. Registry-only -- no Egeria call --
    so it is cheap enough to ride on the survey pane's candidates read.

    `pointers` is the result of `catalog_and_survey.check_pointers` when a caller has READ them
    from Egeria; only then can a row say a stored pointer is gone (stale). Without it, no row
    claims anything about whether a stored pointer still resolves.

    `declared` ({process qualifiedName: True/False}) is what a caller READ from Egeria about the mixed processes
    (`check_declared`); only then can a mixed row say "process not declared"."""
    from resource_explorer import catalog_and_survey as cas

    entity = _resource_entity(registry, entity_type, slug)
    pointers = pointers or {}
    rows = []
    for process in _processes_for_rows(entity_type, technology_type):
        if process.kind == KIND_DELETE:
            continue  # never offered, in any form
        runs = registry.list_native_survey_runs(entity_type, slug, process.qualified_name)
        latest = runs[0] if runs else None
        if process.kind == KIND_RE_DEFINITION and latest is None:
            continue  # listed once Egeria has coordinated a run of it; it is started beside its own Run
        if entity is not None and hasattr(entity, "db_type") and is_postgres_only(process) \
                and not _is_postgres(entity):
            continue  # Egeria's PostgreSQL survey (alone, or as a process's first step) is offered for PostgreSQL only
        if process.kind == KIND_CATALOG_AND_SURVEY:
            rows.append(_catalog_row(registry, entity_type, slug, technology_type, entity,
                                     process, latest, stale=(pointers.get("database") == "gone")))
            continue
        reason = cannot_run_reason(process, entity, registry=registry, technology_type=technology_type)
        gone_key = "server" if process.target == "server" else "database"
        gone = pointers.get(gone_key) == "gone"
        if gone:
            reason = cas.STALE_WORDS
        if process.kind == KIND_MIXED_PROCESS and not reason and (declared or {}).get(process.qualified_name) is False:
            reason = PROCESS_NOT_DECLARED
        creds = (credentials_state(entity)
                 if (process.kind in (KIND_SURVEY_EXISTING, KIND_MIXED_PROCESS) and entity is not None
                     and process.needs_credentials)
                 else PRESENT)
        stored = None
        if latest and latest.get("survey_report_guid"):
            stored = registry.count_native_survey_annotations(latest["survey_report_guid"])
        derived = (derive_mixed_state(latest) if process.kind in _PROCESS_KINDS
                   else derive_native_state(latest, stored_annotation_count=stored))
        if process.kind in _PROCESS_KINDS:
            derived["waiting_for"] = _waiting_for_engine_host(registry, entity_type, slug, derived)
        row = {
            "qualified_name": process.qualified_name,
            "display_name": process.display_name,
            "kind": process.kind,
            "target": process.target,
            "description": process.description,
            "runnable": not reason,
            "cannot_run_reason": reason,
            "neutral": row_is_neutral(reason),
            "stale": gone,
            "credentials": creds,
            "credentials_note": UNCONFIRMED_CREDENTIALS if creds == NOT_CONFIGURED else "",
            "in_flight": derived["state"] in (SUBMITTED, RUNNING, AWAITING_REPORT, UNREADABLE),
            "cancellable": bool(latest) and cancel_offered(latest),
            "run": derived,
            "reach_note": cas.reach_note_for(derived),
            "tried": cas.tried_words(derived, entity),
            "notes": (cas.server_credential_notes(registry, entity)
                      if process.target == "server" and hasattr(entity, "db_type") else []),
        }
        if hasattr(entity, "db_type"):
            wiring = cas.wiring_reason(entity_type, technology_type, entity)
            # The control sits on the "Catalog and Survey" row; a survey row offers it only when its stored
            # pointer is GONE (the same row says so and offers the way back). Never three buttons for one act.
            row["register"] = {"available": not wiring and gone,
                               "label": cas.REGISTER_AGAIN_LABEL if gone else cas.REGISTER_LABEL, "why_not": wiring}
            if gone:
                row["gone_line"] = cas.gone_line(registry, entity_type, slug, entity, technology_type)
        if process.kind == KIND_RE_DEFINITION:
            # The same answer the definition's own card carries, so the row beside Egeria's other runs can press it.
            row["coordinated_by_egeria"] = coordinate_control(registry, entity_type, slug, process.qualified_name)
        if process.target == "server" and entity is not None:
            row["discovered"] = cas.discovered_databases(registry, entity, latest)
        rows.append(row)
    return rows


def _catalog_row(registry, entity_type, slug, technology_type, entity, process, latest, *,
                 stale: bool = False) -> dict:
    """The "Catalog and Survey" row: registering the SERVER with Egeria (then the database it
    holds). Optional; a never-registered database is a neutral state, not a fault."""
    from resource_explorer import catalog_and_survey as cas

    wiring = cas.wiring_reason(entity_type, technology_type, entity)
    has_ptr = bool((getattr(entity, "egeria_asset_guid", "") or "").strip()) if entity is not None else False
    # A pointer Egeria answered "no such element" to is not a registration.
    has_ptr = has_ptr and not stale
    derived = cas.derive_catalog_state(latest, has_pointer=has_ptr)
    in_flight = cas.catalog_in_flight(derived, latest)
    live = entity is not None and hasattr(entity, "db_type") and not wiring
    addr = cas.address_state(registry, entity_type, slug, entity) if live else {"current": "", "held": "", "will_change": False}
    return {
        "cancellable": False,        # a registration is a process; its identifier is never an engine action
        "qualified_name": process.qualified_name,
        "display_name": process.display_name,
        "kind": process.kind,
        "target": "server",
        "description": process.description,
        "runnable": False,                      # never through the survey Run button
        "cannot_run_reason": wiring,
        "neutral": False,
        "stale": stale,
        "credentials": PRESENT,
        "credentials_note": "",
        "in_flight": in_flight,
        "run": derived,
        "reach_note": cas.reach_note_for(derived),
        "notes": cas.catalog_notes(registry, entity_type, entity, latest, derived, in_flight) if live else [],
        "asset_guid": (getattr(entity, "egeria_asset_guid", "") or "") if entity is not None else "",
        "register": cas.register_control(registry, entity, derived, in_flight, wiring, stale=stale,
                                         will_change=addr["will_change"]) if live
        else {"available": False, "label": cas.REGISTER_LABEL, "why_not": wiring, "start_again": False},
        **({"gone_line": cas.gone_line(registry, entity_type, slug, entity, technology_type)} if stale else {}),
        # "Registered" proves the elements exist, not that Egeria can reach the database: what the survey runs prove.
        "reach": cas.reach_proof(registry, entity_type, slug, technology_type, entity) if live else None,
        "address": addr,
    }


# ── a mixed process run (engine host step 4, M4) ─────────────────────────────

def _declared(run: dict) -> dict:
    import json

    raw = run.get("declared")
    try:
        out = raw if isinstance(raw, dict) else json.loads(raw or "{}")
    except (TypeError, ValueError):
        out = {}
    return out if isinstance(out, dict) else {}


def derive_mixed_state(run: dict | None, *, now: datetime | None = None) -> dict:
    """A mixed process run's row state, from persisted proof only: the row (process instance GUID, the last read's
    time and error) and what that read stored of each step (`declared.process_run`: action, Egeria's status, guards,
    report). Same shape as `derive_native_state`, plus `line` (the one run line) and `steps`.

    complete only when the LAST declared step COMPLETED with survey-completed; failed when any step ended otherwise
    ("step 2 not started · step 1 failed" when Egeria never created the next step); running while a step is active
    or the next one has not appeared yet after a success."""
    from resource_explorer import mixed_process

    out = derive_native_state(run, now=now)
    out.update(line="", steps=[])
    if not run or not run.get("engine_action_guid"):
        return out
    pr = _declared(run).get("process_run") or {}
    steps = pr.get("steps") or []
    # The ONE graded list: the run's stored step list, else (legacy row) the steps it has. A line stored before the
    # words walked that list may still name a step beyond it ("step 5 ... not started yet"): trimmed here at read time.
    stored = _declared(run).get("expected_steps")
    graded = len(stored) if isinstance(stored, list) and stored else len(steps)
    words = mixed_process.trim_words_to_steps(pr.get("words") or "", graded) if steps else (pr.get("words") or "")
    words = mixed_process.modernise_words(words)
    had_run = bool(pr)
    pr = {**pr, "words": words}
    out.update(line=words, steps=steps, report_guid="", annotation_count=None)
    if not had_run or not steps:
        # Nothing read yet, or Egeria has linked no engine action to the instance yet: submitted (or the last read
        # failed), as derive_native_state says from the row, never "running".
        out["state"] = UNREADABLE if out.get("error") else SUBMITTED
        return out
    process = mixed_process.declared_process(run.get("executor_ref") or "")
    # The run's own step list, stored when it started. A row from before that (none stored) is never graded against a
    # definition that may have changed since: it expects the steps it has, so a run whose steps all ended is over.
    stored = _declared(run).get("expected_steps")
    expected = len(stored) if isinstance(stored, list) and stored else len(steps)
    by_index = list(steps)
    def _ended_badly(step: dict) -> bool:
        return (step.get("status") not in ("", None, *ACTIVE_STATUSES)
                and not (step.get("status") == "COMPLETED" and "survey-completed" in (step.get("guards") or [])))

    ended_badly = next((s for s in by_index if _ended_badly(s)), None)
    # An RE definition goes on after an invalid or failed step (case 3, review M1: its steps always emit `Any`), so
    # while a step is active or still to come it is running; failed only once nothing more will run.
    goes_on = process is not None and process.qualified_name.startswith(mixed_process.DEFINITION_PREFIX) \
        and mixed_process.by_qualified_name(process.qualified_name) is None
    # A run whose step said the process ended there (no authored link took its guards) will create no more steps.
    from resource_explorer.step_outcome import PROCESS_ENDED_PREFIX

    # The stored flag (computed from the full message); the cut message only for a row stored before the flag.
    ended_early = any(s.get("process_ended") if "process_ended" in s
                      else PROCESS_ENDED_PREFIX in (s.get("message") or "") for s in by_index)
    still_going = any(s.get("status") in ACTIVE_STATUSES for s in by_index) or (
        len(by_index) < expected and not ended_early)
    # Refused (survey-invalid) is not failed (survey-failed or any other end): a definition run whose steps all ended
    # with some refused is PARTIAL, not a failure of the run. Only a definition that ran to its end can be partial.
    refused = [s for s in by_index if _ended_badly(s) and mixed_process.is_refused_step(s)]
    failed_steps = [s for s in by_index if _ended_badly(s) and not mixed_process.is_refused_step(s)]
    if goes_on and not ended_early:
        ended_badly = failed_steps[0] if failed_steps else None
    by_type = {st.request_type: st for st in process.steps} if process else {}

    # The run's own plan against the definition as it is now (architect ruling 2026-10-16, item 1): the line counts
    # the PLAN it was made under, the card header counts the definition. They differ only when the definition changed.
    current_types = ([st.request_type for st in sorted(process.steps, key=lambda x: x.number)] if process else [])
    recorded = isinstance(stored, list) and bool(stored)
    out["plan"] = {"recorded": recorded, "steps": expected, "definition_steps": len(current_types),
                   "differs": bool(recorded and current_types and list(stored) != current_types)}

    def _problems() -> list[dict]:
        """Every step that ended other than completed: its place, name, kind (refused or failed) and the
        launcher's reason, for the run's on-demand detail."""
        out_list = []
        for s in by_index:
            if not _ended_badly(s):
                continue
            declared_step = by_type.get(s.get("request_type"))
            out_list.append({"step": declared_step.number if declared_step else 0,
                             "label": declared_step.label if declared_step else (s.get("request_type") or ""),
                             "kind": "refused" if mixed_process.is_refused_step(s) else "failed",
                             "reason": _refusal_reason(s.get("message") or ""),
                             "credential": bool(mixed_process.is_refused_step(s)
                                                and CREDENTIAL_GAP.search(s.get("message") or ""))})
        return out_list

    if ended_badly is not None and not (goes_on and still_going):
        out["state"] = FAILED
        out["message"] = pr.get("words") or out["message"]
        out["problems"] = _problems()
        out["fixable"] = _fixable(out["problems"])
        return out
    if still_going:
        out["state"] = RUNNING
        out["message"] = pr.get("words") or out["message"]
        return out
    if goes_on and refused:
        ran = sum(1 for s in by_index if not _ended_badly(s))
        out["state"] = PARTIAL
        out["message"] = pr.get("words") or out["message"]
        plan = out["plan"]
        out["ran"] = ran
        if not plan["recorded"]:
            out["summary"] = f"{ran} ran · this run's plan wasn't recorded"     # a count RE did not record is not shown as one
        elif plan["differs"]:
            out["summary"] = (f"{ran} of its {expected} steps ran · made under an earlier version of this definition "
                              f"({expected} steps, now {plan['definition_steps']})")
        else:
            out["summary"] = f"{ran} of {expected} ran"
        out["problems"] = _problems()
        out["fixable"] = _fixable(out["problems"])
        return out
    out["state"] = COMPLETE
    out["message"] = pr.get("words") or out["message"]
    out["report_guid"] = (by_index[-1].get("report_guid") if by_index else "") or ""
    return out


#: The last-run status words (the definition card's ⚠/✓ logic reads these) for a coordinated run's derived state.
_LAST_RUN_STATUS = {COMPLETE: "ok", PARTIAL: "partial", FAILED: "failed"}


def coordinated_last_runs(registry, entity_type: str, slug: str) -> dict[str, dict]:
    """The latest Egeria-coordinated run of each RE Survey Definition on this resource, in the shape of
    `registry.get_survey_definition_last_activity`'s entries, from the stored native-survey rows only (a run RE
    coordinated leaves an activity row; one Egeria coordinated leaves this row and none other). A press Egeria did
    not accept (no GUID) is not a run. The status word comes from `derive_mixed_state` over the stored steps; a run
    not yet read back or still going is `recorded` (it ran; nothing yet to call good or bad)."""
    from datetime import datetime as _dt

    from resource_explorer import mixed_process

    out: dict[str, dict] = {}
    for run in registry.list_native_survey_runs(entity_type, slug):          # newest first
        ref = run.get("executor_ref") or ""
        if (not ref.startswith(mixed_process.DEFINITION_PREFIX) or ref in out
                or not run.get("engine_action_guid") or run.get("submit_error")):
            continue
        derived = derive_mixed_state(run)
        when = (run.get("surveyed_at") or "").strip()
        if not when:
            continue
        try:
            parsed = _dt.fromisoformat(when.replace("Z", "+00:00"))
        except ValueError:
            continue
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        declared = _declared(run)
        out[ref] = {
            "last_run_at": parsed.isoformat(),
            "last_run_status": _LAST_RUN_STATUS.get(derived.get("state"), "recorded"),
            "last_run_activity_id": "",
            "last_run_errors": [f"step {p['step']} {p['label']} {p['kind']}: {p['reason']}".strip(": ")
                                for p in derived.get("problems") or []],
            "last_run_engine_note": "",
            "last_run_coordinated_by": "egeria",
            "last_run_started_by": "",
            "last_run_by": (run.get("submitted_by") or "")
            if declared.get("started_in") == mixed_process.STARTED_IN_RE else "",
        }
    return out


def definition_run_history(registry, entity_type: str, slug: str, limit: int = 30) -> dict[str, list[dict]]:
    """Every run of each RE Survey Definition on this resource, newest first, WHOEVER coordinated it, for the
    definition card's "runs · N ›" list (designer 2026-10-16, Q1). An Egeria-coordinated run comes from its
    native-survey row through the same `derive_mixed_state` the run line uses; a run RE coordinated comes from its
    activity row. Each: when, coordinated_by ("egeria"|"re"), by, state, ran/of (a count RE recorded, else None),
    version_note ("(4-step version)" when the run's plan differs from the definition now, architect item 1) and the
    step reports. A press Egeria did not accept (no GUID) is not a run."""
    from datetime import datetime as _dt

    from resource_explorer import mixed_process

    def _key(when: str):
        try:
            d = _dt.fromisoformat((when or "").replace("Z", "+00:00"))
        except ValueError:
            return 0.0
        return (d if d.tzinfo else d.replace(tzinfo=timezone.utc)).timestamp()

    out: dict[str, list[dict]] = {}
    for run in registry.list_native_survey_runs(entity_type, slug):
        ref = run.get("executor_ref") or ""
        if not ref.startswith(mixed_process.DEFINITION_PREFIX) or not run.get("engine_action_guid") or run.get("submit_error"):
            continue
        d = derive_mixed_state(run)
        plan = d.get("plan") or {}
        recorded = bool(plan.get("recorded"))
        if d["state"] == COMPLETE:
            ran, of = (plan.get("steps") if recorded else None), (plan.get("steps") if recorded else None)
        else:
            ran, of = d.get("ran"), (plan.get("steps") if recorded else None)
        out.setdefault(ref, []).append({
            "when": run.get("surveyed_at") or "", "coordinated_by": "egeria",
            "by": (run.get("submitted_by") or ""), "state": d["state"], "ran": ran, "of": of,
            "version_note": f"({plan['steps']}-step version)" if plan.get("differs") else "",
            "reports": [{"step": i + 1, "guid": st["report_guid"]} for i, st in enumerate(d.get("steps") or [])
                        if st.get("report_guid")]})
    for a in registry.list_survey_definition_runs(entity_type, slug):
        out.setdefault(a["ref"], []).append({
            "when": a["when"], "coordinated_by": "re", "by": a["by"],
            "state": ("complete" if a["status"] == "ok" else "partial" if a["status"] == "partial"
                      else "running" if str(a["status"]).lower() in ("running", "queued", "pending", "claimed", "submitted") else "failed"),
            "ran": None, "of": None, "version_note": "", "reports": []})
    for ref in out:
        out[ref].sort(key=lambda r: _key(r["when"]), reverse=True)
        del out[ref][limit:]
    return out


#: A refusal the person can fix: the capability gate said the database credential cannot read enough
#: ("needs `read`: connected as X, which has SELECT on 3 of 61 relation(s) — ..."), however the step phrases it.
CREDENTIAL_GAP = re.compile(r"needs `\w+`|\bSELECT on \d+ of \d+|\bstored credential\b", re.IGNORECASE)


def _step_range(numbers: list[int]) -> str:
    nums = sorted(set(n for n in numbers if n))
    if not nums:
        return ""
    if len(nums) == 1:
        return f"step {nums[0]}"
    return f"steps {nums[0]}–{nums[-1]}" if nums == list(range(nums[0], nums[-1] + 1)) \
        else "steps " + ", ".join(str(n) for n in nums)


def _fixable(problems: list[dict]) -> dict | None:
    """The refusal the person can fix, for the run line: which steps were not run, the first clause of why, and what
    to change. None when no refusal is a credential gap (a failure is never offered as fixable)."""
    gap = [p for p in problems if p.get("credential")]
    if not gap:
        return None
    reason = re.split(r"\s+[—-]\s+", gap[0].get("reason") or "", maxsplit=1)[0].strip().rstrip(".")
    reason = re.sub(r"^needs `\w+`:\s*", "", reason)
    return {"kind": "credential", "steps": [p["step"] for p in gap], "reason": reason,
            "words": f"{_step_range([p['step'] for p in gap])} not run · {reason}",
            "action": "set the database credential"}


def _refusal_reason(message: str) -> str:
    """The launcher's reason for refusing a step, from the step's stored message: what follows the engine host's
    "Resource Explorer did not run <step>: " lead, without the process-ended note it may end with."""
    text = (message or "").strip()
    text = re.sub(r"^.*?\bdid not run [^:]*:\s*", "", text, count=1) if "did not run" in text else text
    return text.split(" · process ended", 1)[0].strip()


#: A mixed run whose RE step Egeria created but nobody claimed: said with why, never left as a bare "running".
WAITING_FOR_ENGINE_HOST = "waiting for RE's engine host"


def _waiting_for_engine_host(registry, entity_type: str, slug: str, derived: dict) -> str:
    """"waiting for RE's engine host · <why>" when the last read shows an RE step Egeria created and nobody has
    claimed (APPROVED or REQUESTED), else "". Registry-only: the engine host's switch, and whether the target is now
    private to its owner (the host leaves such actions unclaimed, owner Q-O3)."""
    from resource_explorer import mixed_process

    if derived.get("state") != RUNNING:
        return ""
    re_types = {s.request_type for p in mixed_process.MIXED_PROCESSES.values() for s in p.re_steps}
    waiting = [s for s in derived.get("steps") or []
               if s.get("request_type") in re_types and s.get("status") in ("APPROVED", "REQUESTED")]
    if not waiting:
        return ""
    from resource_explorer import engine_host

    if not engine_host.engine_host_enabled():
        why = ENGINE_HOST_OFF + " · turn it on, or cancel step 2 in Egeria"
    else:
        try:
            private = registry.private_owner_for_entity(entity_type, slug)
        except Exception:  # noqa: BLE001 -- unknown is said as unknown
            private = None
        why = ("the database is private to its owner, so the unattended host leaves it · run it attended, or cancel "
               "step 2 in Egeria") if private else "not claimed yet"
    return f"{WAITING_FOR_ENGINE_HOST} · {why}"


def refresh_mixed_run(registry, port, run: dict, *, entity_type: str, slug: str) -> dict:
    """Read one mixed process run back (read-only): the instance's engine actions in run order, grouped into its one
    run line (`mixed_process.group_by_process`). Stores what it read on the row (`declared.process_run`) and the
    newest step's Egeria status as the row's; a failed read is recorded as one and never becomes a status."""
    from resource_explorer import mixed_process

    guid = run["engine_action_guid"]
    entity = _resource_entity(registry, entity_type, slug)
    read_at = _now()
    try:
        views = port.read_process_run(guid)
    except Exception as exc:  # noqa: BLE001 -- recorded, not raised
        registry.record_native_survey_readback(guid, read_at=read_at, error=scrub_secret(_short_error(exc), entity))
        return _refetch_mixed(registry, entity_type, slug, run)
    lines = [ln for ln in mixed_process.group_by_process(views, duplicates=_refused_duplicates(registry, views))
             if ln.instance_guid == guid]
    line = lines[0] if lines else mixed_process.RunLine(guid, run.get("executor_ref") or "", [])
    if not line.process_name:
        line.process_name = run.get("executor_ref") or ""
    # The time is not in the words: it is the reader's local time, drawn on the page from the row's own stored
    # submission time (a UTC date here read as the wrong day).
    started = ""
    # Case 3 (C4): a definition's run says where it was started: from RE's control by the person who pressed, or in
    # Egeria by the requester its actions carry. A mixed process run says "started by", as before.
    declared = _declared(run)
    started_in = declared.get("started_in") or ""
    started_by = (run.get("submitted_by") or "") if started_in == mixed_process.STARTED_IN_RE else ""
    stored = declared.get("expected_steps")
    detail = mixed_process.run_line_dict(line, started=started, started_in=started_in, started_by=started_by,
                                         expected_steps=stored if isinstance(stored, list) and stored else None)
    detail["words"] = scrub_secret(detail["words"], entity)
    for step in detail["steps"]:
        step["message"] = scrub_secret(step.get("message") or "", entity)
    registry.merge_native_survey_declared(guid, {"process_run": {**detail, "read_at": read_at}})
    newest = line.steps[-1].status if line.steps else ""
    registry.record_native_survey_readback(guid, read_at=read_at, status=newest, message=detail["words"])
    return _refetch_mixed(registry, entity_type, slug, run)


def _refused_duplicates(registry, views: list) -> set[str]:
    """The actions of a process run RE's engine host refused as second starts of a step, by the flag on their own
    runs rows (`target.duplicate_trigger`). An action whose row cannot be read is not counted (the reader's text
    fallback still applies)."""
    out: set[str] = set()
    lookup = getattr(registry, "engine_action_runs", None)
    if not callable(lookup):
        return out
    import json

    for v in views:
        try:
            rows = lookup(v.guid)
        except Exception:  # noqa: BLE001 - a flag not read is not a flag
            continue
        for row in rows:
            try:
                target = json.loads(row.get("target") or "{}")
            except (TypeError, ValueError):
                continue
            if isinstance(target, dict) and target.get("duplicate_trigger"):
                out.add(v.guid)
    return out


def _refetch_mixed(registry, entity_type: str, slug: str, run: dict) -> dict:
    runs = registry.list_native_survey_runs(entity_type, slug, run.get("executor_ref") or "")
    latest = next((r for r in runs if r.get("engine_action_guid") == run.get("engine_action_guid")),
                  runs[0] if runs else None)
    return derive_mixed_state(latest)


def check_declared(port, entity_type: str, technology_type: str) -> dict:
    """{qualifiedName: True/False/None} for this resource's mixed processes, read from Egeria (None: unreadable)."""
    out: dict = {}
    for process in _processes_for_rows(entity_type, technology_type):
        if process.kind != KIND_MIXED_PROCESS:
            continue
        try:
            out[process.qualified_name] = bool(port.process_declared(process.qualified_name))
        except Exception as exc:  # noqa: BLE001 -- unreadable is not "not declared"
            log.warning("could not check %s is declared: %s", process.qualified_name, _short_error(exc))
            out[process.qualified_name] = None
    return out


# ── submit and read back ────────────────────────────────────────────────────


def refresh_run(registry, port: EgeriaSurveyPort, run: dict, *, entity_type: str,
                slug: str) -> dict:
    """Read one engine action back from Egeria (read-only) and, once Egeria says
    it completed, read its report into RE. Persists exactly what it read, and
    the time it read it. Never raises: a failed read is recorded on the row as
    a failed read -- it does not become a status.

    Idempotent. Reading a completed action's report twice writes the report's
    annotations once (keyed on report + annotation GUID), so a second browser
    polling the same row cannot duplicate findings.
    """
    guid = run["engine_action_guid"]
    process_qn = (run.get("executor_ref")
                  or (run.get("step_key") or "")[len(NATIVE_SURVEY_STEP_PREFIX):])

    from resource_explorer.surveyors.technology_type_processes import kind_of
    if kind_of(process_qn) == KIND_CATALOG_AND_SURVEY:
        # A registration process has no survey report of its own: its proof is the database element.
        from resource_explorer import catalog_and_survey as cas
        return cas.refresh_registration_run(registry, port, run, entity_type=entity_type, slug=slug)
    if is_process_run(process_qn):
        return refresh_mixed_run(registry, port, run, entity_type=entity_type, slug=slug)

    entity = _resource_entity(registry, entity_type, slug)
    try:
        action = port.read_action(guid)
    except Exception as exc:  # noqa: BLE001 -- recorded, not raised
        registry.record_native_survey_readback(
            guid, read_at=_now(), error=scrub_secret(_short_error(exc), entity))
        return _refetch(registry, entity_type, slug, process_qn)
    registry.record_native_survey_readback(
        guid, read_at=_now(), status=action.status,
        message=scrub_secret(action.message, entity),
        requested_at=action.requested_at, started_at=action.started_at)

    if action.status in SUCCESS_STATUSES:
        _read_report_in(registry, port, run, action, entity_type=entity_type,
                        slug=slug, process_qn=process_qn)
    return _refetch(registry, entity_type, slug, process_qn)


def _read_report_in(registry, port, run, action, *, entity_type, slug, process_qn) -> None:
    guid = run["engine_action_guid"]
    recorded = run.get("report_annotation_count")
    have = run.get("survey_report_guid") or ""
    if have and run.get("report_read_at") and recorded is not None \
            and registry.count_native_survey_annotations(have) == recorded:
        return  # already read in and intact; nothing to do
    try:
        report_guid = have or port.report_guid_for_action(guid)
        if not report_guid:
            return  # Egeria has not linked a report yet -- stays "awaiting"
        report = port.read_report(report_guid)
        if report.originator_guid and report.originator_guid != guid:
            raise NativeSurveyError(
                f"report {report_guid} says it originated from "
                f"{report.originator_guid}, not from engine action {guid}")
        registry.record_native_survey_report(
            guid, entity_type=entity_type, slug=slug, process_qualified_name=process_qn,
            report_guid=report_guid, report_at=report.at, read_at=_now(),
            annotations=report.annotations)
    except Exception as exc:  # noqa: BLE001 -- recorded, not raised
        # Error-only: the action's own status/message, written a moment ago,
        # stay on the row beside the fact that the report could not be read.
        registry.record_native_survey_readback(
            guid, read_at=_now(), error="report read failed: " + _short_error(exc))


def scrub_secret(text: str, entity) -> str:
    """`text` with the resource's stored password (and its encoded spellings) masked. Egeria's own
    sentences are kept whole, but a driver error that echoes the password it was handed must not
    reach a proof row, an activity row or a response."""
    from resource_explorer import secret_redaction

    secret = getattr(entity, "db_password", "") or ""
    return secret_redaction.scrub(text, secret) if secret else text


def _short_error(exc: Exception) -> str:
    text = " ".join(str(exc).split())
    return (text[:300] or type(exc).__name__)


def _refetch(registry, entity_type: str, slug: str, process_qn: str) -> dict:
    runs = registry.list_native_survey_runs(entity_type, slug, process_qn)
    latest = runs[0] if runs else None
    stored = None
    if latest and latest.get("survey_report_guid"):
        stored = registry.count_native_survey_annotations(latest["survey_report_guid"])
    return derive_native_state(latest, stored_annotation_count=stored)


def submit_native_survey(registry, port: EgeriaSurveyPort, entity_type: str, slug: str,
                         process_qualified_name: str, *, technology_type: str,
                         submitted_by: str = "") -> dict:
    """Submit one native survey and prove the dispatch by reading it back.

    Returns the derived row state. Raises `NativeSurveyCannotRun` (a reason RE
    knew before asking Egeria), `NativeSurveyBusy` (the same survey is already
    in flight -- a launch is not idempotent at Egeria, so a second click would
    make a second engine action), or `NativeSurveyError` (Egeria refused; the
    refusal is also persisted, so the row can say so).
    """
    entity = _resource_entity(registry, entity_type, slug)
    if entity is None:
        raise NativeSurveyCannotRun(f"{entity_type} {slug!r} is not registered")
    process = next((p for p in _processes_for_rows(entity_type, technology_type)
                    if p.qualified_name == process_qualified_name), None)
    if process is None or process.kind == KIND_DELETE:
        raise NativeSurveyCannotRun(
            f"{process_qualified_name!r} is not a survey RE offers for this resource")
    from resource_explorer import catalog_and_survey as cas

    if process.kind == KIND_CATALOG_AND_SURVEY:
        raise NativeSurveyCannotRun(
            "This one registers the database's SERVER with Egeria first; press "
            f"{cas.REGISTER_LABEL!r} (it is not a survey Run).")
    reason = cannot_run_reason(process, entity, registry=registry, technology_type=technology_type)
    if reason:
        raise NativeSurveyCannotRun(reason)

    runs = registry.list_native_survey_runs(entity_type, slug, process_qualified_name)
    if runs:
        latest = runs[0]
        stored = (registry.count_native_survey_annotations(latest["survey_report_guid"])
                  if latest.get("survey_report_guid") else None)
        state = (derive_mixed_state(latest) if process.kind in _PROCESS_KINDS
                 else derive_native_state(latest, stored_annotation_count=stored))["state"]
        if state in (SUBMITTED, RUNNING, AWAITING_REPORT, UNREADABLE):
            raise NativeSurveyBusy(
                "This survey is already in flight on this resource "
                f"(engine action {latest['engine_action_guid']}).")

    if process.target == "server":
        asset_guid = cas.server_pointer(registry, entity)
        what = "server"
    else:
        asset_guid = entity.egeria_asset_guid.strip()
        what = "asset"
    try:
        exists = port.asset_exists(asset_guid)
    except Exception as exc:  # noqa: BLE001
        raise NativeSurveyError(
            f"Could not check the resource's Egeria {what} first: {_short_error(exc)}") from exc
    if not exists:
        raise NativeSurveyStale(
            f"{cas.STALE_WORDS} ({asset_guid}); register the server with Egeria again.")

    started_as = STARTED_AS_ACTION_TYPE
    started_by = started_in = ""
    if process.kind in _PROCESS_KINDS:
        # Engine host step 4: the process must be declared in Egeria (read first; a failed read is not "absent").
        try:
            declared = port.process_declared(process_qualified_name)
        except Exception as exc:  # noqa: BLE001
            raise NativeSurveyError(f"Could not check that {process_qualified_name} is declared in Egeria: "
                                    f"{_short_error(exc)}") from exc
        if not declared:
            raise NativeSurveyCannotRun(PROCESS_NOT_DECLARED)
        started_as = STARTED_AS_PROCESS
        if process.kind == KIND_RE_DEFINITION:
            # Case 3: pressed in RE ("coordinated by Egeria"), coordinated by Egeria. No RE-coordinated run is made.
            from resource_explorer import engine_host, mixed_process

            started_by, started_in = engine_host.STARTED_BY_EGERIA, mixed_process.STARTED_IN_RE

    submitted_at = _now()
    try:
        if process.kind in _PROCESS_KINDS:
            # The process instance GUID is what the row holds (as for a catalog-and-survey run).
            engine_action_guid = port.initiate_process_run(
                process_qualified_name, process.action_target_name, asset_guid)
        else:
            engine_action_guid = port.initiate(
                process_qualified_name, process.action_target_name, asset_guid)
    except Exception as exc:  # noqa: BLE001
        error = scrub_secret(_short_error(exc), entity)
        registry.record_native_survey_submission(
            entity_type, slug, process_qualified_name, submitted_at,
            submit_error=error, submitted_by=submitted_by, started_as=started_as,
            started_by=started_by, started_in=started_in)
        raise NativeSurveyError(error) from exc

    if process.kind == KIND_RE_DEFINITION:
        # One run line per process instance (review L5): RE's engine host may already have listed it from the first
        # step it ran; then this press is recorded on that row, never as a second one.
        if not registry.record_native_survey_submission_once(
                entity_type, slug, process_qualified_name, submitted_at, engine_action_guid=engine_action_guid,
                submitted_by=submitted_by, started_as=started_as, started_by=started_by, started_in=started_in,
                declared_extra=mixed_process.expected_steps_record(process_qualified_name)):
            registry.adopt_native_survey_submission(
                engine_action_guid, submitted_by=submitted_by,
                declared_patch={"started_as": started_as, "started_by": started_by, "started_in": started_in})
    else:
        registry.record_native_survey_submission(
            entity_type, slug, process_qualified_name, submitted_at,
            engine_action_guid=engine_action_guid, submitted_by=submitted_by, started_as=started_as,
            started_by=started_by, started_in=started_in)
    log.info("submitted native survey %s for %s/%s: engine action %s",
             process_qualified_name, entity_type, slug, engine_action_guid)

    # The dispatch is proven by reading the action back, read-only. If that one
    # read fails, the row says exactly that (submitted, last read failed) rather
    # than pretending the read happened.
    run = registry.list_native_survey_runs(entity_type, slug, process_qualified_name)[0]
    return refresh_run(registry, port, run, entity_type=entity_type, slug=slug)


_IN_FLIGHT = (SUBMITTED, RUNNING, AWAITING_REPORT, UNREADABLE, REPORT_INCOMPLETE)


def own_action_run(registry, entity_type: str, slug: str, engine_action_guid: str) -> dict:
    """The step_runs row that recorded this engine action for this resource, else `NativeActionNotOurs`.
    Registry-only: a GUID RE never recorded for this resource is refused before Egeria hears of it."""
    for run in registry.list_native_survey_runs(entity_type, slug):
        if run.get("engine_action_guid") and run["engine_action_guid"] == engine_action_guid:
            return run
    raise NativeActionNotOurs(f"{engine_action_guid} is not one of the Egeria actions RE started for {slug}")


def cancel_gate(registry, port: EgeriaSurveyPort, entity_type: str, slug: str, engine_action_guid: str) -> dict:
    """May this action still be cancelled? FAIL CLOSED, because Egeria's cancel has no status guard
    (EngineActionHandler.cancelEngineAction :2431-2490 writes CANCELLED unconditionally, so a cancel sent for an
    action that finished would turn COMPLETED into CANCELLED and RE would never read in the report).

    1. Ownership, then the allowlist (`cancellable_kind`): a single-action survey only; a process instance or a
       registration is `NativeSurveyCannotRun` (409) with nothing read or sent.
    2. A FRESH read (the poll's own `refresh_run`). If it failed, or the row's read time did not advance, the
       current status is unknown: `NativeStatusUnreadable` (502, "could not read Egeria's current status ·
       not cancelled"). The stored status is never trusted instead.
    3. Only an action Egeria NOW reports as waiting or running passes (else 409).

    Returns the refreshed row. A window remains between this read and the write (see `cancel_native_action`'s
    second read, which narrows it to the round trip of one call; it cannot be closed, as Egeria offers no
    conditional cancel)."""
    run = own_action_run(registry, entity_type, slug, engine_action_guid)
    if not cancellable_kind(run):
        raise NativeSurveyCannotRun(
            "Only a single-action survey can be cancelled from here; this one was started as a process or a "
            "registration, whose identifier is not an engine action.")
    before = run.get("engine_action_read_at") or ""
    refresh_run(registry, port, run, entity_type=entity_type, slug=slug)
    fresh = own_action_run(registry, entity_type, slug, engine_action_guid)
    err = fresh.get("engine_action_read_error") or ""
    # "report read failed" is recorded only AFTER a successful status read of a finished action: the status is
    # known. Any other read error means the status is not.
    if (err and not err.startswith("report read failed")) or (fresh.get("engine_action_read_at") or "") <= before:
        raise NativeStatusUnreadable(UNREADABLE_STATUS_WORDS)
    status = fresh.get("engine_action_status") or ""
    if status not in ACTIVE_STATUSES:
        raise NativeSurveyCannotRun(
            f"Only a waiting or running action can be cancelled; Egeria now says {status or 'nothing yet'}.")
    return fresh


UNREADABLE_STATUS_WORDS = "could not read Egeria's current status · not cancelled"
FINISHED_MAYBE_WORDS = "Egeria may already have finished · the report may exist"


def cancel_native_action(registry, port: EgeriaSurveyPort, entity_type: str, slug: str,
                         engine_action_guid: str, *, gated: bool = False) -> dict:
    """Cancel one of this resource's Egeria survey actions, and say what Egeria then reports.

    1. `cancel_gate` (ownership, allowlist, a fresh successful read, live): unless `gated=True`, the caller
       already ran it.
    2. A SECOND status read immediately before the write, in this same session: anything not live, or a failed
       read, refuses (`NativeSurveyCannotRun` / `NativeStatusUnreadable`). The remaining window is one round
       trip; Egeria has no conditional cancel, so it is narrowed, not closed.
    3. `port.cancel` (the write), as whoever the port acts as.
    4. The read-back is the SAME status refresh the poll uses. `cancelled` only when it says CANCELLED. If it
       does and the action already has a report, `note` says Egeria may have finished first.

    Returns {cancelled, egeria_status, read_error, process_qualified_name, note}."""
    run = own_action_run(registry, entity_type, slug, engine_action_guid)
    process_qn = run.get("executor_ref") or (run.get("step_key") or "")[len(NATIVE_SURVEY_STEP_PREFIX):]
    if not gated:
        run = cancel_gate(registry, port, entity_type, slug, engine_action_guid)
    elif not cancellable_kind(run):
        raise NativeSurveyCannotRun("Only a single-action survey can be cancelled from here.")
    try:
        last = port.read_action(engine_action_guid)
    except Exception as exc:  # noqa: BLE001
        raise NativeStatusUnreadable(UNREADABLE_STATUS_WORDS) from exc
    registry.record_native_survey_readback(
        engine_action_guid, read_at=_now(), status=last.status,
        message=scrub_secret(last.message, _resource_entity(registry, entity_type, slug)))
    if last.status not in ACTIVE_STATUSES:
        raise NativeSurveyCannotRun(
            f"Only a waiting or running action can be cancelled; Egeria now says {last.status}.")
    try:
        port.cancel(engine_action_guid)
    except Exception as exc:  # noqa: BLE001 -- the route reads __cause__ for an Egeria security refusal
        raise NativeSurveyError(
            f"Egeria did not cancel the action: {scrub_secret(_short_error(exc), _resource_entity(registry, entity_type, slug))}"
        ) from exc
    refresh_run(registry, port, run, entity_type=entity_type, slug=slug)
    after = own_action_run(registry, entity_type, slug, engine_action_guid)      # THIS action's stored row
    now_status = after.get("engine_action_status") or ""
    note = ""
    if now_status == "CANCELLED":
        try:
            if port.report_guid_for_action(engine_action_guid):
                note = FINISHED_MAYBE_WORDS
        except Exception as exc:  # noqa: BLE001 -- an unreadable report check adds no claim either way, but says so
            note = f"couldn't check for a report ({type(exc).__name__})"
    return {"cancelled": now_status == "CANCELLED", "egeria_status": now_status,
            "read_error": after.get("engine_action_read_error") or "",
            "process_qualified_name": process_qn, "note": note}


def refresh_resource(registry, port: EgeriaSurveyPort, entity_type: str, slug: str) -> int:
    """Read back every in-flight native survey on one resource -- the polling
    target. A registry read only when nothing is in flight. Returns how many
    runs it read back."""
    n = 0
    for run in registry.list_native_survey_runs(entity_type, slug):
        if not run.get("engine_action_guid"):
            continue
        if _run_in_flight(registry, run, entity_type, slug):
            refresh_run(registry, port, run, entity_type=entity_type, slug=slug)
            n += 1
    return n


def _run_in_flight(registry, run: dict, entity_type: str, slug: str) -> bool:
    """Does this run still have read-back work to do? A registration run is judged by its own
    derivation (a COMPLETED process has no report to wait for); every other run by the survey one."""
    from resource_explorer.surveyors.technology_type_processes import kind_of

    process_qn = run.get("executor_ref") or (run.get("step_key") or "")[len(NATIVE_SURVEY_STEP_PREFIX):]
    if kind_of(process_qn) == KIND_CATALOG_AND_SURVEY:
        from resource_explorer import catalog_and_survey as cas
        entity = _resource_entity(registry, entity_type, slug)
        has_ptr = bool((getattr(entity, "egeria_asset_guid", "") or "").strip())
        return cas.catalog_in_flight(cas.derive_catalog_state(run, has_pointer=has_ptr), run)
    if is_process_run(process_qn):
        return derive_mixed_state(run)["state"] in _IN_FLIGHT
    stored = (registry.count_native_survey_annotations(run["survey_report_guid"])
              if run.get("survey_report_guid") else None)
    return derive_native_state(run, stored_annotation_count=stored)["state"] in _IN_FLIGHT


def sweep_in_flight(registry, port: EgeriaSurveyPort) -> int:
    """Read back every in-flight native survey on every resource, so a survey
    still completes -- and its report still lands in RE -- when nobody has the
    page open. Identity-free by construction: it reads Egeria and writes the
    proof rows of runs somebody else submitted, on nobody's behalf. Returns how
    many runs it read back."""
    n = 0
    for run in registry.list_in_flight_native_survey_runs():
        entity_type = run.get("entity_type") or "database"
        if not _run_in_flight(registry, run, entity_type, run["slug"]):
            continue
        refresh_run(registry, port, run, entity_type=entity_type, slug=run["slug"])
        n += 1
    return n
