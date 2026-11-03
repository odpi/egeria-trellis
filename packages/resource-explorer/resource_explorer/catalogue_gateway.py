"""The one door a database catalogue commit uses to reach Egeria.

Slice B of the catalogue-scope work (`BRIEF-CURATE-CATALOGUE-COMMIT-DATABASES.md`).
`CatalogueGateway` is the whole surface the commit needs, as small named
operations, so the commit logic (`catalogue_commit.py`) is written against one
interface and the tests stand a stateful fake behind it. `PyegeriaCatalogueGateway`
is the real one.

**Everything the real gateway sends is recorded in the evidence, not invented.**
The mechanism comes from `evidence/SCRATCH-CATALOGUER-TEST-2-2026-10-05.md` and
`evidence/CATALOGUE-LEVER-FINDINGS.md`:

* the schema element is created from the technology type "PostgreSQL Relational
  Database Schema" (template GUID below; the short names return nothing) and
  attached to the JDBC cataloguer as a SCHEMA-kind catalog target with no
  configuration lists and `deleteMethod` ARCHIVE. **Never the database element,
  never the server**: a database-kind target catalogues every schema's tables
  directly under the database whatever its lists say;
* the targets are read with the body `{"class": "ResultsRequestBody",
  "graphQueryDepth": 0}`, because pyegeria's default sets a relationship type
  as the element type and the server answers 500 (ISSUE-122);
* the `/archive` endpoint answers 500 on this build, so an archive is the delete
  endpoint with `deleteMethod` ARCHIVE, and without `forLineage` and
  `forDuplicateProcessing` true it fails 400 and leaves one child archived;
* every delete is per element, leaf first, `forLineage` true, never a cascade
  (a cascade is partial and not atomic, S18);
* the survey is scoped by the request parameter `includeSchemaNames`, comma
  joined; the element's own connection configuration is ignored by the service;
* RE never restarts a connector. A forced refresh is the daemon's refresh call
  for the exact connector name.

What the evidence did NOT establish, and so is isolated in one small method each
and listed in the implemented note for the first live run: the parent/anchor
fields on the template create (the scratch runs created the schema without a
parent), the shape of a non-empty catalog-target answer, the relationship-read
answer shape, and the integration-daemon status keys.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from resource_explorer.database_server_link import ServerLink

log = logging.getLogger(__name__)

#: The JDBC cataloguer integration connector (the content pack's GUID).
JDBC_CATALOGUER_GUID = "70dcd0b7-9f06-48ad-ad44-ae4d7a7762aa"
#: The exact connector name a refresh is addressed to. The short names match nothing.
JDBC_CATALOGUER_NAME = "JDBCDatabaseCataloguer"
#: The schema template: technology type "PostgreSQL Relational Database Schema".
SCHEMA_TECH_TYPE = "PostgreSQL Relational Database Schema"
SCHEMA_TEMPLATE_GUID = "82a5417c-d882-4271-8444-4c6a996a8bfc"
DATABASE_TECH_TYPE = "PostgreSQL Relational Database"
#: Egeria's own attach step: a GovernanceActionType (NOT the process) that attaches an EXISTING
#: element, given as action target `newAsset`, to the JDBC cataloguer as a SCHEMA-kind target.
CATALOG_SCHEMA_ACTION_TYPE = "PostgreSQLGovernance::catalog-postgres-schema"
#: The relationship the JDBC cataloguer itself uses to put a schema under a database
#: (`RelationalDatabaseCataloguer.java:255-259`): DataSetContent, the DATABASE at end 2.
SCHEMA_PARENT_RELATIONSHIP = "DataSetContent"
SCHEMA_PARENT_AT_END1 = False
#: What a catalog target does with an element when its schema leaves the list.
TARGET_DELETE_METHOD = "ARCHIVE"
#: The honest value for a version RE has no record of.
VERSION_NOT_RECORDED = "not recorded"

#: delete forms RE performs
SOFT_DELETE = "soft_delete"
ARCHIVE = "archive"

#: HARD BLOCK (2026-10-06, ISSUE-117; user-facing name: "archive-cascade block"): in the Egeria build of 2026-10-06,
#: archiving ANY element that is not its own anchor (a schema, a table, a column, a schema type) archived the anchor's
#: WHOLE tree: leaving out us_sales archived the coco_pharma database element and both demo schemas.
#:
#: LIFTED 2026-10-23 FOR THE LEAVE-OUT ARCHIVE PATH ONLY (architect ruling; proof
#: docs/design-notes/evidence/REPROOF-1021-PHASE2-RESULT.md: on odpi/egeria c6dbf952 / image a8bcff5c the leaf-first
#: ARCHIVE of a cataloguer-built schema took exactly its 14 anchored elements and nothing else). The lift covers ONE
#: scope, `LEAVE_OUT_ARCHIVE`: a leave-out whose form is ARCHIVE. Everything else keeps the block as before, with its
#: own block word, until its own throwaway proof (ruled 2026-10-21): soft delete (a leave-out with nothing hanging off
#: it included), the investigation-delete ARCHIVE (anchor = Project), and bulk delete in Egeria.
#:
#: Re-arming the block for the leave-out path too needs no code change, one variable read at CALL time:
#:
#:     RE_ISSUE_117_BLOCK=on                                  ON, stamped "(no stamp)"
#:     RE_ISSUE_117_BLOCK=<who>/<UTC ISO time>                ON, stamped "(<who>/<UTC>)"   e.g. dwolfson/2026-10-23T14:05:00Z
#:
#: Any other non-empty value is also ON ("(no stamp)", with a logged reason): a value meant to re-arm never lifts.
#: The re-arm does not expire. While the block is ON (re-armed, or for the scopes never lifted) it is lifted for ONE
#: database only, by the slug-scoped override, also read at CALL time:
#:
#:     RE_ISSUE_117_BLOCK_OFF=<who>/<UTC ISO time>/<database slug>      e.g.  dwolfson/2026-10-21T14:05:00Z/throwaway_db
#:
#: The override opens that slug for 60 minutes after the stamp (a forgotten variable can never leave it off). Any
#: other shape (the old bare clearance, a missing slug, an unparsable time, a stamp more than a few minutes in the
#: future, an expired stamp) is no override, with a logged reason. A caller that cannot name the database slug it acts
#: for is blocked, in every scope. There is no code constant to edit. While blocked, `delete_element` raises before it
#: touches a client and nothing is sent; choices are still recorded in RE.
ISSUE_117_BLOCK_ENV = "RE_ISSUE_117_BLOCK"
#: The one scope the 2026-10-23 lift opens: a leave-out whose form is ARCHIVE. Callers outside it pass no scope.
LEAVE_OUT_ARCHIVE = "leave_out_archive"
LIFTED_SCOPES = frozenset({LEAVE_OUT_ARCHIVE})
LIFT_EVIDENCE = ("lifted 2026-10-23, re-proven 2026-10-29 on odpi/egeria abe31272 / image 3f396977, evidence "
                 "docs/design-notes/evidence/REPROOF-1029-RESULT.md")
NO_STAMP = "(no stamp)"
ISSUE_117_ENV = "RE_ISSUE_117_BLOCK_OFF"
OVERRIDE_WINDOW = timedelta(minutes=60)
OVERRIDE_FUTURE_SLACK = timedelta(minutes=5)
_logged_override_reasons: set[tuple[str, str]] = set()


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def issue_117_override() -> tuple[str, datetime | None, str]:
    """Parse the environment override: `(slug, expires_at, "")` when it lifts the block for `slug` right now,
    else `("", None, reason)`. `reason` is "" only when the variable is unset. A bad value is logged once."""
    import os
    raw = (os.environ.get(ISSUE_117_ENV) or "").strip()
    if not raw:
        return "", None, ""

    def refuse(reason: str) -> tuple[str, None, str]:
        if (raw, reason) not in _logged_override_reasons:
            _logged_override_reasons.add((raw, reason))
            log.warning("archive-cascade block stays ON: %s=%r is not a usable override (%s); "
                        "the format is <who>/<UTC ISO time>/<database slug>", ISSUE_117_ENV, raw, reason)
        return "", None, reason

    parts = raw.rsplit("/", 2)
    if len(parts) != 3 or not all(p.strip() for p in parts):
        return refuse("expected <who>/<UTC ISO time>/<database slug>")
    _who, stamp_text, slug = (p.strip() for p in parts)
    try:
        stamp = datetime.fromisoformat(stamp_text.replace("Z", "+00:00").replace("z", "+00:00"))
    except ValueError:
        return refuse(f"time {stamp_text!r} is not an ISO time")
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    now = _now_utc()
    if stamp > now + OVERRIDE_FUTURE_SLACK:
        return refuse(f"time {stamp_text!r} is in the future")
    expires = stamp + OVERRIDE_WINDOW
    if now >= expires:
        return refuse(f"expired at {expires.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}")
    return slug, expires, ""


def issue_117_rearmed() -> tuple[bool, str]:
    """`(True, "(<who>/<UTC>)" or "(no stamp)")` when RE_ISSUE_117_BLOCK re-arms the block for the lifted scopes,
    else `(False, "")`. Unset or blank is not armed; `on` is ON with no stamp; `<who>/<UTC ISO time>` is ON with that
    stamp; anything else is ON with no stamp and a logged reason (a value meant to re-arm never lifts)."""
    import os
    raw = (os.environ.get(ISSUE_117_BLOCK_ENV) or "").strip()
    if not raw:
        return False, ""
    if raw.lower() == "on":
        return True, NO_STAMP
    parts = raw.rsplit("/", 1)
    reason = ""
    if len(parts) != 2 or not all(p.strip() for p in parts):
        reason = "expected on or <who>/<UTC ISO time>"
    else:
        who, stamp_text = (p.strip() for p in parts)
        try:
            stamp = datetime.fromisoformat(stamp_text.replace("Z", "+00:00").replace("z", "+00:00"))
        except ValueError:
            reason = f"time {stamp_text!r} is not an ISO time"
        else:
            if stamp.tzinfo is None:
                stamp = stamp.replace(tzinfo=timezone.utc)
            return True, f"({who}/{stamp.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')})"
    if (raw, reason) not in _logged_override_reasons:
        _logged_override_reasons.add((raw, reason))
        log.warning("archive-cascade block is ON with no stamp: %s=%r (%s)", ISSUE_117_BLOCK_ENV, raw, reason)
    return True, NO_STAMP


def issue_117_blocked(slug: str, *, scope: str = "") -> bool:
    """True when an archive/delete for database `slug` in `scope` may not be sent. A scope in `LIFTED_SCOPES` (only
    `LEAVE_OUT_ARCHIVE`) passes unless RE_ISSUE_117_BLOCK re-arms it; every other scope, and a re-armed one, passes
    only when the override names exactly `slug` inside its window. `slug` is required in every scope: a caller that
    cannot name the database it acts for passes "" and is blocked."""
    if not slug:
        return True
    if scope in LIFTED_SCOPES and not issue_117_rearmed()[0]:
        return False
    named, _expires, _reason = issue_117_override()
    return not (named and slug == named)


def issue_117_state_line() -> str:
    """The start-up line. OFF (the leave-out lift, with its evidence) or ON (re-armed, with its stamp); a live
    slug override is added after it."""
    armed, stamp = issue_117_rearmed()
    line = f"archive-cascade block: ON {stamp}" if armed else f"archive-cascade block: OFF ({LIFT_EVIDENCE}) · scope: leave-out archive"
    named, expires, _reason = issue_117_override()
    if named and expires is not None:
        line += f" · OFF for {named} until {expires.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}"
    return line


#: The build the lift was last proven on (2026-10-29 re-proof; before that 2026-10-27 on odpi/egeria 12b1aaae / image
#: c2ca054c, REPROOF-1027-RESULT.md; 2026-10-26 on odpi/egeria 8de9572c / image 4994af36, REPROOF-1026-RESULT.md;
#: first proven 2026-10-23 on odpi/egeria c6dbf952 / image a8bcff5c,
#: REPROOF-1021-PHASE2-RESULT.md). RE does NOT compare the platform's build with it (it cannot tell an
#: older build from a newer one by this string): the start-up line prints both side by side, and the only control
#: for an older platform is the env re-arm, RE_ISSUE_117_BLOCK=on (architect ruling (b), 2026-10-23).
PROVEN_BUILD = "odpi/egeria abe31272 / image 3f396977"
MAX_ORIGIN_CHARS = 200            # the origin text is logged: never more than this
PLATFORM_UNREACHABLE = "platform: unreachable at start"
#: The platform's own origin endpoint: the one pyegeria's `BasePlatformClient.async_get_platform_origin` reads.
PLATFORM_ORIGIN_PATH = "/open-metadata/platform-services/server-platform/origin"


def read_platform_origin(timeout: float = 3.0) -> str:
    """ONE cheap read at start-up: the platform's origin string, or "" when it cannot be read. The endpoint pyegeria's
    `get_platform_origin` reads, called directly: building that client makes a second read (`check_connection`) and
    drives its own event loop, which the web start (inside uvloop) cannot do. No credential is sent; TLS is not
    verified, as pyegeria does not verify it (`enable_ssl_check = False`). Never raises."""
    try:
        import httpx

        from resource_explorer.config import egeria_platform_url

        url = (egeria_platform_url() or "").rstrip("/")
        if not url:
            return ""
        r = httpx.get(url + PLATFORM_ORIGIN_PATH, timeout=timeout, verify=False)
        return " ".join(r.text.split())[:MAX_ORIGIN_CHARS] if r.status_code == 200 else ""
    except Exception as exc:  # noqa: BLE001 - never fails the start
        log.info("platform origin not read at start: %s", type(exc).__name__)
        return ""


def platform_origin_words(origin: str) -> str:
    """"platform reports <origin> · lift proven on <build>", or "platform: unreachable at start". Said side by side;
    never a verdict that the two builds match."""
    origin = (origin or "").strip()[:MAX_ORIGIN_CHARS]
    if not origin:
        return PLATFORM_UNREACHABLE
    return f"platform reports {origin} · lift proven on {PROVEN_BUILD}"


def issue_117_start_line(read=None) -> str:
    """The start-up line: the block state, then what the platform reports beside the proven build. The block state
    is the same whatever the read answers (an unreachable platform changes nothing)."""
    return f"{issue_117_state_line()} · {platform_origin_words((read or read_platform_origin)())}"


ISSUE_117_WORDS = ("Egeria archives the whole database tree when any part of it is archived "
                   "(archive-cascade block, archiveBeanInRepository) · choice kept, nothing sent")


class GatewayError(RuntimeError):
    """An Egeria read or write failed. The message is Egeria's own word, shortened."""


@dataclass
class CatalogTarget:
    relationship_guid: str
    element_guid: str
    name: str = ""


@dataclass
class ElementRead:
    guid: str
    qualified_name: str
    type_name: str = ""
    archived: bool = False
    #: an archived element's archive time (Memento archiveDate, else versions), ISO UTC; '' otherwise
    archived_at: str = ""


@dataclass
class EndpointRead:
    """An Endpoint element: its GUID, qualifiedName and `networkAddress` (D1-a, the endpoint resolver)."""
    guid: str
    qualified_name: str
    network_address: str = ""


@dataclass
class Relationship:
    type_name: str
    other_guid: str = ""
    other_type: str = ""
    guid: str = ""
    #: An `ActionTarget` relationship carries the engine action's `activityStatus` and
    #: `completionTime` (epoch ms as a string); the action element carries `completionMessage`.
    activity_status: str = ""
    completion_time: str = ""
    completion_message: str = ""
    #: The far end's qualifiedName and name (a DataFlow's meaning depends on WHAT is at the other end).
    other_qualified_name: str = ""
    other_name: str = ""
    #: The engine action's `requestType` (e.g. `survey-postgres-database`, `catalog-postgres-schema`).
    action_kind: str = ""
    #: The relationship's own properties as strings (a PeerDuplicateLink's `statusIdentifier`, `steward`, ...).
    properties: dict = field(default_factory=dict)
    #: Whether the far end (`other_guid`) is the relationship's end 1 (`elementAtEnd1`); None when Egeria did not say.
    other_at_end1: bool | None = None


@dataclass
class EngineActionStatus:
    status: str = ""
    message: str = ""
    completion_time: str = ""


@dataclass
class PublishedDatabase:
    server_guid: str
    database_guid: str
    server_name: str
    database_qualified_name: str
    survey_submissions: list[dict] = field(default_factory=list)


@dataclass
class SurveyOutcome:
    """What a read of Egeria says about a survey that was started: the engine action's own
    status and message, and the newest survey report made since, with its annotation count.
    `annotations` None means no report was seen (not "a report with none")."""
    action_status: str = ""
    message: str = ""
    report_guid: str = ""
    annotations: int | None = None


#: Engine action `activityStatus` values that mean the action did not complete.
FAILED_ACTION_STATUSES = frozenset({"FAILED", "INVALID", "CANCELLED"})
#: ...and the ones that mean it is over, one way or the other.
TERMINAL_ACTION_STATUSES = frozenset({"COMPLETED"}) | FAILED_ACTION_STATUSES
#: Running. Any other value (and a missing one) is one Resource Explorer does not know.
RUNNING_ACTION_STATUSES = frozenset({"REQUESTED", "APPROVED", "WAITING", "ACTIVATING", "IN_PROGRESS"})


@dataclass
class ConnectorStatus:
    name: str
    status: str = ""
    last_refresh_time: str = ""


# ── archived siblings (S19, owner ruling 2026-10-14) ─────────────────────────
#: Egeria renames an element it archives to `<qualifiedName>_archivedOn_<date>` and keeps it as a Memento
#: (captured 2026-10-14: `...::re_throwaway_62_archivedOn_Fri Oct 09 01:28:08 GMT 2026`). The original name is
#: then FREE, so a read by it answers "No elements found" and a create under it makes a NEW element.
ARCHIVED_ON = "_archivedOn_"
SIBLING_UNREADABLE = "could not check whether an archived copy exists"
REINCLUDE_TAIL = "re-including creates a new element; the archived record stays"
SIBLING_CHANGED = "the archived copy changed since you confirmed · nothing created · check again"
NEEDS_CONFIRMATION = "needs_confirmation"
#: Java's `Date.toString()`: `EEE MMM dd HH:mm:ss zzz yyyy`, the zone any abbreviation (GMT on the quickstart,
#: BST, CEST, PDT, ... elsewhere). Only the day is read from it; the ORDER comes from the element's own times.
_JAVA_DATE_RE = re.compile(r"^[A-Z][a-z]{2} ([A-Z][a-z]{2}) (\d{1,2}) (\d{2}):(\d{2}):(\d{2}) (\S+) (\d{4})$")
_MONTHS = {m: i for i, m in enumerate(("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov",
                                       "Dec"), start=1)}
#: Paging, as `egeria_link_once` pages (re/lineage-link-dedupe): read until "No elements found"; "No elements
#: returned" is a page Egeria emptied by filtering and is read past (a bounded run of them); a bound on pages.
FIND_PAGE_SIZE = 500
MAX_FIND_PAGES = 200
MAX_FILTERED_RUN = 10
_END_OF_LIST = "no elements found"
_FILTERED_PAGE = "no elements returned"


def reinclude_words(date: str) -> str:
    return f"archived in Egeria · {date or 'date not stated'} · {REINCLUDE_TAIL}"


class SiblingLookupFailed(GatewayError):
    """The archived-sibling lookup could not be made: never read as "no archived copy"."""

    def __init__(self, detail: str = ""):
        super().__init__(f"{SIBLING_UNREADABLE}" + (f" ({detail})" if detail else ""))


@dataclass
class ArchivedSibling:
    guid: str
    qualified_name: str
    #: the suffix exactly as Egeria wrote it; the day it names ('' when the suffix is not a Java date)
    archived_on: str = ""
    archived_day: str = ""
    #: when Egeria archived it, from the element itself (the Memento's archiveDate, else versions.updateTime,
    #: else versions.createTime), as ISO UTC; '' when the element states none. This orders the copies.
    archived_at: str = ""
    archived: bool = True

    @property
    def date(self) -> str:
        """The day it was archived, for the screen: `2026-10-09`, or Egeria's own text when unparsed."""
        return self.archived_day or self.archived_on


def java_date_day(text: str) -> str:
    """`2026-10-09` from `Fri Oct 09 01:28:08 <any zone> 2026`; '' when the text is not that form."""
    m = _JAVA_DATE_RE.match(text.strip())
    if not m or m.group(1) not in _MONTHS:
        return ""
    return f"{int(m.group(7)):04d}-{_MONTHS[m.group(1)]:02d}-{int(m.group(2)):02d}"


def _iso_of(value: Any) -> str:
    """An Egeria time (epoch ms, as a number or a string, or an ISO string) as ISO UTC; '' when unreadable."""
    from datetime import datetime, timezone
    if value in (None, ""):
        return ""
    text = str(value).strip()
    if text.isdigit():
        return datetime.fromtimestamp(int(text) / 1000, tz=timezone.utc).isoformat()
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return ""
    return (dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)).astimezone(timezone.utc).isoformat()


def _archived_at_of(element: dict) -> str:
    for c in classifications_named(element, "Memento"):
        cp = c.get("classificationProperties") or c.get("properties") or {}
        got = _iso_of(((cp.get("propertiesAsStrings") or {}) if isinstance(cp, dict) else {}).get("archiveDate")
                      or (cp.get("archiveDate") if isinstance(cp, dict) else None))
        if got:
            return got
    header = element.get("elementHeader") if isinstance(element.get("elementHeader"), dict) else {}
    versions = element.get("versions") if isinstance(element.get("versions"), dict) else header.get("versions") or {}
    return _iso_of(versions.get("updateTime")) or _iso_of(versions.get("createTime"))


def archived_on_of(qualified_name: str, original: str) -> tuple[str, str]:
    """(the suffix text, the day it names or '') of `<original>_archivedOn_<suffix>`."""
    text = qualified_name[len(original) + len(ARCHIVED_ON):]
    return text, java_date_day(text)


def _sibling_of(element: dict, qualified_name: str) -> ArchivedSibling:
    qn = _qn_of(element)
    text, day = archived_on_of(qn, qualified_name)
    return ArchivedSibling(_guid_of(element), qn, text, day, _archived_at_of(element), _is_memento(element))


def _newest_first(sibs: list[ArchivedSibling]) -> list[ArchivedSibling]:
    return sorted(sibs, key=lambda s: (s.archived_at, s.archived_day, s.qualified_name), reverse=True)


def parse_archived_siblings(res: Any, qualified_name: str) -> list[ArchivedSibling]:
    """The elements named `<qualified_name>_archivedOn_...` in a find answer (one page, or the raw elements of
    every page), NEWEST first. "No elements found" is []; an answer this does not recognise raises."""
    _no_answer(res, "elements")
    if _no_elements(res):
        return []
    if not isinstance(res, list) or not all(isinstance(x, dict) and _guid_of(x) for x in res):
        raise GatewayError(f"unrecognised elements answer from Egeria: {type(res).__name__}")
    prefix = qualified_name + ARCHIVED_ON
    return _newest_first([_sibling_of(x, qualified_name) for x in res if _qn_of(x).startswith(prefix)])


def find_all_starting_with(client: Any, prefix: str, *, type_name: str = "",
                           page_size: int = FIND_PAGE_SIZE, starts_with: bool = True) -> list[dict]:
    """Every raw element a starts-with, forLineage find for `prefix` answers, read page by page to the end.
    The body is the one `elements_under` sends (captured 6.2 returning a Memento) plus the page and, given, the
    element type. Anything that is not an answer, a page, the end or a filtered page raises
    `SiblingLookupFailed`: a lookup that could not be made never reads as "nothing there"."""
    from resource_explorer.egeria_absence import lookup_exception_is_absent
    found: list[dict] = []
    start, filtered_run = 0, 0
    for _ in range(MAX_FIND_PAGES):
        body = {"class": "SearchStringRequestBody", "searchString": prefix, "startsWith": starts_with, "ignoreCase": False,
                "forLineage": True, "forDuplicateProcessing": True, "graphQueryDepth": 0,
                "startFrom": start, "pageSize": page_size}
        if type_name:
            body["metadataElementTypeName"] = type_name
        try:
            res = client.find_metadata_elements_with_string(
                search_string=prefix, starts_with=starts_with, start_from=start, page_size=page_size,
                metadata_element_type=type_name or None, body=body)
        except Exception as exc:
            if lookup_exception_is_absent(exc):
                return found                     # the end of the list, said as a typed not-found
            raise SiblingLookupFailed(_short(exc, 200)) from exc
        if res is None:
            raise SiblingLookupFailed("the client returned nothing")
        if isinstance(res, str):
            said = res.strip().rstrip(".").strip().lower()
            if said == _END_OF_LIST:
                return found
            if said != _FILTERED_PAGE:
                raise SiblingLookupFailed(f"unexpected answer {res!r:.120}")
            res = []
        if not isinstance(res, list):
            raise SiblingLookupFailed(f"unexpected answer type {type(res).__name__}")
        if not res:
            filtered_run += 1
            if filtered_run > MAX_FILTERED_RUN:
                raise SiblingLookupFailed(f"more than {MAX_FILTERED_RUN} pages in a row came back empty after "
                                          "Egeria's filtering, so the end of the list was not reached")
        else:
            filtered_run = 0
            for x in res:
                if not (isinstance(x, dict) and _guid_of(x)):
                    raise SiblingLookupFailed("an element in the answer carries no GUID")
                found.append(x)
        start += page_size
    raise SiblingLookupFailed(f"more than {MAX_FIND_PAGES} pages")


#: The element type of each qualifiedName RE builds and re-includes (`<deployed implementation type>::...`, the
#: template's own prefix): an archived copy keeps its type, so the sibling search is TYPED with it. A schema is a
#: DeployedDatabaseSchema (`schema_elements`), a database a RelationalDatabase (`publish_database`'s typed read).
ARCHIVED_SIBLING_TYPES = {SCHEMA_TECH_TYPE: "DeployedDatabaseSchema", DATABASE_TECH_TYPE: "RelationalDatabase"}


def archived_sibling_type(qualified_name: str) -> str:
    """The element type the archived copies of `qualified_name` carry, from the name's template prefix. A name RE
    does not know the type of raises `SiblingLookupFailed` (never an untyped search, never "no archived copy")."""
    type_name = ARCHIVED_SIBLING_TYPES.get(qualified_name.split("::", 1)[0])
    if not type_name:
        raise SiblingLookupFailed(f"no element type is known for {qualified_name!r}, so its archived copies are not "
                                  "searched (an untyped search reads the whole repository)")
    return type_name


def find_archived_siblings(client: Any, qualified_name: str, *, type_name: str = "") -> list[ArchivedSibling]:
    """S19: the archived copies of `qualified_name`, by the name Egeria gives them on archive, newest first.

    TYPED (architect ruling 2026-10-23): the find carries the element's own type (`type_name`, else
    `archived_sibling_type`) and the exact prefix `<qualifiedName>_archivedOn_`. The untyped form took ~4 min in a
    live leave-out on the grown repository. Every page is read; a read that cannot be made raises
    `SiblingLookupFailed`."""
    raw = find_all_starting_with(client, qualified_name + ARCHIVED_ON,
                                 type_name=type_name or archived_sibling_type(qualified_name))
    return parse_archived_siblings(raw or "No elements found", qualified_name)


def decide_reinclude(live: Any, sibs: list[ArchivedSibling], confirmed_guid: str = "") -> dict:
    """What a create under one name may do, given the live element (or None) and its archived copies.

    `state`: exists (a live element: adopt, no create) | clear (create) | needs_confirmation (NO create) |
    confirmed (create; the confirmation names the newest copy) | changed (NO create; it names another copy)."""
    if live is not None:
        return {"state": "exists", "live": live, "sibling": None, "text": ""}
    if not sibs:
        return {"state": "clear", "live": None, "sibling": None, "text": ""}
    newest = sibs[0]
    base = {"live": None, "sibling": newest, "others": [x.guid for x in sibs[1:]]}
    if confirmed_guid and confirmed_guid == newest.guid:
        return {**base, "state": "confirmed", "text": reinclude_words(newest.date)}
    if confirmed_guid:
        return {**base, "state": "changed", "text": SIBLING_CHANGED}
    return {**base, "state": NEEDS_CONFIRMATION, "text": reinclude_words(newest.date)}


# ── names ────────────────────────────────────────────────────────────────────

def server_name_for(db_entity) -> str:
    """The name Egeria's template carries for the server: `<egeria host>:<port>`."""
    host = getattr(db_entity, "egeria_host", "") or db_entity.host
    return f"{host}:{db_entity.port}"


def server_qualified_name(server_name: str) -> str:
    """The SoftwareServer RE's publish creates from the "PostgreSQL Server" template: `PostgreSQL Server::<host:port>`
    (rehearsal 1 evidence: the created server `PostgreSQL Server::host.docker.internal:5442`)."""
    return f"PostgreSQL Server::{server_name}"


def database_qualified_name(server_name: str, database_name: str) -> str:
    return f"PostgreSQL Relational Database::{server_name}::{database_name}"


def schema_qualified_name(server_name: str, database_name: str, schema: str) -> str:
    """The qualifiedName the schema template produces (evidence, 2026-10-05)."""
    return f"{SCHEMA_TECH_TYPE}::{server_name}::{database_name}.{schema}"


def schema_placeholders(db_entity, schema: str, description: str = "") -> dict[str, str]:
    """The placeholders the schema template takes AND the request parameters Egeria's attach action
    copies into the target: one dict, so the template create and the action type cannot disagree."""
    from resource_explorer.config import get_config
    from resource_explorer.surveyors.database.egeria_database_surveyor import _secrets_collection_name
    cfg = get_config().egeria
    return {
        "databaseName": db_entity.database_name,
        "serverName": server_name_for(db_entity),
        "hostIdentifier": getattr(db_entity, "egeria_host", "") or db_entity.host,
        "portNumber": str(db_entity.port),
        "schemaName": schema,
        "schemaDescription": description or f"PostgreSQL schema {schema} in {db_entity.database_name}",
        "versionIdentifier": VERSION_NOT_RECORDED,
        "secretsCollectionName": _secrets_collection_name(db_entity.slug),
        "secretsStorePathName": cfg.secrets_store_path_name,
    }


class CatalogueGateway(Protocol):
    """Everything a catalogue commit asks of Egeria. Reads raise `GatewayError`."""

    def publish_database(self, db_entity, db_user: str, db_pwd: str, *, registry=None,
                         submitted_by: str = "", reinclude_guid: str = "") -> PublishedDatabase: ...
    def publish_local_report(self, db_entity, db_user: str, db_pwd: str, measured: dict, *,
                             registry=None, submitted_by: str = "") -> dict: ...
    def set_zone_membership(self, guid: str, zones: list[str]) -> bool: ...
    def read_zones(self, guid: str) -> list[str]: ...
    def initiate_catalog_action(self, schema_guid: str, request_parameters: dict[str, str]) -> str: ...
    def engine_action_status(self, guid: str) -> EngineActionStatus: ...
    def survey_outcome(self, database_guid: str, engine_action_guid: str, since: str) -> SurveyOutcome: ...
    def set_owner(self, guid: str, owner: str, *, person: bool = False) -> tuple[str, str]: ...
    def read_element(self, qualified_name: str, *, for_lineage: bool = False) -> ElementRead | None: ...
    def qualified_name_of(self, guid: str) -> str: ...
    def create_schema_element(self, db_entity, schema: str, database_guid: str, *,
                              description: str = "") -> str: ...
    def mark_on_behalf(self, guid: str, requester: str, owner: str) -> str: ...
    def link_database_to_server(self, server_guid: str, database_guid: str, *, server_name: str = "",
                                write: bool = True) -> "ServerLink": ...
    def link_published_database(self, db_entity, *, write: bool = True) -> "ServerLink": ...
    def list_catalog_targets(self) -> list[CatalogTarget]: ...
    def add_catalog_target(self, element_guid: str, name: str) -> str: ...
    def remove_catalog_target(self, relationship_guid: str) -> None: ...
    def elements_under(self, qualified_name_prefix: str) -> list[ElementRead]: ...
    def archived_siblings(self, qualified_name: str, *, type_name: str = "") -> list[ArchivedSibling]: ...
    def schema_elements(self, prefix: str) -> list[ElementRead]: ...
    def relationships(self, guid: str) -> list[Relationship]: ...
    def find_endpoints(self, search: str) -> list[EndpointRead]: ...
    def link_data_set_content(self, data_set_guid: str, content_guid: str) -> None: ...
    def delete_element(self, guid: str, form: str, *, slug: str, scope: str = "") -> None: ...
    def initiate_survey(self, database_guid: str, request_parameters: dict[str, str]) -> tuple[str, str]: ...
    def connector_status(self) -> ConnectorStatus | None: ...
    def refresh_connector(self, timeout: int = 120) -> None: ...


# ── the real one ─────────────────────────────────────────────────────────────

#: Egeria's own sentence is kept whole: a proof row's text was once cut at 300 characters, before
#: the sentence that said why. 4000 is a safety bound for a runaway body, not a display width.
MAX_EGERIA_TEXT = 4000


def _short(exc: Exception, n: int = MAX_EGERIA_TEXT) -> str:
    return " ".join(str(exc).split())[:n] or type(exc).__name__


#: Egeria 6.2's ElementHeader (ElementHeader.java, shape since e17cc09d44) has NO generic `classifications` list:
#: each classification below has its own field, holding one ElementClassification whose `classificationProperties`
#: is the flattened bean (`{"class": "ZoneMembershipProperties", "zoneMembership": [...]}`). Captured live
#: 2026-10-14 (tests/fixtures/egeria_6_2): AssetMaker.get_asset_by_guid -> `elementHeader.memento/.ownership/.anchor/
#: .dataScope`; CollectionManager.get_collection_by_guid -> `elementHeader.zoneMembership`. MetadataExpert's answers
#: (an OpenMetadataElement) still carry the top-level `classifications` list, which stays the fallback.
HEADER_CLASSIFICATION_FIELDS = {
    "Anchors": "anchor", "ZoneMembership": "zoneMembership", "ZoneMembershipProfile": "zoneMembershipProfile",
    "Impact": "impact", "Criticality": "criticality", "Confidentiality": "confidentiality",
    "Confidence": "confidence", "Retention": "retention", "GovernanceExpectations": "governanceExpectations",
    "GovernanceMeasurements": "governanceMeasurements", "DigitalResourceOrigin": "digitalResourceOrigin",
    "Ownership": "ownership", "Memento": "memento", "Promise": "promise", "Template": "template",
    "TemplateSubstitute": "templateSubstitute", "TypeEmbeddedAttribute": "schemaType", "DataScope": "dataScope",
    "RunMetrics": "runMetrics", "DataAssetEncoding": "dataAssetEncoding", "CalculatedValue": "calculatedValue",
    "PrimaryKey": "primaryKey", "KnownDuplicate": "knownDuplicate", "ConsolidatedDuplicate": "consolidateDuplicate",
    "SecurityTags": "securityTags", "AccountingCodes": "accountingCodes",
    "SecurityListMembership": "securityListMembership", "UserAccountProfile": "userAccountProfile",
    "ProjectClassification": "projectClassification",
}
#: The 6.2 header's classification lists (several classifications each); `otherClassifications` holds the rest.
HEADER_CLASSIFICATION_LISTS = ("executionPoints", "duplicateClassifications", "collectionKinds", "glossaryTermKinds",
                               "locationKinds", "projectKinds", "otherClassifications")


class UnreadableClassification(GatewayError):
    """A classification's header field is there but is not a classification RE can read: never "absent"."""


def _is_named(c: Any, name: str) -> bool:
    return isinstance(c, dict) and (c.get("classificationName") == name
                                    or (c.get("type") or {}).get("typeName") == name)


def classifications_named(element: Any, name: str) -> list[dict]:
    """The element's `name` classifications, in either shape Egeria answers. The 6.2 dedicated header field is
    read FIRST; only when it is absent (missing or null) are the lists read: `classifications` (top level, then
    under `elementHeader`) and the header's lists, `otherClassifications` included. A dedicated field that is
    present but not a classification RAISES `UnreadableClassification`: it is never read as "absent"."""
    in_field, in_lists = classification_sources(element, name)
    return in_field if in_field else in_lists


def classification_sources(element: Any, name: str) -> tuple[list[dict], list[dict]]:
    """(the 6.2 dedicated header field's `name` classification, or []; the `name` classifications in the lists).
    A reader that must not silently prefer one when both are there (zones) compares the two. A dedicated field
    that is present but is not a `name` classification RAISES `UnreadableClassification` (review round 1: `{}` or
    another classification in `elementHeader.anchor` read as "own anchor, unzoned")."""
    if not isinstance(element, dict):
        return [], []
    header = element.get("elementHeader") if isinstance(element.get("elementHeader"), dict) else {}
    in_field: list[dict] = []
    field = HEADER_CLASSIFICATION_FIELDS.get(name)
    if field and header.get(field) is not None:
        value = header[field]
        if not _is_named(value, name):
            raise UnreadableClassification(f"a {name} in a shape RE cannot read (elementHeader.{field})")
        in_field = [value]
    found = [*(element.get("classifications") or []), *(header.get("classifications") or [])]
    for listed in HEADER_CLASSIFICATION_LISTS:
        found.extend(header.get(listed) or [])
    return in_field, [c for c in found if _is_named(c, name)]


def _guid_of(element: Any) -> str:
    """LIVE: top-level `elementGUID`. (`elementHeader.guid` is the asset-graph shape.)"""
    if not isinstance(element, dict):
        return ""
    if element.get("elementGUID"):
        return str(element["elementGUID"])
    h = element.get("elementHeader")
    return str(h.get("guid") or "") if isinstance(h, dict) else ""


def _type_of(element: Any) -> str:
    """LIVE: top-level `type.typeName`."""
    if not isinstance(element, dict):
        return ""
    t = element.get("type")
    if not isinstance(t, dict):
        h = element.get("elementHeader")
        t = h.get("type") if isinstance(h, dict) else None
    return str(t.get("typeName") or "") if isinstance(t, dict) else ""


def _qn_of(element: Any) -> str:
    """LIVE: `elementProperties.propertiesAsStrings.qualifiedName`, or the typed value in
    `elementProperties.propertyValueMap.qualifiedName.primitiveValue`."""
    if not isinstance(element, dict):
        return ""
    ep = element.get("elementProperties")
    if isinstance(ep, dict):
        v = (ep.get("propertiesAsStrings") or {}).get("qualifiedName")
        if v:
            return str(v)
        pv = (ep.get("propertyValueMap") or {}).get("qualifiedName")
        if isinstance(pv, dict) and pv.get("primitiveValue"):
            return str(pv["primitiveValue"])
    props = element.get("properties")
    return str(props.get("qualifiedName") or "") if isinstance(props, dict) else ""


def _is_memento(element: Any) -> bool:
    """A Memento on the element: 6.2's `elementHeader.memento`, else the `classifications` list. A `memento`
    field RE cannot read raises (`UnreadableClassification`), so an archived element is never read as live."""
    return bool(classifications_named(element, "Memento"))


def _element_read(element: Any) -> ElementRead:
    archived = _is_memento(element)
    return ElementRead(guid=_guid_of(element), qualified_name=_qn_of(element), type_name=_type_of(element),
                       archived=archived, archived_at=_archived_at_of(element) if archived else "")


def _strings_of(element: Any) -> dict:
    """LIVE: `elementProperties.propertiesAsStrings` of a raw element ({} when it has none)."""
    ep = element.get("elementProperties") if isinstance(element, dict) else None
    ps = ep.get("propertiesAsStrings") if isinstance(ep, dict) else None
    return ps if isinstance(ps, dict) else {}


def _zone_list_from_string(raw: str) -> list[str]:
    """`[a, b]` (a Java list rendering) or `{0=a, 1=b}` (an array's propertiesAsStrings rendering). Anything
    else raises: a ZoneMembership RE cannot parse is never read as "no zones" (Brief Z round 2)."""
    text = str(raw).strip()
    if text in ("", "[]", "{}"):
        return []                      # an explicitly empty array: unzoned, as Egeria reads it
    if text.startswith("[") and text.endswith("]"):
        return [p.strip() for p in text[1:-1].split(",") if p.strip()]
    if text.startswith("{") and text.endswith("}"):
        parts = [p.strip() for p in text[1:-1].split(",") if p.strip()]
        if all("=" in p for p in parts):
            return [p.split("=", 1)[1].strip() for p in parts]
    raise GatewayError(f"a ZoneMembership in a shape RE cannot read ({text[:60]!r}): its zones are unknown")


def zones_of_element(element: Any) -> list[str]:
    """The element's ZoneMembership zones from its raw classifications (top level, or under elementHeader);
    `[]` ONLY when no ZoneMembership is on it (or one carries an explicitly empty list). Raises on an element it
    does not recognise, and on a ZoneMembership whose zones it cannot parse, so "unreadable" is never read as
    "no zones"."""
    if not (isinstance(element, dict) and _guid_of(element)):
        raise GatewayError("unrecognised element answer from Egeria: cannot read its zones")
    in_field, in_lists = classification_sources(element, "ZoneMembership")
    zones = [_zones_of(c) for c in (in_field[:1] + in_lists[:1])]
    if len(zones) == 2 and sorted(zones[0]) != sorted(zones[1]):
        # Review round 1: fail closed. Egeria answered two zone lists for one element; RE does not pick one.
        raise GatewayError(f"the zones in elementHeader.zoneMembership {zones[0]} and in the classifications list "
                           f"{zones[1]} disagree: its zones are unknown")
    return zones[0] if zones else []


#: The 6.2 ZoneMembershipProperties bean's keys (ZoneMembershipProperties -> ClassificationBeanProperties ->
#: ClassificationProperties, all @JsonInclude(NON_NULL)) plus the `typeName` the answer carries.
_ZONE_BEAN_KEYS = {"class", "typeName", "zoneMembership", "effectiveFrom", "effectiveTo", "extendedProperties"}


def _zones_of(c: dict) -> list[str]:
    """One ZoneMembership classification's zones; raises when they cannot be read."""
    cp = c.get("classificationProperties") or c.get("properties") or {}
    if not isinstance(cp, dict):
        raise GatewayError("a ZoneMembership with unreadable properties: its zones are unknown")
    pvm = cp.get("propertyValueMap") or {}
    if isinstance(pvm, dict) and "zoneMembership" in pvm:
        # Present but null, or an array with no values: Egeria reads the element as unzoned (round 3).
        value = pvm.get("zoneMembership")
        if value is None:
            return []
        if not isinstance(value, dict):
            raise GatewayError("a ZoneMembership whose zone list RE cannot read: its zones are unknown")
        arr_values = value.get("arrayValues")
        if arr_values is None and ("arrayValues" in value or value.get("arrayCount") == 0):
            return []
        arr = arr_values.get("propertiesAsStrings") if isinstance(arr_values, dict) else None
        if isinstance(arr, dict):
            return [str(arr[k]) for k in sorted(arr, key=lambda x: int(x) if str(x).isdigit() else 0)]
        if value.get("arrayCount") == 0:
            return []
        raise GatewayError("a ZoneMembership whose zone list RE cannot read: its zones are unknown")
    if isinstance(cp.get("zoneMembership"), list):
        return [str(z) for z in cp["zoneMembership"]]
    strings = cp.get("propertiesAsStrings")
    if isinstance(strings, dict) and "zoneMembership" in strings:
        raw = strings["zoneMembership"]
        # Null: the array was null and valueAsString of a null array is null. Egeria: unzoned.
        return [] if raw is None else _zone_list_from_string(raw)
    if cp.get("class") == "ZoneMembershipProperties" and set(cp) <= _ZONE_BEAN_KEYS and cp.get("zoneMembership") is None:
        # 6.2's bean drops a null list (@JsonInclude NON_NULL, ZoneMembershipProperties.java:21). Egeria's access
        # connector reads a null list as no zones: getZoneMembership (OpenMetadataAccessSecurityConnector
        # :1869-1883) -> getStringArrayProperty null, and validateZoneAccess checks zones only when
        # `(zoneMembership != null) && (! zoneMembership.isEmpty())` (:1112-1117).
        return []
    raise GatewayError("a ZoneMembership with no zone list RE can read: its zones are unknown")


def parse_initiate_answer(res: Any) -> str:
    """A governance action type's `initiate` answer (a GUIDResponse) -> the ENGINE ACTION's guid."""
    guid = str(res.get("guid") or "") if isinstance(res, dict) else ""
    if not guid or guid == "Action not initiated":
        raise GatewayError("Egeria did not initiate the action")
    return guid


def parse_engine_action_answer(res: Any) -> EngineActionStatus:
    """An EngineAction raw element -> its status. The attribute is `activityStatus` (NOT
    `actionStatus`), seen live. An element without one reads status "" (not stated), never a guess."""
    if not (isinstance(res, dict) and _guid_of(res)):
        raise GatewayError("unrecognised engine action answer from Egeria")
    ps = _strings_of(res)
    # A missing activityStatus is "not stated yet" (about 1.5 s while an action starts), not an error:
    # the callers treat "" as still running.
    return EngineActionStatus(status=str(ps.get("activityStatus") or ""), message=str(ps.get("completionMessage") or ""),
                              completion_time=str(ps.get("completionTime") or ""))


def parse_catalog_targets_answer(res: Any) -> list[CatalogTarget]:
    """`get_catalog_targets`' answer -> targets. LIVE item (rehearsal 2): `elementHeader.guid` is the
    TARGET element, `relatedBy.relationshipHeader.guid` the CatalogTarget relationship and
    `relatedBy.relationshipProperties.catalogTargetName` its name. "No elements found" is an empty
    list; ANY other shape raises, because an empty list here means "not attached" and RE would
    initiate another attach (Egeria creates another CatalogTarget every time)."""
    if _no_elements(res):
        return []
    if not isinstance(res, list):
        raise GatewayError(f"unrecognised catalog targets answer from Egeria: {type(res).__name__}")
    out = []
    for item in res:
        rb = item.get("relatedBy") if isinstance(item, dict) else None
        rel = ((rb or {}).get("relationshipHeader") or {}).get("guid") if isinstance(rb, dict) else ""
        eh = item.get("elementHeader") if isinstance(item, dict) else None
        el = eh.get("guid") if isinstance(eh, dict) else ""
        if not (rel and el):
            raise GatewayError("unrecognised catalog target in Egeria's answer (no relatedBy/elementHeader guids)")
        props = (rb.get("relationshipProperties") or {}) if isinstance(rb, dict) else {}
        out.append(CatalogTarget(relationship_guid=str(rel), element_guid=str(el),
                                 name=str(props.get("catalogTargetName") or "")))
    return out


def _no_elements(res: Any) -> bool:
    return isinstance(res, str) and ("no element" in res.lower() or "not found" in res.lower())


def _no_answer(res: Any, what: str) -> None:
    """A None from a pyegeria read is never "none there". Up to pyegeria 6.2.1, `@dynamic_catch` with
    PYEGERIA_ENABLE_LOGGER_CATCH on turned every failure into None (ISSUE-134); no read RE calls returns None
    for a miss (a miss is Egeria's "No elements found" string). So None is "could not read": it raises."""
    if res is None:
        raise GatewayError(f"Egeria gave no {what} answer (the client returned nothing): unreadable, not absent")


def parse_element_answer(res: Any) -> ElementRead | None:
    """One raw element -> `ElementRead`, or None when Egeria says there is none.

    An answer this does not recognise RAISES: parsing a shape it does not know into "nothing
    there" is how D2 made every element look absent (and would have made every leave-out a soft
    delete)."""
    _no_answer(res, "element")
    if _no_elements(res):
        return None
    if isinstance(res, dict) and _guid_of(res):
        return _element_read(res)
    raise GatewayError(f"unrecognised element answer from Egeria: {type(res).__name__} "
                       f"with keys {sorted(res)[:8] if isinstance(res, dict) else str(res)[:80]}")


def parse_elements_answer(res: Any) -> list[ElementRead]:
    """A list of raw elements -> `[ElementRead]`; "No elements found" -> []; anything else raises."""
    _no_answer(res, "elements")
    if _no_elements(res):
        return []
    if isinstance(res, list):
        out = []
        for x in res:
            if not (isinstance(x, dict) and _guid_of(x)):
                raise GatewayError("unrecognised element in a list answer from Egeria")
            out.append(_element_read(x))
        return out
    raise GatewayError(f"unrecognised elements answer from Egeria: {type(res).__name__}")


def parse_related_answer(res: Any) -> list[Relationship]:
    """`get_all_related_elements`' dict `{startingElement, elementList, mermaidGraph}` ->
    `[Relationship]`. LIVE item keys: `type` (the relationship type), `relationshipGUID`,
    `element`, `elementAtEnd1`. "No elements found" -> []; anything else raises, because an
    empty list here means "nothing hangs off it"."""
    _no_answer(res, "related-elements")
    if _no_elements(res):
        return []
    if isinstance(res, dict) and isinstance(res.get("elementList"), list):
        out = []
        for item in res["elementList"]:
            if not isinstance(item, dict):
                raise GatewayError("unrecognised relationship item from Egeria")
            t = item.get("type")
            rel_type = str(t.get("typeName") or "") if isinstance(t, dict) else ""
            other = item.get("element") if isinstance(item.get("element"), dict) else {}
            rp = item.get("relationshipProperties")
            rps = (rp.get("propertiesAsStrings") if isinstance(rp, dict) else None) or {}
            out.append(Relationship(
                type_name=rel_type, other_guid=_guid_of(other), other_type=_type_of(other),
                guid=str(item.get("relationshipGUID") or ""),
                activity_status=str(rps.get("activityStatus") or ""),
                completion_time=str(rps.get("completionTime") or ""),
                completion_message=(str(_strings_of(other).get("completionMessage") or "")
                                    if rel_type == "ActionTarget" else ""),
                action_kind=(str(_strings_of(other).get("requestType") or "") if rel_type == "ActionTarget" else ""),
                other_qualified_name=_qn_of(other),
                other_name=str(_strings_of(other).get("displayName") or ""),
                properties={str(k): str(v) for k, v in rps.items()} if isinstance(rps, dict) else {},
                other_at_end1=item.get("elementAtEnd1") if isinstance(item.get("elementAtEnd1"), bool) else None))
        return out
    if isinstance(res, dict) and not res:
        return []
    raise GatewayError(f"unrecognised related-elements answer from Egeria: {type(res).__name__} "
                       f"with keys {sorted(res)[:8] if isinstance(res, dict) else str(res)[:80]}")


class PyegeriaCatalogueGateway:
    """The real gateway, over pyegeria. Built lazily: constructing it opens nothing."""

    def __init__(self, db_entity=None, *, view_server: str = "", platform_url: str = "",
                 daemon_server: str = "", identity=None):
        """WHICH Egeria: the arguments, else the entity's stored `egeria_url`/`egeria_server`,
        else the configured one (always under the platform allowlist). WHO: `identity`, else
        `current_principal()` — the signed-in Caller on a route, the daemon in a queued run or the
        background drain (Brief I). An entity's stored Egeria user/password are never read
        (owner's ruling, 2026-10-09)."""
        from resource_explorer.config import get_config
        cfg = get_config().egeria
        e = db_entity
        self.view_server = view_server or getattr(e, "egeria_server", "") or cfg.view_server
        self.platform_url = platform_url or getattr(e, "egeria_url", "") or cfg.platform_url
        self.daemon_server = daemon_server or cfg.integration_daemon_server
        self._identity = identity
        self._clients: dict[str, Any] = {}

    # -- clients ---------------------------------------------------------

    def principal(self):
        if self._identity is None:
            from resource_explorer.egeria_clients import current_principal

            self._identity = current_principal()
        return self._identity

    def _factory(self):
        from resource_explorer.egeria_clients import egeria_client

        return egeria_client(self.principal(), purpose="catalog gateway",
                             view_server=self.view_server, platform_url=self.platform_url)

    def _client(self, name: str):
        if name not in self._clients:
            import pyegeria
            self._clients[name] = self._factory().of(getattr(pyegeria, name))
        return self._clients[name]

    def _server_ops(self):
        if "ServerOps" not in self._clients:
            from pyegeria import ServerOps
            self._clients["ServerOps"] = self._factory().of(ServerOps, server=self.daemon_server)
        return self._clients["ServerOps"]

    def _surveyor(self):
        from resource_explorer.surveyors.database.egeria_database_surveyor import EgeriaDatabaseSurveyor
        if "surveyor" not in self._clients:
            self._clients["surveyor"] = EgeriaDatabaseSurveyor(
                platform_url=self.platform_url, view_server=self.view_server,
                identity=self.principal())
        return self._clients["surveyor"]

    # -- step 1 ----------------------------------------------------------

    def publish_database(self, db_entity, db_user: str, db_pwd: str, *, registry=None,
                         submitted_by: str = "", reinclude_guid: str = "") -> PublishedDatabase:
        """The server and database elements, as Classic's publish did, but with no survey. `reinclude_guid`
        (S19) is the archived database copy the press confirmed; the surveyor re-checks it before any create."""
        try:
            res = self._surveyor().catalog_and_survey(
                db_entity, db_user, db_pwd, registry=registry, survey_after_catalog=False,
                submitted_by=submitted_by, reinclude_guid=reinclude_guid)
        except Exception as exc:
            raise GatewayError(_short(exc)) from exc
        server = server_name_for(db_entity)
        # The server is read by its EXACT qualifiedName, never by a name search and never from a stored
        # variable (rehearsal 2, D-E: a repeat commit printed the database's own guid as the server).
        server_guid = self.find_server(server)
        return PublishedDatabase(
            server_guid=server_guid, database_guid=res.get("database_guid", ""),
            server_name=server, database_qualified_name=database_qualified_name(server, db_entity.database_name))

    def find_server(self, server_name: str) -> str:
        """The one SoftwareServer whose qualifiedName is exactly `PostgreSQL Server::<host:port>` (as RE's
        publish created it in rehearsal 1). None reads "server not found", several read "server ambiguous ·
        N matches": the commit does not guess. A server element is shared by host:port."""
        qn = server_qualified_name(server_name)
        body = {"class": "SearchStringRequestBody", "searchString": qn, "startsWith": True, "ignoreCase": False,
                "forLineage": True, "forDuplicateProcessing": True, "graphQueryDepth": 0}
        try:
            res = self._client("MetadataExpert").find_metadata_elements_with_string(
                search_string=qn, starts_with=True, body=body)
            found = [e for e in parse_elements_answer(res) if e.qualified_name == qn]
        except GatewayError:
            raise
        except Exception as exc:
            if "No elements found" in str(exc):
                found = []
            else:
                raise GatewayError(f"could not look for the server {qn}: {_short(exc)}") from exc
        if not found:
            raise GatewayError(f"server not found: no element has the qualifiedName {qn}")
        if len(found) > 1:
            raise GatewayError(f"server ambiguous · {len(found)} matches for {qn}; Resource Explorer will not guess")
        return found[0].guid

    def publish_local_report(self, db_entity, db_user: str, db_pwd: str, measured: dict, *,
                             registry=None, submitted_by: str = "") -> dict:
        import json as _json
        data = _json.loads(measured.get("survey_data") or "{}")
        try:
            return self._surveyor().publish_local_survey(
                db_entity=db_entity, schema_info=data.get("schema_info", {}),
                schema_count=measured.get("schema_count", 0), table_count=measured.get("table_count", 0),
                column_count=measured.get("column_count", 0), surveyed_at=measured.get("surveyed_at", ""),
                registry=registry, db_user=db_user, db_pwd=db_pwd,
                statistics=data.get("statistics", {}), submitted_by=submitted_by,
                survey_after_catalog=False, declared_measures=True)
        except Exception as exc:
            raise GatewayError(_short(exc)) from exc

    def set_zone_membership(self, guid: str, zones: list[str]) -> bool:
        """Put the element in `zones`. Egeria's security connector REJECTS a zone change whose
        before and after are equal (see `egeria_identity.current_zones`), so this reads first: an
        empty read means "could not tell", never "no zones". A refusal RAISES with Egeria's own
        word (the caller reports it and never retries it blindly)."""
        from resource_explorer.egeria_identity import current_zones, zone_membership_body
        have = current_zones(guid)
        if have and sorted(have) == sorted(zones):
            return True
        try:
            self._client("ClassificationExplorer").add_zone_membership(guid, zone_membership_body(zones))
        except Exception as exc:
            raise GatewayError(_short(exc)) from exc
        return True

    def read_zones(self, guid: str) -> list[str]:
        """The element's zones as Egeria holds them now: `[]` is "no ZoneMembership" (read from the
        raw classifications), and an unreadable answer RAISES, never `[]`."""
        try:
            el = self._client("MetadataExpert").get_metadata_element_by_guid(guid)
        except Exception as exc:
            raise GatewayError(f"could not read {guid[:8]}: {_short(exc)}") from exc
        return zones_of_element(el)

    def engine_action_status(self, guid: str) -> EngineActionStatus:
        try:
            el = self._client("MetadataExpert").get_metadata_element_by_guid(guid)
        except Exception as exc:
            raise GatewayError(f"could not read engine action {guid[:8]}: {_short(exc)}") from exc
        return parse_engine_action_answer(el)

    def survey_outcome(self, database_guid: str, engine_action_guid: str, since: str) -> SurveyOutcome:
        """The engine action's status and message, and the newest survey report created at or
        after `since` with its annotation count.

        The engine action's `activityStatus`/`completionMessage` are the names seen live (read-back
        of 2026-10-05). UNVERIFIED LIVE: the report-by-time pick; a report not seen comes back as
        no report, which the commit shows as "submitted", never as done."""
        from resource_explorer.surveyors.egeria_survey_reader import (
            get_survey_reports_by_guid, reported_annotations)
        out = SurveyOutcome()
        try:
            el = self._client("MetadataExpert").get_metadata_element_by_guid(engine_action_guid)
        except Exception as exc:
            raise GatewayError(f"could not read engine action {engine_action_guid[:8]}: {_short(exc)}") from exc
        st = parse_engine_action_answer(el)
        out.action_status, out.message = st.status, st.message
        reports = [r for r in get_survey_reports_by_guid(self._client("AssetMaker"), database_guid)
                   if str(r.get("surveyed_at") or "")[:19] >= str(since or "")[:19] and r.get("guid")]
        if reports:
            newest = reports[0]                       # the reader sorts newest first
            out.report_guid = newest["guid"]
            try:
                # Paged to the end of the report's ReportedAnnotation list: one graph read holds one page (999 live).
                out.annotations = len(reported_annotations(self._client("MetadataExpert"), newest["guid"]))
            except Exception as exc:
                raise GatewayError(f"could not read survey report {newest['guid'][:8]}: {_short(exc)}") from exc
        return out

    def set_owner(self, guid: str, owner: str, *, person: bool = False) -> tuple[str, str]:
        """Add Ownership after read-back. ('set' | 'already' | 'refused', detail).

        `person=True` (the requester): `owner` is a person's userId, and Ownership names their
        profile when Egeria links one to their UserIdentity (7h, `person_ownership_body`). A
        Context-declared owner (free text) keeps the plain form."""
        from resource_explorer.egeria_identity import ownership_body, person_ownership_body

        if person:
            body, shape = person_ownership_body(owner)
        else:
            body, shape = ownership_body(owner), None
        named = body["properties"]["owner"]
        try:
            element = self._client("MetadataExpert").get_metadata_element_by_guid(guid)
        except Exception as exc:
            raise GatewayError(f"could not read {guid} before classifying: {_short(exc)}") from exc
        owned = classifications_named(element, "Ownership")      # 6.2's elementHeader.ownership first
        existing = owned[-1] if owned else None
        if existing is not None:
            props = existing.get("classificationProperties") or {}
            current = ""
            vm = props.get("propertyValueMap") or {}
            if isinstance(vm.get("owner"), dict):
                current = str(vm["owner"].get("primitiveValue") or "")
            current = current or str((props.get("propertiesAsStrings") or {}).get("owner") or "")
            current = current or str(props.get("owner") or "")
            if current == named:
                return "already", f"Ownership already names {named}"
        note = f" ({shape.note})" if shape is not None and shape.note else ""
        try:
            self._client("ClassificationExplorer").add_ownership_to_element(guid, body)
            return "set", f"Ownership set to {named}{note}"
        except Exception as exc:
            if existing is not None:
                return "refused", _short(exc)
            raise GatewayError(_short(exc)) from exc

    # -- schema elements and targets ---------------------------------------

    def read_element(self, qualified_name: str, *, for_lineage: bool = False) -> ElementRead | None:
        body = {"class": "UniqueNameRequestBody", "name": qualified_name,
                "namePropertyName": "qualifiedName", "forLineage": for_lineage,
                "forDuplicateProcessing": for_lineage}
        try:
            res = self._client("MetadataExpert").get_metadata_element_by_unique_name(
                name=qualified_name, property_name="qualifiedName", body=body)
        except Exception as exc:
            # Absence from the exception's TYPE and code (`egeria_absence.is_absent`), never its text: a
            # timeout or a 401 whose message names "404" or "not found" used to read as absent, so an attach
            # created the schema and a leave-out wrote a "gone" proof for an element nobody could read.
            from resource_explorer.egeria_absence import lookup_exception_is_absent
            if lookup_exception_is_absent(exc):
                return None
            raise GatewayError(_short(exc)) from exc
        return parse_element_answer(res)

    def create_schema_element(self, db_entity, schema: str, database_guid: str, *,
                              description: str = "") -> str:
        """The DeployedDatabaseSchema, from the template, under the database element.

        The parent link is the JDBC cataloguer's OWN: `RelationalDatabaseCataloguer.getOrCreateSchema`
        (egeria `RelationalDatabaseCataloguer.java:255-259`) anchors the schema to the database and
        parents it by DataSetContent with the database at END 2 (`setParentAtEnd1(false)`). Rehearsal
        2 sent the database at end 1 and Egeria rejected it (OMRS-REPOSITORY-400-047: end 1 must be a
        DataSet), still creating the element unparented. Same element for the same placeholders as
        Egeria's own process, by qualifiedName
        `PostgreSQL Relational Database Schema::<host:port>::<db>.<schema>`."""
        body = {
            "class": "TemplateRequestBody",
            "templateGUID": SCHEMA_TEMPLATE_GUID,
            "isOwnAnchor": False,
            "anchorGUID": database_guid,
            "parentGUID": database_guid,
            "parentRelationshipTypeName": SCHEMA_PARENT_RELATIONSHIP,
            "parentAtEnd1": SCHEMA_PARENT_AT_END1,
            "deepCopy": True,
            "placeholderPropertyValues": schema_placeholders(db_entity, schema, description),
        }
        try:
            guid = self._client("AutomatedCuration").create_elem_from_template(body)
        except Exception as exc:
            raise GatewayError(f"creating the schema element for {schema} from the template failed: "
                               f"{_short(exc)}") from exc
        return guid if isinstance(guid, str) else _guid_of(guid)

    def qualified_name_of(self, guid: str) -> str:
        """The qualifiedName Egeria holds for `guid`, read by GUID. Raises GatewayError when it
        cannot be read (never '' for "could not tell")."""
        try:
            el = self._client("MetadataExpert").get_metadata_element_by_guid(guid)
        except Exception as exc:
            raise GatewayError(f"could not read {guid[:8]}: {_short(exc)}") from exc
        qn = _qn_of(el)
        if not qn:
            raise GatewayError(f"no qualifiedName on the answer for {guid[:8]}")
        return qn

    def mark_on_behalf(self, guid: str, requester: str, owner: str) -> str:
        """Brief I: a template copy cannot carry `additionalProperties`, so `requestedBy` is merged
        into its CURRENT map after the create (read, merge, write; UNVERIFIED LIVE), and Ownership
        names `owner` (`set_owner`). '' when both landed (or nobody asked), else
        "requester not recorded (<reason>)" for the caller to report as partial. Never raises."""
        from resource_explorer.egeria_identity import OnBehalf, record_requested_by

        if not requester:
            return ""
        reasons = []
        why = record_requested_by(guid, OnBehalf(requester=requester, owner=owner),
                                  client=self._client("MetadataExpert"))
        if why:
            reasons.append(f"requestedBy: {why}")
        if owner:
            try:
                outcome, detail = self.set_owner(guid, owner, person=True)
                if outcome == "refused":
                    reasons.append(f"Ownership refused: {detail}")
            except GatewayError as exc:
                reasons.append(f"Ownership: {_short(exc, 200)}")
        return f"requester not recorded ({'; '.join(reasons)})" if reasons else ""

    def link_database_to_server(self, server_guid: str, database_guid: str, *, server_name: str = "",
                                write: bool = True) -> "ServerLink":
        """Relate the database to its server's DatabaseManager (`CapabilityAssetUse`, OWNS), reading first and
        never creating a second link (`database_server_link`). Never raises; the outcome says what happened."""
        from resource_explorer import database_server_link as dsl

        try:
            expert, maker = self._client("MetadataExpert"), self._client("AssetMaker")
        except Exception as exc:  # noqa: BLE001 - a client that cannot be built is "could not check"
            return dsl.ServerLink(dsl.COULD_NOT_CHECK, f"could not check ({_short(exc)}) · not linked")
        return dsl.link_database_to_server(expert, maker, server_guid=server_guid, database_guid=database_guid,
                                           server_label=server_name, write=write)

    def link_published_database(self, db_entity, *, write: bool = True) -> "ServerLink":
        """The heal's one door: find the server and the database by their EXACT qualifiedNames
        (`egeria_lookup`: absent is None, a failed lookup raises and is "could not check", never a create), check
        that the element RE points at is the one Egeria holds under that name, then link."""
        from resource_explorer import database_server_link as dsl
        from resource_explorer.egeria_lookup import guid_by_qualified_name

        server_name = server_name_for(db_entity)
        server_qn = server_qualified_name(server_name)
        db_qn = database_qualified_name(server_name, db_entity.database_name)
        try:
            expert = self._client("MetadataExpert")
            # typed: an element of another type holding the name raises, which reads "could not check", so a
            # wrong-type element is never a link target.
            server_guid = guid_by_qualified_name(expert, server_qn, type_name="SoftwareServer")
            db_guid = guid_by_qualified_name(expert, db_qn, type_name="RelationalDatabase")
        except Exception as exc:  # noqa: BLE001 - any failed lookup is "could not check"
            return dsl.ServerLink(dsl.COULD_NOT_CHECK, f"could not check ({_short(exc)}) · not linked")
        if not server_guid:
            return dsl.ServerLink(dsl.NOT_LINKED, f"not linked: no server element named {server_qn}")
        if not db_guid:
            return dsl.ServerLink(dsl.NOT_LINKED, f"not linked: no database element named {db_qn}")
        pointer = (getattr(db_entity, "egeria_asset_guid", "") or "").strip()
        if pointer and pointer != db_guid:
            return dsl.ServerLink(dsl.NOT_LINKED, f"not linked: RE points at {pointer[:8]} but Egeria holds "
                                                  f"{db_guid[:8]} under that name")
        return self.link_database_to_server(server_guid, db_guid, server_name=server_name, write=write)

    def initiate_catalog_action(self, schema_guid: str, request_parameters: dict[str, str]) -> str:
        """Egeria's own attach: `PostgreSQLGovernance::catalog-postgres-schema` with the schema
        element as action target `newAsset`. Returns the ENGINE ACTION's guid (the attach happens
        afterwards, on Egeria's side; the caller reads the target back)."""
        return self._initiate_action_type(
            CATALOG_SCHEMA_ACTION_TYPE,
            [{"class": "NewActionTarget", "actionTargetName": "newAsset", "actionTargetGUID": schema_guid.strip()}],
            request_parameters)

    def _initiate_action_type(self, qualified_name: str, action_targets: list[dict],
                              request_parameters: dict[str, str]) -> str:
        ac = self._client("AutomatedCuration")
        body = {"class": "InitiateGovernanceActionTypeRequestBody",
                "governanceActionTypeQualifiedName": qualified_name,
                "actionTargets": action_targets, "requestParameters": dict(request_parameters)}
        url = f"{ac.ref_curation_command_base}/governance-action-types/initiate"
        try:
            res = ac.make_request("POST", url, body).json()
        except Exception as exc:
            raise GatewayError(_short(exc)) from exc
        return parse_initiate_answer(res)

    def list_catalog_targets(self) -> list[CatalogTarget]:
        """Read the cataloguer's targets FIRST, with the body pyegeria's default lacks."""
        try:
            res = self._client("AssetMaker").get_catalog_targets(
                JDBC_CATALOGUER_GUID, body={"class": "ResultsRequestBody", "graphQueryDepth": 0})
        except Exception as exc:
            raise GatewayError(_short(exc)) from exc
        return parse_catalog_targets_answer(res)

    def add_catalog_target(self, element_guid: str, name: str) -> str:
        """A SCHEMA-kind target: no configuration lists, `deleteMethod` ARCHIVE."""
        body = {"class": "NewRelationshipRequestBody",
                "properties": {"class": "CatalogTargetProperties", "catalogTargetName": name,
                               "deleteMethod": TARGET_DELETE_METHOD}}
        try:
            res = self._client("AssetMaker").add_catalog_target(JDBC_CATALOGUER_GUID, element_guid, body)
        except Exception as exc:
            raise GatewayError(_short(exc)) from exc
        return res if isinstance(res, str) else str((res or {}).get("guid") or "")

    def remove_catalog_target(self, relationship_guid: str) -> None:
        body = {"class": "DeleteRelationshipRequestBody", "forLineage": True, "forDuplicateProcessing": True}
        flags = "forLineage=True forDuplicateProcessing=True"
        log.info("egeria write BEFORE: remove_catalog_target relationship=%s type=CatalogTarget flags: %s",
                 relationship_guid, flags)
        try:
            self._client("AssetMaker").remove_catalog_target(relationship_guid, body)
        except Exception as exc:
            log.warning("egeria write AFTER: remove_catalog_target relationship=%s outcome=failed: %s",
                        relationship_guid, _short(exc))
            raise GatewayError(_short(exc)) from exc
        log.info("egeria write AFTER: remove_catalog_target relationship=%s outcome=ok", relationship_guid)

    # -- reads under a schema ---------------------------------------------

    def elements_under(self, qualified_name_prefix: str) -> list[ElementRead]:
        body = {"class": "SearchStringRequestBody", "searchString": qualified_name_prefix,
                "startsWith": True, "ignoreCase": False, "forLineage": True,
                "forDuplicateProcessing": True, "graphQueryDepth": 0}
        try:
            res = self._client("MetadataExpert").find_metadata_elements_with_string(
                search_string=qualified_name_prefix, starts_with=True, body=body)
        except Exception as exc:
            if "No elements found" in str(exc):
                return []
            raise GatewayError(_short(exc)) from exc
        return [e for e in parse_elements_answer(res) if e.qualified_name.startswith(qualified_name_prefix)]

    def archived_siblings(self, qualified_name: str, *, type_name: str = "") -> list[ArchivedSibling]:
        """S19: the archived copies of `qualified_name`, found by the name Egeria gives them on archive,
        `<qualified_name>_archivedOn_<date>`, newest first, every page read. A read that cannot be made raises
        `SiblingLookupFailed`: never "no archived copy", so nothing is created on it.

        `type_name` is the element's own type when the caller knows it (a leave-out's walk does). Without it the
        type is guessed from the name's template prefix (`archived_sibling_type`), which is right only for a schema
        or a database: every element under a schema carries the schema's prefix, so a column's copy searched as a
        DeployedDatabaseSchema is never found (the 2026-10-25 post-reset re-proof, `widgets::made_on`)."""
        try:
            client = self._client("MetadataExpert")
        except Exception as exc:
            raise SiblingLookupFailed(_short(exc, 200)) from exc
        return find_archived_siblings(client, qualified_name, type_name=type_name)

    def schema_elements(self, prefix: str) -> list[ElementRead]:
        """Every DeployedDatabaseSchema named `<prefix>...`, live AND archived (forLineage), in ONE paged find:
        the preview builds every schema's answer from it instead of reading each schema by name."""
        try:
            client = self._client("MetadataExpert")
        except Exception as exc:
            raise SiblingLookupFailed(_short(exc, 200)) from exc
        raw = find_all_starting_with(client, prefix, type_name="DeployedDatabaseSchema")
        return [_element_read(x) for x in raw if _qn_of(x).startswith(prefix)]

    def relationships(self, guid: str) -> list[Relationship]:
        body = {"class": "ResultsRequestBody", "graphQueryDepth": 0,
                "forLineage": True, "forDuplicateProcessing": True}
        try:
            res = self._client("MetadataExpert").get_all_related_elements(guid=guid, body=body)
        except Exception as exc:
            if "No elements found" in str(exc):
                return []
            raise GatewayError(_short(exc)) from exc
        return parse_related_answer(res)

    def find_endpoints(self, search: str) -> list[EndpointRead]:
        """Every Endpoint element whose strings contain `search` (D1-a: `:<port>/<database>`), every page read.
        The caller parses each `networkAddress` and keeps only its own endpoint: the find is a net, not the answer.
        A read that cannot be made raises `GatewayError`, never "no endpoint"."""
        try:
            raw = find_all_starting_with(self._client("MetadataExpert"), search, type_name="Endpoint",
                                         starts_with=False)
        except Exception as exc:
            raise GatewayError(_short(exc)) from exc
        return [EndpointRead(_guid_of(x), _qn_of(x), str(_strings_of(x).get("networkAddress") or "")) for x in raw]

    def link_data_set_content(self, data_set_guid: str, content_guid: str) -> None:
        """D1-b: ONE DataSetContent relationship, end 1 the data set and end 2 its content (Egeria's type def and
        AssetMaker's `data-sets/{dataSetGUID}/data-set-content/{dataContentAssetGUID}/attach`). pyegeria's wrapper
        returns None, not the new relationship's GUID: the caller reads the relationship back to learn it. A refusal
        raises `GatewayError` with Egeria's words; nothing is retried here."""
        log.info("egeria write BEFORE: link_data_set_content data_set=%s content=%s type=DataSetContent",
                 data_set_guid, content_guid)
        try:
            self._client("AssetMaker").link_data_set_content(data_set_guid, content_guid, None)
        except Exception as exc:
            log.warning("egeria write AFTER: link_data_set_content data_set=%s content=%s outcome=failed: %s",
                        data_set_guid, content_guid, _short(exc))
            raise GatewayError(_short(exc)) from exc
        log.info("egeria write AFTER: link_data_set_content data_set=%s content=%s outcome=ok", data_set_guid,
                 content_guid)

    def delete_element(self, guid: str, form: str, *, slug: str, scope: str = "") -> None:
        """One element, never a cascade. `slug` is the database the delete acts for and `scope` the path it is on:
        the archive-cascade block is lifted for `LEAVE_OUT_ARCHIVE` (an ARCHIVE only; a soft delete keeps the block
        whatever scope it names), else for one named database only. An archive is the delete endpoint with
        ARCHIVE and forLineage and forDuplicateProcessing true (the archive
        endpoint answers 500 on this build)."""
        method = "ARCHIVE" if form == ARCHIVE else "SOFT_DELETE"
        flags = "deleteMethod=%s forLineage=True forDuplicateProcessing=True cascade_delete=False" % method
        if issue_117_blocked(slug, scope=scope if method == "ARCHIVE" else ""):
            log.warning("egeria write refused by archive-cascade block: %s element=%s slug=%r scope=%r flags: %s",
                        method, guid, slug, scope, flags)
            raise GatewayError(ISSUE_117_WORDS)          # before any client is built: nothing is sent
        body = {"class": "DeleteElementRequestBody", "deleteMethod": method,
                "forLineage": True, "forDuplicateProcessing": True}
        log.info("egeria write BEFORE: %s element=%s flags: %s", method, guid, flags)
        try:
            self._client("MetadataExpert").delete_metadata_element(guid, body, cascade_delete=False)
        except Exception as exc:
            log.warning("egeria write AFTER: %s element=%s outcome=failed: %s", method, guid, _short(exc))
            raise GatewayError(_short(exc)) from exc
        log.info("egeria write AFTER: %s element=%s outcome=ok", method, guid)

    # -- survey and connector ---------------------------------------------

    def initiate_survey(self, database_guid: str, request_parameters: dict[str, str]) -> tuple[str, str]:
        """The native database survey, with request parameters (RE's own helper cannot pass any)."""
        from resource_explorer.surveyors.technology_type_processes import KIND_SURVEY_EXISTING, get_process_by_kind
        native = get_process_by_kind("database", DATABASE_TECH_TYPE, KIND_SURVEY_EXISTING)
        if not native:
            raise GatewayError("no native survey process is configured for PostgreSQL Relational Database")
        guid = self._initiate_action_type(
            native.qualified_name,
            [{"class": "NewActionTarget", "actionTargetName": "serverToSurvey", "actionTargetGUID": database_guid.strip()}],
            request_parameters)
        return guid, native.qualified_name

    def connector_status(self) -> ConnectorStatus | None:
        try:
            status = self._server_ops().get_integration_daemon_status(self.daemon_server)
        except Exception as exc:
            raise GatewayError(_short(exc)) from exc
        return parse_connector_status(status, JDBC_CATALOGUER_NAME)

    def refresh_connector(self, timeout: int = 120) -> None:
        try:
            self._server_ops().refresh_integration_connectors(
                JDBC_CATALOGUER_NAME, self.daemon_server, timeout)
        except Exception as exc:
            raise GatewayError(_short(exc)) from exc


def parse_connector_status(status: Any, connector_name: str) -> ConnectorStatus | None:
    """Find one connector in the integration daemon's status, tolerantly (the keys
    were not established live). The time is the CONNECTOR's, never a target's."""
    def walk(x):
        if isinstance(x, dict):
            yield x
            for v in x.values():
                yield from walk(v)
        elif isinstance(x, list):
            for v in x:
                yield from walk(v)
    for d in walk(status):
        name = d.get("connectorName") or d.get("name")
        if name == connector_name and ("lastRefreshTime" in d or "connectorStatus" in d):
            return ConnectorStatus(name=str(name), status=str(d.get("connectorStatus") or ""),
                                   last_refresh_time=str(d.get("lastRefreshTime") or ""))
    return None


def like_matches(pattern: str, name: str) -> bool:
    """Would JDBC's metadata calls, given `pattern`, also return `name`?

    The cataloguer passes real names to `getTables`/`getColumns`/`getSchemas` as
    patterns: `_` matches any one character and `%` any run. Case-sensitive, as
    the evidence found (`aXb` came back under `a_b`)."""
    rx = "".join("." if ch == "_" else ".*" if ch == "%" else re.escape(ch) for ch in pattern)
    return re.fullmatch(rx, name, flags=re.DOTALL) is not None
