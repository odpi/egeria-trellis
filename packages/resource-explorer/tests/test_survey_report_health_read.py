"""PI-053: GET /api/egeria/{slug}/survey-report says whether its health figures were ever read, so the
Next report view can show "not read" instead of the zeros the model defaults to."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from resource_explorer.registry import Project, ProjectRegistry


@pytest.fixture
def registry(tmp_path):
    r = ProjectRegistry(db_path=str(tmp_path / "test.db"))
    r.add(Project(slug="myproj", display_name="My Project", github_url="https://github.com/test/myproj"))
    return r


@pytest.fixture
def client(registry, monkeypatch):
    monkeypatch.setattr(
        "resource_explorer.registry.ProjectRegistry.__init__",
        lambda self, db_path=None: setattr(self, "__dict__", registry.__dict__) or None,
    )
    from resource_explorer.web.app import app
    return TestClient(app)


def test_no_stats_row_is_not_read(client):
    data = client.get("/api/egeria/myproj/survey-report").json()
    assert data["health_read"] is False
    assert data["local_surveyed_at"] == ""


def test_a_stats_row_with_zero_stars_is_read(client, registry):
    with registry._conn() as conn:
        conn.execute(
            "INSERT INTO project_stats (project_slug, fetched_at, stars, forks) VALUES (?, ?, 0, 0)",
            ("myproj", "2026-08-01T00:00:00"))
    data = client.get("/api/egeria/myproj/survey-report").json()
    assert data["health_read"] is True
    assert data["health"]["stars"] == 0


def test_a_null_figure_is_null_not_zero(client, registry):
    """An ingestion-only stats row has no GitHub figures; they must reach the page as null."""
    with registry._conn() as conn:
        conn.execute("INSERT INTO project_stats (project_slug, fetched_at, ingestion_file_count) VALUES (?, ?, 12)",
                     ("myproj", "2026-08-01T00:00:00"))
    h = client.get("/api/egeria/myproj/survey-report").json()["health"]
    assert h["stars"] is None and h["forks"] is None and h["open_issues"] is None and h["contributors"] is None


def test_no_stats_row_gives_null_figures(client):
    h = client.get("/api/egeria/myproj/survey-report").json()["health"]
    assert h["stars"] is None and h["contributors"] is None


def test_contributors_reads_the_real_column(client, registry):
    with registry._conn() as conn:
        conn.execute("INSERT INTO project_stats (project_slug, fetched_at, stars, contributors_count) VALUES (?, ?, 1, 7)",
                     ("myproj", "2026-08-01T00:00:00"))
    assert client.get("/api/egeria/myproj/survey-report").json()["health"]["contributors"] == 7
