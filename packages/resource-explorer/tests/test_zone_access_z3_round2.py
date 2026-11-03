"""Brief Z3 round 2 (2026-10-11): the review's F1, F3, F4 and F6.

F1  a dead membership row revived by another person carries THAT person as its requester, so the drain checks and
    stamps them, never the original requester.
F3  an RFA note creates a NoteLog and a Note anchored to the RFA's ToDo: CREATE judged on the ToDo, in the route
    (403 before the local write) and in the reconcile retry (the no-requester rule).
F4  a member refused at drain is dead at once: its reason is its own words, never "queued for retry".
F6  a reclassify's kind change is judged on the zones the move puts the Project in, as Egeria checks it.

Same fakes as test_zone_access_investigations_worklists.py: the access reads at the client boundary, the write
clients bound to the real pyegeria signatures. No Egeria anywhere.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from resource_explorer.registry import ProjectRegistry

sys.path.insert(0, str(Path(__file__).parent))

from tests.test_zone_access_investigations_worklists import (  # noqa: E402,F401  (fixtures)
    OWNER, SECURED, USER, FakeCollections, _install, _signed_in, _zone, registry, web,
)
from tests.zone_fakes import element as _element  # noqa: E402

pytestmark = pytest.mark.usefixtures("mock_egeria_client_connections")

OTHER = "erinoverview"


# ── F1: a revived row is the reviver's ───────────────────────────────────────────────────────────────────

class TestRevivedRowRequester:
    def _enqueue_as(self, registry, monkeypatch, who: str, role: str = "user") -> int:
        from resource_explorer.a2a_auth import current_caller
        from resource_explorer.egeria_outbox import enqueue_collection_members
        from tests.zone_fakes import signed_in

        monkeypatch.setattr("resource_explorer.run_queue.requested_by", lambda: who)
        token = signed_in(who, role=role)
        try:
            return enqueue_collection_members(registry, "i", "cg", [{"member_guid": "g-q", "entity_type": "repo",
                                                                      "entity_slug": "q"}])[0]
        finally:
            current_caller.reset(token)

    def test_a_row_dead_for_a_revived_by_b_is_checked_and_authored_as_b(self, registry, monkeypatch):
        from resource_explorer import egeria_outbox
        from resource_explorer.egeria_outbox import OutboxClients, drain_outbox

        fake = _install(monkeypatch)
        rid = self._enqueue_as(registry, monkeypatch, OTHER, role="curator")
        assert json.loads(registry.list_outbox_elements(entity_slug="i", limit=5)[0]["payload_json"])["portal_role"]
        registry.mark_outbox_failed(rid, "boom", max_attempts=1)                # dead, queued for OTHER (a curator)
        assert self._enqueue_as(registry, monkeypatch, USER) == rid             # USER presses again: revives it
        payload = json.loads(registry.list_outbox_elements(entity_slug="i", limit=5)[0]["payload_json"])
        assert payload["by"] == USER and "portal_role" not in payload          # the reviver, no inherited override
        assert payload["member_guid"] == "g-q"                                  # the rest of the payload is kept
        _zone(fake, "g-q", [OTHER])                                             # granted to the OLD requester only
        authors = []
        real = egeria_outbox._row_requester
        monkeypatch.setattr(egeria_outbox, "_row_requester", lambda row: authors.append(real(row)) or real(row))
        cm = FakeCollections()
        drain_outbox(registry, OutboxClients(collection_manager=cm), lambda qn: "", limit=5)
        assert cm.calls == []
        row = registry.list_outbox_elements(entity_slug="i", limit=5)[0]
        assert row["last_error"] == f"not permitted · zone {SECURED} does not grant {USER} attach in Egeria"
        assert authors and set(authors) == {USER}                               # checked and stamped as USER


# ── F3: RFA notes ────────────────────────────────────────────────────────────────────────────────────────

def _note_patches(monkeypatch, todo: str):
    monkeypatch.setattr(ProjectRegistry, "get_activity", lambda self, eid: {"id": eid})
    monkeypatch.setattr(ProjectRegistry, "get_rfa_action", lambda self, rid: {"id": rid, "egeria_todo_guid": todo})
    saved = []
    monkeypatch.setattr(ProjectRegistry, "upsert_rfa_note", lambda self, **kw: saved.append(kw["notes"]))
    synced = []
    monkeypatch.setattr("resource_explorer.rfa_egeria_sync.sync_rfa_note", lambda reg, row: synced.append(row))
    return saved, synced


class TestRfaNotes:
    def test_a_note_on_a_todo_the_caller_may_not_create_under_is_refused_and_not_saved(self, web, monkeypatch):
        client, _ = web
        fake = _install(monkeypatch)
        _zone(fake, "td", ["dbas"])
        saved, synced = _note_patches(monkeypatch, "td")
        r = client.patch("/api/activity/rfas/e1::0/notes", json={"notes": "asked them"})
        assert r.status_code == 403, r.text
        assert r.json()["detail"] == f"zone {SECURED} does not grant {USER} create in Egeria"
        assert saved == [] and synced == []

    def test_a_granted_note_is_saved_and_synced(self, web, monkeypatch):
        client, _ = web
        fake = _install(monkeypatch)
        _zone(fake, "td", [USER])
        saved, synced = _note_patches(monkeypatch, "td")
        r = client.patch("/api/activity/rfas/e1::0/notes", json={"notes": "asked them"})
        assert r.status_code == 200, r.text
        assert saved == ["asked them"] and len(synced) == 1
        assert ("control", SECURED) in fake.calls

    def test_an_anonymous_note_on_a_todo_is_refused(self, web, monkeypatch):
        client, who = web
        _install(monkeypatch)
        who["identity"] = False
        saved, _ = _note_patches(monkeypatch, "td")
        r = client.patch("/api/activity/rfas/e1::0/notes", json={"notes": "x"})
        assert r.status_code == 403 and r.json()["detail"].startswith("sign in to curate")
        assert saved == []

    def test_a_note_retry_with_nobody_recorded_follows_the_no_requester_rule(self, registry, monkeypatch):
        from resource_explorer import rfa_egeria_sync

        fake = _install(monkeypatch)
        fake.elements["td"] = _element("td", [SECURED])
        rows = [{"id": "e1::0", "egeria_todo_guid": "td", "notes": "n"},
                {"id": "e2::0", "egeria_todo_guid": "td2", "notes": "n"}]
        synced, errors = [], {}
        monkeypatch.setattr(registry, "list_unsynced_rfa_actions", lambda: [])
        monkeypatch.setattr(registry, "list_unsynced_rfa_notes", lambda: rows)
        monkeypatch.setattr(registry, "list_synced_rfa_actions", lambda: [])
        monkeypatch.setattr(registry, "mark_rfa_note_sync_error", lambda rid, why: errors.__setitem__(rid, why))
        monkeypatch.setattr(rfa_egeria_sync, "sync_rfa_note", lambda reg, row: synced.append(row["id"]))
        rfa_egeria_sync.reconcile_rfa_actions(registry)
        assert synced == ["e2::0"]                                    # td2 carries no zone: open
        assert errors["e1::0"] == f"not permitted · no person asked for this run, and zone {SECURED} is in use"


# ── F4: no retry clause for a refusal ────────────────────────────────────────────────────────────────────

def test_a_member_refused_at_drain_is_said_in_its_own_words_not_queued_for_retry(registry, monkeypatch):
    from resource_explorer.egeria_outbox import collection_membership_qn
    from resource_explorer.surveyors.egeria_investigation_publisher import EgeriaInvestigationPublisher

    inv = registry.create_investigation("Inv", project_classification="StudyProject", egeria_project_guid="pg")
    ws = registry.get_or_create_working_set(inv["slug"])
    registry.set_working_set_egeria_collection(ws["slug"], "cg")
    registry.add_working_set_member(ws["slug"], "repo", "q")
    words = f"not permitted · zone {SECURED} does not grant {USER} attach in Egeria"
    rid = registry.enqueue_outbox_element("investigation", inv["slug"], "collection_membership",
                                          collection_membership_qn("cg", "g-q"),
                                          {"collection_guid": "cg", "member_guid": "g-q"})
    registry.mark_outbox_failed(rid, words, max_attempts=1)
    status = EgeriaInvestigationPublisher(registry).link_status(inv["slug"])
    assert status["members"] == [{"entity_type": "repo", "entity_slug": "q", "state": "not_linked", "linkable": True, "reason": words}]  # a refused member stays linkable: the relink retries it and is refused again


# ── F6: the kind change on the zones the move puts the Project in ────────────────────────────────────────

def test_a_tightening_by_a_non_owner_is_refused_on_the_new_zones_before_anything_moves(registry, monkeypatch):
    from resource_explorer.surveyors import investigation_reclassifier as ir
    from resource_explorer.workflows.egeria_work_access import access_words

    fake = _install(monkeypatch)
    fake.controls[SECURED] = {"associatedSecurityList": {"PUBLISH": [USER], "DEFAULT": ["dbas"]}}
    _signed_in(monkeypatch)                                         # USER, not the investigation's owner
    inv = {"slug": "i", "display_name": "I", "project_classification": "StudyProject", "created_by": OWNER,
           "egeria_project_guid": "pg"}
    monkeypatch.setattr(registry, "get_investigation", lambda slug: dict(inv))
    monkeypatch.setattr(ir.InvestigationReclassifier, "_report_guids", lambda self, slug: ([], []))
    moved = []
    monkeypatch.setattr(ir.InvestigationReclassifier, "_move_zones",
                        lambda self, g, target, owner="": moved.append(g) or (True, ""))
    monkeypatch.setattr(ir.InvestigationReclassifier, "_move_kind_classification", lambda self, *a: (True, ""))
    monkeypatch.setattr("resource_explorer.egeria_identity.private_zone_status", lambda: {"status": "ok"})
    monkeypatch.setattr("resource_explorer.egeria_identity.private_zone_is_enforced", lambda: True)
    res = ir.InvestigationReclassifier(registry).reclassify(
        "i", "PersonalProject", access=lambda ws: access_words(registry, ws, entity_type="investigation", slug="i"))
    assert moved == []
    assert res.not_permitted == (f"not permitted · the investigation Project in its new zones: zone {SECURED} "
                                 f"does not grant {USER} classify in Egeria")


def test_a_loosening_with_no_configured_zone_judges_the_kind_on_the_zones_that_stay(registry, monkeypatch):
    """RE clears its own zones and keeps the rest (`_clear_zones`): the kind change is judged on those."""
    from resource_explorer.workflows.egeria_work_access import access_words, reclassify_kind_after_move

    fake = _install(monkeypatch)
    fake.elements["pg"] = _element("pg", [SECURED, "foreign-zone"])
    fake.controls["foreign-zone"] = {"associatedSecurityList": {"DEFAULT": ["dbas"]}}
    _signed_in(monkeypatch)
    w = reclassify_kind_after_move("pg", "PersonalProject", [], own_zones=(SECURED, OWNER))
    assert access_words(registry, [w]) == (f"not permitted · the investigation Project in its new zones: zone "
                                           f"foreign-zone does not grant {USER} classify in Egeria")


# ── F2 (prepared; the refusal behaviour waits for the owner): an UNREADABLE check stays local-first ────────

class TestRfaUnreadableStaysLocal:
    def test_a_status_change_is_recorded_locally_and_not_synced_when_egeria_cannot_be_asked(self, web, monkeypatch):
        client, _ = web
        fake = _install(monkeypatch)
        _zone(fake, "td", [USER])
        fake.fail_element = "td"
        monkeypatch.setattr(ProjectRegistry, "get_activity", lambda self, eid: {"id": eid})
        monkeypatch.setattr(ProjectRegistry, "get_rfa_action", lambda self, rid: {"id": rid, "egeria_todo_guid": "td"})
        saved, errors, synced = [], {}, []
        monkeypatch.setattr(ProjectRegistry, "upsert_rfa_action", lambda self, **kw: saved.append(kw["status"]))
        monkeypatch.setattr(ProjectRegistry, "mark_rfa_sync_error",
                            lambda self, rid, why: errors.__setitem__(rid, why))
        monkeypatch.setattr("resource_explorer.rfa_egeria_sync.sync_rfa_action", lambda reg, row: synced.append(row))
        r = client.patch("/api/activity/rfas/e1::0", json={"status": "deferred"})
        assert r.status_code == 200, r.text
        assert saved == ["deferred"] and synced == []
        assert r.json()["egeria_sync"].startswith("not permitted · could not check access in Egeria (")
        assert errors["e1::0"] == r.json()["egeria_sync"]

    def test_a_note_is_saved_locally_and_not_synced_when_egeria_cannot_be_asked(self, web, monkeypatch):
        client, _ = web
        fake = _install(monkeypatch)
        fake.fail_element = "td"
        saved, synced = _note_patches(monkeypatch, "td")
        errors = {}
        monkeypatch.setattr(ProjectRegistry, "mark_rfa_note_sync_error",
                            lambda self, rid, why: errors.__setitem__(rid, why))
        r = client.patch("/api/activity/rfas/e1::0/notes", json={"notes": "n"})
        assert r.status_code == 200, r.text
        assert saved == ["n"] and synced == []
        assert errors["e1::0"].startswith("not permitted · could not check access in Egeria (")


# ── round 3 ──────────────────────────────────────────────────────────────────────────────────────────────

class TestRound3:
    def _member_row(self, registry, status_error: str, dead: bool):
        from resource_explorer.egeria_outbox import collection_membership_qn

        inv = registry.create_investigation("Inv", project_classification="StudyProject", egeria_project_guid="pg")
        ws = registry.get_or_create_working_set(inv["slug"])
        registry.set_working_set_egeria_collection(ws["slug"], "cg")
        registry.add_working_set_member(ws["slug"], "repo", "q")
        rid = registry.enqueue_outbox_element("investigation", inv["slug"], "collection_membership",
                                              collection_membership_qn("cg", "g-q"),
                                              {"collection_guid": "cg", "member_guid": "g-q"})
        registry.mark_outbox_failed(rid, status_error, max_attempts=1 if dead else 5)
        return inv["slug"]

    def test_l1_a_dead_row_says_its_error_without_a_retry_clause(self, registry):
        """Egeria's own security refusal is dead at once (max_attempts=1): it is never retried, so never says so."""
        from resource_explorer.egeria_outbox import NEEDS_PERMISSION_CHANGE, REFUSED_BY_SECURITY
        from resource_explorer.surveyors.egeria_investigation_publisher import EgeriaInvestigationPublisher

        words = f"{REFUSED_BY_SECURITY}: no · {NEEDS_PERMISSION_CHANGE}"
        slug = self._member_row(registry, words, dead=True)
        reason = EgeriaInvestigationPublisher(registry).link_status(slug)["members"][0]["reason"]
        assert reason == words

    def test_l1_a_row_still_to_be_attempted_keeps_the_retry_clause(self, registry):
        from resource_explorer.surveyors.egeria_investigation_publisher import EgeriaInvestigationPublisher

        slug = self._member_row(registry, "ConnectionError: down", dead=False)
        reason = EgeriaInvestigationPublisher(registry).link_status(slug)["members"][0]["reason"]
        assert reason == "attach failed (ConnectionError: down) — queued for retry, see Admin → Publish Queue"

    def test_l2a_a_revive_with_nobody_drops_the_requester_and_fails_closed_with_zones_in_use(self, registry,
                                                                                           monkeypatch):
        from resource_explorer.egeria_outbox import OutboxClients, drain_outbox, enqueue_collection_members
        from resource_explorer.workflows.curate import NO_REQUESTER_WORDS

        fake = _install(monkeypatch)
        rid = TestRevivedRowRequester()._enqueue_as(registry, monkeypatch, OTHER, role="curator")
        registry.mark_outbox_failed(rid, "boom", max_attempts=1)
        monkeypatch.setattr("resource_explorer.run_queue.requested_by", lambda: "")    # the unattended resync
        assert enqueue_collection_members(registry, "i", "cg", [{"member_guid": "g-q", "entity_type": "repo",
                                                                 "entity_slug": "q"}])[0] == rid
        payload = json.loads(registry.list_outbox_elements(entity_slug="i", limit=5)[0]["payload_json"])
        assert "by" not in payload and "portal_role" not in payload
        fake.elements["g-q"] = _element("g-q", [SECURED])
        cm = FakeCollections()
        drain_outbox(registry, OutboxClients(collection_manager=cm), lambda qn: "", limit=5)
        assert cm.calls == []
        row = registry.list_outbox_elements(entity_slug="i", limit=5)[0]
        assert row["status"] == "dead"
        assert row["last_error"] == f"not permitted · {NO_REQUESTER_WORDS}, and zone {SECURED} is in use"

    def test_l2b_after_an_unreadable_patch_the_reconcile_refuses_with_words_rather_than_syncing(self, web,
                                                                                               registry,
                                                                                               monkeypatch):
        from resource_explorer import rfa_egeria_sync

        client, _ = web
        fake = _install(monkeypatch)
        _zone(fake, "td", ["dbas"])                       # a zoned, existing ToDo
        monkeypatch.setattr(ProjectRegistry, "get_activity", lambda self, eid: {"id": eid})
        registry.upsert_rfa_action(rfa_id="e1::0", entry_id="e1", annotation_index=0, status="open",
                                   activity_status="REQUESTED", assignee="", defer_until="", resolution_note="")
        registry.mark_rfa_synced("e1::0", "td")
        fake.fail_element = "td"                          # Egeria cannot be asked at the press
        synced = []
        monkeypatch.setattr(rfa_egeria_sync, "sync_rfa_action", lambda reg, row: synced.append(row["id"]))
        r = client.patch("/api/activity/rfas/e1::0", json={"status": "deferred"})
        assert r.status_code == 200 and r.json()["egeria_sync"].startswith("not permitted · could not check")
        assert synced == []
        fake.fail_element = ""                            # Egeria is back; the retry runs with nobody recorded
        monkeypatch.setattr(registry, "list_unsynced_rfa_notes", lambda: [])
        monkeypatch.setattr(registry, "list_synced_rfa_actions", lambda: [])
        rfa_egeria_sync.reconcile_rfa_actions(registry)
        assert synced == []
        row = registry.get_rfa_action("e1::0")
        assert row["rfa_status"] == "deferred"
        assert row["sync_error"] == f"not permitted · no person asked for this run, and zone {SECURED} is in use"
