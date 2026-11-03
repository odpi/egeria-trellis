"""The Curate header after a restore: words derive from the newest proof rows that prove them."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from resource_explorer import catalogue_commit as cc  # noqa: E402
from resource_explorer.registry import DatabaseEntity, ProjectRegistry  # noqa: E402

OLD, NEW = "def55997-0000-0000-0000-000000000001", "17f0a963-0000-0000-0000-000000000002"


@pytest.fixture
def reg(tmp_path):
    r = ProjectRegistry(db_path=str(tmp_path / "h.db"))
    r.register_database(DatabaseEntity(slug="db", display_name="D", db_type="postgresql", host="localhost",
                                       egeria_host="h", port=5442, database_name="d"))
    return r


def add(reg, proof, at, guid="", node="database", schema="", detail=None):
    return reg.append_catalogue_commit_proof("db", proof=proof, node_kind=node, schema_name=schema,
                                             element_guid=guid, detail=detail or {}, read_at=f"2026-10-06T{at}:00")


def header(reg, schemas=()):
    view = {"schemas": [{"name": n, "effective": "catalogue", "tables": []} for n in schemas]}
    return cc.derive_commit_state(reg, "db", view)


def history(reg):
    add(reg, "database_published", "10:00", OLD)
    add(reg, "read_failed", "11:00", detail={"error": "SERVER_ERROR_500"})
    return add(reg, "restored", "12:00", NEW, detail={"from_guid": OLD})


def test_restored_element_is_named_and_healed_failure_is_gone(reg):
    history(reg)
    add(reg, "zones_read", "13:00", NEW, detail={"zones": []})
    t = header(reg)["header"]["text"]
    assert "Database element 17f0a963 in Egeria · restored 10-06 12:00 (from def55997)" in t
    assert "def55997 in Egeria" not in t and "failed" not in t
    assert header(reg)["database"]["guid"] == NEW


def test_a_failed_read_newer_than_every_success_still_shows(reg):
    history(reg)
    add(reg, "zones_read", "13:00", NEW, detail={"zones": []})
    add(reg, "read_failed", "14:00", detail={"error": "SERVER_ERROR_500"})
    t = header(reg)["header"]["text"]
    assert "last read of Egeria failed 10-06 14:00: SERVER_ERROR_500" in t


def test_a_restore_alone_heals_an_older_failure(reg):
    history(reg)
    assert "failed" not in header(reg)["header"]["text"]


def test_never_restored_database_reads_as_before(reg):
    add(reg, "database_published", "10:00", OLD)
    add(reg, "read_failed", "11:00", detail={"error": "boom"})
    t = header(reg)["header"]["text"]
    assert "Database element def55997 in Egeria · published 10-06 10:00" in t
    assert "last read of Egeria failed 10-06 11:00: boom" in t and "restored" not in t


def test_schema_archived_then_restored_reads_restored(reg):
    add(reg, "archived", "10:00", OLD, node="schema", schema="us_sales")
    add(reg, "restored", "11:00", NEW, node="schema", schema="us_sales", detail={"from_guid": OLD})
    assert header(reg, ["us_sales"])["schemas"]["us_sales"]["state"] == "restored"


def test_deleting_the_restored_row_brings_the_old_words_back(reg):
    rid = history(reg)
    with reg._conn() as c:
        c.execute("DELETE FROM catalogue_commit_proofs WHERE id = ?", (rid,))
    t = header(reg)["header"]["text"]
    assert "Database element def55997 in Egeria · published" in t and "last read of Egeria failed" in t
