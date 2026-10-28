"""A By-analysis board carries `last_run_at`: when ITS OWN analysis last ran.

`last_surveyed_at` on the same board is the whole resource's survey time. The
card header used it as the card's run time, so a card whose body said "Not run
yet." read "run 8d ago" beside it (laz_local_usda, activity_signals, which has
no step_runs row at all). The server now says separately what the analysis's
own run was ("" when it never ran), on the slow path and on the persisted fast
path alike.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from resource_explorer.registry import DatabaseEntity, ProjectRegistry
from resource_explorer.workflows import analysis as analysis_mod


@pytest.fixture
def registry(tmp_path, monkeypatch):
    for v in ("REGISTRY_DATABASE_URL", "METRICS_DATABASE_URL", "FEEDBACK_DATABASE_URL"):
        monkeypatch.setenv(v, f"sqlite:///{tmp_path}/{v}.db")
    r = ProjectRegistry(db_path=str(tmp_path / "test.db"))
    r.register_database(DatabaseEntity(
        slug="mydb", display_name="My DB", db_type="postgresql",
        host="localhost", port=5432, database_name="mydb",
    ))
    return r


def _wire(monkeypatch, reader_result):
    results_map = {"activity_signals": (MagicMock(return_value=reader_result), None)}
    monkeypatch.setattr(analysis_mod, "_results_map_for", lambda t: (results_map, {}))
    entry = {"id": "activity_signals", "name": "Activity Signals", "description": "",
             "intent": "discovery", "annotation_types": []}
    monkeypatch.setattr("resource_explorer.surveyors.analysis_catalog_reader.get_analyses",
                        lambda t, **kw: [entry])


def _board(registry):
    return analysis_mod.build_survey_results(
        registry, "database", "mydb", include_empty=True, board_id="activity_signals")["dashboards"][0]


def test_no_own_run_means_empty_last_run_at_even_though_the_survey_ran(registry, monkeypatch):
    _wire(monkeypatch, None)
    monkeypatch.setattr(registry, "get_analysis_last_run", lambda et, slug, **kw: {})
    for _ in range(2):   # slow path, then the persisted fast path
        board = _board(registry)
        assert board["has_results"] is False
        assert board["last_run_at"] == ""


def test_own_run_is_stamped_from_the_analysis_not_the_survey(registry, monkeypatch):
    _wire(monkeypatch, {"row_count": 5})
    monkeypatch.setattr(registry, "get_analysis_last_run",
                        lambda et, slug, **kw: {"activity_signals": {"last_run_at": "2026-10-10T09:00:00"}})
    for _ in range(2):
        board = _board(registry)
        assert board["has_results"] is True
        assert board["last_run_at"] == "2026-10-10T09:00:00"
