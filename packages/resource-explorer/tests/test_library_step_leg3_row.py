"""Leg 3 (gate 2026-10-29): a library step's run shows on the Questions tab for databases and file systems alike.

F3 (db): the step answers the questions its manifest names; its drill-down answers. F21: the row's line is the step's
sentence, the one the Survey tab shows, not "1 result: 1 measured". F19: a measure that could not be measured is its own
row with its own state. F20: counts in number. Re-run for a database library step is the run route's predicate.
"""
from __future__ import annotations

from resource_explorer.registry import DatabaseEntity, ProjectRegistry
from tests.library_step_install import install_steps  # noqa: F401 - the fixture


def _db(tmp_path):
    reg = ProjectRegistry(db_path=str(tmp_path / "db.db"))
    reg.register_database(DatabaseEntity(slug="coco", display_name="Coco", db_type="postgresql", host="localhost",
                                         egeria_host="h", port=5442, database_name="coco"))
    return reg


def _store_freshness(reg):
    reg.record_step_annotations("database", "coco", "statistics_freshness", run="r1", measured_at="2026-10-29T10:00:00",
                                annotations=[{"annotation_type": "ResourceMeasureAnnotation", "scope": "public.t",
                                              "summary": "never analyzed; never vacuumed (as of 2026-10-26T01:00:00)",
                                              "resourceProperties": {"Days Since Analyze": None, "asOf": "2026-10-26T01:00:00"},
                                              "additionalProperties": {"resultState": "measured"}}])
    reg.record_step_annotations("database", "coco", "statistics_freshness:statistics_freshness_summary", run="r1",
                                measured_at="2026-10-29T10:00:00",
                                annotations=[{"annotation_type": "ResourceMeasureAnnotation", "scope": "coco",
                                              "summary": "of 1 tables: 1 need attention, 1 never analyzed, 0 stale, 0 current (as of 2026-10-26T01:00:00)",
                                              "resourceProperties": {"Needs Attention": 1, "asOf": "2026-10-26T01:00:00"},
                                              "additionalProperties": {"resultState": "measured"}}])


def test_a_step_answers_the_questions_its_manifest_names_after_rives_own_analyses(install_steps):
    from resource_explorer.surveyors.question_catalog_reader import get_questions

    install_steps("statistics_freshness")
    q = next(q for q in get_questions("database") if q["id"] == "Q-0073")
    assert q["answering"]["analysis_ids"] == ["db_statistics_freshness", "statistics_freshness"]   # catalogue order, no merge


def test_a_file_systems_questions_gain_the_step_too(install_steps):
    from resource_explorer.surveyors.question_catalog_reader import get_questions

    install_steps("fs_activity")
    q = next(q for q in get_questions("filesystem") if q["id"] == "Q-0107")
    assert "fs_activity" in q["answering"]["analysis_ids"]


def test_the_database_drill_down_answers_with_the_summary_row_and_the_rows_line_is_the_steps_sentence(install_steps, tmp_path):
    from resource_explorer.surveyors import annotation_readers as ar
    from resource_explorer.workflows.stage_page import build_measurements

    install_steps("statistics_freshness")
    reg = _db(tmp_path)
    _store_freshness(reg)
    out = build_measurements(reg, "coco", "statistics_freshness", "database")
    names = [m["name"] for m in out["measurements"]]
    assert "public.t" in names and "statistics_freshness_summary" in names       # the further measure is its own row
    summary = next(m for m in out["measurements"] if m["name"] == "statistics_freshness_summary")
    assert summary["state"] == "measured" and "1 need attention" in summary["value"]
    line = ar.headline_reader("database", "statistics_freshness", "ResourceMeasureAnnotation")(reg, "coco")["label"]
    assert line == f"measured · {summary['value']}"                              # the Survey tab's `state words · summary`
    assert "result" not in line


def test_one_plain_result_is_its_sentence_and_not_a_count(tmp_path):
    from resource_explorer.surveyors import annotation_readers as ar

    reg = _db(tmp_path)
    reg.record_step_annotations("database", "coco", "zz_step", run="r", measured_at="2026-10-29T10:00:00",
                                annotations=[{"annotation_type": "ResourceMeasureAnnotation", "scope": "coco",
                                              "summary": "not measured · no statistics stored", "resourceProperties": {},
                                              "additionalProperties": {"resultState": "not_established",
                                                                       "resultStateReason": "no table statistics stored"}}])
    label = ar.headline_reader("database", "zz_step", "ResourceMeasureAnnotation")(reg, "coco")["label"]
    assert label == "not established · not measured · no statistics stored · no table statistics stored"
    assert "result" not in label


def test_a_measure_that_could_not_be_measured_is_its_own_row_with_its_own_state(install_steps, tmp_path):
    from tests.test_files_derived_steps_host import _register, _tree, _walk
    from resource_explorer.workflows.analysis import run_filesystem_analysis
    from resource_explorer.workflows.stage_page import build_measurements

    install_steps("fs_activity")
    reg = ProjectRegistry(db_path=str(tmp_path / "fs.db"))
    _walk(reg, _register(reg, _tree(tmp_path)))
    assert run_filesystem_analysis("tree", "fs_activity", registry=reg).status == "ok"
    reg.record_step_annotations("filesystem", "tree", "fs_activity:fs_access_activity", run="r", measured_at="2026-10-29T10:00:00",
                                annotations=[{"annotation_type": "ResourceProfileAnnotation", "scope": "tree",
                                              "summary": "not measured · access times not recorded on this walk",
                                              "resourceProperties": {}, "additionalProperties": {
                                                  "resultState": "not_established",
                                                  "resultStateReason": "access times not recorded on this walk"}}])
    rows = build_measurements(reg, "tree", "fs_activity", "filesystem")["measurements"]
    access = next(m for m in rows if m["name"] == "fs_access_activity")
    assert access["state"] == "not_established" and access["state_words"] == "not established"
    assert access["value"].startswith("not measured") and access["reason"]
    assert not any(str(m["value"]).startswith("0 ") for m in rows)
    assert any(m["state"] == "measured" for m in rows if m["name"] != "fs_access_activity")   # the modified bands are not dragged down


def test_counts_are_in_number(install_steps):
    import importlib

    install_steps("fs_permissions", "fs_activity")
    perms = importlib.import_module("fs_permissions.step")
    activity = importlib.import_module("fs_activity.step")
    assert perms._n(1, "owner") == "1 owner" and perms._n(2, "owner") == "2 owners"
    assert activity._n(1, "file") == "1 file" and activity._n(3, "file") == "3 files"


def test_re_run_for_a_database_library_step_is_the_run_routes_predicate(install_steps):
    from resource_explorer.workflows.stage_page import runnable_and_reason

    runnable, reason = runnable_and_reason("statistics_freshness", "database")
    assert runnable is False and reason.startswith("step not installed here: 'statistics_freshness'")
    install_steps("statistics_freshness")
    assert runnable_and_reason("statistics_freshness", "database") == (True, "")
