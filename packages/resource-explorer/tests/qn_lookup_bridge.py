"""Test bridge: answer the unique-name read (`MetadataExpert.get_metadata_element_by_unique_name`) from the
old-style `get_guid_for_name` fakes these suites were written against.

Production reads an exact qualifiedName through `resource_explorer.egeria_lookup.guid_by_qualified_name` (the
unique-name endpoint). Most suites already model "what Egeria answers for this name" as a `get_guid_for_name`
mock (a GUID, a list, pyegeria's miss sentence, or a raise). Rather than rewrite every scenario, this turns that
SAME scripted answer into the unique-name answer's real shape (top-level `elementGUID`, `type.typeName` and
`superTypeNames`, from tests/fixtures/egeria_6_2), so each scenario still drives the production lookup. The
fake is `create_autospec(MetadataExpert, spec_set=True)`: its signature is the real one.
"""
from __future__ import annotations

from unittest.mock import create_autospec

from pyegeria import MetadataExpert

NO_ELEMENTS_FOUND = "No elements found"

#: A bridged element is of every type a lookup in these suites asks for; tests of type handling use
#: `element_answer(guid, type_name=...)` directly.
ANY_TYPE_NAMES = ["ValidValueDefinition", "DataClass", "SolutionBlueprint", "SolutionComponent",
                  "FileFolder", "ExternalReference", "Referenceable", "OpenMetadataRoot"]


def element_answer(guid: str, type_name: str = "Referenceable", supers: list[str] | None = None) -> dict:
    return {"elementGUID": guid, "headerVersion": 0, "status": "ACTIVE",
            "type": {"typeName": type_name, "typeCategory": "ENTITY_DEF",
                     "superTypeNames": list(ANY_TYPE_NAMES if supers is None else supers)}}


MULTIPLE_ENTITIES_FOUND = "OMAG-GENERIC-HANDLERS-404-002"


def multiple_entities_404(name: str = "the name"):
    """The REAL exception pyegeria raises when Egeria's unique-name read finds two elements: a 404-wrapped
    answer (`relatedHTTPCode` 404, `exceptionErrorMessageId` OMAG-GENERIC-HANDLERS-404-002) as a
    `PyegeriaNotFoundException`, built the way `_async_make_request` builds it."""
    import httpx
    from pyegeria.core._exceptions import PyegeriaNotFoundException

    message = (f"{MULTIPLE_ENTITIES_FOUND} Multiple Referenceable entities where found with a name of {name} "
               "in the open metadata repositories")
    body = {"class": "VoidResponse", "relatedHTTPCode": 404, "exceptionErrorMessageId": MULTIPLE_ENTITIES_FOUND,
            "exceptionErrorMessage": message}
    resp = httpx.Response(200, json=body, request=httpx.Request("POST", "https://localhost:9443/servers/v/api"))
    return PyegeriaNotFoundException(resp, {"caller_method": "get_metadata_element_by_unique_name"},
                                     additional_info=body)


def convert_answer(answer):
    if isinstance(answer, list):
        if len(answer) > 1:       # the unique-name endpoint cannot return two; a server holding two answers 404-002
            raise multiple_entities_404()
        return convert_answer(answer[0]) if answer else NO_ELEMENTS_FOUND
    if isinstance(answer, str) and len(answer) == 36 and answer.count("-") == 4:
        return element_answer(answer)
    if isinstance(answer, dict) and ("guid" in answer) and "elementGUID" not in answer:
        return element_answer(answer["guid"])
    return answer          # the miss sentence, None, "", an odd string: the helper decides, as in production


def expert_from_legacy(legacy, expert=None):
    """A MetadataExpert fake whose unique-name read answers what `legacy.get_guid_for_name(name, ...)` answers.
    Pass `expert` (an existing autospec) to wire the read into it and keep its other scripted answers."""
    if expert is None:
        expert = create_autospec(MetadataExpert, instance=True, spec_set=True)

    def _read(name=None, property_name="qualifiedName", body=None, **kwargs):
        try:
            answer = legacy.get_guid_for_name(name, property_name=[property_name])
        except TypeError:
            answer = legacy.get_guid_for_name(name)
        return convert_answer(answer)

    expert.get_metadata_element_by_unique_name.side_effect = _read
    return expert


class WithBridgedReads:
    """Wrap an existing expert fake: unique-name reads come from `legacy`, everything else from `inner`."""

    def __init__(self, inner, legacy):
        self._inner = inner
        self._bridge = expert_from_legacy(legacy)

    def get_metadata_element_by_unique_name(self, *a, **k):
        return self._bridge.get_metadata_element_by_unique_name(*a, **k)

    def __getattr__(self, name):
        return getattr(self._inner, name)

    def __setattr__(self, name, value):
        if name in ("_inner", "_bridge"):
            object.__setattr__(self, name, value)
        else:
            setattr(self._inner, name, value)


def unique_name_via_legacy(fake, name, property_name="qualifiedName"):
    """For a hand-written fake class that already has `get_guid_for_name`: give it the unique-name read too,
    `def get_metadata_element_by_unique_name(self, name=None, property_name="qualifiedName", body=None, **kw):
    return unique_name_via_legacy(self, name, property_name)`. Looks `get_guid_for_name` up at call time, so a
    test that swaps it for a raising function still drives the production lookup."""
    try:
        answer = fake.get_guid_for_name(name, property_name=[property_name])
    except TypeError:
        answer = fake.get_guid_for_name(name)
    return convert_answer(answer)


def wire_unique_name(expert, lookup):
    """Wire a MetadataExpert autospec's unique-name read to a scripted `lookup(name, ...)` (a GUID, an element dict,
    a list, the miss sentence, or a raise): the lookup the suite scripts for `get_guid_for_name`, now asked of the
    unique-name endpoint. Returns the expert."""
    def _read(name=None, property_name="qualifiedName", body=None, **kwargs):
        try:
            answer = lookup(name, property_name=[property_name])
        except TypeError:
            answer = lookup(name)
        return convert_answer(answer)

    expert.get_metadata_element_by_unique_name.side_effect = _read
    return expert
