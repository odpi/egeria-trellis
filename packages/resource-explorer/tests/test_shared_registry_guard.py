"""The shared-registry guard fails closed, with a FAKE connect only.

Nothing here opens a real connection: the real psycopg2.connect is replaced by
a recorder, and a refusal is proven by the recorder never being called.
"""
from __future__ import annotations

import pytest
import sqlalchemy

from tests import shared_registry_guard as G

SHARED = (
    "postgresql://u:p@localhost:5442/egeria_advisor"
    "?options=-csearch_path%3Dresource_explorer"
)
TEST_SCHEMA = (
    "postgresql://u:p@localhost:5442/egeria_advisor"
    "?options=-csearch_path%3Dresource_explorer_test_123"
)


class _Reached(Exception):
    """Raised by the fake connect: the guard let the connection through."""


@pytest.fixture
def fake_connect(monkeypatch):
    calls: list = []

    def fake(*args, **kwargs):
        calls.append((args, kwargs))
        raise _Reached()

    monkeypatch.setitem(G._state, "real_psycopg2_connect", fake)
    monkeypatch.delenv(G.ALLOW_ENV, raising=False)
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    return calls


def _open(url: str):
    engine = sqlalchemy.create_engine(url)
    try:
        engine.connect()
    finally:
        engine.dispose()


def test_guard_is_installed_by_conftest():
    assert G._state["installed"]


def test_shared_dsn_fails_closed_before_any_connect(fake_connect):
    with pytest.raises(G.SharedRegistryAccessError) as exc:
        _open(SHARED)
    assert fake_connect == []
    msg = str(exc.value)
    assert "test_shared_dsn_fails_closed_before_any_connect" in msg
    assert G.ALLOW_ENV in msg and "localhost:5442/egeria_advisor" in msg


def test_url_without_search_path_is_refused_on_the_registry_path(fake_connect):
    with pytest.raises(G.SharedRegistryAccessError):
        _open("postgresql://u:p@127.0.0.1:5442/egeria_advisor")
    assert fake_connect == []


def test_raw_psycopg2_connect_to_the_real_schema_is_refused(fake_connect):
    import psycopg2

    with pytest.raises(G.SharedRegistryAccessError):
        psycopg2.connect(SHARED)
    with pytest.raises(G.SharedRegistryAccessError):
        psycopg2.connect(host="localhost", port=5442, dbname="egeria_advisor",
                         options="-csearch_path=resource_explorer")
    assert fake_connect == []


def test_temp_sqlite_registry_passes(fake_connect, tmp_path):
    from resource_explorer.registry import ProjectRegistry

    reg = ProjectRegistry(database_url=f"sqlite:///{tmp_path}/r.db")
    assert reg.database_url.startswith("sqlite:///")
    assert fake_connect == []


def test_per_test_postgres_schema_on_a_test_database_passes(fake_connect):
    with pytest.raises(_Reached):        # the fake was reached, not the guard
        _open(TEST_SCHEMA)
    assert len(fake_connect) == 1


def test_other_host_or_database_passes(fake_connect):
    for url in (
        "postgresql://u:p@db.internal:5442/egeria_advisor",
        "postgresql://u:p@localhost:5999/egeria_advisor",
        "postgresql://u:p@localhost:5442/scratch",
    ):
        with pytest.raises(_Reached):
            _open(url)


def test_opt_in_env_var_lets_it_through(fake_connect, monkeypatch):
    monkeypatch.setenv(G.ALLOW_ENV, "1")
    with pytest.raises(_Reached):
        _open(SHARED)


def test_github_actions_service_container_is_exempt(fake_connect, monkeypatch):
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    with pytest.raises(_Reached):
        _open(SHARED)


def test_cached_config_pointing_at_the_shared_dsn_is_caught(fake_connect, monkeypatch, tmp_path):
    """The incident: config cached with the shared URL, env var ignored."""
    import resource_explorer.config as config
    from resource_explorer.registry import ProjectRegistry

    monkeypatch.delenv("REGISTRY_DATABASE_URL", raising=False)
    cached = config.ExplorerConfig()
    cached.registry.database_url = SHARED
    monkeypatch.setattr(config, "_config", cached)
    # The fixture's env var is ignored because the config is already cached.
    monkeypatch.setenv("REGISTRY_DATABASE_URL", f"sqlite:///{tmp_path}/ignored.db")
    ProjectRegistry._pg_schema_ready.discard(SHARED)
    ProjectRegistry._pg_engine_cache.pop(SHARED, None)
    with pytest.raises(G.SharedRegistryAccessError):
        ProjectRegistry()
    assert fake_connect == []


def test_conftest_resets_the_cached_config_between_tests(monkeypatch):
    import resource_explorer.config as config

    assert config._config is None
    monkeypatch.setenv("REGISTRY_DATABASE_URL", "sqlite:///from_env.db")
    assert config.get_config().registry.database_url == "sqlite:///from_env.db"


def test_shared_instance_is_derived_from_declared_defaults(monkeypatch):
    monkeypatch.setenv("PGVECTOR_PORT", "1")        # ambient env cannot move it
    assert (5442, "egeria_advisor") in G.shared_instances()


def test_default_config_without_env_file_fails_closed(fake_connect, monkeypatch):
    """The 2026-10-05 scenario: a worktree has no .env and no env var, so the
    declared DEFAULT (the shared Postgres) is what ProjectRegistry() resolves.
    The guard must refuse at connect, before the schema migration runs."""
    import resource_explorer.config as config
    from resource_explorer.registry import ProjectRegistry

    monkeypatch.delenv("REGISTRY_DATABASE_URL", raising=False)
    monkeypatch.setitem(config.RegistryConfig.model_config, "env_file", None)
    monkeypatch.setattr(config, "_config", None)
    url = config.get_config().registry.database_url
    assert "localhost:5442/egeria_advisor" in url            # really the default
    ProjectRegistry._pg_schema_ready.discard(url)
    ProjectRegistry._pg_engine_cache.pop(url, None)
    with pytest.raises(G.SharedRegistryAccessError) as exc:
        ProjectRegistry()                                    # no args, as a command does
    assert fake_connect == []
    assert url not in ProjectRegistry._pg_schema_ready       # never reached migration


# --- vector store: the PGVECTOR_SCHEMA gap (closed 2026-10-05) ---------------


def test_default_pgvector_store_is_refused_with_the_setting_named(fake_connect, monkeypatch):
    """True declared default: no PGVECTOR_SCHEMA env, .env disabled."""
    import resource_explorer.config as config
    from resource_explorer.vector_store_pg import PgVectorStore

    monkeypatch.delenv("PGVECTOR_SCHEMA", raising=False)
    monkeypatch.setitem(config.PgVectorConfig.model_config, "env_file", None)
    monkeypatch.setattr(config, "_config", None)
    assert config.get_config().pgvector.schema_name == "resource_explorer"   # really the default
    with pytest.raises(G.SharedRegistryAccessError) as exc:
        PgVectorStore()
    msg = str(exc.value)
    assert "PGVECTOR_SCHEMA" in msg and "'resource_explorer'" in msg
    assert "test_default_pgvector_store_is_refused_with_the_setting_named" in msg
    assert fake_connect == []


def test_explicit_shared_schema_argument_is_refused(fake_connect):
    from resource_explorer.vector_store_pg import PgVectorStore

    with pytest.raises(G.SharedRegistryAccessError):
        PgVectorStore(schema="resource_explorer")
    assert fake_connect == []


def test_autouse_fixture_gives_a_per_test_scratch_schema(isolate_pgvector_schema, fake_connect):
    """A default store under the suite resolves to the scratch schema, no connect."""
    from resource_explorer.vector_store_pg import PgVectorStore

    scratch = isolate_pgvector_schema
    assert scratch.startswith(G.TEST_SCHEMA_PREFIX + "_") and scratch != "resource_explorer"
    store = PgVectorStore()
    assert store._config.schema == scratch
    assert scratch in G._state["vector_schemas_built"]
    assert fake_connect == []
    G._state["vector_schemas_built"].discard(scratch)   # nothing real to drop


def test_scratch_schema_is_distinct_per_test_and_not_built_unless_used(isolate_pgvector_schema):
    assert isolate_pgvector_schema not in G._state["vector_schemas_built"]


def test_a_tests_own_schema_override_is_left_alone(fake_connect, monkeypatch):
    from resource_explorer.vector_store_pg import PgVectorStore

    monkeypatch.setenv("PGVECTOR_SCHEMA", "my_own_schema")
    assert PgVectorStore()._config.schema == "my_own_schema"
    assert PgVectorStore(schema="resource_explorer_test_7")._config.schema == "resource_explorer_test_7"
    G._state["vector_schemas_built"].discard("my_own_schema")
    G._state["vector_schemas_built"].discard("resource_explorer_test_7")


def test_vector_schema_opt_in_and_ci_exemptions(fake_connect, monkeypatch):
    from resource_explorer.vector_store_pg import PgVectorStore

    monkeypatch.setenv("PGVECTOR_SCHEMA", "resource_explorer")
    monkeypatch.setenv(G.ALLOW_ENV, "1")
    assert PgVectorStore()._config.schema == "resource_explorer"
    monkeypatch.delenv(G.ALLOW_ENV)
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    assert PgVectorStore()._config.schema == "resource_explorer"
    G._state["vector_schemas_built"].discard("resource_explorer")

SHARED_URL = SHARED


def test_isolation_replaces_unset_and_shared_urls_outside_ci(tmp_path):
    """Outside CI every unset or shared URL becomes temp SQLite. Fails if the
    isolation stops replacing them. Driven by a fake environment, so it does
    not depend on the ambient GITHUB_ACTIONS."""
    env = {"METRICS_DATABASE_URL": SHARED_URL}          # one shared, two unset
    set_ = {}
    replaced = G.isolate_url_settings(env, set_.__setitem__, tmp_path)
    assert sorted(replaced) == sorted(G.SHARED_DB_URL_SETTINGS)
    assert all(v.startswith("sqlite:///") for v in set_.values())
    assert len(set_) == 3


def test_isolation_keeps_a_deliberate_override_outside_ci(tmp_path):
    env = {"REGISTRY_DATABASE_URL": "sqlite:///mine.db",
           "FEEDBACK_DATABASE_URL": TEST_SCHEMA}
    set_ = {}
    replaced = G.isolate_url_settings(env, set_.__setitem__, tmp_path)
    assert replaced == ["METRICS_DATABASE_URL"] and list(set_) == replaced


def test_isolation_leaves_the_environment_untouched_under_ci(tmp_path):
    """CI's Postgres service container IS the registry: nothing is replaced."""
    env = {"GITHUB_ACTIONS": "true", "REGISTRY_DATABASE_URL": SHARED_URL}
    set_ = {}
    assert G.isolate_url_settings(env, set_.__setitem__, tmp_path) == []
    assert set_ == {}


def test_autouse_fixture_applies_the_isolation_outside_ci():
    """What the real fixture did to this process. It leaves two cases alone on purpose:
    GitHub Actions (the service container IS the registry) and a setting the run itself
    pointed at a deliberate non-shared target (the local CI-mode gate sets all three to a
    resource_explorer_test_* schema). In every other case the setting must be SQLite; in
    the deliberate-override case it must at least not address the shared registry
    (the fixture's own decision table is driven directly by the isolate_url_settings tests)."""
    import os
    from resource_explorer.config import get_config
    from resource_explorer.registry_label import is_shared_registry

    if os.environ.get("GITHUB_ACTIONS", "").lower() == "true":
        pytest.skip("CI leaves the URL settings to the service container")
    cfg = get_config()
    for url in (cfg.registry.database_url, cfg.observability.metrics_database_url,
                cfg.feedback.database_url):
        assert url.startswith("sqlite:///") or not is_shared_registry(url), url[:12]


def test_refusal_names_the_metrics_setting(fake_connect, monkeypatch):
    """The 2026-10-05 leak: MetricsCollector on its default URL."""
    import resource_explorer.config as config
    from resource_explorer.observability.metrics_collector import MetricsCollector

    for name in ("REGISTRY_DATABASE_URL", "METRICS_DATABASE_URL", "FEEDBACK_DATABASE_URL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setitem(config.ObservabilityConfig.model_config, "env_file", None)
    monkeypatch.setattr(config, "_config", None)
    with pytest.raises(G.SharedRegistryAccessError) as exc:
        MetricsCollector()
    assert "METRICS_DATABASE_URL" in str(exc.value)
    assert fake_connect == []


def test_feedback_store_default_is_refused_and_named(fake_connect, monkeypatch):
    import resource_explorer.config as config
    from resource_explorer.feedback_store import FeedbackStore

    for name in ("REGISTRY_DATABASE_URL", "METRICS_DATABASE_URL", "FEEDBACK_DATABASE_URL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setitem(config.FeedbackConfig.model_config, "env_file", None)
    monkeypatch.setattr(config, "_config", None)
    with pytest.raises(G.SharedRegistryAccessError) as exc:
        FeedbackStore()
    assert "FEEDBACK_DATABASE_URL" in str(exc.value)


def test_setting_hint_reads_the_calling_module():
    base = "/x/resource_explorer/"
    assert "METRICS" in G.setting_hint([base + "metrics_collector.py"])
    assert "FEEDBACK" in G.setting_hint([base + "feedback_store.py"])
    assert "leader" in G.setting_hint([base + "registry.py", base + "leader_election.py"])
    assert "unknown" in G.setting_hint(["/x/other.py"])


def test_both_isolation_fixtures_coexist_in_one_test(isolate_pgvector_schema, tmp_path):
    """DB URL settings AND the vector schema are isolated together.

    Deliberately NOT using `fake_connect`: it clears GITHUB_ACTIONS, so a CI
    check inside the body would disagree with the autouse URL fixture, which
    ran under the real flag (and is a deliberate no-op in CI, leaving the URL
    variables unset). Constructing a PgVectorStore never connects, and
    isolate_url_settings is driven with a fake environment, so nothing here
    reads the ambient URL variables except under non-CI, where they are set.
    """
    import os
    from resource_explorer.config import get_config
    from resource_explorer.registry_label import is_shared_registry
    from resource_explorer.vector_store_pg import PgVectorStore

    # URL half, independent of the ambient mode: a fake environment.
    set_ = {}
    G.isolate_url_settings({}, set_.__setitem__, tmp_path)
    assert sorted(set_) == sorted(G.SHARED_DB_URL_SETTINGS)
    assert not any(is_shared_registry(v) for v in set_.values())
    # What the real fixture did to this process: only outside CI.
    if os.environ.get("GITHUB_ACTIONS", "").lower() != "true":
        for name in G.SHARED_DB_URL_SETTINGS:
            assert not is_shared_registry(os.environ[name]), name
    # Vector half, unconditional (this fixture runs in CI too).
    scratch = isolate_pgvector_schema
    assert get_config().pgvector.schema_name == scratch
    assert PgVectorStore()._config.schema == scratch
    G._state["vector_schemas_built"].discard(scratch)
