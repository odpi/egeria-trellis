"""Engine host, step 3: Analytic Library steps that Egeria dispatches through RE's engine host.

Design: docs/design-notes/DESIGN-RE-ENGINE-HOST-BY-EQUIVALENCE.md §4.3 and §5.4 ("user steps from trellis-analytics,
which follow once SurveyStepHost exists"); Mandy's answer to Q-M2 (a SurveyActionService per service, a
GovernanceActionExecutor, RE dispatches on requestType).

A library step's manifest declares its request type, its target kind, its annotation type and the guard that
applies when there is nothing to derive. RE's step host (`survey_step_host.library_steps`) maps request type to
step from the installed manifests, with no list in RE's code; the engine host claims those request types, runs the
step through the same synthetic-step executor as `db_derived` (so its annotations still land in `step_annotations`),
publishes them on the SurveyReport through E4's report path and completes only from the stored proof.

Only the pyegeria boundary is faked, with the autospec'd `FakeEgeria` of test_engine_host_e4 (real signatures).
"""
from __future__ import annotations

import re

import pytest

from tests.library_step_install import staleness_installed  # noqa: F401 - the fixture
from tests.test_engine_host_e4 import (  # noqa: F401 - fixtures and helpers reused
    DB_GUID,
    EMPTY_DB_GUID,
    REQUESTER,
    _daemon_user,
    _row,
    _run_queue_once,
    _sweep,
    egeria,
    no_connection,
    reg,
)

REQUEST_TYPE = "re-database-staleness"
LATER = "2026-10-15T09:00:00"

#: Engine host step 4: database_staleness declares the `source_report` input, so under the engine host it runs only
#: as a later process step, handed the previous step's report as the `surveyReport` action target. These step-3 tests
#: give it one: the native PostgreSQLSurvey report captured live (tests/fixtures/egeria_6_2, M2), with one table's
#: counters changed. Staleness is still step 3's, from the snapshots; the report adds the separate "writes since
#: statistics reset" measure. Without the target it completes survey-invalid (test_engine_host_step4_mixed_process.py).
SOURCE_REPORT = "cb92eedc-5e24-4da7-ba5e-fc11d1707d8f"
EMPTY_REPORT = "0a1b2c3d-0000-4000-8000-00000000e0e0"


def _lib_action(egeria, guid, *, target=DB_GUID, report=SOURCE_REPORT):
    from tests.test_engine_host_step4_mixed_process import _native_report

    egeria.native_reports.setdefault(SOURCE_REPORT, _native_report(written={"src_cd": 250}))
    if report == EMPTY_REPORT:
        egeria.native_reports.setdefault(EMPTY_REPORT, _native_report(tables=False, subject=target,
                                                                      guid=EMPTY_REPORT))
    return egeria.add_action(guid, target=target, request_type=REQUEST_TYPE, report=report)


def _second_survey(reg, slug="coco_pharma"):
    """A second stored survey of coco_pharma with one table changed, so the step has something to derive."""
    from tests.test_engine_host_e4 import _table

    reg.record_database_survey(slug=slug, schema_count=1, table_count=2, column_count=3, survey_data={},
                               source="local", surveyed_at=LATER)
    reg.write_detail_rows("database_tables", slug, LATER, source="local",
                          rows=[_table("customer", 5000), _table("orders", 40250)])


def _annotation_bodies(egeria, report_guid):
    out = []
    for r in egeria.relationships:
        if r["end1"] == report_guid and egeria.elements.get(r["end2"], {}).get("type") == "Annotation":
            out.append(egeria.elements[r["end2"]]["body"]["properties"])
    return out


# ── the seam: request type -> step, from the manifests ─────────────────────────

def test_the_step_host_maps_request_types_from_installed_manifests(staleness_installed):
    from resource_explorer import engine_host
    from resource_explorer.surveyors import survey_step_host

    steps = survey_step_host.library_steps()
    assert list(steps) == [REQUEST_TYPE]
    step = steps[REQUEST_TYPE]
    assert (step.step_key, step.entity_type, step.annotation_type, step.nothing_to_derive_guard) == (
        "database_staleness", "database", "ResourceMeasureAnnotation", "survey-completed")
    assert step.provided_by["package"] == "trellis-step-database-staleness"
    # the engine host claims exactly the built-in request types (every registry step since case 3) and the library's
    builtin = {engine_host.request_type_for_step(k): k for k in engine_host.registry_steps()}
    assert engine_host.supported_request_types() == {**builtin, REQUEST_TYPE: "database_staleness"}


def test_without_the_package_installed_the_library_request_type_is_not_supported():
    from resource_explorer import engine_host
    from resource_explorer.surveyors import survey_step_host

    assert survey_step_host.library_steps() == {}
    assert REQUEST_TYPE not in engine_host.supported_request_types()
    assert set(engine_host.supported_request_types()) == {engine_host.request_type_for_step(k)
                                                          for k in engine_host.registry_steps()}


def test_a_step_that_needs_a_connection_is_not_admitted(staleness_installed, monkeypatch):
    """The engine host opens no connection to a resource (no credential question in this slice, §5.4), so a step
    declaring the connection input is not offered to Egeria at all, and says why."""
    from trellis_analytics.manifest import StepManifest

    from resource_explorer.surveyors import survey_step_host

    needs = StepManifest(name="needs_conn", function="x.y", description="d", source="resource", version="1",
                         resource_types=["database"], inputs=["connection"], credential_tier="read_only")
    real = survey_step_host.discover_steps
    monkeypatch.setattr(survey_step_host, "discover_steps",
                        lambda entity_type: {**real(entity_type), "needs_conn": needs})
    survey_step_host.refresh("database")
    assert set(survey_step_host.library_steps()) == {REQUEST_TYPE}
    refused = survey_step_host.library_step_refusals()
    assert "needs_conn" in refused and "connection" in refused["needs_conn"]


# ── end to end: claimed, run through the step host, published, completed ───────

def test_a_library_action_is_claimed_run_published_and_completed(staleness_installed, reg, egeria, no_connection):
    _second_survey(reg)
    _lib_action(egeria, "ea-lib")
    assert _sweep(reg).claimed == ["ea-lib"]
    assert _run_queue_once(reg) is not None

    action = egeria.actions["ea-lib"]
    assert action["statusUpdates"] == ["IN_PROGRESS"]
    done = action["completion"]
    assert done["status"] == "ACTIONED" and done["guards"] == ["survey-completed"], done

    [(report_guid, body)] = egeria.reports()
    props = body["properties"]
    assert re.fullmatch(rf"SurveyReport::{REQUEST_TYPE}::{DB_GUID}::\S+", props["qualifiedName"]), props
    assert props["purpose"] == REQUEST_TYPE and props["authors"] == [_daemon_user()]
    assert body["parentGUID"] == DB_GUID and body["parentRelationshipTypeName"] == "ReportSubject"
    assert {"type": "ReportOriginator", "end1": "ea-lib", "end2": report_guid} in [
        {k: r[k] for k in ("type", "end1", "end2")} for r in egeria.relationships]
    assert done["targets"] == [{"actionTargetName": "surveyReport", "actionTargetGUID": report_guid}]

    # the step's annotations, as Egeria's ResourceMeasureAnnotation with the envelope in additionalProperties:
    # staleness exactly as in step 3 (from the snapshots), and, separately, the report's writes since reset
    bodies = _annotation_bodies(egeria, report_guid)
    anns = {a["additionalProperties"]["scope"]: a for a in bodies if a["annotationType"] == "Database Staleness"}
    writes = {a["additionalProperties"]["scope"]: a for a in bodies
              if a["annotationType"] == "Writes Since Statistics Reset"}
    assert set(anns) == {"public.customer", "public.orders"} and len(writes) == 10 and len(bodies) == 12
    orders = anns["public.orders"]
    assert orders["class"] == "ResourceMeasureAnnotationProperties"
    assert orders["analysisStep"] == "database_staleness"
    assert orders["resourceProperties"]["rowsChanged"] == "250"
    assert "writesSinceStatisticsReset" not in orders["resourceProperties"]
    assert orders["additionalProperties"]["resultState"] == "measured"
    assert anns["public.customer"]["additionalProperties"]["resultState"] == "nothing_found"
    src_cd = writes["public.src_cd"]
    assert src_cd["analysisStep"] == "database_staleness" and src_cd["class"] == "ResourceMeasureAnnotationProperties"
    assert src_cd["resourceProperties"]["writesSinceStatisticsReset"] == "250"
    assert src_cd["additionalProperties"]["measure"] == "writes_since_statistics_reset"
    assert "rowsChanged" not in src_cd["resourceProperties"]
    assert len({a["qualifiedName"] for a in bodies}) == 12
    assert props["additionalProperties"]["derived_from_report"] == SOURCE_REPORT

    # stored where RE reads them today; a reader of the step never sees the other measure's rows
    run, rows = reg.query_step_annotations("database", "coco_pharma", "database_staleness")
    assert run and {r["scope"] for r in rows} == {"public.customer", "public.orders"}
    _run2, wrows = reg.query_step_annotations("database", "coco_pharma",
                                              "database_staleness:writes_since_statistics_reset")
    assert {r["scope"] for r in wrows} == set(writes)

    row = _row(reg)
    assert row["state"] == "succeeded"
    outcome = row["target"]["outcome"]
    assert (outcome["guard"], outcome["report_guid"], outcome["annotation_count"]) == (
        "survey-completed", report_guid, 12)
    assert egeria.annotations_under(report_guid) == 12
    assert row["target"]["completion_recorded_at"]

    # identity unchanged: every write is the engine host daemon for the requester
    daemon = _daemon_user()
    for cls, method, who in egeria.calls:
        if method in ("get_metadata_guid_by_unique_name", "get_active_engine_actions") or cls == "ActorManager":
            continue
        assert (who.kind, who.reason, who.requested_by, who.client_user) == \
            ("daemon", "engine_host", REQUESTER, daemon), f"{cls}.{method} ran as {who}"


def _states(egeria, report_guid):
    """{(annotationType, scope): (resultState, summary-or-explanation words)} of one published report."""
    return {(a["annotationType"], a["additionalProperties"]["scope"]):
            (a["additionalProperties"]["resultState"], a.get("summary") or "", a.get("explanation") or "")
            for a in _annotation_bodies(egeria, report_guid)}


def test_report_with_no_table_measurements_and_no_snapshots_completes_with_both_absences(
        staleness_installed, reg, egeria, no_connection):
    """Live bug (laz_local_egeria_observations): a valid report and no stored snapshots was completed survey-invalid
    "nothing to derive". Ruling 2026-10-15: an absence honestly recorded completes survey-completed."""
    _lib_action(egeria, "ea-empty", target=EMPTY_DB_GUID, report=EMPTY_REPORT)
    assert _sweep(reg).claimed == ["ea-empty"]
    _run_queue_once(reg)

    done = egeria.actions["ea-empty"]["completion"]
    assert done["status"] == "ACTIONED" and done["guards"] == ["survey-completed"], done
    [(report_guid, _)] = egeria.reports()
    got = _states(egeria, report_guid)
    assert {v[0] for v in got.values()} == {"not_established"} and len(got) == 2
    assert got[("Database Staleness", "empty_db")][1] == "not measured · no survey has counted rows yet"
    assert got[("Writes Since Statistics Reset", "empty_db")][1] == \
        "not measured · the survey report has no table measurements"
    outcome = _row(reg)["target"]["outcome"]
    assert outcome["guard"] == "survey-completed" and outcome["report_guid"] == report_guid
    _run, rows = reg.query_step_annotations("database", "empty_db", "database_staleness")
    assert [r["result_state"] for r in rows] == ["not_established"] and rows[0]["summary"]


def test_report_present_no_snapshots_publishes_writes_and_staleness_not_measured(
        staleness_installed, reg, egeria, no_connection):
    from tests.test_engine_host_step4_mixed_process import _native_report

    guid = "0a1b2c3d-0000-4000-8000-00000000e0e1"
    egeria.native_reports[guid] = _native_report(written={"src_cd": 250}, subject=EMPTY_DB_GUID, guid=guid)
    _lib_action(egeria, "ea-nosnap", target=EMPTY_DB_GUID, report=guid)
    _sweep(reg)
    _run_queue_once(reg)
    done = egeria.actions["ea-nosnap"]["completion"]
    assert done["status"] == "ACTIONED" and done["guards"] == ["survey-completed"], done
    [(report_guid, _)] = egeria.reports()
    got = _states(egeria, report_guid)
    assert got[("Database Staleness", "empty_db")][:2] == ("not_established",
                                                           "not measured · no survey has counted rows yet")
    assert got[("Writes Since Statistics Reset", "public.src_cd")][0] == "measured"
    assert all(v[1] for v in got.values())


def test_standalone_with_two_snapshots_completes_staleness_measured_writes_not_measured(
        staleness_installed, reg, egeria, no_connection):
    _second_survey(reg)
    _lib_action(egeria, "ea-alone2", report=None)
    assert _sweep(reg).claimed == ["ea-alone2"]
    _run_queue_once(reg)
    done = egeria.actions["ea-alone2"]["completion"]
    assert done["status"] == "ACTIONED" and done["guards"] == ["survey-completed"], done
    [(report_guid, body)] = egeria.reports()
    assert "derived_from_report" not in body["properties"].get("additionalProperties", {})
    got = _states(egeria, report_guid)
    assert got[("Database Staleness", "public.orders")][0] == "measured"
    assert got[("Writes Since Statistics Reset", "coco_pharma")][:2] == (
        "not_established", "writes since statistics reset: not measured · no survey report in this run")
    assert all(v[1] for v in got.values())


def test_standalone_with_one_snapshot_completes_with_both_absences(staleness_installed, reg, egeria, no_connection):
    _lib_action(egeria, "ea-alone1", report=None)
    _sweep(reg)
    _run_queue_once(reg)
    done = egeria.actions["ea-alone1"]["completion"]
    assert done["status"] == "ACTIONED" and done["guards"] == ["survey-completed"], done
    [(report_guid, _)] = egeria.reports()
    got = _states(egeria, report_guid)
    assert {v[0] for v in got.values()} == {"not_established"}
    assert got[("Database Staleness", "public.orders")][1] == "not measured · one snapshot only"
    assert got[("Writes Since Statistics Reset", "coco_pharma")][1] == \
        "writes since statistics reset: not measured · no survey report in this run"


def test_the_declared_nothing_guard_is_the_one_used(staleness_installed, reg, egeria, no_connection, monkeypatch):
    """A step that declares survey-completed for nothing to derive gets a published report and survey-completed."""
    from resource_explorer.surveyors import survey_step_host

    real = survey_step_host.discover_steps

    def declares_completed(entity_type):
        found = dict(real(entity_type))
        m = found["database_staleness"]
        found["database_staleness"] = m.model_copy(update={"nothing_to_derive_guard": "survey-completed"})
        return found

    monkeypatch.setattr(survey_step_host, "discover_steps", declares_completed)
    survey_step_host.refresh("database")
    _lib_action(egeria, "ea-empty2", target=EMPTY_DB_GUID, report=EMPTY_REPORT)
    _sweep(reg)
    _run_queue_once(reg)

    done = egeria.actions["ea-empty2"]["completion"]
    assert done["guards"] == ["survey-completed"], done
    [(report_guid, _)] = egeria.reports()
    assert egeria.annotations_under(report_guid) == 2      # both measures, each not_established


def test_a_failed_publish_never_completes_survey_completed(staleness_installed, reg, egeria, no_connection):
    _second_survey(reg)
    egeria.fail["create_annotation"] = RuntimeError("Egeria said no")
    _lib_action(egeria, "ea-libfail")
    _sweep(reg)
    _run_queue_once(reg)
    done = egeria.actions["ea-libfail"]["completion"]
    assert done["guards"] == ["survey-failed"], done
    assert "11 of 12 reached Egeria" in done["message"]


# ── what is left alone ───────────────────────────────────────────────────────

def test_an_unknown_request_type_is_still_left_unclaimed(staleness_installed, reg, egeria):
    egeria.add_action("ea-unknown", request_type="re-not-a-step")
    result = _sweep(reg)
    assert result.claimed == []
    assert result.skipped.get("unknown_request_type") == ["ea-unknown"]
    assert egeria.actions["ea-unknown"]["processingEngineUserId"] is None
    assert not egeria.method_calls("claim_engine_action")


def test_a_library_request_type_is_left_unclaimed_when_the_step_is_not_installed(reg, egeria):
    _lib_action(egeria, "ea-notinst")
    result = _sweep(reg)
    assert result.claimed == [] and result.skipped.get("unknown_request_type") == ["ea-notinst"]


def test_re_db_derived_is_unchanged_with_a_library_step_installed(staleness_installed, reg, egeria, no_connection):
    egeria.add_action("ea-db", request_type="re-db-derived")
    assert _sweep(reg).claimed == ["ea-db"]
    _run_queue_once(reg)
    done = egeria.actions["ea-db"]["completion"]
    assert done["guards"] == ["survey-completed"], done
    [(report_guid, body)] = egeria.reports()
    assert body["properties"]["purpose"] == "re-db-derived"
    assert all(a["analysisStep"] != "database_staleness" for a in _annotation_bodies(egeria, report_guid))
    assert _row(reg)["target"]["step_key"] == "db_derived"


def test_the_library_and_the_engine_host_name_the_same_survey_guards():
    from trellis_analytics.manifest import SURVEY_GUARDS

    from resource_explorer import engine_host

    assert SURVEY_GUARDS == engine_host.SURVEY_GUARDS


# ── review round 1 ────────────────────────────────────────────────────────────

def _with_extra_manifests(monkeypatch, *extra):
    """Discovery returns the installed steps plus `extra` manifests; RE re-attaches."""
    from resource_explorer.surveyors import survey_step_host

    real = survey_step_host.discover_steps
    monkeypatch.setattr(survey_step_host, "discover_steps",
                        lambda entity_type: {**real(entity_type), **{m.name: m for m in extra}})
    survey_step_host.refresh("database")


def _manifest(name, **kw):
    from trellis_analytics.manifest import StepManifest

    base = dict(name=name, function="x.y", description="d", source="ods", version="1", resource_types=["database"])
    base.update(kw)
    return StepManifest(**base)


@pytest.mark.parametrize("builtin", ["sql_analysis", "postgres_schema_and_stats", "postgres_operations", "db_derived"])
def test_f1_a_library_step_may_not_take_any_built_in_steps_name(staleness_installed, monkeypatch, builtin):
    from resource_explorer import engine_host
    from resource_explorer.surveyors import survey_step_host
    from resource_explorer.surveyors.database.survey_definition_adapter import DATABASE_STEP_REGISTRY
    from resource_explorer.surveyors.survey_definition_executor import get_adapter

    assert builtin in DATABASE_STEP_REGISTRY
    builtin_runner = get_adapter("database").re_analysis_steps[builtin]
    _with_extra_manifests(monkeypatch, _manifest(builtin))
    assert all(s.step_key != builtin for s in survey_step_host.library_steps().values())
    assert "built-in" in survey_step_host.library_step_refusals()[builtin]
    # the built-in's request type still dispatches to the built-in (every registry step is one since case 3)
    assert engine_host.supported_request_types().get("re-" + builtin.replace("_", "-")) == builtin
    # and it never replaces the built-in's runner
    assert get_adapter("database").re_analysis_steps[builtin] is builtin_runner


def test_f1_a_library_step_may_not_take_a_built_in_steps_request_type(staleness_installed, monkeypatch):
    from resource_explorer.surveyors import survey_step_host

    _with_extra_manifests(monkeypatch, _manifest("sneaky", request_type="re-sql-analysis"))
    assert "re-sql-analysis" not in survey_step_host.library_steps()
    assert "built-in" in survey_step_host.library_step_refusals()["sneaky"]


def test_an_annotation_type_the_report_cannot_carry_is_refused(staleness_installed, monkeypatch):
    from resource_explorer.surveyors import survey_step_host

    _with_extra_manifests(monkeypatch, _manifest("graded", annotation_type="QualityAnnotation"))
    assert set(survey_step_host.library_steps()) == {REQUEST_TYPE}
    assert "QualityAnnotation" in survey_step_host.library_step_refusals()["graded"]


def test_two_steps_declaring_one_request_type_are_both_refused(staleness_installed, monkeypatch):
    from resource_explorer.surveyors import survey_step_host

    _with_extra_manifests(monkeypatch, _manifest("twin", request_type=REQUEST_TYPE))
    assert REQUEST_TYPE not in survey_step_host.library_steps()
    refused = survey_step_host.library_step_refusals()
    assert "twin" in refused["database_staleness"] and "database_staleness" in refused["twin"]


def test_a_private_target_with_a_library_request_type_is_left_unclaimed(staleness_installed, reg, egeria):
    from resource_explorer.a2a_auth import CallerIdentity, current_caller
    from tests.test_engine_host_e4 import PRIVATE_DB_GUID

    reset = current_caller.set(CallerIdentity(user_id="alice", egeria_token=None, auth_source="app-jwt"))
    try:
        inv = reg.create_investigation("Alice Private", project_classification="PersonalProject")
        ws = reg.get_or_create_working_set(inv["slug"])
        reg.add_working_set_member(ws["slug"], "database", "alice_db")
    finally:
        current_caller.reset(reset)
    _lib_action(egeria, "ea-priv", target=PRIVATE_DB_GUID)
    result = _sweep(reg)
    assert result.claimed == [] and "ea-priv" in result.skipped.get("private_target", [])
    assert not egeria.method_calls("claim_engine_action")


def test_a_step_that_raises_completes_survey_failed(staleness_installed, reg, egeria, no_connection, monkeypatch):
    import database_staleness.step as user_code

    def boom(inputs):
        raise RuntimeError("the step broke")

    monkeypatch.setattr(user_code, "run", boom)
    _second_survey(reg)
    _lib_action(egeria, "ea-boom")
    _sweep(reg)
    _run_queue_once(reg)
    done = egeria.actions["ea-boom"]["completion"]
    assert done["guards"] == ["survey-failed"], done
    assert "the step broke" in done["message"]
    assert egeria.reports() == []


# F2: what step code can reach

def test_f2_a_step_gets_no_registry_no_dsn_and_no_egeria_identity(staleness_installed, reg, egeria, no_connection,
                                                                  monkeypatch):
    import database_staleness.step as user_code

    from resource_explorer.egeria_clients import NoCallerIdentity, current_principal

    seen = {}
    real = user_code.run

    def spy(inputs):
        ods = inputs.ods
        seen["attrs"] = {a for a in dir(ods) if not a.startswith("__")}
        seen["reg"] = any(hasattr(ods, a) for a in ("_reg", "url", "_conn", "registry_view"))
        seen["bound"] = [getattr(getattr(ods, a), "__self__", None) for a in seen["attrs"]]
        try:
            current_principal()
            seen["identity"] = "present"
        except NoCallerIdentity:
            seen["identity"] = "none"
        return real(inputs)

    monkeypatch.setattr(user_code, "run", spy)
    _second_survey(reg)
    _lib_action(egeria, "ea-narrow")
    _sweep(reg)
    _run_queue_once(reg)
    assert seen["reg"] is False, seen["attrs"]
    assert seen["attrs"] == {"row_count_snapshots", "commit_proofs", "catalogue_scope_events",
                             "resource_scope_events", "outbox_status_counts", "table_activity_snapshots",
                             "table_keys"}, seen["attrs"]   # no `query`
    assert all(b is None for b in seen["bound"]), "a method is bound to the reader object"
    assert seen["identity"] == "none"
    # publishing still happened as the daemon, after the step returned
    assert egeria.actions["ea-narrow"]["completion"]["guards"] == ["survey-completed"]


# F3: a worker without the package leaves the row queued

def test_f3_a_worker_without_the_package_leaves_a_library_row_queued(staleness_installed, reg, egeria, monkeypatch):
    from resource_explorer.surveyors import survey_step_host

    _second_survey(reg)
    _lib_action(egeria, "ea-q")
    assert _sweep(reg).claimed == ["ea-q"]
    # this worker does not have the package
    monkeypatch.setattr(survey_step_host, "discover_steps", lambda entity_type: {})
    survey_step_host.refresh("database")
    assert _run_queue_once(reg) is None
    assert _row(reg)["state"] == "queued"
    assert egeria.actions["ea-q"]["completion"] is None


def test_f3_a_row_no_worker_can_run_is_completed_survey_failed_after_the_bound(staleness_installed, reg, egeria,
                                                                             monkeypatch):
    from resource_explorer import engine_host
    from resource_explorer.surveyors import survey_step_host
    from tests.test_engine_host_e4 import _restart

    _lib_action(egeria, "ea-stuck")
    assert _sweep(reg).claimed == ["ea-stuck"]
    monkeypatch.setattr(survey_step_host, "discover_steps", lambda entity_type: {})
    survey_step_host.refresh("database")
    # within the bound: left queued
    assert "ea-stuck" in _restart(reg).pending and _row(reg)["state"] == "queued"
    monkeypatch.setattr(engine_host, "UNRUNNABLE_QUEUED_SECONDS", 0)
    result = _restart(reg)
    assert "ea-stuck" in result.completed
    done = egeria.actions["ea-stuck"]["completion"]
    assert done["guards"] == ["survey-failed"] and "not installed" in done["message"], done
    row = _row(reg)
    assert row["state"] == "failed" and row["target"]["outcome"]["guard"] == "survey-failed"


def test_understanding_lists_a_library_step_like_a_native_survey(staleness_installed, reg, egeria, no_connection,
                                                                 monkeypatch, tmp_path):
    import resource_explorer.config as config_module
    from resource_explorer.egeria_clients import Daemon, DaemonReason
    from resource_explorer.native_survey_run import (
        COMPLETE,
        PyegeriaSurveyPort,
        native_survey_rows,
        port_session,
        refresh_resource,
    )

    (tmp_path / "om.omsecrets").write_text("{}")
    monkeypatch.setenv("EGERIA_SECRETS_STORE_LOCAL_PATH", str(tmp_path / "om.omsecrets"))
    config_module._config = None
    _second_survey(reg)
    _lib_action(egeria, "ea-und-lib")
    _sweep(reg)
    _run_queue_once(reg)
    with port_session(PyegeriaSurveyPort(identity=Daemon(DaemonReason.SCHEDULER))) as port:
        assert refresh_resource(reg, port, "database", "coco_pharma") == 1
    rows = {r["qualified_name"]: r for r in native_survey_rows(reg, "database", "coco_pharma",
                                                                "PostgreSQL Relational Database")}
    mine = rows[f"ResourceExplorerSurvey::{REQUEST_TYPE}"]
    assert mine["display_name"] == "Resource Explorer: Database Staleness"
    assert mine["run"]["state"] == COMPLETE, mine["run"]
    assert mine["run"]["engine_action_guid"] == "ea-und-lib"
    assert mine["run"]["report_guid"] == _row(reg)["target"]["outcome"]["report_guid"]
    assert mine["credentials"] == "present" and not mine["cannot_run_reason"], mine


# ── review round 2: nothing reachable from a step's inputs holds the registry or its URL ──

def _reachable(root, limit: int = 4000):
    """Every object reachable from `root` through attributes, slots, closures, `__self__`, `__wrapped__`, function
    globals (not descending into modules or classes), containers. Bounded."""
    import types

    seen, out, todo = set(), [], [root]
    while todo and len(out) < limit:
        obj = todo.pop()
        if id(obj) in seen or isinstance(obj, (types.ModuleType, type)):
            continue
        seen.add(id(obj))
        out.append(obj)
        if isinstance(obj, (str, bytes, int, float, bool)) or obj is None:
            continue
        if isinstance(obj, dict):
            todo.extend(obj.keys())
            todo.extend(obj.values())
            continue
        if isinstance(obj, (list, tuple, set, frozenset)):
            todo.extend(obj)
            continue
        if isinstance(obj, types.FunctionType):
            todo.extend(c.cell_contents for c in (obj.__closure__ or ()) if _has(c))
            todo.extend(v for v in obj.__globals__.values() if not isinstance(v, (types.ModuleType, type)))
            todo.append(getattr(obj, "__wrapped__", None))
            todo.extend((obj.__defaults__ or ()))
            continue
        for name in ("__self__", "__func__", "__wrapped__"):
            if hasattr(obj, name):
                todo.append(getattr(obj, name))
        todo.append(getattr(obj, "__dict__", None))
        for slot in getattr(type(obj), "__slots__", ()) or ():
            if isinstance(slot, str) and hasattr(obj, slot):
                todo.append(getattr(obj, slot))
    return out


def _has(cell) -> bool:
    try:
        cell.cell_contents
        return True
    except ValueError:
        return False


def test_f2_round2_no_registry_or_dsn_is_reachable_and_reachable_connections_are_read_only(
        staleness_installed, reg, egeria, no_connection, monkeypatch):
    import sqlite3

    import database_staleness.step as user_code

    from resource_explorer.registry import ProjectRegistry

    found = {}
    real = user_code.run

    def probe(inputs):
        objs = _reachable(inputs)
        found["registries"] = [o for o in objs if isinstance(o, ProjectRegistry)]
        dsn, path = reg.database_url, reg.database_url.removeprefix("sqlite:///")
        found["dsn"] = [o for o in objs if isinstance(o, str) and (dsn in o or path in o)]
        found["writes"] = []
        for conn in (o for o in objs if isinstance(o, sqlite3.Connection)):
            try:
                conn.execute("CREATE TABLE step_was_here (a INTEGER)")
                found["writes"].append("created a table")
            except sqlite3.Error:
                pass
            try:
                conn.execute("DELETE FROM runs")
                found["writes"].append("deleted runs")
            except sqlite3.Error:
                pass
        found["connections"] = sum(1 for o in objs if isinstance(o, sqlite3.Connection))
        return real(inputs)

    monkeypatch.setattr(user_code, "run", probe)
    _second_survey(reg)
    _lib_action(egeria, "ea-walk")
    _sweep(reg)
    _run_queue_once(reg)
    assert found["registries"] == []
    assert found["dsn"] == []
    assert found["connections"] == 0          # the reader's connection lives in its own thread, out of reach
    assert found["writes"] == []
    assert egeria.actions["ea-walk"]["completion"]["guards"] == ["survey-completed"]


def test_the_claim_filter_is_exact_not_fooled_by_request_parameters(staleness_installed, reg):
    """A row whose request_parameters mention a supported request type is not claimable when its own
    request_type is not."""
    import json

    run_id = reg.enqueue_run("engine_action", {"engine_action_guid": "x", "request_type": "re-unknown",
                                               "request_parameters": {"k": '"request_type": "re-db-derived"',
                                                                      "request_type": "re-db-derived"}})
    assert reg.claim_next_run("w", {}, kinds=["engine_action"], engine_request_types={"re-db-derived"}) is None
    assert json.loads(reg.list_runs(kind="engine_action")[0]["target"])["request_type"] == "re-unknown"
    ok = reg.enqueue_run("engine_action", {"engine_action_guid": "y", "request_type": "re-db-derived"})
    assert reg.claim_next_run("w", {}, kinds=["engine_action"], engine_request_types={"re-db-derived"})["id"] == ok
    assert run_id


def test_a_refresh_removes_steps_the_last_attach_had_and_this_one_does_not(staleness_installed, monkeypatch):
    """Test-only (or uninstalled) steps never stay in the global step registry after the next attach."""
    from resource_explorer.surveyors import survey_step_host
    from resource_explorer.surveyors.database.survey_definition_adapter import DATABASE_STEP_REGISTRY
    from resource_explorer.surveyors.survey_definition_executor import get_adapter

    real = survey_step_host.discover_steps
    _with_extra_manifests(monkeypatch, _manifest("temporary_step"))
    assert "temporary_step" in DATABASE_STEP_REGISTRY
    monkeypatch.setattr(survey_step_host, "discover_steps", real)
    survey_step_host.refresh("database")
    assert "temporary_step" not in DATABASE_STEP_REGISTRY
    assert "temporary_step" not in get_adapter("database").re_analysis_steps
    assert "database_staleness" in DATABASE_STEP_REGISTRY
