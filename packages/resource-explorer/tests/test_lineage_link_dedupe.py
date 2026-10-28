"""LineageMapping is MULTI-LINK (`OpenMetadataTypesArchive1_2.getLineageMappingRelationship`,
`setMultiLink(true)`): every `LineageLinker.link_lineage` call makes another relationship between the same
two columns. The database surveyor's column lineage linked every (source, target) pair on every publish
with no read first, so each re-survey added one duplicate per pair, and a pair the SQLGlot tree yields
twice was linked twice in one run. RE now builds each pair once per run and links it only when no
LineageMapping between exactly those two columns exists, and never when it cannot tell.

End 1 of LineageMapping is `sourceElement`, end 2 `targetElement` (the type archive; `link_lineage`'s
`from-elements/{elementOneGUID}` is end 1, `LineageHandler.linkLineage` -> `createRelatedElementsInStore(
type, elementOneGUID, elementTwoGUID)`). The read starts at the target view column (end 2): a view column
is fed by the few leaves of one expression, a base-table column can feed every view built on it.

Also here: the shared link-once reader keeps reading past a FILTERED page. Egeria pages the relationships
first and then drops those whose far end fails validation (`MetadataElementHandler.
getRelatedMetadataElements`); a page with relationships but none left after filtering comes back as an
EMPTY `elementList` (pyegeria: "No elements returned"), while the end of the list carries no `elementList`
at all (pyegeria: "No elements found"; live dev Egeria 2026-10-14, read-only probe: at, and 1000 past,
the end of a 4-link list). `RelationshipAccumulator.getRelationships` documents it: "Null means no
relationships were retrieved ...; Empty list means all retrieved relationships were filtered out and can
retrieve again."

Fakes are `create_autospec(<pyegeria class>, spec_set=True, instance=True)` at the pyegeria boundary only,
answering the LIVE shape: a dict with `elementList`, entries carrying `relationshipGUID`,
`versions.createTime` and the far end under `element` with a top-level `elementGUID`.
"""
from __future__ import annotations

import logging
import uuid
from unittest.mock import create_autospec

import pytest
from pyegeria import AutomatedCuration, ExternalReferences
from pyegeria.omvs.lineage_linker import LineageLinker
from pyegeria.omvs.metadata_expert import MetadataExpert

NO_ELEMENTS_FOUND = "No elements found"        # pyegeria: no elementList at all (the end of the list)
NO_ELEMENTS_RETURNED = "No elements returned"  # pyegeria: an EMPTY elementList (a filtered page)


def _guid() -> str:
    return str(uuid.uuid4())


class FakeEgeria:
    """In-memory columns and LineageMapping relationships (end 1 source, end 2 target)."""

    def __init__(self):
        self.columns: dict[str, str] = {}                  # name -> guid
        self.links: list[dict] = []                        # {rel, src, tgt, created}
        self.related_calls: list[dict] = []
        self.expert = create_autospec(MetadataExpert, spec_set=True, instance=True)
        self.linker = create_autospec(LineageLinker, spec_set=True, instance=True)
        self.ac = create_autospec(AutomatedCuration, spec_set=True, instance=True)
        self.expert.get_related_metadata_elements.side_effect = self._related
        self.linker.link_lineage.side_effect = self._link
        self.ac.get_guid_for_name.side_effect = lambda name, *a, **k: self.columns.get(name) or NO_ELEMENTS_FOUND

    def column(self, *names: str) -> str:
        guid = _guid()
        for name in names:
            self.columns[name] = guid
        return guid

    def _link(self, element_one_guid, relationship_type_name, element_two_guid, body):
        assert relationship_type_name == "LineageMapping"
        return self.add_link(element_one_guid, element_two_guid)

    def add_link(self, src, tgt, created="2026-10-14T10:00:00.000+00:00"):
        rel = _guid()
        self.links.append({"rel": rel, "src": src, "tgt": tgt, "created": created})
        return rel

    def _related(self, guid, relationship_type, body, for_lineage=None, for_duplicate_processing=None,
                 starting_at_end=0, start_from=0, page_size=500, timeout=30, mermaid_only=False, **kw):
        self.related_calls.append({"guid": guid, "type": relationship_type, "end": starting_at_end,
                                   "start_from": start_from, "page_size": page_size})
        assert relationship_type == "LineageMapping"
        assert starting_at_end in (1, 2)
        if starting_at_end == 2:
            mine = [(lk, lk["src"]) for lk in self.links if lk["tgt"] == guid]
        else:
            mine = [(lk, lk["tgt"]) for lk in self.links if lk["src"] == guid]
        page = mine[start_from:start_from + page_size]
        if not page:
            return NO_ELEMENTS_FOUND
        return {
            "class": "RelatedMetadataElementsResponse",
            "startingElement": {"elementGUID": guid},
            "elementList": [{
                "class": "RelatedMetadataElement",
                "relationshipGUID": lk["rel"],
                "type": {"typeName": "LineageMapping"},
                "versions": {"createdBy": "resourceexplorernpa", "createTime": lk["created"]},
                "startingElementAtEnd1": starting_at_end == 1,
                "element": {"elementGUID": far, "type": {"typeName": "RelationalColumn"}},
            } for lk, far in page],
            "mermaidGraph": "",
        }

    def links_between(self, src, tgt):
        return [lk for lk in self.links if lk["src"] == src and lk["tgt"] == tgt]


class _Clients:
    def __init__(self, eg):
        self._by_cls = {MetadataExpert: eg.expert, LineageLinker: eg.linker, AutomatedCuration: eg.ac}

    def of(self, cls, **kw):
        return self._by_cls[cls]


@pytest.fixture
def eg():
    return FakeEgeria()


def _surveyor(eg):
    from resource_explorer.surveyors.database.egeria_database_surveyor import EgeriaDatabaseSurveyor

    s = EgeriaDatabaseSurveyor(platform_url="https://egeria.invalid", view_server="vs")
    s._automated_curation = eg.ac
    s._clients = lambda: _Clients(eg)
    return s


def _db():
    from resource_explorer.registry import DatabaseEntity

    return DatabaseEntity(slug="shop", display_name="Shop", db_type="postgresql", host="h", port=5432,
                          database_name="shop")


def _leaf(table, column):
    return {"name": column, "source": table}


# A view v(total, label): total <- orders.amount + orders.tax, label <- customers.name.
_VIEW = {"schema": "public", "name": "v", "lineage": {
    "total": {"downstream": [_leaf("orders", "amount"), _leaf("orders", "tax")]},
    "label": {"downstream": [_leaf("customers", "name")]},
}}


def _columns(eg):
    return {
        "amount": eg.column("shop.public.orders.amount"),
        "tax": eg.column("shop.public.orders.tax"),
        "name": eg.column("shop.public.customers.name"),
        "total": eg.column("shop.public.v.total"),
        "label": eg.column("shop.public.v.label"),
    }


def _pairs(c):
    return [(c["amount"], c["total"]), (c["tax"], c["total"]), (c["name"], c["label"])]


# ── the database surveyor ─────────────────────────────────────────────────────────────────────

def test_publishing_the_same_view_twice_leaves_one_link_per_pair(eg):
    c = _columns(eg)
    _surveyor(eg)._publish_column_lineage(_db(), [_VIEW])
    assert eg.linker.link_lineage.call_count == 3
    _surveyor(eg)._publish_column_lineage(_db(), [_VIEW])
    for src, tgt in _pairs(c):
        assert len(eg.links_between(src, tgt)) == 1, (src, tgt)
    assert eg.linker.link_lineage.call_count == 3, "the re-publish must not link again"
    assert len(eg.links) == 3


def test_the_read_starts_at_the_target_column_end_2(eg):
    c = _columns(eg)
    _surveyor(eg)._publish_column_lineage(_db(), [_VIEW])
    assert eg.related_calls, "every link is preceded by a read"
    assert {call["end"] for call in eg.related_calls} == {2}
    assert {call["guid"] for call in eg.related_calls} == {c["total"], c["label"]}
    kw = eg.linker.link_lineage.call_args_list[0].kwargs
    assert (kw["element_one_guid"], kw["element_two_guid"]) in _pairs(c), "source is end 1, target end 2"


def test_a_pair_repeated_within_one_run_gets_one_link(eg):
    """`public.orders` and `orders` are the same source column; SQLGlot can yield both leaves."""
    c = _columns(eg)
    view = {"schema": "public", "name": "v", "lineage": {"total": {"downstream": [
        _leaf("orders", "amount"), _leaf("public.orders", "amount"),
        {"name": "x", "downstream": [_leaf("orders", "amount")]}]}}}
    _surveyor(eg)._publish_column_lineage(_db(), [view])
    assert len(eg.links_between(c["amount"], c["total"])) == 1
    assert eg.linker.link_lineage.call_count == 1


def test_the_same_pair_from_two_views_in_one_run_gets_one_link(eg):
    """Two views whose columns resolve to one Egeria element (the short-name fallback)."""
    src = eg.column("shop.public.orders.amount")
    tgt = eg.column("v.total")                      # only the fallback name resolves
    views = [{"schema": "public", "name": "v", "lineage": {"total": {"downstream": [_leaf("orders", "amount")]}}},
             {"schema": "sales", "name": "v", "lineage": {"total": {"downstream": [_leaf("public.orders", "amount")]}}}]
    _surveyor(eg)._publish_column_lineage(_db(), views)
    assert len(eg.links_between(src, tgt)) == 1
    assert eg.linker.link_lineage.call_count == 1


def test_an_existing_link_made_by_someone_else_is_respected(eg):
    c = _columns(eg)
    eg.add_link(c["amount"], c["total"])
    _surveyor(eg)._publish_column_lineage(_db(), [_VIEW])
    assert len(eg.links_between(c["amount"], c["total"])) == 1
    assert eg.linker.link_lineage.call_count == 2


def test_a_lookup_that_cannot_be_made_creates_no_link_and_is_recorded(eg, caplog):
    _columns(eg)
    eg.expert.get_related_metadata_elements.side_effect = TimeoutError("read timed out")
    caplog.set_level(logging.DEBUG)
    s = _surveyor(eg)
    s._publish_column_lineage(_db(), [_VIEW])
    eg.linker.link_lineage.assert_not_called()
    assert eg.links == []
    warned = [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]
    assert any("could not check" in w for w in warned), warned
    assert "shop.public.orders.amount -> shop.public.v.total" in s.lineage_not_checked


def test_an_unreadable_answer_creates_no_link(eg):
    _columns(eg)
    eg.expert.get_related_metadata_elements.side_effect = lambda *a, **k: "Something else went wrong"
    _surveyor(eg)._publish_column_lineage(_db(), [_VIEW])
    eg.linker.link_lineage.assert_not_called()


def test_a_failed_link_is_logged_and_the_others_still_link(eg, caplog):
    c = _columns(eg)
    real = eg._link

    def refuse_first(element_one_guid, relationship_type_name, element_two_guid, body):
        if (element_one_guid, element_two_guid) == (c["amount"], c["total"]):
            raise RuntimeError("Egeria said no")
        return real(element_one_guid, relationship_type_name, element_two_guid, body)
    eg.linker.link_lineage.side_effect = refuse_first
    caplog.set_level(logging.DEBUG)
    _surveyor(eg)._publish_column_lineage(_db(), [_VIEW])
    assert len(eg.links) == 2
    warned = [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]
    assert any("Egeria said no" in w for w in warned), warned


# ── the shared reader: a filtered page is not the end of the list ─────────────────────────────

class _FilteringExtRefEgeria:
    """ExternalReferenceLinks to one reference where Egeria filtered out a whole page: the answer for
    page 2 is an EMPTY elementList ("No elements returned"), and this element's link is on page 3."""

    def __init__(self, element_guid: str, *, filtered_pages: int = 1):
        self.element_guid, self.rel = element_guid, _guid()
        self.filtered_pages = filtered_pages
        self.expert = create_autospec(MetadataExpert, spec_set=True, instance=True)
        self.ext = create_autospec(ExternalReferences, spec_set=True, instance=True)
        self.expert.get_related_metadata_elements.side_effect = self._related
        self.ext.link_external_reference.return_value = _guid()
        self.starts: list[int] = []

    def _entry(self, far, rel=None):
        return {"class": "RelatedMetadataElement", "relationshipGUID": rel or _guid(),
                "versions": {"createTime": "2026-10-14T10:00:00.000+00:00"},
                "element": {"elementGUID": far}}

    def _related(self, guid, relationship_type, body, starting_at_end=0, start_from=0, page_size=500, **kw):
        self.starts.append(start_from)
        page = start_from // page_size
        if page == 0:
            return {"startingElement": {"elementGUID": guid}, "elementList": [self._entry(_guid())]}
        if page <= self.filtered_pages:
            return NO_ELEMENTS_RETURNED
        if page == self.filtered_pages + 1:
            return {"startingElement": {"elementGUID": guid},
                    "elementList": [self._entry(self.element_guid, self.rel)]}
        return NO_ELEMENTS_FOUND


def test_a_filtered_page_is_read_past_and_the_link_beyond_it_is_found():
    from resource_explorer.egeria_extref_link import ALREADY_LINKED, link_external_reference_once

    asset = _guid()
    fe = _FilteringExtRefEgeria(asset)
    out = link_external_reference_once(fe.expert, fe.ext, asset, _guid(), page_size=1)
    assert out.outcome == ALREADY_LINKED and out.relationship_guid == fe.rel
    fe.ext.link_external_reference.assert_not_called()
    assert fe.starts == [0, 1, 2, 3]


def test_a_long_run_of_filtered_pages_is_could_not_check_not_the_end():
    from resource_explorer.egeria_extref_link import COULD_NOT_CHECK, link_external_reference_once

    asset = _guid()
    fe = _FilteringExtRefEgeria(asset, filtered_pages=10_000)
    out = link_external_reference_once(fe.expert, fe.ext, asset, _guid(), page_size=1)
    assert out.outcome == COULD_NOT_CHECK
    fe.ext.link_external_reference.assert_not_called()
    assert len(fe.starts) < 50, "a bounded number of filtered pages is read, not every page"
