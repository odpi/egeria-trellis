"""Brief D 7f.2 / 7f.3 against real Postgres (throwaway schema only).

`_get_table_statistics` used to end in `LIMIT 100`: a database with 219 tables
left 119 sizes NULL in every snapshot. The unit tests fake the connection, so
only a real catalog read can pin that every user table gets a size.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.requires_pgvector

N_TABLES = 130          # comfortably past the old LIMIT 100


@pytest.fixture
def pg_conn(pg_test_schema):
    import psycopg2
    from resource_explorer.config import get_config
    from resource_explorer.surveyors.database.connection import PostgreSQLConnection

    cfg = get_config().pgvector
    schema = f"{pg_test_schema}_dbsnap"        # swept with its pid-owner's schemas
    admin = psycopg2.connect(host=cfg.host, port=cfg.port, dbname=cfg.dbname,
                             user=cfg.db_user, password=cfg.password)
    admin.autocommit = True
    with admin.cursor() as cur:
        cur.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
        cur.execute(f'CREATE SCHEMA "{schema}"')
        for i in range(N_TABLES):
            cur.execute(f'CREATE TABLE "{schema}".t_{i:03d} (id int primary key, v text)')
        cur.execute(f'INSERT INTO "{schema}".t_000 SELECT g, repeat(\'x\', 200) '
                    f'FROM generate_series(1, 500) g')
        cur.execute(f'CREATE MATERIALIZED VIEW "{schema}".mv_000 AS '
                    f'SELECT id FROM "{schema}".t_000')
    conn = PostgreSQLConnection(cfg.host, cfg.port, cfg.dbname, cfg.db_user, cfg.password)
    conn.connect()
    try:
        yield conn, schema
    finally:
        conn._conn.close()
        with admin.cursor() as cur:
            cur.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
        admin.close()


def test_every_user_table_in_the_schema_gets_a_size(pg_conn):
    conn, schema = pg_conn
    mine = {r["tablename"]: r for r in conn._get_table_statistics()
            if r["schemaname"] == schema}
    expected = {f"t_{i:03d}" for i in range(N_TABLES)} | {"mv_000"}
    assert set(mine) == expected, (
        f"{len(expected - set(mine))} table(s) came back unsized -- the old "
        f"LIMIT 100 left them NULL")
    for name, row in mine.items():
        assert row["total_bytes"] is not None and row["total_bytes"] > 0, name
        assert row["relation_bytes"] is not None, name
        assert row["total_bytes"] >= row["relation_bytes"], (
            "pg_total_relation_size includes the heap that pg_relation_size reports")
    assert mine["t_000"]["total_bytes"] > mine["t_001"]["total_bytes"]


def test_sizes_arrive_as_table_size_bytes_through_the_store_path(pg_conn, tmp_path):
    """Sizes read here reach `database_tables.size_bytes` for all N tables."""
    from resource_explorer.registry import DatabaseEntity, ProjectRegistry
    from resource_explorer.surveyors.database.database_surveyor import DatabaseSurveyor

    conn, schema = pg_conn
    registry = ProjectRegistry(db_path=str(tmp_path / "t.db"))
    entity = DatabaseEntity(slug="d", display_name="D", db_type="postgresql",
                            host="h", port=5432, database_name="d",
                            db_user="u", db_password="p")
    registry.register_database(entity)
    names = sorted(f"t_{i:03d}" for i in range(N_TABLES))
    schema_info = {
        "schemas": [{"name": schema, "description": "", "tables": [
            {"name": n, "type": "BASE TABLE", "description": "", "columns": [],
             "source": "information_schema"} for n in names]}],
        "total_tables": len(names), "total_columns": 0,
    }
    surveyor = DatabaseSurveyor(entity, {"user": "u", "password": "p"}, registry)
    results = {"schema_info": schema_info, "statistics": conn.get_statistics(),
               "annotations": [], "surveyed_at": "2026-10-09T10:00:00"}
    surveyor._store_results(results)
    tables = registry.query_detail_rows("database_tables", "d", "2026-10-09T10:00:00")
    assert len(tables) == N_TABLES
    assert all(t["size_bytes"] and t["size_bytes"] > 0 for t in tables)
    sch = registry.query_detail_rows("database_schemas", "d", "2026-10-09T10:00:00")
    assert sch[0]["total_table_size_bytes"] == sum(t["size_bytes"] for t in tables)


def test_the_reset_word_is_read_from_pg_stat_database(pg_conn):
    from resource_explorer.registry import STATS_NEVER_RESET

    conn, _ = pg_conn
    evidence = conn.get_stats_reset_evidence()
    assert evidence is not None, "pg_stat_database is readable by any role"
    assert evidence == STATS_NEVER_RESET or evidence[:2] == "20"
    assert conn.get_statistics()["stats_reset_evidence"] == evidence


def test_index_and_heap_sizes_are_read_for_every_table(pg_conn):
    conn, schema = pg_conn
    mine = [r for r in conn._get_table_statistics() if r["schemaname"] == schema]
    assert len(mine) == N_TABLES + 1
    for r in mine:
        assert r["index_bytes"] is not None and r["relation_bytes"] is not None, r
    t0 = next(r for r in mine if r["tablename"] == "t_000")
    assert t0["index_bytes"] > 0, "a primary key has an index"
    assert t0["total_bytes"] >= t0["relation_bytes"] + t0["index_bytes"]


def test_migration_adds_the_columns_on_a_postgres_registry_that_lacks_them(pg_test_schema):
    from resource_explorer.config import get_config
    from resource_explorer.registry import ProjectRegistry
    import psycopg2

    cfg = get_config().pgvector
    schema = f"{pg_test_schema}_dbcols"
    admin = psycopg2.connect(host=cfg.host, port=cfg.port, dbname=cfg.dbname,
                             user=cfg.db_user, password=cfg.password)
    admin.autocommit = True
    with admin.cursor() as cur:
        cur.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
        cur.execute(f'CREATE SCHEMA "{schema}"')
    url = (f"postgresql://{cfg.db_user}:{cfg.password}@{cfg.host}:{cfg.port}"
           f"/{cfg.dbname}?options=-csearch_path%3D{schema}")

    def cols():
        with admin.cursor() as cur:
            cur.execute("SELECT column_name, data_type FROM information_schema.columns "
                        "WHERE table_schema = %s AND table_name = 'database_tables'",
                        (schema,))
            return dict(cur.fetchall())
    try:
        ProjectRegistry(database_url=url)                       # fresh: has them
        assert cols().get("index_bytes") == "bigint"
        assert cols().get("table_bytes") == "bigint"
        with admin.cursor() as cur:                             # an older registry
            cur.execute(f'ALTER TABLE "{schema}".database_tables '
                        f'DROP COLUMN table_bytes, DROP COLUMN index_bytes')
        assert "index_bytes" not in cols()
        for _ in range(2):                                      # start twice: idempotent
            # schema init runs once per PROCESS per URL; forget it, as a restart does
            ProjectRegistry._pg_schema_ready.discard(url)
            ProjectRegistry(database_url=url)
            assert cols().get("index_bytes") == "bigint"
            assert cols().get("table_bytes") == "bigint"
    finally:
        with admin.cursor() as cur:
            cur.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
        admin.close()
