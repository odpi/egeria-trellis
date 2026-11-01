"""Licence id -> the three flags, as the adapter reads them.

Only ids RE knows are mapped. An id not in the table, and a missing licence, give every flag `NOT_REPORTED`
(the licence is stated, or not, and what it permits is then not established): a flag is never guessed from a name.
Keys are the lower-cased ids CKAN, DataCite (SPDX) and Socrata-style portals use.
"""
from __future__ import annotations

from resource_explorer.open_data.absence import NOT_REPORTED

# attribution: must credit; share_alike: derivatives must carry the same licence; commercial: commercial use allowed
_FLAGS = {
    "cc-zero": (False, False, True), "cc0-1.0": (False, False, True), "cc0": (False, False, True),
    "odc-pddl": (False, False, True), "other-pd": (False, False, True),
    "cc-by": (True, False, True), "cc-by-4.0": (True, False, True), "cc-by-3.0": (True, False, True),
    "odc-by": (True, False, True), "uk-ogl": (True, False, True), "other-open": (True, False, True),
    "ca-ogl-lgo": (True, False, True),
    "cc-by-sa": (True, True, True), "cc-by-sa-4.0": (True, True, True), "cc-by-sa-3.0": (True, True, True),
    "odc-odbl": (True, True, True), "gfdl": (True, True, True),
    "cc-nc": (True, False, False), "cc-by-nc-4.0": (True, False, False), "cc-by-nc-sa-4.0": (True, True, False),
    "other-nc": (True, False, False),
}


def licence_flags(licence_id) -> dict:
    """{"attribution", "share_alike", "commercial"}: True/False as the table states, else `NOT_REPORTED`."""
    key = licence_id.strip().lower() if isinstance(licence_id, str) else ""
    row = _FLAGS.get(key)
    if row is None:
        return {"attribution": NOT_REPORTED, "share_alike": NOT_REPORTED, "commercial": NOT_REPORTED}
    return {"attribution": row[0], "share_alike": row[1], "commercial": row[2]}
