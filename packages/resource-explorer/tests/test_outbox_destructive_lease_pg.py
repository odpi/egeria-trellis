"""Postgres versions of the lapsed-claim tests (tests/test_outbox_destructive_lease.py).

They use the `pg_registry` fixture (a throwaway `resource_explorer_test_*` schema) and so SKIP under PGVECTOR_PORT=1.
The SQL differs on Postgres where it matters: `?` becomes `%s`, LIKE needs the ESCAPE clause to treat `_` literally,
and FOR UPDATE SKIP LOCKED is added to the claim."""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from resource_explorer.egeria_outbox import CLAIM_LAPSED_DESTRUCTIVE
from resource_explorer.registry import Project

# Brief I: these tests drive the drain mechanics, as the background loop does: a declared daemon job.
pytestmark = pytest.mark.usefixtures("as_daemon")


def _clear_outbox(conn):
    """Empty the outbox of the THROWAWAY schema only. The schema is read back from the connection and must be a
    `resource_explorer_test*` one, and the DELETE names it: were the search_path option ever ignored, this would
    otherwise empty the real resource_explorer.egeria_outbox on the shared Postgres."""
    row = conn.execute("SELECT current_schema() AS s").fetchone()
    schema = row["s"] if hasattr(row, "keys") else row[0]
    if not str(schema).startswith("resource_explorer_test"):
        pytest.fail(f"refusing to DELETE FROM egeria_outbox: current_schema() is {schema!r}, not a test schema")
    conn.execute(f'DELETE FROM "{schema}".egeria_outbox')


@pytest.fixture()
def db(pg_registry):
    with pg_registry._conn() as conn:
        _clear_outbox(conn)
    if not pg_registry.get("pgp"):
        pg_registry.add(Project(slug="pgp", display_name="pgp", github_url="https://github.com/o/pgp"))
    yield pg_registry
    with pg_registry._conn() as conn:
        _clear_outbox(conn)


class _FakeConn:
    def __init__(self, schema):
        self.schema, self.sql = schema, []

    def execute(self, sql, params=()):
        self.sql.append(sql)

        class R:
            def fetchone(_s):
                return {"s": self.schema}
        return R()


@pytest.mark.parametrize("schema", ["resource_explorer", "public", "egeria_advisor", ""])
def test_the_guard_refuses_a_non_test_schema_and_deletes_nothing(schema):
    conn = _FakeConn(schema)
    with pytest.raises(pytest.fail.Exception, match="refusing to DELETE"):
        _clear_outbox(conn)
    assert not any("DELETE" in q for q in conn.sql)


def test_the_guard_qualifies_the_delete_with_the_quoted_test_schema():
    conn = _FakeConn("resource_explorer_test_abc123")
    _clear_outbox(conn)
    assert conn.sql[-1] == 'DELETE FROM "resource_explorer_test_abc123".egeria_outbox'


def _later():
    return (datetime.utcnow() + timedelta(hours=2)).isoformat()


def _enq(db, kind):
    return db.enqueue_outbox_element("repo", "pgp", kind, f"Q::{kind}", {})


@pytest.mark.parametrize("kind", ["catalogue_schema_leave_out", "doc_source_unpublish", "some_new_archive_kind"])
def test_a_lapsed_destructive_row_is_marked_dead_and_never_claimed(db, kind):
    row = _enq(db, kind)
    assert [r["id"] for r in db.claim_due_outbox_elements()] == [row]
    assert db.claim_due_outbox_elements(now=_later()) == []
    r = db.get_outbox_element(row)
    assert r["status"] == "dead" and r["last_error"] == CLAIM_LAPSED_DESTRUCTIVE


def test_a_lapsed_additive_row_is_reclaimed(db):
    row = _enq(db, "annotation")
    db.claim_due_outbox_elements()
    assert [r["id"] for r in db.claim_due_outbox_elements(now=_later())] == [row]


def test_the_mark_dead_update_does_not_overwrite_a_row_that_is_already_done(db):
    row = _enq(db, "doc_source_unpublish")
    db.claim_due_outbox_elements()
    cutoff = _later()
    with db._conn() as conn:
        # The claimer finishes between the kill pass's SELECT and its UPDATE: run only the UPDATE's guard.
        conn.execute("UPDATE egeria_outbox SET status='done', claimed_at='' WHERE id=?", (row,))
        marks = "?"
        from resource_explorer.egeria_outbox import CLAIM_LAPSED_DESTRUCTIVE as why
        cur = conn.execute(
            f"UPDATE egeria_outbox SET status='dead', claimed_at='', next_attempt_at='', last_error=? "
            f"WHERE id IN ({marks}) AND status='running' AND claimed_at <= ?", (why, row, cutoff))
        assert (cur.rowcount or 0) == 0
    assert db.get_outbox_element(row)["status"] == "done"
    with db._conn() as conn:
        assert db._kill_lapsed_destructive_claims(conn, cutoff) == []
    assert db.get_outbox_element(row)["status"] == "done"


@pytest.mark.parametrize("kind,destructive", [
    ("leaveXout", False), ("leave_out_x", True), ("annotation_x_y", False), ("my_archive_kind", True), ("a%b", False),
])
def test_the_escape_pattern_treats_underscore_literally(db, kind, destructive):
    from resource_explorer.egeria_outbox import is_destructive_outbox_kind
    assert is_destructive_outbox_kind(kind) is destructive
    row = _enq(db, kind)
    db.claim_due_outbox_elements()
    db.claim_due_outbox_elements(now=_later())
    assert (db.get_outbox_element(row)["status"] == "dead") is destructive


def test_peek_excludes_a_lapsed_destructive_row_but_shows_a_lapsed_additive_one(db):
    bad = _enq(db, "doc_source_unpublish")
    good = _enq(db, "annotation")
    db.claim_due_outbox_elements()
    ids = [r["id"] for r in db.peek_due_outbox_elements(now=_later())]
    assert good in ids and bad not in ids
    assert db.get_outbox_element(bad)["status"] == "running", "peek only looks; the claim pass is what kills it"
