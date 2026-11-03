"""Egeria-native surveys: list, run, read back.

`BRIEF-NATIVE-EGERIA-SURVEY-LAUNCH.md` is the brief and the
gate. Prefect is not in this path: a run here is a direct pyegeria submission
whose proof lives on `step_runs` (see `native_survey_run.py`).

Every Egeria call happens off the event loop via `asyncio.to_thread`, which
COPIES the request's context (a bare `threading.Thread` would not -- reference:
ContextVar identity in threads). Even so, the caller is read here, once, in the
request, and passed down explicitly: nothing below this file reads a ContextVar.
"""
from __future__ import annotations

import asyncio
import json
import logging

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from resource_explorer import native_survey_run as nsr
from resource_explorer.registry import ProjectRegistry

log = logging.getLogger(__name__)
router = APIRouter()

_ENTITY_TYPES = ("database", "filesystem")


class RunRequest(BaseModel):
    process_qualified_name: str
    #: A credential for THIS coordinated run only (DESIGN-COORDINATED-RUN-CREDENTIAL.md §2.1): sent in the body of this
    #: one call by the "with options…" dialog, held in this process's memory for the run's process instance, never
    #: stored. The password is left out of the model's own repr.
    db_user: str = ""
    db_pwd: str = Field(default="", repr=False)


def _port() -> nsr.EgeriaSurveyPort:
    """Indirection so tests can substitute a fake Egeria."""
    from resource_explorer import catalog_and_survey as cas

    return cas.PyegeriaRegistrationPort()


def _technology_type(entity_type: str) -> str:
    from resource_explorer.surveyors.survey_definition_executor import get_adapter

    return get_adapter(entity_type).egeria_technology_type_name


def _reader(request: Request) -> dict:
    """Who is reading, read HERE in the request: the signed-in person and whether they are an admin. It decides only
    whether a definition's coordinated run offers Cancel to them (owner Q2); a reader that cannot be told is nobody."""
    from resource_explorer.config import get_config
    from resource_explorer.run_queue import requested_by
    from resource_explorer.web import admin_auth

    try:
        return {"viewer": requested_by(),
                "viewer_is_admin": bool(admin_auth.is_admin_request(request, get_config().feedback))}
    except Exception as exc:  # noqa: BLE001 -- nobody: no Cancel is offered
        log.warning("native surveys: could not tell who is reading (%s); no Cancel offered", exc)
        return {"viewer": "", "viewer_is_admin": False}


def _check(entity_type: str, slug: str, registry: ProjectRegistry) -> str:
    if entity_type not in _ENTITY_TYPES:
        raise HTTPException(status_code=404,
                            detail=f"No native Egeria surveys for entity type {entity_type!r}")
    if nsr._resource_entity(registry, entity_type, slug) is None:
        raise HTTPException(status_code=404, detail=f"{entity_type} {slug!r} not found")
    return _technology_type(entity_type)


@router.get("/{entity_type}/{slug}")
async def list_native_surveys(entity_type: str, slug: str, request: Request) -> dict:
    """The native surveys for this resource, each with what RE can do about it
    and its derived state. Registry-only: no Egeria call."""
    registry = ProjectRegistry()
    reader = _reader(request)      # who reads, read HERE in the request
    tech = _check(entity_type, slug, registry)
    rows = await asyncio.to_thread(lambda: nsr.native_survey_rows(registry, entity_type, slug, tech, **reader))
    return {"technology_type": tech, "surveys": rows}


def _run_credential(body: RunRequest, submitted_by: str) -> tuple[str, str] | None:
    """The one-run credential in the start call's body, or None when none was given (DESIGN-COORDINATED-RUN-CREDENTIAL
    .md §2.5). Only the signed-in person starting this run can supply one: nobody signed in is a 401 (`Caller()`), RE's
    own daemon account is a 403, and half a credential, or a password shorter than
    `RUN_CREDENTIAL_MIN_LENGTH`, is a 400. Checked before Egeria hears of the run."""
    user, pwd = (body.db_user or "").strip(), body.db_pwd or ""
    if not user and not pwd:
        return None
    from resource_explorer.egeria_clients import Caller, is_daemon_user

    Caller()                                  # NoCallerIdentity / CallerTokenExpired -> 401 (not logged)
    if not (submitted_by or "").strip():
        raise HTTPException(status_code=401, detail=nsr.RUN_CREDENTIAL_NEEDS_STARTER)
    if is_daemon_user(submitted_by):
        raise HTTPException(status_code=403, detail="Resource Explorer's own service account cannot start a run "
                                                    "with a credential for this run.")
    if not user or not pwd:
        raise HTTPException(status_code=400, detail=nsr.RUN_CREDENTIAL_NEEDS_BOTH)
    if len(pwd) < nsr.RUN_CREDENTIAL_MIN_LENGTH:
        # A short password is a common substring: the process-wide log redaction that masks it would then mask (and so
        # reveal) it inside unrelated words in every log line of the run.
        raise HTTPException(status_code=400, detail=nsr.RUN_CREDENTIAL_TOO_SHORT)
    return user, pwd


@router.post("/{entity_type}/{slug}/run")
async def run_native_survey(entity_type: str, slug: str, body: RunRequest, request: Request) -> dict:
    """Submit one native survey to Egeria and prove the dispatch by reading the
    engine action back. Returns as soon as Egeria has accepted it -- a native
    survey can take many minutes, so completion is `refresh`'s job."""
    from resource_explorer.run_queue import requested_by

    registry = ProjectRegistry()
    reader = _reader(request)      # who reads, read HERE in the request
    tech = _check(entity_type, slug, registry)
    submitted_by = requested_by()      # read HERE, in the request, then passed down
    run_credential = _run_credential(body, submitted_by)
    from resource_explorer.web.routes.curate import authorize_resource_write
    await authorize_resource_write(registry, entity_type, slug, write="start_action")    # Brief Z2

    def _do() -> dict:
        if entity_type == "filesystem" and body.process_qualified_name == nsr.FOLDER_SURVEY_QN:
            # Files step 2: the stored reachability state decides who leads (a path Resource Explorer leads is refused
            # below with its own words). The mounted-prefix check runs only if there is no row yet; it launches nothing.
            from resource_explorer import file_reach

            file_reach.ensure_checked(slug, registry)
        with nsr.port_session(_port()) as port:
            return nsr.submit_native_survey(
                registry, port, entity_type, slug, body.process_qualified_name,
                technology_type=tech, submitted_by=submitted_by, run_credential=run_credential)

    try:
        state = await asyncio.to_thread(_do)
    except nsr.NativeSurveyNotStarted as exc:
        # Fix round 2, F2: the start entry could not be written, so Egeria was never asked.
        raise HTTPException(status_code=503, detail=str(exc))
    except nsr.NativeSurveyBusy as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except nsr.NativeSurveyCannotRun as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except nsr.NativeSurveyError as exc:
        _log_activity(registry, entity_type, slug, body.process_qualified_name,
                      status="error", summary=f"Egeria did not accept the survey: {exc}")
        raise HTTPException(status_code=502, detail=str(exc))

    detail = {"engine_action_guid": state["engine_action_guid"], "submitted_by": submitted_by}
    if body.process_qualified_name in nsr.REQUEST_PARAMETERS:
        detail["request_parameters"] = dict(nsr.REQUEST_PARAMETERS[body.process_qualified_name])
    if run_credential is not None:
        from resource_explorer.run_credential_holder import SCOPE

        # Who it runs as and the scope; never the password.
        detail["run_credential"] = {"user": run_credential[0], "scope": SCOPE}
    _log_activity(
        registry, entity_type, slug, body.process_qualified_name, status="triggered",
        summary=(f"Submitted Egeria survey {body.process_qualified_name} on {slug}: "
                 f"engine action {state['engine_action_guid']}"
                 + (f" · as {run_credential[0]} (this run)" if run_credential is not None else "")),
        detail=detail)
    # Fix round 2, F3: Egeria has started the run. A registry that fails while the rows are read must not turn that into
    # a 500 the page reads as "not started": the answer says started, with no rows (the page reads them again).
    try:
        surveys = await asyncio.to_thread(lambda: nsr.native_survey_rows(registry, entity_type, slug, tech, **reader))
    except Exception as exc:  # noqa: BLE001 -- the start stands; the rows are read again later
        log.warning("native survey %s started on %s; its rows could not be read (%s)",
                    body.process_qualified_name, slug, nsr._short_error(exc))
        surveys = None
    return {"run": state, "surveys": surveys}


@router.post("/{entity_type}/{slug}/check")
async def check_native_survey_pointers(entity_type: str, slug: str, request: Request) -> dict:
    """Read the stored Egeria pointers (the database asset, the server) back from Egeria and return the
    rows with what that read established. One read per stored pointer; nothing is written. A pointer is
    reported gone only when Egeria answered that no such element exists -- a failed read is 'unreadable'."""
    from resource_explorer import catalog_and_survey as cas

    registry = ProjectRegistry()
    reader = _reader(request)      # who reads, read HERE in the request
    tech = _check(entity_type, slug, registry)

    def _do() -> dict:
        entity = nsr._resource_entity(registry, entity_type, slug)
        stored = bool((getattr(entity, "egeria_asset_guid", "") or "").strip()) or bool(
            entity is not None and hasattr(entity, "db_type") and cas.server_pointer(registry, entity))
        declared: dict = {}
        if not stored:
            pointers = {"database": "none", "server": "none", "error": ""}
        else:
            with nsr.port_session(_port()) as port:
                pointers = cas.check_pointers(registry, port, entity_type, slug)
                # Engine host step 4: is each mixed process declared in Egeria (read-only, one lookup each)?
                declared = nsr.check_declared(port, entity_type, tech)
        return {"technology_type": tech, "pointers": pointers, "declared": declared,
                "surveys": nsr.native_survey_rows(registry, entity_type, slug, tech, pointers=pointers,
                                                  declared=declared, **reader)}

    return await asyncio.to_thread(_do)


class RegisterRequest(BaseModel):
    #: The person's explicit "start again": submit a second database process even though an earlier one
    #: has not resolved. Never implied.
    start_again: bool = False


@router.post("/{entity_type}/{slug}/register")
async def register_with_egeria(entity_type: str, slug: str, request: Request,
                               body: RegisterRequest = RegisterRequest()) -> dict:
    """The one press that WRITES to Egeria here: register this database's SERVER (adopting it if it is
    already there), submit Egeria's server survey, and have Egeria's own process create the database.
    Optional -- nothing else in RE needs it. See `catalog_and_survey.py` for each write and read-back."""
    from resource_explorer import catalog_and_survey as cas
    from resource_explorer.run_queue import requested_by

    registry = ProjectRegistry()
    reader = _reader(request)      # who reads, read HERE in the request
    tech = _check(entity_type, slug, registry)
    submitted_by = requested_by()       # read HERE, in the request, then passed down
    from resource_explorer.web.routes.curate import authorize_resource_write
    await authorize_resource_write(registry, entity_type, slug, write="publish")         # Brief Z2
    proc = cas._process_for(entity_type, tech, nsr.KIND_CATALOG_AND_SURVEY)
    process_qn = proc.qualified_name if proc else ""

    def _do() -> dict:
        with nsr.port_session(_port()) as port:
            return cas.register_with_egeria(registry, port, entity_type, slug,
                                            technology_type=tech, submitted_by=submitted_by,
                                            start_again=body.start_again)

    try:
        result = await asyncio.to_thread(_do)
    except nsr.NativeSurveyBusy as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except nsr.NativeSurveyCannotRun as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except nsr.NativeSurveyError as exc:
        _log_activity(registry, entity_type, slug, process_qn, status="error",
                      summary=f"Egeria did not accept the registration: {exc}")
        raise HTTPException(status_code=502, detail=str(exc))

    _log_activity(
        registry, entity_type, slug, process_qn, status="triggered",
        summary=(f"Registered {slug}'s server with Egeria ({result['server'].get('how')}, "
                 f"server {result['server'].get('guid')}); database: {result['database'].get('how')}"),
        detail={"server": result["server"], "database": result["database"],
                "secrets_file_reprojected": bool(result["projected"]["written"]),
                "start_again": body.start_again, "submitted_by": submitted_by})
    return {"registered": result, "surveys": await asyncio.to_thread(
        lambda: nsr.native_survey_rows(registry, entity_type, slug, tech, **reader))}


@router.post("/{entity_type}/{slug}/refresh")
async def refresh_native_surveys(entity_type: str, slug: str, request: Request) -> dict:
    """Read back every in-flight native survey on this resource, read-only,
    and return the rows as they now stand. The UI polls this while anything is
    in flight; with nothing in flight it is a registry read and no Egeria call."""
    registry = ProjectRegistry()
    reader = _reader(request)      # who reads, read HERE in the request
    tech = _check(entity_type, slug, registry)

    def _do() -> list[dict]:
        # Nothing in flight -> no port is even built (no Egeria client, no
        # event loop): the poll target must be free when there is nothing to poll.
        # A run the sweep gave up on is not "in flight", but this is its "refresh to retry": read it too.
        if any(r["in_flight"] or (r.get("run") or {}).get("read_gave_up")
               for r in nsr.native_survey_rows(registry, entity_type, slug, tech)):
            with nsr.port_session(_port()) as port:
                nsr.refresh_resource(registry, port, entity_type, slug)
        return nsr.native_survey_rows(registry, entity_type, slug, tech, **reader)

    return {"technology_type": tech, "surveys": await asyncio.to_thread(_do)}


def _egeria_said(status: int, detail: str) -> JSONResponse:
    """A cancel refusal that is EGERIA'S answer (`by: egeria`). Refusals RE's own gate gives before Egeria is asked
    (sign-in, the access rule, "only a single-action survey") carry no `by`, and the page shows them verbatim."""
    return JSONResponse(status_code=status, content={"detail": detail, "by": "egeria"})


@router.post("/{entity_type}/{slug}/egeria-actions/{engine_action_guid}/cancel")
async def cancel_egeria_action(entity_type: str, slug: str, engine_action_guid: str, request: Request) -> dict:
    """Cancel one of this resource's waiting or running Egeria survey actions. An Egeria WRITE, and a person's
    direct act, so it runs as the signed-in CALLER (Brief I; never the daemon): nobody signed in is a 401.

    Fails CLOSED (Egeria's cancel has no status guard). Order, each step before the write: sign-in -> the GUID is one RE recorded for THIS resource (404
    otherwise; an arbitrary GUID is never sent) -> a fresh read: only a waiting or running action (409) -> Egeria's access rule for the engine action element
    (`engine_action_cancel_access`; 403 with the reason) -> (a failed or unadvanced read is a 502, not a guess; a process or registration GUID is a 409) -> a second read right before the write -> the cancel (the sub-second window between that read and the write cannot be closed) -> a read-back by the same status refresh
    the poll uses. The answer says `cancelled` only when that read-back says CANCELLED."""
    from resource_explorer.egeria_clients import Caller, EgeriaRefused, refusal
    from resource_explorer.workflows.curate import engine_action_cancel_access

    from resource_explorer.run_queue import requested_by

    registry = ProjectRegistry()
    reader = _reader(request)      # who reads, read HERE in the request
    _check(entity_type, slug, registry)
    Caller()                                  # NoCallerIdentity / CallerTokenExpired -> 401 (not logged)
    person = requested_by()                   # read HERE, in the request, as the run route does

    def refuse(process_qn: str, why: str) -> None:
        _log_activity(registry, entity_type, slug, process_qn, status="error",
                      summary=f"Cancel of Egeria action {engine_action_guid} refused: {why}",
                      detail={"engine_action_guid": engine_action_guid, "cancelled_by": person, "refused": why})

    try:
        run = nsr.own_action_run(registry, entity_type, slug, engine_action_guid)
    except nsr.NativeActionNotOurs as exc:
        refuse("", str(exc))
        raise HTTPException(status_code=404, detail=str(exc))
    process_qn = run.get("executor_ref") or (run.get("step_key") or "")[len(nsr.NATIVE_SURVEY_STEP_PREFIX):]

    def _gate() -> None:
        with nsr.port_session(_port()) as port:
            nsr.cancel_gate(registry, port, entity_type, slug, engine_action_guid)

    # The liveness gate (a fresh read) comes BEFORE the access check: a finished action is a 409, not a 403.
    try:
        await asyncio.to_thread(_gate)
    except nsr.NativeActionNotActive as exc:
        refuse(process_qn, str(exc))
        return _egeria_said(409, str(exc))       # Egeria's own fresh read said the action is over: "too late"
    except nsr.NativeSurveyCannotRun as exc:
        refuse(process_qn, str(exc))
        raise HTTPException(status_code=409, detail=str(exc))
    except nsr.NativeStatusUnreadable as exc:
        refuse(process_qn, str(exc))
        raise HTTPException(status_code=502, detail=str(exc))
    decision = await asyncio.to_thread(engine_action_cancel_access, registry, entity_type, slug, engine_action_guid)
    if not decision.allowed:
        refuse(process_qn, decision.reason)
        raise HTTPException(status_code=403, detail=decision.reason)

    def _do() -> dict:
        with nsr.port_session(_port()) as port:
            return nsr.cancel_native_action(registry, port, entity_type, slug, engine_action_guid, gated=True)

    try:
        out = await asyncio.to_thread(_do)
    except nsr.NativeActionNotOurs as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except nsr.NativeActionNotActive as exc:
        refuse(process_qn, str(exc))
        return _egeria_said(409, str(exc))
    except nsr.NativeSurveyCannotRun as exc:
        refuse(process_qn, str(exc))
        raise HTTPException(status_code=409, detail=str(exc))
    except nsr.NativeStatusUnreadable as exc:
        refuse(process_qn, str(exc))
        raise HTTPException(status_code=502, detail=str(exc))
    except nsr.NativeSurveyError as exc:
        _log_activity(registry, entity_type, slug, process_qn, status="error",
                      summary=f"Egeria did not cancel action {engine_action_guid}: {exc}",
                      detail={"engine_action_guid": engine_action_guid, "cancelled_by": person})
        refused = refusal(exc.__cause__) if exc.__cause__ is not None else None
        if isinstance(refused, EgeriaRefused):
            raise refused
        # The definition card's classifier, not a second one: a 4xx answer is a refusal (422, Egeria's reason; 403 when it
        # is a not-permitted), anything else -- 5xx, a timeout, no answer -- stays the 502 the page words as "not sent".
        cause = exc.__cause__
        if cause is not None and nsr.classify_cancel_answer(cause) == nsr.REFUSED:
            code = 403 if nsr._refusal_kind(cause) == nsr.CANCEL_NOT_PERMITTED else 422
            return _egeria_said(code, nsr._egeria_reason(cause))
        # Not sent: Egeria gave no answer. The flag tells the page to say so, whatever the status text is.
        return JSONResponse(status_code=502, content={"detail": str(exc), "unsent": True})

    _log_activity(
        registry, entity_type, slug, out["process_qualified_name"] or process_qn,
        status="ok" if out["cancelled"] else "unconfirmed",
        summary=(f"Cancelled Egeria action {engine_action_guid} on {slug}: Egeria now says {out['egeria_status']}"
                 if out["cancelled"] else
                 f"Asked Egeria to cancel action {engine_action_guid} on {slug}; the read-back says "
                 f"{out['egeria_status'] or 'nothing'}{' (' + out['read_error'] + ')' if out['read_error'] else ''}")
        + (f" · {out['note']}" if out["note"] else ""),
        detail={"engine_action_guid": engine_action_guid, "egeria_status": out["egeria_status"],
                "access_basis": decision.basis, "cancelled_by": person})
    tech = _technology_type(entity_type)
    return {**out, "access_basis": decision.basis, "surveys": await asyncio.to_thread(
        lambda: nsr.native_survey_rows(registry, entity_type, slug, tech, **reader))}


@router.post("/{entity_type}/{slug}/process-runs/{instance_guid}/cancel")
async def cancel_process_run(entity_type: str, slug: str, instance_guid: str, request: Request) -> dict:
    """Cancel an Egeria-coordinated run of an RE Survey Definition that RE started (design
    DESIGN-ENGINE-HOST-GOVERNANCE-ENGINE-API.md §2.4). An Egeria WRITE, made as the signed-in person who pressed it
    (owner rule, 2026-10-14: actions run as the initiating user), never the service account.

    Before Egeria hears of it, from RE's own rows (`nsr.process_cancel_refusal`, the same decision the button uses):
    sign-in (401); the GUID is a process instance RE recorded for THIS resource (404); RE started the run (409, owner
    Q3, admins included); the person is its starter or an admin (403, owner Q2); it is in flight (409). Then a FRESH
    read-back of the instance (`nsr.fresh_cancel_gate`): a read that fails is 502 ("cancel not sent · couldn't reach
    Egeria"), a run it shows over is 409 ("too late"), and neither sends anything. Then
    `cancel_governance_action_process`, the proof row (`declared.cancel`) and one read-back. The answer says
    `cancelled` only when that read-back shows CANCELLED; a 4xx is Egeria's refusal (`refused_kind`: not_permitted
    only from 401/403 or UserNotAuthorized, too_late when the read-back shows the run over, else refused); 5xx, 404 or
    no answer is `unsent`; a timeout is `unknown` and its read-back decides. Each is stored on the row as the press's
    proof and drawn from it."""
    from resource_explorer.egeria_clients import Caller

    registry = ProjectRegistry()
    reader = _reader(request)      # who reads, read HERE in the request
    tech = _check(entity_type, slug, registry)
    Caller()                                  # NoCallerIdentity / CallerTokenExpired -> 401 (not logged)
    person = reader["viewer"]

    def refuse(process_qn: str, why: str) -> None:
        _log_activity(registry, entity_type, slug, process_qn, status="error",
                      summary=f"Cancel of Egeria process run {instance_guid} refused: {why}",
                      detail={"process_instance_guid": instance_guid, "cancelled_by": person, "refused": why})

    try:
        run = nsr.own_action_run(registry, entity_type, slug, instance_guid)
    except nsr.NativeActionNotOurs as exc:
        refuse("", str(exc))
        raise HTTPException(status_code=404, detail=str(exc))
    process_qn = run.get("executor_ref") or ""
    refusal = nsr.process_cancel_refusal(run, viewer=person, viewer_is_admin=reader["viewer_is_admin"])
    if refusal is not None:
        code, why = refusal
        refuse(process_qn, why)
        raise HTTPException(status_code=code, detail=why)

    def _do() -> dict:
        with nsr.port_session(_port()) as port:
            # A fresh read-back first, never the stored state: no cancel is sent to a run Egeria already finished,
            # and a read that fails sends nothing (review round 1, item 4).
            gate = nsr.fresh_cancel_gate(registry, port, entity_type, slug, run)
            if gate is not None:
                return {"gate": gate}
            return nsr.cancel_process_run(registry, port, entity_type, slug, run, by=person)

    out = await asyncio.to_thread(_do)
    if "gate" in out:
        code, why = out["gate"]
        refuse(process_qn, why)
        raise HTTPException(status_code=code, detail=why)
    if out["unsent"] and not out.get("unknown"):
        summary = f"Cancel of process run {instance_guid} on {slug} not sent: could not reach Egeria ({out['unsent']})"
    elif out.get("unknown"):
        summary = (f"Cancel of process run {instance_guid} on {slug}: Egeria's answer is unknown ({out['unsent']}); "
                   + ("its read-back now shows CANCELLED" if out["cancelled"] else "its read-back decides"))
    elif out["refused"]:
        summary = f"Egeria refused the cancel of process run {instance_guid} on {slug}: {out['refused']}"
    elif out["cancelled"]:
        summary = f"Cancelled Egeria process run {instance_guid} on {slug}: Egeria now says CANCELLED"
    else:
        summary = (f"Asked Egeria to cancel process run {instance_guid} on {slug}; accepted, its read-back does not "
                   "show CANCELLED yet")
    _log_activity(registry, entity_type, slug, process_qn,
                  status="ok" if out["cancelled"] else ("error" if out["refused"] or (out["unsent"] and not out.get("unknown"))
                                                        else "unconfirmed"),
                  summary=summary, detail={"process_instance_guid": instance_guid, "cancelled_by": person,
                                           "accepted": out["accepted"], "refused": out["refused"],
                                           "refused_kind": out["refused_kind"], "unsent": out["unsent"]})
    return {"accepted": out["accepted"], "refused": out["refused"], "refused_kind": out["refused_kind"],
            "unsent": out["unsent"], "unknown": bool(out.get("unknown")), "cancelled": out["cancelled"],
            "surveys": await asyncio.to_thread(
                lambda: nsr.native_survey_rows(registry, entity_type, slug, tech, **reader))}


@router.get("/{entity_type}/{slug}/reports/{report_guid}")
async def native_survey_report(entity_type: str, slug: str, report_guid: str) -> dict:
    """The annotations RE read from one survey report, with the report's time.
    Served from RE's own store (no Egeria call), and only for a report that one
    of this resource's runs actually read in."""
    registry = ProjectRegistry()
    _check(entity_type, slug, registry)

    def _do() -> dict:
        runs = [r for r in registry.list_native_survey_runs(entity_type, slug)
                if r.get("survey_report_guid") == report_guid]
        if not runs:
            return {}
        run = runs[0]
        annotations = registry.query_native_survey_annotations(report_guid)
        # This is RE's own stored copy, read from Egeria at some time. The read time is the stored
        # annotations' own `read_at` (the run's report read time when they carry none). If the
        # `egeria_reset` marker is LATER than that read, Egeria has been reset since: the screen says so,
        # once. Nothing is asked of Egeria, and the annotations and their order are exactly what was stored.
        from resource_explorer.catalogue_commit import _ts, egeria_reset_at, reset_since_read
        copy_read_at = max((a.get("read_at") or "" for a in annotations), key=_ts, default="") \
            or (run.get("report_read_at") or "")
        try:
            reset_at = egeria_reset_at(registry, slug)
        except Exception as exc:  # noqa: BLE001 - no guess either way: "no reset" would call a stored copy live
            raise HTTPException(status_code=503, detail="Could not check whether Egeria was reset, so the stored "
                                f"copy is not shown as current · the record of Egeria resets could not be read: {exc}")
        return {
            "report_guid": report_guid,
            "report_at": run.get("survey_report_at") or "",
            "read_at": run.get("report_read_at") or "",
            "stored_copy_read_at": copy_read_at,
            "egeria_reset_at": reset_at,
            "reset_since": reset_since_read(copy_read_at, reset_at),
            "engine_action_guid": run.get("engine_action_guid") or "",
            "process_qualified_name": (run.get("step_key") or "")[len(nsr.NATIVE_SURVEY_STEP_PREFIX):]
            if (run.get("step_key") or "").startswith(nsr.NATIVE_SURVEY_STEP_PREFIX) else "",
            "requested_at": run.get("surveyed_at") or "",
            "annotations": annotations,
        }

    report = await asyncio.to_thread(_do)
    if not report:
        raise HTTPException(status_code=404,
                            detail="No such report has been read into RE for this resource")
    return report


def _log_activity(registry, entity_type, slug, process_qn, *, status, summary, detail=None) -> None:
    """CLAUDE.md rule 16: every operation writes an activity entry. Best-effort:
    a failure to log must not turn an accepted submission into an error."""
    try:
        from resource_explorer.activity_logger import log_survey

        log_survey(registry, entity_type=entity_type, entity_slug=slug, entity_name=slug,
                   entity_location="", intent="discovery", status=status, summary=summary,
                   detail=json.dumps(detail or {"process": process_qn}))
    except Exception as exc:  # noqa: BLE001
        log.warning("could not write the activity entry for native survey %s: %s", process_qn, exc)
