"""A live claim stays live: the holder re-stamps it while it works; a dead holder's claim lapses on the lease.

Backlog: "claim heartbeat". Lease semantics unchanged (`take_claim(stale_after_seconds)`), and a
claim is renewed only by the holder that still holds it. Temp SQLite; no Egeria.
"""
from __future__ import annotations

import time

import pytest

from resource_explorer.registry import ProjectRegistry
from resource_explorer.surveyors.arch_recovery import blueprint_materializer as bm
from tests.test_blueprint_identity_kind_repo import BASE, OLD, _m, _make, _prov, ROOT

pytestmark = pytest.mark.usefixtures("as_daemon")


@pytest.fixture
def reg(tmp_path):
    return ProjectRegistry(db_path=str(tmp_path / "t.db"))


def test_refresh_claim_renews_only_for_the_holder(reg):
    assert reg.take_claim("k", "me")
    assert reg.refresh_claim("k", "me") is True
    assert reg.refresh_claim("k", "someone-else") is False
    assert reg.refresh_claim("gone", "me") is False


def test_a_dead_holders_claim_is_reclaimable_after_the_lease_and_not_before(reg):
    assert reg.take_claim("k", "dead")
    assert not reg.take_claim("k", "next", stale_after_seconds=60)      # inside the lease: refused
    time.sleep(0.3)
    assert reg.take_claim("k", "next", stale_after_seconds=0.1)         # past the lease: taken over once
    assert not reg.take_claim("k", "third", stale_after_seconds=60)     # and only one taker
    assert reg.refresh_claim("k", "dead") is False                       # the old holder cannot renew it back


def test_a_long_run_keeps_its_claim_and_nobody_else_adopts(reg, monkeypatch):
    monkeypatch.setattr(bm, "CLAIM_HEARTBEAT_SECONDS", 0.05)
    m = _m(reg, found={BASE: {"guid": OLD, "props": _prov(ROOT)}})
    real = m._automated_curation.get_guid_for_name.side_effect
    seen = {}

    def slow_search(qn, **kw):
        out = real(qn, **kw)
        time.sleep(0.6)                      # the work outlives a 0.25 s lease several times over
        # an intruder using the same lease: while the heartbeat is renewing, it cannot take the claim
        seen["taken"] = reg.take_claim(f"blueprint-claim::{BASE}", "intruder", stale_after_seconds=0.25)
        return out

    m._automated_curation.get_guid_for_name.side_effect = slow_search
    assert _make(m, live_clusters={ROOT})["guid"] == OLD
    assert seen["taken"] is False
    assert reg.take_claim(f"blueprint-claim::{BASE}", "later")           # released by the holder when done


def test_the_heartbeat_stops_when_the_work_ends(reg, monkeypatch):
    import threading
    monkeypatch.setattr(bm, "CLAIM_HEARTBEAT_SECONDS", 0.05)
    m = _m(reg, found={BASE: {"guid": OLD, "props": _prov(ROOT)}})
    _make(m, live_clusters={ROOT})
    assert not [t for t in threading.enumerate() if t.name == "blueprint-claim-heartbeat"]
