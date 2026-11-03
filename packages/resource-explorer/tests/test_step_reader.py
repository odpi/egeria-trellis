"""The reader a user step is handed (`survey_step_host.step_reader`): RE's typed read methods only (no `query`, no SQL
text from the step), on a connection the step cannot reach and that cannot write, on both backends.

SQLite tests run everywhere. The Postgres tests are `requires_pgvector`: they create and drop ONLY their own schema,
`resource_explorer_test_stepreader_<pid>` (a name the conftest's orphan sweep never matches), and never construct a
ProjectRegistry.
"""
from __future__ import annotations

import os
import sqlite3

import pytest

from resource_explorer.surveyors.survey_step_host import STEP_ODS_METHODS, step_reader
from trellis_analytics.readers.ods import OdsUnreadable

#: The tables and columns the typed methods read (trellis_analytics.readers.ods), and one row each.
DDL = [
    "CREATE TABLE database_surveys (database_slug TEXT, surveyed_at TEXT, source TEXT, invalid_at TEXT)",
    "CREATE TABLE database_tables (database_slug TEXT, surveyed_at TEXT, source TEXT, schema_name TEXT, "
    "table_name TEXT, table_type TEXT, row_count INTEGER, state TEXT)",
    "CREATE TABLE catalogue_commit_proofs (id INTEGER, database_slug TEXT, node_kind TEXT, schema_name TEXT, "
    "table_name TEXT, proof TEXT, element_guid TEXT, read_at TEXT, detail_json TEXT)",
    "CREATE TABLE catalogue_scope_events (id INTEGER, database_slug TEXT, node_kind TEXT, schema_name TEXT, "
    "table_name TEXT, choice TEXT, action TEXT, source TEXT, proposal_rule TEXT, changed_at TEXT)",
    "CREATE TABLE resource_scope_events (id INTEGER, resource_type TEXT, resource_slug TEXT, locator TEXT, "
    "kind TEXT, choice TEXT, action TEXT, source TEXT, proposal_rule TEXT, changed_at TEXT)",
    "CREATE TABLE egeria_outbox (id INTEGER, entity_type TEXT, entity_slug TEXT, status TEXT)",
    "CREATE TABLE database_table_activity (database_slug TEXT, surveyed_at TEXT, source TEXT, schema_name TEXT, "
    "table_name TEXT, rows_inserted BIGINT, rows_updated BIGINT, rows_deleted BIGINT, live_tuples BIGINT, "
    "dead_tuples BIGINT, seq_scan BIGINT, idx_scan BIGINT, last_vacuum TEXT, last_autovacuum TEXT, "
    "last_analyze TEXT, last_autoanalyze TEXT, pending_changes BIGINT, stats_reset TEXT, state TEXT)",
    "CREATE TABLE database_columns (database_slug TEXT, surveyed_at TEXT, source TEXT, schema_name TEXT, "
    "table_name TEXT, column_name TEXT, is_primary_key INTEGER, foreign_key_json TEXT)",
]
ROWS = [
    "INSERT INTO database_surveys VALUES ('shop', '2026-10-01', 'local', NULL)",
    "INSERT INTO database_tables VALUES ('shop', '2026-10-01', 'local', 'public', 'orders', 'BASE TABLE', 5, 'm')",
    "INSERT INTO catalogue_commit_proofs VALUES (1, 'shop', 'schema', 'public', '', 'p', 'g', 'r', '{\"a\": 1}')",
    "INSERT INTO catalogue_scope_events VALUES (1, 'shop', 'schema', 'public', '', 'include', 'set', 's', '', 'c')",
    "INSERT INTO resource_scope_events VALUES (1, 'database', 'shop', 'public', 'schema', 'include', 'set', 's', '', 'c')",
    "INSERT INTO egeria_outbox VALUES (1, 'database', 'shop', 'done')",
    "INSERT INTO database_table_activity VALUES ('shop', '2026-10-01', 'local', 'public', 'orders', 7, 2, 1, 40, 0, "
    "3, 4, '', '', '2026-09-30', '', 6, NULL, 'measured')",
    "INSERT INTO database_columns VALUES ('shop', '2026-10-01', 'local', 'public', 'orders', 'customer_id', 0, "
    "'{\"foreign_schema\": \"public\", \"foreign_table\": \"customer\", \"foreign_column\": \"id\"}')",
]


def _reachable(root, limit: int = 4000):
    from tests.test_engine_host_library_steps import _reachable as walk

    return walk(root, limit)


def _requests_of(ods, kind):
    """The request queue of THIS reader, read from its own method's closure (never found by enumeration)."""
    [requests] = [c.cell_contents for c in ods.outbox_status_counts.__closure__
                  if isinstance(c.cell_contents, kind)]
    return requests


def _all_typed_reads(ods) -> dict:
    return {
        "row_count_snapshots": ods.row_count_snapshots("shop", limit=2),
        "commit_proofs": ods.commit_proofs("shop"),
        "catalogue_scope_events": ods.catalogue_scope_events("shop"),
        "resource_scope_events": ods.resource_scope_events("database", "shop"),
        "outbox_status_counts": ods.outbox_status_counts("database", "shop"),
        "table_activity_snapshots": ods.table_activity_snapshots("shop", limit=2),
        "table_keys": ods.table_keys("shop"),
    }


def _assert_reads(got):
    assert got["row_count_snapshots"][0]["tables"][0]["row_count"] == 5
    assert got["commit_proofs"][0]["detail"] == {"a": 1}
    assert got["catalogue_scope_events"][0]["choice"] == "include"
    assert got["resource_scope_events"][0]["locator"] == "public"
    assert got["outbox_status_counts"] == {"done": [1]}
    [snap] = got["table_activity_snapshots"]
    assert (snap["surveyed_at"], snap["source"]) == ("2026-10-01", "local")
    [act] = snap["tables"]
    assert (act["table_name"], act["rows_inserted"], act["live_tuples"], act["last_analyze"]) == (
        "orders", 7, 40, "2026-09-30")
    assert "seq_scan" not in act                                  # only the columns the read names
    keys = got["table_keys"]
    [orders] = keys["tables"]
    assert (keys["surveyed_at"], orders["table_type"], orders["keys_captured"]) == (
        "2026-10-01", "BASE TABLE", True)
    assert orders["foreign_keys"] == [{"column": "customer_id", "to_schema": "public", "to_table": "customer",
                                       "to_column": "id"}]


@pytest.fixture
def sqlite_url(tmp_path):
    path = tmp_path / "ods.sqlite"
    c = sqlite3.connect(path)
    for sql in DDL + ROWS:
        c.execute(sql)
    c.commit()
    c.close()
    return f"sqlite:///{path}"


def _count(url) -> int:
    c = sqlite3.connect(url.removeprefix("sqlite:///"))
    try:
        return c.execute("SELECT count(*) FROM egeria_outbox").fetchone()[0]
    finally:
        c.close()


def test_a_step_reader_offers_the_typed_reads_and_no_query(sqlite_url):
    with step_reader(sqlite_url) as ods:
        assert not hasattr(ods, "query")
        assert {a for a in dir(ods) if not a.startswith("_")} == set(STEP_ODS_METHODS)
        assert "query" not in STEP_ODS_METHODS
        with pytest.raises(AttributeError):
            ods.query = lambda *a: None


def test_every_typed_read_works_on_sqlite(sqlite_url):
    with step_reader(sqlite_url) as ods:
        _assert_reads(_all_typed_reads(ods))


def test_the_reader_thread_refuses_any_other_name_even_sent_by_hand(sqlite_url):
    """A step that digs out the request queue and asks for `query` (or any reader method) by name is refused."""
    import queue

    from resource_explorer.surveyors.survey_step_host import _Requests

    with step_reader(sqlite_url) as ods:
        requests = _requests_of(ods, _Requests)
        for name in ("query", "_conn", "registry_view", "__init__"):
            reply: queue.Queue = queue.Queue(maxsize=1)
            requests.put((name, ("delete from egeria_outbox",), {}, reply))
            status, words = reply.get(timeout=10)
            assert status == "error" and "not one of the reads a step may make" in words, (name, words)
    assert _count(sqlite_url) == 1


def test_sqlite_attach_pragma_and_writes_are_refused_by_the_connection_itself(sqlite_url, tmp_path):
    from resource_explorer.surveyors.survey_step_host import _execute_read_only, _open_read_only

    conn, pg = _open_read_only(sqlite_url)
    try:
        for sql in (f"ATTACH DATABASE 'file:{tmp_path / 'ods.sqlite'}?mode=rw' AS w", "PRAGMA query_only = OFF",
                    "DELETE FROM egeria_outbox", "CREATE TABLE z (a)", "select 1; delete from egeria_outbox"):
            with pytest.raises(Exception):
                _execute_read_only(conn, pg, sql, ())
    finally:
        conn.close()
    assert _count(sqlite_url) == 1


def test_sqlite_no_connection_registry_or_url_is_reachable(sqlite_url):
    from resource_explorer.registry import ProjectRegistry

    with step_reader(sqlite_url) as ods:
        objs = _reachable(ods)
    path = sqlite_url.removeprefix("sqlite:///")
    assert not [o for o in objs if isinstance(o, sqlite3.Connection)]
    assert not [o for o in objs if isinstance(o, ProjectRegistry)]
    assert not [o for o in objs if isinstance(o, str) and path in o]


def test_the_reader_stops_with_its_block(sqlite_url):
    import threading

    before = threading.active_count()
    with step_reader(sqlite_url) as ods:
        ods.outbox_status_counts("database", "shop")
    assert threading.active_count() == before
    with pytest.raises(OdsUnreadable):
        ods.outbox_status_counts("database", "shop")


# ── Postgres ──────────────────────────────────────────────────────────────────

@pytest.fixture
def pg_url():
    import psycopg2

    from tests.conftest import _PGVECTOR_AVAILABLE

    if not _PGVECTOR_AVAILABLE:
        pytest.skip("pgvector/Postgres not reachable")
    from resource_explorer.config import get_config

    cfg = get_config().pgvector
    schema = f"resource_explorer_test_stepreader_{os.getpid()}"
    admin = psycopg2.connect(host=cfg.host, port=cfg.port, dbname=cfg.dbname, user=cfg.db_user,
                             password=cfg.password, options=f"-csearch_path={schema}")
    admin.autocommit = True
    with admin.cursor() as cur:
        cur.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
        cur.execute(f'CREATE SCHEMA "{schema}"')
        for sql in DDL + ROWS:
            cur.execute(sql)
    url = (f"postgresql://{cfg.db_user}:{cfg.password}@{cfg.host}:{cfg.port}/{cfg.dbname}"
           f"?options=-csearch_path%3D{schema}")
    try:
        yield url, admin, schema
    finally:
        with admin.cursor() as cur:
            cur.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
        admin.close()


def _pg_count(admin, schema) -> int:
    with admin.cursor() as cur:
        cur.execute(f'SELECT count(*) FROM "{schema}".egeria_outbox')
        return cur.fetchone()[0]


@pytest.mark.requires_pgvector
def test_every_typed_read_works_on_postgres(pg_url):
    url, _admin, _schema = pg_url
    with step_reader(url) as ods:
        assert not hasattr(ods, "query")
        _assert_reads(_all_typed_reads(ods))


@pytest.mark.requires_pgvector
@pytest.mark.parametrize("sql", [
    # the review's probe: a commit inside the text ends the READ ONLY transaction
    "select E'\\'' as a; commit; select current_setting('transaction_read_only') as ro, E'\\'' as b",
    "select set_config('default_transaction_read_only','off',false); delete from egeria_outbox",
    "select 1; delete from egeria_outbox",
    "with d as (delete from egeria_outbox returning id) select * from d",
    "delete from egeria_outbox",
])
def test_pg_executor_runs_one_statement_in_a_read_only_transaction(pg_url, sql):
    """Defence in depth under the typed methods: even raw text handed to the executor cannot write or leave the
    READ ONLY transaction (one statement, through a server-side cursor; always rolled back)."""
    from resource_explorer.surveyors.survey_step_host import _execute_read_only, _open_read_only

    url, admin, schema = pg_url
    conn, pg = _open_read_only(url)
    try:
        with pytest.raises(Exception):
            _execute_read_only(conn, pg, sql, ())
        # a set_config that ran inside a transaction does not survive it
        _execute_read_only(conn, pg, "select set_config('default_transaction_read_only','off',false) as s", ())
        assert _execute_read_only(conn, pg, "select current_setting('transaction_read_only') as ro", ()) == [
            {"ro": "on"}]
    finally:
        conn.close()
    assert _pg_count(admin, schema) == 1


@pytest.mark.requires_pgvector
def test_pg_no_connection_or_url_is_reachable(pg_url):
    import psycopg2.extensions

    url, _admin, _schema = pg_url
    with step_reader(url) as ods:
        objs = _reachable(ods)
    assert not [o for o in objs if isinstance(o, psycopg2.extensions.connection)]
    assert not [o for o in objs if isinstance(o, str) and ("advisor@" in o or "search_path" in o)]


# ── no step code runs in the reader thread ────────────────────────────────────

class _Evil(str):
    """A str whose methods record which thread called them."""
    seen: list = []

    def _mark(self):
        import threading

        _Evil.seen.append(threading.current_thread().name)

    def replace(self, *a, **k):
        self._mark()
        return str.replace(self, *a, **k)

    def lower(self):
        self._mark()
        return str.lower(self)

    def __eq__(self, other):
        self._mark()
        return str.__eq__(self, other)

    def __hash__(self):
        self._mark()
        return str.__hash__(self)

    def __str__(self):
        self._mark()
        return str.__str__(self)


def test_a_str_subclass_argument_runs_no_step_code_in_the_reader_thread(sqlite_url):
    _Evil.seen.clear()
    with step_reader(sqlite_url) as ods:
        ods.outbox_status_counts(_Evil("database"), _Evil("shop"))
        ods.row_count_snapshots(_Evil("shop"), limit=2)
    assert "re-step-reader" not in _Evil.seen, _Evil.seen


def test_anything_but_plain_values_is_refused(sqlite_url):
    with step_reader(sqlite_url) as ods:
        with pytest.raises(TypeError):
            ods.row_count_snapshots(["shop"])
        with pytest.raises(TypeError):
            ods.row_count_snapshots("shop", limit=2.5)


def test_hand_sent_requests_with_odd_values_or_reply_objects_run_no_step_code(sqlite_url):
    import queue

    from resource_explorer.surveyors.survey_step_host import _Requests

    class OddReply(queue.Queue):
        def put(self, *a, **k):
            _Evil.seen.append(__import__("threading").current_thread().name)
            return super().put(*a, **k)

    _Evil.seen.clear()
    with step_reader(sqlite_url) as ods:
        requests = _requests_of(ods, _Requests)
        reply: queue.Queue = queue.Queue(maxsize=1)
        requests.put(("row_count_snapshots", (_Evil("shop"),), {}, reply))
        status, words = reply.get(timeout=10)
        assert status == "error" and "plain" in words
        requests.put(("row_count_snapshots", ("shop",), {}, OddReply()))
        ods.outbox_status_counts("database", "shop")      # the thread is still serving
    assert "re-step-reader" not in _Evil.seen, _Evil.seen


# ── the two typed reads the library steps added (statistics_freshness, change_rates, hub_tables) ──

INJECTIONS = ("shop'; DELETE FROM egeria_outbox; --", "shop' OR '1'='1", "x') UNION SELECT * FROM egeria_outbox --")


def _new_reads(ods, slug):
    return ods.table_activity_snapshots(slug, limit=2), ods.table_keys(slug)


def test_the_new_reads_take_their_parameters_as_values_never_as_sql(sqlite_url):
    with step_reader(sqlite_url) as ods:
        for text in INJECTIONS:
            assert _new_reads(ods, text) == ([], None), text
    assert _count(sqlite_url) == 1


def test_the_new_reads_refuse_anything_but_plain_values_and_bound_their_limit(sqlite_url):
    with step_reader(sqlite_url) as ods:
        for bad in (["shop"], {"slug": "shop"}, 2.5, b"shop"):
            with pytest.raises(TypeError):
                ods.table_activity_snapshots(bad)
            with pytest.raises(TypeError):
                ods.table_keys(bad)
        with pytest.raises(TypeError):
            ods.table_activity_snapshots("shop", limit=2.5)
        assert len(ods.table_activity_snapshots("shop", limit=0)) == 1      # never an empty answer


def test_the_activity_read_is_capped_at_ten_snapshots(sqlite_url):
    """Twelve stored snapshots: any limit above ten returns the newest ten, never the history."""
    c = sqlite3.connect(sqlite_url.removeprefix("sqlite:///"))
    for day in range(2, 13):
        c.execute("INSERT INTO database_table_activity (database_slug, surveyed_at, source, schema_name, table_name, "
                  "rows_inserted, live_tuples) VALUES ('shop', ?, 'local', 'public', 'orders', ?, 1)",
                  (f"2026-10-{day:02d}", day))
    c.commit()
    c.close()
    with step_reader(sqlite_url) as ods:
        for limit in (11, 12, 10**9):
            snaps = ods.table_activity_snapshots("shop", limit=limit)
            assert len(snaps) == 10, (limit, len(snaps))
            assert [s["surveyed_at"] for s in snaps][0] == "2026-10-12"           # the newest ten, newest first
        assert len(ods.table_activity_snapshots("shop", limit=3)) == 3


def test_the_new_reads_offer_no_other_way_in(sqlite_url):
    """The step is handed the two new methods and nothing else new: no `query`, no table name, no column list."""
    import inspect

    from trellis_analytics.readers.ods import OdsReader

    assert {"table_activity_snapshots", "table_keys"} <= set(STEP_ODS_METHODS)
    assert list(inspect.signature(OdsReader.table_activity_snapshots).parameters) == ["self", "slug", "limit"]
    assert list(inspect.signature(OdsReader.table_keys).parameters) == ["self", "slug"]
    with step_reader(sqlite_url) as ods:
        assert not hasattr(ods, "query") and not hasattr(ods, "MAX_SNAPSHOTS")


@pytest.mark.requires_pgvector
def test_pg_the_new_reads_take_their_parameters_as_values_never_as_sql(pg_url):
    url, admin, schema = pg_url
    with step_reader(url) as ods:
        for text in INJECTIONS:
            assert _new_reads(ods, text) == ([], None), text
    assert _pg_count(admin, schema) == 1


def test_a_list_of_slugs_is_refused_for_a_step_about_one_resource_and_allowed_only_on_a_set_steps_reader(sqlite_url):
    with step_reader(sqlite_url) as ods:
        with pytest.raises(TypeError):
            ods.row_count_snapshots(["shop"])
    with step_reader(sqlite_url, allow_slug_lists=True) as ods:
        got = ods.row_count_snapshots(["shop"])
        assert list(got) == ["shop"] and isinstance(got["shop"], list)
        assert ods.row_count_snapshots("shop") == got["shop"]           # the plain call is unchanged
        for bad in ([1], [b"shop"], [["shop"]], ["shop", None], [{"slug": "shop"}]):
            with pytest.raises(TypeError):
                ods.row_count_snapshots(bad)
        with pytest.raises(TypeError, match="at most"):
            ods.row_count_snapshots(["shop"] * 501)
        with pytest.raises(TypeError):
            ods.row_count_snapshots("shop", limit=[2])                  # a list is never allowed beyond the slug
        with pytest.raises(TypeError):
            ods.resource_scope_events(["database"], "shop")             # first parameter is not a slug: refused
