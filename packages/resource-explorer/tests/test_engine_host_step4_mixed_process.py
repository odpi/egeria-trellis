"""Engine host, step 4: one mixed Egeria process, a native survey step chained to an RE step.

Design: docs/design-notes/SLICE-ENGINE-HOST-STEP4-MIXED-PROCESS.md (§5.2, tests 1-8) and the owner's decision for
this step that RE's Run can start the process (runnable only with the engine host on, the process declared in Egeria
and the native step's credentials in place).

Only the pyegeria boundary is faked, with test_engine_host_e4's `FakeEgeria` (every client a
`create_autospec(<real class>, spec_set=True, instance=True)`), answering with the shapes captured live, read-only,
on 2026-10-15 (M2): tests/fixtures/egeria_6_2/engine_action__*, process_instance__*, survey_report__*. Everything
between the sweep and the client is real: the sweep, the queue, the step host, the library step, the report
publisher, the native-survey Run path and its read-back.
"""
from __future__ import annotations

import copy
import json
import logging
import re
from pathlib import Path

import pytest

from tests.library_step_install import staleness_installed  # noqa: F401 - the fixture
from tests.test_engine_host_e4 import (  # noqa: F401 - fixtures and helpers reused
    DB_GUID,
    PRIVATE_DB_GUID,
    REQUESTER,
    _daemon_user,
    _row,
    _run_queue_once,
    _sweep,
    egeria,
    no_connection,
    reg,
)

PKG = Path(__file__).resolve().parent.parent
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "egeria_6_2"
SCRIPT = PKG / "scripts" / "generate_engine_host_declaration.py"
PROCESS_DOC = PKG / "docs" / "dr-egeria" / "engine-host" / "processes" / "postgres-survey-then-derived.md"
PROCESS_QN = "GovActionProcess::ResourceExplorer::PostgresSurveyThenDerived"
STEP2_TYPE = "re-database-staleness"
TECH = "PostgreSQL Relational Database"

# From the M2 fixtures: the native survey step's report and the process instance its actions are anchored to.
NATIVE_REPORT = "cb92eedc-5e24-4da7-ba5e-fc11d1707d8f"
INSTANCE = "06994d5d-a228-408c-8842-fd6256db4c34"
SURVEY_STEP = "532d040d-e2ca-45b8-9167-675d6e20128f"


def _fx(name: str):
    return json.loads((FIXTURES / name).read_text())["answer"]


def _native_report(*, written: dict[str, int] | None = None, tables: bool = True, subject: str = DB_GUID,
                   guid: str = NATIVE_REPORT) -> dict:
    """The live native PostgreSQLSurvey report (M2), made a report of `subject` (its ReportSubject and anchor), with
    some tables' "Number Of Rows Inserted" changed."""
    answer = copy.deepcopy(_fx("survey_report__native_postgres_database__asset_by_guid.json"))
    answer["elementHeader"]["guid"] = guid
    answer["elementHeader"]["anchor"]["classificationProperties"]["anchorGUID"] = subject
    for e in answer["reportSubjects"]:
        e["relatedElement"]["elementHeader"]["guid"] = subject
    keep = []
    for entry in answer["reportedAnnotations"]:
        props = entry["relatedElement"]["properties"]
        if props.get("annotationType") == "Capture Database Table Measurements":
            if not tables:
                continue
            name = props["resourceProperties"]["Table Name"]
            if written and name in written:
                props["resourceProperties"]["Number Of Rows Inserted"] = str(written[name])
        keep.append(entry)
    answer["reportedAnnotations"] = keep
    return answer


def _set_pv(element: dict, key: str, value) -> None:
    pvm = element.setdefault("elementProperties", {}).setdefault("propertyValueMap", {})
    if key == "activityStatus":
        pvm[key] = {"class": "EnumTypePropertyValue", "symbolicName": value, "typeName": "ActivityStatus"}
    elif key == "completionGuards":
        pvm[key] = {"class": "ArrayTypePropertyValue", "arrayCount": len(value), "arrayValues": {"propertyValueMap": {
            str(i): {"class": "PrimitiveTypePropertyValue", "primitiveValue": g} for i, g in enumerate(value)}}}
    else:
        pvm[key] = {"class": "PrimitiveTypePropertyValue", "primitiveValue": value}


def _anchor(element: dict, instance: str) -> None:
    for c in element.get("classifications") or []:
        if c.get("classificationName") == "Anchors":
            c["classificationProperties"]["propertiesAsStrings"]["anchorGUID"] = instance


def _process_name(instance: str = INSTANCE) -> str:
    return f"{PROCESS_QN}@1791853850864::{instance}"


def _step2_graph(guid: str, *, previous_status: str = "COMPLETED", previous_guards=("survey-completed",),
                 status: str = "APPROVED", guards=(), report: str = "", instance: str = INSTANCE) -> dict:
    """The next action after a native survey step, as Egeria answered it live (M2), made RE's step 2."""
    g = copy.deepcopy(_fx("engine_action__next_step_after_survey__related.json"))
    start = g["startingElement"]
    start["elementGUID"] = guid
    _set_pv(start, "requestType", STEP2_TYPE)
    _set_pv(start, "processName", _process_name(instance))
    _set_pv(start, "activityStatus", status)
    _set_pv(start, "completionGuards", list(guards))
    _anchor(start, instance)
    for e in g["elementList"]:
        if e["type"]["typeName"] == "ControlFlow":
            _set_pv(e["element"], "activityStatus", previous_status)
            _set_pv(e["element"], "completionGuards", list(previous_guards))
    if report:
        g["elementList"].append({"type": {"typeName": "ReportOriginator"}, "elementAtEnd1": False,
                                 "element": {"elementGUID": report, "type": {"typeName": "SurveyReport"}}})
    return g


def _survey_step_graph(guid: str = SURVEY_STEP, *, status: str = "COMPLETED", guards=("survey-completed",),
                       next_guid: str | None = None, instance: str = INSTANCE) -> dict:
    """The native survey step (M2) as step 1 of the mixed process: no previous action, ControlFlow to `next_guid`."""
    g = copy.deepcopy(_fx("engine_action__survey_step_in_process__related.json"))
    start = g["startingElement"]
    start["elementGUID"] = guid
    _set_pv(start, "processName", _process_name(instance))
    _set_pv(start, "activityStatus", status)
    _set_pv(start, "completionGuards", list(guards))
    _anchor(start, instance)
    out = []
    for e in g["elementList"]:
        if e["type"]["typeName"] == "ControlFlow":
            if e["elementAtEnd1"] or next_guid is None:
                continue                  # step 1 has no previous action; no next unless Egeria created one
            e["element"]["elementGUID"] = next_guid
        if e["type"]["typeName"] == "ReportOriginator" and status != "COMPLETED":
            continue
        out.append(e)
    g["elementList"] = out
    return g


def _instance_graph(instance: str, first: str) -> dict:
    g = copy.deepcopy(_fx("process_instance__create_and_survey__related.json"))
    for e in g["elementList"]:
        if e["type"]["typeName"] == "ActionRequester":
            e["element"]["elementGUID"] = first
    g["startingElement"]["elementGUID"] = instance
    return g


def _step2_action(egeria, guid: str = "ea-s2", *, report: str | None = NATIVE_REPORT, target=DB_GUID, **graph):
    """Step 2's engine action as RE's sweep lists it, its neighbourhood as Egeria answers it, and the report."""
    action = egeria.add_action(guid, target=target, request_type=STEP2_TYPE, report=report,
                               target_name="serverToSurvey")
    egeria.graphs[guid] = _step2_graph(guid, **graph)
    # its previous step: the native survey, whose ReportOriginator report is the one handed on
    egeria.graphs.setdefault(SURVEY_STEP, _survey_step_graph(next_guid=guid))
    egeria.native_reports.setdefault(NATIVE_REPORT, _native_report(written={"src_cd": 250}))
    return action


def _script():
    import importlib.util

    spec = importlib.util.spec_from_file_location("generate_engine_host_declaration", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _blocks(doc: str) -> list[tuple[str, dict[str, str]]]:
    """(command, {heading: value}) for every `## <command>` block of a Dr.Egeria document, in order."""
    out = []
    for block in doc.split("\n## ")[1:]:
        command, _, body = block.partition("\n")
        fields = dict(re.findall(r"^### (.+?)\n(.*?)(?=\n### |\n___|\Z)", body, re.S | re.M))
        out.append((command.strip(), {k.strip(): v.strip() for k, v in fields.items()}))
    return out


def _declared_shape(doc: str) -> dict:
    """What the document declares: the process, its steps in order (type, engine, request type, produced guards),
    and the next-step links (guard, mandatory)."""
    blocks = _blocks(doc)
    steps = {f["Qualified Name"]: f for c, f in blocks if c == "Create Governance Action Process Step"}
    executors = {f["Governance Action Type"]: f for c, f in blocks if c == "Link Action to Action Executor"}
    [first] = [f for c, f in blocks if c == "Link First Process Step"]
    nexts = {f["Governance Action Process Step"]: f for c, f in blocks if c == "Link Next Process Step"}
    order, at = [], first["Governance Action Process Step"]
    while at and at not in order:
        order.append(at)
        at = (nexts.get(at) or {}).get("Next Governance Action Process Step")

    def prop(step, name):
        m = re.search(rf"^\| {re.escape(name)} \| (.+?) \|$", steps[step]["Additional Properties"], re.M)
        return m.group(1) if m else None

    [process] = [f for c, f in blocks if c == "Create Governance Action Process"]
    return {
        "process": process["Qualified Name"], "first_of": first["Governance Action Process"],
        "steps": [(qn, prop(qn, "governance_action_type"), executors[qn]["Governance Engine"],
                   executors[qn]["Request Type"], steps[qn]["Produced Guards"]) for qn in order],
        "links": [(f["Governance Action Process Step"], f["Next Governance Action Process Step"], f["Guard"],
                   f["Mandatory Guard"]) for c, f in blocks if c == "Link Next Process Step"],
    }


def _registry_shape(process) -> dict:
    steps = sorted(process.steps, key=lambda s: s.number)
    return {
        "process": process.qualified_name, "first_of": process.qualified_name,
        "steps": [(s.qualified_name, s.governance_action_type, s.governance_engine, s.request_type,
                   ", ".join(s.produced_guards)) for s in steps],
        "links": [(process.step(lk.from_step).qualified_name, process.step(lk.to_step).qualified_name, lk.guard,
                   "true" if lk.mandatory else "false") for lk in process.links],
    }


# ── test 1: the generated process equals the registry (M1) ─────────────────────

def test_1_the_generated_process_document_equals_the_registry_entry(staleness_installed):
    from resource_explorer.mixed_process import MIXED_PROCESSES

    process = MIXED_PROCESSES["postgres_survey_then_derived"]
    assert process.qualified_name == PROCESS_QN
    doc = PROCESS_DOC.read_text()
    assert doc == _script().render_mixed_process(process), "stale: run the generator with --mixed-process"
    assert _declared_shape(doc) == _registry_shape(process)
    shape = _declared_shape(doc)
    # step 1 is the content pack's survey, step 2 RE's step-3 type; one link, survey-completed, mandatory
    assert [s[1] for s in shape["steps"]] == ["PostgreSQLSurvey::survey-postgres-database",
                                              "ResourceExplorerSurvey::re-database-staleness"]
    assert [s[2:4] for s in shape["steps"]] == [("PostgreSQLSurvey", "survey-postgres-database"),
                                                ("ResourceExplorerSurvey", STEP2_TYPE)]
    assert [lk[2:] for lk in shape["links"]] == [("survey-completed", "true")]


def test_1_changing_the_guard_changes_the_document_and_fails_the_equality(staleness_installed):
    import dataclasses

    from resource_explorer.mixed_process import MIXED_PROCESSES

    process = MIXED_PROCESSES["postgres_survey_then_derived"]
    changed = dataclasses.replace(process, links=(dataclasses.replace(process.links[0], guard="survey-invalid"),))
    rendered = _script().render_mixed_process(changed)
    assert rendered != PROCESS_DOC.read_text()
    assert _declared_shape(PROCESS_DOC.read_text()) != _registry_shape(changed)
    not_mandatory = dataclasses.replace(process, links=(dataclasses.replace(process.links[0], mandatory=False),))
    assert _declared_shape(PROCESS_DOC.read_text()) != _registry_shape(not_mandatory)


def test_1_check_mode_for_the_mixed_process_passes_on_the_committed_tree(tmp_path):
    import os
    import subprocess
    import sys

    from tests.library_step_install import pythonpath

    env = {**os.environ, "PYTHONPATH": pythonpath(tmp_path)}
    out = subprocess.run([sys.executable, str(SCRIPT), "--mixed-process", "--check"], capture_output=True,
                         text=True, timeout=120, env=env)
    assert out.returncode == 0, out.stdout + out.stderr
    assert "processes/postgres-survey-then-derived.md" in out.stdout


def test_1_the_runner_accepts_every_command_and_needs_no_pyegeria_steps(monkeypatch):
    """Dr.Egeria has a command for every element (pyegeria 6.2.3 commands_action_author.json). A dry run: no client,
    no subprocess, no row. Never a bootstrap batch."""
    from resource_explorer import dr_egeria_runner

    monkeypatch.delenv("RE_DR_EGERIA_DOCS_DIR", raising=False)
    out = dr_egeria_runner.run_document("engine-host/processes/postgres-survey-then-derived.md", dry_run=True)
    assert out["valid"] is True, out["errors"]
    assert [(c["kind"], c["command"]) for c in out["commands"]] == [
        ("dr_egeria", "Create Governance Action Process"),
        ("dr_egeria", "Create Governance Action Process Step"), ("dr_egeria", "Create Governance Action Process Step"),
        ("dr_egeria", "Link Action to Action Executor"), ("dr_egeria", "Link Action to Action Executor"),
        ("dr_egeria", "Link First Process Step"), ("dr_egeria", "Link Next Process Step")]
    assert not (PROCESS_DOC.parent / "_batch.json").exists()
    assert not PROCESS_DOC.with_name(PROCESS_DOC.stem + ".pyegeria-steps.json").exists()


def test_1_the_yaml_row_and_the_registry_name_the_same_process():
    from resource_explorer.mixed_process import KIND_MIXED_PROCESS, MIXED_PROCESSES
    from resource_explorer.surveyors.technology_type_processes import get_native_processes

    rows = [p for p in get_native_processes("database", TECH) if p.kind == KIND_MIXED_PROCESS]
    assert [p.qualified_name for p in rows] == [p.qualified_name for p in MIXED_PROCESSES.values()]
    # started with the asset under the name RE's own PostgreSQLSurvey run uses
    survey = next(p for p in get_native_processes("database", TECH)
                  if p.qualified_name == "PostgreSQLSurvey::survey-postgres-database")
    assert rows[0].action_target_name == survey.action_target_name == "serverToSurvey"
    assert rows[0].needs_credentials is True


# ── test 2: targets carry (M3) ─────────────────────────────────────────────────

def test_2_step_2_reads_the_survey_report_target_and_records_derived_from_report(
        staleness_installed, reg, egeria, no_connection, monkeypatch):
    _step2_action(egeria)
    result = _sweep(reg)
    assert result.claimed == ["ea-s2"], result
    row = _row(reg)
    # the asset target maps the slug; the report target is not an asset
    assert (row["target"]["slug"], row["target"]["asset_guid"]) == ("coco_pharma", DB_GUID)
    assert row["target"]["source_report"] == NATIVE_REPORT
    assert not row["target"]["refusal"]
    _run_queue_once(reg)

    # the step read step 1's report: its header from the graph read, its annotations from the PAGED
    # ReportedAnnotation list (the fake's clients bind the real signatures)
    assert any(p[0] == NATIVE_REPORT and p[3] > 0 for p in egeria.related_pages), "report's annotations not read"
    done = egeria.actions["ea-s2"]["completion"]
    assert done["status"] == "ACTIONED" and done["guards"] == ["survey-completed"], done
    [(report_guid, body)] = egeria.reports()
    assert body["properties"]["additionalProperties"]["derived_from_report"] == NATIVE_REPORT
    assert done["targets"] == [{"actionTargetName": "surveyReport", "actionTargetGUID": report_guid}]
    outcome = _row(reg)["target"]["outcome"]
    assert outcome["derived_from_report"] == NATIVE_REPORT and outcome["report_guid"] == report_guid

    # two measures, kept distinct: staleness from the stored snapshots (coco_pharma has one: "not measured · one
    # snapshot only", no substitute figure), and the report's writes since statistics reset, its own annotations
    stale, writes = {}, {}
    for r in egeria.relationships:
        if r["end1"] == report_guid and egeria.elements.get(r["end2"], {}).get("type") == "Annotation":
            p = egeria.elements[r["end2"]]["body"]["properties"]
            {"Database Staleness": stale, "Writes Since Statistics Reset": writes}[p["annotationType"]][
                p["additionalProperties"]["scope"]] = p
    assert set(stale) == {"public.customer", "public.orders"}
    for p in stale.values():
        assert p["additionalProperties"]["resultState"] == "not_established"
        assert p["explanation"].startswith("not measured · one snapshot only")
        assert not p.get("resourceProperties")       # no substitute figure
    assert len(writes) == 10
    assert writes["public.src_cd"]["additionalProperties"]["resultState"] == "measured"
    assert writes["public.src_cd"]["resourceProperties"]["writesSinceStatisticsReset"] == "250"
    assert writes["public.src_cd"]["resourceProperties"]["sourceReport"] == NATIVE_REPORT
    assert writes["public.weight"]["additionalProperties"]["resultState"] == "nothing_found"
    assert outcome["annotation_count"] == 12


# ── test 3: missing report target (M3) ─────────────────────────────────────────

def test_3_without_a_survey_report_target_step_2_runs_standalone_and_completes(
        staleness_installed, reg, egeria, no_connection):
    """Ruling 2026-10-15: no surveyReport target is a standalone run, not an invalid one. One snapshot: both
    measures say why they are not measured, and the run completes survey-completed."""
    _step2_action(egeria, "ea-alone", report=None)
    assert _sweep(reg).claimed == ["ea-alone"]
    _run_queue_once(reg)
    done = egeria.actions["ea-alone"]["completion"]
    assert done["status"] == "ACTIONED" and done["guards"] == ["survey-completed"], done
    [(report_guid, body)] = egeria.reports()
    # no report to read, none read: the only read is the read-back of the report this run made
    assert {c.args[0] for c in egeria.of("AssetMaker").get_asset_by_guid.call_args_list} <= {report_guid}
    assert "derived_from_report" not in body["properties"].get("additionalProperties", {})
    texts = {}
    for r in egeria.relationships:
        if r["end1"] == report_guid and egeria.elements.get(r["end2"], {}).get("type") == "Annotation":
            p = egeria.elements[r["end2"]]["body"]["properties"]
            texts[(p["annotationType"], p["additionalProperties"]["scope"])] = p
    assert texts[("Database Staleness", "public.orders")]["summary"] == "not measured · one snapshot only"
    assert texts[("Writes Since Statistics Reset", "coco_pharma")]["summary"] == \
        "writes since statistics reset: not measured · no survey report in this run"
    outcome = _row(reg)["target"]["outcome"]
    assert outcome["guard"] == "survey-completed" and outcome["report_guid"] == report_guid


def test_3_an_unreadable_report_target_fails_in_its_words(staleness_installed, reg, egeria, no_connection):
    _step2_action(egeria, "ea-bad", report="0a1b2c3d-0000-4000-8000-0000000000ee")
    egeria.graphs["ea-bad"] = "No elements found"          # started alone: no previous step to match
    _sweep(reg)
    _run_queue_once(reg)
    done = egeria.actions["ea-bad"]["completion"]
    assert done["guards"] == ["survey-failed"], done
    assert "surveyReport action target 0a1b2c3d-0000-4000-8000-0000000000ee could not be read" in done["message"]
    assert egeria.reports() == []


# review round 1 (HIGH): the report must be this database's, the previous step's, and only one

USDA_ASSET = "304152cf-fa47-4496-9a14-27a7f5b35df2"
OTHER_REPORT = "0a1b2c3d-0000-4000-8000-00000000abcd"


def _invalid(egeria, reg, guid):
    _sweep(reg)
    _run_queue_once(reg)
    done = egeria.actions[guid]["completion"]
    assert done["status"] == "INVALID" and done["guards"] == ["survey-invalid"], done
    assert egeria.reports() == [] and not egeria.method_calls("create_annotation")
    return done["message"]


def test_review_a_report_of_another_database_is_refused(staleness_installed, reg, egeria, no_connection):
    """Started alone: serverToSurvey = coco_pharma, surveyReport = a laz_local_usda report. Never usda's counters as
    coco_pharma's."""
    egeria.native_reports[NATIVE_REPORT] = _native_report(subject=USDA_ASSET)
    _step2_action(egeria, "ea-other-db")
    egeria.graphs["ea-other-db"] = "No elements found"
    msg = _invalid(egeria, reg, "ea-other-db")
    assert f"is a survey report of {USDA_ASSET}, not of this action's database {DB_GUID}" in msg


def test_review_two_report_targets_are_refused(staleness_installed, reg, egeria, no_connection):
    action = _step2_action(egeria, "ea-two")
    action["actionTargetElements"].append({"actionTargetName": "surveyReport", "actionTargetGUID": OTHER_REPORT})
    egeria.graphs["ea-two"] = "No elements found"
    assert _sweep(reg).claimed == ["ea-two"]
    _run_queue_once(reg)
    done = egeria.actions["ea-two"]["completion"]
    assert done["guards"] == ["survey-invalid"] and "2 surveyReport action targets" in done["message"], done
    assert "not choosing one" in done["message"] and egeria.reports() == []
    assert not egeria.method_calls("get_asset_by_guid")


def test_review_a_report_with_no_stated_type_fails_closed(staleness_installed, reg, egeria, no_connection):
    report = _native_report()
    del report["elementHeader"]["type"]
    egeria.native_reports[NATIVE_REPORT] = report
    _step2_action(egeria, "ea-notype")
    msg = _invalid(egeria, reg, "ea-notype")
    assert "an element of no stated type, not a SurveyReport" in msg


def test_review_a_report_that_is_not_the_previous_steps_is_refused(staleness_installed, reg, egeria, no_connection):
    egeria.native_reports[OTHER_REPORT] = _native_report(guid=OTHER_REPORT)     # coco_pharma's, but an older one
    _step2_action(egeria, "ea-stale", report=OTHER_REPORT)
    msg = _invalid(egeria, reg, "ea-stale")
    assert f"surveyReport {OTHER_REPORT} is not the report of the previous process step ({NATIVE_REPORT})" in msg


# ── test 4: step 2 is claimed only as step 2 ───────────────────────────────────

@pytest.mark.parametrize("status,guards", [("FAILED", ("survey-failed",)), ("IN_PROGRESS", ()),
                                           ("COMPLETED", ("survey-invalid",))])
def test_4_step_2_whose_previous_step_did_not_complete_with_the_guard_is_not_claimed(
        staleness_installed, reg, egeria, caplog, status, guards):
    from resource_explorer import engine_host

    _step2_action(egeria, "ea-early", previous_status=status, previous_guards=guards)
    host_log = logging.getLogger("resource_explorer.engine_host")
    with caplog.at_level(logging.INFO, logger=host_log.name):
        first = _sweep(reg)
        second = _sweep(reg)
    for result in (first, second):
        assert result.claimed == []
        assert result.skipped.get(engine_host.PREVIOUS_STEP_NOT_COMPLETED) == ["ea-early"], result.skipped
    assert not egeria.method_calls("claim_engine_action")
    assert reg.list_runs(kind="engine_action") == []
    # recorded once, not every 5 seconds (a fresh host per sweep here, so: once per host)
    said = [r for r in caplog.records if "ea-early" in r.getMessage()]
    assert len(said) == 2 and all(engine_host.PREVIOUS_STEP_NOT_COMPLETED in r.getMessage() for r in said)


def test_4_recorded_once_per_running_host(staleness_installed, reg, egeria, caplog):
    from resource_explorer import engine_host
    from tests.test_engine_host_e4 import _host

    _step2_action(egeria, "ea-early2", previous_status="FAILED", previous_guards=("survey-failed",))
    host = _host(reg)
    try:
        with caplog.at_level(logging.INFO, logger="resource_explorer.engine_host"):
            host.sweep_once()
            host.sweep_once()
    finally:
        host.close()
    said = [r for r in caplog.records if "ea-early2" in r.getMessage()]
    assert len(said) == 1 and engine_host.PREVIOUS_STEP_NOT_COMPLETED in said[0].getMessage()


def test_4_an_unreadable_neighbourhood_claims_nothing(staleness_installed, reg, egeria):
    _step2_action(egeria, "ea-unread")
    egeria.graphs["ea-unread"] = {"unexpected": "shape"}
    result = _sweep(reg)
    assert result.claimed == [] and result.skipped.get("screen_failed") == ["ea-unread"]


def test_4_a_lone_action_with_no_relationship_at_all_is_still_claimed(staleness_installed, reg, egeria):
    _step2_action(egeria, "ea-lone")
    egeria.graphs["ea-lone"] = "No elements found"
    assert _sweep(reg).claimed == ["ea-lone"]


# ── test 5: the grouping reader (M4) ───────────────────────────────────────────

def _view(graph):
    from resource_explorer.mixed_process import parse_action_graph

    return parse_action_graph(graph)


def test_5_a_native_and_an_re_action_of_one_instance_render_one_run_line_with_two_reports():
    from resource_explorer.mixed_process import group_by_process, run_line_dict

    native = _view(_survey_step_graph(next_guid="ea-s2"))
    mine = _view(_step2_graph("ea-s2", status="COMPLETED", guards=("survey-completed",),
                              report="7c1d0f9e-0000-4000-8000-00000000a002"))
    [line] = group_by_process([mine, native])
    assert [s.guid for s in line.steps] == [SURVEY_STEP, "ea-s2"]          # run order, not input order
    out = run_line_dict(line, started="10-15")
    assert out["words"] == ("Postgres survey then derived · step 1 PostgreSQLSurvey ✓ report cb92eedc · "
                            "step 2 re-database-staleness ✓ report 7c1d0f9e · started by peterprofile 10-15")
    assert [s["report_guid"] for s in out["steps"]] == [NATIVE_REPORT, "7c1d0f9e-0000-4000-8000-00000000a002"]


def test_5_a_lone_action_renders_alone_and_an_unreadable_one_says_process_unknown():
    from unittest.mock import create_autospec

    from pyegeria import MetadataExpert

    from resource_explorer.mixed_process import group_by_process, read_action_views, run_line_words

    lone = {"startingElement": {"elementGUID": "ea-lone", "classifications": [], "elementProperties": {
        "propertyValueMap": {"activityStatus": {"symbolicName": "COMPLETED"},
                             "requestType": {"primitiveValue": "survey-postgres-database"}}}}, "elementList": []}
    expert = create_autospec(MetadataExpert, spec_set=True, instance=True)
    answers = {"ea-lone": lone, "ea-gone": RuntimeError("read timeout")}

    def related(guid=None, *a, **k):
        if isinstance(answers[guid], Exception):
            raise answers[guid]
        return answers[guid]

    expert.get_all_related_elements.side_effect = related
    lines = group_by_process(read_action_views(expert, ["ea-lone", "ea-gone"]))
    assert [(ln.instance_guid, [s.guid for s in ln.steps], ln.note) for ln in lines] == [
        ("", ["ea-lone"], ""), ("", ["ea-gone"], "process unknown")]
    assert "process unknown" not in run_line_words(lines[0])
    assert run_line_words(lines[1]).endswith("process unknown")


def test_5_the_live_native_process_run_reads_in_run_order_from_its_instance():
    """The M2 capture of a native three-step process, read from its instance (ActionRequester, then ControlFlow)."""
    from unittest.mock import create_autospec

    from pyegeria import MetadataExpert

    from resource_explorer.mixed_process import read_process_run

    by_guid = {INSTANCE: "process_instance__create_and_survey__related.json",
               "eda20b32-582d-41de-902e-d68c6402770c": "engine_action__first_step_in_process__related.json",
               SURVEY_STEP: "engine_action__survey_step_in_process__related.json",
               "a07a11e2-b6aa-45ec-9dcb-687d69a8ee1c": "engine_action__next_step_after_survey__related.json"}
    expert = create_autospec(MetadataExpert, spec_set=True, instance=True)
    expert.get_all_related_elements.side_effect = lambda guid=None, *a, **k: _fx(by_guid[guid])
    views = read_process_run(expert, INSTANCE)
    assert [v.request_type for v in views] == ["create-postgres-database", "survey-postgres-database",
                                               "print-survey-report"]
    assert {v.instance_guid for v in views} == {INSTANCE}
    assert views[1].report_guid == NATIVE_REPORT
    # the step after the survey carries surveyReport and the original asset target, under its started name
    assert views[2].targets == [("surveyReport", NATIVE_REPORT), ("newAsset", "304152cf-fa47-4496-9a14-27a7f5b35df2")]
    assert [(p.guard, p.status, p.completion_guards) for p in views[2].previous] == [
        ("survey-completed", "COMPLETED", ["survey-completed"])]


# ── M5: RE's Run starts the process; tests 6 and the one run line in Understanding ──

def _ready(egeria, monkeypatch):
    egeria.declared.add(PROCESS_QN)
    egeria.assets.add(DB_GUID)


def _rows(reg, **kw):
    from resource_explorer.native_survey_run import native_survey_rows

    return {r["qualified_name"]: r for r in native_survey_rows(reg, "database", "coco_pharma", TECH, **kw)}


def _run(reg):
    from resource_explorer import catalog_and_survey as cas
    from resource_explorer.egeria_clients import Daemon, DaemonReason
    from resource_explorer.native_survey_run import port_session, submit_native_survey

    with port_session(cas.PyegeriaRegistrationPort(identity=Daemon(DaemonReason.SCHEDULER))) as port:
        return submit_native_survey(reg, port, "database", "coco_pharma", PROCESS_QN, technology_type=TECH,
                                    submitted_by=REQUESTER)


def _refresh(reg):
    from resource_explorer import catalog_and_survey as cas
    from resource_explorer.egeria_clients import Daemon, DaemonReason
    from resource_explorer.native_survey_run import port_session, refresh_resource

    with port_session(cas.PyegeriaRegistrationPort(identity=Daemon(DaemonReason.SCHEDULER))) as port:
        return refresh_resource(reg, port, "database", "coco_pharma")


def test_m5_run_starts_the_process_with_the_database_asset_as_its_target(staleness_installed, reg, egeria,
                                                                          monkeypatch):
    _ready(egeria, monkeypatch)
    row = _rows(reg)[PROCESS_QN]
    assert row["runnable"] is True and row["kind"] == "mixed_process", row
    _run(reg)
    assert egeria.process_starts == [{"process": PROCESS_QN, "request_parameters": None, "action_targets": [
        {"class": "NewActionTarget", "actionTargetName": "serverToSurvey", "actionTargetGUID": DB_GUID}]}]
    assert not egeria.method_calls("initiate_gov_action_type")
    [run] = reg.list_native_survey_runs("database", "coco_pharma", PROCESS_QN)
    assert run["engine_action_guid"] == "pi-1" and json.loads(run["declared"])["started_as"] == "process"
    assert run["submitted_by"] == REQUESTER
    assert _rows(reg)[PROCESS_QN]["cancellable"] is False      # a process instance is never an engine action


def test_m5_not_runnable_with_the_engine_host_off(staleness_installed, reg, egeria, monkeypatch):
    from resource_explorer.native_survey_run import ENGINE_HOST_OFF, NativeSurveyCannotRun

    _ready(egeria, monkeypatch)
    monkeypatch.delenv("RE_ENGINE_HOST_ENABLED", raising=False)
    row = _rows(reg)[PROCESS_QN]
    assert row["runnable"] is False and row["cannot_run_reason"] == ENGINE_HOST_OFF
    with pytest.raises(NativeSurveyCannotRun, match=ENGINE_HOST_OFF):
        _run(reg)
    assert egeria.process_starts == []


def test_m5_not_runnable_when_the_process_is_not_declared_in_egeria(staleness_installed, reg, egeria, monkeypatch):
    from resource_explorer.native_survey_run import PROCESS_NOT_DECLARED, NativeSurveyCannotRun

    egeria.assets.add(DB_GUID)                      # the database is there; the process is not
    with pytest.raises(NativeSurveyCannotRun, match="process not declared"):
        _run(reg)
    assert egeria.process_starts == []
    assert reg.list_native_survey_runs("database", "coco_pharma", PROCESS_QN) == []
    # once a read has said so, the row says so too; the registry-only listing never claims it
    assert _rows(reg)[PROCESS_QN]["runnable"] is True
    row = _rows(reg, declared={PROCESS_QN: False})[PROCESS_QN]
    assert row["runnable"] is False and row["cannot_run_reason"] == PROCESS_NOT_DECLARED


def test_m5_not_runnable_without_step_2_installed_where_the_engine_host_runs(reg, egeria, monkeypatch):
    _ready(egeria, monkeypatch)
    # step 2 (an Analytic Library step) is not installed here
    assert "not installed" in _rows(reg)[PROCESS_QN]["cannot_run_reason"]


def test_m5_needs_credentials_as_postgresqlsurvey_does(staleness_installed, reg, egeria, monkeypatch, tmp_path):
    import resource_explorer.config as config_module
    from resource_explorer.native_survey_run import NO_CREDENTIALS

    _ready(egeria, monkeypatch)
    (tmp_path / "om.omsecrets").write_text("{}")              # the secrets file has no collection for coco_pharma
    monkeypatch.setenv("EGERIA_SECRETS_STORE_LOCAL_PATH", str(tmp_path / "om.omsecrets"))
    config_module._config = None
    row = _rows(reg)[PROCESS_QN]
    assert row["runnable"] is False and row["cannot_run_reason"] == NO_CREDENTIALS
    assert row["cannot_run_reason"] == _rows(reg)["PostgreSQLSurvey::survey-postgres-database"]["cannot_run_reason"]


def test_6_failure_stops_the_chain_and_understanding_says_so(staleness_installed, reg, egeria, monkeypatch):
    from resource_explorer.native_survey_run import FAILED, RUNNING

    _ready(egeria, monkeypatch)
    _run(reg)
    egeria.graphs["pi-1"] = _instance_graph("pi-1", "s1")
    egeria.graphs["s1"] = _survey_step_graph("s1", status="FAILED", guards=("survey-failed",), instance="pi-1")
    assert _refresh(reg) == 1
    run = _rows(reg)[PROCESS_QN]["run"]
    assert run["state"] == FAILED and run["state"] != RUNNING, run
    assert "step 1 PostgreSQLSurvey ✗ survey-failed · step 2 not started · step 1 failed" in run["line"], run["line"]
    assert "pending" not in run["line"] and "waits" not in run["line"]
    assert _rows(reg)[PROCESS_QN]["in_flight"] is False
    assert _refresh(reg) == 0                                   # nothing left to read


def test_6_a_completed_two_step_run_is_one_line_with_both_reports(staleness_installed, reg, egeria, monkeypatch):
    from resource_explorer.native_survey_run import COMPLETE, RUNNING

    _ready(egeria, monkeypatch)
    _run(reg)
    egeria.graphs["pi-1"] = _instance_graph("pi-1", "s1")
    egeria.graphs["s1"] = _survey_step_graph("s1", next_guid="s2", instance="pi-1")
    egeria.graphs["s2"] = _step2_graph("s2", status="IN_PROGRESS", instance="pi-1")
    _refresh(reg)
    row = _rows(reg)[PROCESS_QN]
    assert row["run"]["state"] == RUNNING and row["in_flight"] is True
    assert "step 2 re-database-staleness running" in row["run"]["line"]
    egeria.graphs["s2"] = _step2_graph("s2", status="COMPLETED", guards=("survey-completed",), instance="pi-1",
                                       report="7c1d0f9e-0000-4000-8000-00000000a002")
    _refresh(reg)
    run = _rows(reg)[PROCESS_QN]["run"]
    assert run["state"] == COMPLETE, run
    assert run["line"].startswith("Postgres survey then derived · step 1 PostgreSQLSurvey ✓ report cb92eedc · "
                                  "step 2 re-database-staleness ✓ report 7c1d0f9e · started by peterprofile")
    assert [s["report_guid"] for s in run["steps"]] == [NATIVE_REPORT, "7c1d0f9e-0000-4000-8000-00000000a002"]


def test_6_an_unreadable_process_read_is_recorded_never_a_status(staleness_installed, reg, egeria, monkeypatch):
    from resource_explorer.native_survey_run import SUBMITTED, UNREADABLE

    _ready(egeria, monkeypatch)
    _run(reg)
    assert _rows(reg)[PROCESS_QN]["run"]["state"] in (SUBMITTED, UNREADABLE)
    egeria.fail["get_all_related_elements"] = RuntimeError("read timeout")
    _refresh(reg)
    run = _rows(reg)[PROCESS_QN]["run"]
    assert run["state"] == UNREADABLE and "read timeout" in run["error"], run


# review round 1: per-step words scrubbed, PostgreSQL only, an unclaimed RE step says why

def test_review_each_steps_message_is_scrubbed_like_the_line(staleness_installed, reg, egeria, monkeypatch):
    from resource_explorer import native_survey_run as nsr
    from resource_explorer.registry import DatabaseEntity

    _ready(egeria, monkeypatch)
    _run(reg)
    secret = "hunter2-Pw-77"
    real = nsr._resource_entity

    def with_password(registry, entity_type, slug):
        e = real(registry, entity_type, slug)
        return DatabaseEntity(**{**e.__dict__, "db_password": secret}) if e is not None else e

    monkeypatch.setattr(nsr, "_resource_entity", with_password)
    egeria.graphs["pi-1"] = _instance_graph("pi-1", "s1")
    egeria.graphs["s1"] = _survey_step_graph("s1", status="FAILED", guards=("survey-failed",), instance="pi-1")
    _set_pv(egeria.graphs["s1"]["startingElement"], "completionMessage",
            f'FATAL: password authentication failed for "{secret}"')
    _refresh(reg)
    run = reg.list_native_survey_runs("database", "coco_pharma", PROCESS_QN)[0]
    assert secret not in json.dumps(run, default=str)
    steps = _rows(reg)[PROCESS_QN]["run"]["steps"]
    assert "password authentication failed" in steps[0]["message"] and secret not in steps[0]["message"]


def test_review_postgresql_surveys_are_offered_for_postgresql_only(staleness_installed, reg, egeria, monkeypatch):
    from resource_explorer.native_survey_run import NOT_POSTGRES, NativeSurveyCannotRun, native_survey_rows
    from resource_explorer.registry import DatabaseEntity

    _ready(egeria, monkeypatch)
    reg.register_database(DatabaseEntity(slug="shop_mysql", display_name="shop", db_type="mysql", host="localhost",
                                         port=3306, database_name="shop", egeria_asset_guid=DB_GUID))
    names = {r["qualified_name"] for r in native_survey_rows(reg, "database", "shop_mysql", TECH)}
    assert PROCESS_QN not in names and "PostgreSQLSurvey::survey-postgres-database" not in names
    assert PROCESS_QN in _rows(reg)            # coco_pharma is PostgreSQL: still offered
    from resource_explorer import catalog_and_survey as cas
    from resource_explorer.egeria_clients import Daemon, DaemonReason
    from resource_explorer.native_survey_run import port_session, submit_native_survey

    with port_session(cas.PyegeriaRegistrationPort(identity=Daemon(DaemonReason.SCHEDULER))) as port:
        with pytest.raises(NativeSurveyCannotRun):
            submit_native_survey(reg, port, "database", "shop_mysql", PROCESS_QN, technology_type=TECH)
    assert egeria.process_starts == [] and NOT_POSTGRES


def test_review_an_unclaimed_re_step_says_it_waits_for_the_engine_host_and_why(staleness_installed, reg, egeria,
                                                                              monkeypatch):
    from resource_explorer.native_survey_run import RUNNING, WAITING_FOR_ENGINE_HOST

    _ready(egeria, monkeypatch)
    _run(reg)
    egeria.graphs["pi-1"] = _instance_graph("pi-1", "s1")
    egeria.graphs["s1"] = _survey_step_graph("s1", next_guid="s2", instance="pi-1")
    egeria.graphs["s2"] = _step2_graph("s2", status="APPROVED", instance="pi-1")
    _refresh(reg)
    monkeypatch.delenv("RE_ENGINE_HOST_ENABLED", raising=False)
    run = _rows(reg)[PROCESS_QN]["run"]
    assert run["state"] == RUNNING
    assert "step 2 re-database-staleness not claimed yet" in run["line"]
    assert run["waiting_for"].startswith(f"{WAITING_FOR_ENGINE_HOST} · engine host off")
    monkeypatch.setenv("RE_ENGINE_HOST_ENABLED", "true")
    assert _rows(reg)[PROCESS_QN]["run"]["waiting_for"] == f"{WAITING_FOR_ENGINE_HOST} · not claimed yet"


# ── test 7: same identity ─────────────────────────────────────────────────────

def test_7_step_2_runs_as_the_engine_host_daemon_for_the_requester(staleness_installed, reg, egeria, no_connection):
    _step2_action(egeria, "ea-who")
    _sweep(reg)
    _run_queue_once(reg)
    daemon = _daemon_user()
    sweep_reads = {"get_metadata_guid_by_unique_name", "get_approved_engine_actions"}
    mine = [c for c in egeria.calls if c[0] != "ActorManager" and c[1] not in sweep_reads]
    methods = {m for _, m, _ in mine}
    assert {"get_all_related_elements", "claim_engine_action", "get_asset_by_guid", "create_asset",
            "record_completion_status"} <= methods, sorted(methods)
    for cls, method, who in mine:
        assert (who.kind, who.reason, who.requested_by, who.client_user) == \
            ("daemon", "engine_host", REQUESTER, daemon), f"{cls}.{method} ran as {who}"
    report_guid = egeria.reports()[0][0]
    owners = [b for kind, g, b in egeria.classifications if kind == "Ownership" and g == report_guid]
    assert owners and owners[0]["properties"]["owner"] == REQUESTER, owners


# ── test 8: off means off; a private target is not claimed ─────────────────────

def test_8_off_means_off_for_the_process_created_action(staleness_installed, reg, egeria, monkeypatch):
    monkeypatch.delenv("RE_ENGINE_HOST_ENABLED", raising=False)
    _step2_action(egeria, "ea-off")
    result = _sweep(reg)
    assert result.claimed == [] and egeria.built == [] and egeria.calls == []


def test_8_a_process_created_action_on_a_private_target_is_not_claimed(staleness_installed, reg, egeria):
    from resource_explorer.a2a_auth import CallerIdentity, current_caller

    reset = current_caller.set(CallerIdentity(user_id="alice", egeria_token=None, auth_source="app-jwt"))
    try:
        inv = reg.create_investigation("Alice Private", project_classification="PersonalProject")
        ws = reg.get_or_create_working_set(inv["slug"])
        reg.add_working_set_member(ws["slug"], "database", "alice_db")
    finally:
        current_caller.reset(reset)
    _step2_action(egeria, "ea-priv", target=PRIVATE_DB_GUID)
    result = _sweep(reg)
    assert result.claimed == [] and "ea-priv" in result.skipped.get("private_target", [])
    assert not egeria.method_calls("claim_engine_action")
