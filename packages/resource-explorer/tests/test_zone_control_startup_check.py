"""The start-up check over every zone RE stamps (design brief, 2026-10-14).

Egeria e9fd817f4c ("Make governance zones without a security control private") makes every named zone
secured: a zone with no SecurityAccessControl, or whose control has neither a list for the operation nor a
DEFAULT list, grants nobody. The owner-userId zone stays exempt (`userId.equals(zoneName)` grants first).

So at start-up RE reads the control of each zone it will stamp (the private zone, plus a configured draft or
publish zone) and records three facts per zone: the control present / absent, the operations it lists, and
whether a DEFAULT list exists. A zone that would lock everyone out blocks publishing into it, with those
words; a READ-only control warns that writes would be denied; an unreadable control is "could not check",
never "fine".

Only the pyegeria client boundary is faked: `create_autospec(SecurityOfficer, spec_set=True)`, so every call
binds against the real signature. RE's own reader (`zone_access.EgeriaAccessReader.control`), the
assessment, the report and the gates run for real.
"""
from __future__ import annotations

from unittest.mock import create_autospec

import pytest

PUBLISH_ZONE = "coco-public"


@pytest.fixture
def officer(monkeypatch):
    """The Security Officer client RE reads controls with, as an autospec of the real pyegeria class."""
    from pyegeria.omvs.security_officer import SecurityOfficer

    from resource_explorer import egeria_identity as ident

    so = create_autospec(SecurityOfficer, spec_set=True, instance=True)
    monkeypatch.setattr("resource_explorer.zone_access._security_officer", lambda: so)
    monkeypatch.setattr("resource_explorer.zone_access._platform", lambda: ("Quickstart platform", "plat-guid"))
    monkeypatch.setenv("EXPLORER_PUBLISH_ZONES", PUBLISH_ZONE)
    monkeypatch.delenv("EXPLORER_DRAFT_ZONE", raising=False)
    monkeypatch.setattr(ident, "_zone_controls_state", None)
    monkeypatch.setattr(ident, "_private_zone_state", None)
    return so


def _entry(report: list[dict], zone: str) -> dict:
    return next(e for e in report if e["zone"] == zone)


def test_control_with_a_default_list_is_ok(officer):
    from resource_explorer import egeria_identity as ident

    officer.get_security_access_control.return_value = {
        "controlName": PUBLISH_ZONE, "associatedSecurityList": {"DEFAULT": ["allUsers"], "DELETE": ["admins"]}}

    report = ident.check_zone_controls()
    e = _entry(report, PUBLISH_ZONE)
    assert (e["control"], e["operations"], e["default_list"], e["verdict"]) == ("present", ["DELETE"], True, "ok")
    assert ident.zone_lockout_refusal([PUBLISH_ZONE]) == ""
    officer.get_security_access_control.assert_called_with("Quickstart platform", PUBLISH_ZONE,
                                                           platform_guid="plat-guid")
    assert f"{PUBLISH_ZONE} · control present · lists DELETE · DEFAULT yes · ok" in ident.zone_controls_line(report)


def test_absent_control_blocks_publishing_with_the_words(officer, monkeypatch):
    from resource_explorer import egeria_identity as ident
    from resource_explorer.workflows.curate import promote_to_publish_zones

    officer.get_security_access_control.return_value = None      # Egeria: no such control

    e = _entry(ident.check_zone_controls(), PUBLISH_ZONE)
    assert (e["control"], e["operations"], e["default_list"], e["verdict"]) == ("absent", [], False, "blocked")
    assert "would lock out everyone once Egeria e9fd817f4c ships" in e["words"]

    refusal = ident.zone_lockout_refusal([PUBLISH_ZONE])
    assert f"'{PUBLISH_ZONE}'" in refusal and "would lock out everyone once Egeria e9fd817f4c ships" in refusal

    # The accept path refuses BEFORE the zone write.
    writes = []
    monkeypatch.setattr(ident, "set_zone_membership", lambda *a, **k: writes.append(a) or True)
    monkeypatch.setattr(ident, "current_zones", lambda *a, **k: [])
    out = promote_to_publish_zones("guid-1")
    assert out["status"] == "blocked" and not writes
    assert "would lock out everyone once Egeria e9fd817f4c ships" in out["words"]


def test_private_zone_control_with_neither_read_nor_default_is_not_enforced(officer, monkeypatch):
    """The private zone goes through its own gate (`ensure_private_zone_exists` -> `enforced`), with the same
    words; RE never overwrites an operator's control, it reports it."""
    from resource_explorer import egeria_identity as ident

    monkeypatch.setattr(ident, "_resolve_platform", lambda: ("Quickstart platform", "plat-guid"))

    class _Clients:
        def of(self, cls):
            return officer
    monkeypatch.setattr("resource_explorer.egeria_clients.egeria_client", lambda *a, **k: _Clients())
    officer.get_security_access_control.return_value = {"associatedSecurityList": {"UPDATE_PROPERTIES": ["x"]}}

    state = ident.ensure_private_zone_exists()
    assert state["enforced"] is False and state["status"] == "locks_out"
    assert "would lock out everyone once Egeria e9fd817f4c ships" in state["detail"]
    officer.set_security_access_control.assert_not_called()
    e = _entry(ident.zone_controls_report(), ident.private_zone())
    assert (e["control"], e["operations"], e["default_list"], e["verdict"]) == (
        "present", ["UPDATE_PROPERTIES"], False, "blocked")


def test_read_only_control_warns_but_does_not_block(officer):
    from resource_explorer import egeria_identity as ident

    officer.get_security_access_control.return_value = {"associatedSecurityList": {"READ": ["allUsers"]}}

    e = _entry(ident.check_zone_controls(), PUBLISH_ZONE)
    assert (e["control"], e["operations"], e["default_list"], e["verdict"]) == ("present", ["READ"], False, "warned")
    assert "writes would be denied once Egeria e9fd817f4c ships" in e["words"]
    assert ident.zone_lockout_refusal([PUBLISH_ZONE]) == ""


def test_unreadable_control_is_could_not_check_and_blocks(officer):
    from resource_explorer import egeria_identity as ident

    officer.get_security_access_control.side_effect = ConnectionError("secrets store unreadable")

    report = ident.check_zone_controls()
    e = _entry(report, PUBLISH_ZONE)
    assert (e["control"], e["operations"], e["default_list"], e["verdict"]) == (
        "could not check", None, None, "could_not_check")
    assert "secrets store unreadable" in e["words"]
    assert "could not check" in ident.zone_lockout_refusal([PUBLISH_ZONE])
    assert f"{PUBLISH_ZONE} · could not check" in ident.zone_controls_line(report)


def test_the_owner_zone_and_unstamped_zones_are_never_read(officer):
    from resource_explorer import egeria_identity as ident

    assert ident.zone_lockout_refusal(["alice", "someone-elses-zone"]) == ""
    officer.get_security_access_control.assert_not_called()
