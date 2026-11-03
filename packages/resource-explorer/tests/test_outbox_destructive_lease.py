"""The drain's lease reclaim must never make a destructive write due again (incident rule: the drain never
retries a destructive write). A lapsed claim on such a row means the first send may have landed."""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from resource_explorer.egeria_outbox import CLAIM_LAPSED_DESTRUCTIVE
from resource_explorer.registry import Project, ProjectRegistry

# Brief I: these tests drive the drain mechanics, as the background loop does: a declared daemon job.
pytestmark = pytest.mark.usefixtures("as_daemon")


@pytest.fixture()
def db(tmp_path):
    reg = ProjectRegistry(database_url=f"sqlite:///{tmp_path/'t.db'}")
    reg._init_schema()
    reg.add(Project(slug="p", display_name="p", github_url="https://github.com/o/p"))
    return reg


def _later():
    return (datetime.utcnow() + timedelta(hours=2)).isoformat()


@pytest.mark.parametrize("kind", ["catalogue_schema_leave_out", "doc_source_unpublish", "some_new_archive_kind"])
def test_a_lapsed_destructive_claim_is_dead_not_due(db, kind):
    row = db.enqueue_outbox_element("repo", "p", kind, "Q::0", {})
    assert [r["id"] for r in db.claim_due_outbox_elements()] == [row]      # claimer takes it, then "dies"
    assert db.peek_due_outbox_elements(now=_later()) == [], "not even visible as due"
    assert db.claim_due_outbox_elements(now=_later()) == [], "never handed to a drainer again"
    r = db.get_outbox_element(row)
    assert r["status"] == "dead"
    assert r["last_error"] == CLAIM_LAPSED_DESTRUCTIVE == "claim lapsed; destructive writes are never re-sent"
    assert db.outbox_counts() == {"dead": 1}


def test_a_lapsed_ordinary_claim_is_still_reclaimed(db):
    row = db.enqueue_outbox_element("repo", "p", "annotation", "Q::0", {})
    db.claim_due_outbox_elements()
    assert [r["id"] for r in db.claim_due_outbox_elements(now=_later())] == [row]


def test_a_destructive_claim_inside_the_lease_is_left_running(db):
    row = db.enqueue_outbox_element("repo", "p", "doc_source_unpublish", "Q::0", {})
    db.claim_due_outbox_elements()
    assert db.claim_due_outbox_elements() == []
    assert db.get_outbox_element(row)["status"] == "running"


def test_a_pending_destructive_row_is_still_sent_the_first_time(db):
    row = db.enqueue_outbox_element("repo", "p", "doc_source_unpublish", "Q::0", {})
    assert [r["id"] for r in db.claim_due_outbox_elements()] == [row]


def test_the_dead_row_is_not_retried_from_the_queue_either(db):
    from fastapi import HTTPException

    from resource_explorer.web.routes import outbox as routes
    row = db.enqueue_outbox_element("repo", "p", "catalogue_schema_leave_out", "Q::0", {})
    db.claim_due_outbox_elements()
    db.claim_due_outbox_elements(now=_later())
    assert routes.is_destructive_kind("catalogue_schema_leave_out") is True
    assert db.get_outbox_element(row)["status"] == "dead"


def test_a_row_the_claimer_just_finished_is_never_turned_dead(db):
    """The kill pass selects, then updates; a slow claimer may mark the row done in between."""
    row = db.enqueue_outbox_element("repo", "p", "doc_source_unpublish", "Q::0", {})
    db.claim_due_outbox_elements()
    real = db._non_destructive_sql

    def finish_in_the_gap():
        db.mark_outbox_done(row, "guid-1")      # the claimer completes after the SELECT would have seen it running
        return real()

    # Simulate the race at the UPDATE: select sees 'running', then the row completes before the UPDATE runs.
    with db._conn() as conn:
        lease_cutoff = _later()
        orig = conn.execute

        class Wrapper:
            def __init__(self, c): self.c = c; self.is_postgres = getattr(c, "is_postgres", False)
            def execute(self, sql, params=()):
                out = self.c.execute(sql, params)
                if sql.lstrip().startswith("SELECT o.id FROM egeria_outbox o WHERE o.status = 'running'"):
                    rows = out.fetchall()
                    db_done = self.c.execute("UPDATE egeria_outbox SET status='done', claimed_at='' WHERE id=?", (row,))
                    class R:  # replay the rows the SELECT saw
                        def fetchall(_s): return rows
                    return R()
                return out
        db._kill_lapsed_destructive_claims(Wrapper(conn), lease_cutoff)
    assert db.get_outbox_element(row)["status"] == "done"


@pytest.mark.parametrize("kind,destructive", [
    ("annotation_x_y", False), ("my_archive_kind", True), ("archiveXkind", True), ("a%b", False),
    ("doc_source_unpublish", True), ("catalogue_schema_leave_out", True), ("leaveXout", False), ("leave_out_x", True),
    ("deleteit", True), ("to_detach", True), ("Remove_thing", True), ("annotation", False),
])
def test_the_sql_rule_matches_the_python_rule_exactly(db, kind, destructive):
    """`_` is a LIKE wildcard: the word 'leave_out' must not match 'leaveXout'. SQL and Python must agree."""
    from resource_explorer.egeria_outbox import is_destructive_outbox_kind
    assert is_destructive_outbox_kind(kind) is destructive
    row = db.enqueue_outbox_element("repo", "p", kind, "Q::0", {})
    db.claim_due_outbox_elements()
    db.claim_due_outbox_elements(now=_later())
    status = db.get_outbox_element(row)["status"]
    assert (status == "dead") is destructive, (kind, status)
