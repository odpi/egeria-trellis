"""F-AB-2: a run acquires its source tree once, and the step that fetched it says so.

Measured 2026-11-17 on a public repo: a 3-step Prefect-ordered run re-acquired
the zipball once per step (each planned step builds its own orchestrator), and
every `step_runs` row recorded bytes 0, api_calls 0 and `acquisition=not-consulted`
— the orchestrator resolves resources before any step's `observe` scope opens,
so the download was charged to no step at all.

Offline: GitHub is a fake that counts acquisitions and reports what a real one
would through the same accounting seams (`external_calls`, `acquisition`). The
registry is a temp SQLite file; Prefect runs in-process (conftest).
"""
from __future__ import annotations

import logging
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from resource_explorer.observability import acquisition, external_calls
from resource_explorer.registry import Project, ProjectRegistry
from resource_explorer.surveyors.repo_survey_definition_adapter import STEP_REGISTRY

#: Three steps that all read `zipball_root` and need no stored context to run.
STEPS = ["repo_symbol_extraction", "repo_manifest_parse", "repo_data_profiling"]
ZIP_BYTES = 45_000
SLUG = "fetchonce"


@pytest.fixture(autouse=True, scope="module")
def _quiet_prefect_logs():
    logging.disable(logging.INFO)
    yield
    logging.disable(logging.NOTSET)


@pytest.fixture
def registry(tmp_path, monkeypatch):
    db_path = str(tmp_path / "registry.db")
    # The flow's tasks build their own ProjectRegistry(): point it at this file, never the shared one.
    monkeypatch.setenv("REGISTRY_DATABASE_URL", f"sqlite:///{db_path}")
    reg = ProjectRegistry(db_path=db_path)
    reg.add(Project(slug=SLUG, display_name="Fetch Once",
                    github_url="https://github.com/test/fetchonce", description=""))
    return reg


@pytest.fixture
def fake_github(tmp_path):
    """A GitHubClient whose zipball acquisition is counted and costs what a real one reports:
    a repo lookup and a SHA lookup (two API calls), a cache miss, and the bytes that landed."""
    root = tmp_path / "tree"
    root.mkdir()
    counts = SimpleNamespace(fetches=0, repo_lookups=0)

    class FakeGitHubClient:
        def __init__(self, *a, **k):
            self.last_served_sha = None

        def get_repo(self, url):
            counts.repo_lookups += 1
            external_calls.record_call("api.github.com", byte_count=2_000)
            return SimpleNamespace(full_name="test/fetchonce")

        def zipball_root(self, repo, subproject_path=None):
            @contextmanager
            def _cm():
                counts.fetches += 1
                external_calls.record_call("api.github.com", byte_count=500)   # default-branch SHA
                self.last_served_sha = "abc123def456"
                acquisition.record_miss("zipball")
                external_calls.record_bytes(ZIP_BYTES)
                yield str(root)
            return _cm()

    with patch("resource_explorer.github.client.GitHubClient", FakeGitHubClient):
        yield counts


@pytest.fixture
def quiet_surveyors():
    """The three steps' surveyors do no work of their own: what is measured is the acquisition."""
    patchers = []
    for key in STEPS:
        mock_cls = MagicMock()
        mock_cls.return_value.run.return_value = []
        p = patch.object(STEP_REGISTRY[key], "surveyor_cls", mock_cls)
        p.start()
        patchers.append(p)
    yield
    for p in patchers:
        p.stop()


def _rows(registry, step_key):
    rows = registry.query_step_runs(slug=SLUG, step_key=step_key)
    assert rows, f"no step_runs row for {step_key}"
    return rows


def _assert_fetched(registry, step_key):
    for row in _rows(registry, step_key):
        m = row["metrics"]
        assert m["acquisition"] == "cold", (step_key, m)
        assert m["bytes_fetched"] >= ZIP_BYTES, (step_key, m)
        assert m["api_calls"] >= 2, (step_key, m)
        assert m["cache_misses"] >= 1, (step_key, m)


def _assert_reused(registry, step_key):
    for row in _rows(registry, step_key):
        m = row["metrics"]
        assert m["acquisition"] == "reused", (step_key, m)
        assert m["bytes_fetched"] == 0, (step_key, m)
        assert m["api_calls"] == 0, (step_key, m)
        assert m["cache_hits"] == 0 and m["cache_misses"] == 0, (step_key, m)


def _plan(linear: bool):
    return [{"step_key": k, "qualified_name": f"QN::{k}",
             "depends_on": [STEPS[i - 1]] if linear and i else [], "guarded_by": {}}
            for i, k in enumerate(STEPS)]


def test_a_three_step_flow_run_fetches_the_tree_once_and_the_rows_say_who_fetched(
        registry, fake_github, quiet_surveyors):
    from resource_explorer.prefect import flows

    report = flows.re_survey_definition_flow("repo", SLUG, _plan(linear=True), {}, surveyed_at="SNAP-F")

    assert [r["status"] for r in report] == ["ok", "ok", "ok"], report
    assert fake_github.fetches == 1
    assert fake_github.repo_lookups == 1
    _assert_fetched(registry, STEPS[0])
    for later in STEPS[1:]:
        _assert_reused(registry, later)


def test_concurrent_planned_steps_still_fetch_once(registry, fake_github, quiet_surveyors):
    """No dependencies between the steps, so Prefect may run them at once: one fetches, the others wait."""
    from resource_explorer.prefect import flows

    flows.re_survey_definition_flow("repo", SLUG, _plan(linear=False), {}, surveyed_at="SNAP-C")

    assert fake_github.fetches == 1
    states = {k: {r["metrics"]["acquisition"] for r in _rows(registry, k)} for k in STEPS}
    assert sorted(s for v in states.values() for s in v) == ["cold", "reused", "reused"], states


def test_one_orchestrator_run_charges_the_download_to_its_first_reader(
        registry, fake_github, quiet_surveyors):
    """The in-process path already shared the tree; its rows did not say where the download went."""
    from resource_explorer.surveyors.survey_orchestrator import SurveyOrchestrator

    result = SurveyOrchestrator(registry).run(SLUG, steps=list(STEPS))

    assert fake_github.fetches == 1
    assert result.survey_commit_sha == "abc123def456"
    _assert_fetched(registry, STEPS[0])
    for later in STEPS[1:]:
        _assert_reused(registry, later)
    assert _rows(registry, STEPS[0])[0]["metrics"]["acquisition_ms"] >= 0


def test_a_step_that_reads_no_resource_stays_not_consulted(registry, fake_github):
    from resource_explorer.surveyors.survey_orchestrator import SurveyOrchestrator

    key = next(k for k, i in STEP_REGISTRY.items()
               if not i.requires_resources and not getattr(i, "requires_context", None))
    mock_cls = MagicMock()
    mock_cls.return_value.run.return_value = []
    with patch.object(STEP_REGISTRY[key], "surveyor_cls", mock_cls):
        SurveyOrchestrator(registry).run(SLUG, steps=[key])

    assert fake_github.fetches == 0
    assert all(r["metrics"]["acquisition"] == "not-consulted" for r in _rows(registry, key))


def test_the_pool_acquires_once_and_does_not_keep_a_failure():
    from resource_explorer.surveyors import run_resource_pool

    entered = []

    @contextmanager
    def good():
        entered.append("good")
        yield "root"

    @contextmanager
    def bad():
        raise RuntimeError("download failed")
        yield  # pragma: no cover

    with run_resource_pool.run_resource_scope() as pool:
        with pytest.raises(RuntimeError):
            pool.get_or_acquire(("zipball_root", "r"), bad)
        with acquisition.acquisition_scope() as seen:
            assert pool.get_or_acquire(("zipball_root", "r"), good) == ("root", False)
            assert pool.get_or_acquire(("zipball_root", "r"), good) == ("root", True)
        assert seen.state == "reused" and seen.lookups == 0
        with run_resource_pool.run_resource_scope() as inner:
            assert inner is pool
    assert entered == ["good"]
    assert run_resource_pool.current() is None
