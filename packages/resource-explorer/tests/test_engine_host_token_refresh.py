"""F57: the engine host re-mints its daemon token on a 401 and retries the pass once.

Seen live 2026-11-04 21:00Z: the Egeria platform restarted (JVM OOM), every token it had issued died, and
`egeria_clients` re-mints a daemon token only at its own `exp` (`_daemon_token_due`) — so every sweep and
completion pass failed AUTHORIZATION_ERROR_401 for an hour until 8813 was restarted. Here a client that 401s
once then succeeds costs one refresh, one retry and no ERROR; one that 401s twice is the ERROR it always was.
Clients are autospecs at the one constructor seam, as in test_engine_host.py.
"""
from __future__ import annotations

import logging

import pytest

from resource_explorer import egeria_clients
from tests.test_engine_host import Fakes, _action, _engine_rows, _host, enabled, reg  # noqa: F401 - fixtures

LOGGER = "resource_explorer.engine_host"


def _dead_token():
    """A bare 401 transport answer (no Egeria JSON body), as pyegeria raises it for an invalidated bearer token."""
    from pyegeria.core._exceptions import PyegeriaUnauthorizedException

    return PyegeriaUnauthorizedException(None, context={"caller method": "get_approved_engine_actions"},
                                         additional_info={"relatedHTTPCode": 401,
                                                          "exceptionErrorMessageId": "AUTHORIZATION_ERROR_401"})


@pytest.fixture
def refreshes(monkeypatch):
    """Counts `EgeriaClients.refresh` calls and records which identity each was for."""
    calls: list[str] = []
    original = egeria_clients.EgeriaClients.refresh

    def counting(self):
        calls.append(self.identity.kind)
        return original(self)

    monkeypatch.setattr(egeria_clients.EgeriaClients, "refresh", counting)
    return calls


def _holding_a_token(fakes: Fakes) -> Fakes:
    """The fakes mint a real (string) token, so the client set HOLDS one and `of()` does not re-mint on every
    call for want of one (an autospec's default MagicMock is not a token): only F57's refresh then counts."""
    fakes.engine.create_egeria_bearer_token.return_value = "a-daemon-token"
    fakes.expert.create_egeria_bearer_token.return_value = "a-daemon-token"
    return fakes


def _fail_n_times(fakes: Fakes, n: int):
    """The approved listing 401s on its first `n` calls, then answers the page as before."""
    original = fakes.engine.get_approved_engine_actions.side_effect
    seen = {"calls": 0}

    def listing(*args, **kwargs):
        seen["calls"] += 1
        if seen["calls"] <= n:
            raise _dead_token()
        return original(*args, **kwargs)

    fakes.engine.get_approved_engine_actions.side_effect = listing
    return seen


def test_one_401_costs_one_refresh_one_retry_and_no_error(reg, enabled, monkeypatch, caplog, refreshes):
    fakes = _holding_a_token(Fakes(monkeypatch, [_action("a-good")]))
    seen = _fail_n_times(fakes, 1)
    host = _host(reg)
    try:
        with caplog.at_level(logging.INFO, logger=LOGGER):
            result = host.sweep_once()
    finally:
        host.close()

    assert result.claimed == ["a-good"], "the retried pass does the work the refused one could not"
    assert seen["calls"] == 2, "exactly one retry"
    assert refreshes == ["daemon"], f"one refresh of the daemon clients, never a person's: {refreshes}"
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR], "a recovered 401 is not an error"
    minted = [r.getMessage() for r in caplog.records if "re-minted" in r.getMessage()]
    assert len(minted) == 1 and "401" in minted[0] and "Egeria restarted?" in minted[0], minted
    assert [r["target"]["engine_action_guid"] for r in _engine_rows(reg)] == ["a-good"]


def test_a_second_401_is_the_error_it_always_was(reg, enabled, monkeypatch, caplog, refreshes):
    from pyegeria.core._exceptions import PyegeriaUnauthorizedException

    from resource_explorer.engine_host import EngineHostLoop

    fakes = _holding_a_token(Fakes(monkeypatch, [_action("a-good")]))
    seen = _fail_n_times(fakes, 4)          # the token stays dead for two whole passes
    host = _host(reg)
    try:
        with pytest.raises(PyegeriaUnauthorizedException):
            host.sweep_once()
        assert seen["calls"] == 2, "one retry, never a loop"
        assert refreshes == ["daemon"], "refreshed once; a second refusal is not retried again"
        with caplog.at_level(logging.INFO, logger=LOGGER):
            EngineHostLoop._guarded(lambda: host.sweep_once(), "sweep")   # the loop's guard: the existing ERROR
    finally:
        host.close()
    assert seen["calls"] == 4 and refreshes == ["daemon", "daemon"], "each pass: one refresh, one retry"
    errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert len(errors) == 1 and "sweep failed" in errors[0].getMessage(), [r.getMessage() for r in errors]
    assert fakes.claims == [] and _engine_rows(reg) == []


def test_the_completion_pass_has_the_same_once_and_retry(reg, enabled, monkeypatch, caplog, refreshes):
    fakes = _holding_a_token(Fakes(monkeypatch, []))
    original = fakes.engine.get_active_claimed_engine_actions.side_effect
    seen = {"calls": 0}

    def claimed(*args, **kwargs):
        seen["calls"] += 1
        if seen["calls"] == 1:
            raise _dead_token()
        return original(*args, **kwargs)

    fakes.engine.get_active_claimed_engine_actions.side_effect = claimed
    host = _host(reg)
    try:
        with caplog.at_level(logging.INFO, logger=LOGGER):
            result = host.restart_pass()
    finally:
        host.close()
    assert seen["calls"] == 2 and refreshes == ["daemon"]
    assert result.errors == [] and not [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert any("completion pass" in r.getMessage() and "re-minted" in r.getMessage() for r in caplog.records)


def test_a_refusal_that_is_not_a_token_refusal_is_untouched(reg, enabled, monkeypatch, refreshes):
    fakes = _holding_a_token(Fakes(monkeypatch, [_action("a-good")]))
    fakes.engine.get_approved_engine_actions.side_effect = RuntimeError("network down")
    host = _host(reg)
    try:
        with pytest.raises(RuntimeError):
            host.sweep_once()
    finally:
        host.close()
    assert refreshes == [], "only a 401/403 earns a re-mint"


def test_is_token_refused_reads_the_same_verdict_as_the_login_check():
    from resource_explorer.engine_host import is_token_refused

    assert is_token_refused(_dead_token())
    assert not is_token_refused(RuntimeError("timeout"))
