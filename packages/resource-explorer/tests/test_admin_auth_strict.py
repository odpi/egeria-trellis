"""The shared admin check: identity comes from the signed-in JWT, and an unconfigured deployment fails closed.

Two weaknesses found 2026-10-14 (Opus review of re/dr-egeria-runner), owner ruling the same day:
  1. `is_admin_request` trusted a client-sent `X-Admin-User` header, not tied to the signed-in user's JWT `sub`:
     a non-admin who knew an admin's name could send it and pass.
  2. `require_admin` let any signed-in user through when no admin was configured. Now 403 "no admin configured",
     unless the deployment sets RE_ADMIN_OPEN_WHEN_UNCONFIGURED=true (local dev only).
`X-Admin-Token` (a server-held secret, not a name) keeps working: the admin pages send it.

Nothing in admin_auth or auth is faked here: real signed bearer tokens, the real config object. Only the work
behind each route (heal, bulk resolve, the outbox registry) is replaced so a pass writes nothing and never reaches
the shared registry.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from resource_explorer.auth import create_access_token
from resource_explorer.config import get_config
from resource_explorer.web.app import app


@pytest.fixture()
def client():
    return TestClient(app)


@pytest.fixture()
def admins(monkeypatch):
    """Set the deployment's admin configuration on the real config object."""
    fb = get_config().feedback

    def _set(users=(), token="", open_when_unconfigured=False):
        monkeypatch.setattr(fb, "admin_users", list(users), raising=False)
        monkeypatch.setattr(fb, "admin_token", token, raising=False)
        if open_when_unconfigured or "admin_open_when_unconfigured" in type(fb).model_fields:
            monkeypatch.setattr(fb, "admin_open_when_unconfigured", open_when_unconfigured, raising=False)

    return _set


def _as(user_id: str, **extra) -> dict:
    return {"Authorization": "Bearer " + create_access_token(user_id=user_id, egeria_token="t"), **extra}


@pytest.fixture(autouse=True)
def _no_side_effects():
    registry = MagicMock()
    registry.get_outbox_element.return_value = None
    registry.retry_outbox_element.return_value = True
    with patch("resource_explorer.bootstrap.check_and_heal", return_value={"batches": {}}), \
         patch("resource_explorer.bulk_ops.resolve_all", return_value=MagicMock(as_dict=lambda: {"results": []})), \
         patch("resource_explorer.web.routes.outbox.ProjectRegistry", return_value=registry):
        yield


def _bootstrap(client, headers):
    return client.post("/api/bootstrap/run", json={}, headers=headers)


def _resolve_all(client, headers):
    return client.post("/api/egeria/linkage/resolve-all",
                       json={"targets": [], "action": "discard", "dry_run": True}, headers=headers)


def _retry(client, headers):
    return client.post("/api/outbox/999999/retry", headers=headers)


def _feedback_list(client, headers):
    return client.get("/api/feedback", headers=headers)


def _curate_feedback(client, headers):
    return client.get("/api/curate/feedback", headers=headers)


REQUIRE_ADMIN_ROUTES = [_bootstrap, _resolve_all, _retry]
ALL_ADMIN_ROUTES = REQUIRE_ADMIN_ROUTES + [_feedback_list, _curate_feedback]


class TestXAdminUserHeaderIsNotIdentity:
    @pytest.mark.parametrize("call", ALL_ADMIN_ROUTES, ids=lambda f: f.__name__)
    def test_a_non_admin_naming_an_admin_in_the_header_is_refused(self, client, admins, call):
        admins(users=["alice"])
        assert call(client, _as("mallory", **{"X-Admin-User": "alice"})).status_code == 403

    def test_admin_status_does_not_believe_the_header(self, client, admins):
        admins(users=["alice"])
        body = client.get("/api/egeria/admin-status", headers=_as("mallory", **{"X-Admin-User": "alice"})).json()
        assert body["admin"] is False

    def test_an_anonymous_header_alone_is_refused(self, client, admins):
        admins(users=["alice"])
        assert _feedback_list(client, {"X-Admin-User": "alice"}).status_code == 403


class TestJwtSubInTheListIsAdmin:
    @pytest.mark.parametrize("call", ALL_ADMIN_ROUTES, ids=lambda f: f.__name__)
    def test_a_listed_sub_passes_without_any_header(self, client, admins, call):
        admins(users=["alice"])
        assert call(client, _as("alice")).status_code == 200

    def test_admin_status_reports_a_listed_sub(self, client, admins):
        admins(users=["alice"])
        assert client.get("/api/egeria/admin-status", headers=_as("alice")).json()["admin"] is True

    def test_list_entries_are_compared_trimmed(self, client, admins):
        admins(users=[" alice "])
        assert _bootstrap(client, _as("alice")).status_code == 200

    def test_a_listed_sub_sending_another_name_is_still_itself(self, client, admins):
        admins(users=["alice"])
        assert _bootstrap(client, _as("alice", **{"X-Admin-User": "nobody"})).status_code == 200


def _claims_of_a_real_token(sub: str) -> dict:
    import jwt
    return jwt.decode(create_access_token(user_id=sub, egeria_token="t"), options={"verify_signature": False})


def _forged(sub: str) -> dict:
    import jwt
    from resource_explorer.auth import auth_config
    tok = jwt.encode(_claims_of_a_real_token(sub), "not-the-server-secret-" * 2, algorithm=auth_config().jwt_algorithm)
    return {"Authorization": "Bearer " + tok}


def _expired(sub: str) -> dict:
    import time
    import jwt
    from resource_explorer.auth import auth_config
    cfg = auth_config()
    claims = {**_claims_of_a_real_token(sub), "iat": int(time.time()) - 7200, "exp": int(time.time()) - 3600}
    return {"Authorization": "Bearer " + jwt.encode(claims, cfg.jwt_secret, algorithm=cfg.jwt_algorithm)}


def _alg_none(sub: str) -> dict:
    import base64
    import json

    def b64(d):
        return base64.urlsafe_b64encode(json.dumps(d).encode()).rstrip(b"=").decode()
    return {"Authorization": "Bearer " + b64({"alg": "none", "typ": "JWT"}) + "." + b64(_claims_of_a_real_token(sub)) + "."}


class TestOnlyAVerifiedTokenIsIdentity:
    """Identity is read only from a token the server verifies: a listed name in a token it did not sign, or one
    that has expired, or one claiming alg=none, is refused (401 from the auth layer or 403 from the gate)."""

    @pytest.mark.parametrize("bad", [_forged, _expired, _alg_none], ids=lambda f: f.__name__)
    @pytest.mark.parametrize("call", ALL_ADMIN_ROUTES, ids=lambda f: f.__name__)
    def test_a_listed_sub_in_an_unverified_token_is_refused(self, client, admins, bad, call):
        admins(users=["alice"])
        with patch("resource_explorer.bootstrap.check_and_heal") as heal, \
             patch("resource_explorer.bulk_ops.resolve_all") as ra:
            assert call(client, bad("alice")).status_code in (401, 403)
        heal.assert_not_called()
        ra.assert_not_called()

    @pytest.mark.parametrize("bad", [_forged, _expired, _alg_none], ids=lambda f: f.__name__)
    def test_admin_status_does_not_count_an_unverified_token(self, client, admins, bad):
        admins(users=["alice"])
        res = client.get("/api/egeria/admin-status", headers=bad("alice"))
        assert res.status_code == 401 or res.json()["admin"] is False

    def test_the_bad_token_builders_are_well_formed_so_a_refusal_means_something(self):
        """Known-positive: the same claims, signed properly, ARE accepted — so each refusal above is about the
        signature/expiry/alg, not a malformed payload."""
        import jwt
        from resource_explorer.auth import auth_config, decode_token
        cfg = auth_config()
        good = jwt.encode(_claims_of_a_real_token("alice"), cfg.jwt_secret, algorithm=cfg.jwt_algorithm)
        assert decode_token(good)["sub"] == "alice"


class TestAdminTokenStillWorks:
    @pytest.mark.parametrize("call", ALL_ADMIN_ROUTES, ids=lambda f: f.__name__)
    def test_a_signed_in_user_with_the_token_passes(self, client, admins, call):
        admins(token="s3cret")
        assert call(client, _as("bob", **{"X-Admin-Token": "s3cret"})).status_code == 200

    def test_a_wrong_token_is_refused(self, client, admins):
        admins(token="s3cret")
        assert _bootstrap(client, _as("bob", **{"X-Admin-Token": "guess"})).status_code == 403


class TestUnconfiguredFailsClosed:
    @pytest.mark.parametrize("call", REQUIRE_ADMIN_ROUTES, ids=lambda f: f.__name__)
    def test_a_signed_in_user_is_refused_with_no_admin_configured(self, client, admins, call):
        admins()
        res = call(client, _as("bob"))
        assert res.status_code == 403
        assert "no admin configured" in res.json()["detail"]

    def test_nothing_runs_when_refused(self, client, admins):
        admins()
        with patch("resource_explorer.bootstrap.check_and_heal") as heal:
            assert _bootstrap(client, _as("bob")).status_code == 403
        heal.assert_not_called()

    @pytest.mark.parametrize("call", REQUIRE_ADMIN_ROUTES, ids=lambda f: f.__name__)
    def test_the_dev_flag_restores_open_access_for_signed_in_users(self, client, admins, call):
        admins(open_when_unconfigured=True)
        assert call(client, _as("bob")).status_code == 200

    def test_the_dev_flag_never_admits_an_anonymous_caller(self, client, admins):
        admins(open_when_unconfigured=True)
        assert _bootstrap(client, {}).status_code == 403

    def test_the_dev_flag_is_ignored_once_an_admin_is_configured(self, client, admins):
        admins(users=["alice"], open_when_unconfigured=True)
        assert _bootstrap(client, _as("bob")).status_code == 403

    @pytest.mark.parametrize("users,flag,want_open", [([], False, False), ([], True, True), (["alice"], True, False)])
    def test_admin_status_reports_open_so_the_ui_locks_what_the_server_refuses(self, client, admins, users, flag,
                                                                               want_open):
        admins(users=users, open_when_unconfigured=flag)
        assert client.get("/api/egeria/admin-status", headers=_as("bob")).json()["open"] is want_open

    def test_the_flag_defaults_off(self, monkeypatch):
        monkeypatch.delenv("RE_ADMIN_OPEN_WHEN_UNCONFIGURED", raising=False)
        from resource_explorer.config import FeedbackConfig
        assert FeedbackConfig(_env_file=None).admin_open_when_unconfigured is False

    def test_the_flag_reads_its_env_var(self, monkeypatch):
        monkeypatch.setenv("RE_ADMIN_OPEN_WHEN_UNCONFIGURED", "true")
        from resource_explorer.config import FeedbackConfig
        assert FeedbackConfig(_env_file=None).admin_open_when_unconfigured is True
