"""TR-42: EA's log says who a v2 answer ran as, and who any token mint signed in as. Names only.

In U3 (2026-11-17) "no NPA sign-in in the log" was a void check: the log never named who a v2 answer ran as, nor the
user pyegeria minted a token for, so the absence of a service-account name proved nothing (absence-as-answer). Now:
- `v2.answer` logs "v2 answer as <user> via bearer token" (or "with no bearer token") once per run, at INFO;
- `auth.apply_token` logs "Egeria token minted for <user> (password sign-in)" whenever it has to mint, and nothing
  when the caller brought a token (no mint happened).
Never a password or a token value.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from advisor import auth
from advisor import spec_authoring_v2 as v2

SECRET_TOKEN = "eyJ-secret-bearer-token-value"
SECRET_PWD = "hunter2-secret-password"


@pytest.fixture
def said():
    from loguru import logger
    lines: list[str] = []
    sink = logger.add(lambda m: lines.append(m.record["message"]), level="INFO")
    yield lines
    logger.remove(sink)


def _refused_spec():
    return {"conditions": {v2.STATUS_REFUSED: "status 'Bogus' is not one Egeria knows"}}


def test_a_v2_answer_names_its_user_and_the_token(said):
    v2.answer(_refused_spec(), SimpleNamespace(user_id="erinoverview", token=SECRET_TOKEN))
    assert "v2 answer as erinoverview via bearer token" in said


def test_a_v2_answer_with_no_token_says_so(said):
    v2.answer(_refused_spec(), SimpleNamespace(user_id="peterprofile", token=None))
    assert "v2 answer as peterprofile with no bearer token" in said


def test_a_v2_answer_that_runs_logs_its_identity_once(said, monkeypatch):
    import pyegeria.authoring as authoring

    monkeypatch.setattr(authoring, "preview", lambda *a, **k: {"rows": [], "columns": [], "total": 0})
    spec = {"conditions": {}, "columns": [], "target_type": "Risk"}
    try:
        v2.answer(spec, SimpleNamespace(user_id="erinoverview", token=SECRET_TOKEN))
    except Exception:  # noqa: BLE001 -- rendering a fake preview may not complete; the identity line comes first
        pass
    assert said.count("v2 answer as erinoverview via bearer token") == 1


def test_nothing_secret_reaches_the_log(said):
    v2.answer(_refused_spec(), SimpleNamespace(user_id="erinoverview", token=SECRET_TOKEN, user_pwd=SECRET_PWD))
    blob = "\n".join(said)
    assert SECRET_TOKEN not in blob and SECRET_PWD not in blob


def test_a_password_mint_names_the_user_it_signed_in_as(said, monkeypatch):
    calls = []
    monkeypatch.setattr(auth, "_trellis_apply_token", lambda client, token: calls.append((client.user_id, token)))
    auth.apply_token(SimpleNamespace(user_id="egeriaadvisornpa", user_pwd=SECRET_PWD), None)
    assert calls == [("egeriaadvisornpa", None)]
    assert "Egeria token minted for egeriaadvisornpa (password sign-in)" in said
    assert SECRET_PWD not in "\n".join(said)


def test_a_caller_supplied_token_is_not_a_mint(said, monkeypatch):
    monkeypatch.setattr(auth, "_trellis_apply_token", lambda client, token: None)
    auth.apply_token(SimpleNamespace(user_id="erinoverview", user_pwd=None), SECRET_TOKEN)
    assert not [line for line in said if "minted" in line]
    assert SECRET_TOKEN not in "\n".join(said)
