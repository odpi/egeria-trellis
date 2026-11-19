"""TR-62c: a cancel Prefect REJECTs because the run has no infrastructure yet is a cancel that worked.

laz 2026-11-19, flow run 90cf24d0: a run still SCHEDULED hit RE's deadline. Prefect rejected the CANCELLING proposal
("Scheduled flow run has no infrastructure to terminate.") and set CANCELLED itself, but RE reported "cancel attempt
FAILED ... the run may still be live", and the Admin route answered 502 on a run it had cancelled. Fakes only.
"""
from __future__ import annotations

import asyncio
import types
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from resource_explorer.surveyors import prefect_adapter as adapter
from resource_explorer.web.app import app

RUN_ID = "11111111-2222-3333-4444-555555555555"
NO_INFRA = "Scheduled flow run has no infrastructure to terminate."


def _set_state_result(status: str, reason: str = ""):
    return types.SimpleNamespace(status=types.SimpleNamespace(value=status),
                                 details=types.SimpleNamespace(reason=reason))


def _run_in(kind: str):
    state = MagicMock()
    state.is_completed.return_value = kind == "COMPLETED"
    state.is_failed.return_value = False
    state.is_cancelled.return_value = kind == "CANCELLED"
    state.name = kind.title()
    state.type = types.SimpleNamespace(value=kind)
    return types.SimpleNamespace(state=state)


def _client(cancel_status: str, after: str, *, before: str = "SCHEDULED", reason: str = NO_INFRA):
    """A fake Prefect client: the run reads `before` until the cancel is proposed, then `after`."""
    c = MagicMock()
    c.__aenter__ = AsyncMock(return_value=c)
    c.__aexit__ = AsyncMock(return_value=False)
    current = {"kind": before}

    async def set_state(**kw):
        current["kind"] = after
        return _set_state_result(cancel_status, reason)

    async def read(_id):
        return _run_in(current["kind"])

    c.set_flow_run_state = AsyncMock(side_effect=set_state)
    c.read_flow_run = read
    c.read_logs = AsyncMock(return_value=[])
    return c


class TestRequestCancel:
    @pytest.mark.parametrize("status", ["REJECT", "ABORT"])
    @pytest.mark.parametrize("after", ["CANCELLED", "CANCELLING"])
    def test_a_rejected_proposal_on_a_run_prefect_cancelled_is_success(self, status, after):
        out = asyncio.run(adapter.request_cancel(_client(status, after), RUN_ID))
        assert out == adapter.CancelOutcome(refused="", before_start=True)

    def test_an_abort_on_a_completed_run_stays_a_refusal(self):
        out = asyncio.run(adapter.request_cancel(
            _client("ABORT", "COMPLETED", before="COMPLETED", reason="run is already COMPLETED"), RUN_ID))
        assert out.refused == "Prefect refused the cancel (abort: run is already COMPLETED)"
        assert not out.before_start

    def test_an_unreadable_state_after_a_reject_stays_a_refusal(self):
        c = _client("REJECT", "CANCELLED")

        async def boom(_id):
            raise ConnectionError("gone")

        c.read_flow_run = boom
        out = asyncio.run(adapter.request_cancel(c, RUN_ID))
        assert out.refused.startswith("Prefect refused the cancel (reject: ") and not out.before_start
        assert out.refused.endswith("; its state could not be read afterwards (ConnectionError)")

    def test_an_accepted_cancelling_is_not_re_read(self):
        c = _client("ACCEPT", "CANCELLING", before="RUNNING")
        c.read_flow_run = AsyncMock(side_effect=AssertionError("no re-read on ACCEPT"))
        assert asyncio.run(adapter.request_cancel(c, RUN_ID)) == adapter.CancelOutcome()


def _time_out(client):
    ticks = iter([0.0, 10.0])

    async def no_sleep(_):
        return None

    with pytest.raises(adapter.PrefectFlowRunTimeout) as ei:
        asyncio.run(adapter._await_flow_run(client, RUN_ID, RUN_ID, timeout_seconds=10, poll_interval=1,
                                            clock=lambda: next(ticks), sleep=no_sleep))
    return str(ei.value)


class TestDeadlinePath:
    def test_a_scheduled_run_cancelled_by_prefect_reads_cancelled_before_it_started(self):
        msg = _time_out(_client("REJECT", "CANCELLED"))
        assert f"timed out after 10 s waiting for Prefect flow run {RUN_ID} (state Scheduled)" in msg
        assert msg.endswith("; the run was cancelled before it started")
        assert "FAILED" not in msg and "may still be live" not in msg

    def test_a_refusal_on_a_finished_run_keeps_the_failure_words(self):
        msg = _time_out(_client("ABORT", "COMPLETED", before="RUNNING", reason="run is already COMPLETED"))
        assert "; cancel attempt FAILED (Prefect refused the cancel (abort: run is already COMPLETED))" in msg
        assert msg.endswith("- the run may still be live")

    def test_a_running_run_accepted_into_cancelling_reads_as_before(self):
        msg = _time_out(_client("ACCEPT", "CANCELLING", before="RUNNING"))
        assert msg.endswith("; the run was cancelled")


class TestAdminRoute:
    @staticmethod
    def _post(client):
        with patch("resource_explorer.web.routes.prefect_status.re_prefect_client", return_value=client):
            return TestClient(app).post(f"/api/prefect/flow-runs/{RUN_ID}/cancel")

    def test_a_queued_run_prefect_cancelled_is_200_cancelled(self):
        r = self._post(_client("REJECT", "CANCELLED"))
        assert r.status_code == 200
        assert r.json() == {"status": "cancelled", "id": RUN_ID}

    def test_a_refusal_on_a_completed_run_is_still_a_502(self):
        r = self._post(_client("ABORT", "COMPLETED", before="COMPLETED", reason="run is already COMPLETED"))
        assert r.status_code == 502
        assert "run is already COMPLETED" in r.json()["detail"]

    def test_a_running_run_is_200_cancelling(self):
        r = self._post(_client("ACCEPT", "CANCELLING", before="RUNNING"))
        assert r.status_code == 200 and r.json()["status"] == "cancelling"
