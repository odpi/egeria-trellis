"""reproject-secrets: the `.omsecrets` file is re-derived from the registry.

Fake credentials, temp paths and a temp SQLite registry only. Run with
PGVECTOR_PORT=1 and REGISTRY_DATABASE_URL pointing at a temp SQLite file.
"""
from __future__ import annotations

import os
import stat

import pytest
import yaml
from typer.testing import CliRunner

from resource_explorer import credential_crypto, omsecrets_reproject, omsecrets_store
from resource_explorer.registry import DatabaseEntity, ProjectRegistry

PW_A = "fake-pw-alpha-0001"
PW_B = "fake-pw-bravo-0002"


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("REGISTRY_DATABASE_URL", f"sqlite:///{tmp_path / 'reg.db'}")
    monkeypatch.setenv("RE_DB_CREDENTIAL_KEY", "fake-test-key")
    secrets = tmp_path / "secrets" / "resource-explorer.omsecrets"
    monkeypatch.setenv("EGERIA_SECRETS_STORE_LOCAL_PATH", str(secrets))
    credential_crypto.reset_credential_key_cache()
    from resource_explorer import config

    # get_config() is cached process-wide; without this reset the env vars above
    # are ignored and ProjectRegistry() would open the SHARED registry. Fail
    # closed BEFORE anything can connect.
    monkeypatch.setattr(config, "_config", None)
    assert config.get_config().registry.database_url == f"sqlite:///{tmp_path / 'reg.db'}"
    assert config.get_config().egeria.secrets_store_local_path == str(secrets)
    omsecrets_reproject._reported_errors.clear()
    omsecrets_reproject._NOT_CONFIGURED_SAID = False
    reg = ProjectRegistry()
    assert reg.database_url.startswith("sqlite:///")
    yield reg, secrets
    credential_crypto.reset_credential_key_cache()


def _add(reg, slug, user="fake_user", pw=PW_A):
    reg.register_database(DatabaseEntity(
        slug=slug, display_name=slug, db_type="postgresql", host="h", port=5432, database_name=slug,
        db_user=user, db_password=pw))


def _collections(path):
    return yaml.safe_load(open(path))["secretsCollections"]


def _rows(reg):
    return [r for r in reg.list_activity(limit=200) if r["operation"] == omsecrets_reproject.OPERATION]


def test_missing_file_is_recreated_with_structure_and_matching_credential(env):
    reg, path = env
    _add(reg, "db_a"); _add(reg, "db_b", user="fake_b", pw=PW_B)
    assert not path.exists()
    omsecrets_reproject.heal_missing(reg)
    cols = _collections(path)
    assert set(cols) == {"db_a::PostgreSQL Secret", "db_b::PostgreSQL Secret"}
    c = cols["db_b::PostgreSQL Secret"]
    assert c["displayName"] == "db_b::PostgreSQL Secret" and c["refreshTimeInterval"] == 60
    # decrypt round-trip: projected value equals the stored credential
    stored = reg.get_database("db_b")
    assert (c["secrets"]["userId"] == stored.db_user) and (c["secrets"]["clearPassword"] == stored.db_password)
    assert c["secrets"]["clearPassword"] == credential_crypto.decrypt_db_password(
        _raw_stored(reg, "db_b"))
    assert omsecrets_store.has_collection("db_a::PostgreSQL Secret", path=str(path))


def _raw_stored(reg, slug):
    with reg._conn() as conn:
        return conn.execute("SELECT db_password FROM databases WHERE slug=?", (slug,)).fetchone()[0]


def test_no_credential_is_skipped_and_said_not_written(env, caplog):
    reg, path = env
    _add(reg, "db_a"); _add(reg, "db_none", user="", pw="")
    with caplog.at_level("INFO"):
        out = omsecrets_reproject.heal_missing(reg)
    assert set(_collections(path)) == {"db_a::PostgreSQL Secret"}
    assert [o.status for o in out if o.slug == "db_none"] == [omsecrets_reproject.SKIPPED]
    assert "skipped db_none" in caplog.text
    assert all(r["entity_slug"] != "db_none" for r in _rows(reg))


def test_other_collections_survive(env):
    reg, path = env
    omsecrets_store.write_credential("other::Thing", "o_user", "o_pw_fake", path=str(path))
    _add(reg, "db_a")
    omsecrets_reproject.heal_missing(reg)
    cols = _collections(path)
    assert cols["other::Thing"]["secrets"]["userId"] == "o_user"
    assert "db_a::PostgreSQL Secret" in cols


def test_idempotent_and_second_startup_writes_nothing(env):
    reg, path = env
    _add(reg, "db_a")
    omsecrets_reproject.heal_missing(reg)
    first, n = path.read_bytes(), len(_rows(reg))
    mtime = path.stat().st_mtime_ns
    omsecrets_reproject.heal_missing(reg)
    omsecrets_reproject.reproject(reg, path=str(path))
    assert path.read_bytes() == first and path.stat().st_mtime_ns == mtime
    assert len(_rows(reg)) == n == 1


def test_one_row_per_collection_written_only(env):
    reg, path = env
    _add(reg, "db_a"); _add(reg, "db_b", pw=PW_B); _add(reg, "db_none", user="", pw="")
    omsecrets_store.write_credential("db_b::PostgreSQL Secret", "fake_user", PW_B, path=str(path))
    omsecrets_reproject.heal_missing(reg)
    rows = _rows(reg)
    assert [r["entity_slug"] for r in rows] == ["db_a"]
    assert rows[0]["status"] == "ok"
    assert PW_A not in repr(rows) and PW_B not in repr(rows)


def test_auto_path_does_not_overwrite_existing_but_cli_reconciles(env):
    reg, path = env
    _add(reg, "db_a")
    omsecrets_store.write_credential("db_a::PostgreSQL Secret", "hand_edit", "hand_pw", path=str(path))
    omsecrets_reproject.heal_missing(reg)
    assert _collections(path)["db_a::PostgreSQL Secret"]["secrets"]["userId"] == "hand_edit"
    omsecrets_reproject.reproject(reg, path=str(path))
    assert _collections(path)["db_a::PostgreSQL Secret"]["secrets"]["userId"] == "fake_user"


def test_unwritable_directory_gives_error_row_and_does_not_raise(env):
    reg, path = env
    _add(reg, "db_a")
    path.parent.mkdir()
    path.parent.chmod(0o500)
    try:
        if os.access(path.parent, os.W_OK):
            pytest.skip("running as a user that ignores directory permissions")
        out = omsecrets_reproject.heal_missing(reg)
        omsecrets_reproject.heal_missing(reg)  # persistent failure: still one row
    finally:
        path.parent.chmod(0o700)
    assert [o.status for o in out] == [omsecrets_reproject.ERROR]
    rows = _rows(reg)
    assert len(rows) == 1 and rows[0]["status"] == "error"
    assert PW_A not in repr(rows)


def test_unconfigured_path_does_nothing(env, monkeypatch, tmp_path, caplog):
    reg, path = env
    _add(reg, "db_a")
    monkeypatch.setenv("EGERIA_SECRETS_STORE_LOCAL_PATH", "")
    from resource_explorer import config
    monkeypatch.setattr(config, "_config", None)
    assert config.get_config().egeria.secrets_store_local_path == ""
    with caplog.at_level("DEBUG"):
        assert omsecrets_reproject.heal_missing(reg) == []
        omsecrets_reproject.heal_missing(reg)
    assert not path.exists() and _rows(reg) == []
    assert caplog.text.count("not configured") == 1
    assert not [r for r in caplog.records if r.levelname in ("ERROR", "WARNING")]


def test_decrypt_failure_for_one_database_does_not_stop_others(env):
    reg, path = env
    _add(reg, "db_a"); _add(reg, "db_bad", pw=PW_B); _add(reg, "db_c")
    with reg._conn() as conn:
        conn.execute("UPDATE databases SET db_password=? WHERE slug='db_bad'", ("enc:v1:not-a-token",))
    out = {o.slug: o.status for o in omsecrets_reproject.heal_missing(reg)}
    assert out == {"db_a": "written", "db_bad": "error", "db_c": "written"}
    assert set(_collections(path)) == {"db_a::PostgreSQL Secret", "db_c::PostgreSQL Secret"}
    assert {r["entity_slug"]: r["status"] for r in _rows(reg)} == {
        "db_a": "ok", "db_bad": "error", "db_c": "ok"}


def test_unreadable_existing_file_is_not_overwritten(env):
    reg, path = env
    _add(reg, "db_a")
    path.parent.mkdir()
    path.write_text("secretsCollections: [unclosed\n  : :")
    before = path.read_bytes()
    out = omsecrets_reproject.heal_missing(reg)
    assert path.read_bytes() == before and out[0].status == omsecrets_reproject.ERROR


def test_write_is_atomic_and_mode_preserved(env):
    reg, path = env
    _add(reg, "db_a")
    path.parent.mkdir()
    omsecrets_store.write_credential("x::y", "u", "p_fake", path=str(path))
    path.chmod(0o640)
    omsecrets_reproject.reproject(reg, path=str(path))
    assert stat.S_IMODE(path.stat().st_mode) == 0o640
    assert [p.name for p in path.parent.iterdir()] == [path.name]  # no temp left


async def test_startup_hook_recreates_deleted_file(env, monkeypatch):
    reg, path = env
    _add(reg, "db_a"); _add(reg, "db_b", pw=PW_B)
    monkeypatch.setenv("EXPLORER_EMBED_WORKER", "0")
    import resource_explorer.config as config
    import resource_explorer.surveyors.prefect_adapter as pa
    from resource_explorer.web import app as webapp
    from contextlib import asynccontextmanager
    lifespan = asynccontextmanager(webapp._lifespan)

    async def _noop():
        return None
    monkeypatch.setattr(pa, "alog_prefect_reachability_at_startup", _noop)
    monkeypatch.setattr(config, "get_llm_tier_config", lambda: None)
    assert not path.exists()
    async with lifespan(webapp.app):
        assert set(_collections(path)) == {"db_a::PostgreSQL Secret", "db_b::PostgreSQL Secret"}
        n = len(_rows(reg))
    assert n == 2
    mt = path.stat().st_mtime_ns
    async with lifespan(webapp.app):
        pass
    assert path.stat().st_mtime_ns == mt and len(_rows(reg)) == 2  # second startup writes nothing


def test_resync_loop_heals(env, monkeypatch):
    reg, path = env
    _add(reg, "db_a")
    import threading
    from resource_explorer import egeria_resync

    stop = threading.Event()
    monkeypatch.setattr(egeria_resync, "scan_and_clear",
                        lambda *a, **k: (stop.set(), {"reachable": True})[1])
    egeria_resync._loop(1, stop)
    assert "db_a::PostgreSQL Secret" in _collections(path)


def test_cli_all_and_slug(env):
    reg, path = env
    from resource_explorer.cli.main import app

    _add(reg, "db_a"); _add(reg, "db_b", pw=PW_B); _add(reg, "db_none", user="", pw="")
    r = CliRunner().invoke(app, ["database", "reproject-secrets", "db_a"])
    assert r.exit_code == 0, r.output
    assert set(_collections(path)) == {"db_a::PostgreSQL Secret"}
    r = CliRunner().invoke(app, ["database", "reproject-secrets", "--all"])
    assert r.exit_code == 0 and "skipped" in r.output and "db_none" in r.output
    assert set(_collections(path)) == {"db_a::PostgreSQL Secret", "db_b::PostgreSQL Secret"}
    assert PW_A not in r.output and PW_B not in r.output
    before = path.read_bytes()
    r = CliRunner().invoke(app, ["database", "reproject-secrets", "--all"])
    assert "unchanged" in r.output and path.read_bytes() == before
    assert CliRunner().invoke(app, ["database", "reproject-secrets"]).exit_code == 2
    assert CliRunner().invoke(app, ["database", "reproject-secrets", "nope"]).exit_code == 1
