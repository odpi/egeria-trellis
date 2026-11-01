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


# Licence IRIs (DCAT-AP's `dct:license`) -> the ids above. Keys are the IRI lower-cased, without scheme, "www."
# or a trailing slash, so http/https and trailing-slash spellings meet. An IRI not here is NOT mapped: the caller
# keeps the IRI as the licence id and the flags stay `NOT_REPORTED` (a flag is never guessed from an IRI's look).
_IRIS = {
    "creativecommons.org/licenses/by/4.0": "cc-by-4.0",
    "creativecommons.org/licenses/by/3.0": "cc-by-3.0",
    "creativecommons.org/licenses/by-sa/4.0": "cc-by-sa-4.0",
    "creativecommons.org/licenses/by-sa/3.0": "cc-by-sa-3.0",
    "creativecommons.org/licenses/by-nc/4.0": "cc-by-nc-4.0",
    "creativecommons.org/licenses/by-nc-sa/4.0": "cc-by-nc-sa-4.0",
    "creativecommons.org/publicdomain/zero/1.0": "cc0-1.0",
    "opendatacommons.org/licenses/odbl": "odc-odbl",
    "opendatacommons.org/licenses/odbl/1.0": "odc-odbl",
    "opendatacommons.org/licenses/odbl/1-0": "odc-odbl",
    "opendatacommons.org/licenses/by": "odc-by",
    "opendatacommons.org/licenses/by/1.0": "odc-by",
    "opendatacommons.org/licenses/pddl": "odc-pddl",
    "opendatacommons.org/licenses/pddl/1.0": "odc-pddl",
    "nationalarchives.gov.uk/doc/open-government-licence/version/3": "uk-ogl",
    "nationalarchives.gov.uk/doc/open-government-licence": "uk-ogl",
    # the EU Publications Office licence authority list (data.europa.eu's own vocabulary)
    "publications.europa.eu/resource/authority/licence/cc_by_4_0": "cc-by-4.0",
    "publications.europa.eu/resource/authority/licence/cc_by_sa_4_0": "cc-by-sa-4.0",
    "publications.europa.eu/resource/authority/licence/cc_by_nc_4_0": "cc-by-nc-4.0",
    "publications.europa.eu/resource/authority/licence/cc0_1_0": "cc0-1.0",
    "publications.europa.eu/resource/authority/licence/odc_by": "odc-by",
    "publications.europa.eu/resource/authority/licence/odc_odbl": "odc-odbl",
    "publications.europa.eu/resource/authority/licence/pddl_1_0": "odc-pddl",
    "publications.europa.eu/resource/authority/licence/ogl_uk_3_0": "uk-ogl",
}


def _iri_key(iri: str) -> str:
    text = iri.strip().lower()
    for prefix in ("https://", "http://"):
        if text.startswith(prefix):
            text = text[len(prefix):]
            break
    if text.startswith("www."):
        text = text[4:]
    for tail in ("/legalcode", "/deed.en", "/deed"):
        if text.endswith(tail):
            text = text[: -len(tail)]
    return text.rstrip("/")


def licence_id_from_iri(raw) -> str:
    """The id `licence_flags` knows for a licence IRI (or an id already in the table), else `raw` unchanged:
    an IRI RE cannot map stays the IRI, and its flags are then `NOT_REPORTED`."""
    if not isinstance(raw, str) or not raw.strip():
        return raw
    text = raw.strip()
    if text.lower() in _FLAGS:
        return text.lower()
    return _IRIS.get(_iri_key(text), text)
