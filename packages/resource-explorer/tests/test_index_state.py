"""Indexer silent no-op and the "Not yet indexed" state (owner decision 2026-10-19).

Before this change `IncrementalIndexer.refresh` advanced `projects.last_commit_sha` for a project whose only
collection was a shared `web_docs_*` one, without indexing anything, so every later run said "No changes".
The surveyor counted that shared collection's chunks as the project's own. One predicate now answers
"does this project have its own index" for the indexer, the surveyor, the API and the UI.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, create_autospec, patch

import pytest

from resource_explorer.github.client import GitHubClient
from resource_explorer.index_state import (
    INDEXED, NOT_YET_INDEXED, has_own_index, index_state, own_collections, shared_collections)
from resource_explorer.ingestion.incremental import IncrementalIndexer, RefreshResult
from resource_explorer.registry import Project, ProjectRegistry
from resource_explorer.surveyors.sub_surveyors.rag_ingestion import RagIngestionSurveyor

NEXT = Path(__file__).resolve().parents[1] / "resource_explorer" / "web" / "static" / "next"
WEB_DOCS = "web_docs_egeria_project_org"


@pytest.fixture
def registry(tmp_path):
    return ProjectRegistry(db_path=str(tmp_path / "t.db"))


def _project(registry, slug, collections, sha="old111"):
    p = Project(slug=slug, display_name=slug, github_url=f"https://github.com/o/{slug}",
                collections=list(collections), last_commit_sha=sha)
    registry.add(p)
    registry.update_commit_sha(slug, sha)
    return registry.get(slug)


def _indexer(registry, latest="new222", changed=("src/main.py",)):
    """A real IncrementalIndexer whose GitHub client is an autospec of the real class (spec_set), so a
    call the real client does not have fails the test instead of passing silently."""
    client = create_autospec(GitHubClient, instance=True, spec_set=True)
    client.get_repo.return_value = MagicMock(name="repo")
    client.get_latest_commit_sha.return_value = latest
    indexer = IncrementalIndexer.__new__(IncrementalIndexer)
    indexer.registry = registry
    indexer.client = client
    indexer._get_changed_files = lambda repo, old, new: list(changed)
    return indexer


class TestPredicate:
    """Test 4. New module, so it fails on b3062ef7 (import error)."""

    def test_own_collection_counts(self):
        assert has_own_index("egeria_trellis", ["egeria_trellis_python_code"])

    def test_shared_web_docs_does_not_count(self):
        assert not has_own_index("egeria_trellis", [WEB_DOCS])

    def test_another_projects_collection_does_not_count(self):
        assert not has_own_index("egeria_trellis", ["other_python_code"])

    def test_unknown_type_does_not_count(self):
        assert not has_own_index("egeria_trellis", ["egeria_trellis_not_a_type"])

    def test_empty_and_none_mean_none(self):
        assert not has_own_index("egeria_trellis", [])
        assert not has_own_index("egeria_trellis", None)

    def test_split_between_own_and_shared(self):
        cols = ["egeria_trellis_python_code", WEB_DOCS]
        assert own_collections("egeria_trellis", cols) == ["egeria_trellis_python_code"]
        assert shared_collections("egeria_trellis", cols) == [WEB_DOCS]


class TestIndexerNoOp:
    def test_shared_only_project_keeps_its_sha_and_reports_not_yet_indexed(self, registry):
        """Test 1. Fails on b3062ef7: the SHA moves to new222 and refresh() returns None."""
        project = _project(registry, "egeria_trellis", [WEB_DOCS])
        result = _indexer(registry).refresh(project)
        assert result is RefreshResult.NOT_YET_INDEXED
        assert registry.get("egeria_trellis").last_commit_sha == "old111"

    def test_the_next_run_is_still_not_yet_indexed_not_no_changes(self, registry):
        project = _project(registry, "egeria_trellis", [WEB_DOCS])
        indexer = _indexer(registry)
        indexer.refresh(project)
        assert indexer.refresh(registry.get("egeria_trellis")) is RefreshResult.NOT_YET_INDEXED

    def test_own_index_with_unrelated_changes_advances_the_sha(self, registry):
        """Test 2. Pins existing behaviour (passes on b3062ef7 apart from the return value)."""
        project = _project(registry, "myproj", ["myproj_python_code"])
        indexer = _indexer(registry, changed=("assets/logo.png",))
        # release_notes is always affected but this project does not have that collection.
        result = indexer.refresh(project)
        assert result is RefreshResult.NOTHING_AFFECTED
        assert registry.get("myproj").last_commit_sha == "new222"

    def test_unchanged_sha_is_no_changes(self, registry):
        project = _project(registry, "myproj", ["myproj_python_code"], sha="same")
        indexer = _indexer(registry, latest="same")
        with patch.object(registry, "get_data_profiles", return_value=[{"x": 1}]):
            assert indexer.refresh(project) is RefreshResult.NO_CHANGES


class TestSurveyorSharedOnly:
    def test_shared_collection_is_not_counted_as_the_projects_own(self, registry):
        """Test 3. Fails on b3062ef7: 'N chunk(s) across 1 collection(s)', confidence 100."""
        project = _project(registry, "egeria_trellis", [WEB_DOCS])
        with patch("resource_explorer.vector_store_pg.MultiCollectionStore") as store, \
             patch("resource_explorer.ingestion.incremental.IncrementalIndexer.refresh",
                   return_value=RefreshResult.NOT_YET_INDEXED), \
             patch("resource_explorer.query_cache.QueryCache.invalidate_project"):
            store.return_value.count.return_value = 9000
            anns = RagIngestionSurveyor(project, registry).run()
        assert len(anns) == 1
        a = anns[0]
        assert a.summary.startswith("Not indexed for chat")
        assert "chunk" not in a.summary
        assert a.confidence != 100
        assert a.json_properties["outcome"] == "unverified"
        assert WEB_DOCS in a.explanation
        assert a.resource_properties["total_chunks"] == 0

    def test_own_collection_still_reports_its_chunks(self, registry):
        project = _project(registry, "myproj", ["myproj_python_code", WEB_DOCS])
        with patch("resource_explorer.vector_store_pg.MultiCollectionStore") as store, \
             patch("resource_explorer.ingestion.incremental.IncrementalIndexer.refresh"), \
             patch("resource_explorer.query_cache.QueryCache.invalidate_project"):
            store.return_value.count.side_effect = lambda c: {"myproj_python_code": 10, WEB_DOCS: 500}[c]
            a = RagIngestionSurveyor(project, registry).run()[0]
        assert a.resource_properties["total_chunks"] == 10


class TestApiPayload:
    """Test 5. Fails on b3062ef7: the payload has no index_state."""

    def test_summary_carries_index_state_from_the_helper(self, tmp_path, monkeypatch):
        monkeypatch.setenv("REGISTRY_DATABASE_URL", f"sqlite:///{tmp_path / 'api.db'}")
        from resource_explorer.web.routes.projects import _list_projects_sync, _to_summary
        reg = ProjectRegistry()
        a = _project(reg, "shared_only", [WEB_DOCS])
        b = _project(reg, "has_own", ["has_own_python_code"])
        assert _to_summary(a).index_state == NOT_YET_INDEXED == index_state(a)
        assert _to_summary(b).index_state == INDEXED == index_state(b)
        by_slug = {s.slug: s for s in _list_projects_sync(True, True)}
        assert by_slug["shared_only"].index_state == NOT_YET_INDEXED
        assert by_slug["has_own"].index_state == INDEXED
        assert by_slug["has_own"].last_indexed_at == b.last_indexed_at


class TestCallersKeepWorking:
    """Test 7. No caller read the old None, so none needed re-pinning; these pin the new return value."""

    def test_scheduler_ingest_branch_succeeds_for_an_indexed_repo(self, registry):
        from resource_explorer import scheduler
        project = _project(registry, "myproj", ["myproj_python_code"])
        with patch("resource_explorer.ingestion.incremental.IncrementalIndexer.refresh",
                   return_value=RefreshResult.INDEXED), \
             patch("resource_explorer.query_cache.QueryCache.invalidate_project"):
            _name, _url, errors = scheduler._run_repo_survey("myproj", "rag_ingestion", registry)
        assert errors == []

    def test_scheduler_ingest_branch_says_so_for_a_not_yet_indexed_repo(self, registry):
        from resource_explorer import scheduler
        _project(registry, "egeria_trellis", [WEB_DOCS])
        with patch("resource_explorer.ingestion.incremental.IncrementalIndexer.refresh",
                   return_value=RefreshResult.NOT_YET_INDEXED), \
             patch("resource_explorer.query_cache.QueryCache.invalidate_project"):
            _n, _u, errors = scheduler._run_repo_survey("egeria_trellis", "rag_ingestion", registry)
        assert errors and "Not yet indexed" in errors[0]

    def test_refresh_route_returns_the_result(self, tmp_path, monkeypatch):
        import asyncio
        monkeypatch.setenv("REGISTRY_DATABASE_URL", f"sqlite:///{tmp_path / 'route.db'}")
        reg = ProjectRegistry()
        _project(reg, "egeria_trellis", [WEB_DOCS])
        from resource_explorer.web.routes.projects import refresh_project
        with patch("resource_explorer.ingestion.incremental.IncrementalIndexer.refresh",
                   return_value=RefreshResult.NOT_YET_INDEXED), \
             patch("resource_explorer.query_cache.QueryCache.invalidate_project"):
            out = asyncio.run(refresh_project("egeria_trellis"))
        assert out.status == "ok" and out.result == "not_yet_indexed"


NODE = Path.home() / ".nvm/versions/node/v20.11.0/bin/node"


@pytest.mark.skipif(not (NODE.exists() or shutil.which("node")), reason="node not installed")
class TestCueHarness:
    """Test 6. Fails on b3062ef7: indexStateCueHtml does not exist."""

    def _render(self, tmp_path, states):
        node = str(NODE) if NODE.exists() else shutil.which("node")
        for name in ("glyphs.js", "link-actions.js", "format.js"):
            (tmp_path / name).write_text((NEXT / name).read_text(), encoding="utf-8")
        src = (NEXT / "stages" / "repo-header.js").read_text()
        src = (src.replace("'/static/next/glyphs.js'", "'./glyphs.mjs'")
                  .replace("'/static/next/link-actions.js'", "'./link-actions.mjs'")
                  .replace("'/static/next/format.js'", "'./format.mjs'"))
        (tmp_path / "repo-header.mjs").write_text(src, encoding="utf-8")
        for name in ("glyphs", "link-actions", "format"):
            body = (tmp_path / f"{name}.js").read_text().replace("'/static/next/", "'./").replace(".js'", ".mjs'")
            (tmp_path / f"{name}.mjs").write_text(body, encoding="utf-8")
        script = (
            "import { indexStateCueHtml } from './repo-header.mjs';\n"
            f"const states = {states!r};\n".replace("'", '"') +
            "console.log(JSON.stringify(states.map((s) => indexStateCueHtml(s === null ? null : { index_state: s }))));\n")
        (tmp_path / "run.mjs").write_text(script, encoding="utf-8")
        out = subprocess.run([node, str(tmp_path / "run.mjs")], capture_output=True, text=True, check=True)
        import json
        return json.loads(out.stdout)

    def test_cue_renders_for_not_yet_indexed_and_is_absent_for_indexed(self, tmp_path):
        not_yet, indexed, missing = self._render(tmp_path, ["not_yet_indexed", "indexed", "__none__"])
        assert 'data-index-state="not_yet_indexed"' in not_yet
        assert "not yet indexed" in not_yet and "can&#39;t search" in not_yet
        assert indexed == "" and missing == ""
