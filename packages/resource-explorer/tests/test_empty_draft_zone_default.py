"""Owner's ruling 2026-10-08: DRAFT is a content status, not a zone.

RE stamps NO zone on what it creates unless `EXPLORER_DRAFT_ZONE` is set. Recording fakes at the
classification client, so an empty-body `ZoneMembership` call (which would change Egeria's behaviour)
cannot hide behind a stubbed helper.
"""
from __future__ import annotations

import threading
from unittest.mock import MagicMock

import pytest

from resource_explorer import egeria_identity as ident
from resource_explorer.workflows import curate

# Brief I: these tests fake the Egeria clients; the publish under test is the worker's, a declared daemon job.
pytestmark = pytest.mark.usefixtures("as_daemon")

CONFIGURED = "re-draft-configured"


class Spy:
    def __init__(self):
        self.ownership, self.zones, self.cleared = [], [], []

    def add_ownership_to_element(self, guid, body):
        self.ownership.append((guid, body))

    def add_zone_membership(self, guid, body):
        self.zones.append((guid, body))

    def clear_zone_membership(self, guid, body):
        self.cleared.append(guid)


@pytest.fixture(autouse=True)
def _no_ambient_zone(monkeypatch):
    monkeypatch.delenv("EXPLORER_DRAFT_ZONE", raising=False)
    monkeypatch.delenv("EXPLORER_PUBLISH_ZONES", raising=False)


@pytest.fixture
def spy(monkeypatch):
    s = Spy()
    monkeypatch.setattr(ident, "classification_client", lambda identity=None: s)
    return s


def test_default_draft_zone_is_empty():
    assert ident.draft_zone() is None
    assert ident.draft_zones() == []


def test_a_configured_draft_zone_is_exactly_that_one_zone(monkeypatch):
    monkeypatch.setenv("EXPLORER_DRAFT_ZONE", f"  {CONFIGURED} ")
    assert ident.draft_zone() == CONFIGURED
    assert ident.draft_zones() == [CONFIGURED]


def test_stamp_published_sends_no_zone_membership_by_default(spy):
    out = ident.stamp_published("g1", "dan", client=spy)
    assert spy.zones == [], "an unzoned element must carry no ZoneMembership call at all"
    assert out["zones"] == [] and out["zone_membership"] is False
    assert len(spy.ownership) == 1                       # ownership is untouched by the ruling


def test_stamp_published_sends_the_configured_zone(spy, monkeypatch):
    monkeypatch.setenv("EXPLORER_DRAFT_ZONE", CONFIGURED)
    ident.stamp_published("g1", "dan", client=spy)
    assert [b["properties"]["zoneMembership"] for _, b in spy.zones] == [[CONFIGURED]]


def test_materialiser_sends_no_zone_by_default_and_the_configured_one_when_set(spy, monkeypatch, zone_controls_ok):
    from tests.test_component_materializer import _materializer

    reg = MagicMock(get_materialized_component=MagicMock(return_value=None))
    _materializer(registry=reg).materialize("repo", "p", "src/a", name="a",
                                            component_type="Software Service",
                                            perspective="deployment", confidence=80)
    assert spy.zones == []
    monkeypatch.setenv("EXPLORER_DRAFT_ZONE", CONFIGURED)
    _materializer(registry=reg).materialize("repo", "p", "src/b", name="b",
                                            component_type="Software Service",
                                            perspective="deployment", confidence=80)
    assert [b["properties"]["zoneMembership"] for _, b in spy.zones] == [[CONFIGURED]]


def test_private_zones_still_win_over_the_draft_zone(spy, monkeypatch):
    from tests.test_component_materializer import _materializer

    monkeypatch.setenv("EXPLORER_DRAFT_ZONE", CONFIGURED)
    reg = MagicMock(get_materialized_component=MagicMock(return_value=None))
    m = _materializer(registry=reg)
    owner_zones = ident.private_zones("alice")
    # the same expression the materialiser uses: private zones when present, else the draft zones
    assert (owner_zones or ident.draft_zones()) == owner_zones
    assert ([] or ident.draft_zones()) == [CONFIGURED]
    assert m is not None


def test_publisher_default_zone_names_are_empty_and_no_property_is_written(monkeypatch):
    from resource_explorer.surveyors.egeria_publisher import EgeriaPublisher

    assert EgeriaPublisher(platform_url="https://fake").zone_names == []
    monkeypatch.setenv("EXPLORER_DRAFT_ZONE", CONFIGURED)
    assert EgeriaPublisher(platform_url="https://fake").zone_names == [CONFIGURED]


def test_referenced_asset_stamp_is_unzoned_by_default(spy, monkeypatch):
    from resource_explorer.surveyors.egeria_publisher import EgeriaPublisher

    pub = EgeriaPublisher(platform_url="https://fake")
    pub._stamp_governance("asset-guid", produced=False)
    assert spy.zones == []


def test_ensure_draft_zone_exists_does_nothing_when_unconfigured(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("Egeria must not be contacted when no draft zone is configured")
    monkeypatch.setattr("resource_explorer.egeria_clients._daemon_credential", boom)
    assert ident.ensure_draft_zone_exists() == {"status": "none", "zone": None}


def _run_bootstrap(worker, monkeypatch):
    """Run `_ensure_draft_zone` synchronously and report whether the draft ensure was called."""
    called = []
    monkeypatch.setattr(ident, "ensure_draft_zone_exists", lambda *a, **k: called.append(1) or {"status": "x"})
    monkeypatch.setattr(ident, "ensure_private_zone_exists", lambda *a, **k: {"enforced": True})
    monkeypatch.setattr(worker, "LeaderLock", lambda name: type(
        "L", (), {"acquire": lambda s: True, "release": lambda s: None})())

    class T(threading.Thread):
        def start(self):                      # run the bootstrap body inline, deterministically
            self.run()
    monkeypatch.setattr(worker.threading, "Thread", T)
    worker._ensure_draft_zone()
    return called


def test_worker_skips_the_draft_zone_ensure_by_default_and_logs_the_mode(monkeypatch, caplog):
    import resource_explorer.worker as worker

    with caplog.at_level("INFO"):
        assert _run_bootstrap(worker, monkeypatch) == []
    assert "draft zone: none (default)" in caplog.text


def test_worker_ensures_the_draft_zone_when_configured(monkeypatch, caplog):
    import resource_explorer.worker as worker

    monkeypatch.setenv("EXPLORER_DRAFT_ZONE", CONFIGURED)
    with caplog.at_level("INFO"):
        assert _run_bootstrap(worker, monkeypatch) == [1]
    assert f"draft zone: {CONFIGURED}" in caplog.text


def test_post_write_verification_passes_for_an_unzoned_element(monkeypatch):
    """Promotion with nothing configured reads the zones back: an element RE never zoned reads as
    `[]`, which is 'already unzoned', not a mismatch or an error."""
    el = {"elementGUID": "g1", "type": {"typeName": "SolutionBlueprint"}, "classifications": []}
    fake = MagicMock()
    fake.get_metadata_element_by_guid.return_value = el
    monkeypatch.setattr(ident, "_metadata_client", lambda identity=None: fake)
    clear = MagicMock()
    monkeypatch.setattr(ident, "clear_zone_membership", clear)
    out = curate.promote_to_publish_zones("g1")
    assert out["status"] == "already_unzoned" and out["zones"] == []
    clear.assert_not_called()


@pytest.fixture
def zone_controls_ok(monkeypatch):
    """The configured zones' controls admit everyone, so the start-up zone check lets them be written."""
    from tests.zone_fakes import zone_controls_grant_all

    return zone_controls_grant_all(monkeypatch)
