"""
Pytest configuration and shared fixtures for Egeria Advisor tests.

Also home to the real (non-mocked) pgvector integration tier for EA's
vector store — mirrors Resource Explorer's tests/conftest.py pattern
(requires_pgvector marker + auto-skip when Postgres isn't reachable), added
as part of the trellis-vectorstore extraction to close a real, confirmed
gap: EA previously had zero pytest coverage of PgVectorStore/BaseVectorStore
at all, only non-pytest standalone scripts.

Isolation note: RE's equivalent fixture uses a throwaway Postgres *schema*
(RE's tables are always schema-qualified). EA's tables are always
unqualified/`public` — testing in a separate schema would test a
configuration EA never actually runs in. The originally-preferred isolation
(a throwaway *database*, `egeria_advisor_vs_test`) needs CREATEDB, which
the `egeria_advisor` role does not have (confirmed live) — falls back to
the documented contingency: prefixed throwaway tables (`zzz_vstest_*`) in
the real `public` schema, dropped in teardown. This means list_collections()
assertions in these tests must be superset-style ("in", not "=="), since
real EA production tables are also present in the same schema.
"""

import os
import sys
from pathlib import Path

import pytest

# TR-12: there is no built-in Postgres password any more, and EA refuses to connect without one, by name. Unit tests
# mock the connection, so they get a placeholder that opens nothing. Set before advisor.config is imported (its
# settings are read once). A real PGVECTOR_PASSWORD exported in the shell (e.g. `source
# ~/.re-gate/secrets/pgvector.env`) wins, and only then does the requires_pgvector tier reach Postgres.
#: Whether a REAL password was supplied (shell or `source ~/.re-gate/secrets/pgvector.env`), decided before the
#: placeholder below is set. Only then may a requires_pgvector test reach Postgres.
#: (The placeholder itself is not a supplied password: xdist workers inherit it from the controller's environment.)
_PG_PLACEHOLDER = "test-only-not-a-password"
_PG_PASSWORD_SUPPLIED = os.environ.get("PGVECTOR_PASSWORD", "") not in ("", _PG_PLACEHOLDER)
os.environ.setdefault("PGVECTOR_PASSWORD", _PG_PLACEHOLDER)

#: TR-12 R1: a requires_pgvector test SKIPS, never errors, when no password is supplied, with this reason.
PG_PASSWORD_SKIP_REASON = "PGVECTOR_PASSWORD not set (TR-12)"
#: TR-12 R1: the gate sets this (with the secret sourced). Then a missing password or an unreachable Postgres FAILS
#: the run instead of skipping the requires_pgvector tier, so that tier can never be lost silently.
_REQUIRE_PG = os.environ.get("TRELLIS_REQUIRE_PG") == "1"

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))


@pytest.fixture
def sample_query():
    """Sample user query for testing."""
    return "How do I create a glossary?"


@pytest.fixture
def sample_code_query():
    """Sample code search query."""
    return "Show me examples of using the collection manager"


@pytest.fixture
def sample_quantitative_query():
    """Sample quantitative query."""
    return "How many lines of code are in the project?"


# ---------------------------------------------------------------------------
# pgvector integration tier (trellis-vectorstore extraction, Phase 4)
# ---------------------------------------------------------------------------

_VS_TEST_PREFIX = "zzz_vstest_"


def _pgvector_reachable() -> bool:
    try:
        import psycopg2
        from advisor.config import settings

        conn = psycopg2.connect(
            host=settings.pgvector_host, port=settings.pgvector_port,
            dbname=settings.pgvector_dbname, user=settings.pgvector_user,
            password=settings.pgvector_password, connect_timeout=2,
        )
        conn.close()
        return True
    except Exception:
        return False


_PGVECTOR_AVAILABLE = _PG_PASSWORD_SUPPLIED and _pgvector_reachable()


def pg_unavailable_reason() -> str:
    """Why the Postgres tier cannot run: the missing password first (TR-12 R1), else reachability."""
    return (PG_PASSWORD_SKIP_REASON if not _PG_PASSWORD_SUPPLIED
            else "pgvector/Postgres not reachable at the configured host:port")


def skip_or_fail_pg() -> None:
    """For a fixture that needs Postgres: skip with the reason, or FAIL under TRELLIS_REQUIRE_PG=1 (the gate), so a
    fixture-level skip cannot lose the tier silently either (TR-12 R1)."""
    if _REQUIRE_PG:
        pytest.fail(f"TRELLIS_REQUIRE_PG=1 but {pg_unavailable_reason()}")
    pytest.skip(pg_unavailable_reason())


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "requires_pgvector: needs a live reachable Postgres/pgvector instance "
        "(auto-skipped when one isn't available)",
    )
    config.addinivalue_line(
        "markers",
        'live(reason="..."): needs a running service (Postgres, Egeria, MLflow or Phoenix). The reason '
        "is required and names the service. With EA_SKIP_LIVE=1 (CI, the gate) every live test is "
        "SKIPPED with that reason, so -rs lists it; it is never deselected silently.",
    )


# Phase 1 Q1 (2026-11-10). A test outside tests/unit that needs a service is marked live with its
# reason. CI and the gate set EA_SKIP_LIVE=1 and run with -rs, so each such test shows up as
# "SKIPPED live: <reason>" -- skipped with its reason rather than `-m "not live"`, which would
# deselect it and print nothing. Locally, without EA_SKIP_LIVE, live tests run (requires_pgvector
# still skips them when Postgres is unreachable).
_SKIP_LIVE = os.environ.get("EA_SKIP_LIVE") == "1"

def pytest_collection_modifyitems(config, items):
    for item in items:
        marker = item.get_closest_marker("live")
        if marker is None:
            continue
        reason = marker.kwargs.get("reason") or (marker.args[0] if marker.args else "")
        if not str(reason).strip():
            raise pytest.UsageError(f"{item.nodeid}: @pytest.mark.live needs a reason naming the service")
        if _SKIP_LIVE:
            item.add_marker(pytest.mark.skip(reason=f"live: {reason}"))

    if _PGVECTOR_AVAILABLE:
        return
    needing = [item for item in items if "requires_pgvector" in item.keywords]
    if not needing:
        return
    why = pg_unavailable_reason()
    if _REQUIRE_PG:
        raise pytest.UsageError(
            f"TRELLIS_REQUIRE_PG=1 but {len(needing)} requires_pgvector test(s) cannot run: {why}. "
            "Source ~/.re-gate/secrets/pgvector.env (or export PGVECTOR_PASSWORD) and check Postgres is up.")
    skip = pytest.mark.skip(reason=why)
    for item in needing:
        item.add_marker(skip)


@pytest.fixture
def vs_test_prefix():
    """The throwaway-table prefix these tests write under — never a real
    EA collection name. Drops any leftover zzz_vstest_* tables before and
    after, so a previous crashed run can't leave stale state behind."""
    if not _PGVECTOR_AVAILABLE:
        skip_or_fail_pg()

    import psycopg2
    from advisor.config import settings

    def _drop_all():
        conn = psycopg2.connect(
            host=settings.pgvector_host, port=settings.pgvector_port,
            dbname=settings.pgvector_dbname, user=settings.pgvector_user,
            password=settings.pgvector_password,
        )
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'public' AND table_name LIKE %s",
                (f"{_VS_TEST_PREFIX}%",),
            )
            for (name,) in cur.fetchall():
                cur.execute(f'DROP TABLE IF EXISTS "{name}" CASCADE')
        conn.close()

    _drop_all()
    yield _VS_TEST_PREFIX
    _drop_all()


class _FakeEmbeddingProvider:
    """A tiny deterministic EmbeddingProvider — avoids loading EA's real
    sentence-transformers model (slow, and irrelevant to what these tests
    verify: DDL/SQL shape, extra-column round-tripping, filter DSL
    behavior — none of which depend on real embedding quality)."""

    def embed_texts(self, texts):
        import numpy as np
        return np.array([self._vec(t) for t in texts], dtype="float32")

    def embed_query(self, text):
        import numpy as np
        return np.array(self._vec(text), dtype="float32")

    @staticmethod
    def _vec(text: str):
        # A cheap hash-based pseudo-embedding — distinct texts get distinct
        # (deterministic) vectors, enough to exercise real ORDER BY distance
        # behavior without a real model.
        import hashlib
        h = hashlib.sha256(text.encode()).digest()
        return [b / 255.0 for b in h[:32]] * 12  # 32*12 = 384 dims


@pytest.fixture
def ea_vs_store(vs_test_prefix):
    """A real PgVectorStore, EA-shaped (schema=None/unqualified, metric=l2)
    but scoped to zzz_vstest_-prefixed tables via table_name_map — so it
    exercises EA's exact configuration against the real live database
    without ever touching a real production collection name."""
    from trellis_vectorstore import PgVectorStoreConfig
    from trellis_vectorstore.pg import PgVectorStore
    from advisor.config import settings
    from advisor.vector_store_pg import EA_COLLECTION_SCHEMAS

    config = PgVectorStoreConfig(
        host=settings.pgvector_host, port=settings.pgvector_port,
        dbname=settings.pgvector_dbname, user=settings.pgvector_user,
        password=settings.pgvector_password, schema=None,
        max_connections=5, ef_search=settings.pgvector_ef_search,
    )
    table_name_map = {
        "pyegeria": f"{vs_test_prefix}pyegeria",
        "pyegeria_cli": f"{vs_test_prefix}pyegeria_cli",
        "plain": f"{vs_test_prefix}plain",
    }
    collection_schemas = {
        f"{vs_test_prefix}pyegeria": EA_COLLECTION_SCHEMAS["pyegeria"],
        f"{vs_test_prefix}pyegeria_cli": EA_COLLECTION_SCHEMAS["pyegeria_cli"],
    }
    store = PgVectorStore(
        config, metric="l2", embeddings=_FakeEmbeddingProvider(),
        collection_schemas=collection_schemas, table_name_map=table_name_map,
    )
    store.connect()
    yield store
    store.disconnect()


# ---------------------------------------------------------------------------
# Throwaway Postgres schema for tests that WRITE (Phase 1 Q1-1, 2026-11-10)
# ---------------------------------------------------------------------------
# RE's pattern (packages/resource-explorer/tests/conftest.py): a schema of its own per test,
# created, used, dropped. Until Q1-1, test_code_intel_agent seeded and deleted rows in RE's REAL
# resource_explorer schema and test_code_symbol_store in EA's real code_symbols, so every EA unit run
# on laz wrote the shared Postgres. Now they write only `ea_test_<pid>_<hex>`, dropped afterwards;
# schemas left by a crashed run are swept when their process is gone.

_EA_TEST_SCHEMA_PREFIX = "ea_test_"


def _pg_connect():
    import psycopg2
    from advisor.config import settings
    return psycopg2.connect(
        host=settings.pgvector_host, port=settings.pgvector_port,
        dbname=settings.pgvector_dbname, user=settings.pgvector_user,
        password=settings.pgvector_password, connect_timeout=5,
    )


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _sweep_orphan_ea_test_schemas(cur) -> None:
    cur.execute("SELECT schema_name FROM information_schema.schemata WHERE schema_name LIKE %s",
                (_EA_TEST_SCHEMA_PREFIX.replace("_", "\\_") + "%",))
    for (name,) in cur.fetchall():
        pid = name[len(_EA_TEST_SCHEMA_PREFIX):].split("_", 1)[0]
        if pid.isdigit() and int(pid) != os.getpid() and not _pid_alive(int(pid)):
            cur.execute(f'DROP SCHEMA IF EXISTS "{name}" CASCADE')


@pytest.fixture
def ea_test_schema():
    """A Postgres schema of this test's own, dropped afterwards. Never public, never resource_explorer."""
    if not _PGVECTOR_AVAILABLE:
        skip_or_fail_pg()
    import uuid
    name = f"{_EA_TEST_SCHEMA_PREFIX}{os.getpid()}_{uuid.uuid4().hex[:8]}"
    conn = _pg_connect()
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            _sweep_orphan_ea_test_schemas(cur)
            cur.execute(f'CREATE SCHEMA "{name}"')
        yield name
    finally:
        with conn.cursor() as cur:
            cur.execute(f'DROP SCHEMA IF EXISTS "{name}" CASCADE')
        conn.close()


@pytest.fixture
def ea_test_db(ea_test_schema, monkeypatch):
    """EA's ConsolidatedDBManager, scoped to the throwaway schema, installed as get_db_manager()'s
    singleton for this test. Its connections use search_path=<schema>,public: EA's unqualified tables
    are created and used in the throwaway schema (public stays on the path only for the vector type)."""
    from psycopg2.pool import ThreadedConnectionPool
    from advisor import code_symbol_store, db_consolidated

    schema = ea_test_schema

    class _SchemaScopedDB(db_consolidated.ConsolidatedDBManager):
        def connect(self) -> None:
            if self._pool is not None:
                return
            with self._lock:
                if self._pool is not None:
                    return
                self._pool = ThreadedConnectionPool(
                    minconn=1, maxconn=3, host=self.host, port=self.port, dbname=self.dbname,
                    user=self.user, password=self.password, options=f"-csearch_path={schema},public")
                self._initialize_schemas()

    db = _SchemaScopedDB()
    db.connect()
    current = db.execute_query("SELECT current_schema() AS s")[0]["s"]
    assert current == schema, f"test DB is not scoped to its throwaway schema (current_schema={current})"
    monkeypatch.setattr(db_consolidated, "_db_manager", db)
    monkeypatch.setattr(code_symbol_store, "_store", None)
    yield db
    db.disconnect()


#: EA's own Egeria service account is on the sign-in refusal list from A-I1 (2026-11-11). It is read from
#: settings.daemon_user_id: EA_DAEMON_USER_ID (TR-34; the deprecated EGERIA_USER still read, no default now).
#: A developer's shell may export EGERIA_USER, so every test runs with a fixed service account no test signs in
#: as. A test about the service account sets its own.
TEST_SERVICE_ACCOUNT = "ea-test-service-account"


@pytest.fixture(autouse=True)
def _pinned_ea_service_account(monkeypatch):
    from advisor.config import settings
    monkeypatch.setattr(settings, "daemon_user_id", TEST_SERVICE_ACCOUNT)
