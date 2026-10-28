"""Tests for SurveyOrchestrator's steps parameter.

Focus: steps=None must keep running every sub-surveyor and self-logging
exactly as before this parameter existed (regression guard); steps=[...]
must run only the named sub-surveyor(s) and must NOT self-log — the caller
(scheduler.py's per-analysis-id dispatch, or repo_survey_definition_adapter.py)
writes its own, more specific activity-log entry for a targeted run, and
self-logging too would double it.
"""
from __future__ import annotations

from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import pytest

from resource_explorer.registry import Project, ProjectRegistry
from resource_explorer.surveyors.repo_survey_definition_adapter import STEP_REGISTRY
from resource_explorer.surveyors.survey_orchestrator import SurveyOrchestrator


@contextmanager
def _fake_zipball_root(*_args, **_kwargs):
    """Stand-in for _acquire_zipball_root (D6) — no real network call.
    Any full-run (steps=None) test now resolves this resource because
    repo_data_profiling declares requires_resources={"zipball_root": ...}."""
    yield "/fake/zipball/root"


@contextmanager
def _fake_git_clone_root(*_args, **_kwargs):
    """Stand-in for _acquire_git_clone_root — no real clone. Any full-run
    (steps=None) test now resolves this resource too, because
    repo_arch_coupling declares requires_resources={"git_clone_root": ...}."""
    yield "/fake/clone/root"


@pytest.fixture
def registry(tmp_path):
    return ProjectRegistry(db_path=str(tmp_path / "test.db"))


@pytest.fixture
def project(registry):
    # A dependency row, so `repo_cve_scan`'s `requires_context` precondition is
    # satisfied and it dispatches like every other step. These tests are about
    # "all selected steps run", not about preconditions — without this they fail
    # on a step that is correctly SKIPPED rather than on a dispatch bug, which
    # would make them report the wrong thing. Precondition behaviour has its own
    # file: tests/test_step_preconditions.py.
    registry.add(Project(
        slug="myproj",
        display_name="My Project",
        github_url="https://github.com/test/myproj",
        description="",
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
        # And a stored symbol, so `repo_api_structure`'s `has_code_symbols` is met for the same reason.
        conn.execute(
            "INSERT INTO project_code_symbols (project_slug, file_path, language, kind, name, "
            "qualified_name) VALUES ('myproj', 'a.py', 'python', 'function', 'f', 'a.f')")
    return "myproj"



#: Steps that a bare fixture project legitimately does NOT run.
#:
#: Each declares a `requires_context` precondition, so on a project with no
#: file inventory the orchestrator skips it and records SKIPPED_BY_DESIGN with a
#: reason. That is the precondition mechanism working, not a gap in coverage —
#: and the tests below assert the SKIP is recorded rather than silently
#: excluding these, because a step that vanishes from a run is indistinguishable
#: from one that ran and found nothing.
def _precondition_skipped(project, registry):
    from resource_explorer.surveyors import step_preconditions
    from resource_explorer.surveyors.repo_survey_definition_adapter import STEP_REGISTRY

    # `project` is the SLUG here (what SurveyOrchestrator.run takes); the
    # precondition evaluator wants the Project row. Resolving it rather than
    # passing the string through is the difference between asking the real
    # question and getting an AttributeError that looks like a test bug.
    proj = registry.get(project) if isinstance(project, str) else project
    out = set()
    for key, info in STEP_REGISTRY.items():
        ctx = getattr(info, "requires_context", None)
        if not ctx:
            continue
        may_run, _name, _why = step_preconditions.evaluate(registry, proj, ctx)
        if not may_run:
            out.add(key)
    return out


def _patch_all_surveyors():
    """Patch every StepInfo.surveyor_cls in STEP_REGISTRY so .run() returns
    [] with no real filesystem/network work, while leaving us able to see
    which ones were actually constructed.

    Patching module-level class names (e.g. patch(".survey_orchestrator.
    HealthSurveyor")) doesn't work post-registry-consolidation — SurveyOrchestrator
    no longer imports these classes itself, and STEP_REGISTRY's StepInfo
    entries already hold direct references to the real class objects
    (captured once, at module-import time). Patching info.surveyor_cls on
    each entry directly is the only thing that actually intercepts
    construction.
    """
    mocks: dict[str, MagicMock] = {}
    patchers = []
    for step_key, info in STEP_REGISTRY.items():
        mock_cls = MagicMock()
        mock_cls.return_value.run.return_value = []
        p = patch.object(info, "surveyor_cls", mock_cls)
        p.start()
        patchers.append(p)
        mocks[step_key] = mock_cls
    # D6: repo_data_profiling now declares requires_resources -- patch the
    # actual zipball-download primitive so a full run doesn't hit the real
    # GitHub API in a unit test.
    zip_patcher = patch(
        "resource_explorer.surveyors.repo_survey_definition_adapter._acquire_zipball_root",
        _fake_zipball_root,
    )
    zip_patcher.start()
    patchers.append(zip_patcher)
    # repo_arch_coupling declares requires_resources={"git_clone_root": ...} —
    # patch the clone primitive too, same reasoning as the zipball one above.
    clone_patcher = patch(
        "resource_explorer.surveyors.repo_survey_definition_adapter._acquire_git_clone_root",
        _fake_git_clone_root,
    )
    clone_patcher.start()
    patchers.append(clone_patcher)
    return mocks, patchers


class TestStepsNone:
    def test_runs_all_ten_surveyors_and_self_logs(self, registry, project):
        mocks, patchers = _patch_all_surveyors()
        try:
            with patch("resource_explorer.surveyors.survey_orchestrator.log_survey") as mock_log:
                result = SurveyOrchestrator(registry).run(project)
                skipped = _precondition_skipped(project, registry)
                for key, m in mocks.items():
                    if key in skipped:
                        m.return_value.run.assert_not_called()
                    else:
                        m.return_value.run.assert_called_once()
                # The skip must be RECORDED, not merely not-run — otherwise a
                # precondition miss is indistinguishable from a step that ran
                # and found nothing, which is what result_status exists to
                # prevent. Asserting the reason is present, not just the key.
                for key in skipped:
                    assert key in result.skipped_steps, f"{key} skipped without a record"
                    assert result.skipped_steps[key], f"{key} skipped without a reason"
                mock_log.assert_called_once()
        finally:
            for p in patchers:
                p.stop()

    def test_updates_last_surveyed_at(self, registry, project):
        _mocks, patchers = _patch_all_surveyors()
        try:
            with patch("resource_explorer.surveyors.survey_orchestrator.log_survey"):
                assert registry.get(project).last_surveyed_at == ""
                SurveyOrchestrator(registry).run(project)
                assert registry.get(project).last_surveyed_at != ""
        finally:
            for p in patchers:
                p.stop()


class TestStepsFiltered:
    def test_runs_only_named_step_and_does_not_self_log(self, registry, project):
        mocks, patchers = _patch_all_surveyors()
        try:
            with patch("resource_explorer.surveyors.survey_orchestrator.log_survey") as mock_log:
                SurveyOrchestrator(registry).run(project, steps=["repo_health"])
                mocks["repo_health"].return_value.run.assert_called_once()
                for key, m in mocks.items():
                    if key != "repo_health":
                        m.return_value.run.assert_not_called()
                mock_log.assert_not_called()
        finally:
            for p in patchers:
                p.stop()

    def test_multiple_steps_all_run(self, registry, project):
        mocks, patchers = _patch_all_surveyors()
        try:
            with patch("resource_explorer.surveyors.survey_orchestrator.log_survey"):
                SurveyOrchestrator(registry).run(
                    project, steps=["repo_language", "repo_file_classification", "repo_file_structure"]
                )
                mocks["repo_language"].return_value.run.assert_called_once()
                mocks["repo_file_classification"].return_value.run.assert_called_once()
                mocks["repo_file_structure"].return_value.run.assert_called_once()
                mocks["repo_health"].return_value.run.assert_not_called()
        finally:
            for p in patchers:
                p.stop()

    def test_unknown_step_key_is_silently_ignored(self, registry, project):
        mocks, patchers = _patch_all_surveyors()
        try:
            with patch("resource_explorer.surveyors.survey_orchestrator.log_survey"):
                result = SurveyOrchestrator(registry).run(project, steps=["not_a_real_step"])
                for m in mocks.values():
                    m.return_value.run.assert_not_called()
                assert result.annotations == []
        finally:
            for p in patchers:
                p.stop()

    def test_step_filtered_run_also_updates_last_surveyed_at(self, registry, project):
        _mocks, patchers = _patch_all_surveyors()
        try:
            with patch("resource_explorer.surveyors.survey_orchestrator.log_survey"):
                assert registry.get(project).last_surveyed_at == ""
                SurveyOrchestrator(registry).run(project, steps=["repo_health"])
                assert registry.get(project).last_surveyed_at != ""
        finally:
            for p in patchers:
                p.stop()


class TestScopeLocator:
    """D5/D6 repo scope-narrowing funnel plan — scope_locator is only
    forwarded to surveyors whose StepInfo.accepts_scope_locator is True;
    every other step is constructed exactly as before (no scope_locator
    kwarg at all), regardless of what scope_locator was passed to run()."""

    def test_forwarded_only_to_accepting_step(self, registry, project):
        mocks, patchers = _patch_all_surveyors()
        try:
            with patch("resource_explorer.surveyors.survey_orchestrator.log_survey"):
                SurveyOrchestrator(registry).run(
                    project, steps=["repo_api_structure", "repo_health"], scope_locator="src",
                )
                # repo_api_structure accepts_scope_locator=True
                _, kwargs = mocks["repo_api_structure"].call_args
                assert kwargs["scope_locator"] == "src"
                # repo_health does not declare accepts_scope_locator — must
                # never receive the kwarg (its constructor doesn't accept it).
                _, kwargs = mocks["repo_health"].call_args
                assert "scope_locator" not in kwargs
        finally:
            for p in patchers:
                p.stop()

    def test_default_scope_locator_is_empty_string(self, registry, project):
        mocks, patchers = _patch_all_surveyors()
        try:
            with patch("resource_explorer.surveyors.survey_orchestrator.log_survey"):
                SurveyOrchestrator(registry).run(project, steps=["repo_api_structure"])
                _, kwargs = mocks["repo_api_structure"].call_args
                assert kwargs["scope_locator"] == ""
        finally:
            for p in patchers:
                p.stop()

    def test_all_four_corpus_shaped_steps_accept_scope_locator(self):
        for key in ("repo_file_size", "repo_api_structure", "repo_data_profiling", "repo_file_classification"):
            assert STEP_REGISTRY[key].accepts_scope_locator is True


class TestFastFlag:
    """A real, confirmed slowness fix: Coarse Scout could block 10+ minutes
    because HealthSurveyor's stats refresh made one GitHub API call per
    commit in the lookback window. fast=True is forwarded only to steps
    whose StepInfo.accepts_fast is True (repo_health today) — every other
    step is constructed exactly as before, no fast kwarg at all."""

    def test_forwarded_only_to_accepting_step(self, registry, project):
        mocks, patchers = _patch_all_surveyors()
        try:
            with patch("resource_explorer.surveyors.survey_orchestrator.log_survey"):
                SurveyOrchestrator(registry).run(
                    project, steps=["repo_health", "repo_language"], fast=True,
                )
                _, kwargs = mocks["repo_health"].call_args
                assert kwargs["fast"] is True
                # repo_language does not declare accepts_fast — must never
                # receive the kwarg (its constructor doesn't accept it).
                _, kwargs = mocks["repo_language"].call_args
                assert "fast" not in kwargs
        finally:
            for p in patchers:
                p.stop()

    def test_default_fast_is_false(self, registry, project):
        mocks, patchers = _patch_all_surveyors()
        try:
            with patch("resource_explorer.surveyors.survey_orchestrator.log_survey"):
                SurveyOrchestrator(registry).run(project, steps=["repo_health"])
                _, kwargs = mocks["repo_health"].call_args
                assert kwargs["fast"] is False
        finally:
            for p in patchers:
                p.stop()

    def test_which_steps_accept_fast(self):
        """fast skips StatsFetcher's per-commit diff-stats calls. It moved with
        the fetch itself: repo_git_statistics now does the refresh, repo_health
        keeps the flag because its scoring window is the same 90-day history."""
        accepting = {k for k, info in STEP_REGISTRY.items() if info.accepts_fast}
        assert accepting == {"repo_health", "repo_git_statistics"}


class TestCostTierFilter:
    """Step cost tiers plan (docs/step-cost-tiers-plan.md, D5) —
    max_fetch_cost/max_compute_cost narrow step_keys_to_run by ordinal
    position, same set-narrowing shape steps=[...] already has. Both None
    (the default) must run every step, unchanged."""

    def test_both_none_runs_all_21_steps(self, registry, project):
        mocks, patchers = _patch_all_surveyors()
        try:
            with patch("resource_explorer.surveyors.survey_orchestrator.log_survey"):
                result = SurveyOrchestrator(registry).run(project)
                skipped = _precondition_skipped(project, registry)
                for key, m in mocks.items():
                    if key in skipped:
                        m.return_value.run.assert_not_called()
                    else:
                        m.return_value.run.assert_called_once()
                assert set(result.skipped_steps) == skipped, (
                    "every precondition miss must be recorded, and nothing else")
        finally:
            for p in patchers:
                p.stop()

    def test_max_fetch_cost_none_excludes_every_download_and_api_heavy_step(self, registry, project):
        """Ceiling "none" must exclude the 4 zipball (download) steps and
        every step that makes its own API calls (repo_git_statistics,
        repo_sub_resource_survey) — anything above "none" on the fetch
        axis, regardless of compute_cost."""
        mocks, patchers = _patch_all_surveyors()
        try:
            with patch("resource_explorer.surveyors.survey_orchestrator.log_survey"):
                SurveyOrchestrator(registry).run(project, max_fetch_cost="none")
                excluded = {k for k, info in STEP_REGISTRY.items() if info.fetch_cost != "none"}
                assert excluded == {
                    # repo_classification (2026-08-23) — declared the dataclass
                    # default fetch_cost="none" while making a dozen GitHub calls
                    # per repo. The presentation session measured it at 3 repos in
                    # 10 minutes against 1 second for the other three Discovery
                    # steps over 60 repos, and this assertion is the record that
                    # a "none" ceiling now excludes it.
                    "repo_classification",
                    "repo_file_inventory", "repo_homepage", "repo_data_profiling",
                    "repo_symbol_extraction", "repo_rag_ingestion",
                    # repo_website_ingestion (2026-08-20) — fetches the project's
                    # external site over HTTP, so it is not zero-fetch.
                    "repo_website_ingestion",
                    "repo_git_statistics", "repo_sub_resource_survey",
                    # repo_cve_scan (2026-08-26) — one batched call to OSV.dev.
                    # No repo download and nothing fetched about the repo
                    # itself, but still not zero-fetch.
                    "repo_cve_scan",
                    # repo_cii_badge (2026-08-26) — one GET against
                    # bestpractices.dev. Same shape as repo_cve_scan: nothing
                    # about the repo is downloaded, and it is still not
                    # zero-fetch, so a "none" ceiling has to exclude it.
                    "repo_cii_badge",
                    # repo_manifest_parse (2026-08-23) — shares the zipball, but
                    # sharing an extraction does not make a step zero-fetch: the
                    # download still has to happen for it to run at all.
                    "repo_manifest_parse",
                    # repo_arch_lens (2026-08-25) — reads the project's own
                    # architecture document, up to MAX_DOC_FILES GitHub calls and
                    # often against a DIFFERENT repository. It needs no shared
                    # resource (requires_resources={}), which is exactly the trap
                    # repo_classification fell into: an empty resource
                    # declaration is not evidence a step is zero-fetch.
                    "repo_arch_lens",
                    # repo_arch_detect/repo_arch_coupling (Phase 1 plan §4.2) —
                    # a zipball and a real git clone respectively, both downloads.
                    "repo_arch_detect", "repo_arch_coupling",
                    # The four GAP analyses (2026-09-01). All fetch_cost=
                    # "download": each reads repository CONTENT, and they share
                    # the one extraction resolve_resources already dedupes, so
                    # they add no download beyond the zipball steps above.
                    # Listed rather than the assertion being widened — a step
                    # acquiring a fetch cost is a real change and this test
                    # exists to make it argued for.
                    "repo_secret_scan", "repo_telemetry_scan",
                    "repo_contribution_provenance", "repo_sla_content",
                }
                for key in excluded:
                    mocks[key].return_value.run.assert_not_called()
                for key, m in mocks.items():
                    if key not in excluded:
                        m.return_value.run.assert_called_once()
        finally:
            for p in patchers:
                p.stop()

    def test_max_compute_cost_low_excludes_medium_and_high_steps(self, registry, project):
        mocks, patchers = _patch_all_surveyors()
        try:
            with patch("resource_explorer.surveyors.survey_orchestrator.log_survey"):
                SurveyOrchestrator(registry).run(project, max_compute_cost="low")
                excluded = {k for k, info in STEP_REGISTRY.items() if info.compute_cost != "low"}
                assert excluded == {
                    "repo_symbol_extraction", "repo_rag_ingestion",
                    # repo_data_profiling was here until 2026-09-01, when it was
                    # re-declared low on measurement. Raw it shows 0.01s median
                    # over 85 runs — a number that cannot separate "cheap" from
                    # "never reached its input". Restricted to the 26
                    # INTERPRETABLE observations (non-zero annotations, no
                    # `unverified` outcome, so the run provably had work): median
                    # 39 annotations at 0.10s, p90 0.76s, max 1.41s — well inside
                    # low's 5s ceiling WITH proof it was working. Over-declaring
                    # had excluded it from the cheap tier it suits.
                    # repo_website_ingestion (2026-08-20) — embeds the project's
                    # external site. medium rather than rag_ingestion's high: one
                    # page, not a whole repo.
                    "repo_website_ingestion",
                    # repo_arch_coupling — classify_subtree/cohesion computation
                    # over the whole import graph. Raised medium -> HIGH on
                    # 2026-09-01: 32 runs give p90 132s against the 60s medium
                    # ceiling (max 600s) with median 0 socket connects, so it is
                    # real compute rather than network wait. Still excluded here
                    # either way; the tier changed, not the outcome.
                    "repo_arch_coupling",
                    # repo_manifest_parse is deliberately NOT here: it declares
                    # compute_cost="low" because it measures like
                    # repo_file_inventory (0.0-0.6s across 359-5,763 files), not
                    # like the medium steps. It was "medium" on a guess for a
                    # few hours; see its StepInfo comment for the numbers.
                    # Two of the four GAP analyses (2026-09-01), and only two:
                    # repo_secret_scan and repo_telemetry_scan declare
                    # compute_cost="medium" (regex scanning over file content),
                    # while repo_contribution_provenance and repo_sla_content
                    # are "low" — they read a handful of named documents. The
                    # asymmetry is the point: had all four been added here, the
                    # tiers would have been copied rather than measured, which
                    # is how repo_data_profiling sat at "medium" for months on
                    # a guess. Both are flagged VERIFY against
                    # step_cost_observer once they have real runs behind them.
                    "repo_secret_scan", "repo_telemetry_scan",
                }
                for key in excluded:
                    mocks[key].return_value.run.assert_not_called()
        finally:
            for p in patchers:
                p.stop()

    def test_both_ceilings_combine_as_an_intersection(self, registry, project):
        """max_fetch_cost and max_compute_cost each narrow independently —
        a step must clear both ceilings to run."""
        mocks, patchers = _patch_all_surveyors()
        try:
            with patch("resource_explorer.surveyors.survey_orchestrator.log_survey"):
                SurveyOrchestrator(registry).run(
                    project, max_fetch_cost="none", max_compute_cost="low",
                )
                mocks["repo_health"].return_value.run.assert_called_once()
                for key in (
                    "repo_git_statistics", "repo_rag_ingestion", "repo_data_profiling",
                    "repo_symbol_extraction", "repo_sub_resource_survey",
                ):
                    mocks[key].return_value.run.assert_not_called()
        finally:
            for p in patchers:
                p.stop()

    def test_cost_ceiling_combines_with_explicit_steps_as_an_intersection(self, registry, project):
        mocks, patchers = _patch_all_surveyors()
        try:
            with patch("resource_explorer.surveyors.survey_orchestrator.log_survey"):
                SurveyOrchestrator(registry).run(
                    project, steps=["repo_health", "repo_rag_ingestion"], max_fetch_cost="none",
                )
                mocks["repo_health"].return_value.run.assert_called_once()
                mocks["repo_rag_ingestion"].return_value.run.assert_not_called()
        finally:
            for p in patchers:
                p.stop()


class TestApiStructureNeedsSymbols:
    """`repo_api_structure` (api/low) declares `has_code_symbols`. Its producer downloads and extracts, so
    at this step's own tier the resolver proposes it and the step is skipped: a lone or scoped request never
    pulls a zipball, whatever ceiling the caller set, because a download the run has not acquired is its own
    consent reason."""

    @pytest.fixture
    def bare(self, registry):
        registry.add(Project(slug="bare", display_name="Bare",
                             github_url="https://github.com/test/bare", description=""))
        return "bare"

    @pytest.mark.parametrize("kwargs", [
        {}, {"scope_locator": "src"},
        {"scope_locator": "src", "max_fetch_cost": "download", "max_compute_cost": "medium"},
    ])
    def test_a_lone_or_scoped_request_is_skipped_with_a_proposal_and_downloads_nothing(
            self, registry, bare, kwargs):
        mocks, patchers = _patch_all_surveyors()
        try:
            with patch("resource_explorer.surveyors.survey_orchestrator.log_survey"):
                result = SurveyOrchestrator(registry).run(bare, steps=["repo_api_structure"], **kwargs)
            mocks["repo_symbol_extraction"].assert_not_called()
            mocks["repo_api_structure"].return_value.run.assert_not_called()
            assert "repo_api_structure" in result.skipped_steps
            assert "repo_symbol_extraction" in result.skipped_steps["repo_api_structure"]
            assert result.proposals["repo_api_structure"]["steps"] == ["repo_symbol_extraction"]
            assert not result.auto_ran_steps
            # The skip is a ROW, not an omission: a step_skipped annotation saying why, in plain words.
            skips = [a for a in result.annotations
                     if getattr(a, "check_name", "") == "step_skipped"]
            assert len(skips) == 1
            assert "repo_symbol_extraction" in skips[0].summary and "Skipped" in skips[0].summary
        finally:
            for p in patchers:
                p.stop()

    def test_with_symbols_present_extraction_is_not_run_and_the_step_runs(self, registry, project):
        mocks, patchers = _patch_all_surveyors()
        try:
            with patch("resource_explorer.surveyors.survey_orchestrator.log_survey"):
                result = SurveyOrchestrator(registry).run(project, steps=["repo_api_structure"])
            mocks["repo_symbol_extraction"].assert_not_called()
            mocks["repo_api_structure"].return_value.run.assert_called_once()
            assert "repo_api_structure" not in result.skipped_steps
        finally:
            for p in patchers:
                p.stop()
