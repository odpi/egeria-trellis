"""A work list of more than one kind (DESIGN-WORK-LISTS-WINNOWING-AND-MIXED-KINDS §5, §6.3).

`entity_type` moves from the list to the member: backfilled from the list (every list before this held one kind),
part of the key (a repository and a database may share a slug). The list reads as its members' one kind, or
"mixed", with a `kinds` summary derived on every read. Publish, export and add-to-investigation treat each member
as its own kind; runs stay per kind (one batch per kind, each its own set id); a cut made under one kind's chip
covers that kind's members only.
"""
from __future__ import annotations

import sqlite3

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from resource_explorer.registry import ProjectRegistry
from resource_explorer.work_lists import MIXED, WorkLists


@pytest.fixture
def wls(tmp_path):
    return WorkLists(ProjectRegistry(db_path=str(tmp_path / "w.db")))


def _mixed(wls, **kw):
    return wls.create("Order system", [{"entity_slug": "orders", "entity_type": "repo"},
                                       {"entity_slug": "orders", "entity_type": "database"},
                                       {"entity_slug": "landing", "entity_type": "filesystem"}], **kw)


# ── the migration ───────────────────────────────────────────────────────────


def _old_tables(db: str) -> None:
    c = sqlite3.connect(db)
    c.execute("""CREATE TABLE work_lists (slug TEXT PRIMARY KEY, display_name TEXT NOT NULL,
        description TEXT NOT NULL DEFAULT '', entity_type TEXT NOT NULL DEFAULT 'repo',
        investigation TEXT NOT NULL DEFAULT '', created_by TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL,
        derived_from TEXT NOT NULL DEFAULT '', egeria_guid TEXT NOT NULL DEFAULT '',
        published_at TEXT NOT NULL DEFAULT '')""")
    c.execute("""CREATE TABLE work_list_members (work_list_slug TEXT NOT NULL, entity_slug TEXT NOT NULL,
        rationale TEXT NOT NULL DEFAULT '', confidence INTEGER, added_at TEXT NOT NULL,
        PRIMARY KEY (work_list_slug, entity_slug))""")
    c.execute("INSERT INTO work_lists (slug, display_name, entity_type, created_at) VALUES ('dbs', 'D', 'database', 'x')")
    c.execute("INSERT INTO work_list_members VALUES ('dbs', 'crm', 'big', 80, '2026-01-01')")
    c.execute("INSERT INTO work_list_members VALUES ('gone', 'p', '', NULL, '2026-01-01')")   # its list was deleted
    c.commit()
    c.close()


def test_an_old_members_table_gains_each_members_kind_from_its_list(tmp_path):
    db = str(tmp_path / "old.db")
    _old_tables(db)
    wls = WorkLists(ProjectRegistry(db_path=db))
    got = wls.get("dbs")
    assert got["members"][0]["entity_type"] == "database"
    assert (got["members"][0]["rationale"], got["members"][0]["confidence"]) == ("big", 80)   # nothing else moved
    assert got["entity_type"] == "database" and got["kinds"] == {"database": 1}
    c = sqlite3.connect(db)
    orphan = c.execute("SELECT entity_type FROM work_list_members WHERE work_list_slug = 'gone'").fetchone()
    pk = [r[1] for r in c.execute("PRAGMA table_info(work_list_members)").fetchall() if r[5]]
    c.close()
    assert orphan == ("",)                       # never a guessed 'repo'
    assert sorted(pk) == ["entity_slug", "entity_type", "work_list_slug"]
    ProjectRegistry(db_path=db)                  # a second start is a no-op
    wls.set_member("dbs", "crm", entity_type="repo", rationale="same name, other kind")
    assert sorted(m["entity_type"] for m in wls.get("dbs")["members"]) == ["database", "repo"]


# ── storage ─────────────────────────────────────────────────────────────────


def test_a_list_of_more_than_one_kind_reads_mixed_with_its_kinds(wls):
    got = _mixed(wls)
    assert got["entity_type"] == MIXED and got["stored_entity_type"] == MIXED
    assert got["kinds"] == {"repo": 1, "database": 1, "filesystem": 1}
    assert sorted((m["entity_type"], m["entity_slug"]) for m in got["members"]) == [
        ("database", "orders"), ("filesystem", "landing"), ("repo", "orders")]
    listed = next(w for w in wls.list_all() if w["slug"] == got["slug"])
    assert listed["entity_type"] == MIXED and listed["member_count"] == 3 and listed["kinds"]["database"] == 1


def test_a_one_kind_list_is_as_before(wls):
    got = wls.create("Repos", ["a", "b"])
    assert got["entity_type"] == "repo" and got["kinds"] == {"repo": 2}
    wls.set_member(got["slug"], "c")                       # no kind: the list's
    assert {m["entity_type"] for m in wls.get(got["slug"])["members"]} == {"repo"}


def test_a_mixed_list_needs_the_kind_and_says_so(wls):
    got = _mixed(wls)
    with pytest.raises(ValueError, match="say which kind"):
        wls.set_member(got["slug"], "new-one")
    with pytest.raises(ValueError, match="is in this list as database and as repo"):
        wls.set_member(got["slug"], "orders", rationale="which one?")
    wls.set_member(got["slug"], "orders", entity_type="database", rationale="the database")
    by = {(m["entity_type"], m["entity_slug"]): m for m in wls.get(got["slug"])["members"]}
    assert by[("database", "orders")]["rationale"] == "the database" and by[("repo", "orders")]["rationale"] == ""
    with pytest.raises(ValueError, match="more than one kind"):
        wls.remove_member(got["slug"], "orders")
    wls.remove_member(got["slug"], "orders", "repo")
    wls.remove_member(got["slug"], "landing")             # one member has that slug: no kind needed
    assert [(m["entity_type"], m["entity_slug"]) for m in wls.get(got["slug"])["members"]] == [("database", "orders")]
    assert wls.get(got["slug"])["entity_type"] == "database"   # the read follows the members


def test_promote_keeps_each_survivors_kind(wls):
    got = _mixed(wls)
    short = wls.promote(got["slug"], [{"entity_slug": "orders", "entity_type": "database"}, "landing"])
    assert sorted((m["entity_type"], m["entity_slug"]) for m in short["members"]) == [
        ("database", "orders"), ("filesystem", "landing")]
    assert short["entity_type"] == MIXED


def test_the_journal_adds_a_member_as_its_own_kind(wls):
    from resource_explorer.journal import Journal

    j = Journal(wls.registry)
    j.write("repo", "orders", author="a", body="look", suggest_to=["Consumer"])
    j.write("database", "orders_db", author="a", body="this too", suggest_to=["Consumer"])
    got = wls.get("suggested-to-consumer")
    assert sorted((m["entity_type"], m["entity_slug"]) for m in got["members"]) == [
        ("database", "orders_db"), ("repo", "orders")]          # before this, the database joined as a repo


def test_export_rows_state_each_member_as_its_own_kind(wls):
    from resource_explorer.batch_io import work_list_export_rows

    rows = work_list_export_rows(wls.registry, _mixed(wls))
    assert sorted((r["resource_type"], r["status_slug"]) for r in rows) == [
        ("database", "orders"), ("filesystem", "landing"), ("repo", "orders")]


# ── publish ─────────────────────────────────────────────────────────────────


class _Stop(Exception):
    pass


def test_publish_resolves_and_reports_each_member_as_its_own_kind(wls, monkeypatch):
    from resource_explorer import work_lists as mod
    from resource_explorer.member_assets import NotInEgeriaYet

    asked: list[tuple[str, str]] = []

    def fake_guid(registry, kind, slug):
        asked.append((kind, slug))
        if kind == "filesystem":
            raise NotInEgeriaYet(kind)
        return f"guid-{kind}-{slug}"

    monkeypatch.setattr(mod, "member_egeria_guid", fake_guid)
    monkeypatch.setattr("resource_explorer.workflows.egeria_work_access.access_words", lambda *a, **k: "")
    monkeypatch.setattr("resource_explorer.zone_access.EgeriaAccessReader", lambda: object())
    queued: list[dict] = []
    monkeypatch.setattr(mod, "enqueue_work_list_members",
                        lambda reg, slug, guid, members, run_id="": queued.extend(members) or [])

    class _Clients:
        def require(self, name):
            return object()

    monkeypatch.setattr("resource_explorer.egeria_outbox._default_clients", lambda who: (_Clients(), lambda *a: ""))
    monkeypatch.setattr("resource_explorer.egeria_clients.current_principal", lambda: "me")
    monkeypatch.setattr("resource_explorer.surveyors.egeria_investigation_publisher._create_typed_collection",
                        lambda *a, **k: "wl-guid")
    got = _mixed(wls)
    out = wls.publish(got["slug"])
    assert sorted(asked) == [("database", "orders"), ("filesystem", "landing"), ("repo", "orders")]
    assert sorted((m["entity_type"], m["entity_slug"]) for m in queued) == [("database", "orders"), ("repo", "orders")]
    assert out["not_published"] == [{"entity_slug": "landing", "entity_type": "filesystem",
                                      "words": "not in Egeria yet · filesystem"}]


# ── runs per kind ───────────────────────────────────────────────────────────


def test_runs_stay_per_kind_each_with_its_own_set(wls, monkeypatch):
    n = iter(range(100))
    targets: list[dict] = []
    monkeypatch.setattr(wls.registry, "enqueue_run",
                        lambda kind, target, requested_by="": targets.append(target) or f"run-{next(n)}")
    members = [(m["entity_type"], m["entity_slug"]) for m in _mixed(wls)["members"]]
    out = wls.enqueue_per_kind({"repo": "repository_health", "database": "schema_inventory"}, members,
                               work_list_slug="wl")
    by = {r["entity_type"]: r for r in out["runs"]}
    assert set(by) == {"repo", "database"} and by["repo"]["set_id"] != by["database"]["set_id"]
    assert {(t["entity_type"], t["analysis_id"]) for t in targets} == {
        ("repo", "repository_health"), ("database", "schema_inventory")}
    assert out["not_run"] == [{"entity_type": "filesystem", "count": 1,
                               "words": "no analysis chosen for the 1 filesystem member(s)"}]


def test_a_kind_refused_is_a_line_and_the_others_still_run(wls, monkeypatch):
    from resource_explorer.workflows.curate import CurationDenied

    monkeypatch.setattr(wls.registry, "enqueue_run", lambda kind, target, requested_by="": "run-1")
    members = [("repo", "a"), ("database", "b")]
    refusal_for = lambda kind: (lambda s: "not permitted · zone z" if kind == "database" else "")
    out = wls.enqueue_per_kind({"repo": "x", "database": "y"}, members, refusal_for=refusal_for)
    by = {r["entity_type"]: r for r in out["runs"]}
    assert by["repo"]["runs"] and by["database"]["refused"] == "not permitted · zone z"
    with pytest.raises(CurationDenied):
        wls.enqueue_per_kind({"database": "y"}, [("database", "b")], refusal_for=refusal_for)


# ── a cut under one kind's chip ─────────────────────────────────────────────


def _db_filter(kept, dropped=(), parent_count=None):
    return {"entity_type": "database", "applied_at": "2026-10-28T12:00:00+00:00",
            "criteria": [{"question": "How big?", "analysis_ids": ["schema_inventory"],
                          "field": "measure:schema_inventory:relation_count", "op": ">=", "value": 10}],
            "kept": list(kept), "dropped": list(dropped), "could_not_check": [],
            "parent_count": len(kept) + len(dropped) if parent_count is None else parent_count}


def test_a_cut_made_over_one_kind_covers_that_kinds_members_only(wls):
    parent = wls.create("Mixed", [{"entity_slug": "d1", "entity_type": "database"},
                                  {"entity_slug": "d2", "entity_type": "database"},
                                  {"entity_slug": "r1", "entity_type": "repo"}])
    short = wls.promote(parent["slug"], ["d1"], filter=_db_filter(["d1"], ["d2"]))
    assert short["filter"]["entity_type"] == "database" and short["filter"]["parent_count"] == 2
    assert [(m["entity_type"], m["entity_slug"]) for m in short["members"]] == [("database", "d1")]
    with pytest.raises(ValueError, match="not in any bucket: d2"):          # the repo is not asked; d2 is
        wls.promote(parent["slug"], ["d1"], filter=_db_filter(["d1"], parent_count=1))
    out = wls.reapply(short["slug"], _db_filter(["d2"], ["d1"]))
    assert out["joined"] == ["d2"] and out["dropped"] == ["d1"]


# ── routes ──────────────────────────────────────────────────────────────────


@pytest.fixture
def client(wls, monkeypatch):
    from resource_explorer.web.routes import work_lists as routes

    monkeypatch.setattr(routes, "WorkLists", lambda: wls)
    app = FastAPI()
    app.include_router(routes.router, prefix="/api/work-lists")
    return TestClient(app)


def test_routes_create_a_mixed_list_and_refuse_an_unknown_kind(client):
    r = client.post("/api/work-lists/", json={"display_name": "M", "members": [
        {"entity_slug": "a", "entity_type": "repo"}, {"entity_slug": "b", "entity_type": "database"}]})
    assert r.status_code == 200 and r.json()["entity_type"] == "mixed"
    r = client.post("/api/work-lists/", json={"display_name": "M", "members": [
        {"entity_slug": "a", "entity_type": "planet"}]})
    assert r.status_code == 422 and "planet" in r.json()["detail"]


def test_one_batch_over_a_mixed_list_is_refused_with_where_to_go(client, wls):
    got = _mixed(wls)
    r = client.post("/api/work-lists/runs/batch", json={"analysis_id": "x", "work_list_slug": got["slug"],
                                                        "entity_type": "repo"})
    assert r.status_code == 422 and f"/api/work-lists/{got['slug']}/runs/per-kind" in r.json()["detail"]


def test_the_per_kind_route_checks_each_analysis_against_its_own_kind(client, wls, monkeypatch):
    got = _mixed(wls)
    seen: list[tuple[str, str]] = []

    def plan(aid, kind):
        seen.append((aid, kind))
        if aid == "bad":
            raise KeyError(aid)

    monkeypatch.setattr("resource_explorer.workflows.analysis.resolve_analysis_plan", plan)
    monkeypatch.setattr("resource_explorer.workflows.egeria_work_access.batch_refusal", lambda *a, **k: "")
    monkeypatch.setattr("resource_explorer.zone_access.EgeriaAccessReader", lambda: object())
    monkeypatch.setattr(wls.registry, "enqueue_run", lambda kind, target, requested_by="": "run-1")
    r = client.post(f"/api/work-lists/{got['slug']}/runs/per-kind", json={"analyses": {"database": "bad"}})
    assert r.status_code == 400 and "for database" in r.json()["detail"]
    r = client.post(f"/api/work-lists/{got['slug']}/runs/per-kind",
                    json={"analyses": {"repo": "rh", "database": "si"},
                          "members": [{"entity_slug": "orders", "entity_type": "database"}]})
    assert r.status_code == 200, r.text
    assert [x["entity_type"] for x in r.json()["runs"]] == ["database"]
    assert ("si", "database") in seen
    r = client.post(f"/api/work-lists/{got['slug']}/runs/per-kind",
                    json={"analyses": {}, "members": [{"entity_slug": "zz", "entity_type": "repo"}]})
    assert r.status_code == 422 and "repo zz" in r.json()["detail"]


def test_remove_member_route_needs_the_kind_only_when_two_share_a_slug(client, wls):
    got = _mixed(wls)
    assert client.delete(f"/api/work-lists/{got['slug']}/members/orders").status_code == 422
    r = client.delete(f"/api/work-lists/{got['slug']}/members/orders?entity_type=repo")
    assert r.status_code == 200 and r.json()["kinds"] == {"database": 1, "filesystem": 1}
