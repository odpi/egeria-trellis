"""Winnowing (DESIGN-WORK-LISTS-WINNOWING-AND-MIXED-KINDS §2, §6.2): a shortlist keeps the filter it was cut on.

Three buckets, never two: every member of the list it was cut from is kept, dropped or could-not-check, and a
could-not-check member carries its sentence. A stored filter that disagrees with the members beside it is refused
before anything is written. Re-applying is automatic when an answer refreshes, and a press too (owner, 2026-10-28);
every re-apply is recorded, and a member that can no longer be checked is held, never silently dropped.
"""
from __future__ import annotations

import json
import sqlite3

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from resource_explorer.registry import ProjectRegistry
from resource_explorer.winnowing import check_filter, column_types, criterion_words, filter_words
from resource_explorer.work_lists import WorkLists

CRIT = {"question_id": "Q-0010", "question": "How big is it?", "analysis_ids": ["schema_inventory"],
        "field": "measure:schema_inventory:relation_count", "op": ">=", "value": 10}


def _filter(kept, dropped=(), unchecked=(), *, parent_count=None, criteria=None, applied="2026-10-28T12:00:00+00:00"):
    unchecked = [{"entity_slug": s, "words": "never measured · run schema_inventory first"} for s in unchecked]
    return {"criteria": [dict(c) for c in (criteria or [CRIT])], "applied_at": applied, "oldest_answer": "2026-10-20T00:00:00+00:00",
            "kept": list(kept), "dropped": list(dropped), "could_not_check": unchecked,
            "parent_count": len(kept) + len(dropped) + len(unchecked) if parent_count is None else parent_count}


@pytest.fixture
def wls(tmp_path):
    return WorkLists(ProjectRegistry(db_path=str(tmp_path / "w.db")))


@pytest.fixture
def parent(wls):
    return wls.create("Sales databases", ["a", "b", "c", "d"], entity_type="database")


# ── check_filter ────────────────────────────────────────────────────────────


def test_a_good_filter_is_kept_with_all_three_buckets():
    got = check_filter(_filter(["a"], ["b", "c"], ["d"]))
    assert got["kept"] == ["a"] and got["dropped"] == ["b", "c"]
    assert got["could_not_check"] == [{"entity_slug": "d", "words": "never measured · run schema_inventory first"}]
    assert got["criteria"][0]["lens_dimension"] == ""        # accepted now, so a lens changes no shape later


@pytest.mark.parametrize("change, words", [
    (lambda f: f.update(criteria=[]), "no criteria"),
    (lambda f: f["criteria"][0].update(op="contains"), "a measure is filtered by"),
    (lambda f: f["criteria"][0].update(value="ten"), "needs a number"),
    (lambda f: f["criteria"][0].update(field="measure:other:x"), "does not answer this question"),
    (lambda f: f["criteria"][0].update(field="color"), "is not something a column is filtered by"),
    (lambda f: f["criteria"][0].update(surprise=1), "keys no reader understands"),
    (lambda f: f.update(could_not_check=[{"entity_slug": "d", "words": ""}]), "the sentence saying why"),
    (lambda f: f.update(dropped=["a"]), "two buckets"),
    (lambda f: f.update(parent_count=9), "every member is kept, dropped or could not be checked"),
    (lambda f: f.update(applied_at="yesterday"), "not an ISO date"),
])
def test_a_bad_filter_is_refused_in_words(change, words):
    f = _filter(["a"], ["b", "c"], ["d"])
    change(f)
    with pytest.raises(ValueError, match=words):
        check_filter(f)


def test_state_and_headline_criteria_are_checked_by_their_own_rules():
    state = dict(CRIT, field="state", op="is", value="answered")
    check_filter(_filter(["a"], criteria=[state]))
    with pytest.raises(ValueError, match="not an answer's state"):
        check_filter(_filter(["a"], criteria=[dict(state, value="not run")]))   # an absence, never a filter value
    head = dict(CRIT, field="headline", op="contains", value="orders")
    check_filter(_filter(["a"], criteria=[head]))
    with pytest.raises(ValueError, match="the words to look for"):
        check_filter(_filter(["a"], criteria=[dict(head, value=" ")]))


def test_words_say_what_it_was_cut_on_and_who_could_not_be_checked():
    f = check_filter(_filter(["a"], ["b", "c"], ["d"]))
    assert criterion_words(f["criteria"][0]) == "Q-0010 · relation_count at least 10"
    assert filter_words(f) == ("kept 1 of 4 on Q-0010 · relation_count at least 10 as of 2026-10-28"
                               " · 1 could not be checked")


# ── column_types ────────────────────────────────────────────────────────────


def test_column_types_read_declared_measures_and_absence_sentences():
    got = column_types("database", ["schema_inventory", "no_such_analysis"])
    inv = got["analyses"]["schema_inventory"]
    assert inv["declared"] and inv["result_kind"] == "list"
    assert "relation_count" in [m["value_field"] for m in inv["measures"]]
    assert inv["absence"]["not_measured"].startswith("Not measured")
    assert got["unknown"] == ["no_such_analysis"]           # listed, never dropped


def test_an_undeclared_analysis_offers_state_and_headline_and_says_so():
    got = column_types("repo", ["language_file_classification"])["analyses"]["language_file_classification"]
    assert got["declared"] is False and got["measures"] == []
    assert "state and headline only" in got["words"]


# ── promote with a filter ───────────────────────────────────────────────────


def test_promote_stores_the_filter_and_the_shortlist_says_what_it_was_cut_from(wls, parent):
    short = wls.promote(parent["slug"], ["a"], filter=_filter(["a"], ["b", "c"], ["d"]))
    assert [m["entity_slug"] for m in short["members"]] == ["a"]
    assert short["derived_from"] == parent["slug"]
    f = short["filter"]
    assert f["parent_slug"] == parent["slug"] and f["parent_count"] == 4
    assert [u["entity_slug"] for u in f["could_not_check"]] == ["d"]
    assert "filter_json" not in short and "filter_as_of" not in short
    with wls._conn() as conn:
        row = conn.execute("SELECT filter_as_of FROM work_lists WHERE slug = ?", (short["slug"],)).fetchone()
    assert row["filter_as_of"] == "2026-10-28T12:00:00+00:00"


def test_a_hand_picked_shortlist_has_no_filter(wls, parent):
    assert wls.promote(parent["slug"], ["a", "b"])["filter"] is None


@pytest.mark.parametrize("survivors, f, words", [
    (["a"], _filter(["a"], ["b", "c"]), "not in any bucket: d"),
    (["a"], _filter(["a"], ["b", "c", "zz"], parent_count=4), "not members: zz"),
    (["a", "b"], _filter(["a"], ["b", "c"], ["d"]), "exactly the filter's kept bucket"),
])
def test_a_filter_that_disagrees_with_the_list_creates_nothing(wls, parent, survivors, f, words):
    before = len(wls.list_all())
    with pytest.raises(ValueError, match=words):
        wls.promote(parent["slug"], survivors, filter=f)
    assert len(wls.list_all()) == before


def test_an_unparseable_stored_filter_is_said_never_read_as_none(wls, parent):
    short = wls.promote(parent["slug"], ["a"])
    with wls._conn() as conn:
        conn.execute("UPDATE work_lists SET filter_json = ? WHERE slug = ?", ("{not json", short["slug"]))
    got = wls.get(short["slug"])["filter"]
    assert got and "could not be read" in got["unreadable"]


# ── re-apply ────────────────────────────────────────────────────────────────


def test_reapply_moves_members_to_the_new_kept_bucket_and_keeps_old_reasons(wls, parent):
    short = wls.promote(parent["slug"], ["a", "b"], filter=_filter(["a", "b"], ["c"], ["d"]),
                        rationale="big enough")
    out = wls.reapply(short["slug"], _filter(["a", "c"], ["b", "d"], applied="2026-10-29T09:00:00+00:00"))
    assert out["joined"] == ["c"] and out["dropped"] == ["b"]
    by = {m["entity_slug"]: m for m in out["work_list"]["members"]}
    assert set(by) == {"a", "c"}
    assert by["a"]["rationale"] == "big enough"
    assert by["c"]["rationale"] == "joined when Q-0010 · relation_count at least 10 was re-applied"
    assert out["work_list"]["filter"]["applied_at"] == "2026-10-29T09:00:00+00:00"


def test_a_member_that_moves_to_could_not_check_is_held_and_every_reapply_is_recorded(wls, parent):
    short = wls.promote(parent["slug"], ["a", "b"], filter=_filter(["a", "b"], ["c"], ["d"]))
    f = _filter(["c"], ["b"], ["a", "d"], applied="2026-10-29T09:00:00+00:00")
    f["newest_answer"] = "2026-10-29T08:00:00+00:00"
    out = wls.reapply(short["slug"], f, automatic=True)
    assert out["joined"] == ["c"] and out["dropped"] == ["b"]
    assert out["held"] == [{"entity_slug": "a", "entity_type": "database",
                            "words": "never measured · run schema_inventory first"}]
    assert sorted(m["entity_slug"] for m in out["work_list"]["members"]) == ["a", "c"]   # a stays, said
    rec = out["work_list"]["filter"]["reapplies"]
    assert len(rec) == 1 and rec[0]["automatic"] is True
    assert rec[0]["answers_as_of"] == "2026-10-29T08:00:00+00:00" and rec[0]["could_not_check"] == 2
    wls.reapply(short["slug"], _filter(["c"], ["a", "b", "d"]))
    rec = wls.get(short["slug"])["filter"]["reapplies"]
    assert [r["automatic"] for r in rec] == [True, False]                 # newest last, pressed recorded too
    assert rec[1]["dropped"] == ["a"]                                    # checked again and failed: dropped


def test_the_reapply_record_keeps_the_last_twenty(wls, parent):
    from resource_explorer.work_lists import REAPPLY_HISTORY

    short = wls.promote(parent["slug"], ["a"], filter=_filter(["a"], ["b", "c", "d"]))
    for _ in range(REAPPLY_HISTORY + 3):
        wls.reapply(short["slug"], _filter(["a"], ["b", "c", "d"]), automatic=True)
    assert len(wls.get(short["slug"])["filter"]["reapplies"]) == REAPPLY_HISTORY


def test_reapply_refuses_a_list_with_no_filter_or_different_criteria(wls, parent):
    plain = wls.promote(parent["slug"], ["a"])
    with pytest.raises(ValueError, match="not cut by a filter"):
        wls.reapply(plain["slug"], _filter(["a"], ["b", "c", "d"]))
    short = wls.promote(parent["slug"], ["a"], filter=_filter(["a"], ["b", "c", "d"]))
    with pytest.raises(ValueError, match="a different filter is a new shortlist"):
        wls.reapply(short["slug"], _filter(["a"], ["b", "c", "d"], criteria=[dict(CRIT, value=20)]))
    with pytest.raises(KeyError):
        wls.reapply("wl-none", _filter(["a"]))


# ── the migration ───────────────────────────────────────────────────────────


def test_an_existing_work_lists_table_gains_the_filter_columns(tmp_path):
    db = str(tmp_path / "old.db")
    c = sqlite3.connect(db)
    c.execute("""CREATE TABLE work_lists (slug TEXT PRIMARY KEY, display_name TEXT NOT NULL,
        description TEXT NOT NULL DEFAULT '', entity_type TEXT NOT NULL DEFAULT 'repo',
        investigation TEXT NOT NULL DEFAULT '', created_by TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL,
        derived_from TEXT NOT NULL DEFAULT '', egeria_guid TEXT NOT NULL DEFAULT '',
        published_at TEXT NOT NULL DEFAULT '')""")
    c.execute("INSERT INTO work_lists (slug, display_name, created_at) VALUES ('old', 'Old', '2026-01-01')")
    c.commit()
    c.close()
    wls = WorkLists(ProjectRegistry(db_path=db))
    assert wls.get("old")["filter"] is None                  # an old list reads as "not cut by a filter"
    ProjectRegistry(db_path=db)                              # a second start is a no-op


# ── routes ──────────────────────────────────────────────────────────────────


@pytest.fixture
def client(wls, monkeypatch):
    from resource_explorer.web.routes import work_lists as routes

    monkeypatch.setattr(routes, "WorkLists", lambda: wls)
    app = FastAPI()
    app.include_router(routes.router, prefix="/api/work-lists")
    return TestClient(app)


def test_routes_promote_with_a_filter_and_reapply(client, parent):
    r = client.post(f"/api/work-lists/{parent['slug']}/promote",
                    json={"survivors": ["a"], "filter": _filter(["a"], ["b"], ["c", "d"])})
    assert r.status_code == 200, r.text
    short = r.json()
    assert short["filter"]["kept"] == ["a"]
    r = client.post(f"/api/work-lists/{short['slug']}/reapply",
                    json={"filter": _filter(["b"], ["a", "c", "d"]), "automatic": True})
    assert r.status_code == 200 and r.json()["joined"] == ["b"] and r.json()["dropped"] == ["a"]
    assert r.json()["reapply"]["automatic"] is True


def test_routes_say_422_with_the_words(client, parent):
    r = client.post(f"/api/work-lists/{parent['slug']}/promote",
                    json={"survivors": ["a"], "filter": _filter(["a"], ["b"])})
    assert r.status_code == 422 and "not in any bucket" in r.json()["detail"]
    assert client.post("/api/work-lists/wl-none/reapply", json={"filter": _filter(["a"])}).status_code == 404


def test_column_types_route_is_not_taken_for_a_work_list_slug(client):
    r = client.get("/api/work-lists/column-types?entity_type=database&analysis_ids=schema_inventory")
    assert r.status_code == 200 and "schema_inventory" in r.json()["analyses"]
    assert client.get("/api/work-lists/column-types?entity_type=planet").status_code == 422
    assert json.loads(client.get("/api/work-lists/column-types?entity_type=repo").text)["analyses"] == {}
