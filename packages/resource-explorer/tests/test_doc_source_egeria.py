"""Tests for doc_source_egeria.py's publish/unpublish/read-back — the
ExternalReference linkage BRIEF-DATABASE-DOCUMENTATION-SOURCES.md slice 1
asks for, reusing egeria_publisher.py's `_publish_homepage_reference`
pattern. Mocks `_client`/`_find_ref_guid`/pyegeria rather than talking to a
real platform — the gate step does that separately, live, against
laz_local_adventureworks (see DOC-SOURCES-DECLARE-AND-PROBE-IMPLEMENTED.md).
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from resource_explorer import doc_source_egeria as m

class _FakeClients:
    """Stands in for `egeria_clients.EgeriaClients` (Brief I): who acts is not this file's
    subject. `of(cls)` builds the (patched, fake) class the code under test asks for."""

    def of(self, cls, **kw):
        return cls("view1", "https://egeria.example", "u", "")


_EGERIA_KW = dict(clients=_FakeClients())


@pytest.fixture(autouse=True)
def _no_existing_links(monkeypatch):
    """The link step first reads the reference's ExternalReferenceLinks (multi-link: a blind link
    duplicates). These tests are about find-or-create; their asset is never linked yet, so the read
    answers pyegeria's live end-of-list sentence. Duplicate behaviour: test_extref_link_dedupe.py."""
    from unittest.mock import create_autospec

    from pyegeria.omvs.metadata_expert import MetadataExpert

    expert = create_autospec(MetadataExpert, spec_set=True, instance=True)
    expert.get_related_metadata_elements.return_value = "No elements found"
    monkeypatch.setattr(m, "_expert", lambda clients: expert)
    return expert


def test_source_type_label_covers_the_full_vocabulary():
    """Owner gate feedback on 8813 (2026-09-29) added installation_guide and
    user_manual; design added api_reference and release_notes while touching
    the list. Pinned here alongside registry.py's DOC_SOURCE_TYPES so the two
    can't drift apart."""
    assert m._SOURCE_TYPE_LABEL == {
        "data_dictionary": "data dictionary",
        "design_notes": "design notes",
        "runbook": "runbook",
        "wiki": "wiki",
        "installation_guide": "installation guide",
        "user_manual": "user manual",
        "api_reference": "api reference",
        "release_notes": "release notes",
        "other": "documentation",
    }


def test_publish_creates_a_new_reference_and_links_it(monkeypatch):
    fake_client = MagicMock()
    fake_client.create_external_reference.return_value = "ref-guid-1"
    fake_client.link_external_reference.return_value = "link-guid-1"

    monkeypatch.setattr(m, "_client", lambda *a, **kw: fake_client)
    monkeypatch.setattr(m, "_find_ref_guid", lambda client, qn: "")

    source = {"url": "https://docs.example/dict", "label": "Data dict",
              "source_type": "data_dictionary", "entity_slug": "adventureworks"}
    result = m.publish_doc_source(source, "asset-guid-1", **_EGERIA_KW)

    assert result["ok"] is True
    assert result["ref_guid"] == "ref-guid-1"
    assert result["link_guid"] == "link-guid-1"
    fake_client.create_external_reference.assert_called_once()
    body = fake_client.create_external_reference.call_args.kwargs["body"]
    assert body["properties"]["url"] == "https://docs.example/dict"
    assert body["properties"]["qualifiedName"] == "ExternalReference::https://docs.example/dict"
    fake_client.link_external_reference.assert_called_once_with("asset-guid-1", "ref-guid-1")


def test_publish_reuses_an_existing_reference_by_qualified_name(monkeypatch):
    fake_client = MagicMock()
    fake_client.link_external_reference.return_value = "link-guid-2"

    monkeypatch.setattr(m, "_client", lambda *a, **kw: fake_client)
    monkeypatch.setattr(m, "_find_ref_guid", lambda client, qn: "existing-guid")
    monkeypatch.setattr(m, "ref_guid_exists", lambda *a, **kw: True)

    source = {"url": "https://docs.example/dict"}
    result = m.publish_doc_source(source, "asset-guid-1", **_EGERIA_KW)

    assert result["ok"] is True
    assert result["ref_guid"] == "existing-guid"
    fake_client.create_external_reference.assert_not_called()
    fake_client.link_external_reference.assert_called_once_with("asset-guid-1", "existing-guid")


def test_publish_with_no_url_fails_cleanly(monkeypatch):
    result = m.publish_doc_source({"url": ""}, "asset-guid-1", **_EGERIA_KW)
    assert result["ok"] is False
    assert "url" in result["error"].lower()


# ── Adoption-race guard (round 4, 2026-09-29) ────────────────────────────────
# `_find_ref_guid` matches by qualifiedName ALONE — global, not scoped to
# whether the match is currently being deleted by a concurrent
# doc_source_unpublish. `is_ref_unpublishing`/`known_ref_guid` are how
# `egeria_outbox.py`'s `_create_doc_source_publish` (which has a registry to
# ask) tells `publish_doc_source` "this candidate GUID is unsafe to reuse".

def test_publish_does_not_reuse_a_found_ref_that_is_being_unpublished(monkeypatch):
    fake_client = MagicMock()
    fake_client.create_external_reference.return_value = "fresh-guid"
    fake_client.link_external_reference.return_value = "fresh-link-guid"

    monkeypatch.setattr(m, "_client", lambda *a, **kw: fake_client)
    # _find_ref_guid finds the OLD reference — still visible to Egeria
    # because its concurrent unpublish hasn't completed the delete yet.
    monkeypatch.setattr(m, "_find_ref_guid", lambda client, qn: "being-deleted-guid")

    source = {"url": "https://egeria.ai", "label": "not-adventureworks",
              "source_type": "installation_guide"}
    result = m.publish_doc_source(
        source, "asset-guid-1",
        is_ref_unpublishing=lambda guid: guid == "being-deleted-guid",
        **_EGERIA_KW,
    )

    assert result["ok"] is True
    # Must NOT have adopted the reference being deleted -- a brand new one
    # was created and linked instead.
    assert result["ref_guid"] == "fresh-guid"
    fake_client.create_external_reference.assert_called_once()
    fake_client.link_external_reference.assert_called_once_with("asset-guid-1", "fresh-guid")


def test_publish_reuses_a_found_ref_when_nothing_is_unpublishing_it(monkeypatch):
    # Sanity check the guard is not overzealous: a normal reuse (no pending
    # unpublish for the found guid) is unaffected.
    fake_client = MagicMock()
    fake_client.link_external_reference.return_value = "link-guid-2"

    monkeypatch.setattr(m, "_client", lambda *a, **kw: fake_client)
    monkeypatch.setattr(m, "_find_ref_guid", lambda client, qn: "existing-guid")
    monkeypatch.setattr(m, "ref_guid_exists", lambda *a, **kw: True)

    source = {"url": "https://docs.example/dict"}
    result = m.publish_doc_source(
        source, "asset-guid-1", is_ref_unpublishing=lambda guid: False, **_EGERIA_KW,
    )

    assert result["ok"] is True
    assert result["ref_guid"] == "existing-guid"
    fake_client.create_external_reference.assert_not_called()


def test_publish_with_known_ref_guid_links_it_without_a_lookup(monkeypatch):
    # The self-heal shape (round 4): a local row already carries a ref guid
    # (from a read-back adoption) but no link guid. publish_doc_source must
    # link THAT reference, not search for/create a different one.
    fake_client = MagicMock()
    fake_client.link_external_reference.return_value = "link-guid-3"

    monkeypatch.setattr(m, "_client", lambda *a, **kw: fake_client)
    monkeypatch.setattr(m, "ref_guid_exists", lambda *a, **kw: True)

    def poisoned(client, qn):
        raise AssertionError("_find_ref_guid must not be called when known_ref_guid is given")

    monkeypatch.setattr(m, "_find_ref_guid", poisoned)

    source = {"url": "https://egeria.ai"}
    result = m.publish_doc_source(
        source, "asset-guid-1", known_ref_guid="known-guid-1",
        is_ref_unpublishing=lambda guid: False, **_EGERIA_KW,
    )

    assert result["ok"] is True
    assert result["ref_guid"] == "known-guid-1"
    fake_client.create_external_reference.assert_not_called()
    fake_client.link_external_reference.assert_called_once_with("asset-guid-1", "known-guid-1")


def test_publish_abandons_a_known_ref_guid_that_is_being_unpublished(monkeypatch):
    # The exact incident shape: the row's OWN stored ref_guid turns out to
    # be the one concurrently being deleted (adopted by an earlier
    # read-back before this guard existed, or before this call's own
    # unpublish check). It must be abandoned, not linked to — falling
    # through to the normal find-or-create flow for a fresh, independent
    # reference.
    fake_client = MagicMock()
    fake_client.create_external_reference.return_value = "fresh-guid-2"
    fake_client.link_external_reference.return_value = "fresh-link-2"

    monkeypatch.setattr(m, "_client", lambda *a, **kw: fake_client)
    monkeypatch.setattr(m, "_find_ref_guid", lambda client, qn: "")  # nothing else found either

    source = {"url": "https://egeria.ai"}
    result = m.publish_doc_source(
        source, "asset-guid-1", known_ref_guid="stale-guid",
        is_ref_unpublishing=lambda guid: guid == "stale-guid", **_EGERIA_KW,
    )

    assert result["ok"] is True
    assert result["ref_guid"] == "fresh-guid-2"
    fake_client.create_external_reference.assert_called_once()


# ── Stuck-row / stale-guid guard (round 5, 2026-09-29) ───────────────────────
# Live incident: a self-heal re-queued a publish for a row whose stored ref
# guid had been genuinely DELETED from Egeria by an unrelated unpublish. The
# old code reused the dead guid unconditionally, the link call against a
# nonexistent element failed silently (caught by the best-effort try/except),
# and the outbox row still completed "done" -- a permanent, silent no-op
# loop. `ref_guid_exists` closes this: no ref guid is trusted without asking
# Egeria first.

def _patched_metadata_expert(monkeypatch, behaviors: dict):
    """Same style `test_egeria_recheck.py` uses for the identical client:
    a stand-in for pyegeria's MetadataExpert, keyed by guid -> outcome
    (an exception to raise, a "not found" string sentinel, or nothing for
    a hit). Patched at the real import path `ref_guid_exists` reaches for,
    not at a module attribute of `doc_source_egeria` -- the same reasoning
    `test_egeria_recheck.py`'s own `_patched` helper documents: this checks
    what `ref_guid_exists` actually calls, not a substitute for it."""
    import sys
    from unittest.mock import MagicMock

    class FakeElementClient:
        def __init__(self, *a, **kw):
            pass

        def create_egeria_bearer_token(self, *a, **kw):
            pass

        def get_metadata_element_by_guid(self, guid, *a, **kw):
            outcome = behaviors.get(guid)
            if isinstance(outcome, BaseException):
                raise outcome
            if outcome is not None:
                return outcome
            return {"elementHeader": {}, "guid": guid}

    fake_module = MagicMock()
    fake_module.MetadataExpert = FakeElementClient
    monkeypatch.setitem(sys.modules, "pyegeria.omvs.metadata_expert", fake_module)


def test_ref_guid_exists_is_false_for_an_empty_guid():
    assert m.ref_guid_exists("", **_EGERIA_KW) is False


def test_ref_guid_exists_is_true_when_egeria_still_has_the_element(monkeypatch):
    _patched_metadata_expert(monkeypatch, {})  # every guid resolves
    assert m.ref_guid_exists("still-here", **_EGERIA_KW) is True


def test_ref_guid_exists_reads_the_live_open_metadata_element_shape(monkeypatch):
    """LIVE 2026-10-14: get_metadata_element_by_guid answers an OpenMetadataElement -- a TOP-LEVEL
    `elementGUID`, no `elementHeader` and no `guid`. The fake above answers a shape Egeria never sends,
    which is how every Enrichment doc-source publish came to fail its own existence check ("unexpected
    answer {'headerVersion': 0, 'status': 'ACTIVE', ...}") and retry forever."""
    live = {"headerVersion": 0, "status": "ACTIVE",
            "type": {"typeId": "af536f20-062b-48ef-9c31-1ddd05b04c56", "typeName": "ExternalReference"},
            "elementGUID": "c1d541ca-971a-4768-9f72-b5d1aa8a4afa", "properties": {}}
    _patched_metadata_expert(monkeypatch, {"c1d541ca-971a-4768-9f72-b5d1aa8a4afa": live})
    assert m.ref_guid_exists("c1d541ca-971a-4768-9f72-b5d1aa8a4afa", **_EGERIA_KW) is True


def test_ref_guid_exists_is_false_on_a_confirmed_unknown_guid_error(monkeypatch):
    unknown_guid_error = Exception(
        "OMRS-REPOSITORY-404-002 The entity identified with guid b9925119 "
        "is not known to the open metadata repository")
    _patched_metadata_expert(monkeypatch, {"b9925119": unknown_guid_error})
    assert m.ref_guid_exists("b9925119", **_EGERIA_KW) is False


def test_ref_guid_exists_is_false_on_a_not_found_string_sentinel(monkeypatch):
    # Same "absent shape" test_egeria_recheck.py models: some misses come
    # back as a plain string rather than an exception.
    _patched_metadata_expert(monkeypatch, {"gone-guid": "no elements found"})
    assert m.ref_guid_exists("gone-guid", **_EGERIA_KW) is False


def test_ref_guid_exists_raises_could_not_check_on_a_connection_error(monkeypatch):
    # An unrelated failure (Egeria unreachable) is not proof the element is
    # gone. It used to return False, and the caller then created a fresh
    # ExternalReference beside a perfectly good one. Now it says it could not
    # check (re/lookup-failure-never-creates, AUDIT-PYEGERIA-6.2.2.md).
    _patched_metadata_expert(monkeypatch, {"unreachable-guid": Exception("Connection refused")})
    with pytest.raises(m.DocSourceLookupError, match="could not be made"):
        m.ref_guid_exists("unreachable-guid", **_EGERIA_KW)


def test_publish_with_a_dead_known_ref_guid_creates_a_new_reference_and_links_it(monkeypatch):
    """The required regression test for the live incident (round 5,
    2026-09-29): a row carrying `egeria_external_ref_guid = b9925119...`,
    the exact ExternalReference deleted from Egeria by an unrelated
    unpublish, must NOT be silently accepted as already-published on its
    next self-heal publish attempt. It must create a brand NEW reference and
    a brand NEW link -- proving the fix closes the silent no-op loop rather
    than only detecting it."""
    fake_client = MagicMock()
    fake_client.create_external_reference.return_value = "fresh-ref-guid"
    fake_client.link_external_reference.return_value = "fresh-link-guid"

    monkeypatch.setattr(m, "_client", lambda *a, **kw: fake_client)
    # Once the known (dead) guid is cleared, the code correctly falls back
    # to the normal qualifiedName lookup -- which also finds nothing else,
    # same as a genuinely fresh publish.
    monkeypatch.setattr(m, "_find_ref_guid", lambda client, qn: "")
    # The stored ref guid does NOT resolve in Egeria -- it was deleted.
    monkeypatch.setattr(m, "ref_guid_exists", lambda guid, **kw: guid != "b9925119-dead-guid")

    source = {"url": "https://egeria.ai", "label": "not-adventureworks",
              "source_type": "installation_guide"}
    result = m.publish_doc_source(
        source, "asset-guid-1", known_ref_guid="b9925119-dead-guid",
        is_ref_unpublishing=lambda guid: False,  # not concurrently unpublishing -- just gone
        **_EGERIA_KW,
    )

    assert result["ok"] is True
    # Must NOT have adopted the dead guid -- a brand new reference and link.
    assert result["ref_guid"] == "fresh-ref-guid"
    assert result["link_guid"] == "fresh-link-guid"
    fake_client.create_external_reference.assert_called_once()
    fake_client.link_external_reference.assert_called_once_with("asset-guid-1", "fresh-ref-guid")


def test_publish_found_ref_guid_also_verified_before_reuse(monkeypatch):
    # The same guard applies to a guid `_find_ref_guid` looks up fresh (not
    # only one carried in as `known_ref_guid`) -- the qualifiedName search
    # can also return a since-deleted guid.
    fake_client = MagicMock()
    fake_client.create_external_reference.return_value = "fresh-ref-guid-2"
    fake_client.link_external_reference.return_value = "fresh-link-guid-2"

    monkeypatch.setattr(m, "_client", lambda *a, **kw: fake_client)
    monkeypatch.setattr(m, "_find_ref_guid", lambda client, qn: "found-but-dead-guid")
    monkeypatch.setattr(m, "ref_guid_exists", lambda guid, **kw: False)

    source = {"url": "https://egeria.ai"}
    result = m.publish_doc_source(source, "asset-guid-1", **_EGERIA_KW)

    assert result["ok"] is True
    assert result["ref_guid"] == "fresh-ref-guid-2"
    fake_client.create_external_reference.assert_called_once()


def test_publish_reports_failure_rather_than_raising(monkeypatch):
    def _boom(*a, **kw):
        raise RuntimeError("platform unreachable")

    monkeypatch.setattr(m, "_client", _boom)

    result = m.publish_doc_source({"url": "https://x"}, "asset-guid-1", **_EGERIA_KW)

    assert result["ok"] is False
    assert "platform unreachable" in result["error"]


def test_unpublish_detaches_and_deletes(monkeypatch):
    fake_client = MagicMock()
    monkeypatch.setattr(m, "_client", lambda *a, **kw: fake_client)

    result = m.unpublish_doc_source("ref-guid-1", "asset-guid-1", **_EGERIA_KW)

    assert result["ok"] is True
    fake_client.detach_external_reference.assert_called_once_with("asset-guid-1", "ref-guid-1")
    fake_client.delete_external_reference.assert_called_once_with("ref-guid-1")


def test_unpublish_with_no_ref_guid_is_a_no_op(monkeypatch):
    fake_client = MagicMock()
    monkeypatch.setattr(m, "_client", lambda *a, **kw: fake_client)

    result = m.unpublish_doc_source("", "asset-guid-1", **_EGERIA_KW)

    assert result["ok"] is True
    fake_client.detach_external_reference.assert_not_called()


def test_unpublish_tolerates_a_failed_detach_and_still_deletes(monkeypatch):
    fake_client = MagicMock()
    fake_client.detach_external_reference.side_effect = RuntimeError("relationship already gone")
    monkeypatch.setattr(m, "_client", lambda *a, **kw: fake_client)

    result = m.unpublish_doc_source("ref-guid-1", "asset-guid-1", **_EGERIA_KW)

    assert result["ok"] is True
    fake_client.delete_external_reference.assert_called_once_with("ref-guid-1")


def test_unpublish_removes_only_the_one_reference():
    """The brief's removal rule: 'removal deletes... the ExternalReference
    it created (and only that one)'. Nothing in unpublish_doc_source's
    signature accepts more than one GUID, which is the enforcement — this
    test pins that the call it makes names exactly the one GUID passed."""
    import inspect
    sig = inspect.signature(m.unpublish_doc_source)
    assert "ref_guid" in sig.parameters
    assert "ref_guids" not in sig.parameters  # no plural/batch form exists


def test_read_back_returns_empty_list_when_egeria_unreachable(monkeypatch):
    def _boom(*a, **kw):
        raise RuntimeError("no route to host")

    fake_module = MagicMock()
    fake_module.MetadataExpert.side_effect = _boom
    import sys
    monkeypatch.setitem(sys.modules, "pyegeria.omvs.metadata_expert", fake_module)

    out = m.read_back_doc_sources("asset-guid-1", **_EGERIA_KW)
    assert out == []


def test_read_back_returns_empty_list_with_no_asset_guid():
    # No network attempted at all when there's no asset to ask about.
    assert m.read_back_doc_sources("", **_EGERIA_KW) == []


def test_read_back_extracts_url_and_label(monkeypatch):
    # Shape confirmed live against a real Egeria platform (this slice's
    # gate check, 2026-09-28): `get_related_metadata_elements` returns
    # `{"elementList": [{"element": {"elementGUID": ..., "elementProperties":
    # {"propertiesAsStrings": {...}}}}]}`, not a bare list of elements — an
    # earlier version of read_back_doc_sources assumed the latter and
    # silently returned zero references against the real platform (see
    # doc_source_egeria.py's own comment on this fix).
    fake_element_client = MagicMock()
    fake_element_client.get_related_metadata_elements.return_value = {
        "elementList": [
            {
                "element": {
                    "elementGUID": "ref-guid-9",
                    "elementProperties": {
                        "propertiesAsStrings": {"url": "https://docs.example/x", "displayName": "X docs"},
                    },
                },
            },
            # An element with no URL must be skipped, not crash the read-back.
            {"element": {"elementGUID": "ref-guid-10", "elementProperties": {"propertiesAsStrings": {}}}},
        ],
    }

    fake_module = MagicMock()
    fake_module.MetadataExpert.return_value = fake_element_client
    import sys
    monkeypatch.setitem(sys.modules, "pyegeria.omvs.metadata_expert", fake_module)

    out = m.read_back_doc_sources("asset-guid-1", **_EGERIA_KW)

    assert out == [{"ref_guid": "ref-guid-9", "url": "https://docs.example/x", "label": "X docs"}]
