"""Header words and band words for a database read the same source (audit 2026-10-28, defects 1 and 2)."""
from __future__ import annotations

import pytest

from resource_explorer import catalogue_commit as cc
from resource_explorer.registry import DatabaseEntity, ProjectRegistry

GUID = "c990f349-0000-0000-0000-000000000001"


@pytest.fixture
def reg(tmp_path):
    r = ProjectRegistry(db_path=str(tmp_path / "h.db"))
    r.register_database(DatabaseEntity(slug="db", display_name="D", db_type="postgresql", host="localhost",
                                       egeria_host="h", port=5442, database_name="d"))
    return r


def _proof(reg, proof, at, guid="", node="database", schema=""):
    return reg.append_catalogue_commit_proof("db", proof=proof, node_kind=node, schema_name=schema,
                                             element_guid=guid, detail={}, read_at=f"2026-10-24T{at}:00")


def test_published_element_is_found_from_proof_rows_when_the_cached_guid_is_empty(reg):
    assert cc.published_element_of(reg, "db") is None
    _proof(reg, "database_published", "00:45", GUID)
    got = cc.published_element_of(reg, "db")
    assert got and got["guid"] == GUID


def test_a_band_publish_and_the_helper_agree(reg):
    _proof(reg, "database_published", "00:45", GUID)
    band = cc.derive_commit_state(reg, "db", {"schemas": []})
    assert band["database"]["guid"] == cc.published_element_of(reg, "db")["guid"]


def test_summary_row_says_published_and_surveyed_where_the_band_does(reg, monkeypatch):
    from resource_explorer.web.routes import databases as routes
    _proof(reg, "database_published", "00:45", GUID)
    monkeypatch.setattr("resource_explorer.registry.ProjectRegistry", lambda *a, **k: reg)
    db = reg.get_database("db")
    assert not db.egeria_asset_guid and not db.last_surveyed_at
    monkeypatch.setattr(reg, "get_latest_database_survey",
                        lambda slug: {"surveyed_at": "2026-10-24T00:54:00", "schema_count": 4}, raising=False)
    row = routes._to_summary(db)
    assert row.is_published is True
    assert row.last_surveyed_at == "2026-10-24T00:54:00"


def test_header_has_no_n_in_egeria_sentence(reg):
    _proof(reg, "database_published", "00:45", GUID)
    _proof(reg, "egeria_reset", "00:10", node="database")
    view = {"schemas": [{"name": "s", "effective": "catalogue", "tables": []}]}
    text = cc.derive_commit_state(reg, "db", view)["header"]["text"]
    assert " in Egeria" in text            # the element sentence stays
    assert "schemas attached" in text or "no schemas attached" in text
    assert "0 in Egeria" not in text and not any(p.strip().endswith(" in Egeria") and p.strip()[0].isdigit()
                                                  for p in text.split(" · "))
