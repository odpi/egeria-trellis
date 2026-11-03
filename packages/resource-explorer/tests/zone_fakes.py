"""A fake Egeria at the curation-access client boundary (Brief Z), shared by the tests that reach the check.

`zone_access` builds its two pyegeria clients through `_metadata_expert()` and `_security_officer()` and
resolves the platform through `_platform()`; these fakes replace exactly those, so everything of RE's own
(the decision, the emulated zone loop, the publish workflow) runs for real.
"""
from __future__ import annotations

AVAILABLE = {"userAccountStatus": "AVAILABLE", "userAccountType": "EMPLOYEE", "securityGroups": [], "securityRoles": []}


def element(guid: str, zones=(), owners=None, anchor: str = "") -> dict:
    """A raw metadata element in the live shape, with Egeria's array encoding for zones and owners, and (Brief Z2
    round 2) an `Anchors` classification naming `anchor` when given."""
    classes = []
    if anchor:
        classes.append({"classificationName": "Anchors", "classificationProperties": {"propertyValueMap": {
            "anchorGUID": {"primitiveValue": anchor}}}})
    if zones:
        classes.append({"classificationName": "ZoneMembership", "classificationProperties": {"propertyValueMap": {
            "zoneMembership": {"arrayValues": {"propertiesAsStrings": {str(i): z for i, z in enumerate(zones)}}}}}})
    if owners is not None:
        classes.append({"classificationName": "Ownership", "classificationProperties": {"propertyValueMap": {
            "userIds": {"arrayValues": {"propertiesAsStrings": {str(i): o for i, o in enumerate(owners)}}}}}})
    return {"elementGUID": guid, "classifications": classes}


class FakeEgeria:
    """The two pyegeria clients an access check builds, in one recording fake. Every account is AVAILABLE
    with no groups unless `accounts` says otherwise (None = Egeria has no such account)."""

    def __init__(self):
        self.elements: dict[str, dict] = {}
        self.controls: dict[str, dict] = {}
        self.accounts: dict[str, dict | None] = {}
        self.fail_control = ""
        self.fail_element = ""
        self.calls: list[tuple] = []

    def get_metadata_element_by_guid(self, guid, **kw):
        self.calls.append(("element", guid))
        if guid == self.fail_element:
            raise ConnectionError("platform unreachable")
        return self.elements.get(guid) or element(guid)

    def get_security_access_control(self, platform, zone, **kw):
        self.calls.append(("control", zone))
        if zone == self.fail_control:
            raise ConnectionError("secrets store unreadable")
        return self.controls.get(zone)

    def get_user_account(self, platform, user_id, **kw):
        self.calls.append(("account", user_id))
        return self.accounts.get(user_id, AVAILABLE)


def install(monkeypatch, *, zones=()) -> FakeEgeria:
    """Fake the client boundary; `zones` are the publish zones RE is configured with (default none)."""
    fake = FakeEgeria()
    monkeypatch.setattr("resource_explorer.zone_access._metadata_expert", lambda: fake)
    monkeypatch.setattr("resource_explorer.zone_access._security_officer", lambda: fake)
    monkeypatch.setattr("resource_explorer.zone_access._platform", lambda: ("Quickstart platform", "plat-guid"))
    monkeypatch.delenv("EXPLORER_DRAFT_ZONE", raising=False)
    monkeypatch.setattr("resource_explorer.egeria_identity.configured_publish_zones", lambda: list(zones))
    return fake


def signed_in(user_id: str, role: str = "user", source: str = "app-jwt"):
    """Set the caller ContextVar; returns the reset token."""
    from resource_explorer.a2a_auth import CallerIdentity, current_caller

    return current_caller.set(CallerIdentity(user_id=user_id, egeria_token="t", auth_source=source, role=role))


def permit_publish_routes(monkeypatch, user_id: str = "publisher") -> FakeEgeria:
    """For tests of a publish route that are about something else: no zone in use (every element answers with
    no ZoneMembership) and a signed-in caller as the curation check sees it (`workflows.curate._caller_identity`
    only; the route's own sign-in handling is untouched)."""
    from resource_explorer.a2a_auth import CallerIdentity

    fake = install(monkeypatch)
    monkeypatch.setattr("resource_explorer.workflows.curate._caller_identity",
                        lambda: CallerIdentity(user_id=user_id, egeria_token="t", auth_source="app-jwt"))
    return fake


def open_access(monkeypatch, *, caller_if_none: str = "") -> FakeEgeria:
    """For tests of a write path that are about something else (Brief Z2): the access reads are faked at the
    client boundary and every element answers with no ZoneMembership, so the check runs and permits. RE's own
    zone configuration is left as the test set it (a configured zone with no access control is not secured).
    `caller_if_none`: when the test publishes no caller identity of its own (a route test that patches only the
    route's author lookup), the curation check sees this signed-in user; a caller the test did set is kept."""
    from resource_explorer.a2a_auth import CallerIdentity

    fake = FakeEgeria()
    monkeypatch.setattr("resource_explorer.zone_access._metadata_expert", lambda: fake)
    monkeypatch.setattr("resource_explorer.zone_access._security_officer", lambda: fake)
    monkeypatch.setattr("resource_explorer.zone_access._platform", lambda: ("Quickstart platform", "plat-guid"))
    if caller_if_none:
        from resource_explorer.workflows import curate as wc

        real = wc._caller_identity
        stand_in = CallerIdentity(user_id=caller_if_none, egeria_token="t", auth_source="app-jwt")
        monkeypatch.setattr(wc, "_caller_identity", lambda: real() or stand_in)
    return fake


#: A security access control whose DEFAULT list admits every user: what a configured zone needs so that Egeria
#: e9fd817f4c (every named zone secured; no control grants nobody) still lets everyone in. RE's start-up zone check
#: (`egeria_identity.zone_lockout_refusal`) refuses to publish into a configured zone without one.
GRANT_ALL_CONTROL = {"associatedSecurityList": {"DEFAULT": ["allUsers"]}}


def grant_all(*zones: str) -> dict:
    """`{zone: GRANT_ALL_CONTROL}` for `FakeEgeria.controls`."""
    return {z: GRANT_ALL_CONTROL for z in zones}


def zone_controls_grant_all(monkeypatch):
    """For tests of a write into a configured zone that are about something else: every zone's control is read
    (as RE reads it, `zone_access.EgeriaAccessReader.control`) from an autospec of the real pyegeria
    `SecurityOfficer` answering `GRANT_ALL_CONTROL`, so the start-up zone check passes."""
    from unittest.mock import create_autospec

    from pyegeria.omvs.security_officer import SecurityOfficer

    so = create_autospec(SecurityOfficer, instance=True, spec_set=True)
    so.get_security_access_control.return_value = GRANT_ALL_CONTROL
    monkeypatch.setattr("resource_explorer.zone_access._security_officer", lambda: so)
    monkeypatch.setattr("resource_explorer.zone_access._platform", lambda: ("Quickstart platform", "plat-guid"))
    return so


#: pyegeria's real by-name miss.
NO_ELEMENTS_FOUND = "No elements found"


def real_lookup(found: dict | None = None):
    """The REAL `EgeriaPublisher._find_element_guid`, over an autospec'd pyegeria `MetadataExpert` (bound to the
    real signature) answering the unique-name read: pyegeria's real miss ("No elements found") for every name, or
    the element `found` maps it to.

    For tests that hand the outbox drain its clients: never a stand-in that answers "" or None, since a look-up that
    could not be made must not read as "absent"."""
    from unittest.mock import create_autospec

    from pyegeria import MetadataExpert

    from resource_explorer.surveyors.egeria_publisher import EgeriaPublisher
    from tests.qn_lookup_bridge import element_answer

    expert = create_autospec(MetadataExpert, instance=True, spec_set=True)
    expert.get_metadata_element_by_unique_name.side_effect = (
        lambda name=None, property_name="qualifiedName", body=None, **k: (
            element_answer(found[name]) if name in (found or {}) else NO_ELEMENTS_FOUND))
    publisher = EgeriaPublisher()
    publisher._metadata_expert = expert
    return publisher._find_element_guid
