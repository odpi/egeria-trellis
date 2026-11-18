"""A pyegeria client for the request's own user, from their own Egeria bearer token, holding no password.

Ruling RULING-EA-V2-SERVICE-USER-CLIENT-2026-11-17 A1: the client is built per request from the CURRENT
request's token (the session JWT's `egeria_token`, as `advisor.auth.get_egeria_credentials` gives it), with
`user_pwd=NO_PASSWORD`, so a 401 can never re-sign-in as anyone (the 2026-10-28 identity-switch shape). Never
the service account: no token, no user, or a service-account user gives None, and the caller says "sign in"
without any Egeria call and without a fallback.
"""
from __future__ import annotations

from typing import Any, Dict, Optional


class PasswordOnClient(RuntimeError):
    """A user client ended up holding a password (refused)."""


def assert_no_password(client: Any) -> None:
    holders = [client, *((getattr(client, "_instantiated_clients", None) or {}).values())]
    if any(getattr(c, "user_pwd", None) for c in holders):
        raise PasswordOnClient("An Egeria client for a signed-in user holds a password; refused.")


def usable_credentials(creds: Optional[Dict[str, str]]) -> bool:
    """A user id and a bearer token, and not a service account."""
    from advisor.auth import is_service_account
    return bool(creds and creds.get("token") and creds.get("user_id")) and not is_service_account(creds["user_id"])


def build_user_client(creds: Optional[Dict[str, str]]) -> Optional[Any]:
    """An EgeriaTech client as `creds["user_id"]`, bearer token applied, no password; None when `creds` can't
    give one (no token or user, or a service account). Building it makes no Egeria call."""
    if not usable_credentials(creds):
        return None
    from advisor.auth import apply_token
    from advisor.mcp_config import get_pyegeria_platform_config
    from pyegeria.core._no_password import NO_PASSWORD
    from pyegeria.egeria_tech_client import EgeriaTech
    conn = get_pyegeria_platform_config()
    client = EgeriaTech(view_server=conn["view_server"], platform_url=conn["platform_url"], user_id=creds["user_id"],
                        user_pwd=NO_PASSWORD)
    apply_token(client, creds["token"])
    assert_no_password(client)
    assert_identity(client, creds["user_id"])
    # The CLIENT, never pyegeria's as_client(...): that is a context manager for `with` (bound_reads below).
    # Returned as the client, a run read no identity from it and pyegeria fell back to the configured service
    # account (U3 2026-11-17, ruling RULING-EA-U3-IDENTITY-SWITCH-bbc70228).
    return client


class WrongIdentity(RuntimeError):
    """A user client that doesn't name the request's user, or carries no bearer token (refused)."""


def assert_identity(client: Any, user_id: str) -> None:
    """`client` is a client for `user_id` holding a bearer token: what pyegeria's runs read from it."""
    if getattr(client, "user_id", None) != user_id or not getattr(client, "token", None):
        raise WrongIdentity("An Egeria client built for a signed-in user doesn't carry that user's identity; refused.")


def bound_reads(client: Any):
    """`with bound_reads(client):` -- every report-spec read inside is for `client`'s user (pyegeria's
    egeria_spec_cache.as_client, a context manager), so a multi-user process never reads as the fallback."""
    from pyegeria.view.egeria_spec_cache import as_client
    return as_client(client)


def close_client(client: Any) -> None:
    fn = getattr(client, "close_session", None)
    if fn is None:
        return
    try:
        fn()
    except Exception:  # noqa: BLE001 -- closing never changes the answer
        pass
