"""resource_curation is created by the registry's migrations, not by a GET.

`curate_plan._ensure_schema` used to run `CREATE TABLE IF NOT EXISTS resource_curation` (and a column probe plus
up to seven ALTERs) on every read of a curation record -- DDL on a read path, which also fails under a read-only
session. The table now comes from `ProjectRegistry._init_schema`, with the columns, order, defaults and index the
real registry already has, so applying it there is a no-op. Pinned here:

  1. a fresh SQLite registry and a fresh Postgres schema both have the table, with the exact columns and the index;
  2. a registry holding the ORIGINAL 11-column table (the shape that shipped first) is brought forward by the
     migration, keeping its rows;
  3. a GET on the plan route issues no DDL at all (every statement is captured);
  4. the plan carries the commits a created curation put there, as before.
"""
from __future__ import annotations

import re
import sqlite3

import pytest
from fastapi.testclient import TestClient

from resource_explorer.curate_plan import Curations
from resource_explorer.registry import Project, ProjectRegistry

#: (name, default) in the order the real registry has them (ordinal 1..18, read-only snapshot 2026-10-11).
EXPECTED = [
    ("id", None), ("entity_type", None), ("entity_slug", None), ("author", None), ("requested_at", None),
    ("selection", "{}"), ("manifest", "{}"), ("state", "queued"), ("steps", "[]"), ("finished_at", ""),
    ("activity_id", ""), ("kind", "catalogue"), ("name", ""), ("report", "{}"), ("uses", "[]"),
    ("corrects", ""), ("corrected_by", "{}"), ("layer2_offer", "{}"),
]
DDL_RE = re.compile(r"^\s*(CREATE|ALTER|DROP)\b", re.I)


def _sqlite_columns(path):
    c = sqlite3.connect(path)
    try:
        rows = c.execute("PRAGMA table_info(resource_curation)").fetchall()
        idx = c.execute("PRAGMA index_list(resource_curation)").fetchall()
        icols = {r[1]: [x[2] for x in c.execute(f"PRAGMA index_info({r[1]})").fetchall()] for r in idx}
    finally:
        c.close()
    return rows, icols


def test_fresh_sqlite_gets_the_table_from_migrations(tmp_path):
    db = str(tmp_path / "t.db")
    ProjectRegistry(db_path=db)
    rows, icols = _sqlite_columns(db)
    assert [(r[1], (r[4] or "").strip("'") if r[4] is not None else None) for r in rows] == EXPECTED
    assert all(r[3] == 1 for r in rows if r[1] != "id"), "every column but the PK is NOT NULL"
    assert icols["idx_resource_curation_entity"] == ["entity_type", "entity_slug", "requested_at"]


def test_fresh_postgres_schema_gets_the_table_from_migrations(pg_registry):
    with pg_registry._conn() as conn:
        cols = conn.execute(
            "SELECT column_name, column_default, is_nullable FROM information_schema.columns "
            "WHERE table_schema = current_schema() AND table_name = 'resource_curation' "
            "ORDER BY ordinal_position").fetchall()
        idx = conn.execute("SELECT indexname, indexdef FROM pg_indexes "
                           "WHERE schemaname = current_schema() AND tablename = 'resource_curation'").fetchall()
    got = [(r["column_name"], None if r["column_default"] is None else r["column_default"].split("::")[0].strip("'"))
           for r in cols]
    assert got == EXPECTED
    assert all(r["is_nullable"] == "NO" for r in cols)
    defs = {r["indexname"]: r["indexdef"] for r in idx}
    assert "(entity_type, entity_slug, requested_at)" in defs["idx_resource_curation_entity"]
    assert "resource_curation_pkey" in defs


def test_original_shape_table_is_brought_forward_keeping_rows(tmp_path):
    db = str(tmp_path / "old.db")
    c = sqlite3.connect(db)
    c.execute("""CREATE TABLE resource_curation (id TEXT PRIMARY KEY, entity_type TEXT NOT NULL, entity_slug TEXT NOT NULL,
        author TEXT NOT NULL, requested_at TEXT NOT NULL, selection TEXT NOT NULL DEFAULT '{}',
        manifest TEXT NOT NULL DEFAULT '{}', state TEXT NOT NULL DEFAULT 'queued', steps TEXT NOT NULL DEFAULT '[]',
        finished_at TEXT NOT NULL DEFAULT '', activity_id TEXT NOT NULL DEFAULT '')""")
    c.execute("INSERT INTO resource_curation (id, entity_type, entity_slug, author, requested_at) "
              "VALUES ('old1', 'repo', 'p', 'me', '2026-01-01T00:00:00')")
    c.commit(); c.close()
    ProjectRegistry(db_path=db)
    rows, _ = _sqlite_columns(db)
    assert [r[1] for r in rows] == [n for n, _ in EXPECTED]
    rec = Curations(ProjectRegistry(db_path=db)).get("old1")
    assert rec["kind"] == "catalogue" and rec["id"] == "old1"


@pytest.fixture
def registry(tmp_path):
    r = ProjectRegistry(db_path=str(tmp_path / "t.db"))
    r.add(Project(slug="p", display_name="P repo", github_url="https://github.com/x/p", description=""))
    return r


@pytest.fixture
def client(registry, monkeypatch):
    monkeypatch.setattr("resource_explorer.registry.ProjectRegistry.__init__",
                        lambda self, db_path=None: setattr(self, "__dict__", registry.__dict__) or None)
    monkeypatch.setenv("TRELLIS_ANONYMOUS_READ", "true")
    from resource_explorer.web.app import app
    return TestClient(app)


def test_plan_get_issues_no_ddl_and_carries_the_commits(client, registry, monkeypatch):
    rec = Curations(registry).create("repo", "p", author="me", selection={}, manifest={}, steps=["a"])
    seen: list[str] = []
    from resource_explorer.registry import ConnectionWrapper
    real = ConnectionWrapper.execute

    def spy(self, sql, params=None):
        seen.append(sql)
        return real(self, sql, params)

    monkeypatch.setattr(ConnectionWrapper, "execute", spy)
    r = client.get("/api/projects/p/curate/plan")
    assert r.status_code == 200, r.text
    assert seen, "the spy saw the route's statements (a control: it can fail)"
    assert any("resource_curation" in s for s in seen), "the route did read the curation table"
    assert [s for s in seen if DDL_RE.match(s)] == []
    assert [c["id"] for c in r.json()["commits"]] == [rec["id"]]


def test_curations_reads_issue_no_ddl(registry, monkeypatch):
    cur = Curations(registry)
    rec = cur.create("repo", "p", author="me", selection={}, manifest={}, steps=["a"])
    from resource_explorer.registry import ConnectionWrapper
    seen: list[str] = []
    real = ConnectionWrapper.execute
    monkeypatch.setattr(ConnectionWrapper, "execute",
                        lambda self, sql, params=None: (seen.append(sql), real(self, sql, params))[1])
    cur.get(rec["id"]); cur.for_resource("repo", "p")
    assert seen and [s for s in seen if DDL_RE.match(s)] == []


# ── resource_journal: same move (journal.py's per-call _ensure_schema) ──────────────────────────

JOURNAL_EXPECTED = [("id", None), ("entity_type", None), ("entity_slug", None), ("author", None),
                    ("written_at", None), ("body", None), ("suggested_to", "[]"), ("compile_id", None),
                    ("note", None), ("turn_json", None)]


def test_fresh_sqlite_gets_the_journal_table_from_migrations(tmp_path):
    db = str(tmp_path / "j.db")
    ProjectRegistry(db_path=db)
    c = sqlite3.connect(db)
    try:
        rows = c.execute("PRAGMA table_info(resource_journal)").fetchall()
        idx = {r[1]: [x[2] for x in c.execute(f"PRAGMA index_info({r[1]})").fetchall()]
               for r in c.execute("PRAGMA index_list(resource_journal)").fetchall()}
    finally:
        c.close()
    # SQLite reports an explicit DEFAULT NULL as the text "NULL": that is no default (None) here.
    assert [(r[1], None if r[4] in (None, "NULL") else r[4].strip("'")) for r in rows] == JOURNAL_EXPECTED
    assert idx["idx_resource_journal_entity"] == ["entity_type", "entity_slug", "written_at"]


def test_fresh_postgres_schema_gets_the_journal_table_from_migrations(pg_registry):
    with pg_registry._conn() as conn:
        cols = conn.execute(
            "SELECT column_name, column_default, is_nullable FROM information_schema.columns "
            "WHERE table_schema = current_schema() AND table_name = 'resource_journal' "
            "ORDER BY ordinal_position").fetchall()
        idx = conn.execute("SELECT indexname, indexdef FROM pg_indexes "
                           "WHERE schemaname = current_schema() AND tablename = 'resource_journal'").fetchall()
    assert [(r["column_name"], None if r["column_default"] is None else r["column_default"].split("::")[0].strip("'"))
            for r in cols] == JOURNAL_EXPECTED
    # compile_id, note and turn_json are the nullable columns: NULL when the entry was not made from a chat turn
    # with a compile, has no note of the person's, or is not a derived turn.
    assert {r["column_name"] for r in cols if r["is_nullable"] == "YES"} == {"compile_id", "note", "turn_json"}
    defs = {r["indexname"]: r["indexdef"] for r in idx}
    assert "(entity_type, entity_slug, written_at)" in defs["idx_resource_journal_entity"]
    assert "resource_journal_pkey" in defs


def test_an_older_journal_table_gains_note_and_turn_json_and_a_second_start_is_a_no_op(tmp_path):
    """The table as the live registry has it (8 columns, before note/turn_json): the migration ADDs the two columns,
    keeps the old rows readable, and a second start neither errors nor adds a duplicate."""
    db = str(tmp_path / "old.db")
    c = sqlite3.connect(db)
    c.execute("""CREATE TABLE resource_journal (id TEXT PRIMARY KEY, entity_type TEXT NOT NULL, entity_slug TEXT NOT NULL,
                 author TEXT NOT NULL, written_at TEXT NOT NULL, body TEXT NOT NULL,
                 suggested_to TEXT NOT NULL DEFAULT '[]', compile_id TEXT)""")
    c.execute("INSERT INTO resource_journal VALUES ('old1','repo','p','a','2026-10-01T00:00:00','before',"
              "'[\"Steward\"]',NULL)")
    c.commit(); c.close()

    def columns():
        k = sqlite3.connect(db)
        try:
            return [r[1] for r in k.execute("PRAGMA table_info(resource_journal)").fetchall()]
        finally:
            k.close()

    assert "note" not in columns() and "turn_json" not in columns()
    reg = ProjectRegistry(db_path=db)
    assert columns()[-2:] == ["note", "turn_json"] and len(columns()) == 10
    ProjectRegistry(db_path=db)                       # second start: no error, no duplicate
    assert columns().count("note") == 1 and columns().count("turn_json") == 1 and len(columns()) == 10
    from resource_explorer.journal import Journal
    old = Journal(reg).entries("repo", "p")[0]
    assert old["body"] == "before" and old["suggested_to"] == ["Steward"] and "note" not in old and "turn" not in old


def test_a_fresh_registry_started_twice_keeps_one_of_each_journal_column(tmp_path):
    db = str(tmp_path / "twice.db")
    ProjectRegistry(db_path=db)
    ProjectRegistry(db_path=db)
    c = sqlite3.connect(db)
    try:
        names = [r[1] for r in c.execute("PRAGMA table_info(resource_journal)").fetchall()]
    finally:
        c.close()
    assert names == [n for n, _ in JOURNAL_EXPECTED]


def test_journal_reads_and_writes_issue_no_ddl_and_output_is_unchanged(registry, monkeypatch):
    from resource_explorer.journal import Journal
    from resource_explorer.registry import ConnectionWrapper
    seen: list[str] = []
    real = ConnectionWrapper.execute
    monkeypatch.setattr(ConnectionWrapper, "execute",
                        lambda self, sql, params=None: (seen.append(sql), real(self, sql, params))[1])
    j = Journal(registry)
    j.write("repo", "p", author="a", body="first")
    j.write("repo", "p", author="a", body="second")
    got = j.entries("repo", "p")
    assert any("resource_journal" in s for s in seen), "control: the spy saw the journal statements"
    assert [s for s in seen if DDL_RE.match(s)] == []
    assert [e["body"] for e in got] == ["second", "first"]
    assert set(got[0]) == {"id", "author", "written_at", "body", "suggested_to", "compile_id"} and got[0]["suggested_to"] == []
