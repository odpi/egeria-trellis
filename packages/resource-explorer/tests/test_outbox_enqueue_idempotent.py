"""test_outbox_enqueue_idempotent (2026-10-07 reliability batch). Fake clients/exceptions only; temp SQLite."""
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



# ── 3. repeated blueprint verdict ───────────────────────────────────────────────

class TestEnqueueIsIdempotent:
    def _ids(self, reg, members=("m1", "m2")):
        return outbox.enqueue_blueprint_members(reg, "repo", "myproj", "bp", list(members))

    def test_a_second_press_returns_the_existing_rows(self, reg):
        first = self._ids(reg)
        second = self._ids(reg)
        assert second == first
        assert len(reg.list_outbox_elements()) == 2

    @pytest.mark.parametrize("state", ["failed", "running", "done"])
    def test_existing_row_in_any_live_or_done_state_is_not_duplicated(self, reg, state):
        first = self._ids(reg, ("m1",))
        with reg._conn() as c:
            c.execute("UPDATE egeria_outbox SET status=? WHERE id=?", (state, first[0]))
        assert self._ids(reg, ("m1",)) == first
        assert len(reg.list_outbox_elements()) == 1

    def test_a_dead_row_is_revived_not_stacked(self, reg):
        first = self._ids(reg, ("m1",))
        reg.mark_outbox_failed(first[0], "refused", max_attempts=1)
        again = self._ids(reg, ("m1",))
        rows = reg.list_outbox_elements()
        assert again == first and len(rows) == 1
        assert rows[0]["status"] == "pending" and rows[0]["attempts"] == 0

    def test_a_retired_row_does_not_block_a_new_one(self, reg):
        first = self._ids(reg, ("m1",))
        other = reg.enqueue_outbox_element("repo", "myproj", "resource_list", "x", {})
        reg.mark_outbox_superseded(first[0], [other], "replaced")
        again = self._ids(reg, ("m1",))
        assert again != first

    def test_collection_members_are_idempotent_too(self, reg):
        m = [{"member_guid": "g1", "entity_slug": "s"}]
        a = outbox.enqueue_collection_members(reg, "inv", "col", m)
        assert outbox.enqueue_collection_members(reg, "inv", "col", m) == a

    def test_other_entities_do_not_collide(self, reg):
        a = outbox.enqueue_blueprint_members(reg, "repo", "one", "bp", ["m"])
        b = outbox.enqueue_blueprint_members(reg, "repo", "two", "bp", ["m"])
        assert a != b

