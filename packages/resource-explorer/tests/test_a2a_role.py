"""The `a2a` process role: one port, path routing, and a bearer token on every call.

What these pin, and why each would otherwise be invisible:

* **Routing** — the old layout gave each agent its own port, so "can I reach the
  docs agent" was answered by the OS. Under one port it is answered by mount
  order, and a mount added after the catch-all `/` mount is simply unreachable
  with no error anywhere. The per-agent card fetch is the cheapest way to prove
  each mount is live *and* that it is the right agent behind it.
* **401 without a token** — the regression this guards is the whole point of the
  role. An A2A surface that quietly went back to answering unauthenticated is
  indistinguishable from a working one until someone looks at who called it.
* **The Egeria-token cache** — asserted by *call count*, not by latency. A cache
  that silently stopped caching would still return the right answer; only the
  number of round trips to the view server changes, which is exactly the kind of
  regression that shows up as "Egeria is slow" months later.
* **Anonymous mode** — asserted in both directions. Testing only that the env var
  opens the door would pass against a service that was open either way.
* **The index** — asserted to list *every* agent, from the same constant the
  server mounts from, so adding an eighth agent cannot silently leave it
  undiscoverable.
"""
from __future__ import annotations

import time
from unittest.mock import patch

import jwt
import pytest
from fastapi.testclient import TestClient

from resource_explorer.a2a_auth import (
    A2AAuthSettings,
    reset_validation_cache,
)
from resource_explorer.a2a_role import DEFAULT_A2A_PORT, build_app
from resource_explorer.agentstack_server import AGENT_NAMES

SECRET = "a2a-test-secret"


@pytest.fixture(autouse=True)
def _clean_cache():
    reset_validation_cache()
    yield
    reset_validation_cache()


def _settings(**overrides) -> A2AAuthSettings:
    base = dict(
        jwt_secret=SECRET,
        egeria_view_server="qs-view-server",
        egeria_platform_url="https://localhost:9443",
    )
    base.update(overrides)
    return A2AAuthSettings(**base)


def _client(settings: A2AAuthSettings | None = None) -> TestClient:
    return TestClient(build_app(host="127.0.0.1", port=DEFAULT_A2A_PORT,
                                settings=settings or _settings()))


def _app_jwt(user_id: str = "peterprofile", egeria_token: str = "egeria-abc") -> str:
    return jwt.encode(
        {
            "sub": user_id,
            "user_id": user_id,
            "role": "user",
            "display_name": "Peter Profile",
            "egeria_token": egeria_token,
            "exp": time.time() + 600,
        },
        SECRET,
        algorithm="HS256",
    )


# Not the configured daemon (EGERIA_USER_ID defaults to erinoverview), which may not sign in (Brief L).
def _egeria_jwt(user_id: str = "garygeeke", ttl: int = 3600) -> str:
    """A token shaped like Egeria's own: RS256 in life, but only its claims
    matter here — nothing verifies its signature, by design."""
    return jwt.encode(
        {"iss": "self", "sub": user_id, "iat": time.time(), "exp": time.time() + ttl},
        "not-our-key",
        algorithm="HS256",
    )


# ── routing ─────────────────────────────────────────────────────────────


class TestRouting:
    def test_every_agent_is_reachable_by_path(self):
        client = _client()
        for name in AGENT_NAMES:
            r = client.get(f"/agents/{name}/.well-known/agent-card.json")
            assert r.status_code == 200, f"{name} is not mounted"
            expected = "" if name == "orchestrator" else f"/agents/{name}"
            # The orchestrator's card points at the root because the root is
            # where it answers as the default agent; every other card must
            # point at its own mount or it sends clients to the wrong agent.
            assert expected in r.json()["url"], (name, r.json()["url"])

    def test_each_path_reaches_a_different_agent(self):
        client = _client()
        names = {
            n: client.get(f"/agents/{n}/.well-known/agent-card.json").json()["name"]
            for n in AGENT_NAMES
        }
        assert len(set(names.values())) == len(AGENT_NAMES), names

    def test_orchestrator_is_the_default_at_the_root(self):
        client = _client()
        root = client.get("/.well-known/agent-card.json")
        assert root.status_code == 200
        assert root.json()["name"] == "Resource Explorer"
        named = client.get("/agents/orchestrator/.well-known/agent-card.json")
        assert named.json()["name"] == root.json()["name"]

    def test_each_agent_exposes_the_a2a_rest_send_endpoint(self):
        """Two halves, because neither alone is proof.

        Over HTTP every agent path answers **401**, not 404 — but auth runs in
        front of routing, so a 401 alone is also what an unmounted path would
        return. The second half drops the gate and asks for the same path with
        the wrong *method*: **405** means the route is really there, **404**
        would mean the mount is empty. A correct POST is deliberately not used
        — an accepted `message:send` runs the agent, and therefore the LLM and
        the vector store, which is not what this test is about.
        """
        client = _client()
        for name in AGENT_NAMES:
            assert client.post(
                f"/agents/{name}/v1/message:send", json={}
            ).status_code == 401, name

        anon = _client(_settings(allow_anonymous=True))
        for name in AGENT_NAMES:
            r = anon.get(f"/agents/{name}/v1/message:send")
            assert r.status_code == 405, (name, r.status_code)


# ── authentication ──────────────────────────────────────────────────────


class TestAuthentication:
    def test_401_without_a_token(self):
        r = _client().get("/whoami")
        assert r.status_code == 401
        assert r.headers["www-authenticate"].startswith("Bearer")

    def test_401_with_a_non_bearer_scheme(self):
        r = _client().get("/whoami", headers={"Authorization": "Basic abc"})
        assert r.status_code == 401

    def test_401_with_a_token_signed_by_someone_else(self):
        forged = jwt.encode({"sub": "mallory", "exp": time.time() + 600},
                            "not-the-secret", algorithm="HS256")
        with patch("trellis_auth.validate_egeria_token", return_value=False):
            r = _client().get("/whoami", headers={"Authorization": f"Bearer {forged}"})
        assert r.status_code == 401

    def test_app_jwt_is_accepted_and_carries_the_egeria_token(self):
        r = _client().get("/whoami",
                          headers={"Authorization": f"Bearer {_app_jwt()}"})
        assert r.status_code == 200
        body = r.json()
        assert body["user_id"] == "peterprofile"
        assert body["auth_source"] == "app-jwt"
        assert body["egeria_token_present"] is True

    def test_raw_egeria_token_is_accepted(self):
        token = _egeria_jwt()
        with patch("trellis_auth.validate_egeria_token", return_value=True) as v:
            r = _client().get("/whoami",
                              headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 200
        assert r.json()["auth_source"] == "egeria-token"
        assert r.json()["user_id"] == "garygeeke"
        assert v.call_count == 1

    def test_a_rejected_egeria_token_is_401(self):
        with patch("trellis_auth.validate_egeria_token", return_value=False):
            r = _client().get("/whoami",
                              headers={"Authorization": f"Bearer {_egeria_jwt()}"})
        assert r.status_code == 401

    def test_egeria_token_is_validated_once_and_cached(self):
        token = _egeria_jwt()
        client = _client()
        with patch("trellis_auth.validate_egeria_token", return_value=True) as v:
            for _ in range(4):
                assert client.get(
                    "/whoami", headers={"Authorization": f"Bearer {token}"}
                ).status_code == 200
        assert v.call_count == 1, (
            f"validated {v.call_count} times for one token — the cache is not "
            "holding, so every agent call now costs an Egeria round trip"
        )

    def test_a_different_token_is_validated_separately(self):
        client = _client()
        with patch("trellis_auth.validate_egeria_token", return_value=True) as v:
            client.get("/whoami", headers={"Authorization": f"Bearer {_egeria_jwt('a')}"})
            client.get("/whoami", headers={"Authorization": f"Bearer {_egeria_jwt('b')}"})
        assert v.call_count == 2

    def test_an_expired_egeria_token_is_not_cached_as_valid(self):
        """`validate_egeria_token` rejects an already-expired token locally, and
        a rejection is deliberately never cached — a token can go from invalid
        to valid (Egeria was down, access was just granted) and caching "no"
        would pin a transient failure for an hour."""
        token = _egeria_jwt(ttl=-10)
        client = _client()
        with patch("trellis_auth.validate_egeria_token", return_value=False) as v:
            client.get("/whoami", headers={"Authorization": f"Bearer {token}"})
            client.get("/whoami", headers={"Authorization": f"Bearer {token}"})
        assert v.call_count == 2


class TestAnonymousMode:
    def test_closed_by_default(self):
        assert _settings().allow_anonymous is False
        assert _client().get("/whoami").status_code == 401

    def test_open_only_with_the_env(self):
        client = _client(_settings(allow_anonymous=True))
        r = client.get("/whoami")
        assert r.status_code == 200
        assert r.json()["auth_source"] == "anonymous"
        assert r.json()["egeria_token_present"] is False

    def test_anonymous_mode_still_honours_a_presented_token(self):
        """Lowering the gate is not the same as discarding the credential —
        otherwise turning on local dev mode would silently un-attribute every
        Egeria write made by a caller who did authenticate."""
        client = _client(_settings(allow_anonymous=True))
        r = client.get("/whoami", headers={"Authorization": f"Bearer {_app_jwt()}"})
        assert r.json()["auth_source"] == "app-jwt"
        assert r.json()["user_id"] == "peterprofile"

    def test_env_var_is_what_sets_it(self, monkeypatch):
        from resource_explorer.a2a_auth import settings_from_env

        monkeypatch.setenv("A2A_ALLOW_ANONYMOUS", "true")
        assert settings_from_env().allow_anonymous is True
        monkeypatch.setenv("A2A_ALLOW_ANONYMOUS", "false")
        assert settings_from_env().allow_anonymous is False
        monkeypatch.delenv("A2A_ALLOW_ANONYMOUS")
        assert settings_from_env().allow_anonymous is False


# ── discovery ───────────────────────────────────────────────────────────


class TestDiscovery:
    def test_index_is_readable_without_a_token(self):
        assert _client().get("/.well-known/agents.json").status_code == 200

    def test_index_lists_every_agent_with_a_url_and_a_card(self):
        body = _client().get("/.well-known/agents.json").json()
        listed = {a["name"] for a in body["agents"]}
        assert listed == set(AGENT_NAMES), (
            f"index lists {listed}, server mounts {set(AGENT_NAMES)} — an agent "
            "missing here is an agent no external orchestrator can find"
        )
        for entry in body["agents"]:
            assert entry["url"].startswith("http")
            assert entry["card_url"].endswith("/.well-known/agent-card.json")
            assert entry["endpoints"]["rest_message_send"].endswith("/v1/message:send")

    def test_index_names_the_required_auth_scheme(self):
        body = _client().get("/.well-known/agents.json").json()
        auth = body["authentication"]
        assert auth["scheme"] == "Bearer"
        assert auth["required"] is True
        assert set(auth["accepts"]) == {"trellis-app-jwt", "egeria-bearer-token"}

    def test_index_reports_anonymous_mode_honestly(self):
        body = _client(_settings(allow_anonymous=True)).get(
            "/.well-known/agents.json").json()
        assert body["authentication"]["anonymous_allowed"] is True
        assert body["authentication"]["required"] is False

    def test_index_carries_re_version_and_the_egeria_binding(self):
        from resource_explorer import __version__

        body = _client().get("/.well-known/agents.json").json()
        assert body["version"] == __version__
        assert body["egeria"]["view_server"] == "qs-view-server"
        # The index reports the platform from RE's own config (get_config()), not from the
        # A2AAuthSettings handed to build_app, so the expected value is whatever the run
        # configures (EGERIA_PLATFORM_URL); a hard-coded localhost only held on a bare machine.
        from resource_explorer.config import get_config

        assert body["egeria"]["platform_url"] == get_config().egeria.platform_url
        assert body["egeria"]["platform_url"]

    def test_exactly_one_default_agent_and_it_is_the_orchestrator(self):
        body = _client().get("/.well-known/agents.json").json()
        defaults = [a["name"] for a in body["agents"] if a["default"]]
        assert defaults == ["orchestrator"]
        assert body["default_agent"] == "orchestrator"

    def test_cards_are_public_but_agent_endpoints_are_not(self):
        client = _client()
        assert client.get("/agents/docs/.well-known/agent-card.json").status_code == 200
        assert client.post("/agents/docs/v1/message:send", json={}).status_code == 401


# ── the CLI wiring ──────────────────────────────────────────────────────


class TestServeCommand:
    def _run(self, args):
        from typer.testing import CliRunner

        from resource_explorer.cli.main import app as cli

        with patch("resource_explorer.a2a_role.run_a2a") as run:
            result = CliRunner().invoke(cli, ["serve", *args])
        return result, run

    def test_base_port_is_accepted_but_warns(self):
        result, run = self._run(["--base-port", "8080"])
        assert result.exit_code == 0, result.output
        assert "deprecated" in result.output.lower()
        assert run.call_args.kwargs["port"] == 8080

    def test_port_is_the_supported_spelling(self):
        result, run = self._run(["--port", "8090"])
        assert result.exit_code == 0, result.output
        assert "deprecated" not in result.output.lower()
        assert run.call_args.kwargs["port"] == 8090

    def test_all_is_accepted_but_ignored(self):
        result, run = self._run(["--all"])
        assert result.exit_code == 0, result.output
        assert "deprecated" in result.output.lower()
        assert run.call_count == 1

    def test_default_port_is_the_single_a2a_port(self):
        result, run = self._run([])
        assert result.exit_code == 0, result.output
        assert run.call_args.kwargs["port"] == DEFAULT_A2A_PORT


class TestCallerPropagation:
    def test_the_factory_uses_the_a2a_callers_token(self):
        """The downstream half of §4, through Brief I's one factory: a client built for the
        A2A caller carries the *caller's* token, not the service account's."""
        from resource_explorer.a2a_auth import CallerIdentity, current_caller
        from resource_explorer.egeria_clients import Caller, egeria_client

        class FakeClient:
            def __init__(self, *a):
                self.user = a[2]
                self.tokens = []
                self.minted = 0

            def set_bearer_token(self, t):
                self.tokens.append(t)

            def create_egeria_bearer_token(self, *a):
                self.minted += 1

        reset = current_caller.set(
            CallerIdentity(user_id="peterprofile", egeria_token="tok-123",
                           auth_source="app-jwt")
        )
        try:
            client = egeria_client(Caller(), purpose="test").of(FakeClient)
        finally:
            current_caller.reset(reset)
        assert (client.user, client.tokens, client.minted) == ("peterprofile", ["tok-123"], 0)

    def test_with_no_caller_the_factory_refuses_rather_than_using_the_service_account(self):
        import pytest

        from resource_explorer.egeria_clients import Caller, NoCallerIdentity

        with pytest.raises(NoCallerIdentity):
            Caller()
