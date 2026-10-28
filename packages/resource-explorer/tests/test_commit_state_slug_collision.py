"""A repository and a database can share one slug; proof rows are keyed by slug alone.

Backlog: `derive_commit_state` slug collision. A repository's blueprint-shape, report, architecture-publish
and sub-resource rows must not make a same-slug database read "committed", and a database's own rows must
still count. Temp SQLite; no Egeria.
"""
from __future__ import annotations

import pytest

from resource_explorer import catalogue_commit as cc
from resource_explorer.registry import DatabaseEntity, ProjectRegistry

VIEW = {"schemas": [{"name": "s1", "effective": "catalogue", "tables": []}]}


@pytest.fixture
def reg(tmp_path):
    r = ProjectRegistry(db_path=str(tmp_path / "t.db"))
    r.register_database(DatabaseEntity(slug="shared", display_name="S", db_type="postgresql", host="localhost",
                                       port=5432, database_name="s"))
    return r


REPO_ROWS = [("shape", "blueprint_shape"), ("rekey", "blueprint_shape"), ("report_published", "repo_report"),
             ("publish_item", "architecture_publish"), ("sub_resource", "sub_resource")]


def test_a_same_slug_repository_does_not_make_the_database_look_committed(reg):
    for proof, kind in REPO_ROWS:
        reg.append_catalogue_commit_proof("shared", proof=proof, node_kind=kind, table_name="x::a",
                                          element_guid="g-repo", read_at="2026-10-06T10:00:00")
    d = cc.derive_commit_state(reg, "shared", VIEW)
    assert d["header"]["state"] == "not_committed"
    assert d["schemas"]["s1"]["state"] in ("none", "uncommitted")
    assert d["database"] is None


def test_the_databases_own_rows_still_count_beside_a_repositorys(reg):
    reg.append_catalogue_commit_proof("shared", proof="shape", node_kind="blueprint_shape", table_name="x::a",
                                      element_guid="g-repo", read_at="2026-10-06T10:00:00")
    reg.append_catalogue_commit_proof("shared", proof="database_published", node_kind="database",
                                      element_guid="g-db-12345678", read_at="2026-10-06T11:00:00")
    d = cc.derive_commit_state(reg, "shared", VIEW)
    assert d["header"]["state"] == "committed"
    assert d["database"]["guid"] == "g-db-12345678"
    assert "g-repo" not in d["header"]["text"]
