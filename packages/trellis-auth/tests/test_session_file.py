"""Tests for the CLI's cached-login file.

Every test redirects `XDG_CONFIG_HOME` at a tmp_path, so nothing here can touch
the developer's real `~/.config/trellis`.
"""
from __future__ import annotations

import json
import stat
from datetime import datetime, timedelta, timezone

import jwt
import pytest

from trellis_auth import session_file


@pytest.fixture(autouse=True)
def isolated_config_home(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    return tmp_path / "config"


def _app_jwt(exp_delta_seconds: int) -> str:
    exp = datetime.now(timezone.utc) + timedelta(seconds=exp_delta_seconds)
    return jwt.encode({"sub": "peterprofile", "exp": exp}, "test-secret", algorithm="HS256")


# ---------------------------------------------------------------------------
# Location
# ---------------------------------------------------------------------------

def test_session_path_follows_xdg(isolated_config_home):
    path = session_file.session_path("egeria-advisor")
    assert path == isolated_config_home / "trellis" / "egeria-advisor" / "session.json"


def test_config_root_falls_back_to_dot_config(monkeypatch, tmp_path):
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    assert session_file.config_root() == tmp_path / ".config" / "trellis"


# ---------------------------------------------------------------------------
# Round trip
# ---------------------------------------------------------------------------

def test_round_trip(tmp_path):
    token = _app_jwt(3600)
    saved = session_file.save_session("egeria-advisor", "peterprofile", token)

    loaded = session_file.load_session("egeria-advisor")
    assert loaded is not None
    assert loaded.user_id == "peterprofile"
    assert loaded.token == token
    assert loaded.app_name == "egeria-advisor"
    assert loaded.expires_at == saved.expires_at
    assert loaded.is_expired is False


def test_expiry_is_taken_from_the_token_when_not_given():
    token = _app_jwt(1800)
    record = session_file.save_session("egeria-advisor", "peterprofile", token)
    claims = jwt.decode(token, options={"verify_signature": False})
    assert record.expires_at == int(claims["exp"])


def test_the_password_is_never_anywhere_in_the_file():
    """A regression guard with teeth: assert on the raw bytes.

    The whole point of caching the app JWT rather than credentials is that a
    stolen session file is a one-hour bearer token, not a permanent password.
    A future `save_session(..., password=...)` convenience would break that,
    and this is what would fail.
    """
    session_file.save_session("egeria-advisor", "peterprofile", _app_jwt(3600))
    raw = session_file.session_path("egeria-advisor").read_bytes()
    assert b"password" not in raw.lower()


def test_file_mode_is_0600():
    session_file.save_session("egeria-advisor", "peterprofile", _app_jwt(3600))
    path = session_file.session_path("egeria-advisor")
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_directory_mode_is_0700():
    session_file.save_session("egeria-advisor", "peterprofile", _app_jwt(3600))
    parent = session_file.session_path("egeria-advisor").parent
    assert stat.S_IMODE(parent.stat().st_mode) == 0o700


def test_overwriting_an_existing_session_keeps_the_mode():
    """The second write goes through a temp file + rename; the mode must survive."""
    session_file.save_session("egeria-advisor", "peterprofile", _app_jwt(3600))
    session_file.save_session("egeria-advisor", "erinoverview", _app_jwt(3600))
    path = session_file.session_path("egeria-advisor")
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert session_file.load_session("egeria-advisor").user_id == "erinoverview"


def test_no_temp_files_are_left_behind():
    session_file.save_session("egeria-advisor", "peterprofile", _app_jwt(3600))
    parent = session_file.session_path("egeria-advisor").parent
    assert [p.name for p in parent.iterdir()] == ["session.json"]


# ---------------------------------------------------------------------------
# Absence, expiry, corruption
# ---------------------------------------------------------------------------

def test_load_returns_none_when_there_is_no_session():
    assert session_file.load_session("egeria-advisor") is None


def test_an_expired_session_is_returned_and_flagged():
    """Returned, not swallowed: the caller needs the time to print a message."""
    session_file.save_session("egeria-advisor", "peterprofile", _app_jwt(-60))
    record = session_file.load_session("egeria-advisor")
    assert record is not None
    assert record.is_expired is True


def test_a_token_with_no_exp_is_not_treated_as_expired():
    """"I cannot tell when this expires" must not mean "you are signed out"."""
    opaque = jwt.encode({"sub": "peterprofile"}, "test-secret", algorithm="HS256")
    session_file.save_session("egeria-advisor", "peterprofile", opaque)
    record = session_file.load_session("egeria-advisor")
    assert record.expires_at is None
    assert record.is_expired is False


def test_a_corrupt_session_file_is_ignored_not_raised(caplog):
    session_file.save_session("egeria-advisor", "peterprofile", _app_jwt(3600))
    session_file.session_path("egeria-advisor").write_text("{not json")
    with caplog.at_level("WARNING", logger="trellis_auth.session_file"):
        assert session_file.load_session("egeria-advisor") is None
    assert any("unreadable session" in r.message for r in caplog.records)


def test_a_session_file_with_no_token_is_ignored():
    path = session_file.session_path("egeria-advisor")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"app": "egeria-advisor", "user_id": "peterprofile"}))
    assert session_file.load_session("egeria-advisor") is None


def test_save_rejects_an_empty_token():
    with pytest.raises(ValueError):
        session_file.save_session("egeria-advisor", "peterprofile", "")


# ---------------------------------------------------------------------------
# clear / message
# ---------------------------------------------------------------------------

def test_clear_session_removes_the_file_and_reports_what_it_did():
    session_file.save_session("egeria-advisor", "peterprofile", _app_jwt(3600))
    assert session_file.clear_session("egeria-advisor") is True
    assert session_file.load_session("egeria-advisor") is None
    assert session_file.clear_session("egeria-advisor") is False


def test_expired_message_names_the_time_and_the_command():
    session_file.save_session("egeria-advisor", "peterprofile", _app_jwt(-60))
    record = session_file.load_session("egeria-advisor")
    message = session_file.expired_message(record, "egeria-advisor login")
    expected_time = f"{record.expires_at_local:%H:%M}"
    assert message == f"session expired at {expected_time}, run `egeria-advisor login`"


def test_expired_message_without_a_session_says_not_signed_in():
    assert session_file.expired_message(None, "egeria-advisor login") == (
        "not signed in, run `egeria-advisor login`"
    )


def test_two_apps_do_not_share_a_session_file():
    session_file.save_session("egeria-advisor", "peterprofile", _app_jwt(3600))
    session_file.save_session("resource-explorer", "erinoverview", _app_jwt(3600))
    assert session_file.load_session("egeria-advisor").user_id == "peterprofile"
    assert session_file.load_session("resource-explorer").user_id == "erinoverview"


# ---------------------------------------------------------------------------
# egeria_expires_at — is the session's end Egeria's word, or the app's cap?
# ---------------------------------------------------------------------------

def _app_jwt_carrying(egeria_token: str) -> str:
    exp = datetime.now(timezone.utc) + timedelta(hours=8)
    return jwt.encode({"sub": "peterprofile", "exp": exp, "egeria_token": egeria_token},
                      "test-secret", algorithm="HS256")


def test_egeria_expiry_is_read_from_the_carried_egeria_token():
    egeria_exp = int((datetime.now(timezone.utc) + timedelta(hours=1)).timestamp())
    egeria = jwt.encode({"sub": "peterprofile", "exp": egeria_exp}, "k", algorithm="HS256")
    record = session_file.save_session("egeria-advisor", "peterprofile", _app_jwt_carrying(egeria))
    assert record.egeria_expires_at == egeria_exp
    assert record.egeria_expiry_known is True


def test_an_undated_egeria_token_leaves_the_egeria_expiry_unknown():
    """The 2026-10-20 case: the app JWT has an 8-hour exp of our own making, the
    Egeria token inside it has none — expires_at is set, but it is not Egeria's."""
    record = session_file.save_session("egeria-advisor", "peterprofile", _app_jwt_carrying("opaque-token"))
    assert record.expires_at is not None
    assert record.egeria_expires_at is None
    assert record.egeria_expiry_known is False
