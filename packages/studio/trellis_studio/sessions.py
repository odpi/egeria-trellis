"""Studio session lifetime (TR-10, owner decision: "shorter TTL + refresh").

A Studio session JWT lives STUDIO_JWT_TTL_MINUTES (default 15). While the user is active, the page asks
`POST /api/auth/refresh` for a new one before it expires. A refresh is a fresh decision, never a rubber stamp:

- the JWT presented must still be valid, unexpired and not signed out;
- the session must be younger than STUDIO_SESSION_MAX_HOURS (counted from `auth_time`, the original sign-in);
- the Egeria token it carries must not have expired;
- and Egeria itself must still accept that token, asked as the user (`egeria.check_signin`). If Egeria refuses it
  (expired or revoked upstream), there is no refresh, and the session is signed out here as well.

Every JWT Studio mints carries a `sid` (one per sign-in, kept across refreshes) and `auth_time`. Signing out
revokes the `sid`. From then on, that session's JWTs are refused everywhere in Studio (the login gate and
`current_user`) and can't be refreshed. A JWT with no `sid` was not minted by this Studio and is refused too.

Limits, said plainly:
- The revocation list lives in this process's memory. A restart forgets it, so a signed-out JWT minted before the
  restart is accepted again until its own `exp` (at most STUDIO_JWT_TTL_MINUTES). Short JWTs are what bound this.
- EA verifies Studio's JWTs (owner decision A) but can't see this list. At EA, a signed-out Studio JWT works
  until its `exp`, and is refused after it.
- Egeria's own token caps every JWT (`create_access_token`), and Studio holds no password to renew it. So a
  session can't outlive the Egeria token it was signed in with (about an hour on laz), whatever the refresh
  settings say. A longer session needs Egeria-side token renewal.
"""
from __future__ import annotations

import secrets
import threading
import time
from datetime import timedelta
from typing import Any, Dict, Optional

import trellis_auth
from trellis_auth import AuthConfig, LoginRequiredMiddleware

from trellis_studio.config import StudioSettings


class Revocations:
    """Signed-out session ids, each kept until the latest time a JWT of that session could still verify."""

    def __init__(self) -> None:
        self._until: Dict[str, float] = {}
        self._lock = threading.Lock()

    def revoke(self, sid: str, until: float) -> None:
        with self._lock:
            self._until[sid] = max(until, self._until.get(sid, 0.0))
            self._prune()

    def is_revoked(self, sid: str) -> bool:
        with self._lock:
            until = self._until.get(sid)
            return until is not None and until > time.time()

    def _prune(self) -> None:
        now = time.time()
        for sid in [s for s, u in self._until.items() if u <= now]:
            del self._until[sid]


def revocations(app) -> Revocations:
    rev = getattr(app.state, "revocations", None)
    if rev is None:
        rev = app.state.revocations = Revocations()
    return rev


def usable(app, user: Optional[Dict[str, Any]]) -> bool:
    """A decoded JWT is a usable Studio session: Studio minted it (it has a `sid`) and it wasn't signed out."""
    if not user:
        return False
    sid = user.get("sid")
    return isinstance(sid, str) and bool(sid) and not revocations(app).is_revoked(sid)


def mint(user_id: str, egeria_token: str, cfg: AuthConfig, settings: StudioSettings, *, role: str = "user",
         display_name: Optional[str] = None, sid: Optional[str] = None,
         auth_time: Optional[int] = None) -> str:
    """A session JWT: trellis-auth's claims plus `sid` and `auth_time`, living STUDIO_JWT_TTL_MINUTES. It never
    outlives the Egeria token (trellis-auth caps it), or the session's maximum age."""
    auth_time = int(auth_time if auth_time is not None else time.time())
    ttl = timedelta(minutes=settings.jwt_ttl_minutes)
    left = auth_time + settings.session_max_hours * 3600 - time.time()
    if left <= 0:
        raise ValueError("the session is past its maximum age")
    ttl = min(ttl, timedelta(seconds=left))
    return trellis_auth.create_access_token(
        user_id, egeria_token, cfg, role=role, display_name=display_name or user_id, ttl=ttl,
        extra_claims={"sid": sid or secrets.token_urlsafe(16), "auth_time": auth_time})


def revoke_until(user: Dict[str, Any], settings: StudioSettings) -> float:
    """The latest time any JWT of `user`'s session could still verify: its maximum age, or its own exp."""
    by_age = float(user.get("auth_time") or 0) + settings.session_max_hours * 3600
    return max(by_age, float(user.get("exp") or 0))


class StudioLoginMiddleware(LoginRequiredMiddleware):
    """trellis-auth's login gate, but a JWT must also be a usable Studio session (it has a `sid`, and it
    wasn't signed out). A signed-out JWT gets the same 401 as no JWT, on every non-public path."""

    def __init__(self, app, config: AuthConfig, policy, revoked: Revocations) -> None:
        super().__init__(app, config=config, policy=policy)
        self.revoked = revoked

    def _has_valid_token(self, scope) -> bool:
        if not super()._has_valid_token(scope):
            return False
        for key, value in scope.get("headers") or ():
            if key.lower() == b"authorization":
                token = value.decode("latin-1")[len("Bearer "):].strip()
                try:
                    claims = trellis_auth.decode_token(token, self.config)
                except Exception:  # noqa: BLE001 -- the parent check passed; anything odd is no session
                    return False
                sid = claims.get("sid")
                return isinstance(sid, str) and bool(sid) and not self.revoked.is_revoked(sid)
        return False
