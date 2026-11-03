"""Tests for RagIngestionSurveyor — the step that refreshes pgvector.

RAG ingestion is the fourth and largest instance of the pattern
repo_symbol_extraction, repo_file_inventory and repo_git_statistics each fixed:
something every consumer depends on that no survey step wrote. It populates the
collections Chat and the query router read, and it ran at registration, on
webhook, from the scheduler and from a bespoke route branch — never as part of a
survey, so it had no freshness signal and no results.

Modelled on tests/test_git_statistics_surveyor.py, which has the same shape:
wrap an existing operation, report what is there afterwards, degrade to a
reported failure rather than raising.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from resource_explorer.registry import Project, ProjectRegistry
from resource_explorer.surveyors.sub_surveyors.rag_ingestion import (
    STEP,
    RagIngestionSurveyor,
)


@pytest.fixture
def registry(tmp_path):
    return ProjectRegistry(db_path=str(tmp_path / "t.db"))


@pytest.fixture
def project(registry):
    p = Project(slug="myproj", display_name="My Proj",
                github_url="https://github.com/o/myproj", description="",
                collections=["myproj_markdown_docs", "myproj_java_code"])
    registry.add(p)
    return p


def _counts(mapping):
    """Patch MultiCollectionStore so count() answers from a dict."""
    store = patch("resource_explorer.vector_store_pg.MultiCollectionStore")
    mock = store.start()
    mock.return_value.count.side_effect = lambda c: mapping[c]
    return store, mock


class TestRefresh:
    def test_refreshes_then_reports_live_counts(self, registry, project):
        stop, _mock = _counts({"myproj_markdown_docs": 120, "myproj_java_code": 80})
        try:
            with patch("resource_explorer.ingestion.incremental.IncrementalIndexer.refresh") as refresh, \
                 patch("resource_explorer.query_cache.QueryCache.invalidate_project") as invalidate:
                anns = RagIngestionSurveyor(project, registry).run()
        finally:
            stop.stop()

        refresh.assert_called_once_with(project)
        invalidate.assert_called_once_with("myproj")
        assert anns[0].analysis_step == STEP
        props = anns[0].resource_properties
        assert props["total_chunks"] == 200
        assert props["collections"] == 2
        assert props["myproj_markdown_docs"] == 120
        assert props["ingested"] is True
        assert anns[0].confidence == 100

    def test_invalidates_the_query_cache(self, registry, project):
        """Skipping this leaves chat answering from cache built against the
        pre-refresh index — the existing on-demand route does it too."""
        stop, _mock = _counts({"myproj_markdown_docs": 1, "myproj_java_code": 1})
        try:
            with patch("resource_explorer.ingestion.incremental.IncrementalIndexer.refresh"), \
                 patch("resource_explorer.query_cache.QueryCache.invalidate_project") as invalidate:
                RagIngestionSurveyor(project, registry).run()
        finally:
            stop.stop()
        invalidate.assert_called_once_with("myproj")

    def test_persists_a_metric_snapshot_for_the_trend(self, registry, project):
        stop, _mock = _counts({"myproj_markdown_docs": 10, "myproj_java_code": 5})
        try:
            with patch("resource_explorer.ingestion.incremental.IncrementalIndexer.refresh"), \
                 patch("resource_explorer.query_cache.QueryCache.invalidate_project"):
                RagIngestionSurveyor(project, registry, surveyed_at="2026-08-20T00:00:00").run()
        finally:
            stop.stop()

        m = registry.query_metrics("myproj", "rag_ingestion")
        assert m["total_chunks"] == 15
        assert m["collections"] == 2
        assert m["detail"]["by_collection"]["myproj_java_code"] == 5


class TestDegradation:
    def test_refresh_failure_reports_stored_counts_rather_than_raising(self, registry, project):
        """An embedding or GitHub hiccup must not fail the survey; what is
        already in pgvector is still what Chat can retrieve."""
        stop, _mock = _counts({"myproj_markdown_docs": 40, "myproj_java_code": 0})
        try:
            with patch("resource_explorer.ingestion.incremental.IncrementalIndexer.refresh",
                       side_effect=RuntimeError("embedding backend down")):
                anns = RagIngestionSurveyor(project, registry).run()
        finally:
            stop.stop()

        assert anns[0].resource_properties["total_chunks"] == 40
        assert anns[0].resource_properties["ingested"] is False
        assert anns[0].confidence == 50
        assert "embedding backend down" in anns[0].explanation

    def test_nothing_indexed_at_all_is_reported_not_raised(self, registry, project):
        stop, mock = _counts({})
        mock.return_value.count.side_effect = RuntimeError("no such collection")
        try:
            with patch("resource_explorer.ingestion.incremental.IncrementalIndexer.refresh",
                       side_effect=RuntimeError("boom")):
                anns = RagIngestionSurveyor(project, registry).run()
        finally:
            stop.stop()

        assert len(anns) == 1
        assert anns[0].confidence == 0
        assert anns[0].resource_properties["ingested"] is False


class TestOrdering:
    def test_a_repo_with_no_collections_is_reported_as_never_indexed(self, registry):
        bare = Project(slug="bare", display_name="Bare", github_url="https://github.com/o/bare",
                       description="", collections=[])
        registry.add(bare)
        stop, _mock = _counts({})
        try:
            with patch("resource_explorer.ingestion.incremental.IncrementalIndexer.refresh"), \
                 patch("resource_explorer.query_cache.QueryCache.invalidate_project"):
                anns = RagIngestionSurveyor(bare, registry).run()
        finally:
            stop.stop()

        assert len(anns) == 1
        assert anns[0].summary == "Not indexed for chat (no collections registered)"
        assert "indexed no chunks" not in anns[0].explanation
        assert anns[0].json_properties["outcome"] == "unverified"
        assert "never indexed" in anns[0].json_properties["outcome_cause"]

    def test_is_the_last_step_in_the_registry(self):
        """Nothing downstream reads pgvector, and this is the most expensive
        step in the set — STEP_REGISTRY order is also Full Survey order."""
        from resource_explorer.surveyors.repo_survey_definition_adapter import STEP_REGISTRY

        assert list(STEP_REGISTRY)[-1] == "repo_rag_ingestion"
