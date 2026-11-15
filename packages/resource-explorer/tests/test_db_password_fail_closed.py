"""TR-12: no built-in Postgres password. RE fails closed, naming the variable, never its value."""
from __future__ import annotations

import pytest

import resource_explorer.config as config_module
from resource_explorer.config import (
    MissingDatabaseSetting,
    check_database_settings_at_startup,
    get_config,
    require_database_url,
)

SECRET = "s3cr3t-not-logged"
URL_VARS = ("REGISTRY_DATABASE_URL", "FEEDBACK_DATABASE_URL", "METRICS_DATABASE_URL")


@pytest.fixture
def bare_env(monkeypatch, tmp_path):
    """No password and no URL: what a deployment that sets nothing now looks like. .env files are not read."""
    monkeypatch.chdir(tmp_path)
    for name in ("PGVECTOR_PASSWORD", *URL_VARS):
        monkeypatch.delenv(name, raising=False)
    config_module._config = None
    yield monkeypatch
    config_module._config = None


def test_there_is_no_built_in_password(bare_env):
    cfg = get_config()
    assert cfg.pgvector.password == ""
    assert cfg.registry.database_url == cfg.feedback.database_url == cfg.observability.metrics_database_url == ""


def test_startup_refuses_naming_every_missing_setting(bare_env):
    with pytest.raises(MissingDatabaseSetting) as info:
        check_database_settings_at_startup()
    words = str(info.value)
    for name in (*URL_VARS, "PGVECTOR_PASSWORD"):
        assert name in words
    assert "advisor" not in words.replace("egeria_advisor", "")


def test_the_password_fills_all_three_urls_and_is_never_in_a_message(bare_env):
    bare_env.setenv("PGVECTOR_PASSWORD", SECRET)
    cfg = get_config()
    for url in (cfg.registry.database_url, cfg.feedback.database_url, cfg.observability.metrics_database_url):
        assert url.startswith("postgresql://egeria_advisor:") and url.endswith("?options=-csearch_path%3Dresource_explorer")
    check_database_settings_at_startup()              # nothing missing: no raise


def test_an_explicit_url_wins_and_a_missing_password_only_warns(bare_env, caplog):
    for name in URL_VARS:
        bare_env.setenv(name, "sqlite:///x.db")
    cfg = get_config()
    assert cfg.registry.database_url == "sqlite:///x.db"
    with caplog.at_level("WARNING"):
        check_database_settings_at_startup()
    assert "PGVECTOR_PASSWORD is not set" in caplog.text


def test_each_user_refuses_by_name(bare_env):
    from resource_explorer.registry import ProjectRegistry

    with pytest.raises(MissingDatabaseSetting, match="REGISTRY_DATABASE_URL"):
        ProjectRegistry()
    from resource_explorer.observability.metrics_collector import resolve_metrics_url

    with pytest.raises(MissingDatabaseSetting, match="METRICS_DATABASE_URL"):
        resolve_metrics_url()
    with pytest.raises(MissingDatabaseSetting, match="FEEDBACK_DATABASE_URL"):
        from resource_explorer.feedback_store import FeedbackStore

        FeedbackStore()


def test_the_vector_store_refuses_to_connect_without_the_password(bare_env):
    from resource_explorer.vector_store_pg import PgVectorStore

    store = PgVectorStore()                            # construction is still side-effect free
    with pytest.raises(MissingDatabaseSetting, match="PGVECTOR_PASSWORD"):
        store.connect()


def test_require_database_url_passes_a_value_through():
    assert require_database_url("sqlite:///a.db", "REGISTRY_DATABASE_URL", "registry") == "sqlite:///a.db"
