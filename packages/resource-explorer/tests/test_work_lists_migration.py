"""The work-list tables are created by the registry's migrations, not by a read or a write.

`work_lists._ensure_schema` ran CREATE TABLE IF NOT EXISTS x3 plus an index create at the top of ~9 WorkLists
methods. The tables now come from `ProjectRegistry._init_schema` with the shapes the real registry already has
(read-only snapshot 2026-10-11), so applying that there is a no-op."""
from __future__ import annotations

import re
import sqlite3

import pytest

from resource_explorer.journal import Journal
from resource_explorer.registry import ConnectionWrapper, Project, ProjectRegistry
from resource_explorer.work_lists import WorkLists

DDL_RE = re.compile(r"^\s*(CREATE|ALTER|DROP)\b", re.I)
EXPECTED = {
    "work_lists": [("slug", None), ("display_name", None), ("description", ""), ("entity_type", "repo"),
                   ("investigation", ""), ("created_by", ""), ("created_at", None), ("derived_from", ""),
                   ("egeria_guid", ""), ("published_at", ""), ("filter_json", None), ("filter_as_of", None)],
    "work_list_members": [("work_list_slug", None), ("entity_slug", None), ("rationale", ""),
                          ("confidence", None), ("added_at", None), ("entity_type", "")],
    "work_list_runs": [("set_id", None), ("run_id", None), ("work_list_slug", ""), ("entity_slug", None),
                       ("analysis_id", None), ("enqueued_at", None)],
}


def test_fresh_sqlite_gets_the_work_list_tables(tmp_path):
    db = str(tmp_path / "w.db")
    ProjectRegistry(db_path=db)
    c = sqlite3.connect(db)
    try:
        for table, exp in EXPECTED.items():
            rows = c.execute(f"PRAGMA table_info({table})").fetchall()
            assert [(r[1], (r[4] or "").strip("'") if r[4] is not None else None) for r in rows] == exp, table
        idx = [r[1] for r in c.execute("PRAGMA index_list(work_list_runs)").fetchall()]
        assert "idx_work_list_runs_set" in idx
        assert [x[2] for x in c.execute("PRAGMA index_info(idx_work_list_runs_set)").fetchall()] == ["set_id"]
    finally:
        c.close()


def test_fresh_postgres_schema_gets_the_work_list_tables(pg_registry):
    with pg_registry._conn() as conn:
        for table, exp in EXPECTED.items():
            cols = conn.execute(
                "SELECT column_name, column_default FROM information_schema.columns "
                "WHERE table_schema = current_schema() AND table_name = ? ORDER BY ordinal_position",
                (table,)).fetchall()
            got = [(r["column_name"], None if r["column_default"] is None
                    else r["column_default"].split("::")[0].strip("'")) for r in cols]
            assert got == exp, table
        idx = conn.execute("SELECT indexdef FROM pg_indexes WHERE schemaname = current_schema() "
                           "AND indexname = 'idx_work_list_runs_set'").fetchall()
    assert idx and "(set_id)" in idx[0]["indexdef"]


@pytest.fixture
def registry(tmp_path):
    r = ProjectRegistry(db_path=str(tmp_path / "t.db"))
    for s in ("p", "q"):
        r.add(Project(slug=s, display_name=s.upper(), github_url=f"https://github.com/x/{s}", description=""))
    return r


def _spy(monkeypatch, registry):
    # WorkLists.get builds a bare ProjectRegistry() for dispositions (pre-existing); share the test registry
    # so that construction does not re-run the registry's own init and muddy the capture.
    monkeypatch.setattr("resource_explorer.registry.ProjectRegistry.__init__",
                        lambda self, db_path=None: setattr(self, "__dict__", registry.__dict__) or None)
    seen: list[str] = []
    real = ConnectionWrapper.execute
    monkeypatch.setattr(ConnectionWrapper, "execute",
                        lambda self, sql, params=None: (seen.append(sql), real(self, sql, params))[1])
    return seen


def test_work_list_reads_and_writes_issue_no_ddl(registry, monkeypatch):
    seen = _spy(monkeypatch, registry)
    wl = WorkLists(registry)
    made = wl.create("Look at these", ["p"], created_by="me")
    wl.set_member(made["slug"], "q", rationale="why")
    wl.set_investigation(made["slug"], "inv")
    got = wl.get(made["slug"])
    wl.list_all()
    wl.remove_member(made["slug"], "q")
    wl.batch_progress("set-none")
    wl.delete(made["slug"])
    assert any("work_list" in s for s in seen), "control: the spy saw the work-list statements"
    assert [s for s in seen if DDL_RE.match(s)] == []
    assert got["display_name"] == "Look at these" and [m["entity_slug"] for m in got["members"]] == ["p", "q"]


def test_journal_write_with_a_suggestion_issues_no_ddl(registry, monkeypatch):
    seen = _spy(monkeypatch, registry)
    out = Journal(registry).write("repo", "p", author="a", body="worth a look", suggest_to=["Consumer"])
    assert out["work_lists"][0]["work_list"] == "suggested-to-consumer"
    assert any("work_list" in s for s in seen)
    assert any("resource_journal" in s for s in seen)
    # Both moves are in (resource_journal #596, work lists here): a journal write with a suggestion issues no DDL at all.
    assert [s for s in seen if DDL_RE.match(s)] == []
