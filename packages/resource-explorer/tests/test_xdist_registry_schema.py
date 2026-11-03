"""Per-worker registry schemas under pytest-xdist: naming, rewrite, create-where-used, teardown drop."""
from __future__ import annotations

import pytest

from tests import xdist_registry_schema as X

SHARED = "postgresql://egeria_advisor:advisor@localhost:5442/egeria_advisor?options=-csearch_path%3Dresource_explorer"
PRIVATE = "postgresql://egeria_advisor:advisor@localhost:5499/egeria_advisor?options=-csearch_path%3Dresource_explorer"


def _env(url, **extra):
    env = {name: url for name in X.SETTINGS}
    env.update(extra)
    return env


def test_schema_names_the_run_and_the_worker():
    assert X.worker_schema("gw3", "4242") == "resource_explorer_xdist_4242_gw3"
    assert X.worker_schema("gw3", "4242") != X.worker_schema("gw3", "4243")   # two runs never share
    assert X.worker_schema("gw3", "4242") != X.worker_schema("gw4", "4242")


def test_only_the_resource_explorer_search_path_is_rewritten():
    env = {
        "REGISTRY_DATABASE_URL": SHARED,
        "FEEDBACK_DATABASE_URL": "sqlite:////tmp/f.db",
        "METRICS_DATABASE_URL": SHARED.replace("resource_explorer", "resource_explorer_test_1"),
    }
    assert X.rewritten(env, "S") == {"REGISTRY_DATABASE_URL": SHARED[: -len("resource_explorer")] + "S"}


def test_not_a_worker_changes_nothing():
    env = _env(SHARED, GITHUB_ACTIONS="true")
    assert X.apply(env, "", "1", create_fn=lambda *a: pytest.fail("created")) is None
    assert env == _env(SHARED, GITHUB_ACTIONS="true")


def test_in_ci_every_setting_moves_to_the_worker_schema_and_it_is_created_once():
    env = _env(SHARED, GITHUB_ACTIONS="true")
    made = []
    info = X.apply(env, "gw0", "77", create_fn=lambda dsn, schema: made.append((dsn, schema)))
    assert {env[n] for n in X.SETTINGS} == {SHARED[: -len("resource_explorer")] + "resource_explorer_xdist_77_gw0"}
    assert made == [(SHARED.split("?")[0], "resource_explorer_xdist_77_gw0")]
    assert info["created"] == [SHARED.split("?")[0]]


def test_outside_ci_the_shared_registry_gets_no_ddl():
    # isolate_url_settings replaces these URLs per test with temp SQLite, so the schema would never be used.
    env = _env(SHARED)
    info = X.apply(env, "gw1", "77", create_fn=lambda *a: pytest.fail("DDL on the shared instance"))
    assert env["REGISTRY_DATABASE_URL"].endswith("resource_explorer_xdist_77_gw1")
    assert info["created"] == []
    assert X.teardown(info, drop_fn=lambda *a: pytest.fail("dropped")) == []


def test_outside_ci_a_private_postgres_is_used_so_the_schema_is_created():
    env = _env(PRIVATE)
    made = []
    X.apply(env, "gw2", "77", create_fn=lambda dsn, schema: made.append(schema))
    assert made == ["resource_explorer_xdist_77_gw2"]


def test_teardown_drops_where_created_and_releases_the_cached_engine():
    from resource_explorer.registry import ProjectRegistry

    env = _env(PRIVATE)
    info = X.apply(env, "gw5", "88", create_fn=lambda *a: None)
    url = info["urls"]["REGISTRY_DATABASE_URL"]

    class FakeEngine:
        disposed = False

        def dispose(self):
            FakeEngine.disposed = True

    ProjectRegistry._pg_engine_cache[url] = FakeEngine()
    ProjectRegistry._pg_schema_ready.add(url)
    dropped = []
    try:
        assert X.teardown(info, drop_fn=lambda dsn, schema: dropped.append((dsn, schema))) == [PRIVATE.split("?")[0]]
    finally:
        ProjectRegistry._pg_engine_cache.pop(url, None)
        ProjectRegistry._pg_schema_ready.discard(url)
    assert dropped == [(PRIVATE.split("?")[0], "resource_explorer_xdist_88_gw5")]
    assert FakeEngine.disposed and url not in ProjectRegistry._pg_schema_ready


@pytest.mark.requires_pgvector
def test_create_then_drop_really_creates_and_removes_the_schema(pg_test_schema):
    import psycopg2
    from resource_explorer.config import get_config

    cfg = get_config().pgvector
    dsn = f"postgresql://{cfg.db_user}:{cfg.password}@{cfg.host}:{cfg.port}/{cfg.dbname}"
    schema = f"{pg_test_schema}_xdistprobe"   # the per-process test prefix: never a shared name

    def exists():
        conn = psycopg2.connect(dsn)
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT 1 FROM information_schema.schemata WHERE schema_name = %s", (schema,))
                return cur.fetchone() is not None
        finally:
            conn.close()

    X.create(dsn, schema)
    try:
        assert exists()
    finally:
        X.drop(dsn, schema)
    assert not exists()

