"""Make a MULTI-LINK Egeria relationship once: read the existing links first, link only when none joins
exactly these two elements, and never link when the read cannot be completed.

A multi-link type (`RelationshipDef.setMultiLink(true)` in the type archive) is never merged or refused:
every create call adds another relationship between the same two elements. Callers name the type, the
element to read from and the END it sits at, the element expected at the far end, and the create call.
Three outcomes matter:

  already linked   a link between exactly these two exists: no create call, its relationship GUID is
                   returned (the oldest by `versions.createTime` when duplicates are already there).
  linked           none exists: one create call, its relationship GUID returned.
  could not check  the read failed, gave an answer we cannot read, or could not reach the end of the list:
                   NO link. A missing link is recoverable on the next publish; a duplicate is the bug.

The read is `MetadataExpert.get_related_metadata_elements(read_guid, relationship_type, ...,
starting_at_end=read_end)`, where `read_end` is the end of the type at which `read_guid` sits. The live
answer is a dict with `elementList`; each entry carries `relationshipGUID`, `versions.createTime` and the
far end under `element` (or `relatedElement`) with a top-level `elementGUID`. Pick the end whose element
has fewer links of this type: every page of its links is read.

End of the list versus a filtered page. Egeria pages the RELATIONSHIPS and then drops those whose far end
fails validation (`MetadataElementHandler.getRelatedMetadataElements`, omf-metadata-server), so a page can
hold fewer entries than were paged, or none, before the true end. The two are told apart by the answer:

  * the end of the list: the repository returned no relationships, the handler returns null and the
    response carries NO `elementList` -> pyegeria's "No elements found". Live dev Egeria (2026-10-14,
    read-only) answered this at, and 1000 entries past, the end of a 4-link list, and for an element with
    none at all.
  * a filtered page: relationships were paged but none survived, the handler returns an EMPTY list ->
    pyegeria's "No elements returned". `RelationshipAccumulator.getRelationships` states the contract:
    "Null means no relationships were retrieved ...; Empty list means all retrieved relationships were
    filtered out and can retrieve again."

So "No elements returned" moves to the next page, and so does a short page. At most `MAX_FILTERED_RUN`
filtered pages in a row are read past; more than that is "could not check", as is reading `MAX_PAGES`
pages without reaching the end. The limit that remains: a link whose far end Egeria filters out for this
caller (not visible, not effective, a Memento without `for_lineage`) is not seen, so one more may be made.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass

log = logging.getLogger(__name__)

PAGE_SIZE = 500
#: A bound on pages read; reaching it means the list could not be read to its end.
MAX_PAGES = 200
#: A bound on consecutive filtered ("No elements returned") pages read past before giving up.
MAX_FILTERED_RUN = 10

LINKED = "linked"
ALREADY_LINKED = "already linked"
COULD_NOT_CHECK = "could not check"
LINK_FAILED = "link failed"

#: pyegeria's answer when the response carries no elementList: the end of the list.
_END_OF_LIST = "no elements found"
#: pyegeria's answer for an EMPTY elementList: Egeria filtered out every relationship on this page.
_FILTERED_PAGE = "no elements returned"


class Unreadable(Exception):
    """The existing links could not be read to the end."""


@dataclass(frozen=True)
class LinkOutcome:
    outcome: str
    relationship_guid: str = ""
    error: str = ""

    @property
    def linked(self) -> bool:
        """True when a link between the two exists now (made here or found)."""
        return self.outcome in (LINKED, ALREADY_LINKED)


def _entry_element_guid(entry: dict) -> str:
    far = entry.get("element") or entry.get("relatedElement") or {}
    if not isinstance(far, dict):
        return ""
    return str(far.get("elementGUID") or (far.get("elementHeader") or {}).get("guid") or "").strip()


_END = object()
_FILTERED = object()


def _page_entries(answer):
    """The entries of one page, `_END` at the end of the list, `_FILTERED` for a page Egeria emptied by
    filtering; raises `Unreadable` otherwise."""
    if isinstance(answer, str):
        said = answer.strip().rstrip(".").strip().lower()
        if said == _END_OF_LIST:
            return _END
        if said == _FILTERED_PAGE:
            return _FILTERED
        raise Unreadable(f"unexpected answer {answer!r:.120}")
    if isinstance(answer, dict):
        entries = answer.get("elementList")
        if entries is None:
            raise Unreadable(f"answer has no elementList (keys: {sorted(answer)[:8]})")
    elif isinstance(answer, list):
        entries = answer
    else:
        raise Unreadable(f"unexpected answer type {type(answer).__name__}")
    if not isinstance(entries, list):
        raise Unreadable("elementList is not a list")
    return entries or _FILTERED


def existing_links(metadata_expert, relationship_type: str, read_guid: str, read_end: int, far_guid: str,
                   *, page_size: int = PAGE_SIZE) -> list[dict]:
    """Every `relationship_type` link between `read_guid` (at end `read_end`) and `far_guid`, as the raw
    entries. Raises `Unreadable` when `read_guid`'s links cannot be read to the end."""
    found: list[dict] = []
    start = 0
    filtered_run = 0
    for _ in range(MAX_PAGES):
        try:
            answer = metadata_expert.get_related_metadata_elements(
                read_guid, relationship_type, {"class": "ResultsRequestBody"},
                starting_at_end=read_end, start_from=start, page_size=page_size)
        except Exception as exc:  # every failure is "could not check"
            raise Unreadable(f"{type(exc).__name__}: {' '.join(str(exc).split())[:200]}") from exc
        entries = _page_entries(answer)
        if entries is _END:
            return found
        if entries is _FILTERED:
            filtered_run += 1
            if filtered_run > MAX_FILTERED_RUN:
                raise Unreadable(f"more than {MAX_FILTERED_RUN} pages in a row came back empty after "
                                 "Egeria's filtering, so the end of the list was not reached")
            start += page_size
            continue
        filtered_run = 0
        for entry in entries:
            if not isinstance(entry, dict):
                raise Unreadable("an entry is not an object")
            far = _entry_element_guid(entry)
            if not far:
                # It might be the link we are looking for: never link past an entry we cannot attribute.
                raise Unreadable("an entry carries no element GUID")
            if far == far_guid:
                found.append(entry)
        start += page_size
    raise Unreadable(f"more than {MAX_PAGES} pages of links")


def related_entries(metadata_expert, relationship_type: str, read_guid: str, read_end: int,
                    *, page_size: int = PAGE_SIZE) -> list[dict]:
    """EVERY `relationship_type` entry of `read_guid` (sitting at end `read_end`), paged to the end of the list, as
    the raw entries `get_related_metadata_elements` gives. Raises `Unreadable` when the end was not reached (a
    failed read, an answer that is not a page, too many empty pages in a row, or `MAX_PAGES` pages): a count made
    from a partial list is never returned as if it were the whole.

    The one reader a proof that counts a report's annotations uses, because a single `get_asset_by_guid(
    graphQueryDepth=1)` read returns ONE graph page (999 entries live, 2026-10-21), however many a report holds."""
    found: list[dict] = []
    start = 0
    filtered_run = 0
    for _ in range(MAX_PAGES):
        try:
            answer = metadata_expert.get_related_metadata_elements(
                read_guid, relationship_type, {"class": "ResultsRequestBody"},
                starting_at_end=read_end, start_from=start, page_size=page_size)
        except Exception as exc:  # every failure is "could not read to the end"
            raise Unreadable(f"{type(exc).__name__}: {' '.join(str(exc).split())[:200]}") from exc
        entries = _page_entries(answer)
        if entries is _END:
            return found
        if entries is _FILTERED:
            filtered_run += 1
            if filtered_run > MAX_FILTERED_RUN:
                raise Unreadable(f"more than {MAX_FILTERED_RUN} pages in a row came back empty after "
                                 "Egeria's filtering, so the end of the list was not reached")
            start += page_size
            continue
        filtered_run = 0
        for entry in entries:
            if not isinstance(entry, dict):
                raise Unreadable("an entry is not an object")
            found.append(entry)
        start += page_size
    raise Unreadable(f"more than {MAX_PAGES} pages of {relationship_type} entries")


def _oldest(entries: list[dict]) -> dict:
    return min(entries, key=lambda e: str((e.get("versions") or {}).get("createTime") or "~"))


def link_once(metadata_expert, *, relationship_type: str, read_guid: str, read_end: int, far_guid: str,
              create: Callable[[], str], what: str, page_size: int = PAGE_SIZE) -> LinkOutcome:
    """Make the `relationship_type` link between `read_guid` (at end `read_end`) and `far_guid` by calling
    `create()` unless a link between exactly these two already exists. `what` names the link in log lines
    and errors. Never raises; failures come back as the outcome, logged at warning."""
    try:
        existing = existing_links(metadata_expert, relationship_type, read_guid, read_end, far_guid,
                                  page_size=page_size)
    except Unreadable as exc:
        words = (f"could not check for an existing {relationship_type} {what} ({exc}), so no link was "
                 "made: a duplicate is worse than a missing link")
        log.warning("%s", words)
        return LinkOutcome(COULD_NOT_CHECK, error=words)
    if existing:
        keep = _oldest(existing)
        rel = str(keep.get("relationshipGUID") or "")
        if len(existing) > 1:
            log.warning("%d %s links %s already exist (duplicates); using the oldest, %s",
                        len(existing), relationship_type, what, rel or "(no GUID)")
        error = "" if rel else (f"a {relationship_type} already exists but Egeria's answer carried no "
                                "relationship GUID")
        return LinkOutcome(ALREADY_LINKED, relationship_guid=rel, error=error)
    try:
        rel = create() or ""
    except Exception as exc:  # noqa: BLE001 - returned for the caller to record
        words = f"could not link {relationship_type} {what}: {exc}"[:400]
        log.warning("%s", words)
        return LinkOutcome(LINK_FAILED, error=words)
    log.info("Linked %s %s (relationship %s)", relationship_type, what, rel or "?")
    return LinkOutcome(LINKED, relationship_guid=str(rel))
