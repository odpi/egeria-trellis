"""Confirm gates for EA's Dr.Egeria writes: show first, process only on an explicit confirm + digest.

Shared by the two places EA processes Dr.Egeria markdown it composed or holds itself:
  - chat actions (DrEgeriaActionAgent.handle / confirm), design note rev 4 §1e item 1;
  - LGCI governance plans (GovernancePlanAgent.prepare_execute / execute / retry).

The pattern, the same as B6 (spec publish) and B7b (dashboard authoring):
  1. SHOW: the exact text that would be processed is shown, and a pending action is registered with
     the SHA-256 of that text and who it was shown to (their credential identity).
  2. CONFIRM: the caller presents the pending id and the digest it was shown. Refused, in words, when
     the pending action is unknown or older than its TTL, belongs to another sign-in, or the digest is
     not the shown one. The caller then re-reads what it would process and refuses again if that is
     no longer the shown text (it changed underneath).
  3. ONCE: a pending action runs at most once; a second confirm answers with the first result. A run
     that failed part-way may have written part of the text, so it is consumed too.
"""
from __future__ import annotations

import hashlib
import secrets
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

UNKNOWN = "unknown"            # never issued, cancelled, or past the TTL
OTHER_SIGN_IN = "other_sign_in"
CHANGED = "changed"            # the digest presented is not the one shown


def text_digest(text: str) -> str:
    """The SHA-256 of the exact text shown, as confirmed."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def caller_identity(conn: Dict[str, str]) -> str:
    """Who a pending action belongs to: a hash of the credential PRESENTED, plus the Egeria it runs on.

    `conn` is DrEgeriaActionAgent._get_egeria_conn()'s result (auth, user_id, token or user_pass, url,
    server_name). Never a bare user_id: the dashboards' credential_identity rule."""
    secret = conn.get("token") or conn.get("user_pass") or ""
    raw = f"{conn.get('auth')}|{conn.get('user_id')}|{secret}|{conn.get('url')}|{conn.get('server_name')}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


@dataclass
class Pending:
    pending_id: str
    identity: str
    digest: str
    payload: Dict[str, Any]
    created: float = field(default_factory=lambda: time.monotonic())
    result: Optional[Dict[str, Any]] = None
    lock: threading.Lock = field(default_factory=threading.Lock)


class ConfirmGate:
    """Pending actions for one kind of write, held in this process (a restart forgets them: show again)."""

    def __init__(self, ttl_s: int):
        self.ttl_s = ttl_s
        self.pending: Dict[str, Pending] = {}
        self._lock = threading.Lock()

    def register(self, identity: str, text: str, payload: Optional[Dict[str, Any]] = None) -> Pending:
        p = Pending(secrets.token_urlsafe(16), identity, text_digest(text), dict(payload or {}))
        with self._lock:
            now = time.monotonic()
            for k in [k for k, v in self.pending.items() if now - v.created > self.ttl_s]:
                self.pending.pop(k, None)
            self.pending[p.pending_id] = p
        return p

    def check(self, pending_id: Any, identity: str, digest: Any) -> "tuple[Optional[Pending], Optional[str]]":
        """(the pending action, None) when it may run, else (None, UNKNOWN | OTHER_SIGN_IN | CHANGED)."""
        with self._lock:
            p = self.pending.get(pending_id) if isinstance(pending_id, str) else None
        if p is None or time.monotonic() - p.created > self.ttl_s:
            return None, UNKNOWN
        if p.identity != identity:
            return None, OTHER_SIGN_IN
        if not isinstance(digest, str) or not secrets.compare_digest(digest, p.digest):
            return None, CHANGED
        return p, None

    def discard(self, pending_id: Any) -> None:
        with self._lock:
            self.pending.pop(pending_id if isinstance(pending_id, str) else "", None)

    def clear(self) -> None:
        with self._lock:
            self.pending.clear()
