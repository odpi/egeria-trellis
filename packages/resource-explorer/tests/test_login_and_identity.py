"""RE's trellis-auth adoption: login, identity, ownership, zones, scoping.

`docs/runtime-architecture-plan.md` §4, the RE side. What each group pins, and
why it is worth pinning:

* **Middleware** — a 401 on a protected route and a pass on a public one. The
  allowlist is the whole policy; a bug here fails *open* and nothing else
  notices.
* **Login / Portal routes** — that the password never leaves the login call and
  that the app JWT carries the Egeria token rather than a password.
* **CLI** — that `login` writes a 0600 session with no password in it, and that
  a gated command exits **2**, not 1, with one line.
* **Per-request client** — that a publish authenticates with the *caller's*
  token, not the service account's credentials. This is the whole point of the
  change and is invisible without a mock: a service-account publish and a
  per-user publish both succeed.
* **Ownership / ZoneMembership** — the actual pyegeria calls and their bodies.
* **Curate authorization** — owner allowed, curator allowed, stranger 403.
* **Promotion** — accept moves the element out of the draft zone.
* **user_id scoping** — two users, three tables, no bleed; and the `''`
  fallback that keeps pre-existing rows readable.
* **Fairness** — real ids are subject to it, `''` still is not.
"""
from __future__ import annotations

import json
import stat

import jwt
import pytest
from fastapi.testclient import TestClient

from resource_explorer.registry import Project, ProjectRegistry


SECRET = "test-re-secret"


@pytest.fixture(autouse=True)
def _auth_env(monkeypatch):
    """A known JWT secret, and a policy cache that does not survive a test."""
    monkeypatch.setenv("RE_JWT_SECRET", SECRET)
    monkeypatch.setenv("RE_PORTAL_SECRET", "test-portal-secret")
    monkeypatch.delenv("TRELLIS_ANONYMOUS_READ", raising=False)
    monkeypatch.delenv("EXPLORER_ANONYMOUS_READ", raising=False)
    monkeypatch.delenv("TRELLIS_REQUIRE_LOGIN", raising=False)
    from resource_explorer import auth as re_auth

    # reset_auth_secrets_cache() alongside reset_policy_cache(): jwt_secret()/
    # portal_secret() now read through a cached BaseSettings instance (so
    # `.env` works — see auth.py's _AuthSecretsConfig), and without dropping
    # that cache here every test after the first would see whichever
    # RE_JWT_SECRET/RE_PORTAL_SECRET the first test happened to set.
    re_auth.reset_policy_cache()
    re_auth.reset_auth_secrets_cache()
    yield
    re_auth.reset_policy_cache()
    re_auth.reset_auth_secrets_cache()


def _app_under(monkeypatch, **env):
    """A freshly-built FastAPI app whose middleware read `env`.

    `LoginRequiredMiddleware` resolves the policy **once**, when it is
    constructed at `web/app.py` import — deliberately, so the middleware, the
    `/api/auth/policy` route and any handler cannot disagree about the request
    already in flight. The consequence for tests is that setting an env var is
    not enough: the module has to be re-imported under it.

    `tests/conftest.py` turns the gate OFF for the suite (the ~300 route tests
    predate it), so every test here that is actually about the gate has to turn
    it back on for its own app object rather than inheriting one.
    """
    import importlib

    for key, value in env.items():
        monkeypatch.setenv(key, value)
    from resource_explorer import auth as re_auth

    re_auth.reset_policy_cache()
    re_auth.reset_auth_secrets_cache()
    import resource_explorer.web.app as web_app

    return importlib.reload(web_app).app


@pytest.fixture(scope="module", autouse=True)
def _restore_shared_app():
    """Put `web/app.py` back the way the rest of the suite expects it.

    `_app_under` reloads the module to get an app whose middleware read a
    different policy, and `monkeypatch` restores the *environment* but not the
    *module* — so without this the reloaded, login-required `app` object stays
    in `sys.modules` and every later route test in the session 401s. Found
    exactly that way: 273 failures in modules that have nothing to do with
    auth.

    Module-scoped so it runs after every function-scoped `monkeypatch` has
    already undone its `setenv`, which is what makes the final reload pick up
    conftest's suite default rather than this module's override.
    """
    yield
    import importlib

    from resource_explorer import auth as re_auth
    import resource_explorer.web.app as web_app

    re_auth.reset_policy_cache()
    re_auth.reset_auth_secrets_cache()
    importlib.reload(web_app)


@pytest.fixture(autouse=True)
def _no_caller():
    """No identity leaks between tests.

    The identity is a ContextVar, and a test that sets one and returns without
    resetting it makes the *next* test pass for the wrong reason — the failure
    mode that makes a ContextVar-based identity worth guarding in the fixture
    rather than per test.
    """
    from resource_explorer.a2a_auth import current_caller

    token = current_caller.set(None)
    yield
    current_caller.reset(token)


def _app_token(user_id: str = "peterprofile", *, role: str = "user",
               egeria_token: str = "egeria-token-abc") -> str:
    from resource_explorer.auth import create_access_token

    return create_access_token(user_id=user_id, egeria_token=egeria_token, role=role)


# ---------------------------------------------------------------------------
# 1. The login gate
# ---------------------------------------------------------------------------


class TestLoginRequiredMiddleware:
    @pytest.fixture()
    def client(self, monkeypatch):
        return TestClient(_app_under(monkeypatch, EXPLORER_REQUIRE_LOGIN="true"))

    def test_protected_route_401s_without_a_token(self, client):
        r = client.get("/api/projects/")
        assert r.status_code == 401
        # A well-formed 401 names the scheme; without this a CLI or A2A client
        # cannot tell "sign in" from "you are forbidden".
        assert r.headers.get("www-authenticate") == "Bearer"
        assert r.json()["error"] == "login_required"

    def test_protected_route_passes_with_a_valid_token(self, client):
        r = client.get("/api/projects/", headers={"Authorization": f"Bearer {_app_token()}"})
        assert r.status_code != 401

    def test_an_invalid_token_is_not_a_session(self, client):
        r = client.get("/api/projects/", headers={"Authorization": "Bearer not-a-jwt"})
        assert r.status_code == 401

    def test_a_token_signed_with_another_secret_is_rejected(self, client):
        forged = jwt.encode({"sub": "mallory", "exp": 9999999999}, "wrong-secret",
                            algorithm="HS256")
        r = client.get("/api/projects/", headers={"Authorization": f"Bearer {forged}"})
        assert r.status_code == 401

    @pytest.mark.parametrize("path", [
        "/health", "/health/ready", "/", "/api/auth/policy", "/api/auth/defaults",
        "/api/auth/me",
    ])
    def test_public_paths_are_reachable_without_a_token(self, client, path):
        assert client.get(path).status_code != 401

    def test_well_known_is_public_so_a2a_cards_can_be_discovered(self, client):
        """An A2A surface whose cards sit behind the credential they describe
        cannot be discovered at all — the plan says so, and the shared policy
        allowlists `/.well-known/` for exactly this. RE's own app serves no
        card at this path (the A2A role mounts its own app), so the assertion
        is that it is not a 401 rather than that it is a 200."""
        assert client.get("/.well-known/agents.json").status_code != 401

    def test_a_cross_origin_401_still_carries_its_cors_headers(self, client):
        """CORS must be OUTSIDE the login gate.

        Ordering was measured rather than assumed (see
        `_install_login_required_middleware`): the two readings are one
        `reversed()` apart, and the wrong one leaves the 401 undecorated. A
        browser then reports an opaque network error instead of "please sign
        in", and the login form never appears — the exact broken page this
        whole change exists to prevent, reintroduced by a middleware ordering
        nobody would think to look at.
        """
        r = client.get("/api/projects/", headers={"Origin": "http://localhost:5173"})
        assert r.status_code == 401
        assert r.headers.get("access-control-allow-origin")


class TestAnonymousReadOverride:
    def test_anonymous_read_lets_a_get_through_and_still_401s_a_write(self, monkeypatch):
        """The dev-only override lowers the gate for reads and NOT for writes.

        That asymmetry is the whole point: a read-only anonymous user cannot
        create anything, so nothing lands in Egeria or in a namespace with no
        identity behind it — the 2026-08-29 ownership decision survives the
        override.
        """
        client = TestClient(_app_under(
            monkeypatch, EXPLORER_REQUIRE_LOGIN="true", EXPLORER_ANONYMOUS_READ="true",
        ))
        assert client.get("/api/projects/").status_code != 401
        assert client.post("/api/projects/", json={}).status_code == 401


# ---------------------------------------------------------------------------
# 2. Login and Portal exchange
# ---------------------------------------------------------------------------


class TestAuthRoutes:
    @pytest.fixture()
    def client(self, monkeypatch):
        return TestClient(_app_under(monkeypatch, EXPLORER_REQUIRE_LOGIN="true"))

    def test_login_exchanges_the_password_once_and_returns_an_app_jwt(
        self, client, monkeypatch,
    ):
        seen = {}

        def _fake_login(user_id, password):
            seen["user_id"] = user_id
            seen["password"] = password
            return _egeria_token("peterprofile")

        import resource_explorer.auth as re_auth
        monkeypatch.setattr(re_auth, "login_with_password", _fake_login)

        r = client.post("/api/auth/login",
                        json={"username": "peterprofile", "password": "hunter2"})
        assert r.status_code == 200
        body = r.json()
        assert body["egeria_user"] == "peterprofile"
        claims = jwt.decode(body["access_token"], SECRET, algorithms=["HS256"])
        # The contract in one assertion: the Egeria token rides in the app JWT,
        # and nothing password-shaped does.
        assert claims["egeria_token"] == _egeria_token("peterprofile")
        assert "password" not in claims and "egeria_password" not in claims
        assert seen == {"user_id": "peterprofile", "password": "hunter2"}

    def test_bad_credentials_are_a_401_not_a_500(self, client, monkeypatch):
        import resource_explorer.auth as re_auth
        monkeypatch.setattr(re_auth, "login_with_password", lambda u, p: None)
        r = client.post("/api/auth/login", json={"username": "x", "password": "y"})
        assert r.status_code == 401

    def test_portal_exchange_accepts_the_payload_the_portal_really_issues(
        self, client, monkeypatch,
    ):
        """`{sub, role, display_name, egeria_token, exp}` — not the retired
        `{egeria_user, egeria_password}` shape nothing ever produced."""
        import resource_explorer.auth as re_auth
        monkeypatch.setattr(re_auth, "validate_egeria_token", lambda t: True)

        portal_token = jwt.encode(
            {
                "sub": "garygeeke",
                "role": "curator",
                "display_name": "Gary Geeke",
                "egeria_token": _egeria_token("garygeeke"),
                "exp": 9999999999,
            },
            "test-portal-secret", algorithm="HS256",
        )
        r = client.post("/api/auth/portal", json={"portal_token": portal_token})
        assert r.status_code == 200
        claims = jwt.decode(r.json()["access_token"], SECRET, algorithms=["HS256"])
        assert claims["user_id"] == "garygeeke"
        assert claims["role"] == "curator"
        assert claims["egeria_token"] == _egeria_token("garygeeke")

    def test_portal_rejects_the_retired_password_contract_by_name(self, client):
        portal_token = jwt.encode(
            {"egeria_user": "u", "egeria_password": "p", "exp": 9999999999},
            "test-portal-secret", algorithm="HS256",
        )
        r = client.post("/api/auth/portal", json={"portal_token": portal_token})
        assert r.status_code == 400
        assert "retired password contract" in r.json()["detail"]

    def test_me_reports_the_signed_in_user(self, client):
        r = client.get("/api/auth/me",
                       headers={"Authorization": f"Bearer {_app_token('dan')}"})
        assert r.json() == {
            "authenticated": True, "user_id": "dan", "egeria_user": "dan",
            "role": "user", "display_name": "dan",
        }

    def test_me_is_public_and_says_so_when_nobody_is_signed_in(self, client):
        assert client.get("/api/auth/me").json() == {"authenticated": False}

    def test_defaults_never_returns_a_password(self, client):
        body = client.get("/api/auth/defaults").json()
        assert "username" in body
        assert not any("pass" in k.lower() for k in body)


def _egeria_token(sub: str) -> str:
    """An Egeria-shaped RS256-style bearer token (HS256 here — never verified).

    `egeria_token_expiry` decodes without verifying, deliberately, so the
    algorithm is irrelevant; what matters is that it carries `sub` and `exp`.
    """
    return jwt.encode({"iss": "self", "sub": sub, "exp": 9999999999}, "irrelevant",
                      algorithm="HS256")


# ---------------------------------------------------------------------------
# 3. The CLI's cached login
# ---------------------------------------------------------------------------


class TestCliSession:
    @pytest.fixture(autouse=True)
    def _config_home(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    def test_login_writes_a_0600_session_with_no_password_in_it(self):
        from resource_explorer.cli import session as cli_session

        record = cli_session.save_login("peterprofile", _egeria_token("peterprofile"))
        assert record.path.exists()
        assert stat.S_IMODE(record.path.stat().st_mode) == 0o600
        raw = record.path.read_text()
        assert "hunter2" not in raw
        stored = json.loads(raw)
        assert set(stored) == {"app", "user_id", "token", "expires_at"}
        assert stored["app"] == "resource-explorer"
        # The cached artifact is the app JWT — the same thing the browser's
        # Authorization header carries, so there is one format and one expiry.
        claims = jwt.decode(stored["token"], SECRET, algorithms=["HS256"])
        assert claims["egeria_token"] == _egeria_token("peterprofile")

    def test_logout_removes_it_and_is_idempotent(self):
        from resource_explorer.cli import session as cli_session

        cli_session.save_login("dan", _egeria_token("dan"))
        assert cli_session.clear_session() is True
        assert cli_session.clear_session() is False

    def test_require_identity_exits_2_with_one_line_when_not_signed_in(self, capsys):
        from resource_explorer.cli import session as cli_session

        class _Console:
            def __init__(self): self.lines = []
            def print(self, text): self.lines.append(text)

        console = _Console()
        with pytest.raises(SystemExit) as exc:
            cli_session.require_identity(console)
        # 2, not 1: "you are not signed in" is a different outcome from "the
        # command ran and failed", and a wrapping script must be able to tell
        # them apart without parsing the message.
        assert exc.value.code == 2
        assert len(console.lines) == 1
        assert "resource-explorer login" in console.lines[0]

    def test_an_expired_session_exits_2_and_says_when_it_lapsed(self):
        from trellis_auth import session_file

        from resource_explorer.auth import create_access_token
        from resource_explorer.cli import session as cli_session

        token = create_access_token("dan", _egeria_token("dan"))
        session_file.save_session("resource-explorer", "dan", token, expires_at=1)

        class _Console:
            def __init__(self): self.lines = []
            def print(self, text): self.lines.append(text)

        console = _Console()
        with pytest.raises(SystemExit) as exc:
            cli_session.require_identity(console)
        assert exc.value.code == 2
        assert "expired at" in console.lines[0]

    def test_resolve_identity_never_fails_for_a_read_only_command(self):
        from resource_explorer.cli import session as cli_session

        assert cli_session.resolve_identity() == (None, None)
        assert cli_session.resolve_identity("dan") == ("dan", None)

    def test_activate_publishes_the_caller_the_rest_of_re_reads(self):
        from resource_explorer.a2a_auth import caller
        from resource_explorer.cli import session as cli_session
        from resource_explorer.run_queue import requested_by

        cli_session.save_login("peterprofile", _egeria_token("peterprofile"))
        _, identity = cli_session.resolve_identity()
        cli_session.activate(identity)
        try:
            assert caller().user_id == "peterprofile"
            # The point of one identity mechanism: the run queue reads the same
            # thing the publisher does, with nothing threaded through.
            assert requested_by() == "peterprofile"
        finally:
            cli_session.activate(None)


# ---------------------------------------------------------------------------
# 4. The per-request Egeria client
# ---------------------------------------------------------------------------


class _FakeClient:
    def __init__(self, *args, **kwargs):
        self.args = args
        self.bearer = None
        self.minted = False

    def set_bearer_token(self, token):
        self.bearer = token

    def create_egeria_bearer_token(self, *args, **kwargs):
        self.minted = True


class TestPerRequestClient:
    def test_a_signed_in_caller_reuses_their_own_token(self):
        from resource_explorer.a2a_auth import CallerIdentity, current_caller
        from resource_explorer.egeria_clients import egeria_client
        from resource_explorer.egeria_identity import caller_credentials

        reset = current_caller.set(
            CallerIdentity(user_id="dan", egeria_token="dan-token", auth_source="app-jwt")
        )
        try:
            identity = caller_credentials()
            assert identity.user_id == "dan"
            client = egeria_client(identity, purpose="test").of(_FakeClient)
            # The whole change in one assertion: Egeria sees Dan's credential,
            # so Egeria's own provenance records Dan.
            assert client.bearer == "dan-token"
            assert client.minted is False
        finally:
            current_caller.reset(reset)

    def test_the_worker_role_still_mints_its_own_service_account_token(self):
        from resource_explorer.egeria_clients import Daemon, DaemonReason, egeria_client

        client = egeria_client(Daemon(DaemonReason.SCHEDULER), purpose="test").of(_FakeClient)
        assert client.bearer is None
        assert client.minted is True

    def test_no_caller_refuses_rather_than_falling_back_to_the_service_account(self):
        """Brief I: nothing falls back. With no caller and no declared daemon job, it is a 401."""
        from resource_explorer.egeria_clients import NoCallerIdentity
        from resource_explorer.egeria_identity import caller_credentials

        with pytest.raises(NoCallerIdentity) as exc:
            caller_credentials()
        assert "sign in" in str(exc.value)

    def test_a_queued_run_owns_as_the_requester_but_authenticates_as_the_worker(self):
        """The documented interim shape. A token does not survive the queue
        (one-hour lifetime, dies on platform restart), so the daemon
        authenticates as itself and `Ownership` carries the requester."""
        from resource_explorer.egeria_clients import Daemon, DaemonReason, egeria_client

        identity = Daemon(DaemonReason.RUN_QUEUE, requested_by="dan")
        assert identity.user_id == "dan"          # what Ownership will say
        assert identity.is_person is False        # how the call authenticates
        client = egeria_client(identity, purpose="test").of(_FakeClient)
        assert client.minted is True

    def test_the_publisher_builds_its_clients_from_the_caller(self, monkeypatch):
        from resource_explorer.a2a_auth import CallerIdentity, current_caller
        from resource_explorer.surveyors.egeria_publisher import EgeriaPublisher

        built = []

        class _Recording(_FakeClient):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                built.append(self)

        fake_pyegeria = type("m", (), {
            "AssetMaker": _Recording, "AutomatedCuration": _Recording,
            "CollectionManager": _Recording, "ExternalReferences": _Recording,
        })
        monkeypatch.setitem(__import__("sys").modules, "pyegeria", fake_pyegeria)
        monkeypatch.setitem(__import__("sys").modules, "pyegeria.omvs.data_discovery",
                            type("m", (), {"DataDiscovery": _Recording}))
        monkeypatch.setitem(__import__("sys").modules, "pyegeria.omvs.metadata_expert",
                            type("m", (), {"MetadataExpert": _Recording}))

        reset = current_caller.set(
            CallerIdentity(user_id="dan", egeria_token="dan-token", auth_source="app-jwt")
        )
        try:
            publisher = EgeriaPublisher(platform_url="https://localhost:9443")
            publisher._connect()
        finally:
            current_caller.reset(reset)

        assert built, "no pyegeria clients were constructed"
        assert all(c.bearer == "dan-token" for c in built)
        assert not any(c.minted for c in built)
        # The client's own user id is the person's too, so a pyegeria call that
        # reads it (rather than the token) is attributed the same way.
        assert publisher.user_id == "dan"


# ---------------------------------------------------------------------------
# 5. Ownership and ZoneMembership
# ---------------------------------------------------------------------------


class _ClassificationSpy:
    def __init__(self):
        self.ownership = []
        self.zones = []

    def add_ownership_to_element(self, guid, body):
        self.ownership.append((guid, body))

    def add_zone_membership(self, guid, body):
        self.zones.append((guid, body))


class TestOwnershipAndZones:
    def test_ownership_body_matches_the_class_pyegeria_actually_validates(self):
        """`OwnershipProperties`, NOT the `OwnerProperties` in pyegeria's own
        docstring — `add_ownership_to_element` passes
        `prop=["OwnershipProperties"]` and rejects anything else before a
        request is made. The docstring is wrong; this pins the code's answer,
        which is what the server enforces. Logged upstream."""
        from resource_explorer.egeria_identity import ownership_body

        body = ownership_body("dan")
        assert body["class"] == "NewClassificationRequestBody"
        assert body["properties"] == {
            "class": "OwnershipProperties",
            "owner": "dan",
            "ownerTypeName": "UserIdentity",
            "ownerPropertyName": "userId",
        }

    def test_zone_body_matches_pyegerias_own_contract(self):
        from resource_explorer.egeria_identity import zone_membership_body

        body = zone_membership_body(["resource-explorer-draft"])
        assert body["properties"] == {
            "class": "ZoneMembershipProperties",
            "zoneMembership": ["resource-explorer-draft"],
        }

    def test_stamp_published_sets_both_on_one_client(self, monkeypatch):
        from resource_explorer.egeria_identity import stamp_published

        monkeypatch.setenv("EXPLORER_DRAFT_ZONE", "resource-explorer-draft")   # the configured case
        spy = _ClassificationSpy()
        out = stamp_published("guid-1", "dan", client=spy)
        assert out["ownership"] is True and out["zone_membership"] is True
        assert spy.ownership[0][0] == "guid-1"
        assert spy.ownership[0][1]["properties"]["owner"] == "dan"
        assert spy.zones[0][1]["properties"]["zoneMembership"] == ["resource-explorer-draft"]

    def test_a_classification_failure_is_reported_not_raised(self):
        """A publish that produced real elements must not be reported as a
        failure because one classification call did not land — but it must not
        be reported as a success either."""
        from resource_explorer.egeria_identity import set_ownership

        class _Broken:
            def add_ownership_to_element(self, guid, body):
                raise RuntimeError("Egeria said no")

        assert set_ownership("g", "dan", client=_Broken()) is False

    def test_publish_stamps_ownership_and_the_draft_zone(self, monkeypatch):
        from resource_explorer.egeria_identity import EgeriaIdentity
        from resource_explorer.surveyors.egeria_publisher import EgeriaPublisher

        spy = _ClassificationSpy()
        publisher = EgeriaPublisher(
            platform_url="https://localhost:9443",
            identity=EgeriaIdentity(user_id="dan", token="dan-token"),
        )
        monkeypatch.setattr(
            "resource_explorer.egeria_identity.classification_client", lambda i=None: spy,
        )
        publisher._stamp_governance("asset-guid", "report-guid")

        assert [g for g, _ in spy.ownership] == ["asset-guid", "report-guid"]
        assert all(b["properties"]["owner"] == "dan" for _, b in spy.ownership)
        # Default (2026-10-08): no draft zone, so NO ZoneMembership call at all.
        assert spy.zones == []

    def test_publish_stamps_a_configured_draft_zone(self, monkeypatch):
        from resource_explorer.egeria_identity import EgeriaIdentity
        from resource_explorer.surveyors.egeria_publisher import EgeriaPublisher

        monkeypatch.setenv("EXPLORER_DRAFT_ZONE", "resource-explorer-draft")
        spy = _ClassificationSpy()
        publisher = EgeriaPublisher(
            platform_url="https://localhost:9443",
            identity=EgeriaIdentity(user_id="dan", token="dan-token"),
        )
        monkeypatch.setattr(
            "resource_explorer.egeria_identity.classification_client", lambda i=None: spy,
        )
        publisher._stamp_governance("asset-guid", "report-guid")
        assert [b["properties"]["zoneMembership"] for _, b in spy.zones] == [
            ["resource-explorer-draft"]] * 2

    def test_the_draft_zone_and_publish_zones_are_configurable(self, monkeypatch):
        from resource_explorer.egeria_identity import configured_publish_zones, draft_zone

        monkeypatch.delenv("EXPLORER_DRAFT_ZONE", raising=False)
        assert draft_zone() is None                    # no default draft zone (2026-10-08)
        # Configured-only (owner, 2026-10-07): with nothing configured there is NO publish zone.
        monkeypatch.delenv("EXPLORER_PUBLISH_ZONES", raising=False)
        monkeypatch.setattr(
            "resource_explorer.config.get_config",
            lambda: type("c", (), {"egeria": type("e", (), {"default_catalog_zones": []})()})(),
        )
        assert configured_publish_zones() == []

        monkeypatch.setenv("EXPLORER_DRAFT_ZONE", "re-draft-2")
        monkeypatch.setenv("EXPLORER_PUBLISH_ZONES", "alpha, beta")
        assert draft_zone() == "re-draft-2"
        assert configured_publish_zones() == ["alpha", "beta"]


# ---------------------------------------------------------------------------
# 6. Curate authorization and promotion
# ---------------------------------------------------------------------------


class TestCurateAuthorization:
    """Brief Z (2026-10-10): the old "owner = last decided_by" gate is gone; access follows Egeria's zones.
    The full matrix is tests/test_curation_access_by_zone.py; these pin the entry points' shape."""

    @staticmethod
    def _as(user_id, role="user"):
        from resource_explorer.a2a_auth import CallerIdentity, current_caller

        return current_caller.set(
            CallerIdentity(user_id=user_id, egeria_token="t", auth_source="app-jwt",
                           role=role)
        )

    @pytest.fixture()
    def reg(self, tmp_path, monkeypatch):
        monkeypatch.delenv("EXPLORER_DRAFT_ZONE", raising=False)
        monkeypatch.setattr("resource_explorer.egeria_identity.configured_publish_zones", lambda: [])
        r = ProjectRegistry(db_path=str(tmp_path / "auth.db"))
        r.add(Project(slug="p", display_name="p", github_url="https://github.com/o/p"))
        return r

    def test_someone_elses_decision_no_longer_locks_an_element(self, reg):
        from resource_explorer.a2a_auth import current_caller
        from resource_explorer.workflows.curate import may_curate

        reg.record_component_verdict("repo", "p", "src/a", "accepted", "", "", decided_by="dan")
        reset = self._as("mallory")
        try:
            assert may_curate(reg, "repo", "p", "src/a") == (True, "")
        finally:
            current_caller.reset(reset)

    def test_nobody_signed_in_may_not(self, reg):
        from resource_explorer.workflows.curate import may_curate

        allowed, why = may_curate(reg, "repo", "p", "src/a")
        assert allowed is False
        assert "sign in" in why

    def test_require_curation_rights_raises_the_type_routes_turn_into_403(self, reg):
        from resource_explorer.workflows.curate import CurationDenied, require_curation_rights

        with pytest.raises(CurationDenied):
            require_curation_rights(reg, "repo", "p", "src/a")


class TestCurateRoutes:
    @pytest.fixture()
    def client(self, tmp_path, monkeypatch):
        db_url = f"sqlite:///{tmp_path/'curate.db'}"
        monkeypatch.setenv("REGISTRY_DATABASE_URL", db_url)
        reg = ProjectRegistry(database_url=db_url)
        reg._init_schema()
        reg.add(Project(slug="p", display_name="p", github_url="https://github.com/o/p"))

        import resource_explorer.web.routes.curate as curate_routes
        monkeypatch.setattr(curate_routes, "_registry",
                            lambda: ProjectRegistry(database_url=db_url))
        # The verdict routes are decisions only (Brief A): no Egeria write to stub here.

        app = _app_under(monkeypatch, EXPLORER_REQUIRE_LOGIN="true")
        return TestClient(app), ProjectRegistry(database_url=db_url)

    def test_the_first_verdict_records_its_author_as_the_owner(self, client):
        api, reg = client
        r = api.post("/api/projects/p/components/verdicts",
                     json={"scope_locators": ["src/a"], "verdict": "accepted"},
                     headers={"Authorization": f"Bearer {_app_token('dan')}"})
        assert r.status_code == 200
        assert r.json()["verdicts"][0]["decided_by"] == "dan"

    def test_another_person_may_re_decide_when_zones_are_not_in_use(self, client, monkeypatch):
        """Brief Z: `decided_by` is attribution, not ownership. With no zone anywhere, it is open."""
        monkeypatch.delenv("EXPLORER_DRAFT_ZONE", raising=False)
        monkeypatch.setattr("resource_explorer.egeria_identity.configured_publish_zones", lambda: [])
        api, reg = client
        api.post("/api/projects/p/components/verdicts",
                 json={"scope_locators": ["src/a"], "verdict": "accepted"},
                 headers={"Authorization": f"Bearer {_app_token('dan')}"})
        r = api.post("/api/projects/p/components/verdicts",
                     json={"scope_locators": ["src/a"], "verdict": "rejected"},
                     headers={"Authorization": f"Bearer {_app_token('mallory')}"})
        assert r.status_code == 200
        assert r.json()["verdicts"][0]["decided_by"] == "mallory"

    def test_a_curator_role_gets_through(self, client):
        api, reg = client
        api.post("/api/projects/p/components/verdicts",
                 json={"scope_locators": ["src/a"], "verdict": "accepted"},
                 headers={"Authorization": f"Bearer {_app_token('dan')}"})
        r = api.post("/api/projects/p/components/verdicts",
                     json={"scope_locators": ["src/a"], "verdict": "rejected"},
                     headers={"Authorization": f"Bearer {_app_token('erin', role='curator')}"})
        assert r.status_code == 200

    def test_accept_does_not_promote_anything_publish_does(self, client, monkeypatch):
        """Accept is a decision only (Brief A follow-up 1): no materialization and no zone change from the
        verdict route. The promotion now happens in Publish (architecture_publish._publish_component)."""
        api, reg = client
        monkeypatch.setenv("EXPLORER_DRAFT_ZONE", "resource-explorer-draft")
        # Zones are configured, so the verdict asks Egeria about them (Brief Z): neither zone has a control.
        monkeypatch.setattr("resource_explorer.zone_access._platform", lambda: ("plat", "g"))
        monkeypatch.setattr("resource_explorer.zone_access._security_officer",
                            lambda: type("SO", (), {"get_security_access_control": lambda self, *a, **k: None,
                                                    "get_user_account": lambda self, *a, **k: {
                                                        "userAccountStatus": "AVAILABLE"}})())
        spy = _ClassificationSpy()
        monkeypatch.setattr("resource_explorer.egeria_identity.classification_client", lambda i=None: spy)
        monkeypatch.setenv("EXPLORER_PUBLISH_ZONES", "egeria-runtime")
        r = api.post("/api/projects/p/components/verdicts",
                     json={"scope_locators": ["src/a"], "verdict": "accepted"},
                     headers={"Authorization": f"Bearer {_app_token('dan')}"})
        assert r.status_code == 200
        assert "promotion" not in r.json() and "materialization" not in r.json()
        assert spy.zones == []

    def test_an_already_promoted_element_is_not_promoted_again(self, monkeypatch):
        """A no-op zone change is an ERROR in Egeria, not a nothing.

        Observed live 2026-09-04: `OMAG-SERVER-SECURITY-403-005 User
        erinoverview is not authorized to change the zone membership for
        element ... from [egeria-runtime] to [egeria-runtime]`. Re-accepting an
        already-accepted element is an ordinary thing to do, so without this
        check "already done" reaches the user as a permissions failure.
        """
        from resource_explorer.workflows import curate as curate_wf

        monkeypatch.setenv("EXPLORER_PUBLISH_ZONES", "egeria-runtime")
        monkeypatch.setattr(
            "resource_explorer.egeria_identity.current_zones",
            lambda guid, identity=None: ["egeria-runtime"],
        )
        called = []
        monkeypatch.setattr(
            "resource_explorer.egeria_identity.set_zone_membership",
            lambda *a, **k: called.append(a) or True,
        )
        out = curate_wf.promote_to_publish_zones("g")
        assert out["status"] == "already_promoted"
        assert called == []

    def test_an_unreadable_current_zone_still_attempts_the_promotion(self, monkeypatch):
        """`current_zones` returns `[]` for "could not tell", and that must not
        be read as "in no zones, nothing to do" — an unreachable Egeria would
        then silently skip every promotion."""
        from resource_explorer.workflows import curate as curate_wf
        from tests.zone_fakes import zone_controls_grant_all

        zone_controls_grant_all(monkeypatch)      # the zone's control admits everyone (start-up zone check)
        monkeypatch.setenv("EXPLORER_PUBLISH_ZONES", "egeria-runtime")
        monkeypatch.setattr(
            "resource_explorer.egeria_identity.current_zones",
            lambda guid, identity=None: [],
        )
        monkeypatch.setattr(
            "resource_explorer.egeria_identity.set_zone_membership",
            lambda *a, **k: True,
        )
        monkeypatch.setattr("resource_explorer.egeria_identity.read_zones",
                            lambda guid, identity=None: ["egeria-runtime"])
        assert curate_wf.promote_to_publish_zones("g")["status"] == "promoted"

    def test_reject_does_not_promote_anything(self, client, monkeypatch):
        api, reg = client
        r = api.post("/api/curate/component-verdicts/repo/p",
                     json={"scope_locator": "src/b", "verdict": "rejected"},
                     headers={"Authorization": f"Bearer {_app_token('dan')}"})
        assert "promotion" not in r.json()


# ---------------------------------------------------------------------------
# 7. user_id scoping
# ---------------------------------------------------------------------------


@pytest.fixture()
def reg(tmp_path):
    r = ProjectRegistry(database_url=f"sqlite:///{tmp_path/'scoped.db'}")
    r._init_schema()
    return r


class TestUserScoping:
    def test_working_set_is_per_user(self, reg):
        reg.set_working_set_hidden("repo", "x", True, user_id="alice")
        reg.set_working_set_hidden("repo", "x", False, user_id="bob")
        assert reg.is_working_set_hidden("repo", "x", user_id="alice") is True
        assert reg.is_working_set_hidden("repo", "x", user_id="bob") is False
        assert reg.is_working_set_hidden("repo", "x", user_id="carol") is False

    def test_working_set_falls_back_to_the_shared_bucket(self, reg):
        """A row written before scoping existed must stay visible to whoever
        set it, rather than every hidden resource silently reappearing the
        first time they sign in."""
        reg.set_working_set_hidden("repo", "legacy", True, user_id="")
        assert reg.is_working_set_hidden("repo", "legacy", user_id="alice") is True
        reg.set_working_set_hidden("repo", "legacy", False, user_id="alice")
        assert reg.is_working_set_hidden("repo", "legacy", user_id="alice") is False
        assert reg.is_working_set_hidden("repo", "legacy", user_id="bob") is True

    def test_project_context_is_per_user(self, reg):
        reg.set_project_context("repo", "y", "assigned",
                                egeria_project_guid="alice-guid", user_id="alice")
        reg.set_project_context("repo", "y", "assigned",
                                egeria_project_guid="bob-guid", user_id="bob")
        assert reg.get_project_context("repo", "y", user_id="alice")["egeria_project_guid"] == "alice-guid"
        assert reg.get_project_context("repo", "y", user_id="bob")["egeria_project_guid"] == "bob-guid"
        assert reg.get_project_context("repo", "y", user_id="carol") is None

    def test_conversation_history_is_read_only_by_its_owner(self, reg):
        reg.append_turn("s1", "user", "alice's question", user_id="alice")
        reg.append_turn("s1", "assistant", "answer", user_id="alice")
        assert len(reg.load_turns("s1", user_id="alice")) == 2
        assert reg.load_turns("s1", user_id="bob") == []
        assert [s["session_id"] for s in reg.list_sessions(user_id="alice")] == ["s1"]
        assert reg.list_sessions(user_id="bob") == []

    def test_the_caller_is_resolved_from_the_context_var_not_the_call_site(self, reg):
        """Scoping lives in the registry layer, per the plan's isolation
        matrix — a route that has to remember to pass a user id is a route
        that will not."""
        from resource_explorer.a2a_auth import CallerIdentity, current_caller

        reset = current_caller.set(
            CallerIdentity(user_id="alice", egeria_token="t", auth_source="app-jwt")
        )
        try:
            reg.set_working_set_hidden("repo", "z", True)   # no user_id passed
        finally:
            current_caller.reset(reset)
        assert reg.is_working_set_hidden("repo", "z", user_id="alice") is True
        assert reg.is_working_set_hidden("repo", "z", user_id="bob") is False

    def test_the_migration_widens_the_primary_key_not_just_the_columns(self, tmp_path):
        """`ADD COLUMN` alone leaves the old two-column key in place, so the
        second user to touch a resource collides with the first — a schema that
        looks migrated with no isolation behind it."""
        import sqlite3

        db = tmp_path / "legacy.db"
        con = sqlite3.connect(db)
        con.execute(
            "CREATE TABLE resource_working_set (entity_type TEXT NOT NULL, "
            "entity_slug TEXT NOT NULL, hidden INTEGER NOT NULL DEFAULT 0, "
            "updated_at TEXT NOT NULL, PRIMARY KEY (entity_type, entity_slug))"
        )
        con.execute("INSERT INTO resource_working_set VALUES ('repo','old',1,'2020')")
        con.commit()
        con.close()

        r = ProjectRegistry(database_url=f"sqlite:///{db}")
        r._init_schema()
        r.set_working_set_hidden("repo", "old", False, user_id="alice")
        r.set_working_set_hidden("repo", "old", False, user_id="bob")
        assert r.is_working_set_hidden("repo", "old", user_id="") is True


# ---------------------------------------------------------------------------
# 8. Queue fairness on real ids
# ---------------------------------------------------------------------------


class TestQueueFairness:
    def test_requested_by_is_the_signed_in_caller(self):
        from resource_explorer.a2a_auth import CallerIdentity, current_caller
        from resource_explorer.run_queue import requested_by

        assert requested_by() == ""
        reset = current_caller.set(
            CallerIdentity(user_id="dan", egeria_token="t", auth_source="app-jwt")
        )
        try:
            assert requested_by() == "dan"
        finally:
            current_caller.reset(reset)

    def test_fairness_applies_to_a_real_id(self, reg):
        reg.enqueue_run("analysis_run", {"slug": "a"}, requested_by="dan")
        reg.enqueue_run("analysis_run", {"slug": "b"}, requested_by="dan")
        first = reg.claim_next_run("w1")
        assert first is not None and first["requested_by"] == "dan"
        # Dan already has one running; his second row waits.
        assert reg.claim_next_run("w2") is None

    def test_the_empty_bucket_is_still_exempt_for_the_workers_own_runs(self, reg):
        reg.enqueue_run("analysis_run", {"slug": "a"}, requested_by="")
        reg.enqueue_run("analysis_run", {"slug": "b"}, requested_by="")
        assert reg.claim_next_run("w1") is not None
        assert reg.claim_next_run("w2") is not None

    def test_one_users_backlog_does_not_block_another_user(self, reg):
        reg.enqueue_run("analysis_run", {"slug": "a"}, requested_by="dan")
        reg.enqueue_run("analysis_run", {"slug": "b"}, requested_by="dan")
        reg.enqueue_run("analysis_run", {"slug": "c"}, requested_by="erin")
        assert reg.claim_next_run("w1")["requested_by"] == "dan"
        assert reg.claim_next_run("w2")["requested_by"] == "erin"

    def test_a_queued_run_executes_as_the_person_who_asked_for_it(self):
        from resource_explorer.a2a_auth import caller
        from resource_explorer.run_queue import _run_as_requester

        with _run_as_requester({"requested_by": "dan"}):
            identity = caller()
            assert identity.user_id == "dan"
            # No token survives the queue — the worker authenticates as itself
            # and Ownership carries the requester. Documented interim.
            assert identity.egeria_token is None
        assert caller() is None

    def test_a_service_account_run_executes_with_no_caller_at_all(self):
        from resource_explorer.a2a_auth import caller
        from resource_explorer.run_queue import _run_as_requester

        with _run_as_requester({"requested_by": ""}):
            assert caller() is None


# ---------------------------------------------------------------------------
# 9. The zone bootstrap
# ---------------------------------------------------------------------------


class TestDraftZoneBootstrap:
    @pytest.mark.parametrize("miss", [
        "No elements found",   # what the live platform actually returns
        "No element found",    # what pyegeria's docstring says it returns
        "",
        "some other explanatory sentence",
    ])
    def test_a_not_found_sentence_is_never_mistaken_for_a_guid(self, miss):
        """The bug this exists for, found live on 2026-09-04.

        `get_metadata_element_by_unique_name` signals "nothing here" by
        returning a human sentence. The first version tested for the literal
        `"No element found"` from pyegeria's docstring; the platform says
        **"No elements found"** — plural — so the test passed on a miss and
        `ensure_draft_zone_exists` reported
        `{"status": "exists", "guid": "No elements found"}`. The zone was never
        created and the result said it already had been.

        Matching the *shape of a GUID* rather than the *wording of an error*
        cannot go stale when the message changes, which is why the parametrize
        list includes both wordings and does not care which is current.
        """
        from resource_explorer.egeria_identity import _existing_guid

        client = type("c", (), {
            "get_metadata_element_by_unique_name": lambda self, qn: miss,
        })()
        if miss in ("No elements found", "No element found"):
            assert _existing_guid(client, "GovernanceZone::x") == ""
        else:
            # Neither a GUID nor pyegeria's miss sentence: never a GUID, and never a miss either
            # (re/lookup-failure-never-creates round 3): could not check, so no zone is created on it.
            with pytest.raises(RuntimeError, match="could not be made"):
                _existing_guid(client, "GovernanceZone::x")

    def test_a_real_guid_is_recognised_in_either_return_shape(self):
        guid = "502438ae-3503-4481-b370-cb9c9b75f030"
        from resource_explorer.egeria_identity import _existing_guid

        as_string = type("c", (), {
            "get_metadata_element_by_unique_name": lambda self, qn: guid,
        })()
        as_dict = type("c", (), {
            "get_metadata_element_by_unique_name": lambda self, qn: {"elementGUID": guid},
        })()
        assert _existing_guid(as_string, "q") == guid
        assert _existing_guid(as_dict, "q") == guid

    def test_it_never_raises_when_egeria_is_unreachable(self, monkeypatch):
        """A deployment whose Egeria is down at worker start must still start.
        The zone is created on the next attempt, and until then a publish still
        names it in its ZoneMembership."""
        from resource_explorer import egeria_identity

        monkeypatch.setenv("EXPLORER_DRAFT_ZONE", "resource-explorer-draft")   # configured: it tries
        monkeypatch.setattr(
            "resource_explorer.egeria_clients._daemon_credential",
            lambda: (_ for _ in ()).throw(RuntimeError("no platform")),
        )
        out = egeria_identity.ensure_draft_zone_exists()
        assert out["status"] == "error"
        assert out["zone"] == "resource-explorer-draft"
