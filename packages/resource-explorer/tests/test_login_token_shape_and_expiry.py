"""The CLI login and status line, after the 2026-10-20 dead-token session.

`resource-explorer login` cached a 442-char, 6-dot value with no `exp` that Egeria
401'd on every call, and the session file said it was good until 05:11 — RE's
own 8-hour cap, not anything Egeria said. Two things are pinned here:

* the CLI login makes the same one authorized read web login makes, and caches
  nothing when Egeria refuses the token;
* a session whose Egeria token has no `exp` says its expiry is unknown, and
  `whoami` asks Egeria rather than trusting the file.

The token-shape gate itself lives in trellis-auth (`is_well_formed_egeria_token`)
and is tested there.
"""
from __future__ import annotations

import jwt
import pytest
from typer.testing import CliRunner

from tests.test_login_and_identity import _auth_env  # noqa: F401  (autouse: known secret)


def _egeria_token(sub: str, exp: int | None = 9999999999) -> str:
    claims = {"iss": "self", "sub": sub}
    if exp is not None:
        claims["exp"] = exp
    return jwt.encode(claims, "irrelevant", algorithm="HS256")


@pytest.fixture(autouse=True)
def _config_home(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))


def _verdict(monkeypatch, verdict):
    import resource_explorer.egeria_clients as ec

    calls = []
    monkeypatch.setattr(ec, "check_token_accepted", lambda u, t: calls.append(u) or verdict)
    return calls


def _login(monkeypatch, token):
    import resource_explorer.auth as re_auth
    from resource_explorer.cli.main import app

    monkeypatch.setattr(re_auth, "login_with_password", lambda u, p: token)
    return CliRunner().invoke(app, ["login", "--user", "peterprofile"], input="pw\n")


# ── CLI login ───────────────────────────────────────────────────────────────

def test_cli_login_refused_by_egeria_caches_nothing(monkeypatch):
    from resource_explorer.cli import session as cli_session

    calls = _verdict(monkeypatch, "refused")
    r = _login(monkeypatch, _egeria_token("peterprofile"))
    assert r.exit_code == 1, r.output
    assert "refuses its requests" in r.output
    assert calls == ["peterprofile"]
    assert cli_session.load_session() is None


def test_cli_login_accepted_names_egerias_expiry(monkeypatch):
    _verdict(monkeypatch, "accepted")
    r = _login(monkeypatch, _egeria_token("peterprofile"))
    assert r.exit_code == 0, r.output
    assert "Signed in as peterprofile until" in r.output
    assert "unknown" not in r.output


def test_cli_login_unreachable_check_still_signs_in_and_says_so(monkeypatch):
    _verdict(monkeypatch, "unreachable")
    r = _login(monkeypatch, _egeria_token("peterprofile"))
    assert r.exit_code == 0, r.output
    assert "Could not check with Egeria" in r.output


def test_cli_login_with_an_undated_token_does_not_claim_an_until(monkeypatch):
    _verdict(monkeypatch, "accepted")
    r = _login(monkeypatch, _egeria_token("peterprofile", exp=None))
    assert r.exit_code == 0, r.output
    assert "expiry is unknown" in r.output
    assert "Signed in as peterprofile until" not in r.output


def test_the_password_and_token_never_reach_the_output(monkeypatch):
    _verdict(monkeypatch, "accepted")
    token = _egeria_token("peterprofile")
    r = _login(monkeypatch, token)
    assert token not in r.output and "pw\n" not in r.output.replace("Password: ", "")


# ── session_status_line / whoami ────────────────────────────────────────────

def _cache(sub="peterprofile", exp=9999999999):
    from resource_explorer.cli import session as cli_session

    return cli_session.save_login(sub, _egeria_token(sub, exp=exp))


def test_status_line_with_an_undated_egeria_token_says_expiry_unknown():
    """The reported case: the app JWT's exp is RE's 8-hour cap, which the line must
    not present as Egeria's word."""
    from resource_explorer.cli import session as cli_session

    record = _cache(exp=None)
    assert record.expires_at is not None           # RE's cap is there…
    line = cli_session.session_status_line()
    assert "expiry is unknown" in line             # …but not passed off as Egeria's
    assert not line.startswith("signed in as peterprofile until")


def test_status_line_with_a_dated_egeria_token_says_until():
    from resource_explorer.cli import session as cli_session

    _cache()
    assert cli_session.session_status_line().startswith("signed in as peterprofile until ")


def test_status_check_reports_a_token_egeria_refuses(monkeypatch):
    from resource_explorer.cli import session as cli_session

    _cache()
    calls = _verdict(monkeypatch, "refused")
    line = cli_session.session_status_line(check_with_egeria=True)
    assert "Egeria refuses this session's token" in line
    assert "resource-explorer login" in line
    assert calls == ["peterprofile"]


def test_status_without_check_makes_no_egeria_call(monkeypatch):
    from resource_explorer.cli import session as cli_session

    _cache()
    calls = _verdict(monkeypatch, "refused")
    cli_session.session_status_line()
    assert calls == []


def test_whoami_command_checks_with_egeria(monkeypatch):
    from resource_explorer.cli.main import app

    _cache()
    _verdict(monkeypatch, "unreachable")
    r = CliRunner().invoke(app, ["whoami"])
    assert r.exit_code == 0, r.output
    assert "signed in as peterprofile until" in r.output
    assert "could not check with Egeria" in r.output
