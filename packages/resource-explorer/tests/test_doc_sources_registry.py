"""Registry read/write for `doc_sources` — BRIEF-DATABASE-DOCUMENTATION-
SOURCES.md slice 1. Covers declare, probe-result write, removal, and the
Egeria read-back upsert (`upsert_doc_source_from_egeria`), independent of
the route layer and of any real Egeria/network call.
"""
from __future__ import annotations

import pytest

from resource_explorer.registry import ProjectRegistry


@pytest.fixture
def registry(tmp_path):
    return ProjectRegistry(db_path=str(tmp_path / "t.db"))


def test_add_and_list_roundtrip(registry):
    row = registry.add_doc_source("database", "adventureworks",
                                   "https://example.com/dict", label="Data dict",
                                   source_type="data_dictionary", added_by="dan")

    assert row["url"] == "https://example.com/dict"
    assert row["label"] == "Data dict"
    assert row["source_type"] == "data_dictionary"
    assert row["origin"] == "local"
    assert row["probe_state"] in ("", None)

    listed = registry.list_doc_sources("database", "adventureworks")
    assert len(listed) == 1
    assert listed[0]["id"] == row["id"]


def test_invalid_source_type_falls_back_to_other(registry):
    row = registry.add_doc_source("database", "s", "https://x", source_type="nonsense")
    assert row["source_type"] == "other"


def test_doc_source_types_vocabulary():
    """Owner gate feedback on 8813 (2026-09-29) added installation_guide and
    user_manual; design added api_reference and release_notes while touching
    the list. Pinned here so the vocabulary can't silently drift."""
    assert ProjectRegistry.DOC_SOURCE_TYPES == (
        "data_dictionary", "design_notes", "runbook", "wiki",
        "installation_guide", "user_manual", "api_reference", "release_notes",
        "other",
    )


@pytest.mark.parametrize("source_type", [
    "data_dictionary", "design_notes", "runbook", "wiki",
    "installation_guide", "user_manual", "api_reference", "release_notes",
    "other",
])
def test_each_declared_source_type_round_trips(registry, source_type):
    row = registry.add_doc_source("database", "s", f"https://x/{source_type}",
                                   source_type=source_type)
    assert row["source_type"] == source_type


def test_scoped_by_entity_type_and_slug(registry):
    registry.add_doc_source("database", "a", "https://a.example/1")
    registry.add_doc_source("database", "b", "https://b.example/1")
    registry.add_doc_source("filesystem", "a", "https://a.example/fs")

    assert len(registry.list_doc_sources("database", "a")) == 1
    assert len(registry.list_doc_sources("database", "b")) == 1
    assert len(registry.list_doc_sources("filesystem", "a")) == 1


def test_record_probe_result(registry):
    row = registry.add_doc_source("database", "s", "https://x")
    updated = registry.record_doc_source_probe(
        "database", "s", row["id"], state="reachable", status_code=200,
        elapsed_ms=123, title="Some Page", byte_count=456,
    )
    assert updated["probe_state"] == "reachable"
    assert updated["probe_status_code"] == 200
    assert updated["probe_ms"] == 123
    assert updated["probe_title"] == "Some Page"
    assert updated["probe_byte_count"] == 456
    assert updated["probed_at"]


def test_remove_returns_the_row_it_deleted(registry):
    row = registry.add_doc_source("database", "s", "https://x")
    registry.set_doc_source_egeria_ref("database", "s", row["id"], "guid-123")

    removed = registry.remove_doc_source("database", "s", row["id"])

    assert removed["egeria_external_ref_guid"] == "guid-123"
    assert registry.list_doc_sources("database", "s") == []
    # Removing again finds nothing rather than erroring.
    assert registry.remove_doc_source("database", "s", row["id"]) is None


def test_upsert_from_egeria_creates_a_new_row_for_an_unknown_reference(registry):
    row = registry.upsert_doc_source_from_egeria(
        "database", "s", url="https://wiki.example/dict", ref_guid="guid-999",
        label="Wiki dict", source_type="wiki",
    )
    assert row["origin"] == "egeria"
    assert row["egeria_external_ref_guid"] == "guid-999"
    assert len(registry.list_doc_sources("database", "s")) == 1


def test_upsert_from_egeria_matches_existing_local_row_by_url(registry):
    """A source a person declared locally, then published — Egeria's own
    read-back of it must fold onto the same row rather than duplicating."""
    local = registry.add_doc_source("database", "s", "https://x/dict", label="Dict")

    upserted = registry.upsert_doc_source_from_egeria(
        "database", "s", url="https://x/dict", ref_guid="guid-abc",
    )

    assert upserted["id"] == local["id"]
    assert upserted["egeria_external_ref_guid"] == "guid-abc"
    assert len(registry.list_doc_sources("database", "s")) == 1


def test_upsert_from_egeria_matches_by_guid_even_if_url_changed(registry):
    local = registry.add_doc_source("database", "s", "https://old-url/dict")
    registry.set_doc_source_egeria_ref("database", "s", local["id"], "guid-fixed")

    upserted = registry.upsert_doc_source_from_egeria(
        "database", "s", url="https://new-url/dict", ref_guid="guid-fixed",
    )

    assert upserted["id"] == local["id"]
    assert len(registry.list_doc_sources("database", "s")) == 1


def test_repeat_read_back_does_not_duplicate(registry):
    registry.upsert_doc_source_from_egeria("database", "s", url="https://x", ref_guid="g1")
    registry.upsert_doc_source_from_egeria("database", "s", url="https://x", ref_guid="g1")
    registry.upsert_doc_source_from_egeria("database", "s", url="https://x", ref_guid="g1")

    assert len(registry.list_doc_sources("database", "s")) == 1


# ── Adoption-race guard: has_pending_unpublish_for_ref (round 4, 2026-09-29) ─
# The incident this closes: a source removed at 15:21:41 enqueued a
# doc_source_unpublish for its ExternalReference; a DIFFERENT source at the
# same URL, declared a second later, adopted that SAME reference — while it
# was still being deleted — via the read-back `url` fallback. This method is
# the guard `_sync_egeria_read_back`/`publish_doc_source` check before
# adopting or reusing any ExternalReference found by URL/qualifiedName.

def test_has_pending_unpublish_for_ref_false_with_no_outbox_rows(registry):
    assert registry.has_pending_unpublish_for_ref("database", "s", "ref-1") is False


def test_has_pending_unpublish_for_ref_false_for_empty_guid(registry):
    assert registry.has_pending_unpublish_for_ref("database", "s", "") is False


def test_has_pending_unpublish_for_ref_true_when_pending(registry):
    from resource_explorer.egeria_outbox import enqueue_doc_source_unpublish

    enqueue_doc_source_unpublish(registry, "database", "s", "ref-1")

    assert registry.has_pending_unpublish_for_ref("database", "s", "ref-1") is True


def test_has_pending_unpublish_for_ref_true_when_running(registry):
    from resource_explorer.egeria_outbox import enqueue_doc_source_unpublish

    element_id = enqueue_doc_source_unpublish(registry, "database", "s", "ref-1")
    registry.claim_due_outbox_elements(element_id=element_id)  # pending -> running

    assert registry.has_pending_unpublish_for_ref("database", "s", "ref-1") is True


def test_has_pending_unpublish_for_ref_false_once_done(registry):
    from resource_explorer.egeria_outbox import enqueue_doc_source_unpublish

    element_id = enqueue_doc_source_unpublish(registry, "database", "s", "ref-1")
    registry.mark_outbox_done(element_id, "ref-1")

    assert registry.has_pending_unpublish_for_ref("database", "s", "ref-1") is False


def test_has_pending_unpublish_for_ref_scoped_to_the_right_guid_and_entity(registry):
    from resource_explorer.egeria_outbox import enqueue_doc_source_unpublish

    enqueue_doc_source_unpublish(registry, "database", "s", "ref-OTHER")
    enqueue_doc_source_unpublish(registry, "database", "other-slug", "ref-1")

    # Neither an unpublish for a different ref, nor one for a different
    # entity, should register as "pending for ref-1 on database/s".
    assert registry.has_pending_unpublish_for_ref("database", "s", "ref-1") is False
