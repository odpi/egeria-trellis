"""`_schema_inventory_container_headline` — Slice 21a's per-schema
classification for "Which schemas carry the data, and which are system,
empty or staging?" (`levels: [container]` alone).

Before this, `FactLayer._headline_for` had no notion of level, so this
question rendered the exact same resource-level sentence "How big is this
database" does — names schemas, classifies none. Found live, owner's
question, 2026-09-26.
"""
from __future__ import annotations

import json

from resource_explorer.surveyors.database.survey_definition_adapter import (
    _schema_inventory_container_headline,
)


class _FakeRegistry:
    def __init__(self, tables, survey_data=None):
        self._tables = tables
        self._survey_data = survey_data

    def query_detail_rows(self, table, slug):
        if table == "database_tables":
            return list(self._tables)
        return []

    def get_database_surveys(self, slug):
        if self._survey_data is None:
            return []
        return [{"survey_data": json.dumps(self._survey_data)}]


def _table(schema, name, row_count=None, state="measured", table_type="BASE TABLE"):
    return {"schema_name": schema, "table_name": name, "table_type": table_type,
            "state": state, "row_count": row_count}


def _cap(by_schema):
    return {"credential_capability": {"by_schema": by_schema}}


class TestSystemSchemasAreFoldedAway:
    def test_pg_catalog_and_information_schema_are_never_named(self):
        registry = _FakeRegistry(tables=[
            _table("public", "a", row_count=5),
            _table("pg_catalog", "pg_class", row_count=1000),
            _table("information_schema", "tables", row_count=50),
        ])
        result = _schema_inventory_container_headline(registry, "mydb")
        assert "pg_catalog" not in result["label"]
        assert "information_schema" not in result["label"]
        assert "2 system schema(s) folded" in result["label"]

    def test_pg_toast_and_pg_temp_prefixes_are_folded_too(self):
        registry = _FakeRegistry(tables=[
            _table("public", "a", row_count=5),
            _table("pg_toast_12345", "x", row_count=0),
            _table("pg_temp_3", "y", row_count=0),
        ])
        result = _schema_inventory_container_headline(registry, "mydb")
        assert "2 system schema(s) folded" in result["label"]


class TestDataAndEmptyClassification:
    def test_a_schema_with_measured_rows_is_named_with_its_row_total(self):
        registry = _FakeRegistry(tables=[
            _table("coco_ods", "a", row_count=1000),
            _table("coco_ods", "b", row_count=100),
        ])
        result = _schema_inventory_container_headline(registry, "mydb")
        assert "coco_ods 2 table(s) · 1,100 row(s)" in result["label"]

    def test_estimate_state_adds_the_est_caveat(self):
        from resource_explorer.registry import STATE_CATALOG_ESTIMATE
        registry = _FakeRegistry(tables=[
            _table("coco_ods", "a", row_count=50, state=STATE_CATALOG_ESTIMATE),
        ])
        result = _schema_inventory_container_headline(registry, "mydb")
        assert "50 row(s) (est.)" in result["label"]

    def test_zero_measured_rows_across_every_table_is_empty(self):
        registry = _FakeRegistry(tables=[
            _table("eu_sales", "a", row_count=0),
        ])
        result = _schema_inventory_container_headline(registry, "mydb")
        assert "eu_sales 1 table(s) · 0 row(s) — empty" in result["label"]

    def test_a_schema_with_no_tables_at_all_is_empty(self):
        """A schema can have zero tables entirely — SCOPE_EMPTY's own case,
        or simply a schema `get_schema_info` found with nothing in it."""
        registry = _FakeRegistry(tables=[
            _table("public", "a", row_count=1),
        ], survey_data=_cap({
            "public": {"usage_granted": True, "table_total": 1, "table_select": 1},
            "empty_schema": {"usage_granted": True, "table_total": 0, "table_select": 0},
        }))
        # `empty_schema` never appears in database_tables (it has no rows to
        # be distinct over) so this reader — which only iterates schemas it
        # finds IN database_tables — will not name it. This test instead
        # pins the more common shape: a schema present with a SCOPE_EMPTY
        # verdict from the probe, table_total 0, genuinely absent from
        # database_tables. Documented here rather than asserted on, since
        # there is nothing in `tables` to iterate for it — see the
        # `_schema_inventory_results`'s own docstring on `schema_total` vs
        # `schemas_with_tables` for the parallel gap.
        result = _schema_inventory_container_headline(registry, "mydb")
        assert "public 1 table(s) · 1 row(s)" in result["label"]


class TestViewOnlySchemasAreDistinctFromEmpty:
    """Found live, `adventureworks`, 2026-09-27: AdventureWorks's shortcut
    schemas (`hr`/`pe`/`pr`/`pu`/`sa`) hold only views over tables that
    live in another schema — genuinely zero base tables, not a schema
    nobody has populated. Views' row_count is never measured, so before
    this fix they fell into the same "0 row(s) — empty" bucket as a truly
    empty schema, indistinguishable from it."""

    def test_a_schema_of_only_views_is_not_reported_as_empty(self):
        registry = _FakeRegistry(tables=[
            _table("hr", "v_employee", table_type="VIEW"),
            _table("hr", "v_department", table_type="VIEW"),
        ])
        result = _schema_inventory_container_headline(registry, "mydb")
        assert "empty" not in result["label"]
        assert "hr 2 view(s)" in result["label"]
        assert "no base tables" in result["label"]

    def test_a_schema_with_at_least_one_base_table_is_not_views_only(self):
        registry = _FakeRegistry(tables=[
            _table("public", "v", table_type="VIEW"),
            _table("public", "t", table_type="BASE TABLE", row_count=5),
        ])
        result = _schema_inventory_container_headline(registry, "mydb")
        assert "views only" not in result["label"] and "view(s)" not in result["label"]
        assert "public 2 table(s) · 5 row(s)" in result["label"]


class TestCredentialScopeClassification:
    def test_no_usage_renders_no_access(self):
        registry = _FakeRegistry(
            tables=[_table("demo", "a", row_count=None)],
            survey_data=_cap({"demo": {"usage_granted": False, "table_total": 1, "table_select": 0}}),
        )
        result = _schema_inventory_container_headline(registry, "mydb")
        assert "demo 1 table(s) — no access" in result["label"]

    def test_usage_but_no_select_renders_structure_only(self):
        registry = _FakeRegistry(
            tables=[_table("coco_ods", "a", row_count=None), _table("coco_ods", "b", row_count=None)],
            survey_data=_cap({"coco_ods": {"usage_granted": True, "table_total": 2, "table_select": 0}}),
        )
        result = _schema_inventory_container_headline(registry, "mydb")
        assert "coco_ods 2 table(s) — structure only" in result["label"]


class TestStagingIsMarkedAsAHeuristic:
    def test_a_staging_named_schema_says_by_name(self):
        registry = _FakeRegistry(tables=[_table("stg_orders", "a", row_count=5)])
        result = _schema_inventory_container_headline(registry, "mydb")
        assert "stg_orders 1 table(s) — staging (by name)" in result["label"]

    def test_tmp_scratch_and_sandbox_all_match(self):
        for name in ("tmp_loads", "scratch_area", "sandbox_dev", "staging"):
            registry = _FakeRegistry(tables=[_table(name, "a", row_count=5)])
            result = _schema_inventory_container_headline(registry, "mydb")
            assert "staging (by name)" in result["label"], name


class TestOrdering:
    def test_data_schemas_sort_by_rows_descending(self):
        registry = _FakeRegistry(tables=[
            _table("small", "a", row_count=10),
            _table("big", "a", row_count=1000),
            _table("medium", "a", row_count=100),
        ])
        result = _schema_inventory_container_headline(registry, "mydb")
        label = result["label"]
        assert label.index("big") < label.index("medium") < label.index("small")

    def test_data_then_staging_then_empty_then_no_access_then_system(self):
        """Dan's gate, `coco_pharma`, 2026-09-27: staging now sorts ahead of
        empty (both are "has tables, not blocked" categories; staging just
        carries a naming caveat), and no-access — nothing knowable about the
        schema at all, not even its table count — is worst, right before
        the system fold. See structure-only/views-only's own dedicated
        ordering test below for why THOSE now beat empty specifically."""
        registry = _FakeRegistry(
            tables=[
                _table("real_data", "a", row_count=5),
                _table("nothing_here", "a", row_count=0),
                _table("tmp_stuff", "a", row_count=5),
                _table("locked", "a", row_count=None),
                _table("pg_catalog", "a", row_count=1),
            ],
            survey_data=_cap({
                "locked": {"usage_granted": False, "table_total": 1, "table_select": 0},
            }),
        )
        result = _schema_inventory_container_headline(registry, "mydb")
        label = result["label"]
        assert (label.index("real_data") < label.index("tmp_stuff")
                < label.index("nothing_here") < label.index("locked"))
        assert label.rstrip(".").endswith("1 system schema(s) folded")


class TestStructureOnlyOutranksEmptyRegardlessOfSize:
    """The exact live scenario, `coco_pharma`, 2026-09-27: `eu_sales`,
    `public`, `target_sales`, `us_sales` (all genuinely empty, 0-1 tables
    each) listed ABOVE `coco_ods` (23 tables) and `coco_sus` (30 tables),
    both structure-only. For a credential that cannot read rows, a
    structure-only schema with many tables is almost certainly where the
    real data lives, so it must outrank an empty one regardless of size —
    the four empty schemas' per-schema states were themselves correct (the
    surveyor's own 3 SELECT-able tables really are empty); only the ORDER
    was wrong."""

    def test_large_structure_only_schemas_beat_small_empty_ones(self):
        registry = _FakeRegistry(
            tables=(
                [_table("eu_sales", "eu_sales_forecast", row_count=0)]
                + [_table("public", f"t{i}", row_count=0) for i in range(1)]
                + [_table("target_sales", "consolidated_forecast", row_count=0)]
                + [_table("us_sales", "us_sales_forecast", row_count=0)]
                + [_table("coco_ods", f"t{i}") for i in range(23)]
                + [_table("coco_sus", f"t{i}") for i in range(30)]
            ),
            survey_data=_cap({
                "coco_ods": {"usage_granted": True, "table_total": 23, "table_select": 0},
                "coco_sus": {"usage_granted": True, "table_total": 30, "table_select": 0},
            }),
        )
        result = _schema_inventory_container_headline(registry, "mydb")
        label = result["label"]
        # Both structure-only schemas beat every empty one...
        for structure_only in ("coco_ods", "coco_sus"):
            for empty in ("eu_sales", "public", "target_sales", "us_sales"):
                assert label.index(structure_only) < label.index(empty), \
                    f"{structure_only} should outrank {empty}"
        # ...and within the structure-only group, the larger one comes first.
        assert label.index("coco_sus") < label.index("coco_ods")

    def test_within_structure_only_sorts_by_table_count_descending(self):
        registry = _FakeRegistry(
            tables=(
                [_table("small_locked", "a", row_count=None)]
                + [_table("big_locked", f"t{i}", row_count=None) for i in range(5)]
            ),
            survey_data=_cap({
                "small_locked": {"usage_granted": True, "table_total": 1, "table_select": 0},
                "big_locked": {"usage_granted": True, "table_total": 5, "table_select": 0},
            }),
        )
        result = _schema_inventory_container_headline(registry, "mydb")
        label = result["label"]
        assert label.index("big_locked") < label.index("small_locked")


class TestReturnsNoneWhenNothingToSay:
    def test_no_tables_at_all(self):
        registry = _FakeRegistry(tables=[])
        assert _schema_inventory_container_headline(registry, "mydb") is None


class TestSlice21aFollowups:
    """Owner's gate on 8812, 2026-09-27 — three defects in the per-schema
    breakdown found live against `coco_pharma`."""

    def test_a_zero_table_schema_the_probe_knows_about_still_appears(self):
        """`public` (USAGE granted, zero tables) never has a `database_
        tables` row to be grouped by, so it was silently missing from a list
        the header's own schema_total says should have every schema."""
        registry = _FakeRegistry(
            tables=[_table("coco_ods", "a", row_count=5)],
            survey_data=_cap({
                "coco_ods": {"usage_granted": True, "table_total": 1, "table_select": 1},
                "public": {"usage_granted": True, "table_total": 0, "table_select": 0},
            }),
        )
        result = _schema_inventory_container_headline(registry, "mydb")
        assert "public — empty (no tables)" in result["label"]

    def test_structure_only_schema_still_names_its_estimated_row_total(self):
        """A structure-only schema can still carry a real catalog-estimated
        row total (the catalog-only fallback reads pg_class.reltuples
        regardless of SELECT grants) — dropping it erased size information
        "How big is this database" already counts."""
        from resource_explorer.registry import STATE_CATALOG_ESTIMATE
        registry = _FakeRegistry(
            tables=[_table("coco_ods", "a", row_count=113, state=STATE_CATALOG_ESTIMATE)],
            survey_data=_cap({
                "coco_ods": {"usage_granted": True, "table_total": 1, "table_select": 0},
            }),
        )
        result = _schema_inventory_container_headline(registry, "mydb")
        assert "coco_ods 1 table(s) · ~113 row(s) (est.) — structure only" in result["label"]

    def test_a_schema_whose_only_table_has_no_row_data_is_empty_not_data(self):
        """`row_count IS NULL` (never measured, no catalog-estimate fallback
        either) used to fall through to the "data" branch, where `row_total
        or 0` silently displayed "not measured" as a measured "0 row(s)"
        under the DATA classification rather than `empty`."""
        registry = _FakeRegistry(
            tables=[_table("eu_sales", "eu_sales_forecast", row_count=None)],
        )
        result = _schema_inventory_container_headline(registry, "mydb")
        assert "eu_sales 1 table(s) · 0 row(s) — empty" in result["label"]

    def test_ordering_is_data_then_structure_only_then_empty_then_system(self):
        """Dan's gate, `coco_pharma`, 2026-09-27: `coco_ods` here is
        structure-only (USAGE granted, no SELECT) — before this fix it sank
        below every empty schema regardless of size, the exact defect the
        gate named ("`coco_ods` (23 tables) listed below `eu_sales`/`public`
        (0-1 tables, genuinely empty)"). Structure-only now sorts right
        after data."""
        from resource_explorer.registry import STATE_CATALOG_ESTIMATE
        registry = _FakeRegistry(
            tables=[
                _table("real_data", "a", row_count=500),
                _table("eu_sales", "a", row_count=None),
                _table("coco_ods", "a", row_count=113, state=STATE_CATALOG_ESTIMATE),
                _table("pg_catalog", "a", row_count=1),
            ],
            survey_data=_cap({
                "coco_ods": {"usage_granted": True, "table_total": 1, "table_select": 0},
                "public": {"usage_granted": True, "table_total": 0, "table_select": 0},
            }),
        )
        result = _schema_inventory_container_headline(registry, "mydb")
        label = result["label"]
        assert (label.index("real_data") < label.index("coco_ods")
                < label.index("eu_sales") < label.index("public"))
        assert label.rstrip(".").endswith("1 system schema(s) folded")
