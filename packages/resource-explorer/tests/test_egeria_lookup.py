"""Exact qualifiedName lookups go through Egeria's unique-name read, not the exact-value search.

Live 2026-10-21/22: `get_guid_for_name(qn, property_name=["qualifiedName"])` timed out (30 s) on every adoption
lookup, blocking every new publish; `MetadataExpert.get_metadata_element_by_unique_name` answered in under 0.1 s
on the same repository. `resource_explorer.egeria_lookup.guid_by_qualified_name` is the one helper; this file
pins its contract and, per migrated site, that the fast method is called, `get_guid_for_name` is not, and a
lookup that fails still refuses to create.

Fakes are `create_autospec(..., spec_set=True)`: every call binds the real pyegeria signature.
"""
from __future__ import annotations

import ast
import pathlib
from unittest.mock import create_autospec

import pytest
from pyegeria import AutomatedCuration, MetadataExpert
from pyegeria.core._exceptions import PyegeriaConnectionException

from resource_explorer.egeria_lookup import (
    NoAnswer, QualifiedNameLookupError, QualifiedNameTypeMismatch, guid_by_qualified_name)
from tests.qn_lookup_bridge import element_answer

MISS = "No elements found"
GUID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
QN = "SolutionComponent::x::y::z"

pytestmark = pytest.mark.usefixtures("signed_in_caller")


def expert(answer=None, *, raises=None):
    fake = create_autospec(MetadataExpert, instance=True, spec_set=True)
    if raises is not None:
        fake.get_metadata_element_by_unique_name.side_effect = raises
    else:
        fake.get_metadata_element_by_unique_name.return_value = answer
    return fake


# ── the helper's contract ────────────────────────────────────────────────

def test_found_returns_the_guid_and_asks_for_qualified_name():
    fake = expert(element_answer(GUID, "FileFolder"))
    assert guid_by_qualified_name(fake, QN) == GUID
    fake.get_metadata_element_by_unique_name.assert_called_once_with(QN, property_name="qualifiedName")


def test_the_explicit_not_found_answer_is_none_and_a_fake_raising_raises():
    """The architect's one test: a fake returning the string gives None, a fake raising gives the raise."""
    assert guid_by_qualified_name(expert(MISS), QN) is None
    assert guid_by_qualified_name(expert("No element found"), QN) is None
    boom = PyegeriaConnectionException()
    with pytest.raises(PyegeriaConnectionException):
        guid_by_qualified_name(expert(raises=boom), QN)


@pytest.mark.parametrize("odd", ["Server busy", "", "   ", {}, [], [element_answer(GUID)], 7, {"x": 1},
                                 {"elementGUID": "not-a-guid"}])
def test_an_answer_of_an_unknown_shape_raises_never_absent(odd):
    with pytest.raises(QualifiedNameLookupError):
        guid_by_qualified_name(expert(odd), QN)


def test_no_answer_at_all_raises_and_says_so():
    from resource_explorer.egeria_absence import lookup_could_not_be_made

    with pytest.raises(NoAnswer) as info:
        guid_by_qualified_name(expert(None), QN)
    assert "Egeria gave no answer" in lookup_could_not_be_made(QN, info.value)


def test_the_asset_graph_shape_is_read_too():
    answer = {"elementHeader": {"guid": GUID, "type": {"typeName": "RelationalDatabase"}}}
    assert guid_by_qualified_name(expert(answer), QN) == GUID


def test_type_is_checked_against_the_type_and_its_supertypes():
    answer = element_answer(GUID, "RelationalDatabase", supers=["Database", "DataAsset", "Referenceable"])
    assert guid_by_qualified_name(expert(answer), QN, type_name="RelationalDatabase") == GUID
    assert guid_by_qualified_name(expert(answer), QN, type_name="DataAsset") == GUID


def test_another_type_holding_the_name_raises_it_is_not_absent():
    answer = element_answer(GUID, "DataFile", supers=["Referenceable"])
    with pytest.raises(QualifiedNameTypeMismatch):
        guid_by_qualified_name(expert(answer), QN, type_name="FileFolder")


def test_a_miss_with_a_type_is_still_none():
    assert guid_by_qualified_name(expert(MISS), QN, type_name="FileFolder") is None


# ── the guard: the slow endpoint does not come back ──────────────────────

#: Every remaining `get_guid_for_name(` in resource_explorer, with why it is NOT an exact-qualifiedName lookup.
#: pyegeria's methods on `classification-explorer/elements/by-exact-property-value` (30 s on a grown repository).
SLOW_EXACT_VALUE_METHODS = ("get_guid_for_name", "get_elements_by_property_value",
                            "_async_get_elements_by_property_value")
ALLOWED_GET_GUID_FOR_NAME: dict[str, int] = {}   # none left (2026-10-23): display/path names use a typed search
#: The scripts that still call the slow endpoint, keyed by path relative to the package root: (count, reason).
#: Not changed in this round (review 4, 2026-10-23): each is a known site to move, not a licence for more.
ALLOWED_SLOW_IN_SCRIPTS: dict[str, tuple[int, str]] = {
    "scripts/create_measure_definitions.py": (1, "follow-up: slow by-exact-value lookup, see FAST-LOOKUP-SITES"),
    "scripts/verify_perspectives.py": (1, "follow-up: slow by-exact-value lookup, see FAST-LOOKUP-SITES"),
}


def _slow_calls_under(root: pathlib.Path, relative_to: pathlib.Path) -> dict[str, int]:
    found: dict[str, int] = {}
    for path in root.rglob("*.py"):
        tree = ast.parse(path.read_text())
        n = sum(1 for node in ast.walk(tree)
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr in SLOW_EXACT_VALUE_METHODS)
        if n:
            found[path.relative_to(relative_to).as_posix()] = n
    return found


def test_get_guid_for_name_does_not_come_back_for_qualified_names():
    package = pathlib.Path(__file__).resolve().parent.parent
    root = package / "resource_explorer"
    found = _slow_calls_under(root, root)
    scripts = _slow_calls_under(package / "scripts", package)
    assert scripts == {path: count for path, (count, _why) in ALLOWED_SLOW_IN_SCRIPTS.items()}, (
        "a slow by-exact-value call came back in scripts/ (or an allowed one went): use egeria_lookup's "
        "guid_by_qualified_name / guid_by_exact_value, or move it off ALLOWED_SLOW_IN_SCRIPTS")
    assert all(why.strip() for _count, why in ALLOWED_SLOW_IN_SCRIPTS.values())
    assert found == ALLOWED_GET_GUID_FOR_NAME, (
        "a get_guid_for_name( call came back (or an allowed one went): look up an exact qualifiedName with "
        "egeria_lookup.guid_by_qualified_name and a display/path name with egeria_lookup.guid_by_exact_value")


# ── per site: the fast method is called, the slow one is not, a failure still creates nothing ──

def _no_slow(ac):
    ac.get_guid_for_name.assert_not_called()


def test_publisher_find_element_guid():
    from resource_explorer.surveyors.egeria_publisher import EgeriaPublisher

    pub = EgeriaPublisher()
    pub._automated_curation = create_autospec(AutomatedCuration, instance=True, spec_set=True)
    pub._metadata_expert = expert(element_answer(GUID))
    assert pub._find_element_guid(QN) == GUID
    pub._metadata_expert = expert(MISS)
    assert pub._find_element_guid(QN) == ""
    pub._metadata_expert = expert(raises=PyegeriaConnectionException())
    with pytest.raises(PyegeriaConnectionException):
        pub._find_element_guid(QN)                    # never "" : the caller's create does not run
    _no_slow(pub._automated_curation)


@pytest.mark.parametrize("modname, cls_name", [
    ("resource_explorer.surveyors.arch_recovery.materializer", "ComponentMaterializer"),
    ("resource_explorer.surveyors.arch_recovery.blueprint_materializer", "BlueprintMaterializer"),
])
def test_materializers_find_element_guid(modname, cls_name):
    import importlib

    m = getattr(importlib.import_module(modname), cls_name)(platform_url="https://fake")
    m._metadata_expert = expert(element_answer(GUID))
    assert m._find_element_guid(QN) == GUID
    m._metadata_expert = expert(MISS)
    assert m._find_element_guid(QN) == ""
    m._metadata_expert = expert(raises=PyegeriaConnectionException())
    with pytest.raises(PyegeriaConnectionException):
        m._find_element_guid(QN)


def test_blueprint_find_blueprint_uses_the_fast_read_and_verifies_on_read_back():
    from unittest.mock import MagicMock

    from resource_explorer.surveyors.arch_recovery.blueprint_materializer import BlueprintMaterializer

    m = BlueprintMaterializer(platform_url="https://fake")
    m._metadata_expert = expert(MISS)
    m._solution_architect = MagicMock()
    assert m._find_blueprint("SolutionBlueprint::a") is None
    m._metadata_expert.get_metadata_element_by_unique_name.assert_called_once_with(
        "SolutionBlueprint::a", property_name="qualifiedName")
    m._metadata_expert = expert(raises=PyegeriaConnectionException())
    with pytest.raises(PyegeriaConnectionException):
        m._find_blueprint("SolutionBlueprint::a")
    m._solution_architect.create_solution_blueprint.assert_not_called()


def test_database_surveyor_finders():
    from resource_explorer.surveyors.database.egeria_database_surveyor import (
        EgeriaDatabaseSurveyor, EgeriaDatabaseSurveyorError)

    s = EgeriaDatabaseSurveyor(platform_url="https://fake", view_server="v")
    s._automated_curation = create_autospec(AutomatedCuration, instance=True, spec_set=True)
    for finder in (s._find_element_guid, s._find_by_qualified_name):
        s._metadata_expert = expert(element_answer(GUID))
        assert finder(QN) == GUID
        s._metadata_expert = expert(MISS)
        assert finder(QN) == ""
        s._metadata_expert = expert(raises=PyegeriaConnectionException())
        with pytest.raises(EgeriaDatabaseSurveyorError, match="Egeria"):
            finder(QN)
    _no_slow(s._automated_curation)


def test_filesystem_surveyor_root_folder_is_the_fast_read_typed_and_paths_are_a_typed_search():
    from resource_explorer.surveyors.filesystem.egeria_filesystem_surveyor import (
        EgeriaFileSystemSurveyor, EgeriaFileSystemSurveyorError)

    s = EgeriaFileSystemSurveyor(platform_url="https://fake", view_server="v")
    s._automated_curation = create_autospec(AutomatedCuration, instance=True, spec_set=True)
    s._metadata_expert = expert(element_answer(GUID, "FileFolder"))
    assert s._find_element_guid(QN, type_name="FileFolder", qualified_name=True) == GUID
    s._metadata_expert = expert(MISS)
    assert s._find_element_guid(QN, type_name="FileFolder", qualified_name=True) == ""
    s._metadata_expert = expert(raises=PyegeriaConnectionException())
    with pytest.raises(EgeriaFileSystemSurveyorError, match="could not be made"):
        s._find_element_guid(QN, type_name="FileFolder", qualified_name=True)
    _no_slow(s._automated_curation)
    # a file's path is a pathName, not a qualifiedName: a typed string search, never the exact-value search
    s._metadata_expert = searcher([found("/mnt/x/a.csv", "pathName", type_name="CSVFile", supers=["DataFile"])])
    assert s._find_element_guid("/mnt/x/a.csv", type_name="DataFile") == GUID
    assert_typed_exact(s._metadata_expert, "/mnt/x/a.csv", "DataFile")
    _no_slow(s._automated_curation)
    _no_slow(s._metadata_expert)


def test_doc_source_find_ref_guid_is_bounded_and_fast():
    from resource_explorer.doc_source_egeria import DocSourceLookupError, _find_ref_guid

    class Clients:
        def __init__(self, fake):
            self.fake = fake

        def of(self, cls):
            assert cls is MetadataExpert, "the slow AutomatedCuration search must not be built for this"
            return self.fake

    assert _find_ref_guid(Clients(expert(element_answer(GUID))), "ExternalReference::u") == GUID
    assert _find_ref_guid(Clients(expert(MISS)), "ExternalReference::u") == ""
    with pytest.raises(DocSourceLookupError, match="could not be made"):
        _find_ref_guid(Clients(expert(raises=PyegeriaConnectionException())), "ExternalReference::u")


def test_investigation_purposes_lookup_is_typed_and_fast():
    from resource_explorer import investigation_purposes as ip

    assert ip._lookup(expert(element_answer(GUID, "ValidValueDefinition", supers=[])), "q") == GUID
    assert ip._lookup(expert(MISS), "q") == ""
    for bad in (expert(raises=PyegeriaConnectionException()), expert(element_answer(GUID, "DataClass", supers=[])), None):
        with pytest.raises(ip.CouldNotCheck):
            ip._lookup(bad, "q")


def test_data_class_seed_lookup_is_typed_and_fast():
    from resource_explorer.surveyors.database import bootstrap_data_classes as bdc

    assert bdc._existing_guid(expert(element_answer(GUID, "DataClass", supers=[])), "q") == GUID
    assert bdc._existing_guid(expert(MISS), "q", "ValidValueDefinition") is None
    for bad in (expert(raises=PyegeriaConnectionException()), expert(element_answer(GUID, "DataClass", supers=[])), None):
        with pytest.raises(bdc.CouldNotCheck):
            bdc._existing_guid(bad, "q", "ValidValueDefinition")


def test_proposal_find_existing_is_typed_and_fast():
    from resource_explorer.surveyors.database.egeria_reference_catalog import ProposalLookupError, _find_existing

    assert _find_existing(expert(element_answer(GUID, "DataClass", supers=[])), "q", "DataClass") == GUID
    assert _find_existing(expert(MISS), "q", "DataClass") == ""
    for bad in (expert(raises=PyegeriaConnectionException()), expert(element_answer(GUID, "DataField", supers=[])), None):
        with pytest.raises(ProposalLookupError):
            _find_existing(bad, "q", "DataClass")


def test_bootstrap_canary_by_qualified_name_is_the_fast_read_and_a_failure_is_unknown_not_missing(tmp_path):
    from resource_explorer import bootstrap as bs

    batch = bs.Batch(batch_id="b", path=tmp_path, display_name="b", files=[], canary={"qualified_name": "Q::b"})
    assert bs.canary_present(batch, expert=expert(element_answer(GUID))) is True
    assert bs.canary_present(batch, expert=expert(MISS)) is False
    assert bs.canary_present(batch, expert=expert(raises=PyegeriaConnectionException())) is None
    assert bs.canary_present(batch, expert=expert("Server busy")) is None


# ── two elements holding the name: Egeria's 404 OMAG-GENERIC-HANDLERS-404-002 is "more than one", never absent ──

def _dup():
    from tests.qn_lookup_bridge import multiple_entities_404
    return multiple_entities_404(QN)


def test_the_duplicate_404_is_a_real_pyegeria_not_found_with_the_message_id():
    from pyegeria.core._exceptions import PyegeriaNotFoundException
    from resource_explorer.egeria_absence import egeria_message_id

    exc = _dup()
    assert isinstance(exc, PyegeriaNotFoundException)
    assert exc.related_http_code == 404
    assert egeria_message_id(exc) == "OMAG-GENERIC-HANDLERS-404-002"


def test_helper_turns_the_duplicate_404_into_qualified_name_ambiguous():
    from resource_explorer.egeria_lookup import QualifiedNameAmbiguous

    original = _dup()
    with pytest.raises(QualifiedNameAmbiguous) as info:
        guid_by_qualified_name(expert(raises=original), QN)
    assert info.value.original is original and info.value.__cause__ is original
    assert "Multiple" in str(info.value) or "more than one" in str(info.value)
    assert isinstance(info.value, QualifiedNameLookupError)


def test_any_other_not_found_propagates_unchanged():
    from pyegeria.core._exceptions import PyegeriaNotFoundException
    from tests.test_lookup_failure_round2 import _egeria_error

    other = _egeria_error(PyegeriaNotFoundException, 404, "OMRS-METADATA-COLLECTION-404-001")
    with pytest.raises(PyegeriaNotFoundException) as info:
        guid_by_qualified_name(expert(raises=other), QN)
    assert info.value is other


def test_classify_the_duplicate_404_is_unreadable_not_absent():
    from resource_explorer.egeria_absence import UNREADABLE, is_absent, lookup_exception_is_absent

    assert is_absent(_dup()) == UNREADABLE
    assert lookup_exception_is_absent(_dup()) is False


def test_publisher_sub_resources_do_not_create_on_a_duplicated_name():
    from unittest.mock import MagicMock
    from resource_explorer.surveyors.egeria_publisher import EgeriaPublisher

    pub = EgeriaPublisher()
    pub._automated_curation = create_autospec(AutomatedCuration, instance=True, spec_set=True)
    pub._metadata_expert = expert(raises=_dup())
    with pytest.raises(Exception) as info:
        pub._find_element_guid(QN)
    from resource_explorer.egeria_absence import lookup_exception_is_absent
    assert not lookup_exception_is_absent(info.value)      # the sub-resource loop's own test: could not check


def test_materializers_do_not_create_on_a_duplicated_name():
    import importlib
    from resource_explorer.egeria_lookup import QualifiedNameAmbiguous

    for modname, cls_name in [("resource_explorer.surveyors.arch_recovery.materializer", "ComponentMaterializer"),
                              ("resource_explorer.surveyors.arch_recovery.blueprint_materializer", "BlueprintMaterializer")]:
        m = getattr(importlib.import_module(modname), cls_name)(platform_url="https://fake")
        m._metadata_expert = expert(raises=_dup())
        with pytest.raises(QualifiedNameAmbiguous):
            m._find_element_guid(QN)


def test_blueprint_find_blueprint_duplicate_is_blueprint_ambiguous_by_message_id():
    from unittest.mock import MagicMock
    from resource_explorer.surveyors.arch_recovery.blueprint_materializer import (
        BlueprintAmbiguous, BlueprintMaterializer)

    m = BlueprintMaterializer(platform_url="https://fake")
    m._metadata_expert = expert(raises=_dup())
    m._solution_architect = MagicMock()
    with pytest.raises(BlueprintAmbiguous):
        m._find_blueprint("SolutionBlueprint::a")
    m._solution_architect.create_solution_blueprint.assert_not_called()


def test_database_surveyor_finders_do_not_adopt_or_create_on_a_duplicated_name():
    from resource_explorer.surveyors.database.egeria_database_surveyor import (
        EgeriaDatabaseSurveyor, EgeriaDatabaseSurveyorError)

    s = EgeriaDatabaseSurveyor(platform_url="https://fake", view_server="v")
    s._automated_curation = create_autospec(AutomatedCuration, instance=True, spec_set=True)
    for finder in (s._find_element_guid, s._find_by_qualified_name):
        s._metadata_expert = expert(raises=_dup())
        with pytest.raises(EgeriaDatabaseSurveyorError, match="Egeria"):
            finder(QN)


def test_filesystem_surveyor_does_not_create_on_a_duplicated_name():
    from resource_explorer.surveyors.filesystem.egeria_filesystem_surveyor import (
        EgeriaFileSystemSurveyor, EgeriaFileSystemSurveyorError)

    s = EgeriaFileSystemSurveyor(platform_url="https://fake", view_server="v")
    s._automated_curation = create_autospec(AutomatedCuration, instance=True, spec_set=True)
    s._metadata_expert = expert(raises=_dup())
    with pytest.raises(EgeriaFileSystemSurveyorError, match="could not be made"):
        s._find_element_guid(QN, type_name="FileFolder", qualified_name=True)


def test_doc_source_does_not_create_on_a_duplicated_name():
    from resource_explorer.doc_source_egeria import DocSourceLookupError, _find_ref_guid

    class Clients:
        def of(self, cls):
            return expert(raises=_dup())

    with pytest.raises(DocSourceLookupError, match="could not be made"):
        _find_ref_guid(Clients(), "ExternalReference::u")


def test_investigation_purposes_does_not_create_on_a_duplicated_name():
    from resource_explorer import investigation_purposes as ip

    with pytest.raises(ip.CouldNotCheck):
        ip._lookup(expert(raises=_dup()), "q")


def test_reference_catalog_proposal_does_not_create_on_a_duplicated_name():
    from resource_explorer.surveyors.database.egeria_reference_catalog import ProposalLookupError, _find_existing

    with pytest.raises(ProposalLookupError):
        _find_existing(expert(raises=_dup()), "q", "DataClass")


def test_bootstrap_data_class_seed_does_not_create_on_a_duplicated_name():
    from resource_explorer.surveyors.database import bootstrap_data_classes as bdc

    with pytest.raises(bdc.CouldNotCheck):
        bdc._existing_guid(expert(raises=_dup()), "q", "ValidValueDefinition")


# ── display names and file system paths: a TYPED string search (egeria_lookup.guid_by_exact_value) ──────────
# Architect ruling 2026-10-22 ("ARCHITECT on fast-lookup"): exact, one type, two hits = UNREADABLE, an Egeria
# error raises (never "absent"); the bootstrap displayName canary heals on an instant path.

from resource_explorer.egeria_lookup import ExactValueAmbiguous, guid_by_exact_value, is_deprecated  # noqa: E402

GUID2 = "bbbbbbbb-cccc-dddd-eeee-ffffffffffff"


def found(value, prop="displayName", *, guid=GUID, type_name="GlossaryTerm", supers=None, status="ACTIVE"):
    """One element as `find_metadata_elements_with_string` returns it (LIVE shape, fixtures/egeria_6_2)."""
    return {"elementGUID": guid, "headerVersion": 0, "status": "ACTIVE",
            "type": {"typeName": type_name, "typeCategory": "ENTITY_DEF",
                     "superTypeNames": list(supers or []) + ["Referenceable", "OpenMetadataRoot"]},
            "elementProperties": {"propertiesAsStrings": {prop: value, "qualifiedName": f"X::{value}::{guid[:4]}",
                                                          "contentStatus": status}}}


def searcher(answer=None, *, raises=None):
    """`create_autospec(MetadataExpert, spec_set=True)` scripted for the typed string search."""
    fake = create_autospec(MetadataExpert, instance=True, spec_set=True)
    if raises is not None:
        fake.find_metadata_elements_with_string.side_effect = raises
    else:
        fake.find_metadata_elements_with_string.return_value = answer
    return fake


def assert_typed_exact(fake, value, type_name):
    kw = fake.find_metadata_elements_with_string.call_args.kwargs
    assert kw["search_string"] == value and kw["metadata_element_type"] == type_name
    assert kw["starts_with"] is True and kw["ends_with"] is True and kw["ignore_case"] is False
    body = kw["body"]
    assert (body["searchString"], body["metadataElementTypeName"], body["startsWith"], body["endsWith"],
            body["ignoreCase"], body["graphQueryDepth"]) == (value, type_name, True, True, False, 0)
    fake.get_metadata_element_by_unique_name.assert_not_called()


def _no_slow_anywhere(*fakes):
    for fake in fakes:
        for name in SLOW_EXACT_VALUE_METHODS:
            if hasattr(fake, name):
                getattr(fake, name).assert_not_called()


# the helper's contract

def test_exact_value_one_hit_is_the_guid():
    fake = searcher([found("Q?")])
    assert guid_by_exact_value(fake, "Q?", property_name="displayName", type_name="GlossaryTerm") == GUID
    assert_typed_exact(fake, "Q?", "GlossaryTerm")
    _no_slow_anywhere(fake)


def test_exact_value_none_is_none():
    assert guid_by_exact_value(searcher(MISS), "Q?", property_name="displayName", type_name="GlossaryTerm") is None


def test_exact_value_an_empty_list_raises_never_absent():
    """Fail-on-purpose: `[]` is not Egeria's "No elements found" (`egeria_absence.is_absent([])` is unreadable),
    so returning None for it (a create cue) fails here."""
    from resource_explorer.egeria_absence import UNREADABLE, is_absent, lookup_exception_is_absent

    assert is_absent([]) == UNREADABLE
    with pytest.raises(QualifiedNameLookupError, match="empty element list") as caught:
        guid_by_exact_value(searcher([]), "Q?", property_name="displayName", type_name="GlossaryTerm")
    assert not lookup_exception_is_absent(caught.value)


def test_exact_value_a_typed_not_found_is_none():
    import httpx
    from pyegeria.core._exceptions import PyegeriaNotFoundException

    body = {"class": "VoidResponse", "relatedHTTPCode": 404, "exceptionErrorMessageId": "OMAG-GENERIC-404-001",
            "exceptionErrorMessage": "not found"}
    exc = PyegeriaNotFoundException(httpx.Response(200, json=body, request=httpx.Request("POST", "https://h/x")),
                                    {"caller_method": "find_metadata_elements_with_string"}, additional_info=body)
    from resource_explorer.egeria_absence import lookup_exception_is_absent
    if not lookup_exception_is_absent(exc):
        pytest.skip("this not-found shape is not classified absent; the raise path is covered below")
    assert guid_by_exact_value(searcher(raises=exc), "Q?", property_name="displayName",
                               type_name="GlossaryTerm") is None


def test_exact_value_two_hits_are_unreadable():
    fake = searcher([found("Q?"), found("Q?", guid=GUID2)])
    with pytest.raises(ExactValueAmbiguous) as caught:
        guid_by_exact_value(fake, "Q?", property_name="displayName", type_name="GlossaryTerm")
    assert caught.value.guids == [GUID, GUID2]
    from resource_explorer.egeria_absence import lookup_exception_is_absent
    assert not lookup_exception_is_absent(caught.value)


def test_exact_value_the_duplicate_404_is_unreadable_too():
    from tests.qn_lookup_bridge import multiple_entities_404
    with pytest.raises(ExactValueAmbiguous):
        guid_by_exact_value(searcher(raises=multiple_entities_404("Q?")), "Q?", property_name="displayName",
                            type_name="GlossaryTerm")


def test_exact_value_an_egeria_error_raises_never_absent():
    with pytest.raises(PyegeriaConnectionException):
        guid_by_exact_value(searcher(raises=PyegeriaConnectionException()), "Q?", property_name="displayName",
                            type_name="GlossaryTerm")


@pytest.mark.parametrize("odd", [None, "Server busy", {"elementGUID": GUID}, [{"no": "guid"}], ["x"]])
def test_exact_value_an_odd_answer_raises_never_absent(odd):
    with pytest.raises(QualifiedNameLookupError):
        guid_by_exact_value(searcher(odd), "Q?", property_name="displayName", type_name="GlossaryTerm")


def test_exact_value_another_type_in_the_answer_raises():
    with pytest.raises(QualifiedNameLookupError, match="is of type"):
        guid_by_exact_value(searcher([found("Q?", type_name="Glossary")]), "Q?", property_name="displayName",
                            type_name="GlossaryTerm")


def test_exact_value_a_subtype_is_the_type():
    fake = searcher([found("/p/a.csv", "pathName", type_name="CSVFile", supers=["DataFile"])])
    assert guid_by_exact_value(fake, "/p/a.csv", property_name="pathName", type_name="DataFile") == GUID


def test_exact_value_only_the_named_property_counts():
    """A literal like-match hits ANY string property: a description equal to the value is not a hit."""
    other = found("something else")
    other["elementProperties"]["propertiesAsStrings"]["description"] = "Q?"
    assert guid_by_exact_value(searcher([other]), "Q?", property_name="displayName",
                               type_name="GlossaryTerm") is None


def test_exact_value_a_full_page_raises_it_cannot_count():
    from resource_explorer.egeria_lookup import EXACT_VALUE_PAGE_SIZE
    page = [found("other", guid=f"{i:08x}-0000-4000-8000-000000000000") for i in range(EXACT_VALUE_PAGE_SIZE)]
    with pytest.raises(QualifiedNameLookupError, match="page is full"):
        guid_by_exact_value(searcher(page), "Q?", property_name="displayName", type_name="GlossaryTerm")


def test_exact_value_deprecated_excluded_only_when_asked():
    both = [found("Q?", status="DEPRECATED"), found("Q?", guid=GUID2)]
    assert guid_by_exact_value(searcher(both), "Q?", property_name="displayName", type_name="GlossaryTerm",
                               exclude_deprecated=True) == GUID2
    with pytest.raises(ExactValueAmbiguous):
        guid_by_exact_value(searcher(both), "Q?", property_name="displayName", type_name="GlossaryTerm")
    assert is_deprecated(found("Q?", status="DEPRECATED"))
    assert not is_deprecated(found("Q?"))


# the bootstrap displayName canary (the questions batch): an instant path, so it can heal

def _display_batch(tmp_path, **canary):
    from resource_explorer import bootstrap as bs
    return bs, bs.Batch(batch_id="questions", path=tmp_path, display_name="q", files=[],
                        canary={"display_name": "Q?", **canary})


def test_bootstrap_display_name_canary_hit_none_two_error(tmp_path):
    bs, batch = _display_batch(tmp_path)
    hit = searcher([found("Q?")])
    assert bs.canary_present(batch, expert=hit) is True
    assert_typed_exact(hit, "Q?", "GlossaryTerm")
    assert bs.canary_present(batch, expert=searcher(MISS)) is False            # verified missing: heal
    assert bs.canary_present(batch, expert=searcher([found("Q?"), found("Q?", guid=GUID2)])) is None   # no heal
    assert bs.canary_present(batch, expert=searcher(raises=PyegeriaConnectionException())) is None
    assert bs.canary_present(batch, expert=searcher("Server busy")) is None
    _no_slow_anywhere(hit)


def test_bootstrap_display_name_canary_counts_a_deprecated_term_as_present(tmp_path):
    """The canary asks "did this batch run"; reading a retired term as missing would re-run a CREATE document."""
    bs, batch = _display_batch(tmp_path)
    assert bs.canary_present(batch, expert=searcher([found("Q?", status="DEPRECATED")])) is True


def test_bootstrap_display_name_canary_uses_a_declared_type(tmp_path):
    bs, batch = _display_batch(tmp_path, type="Perspective")
    fake = searcher([found("Q?", type_name="Perspective")])
    assert bs.canary_present(batch, expert=fake) is True
    assert_typed_exact(fake, "Q?", "Perspective")


def test_bootstrap_display_name_canary_never_asks_the_classification_explorer(tmp_path, monkeypatch):
    """With no expert passed, the canary builds a MetadataExpert; the ClassificationExplorer (whose
    get_guid_for_name was the slow call) is not constructed."""
    from resource_explorer.surveyors import survey_definition_reader as sdr

    bs, batch = _display_batch(tmp_path)
    fake = searcher([found("Q?")])
    monkeypatch.setattr(sdr.SurveyDefinitionReader, "_connect_metadata_expert", lambda self: fake)
    monkeypatch.setattr(sdr.SurveyDefinitionReader, "_connect_classification_explorer",
                        lambda self: pytest.fail("the ClassificationExplorer was asked"))
    assert bs.canary_present(batch) is True


# the file system surveyor: a data file's path (catalog_and_survey) and the mount (publish_step_annotations)

def _fs():
    from resource_explorer.surveyors.filesystem.egeria_filesystem_surveyor import EgeriaFileSystemSurveyor
    s = EgeriaFileSystemSurveyor(platform_url="https://fake", view_server="v")
    s._automated_curation = create_autospec(AutomatedCuration, instance=True, spec_set=True)
    return s


def test_filesystem_file_path_hit_none_two_error():
    from resource_explorer.surveyors.filesystem.egeria_filesystem_surveyor import EgeriaFileSystemSurveyorError

    s = _fs()
    s._metadata_expert = searcher([found("/m/a.csv", "pathName", type_name="CSVFile", supers=["DataFile"])])
    assert s._find_element_guid("/m/a.csv", type_name=s.DATA_FILE_TYPE) == GUID
    s._metadata_expert = searcher(MISS)
    assert s._find_element_guid("/m/a.csv", type_name=s.DATA_FILE_TYPE) == ""
    s._metadata_expert = searcher([found("/m/a.csv", "pathName", type_name="DataFile"),
                                   found("/m/a.csv", "pathName", guid=GUID2, type_name="DataFile")])
    with pytest.raises(EgeriaFileSystemSurveyorError, match="could not be made"):
        s._find_element_guid("/m/a.csv", type_name=s.DATA_FILE_TYPE)
    s._metadata_expert = searcher(raises=PyegeriaConnectionException())
    with pytest.raises(EgeriaFileSystemSurveyorError, match="could not be made"):
        s._find_element_guid("/m/a.csv", type_name=s.DATA_FILE_TYPE)
    with pytest.raises(ValueError):
        s._find_element_guid("/m/a.csv")                     # a pathName search is always typed
    _no_slow_anywhere(s._automated_curation, s._metadata_expert)


def _fs_entity(guid=""):
    from resource_explorer.registry import FileSystemEntity
    return FileSystemEntity(slug="fs1", display_name="FS 1", local_mount_point="/m", canonical_mount_point="/m",
                            egeria_asset_guid=guid)


def _publish_mount_lookup(s, fs_entity, monkeypatch):
    """Drive the real publish_step_annotations; return (what stopped it, every name `_find_element_guid` was
    asked). Nothing after the mount look-up is under test, so whatever stops it later is returned, not raised."""
    asked = []
    real = s._find_element_guid

    def _recording(name, type_name=None, **kw):
        asked.append(name)
        return real(name, type_name, **kw)

    monkeypatch.setattr(s, "connect", lambda: None)
    monkeypatch.setattr(s, "_find_element_guid", _recording)
    try:
        s._publish_step_annotations(fs_entity, {"inventory": []}, None)
    except Exception as exc:                                     # noqa: BLE001 - what stopped it is returned
        return exc, asked
    return None, asked


def test_filesystem_mount_prefers_the_stored_guid_then_the_qualified_name_then_a_typed_path_search(monkeypatch):
    from resource_explorer.surveyors.filesystem.egeria_filesystem_surveyor import EgeriaFileSystemSurveyorError

    # 1. the GUID RE holds: the mount is never looked up
    s = _fs()
    s._metadata_expert = searcher(MISS)
    s._metadata_expert.get_metadata_element_by_unique_name.return_value = MISS
    exc, asked = _publish_mount_lookup(s, _fs_entity(GUID), monkeypatch)
    assert "/m" not in asked and "FileFolder::fs1:/m" not in asked
    assert not (isinstance(exc, EgeriaFileSystemSurveyorError) and "not yet published" in str(exc))

    # 2. none held, none found by qualifiedName or pathName: "not yet published", nothing created
    s = _fs()
    s._metadata_expert = searcher(MISS)
    s._metadata_expert.get_metadata_element_by_unique_name.return_value = MISS
    exc, asked = _publish_mount_lookup(s, _fs_entity(), monkeypatch)
    assert isinstance(exc, EgeriaFileSystemSurveyorError) and "not yet published" in str(exc)
    assert asked == ["FileFolder::fs1:/m", "/m"]
    s._metadata_expert.get_metadata_element_by_unique_name.assert_called_once_with(
        "FileFolder::fs1:/m", property_name="qualifiedName")
    assert_typed_exact_after_qn(s._metadata_expert, "/m", "FileFolder")
    s._automated_curation.create_folder_element_from_template.assert_not_called()

    # 3. found by the typed pathName search when the qualifiedName read misses
    s = _fs()
    s._metadata_expert = searcher([found("/m", "pathName", type_name="FileFolder")])
    s._metadata_expert.get_metadata_element_by_unique_name.return_value = MISS
    exc, asked = _publish_mount_lookup(s, _fs_entity(), monkeypatch)
    assert asked[:2] == ["FileFolder::fs1:/m", "/m"]
    assert not (isinstance(exc, EgeriaFileSystemSurveyorError) and "not yet published" in str(exc))

    # 4. two FileFolders hold the mount's pathName: could not be made, never "not yet published"
    s = _fs()
    s._metadata_expert = searcher([found("/m", "pathName", type_name="FileFolder"),
                                   found("/m", "pathName", guid=GUID2, type_name="FileFolder")])
    s._metadata_expert.get_metadata_element_by_unique_name.return_value = MISS
    exc, _ = _publish_mount_lookup(s, _fs_entity(), monkeypatch)
    assert isinstance(exc, EgeriaFileSystemSurveyorError) and "could not be made" in str(exc)

    # 5. Egeria error on the qualifiedName read: could not be made, the path search is not tried
    s = _fs()
    s._metadata_expert = searcher(MISS)
    s._metadata_expert.get_metadata_element_by_unique_name.side_effect = PyegeriaConnectionException()
    exc, asked = _publish_mount_lookup(s, _fs_entity(), monkeypatch)
    assert isinstance(exc, EgeriaFileSystemSurveyorError) and "could not be made" in str(exc)
    assert asked == ["FileFolder::fs1:/m"]
    _no_slow_anywhere(s._automated_curation, s._metadata_expert)


def assert_typed_exact_after_qn(fake, value, type_name):
    kw = fake.find_metadata_elements_with_string.call_args.kwargs
    assert kw["search_string"] == value and kw["metadata_element_type"] == type_name
    assert kw["starts_with"] is True and kw["ends_with"] is True
