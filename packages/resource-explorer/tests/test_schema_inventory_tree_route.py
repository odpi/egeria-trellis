"""GET /api/databases/{slug}/schema-inventory-tree — Slice 22's route for
the /next Schema Inventory view."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from resource_explorer.registry import DatabaseEntity, ProjectRegistry


@pytest.fixture
def registry(tmp_path):
    r = ProjectRegistry(db_path=str(tmp_path / "test.db"))
    r.register_database(DatabaseEntity(
        slug="db", display_name="db", db_type="postgresql",
        host="localhost", port=5442, database_name="db",
    ))
    return r


@pytest.fixture
def client(registry, monkeypatch):
    monkeypatch.setattr(
        "resource_explorer.registry.ProjectRegistry.__init__",
        lambda self, db_path=None: setattr(self, "__dict__", registry.__dict__) or None,
    )
    from resource_explorer.web.app import app
    return TestClient(app)


class TestSchemaInventoryTreeRoute:
    def test_unknown_database_returns_404(self, client):
        resp = client.get("/api/databases/not-a-real-db/schema-inventory-tree")
        assert resp.status_code == 404

    def test_no_stored_rows_yet_returns_an_empty_tree_not_a_404(self, client):
        resp = client.get("/api/databases/db/schema-inventory-tree")
        assert resp.status_code == 200
        # A2.1: the route also names the surveys it read (none stored here).
        assert resp.json()["schemas"] == []
        assert resp.json()["sources"]["chosen"]["schemas"] == 0

    def test_a_real_schema_appears_with_its_tables(self, registry, client):
        registry.write_detail_rows("database_tables", "db", "2026-09-27T00:00:00", rows=[
            {"schema_name": "public", "table_name": "orders", "table_type": "BASE TABLE",
             "row_count": 5, "size_bytes": 100, "state": "measured"},
        ])
        registry.write_detail_rows("database_columns", "db", "2026-09-27T00:00:00", rows=[
            {"schema_name": "public", "table_name": "orders", "column_name": "id",
             "ordinal_position": 1, "data_type": "integer", "base_type": "integer",
             "is_nullable": 0, "is_primary_key": 1, "foreign_key_json": None,
             "description": "", "state": "measured"},
        ])
        resp = client.get("/api/databases/db/schema-inventory-tree")
        assert resp.status_code == 200
        data = resp.json()
        schema = next(s for s in data["schemas"] if s["schema"] == "public")
        assert schema["tables"][0]["name"] == "orders"
        assert schema["tables"][0]["columns"][0]["key_role"] == "PK"
