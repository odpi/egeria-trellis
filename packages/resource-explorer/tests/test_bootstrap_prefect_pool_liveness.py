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

import logging
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from resource_explorer import bootstrap as bs


@pytest.fixture(autouse=True)
def _own_sha(monkeypatch):
    """This RE runs the commit ON_SHA's worker advertises, so a live ON_SHA worker is one a step goes to."""
    from resource_explorer import build_info
    monkeypatch.setenv("TRELLIS_RE_SHA", "0123456789ab" + "cdef0123456789abcdef01234567")
    build_info.re_sha.cache_clear()
    yield
    build_info.re_sha.cache_clear()


@pytest.fixture(autouse=True)
def _clear_pool_status():
    with bs._prefect_pool_status_lock:
        bs._prefect_pool_status = bs.PrefectPoolStatus()
    bs._prefect_pool_logged_state = None
    yield
    with bs._prefect_pool_status_lock:
        bs._prefect_pool_status = bs.PrefectPoolStatus()
    bs._prefect_pool_logged_state = None


def _fake_worker(name="worker-1", last_heartbeat="2026-09-28T00:00:00Z", **extra):
    return SimpleNamespace(name=name, last_heartbeat_time=last_heartbeat, **extra)


#: The deployments the fake Prefect knows, by name, and their pool (resolve_dispatch reads them).
DEPLOYMENTS = {"RE Survey Flow/re-survey-step-deployment": "resource-explorer-pool",
               "RE Survey Flow/re-survey-step-0123456789ab": "resource-explorer-pool"}


def _client(workers, deployments=None):
    fake_client = MagicMock()
    fake_client.read_workers_for_work_pool = AsyncMock(return_value=workers)
    known = DEPLOYMENTS if deployments is None else deployments

    async def read_deployment(name):
        if name not in known:
            raise LookupError(f"{name} not found")
        return SimpleNamespace(id=name, name=name.split("/", 1)[-1], work_pool_name=known[name])

    fake_client.read_deployment_by_name = AsyncMock(side_effect=read_deployment)
    fake_client.__aenter__ = AsyncMock(return_value=fake_client)
    fake_client.__aexit__ = AsyncMock(return_value=False)
    return fake_client


def _check(workers, now, deployments=None):
    with patch("resource_explorer.surveyors.prefect_adapter.re_prefect_client",
               return_value=_client(workers, deployments)):
        return bs.check_prefect_pool_workers(pool="resource-explorer-pool", now=now)


#: The laz state found 2026-11-13: three registrations, the newest heartbeat a month old.
NOW = datetime(2026, 11, 13, 12, 17, tzinfo=timezone.utc)
STALE = (NOW - timedelta(days=32)).strftime("%Y-%m-%dT%H:%M:%SZ")
FRESH = (NOW - timedelta(seconds=20)).strftime("%Y-%m-%dT%H:%M:%SZ")
#: A worker that advertises its commit (the container worker's, and the host worker's once it does).
ON_SHA = "re-worker-0123456789ab-pop-os"


class TestPoolWorkerLiveness:
    def test_reachable_pool_with_workers_reports_live_registered_and_heartbeat(self):
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
            status = bs.check_prefect_pool_workers(
                pool="resource-explorer-pool",
                now=datetime(2026, 9, 28, 0, 5, 30, tzinfo=timezone.utc))

        assert status.reachable is True
        # w1 has been silent 5.5 minutes (> 3 x 30 s): registered, not live.
        assert status.live_count == 1 and status.registered_count == 2
        assert status.worker_count == 1, "the deprecated alias is the live count"
        assert status.last_heartbeat == "2026-09-28T00:05:00Z"
        assert status.last_check_error == ""

        # And it lands in the same status surface the admin banner reads
        # alongside the Dr.Egeria batch status.
        st = bs.get_status()
        assert st["prefect_pool"]["worker_count"] == 1
        assert st["prefect_pool"]["live_count"] == 1
        assert st["prefect_pool"]["registered_count"] == 2
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
        assert status.worker_count == 0 and status.registered_count == 0
        assert status.summary == "no registered worker"
        assert status.runs_locally_instead, "an empty pool is no live worker too"
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
        for key in ("live_count", "registered_count", "worker_count", "never_heard_from"):
            assert st["prefect_pool"][key] is None, f"{key}: an unreachable pool is not an empty one"
        # Not silence either (Architect ruling 2026-11-13): an unreachable server is a warning of its own,
        # worded apart from "no live worker".
        assert st["prefect_pool"]["summary"] == "server not reachable; the pool could not be checked"
        assert "no live worker" not in st["prefect_pool"]["summary"]
        assert st["prefect_pool"]["runs_locally_words"].startswith(
            "Runs are not recorded in Prefect; dispatched steps (Soda data-quality, ")
        assert st["prefect_pool"]["runs_locally_words"].endswith(
            "run in Resource Explorer's process instead.")


class TestLiveNotRegistered:
    """2026-11-13: laz's pool listed 3 workers whose last heartbeat was a month old, and the
    check reported "has 3 worker(s)". Live is what it counts now."""

    def test_three_registered_a_month_silent_is_no_live_worker(self, caplog):
        caplog.set_level(logging.INFO, logger=bs.log.name)
        status = _check([_fake_worker(f"w{i}", STALE) for i in range(3)], NOW)
        assert (status.live_count, status.registered_count, status.worker_count) == (0, 3, 0)
        assert status.summary == f"no live worker: 3 registered, last heartbeat 32 days ago ({STALE})"
        assert status.runs_locally_instead
        warns = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert len(warns) == 1 and "no live worker" in warns[0].getMessage()
        assert "run in Resource Explorer's process instead" in warns[0].getMessage()
        payload = bs.get_status()["prefect_pool"]
        assert payload["live_count"] == 0 and payload["registered_count"] == 3
        assert payload["summary"] == status.summary
        assert payload["runs_locally_words"].startswith("Prefect-dispatched steps (Soda data-quality, ")
        assert payload["runs_locally_words"].endswith(
            "run in Resource Explorer's process instead: no Prefect run history, retries or cancel.")

    def test_one_of_three_in_the_window_is_live(self, caplog):
        caplog.set_level(logging.INFO, logger=bs.log.name)
        status = _check([_fake_worker("a", STALE), _fake_worker(ON_SHA, FRESH), _fake_worker("c", STALE)], NOW)
        assert (status.live_count, status.registered_count) == (1, 3)
        assert status.summary == "1 live worker(s) of 3 registered; last heartbeat 20 seconds ago"
        assert status.parity_unchecked == []
        assert status.runs_locally_instead == []
        assert [r.levelno for r in caplog.records if r.levelno >= logging.INFO] == [logging.INFO]

    def test_prefects_own_status_wins_over_the_heartbeat(self):
        assert _check([_fake_worker("a", STALE, status="ONLINE")], NOW).live_count == 1
        assert _check([_fake_worker("a", FRESH, status=SimpleNamespace(value="OFFLINE"))], NOW).live_count == 0

    def test_the_window_is_factor_times_the_workers_own_interval(self, monkeypatch):
        beat = (NOW - timedelta(seconds=250)).strftime("%Y-%m-%dT%H:%M:%SZ")
        assert _check([_fake_worker("a", beat)], NOW).live_count == 0, "3 x 30 s"
        assert _check([_fake_worker("a", beat, heartbeat_interval_seconds=100)], NOW).live_count == 1
        from resource_explorer.config import PrefectConfig, get_config
        monkeypatch.setenv("PREFECT_WORKER_LIVE_FACTOR", "10")
        assert PrefectConfig().worker_live_factor == 10, "PREFECT_WORKER_LIVE_FACTOR sets it"
        monkeypatch.setattr(get_config().prefect, "worker_live_factor", 10.0)
        assert _check([_fake_worker("a", beat)], NOW).live_count == 1

    def test_no_heartbeat_at_all_is_not_live_and_is_counted(self):
        status = _check([_fake_worker("a", None), _fake_worker("b", STALE)], NOW)
        assert (status.live_count, status.registered_count, status.never_heard_from) == (0, 2, 1)
        assert status.summary.endswith("; 1 never heard from")
        only = _check([_fake_worker("a", None)], NOW)
        assert only.summary == "no live worker: 1 registered, last heartbeat never; 1 never heard from"

    def test_a_state_is_logged_once_and_recovery_once(self, caplog):
        caplog.set_level(logging.INFO, logger=bs.log.name)
        stale = [_fake_worker(f"w{i}", STALE) for i in range(3)]
        _check(stale, NOW)
        _check(stale, NOW + timedelta(minutes=10))
        assert len([r for r in caplog.records if r.levelno == logging.WARNING]) == 1
        caplog.clear()
        _check([_fake_worker(ON_SHA, FRESH)], NOW)
        _check([_fake_worker(ON_SHA, FRESH)], NOW)
        said = [r for r in caplog.records if r.levelno >= logging.INFO]
        assert [r.levelno for r in said] == [logging.INFO] and "1 live worker(s)" in said[0].getMessage()
        caplog.clear()
        _check(stale, NOW)
        assert len([r for r in caplog.records if r.levelno == logging.WARNING]) == 1, "lost again: said again"

    def test_ages_use_one_wording(self):
        assert bs._age_words(45) == "45 seconds"
        assert bs._age_words(90 * 60) == "90 minutes"
        assert bs._age_words(47 * 3600) == "47 hours"
        assert bs._age_words(2 * 86400) == "2 days"
        assert bs._age_words(86400 + 1) == "24 hours"


class TestRunsLocallyInstead:
    """With no live worker nothing waits: per-step dispatch finds no ONLINE worker and runs the step
    locally (test_prefect_poll_timeout::test_no_worker_falls_back_locally_under_the_existing_policy);
    whole-definition runs are sequenced in-process and never use the pool."""

    def test_built_from_prefect_only_steps(self, monkeypatch):
        from resource_explorer.surveyors import survey_definition_executor as sde
        monkeypatch.setattr(sde, "PREFECT_ONLY_STEPS", sde.PREFECT_ONLY_STEPS + ("a_new_prefect_step",))
        steps = bs.prefect_pool_runs_locally_instead()
        assert steps[:2] == ["Soda data-quality", "Great Expectations validation"]
        assert "a_new_prefect_step" in steps, "a step added to PREFECT_ONLY_STEPS must be reported"
        assert "any step marked executes_at=prefect" in steps

    def test_res_own_steps_only_when_route_local_steps_is_on(self, monkeypatch):
        from resource_explorer.config import get_config
        assert not any("own steps" in s for s in bs.prefect_pool_runs_locally_instead())
        monkeypatch.setattr(get_config().prefect, "route_local_steps", True)
        assert "Resource Explorer's own steps (PREFECT_ROUTE_LOCAL_STEPS is on)" in \
            bs.prefect_pool_runs_locally_instead()

    def test_the_check_is_read_only(self):
        client = _client([_fake_worker("w0", STALE)])
        with patch("resource_explorer.surveyors.prefect_adapter.re_prefect_client", return_value=client):
            bs.check_prefect_pool_workers(pool="resource-explorer-pool", now=NOW)
        called = {c[0] for c in client.method_calls} - {"__aenter__", "__aexit__"}
        assert called <= {"read_workers_for_work_pool", "read_deployment_by_name"}, called


class TestServerUnreachable:
    def _down(self):
        with patch("resource_explorer.surveyors.prefect_adapter.re_prefect_client",
                   side_effect=ConnectionError("connection refused")):
            return bs.check_prefect_pool_workers(pool="resource-explorer-pool", now=NOW)

    def test_said_once_per_state_change(self, caplog):
        caplog.set_level(logging.INFO, logger=bs.log.name)
        self._down()
        self._down()
        warns = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert len(warns) == 1 and "server not reachable" in warns[0].getMessage()
        assert "connection refused" in warns[0].getMessage(), "the cause is in the one warning"
        caplog.clear()
        _check([_fake_worker(ON_SHA, FRESH)], NOW)
        said = [r for r in caplog.records if r.levelno >= logging.INFO]
        assert [r.levelno for r in said] == [logging.INFO], "back: said once, at INFO"

    def test_payload_says_whether_prefect_is_on(self, monkeypatch):
        from resource_explorer.config import get_config
        self._down()
        assert bs.get_status()["prefect_pool"]["enabled"] is get_config().prefect.enabled
        monkeypatch.setattr(get_config().prefect, "enabled", False)
        assert bs.get_status()["prefect_pool"]["enabled"] is False


class TestCommitParityUnchecked:
    """Architect ruling 2026-11-13: a live worker that advertises no commit can still be dispatched to, but it
    is a WARNING (once per change) and a banner line, never a silent all-clear."""

    def test_a_live_worker_with_no_commit_is_a_warning_said_once(self, caplog):
        caplog.set_level(logging.INFO, logger=bs.log.name)
        status = _check([_fake_worker("re-host-worker-laz", FRESH)], NOW)
        assert status.live_count == 1 and status.parity_unchecked == ["re-host-worker-laz"]
        assert status.summary == ("1 live worker(s) of 1 registered; last heartbeat 20 seconds ago; commit parity "
                                  "not checked: worker re-host-worker-laz advertises no commit, so a step "
                                  "dispatched to it may run on other code than this Resource Explorer's")
        _check([_fake_worker("re-host-worker-laz", FRESH)], NOW)
        warns = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert len(warns) == 1 and "commit parity not checked" in warns[0].getMessage()
        assert bs.get_status()["prefect_pool"]["parity_unchecked"] == ["re-host-worker-laz"]
        assert status.runs_locally_instead == [], "a live worker: nothing runs locally instead"

    def test_advertising_a_commit_clears_it_once_at_info(self, caplog):
        caplog.set_level(logging.INFO, logger=bs.log.name)
        _check([_fake_worker("re-host-worker-laz", FRESH)], NOW)
        caplog.clear()
        status = _check([_fake_worker(ON_SHA, FRESH)], NOW)
        assert status.parity_unchecked == []
        assert [r.levelno for r in caplog.records if r.levelno >= logging.INFO] == [logging.INFO]

    def test_a_stale_worker_with_no_commit_is_not_counted(self):
        status = _check([_fake_worker("re-host-worker-laz", STALE), _fake_worker(ON_SHA, FRESH)], NOW)
        assert status.parity_unchecked == []


class TestPoolMissing:
    """Read-back of 377c3353: a pool the server says does not exist (404) is its own state, not "server not
    reachable"; the fix is to create the pool, not to start the server."""

    @staticmethod
    def _missing():
        import httpx

        request = httpx.Request("POST", "http://prefect/api/work_pools/resource-explorer-pool/workers/filter")
        response = httpx.Response(404, request=request)
        err = httpx.HTTPStatusError("Client error '404 Not Found'", request=request, response=response)
        client = MagicMock()
        client.read_workers_for_work_pool = AsyncMock(side_effect=err)
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=False)
        with patch("resource_explorer.surveyors.prefect_adapter.re_prefect_client", return_value=client):
            return bs.check_prefect_pool_workers(pool="resource-explorer-pool", now=NOW)

    def test_a_missing_pool_says_so_and_is_not_unreachable(self, caplog):
        caplog.set_level(logging.INFO, logger=bs.log.name)
        status = self._missing()
        assert status.reachable is True and status.pool_exists is False
        assert status.summary == "pool resource-explorer-pool doesn't exist"
        assert status.runs_locally_instead
        self._missing()
        warns = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert len(warns) == 1 and "doesn't exist" in warns[0].getMessage()
        assert "not reachable" not in warns[0].getMessage()
        payload = bs.get_status()["prefect_pool"]
        assert payload["pool_exists"] is False and payload["reachable"] is True
        assert payload["runs_locally_words"].startswith("Prefect-dispatched steps (")

    def test_pool_exists_is_true_once_read_and_unknown_when_unreachable(self):
        _check([_fake_worker("w", FRESH)], NOW)
        assert bs.get_status()["prefect_pool"]["pool_exists"] is True
        with patch("resource_explorer.surveyors.prefect_adapter.re_prefect_client",
                   side_effect=ConnectionError("refused")):
            bs.check_prefect_pool_workers(pool="resource-explorer-pool", now=NOW)
        assert bs.get_status()["prefect_pool"]["pool_exists"] is None

    def test_prefects_own_not_found_counts_too(self):
        class ObjectNotFound(Exception):
            pass
        assert bs._is_not_found(ObjectNotFound("x"))
        assert not bs._is_not_found(ConnectionError("refused"))


#: hedwig's state: the default deployment left on the pool of the stopped 11-09 host worker.
OTHER_POOL = {"RE Survey Flow/re-survey-step-deployment": "default-agent-pool"}


class TestDispatchRefusedWhileAWorkerIsLive:
    """Architect ruling 2026-11-14: the banner uses the same decision dispatch makes (resolve_dispatch on the
    configured pool). hedwig 2026-11-14: "1 live worker(s) of 1 registered" with no warning, while every
    dispatched step ran locally because the default deployment was on another pool."""

    def test_a_worker_with_no_commit_and_the_default_deployment_on_another_pool(self, caplog):
        caplog.set_level(logging.INFO, logger=bs.log.name)
        status = _check([_fake_worker("re-host-worker-hedwig", FRESH)], NOW, deployments=OTHER_POOL)
        assert status.live_count == 1 and status.dispatch_to == ""
        assert status.dispatch_refused.startswith(
            "default deployment 'RE Survey Flow/re-survey-step-deployment' is on pool default-agent-pool but "
            "Resource Explorer is configured for pool resource-explorer-pool")
        assert "but a dispatched step runs locally: default deployment" in status.summary
        assert status.runs_locally_instead, "what runs locally instead is listed, as for no live worker"
        _check([_fake_worker("re-host-worker-hedwig", FRESH)], NOW, deployments=OTHER_POOL)
        warns = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert len(warns) == 1 and "configured for pool resource-explorer-pool" in warns[0].getMessage()
        payload = bs.get_status()["prefect_pool"]
        assert payload["dispatch_refused"] == status.dispatch_refused and payload["dispatch_to"] == ""

    def test_a_worker_on_this_commit_goes_to_its_own_deployment(self):
        status = _check([_fake_worker(ON_SHA, FRESH)], NOW, deployments={
            **OTHER_POOL, "RE Survey Flow/re-survey-step-0123456789ab": "resource-explorer-pool"})
        assert status.dispatch_refused == "" and status.dispatch_to == "re-survey-step-0123456789ab"
        assert bs.get_status()["prefect_pool"]["dispatch_to"] == "re-survey-step-0123456789ab"

    def test_a_check_that_fails_is_said_not_cleared(self):
        client = _client([_fake_worker(ON_SHA, FRESH)])
        client.read_deployment_by_name = AsyncMock(side_effect=RuntimeError("boom"))
        with patch("resource_explorer.surveyors.prefect_adapter.re_prefect_client", return_value=client):
            status = bs.check_prefect_pool_workers(pool="resource-explorer-pool", now=NOW)
        assert "is missing: RuntimeError: boom" in status.dispatch_refused
