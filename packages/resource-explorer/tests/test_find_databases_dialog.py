"""Find databases dialog, slice 1 (server side).

REPLY-DESIGNER-DISCOVERY-SOURCES-ALL-KINDS.md §2 and slice 1:

* `list_databases()` LISTS the databases the credential cannot CONNECT to, with
  their mark, and `size_bytes`/`owner` lose their 0 / '' defaults (None = not read);
* a saved source (a registered db server) remembers its last run's candidate
  set, so Run can say "n new since <date>" (migration: additive, nullable,
  SQLite and Postgres);
* the prior verdict is keyed by resource_key('database', 'host:port/name');
* credentials never appear in a response.

Never touches the shared registry or a real database server: every registry is a
tmp SQLite file, server discovery is stubbed, the Postgres migration is checked
against a fake connection (no network).
"""
from __future__ import annotations

import contextlib
import json
import sqlite3

import pytest
from fastapi.testclient import TestClient

from resource_explorer.batch_io import resource_key
from resource_explorer.registry import DatabaseServer, ProjectRegistry
from resource_explorer.surveyors.database.connection import PostgreSQLConnection

SECRET = "hunter2-do-not-leak"


# ── list_databases(): non-connectable are listed; no 0 / '' defaults ─────────

def _conn_returning(rows):
    conn = PostgreSQLConnection.__new__(PostgreSQLConnection)
    seen = {}

    def execute_query(query, params=None):
        seen["query"] = query
        return rows

    conn.execute_query = execute_query
    return conn, seen


ROWS = [
    {"name": "coco_pharma", "can_connect": True, "size_pretty": "22 MB", "size_bytes": 23068672,
     "owner": "postgres", "description": "Pharma sample", "encoding": "UTF8"},
    {"name": "region_east", "can_connect": False, "size_pretty": None, "size_bytes": None,
     "owner": "east_dba", "description": None, "encoding": "UTF8"},
    {"name": "empty_one", "can_connect": True, "size_pretty": "7 MB", "size_bytes": 0,
     "owner": None, "description": None, "encoding": None},
]


def test_list_databases_lists_non_connectable_with_their_mark():
    conn, _ = _conn_returning(ROWS)
    out = {d["name"]: d for d in conn.list_databases()}
    assert set(out) == {"coco_pharma", "region_east", "empty_one"}
    assert out["region_east"]["can_connect"] is False
    assert out["coco_pharma"]["can_connect"] is True


def test_list_databases_does_not_filter_in_sql_and_guards_the_size_read():
    conn, seen = _conn_returning(ROWS)
    conn.list_databases()
    q = " ".join(seen["query"].split())
    # The old filter. A database dropped here is a database a steward is never told about.
    assert "AND has_database_privilege" not in q
    assert "WHERE d.datistemplate = false ORDER BY" in q
    # pg_database_size() raises without CONNECT, so the size read is guarded, not unconditional.
    assert "CASE WHEN has_database_privilege" in q


def test_unread_size_and_owner_are_none_not_zero_or_blank():
    conn, _ = _conn_returning(ROWS)
    out = {d["name"]: d for d in conn.list_databases()}
    east = out["region_east"]
    assert east["size_bytes"] is None and east["size_pretty"] is None
    blank = out["empty_one"]
    assert blank["owner"] is None and blank["encoding"] is None


def test_a_measured_zero_size_stays_zero_and_unread_is_distinct():
    """Known-negative: the fix must not turn a real 0 into 'not read' either."""
    conn, _ = _conn_returning(ROWS)
    out = {d["name"]: d for d in conn.list_databases()}
    assert out["empty_one"]["size_bytes"] == 0
    assert out["region_east"]["size_bytes"] is None
    assert out["empty_one"]["size_bytes"] != out["region_east"]["size_bytes"]


def test_description_empty_is_a_measurement_not_none():
    """shobj_description is world-readable, so NULL from the server means 'none
    set' (''), distinct from a source that never returned the field (None)."""
    conn, _ = _conn_returning(ROWS)
    out = {d["name"]: d for d in conn.list_databases()}
    assert out["region_east"]["description"] == ""
    assert out["coco_pharma"]["description"] == "Pharma sample"


# ── migration: additive, nullable, idempotent; SQLite and Postgres ───────────

_OLD_DB_SERVERS = (
    "CREATE TABLE db_servers (slug TEXT PRIMARY KEY, display_name TEXT NOT NULL, "
    "db_type TEXT NOT NULL DEFAULT 'postgresql', host TEXT NOT NULL, port INTEGER NOT NULL DEFAULT 5432, "
    "description TEXT DEFAULT '', db_user TEXT DEFAULT '', db_password TEXT DEFAULT '', "
    "egeria_host TEXT DEFAULT '', egeria_url TEXT DEFAULT '', egeria_server TEXT DEFAULT '', "
    "egeria_user TEXT DEFAULT '', egeria_password TEXT DEFAULT '', status TEXT DEFAULT 'active', "
    "registered_at TEXT NOT NULL, error_message TEXT DEFAULT '', group_slug TEXT DEFAULT '')"
)


def test_sqlite_migration_adds_nullable_columns_keeps_rows_and_is_idempotent(tmp_path):
    path = str(tmp_path / "old.db")
    raw = sqlite3.connect(path)
    raw.execute(_OLD_DB_SERVERS)
    raw.execute("INSERT INTO db_servers (slug, display_name, host, registered_at) "
                "VALUES ('regional_pg','Regional','pg.example',  '2026-09-01T00:00:00')")
    raw.commit(); raw.close()

    for _ in range(2):  # the second open proves idempotence
        reg = ProjectRegistry(db_path=path)
    cols = {r[1]: r for r in sqlite3.connect(path).execute("PRAGMA table_info(db_servers)")}
    assert "last_run_at" in cols and "last_run_candidates" in cols
    for c in ("last_run_at", "last_run_candidates"):
        assert cols[c][3] == 0, f"{c} must be nullable"      # notnull flag
    srv = reg.get_server("regional-pg")
    # An existing source has never run: None, which is not "ran and found nothing".
    assert srv.last_run_at is None and srv.last_run_candidates is None


def test_postgres_migration_sql_without_a_connection():
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
    reg._add_server_last_run_columns(_PgConn({"slug", "host"}))
    assert executed[0][1] == ("db_servers",)
    alters = [e[0] for e in executed if e[0].startswith("ALTER")]
    assert alters == [
        "ALTER TABLE db_servers ADD COLUMN last_run_at TEXT DEFAULT NULL",
        "ALTER TABLE db_servers ADD COLUMN last_run_candidates TEXT DEFAULT NULL",
    ]

    executed.clear()
    reg._add_server_last_run_columns(_PgConn({"slug", "last_run_at", "last_run_candidates"}))
    assert len(executed) == 1 and "information_schema" in executed[0][0]   # no ALTER on re-run

    executed.clear()   # known-negative: only the missing one is added
    reg._add_server_last_run_columns(_PgConn({"slug", "last_run_at"}))
    assert [e[0] for e in executed if e[0].startswith("ALTER")] == [
        "ALTER TABLE db_servers ADD COLUMN last_run_candidates TEXT DEFAULT NULL"]


# ── saved-source last-run storage ────────────────────────────────────────────

@pytest.fixture
def registry(tmp_path):
    r = ProjectRegistry(db_path=str(tmp_path / "t.db"))
    r.register_server(DatabaseServer(slug="regional-pg", display_name="Regional PG",
                                      host="pg.regional", port=5432, db_user="scout_ro",
                                      db_password=SECRET, group_slug="regional"))
    return r


def test_never_run_is_none_and_ran_empty_is_an_empty_list(registry):
    assert registry.get_server("regional-pg").last_run_candidates is None
    registry.record_server_run("regional-pg", "2026-10-01T09:12:00", [])
    srv = registry.get_server("regional-pg")
    assert srv.last_run_candidates == [] and srv.last_run_at == "2026-10-01T09:12:00"


def test_a_run_replaces_the_previous_set_and_round_trips(registry):
    registry.record_server_run("regional-pg", "2026-09-28T10:00:00", ["b:1/x", "b:1/y", "b:1/x"])
    assert registry.get_server("regional-pg").last_run_candidates == ["b:1/x", "b:1/y"]
    registry.record_server_run("regional-pg", "2026-10-01T09:12:00", ["b:1/z"])
    srv = registry.get_server("regional-pg")
    assert srv.last_run_candidates == ["b:1/z"] and srv.last_run_at == "2026-10-01T09:12:00"
    assert [s.last_run_candidates for s in registry.list_servers()] == [["b:1/z"]]


def test_storage_is_per_source(registry):
    registry.register_server(DatabaseServer(slug="other", display_name="Other", host="o", db_user="u"))
    registry.record_server_run("regional-pg", "2026-10-01T09:12:00", ["k"])
    assert registry.get_server("other").last_run_candidates is None


# ── the routes ───────────────────────────────────────────────────────────────

@pytest.fixture
def client(registry, monkeypatch):
    monkeypatch.setattr(
        "resource_explorer.registry.ProjectRegistry.__init__",
        lambda self, db_path=None: setattr(self, "__dict__", registry.__dict__) or None,
    )
    from resource_explorer.web.app import app
    return TestClient(app)


class _Listing:
    """Stands in for the server: whatever `rows` holds is what list_databases() returns."""
    rows: list[dict] = []
    fail_with: str | None = None
    seen: list[tuple] = []


def _row(name, *, can_connect=True, size="10 MB", size_bytes=10485760, owner="postgres", description=""):
    return {"name": name, "can_connect": can_connect, "size_pretty": size if can_connect else None,
            "size_bytes": size_bytes if can_connect else None, "owner": owner,
            "description": description, "encoding": "UTF8"}


@pytest.fixture(autouse=True)
def stub_server(monkeypatch):
    _Listing.rows = [_row("a_forecast", description="Monthly"), _row("b_forecast")]
    _Listing.fail_with = None
    _Listing.seen = []

    class _Conn:
        def list_databases(self):
            return list(_Listing.rows)

    @contextlib.contextmanager
    def fake_server_connection(host, port, user, password, db_type="postgresql"):
        _Listing.seen.append((host, port, user))
        if _Listing.fail_with:
            raise RuntimeError(_Listing.fail_with.replace("{pw}", password))
        yield _Conn()

    monkeypatch.setattr(
        "resource_explorer.surveyors.database.connection.server_connection", fake_server_connection)


def test_first_run_has_nothing_to_compare_with(client, registry):
    r = client.post("/api/db-servers/regional-pg/run")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["first_run"] is True and body["previous_run_at"] is None
    assert body["new_count"] is None          # not 0: there is no baseline, which is not "nothing new"
    assert body["candidate_count"] == 2
    assert all(c["is_new"] is None for c in body["candidates"])
    srv = registry.get_server("regional-pg")
    assert srv.last_run_at == body["run_at"]
    assert sorted(srv.last_run_candidates) == [
        "pg.regional:5432/a_forecast", "pg.regional:5432/b_forecast"]


def test_immediate_rerun_reports_zero_new_since_the_first_run(client):
    first = client.post("/api/db-servers/regional-pg/run").json()
    again = client.post("/api/db-servers/regional-pg/run").json()
    assert again["first_run"] is False
    assert again["new_count"] == 0
    assert again["previous_run_at"] == first["run_at"]
    assert all(c["is_new"] is False for c in again["candidates"])


def test_a_database_that_appeared_since_is_counted_new_and_flagged(client):
    client.post("/api/db-servers/regional-pg/run")
    _Listing.rows.append(_row("c_forecast"))
    third = client.post("/api/db-servers/regional-pg/run").json()
    assert third["new_count"] == 1
    assert {c["name"]: c["is_new"] for c in third["candidates"]} == {
        "a_forecast": False, "b_forecast": False, "c_forecast": True}
    # ...and the baseline moved on: the fourth run has nothing new.
    assert client.post("/api/db-servers/regional-pg/run").json()["new_count"] == 0


def test_a_failed_run_stores_nothing_and_leaves_the_baseline(client, registry):
    client.post("/api/db-servers/regional-pg/run")
    before = registry.get_server("regional-pg")
    _Listing.fail_with = "FATAL: password authentication failed for {pw}"
    r = client.post("/api/db-servers/regional-pg/run")
    assert r.status_code == 500
    after = registry.get_server("regional-pg")
    assert (after.last_run_at, after.last_run_candidates) == (before.last_run_at, before.last_run_candidates)


def test_no_credential_is_a_400_and_stores_nothing(client, registry):
    registry.register_server(DatabaseServer(slug="bare", display_name="Bare", host="h"))
    r = client.post("/api/db-servers/bare/run")
    assert r.status_code == 400 and "no stored credentials" in r.json()["detail"]
    assert registry.get_server("bare").last_run_at is None
    assert _Listing.seen == []            # never even connected


def test_unknown_source_is_404(client):
    assert client.post("/api/db-servers/nope/run").status_code == 404


def test_non_connectable_database_is_a_candidate_with_unread_facts(client):
    _Listing.rows = [_row("ok_db"), _row("region_east", can_connect=False, owner="east_dba")]
    body = client.post("/api/db-servers/regional-pg/run").json()
    east = next(c for c in body["candidates"] if c["name"] == "region_east")
    assert east["can_connect"] is False
    assert east["size_pretty"] is None and east["size_bytes"] is None   # not 0, not ''
    assert east["owner"] == "east_dba"
    assert east["description"] == ""                                    # measured empty
    ok = next(c for c in body["candidates"] if c["name"] == "ok_db")
    assert ok["can_connect"] is True and ok["size_bytes"] == 10485760


def test_already_registered_is_flagged_by_address_not_only_by_server(client, registry):
    from resource_explorer.registry import DatabaseEntity
    # Registered standalone (no server_slug) under the same host:port/name.
    registry.register_database(DatabaseEntity(
        slug="a_standalone", display_name="A", db_type="postgresql", host="pg.regional",
        port=5432, database_name="a_forecast"))
    body = client.post("/api/db-servers/regional-pg/run").json()
    by = {c["name"]: c for c in body["candidates"]}
    assert by["a_forecast"]["is_registered"] is True
    assert by["a_forecast"]["registered_slug"] == "a_standalone"
    assert by["b_forecast"]["is_registered"] is False


def test_prior_verdict_is_keyed_by_resource_key(client, registry):
    key = resource_key("database", "pg.regional:5432/b_forecast")
    assert key == "pg.regional:5432/b_forecast"
    registry.set_disposition_for_entity("database", key, "ignored", reason="archive")
    body = client.post("/api/db-servers/regional-pg/run").json()
    by = {c["name"]: c for c in body["candidates"]}
    assert by["b_forecast"]["verdict"]["disposition"] == "ignored"
    assert by["b_forecast"]["verdict"]["reason"] == "archive"
    assert by["b_forecast"]["key"] == key
    assert by["a_forecast"]["verdict"] is None          # nobody decided: None, not "ignored"


def test_verdict_key_is_case_and_slash_insensitive_like_the_csv_contract(client, registry):
    """Known-negative guard: the key is resource_key's, so 'PG.Regional:5432/B_Forecast/'
    written elsewhere settles to the same row discover reads."""
    registry.set_disposition_for_entity(
        "database", resource_key("database", "PG.Regional:5432/B_Forecast/"), "ignored")
    # b_forecast is lowercase in the listing: the keys agree after settling.
    by = {c["name"]: c for c in client.post("/api/db-servers/regional-pg/run").json()["candidates"]}
    assert by["b_forecast"]["verdict"]["disposition"] == "ignored"


def test_verdict_under_the_wrong_key_is_not_picked_up(client, registry):
    registry.set_disposition_for_entity("database", "b_forecast", "ignored")   # a bare name is not the key
    by = {c["name"]: c for c in client.post("/api/db-servers/regional-pg/run").json()["candidates"]}
    assert by["b_forecast"]["verdict"] is None


def test_registered_row_shows_the_verdict_set_under_its_slug(client, registry):
    from resource_explorer.registry import DatabaseEntity
    registry.register_database(DatabaseEntity(
        slug="a_reg", display_name="A", db_type="postgresql", host="pg.regional",
        port=5432, database_name="a_forecast"))
    registry.set_disposition_for_entity("database", "a_reg", "recommended")
    by = {c["name"]: c for c in client.post("/api/db-servers/regional-pg/run").json()["candidates"]}
    assert by["a_forecast"]["verdict"]["disposition"] == "recommended"


def test_listing_exposes_last_run_but_never_the_candidate_set_or_password(client):
    before = client.get("/api/db-servers/").json()[0]
    assert before["last_run_at"] is None and before["last_run_candidate_count"] is None
    client.post("/api/db-servers/regional-pg/run")
    after = client.get("/api/db-servers/").json()[0]
    assert after["last_run_candidate_count"] == 2 and after["last_run_at"]
    assert "last_run_candidates" not in after
    assert SECRET not in json.dumps(after) and "db_password" not in after


def test_no_response_carries_the_credential_even_when_the_driver_echoes_it(client):
    _Listing.fail_with = "could not connect: user=scout_ro password={pw}"
    for resp in (
        client.post("/api/db-servers/regional-pg/run"),
        client.post("/api/db-servers/regional-pg/discover"),
        client.post("/api/db-servers/regional-pg/test"),
        client.post("/api/db-servers/_discover-inline",
                    json={"host": "h", "db_user": "u", "db_password": SECRET}),
        client.post("/api/db-servers/_test-inline",
                    json={"host": "h", "db_user": "u", "db_password": SECRET}),
    ):
        assert SECRET not in resp.text, resp.request.url
    # And the stored source is not echoed by a successful run either.
    _Listing.fail_with = None
    assert SECRET not in client.post("/api/db-servers/regional-pg/run").text
    assert SECRET not in client.get("/api/db-servers/regional-pg").text


def test_discover_is_read_only_and_run_is_what_remembers(client, registry):
    client.post("/api/db-servers/regional-pg/discover")
    assert registry.get_server("regional-pg").last_run_candidates is None


def test_inline_discover_lists_candidates_registers_nothing_and_stores_nothing(client, registry):
    before = [s.slug for s in registry.list_servers()]
    r = client.post("/api/db-servers/_discover-inline",
                    json={"host": "pg.new", "port": 5433, "db_user": "u", "db_password": SECRET})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["host"] == "pg.new" and body["port"] == 5433
    names = [c["name"] for c in body["candidates"]]
    assert names == ["a_forecast", "b_forecast"]
    assert all(c["server_slug"] is None for c in body["candidates"])
    assert body["candidates"][0]["address"] == "pg.new:5433/a_forecast"
    assert [s.slug for s in registry.list_servers()] == before
    assert _Listing.seen[-1] == ("pg.new", 5433, "u")


def test_add_database_then_run_shows_it_registered(client):
    assert client.post("/api/db-servers/regional-pg/add-database?database_name=a_forecast").status_code == 200
    by = {c["name"]: c for c in client.post("/api/db-servers/regional-pg/run").json()["candidates"]}
    assert by["a_forecast"]["is_registered"] is True and by["b_forecast"]["is_registered"] is False


def test_test_connection_counts_listed_and_connectable_separately(client):
    _Listing.rows = [_row("ok"), _row("nope", can_connect=False)]
    # test route runs conn.execute_query("SELECT version()") too: give the stub one.
    import resource_explorer.surveyors.database.connection as c

    class _Conn:
        def execute_query(self, q): return [{"version": "PostgreSQL 16"}]
        def list_databases(self): return list(_Listing.rows)

    @contextlib.contextmanager
    def sc(*a, **k):
        yield _Conn()

    import pytest as _p
    mp = _p.MonkeyPatch()
    mp.setattr(c, "server_connection", sc)
    try:
        body = client.post("/api/db-servers/regional-pg/test").json()
    finally:
        mp.undo()
    assert body["database_count"] == 2 and body["connectable_count"] == 1


# ── the rename (Admin → Repository discovery sources) ────────────────────────

def test_admin_label_is_renamed_in_next_and_classic():
    from pathlib import Path
    static = Path(__file__).resolve().parents[1] / "resource_explorer" / "web" / "static"
    idx = (static / "next" / "admin" / "index.js").read_text(encoding="utf-8")
    pane = (static / "next" / "admin" / "discovery_sources.js").read_text(encoding="utf-8")
    classic = (static / "index.html").read_text(encoding="utf-8")
    assert "label: '🔍 Repository discovery sources'" in idx
    assert "🔍 Repository discovery sources</h3>" in pane
    assert "{ tab: 'admin-discovery-sources', label: '🔍 Repository discovery sources' }" in classic
    for text in (idx, pane, classic):
        assert "label: '🔍 Discovery Sources'" not in text
        assert "🔍 Discovery Sources</h3>" not in text and "🔍 Discovery Sources</h1>" not in text
