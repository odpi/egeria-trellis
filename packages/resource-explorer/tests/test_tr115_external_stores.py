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
def test_schema_apply_creates_all_four_stores_and_external_mode_then_runs_no_ddl_anywhere():
    """The end-to-end proof, in the shape of the existing TR-112b test: one `schema apply` brings the registry AND the four stores to
    version; a second process in external mode sends no DDL at all (registry, metrics, feedback, vectorstore, artifact tree), and
    a database that was never applied makes every one of them refuse with SchemaNotReady."""
    from resource_explorer import schema_stores
    schema_stores.apply_all(f"{BASE_URL}?options=-csearch_path%3Dresource_explorer_test_{uuid.uuid4().hex[:8]}")


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
