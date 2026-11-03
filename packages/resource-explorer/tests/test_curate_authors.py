"""Curate authors — tags, resource feedback and curator notes record WHO.

REPLY-DESIGNER-CURATE-AND-UNDERSTANDING-ALL-KINDS.md, "Two things to fix
before anything is drawn" item 1, plus the owner's 2026-10-01 rulings: journal
style notes are append-only (a signed note cannot be deleted; legacy unsigned
ones can), and the author comes from the session, never the request body.

Never touches the shared registry: every test uses a tmp SQLite file; the
Postgres migration is checked against a fake connection (no network).
"""
from __future__ import annotations

import inspect
import sqlite3

import pytest
from fastapi.testclient import TestClient

from resource_explorer.auth import create_access_token
from resource_explorer.registry import Project, ProjectRegistry

UNSIGNED = "unsigned · from before authors were recorded"


@pytest.fixture
def registry(tmp_path):
    r = ProjectRegistry(db_path=str(tmp_path / "t.db"))
    r.add(Project(slug="p", display_name="P", github_url="https://github.com/x/p", description=""))
    return r


@pytest.fixture
def client(registry, monkeypatch):
    monkeypatch.setattr(
        "resource_explorer.registry.ProjectRegistry.__init__",
        lambda self, db_path=None: setattr(self, "__dict__", registry.__dict__) or None,
    )
    from resource_explorer import auth
    from resource_explorer.web.app import app
    from resource_explorer.web.routes import curate as curate_routes

    # Isolation. routes/curate.py does `from resource_explorer.auth import
    # get_current_user`, binding the name ONCE, at the first import of the app.
    # Several earlier test files (test_curate.py, test_curate_blueprints_route.py,
    # test_reports.py, ...) monkeypatch `resource_explorer.auth.get_current_user`
    # to a fixed signed-in user *before* their first `from ...web.app import
    # app`, so when they run first the app is imported under that patch and
    # curate.py keeps the stub for the rest of the process (monkeypatch undoes
    # the auth attribute, never the already-bound name). Every signed-out case
    # here then saw "peterprofile" and the guard never fired. Rebind the route's
    # name to the real resolver for this test, and undo it afterwards.
    # `auth.get_current_user` is the real function here: any patch of it by a
    # previous test was reverted by that test's own monkeypatch teardown.
    monkeypatch.setattr(curate_routes, "get_current_user", auth.get_current_user)
    return TestClient(app)


def as_user(user_id: str) -> dict:
    return {"Authorization": "Bearer " + create_access_token(user_id=user_id, egeria_token="t")}


def _legacy_insert(registry, table, **cols):
    """Insert a row the way pre-author code did: no author column named."""
    names = ",".join(cols)
    marks = ",".join("?" for _ in cols)
    with registry._conn() as conn:
        conn.execute(f"INSERT INTO {table} ({names}) VALUES ({marks})", tuple(cols.values()))


# ── 401 when nobody is signed in, for each of the write routes ──────────

WRITE_CALLS = [
    ("add tag", "post", "/api/curate/tags/repo/p", {"tag": "x"}),
    ("delete tag", "delete", "/api/curate/tags/repo/p/x", None),
    ("feedback", "post", "/api/curate/feedback/repo/p", {"rating": 4, "message": "m"}),
    ("delete note", "delete", "/api/curate/notes/some-id", None),
]


@pytest.mark.parametrize("name,method,url,body", WRITE_CALLS, ids=[c[0] for c in WRITE_CALLS])
def test_signed_out_write_is_401_and_writes_nothing(client, registry, name, method, url, body):
    r = getattr(client, method)(url, **({"json": body} if body is not None else {}))
    assert r.status_code == 401, r.text
    assert registry.list_resource_tags("repo", "p") == []
    assert registry.list_resource_feedback("repo", "p") == []
    assert registry.list_curator_notes("repo", "p") == []


def test_signed_out_delete_does_not_remove_an_existing_legacy_note(client, registry):
    """The original hole: DELETE /notes/{id} deleted any note for anyone."""
    _legacy_insert(registry, "resource_curator_notes", id="old", entity_type="repo",
                   entity_slug="p", note="legacy", created_at="2026-01-01T00:00:00")
    assert client.delete("/api/curate/notes/old").status_code == 401
    assert len(registry.list_curator_notes("repo", "p")) == 1


# ── signed-in writes record the session user; the body cannot override ───────


def test_signed_in_writes_record_author(client):
    h = as_user("alice")
    assert client.post("/api/curate/tags/repo/p", json={"tag": "Gold"}, headers=h).status_code == 200
    assert client.post("/api/curate/feedback/repo/p", json={"rating": 5, "message": "m"}, headers=h).status_code == 200
    tag = client.get("/api/curate/tags-detail/repo/p").json()[0]
    fb = client.get("/api/curate/feedback/repo/p").json()[0]
    for row in (tag, fb):
        assert row["author"] == "alice" and row["authored"] is True
        assert row["author_label"] == "alice"


def test_body_author_is_ignored_and_users_are_not_mixed(client):
    a, b = as_user("alice"), as_user("bob")
    client.post("/api/curate/feedback/repo/p", json={"message": "fa", "author": "bob"}, headers=a)
    client.post("/api/curate/feedback/repo/p", json={"message": "fb", "author": "alice"}, headers=b)
    client.post("/api/curate/tags/repo/p", json={"tag": "t", "author": "bob"}, headers=a)
    authors = {f["message"]: f["author"] for f in client.get("/api/curate/feedback/repo/p").json()}
    assert authors == {"fa": "alice", "fb": "bob"}
    assert client.get("/api/curate/tags-detail/repo/p").json()[0]["author"] == "alice"


def test_tag_removal_records_who_removed_it(client, registry):
    client.post("/api/curate/tags/repo/p", json={"tag": "t"}, headers=as_user("alice"))
    r = client.delete("/api/curate/tags/repo/p/t", headers=as_user("bob"))
    assert r.status_code == 200 and r.json()["removed_by"] == "bob"
    acts = registry.list_activity(entity_type="repo", entity_slug="p", operation="curate_tag_removed")
    assert acts and "bob" in acts[0]["summary"]


# ── legacy rows read back unsigned, never blank ──────────────────────────────


def test_legacy_rows_read_back_unsigned(client, registry):
    _legacy_insert(registry, "resource_tags", entity_type="repo", entity_slug="p",
                   tag="old", created_at="2026-01-01T00:00:00")
    _legacy_insert(registry, "resource_feedback", id="f1", entity_type="repo", entity_slug="p",
                   rating=3, category="", message="old", created_at="2026-01-01T00:00:00")
    _legacy_insert(registry, "resource_curator_notes", id="n1", entity_type="repo", entity_slug="p",
                   note="old", created_at="2026-01-01T00:00:00")
    rows = (client.get("/api/curate/tags-detail/repo/p").json()
            + client.get("/api/curate/feedback/repo/p").json()
            + client.get("/api/curate/notes/repo/p").json())
    assert len(rows) == 3
    for row in rows:
        assert row["author"] is None
        assert row["authored"] is False
        assert row["author_label"] == UNSIGNED


# ── notes are append-only once signed; legacy unsigned are deletable ─────────


def test_signed_note_cannot_be_deleted_even_by_its_author(client, registry):
    h = as_user("alice")
    nid = registry.add_curator_note("repo", "p", "n", author="alice")["id"]
    for who in ("alice", "bob"):
        r = client.delete(f"/api/curate/notes/{nid}", headers=as_user(who))
        assert r.status_code == 409
        assert "append-only" in r.json()["detail"]
    assert len(registry.list_curator_notes("repo", "p")) == 1


def test_registry_delete_itself_refuses_a_signed_note(registry):
    entry = registry.add_curator_note("repo", "p", "n", author="alice")
    assert registry.delete_curator_note(entry["id"]) is False
    assert len(registry.list_curator_notes("repo", "p")) == 1


def test_legacy_unsigned_note_can_be_deleted_by_a_signed_in_user(client, registry):
    _legacy_insert(registry, "resource_curator_notes", id="n1", entity_type="repo",
                   entity_slug="p", note="old", created_at="2026-01-01T00:00:00")
    assert client.delete("/api/curate/notes/n1", headers=as_user("alice")).status_code == 200
    assert registry.list_curator_notes("repo", "p") == []
    assert client.delete("/api/curate/notes/n1", headers=as_user("alice")).status_code == 404


def test_there_is_no_note_edit_route(client):
    assert client.put("/api/curate/notes/x", json={"note": "n"}, headers=as_user("a")).status_code in (404, 405)
    assert client.patch("/api/curate/notes/x", json={"note": "n"}, headers=as_user("a")).status_code in (404, 405)


# ── guard: a new curate write route must call the sign-in guard ──────────────

#: Write routes with their own, stronger authorization (workflow-layer curation
#: rights, 403), not part of the tags/feedback/notes family.
_OWN_AUTHORIZATION = {"add_blueprint_verdict"}


def test_every_curate_write_route_has_the_signin_guard():
    from resource_explorer.web.routes import curate
    write_endpoints = []
    for route in curate.router.routes:
        if route.methods & {"POST", "PUT", "PATCH", "DELETE"}:
            write_endpoints.append(route.endpoint)
    names = {fn.__name__ for fn in write_endpoints}
    # The four this slice covers must be present, so renaming cannot hide them.
    assert {"add_tag", "remove_tag", "add_feedback", "delete_note"} <= names
    unguarded = [fn.__name__ for fn in write_endpoints
                 if fn.__name__ not in _OWN_AUTHORIZATION
                 and "_require_author(" not in inspect.getsource(fn)]
    assert unguarded == [], f"curate write routes without the sign-in guard: {unguarded}"


def test_guard_check_fails_on_an_unguarded_route():
    """Known-negative for the check above: it must flag a route without the call."""
    def sneaky_write():
        return None
    assert "_require_author(" not in inspect.getsource(sneaky_write)


def test_the_tag_routes_save_in_re_only_and_publish_sends():
    """The owner's 2026-10-01 no-op seam was superseded on 2026-10-10 (Brief T: "tags should be public"), and Brief F
    (2026-10-11, one Publish per page) moved the send to the page's Publish: the tag routes save in RE and answer
    the plan's state; nothing on Save calls Egeria (tests/test_tags_groups_to_egeria.py)."""
    import inspect

    from resource_explorer.web.routes import curate
    assert not hasattr(curate, "_publish_curation_to_egeria")
    assert not hasattr(curate, "sync_to_egeria")
    for fn in (curate.add_tag, curate.remove_tag):
        src = inspect.getsource(fn)
        assert "curation_state(" in src and "sync_curation" not in src


# ── migration ────────────────────────────────────────────────────────────────

_OLD_SCHEMA = [
    "CREATE TABLE resource_tags (entity_type TEXT NOT NULL, entity_slug TEXT NOT NULL, tag TEXT NOT NULL, created_at TEXT NOT NULL, PRIMARY KEY (entity_type, entity_slug, tag))",
    "CREATE TABLE resource_feedback (id TEXT PRIMARY KEY, entity_type TEXT NOT NULL, entity_slug TEXT NOT NULL, rating INTEGER DEFAULT NULL, category TEXT DEFAULT '', message TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL)",
    "CREATE TABLE resource_curator_notes (id TEXT PRIMARY KEY, entity_type TEXT NOT NULL, entity_slug TEXT NOT NULL, note TEXT NOT NULL, created_at TEXT NOT NULL)",
]


def test_sqlite_migration_adds_author_keeps_rows_and_is_idempotent(tmp_path):
    path = str(tmp_path / "old.db")
    raw = sqlite3.connect(path)
    for ddl in _OLD_SCHEMA:
        raw.execute(ddl)
    raw.execute("INSERT INTO resource_tags VALUES ('repo','p','old','2026-01-01')")
    raw.execute("INSERT INTO resource_curator_notes VALUES ('n','repo','p','old note','2026-01-01')")
    raw.commit(); raw.close()

    for _ in range(2):  # second open proves idempotence
        reg = ProjectRegistry(db_path=path)
    cols = lambda t: {r[1] for r in sqlite3.connect(path).execute(f"PRAGMA table_info({t})")}
    for t in ("resource_tags", "resource_feedback", "resource_curator_notes"):
        assert "author" in cols(t)
    assert reg.list_resource_tags_with_authors("repo", "p")[0]["authored"] is False
    assert reg.list_curator_notes("repo", "p")[0]["author_label"] == UNSIGNED


def test_postgres_migration_sql_without_a_connection():
    """Dialect check against a fake connection: on Postgres the column list is
    read from information_schema, and the ALTER is skipped when present."""
    executed = []

    class _Rows:
        def __init__(self, rows): self._rows = rows
        def fetchall(self): return self._rows

    class _PgConn:
        is_postgres = True
        def __init__(self, existing): self.existing = existing
        def execute(self, sql, params=()):
            executed.append((" ".join(sql.split()), params))
            if "information_schema.columns" in sql:
                return _Rows([{"column_name": c} for c in self.existing])
            return _Rows([])

    reg = ProjectRegistry.__new__(ProjectRegistry)
    reg._add_author_column(_PgConn({"id", "note"}), "resource_curator_notes")
    assert executed[0][1] == ("resource_curator_notes",)
    assert executed[1][0] == "ALTER TABLE resource_curator_notes ADD COLUMN author TEXT DEFAULT NULL"

    executed.clear()
    reg._add_author_column(_PgConn({"id", "author"}), "resource_curator_notes")
    assert len(executed) == 1 and "information_schema" in executed[0][0]  # no ALTER on re-run
