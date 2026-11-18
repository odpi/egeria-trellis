"""Rebuild a repository's chat index from scratch: queue, progress, accept or cancel (the held first rebuild), undo.

BRIEF-REINDEX-FROM-SCRATCH.md §7. Admins only (Q3). The work runs through the run queue as kind `reindex`
(`reindex.py`); this router starts nothing itself and writes nothing but the queue row, the accept and the undo.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from resource_explorer import reindex
from resource_explorer.web.admin_auth import require_admin

log = logging.getLogger(__name__)

router = APIRouter()


def _registry():
    from resource_explorer.registry import ProjectRegistry

    return ProjectRegistry()


def _who(request: Request) -> str:
    """The admin who said yes: the signed-in person, else the shared admin token's name."""
    from resource_explorer.run_queue import requested_by
    from resource_explorer.web.admin_auth import _signed_in_sub

    return requested_by() or _signed_in_sub(request) or "admin-token"


def _refused(exc: reindex.ReindexRefused) -> HTTPException:
    return HTTPException(status_code=exc.status, detail=str(exc))


class QueueBody(BaseModel):
    slug: str


@router.get("/ask")
def ask(slug: str, request: Request) -> dict:
    """What a rebuild of `slug` would do, before anyone says yes: the collections, the measured space, whether it is
    the held first rebuild. `refused` says why one cannot be queued now."""
    require_admin(request)
    return reindex.ask(_registry(), slug)


@router.post("/queue")
def queue(body: QueueBody, request: Request) -> dict:
    """Queue a rebuild of one repository. The first rebuild ever is held for an admin to accept before it swaps."""
    require_admin(request)
    try:
        out = reindex.queue(_registry(), body.slug, requested_by=_who(request))
    except reindex.ReindexRefused as exc:
        raise _refused(exc) from exc
    log.info("rebuild of %s queued by %s (held=%s)", body.slug, _who(request), out["held"])
    return out


@router.get("/progress")
def progress(slug: str, request: Request) -> dict:
    """The latest rebuild of `slug`: its phase, each step's words, the counts, the survey writers, the undo window."""
    require_admin(request)
    return reindex.progress(_registry(), slug)


@router.post("/{run_id}/accept")
def accept(run_id: str, request: Request) -> dict:
    """Accept a held rebuild: a continuation run does the survey writes and the swap."""
    require_admin(request)
    try:
        return reindex.accept(_registry(), run_id, requested_by=_who(request))
    except reindex.ReindexRefused as exc:
        raise _refused(exc) from exc


@router.post("/{run_id}/cancel")
def cancel(run_id: str, request: Request) -> dict:
    """Cancel a rebuild held for accept: drop what it built. The live index was never changed."""
    require_admin(request)
    try:
        return reindex.cancel_held(_registry(), run_id, by=_who(request))
    except reindex.ReindexRefused as exc:
        raise _refused(exc) from exc


@router.post("/{slug}/undo")
def undo(slug: str, request: Request) -> dict:
    """Restore the previous index of `slug`'s last rebuild, within its 7 days."""
    require_admin(request)
    try:
        return reindex.undo(_registry(), slug, requested_by=_who(request))
    except reindex.ReindexRefused as exc:
        raise _refused(exc) from exc


@router.post("/{slug}/discard-previous")
def discard_previous(slug: str, request: Request) -> dict:
    """Drop the previous index of `slug`'s last rebuild now, ending its undo window early."""
    require_admin(request)
    try:
        return reindex.discard_previous(_registry(), slug, by=_who(request))
    except reindex.ReindexRefused as exc:
        raise _refused(exc) from exc
