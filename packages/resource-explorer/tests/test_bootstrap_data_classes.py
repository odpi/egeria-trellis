"""Tests for `bootstrap_data_classes.py`'s existence check.

Live verification (2026-09-22) found the script's `get_guid_for_name` calls
checked their result with a bare `if not data_class_guid` — but pyegeria's
`get_guid_for_name` returns the literal string "No elements found" (not
None/""/an exception) on a miss. That string is truthy, so a miss read as
"already exists" and every create was silently skipped: a real run against
the live dev platform reported "Created Data Classes: 0, Skipped: 6" when
in fact nothing had ever been created. These tests pin the fix — routing the
lookup through `survey_definition_reader._as_guid`, which rejects the
sentinel because it contains whitespace, unlike a real GUID — with a
duck-typed fake pyegeria client rather than live Egeria.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import inspect

import pytest
from pyegeria.core._server_client import ServerClient
from pyegeria.omvs.reference_data import ReferenceDataManager as _RealRef

from resource_explorer.surveyors.database import bootstrap_data_classes as bdc

NO_ELEMENTS_FOUND = "No elements found"

#: pyegeria's real `get_guid_for_name` (both DataDesigner and ReferenceDataManager inherit it).
_GUID_SIG = inspect.signature(ServerClient.get_guid_for_name)
_DEFAULT_PROPS = _GUID_SIG.parameters["property_name"].default
_FIND_SIG = inspect.signature(_RealRef.find_valid_value_definitions)


class _FakeDesigner:
    """Duck-typed stand-in for pyegeria's DataDesigner.

    `existing_names` simulates DataClasses pyegeria already knows about;
    anything else looks up as the miss sentinel, exactly like a real miss.
    """

    def __init__(self, existing_names: set[str] | None = None):
        self.existing_names = existing_names or set()
        self.created: list[dict] = []
        self.linked: list[tuple[str, str]] = []

    def create_egeria_bearer_token(self, *_args, **_kwargs):
        return "fake-token"

    def set_bearer_token(self, *_args, **_kwargs):   # the factory shares one token (Brief I)
        pass

    def get_guid_for_name(self, name, property_name=_DEFAULT_PROPS, type_name=None):
        _GUID_SIG.bind(self, name, property_name, type_name)
        if name in self.existing_names:
            return f"guid-for-{name}"
        return NO_ELEMENTS_FOUND

    def create_data_class(self, body):
        qn = body["properties"]["qualifiedName"]
        self.created.append(body)
        self.existing_names.add(qn)
        return f"guid-for-{qn}"

    def link_data_class_definition(self, data_definition_guid, data_class_guid):
        self.linked.append((data_definition_guid, data_class_guid))


class _FakeReferenceDataManager:
    def __init__(self, existing_names: set[str] | None = None):
        self.existing_names = existing_names or set()
        self.created: list[dict] = []
        self.links: list[tuple[str, str]] = []
        self.lookups: list[tuple[str, str | None]] = []

    def create_egeria_bearer_token(self, *_args, **_kwargs):
        return "fake-token"

    def set_bearer_token(self, *_args, **_kwargs):   # the factory shares one token (Brief I)
        pass

    def get_guid_for_name(self, name, property_name=_DEFAULT_PROPS, type_name=None):
        """Answers TYPED lookups only: an untyped one misses even an existing plain
        ValidValueDefinition, the way the Reference Data view's default type hides it."""
        _GUID_SIG.bind(self, name, property_name, type_name)
        self.lookups.append((name, type_name))
        if type_name == "ValidValueDefinition" and name in self.existing_names:
            return f"guid-for-{name}"
        return NO_ELEMENTS_FOUND

    def find_valid_value_definitions(self, *args, **kwargs):
        """Typed finds only (the keyword reconcile of an existing set)."""
        b = _FIND_SIG.bind(self, *args, **kwargs)
        b.apply_defaults()
        if b.arguments["kwargs"].get("metadata_element_type") != "ValidValueDefinition":
            raise AssertionError("untyped find")
        prefix = b.arguments["search_string"]
        hits = [{"elementHeader": {"guid": f"guid-for-{n}"}, "properties": {"qualifiedName": n}}
                for n in sorted(self.existing_names) if n.startswith(prefix)]
        return hits or NO_ELEMENTS_FOUND

    def create_valid_value_definition(self, body):
        qn = body["properties"]["qualifiedName"]
        self.created.append(body)
        self.existing_names.add(qn)
        return f"guid-for-{qn}"

    def link_valid_value_definition(self, vv_set_guid, vv_member_guid, **_kwargs):
        self.links.append((vv_set_guid, vv_member_guid))


@pytest.fixture(autouse=True)
def _egeria_env(monkeypatch):
    monkeypatch.setenv("EGERIA_PLATFORM_URL", "https://host.docker.internal:9443")
    monkeypatch.setenv("EGERIA_VIEW_SERVER", "qs-view-server")
    monkeypatch.setenv("EGERIA_USER", "erinoverview")
    monkeypatch.setenv("EGERIA_USER_PASSWORD", "secret")


_RELATED_SIG = inspect.signature(
    __import__("pyegeria.omvs.metadata_expert", fromlist=["MetadataExpert"]).MetadataExpert.get_related_metadata_elements)


class _FakeExpert:
    """MetadataExpert stand-in: answers the links the two fakes above recorded."""

    def __init__(self, designer, ref_manager):
        self.designer, self.ref_manager = designer, ref_manager

    def create_egeria_bearer_token(self, *_args, **_kwargs):
        return "fake-token"

    def set_bearer_token(self, *_args, **_kwargs):
        pass

    def get_related_metadata_elements(self, *args, **kwargs):
        a = _RELATED_SIG.bind(self, *args, **kwargs).arguments
        guid, rel = a["guid"], a["relationship_type"]
        if rel == "ValidValueMember":
            others = [m for s_, m in self.ref_manager.links if s_ == guid]
        else:
            others = [d for s_, d in self.designer.linked if s_ == guid]
        return {"elementList": [{"element": {"elementGUID": o}} for o in others]} if others else NO_ELEMENTS_FOUND


def _run_bootstrap(designer, ref_manager):
    # bootstrap_data_classes.py imports its pyegeria clients locally, inside
    # bootstrap_data_classes(), so patching them on their owning modules
    # (rather than on bootstrap_data_classes itself) is what actually
    # intercepts the call.
    with patch("pyegeria.omvs.data_designer.DataDesigner", return_value=designer), patch(
        "pyegeria.omvs.reference_data.ReferenceDataManager", return_value=ref_manager,
    ), patch("pyegeria.omvs.metadata_expert.MetadataExpert",
             return_value=_FakeExpert(designer, ref_manager)):
        return bdc.bootstrap_data_classes()


class TestSentinelMissIsNotAnExistingElement:
    """A "No elements found" miss must be treated as absent, not present."""

    def test_all_six_data_classes_are_created_on_first_run(self):
        designer = _FakeDesigner()
        ref_manager = _FakeReferenceDataManager()

        rc = _run_bootstrap(designer, ref_manager)

        assert rc == 0
        created_names = {
            body["properties"]["qualifiedName"] for body in designer.created
        }
        assert created_names == {
            f"DataClass::{spec['name']}" for spec in bdc.STANDARD_DATA_CLASSES
        }
        # None of the six were mistaken for "already exists".
        assert len(designer.created) == len(bdc.STANDARD_DATA_CLASSES)

    def test_valid_value_sets_and_keywords_are_also_created(self):
        designer = _FakeDesigner()
        ref_manager = _FakeReferenceDataManager()

        _run_bootstrap(designer, ref_manager)

        created_names = {body["properties"]["qualifiedName"] for body in ref_manager.created}
        for spec in bdc.STANDARD_DATA_CLASSES:
            assert f"ValidValuesSet::{spec['name']}Keywords" in created_names
            for kw in spec["dataPatterns"]:
                assert f"ValidValueDefinition::{spec['name']}Keyword::{kw}" in created_names


class TestBodyShapesMatchWhatEgeriaActuallyAccepts:
    """Live verification (2026-09-22) found the sentinel fix alone was not
    enough to seed the platform: fixing the existence check just uncovered a
    second, real bug underneath it — `create_data_class`'s body omitted
    `properties.class`/`isOwnAnchor`, which Egeria rejects with a 400
    (`CLIENT_ERROR_400`), and `link_valid_value_definition` was called with no
    body at all, a gap `egeria_reference_catalog.py`'s own docstring already
    named ("makes pyegeria synthesise one and POST it un-serialised — a live
    bug this does not copy"). Both are fixed to match the body shapes
    `egeria_reference_catalog.py`'s `build_proposed_data_class_body`/
    `build_proposed_valid_value_set_body` already use correctly, verified
    end-to-end against the live dev platform (6/6 Data Classes created)."""

    def test_create_data_class_body_has_the_required_class_and_anchor_fields(self):
        designer = _FakeDesigner()
        ref_manager = _FakeReferenceDataManager()
        _run_bootstrap(designer, ref_manager)

        for body in designer.created:
            assert body["isOwnAnchor"] is True
            assert body["properties"]["class"] == "DataClassProperties"

    def test_valid_value_set_and_keyword_bodies_declare_isOwnAnchor(self):
        designer = _FakeDesigner()
        ref_manager = _FakeReferenceDataManager()
        _run_bootstrap(designer, ref_manager)

        for body in ref_manager.created:
            assert body["isOwnAnchor"] is True
            assert body["properties"]["class"] == "ValidValueDefinitionProperties"

    def test_linking_a_keyword_passes_an_explicit_relationship_body(self):
        # link_valid_value_definition(set_guid, member_guid, body=...) — a
        # missing `body` kwarg is exactly the bug being pinned here.
        # _FakeReferenceDataManager.link_valid_value_definition only records
        # (vv_set_guid, vv_member_guid) positionally and swallows kwargs, so
        # use a stricter fake that captures the body too.
        class _StrictRefManager(_FakeReferenceDataManager):
            def __init__(self, *a, **kw):
                super().__init__(*a, **kw)
                self.link_bodies: list[dict | None] = []

            def link_valid_value_definition(self, vv_set_guid, vv_member_guid, body=None):
                self.link_bodies.append(body)
                return super().link_valid_value_definition(vv_set_guid, vv_member_guid, body=body)

        strict_ref = _StrictRefManager()
        _run_bootstrap(_FakeDesigner(), strict_ref)
        assert strict_ref.link_bodies
        for body in strict_ref.link_bodies:
            assert body is not None
            assert body["class"] == "NewRelationshipRequestBody"
            assert body["properties"]["class"] == "ValidValueMemberProperties"


class TestRealExistingElementsAreSkipped:
    """A real GUID (not the sentinel) must still short-circuit creation —
    the fix must not turn every run into an unconditional re-create."""

    def test_pre_existing_data_class_is_skipped_not_recreated(self):
        designer = _FakeDesigner(existing_names={"DataClass::EmailAddress"})
        ref_manager = _FakeReferenceDataManager()

        _run_bootstrap(designer, ref_manager)

        created_names = {body["properties"]["qualifiedName"] for body in designer.created}
        assert "DataClass::EmailAddress" not in created_names
        # The other five are still created normally.
        assert len(created_names) == len(bdc.STANDARD_DATA_CLASSES) - 1


class TestPreFixBehaviourWouldHaveSkippedEverything:
    """Documents the bug directly: the naive `if not guid` check treats the
    sentinel as truthy and would report every element as already existing."""

    def test_naive_truthiness_check_is_fooled_by_the_sentinel(self):
        designer = _FakeDesigner()
        guid = designer.get_guid_for_name("DataClass::EmailAddress")
        assert guid == NO_ELEMENTS_FOUND
        assert bool(guid) is True  # the bug: a miss reads as truthy
        assert bdc._as_guid(guid) is None  # the fix: rejected as not a real GUID


def _all_vvd_names() -> set[str]:
    names = set()
    for spec in bdc.STANDARD_DATA_CLASSES:
        names.add(f"ValidValuesSet::{spec['name']}Keywords")
        names.update(f"ValidValueDefinition::{spec['name']}Keyword::{kw}" for kw in spec["dataPatterns"])
    return names


class TestPlainValidValueDefinitionsAreLookedUpTyped:
    """The keyword sets and keywords are plain ValidValueDefinitions. Found 2026-10-13: Egeria's
    Reference Data view defaults its type to ReferenceDataValue; the existence check looks them up
    typed so an existing one is never created twice."""

    def test_existing_sets_and_keywords_are_found_and_not_recreated(self):
        designer = _FakeDesigner(existing_names={f"DataClass::{s['name']}" for s in bdc.STANDARD_DATA_CLASSES})
        ref_manager = _FakeReferenceDataManager(existing_names=_all_vvd_names())

        assert _run_bootstrap(designer, ref_manager) == 0

        created = [b["properties"]["qualifiedName"] for b in ref_manager.created]
        assert created == [bdc.canary_qualified_name(bdc.STANDARD_DATA_CLASSES)], (
            "an existing plain ValidValueDefinition was created again")
        assert designer.created == []
        assert {t for _n, t in ref_manager.lookups} == {"ValidValueDefinition"}


class TestALookupThatRaisesCreatesNothing:
    """6.2.2 audit: a lookup that RAISES is "could not check", never "absent, so create"."""

    def test_a_raising_set_lookup_stops_and_creates_nothing(self):
        class Raising(_FakeReferenceDataManager):
            def get_guid_for_name(self, name, property_name=_DEFAULT_PROPS, type_name=None):
                if name.startswith("ValidValuesSet::"):
                    raise RuntimeError("platform not answering")
                return super().get_guid_for_name(name, property_name, type_name)

        designer = _FakeDesigner(existing_names={f"DataClass::{s['name']}" for s in bdc.STANDARD_DATA_CLASSES})
        ref_manager = Raising()

        assert _run_bootstrap(designer, ref_manager) == 1
        assert ref_manager.created == [] and ref_manager.links == []
        # The reason, from the outcome the log line and activity row are built from (not from log
        # capture, which another test in the suite can disable).
        outcome = bdc.ensure_data_classes(designer, ref_manager, _FakeExpert(designer, ref_manager))
        assert outcome.status == bdc.COULD_NOT_CHECK
        assert "could not check 'ValidValuesSet::EmailAddressKeywords'" in outcome.line
        assert ref_manager.created == [] and ref_manager.links == []

    def test_a_raising_data_class_lookup_stops_and_creates_nothing(self):
        class Raising(_FakeDesigner):
            def get_guid_for_name(self, name, property_name=_DEFAULT_PROPS, type_name=None):
                raise RuntimeError("Multiple elements found for supplied name!")

        designer, ref_manager = Raising(), _FakeReferenceDataManager()
        assert _run_bootstrap(designer, ref_manager) == 1
        assert designer.created == [] and ref_manager.created == []
