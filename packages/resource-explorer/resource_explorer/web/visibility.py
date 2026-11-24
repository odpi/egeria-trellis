"""TR-147: who may see an investigation, asked at the web layer, and the work lists that hang off one.

An investigation's data request and a work list linked to an investigation belong with that investigation: a creator-only investigation is
visible to its creator only (`registry._may_see_investigation`). The routes that expose or act on them used to ask only whether SOMEONE was
signed in (or nothing at all), so any signed-in user could read, or replace, another person's request.

The guard has two rules, in this order:
1. A signed-in caller FIRST. An anonymous caller gets 401 and never the shared identity "": that identity sees every investigation by design
   (it is how the service acts on a user's behalf), so falling back to it would show everything to a caller nobody identified.
2. Then `registry.get_investigation(slug, user_id=<that caller>)`. None means 404, with the SAME words as a slug that does not exist, so it
   never confirms that a private investigation is there.
"""
from __future__ import annotations

from fastapi import HTTPException, Request


def signed_in_user_id(request: Request | None = None) -> str:
    """The signed-in caller's id (the same identity the registry scopes by), or 401. Never "" and never the shared identity."""
    from resource_explorer.a2a_auth import caller

    identity = caller()
    user_id = (getattr(identity, "user_id", "") or "").strip()
    if identity is None or not user_id or getattr(identity, "auth_source", "") == "anonymous":
        raise HTTPException(status_code=401, detail="Sign in to continue.")
    return user_id


def visible_investigation_or_404(request: Request | None, slug: str, *, registry=None, not_found: str | None = None) -> dict:
    """The investigation `slug`, as the signed-in caller sees it; 401 for no caller; 404 for a slug that does not exist AND for one the
    caller may not see (identical words: `not_found`, else "No investigation '<slug>'.")."""
    user_id = signed_in_user_id(request)
    if registry is None:
        from resource_explorer.registry import ProjectRegistry

        registry = ProjectRegistry()
    inv = registry.get_investigation(slug, user_id=user_id) if slug else None
    if inv is None:
        raise HTTPException(status_code=404, detail=not_found or f"No investigation {slug!r}.")
    return inv


def visible_work_list_or_404(request: Request | None, slug: str, *, registry=None, not_found: str | None = None) -> dict | None:
    """The work list `slug` when the caller may see it, None when there is no such list (the route says its own 404). A list with NO
    investigation is visible to every signed-in person, as before. A list linked to an investigation the caller cannot see is a 404 with
    `not_found` (the words the route uses for a missing list), and a caller who is not signed in gets 401 for such a list."""
    from resource_explorer.work_lists import WorkLists

    wls = WorkLists(registry)
    wl = wls.get(slug) if slug else None
    if wl is None:
        return None
    investigation = (wl.get("investigation") or "").strip()
    if investigation:
        visible_investigation_or_404(request, investigation, registry=wls.registry,
                                     not_found=not_found or f"work list {slug!r} not found")
    return wl
