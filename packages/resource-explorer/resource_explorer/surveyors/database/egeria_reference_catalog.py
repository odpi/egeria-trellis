"""Read the Data Classes and Valid Value Sets the Egeria platform already
holds, and publish proposals for the ones it does not.

Phase 1 slice 10. Backs `data_class_match` and `reference_data_match`
(`docs/multi-resource-questions-design.md` §5.4) and the proposal convention
in `docs/egeria-support-for-multi-resource.md` §3, as revised by the corrected
probes 4 and 5 of 2026-09-21 (`PROBES-2026-09-21.md`).

**`contentStatus: DRAFT`, not `initialStatus`.** Probe 4's first pass tested
`initialStatus`, which sets Egeria's generic *entity lifecycle* status
(`ElementStatus`) and is silently dropped by pyegeria's `NewElementRequestBody`
anyway (pyegeria ISSUE-113). The mechanism §3 actually meant is
`contentStatus` — a domain property on `AuthoredReferenceableProperties`,
which `DataClassProperties` and `ValidValueDefinitionProperties` both inherit.
It was verified live on 2026-09-21 to round-trip as `DRAFT` on both a
`DataClass` and a `DataClassAnnotation`, with the element's own `ElementStatus`
staying `ACTIVE` throughout. Every proposal this module creates sets
`properties.contentStatus = "DRAFT"` and nothing sets `initialStatus`.

**Read before you conclude.** `list_known_data_classes` /
`list_known_valid_value_sets` return a `ReferenceCatalog` whose `available`
flag is False when the platform could not be read at all. That is the
difference between "the platform holds no Data Classes" (a fact, and a reason
to propose) and "we could not ask" (not a fact, and `column_matching` turns it
into `MATCH_NO_CANDIDATES` rather than a clean "no match"). A caller that
treats an empty list as the former when it is the latter reports every PII
column as unmatched.

No live Egeria in the build environment, so every function here is exercised
against a duck-typed fake client in
`tests/test_postgres_column_profile.py`; the request-body shapes are taken
from pyegeria's own REST reference files rather than guessed. See
`POSTGRES-COLUMN-PROFILE-IMPLEMENTED.md`.
"""
from __future__ import annotations

import logging
import os
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from resource_explorer.surveyors.database.column_matching import (
    ColumnMatch,
    KnownDataClass,
    KnownValidValueSet,
)
from resource_explorer.config import egeria_platform_url, egeria_view_server  # noqa: E402,F401

log = logging.getLogger(__name__)

#: The value `contentStatus` takes for an unconfirmed proposal. Egeria's
#: `ContentStatus.DRAFT` — "the content is incomplete".
CONTENT_STATUS_DRAFT = "DRAFT"

#: Egeria type name for a Data Class, as it appears in an element's
#: `elementHeader.type.typeName`. Needed because pyegeria has no
#: `find_data_classes`: a Data Class is a subtype of `DataValueSpecification`
#: and shares that endpoint, so the results must be filtered by type here.
DATA_CLASS_TYPE_NAME = "DataClass"

#: Egeria type names a READ accepts as a reference-data *set*. On Egeria 6.2 a set is a plain
#: `ValidValueDefinition` carrying members: 6.2 knows no `ValidValueSet` / `ValidValuesSet` type
#: (live probe, 2026-10-13: OMAG-COMMON-400-018 on findElements). Both spellings stay in this
#: read-side list only because `valid_value_set_from_element` reads them from whatever an element
#: says; nothing RE writes uses them.
VALID_VALUE_SET_TYPE_NAMES = ("ValidValueSet", "ValidValuesSet", "ValidValueDefinition")

#: The type every RE `find_valid_value_definitions` passes as `metadata_element_type`. Egeria's
#: Reference Data view defaults an untyped find to `ReferenceDataValue` (egeria
#: `ReferenceDataInstance.java:65-69`), so without it the plain `ValidValueDefinition`s RE's
#: bootstrap creates (keyword sets and keywords) are never returned.
VALID_VALUE_DEFINITION_TYPE_NAME = "ValidValueDefinition"

#: qualifiedName prefixes for the elements this module proposes. Distinct
#: prefixes so a proposal is identifiable as RE's own, and so the idempotency
#: lookup (`get_guid_for_name`) cannot collide with a curator's own element.
PROPOSED_DATA_CLASS_PREFIX = "DataClass::ResourceExplorer::proposed"
PROPOSED_VALID_VALUE_SET_PREFIX = "ValidValueDefinition::ResourceExplorer::proposed"


class ReferenceCatalogError(RuntimeError):
    """Raised only where a caller must not proceed — never for "the platform
    holds none of these", which is a legitimate answer carried on
    `ReferenceCatalog.available`."""


@dataclass
class ReferenceCatalog:
    """What the platform holds, and whether we managed to ask.

    `available=False` with an empty list is the state that must never be
    rendered as "there are none". The `unavailable_reason` is carried through
    into `MATCH_NO_CANDIDATES`'s prose so a screen can say *why* nothing was
    established.
    """

    data_classes: list[KnownDataClass] = field(default_factory=list)
    valid_value_sets: list[KnownValidValueSet] = field(default_factory=list)
    available: bool = True
    unavailable_reason: str = ""

    @property
    def draft_data_classes(self) -> list[KnownDataClass]:
        return [c for c in self.data_classes if c.is_draft]

    def as_dict(self) -> dict[str, Any]:
        return {
            "available": self.available,
            "unavailable_reason": self.unavailable_reason,
            "data_class_count": len(self.data_classes),
            "draft_data_class_count": len(self.draft_data_classes),
            "valid_value_set_count": len(self.valid_value_sets),
        }


# ── Reading ───────────────────────────────────────────────────────────────────

def _as_tuple(value: Any) -> tuple[str, ...]:
    """Normalise an Egeria list-ish property to a tuple of strings.

    Egeria returns a list for a populated array property, omits the key
    entirely when empty, and — for some output formats — returns a
    comma-joined string. All three arrive here; only the first is documented.
    """
    if value is None:
        return ()
    if isinstance(value, str):
        return tuple(p.strip() for p in value.split(",") if p.strip())
    if isinstance(value, (list, tuple, set)):
        return tuple(str(v).strip() for v in value if str(v).strip())
    return (str(value),)


def _element_type_name(element: dict) -> str:
    header = element.get("elementHeader") or {}
    type_info = header.get("type") or {}
    return str(type_info.get("typeName") or element.get("typeName") or "")


def _element_guid(element: dict) -> str:
    header = element.get("elementHeader") or {}
    return str(header.get("guid") or element.get("guid") or "")


def data_class_from_element(element: dict) -> KnownDataClass | None:
    """Reduce one Egeria element to a `KnownDataClass`, or `None` if it is not
    one.

    Returning `None` for a non-DataClass rather than filtering upstream keeps
    the type check in one place: `find_data_value_specifications` returns every
    `DataValueSpecification` subtype, and the set of subtypes is Egeria's to
    grow.
    """
    if not isinstance(element, dict):
        return None
    if _element_type_name(element) != DATA_CLASS_TYPE_NAME:
        return None
    props = element.get("properties") or {}
    qualified_name = str(props.get("qualifiedName") or "")
    if not qualified_name:
        # An element with no qualifiedName cannot be matched against
        # idempotently or referred to in a finding. Skipped rather than
        # matched under a blank name.
        return None
    return KnownDataClass(
        guid=_element_guid(element),
        qualified_name=qualified_name,
        display_name=str(props.get("displayName") or qualified_name),
        namespace_path=str(props.get("namespacePath") or ""),
        specification=str(props.get("specification") or ""),
        data_patterns=_as_tuple(props.get("dataPatterns")),
        data_type=str(props.get("dataType") or ""),
        match_property_names=_as_tuple(props.get("matchPropertyNames")),
        value_list=_as_tuple(props.get("valueList")) or _as_tuple(props.get("sampleValues")),
        content_status=str(props.get("contentStatus") or ""),
    )


def _member_values(element: dict) -> tuple[str, ...]:
    """Pull a set's member `preferredValue`s out of a graph-query result.

    pyegeria has no `get_valid_value_members`: membership comes back inside
    `get_valid_value_definition_by_guid(..., graph_query_depth=N)`'s related
    elements, and the key it arrives under differs by output format. Every
    known shape is tried, and a set whose members could not be read ends up
    with an empty `values` tuple — which `reference_data_match` treats as "no
    member values readable", not as an empty set.
    """
    values: list[str] = []
    for key in ("members", "validValueMembers", "relatedElements", "memberOf"):
        related = element.get(key)
        if not isinstance(related, (list, tuple)):
            continue
        for member in related:
            if not isinstance(member, dict):
                continue
            props = member.get("properties") or member
            value = props.get("preferredValue") or props.get("displayName")
            if value:
                values.append(str(value).strip())
    # Some formats put the values straight on the set.
    values.extend(_as_tuple((element.get("properties") or {}).get("valueList")))
    return tuple(dict.fromkeys(v for v in values if v))


def valid_value_set_from_element(element: dict) -> KnownValidValueSet | None:
    if not isinstance(element, dict):
        return None
    if _element_type_name(element) not in VALID_VALUE_SET_TYPE_NAMES:
        return None
    props = element.get("properties") or {}
    qualified_name = str(props.get("qualifiedName") or "")
    if not qualified_name:
        return None
    return KnownValidValueSet(
        guid=_element_guid(element),
        qualified_name=qualified_name,
        display_name=str(props.get("displayName") or qualified_name),
        values=_member_values(element),
        content_status=str(props.get("contentStatus") or ""),
    )


def _results(raw: Any) -> list[dict]:
    """pyegeria returns a list for JSON output and a string ("no elements
    found", or a rendered report) otherwise. A string is not zero results and
    is not results either — the caller must know the difference, so this
    raises rather than returning [].
    """
    if isinstance(raw, list):
        return [r for r in raw if isinstance(r, dict)]
    if isinstance(raw, dict):
        return [raw]
    raise ReferenceCatalogError(
        f"Expected a list of elements from pyegeria, got {type(raw).__name__}: "
        f"{str(raw)[:200]!r} — an empty result cannot be distinguished from a "
        f"rendered report, so this is reported rather than read as 'none'."
    )


def list_known_data_classes(designer, page_size: int = 500) -> ReferenceCatalog:
    """Every `DataClass` the platform holds (§5.4's "against every Egeria
    `DataClass` already in the platform").

    `DataDesigner` has no `find_data_classes` — verified against pyegeria's
    source: a `DataClass` is a subtype of `DataValueSpecification` and shares
    its endpoint, so this searches `find_data_value_specifications("*")` and
    filters by `typeName`. Getting this wrong is silent: a
    `find_data_classes` that does not exist raises `AttributeError`, but a
    search that forgets to filter returns Data Structures and Data Fields and
    would match columns against them.
    """
    try:
        raw = designer.find_data_value_specifications(
            "*", starts_with=False, page_size=page_size, output_format="JSON",
        )
    except Exception as exc:
        log.warning("Could not list Data Classes from Egeria: %s", exc)
        return ReferenceCatalog(
            available=False,
            unavailable_reason=f"the Data Class search failed: {exc}",
        )
    try:
        elements = _results(raw)
    except ReferenceCatalogError as exc:
        # pyegeria's "no elements found" string lands here. That IS an empty
        # platform, so it is reported as available-and-empty; anything else is
        # unreadable.
        text = str(raw).lower() if not isinstance(raw, (list, dict)) else ""
        if "no " in text and "found" in text:
            return ReferenceCatalog(data_classes=[], available=True)
        log.warning("Unreadable Data Class search result: %s", exc)
        return ReferenceCatalog(available=False, unavailable_reason=str(exc))

    classes = [c for c in (data_class_from_element(e) for e in elements) if c]
    return ReferenceCatalog(data_classes=classes, available=True)


def list_known_valid_value_sets(
    ref_manager, page_size: int = 500, graph_query_depth: int = 2
) -> ReferenceCatalog:
    """Every reference-data set the platform holds, with its member values.

    `graph_query_depth` is what brings the members back at all — pyegeria has
    no member-retrieval method, so a depth-0 search returns sets with no
    values and `reference_data_match` would then report every one of them as
    having no readable members.
    """
    try:
        raw = ref_manager.find_valid_value_definitions(
            "*", starts_with=False, page_size=page_size,
            graph_query_depth=graph_query_depth, output_format="JSON",
            # Typed: the Reference Data view defaults the type to ReferenceDataValue, which hides
            # plain ValidValueDefinitions (RE's keyword sets) from an untyped find.
            metadata_element_type=VALID_VALUE_DEFINITION_TYPE_NAME,
        )
    except Exception as exc:
        log.warning("Could not list Valid Value Sets from Egeria: %s", exc)
        return ReferenceCatalog(
            available=False,
            unavailable_reason=f"the Valid Value Set search failed: {exc}",
        )
    try:
        elements = _results(raw)
    except ReferenceCatalogError as exc:
        text = str(raw).lower() if not isinstance(raw, (list, dict)) else ""
        if "no " in text and "found" in text:
            return ReferenceCatalog(valid_value_sets=[], available=True)
        log.warning("Unreadable Valid Value Set search result: %s", exc)
        return ReferenceCatalog(available=False, unavailable_reason=str(exc))

    sets = [s for s in (valid_value_set_from_element(e) for e in elements) if s]
    # A set with no member values is kept rather than dropped: it is real, and
    # `reference_data_match` reports "exists but has no readable members"
    # distinctly from "does not exist".
    return ReferenceCatalog(valid_value_sets=sets, available=True)


def load_reference_catalog(
    designer=None, ref_manager=None, page_size: int = 500
) -> ReferenceCatalog:
    """Both halves in one call, merging the availability flags.

    A missing client is `available=False` for that half only. The merged
    catalogue is `available` only when BOTH halves were read, because the two
    questions consume different halves and an unavailable one must not be
    masked by the other having worked.
    """
    classes = (
        list_known_data_classes(designer, page_size) if designer is not None
        else ReferenceCatalog(available=False, unavailable_reason="no DataDesigner client")
    )
    sets = (
        list_known_valid_value_sets(ref_manager, page_size) if ref_manager is not None
        else ReferenceCatalog(available=False, unavailable_reason="no ReferenceDataManager client")
    )
    reasons = [r for r in (classes.unavailable_reason, sets.unavailable_reason) if r]
    return ReferenceCatalog(
        data_classes=classes.data_classes,
        valid_value_sets=sets.valid_value_sets,
        available=classes.available and sets.available,
        unavailable_reason="; ".join(reasons),
    )


# ── Data-class keywords: one loader, one source label ─────────────────────────

def keyword_prefix(data_class_name: str) -> str:
    """qualifiedName prefix of one data class's keyword definitions (`bootstrap_data_classes.py`)."""
    return f"ValidValueDefinition::{data_class_name}Keyword::"


#: KeywordLoad.state values. `egeria` = at least one keyword set read; `egeria_none` = Egeria was
#: read and holds none; `unreadable` = Egeria could not be read (never "had none");
#: `not_configured` = no platform to ask.
KEYWORDS_FROM_EGERIA = "egeria"
KEYWORDS_EGERIA_NONE = "egeria_none"
KEYWORDS_UNREADABLE = "unreadable"
KEYWORDS_NOT_CONFIGURED = "not_configured"


@dataclass
class KeywordLoad:
    """The data-class keywords Egeria gave, and which source the reader therefore uses.

    `label` is the words a screen shows; it comes from this object only, so a reader's label can never
    disagree with what it loaded.
    """

    state: str
    by_class: dict[str, list[str]] = field(default_factory=dict)
    reason: str = ""
    #: The data classes asked for, and those whose keywords could not be read (a reader falls back
    #: to the built-in keywords for exactly these, class by class).
    asked: int = 0
    unread: list[str] = field(default_factory=list)

    @property
    def sets(self) -> int:
        return len(self.by_class)

    @property
    def label(self) -> str:
        if self.state == KEYWORDS_FROM_EGERIA:
            if self.unread:
                return (f"keywords: Egeria, {self.sets} of {self.asked} sets · "
                        f"{len(self.unread)} could not be read")
            return f"keywords: Egeria, {self.sets} set{'' if self.sets == 1 else 's'}"
        if self.state == KEYWORDS_EGERIA_NONE:
            return "keywords: built-in list · Egeria had none"
        if self.state == KEYWORDS_NOT_CONFIGURED:
            return "keywords: built-in list · Egeria not configured"
        why = f" ({self.reason})" if self.reason == "not signed in" else ""
        return f"keywords: built-in list · could not read Egeria{why}"

    def as_dict(self) -> dict[str, Any]:
        return {"source": "egeria" if self.state == KEYWORDS_FROM_EGERIA else "built_in",
                "state": self.state, "sets": self.sets, "asked": self.asked, "unread": self.unread,
                "label": self.label, "reason": self.reason}

    def keywords_for(self, name: str, built_in: list[str]) -> list[str]:
        """One class's keywords: Egeria's when read, else `built_in` (also for a class Egeria was
        read for and holds none, or could not be read for)."""
        return list(self.by_class.get(name) or built_in)


def _keyword_values(elements: list[dict]) -> list[str]:
    out = []
    for item in elements:
        props = item.get("properties") or {}
        val = props.get("preferredValue") or props.get("displayName")
        if isinstance(val, str) and val.strip():
            out.append(val.strip().lower())
    return sorted(set(out))


def load_data_class_keywords(ref_manager, data_class_names: Sequence[str],
                             data_class_exists=None) -> KeywordLoad:
    """Each data class's keywords from Egeria, by a TYPED find on its keyword prefix.

    `data_class_exists(name)`, when given, is asked first: False skips that class (Egeria holds no
    such DataClass, so it has no keywords there); raising marks the read failed. A `TimeoutError`
    from it stops every later lookup (the platform is not answering; round 6 of Brief I).

    A failed read is never "Egeria had none": with no keywords read and any failure, the state is
    `unreadable`.
    """
    by_class: dict[str, list[str]] = {}
    failures: list[str] = []
    unread: list[str] = []
    timed_out = False
    for name in data_class_names:
        if timed_out:
            failures.append(f"{name}: skipped after a timed-out lookup")
            unread.append(name)
            continue
        try:
            if data_class_exists is not None and not data_class_exists(name):
                continue
            raw = ref_manager.find_valid_value_definitions(
                search_string=keyword_prefix(name), starts_with=True, output_format="JSON",
                metadata_element_type=VALID_VALUE_DEFINITION_TYPE_NAME,
            )
        except TimeoutError:
            timed_out = True
            failures.append(f"{name}: timed out")
            unread.append(name)
            continue
        except Exception as exc:  # noqa: BLE001 - recorded as "could not read", never as "none"
            failures.append(f"{name}: {exc}")
            unread.append(name)
            continue
        if isinstance(raw, (list, dict)):
            values = _keyword_values(_results(raw))
            if values:
                by_class[name] = values
        elif not (isinstance(raw, str) and "no " in raw.lower() and "found" in raw.lower()):
            failures.append(f"{name}: unreadable answer {str(raw)[:80]!r}")
            unread.append(name)
    if failures:
        log.warning("Data-class keywords: could not read %d of %d: %s",
                    len(failures), len(data_class_names), "; ".join(failures)[:500])
    asked = len(data_class_names)
    if by_class:
        return KeywordLoad(KEYWORDS_FROM_EGERIA, by_class, "; ".join(failures), asked, unread)
    if failures:
        return KeywordLoad(KEYWORDS_UNREADABLE, {}, "; ".join(failures), asked, unread)
    return KeywordLoad(KEYWORDS_EGERIA_NONE, asked=asked)


# ── Proposing: `contentStatus: DRAFT` element creation ────────────────────────

def _sanitise(part: str) -> str:
    return "".join(c if (c.isalnum() or c in "-_.") else "_" for c in part)


def proposed_data_class_qualified_name(match: ColumnMatch) -> str:
    """The identity of a proposed Data Class.

    Keyed on the COLUMN, not on the detected pattern: two runs of this step
    over the same column must converge on the same element rather than
    creating a second proposal, and the pattern is exactly the thing a second
    run might decide differently.
    """
    return (
        f"{PROPOSED_DATA_CLASS_PREFIX}::"
        f"{_sanitise(match.schema_name)}.{_sanitise(match.table_name)}."
        f"{_sanitise(match.column_name)}"
    )


def proposed_valid_value_set_qualified_name(match: ColumnMatch) -> str:
    return (
        f"{PROPOSED_VALID_VALUE_SET_PREFIX}::"
        f"{_sanitise(match.schema_name)}.{_sanitise(match.table_name)}."
        f"{_sanitise(match.column_name)}"
    )


def build_proposed_data_class_body(match: ColumnMatch, qualified_name: str) -> dict:
    """The `create_data_class` body for a DRAFT proposal.

    Field names are the real ones, verified against pyegeria's
    `Egeria-api-data-designer.http` `createDataClass` example rather than
    inferred: `namespacePath` (not `namespace`), `dataPatterns` as a LIST (there
    is no `valuePattern` on this type), and `properties.class =
    "DataClassProperties"` — which RE's own `bootstrap_data_classes.py` omits,
    a bug this deliberately does not copy.

    `contentStatus: DRAFT` is the whole point: the element is created, fully
    persisted and normally visible, flagged as content-incomplete. No
    `initialStatus` is sent — it is a different field, it means entity
    lifecycle rather than content completeness, and pyegeria drops it silently
    (ISSUE-113).
    """
    statement = match.describe()
    return {
        "class": "NewElementRequestBody",
        "isOwnAnchor": True,
        "properties": {
            "class": "DataClassProperties",
            "qualifiedName": qualified_name,
            "displayName": (
                f"Proposed: {match.detected_patterns[0]} "
                f"({match.schema_name}.{match.table_name}.{match.column_name})"
                if match.detected_patterns
                else f"Proposed class for {match.column_path}"
            ),
            "description": (
                f"PROPOSAL — not confirmed content. Resource Explorer's "
                f"data_class_match found no existing Data Class matching "
                f"{match.column_path}, and the sampled values follow a "
                f"recognisable shape: {match.proposed_specification_prose or 'see dataPatterns'}. "
                f"{statement} Review and either confirm this class (clear "
                f"contentStatus) or delete it."
            ),
            # §5.8's second rule reaches the proposal itself: the specification
            # is stated together with the fraction of the sample that supports
            # it, so a curator reading only this element still sees the
            # qualification.
            "specification": match.proposed_specification,
            "dataPatterns": [match.proposed_specification] if match.proposed_specification else [],
            "matchPropertyNames": [match.column_name],
            "namespacePath": f"{match.schema_name}.{match.table_name}",
            "sampleValues": list(match.proposed_values[:20]),
            "contentStatus": CONTENT_STATUS_DRAFT,
            "additionalProperties": _proposal_evidence_properties(match),
        },
    }


def build_proposed_valid_value_set_body(match: ColumnMatch, qualified_name: str) -> dict:
    """The `create_valid_value_definition` body for a DRAFT proposed set.

    There is **no** `create_valid_value_set` in pyegeria and no
    `ValidValueSetProperties` — verified against the whole egeria-python
    checkout. A set is created through `create_valid_value_definition` with
    `properties.class = "ValidValueDefinitionProperties"`, which is also what
    RE's own `bootstrap_data_classes.py` does, and with NO `typeName`, exactly as
    bootstrap creates its sets: a set is a plain `ValidValueDefinition` whose members
    hang off it by `ValidValueMember`. Egeria 6.2 does not know a `ValidValueSet` type
    (live read-only probe, 2026-10-13: findElements answers OMAG-COMMON-400-018 "The type
    name ValidValueSet ... is not recognized", and a typed look-up as `ValidValueSet` or
    `ValidValuesSet` is a 500), so the `typeName: "ValidValueSet"` sent until then made
    every proposed-set create fail on 6.2. The existing sets
    (`ValidValuesSet::<Name>Keywords`) are stored as `ValidValueDefinition`.

    The member `ValidValueDefinition`s are created separately, by
    `publish_proposed_valid_value_set`, because each is its own element.
    """
    return {
        "class": "NewElementRequestBody",
        "isOwnAnchor": True,
        "properties": {
            "class": "ValidValueDefinitionProperties",
            "qualifiedName": qualified_name,
            "displayName": f"Proposed reference set for {match.column_path}",
            "description": (
                f"PROPOSAL — not confirmed content. Resource Explorer's "
                f"reference_data_match found {match.column_path} to be "
                f"low-cardinality with a closed set of values and no existing "
                f"Valid Value Set covering them. {match.describe()} Review and "
                f"either confirm this set (clear contentStatus) or delete it."
            ),
            "namespacePath": f"{match.schema_name}.{match.table_name}",
            "usage": "Proposed by Resource Explorer from sampled column values.",
            "isCaseSensitive": False,
            "contentStatus": CONTENT_STATUS_DRAFT,
            "additionalProperties": _proposal_evidence_properties(match),
        },
    }


def build_proposed_valid_value_member_body(
    set_qualified_name: str, value: str
) -> dict:
    """One member `ValidValueDefinition` of a proposed set, also DRAFT."""
    return {
        "class": "NewElementRequestBody",
        "isOwnAnchor": True,
        "properties": {
            "class": "ValidValueDefinitionProperties",
            "qualifiedName": f"{set_qualified_name}::{_sanitise(value)}",
            "displayName": value,
            "preferredValue": value,
            "description": (
                "PROPOSAL — an observed value of the column this set was "
                "proposed from, not a confirmed reference-data value."
            ),
            "isCaseSensitive": False,
            "contentStatus": CONTENT_STATUS_DRAFT,
        },
    }


def _proposal_evidence_properties(match: ColumnMatch) -> dict[str, str]:
    """The sample provenance, as a string map on the proposed element.

    §5.8's first rule applied to a proposal: a curator looking at a candidate
    class months later must be able to see what it was inferred from — which
    strategy ran, how many of how many rows, and with which seed — without
    finding the survey report it came from.
    """
    sample = match.provenance.as_dict() if match.provenance else {}
    evidence = {
        "proposedBy": "resource-explorer/postgres_column_profile",
        "proposedFor": match.column_path,
        "question": match.question,
        "sampleEnvelope": str(sample.get("envelope", "no sample provenance recorded")),
        "sampleStrategy": str(sample.get("strategy", "")),
        "sampleRows": str(sample.get("sample_rows", "")),
        "totalRows": str(sample.get("total_rows", "")),
        "sampleSeed": str(sample.get("seed", "")),
        "sampledConformance": (
            f"{match.sampled_conformance:.4f}" if match.sampled_conformance is not None else ""
        ),
        "detectedPatterns": ",".join(match.detected_patterns),
    }
    return {k: v for k, v in evidence.items() if v}


def build_associated_annotation_body(label: str, description: str) -> dict:
    """The relationship body for `AssociatedAnnotation`.

    `properties.class` is `AssociatedAnnotationProperties`. pyegeria's
    `link_annotation_to_described_element` passes
    `"AnnotationDescribedElementRelationship"` as its own internal `prop`
    hint, which is not a real Egeria properties class — harmless while a body
    IS passed, and a malformed POST when one is not (pyegeria synthesises
    `{"properties": {"class": prop[0]}}` from the hint). So the body is always
    passed explicitly, never left to default.
    """
    return {
        "class": "NewRelationshipRequestBody",
        "properties": {
            "class": "AssociatedAnnotationProperties",
            "label": label,
            "description": description,
        },
    }


@dataclass
class ProposalResult:
    """What one proposal actually managed to write.

    Every field can be empty, and each emptiness means something different —
    which is why this is a record rather than a bool. `created` False with
    `existing_guid` set is a converged idempotent re-run; `created` False with
    neither set and an `error` is a failure that must not be reported as a
    published proposal.
    """

    question: str
    column_path: str
    kind: str                       # "data_class" | "valid_value_set"
    qualified_name: str = ""
    guid: str = ""
    created: bool = False
    existing_guid: str = ""
    member_guids: list[str] = field(default_factory=list)
    associated_annotation_guid: str = ""
    associated_annotation_linked: bool = False
    content_status: str = CONTENT_STATUS_DRAFT
    error: str = ""
    #: Members whose look-up could not be made, so they were not created. The set itself still counts as
    #: published (a partial set is a usable proposal, as for a member whose create failed); this says which
    #: members are missing because RE could not check, rather than leaving the shortfall unexplained.
    members_not_checked: list[str] = field(default_factory=list)

    @property
    def published(self) -> bool:
        return bool(self.guid) and not self.error

    def as_dict(self) -> dict[str, Any]:
        return {
            "question": self.question,
            "column": self.column_path,
            "kind": self.kind,
            "qualified_name": self.qualified_name,
            "guid": self.guid,
            "created": self.created,
            "existing_guid": self.existing_guid,
            "member_guids": list(self.member_guids),
            "associated_annotation_guid": self.associated_annotation_guid,
            "associated_annotation_linked": self.associated_annotation_linked,
            "content_status": self.content_status,
            "published": self.published,
            "error": self.error,
            "members_not_checked": list(self.members_not_checked),
        }


class ProposalLookupError(ReferenceCatalogError):
    """The idempotency look-up before a proposal's create could not be made. Nothing is created on it."""


def _find_existing(expert, qualified_name: str, type_name: str) -> str:
    """The GUID of the `type_name` element whose qualifiedName is `qualified_name`, or "" for a real miss.

    Read through the unique-name endpoint (`egeria_lookup`): the exact-value search timed out on a large
    repository. Typed: the element must be of the type about to be created (`DataClass`, or
    `ValidValueDefinition`, which is what a set is on Egeria 6.2). qualifiedName is unique, so an element of
    another type holding the name is NOT "absent" (a create would be refused): it is a look-up that could not
    be settled.

    Only Egeria's explicit "No elements found" (or a typed not-found) returns "". A look-up that raised for any
    other reason (unreachable, 401/403, 500, timeout), or answered anything that is not an element, or found
    another type, could not be checked and raises `ProposalLookupError`. It used to return "" and the caller
    created, with Egeria's duplicate-qualifiedName refusal the only backstop (AUDIT-PYEGERIA-6.2.2.md).
    """
    from resource_explorer.egeria_absence import lookup_could_not_be_made, lookup_exception_is_absent
    from resource_explorer.egeria_lookup import guid_by_qualified_name

    if expert is None:
        raise ProposalLookupError(lookup_could_not_be_made(qualified_name, ValueError("no MetadataExpert client")))
    try:
        return guid_by_qualified_name(expert, qualified_name, type_name=type_name) or ""
    except Exception as exc:
        if lookup_exception_is_absent(exc):
            return ""
        raise ProposalLookupError(lookup_could_not_be_made(qualified_name, exc)) from exc


def publish_proposed_data_class(
    designer,
    match: ColumnMatch,
    *,
    discovery=None,
    annotation_guid: str = "",
    expert=None,
) -> ProposalResult:
    """Create a candidate `DataClass` with `contentStatus: DRAFT`, and link it
    to its evidence annotation via `AssociatedAnnotation`.

    This is §3's revised guidance: a proposal is a normal element flagged as
    incomplete content, linked to the annotation that evidences it — not an
    RFA carrying a specification in a string map. The RFA path remains for the
    cases DRAFT creation does not cover; see `postgres_column_profile`'s
    `_rfa_for_match`.

    `annotation_guid` is the `DataClassAnnotation` created for this column
    earlier in the same publish. Without one, the element is still created
    (the proposal is the useful artefact) and
    `associated_annotation_linked` stays False rather than the whole proposal
    failing — a linked-but-unpublished proposal is worth less than an
    unlinked one.
    """
    qualified_name = proposed_data_class_qualified_name(match)
    result = ProposalResult(
        question=match.question, column_path=match.column_path,
        kind="data_class", qualified_name=qualified_name,
    )

    try:
        existing = _find_existing(expert, qualified_name, DATA_CLASS_TYPE_NAME)
    except ProposalLookupError as exc:
        result.error = str(exc)
        log.warning("Proposed Data Class for %s not created: %s", match.column_path, exc)
        return result
    if existing:
        result.guid = existing
        result.existing_guid = existing
    else:
        body = build_proposed_data_class_body(match, qualified_name)
        try:
            guid = designer.create_data_class(body=body)
        except Exception as exc:
            result.error = f"create_data_class failed: {exc}"
            log.warning("Proposed Data Class for %s failed: %s", match.column_path, exc)
            return result
        if not guid:
            result.error = (
                "create_data_class returned no GUID — the create may have "
                "succeeded, so this is reported rather than retried"
            )
            return result
        result.guid = str(guid)
        result.created = True

    _link_evidence(result, discovery, annotation_guid, match)
    return result


def publish_proposed_valid_value_set(
    ref_manager,
    match: ColumnMatch,
    *,
    discovery=None,
    annotation_guid: str = "",
    max_members: int = 64,
    expert=None,
) -> ProposalResult:
    """Create a candidate reference-data set with `contentStatus: DRAFT`, one
    DRAFT member per observed value, and an `AssociatedAnnotation` link to the
    evidence.

    Members are attached with an explicit `ValidValueMemberProperties` body.
    RE's own `bootstrap_data_classes.py` calls
    `link_valid_value_definition` with no body, which makes pyegeria
    synthesise one and POST it un-serialised — a live bug this does not copy.

    A member that fails to create does not fail the set: the set plus the
    members that did land is a usable proposal, and `member_guids` says how
    many arrived so a partial write is visible rather than assumed complete.
    """
    qualified_name = proposed_valid_value_set_qualified_name(match)
    result = ProposalResult(
        question=match.question, column_path=match.column_path,
        kind="valid_value_set", qualified_name=qualified_name,
    )

    try:
        existing = _find_existing(expert, qualified_name, VALID_VALUE_DEFINITION_TYPE_NAME)
    except ProposalLookupError as exc:
        result.error = str(exc)
        log.warning("Proposed Valid Value Set for %s not created: %s", match.column_path, exc)
        return result
    if existing:
        result.guid = existing
        result.existing_guid = existing
    else:
        try:
            guid = ref_manager.create_valid_value_definition(
                body=build_proposed_valid_value_set_body(match, qualified_name)
            )
        except Exception as exc:
            result.error = f"create_valid_value_definition failed: {exc}"
            log.warning("Proposed Valid Value Set for %s failed: %s", match.column_path, exc)
            return result
        if not guid:
            result.error = "create_valid_value_definition returned no GUID"
            return result
        result.guid = str(guid)
        result.created = True

    unchecked_members: list[str] = []
    for value in list(match.proposed_values)[:max_members]:
        member_body = build_proposed_valid_value_member_body(qualified_name, value)
        member_qn = member_body["properties"]["qualifiedName"]
        try:
            member_guid = _find_existing(expert, member_qn, VALID_VALUE_DEFINITION_TYPE_NAME)
        except ProposalLookupError as exc:
            # This member is not created; the set and the members that did land are still a usable proposal,
            # but the result says one could not be checked (a partial set is never reported as whole).
            log.warning("Proposed member %r of %s not created: %s", value, qualified_name, exc)
            unchecked_members.append(value)
            continue
        if not member_guid:
            try:
                member_guid = str(ref_manager.create_valid_value_definition(body=member_body) or "")
            except Exception as exc:
                log.warning("Proposed member %r of %s failed: %s", value, qualified_name, exc)
                continue
        if not member_guid:
            continue
        try:
            ref_manager.link_valid_value_definition(
                result.guid, member_guid,
                # ValidValueMemberProperties (Egeria 6.2) has isDefaultValue and ordinal only; it is
                # ignoreUnknown, so the label/description sent here until 2026-10-13 were dropped.
                body={
                    "class": "NewRelationshipRequestBody",
                    "properties": {
                        "class": "ValidValueMemberProperties",
                        "isDefaultValue": False,
                    },
                },
            )
        except Exception as exc:
            log.warning("Attaching member %r to %s failed: %s", value, qualified_name, exc)
            continue
        result.member_guids.append(member_guid)

    result.members_not_checked = unchecked_members
    _link_evidence(result, discovery, annotation_guid, match)
    return result


def _link_evidence(
    result: ProposalResult, discovery, annotation_guid: str, match: ColumnMatch
) -> None:
    """Attach the proposal to its evidence annotation via `AssociatedAnnotation`.

    Records the attempt's outcome on `result` rather than raising: the link is
    valuable and the element is more valuable, and a proposal reported as
    failed because its link failed would be re-created on the next run.
    """
    if not (discovery is not None and annotation_guid and result.guid):
        return
    result.associated_annotation_guid = annotation_guid
    body = build_associated_annotation_body(
        label=f"Evidence for {result.kind} proposal",
        description=(
            f"The {match.question} finding this proposal was derived from. "
            f"{match.describe()}"
        ),
    )
    try:
        discovery.link_annotation_to_described_element(
            annotation_guid, result.guid, body,
        )
    except Exception as exc:
        log.warning(
            "AssociatedAnnotation link (annotation %s -> %s %s) failed: %s",
            annotation_guid, result.kind, result.guid, exc,
        )
        return
    result.associated_annotation_linked = True


# ── Client construction ───────────────────────────────────────────────────────

def build_reference_clients(
    platform_url: str = "",
    view_server: str = "",
    user_id: str = "",
    user_password: str = "",
    identity=None,
) -> tuple[Any, Any]:
    """`(DataDesigner, ReferenceDataManager)`, authenticated.

    `ReferenceDataManager` (`pyegeria.omvs.reference_data`) is the class that
    owns valid values — not `ValidMetadataManager`, which governs Egeria's own
    metadata properties rather than business reference data, and not
    `DataDesigner`, which owns data classes only.

    Built by the one factory (Brief I) as `identity`, else `current_principal()` — the
    signed-in person's token on a route, the declared daemon job in a run. `user_id` and
    `user_password` are accepted for old callers and ignored: they no longer authenticate.
    """
    from pyegeria.omvs.data_designer import DataDesigner
    from pyegeria.omvs.reference_data import ReferenceDataManager

    from resource_explorer.egeria_clients import current_principal, egeria_client

    platform_url = platform_url or egeria_platform_url()
    if not platform_url:
        raise ReferenceCatalogError(
            "EGERIA_PLATFORM_URL is not set — cannot read the platform's Data "
            "Classes, and an unread platform must not be reported as an empty one."
        )
    view_server = view_server or egeria_view_server()
    clients = egeria_client(identity or current_principal(), purpose="reference catalog",
                            view_server=view_server, platform_url=platform_url)
    return clients.of(DataDesigner), clients.of(ReferenceDataManager)


def matches_needing_proposals(matches: Sequence[ColumnMatch]) -> list[ColumnMatch]:
    """The subset a DRAFT proposal should be created for.

    Only `MATCH_UNMATCHED_PATTERNED`. Not the absence verdicts — proposing a
    Data Class for a column nothing was sampled from would turn "we did not
    look" into a governance element.
    """
    from resource_explorer.surveyors.database.column_matching import (
        MATCH_UNMATCHED_PATTERNED,
    )

    return [m for m in matches if m.verdict == MATCH_UNMATCHED_PATTERNED]
