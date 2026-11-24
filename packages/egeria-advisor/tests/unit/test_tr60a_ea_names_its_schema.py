"""TR-60a (Architect's TR-60 ruling, point 2): every EA connection names its schema, and EA says so when the
collections it answers from are empty.

TR-60: EA's tables are unqualified. Connecting as the role egeria_advisor (TR-12), whose search_path is
"$user", public, it resolved them to the schema egeria_advisor (the feedback store's, deliberately there), where
startup had created EMPTY copies: 18 answers from 0 rows, nothing flagged. The hot fix was a role setting on laz; the
durable fix is that EA itself sends "-c search_path=public" on every connection (a connection option overrides the
role's default), so it reads and writes public whatever the role is called.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from advisor import config
from advisor import knowledge_base_check as kb


# ── every EA connection carries the option ──────────────────────────────────────────────────────────────────────

def test_the_option_pins_public():
    assert config.EA_PG_OPTIONS == "-c search_path=public"


def test_the_vector_store_sends_it_on_every_connection(monkeypatch):
    from advisor.vector_store_pg import PgVectorStore

    store = PgVectorStore(password="x-not-real")
    assert store._config.options == config.EA_PG_OPTIONS
    assert store._connect_kwargs()["options"] == config.EA_PG_OPTIONS


def test_the_operational_pool_sends_it(monkeypatch):
    from advisor import db_consolidated as dbc

    seen = {}

    def _pool(*a, **k):
        seen.update(k)
        return MagicMock()
    monkeypatch.setattr(dbc, "ThreadedConnectionPool", _pool)
    monkeypatch.setattr(dbc.ConsolidatedDBManager, "_initialize_schemas", lambda self: None)
    mgr = dbc.ConsolidatedDBManager()
    mgr.password = "x-not-real"
    mgr.connect()
    assert seen["options"] == config.EA_PG_OPTIONS


@pytest.mark.requires_pgvector
def test_a_live_connection_with_eas_options_reads_public():
    """The real thing, where a Postgres is supplied (the gated tier; read-only): whatever the role's search_path,
    a connection opened with EA's options resolves unqualified names in public only."""
    import psycopg2

    from advisor.config import settings
    with psycopg2.connect(host=settings.pgvector_host, port=settings.pgvector_port, dbname=settings.pgvector_dbname,
                          user=settings.pgvector_user, password=settings.pgvector_password, connect_timeout=5,
                          options=config.EA_PG_OPTIONS) as conn, conn.cursor() as cur:
        cur.execute("SELECT current_schemas(false)")
        assert cur.fetchone()[0] == ["public"]


# ── empty collections are said, at startup and in /api/status ─────────────────────────────────────────────────────

class _Store:
    def __init__(self, counts, broken=()):
        self.counts, self.broken = counts, set(broken)

    def get_collection_stats(self, name):
        if name in self.broken:
            raise RuntimeError(f"relation {name} does not exist")
        return {"name": name, "num_entities": self.counts.get(name, 0)}


@pytest.fixture
def warned():
    from loguru import logger
    lines: list[str] = []
    sink = logger.add(lambda m: lines.append(m.record["message"]), level="WARNING")
    kb._reset_for_tests()
    yield lines
    logger.remove(sink)
    kb._reset_for_tests()


def test_every_collection_empty_is_a_warning_naming_them(warned):
    out = kb.check_enabled_collections(_Store({}), ["pyegeria", "egeria_java"])
    assert out["state"] == "empty" and out["empty"] == ["pyegeria", "egeria_java"]
    assert any("EMPTY" in line and "pyegeria, egeria_java" in line for line in warned)


def test_some_empty_is_partly_empty_and_names_only_those(warned):
    out = kb.check_enabled_collections(_Store({"pyegeria": 6464}), ["pyegeria", "egeria_java"])
    assert out["state"] == "partly_empty" and out["empty"] == ["egeria_java"]
    assert any("egeria_java" in line and "pyegeria," not in line for line in warned)


def test_all_holding_rows_is_ok_and_quiet(warned):
    out = kb.check_enabled_collections(_Store({"pyegeria": 6464, "egeria_java": 61433}), ["pyegeria", "egeria_java"])
    assert out["state"] == "ok" and out["empty"] == [] and warned == []


def test_nothing_countable_is_unread_with_the_reasons(warned):
    out = kb.check_enabled_collections(_Store({}, broken={"pyegeria"}), ["pyegeria"])
    assert out["state"] == "unread" and "does not exist" in out["unread"]["pyegeria"]
    assert warned


def test_status_says_not_checked_until_startup_counts():
    kb._reset_for_tests()
    assert kb.knowledge_base_status()["state"] == "not_checked"


def test_api_status_carries_the_knowledge_base(warned):
    import asyncio

    from advisor.web.app import system_status   # the route's own function: the login middleware is not the point
    kb.check_enabled_collections(_Store({}), ["pyegeria"])
    body = asyncio.run(system_status())
    assert body["knowledge_base"]["state"] == "empty" and body["knowledge_base"]["empty"] == ["pyegeria"]
    assert "mcp_servers" in body
