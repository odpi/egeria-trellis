"""S3 (BRIEFS-EA-PHASE0-SAFETY-2026-11-10): Dr.Egeria runs as the signed-in user.

A fake EgeriaTech records how each client was authenticated. No Egeria, no
DB: the dispatcher is faked too, so nothing is validated or processed for real.
"""
import json

import httpx
import pytest
from pyegeria.core._no_password import NO_PASSWORD

import pyegeria
from pyegeria import PyegeriaUnauthorizedException

import md_processing.dr_egeria as dr_egeria_mod
from advisor.agents import dr_egeria_agent as mod
from advisor.agents.dr_egeria_agent import (
    SIGN_IN_AGAIN,
    SIGN_IN_TO_RUN,
    DrEgeriaActionAgent,
)

MARKDOWN = "## Create Glossary\n\n### Display Name\nTerms\n"
USER = {"user_id": "erinoverview", "password": "", "token": "user-token-abc"}


def _unauthorized():
    resp = httpx.Response(401, text="", request=httpx.Request("POST", "https://egeria/x"))
    return PyegeriaUnauthorizedException(resp, {"caller method": "test"}, {"userid": "erinoverview"})


class FakeEgeriaTech:
    """Records construction and every way it was authenticated."""

    instances: list = []
    profile_error = None  # exception the profile probe raises, if any

    def __init__(self, view_server=None, platform_url=None, user_id=None, user_pwd=None, *a, **kw):
        self.args = {"view_server": view_server, "platform_url": platform_url,
                     "user_id": user_id, "user_pwd": user_pwd, "extra": (a, kw)}
        self.bearer_tokens = []
        self.minted = []
        FakeEgeriaTech.instances.append(self)

    def set_bearer_token(self, token):
        self.bearer_tokens.append(token)

    def create_egeria_bearer_token(self, *args, **kwargs):
        self.minted.append((args, kwargs))
        return "service-token"

    async def _async_get_my_profile(self, *a, **kw):
        if FakeEgeriaTech.profile_error is not None:
            raise FakeEgeriaTech.profile_error
        return {"profile": "ok"}


class FakeDispatcher:
    def __init__(self, client, results=None, raises=None):
        self.client = client
        self.results = results if results is not None else [
            {"is_command": True, "status": "success", "verb": "Create",
             "object_type": "Glossary", "message": "validated", "output": "ok"}
        ]
        self.raises = raises
        self.calls = []

    async def dispatch_batch(self, commands, context):
        self.calls.append(context["directive"])
        if self.raises is not None:
            raise self.raises
        return self.results


@pytest.fixture
def fakes(monkeypatch):
    FakeEgeriaTech.instances = []
    FakeEgeriaTech.profile_error = None
    state = {"dispatchers": [], "results": None, "raises": None}

    def fake_setup_dispatcher(client):
        d = FakeDispatcher(client, state["results"], state["raises"])
        state["dispatchers"].append(d)
        return d

    monkeypatch.setattr(pyegeria, "EgeriaTech", FakeEgeriaTech)
    monkeypatch.setattr(dr_egeria_mod, "setup_dispatcher", fake_setup_dispatcher)
    from advisor.config import settings
    monkeypatch.setattr(settings, "daemon_user_id", "svc-user", raising=False)
    monkeypatch.setattr(settings, "daemon_user_password", "svc-secret", raising=False)
    return state


@pytest.fixture
def agent():
    a = DrEgeriaActionAgent.__new__(DrEgeriaActionAgent)
    a._config_path = "unused"
    a._template_index = None
    a._egeria_conn = {"url": "https://egeria.test:9443", "server_name": "qs-view-server"}
    return a


def _run(agent, creds, directive="validate", **kw):
    return json.loads(agent.execute(MARKDOWN, directive=directive, egeria_credentials=creds, **kw))


class TestSignedInRunUsesTheUsersToken:
    def test_token_applied_no_password_no_minting(self, fakes, agent):
        env = _run(agent, USER)
        assert env["success"] is True
        assert env.get("auth_required") is None
        [client] = FakeEgeriaTech.instances
        assert client.args["user_id"] == "erinoverview"
        assert client.args["user_pwd"] is NO_PASSWORD   # explicit (TR-34 R1), never the settings password
        assert "svc-secret" not in json.dumps(client.args, default=str)
        assert client.bearer_tokens == ["user-token-abc"]
        assert client.minted == []
        # the dispatcher ran on that very client, validate only
        [d] = fakes["dispatchers"]
        assert d.client is client and d.calls == ["validate"]

    def test_service_operation_flag_is_ignored_for_a_signed_in_user(self, fakes, agent):
        _run(agent, USER, service_operation=True)
        [client] = FakeEgeriaTech.instances
        assert client.bearer_tokens == ["user-token-abc"] and client.minted == []
        assert client.args["user_pwd"] is NO_PASSWORD   # explicit (TR-34 R1), never the settings password

    def test_a_password_in_the_credentials_is_never_used(self, fakes, agent):
        _run(agent, {**USER, "password": "leaked"})
        [client] = FakeEgeriaTech.instances
        assert client.args["user_pwd"] is NO_PASSWORD and client.minted == []   # explicit (TR-34 R1)

    def test_credentials_are_not_cached_across_users(self, fakes, agent):
        _run(agent, USER)
        _run(agent, {"user_id": "peterprofile", "password": "", "token": "peter-token"})
        a, b = FakeEgeriaTech.instances
        assert (a.args["user_id"], a.bearer_tokens) == ("erinoverview", ["user-token-abc"])
        assert (b.args["user_id"], b.bearer_tokens) == ("peterprofile", ["peter-token"])


class TestRefusedWithoutCredentials:
    @pytest.mark.parametrize("creds", [None, {}, {"user_id": "", "token": ""}])
    def test_no_credentials_refused(self, fakes, agent, creds):
        env = _run(agent, creds)
        assert env["success"] is False
        assert env["output"] == SIGN_IN_TO_RUN == "Sign in to run Dr.Egeria commands."
        assert env["auth_required"] is True and env["auth_reason"] == "no_credentials"
        assert FakeEgeriaTech.instances == []  # no client, so no service password either
        assert fakes["dispatchers"] == []

    def test_user_without_token_refused_not_minted(self, fakes, agent):
        env = _run(agent, {"user_id": "erinoverview", "password": "", "token": ""})
        assert env["output"] == SIGN_IN_TO_RUN and env["auth_reason"] == "no_token"
        assert FakeEgeriaTech.instances == []

    def test_handle_shows_the_sentence(self, fakes, agent, monkeypatch):
        tpl = {"command_name": "Create Glossary", "attributes": []}
        monkeypatch.setattr(agent, "find_template", lambda q: tpl)
        monkeypatch.setattr(agent, "extract_params", lambda q, t: {})
        monkeypatch.setattr(mod, "compose_command", lambda t, p: MARKDOWN)
        out = agent.handle("create a glossary", directive="validate", egeria_credentials=None)
        assert out["response"] == SIGN_IN_TO_RUN
        assert out["auth_required"] is True
        assert FakeEgeriaTech.instances == []

    def test_explicit_service_operation_uses_service_account(self, fakes, agent):
        env = _run(agent, None, service_operation=True)
        assert env["success"] is True
        [client] = FakeEgeriaTech.instances
        assert client.args["user_id"] == "svc-user" and client.args["user_pwd"] == "svc-secret"
        assert len(client.minted) == 1 and client.bearer_tokens == []


class TestRefusedTokenSaysSignInAgain:
    def test_401_on_probe_no_password_retry(self, fakes, agent):
        FakeEgeriaTech.profile_error = _unauthorized()
        env = _run(agent, USER)
        assert env["success"] is False
        assert env["output"] == SIGN_IN_AGAIN and "Sign in again" in SIGN_IN_AGAIN
        assert env["auth_reason"] == "token_refused"
        [client] = FakeEgeriaTech.instances  # one client, never rebuilt with a password
        assert client.minted == [] and client.args["user_pwd"] is NO_PASSWORD   # explicit (TR-34 R1), never the settings password
        assert fakes["dispatchers"] == []  # nothing validated on a refused token

    def test_expired_token_refused_before_any_call(self, fakes, agent, monkeypatch):
        import trellis_auth.auth as ta
        monkeypatch.setattr(ta, "egeria_token_expiry", lambda t: 1)
        env = _run(agent, USER)
        assert env["output"] == SIGN_IN_AGAIN
        assert fakes["dispatchers"] == []

    def test_401_raised_out_of_the_dispatcher(self, fakes, agent):
        fakes["raises"] = _unauthorized()
        env = _run(agent, USER)
        assert env["output"] == SIGN_IN_AGAIN and env["auth_required"] is True
        [client] = FakeEgeriaTech.instances
        assert client.minted == []

    def test_401_swallowed_into_a_command_failure(self, fakes, agent):
        fakes["results"] = [{
            "is_command": True, "status": "failure", "verb": "Create", "object_type": "Glossary",
            "message": "Execution failed: AUTHORIZATION_ERROR_401 User not authorized",
        }]
        env = _run(agent, USER)
        assert env["success"] is False and env["auth_required"] is True
        assert env["execution_errors"][0]["message"] == SIGN_IN_AGAIN
        [client] = FakeEgeriaTech.instances
        assert client.minted == []

    def test_handle_says_sign_in_again(self, fakes, agent, monkeypatch):
        FakeEgeriaTech.profile_error = _unauthorized()
        tpl = {"command_name": "Create Glossary", "attributes": []}
        monkeypatch.setattr(agent, "find_template", lambda q: tpl)
        monkeypatch.setattr(agent, "extract_params", lambda q, t: {})
        monkeypatch.setattr(mod, "compose_command", lambda t, p: MARKDOWN)
        out = agent.handle("create a glossary", directive="validate", egeria_credentials=USER)
        assert out["response"] == SIGN_IN_AGAIN

    def test_other_probe_errors_do_not_block_the_run(self, fakes, agent):
        FakeEgeriaTech.profile_error = RuntimeError("no profile for this user")
        env = _run(agent, USER)
        assert env["success"] is True
        assert len(fakes["dispatchers"]) == 1


def test_real_apply_token_sets_the_bearer_and_never_mints(monkeypatch):
    """The real advisor.auth.apply_token on a fake client: token → set, no mint."""
    from advisor.auth import apply_token
    c = FakeEgeriaTech("s", "u", "erinoverview")
    apply_token(c, "tok")
    assert c.bearer_tokens == ["tok"] and c.minted == []
