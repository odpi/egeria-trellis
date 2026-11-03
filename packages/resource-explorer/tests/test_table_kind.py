"""A1: `table_kind` on `database_table_activity` and in the typed read; a row from before the column says so."""
import sqlite3

import pytest

from resource_explorer.table_kind import table_kind_of
from resource_explorer.surveyors.survey_step_host import step_reader
from trellis_analytics_re.ods import TABLE_KIND_SINCE

COLS = ("database_slug TEXT, surveyed_at TEXT, source TEXT, schema_name TEXT, table_name TEXT, rows_inserted BIGINT, "
        "rows_updated BIGINT, rows_deleted BIGINT, live_tuples BIGINT, dead_tuples BIGINT, seq_scan BIGINT, "
        "idx_scan BIGINT, last_vacuum TEXT, last_autovacuum TEXT, last_analyze TEXT, last_autoanalyze TEXT, "
        "pending_changes BIGINT, stats_reset TEXT, state TEXT")
SURVEYS = "CREATE TABLE database_surveys (database_slug TEXT, surveyed_at TEXT, source TEXT, invalid_at TEXT)"


def _db(tmp_path, *, with_column: bool, rows):
    path = tmp_path / "ods.sqlite"
    c = sqlite3.connect(path)
    c.execute(SURVEYS)
    c.execute(f"CREATE TABLE database_table_activity ({COLS}{', table_kind TEXT' if with_column else ''})")
    for at, name, kind in rows:
        vals = ("shop", at, "local", "public", name, 1, 0, 0, 10, 0, 0, 0, "", "", "", "", 0, None, "measured")
        if with_column:
            c.execute(f"INSERT INTO database_table_activity VALUES ({','.join('?' * 20)})", (*vals, kind))
        else:
            c.execute(f"INSERT INTO database_table_activity VALUES ({','.join('?' * 19)})", vals)
    c.commit()
    c.close()
    return f"sqlite:///{path}"


def test_three_views_read_as_not_base_tables(tmp_path):
    rows = [("2026-11-03", f"t{i}", "base table") for i in range(263)]
    rows += [("2026-11-03", f"v{i}", "view") for i in range(3)]
    url = _db(tmp_path, with_column=True, rows=rows)
    with step_reader(url) as ods:
        [snap] = ods.table_activity_snapshots("shop")
    kinds = [t["table_kind"] for t in snap["tables"]]
    assert len(kinds) == 266
    assert kinds.count("base table") == 263
    assert [k for k in kinds if k != "base table"] == ["view"] * 3


def test_a_row_from_before_the_column_says_so_and_is_not_a_base_table(tmp_path):
    url = _db(tmp_path, with_column=True, rows=[("2026-10-01", "old", None), ("2026-11-03", "new", None)])
    with step_reader(url) as ods:
        got = {s["surveyed_at"]: s["tables"][0]["table_kind"] for s in ods.table_activity_snapshots("shop")}
    assert got["2026-10-01"] == f"kind not recorded (snapshot before {TABLE_KIND_SINCE})"
    assert got["2026-11-03"] == "kind not recorded for this table"


def test_a_registry_without_the_column_is_read_not_migrated(tmp_path):
    url = _db(tmp_path, with_column=False, rows=[("2026-10-01", "old", None)])
    with step_reader(url) as ods:
        [snap] = ods.table_activity_snapshots("shop")
    assert snap["tables"][0]["table_kind"].startswith("kind not recorded (this registry has no table_kind column")
    c = sqlite3.connect(url.removeprefix("sqlite:///"))
    assert "table_kind" not in [r[1] for r in c.execute("PRAGMA table_info(database_table_activity)")]


@pytest.mark.parametrize("table_type,relkind,kind", [
    ("BASE TABLE", None, "base table"), ("VIEW", None, "view"), ("MATERIALIZED VIEW", None, "materialized view"),
    ("FOREIGN", None, "foreign table"), ("PARTITIONED TABLE", None, "partitioned table"),
    ("", "m", "materialized view"), ("", None, None), (None, None, None), ("weird", None, None)])
def test_kind_words(table_type, relkind, kind):
    assert table_kind_of(table_type, relkind) == kind


def test_the_writers_store_the_kind_and_the_registry_carries_the_column():
    from resource_explorer.registry import _DETAIL_TABLE_SPECS, _DB_FS_DETAIL_TABLE_MIGRATIONS
    assert "table_kind" in _DETAIL_TABLE_SPECS["database_table_activity"].value_columns
    assert ("table_kind", "TEXT DEFAULT NULL") in dict(_DB_FS_DETAIL_TABLE_MIGRATIONS)["database_table_activity"]
