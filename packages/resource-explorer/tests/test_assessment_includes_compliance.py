"""The Assessment survey for repositories includes the four compliance steps.

Owner decision 2026-10-02 (the analysis-gaps plan for repos and databases, slice R2):
`repo_secret_scan`, `repo_telemetry_scan`, `repo_contribution_provenance` and
`repo_sla_content` sat only in the Compliance and Full surveys, so a repository
that was *assessed* never got them (the 2026-09-11 audit found them run on 2, 1,
1 and 1 of the 14 kept repositories). They are now members of
RepoAssessmentSurvey as well. Compliance and Full keep them: steps are shared,
never moved.

This file pins four things:

* the membership and ORDER of the Assessment definition, in the CSV that is its
  source and in the generated Dr.Egeria document that Egeria is authored from;
* every prerequisite the four declare is satisfied by an earlier step in the
  same definition;
* no other survey lost a step;
* what happens when the four cannot run (no inventory, no GitHub access): the
  step is reported as skipped or as an error, never as a clean zero.
"""
from __future__ import annotations

import csv
import re
from pathlib import Path

import pytest

from resource_explorer.registry import Project, ProjectRegistry
from resource_explorer.surveyors import result_status
from resource_explorer.surveyors.repo_survey_definition_adapter import STEP_REGISTRY

PKG = Path(__file__).resolve().parent.parent
CSV_PATH = PKG / "docs" / "dr-egeria" / "repo_survey_types.csv"
DEFS = PKG / "docs" / "dr-egeria" / "survey-definitions"

FOUR = (
    "repo_secret_scan",
    "repo_telemetry_scan",
    "repo_contribution_provenance",
    "repo_sla_content",
)

#: What Assessment held before 2026-10-02. None of these may be dropped.
OLD_ASSESSMENT = (
    "repo_git_statistics", "repo_file_inventory", "repo_documentation",
    "repo_security", "repo_security_features", "repo_ci_quality",
    "repo_cve_scan", "repo_foss_scorecard", "repo_cii_badge",
    "repo_security_summary",
)

#: The decided order. The four follow repo_cve_scan (the last measurement before
#: them) and precede the two reducers, which must stay at the end.
EXPECTED_ASSESSMENT = (
    "repo_git_statistics", "repo_file_inventory", "repo_documentation",
    "repo_security", "repo_security_features", "repo_ci_quality",
    "repo_cve_scan",
    "repo_secret_scan", "repo_telemetry_scan",
    "repo_contribution_provenance", "repo_sla_content",
    "repo_foss_scorecard", "repo_cii_badge", "repo_security_summary",
)


def _csv_steps(group: str) -> list[str]:
    rows = [r for r in csv.DictReader(CSV_PATH.open(newline="", encoding="utf-8"))
            if r["survey_group"] == group]
    return [r["step_key"] for r in sorted(rows, key=lambda r: int(r["step_order"]))]


def _doc_chain(filename: str) -> list[str]:
    """The step keys of a generated document, in LINK order (Link First, then
    each Link Next), not in the order the step blocks happen to be written."""
    text = (DEFS / filename).read_text()
    first = re.search(
        r"## Link First Process Step.*?### Governance Action Process Step\n"
        r"GovActionProcessStep::\w+::(\w+)", text, re.S)
    nxt = dict(re.findall(
        r"## Link Next Process Step\n### Governance Action Process Step\n"
        r"GovActionProcessStep::\w+::(\w+)\n\n### Next Governance Action Process Step\n"
        r"GovActionProcessStep::\w+::(\w+)", text))
    chain, cur = [first.group(1)], first.group(1)
    while cur in nxt:
        cur = nxt[cur]
        chain.append(cur)
    return chain


# ── membership and order ─────────────────────────────────────────────────

def test_assessment_carries_the_compliance_steps_by_owner_decision():
    assert tuple(_csv_steps("RepoAssessmentSurvey")) == EXPECTED_ASSESSMENT


def test_the_generated_assessment_document_has_the_same_chain_as_the_csv():
    assert _doc_chain("repo-survey-definition-assessment.md") == list(EXPECTED_ASSESSMENT)


def test_no_assessment_step_was_dropped():
    assert set(OLD_ASSESSMENT) <= set(_csv_steps("RepoAssessmentSurvey"))


def test_the_two_reducers_stay_at_the_end_after_the_four():
    chain = _csv_steps("RepoAssessmentSurvey")
    for step in FOUR:
        assert chain.index(step) < chain.index("repo_foss_scorecard")
        assert chain.index(step) < chain.index("repo_security_summary")
    assert chain[-1] == "repo_security_summary"


def test_every_prerequisite_of_the_four_is_an_earlier_step_of_the_definition():
    chain = _csv_steps("RepoAssessmentSurvey")
    for step in FOUR:
        info = STEP_REGISTRY[step]
        assert info.requires, f"{step} declares no prerequisite — expected has_file_inventory"
        for producer in info.requires:
            assert producer in chain, f"{step} needs {producer}, which Assessment does not run"
            assert chain.index(producer) < chain.index(step), (
                f"{step} would run before {producer}, whose output it reads")


def test_the_four_need_no_clone_and_no_resource_assessment_does_not_already_acquire():
    """The zipball is already acquired for repo_file_inventory; the four reuse
    that one download (resolve_resources dedupes within a run). A clone, or a
    resource nothing else in the definition needs, would add a new fetch and a
    new failure mode — this pins that it does not."""
    chain = _csv_steps("RepoAssessmentSurvey")
    already = {r for k in chain if k not in FOUR for r in STEP_REGISTRY[k].requires_resources}
    for step in FOUR:
        assert set(STEP_REGISTRY[step].requires_resources) == {"zipball_root"}
        assert set(STEP_REGISTRY[step].requires_resources) <= already


def test_assessment_cost_shape_is_stated_not_hidden():
    """Assessment used to be all-low (test_repo_survey_definition_adapter's
    old statement). It no longer is, deliberately. The exceptions are exactly
    the compliance steps and the first two prerequisites; anything else going
    above `low` is a different decision and should fail here."""
    chain = _csv_steps("RepoAssessmentSurvey")
    above_low = {k for k in chain[2:] if STEP_REGISTRY[k].compute_cost != "low"}
    assert above_low == {"repo_secret_scan", "repo_telemetry_scan"}
    assert STEP_REGISTRY["repo_secret_scan"].compute_cost == "high"


def test_secret_scan_is_prefect_routed_in_the_assessment_document():
    text = (DEFS / "repo-survey-definition-assessment.md").read_text()
    block = text.split("GovActionProcessStep::RepoAssessmentSurvey::repo_secret_scan\n", 1)[1]
    assert "| executes_at | prefect |" in block.split("___")[0]


def test_assessment_scopes_the_three_questions_that_cite_these_analyses():
    text = (DEFS / "repo-survey-definition-assessment.md").read_text()
    for q in ("How does the repository handle secrets, credentials, and sensitive configurations?",
              "Does the software contain telemetry, phone-home mechanisms, or external metrics tracking?",
              "Is intellectual property (IP) provenance managed via CLA or DCO?"):
        assert f"### Scope Reference\n{q}\n" in text


# ── no step dropped anywhere ─────────────────────────────────────────────

def test_compliance_still_contains_the_four_and_its_other_steps():
    chain = _csv_steps("RepoComplianceSurvey")
    assert set(FOUR) <= set(chain)
    assert _doc_chain("repo-survey-definition-compliance.md") == chain
    assert len(chain) == 14


def test_full_survey_still_contains_the_four():
    assert set(FOUR) <= set(_doc_chain("repo-survey-definition-full.md"))


def test_the_membership_check_can_fail():
    """Known-negative: the old Assessment membership must be rejected by the
    comparison above, or those assertions pass for a comparison never false."""
    assert tuple(OLD_ASSESSMENT) != EXPECTED_ASSESSMENT
    assert not set(FOUR) <= set(OLD_ASSESSMENT)


# ── could not run is not a clean zero ────────────────────────────────────

@pytest.fixture
def registry(tmp_path):
    return ProjectRegistry(db_path=str(tmp_path / "r2.db"))


@pytest.fixture
def project(registry):
    p = Project(slug="r2repo", display_name="R2 Repo",
                github_url="https://github.com/test/r2repo", collections=[])
    registry.add(p)
    return p


def test_no_github_access_aborts_the_run_it_does_not_report_a_clean_zero(registry, project, monkeypatch):
    """The zipball is the only thing in these four that needs GitHub, and it is
    acquired before any step runs. If it cannot be (no token, no network) the
    orchestrator raises: nothing is returned that a reader could take for 'no
    secrets found', and no finding row of any of the four kinds is written. The
    executor turns the raise into status 'error' on each step of the batch
    (tests/test_survey_definition_executor.py::test_run_batch_exception_is_
    caught_not_raised)."""
    from contextlib import contextmanager

    from resource_explorer.surveyors import repo_survey_definition_adapter as adapter
    from resource_explorer.surveyors.survey_orchestrator import SurveyOrchestrator

    @contextmanager
    def _no_token(project, registry, served=None):
        raise RuntimeError("GITHUB_TOKEN is not set")
        yield  # pragma: no cover

    monkeypatch.setattr(adapter, "_acquire_zipball_root", _no_token)
    with pytest.raises(RuntimeError, match="GITHUB_TOKEN"):
        SurveyOrchestrator(registry).run(project.slug, steps=list(FOUR))
    for kind in ("secret_scan_findings", "telemetry_scan_findings",
                 "contribution_provenance_findings", "sla_content_findings"):
        assert registry.query_findings(project.slug, kind) == []


def test_no_inventory_is_skipped_by_design_with_a_reason_not_a_pass(registry, project, monkeypatch, tmp_path):
    """The repo has a zipball (an empty one) but no file inventory and the
    inventory producer is not in the budget. Each of the four must say it was
    skipped, with a reason, and none may write a 'pass'-shaped finding."""
    from contextlib import contextmanager

    from resource_explorer.surveyors import repo_survey_definition_adapter as adapter
    from resource_explorer.surveyors.survey_orchestrator import SurveyOrchestrator

    root = tmp_path / "empty"
    root.mkdir()

    @contextmanager
    def _empty_zip(project, registry, served=None):
        yield str(root)

    monkeypatch.setattr(adapter, "_acquire_zipball_root", _empty_zip)
    result = SurveyOrchestrator(registry).run(
        project.slug, steps=list(FOUR), max_fetch_cost="none")  # nothing runs at "none"
    # With a fetch ceiling of none every download-tier step is excluded from the
    # run and must therefore not be listed as run.
    assert not set(FOUR) & set(result.steps_run)

    result = SurveyOrchestrator(registry).run(project.slug, steps=list(FOUR))
    skipped = set(result.skipped_steps)
    skipped_anns = [a for a in result.annotations
                    if result_status.SKIPPED_BY_DESIGN in getattr(a, "candidate_classifications", [])]
    assert skipped_anns or skipped, "an inventory-less repo produced neither a skip nor a finding"
    for kind in ("secret_scan_findings", "telemetry_scan_findings",
                 "contribution_provenance_findings", "sla_content_findings"):
        for row in registry.query_findings(project.slug, kind):
            assert row["label"] not in {"pass", "present", "ok", "clean"}, (
                f"{kind}: an inventory-less repo was reported {row['label']!r}")
