"""Prefect step dispatch must not hang (2026-10-02).

`_run_prefect_step_api` polled a flow run in `while True` with no deadline; a
work pool with no online worker left the run Scheduled forever and the whole
survey hung in the web server's worker thread with no error and no record.

Layer 1: no ONLINE worker on the pool -> no flow run is created, honest error.
Layer 2: the poll is bounded; on expiry the run is cancelled best-effort and an
honest error naming the flow run id is raised (never a pass, never zero
findings). Clock/sleep are injected: nothing here sleeps for real, and the
Prefect client is a stub (no ambient get_client(), no server).
"""
from __future__ import annotations

import asyncio
import types
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import resource_explorer.config as config_module
import resource_explorer.surveyors.prefect_adapter as adapter
from resource_explorer.surveyors import survey_definition_executor as sde_module
from resource_explorer.surveyors.survey_definition_executor import (
    ResourceTypeAdapter, SurveyDefinitionExecutor, register_adapter,
)
from resource_explorer.surveyors.survey_definition_reader import SurveyDefinition, SurveyStep

FLOW_RUN_ID = "11111111-2222-3333-4444-555555555555"


def _cfg(timeout=None):
    prefect = types.SimpleNamespace(enabled=True, api_url="http://x/api", work_pool="cfg-pool")
    if timeout is not None:
        prefect.step_timeout_seconds = timeout
    return types.SimpleNamespace(prefect=prefect)


class FakeClock:
    def __init__(self):
        self.now = 0.0
        self.sleeps = []

    def __call__(self):
        return self.now

    async def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


def _state(kind):
    st = MagicMock()
    st.name = kind
    st.type = kind
    st.message = ""
    st.is_completed.return_value = kind == "Completed"
    st.is_failed.return_value = kind == "Failed"
    st.is_cancelled.return_value = kind == "Cancelled"
    st.result = AsyncMock(return_value={"findings": 3})
    return st


def _client(*, workers, states, cancel_raises=None):
    """A stub PrefectClient. `states` is consumed one per read_flow_run; the
    last one repeats."""
    c = MagicMock()
    c.__aenter__ = AsyncMock(return_value=c)
    c.__aexit__ = AsyncMock(return_value=False)
    c.read_deployment_by_name = AsyncMock(
        return_value=types.SimpleNamespace(id="dep-1", work_pool_name="resource-explorer-pool"))
    c.read_workers_for_work_pool = AsyncMock(return_value=workers)
    c.create_flow_run_from_deployment = AsyncMock(return_value=types.SimpleNamespace(id=FLOW_RUN_ID))
    seq = list(states)

    async def read(_id):
        st = seq.pop(0) if len(seq) > 1 else seq[0]
        return types.SimpleNamespace(state=st)

    c.read_flow_run = read
    c.set_flow_run_state = AsyncMock(side_effect=cancel_raises)
    return c


def _worker(status):
    return types.SimpleNamespace(name="w", status=types.SimpleNamespace(value=status))


def _run_api(client, clock, timeout=None, cfg=None):
    with patch.object(adapter, "get_config", lambda: cfg or _cfg()), \
         patch.object(adapter, "re_prefect_client", return_value=client):
        return asyncio.run(adapter._run_prefect_step_api(
            "repo", "slug", "repo_secret_scan", {}, timeout_seconds=timeout,
            poll_interval=5.0, clock=clock, sleep=clock.sleep))


class TestFailFast:
    @pytest.mark.parametrize("workers", [[], [_worker("OFFLINE")]])
    def test_no_online_worker_creates_no_flow_run_and_names_the_cause(self, workers):
        client = _client(workers=workers, states=[_state("Scheduled")])
        clock = FakeClock()
        with pytest.raises(RuntimeError) as ei:
            _run_api(client, clock, timeout=100)
        assert "no online Prefect worker for pool resource-explorer-pool" in str(ei.value)
        assert "make prefect-up" in str(ei.value)
        client.create_flow_run_from_deployment.assert_not_called()
        assert clock.sleeps == []

    def test_an_online_worker_dispatches(self):
        client = _client(workers=[_worker("OFFLINE"), _worker("ONLINE")],
                         states=[_state("Completed")])
        out, run_id = _run_api(client, FakeClock(), timeout=100)
        assert out == {"findings": 3} and run_id == FLOW_RUN_ID
        client.create_flow_run_from_deployment.assert_awaited_once()

    def test_no_worker_falls_back_locally_under_the_existing_policy(self):
        """run_prefect_step's existing policy (any non-cancel error -> local,
        cause recorded in dispatch_info) is reused, not replaced."""
        client = _client(workers=[], states=[_state("Scheduled")])
        info: dict = {}
        with patch.object(adapter, "get_config", lambda: _cfg()), \
             patch.object(adapter, "re_prefect_client", return_value=client), \
             patch.object(adapter.run_surveyor_step_task, "fn", return_value={"via": "local"}):
            out = adapter.run_prefect_step("repo", "s", "repo_secret_scan", {}, dispatch_info=info)
        assert out == {"via": "local"}
        assert info["engine"] == "local"
        assert "no online Prefect worker for pool resource-explorer-pool" in info["dispatch_failed"]
        client.create_flow_run_from_deployment.assert_not_called()


class TestBoundedPoll:
    def test_never_leaving_scheduled_times_out_with_run_id_and_cancels(self):
        client = _client(workers=[_worker("ONLINE")], states=[_state("Scheduled")])
        clock = FakeClock()
        with pytest.raises(adapter.PrefectFlowRunTimeout) as ei:
            _run_api(client, clock, timeout=60)
        msg = str(ei.value)
        assert f"timed out after 60 s waiting for Prefect flow run {FLOW_RUN_ID} (state Scheduled)" in msg
        assert ei.value.flow_run_id == FLOW_RUN_ID
        client.set_flow_run_state.assert_awaited_once()
        assert "cancelled" in msg
        assert clock.now == 60.0  # fake clock only; no real sleeping

    def test_cancel_failure_is_reported_not_hidden(self):
        client = _client(workers=[_worker("ONLINE")], states=[_state("Scheduled")],
                         cancel_raises=ConnectionError("api gone"))
        with pytest.raises(adapter.PrefectFlowRunTimeout) as ei:
            _run_api(client, FakeClock(), timeout=10)
        assert "cancel attempt FAILED" in str(ei.value)
        assert "api gone" in str(ei.value)
        assert FLOW_RUN_ID in str(ei.value)

    def test_completes_before_deadline_behaves_as_before(self):
        client = _client(workers=[_worker("ONLINE")],
                         states=[_state("Scheduled"), _state("Running"), _state("Completed")])
        clock = FakeClock()
        out, run_id = _run_api(client, clock, timeout=60)
        assert out == {"findings": 3} and run_id == FLOW_RUN_ID
        client.set_flow_run_state.assert_not_called()
        assert clock.sleeps == [5.0, 5.0]

    def test_failed_and_cancelled_runs_unchanged(self):
        with pytest.raises(RuntimeError, match="failed"):
            _run_api(_client(workers=[_worker("ONLINE")], states=[_state("Failed")]), FakeClock(), 60)
        with pytest.raises(adapter.PrefectFlowRunCancelled):
            _run_api(_client(workers=[_worker("ONLINE")], states=[_state("Cancelled")]), FakeClock(), 60)

    def test_timeout_is_not_swallowed_into_a_local_rerun(self):
        client = _client(workers=[_worker("ONLINE")], states=[_state("Scheduled")])
        with patch.object(adapter, "get_config", lambda: _cfg(timeout=0.001)), \
             patch.object(adapter, "re_prefect_client", return_value=client), \
             patch.object(adapter, "_run_prefect_step_api",
                          AsyncMock(side_effect=adapter.PrefectFlowRunTimeout("t", FLOW_RUN_ID))), \
             patch.object(adapter.run_surveyor_step_task, "fn",
                          side_effect=AssertionError("must not re-run locally")):
            with pytest.raises(adapter.PrefectFlowRunTimeout):
                adapter.run_prefect_step("repo", "s", "repo_secret_scan", {})


class TestTimeoutSetting:
    def test_default_is_the_documented_twenty_minutes(self):
        assert config_module.DEFAULT_PREFECT_STEP_TIMEOUT_SECONDS == 1200.0
        assert config_module.PrefectConfig().step_timeout_seconds == 1200.0
        with patch.object(adapter, "get_config", lambda: _cfg()):
            assert adapter._step_timeout_seconds() == 1200.0

    def test_env_overrides(self, monkeypatch):
        monkeypatch.setenv("PREFECT_STEP_TIMEOUT_SECONDS", "42")
        assert config_module.PrefectConfig().step_timeout_seconds == 42.0

    def test_configured_value_is_honoured_by_the_poll(self):
        client = _client(workers=[_worker("ONLINE")], states=[_state("Scheduled")])
        clock = FakeClock()
        with pytest.raises(adapter.PrefectFlowRunTimeout, match="timed out after 30 s"):
            _run_api(client, clock, timeout=None, cfg=_cfg(timeout=30))
        assert clock.now == 30.0

    def test_nonpositive_is_rejected(self, monkeypatch):
        monkeypatch.setenv("PREFECT_STEP_TIMEOUT_SECONDS", "0")
        with pytest.raises(Exception):
            config_module.PrefectConfig()


class TestExecutorRecordsAnHonestError:
    def _survey(self, timeout_runner):
        local_runner = MagicMock(return_value={"ok": True})
        ad = ResourceTypeAdapter(
            entity_type="pf-timeout", technology_type="Fake Tech",
            re_analysis_steps={"local_step": local_runner, "pf_step": MagicMock()},
            get_entity=lambda registry, slug: MagicMock(slug=slug, display_name="", github_url=""),
            publish=MagicMock(return_value="report-guid"),
        )
        register_adapter(ad)
        sd = SurveyDefinition(
            process_guid="proc-pt", display_name="S", qualified_name="GovActionProcess::pt",
            supported_technology_type="Fake Tech",
            steps=[
                SurveyStep(guid="s1", display_name="Pf", qualified_name="Step::Pf",
                           executes_at="prefect", re_analysis_step="pf_step"),
                SurveyStep(guid="s2", display_name="Local", qualified_name="Step::Local",
                           executes_at="resource-explorer", re_analysis_step="local_step"),
            ],
        )
        registry = MagicMock()
        registry.get_survey_definition_guid.return_value = None
        registry.has_assigned_egeria_project.return_value = False
        reader = MagicMock()
        reader.fetch.return_value = sd
        reader.find_candidate_process_guids.return_value = [
            {"guid": "proc-pt", "qualified_name": "GovActionProcess::pt", "display_name": "S"}]
        ex = SurveyDefinitionExecutor(registry, reader=reader)
        fake_cfg = MagicMock()
        fake_cfg.prefect.enabled = True
        fake_cfg.prefect.route_local_steps = False
        with patch.object(sde_module, "_prefect_orchestration_enabled", return_value=False), \
             patch.object(config_module, "get_config", return_value=fake_cfg), \
             patch.object(adapter, "run_prefect_step", timeout_runner), \
             patch.object(SurveyDefinitionExecutor, "_record_cost") as rec:
            result = ex.run(entity_type="pf-timeout", slug="x")
        return result, local_runner, rec

    def test_timed_out_step_is_an_error_with_no_findings_and_survey_continues(self):
        boom = MagicMock(side_effect=adapter.PrefectFlowRunTimeout(
            f"timed out after 1200 s waiting for Prefect flow run {FLOW_RUN_ID} (state Scheduled)",
            flow_run_id=FLOW_RUN_ID))
        result, local_runner, rec = self._survey(boom)
        by = {s["step"]: s for s in result["steps"]}
        assert by["Step::Pf"]["status"] == "error"
        assert by["Step::Pf"]["flow_run_id"] == FLOW_RUN_ID
        assert FLOW_RUN_ID in by["Step::Pf"]["detail"]
        assert any(FLOW_RUN_ID in e for e in result["errors"])
        # the rest of the survey ran
        local_runner.assert_called_once()
        assert by["Step::Local"]["status"] == "ok"
        # no cost row / output was persisted for the timed-out step: only the
        # local step reached _record_cost
        assert rec.call_count == 1
        assert rec.call_args.args[3] == {"ok": True}

