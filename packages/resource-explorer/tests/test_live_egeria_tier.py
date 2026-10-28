"""The live Egeria tiers are opt-in. Pure logic, fake environment, no Egeria.

Nothing here contacts a platform: reachability is a recording stub. Passes with
GITHUB_ACTIONS unset or true because every test passes its own environment.
"""
from __future__ import annotations

import pytest

from tests import live_egeria_tier as T
from tests import conftest as C

CLEARED = "dwolfson+peers-ask-2026-10-05/2026-10-05T18:00Z"


class Probe:
    def __init__(self, up=True):
        self.up, self.calls = up, 0

    def __call__(self):
        self.calls += 1
        return self.up


def _decide(env=None, reads=False, writes=False, up=True):
    probe = Probe(up)
    return T.decide(env or {}, reads_flag=reads, writes_flag=writes, reachable=probe), probe


def test_default_skips_reads_even_when_egeria_is_reachable_and_never_probes():
    d, probe = _decide(up=True)
    assert not d.reads_on and not d.writes_on
    assert d.read_skip_reason == (
        "live Egeria tier is opt-in: run with --live-egeria-reads "
        "(or RE_LIVE_EGERIA_READS=1) after a peer round")
    assert probe.calls == 0, "a default run must not contact the platform"


def test_reads_flag_or_env_turns_reads_on():
    assert _decide(reads=True)[0].reads_on
    assert _decide({T.READS_ENV: "1"})[0].reads_on
    assert not _decide({T.READS_ENV: "0"})[0].reads_on


def test_reads_on_but_unreachable_skips_with_the_reachability_reason():
    d, _ = _decide(reads=True, up=False)
    assert not d.reads_on and d.read_skip_reason == T.UNREACHABLE_REASON


def test_writes_flag_without_clearance_skips_writes_not_fails():
    for env in ({}, {T.CLEARED_ENV: ""}, {T.CLEARED_ENV: "   "}):
        d, _ = _decide(env, writes=True)
        assert d.reads_on is True                 # writes imply reads
        assert d.writes_on is False
        assert d.write_skip_reason == (
            "live Egeria writes need RE_LIVE_EGERIA_WRITES_CLEARED=<who>/<UTC time> "
            "naming the peer round")


def test_clearance_without_the_flag_does_not_start_writes():
    d, _ = _decide({T.CLEARED_ENV: CLEARED}, reads=True)
    assert d.reads_on and not d.writes_on


def test_flag_plus_clearance_turns_writes_on_and_carries_the_value():
    d, _ = _decide({T.CLEARED_ENV: CLEARED}, writes=True)
    assert d.reads_on and d.writes_on and d.cleared_by == CLEARED


def test_ci_is_always_off_whatever_the_flags():
    env = {"GITHUB_ACTIONS": "true", T.CLEARED_ENV: CLEARED, T.READS_ENV: "1"}
    d, probe = _decide(env, reads=True, writes=True)
    assert not d.reads_on and not d.writes_on and probe.calls == 0


def test_banner_names_url_and_user_and_never_a_secret():
    reads, _ = _decide(reads=True)
    line = T.banner(reads, "https://localhost:9443", "erinoverview")
    assert line == "live Egeria tier: https://localhost:9443 as erinoverview · reads ON"
    writes, _ = _decide({T.CLEARED_ENV: CLEARED}, writes=True)
    line = T.banner(writes, "https://localhost:9443", "erinoverview")
    assert line == ("live Egeria tier: https://localhost:9443 as erinoverview"
                    f" · writes ON · cleared by {CLEARED}")
    assert "secret" not in line and "password" not in line.lower()


def test_banner_is_silent_when_the_tier_is_off():
    off, _ = _decide()
    assert T.banner(off, "https://localhost:9443", "erinoverview") is None


# --- the real collection hook, with fake items and a recording probe --------

class _Item:
    def __init__(self, *marks):
        self.keywords = {m: True for m in marks}
        self.added = []

    def add_marker(self, mark):
        self.added.append(mark.kwargs.get("reason"))


class _Config:
    def __init__(self, **opts):
        self.opts = opts

    def getoption(self, name):
        return self.opts.get(name, False)


def _run_hook(monkeypatch, env, **opts):
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    monkeypatch.delenv(T.READS_ENV, raising=False)
    monkeypatch.delenv(T.CLEARED_ENV, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    probe = Probe(True)
    monkeypatch.setattr(C, "_egeria_available", probe)
    reads, writes = _Item("requires_egeria"), _Item("live_egeria_writes", "requires_egeria")
    legacy = _Item("live_egeria", "requires_egeria")
    C.pytest_collection_modifyitems(_Config(**opts), [reads, writes, legacy])
    return reads, writes, legacy, probe


def test_hook_default_skips_every_live_item_without_probing(monkeypatch):
    reads, writes, legacy, probe = _run_hook(monkeypatch, {})
    assert all(i.added for i in (reads, writes, legacy))
    assert "opt-in" in reads.added[0] and probe.calls == 0


def test_hook_reads_flag_runs_reads_but_not_writes(monkeypatch):
    reads, writes, _, _ = _run_hook(monkeypatch, {}, **{"--live-egeria-reads": True})
    assert reads.added == []
    assert any("opt-in" in r for r in writes.added)


def test_hook_writes_flag_without_clearance_skips_writes_with_the_reason(monkeypatch):
    reads, writes, _, _ = _run_hook(monkeypatch, {}, **{"--live-egeria-writes": True})
    assert reads.added == []
    assert "RE_LIVE_EGERIA_WRITES_CLEARED" in writes.added[-1]


def test_hook_writes_flag_with_clearance_runs_writes(monkeypatch):
    _, writes, _, _ = _run_hook(monkeypatch, {T.CLEARED_ENV: CLEARED},
                                **{"--live-egeria-writes": True})
    assert writes.added == []


def test_the_write_fixture_records_the_clearance_value():
    import inspect
    from tests import live_egeria_write_fixtures as F

    src = inspect.getsource(F.live_egeria_write_target)
    assert "live_egeria_writes_cleared_by" in src and "cleared_by=cleared_by" in src
    assert "cleared_by" in F.LiveEgeriaWriteTarget.__dataclass_fields__
