"""The identifier match, read-first, before an import.

For one candidate (a DOI first, else the portal-scoped dataset id) it asks three questions and writes nothing:

  (a) is it a registered `open_data` row?                       -> RE's own store
  (b) is it an Egeria external identifier?                      -> a typed unique-name READ on the healed catalog
  (c) with a DOI: what does DataCite say (publisher, relations) -> stored on the record for OD3

Fail-closed: an Egeria error is "could not check", never "not found". The import proceeds with that word on the
row; it is never silent and never a green "not in the catalog".
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

log = logging.getLogger(__name__)

ALREADY = "already_in_catalog"
NOT_HELD = "not_held"
COULD_NOT_CHECK = "could_not_check"
CHECK_WORDS = {ALREADY: "already in the catalog", NOT_HELD: "not in the catalog", COULD_NOT_CHECK: "could not check"}

#: The qualifiedName under which an Egeria element would carry this identifier as an external identifier. OD1 only
#: READS under it; OD4 (publish) settles what is written, and this one function is where the two must agree.
EXTERNAL_ID_QUALIFIED_NAME_PREFIX = "ExternalIdentifier::"


def external_identifier_qualified_name(identifier: str) -> str:
    return f"{EXTERNAL_ID_QUALIFIED_NAME_PREFIX}{identifier}"


class EgeriaIdentifierLookup:
    """`lookup(identifier) -> guid | None`; raises when Egeria could not answer. Connects lazily, once: a failure
    to connect is remembered, so 25 rows against a down Egeria cost one attempt, not 25 timeouts."""

    def __init__(self, connect=None):
        self._connect = connect
        self._expert = None
        self._failure: Exception | None = None

    def _expert_client(self):
        if self._failure is not None:
            raise self._failure
        if self._expert is None:
            try:
                if self._connect is not None:
                    self._expert = self._connect()
                else:
                    from resource_explorer.surveyors.survey_definition_reader import SurveyDefinitionReader

                    self._expert = SurveyDefinitionReader()._connect_metadata_expert()
            except Exception as exc:
                self._failure = exc
                raise
        return self._expert

    def __call__(self, identifier: str):
        from resource_explorer.egeria_lookup import guid_by_qualified_name

        return guid_by_qualified_name(self._expert_client(), external_identifier_qualified_name(identifier))


@dataclass
class MatchResult:
    identifier: str
    status: str = NOT_HELD
    where: str = ""                 # "open_data <slug>" or "Egeria <guid>"
    detail: str = ""                # why it could not be checked
    words: str = ""
    datacite: dict | None = None
    datacite_status: str = ""       # "" (no DOI) | "resolved" | "could not check"
    datacite_detail: str = ""

    def __post_init__(self):
        self.words = CHECK_WORDS[self.status]

    def as_dict(self) -> dict:
        label = self.words + (f" · {self.where}" if self.where else "")
        return {"identifier": self.identifier, "status": self.status, "words": label, "where": self.where,
                "detail": self.detail, "datacite_status": self.datacite_status,
                "datacite_detail": self.datacite_detail}


def match_identifier(store, identifier: str, *, egeria_lookup, doi: str | None = None, resolver=None) -> MatchResult:
    """One row's match. `egeria_lookup(identifier) -> guid | None` (raises when it could not answer);
    `resolver(doi) -> record` is the DataCite resolver's `resolve`."""
    out = MatchResult(identifier)
    if doi:
        try:
            out.datacite = resolver(doi) if resolver else None
            out.datacite_status = "resolved" if out.datacite else "could not check"
        except Exception as exc:
            out.datacite_status = "could not check"
            out.datacite_detail = f"{type(exc).__name__}: {exc}"[:200]
            log.info("open data: DataCite could not resolve %s: %s", doi, exc)

    held = store.dataset_by_identifier(identifier)
    if held:
        out.status, out.where = ALREADY, f"open_data {held['slug']}"
        out.words = CHECK_WORDS[ALREADY]
        return out

    try:
        guid = egeria_lookup(identifier)
    except Exception as exc:                 # fail closed: an error is never "not found"
        out.status = COULD_NOT_CHECK
        out.detail = f"{type(exc).__name__}: {exc}"[:200]
        out.words = CHECK_WORDS[COULD_NOT_CHECK]
        log.info("open data: Egeria could not check %s: %s", identifier, exc)
        return out
    if guid:
        out.status, out.where = ALREADY, f"Egeria {guid}"
    else:
        out.status = NOT_HELD
    out.words = CHECK_WORDS[out.status]
    return out
