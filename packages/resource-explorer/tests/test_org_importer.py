"""Tests for OrgImporter (catalog-only registration) and _run_import_batch
(background batch runner) — resource_explorer/github/org_importer.py."""
from __future__ import annotations

from unittest.mock import patch

import pytest

from resource_explorer.github.org_importer import (
    OrgImporter,
    OrgImporterError,
    _run_import_batch,
    _url_to_slug,
)
from resource_explorer.registry import ProjectRegistry


@pytest.fixture
def registry(tmp_path):
    return ProjectRegistry(db_path=str(tmp_path / "test.db"))


class TestUrlToSlug:
    def test_derives_repo_name_not_owner_repo(self):
        assert _url_to_slug("https://github.com/my-org/My-Repo") == "my_repo"

    def test_strips_trailing_slash(self):
        assert _url_to_slug("https://github.com/my-org/my-repo/") == "my_repo"


class TestOrgImporterImportRepo:
    def test_registers_project_with_no_ingestion(self, registry):
        with patch("resource_explorer.github.stats_fetcher.StatsFetcher") as MockStats:
            MockStats.return_value.fetch.return_value = {}
            project = OrgImporter(registry).import_repo(
                "https://github.com/my-org/widgets", "widgets", "A widget repo", group_slug="myproducts",
            )

        assert project.slug == "widgets"
        assert project.github_url == "https://github.com/my-org/widgets"
        assert project.group_slug == "myproducts"
        assert project.collections == []  # never ingested

    def test_import_makes_no_stats_fetch(self, registry):
        """The GitHub stats fetch (~2 min per repo) moved to the first scouting survey."""
        from unittest.mock import create_autospec
        from resource_explorer.github.stats_fetcher import StatsFetcher
        fake = create_autospec(StatsFetcher, instance=True, spec_set=True)
        fake.fetch.side_effect = AssertionError("import must not fetch stats")
        with patch("resource_explorer.github.stats_fetcher.StatsFetcher", return_value=fake) as cls:
            OrgImporter(registry).import_repo("https://github.com/my-org/widgets", "widgets")
        cls.assert_not_called()
        fake.fetch.assert_not_called()
        assert registry.get("widgets") is not None
        assert registry.get_latest_project_stats("widgets") is None

    def test_already_registered_by_url_raises(self, registry):
        with patch("resource_explorer.github.stats_fetcher.StatsFetcher") as MockStats:
            MockStats.return_value.fetch.return_value = {}
            importer = OrgImporter(registry)
            importer.import_repo("https://github.com/my-org/widgets", "widgets")
            with pytest.raises(OrgImporterError):
                importer.import_repo("https://github.com/my-org/widgets", "widgets")


class TestRunImportBatch:
    def test_writes_one_scout_entry_per_repo_plus_summary(self, registry):
        repos = [
            {"github_url": "https://github.com/my-org/a", "display_name": "a", "description": ""},
            {"github_url": "https://github.com/my-org/b", "display_name": "b", "description": ""},
        ]
        with patch("resource_explorer.github.org_importer.ProjectRegistry", return_value=registry), \
             patch("resource_explorer.github.stats_fetcher.StatsFetcher") as MockStats:
            MockStats.return_value.fetch.return_value = {}
            _run_import_batch("my-org", repos, "")

        entries = registry.list_activity()
        assert len(entries) == 3  # 2 per-repo + 1 batch summary
        assert all(e["operation"] == "scout" for e in entries)
        statuses = [e["status"] for e in entries]
        assert statuses.count("ok") == 3
        assert registry.get("a") is not None
        assert registry.get("b") is not None

    def test_one_repo_failure_does_not_stop_the_batch(self, registry):
        repos = [
            {"github_url": "https://github.com/my-org/good", "display_name": "good", "description": ""},
            {"github_url": "https://github.com/my-org/bad", "display_name": "bad", "description": ""},
        ]

        real_import_repo = OrgImporter.import_repo

        def _flaky_import_repo(self, github_url, *args, **kwargs):
            if "bad" in github_url:
                raise RuntimeError("boom")
            return real_import_repo(self, github_url, *args, **kwargs)

        with patch("resource_explorer.github.org_importer.ProjectRegistry", return_value=registry), \
             patch("resource_explorer.github.stats_fetcher.StatsFetcher") as MockStats, \
             patch.object(OrgImporter, "import_repo", _flaky_import_repo):
            MockStats.return_value.fetch.return_value = {}
            _run_import_batch("my-org", repos, "")

        assert registry.get("good") is not None
        assert registry.get("bad") is None
        entries = registry.list_activity()
        statuses = [e["status"] for e in entries]
        assert statuses.count("error") == 2  # the failed repo + the batch summary (has a failure)
        assert statuses.count("ok") == 1     # the succeeded repo

    def test_group_slug_is_applied_to_each_imported_repo(self, registry):
        registry.create_group("myproducts", "My Products")
        repos = [{"github_url": "https://github.com/my-org/a", "display_name": "a", "description": ""}]
        with patch("resource_explorer.github.org_importer.ProjectRegistry", return_value=registry), \
             patch("resource_explorer.github.stats_fetcher.StatsFetcher") as MockStats:
            MockStats.return_value.fetch.return_value = {}
            _run_import_batch("my-org", repos, "myproducts")

        assert registry.get("a").group_slug == "myproducts"
