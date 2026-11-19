"""TR-62a: no dispatched step idles for 20 minutes without anyone knowing.

hedwig 2026-11-18: repo_file_inventory sat ~21 min idle on the worker with a GitHub socket in CLOSE-WAIT. Cause,
reproduced here against a fake GitHub: PyGitHub's default retry sleeps until the rate limit resets (3600 s asked for
a reset an hour away). Every GitHub server below is a local fake on 127.0.0.1; every Prefect client is a fake.
"""
from __future__ import annotations

import asyncio
import json
import socket
import threading
import time
import types
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from resource_explorer.config import get_config
from resource_explorer.github import bounded_retry
from resource_explorer.github.client import GitHubClient
from resource_explorer.prefect import flows
from resource_explorer.surveyors import prefect_adapter as adapter
from resource_explorer.surveyors import step_heartbeat

RUN_ID = "11111111-2222-3333-4444-555555555555"


# ── fake GitHub servers ─────────────────────────────────────────────────────────────────────────────────────────

class _Server:
    def __init__(self, handler):
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.srv.daemon_threads = True
        self.thread = threading.Thread(target=self.srv.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.srv.server_port}"

    def close(self):
        self.srv.shutdown()
        self.srv.server_close()


def _rate_limited(reset_epoch: int, hits: list, retry_after: str | None = None):
    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            hits.append(self.path)
            body = json.dumps({"message": "API rate limit exceeded"}).encode()
            self.send_response(403)
            self.send_header("X-RateLimit-Limit", "60")
            self.send_header("X-RateLimit-Remaining", "0")
            self.send_header("X-RateLimit-Reset", str(reset_epoch))
            if retry_after is not None:
                self.send_header("Retry-After", retry_after)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    return H


@pytest.fixture
def github_cfg(monkeypatch):
    cfg = get_config().github
    monkeypatch.setattr(cfg, "token", "")
    monkeypatch.setattr(cfg, "request_timeout_seconds", 1)
    monkeypatch.setattr(cfg, "rate_limit_max_wait_seconds", 60)
    monkeypatch.setattr(cfg, "clone_timeout_seconds", 300)
    return cfg


@pytest.fixture
def no_real_sleep(monkeypatch):
    slept: list[float] = []
    monkeypatch.setattr(bounded_retry.time, "sleep", lambda s: slept.append(s))
    return slept


class TestRateLimitFailsNow:
    def test_a_reset_an_hour_away_fails_within_the_bound_and_names_the_reset(self, github_cfg, no_real_sleep):
        reset = int(time.time()) + 3600
        hits: list = []
        srv = _Server(_rate_limited(reset, hits))
        try:
            t0 = time.monotonic()
            with pytest.raises(bounded_retry.GitHubRateLimitWait) as ei:
                GitHubClient(base_url=srv.base).get_repo("https://github.com/odpi/egeria")
            took = time.monotonic() - t0
        finally:
            srv.close()
        hhmm = datetime.fromtimestamp(reset, timezone.utc).strftime("%H:%M")
        assert f"GitHub rate limit reached · resets at {hhmm} UTC" in str(ei.value)
        assert "GITHUB_TOKEN set: no" in str(ei.value)
        assert no_real_sleep == []          # it never slept: the 3600 s wait was refused, not taken
        assert len(hits) == 1 and took < 10

    def test_the_token_is_named_as_set_never_shown(self, github_cfg, no_real_sleep, monkeypatch):
        monkeypatch.setattr(github_cfg, "token", "ghp_SECRETVALUE123")
        srv = _Server(_rate_limited(int(time.time()) + 3600, []))
        try:
            with pytest.raises(bounded_retry.GitHubRateLimitWait) as ei:
                GitHubClient(base_url=srv.base).get_repo("https://github.com/odpi/egeria")
        finally:
            srv.close()
        assert "GITHUB_TOKEN set: yes" in str(ei.value)
        assert "SECRETVALUE" not in str(ei.value) and "ghp_" not in str(ei.value)

    def test_a_short_wait_is_taken_and_named_on_the_heartbeat(self, github_cfg, monkeypatch):
        phases: list[str] = []
        hb = step_heartbeat.Heartbeat(RUN_ID, client_factory=MagicMock())
        token = step_heartbeat._CURRENT.set(hb)
        monkeypatch.setattr(bounded_retry.time, "sleep", lambda s: phases.append((hb.phase_word, s)))
        retry = bounded_retry.BoundedGithubRetry(max_wait=60)
        resp = types.SimpleNamespace(headers={"Retry-After": "2"}, status=403)
        monkeypatch.setattr(retry, "get_retry_after", lambda r: 2.0)
        try:
            retry.sleep(resp)
        finally:
            step_heartbeat._CURRENT.reset(token)
        assert phases == [("waiting for GitHub rate limit", 2.0)]
        assert hb.phase_word == "running"   # restored after the wait

    def test_the_bound_survives_urllib3_making_a_new_retry(self):
        retry = bounded_retry.BoundedGithubRetry(max_wait=7, token_set=True)
        nxt = retry.new(total=1)
        assert (nxt.max_wait, nxt.token_set) == (7.0, True)
        assert retry.total == bounded_retry.TOTAL_ATTEMPTS

    def test_every_github_on_the_step_path_is_bounded_and_the_contributor_reads_stay_unretried(self, github_cfg):
        from resource_explorer.github import doc_locations

        c = GitHubClient(base_url="http://127.0.0.1:9")
        for gh in (c._gh, doc_locations._make_client()):
            req = gh.requester
            assert isinstance(req._Requester__retry, bounded_retry.BoundedGithubRetry)
            assert req._Requester__timeout == 1
        assert c._gh_once.requester._Requester__retry is None and c._gh_once.requester._Requester__timeout == 1


class TestStalledConnections:
    @staticmethod
    def _silent_server():
        """Accepts connections and never answers: a stalled GitHub."""
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        sock.listen(16)
        held: list = []
        stop = threading.Event()

        def accept():
            sock.settimeout(0.2)
            while not stop.is_set():
                try:
                    held.append(sock.accept()[0])
                except OSError:
                    pass

        threading.Thread(target=accept, daemon=True).start()

        def close():
            stop.set()
            for s in held:
                s.close()
            sock.close()

        return f"http://127.0.0.1:{sock.getsockname()[1]}", close

    def test_a_stalled_api_call_fails_within_the_per_request_timeout(self, github_cfg, no_real_sleep):
        base, close = self._silent_server()
        try:
            t0 = time.monotonic()
            with pytest.raises(Exception):
                GitHubClient(base_url=base).get_repo("https://github.com/odpi/egeria")
            took = time.monotonic() - t0
        finally:
            close()
        # 1 s read timeout, at most TOTAL_ATTEMPTS reads; it used to be 15 s x 10 attempts plus back-off
        assert took < 1 * bounded_retry.TOTAL_ATTEMPTS + 6

    def test_a_trickling_zipball_stops_at_the_total_budget(self, github_cfg, tmp_path, monkeypatch):
        """Never quiet long enough for a read timeout, never finished: only a total bound stops it."""
        monkeypatch.setattr(github_cfg, "clone_timeout_seconds", 2)

        class Trickle(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.send_header("Content-Type", "application/zip")
                self.end_headers()
                try:
                    for _ in range(200):
                        self.wfile.write(b"x" * 64)
                        self.wfile.flush()
                        time.sleep(0.1)
                except OSError:
                    pass

            def log_message(self, *a):
                pass

        srv = _Server(Trickle)
        repo = types.SimpleNamespace(full_name="odpi/egeria", default_branch="main")
        import requests

        monkeypatch.setattr(requests.Response, "iter_content", _small_chunks(requests.Response.iter_content))
        try:
            t0 = time.monotonic()
            with pytest.raises(RuntimeError, match="exceeded its 2 s budget"):
                GitHubClient(base_url=srv.base).fetch_zipball(repo, tmp_path / "z.zip")
            took = time.monotonic() - t0
        finally:
            srv.close()
        assert 2 <= took < 8

    def test_the_download_names_its_phase(self, github_cfg, tmp_path, monkeypatch):
        seen: list[str] = []
        hb = step_heartbeat.Heartbeat(RUN_ID, client_factory=MagicMock())
        token = step_heartbeat._CURRENT.set(hb)

        def fake_get(*a, **k):
            seen.append(hb.phase_word)
            assert k["timeout"] == (10, 1)          # connect 10 s, read = request_timeout_seconds
            r = MagicMock()
            r.iter_content.return_value = [b"zip"]
            return r

        monkeypatch.setattr("requests.get", fake_get)
        repo = types.SimpleNamespace(full_name="odpi/egeria", default_branch="main")
        try:
            GitHubClient(base_url="http://127.0.0.1:9").fetch_zipball(repo, tmp_path / "z.zip")
        finally:
            step_heartbeat._CURRENT.reset(token)
        assert seen == ["downloading"] and hb.phase_word == "running"


def _small_chunks(orig):
    def iter_content(self, chunk_size=1, decode_unicode=False):
        return orig(self, chunk_size=64, decode_unicode=decode_unicode)
    return iter_content


# ── the heartbeat ───────────────────────────────────────────────────────────────────────────────────────────────

class FakePrefect:
    """A sync Prefect client: records heartbeat lines, answers the run's state."""

    def __init__(self, state="RUNNING", fail_read=False):
        self.logs: list = []
        self.state = state
        self.fail_read = fail_read
        self.reads = 0

    def __call__(self):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def create_logs(self, logs):
        self.logs.extend(logs)

    def read_flow_run(self, _id):
        self.reads += 1
        if self.fail_read:
            raise ConnectionError("prefect gone")
        return types.SimpleNamespace(state=types.SimpleNamespace(type=types.SimpleNamespace(value=self.state)))


class TestHeartbeat:
    def test_beats_during_a_step_with_its_phase_and_does_not_outlive_it(self):
        fake = FakePrefect()
        with step_heartbeat.running(RUN_ID, interval=0.02, client_factory=fake) as hb:
            with step_heartbeat.phase("inventorying"):
                time.sleep(0.15)
        assert not hb.alive                                   # stopped and joined on the way out
        n = len(fake.logs)
        time.sleep(0.1)
        assert len(fake.logs) == n                            # nothing after the step
        words = [log.message for log in fake.logs]
        assert any(w.startswith("RE heartbeat · inventorying · ") for w in words)
        assert all(str(log.flow_run_id) == RUN_ID for log in fake.logs)

    def test_it_stops_when_the_step_raises(self):
        fake = FakePrefect()
        with pytest.raises(ValueError):
            with step_heartbeat.running(RUN_ID, interval=0.02, client_factory=fake) as hb:
                raise ValueError("step failed")
        assert not hb.alive

    def test_a_cancel_seen_by_a_beat_sets_the_flag(self):
        fake = FakePrefect(state="CANCELLING")
        hb = step_heartbeat.Heartbeat(RUN_ID, client_factory=fake)
        hb.beat()
        assert hb.cancelled.is_set()

    def test_a_failed_state_read_warns_once_and_leaves_the_flag_unset(self, caplog):
        fake = FakePrefect(fail_read=True)
        hb = step_heartbeat.Heartbeat(RUN_ID, client_factory=fake)
        for _ in range(3):
            hb.beat()
        assert not hb.cancelled.is_set()
        assert caplog.text.count("read the run's state") == 1
        assert len(fake.logs) == 3                             # the beats themselves still went out

    def test_no_flow_run_no_thread(self):
        with step_heartbeat.running("") as hb:
            assert hb is None
        with step_heartbeat.phase("downloading"):             # a no-op outside a heartbeat
            pass

    def test_the_flow_body_runs_a_heartbeat_for_the_step_and_its_flag_feeds_the_cancel_check(self, tmp_path):
        from resource_explorer.registry import ProjectRegistry

        registry = ProjectRegistry(db_path=str(tmp_path / "t.db"))
        fake = FakePrefect()
        made: list = []

        def heartbeat_for(fr):
            cm = step_heartbeat.running(fr, interval=0.02, client_factory=fake)
            made.append(cm)
            return cm

        def runner(**kw):
            with step_heartbeat.phase("downloading"):
                time.sleep(0.1)
            return {"ok": True}

        with patch.object(flows, "ProjectRegistry", lambda *a, **k: registry), \
             patch.object(flows, "run_surveyor_step_task", runner), \
             patch.object(flows, "_heartbeat_for", heartbeat_for), \
             patch("resource_explorer.surveyors.run_cancellation.prefect_run_is_cancelled", lambda *a, **k: False):
            flows._run_step_as_flow_run(RUN_ID, "database", "s1", "step_x", {}, keep_result=True)
        assert any("· downloading ·" in log.message for log in fake.logs)
        assert threading.active_count() < 50 and not any(
            t.name.startswith("re-heartbeat-") for t in threading.enumerate())

        hb = step_heartbeat.Heartbeat(RUN_ID, client_factory=fake)
        hb.cancelled.set()
        with patch("resource_explorer.surveyors.run_cancellation.prefect_run_is_cancelled", lambda *a, **k: False):
            assert flows._cancel_check_for(RUN_ID, hb)() is True


# ── RE's waiter names the last heartbeat ────────────────────────────────────────────────────────────────────────

def _log(message, ts):
    return types.SimpleNamespace(message=message, timestamp=ts)


def _waiting_client(logs=None, read_logs_raises=None):
    state = MagicMock()
    state.is_completed.return_value = False
    state.is_failed.return_value = False
    state.is_cancelled.return_value = False
    state.name = "Running"
    state.type = types.SimpleNamespace(value="RUNNING")
    client = MagicMock()
    client.read_flow_run = AsyncMock(return_value=types.SimpleNamespace(state=state))
    client.set_flow_run_state = AsyncMock(return_value=types.SimpleNamespace(
        status=types.SimpleNamespace(value="ACCEPT"), details=None))
    client.read_logs = AsyncMock(side_effect=read_logs_raises, return_value=logs or [])
    return client


def _time_out(client):
    async def no_sleep(_):
        return None

    ticks = iter([0.0, 1200.0])   # dispatch, then the deadline

    with pytest.raises(adapter.PrefectFlowRunTimeout) as ei:
        asyncio.run(adapter._await_flow_run(client, RUN_ID, RUN_ID, timeout_seconds=1200, poll_interval=1,
                                            clock=lambda: next(ticks), sleep=no_sleep))
    return str(ei.value)


class TestTimeoutLineNamesTheHeartbeat:
    def test_last_heard_with_its_phase(self):
        ts = datetime(2026, 11, 18, 23, 24, 5, tzinfo=timezone.utc)
        client = _waiting_client([
            _log("some step output", datetime(2026, 11, 18, 23, 25, tzinfo=timezone.utc)),
            _log("RE heartbeat · downloading · 54 s into the step", ts),
            _log("RE heartbeat · running · 24 s into the step", datetime(2026, 11, 18, 23, 23, 35,
                                                                         tzinfo=timezone.utc)),
        ])
        msg = _time_out(client)
        assert f"timed out after 1200 s waiting for Prefect flow run {RUN_ID} (state Running)" in msg
        assert "· last heard 23:24:05Z (downloading)" in msg
        assert msg.endswith("; the run was cancelled")
        kwargs = client.read_logs.await_args.kwargs
        assert [str(i) for i in kwargs["log_filter"].flow_run_id.any_] == [RUN_ID]

    def test_no_heartbeat_says_so(self):
        msg = _time_out(_waiting_client([_log("worker started", datetime.now(timezone.utc))]))
        assert "· no heartbeat received" in msg
        assert "0 s ago" not in msg and "last heard" not in msg

    def test_an_unreadable_log_says_so_rather_than_none(self):
        msg = _time_out(_waiting_client(read_logs_raises=ConnectionError("gone")))
        assert "· heartbeat unreadable (ConnectionError)" in msg
