"""TR-120: the default binding "Create in Egeria" only records intent; the Egeria Project is created when the
investigation is promoted. An API create, and the page, showed an empty egeria_project_guid with no explanation, and
the page read "local". The WORDS are fixed, not the binding: no auto-promote, no change to when the Project is made.

No Egeria and no Postgres: a sqlite registry like tests/test_open_data.py.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from test_open_data import reg  # noqa: E402,F401  (the sqlite registry fixture is shared)

from resource_explorer.registry import ProjectRegistry  # noqa: E402

NEXT = Path(__file__).parent.parent / "resource_explorer" / "web" / "static" / "next"
WORDS = "Egeria Project not created yet: Publish to Egeria creates it"


@pytest.fixture
def client(reg, monkeypatch):
    from fastapi.testclient import TestClient

    from resource_explorer.web.app import app
    from resource_explorer.web.routes import investigations as routes

    monkeypatch.setattr(routes, "_registry", lambda: reg)
    return TestClient(app)


def test_an_api_create_with_the_egeria_binding_says_the_project_is_not_created_yet(client):
    r = client.post("/api/investigations/", json={"display_name": "Words", "egeria_binding": "egeria"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["egeria_project_guid"] in ("", None)               # still empty: nothing was created
    assert body["egeria_project_words"] == WORDS
    assert body["egeria_binding"] == "egeria"


def test_the_default_binding_is_egeria_so_a_plain_create_says_it_too(client):
    body = client.post("/api/investigations/", json={"display_name": "Default"}).json()
    assert body["egeria_binding"] == "egeria" and body["egeria_project_words"] == WORDS


def test_a_get_and_a_list_carry_the_same_words(client):
    slug = client.post("/api/investigations/", json={"display_name": "Reread"}).json()["slug"]
    assert client.get(f"/api/investigations/{slug}").json()["egeria_project_words"] == WORDS
    assert [i for i in client.get("/api/investigations/").json() if i["slug"] == slug][0]["egeria_project_words"] == WORDS


def test_a_local_investigation_is_not_told_a_project_is_coming(client):
    body = client.post("/api/investigations/", json={"display_name": "Mine", "egeria_binding": "local"}).json()
    assert "egeria_project_words" not in body


def test_once_the_project_exists_the_words_go_away(reg):
    inv = reg.create_investigation("Promoted", egeria_binding="egeria")
    assert inv["egeria_project_words"] == WORDS
    with reg._conn() as conn:
        conn.execute("UPDATE investigations SET egeria_project_guid = 'g-1' WHERE slug = ?", (inv["slug"],))
    again = reg.get_investigation(inv["slug"])
    assert again["egeria_project_guid"] == "g-1" and "egeria_project_words" not in again


def test_after_an_egeria_reset_the_unbound_state_keeps_its_own_words(reg):
    inv = reg.create_investigation("Reset", egeria_binding="egeria")
    with reg._conn() as conn:
        conn.execute("UPDATE investigations SET egeria_project_status = 'unbound' WHERE slug = ?", (inv["slug"],))
    assert "egeria_project_words" not in reg.get_investigation(inv["slug"])


def test_the_words_come_from_one_constant(reg):
    assert ProjectRegistry.EGERIA_PROJECT_NOT_CREATED_WORDS == WORDS


def test_no_promote_happens_and_no_guid_is_made_by_a_create(client, reg):
    slug = client.post("/api/investigations/", json={"display_name": "No auto promote"}).json()["slug"]
    row = reg.get_investigation(slug)
    assert not row["egeria_project_guid"] and row["egeria_context"]["egeria_project_guid"] == ""


def test_the_choice_is_relabelled_and_the_old_label_is_gone(client):
    bindings = client.get("/api/investigations/classifications").json()["bindings"]
    by_name = {b["name"]: b for b in bindings}
    assert by_name["egeria"]["label"] == "Egeria Project (created when you publish)"
    assert "Create in Egeria" not in by_name["egeria"]["label"]
    assert by_name["local"]["label"] == "Ad-hoc"                      # the other choice is untouched
    assert [b["name"] for b in bindings] == ["egeria", "local"]       # same choices, same order, same names


def test_the_page_script_shows_the_server_words_not_a_local_glyph_for_an_unpromoted_egeria_investigation():
    js = (NEXT / "stages" / "investigation.js").read_text(encoding="utf-8")
    assert "inv.egeria_project_words" in js and "data-inv-egeria-words" in js
    assert "No Egeria Project yet — local only." in js            # still said when there are no server words
