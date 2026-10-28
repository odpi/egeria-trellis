"""Slice 17c (SLICE-17B-RENDER-BOUND-LEVEL-GATE-IMPLEMENTED.md's
follow-up): the render-bound rule slice 17b applied only to sub-resource-level
questions is generalized to every level. A known fact with nothing renderable
-- no `headline`, no `value.detail`/`summary`/`description` prose, and a
`scalarMeasures()`-equivalent fallback that finds nothing because every field
is a list/dict -- must never sit under a ✓, at ANY level, not only a
container/member/field one.

Trigger: the live `coco_pharma` gate for slice 17b found the identical defect
one level up from the one 17b fixed. "Is this database a primary or a
replica..." is a plain `resource`-level question -- exempt from 17b's
sub-resource check entirely -- and rendered a ✓ with NO answer text at all,
because `db_resilience` has no `headline_reader` and its value
(`replication`/`wal_archiving`/`backup_tool_signals`/`clustering`) is four
nested dicts, none of which `scalarMeasures()`'s fallback can say anything
about (it skips every list/object field by design). `db_external_dependencies`
has the identical shape (every field is a list). `db_activity_signals` does
NOT -- it carries two real scalar fields (`stats_reset`, `table_count`)
alongside its one list -- so it was never silently empty, only thin; this
slice's headline for it improves the answer without needing the gate's help.
"""
from __future__ import annotations

import pytest

from resource_explorer.facts import Envelope, Fact, FactLayer, clear_target_shape_cache
from resource_explorer.surveyors.result_status import MEASURED


@pytest.fixture(autouse=True)
def _clear_shape_cache():
    clear_target_shape_cache()
    yield
    clear_target_shape_cache()


def _fact_layer(monkeypatch, catalog: list[dict] | None = None) -> FactLayer:
    monkeypatch.setattr(
        "resource_explorer.surveyors.analysis_catalog_reader.get_analyses",
        lambda resource_type, **kwargs: (catalog or []),
    )
    fl = FactLayer.__new__(FactLayer)  # skip __init__ -- no registry needed here
    fl.resource_type = "database"
    return fl


def _envelope(*facts: Fact) -> Envelope:
    env = Envelope(subject="coco_ods")
    env.facts = list(facts)
    return env


class TestRendersText:
    """Direct unit tests of `FactLayer._renders_text`, the primitive the
    generalized gate is built on."""

    def test_a_headline_renders(self):
        fl = FactLayer.__new__(FactLayer)
        f = Fact(analysis_id="x", state=MEASURED, value={}, headline="Primary; no replicas.")
        assert fl._renders_text(f) is True

    def test_prose_in_value_detail_renders(self):
        fl = FactLayer.__new__(FactLayer)
        f = Fact(analysis_id="x", state=MEASURED, value={"detail": "Some real prose."})
        assert fl._renders_text(f) is True

    def test_a_plain_scalar_field_renders(self):
        fl = FactLayer.__new__(FactLayer)
        f = Fact(analysis_id="x", state=MEASURED, value={"table_count": 56})
        assert fl._renders_text(f) is True

    def test_all_nested_dicts_renders_nothing(self):
        """The exact db_resilience shape."""
        fl = FactLayer.__new__(FactLayer)
        f = Fact(analysis_id="db_resilience", state=MEASURED, value={
            "replication": {"is_in_recovery": False, "replicas": []},
            "wal_archiving": {"archive_mode": "off"},
            "backup_tool_signals": {"detected_extensions": []},
            "clustering": {"citus_detected": False, "citus_version": None},
        })
        assert fl._renders_text(f) is False

    def test_all_lists_renders_nothing(self):
        """The exact db_external_dependencies shape."""
        fl = FactLayer.__new__(FactLayer)
        f = Fact(analysis_id="db_external_dependencies", state=MEASURED, value={
            "extensions": [], "foreign_servers": [], "foreign_tables": [],
            "publications": [], "subscriptions": [],
        })
        assert fl._renders_text(f) is False

    def test_an_empty_value_renders_nothing(self):
        fl = FactLayer.__new__(FactLayer)
        f = Fact(analysis_id="x", state=MEASURED, value={})
        assert fl._renders_text(f) is False

    def test_verdict_field_alone_does_not_count(self):
        """`scalarMeasures()` explicitly excludes `verdict` -- it is shown as
        the bolded verdict word instead, never folded into the scalar dump."""
        fl = FactLayer.__new__(FactLayer)
        f = Fact(analysis_id="x", state=MEASURED, value={"verdict": "yes"})
        assert fl._renders_text(f) is False

    def test_an_overlong_scalar_does_not_count(self):
        """`scalarMeasures()` drops any value whose string form exceeds 60
        chars -- a long free-text field is not a `key value` scalar pair."""
        fl = FactLayer.__new__(FactLayer)
        f = Fact(analysis_id="x", state=MEASURED, value={"note": "x" * 61})
        assert fl._renders_text(f) is False

    def test_a_null_or_empty_scalar_does_not_count(self):
        fl = FactLayer.__new__(FactLayer)
        f = Fact(analysis_id="x", state=MEASURED, value={"a": None, "b": ""})
        assert fl._renders_text(f) is False

    def test_activity_signals_real_shape_renders_via_its_scalars(self):
        """db_activity_signals was never silently empty like db_resilience --
        `stats_reset`/`table_count` are real top-level scalars alongside the
        one list field."""
        fl = FactLayer.__new__(FactLayer)
        f = Fact(analysis_id="db_activity_signals", state=MEASURED, value={
            "table_activity": [{"tablename": "orders"}],
            "stats_reset": "2026-09-20 10:00:00+00",
            "table_count": 56,
        })
        assert fl._renders_text(f) is True


class TestGateFiresAtAnyLevelNotJustSubResource:
    """The core generalization: a resource-level question (previously exempt
    from `_check_level` entirely) now gates too, when nothing renders."""

    def test_a_resource_level_question_with_nothing_renderable_gates(self, monkeypatch):
        fl = _fact_layer(monkeypatch, [{"id": "db_resilience", "target_shape": "whole_resource_only"}])
        env = _envelope(Fact(analysis_id="db_resilience", state=MEASURED, value={
            "replication": {"is_in_recovery": False, "replicas": []},
            "wal_archiving": {"archive_mode": "off"},
            "backup_tool_signals": {"detected_extensions": []},
            "clustering": {"citus_detected": False, "citus_version": None},
        }))
        fl._check_level(env, {"levels": ["resource"]})
        assert env.level_mismatch is True
        assert "no summary reader" in env.level_note

    def test_missing_levels_key_also_gates_when_nothing_renders(self, monkeypatch):
        """Missing `levels` defaults to `["resource"]` -- must not be read as
        a second reason to exempt an all-nested-dict fact."""
        fl = _fact_layer(monkeypatch, [{"id": "db_resilience", "target_shape": "whole_resource_only"}])
        env = _envelope(Fact(analysis_id="db_resilience", state=MEASURED, value={
            "replication": {"is_in_recovery": None, "replicas": []},
        }))
        fl._check_level(env, {})
        assert env.level_mismatch is True

    def test_a_resource_level_question_with_a_headline_does_not_gate(self, monkeypatch):
        fl = _fact_layer(monkeypatch, [{"id": "db_resilience", "target_shape": "whole_resource_only"}])
        env = _envelope(Fact(
            analysis_id="db_resilience", state=MEASURED, value={"replication": {}},
            headline="Primary; no replicas; WAL archiving off; no backup tool detected.",
        ))
        fl._check_level(env, {"levels": ["resource"]})
        assert env.level_mismatch is False

    def test_a_resource_level_question_with_a_plain_scalar_does_not_gate(self, monkeypatch):
        """A genuinely thin analysis (a bare scalar, no headline) still
        counts as answered at resource level -- the bar is "says something",
        not "says something good"."""
        fl = _fact_layer(monkeypatch, [{"id": "some_analysis", "target_shape": "whole_resource_only"}])
        env = _envelope(Fact(analysis_id="some_analysis", state=MEASURED, value={"table_count": 12}))
        fl._check_level(env, {"levels": ["resource"]})
        assert env.level_mismatch is False

    def test_one_renderable_fact_among_several_unrenderable_ones_is_enough(self, monkeypatch):
        fl = _fact_layer(monkeypatch, [
            {"id": "db_resilience", "target_shape": "whole_resource_only"},
            {"id": "db_external_dependencies", "target_shape": "whole_resource_only"},
            {"id": "row_count_snapshot", "target_shape": "corpus"},
        ])
        env = _envelope(
            Fact(analysis_id="db_resilience", state=MEASURED, value={"replication": {}}),
            Fact(analysis_id="db_external_dependencies", state=MEASURED, value={"extensions": []}),
            Fact(analysis_id="row_count_snapshot", state=MEASURED, value={},
                 headline="1,204 row(s), 3.2 MB."),
        )
        fl._check_level(env, {"levels": ["resource"]})
        assert env.level_mismatch is False

    def test_no_known_facts_still_does_not_gate(self, monkeypatch):
        fl = _fact_layer(monkeypatch, [])
        env = Envelope(subject="coco_ods")
        env.facts = []
        fl._check_level(env, {"levels": ["resource"]})
        assert env.level_mismatch is False


class TestSubResourceCheckStillAppliesOnTopOfRenderability:
    """The two checks compose: clearing "renders something" does not by
    itself satisfy a sub-resource question if what rendered was only a
    scalar rollup, not a headline naming a member."""

    def test_a_sub_resource_question_answered_only_by_scalar_rollup_still_gates(self, monkeypatch):
        fl = _fact_layer(monkeypatch, [{"id": "schema_inventory", "target_shape": "single_container"}])
        env = _envelope(Fact(
            analysis_id="schema_inventory", state=MEASURED,
            value={"table_count": 56, "column_count": 427},  # renders, but no headline
        ))
        fl._check_level(env, {"levels": ["container"]})
        assert env.level_mismatch is True
        assert "no reader shows them yet" in env.level_note


class TestRealCatalogHeadlinesNowExistForTheThreeOperationsAnalyses:
    """Confirms, against the real map on disk, that slice 17c actually wired
    up headline readers for the three analyses named in the design ruling --
    not just that the gate degrades gracefully without them."""

    def test_db_resilience_now_has_a_headline_reader(self):
        from resource_explorer.surveyors.database.survey_definition_adapter import (
            DATABASE_ANALYSIS_HEADLINE_MAP,
        )
        assert "db_resilience" in DATABASE_ANALYSIS_HEADLINE_MAP

    def test_db_activity_signals_now_has_a_headline_reader(self):
        from resource_explorer.surveyors.database.survey_definition_adapter import (
            DATABASE_ANALYSIS_HEADLINE_MAP,
        )
        assert "db_activity_signals" in DATABASE_ANALYSIS_HEADLINE_MAP

    def test_db_external_dependencies_now_has_a_headline_reader(self):
        from resource_explorer.surveyors.database.survey_definition_adapter import (
            DATABASE_ANALYSIS_HEADLINE_MAP,
        )
        assert "db_external_dependencies" in DATABASE_ANALYSIS_HEADLINE_MAP

    def test_privilege_audit_now_has_a_headline_reader(self):
        """Added on review: a Security-perspective question with a known
        answer and no sentence is a visible gap, not an acceptable floor --
        given a headline in the same pass as the other three, not deferred."""
        from resource_explorer.surveyors.database.survey_definition_adapter import (
            DATABASE_ANALYSIS_HEADLINE_MAP,
        )
        assert "privilege_audit" in DATABASE_ANALYSIS_HEADLINE_MAP


class TestHeadlineFunctionsProduceRealSentences:
    """Reader-level tests (fake registry, real functions) confirming the
    written sentences read like the answer a Data Owner came for, not just
    that a non-empty string exists."""

    def _registry_with_survey(self, survey_data: dict):
        class _FakeRegistry:
            def get_latest_database_survey(self, slug):
                import json
                return {"survey_data": json.dumps(survey_data)}

        return _FakeRegistry()

    def test_resilience_headline_names_primary_and_replica_count(self):
        from resource_explorer.surveyors.database.survey_definition_adapter import (
            _db_resilience_headline,
        )
        registry = self._registry_with_survey({"operations": {"resilience": {
            "replication": {"is_in_recovery": False, "replicas": [{"application_name": "r1"}]},
            "wal_archiving": {"archive_mode": "on", "failed_count": 0},
            "backup_tool_signals": {"detected_extensions": ["pgbackrest"]},
            "clustering": {"citus_detected": False, "citus_version": None},
        }}})
        result = _db_resilience_headline(registry, "coco_ods")
        assert result is not None
        label = result["label"]
        assert "primary" in label.lower()
        assert "1 replica" in label
        assert "pgbackrest" in label

    def test_resilience_headline_names_replica_role(self):
        from resource_explorer.surveyors.database.survey_definition_adapter import (
            _db_resilience_headline,
        )
        registry = self._registry_with_survey({"operations": {"resilience": {
            "replication": {"is_in_recovery": True, "replicas": []},
            "wal_archiving": {"archive_mode": "off"},
            "backup_tool_signals": {"detected_extensions": []},
            "clustering": {"citus_detected": False, "citus_version": None},
        }}})
        result = _db_resilience_headline(registry, "coco_ods")
        assert "replica" in result["label"].lower()
        assert "no backup tool detected" in result["label"]

    def test_resilience_headline_is_none_when_nothing_measured(self):
        from resource_explorer.surveyors.database.survey_definition_adapter import (
            _db_resilience_headline,
        )
        registry = self._registry_with_survey({})
        assert _db_resilience_headline(registry, "coco_ods") is None

    def test_external_dependencies_headline_names_actual_counts(self):
        from resource_explorer.surveyors.database.survey_definition_adapter import (
            _db_external_dependencies_headline,
        )
        registry = self._registry_with_survey({"operations": {"external_dependencies": {
            "extensions": [{"extname": "pgcrypto"}, {"extname": "citus"}],
            "foreign_servers": [], "foreign_tables": [], "publications": [], "subscriptions": [],
        }}})
        result = _db_external_dependencies_headline(registry, "coco_ods")
        assert "2 extension" in result["label"]

    def test_external_dependencies_headline_says_so_when_all_zero(self):
        from resource_explorer.surveyors.database.survey_definition_adapter import (
            _db_external_dependencies_headline,
        )
        registry = self._registry_with_survey({"operations": {"external_dependencies": {
            "extensions": [], "foreign_servers": [], "foreign_tables": [],
            "publications": [], "subscriptions": [],
        }}})
        result = _db_external_dependencies_headline(registry, "coco_ods")
        assert "no extensions" in result["label"].lower()

    def test_activity_signals_headline_names_reads_and_writes_and_when(self):
        """Live gate feedback (2026-09-26): the counter WHEN matters as much
        as the counts -- the reset timestamp is load-bearing, not detail.

        Fixture keys are the STORED names (`connection.py`'s own
        `_survey_operations()` renames `n_tup_ins`/`n_tup_upd`/`n_tup_del`
        to `rows_inserted`/`rows_updated`/`rows_deleted` before writing —
        only `seq_scan`/`idx_scan` keep their raw pg_stat_user_tables
        names). Using the raw names here would pass even with the
        live-found bug that read those same raw names back (found live,
        `adventureworks`, 2026-09-27: "0 writes and 861 reads" reported for
        a database with 761,184 inserts + 1,435 updates — this test's own
        fixture, before the fix, used to validate against its own wrong
        assumption rather than the real stored shape)."""
        from resource_explorer.surveyors.database.survey_definition_adapter import (
            _db_activity_signals_headline,
        )
        registry = self._registry_with_survey({"operations": {"activity_signals": {
            "table_activity": [
                {"rows_inserted": 10, "rows_updated": 2, "rows_deleted": 0, "seq_scan": 5, "idx_scan": 100},
            ],
            "stats_reset": "2026-09-20 10:00:00+00",
            "table_count": 1,
        }}})
        result = _db_activity_signals_headline(registry, "coco_ods")
        label = result["label"]
        assert "12 writes" in label
        assert "105 reads" in label
        assert "since statistics were reset on 2026-09-20 10:00:00+00" in label

    def test_activity_signals_headline_does_not_read_the_raw_pg_stat_column_names(self):
        """The regression this whole fix is about: `n_tup_ins`/`n_tup_upd`/
        `n_tup_del` are never present on a stored row (they were renamed
        before storage), so a row carrying ONLY those raw names must count
        as zero writes, not silently sum a key that happens to look right."""
        from resource_explorer.surveyors.database.survey_definition_adapter import (
            _db_activity_signals_headline,
        )
        registry = self._registry_with_survey({"operations": {"activity_signals": {
            "table_activity": [
                {"n_tup_ins": 761184, "n_tup_upd": 1435, "n_tup_del": 0, "seq_scan": 5, "idx_scan": 100},
            ],
            "stats_reset": "2026-09-20 10:00:00+00",
            "table_count": 1,
        }}})
        result = _db_activity_signals_headline(registry, "coco_ods")
        assert "0 writes" in result["label"]

    def test_activity_signals_headline_says_never_reset_when_stats_reset_is_null(self):
        """`pg_stat_database.stats_reset` NULL means the counters have never
        been reset since collection began -- a real, distinct case from "we
        don't know", not papered over with generic wording that implies a
        reset happened. NOT "since the server started" (owner's correction,
        2026-09-26): cumulative statistics survive a server restart, so that
        wording would be factually wrong, not just imprecise."""
        from resource_explorer.surveyors.database.survey_definition_adapter import (
            _db_activity_signals_headline,
        )
        registry = self._registry_with_survey({"operations": {"activity_signals": {
            "table_activity": [
                {"rows_inserted": 1, "rows_updated": 0, "rows_deleted": 0, "seq_scan": 0, "idx_scan": 0},
            ],
            "stats_reset": "",
            "table_count": 1,
        }}})
        result = _db_activity_signals_headline(registry, "coco_ods")
        assert "since statistics collection began (never reset)" in result["label"]
        assert "server started" not in result["label"]

    def test_privilege_audit_headline_names_roles_and_public_grants(self):
        from resource_explorer.surveyors.database.survey_definition_adapter import (
            _db_privilege_audit_headline,
        )
        registry = self._registry_with_survey({"operations": {"privilege_audit": {
            "roles": [
                {"rolname": "postgres", "rolsuper": True},
                {"rolname": "egeria_user", "rolsuper": False},
                {"rolname": "app_user", "rolsuper": False},
            ],
            "table_grants": [
                {"table_schema": "public", "table_name": "orders",
                 "grantee": "PUBLIC", "privilege_type": "SELECT"},
                {"table_schema": "public", "table_name": "customers",
                 "grantee": "PUBLIC", "privilege_type": "SELECT"},
                {"table_schema": "public", "table_name": "orders",
                 "grantee": "app_user", "privilege_type": "INSERT"},
            ],
            "default_acl": [],
        }}})
        result = _db_privilege_audit_headline(registry, "coco_ods")
        label = result["label"]
        assert "3 role(s); 1 superuser(s)" in label
        assert "PUBLIC has SELECT on 2 table(s)" in label
        assert "0 table(s) world-writable" in label

    def test_privilege_audit_headline_names_world_writable_public_grants(self):
        from resource_explorer.surveyors.database.survey_definition_adapter import (
            _db_privilege_audit_headline,
        )
        registry = self._registry_with_survey({"operations": {"privilege_audit": {
            "roles": [{"rolname": "postgres", "rolsuper": True}],
            "table_grants": [
                {"table_schema": "public", "table_name": "logs",
                 "grantee": "PUBLIC", "privilege_type": "INSERT"},
            ],
            "default_acl": [],
        }}})
        result = _db_privilege_audit_headline(registry, "coco_ods")
        assert "1 table(s) world-writable" in result["label"]

    def test_privilege_audit_headline_says_so_with_no_public_grants(self):
        from resource_explorer.surveyors.database.survey_definition_adapter import (
            _db_privilege_audit_headline,
        )
        registry = self._registry_with_survey({"operations": {"privilege_audit": {
            "roles": [{"rolname": "postgres", "rolsuper": True}],
            "table_grants": [
                {"table_schema": "public", "table_name": "orders",
                 "grantee": "app_user", "privilege_type": "SELECT"},
            ],
            "default_acl": [],
        }}})
        result = _db_privilege_audit_headline(registry, "coco_ods")
        assert "no PUBLIC SELECT grants" in result["label"]

    def test_privilege_audit_headline_is_none_when_nothing_measured(self):
        from resource_explorer.surveyors.database.survey_definition_adapter import (
            _db_privilege_audit_headline,
        )
        registry = self._registry_with_survey({})
        assert _db_privilege_audit_headline(registry, "coco_ods") is None

    def test_privilege_audit_headline_is_none_when_roles_is_empty_even_with_grants(self):
        """The exact coco_pharma collection failure (2026-09-26): roles=[]
        with real table_grants present is a broken read (an unescaped % in
        the roles query, since fixed in connection.py), not a database with
        zero roles -- every live Postgres has at least the connecting role,
        and pg_roles is universally readable. Must fall to the honest
        no-summary-reader state rather than render "0 role(s); 0
        superuser(s)"."""
        from resource_explorer.surveyors.database.survey_definition_adapter import (
            _db_privilege_audit_headline,
        )
        registry = self._registry_with_survey({"operations": {"privilege_audit": {
            "roles": [],
            "table_grants": [
                {"table_schema": "public", "table_name": "orders",
                 "grantee": "PUBLIC", "privilege_type": "SELECT"},
            ],
            "default_acl": [],
        }}})
        assert _db_privilege_audit_headline(registry, "coco_ods") is None

    def test_privilege_audit_headline_is_none_when_everything_is_empty(self):
        from resource_explorer.surveyors.database.survey_definition_adapter import (
            _db_privilege_audit_headline,
        )
        registry = self._registry_with_survey({"operations": {"privilege_audit": {
            "roles": [], "table_grants": [], "default_acl": [],
        }}})
        assert _db_privilege_audit_headline(registry, "coco_ods") is None

    def test_privilege_audit_headline_allows_a_real_zero_grants_count(self):
        """Unlike roles, table_grants CAN legitimately be empty -- a
        database where every table carries only its owner's default
        privileges, with no explicit GRANT rows at all, is a real state
        (`get_privilege_audit()`'s own query filters `c.relacl IS NOT
        NULL`), not a collection failure."""
        from resource_explorer.surveyors.database.survey_definition_adapter import (
            _db_privilege_audit_headline,
        )
        registry = self._registry_with_survey({"operations": {"privilege_audit": {
            "roles": [{"rolname": "postgres", "rolsuper": True}],
            "table_grants": [],
            "default_acl": [],
        }}})
        result = _db_privilege_audit_headline(registry, "coco_ods")
        assert result is not None
        assert "1 role(s); 1 superuser(s)" in result["label"]


class TestPrivilegeAuditRolesQueryDoesNotChokeOnItsOwnLikePattern:
    """Regression test for the root cause behind the collection failure
    above: `get_privilege_audit()`'s roles query had a literal `%` in a
    LIKE pattern, but `execute_query` always passes a params tuple (even
    the default empty `()`) to psycopg2, which switches on printf-style
    query substitution and chokes on any `%` that isn't part of a valid
    placeholder -- silently caught by the query's own try/except and
    turned into an empty roles list, indistinguishable from "no roles"."""

    def test_the_roles_query_runs_without_raising_against_a_real_connection_shape(self):
        """Exercises the exact query string `get_privilege_audit` sends,
        against a stub cursor that mimics psycopg2's real percent-sign
        substitution behavior (raises on an unescaped literal `%`), the
        cheapest way to pin this without a real Postgres connection."""
        import re

        from resource_explorer.surveyors.database.connection import PostgreSQLConnection

        class _StubCursor:
            def __init__(self):
                self.description = [("rolname",)]

            def execute(self, query, params=()):
                # Mirrors psycopg2: with a params tuple (even empty), `%%`
                # is the literal-percent escape and `%s`/`%(name)s` are
                # placeholders; any OTHER `%` is invalid.
                stripped = query.replace("%%", "")
                stripped = re.sub(r"%s|%\([a-zA-Z_]+\)s", "", stripped)
                if "%" in stripped:
                    raise IndexError("tuple index out of range")

            def fetchall(self):
                return [("egeria_user",)]

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        class _StubConn:
            def cursor(self):
                return _StubCursor()

        conn = PostgreSQLConnection.__new__(PostgreSQLConnection)
        conn._conn = _StubConn()
        result = conn.get_privilege_audit()
        assert result["roles"] == [{"rolname": "egeria_user"}]
