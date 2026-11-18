"""The router factory: who may do what (Q3), identity from the session only, and external rows."""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from trellis_feedback import FeedbackError
from trellis_feedback.router import make_router


@pytest.fixture
def setup(store):
    """An app with a fake session: the `X-Test-User` header stands in for the app's own sign-in,
    and `peterprofile` is the only admin. `rows` is RE's resource_feedback table, as a set of ids."""
    rows = {"1", "2"}

    def current_user(request):
        return request.headers.get("X-Test-User", "")

    def is_admin(request):
        return current_user(request) == "peterprofile"

    app = FastAPI()
    app.include_router(
        make_router(store, is_admin=is_admin, current_user=current_user,
                    external_lookup={"resource_feedback": lambda ids: [i for i in ids if i in rows]}),
        prefix="/api/fb",
    )
    return TestClient(app), rows


ADMIN = {"X-Test-User": "peterprofile"}
USER = {"X-Test-User": "erinoverview"}


def submit(client, headers=USER, **body):
    payload = {"kind": "answer", "vote": -1, "query_text": "q?", "response_text": "a."}
    payload.update(body)
    return client.post("/api/fb/items", json=payload, headers=headers)


def test_anyone_signed_in_may_submit_and_the_user_comes_from_the_session(setup):
    client, _ = setup
    r = submit(client, user_id="someone-else")        # a client-sent user is never read
    assert r.status_code == 200, r.text
    assert r.json()["item"]["user_id"] == "erinoverview"


def test_a_retried_submit_with_its_key_is_not_duplicated_and_returns_only_the_id(setup):
    client, _ = setup
    first = submit(client, idempotency_key="abc").json()
    again = submit(client, idempotency_key="abc").json()
    assert first["created"] and not again["created"]
    assert again["item"] == {"id": first["item"]["id"]}


def test_replaying_another_users_key_returns_nothing_of_theirs(setup):
    # 0.1 read-back P2.
    client, _ = setup
    mine = submit(client, headers=USER, idempotency_key="k", query_text="private question",
                  response_text="private answer", email="erin@example.com").json()
    theirs = submit(client, headers={"X-Test-User": "mallory"}, idempotency_key="k",
                    query_text="mallory's own").json()
    assert theirs["created"] and theirs["item"]["id"] != mine["item"]["id"]
    assert "private" not in str(theirs) and "erin@example.com" not in str(theirs)


def test_an_anonymous_replay_returns_only_the_id(setup):
    # Every anonymous caller is the user "", so a replay must not echo the stored item.
    client, _ = setup
    first = submit(client, headers={}, idempotency_key="anon", query_text="anonymous secret").json()
    again = submit(client, headers={}, idempotency_key="anon", query_text="someone else").json()
    assert not again["created"] and again == {"item": {"id": first["item"]["id"]}, "created": False}


def test_a_bad_submission_is_a_400_not_a_500(setup):
    client, _ = setup
    assert submit(client, vote=7).status_code == 400


@pytest.mark.parametrize("method, path, body", [
    ("get", "/api/fb/items", None),
    ("get", "/api/fb/items/x", None),
    ("delete", "/api/fb/items/x", None),
    ("post", "/api/fb/triage", {"ref_id": "x", "status": "actioned"}),
    ("get", "/api/fb/external/resource_feedback", None),
    ("delete", "/api/fb/external/resource_feedback/9", None),
])
@pytest.mark.parametrize("headers", [USER, {}])
def test_reading_triage_and_deletion_are_admin_only(setup, method, path, body, headers):
    # Q3: only admins read other users' questions and answers, whatever the login policy -- an
    # anonymous caller (the app's login switched off) is refused here too.
    client, _ = setup
    r = client.request(method, path, json=body, headers=headers)
    assert r.status_code == 403


def test_an_admin_lists_triages_and_reads_the_history(setup):
    client, _ = setup
    item_id = submit(client).json()["item"]["id"]
    r = client.post("/api/fb/triage", json={"ref_id": item_id, "status": "triaged", "note": "seen"}, headers=ADMIN)
    assert r.status_code == 200, r.text
    assert r.json()["event"]["actor"] == "peterprofile"
    submit(client)                         # a second, untriaged item
    listing = client.get("/api/fb/items", params={"status": "triaged"}, headers=ADMIN).json()
    assert [x["id"] for x in listing["items"]] == [item_id]
    assert (listing["total"], listing["matched"], listing["filtered"]) == (2, 1, True)
    detail = client.get(f"/api/fb/items/{item_id}", headers=ADMIN).json()
    assert detail["triage"]["status"] == "triaged"
    assert [e["note"] for e in detail["history"]] == ["seen"]


def test_an_unknown_status_is_a_400(setup):
    client, _ = setup
    item_id = submit(client).json()["item"]["id"]
    r = client.post("/api/fb/triage", json={"ref_id": item_id, "status": "resolved"}, headers=ADMIN)
    assert r.status_code == 400


def test_deleting_an_item_through_the_route_takes_its_history(setup, store):
    client, _ = setup
    item_id = submit(client).json()["item"]["id"]
    client.post("/api/fb/triage", json={"ref_id": item_id, "status": "triaged"}, headers=ADMIN)
    assert client.delete(f"/api/fb/items/{item_id}", headers=ADMIN).status_code == 200
    assert store.history(item_id) == []
    assert client.get(f"/api/fb/items/{item_id}", headers=ADMIN).status_code == 404


def test_external_rows_triage_gone_and_clear(setup):
    client, rows = setup
    ok = client.post("/api/fb/triage", json={"ref_id": "1", "source": "resource_feedback",
                                             "status": "known_issue", "note": "dup"}, headers=ADMIN)
    assert ok.status_code == 200, ok.text
    # a row that does not exist cannot take a status
    missing = client.post("/api/fb/triage", json={"ref_id": "99", "source": "resource_feedback",
                                                  "status": "actioned"}, headers=ADMIN)
    assert missing.status_code == 404
    # its history cannot be cleared while the row still exists
    assert client.delete("/api/fb/external/resource_feedback/1", headers=ADMIN).status_code == 409

    rows.discard("1")                    # RE deletes the row itself, outside the library (Q7)
    view = client.get("/api/fb/external/resource_feedback", params={"ids": "2"}, headers=ADMIN).json()
    assert view["states"]["2"]["status"] == "new"
    assert [(g["ref_id"], g["triage"]["status"], [e["note"] for e in g["history"]]) for g in view["gone"]] \
        == [("1", "known_issue", ["dup"])]

    cleared = client.delete("/api/fb/external/resource_feedback/1", headers=ADMIN)
    assert cleared.status_code == 200 and cleared.json()["deleted_events"] == 1
    assert client.get("/api/fb/external/resource_feedback", headers=ADMIN).json()["gone"] == []


def test_meta_lists_the_vocabularies(setup):
    client, _ = setup
    meta = client.get("/api/fb/meta", headers=USER).json()
    assert meta["statuses"][0] == "new" and "triaged" in meta["statuses"]
    assert meta["sources"] == ["item", "resource_feedback"]


def test_a_lookup_for_an_undeclared_source_is_refused_at_construction(store):
    with pytest.raises(FeedbackError):
        make_router(store, is_admin=lambda r: True, current_user=lambda r: "",
                    external_lookup={"other_table": lambda ids: ids})
