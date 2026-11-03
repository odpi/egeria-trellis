"""RE's engine host on the Governance Engine view service (DESIGN-ENGINE-HOST-GOVERNANCE-ENGINE-API.md, §3).

Only the pyegeria client boundary is faked, with test_engine_host_e4's `FakeEgeria`: every client RE builds goes through
`egeria_clients._build`, which returns `create_autospec(<real class>, spec_set=True, instance=True)`, so a call binds
the INSTALLED pyegeria signature; the fake's Governance Engine answers as Egeria does (the approved listing holds only
the engine's APPROVED, unclaimed actions; an action target status is keyed on the ActionTarget relationship GUID), and
its Automated Curation refuses the deprecated engine-action wrappers outright. Everything between the sweep and the
client is real: the sweep, the queue, the synthetic-step executor, the publisher, the routes and the read-back.

§3 tests 1-5, plus the owner's Q1 (relationship GUID, never the element's), Q2 (Cancel for the starter and admins only)
and Q3 (no Cancel for a run RE did not start). Test 6 (the live read-only probe) is recorded in the builder's handoff.
"""
from __future__ import annotations

import ast
import inspect
import json
import logging
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests.test_engine_host_case3 import ANALYSIS, INSTANCE, TECH, _process_action
from tests.test_engine_host_e4 import (  # noqa: F401 - fixtures and helpers reused
    DB_GUID,
    ENGINE_GUID,
    UNKNOWN_GUID,
    _daemon_user,
    _host,
    _rel_guid,
    _restart,
    _row,
    _rows,
    _run_queue_once,
    _sweep,
    egeria,
    no_connection,
    reg,
)

OTHER_ENGINE_GUID = "0a1b2c3d-0000-4000-8000-00000000e002"
ENGINE_HOST = Path(__file__).resolve().parent.parent / "resource_explorer" / "engine_host.py"
OLD_ENGINE_ACTION_CALLS = ("get_active_engine_actions", "get_active_claimed_engine_actions", "claim_engine_action",
                           "update_engine_action_status", "record_completion_status")


def _gev_calls(egeria, method: str | None = None) -> list:
    spec = egeria.of("GovernanceEngine")
    return [c for c in spec.mock_calls if c[0] and (method is None or c[0] == method)]


def _target_statuses(egeria, action_guid: str, element_guid: str) -> list[dict]:
    rel = _rel_guid(action_guid, element_guid)
    return [s for s in egeria.target_statuses if s["relationship"] == rel]


def _assert_bound_to_the_real_signatures(egeria) -> None:
    """§3 test 2: every call RE made on the Governance Engine client binds pyegeria's own signature."""
    from pyegeria import GovernanceEngine

    for name, args, kwargs in _gev_calls(egeria):
        inspect.signature(getattr(GovernanceEngine, name)).bind(None, *args, **kwargs)


# ── §3 test 1: end to end per write path ───────────────────────────────────────────────────────────────────────────

def test_completed_claims_runs_sets_each_target_and_completes_on_the_governance_engine(reg, egeria, no_connection):
    egeria.add_action("ea-ok")
    assert _sweep(reg).claimed == ["ea-ok"]
    _run_queue_once(reg)

    calls = [c[0] for c in _gev_calls(egeria) if c[0] != "create_egeria_bearer_token"]
    assert calls[:2] == ["get_approved_engine_actions", "claim_engine_action"], calls
    a = egeria.actions["ea-ok"]
    assert a["statusUpdates"] == ["IN_PROGRESS"]
    done = a["completion"]
    assert done["status"] == "ACTIONED" and done["guards"] == ["survey-completed"]
    # The ActionTarget's status, on the RELATIONSHIP (owner Q1): IN_PROGRESS at the start, and at the end the
    # ActivityStatus the completion maps to (ACTIONED -> COMPLETED; ACTIONED is a CompletionStatus, not an
    # ActivityStatus, and the fake refuses it as Egeria does), with the completion's own words.
    statuses = _target_statuses(egeria, "ea-ok", DB_GUID)
    assert [s["status"] for s in statuses] == ["IN_PROGRESS", "COMPLETED"], egeria.target_statuses
    assert statuses[0]["start_date"] and not statuses[0]["completion_date"]
    assert statuses[1]["completion_date"] and statuses[1]["message"] == done["message"]
    assert all(s["relationship"] != DB_GUID for s in egeria.target_statuses), "never the element's GUID"
    _assert_bound_to_the_real_signatures(egeria)

    row = _row(reg)          # the next read
    assert row["state"] == "succeeded"
    assert row["target"]["outcome"]["guard"] == "survey-completed"
    assert row["target"]["completion_recorded_at"]
    assert "target_status_errors" not in row["target"]
    assert row["target"]["action_targets"] == [{"name": "newAsset", "guid": DB_GUID,
                                                "relationship_guid": _rel_guid("ea-ok", DB_GUID)}]


def test_survey_invalid_sets_the_target_invalid_with_the_completion_words(reg, egeria, no_connection):
    egeria.add_action("ea-inv", target=UNKNOWN_GUID)
    assert _sweep(reg).claimed == ["ea-inv"]
    _run_queue_once(reg)

    done = egeria.actions["ea-inv"]["completion"]
    assert done["status"] == "INVALID" and done["guards"] == ["survey-invalid"]
    statuses = _target_statuses(egeria, "ea-inv", UNKNOWN_GUID)
    assert [s["status"] for s in statuses] == ["IN_PROGRESS", "INVALID"]
    assert statuses[1]["message"] == done["message"] and "not registered in Resource Explorer" in done["message"]
    row = _row(reg)
    assert row["state"] == "failed" and row["target"]["outcome"]["guard"] == "survey-invalid"


def test_failed_sets_the_target_failed_with_the_completion_words(reg, egeria, no_connection):
    egeria.add_action("ea-fail")
    _sweep(reg)
    egeria.fail["create_asset"] = RuntimeError("OMAG-COMMON-500-001 the repository is unavailable")
    _run_queue_once(reg)

    done = egeria.actions["ea-fail"]["completion"]
    assert done["status"] == "FAILED"
    statuses = _target_statuses(egeria, "ea-fail", DB_GUID)
    assert [s["status"] for s in statuses] == ["IN_PROGRESS", "FAILED"]
    assert statuses[1]["message"] == done["message"]
    assert _row(reg)["target"]["outcome"]["guard"] == "survey-failed"


def test_cancelled_mid_run_publishes_nothing_sends_no_completion_and_marks_the_target(reg, egeria, no_connection):
    """§2.4: Egeria cancels the process while RE runs step 2. The step runs to its end; before publishing RE reads the
    action back, sees CANCELLED, publishes no report, sends no completion, sets the worked target CANCELLED "cancelled
    in Egeria" and stores the outcome cancelled."""
    a = _process_action(egeria, "ea-mid", "db_derived")
    a["instance"] = INSTANCE
    assert _sweep(reg).claimed == ["ea-mid"]
    in_progress = egeria.of("GovernanceEngine").update_engine_action_status.side_effect

    def cancel_arrives_while_running(engine_action_guid, status, body=None):
        in_progress(engine_action_guid, status, body)
        egeria._cancel_process(INSTANCE)           # someone presses Cancel while the step runs

    egeria.of("GovernanceEngine").update_engine_action_status.side_effect = cancel_arrives_while_running
    _run_queue_once(reg)

    assert egeria.reports() == [], "a cancelled action publishes no report"
    assert egeria.actions["ea-mid"]["completion"] is None and not _gev_calls(egeria, "record_completion_status")
    statuses = _target_statuses(egeria, "ea-mid", DB_GUID)
    assert [(s["status"], s["message"]) for s in statuses] == [("IN_PROGRESS", None),
                                                              ("CANCELLED", "cancelled in Egeria")]
    row = _row(reg)
    assert row["state"] == "cancelled"
    outcome = row["target"]["outcome"]
    assert outcome["cancelled"] is True and outcome["message"] == "cancelled in Egeria"
    assert row["target"]["completion_not_sent"] == "cancelled in Egeria"
    assert "completion_recorded_at" not in row["target"]
    # The restart pass never completes a run Egeria cancelled (it is not even listed: CANCELLED is not active).
    assert _restart(reg).completed == []


# ── §3 test 2: the fakes bind the real signatures ──────────────────────────────────────────────────────────────────

def test_the_governance_engine_fake_refuses_a_keyword_pyegeria_does_not_have(reg, egeria):
    egeria.add_action("ea-sig")
    _sweep(reg)
    engine = egeria.of("GovernanceEngine")
    with pytest.raises(TypeError):
        engine.update_action_target_status(_rel_guid("ea-sig", DB_GUID), "COMPLETED", completionMessage="camel")
    with pytest.raises(TypeError):
        engine.get_approved_engine_actions(ENGINE_GUID, startFrom=0)


# ── §3 test 3: the client-side screen stays the gate ───────────────────────────────────────────────────────────────

def test_a_listing_that_returns_another_engines_work_still_claims_none_of_it(reg, egeria):
    """The server filters by engine; RE's `_screen` still checks the engine GUID. If the listing ever returns more
    than it should, nothing of another engine's is claimed. (Fail on purpose: with `_screen`'s engine-GUID check
    removed, this test fails.)"""
    egeria.overreturn = True
    theirs = egeria.add_action("ea-theirs")
    theirs["governanceEngineGUID"] = OTHER_ENGINE_GUID
    result = _sweep(reg)
    assert result.claimed == [] and "ea-theirs" in result.skipped.get("other_engine", [])
    assert not _gev_calls(egeria, "claim_engine_action")
    assert _rows(reg) == []


# ── §3 test 4: adoption is the restart pass's alone ────────────────────────────────────────────────────────────────

def test_an_own_claim_with_no_row_is_adopted_by_the_restart_pass_and_never_by_the_sweep(reg, egeria):
    egeria.add_action("ea-orphan", status="ACTIVATING", claimed_by=_daemon_user())
    assert _sweep(reg).adopted == [] and _rows(reg) == [], "the approved listing never shows a claimed action"
    egeria.overreturn = True                       # even a listing that shows it: the sweep leaves it
    swept = _sweep(reg)
    assert swept.adopted == [] and "ea-orphan" in swept.skipped.get("already_claimed", [])
    assert _rows(reg) == []
    egeria.overreturn = False
    assert _restart(reg).adopted == ["ea-orphan"]
    assert [r["target"]["engine_action_guid"] for r in _rows(reg)] == ["ea-orphan"]
    assert not _gev_calls(egeria, "claim_engine_action"), "adopted, never claimed a second time"


# ── §3 test 5: no Automated Curation engine-action call is left ────────────────────────────────────────────────────

def test_engine_host_names_no_automated_curation_client_and_no_old_listing():
    """`engine_host.py` builds no `AutomatedCuration` client at all (cancel lives on the route's port) and calls none
    of the server-wide or deprecated listings. Checked on the code, not the docstrings."""
    tree = ast.parse(ENGINE_HOST.read_text())
    names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    imported = {a.name for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) for a in n.names}
    assert "AutomatedCuration" not in names | imported
    called = {n.func.attr for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)}
    assert "get_active_engine_actions" not in called
    # The other four are called, and only on the Governance Engine client (the fake refuses them on Automated
    # Curation, so the end-to-end tests above fail on any regression).
    assert {"claim_engine_action", "update_engine_action_status", "record_completion_status",
            "get_active_claimed_engine_actions", "get_approved_engine_actions",
            "update_action_target_status"} <= called


def test_the_governance_engine_client_never_falls_back_to_the_old_urls():
    from pyegeria import GovernanceEngine

    from resource_explorer import engine_host

    assert inspect.signature(GovernanceEngine.__init__).parameters["legacy_fallback"].default is False

    class Client:
        legacy_fallback = True

    assert engine_host.governance_engine_client(Client()).legacy_fallback is False


def test_a_view_server_without_the_service_is_said_once_and_nothing_falls_back(reg, egeria, caplog):
    egeria.add_action("ea-404")
    host = _host(reg)
    try:
        host.sweep_once()                                     # builds the specs
        from pyegeria.core._exceptions import PyegeriaNotFoundException

        egeria.of("GovernanceEngine").get_approved_engine_actions.side_effect = PyegeriaNotFoundException(
            None, context={"caller method": "get_approved_engine_actions"}, additional_info={"relatedHTTPCode": 404})
        with caplog.at_level(logging.WARNING, logger="resource_explorer.engine_host"):
            first = host.sweep_once()
            second = host.sweep_once()
    finally:
        host.close()
    said = [r for r in caplog.records if "governance-engine view service" in r.getMessage()]
    assert len(said) == 1, [r.getMessage() for r in caplog.records]
    assert first.claimed == [] == second.claimed
    assert "governance-engine" in first.note


# ── owner Q1: the relationship GUID or nothing ─────────────────────────────────────────────────────────────────────

def test_a_target_without_a_relationship_guid_gets_no_status_and_the_run_says_so(reg, egeria, no_connection):
    a = egeria.add_action("ea-norel")
    for t in a["actionTargetElements"]:
        t.pop("actionTargetRelationshipGUID")
    _sweep(reg)
    _run_queue_once(reg)
    assert not _gev_calls(egeria, "update_action_target_status"), "never the element's GUID instead"
    row = _row(reg)
    assert row["state"] == "succeeded", "a target status is a courtesy; the completion still goes"
    [err] = row["target"]["target_status_errors"]
    assert err["target"] == DB_GUID and "actionTargetRelationshipGUID" in err["error"]


def test_a_target_status_egeria_refuses_is_recorded_and_never_blocks_the_completion(reg, egeria, no_connection):
    egeria.add_action("ea-tsfail")
    _sweep(reg)
    egeria.fail["update_action_target_status"] = RuntimeError("read timeout")
    _run_queue_once(reg)
    assert egeria.actions["ea-tsfail"]["completion"]["status"] == "ACTIONED"
    row = _row(reg)
    assert row["state"] == "succeeded"
    [err] = row["target"]["target_status_errors"]
    assert err["status"] == "IN_PROGRESS" and "read timeout" in err["error"]
    # The end status still went (one failure does not stop the next update).
    assert [s["status"] for s in _target_statuses(egeria, "ea-tsfail", DB_GUID)] == ["COMPLETED"]


# ── §2.4: Cancel on the definition card (owner Q2, Q3), end to end through the route ───────────────────────────────

STARTER = "dan"


def _running_definition_run(reg, egeria, *, started_in: str = "re") -> dict:
    """An Egeria-coordinated run of the Database Analysis Survey on coco_pharma, step 1 running in Egeria."""
    from resource_explorer import mixed_process
    from resource_explorer import native_survey_run as nsr

    first = sorted(mixed_process.definition_process(ANALYSIS).steps, key=lambda st: st.number)[0]
    a = _process_action(egeria, "ea-step1", first.request_type[len("re-"):].replace("-", "_"), status="IN_PROGRESS",
                        claimed_by=_daemon_user())
    a["instance"] = INSTANCE
    egeria.graphs[INSTANCE] = {"startingElement": {"elementGUID": INSTANCE}, "elementList": [
        {"type": {"typeName": "ActionRequester"}, "elementAtEnd1": True,
         "element": {"elementGUID": "ea-step1", "type": {"typeName": "EngineAction"}}}]}
    assert reg.record_native_survey_submission_once(
        "database", "coco_pharma", ANALYSIS, "2026-10-17T09:00:00", engine_action_guid=INSTANCE,
        submitted_by=STARTER if started_in == "re" else "", started_as="process", started_by="egeria",
        started_in=started_in, declared_extra=mixed_process.expected_steps_record(ANALYSIS))
    [run] = reg.list_native_survey_runs("database", "coco_pharma", ANALYSIS)
    from resource_explorer import catalog_and_survey as cas
    from resource_explorer.egeria_clients import Daemon, DaemonReason

    with nsr.port_session(cas.PyegeriaRegistrationPort(identity=Daemon(DaemonReason.SCHEDULER))) as port:
        state = nsr.refresh_mixed_run(reg, port, run, entity_type="database", slug="coco_pharma")
    assert state["state"] == "running", state
    return run


@pytest.fixture
def web(reg, egeria, monkeypatch):
    who = {"user_id": STARTER, "admin": False}
    monkeypatch.setattr("resource_explorer.registry.ProjectRegistry.__init__",
                        lambda self, db_path=None: setattr(self, "__dict__", reg.__dict__) or None)
    monkeypatch.setenv("TRELLIS_ANONYMOUS_READ", "true")
    monkeypatch.setattr("resource_explorer.auth.get_current_user",
                        lambda request: {"user_id": who["user_id"], "egeria_token": f"tok-{who['user_id']}",
                                         "role": "user"})
    monkeypatch.setattr("resource_explorer.web.admin_auth.is_admin_request", lambda request, cfg: who["admin"])
    from resource_explorer.web.app import app

    return TestClient(app), who


def _def_row(client) -> dict:
    r = client.get("/api/native-surveys/database/coco_pharma")
    assert r.status_code == 200, r.text
    rows = r.json()["surveys"]
    return next(r for r in rows if r["qualified_name"] == ANALYSIS)


def mixed_process_plan() -> list:
    from resource_explorer import mixed_process

    return mixed_process.expected_steps_record(ANALYSIS)["expected_steps"]


CANCEL_URL = f"/api/native-surveys/database/coco_pharma/process-runs/{INSTANCE}/cancel"


def _step1_ends_the_run(egeria, status: str = "FAILED") -> None:
    """Egeria's read of step 1 now says it ended and the process ended there (RE's engine host appends the note):
    the run is over, with no step active."""
    from resource_explorer.step_outcome import PROCESS_ENDED_PREFIX

    pvm = egeria.graphs["ea-step1"]["startingElement"]["elementProperties"]["propertyValueMap"]
    pvm["activityStatus"]["symbolicName"] = status
    pvm["completionMessage"] = {"class": "PrimitiveTypePropertyValue", "primitiveDefCategory": "OM_PRIMITIVE_TYPE_STRING",
                                "primitiveValue": f"step 1 ended · {PROCESS_ENDED_PREFIX} step 1"}


def _on_cancel(egeria, fn) -> None:
    """Egeria's answer to the next process cancel is `fn` (on the built Automated Curation spec, or the one built next)."""
    try:
        spec = egeria.of("AutomatedCuration")
    except StopIteration:
        egeria._cancel_process = fn
        return
    spec.cancel_governance_action_process.side_effect = fn


def test_the_starter_cancels_as_themself_and_the_line_reads_cancelled_only_from_egerias_read_back(reg, egeria, web):
    client, _who = web
    _running_definition_run(reg, egeria)
    assert _def_row(client)["process_cancel"]["offered"] is True

    r = client.post(CANCEL_URL)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["accepted"] is True and body["cancelled"] is True and body["refused"] == ""
    # Made as the person who pressed, never the service account (owner rule, 2026-10-14).
    [(guid, who)] = egeria.cancels
    assert guid == INSTANCE and who.kind == "caller" and who.user_id == STARTER
    assert ("AutomatedCuration", STARTER) in egeria.built
    # The proof row, then the line: ⊘ cancelled from the read-back.
    [run] = reg.list_native_survey_runs("database", "coco_pharma", ANALYSIS)
    press = json.loads(run["declared"])["cancel"]
    assert {k: press[k] for k in ("by", "instance_guid", "answer")} == {"by": STARTER, "instance_guid": INSTANCE,
                                                                        "answer": "accepted"}
    row = _def_row(client)
    assert row["run"]["egeria_status"] == "CANCELLED" and row["run"]["cancel"]["state"] == "done"
    done = row["run"]["cancel"]
    assert done["by"] == STARTER and done["at"] and done["of"] == len(mixed_process_plan()) and done["finished"] == 0
    assert not done.get("outside")
    assert "⊘ cancelled" in row["run"]["line"] and row["in_flight"] is False
    assert row["process_cancel"]["offered"] is False


def test_an_accepted_cancel_egeria_has_not_applied_yet_reads_cancelling_from_the_press_row(reg, egeria, web):
    client, _who = web
    _running_definition_run(reg, egeria)
    egeria.ignore_cancel = True                 # Egeria accepts the call; its read-back still says IN_PROGRESS
    body = client.post(CANCEL_URL).json()
    assert body["accepted"] is True and body["cancelled"] is False
    row = _def_row(client)
    assert row["run"]["state"] == "running" and row["run"]["cancel"]["state"] == "pending"
    assert row["process_cancel"]["offered"] is False, "no second press while one is pending"
    assert client.post(CANCEL_URL).status_code == 409


def test_a_cancel_egeria_refuses_shows_egerias_reason_and_changes_nothing_else(reg, egeria, web):
    from tests.test_engine_host_e4 import _refusal

    client, _who = web
    _running_definition_run(reg, egeria)
    egeria.fail["cancel_governance_action_process"] = _refusal(
        "OMAG-GENERIC-HANDLERS-403-009", "the process instance has already completed")
    body = client.post(CANCEL_URL).json()
    assert body["accepted"] is False and "already completed" in body["refused"]
    row = _def_row(client)
    assert row["run"]["state"] == "running"
    c = row["run"]["cancel"]
    assert c["state"] == "refused" and "already completed" in c["reason"] and c["kind"] == "not_permitted"
    assert row["process_cancel"]["offered"] is True, "not permitted: Cancel run stays"


def test_a_cancel_is_never_sent_to_a_run_the_fresh_read_shows_finished(reg, egeria, web):
    """Review round 1, item 4: the route reads the run back BEFORE it writes. Stored state says running; Egeria's
    fresh read says step 1 FAILED and nothing is active: 409 "too late", and no cancel reaches Egeria."""
    client, _who = web
    _running_definition_run(reg, egeria)
    _step1_ends_the_run(egeria)                                   # it ended before the press reached Egeria
    r = client.post(CANCEL_URL)
    assert r.status_code == 409 and r.json()["detail"] == "too late to cancel · the run had already finished"
    assert egeria.cancels == [], "no cancel is sent to a finished run"
    assert _def_row(client)["process_cancel"]["offered"] is False


def test_a_fresh_read_that_fails_sends_no_cancel_and_says_couldnt_reach_egeria(reg, egeria, web):
    from pyegeria.core._exceptions import PyegeriaConnectionException

    client, _who = web
    _running_definition_run(reg, egeria)
    egeria.fail["get_all_related_elements"] = PyegeriaConnectionException(
        context={"caller method": "get_all_related_elements"}, additional_info={"endpoint": "https://fake"})
    r = client.post(CANCEL_URL)
    assert r.status_code == 502 and r.json()["detail"] == "cancel not sent · couldn't reach Egeria · try again"
    assert egeria.cancels == []


def test_a_refusal_then_a_read_back_showing_the_run_over_is_too_late(reg, egeria, web):
    """A 409 from Egeria (not 401/403): its kind is from the read-back. The run had finished meanwhile: too late."""
    from tests.test_engine_host_e4 import _refusal

    client, _who = web
    _running_definition_run(reg, egeria)
    def refused_after_it_finished(process_instance_guid):
        _step1_ends_the_run(egeria)                                 # it finished as the press arrived
        raise _refusal("OMAG-GENERIC-HANDLERS-409-001", "the process instance is not active", http=409)

    _on_cancel(egeria, refused_after_it_finished)
    body = client.post(CANCEL_URL).json()
    assert body["refused_kind"] == "too_late" and body["accepted"] is False
    row = _def_row(client)
    assert row["run"]["cancel"]["state"] == "too_late" and row["process_cancel"]["offered"] is False


def test_a_4xx_refusal_that_is_not_401_or_403_is_never_not_permitted(reg, egeria, web):
    """Item 5(b): not permitted only from Egeria's 401/403 or a UserNotAuthorized message id. A 400 on a run still
    active is a plain refusal with Egeria's reason."""
    from tests.test_engine_host_e4 import _refusal

    client, _who = web
    _running_definition_run(reg, egeria)
    egeria.fail["cancel_governance_action_process"] = _refusal("OMAG-COMMON-400-009", "bad instance", http=400)
    body = client.post(CANCEL_URL).json()
    assert body["refused_kind"] == "refused" and "bad instance" in body["refused"]
    c = _def_row(client)["run"]["cancel"]
    assert c["state"] == "refused" and c["kind"] == "refused"


def test_an_accepted_cancel_on_a_run_that_then_finishes_reads_too_late_not_pending_forever(reg, egeria, web):
    """Item 5(a): "cancel requested … waiting for Egeria" only while the run is in flight. Egeria accepted, applied
    nothing, and the run then finished on its own: the press row reads too late."""
    client, _who = web
    _running_definition_run(reg, egeria)
    egeria.ignore_cancel = True
    assert client.post(CANCEL_URL).json()["accepted"] is True
    assert _def_row(client)["run"]["cancel"]["state"] == "pending"
    _step1_ends_the_run(egeria)
    client.post("/api/native-surveys/database/coco_pharma/refresh")
    row = _def_row(client)
    assert row["run"]["state"] not in ("running", "submitted")
    assert row["run"]["cancel"]["state"] == "too_late"


def test_an_unreachable_egeria_is_not_sent_never_refused_and_cancel_comes_back(reg, egeria, web):
    client, _who = web
    _running_definition_run(reg, egeria)
    from pyegeria.core._exceptions import PyegeriaConnectionException

    egeria.fail["cancel_governance_action_process"] = PyegeriaConnectionException(   # no HTTP answer
        context={"caller method": "cancel_governance_action_process"}, additional_info={"endpoint": "https://fake"})
    body = client.post(CANCEL_URL).json()
    assert body["unsent"] and body["refused"] == "" and body["accepted"] is False
    row = _def_row(client)
    assert row["run"]["cancel"]["state"] == "unsent"
    assert row["process_cancel"]["offered"] is True


def test_q2_only_the_starter_or_an_admin_is_offered_cancel_and_anyone_else_is_refused(reg, egeria, web):
    client, who = web
    _running_definition_run(reg, egeria)
    who["user_id"] = "peterprofile"
    assert _def_row(client)["process_cancel"]["offered"] is False
    r = client.post(CANCEL_URL)
    assert r.status_code == 403 and egeria.cancels == []
    who["admin"] = True
    assert _def_row(client)["process_cancel"]["offered"] is True
    assert client.post(CANCEL_URL).status_code == 200
    [(_guid, by)] = egeria.cancels
    assert by.user_id == "peterprofile", "an admin's cancel runs as the admin who pressed"


def test_q3_a_run_re_did_not_start_has_no_cancel_for_anyone_admins_included(reg, egeria, web):
    client, who = web
    _running_definition_run(reg, egeria, started_in="egeria")
    for user, admin in ((STARTER, False), ("peterprofile", True)):
        who["user_id"], who["admin"] = user, admin
        row = _def_row(client)
        assert row["process_cancel"]["offered"] is False, (user, admin)
        assert row["process_cancel"]["outside"] is True, "the line says it was started outside RE"
        assert client.post(CANCEL_URL).status_code == 409
    assert egeria.cancels == []


def test_cancel_needs_a_signed_in_person(reg, egeria, web, monkeypatch):
    client, _who = web
    _running_definition_run(reg, egeria)
    monkeypatch.setattr("resource_explorer.auth.get_current_user", lambda request: None)
    assert client.post(CANCEL_URL).status_code in (401, 403)
    assert egeria.cancels == []


def test_a_run_cancelled_outside_re_says_so_and_a_step_re_still_runs_is_finishing(reg, egeria, web):
    client, _who = web
    _running_definition_run(reg, egeria)
    from resource_explorer.egeria_clients import Daemon, DaemonReason, acting_as

    with acting_as(Daemon(DaemonReason.SCHEDULER)):
        egeria._cancel_process(INSTANCE)                    # cancelled in Egeria, no press of RE's
    egeria.cancels.clear()
    reg.enqueue_run("engine_action", {"engine_action_guid": "ea-step1"}, requested_by="")   # RE still holds the step
    client.post("/api/native-surveys/database/coco_pharma/refresh")
    row = _def_row(client)
    c = row["run"]["cancel"]
    assert c["state"] == "done" and c["outside"] is True and "by" not in c
    # Item 7: Egeria's own completionTime on the cancelled action, never RE's read time.
    from tests.test_engine_host_e4 import CANCEL_TIME_MS
    from datetime import UTC, datetime

    assert c["at"] == datetime.fromtimestamp(CANCEL_TIME_MS / 1000, UTC).isoformat(timespec="seconds").replace(
        "+00:00", "Z")
    [step] = [st for st in row["run"]["steps"] if st["status"] == "CANCELLED"]
    assert step["completion_time"] == c["at"]
    assert row["process_cancel"]["finishing"]["step"] == 1


# ── review round 1 ─────────────────────────────────────────────────────────────────────────────────────────────────

def test_the_target_status_fake_refuses_a_completion_status_word(reg, egeria):
    """Item 1: `ActionTargetStatusRequestBody.status` is an ActivityStatus; ACTIONED is not one."""
    egeria.add_action("ea-enum")
    _sweep(reg)
    engine = egeria.of("GovernanceEngine")
    with pytest.raises(Exception, match="not an ActivityStatus"):
        engine.update_action_target_status(_rel_guid("ea-enum", DB_GUID), "ACTIONED")
    from resource_explorer.engine_host import target_activity_status

    assert [target_activity_status({"status": s}) for s in ("ACTIONED", "INVALID", "FAILED")] == \
        ["COMPLETED", "INVALID", "FAILED"]
    assert target_activity_status({"status": "INVALID", "cancelled": True}) == "CANCELLED"


def test_a_step_cancelled_while_queued_is_never_set_in_progress_and_publishes_nothing(reg, egeria, no_connection):
    """Item 2: Egeria's updateEngineActionStatus checks only the claimant, so an IN_PROGRESS after a cancel would
    un-cancel it. Cancelled between the claim and the run: no IN_PROGRESS, no step, no report, no completion."""
    from resource_explorer.egeria_clients import Daemon, DaemonReason, acting_as

    a = _process_action(egeria, "ea-queued", "db_derived")
    a["instance"] = INSTANCE
    assert _sweep(reg).claimed == ["ea-queued"]
    with acting_as(Daemon(DaemonReason.SCHEDULER)):
        egeria._cancel_process(INSTANCE)
    _run_queue_once(reg)

    assert not _gev_calls(egeria, "update_engine_action_status"), "IN_PROGRESS is never sent to a cancelled action"
    assert egeria.actions["ea-queued"]["actionStatus"] == "CANCELLED", "still cancelled in Egeria"
    assert egeria.reports() == [] and not _gev_calls(egeria, "record_completion_status")
    assert [s["status"] for s in _target_statuses(egeria, "ea-queued", DB_GUID)] == ["CANCELLED"]
    row = _row(reg)
    assert row["state"] == "cancelled"
    assert row["target"]["outcome"]["cancelled"] is True
    assert row["target"]["completion_not_sent"] == "cancelled in Egeria"


def test_a_cancel_after_the_report_is_published_keeps_the_report_and_sends_no_completion(reg, egeria, no_connection):
    """Item 3: Egeria cancels while the report is being published. The report is in Egeria; the outcome keeps it with
    `cancelled_after_publish`, no completion is sent and the worked target ends CANCELLED. The run line is the done
    line (never "will publish nothing")."""
    from resource_explorer.egeria_clients import Daemon, DaemonReason, acting_as

    a = _process_action(egeria, "ea-late", "db_derived")
    a["instance"] = INSTANCE
    assert _sweep(reg).claimed == ["ea-late"]
    create_asset = egeria._create_asset

    def cancel_as_the_report_lands(asset_type=None, body=None):
        guid = create_asset(asset_type, body)
        with acting_as(Daemon(DaemonReason.SCHEDULER)):
            egeria._cancel_process(INSTANCE)
        return guid

    egeria._create_asset = cancel_as_the_report_lands      # wired when the run first builds its AssetMaker
    _run_queue_once(reg)

    [(report_guid, _body)] = egeria.reports()
    assert egeria.actions["ea-late"]["completion"] is None and not _gev_calls(egeria, "record_completion_status")
    row = _row(reg)
    outcome = row["target"]["outcome"]
    assert outcome["report_guid"] == report_guid and outcome["cancelled_after_publish"] is True
    assert outcome["guard"] == "survey-completed" and not outcome.get("cancelled")
    assert row["state"] == "cancelled" and "will publish nothing" not in (row.get("error") or "")
    assert [s["status"] for s in _target_statuses(egeria, "ea-late", DB_GUID)] == ["IN_PROGRESS", "CANCELLED"]
    assert _restart(reg).completed == []
    from resource_explorer import native_survey_run as nsr

    derived = {"cancelled": True, "steps": [{"status": "CANCELLED", "action_guid": "ea-late"}],
               "cancel_context": {"cancelled_step": {"step": 2, "label": "db_derived"}}}
    assert nsr._finishing_step(reg, derived) is None, "a step that published is never 'finishing'"


@pytest.mark.parametrize("make, expected", [
    ("timeout", "unknown"), ("connection", "unreachable"), ("not_found", "unreachable"), ("server_500", "unreachable"),
    ("bad_400", "refused"), ("conflict_409", "refused"), ("forbidden_403", "refused"), ("unauthorised_401", "refused"),
])
def test_the_cancel_answer_classifier_on_pyegerias_own_exceptions(make, expected):
    """Item 6, with pyegeria 6.2.5's real exception classes: only Egeria's 4xx is a refusal; 5xx, 404, a connection
    error and no answer are "not sent"; a timeout is its own unknown outcome."""
    import httpx
    from pyegeria.core._exceptions import (PyegeriaConnectionException, PyegeriaNotFoundException,
                                           PyegeriaTimeoutException)
    from tests.test_engine_host_e4 import _refusal

    from resource_explorer import native_survey_run as nsr

    exc = {
        "timeout": lambda: PyegeriaTimeoutException(context={}, additional_info={"endpoint": "https://fake"},
                                                    e=httpx.ReadTimeout("read timed out")),
        "connection": lambda: PyegeriaConnectionException(context={}, additional_info={"endpoint": "https://fake"}),
        "not_found": lambda: PyegeriaNotFoundException(None, context={}, additional_info={"relatedHTTPCode": 404}),
        "server_500": lambda: _refusal("OMAG-COMMON-500-001", "repository down", http=500),
        "bad_400": lambda: _refusal("OMAG-COMMON-400-009", "bad", http=400),
        "conflict_409": lambda: _refusal("OMAG-COMMON-409-001", "conflict", http=409),
        "forbidden_403": lambda: _refusal("OMAG-COMMON-403-001", "no", http=403),
        "unauthorised_401": lambda: _refusal("OMAG-COMMON-401-001", "no", http=401),
    }[make]()
    assert nsr.classify_cancel_answer(exc) == expected
    if expected == "refused":
        assert nsr._refusal_kind(exc) == ("not_permitted" if make in ("forbidden_403", "unauthorised_401")
                                          else "refused")


def test_a_5xx_answer_to_cancel_is_not_sent_never_refused(reg, egeria, web):
    from tests.test_engine_host_e4 import _refusal

    client, _who = web
    _running_definition_run(reg, egeria)
    egeria.fail["cancel_governance_action_process"] = _refusal("OMAG-COMMON-500-001", "repository down", http=500)
    body = client.post(CANCEL_URL).json()
    assert body["refused"] == "" and body["unsent"] and body["accepted"] is False
    row = _def_row(client)
    assert row["run"]["cancel"]["state"] == "unsent" and row["process_cancel"]["offered"] is True


def test_a_timed_out_cancel_is_decided_by_the_read_back_and_reads_cancelled_by_the_presser(reg, egeria, web):
    """Item 6: a timeout may have reached Egeria. It applied the cancel and the answer never came back: the read-back
    decides, and the line is "cancelled by <who>", never "cancelled in Egeria · not from Resource Explorer"."""
    import httpx
    from pyegeria.core._exceptions import PyegeriaTimeoutException

    client, _who = web
    _running_definition_run(reg, egeria)
    apply = egeria._cancel_process

    def applied_then_timed_out(process_instance_guid):
        apply(process_instance_guid)
        raise PyegeriaTimeoutException(context={}, additional_info={"endpoint": "https://fake"},
                                       e=httpx.ReadTimeout("read timed out"))

    _on_cancel(egeria, applied_then_timed_out)
    body = client.post(CANCEL_URL).json()
    assert body["unknown"] is True and body["refused"] == "" and body["cancelled"] is True
    c = _def_row(client)["run"]["cancel"]
    assert c["state"] == "done" and c["by"] == STARTER and not c.get("outside")


def test_any_press_row_makes_a_later_cancelled_cancelled_by_the_presser(reg, egeria, web):
    """Item 6: an unreachable press, then Egeria cancels (perhaps that press after all): "cancelled by", not outside."""
    from pyegeria.core._exceptions import PyegeriaConnectionException
    from resource_explorer.egeria_clients import Daemon, DaemonReason, acting_as

    client, _who = web
    _running_definition_run(reg, egeria)
    egeria.fail["cancel_governance_action_process"] = PyegeriaConnectionException(
        context={}, additional_info={"endpoint": "https://fake"})
    assert client.post(CANCEL_URL).json()["unsent"]
    with acting_as(Daemon(DaemonReason.SCHEDULER)):
        egeria._cancel_process(INSTANCE)
    client.post("/api/native-surveys/database/coco_pharma/refresh")
    c = _def_row(client)["run"]["cancel"]
    assert c["state"] == "done" and c["by"] == STARTER and not c.get("outside")


def test_a_worker_that_died_after_in_progress_leaves_the_target_to_the_restart_pass(reg, egeria, no_connection,
                                                                                   monkeypatch):
    """Item 8: the restart pass's completion (E5, from the ended row) ends the worked target too: FAILED, with the
    completion's own words."""
    from resource_explorer import engine_host

    class WorkerDied(BaseException):
        pass

    def died(*a, **k):
        raise WorkerDied()

    egeria.add_action("ea-died")
    _sweep(reg)
    decide = engine_host._decide
    monkeypatch.setattr(engine_host, "_decide", died)
    with pytest.raises(WorkerDied):
        _run_queue_once(reg)
    monkeypatch.setattr(engine_host, "_decide", decide)
    statuses = _target_statuses(egeria, "ea-died", DB_GUID)
    assert [s["status"] for s in statuses] == ["IN_PROGRESS"]
    row = _row(reg)
    reg.finish_run(row["id"], "failed", error="interrupted: the worker process ended")   # the reconciler
    assert _restart(reg).completed == ["ea-died"]
    done = egeria.actions["ea-died"]["completion"]
    statuses = _target_statuses(egeria, "ea-died", DB_GUID)
    assert [s["status"] for s in statuses] == ["IN_PROGRESS", "FAILED"]
    assert statuses[1]["message"] == done["message"] and statuses[1]["completion_date"]
    assert _row(reg)["target"].get("final_target_status_sent_at")
    _restart(reg)
    assert len(_target_statuses(egeria, "ea-died", DB_GUID)) == 2, "sent once"


def test_worked_targets_are_once_per_action_target_relationship():
    """Item 9: two ActionTarget relationships to one element are two targets; one listed twice is one."""
    from resource_explorer.engine_host import worked_targets

    target = {"action_targets": [{"name": "newAsset", "guid": DB_GUID, "relationship_guid": "rel-1"},
                                 {"name": "otherAsset", "guid": DB_GUID, "relationship_guid": "rel-2"},
                                 {"name": "newAsset", "guid": DB_GUID, "relationship_guid": "rel-1"}],
              "asset_guid": DB_GUID}
    assert [t["relationship_guid"] for t in worked_targets(target)] == ["rel-1", "rel-2"]


# ── card fixes after the live Cancel test (BRIEF-ENGINE-HOST-CANCEL-CARD-FIXES, 2026-10-18) ──────────────────────────

def test_d1_the_publish_path_records_publish_started_and_the_report_guid_before_the_report_is_filled(
        reg, egeria, no_connection):
    """(a) The run row proves the publish began (`publish_started_at`) before anything is written to Egeria, and names
    the report (`report_guid`) the moment its element exists, before the first annotation fills it. Observed only at
    the Egeria client boundary."""
    egeria.add_action("ea-proof")
    assert _sweep(reg).claimed == ["ea-proof"]
    seen: dict = {}
    create_asset, create_annotation = egeria._create_asset, egeria._create_annotation

    def at_create(asset_type=None, body=None):
        if ((body or {}).get("properties") or {}).get("typeName") == "SurveyReport":
            seen.setdefault("at_create", dict(_row(reg)["target"]))
        return create_asset(asset_type, body)

    def at_first_annotation(body=None):
        seen.setdefault("at_fill", dict(_row(reg)["target"]))
        return create_annotation(body)

    egeria._create_asset, egeria._create_annotation = at_create, at_first_annotation
    _run_queue_once(reg)

    [(report_guid, _body)] = egeria.reports()
    assert seen["at_create"].get("publish_started_at"), "publish_started_at is written before the report is created"
    assert not seen["at_create"].get("report_guid"), "no report GUID before the element exists"
    assert seen["at_fill"]["report_guid"] == report_guid, "the GUID is on the row before the report is filled"
    final = _row(reg)["target"]
    assert final["report_guid"] == report_guid and final["outcome"]["report_guid"] == report_guid


def _cancelled_definition_run_with_step_row(reg, egeria, client, step_target: dict, *, state: str = "running"):
    """A definition run the starter cancelled (Egeria read back CANCELLED on step 1), with RE's engine-host run row for
    step 1's action in `state` and carrying `step_target` (the proof the engine host wrote)."""
    _running_definition_run(reg, egeria)
    assert client.post(CANCEL_URL).json()["cancelled"] is True
    run_id = reg.enqueue_run("engine_action", {"engine_action_guid": "ea-step1", **step_target}, requested_by="")
    if state != "queued":
        reg.finish_run(run_id, state)
    client.post("/api/native-surveys/database/coco_pharma/refresh")
    return _def_row(client)


def test_d1_a_cancel_after_publish_started_reads_report_kept_never_will_publish_nothing(reg, egeria, web):
    """(b) The step is still finishing and its row proves the publish began: the finishing cue carries `report_kept`
    and the report, so the card says the report is kept. Without the proof it stays "will publish nothing"."""
    client, _who = web
    row = _cancelled_definition_run_with_step_row(
        reg, egeria, client, {"publish_started_at": "2026-10-18T07:40:10Z", "report_guid": "becae45f-rep"},
        state="queued")
    assert row["run"]["cancel"]["state"] == "done"
    fin = row["process_cancel"]["finishing"]
    assert fin["step"] == 1 and fin["report_kept"] is True and fin["report_guid"] == "becae45f-rep"
    assert fin["publish_started_at"] == "2026-10-18T07:40:10Z"


def test_d1_without_the_publish_proof_the_finishing_step_is_not_said_to_keep_a_report(reg, egeria, web):
    client, _who = web
    row = _cancelled_definition_run_with_step_row(reg, egeria, client, {}, state="queued")
    fin = row["process_cancel"]["finishing"]
    assert fin["step"] == 1 and not fin.get("report_kept")


def test_d2_the_done_line_carries_the_report_a_cancelled_after_publish_step_kept(reg, egeria, web):
    """(c) The step ended `cancelled_after_publish` with its report: the done cue carries that step's report link;
    "K of M steps finished" still does not count it."""
    client, _who = web
    row = _cancelled_definition_run_with_step_row(
        reg, egeria, client,
        {"outcome": {"guard": "survey-completed", "report_guid": "becae45f-rep", "cancelled_after_publish": True}},
        state="cancelled")
    assert row["process_cancel"]["finishing"] is None
    assert row["process_cancel"]["kept"] == [{"step": 1, "guid": "becae45f-rep"}]
    assert row["run"]["cancel"]["state"] == "done" and row["run"]["cancel"]["finished"] == 0


def test_d2_a_step_cancelled_before_it_published_keeps_no_report(reg, egeria, web):
    client, _who = web
    row = _cancelled_definition_run_with_step_row(
        reg, egeria, client, {"outcome": {"guard": "survey-invalid", "report_guid": "", "cancelled": True}},
        state="cancelled")
    assert row["process_cancel"]["kept"] == []


def test_d3_the_runs_history_says_cancelled_for_a_cancelled_run(reg, egeria, web):
    """(d) One source: the history entry reads the same `derive_mixed_state` the run line does."""
    from resource_explorer import native_survey_run as nsr

    client, _who = web
    _running_definition_run(reg, egeria)
    assert client.post(CANCEL_URL).json()["cancelled"] is True
    [entry] = nsr.definition_run_history(reg, "database", "coco_pharma")[ANALYSIS]
    assert entry["state"] == "cancelled"


def test_d3_a_run_that_failed_still_reads_failed_in_the_runs_history(reg, egeria, web):
    from resource_explorer import native_survey_run as nsr

    client, _who = web
    _running_definition_run(reg, egeria)
    _step1_ends_the_run(egeria)
    client.post("/api/native-surveys/database/coco_pharma/refresh")
    [entry] = nsr.definition_run_history(reg, "database", "coco_pharma")[ANALYSIS]
    assert entry["state"] == "failed"


# ── review fix round (2026-10-18) ────────────────────────────────────────────────────────────────────────────────────

def test_fix1_publish_started_without_a_report_guid_reads_being_written_not_kept(reg, egeria, web):
    """A row with `publish_started_at` and no `report_guid` proves publishing began, not that a report exists: the
    cue carries no `report_kept` (the JS then says "its report is being written")."""
    client, _who = web
    row = _cancelled_definition_run_with_step_row(
        reg, egeria, client, {"publish_started_at": "2026-10-18T07:40:10Z"}, state="queued")
    fin = row["process_cancel"]["finishing"]
    assert fin["step"] == 1 and fin["report_kept"] is False and fin["report_guid"] == ""
    assert fin["publish_started_at"] == "2026-10-18T07:40:10Z"


def test_fix2_the_last_run_summary_of_a_cancelled_run_reads_cancelled_not_failed(reg, egeria, web):
    from resource_explorer import native_survey_run as nsr

    client, _who = web
    _running_definition_run(reg, egeria)
    assert client.post(CANCEL_URL).json()["cancelled"] is True
    [last] = nsr.coordinated_last_runs(reg, "database", "coco_pharma").values()
    assert last["last_run_status"] == "cancelled"


def test_fix2_the_last_run_summary_of_a_real_failure_still_reads_failed(reg, egeria, web):
    from resource_explorer import native_survey_run as nsr

    client, _who = web
    _running_definition_run(reg, egeria)
    _step1_ends_the_run(egeria)
    client.post("/api/native-surveys/database/coco_pharma/refresh")
    [last] = nsr.coordinated_last_runs(reg, "database", "coco_pharma").values()
    assert last["last_run_status"] == "failed"


def test_fix3_a_kept_report_falls_back_to_the_rows_own_report_guid_proof(reg, egeria, web):
    """The cancelled step's outcome has no report_guid (publish raised after create, or the worker died then) but the
    row's top-level proof names the report that exists: the done line links it."""
    client, _who = web
    row = _cancelled_definition_run_with_step_row(
        reg, egeria, client,
        {"report_guid": "becae45f-rep", "publish_started_at": "2026-10-18T07:40:10Z",
         "outcome": {"guard": "survey-failed", "report_guid": "", "cancelled_after_publish": True}},
        state="cancelled")
    assert row["process_cancel"]["kept"] == [{"step": 1, "guid": "becae45f-rep"}]


def test_fix5a_a_proof_row_that_cannot_be_written_leaves_the_publish_completed(reg, egeria, no_connection, monkeypatch):
    """merge_run_target raising inside publish_proof never stops or fails the publish."""
    egeria.add_action("ea-noproof")
    assert _sweep(reg).claimed == ["ea-noproof"]

    real = reg.merge_run_target

    def boom(run_id, patch):
        if set(patch) & {"publish_started_at", "report_guid"}:       # the proof writes only; the outcome still lands
            raise RuntimeError("registry unavailable")
        return real(run_id, patch)

    monkeypatch.setattr(reg, "merge_run_target", boom)
    _run_queue_once(reg)
    [(report_guid, _body)] = egeria.reports()
    outcome = _row(reg)["target"]["outcome"]
    assert outcome["guard"] == "survey-completed" and outcome["report_guid"] == report_guid
