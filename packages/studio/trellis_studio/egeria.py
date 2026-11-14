"""The one way Studio builds a pyegeria client: for the signed-in user, from the bearer token in their session.

No user, no token, no client: `user_client` raises 401 before pyegeria is touched. The client never holds a
password (S0 read-back R1): pyegeria fills a missing password from its settings default (`user_pwd or
settings.User_Profile.user_pwd`), on the client and on every sub-client it builds lazily, and a client holding a
password may re-sign-in after a 401, which is the shape of the 2026-10-28 identity switch (an expired user token
silently re-signed as the demo persona). So Studio passes user_pwd=None, scrubs the password from the client and
from each sub-client as it is built, and asserts none is left. A rejected token surfaces as
PyegeriaUnauthorizedException, which the app answers with 401. Nothing is cached across requests or users; every
client is closed when its request is done (`close`).
"""
from __future__ import annotations

from typing import Any, Dict

from trellis_studio.auth import egeria_token_or_401, user_id_of
from trellis_studio.config import StudioSettings


def _client_class():
    from pyegeria.egeria_tech_client import EgeriaTech
    return EgeriaTech


class PasswordOnClient(RuntimeError):
    """A Studio client must never hold a password; building one that does is refused."""


def _scrub(client: Any) -> None:
    client.user_pwd = None
    get_sub = getattr(client, "_get_subclient", None)
    if get_sub is None:
        return

    def scrubbed(name: str, _get=get_sub):
        sub = _get(name)
        sub.user_pwd = None
        return sub
    client._get_subclient = scrubbed           # an instance attribute shadows the class method
    for sub in (getattr(client, "_instantiated_clients", None) or {}).values():
        sub.user_pwd = None


def assert_no_password(client: Any) -> None:
    holders = [client, *((getattr(client, "_instantiated_clients", None) or {}).values())]
    if any(getattr(c, "user_pwd", None) for c in holders):
        raise PasswordOnClient("A Studio Egeria client holds a password; refused (S0 R1).")


def user_client(user: Dict[str, Any], settings: StudioSettings) -> Any:
    token = egeria_token_or_401(user)
    cls = _client_class()
    client = cls(view_server=settings.egeria_view_server, platform_url=settings.egeria_platform_url,
                 user_id=user_id_of(user), user_pwd=None)
    _scrub(client)
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
