"""Byte sizes and row/tuple counters are 64-bit in the registry (2026-10-10).

They were INTEGER (int4, max 2,147,483,647) on Postgres: a table over ~2 GiB made
the detail executemany raise "integer out of range", and record_database_survey
only logged a WARNING while the detail rows rolled back, so readers showed the
previous scan as current. SQLite INTEGER is already 64-bit, so the migration is
Postgres-only.
"""
from __future__ import annotations

import logging
import sqlite3

import pytest

from resource_explorer.registry import (
    _PG_INT4_TO_BIGINT_COLUMNS, DatabaseEntity, Project, ProjectRegistry,
)

BIG = 3_000_000_000
SCHEMA_TOTAL = 5_000_000_000


def test_the_widened_list_names_only_real_columns_in_ddl(tmp_path):
    reg = ProjectRegistry(db_path=str(tmp_path / "r.db"))
    with reg._conn() as conn:
        for table, col in _PG_INT4_TO_BIGINT_COLUMNS:
            assert col in reg._get_table_columns(conn, table), (table, col)
    assert len(set(_PG_INT4_TO_BIGINT_COLUMNS)) == len(_PG_INT4_TO_BIGINT_COLUMNS)


def test_sqlite_registry_init_runs_the_widening_as_a_no_op_and_stores_big_values(tmp_path):
    reg = ProjectRegistry(db_path=str(tmp_path / "r.db"))
    ProjectRegistry(db_path=str(tmp_path / "r.db"))          # second start
    reg.register_database(_entity())
    reg.write_detail_rows("database_tables", "d", "2026-10-10T00:00:00", rows=[
        {"schema_name": "s", "table_name": "t", "size_bytes": BIG, "row_count": BIG}])
    row = reg.query_detail_rows("database_tables", "d", "2026-10-10T00:00:00")[0]
    assert (row["size_bytes"], row["row_count"]) == (BIG, BIG)


def _entity():
    return DatabaseEntity(slug="d", display_name="D", db_type="postgresql", host="h",
                          port=5432, database_name="d", db_user="u", db_password="p")


# ── Postgres (throwaway schemas only) ───────────────────────────────────────

pg = pytest.mark.requires_pgvector


def _admin():
    import psycopg2
    from resource_explorer.config import get_config
    cfg = get_config().pgvector
    c = psycopg2.connect(host=cfg.host, port=cfg.port, dbname=cfg.dbname,
                         user=cfg.db_user, password=cfg.password)
    c.autocommit = True
    return c, cfg


def _types(schema):
    c, _ = _admin()
    try:
        with c.cursor() as cur:
            cur.execute(
                "SELECT table_name, column_name, data_type FROM information_schema.columns "
                "WHERE table_schema = %s", (schema,))
            return {(t, col): dt for t, col, dt in cur.fetchall()}
    finally:
        c.close()


def _start(url):
    """A process start: `_init_schema` runs once per database_url per process
    (`ProjectRegistry._pg_schema_ready`), so forget the url to simulate one."""
    ProjectRegistry._pg_schema_ready.discard(url)
    return ProjectRegistry(database_url=url)


@pytest.fixture
def own_schema(pg_test_schema):
    name = f"{pg_test_schema}_bigint"       # swept with this run's other test schemas
    c, cfg = _admin()
    with c.cursor() as cur:
        cur.execute(f'DROP SCHEMA IF EXISTS "{name}" CASCADE')
        cur.execute(f'CREATE SCHEMA "{name}"')
    url = (f"postgresql://{cfg.db_user}:{cfg.password}@{cfg.host}:{cfg.port}/{cfg.dbname}"
           f"?options=-csearch_path%3D{name}")
    yield name, url, c
    with c.cursor() as cur:
        cur.execute(f'DROP SCHEMA IF EXISTS "{name}" CASCADE')
    c.close()


@pg
def test_fresh_postgres_registry_has_bigint_columns(own_schema):
    name, url, _ = own_schema
    _start(url)
    types = _types(name)
    for key in _PG_INT4_TO_BIGINT_COLUMNS:
        assert types[key] == "bigint", key
    assert types[("database_tables", "column_count")] == "integer"      # not widened


@pg
def test_migration_widens_forced_integer_columns_keeps_data_and_second_start_is_a_no_op(own_schema):
    name, url, admin = own_schema
    reg = _start(url)
    reg.register_database(_entity())
    reg.write_detail_rows("database_tables", "d", "2026-10-10T00:00:00", rows=[
        {"schema_name": "s", "table_name": "t", "size_bytes": 123, "row_count": 45}])
    with admin.cursor() as cur:
        for t, c in _PG_INT4_TO_BIGINT_COLUMNS:
            cur.execute(f'ALTER TABLE "{name}".{t} ALTER COLUMN {c} TYPE INTEGER')
    assert all(_types(name)[k] == "integer" for k in _PG_INT4_TO_BIGINT_COLUMNS)

    reg2 = _start(url)                      # migrates
    assert all(_types(name)[k] == "bigint" for k in _PG_INT4_TO_BIGINT_COLUMNS)
    row = reg2.query_detail_rows("database_tables", "d", "2026-10-10T00:00:00")[0]
    assert (row["size_bytes"], row["row_count"]) == (123, 45)

    with admin.cursor() as cur:       # a second start must issue no ALTER at all
        cur.execute("SELECT oid, relfilenode FROM pg_class WHERE relname='database_tables' "
                    "AND relnamespace = %s::regnamespace", (name,))
        before = cur.fetchone()
    _start(url)
    with admin.cursor() as cur:
        cur.execute("SELECT oid, relfilenode FROM pg_class WHERE relname='database_tables' "
                    "AND relnamespace = %s::regnamespace", (name,))
        assert cur.fetchone() == before       # a rewrite would change relfilenode
    reg2.write_detail_rows("database_tables", "d", "2026-10-10T01:00:00", rows=[
        {"schema_name": "s", "table_name": "t", "size_bytes": BIG}])


@pg
def test_values_over_int4_round_trip_through_record_database_survey_without_the_warning(
        own_schema, caplog):
    _, url, _ = own_schema
    reg = _start(url)
    reg.register_database(_entity())
    at = "2026-10-10T02:00:00"
    survey = {"surveyed_at": at, "schema_info": {"schemas": [{
        "name": "s", "description": "", "tables": [
            {"name": "a", "type": "BASE TABLE", "description": "", "columns": [],
             "size_bytes": BIG, "row_count": BIG},
            {"name": "b", "type": "BASE TABLE", "description": "", "columns": [],
             "size_bytes": SCHEMA_TOTAL - BIG, "row_count": 1}]}]}}
    with caplog.at_level(logging.WARNING):
        reg.record_database_survey("d", 1, 2, 0, survey, surveyed_at=at)
    assert "Structured detail rows not written" not in caplog.text
    tables = {r["table_name"]: r for r in reg.query_detail_rows("database_tables", "d", at)}
    assert tables["a"]["size_bytes"] == BIG and tables["a"]["row_count"] == BIG
    sch = reg.query_detail_rows("database_schemas", "d", at)[0]
    assert sch["total_table_size_bytes"] == SCHEMA_TOTAL
    reg.write_detail_rows("database_table_activity", "d", at, rows=[
        {"schema_name": "s", "table_name": "a", "rows_inserted": BIG, "seq_scan": BIG,
         "live_tuples": BIG, "pending_changes": BIG}])
    act = reg.query_detail_rows("database_table_activity", "d", at)[0]
    assert act["rows_inserted"] == BIG and act["pending_changes"] == BIG


@pg
def test_a_lock_held_by_another_process_fails_init_with_a_clear_message_and_widens_nothing(own_schema):
    import time
    name, url, admin = own_schema
    _start(url)
    with admin.cursor() as cur:
        for t, c in _PG_INT4_TO_BIGINT_COLUMNS:
            cur.execute(f'ALTER TABLE "{name}".{t} ALTER COLUMN {c} TYPE INTEGER')
    other, _ = _admin()
    other.autocommit = False
    try:
        with other.cursor() as cur:       # a reader holding the table open
            cur.execute(f'LOCK TABLE "{name}".database_tables IN ACCESS SHARE MODE')
        t0 = time.monotonic()
        with pytest.raises(RuntimeError, match=r"database_tables.*another process holds a lock.*"
                                               r"stop other Resource Explorer processes and restart"):
            _start(url)
        assert time.monotonic() - t0 < 30
    finally:
        other.rollback()
        other.close()
    assert all(_types(name)[k] == "integer" for k in _PG_INT4_TO_BIGINT_COLUMNS)  # rolled back
    ProjectRegistry._pg_schema_ready.discard(url)
    _start(url)                                                    # lock gone: succeeds
    assert all(_types(name)[k] == "bigint" for k in _PG_INT4_TO_BIGINT_COLUMNS)


def _project_and_doc_round_trip(reg):
    reg.add(Project(slug="big", display_name="Big", github_url="https://github.com/a/big"))
    reg.upsert_file_inventory("big", [("a.bin", BIG)])
    reg.store_data_profiles("big", [{"file_path": "d.parquet", "format": "parquet",
                                     "row_count": BIG, "col_count": 3, "schema_json": "{}",
                                     "null_summary": "", "file_size_bytes": SCHEMA_TOTAL}])
    prof = reg.get_data_profiles("big")[0]
    assert (prof["row_count"], prof["file_size_bytes"]) == (BIG, SCHEMA_TOTAL)
    src = reg.add_doc_source("project", "big", "https://example.org/x.pdf")
    got = reg.record_doc_source_probe("project", "big", src["id"], state="reachable",
                                      status_code=200, elapsed_ms=5, byte_count=BIG)
    assert got["probe_byte_count"] == BIG
    with reg._conn() as conn:
        conn.execute("UPDATE doc_sources SET ingested_bytes=? WHERE id=?", (SCHEMA_TOTAL, src["id"]))
        row = conn.execute("SELECT ingested_bytes FROM doc_sources WHERE id=?", (src["id"],)).fetchone()
        assert row["ingested_bytes"] == SCHEMA_TOTAL
        inv = conn.execute("SELECT file_size_bytes FROM project_file_inventory "
                           "WHERE project_slug='big'").fetchone()
        assert inv["file_size_bytes"] == BIG


def test_project_and_doc_columns_hold_values_over_int4_on_sqlite(tmp_path):
    _project_and_doc_round_trip(ProjectRegistry(db_path=str(tmp_path / "r.db")))


@pg
def test_project_and_doc_columns_hold_values_over_int4_on_postgres(own_schema):
    _, url, _ = own_schema
    _project_and_doc_round_trip(_start(url))
