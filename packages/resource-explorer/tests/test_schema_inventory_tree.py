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


class TestEmptyIsThreeDistinctStates:
    """REPLY-DESIGNER-ROUND2-DATABASE-SCREENS.md §3.3: `_schema_inventory_
    container_rows` used to fold "no tables at all", "never measured" and
    "measured, and zero" into one `"empty"` classification, storing
    `row_total or 0` for the never-measured case too — a genuine unknown
    rendered identically to a confirmed zero. Each of these three tests
    would fail if any two of the three states collapsed back into the same
    classification/`row_total`.
    """

    def _row_for(self, registry, slug, schema):
        from resource_explorer.surveyors.database.survey_definition_adapter import (
            _schema_inventory_container_rows,
        )
        rows = _schema_inventory_container_rows(registry, slug)
        return next(r for r in rows if r["schema"] == schema)

    def test_zero_tables_renders_no_tables_not_empty(self, registry):
        # A schema the credential probe knows about (USAGE granted) with
        # zero tables never appears in `database_tables` at all -- the
        # `states`/`by_schema` backfill (Slice 21a follow-up) is what gives
        # it a row here, with `table_count: 0`. Needs a credential-capability
        # probe recorded via `record_database_survey`, at an EARLIER
        # `surveyed_at` than the detail-row write below -- recording a
        # survey backfills placeholder `database_tables` rows from its own
        # (empty, in this fixture) schema info, and doing that at the SAME
        # `surveyed_at` as the real detail rows silently displaces them
        # (`query_detail_rows`'s own "latest wins" pick between two writes
        # under one key) -- registry.py's own docstring on
        # `record_database_survey` warns of exactly this collision.
        registry.record_database_survey(
            "db", schema_count=2, table_count=1, column_count=1,
            survey_data={"credential_capability": {"by_schema": {
                "other": {"usage_granted": True, "table_total": 1, "table_select": 1},
                "empty_schema": {"usage_granted": True, "table_total": 0, "table_select": 0},
            }}},
            surveyed_at="2026-09-26T00:00:00",
        )
        registry.write_detail_rows("database_tables", "db", "2026-09-27T00:00:00",
            rows=[_table("other", "a", row_count=5)])
        row = self._row_for(registry, "db", "empty_schema")
        assert row["classification"] == "no_tables"
        assert row["table_count"] == 0
        assert row["row_total"] is None

    def test_row_total_none_renders_rows_not_measured(self, registry):
        registry.write_detail_rows("database_tables", "db", "2026-09-27T00:00:00",
            rows=[_table("unmeasured", "t", row_count=None)])
        row = self._row_for(registry, "db", "unmeasured")
        assert row["classification"] == "not_measured"
        assert row["row_total"] is None

    def test_row_total_zero_renders_measured_zero(self, registry):
        registry.write_detail_rows("database_tables", "db", "2026-09-27T00:00:00",
            rows=[_table("counted_empty", "t", row_count=0)])
        row = self._row_for(registry, "db", "counted_empty")
        assert row["classification"] == "empty"
        assert row["row_total"] == 0

    def test_the_three_states_are_pairwise_distinguishable(self, registry):
        registry.write_detail_rows("database_tables", "db", "2026-09-27T00:00:00", rows=[
            _table("unmeasured", "t", row_count=None),
            _table("counted_empty", "t", row_count=0),
        ])
        unmeasured = self._row_for(registry, "db", "unmeasured")
        counted_empty = self._row_for(registry, "db", "counted_empty")
        assert unmeasured["classification"] != counted_empty["classification"]
        assert unmeasured["row_total"] != counted_empty["row_total"]

    def test_headline_uses_three_distinct_phrases(self, registry):
        from resource_explorer.surveyors.database.survey_definition_adapter import (
            _schema_inventory_container_headline,
        )
        registry.write_detail_rows("database_tables", "db", "2026-09-27T00:00:00", rows=[
            _table("unmeasured", "t", row_count=None),
            _table("counted_empty", "t", row_count=0),
        ])
        headline = _schema_inventory_container_headline(registry, "db")
        assert "rows not measured" in headline["label"]
        assert "0 row(s) — empty" in headline["label"]

    def test_measurements_note_uses_three_distinct_phrases(self, registry):
        from resource_explorer.surveyors.database.survey_definition_adapter import (
            _schema_inventory_container_measurements,
        )
        registry.write_detail_rows("database_tables", "db", "2026-09-27T00:00:00", rows=[
            _table("unmeasured", "t", row_count=None),
            _table("counted_empty", "t", row_count=0),
        ])
        rows = _schema_inventory_container_measurements(registry, "db")
        notes = {r["name"]: r["note"] for r in rows}
        assert notes["unmeasured"] == "rows not measured"
        assert notes["counted_empty"] == "empty"


class TestCocoPharmaSalesSchemasLiveRegression:
    """Live-checked 2026-09-27 against the shared registry
    (`localhost_docker_coco_pharma`): `eu_sales`, `target_sales` and
    `us_sales` each hold exactly one table, and the credential the survey
    ran as has `usage_granted=True` and `table_select == table_total == 1`
    for all three (SCOPE_READABLE, not a visibility gap) -- yet every one of
    those tables' `database_tables.row_count` is stored `NULL` with
    `state == "measured"` (never went through `pg_stat_user_tables`, i.e.
    never ANALYZEd/VACUUMed -- the classic way to get Postgres's `reltuples
    == -1` "never analyzed" sentinel, per `database_surveyor.py`'s own
    `NEVER_ANALYZED` docstring). Before this fix, `_schema_inventory_
    container_rows` folded that into `row_total or 0` and reported these
    three schemas as measured, confirmed-empty ("0 row(s) — empty"). They
    are NOT genuine zeros -- they were never measured. This regression test
    pins the corrected reading using the exact shape found live."""

    def test_pins_never_measured_not_confirmed_zero(self, registry):
        registry.write_detail_rows("database_tables", "db", "2026-09-27T00:00:00", rows=[
            _table("eu_sales", "eu_sales_forecast", row_count=None, state="measured"),
            _table("target_sales", "consolidated_forecast", row_count=None, state="measured"),
            _table("us_sales", "us_sales_forecast", row_count=None, state="measured"),
        ])
        from resource_explorer.surveyors.database.survey_definition_adapter import (
            _schema_inventory_container_rows,
        )
        rows = {r["schema"]: r for r in _schema_inventory_container_rows(registry, "db")}
        for schema in ("eu_sales", "target_sales", "us_sales"):
            assert rows[schema]["classification"] == "not_measured", schema
            assert rows[schema]["row_total"] is None, schema
            assert rows[schema]["table_count"] == 1, schema
