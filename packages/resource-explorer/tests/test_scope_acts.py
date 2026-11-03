"""W1-A: the "add to <investigation>" act on a report and on a member list
puts the resource in the investigation's SCOPE and mints no work list
(REPLY-DESIGNER-WORK-LISTS-VS-INVESTIGATIONS section 4). Temp SQLite registry."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from resource_explorer.registry import Project, ProjectRegistry
from resource_explorer.work_lists import WorkLists

SEL = {"metric": "advisories", "members": ["GHSA-1", "GHSA-2"], "total": 4, "facet": "high"}


@pytest.fixture
def registry(tmp_path):
    db = str(tmp_path / "t.db")
    assert db.startswith(str(tmp_path))   # never the shared registry
    r = ProjectRegistry(db_path=db)
    r.add(Project(slug="p", display_name="P repo", github_url="https://github.com/x/p", description=""))
    return r


@pytest.fixture
def client(registry, monkeypatch):
    monkeypatch.setattr("resource_explorer.registry.ProjectRegistry.__init__",
                        lambda self, db_path=None: setattr(self, "__dict__", registry.__dict__) or None)
    monkeypatch.setenv("TRELLIS_ANONYMOUS_READ", "true")
    monkeypatch.setattr("resource_explorer.auth.get_current_user", lambda request: {"user_id": "peterprofile"})
    from resource_explorer.web.app import app
    return TestClient(app)


@pytest.fixture
def inv(registry):
    return registry.create_investigation("Customer 360", egeria_binding="local")["slug"]


def _record(client, registry):
    from tests.test_reports import TestTheRoute
    TestTheRoute()._seed(registry)
    return client.post("/api/projects/p/members/cve_scan/report",
                       json={"metric": "advisories", "name": "High advisories"}).json()["record"]


def _members(registry, slug):
    return {(m["entity_type"], m["entity_slug"]): m for m in registry.list_investigation_members(slug)}


class TestReportAct:
    def test_adds_to_scope_with_the_line_as_rationale_records_the_use_and_mints_no_list(self, client, registry, inv):
        rec = _record(client, registry)
        before = len(WorkLists(registry).list_all())
        r = client.post(f"/api/projects/p/records/{rec['id']}/act", json={"action": "scope", "investigation": inv})
        assert r.status_code == 200, r.text
        out = r.json()
        assert out["already_in_scope"] is False and out["investigation_name"] == "Customer 360"
        m = _members(registry, inv)[("repo", "p")]
        assert m["membership_rationale"] == out["provenance"] and m["membership_rationale"].startswith("4 advisories")
        assert out["record"]["uses"][-1]["act"] == "scope" and out["record"]["uses"][-1]["target"] == inv
        assert len(WorkLists(registry).list_all()) == before

    def test_already_in_scope_writes_nothing_and_keeps_the_reason(self, client, registry, inv):
        rec = _record(client, registry)
        ws = registry.get_or_create_working_set(inv)
        registry.add_working_set_member(ws["slug"], "repo", "p", membership_rationale="my own reason")
        r = client.post(f"/api/projects/p/records/{rec['id']}/act", json={"action": "scope", "investigation": inv})
        assert r.status_code == 200 and r.json()["already_in_scope"] is True
        assert _members(registry, inv)[("repo", "p")]["membership_rationale"] == "my own reason"
        assert not r.json()["record"].get("uses")

    def test_a_missing_or_closed_investigation_is_refused(self, client, registry, inv):
        rec = _record(client, registry)
        url = f"/api/projects/p/records/{rec['id']}/act"
        assert client.post(url, json={"action": "scope", "investigation": "nope"}).status_code == 404
        assert client.post(url, json={"action": "scope"}).status_code == 422
        registry.close_investigation(inv)
        assert client.post(url, json={"action": "scope", "investigation": inv}).status_code == 409
        assert registry.list_investigation_members(inv) == []

    def test_a_database_report_scopes_the_database(self, client, registry, inv, monkeypatch):
        from resource_explorer.curate_plan import Curations
        monkeypatch.setattr("resource_explorer.web.routes.projects._entity_display_name", lambda reg, et, s: "Sales DB")
        rec = Curations(registry).create_report("database", "sales", author="a", name="Sales report",
                                                report={"header": "h", "analysis_id": "x", "groups": [], "provenance": "p"})
        r = client.post(f"/api/projects/entity/database/sales/records/{rec['id']}/act",
                        json={"action": "scope", "investigation": inv})
        assert r.status_code == 200, r.text
        assert ("database", "sales") in _members(registry, inv)


class TestMemberListAct:
    def test_scope_adds_the_repo_with_the_provenance_line(self, client, registry, inv):
        before = len(WorkLists(registry).list_all())
        r = client.post("/api/projects/p/members/cve_scan/promote?entity_type=repo",
                        json={"action": "scope", "investigation": inv, **SEL})
        assert r.status_code == 200, r.text
        assert r.json()["already_in_scope"] is False
        assert _members(registry, inv)[("repo", "p")]["membership_rationale"] == r.json()["provenance"]
        assert len(WorkLists(registry).list_all()) == before

    def test_already_in_scope_keeps_the_reason(self, client, registry, inv):
        ws = registry.get_or_create_working_set(inv)
        registry.add_working_set_member(ws["slug"], "repo", "p", membership_rationale="first reason")
        r = client.post("/api/projects/p/members/cve_scan/promote?entity_type=repo",
                        json={"action": "scope", "investigation": inv, **SEL})
        assert r.status_code == 200 and r.json()["already_in_scope"] is True
        assert _members(registry, inv)[("repo", "p")]["membership_rationale"] == "first reason"
