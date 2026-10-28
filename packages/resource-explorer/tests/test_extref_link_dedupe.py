"""ExternalReferenceLink is MULTI-LINK: Egeria creates another copy on every link call (live, dev
Egeria 2026-10-14: 132 links, 87 duplicates; repo surveys made one per run, doc-source publish a few,
one from an outbox retry). RE links an element to a reference only when no link between exactly those
two exists, and never when it cannot tell.

Fakes are `create_autospec(<real pyegeria class>, spec_set=True, instance=True)` at the pyegeria
boundary only, and they answer the LIVE shape of `get_related_metadata_elements`: a dict with
`elementList`, each entry carrying `relationshipGUID`, `versions.createTime` and the far end under
`element` with a top-level `elementGUID`; an exhausted list is pyegeria's "No elements found".
"""
from __future__ import annotations

import logging
import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock, create_autospec

import pytest
from pyegeria import AutomatedCuration, ExternalReferences
from pyegeria.omvs.metadata_expert import MetadataExpert

NO_ELEMENTS_FOUND = "No elements found"      # pyegeria.core._globals.NO_ELEMENTS_FOUND


def _guid() -> str:
    return str(uuid.uuid4())


class FakeEgeria:
    """A tiny in-memory Egeria holding ExternalReferences and ExternalReferenceLinks."""

    def __init__(self):
        self.refs: dict[str, str] = {}                      # qualifiedName -> guid
        self.links: list[dict] = []                         # {rel, element, ref, created}
        self.related_calls: list[dict] = []
        self.expert = create_autospec(MetadataExpert, spec_set=True, instance=True)
        self.ext = create_autospec(ExternalReferences, spec_set=True, instance=True)
        self.ac = create_autospec(AutomatedCuration, spec_set=True, instance=True)
        self.expert.get_related_metadata_elements.side_effect = self._related
        self.expert.get_metadata_element_by_guid.side_effect = (
            lambda guid, *a, **k: {"elementGUID": guid} if guid in self.refs.values() else "No element found")
        self.ext.create_external_reference.side_effect = self._create_ref
        self.ext.link_external_reference.side_effect = self._link
        self.ac.get_guid_for_name.side_effect = (
            lambda name, *a, **k: self.refs.get(name) or "No element found")

    # pyegeria boundary behaviour ------------------------------------------------------------
    def _create_ref(self, body=None):
        guid = _guid()
        self.refs[body["properties"]["qualifiedName"]] = guid
        return guid

    def _link(self, element_guid, ext_ref_guid, body=None):
        return self.add_link(element_guid, ext_ref_guid)

    def add_link(self, element_guid, ref_guid, created="2026-10-14T10:00:00.000+00:00"):
        rel = _guid()
        self.links.append({"rel": rel, "element": element_guid, "ref": ref_guid, "created": created})
        return rel

    def _related(self, guid, relationship_type, body, for_lineage=None, for_duplicate_processing=None,
                 starting_at_end=0, start_from=0, page_size=500, timeout=30, mermaid_only=False, **kw):
        self.related_calls.append({"guid": guid, "type": relationship_type, "end": starting_at_end,
                                   "start_from": start_from, "page_size": page_size})
        assert relationship_type == "ExternalReferenceLink"
        assert starting_at_end == 2, "the reference is at end 2 of ExternalReferenceLink"
        mine = [lk for lk in self.links if lk["ref"] == guid]
        page = mine[start_from:start_from + page_size]
        if not page:
            return NO_ELEMENTS_FOUND
        return {
            "class": "RelatedMetadataElementsResponse",
            "startingElement": {"elementGUID": guid},
            "elementList": [{
                "class": "RelatedMetadataElement",
                "relationshipGUID": lk["rel"],
                "type": {"typeName": "ExternalReferenceLink"},
                "versions": {"createdBy": "resourceexplorernpa", "createTime": lk["created"]},
                "startingElementAtEnd1": False,
                "element": {"elementGUID": lk["element"], "type": {"typeName": "GitHubRepository"},
                            "versions": {"createTime": "2026-10-01T00:00:00.000+00:00"}},
            } for lk in page],
            "mermaidGraph": "",
        }

    def links_between(self, element_guid, ref_guid):
        return [lk for lk in self.links if lk["element"] == element_guid and lk["ref"] == ref_guid]


@pytest.fixture
def eg():
    return FakeEgeria()


# ── the repo-survey homepage publisher ────────────────────────────────────────────────────────

def _publisher(eg):
    from resource_explorer.surveyors.egeria_publisher import EgeriaPublisher

    pub = EgeriaPublisher(platform_url="https://egeria.invalid", view_server="vs",
                          registry=MagicMock())
    pub._registry.get.return_value = SimpleNamespace(homepage_url="https://egeria-project.org")
    pub._external_references = eg.ext
    pub._metadata_expert = eg.expert
    pub._automated_curation = eg.ac
    return pub


_RESULT = SimpleNamespace(resource_slug="egeria", github_url="https://github.com/odpi/egeria",
                          project_display_name="Egeria")


def test_homepage_published_twice_leaves_one_link_and_the_second_makes_no_link_call(eg):
    pub, asset = _publisher(eg), _guid()
    ref1 = pub._publish_homepage_reference(_RESULT, asset)
    assert eg.ext.link_external_reference.call_count == 1
    ref2 = pub._publish_homepage_reference(_RESULT, asset)
    assert ref1 == ref2
    assert len(eg.links_between(asset, ref1)) == 1
    assert eg.ext.link_external_reference.call_count == 1, "the re-publish must not link again"


def test_homepage_link_to_a_different_asset_does_not_stop_linking_this_one(eg):
    pub, other, asset = _publisher(eg), _guid(), _guid()
    pub._publish_homepage_reference(_RESULT, other)
    ref = pub._publish_homepage_reference(_RESULT, asset)
    assert len(eg.links_between(other, ref)) == 1
    assert len(eg.links_between(asset, ref)) == 1


def test_homepage_existing_link_on_page_two_is_respected(eg):
    """500 other assets link the same reference first; this asset's link is entry 501 (page 2)."""
    pub, asset = _publisher(eg), _guid()
    ref = pub._publish_homepage_reference(_RESULT, _guid())
    for _ in range(499):
        eg.add_link(_guid(), ref)
    eg.add_link(asset, ref)
    calls_before = eg.ext.link_external_reference.call_count
    pub._publish_homepage_reference(_RESULT, asset)
    assert eg.ext.link_external_reference.call_count == calls_before
    assert len(eg.links_between(asset, ref)) == 1
    assert [c["start_from"] for c in eg.related_calls][-3:] == [0, 500, 1000]


def test_homepage_lookup_that_cannot_be_made_makes_no_link_and_says_so(eg, caplog):
    pub, asset = _publisher(eg), _guid()
    eg.expert.get_related_metadata_elements.side_effect = TimeoutError("read timed out")
    caplog.set_level(logging.DEBUG)
    pub._publish_homepage_reference(_RESULT, asset)
    eg.ext.link_external_reference.assert_not_called()
    warned = [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]
    assert any("could not check" in w for w in warned), warned


def test_homepage_failed_link_logs_at_warning(eg, caplog):
    pub, asset = _publisher(eg), _guid()
    eg.ext.link_external_reference.side_effect = RuntimeError("Egeria said no")
    caplog.set_level(logging.DEBUG)
    pub._publish_homepage_reference(_RESULT, asset)
    warned = [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]
    assert any("Egeria said no" in w for w in warned), warned


# ── Enrichment doc-source publish ─────────────────────────────────────────────────────────────

class _Clients:
    def __init__(self, eg):
        self._by_cls = {MetadataExpert: eg.expert, ExternalReferences: eg.ext, AutomatedCuration: eg.ac}

    def of(self, cls, **kw):
        return self._by_cls[cls]


_SOURCE = {"url": "https://docs.example.com/dict", "label": "Dict", "source_type": "data_dictionary"}


def test_doc_source_retry_after_a_successful_link_does_not_link_again(eg):
    from resource_explorer.doc_source_egeria import publish_doc_source

    asset = _guid()
    first = publish_doc_source(dict(_SOURCE), asset, clients=_Clients(eg))
    assert first["ok"] and first["link_guid"], first
    # The outbox retries with the reference it stored (the #661 shape: link made, row not marked done).
    again = publish_doc_source(dict(_SOURCE), asset, clients=_Clients(eg), known_ref_guid=first["ref_guid"])
    assert again["ok"], again
    assert eg.ext.link_external_reference.call_count == 1
    assert len(eg.links_between(asset, first["ref_guid"])) == 1
    assert again["link_guid"] == first["link_guid"], "the existing relationship GUID is returned"


def test_doc_source_existing_duplicates_return_the_oldest_link(eg):
    from resource_explorer.doc_source_egeria import publish_doc_source

    asset = _guid()
    first = publish_doc_source(dict(_SOURCE), asset, clients=_Clients(eg))
    eg.links[0]["created"] = "2026-10-14T12:00:00.000+00:00"
    oldest = eg.add_link(asset, first["ref_guid"], created="2026-10-01T09:00:00.000+00:00")
    again = publish_doc_source(dict(_SOURCE), asset, clients=_Clients(eg), known_ref_guid=first["ref_guid"])
    assert again["link_guid"] == oldest
    assert eg.ext.link_external_reference.call_count == 1


def test_doc_source_lookup_that_cannot_be_made_makes_no_link_and_says_so(eg):
    from resource_explorer.doc_source_egeria import publish_doc_source

    eg.expert.get_related_metadata_elements.side_effect = TimeoutError("read timed out")
    result = publish_doc_source(dict(_SOURCE), _guid(), clients=_Clients(eg))
    eg.ext.link_external_reference.assert_not_called()
    assert result["link_guid"] == ""
    assert "could not check" in result["error"]


def test_doc_source_unrecognised_answer_is_could_not_check(eg):
    from resource_explorer.doc_source_egeria import publish_doc_source

    eg.expert.get_related_metadata_elements.side_effect = lambda *a, **k: "Something else went wrong"
    result = publish_doc_source(dict(_SOURCE), _guid(), clients=_Clients(eg))
    eg.ext.link_external_reference.assert_not_called()
    assert "could not check" in result["error"]


def test_doc_source_entry_without_an_element_guid_is_could_not_check(eg):
    """An entry we cannot attribute to an element might be ours: never link past it."""
    from resource_explorer.doc_source_egeria import publish_doc_source

    asset = _guid()
    first = publish_doc_source(dict(_SOURCE), _guid(), clients=_Clients(eg))
    real = eg._related

    def strip(*a, **k):
        ans = real(*a, **k)
        if isinstance(ans, dict):
            for e in ans["elementList"]:
                e["element"] = {}
        return ans
    eg.expert.get_related_metadata_elements.side_effect = strip
    result = publish_doc_source(dict(_SOURCE), asset, clients=_Clients(eg), known_ref_guid=first["ref_guid"])
    assert eg.ext.link_external_reference.call_count == 1
    assert "could not check" in result["error"]


# ── the helper itself ─────────────────────────────────────────────────────────────────────────

def test_helper_reads_related_element_alias_and_pages(eg):
    from resource_explorer.egeria_extref_link import ALREADY_LINKED, link_external_reference_once

    ref, asset = _guid(), _guid()
    for _ in range(3):
        eg.add_link(_guid(), ref)
    rel = eg.add_link(asset, ref)
    real = eg._related

    def as_related_element(*a, **k):
        ans = real(*a, **k)
        if isinstance(ans, dict):
            for e in ans["elementList"]:
                e["relatedElement"] = e.pop("element")
        return ans
    eg.expert.get_related_metadata_elements.side_effect = as_related_element
    out = link_external_reference_once(eg.expert, eg.ext, asset, ref, page_size=2)
    assert out.outcome == ALREADY_LINKED and out.relationship_guid == rel
    assert [c["start_from"] for c in eg.related_calls] == [0, 2, 4]
    eg.ext.link_external_reference.assert_not_called()
