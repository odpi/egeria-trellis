"""Tests for dashboard graph builders — graceful empty-data handling."""
from __future__ import annotations

import importlib.util
import json
import sqlite3

import pytest

from resource_explorer.registry import Project, ProjectRegistry

_plotly_available = pytest.mark.skipif(
    importlib.util.find_spec("plotly") is None, reason="plotly not installed"
)
_plotext_available = pytest.mark.skipif(
    importlib.util.find_spec("plotext") is None, reason="plotext not installed"
)


@pytest.fixture
def registry(tmp_path):
    r = ProjectRegistry(db_path=str(tmp_path / "test.db"))
    r.add(Project(slug="proj", display_name="Proj", github_url="https://github.com/a/b"))
    return r


def _insert_stats(db_path, fetched_at, stars, commits_30d=5, forks=10, lang_breakdown=None):
    conn = sqlite3.connect(db_path)
    conn.execute("""
        INSERT INTO project_stats
        (project_slug, fetched_at, stars, forks, commits_30d, language_breakdown,
         primary_language, contributors_count, releases_count)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, ("proj", fetched_at, stars, forks, commits_30d,
          json.dumps(lang_breakdown or {"Python": 10000}), "Python", 5, 3))
    conn.commit()
    conn.close()


@_plotext_available
class TestGraphsWithNoDataTerminal:
    def test_commits_terminal_no_data(self, registry, monkeypatch, capsys):
        from resource_explorer.dashboard import graphs
        monkeypatch.setattr(graphs, "_load_history", lambda slug, limit=12: [])
        graphs.commits_over_time_terminal("proj")
        captured = capsys.readouterr()
        assert "No stats data" in captured.out

    def test_stars_terminal_no_data(self, registry, monkeypatch, capsys):
        from resource_explorer.dashboard import graphs
        monkeypatch.setattr(graphs, "_load_history", lambda slug, limit=12: [])
        graphs.stars_over_time_terminal("proj")
        captured = capsys.readouterr()
        assert "No stats data" in captured.out


@_plotly_available
class TestGraphsWithNoData:
    def test_stars_plotly_returns_figure_with_empty_data(self, registry, monkeypatch):
        from resource_explorer.dashboard import graphs
        monkeypatch.setattr(graphs, "_load_history", lambda slug, limit=12: [])
        fig = graphs.stars_over_time_plotly("proj")
        data = json.loads(fig.to_json())
        assert data["data"][0]["x"] == []
        assert data["data"][0]["y"] == []

    def test_language_breakdown_plotly_empty(self, registry, monkeypatch):
        from resource_explorer.dashboard import graphs
        monkeypatch.setattr(graphs, "_latest_row", lambda slug: {})
        fig = graphs.language_breakdown_plotly("proj")
        data = json.loads(fig.to_json())
        assert data["data"][0]["labels"] == []

    def test_health_radar_plotly_no_data(self, registry, monkeypatch):
        from resource_explorer.dashboard import graphs
        monkeypatch.setattr(graphs, "_latest_row", lambda slug: {})
        fig = graphs.health_radar_plotly("proj")
        assert "No data" in fig.layout.title.text


@_plotly_available
class TestGraphsWithData:
    def test_stars_plotly_populated(self, registry, monkeypatch):
        from resource_explorer.dashboard import graphs
        rows = [
            {"fetched_at": "2024-01-01T00:00:00", "stars": 100, "commits_30d": 5},
            {"fetched_at": "2024-06-01T00:00:00", "stars": 500, "commits_30d": 20},
        ]
        monkeypatch.setattr(graphs, "_load_history", lambda slug, limit=12: rows)
        fig = graphs.stars_over_time_plotly("proj")
        data = json.loads(fig.to_json())
        assert data["data"][0]["y"] == [100, 500]

    def test_language_breakdown_plotly_populated(self, registry, monkeypatch):
        from resource_explorer.dashboard import graphs
        monkeypatch.setattr(graphs, "_latest_row", lambda slug: {
            "language_breakdown": json.dumps({"Python": 80000, "Shell": 5000})
        })
        fig = graphs.language_breakdown_plotly("proj")
        data = json.loads(fig.to_json())
        labels = data["data"][0]["labels"]
        assert any(l.startswith("Python") for l in labels)
        assert any(l.startswith("Shell") for l in labels)

    def test_language_breakdown_handles_str_dict_format(self, registry, monkeypatch):
        """StatsFetcher stores language_breakdown as str(dict) — test both formats."""
        from resource_explorer.dashboard import graphs
        monkeypatch.setattr(graphs, "_latest_row", lambda slug: {
            "language_breakdown": "{'Python': 80000, 'Shell': 5000}"
        })
        fig = graphs.language_breakdown_plotly("proj")
        data = json.loads(fig.to_json())
        assert any(l.startswith("Python") for l in data["data"][0]["labels"])

    def test_language_pie_labels_small_slices_and_legend_percentages(self, registry, monkeypatch):
        """walk3: a 0.0025% slice printed '0.00249%' outside the pie, over the title."""
        from resource_explorer.dashboard import graphs
        monkeypatch.setattr(graphs, "_latest_row", lambda slug: {
            "language_breakdown": json.dumps({
                "Python": 995000, "JavaScript": 2500, "Shell": 2475, "CSS": 25})
        })
        trace = json.loads(graphs.language_breakdown_plotly("proj").to_json())["data"][0]
        by_name = {l.split(" ")[0]: i for i, l in enumerate(trace["labels"])}
        # the legend carries every slice with its percentage
        assert trace["labels"][by_name["Python"]] == "Python 99.5%"
        assert trace["labels"][by_name["CSS"]] == "CSS <0.01%"
        assert all(l.rstrip().endswith("%") for l in trace["labels"])
        # no printed number runs past 3 significant figures
        for l in trace["labels"]:
            num = l.rsplit(" ", 1)[1].strip("<%")
            assert len(num.replace(".", "").lstrip("0")) <= 3, l
        # slices under 3% print nothing on the pie; larger ones sit inside, horizontal
        for name in ("JavaScript", "Shell", "CSS"):
            assert trace["text"][by_name[name]] == "", name
        assert trace["text"][by_name["Python"]] == "99.5%"
        assert trace["textinfo"] == "text"
        assert trace["textposition"] == "inside"
        assert trace["insidetextorientation"] == "horizontal"

    def test_health_radar_scores_capped_at_ten(self, registry, monkeypatch):
        from resource_explorer.dashboard import graphs
        monkeypatch.setattr(graphs, "_latest_row", lambda slug: {
            "commits_30d": 999, "contributors_count": 999,
            "stars": 999999, "releases_count": 999, "open_issues": 0,
        })
        fig = graphs.health_radar_plotly("proj")
        data = json.loads(fig.to_json())
        scores = data["data"][0]["r"]
        assert all(s <= 10 for s in scores)
