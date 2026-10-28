"""The publish summary's governance sentence reports each part from its own recorded outcome."""
from resource_explorer.surveyors.egeria_publisher import governance_summary


def _o(own, zone, zones):
    return {"ownership": own, "zone_membership": zone, "zones": zones, "owner": "peterprofile"}


def test_ownership_set_and_no_draft_zone_is_not_a_warning():
    # The 2026-10-10 11:52 "Publish again": Ownership landed, no draft zone configured.
    text = governance_summary({"d4704529": _o(True, False, []), "cfdcd75f": _o(True, False, [])})
    assert "⚠" not in text and "NOT set" not in text
    assert "Ownership set on 2 element(s)" in text
    assert "zones left to Egeria" in text


def test_ownership_failure_is_reported_alone_per_element_count():
    text = governance_summary({"a": _o(False, False, []), "b": _o(True, False, [])})
    assert "⚠ Ownership NOT set on 1 of 2" in text
    assert "ZoneMembership" not in text.replace("zones left", "")
    assert "zones left to Egeria" in text


def test_zone_failure_only_when_a_zone_was_configured():
    text = governance_summary({"a": _o(True, False, ["draft"]), "b": _o(True, True, ["draft"])})
    assert "⚠ ZoneMembership NOT set on 1 of 2" in text
    assert "Ownership set on 2" in text and "⚠ Ownership" not in text


def test_all_set_has_no_warning_and_empty_is_silent():
    assert "⚠" not in governance_summary({"a": _o(True, True, ["draft"])})
    assert governance_summary({}) == ""
