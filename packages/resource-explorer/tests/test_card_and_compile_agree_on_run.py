"""The Analysis card and the chat compile must agree on whether an analysis ran.

Seeded in the shape found live on `marquez` (2026-10-13): two surveys whose
activity_log rows carry NO `steps` (empty detail), step_runs rows for every
step that executed, and stored findings for cve_scan and dependency_support.

Before the fix the card (FactLayer -> registry.get_analysis_last_run, which
read only activity_log) said "never run" / "predate per-step recording" for
both, while the compile (which reads stored findings) packed them as
evidence. The run record now also reads step_runs, so card and compile derive
"did it run" from records that exist.
"""
from __future__ import annotations

import pytest

from resource_explorer.context_compile import compile_context
from resource_explorer.facts import FactLayer
from resource_explorer.registry import ActivityEntry, Project, ProjectRegistry
from resource_explorer.surveyors.result_status import MEASURED, NEVER_RUN, NOT_ESTABLISHED

SNAP = "2026-10-11T21:25:47.660814"


@pytest.fixture
def registry(tmp_path):
    reg = ProjectRegistry(db_path=str(tmp_path / "reg.db"))
    reg.add(Project(slug="marquez", display_name="M", github_url="u", description=""))
    # A survey that finished minutes after its steps started, recording no steps.
    reg.write_activity(ActivityEntry(
        id="s1", ts="2026-10-11T21:29:05.175311+00:00", operation="survey",
        intent="assessment", entity_type="repo", entity_slug="marquez",
        summary="117 annotation(s), 0 error(s)", detail=""))
    for step in ("repo_cve_scan", "repo_dependency", "repo_dependency_support"):
        reg.record_step_run("marquez", step, SNAP, entity_type="repo")
    reg.upsert_finding("marquez", "cve_scan", [{
        "check_name": "python:idna", "label": "moderate",
        "summary": "4 advisories for idna ==2.10: GHSA-65pc-fj4g-8rjx",
        "confidence": 100, "detail": {}}], surveyed_at=SNAP)
    reg.upsert_finding("marquez", "dependency_support", [{
        "check_name": "technology", "label": "PostgreSQL",
        "summary": "PostgreSQL - indicated by org.postgresql:postgresql",
        "confidence": 85, "detail": {}}], surveyed_at=SNAP)
    return reg


def _packed(c):
    return {p["key"] for p in c.manifest["packed"]}


def test_card_and_compile_agree_that_cve_scan_and_dependency_support_ran(registry):
    facts = FactLayer(registry)
    for aid in ("cve_scan", "dependency_support"):
        f = facts.fact("marquez", aid)
        assert f.state not in (NEVER_RUN, NOT_ESTABLISHED), (aid, f.state, f.note)
        assert f.last_run_at.startswith(SNAP[:19])
    c = compile_context(registry, "marquez", "Are there outstanding CVEs?")
    assert "cve_scan" in _packed(c)
    gap_keys = {g["key"] for g in c.manifest["gaps"]}
    assert not gap_keys & {"cve_scan", "dependency_support"}


def test_a_step_runs_only_credit_claims_it_ran_not_that_it_succeeded(registry):
    """step_runs rows exist for a step that raised too (survey_orchestrator
    records outside the try), so the credit must not carry status 'ok'."""
    runs = registry.get_analysis_last_run("repo", "marquez")
    for aid in ("cve_scan", "dependency_support"):
        assert runs[aid]["last_run_via"] == "step_runs"
        assert runs[aid]["last_run_status"] == "recorded"


def test_a_step_runs_credit_never_overrides_a_logged_error(registry):
    """An activity_log row that names the steps and says error stays as is
    when it is at least as new as the step_runs snapshot."""
    import json
    registry.write_activity(ActivityEntry(
        id="s2", ts="2026-10-12T10:00:00+00:00", operation="survey",
        intent="assessment", entity_type="repo", entity_slug="marquez",
        detail=json.dumps({"surveyed_at": "2026-10-12T10:00:00", "steps": [
            {"step": "repo_cve_scan", "re_analysis_step": "repo_cve_scan",
             "status": "error"}]})))
    run = registry.get_analysis_last_run("repo", "marquez")["cve_scan"]
    assert run["last_run_status"] == "error" and run["last_run_via"] == "survey"


def test_an_analysis_with_no_run_and_no_results_is_a_compile_gap_and_never_run(registry):
    # No step_runs row and no findings for repo_license_classification.
    f = FactLayer(registry).fact("marquez", "license_classification")
    assert f.state in (NEVER_RUN, NOT_ESTABLISHED)
    c = compile_context(registry, "marquez", "What license does this use?")
    assert "license_classification" not in _packed(c)
    assert "license_classification" in {g["key"] for g in c.manifest["gaps"]}


def test_a_survey_older_than_any_step_record_stays_unattributable(tmp_path):
    reg = ProjectRegistry(db_path=str(tmp_path / "old.db"))
    reg.add(Project(slug="old", display_name="O", github_url="u", description=""))
    reg.write_activity(ActivityEntry(
        id="s0", ts="2026-08-01T10:00:00+00:00", operation="survey",
        intent="assessment", entity_type="repo", entity_slug="old", detail=""))
    f = FactLayer(reg).fact("old", "cve_scan")
    assert f.state == NOT_ESTABLISHED
    assert "predate" in f.note
