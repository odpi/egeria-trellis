"""A step of an RE Survey Definition takes no source report, whatever Egeria carries forward (owner, 2026-10-16).

Egeria hands every later step of a process the surveyReport of each earlier step; in step 6 of DatabaseAnalysisSurvey
that is the reports of RE's own earlier steps, not a source for `re-database-staleness`. Only the step-4 mixed process
(native survey, then staleness) derives from the previous step's report. Only the pyegeria boundary is faked.
"""
from __future__ import annotations

from tests.library_step_install import staleness_installed  # noqa: F401 - the fixture
from tests.test_engine_host_case3 import ANALYSIS, _process_action
from tests.test_engine_host_e4 import _row, _run_queue_once, _sweep, egeria, no_connection, reg  # noqa: F401
from tests.test_engine_host_step4_mixed_process import NATIVE_REPORT, _step2_action

CARRIED = ["5046523f-0000-4000-8000-000000000001", "8cfe0d62-0000-4000-8000-000000000002",
           "e7cd11cf-0000-4000-8000-000000000003"]


def _add_reports(action, reports):
    for g in reports:
        action["actionTargetElements"].append({"actionTargetName": "surveyReport", "actionTargetGUID": g})


def test_a_definition_step_with_carried_reports_runs_standalone_and_is_not_refused(
        staleness_installed, reg, egeria, no_connection):
    a = _process_action(egeria, "ea-6", "database_staleness", definition=ANALYSIS, next_guards=(),
                        previous=("ea-5", "COMPLETED", ["survey-completed", "Any"]))
    _add_reports(a, CARRIED)
    assert _sweep(reg).claimed == ["ea-6"]
    _run_queue_once(reg)
    done = egeria.actions["ea-6"]["completion"]
    assert done["status"] == "ACTIONED" and done["guards"][0] == "survey-completed", done
    assert "not choosing one" not in done["message"]
    [(report_guid, _body)] = egeria.reports()
    # no carried report was read: the only read is the read-back of the report this run made
    assert {c.args[0] for c in egeria.of("AssetMaker").get_asset_by_guid.call_args_list} <= {report_guid}
    texts = {}
    for r in egeria.relationships:
        if r["end1"] == report_guid and egeria.elements.get(r["end2"], {}).get("type") == "Annotation":
            p = egeria.elements[r["end2"]]["body"]["properties"]
            texts[p["annotationType"]] = p["summary"]
    assert texts["Writes Since Statistics Reset"] == \
        "writes since statistics reset: not measured · no survey report in this run"
    assert _row(reg)["target"]["outcome"]["report_guid"] == report_guid


def test_the_mixed_process_still_refuses_two_reports_and_a_non_predecessor(
        staleness_installed, reg, egeria, no_connection):
    action = _step2_action(egeria, "ea-two")
    _add_reports(action, [CARRIED[0]])
    assert _sweep(reg).claimed == ["ea-two"]
    _run_queue_once(reg)
    done = egeria.actions["ea-two"]["completion"]
    assert done["guards"] == ["survey-invalid"] and "2 surveyReport action targets" in done["message"], done
    assert egeria.reports() == []

    _step2_action(egeria, "ea-stale", report=CARRIED[1])
    _sweep(reg)
    _run_queue_once(reg)
    done = egeria.actions["ea-stale"]["completion"]
    assert done["guards"] == ["survey-invalid"], done
    assert f"surveyReport {CARRIED[1]} is not the report of the previous process step ({NATIVE_REPORT})" \
        in done["message"]


def test_an_undeterminable_process_keeps_refusing_ambiguity(staleness_installed, reg, egeria, no_connection):
    action = _step2_action(egeria, "ea-unknown")
    _add_reports(action, CARRIED[:2])
    action["processName"] = "GovActionProcess::NotDeclaredAnywhere@1791853850864::abc"
    assert _sweep(reg).claimed == ["ea-unknown"]
    _run_queue_once(reg)
    done = egeria.actions["ea-unknown"]["completion"]
    assert done["guards"] == ["survey-invalid"] and "not choosing one" in done["message"], done
