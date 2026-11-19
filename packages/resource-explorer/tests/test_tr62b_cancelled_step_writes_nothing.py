"""TR-62b: a cancelled dispatched step stops, and writes nothing after the cancel.

hedwig 2026-11-18, run fbfab6f3 (repo_file_inventory): RE's 1200 s deadline cancelled the run by FORCING CANCELLED.
The ProcessWorker stops a process only on CANCELLING, so the process ran on, wrote its flow_run_results row 68 s
later and set COMPLETED over CANCELLED. Every Prefect call here is a fake; the registry is a temp SQLite file.
"""
from __future__ import annotations

import asyncio
import types
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from resource_explorer.prefect import flows
from resource_explorer.registry import Project, ProjectRegistry
from resource_explorer.surveyors import prefect_adapter as adapter
from resource_explorer.surveyors import run_cancellation
from resource_explorer.surveyors.sub_surveyors.file_inventory import FileInventorySurveyor

SLUG = "tr62b_repo"   # already normalised: the registry stores slugs with "_"


class FakeRun:
    """One flow run's state, as the cancel check reads it. `cancel()` is RE's deadline arriving."""

    def __init__(self, run_id: str):
        self.id = run_id
        self.state = "RUNNING"
        self.reads = 0

    def cancel(self):
        self.state = "CANCELLING"

    def is_cancelled(self) -> bool:
        self.reads += 1
        return self.state in run_cancellation.CANCEL_STATES


def _project():
    return Project(slug=SLUG, display_name=SLUG, github_url=f"https://github.com/o/{SLUG}", description="")


@pytest.fixture
def registry(tmp_path):
    reg = ProjectRegistry(db_path=str(tmp_path / "t.db"))
    reg.add(_project())
    reg.upsert_file_inventory(SLUG, [("README.md", 10), ("old.py", 20)])
    return reg


@pytest.fixture
def fresh_tree(tmp_path):
    root = tmp_path / "zipball"
    (root / "src").mkdir(parents=True)
    (root / "README.md").write_text("new readme\n")
    (root / "src" / "new.py").write_text("print('new')\n")
    return root


def _inventory(registry):
    with registry._conn() as conn:
        rows = conn.execute(
            "SELECT file_path, file_size_bytes, indexed_at, flow_run_id FROM project_file_inventory "
            "WHERE project_slug = ? ORDER BY file_path", (SLUG,)).fetchall()
    return [tuple(dict(r).values()) for r in rows]


def _inventory_runner(registry, root, run: FakeRun | None):
    """The step's runner: the REAL FileInventorySurveyor, whose mid-step replace goes through
    IngestionPipeline._store_file_inventory -> registry.upsert_file_inventory. The cancel lands while it runs."""

    def runner(**kw):
        if run is not None:
            run.cancel()
        anns = FileInventorySurveyor(_project(), registry, str(root)).run()
        return {"annotations": [a.check_name for a in anns]}

    return runner


def _run_flow_body(registry, runner, run: FakeRun):
    with patch.object(flows, "ProjectRegistry", lambda *a, **k: registry), \
         patch.object(flows, "run_surveyor_step_task", runner), \
         patch.object(flows, "_cancel_check_for", lambda fr, hb=None: run.is_cancelled):
        return flows._run_step_as_flow_run(run.id, "repo", SLUG, "repo_file_inventory", {}, keep_result=True)


class TestCancelMidStep:
    def test_cancelled_mid_step_and_finishing_anyway_leaves_everything_as_it_was(self, registry, fresh_tree):
        before = _inventory(registry)
        assert [r[0] for r in before] == ["README.md", "old.py"]          # a real kept inventory to protect
        run = FakeRun("fr-cancelled")
        with pytest.raises(run_cancellation.RunCancelled) as ei:
            _run_flow_body(registry, _inventory_runner(registry, fresh_tree, run), run)
        assert ei.value.where == "replacing project_file_inventory"   # stopped AT the mid-step replace
        assert _inventory(registry) == before                         # kept inventory unchanged, row for row
        assert registry.read_flow_run_result(run.id) is None          # no flow_run_results row
        assert registry.step_run_ids_for_flow_run(run.id) == []       # no work row
        assert registry.query_step_runs(slug=SLUG, step_key="repo_file_inventory") == []

    def test_a_runner_that_swallows_errors_cannot_swallow_the_cancel(self, registry, fresh_tree):
        """FileInventorySurveyor.run catches Exception and returns a 'failed' annotation; RunCancelled is a
        BaseException precisely so that catch does not turn the cancel into one more annotation."""
        assert not issubclass(run_cancellation.RunCancelled, Exception)

    def test_a_cancel_after_the_step_still_stops_the_result_row(self, registry):
        run = FakeRun("fr-late")

        def runner(**kw):  # the step's own work is done; the cancel lands before the flow keeps anything
            run.cancel()
            return {"ok": True}

        with pytest.raises(run_cancellation.RunCancelled) as ei:
            _run_flow_body(registry, runner, run)
        assert ei.value.where == "recording the step's work row"
        assert registry.read_flow_run_result(run.id) is None
        assert registry.step_run_ids_for_flow_run(run.id) == []

    def test_the_next_run_of_the_same_step_is_not_refused_and_records_its_provenance(self, registry, fresh_tree):
        cancelled = FakeRun("fr-cancelled")
        with pytest.raises(run_cancellation.RunCancelled):
            _run_flow_body(registry, _inventory_runner(registry, fresh_tree, cancelled), cancelled)
        nxt = FakeRun("fr-next")
        _, summary = _run_flow_body(registry, _inventory_runner(registry, fresh_tree, None), nxt)
        rows = _inventory(registry)
        assert sorted(r[0] for r in rows) == ["README.md", "src/new.py"]
        assert {r[3] for r in rows} == {"fr-next"}                       # provenance: the run that wrote them
        assert registry.read_flow_run_result("fr-next") is not None
        assert summary["flow_run_id"] == "fr-next"

    def test_outside_a_flow_run_the_writer_does_not_check_and_records_no_run(self, registry):
        registry.upsert_file_inventory(SLUG, [("cli.py", 1)])
        assert _inventory(registry)[0][3] is None                       # NULL: CLI / in-process write

    def test_the_flow_ends_cancelled_never_completed(self):
        exc = run_cancellation.RunCancelled("fr-x", "writing flow_run_results")
        with patch.object(flows, "_run_step_as_flow_run", side_effect=exc):
            state = flows.re_survey_flow.fn("repo", SLUG, "repo_file_inventory", {})
        assert state.is_cancelled() and not state.is_completed()
        assert "writing flow_run_results" in state.message

    def test_the_in_process_flow_does_not_let_the_base_exception_escape(self):
        exc = run_cancellation.RunCancelled("fr-x", "replacing project_file_inventory")
        with patch.object(flows, "_run_step_as_flow_run", side_effect=exc):
            state = flows.re_survey_step_in_process_flow.fn("repo", SLUG, "repo_file_inventory", {})
        assert state.is_cancelled()


class TestOrchestratorChecks:
    def test_a_cancelled_run_stops_between_steps_and_keeps_no_snapshot(self, tmp_path):
        """The repo orchestrator checks once per step and before its own work rows and kept snapshot."""
        from resource_explorer.surveyors import survey_orchestrator as so

        src = open(so.__file__).read()
        assert src.count("run_cancellation.raise_if_cancelled(") == 4
        for where in ("starting step", "recording the work row of {producer}",
                      "recording the work row of {step_key}", "keeping the survey snapshot"):
            assert where in src


class TestCancelRequest:
    def test_the_deadline_cancel_sends_cancelling_unforced(self):
        client = MagicMock()
        client.set_flow_run_state = AsyncMock(return_value=types.SimpleNamespace(
            status=types.SimpleNamespace(value="ACCEPT"), details=None))
        assert asyncio.run(adapter._cancel_flow_run_best_effort(client, "id-1")) == ""
        kwargs = client.set_flow_run_state.await_args.kwargs
        assert kwargs["state"].type.value == "CANCELLING"
        assert "force" not in kwargs

    def test_a_refused_cancel_is_reported_not_called_a_cancel(self):
        client = MagicMock()
        client.set_flow_run_state = AsyncMock(return_value=types.SimpleNamespace(
            status=types.SimpleNamespace(value="ABORT"),
            details=types.SimpleNamespace(reason="already COMPLETED")))
        words = asyncio.run(adapter._cancel_flow_run_best_effort(client, "id-1"))
        assert "refused" in words and "already COMPLETED" in words

    def test_the_waiter_disowns_a_run_at_cancelling(self):
        state = MagicMock()
        state.is_completed.return_value = False
        state.is_failed.return_value = False
        state.is_cancelled.return_value = False
        state.type = types.SimpleNamespace(value="CANCELLING")
        client = MagicMock()
        client.read_flow_run = AsyncMock(return_value=types.SimpleNamespace(state=state))

        async def no_sleep(_):
            return None

        with pytest.raises(adapter.PrefectFlowRunCancelled):
            asyncio.run(adapter._await_flow_run(client, "id-1", "id-1", timeout_seconds=60, poll_interval=1,
                                                clock=lambda: 0.0, sleep=no_sleep))


class TestCancelCheck:
    def test_a_state_read_failure_answers_no_and_warns_once(self, caplog):
        def boom():
            raise ConnectionError("prefect gone")

        warned: list = []
        for _ in range(3):
            assert run_cancellation.prefect_run_is_cancelled(
                "11111111-1111-1111-1111-111111111111", client_factory=boom, _warned=warned) is False
        assert caplog.text.count("cancel check skipped") == 1

    @pytest.mark.parametrize("kind,cancelled", [("RUNNING", False), ("CANCELLING", True), ("CANCELLED", True),
                                                ("COMPLETED", False)])
    def test_reads_the_runs_state_through_a_sync_client(self, kind, cancelled):
        run = types.SimpleNamespace(state=types.SimpleNamespace(type=types.SimpleNamespace(value=kind)))
        client = MagicMock()
        client.__enter__ = MagicMock(return_value=client)
        client.__exit__ = MagicMock(return_value=False)
        client.read_flow_run = MagicMock(return_value=run)
        assert run_cancellation.prefect_run_is_cancelled(
            "11111111-1111-1111-1111-111111111111", client_factory=lambda: client) is cancelled

    def test_no_scope_no_check(self):
        run_cancellation.raise_if_cancelled("anything")   # outside a flow run: a no-op
