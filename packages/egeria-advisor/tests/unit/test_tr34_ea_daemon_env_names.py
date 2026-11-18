"""TR-34 (ruling RULING-RE-SERVICE-ACCOUNT-ENV-NAMES-2026-11-17 §4 + addendum): EA's service account is
EA_DAEMON_USER_ID / EA_DAEMON_USER_PASSWORD.
- The old EGERIA_USER / EGERIA_PASSWORD are read for one release, with a warning, and removed after 6.3; the new
  name wins.
- The built-in "garygeeke"/"secret" default is gone: unset, the service paths refuse in words.
- EA never lets pyegeria fill an identity from its own settings: a client with no token and no credential of its
  own is refused before minting, and the service clients pass the credential explicitly.
- pyegeria's CLI identity (EGERIA_USER / EGERIA_USER_PASSWORD) in a server process is warned about, by name.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from advisor import config as cfg

PKG = Path(__file__).resolve().parents[2]
NAMES = ("EA_DAEMON_USER_ID", "EA_DAEMON_USER_PASSWORD", "EGERIA_USER", "EGERIA_PASSWORD", "EGERIA_USER_PASSWORD")


@pytest.fixture
def clean(monkeypatch):
    for n in NAMES:
        monkeypatch.delenv(n, raising=False)
    monkeypatch.setattr(cfg, "_WARNED_ONCE", set())
    return monkeypatch


@pytest.fixture
def warnings():
    from loguru import logger
    said = []
    sink = logger.add(lambda m: said.append(m.record["message"]), level="WARNING")
    yield said
    logger.remove(sink)


def _settings():
    return cfg.AdvisorSettings(_env_file=None)


def test_the_new_names_alone_are_used_with_no_warning(clean, warnings):
    clean.setenv("EA_DAEMON_USER_ID", "egeriaadvisornpa")
    clean.setenv("EA_DAEMON_USER_PASSWORD", "new-secret")
    s = _settings()
    assert (s.daemon_user_id, s.daemon_user_password) == ("egeriaadvisornpa", "new-secret")
    assert not [m for m in warnings if "EA_DAEMON" in m]


def test_the_old_names_alone_still_work_with_one_warning_naming_both(clean, warnings):
    clean.setenv("EGERIA_USER", "egeriaadvisornpa")
    clean.setenv("EGERIA_PASSWORD", "old-secret")
    s = _settings()
    _settings()                                     # a second read warns no more
    assert (s.daemon_user_id, s.daemon_user_password) == ("egeriaadvisornpa", "old-secret")
    said = [m for m in warnings if "deprecated" in m]
    assert len(said) == 2
    assert any("EGERIA_USER is deprecated" in m and "EA_DAEMON_USER_ID" in m for m in said)
    assert any("EGERIA_PASSWORD is deprecated" in m and "EA_DAEMON_USER_PASSWORD" in m for m in said)
    assert not any("old-secret" in m or "egeriaadvisornpa" in m for m in said)        # names only


def test_both_set_and_different_the_new_one_wins_with_a_warning(clean, warnings):
    clean.setenv("EA_DAEMON_USER_ID", "egeriaadvisornpa")
    clean.setenv("EGERIA_USER", "erinoverview")
    clean.setenv("EA_DAEMON_USER_PASSWORD", "new-secret")
    clean.setenv("EGERIA_PASSWORD", "persona-secret")
    s = _settings()
    assert (s.daemon_user_id, s.daemon_user_password) == ("egeriaadvisornpa", "new-secret")
    assert any("EA_DAEMON_USER_ID and EGERIA_USER are both set and differ: using EA_DAEMON_USER_ID" in m
               for m in warnings)
    assert any("EA_DAEMON_USER_PASSWORD and EGERIA_PASSWORD are both set and differ" in m for m in warnings)
    assert not any("erinoverview" in m or "secret" in m.split(":")[0] for m in warnings)


def test_the_old_name_in_the_env_file_is_seen(clean, tmp_path, monkeypatch):
    (tmp_path / ".env").write_text("EGERIA_USER=egeriaadvisornpa\n")
    monkeypatch.chdir(tmp_path)
    assert cfg.warn_old_daemon_names() == [
        "EGERIA_USER is deprecated for EA's service account and is removed after 6.3: set EA_DAEMON_USER_ID "
        "instead (using EGERIA_USER for now)"]


# ── neither set: no built-in default, fail closed in words ───────────────────

def test_neither_set_is_unset_with_no_built_in_default(clean):
    s = _settings()
    assert (s.daemon_user_id, s.daemon_user_password) == ("", "")
    defaults = {name: f.default for name, f in cfg.AdvisorSettings.model_fields.items()}
    assert defaults["daemon_user_id"] == "" and defaults["daemon_user_password"] == ""
    assert "garygeeke" not in defaults.values() and "secret" not in defaults.values()


def test_the_service_fallback_with_no_account_is_empty_and_says_why(clean, warnings, monkeypatch):
    from advisor import auth
    monkeypatch.setattr(cfg.settings, "daemon_user_id", "")
    monkeypatch.setattr(cfg.settings, "daemon_user_password", "")
    assert auth.resolve_egeria_credentials(None) == {"user_id": "", "password": "", "token": ""}
    assert any(cfg.NO_SERVICE_ACCOUNT in m for m in warnings)


def test_apply_token_refuses_to_let_pyegeria_fill_the_identity(clean):
    """A client with no token and no user and password of its own would mint from pyegeria's settings."""
    from advisor import auth

    class Bare:
        user_id, user_pwd, minted = None, None, False

        def create_egeria_bearer_token(self, *a, **k):
            self.minted = True
    c = Bare()
    with pytest.raises(PermissionError, match="EA_DAEMON_USER_ID and EA_DAEMON_USER_PASSWORD"):
        auth.apply_token(c, None)
    assert not c.minted


# ── pyegeria's CLI identity in a server process ──────────────────────────────

@pytest.mark.parametrize("present", [("EGERIA_USER",), ("EGERIA_USER_PASSWORD",),
                                     ("EGERIA_USER", "EGERIA_USER_PASSWORD")])
def test_the_cli_identity_in_a_server_is_warned_by_name(clean, warnings, present):
    said = cfg.warn_server_identity_env("web", {n: "x-value" for n in present})
    assert len(said) == 1 and all(n in said[0] for n in present) and "x-value" not in said[0]
    assert "EA_DAEMON_USER_ID / EA_DAEMON_USER_PASSWORD" in said[0] and said[0] in warnings


def test_no_cli_identity_no_warning(clean):
    assert cfg.warn_server_identity_env("web", {"EGERIA_VIEW_SERVER": "qs-view-server"}) == []


def test_the_web_app_warns_at_startup():
    src = (PKG / "advisor" / "web" / "app.py").read_text()
    assert 'warn_server_identity_env("web")' in src and "warn_old_daemon_names()" in src


def test_env_example_has_the_daemon_names_and_no_persona():
    src = (PKG / ".env.example").read_text()
    assert "EA_DAEMON_USER_ID=" in src and "EA_DAEMON_USER_PASSWORD=" in src
    assert "the app's own service account, never a person" in src and "CLI only: pyegeria tools run as you" in src
    assert "EGERIA_USER=garygeeke" not in src


# ── the real object: EA's service clients sign in as EA_DAEMON_*, never pyegeria's settings ──

@pytest.fixture
def real_client(clean, monkeypatch):
    """A real EgeriaTech on an unroutable platform, any HTTP request a failure; a persona in pyegeria's CLI
    variables, so a settings fallback would name it. Minting is recorded, not sent."""
    import httpx
    from pyegeria.egeria_tech_client import EgeriaTech

    def no_network(self, request, *a, **k):
        pytest.fail(f"an HTTP request was made: {request.method} {request.url}")
    monkeypatch.setattr(httpx.Client, "send", no_network)
    monkeypatch.setattr(httpx.AsyncClient, "send", no_network)
    monkeypatch.setenv("EGERIA_USER", "erinoverview")
    monkeypatch.setenv("EGERIA_USER_PASSWORD", "persona-secret")
    monkeypatch.setattr("advisor.mcp_config.get_pyegeria_platform_config",
                        lambda *a, **k: {"view_server": "qs-view-server", "platform_url": "https://127.0.0.1:9"})
    monkeypatch.setattr(cfg.settings, "daemon_user_id", "egeriaadvisornpa")
    monkeypatch.setattr(cfg.settings, "daemon_user_password", "daemon-secret")
    minted = []

    def mint(self, user_id=None, password=None, new_password=None):
        minted.append({"client_user": self.user_id, "client_pwd": self.user_pwd, "user_arg": user_id,
                       "password_arg": password})
        return "minted-token"
    monkeypatch.setattr(EgeriaTech, "create_egeria_bearer_token", mint)
    return minted


def test_the_spec_sync_service_client_signs_in_as_ea_daemon(real_client):
    from pyegeria.egeria_tech_client import EgeriaTech
    from advisor import egeria_spec_sync as sync
    client = sync._build_client(sync._service_connection())
    assert isinstance(client, EgeriaTech) and client.user_id == "egeriaadvisornpa"
    assert real_client == [{"client_user": "egeriaadvisornpa", "client_pwd": "daemon-secret",
                            "user_arg": None, "password_arg": None}]       # its own credential, never the persona


def test_the_dr_egeria_service_client_passes_its_credential_explicitly(real_client):
    from advisor.agents import dr_egeria_agent as dra
    conn = {"server_name": "qs-view-server", "url": "https://127.0.0.1:9", "auth": "service",
            "user_id": "egeriaadvisornpa", "user_pass": "daemon-secret"}
    client = dra._build_client(conn)
    assert client.user_id == "egeriaadvisornpa"
    assert real_client[-1]["user_arg"] == "egeriaadvisornpa" and real_client[-1]["password_arg"] == "daemon-secret"


def test_the_dr_egeria_service_path_refuses_without_an_account(clean):
    from advisor.agents import dr_egeria_agent as dra
    with pytest.raises(dra.DrEgeriaAuthError):
        dra._build_client({"server_name": "s", "url": "https://127.0.0.1:9", "auth": "service", "user_id": "", "user_pass": ""})
