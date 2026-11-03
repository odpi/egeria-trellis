"""The catalogue read-back is three count chips in a fixed order, from proof rows (architect ruling 2026-10-23).

"published by RE N" (the database_published row), "attached N" (target rows), "cataloged by Egeria N" (the
elements read back under each schema): ◌ while the cataloguer has not built the tree, ✓ once it has, and
"not checked since <read_at>" on a pending count whose newest read-back row is older than 120 s. The word
"waiting" is gone. Temp SQLite; no Egeria.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from resource_explorer import catalogue_commit as cc
from resource_explorer.registry import DatabaseEntity, ProjectRegistry

VIEW = {"schemas": [{"name": "sales", "effective": "catalogue", "tables": []},
                    {"name": "hr", "effective": "catalogue", "tables": []},
                    {"name": "tmp", "effective": "leave_out", "tables": []}]}
NOW = datetime(2026, 10, 23, 9, 1, 0, tzinfo=timezone.utc)


@pytest.fixture
def reg(tmp_path):
    r = ProjectRegistry(db_path=str(tmp_path / "t.db"))
    r.register_database(DatabaseEntity(slug="aw", display_name="AW", db_type="postgresql", host="localhost",
                                       port=5432, database_name="aw"))
    r.append_catalogue_commit_proof("aw", proof=cc.P_DATABASE, node_kind="database", element_guid="db-guid-1234",
                                    detail={"server_guid": "srv-guid-1234"}, read_at="2026-10-23T09:00:00")
    for s in ("sales", "hr"):
        r.append_catalogue_commit_proof("aw", proof=cc.P_TARGET, schema_name=s, element_guid=f"{s}-guid",
                                        target_guid=f"{s}-target", read_at="2026-10-23T09:00:30")
    return r


def _chips(d):
    return {c["key"]: c for c in d.get("read_back", d)["chips"]}


def test_attached_not_catalogued_reads_pending_never_waiting(reg):
    d = cc.derive_commit_state(reg, "aw", VIEW, NOW)
    assert [c["key"] for c in d["read_back"]["chips"]] == ["published", "attached", "catalogued"]
    c = _chips(d)
    assert (c["published"]["word"], c["published"]["count"], c["published"]["cue"]) == ("published by RE", 2, "read_back")
    assert (c["attached"]["count"], c["attached"]["cue"]) == (2, "read_back")
    assert (c["catalogued"]["word"], c["catalogued"]["count"], c["catalogued"]["cue"]) == ("cataloged by Egeria", 0, "pending")
    assert "hr" in c["catalogued"]["hover"] and "sales" in c["catalogued"]["hover"]
    words = cc.read_back_words(d["read_back"])
    assert words == "published by RE 2 ✓ · attached 2 schemas ✓ · cataloged by Egeria 0 schemas ◌"
    assert (c["published"]["unit"], c["attached"]["unit"], c["catalogued"]["unit"]) == ("", "schemas", "schemas")
    assert "waiting" not in words


def test_counts_come_from_the_rows_the_tree_built_is_a_check(reg):
    for s, tables in (("sales", ["orders", "lines"]), ("hr", ["people"])):
        reg.append_catalogue_commit_proof("aw", proof=cc.P_ELEMENTS, schema_name=s, element_guid=f"{s}-guid",
                                          target_guid=f"{s}-target", detail={"tables": tables, "columns": 9},
                                          read_at="2026-10-23T09:00:50")
    c = _chips(cc.derive_commit_state(reg, "aw", VIEW))
    assert (c["attached"]["count"], c["catalogued"]["count"], c["catalogued"]["cue"]) == (2, 2, "read_back")
    assert c["catalogued"]["not_checked_since"] == ""


def test_one_of_two_built_is_still_pending(reg):
    reg.append_catalogue_commit_proof("aw", proof=cc.P_ELEMENTS, schema_name="sales", element_guid="sales-guid",
                                      detail={"tables": ["orders"]}, read_at="2026-10-23T09:00:50")
    d = cc.derive_commit_state(reg, "aw", VIEW, NOW)
    c = _chips(d)
    assert (c["catalogued"]["count"], c["catalogued"]["cue"]) == (1, "pending")
    assert c["catalogued"]["unit"] == "schema"                          # singular for 1
    assert cc.read_back_words(d["read_back"]) == "published by RE 2 ✓ · attached 2 schemas ✓ · cataloged by Egeria 1 schema ◌"


def test_a_stale_read_says_not_checked_since(reg):
    d = cc.read_back_chips_for(reg, "aw", ["sales", "hr"], now=NOW)
    assert _chips(d)["catalogued"]["not_checked_since"] == ""          # 30 s old: fresh
    later = datetime(2026, 10, 23, 9, 5, 0, tzinfo=timezone.utc)
    stale = _chips(cc.read_back_chips_for(reg, "aw", ["sales", "hr"], now=later))["catalogued"]
    assert stale["cue"] == "pending" and stale["not_checked_since"] == "2026-10-23T09:00:30"
    # F37: the step's words carry no number beside "not checked", and one cue.
    words = cc.read_back_words(cc.read_back_chips_for(reg, "aw", ["sales", "hr"], now=later))
    assert words.endswith("cataloged by Egeria ◌ not checked since 10-23 09:00") and words.count("◌") == 1


def test_nothing_published_or_attached_has_no_counts(tmp_path):
    """No publish/restore row and no schema proof row: no count chips at all, never three measured zeros."""
    r = ProjectRegistry(db_path=str(tmp_path / "e.db"))
    d = cc.derive_commit_state(r, "none", VIEW)
    assert d["read_back"] == {"chips": []}
    assert cc.read_back_chips_for(r, "none", ["sales", "hr"]) == {"chips": []}
    assert cc.read_back_words(d["read_back"]) == "nothing read back from Egeria"
    assert "not read back" in d["header"]["text"] or d["header"]["state"] == "not_committed"


def test_the_step_text_and_the_chips_are_one_derivation_through_a_reset(reg):
    """Rows read before an Egeria reset are history: the step's chips drop them exactly as the screen's do."""
    reg.append_catalogue_commit_proof("aw", proof=cc.P_EGERIA_RESET, node_kind="database",
                                      detail={"text": "Egeria reset"}, read_at="2026-10-23T09:00:40")
    screen = cc.derive_commit_state(reg, "aw", VIEW, NOW)["read_back"]
    step = cc.read_back_chips_for(reg, "aw", ["sales", "hr"], now=NOW)
    # every proof predates the reset: nothing is counted, and what they once proved is ◌, never ✓ (ruling (c),
    # 2026-10-23: "cataloged by Egeria 0 ◌ until the cataloguer rebuilds")
    assert step == screen
    # (this fixture's schemas were attached, never catalogued, before the reset: that chip has nothing to wait for)
    assert [(c["count"], c["cue"]) for c in step["chips"]] == [(0, "pending"), (0, "pending"), (0, "none")]
    reg.append_catalogue_commit_proof("aw", proof=cc.P_TARGET, schema_name="sales", element_guid="sales-guid-2",
                                      target_guid="sales-target-2", read_at="2026-10-23T09:00:45")
    screen = cc.derive_commit_state(reg, "aw", VIEW, NOW)["read_back"]
    step = cc.read_back_chips_for(reg, "aw", ["sales", "hr"], now=NOW)
    assert step == screen
    c = _chips(step)
    assert (c["published"]["count"], c["attached"]["count"]) == (0, 1)
