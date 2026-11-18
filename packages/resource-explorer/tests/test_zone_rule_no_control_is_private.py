"""RE follows Egeria's current zone rule: a zone with no security access control is private (2026-11-10).

BRIEF-ZONE-RULE-NO-CONTROL-IS-PRIVATE, owner's decisions D1 and D2 ("Approve as recommended"). Egeria e9fd817f4c
("Make governance zones without a security control private"), in b49c5ed0 (the pinned quickstart image 242abf68),
changed `OpenMetadataAccessSecurityConnector.validateZoneAccess`:

    "Every named zone is secured.  A zone with no security access control, or whose control has neither a list
     for this operation nor a DEFAULT list, grants access to no-one."            (:1156-1161)

One test per connector line of `validateZoneAccess` at b49c5ed0 (:1118-1199), the `RE_ZONE_RULE=legacy` switch
for an older platform, the refusal wording (never naming a person's zone), and `feedback_routing._zone_restriction`.
"""
from __future__ import annotations

import pytest

from resource_explorer import zone_access as za
from resource_explorer.zone_access import zone_grants

USER, OTHER = "garygeeke", "peterprofile"
AVAILABLE = {"userAccountStatus": "AVAILABLE", "userAccountType": "EMPLOYEE", "securityGroups": [], "securityRoles": []}


@pytest.fixture(autouse=True)
def _current_rule(monkeypatch):
    monkeypatch.delenv(za.ZONE_RULE_ENV, raising=False)
    # RE's own configured zones (named in refusals); a test that needs some sets them.
    monkeypatch.setattr(za, "_re_configured_zones", lambda: set())


def _grants(zones, controls=None, *, account=AVAILABLE, owners=None, maintainers=None, op=za.UPDATE_OPERATION):
    controls = controls or {}
    return zone_grants(USER, zones, control_for=controls.get, account_for=lambda: account, owners=owners,
                       maintainers=maintainers, operations=(op,))


def _ctl(**lists):
    return {"associatedSecurityList": lists}


class TestTheConnectorLineByLine:
    def test_no_zones_is_open(self):
        """:1196-1198 "If this point is reached, the user has access" (an element in no zone)."""
        assert _grants([]).allowed is True

    def test_the_users_own_zone_grants(self):
        """:1150-1153 `userId.equals(zoneName)` returns true: the user's personal zone, with no control."""
        v = _grants([USER])
        assert v.allowed is True and v.granted_by == f"zone {USER}"

    def test_a_zone_with_no_control_refuses(self):
        """:1156-1161 the new case: no control grants no one."""
        v = _grants(["nocontrol"])
        assert v.allowed is False and v.secured == ["nocontrol"] and v.uncontrolled == ["nocontrol"]
        assert v.unlisted == []

    def test_a_control_without_the_operations_list_or_default_refuses(self):
        """:1156-1161 "whose control has neither a list for this operation nor a DEFAULT list"."""
        v = _grants(["z"], {"z": _ctl(READ=["allUsers"])})
        assert v.allowed is False and v.uncontrolled == ["z"] and v.unlisted == ["z"]

    def test_a_control_without_read_but_with_default_uses_default(self):
        """`getAssociatedSecurityListForZone` :935-980: the operation's list, else DEFAULT."""
        assert _grants(["z"], {"z": _ctl(DEFAULT=["allUsers"])}, op=za.READ_OPERATION).allowed is True

    def test_all_users(self):
        """`isAccountInSecurityList` (:1167): `allUsers`."""
        assert _grants(["z"], {"z": _ctl(DEFAULT=["allUsers"])}).allowed is True

    def test_the_account_type_group(self):
        """`isAccountInSecurityList`: EMPLOYEE => employeeUsers."""
        assert _grants(["z"], {"z": _ctl(DEFAULT=["employeeUsers"])}).allowed is True
        assert _grants(["z"], {"z": _ctl(DEFAULT=["contractUsers"])}).allowed is False

    def test_a_security_group(self):
        assert _grants(["z"], {"z": _ctl(DEFAULT=["sales"])},
                       account={**AVAILABLE, "securityGroups": ["sales"]}).allowed is True

    def test_a_role(self):
        assert _grants(["z"], {"z": _ctl(DEFAULT=["steward"])},
                       account={**AVAILABLE, "securityRoles": ["steward"]}).allowed is True

    def test_instance_owner(self):
        """:1172-1184 the instance groups (`getInstanceBasedGroups` :1379, `isUserAnOwner` :1421)."""
        assert _grants(["z"], {"z": _ctl(DEFAULT=["instanceOwner"])}, owners=[USER]).allowed is True
        assert _grants(["z"], {"z": _ctl(DEFAULT=["instanceOwner"])}, owners=[OTHER]).allowed is False

    def test_existing_and_new_maintainer(self):
        assert _grants(["z"], {"z": _ctl(DEFAULT=["existingMaintainer"])}, owners=[OTHER],
                       maintainers=[USER]).allowed is True
        assert _grants(["z"], {"z": _ctl(DEFAULT=["newMaintainer"])}, owners=[OTHER],
                       maintainers=[OTHER]).allowed is True

    def test_two_zones_one_granting_is_enough(self):
        """The loop returns true on the first zone that grants; an uncontrolled zone beside it does not refuse."""
        v = _grants(["nocontrol", "z"], {"z": _ctl(DEFAULT=["allUsers"])})
        assert v.allowed is True

    def test_two_zones_none_granting_refuses(self):
        """:1188-1191 any zone seen and none granted => denied."""
        v = _grants(["nocontrol", "z"], {"z": _ctl(DEFAULT=["dbas"])})
        assert v.allowed is False and v.secured == ["nocontrol", "z"]

    def test_an_unknown_account_is_refused_before_any_zone(self):
        """:1131 `getActiveUserAccount` first."""
        v = zone_grants(USER, ["z"], control_for={}.get, account_for=lambda: None)
        assert v.allowed is False and "no account" in v.why


class TestTheLegacySwitch:
    def test_legacy_ignores_a_zone_with_no_control(self, monkeypatch):
        """`RE_ZONE_RULE=legacy`: the connector before e9fd817f4c (af400039c2 :1130-1134), for an older platform."""
        monkeypatch.setenv(za.ZONE_RULE_ENV, "legacy")
        assert za.zone_rule() == za.ZONE_RULE_LEGACY
        assert _grants(["nocontrol"]).allowed is True
        assert _grants(["nocontrol", "z"], {"z": _ctl(DEFAULT=["dbas"])}).allowed is False

    @pytest.mark.parametrize("value", ["", "current", "LEGACYISH", "open"])
    def test_anything_else_is_the_current_rule(self, monkeypatch, value):
        monkeypatch.setenv(za.ZONE_RULE_ENV, value)
        assert za.zone_rule() == za.ZONE_RULE_CURRENT
        assert _grants(["nocontrol"]).allowed is False

    def test_legacy_spelled_in_capitals(self, monkeypatch):
        monkeypatch.setenv(za.ZONE_RULE_ENV, " Legacy ")
        assert _grants(["nocontrol"]).allowed is True


class TestTheRefusalWords:
    def test_a_configured_zone_with_no_control_is_named(self, monkeypatch):
        monkeypatch.setattr(za, "_re_configured_zones", lambda: {"zone-a"})
        v = _grants(["zone-a"])
        assert v.why == ("zone zone-a has no security access control, so only its own user may access it "
                         f"({USER} may not update it in Egeria)")

    def test_a_control_without_the_list_says_so(self, monkeypatch):
        v = _grants(["z"], {"z": _ctl(READ=["allUsers"])})
        assert v.why == (f"the control of zone z lists neither UPDATE_PROPERTIES nor DEFAULT, so only its own user "
                         f"may update it ({USER} may not update it in Egeria)")

    def test_a_personal_zone_with_no_control_is_never_named(self):
        """A private element is zoned [private zone, <owner's userId>]: the owner's zone has no control, and its
        name says whose element it is. Under the old rule it was skipped, so it never appeared in a refusal."""
        v = _grants(["private-zone", OTHER], {"private-zone": _ctl(DEFAULT=["nobody"])})
        assert v.allowed is False and OTHER in v.person_zones and OTHER not in v.why

    def test_an_owners_zone_is_withheld_even_with_a_control(self):
        v = _grants(["z", OTHER], {"z": _ctl(DEFAULT=["dbas"]), OTHER: _ctl(DEFAULT=["dbas"])}, owners=[OTHER])
        assert v.person_zones == [OTHER]


class TestTheCurateSentence:
    """`workflows.curate.curation_access` turns a refusal into the sentence a person reads."""

    @pytest.fixture
    def egeria(self, monkeypatch):
        from tests.zone_fakes import install, signed_in

        fake = install(monkeypatch)
        token = signed_in(USER)
        yield fake
        from resource_explorer.a2a_auth import current_caller
        current_caller.reset(token)

    def _decide(self, guid):
        from resource_explorer.workflows.curate import curation_access

        return curation_access(None, "repo", "p", "x", element_guid=guid)

    def test_an_element_in_an_uncontrolled_foreign_zone_is_refused_without_naming_it(self, egeria):
        from tests.zone_fakes import element

        egeria.elements["g"] = element("g", [OTHER])
        d = self._decide("g")
        assert d.allowed is False and OTHER not in d.reason
        assert d.reason == f"private to its owner: {USER} may not update it in Egeria"

    def test_an_element_in_an_uncontrolled_configured_zone_names_it(self, egeria, monkeypatch):
        from tests.zone_fakes import element

        monkeypatch.setattr(za, "_re_configured_zones", lambda: {"zone-a"})
        egeria.elements["g"] = element("g", ["zone-a"])
        d = self._decide("g")
        assert d.allowed is False and d.reason.startswith("zone zone-a has no security access control")

    def test_the_users_own_zone_still_grants(self, egeria):
        from tests.zone_fakes import element

        egeria.elements["g"] = element("g", [USER])
        assert self._decide("g").allowed is True

    def test_no_zones_still_open(self, egeria):
        assert self._decide("g").allowed is True


class TestZoneRestriction:
    """`feedback_routing._zone_restriction` (D2): public only with no zones or a zone whose READ/DEFAULT list
    holds allUsers; otherwise restricted; fails closed."""

    def _call(self, monkeypatch, zones, controls=None, *, owners=None, error=None):
        from resource_explorer import feedback_routing

        controls = controls or {}

        class Reader:
            def __init__(self, **kw):
                pass

            def element(self, guid):
                if error:
                    raise error
                return za.ElementFacts(zones=zones, owners=owners, maintainers=None)

            def control(self, zone):
                if isinstance(controls.get(zone), Exception):
                    raise controls[zone]
                return controls.get(zone)

        monkeypatch.setattr(za, "EgeriaAccessReader", Reader)

        class Repo:
            egeria_asset_guid = "g-1"

        class R:
            def get(self, slug):
                return Repo()
            get_database = get_filesystem = get

        # The module's autouse stub in test_feedback_routing does not apply here; call the function itself.
        return feedback_routing._zone_restriction("repo", "r1", R())

    def test_no_zones_is_public(self, monkeypatch):
        assert self._call(monkeypatch, []) == ""

    def test_an_uncontrolled_zone_is_restricted(self, monkeypatch):
        monkeypatch.setattr(za, "_re_configured_zones", lambda: {"zone-a"})
        assert self._call(monkeypatch, ["zone-a"]) == "the resource is in a restricted zone (zone-a)"

    def test_a_personal_zone_is_restricted_and_not_named(self, monkeypatch):
        got = self._call(monkeypatch, [OTHER])
        assert got == "the resource is in a zone private to its owner" and OTHER not in got

    def test_a_read_all_users_zone_is_public(self, monkeypatch):
        assert self._call(monkeypatch, ["z"], {"z": _ctl(READ=["allUsers"])}) == ""

    def test_a_default_all_users_zone_is_public(self, monkeypatch):
        assert self._call(monkeypatch, ["z"], {"z": _ctl(DEFAULT=["allUsers"])}) == ""

    def test_one_public_zone_is_enough(self, monkeypatch):
        assert self._call(monkeypatch, [OTHER, "z"], {"z": _ctl(READ=["allUsers"])}) == ""

    def test_a_read_list_of_roles_only_is_restricted(self, monkeypatch):
        got = self._call(monkeypatch, ["z"], {"z": _ctl(READ=["steward"], DEFAULT=["allUsers"])})
        assert got == "the resource is in a restricted zone (z)"

    def test_an_owners_zone_is_not_named(self, monkeypatch):
        got = self._call(monkeypatch, ["z", OTHER], {"z": _ctl(READ=["dbas"]), OTHER: _ctl(READ=["dbas"])},
                         owners=[OTHER])
        assert got == "the resource is in a restricted zone (z and a zone private to its owner)"

    def test_a_read_error_is_restricted(self, monkeypatch):
        got = self._call(monkeypatch, ["z"], error=za.AccessUnreadable("no answer"))
        assert "could not be read" in got

    def test_a_control_read_error_is_restricted(self, monkeypatch):
        got = self._call(monkeypatch, ["z"], {"z": za.AccessUnreadable("secrets store")})
        assert "could not be read" in got

    def test_legacy_does_not_loosen_it(self, monkeypatch):
        """D2 is deliberately stricter than the connector, so it is not gated by RE_ZONE_RULE."""
        monkeypatch.setenv(za.ZONE_RULE_ENV, "legacy")
        monkeypatch.setattr(za, "_re_configured_zones", lambda: {"zone-a"})
        assert self._call(monkeypatch, ["zone-a"]) != ""
