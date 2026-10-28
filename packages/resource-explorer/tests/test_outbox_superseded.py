"""test_outbox_superseded (2026-10-07 reliability batch). Fake clients/exceptions only; temp SQLite."""
from __future__ import annotations

import os

import pytest

import resource_explorer.egeria_outbox as outbox
import resource_explorer.run_queue as rq
from resource_explorer.activity_logger import log_survey
from resource_explorer.curate_plan import Curations
from resource_explorer.registry import Project, ProjectRegistry

# Brief I: these tests drive the drain mechanics, as the background loop does: a declared daemon job.
pytestmark = pytest.mark.usefixtures("as_daemon")


@pytest.fixture
def reg(tmp_path):
    r = ProjectRegistry(db_path=str(tmp_path / "rel.db"))
    r.add(Project(slug="myproj", display_name="My Project",
                  github_url="https://github.com/test/myproj", description="A test repo."))
    return r


def _open_activity(reg, summary="working…"):
    return log_survey(reg, entity_type="repo", entity_slug="myproj", entity_name="My Project",
                      entity_location="", intent="curate", status="running", summary=summary)


def _run(reg, monkeypatch, kind, target, handler, activity_id):
    reg.enqueue_run(kind, target, result_ref=activity_id)
    row = reg.claim_next_run("host:1", {"pid": os.getpid()})
    monkeypatch.setitem(rq.HANDLERS, kind, handler)
    return rq.execute_run(row, reg)



# ── 4. superseded ───────────────────────────────────────────────────────────────

class TestSuperseded:
    def _dead(self, reg, qn="q1"):
        rid = reg.enqueue_outbox_element("repo", "myproj", "annotation", qn, {})
        reg.mark_outbox_failed(rid, "nope", max_attempts=1)
        return rid

    def test_marks_a_dead_row_terminal_with_a_sentence(self, reg):
        dead = self._dead(reg)
        keeper = reg.enqueue_outbox_element("repo", "myproj", "annotation", "q2", {})
        assert reg.mark_outbox_superseded(dead, [keeper], "second set carries the intent") is True
        row = next(r for r in reg.list_outbox_elements() if r["id"] == dead)
        assert row["status"] == "superseded" and row["completed_at"]
        assert f"superseded by outbox row(s) {keeper}: second set" in row["last_error"]

    def test_the_drain_never_claims_it(self, reg):
        dead = self._dead(reg)
        reg.mark_outbox_superseded(dead, [999], "x")
        assert reg.claim_due_outbox_elements() == []

    def test_retry_only_revives_dead(self, reg):
        dead = self._dead(reg)
        reg.mark_outbox_superseded(dead, [999], "x")
        assert reg.retry_outbox_element(dead) is False

    def test_counted_separately_from_done_and_dead(self, reg):
        a, b = self._dead(reg, "a"), self._dead(reg, "b")
        d = reg.enqueue_outbox_element("repo", "myproj", "annotation", "c", {})
        reg.mark_outbox_done(d)
        reg.mark_outbox_superseded(a, [d], "x")
        assert reg.outbox_counts() == {"superseded": 1, "dead": 1, "done": 1}
        assert [r["id"] for r in reg.list_outbox_elements(status="superseded")] == [a]
        assert "superseded" in reg.OUTBOX_TERMINAL

    def test_is_valid_for_the_api_filter(self):
        from resource_explorer.web.routes.outbox import _VALID_STATUSES
        assert "superseded" in _VALID_STATUSES

    def test_will_not_overwrite_done_and_needs_a_superseder(self, reg):
        d = reg.enqueue_outbox_element("repo", "myproj", "annotation", "c", {})
        reg.mark_outbox_done(d)
        assert reg.mark_outbox_superseded(d, [5], "x") is False
        with pytest.raises(ValueError):
            reg.mark_outbox_superseded(d, [], "x")
        with pytest.raises(ValueError):
            reg.mark_outbox_superseded(d, [d], "x")

    def test_purge_keeps_superseded_by_decision(self, reg):
        a = self._dead(reg)
        reg.mark_outbox_superseded(a, [9], "x")
        with reg._conn() as c:
            c.execute("UPDATE egeria_outbox SET completed_at='2000-01-01T00:00:00' WHERE id=?", (a,))
        d = reg.enqueue_outbox_element("repo", "myproj", "annotation", "old", {})
        reg.mark_outbox_done(d)
        with reg._conn() as c:
            c.execute("UPDATE egeria_outbox SET completed_at='2000-01-01T00:00:00' WHERE id=?", (d,))
        assert reg.purge_outbox_completed(older_than_days=1) == 1
        assert reg.outbox_counts() == {"superseded": 1}

    def test_a_superseded_row_does_not_hold_a_publish_run_open(self, reg):
        rid = reg.enqueue_outbox_element("repo", "myproj", "annotation", "q", {}, run_id="run1")
        reg.mark_outbox_superseded(rid, [999], "carried elsewhere")
        # terminal, so not 'remaining'; no activity row for it -> no-op False, but must not raise
        assert reg.complete_publish_run_if_done("run1") is False
