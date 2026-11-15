"""TR-20 and TR-21 (Architect ruling 2026-11-15, RULING-PREFECT-NO-LOCAL-FALLBACK-AND-RESULT-TRANSPORT-2026-11-15).

Found on hedwig 2026-11-15: a COMPLETED container flow run whose result file could not be read from RE's process
("outside of the base path") was run AGAIN locally: two step_runs rows, two downloads, one dispatch.

TR-20: a local fallback is allowed only before create_flow_run has returned an id; after it, every outcome is the
flow run's (completed, result_unreadable, mismatch, failed, cancelled, unknown), recorded engine=prefect with the id.
TR-21: the result is the flow_run_results row the flow wrote, read by flow_run_id only; one work row per flow run.
Every case asserts the double-run guard: the local runner is called exactly as often as the row says (0 after an id).
"""
from __future__ import annotations

import json
import types
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

import resource_explorer.surveyors.prefect_adapter as adapter
from resource_explorer.prefect import flows
from resource_explorer.registry import ProjectRegistry
from resource_explorer.surveyors import step_cost_observer

RUN_ID = "11111111-2222-3333-4444-555555555555"


def _state(kind, message=""):
    st = MagicMock()
    st.name = kind
    st.type = types.SimpleNamespace(value=kind.upper())
    st.message = message
    st.is_completed.return_value = kind == "Completed"
    st.is_failed.return_value = kind == "Failed"
    st.is_cancelled.return_value = kind == "Cancelled"
    return st


def _client(states=None, *, create_raises=None, read_raises=None, deployment_missing=False):
    c = MagicMock()
    c.__aenter__ = AsyncMock(return_value=c)
    c.__aexit__ = AsyncMock(return_value=False)

    async def read_deployment(name):
        if deployment_missing:
            raise LookupError(f"{name} not found")
        return types.SimpleNamespace(id="dep", name=name, work_pool_name="resource-explorer-pool")

    c.read_deployment_by_name = AsyncMock(side_effect=read_deployment)
    c.read_workers_for_work_pool = AsyncMock(return_value=[
        types.SimpleNamespace(name="re-host-worker-x", status=types.SimpleNamespace(value="ONLINE"))])
    c.create_flow_run_from_deployment = AsyncMock(
        side_effect=create_raises, return_value=types.SimpleNamespace(id=RUN_ID))
    seq = list(states or [_state("Completed")])

    async def read(_id):
        if read_raises:
            raise read_raises
        return types.SimpleNamespace(state=seq.pop(0) if len(seq) > 1 else seq[0])

    c.read_flow_run = read
    c.set_flow_run_state = AsyncMock()
    return c


class _Server:
    """A Prefect server that keeps runs by idempotency key, as Prefect does: a create with a key it has seen returns
    the existing run. `fail_first_after_creating`: the first create MAKES the run, then the response is lost."""

    def __init__(self, fail_first_after_creating=False):
        self.runs: dict[str, object] = {}
        self.keys_seen: list[str] = []
        self.fail_first = fail_first_after_creating

    async def create(self, deployment_id, parameters, tags, idempotency_key=None):
        self.keys_seen.append(idempotency_key)
        run = self.runs.setdefault(idempotency_key, types.SimpleNamespace(id=RUN_ID))
        if self.fail_first:
            self.fail_first = False
            raise httpx.ReadTimeout("the response was lost")
        return run


class _Results:
    def __init__(self, rows=None):
        self.rows = rows if rows is not None else {RUN_ID: {"output": {"findings": 3}, "norm_hash": "h",
                                                             "step_run_id": 7}}

    def read_flow_run_result(self, flow_run_id):
        row = self.rows.get(flow_run_id)
        return dict(row, flow_run_id=flow_run_id) if row else None


def _dispatch(client, results=None, timeout=60):
    """run_prefect_step through the real REST path; the local runner counts its calls (the double-run guard)."""
    cfg = types.SimpleNamespace(prefect=types.SimpleNamespace(
        enabled=True, api_url="http://x/api", work_pool="resource-explorer-pool", step_timeout_seconds=timeout))
    local = MagicMock(return_value={"ran": "locally"})
    info: dict = {}
    out = exc = None
    with patch.object(adapter, "get_config", lambda: cfg), \
         patch.object(adapter, "re_prefect_client", return_value=client), \
         patch.object(adapter, "_results_registry", lambda: results or _Results()), \
         patch.object(adapter.asyncio, "sleep", AsyncMock()), \
         patch.object(adapter.run_surveyor_step_task, "fn", local):
        try:
            out = adapter.run_prefect_step("repo", "r", "repo_secret_scan", {}, dispatch_info=info)
        except Exception as e:  # noqa: BLE001 - the outcome under test
            exc = e
    return out, exc, info, local.call_count


class TestTr20OutcomeTable:
    def test_completed_and_read(self):
        out, exc, info, local = _dispatch(_client([_state("Completed")]))
        assert exc is None and out == {"findings": 3}
        assert info["engine"] == "prefect" and info["flow_run_id"] == RUN_ID and local == 0

    def test_completed_but_no_result_row_is_unreadable_and_never_run_locally(self):
        _, exc, info, local = _dispatch(_client([_state("Completed")]), results=_Results({}))
        assert isinstance(exc, adapter.PrefectResultUnreadable) and exc.status == "result_unreadable"
        assert info["engine"] == "prefect" and info["flow_run_id"] == RUN_ID and info["status"] == "result_unreadable"
        assert local == 0, "the hedwig 2026-11-15 double run"

    def test_a_summary_that_disagrees_with_the_row_is_a_mismatch(self):
        msg = json.dumps({"norm_hash": "other", "step_run_id": 7})
        out, exc, info, local = _dispatch(_client([_state("Completed", msg)]))
        assert isinstance(exc, adapter.PrefectRunOutcome) and exc.status == "mismatch" and out is None
        assert info["status"] == "mismatch" and local == 0

    def test_a_summary_that_agrees_is_used(self):
        msg = json.dumps({"norm_hash": "h", "step_run_id": 7, "annotations": 1})
        out, exc, _, local = _dispatch(_client([_state("Completed", msg)]))
        assert exc is None and out == {"findings": 3} and local == 0

    @pytest.mark.parametrize("kind,state", [("Failed", "FAILED"), ("Crashed", "CRASHED")])
    def test_failed_or_crashed(self, kind, state):
        _, exc, info, local = _dispatch(_client([_state(kind)]))
        assert isinstance(exc, adapter.PrefectFlowRunFailed) and exc.status == "failed" and exc.state == state
        assert info["engine"] == "prefect" and info["flow_run_id"] == RUN_ID and local == 0

    def test_cancelled(self):
        _, exc, info, local = _dispatch(_client([_state("Cancelled")]))
        assert isinstance(exc, adapter.PrefectFlowRunCancelled) and info["status"] == "cancelled" and local == 0

    def test_a_wait_that_times_out_is_unknown(self):
        _, exc, info, local = _dispatch(_client([_state("Running")]), timeout=0.000001)
        assert isinstance(exc, adapter.PrefectFlowRunTimeout) and exc.status == "unknown"
        assert info["flow_run_id"] == RUN_ID and local == 0

    def test_a_run_that_cannot_be_read_is_unknown(self):
        _, exc, info, local = _dispatch(_client(read_raises=ConnectionError("server went away")))
        assert isinstance(exc, adapter.PrefectRunOutcome) and exc.status == "unknown"
        assert "server went away" in str(exc) and info["flow_run_id"] == RUN_ID and local == 0

    def test_a_deployment_that_is_not_found_falls_back_once(self):
        out, exc, info, local = _dispatch(_client(deployment_missing=True))
        assert exc is None and out == {"ran": "locally"} and local == 1
        assert info["engine"] == "local" and info["flow_run_id"] == "" and "not found" in info["dispatch_failed"]

    def test_a_create_that_could_not_connect_falls_back_once(self):
        """R1: a connect failure means the request was never sent, so no run can exist."""
        _, exc, info, local = _dispatch(_client(create_raises=httpx.ConnectError("connection refused")))
        assert exc is None and local == 1 and info["engine"] == "local" and "refused" in info["dispatch_failed"]

    def test_a_create_that_failed_after_the_server_made_the_run_is_followed_not_run_locally(self):
        """R1 (Architect read-back 2026-11-15): the first create times out on the response although the server made
        the run; the retry with the same idempotency key returns that run, which is followed."""
        server = _Server(fail_first_after_creating=True)
        c = _client([_state("Completed")])
        c.create_flow_run_from_deployment = AsyncMock(side_effect=server.create)
        out, exc, info, local = _dispatch(c)
        assert exc is None and out == {"findings": 3} and local == 0
        assert info["engine"] == "prefect" and info["flow_run_id"] == RUN_ID
        assert len(server.runs) == 1 and len(server.keys_seen) == 2 and len(set(server.keys_seen)) == 1

    def test_two_failing_creates_are_unknown_and_never_run_locally(self):
        _, exc, info, local = _dispatch(_client(create_raises=RuntimeError("422 bad parameters")))
        assert isinstance(exc, adapter.PrefectRunOutcome) and exc.status == "unknown" and local == 0
        assert "idempotency key" in str(exc) and info["engine"] == "prefect" and info["status"] == "unknown"

    def test_a_client_that_fails_to_close_after_a_completed_run_returns_the_result(self):
        """R2: closing the client after the result was read must not turn into a local run."""
        c = _client([_state("Completed")])
        c.__aexit__ = AsyncMock(side_effect=RuntimeError("close failed"))
        out, exc, info, local = _dispatch(c)
        assert exc is None and out == {"findings": 3} and local == 0 and info["engine"] == "prefect"

    def test_a_client_that_fails_to_close_after_a_failed_run_keeps_the_runs_outcome(self):
        c = _client([_state("Failed")])
        c.__aexit__ = AsyncMock(side_effect=RuntimeError("close failed"))
        _, exc, info, local = _dispatch(c)
        assert isinstance(exc, adapter.PrefectFlowRunFailed) and local == 0 and info["flow_run_id"] == RUN_ID

    def test_a_client_that_fails_to_close_before_any_create_still_falls_back(self):
        c = _client(deployment_missing=True)
        c.__aexit__ = AsyncMock(side_effect=RuntimeError("close failed"))
        _, exc, info, local = _dispatch(c)
        assert exc is None and local == 1 and info["engine"] == "local"

    def test_the_in_process_flow_failing_is_never_run_again_locally(self):
        cfg = types.SimpleNamespace(prefect=types.SimpleNamespace(enabled=True, api_url="http://x/api",
                                                                  work_pool="resource-explorer-pool"))
        local = MagicMock(return_value={"ran": "locally"})
        boom = MagicMock(side_effect=RuntimeError("step blew up"))
        info: dict = {}
        with patch.object(adapter, "get_config", lambda: cfg), \
             patch.object(adapter, "check_prefect_reachable_sync", lambda: (True, "ok")), \
             patch.object(adapter, "_in_process_step_flow", lambda: boom), \
             patch.object(adapter.run_surveyor_step_task, "fn", local), \
             pytest.raises(adapter.PrefectFlowRunFailed):
            adapter.run_prefect_step("database", "d", "s", {}, dispatch_info=info, credential_ref="ref-1")
        assert local.call_count == 0 and info["engine"] == "prefect" and info["status"] == "failed"


@pytest.fixture
def registry(tmp_path):
    return ProjectRegistry(db_path=str(tmp_path / "t.db"))


class TestTr21FlowRunResults:
    def test_a_second_write_for_the_same_flow_run_never_overwrites(self, registry, caplog):
        assert registry.write_flow_run_result("fr-1", entity_type="repo", slug="r", step_key="s", step_run_id=1,
                                              output_json='{"a": 1}', norm_hash="h1")
        assert not registry.write_flow_run_result("fr-1", entity_type="repo", slug="r", step_key="s",
                                                  step_run_id=2, output_json='{"a": 2}', norm_hash="h2")
        kept = registry.read_flow_run_result("fr-1")
        assert kept["output"] == {"a": 1} and kept["norm_hash"] == "h1" and kept["step_run_id"] == 1
        assert "different norm_hash" in caplog.text

    def test_two_runs_with_the_same_norm_hash_each_read_their_own_row(self, registry):
        for fr, sid in (("fr-a", 11), ("fr-b", 12)):
            registry.write_flow_run_result(fr, entity_type="repo", slug="r", step_key="s", step_run_id=sid,
                                           output_json='{"same": true}', norm_hash="same")
        with patch.object(adapter, "_results_registry", lambda: registry):
            a = adapter._read_flow_run_output("fr-a", json.dumps({"norm_hash": "same", "step_run_id": 11}))
            b = adapter._read_flow_run_output("fr-b", json.dumps({"norm_hash": "same", "step_run_id": 12}))
            with pytest.raises(adapter.PrefectRunOutcome) as ei:  # fr-a's summary checked against fr-b's row
                adapter._read_flow_run_output("fr-b", json.dumps({"norm_hash": "same", "step_run_id": 11}))
        assert a == b == {"same": True} and ei.value.status == "mismatch"

    def test_rows_past_retention_are_pruned(self, registry):
        registry.write_flow_run_result("old", entity_type="repo", slug="r", step_key="s", step_run_id=None,
                                       output_json="{}", norm_hash="h")
        assert registry.prune_flow_run_results(now=datetime.now(UTC) + timedelta(days=31)) == 1
        assert registry.read_flow_run_result("old") is None
        assert registry.FLOW_RUN_RESULTS_KEEP_DAYS == 30


class TestTr21OneWorkRowPerFlowRun:
    """The worker's flow body (`_run_step_as_flow_run`) on a real registry, with the runner replaced."""

    @staticmethod
    def _run(registry, entity_type, runner, flow_run_id="fr-9"):
        with patch.object(flows, "ProjectRegistry", lambda *a, **k: registry), \
             patch.object(flows, "run_surveyor_step_task", runner):
            return flows._run_step_as_flow_run(flow_run_id, entity_type, "s1", "step_x", {}, keep_result=True)

    def test_a_runner_that_records_its_own_row_gets_it_tagged_and_no_second(self, registry):
        def repo_runner(**kw):  # the repo orchestrator records its own row, as SurveyOrchestrator.run does
            with step_cost_observer.observe("step_x", "", "", entity_type="repo", slug="s1") as obs:
                pass
            step_cost_observer.record(registry, "s1", obs[0], "2026-11-15T00:00:00Z")
            return {"annotations": [{"a": 1}]}

        _, summary = self._run(registry, "repo", repo_runner)
        rows = registry.query_step_runs(slug="s1", step_key="step_x")
        assert [(r["executor"], r["source"], r["flow_run_id"]) for r in rows] == [("prefect", "prefect", "fr-9")]
        assert summary["step_run_id"] == rows[0]["id"] and summary["annotations"] == 1

    @pytest.mark.parametrize("entity_type", ["database", "filesystem"])
    def test_a_runner_that_records_none_gets_exactly_one_from_the_flow(self, registry, entity_type):
        _, summary = self._run(registry, entity_type, lambda **kw: {"ok": True})
        rows = registry.query_step_runs(slug="s1", step_key="step_x", entity_type=entity_type)
        assert [(r["executor"], r["flow_run_id"]) for r in rows] == [("prefect", "fr-9")]
        kept = registry.read_flow_run_result("fr-9")
        assert kept["output"] == {"ok": True} and kept["step_run_id"] == rows[0]["id"]
        _, norm_hash = flows.output_json_and_hash({"ok": True})
        assert kept["norm_hash"] == norm_hash == summary["norm_hash"]

    def test_outside_a_flow_run_rows_stay_local(self, registry):
        with step_cost_observer.observe("step_y", "", "", entity_type="repo", slug="s1") as obs:
            pass
        assert obs[0].executor == "local" and obs[0].flow_run_id == ""


def test_the_targeted_ddl_file_is_the_registrys_own_statement():
    """docs/ddl-flow-run-results.sql is what is applied by hand (never a full init); it must say what init says."""
    import re
    from pathlib import Path

    def norm(t):
        return re.sub(r"\s+", " ", t).replace("( ", "(").replace(" )", ")").strip()

    sql = (Path(__file__).resolve().parents[1] / "docs" / "ddl-flow-run-results.sql").read_text()
    body = sql[sql.index("CREATE TABLE"):sql.index(";", sql.index("CREATE TABLE"))]
    assert norm(body) == norm(ProjectRegistry.FLOW_RUN_RESULTS_DDL)
    assert "lock_timeout" in sql and "statement_timeout" in sql


def test_every_create_carries_an_idempotency_key():
    c = _client([_state("Completed")])
    _dispatch(c)
    key = c.create_flow_run_from_deployment.await_args.kwargs["idempotency_key"]
    assert isinstance(key, str) and len(key) == 32
