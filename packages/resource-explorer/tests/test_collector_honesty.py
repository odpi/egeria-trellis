"""Collector-honesty design ruling (2026-09-26, SLICE-17C-RENDERABLE-ANSWER-GATE-IMPLEMENTED.md's "Live gate follow-ups" section): a
collector that catches an exception records it on its own section
(`_errors`), rather than only silently degrading to an empty list or a
zero -- and a reader renders a recorded error as "collection failed:
<reason> — re-run", never as if the empty default were a real measurement.

Trigger: `get_privilege_audit()`'s roles query broke on every single run
(an unescaped `%`, fixed in slice 17c) and its own `try/except` silently
turned that into `roles = []`, rendered as "0 role(s); 0 superuser(s)" —
a confident wrong answer indistinguishable from a real audit finding. This
generalizes the fix beyond that one field: every operations-family
collector with a headline reader now records what actually went wrong.

No live Postgres is used — each collector is exercised directly against a
`PostgreSQLConnection` with `execute_query` swapped for a fake that raises
on demand.
"""
from __future__ import annotations

from resource_explorer.surveyors.database.connection import PostgreSQLConnection
from resource_explorer.surveyors.database.survey_definition_adapter import (
    _collection_failed_headline,
    _db_external_dependencies_headline,
    _db_privilege_audit_headline,
    _db_resilience_headline,
    _merge_collector_errors,
)


class _FakeRegistry:
    """Answers `get_latest_database_survey` from a canned `survey_data`
    dict, the same shape `_operations_section_reader` reads."""

    def __init__(self, survey_data: dict):
        import json

        self._blob = json.dumps(survey_data)

    def get_latest_database_survey(self, slug):
        return {"survey_data": self._blob}


class _RaisingConnection(PostgreSQLConnection):
    """A real `PostgreSQLConnection` whose `execute_query` raises for any
    query matching one of the given substrings, and otherwise returns an
    empty list -- enough to exercise a collector's own try/except without a
    live database."""

    def __init__(self, raise_on: list[str]):
        super().__init__(host="localhost", port=5432, database="x", user="u", password="p")
        self._raise_on = raise_on

    def execute_query(self, query, params=()):
        if any(marker in query for marker in self._raise_on):
            raise RuntimeError("connection reset by peer")
        return []


class TestMergeCollectorErrors:
    def test_combines_errors_from_several_sections(self):
        merged = _merge_collector_errors(
            {"a": 1, "_errors": {"x": "boom"}},
            {"b": 2},
            {"_errors": {"y": "also boom"}},
        )
        assert merged == {"x": "boom", "y": "also boom"}

    def test_no_errors_anywhere_is_an_empty_dict(self):
        assert _merge_collector_errors({"a": 1}, {"b": 2}, None) == {}


class TestCollectionFailedHeadline:
    def test_names_the_field_and_the_reason(self):
        result = _collection_failed_headline({"roles": "tuple index out of range"})
        assert result["label"] == "Collection failed (roles): tuple index out of range — re-run."
        assert result["status"] == "error"


class TestGetPrivilegeAuditRecordsErrorsPerField:
    def test_a_broken_roles_query_is_recorded_not_silently_emptied(self):
        conn = _RaisingConnection(raise_on=["FROM pg_roles"])
        result = conn.get_privilege_audit()
        assert result["roles"] == []
        assert result["_errors"] == {"roles": "connection reset by peer"}

    def test_all_three_fields_succeeding_carries_no_errors_key(self):
        conn = _RaisingConnection(raise_on=[])
        result = conn.get_privilege_audit()
        assert "_errors" not in result


class TestGetExternalDependenciesRecordsErrorsPerField:
    def test_a_broken_extensions_query_is_recorded(self):
        conn = _RaisingConnection(raise_on=["FROM pg_extension"])
        result = conn.get_external_dependencies()
        assert result["extensions"] == []
        assert result["_errors"] == {"extensions": "connection reset by peer"}

    def test_a_broken_subscriptions_query_does_not_hide_the_others(self):
        conn = _RaisingConnection(raise_on=["FROM pg_subscription"])
        result = conn.get_external_dependencies()
        assert result["_errors"] == {"subscriptions": "connection reset by peer"}
        # The other four fields, none of which touch pg_subscription,
        # still measured successfully.
        assert "extensions" in result and "foreign_servers" in result


class TestGetReplicationStatusRecordsErrors:
    def test_a_broken_replicas_query_is_recorded(self):
        conn = _RaisingConnection(raise_on=["FROM pg_stat_replication"])
        result = conn.get_replication_status()
        assert result["replicas"] == []
        assert result["_errors"] == {"replicas": "connection reset by peer"}


class TestGetWalArchivingStatusRecordsErrors:
    def test_a_broken_archiver_stats_query_is_recorded(self):
        conn = _RaisingConnection(raise_on=["FROM pg_stat_archiver"])
        result = conn.get_wal_archiving_status()
        assert result["_errors"] == {"archiver_stats": "connection reset by peer"}


class TestGetBackupToolSignalsRecordsErrors:
    def test_a_broken_extension_query_is_recorded(self):
        conn = _RaisingConnection(raise_on=["FROM pg_extension"])
        result = conn.get_backup_tool_signals()
        assert result["detected_extensions"] == []
        assert result["_errors"] == {"detected_extensions": "connection reset by peer"}


class TestGetClusteringInfoRecordsErrors:
    def test_a_broken_citus_query_is_recorded(self):
        conn = _RaisingConnection(raise_on=["FROM pg_extension WHERE extname = 'citus'"])
        result = conn.get_clustering_info()
        assert result["citus_detected"] is False
        assert result["_errors"] == {"citus_detected": "connection reset by peer"}


class TestResilienceHeadlineRendersCollectionFailure:
    def test_a_failure_in_any_of_the_four_sections_fails_the_whole_headline(self):
        survey_data = {"operations": {"resilience": {
            "replication": {"is_in_recovery": False, "replicas": [],
                             "_errors": {"replicas": "connection reset"}},
            "wal_archiving": {"archive_mode": "off"},
            "backup_tool_signals": {"detected_extensions": []},
            "clustering": {"citus_detected": False, "citus_version": None},
        }}}
        registry = _FakeRegistry(survey_data)
        result = _db_resilience_headline(registry, "coco_ods")
        assert result["status"] == "error"
        assert "Collection failed (replicas)" in result["label"]
        assert "re-run" in result["label"]

    def test_no_errors_renders_the_normal_sentence(self):
        survey_data = {"operations": {"resilience": {
            "replication": {"is_in_recovery": False, "replicas": []},
            "wal_archiving": {"archive_mode": "off"},
            "backup_tool_signals": {"detected_extensions": []},
            "clustering": {"citus_detected": False, "citus_version": None},
        }}}
        registry = _FakeRegistry(survey_data)
        result = _db_resilience_headline(registry, "coco_ods")
        assert result["status"] == "info"
        assert "primary" in result["label"].lower()


class TestExternalDependenciesHeadlineRendersCollectionFailure:
    def test_a_recorded_error_fails_the_headline_rather_than_showing_zero_counts(self):
        survey_data = {"operations": {"external_dependencies": {
            "extensions": [], "foreign_servers": [], "foreign_tables": [],
            "publications": [], "subscriptions": [],
            "_errors": {"extensions": "connection reset"},
        }}}
        registry = _FakeRegistry(survey_data)
        result = _db_external_dependencies_headline(registry, "coco_ods")
        assert result["status"] == "error"
        assert "Collection failed (extensions)" in result["label"]


class TestPrivilegeAuditHeadlineRendersCollectionFailureBeforeTheHonestFloor:
    """A recorded error must win over the (already correct) "roles empty ->
    None" honest-absence floor -- a caller that can tell WHY roles is empty
    should say so, rather than falling back to the generic no-summary-reader
    state a plain empty list gets."""

    def test_a_recorded_roles_error_renders_collection_failed_not_none(self):
        survey_data = {"operations": {"privilege_audit": {
            "roles": [], "table_grants": [{"table_schema": "public", "table_name": "orders",
                                            "grantee": "PUBLIC", "privilege_type": "SELECT"}],
            "default_acl": [],
            "_errors": {"roles": "connection reset"},
        }}}
        registry = _FakeRegistry(survey_data)
        result = _db_privilege_audit_headline(registry, "coco_ods")
        assert result is not None
        assert result["status"] == "error"
        assert "Collection failed (roles)" in result["label"]

    def test_empty_roles_with_no_recorded_error_still_falls_to_none(self):
        """Unrelated to a recorded failure -- e.g. a stub registry the real
        _read_results None-guard already covers -- stays the pre-existing
        honest-absence behavior."""
        survey_data = {"operations": {"privilege_audit": {
            "roles": [], "table_grants": [], "default_acl": [],
        }}}
        registry = _FakeRegistry(survey_data)
        assert _db_privilege_audit_headline(registry, "coco_ods") is None
