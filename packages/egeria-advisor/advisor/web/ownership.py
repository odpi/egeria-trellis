"""Who is asking, for per-user artifacts (SS-4, A-I1 2026-11-11).

Plans, drafts and planning-session transcripts are namespaced per user: each user sees the shared
namespace (artifacts with no owner, including everything written before namespacing) plus their own.
An admin sees every namespace. Something that isn't visible is a 404, never a 403, so the reply does not
confirm that another user's id exists.

"Admin" here is the one shared gate (advisor.auth.is_admin, trellis_auth.require_admin, Phase 0 S1).
It maps onto the doc and draft managers' existing curator view, so a role of "admin" or "curator" in the
session JWT keeps the view it already had. Anonymous mode's stand-in user is no user: shared namespace only.
"""
from __future__ import annotations

from typing import Optional, Tuple

from fastapi import Request

ADMIN_ROLE = "admin"


def requester(request: Request) -> Tuple[Optional[str], Optional[str]]:
    """(user_id, role) for ownership checks: user_id None when anonymous or signed out; role "admin" when
    the shared admin gate admits this request, else the session's own role."""
    from advisor.auth import get_current_user, is_admin, is_anonymous
    user = get_current_user(request)
    if not user or is_anonymous(user):
        return None, None
    user_id = user.get("user_id") or user.get("sub") or None
    role = ADMIN_ROLE if is_admin(request) else user.get("role")
    return user_id, role


def can_see(owner: Optional[str], user_id: Optional[str], role: Optional[str]) -> bool:
    """Shared (owner None) is visible to all; an owned artifact to its owner, and to an admin or curator."""
    if owner is None:
        return True
    if (role or "").lower() in ("admin", "curator"):
        return True
    return user_id is not None and owner == user_id
