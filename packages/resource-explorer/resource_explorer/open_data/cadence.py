"""The update cadence a publisher states (DCAT `accrualPeriodicity`), read as the days between updates.

Three forms occur: an ISO 8601 duration ("P1M", "R/P1M", the repeating-interval form), a Dublin Core Collection Description Frequency
IRI ("http://purl.org/cld/freq/monthly"), and a plain word ("Monthly"). A month is a twelfth of 365 days, rounded (30; a quarter 91), a year 365: a cadence is read
to the day, because the modified dates it is compared with are. A stated cadence that is not an interval (irregular, continuous, as
needed, unknown, or a word RE does not know) is kept as stated, with `days` None: stated, and not judged.
"""
from __future__ import annotations

import re
from typing import NamedTuple

from resource_explorer.open_data.absence import NOT_REPORTED


class Cadence(NamedTuple):
    days: float | None      # days between updates; None when the stated cadence is not an interval
    label: str              # the cadence in words
    raw: str                # as the publisher stated it


_ISO = re.compile(r"^(?:R\d*/)?P(?:(\d+)Y)?(?:(\d+)M)?(?:(\d+)W)?(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?)?$")
_IRI = re.compile(r"^https?://purl\.org/cld/freq/([A-Za-z]+)/?$", re.IGNORECASE)

_WORDS: dict[str, float | None] = {
    "daily": 1, "weekly": 7, "biweekly": 14, "bi-weekly": 14, "fortnightly": 14, "monthly": 30, "bimonthly": 61, "quarterly": 91,
    "semiannual": 182, "semiannually": 182, "biannual": 182, "semi-annual": 182, "annual": 365, "annually": 365, "yearly": 365,
    "continuous": None, "continuously": None, "irregular": None, "as needed": None, "asneeded": None, "unknown": None,
}


def parse_cadence(raw) -> Cadence | None:
    """The stated cadence, or None when the publisher stated none (not a string, empty, or not reported)."""
    if not isinstance(raw, str) or not raw.strip() or raw == NOT_REPORTED:
        return None
    text = raw.strip()
    iri = _IRI.match(text)
    word = (iri.group(1) if iri else text).lower()
    if word in _WORDS:
        days = _WORDS[word]
        return Cadence(float(days) if days is not None else None, word, raw)
    m = _ISO.match(text.upper())
    if m and any(m.groups()):
        years, months, weeks, days, hours, minutes = (int(g) if g else 0 for g in m.groups())
        total = years * 365 + round(months * 365 / 12) + weeks * 7 + days + hours / 24 + minutes / 1440
        if total > 0:
            return Cadence(float(total), f"every {total:g} days", raw)
    return Cadence(None, text, raw)
