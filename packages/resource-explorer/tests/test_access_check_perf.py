"""The curation-access check's cost on a Publish press and run (2026-10-10, after Brief Z).

Live on 8813 a 5-item egeria_git press took 23.7 s in a deployment with NO zones in use. The access half of
that was: one Egeria element read per blueprint member already in Egeria, each on a freshly built client
(pyegeria's constructor handshake + a daemon token mint + the read: three Egeria calls), one after another;
and `blueprint_attach_scopes` walking every architecture_recovery scope with one registry query each.

These tests measure the same shape with fakes that cost `LATENCY` seconds per Egeria call: they count the
Egeria calls and the per-scope registry queries and bound the elapsed time. Nothing of RE's own is faked:
the route, the decision, `publish_item_access`, the run's re-check.
"""
from __future__ import annotations

import threading
import time

import pytest
from fastapi.testclient import TestClient

from resource_explorer import architecture_publish as ap
from tests.test_architecture_publish_e2e import world  # noqa: F401 - the fixture
from tests.zone_fakes import FakeEgeria, element

pytestmark = pytest.mark.usefixtures("as_daemon")

LATENCY = 0.3              # seconds per Egeria call
USER = "peterprofile"
BP = "deployment::OMAG-Server-Platform"
NEW = [f"src/new{i}" for i in range(4)]          # accepted, not in Egeria yet: written by this press
HELD = [f"src/held{i}" for i in range(8)]        # accepted, already in Egeria: members the blueprint attaches
FILLER = 200                                      # other components of the repository (scopes the map walks)


class SlowEgeria(FakeEgeria):
    """The access check's clients with a cost: building one is pyegeria's handshake plus a token mint (two
    Egeria calls), each read one call. Thread-safe counting; reads overlap when the caller overlaps them."""

    def __init__(self):
        super().__init__()
        self.lock = threading.Lock()
        self.builds = 0
        self.reads = 0

    def build(self):
        time.sleep(2 * LATENCY)
        with self.lock:
            self.builds += 1
        return self

    def get_metadata_element_by_guid(self, guid, **kw):
        time.sleep(LATENCY)
        with self.lock:
            self.reads += 1
        return super().get_metadata_element_by_guid(guid, **kw)

    @property
    def egeria_calls(self) -> int:
        return 2 * self.builds + self.reads


@pytest.fixture
def slow(monkeypatch):
    fake = SlowEgeria()
    monkeypatch.setattr("resource_explorer.zone_access._metadata_expert", fake.build)
    monkeypatch.setattr("resource_explorer.zone_access._security_officer", fake.build)
    monkeypatch.setattr("resource_explorer.zone_access._platform", lambda: ("Quickstart platform", "plat-guid"))
    monkeypatch.delenv("EXPLORER_DRAFT_ZONE", raising=False)
    monkeypatch.setattr("resource_explorer.egeria_identity.configured_publish_zones", lambda: [])
    return fake


def _seed(reg):
    """The egeria_git press in miniature: 4 new components and one new blueprint whose members are those 4
    and 8 components already in Egeria, in a repository with FILLER other components. No zone anywhere."""
    def comp(scope, slug):
        reg.upsert_finding("p", "architecture_recovery", [{
            "check_name": "component", "label": "x",
            "detail": {"name": slug.upper(), "slug": slug, "type": "Software Service", "admission": "built"}}],
            surveyed_at="2026-10-09T00:00:00", scope_locator=scope)
    for scope in NEW + HELD:
        comp(scope, scope.rsplit("/", 1)[-1])
        reg.record_component_verdict("repo", "p", scope, "accepted", "", "", decided_by=USER)
    for i in range(FILLER):
        comp(f"lib/f{i}", f"f{i}")
    for i, scope in enumerate(HELD):
        reg.record_materialized_component("repo", "p", scope, f"SolutionComponent::repo::p::{scope}", f"g-held{i}")
    persp, _, cluster = BP.partition("::")
    reg.upsert_finding("p", "architecture_blueprints", [{
        "check_name": "candidate_blueprint", "label": cluster,
        "detail": {"name": cluster, "perspective": persp, "members": [s.rsplit("/", 1)[-1] for s in NEW + HELD],
                   "children": [], "parent": "", "oversized": False, "composed_into": ""}}],
        surveyed_at="2026-10-09T00:00:00")
    reg.record_component_verdict("repo", "p", BP, "accepted", "", "", verdict_target="blueprint", decided_by=USER)


def _count_scope_queries(monkeypatch, reg) -> list:
    seen: list = []
    real = type(reg).query_findings_all_runs

    def counting(self, slug, kind, scope):
        if kind == "architecture_recovery":
            seen.append(scope)
        return real(self, slug, kind, scope)
    monkeypatch.setattr(type(reg), "query_findings_all_runs", counting)
    return seen


@pytest.fixture
def press(world, monkeypatch):
    """POST /architecture/publish through the real app, as USER, with the real curation check."""
    reg, _fake, _outbox = world
    # Imported BEFORE anything is patched: a route module that binds `get_current_user` by name at its first
    # import would otherwise keep the patched one for every later test in the process.
    from resource_explorer.web.app import app
    monkeypatch.setattr("resource_explorer.registry.ProjectRegistry.__init__",
                        lambda self, db_path=None: setattr(self, "__dict__", reg.__dict__) or None)
    monkeypatch.setenv("TRELLIS_ANONYMOUS_READ", "true")
    monkeypatch.setattr("resource_explorer.auth.get_current_user",
                        lambda request: {"user_id": USER, "egeria_token": f"tok-{USER}"})
    from resource_explorer.a2a_auth import CallerIdentity

    monkeypatch.setattr("resource_explorer.workflows.curate._caller_identity",
                        lambda: CallerIdentity(user_id=USER, egeria_token="t", auth_source="app-jwt"))
    return TestClient(app)


def _measure(fn):
    t0 = time.perf_counter()
    out = fn()
    return out, time.perf_counter() - t0


def test_press_access_cost_with_no_zones(world, press, slow, monkeypatch, capsys):
    reg, _fake, _outbox = world
    _seed(reg)
    plan = ap.publish_plan(reg, "p")
    assert [c["path"] for c in plan["components"]["to_write"]] == NEW
    assert [b["key"] for b in plan["blueprints"]["to_write"]] == [BP]
    scopes = _count_scope_queries(monkeypatch, reg)

    r, elapsed = _measure(lambda: press.post("/api/projects/p/architecture/publish"))
    assert r.status_code == 200, r.text
    assert r.json()["queued"] == 5 and r.json()["refused"] == []
    with capsys.disabled():
        print(f"\n[press] egeria calls={slow.egeria_calls} (builds={slow.builds} reads={slow.reads}) "
              f"per-scope registry queries={len(scopes)} elapsed={elapsed:.2f}s")
    # Every held member is read once: zones not configured, so the element's own ZoneMembership decides.
    assert slow.reads == len(HELD)
    # One client per concurrent reader, not one per read; no walk of every scope one query at a time.
    assert slow.builds <= 4
    assert len(scopes) < 10
    assert elapsed < 4 * LATENCY * 2 + 1.5


def test_run_access_cost_with_no_zones(world, slow, monkeypatch, capsys):
    """The run re-checks each item right before its write and reads every element fresh for each check (no
    cache across checks: promotion changes zones mid-run), but on one client per check, not one per read.
    Run on a shared-pool thread, as the run queue runs it (`run_queue`: run_sync -> claim_and_execute_once), so
    the reads go one after another (the pool's re-entrancy rule)."""
    import json as _json

    from resource_explorer.a2a_auth import CallerIdentity, current_caller
    from resource_explorer.concurrency import run_sync

    reg, _fake, _outbox = world
    _seed(reg)
    target = {"slug": "p", "paths": list(NEW), "blueprints": [BP]}
    scopes = _count_scope_queries(monkeypatch, reg)

    def as_the_worker():
        # Pool threads carry no ContextVar: the worker declares its daemon and the requester itself.
        from resource_explorer.egeria_clients import Daemon, DaemonReason, acting_as

        reset = current_caller.set(CallerIdentity(user_id=USER, egeria_token="", auth_source="queued-run"))
        try:
            with acting_as(Daemon(DaemonReason.SCHEDULER)):
                return ap.run_publish(reg, "p", target, "run-1")
        finally:
            current_caller.reset(reset)
    res, elapsed = _measure(lambda: run_sync(as_the_worker))
    with capsys.disabled():
        print(f"\n[run]   egeria calls={slow.egeria_calls} (builds={slow.builds} reads={slow.reads}) "
              f"per-scope registry queries={len(scopes)} elapsed={elapsed:.2f}s")
    assert sorted(r["status"] for r in res) == ["done"] * 5, _json.dumps(res)
    # Two checks touch Egeria: the blueprint before its create (its 12 members, the 4 just written included)
    # and the elements its write resolved (the blueprint and the 12), each read fresh, each on one client.
    assert slow.reads == 12 + 13
    assert slow.builds == 2
    assert len(scopes) < 10


# ── what the batching must not change ─────────────────────────────────────────────────────────────────

def test_the_run_reader_reads_every_check_fresh(monkeypatch):
    """`cache_elements=False` (the run): an element whose zones change between two checks (promotion does
    that mid-run) is seen with its new zones by the next check."""
    from tests.zone_fakes import install
    from resource_explorer.zone_access import EgeriaAccessReader

    fake = install(monkeypatch)
    reader = EgeriaAccessReader()
    with reader.check(["g-1"]):
        assert reader.element("g-1")[0] == []
        assert reader.element("g-1")[0] == [], "within one check: read once"
    fake.elements["g-1"] = element("g-1", ["published-zone"])
    with reader.check(["g-1"]):
        assert reader.element("g-1")[0] == ["published-zone"]
    assert reader.element("g-1")[0] == ["published-zone"]
    assert [c for c in fake.calls if c[0] == "element"] == [("element", "g-1")] * 3


def test_the_press_reader_holds_elements_for_its_request_only(monkeypatch):
    from tests.zone_fakes import install
    from resource_explorer.zone_access import EgeriaAccessReader

    fake = install(monkeypatch)
    reader = EgeriaAccessReader(cache_elements=True)
    with reader.check(["g-1", "g-2"]):
        pass
    fake.elements["g-1"] = element("g-1", ["published-zone"])
    assert reader.element("g-1")[0] == [], "one request: the read it made is the answer it uses"
    assert EgeriaAccessReader(cache_elements=True).element("g-1")[0] == ["published-zone"], "a new request reads again"
    assert sorted(c[1] for c in fake.calls if c[0] == "element") == ["g-1", "g-1", "g-2"]


def test_one_unreadable_element_denies_only_its_own_check(monkeypatch):
    """A failed read in a batch is that element's answer (deny, with the reason), never the batch's, and never
    'no zones'."""
    from tests.zone_fakes import install
    from resource_explorer.zone_access import AccessUnreadable, EgeriaAccessReader

    fake = install(monkeypatch)
    fake.fail_element = "g-bad"
    reader = EgeriaAccessReader(cache_elements=True)
    with reader.check(["g-ok", "g-bad", "g-ok2"]):
        assert reader.element("g-ok")[0] == []
        assert reader.element("g-ok2")[0] == []
        with pytest.raises(AccessUnreadable, match="the element g-bad: ConnectionError: platform unreachable"):
            reader.element("g-bad")
        with pytest.raises(AccessUnreadable):
            reader.element("g-bad")


def test_a_lane_that_cannot_build_its_client_makes_its_elements_unreadable(monkeypatch):
    from resource_explorer.zone_access import AccessUnreadable, read_elements

    def boom():
        raise ConnectionError("handshake refused")
    monkeypatch.setattr("resource_explorer.zone_access._metadata_expert", boom)
    out = read_elements(["g-1", "g-2", "g-3"])
    assert set(out) == {"g-1", "g-2", "g-3"}
    assert all(isinstance(v, AccessUnreadable) and "handshake refused" in str(v) for v in out.values())


def test_a_lane_with_no_answer_in_time_is_unreadable_not_open(monkeypatch):
    from resource_explorer import zone_access
    from resource_explorer.zone_access import AccessUnreadable, read_elements

    gate = threading.Event()

    class Hangs:
        def get_metadata_element_by_guid(self, guid, **kw):
            gate.wait(5)
            return element(guid)
    monkeypatch.setattr(zone_access, "_metadata_expert", Hangs)
    monkeypatch.setattr(zone_access, "ACCESS_READ_TIMEOUT_SECONDS", 0.2)
    try:
        out = read_elements(["g-1"])
    finally:
        gate.set()
    assert isinstance(out["g-1"], AccessUnreadable) and "no answer within 0.2s" in str(out["g-1"])


def test_a_zoned_member_still_refuses_the_blueprint_on_the_press(world, press, monkeypatch):
    """Brief Z unchanged by the batching: one held member in a secured zone that grants USER nothing refuses
    the blueprint (and only it) at the press, with the member named."""
    from tests.zone_fakes import install

    reg, _fake, _outbox = world
    _seed(reg)
    fake = install(monkeypatch)
    fake.elements["g-held3"] = element("g-held3", ["sales-zone"], owners=["someone-else"])
    fake.controls["sales-zone"] = {"associatedSecurityList": {"DEFAULT": ["salesTeam"]}}
    r = press.post("/api/projects/p/architecture/publish")
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["queued"] == 4
    assert [(x["key"], x["words"]) for x in out["refused"]] == [
        (BP, f"held3: zone sales-zone does not grant {USER} attach in Egeria")]
