"""Retention basis: the one list, and the one reading of a stored retention field.

Egeria's `retentionBasis` is an enum sent as an ordinal. The ordinals and display names were
read from this platform's retentionBasis valid values (2026-10-08, PR/CI, read-only) and match
Dr.Egeria's processors.py.

Stored shape (inside the enrichment JSON, no DDL): `value` is the enum NAME, `note` is free text.
A value written before this existed is free text ('Ongoing'). Owner ruling 2026-10-08: it is read
as PROJECT_LIFETIME (2) and its text becomes the note. Done at READ time by `resolve`; nothing is
rewritten. The page mirrors this file in static/next/retention-basis.js (a test pins them equal).
"""
from __future__ import annotations

import re

#: (enum name, label as Egeria spells it, ordinal, hint -- hover text only)
RETENTION_BASES: tuple[tuple[str, str, int, str], ...] = (
    ("UNCLASSIFIED", "Unclassified", 0, "no assessment of the retention requirements"),
    ("TEMPORARY", "Temporary", 1, "temporary, no formal retention requirements"),
    ("PROJECT_LIFETIME", "Project Lifetime", 2, "needed for the lifetime of the referenced project"),
    ("TEAM_LIFETIME", "Team Lifetime", 3, "needed for the lifetime of the referenced team"),
    ("CONTRACT_LIFETIME", "Contract Lifetime", 4, "needed for the lifetime of the referenced contract"),
    ("REGULATED_LIFETIME", "Regulated Lifetime", 5, "defined by the referenced regulation"),
    ("TIMEBOXED_LIFETIME", "Time Boxed Lifetime", 6, "needed for the specified time"),
    ("OTHER", "Other", 99, "another basis"),
)
ORDINALS = {n: o for n, _l, o, _h in RETENTION_BASES}
LABELS = {n: l for n, l, _o, _h in RETENTION_BASES}
LEGACY_BASIS = "PROJECT_LIFETIME"


def ordinal(name: str) -> int:
    """The wire ordinal for an enum name; an unknown name is refused, never guessed."""
    try:
        return ORDINALS[name]
    except KeyError:
        raise ValueError(f"not a retention basis: {name!r}") from None


def _key(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(text).lower())


_BY_KEY = {**{_key(n): n for n, _l, _o, _h in RETENTION_BASES}, **{_key(l): n for n, l, _o, _h in RETENTION_BASES}}
_BY_ORDINAL = {str(o): n for n, _l, o, _h in RETENTION_BASES}


def match_existing(value: str) -> str:
    """The basis name an old stored value already says, or ''. Matches the enum name, Egeria's label or
    the ordinal, trimmed and case-insensitively ('temporary', 'Time Boxed Lifetime', '1')."""
    value = str(value or "").strip()
    return _BY_ORDINAL.get(value) or _BY_KEY.get(_key(value), "") if value else ""


def resolve(field: dict | None) -> dict:
    """{basis, note, carried} for a stored retention field.

    Exact enum name -> new shape. Otherwise an old value: if it names a basis (name, label or ordinal,
    any case) that basis is used; any other free text is read as PROJECT_LIFETIME (owner ruling). Either
    way the text is kept as the note and carried is True. Nothing stored -> basis '' (never a default)."""
    field = field or {}
    value = str(field.get("value") or "").strip()
    note = str(field.get("note") or "").strip()
    if value in ORDINALS:
        return {"basis": value, "note": note, "carried": False}
    if value:
        return {"basis": match_existing(value) or LEGACY_BASIS, "note": value, "carried": True}
    return {"basis": "", "note": note, "carried": False}
