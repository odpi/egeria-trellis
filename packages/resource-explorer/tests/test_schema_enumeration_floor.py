"""Enumeration-floor design ruling (security-model.md §2.1/§3.4, 2026-09-26,
recorded as a Decision in docs/design-notes/SLICE-17B-RENDER-BOUND-LEVEL-GATE-
IMPLEMENTED.md): on an engine whose structural floor is unprivileged
(Postgres), every enumeration RE does -- the credential-capability probe,
`get_schema_info()`'s inventory, row counts -- reads that floor
(`pg_namespace`/`pg_class`), never a privilege-filtered view, so there is
exactly one denominator.

Trigger: `coco_pharma` (2026-09-26) showed the credential banner and the
size answer disagreeing on totals (61 vs. 56 tables) because
`get_schema_info()` enumerated schemas from `information_schema.schemata`
-- privilege-filtered by Postgres to schemas the role owns or holds ANY
grant on -- while `get_credential_capability()` already read the
unprivileged floor for its own probe. A schema with zero grants at all
(`demo`, `demo_auth`) was invisible to the inventory entirely, not merely
thin.

No live Postgres is used. `_FakeFloorConnection` is a real
`PostgreSQLConnection` with `execute_query` replaced by a substring-keyed
fake dispatcher, the same pattern `test_postgres_catalog_fallback.py`'s
`_FakeCursorConnection` uses.
"""
from __future__ import annotations

from resource_explorer.surveyors.database.connection import PostgreSQLConnection


class _FakeFloorConnection(PostgreSQLConnection):
    """Answers exactly the queries `get_schema_info()`, `_enumerate_relations()`
    and `get_credential_capability()` issue, keyed by substring."""

    def __init__(
        self,
        privileged_schema_names: list[str],
        floor_schemas: list[dict],
        floor_tables: list[dict],
        catalog_tables_by_schema: dict[str, dict],
        catalog_columns_by_schema_table: dict[tuple, list[dict]],
    ):
        super().__init__(host="localhost", port=5432, database="x", user="u", password="p")
        self._privileged_schema_names = privileged_schema_names
        self._floor_schemas = floor_schemas
        self._floor_tables = floor_tables
        self._catalog_tables_by_schema = catalog_tables_by_schema
        self._catalog_columns_by_schema_table = catalog_columns_by_schema_table
        self.executed: list[str] = []

    def execute_query(self, query, params=()):
        self.executed.append(query)

        if "FROM information_schema.schemata" in query:
            return [{"schema_name": n} for n in self._privileged_schema_names]

        if "has_schema_privilege(current_user, n.nspname, 'USAGE')" in query:
            # _enumerate_relations' schema half
            return list(self._floor_schemas)

        if "has_table_privilege(current_user, c.oid, 'SELECT')" in query:
            # _enumerate_relations' table half
            return list(self._floor_tables)

        if "obj_description(n.oid, 'pg_namespace')" in query:
            return []  # no schema descriptions in this fixture

        if "information_schema.table_constraints" in query:
            return []  # no PK/FK for privileged schemas in this fixture

        if "FROM information_schema.tables t" in query:
            return []  # privileged schemas in this fixture have no tables

        if "c.relkind, c.reltuples" in query:
            # _catalog_table_summary(schema_name) -- one schema, from params
            schema_name = params[0] if params else None
            tables = self._catalog_tables_by_schema.get(schema_name, {})
            return [
                {"table_name": name, "relkind": info["relkind"], "reltuples": info["reltuples"]}
                for name, info in tables.items()
            ]

        if "FROM pg_attribute a" in query:
            schema_name = params[0] if params else None
            table_name = params[1] if len(params) > 1 else None
            return list(self._catalog_columns_by_schema_table.get((schema_name, table_name), []))

        if "SELECT current_user AS connected_as" in query:
            return [{"connected_as": "surveyor"}]

        if "pg_has_role(current_user, 'pg_monitor', 'MEMBER')" in query:
            return [{"has_role": False}]

        raise AssertionError(f"unexpected query in test fake: {query}")


class TestEnumerateRelationsIsTheSharedFloor:
    def test_returns_the_raw_floor_schemas_and_tables(self):
        conn = _FakeFloorConnection(
            privileged_schema_names=["public"],
            floor_schemas=[
                {"schema_name": "public", "usage_granted": True},
                {"schema_name": "demo", "usage_granted": False},
            ],
            floor_tables=[
                {"schema_name": "public", "table_name": "orders", "relkind": "r",
                 "can_select": True, "can_insert": True},
                {"schema_name": "demo", "table_name": "secrets", "relkind": "r",
                 "can_select": False, "can_insert": False},
            ],
            catalog_tables_by_schema={},
            catalog_columns_by_schema_table={},
        )
        schemas, tables = conn._enumerate_relations()
        assert [s["schema_name"] for s in schemas] == ["public", "demo"]
        assert [t["table_name"] for t in tables] == ["orders", "secrets"]

    def test_raises_rather_than_silently_defaulting(self):
        """The shared helper itself does not catch -- each CALLER decides
        what its own section should look like on failure, since "no rows"
        means something different to a probe than to an inventory."""
        import pytest

        class _BrokenConnection(PostgreSQLConnection):
            def __init__(self):
                super().__init__(host="localhost", port=5432, database="x", user="u", password="p")

            def execute_query(self, query, params=()):
                raise RuntimeError("connection reset")

        conn = _BrokenConnection()
        with pytest.raises(RuntimeError):
            conn._enumerate_relations()


class TestGetCredentialCapabilityUsesTheSharedFloor:
    def test_totals_come_from_enumerate_relations(self):
        conn = _FakeFloorConnection(
            privileged_schema_names=[],
            floor_schemas=[
                {"schema_name": "coco_ods", "usage_granted": True},
                {"schema_name": "demo", "usage_granted": False},
            ],
            floor_tables=[
                {"schema_name": "coco_ods", "table_name": "orders", "relkind": "r",
                 "can_select": True, "can_insert": False},
                {"schema_name": "demo", "table_name": "secrets", "relkind": "r",
                 "can_select": False, "can_insert": False},
            ],
            catalog_tables_by_schema={},
            catalog_columns_by_schema_table={},
        )
        result = conn.get_credential_capability()
        assert result["schema_total"] == 2
        assert result["schema_visible"] == 1
        assert result["table_total"] == 2
        assert result["table_select"] == 1
        assert "_errors" not in result

    def test_records_an_error_when_enumeration_fails(self):
        class _BrokenConnection(PostgreSQLConnection):
            def __init__(self):
                super().__init__(host="localhost", port=5432, database="x", user="u", password="p")

            def execute_query(self, query, params=()):
                if "SELECT current_user" in query:
                    return [{"connected_as": "surveyor"}]
                if "pg_monitor" in query:
                    return [{"has_role": False}]
                raise RuntimeError("connection reset mid-enumeration")

        conn = _BrokenConnection()
        result = conn.get_credential_capability()
        assert result["schema_total"] == 0
        assert result["table_total"] == 0
        assert "_errors" in result
        assert "enumeration" in result["_errors"]


class TestGetSchemaInfoFillsInZeroPrivilegeSchemas:
    """The exact coco_pharma regression: a schema with literally zero
    privilege never appears via information_schema.schemata, so it must be
    added from the floor pass or it silently vanishes from the inventory."""

    def test_a_zero_privilege_schema_appears_with_its_real_tables(self):
        conn = _FakeFloorConnection(
            privileged_schema_names=["coco_ods"],
            floor_schemas=[
                {"schema_name": "coco_ods", "usage_granted": True},
                {"schema_name": "demo", "usage_granted": False},
                {"schema_name": "demo_auth", "usage_granted": False},
            ],
            floor_tables=[
                {"schema_name": "coco_ods", "table_name": "orders", "relkind": "r",
                 "can_select": True, "can_insert": False},
                {"schema_name": "demo", "table_name": "secrets", "relkind": "r",
                 "can_select": False, "can_insert": False},
                {"schema_name": "demo_auth", "table_name": "users", "relkind": "r",
                 "can_select": False, "can_insert": False},
                {"schema_name": "demo_auth", "table_name": "sessions", "relkind": "v",
                 "can_select": False, "can_insert": False},
            ],
            catalog_tables_by_schema={
                "demo": {"secrets": {"relkind": "r", "reltuples": 12.0}},
                "demo_auth": {
                    "users": {"relkind": "r", "reltuples": 3.0},
                    "sessions": {"relkind": "v", "reltuples": None},
                },
            },
            catalog_columns_by_schema_table={
                ("demo", "secrets"): [
                    {"column_name": "id", "ordinal_position": 1, "data_type": "integer"},
                ],
                ("demo_auth", "users"): [
                    {"column_name": "id", "ordinal_position": 1, "data_type": "integer"},
                ],
                ("demo_auth", "sessions"): [],
            },
        )
        info = conn.get_schema_info()
        names = {s["name"]: s for s in info["schemas"]}
        assert set(names) == {"coco_ods", "demo", "demo_auth"}

        assert names["demo"]["access"] == "no_usage"
        assert [t["name"] for t in names["demo"]["tables"]] == ["secrets"]
        assert names["demo"]["tables"][0]["source"] == "catalog_fallback"
        assert names["demo"]["tables"][0]["type"] == "BASE TABLE"
        assert len(names["demo"]["tables"][0]["columns"]) == 1

        assert names["demo_auth"]["access"] == "no_usage"
        assert {t["name"] for t in names["demo_auth"]["tables"]} == {"users", "sessions"}

        assert "access" not in names["coco_ods"]

        # Totals now include the floor schemas' tables -- the exact
        # convergence the design ruling asked for.
        assert info["total_tables"] == 3  # orders + secrets + users + sessions... see below
        assert info["total_columns"] == 2  # 1 (secrets.id) + 1 (users.id) + 0 (sessions)

    def test_a_schema_visible_to_information_schema_is_never_duplicated(self):
        """A schema already found via the privileged list must not ALSO get
        a floor stub -- the floor pass only fills in what's missing."""
        conn = _FakeFloorConnection(
            privileged_schema_names=["coco_ods"],
            floor_schemas=[{"schema_name": "coco_ods", "usage_granted": True}],
            floor_tables=[
                {"schema_name": "coco_ods", "table_name": "orders", "relkind": "r",
                 "can_select": True, "can_insert": False},
            ],
            catalog_tables_by_schema={},
            catalog_columns_by_schema_table={},
        )
        info = conn.get_schema_info()
        assert len([s for s in info["schemas"] if s["name"] == "coco_ods"]) == 1

    def test_records_an_error_when_the_floor_pass_itself_fails(self):
        class _PartlyBrokenConnection(PostgreSQLConnection):
            def __init__(self):
                super().__init__(host="localhost", port=5432, database="x", user="u", password="p")

            def execute_query(self, query, params=()):
                if "FROM information_schema.schemata" in query:
                    return []
                if "obj_description(n.oid, 'pg_namespace')" in query:
                    return []
                raise RuntimeError("floor query failed")

        conn = _PartlyBrokenConnection()
        info = conn.get_schema_info()
        assert info["schemas"] == []
        assert "_errors" in info
        assert "enumeration_floor" in info["_errors"]

    def test_credential_capability_and_schema_info_agree_on_table_total(self):
        """The end-to-end convergence claim: with the same floor data, the
        probe's table_total and the inventory's total_tables must match."""
        floor_schemas = [
            {"schema_name": "coco_ods", "usage_granted": True},
            {"schema_name": "demo", "usage_granted": False},
        ]
        floor_tables = [
            {"schema_name": "coco_ods", "table_name": "orders", "relkind": "r",
             "can_select": True, "can_insert": False},
            {"schema_name": "demo", "table_name": "secrets", "relkind": "r",
             "can_select": False, "can_insert": False},
        ]
        conn = _FakeFloorConnection(
            privileged_schema_names=["coco_ods"],
            floor_schemas=floor_schemas,
            floor_tables=floor_tables,
            catalog_tables_by_schema={
                # coco_ods is privileged (USAGE granted) but this fixture's
                # information_schema.tables answers empty for every schema
                # (see the fake's blanket handler) -- the PRE-EXISTING
                # catalog-only fallback `_get_tables_for_schema` already
                # runs for every privileged schema is what recovers
                # "orders" here, exactly as it does live. This test is
                # about the NEW floor pass (demo) staying consistent with
                # that already-working mechanism, not re-testing it.
                "coco_ods": {"orders": {"relkind": "r", "reltuples": 5.0}},
                "demo": {"secrets": {"relkind": "r", "reltuples": 1.0}},
            },
            catalog_columns_by_schema_table={
                ("coco_ods", "orders"): [],
                ("demo", "secrets"): [],
            },
        )
        cap = conn.get_credential_capability()
        info = conn.get_schema_info()
        assert cap["table_total"] == info["total_tables"]
