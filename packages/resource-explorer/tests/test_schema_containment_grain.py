"""Schema as the aggregation grain of database analysis output.

REPLY-SCHEMA-AS-SUB-RESOURCE.md shape 1 (§1) with §5's per-engine correction,
replying to ASK-SCHEMA-AS-SUB-RESOURCE.md (#263). Shape 2 (rows in
`sub_resources`) is a separate, later piece and is NOT tested here.

Three properties every test below is really about:

1. **The grain is declared, never assumed.** `"schema"` is Postgres's word.
   An engine with no declaration must produce whole-database output LABELLED
   as undeclared, not an empty breakdown that reads as "no schemas".
2. **A rollup is labelled as a rollup and is never an average.** The set of
   kinds with counts, the list of signatures, the per-schema summaries plus
   cross-schema edges — each is a structure a reader can dispute, not one
   number.
3. **What one schema could not be read is said about that schema.** A schema
   with `USAGE` and no `SELECT` is "structure only" for itself, not a
   contribution to a database-wide fraction.
"""
from __future__ import annotations

import pytest

from resource_explorer.registry import (
    STATE_MEASURED,
    STATE_NOT_MEASURED,
    DatabaseEntity,
    ProjectRegistry,
)
from resource_explorer.surveyors.database import schema_scope
from resource_explorer.surveyors.database.connection import (
    NO_CONTAINMENT,
    POSTGRES_CONTAINMENT,
    STRUCTURAL_FLOOR_NONE,
    STRUCTURAL_FLOOR_ROLE_GRANT,
    STRUCTURAL_FLOOR_UNPRIVILEGED,
    DatabaseConnection,
    PostgreSQLConnection,
    containment_for_engine,
)
from resource_explorer.surveyors.database.db_derived import (
    load_inputs,
    run_db_derived,
    scope_inputs,
)
from resource_explorer.surveyors.database.survey_definition_adapter import (
    _credential_scope_status,
    _db_derived_field_reader,
)

NOW = "2026-09-24T12:00:00"


# ── fixtures ────────────────────────────────────────────────────────────────

@pytest.fixture
def registry(tmp_path):
    r = ProjectRegistry(db_path=str(tmp_path / "test.db"))
    r.register_database(DatabaseEntity(
        slug="coco_pharma", display_name="Coco Pharma", db_type="postgresql",
        host="localhost", port=5442, database_name="coco_pharma",
    ))
    return r


def _table(name, *, schema, rows=1000, ttype="BASE TABLE", desc=""):
    return {
        "schema_name": schema, "table_name": name, "table_type": ttype,
        "row_count": rows, "size_bytes": 8192, "description": desc,
        "state": STATE_MEASURED,
    }


def _column(table, name, *, schema, pk=False, fk=None, dtype="integer", desc=""):
    return {
        "schema_name": schema, "table_name": table, "column_name": name,
        "data_type": dtype, "base_type": dtype, "description": desc,
        "is_primary_key": 1 if pk else 0, "foreign_key_json": fk,
        "state": STATE_MEASURED,
    }


def _store(registry, slug, *, tables, columns, surveyed_at=NOW):
    registry.record_database_survey(
        slug=slug, schema_count=1, table_count=len(tables),
        column_count=len(columns), survey_data={}, surveyed_at=surveyed_at,
    )
    registry.write_detail_rows("database_tables", slug, surveyed_at, rows=tables)
    registry.write_detail_rows("database_columns", slug, surveyed_at, rows=columns)


def _coco_pharma_rows():
    """The ask's own live shape: one real transactional schema with FK
    structure, three single-table regional marts, and `public` empty of tables.
    """
    tables = [
        _table("customer", schema="coco_ods", rows=50_000),
        _table("orders", schema="coco_ods", rows=400_000),
        _table("order_line", schema="coco_ods", rows=2_000_000),
        _table("eu_sales_fact", schema="eu_sales", rows=900),
        _table("us_sales_fact", schema="us_sales", rows=900),
    ]
    columns = [
        _column("customer", "customer_id", schema="coco_ods", pk=True),
        _column("customer", "email", schema="coco_ods", dtype="text"),
        _column("orders", "order_id", schema="coco_ods", pk=True),
        _column("orders", "customer_id", schema="coco_ods",
                fk={"foreign_schema": "coco_ods", "foreign_table": "customer",
                    "foreign_column": "customer_id"}),
        _column("order_line", "order_line_id", schema="coco_ods", pk=True),
        _column("order_line", "order_id", schema="coco_ods",
                fk={"foreign_schema": "coco_ods", "foreign_table": "orders",
                    "foreign_column": "order_id"}),
        _column("eu_sales_fact", "eu_sales_fact_id", schema="eu_sales", pk=True),
        _column("eu_sales_fact", "amount", schema="eu_sales", dtype="numeric"),
        _column("us_sales_fact", "us_sales_fact_id", schema="us_sales", pk=True),
        _column("us_sales_fact", "amount", schema="us_sales", dtype="numeric"),
    ]
    return tables, columns


def _derived(registry, slug="coco_pharma"):
    return run_db_derived(registry, slug)["derived"]


# ── 1. the declaration (REPLY §5) ───────────────────────────────────────────

class TestPostgresContainmentDeclaration:
    def test_levels_are_ordered_outermost_first(self):
        assert [level.name for level in POSTGRES_CONTAINMENT.levels] == [
            "database", "schema",
        ]
        assert POSTGRES_CONTAINMENT.engine == "postgresql"

    def test_the_grain_is_derived_as_the_innermost_namespace_level(self):
        """§5 point 2: "the lowest `namespace` level above table"."""
        grain = POSTGRES_CONTAINMENT.aggregation_grain
        assert grain is not None
        assert grain.name == "schema"

    def test_a_postgres_database_is_not_a_namespace(self):
        """The reason the grain is `schema` and not `database`: one Postgres
        connection cannot qualify a table with the database name. If this flag
        ever flips, the grain silently moves, so it is pinned."""
        database = POSTGRES_CONTAINMENT.level("database")
        assert database.namespace is False
        assert database.physical_unit is True
        assert database.security_boundary is True

    def test_schema_semantics_match_the_reply_table(self):
        """§5, row 1: "a namespace with its own privilege (USAGE); `public`
        default; extensions and `pg_toast` own schemas". `owner` False is what
        distinguishes it from Oracle, where the same level IS a user."""
        schema = POSTGRES_CONTAINMENT.level("schema")
        assert schema.namespace is True
        assert schema.security_boundary is True
        assert schema.owner is False
        assert schema.physical_unit is False
        assert schema.default_container == "public"
        assert schema.egeria_technology_type

    def test_system_containers_include_the_toast_prefix(self):
        assert POSTGRES_CONTAINMENT.is_system_container("pg_catalog")
        assert POSTGRES_CONTAINMENT.is_system_container("information_schema")
        assert POSTGRES_CONTAINMENT.is_system_container("pg_toast")
        assert POSTGRES_CONTAINMENT.is_system_container("pg_toast_temp_1")

    def test_public_is_an_ordinary_container(self):
        """§1: "treat `public` as a schema like any other". Declaring it the
        default must not make it excludable."""
        assert not POSTGRES_CONTAINMENT.is_system_container("public")

    def test_an_undeclared_engine_gets_nothing_not_postgres(self):
        for engine in ("mysql", "oracle", "duckdb", "", None):
            assert containment_for_engine(engine) is NO_CONTAINMENT
        assert NO_CONTAINMENT.aggregation_grain is None
        assert NO_CONTAINMENT.declared is False

    def test_engine_spellings_resolve(self):
        for engine in ("postgresql", "postgres", "PostgreSQL", " Postgres "):
            assert containment_for_engine(engine) is POSTGRES_CONTAINMENT

    def test_the_connection_layer_declares_it(self):
        """Same shape as `EngineCapabilities`: declared on the connection, with
        an honest default for an engine nobody has taught this codebase."""
        conn = PostgreSQLConnection("h", 5432, "d", "u", "p")
        assert conn.containment is POSTGRES_CONTAINMENT

        class _Unknown(DatabaseConnection):
            def connect(self): ...
            def execute_query(self, query, params=()): return []
            def get_schema_info(self): return {}
            def get_statistics(self): return {}
            def close(self): ...

        assert _Unknown().containment is NO_CONTAINMENT

    def test_as_dict_names_the_derived_grain(self):
        payload = POSTGRES_CONTAINMENT.as_dict()
        assert payload["aggregation_grain"] == "schema"
        assert [lvl["name"] for lvl in payload["levels"]] == ["database", "schema"]
        assert payload["structural_floor"] == STRUCTURAL_FLOOR_UNPRIVILEGED

    def test_the_structural_floor_is_declared_not_assumed(self):
        """Postgres's "structure is visible without SELECT" property is what
        `#257`'s catalog fallback and the `structure_only` credential state
        both rest on, and it does NOT generalize — Oracle/SQL Server need a
        role grant for it, MySQL has no floor at all. Declared per engine so a
        future declaration says which, rather than inheriting this one."""
        assert POSTGRES_CONTAINMENT.structural_floor == STRUCTURAL_FLOOR_UNPRIVILEGED
        assert STRUCTURAL_FLOOR_ROLE_GRANT != STRUCTURAL_FLOOR_NONE

    def test_an_undeclared_engine_claims_no_floor_either_way(self):
        """Empty, not `none`: nobody has looked at that engine's catalog. A
        `none` here would be a measured claim this codebase has not made."""
        assert NO_CONTAINMENT.structural_floor == ""


# ── 2. the WHERE clause the reply promised ──────────────────────────────────

class TestTheSchemaColumnIsAlreadyOnEveryRow:
    def test_scoping_is_a_filter_over_stored_rows(self, registry):
        """REPLY §1 claims the structured tables "carry `schema` on every row,
        so a schema scope is a WHERE clause, not new plumbing". The column is
        named `schema_name`, and the claim otherwise holds."""
        tables, columns = _coco_pharma_rows()
        _store(registry, "coco_pharma", tables=tables, columns=columns)
        inputs = load_inputs(registry, "coco_pharma")
        assert all(schema_scope.CONTAINER_COLUMN in row for row in inputs.tables)
        assert all(schema_scope.CONTAINER_COLUMN in row for row in inputs.columns)

        scoped = scope_inputs(inputs, "coco_ods")
        assert {t["table_name"] for t in scoped.tables} == {
            "customer", "orders", "order_line",
        }
        assert all(c["schema_name"] == "coco_ods" for c in scoped.columns)


# ── 3. per-schema output for each analysis (REPLY §1's table) ───────────────

class TestClassificationIsPerSchemaWithASetRollup:
    def test_each_schema_gets_its_own_verdict(self, registry):
        tables, columns = _coco_pharma_rows()
        _store(registry, "coco_pharma", tables=tables, columns=columns)
        result = _derived(registry)["db_classification"]

        assert set(result["by_schema"]) == {"coco_ods", "eu_sales", "us_sales"}
        for name, payload in result["by_schema"].items():
            assert "kind" in payload and "scores" in payload, name

    def test_the_rollup_is_a_set_of_kinds_with_counts_not_one_verdict(self, registry):
        tables, columns = _coco_pharma_rows()
        _store(registry, "coco_pharma", tables=tables, columns=columns)
        agg = _derived(registry)["db_classification"]["aggregation"]

        assert agg["is_rollup"] is True
        assert agg["averaged"] is False
        assert agg["rollup_kind"] == "set_of_kinds"
        assert agg["grain"] == "schema"
        assert isinstance(agg["kinds"], dict)
        # Every classified schema is counted; the total across kinds plus the
        # undecided and unmeasured lists accounts for every schema, so nothing
        # is silently dropped into an average.
        accounted = (
            sum(agg["kinds"].values())
            + len(agg["undecided_containers"])
            + len(agg["not_measured_containers"])
        )
        assert accounted == agg["container_count"] == 3
        assert "rollup" in agg["explanation"].lower()


class TestRelationshipGraphIsPerSchemaPlusCrossSchemaEdges:
    def _cross_schema_rows(self):
        tables = [
            _table("customer", schema="core"),
            _table("orders", schema="sales"),
        ]
        columns = [
            _column("customer", "customer_id", schema="core", pk=True),
            _column("orders", "order_id", schema="sales", pk=True),
            _column("orders", "customer_id", schema="sales",
                    fk={"foreign_schema": "core", "foreign_table": "customer",
                        "foreign_column": "customer_id"}),
        ]
        return tables, columns

    def test_each_schema_reports_its_own_components(self, registry):
        tables, columns = _coco_pharma_rows()
        _store(registry, "coco_pharma", tables=tables, columns=columns)
        by_schema = _derived(registry)["db_relationship_graph"]["by_schema"]

        assert by_schema["coco_ods"]["verdict"] == "data_model"
        assert by_schema["coco_ods"]["edge_count"] == 2
        # The ask's own screenshot number, now said about the right subject:
        # a single-table mart genuinely has no model, and that is a statement
        # about `eu_sales`, not about the database.
        assert by_schema["eu_sales"]["verdict"] == "bag_of_tables"
        assert by_schema["eu_sales"]["table_count"] == 1

    def test_cross_schema_edges_are_a_database_level_fact(self, registry):
        """§1: "cross-schema FK edges reported as a database-level fact in
        their own right" — not folded into either schema's count."""
        tables, columns = self._cross_schema_rows()
        _store(registry, "coco_pharma", tables=tables, columns=columns)
        result = _derived(registry)["db_relationship_graph"]
        agg = result["aggregation"]

        assert agg["cross_container_edge_count"] == 1
        assert agg["cross_container_pairs"] == {"sales → core": 1}
        # Counted in NEITHER schema.
        assert result["by_schema"]["sales"]["edge_count"] == 0
        assert result["by_schema"]["core"]["edge_count"] == 0

    def test_a_cross_schema_reference_is_not_reported_as_dangling(self, registry):
        """Within a one-schema scope the target table is absent, which the
        unscoped check calls a dangling reference. It is not dangling — it
        resolves in another schema of the same database."""
        tables, columns = self._cross_schema_rows()
        _store(registry, "coco_pharma", tables=tables, columns=columns)
        sales = _derived(registry)["db_relationship_graph"]["by_schema"]["sales"]

        assert sales["dangling_references"] == []
        assert len(sales["cross_container_references"]) == 1
        assert sales["cross_container_references"][0]["to_schema"] == "core"

    def test_no_cross_schema_edge_is_a_measured_finding(self, registry):
        tables, columns = _coco_pharma_rows()
        _store(registry, "coco_pharma", tables=tables, columns=columns)
        agg = _derived(registry)["db_relationship_graph"]["aggregation"]

        assert agg["cross_container_edge_count"] == 0
        assert "No foreign key crosses" in agg["explanation"]


class TestFingerprintIsPerSchemaAndSurfacesCopies:
    def _two_matching_schemas(self):
        tables, columns = [], []
        for schema in ("coco_ods", "coco_ods_bak"):
            tables += [
                _table("customer", schema=schema),
                _table("orders", schema=schema),
            ]
            columns += [
                _column("customer", "customer_id", schema=schema, pk=True),
                _column("customer", "email", schema=schema, dtype="text"),
                _column("orders", "order_id", schema=schema, pk=True),
                _column("orders", "total", schema=schema, dtype="numeric"),
            ]
        return tables, columns

    def test_the_rollup_is_a_list_of_signatures_not_one_digest(self, registry):
        tables, columns = _coco_pharma_rows()
        _store(registry, "coco_pharma", tables=tables, columns=columns)
        agg = _derived(registry)["db_fingerprint"]["aggregation"]

        assert agg["rollup_kind"] == "list_of_signatures"
        assert agg["averaged"] is False
        assert [s["container"] for s in agg["signatures"]] == [
            "coco_ods", "eu_sales", "us_sales",
        ]
        assert len({s["digest"] for s in agg["signatures"]}) == 3

    def test_two_schemas_with_matching_signatures_surface(self, registry):
        """The buried finding §1 names. The whole-database fingerprint cannot
        see this at all: it hashes one signature for the entire database."""
        tables, columns = self._two_matching_schemas()
        _store(registry, "coco_pharma", tables=tables, columns=columns)
        result = _derived(registry)["db_fingerprint"]

        cross = result["aggregation"]["cross_container_matches"]
        assert cross, "a schema that is a copy of another schema must surface"
        assert {m["container"] for m in cross} == {"coco_ods", "coco_ods_bak"}
        assert all(m["verdict"] == "likely_copy" for m in cross)
        assert all(m["same_database"] is True for m in cross)
        assert result["by_schema"]["coco_ods"]["best_similarity"] == 1.0

    def test_a_schema_matching_another_databases_schema_surfaces(self, registry):
        """The `coco_pharma.coco_ods` vs the `coco_ods` DATABASE case."""
        registry.register_database(DatabaseEntity(
            slug="coco_ods", display_name="Coco ODS", db_type="postgresql",
            host="localhost", port=5442, database_name="coco_ods",
        ))
        tables, columns = [], []
        for slug, schema in (("coco_pharma", "coco_ods"), ("coco_ods", "public")):
            _store(
                registry, slug,
                tables=[_table("customer", schema=schema),
                        _table("orders", schema=schema)],
                columns=[
                    _column("customer", "customer_id", schema=schema, pk=True),
                    _column("orders", "order_id", schema=schema, pk=True),
                    _column("orders", "total", schema=schema, dtype="numeric"),
                ],
            )
        cross = _derived(registry)["db_fingerprint"]["aggregation"][
            "cross_container_matches"
        ]
        assert [m["target_slug"] for m in cross] == ["coco_ods"]
        assert cross[0]["target_container"] == "public"
        assert cross[0]["same_database"] is False
        assert cross[0]["verdict"] == "likely_copy"

    def test_unrelated_schemas_do_not_match(self, registry):
        tables, columns = _coco_pharma_rows()
        _store(registry, "coco_pharma", tables=tables, columns=columns)
        assert _derived(registry)["db_fingerprint"]["aggregation"][
            "cross_container_matches"
        ] == []


class TestConventionsArePerSchemaWithTheSpreadNamed:
    def test_each_schema_has_its_own_checks_and_the_rollup_names_the_spread(
        self, registry,
    ):
        tables, columns = _coco_pharma_rows()
        # One table in one schema loses its primary key, so the gap belongs to
        # a named schema rather than to a database-wide "1 of 5".
        columns = [
            c for c in columns
            if not (c["schema_name"] == "eu_sales" and c["is_primary_key"])
        ]
        _store(registry, "coco_pharma", tables=tables, columns=columns)
        result = _derived(registry)["schema_conventions"]

        assert set(result["by_schema"]) == {"coco_ods", "eu_sales", "us_sales"}
        eu = result["by_schema"]["eu_sales"]["checks"]["tables_without_primary_key"]
        assert eu["count"] == 1 and eu["total"] == 1
        ods = result["by_schema"]["coco_ods"]["checks"]["tables_without_primary_key"]
        assert ods["count"] == 0

        agg = result["aggregation"]
        assert agg["rollup_kind"] == "totals_plus_spread"
        spread = {e["container"]: e for e in agg["spread"]["tables_without_primary_key"]}
        assert spread["eu_sales"]["count"] == 1
        assert "eu_sales" in agg["explanation"]

    def test_the_whole_database_totals_are_left_intact(self, registry):
        """§1's rollup for this row is "totals, with the per-schema spread" —
        the totals are the existing block, unchanged."""
        tables, columns = _coco_pharma_rows()
        _store(registry, "coco_pharma", tables=tables, columns=columns)
        checks = _derived(registry)["schema_conventions"]["checks"]
        assert checks["tables_without_primary_key"]["total"] == 5


class TestGrainDeterminationIsLeftAlone:
    def test_it_is_already_per_table_and_says_so(self, registry):
        """§1: "already per table; nothing to do". Confirmed rather than
        assumed — and marked, so the confirmation is visible."""
        tables, columns = _coco_pharma_rows()
        _store(registry, "coco_pharma", tables=tables, columns=columns)
        grain = _derived(registry)["grain_determination"]

        assert "by_schema" not in grain
        assert grain["aggregation"]["is_rollup"] is False
        assert grain["aggregation"]["grain"] == "table"
        assert {g["schema_name"] for g in grain["grains"]} == {
            "coco_ods", "eu_sales", "us_sales",
        }


# ── 4. system containers, and the undeclared engine ─────────────────────────

class TestSystemContainersAreExcluded:
    def test_pg_catalog_rows_are_left_out_and_named(self, registry):
        tables, columns = _coco_pharma_rows()
        tables.append(_table("pg_class", schema="pg_catalog"))
        columns.append(_column("pg_class", "oid", schema="pg_catalog"))
        tables.append(_table("t", schema="pg_toast_temp_1"))
        _store(registry, "coco_pharma", tables=tables, columns=columns)
        result = _derived(registry)["db_classification"]

        assert "pg_catalog" not in result["by_schema"]
        assert "pg_toast_temp_1" not in result["by_schema"]
        # Excluded, and said so — a silent drop would make a changed table
        # count unexplainable.
        assert set(result["aggregation"]["excluded_system_containers"]) == {
            "pg_catalog", "pg_toast_temp_1",
        }

    def test_exclusion_comes_from_the_declaration_not_a_local_list(self, registry):
        """The same names must not be re-hardcoded per call site: a level that
        declares no system containers excludes none."""
        from dataclasses import replace

        level = replace(
            POSTGRES_CONTAINMENT.level("schema"),
            system_containers=(), system_container_prefixes=(),
        )
        from resource_explorer.surveyors.database.connection import EngineContainment

        containment = EngineContainment(engine="postgresql", levels=(level,))
        assert not containment.is_system_container("pg_catalog")
        assert schema_scope.containers_in_rows(
            containment, [{"schema_name": "pg_catalog"}],
        ) == ["pg_catalog"]


class TestAnUndeclaredEngineIsAnAbsenceNotAFlatNamespace:
    def test_it_says_the_hierarchy_is_undeclared(self, registry):
        registry.register_database(DatabaseEntity(
            slug="mystery", display_name="Mystery", db_type="mysql",
            host="localhost", port=3306, database_name="mystery",
        ))
        _store(
            registry, "mystery",
            tables=[_table("t", schema="mystery")],
            columns=[_column("t", "id", schema="mystery", pk=True)],
        )
        derived = run_db_derived(registry, "mystery")["derived"]
        for key in ("db_classification", "db_relationship_graph",
                    "db_fingerprint", "schema_conventions"):
            agg = derived[key]["aggregation"]
            assert agg["grain"] is None, key
            assert agg["reason"] == schema_scope.REASON_UNDECLARED, key
            assert "by_schema" not in derived[key], key
            # Lower-case "not", to match house voice (REPLY-COPY-REVIEW-
            # CREDENTIAL-AND-FIT-LANGUAGE.md §6(a)) -- the emphasis is real
            # without shouting it.
            assert "not a finding" in agg["explanation"], key


# ── 5. credential capability, per schema (REPLY §2) ─────────────────────────

_CAP_COCO_PHARMA = {
    "connected_as": "egeria_user",
    "schema_total": 4, "schema_visible": 3,
    "relation_total": 26, "relation_select": 3,
    "by_schema": {
        # USAGE, no SELECT — the incident. "Structure only", for this schema.
        "coco_ods": {"usage_granted": True, "table_total": 23, "table_select": 0},
        "eu_sales": {"usage_granted": True, "table_total": 1, "table_select": 1},
        "us_sales": {"usage_granted": True, "table_total": 1, "table_select": 1},
        "demo": {"usage_granted": False, "table_total": 1, "table_select": 0},
    },
    "stats_role": False, "write_probed": True, "write_capable": False,
}


class TestPerSchemaCredentialScope:
    def test_each_schema_gets_its_own_state(self):
        states = schema_scope.container_scope_states(_CAP_COCO_PHARMA)
        assert states["coco_ods"]["state"] == schema_scope.SCOPE_STRUCTURE_ONLY
        assert states["eu_sales"]["state"] == schema_scope.SCOPE_READABLE
        assert states["demo"]["state"] == schema_scope.SCOPE_NOT_VISIBLE

    def test_partial_and_empty_are_distinct_states(self):
        states = schema_scope.container_scope_states({"by_schema": {
            "part": {"usage_granted": True, "table_total": 4, "table_select": 2},
            "bare": {"usage_granted": True, "table_total": 0, "table_select": 0},
        }})
        assert states["part"]["state"] == schema_scope.SCOPE_PARTIALLY_READABLE
        assert states["bare"]["state"] == schema_scope.SCOPE_EMPTY

    def test_no_probe_says_nothing_rather_than_everything_is_readable(self):
        assert schema_scope.container_scope_states(None) == {}
        assert schema_scope.credential_shortfall(None) is None

    def test_full_coverage_stays_silent(self):
        assert schema_scope.credential_shortfall({
            "relation_total": 2, "relation_select": 2,
            "by_schema": {"public": {"usage_granted": True, "table_total": 2,
                                     "table_select": 2}},
        }, POSTGRES_CONTAINMENT) is None

    def test_the_shortfall_phrase_counts_schemas_before_tables(self):
        """§2's worked example: "3 of 8 schemas readable; 3 of 26 tables"
        instead of "3 tables"."""
        shortfall = schema_scope.credential_shortfall(
            _CAP_COCO_PHARMA, POSTGRES_CONTAINMENT,
        )
        assert shortfall["phrase"] == "2 of 4 schemas readable (3 visible); 3 of 26 relation(s)"
        # Worst first: no visibility, then structure-only.
        assert shortfall["short_containers"] == ["demo", "coco_ods"]

    def test_the_level_name_comes_from_the_engine(self):
        """Never the literal "schema": the same probe against Oracle would be
        reporting on owners."""
        generic = schema_scope.credential_shortfall(_CAP_COCO_PHARMA)
        assert "containers readable" in generic["phrase"]

    def test_the_fact_envelope_carries_the_per_schema_breakdown(self, registry):
        registry.record_database_survey(
            slug="coco_pharma", schema_count=4, table_count=3, column_count=0,
            survey_data={"credential_capability": _CAP_COCO_PHARMA},
        )
        registry.write_detail_rows(
            "database_tables", "coco_pharma", NOW,
            rows=[_table("t", schema="eu_sales")],
        )
        status = _credential_scope_status(registry, "coco_pharma")
        assert status is not None
        # The existing database-wide fraction is untouched...
        assert "3 of 26" in status["fraction"]
        # ...and the per-schema reading is what §2 adds.
        assert status["schema_fraction"] == "2 of 4 schemas readable (3 visible); 3 of 26 relation(s)"
        assert status["by_container"]["coco_ods"]["state"] == (
            schema_scope.SCOPE_STRUCTURE_ONLY
        )

    def test_a_structure_only_schema_is_marked_on_its_own_results(self, registry):
        """The point of the whole §2 slice: `coco_ods`'s findings must not
        render like a schema that was fully read."""
        tables, columns = _coco_pharma_rows()
        _store(registry, "coco_pharma", tables=tables, columns=columns)
        registry.record_database_survey(
            slug="coco_pharma", schema_count=4, table_count=5, column_count=10,
            survey_data={"credential_capability": _CAP_COCO_PHARMA},
        )
        value = _db_derived_field_reader("db_classification")(registry, "coco_pharma")

        assert value["by_schema"]["coco_ods"]["_status"]["container_state"] == (
            schema_scope.SCOPE_STRUCTURE_ONLY
        )
        # A fully readable schema is not caveated.
        assert "_status" not in value["by_schema"]["eu_sales"]


class TestTheCapabilityShortfallMessageNamesSchemas:
    def test_the_rfa_lists_which_schemas_are_short(self):
        from resource_explorer.surveyors.database.database_surveyor import (
            DatabaseSurveyor,
        )

        surveyor = DatabaseSurveyor(
            DatabaseEntity(
                slug="coco_pharma", display_name="Coco Pharma",
                db_type="postgresql", host="localhost", port=5442,
                database_name="coco_pharma",
            ),
            {"user": "u", "password": "p"}, None,
        )
        annotations = surveyor._create_credential_capability_annotations(
            _CAP_COCO_PHARMA,
        )
        rfa = [a for a in annotations if hasattr(a, "action_requested")]
        assert rfa, "thin coverage must still raise the RFA"
        assert rfa[0].summary == (
            "connected as egeria_user: 2 of 4 schemas readable (3 visible); 3 of 26 relation(s)"
        )
        assert "coco_ods (structure_only)" in rfa[0].explanation
        assert "demo (not_visible)" in rfa[0].explanation

    def test_the_probe_not_supported_case_is_unchanged(self):
        from resource_explorer.surveyors.database.database_surveyor import (
            DatabaseSurveyor,
        )

        surveyor = object.__new__(DatabaseSurveyor)
        annotations = surveyor._create_credential_capability_annotations(None)
        assert len(annotations) == 1
        assert annotations[0].confidence == 0


# ── 6. the rollup is never a silent average ─────────────────────────────────

class TestNoRollupIsASilentAverage:
    def test_every_structural_rollup_declares_itself(self, registry):
        tables, columns = _coco_pharma_rows()
        _store(registry, "coco_pharma", tables=tables, columns=columns)
        derived = _derived(registry)
        for key in ("db_classification", "db_relationship_graph",
                    "db_fingerprint", "schema_conventions"):
            agg = derived[key]["aggregation"]
            assert agg["is_rollup"] is True, key
            assert agg["averaged"] is False, key
            assert agg["grain"] == "schema", key
            assert agg["explanation"], key

    def test_the_spread_reaches_the_sentence_the_screen_relays(self, registry):
        """`next/app.js`'s `readEnvelope` relays `explanation` and skips nested
        objects, so a breakdown living only in `by_schema` would never reach
        the reader who raised this. The rollup sentence is appended to the
        whole-database one."""
        tables, columns = _coco_pharma_rows()
        _store(registry, "coco_pharma", tables=tables, columns=columns)
        derived = _derived(registry)
        for key in ("db_classification", "db_relationship_graph",
                    "db_fingerprint", "schema_conventions"):
            payload = derived[key]
            assert payload["aggregation"]["explanation"] in payload["explanation"], key
        assert "schema" in derived["db_relationship_graph"]["explanation"]

    def test_an_empty_database_does_not_claim_a_breakdown(self, registry):
        """No stored rows at all: the containers list is empty and the checks
        stay `not_measured`. An empty `by_schema` here is honest only because
        the whole-database payloads already say nothing was measured."""
        derived = _derived(registry)
        assert derived["db_classification"]["state"] == STATE_NOT_MEASURED
        assert derived["db_classification"]["by_schema"] == {}
        assert derived["db_classification"]["aggregation"]["container_count"] == 0


class TestVisibleVersusReadableAreLabelled:
    """Walk 2026-10-12, coco_pharma: the header said "sees 6 of 8 schemas" (USAGE) and the panel "4 of 8 schemas
    readable" (every table SELECTable). Both true, counting different things; the phrase now says both."""

    def test_the_phrase_names_visible_when_it_differs_from_readable(self):
        by_schema = {f"ok{i}": {"usage_granted": True, "table_total": 2, "table_select": 2} for i in range(4)}
        by_schema["partial"] = {"usage_granted": True, "table_total": 5, "table_select": 1}
        by_schema["structure"] = {"usage_granted": True, "table_total": 5, "table_select": 0}
        by_schema["blind1"] = {"usage_granted": False, "table_total": 3, "table_select": 0}
        by_schema["blind2"] = {"usage_granted": False, "table_total": 3, "table_select": 0}
        s = schema_scope.credential_shortfall(
            {"relation_total": 61, "relation_select": 9, "by_schema": by_schema}, POSTGRES_CONTAINMENT)
        assert s["containers_visible"] == 6 and s["containers_readable"] == 4
        assert s["phrase"] == "4 of 8 schemas readable (6 visible); 9 of 61 relation(s)"

    def test_no_visible_clause_when_the_two_agree(self):
        by_schema = {"a": {"usage_granted": True, "table_total": 2, "table_select": 2},
                     "b": {"usage_granted": False, "table_total": 2, "table_select": 0}}
        s = schema_scope.credential_shortfall(
            {"relation_total": 4, "relation_select": 2, "by_schema": by_schema}, POSTGRES_CONTAINMENT)
        assert s["phrase"] == "1 of 2 schemas readable; 2 of 4 relation(s)"
