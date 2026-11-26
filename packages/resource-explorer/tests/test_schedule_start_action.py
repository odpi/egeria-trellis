"""TR-95 fix-up: the schedule routes and the scheduled work-list batch ask for `start_action` too.

A schedule's target can be a Survey Definition, which can start engine actions. "Run now" ran it as the caller with
only a 404 check, and saving a schedule asked for `publish` only. Owner's ruling (2026-11-20): "Check start_action,
all types".
"""
from __future__ import annotations

import types
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from resource_explorer.web.app import app

SURVEY_ROW = {"analysis_id": "RepoFullSurvey", "target_kind": "survey", "schedule": "daily",
              "next_run": "2026-11-21T00:00:00"}
ANALYSIS_ROW = {"analysis_id": "repo_health", "target_kind": "analysis", "schedule": "daily",
                "next_run": "2026-11-21T00:00:00"}


@pytest.fixture
def client():
    return TestClient(app)


def _decider(monkeypatch, *, deny=()):
    asked = []

    def decide(reg, entity_type, slug, write, **kw):
        asked.append((entity_type, slug, write))
        if write in deny:
            return types.SimpleNamespace(allowed=False, reason=f"you may not {write} here")
        return types.SimpleNamespace(allowed=True, reason="")

    monkeypatch.setattr("resource_explorer.workflows.curate.resource_access", decide)
    return asked


def _reg(rows):
    reg = MagicMock()
    reg.get_schedules.return_value = rows
    return reg


@pytest.mark.parametrize("entity_type,row", [("repo", SURVEY_ROW), ("database", SURVEY_ROW),
                                             ("filesystem", SURVEY_ROW), ("repo", ANALYSIS_ROW)])
def test_run_now_is_403_without_start_action_and_runs_nothing(client, monkeypatch, entity_type, row):
    asked = _decider(monkeypatch, deny={"start_action"})
    with patch("resource_explorer.web.routes.schedules.ProjectRegistry", return_value=_reg([row])), \
         patch("resource_explorer.web.routes.schedules._require_resource"), \
         patch("resource_explorer.scheduler._execute") as ex:
        resp = client.post(f"/api/schedules/{entity_type}/s1/{row['analysis_id']}/run")
    assert resp.status_code == 403, resp.text
    assert not ex.called
    assert (entity_type, "s1", "start_action") in asked


def test_run_now_still_runs_for_a_permitted_caller(client, monkeypatch):
    _decider(monkeypatch)
    with patch("resource_explorer.web.routes.schedules.ProjectRegistry", return_value=_reg([SURVEY_ROW])), \
         patch("resource_explorer.web.routes.schedules._require_resource"), \
         patch("resource_explorer.scheduler._execute", return_value=("n", "l", [])) as ex:
        resp = client.post("/api/schedules/repo/s1/RepoFullSurvey/run")
    assert resp.status_code == 200, resp.text
    assert ex.called


@pytest.mark.parametrize("entity_type", ["repo", "database", "filesystem"])
def test_saving_a_survey_schedule_is_403_without_start_action(client, monkeypatch, entity_type):
    asked = _decider(monkeypatch, deny={"start_action"})
    reg = _reg([])
    with patch("resource_explorer.web.routes.schedules.ProjectRegistry", return_value=reg), \
         patch("resource_explorer.web.routes.schedules._require_resource"):
        resp = client.post(f"/api/schedules/{entity_type}/s1",
                           json={"analysis_id": "RepoFullSurvey", "schedule": "daily", "target_kind": "survey"})
    assert resp.status_code == 403, resp.text
    assert (entity_type, "s1", "start_action") in asked
    assert not reg.save_schedule.called


def test_saving_a_survey_schedule_still_works_when_permitted_and_publish_still_applies(client, monkeypatch):
    asked = _decider(monkeypatch)
    reg = _reg([])
    with patch("resource_explorer.web.routes.schedules.ProjectRegistry", return_value=reg), \
         patch("resource_explorer.web.routes.schedules._require_resource"):
        ok = client.post("/api/schedules/repo/s1",
                         json={"analysis_id": "RepoFullSurvey", "schedule": "daily", "target_kind": "survey"})
    assert ok.status_code == 200, ok.text
    assert [a[2] for a in asked] == ["start_action", "publish"]
    asked = _decider(monkeypatch, deny={"publish"})
    with patch("resource_explorer.web.routes.schedules.ProjectRegistry", return_value=_reg([])), \
         patch("resource_explorer.web.routes.schedules._require_resource"):
        no = client.post("/api/schedules/repo/s1",
                         json={"analysis_id": "RepoFullSurvey", "schedule": "daily", "target_kind": "survey"})
    assert no.status_code == 403


def test_saving_an_analysis_schedule_does_not_ask_for_start_action(client, monkeypatch):
    asked = _decider(monkeypatch)
    with patch("resource_explorer.web.routes.schedules.ProjectRegistry", return_value=_reg([])), \
         patch("resource_explorer.web.routes.schedules._require_resource"):
        resp = client.post("/api/schedules/repo/s1",
                           json={"analysis_id": "repo_health", "schedule": "daily", "target_kind": "analysis"})
    assert resp.status_code == 200, resp.text
    assert [a[2] for a in asked] == ["publish"]


# ── W1: a work-list schedule with no recorded creator follows the no-requester rule ──────────────────────

def test_a_batch_member_with_no_requester_and_no_zones_is_not_refused(monkeypatch):
    from resource_explorer.workflows import curate as wc
    from resource_explorer.workflows.egeria_work_access import definition_batch_refusal

    monkeypatch.setattr(wc, "_caller_identity", lambda: None)
    monkeypatch.setattr(wc, "zones_in_use", lambda *a, **k: [])
    reg = MagicMock()
    reg.has_assigned_egeria_project.return_value = False
    assert definition_batch_refusal(reg, "database", "db1") == ""


def test_a_batch_member_with_no_requester_and_zones_in_use_is_refused_in_words_that_say_why(monkeypatch):
    from resource_explorer.workflows import curate as wc
    from resource_explorer.workflows.egeria_work_access import definition_batch_refusal

    monkeypatch.setattr(wc, "_caller_identity", lambda: None)
    monkeypatch.setattr(wc, "zones_in_use", lambda *a, **k: ["zoneA"])
    reg = MagicMock()
    reg.has_assigned_egeria_project.return_value = False
    why = definition_batch_refusal(reg, "database", "db1")
    assert why.startswith("not permitted") and wc.NO_REQUESTER_WORDS in why and "zoneA" in why
    assert "sign in to curate" not in why
