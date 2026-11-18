"""EA's auth adapter over trellis-auth, after the 2026-09-04 token contract
change: the session JWT carries the user's Egeria bearer token, never their
password. See advisor/auth.py and docs/runtime-architecture-plan.md §4.

These exercise EA's own layer — the anonymous bypass, the credential dict shape
the ~15 `egeria_credentials=` call sites consume, and the service-account
fallback the 2026-08-29 decision constrains. The mechanism itself is tested in
packages/trellis-auth/tests/test_auth.py.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import jwt
import pytest
# starlette's HTTPException, not fastapi's: fastapi.HTTPException is a SUBCLASS,
# so asserting on it would miss the ones trellis_auth raises (a FastAPI app's
# default handler catches the starlette base, which is why raising it works).
from starlette.exceptions import HTTPException

from advisor import auth


@pytest.fixture(autouse=True)
def _known_auth_config(monkeypatch):
    """Pin the adapter's config so tests don't depend on advisor.yaml or the
    machine-id secret fallback."""
    monkeypatch.setattr(auth, "_auth_cfg", lambda: {
        "enabled": True,
        "anonymous_rag_mode": True,
        "jwt_ttl_hours": 8,
        "jwt_secret": "test-jwt-secret",
        "portal": {"shared_secret": "test-portal-secret"},
    })
    # Both names for each secret. _jwt_secret()/_portal_secret() gained a
    # TRELLIS_* fallback on 2026-09-06 so EA honours the workspace-wide secret
    # the trellis compose overlay sets; clearing only the ADVISOR_* name then
    # stopped isolating these tests. This checkout's own .env sets
    # TRELLIS_PORTAL_SECRET, and EA calls load_dotenv(), so the real value
    # reached _portal_secret() and the fixture's "test-portal-secret" no longer
    # verified the token it had just signed -- InvalidSignatureError, on a
    # machine-dependent value.
    for var in ("ADVISOR_JWT_SECRET", "TRELLIS_JWT_SECRET",
                "ADVISOR_PORTAL_SECRET", "TRELLIS_PORTAL_SECRET"):
        monkeypatch.delenv(var, raising=False)
    # The login policy is process-cached (advisor.auth.get_policy); clear it
    # around every test so one that varies TRELLIS_*/ADVISOR_* env cannot leak
    # its policy into the next.
    for var in ("TRELLIS_REQUIRE_LOGIN", "ADVISOR_REQUIRE_LOGIN",
                "TRELLIS_ANONYMOUS_READ", "ADVISOR_ANONYMOUS_READ"):
        monkeypatch.delenv(var, raising=False)
    auth.reset_policy_cache()
    yield
    auth.reset_policy_cache()


class FakeRequest:
    """Minimal stand-in for a Request — only .headers is read."""

    def __init__(self, authorization: str | None = None):
        self.headers = {"Authorization": authorization} if authorization else {}


def make_egeria_token(user_id: str = "dan.egeria", ttl_seconds: int = 3600) -> str:
    now = datetime.now(timezone.utc)
    return jwt.encode(
        {"iss": "self", "sub": user_id, "iat": now,
         "exp": now + timedelta(seconds=ttl_seconds)},
        "egeria-view-server-key",
        algorithm="HS256",
    )


def signed_request(user_id: str = "dan", egeria_token: str | None = None) -> FakeRequest:
    token = auth.create_access_token(
        user_id=user_id, egeria_token=egeria_token or make_egeria_token()
    )
    return FakeRequest(f"Bearer {token}")


# ---------------------------------------------------------------------------
# The session JWT
# ---------------------------------------------------------------------------

def test_session_jwt_carries_the_egeria_token_and_no_password():
    egeria_token = make_egeria_token()
    payload = auth.decode_token(auth.create_access_token("dan", egeria_token))

    assert payload["sub"] == "dan"
    assert payload["egeria_token"] == egeria_token
    assert "egeria_password" not in payload
    assert not any("password" in key.lower() for key in payload)
    assert "hunter2" not in json.dumps(payload)


def test_session_jwt_exp_is_capped_at_the_egeria_tokens_expiry():
    """Egeria's tokens last one hour (measured 2026-09-04 against
    qs-view-server); EA's jwt_ttl_hours is 8. The shorter one wins, so a session
    can never outlive the token it carries."""
    egeria_token = make_egeria_token(ttl_seconds=3600)
    payload = auth.decode_token(auth.create_access_token("dan", egeria_token))

    egeria_exp = jwt.decode(egeria_token, options={"verify_signature": False})["exp"]
    assert payload["exp"] == egeria_exp
    assert payload["exp"] < (datetime.now(timezone.utc) + timedelta(hours=8)).timestamp()


# ---------------------------------------------------------------------------
# Per-request propagation
# ---------------------------------------------------------------------------

def test_get_egeria_token_round_trip():
    egeria_token = make_egeria_token()
    assert auth.get_egeria_token(signed_request(egeria_token=egeria_token)) == egeria_token


def test_get_egeria_credentials_carries_the_token_and_an_empty_password():
    egeria_token = make_egeria_token()
    creds = auth.get_egeria_credentials(signed_request(egeria_token=egeria_token))

    assert creds == {"user_id": "dan", "password": "", "token": egeria_token}


def test_get_egeria_credentials_anonymous_returns_none():
    assert auth.get_egeria_credentials(FakeRequest()) is None


def test_resolve_egeria_credentials_preserves_a_signed_in_users_token():
    creds = {"user_id": "dan", "password": "", "token": "tok"}
    assert auth.resolve_egeria_credentials(creds) == creds


def test_resolve_egeria_credentials_service_account_fallback_has_no_token(monkeypatch):
    """The one legitimately password-backed path: an anonymous/background call.
    It must be distinguishable from a signed-in person — empty `token` is the
    signal, so a call site can refuse to attribute artifacts to it (2026-08-29
    decision)."""
    from advisor.config import settings
    monkeypatch.setattr(settings, "daemon_user_id", "erinoverview", raising=False)
    monkeypatch.setattr(settings, "daemon_user_password", "secret", raising=False)

    resolved = auth.resolve_egeria_credentials(None)

    assert resolved["user_id"] == "erinoverview"
    assert resolved["password"] == "secret"
    assert resolved["token"] == ""


# ---------------------------------------------------------------------------
# EA's own policy layer — the anonymous bypass trellis_auth has no notion of
# ---------------------------------------------------------------------------

def test_auth_disabled_bypass_yields_an_anonymous_user_with_no_token(monkeypatch):
    """The bypass still exists; since 2026-09-04 the *switch* is the shared policy.

    `advisor.yaml`'s `auth.enabled` is retired — this used to monkeypatch
    `_auth_cfg` to `{"enabled": False}` and would now be silently ignored,
    which is exactly what the deprecation warning is for.
    """
    monkeypatch.setenv("ADVISOR_REQUIRE_LOGIN", "false")
    auth.reset_policy_cache()

    user = auth.get_current_user(FakeRequest())

    assert user is not None and user["anonymous"] is True
    assert user["egeria_token"] == ""
    assert auth.get_egeria_credentials(FakeRequest())["token"] == ""


def test_require_egeria_user_401s_when_anonymous():
    with pytest.raises(HTTPException) as excinfo:
        auth.require_egeria_user(FakeRequest())
    assert excinfo.value.status_code == 401


# ---------------------------------------------------------------------------
# Portal SSO — the Portal's real payload shape
# ---------------------------------------------------------------------------

def portal_token(**overrides) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": "dan.egeria",
        "role": "user",
        "display_name": "Dan Egeria",
        "egeria_token": make_egeria_token(),
        "exp": now + timedelta(minutes=5),
    }
    payload.update(overrides)
    return jwt.encode(payload, "test-portal-secret", algorithm="HS256")


def test_exchange_portal_token_accepts_the_portals_real_shape():
    egeria_token = make_egeria_token()
    payload = auth.exchange_portal_token(portal_token(egeria_token=egeria_token))

    assert payload["sub"] == "dan.egeria"
    assert payload["egeria_token"] == egeria_token


def test_exchange_portal_token_rejects_the_retired_password_shape():
    now = datetime.now(timezone.utc)
    old_shape = jwt.encode(
        {"egeria_user": "dan.egeria", "egeria_password": "hunter2",
         "iat": now, "exp": now + timedelta(minutes=5)},
        "test-portal-secret",
        algorithm="HS256",
    )

    with pytest.raises(HTTPException) as excinfo:
        auth.exchange_portal_token(old_shape)

    assert excinfo.value.status_code == 400
    assert "egeria_token" in excinfo.value.detail
    assert "2026-09-04" in excinfo.value.detail


def test_portal_sso_mints_a_session_with_no_password():
    """The full SSO path as /api/auth/portal runs it."""
    egeria_token = make_egeria_token()
    payload = auth.exchange_portal_token(portal_token(egeria_token=egeria_token, role="admin"))
    session = auth.decode_token(auth.create_access_token(
        user_id=payload["sub"],
        egeria_token=payload["egeria_token"],
        role=payload["role"],
        display_name=payload["display_name"],
    ))

    assert session["sub"] == "dan.egeria"
    assert session["role"] == "admin"
    assert session["egeria_token"] == egeria_token
    assert not any("password" in key.lower() for key in session)


# ---------------------------------------------------------------------------
# apply_token, re-exported for live call sites
# ---------------------------------------------------------------------------

def test_apply_token_prefers_the_users_token_over_the_clients_own_credentials():
    class Client:
        bearer = None
        self_authenticated = False

        def __init__(self, user_id=None, user_pwd=None):
            self.user_id, self.user_pwd = user_id, user_pwd

        def set_bearer_token(self, token):
            self.bearer = token

        def create_egeria_bearer_token(self):
            self.self_authenticated = True

    signed_in, service, bare = Client("erinoverview"), Client("ea-svc", "svc-secret"), Client()
    auth.apply_token(signed_in, "tok")
    auth.apply_token(service, "")
    assert signed_in.bearer == "tok" and signed_in.self_authenticated is False
    assert service.bearer is None and service.self_authenticated is True    # its own user and password
    # TR-34: no token and no credential of its own: minting would be pyegeria's settings fallback. Refused.
    with pytest.raises(PermissionError, match="EA_DAEMON_USER_ID and EA_DAEMON_USER_PASSWORD"):
        auth.apply_token(bare, "")
    assert bare.self_authenticated is False
