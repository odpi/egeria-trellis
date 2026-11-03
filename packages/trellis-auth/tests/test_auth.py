"""Tests for the 2026-09-04 token contract: the app JWT carries the user's
Egeria bearer token, never a password. See trellis_auth/auth.py's module
docstring and docs/runtime-architecture-plan.md §4."""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone

import jwt
import pytest
from starlette.exceptions import HTTPException

from trellis_auth import (
    AuthConfig,
    apply_token,
    create_access_token,
    decode_token,
    egeria_token_expiry,
    exchange_portal_token,
    get_current_user,
    get_egeria_credentials,
    get_egeria_token,
    is_authenticated,
    login_with_password,
    require_egeria_user,
    validate_egeria_credentials,
    validate_egeria_token,
)


# ---------------------------------------------------------------------------
# Helpers — Egeria bearer tokens are RS256 JWTs from the view server. We never
# verify their signature (see egeria_token_expiry), so an HS256 stand-in with
# the same claims exercises every path this package has.
# ---------------------------------------------------------------------------

def make_egeria_token(user_id: str = "dan.egeria", ttl_seconds: int = 3600) -> str:
    now = datetime.now(timezone.utc)
    return jwt.encode(
        {
            "iss": "self",
            "sub": user_id,
            "iat": now,
            "exp": now + timedelta(seconds=ttl_seconds),
            "displayName": "Dan Egeria",
        },
        "egeria-view-server-key",
        algorithm="HS256",
    )


def make_portal_token(config: AuthConfig, **overrides) -> str:
    """A token in the shape the Portal actually issues
    (demo_auth_handler.py::_make_jwt)."""
    now = datetime.now(timezone.utc)
    payload = {
        "sub": "dan.egeria",
        "role": "user",
        "display_name": "Dan Egeria",
        "egeria_token": make_egeria_token(),
        "exp": now + timedelta(minutes=5),
    }
    payload.update(overrides)
    secret = overrides.pop("_secret", config.portal_secret)
    return jwt.encode(payload, secret, algorithm=config.jwt_algorithm)


# ---------------------------------------------------------------------------
# Token round-trip
# ---------------------------------------------------------------------------

def test_create_and_decode_round_trip(config):
    egeria_token = make_egeria_token()
    token = create_access_token("dan", egeria_token, config)
    payload = decode_token(token, config)

    assert payload["sub"] == "dan"
    assert payload["user_id"] == "dan"
    assert payload["role"] == "user"
    assert payload["display_name"] == "dan"
    assert payload["egeria_token"] == egeria_token
    assert "exp" in payload and "iat" in payload


def test_app_jwt_never_contains_a_password(config):
    """The whole point of the 2026-09-04 change. Assert on the decoded claims
    AND on the raw token bytes, so a password smuggled under any other key name
    still fails."""
    token = create_access_token("dan", make_egeria_token(), config)
    payload = decode_token(token, config)

    assert "egeria_password" not in payload
    assert "password" not in payload
    assert not any("password" in key.lower() for key in payload)

    # The JWT body is base64 of the JSON claims — a password anywhere in it
    # would be recoverable by anyone holding the token.
    import base64
    import json
    body = token.split(".")[1]
    body += "=" * (-len(body) % 4)
    raw = json.loads(base64.urlsafe_b64decode(body))
    assert "hunter2" not in json.dumps(raw)


def test_create_access_token_requires_an_egeria_token(config):
    with pytest.raises(ValueError) as excinfo:
        create_access_token("dan", "", config)
    assert "bearer token" in str(excinfo.value).lower()


def test_create_access_token_role_and_display_name(config):
    token = create_access_token(
        "dan", make_egeria_token(), config, role="admin", display_name="Dan W"
    )
    payload = decode_token(token, config)
    assert payload["role"] == "admin"
    assert payload["display_name"] == "Dan W"


def test_decode_expired_token(config):
    now = datetime.now(timezone.utc)
    payload = {
        "sub": "dan",
        "egeria_token": make_egeria_token(),
        "iat": now - timedelta(hours=10),
        "exp": now - timedelta(hours=2),
    }
    expired = jwt.encode(payload, config.jwt_secret, algorithm=config.jwt_algorithm)

    with pytest.raises(jwt.ExpiredSignatureError):
        decode_token(expired, config)


def test_decode_invalid_signature(config):
    token = create_access_token("dan", make_egeria_token(), config)
    wrong_config = AuthConfig(jwt_secret="a-different-secret")

    with pytest.raises(jwt.InvalidSignatureError):
        decode_token(token, wrong_config)


# ---------------------------------------------------------------------------
# exp capping — the app session must never outlive the Egeria token it carries
# ---------------------------------------------------------------------------

def test_exp_capped_at_egeria_token_expiry(config):
    """config.jwt_ttl_hours is 8; Egeria's own tokens last 1 hour (measured
    2026-09-04 against qs-view-server). The Egeria expiry must win."""
    egeria_token = make_egeria_token(ttl_seconds=3600)
    egeria_exp = egeria_token_expiry(egeria_token)

    payload = decode_token(create_access_token("dan", egeria_token, config), config)

    assert payload["exp"] == egeria_exp
    # And it is genuinely shorter than the app TTL would have been.
    assert payload["exp"] < (datetime.now(timezone.utc) + timedelta(hours=8)).timestamp()


def test_exp_uses_app_ttl_when_egeria_token_lives_longer(config):
    """A long-lived Egeria token must not extend the app session past its own
    configured TTL — the cap is a min(), not an assignment."""
    egeria_token = make_egeria_token(ttl_seconds=60 * 60 * 24 * 7)  # a week
    payload = decode_token(create_access_token("dan", egeria_token, config), config)

    app_ttl_exp = (datetime.now(timezone.utc) + timedelta(hours=8)).timestamp()
    assert payload["exp"] == pytest.approx(app_ttl_exp, abs=5)
    assert payload["exp"] < egeria_token_expiry(egeria_token)


def test_exp_falls_back_to_app_ttl_for_an_opaque_egeria_token(config):
    """A deployment whose Egeria issues an opaque (non-JWT) token has no exp to
    read — we must still issue a usable session rather than refusing."""
    payload = decode_token(create_access_token("dan", "an-opaque-token", config), config)
    app_ttl_exp = (datetime.now(timezone.utc) + timedelta(hours=8)).timestamp()
    assert payload["exp"] == pytest.approx(app_ttl_exp, abs=5)


def test_egeria_token_expiry_shapes():
    assert egeria_token_expiry("") is None
    assert egeria_token_expiry("not-a-jwt") is None
    assert egeria_token_expiry(make_egeria_token()) is not None
    no_exp = jwt.encode({"sub": "dan"}, "k", algorithm="HS256")
    assert egeria_token_expiry(no_exp) is None


# ---------------------------------------------------------------------------
# get_current_user / require_egeria_user / is_authenticated
# ---------------------------------------------------------------------------

def test_get_current_user_missing_header_returns_none(config, make_request):
    request = make_request(authorization=None)
    assert get_current_user(request, config) is None


def test_get_current_user_malformed_header_returns_none(config, make_request):
    # No "Bearer " prefix at all.
    request = make_request(authorization="Basic dXNlcjpwYXNz")
    assert get_current_user(request, config) is None


def test_get_current_user_invalid_token_returns_none(config, make_request):
    request = make_request(authorization="Bearer not-a-real-jwt")
    assert get_current_user(request, config) is None


def test_get_current_user_valid_token(config, make_request):
    token = create_access_token("dan", make_egeria_token(), config)
    request = make_request(authorization=f"Bearer {token}")

    user = get_current_user(request, config)

    assert user is not None
    assert user["sub"] == "dan"


def test_require_egeria_user_raises_401_when_absent(config, make_request):
    request = make_request(authorization=None)
    with pytest.raises(HTTPException) as excinfo:
        require_egeria_user(request, config)
    assert excinfo.value.status_code == 401


def test_require_egeria_user_returns_payload_when_present(config, make_request):
    token = create_access_token("dan", make_egeria_token(), config)
    request = make_request(authorization=f"Bearer {token}")

    user = require_egeria_user(request, config)

    assert user["sub"] == "dan"


def test_is_authenticated(config, make_request):
    token = create_access_token("dan", make_egeria_token(), config)
    assert is_authenticated(make_request(authorization=f"Bearer {token}"), config) is True
    assert is_authenticated(make_request(authorization=None), config) is False


# ---------------------------------------------------------------------------
# get_egeria_token / the removed get_egeria_credentials
# ---------------------------------------------------------------------------

def test_get_egeria_token_present(config, make_request):
    egeria_token = make_egeria_token()
    token = create_access_token("dan", egeria_token, config)
    request = make_request(authorization=f"Bearer {token}")

    assert get_egeria_token(request, config) == egeria_token


def test_get_egeria_token_absent_returns_none(config, make_request):
    assert get_egeria_token(make_request(authorization=None), config) is None


def test_get_egeria_credentials_is_removed_with_a_clear_message(config, make_request):
    request = make_request(authorization=None)
    with pytest.raises(RuntimeError) as excinfo:
        get_egeria_credentials(request, config)
    message = str(excinfo.value)
    assert "get_egeria_token" in message
    assert "bearer token" in message.lower()
    assert "2026-09-04" in message


# ---------------------------------------------------------------------------
# apply_token
# ---------------------------------------------------------------------------

class _RecordingClient:
    def __init__(self):
        self.bearer = None
        self.self_authenticated = False

    def set_bearer_token(self, token):
        self.bearer = token

    def create_egeria_bearer_token(self):
        self.self_authenticated = True


def test_apply_token_uses_the_supplied_token():
    client = _RecordingClient()
    apply_token(client, "a-token")
    assert client.bearer == "a-token"
    assert client.self_authenticated is False


def test_apply_token_falls_back_to_the_clients_own_credentials():
    client = _RecordingClient()
    apply_token(client, None)
    assert client.bearer is None
    assert client.self_authenticated is True


# ---------------------------------------------------------------------------
# Portal token exchange — the Portal's REAL payload shape
# ---------------------------------------------------------------------------

def test_exchange_portal_token_success(config):
    egeria_token = make_egeria_token()
    portal_token = make_portal_token(config, egeria_token=egeria_token)

    result = exchange_portal_token(portal_token, config)

    assert result["sub"] == "dan.egeria"
    assert result["role"] == "user"
    assert result["display_name"] == "Dan Egeria"
    assert result["egeria_token"] == egeria_token


def test_portal_token_round_trips_into_an_app_jwt(config):
    """The end-to-end SSO path: validate the Portal's token, then mint our own
    session from it with no Egeria round-trip."""
    egeria_token = make_egeria_token()
    payload = exchange_portal_token(
        make_portal_token(config, egeria_token=egeria_token, role="admin"), config
    )
    app_token = create_access_token(
        payload["sub"], payload["egeria_token"], config,
        role=payload["role"], display_name=payload["display_name"],
    )
    decoded = decode_token(app_token, config)

    assert decoded["sub"] == "dan.egeria"
    assert decoded["role"] == "admin"
    assert decoded["egeria_token"] == egeria_token
    assert "egeria_password" not in decoded


def test_exchange_portal_token_rejects_the_retired_password_shape(config):
    """Pre-2026-09-04 contract: {egeria_user, egeria_password, iat, exp}.
    Nothing ever produced it, so a caller sending one is on the old contract and
    must be told so explicitly rather than getting a generic 401."""
    now = datetime.now(timezone.utc)
    old_shape = jwt.encode(
        {
            "egeria_user": "dan.egeria",
            "egeria_password": "hunter2",
            "iat": now,
            "exp": now + timedelta(minutes=5),
        },
        config.portal_secret,
        algorithm=config.jwt_algorithm,
    )

    with pytest.raises(HTTPException) as excinfo:
        exchange_portal_token(old_shape, config)

    assert excinfo.value.status_code == 400
    detail = excinfo.value.detail
    assert "egeria_password" in detail
    assert "egeria_token" in detail
    assert "2026-09-04" in detail


def test_exchange_portal_token_missing_egeria_token(config):
    portal_token = make_portal_token(config, egeria_token="")
    with pytest.raises(HTTPException) as excinfo:
        exchange_portal_token(portal_token, config)
    assert excinfo.value.status_code == 400
    assert "egeria_token" in excinfo.value.detail


def test_exchange_portal_token_missing_sub(config):
    portal_token = make_portal_token(config, sub="")
    with pytest.raises(HTTPException) as excinfo:
        exchange_portal_token(portal_token, config)
    assert excinfo.value.status_code == 400
    assert "sub" in excinfo.value.detail


def test_exchange_portal_token_not_configured():
    config = AuthConfig(jwt_secret="test-secret", portal_secret="")

    with pytest.raises(HTTPException) as excinfo:
        exchange_portal_token("whatever", config)
    assert excinfo.value.status_code == 503


def test_exchange_portal_token_expired(config):
    now = datetime.now(timezone.utc)
    expired = make_portal_token(config, exp=now - timedelta(minutes=1))

    with pytest.raises(HTTPException) as excinfo:
        exchange_portal_token(expired, config)
    assert excinfo.value.status_code == 401
    assert "expired" in excinfo.value.detail.lower()


def test_exchange_portal_token_wrong_secret(config):
    now = datetime.now(timezone.utc)
    signed_wrong = jwt.encode(
        {
            "sub": "dan.egeria",
            "role": "user",
            "display_name": "Dan Egeria",
            "egeria_token": make_egeria_token(),
            "exp": now + timedelta(minutes=5),
        },
        "not-the-portal-secret",
        algorithm=config.jwt_algorithm,
    )

    with pytest.raises(HTTPException) as excinfo:
        exchange_portal_token(signed_wrong, config)
    assert excinfo.value.status_code == 401


# ---------------------------------------------------------------------------
# login_with_password / validate_egeria_token / validate_egeria_credentials
# — pyegeria client mocked, never a live server
# ---------------------------------------------------------------------------

def _connected_config() -> AuthConfig:
    return AuthConfig(
        jwt_secret="test-secret",
        egeria_view_server="qs-view-server",
        egeria_platform_url="https://localhost:9443",
    )


def _install_fake_pyegeria(monkeypatch, cls):
    fake_module = type("module", (), {"EgeriaTech": cls})
    monkeypatch.setitem(sys.modules, "pyegeria.egeria_tech_client", fake_module)


def test_login_with_password_returns_the_token(monkeypatch):
    issued = make_egeria_token()

    class _FakeEgeriaTech:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        def create_egeria_bearer_token(self, user_id, password):
            assert (user_id, password) == ("dan.egeria", "hunter2")
            return issued

    _install_fake_pyegeria(monkeypatch, _FakeEgeriaTech)

    assert login_with_password("dan.egeria", "hunter2", _connected_config()) == issued


def test_login_with_password_bad_credentials_returns_none(monkeypatch):
    class _FakeEgeriaTech:
        def __init__(self, **kwargs):
            pass

        def create_egeria_bearer_token(self, user_id, password):
            raise RuntimeError("invalid credentials")

    _install_fake_pyegeria(monkeypatch, _FakeEgeriaTech)

    assert login_with_password("dan.egeria", "wrong", _connected_config()) is None


def test_login_with_password_unconfigured_returns_none():
    config = AuthConfig(jwt_secret="test-secret")
    assert login_with_password("dan.egeria", "hunter2", config) is None


def test_login_with_password_missing_pyegeria_returns_none(monkeypatch):
    monkeypatch.setitem(sys.modules, "pyegeria.egeria_tech_client", None)
    assert login_with_password("dan.egeria", "hunter2", _connected_config()) is None


def test_validate_egeria_token_success(monkeypatch):
    class _FakeEgeriaTech:
        def __init__(self, **kwargs):
            self.bearer = None

        def set_bearer_token(self, token):
            self.bearer = token

        def create_egeria_bearer_token(self):  # pragma: no cover - must not fire
            raise AssertionError("should have reused the supplied token")

        def get_my_profile(self):
            assert self.bearer, "token was not applied before the call"
            return {"class": "OpenMetadataRootElement"}

    _install_fake_pyegeria(monkeypatch, _FakeEgeriaTech)

    assert validate_egeria_token(make_egeria_token(), _connected_config()) is True


def test_validate_egeria_token_rejected_by_egeria(monkeypatch):
    class _FakeEgeriaTech:
        def __init__(self, **kwargs):
            pass

        def set_bearer_token(self, token):
            pass

        def get_my_profile(self):
            raise RuntimeError("AUTHORIZATION_ERROR_401")

    _install_fake_pyegeria(monkeypatch, _FakeEgeriaTech)

    assert validate_egeria_token(make_egeria_token(), _connected_config()) is False


def test_validate_egeria_token_expired_short_circuits(monkeypatch):
    """An already-expired token must be rejected locally, without a call."""
    class _FakeEgeriaTech:  # pragma: no cover - must never be constructed
        def __init__(self, **kwargs):
            raise AssertionError("expired token should not reach Egeria")

    _install_fake_pyegeria(monkeypatch, _FakeEgeriaTech)

    expired = make_egeria_token(ttl_seconds=-60)
    assert validate_egeria_token(expired, _connected_config()) is False


def test_validate_egeria_token_empty_is_false():
    assert validate_egeria_token("", _connected_config()) is False


def test_validate_egeria_credentials_service_account_match_when_unconfigured():
    config = AuthConfig(
        jwt_secret="test-secret",
        egeria_view_server="",
        egeria_platform_url="",
        egeria_service_account_user="svc",
        egeria_service_account_password="svc-pwd",
    )

    assert validate_egeria_credentials("svc", "svc-pwd", config) is True
    assert validate_egeria_credentials("svc", "wrong-pwd", config) is False


def test_validate_egeria_credentials_success_via_mocked_client(monkeypatch):
    class _FakeEgeriaTech:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        def create_egeria_bearer_token(self, user_id, password):
            return make_egeria_token()   # JWT-shaped: login refuses anything else (2026-10-20)

    _install_fake_pyegeria(monkeypatch, _FakeEgeriaTech)

    assert validate_egeria_credentials("dan.egeria", "hunter2", _connected_config()) is True


def test_validate_egeria_credentials_failure_via_mocked_client(monkeypatch):
    class _FakeEgeriaTech:
        def __init__(self, **kwargs):
            pass

        def create_egeria_bearer_token(self, user_id, password):
            raise RuntimeError("invalid credentials")

    _install_fake_pyegeria(monkeypatch, _FakeEgeriaTech)

    assert validate_egeria_credentials("dan.egeria", "wrong", _connected_config()) is False


def test_validate_egeria_credentials_missing_pyegeria_returns_false(monkeypatch):
    """pyegeria is an optional extra — a missing install must degrade to
    False (caught by the broad except), not raise ImportError."""
    monkeypatch.setitem(sys.modules, "pyegeria.egeria_tech_client", None)

    assert validate_egeria_credentials("dan.egeria", "hunter2", _connected_config()) is False


# ---------------------------------------------------------------------------
# 2026-10-20: a login cached a 442-char, 6-dot "token" with no exp, and Egeria
# 401'd every call made with it. pyegeria's create_egeria_bearer_token returns
# the token endpoint's response body whatever the HTTP status, so an error body
# comes back looking like a token. login_with_password must refuse anything
# that is not JWT-shaped.
# ---------------------------------------------------------------------------

# Not Egeria's exact body (that was never captured) — the same shape: a JSON
# error with dots in it, the kind of thing a 401/5xx answer carries.
_ERROR_BODY = (
    '{"class":"VoidResponse","relatedHTTPCode":401,"exceptionClassName":'
    '"org.odpi.openmetadata.frameworks.auditlog.messagesets.ExceptionMessageDefinition",'
    '"exceptionErrorMessage":"OMAG-SECURITY-401-001 user peterprofile is not authorized"}'
)

_NOT_TOKENS = [
    _ERROR_BODY,
    "FAILED",                                        # pyegeria's own transport-error return
    "<html><body>502 Bad Gateway</body></html>",
    "a.b.c",                                         # three segments, none of them JSON
    "eyJhbGciOiJIUzI1NiJ9.e30",                      # two segments
    " " + "x" * 10,
]


@pytest.mark.parametrize("value", _NOT_TOKENS)
def test_is_well_formed_egeria_token_refuses_non_jwts(value):
    from trellis_auth import is_well_formed_egeria_token

    assert is_well_formed_egeria_token(value) is False


def test_is_well_formed_egeria_token_accepts_a_jwt():
    from trellis_auth import is_well_formed_egeria_token

    assert is_well_formed_egeria_token(make_egeria_token()) is True
    assert is_well_formed_egeria_token(None) is False


def test_egeria_token_shape_never_shows_content():
    from trellis_auth import egeria_token_shape

    assert egeria_token_shape("ab.cd.ef") == "8 chars, 2 dots"
    assert "secret" not in egeria_token_shape("secret.value")


@pytest.mark.parametrize("value", _NOT_TOKENS)
def test_login_with_password_refuses_a_reply_that_is_not_a_token(monkeypatch, caplog, value):
    class _FakeEgeriaTech:
        def __init__(self, **kwargs):
            pass

        def create_egeria_bearer_token(self, user_id, password):
            return value

    _install_fake_pyegeria(monkeypatch, _FakeEgeriaTech)

    with caplog.at_level("DEBUG", logger="trellis_auth.auth"):
        assert login_with_password("peterprofile", "hunter2", _connected_config()) is None
    assert "not a bearer token" in caplog.text
    assert "hunter2" not in caplog.text
    if len(value) > 8:
        assert value not in caplog.text, "the reply itself must never be logged"


def test_real_pyegeria_returns_an_error_body_as_a_token_and_login_refuses_it(monkeypatch):
    """The root cause, end to end through the real pyegeria: only HTTP is faked.

    The token endpoint answers 401 with a JSON body; pyegeria hands that body back
    as the token. Before the shape gate login_with_password returned it."""
    httpx = pytest.importorskip("httpx")
    bsc = pytest.importorskip("pyegeria.core._base_server_client")
    from pyegeria.egeria_tech_client import EgeriaTech

    def handler(request):
        if request.url.path.endswith("/api/token"):
            return httpx.Response(401, text=_ERROR_BODY)
        return httpx.Response(200, json={})

    real = httpx.AsyncClient

    class _Faked(real):
        def __init__(self, *a, **kw):
            kw["transport"] = httpx.MockTransport(handler)
            super().__init__(*a, **kw)

    monkeypatch.setattr(bsc, "AsyncClient", _Faked)
    config = _connected_config()

    raw = EgeriaTech(view_server=config.egeria_view_server, platform_url=config.egeria_platform_url,
                     user_id="peterprofile", user_pwd="wrong").create_egeria_bearer_token("peterprofile", "wrong")
    assert raw == _ERROR_BODY, "pyegeria returns the error body as if it were a token"

    assert login_with_password("peterprofile", "wrong", config) is None
