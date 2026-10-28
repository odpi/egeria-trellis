"""Tests for the catalog-only schema-enumeration fallback (design: REPLY-
DATABASE-CREDENTIAL-CAPABILITY-VISIBILITY.md §0, replying to
ASK-DATABASE-CREDENTIAL-CAPABILITY-VISIBILITY.md #251).

`information_schema.tables`/`.columns` (the source `PostgreSQLConnection.
get_schema_info()`/`_get_tables_for_schema()` normally reads) are
privilege-filtered by Postgres: a table with no `SELECT` grant on it simply
does not appear. `pg_class`/`pg_attribute`/`pg_namespace` are catalog
metadata and are not — any connected role can read them regardless of
`USAGE`/`SELECT` grants (the same fact `get_credential_capability()` relies
on, PR #253). `_catalog_only_fallback()` uses that gap to recover table
names, column names/types and an ANALYZE-time row estimate
(`pg_class.reltuples`) for tables `information_schema` hid entirely, tagging
them `STATE_CATALOG_ESTIMATE` rather than letting them look identical to a
fully, exactly measured row.

No live Postgres is used. `_FakeCursorConnection` is a real
`PostgreSQLConnection` with `execute_query` replaced by a fake dispatcher
keyed on substrings of the SQL text — the same "duck-typed stand-in records
the SQL it was asked to run" pattern `test_postgres_column_profile.py`'s
`_FakeSamplingConnection` uses, adapted to a connection that must answer
several *different* queries (schemas, PK, FK, information_schema tables/
columns, and now the two catalog-only queries) rather than one.

Three scenarios, matching the task's own three cases:

1. **Full access** — `information_schema` already sees every table in the
   schema; the fallback must never trigger, and behaviour (including exact
   PK/FK/nullable/default data) is unchanged.
2. **Zero access to a schema** — `information_schema` returns nothing for
   the schema; the fallback provides every table's name, column names/types
   and an estimated row count, all clearly marked.
3. **Partial access** — one table visible via `information_schema`, one
   only via the catalog; both appear, correctly and distinctly labeled.

A second block of tests covers the two downstream consumers this must flow
through to make "How big is this database" honest:
`database_rows_from_survey_data()` (turns the connection's dict into
`database_tables`/`database_columns` detail rows) and
`_schema_inventory_results`/`_row_count_snapshot_results` (the results
readers `facts.py` calls) plus `facts.py`'s own note text.
"""
from __future__ import annotations

import pytest

from resource_explorer.registry import (
    DatabaseEntity,
    ProjectRegistry,
    STATE_CATALOG_ESTIMATE,
    STATE_MEASURED,
)
from resource_explorer.surveyors.database.connection import PostgreSQLConnection
from resource_explorer.surveyors.result_materializer import database_rows_from_survey_data
from resource_explorer.surveyors.database.survey_definition_adapter import (
    _row_count_snapshot_results,
    _schema_inventory_results,
)


# ── low-level: PostgreSQLConnection._get_tables_for_schema() ───────────────


class _FakeCursorConnection(PostgreSQLConnection):
    """A real PostgreSQLConnection with `execute_query` swapped for a fake
    dispatcher, so `_get_tables_for_schema()`/`_catalog_only_fallback()` run
    for real against canned SQL responses — no live Postgres, no psycopg2.
    """

    def __init__(self, information_schema_tables, catalog_tables, catalog_columns,
                 catalog_pk_rows=None, catalog_fk_rows=None, fail_catalog_keys=False):
        super().__init__(host="localhost", port=5432, database="x", user="u", password="p")
        #: {table_name: {"table_type": ..., "columns": [col_row, ...]}} — what
        #: information_schema.tables/columns would return for this schema.
        self._info_schema_tables = information_schema_tables
        #: {table_name: {"relkind": "r", "reltuples": N}} — what pg_class
        #: (unfiltered) says exists in this schema.
        self._catalog_tables = catalog_tables
        #: {table_name: [{"column_name", "ordinal_position", "data_type"}]}
        #: — what pg_attribute (unfiltered) says about a table's columns.
        self._catalog_columns = catalog_columns
        #: Slice 21b — what `pg_constraint` (unfiltered) says a fallback
        #: table's PK/FK constraints are. `[]` (the default) means the
        #: catalog query succeeded and genuinely found no keys, distinct
        #: from `fail_catalog_keys=True`, which simulates the query itself
        #: failing (e.g. permission oddity) so `_catalog_keys_for_schema()`
        #: falls back to `None` (unestablished, not a guessed `False`).
        self._catalog_pk_rows = catalog_pk_rows or []
        self._catalog_fk_rows = catalog_fk_rows or []
        self._fail_catalog_keys = fail_catalog_keys
        self.executed: list[str] = []

    def execute_query(self, query, params=()):
        self.executed.append(query)
        if "information_schema.table_constraints" in query and "PRIMARY KEY" in query:
            return []
        if "information_schema.table_constraints" in query and "FOREIGN KEY" in query:
            return []
        if "FROM pg_constraint con" in query and "contype = 'p'" in query:
            if self._fail_catalog_keys:
                raise RuntimeError("pg_constraint (PK) unreachable in this test")
            return list(self._catalog_pk_rows)
        if "FROM pg_constraint con" in query and "contype = 'f'" in query:
            if self._fail_catalog_keys:
                raise RuntimeError("pg_constraint (FK) unreachable in this test")
            return list(self._catalog_fk_rows)
        if "FROM information_schema.tables t" in query:
            rows = []
            for name, info in self._info_schema_tables.items():
                for col in info["columns"] or [None]:
                    row = {
                        "table_name": name,
                        "table_type": info["table_type"],
                        "table_description": "",
                        "column_name": col["column_name"] if col else None,
                        "data_type": col["data_type"] if col else None,
                        "udt_name": col["data_type"] if col else None,
                        "is_nullable": "YES" if col and col.get("nullable", True) else "NO",
                        "column_default": None,
                        "ordinal_position": col["ordinal_position"] if col else None,
                        "character_maximum_length": None,
                        "numeric_precision": None,
                        "numeric_scale": None,
                        "column_description": "",
                    }
                    rows.append(row)
            return rows
        if "c.relkind IN ('r', 'p', 'v', 'm')" in query:
            return [
                {"table_name": name, "relkind": info["relkind"], "reltuples": info["reltuples"]}
                for name, info in self._catalog_tables.items()
            ]
        if "FROM pg_attribute a" in query:
            table_name = params[1] if len(params) > 1 else None
            return list(self._catalog_columns.get(table_name, []))
        raise AssertionError(f"unexpected query in test fake: {query}")


def _schema(name="public"):
    return name


class TestFullAccessNeverTriggersFallback:
    def test_information_schema_already_has_every_catalog_table(self):
        conn = _FakeCursorConnection(
            information_schema_tables={
                "customers": {
                    "table_type": "BASE TABLE",
                    "columns": [
                        {"column_name": "id", "data_type": "integer",
                         "ordinal_position": 1, "nullable": False},
                    ],
                },
            },
            catalog_tables={"customers": {"relkind": "r", "reltuples": 20}},
            catalog_columns={},
        )
        tables = conn._get_tables_for_schema(_schema())
        assert len(tables) == 1
        assert tables[0]["name"] == "customers"
        assert tables[0]["source"] == "information_schema"
        assert tables[0]["columns"][0]["source"] == "information_schema"
        # No pg_attribute query should even have been issued, because the
        # catalog fallback has nothing to add.
        assert not any("FROM pg_attribute a" in q for q in conn.executed)


class TestZeroAccessToASchema:
    def test_catalog_fallback_recovers_every_table_and_its_columns(self):
        conn = _FakeCursorConnection(
            information_schema_tables={},  # egeria_user: no SELECT anywhere in coco_ods
            catalog_tables={
                "orders": {"relkind": "r", "reltuples": 1500},
                "line_items": {"relkind": "r", "reltuples": 42000},
            },
            catalog_columns={
                "orders": [
                    {"column_name": "id", "ordinal_position": 1, "data_type": "integer"},
                    {"column_name": "customer_id", "ordinal_position": 2, "data_type": "integer"},
                ],
                "line_items": [
                    {"column_name": "id", "ordinal_position": 1, "data_type": "bigint"},
                ],
            },
        )
        tables = conn._get_tables_for_schema(_schema("coco_ods"))
        by_name = {t["name"]: t for t in tables}
        assert set(by_name) == {"orders", "line_items"}

        orders = by_name["orders"]
        assert orders["source"] == "catalog_fallback"
        assert orders["row_count_estimate"] == 1500
        assert orders["row_count_basis"] == "estimated"
        assert [c["name"] for c in orders["columns"]] == ["id", "customer_id"]
        for col in orders["columns"]:
            assert col["source"] == "catalog_fallback"
            # `nullable`/`default` have no catalog-only source at all — see
            # connection.py's docstring on why they stay unestablished
            # rather than a fabricated guess.
            assert col["nullable"] is None
            assert col["default"] is None
            # `is_primary_key` DOES have a catalog-only source (`pg_
            # constraint`, Slice 21b) — with no PK rows configured in this
            # test, the catalog query genuinely ran and found none, so this
            # is a real, established `False`, not a guess.
            assert col["is_primary_key"] is False
            assert col["type"]  # a real Postgres type name, not blank

        assert by_name["line_items"]["row_count_estimate"] == 42000


class TestPartialAccess:
    def test_visible_table_and_hidden_table_both_appear_correctly_labeled(self):
        conn = _FakeCursorConnection(
            information_schema_tables={
                "public_view_table": {
                    "table_type": "BASE TABLE",
                    "columns": [
                        {"column_name": "id", "data_type": "integer",
                         "ordinal_position": 1, "nullable": False},
                    ],
                },
            },
            catalog_tables={
                "public_view_table": {"relkind": "r", "reltuples": 5},
                "hidden_table": {"relkind": "r", "reltuples": 999},
            },
            catalog_columns={
                "hidden_table": [
                    {"column_name": "secret_id", "ordinal_position": 1, "data_type": "uuid"},
                ],
            },
        )
        tables = conn._get_tables_for_schema(_schema())
        by_name = {t["name"]: t for t in tables}
        assert set(by_name) == {"public_view_table", "hidden_table"}
        assert by_name["public_view_table"]["source"] == "information_schema"
        assert "row_count_estimate" not in by_name["public_view_table"]
        assert by_name["hidden_table"]["source"] == "catalog_fallback"
        assert by_name["hidden_table"]["row_count_estimate"] == 999


class TestCatalogFallbackRecoversPrimaryAndForeignKeys:
    """Slice 21b: `pg_constraint` is catalog metadata like `pg_class`/
    `pg_attribute` — not privilege-filtered — so a catalog-fallback table no
    longer needs to report `is_primary_key`/`foreign_key` as `None`. Fixed
    2026-09-26; before this, `_catalog_columns_for_table()` always wrote
    `None` for both, unconditionally, even when the key data was exactly as
    available as the column names it was already recovering."""

    def test_a_recovered_table_gets_its_real_primary_key(self):
        conn = _FakeCursorConnection(
            information_schema_tables={},
            catalog_tables={"orders": {"relkind": "r", "reltuples": 1500}},
            catalog_columns={"orders": [
                {"column_name": "id", "ordinal_position": 1, "data_type": "integer"},
                {"column_name": "customer_id", "ordinal_position": 2, "data_type": "integer"},
            ]},
            catalog_pk_rows=[{"table_name": "orders", "column_name": "id"}],
        )
        tables = conn._get_tables_for_schema(_schema("coco_ods"))
        by_name = {c["name"]: c for c in tables[0]["columns"]}
        assert by_name["id"]["is_primary_key"] is True
        assert by_name["customer_id"]["is_primary_key"] is False

    def test_a_recovered_table_gets_its_real_foreign_key(self):
        conn = _FakeCursorConnection(
            information_schema_tables={},
            catalog_tables={"orders": {"relkind": "r", "reltuples": 1500}},
            catalog_columns={"orders": [
                {"column_name": "customer_id", "ordinal_position": 1, "data_type": "integer"},
            ]},
            catalog_fk_rows=[{
                "table_name": "orders", "column_name": "customer_id",
                "foreign_schema": "coco_ods", "foreign_table": "customers",
                "foreign_column": "id",
            }],
        )
        tables = conn._get_tables_for_schema(_schema("coco_ods"))
        col = tables[0]["columns"][0]
        assert col["foreign_key"] == {
            "foreign_schema": "coco_ods", "foreign_table": "customers", "foreign_column": "id",
        }

    def test_information_schema_visible_table_still_gets_catalog_keys(self):
        """BRIEF-KEYS-AND-ACTIVITY-CLOBBER.md §A (2026-09-27): PK/FK are no
        longer read from `information_schema.table_constraints`/
        `key_column_usage`/`constraint_column_usage` at all, even for a
        table `information_schema` can see fully — that query joined
        `constraint_column_usage`, which is not keyed per column and
        multiplied/collapsed rows when the REFERENCED table (e.g.
        `person.businessentity`, referenced by five different tables on
        AdventureWorks) carried several referencing constraints. `pg_
        constraint`/`pg_attribute`, keyed by the REFERENCING side, has no
        such collapse. This reproduces the shape: three tables' worth of FK
        columns pointing at the same referenced table, a two-column
        composite-style FK, and one column carrying two distinct FK
        constraints (rare, legal) — all of it must survive."""
        conn = _FakeCursorConnection(
            information_schema_tables={
                "orderdetail": {
                    "table_type": "BASE TABLE",
                    "columns": [
                        {"column_name": "order_id", "data_type": "integer",
                         "ordinal_position": 1, "nullable": False},
                        {"column_name": "line_no", "data_type": "integer",
                         "ordinal_position": 2, "nullable": False},
                        {"column_name": "vendor_id", "data_type": "integer",
                         "ordinal_position": 3, "nullable": True},
                    ],
                },
            },
            catalog_tables={"orderdetail": {"relkind": "r", "reltuples": 100}},
            catalog_columns={},
            catalog_fk_rows=[
                # Two columns of the same local table referencing the same
                # target table on two different columns — the composite
                # shape (each local column contributes its own entry).
                {"table_name": "orderdetail", "column_name": "order_id",
                 "foreign_schema": "sales", "foreign_table": "orders",
                 "foreign_column": "id"},
                {"table_name": "orderdetail", "column_name": "line_no",
                 "foreign_schema": "sales", "foreign_table": "orders",
                 "foreign_column": "line_no"},
                # One column, two distinct FK constraints (legal).
                {"table_name": "orderdetail", "column_name": "vendor_id",
                 "foreign_schema": "purchasing", "foreign_table": "vendor",
                 "foreign_column": "id"},
                {"table_name": "orderdetail", "column_name": "vendor_id",
                 "foreign_schema": "purchasing", "foreign_table": "temp_vendor",
                 "foreign_column": "id"},
            ],
        )
        tables = conn._get_tables_for_schema(_schema("sales"))
        assert tables[0]["source"] == "information_schema"
        by_name = {c["name"]: c for c in tables[0]["columns"]}

        # All three FK-bearing columns are captured — nothing collapsed by
        # a referenced-table-side collision the way `constraint_column_
        # usage` used to collapse them.
        assert by_name["order_id"]["foreign_key"] == {
            "foreign_schema": "sales", "foreign_table": "orders", "foreign_column": "id",
        }
        assert by_name["line_no"]["foreign_key"] == {
            "foreign_schema": "sales", "foreign_table": "orders", "foreign_column": "line_no",
        }
        assert by_name["vendor_id"]["foreign_key"] == {
            "foreign_schema": "purchasing", "foreign_table": "vendor", "foreign_column": "id",
        }
        # The rare column-carries-two-FKs case keeps both, not just the one
        # that happened to be inserted last.
        assert by_name["vendor_id"]["foreign_keys"] == [
            {"foreign_schema": "purchasing", "foreign_table": "vendor", "foreign_column": "id"},
            {"foreign_schema": "purchasing", "foreign_table": "temp_vendor", "foreign_column": "id"},
        ]
        # And no information_schema PK/FK query was ever issued — the
        # retired queries are gone, not merely unused.
        assert not any("information_schema.table_constraints" in q for q in conn.executed)

    def test_a_failed_catalog_key_query_stays_unestablished_not_a_guessed_false(self):
        """The `pg_constraint` read itself can fail (an odd permission
        setup, a connection hiccup) — that must NOT be read as "genuinely no
        keys." `_catalog_keys_for_schema()` returns `None` (not `{}`) for a
        failed half, and `_catalog_columns_for_table()` must propagate that
        as `None`, not silently degrade a failure into a confident `False`
        the exact way `nullable`/`default` already refuse to."""
        conn = _FakeCursorConnection(
            information_schema_tables={},
            catalog_tables={"orders": {"relkind": "r", "reltuples": 1500}},
            catalog_columns={"orders": [
                {"column_name": "id", "ordinal_position": 1, "data_type": "integer"},
            ]},
            fail_catalog_keys=True,
        )
        tables = conn._get_tables_for_schema(_schema("coco_ods"))
        col = tables[0]["columns"][0]
        assert col["is_primary_key"] is None
        assert col["foreign_key"] is None


# ── database_rows_from_survey_data(): connection dict -> detail rows ───────


def _survey_data_with(schema_tables):
    return {
        "schema_info": {
            "schemas": [
                {"name": "coco_ods", "description": "", "tables": schema_tables},
            ],
            "total_tables": len(schema_tables),
            "total_columns": sum(len(t.get("columns") or []) for t in schema_tables),
        },
        "statistics": {},
        "views": [],
    }


class TestDatabaseRowsFromSurveyData:
    def test_catalog_fallback_table_gets_state_catalog_estimate(self):
        survey_data = _survey_data_with([
            {
                "name": "orders", "type": "BASE TABLE", "description": "",
                "source": "catalog_fallback",
                "row_count_estimate": 1500,
                "row_count_basis": "estimated",
                "columns": [
                    {"name": "id", "type": "integer", "base_type": "integer",
                     "nullable": None, "default": None, "position": 1,
                     "description": "", "is_primary_key": None,
                     "foreign_key": None, "source": "catalog_fallback"},
                ],
            },
        ])
        rows = database_rows_from_survey_data(survey_data)
        [table_row] = rows["database_tables"]
        assert table_row["state"] == STATE_CATALOG_ESTIMATE
        # No pg_stat_user_tables match was simulated (row_count absent) —
        # the reltuples estimate is what should have been used.
        assert table_row["row_count"] == 1500

        [col_row] = rows["database_columns"]
        assert col_row["state"] == STATE_CATALOG_ESTIMATE
        assert col_row["is_nullable"] is None
        assert col_row["is_primary_key"] is None

    def test_information_schema_table_still_gets_state_measured(self):
        survey_data = _survey_data_with([
            {
                "name": "customers", "type": "BASE TABLE", "description": "",
                "source": "information_schema", "row_count": 20,
                "columns": [
                    {"name": "id", "type": "integer", "base_type": "integer",
                     "nullable": False, "default": None, "position": 1,
                     "description": "", "is_primary_key": True,
                     "foreign_key": None, "source": "information_schema"},
                ],
            },
        ])
        rows = database_rows_from_survey_data(survey_data)
        [table_row] = rows["database_tables"]
        assert table_row["state"] == STATE_MEASURED
        assert table_row["row_count"] == 20
        [col_row] = rows["database_columns"]
        assert col_row["state"] == STATE_MEASURED
        assert col_row["is_nullable"] == 0
        assert col_row["is_primary_key"] == 1

    def test_catalog_fallback_table_with_a_real_stats_match_uses_that_not_the_estimate(self):
        # Actually the common case, not the rare one (corrected 2026-09-24/25
        # — `pg_stat_user_tables` is unfiltered, no `pg_monitor` needed; see
        # `DATABASE-STEP-CAPABILITY-AUDIT.md`'s "Correction"): any credential
        # can see `pg_stat_user_tables` for a table it still cannot SELECT
        # from. database_surveyor.py's _store_results would set a real
        # `row_count` in that case; this checks the materializer prefers it
        # over `row_count_estimate` when both are present.
        survey_data = _survey_data_with([
            {
                "name": "orders", "type": "BASE TABLE", "description": "",
                "source": "catalog_fallback", "row_count": 1502,
                "row_count_estimate": 1500, "row_count_basis": "estimated",
                "columns": [],
            },
        ])
        rows = database_rows_from_survey_data(survey_data)
        [table_row] = rows["database_tables"]
        assert table_row["row_count"] == 1502


# ── results readers: what facts.py actually sees ───────────────────────────


@pytest.fixture
def registry(tmp_path):
    return ProjectRegistry(db_path=str(tmp_path / "test.db"))


@pytest.fixture
def db_entity(registry):
    entity = DatabaseEntity(
        slug="coco_pharma", display_name="Coco Pharma", db_type="postgresql",
        host="localhost", port=5432, database_name="coco_pharma",
        db_user="egeria_user", db_password="secret",
    )
    registry.register_database(entity)
    return entity


def _write_tables(registry, slug, rows, surveyed_at="2026-09-24T00:00:00"):
    registry.write_detail_rows("database_tables", slug, surveyed_at, rows=rows)


def _write_columns(registry, slug, rows, surveyed_at="2026-09-24T00:00:00"):
    registry.write_detail_rows("database_columns", slug, surveyed_at, rows=rows)


class TestSchemaInventoryResultsSurfaceTheEstimate:
    def test_mixed_measured_and_catalog_estimate_tables(self, registry, db_entity):
        slug = db_entity.slug
        _write_tables(registry, slug, [
            {"schema_name": "public", "table_name": "customers",
             "table_type": "BASE TABLE", "row_count": 5, "state": STATE_MEASURED},
            {"schema_name": "coco_ods", "table_name": "orders",
             "table_type": "BASE TABLE", "row_count": 1500,
             "state": STATE_CATALOG_ESTIMATE},
        ])
        _write_columns(registry, slug, [
            {"schema_name": "public", "table_name": "customers",
             "column_name": "id", "state": STATE_MEASURED},
            {"schema_name": "coco_ods", "table_name": "orders",
             "column_name": "id", "state": STATE_CATALOG_ESTIMATE},
        ])

        value = _schema_inventory_results(registry, slug)
        assert value["relation_count"] == 2
        assert value["catalog_only_table_count"] == 1
        by_name = {t["table_name"]: t for t in value["tables"]}
        assert by_name["customers"]["row_count_is_estimate"] is False
        assert by_name["orders"]["row_count_is_estimate"] is True

    def test_fully_measured_database_reports_zero_catalog_only(self, registry, db_entity):
        slug = db_entity.slug
        _write_tables(registry, slug, [
            {"schema_name": "public", "table_name": "customers",
             "table_type": "BASE TABLE", "row_count": 5, "state": STATE_MEASURED},
        ])
        value = _schema_inventory_results(registry, slug)
        assert value["catalog_only_table_count"] == 0
        assert value["tables"][0]["row_count_is_estimate"] is False


class TestSchemaInventoryResultsNameRelationKindsSeparately:
    """Design ruling (security-model.md §2.1/§3.4, 2026-09-26): relation
    kinds are reported separately and named, never blended into one "table
    count" -- base_table_count stays the stable field the comparators
    already diff `relation_count` (renamed from `table_count`,
    REPLY-DESIGNER-ROUND2-DATABASE-SCREENS.md §2.3) as if it meant."""

    def test_mixed_relation_kinds_are_each_counted(self, registry, db_entity):
        slug = db_entity.slug
        _write_tables(registry, slug, [
            {"schema_name": "public", "table_name": "customers",
             "table_type": "BASE TABLE", "row_count": 5, "state": STATE_MEASURED},
            {"schema_name": "public", "table_name": "recent_orders",
             "table_type": "VIEW", "state": STATE_MEASURED},
            {"schema_name": "public", "table_name": "monthly_totals",
             "table_type": "MATERIALIZED VIEW", "state": STATE_MEASURED},
            {"schema_name": "public", "table_name": "remote_customers",
             "table_type": "FOREIGN", "state": STATE_MEASURED},
        ])
        value = _schema_inventory_results(registry, slug)
        assert value["relation_count"] == 4
        assert value["base_table_count"] == 1
        assert value["view_count"] == 1
        assert value["materialized_view_count"] == 1
        assert value["foreign_table_count"] == 1

    def test_all_base_tables_reports_zero_for_the_others(self, registry, db_entity):
        slug = db_entity.slug
        _write_tables(registry, slug, [
            {"schema_name": "public", "table_name": "a",
             "table_type": "BASE TABLE", "state": STATE_MEASURED},
            {"schema_name": "public", "table_name": "b",
             "table_type": "BASE TABLE", "state": STATE_MEASURED},
        ])
        value = _schema_inventory_results(registry, slug)
        assert value["relation_count"] == value["base_table_count"] == 2
        assert value["view_count"] == 0
        assert value["materialized_view_count"] == 0
        assert value["foreign_table_count"] == 0


class TestRowCountSnapshotResultsLabelEstimates:
    def test_estimated_count_reported_alongside_measured_count(self, registry, db_entity):
        slug = db_entity.slug
        _write_tables(registry, slug, [
            {"schema_name": "public", "table_name": "customers",
             "table_type": "BASE TABLE", "row_count": 5, "state": STATE_MEASURED},
            {"schema_name": "coco_ods", "table_name": "orders",
             "table_type": "BASE TABLE", "row_count": 1500,
             "state": STATE_CATALOG_ESTIMATE},
        ])
        value = _row_count_snapshot_results(registry, slug)
        assert value["measured_count"] == 2
        assert value["estimated_count"] == 1
        assert value["total_row_count"] == 1505
        by_name = {t["table_name"]: t for t in value["tables"]}
        assert by_name["orders"]["row_count_is_estimate"] is True
        assert by_name["customers"]["row_count_is_estimate"] is False


# ── facts.py: the note a reader actually sees ───────────────────────────────


class TestFactsNoteCarriesTheCatalogCaveat:
    def test_note_states_catalog_visible_count_and_select_fraction(self):
        from resource_explorer.facts import FactLayer
        from resource_explorer.surveyors.result_status import MEASURED_WITHIN_CREDENTIAL_SCOPE

        value = {
            "relation_count": 23,
            "catalog_only_table_count": 20,
            "_status": {
                "state": MEASURED_WITHIN_CREDENTIAL_SCOPE,
                "connected_as": "egeria_user",
                "fraction": "3 of 23 tables in 6 of 8 schemas",
            },
        }
        note = FactLayer._note_for(MEASURED_WITHIN_CREDENTIAL_SCOPE, value, {})
        # REPLY-COPY-REVIEW-CREDENTIAL-AND-FIT-LANGUAGE.md §3: the headline
        # (the count and who can read how much of it) leads, not the
        # parenthetical, and "catalog-only ones" -- a term coined in the same
        # sentence it's used -- became "the other N", computed rather than
        # left for the reader to work out.
        assert "23 tables, of which" in note
        assert "can read 3" in note
        assert "the other 20" in note
        assert "planner estimates, not exact" in note

    def test_note_falls_back_to_the_bare_fraction_with_no_catalog_recovery(self):
        from resource_explorer.facts import FactLayer
        from resource_explorer.surveyors.result_status import MEASURED_WITHIN_CREDENTIAL_SCOPE

        value = {
            "relation_count": 3,
            "catalog_only_table_count": 0,
            "_status": {
                "state": MEASURED_WITHIN_CREDENTIAL_SCOPE,
                "connected_as": "egeria_user",
                "fraction": "3 of 26 tables in 6 of 8 schemas",
            },
        }
        note = FactLayer._note_for(MEASURED_WITHIN_CREDENTIAL_SCOPE, value, {})
        assert "visible via catalog" not in note
        assert "3 of 26 tables in 6 of 8 schemas" in note


