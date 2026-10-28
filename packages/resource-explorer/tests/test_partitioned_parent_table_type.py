"""A partitioned parent (relkind `p`) is stored as "PARTITIONED TABLE", not "BASE TABLE".

Only the statistics currency sets it apart (autovacuum never analyzes a parent);
everywhere else it is still a table: counted, listed, checked, labelled "table".
"""
from __future__ import annotations

from pathlib import Path

from resource_explorer.statistics_currency import PARTITIONED, derive_statistics_currency
from resource_explorer.surveyors.database.db_derived import _is_base_table
from resource_explorer.surveyors.database.survey_definition_adapter import (
    _schema_inventory_container_headline,
    _schema_inventory_headline,
)
from resource_explorer.table_kinds import PARTITIONED_TABLE, is_table_type
from tests.test_postgres_catalog_fallback import _FakeCursorConnection, _schema
from tests import test_schema_inventory_container_headline as _container
from tests.test_schema_inventory_headline import _FakeRegistry, _table

AS_OF = "2026-10-09T10:00:00"
_COL = [{"column_name": "id", "data_type": "integer", "ordinal_position": 1, "nullable": False}]


class TestRelkindMapping:
    def test_catalog_summary_maps_p_to_partitioned_and_keeps_relkind(self):
        conn = _FakeCursorConnection(
            information_schema_tables={},
            catalog_tables={"parent": {"relkind": "p", "reltuples": -1},
                            "plain": {"relkind": "r", "reltuples": 5}},
            catalog_columns={},
        )
        out = conn._catalog_table_summary("public")
        assert out["parent"]["table_type"] == "PARTITIONED TABLE" and out["parent"]["relkind"] == "p"
        assert out["plain"]["table_type"] == "BASE TABLE" and out["plain"]["relkind"] == "r"

    def test_fallback_only_table_is_stored_as_partitioned(self):
        conn = _FakeCursorConnection(
            information_schema_tables={},
            catalog_tables={"parent": {"relkind": "p", "reltuples": -1}},
            catalog_columns={"parent": [{"column_name": "id", "data_type": "integer", "ordinal_position": 1}]},
        )
        (t,) = conn._get_tables_for_schema(_schema())
        assert t["type"] == "PARTITIONED TABLE" and t["relkind"] == "p"

    def test_information_schema_says_base_table_for_a_parent_and_the_catalog_corrects_it(self):
        conn = _FakeCursorConnection(
            information_schema_tables={"parent": {"table_type": "BASE TABLE", "columns": _COL},
                                       "plain": {"table_type": "BASE TABLE", "columns": _COL}},
            catalog_tables={"parent": {"relkind": "p", "reltuples": -1},
                            "plain": {"relkind": "r", "reltuples": 5}},
            catalog_columns={},
        )
        by = {t["name"]: t for t in conn._get_tables_for_schema(_schema())}
        assert by["parent"]["type"] == "PARTITIONED TABLE" and by["parent"]["source"] == "information_schema"
        assert by["plain"]["type"] == "BASE TABLE"


class TestStillATable:
    def test_helper(self):
        assert is_table_type("BASE TABLE") and is_table_type(PARTITIONED_TABLE) and is_table_type("PARTITIONED_TABLE")
        assert not any(is_table_type(x) for x in ("VIEW", "MATERIALIZED VIEW", "FOREIGN", "", None))

    def test_structural_checks_keep_it(self):
        assert _is_base_table({"table_type": "PARTITIONED TABLE"})

    def test_inventory_headline_counts_it_with_base_tables(self):
        reg = _FakeRegistry(tables=[_table("public", "orders", "PARTITIONED TABLE"),
                                    _table("public", "t", "BASE TABLE"),
                                    _table("public", "v", "VIEW")], columns=[])
        label = _schema_inventory_headline(reg, "db")["label"]
        assert "2 base" in label and "1 view" in label

    def test_a_schema_of_only_a_parent_is_not_views_only(self):
        reg = _container._FakeRegistry(tables=[_container._table("sales", "orders", table_type="PARTITIONED TABLE")])
        label = _schema_inventory_container_headline(reg, "db")["label"]
        assert "no base tables" not in label

    def test_screens_label_it_a_table(self):
        root = Path(__file__).resolve().parent.parent / "resource_explorer/web/static/next"
        assert "'PARTITIONED TABLE': 'table'" in (root / "app.js").read_text()
        assert "'PARTITIONED TABLE': 'table'" in (root / "stages/curate-scope.js").read_text()


class TestStatisticsCurrency:
    def test_table_type_alone_labels_it_partitioned_and_out_of_the_counts(self):
        tables = [{"schema_name": "p", "table_name": "parent", "table_type": "PARTITIONED TABLE"},
                  {"schema_name": "p", "table_name": "part1", "table_type": "BASE TABLE"}]
        rows = [{"schema_name": "p", "table_name": "part1", "last_analyze": "2026-10-08T10:00:00",
                 "stats_reset": "never"}]
        d = derive_statistics_currency(tables, rows, AS_OF, schemas=["p"])
        assert d["tables"]["p.parent"]["state"] == PARTITIONED
        assert d["database"]["total"] == 1 and d["database"]["never"] == 0
