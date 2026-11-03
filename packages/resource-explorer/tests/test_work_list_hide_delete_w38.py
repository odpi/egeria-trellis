"""W38: hide a work list for yourself; delete one only if you made it (or are an admin).

Hide is a per-person view preference in `work_list_view`: it changes nothing for anyone else and nothing in Egeria.
Delete is the existing route plus a creator-or-admin check, a 403 that says who may, and one Activity entry.
"""
from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from resource_explorer.a2a_auth import CallerIdentity, current_caller
from resource_explorer.registry import ProjectRegistry
from resource_explorer.work_lists import WorkLists

NEXT = Path(__file__).resolve().parent.parent / "resource_explorer" / "web" / "static"


@contextmanager
def as_user(user_id):
    reset = current_caller.set(CallerIdentity(user_id=user_id, egeria_token="t", auth_source="app-jwt"))
    try:
        yield
    finally:
        current_caller.reset(reset)


@pytest.fixture
def wls(tmp_path):
    return WorkLists(ProjectRegistry(db_path=str(tmp_path / "w.db")))


@pytest.fixture
def client(wls, monkeypatch):
    from resource_explorer.web.routes import work_lists as routes

    monkeypatch.setattr(routes, "WorkLists", lambda: wls)
    app = FastAPI()
    app.include_router(routes.router, prefix="/api/work-lists")
    return TestClient(app)


def _slugs(rows, hidden):
    return sorted(r["slug"] for r in rows if r["hidden"] is hidden)


def test_hide_is_per_person_and_reversible(client, wls):
    a = wls.create("A list", ["x"], created_by="erin")["slug"]
    b = wls.create("B list", ["y"], created_by="erin")["slug"]
    with as_user("peterprofile"):
        assert client.put(f"/api/work-lists/{a}/hidden", json={"hidden": True}).json() == {"slug": a, "hidden": True}
        rows = client.get("/api/work-lists/").json()
        assert _slugs(rows, True) == [a] and _slugs(rows, False) == [b]
    with as_user("garygeeke"):
        assert _slugs(client.get("/api/work-lists/").json(), True) == []      # still listed for everyone else
    with as_user("peterprofile"):
        client.put(f"/api/work-lists/{a}/hidden", json={"hidden": False})
        assert _slugs(client.get("/api/work-lists/").json(), True) == []
    assert wls.get(a) is not None                                              # hiding never removed anything


def test_hiding_an_unknown_list_is_404(client):
    assert client.put("/api/work-lists/nope/hidden", json={"hidden": True}).status_code == 404


def test_a_hidden_list_still_opens_by_slug(client, wls):
    a = wls.create("A list", ["x"], created_by="erin")["slug"]
    with as_user("peterprofile"):
        client.put(f"/api/work-lists/{a}/hidden", json={"hidden": True})
        assert client.get(f"/api/work-lists/{a}").status_code == 200


def test_delete_refused_for_a_non_creator_with_the_words_saying_who_may(client, wls):
    a = wls.create("A list", ["x"], created_by="erin")["slug"]
    with as_user("peterprofile"):
        r = client.delete(f"/api/work-lists/{a}")
    assert r.status_code == 403
    assert "erin" in r.json()["detail"] and "admin" in r.json()["detail"] and "hide it for yourself" in r.json()["detail"]
    assert wls.get(a) is not None


def test_creator_deletes_and_the_response_keeps_the_egeria_guid_and_activity_says_so(client, wls):
    a = wls.create("Throwaway", ["x"], created_by="erin")["slug"]
    with wls._conn() as conn:
        conn.execute("UPDATE work_lists SET egeria_guid = ? WHERE slug = ?", ("g-1234", a))
    with as_user("erin"):
        client.put(f"/api/work-lists/{a}/hidden", json={"hidden": True})
        r = client.delete(f"/api/work-lists/{a}")
    assert r.status_code == 200
    assert r.json() == {"removed": a, "egeria_collection_kept": True, "egeria_guid": "g-1234", "deleted_by": "erin",
                       "activity_logged": True, "activity_error": ""}
    assert wls.get(a) is None
    with wls._conn() as conn:
        assert conn.execute("SELECT COUNT(*) FROM work_list_view WHERE work_list_slug = ?", (a,)).fetchone()[0] == 0
    entries = wls.registry.list_activity(entity_type="work_list", entity_slug=a)
    assert any("work list 'Throwaway' deleted by erin" in (e.get("summary") or "") for e in entries)


def test_a_suggestion_is_deleted_only_by_the_person_it_was_suggested_to(client, wls):
    s = wls.create("For gary", ["x"], created_by="erin", slug="suggested-to-garygeeke")["slug"]
    with as_user("peterprofile"):
        assert client.delete(f"/api/work-lists/{s}").status_code == 403
    with as_user("garygeeke"):
        assert client.delete(f"/api/work-lists/{s}").status_code == 200


def test_the_ddl_is_additive_and_a_second_start_is_a_no_op(tmp_path):
    db = str(tmp_path / "x.db")
    ProjectRegistry(db_path=db)
    ProjectRegistry(db_path=db)
    with ProjectRegistry(db_path=db)._conn() as conn:
        conn.execute("SELECT work_list_slug, user_id, hidden, updated_at FROM work_list_view").fetchall()


def test_served_text_carries_the_delete_button_confirm_and_hide_control():
    wl = (NEXT / "next" / "work-list-delete.js").read_text(encoding="utf-8")
    assert 'data-act="delete-list"' in wl and "Delete this list…" in wl
    assert "deleteListButtonHtml()" in (NEXT / "next" / "worklist.js").read_text(encoding="utf-8")
    assert "This cannot be undone." in wl and "confirmLabel: 'Delete list'" in wl
    app = (NEXT / "next" / "app.js").read_text(encoding="utf-8")
    assert "data-wl-hide=" in app and 'data-act="show-hidden-lists"' in app and "no work lists shown" in app
    api = (NEXT / "re-api.js").read_text(encoding="utf-8")
    assert "/hidden`" in api and "export const deleteWorkList" in api
