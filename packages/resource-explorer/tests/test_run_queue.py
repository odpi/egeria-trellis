"""The Postgres run queue — claiming, heartbeating, fairness, reconciliation.

Two tiers, deliberately, because the interesting property cannot be tested on
SQLite. `claim_next_run` relies on `FOR UPDATE SKIP LOCKED` for its "only one
claimer wins" guarantee, and SQLite has no such clause — it serialises writers
instead, which produces the same *outcome* by a different mechanism. A green
SQLite test therefore tells you nothing about the Postgres behaviour it is
standing in for, which is exactly the trap `claim_due_outbox_elements`'s own
comment records (the SQLite tests were green while the real backend raised
FeatureNotSupported). So the concurrency test is marked `requires_pgvector` and
runs against the throwaway Postgres schema.
"""
from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timedelta, timezone

import pytest

from resource_explorer.registry import Project, ProjectRegistry


@pytest.fixture
def reg(tmp_path):
    r = ProjectRegistry(db_path=str(tmp_path / "queue.db"))
    r.add(Project(
        slug="myproj", display_name="My Project",
        github_url="https://github.com/test/myproj", description="A test repo.",
    ))
    return r


def _now(offset_seconds: int = 0) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=offset_seconds)).isoformat()


class TestEnqueue:
    def test_an_enqueued_row_starts_queued_and_unclaimed(self, reg):
        run_id = reg.enqueue_run("scouting_scan", {"slug": "myproj"})
        row = reg.get_run(run_id)
        assert row["state"] == "queued"
        assert row["claimed_by"] == ""
        assert row["heartbeat_at"] == ""
        assert json.loads(row["target"]) == {"slug": "myproj"}

    def test_an_unknown_kind_is_refused_at_enqueue(self, reg):
        """Rather than accepted and left queued for ever. A row nothing can
        execute is indistinguishable, from the outside, from a queue nobody is
        draining — so the mistake has to be caught where it is made."""
        with pytest.raises(ValueError, match="unknown run kind"):
            reg.enqueue_run("not_a_kind", {})

    def test_the_activity_id_round_trips_as_result_ref(self, reg):
        """The frontend polls the activity entry, not the run. result_ref is
        the only thing joining the two."""
        run_id = reg.enqueue_run("scouting_scan", {"slug": "myproj"}, result_ref="act-1")
        assert reg.get_run(run_id)["result_ref"] == "act-1"


class TestClaim:
    def test_a_claim_marks_the_row_and_records_the_worker(self, reg):
        run_id = reg.enqueue_run("scouting_scan", {"slug": "myproj"})
        row = reg.claim_next_run("host:123", {"pid": 123, "started_at": "t"})
        assert row["id"] == run_id
        assert row["state"] == "claimed"
        assert row["claimed_by"] == "host:123"
        assert json.loads(row["runner"])["pid"] == 123
        assert row["heartbeat_at"]

    def test_a_claimed_row_is_not_claimed_twice(self, reg):
        reg.enqueue_run("scouting_scan", {"slug": "myproj"})
        assert reg.claim_next_run("a:1") is not None
        assert reg.claim_next_run("b:2") is None

    def test_oldest_first(self, reg):
        first = reg.enqueue_run("scouting_scan", {"slug": "a"}, now=_now(-60))
        reg.enqueue_run("scouting_scan", {"slug": "b"}, now=_now())
        assert reg.claim_next_run("a:1")["id"] == first

    def test_kinds_filter_leaves_other_kinds_queued(self, reg):
        reg.enqueue_run("scouting_scan", {"slug": "a"})
        assert reg.claim_next_run("a:1", kinds=["analysis_run"]) is None
        assert reg.get_run(reg.list_runs()[0]["id"])["state"] == "queued"

    def test_an_empty_queue_is_not_an_error(self, reg):
        assert reg.claim_next_run("a:1") is None


class TestFairness:
    """At most one active run per attributed user — the plan's step-5 hook."""

    def test_a_user_with_a_running_row_does_not_get_a_second(self, reg):
        reg.enqueue_run("scouting_scan", {"slug": "a"}, requested_by="alice", now=_now(-60))
        second = reg.enqueue_run("scouting_scan", {"slug": "b"}, requested_by="alice")
        first = reg.claim_next_run("a:1")
        assert first["requested_by"] == "alice"
        assert reg.claim_next_run("b:2") is None, "alice got two runs at once"
        # …and the second row is waiting, not lost.
        assert reg.get_run(second)["state"] == "queued"

    def test_the_waiting_row_is_claimable_once_the_first_finishes(self, reg):
        reg.enqueue_run("scouting_scan", {"slug": "a"}, requested_by="alice", now=_now(-60))
        second = reg.enqueue_run("scouting_scan", {"slug": "b"}, requested_by="alice")
        first = reg.claim_next_run("a:1")
        reg.finish_run(first["id"], "succeeded")
        assert reg.claim_next_run("b:2")["id"] == second

    def test_one_user_does_not_block_another(self, reg):
        reg.enqueue_run("scouting_scan", {"slug": "a"}, requested_by="alice", now=_now(-60))
        reg.enqueue_run("scouting_scan", {"slug": "b"}, requested_by="bob")
        reg.claim_next_run("a:1")
        assert reg.claim_next_run("b:2")["requested_by"] == "bob"

    def test_the_unattributed_bucket_is_exempt(self, reg):
        """Every row is `requested_by=""` until trellis-auth lands. Applying
        fairness to that bucket would collapse the whole queue to one run at a
        time for everybody — a visible regression against the pre-queue
        behaviour, where each route spawned its own thread. This is the
        assertion that stops a later "fix" from tightening the rule without
        noticing what it costs."""
        reg.enqueue_run("scouting_scan", {"slug": "a"}, now=_now(-60))
        reg.enqueue_run("scouting_scan", {"slug": "b"})
        assert reg.claim_next_run("a:1") is not None
        assert reg.claim_next_run("b:2") is not None


class TestHeartbeat:
    def test_a_heartbeat_moves_the_timestamp(self, reg):
        run_id = reg.enqueue_run("scouting_scan", {"slug": "a"})
        reg.claim_next_run("a:1", now=_now(-100))
        before = reg.get_run(run_id)["heartbeat_at"]
        assert reg.heartbeat_run(run_id) is True
        assert reg.get_run(run_id)["heartbeat_at"] > before

    def test_a_terminal_row_reports_false(self, reg):
        """How the heartbeat thread learns its run was reconciled or cancelled
        out from under it, instead of writing heartbeats onto a finished row
        for ever."""
        run_id = reg.enqueue_run("scouting_scan", {"slug": "a"})
        reg.claim_next_run("a:1")
        reg.finish_run(run_id, "succeeded")
        assert reg.heartbeat_run(run_id) is False

    def test_the_heartbeat_thread_ticks_while_work_runs(self):
        from resource_explorer.run_queue import _Heartbeat

        class _Reg:
            def __init__(self):
                self.beats = 0

            def heartbeat_run(self, run_id):
                self.beats += 1
                return True

        r = _Reg()
        with _Heartbeat(r, "run-1", interval=0.01):
            deadline = threading.Event()
            deadline.wait(0.1)
        assert r.beats >= 1, "the run was never proven alive while it ran"

    def test_a_failing_heartbeat_does_not_kill_the_run(self):
        """A lost heartbeat makes a row a reconciliation candidate; it must not
        take down the work that is genuinely still running."""
        from resource_explorer.run_queue import _Heartbeat

        class _Reg:
            def heartbeat_run(self, run_id):
                raise RuntimeError("database went away")

        with _Heartbeat(_Reg(), "run-1", interval=0.01):
            threading.Event().wait(0.05)
        # No exception escaping the context manager is the assertion.


class TestCancel:
    def test_a_held_kind_row_is_never_cancelled(self, reg):
        """engine_action rows stand for Egeria engine actions RE has claimed: cancelling one locally would
        leave the action claimed with nothing to complete it. Refused here, so every caller gets it."""
        run_id = reg.enqueue_run("engine_action", {"engine_action_guid": "a-1"})
        assert reg.cancel_queued_run(run_id) is False
        assert reg.get_run(run_id)["state"] == "queued"

    def test_a_queued_row_cancels(self, reg):
        run_id = reg.enqueue_run("scouting_scan", {"slug": "a"})
        assert reg.cancel_queued_run(run_id) is True
        assert reg.get_run(run_id)["state"] == "cancelled"

    def test_a_claimed_row_is_refused_rather_than_mislabelled(self, reg):
        """Python threads cannot be interrupted. Writing `cancelled` over a row
        whose worker is still running it would be a false claim about live
        work — the exact failure run_reconciler.py exists to remove."""
        run_id = reg.enqueue_run("scouting_scan", {"slug": "a"})
        reg.claim_next_run("a:1")
        assert reg.cancel_queued_run(run_id) is False
        assert reg.get_run(run_id)["state"] == "claimed"


class TestReconciliation:
    """Stale heartbeat makes a candidate; only a dead pid makes a failure."""

    def _claimed_by(self, reg, runner, *, heartbeat_offset=-600):
        run_id = reg.enqueue_run("scouting_scan", {"slug": "a"})
        reg.claim_next_run("host:1", runner, now=_now(heartbeat_offset))
        return run_id

    def test_a_dead_claimer_fails_the_row_with_the_reason(self, reg):
        from resource_explorer.run_reconciler import reconcile_runs

        run_id = self._claimed_by(reg, {"pid": 2 ** 22, "started_at": "whenever"})
        result = reconcile_runs(reg)
        assert result["resolved"] == 1
        row = reg.get_run(run_id)
        assert row["state"] == "failed"
        assert "no longer running" in row["error"]
        assert "host:1" in row["error"]

    def test_a_live_claimer_is_left_alone_however_stale_its_heartbeat(self, reg):
        """A worker parked in a slow Egeria call is late, not gone. The
        heartbeat is a filter in front of the liveness check, never a verdict
        of its own."""
        from resource_explorer.run_reconciler import process_identity, reconcile_runs

        run_id = self._claimed_by(reg, process_identity())
        assert reconcile_runs(reg)["still_running"] == 1
        assert reg.get_run(run_id)["state"] == "claimed"

    def test_a_fresh_heartbeat_is_not_even_examined(self, reg):
        from resource_explorer.run_reconciler import reconcile_runs

        self._claimed_by(reg, {"pid": 2 ** 22, "started_at": "whenever"},
                         heartbeat_offset=0)
        assert reconcile_runs(reg)["resolved"] == 0

    def test_ignore_heartbeat_catches_a_dead_claimer_that_beat_recently(self, reg):
        """What worker startup uses. A pid that is provably gone is gone
        whether or not it heartbeated ten seconds before it died, and waiting
        three intervals to notice a claim the previous process abandoned would
        be a delay bought with nothing."""
        from resource_explorer.run_reconciler import reconcile_runs

        run_id = self._claimed_by(reg, {"pid": 2 ** 22, "started_at": "whenever"},
                                  heartbeat_offset=0)
        assert reconcile_runs(reg, ignore_heartbeat=True)["resolved"] == 1
        assert reg.get_run(run_id)["state"] == "failed"

    def test_a_row_with_no_recorded_owner_is_left_alone(self, reg):
        """"I cannot tell whether this is alive" must never resolve to "it is
        dead" — the same rule the activity-log reconciler holds to."""
        from resource_explorer.run_reconciler import reconcile_runs

        self._claimed_by(reg, {})
        assert reconcile_runs(reg)["left_alone"] == 1


class TestExecution:
    def test_a_kind_with_no_handler_fails_loudly(self, reg):
        """Rather than being left queued. A row nothing can run looks exactly
        like a queue nobody is draining."""
        from resource_explorer.run_queue import execute_run

        outcome = execute_run(
            {"id": "r1", "kind": "mystery", "target": "{}", "result_ref": ""}, reg,
        )
        assert outcome.state == "failed"
        assert "no handler" in outcome.error

    def test_a_successful_run_lands_succeeded(self, reg, monkeypatch):
        import resource_explorer.run_queue as rq

        run_id = reg.enqueue_run("scouting_scan", {"slug": "myproj"}, result_ref="")
        row = reg.claim_next_run("host:1", {"pid": os.getpid()})
        monkeypatch.setitem(rq.HANDLERS, "scouting_scan",
                            lambda target, ref: rq.RunOutcome(state="succeeded"))
        rq.execute_run(row, reg)
        assert reg.get_run(run_id)["state"] == "succeeded"
        assert reg.get_run(run_id)["started_at"]
        assert reg.get_run(run_id)["finished_at"]

    def test_a_handler_that_raises_still_writes_a_terminal_state(self, reg, monkeypatch):
        """The one way a run may legitimately end without a terminal state is
        the process dying — which is what reconciliation is for. Everything
        else has to record an outcome."""
        import resource_explorer.run_queue as rq

        run_id = reg.enqueue_run("scouting_scan", {"slug": "myproj"})
        row = reg.claim_next_run("host:1", {"pid": os.getpid()})

        def _boom(target, ref):
            raise RuntimeError("boom")

        monkeypatch.setitem(rq.HANDLERS, "scouting_scan", _boom)
        rq.execute_run(row, reg)
        assert reg.get_run(run_id)["state"] == "failed"
        assert "boom" in reg.get_run(run_id)["error"]

    def test_the_executing_process_stamps_itself_on_the_activity_entry(self, reg, monkeypatch):
        """docs/process-model.md F5, and the reason the enqueueing route no
        longer records ownership: the owner is whoever runs it."""
        import resource_explorer.run_queue as rq
        from resource_explorer.activity_logger import log_survey
        from resource_explorer.run_reconciler import owner_of

        activity_id = log_survey(
            reg, entity_type="repo", entity_slug="myproj", entity_name="My Project",
            entity_location="", intent="scouting", status="running", summary="…",
        )
        assert owner_of(reg.get_activity(activity_id)["detail"]) == {}

        reg.enqueue_run("scouting_scan", {"slug": "myproj"}, result_ref=activity_id)
        row = reg.claim_next_run("host:1", {"pid": os.getpid()})
        monkeypatch.setitem(rq.HANDLERS, "scouting_scan",
                            lambda target, ref: rq.RunOutcome(state="succeeded"))
        rq.execute_run(row, reg)

        owner = owner_of(reg.get_activity(activity_id)["detail"])
        assert owner.get("pid") == os.getpid()


class TestQueueConsumptionSwitch:
    def test_off_by_env(self, monkeypatch):
        from resource_explorer.run_queue import queue_consumption_enabled

        monkeypatch.setenv("EXPLORER_RUN_QUEUE_ENABLED", "false")
        assert queue_consumption_enabled() is False
        monkeypatch.setenv("EXPLORER_RUN_QUEUE_ENABLED", "true")
        assert queue_consumption_enabled() is True

    def test_on_by_default(self, monkeypatch):
        from resource_explorer.run_queue import queue_consumption_enabled

        monkeypatch.delenv("EXPLORER_RUN_QUEUE_ENABLED", raising=False)
        assert queue_consumption_enabled() is True


class TestEmbeddedWorkerExecutesQueuedRows:
    def test_claim_and_execute_once_drains_one_row(self, reg, monkeypatch):
        """`make dev` behaviour: a web process with the default
        --embed-worker must execute queued rows itself, or every click in the
        dev profile queues work nothing runs."""
        import resource_explorer.run_queue as rq

        ran = []
        monkeypatch.setitem(rq.HANDLERS, "scouting_scan",
                            lambda target, ref: ran.append(target) or rq.RunOutcome(state="succeeded"))
        run_id = reg.enqueue_run("scouting_scan", {"slug": "myproj"})

        claimed = rq.claim_and_execute_once(reg)
        assert claimed is not None and claimed["id"] == run_id
        assert ran == [{"slug": "myproj"}]
        assert reg.get_run(run_id)["state"] == "succeeded"

    def test_nothing_queued_is_reported_as_nothing_claimed(self, reg):
        import resource_explorer.run_queue as rq

        assert rq.claim_and_execute_once(reg) is None

    def test_the_worker_role_starts_the_queue_loop(self):
        """Structural: run_worker must reach start_queue_runner, or a standalone
        `resource-explorer worker` holds the background loops and drains
        nothing."""
        from pathlib import Path

        src = (Path(__file__).resolve().parents[1] / "resource_explorer"
               / "worker.py").read_text()
        body = src.split("def run_worker(")[1]
        assert "start_queue_runner()" in body
        assert "stop_queue_runner()" in body

    def test_the_queue_loop_is_not_leader_elected(self):
        """The one worker-owned loop that must NOT take an advisory lock: every
        extra queue consumer is extra throughput (SKIP LOCKED is what makes
        that safe), whereas a leader would make the queue exactly as fast as
        its leader. Pinned because "it doesn't take a lock" reads like an
        oversight."""
        from resource_explorer.worker import loop_specs

        assert "run-queue" not in [s.name for s in loop_specs()]


@pytest.mark.requires_pgvector
class TestPostgresClaimSemantics:
    """SKIP LOCKED, against the backend that actually has it.

    A SQLite version of this test passes for the wrong reason — SQLite
    serialises writers, so "only one claimer wins" holds there without any of
    the machinery being exercised. See this module's docstring.
    """

    def test_two_concurrent_claimers_and_only_one_wins(self, pg_registry):
        run_id = pg_registry.enqueue_run("scouting_scan", {"slug": "concurrent"})

        results: list = []
        barrier = threading.Barrier(2)

        def _claim(name: str) -> None:
            # A fresh registry per thread: connections are not shared across
            # threads, and two claimers on one connection would serialise
            # through the connection rather than through the database, which is
            # not the thing being tested.
            reg = ProjectRegistry(database_url=pg_registry.database_url)
            barrier.wait(timeout=10)
            results.append(reg.claim_next_run(name))

        threads = [threading.Thread(target=_claim, args=(f"host:{i}",)) for i in (1, 2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=20)

        won = [r for r in results if r is not None]
        assert len(won) == 1, f"expected exactly one winner, got {len(won)}"
        assert won[0]["id"] == run_id
        assert pg_registry.get_run(run_id)["state"] == "claimed"

    def test_two_claimers_take_two_different_rows(self, pg_registry):
        """The other half of SKIP LOCKED, and the half a blocking lock would
        get wrong by being slow rather than by being incorrect: a second
        claimer must skip the locked row and take the next one, not wait."""
        ids = {pg_registry.enqueue_run("scouting_scan", {"slug": f"r{i}"}) for i in range(2)}

        claimed: list = []
        barrier = threading.Barrier(2)

        def _claim(name: str) -> None:
            reg = ProjectRegistry(database_url=pg_registry.database_url)
            barrier.wait(timeout=10)
            row = reg.claim_next_run(name)
            if row is not None:
                claimed.append(row["id"])

        threads = [threading.Thread(target=_claim, args=(f"host:{i}",)) for i in (1, 2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=20)

        assert set(claimed) == ids

    def test_a_claimer_locks_only_the_row_it_takes(self, pg_registry, monkeypatch):
        """Both claimers have READ their candidates before either has taken a lock (a barrier inside the scan
        forces it). A scan that locked every candidate it read would leave the second claimer nothing."""
        from resource_explorer import presser_hold

        ids = {pg_registry.enqueue_run("scouting_scan", {"slug": f"s{i}"}) for i in range(2)}
        real = presser_hold.claimable_by
        met = threading.Barrier(2)
        seen = threading.local()

        def _meet_once(*a, **kw):
            if not getattr(seen, "done", False):
                seen.done = True
                met.wait(timeout=5)
            return real(*a, **kw)

        monkeypatch.setattr(presser_hold, "claimable_by", _meet_once)
        claimed: list = []

        def _claim(name: str) -> None:
            reg = ProjectRegistry(database_url=pg_registry.database_url)
            try:
                row = reg.claim_next_run(name)
            except threading.BrokenBarrierError:
                return
            if row is not None:
                claimed.append(row["id"])

        threads = [threading.Thread(target=_claim, args=(f"host:{i}",)) for i in (1, 2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=20)

        assert set(claimed) == ids
