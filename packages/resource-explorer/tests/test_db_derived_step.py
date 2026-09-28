"""Tests for `db_derived` — the zero-fetch derivation step (Phase 1 slice 9).

Two things every test here is really about:

1. **Zero fetch.** `TestZeroFetch` fails if the step so much as reaches for a
   connection. It is the property the whole slice rests on, and it is the one
   a future refactor is most likely to break by accident (by folding the step
   back into `DatabaseSurveyor.survey()`, which opens a connection before
   dispatching anything).
2. **Absence discipline.** Every check has a positive case AND an
   insufficient-data case, and the tests assert the two do not render the
   same. Several go further and assert the *third* case — measured, and the
   answer is genuinely negative — is distinct from both.
"""
from __future__ import annotations

import pytest

from resource_explorer.registry import (
    SOURCE_EGERIA,
    STATE_MEASURED,
    STATE_NOT_MEASURED,
    DatabaseEntity,
    ProjectRegistry,
)
from resource_explorer.surveyors.database.db_derived import (
    DB_DERIVED_ANALYSES,
    KIND_STAGING,
    check_conventions,
    classify_database,
    derive_change_rates,
    derive_relationship_graph,
    determine_grain,
    fingerprint_database,
    load_inputs,
    propose_data_scope,
    run_db_derived,
)
from resource_explorer.surveyors.survey_report import (
    AnnotationType,
    DataGrainAnnotation,
    FingerprintAnnotation,
)

NOW = "2026-09-21T12:00:00"
EARLIER = "2026-09-14T12:00:00"


@pytest.fixture
def registry(tmp_path):
    r = ProjectRegistry(db_path=str(tmp_path / "test.db"))
    r.register_database(DatabaseEntity(
        slug="coco_ods", display_name="Coco ODS", db_type="postgresql",
        host="localhost", port=5442, database_name="coco_ods",
    ))
    return r


# ── row builders ───────────────────────────────────────────────────────────

def _table(name, *, rows=1000, cols=8, size=8192, desc="", ttype="BASE TABLE",
           schema="public"):
    return {
        "schema_name": schema, "table_name": name, "table_type": ttype,
        "row_count": rows, "column_count": cols, "size_bytes": size,
        "description": desc, "state": STATE_MEASURED,
    }


def _column(table, name, *, pk=False, fk=None, dtype="integer", desc="",
            schema="public", keys_captured=True):
    return {
        "schema_name": schema, "table_name": table, "column_name": name,
        "data_type": dtype, "base_type": dtype, "description": desc,
        "is_primary_key": (1 if pk else 0) if keys_captured else None,
        "foreign_key_json": fk,
        "state": STATE_MEASURED,
    }


def _activity(table, *, ins=0, upd=0, dele=0, seq=0, idx=0, reset=None,
              schema="public", null_counters=False):
    return {
        "schema_name": schema, "table_name": table,
        "rows_inserted": None if null_counters else ins,
        "rows_updated": None if null_counters else upd,
        "rows_deleted": None if null_counters else dele,
        "seq_scan": None if null_counters else seq,
        "idx_scan": None if null_counters else idx,
        "stats_reset": reset,
        "state": STATE_MEASURED,
    }


def _profile(table, column, *, distinct=None, min_value="", max_value="",
             histogram=None, schema="public"):
    return {
        "schema_name": schema, "table_name": table, "column_name": column,
        "distinct_count": distinct, "min_value": min_value,
        "max_value": max_value, "histogram_bounds_json": histogram,
        "state": STATE_MEASURED,
    }


def _store(registry, slug, surveyed_at, *, tables=None, columns=None,
           profiles=None, activity=None, source="local", record_survey=True):
    """Write one snapshot's rows, and (by default) the `database_surveys` row
    that makes the snapshot discoverable to the change-rate walker."""
    if record_survey:
        registry.record_database_survey(
            slug=slug, schema_count=1, table_count=len(tables or []),
            column_count=len(columns or []), survey_data={},
            source=source, surveyed_at=surveyed_at,
        )
    for table, rows in (
        ("database_tables", tables), ("database_columns", columns),
        ("database_column_profiles", profiles),
        ("database_table_activity", activity),
    ):
        if rows is not None:
            registry.write_detail_rows(table, slug, surveyed_at,
                                       source=source, rows=rows)


def _normalised_schema(registry, slug="coco_ods", surveyed_at=NOW, **kw):
    """A small, well-modelled OLTP schema: PKs everywhere, FKs connecting."""
    tables = [
        _table("customer", rows=50_000, cols=9),
        _table("orders", rows=400_000, cols=11),
        _table("order_line", rows=2_000_000, cols=7),
    ]
    columns = [
        _column("customer", "customer_id", pk=True),
        _column("customer", "email", dtype="character varying"),
        _column("orders", "order_id", pk=True),
        _column("orders", "customer_id",
                fk={"foreign_schema": "public", "foreign_table": "customer",
                    "foreign_column": "customer_id"}),
        _column("orders", "ordered_at", dtype="timestamp without time zone"),
        _column("order_line", "order_line_id", pk=True),
        _column("order_line", "order_id",
                fk={"foreign_schema": "public", "foreign_table": "orders",
                    "foreign_column": "order_id"}),
    ]
    activity = [
        _activity("customer", ins=50_000, upd=20_000, dele=500, idx=900_000, seq=12),
        _activity("orders", ins=400_000, upd=300_000, dele=1_000, idx=4_000_000, seq=30),
        _activity("order_line", ins=2_000_000, upd=90_000, dele=2_000, idx=8_000_000, seq=5),
    ]
    _store(registry, slug, surveyed_at, tables=tables, columns=columns,
           activity=activity, **kw)
    return tables, columns, activity


# ═══════════════════════════════════════════════════════════════════════════
# The property the whole slice rests on
# ═══════════════════════════════════════════════════════════════════════════

class TestZeroFetch:
    def test_no_connection_is_ever_opened(self, registry, monkeypatch):
        """The step must not touch the connection layer at all.

        Asserted by replacing `database_connection` with something that
        raises: a step that opens a connection fails loudly here rather than
        quietly costing a round trip in production.
        """
        import resource_explorer.surveyors.database.connection as conn_mod

        def _explode(*args, **kwargs):
            raise AssertionError(
                "db_derived opened a database connection — it is a zero-fetch "
                "step and must read stored rows only"
            )

        monkeypatch.setattr(conn_mod, "database_connection", _explode)
        monkeypatch.setattr(
            "resource_explorer.surveyors.database.database_surveyor."
            "database_connection", _explode, raising=False,
        )
        _normalised_schema(registry)
        result = run_db_derived(registry, "coco_ods")
        assert result["annotations"]

    def test_the_adapter_handler_takes_no_credentials(self):
        """`_run_db_derived` must not accept db_user/db_pwd.

        Not cosmetic: a handler that accepts credentials invites a future
        edit to use them, which would silently turn the cheapest step in the
        catalog into one that needs a reachable server.
        """
        import inspect

        from resource_explorer.surveyors.database.survey_definition_adapter import (
            _run_db_derived,
        )

        params = inspect.signature(_run_db_derived).parameters
        assert "db_user" not in params
        assert "db_pwd" not in params

    def test_it_answers_for_a_database_with_no_credentials(self, registry):
        """The pay-off of zero-fetch: a database whose credentials are gone
        and whose server is unreachable still gets these answers."""
        _normalised_schema(registry)
        db = registry.get_database("coco_ods")
        assert not db.db_user and not db.db_password
        result = run_db_derived(registry, "coco_ods")
        assert result["derived"]["db_relationship_graph"]["state"] == STATE_MEASURED

    def test_every_catalog_id_is_produced(self, registry):
        _normalised_schema(registry)
        derived = run_db_derived(registry, "coco_ods")["derived"]
        for analysis_id in DB_DERIVED_ANALYSES:
            assert analysis_id in derived, analysis_id


# ═══════════════════════════════════════════════════════════════════════════
# 1. db_classification
# ═══════════════════════════════════════════════════════════════════════════

class TestClassification:
    def test_a_normalised_oltp_schema_classifies_transactional(self, registry):
        _normalised_schema(registry)
        inputs = load_inputs(registry, "coco_ods")
        result = classify_database(inputs, {"state": STATE_NOT_MEASURED})
        assert result["state"] == STATE_MEASURED
        assert result["kind"] == "transactional"
        assert result["confidence"] > 0
        assert "structure" in result["signals_used"]
        assert "activity" in result["signals_used"]

    def test_star_schema_naming_and_shape_classifies_analytical(self, registry):
        tables = [
            _table("dim_customer", rows=50_000, cols=25),
            _table("dim_date", rows=3_650, cols=22),
            _table("fact_sales", rows=90_000_000, cols=30),
        ]
        columns = (
            [_column("dim_customer", "customer_key", pk=True)]
            + [_column("dim_customer", f"attr_{i}", dtype="text") for i in range(24)]
            + [_column("dim_date", "date_key", pk=True)]
            + [_column("fact_sales", "sales_id", pk=True)]
            + [_column("fact_sales", "customer_key",
                       fk={"foreign_schema": "public",
                           "foreign_table": "dim_customer",
                           "foreign_column": "customer_key"})]
        )
        activity = [
            _activity("dim_customer", ins=50_000, seq=4_000, idx=100),
            _activity("dim_date", ins=3_650, seq=4_000, idx=10),
            _activity("fact_sales", ins=90_000_000, seq=50_000, idx=200),
        ]
        _store(registry, "coco_ods", NOW, tables=tables, columns=columns,
               activity=activity)
        result = classify_database(load_inputs(registry, "coco_ods"),
                                   {"state": STATE_NOT_MEASURED})
        assert result["kind"] == "analytical"

    def test_staging_naming_and_churn_classifies_staging(self, registry):
        tables = [
            _table("stg_orders", rows=120_000, cols=6, ttype="BASE TABLE"),
            _table("stg_customers", rows=40_000, cols=5),
            _table("raw_events", rows=900_000, cols=4),
        ]
        # No primary keys anywhere — the staging shape.
        columns = [
            _column("stg_orders", "payload", dtype="jsonb"),
            _column("stg_customers", "payload", dtype="jsonb"),
            _column("raw_events", "payload", dtype="jsonb"),
        ]
        activity = [
            _activity("stg_orders", ins=5_000_000, dele=4_900_000, seq=40),
            _activity("stg_customers", ins=900_000, dele=880_000, seq=12),
            _activity("raw_events", ins=9_000_000, dele=8_000_000, seq=8),
        ]
        _store(registry, "coco_ods", NOW, tables=tables, columns=columns,
               activity=activity)
        result = classify_database(load_inputs(registry, "coco_ods"),
                                   {"state": STATE_NOT_MEASURED})
        assert result["kind"] == KIND_STAGING

    # ── absence ───────────────────────────────────────────────────────────

    def test_no_stored_rows_at_all_is_not_established(self, registry):
        result = classify_database(load_inputs(registry, "coco_ods"),
                                   {"state": STATE_NOT_MEASURED})
        assert result["state"] == STATE_NOT_MEASURED
        assert result["kind"] is None
        assert result["confidence"] == 0
        assert "insufficient signal" in result["explanation"]

    def test_missing_tuple_counters_lower_confidence_and_do_not_mean_staging(
        self, registry,
    ):
        """The brief's named failure mode.

        The same schema, twice: once with tuple counters, once with the
        counters NULL because ANALYZE never ran. The second must not become
        "staging" (which is what reading NULL as zero writes/zero reads would
        suggest), and must carry lower confidence because it had less to go
        on.
        """
        _normalised_schema(registry)
        with_counters = classify_database(load_inputs(registry, "coco_ods"),
                                          {"state": STATE_NOT_MEASURED})

        r2 = ProjectRegistry(db_path=registry.db_path.replace(".db", "-2.db"))
        r2.register_database(DatabaseEntity(
            slug="coco_ods", display_name="Coco ODS", db_type="postgresql",
            host="localhost", port=5442, database_name="coco_ods",
        ))
        tables, _columns, _ = _normalised_schema(r2)
        # Same tables/columns, counters present as rows but NULL throughout.
        r2.write_detail_rows(
            "database_table_activity", "coco_ods", NOW,
            rows=[_activity(t["table_name"], null_counters=True) for t in tables],
        )
        without = classify_database(load_inputs(r2, "coco_ods"),
                                    {"state": STATE_NOT_MEASURED})

        assert without["kind"] != KIND_STAGING
        assert "activity" in without["signals_missing"]
        assert without["confidence"] < with_counters["confidence"]
        assert "NOT evidence of a staging" in without["explanation"]

    def test_a_measured_but_idle_database_is_not_read_as_evidence(self, registry):
        """Counters present and genuinely zero is a real state — but it does
        not distinguish the five kinds, so it is excluded from the evidence
        rather than scoring every kind zero."""
        tables = [_table("thing", rows=10, cols=3)]
        columns = [_column("thing", "thing_id", pk=True)]
        _store(registry, "coco_ods", NOW, tables=tables, columns=columns,
               activity=[_activity("thing")])
        result = classify_database(load_inputs(registry, "coco_ods"),
                                   {"state": STATE_NOT_MEASURED})
        assert "activity" in result["signals_missing"]

    def test_confidence_never_reaches_certainty(self, registry):
        _normalised_schema(registry)
        fingerprint = {
            "state": STATE_MEASURED, "comparable_databases": 2,
            "best_similarity": 0.99,
        }
        result = classify_database(load_inputs(registry, "coco_ods"), fingerprint)
        assert result["confidence"] <= 95

    def test_a_measured_no_match_fingerprint_is_not_reported_as_missing(
        self, registry,
    ):
        """Found live, `adventureworks`, 2026-09-27: `db_fingerprint` had
        genuinely measured against 2 comparable databases and found nothing
        above the reportable Jaccard threshold — a real, negative finding —
        but `classify_database` still said "No data for: fingerprint",
        because `best_similarity is None` (the no-match case) was
        indistinguishable from having no comparable databases at all."""
        _normalised_schema(registry)
        fingerprint = {
            "state": STATE_MEASURED, "comparable_databases": 2,
            "best_similarity": None,
        }
        result = classify_database(load_inputs(registry, "coco_ods"), fingerprint)
        assert "fingerprint" not in result["signals_missing"]
        assert "fingerprint" in result["signals_used"]


# ═══════════════════════════════════════════════════════════════════════════
# 2. db_relationship_graph
# ═══════════════════════════════════════════════════════════════════════════

class TestRelationshipGraph:
    def test_a_connected_schema_is_a_data_model(self, registry):
        _normalised_schema(registry)
        result = derive_relationship_graph(load_inputs(registry, "coco_ods"))
        assert result["state"] == STATE_MEASURED
        assert result["verdict"] == "data_model"
        assert result["edge_count"] == 2
        assert result["component_count"] == 1
        assert result["isolated_tables"] == []
        assert {h["table"] for h in result["most_referenced"]} == {
            "public.customer", "public.orders",
        }

    def test_no_foreign_keys_with_keys_captured_is_a_real_finding(self, registry):
        """The distinction the whole module turns on, half one: keys WERE
        captured, and there are genuinely none. A positive finding."""
        tables = [_table("a"), _table("b")]
        columns = [_column("a", "x", pk=True), _column("b", "y", pk=True)]
        _store(registry, "coco_ods", NOW, tables=tables, columns=columns)
        result = derive_relationship_graph(load_inputs(registry, "coco_ods"))
        assert result["state"] == STATE_MEASURED
        assert result["verdict"] == "bag_of_tables"
        assert "real finding" in result["explanation"]

    def test_keys_never_captured_is_not_a_finding(self, registry):
        """Half two: a native-survey read-back stores every `is_primary_key`
        as NULL. Reporting "bag of tables" here would be a confident answer
        to a question nobody asked the database."""
        tables = [_table("a"), _table("b")]
        columns = [
            _column("a", "x", keys_captured=False),
            _column("b", "y", keys_captured=False),
        ]
        _store(registry, "coco_ods", NOW, tables=tables, columns=columns,
               source=SOURCE_EGERIA)
        inputs = load_inputs(registry, "coco_ods", source=SOURCE_EGERIA)
        assert not inputs.keys_were_captured
        result = derive_relationship_graph(inputs)
        assert result["state"] == STATE_NOT_MEASURED
        assert result["reason"] == "keys_not_captured"
        assert "not a finding that the database has no foreign keys" in \
            result["explanation"]

    def test_the_two_absences_render_differently(self, registry):
        """No rows at all, versus rows without keys — different reasons."""
        empty = derive_relationship_graph(load_inputs(registry, "coco_ods"))
        assert empty["reason"] == "no_schema_rows"

    def test_partial_model_when_most_tables_stand_alone(self, registry):
        tables = [_table(f"t{i}") for i in range(10)]
        columns = [_column(f"t{i}", "id", pk=True) for i in range(10)]
        columns.append(_column(
            "t1", "t0_id",
            fk={"foreign_schema": "public", "foreign_table": "t0",
                "foreign_column": "id"},
        ))
        _store(registry, "coco_ods", NOW, tables=tables, columns=columns)
        result = derive_relationship_graph(load_inputs(registry, "coco_ods"))
        assert result["verdict"] == "partial_model"
        assert len(result["isolated_tables"]) == 8

    def test_views_are_not_counted_as_tables(self, registry):
        tables = [_table("t", ttype="BASE TABLE"), _table("v", ttype="VIEW")]
        columns = [_column("t", "id", pk=True), _column("v", "id", pk=False)]
        _store(registry, "coco_ods", NOW, tables=tables, columns=columns)
        result = derive_relationship_graph(load_inputs(registry, "coco_ods"))
        assert result["table_count"] == 1

    def test_a_table_with_uncaptured_keys_is_excluded_not_counted_isolated(self, registry):
        """Slice 21b: a MIXED database — `a`'s keys were captured (a live
        survey), `b`'s were not (catalog-only fallback, or a partial
        credential) — used to have `keys_were_captured` (the database-wide
        `any()`) read True from `a` alone, then silently count `b` as a
        verified "isolated" table, identically to a table that genuinely
        has no foreign key. `b` must be excluded from the graph entirely,
        with its own count reported separately, not folded into
        `isolated_tables`."""
        tables = [_table("a"), _table("b")]
        columns = [
            _column("a", "x", pk=True),
            _column("b", "y", keys_captured=False),
        ]
        _store(registry, "coco_ods", NOW, tables=tables, columns=columns)
        result = derive_relationship_graph(load_inputs(registry, "coco_ods"))
        assert result["state"] == STATE_MEASURED
        assert result["table_count"] == 1
        assert result["unmeasured_table_count"] == 1
        # `a` (measured, no FK, sole member of the measured set) is a real
        # isolated finding; `b` (uncaptured) must never appear here at all —
        # it was excluded, not verified isolated.
        assert result["isolated_tables"] == ["public.a"]
        assert "public.b" not in result["isolated_tables"]
        assert "1 other table(s) excluded" in result["explanation"]

    def test_a_reference_to_an_uncovered_table_is_dangling_not_an_edge(self, registry):
        tables = [_table("a")]
        columns = [_column(
            "a", "b_id",
            fk={"foreign_schema": "other", "foreign_table": "b",
                "foreign_column": "id"},
        )]
        _store(registry, "coco_ods", NOW, tables=tables, columns=columns)
        result = derive_relationship_graph(load_inputs(registry, "coco_ods"))
        assert result["edge_count"] == 1
        assert len(result["dangling_references"]) == 1


# ═══════════════════════════════════════════════════════════════════════════
# 3. grain_determination
# ═══════════════════════════════════════════════════════════════════════════

class TestGrain:
    def test_a_primary_key_is_the_grain(self, registry):
        _normalised_schema(registry)
        result = determine_grain(load_inputs(registry, "coco_ods"))
        by_table = {g["table_name"]: g for g in result["grains"]}
        assert by_table["customer"]["grain_statement"] == "one row per customer_id"
        assert by_table["customer"]["basis"] == "primary_key"
        assert by_table["customer"]["confidence"] == 90

    def test_every_grain_row_carries_name_and_summary_for_the_evidence_panel(self, registry):
        """Live gate, owner, `coco_pharma`, 2026-09-27: the "numbers behind
        this" evidence panel rendered 58 grain rows as a bare repeated
        `label` value ("gap gap gap gap gap gap and 52 more") because
        `measureHtml()`'s array branch looks for `name`/`summary` (or
        `check_name`/`detail`), which grain entries never had."""
        _normalised_schema(registry)
        result = determine_grain(load_inputs(registry, "coco_ods"))
        for g in result["grains"]:
            assert g["name"] == g["qualified_name"]
            assert g["summary"], g["qualified_name"]
        by_table = {g["table_name"]: g for g in result["grains"]}
        assert by_table["customer"]["summary"] == "one row per customer_id"

    def test_a_gap_rows_summary_is_its_own_explanation(self, registry):
        tables = [_table("log_lines", rows=1_000_000)]
        columns = [
            _column("log_lines", "level", dtype="text"),
            _column("log_lines", "message", dtype="text"),
        ]
        profiles = [
            _profile("log_lines", "level", distinct=5),
            _profile("log_lines", "message", distinct=40_000),
        ]
        _store(registry, "coco_ods", NOW, tables=tables, columns=columns,
               profiles=profiles)
        grain = determine_grain(load_inputs(registry, "coco_ods"))["grains"][0]
        assert grain["name"] == "public.log_lines"
        assert "modelling gap" in grain["summary"]

    def test_a_composite_key_with_a_date_carries_an_interval(self, registry):
        tables = [_table("account_balance_daily")]
        columns = [
            _column("account_balance_daily", "account_id", pk=True),
            _column("account_balance_daily", "balance_date", pk=True,
                    dtype="date"),
            _column("account_balance_daily", "balance", dtype="numeric"),
        ]
        _store(registry, "coco_ods", NOW, tables=tables, columns=columns)
        grain = determine_grain(load_inputs(registry, "coco_ods"))["grains"][0]
        assert grain["grain_statement"] == "one row per account_id + balance_date"
        assert grain["interval"] == "per-date"

    def test_a_unique_column_stands_in_for_a_missing_primary_key(self, registry):
        tables = [_table("event", rows=1_000)]
        columns = [
            _column("event", "event_uuid", pk=False, dtype="uuid"),
            _column("event", "kind", dtype="text"),
        ]
        profiles = [
            _profile("event", "event_uuid", distinct=1_000),
            _profile("event", "kind", distinct=4),
        ]
        _store(registry, "coco_ods", NOW, tables=tables, columns=columns,
               profiles=profiles)
        grain = determine_grain(load_inputs(registry, "coco_ods"))["grains"][0]
        assert grain["grain_statement"] == "one row per event_uuid"
        assert grain["basis"] == "unique_column_exact"
        # Weaker than a declared key, and the explanation says why.
        assert grain["confidence"] < 90
        assert "not a declared constraint" in grain["explanation"]

    def test_a_negative_n_distinct_is_a_fraction_not_a_count(self, registry):
        """`pg_stats.n_distinct` is negative to mean "this fraction of rows",
        with -1 meaning unique. Slice 7 stores the raw value, so a consumer
        reading it as a count sees -1 distinct values in a perfectly unique
        column — the opposite of the truth."""
        tables = [_table("event", rows=5_000)]
        columns = [_column("event", "event_uuid", dtype="uuid")]
        profiles = [_profile("event", "event_uuid", distinct=-1)]
        _store(registry, "coco_ods", NOW, tables=tables, columns=columns,
               profiles=profiles)
        grain = determine_grain(load_inputs(registry, "coco_ods"))["grains"][0]
        assert grain["grain_statement"] == "one row per event_uuid"

    # ── absence ───────────────────────────────────────────────────────────

    def test_no_key_and_no_profile_is_not_established(self, registry):
        tables = [_table("blob_table", rows=10)]
        columns = [_column("blob_table", "payload", dtype="jsonb")]
        _store(registry, "coco_ods", NOW, tables=tables, columns=columns)
        grain = determine_grain(load_inputs(registry, "coco_ods"))["grains"][0]
        assert grain["state"] == STATE_NOT_MEASURED
        assert grain["label"] == "unverified"
        assert grain["confidence"] == 0
        assert "NOT established" in grain["explanation"]

    def test_profiles_present_but_nothing_unique_is_a_real_gap(self, registry):
        """Distinct from the case above: here we DID look, with real data, and
        the answer is that no column identifies a row. A modelling gap."""
        tables = [_table("log_lines", rows=1_000_000)]
        columns = [
            _column("log_lines", "level", dtype="text"),
            _column("log_lines", "message", dtype="text"),
        ]
        profiles = [
            _profile("log_lines", "level", distinct=5),
            _profile("log_lines", "message", distinct=40_000),
        ]
        _store(registry, "coco_ods", NOW, tables=tables, columns=columns,
               profiles=profiles)
        grain = determine_grain(load_inputs(registry, "coco_ods"))["grains"][0]
        assert grain["state"] == STATE_MEASURED
        assert grain["label"] == "gap"
        assert "modelling gap" in grain["explanation"]

    def test_the_two_undetermined_states_are_not_the_same(self, registry):
        """Explicitly: "we could not look" and "we looked and nothing is
        unique" must differ in state AND in label, or a screen showing either
        would say the same wrong thing."""
        tables = [_table("no_profile", rows=10), _table("profiled", rows=100)]
        columns = [
            _column("no_profile", "x", dtype="text"),
            _column("profiled", "y", dtype="text"),
        ]
        profiles = [_profile("profiled", "y", distinct=3)]
        _store(registry, "coco_ods", NOW, tables=tables, columns=columns,
               profiles=profiles)
        grains = {g["table_name"]: g
                  for g in determine_grain(load_inputs(registry, "coco_ods"))["grains"]}
        assert grains["no_profile"]["state"] == STATE_NOT_MEASURED
        assert grains["profiled"]["state"] == STATE_MEASURED
        assert grains["no_profile"]["label"] != grains["profiled"]["label"]

    def test_a_grain_is_published_as_a_draft_proposal(self, registry):
        """Support doc §3's project-owner decision: a proposal is a normal
        annotation with `contentStatus: DRAFT`, not an RFA convention."""
        _normalised_schema(registry)
        annotations = run_db_derived(registry, "coco_ods")["annotations"]
        grains = [a for a in annotations if isinstance(a, DataGrainAnnotation)]
        assert grains
        assert all(g.content_status == "DRAFT" for g in grains)
        assert all(g.annotation_type == AnnotationType.DATA_GRAIN for g in grains)
        # No DataGrain element exists to point at — that is the point.
        assert all(not g.candidate_data_grain_guids for g in grains)


# ═══════════════════════════════════════════════════════════════════════════
# 4. db_fingerprint
# ═══════════════════════════════════════════════════════════════════════════

class TestFingerprint:
    def _peer(self, registry, slug, columns, tables, surveyed_at=NOW):
        registry.register_database(DatabaseEntity(
            slug=slug, display_name=slug, db_type="postgresql",
            host="localhost", port=5442, database_name=slug,
        ))
        _store(registry, slug, surveyed_at, tables=tables, columns=columns)

    def test_an_identical_schema_is_flagged_as_a_likely_copy(self, registry):
        tables, columns, _ = _normalised_schema(registry)
        self._peer(registry, "coco_ods_restore", columns, tables)
        result = fingerprint_database(registry, load_inputs(registry, "coco_ods"))
        assert result["state"] == STATE_MEASURED
        assert result["comparable_databases"] == 1
        assert result["matches"][0]["slug"] == "coco_ods_restore"
        assert result["matches"][0]["verdict"] == "likely_copy"

    def test_a_subset_is_detected_by_containment_not_jaccard(self, registry):
        """A true subset of a large database has low Jaccard by construction,
        so containment is what catches it."""
        big_tables = [_table(f"t{i}") for i in range(20)]
        big_columns = [_column(f"t{i}", "id", pk=True) for i in range(20)]
        small_tables = big_tables[:3]
        small_columns = big_columns[:3]
        _store(registry, "coco_ods", NOW, tables=small_tables,
               columns=small_columns)
        self._peer(registry, "coco_full", big_columns, big_tables)
        result = fingerprint_database(registry, load_inputs(registry, "coco_ods"))
        top = result["matches"][0]
        assert top["verdict"] == "likely_subset_of"
        assert top["column_jaccard"] < 0.5
        assert top["containment"] == 1.0

    def test_nothing_to_compare_against_is_not_established(self, registry):
        """A registry of one database cannot answer "is this a copy". Saying
        "no match" here would be a finding nobody measured."""
        _normalised_schema(registry)
        result = fingerprint_database(registry, load_inputs(registry, "coco_ods"))
        assert result["comparable_databases"] == 0
        assert "NOT established" in result["explanation"]
        annotations = _scoped(run_db_derived(registry, "coco_ods"), "db_fingerprint")
        assert annotations[0].confidence == 0
        assert annotations[0].label == "unverified"

    def test_a_registered_but_unsurveyed_peer_is_not_comparable(self, registry):
        _normalised_schema(registry)
        registry.register_database(DatabaseEntity(
            slug="never_surveyed", display_name="x", db_type="postgresql",
            host="localhost", port=5442, database_name="x",
        ))
        result = fingerprint_database(registry, load_inputs(registry, "coco_ods"))
        assert result["comparable_databases"] == 0
        assert result["unsurveyed_peers"] == ["never_surveyed"]

    def test_comparable_peers_and_no_match_is_a_real_finding(self, registry):
        _normalised_schema(registry)
        self._peer(
            registry, "unrelated",
            [_column("widget", "sprocket_ref", dtype="text"),
             _column("gizmo", "thingy", dtype="text")],
            [_table("widget"), _table("gizmo")],
        )
        result = fingerprint_database(registry, load_inputs(registry, "coco_ods"))
        assert result["comparable_databases"] == 1
        assert result["matches"] == []
        assert "real finding" in result["explanation"]
        annotations = _scoped(run_db_derived(registry, "coco_ods"), "db_fingerprint")
        assert annotations[0].label == "no_match"
        assert annotations[0].confidence == 100

    def test_the_digest_is_stable_and_order_independent(self, registry):
        _tables, columns, _ = _normalised_schema(registry)
        first = fingerprint_database(registry, load_inputs(registry, "coco_ods"))
        # Same rows, written in a different order.
        registry.write_detail_rows(
            "database_columns", "coco_ods", NOW, rows=list(reversed(columns)),
        )
        second = fingerprint_database(registry, load_inputs(registry, "coco_ods"))
        assert first["digest"] == second["digest"]

    def test_the_annotation_carries_the_thresholds_it_judged_against(self, registry):
        """A disputed "likely copy" needs the line it crossed to be visible."""
        tables, columns, _ = _normalised_schema(registry)
        self._peer(registry, "coco_ods_restore", columns, tables)
        annotations = _scoped(run_db_derived(registry, "coco_ods"), "db_fingerprint")
        ann = annotations[0]
        assert isinstance(ann, FingerprintAnnotation)
        assert ann.json_properties["thresholds"]["copy_jaccard"] == 0.95
        assert ann.fingerprint_properties["algorithm"].startswith("sha256")


# ═══════════════════════════════════════════════════════════════════════════
# 5. schema_conventions
# ═══════════════════════════════════════════════════════════════════════════

class TestConventions:
    def test_a_clean_schema_passes_every_check(self, registry):
        tables = [_table("customer", desc="People who buy things")]
        columns = [_column("customer", "customer_id", pk=True, desc="Key")]
        _store(registry, "coco_ods", NOW, tables=tables, columns=columns)
        checks = check_conventions(load_inputs(registry, "coco_ods"))["checks"]
        assert checks["tables_without_primary_key"]["label"] == "pass"
        assert checks["tables_without_comment"]["label"] == "pass"
        assert checks["naming_convention"]["label"] == "pass"

    def test_missing_keys_comments_and_bad_names_are_reported(self, registry):
        tables = [_table("Order Items"), _table("good_table", desc="ok")]
        columns = [
            _column("Order Items", "ItemID"),
            _column("good_table", "id", pk=True, desc="key"),
        ]
        _store(registry, "coco_ods", NOW, tables=tables, columns=columns)
        checks = check_conventions(load_inputs(registry, "coco_ods"))["checks"]
        assert checks["tables_without_primary_key"]["count"] == 1
        assert checks["tables_without_primary_key"]["items"] == ["public.Order Items"]
        assert checks["tables_without_comment"]["count"] == 1
        assert checks["naming_convention"]["label"] == "gap"
        assert "public.Order Items" in checks["naming_convention"]["items"]

    def test_keys_never_captured_leaves_the_key_checks_unverified(self, registry):
        tables = [_table("a")]
        columns = [_column("a", "x", keys_captured=False)]
        _store(registry, "coco_ods", NOW, tables=tables, columns=columns,
               source=SOURCE_EGERIA)
        checks = check_conventions(
            load_inputs(registry, "coco_ods", source=SOURCE_EGERIA)
        )["checks"]
        check = checks["tables_without_primary_key"]
        assert check["state"] == STATE_NOT_MEASURED
        assert check["count"] is None
        assert check["label"] == "unverified"
        assert "NOT a finding that keys are missing" in check["explanation"]

    def test_nothing_documented_at_all_reads_as_not_captured(self, registry):
        """A real database where not one table and not one column carries a
        comment is likelier to mean the survey never read comments than that
        the database documents nothing. Reported as unverified rather than as
        a confident 100% undocumented."""
        tables = [_table(f"t{i}") for i in range(5)]
        columns = [_column(f"t{i}", "id", pk=True) for i in range(5)]
        _store(registry, "coco_ods", NOW, tables=tables, columns=columns)
        checks = check_conventions(load_inputs(registry, "coco_ods"))["checks"]
        assert checks["tables_without_comment"]["state"] == STATE_NOT_MEASURED
        assert checks["tables_without_comment"]["label"] == "unverified"
        assert checks["column_comment_coverage"]["fraction"] is None

    def test_partial_documentation_is_measured_not_guessed(self, registry):
        """The contrast with the test above: one documented column is enough
        to establish that comments WERE captured, so the rest are real gaps."""
        tables = [_table("t0", desc="documented"), _table("t1")]
        columns = [_column("t0", "id", pk=True, desc="key"),
                   _column("t1", "id", pk=True)]
        _store(registry, "coco_ods", NOW, tables=tables, columns=columns)
        checks = check_conventions(load_inputs(registry, "coco_ods"))["checks"]
        assert checks["tables_without_comment"]["state"] == STATE_MEASURED
        assert checks["tables_without_comment"]["count"] == 1
        assert checks["column_comment_coverage"]["fraction"] == 0.5

    def test_unused_indexes_are_deliberately_not_checked_here(self, registry):
        """Slice 7 owns the unused-index RFA from a live pg_stat_user_indexes
        read. Two RFAs for one problem is the bug this asserts against."""
        _normalised_schema(registry)
        result = check_conventions(load_inputs(registry, "coco_ods"))
        assert "unused_indexes" not in result["checks"]
        assert "unused_indexes" in result["excluded_checks"]
        annotations = run_db_derived(registry, "coco_ods")["annotations"]
        assert not any(
            "index" in (a.check_name or "").lower() for a in annotations
        )

    def test_no_rfa_is_raised_by_this_step(self, registry):
        """Conventions findings are measurements, published as labelled
        ResourceMeasure annotations. Slice 9 raises no RequestForAction at
        all — there is no PUBLIC grant or unused index in its scope."""
        _normalised_schema(registry)
        annotations = run_db_derived(registry, "coco_ods")["annotations"]
        assert not any(
            a.annotation_type == AnnotationType.REQUEST_FOR_ACTION
            for a in annotations
        )


# ═══════════════════════════════════════════════════════════════════════════
# 6. db_change_rates
# ═══════════════════════════════════════════════════════════════════════════

class TestChangeRates:
    def test_two_snapshots_give_a_rate(self, registry):
        _store(registry, "coco_ods", EARLIER,
               tables=[_table("orders", rows=100, size=1000)],
               activity=[_activity("orders", ins=1_000, upd=100, dele=10,
                                   reset="2026-09-01T00:00:00")])
        _store(registry, "coco_ods", NOW,
               tables=[_table("orders", rows=800, size=9000)],
               activity=[_activity("orders", ins=8_000, upd=900, dele=60,
                                   reset="2026-09-01T00:00:00")])
        result = derive_change_rates(registry, load_inputs(registry, "coco_ods"))
        assert result["state"] == STATE_MEASURED
        entry = result["per_table"][0]
        assert entry["change"] == "active"
        assert entry["deltas"] == {"rows_inserted": 7_000, "rows_updated": 800,
                                   "rows_deleted": 50}
        assert result["interval_days"] == pytest.approx(7.0)
        assert entry["per_day"]["rows_inserted"] == pytest.approx(1000.0)
        assert entry["size_bytes_delta"] == 8_000
        assert entry["row_count_delta"] == 700

    def test_one_snapshot_is_insufficient_history_not_zero_change(self, registry):
        """The brief's named case. Reporting "0 rows changed" for a database
        surveyed once is a confident, wrong answer."""
        _store(registry, "coco_ods", NOW,
               tables=[_table("orders")],
               activity=[_activity("orders", ins=1_000)])
        result = derive_change_rates(registry, load_inputs(registry, "coco_ods"))
        assert result["state"] == STATE_NOT_MEASURED
        assert result["reason"] == "insufficient_history"
        assert result["snapshots_available"] == 1
        assert result["per_table"] == []
        assert "NOT a finding that nothing is changing" in result["explanation"]

    def test_no_snapshots_at_all_is_also_insufficient_history(self, registry):
        result = derive_change_rates(registry, load_inputs(registry, "coco_ods"))
        assert result["reason"] == "insufficient_history"
        assert result["snapshots_available"] == 0

    def test_a_measured_zero_is_distinct_from_insufficient_history(self, registry):
        """Two snapshots whose counters did not move IS zero change — a real
        answer, and it must not render like the one-snapshot case."""
        for at in (EARLIER, NOW):
            _store(registry, "coco_ods", at,
                   tables=[_table("orders")],
                   activity=[_activity("orders", ins=1_000,
                                       reset="2026-09-01T00:00:00")])
        result = derive_change_rates(registry, load_inputs(registry, "coco_ods"))
        assert result["state"] == STATE_MEASURED
        entry = result["per_table"][0]
        assert entry["change"] == "idle"
        assert entry["deltas"]["rows_inserted"] == 0
        assert "genuinely idle" in entry["explanation"]

    def test_a_counter_reset_between_snapshots_yields_no_rate(self, registry):
        """registry.py's own `stats_reset` comment: the subtraction would draw
        "-40,000 inserts" as if it were real."""
        _store(registry, "coco_ods", EARLIER,
               tables=[_table("orders")],
               activity=[_activity("orders", ins=50_000,
                                   reset="2026-09-01T00:00:00")])
        _store(registry, "coco_ods", NOW,
               tables=[_table("orders")],
               activity=[_activity("orders", ins=10_000,
                                   reset="2026-09-20T00:00:00")])
        result = derive_change_rates(registry, load_inputs(registry, "coco_ods"))
        entry = result["per_table"][0]
        assert entry["state"] == STATE_NOT_MEASURED
        assert entry["change"] == "counters_reset"
        assert "deltas" not in entry
        assert result["tables_counters_reset"] == 1

    def test_counters_that_went_backwards_are_treated_as_a_reset(self, registry):
        """Even with no recorded `stats_reset` — an older Postgres, or a
        back-filled row — a counter that decreased cannot be a rate."""
        _store(registry, "coco_ods", EARLIER, tables=[_table("orders")],
               activity=[_activity("orders", ins=50_000)])
        _store(registry, "coco_ods", NOW, tables=[_table("orders")],
               activity=[_activity("orders", ins=10_000)])
        entry = derive_change_rates(
            registry, load_inputs(registry, "coco_ods"),
        )["per_table"][0]
        assert entry["change"] == "counters_reset"

    def test_null_counters_in_both_snapshots_are_not_zero(self, registry):
        for at in (EARLIER, NOW):
            _store(registry, "coco_ods", at, tables=[_table("orders")],
                   activity=[_activity("orders", null_counters=True)])
        entry = derive_change_rates(
            registry, load_inputs(registry, "coco_ods"),
        )["per_table"][0]
        assert entry["state"] == STATE_NOT_MEASURED
        assert entry["change"] == "counters_not_measured"
        assert "not zero change" in entry["explanation"]

    def test_a_table_new_since_the_last_survey_has_no_rate_yet(self, registry):
        _store(registry, "coco_ods", EARLIER, tables=[_table("orders")],
               activity=[_activity("orders", ins=10)])
        _store(registry, "coco_ods", NOW,
               tables=[_table("orders"), _table("shipments")],
               activity=[_activity("orders", ins=20),
                         _activity("shipments", ins=5)])
        result = derive_change_rates(registry, load_inputs(registry, "coco_ods"))
        by_table = {e["table_name"]: e for e in result["per_table"]}
        assert by_table["shipments"]["change"] == "new_table"
        assert result["schema_churn"]["tables_added"] == ["public.shipments"]

    def test_schema_churn_reports_removals(self, registry):
        _store(registry, "coco_ods", EARLIER,
               tables=[_table("orders"), _table("legacy")],
               activity=[_activity("orders", ins=10)])
        _store(registry, "coco_ods", NOW, tables=[_table("orders")],
               activity=[_activity("orders", ins=20)])
        churn = derive_change_rates(
            registry, load_inputs(registry, "coco_ods"),
        )["schema_churn"]
        assert churn["tables_removed"] == ["public.legacy"]

    def test_per_table_series_need_no_new_table(self, registry):
        """The design wants per-table series for Understanding charts. The
        existing `database_table_activity` rows across several `surveyed_at`
        values already are that series — this asserts the data is reachable
        without a new structured table, which is why none was added."""
        for at, ins in ((EARLIER, 1_000), (NOW, 5_000)):
            _store(registry, "coco_ods", at, tables=[_table("orders")],
                   activity=[_activity("orders", ins=ins,
                                       reset="2026-09-01T00:00:00")])
        series = [
            registry.query_detail_rows("database_table_activity", "coco_ods", at)[0]
            ["rows_inserted"]
            for at in (EARLIER, NOW)
        ]
        assert series == [1_000, 5_000]


# ═══════════════════════════════════════════════════════════════════════════
# Proposed DataScope
# ═══════════════════════════════════════════════════════════════════════════

class TestProposedDataScope:
    def test_min_max_from_a_profile_gives_a_coverage_range(self, registry):
        tables = [_table("orders")]
        columns = [_column("orders", "ordered_at",
                           dtype="timestamp without time zone")]
        profiles = [_profile("orders", "ordered_at",
                             min_value="2019-01-04", max_value="2026-09-19")]
        _store(registry, "coco_ods", NOW, tables=tables, columns=columns,
               profiles=profiles)
        result = propose_data_scope(load_inputs(registry, "coco_ods"))
        assert result["has_scope"]
        # DataScopeProperties' own key names — support doc §7.
        assert result["dataCoverageStartTime"] == "2019-01-04"
        assert result["dataCoverageEndTime"] == "2026-09-19"
        assert result["basis"] == "profile_min_max"

    def test_histogram_bounds_are_a_lower_confidence_fallback(self, registry):
        """min_value/max_value are written only by the native read-back path;
        slice 7's local pg_stats path writes histogram_bounds instead. Its
        extremes are estimates, and the confidence says so."""
        tables = [_table("orders")]
        columns = [_column("orders", "ordered_at", dtype="date")]
        profiles = [_profile("orders", "ordered_at",
                             histogram=["2020-02-01", "2023-01-01", "2026-08-01"])]
        _store(registry, "coco_ods", NOW, tables=tables, columns=columns,
               profiles=profiles)
        result = propose_data_scope(load_inputs(registry, "coco_ods"))
        assert result["dataCoverageStartTime"] == "2020-02-01"
        assert result["dataCoverageEndTime"] == "2026-08-01"
        assert result["basis"] == "histogram_bounds"
        assert result["confidence"] < 80
        assert "estimates of the extremes" in result["explanation"]

    def test_no_date_columns_is_a_real_answer(self, registry):
        tables = [_table("lookup")]
        columns = [_column("lookup", "code", dtype="text")]
        _store(registry, "coco_ods", NOW, tables=tables, columns=columns)
        result = propose_data_scope(load_inputs(registry, "coco_ods"))
        assert result["state"] == STATE_MEASURED
        assert result["has_scope"] is False
        assert result["reason"] == "no_date_columns"
        assert "A real finding, not missing data" in result["explanation"]

    def test_date_columns_with_no_profile_is_not_established(self, registry):
        """Distinct from the case above: there IS temporal data, we simply
        have no range for it."""
        tables = [_table("orders")]
        columns = [_column("orders", "ordered_at", dtype="date")]
        _store(registry, "coco_ods", NOW, tables=tables, columns=columns)
        result = propose_data_scope(load_inputs(registry, "coco_ods"))
        assert result["state"] == STATE_NOT_MEASURED
        assert result["reason"] == "no_profiled_date_columns"
        assert result["date_column_count"] == 1
        assert "NOT established" in result["explanation"]

    def test_the_proposal_is_published_as_a_draft(self, registry):
        tables = [_table("orders")]
        columns = [_column("orders", "ordered_at", dtype="date")]
        profiles = [_profile("orders", "ordered_at",
                             min_value="2019-01-04", max_value="2026-09-19")]
        _store(registry, "coco_ods", NOW, tables=tables, columns=columns,
               profiles=profiles)
        annotations = _scoped(run_db_derived(registry, "coco_ods"),
                              "proposed_data_scope")
        ann = annotations[0]
        assert ann.content_status == "DRAFT"
        assert ann.resource_properties["dataCoverageStartTime"] == "2019-01-04"
        assert "measured is not declared" in ann.explanation


# ═══════════════════════════════════════════════════════════════════════════
# Publishing
# ═══════════════════════════════════════════════════════════════════════════

class TestPublishMapping:
    def test_content_status_reaches_the_egeria_body_only_when_set(self):
        from resource_explorer.surveyors.annotation_props import build_annotation_props
        from resource_explorer.surveyors.survey_report import (
            ResourceMeasureAnnotation,
        )

        plain = ResourceMeasureAnnotation(summary="s", analysis_step="x")
        assert "contentStatus" not in build_annotation_props(plain, "q")

        proposal = ResourceMeasureAnnotation(
            summary="s", analysis_step="x", content_status="DRAFT",
        )
        assert build_annotation_props(proposal, "q")["contentStatus"] == "DRAFT"

    def test_data_grain_publishes_as_typed_egeria_fields(self):
        from resource_explorer.surveyors.annotation_props import build_annotation_props

        ann = DataGrainAnnotation(
            summary="s", analysis_step="x",
            grain_statement="one row per customer_id",
            granularity_basis="primary_key", interval="per-date",
            content_status="DRAFT",
        )
        props = build_annotation_props(ann, "q")
        assert props["class"] == "DataGrainAnnotationProperties"
        # The Egeria type's own field names, per support doc §3.
        assert props["grainStatement"] == "one row per customer_id"
        assert props["granularityBasis"] == "primary_key"
        assert props["interval"] == "per-date"
        assert props["contentStatus"] == "DRAFT"

    def test_fingerprint_payload_travels_as_additional_properties(self):
        """Its native field names are not known in this environment, so the
        payload must NOT be guessed into typed fields."""
        from resource_explorer.surveyors.annotation_props import build_annotation_props

        ann = FingerprintAnnotation(
            summary="s", analysis_step="x",
            fingerprint_properties={"digest": "abc", "tableCount": 3},
        )
        props = build_annotation_props(ann, "q")
        assert props["class"] == "FingerprintAnnotationProperties"
        assert props["additionalProperties"]["digest"] == "abc"

    def test_the_scope_check_name_literal_matches_its_constant(self):
        """The two annotation sites spell `proposed_data_scope` as a literal so
        that test_annotation_check_names.py's guard (which only reads
        ast.Constant) can see them. This pins the literal against the constant
        the `derived` dict is keyed by, so the dodge cannot become a drift."""
        from resource_explorer.surveyors.database import db_derived as mod

        assert mod._PROPOSED_SCOPE_CHECK == "proposed_data_scope"

    def test_both_new_types_are_in_the_annotation_registry(self):
        from resource_explorer.surveyors.survey_report import (
            ANNOTATION_TYPES_REGISTRY,
        )

        types = {entry["type"] for entry in ANNOTATION_TYPES_REGISTRY}
        assert {"DataGrainAnnotation", "FingerprintAnnotation"} <= types

    def test_every_annotation_names_its_step_and_check(self, registry):
        _normalised_schema(registry)
        annotations = run_db_derived(registry, "coco_ods")["annotations"]
        assert annotations
        for ann in annotations:
            assert ann.analysis_step == "db_derived"
            assert ann.check_name, ann.summary
            assert ann.explanation, ann.summary


class TestSlice21bHeadlines:
    """Slice 21b: the seven `db_derived` analyses that had NO headline
    reader before this (`db_classification`, `db_relationship_graph`,
    `grain_determination`, `schema_conventions`, `subject_signals`,
    `coverage_signals`, `preliminary_fit`) — found live, `coco_pharma`,
    2026-09-26, that all seven rendered at best `_renders_text`'s generic
    "ran; no summary reader" floor."""

    def test_every_one_of_the_seven_has_a_registered_headline(self):
        from resource_explorer.surveyors.database.survey_definition_adapter import (
            DATABASE_ANALYSIS_HEADLINE_MAP,
        )
        for field in (
            "db_classification", "db_relationship_graph", "grain_determination",
            "schema_conventions", "subject_signals", "coverage_signals",
            "preliminary_fit",
        ):
            assert field in DATABASE_ANALYSIS_HEADLINE_MAP

    def test_db_fingerprint_has_a_registered_headline(self, registry):
        """Owner's gate follow-up (2026-09-27): the last `db_derived`
        analysis with no headline — every other derived card had one by
        then, `db_fingerprint`'s ended in "no written summary" instead."""
        from resource_explorer.surveyors.database.survey_definition_adapter import (
            DATABASE_ANALYSIS_HEADLINE_MAP,
        )
        assert "db_fingerprint" in DATABASE_ANALYSIS_HEADLINE_MAP
        _normalised_schema(registry)
        headline = DATABASE_ANALYSIS_HEADLINE_MAP["db_fingerprint"](registry, "coco_ods")
        assert headline is not None
        assert headline["status"] == "info"
        assert headline["label"]

    def test_a_measured_analysis_relays_its_own_explanation(self, registry):
        from resource_explorer.surveyors.database.survey_definition_adapter import (
            DATABASE_ANALYSIS_HEADLINE_MAP,
        )
        _normalised_schema(registry)
        result = run_db_derived(registry, "coco_ods")
        expected = result["derived"]["db_relationship_graph"]["explanation"]
        headline = DATABASE_ANALYSIS_HEADLINE_MAP["db_relationship_graph"](registry, "coco_ods")
        assert headline["label"] == expected
        assert headline["status"] == "info"

    def test_a_not_measured_analysis_relays_its_reason_not_silence(self, registry):
        """The whole point: a not-yet-established analysis must render an
        honest sentence, not nothing (which the pre-Slice-21b state was for
        all seven) and not a confident-looking summary of nothing."""
        from resource_explorer.surveyors.database.survey_definition_adapter import (
            DATABASE_ANALYSIS_HEADLINE_MAP,
        )
        tables = [_table("a"), _table("b")]
        columns = [
            _column("a", "x", keys_captured=False),
            _column("b", "y", keys_captured=False),
        ]
        _store(registry, "coco_ods", NOW, tables=tables, columns=columns,
               source=SOURCE_EGERIA)
        headline = DATABASE_ANALYSIS_HEADLINE_MAP["db_relationship_graph"](registry, "coco_ods")
        assert headline is not None
        assert headline["status"] == "warn"
        assert "not established" in headline["label"] or "NOT established" in headline["label"]

    def test_schema_conventions_headline_names_a_real_gap(self, registry):
        """`check_conventions()`'s own `STATE_MEASURED` payload never sets a
        top-level `explanation` (only each check's own) — the bespoke
        `_schema_conventions_headline` (NOT the generic explanation-relay
        factory) surfaces the first real gap found, found live gap:
        without this, the resource-level headline fell through to just the
        container rollup sentence with no whole-database content ahead of
        it."""
        from resource_explorer.surveyors.database.survey_definition_adapter import (
            DATABASE_ANALYSIS_HEADLINE_MAP,
        )
        tables = [_table("a"), _table("b")]
        columns = [
            _column("a", "x", pk=True),
            _column("b", "y", pk=False),
        ]
        _store(registry, "coco_ods", NOW, tables=tables, columns=columns)
        headline = DATABASE_ANALYSIS_HEADLINE_MAP["schema_conventions"](registry, "coco_ods")
        assert headline is not None
        assert "1 of 2 base tables declare no primary key" in headline["label"]

    def test_schema_conventions_headline_states_a_clean_pass(self, registry):
        from resource_explorer.surveyors.database.survey_definition_adapter import (
            DATABASE_ANALYSIS_HEADLINE_MAP,
        )
        _normalised_schema(registry)
        headline = DATABASE_ANALYSIS_HEADLINE_MAP["schema_conventions"](registry, "coco_ods")
        assert headline is not None
        assert "no gap found" in headline["label"]

    def test_no_stored_rows_at_all_still_states_the_reason(self, registry):
        """Even with zero stored rows, `classify_database` returns a real,
        explained `STATE_NOT_MEASURED` payload (not a falsy one) — so the
        headline correctly renders that reason as a `warn`-status sentence,
        the same honest-absence floor every other reader in this module
        gives, rather than a bare `None` that would look identical to "this
        reader has nothing to say" for an analysis that in fact does."""
        from resource_explorer.surveyors.database.survey_definition_adapter import (
            DATABASE_ANALYSIS_HEADLINE_MAP,
        )
        headline = DATABASE_ANALYSIS_HEADLINE_MAP["db_classification"](registry, "coco_ods")
        assert headline is not None
        assert headline["status"] == "warn"
        assert "Not a finding" in headline["label"]

    def _two_schema_database(self, registry):
        """Two schemas in ONE stored snapshot — `write_detail_rows` replaces
        a (slug, surveyed_at, source)'s whole table, so both schemas' rows
        must be written together, not via two separate `_store()` calls at
        the same `surveyed_at`."""
        tables = [
            _table("customer", schema="public"),
            _table("orders", schema="public"),
            _table("t1", schema="eu_sales"),
        ]
        columns = [
            _column("customer", "customer_id", pk=True, schema="public"),
            _column("orders", "order_id", pk=True, schema="public"),
            _column("orders", "customer_id", schema="public",
                    fk={"foreign_schema": "public", "foreign_table": "customer",
                        "foreign_column": "customer_id"}),
            _column("t1", "id", pk=True, schema="eu_sales"),
        ]
        _store(registry, "coco_ods", NOW, tables=tables, columns=columns)

    def test_relationship_graph_container_headline_names_each_schema(self, registry):
        from resource_explorer.surveyors.database.survey_definition_adapter import (
            DATABASE_ANALYSIS_CONTAINER_HEADLINE_MAP,
        )
        self._two_schema_database(registry)
        headline = DATABASE_ANALYSIS_CONTAINER_HEADLINE_MAP["db_relationship_graph"](
            registry, "coco_ods")
        assert headline is not None
        assert "public:" in headline["label"]
        assert "eu_sales:" in headline["label"]
        # Owner's own design rule ("never a rollup without its parts, which
        # also means never parts without the rollup") — the per-schema list
        # must be led by a one-sentence database-wide summary.
        assert headline["label"].startswith("Keys captured for ")
        assert " — by schema: " in headline["label"]

    def test_grain_determination_container_headline_groups_by_schema(self, registry):
        from resource_explorer.surveyors.database.survey_definition_adapter import (
            DATABASE_ANALYSIS_CONTAINER_HEADLINE_MAP,
        )
        self._two_schema_database(registry)
        headline = DATABASE_ANALYSIS_CONTAINER_HEADLINE_MAP["grain_determination"](
            registry, "coco_ods")
        assert headline is not None
        assert "public:" in headline["label"]
        assert "eu_sales:" in headline["label"]
        assert "table(s) grain-determined" in headline["label"]
        prefix, sep, by_schema_part = headline["label"].partition(" — by schema: ")
        assert sep, headline["label"]
        assert prefix.endswith("table(s) grain-determined")
        assert "public:" in by_schema_part and "eu_sales:" in by_schema_part

    def test_grain_determination_reports_a_key_capture_count_not_a_boolean(self, registry):
        """Slice 21a follow-up (owner's gate, 2026-09-27): a whole-database
        boolean ("keys were captured: yes") beside per-schema
        "keys_not_captured" lines read as a contradiction — the two were
        answering different questions (any table vs THIS table). A count
        states what was actually true without the reader reconciling scope."""
        _normalised_schema(registry)
        result = determine_grain(load_inputs(registry, "coco_ods"))
        assert "keys_were_captured" not in result
        assert result["keys_captured_count"] == result["table_count"]

    def test_a_measured_gap_is_not_counted_as_determined(self, registry):
        """`determine_grain()`'s own `determined_count` definition is
        `grain_statement` truthiness, NOT `state == STATE_MEASURED` — a
        "gap" entry (profiled, and genuinely no candidate key) is measured
        but has no grain statement. Found live, `coco_pharma`, 2026-09-26:
        gating on state alone read a "gap" table as "determined", grossly
        overcounting against the resource-level headline's own count for
        the same database."""
        from resource_explorer.surveyors.database.survey_definition_adapter import (
            DATABASE_ANALYSIS_CONTAINER_HEADLINE_MAP,
        )
        tables = [_table("log_lines", rows=1_000_000)]
        columns = [
            _column("log_lines", "level", dtype="text"),
            _column("log_lines", "message", dtype="text"),
        ]
        profiles = [
            _profile("log_lines", "level", distinct=5),
            _profile("log_lines", "message", distinct=40_000),
        ]
        _store(registry, "coco_ods", NOW, tables=tables, columns=columns,
               profiles=profiles)
        headline = DATABASE_ANALYSIS_CONTAINER_HEADLINE_MAP["grain_determination"](
            registry, "coco_ods")
        assert headline is not None
        assert "public: 0 of 1 table(s) grain-determined" in headline["label"]

    def test_resource_headline_distinguishes_key_basis_from_naming_basis(
        self, registry,
    ):
        """Found live, `adventureworks`, 2026-09-27: "68 carry a time
        interval" read as "68 have a date column in the key" — the strong
        `primary_key_date` basis — but on that database the heuristic that
        actually fired for nearly all of them was a column-NAME match (every
        table has a `modifieddate` column); only 6 genuinely had a date
        column in the primary key. The headline must say which basis did
        the work, not collapse both into one undifferentiated count."""
        from resource_explorer.surveyors.database.survey_definition_adapter import (
            DATABASE_ANALYSIS_HEADLINE_MAP,
        )
        tables = [_table("period_log"), _table("widget")]
        columns = [
            _column("period_log", "log_date", pk=True, dtype="date"),
            _column("widget", "id", pk=True),
            _column("widget", "modifieddate", dtype="timestamp"),
        ]
        _store(registry, "coco_ods", NOW, tables=tables, columns=columns)
        headline = DATABASE_ANALYSIS_HEADLINE_MAP["grain_determination"](
            registry, "coco_ods")
        assert headline is not None
        label = headline["label"]
        assert "2 carry a time interval" in label
        assert "1 from a date column in the key" in label
        assert "1 from naming only" in label
        assert "lower confidence" in label

    def test_resource_headline_flags_naming_only_when_no_table_has_a_key_date(
        self, registry,
    ):
        from resource_explorer.surveyors.database.survey_definition_adapter import (
            DATABASE_ANALYSIS_HEADLINE_MAP,
        )
        tables = [_table("widget")]
        columns = [
            _column("widget", "id", pk=True),
            _column("widget", "modifieddate", dtype="timestamp"),
        ]
        _store(registry, "coco_ods", NOW, tables=tables, columns=columns)
        headline = DATABASE_ANALYSIS_HEADLINE_MAP["grain_determination"](
            registry, "coco_ods")
        assert headline is not None
        assert "1 carry a time interval (naming basis, lower confidence)" in headline["label"]


class TestCoverageStatusFlipsToNotEstablished:
    """Live gate, owner, `coco_pharma`, 2026-09-27: with 3 of 58 tables
    key-captured, `db_relationship_graph`/`db_classification`/
    `grain_determination` all read `_status.state == 'measured'` while their
    own headline text said "insufficient signal"/named 55 of 58 tables
    excluded — a "correct number, wrong label" defect: the text was honest,
    the state field the frontend actually gates a checkmark on was not.
    `_attach_coverage_status` fixes this at the one seam `facts.py`'s
    `_state_for` already prefers (`value["_status"]["state"]`)."""

    def test_thin_relationship_graph_coverage_is_not_established(self, registry):
        tables = [_table(f"t{i}") for i in range(58)]
        columns = [_column("t0", "id", pk=True), _column("t1", "id", pk=True),
                   _column("t2", "id", pk=True)]
        for i in range(3, 58):
            columns.append(_column(f"t{i}", "id", keys_captured=False))
        _store(registry, "coco_ods", NOW, tables=tables, columns=columns)
        from resource_explorer.facts import FactLayer
        from resource_explorer.surveyors.result_status import NOT_ESTABLISHED
        fl = FactLayer(registry=registry, resource_type="database")
        f = fl.fact("coco_ods", "db_relationship_graph")
        assert f.state == NOT_ESTABLISHED
        assert "insufficient" in f.headline.lower() or "excluded" in f.headline.lower()

    def test_majority_coverage_stays_measured(self, registry):
        tables = [_table(f"t{i}") for i in range(10)]
        columns = [_column(f"t{i}", "id", pk=True) for i in range(8)]
        columns += [_column("t8", "id", keys_captured=False),
                    _column("t9", "id", keys_captured=False)]
        _store(registry, "coco_ods", NOW, tables=tables, columns=columns)
        from resource_explorer.facts import FactLayer
        from resource_explorer.surveyors.result_status import MEASURED
        fl = FactLayer(registry=registry, resource_type="database")
        f = fl.fact("coco_ods", "db_relationship_graph")
        assert f.state == MEASURED

    def test_thin_grain_coverage_is_not_established(self, registry):
        tables = [_table(f"t{i}") for i in range(58)]
        columns = [_column("t0", "id", pk=True), _column("t1", "id", pk=True),
                   _column("t2", "id", pk=True)]
        for i in range(3, 58):
            columns.append(_column(f"t{i}", "id", keys_captured=False))
        _store(registry, "coco_ods", NOW, tables=tables, columns=columns)
        from resource_explorer.facts import FactLayer
        from resource_explorer.surveyors.result_status import NOT_ESTABLISHED
        fl = FactLayer(registry=registry, resource_type="database")
        f = fl.fact("coco_ods", "grain_determination")
        assert f.state == NOT_ESTABLISHED

    def test_thin_classification_coverage_is_not_established(self, registry):
        tables = [_table(f"t{i}", cols=2) for i in range(4)]
        columns = [_column("t0", "id", keys_captured=False)]
        _store(registry, "coco_ods", NOW, tables=tables, columns=columns)
        from resource_explorer.facts import FactLayer
        from resource_explorer.surveyors.result_status import NOT_ESTABLISHED
        fl = FactLayer(registry=registry, resource_type="database")
        f = fl.fact("coco_ods", "db_classification")
        assert f.state == NOT_ESTABLISHED


class TestAdapterRegistration:
    def test_db_derived_is_a_registered_re_analysis_step(self):
        from resource_explorer.surveyors.database import survey_definition_adapter as sda

        assert "db_derived" in sda._ADAPTER.re_analysis_steps
        assert "db_derived" in sda._ADAPTER.re_analysis_step_info

    def test_the_catalog_declares_all_six_analyses(self):
        from resource_explorer.surveyors import analysis_catalog_reader as acr

        ids = {a["id"] for a in acr.get_analyses("database", include_egeria_live=False)}
        assert set(DB_DERIVED_ANALYSES) <= ids


# ── helpers ────────────────────────────────────────────────────────────────

def _scoped(result: dict, check_prefix: str) -> list:
    """Annotations belonging to one of the folded checks."""
    return [
        a for a in result["annotations"]
        if (a.annotation_type_name or "") == check_prefix
    ]
