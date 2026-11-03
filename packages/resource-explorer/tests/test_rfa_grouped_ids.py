"""RFA dismiss, restore and note on a request that is one of several grouped under one annotation
(PI-112/113/114). The ids of those rows carry a third part, `{entry}::{annotation}::{sub}`; the routes
used to split on the last `::` only and so read the sub-index as the annotation index."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from resource_explorer.activity_logger import log_survey
from resource_explorer.registry import Project, ProjectRegistry

ITEMS = [
    {"summary": "No SECURITY.md found", "explanation": "e1", "action_requested": "add one", "action_target_name": "SECURITY.md"},
    {"summary": "No CI configuration detected", "explanation": "e2", "action_requested": "add CI", "action_target_name": "ci"},
    {"summary": "No license file", "explanation": "e3", "action_requested": "add one", "action_target_name": "LICENSE"},
]


@pytest.fixture
def registry(tmp_path):
    reg = ProjectRegistry(db_path=str(tmp_path / "t.db"))
    reg.add(Project(slug="p", display_name="P", github_url="u", description=""))
    return reg


@pytest.fixture
def client(registry, monkeypatch):
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).parent))
    from tests.zone_fakes import open_access

    from resource_explorer.web.app import app
    monkeypatch.setattr("resource_explorer.web.routes.activity._registry", lambda: registry)
    # Brief Z3: a status change checks its Egeria ToDo write; no zone in use, a signed-in caller.
    open_access(monkeypatch, caller_if_none="tester")
    return TestClient(app)


@pytest.fixture
def entry(registry):
    log_survey(
        registry=registry, entity_type="repo", entity_slug="p", entity_name="P", entity_location="",
        intent="scouting", status="success", summary="survey",
        annotations=[{"analysis_name": "SecurityHygieneCheck", "annotation_type": "RequestForAction",
                      "count": 3, "status": "local", "summary": ITEMS[0]["summary"], "items": ITEMS}],
    )
    return registry.list_activity(limit=1)[0]["id"]


def _rows(client):
    return {r["summary"]: r for r in client.get("/api/activity/rfas").json()}


def test_grouped_requests_have_three_part_ids(client, entry):
    ids = sorted(r["id"] for r in _rows(client).values())
    assert ids == [f"{entry}::0::0", f"{entry}::0::1", f"{entry}::0::2"]


def test_dismissing_one_grouped_request_suppresses_that_one_only(client, entry):
    r = client.post(f"/api/activity/rfas/{entry}::0::1/dismiss", json={"reason": "wont_do", "note": "not ours"})
    assert r.status_code == 200, r.text
    rows = _rows(client)
    assert rows["No CI configuration detected"]["dismissed"] is True
    assert rows["No CI configuration detected"]["dismissal"]["reason"] == "wont_do"
    assert rows["No SECURITY.md found"]["dismissed"] is False
    assert rows["No license file"]["dismissed"] is False


def test_restoring_brings_the_row_back_and_keeps_the_record(client, entry):
    did = client.post(f"/api/activity/rfas/{entry}::0::2/dismiss", json={"reason": "not_applicable"}).json()["dismissal"]["id"]
    assert client.post(f"/api/activity/rfas/dismissals/{did}/clear", json={}).status_code == 200
    assert _rows(client)["No license file"]["dismissed"] is False
    kept = client.get("/api/activity/rfas/dismissals", params={"include_cleared": True}).json()
    assert any(d["id"] == did and d["cleared_at"] for d in kept)


def test_a_note_on_a_grouped_request_is_saved_and_listed(client, entry):
    r = client.patch(f"/api/activity/rfas/{entry}::0::1/notes", json={"notes": "asked the maintainers"})
    assert r.status_code == 200, r.text
    rows = _rows(client)
    assert rows["No CI configuration detected"]["notes"] == "asked the maintainers"
    assert rows["No SECURITY.md found"]["notes"] == ""


def test_a_status_change_on_a_grouped_request_lands(client, entry):
    r = client.patch(f"/api/activity/rfas/{entry}::0::0", json={"status": "deferred", "defer_until": "next survey"})
    assert r.status_code == 200, r.text
    assert _rows(client)["No SECURITY.md found"]["rfa_status"] == "deferred"


@pytest.mark.parametrize("bad", ["nodelimiter", "a::b", "a::0::x", "a::0::1::2"])
def test_a_malformed_id_is_a_400(client, bad):
    assert client.post(f"/api/activity/rfas/{bad}/dismiss", json={"reason": "wont_do"}).status_code == 400


def test_who_dismissed_and_restored_comes_from_the_signed_in_user(client, entry, monkeypatch):
    monkeypatch.setattr("resource_explorer.web.routes.activity.get_current_user", lambda request: {"user_id": "dan"})
    d = client.post(f"/api/activity/rfas/{entry}::0::0/dismiss", json={"reason": "wont_do", "dismissed_by": "forged"}).json()["dismissal"]
    assert d["created_by"] == "dan"
    cleared = client.post(f"/api/activity/rfas/dismissals/{d['id']}/clear", json={"cleared_by": "forged"}).json()["dismissal"]
    assert cleared["cleared_by"] == "dan"
