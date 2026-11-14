"""The one way Studio builds a pyegeria client: for the signed-in user, from the bearer token in their session.

No user, no token, no client: `user_client` raises 401 before pyegeria is touched. There is no fallback to
configured credentials (trellis_auth.apply_token would fall back to the client's own user_id/password if handed
an empty token, so Studio never calls it with one). Nothing is cached across requests or users.
"""
from __future__ import annotations

from typing import Any, Dict

from trellis_studio.auth import egeria_token_or_401, user_id_of
from trellis_studio.config import StudioSettings


def _client_class():
    from pyegeria.egeria_tech_client import EgeriaTech
    return EgeriaTech


def user_client(user: Dict[str, Any], settings: StudioSettings) -> Any:
    token = egeria_token_or_401(user)
    cls = _client_class()
    client = cls(view_server=settings.egeria_view_server, platform_url=settings.egeria_platform_url,
                 user_id=user_id_of(user))
    client.set_bearer_token(token)
    return client
