"""`db_hub_tables` — "Which tables would a consumer start with?" (design §5.3;
Backlog "`db_hub_tables` is most of the way there for free").

A `db_derived` check, so it reads stored rows only. What these tests pin:

1. It is declared the way every other `db_derived` check is (the analysis
   list, the step map, the analysis catalog, the results/headline maps, the
   annotation).
2. Its result for a fixture database with known tables.
3. NULL stays NULL: a table with no established fact is never counted as zero,
   and a database where nothing is comparable says so instead of ranking.
4. The question that cites it answers on a database, and the answer carries the
   check's provenance.
"""
from __future__ import annotations

import pytest
import yaml
from pathlib import Path

from resource_explorer.registry import (
    STATE_MEASURED,
    STATE_NOT_MEASURED,
    DatabaseEntity,
    ProjectRegistry,
)
from resource_explorer.surveyors.database.db_derived import (
    DB_DERIVED_ANALYSES,
    derive_hub_tables,
    load_inputs,
    run_db_derived,
)

NOW = "2026-10-02T12:00:00"
ROOT = Path(__file__).resolve().parent.parent
HUB_QUESTION = "Which tables would a consumer start with?"


@pytest.fixture
def registry(tmp_path):
    r = ProjectRegistry(db_path=str(tmp_path / "hub.db"))
    r.register_database(DatabaseEntity(
        slug="coco_ods", display_name="Coco ODS", db_type="postgresql",
        host="localhost", port=5442, database_name="coco_ods",
    ))
    return r


# ── row builders (same shapes `test_db_derived_step.py` uses) ───────────────

def _table(name, *, rows=1000, desc="", ttype="BASE TABLE", schema="public"):
    return {
        "schema_name": schema, "table_name": name, "table_type": ttype,
        "row_count": rows, "column_count": 5, "size_bytes": 8192,
        "description": desc, "state": STATE_MEASURED,
    }


def _column(table, name, *, pk=False, fk=None, desc="", schema="public",
            keys_captured=True):
    return {
        "schema_name": schema, "table_name": table, "column_name": name,
        "data_type": "integer", "base_type": "integer", "description": desc,
        "is_primary_key": (1 if pk else 0) if keys_captured else None,
        "foreign_key_json": fk, "state": STATE_MEASURED,
    }


def _fk(table, column="id", schema="public"):
    return {"foreign_schema": schema, "foreign_table": table, "foreign_column": column}


def _activity(table, *, seq=0, idx=0, null_counters=False, schema="public"):
    return {
        "schema_name": schema, "table_name": table,
        "rows_inserted": None if null_counters else 1,
        "rows_updated": None if null_counters else 1,
        "rows_deleted": None if null_counters else 0,
        "seq_scan": None if null_counters else seq,
        "idx_scan": None if null_counters else idx,
        "state": STATE_MEASURED,
    }


def _store(registry, *, tables=None, columns=None, activity=None, slug="coco_ods"):
    registry.record_database_survey(
        slug=slug, schema_count=1, table_count=len(tables or []),
        column_count=len(columns or []), survey_data={}, source="local",
        surveyed_at=NOW,
    )
    for name, rows in (("database_tables", tables), ("database_columns", columns),
                       ("database_table_activity", activity)):
        if rows is not None:
            registry.write_detail_rows(name, slug, NOW, source="local", rows=rows)


def _known_database(registry, **overrides):
    """customer <- orders <- order_line, with rows and reads that grow down the
    chain. Keys captured everywhere; no table or column carries a comment."""
    tables = [
        _table("customer", rows=50_000),
        _table("orders", rows=400_000),
        _table("order_line", rows=2_000_000),
    ]
    columns = [
        _column("customer", "customer_id", pk=True),
        _column("orders", "order_id", pk=True),
        _column("orders", "customer_id", fk=_fk("customer", "customer_id")),
        _column("order_line", "order_line_id", pk=True),
        _column("order_line", "order_id", fk=_fk("orders", "order_id")),
    ]
    activity = [
        _activity("customer", seq=12, idx=900_000),
        _activity("orders", seq=30, idx=4_000_000),
        _activity("order_line", seq=5, idx=8_000_000),
    ]
    spec = {"tables": tables, "columns": columns, "activity": activity}
    spec.update(overrides)
    _store(registry, **spec)


def _hub(registry):
    return derive_hub_tables(load_inputs(registry, "coco_ods"))


# ═══════════════════════════════════════════════════════════════════════════
# 1. Declared like the other db_derived checks
# ═══════════════════════════════════════════════════════════════════════════

class TestDeclaredLikeTheOtherChecks:
    def test_it_is_a_db_derived_analysis(self):
        assert "db_hub_tables" in DB_DERIVED_ANALYSES
        assert len(DB_DERIVED_ANALYSES) == len(set(DB_DERIVED_ANALYSES))

    def test_the_step_map_credits_it_to_db_derived(self):
        from resource_explorer.surveyors.database.survey_definition_adapter import (
            DATABASE_ANALYSIS_RE_STEP_MAP,
        )

        assert DATABASE_ANALYSIS_RE_STEP_MAP["db_hub_tables"] == ["db_derived"]

    def test_the_analysis_catalog_lists_it_for_databases_only(self):
        catalog = yaml.safe_load(
            (ROOT / "resource_explorer/configdata/analysis_catalog.yaml").read_text())
        entry = next(a for a in catalog["database_analyses"] if a["id"] == "db_hub_tables")
        assert entry["resource_types"] == ["database"]
        assert entry["intent"] == "discovery"
        assert entry["availability"] == "inline"
        assert entry["egeria_registration"]["candidate"] is False

    def test_it_has_a_results_reader_and_a_headline(self):
        from resource_explorer.surveyors.database.survey_definition_adapter import (
            DATABASE_ANALYSIS_CONTAINER_HEADLINE_MAP,
            DATABASE_ANALYSIS_HEADLINE_MAP,
            DATABASE_ANALYSIS_RESULTS_MAP,
        )

        reader, trend = DATABASE_ANALYSIS_RESULTS_MAP["db_hub_tables"]
        assert callable(reader) and trend is None
        assert "db_hub_tables" in DATABASE_ANALYSIS_HEADLINE_MAP
        assert "db_hub_tables" in DATABASE_ANALYSIS_CONTAINER_HEADLINE_MAP

    def test_run_db_derived_produces_it_and_one_annotation(self, registry):
        _known_database(registry)
        result = run_db_derived(registry, "coco_ods")
        assert result["derived"]["db_hub_tables"]["state"] == STATE_MEASURED
        hub = [a for a in result["annotations"] if a.check_name == "db_hub_tables"]
        assert len(hub) == 1
        assert hub[0].annotation_type_name == "db_hub_tables"

    def test_it_opens_no_connection(self, registry, monkeypatch):
        import resource_explorer.surveyors.database.connection as conn_mod

        def _explode(*a, **k):
            raise AssertionError("db_hub_tables opened a connection")

        monkeypatch.setattr(conn_mod, "database_connection", _explode)
        _known_database(registry)
        assert _hub(registry)["state"] == STATE_MEASURED


# ═══════════════════════════════════════════════════════════════════════════
# 2. The result for a fixture database with known tables
# ═══════════════════════════════════════════════════════════════════════════

class TestKnownTables:
    def test_the_ranking_follows_the_evidence(self, registry):
        _known_database(registry)
        hub = _hub(registry)
        assert hub["state"] == STATE_MEASURED
        assert hub["table_count"] == 3
        # orders: referenced by order_line, 400k rows, 4M reads. customer:
        # referenced by orders. order_line: nothing references it, but it is
        # the biggest and most read.
        assert [h["table"] for h in hub["hubs"]] == [
            "public.orders", "public.customer", "public.order_line",
        ]
        assert hub["signals_used"] == ["fk_in_degree", "reads", "rows"]

    def test_the_numbers_behind_each_entry_are_the_stored_ones(self, registry):
        _known_database(registry)
        by_name = {h["table"]: h for h in _hub(registry)["hubs"]}
        assert by_name["public.customer"]["fk_in_degree"] == 1
        assert by_name["public.orders"]["fk_in_degree"] == 1
        assert by_name["public.order_line"]["row_count"] == 2_000_000
        assert by_name["public.orders"]["reads"] == 30 + 4_000_000

    def test_a_real_zero_in_degree_is_zero_not_unknown(self, registry):
        """Keys were captured for every table, so "nothing references
        order_line" is a finding and must read 0, not None."""
        _known_database(registry)
        by_name = {h["table"]: h for h in _hub(registry)["hubs"]}
        assert by_name["public.order_line"]["fk_in_degree"] == 0

    def test_it_is_ordered_deterministically(self, registry):
        _known_database(registry)
        assert _hub(registry)["hubs"] == _hub(registry)["hubs"]

    def test_in_degree_counts_distinct_other_tables(self, registry):
        """Two FK columns from one table, and a self-reference, are not extra
        referrers."""
        columns = [
            _column("customer", "customer_id", pk=True),
            _column("customer", "referred_by", fk=_fk("customer", "customer_id")),
            _column("orders", "order_id", pk=True),
            _column("orders", "bill_to", fk=_fk("customer", "customer_id")),
            _column("orders", "ship_to", fk=_fk("customer", "customer_id")),
        ]
        _store(registry, tables=[_table("customer"), _table("orders")], columns=columns)
        by_name = {h["table"]: h for h in _hub(registry)["hubs"]}
        assert by_name["public.customer"]["fk_in_degree"] == 1

    def test_views_are_not_ranked(self, registry):
        tables = [_table("customer"), _table("v_customer", ttype="VIEW")]
        columns = [_column("customer", "customer_id", pk=True),
                   _column("v_customer", "customer_id", pk=True)]
        _store(registry, tables=tables, columns=columns)
        hub = _hub(registry)
        assert hub["table_count"] == 1
        assert all(h["table"] != "public.v_customer" for h in hub["hubs"])

    def test_a_comment_counts_only_once_comments_exist(self, registry):
        tables = [_table("customer", desc="Who we sell to"), _table("orders")]
        columns = [_column("customer", "customer_id", pk=True),
                   _column("orders", "order_id", pk=True)]
        _store(registry, tables=tables, columns=columns)
        hub = _hub(registry)
        assert "comment" in hub["signals_used"]
        by_name = {h["table"]: h for h in hub["hubs"]}
        assert by_name["public.customer"]["has_comment"] is True
        assert by_name["public.orders"]["has_comment"] is False

    def test_every_table_listed_has_a_nonzero_score_and_the_list_is_capped(self, registry):
        tables = [_table(f"t{i:02d}", rows=1000 + i) for i in range(15)]
        columns = [_column(f"t{i:02d}", "id", pk=True) for i in range(15)]
        _store(registry, tables=tables, columns=columns)
        hub = _hub(registry)
        assert len(hub["hubs"]) == 10
        assert hub["ranked_count"] == 15
        assert all(h["score"] > 0 for h in hub["hubs"])

    def test_the_provenance_names_the_rows_it_read(self, registry):
        _known_database(registry)
        prov = _hub(registry)["provenance"]
        assert prov["read_snapshot"] == NOW
        assert prov["table_surveyed_at"]["database_tables"] == NOW


# ═══════════════════════════════════════════════════════════════════════════
# 3. NULL stays NULL
# ═══════════════════════════════════════════════════════════════════════════

class TestNullStaysNull:
    def test_never_surveyed_is_not_measured_not_an_empty_ranking(self, registry):
        hub = _hub(registry)
        assert hub["state"] == STATE_NOT_MEASURED
        assert hub["reason"] == "no_schema_rows"
        assert hub["hubs"] == []

    def test_null_counters_are_not_zero_reads(self, registry):
        """Statistics never collected: every counter NULL. The reads signal
        must be left out and named, and no table may read `reads == 0`."""
        _known_database(registry, activity=[
            _activity("customer", null_counters=True),
            _activity("orders", null_counters=True),
            _activity("order_line", null_counters=True),
        ])
        hub = _hub(registry)
        assert "reads" not in hub["signals_used"]
        assert "reads" in [n["signal"] for n in hub["signals_not_used"]]
        assert all(h["reads"] is None for h in hub["hubs"])
        assert "never counted as zero" in hub["explanation"]

    def test_a_table_with_no_activity_row_is_not_zero_reads(self, registry):
        _known_database(registry, activity=[
            _activity("customer", seq=1, idx=10),
            _activity("orders", seq=1, idx=10),
        ])
        hub = _hub(registry)
        assert "reads" not in hub["signals_used"]
        by_name = {h["table"]: h for h in hub["hubs"]}
        assert by_name["public.order_line"]["reads"] is None

    def test_a_null_row_count_is_not_zero_rows_and_does_not_hide_the_table(self, registry):
        tables = [_table("customer", rows=50_000), _table("orders", rows=None),
                  _table("order_line", rows=2_000_000)]
        _known_database(registry, tables=tables)
        hub = _hub(registry)
        assert "rows" not in hub["signals_used"]
        not_used = {n["signal"]: n for n in hub["signals_not_used"]}
        assert not_used["rows"]["tables_without"] == 1
        by_name = {h["table"]: h for h in hub["hubs"]}
        # Still listed, on the evidence that exists, and its row count is None.
        assert by_name["public.orders"]["row_count"] is None
        assert hub["hubs"][0]["table"] == "public.orders"

    def test_keys_never_captured_is_not_nothing_references_it(self, registry):
        columns = [_column(t, "id", keys_captured=False)
                   for t in ("customer", "orders", "order_line")]
        _known_database(registry, columns=columns)
        hub = _hub(registry)
        assert "fk_in_degree" not in hub["signals_used"]
        assert all(h["fk_in_degree"] is None for h in hub["hubs"])

    def test_one_table_with_uncaptured_keys_makes_no_table_a_zero(self, registry):
        """A referrer whose keys were never captured could reference any
        table, so 0 is not a finding for any of them."""
        columns = [
            _column("customer", "customer_id", pk=True),
            _column("orders", "order_id", pk=True),
            _column("orders", "customer_id", fk=_fk("customer", "customer_id")),
            _column("order_line", "order_line_id", keys_captured=False),
        ]
        _known_database(registry, columns=columns)
        hub = _hub(registry)
        assert "fk_in_degree" not in hub["signals_used"]
        assert all(h["fk_in_degree"] is None for h in hub["hubs"])

    def test_no_comments_anywhere_is_not_undocumented(self, registry):
        _known_database(registry)
        hub = _hub(registry)
        assert "comment" not in hub["signals_used"]
        assert all(h["has_comment"] is None for h in hub["hubs"])

    def test_nothing_comparable_says_so_instead_of_ranking(self, registry):
        tables = [_table("a", rows=None), _table("b", rows=None)]
        columns = [_column("a", "id", keys_captured=False),
                   _column("b", "id", keys_captured=False)]
        _store(registry, tables=tables, columns=columns)
        hub = _hub(registry)
        assert hub["state"] == STATE_NOT_MEASURED
        assert hub["reason"] == "no_comparable_signal"
        assert hub["hubs"] == []
        assert "NOT a finding" in hub["explanation"]

    def test_the_not_measured_annotation_is_unverified_not_a_ranking(self, registry):
        tables = [_table("a", rows=None)]
        columns = [_column("a", "id", keys_captured=False)]
        _store(registry, tables=tables, columns=columns)
        result = run_db_derived(registry, "coco_ods")
        hub = [a for a in result["annotations"] if a.check_name == "db_hub_tables"][0]
        assert hub.label == "unverified"
        assert hub.confidence == 0

    def test_all_zero_on_every_established_signal_is_a_finding_not_a_gap(self, registry):
        tables = [_table("a", rows=0), _table("b", rows=0)]
        columns = [_column("a", "id", pk=True), _column("b", "id", pk=True)]
        _store(registry, tables=tables, columns=columns)
        hub = _hub(registry)
        assert hub["state"] == STATE_MEASURED
        assert hub["hubs"] == []
        assert "none stands out" in hub["explanation"]


# ═══════════════════════════════════════════════════════════════════════════
# 4. The question that cites it
# ═══════════════════════════════════════════════════════════════════════════

def _database_question():
    from resource_explorer.surveyors.question_catalog_reader import get_questions

    return next(q for q in get_questions("database") if q["question"] == HUB_QUESTION)


class TestTheQuestionThatCitesIt:
    def test_the_catalog_row_cites_the_check_and_is_no_longer_a_gap(self):
        q = _database_question()
        assert q["answering"]["kind"] == "analysis"
        assert q["answering"]["analysis_ids"] == ["db_hub_tables"]

    def test_it_answers_on_a_database_and_carries_the_checks_provenance(self, registry):
        from resource_explorer.facts import FactLayer
        from resource_explorer.surveyors.result_status import MEASURED

        _known_database(registry)
        env = FactLayer(registry, resource_type="database").answer(
            "coco_ods", _database_question())
        assert env.answerable
        assert env.blocked_reason == ""
        assert not env.level_mismatch, env.level_note
        [fact] = env.facts
        assert fact.analysis_id == "db_hub_tables"
        assert fact.state == MEASURED
        assert fact.provenance == "measured"
        # What the answer names, and what it was read from.
        assert "public.orders" in fact.headline
        assert fact.value["provenance"]["read_snapshot"] == NOW
        assert fact.value["signals_used"] == ["fk_in_degree", "reads", "rows"]

    def test_through_the_route_a_database_question_reaches_the_database_maps(
            self, registry, monkeypatch):
        from resource_explorer.web.routes import analyses as routes

        _known_database(registry)
        monkeypatch.setattr("resource_explorer.registry.ProjectRegistry",
                            lambda *a, **k: registry)
        out = routes.answer_question("coco_ods", HUB_QUESTION, entity_type="database")
        assert out["answerable"] is True
        assert out["facts"][0]["analysis_id"] == "db_hub_tables"

    def test_a_never_surveyed_database_is_not_answered(self, registry):
        from resource_explorer.facts import FactLayer

        env = FactLayer(registry, resource_type="database").answer(
            "coco_ods", _database_question())
        assert not env.answerable
        assert env.facts[0].is_known is False

    def test_a_database_with_nothing_comparable_is_not_answered_with_a_list(self, registry):
        from resource_explorer.facts import FactLayer

        tables = [_table("a", rows=None)]
        columns = [_column("a", "id", keys_captured=False)]
        _store(registry, tables=tables, columns=columns)
        env = FactLayer(registry, resource_type="database").answer(
            "coco_ods", _database_question())
        assert not env.answerable
