"""The 'uncatalogued' publish status (set by the resync heal pass for a repo
with no publish at all) must reach the cards through the PRODUCTION path:
ScoutingOverview and the analysis payloads read the `repo_publish` row, and the
front ends render "not catalogued, publish needed"."""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from resource_explorer.registry import Project, ProjectRegistry
from resource_explorer.workflows.analysis import (
    build_analysis_last_activity, build_survey_results,
)

STATIC = Path(__file__).parent.parent / "resource_explorer" / "web" / "static"


@pytest.fixture
def registry(tmp_path):
    r = ProjectRegistry(db_path=str(tmp_path / "test.db"))
    r.add(Project(slug="myproj", display_name="My Project",
                  github_url="https://github.com/test/myproj"))
    return r


@pytest.fixture
def client(registry, monkeypatch):
    monkeypatch.setattr(
        "resource_explorer.registry.ProjectRegistry.__init__",
        lambda self, db_path=None: setattr(self, "__dict__", registry.__dict__) or None,
    )
    from resource_explorer.web.app import app
    return TestClient(app)


STATUS = "uncatalogued"  # the red proof flips this to 'stale'


def _flag(registry):
    if STATUS == "uncatalogued":
        registry.mark_egeria_linkage_uncatalogued("repo_publish", "myproj")
    else:
        registry.mark_egeria_linkage_stale("repo_publish", "myproj", stale_guid="g")


def test_scouting_overview_returns_publish_uncatalogued(client, registry):
    _flag(registry)
    data = client.get("/api/projects/myproj/scouting-overview").json()
    assert data["publish_uncatalogued"] is True
    assert data["publish_stale"] is False


def test_analysis_payloads_return_publish_uncatalogued(registry):
    _flag(registry)
    last = build_analysis_last_activity(registry, "repo", "myproj")
    flags = [v["publish_uncatalogued"] for k, v in last.items()
             if not k.startswith("__") and "publish_uncatalogued" in v]
    assert flags and all(flags)
    results = build_survey_results(registry, "repo", "myproj", include_empty=True)
    cards = results["dashboards"]
    assert any(c.get("publish_uncatalogued") is True for c in cards)


def test_the_front_end_renders_the_wording_from_the_field():
    app_js = (STATIC / "next" / "app.js").read_text()
    assert "not published, publish needed" in app_js   # /next: Catalog is retired as on-screen wording (2026-10-10)
    assert "publish_uncatalogued" in app_js
