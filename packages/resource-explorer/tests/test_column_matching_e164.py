"""TR-75: the `e164_phone` value pattern matches phone numbers, never dates, IPs or plain numbers.

The old pattern's `+` was optional, so any 8–22 character run of digits and separators fullmatched: ISO dates, IPv4
addresses, NAICS codes read as floats. The pattern library is shared by the DB surveys and the file class match.
"""
from __future__ import annotations

import pytest

from resource_explorer.surveyors.database.column_matching import VALUE_PATTERNS, detect_patterns, pattern_conformance

E164 = next(p for p in VALUE_PATTERNS if p.name == "e164_phone")

PHONES = ["+441234567890", "+1 (415) 555-0123", "+44 20 7946 0958", "+33 1 23 45 67 89", "(415) 555-0123",
          "415-555-0123", "020 7946 0958"]
NOT_PHONES = ["2024-01-15", "2017-03-04 10:22", "10.0.0.1", "192.168.100.200", "541511", "541511.0", "20240115",
              "12345678.90", "2022", "1-2-3-4-5-6-7-8"]


@pytest.mark.parametrize("value", PHONES)
def test_a_phone_number_matches(value):
    assert pattern_conformance([value], E164.regex) == 1.0


@pytest.mark.parametrize("value", NOT_PHONES)
def test_a_date_an_ip_or_a_plain_number_does_not_match(value):
    assert pattern_conformance([value], E164.regex) == 0.0


def _names(values):
    return {p.name for p, _ in detect_patterns(values)}


def test_a_column_of_iso_dates_is_a_date_not_a_phone():
    names = _names(["2024-01-15", "1987-06-30", "2017-03-04", "2022-12-31"])
    assert "iso_date" in names and "e164_phone" not in names


def test_a_column_of_ipv4_addresses_is_an_ip_not_a_phone():
    names = _names(["10.0.0.1", "192.168.100.200", "172.16.4.20", "8.8.8.8"])
    assert "ipv4" in names and "e164_phone" not in names


def test_a_column_of_international_numbers_is_a_phone():
    # long ungrouped digits may also be card-shaped: several matches are information (detect_patterns' docstring)
    assert "e164_phone" in _names(["+441234567890", "+442079460958", "+14155550123", "+33123456789"])
