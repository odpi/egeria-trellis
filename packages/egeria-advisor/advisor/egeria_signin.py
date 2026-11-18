"""Refuse an Egeria call that has no credential of its own (Phase 0 S3b).

``trellis_auth.apply_token(client, token)`` mints a token from the client's
own ``user_id``/``user_pwd`` when ``token`` is empty. That is how the service
account authenticates, and it is fine when the password is the service
account's. But a caller with a user id and NEITHER a token NOR a password
(an expired or token-less session, or anonymous mode's "anonymous") would
reach the mint with an empty password, which pyegeria fills from its
configured one: the call would run under that user's id on the shared
password.

Each EA site that resolves credentials and builds a pyegeria client checks
``lacks_credential`` first and refuses with ``sign_in_to(...)``, the same
form as Dr.Egeria's "Sign in to run Dr.Egeria commands." (S3). The
service-account fallback, which has a password, is unchanged.
apply_token itself is not changed here (trellis-auth is S1's; a guarded
helper is Phase 4).
"""
from __future__ import annotations

from typing import Optional

SIGN_IN_TO_RUN_REPORTS = "Sign in to run reports."
SIGN_IN_TO_READ_EGERIA = "Sign in to read from Egeria."


def lacks_credential(token: Optional[str], password: Optional[str]) -> bool:
    """True when there is neither a bearer token nor a password to use."""
    return not token and not password
