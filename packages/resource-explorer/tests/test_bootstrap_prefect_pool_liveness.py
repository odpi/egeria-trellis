"""Prefect work-pool worker liveness in the bootstrap monitor (2026-09-28,
part of the whole-definition-Prefect-default change — see
PREFECT-DEFAULT-WHOLE-DEFINITION-IMPLEMENTED.md).

Real problem earlier tonight: `prefect_up.sh` started a worker on the wrong
work pool, and nobody noticed until a run silently degraded to local
execution. This adds a `resource-explorer-pool` worker-liveness check to the
same periodic monitor that already reports Dr.Egeria batch status
(`bootstrap.get_status()`), so a missing/offline worker is a monitored
condition surfaced BEFORE a confusing run failure, not discovered by one.

Same tri-state, fail-open convention as the batch canary checks in this
module: an unreachable Prefect API must report `reachable=False`, never a
false "0 workers" indistinguishable from a genuinely empty (but reachable)
pool.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from resource_explorer import bootstrap as bs


@pytest.fixture(autouse=True)
def _clear_pool_status():
    with bs._prefect_pool_status_lock:
        bs._prefect_pool_status = bs.PrefectPoolStatus()
    yield
    with bs._prefect_pool_status_lock:
        bs._prefect_pool_status = bs.PrefectPoolStatus()


def _fake_worker(name="worker-1", last_heartbeat="2026-09-28T00:00:00Z"):
    return SimpleNamespace(name=name, last_heartbeat_time=last_heartbeat)


class TestPoolWorkerLiveness:
    def test_reachable_pool_with_workers_reports_count_and_heartbeat(self):
        fake_client = MagicMock()
        fake_client.read_workers_for_work_pool = AsyncMock(
            return_value=[_fake_worker("w1", "2026-09-28T00:00:00Z"),
                          _fake_worker("w2", "2026-09-28T00:05:00Z")]
        )
        fake_client.__aenter__ = AsyncMock(return_value=fake_client)
        fake_client.__aexit__ = AsyncMock(return_value=False)

        with patch(
            "resource_explorer.surveyors.prefect_adapter.re_prefect_client",
            return_value=fake_client,
        ):
            status = bs.check_prefect_pool_workers(pool="resource-explorer-pool")

        assert status.reachable is True
        assert status.worker_count == 2
        assert status.last_heartbeat == "2026-09-28T00:05:00Z"
        assert status.last_check_error == ""

        # And it lands in the same status surface the admin banner reads
        # alongside the Dr.Egeria batch status.
        st = bs.get_status()
        assert st["prefect_pool"]["worker_count"] == 2
        assert st["prefect_pool"]["reachable"] is True

    def test_reachable_pool_with_zero_workers_is_reported_not_hidden(self):
        fake_client = MagicMock()
        fake_client.read_workers_for_work_pool = AsyncMock(return_value=[])
        fake_client.__aenter__ = AsyncMock(return_value=fake_client)
        fake_client.__aexit__ = AsyncMock(return_value=False)

        with patch(
            "resource_explorer.surveyors.prefect_adapter.re_prefect_client",
            return_value=fake_client,
        ):
            status = bs.check_prefect_pool_workers(pool="resource-explorer-pool")

        assert status.reachable is True
        assert status.worker_count == 0
        # Reachable-but-empty must not be conflated with unreachable — the
        # tri-state distinction the batch canary checks in this module make.
        assert status.last_check_error == ""

    def test_unreachable_prefect_api_reports_unreachable_not_zero_workers(self):
        with patch(
            "resource_explorer.surveyors.prefect_adapter.re_prefect_client",
            side_effect=ConnectionError("could not connect to Prefect API"),
        ):
            status = bs.check_prefect_pool_workers(pool="resource-explorer-pool")

        assert status.reachable is False
        assert status.last_check_error
        st = bs.get_status()
        assert st["prefect_pool"]["reachable"] is False
