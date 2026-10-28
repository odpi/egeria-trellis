"""Brief L (as scoped by the owner, 2026-10-09): RE's daemon identity is `.env`
(EGERIA_USER_ID / EGERIA_USER_PASSWORD, set to RE's own account `resourceexplorernpa`) behind the
one seam `egeria_clients._daemon_credential()`, a development bootstrap; and because that userId
is now a service account, not a person:

* the startup log names the source in one INFO line, never a value;
* whoami and the popover name the daemon from the seam, not inferred;
* the public login-form defaults never offer the daemon's userId;
* an interactive sign-in AS the daemon (password, Portal token or raw Egeria bearer) is refused
  with "this is Resource Explorer's own service account; sign in as yourself".

Nothing here contacts Egeria: `login_with_password`, the Portal liveness check and Egeria bearer
validation are faked.
"""
from __future__ import annotations

import logging
import time

import jwt
import pytest
from fastapi.testclient import TestClient

from resource_explorer import egeria_clients as ec

NPA, NPA_PW = "resourceexplorernpa", "fake-npa-password-for-tests"
REFUSAL = "this is Resource Explorer's own service account; sign in as yourself"


@pytest.fixture
def cfg(monkeypatch):
    """A COPY of the config with the daemon set to RE's own account (as 8813's .env now is)."""
    from resource_explorer import config

    c = config.get_config().model_copy(deep=True)
    c.egeria.user_id, c.egeria.user_password = NPA, NPA_PW
    if "login_default_user" in type(c.egeria).model_fields:
        c.egeria.login_default_user = ""
    monkeypatch.setattr(config, "get_config", lambda: c)
    monkeypatch.setattr(ec, "_source_logged", False, raising=False)
    return c


@pytest.fixture
def client(cfg, monkeypatch):
    monkeypatch.setenv("RE_PORTAL_SECRET", "test-portal-secret")
    from resource_explorer import auth as re_auth
    from resource_explorer.web.app import app

    re_auth.reset_auth_secrets_cache()
    yield TestClient(app)
    re_auth.reset_auth_secrets_cache()


def _egeria_token(sub: str) -> str:
    return jwt.encode({"iss": "self", "sub": sub, "exp": time.time() + 3600}, "irrelevant", algorithm="HS256")


# ── the seam and its startup line ──────────────────────────────────────────

def test_the_seam_logs_one_info_line_naming_the_source_and_never_a_value(cfg, caplog):
    with caplog.at_level(logging.INFO, logger="resource_explorer.egeria_clients"):
        ec.log_daemon_identity_at_startup()
        first = ec.Daemon(ec.DaemonReason.SCHEDULER)
        ec.Daemon(ec.DaemonReason.OUTBOX)
    lines = [(r.levelno, r.getMessage()) for r in caplog.records if r.name == "resource_explorer.egeria_clients"]
    assert lines == [(logging.INFO, "daemon identity: 'resourceexplorernpa' from .env (EGERIA_USER_ID) — "
                                    "development bootstrap; not for a shipped product")]
    assert not any(NPA_PW in m for _l, m in lines)
    assert (first.client_user, first.password) == (NPA, NPA_PW)


def test_the_web_app_start_logs_the_source_line(cfg, monkeypatch):
    """The web app's start logs the source line, exactly once. Order-independent: the once-per-
    process guard is reset here, and a process-wide `logging.disable(INFO)` left by another test
    module (tests/test_prefect_survey_flow.py does it at import) is lifted for this test only."""
    from resource_explorer.web.app import app

    monkeypatch.setattr(ec, "_source_logged", False)          # explicit, whatever ran before
    # A handler on the module's own logger: the app's start reconfigures logging (Prefect), which
    # takes records away from pytest's root capture.
    seen: list[str] = []
    handler = logging.Handler()
    handler.emit = lambda record: seen.append(record.getMessage())
    logger = logging.getLogger("resource_explorer.egeria_clients")
    saved = (logger.level, logger.disabled, logging.root.manager.disable)
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.disabled = False
    logging.disable(logging.NOTSET)
    try:
        with TestClient(app):
            pass
    finally:
        logger.removeHandler(handler)
        logger.setLevel(saved[0])
        logger.disabled = saved[1]
        logging.disable(saved[2])
    lines = [m for m in seen if m.startswith("daemon identity:")]
    assert lines == ["daemon identity: 'resourceexplorernpa' from .env (EGERIA_USER_ID) — development "
                     "bootstrap; not for a shipped product"], "logged once, at the web app's start"


def test_whoami_names_the_daemon_from_the_seam(cfg):
    from resource_explorer.web.routes.egeria import whoami

    assert whoami()["daemon"] == {"user_id": NPA, "source": "env"}


# ── the public login defaults ──────────────────────────────────────────────

def test_login_defaults_never_offer_the_daemon_user(client, cfg):
    assert client.get("/api/auth/defaults").json() == {"username": ""}
    cfg.egeria.login_default_user = NPA                       # even when configured as the prefill
    assert client.get("/api/auth/defaults").json() == {"username": ""}
    cfg.egeria.login_default_user = "peterprofile"            # RE_LOGIN_DEFAULT_USER, a person
    assert client.get("/api/auth/defaults").json() == {"username": "peterprofile"}


# ── interactive sign-in as the daemon is refused ───────────────────────────

@pytest.mark.parametrize("typed", [NPA, " ResourceExplorerNPA "])
def test_a_password_sign_in_as_the_daemon_is_refused_before_egeria_is_asked(client, monkeypatch, typed):
    import resource_explorer.auth as re_auth

    asked = []
    monkeypatch.setattr(re_auth, "login_with_password", lambda u, p: asked.append(u) or _egeria_token(u))
    r = client.post("/api/auth/login", json={"username": typed, "password": NPA_PW})
    assert r.status_code == 403 and r.json()["detail"] == REFUSAL
    assert asked == [], "the daemon's password is never even tried"


def test_a_person_still_signs_in(client, monkeypatch):
    import resource_explorer.auth as re_auth

    monkeypatch.setattr(re_auth, "login_with_password", lambda u, p: _egeria_token(u))
    r = client.post("/api/auth/login", json={"username": "peterprofile", "password": "x"})
    assert r.status_code == 200 and r.json()["egeria_user"] == "peterprofile"


def test_a_portal_token_for_the_daemon_is_refused(client, monkeypatch):
    import resource_explorer.auth as re_auth

    monkeypatch.setattr(re_auth, "validate_egeria_token", lambda t: True)
    portal_token = jwt.encode({"sub": NPA, "role": "user", "egeria_token": _egeria_token(NPA),
                               "exp": 9999999999}, "test-portal-secret", algorithm="HS256")
    r = client.post("/api/auth/portal", json={"portal_token": portal_token})
    assert r.status_code == 403 and r.json()["detail"] == REFUSAL
    assert "access_token" not in r.json()


def test_a_raw_egeria_bearer_for_the_daemon_is_refused_before_any_egeria_call(cfg):
    from types import SimpleNamespace

    from resource_explorer.a2a_auth import A2AAuthSettings, authenticate, reset_validation_cache

    reset_validation_cache()
    settings = A2AAuthSettings(jwt_secret="s", egeria_view_server="v", egeria_platform_url="https://localhost:9443")
    asked = []

    def ok(token, config):                                      # Egeria would accept it
        asked.append(token)
        return True

    def request(sub):
        return SimpleNamespace(headers={"Authorization": f"Bearer {_egeria_token(sub)}"})

    with pytest.raises(ec.DaemonSignInRefused):
        authenticate(request(NPA), settings, validator=ok)
    assert asked == [], "refused before Egeria is asked to validate"
    person = authenticate(request("garygeeke"), settings, validator=ok)
    assert person is not None and person.user_id == "garygeeke"
    reset_validation_cache()


# ── round 2: a session for the daemon minted ELSEWHERE (shared secret) is refused on resolution ─

A2A_SECRET = "a2a-shared-secret"


def _shared_secret_session(secret, user=NPA):
    """What EA's login (or a pre-merge RE login) mints for the daemon under the shared secret."""
    return jwt.encode({"sub": user, "user_id": user, "role": "user", "egeria_token": _egeria_token(user),
                       "exp": time.time() + 600}, secret, algorithm="HS256")


def test_a_shared_secret_session_for_the_daemon_is_refused_on_the_web(client):
    from resource_explorer.auth import create_access_token

    token = create_access_token(user_id=NPA, egeria_token=_egeria_token(NPA))   # RE's own secret = the shared one
    r = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 403 and r.json()["detail"] == REFUSAL
    person = create_access_token(user_id="peterprofile", egeria_token=_egeria_token("peterprofile"))
    assert client.get("/api/auth/me", headers={"Authorization": f"Bearer {person}"}).json()["user_id"] == "peterprofile"


@pytest.mark.parametrize("anonymous", [False, True])
def test_a_shared_secret_session_for_the_daemon_is_403_on_a2a_even_when_anonymous_is_allowed(cfg, anonymous):
    from resource_explorer.a2a_auth import A2AAuthSettings, reset_validation_cache
    from resource_explorer.a2a_role import DEFAULT_A2A_PORT, build_app

    reset_validation_cache()
    settings = A2AAuthSettings(jwt_secret=A2A_SECRET, egeria_view_server="v",
                               egeria_platform_url="https://localhost:9443", allow_anonymous=anonymous)
    a2a = TestClient(build_app(host="127.0.0.1", port=DEFAULT_A2A_PORT, settings=settings))
    r = a2a.get("/whoami", headers={"Authorization": f"Bearer {_shared_secret_session(A2A_SECRET)}"})
    assert r.status_code == 403 and r.json()["detail"] == REFUSAL
    ok = a2a.get("/whoami", headers={"Authorization": f"Bearer {_shared_secret_session(A2A_SECRET, 'peterprofile')}"})
    assert ok.status_code == 200 and ok.json()["user_id"] == "peterprofile"


def test_a_raw_daemon_bearer_is_403_on_a2a_even_when_anonymous_is_allowed(cfg):
    from unittest.mock import patch

    from resource_explorer.a2a_auth import A2AAuthSettings, reset_validation_cache
    from resource_explorer.a2a_role import DEFAULT_A2A_PORT, build_app

    reset_validation_cache()
    settings = A2AAuthSettings(jwt_secret=A2A_SECRET, egeria_view_server="v",
                               egeria_platform_url="https://localhost:9443", allow_anonymous=True)
    a2a = TestClient(build_app(host="127.0.0.1", port=DEFAULT_A2A_PORT, settings=settings))
    with patch("trellis_auth.validate_egeria_token", return_value=True) as v:
        r = a2a.get("/whoami", headers={"Authorization": f"Bearer {_egeria_token(NPA)}"})
    assert r.status_code == 403 and v.call_count == 0


def test_a_cached_cli_session_for_the_daemon_is_refused(cfg, monkeypatch, tmp_path):
    from rich.console import Console

    from resource_explorer.cli import session as cli_session

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    cli_session.save_login(NPA, _egeria_token(NPA))           # a pre-merge login, still on disk
    assert cli_session.resolve_identity() == (None, None)
    console = Console(record=True, width=200)
    with pytest.raises(SystemExit) as exit_:
        cli_session.require_identity(console)
    assert exit_.value.code == cli_session.EXIT_NOT_SIGNED_IN
    assert REFUSAL in console.export_text()


# ── round 2: the source label ──────────────────────────────────────────────

def test_an_unset_egeria_user_id_is_labelled_the_code_default_and_warned(cfg, monkeypatch, caplog):
    from resource_explorer.config import EgeriaConfig

    monkeypatch.delenv("EGERIA_USER_ID", raising=False)
    cfg.egeria = EgeriaConfig(_env_file=None)                   # nothing set: config.py's default
    with caplog.at_level(logging.INFO, logger="resource_explorer.egeria_clients"):
        ec.log_daemon_identity_at_startup()
    lines = [(r.levelno, r.getMessage()) for r in caplog.records if r.name == "resource_explorer.egeria_clients"]
    assert lines == [(logging.WARNING, f"daemon identity: {cfg.egeria.user_id!r} from the code default "
                                       "(EGERIA_USER_ID unset) — a person's persona as RE's daemon; set "
                                       "EGERIA_USER_ID to RE's own account")]
    assert ec.daemon_identity_status()["source"] == "default"


def test_the_cli_login_refuses_the_daemon_and_never_prefills_it(cfg, monkeypatch, tmp_path):
    from typer.testing import CliRunner

    import resource_explorer.auth as re_auth
    from resource_explorer.cli.main import app

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    asked = []
    monkeypatch.setattr(re_auth, "login_with_password", lambda u, p: asked.append(u) or _egeria_token(u))
    r = CliRunner().invoke(app, ["login", "--user", NPA], input="pw\n")
    assert r.exit_code == 1 and REFUSAL in r.output and asked == []

    r = CliRunner().invoke(app, ["login"], input="\n")          # an empty answer: no daemon default
    assert NPA not in r.output and asked == []
