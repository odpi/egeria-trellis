"""Tests for the general repo-discovery routes ("Discover repos to scout"
plan): GET /api/discovery/foundations, POST /api/discovery/search,
POST /api/discovery/import, POST /api/discovery/disposition.

Supersedes the retired GET /api/projects/discover/{org} /
POST /api/projects/discover/{org}/import (org-only discovery, folded into
the general search)."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from github import GithubException

from resource_explorer.registry import Project, ProjectRegistry


@pytest.fixture(autouse=True)
def _clear_in_flight():
    from resource_explorer.github import org_importer
    org_importer._IN_FLIGHT.clear()
    yield
    org_importer._IN_FLIGHT.clear()


@pytest.fixture
def registry(tmp_path):
    r = ProjectRegistry(db_path=str(tmp_path / "test.db"))
    r.add(Project(
        slug="already_here",
        display_name="Already Here",
        github_url="https://github.com/my-org/already-here",
    ))
    return r


@pytest.fixture
def client(registry, monkeypatch):
    monkeypatch.setattr(
        "resource_explorer.registry.ProjectRegistry.__init__",
        lambda self, db_path=None: setattr(self, "__dict__", registry.__dict__) or None,
    )
    from resource_explorer.web.app import app
    return TestClient(app)


_FAKE_REPOS = [
    {"full_name": "my-org/already-here", "html_url": "https://github.com/my-org/already-here",
     "description": "", "stars": 10, "language": "Python", "archived": False, "fork": False,
     "updated_at": "", "license": "mit", "forks": 2},
    {"full_name": "my-org/new-repo", "html_url": "https://github.com/my-org/new-repo",
     "description": "brand new", "stars": 3, "language": "Go", "archived": False, "fork": False,
     "updated_at": "", "license": "apache-2.0", "forks": 0},
]


class TestFoundationsRoute:
    def test_returns_curated_list(self, client):
        resp = client.get("/api/discovery/foundations")
        assert resp.status_code == 200
        data = resp.json()
        assert "cncf" in data
        assert data["cncf"]["org"] == "cncf"

    def test_loaded_from_configdata_json_not_hardcoded(self, client):
        # Reads configdata/foundation_prefilters.json fresh on every
        # request — an edit to that file takes effect without a restart.
        from resource_explorer.web.routes.discovery import _FOUNDATION_PREFILTERS_PATH
        assert _FOUNDATION_PREFILTERS_PATH.name == "foundation_prefilters.json"
        assert _FOUNDATION_PREFILTERS_PATH.exists()

    def test_missing_file_returns_empty_dict_not_500(self, client, tmp_path, monkeypatch):
        from resource_explorer.web.routes import discovery
        monkeypatch.setattr(discovery, "_FOUNDATION_PREFILTERS_PATH", tmp_path / "nope.json")
        resp = client.get("/api/discovery/foundations")
        assert resp.status_code == 200
        assert resp.json() == {}


class TestQuickListSourcesRoute:
    def test_returns_curated_list(self, client):
        resp = client.get("/api/discovery/quick-list-sources")
        assert resp.status_code == 200
        data = resp.json()
        assert "lfai_landscape" in data
        assert data["lfai_landscape"]["display_name"] == "LF AI & Data"

    def test_cncf_landscape_deliberately_excluded(self, client):
        # cncf_landscape is a real fetch_kind but CNCF's search chip
        # (org:cncf) already covers it — a redundant quick-add would just
        # be confusing.
        data = client.get("/api/discovery/quick-list-sources").json()
        assert "cncf_landscape" not in data

    def test_missing_file_returns_empty_dict_not_500(self, client, tmp_path, monkeypatch):
        from resource_explorer.web.routes import discovery
        monkeypatch.setattr(discovery, "_QUICK_LIST_SOURCES_PATH", tmp_path / "nope.json")
        resp = client.get("/api/discovery/quick-list-sources")
        assert resp.status_code == 200
        assert resp.json() == {}


class TestSearchRoute:
    def test_excludes_archived_and_forks_by_default(self, client):
        with patch(
            "resource_explorer.github.client.GitHubClient.search_repos", return_value=_FAKE_REPOS,
        ) as mock_search:
            client.post("/api/discovery/search", json={"org": "my-org"})
        query = mock_search.call_args[0][0]
        assert "archived:false" in query
        assert "fork:false" in query

    def test_include_archived_and_forks_when_requested(self, client):
        with patch(
            "resource_explorer.github.client.GitHubClient.search_repos", return_value=_FAKE_REPOS,
        ) as mock_search:
            client.post("/api/discovery/search", json={
                "org": "my-org", "include_archived": True, "include_forks": True,
            })
        query = mock_search.call_args[0][0]
        assert "archived:false" not in query
        assert "fork:false" not in query

    def test_flags_already_registered_repos(self, client):
        with patch(
            "resource_explorer.github.client.GitHubClient.search_repos", return_value=_FAKE_REPOS,
        ):
            resp = client.post("/api/discovery/search", json={"org": "my-org"})

        assert resp.status_code == 200
        data = {r["full_name"]: r for r in resp.json()}
        assert data["my-org/already-here"]["already_registered"] is True
        assert data["my-org/new-repo"]["already_registered"] is False

    def test_result_includes_license_and_forks(self, client):
        with patch(
            "resource_explorer.github.client.GitHubClient.search_repos", return_value=_FAKE_REPOS,
        ):
            resp = client.post("/api/discovery/search", json={"org": "my-org"})

        data = {r["full_name"]: r for r in resp.json()}
        assert data["my-org/already-here"]["license"] == "mit"
        assert data["my-org/already-here"]["forks"] == 2

    def test_empty_request_returns_400(self, client):
        resp = client.post("/api/discovery/search", json={})
        assert resp.status_code == 400

    def test_rate_limit_error_returns_502(self, client):
        exc = GithubException(403, {"message": "rate limit exceeded"}, None)
        with patch("resource_explorer.github.client.GitHubClient.search_repos", side_effect=exc):
            resp = client.post("/api/discovery/search", json={"org": "my-org"})

        assert resp.status_code == 502

    def test_result_surfaces_prior_disposition(self, client, registry):
        registry.set_disposition(
            "https://github.com/my-org/new-repo", "ignored", reason="too small",
        )
        with patch(
            "resource_explorer.github.client.GitHubClient.search_repos", return_value=_FAKE_REPOS,
        ):
            resp = client.post("/api/discovery/search", json={"org": "my-org"})

        data = {r["full_name"]: r for r in resp.json()}
        assert data["my-org/new-repo"]["disposition"] == "ignored"
        assert data["my-org/new-repo"]["disposition_reason"] == "too small"
        assert data["my-org/already-here"]["disposition"] == "undecided"


class TestImportRoute:
    def test_skips_already_registered_repos(self, client):
        with patch("threading.Thread") as MockThread:
            resp = client.post("/api/discovery/import", json={
                "repos": [
                    {"github_url": "https://github.com/my-org/already-here", "display_name": "Already Here"},
                    {"github_url": "https://github.com/my-org/new-repo", "display_name": "New Repo"},
                ],
                "group_slug": "",
                "source_label": "my-org",
            })

        assert resp.status_code == 200
        data = resp.json()
        assert data["queued"] == 1
        assert data["skipped"] == ["https://github.com/my-org/already-here"]
        MockThread.assert_called_once()

    def test_unknown_group_slug_returns_404(self, client):
        resp = client.post("/api/discovery/import", json={
            "repos": [{"github_url": "https://github.com/my-org/new-repo", "display_name": "New Repo"}],
            "group_slug": "no-such-group",
        })
        assert resp.status_code == 404

    def test_no_new_repos_starts_no_thread(self, client):
        with patch("threading.Thread") as MockThread:
            resp = client.post("/api/discovery/import", json={
                "repos": [{"github_url": "https://github.com/my-org/already-here", "display_name": "Already Here"}],
                "group_slug": "",
            })

        assert resp.status_code == 200
        assert resp.json()["queued"] == 0
        MockThread.assert_not_called()


class TestDispositionRoute:
    def test_sets_and_round_trips_via_search(self, client, registry):
        resp = client.post("/api/discovery/disposition", json={
            "github_url": "https://github.com/my-org/new-repo",
            "disposition": "tracking",
            "reason": "watching for now",
        })
        assert resp.status_code == 200
        assert resp.json()["disposition"] == "tracking"

        disp = registry.get_disposition("https://github.com/my-org/new-repo")
        assert disp["disposition"] == "tracking"
        assert disp["reason"] == "watching for now"

    def test_invalid_disposition_returns_400(self, client):
        resp = client.post("/api/discovery/disposition", json={
            "github_url": "https://github.com/my-org/new-repo",
            "disposition": "not-a-real-state",
        })
        assert resp.status_code == 400

    def test_recommended_is_a_valid_disposition(self, client, registry):
        """The positive terminal state, added alongside abandoned/ignored
        (the negative terminal states) — 'decided for it', not just 'decided
        against it' or 'not decided yet'."""
        resp = client.post("/api/discovery/disposition", json={
            "github_url": "https://github.com/my-org/new-repo",
            "disposition": "recommended",
        })
        assert resp.status_code == 200
        assert resp.json()["disposition"] == "recommended"
        disp = registry.get_disposition("https://github.com/my-org/new-repo")
        assert disp["disposition"] == "recommended"

    def test_using_is_a_valid_disposition(self, client, registry):
        """A further, stronger positive terminal state than 'recommended' —
        the org is already actively using the resource, or knows of its use
        elsewhere in the org."""
        resp = client.post("/api/discovery/disposition", json={
            "github_url": "https://github.com/my-org/new-repo",
            "disposition": "using",
        })
        assert resp.status_code == 200
        assert resp.json()["disposition"] == "using"
        disp = registry.get_disposition("https://github.com/my-org/new-repo")
        assert disp["disposition"] == "using"

    def test_records_project_slug_when_already_registered(self, client, registry):
        resp = client.post("/api/discovery/disposition", json={
            "github_url": "https://github.com/my-org/already-here",
            "disposition": "investigating",
        })
        assert resp.status_code == 200
        disp = registry.get_disposition("https://github.com/my-org/already-here")
        assert disp["project_slug"] == "already_here"


class TestDiscoverySourcesRoutes:
    def test_create_list_delete_round_trip(self, client):
        resp = client.post("/api/discovery/sources", json={
            "slug": "cncf-data", "display_name": "CNCF Data Tools",
            "source_type": "search", "config": {"org": "cncf", "topic": "data"},
        })
        assert resp.status_code == 200
        assert resp.json()["slug"] == "cncf_data"

        resp = client.get("/api/discovery/sources")
        assert [s["slug"] for s in resp.json()] == ["cncf_data"]

        resp = client.delete("/api/discovery/sources/cncf-data")
        assert resp.status_code == 200
        assert client.get("/api/discovery/sources").json() == []

    def test_delete_unknown_source_404s(self, client):
        resp = client.delete("/api/discovery/sources/nope")
        assert resp.status_code == 404

    def test_invalid_source_type_400s(self, client):
        resp = client.post("/api/discovery/sources", json={
            "slug": "bad", "display_name": "Bad", "source_type": "not-a-type", "config": {},
        })
        assert resp.status_code == 400

    def test_list_source_requires_at_least_one_url(self, client):
        resp = client.post("/api/discovery/sources", json={
            "slug": "empty-list", "display_name": "Empty", "source_type": "list", "config": {},
        })
        assert resp.status_code == 400

    def test_list_source_with_fetch_kind_allows_zero_urls(self, client):
        # A fetch_kind-backed source can start empty — refresh-apply is
        # what's meant to populate it, so requiring a seed URL first would
        # defeat a one-click "add this foundation" flow (e.g. LF AI & Data's
        # landscape.yml — there's no natural single seed URL to require).
        resp = client.post("/api/discovery/sources", json={
            "slug": "lfai-quick", "display_name": "LF AI & Data",
            "source_type": "list", "config": {"fetch_kind": "lfai_landscape"},
        })
        assert resp.status_code == 200
        assert resp.json()["config"]["urls"] == []

    def test_run_search_source_dispatches_to_search_repos(self, client):
        client.post("/api/discovery/sources", json={
            "slug": "cncf-data", "display_name": "CNCF Data Tools",
            "source_type": "search", "config": {"org": "cncf"},
        })
        with patch(
            "resource_explorer.github.client.GitHubClient.search_repos", return_value=_FAKE_REPOS,
        ) as mock_search:
            resp = client.post("/api/discovery/sources/cncf-data/run")
        assert resp.status_code == 200
        assert len(resp.json()) == 2
        args, kwargs = mock_search.call_args
        assert "org:cncf" in args[0]

    def test_run_list_source_returns_urls_enriched(self, client):
        client.post("/api/discovery/sources", json={
            "slug": "enterprise", "display_name": "My Enterprise Repos",
            "source_type": "list", "config": {"urls": ["https://github.com/acme/foo"]},
        })
        fake_repo = MagicMock(
            full_name="acme/foo", html_url="https://github.com/acme/foo", description="",
            stargazers_count=0, language="Python", archived=False, fork=False, updated_at=None,
            license=None, forks_count=0,
        )
        with patch(
            "resource_explorer.github.client.GitHubClient.get_repo", return_value=fake_repo,
        ):
            resp = client.post("/api/discovery/sources/enterprise/run")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["full_name"] == "acme/foo"

    def test_run_list_source_tolerates_a_failing_url(self, client):
        client.post("/api/discovery/sources", json={
            "slug": "enterprise", "display_name": "My Enterprise Repos",
            "source_type": "list", "config": {"urls": ["https://github.com/acme/good", "https://github.com/acme/bad"]},
        })
        good_repo = MagicMock(
            full_name="acme/good", html_url="https://github.com/acme/good", description="",
            stargazers_count=0, language="Python", archived=False, fork=False, updated_at=None,
            license=None, forks_count=0,
        )

        def _get_repo(self, url):
            if "bad" in url:
                raise RuntimeError("404")
            return good_repo

        with patch("resource_explorer.github.client.GitHubClient.get_repo", _get_repo):
            resp = client.post("/api/discovery/sources/enterprise/run")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["full_name"] == "acme/good"

    def test_run_unknown_source_404s(self, client):
        resp = client.post("/api/discovery/sources/nope/run")
        assert resp.status_code == 404


class TestDiscoverySourceRefreshRoutes:
    def test_preview_diffs_against_current_urls(self, client):
        client.post("/api/discovery/sources", json={
            "slug": "cncf-list", "display_name": "CNCF List",
            "source_type": "list",
            "config": {"urls": ["https://github.com/cncf/stale"], "fetch_kind": "cncf_landscape"},
        })
        with patch(
            "resource_explorer.github.source_fetchers.fetch_source_urls",
            return_value=["https://github.com/cncf/fresh"],
        ):
            resp = client.post("/api/discovery/sources/cncf-list/refresh")
        assert resp.status_code == 200
        data = resp.json()
        assert data["added"] == ["https://github.com/cncf/fresh"]
        assert data["removed"] == ["https://github.com/cncf/stale"]
        assert data["fetched_count"] == 1
        assert data["current_count"] == 1

        # Preview must not have persisted anything.
        source = client.get("/api/discovery/sources").json()[0]
        assert source["config"]["urls"] == ["https://github.com/cncf/stale"]

    def test_apply_persists_the_fetched_urls(self, client):
        client.post("/api/discovery/sources", json={
            "slug": "cncf-list", "display_name": "CNCF List",
            "source_type": "list",
            "config": {"urls": ["https://github.com/cncf/stale"], "fetch_kind": "cncf_landscape"},
        })
        with patch(
            "resource_explorer.github.source_fetchers.fetch_source_urls",
            return_value=["https://github.com/cncf/fresh"],
        ):
            resp = client.post("/api/discovery/sources/cncf-list/refresh-apply")
        assert resp.status_code == 200
        assert resp.json()["config"]["urls"] == ["https://github.com/cncf/fresh"]

        source = client.get("/api/discovery/sources").json()[0]
        assert source["config"]["urls"] == ["https://github.com/cncf/fresh"]

    def test_refresh_on_search_source_400s(self, client):
        client.post("/api/discovery/sources", json={
            "slug": "cncf-search", "display_name": "CNCF Search",
            "source_type": "search", "config": {"org": "cncf"},
        })
        resp = client.post("/api/discovery/sources/cncf-search/refresh")
        assert resp.status_code == 400

    def test_refresh_without_fetch_kind_400s(self, client):
        client.post("/api/discovery/sources", json={
            "slug": "plain-list", "display_name": "Plain List",
            "source_type": "list", "config": {"urls": ["https://github.com/acme/one"]},
        })
        resp = client.post("/api/discovery/sources/plain-list/refresh")
        assert resp.status_code == 400

    def test_refresh_unknown_source_404s(self, client):
        resp = client.post("/api/discovery/sources/nope/refresh")
        assert resp.status_code == 404

    def test_unimplemented_fetcher_returns_501(self, client):
        client.post("/api/discovery/sources", json={
            "slug": "lfx-list", "display_name": "LFX List",
            "source_type": "list",
            "config": {"urls": ["https://github.com/lf/one"], "fetch_kind": "lfx_insights"},
        })
        resp = client.post("/api/discovery/sources/lfx-list/refresh")
        assert resp.status_code == 501

    def test_fetch_failure_returns_502(self, client):
        client.post("/api/discovery/sources", json={
            "slug": "cncf-list", "display_name": "CNCF List",
            "source_type": "list",
            "config": {"urls": ["https://github.com/cncf/existing"], "fetch_kind": "cncf_landscape"},
        })
        with patch(
            "resource_explorer.github.source_fetchers.fetch_source_urls",
            side_effect=RuntimeError("network down"),
        ):
            resp = client.post("/api/discovery/sources/cncf-list/refresh")
        assert resp.status_code == 502


class TestWorkingSetRoute:
    def test_round_trip(self, client, registry):
        resp = client.post("/api/discovery/working-set", json={
            "entity_type": "repo", "entity_slug": "already_here", "hidden": True,
        })
        assert resp.status_code == 200
        assert registry.is_working_set_hidden("repo", "already_here") is True

        client.post("/api/discovery/working-set", json={
            "entity_type": "repo", "entity_slug": "already_here", "hidden": False,
        })
        assert registry.is_working_set_hidden("repo", "already_here") is False


class TestDispositionHistoryRoute:
    def test_returns_history_oldest_first(self, client, registry):
        registry.set_disposition(
            "https://github.com/my-org/new-repo", "investigating", decided_by="", reason="",
        )
        registry.set_disposition(
            "https://github.com/my-org/new-repo", "abandoned", reason="not maintained",
        )
        resp = client.get("/api/discovery/disposition-history", params={"github_url": "https://github.com/my-org/new-repo"})
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 2
        assert data[0]["disposition"] == "investigating"
        assert data[1]["disposition"] == "abandoned"
        assert data[1]["reason"] == "not maintained"

    def test_never_decided_returns_empty_list(self, client):
        resp = client.get("/api/discovery/disposition-history", params={"github_url": "https://github.com/never/decided"})
        assert resp.status_code == 200
        assert resp.json() == []
