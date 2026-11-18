"""TR-37 (owner decision 2026-11-17, Architect ruling): RE's service account is RE_DAEMON_USER_ID /
RE_DAEMON_USER_PASSWORD. The old EGERIA_USER_ID / EGERIA_USER_PASSWORD are read for one release (removed after 6.3).

Each case is run both ways a setting can arrive: the process environment and a `.env` file. No test puts a value in
an assertion about a log line: the warnings name variables, never values.
"""
from __future__ import annotations

import logging

import pytest

from resource_explorer import config as cfg_mod
from resource_explorer.config import EgeriaConfig, daemon_env_findings

NAMES = ("RE_DAEMON_USER_ID", "RE_DAEMON_USER_PASSWORD", "EGERIA_USER_ID", "EGERIA_USER_PASSWORD",
         "EGERIA_USER")


@pytest.fixture(autouse=True)
def _clean(monkeypatch, tmp_path):
    for n in NAMES:
        monkeypatch.delenv(n, raising=False)
    monkeypatch.chdir(tmp_path)          # the cwd `.env` is the second env file
    monkeypatch.setattr(cfg_mod, "_ENV_FILES", (tmp_path / "no-package.env", ".env"))
    monkeypatch.setattr(cfg_mod, "_daemon_env_warned", False)
    yield


def _set(way, monkeypatch, tmp_path, values):
    """Deliver `values` through the process environment or a cwd `.env`."""
    if way == "env":
        for k, v in values.items():
            monkeypatch.setenv(k, v)
    else:
        (tmp_path / ".env").write_text("".join(f"{k}={v}\n" for k, v in values.items()))


def _cfg():
    return EgeriaConfig(_env_file=(cfg_mod._ENV_FILES))


WAYS = ["env", "dotenv"]


@pytest.mark.parametrize("way", WAYS)
def test_new_names_are_read(way, monkeypatch, tmp_path):
    _set(way, monkeypatch, tmp_path, {"RE_DAEMON_USER_ID": "re-svc", "RE_DAEMON_USER_PASSWORD": "pw-new"})
    c = _cfg()
    assert (c.user_id, c.user_password) == ("re-svc", "pw-new")
    assert daemon_env_findings() == [], "the new names alone say nothing"


@pytest.mark.parametrize("way", WAYS)
def test_old_names_alone_still_work_and_say_so_once_by_name(way, monkeypatch, tmp_path, caplog):
    _set(way, monkeypatch, tmp_path, {"EGERIA_USER_ID": "old-svc", "EGERIA_USER_PASSWORD": "old-pw-VALUE"})
    c = _cfg()
    assert (c.user_id, c.user_password) == ("old-svc", "old-pw-VALUE")
    with caplog.at_level(logging.WARNING, logger=cfg_mod.__name__):
        first = cfg_mod.warn_daemon_env_once()
        second = cfg_mod.warn_daemon_env_once()
    assert sum("deprecated names" in f for f in first) == 1 and second == [], "ONE deprecation warning, once"
    text = " ".join(r.getMessage() for r in caplog.records)
    for name in ("EGERIA_USER_ID", "RE_DAEMON_USER_ID", "EGERIA_USER_PASSWORD", "RE_DAEMON_USER_PASSWORD"):
        assert name in text
    assert "old-svc" not in text and "old-pw-VALUE" not in text, "names only, never a value"


@pytest.mark.parametrize("way", WAYS)
def test_both_set_and_different_the_new_name_wins_with_a_warning(way, monkeypatch, tmp_path):
    _set(way, monkeypatch, tmp_path, {"RE_DAEMON_USER_ID": "new-svc", "EGERIA_USER_ID": "old-svc",
                                      "RE_DAEMON_USER_PASSWORD": "new-pw", "EGERIA_USER_PASSWORD": "old-pw"})
    c = _cfg()
    assert (c.user_id, c.user_password) == ("new-svc", "new-pw")
    findings = daemon_env_findings()
    assert any("RE_DAEMON_USER_ID and EGERIA_USER_ID are both set and differ" in f for f in findings)
    assert any("RE_DAEMON_USER_PASSWORD and EGERIA_USER_PASSWORD are both set and differ" in f for f in findings)
    assert not any("new-svc" in f or "old-svc" in f or "new-pw" in f or "old-pw" in f for f in findings)


@pytest.mark.parametrize("way", WAYS)
def test_both_set_and_equal_is_not_a_conflict(way, monkeypatch, tmp_path):
    _set(way, monkeypatch, tmp_path, {"RE_DAEMON_USER_ID": "svc", "EGERIA_USER_ID": "svc"})
    assert _cfg().user_id == "svc"
    assert daemon_env_findings() == []


def test_new_name_wins_across_sources(monkeypatch, tmp_path):
    """The new name in `.env` beats the old name in the process environment, which pydantic-settings alone would
    rank the other way round (the environment outranks the file)."""
    monkeypatch.setenv("EGERIA_USER_ID", "old-in-env")
    (tmp_path / ".env").write_text("RE_DAEMON_USER_ID=new-in-file\n")
    assert _cfg().user_id == "new-in-file"
    assert any("both set and differ" in f for f in daemon_env_findings())


@pytest.mark.parametrize("way", WAYS)
def test_neither_set_fails_closed(way, monkeypatch, tmp_path):
    c = _cfg()
    assert (c.user_id, c.user_password) == ("", "")
    from resource_explorer import egeria_clients
    monkeypatch.setattr(cfg_mod, "_config", None)
    with pytest.raises(egeria_clients.NoServiceAccount, match="RE_DAEMON_USER_ID not set"):
        egeria_clients._daemon_credential()


def test_pyegeria_cli_names_in_a_server_environment_are_warned_about_by_name(monkeypatch):
    monkeypatch.setenv("EGERIA_USER", "someoneelse")
    monkeypatch.setenv("EGERIA_USER_PASSWORD", "hunter2")
    findings = daemon_env_findings()
    joined = " ".join(findings)
    assert "EGERIA_USER and EGERIA_USER_PASSWORD are set" in joined
    assert "someoneelse" not in joined and "hunter2" not in joined


def test_egeria_user_alone_is_warned_about_but_never_read_as_the_service_account(monkeypatch):
    monkeypatch.setenv("EGERIA_USER", "someoneelse")
    assert _cfg().user_id == "", "a persona name is never the service account"
    assert any("EGERIA_USER is set" in f for f in daemon_env_findings())


def test_nothing_set_nothing_said():
    assert daemon_env_findings() == []


# ── Architect addendum 2 (TR-37): order in the files, across files, and the two surveyors ──────────────────────

@pytest.mark.parametrize("order", ["old-first", "new-first"])
def test_new_name_wins_whatever_the_order_of_lines_in_one_file(order, monkeypatch, tmp_path):
    lines = ["EGERIA_USER_ID=old-svc\n", "RE_DAEMON_USER_ID=new-svc\n"]
    (tmp_path / ".env").write_text("".join(lines if order == "old-first" else reversed(lines)))
    assert _cfg().user_id == "new-svc"
    findings = daemon_env_findings()
    assert any("both set and differ" in f for f in findings)
    assert not any("old-svc" in f or "new-svc" in f for f in findings), "names only, never a value"


@pytest.mark.parametrize("new_in", ["package-file", "cwd-file"])
def test_new_name_wins_across_the_two_env_files(new_in, monkeypatch, tmp_path):
    """The package .env and the cwd .env are two sources; later wins within a name, but the NEW NAME wins over the
    old one whichever file holds which."""
    pkg = tmp_path / "pkg.env"
    monkeypatch.setattr(cfg_mod, "_ENV_FILES", (pkg, ".env"))
    old_file, new_file = (tmp_path / ".env", pkg) if new_in == "package-file" else (pkg, tmp_path / ".env")
    old_file.write_text("EGERIA_USER_ID=old-svc\n")
    new_file.write_text("RE_DAEMON_USER_ID=new-svc\n")
    assert _cfg().user_id == "new-svc"
    assert any("both set and differ" in f for f in daemon_env_findings())


def test_old_in_the_new_names_blind_spot_still_resolves_when_the_old_name_is_in_the_environment(monkeypatch, tmp_path):
    monkeypatch.setenv("RE_DAEMON_USER_ID", "new-in-env")
    (tmp_path / ".env").write_text("EGERIA_USER_ID=old-in-file\n")
    assert _cfg().user_id == "new-in-env"


@pytest.mark.parametrize("module,cls", [
    ("resource_explorer.surveyors.egeria_tech_type_catalog", "EgeriaTechTypeCatalog"),
    ("resource_explorer.surveyors.egeria_project_finder", "EgeriaProjectFinder"),
])
def test_the_two_surveyors_take_the_service_account_from_config_and_fail_closed(module, cls, monkeypatch):
    import importlib
    from resource_explorer import egeria_clients
    klass = getattr(importlib.import_module(module), cls)
    monkeypatch.setattr(cfg_mod, "_config", None)
    monkeypatch.setenv("RE_DAEMON_USER_ID", "svc-from-config")
    assert klass(platform_url="https://x", view_server="v").user_id == "svc-from-config"
    monkeypatch.setattr(cfg_mod, "_config", None)
    monkeypatch.delenv("RE_DAEMON_USER_ID")
    with pytest.raises(egeria_clients.NoServiceAccount, match="RE_DAEMON_USER_ID not set"):
        klass(platform_url="https://x", view_server="v")
    assert klass(platform_url="https://x", view_server="v", user_id="explicit").user_id == "explicit", \
        "an explicit id is still honoured"
    monkeypatch.setattr(cfg_mod, "_config", None)
    monkeypatch.setenv("EGERIA_USER", "someoneelse")
    with pytest.raises(egeria_clients.NoServiceAccount):
        klass(platform_url="https://x", view_server="v")   # a persona in EGERIA_USER is never the service account
