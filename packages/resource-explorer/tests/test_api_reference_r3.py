"""API reference round 3: the replace race, the fetch-thread cap, and the extra always-deny ranges."""
from __future__ import annotations

import threading
import time
import unittest.mock as mock

import httpx
import pytest

from resource_explorer.api_reference import fetch_policy as fp
from resource_explorer.api_reference import live, store
from resource_explorer.registry import Project, ProjectRegistry

X = "http://127.0.0.1:1/old.json"
Y = "http://127.0.0.1:1/new.json"


@pytest.fixture
def db(tmp_path):
    r = ProjectRegistry(db_path=str(tmp_path / "t.db"))
    r.add(Project(slug="tg", display_name="TG", github_url="https://github.com/x/tg", description=""))
    return r


def _spec(title, path):
    return httpx.MockTransport(lambda r: httpx.Response(200, json={
        "openapi": "3.0.0", "info": {"title": title, "version": "1"},
        "paths": {path: {"get": {"responses": {"200": {"description": "x"}}}}}}))


def _current_rows(db):
    with db._conn() as c:
        return [dict(r) for r in c.execute(
            "SELECT id, origin, title, status, attempted_by FROM api_reference_documents "
            "WHERE source = 'running_instance' AND superseded_at IS NULL").fetchall()]


class TestReplaceRace:
    def _race(self, db, transport):
        live.register_instance(db, "tg", X, transport=_spec("OLD", "/old"))
        real = live.fetch_openapi
        done = []

        def replacing(*a, **k):
            if not done:
                done.append(1)
                live.register_instance(db, "tg", Y, by="bob", transport=_spec("NEW", "/new"))
            return real(*a, **k)

        with mock.patch.object(live, "fetch_openapi", replacing):
            return live.refetch_instance(db, "tg", by="dan", transport=transport)

    def test_success_does_not_land_in_the_replacement_row(self, db):
        r = self._race(db, _spec("OLD", "/old"))
        assert r["ok"] is False and "removed or replaced" in r["reason"]
        rows = _current_rows(db)
        assert [(x["origin"], x["title"]) for x in rows] == [(Y, "NEW")]

    def test_failure_does_not_mark_the_replacement_failed(self, db):
        r = self._race(db, httpx.MockTransport(lambda req: httpx.Response(500)))
        assert r["ok"] is False and "removed or replaced" in r["reason"]
        rows = _current_rows(db)
        assert rows[0]["status"] == "ok" and rows[0]["origin"] == Y

    def test_update_instance_wants_the_row_it_loaded(self, db):
        live.register_instance(db, "tg", X, transport=_spec("OLD", "/old"))
        old_id = live.load_instance(db, "tg")["id"]
        live.register_instance(db, "tg", Y, transport=_spec("NEW", "/new"))
        assert store.update_instance(db, "tg", None, reason="x", by="d", document_id=old_id) is False

    def test_plain_refetch_still_works(self, db):
        live.register_instance(db, "tg", X, transport=_spec("OLD", "/old"))
        assert live.refetch_instance(db, "tg", by="dan", transport=_spec("OLD2", "/old"))["ok"] is True


class TestThreadCap:
    def test_exhaustion_rejects_and_worker_releases(self):
        gate = threading.Event()

        def hang(host, port):
            gate.wait(10)
            return ["127.0.0.1"]

        before = threading.active_count()
        results = [live.fetch_openapi("http://hang.test/o", total_seconds=0.05, allow=["hang.test"], resolver=hang)
                   for _ in range(8)]
        reasons = [r.reason for r in results]
        assert reasons.count("no answer in time") == 6
        assert reasons.count("too many API fetches in progress, try again shortly") == 2
        assert threading.active_count() - before <= 6
        gate.set()
        for _ in range(100):
            if live._FETCH_SLOTS.acquire(blocking=False):
                live._FETCH_SLOTS.release()
                break
            time.sleep(0.02)
        got = []
        for _ in range(6):
            got.append(live._FETCH_SLOTS.acquire(blocking=False))
        assert all(got)                                    # every slot is back once the workers end
        for _ in range(6):
            live._FETCH_SLOTS.release()

    def test_slot_is_released_when_worker_ends_normally(self):
        for _ in range(10):
            live.fetch_openapi("http://127.0.0.1:1/x", transport=httpx.MockTransport(lambda r: httpx.Response(404)))
        time.sleep(0.1)
        got = [live._FETCH_SLOTS.acquire(blocking=False) for _ in range(6)]
        assert all(got)
        for _ in range(6):
            live._FETCH_SLOTS.release()

    def test_deadline_is_checked_between_addresses(self):
        calls = []

        def handler(req):
            calls.append(1)
            time.sleep(0.2)
            raise httpx.ConnectError("no")

        res = live.fetch_openapi("http://multi.test/o", transport=httpx.MockTransport(handler), allow=["multi.test"],
                                 total_seconds=0.1, resolver=lambda h, p: ["127.0.0.1", "127.0.0.2", "127.0.0.3"])
        assert not res.ok and res.reason == "no answer in time"
        time.sleep(0.3)
        assert len(calls) == 1


class TestMoreDenied:
    @pytest.mark.parametrize("ip", ["2001:0:4136:e378:8000:63bf:3fff:fdd2", "2001::1", "::ffff:0:7f00:1",
                                    "::ffff:0:a9fe:a9fe", "64:ff9b:1::a9fe:a9fe", "64:ff9b:1::7f00:1",
                                    "64:ff9b:1:ffff::1"])
    @pytest.mark.parametrize("allow", [["*"], ["::/0"]])
    def test_denied(self, ip, allow):
        assert not fp.check_target(ip, 80, allow=allow).ok

    def test_mapped_loopback_still_unwraps(self):
        assert fp.check_target("::ffff:127.0.0.1", 80, allow=["127.0.0.1"]).ips == ("127.0.0.1",)
