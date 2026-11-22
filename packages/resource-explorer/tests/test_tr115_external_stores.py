"""TR-115 (TR-112b part 2): in RE_SCHEMA_MODE=external the four stores that still run their own DDL must not.

The stores: the metrics collector (RE), `trellis_feedback`, `trellis_vectorstore` (including CREATE EXTENSION vector)
and `trellis_artifact_tree`. Design and decisions: docs/tr115-external-schema-plan.md.

STATE: these tests are written FIRST and FAIL on purpose (the code change is not made yet). Each failing test names the
missing piece (a `ddl=` keyword, `schema_stores`, ...). The "guard" tests at the end pass today and must keep passing: compose and
RE_SCHEMA_MODE=auto stay exactly as they are.

Postgres cases need RE_SCHEMA_TEST_PG_URL (a THROWAWAY database, no search_path); they skip otherwise.
"""
from __future__ import annotations

import os
import re
import uuid
from unittest import mock

import pytest

BASE_URL = os.environ.get("RE_SCHEMA_TEST_PG_URL", "")
needs_pg = pytest.mark.skipif(not BASE_URL, reason="RE_SCHEMA_TEST_PG_URL (a throwaway Postgres) is not set")
DDL = re.compile(r"^\s*(CREATE|ALTER|DROP|COMMENT|TRUNCATE)\b", re.I)


class _Spy:
    """Records every SQL string a SQLAlchemy engine or a psycopg2 cursor is asked to run."""

    def __init__(self):
        self.sql: list[str] = []

    @property
    def ddl(self) -> list[str]:
        return [s for s in self.sql if DDL.match(s)]


def _spy_engine(engine, spy: _Spy):
    from sqlalchemy import event
    event.listen(engine, "before_cursor_execute", lambda conn, cur, stmt, params, ctx, many: spy.sql.append(stmt))


def _not_ready_types():
    """The 'schema not ready' errors a store may raise: RE's SchemaNotReady, or the library's own subclass of it."""
    from resource_explorer.registry import SchemaNotReady
    return (SchemaNotReady,)


# ── metrics collector (SQLite stands in: the DDL path is shared, the DDL is the same statements) ───────────────────

def test_metrics_collector_external_runs_no_ddl_and_says_what_to_run(tmp_path):
    from resource_explorer.observability.metrics_collector import MetricsCollector
    spy = _Spy()
    with mock.patch("resource_explorer.observability.metrics_collector.create_engine",
                    side_effect=lambda url, **kw: (lambda e: (_spy_engine(e, spy), e)[1])(__import__("sqlalchemy").create_engine(url, **kw))):
        with pytest.raises(_not_ready_types(), match="schema apply"):
            MetricsCollector(database_url=f"sqlite:///{tmp_path}/m.db", ddl="external")     # no ddl= keyword yet: TypeError
    assert spy.ddl == [], spy.ddl


# ── trellis_feedback ───────────────────────────────────────────────────────────────────────────────────────────────

def test_feedback_store_external_runs_no_ddl_and_raises_when_tables_are_missing(tmp_path):
    from trellis_feedback import FeedbackStore
    spy = _Spy()
    from sqlalchemy import create_engine
    eng = create_engine(f"sqlite:///{tmp_path}/f.db")
    _spy_engine(eng, spy)
    with pytest.raises(Exception, match="SchemaNotReady|schema apply"):
        FeedbackStore(f"sqlite:///{tmp_path}/f.db", "re", engine=eng, ddl="external")     # no ddl= keyword yet: TypeError
    assert spy.ddl == [], spy.ddl


def test_feedback_store_external_works_once_the_schema_exists(tmp_path):
    from trellis_feedback import FeedbackStore
    url = f"sqlite:///{tmp_path}/f.db"
    FeedbackStore(url, "re")                                   # auto creates the tables (today's behaviour)
    store = FeedbackStore(url, "re", ddl="external")           # then external opens it and reads: no ddl= keyword yet
    assert store is not None


# ── trellis_artifact_tree ──────────────────────────────────────────────────────────────────────────────────────────

def test_artifact_tree_store_external_refuses_create_schema_and_checks_instead():
    from trellis_artifact_tree import ArtifactTreeConfig, ArtifactTreeStore
    cfg = ArtifactTreeConfig(host="x", port=1, dbname="d", user="u", password="p")
    store = ArtifactTreeStore(cfg, ddl="external")             # no ddl= keyword yet: TypeError
    with pytest.raises(Exception, match="external"):
        store.create_schema()                                  # only the schema Job may: apply_schema()
    assert hasattr(store, "apply_schema") and hasattr(store, "check_schema")


def test_re_artifact_tree_sink_never_creates_the_schema_in_external_mode(monkeypatch):
    """build_trees() calls store.create_schema() on every ingestion run (artifact_tree_sink.py): in external mode it must
    check the schema instead and never send DDL."""
    from resource_explorer.ingestion import artifact_tree_sink as sink
    monkeypatch.setenv("RE_SCHEMA_MODE", "external")
    store = mock.MagicMock()
    monkeypatch.setattr(sink, "_store", lambda: store)
    monkeypatch.setattr(sink, "_schema_ready", False)
    cfg = mock.MagicMock()
    cfg.artifact_tree.enabled = True                       # off by default: nothing would run
    monkeypatch.setattr("resource_explorer.config.get_config", lambda: cfg)
    sink.build_trees([("a.md", "# Title\n\nbody\n")], "slug")
    store.create_schema.assert_not_called()
    store.check_schema.assert_called()


# ── trellis_vectorstore ────────────────────────────────────────────────────────────────────────────────────────────

def _fake_pg(spy: _Spy):
    """psycopg2.connect and the pool, recording every execute()."""
    cur = mock.MagicMock()
    cur.execute.side_effect = lambda sql, params=None: spy.sql.append(str(sql))
    cur.fetchone.return_value = (1,)
    cur.__enter__.return_value = cur
    conn = mock.MagicMock()
    conn.cursor.return_value = cur
    conn.__enter__.return_value = conn
    return conn


def test_pgvectorstore_external_connect_never_creates_the_extension_or_schema():
    from trellis_vectorstore import PgVectorStore, PgVectorStoreConfig
    spy = _Spy()
    conn = _fake_pg(spy)
    cfg = PgVectorStoreConfig(host="h", port=1, dbname="d", user="u", password="p", schema="resource_explorer")
    with mock.patch("trellis_vectorstore.pg.psycopg2.connect", return_value=conn), \
         mock.patch("trellis_vectorstore.pg.ThreadedConnectionPool"):
        store = PgVectorStore(cfg, metric="cosine", embeddings=mock.MagicMock(), ddl="external")   # no ddl= keyword yet: TypeError
        store.connect()
    assert spy.ddl == [], spy.ddl


def test_pgvectorstore_external_refuses_to_provision_collections_on_insert():
    from trellis_vectorstore import PgVectorStore, PgVectorStoreConfig
    cfg = PgVectorStoreConfig(host="h", port=1, dbname="d", user="u", password="p")
    with pytest.raises(Exception, match="external"):
        PgVectorStore(cfg, metric="cosine", embeddings=mock.MagicMock(), auto_provision_on_insert=True, ddl="external")


# ── RE glue and the schema Job ─────────────────────────────────────────────────────────────────────────────────────

def test_schema_stores_module_exists_with_apply_and_require_ready():
    from resource_explorer import schema_stores            # the one place RE wires the four stores to the schema step
    assert callable(schema_stores.apply_all) and callable(schema_stores.require_ready)


@needs_pg
def test_schema_apply_creates_all_four_stores_and_external_mode_then_runs_no_ddl_anywhere(monkeypatch):
    """The end-to-end proof: one `schema apply` brings the registry AND the four stores to version; a second run sends nothing;
    a process in external mode then constructs every store without DDL, an insert into an undeclared collection fails loudly,
    and a database that was never applied makes require_ready refuse."""
    import psycopg2
    from urllib.parse import urlsplit
    from resource_explorer import schema_stores
    from resource_explorer.registry import SchemaNotReady
    sch = f"re_tr115_{uuid.uuid4().hex[:8]}"
    q = f"?options=-csearch_path%3D{sch}"
    u = urlsplit(re.sub(r"\+\w+", "", BASE_URL))
    for k, v in {"PGVECTOR_HOST": u.hostname, "PGVECTOR_PORT": str(u.port or 5432), "PGVECTOR_DBNAME": u.path.lstrip("/"),
                 "PGVECTOR_USER": u.username, "PGVECTOR_PASSWORD": u.password or "", "PGVECTOR_SCHEMA": sch,
                 "METRICS_DATABASE_URL": BASE_URL + q, "FEEDBACK_DATABASE_URL": BASE_URL + q, "REGISTRY_DATABASE_URL": BASE_URL + q}.items():
        monkeypatch.setenv(k, v)
    from resource_explorer import config as cfgmod
    getattr(cfgmod, "reset_config", lambda: None)()
    try:
        first = schema_stores.apply_all(BASE_URL + q)
        assert first["stores"] == 4
        second = schema_stores.apply_all(BASE_URL + q)
        assert second["stores"] == 0 and {k: v for k, v in second.items() if k != "stores"} == {k: v for k, v in first.items() if k != "stores"}   # a no-op that reports the same counts
        monkeypatch.setenv("RE_SCHEMA_MODE", "external")
        schema_stores._ready.clear()
        schema_stores.require_ready(BASE_URL + q)
        from resource_explorer.observability.metrics_collector import MetricsCollector
        MetricsCollector()                                                               # no DDL, no error
        from resource_explorer.vector_store_pg import PgVectorStore
        v = PgVectorStore()
        v.connect()
        with pytest.raises(Exception, match="not declared.*schema apply"):
            v.insert_with_embeddings("nope_docs", ["t"], [[0.0] * 384])
        empty = f"re_tr115_empty_{uuid.uuid4().hex[:6]}"
        c = psycopg2.connect(re.sub(r"\+\w+", "", BASE_URL))
        c.autocommit = True
        c.cursor().execute(f'CREATE SCHEMA "{empty}"')
        with pytest.raises(SchemaNotReady, match="schema apply"):
            schema_stores.require_ready(BASE_URL + f"?options=-csearch_path%3D{empty}")
    finally:
        c2 = psycopg2.connect(re.sub(r"\+\w+", "", BASE_URL))
        c2.autocommit = True
        c2.cursor().execute(f'DROP SCHEMA IF EXISTS "{sch}" CASCADE')


# ── the fingerprint follows the four packages (the TR-112b read-back W1 miss, again) ──────────────────────────────

# store -> (module whose source holds that store's DDL, one statement to change)
STORE_DDL = {
    "metrics": ("resource_explorer.observability.metrics_collector", "CREATE TABLE IF NOT EXISTS query_log"),
    "feedback": ("trellis_feedback.store", 'CREATE SCHEMA IF NOT EXISTS'),
    "vectorstore": ("trellis_vectorstore.pg", "CREATE EXTENSION IF NOT EXISTS vector"),
    "artifact_tree": ("trellis_artifact_tree.schema", "CREATE INDEX IF NOT EXISTS artifact_node_by_artifact"),
}


def _src_edit(module: str, old: str):
    """A source_of that serves the real source except `module`, in which one DDL statement is changed."""
    from resource_explorer.schema_fingerprint import default_source

    def source_of(name: str):
        src = default_source(name)
        if name == module:
            assert old in src, f"{old!r} is not in {module}: update STORE_DDL"
            src = src.replace(old, old + " /* changed */", 1)
        return src
    return source_of


@pytest.mark.parametrize("store", sorted(STORE_DDL))
def test_fingerprint_follows_each_store_and_names_which_one_moved(store):
    """Change one DDL statement in a store's package: the combined hash AND that store's own fingerprint change, and the other stores' do not
    (so require_ready can say which store drifted)."""
    from resource_explorer import schema_stores
    base = schema_stores.fingerprints()
    module, old = STORE_DDL[store]
    changed = schema_stores.fingerprints(source_of=_src_edit(module, old))
    assert changed["combined"] != base["combined"], f"{module} is not in the combined schema hash"
    assert changed[store] != base[store], f"{store} has no fingerprint of its own"
    assert {k: v for k, v in changed.items() if k not in (store, "combined")} == \
           {k: v for k, v in base.items() if k not in (store, "combined")}


def test_fingerprint_ignores_comments_and_docstrings_in_the_store_packages():
    from resource_explorer import schema_stores
    from resource_explorer.schema_fingerprint import default_source
    module = STORE_DDL["feedback"][0]

    def source_of(name):
        src = default_source(name)
        return src + "\n# a comment that changes nothing\n" if name == module else src
    assert schema_stores.fingerprints(source_of=source_of) == schema_stores.fingerprints()


# ── the vectorstore collections are dynamic (RE: one table per resource slug and type) ───────────────────────────────

def test_declared_collections_rows_carry_name_dimension_and_hnsw_settings():
    """In external mode create_collection is refused, so the schema Job creates the tables RE will use and RECORDS them in one declared table
    (schema_stores.COLLECTIONS_TABLE): name, embedding dimension, metric, HNSW m and ef_construction."""
    from resource_explorer import schema_stores
    rows = schema_stores.declared_collection_rows(["acme_corp"])
    assert rows, "a resource has collections"
    for r in rows:
        assert r["name"].startswith("acme_corp_")
        assert isinstance(r["dimension"], int) and r["dimension"] > 0
        assert r["metric"] == "cosine"                                     # RE's decision D2
        assert isinstance(r["hnsw_m"], int) and isinstance(r["hnsw_ef_construction"], int)
    assert schema_stores.COLLECTIONS_TABLE


def test_pgvectorstore_external_insert_into_an_undeclared_collection_fails_loudly_and_sends_no_ddl():
    """Otherwise the first new collection breaks a running pod half-way (or silently skips). The error names the collection and the fix."""
    from trellis_vectorstore import PgVectorStore, PgVectorStoreConfig
    spy = _Spy()
    conn = _fake_pg(spy)
    conn.cursor.return_value.fetchone.return_value = None                  # the declared-collections table has no such row
    cfg = PgVectorStoreConfig(host="h", port=1, dbname="d", user="u", password="p", schema="resource_explorer")
    with mock.patch("trellis_vectorstore.pg.psycopg2.connect", return_value=conn), \
         mock.patch("trellis_vectorstore.pg.ThreadedConnectionPool"):
        store = PgVectorStore(cfg, metric="cosine", embeddings=mock.MagicMock(), ddl="external",
                              declared_collections_table="vector_collections")
        with pytest.raises(Exception, match=r"not_declared.*schema apply|schema apply.*not_declared"):
            store.insert_with_embeddings("not_declared", ["t"], [[0.0]])
    assert spy.ddl == [], spy.ddl


# ── the extension: a managed Postgres often will not let the Job's role create it ───────────────────────────────────

def test_missing_vector_extension_gives_an_instruction_not_a_raw_permission_error():
    from resource_explorer import schema_stores
    import psycopg2.errors
    cur = mock.MagicMock()
    cur.__enter__.return_value = cur
    cur.fetchone.return_value = None                                       # pg_extension has no 'vector'

    def execute(sql, params=None):
        if "CREATE EXTENSION" in str(sql):
            raise psycopg2.errors.InsufficientPrivilege("permission denied to create extension \"vector\"")
    cur.execute.side_effect = execute
    conn = mock.MagicMock()
    conn.cursor.return_value = cur
    with pytest.raises(Exception) as ei:
        schema_stores.ensure_extension(conn)
    msg = str(ei.value)
    assert "ask the database admin to run CREATE EXTENSION vector" in msg
    assert "permission denied" not in msg.lower()


def test_existing_vector_extension_is_not_created_again():
    from resource_explorer import schema_stores
    cur = mock.MagicMock()
    cur.__enter__.return_value = cur
    cur.fetchone.return_value = (1,)                                       # already installed (by the admin)
    conn = mock.MagicMock()
    conn.cursor.return_value = cur
    schema_stores.ensure_extension(conn)
    assert not [c for c in cur.execute.call_args_list if "CREATE EXTENSION" in str(c)]


# ── guards: pass today and must keep passing (compose and RE_SCHEMA_MODE=auto are unchanged) ───────────────────────

def test_guard_feedback_store_auto_still_creates_its_tables(tmp_path):
    from sqlalchemy import create_engine, inspect
    from trellis_feedback import FeedbackStore
    eng = create_engine(f"sqlite:///{tmp_path}/g.db")
    FeedbackStore(f"sqlite:///{tmp_path}/g.db", "re", engine=eng)
    assert {"trellis_feedback_items", "trellis_feedback_events"} <= set(inspect(eng).get_table_names())


def test_guard_metrics_collector_auto_still_creates_its_tables(tmp_path):
    from sqlalchemy import create_engine, inspect
    from resource_explorer.observability.metrics_collector import MetricsCollector
    MetricsCollector(database_url=f"sqlite:///{tmp_path}/gm.db")
    assert "query_log" in set(inspect(create_engine(f"sqlite:///{tmp_path}/gm.db")).get_table_names())


def test_guard_default_schema_mode_is_auto(monkeypatch):
    from resource_explorer.registry import SCHEMA_MODES
    monkeypatch.delenv("RE_SCHEMA_MODE", raising=False)
    assert SCHEMA_MODES[0] == "auto"
