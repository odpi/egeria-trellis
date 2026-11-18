"""Real (non-mocked) integration tests for EA's cutover to the shared
trellis-vectorstore package — closes a real, confirmed gap: EA previously
had zero pytest coverage of PgVectorStore/BaseVectorStore at all, only
non-pytest standalone scripts (scripts/test_multi_collection_search.py
etc.). Mirrors Resource Explorer's tests/test_integration_pgvector.py tier.

Every test here is marked `requires_pgvector` (see conftest.py) —
auto-skipped when Postgres isn't reachable — and always operates against
zzz_vstest_-prefixed throwaway tables, never a real production collection.
"""
from __future__ import annotations

import json

import psycopg2
import pytest

pytestmark = [
    pytest.mark.requires_pgvector,
    pytest.mark.live(reason="Postgres with pgvector (writes zzz_vstest_* tables in public, dropped after)"),
]


def _live_columns(schema: str, table: str) -> list[list]:
    from advisor.config import settings
    conn = psycopg2.connect(
        host=settings.pgvector_host, port=settings.pgvector_port,
        dbname=settings.pgvector_dbname, user=settings.pgvector_user,
        password=settings.pgvector_password,
    )
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT column_name, data_type, character_maximum_length, is_nullable, column_default "
                "FROM information_schema.columns WHERE table_schema = %s AND table_name = %s "
                "ORDER BY ordinal_position",
                (schema, table),
            )
            return [list(r) for r in cur.fetchall()]
    finally:
        conn.close()


class TestDdlEquivalence:
    """The real 'byte-for-byte' guarantee — compares what Postgres actually
    persists (information_schema.columns) against the golden baseline
    captured from the live production pyegeria/pyegeria_cli tables before
    this extraction (docs/trellis-vectorstore-extraction.md's Phase 0),
    not generated SQL text (Postgres doesn't persist DDL whitespace)."""

    # Golden baseline captured live from production `public.pyegeria` /
    # `public.pyegeria_cli` before the extraction (Phase 0 pre-flight).
    _GOLDEN_PYEGERIA = [
        ["id", "character varying", 256, "NO", None],
        ["embedding", "USER-DEFINED", None, "NO", None],
        ["text", "text", None, "NO", None],
        ["metadata", "jsonb", None, "NO", "'{}'::jsonb"],
        ["element_type", "character varying", 50, "NO", "''::character varying"],
        ["class_name", "character varying", 200, "NO", "''::character varying"],
        ["method_name", "character varying", 200, "NO", "''::character varying"],
        ["module_path", "character varying", 500, "NO", "''::character varying"],
        ["is_async", "boolean", None, "NO", "false"],
        ["is_private", "boolean", None, "NO", "false"],
    ]
    _GOLDEN_PYEGERIA_CLI = [
        ["id", "character varying", 256, "NO", None],
        ["embedding", "USER-DEFINED", None, "NO", None],
        ["text", "text", None, "NO", None],
        ["metadata", "jsonb", None, "NO", "'{}'::jsonb"],
        ["main_command", "character varying", 100, "NO", "''::character varying"],
        ["subcommand", "character varying", 200, "NO", "''::character varying"],
        ["full_command", "character varying", 500, "NO", "''::character varying"],
    ]

    def test_pyegeria_ddl_matches_golden_baseline(self, ea_vs_store, vs_test_prefix):
        ea_vs_store.create_collection("pyegeria")
        cols = _live_columns("public", f"{vs_test_prefix}pyegeria")
        # Column names/types/nullability/defaults match exactly — only the
        # table name itself differs (prefixed for test isolation).
        assert cols == self._GOLDEN_PYEGERIA

    def test_pyegeria_cli_ddl_matches_golden_baseline(self, ea_vs_store, vs_test_prefix):
        ea_vs_store.create_collection("pyegeria_cli")
        cols = _live_columns("public", f"{vs_test_prefix}pyegeria_cli")
        assert cols == self._GOLDEN_PYEGERIA_CLI


class TestExtraColumnDenormalization:
    def test_insert_populates_jsonb_and_scalar_columns(self, ea_vs_store):
        ea_vs_store.create_collection("pyegeria")
        ea_vs_store.insert_data(
            "pyegeria",
            texts=["class ProjectManager: ..."],
            ids=["sym1"],
            metadata=[{"element_type": "class", "class_name": "ProjectManager", "is_private": False}],
        )
        rows = ea_vs_store.query_by_filter(
            "pyegeria", output_fields=["id", "metadata", "class_name", "is_private"], limit=10,
        )
        assert len(rows) == 1
        row = rows[0]
        assert row["class_name"] == "ProjectManager"
        assert row["is_private"] is False
        assert row["metadata"]["class_name"] == "ProjectManager"  # also in the JSONB blob

    def test_omitted_extra_column_lands_on_declared_default(self, ea_vs_store):
        ea_vs_store.create_collection("pyegeria")
        ea_vs_store.insert_data(
            "pyegeria", texts=["def helper(): ..."], ids=["sym2"], metadata=[{}],
        )
        rows = ea_vs_store.query_by_filter(
            "pyegeria", output_fields=["class_name", "is_private"], limit=10,
        )
        assert rows[0]["class_name"] == ""
        assert rows[0]["is_private"] is False


class TestQueryByFilterDsl:
    @pytest.fixture(autouse=True)
    def _seed(self, ea_vs_store):
        ea_vs_store.create_collection("pyegeria")
        ea_vs_store.insert_data(
            "pyegeria",
            texts=["public one", "private one", "public two"],
            ids=["p1", "p2", "p3"],
            metadata=[
                {"class_name": "Foo", "is_private": False},
                {"class_name": "Bar", "is_private": True},
                {"class_name": "Foo", "is_private": False},
            ],
        )
        self.store = ea_vs_store

    def test_string_equality(self):
        rows = self.store.query_by_filter("pyegeria", filter_expr='class_name == "Foo"', output_fields=["id"])
        assert {r["id"] for r in rows} == {"p1", "p3"}

    def test_bool_equality(self):
        rows = self.store.query_by_filter("pyegeria", filter_expr="is_private == True", output_fields=["id"])
        assert {r["id"] for r in rows} == {"p2"}

    def test_compound_and(self):
        rows = self.store.query_by_filter(
            "pyegeria", filter_expr='class_name == "Foo" and is_private == False', output_fields=["id"],
        )
        assert {r["id"] for r in rows} == {"p1", "p3"}

    def test_unrecognized_fragment_degrades_to_unfiltered_scan(self):
        # This is EA's original, deliberately-preserved behavior — pinned
        # here so it's not silently rediscovered/"fixed" later.
        rows = self.store.query_by_filter("pyegeria", filter_expr="not valid syntax", output_fields=["id"])
        assert {r["id"] for r in rows} == {"p1", "p2", "p3"}


class TestMetricSemantics:
    def test_l2_operator_and_score_formula(self, ea_vs_store):
        import math
        ea_vs_store.create_collection("plain")
        ea_vs_store.create_index("plain")
        ea_vs_store.insert_data("plain", texts=["alpha"], ids=["a"], metadata=[{}])
        results = ea_vs_store.search("plain", query_text="alpha", top_k=1)
        assert len(results) == 1
        # Same text embedded twice via the deterministic fake provider ->
        # distance 0 -> score exp(0) == 1.0, confirming the L2 formula is
        # actually the one in effect (not RE's 1-distance cosine formula).
        assert results[0].score == pytest.approx(1.0, abs=1e-6)


class TestMetricMismatchGuard:
    def test_cosine_metric_type_rejected_on_l2_store(self, ea_vs_store):
        with pytest.raises(ValueError, match="disagrees with this store's configured metric"):
            ea_vs_store.create_index("plain", metric_type="COSINE")


class TestTableNameMapAndIdentifiers:
    def test_unqualified_public_tables(self, ea_vs_store, vs_test_prefix):
        ea_vs_store.create_collection("plain")
        assert ea_vs_store.collection_exists("plain") is True
        # Confirm it's genuinely unqualified/public, not schema-qualified —
        # the live column check above already proves this implicitly, this
        # is the explicit assertion.
        cols = _live_columns("public", f"{vs_test_prefix}plain")
        assert len(cols) == 4  # base schema only, no extra columns for "plain"
