"""`schema_inventory_tree()` — Slice 22's Schemas → Tables → Columns tree
for the /next Schema Inventory view.

Built entirely from `_schema_inventory_container_rows()`'s own per-schema
classification plus the structured `database_tables`/`database_columns`
detail rows — never the classic UI's `survey_data` blob path.
"""
from __future__ import annotations

from resource_explorer.registry import ProjectRegistry, DatabaseEntity, STATE_CATALOG_ESTIMATE
from resource_explorer.surveyors.database.survey_definition_adapter import schema_inventory_tree


import pytest


@pytest.fixture
def registry(tmp_path):
    r = ProjectRegistry(db_path=str(tmp_path / "test.db"))
    r.register_database(DatabaseEntity(
        slug="db", display_name="db", db_type="postgresql",
        host="localhost", port=5442, database_name="db",
    ))
    return r


def _table(schema, name, row_count=None, size_bytes=None, state="measured", column_count=None):
    return {"schema_name": schema, "table_name": name, "table_type": "BASE TABLE",
            "row_count": row_count, "size_bytes": size_bytes, "state": state,
            "column_count": column_count}


def _column(schema, table, name, position, pk=False, fk=None, nullable=True,
            data_type="integer", comment=""):
    return {"schema_name": schema, "table_name": table, "column_name": name,
            "ordinal_position": position, "data_type": data_type, "base_type": data_type,
            "is_nullable": 1 if nullable else 0, "is_primary_key": 1 if pk else 0,
            "foreign_key_json": fk, "description": comment, "state": "measured"}


class TestReturnsNoneWhenNothingToSay:
    def test_no_tables_at_all(self, registry):
        assert schema_inventory_tree(registry, "db") is None


class TestTreeShape:
    def test_a_table_with_columns_carries_pk_fk_and_comment(self, registry):
        registry.write_detail_rows("database_tables", "db", "2026-09-27T00:00:00",
            rows=[_table("public", "orders", row_count=10, size_bytes=1024)])
        registry.write_detail_rows("database_columns", "db", "2026-09-27T00:00:00",
            rows=[
                _column("public", "orders", "id", 1, pk=True, nullable=False),
                _column("public", "orders", "customer_id", 2,
                        fk={"foreign_schema": "public", "foreign_table": "customer",
                            "foreign_column": "id"}),
                _column("public", "orders", "note", 3, comment="free text"),
            ])
        tree = schema_inventory_tree(registry, "db")
        assert tree is not None
        schema = next(s for s in tree["schemas"] if s["schema"] == "public")
        table = schema["tables"][0]
        assert table["name"] == "orders"
        assert table["row_count"] == 10
        assert table["size_bytes"] == 1024
        cols = {c["name"]: c for c in table["columns"]}
        assert cols["id"]["key_role"] == "PK"
        assert cols["id"]["nullable"] is False
        assert cols["customer_id"]["key_role"] == "FK"
        assert cols["customer_id"]["foreign_key"]["foreign_table"] == "customer"
        assert cols["note"]["comment"] == "free text"
        assert cols["note"]["key_role"] == ""

    def test_a_column_with_no_captured_nullability_is_none_not_false(self, registry):
        """A catalog-only-fallback column has no source for `is_nullable` at
        all (connection.py's own docstring) — reporting `False` here would
        be a confident wrong answer, not a genuine "not nullable" measurement."""
        registry.write_detail_rows("database_tables", "db", "2026-09-27T00:00:00",
            rows=[_table("public", "t", row_count=None)])
        row = _column("public", "t", "c", 1, nullable=True)
        row["is_nullable"] = None
        registry.write_detail_rows("database_columns", "db", "2026-09-27T00:00:00", rows=[row])
        tree = schema_inventory_tree(registry, "db")
        col = tree["schemas"][0]["tables"][0]["columns"][0]
        assert col["nullable"] is None

    def test_row_count_state_carries_the_estimate_stamp(self, registry):
        registry.write_detail_rows("database_tables", "db", "2026-09-27T00:00:00",
            rows=[_table("public", "t", row_count=50, state=STATE_CATALOG_ESTIMATE)])
        registry.write_detail_rows("database_columns", "db", "2026-09-27T00:00:00",
            rows=[_column("public", "t", "id", 1, pk=True)])
        tree = schema_inventory_tree(registry, "db")
        table = tree["schemas"][0]["tables"][0]
        assert table["row_count"] == 50
        assert table["row_count_state"] == STATE_CATALOG_ESTIMATE

    def test_a_table_never_measured_reports_none_not_a_false_zero(self, registry):
        registry.write_detail_rows("database_tables", "db", "2026-09-27T00:00:00",
            rows=[_table("public", "t", row_count=None, size_bytes=None)])
        registry.write_detail_rows("database_columns", "db", "2026-09-27T00:00:00", rows=[])
        tree = schema_inventory_tree(registry, "db")
        table = tree["schemas"][0]["tables"][0]
        assert table["row_count"] is None
        assert table["size_bytes"] is None

    def test_schema_order_matches_container_rows_and_carries_the_reason(self, registry):
        from resource_explorer.surveyors.database.survey_definition_adapter import (
            _schema_inventory_container_rows,
        )
        registry.write_detail_rows("database_tables", "db", "2026-09-27T00:00:00",
            rows=[_table("busy", "a", row_count=100), _table("quiet", "a", row_count=0)])
        registry.write_detail_rows("database_columns", "db", "2026-09-27T00:00:00",
            rows=[_column("busy", "a", "id", 1, pk=True), _column("quiet", "a", "id", 1, pk=True)])
        rows = _schema_inventory_container_rows(registry, "db")
        tree = schema_inventory_tree(registry, "db")
        assert [s["schema"] for s in tree["schemas"]] == [r["schema"] for r in rows]
        assert all("reason" in s for s in tree["schemas"])

    def test_system_schema_is_folded_not_expanded_to_tables(self, registry):
        registry.write_detail_rows("database_tables", "db", "2026-09-27T00:00:00",
            rows=[_table("public", "a", row_count=1), _table("pg_catalog", "pg_class", row_count=1)])
        registry.write_detail_rows("database_columns", "db", "2026-09-27T00:00:00",
            rows=[_column("public", "a", "id", 1, pk=True)])
        tree = schema_inventory_tree(registry, "db")
        system_row = next(s for s in tree["schemas"] if s["classification"] == "system")
        assert system_row["system_count"] == 1
        assert "tables" not in system_row
