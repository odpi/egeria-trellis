"""TR-106b: the survey-definitions read says when Resource Explorer's own walk last ran, so the LAST SURVEY tile can show it.
`newest_re_walk(registry, slug)` -> {"at", "kind": "re_walk", "capped"} or None (the key is then ABSENT from the response, never null or 0)."""
from __future__ import annotations

from resource_explorer.web.routes.survey_definitions import newest_re_walk
from tests.test_files2_reach_fork_folder_survey import _fs, registry  # noqa: F401 - fixture reused
from tests.test_files_review_defects_f39_w20 import _egeria_led_survey

OLD, NEW = "2026-11-01T10:00:00", "2026-11-19T07:30:00"
TOTALS = {"total_files": 86491, "total_directories": 2100, "total_data_files": 1889, "total_size_bytes": 8_400_000_000}


def _walk(registry, tmp_path, at, **over):
    if not registry.get_filesystem("lake"):
        _fs(registry, tmp_path)
    registry.add_filesystem_survey("lake", at, {**TOTALS, "surveyed_at": at, **over})


def test_no_walk_is_none(registry, tmp_path):
    _fs(registry, tmp_path)
    assert newest_re_walk(registry, "lake") is None


def test_the_newest_walk_is_reported(registry, tmp_path):
    _walk(registry, tmp_path, OLD)
    _walk(registry, tmp_path, NEW)
    assert newest_re_walk(registry, "lake") == {"at": NEW, "kind": "re_walk", "capped": False}


def test_a_capped_walk_says_capped(registry, tmp_path):
    _walk(registry, tmp_path, NEW, walk_limits={"capped": True, "capped_words": "capped at 100,000 entries"})
    assert newest_re_walk(registry, "lake")["capped"] is True


def test_a_walk_beside_egeria_and_egerias_own_survey_are_not_re_walks(registry, tmp_path):
    _egeria_led_survey(registry, tmp_path)
    registry.add_filesystem_survey("lake", NEW, {**TOTALS, "surveyed_at": NEW}, source="walk-extensions")
    assert newest_re_walk(registry, "lake") is None


def test_a_row_with_no_totals_is_not_a_walk(registry, tmp_path):
    _fs(registry, tmp_path)
    registry.add_filesystem_survey("lake", NEW, {"surveyed_at": NEW})
    assert newest_re_walk(registry, "lake") is None


# ── the route: the walk rides beside the candidates, and the key is absent when there is no walk ──────────────────────
import pytest  # noqa: E402
from unittest.mock import patch  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

_R = "resource_explorer.surveyors.survey_definition_reader.SurveyDefinitionReader."


@pytest.fixture
def client(registry, monkeypatch):
    monkeypatch.setattr("resource_explorer.registry.ProjectRegistry.__init__",
                        lambda self, db_path=None: setattr(self, "__dict__", registry.__dict__) or None)
    from tests.zone_fakes import open_access
    open_access(monkeypatch, caller_if_none="route-tester")
    from resource_explorer.web.app import app
    return TestClient(app)


def _get(client):
    with patch(_R + "find_unscoped_process_guids", return_value=[]), \
         patch(_R + "find_candidate_process_guids_by_questions", return_value=[]), \
         patch(_R + "find_candidate_process_guids", return_value=[]):
        resp = client.get("/api/survey-definitions/filesystem/lake/candidates")
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_route_carries_the_walk_for_a_file_system(client, registry, tmp_path):
    _walk(registry, tmp_path, NEW)
    assert _get(client)["re_walk"] == {"at": NEW, "kind": "re_walk", "capped": False}


def test_route_has_no_re_walk_key_without_a_walk(client, registry, tmp_path):
    _fs(registry, tmp_path)
    assert "re_walk" not in _get(client)
