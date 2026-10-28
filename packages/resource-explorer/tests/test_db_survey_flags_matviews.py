"""PostgreSQL survey: table flags, materialized views, database activity.

Closes three gaps against Egeria's `PostgresDatabaseStatsExtractor`:

1. per table: Has Triggers / Has Rules / Has Row Security (Egeria's display
   names), read from `pg_class`;
2. materialized views from `pg_matviews`, stored beside the view definitions
   with a kind that says materialized;
3. database activity from `pg_stat_database` (Rows Fetched / Inserted /
   Updated / Deleted, Session Time, Active Time), labelled cumulative since the
   last statistics reset.

A value not read is NOT false and NOT 0. A permission error on one schema does
not abort the survey. Only the surveyed Postgres connection is faked, with the
real method signatures (`execute_query(query, params=())`).
"""
from __future__ import annotations

import dataclasses
import json
from unittest.mock import MagicMock

from resource_explorer.registry import DatabaseEntity
from resource_explorer.surveyors.database import table_statistics_metrics as tsm
from resource_explorer.surveyors.database.connection import (
    NO_CAPABILITIES,
    PostgreSQLConnection,
)
from resource_explorer.surveyors.database.database_surveyor import DatabaseSurveyor
from resource_explorer.surveyors.result_materializer import database_rows_from_survey_data

NOW = "2026-10-12T12:00:00"


# ── a PostgreSQLConnection whose client boundary is faked ──────────────────

class _Conn(PostgreSQLConnection):
    """Real `PostgreSQLConnection` methods; only the wire is replaced."""

    def __init__(self, *, flags=None, locked=(), stat_db=None, matviews=None,
                 matview_error=None, views=()):
        super().__init__(host="h", port=5432, database="d", user="u", password="p")
        self._flags = flags or {}
        self._locked = set(locked)
        self._stat_db = stat_db
        self._matviews = matviews or []
        self._matview_error = matview_error
        self._views = list(views)
        self._conn = MagicMock()          # rollback() is recorded here
        self.executed: list[str] = []

    def execute_query(self, query, params=()):
        self.executed.append(query)
        if "FROM pg_stat_database" in query:
            if isinstance(self._stat_db, Exception):
                raise self._stat_db
            return [] if self._stat_db is None else [dict(self._stat_db)]
        if "FROM pg_matviews" in query:
            if self._matview_error:
                raise self._matview_error
            return [dict(m) for m in self._matviews]
        if "FROM information_schema.views" in query:
            return [dict(v) for v in self._views]
        if "FROM information_schema.schemata" in query:
            return [{"schema_name": "public"}, {"schema_name": "locked"}]
        if "c.relkind IN ('r', 'p', 'v', 'm')" in query:
            schema = params[0]
            return [
                {"table_name": name, "relkind": "r", "reltuples": 1, **extra}
                for name, extra in self._flags.get(schema, {}).items()
            ]
        if "FROM information_schema.tables t" in query:
            if params[0] in self._locked:
                raise RuntimeError("permission denied for schema locked")
            return []
        if "FROM pg_attribute a" in query:
            return []
        if "FROM pg_constraint con" in query:
            return []
        if "FROM pg_namespace n" in query and "c.relname" not in query:
            return []                       # schema descriptions / floor schemas
        if "has_table_privilege" in query:
            return []
        raise AssertionError(f"unexpected query in test fake: {query}")

    def execute_query_isolated(self, query, params=()):
        return self.execute_query(query, params)


# ═══════════════════════════════════════════════════════════════════════════
# 1. table flags
# ═══════════════════════════════════════════════════════════════════════════

class TestTableFlags:
    def test_names_are_egerias(self):
        assert tsm.HAS_TRIGGERS.display_name == "Has Triggers"
        assert tsm.HAS_RULES.display_name == "Has Rules"
        assert tsm.HAS_ROW_SECURITY.display_name == "Has Row Security"
        assert all(m.in_egeria for m in tsm.TABLE_STRUCTURE_FLAGS)

    def test_catalog_read_carries_the_three_flags(self):
        conn = _Conn(flags={"public": {
            "orders": {"relhastriggers": True, "relhasrules": False, "relrowsecurity": True},
        }})
        out = conn._catalog_table_summary("public")
        assert out["orders"]["has_triggers"] is True
        assert out["orders"]["has_rules"] is False
        assert out["orders"]["has_row_security"] is True

    def test_a_views_own_select_rule_is_not_reported_as_an_attached_rule(self):
        class Kinds(_Conn):
            def execute_query(self, query, params=()):
                rows = super().execute_query(query, params)
                if "c.relkind IN ('r', 'p', 'v', 'm')" in query:
                    for r in rows:
                        r["relkind"] = "v" if r["table_name"].startswith("v_") else "r"
                return rows

        conn = Kinds(flags={"public": {
            "v_orders": {"relhastriggers": False, "relhasrules": True, "relrowsecurity": False},
            "orders": {"relhastriggers": False, "relhasrules": True, "relrowsecurity": False}}})
        out = conn._catalog_table_summary("public")
        assert out["v_orders"]["has_rules"] is None          # not read, not "has rules"
        assert out["orders"]["has_rules"] is True            # a table's rule is a real one

    def test_a_row_without_the_column_is_not_read_never_false(self):
        conn = _Conn(flags={"public": {"orders": {}}})          # no rel* keys
        out = conn._catalog_table_summary("public")["orders"]
        assert out["has_triggers"] is None and out["has_rules"] is None
        assert out["has_row_security"] is None

    def test_flags_reach_schema_info_tables(self):
        conn = _Conn(flags={"public": {
            "orders": {"relhastriggers": True, "relhasrules": False, "relrowsecurity": False}}})
        (table,) = conn._get_tables_for_schema("public")
        assert (table["has_triggers"], table["has_rules"], table["has_row_security"]) == (True, False, False)

    def test_properties_state_true_false_and_name_what_was_not_read(self):
        props, not_read = tsm.table_flag_properties(
            {"has_triggers": True, "has_rules": False, "has_row_security": None})
        assert props == {"Has Triggers": "true", "Has Rules": "false"}
        assert list(not_read) == ["Has Row Security"]
        props, not_read = tsm.table_flag_properties({"name": "t"})     # none read
        assert props == {} and set(not_read) == {"Has Triggers", "Has Rules", "Has Row Security"}

    def test_published_on_the_table_annotation_beside_the_others(self):
        bodies = _publish(schema_tables=[
            {"name": "orders", "type": "BASE TABLE", "columns": [],
             "has_triggers": True, "has_rules": False, "has_row_security": None},
        ])
        rp = bodies['"public"."orders"']["resourceProperties"]
        assert rp["Has Triggers"] == "true" and rp["Has Rules"] == "false"
        assert "Has Row Security" not in rp
        assert "Last Analyze" in rp                       # beside the currency metrics
        reasons = json.loads(rp["metricsNotRead"])
        assert "Has Row Security" in reasons and "Has Triggers" not in reasons

    def test_stored_table_rows_carry_the_flags_and_null_for_not_read(self):
        schema_info = {"schemas": [{"name": "public", "tables": [
            {"name": "a", "type": "BASE TABLE", "columns": [],
             "has_triggers": True, "has_rules": False, "has_row_security": True},
            {"name": "b", "type": "BASE TABLE", "columns": []},
        ]}]}
        rows = database_rows_from_survey_data({"schema_info": schema_info})["database_tables"]
        by = {r["table_name"]: r for r in rows}
        assert (by["a"]["has_triggers"], by["a"]["has_rules"], by["a"]["has_row_security"]) == (1, 0, 1)
        assert by["b"]["has_triggers"] is None and by["b"]["has_rules"] is None
        assert by["b"]["has_row_security"] is None


# ═══════════════════════════════════════════════════════════════════════════
# 2. materialized views
# ═══════════════════════════════════════════════════════════════════════════

_SCHEMA_INFO = {"schemas": [{"name": "public", "tables": [
    {"name": "orders", "type": "BASE TABLE",
     "columns": [{"name": "id", "type": "integer", "base_type": "integer"}]},
    {"name": "mv_orders", "type": "MATERIALIZED VIEW", "source": "catalog_fallback",
     "columns": [{"name": "id", "type": "integer", "base_type": "integer"}]},
]}]}


def _surveyor():
    entity = DatabaseEntity(slug="d", display_name="D", db_type="postgresql",
                            host="h", port=5432, database_name="d")
    return DatabaseSurveyor(entity, {}, None)


def _schema_info():
    return json.loads(json.dumps(_SCHEMA_INFO))


class TestMaterializedViews:
    MV = {"table_schema": "public", "table_name": "mv_orders",
          "view_definition": " SELECT orders.id FROM orders;",
          "hasindexes": True, "ispopulated": False}

    def test_a_matview_is_stored_beside_the_views_with_its_kind(self):
        s = _surveyor()
        conn = _Conn(
            matviews=[{"table_schema": "public", "table_name": "mv_orders",
                       "view_definition": " SELECT orders.id FROM orders;",
                       "hasindexes": True, "ispopulated": False}],
            views=[{"table_schema": "public", "table_name": "v_orders",
                    "view_definition": "SELECT id FROM orders"}])
        out = s._survey_views(conn, _schema_info())
        by = {v["name"]: v for v in out}
        assert by["v_orders"]["kind"] == "view"
        mv = by["mv_orders"]
        assert mv["kind"] == "materialized_view"
        assert mv["definition"].strip().startswith("SELECT orders.id")
        assert mv["has_indexes"] is True and mv["is_populated"] is False
        assert mv["dependencies"]                       # analysed like a view
        assert s.last_views_not_read == {}

    def test_the_table_row_carries_has_indexes_and_is_populated(self):
        info = _schema_info()
        _surveyor()._survey_views(_Conn(matviews=[self.MV]), info)
        table = info["schemas"][0]["tables"][1]
        assert table["has_indexes"] is True and table["is_populated"] is False
        rows = database_rows_from_survey_data({"schema_info": info})["database_tables"]
        mv_row = [r for r in rows if r["table_name"] == "mv_orders"][0]
        assert mv_row["has_indexes"] == 1 and mv_row["is_populated"] == 0

    def test_materializer_types_it_materialized_and_old_blobs_stay_views(self):
        blob = {"schema_info": {"schemas": []}, "views": [
            {"schema": "public", "name": "mv", "kind": "materialized_view", "definition": "SELECT 1"},
            {"schema": "public", "name": "v", "definition": "SELECT 2"},       # pre-change blob
        ]}
        objs = {o["object_name"]: o["object_type"]
                for o in database_rows_from_survey_data(blob)["database_sql_objects"]}
        assert objs == {"mv": "materialized_view", "v": "view"}

    def test_a_failed_matview_read_is_recorded_and_ordinary_views_survive(self):
        s = _surveyor()
        conn = _Conn(matview_error=RuntimeError("permission denied for pg_matviews"),
                     views=[{"table_schema": "public", "table_name": "v_orders",
                             "view_definition": "SELECT id FROM orders"}])
        out = s._survey_views(conn, _schema_info())
        assert [v["name"] for v in out] == ["v_orders"]
        assert "permission denied" in s.last_views_not_read["materialized_views"]

    def test_no_matviews_read_ok_is_not_the_same_as_not_read(self):
        s = _surveyor()
        s._survey_views(_Conn(matviews=[]), _schema_info())
        assert s.last_views_not_read == {}

    def test_an_unreadable_definition_is_named_not_dropped_silently(self):
        s = _surveyor()
        mv = dict(self.MV, view_definition=None)
        s._survey_views(_Conn(matviews=[mv]), _schema_info())
        assert s.last_views_not_read["materialized_view_definitions"] == ["public.mv_orders"]


# ═══════════════════════════════════════════════════════════════════════════
# 3. database activity
# ═══════════════════════════════════════════════════════════════════════════

_STAT = {"datname": "d", "tup_fetched": 900, "tup_inserted": 10, "tup_updated": 0,
         "tup_deleted": 3, "session_time": 12345.5, "active_time": 678.25}


class TestDatabaseActivity:
    def test_read_returns_the_six_counters(self):
        out = _Conn(stat_db=_STAT).get_database_activity()
        assert out == {"tup_fetched": 900, "tup_inserted": 10, "tup_updated": 0,
                       "tup_deleted": 3, "session_time": 12345.5, "active_time": 678.25}

    def test_an_old_server_without_session_time_leaves_it_not_read(self):
        old = {k: v for k, v in _STAT.items() if k not in ("session_time", "active_time")}
        out = _Conn(stat_db=old).get_database_activity()
        assert out["session_time"] is None and out["active_time"] is None
        assert out["tup_fetched"] == 900

    def test_a_failed_read_carries_the_error_not_zeros(self):
        out = _Conn(stat_db=RuntimeError("permission denied")).get_database_activity()
        assert "permission denied" in out["error"] and "tup_fetched" not in out

    def test_no_row_is_not_zero(self):
        assert _Conn(stat_db=None).get_database_activity() == {"no_row": True}

    def test_properties_use_egerias_names_and_say_cumulative(self):
        props = tsm.database_activity_properties(
            _Conn(stat_db=_STAT).get_database_activity(), "2026-09-01 00:00:00+00:00")
        assert props["Rows Fetched"] == 900 and props["Rows Updated"] == 0   # a true zero stays 0
        assert props["Session Time"] == 12345.5 and props["Active Time"] == 678.25
        assert props["Last statistics reset"] == "2026-09-01 00:00:00+00:00"
        assert "cumulative since the last statistics reset" in props["activityBasis"]
        assert tsm.NOT_READ_KEY not in props

    def test_unread_figures_are_named_with_a_reason_never_zero(self):
        props = tsm.database_activity_properties({"error": "boom"}, None)
        for name in ("Rows Fetched", "Rows Inserted", "Session Time", "Active Time"):
            assert name not in props
            assert "boom" in props[tsm.NOT_READ_KEY][name]
        assert "Last statistics reset" in props[tsm.NOT_READ_KEY]
        assert "Rows Fetched" not in tsm.database_activity_properties(None, None)

    def test_the_rollup_annotation_carries_them_from_the_same_function(self):
        operations = _operations(stat_db=_STAT)
        anns = _surveyor()._create_operations_annotations(operations)
        rollup = [a for a in anns if "Database-wide activity" in a.summary][0]
        rp = rollup.resource_properties
        assert rp["Rows Fetched"] == 900 and rp["Active Time"] == 678.25
        assert rp["Last statistics reset"] == "2026-09-01 00:00:00+00:00"
        assert rp["Rows Deleted"] == 3

    def test_the_rollup_names_a_failed_activity_read(self):
        operations = _operations(stat_db=RuntimeError("no access"))
        rollup = [a for a in _surveyor()._create_operations_annotations(operations)
                  if "Database-wide activity" in a.summary][0]
        rp = rollup.resource_properties
        assert "Rows Fetched" not in rp
        assert "no access" in rp[tsm.NOT_READ_KEY]["Rows Fetched"]


# ═══════════════════════════════════════════════════════════════════════════
# permission errors never abort the survey
# ═══════════════════════════════════════════════════════════════════════════

class TestPermissionErrorsDoNotAbort:
    def test_one_unreadable_schema_is_recorded_and_the_next_still_read(self):
        conn = _Conn(
            locked={"locked"},
            flags={"public": {"orders": {"relhastriggers": False, "relhasrules": False,
                                          "relrowsecurity": False}},
                   "locked": {"secrets": {"relhastriggers": True, "relhasrules": False,
                                           "relrowsecurity": True}}})
        info = conn.get_schema_info()
        by = {s["name"]: s for s in info["schemas"]}
        assert set(by) == {"public", "locked"}
        assert by["locked"]["access"] == "not_readable"
        assert "permission denied" in by["locked"]["not_readable_reason"]
        assert "permission denied" in info["_errors"]["schema:locked"]
        # the unprivileged catalog still shows its structure, and the flags
        (secrets,) = by["locked"]["tables"]
        assert secrets["name"] == "secrets" and secrets["has_row_security"] is True
        assert "access" not in by["public"]
        conn._conn.rollback.assert_called()           # the aborted transaction was ended

    def test_the_catalog_read_failing_too_leaves_the_schema_empty_and_recorded(self):
        class Both(_Conn):
            def execute_query(self, query, params=()):
                if "c.relkind IN ('r', 'p', 'v', 'm')" in query and params[0] == "locked":
                    raise RuntimeError("catalog denied")
                return super().execute_query(query, params)

        info = Both(locked={"locked"}).get_schema_info()
        by = {s["name"]: s for s in info["schemas"]}
        assert by["locked"]["tables"] == [] and by["locked"]["access"] == "not_readable"
        assert "catalog denied" in info["_errors"]["schema_catalog:locked"]


# ── helpers ────────────────────────────────────────────────────────────────

def _operations(stat_db):
    """Run the real `_survey_operations` fetch with only tuple_counters on."""
    conn = _Conn(stat_db=stat_db)
    conn.get_table_activity = lambda: [{
        "schemaname": "public", "tablename": "orders", "rows_inserted": 1, "rows_updated": 0,
        "rows_deleted": 0, "seq_scan": 1, "idx_scan": 0}]
    conn.get_stats_reset = lambda: "2026-09-01 00:00:00+00:00"
    conn.get_stats_reset_evidence = lambda: "2026-09-01 00:00:00+00:00"
    caps = dataclasses.replace(NO_CAPABILITIES, tuple_counters=True)
    return _surveyor()._survey_operations(conn, caps, {"total_tables": 1})


def _publish(schema_tables):
    """Fetch + publish through the real path; return table_statistics bodies."""
    from resource_explorer.surveyors.database.egeria_database_surveyor import EgeriaDatabaseSurveyor

    entity = DatabaseEntity(slug="coco", display_name="Coco", db_type="postgresql",
                            host="h", port=5432, database_name="coco")
    schema_info = {"schemas": [{"name": "public", "tables": schema_tables}]}
    conn = MagicMock()
    conn.get_table_activity.return_value = [{
        "schemaname": "public", "tablename": t["name"], "rows_inserted": 1, "rows_updated": 0,
        "rows_deleted": 0, "hot_updates": 0, "live_tuples": 10, "dead_tuples": 0, "seq_scan": 1,
        "idx_scan": 1, "last_vacuum": "", "last_autovacuum": "", "last_analyze": "",
        "last_autoanalyze": "", "pending_changes": 0} for t in schema_tables]
    conn.get_stats_reset.return_value = "2026-09-01"
    conn.get_stats_reset_evidence.return_value = "2026-09-01"
    conn.get_database_activity.return_value = {"tup_fetched": 1}
    caps = dataclasses.replace(NO_CAPABILITIES, tuple_counters=True)
    operations = DatabaseSurveyor(entity, {}, None)._survey_operations(conn, caps, schema_info)

    s = EgeriaDatabaseSurveyor(platform_url="https://localhost:9443", view_server="v",
                               user_id="u", user_password="p")
    s.connect = lambda: None
    s._find_by_qualified_name = lambda qn: "db-guid"
    s._find_element_guid = lambda qn: ""
    s._asset_maker = MagicMock()
    s._asset_maker.create_asset.return_value = "report-guid"
    s._discovery = MagicMock()
    s._discovery.create_annotation.side_effect = lambda body: "ann"
    s.publish_step_annotations(entity, schema_info, None, NOW, None, operations=operations)
    bodies = [c.kwargs["body"]["properties"] for c in s._discovery.create_annotation.call_args_list]
    return {b["qualifiedName"].rsplit("::", 1)[-1]: b for b in bodies
            if "table_statistics" in b["qualifiedName"]}
