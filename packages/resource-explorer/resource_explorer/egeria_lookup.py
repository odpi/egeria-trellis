"""Exact qualifiedName -> GUID, through Egeria's unique-name read; an exact display/path name -> GUID, through a
typed string search (`guid_by_exact_value`, at the end of this module).

Why this exists (live 2026-10-21/22): `get_guid_for_name(qn, property_name=["qualifiedName"])` goes to
`classification-explorer/elements/by-exact-property-value`, which on a Postgres repository builds one giant OR
list of candidate GUIDs and hydrates them all. It timed out at 30 s on every adoption lookup and blocked every
new publish; its cost grows with the repository. `MetadataExpert.get_metadata_element_by_unique_name` is a
direct unique-name read (under 0.1 s, found or missing, on the same repository at the same time).

Contract of `guid_by_qualified_name`:

  * found                  -> the element's GUID (a str);
  * Egeria's explicit "No elements found" answer -> None (the ONLY absent answer);
  * anything else          -> RAISES. A raised pyegeria exception propagates unchanged (so the caller's own
    `egeria_absence.is_absent` / could-not-check handling still applies), an answer of an unknown shape raises
    `QualifiedNameLookupError`. A failed lookup is never "absent": absent licenses a create.
  * two elements holding the name -> `QualifiedNameAmbiguous` (Egeria's 404 `OMAG-GENERIC-HANDLERS-404-002`,
    which pyegeria raises as a NotFound and which must NOT read as absent);
  * with `type_name`, the element's type (or one of its supertypes) must match; a mismatch raises
    `QualifiedNameTypeMismatch`. qualifiedName is unique across Egeria, so another type holding the name means
    a create would be refused or duplicate: the caller must not read it as "absent".

Call sites never string-match the answer.
"""
from __future__ import annotations

import re

from resource_explorer.egeria_absence import MISS_ANSWERS, MULTIPLE_ENTITIES_FOUND_MESSAGE_ID, egeria_message_id

_GUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.IGNORECASE)
_MISSES = {m.lower() for m in MISS_ANSWERS}


class QualifiedNameLookupError(RuntimeError):
    """The unique-name read gave an answer that is neither an element nor Egeria's explicit miss."""


class NoAnswer(QualifiedNameLookupError):
    """The read returned nothing at all (None). `no_answer` lets `egeria_absence.lookup_could_not_be_made` say
    "Egeria gave no answer", as it does for a look-up that returned None."""

    no_answer = True


class QualifiedNameTypeMismatch(QualifiedNameLookupError):
    """An element holds this qualifiedName but is not of the type asked for."""


class QualifiedNameAmbiguous(QualifiedNameLookupError):
    """Two or more elements hold this qualifiedName. Egeria's unique-name read refuses with
    `OMAG-GENERIC-HANDLERS-404-002` (HTTP 404, which pyegeria raises as a NotFound): that is "there is more than
    one", never "there is none", so nothing is adopted or created. Carries the original exception."""

    def __init__(self, message: str, original: BaseException | None = None):
        super().__init__(message)
        self.original = original
        self.__cause__ = original


def _is_miss(answer) -> bool:
    return isinstance(answer, str) and answer.strip().rstrip(".").strip().lower() in _MISSES


def _guid_of(answer: dict) -> str:
    """LIVE (fixtures/egeria_6_2): top-level `elementGUID`; `elementHeader.guid` is the asset-graph shape."""
    header = answer.get("elementHeader")
    return str(answer.get("elementGUID") or (header.get("guid") if isinstance(header, dict) else "") or "").strip()


def _type_names(answer: dict) -> set[str]:
    """LIVE: top-level `type` {typeName, superTypeNames}; `elementHeader.type` is the asset-graph shape."""
    kind = answer.get("type")
    if not isinstance(kind, dict):
        header = answer.get("elementHeader")
        kind = header.get("type") if isinstance(header, dict) else None
    names: set[str] = set()
    if isinstance(kind, dict):
        if kind.get("typeName"):
            names.add(str(kind["typeName"]))
        supers = kind.get("superTypeNames")
        if isinstance(supers, (list, tuple)):
            names.update(str(x) for x in supers if isinstance(x, str))
    return names


def guid_by_qualified_name(expert, qualified_name: str, *, type_name: str | None = None) -> str | None:
    """The GUID of the element whose qualifiedName is EXACTLY `qualified_name`, None for Egeria's explicit
    "No elements found", and a raise for everything else (see the module docstring)."""
    try:
        answer = expert.get_metadata_element_by_unique_name(qualified_name, property_name="qualifiedName")
    except Exception as exc:
        # Matched on Egeria's message id, never its English. Any other exception propagates unchanged.
        if egeria_message_id(exc) == MULTIPLE_ENTITIES_FOUND_MESSAGE_ID:
            raise QualifiedNameAmbiguous(
                f"more than one element holds the qualifiedName {qualified_name!r} "
                f"({MULTIPLE_ENTITIES_FOUND_MESSAGE_ID}): {' '.join(str(exc).split())[:200]}", exc) from exc
        raise
    if _is_miss(answer):
        return None
    if answer is None:
        raise NoAnswer(f"no answer for {qualified_name!r}")
    if not isinstance(answer, dict) or not answer:
        raise QualifiedNameLookupError(f"unexpected answer for {qualified_name!r}: {answer!r:.120}")
    guid = _guid_of(answer)
    if not _GUID.match(guid):
        raise QualifiedNameLookupError(f"the answer for {qualified_name!r} carries no element GUID")
    if type_name:
        have = _type_names(answer)
        if type_name not in have:
            raise QualifiedNameTypeMismatch(
                f"{qualified_name!r} is held by element {guid} of type {sorted(have) or 'unknown'}, "
                f"not {type_name}: nothing is adopted or created")
    return guid


# ── an exact value of a NAME property (displayName, pathName): a typed string search ──────────────────────────
#
# Why (live 2026-10-22; the evidence note on exact-value search scaling sits outside this repo): `get_guid_for_name`
# goes to `by-exact-property-value`, which timed out at 30 s on the grown repository, untyped or typed. A name
# that is not a qualifiedName cannot use the unique-name read, so it uses `find_metadata_elements_with_string`
# narrowed to ONE type (measured instant on the same repository). On Egeria's Postgres repository a search
# string with startsWith AND endsWith is a literal `like` with no wildcard (QueryBuilder.getSearchStringClause,
# getSafeLikePattern): an exact match on ANY string property of the type. This helper then keeps only the
# elements whose named property is exactly the value (architect ruling 2026-10-22, "ARCHITECT on fast-lookup").

#: The element-local search: no graph, one page. A full page is "could not tell" (more may follow), never a count.
EXACT_VALUE_PAGE_SIZE = 50
DEPRECATED = "DEPRECATED"


class ExactValueAmbiguous(QualifiedNameLookupError):
    """Two or more elements of the type hold the value: the lookup is UNREADABLE (the same rule as a duplicated
    qualifiedName, `QualifiedNameAmbiguous`), so nothing is adopted, created or healed."""

    def __init__(self, message: str, guids: list[str]):
        super().__init__(message)
        self.guids = list(guids)


def _property_of(answer: dict, name: str) -> str | None:
    """LIVE (fixtures/egeria_6_2 find-with-string): `elementProperties.propertiesAsStrings.<name>`, or the typed
    value in `elementProperties.propertyValueMap.<name>.primitiveValue`; `properties.<name>` is the asset-graph
    shape. None when the element does not carry the property."""
    ep = answer.get("elementProperties")
    if isinstance(ep, dict):
        strings = ep.get("propertiesAsStrings")
        if isinstance(strings, dict) and name in strings:
            return str(strings[name])
        pv = (ep.get("propertyValueMap") or {}).get(name)
        if isinstance(pv, dict) and "primitiveValue" in pv:
            return str(pv["primitiveValue"])
        if isinstance(pv, dict) and "symbolicName" in pv:          # an enum (contentStatus)
            return str(pv["symbolicName"])
    props = answer.get("properties")
    if isinstance(props, dict) and name in props:
        return str(props[name])
    return None


def is_deprecated(answer: dict) -> bool:
    """A term (or any authored element) whose `contentStatus` is DEPRECATED, or whose instance status is. Egeria 6
    keeps a term's status in `contentStatus` (pyegeria glossary_manager, ISSUE-113); the header status is read too
    so an instance-level DEPRECATED is never served as live."""
    if (_property_of(answer, "contentStatus") or "").upper() == DEPRECATED:
        return True
    header = answer.get("elementHeader")
    status = answer.get("status") or (header.get("status") if isinstance(header, dict) else "")
    return str(status or "").upper() == DEPRECATED


def guid_by_exact_value(expert, value: str, *, property_name: str, type_name: str,
                        exclude_deprecated: bool = False) -> str | None:
    """The GUID of the ONE element of `type_name` (or a subtype) whose `property_name` is EXACTLY `value`.

      * one element               -> its GUID;
      * none (Egeria's "No elements found", a typed not-found, or no element with the property exactly equal)
                                  -> None;
      * two or more               -> `ExactValueAmbiguous` (UNREADABLE: never "absent", never "pick one");
      * anything else             -> RAISES: a pyegeria exception propagates unchanged, an odd answer, an EMPTY
        element list `[]` (not Egeria's miss: `egeria_absence.is_absent([])` is unreadable), an element without a
        GUID or of another type, or a full page raise `QualifiedNameLookupError`.

    `exclude_deprecated` drops DEPRECATED elements before counting (the question readers: a retired or merged
    term keeps its links, and this is what stops it resolving). `expert` is a `MetadataExpert`."""
    from resource_explorer.egeria_absence import lookup_exception_is_absent

    if not value or not property_name or not type_name:
        raise ValueError("guid_by_exact_value needs a value, a property name and a type name")
    body = {"class": "SearchStringRequestBody", "searchString": value, "startsWith": True, "endsWith": True,
            "ignoreCase": False, "metadataElementTypeName": type_name, "graphQueryDepth": 0,
            "startFrom": 0, "pageSize": EXACT_VALUE_PAGE_SIZE}
    try:
        answer = expert.find_metadata_elements_with_string(
            search_string=value, starts_with=True, ends_with=True, ignore_case=False,
            metadata_element_type=type_name, graph_query_depth=0, start_from=0,
            page_size=EXACT_VALUE_PAGE_SIZE, body=body)
    except Exception as exc:
        if egeria_message_id(exc) == MULTIPLE_ENTITIES_FOUND_MESSAGE_ID:
            raise ExactValueAmbiguous(f"more than one {type_name} holds the {property_name} {value!r}", []) from exc
        if lookup_exception_is_absent(exc):
            return None
        raise
    if _is_miss(answer):
        return None
    if answer is None:
        raise NoAnswer(f"no answer for the {type_name} {property_name} {value!r}")
    if not isinstance(answer, list):
        raise QualifiedNameLookupError(f"unexpected answer for the {type_name} {property_name} {value!r}: "
                                       f"{answer!r:.120}")
    if not answer:
        # An empty element list is not Egeria's miss (that is the "No elements found" sentence, or a typed
        # not-found: `egeria_absence`); `is_absent([])` is UNREADABLE, so it raises and nothing is created.
        raise QualifiedNameLookupError(f"an empty element list for the {type_name} {property_name} {value!r}: "
                                       "not Egeria's \"No elements found\", so it could not be read")
    if len(answer) >= EXACT_VALUE_PAGE_SIZE:
        raise QualifiedNameLookupError(f"{len(answer)} or more {type_name} elements answered {value!r}: the "
                                       "page is full, so the count could not be told")
    held: list[str] = []
    for element in answer:
        if not isinstance(element, dict):
            raise QualifiedNameLookupError(f"an element in the answer for {value!r} is not an element")
        guid = _guid_of(element)
        if not _GUID.match(guid):
            raise QualifiedNameLookupError(f"an element in the answer for {value!r} carries no element GUID")
        if type_name not in _type_names(element):
            raise QualifiedNameLookupError(f"element {guid} in the {type_name} answer for {value!r} is of type "
                                           f"{sorted(_type_names(element)) or 'unknown'}")
        if _property_of(element, property_name) != value:
            continue                      # the value sits in another property (a description, a resourceName)
        if exclude_deprecated and is_deprecated(element):
            continue
        if guid not in held:              # one element listed twice is still one element
            held.append(guid)
    if len(held) > 1:
        raise ExactValueAmbiguous(f"{len(held)} {type_name} elements hold the {property_name} {value!r} "
                                  f"({', '.join(held)}): nothing is adopted, created or healed", held)
    return held[0] if held else None
