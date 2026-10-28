"""test_activity_rows_close (2026-10-07 reliability batch). Fake clients/exceptions only; temp SQLite."""
from __future__ import annotations

import os

import pytest

import resource_explorer.egeria_outbox as outbox
import resource_explorer.run_queue as rq
from resource_explorer.activity_logger import log_survey
from resource_explorer.curate_plan import Curations
from resource_explorer.registry import Project, ProjectRegistry


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
    # Brief I: a person's queued action must record whose it is.
    reg.enqueue_run(kind, target, result_ref=activity_id, requested_by="a")
    row = reg.claim_next_run("host:1", {"pid": os.getpid()})
    monkeypatch.setitem(rq.HANDLERS, kind, handler)
    return rq.execute_run(row, reg)



# ── 1. activity rows that never close ───────────────────────────────────────────

class TestActivityRowsClose:
    def test_a_failed_curate_commit_closes_its_row_and_says_why(self, reg, monkeypatch):
        act = _open_activity(reg)
        rec = Curations(reg).create("repo", "myproj", author="a", selection={}, manifest={},
                                    steps=["publish_asset", "classifications"], activity_id=act)
        Curations(reg).set_step(rec["id"], "publish_asset", "failed",
                                "RuntimeError: no Egeria Project context. Decide it first")
        Curations(reg).finish(rec["id"])

        def handler(target, ref):
            r = Curations(reg).get(target["curation_id"])
            failed = [s["name"] for s in r["steps"] if s["state"] == "failed"]
            return rq.RunOutcome(state="failed", error="failed: " + ", ".join(failed))

        _run(reg, monkeypatch, "curate_commit", {"slug": "myproj", "curation_id": rec["id"]}, handler, act)
        row = reg.get_activity(act)
        assert row["status"] == "error"
        assert "failed at step publish_asset" in row["summary"]
        assert "no Egeria Project context" in row["summary"]

    def test_a_successful_curate_commit_closes_ok(self, reg, monkeypatch):
        act = _open_activity(reg)
        rec = Curations(reg).create("repo", "myproj", author="a", selection={}, manifest={},
                                    steps=["publish_asset"], activity_id=act)
        Curations(reg).set_step(rec["id"], "publish_asset", "done", "ok")
        _run(reg, monkeypatch, "curate_commit", {"slug": "myproj", "curation_id": rec["id"]},
             lambda t, r: rq.RunOutcome(state="succeeded"), act)
        row = reg.get_activity(act)
        assert row["status"] == "ok" and "1 step(s) done" in row["summary"]

    def test_a_handler_that_raises_closes_the_row(self, reg, monkeypatch):
        act = _open_activity(reg)

        def boom(target, ref):
            raise RuntimeError("registry gone")

        _run(reg, monkeypatch, "materialize_components", {"slug": "myproj", "paths": ["a", "b"]}, boom, act)
        row = reg.get_activity(act)
        assert row["status"] == "error"
        assert "registry gone" in row["summary"] and "2 accepted component" in row["summary"]

    def test_a_handler_that_returns_early_with_nothing_to_do_closes_ok(self, reg, monkeypatch):
        act = _open_activity(reg)
        _run(reg, monkeypatch, "materialize_components", {"slug": "myproj", "paths": []},
             lambda t, r: rq.RunOutcome(state="succeeded"), act)
        row = reg.get_activity(act)
        assert row["status"] == "ok" and "Materialised 0 accepted" in row["summary"]

    def test_a_kind_with_no_handler_closes_the_row(self, reg):
        act = _open_activity(reg)
        rq.execute_run({"id": "r1", "kind": "mystery", "target": "{}", "result_ref": act}, reg)
        assert reg.get_activity(act)["status"] == "error"

    def test_a_row_the_handler_already_closed_is_left_as_it_wrote_it(self, reg, monkeypatch):
        act = _open_activity(reg)

        def handler(target, ref):
            reg.update_activity_status(ref, "warning", summary="handler's own words")
            return rq.RunOutcome(state="succeeded")

        _run(reg, monkeypatch, "scouting_scan", {"slug": "myproj"}, handler, act)
        row = reg.get_activity(act)
        assert row["status"] == "warning" and row["summary"] == "handler's own words"

    def test_the_startup_reconciler_still_resolves_a_genuinely_orphaned_row(self, reg):
        from resource_explorer.run_reconciler import reconcile
        act = log_survey(reg, entity_type="repo", entity_slug="myproj", entity_name="My Project",
                         entity_location="", intent="curate", status="running", summary="…",
                         runner={"pid": 2 ** 22, "started_at": "whenever"})
        assert reconcile(reg)["resolved"] == 1
        assert reg.get_activity(act)["status"] != "running"

    def test_close_if_running_never_overwrites_a_finished_row(self, reg):
        act = _open_activity(reg)
        reg.update_activity_status(act, "ok", summary="done")
        assert reg.close_activity_if_running(act, "error", "late") is False
        assert reg.get_activity(act)["summary"] == "done"

