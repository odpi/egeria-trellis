"""Tests for DatabaseSurveyor.survey(steps=...) — the database per-card
dispatch fix (D6 prerequisite, docs/repo-scope-narrowing-funnel.md). Before
this, every local database analysis card triggered the identical whole-DB
survey (schema + statistics + views) regardless of which was clicked."""
from __future__ import annotations

from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import pytest

from resource_explorer.registry import DatabaseEntity, ProjectRegistry
from resource_explorer.surveyors.database.connection import EngineCapabilities
from resource_explorer.surveyors.database.database_surveyor import (
    DATABASE_SURVEYOR_STEP_MAP,
    DatabaseSurveyor,
)


@pytest.fixture
def registry(tmp_path):
    return ProjectRegistry(db_path=str(tmp_path / "test.db"))


@pytest.fixture
def db_entity(registry):
    entity = DatabaseEntity(
        slug="mydb", display_name="My DB", db_type="postgresql",
        host="localhost", port=5432, database_name="mydb",
        db_user="admin", db_password="secret",
    )
    registry.register_database(entity)
    return entity


def _mock_conn():
    conn = MagicMock()
    conn.get_schema_info.return_value = {"schemas": [], "total_tables": 0, "total_columns": 0}
    conn.get_statistics.return_value = {
        "row_stats": [], "table_stats": [],
        "column_stats": [], "table_activity": [], "index_stats": [],
        "stats_reset": "",
    }
    conn.capabilities = EngineCapabilities(
        column_stats=True, tuple_counters=True, index_stats=True,
    )
    return conn


@contextmanager
def _patched_connection(conn):
    with patch(
        "resource_explorer.surveyors.database.database_surveyor.database_connection",
    ) as mock_ctx:
        mock_ctx.return_value.__enter__.return_value = conn
        mock_ctx.return_value.__exit__.return_value = False
        yield


class TestStepsNone:
    def test_runs_schema_statistics_and_views(self, registry, db_entity):
        conn = _mock_conn()
        surveyor = DatabaseSurveyor(db_entity, {"user": "admin", "password": "secret"}, registry)
        with _patched_connection(conn), \
             patch.object(surveyor, "_survey_views", return_value=[]) as mock_views:
            surveyor.survey()
        conn.get_schema_info.assert_called_once()
        conn.get_statistics.assert_called_once()
        mock_views.assert_called_once()


class TestStepsFiltered:
    def test_schema_inventory_steps_skip_statistics(self, registry, db_entity):
        conn = _mock_conn()
        surveyor = DatabaseSurveyor(db_entity, {"user": "admin", "password": "secret"}, registry)
        with _patched_connection(conn), \
             patch.object(surveyor, "_survey_views", return_value=[]) as mock_views:
            surveyor.survey(steps=DATABASE_SURVEYOR_STEP_MAP["schema_inventory"])
        conn.get_schema_info.assert_called_once()  # "schema" always runs
        conn.get_statistics.assert_not_called()
        mock_views.assert_called_once()

    def test_row_count_snapshot_steps_skip_views(self, registry, db_entity):
        conn = _mock_conn()
        surveyor = DatabaseSurveyor(db_entity, {"user": "admin", "password": "secret"}, registry)
        with _patched_connection(conn), \
             patch.object(surveyor, "_survey_views", return_value=[]) as mock_views:
            surveyor.survey(steps=DATABASE_SURVEYOR_STEP_MAP["row_count_snapshot"])
        conn.get_schema_info.assert_called_once()
        conn.get_statistics.assert_called_once()
        mock_views.assert_not_called()

    def test_schema_always_runs_even_if_omitted_from_steps(self, registry, db_entity):
        conn = _mock_conn()
        surveyor = DatabaseSurveyor(db_entity, {"user": "admin", "password": "secret"}, registry)
        with _patched_connection(conn), \
             patch.object(surveyor, "_survey_views", return_value=[]) as mock_views:
            surveyor.survey(steps=["statistics"])  # "schema" deliberately omitted
        conn.get_schema_info.assert_called_once()
        conn.get_statistics.assert_called_once()
        mock_views.assert_not_called()


class TestColumnProfileLoadsReferenceCatalog:
    """`data_class_match`/`reference_data_match`'s "Run ->" button goes
    through DatabaseSurveyor.survey(steps=[...,"column_profile"]), a
    different door than survey_definition_adapter._run_postgres_column_profile
    — see database_surveyor.py's bootstrap fix. Before that fix, survey()
    accepted a `reference_catalog` parameter nothing ever passed, so this
    path always ran column_profile_step with reference_catalog=None and every
    column came back `no_candidates`/"not established" regardless of what
    Egeria actually held. These tests pin that survey() now loads one itself,
    via the SAME `load_reference_catalog`/`build_reference_clients` functions
    the Survey Definition path uses — not a second, divergent implementation.
    """

    def _run_with_column_profile(self, registry, db_entity, **survey_kwargs):
        conn = _mock_conn()
        surveyor = DatabaseSurveyor(db_entity, {"user": "admin", "password": "secret"}, registry)
        with _patched_connection(conn), \
             patch(
                 "resource_explorer.surveyors.database.column_profile_step.run_column_profile"
             ) as mock_profile:
            mock_profile.return_value = {
                "column_profile_rows": [], "annotations": [],
            }
            surveyor.survey(steps=["column_profile"], **survey_kwargs)
        return mock_profile

    def test_loads_catalog_when_none_supplied(self, registry, db_entity):
        fake_catalog = object()
        with patch(
            "resource_explorer.surveyors.database.egeria_reference_catalog.build_reference_clients",
            return_value=("designer", "ref_manager"),
        ) as mock_build, patch(
            "resource_explorer.surveyors.database.egeria_reference_catalog.load_reference_catalog",
            return_value=fake_catalog,
        ) as mock_load:
            mock_profile = self._run_with_column_profile(registry, db_entity)

        mock_build.assert_called_once()
        mock_load.assert_called_once_with("designer", "ref_manager")
        assert mock_profile.call_args.kwargs["reference_catalog"] is fake_catalog

    def test_explicit_reference_catalog_is_not_overridden(self, registry, db_entity):
        explicit_catalog = object()
        with patch(
            "resource_explorer.surveyors.database.egeria_reference_catalog.build_reference_clients",
        ) as mock_build, patch(
            "resource_explorer.surveyors.database.egeria_reference_catalog.load_reference_catalog",
        ) as mock_load:
            mock_profile = self._run_with_column_profile(
                registry, db_entity, reference_catalog=explicit_catalog,
            )

        mock_build.assert_not_called()
        mock_load.assert_not_called()
        assert mock_profile.call_args.kwargs["reference_catalog"] is explicit_catalog

    def test_read_egeria_catalog_false_skips_loading(self, registry, db_entity):
        with patch(
            "resource_explorer.surveyors.database.egeria_reference_catalog.build_reference_clients",
        ) as mock_build, patch(
            "resource_explorer.surveyors.database.egeria_reference_catalog.load_reference_catalog",
        ) as mock_load:
            mock_profile = self._run_with_column_profile(
                registry, db_entity, read_egeria_catalog=False,
            )

        mock_build.assert_not_called()
        mock_load.assert_not_called()
        assert mock_profile.call_args.kwargs["reference_catalog"] is None

    def test_catalog_load_failure_is_non_fatal_and_recorded(self, registry, db_entity):
        with patch(
            "resource_explorer.surveyors.database.egeria_reference_catalog.build_reference_clients",
            side_effect=RuntimeError("EGERIA_PLATFORM_URL is not set"),
        ):
            conn = _mock_conn()
            surveyor = DatabaseSurveyor(
                db_entity, {"user": "admin", "password": "secret"}, registry,
            )
            with _patched_connection(conn), patch(
                "resource_explorer.surveyors.database.column_profile_step.run_column_profile",
                return_value={"column_profile_rows": [], "annotations": []},
            ) as mock_profile:
                result = surveyor.survey(steps=["column_profile"])

        assert mock_profile.call_args.kwargs["reference_catalog"] is None
        assert any(
            "EGERIA_PLATFORM_URL is not set" in err for err in result["errors"]
        )


class TestDatabaseAnalysisStepMap:
    def test_maps_all_local_survey_ids(self):
        # Phase 1 slice 8 (postgres_operations) added db_activity_signals/
        # db_resilience/db_external_dependencies and gave privilege_audit its
        # own dedicated "operations" step — see the next test.
        # Phase 1 slice 10 (postgres_column_profile) added data_class_match/
        # reference_data_match and the "column_profile" step they map to —
        # see test_column_profile_backed_ids_also_need_statistics below.
        # Phase 1 slice 11 (postgres_nested_columns) added
        # nested_column_profile and the "nested_columns" step it maps to,
        # gated on slice 10's sampling infrastructure the same way.
        # credential_capability (design REPLY-DATABASE-CREDENTIAL-CAPABILITY-
        # VISIBILITY.md §3/§4, replying to ASK-...-#251) added the
        # credential_capability id and its own opt-in step.
        assert set(DATABASE_SURVEYOR_STEP_MAP) == {
            "schema_inventory", "row_count_snapshot", "privilege_audit",
            "db_activity_signals", "db_resilience", "db_external_dependencies",
            "data_class_match", "reference_data_match", "nested_column_profile",
            "credential_capability", "sql_analysis",
        }

    def test_privilege_audit_now_runs_the_dedicated_operations_step(self):
        # Superseded Phase 1 slice 8 (postgres_operations, design §5.4/§5.7):
        # privilege_audit is no longer "aspirational" — it has a real,
        # dedicated check (pg_roles/role_table_grants/pg_default_acl, RFA on
        # PUBLIC grants) and no longer needs to run the full survey.
        assert set(DATABASE_SURVEYOR_STEP_MAP["privilege_audit"]) == {"schema", "operations"}

    def test_new_operations_backed_ids_map_to_schema_and_operations(self):
        for analysis_id in ("db_activity_signals", "db_resilience", "db_external_dependencies"):
            assert set(DATABASE_SURVEYOR_STEP_MAP[analysis_id]) == {"schema", "operations"}

    def test_column_profile_backed_ids_also_need_statistics(self):
        # Phase 1 slice 10. "statistics" is not optional for these two:
        # reference_data_match's low-cardinality gate reads slice 7's stored
        # pg_stats n_distinct, and every sample's provenance is stated against
        # the per-table row counts "statistics" collects (design §5.8's "of
        # 4.2M rows"). Without it the step runs and establishes nothing.
        for analysis_id in ("data_class_match", "reference_data_match"):
            assert set(DATABASE_SURVEYOR_STEP_MAP[analysis_id]) == {
                "schema", "statistics", "column_profile",
            }

    def test_nested_column_profile_also_needs_statistics(self):
        # Phase 1 slice 11, same reasoning as slice 10 above: the sample's
        # provenance is stated against "statistics"'s per-table row counts.
        assert set(DATABASE_SURVEYOR_STEP_MAP["nested_column_profile"]) == {
            "schema", "statistics", "nested_columns",
        }


class TestColumnProfileVerdictsAreStored:
    """The column-profile step's verdicts (data_class_match, reference_data_match) are written to
    database_column_matches when the step completes, and read back by the analyses' readers. Before
    2026-10-20 they were built and kept nowhere locally, so both analyses read "no results view yet"."""

    _MATCHES = (
        {"schema": "public", "table": "customer", "column": "email", "column_path": "public.customer.email",
         "question": "data_class_match", "verdict": "matched", "established": True, "confidence": 92,
         "evidence": "pattern", "matched_guid": "dc-1", "matched_qualified_name": "DataClass::email",
         "matched_display_name": "Email address", "matched_element_is_draft": False,
         "sampled_conformance": 0.98, "value_coverage": None, "unmatched_values": [], "detected_patterns": [],
         "privacy_relevant": True, "proposed_specification": "", "proposed_values": [], "other_candidates": [],
         "not_established_reason": "", "unusable_specifications": [],
         "sample": {"strategy": "random", "sample_rows": 40, "total_rows": 4200000, "seed": 7},
         "statement": "matches Email address in 98% of 40 sampled values"},
        {"schema": "public", "table": "customer", "column": "avatar", "column_path": "public.customer.avatar",
         "question": "data_class_match", "verdict": "not_sampled", "established": False, "confidence": 0,
         "evidence": "", "matched_guid": "", "matched_qualified_name": "", "matched_display_name": "",
         "matched_element_is_draft": False, "sampled_conformance": None, "value_coverage": None,
         "unmatched_values": [], "detected_patterns": [], "privacy_relevant": False,
         "proposed_specification": "", "proposed_values": [], "other_candidates": [],
         "not_established_reason": "bytea values are not sampled", "unusable_specifications": [],
         "sample": None, "statement": "not sampled: bytea values are not sampled"},
        {"schema": "public", "table": "customer", "column": "country", "column_path": "public.customer.country",
         "question": "reference_data_match", "verdict": "no_match", "established": True, "confidence": 70,
         "evidence": "", "matched_guid": "", "matched_qualified_name": "", "matched_display_name": "",
         "matched_element_is_draft": False, "sampled_conformance": None, "value_coverage": 0.1,
         "unmatched_values": ["XX"], "detected_patterns": [], "privacy_relevant": False,
         "proposed_specification": "", "proposed_values": [], "other_candidates": [],
         "not_established_reason": "", "unusable_specifications": [], "sample": None,
         "statement": "no reference set covers these values"},
    )

    def _survey(self, registry, db_entity, profile_result=None, raises=None, steps=("column_profile",)):
        conn = _mock_conn()
        surveyor = DatabaseSurveyor(db_entity, {"user": "admin", "password": "secret"}, registry)
        with _patched_connection(conn), \
             patch("resource_explorer.surveyors.database.column_profile_step.run_column_profile") as mock_profile:
            if raises is not None:
                mock_profile.side_effect = raises
            else:
                mock_profile.return_value = profile_result
            return surveyor.survey(steps=list(steps), reference_catalog=object())

    def test_every_verdict_is_stored_and_read_back(self, registry, db_entity):
        from resource_explorer.surveyors.database.survey_definition_adapter import (
            DATABASE_ANALYSIS_RESULTS_MAP,
        )

        self._survey(registry, db_entity, {"column_profile_rows": [], "annotations": [],
                                           "matches": list(self._MATCHES)})
        rows = registry.query_detail_rows("database_column_matches", db_entity.slug)
        assert {(r["column_name"], r["question"]) for r in rows} == {
            ("email", "data_class_match"), ("avatar", "data_class_match"), ("country", "reference_data_match")}

        reader, _ = DATABASE_ANALYSIS_RESULTS_MAP["data_class_match"]
        got = reader(registry, db_entity.slug)
        assert got["column_count"] == 2 and got["established_count"] == 1
        assert got["verdict_counts"] == {"matched": 1, "not_sampled": 1}
        email = next(c for c in got["columns"] if c["column_path"] == "public.customer.email")
        assert email["matched_display_name"] == "Email address" and email["privacy_relevant"] is True
        assert email["sample"]["total_rows"] == 4200000
        # A column no value was sampled from is a stored verdict, not established, never "no match".
        avatar = next(c for c in got["columns"] if c["column_path"] == "public.customer.avatar")
        assert avatar["verdict"] == "not_sampled" and avatar["established"] is False
        assert avatar["state"] == "not_measured"

        reader, _ = DATABASE_ANALYSIS_RESULTS_MAP["reference_data_match"]
        assert reader(registry, db_entity.slug)["verdict_counts"] == {"no_match": 1}

    def test_a_completed_step_with_no_columns_records_coverage_but_no_rows(self, registry, db_entity):
        from resource_explorer.registry import SECTION_COLUMN_MATCHES

        self._survey(registry, db_entity, {"column_profile_rows": [], "annotations": [], "matches": []})
        assert registry.query_detail_rows("database_column_matches", db_entity.slug) == []
        assert SECTION_COLUMN_MATCHES in registry.get_section_coverage("database", db_entity.slug)

    def test_a_failed_step_stores_nothing_not_an_empty_answer(self, registry, db_entity):
        from resource_explorer.registry import SECTION_COLUMN_MATCHES
        from resource_explorer.surveyors.database.survey_definition_adapter import (
            DATABASE_ANALYSIS_RESULTS_MAP,
        )

        self._survey(registry, db_entity, raises=RuntimeError("sampling failed"))
        assert registry.query_detail_rows("database_column_matches", db_entity.slug) == []
        assert SECTION_COLUMN_MATCHES not in registry.get_section_coverage("database", db_entity.slug)
        reader, _ = DATABASE_ANALYSIS_RESULTS_MAP["data_class_match"]
        assert reader(registry, db_entity.slug) == {}

    def test_a_survey_without_the_step_stores_nothing(self, registry, db_entity):
        from resource_explorer.registry import SECTION_COLUMN_MATCHES

        self._survey(registry, db_entity, steps=("schema",))
        assert registry.query_detail_rows("database_column_matches", db_entity.slug) == []
        assert SECTION_COLUMN_MATCHES not in registry.get_section_coverage("database", db_entity.slug)
