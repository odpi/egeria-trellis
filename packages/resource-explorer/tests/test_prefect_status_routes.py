"""Tests for the /api/prefect routes (web/routes/prefect_status.py).

Live end-to-end verification (real server, real worker, real cancel) was
done manually 2026-08-26 against a local `prefect server` + deployed
`re_survey_flow` + worker — see docs/re-ea-consolidation-audit.md and the
Prefect entry in docs/Backlog.md for what was confirmed live. These tests
cover the route layer in isolation (mocked Prefect client) so they run in CI
without a live server, and specifically guard the graceful-degradation
behavior — "Prefect isn't reachable" must render as a status flag, not a 500.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from fastapi.testclient import TestClient

from resource_explorer.web.app import app

client = TestClient(app)


class TestStatus:
    def test_reports_unreachable_without_raising(self):
        with patch("resource_explorer.web.routes.prefect_status.re_prefect_client",
                    side_effect=ConnectionError("no server")):
            r = client.get("/api/prefect/status")
        assert r.status_code == 200
        body = r.json()
        assert body["reachable"] is False
        assert "no server" in body["error"]

    def test_reports_reachable_when_healthcheck_succeeds(self):
        mock_client = AsyncMock()
        mock_client.api_healthcheck = AsyncMock(return_value=None)
        mock_client.__aenter__.return_value = mock_client
        mock_client.__aexit__.return_value = None
        with patch("resource_explorer.web.routes.prefect_status.re_prefect_client", return_value=mock_client):
            r = client.get("/api/prefect/status")
        assert r.status_code == 200
        assert r.json()["reachable"] is True

    def test_reports_unreachable_when_healthcheck_returns_its_failure(self):
        """The real client's shape when no server is listening: entering the client
        succeeds and api_healthcheck() RETURNS the ConnectError (it never raises)."""
        mock_client = AsyncMock()
        mock_client.api_healthcheck = AsyncMock(return_value=httpx.ConnectError("All connection attempts failed"))
        mock_client.__aenter__.return_value = mock_client
        mock_client.__aexit__.return_value = None
        with patch("resource_explorer.web.routes.prefect_status.re_prefect_client", return_value=mock_client):
            r = client.get("/api/prefect/status")
        assert r.status_code == 200
        body = r.json()
        assert body["reachable"] is False
        assert "All connection attempts failed" in body["error"]


class TestReachabilityCheck:
    """prefect_adapter._check_prefect_reachable_async: the startup log line and the
    dispatch gates (check_prefect_reachable_sync) both read it."""

    @staticmethod
    def _client_whose_healthcheck_returns(value):
        mock_client = AsyncMock()
        mock_client.api_healthcheck = AsyncMock(return_value=value)
        mock_client.__aenter__.return_value = mock_client
        mock_client.__aexit__.return_value = None
        return mock_client

    def test_a_returned_failure_is_unreachable(self):
        import asyncio

        from resource_explorer.surveyors import prefect_adapter
        mock_client = self._client_whose_healthcheck_returns(httpx.ConnectError("All connection attempts failed"))
        with patch.object(prefect_adapter, "re_prefect_client", return_value=mock_client):
            ok, detail = asyncio.run(prefect_adapter._check_prefect_reachable_async())
        assert ok is False
        assert "All connection attempts failed" in detail

    def test_a_failure_with_empty_text_still_says_what_it_was(self):
        import asyncio

        from resource_explorer.surveyors import prefect_adapter
        mock_client = self._client_whose_healthcheck_returns(httpx.ConnectError(""))
        with patch.object(prefect_adapter, "re_prefect_client", return_value=mock_client):
            ok, detail = asyncio.run(prefect_adapter._check_prefect_reachable_async())
        assert (ok, detail) == (False, "ConnectError")

    def test_none_is_reachable(self):
        import asyncio

        from resource_explorer.surveyors import prefect_adapter
        mock_client = self._client_whose_healthcheck_returns(None)
        with patch.object(prefect_adapter, "re_prefect_client", return_value=mock_client):
            ok, _ = asyncio.run(prefect_adapter._check_prefect_reachable_async())
        assert ok is True

    def test_against_a_port_nothing_listens_on(self, monkeypatch):
        """No mock: the real PrefectClient against a closed port must read as unreachable."""
        import asyncio
        import socket

        from resource_explorer.surveyors import prefect_adapter
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]       # bound then closed: nothing is listening on it
        cfg = MagicMock()
        cfg.prefect.api_url = f"http://127.0.0.1:{port}/api"
        monkeypatch.setattr(prefect_adapter, "get_config", lambda: cfg)
        ok, detail = asyncio.run(prefect_adapter._check_prefect_reachable_async())
        assert ok is False, detail


class TestFlowRuns:
    def test_unreachable_server_returns_empty_list_not_500(self):
        with patch("resource_explorer.web.routes.prefect_status.re_prefect_client",
                    side_effect=ConnectionError("no server")):
            r = client.get("/api/prefect/flow-runs")
        assert r.status_code == 200
        body = r.json()
        assert body["reachable"] is False
        assert body["flow_runs"] == []

    def test_groups_flow_runs_by_slug_tag(self):
        fake_run = MagicMock()
        fake_run.id = "11111111-1111-1111-1111-111111111111"
        fake_run.name = "test-run"
        fake_run.tags = ["entity_type:repo", "slug:kafka", "step:repo_health"]
        fake_run.state.type.value = "COMPLETED"
        fake_run.state.message = None
        fake_run.created = None
        fake_run.start_time = None
        fake_run.end_time = None
        fake_run.deployment_id = None

        mock_client = AsyncMock()
        mock_client.read_flow_runs = AsyncMock(return_value=[fake_run])
        mock_client.__aenter__.return_value = mock_client
        mock_client.__aexit__.return_value = None
        with patch("resource_explorer.web.routes.prefect_status.re_prefect_client", return_value=mock_client):
            r = client.get("/api/prefect/flow-runs")

        assert r.status_code == 200
        body = r.json()
        assert body["reachable"] is True
        assert body["flow_runs"][0]["slug"] == "kafka"
        assert body["flow_runs"][0]["entity_type"] == "repo"
        assert body["flow_runs"][0]["step"] == "repo_health"


class TestCancel:
    def test_unreachable_server_returns_502_not_500(self):
        with patch("resource_explorer.web.routes.prefect_status.re_prefect_client",
                    side_effect=ConnectionError("no server")):
            r = client.post("/api/prefect/flow-runs/11111111-1111-1111-1111-111111111111/cancel")
        assert r.status_code == 502

    def test_calls_set_flow_run_state_with_cancelled(self):
        mock_client = AsyncMock()
        mock_client.set_flow_run_state = AsyncMock(return_value=None)
        mock_client.__aenter__.return_value = mock_client
        mock_client.__aexit__.return_value = None
        with patch("resource_explorer.web.routes.prefect_status.re_prefect_client", return_value=mock_client):
            r = client.post("/api/prefect/flow-runs/11111111-1111-1111-1111-111111111111/cancel")

        assert r.status_code == 200
        assert r.json()["status"] == "cancelling"
        mock_client.set_flow_run_state.assert_called_once()
        _, kwargs = mock_client.set_flow_run_state.call_args
        assert kwargs["force"] is True
