"""A survey step that emits BOTH a passing check and a failing one in the
same run() must not have the passing check's summary end up under a
RequestForAction label in the activity log.

Real bug, found 2026-09-01 investigating Dan's report ("I don't know what
they mean... they all seem to be SecurityHygieneCheck 3... [and there seem
to be duplicates]"). Two of ten items in the RFA drawer read "Security
policy file present" — a PASS, from SecurityHygieneSurveyor's own
ClassificationAnnotation — but were typed "RequestForAction" and so matched
GET /api/activity/rfas' substring filter.

The bug was not in the surveyor (security_hygiene.py correctly emits
ClassificationAnnotation for a pass, RequestForActionAnnotation only for a
gap — verified by reading it). It was in SurveyOrchestrator.run()'s
self-logging: `by_step` grouped every annotation from one step by STEP NAME
ALONE, so a step producing several annotations (a pass here, a gap there)
collapsed into one dict. `summary` kept the FIRST annotation seen
(first-wins); `annotation_type` kept being overwritten to the LAST one seen
(last-wins) — two different "which one won" rules on the same group, which
is exactly the shape that let a passing summary wear a failing label.

This is also Problem 2 from the same investigation: FileSizeSurveyor's
"Repository disk footprint: ..." (a ResourceMeasureAnnotation, a
measurement, not a request) surfaced in the RFA drawer for the identical
reason — a large-file RequestForActionAnnotation from the SAME step
(FileSizeAnalysis) overwrote the group's annotation_type after the
measurement's summary had already been captured. One root cause, two
symptoms — confirmed here rather than assumed.
"""
from __future__ import annotations

from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import pytest

from resource_explorer.registry import Project, ProjectRegistry
from resource_explorer.surveyors.repo_survey_definition_adapter import STEP_REGISTRY
from resource_explorer.surveyors.survey_orchestrator import SurveyOrchestrator
from resource_explorer.surveyors.survey_report import (
    ClassificationAnnotation,
    RequestForActionAnnotation,
    ResourceMeasureAnnotation,
)


@contextmanager
def _fake_zipball_root(*_args, **_kwargs):
    yield "/fake/zipball/root"


@contextmanager
def _fake_git_clone_root(*_args, **_kwargs):
    yield "/fake/clone/root"


@pytest.fixture
def registry(tmp_path):
    return ProjectRegistry(db_path=str(tmp_path / "test.db"))


@pytest.fixture
def project(registry):
    registry.add(Project(
        slug="myproj", display_name="My Project",
        github_url="https://github.com/test/myproj", description="",
    ))
    with registry._conn() as conn:
        conn.execute(
            "INSERT INTO project_dependencies "
            "(project_slug, dep_name, dep_version, dep_type, ecosystem, "
            " source_file, indexed_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("myproj", "example:lib", "1.0", "runtime", "java", "pom.xml",
             "2026-09-01T00:00:00"),
        )
    return "myproj"


def _patch_all_surveyors_except(step_key: str, mixed_annotations: list):
    """Every step returns []; `step_key` returns `mixed_annotations` — the
    shape a real multi-finding step (SecurityHygieneCheck, FileSizeAnalysis)
    produces in one run()."""
    mocks: dict = {}
    patchers = []
    for key, info in STEP_REGISTRY.items():
        mock_cls = MagicMock()
        mock_cls.return_value.run.return_value = (
            mixed_annotations if key == step_key else []
        )
        p = patch.object(info, "surveyor_cls", mock_cls)
        p.start()
        patchers.append(p)
        mocks[key] = mock_cls
    for target, fake in (
        ("resource_explorer.surveyors.repo_survey_definition_adapter._acquire_zipball_root", _fake_zipball_root),
        ("resource_explorer.surveyors.repo_survey_definition_adapter._acquire_git_clone_root", _fake_git_clone_root),
    ):
        p = patch(target, fake)
        p.start()
        patchers.append(p)
    return mocks, patchers


def test_a_passing_check_never_wears_a_requestforaction_label(registry, project):
    """The known-negative: SecurityHygieneCheck emits a PASS
    (ClassificationAnnotation) then a GAP (RequestForActionAnnotation) in
    the same run — exactly security_hygiene.py's real shape when one check
    passes and another does not. No group in the logged activity entry may
    combine the pass's summary with the RequestForAction type.

    log_survey is mocked here (not read back from the registry) so this test
    is about the by_step grouping alone — every OTHER STEP_REGISTRY member is
    mocked too, and several synthesize their own precondition-skip
    annotations whose analysis_step reads from the mocked class rather than
    the real STEP constant; real-DB round-tripping those is a pre-existing,
    unrelated quirk this test should not need to route around. The drawer
    end-to-end test below exercises the real write path instead, with a
    file inventory present so no step needs to skip."""
    mixed = [
        ClassificationAnnotation(
            summary="Security policy file present",
            analysis_step="SecurityHygieneCheck",
            candidate_classifications=["HasSecurityPolicy"],
        ),
        RequestForActionAnnotation(
            summary="No CI configuration detected",
            analysis_step="SecurityHygieneCheck",
            action_requested="Add a CI configuration",
            action_target_name="CI config",
            explanation="CI catches regressions before merge.",
        ),
    ]
    mocks, patchers = _patch_all_surveyors_except("repo_security", mixed)
    try:
        with patch("resource_explorer.surveyors.survey_orchestrator.log_survey") as mock_log:
            SurveyOrchestrator(registry).run(project)
    finally:
        for p in patchers:
            p.stop()

    mock_log.assert_called_once()
    anns = mock_log.call_args.kwargs["annotations"]

    rfa_rows = [a for a in anns if "RequestForAction" in a["annotation_type"]
                and a["analysis_name"] == "SecurityHygieneCheck"]
    pass_rows = [a for a in anns if a["annotation_type"] == "ClassificationAnnotation"
                 and a["analysis_name"] == "SecurityHygieneCheck"]

    # The two source annotations must land in TWO groups, not one — that's
    # the fix (key by (step, type), not step alone).
    assert len(rfa_rows) == 1
    assert len(pass_rows) == 1

    # The known-negative itself: no RequestForAction-typed row may carry the
    # passing check's own words.
    for row in rfa_rows:
        assert "present" not in row["summary"], (
            f"a RequestForAction row carries a passing summary: {row!r}")
    assert rfa_rows[0]["summary"] == "No CI configuration detected"
    assert pass_rows[0]["summary"] == "Security policy file present"

    # Problem "the drawer says too little": explanation/action_requested/
    # action_target_name must ride along on the RFA row.
    assert rfa_rows[0]["explanation"] == "CI catches regressions before merge."
    assert rfa_rows[0]["action_requested"] == "Add a CI configuration"
    assert rfa_rows[0]["action_target_name"] == "CI config"


def test_a_measurement_never_wears_a_requestforaction_label(registry, project):
    """Problem 2, same root cause: FileSizeAnalysis emits a
    ResourceMeasureAnnotation (a measurement) then a
    RequestForActionAnnotation (a large-file flag) in the same run — the
    measurement's summary must not end up typed RequestForAction."""
    mixed = [
        ResourceMeasureAnnotation(
            summary="Repository disk footprint: 209.7 MB across 699 files (avg 307.1 KB/file)",
            analysis_step="FileSizeAnalysis",
            resource_properties={"total_size_bytes": 219902361, "total_files": 699},
        ),
        RequestForActionAnnotation(
            summary="Very large file: data/dump.sql (250.0 MB)",
            analysis_step="FileSizeAnalysis",
            action_requested="Consider Git LFS or object storage",
            action_target_name="data/dump.sql",
        ),
    ]
    mocks, patchers = _patch_all_surveyors_except("repo_file_size", mixed)
    try:
        with patch("resource_explorer.surveyors.survey_orchestrator.log_survey") as mock_log:
            SurveyOrchestrator(registry).run(project)
    finally:
        for p in patchers:
            p.stop()

    mock_log.assert_called_once()
    anns = mock_log.call_args.kwargs["annotations"]

    rfa_rows = [a for a in anns if "RequestForAction" in a["annotation_type"]
                and a["analysis_name"] == "FileSizeAnalysis"]
    measure_rows = [a for a in anns if a["annotation_type"] == "ResourceMeasureAnnotation"
                    and a["analysis_name"] == "FileSizeAnalysis"]

    assert len(rfa_rows) == 1
    assert len(measure_rows) == 1
    assert "disk footprint" not in rfa_rows[0]["summary"]
    assert measure_rows[0]["summary"].startswith("Repository disk footprint")


def _drawer_rows(reg, slug):
    """Exactly the selection GET /api/activity/rfas performs."""
    return [a for e in reg.list_activity(entity_slug=slug, limit=500)
            for a in (e.get("annotations") or [])
            if "RequestForAction" in (a.get("annotation_type") or "")]


def test_the_rfa_drawer_feed_shows_only_the_real_gap(registry, project):
    """End-to-end through the exact selection GET /api/activity/rfas uses —
    a passing check must produce nothing the drawer would ever show.

    A minimal file inventory is seeded so no OTHER step's precondition
    trips a skip — those skips are real and unrelated to this bug; this test
    is about the RFA feed for one step's mixed pass/gap output, not about
    every precondition in the registry."""
    registry.upsert_file_inventory(project, [("README.md", 100)])
    with registry._conn() as conn:       # and one stored symbol: repo_api_structure's has_code_symbols
        conn.execute(
            "INSERT INTO project_code_symbols (project_slug, file_path, language, kind, name, "
            "qualified_name) VALUES (?, 'a.py', 'python', 'function', 'f', 'a.f')", (project,))
    mixed = [
        ClassificationAnnotation(
            summary="Security policy file present",
            analysis_step="SecurityHygieneCheck",
            candidate_classifications=["HasSecurityPolicy"],
        ),
        RequestForActionAnnotation(
            summary="No CI configuration detected",
            analysis_step="SecurityHygieneCheck",
            action_requested="Add a CI configuration",
        ),
    ]
    mocks, patchers = _patch_all_surveyors_except("repo_security", mixed)
    try:
        SurveyOrchestrator(registry).run(project)
    finally:
        for p in patchers:
            p.stop()

    rows = _drawer_rows(registry, project)
    assert len(rows) == 1
    assert rows[0]["summary"] == "No CI configuration detected"
