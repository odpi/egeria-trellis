"""An RFA that belongs to no resource can be dismissed, and the sweep raises at most one open stale-link RFA.

Owner's rehearsal walk, 2026-10-13: the drawer held 12 open RFAs, 9 with no resource and most of them the same
"N Egeria catalog link(s) are stale" text, because every recheck sweep that still saw the stale links raised
another; and Dismiss on any of them answered "entity_slug is required".

  A. A resource-less RFA is dismissed under an explicit system scope plus its producer and summary. The
     empty-slug guard stays (a dismissal with no subject would match everything).
  B. The sweep raises one open summary RFA, not one per sweep, and completes it (as the sweep) when a full,
     error-free sweep finds nothing stale.
"""
from __future__ import annotations

from unittest.mock import create_autospec, patch

import pytest
from fastapi.testclient import TestClient
from pyegeria.omvs.metadata_expert import MetadataExpert

from resource_explorer.activity_logger import log_rfa, log_survey
from resource_explorer.egeria_linkage import recheck_all_linkages
from resource_explorer.registry import Project, ProjectRegistry

pytestmark = pytest.mark.usefixtures("signed_in_caller")

UNKNOWN = Exception("OMRS-REPOSITORY-404-002 The entity identified with guid g is not known to the open metadata repository")


@pytest.fixture
def registry(tmp_path):
    reg = ProjectRegistry(db_path=str(tmp_path / "t.db"))
    reg.add(Project(slug="p", display_name="P", github_url="u", description=""))
    return reg


@pytest.fixture
def client(registry, monkeypatch):
    from resource_explorer.web.app import app
    monkeypatch.setattr("resource_explorer.web.routes.activity._registry", lambda: registry)
    return TestClient(app)


def _rfa_ids(client):
    return {r["summary"] + "|" + r["entity_slug"]: r for r in client.get("/api/activity/rfas").json()}


def _by_entry(client, entry_id):
    return [r for r in client.get("/api/activity/rfas").json() if r["entry_id"] == entry_id][0]


class TestUnscopedDismissal:
    def test_a_resource_less_rfa_can_be_dismissed_and_hides_only_itself(self, registry, client):
        stale = log_rfa(registry, "repo", "", "Egeria links", "error", "2 Egeria link(s) are stale")
        other = log_rfa(registry, "repo", "", "Publish queue", "error", "3 publishes are stuck")
        log_survey(registry=registry, entity_type="repo", entity_slug="p", entity_name="P", entity_location="",
                   intent="scouting", status="success", summary="s",
                   annotations=[{"analysis_name": "Request for action", "annotation_type": "RequestForActionAnnotation",
                                 "count": 1, "status": "local", "summary": "2 Egeria link(s) are stale"}])
        res_entry = [e for e in registry.list_activity(limit=50) if e["entity_slug"] == "p"][0]["id"]

        r = client.post(f"/api/activity/rfas/{stale}::0/dismiss", json={"reason": "not_applicable"})
        assert r.status_code == 200, r.text
        assert r.json()["dismissal"]["entity_slug"] == registry.RFA_SYSTEM_SCOPE

        assert _by_entry(client, stale)["dismissed"] is True
        assert _by_entry(client, other)["dismissed"] is False        # another no-resource RFA is untouched
        assert _by_entry(client, res_entry)["dismissed"] is False    # a resource's RFA with the same words too

    def test_the_same_finding_raised_again_stays_dismissed(self, registry, client):
        first = log_rfa(registry, "repo", "", "Egeria links", "error", "2 Egeria link(s) are stale")
        client.post(f"/api/activity/rfas/{first}::0/dismiss", json={"reason": "wont_do"})
        second = log_rfa(registry, "repo", "", "Egeria links", "error", "2 Egeria link(s) are stale")
        assert _by_entry(client, second)["dismissed"] is True

    def test_it_can_be_restored(self, registry, client):
        e = log_rfa(registry, "repo", "", "Egeria links", "error", "2 Egeria link(s) are stale")
        d = client.post(f"/api/activity/rfas/{e}::0/dismiss", json={"reason": "not_applicable"}).json()["dismissal"]
        assert client.post(f"/api/activity/rfas/dismissals/{d['id']}/clear", json={}).status_code == 200
        assert _by_entry(client, e)["dismissed"] is False

    def test_a_note_and_a_status_change_work_without_a_resource(self, registry, client):
        e = log_rfa(registry, "repo", "", "Egeria links", "error", "2 Egeria link(s) are stale")
        assert client.patch(f"/api/activity/rfas/{e}::0/notes", json={"notes": "looked"}).status_code == 200
        assert _by_entry(client, e)["notes"] == "looked"

    def test_the_empty_slug_guard_still_refuses_a_subjectless_dismissal(self, registry):
        with pytest.raises(ValueError, match="entity_slug is required"):
            registry.dismiss_rfa("repo", "", "x", "y", "not_applicable")

    def test_a_bare_system_scope_is_refused_too(self, registry):
        with pytest.raises(ValueError, match="system-scope"):
            registry.dismiss_rfa("repo", registry.RFA_SYSTEM_SCOPE, "", "", "not_applicable")


def _sweep(registry, behaviours):
    """Run the recheck with MetadataExpert replaced by an autospec bound to the real signatures."""
    cls = create_autospec(MetadataExpert, spec_set=True)

    def _get(guid, *a, **kw):
        out = behaviours.get(guid)
        if isinstance(out, BaseException):
            raise out
        return {"elementHeader": {}, "guid": guid}

    cls.return_value.get_metadata_element_by_guid.side_effect = _get
    with patch("pyegeria.omvs.metadata_expert.MetadataExpert", cls):
        return recheck_all_linkages(registry)


def _open_stale_rfas(client):
    return [r for r in client.get("/api/activity/rfas").json()
            if "link(s) are stale" in r["summary"] and r["rfa_status"] == "open" and not r["dismissed"]]


class TestSweepRaisesOneOpenRfa:
    @pytest.fixture
    def linked(self, registry):
        for s in ("a", "b"):
            registry.add(Project(slug=s, display_name=s, github_url=f"https://github.com/o/{s}",
                                 description="", egeria_asset_guid=f"guid-{s}"))
        return registry

    def test_two_sweeps_with_the_same_stale_set_leave_one_open_rfa(self, linked, client):
        gone = {"guid-a": UNKNOWN, "guid-b": UNKNOWN}
        assert _sweep(linked, gone)["stale"] == 2
        assert _sweep(linked, gone)["stale"] == 2
        assert len(_open_stale_rfas(client)) == 1

    def test_a_clean_sweep_adds_none_and_completes_the_open_one(self, linked, client):
        gone = {"guid-a": UNKNOWN, "guid-b": UNKNOWN}
        _sweep(linked, gone)
        assert _sweep(linked, {})["stale"] == 0
        assert _open_stale_rfas(client) == []
        done = [r for r in client.get("/api/activity/rfas").json() if "link(s) are stale" in r["summary"]]
        assert len(done) == 1 and done[0]["rfa_status"] == "completed"
        assert "recheck sweep" in done[0]["resolution_note"]

    def test_a_clean_sweep_with_nothing_open_adds_nothing(self, linked, client):
        _sweep(linked, {})
        assert client.get("/api/activity/rfas").json() == []

    def test_it_is_raised_again_after_being_completed_if_still_stale_later(self, linked, client):
        gone = {"guid-a": UNKNOWN}
        _sweep(linked, gone)
        _sweep(linked, {})
        _sweep(linked, gone)
        assert len(_open_stale_rfas(client)) == 1

    def test_legacy_duplicates_are_all_completed_by_a_clean_sweep(self, linked, client):
        for _ in range(3):
            log_rfa(linked, "repo", "", "Egeria links", "error", "2 Egeria link(s) are stale — Egeria was most likely reset")
        assert len(_open_stale_rfas(client)) == 3
        _sweep(linked, {})
        assert _open_stale_rfas(client) == []

    def test_an_unverifiable_sweep_does_not_complete_it(self, linked, client):
        _sweep(linked, {"guid-a": UNKNOWN, "guid-b": UNKNOWN})
        _sweep(linked, {"guid-a": Exception("Connection refused")})
        assert len(_open_stale_rfas(client)) == 1
