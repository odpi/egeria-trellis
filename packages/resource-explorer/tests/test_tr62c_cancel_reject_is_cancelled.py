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


def _run_in(kind: str, started: bool = False):
    state = MagicMock()
    state.is_completed.return_value = kind == "COMPLETED"
    state.is_failed.return_value = False
    state.is_cancelled.return_value = kind == "CANCELLED"
    state.name = kind.title()
    state.type = types.SimpleNamespace(value=kind)
    return types.SimpleNamespace(state=state, start_time="2026-11-19T17:17:39Z" if started else None)


def _client(cancel_status: str, after: str, *, before: str = "SCHEDULED", reason: str = NO_INFRA,
            started: bool = False):
    """A fake Prefect client: the run reads `before` until the cancel is proposed, then `after`."""
    c = MagicMock()
    c.__aenter__ = AsyncMock(return_value=c)
    c.__aexit__ = AsyncMock(return_value=False)
    current = {"kind": before}

    async def set_state(**kw):
        current["kind"] = after
        return _set_state_result(cancel_status, reason)

    async def read(_id):
        return _run_in(current["kind"], started)

    c.set_flow_run_state = AsyncMock(side_effect=set_state)
    c.read_flow_run = read
    c.read_logs = AsyncMock(return_value=[])
    return c


class TestRequestCancel:
    @pytest.mark.parametrize("status", ["REJECT", "ABORT"])
    def test_a_rejected_proposal_on_a_run_that_never_started_is_cancelled_before_it_started(self, status):
        out = asyncio.run(adapter.request_cancel(_client(status, "CANCELLED"), RUN_ID))
        assert out == adapter.CancelOutcome(refused="", cancelled=True, before_start=True)

    @pytest.mark.parametrize("started", [False, True])
    def test_a_refusal_over_cancelling_is_a_cancel_under_way_never_before_it_started(self, started):
        """R1 (a): e.g. a second Admin Cancel on a running run; the worker is still stopping it."""
        out = asyncio.run(adapter.request_cancel(_client("REJECT", "CANCELLING", started=started), RUN_ID))
        assert out == adapter.CancelOutcome()

    def test_a_refusal_over_a_started_run_already_cancelled_is_cancelled_not_before_it_started(self):
        """R1 (b): an earlier cancel won on a run that HAD started."""
        out = asyncio.run(adapter.request_cancel(_client("REJECT", "CANCELLED", before="RUNNING", started=True),
                                                 RUN_ID))
        assert out == adapter.CancelOutcome(refused="", cancelled=True, before_start=False)

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

    def test_a_started_run_already_cancelled_does_not_say_before_it_started(self):
        msg = _time_out(_client("REJECT", "CANCELLED", before="RUNNING", started=True))
        assert msg.endswith("; the run was cancelled") and "before it started" not in msg

    def test_a_started_run_already_cancelling_does_not_say_before_it_started(self):
        msg = _time_out(_client("REJECT", "CANCELLING", before="RUNNING", started=True))
        assert msg.endswith("; the run was cancelled") and "before it started" not in msg

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

    def test_a_second_cancel_on_a_running_run_is_200_cancelling_not_cancelled(self):
        """R1 (a): Prefect refuses the second CANCELLING; the run is still being stopped."""
        r = self._post(_client("REJECT", "CANCELLING", before="CANCELLING", started=True,
                               reason="already cancelling"))
        assert r.status_code == 200 and r.json() == {"status": "cancelling", "id": RUN_ID}

    def test_a_started_run_an_earlier_cancel_finished_is_200_cancelled(self):
        r = self._post(_client("REJECT", "CANCELLED", before="CANCELLED", started=True))
        assert r.status_code == 200 and r.json()["status"] == "cancelled"

    def test_a_running_run_is_200_cancelling(self):
        r = self._post(_client("ACCEPT", "CANCELLING", before="RUNNING"))
        assert r.status_code == 200 and r.json()["status"] == "cancelling"
