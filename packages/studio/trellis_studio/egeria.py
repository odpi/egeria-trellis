"""The one way Studio builds a pyegeria client: for the signed-in user, from the bearer token in their session.

No user, no token, no client: `user_client` raises 401 before pyegeria is touched. The client never holds a
password (S0 read-back R1): a client holding a password may re-sign-in after a 401, which is the shape of the
2026-10-28 identity switch (an expired user token silently re-signed as the demo persona). Studio passes
user_pwd=NO_PASSWORD, pyegeria's explicit opt-out of its configured default password (6.2.42, the floor): the
client records use_default_password=False and every sub-client and helper client it builds carries NO_PASSWORD
too (`helper_client`, `carried_password`), so nothing is scrubbed after the fact; `assert_no_password` still
refuses a client that holds one. A rejected token surfaces as PyegeriaUnauthorizedException, which the app
answers with 401. Nothing is cached across requests or users; every client is closed when its request is done
(`close`).
"""
from __future__ import annotations

from typing import Any, Dict

from pyegeria.core._no_password import NO_PASSWORD
from pyegeria.view import egeria_spec_cache

from trellis_studio.auth import egeria_token_or_401, user_id_of
from trellis_studio.config import StudioSettings


def _client_class():
    from pyegeria.egeria_tech_client import EgeriaTech
    return EgeriaTech


def bound_to(client: Any):
    """Every report-spec read made with `client` is for its user (`egeria_spec_cache.as_client`): Studio is
    declared multi-user, so a read with no identity sees no private spec."""
    return egeria_spec_cache.as_client(client)


def declare_multi_user_process() -> None:
    """Studio serves many users: declare it to pyegeria's spec cache (spec-cache R-A), so a report-spec read
    made with no user's identity takes no fallback identity and sees no private spec."""
    egeria_spec_cache.declare_multi_user()


class PasswordOnClient(RuntimeError):
    """A Studio client must never hold a password; building one that does is refused."""


def assert_no_password(client: Any) -> None:
    holders = [client, *((getattr(client, "_instantiated_clients", None) or {}).values())]
    if any(getattr(c, "user_pwd", None) for c in holders):
        raise PasswordOnClient("A Studio Egeria client holds a password; refused (S0 R1).")


def user_client(user: Dict[str, Any], settings: StudioSettings) -> Any:
    token = egeria_token_or_401(user)
    cls = _client_class()
    client = cls(view_server=settings.egeria_view_server, platform_url=settings.egeria_platform_url,
                 user_id=user_id_of(user), user_pwd=NO_PASSWORD)
    client.set_bearer_token(token)
    assert_no_password(client)
    return client


def close(client: Any) -> None:
    """Close the client's HTTP sessions (sd-dcat read-back: every request's client is closed)."""
    fn = getattr(client, "close_session", None)
    if fn is None:
        return
    try:
        fn()
    except Exception:  # noqa: BLE001 -- closing never turns a request's answer into an error
        pass


class SignInRefused(RuntimeError):
    """Egeria no longer accepts the user's token (expired or revoked upstream)."""


def check_signin(user: Dict[str, Any], settings: StudioSettings) -> None:
    """Ask Egeria, as the user, whether it still accepts their token (TR-10's refresh check): the cheapest read as
    them, `get_my_profile`, on a client with no password (so a refused token can't be quietly re-signed). Returns
    if it does; raises SignInRefused if Egeria refuses it; anything else (Egeria unreachable) propagates as is,
    because "couldn't ask" isn't "refused". Run it in a worker thread: it's a sync pyegeria call."""
    from pyegeria.core._exceptions import PyegeriaUnauthorizedException
    client = user_client(user, settings)
    try:
        client.get_my_profile()
    except PyegeriaUnauthorizedException as exc:
        raise SignInRefused(str(exc)) from exc
    finally:
        try:
            assert_no_password(client)
        finally:
            close(client)
