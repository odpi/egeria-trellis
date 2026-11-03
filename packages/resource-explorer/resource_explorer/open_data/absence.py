"""The typed absence of an open-data field.

A portal that does not give a field is "not reported by this source": a fact about the source, not an empty
string and not a None read as absent. `NOT_REPORTED` is the one value an adapter returns for it, so a caller can
tell "the source said nothing" from "the source said the empty string" and from "RE did not look".
"""
from __future__ import annotations

from typing import Any

NOT_REPORTED = "not_reported"

#: What a screen or CSV shows for `NOT_REPORTED`.
NOT_REPORTED_WORDS = "not reported by this source"
#: The same, for a directory's candidate rows.
NOT_REPORTED_BY_DIRECTORY_WORDS = "not reported by this directory"


def is_reported(value: Any) -> bool:
    return value != NOT_REPORTED


def reported_or_absent(value: Any) -> Any:
    """`value` as the source stated it, or `NOT_REPORTED` when it did not state one.

    Absent = None, "" or whitespace only, an empty list/dict. `False` and `0` are statements, not absences."""
    if value is None:
        return NOT_REPORTED
    if isinstance(value, str) and not value.strip():
        return NOT_REPORTED
    if isinstance(value, (list, tuple, dict, set)) and not value:
        return NOT_REPORTED
    return value


def words(value: Any, *, directory: bool = False) -> Any:
    """`value`, or the absence words when it is `NOT_REPORTED` (for display only; never store the words)."""
    if value == NOT_REPORTED:
        return NOT_REPORTED_BY_DIRECTORY_WORDS if directory else NOT_REPORTED_WORDS
    return value
