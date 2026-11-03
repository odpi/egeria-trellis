"""Bootstrap status + manual run — backs the reinitializing banner and Admin.

Read-mostly. The automatic path (startup check + periodic loop) lives in
resource_explorer/bootstrap.py and needs no HTTP surface; these routes exist so
the UI can say "definitions are being restored" instead of showing a silently
degraded Survey tab, and so an admin can trigger a pass without restarting.
"""
from __future__ import annotations

import asyncio
import time

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from resource_explorer import bootstrap as bootstrap_mod
from resource_explorer.web import admin_auth

router = APIRouter()



class RunRequest(BaseModel):
    force: bool = False
    """Re-run every batch regardless of canary presence.

    Defaults to False, and the UI must confirm before sending True: for a batch
    with idempotent=false (RE's survey definitions) a forced re-run duplicates
    every step link and takes those Survey Definitions out of service until the
    reconciler runs. This is the exact failure the missing-only gate prevents on
    the automatic path, so the manual path has to ask rather than assume.
    """


@router.get("/status")
async def bootstrap_status() -> dict:
    """Per-batch presence/heal state. Cheap — reads in-process state, does not
    hit Egeria, so the banner can poll it without adding load."""
    return bootstrap_mod.get_status()


@router.post("/run")
async def bootstrap_run(request: Request, body: RunRequest | None = None) -> dict:
    """Run a check-and-heal pass now. Admin only; one at a time.

    409 while a heal is already running (a manual one holding the lock, or the automatic loop, which sets
    `_reinitializing`): two heals at once would race the same batches, and a batch that is not idempotent
    duplicates its step links. Blocking work (subprocess dr_egeria calls) is pushed to a thread, matching the
    asyncio.to_thread pattern the other long-running routes use.
    """
    admin_auth.require_admin(request)
    force = bool(body and body.force)
    if bootstrap_mod._reinitializing:
        raise HTTPException(status_code=409, detail="A bootstrap heal is already running")
    try:
        out = await asyncio.to_thread(bootstrap_mod.check_and_heal, bootstrap_mod.DOCS_DIR, force)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"bootstrap run failed: {exc}")
    if out.get("running"):       # lost the lock inside check_and_heal: another pass (manual or automatic) holds it
        raise HTTPException(status_code=409, detail="A bootstrap heal is already running")
    return out


def _require_listed_admin(request: Request) -> str:
    """The signed-in user's `sub`, when it is in the configured admin list (FEEDBACK_ADMIN_USERS); else 403.

    Fails CLOSED: with no admin list configured nobody passes (an admin token alone does not count, since it
    names no person). Identity comes only from the signed-in session's JWT; X-Admin-User and X-Admin-Token
    headers are ignored, because a client chooses what it sends in them."""
    from resource_explorer.auth import get_current_user
    from resource_explorer.config import get_config

    user = get_current_user(request)
    if user is None:
        raise HTTPException(status_code=403, detail="Sign in to do this")
    admins = {str(a).strip() for a in (get_config().feedback.admin_users or []) if str(a).strip()}
    if not admins:
        raise HTTPException(status_code=403, detail="no admin configured (FEEDBACK_ADMIN_USERS is empty)")
    sub = str(user.get("sub") or "").strip()
    if not sub or sub not in admins:
        raise HTTPException(status_code=403, detail="Admin only: your sign-in is not in the admin list")
    return sub


class DrEgeriaRunRequest(BaseModel):
    path: str
    """A .md document under docs/dr-egeria/ (relative to it). Anything else is refused with 400."""
    dry_run: bool = False
    """Parse and validate, list the commands, write nothing."""
    force: bool = False
    """Run a document whose content was already applied. Commands marked not safe to repeat may duplicate
    relationships, so the UI must confirm before sending True."""


@router.post("/dr-egeria/run")
async def dr_egeria_run(request: Request, body: DrEgeriaRunRequest) -> dict:
    """Run one Dr.Egeria document ad hoc, as the signed-in admin's own Egeria session (their bearer token via
    `Caller()`, run by `dr_egeria_child`), never RE's service account. Admin only, dry runs included, and admin
    here means `_require_listed_admin`: the signed-in JWT's `sub` is in FEEDBACK_ADMIN_USERS. Stricter than
    `admin_auth.require_admin`, which also accepts the shared X-Admin-Token and, with RE_ADMIN_OPEN_WHEN_UNCONFIGURED
    set, any signed-in user: a run is attributed to a named person, and a token names no one.

    Calls `dr_egeria_runner.run_document`, the same function as `resource-explorer dr-egeria run`. 400 for a path
    outside docs/dr-egeria/ or an invalid document; 409 for a repeat without `force` or a run already going.
    Blocking work (the pyegeria steps and the dr_egeria subprocess) runs in a thread.
    """
    sub = _require_listed_admin(request)
    from resource_explorer import dr_egeria_runner as runner
    from resource_explorer.bootstrap import SOURCE_ADMIN_SESSION, DrEgeriaIdentity
    from resource_explorer.egeria_clients import (EXPIRED_SENTENCE, Caller, DaemonSignInRefused, _token_expiry,
                                                  refuse_daemon_sign_in)

    identity = None
    if not body.dry_run:
        try:
            who = Caller()          # the signed-in admin's own Egeria session; never the daemon, never a default
        except PermissionError as exc:
            raise HTTPException(status_code=401, detail=str(exc))
        if not (who.user_id or "").strip():
            raise HTTPException(status_code=401, detail=EXPIRED_SENTENCE)
        try:
            refuse_daemon_sign_in(who.user_id)      # RE's own service account is never a person's session
        except DaemonSignInRefused as exc:
            raise HTTPException(status_code=403, detail=str(exc))
        # A document run can last HEAL_TIMEOUT_SECONDS and the token cannot be refreshed mid-run: refuse up front
        # when it would expire inside that window, instead of failing partway through a document.
        exp = _token_expiry(who.token or "")
        if exp is not None and exp - time.time() < bootstrap_mod.HEAL_TIMEOUT_SECONDS:
            raise HTTPException(status_code=401, detail=EXPIRED_SENTENCE)
        identity = DrEgeriaIdentity(user=who.user_id, source=SOURCE_ADMIN_SESSION, token=who.token or "")
    try:
        return await asyncio.to_thread(runner.run_document, body.path, dry_run=body.dry_run, force=body.force,
                                       requested_by=sub, identity=identity)
    except runner.DocumentRefused as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except runner.NoIdentity as exc:
        raise HTTPException(status_code=401, detail=str(exc))
    except runner.RunRefused as exc:
        raise HTTPException(status_code=409, detail=str(exc))
