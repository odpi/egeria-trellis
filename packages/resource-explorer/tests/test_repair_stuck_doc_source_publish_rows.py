"""The one-time repair for stuck `doc_source_publish` outbox rows — round 6
(2026-09-29). `find_stuck_rows` is the script's own audit query; pinned
directly rather than only exercised via its CLI, per this codebase's
convention for the other one-time sweeps in `scripts/`.
"""
from __future__ import annotations

import sys
from pathlib import Path

from resource_explorer.egeria_outbox import enqueue_doc_source_publish
from resource_explorer.registry import DatabaseEntity, ProjectRegistry

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from repair_stuck_doc_source_publish_rows import find_stuck_rows  # noqa


def _registry(tmp_path) -> ProjectRegistry:
    r = ProjectRegistry(db_path=str(tmp_path / "repair.db"))
    r.register_database(DatabaseEntity(
        slug="adventureworks", display_name="AdventureWorks", db_type="postgresql",
        host="localhost", port=5432, database_name="adventureworks",
        egeria_url="https://egeria.example", egeria_server="view1",
        egeria_user="u", egeria_password="p", egeria_asset_guid="asset-1",
    ))
    return r


def test_finds_a_done_row_whose_doc_sources_row_has_no_link_guid(tmp_path):
    reg = _registry(tmp_path)
    row = reg.add_doc_source("database", "adventureworks", "https://egeria.ai")
    reg.set_doc_source_egeria_ref("database", "adventureworks", row["id"], "dead-guid-b99")
    element_id = enqueue_doc_source_publish(reg, "database", "adventureworks", row["id"], row["url"])
    reg.mark_outbox_done(element_id, "dead-guid-b99")

    stuck = find_stuck_rows(reg)

    assert len(stuck) == 1
    assert stuck[0]["outbox_id"] == element_id
    assert stuck[0]["source_id"] == row["id"]
    assert stuck[0]["ref_guid"] == "dead-guid-b99"
    assert stuck[0]["link_guid"] == ""


def test_does_not_flag_a_row_that_is_genuinely_linked(tmp_path):
    reg = _registry(tmp_path)
    row = reg.add_doc_source("database", "adventureworks", "https://docs.example/fine")
    reg.set_doc_source_egeria_ref("database", "adventureworks", row["id"], "ref-1", "link-1")
    element_id = enqueue_doc_source_publish(reg, "database", "adventureworks", row["id"], row["url"])
    reg.mark_outbox_done(element_id, "ref-1")

    assert find_stuck_rows(reg) == []


def test_does_not_flag_a_row_still_pending(tmp_path):
    reg = _registry(tmp_path)
    row = reg.add_doc_source("database", "adventureworks", "https://docs.example/pending")
    enqueue_doc_source_publish(reg, "database", "adventureworks", row["id"], row["url"])

    assert find_stuck_rows(reg) == []


def test_skips_a_row_whose_local_source_was_since_removed(tmp_path):
    reg = _registry(tmp_path)
    row = reg.add_doc_source("database", "adventureworks", "https://docs.example/gone")
    element_id = enqueue_doc_source_publish(reg, "database", "adventureworks", row["id"], row["url"])
    reg.mark_outbox_done(element_id, "some-guid")
    reg.remove_doc_source("database", "adventureworks", row["id"])

    assert find_stuck_rows(reg) == []


def test_repair_reopens_the_same_row_not_a_duplicate(tmp_path):
    reg = _registry(tmp_path)
    row = reg.add_doc_source("database", "adventureworks", "https://egeria.ai")
    reg.set_doc_source_egeria_ref("database", "adventureworks", row["id"], "dead-guid-b99")
    element_id = enqueue_doc_source_publish(reg, "database", "adventureworks", row["id"], row["url"])
    reg.mark_outbox_done(element_id, "dead-guid-b99")

    stuck = find_stuck_rows(reg)
    for r in stuck:
        reg.reopen_outbox_row(r["outbox_id"], "test repair")

    rows = reg.list_outbox_elements(entity_slug="adventureworks")
    publish_rows = [r for r in rows if r["element_kind"] == "doc_source_publish"]
    assert len(publish_rows) == 1
    assert publish_rows[0]["id"] == element_id
    assert publish_rows[0]["status"] == "pending"
    assert publish_rows[0]["egeria_guid"] == ""
    assert find_stuck_rows(reg) == [], "reopened rows are no longer 'done' so no longer flagged"
