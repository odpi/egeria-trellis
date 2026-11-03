"""W1-B: linking a saved work list to an investigation after the fact, and the
"add these N to <investigation>" route (REPLY-DESIGNER-WORK-LISTS-VS-INVESTIGATIONS
section 2). Temp SQLite registry."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from resource_explorer.registry import ProjectRegistry
from resource_explorer.work_lists import WorkLists


@pytest.fixture
def registry(tmp_path):
    db = str(tmp_path / "t.db")
    assert db.startswith(str(tmp_path))   # never the shared registry
    return ProjectRegistry(db_path=db)


@pytest.fixture
def client(registry, monkeypatch):
    monkeypatch.setattr("resource_explorer.registry.ProjectRegistry.__init__",
                        lambda self, db_path=None: setattr(self, "__dict__", registry.__dict__) or None)
    monkeypatch.setenv("TRELLIS_ANONYMOUS_READ", "true")
    from resource_explorer.web.app import app
    return TestClient(app)


@pytest.fixture
def inv(registry):
    return registry.create_investigation("Customer 360", egeria_binding="local")["slug"]


@pytest.fixture
def wl(registry):
    w = WorkLists(registry).create("Sales databases", ["crm", "erp", "hr"], entity_type="database",
                                   rationale="")
    WorkLists(registry).set_member(w["slug"], "crm", rationale="holds the customers")
    return w["slug"]


def _scope(registry, slug):
    return {(m["entity_type"], m["entity_slug"]): m for m in registry.list_investigation_members(slug)}


class TestLinkAfterSave:
    def test_links_a_list_that_was_saved_unlinked(self, client, registry, inv, wl):
        assert WorkLists(registry).get(wl)["investigation"] == ""
        r = client.put(f"/api/work-lists/{wl}/investigation", json={"investigation": inv})
        assert r.status_code == 200, r.text
        assert r.json()["investigation"] == inv
        assert WorkLists(registry).get(wl)["investigation"] == inv
        assert [w["slug"] for w in client.get(f"/api/work-lists/?investigation={inv}").json()] == [wl]

    def test_an_unknown_investigation_is_404_and_nothing_changes(self, client, registry, wl):
        r = client.put(f"/api/work-lists/{wl}/investigation", json={"investigation": "nope"})
        assert r.status_code == 404
        assert WorkLists(registry).get(wl)["investigation"] == ""

    def test_an_unknown_list_is_404(self, client, inv):
        assert client.put("/api/work-lists/nope/investigation", json={"investigation": inv}).status_code == 404

    def test_empty_unlinks(self, client, registry, inv, wl):
        client.put(f"/api/work-lists/{wl}/investigation", json={"investigation": inv})
        assert client.put(f"/api/work-lists/{wl}/investigation", json={"investigation": ""}).status_code == 200
        assert WorkLists(registry).get(wl)["investigation"] == ""


class TestAddToInvestigation:
    def test_adds_every_member_with_its_rationale_or_the_list_name(self, client, registry, inv, wl):
        r = client.post(f"/api/work-lists/{wl}/add-to-investigation", json={"investigation": inv})
        assert r.status_code == 200, r.text
        out = r.json()
        assert sorted(out["added"]) == ["crm", "erp", "hr"] and out["already_in_scope"] == []
        s = _scope(registry, inv)
        assert s[("database", "crm")]["membership_rationale"] == "holds the customers"
        assert s[("database", "erp")]["membership_rationale"] == "from work list Sales databases"
        assert WorkLists(registry).get(wl)["investigation"] == "", "adding does not tag the list"

    def test_a_member_already_in_scope_is_skipped_and_keeps_its_reason(self, client, registry, inv, wl):
        ws = registry.get_or_create_working_set(inv)
        registry.add_working_set_member(ws["slug"], "database", "crm", membership_rationale="mine")
        out = client.post(f"/api/work-lists/{wl}/add-to-investigation", json={"investigation": inv}).json()
        assert out["already_in_scope"] == ["crm"] and sorted(out["added"]) == ["erp", "hr"]
        assert _scope(registry, inv)[("database", "crm")]["membership_rationale"] == "mine"

    def test_a_ticked_subset_adds_only_those(self, client, registry, inv, wl):
        out = client.post(f"/api/work-lists/{wl}/add-to-investigation",
                          json={"investigation": inv, "entity_slugs": ["erp"]}).json()
        assert out["added"] == ["erp"]
        assert set(_scope(registry, inv)) == {("database", "erp")}

    def test_refusals_write_nothing(self, client, registry, inv, wl):
        url = f"/api/work-lists/{wl}/add-to-investigation"
        assert client.post(url, json={"investigation": "nope"}).status_code == 404
        assert client.post(url, json={"investigation": ""}).status_code == 422
        assert client.post(url, json={"investigation": inv, "entity_slugs": ["zzz"]}).status_code == 422
        assert client.post("/api/work-lists/nope/add-to-investigation", json={"investigation": inv}).status_code == 404
        registry.close_investigation(inv)
        assert client.post(url, json={"investigation": inv}).status_code == 409
        assert registry.list_investigation_members(inv) == []
