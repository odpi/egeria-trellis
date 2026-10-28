"""Brief section 9 (owner, 2026-10-07): which analyses a survey definition runs.

The mapping is DERIVED from the definition's `re_analysis_step` properties through the adapter's
`analysis_source_steps`; nothing here (or in the code under test) keeps a list of analyses by hand.
Runs on a temp SQLite registry: no Postgres, no Egeria.
"""
from __future__ import annotations

import pytest

from resource_explorer.registry import DatabaseEntity, ProjectRegistry
from resource_explorer.workflows.stage_page import (
    analyses_run_by_definition,
    build_analyses_index,
    coverage_for_analysis,
    documented_definition_steps,
)

SCAN = "Database Scouting Scan"


@pytest.fixture
def reg(tmp_path):
    r = ProjectRegistry(db_path=str(tmp_path / "t.db"))
    r.register_database(DatabaseEntity(slug="aw", display_name="aw", db_type="postgresql",
                                       host="localhost", port=5432, database_name="aw"))
    return r


def _row(idx, aid):
    return next(r for r in idx["analyses"] if r["analysis_id"] == aid)


def test_the_five_scouting_analyses_name_the_scan_and_their_step(reg):
    idx = build_analyses_index(reg, "aw", entity_type="database")
    expect = {"schema_inventory": "postgres_schema_and_stats", "row_count_snapshot": "postgres_schema_and_stats",
              "db_activity_signals": "postgres_operations", "db_resilience": "postgres_operations",
              "credential_capability": "credential_capability"}
    for aid, step in expect.items():
        cov = _row(idx, aid)["definitions"]
        assert [(d["display_name"], d["step"]) for d in cov["runs_in"]] == [(SCAN, step)], aid
        assert cov["not_in"] == [], aid


def test_the_two_bundle_side_effects_say_the_scan_also_runs_them(reg):
    idx = build_analyses_index(reg, "aw", entity_type="database")
    for aid in ("db_external_dependencies", "privilege_audit"):
        cov = _row(idx, aid)["definitions"]
        assert any(d["display_name"] == SCAN and d["step"] == "postgres_operations" for d in cov["also_in"]), aid
        assert all(d["display_name"] != SCAN for d in cov["runs_in"]), aid


def test_a_definition_missing_one_analysis_yields_not_part_of_the_scan(reg):
    # An edited definition: the operations step is gone, so db_resilience is no longer covered.
    edited = [{"name": "DatabaseScoutingSurvey", "display_name": SCAN, "kind": "scouting",
               "steps": ["postgres_schema_and_stats", "credential_capability"]}]
    idx = build_analyses_index(reg, "aw", entity_type="database", definitions=edited)
    cov = _row(idx, "db_resilience")["definitions"]
    assert cov["runs_in"] == [] and cov["also_in"] == []
    assert [d["display_name"] for d in cov["not_in"]] == [SCAN]
    assert [d["display_name"] for d in _row(idx, "schema_inventory")["definitions"]["runs_in"]] == [SCAN]


def test_the_definition_counts_the_analyses_it_runs_from_its_steps(reg):
    from resource_explorer.surveyors.analysis_catalog_reader import get_analyses
    from resource_explorer.surveyors.database.survey_definition_adapter import DATABASE_ANALYSIS_RE_STEP_MAP
    scan = next(d for d in documented_definition_steps("database") if d["display_name"] == SCAN)
    entries = [a for a in get_analyses("database", include_egeria_live=False) if a.get("action") != "publish"]
    out = analyses_run_by_definition(scan, entries, DATABASE_ANALYSIS_RE_STEP_MAP)
    assert sorted(out["own"]) == sorted(["schema_inventory", "row_count_snapshot", "db_activity_signals",
                                         "db_resilience", "credential_capability"])
    assert sorted(out["also"]) == ["db_external_dependencies", "privilege_audit"]


def test_the_mapping_follows_the_definition_not_a_list(reg):
    """Drop a step from the definition and the count follows; nothing hand-kept can lag."""
    from resource_explorer.surveyors.analysis_catalog_reader import get_analyses
    from resource_explorer.surveyors.database.survey_definition_adapter import DATABASE_ANALYSIS_RE_STEP_MAP
    entries = [a for a in get_analyses("database", include_egeria_live=False) if a.get("action") != "publish"]
    smaller = {"kind": "scouting", "steps": ["postgres_schema_and_stats"]}
    assert sorted(analyses_run_by_definition(smaller, entries, DATABASE_ANALYSIS_RE_STEP_MAP)["own"]) == [
        "row_count_snapshot", "schema_inventory"]


def test_several_definitions_that_run_one_analysis_are_all_named():
    defs = [{"name": "A", "display_name": "Assessment A", "kind": "assessment", "steps": ["postgres_operations"]},
            {"name": "B", "display_name": "Assessment B", "kind": "assessment", "steps": ["postgres_operations"]}]
    cov = coverage_for_analysis("privilege_audit", "assessment", {"privilege_audit": ["postgres_operations"]}, defs)
    assert [d["display_name"] for d in cov["runs_in"]] == ["Assessment A", "Assessment B"]


def test_a_row_says_which_credential_it_uses_as_the_run_route_really_does(reg):
    idx = build_analyses_index(reg, "aw", entity_type="database")
    assert _row(idx, "schema_inventory")["uses_credential"] == "stored"
    assert _row(idx, "db_activity_signals")["uses_credential"] == "stored"
    assert _row(idx, "credential_capability")["uses_credential"] == "stored"
    assert _row(idx, "db_classification")["uses_credential"] == "none"       # db_derived: reads stored rows
    from resource_explorer.workflows.stage_page import _credential_use
    assert _credential_use("repo", "schema_inventory") == ""                  # not a database analysis
    assert _credential_use("database", "no_such_analysis") == ""             # no local runner: nothing to claim


def test_an_override_runs_ran_as_reaches_the_analyses_row(reg):
    import json
    from resource_explorer.activity_logger import log_analysis_run
    log_analysis_run(reg, "database", "aw", "aw", "ok", "ran", "schema_inventory", published=None,
                     extra={"ran_as": {"user": "one_off_user", "scope": "this run"},
                            "not_retried": "not retried · credential was for this run only"})
    row = _row(build_analyses_index(reg, "aw", entity_type="database"), "schema_inventory")
    assert row["last_run_ran_as"] == {"user": "one_off_user", "scope": "this run"}
    assert row["last_run_not_retried"] == "not retried · credential was for this run only"
    assert json.dumps(row).count("password") == 0


def test_last_run_info_sets_ran_as_and_not_retried_on_every_path(reg, monkeypatch):
    """The analyses index reads both keys from every path; a derived or never-run analysis has none."""
    from resource_explorer.activity_logger import log_analysis_run
    from resource_explorer.workflows import stage_page
    # never run
    assert stage_page._last_run_info(reg, "aw", "schema_inventory", "database") == {
        "last_run_at": "", "last_run_status": "", "last_run_via": "", "ran_as": None, "not_retried": ""}
    # own run
    log_analysis_run(reg, "database", "aw", "aw", "ok", "ran", "schema_inventory", published=None)
    own = stage_page._last_run_info(reg, "aw", "schema_inventory", "database")
    assert own["ran_as"] is None and own["not_retried"] == "" and own["last_run_at"]
    # derived path (repo)
    from resource_explorer.registry import Project
    reg.add(Project(slug="r", display_name="R", github_url="https://github.com/o/r"))
    log_analysis_run(reg, "repo", "r", "R", "ok", "ran", "src_analysis", published=None)
    monkeypatch.setattr("resource_explorer.surveyors.repo_survey_definition_adapter.repo_analysis_derived_sources",
                        lambda aid: {"src_analysis": ["k"]} if aid == "derived_one" else {})
    derived = stage_page._last_run_info(reg, "r", "derived_one", "repo")
    assert derived["last_run_via"] == "src_analysis" and derived["ran_as"] is None and derived["not_retried"] == ""


def test_the_index_row_survives_a_run_info_without_the_override_keys(reg, monkeypatch):
    from resource_explorer.workflows import stage_page
    monkeypatch.setattr(stage_page, "_last_run_info", lambda *a, **k: {
        "last_run_at": "x", "last_run_status": "ok", "last_run_via": "v"})
    row = _row(build_analyses_index(reg, "aw", entity_type="database"), "schema_inventory")
    assert row["last_run_ran_as"] is None and row["last_run_not_retried"] == ""
