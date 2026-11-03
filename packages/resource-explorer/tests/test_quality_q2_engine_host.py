"""Quality slice Q2, the engine host: the two steps that were held are now dispatched by Egeria.

The design's required test (DESIGN-QUALITY-AT-SCALE-AND-TESTGEN.md section 3, Q2): an engine action for
`run_quality_checks` on a database with ZERO accepted checks completes `survey-invalid` with a sentence naming why,
and creates no SurveyReport -- never an empty `survey-completed` report. With accepted checks it completes
`survey-completed` with one QualityAnnotation per evaluated check, in a report linked to the action, written as the
engine host for the requester (the identity of work Egeria dispatched).

Only the pyegeria boundary (test_engine_host_e4's `FakeEgeria`: autospec clients, strict annotation beans) and the
surveyed Postgres are faked; the sweep, the queue, the synthetic-step executor, the capability gate, the publisher and
the real run-checks code are real.
"""
from __future__ import annotations

import json

import pytest

from tests.test_engine_host_case3 import (  # noqa: F401 - fixtures and helpers reused
    PASSWORD,
    _process_action,
    _with_credential,
    egeria,
    no_connection,
    reg,
)
from tests.test_engine_host_e4 import _daemon_user, _rows, _run_queue_once, _sweep
from tests.test_value_profile_quality_checks import FakeConn, _connect

QUALITY = "GovActionProcess::DatabaseQualitySurvey"


@pytest.fixture(autouse=True)
def _no_live_database(monkeypatch):
    """Nothing in this file may reach a database: psycopg2 itself refuses (a prerequisite step once tried, and the
    shared Postgres on 5442 answered 'authentication failed'; never again)."""
    import psycopg2

    def refuse(*a, **k):
        raise AssertionError("a test opened a real PostgreSQL connection")

    monkeypatch.setattr(psycopg2, "connect", refuse)
SLUG = "coco_pharma"
NO_ACCEPTED = "no accepted checks: accept proposals in Curate first; nothing was run"


def test_run_quality_checks_with_no_accepted_check_completes_survey_invalid_with_a_sentence(reg, egeria, no_connection):
    _with_credential(reg)
    _process_action(egeria, "ea-run", "run_quality_checks", definition=QUALITY, next_guards=())
    assert _sweep(reg).claimed == ["ea-run"]                   # unheld: claimed, not refused at the claim
    assert _run_queue_once(reg) is not None

    action = egeria.actions["ea-run"]
    done = action["completion"]
    assert done["status"] == "INVALID" and done["guards"] == ["survey-invalid", "Any"], done
    assert NO_ACCEPTED in done["message"] and done["message"].startswith("run_quality_checks did not run on coco_pharma")
    # never an empty completed report: no SurveyReport exists, no target carries one
    assert egeria.reports() == [] and done["targets"] == []
    [row] = _rows(reg)
    assert row["target"]["outcome"]["guard"] == "survey-invalid" and not row["target"]["outcome"]["report_guid"]
    assert "no stored schema inventory" not in done["message"]       # the sentence is this step's own, not db_derived's
    assert PASSWORD not in json.dumps(egeria.actions)


def test_run_quality_checks_with_accepted_checks_completes_survey_completed_with_one_annotation_per_check(
        reg, egeria, monkeypatch):
    from resource_explorer.surveyors.database import connection

    _with_credential(reg)
    params = {"baseline_ct": None, "baseline_value_ct": None, "baseline_value": None, "threshold_value": 0,
              "severity": "error", "profile_surveyed_at": "2026-10-14T09:00:00"}
    reg.record_quality_verdict(SLUG, "public.orders.order_id::Unique", "accepted", params, decided_by="steward")
    reg.record_quality_verdict(SLUG, "public.orders.customer_id::Required", "accepted", params, decided_by="steward")

    def measure(sql):
        return 0 if 'COUNT(DISTINCT "order_id")' in sql else 7          # Unique holds, Required fails (7 missing)

    conn = FakeConn(checks={("public", "orders"): {"record_ct": 40000, "measure": measure}})
    monkeypatch.setattr(connection, "database_connection", lambda *a, **k: _connect(conn)())
    _process_action(egeria, "ea-run", "run_quality_checks", definition=QUALITY, next_guards=())
    assert _sweep(reg).claimed == ["ea-run"]
    assert _run_queue_once(reg) is not None

    done = egeria.actions["ea-run"]["completion"]
    [(report_guid, report)] = egeria.reports()
    assert done["status"] == "ACTIONED" and done["guards"] == ["survey-completed", "Any"], done
    assert done["targets"] == [{"actionTargetName": "surveyReport", "actionTargetGUID": report_guid}]
    assert egeria.annotations_under(report_guid) == 2
    bodies = [e["body"]["properties"] for e in egeria.elements.values() if e["type"] == "Annotation"]
    assert sorted((b["qualityDimension"], b["qualityScore"]) for b in bodies) == [("completeness", 0),
                                                                                  ("uniqueness", 100)]
    assert {b["class"] for b in bodies} == {"QualityAnnotationProperties"} and egeria.dropped == []
    # work Egeria dispatched is the engine host's, not the queue's and not a person's session
    from resource_explorer.egeria_clients import DaemonReason

    writers = {(w.reason, w.client_user) for cls, method, w in egeria.calls if method == "create_annotation"}
    assert writers == {(DaemonReason.ENGINE_HOST.value, _daemon_user())}
    [row] = _rows(reg)
    assert row["target"]["outcome"]["annotation_count"] == 2
    assert row["target"]["outcome"]["annotations_in_egeria"] == 2


# ── a dispatched step never leaves an empty report (design rulings A.1, A.2; review H1) ───────────────────────────

def _store_profile(reg):
    """The value profile of the engine-host world's three columns, stored through the real profile code."""
    from resource_explorer.surveyors.database.quality.value_profile import run_value_profile
    from tests.test_value_profile_quality_checks import BOUNDS

    profile = {("public", "customer"): {"record_ct": 5000, "columns": [
                   {"value_ct": 5000, "distinct_value_ct": 5000, "null_value_ct": 0}]},
               ("public", "orders"): {"record_ct": 40000, "columns": [
                   {"value_ct": 40000, "distinct_value_ct": 40000, "null_value_ct": 0},
                   {"value_ct": 39000, "distinct_value_ct": 5000, "null_value_ct": 1000}]}}
    entity = reg.get_database(SLUG)
    run_value_profile(entity, reg, _connect(FakeConn(profile=profile)), bounds=BOUNDS,
                      surveyed_at="2026-10-21T08:00:00+00:00", ran_as="surveyor")


def _annotation_props(egeria, report_guid):
    return [egeria.elements[r["end2"]]["body"]["properties"] for r in egeria.relationships
            if r["end1"] == report_guid and egeria.elements.get(r["end2"], {}).get("type") == "Annotation"]


def test_propose_with_proposals_completes_with_exactly_one_request_for_action(reg, egeria, no_connection):
    _store_profile(reg)
    _process_action(egeria, "ea-propose", "propose_quality_checks", definition=QUALITY, next_guards=())
    assert _sweep(reg).claimed == ["ea-propose"]
    assert _run_queue_once(reg) is not None

    done = egeria.actions["ea-propose"]["completion"]
    [(report_guid, _)] = egeria.reports()
    assert done["status"] == "ACTIONED" and done["guards"] == ["survey-completed", "Any"], done
    [only] = _annotation_props(egeria, report_guid)          # ONE annotation: never an empty report, never a flood
    proposed = sum(1 for r in reg.query_detail_rows("database_quality_proposals", SLUG) if r["state"] == "proposed")
    assert proposed > 0
    assert only["class"] == "RequestForActionProperties"
    assert only["actionRequested"] == f"review {proposed} proposed quality checks in Resource Explorer"
    batch = reg.query_detail_rows("database_quality_proposals", SLUG)[0]["surveyed_at"]
    assert only["additionalProperties"]["proposedChecks"] == str(proposed)
    assert only["additionalProperties"]["runId"] == batch
    assert egeria.dropped == []
    assert egeria.actions["ea-propose"]["completion"]["targets"] == [
        {"actionTargetName": "surveyReport", "actionTargetGUID": report_guid}]


def test_propose_with_nothing_proposed_completes_with_exactly_one_measure_saying_so(reg, egeria, no_connection):
    """A measured outcome, not an absence: a database whose only profiled table is a view (not applicable) selects no
    rule. The one annotation says so in the design's words."""
    from tests.test_value_profile_quality_checks import PROFILED_AT  # noqa: F401 - the shape of a stored profile

    reg.write_detail_rows("database_value_profiles", SLUG, "2026-10-21T08:00:00+00:00", rows=[{
        "schema_name": "public", "table_name": "v_orders", "column_name": "order_id", "general_type": "N",
        "record_ct": None, "sample_ratio": None, "state": "not_applicable", "reason": "a view, not a table",
        "ran_as": "surveyor"}])
    _process_action(egeria, "ea-none", "propose_quality_checks", definition=QUALITY, next_guards=())
    assert _sweep(reg).claimed == ["ea-none"]
    assert _run_queue_once(reg) is not None

    done = egeria.actions["ea-none"]["completion"]
    [(report_guid, _)] = egeria.reports()
    assert done["status"] == "ACTIONED" and done["guards"] == ["survey-completed", "Any"], done
    [only] = _annotation_props(egeria, report_guid)
    assert only["class"] == "ResourceMeasureAnnotationProperties"
    assert only["summary"] == "0 checks proposed · 0 columns profiled · no selection rule matched"
    assert only["resourceProperties"]["proposedChecks"] == "0"
    assert only["additionalProperties"]["resultState"] == "measured"
    assert egeria.dropped == []


def test_run_checks_publishes_every_evaluated_check_and_a_score_only_for_a_conclusion(reg, egeria, monkeypatch):
    """Review H1/A.2: three accepted checks that do NOT conclude (the table is refused, the column is gone, a
    parameter is bad) used to build no annotation and complete an EMPTY report. Each is now a QualityAnnotation with its
    state word and its reason, and none carries a qualityScore: a 0 would read as failed."""
    from resource_explorer.surveyors.database import connection

    _with_credential(reg)
    params = {"baseline_ct": None, "baseline_value_ct": None, "baseline_value": None, "threshold_value": 0,
              "severity": "error", "profile_surveyed_at": "2026-10-14T09:00:00"}
    reg.record_quality_verdict(SLUG, "public.orders.order_id::Unique", "accepted", params, decided_by="steward")
    reg.record_quality_verdict(SLUG, "public.orders.gone::Required", "accepted", params, decided_by="steward")
    conn = FakeConn(checks={("public", "orders"): {"record_ct": 40000, "measure": lambda sql: None}},
                    facts={("public", "orders"): {"rls_filters": True}})
    monkeypatch.setattr(connection, "database_connection", lambda *a, **k: _connect(conn)())
    _process_action(egeria, "ea-run", "run_quality_checks", definition=QUALITY, next_guards=())
    assert _sweep(reg).claimed == ["ea-run"]
    assert _run_queue_once(reg) is not None

    done = egeria.actions["ea-run"]["completion"]
    [(report_guid, _)] = egeria.reports()
    assert done["status"] == "ACTIONED" and done["guards"] == ["survey-completed", "Any"], done
    bodies = _annotation_props(egeria, report_guid)
    assert len(bodies) == 2 and {b["class"] for b in bodies} == {"QualityAnnotationProperties"}
    by_key = {b["additionalProperties"]["checkKey"]: b for b in bodies}
    # the table is refused for row-level security: not_permitted; the gone column: not_established
    assert by_key["public.orders.order_id::Unique"]["additionalProperties"]["checkState"] == "not_permitted"
    assert by_key["public.orders.gone::Required"]["additionalProperties"]["checkState"] == "not_established"
    for body in bodies:
        assert "qualityScore" not in body, body                  # nothing concluded: no 0 that would read as failed
        assert body["additionalProperties"]["checkStateReason"] and body["qualityDescription"]
    assert egeria.dropped == []


def test_a_dispatched_quality_step_that_built_nothing_never_completes_an_empty_report(reg, egeria, monkeypatch):
    """The property, across the three steps: whichever way a step has nothing to report, the action completes
    survey-invalid with a sentence and no SurveyReport exists. The value profile and the proposal step are stood in at
    the adapter with what the real code returns when it has nothing (`not_established`, no annotation); the run step is
    the real one with no accepted check."""
    from resource_explorer.surveyors.database import survey_definition_adapter as db

    _store_profile(reg)          # the proposal step's precondition (a stored profile) holds
    monkeypatch.setitem(db._ADAPTER.re_analysis_steps, "postgres_value_profile", lambda e, r, **k: {
        "value_profile": {"state": "not_established", "reason": "no stored schema inventory"}, "annotations": []})
    monkeypatch.setitem(db._ADAPTER.re_analysis_steps, "propose_quality_checks", lambda e, r, **k: {
        "quality_proposals": {"state": "not_established", "reason": "no value profile stored"}})
    _with_credential(reg)
    for guid, step in (("ea-vp", "postgres_value_profile"), ("ea-pr", "propose_quality_checks"),
                       ("ea-rc", "run_quality_checks")):
        _process_action(egeria, guid, step, definition=QUALITY, next_guards=())
    assert sorted(_sweep(reg).claimed) == ["ea-pr", "ea-rc", "ea-vp"]
    while _run_queue_once(reg) is not None:
        pass
    assert egeria.reports() == []
    for guid, why in (("ea-vp", "built no annotation"), ("ea-pr", "built no annotation"), ("ea-rc", NO_ACCEPTED)):
        done = egeria.actions[guid]["completion"]
        assert done["status"] == "INVALID" and done["guards"][0] == "survey-invalid", (guid, done)
        assert why in done["message"], (guid, done["message"])
        assert done["targets"] == []
