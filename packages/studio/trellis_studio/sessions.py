"""Studio session lifetime (TR-10, owner decision: "shorter TTL + refresh").

Two credentials per sign-in:
- the ACCESS token: a JWT living STUDIO_JWT_TTL_MINUTES (default 15), carrying `sid` (one per sign-in) and
  `auth_time`. It is what every request (and EA) sees.
- a REFRESH token: opaque, random, held by the page only, and known to Studio only by its hash (`RefreshStore`).
  It is bound to one session: its sid, its user (sub) and the Egeria token signed in with.

`POST /api/auth/refresh {refresh_token}` trades a refresh token for a new access token and a NEW refresh token.
Each refresh token works once (rotation). Presenting one that was already used is a replay: the whole session
(every access and refresh token of that sid) is revoked, and the answer is 401. A refresh is a fresh decision,
never a rubber stamp. Each of these must hold:
- the session isn't signed out or revoked;
- the identity can't change: the new access token is minted for the refresh token's own user, never one taken
  from the request, and an access token sent with the request must be for the same user and session, or the
  session is revoked;
- the session is younger than STUDIO_SESSION_MAX_HOURS (counted from `auth_time`, the original sign-in);
- its Egeria token hasn't expired;
- Egeria itself still accepts that token, asked as the user (`egeria.check_signin`). If Egeria refuses it
  (expired or revoked upstream), there is no refresh, and the session is revoked here as well. If Egeria
  can't be asked, the refresh token is left unused, so the page can try again.

Signing out revokes the sid: that session's access tokens are refused everywhere in Studio (the login gate and
`current_user`), and its refresh tokens are dropped. An access token with no `sid` wasn't minted by this Studio
and is refused too. An expired access token is a 401, never anonymous.

Limits, said plainly:
- Revocations and refresh tokens live in this process's memory. A restart forgets them: every refresh token
  stops working (the user signs in again), and a signed-out access token minted before the restart is accepted
  again until its own `exp` (at most STUDIO_JWT_TTL_MINUTES). Short access tokens are what bound this.
- EA verifies Studio's JWTs (owner decision A) but can't see this list. At EA, a signed-out Studio JWT works
  until its `exp`, and is refused after it.
- Egeria's own token caps every JWT (`create_access_token`), and Studio holds no password to renew it. So a
  session can't outlive the Egeria token it was signed in with (about an hour on laz), whatever the refresh
  settings say. A longer session needs Egeria-side token renewal.
"""
from __future__ import annotations

import hashlib
import secrets
import threading
from dataclasses import dataclass
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


@dataclass
class RefreshRecord:
    sid: str
    sub: str
    egeria_token: str
    role: str
    display_name: str
    auth_time: int
    expires: float
    used: bool = False


class RefreshStore:
    """Refresh tokens by their SHA-256 (the token itself is never kept). One live token per session: issuing a
    new one for a sid (rotation) leaves the old one known but used, so a replay of it is recognised."""

    def __init__(self) -> None:
        self._by_hash: Dict[str, RefreshRecord] = {}
        self._lock = threading.Lock()

    @staticmethod
    def _h(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    def issue(self, rec: RefreshRecord) -> str:
        token = secrets.token_urlsafe(32)
        with self._lock:
            self._prune()
            self._by_hash[self._h(token)] = rec
        return token

    def peek(self, token: str) -> Optional[RefreshRecord]:
        """The record for `token`, used or not; None if unknown or expired."""
        if not token:
            return None
        with self._lock:
            rec = self._by_hash.get(self._h(token))
            return rec if rec is not None and rec.expires > time.time() else None

    def consume(self, token: str) -> bool:
        """Mark `token` used. False if it was already used (a replay, or a concurrent use) or is unknown."""
        with self._lock:
            rec = self._by_hash.get(self._h(token))
            if rec is None or rec.used:
                return False
            rec.used = True
            return True

    def drop_session(self, sid: str) -> None:
        with self._lock:
            for h in [h for h, r in self._by_hash.items() if r.sid == sid]:
                del self._by_hash[h]

    def _prune(self) -> None:
        now = time.time()
        for h in [h for h, r in self._by_hash.items() if r.expires <= now]:
            del self._by_hash[h]


def refresh_store(app) -> RefreshStore:
    store = getattr(app.state, "refresh_store", None)
    if store is None:
        store = app.state.refresh_store = RefreshStore()
    return store


def start_session(app, user_id: str, egeria_token: str, cfg: AuthConfig, settings: StudioSettings, *,
                  role: str = "user", display_name: Optional[str] = None) -> Dict[str, Any]:
    """A new sign-in: an access token and its first refresh token, one new sid."""
    sid, auth_time = secrets.token_urlsafe(16), int(time.time())
    access = mint(user_id, egeria_token, cfg, settings, role=role, display_name=display_name, sid=sid,
                  auth_time=auth_time)
    refresh = refresh_store(app).issue(RefreshRecord(
        sid=sid, sub=user_id, egeria_token=egeria_token, role=role, display_name=display_name or user_id,
        auth_time=auth_time, expires=auth_time + settings.session_max_hours * 3600))
    return {"access_token": access, "refresh_token": refresh, "token_type": "bearer", "user_id": user_id}


def end_session(app, sid: str, settings: StudioSettings, *, auth_time: Optional[float] = None,
                exp: Optional[float] = None) -> None:
    """Revoke every access token of `sid` and drop its refresh tokens."""
    by_age = float(auth_time or time.time()) + settings.session_max_hours * 3600
    revocations(app).revoke(sid, max(by_age, float(exp or 0)))
    refresh_store(app).drop_session(sid)


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
