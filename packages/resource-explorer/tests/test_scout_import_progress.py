"""Scout batch import: visible in-progress rows, already-registered is a skip, no double press.

Owner report 2026-10-14: nothing showed in Activity until the batch finished, so the
same repos were pressed again and three were reported as failures.
"""
from __future__ import annotations

import time
from unittest.mock import create_autospec, patch

import pytest
from fastapi.testclient import TestClient

import resource_explorer.github.org_importer as oi
from resource_explorer.github.client import GitHubClient
from resource_explorer.github.stats_fetcher import StatsFetcher
from resource_explorer.registry import Project, ProjectRegistry

URLS = [f"https://github.com/DataKitchen/repo{i}" for i in range(3)]


def _repos(urls=URLS):
    return [{"github_url": u, "display_name": u.rsplit("/", 1)[1]} for u in urls]


@pytest.fixture(autouse=True)
def _clear_in_flight():
    oi._IN_FLIGHT.clear()
    yield
    oi._IN_FLIGHT.clear()


@pytest.fixture
def registry(tmp_path):
    return ProjectRegistry(db_path=str(tmp_path / "t.db"))


@pytest.fixture(autouse=True)
def _no_github():
    stats = create_autospec(StatsFetcher, instance=True, spec_set=True)
    stats.fetch.side_effect = AssertionError("import must not call StatsFetcher")
    with patch("resource_explorer.github.stats_fetcher.StatsFetcher", return_value=stats) as cls:
        yield
    cls.assert_not_called()


def _scout_rows(registry):
    return {r["entity_location"]: r for r in registry.list_activity(operation="scout")}


def _run(registry, repos):
    with patch.object(oi, "ProjectRegistry", lambda *a, **k: registry):
        batch = oi.begin_import_batch(registry, "search: x", repos)
        oi.claim_in_flight([r["github_url"] for r in repos])
        oi._run_import_batch("search: x", repos, "", batch)
    return batch


class TestRunningRowsExistBeforeTheWork:
    def test_batch_and_every_repo_are_running_inside_the_import_call(self, registry):
        seen = {}
        real = oi.OrgImporter.import_repo

        def spy(self, url, *a, **k):
            seen[url] = {loc: r["status"] for loc, r in _scout_rows(registry).items()}
            return real(self, url, *a, **k)

        with patch.object(oi.OrgImporter, "import_repo", spy):
            _run(registry, _repos())
        first = seen[URLS[0]]
        assert first[URLS[0]] == first[URLS[1]] == first[URLS[2]] == "running"
        assert first["search: x"] == "running"
        # and the repo that already finished is finished when the next one starts
        assert seen[URLS[1]][URLS[0]] == "ok"
        assert seen[URLS[1]][URLS[2]] == "running"

    def test_final_state_updates_the_same_rows(self, registry):
        batch = _run(registry, _repos())
        rows = registry.list_activity(operation="scout")
        assert len(rows) == 4
        assert registry.get_activity(batch.batch_id)["status"] == "ok"


class TestAlreadyRegisteredIsASkip:
    def test_only_skips_gives_ok_and_separate_counts(self, registry):
        for u in URLS:
            registry.add(Project(slug=oi._url_to_slug(u), display_name="x", github_url=u))
        batch = _run(registry, _repos())
        b = registry.get_activity(batch.batch_id)
        assert b["status"] == "ok"
        assert "0 imported, 3 already registered, 0 failed" in b["summary"]
        r = registry.get_activity(batch.repo_ids[URLS[0]])
        assert r["status"] == "ok" and r["summary"].startswith("Skipped")

    def test_real_failure_still_errors(self, registry):
        def boom(self, url, *a, **k):
            raise RuntimeError("404")
        with patch.object(oi.OrgImporter, "import_repo", boom):
            batch = _run(registry, _repos())
        b = registry.get_activity(batch.batch_id)
        assert b["status"] == "error"
        assert "0 imported, 0 already registered, 3 failed" in b["summary"]


class TestRouteAndStatus:
    @pytest.fixture
    def client(self, registry, monkeypatch):
        monkeypatch.setattr(
            "resource_explorer.registry.ProjectRegistry.__init__",
            lambda self, db_path=None: setattr(self, "__dict__", registry.__dict__) or None)
        from resource_explorer.web.app import app
        return TestClient(app)

    def test_rows_are_running_when_the_response_returns(self, client, registry):
        with patch("threading.Thread"):   # work never starts: only the press happened
            r = client.post("/api/discovery/import", json={"repos": _repos(), "source_label": "x"}).json()
        assert r["queued"] == 3 and r["batch_id"]
        st = client.get(f"/api/discovery/import/{r['batch_id']}").json()
        assert st["status"] == "running" and not st["done"]
        assert {v["state"] for v in st["repos"].values()} == {"importing"}

    def test_second_press_on_in_flight_repos_is_refused_visibly(self, client):
        with patch("threading.Thread"):
            client.post("/api/discovery/import", json={"repos": _repos(URLS[:3]), "source_label": "x"})
            r = client.post("/api/discovery/import", json={"repos": _repos(URLS[:3] + ["https://github.com/DataKitchen/new"]),
                                                           "source_label": "x"}).json()
        assert sorted(r["in_flight"]) == sorted(URLS[:3])
        assert r["queued"] == 1

    def test_status_after_finish_names_each_outcome(self, client, registry):
        registry.add(Project(slug="repo0", display_name="x", github_url=URLS[0]))
        batch = _run(registry, _repos())
        st = client.get(f"/api/discovery/import/{batch.batch_id}").json()
        assert st["done"] and st["status"] == "ok"
        assert st["repos"][URLS[0]]["state"] == "already_registered"
        assert st["repos"][URLS[1]]["state"] == "imported"

    def test_never_fetched_repo_is_not_yet_surveyed_not_zero(self, client, registry):
        registry.add(Project(slug="repo0", display_name="x", github_url=URLS[0]))
        ov = client.get("/api/projects/repo0/scouting-overview").json()
        assert ov["stats_fetched"] is False
        assert "stars" in ov["stats_unread"] and "forks" in ov["stats_unread"]

    def test_search_flags_registered_repos(self, client, registry):
        registry.add(Project(slug="repo0", display_name="x", github_url=URLS[0]))
        found = [{"full_name": f"DataKitchen/repo{i}", "html_url": u, "description": "", "stars": 1,
                  "language": "Python", "archived": False, "fork": False, "updated_at": "",
                  "license": "mit", "forks": 0} for i, u in enumerate(URLS)]
        fake = create_autospec(GitHubClient.search_repos, spec_set=True, return_value=found)
        with patch.object(GitHubClient, "search_repos", fake):
            out = client.post("/api/discovery/search", json={"keyword": "DataKitchen"}).json()
        assert {o["html_url"]: o["already_registered"] for o in out} == {URLS[0]: True, URLS[1]: False, URLS[2]: False}


class TestRowsAreReconcilable:
    def test_dead_owner_resolves_batch_and_repo_rows(self, registry):
        from unittest.mock import patch as _p
        from resource_explorer import run_reconciler as rr
        batch = oi.begin_import_batch(registry, "search: x", _repos())
        ids = [batch.batch_id, *batch.repo_ids.values()]
        assert all(registry.get_activity(i)["status"] == "running" for i in ids)
        with _p.object(rr, "_is_alive", return_value=False):
            out = rr.reconcile(registry)
        assert sorted(out["resolved_ids"]) == sorted(ids)
        assert all(registry.get_activity(i)["status"] != "running" for i in ids)

    def test_terminal_row_keeps_its_reason(self, registry):
        registry.add(Project(slug="repo0", display_name="x", github_url=URLS[0]))
        batch = _run(registry, _repos(URLS[:1]))
        r = registry.get_activity(batch.repo_ids[URLS[0]])
        assert "already registered" in r["detail"] and "_runner" not in r["detail"]
