"""First index, asked first: the figures, a yes that names a repository, taking a yes back, and the progress list.

The work is queued through the run queue as kind `first_index` with the requester recorded (see `first_index.py`). This
router writes nothing else and starts nothing itself.
"""
from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from resource_explorer import first_index as fi
from resource_explorer.run_queue import requested_by as _requested_by

log = logging.getLogger(__name__)
router = APIRouter()


def _registry():
    from resource_explorer.registry import ProjectRegistry

    return ProjectRegistry()


@router.get("/ask")
def ask(slug: list[str] = Query(default_factory=list)) -> dict:
    """The figures the ask shows for these repositories. Measured ones say which survey and when; estimated ones are
    `null` (with the words "unknown until the first index") unless the indexed repositories hold enough to estimate from."""
    if not slug:
        raise HTTPException(status_code=400, detail="Name at least one repository (slug=...).")
    return fi.ask_figures(_registry(), list(dict.fromkeys(slug)))


class QueueBody(BaseModel):
    #: Each slug here is a yes that names it. The route queues exactly these and nothing else.
    slugs: list[str] = Field(min_length=1)
    #: One id for the dialog that asked; "" for the single-repository ask.
    batch_id: str = ""
    #: How many repositories the dialog offered (the "of 12").
    batch_total: int = 0
    #: "single" | "all" | "each": which button the person used.
    path: str = "single"


@router.post("/queue")
def queue(body: QueueBody) -> dict:
    """Queue first indexes for the named repositories. Each is its own run row, in the order given.

    A refused one (already indexed, already queued, unknown) comes back in `refused` with its sentence; the rest are
    queued regardless. A request with nobody signed in is refused whole: a first index needs a person's recorded yes.
    """
    who = _requested_by()
    if not who.strip():
        raise HTTPException(status_code=401, detail=fi.NO_REQUESTER_REFUSAL)
    if body.path not in ("single", "all", "each"):
        raise HTTPException(status_code=400, detail="path must be 'single', 'all' or 'each'")
    registry = _registry()
    batch_id = body.batch_id or (str(uuid.uuid4()) if len(body.slugs) > 1 or body.path != "single" else "")
    queued, refused = [], []
    for slug in dict.fromkeys(body.slugs):
        try:
            queued.append({"slug": slug, **fi.queue(registry, slug, requested_by=who, batch_id=batch_id,
                                                    batch_total=body.batch_total or len(body.slugs), path=body.path)})
        except fi.FirstIndexRefused as exc:
            refused.append({"slug": slug, "reason": str(exc), "status": exc.status})
    log.info("first index: %s said yes to %d (refused %d), batch %s, path %s", who, len(queued), len(refused),
             batch_id or "-", body.path)
    return {"queued": queued, "refused": refused, "batch_id": batch_id}


@router.post("/withdraw/{run_id}")
def withdraw(run_id: str) -> dict:
    """Take a yes back. Allowed only until the repository has started; after that nothing interrupts it (409)."""
    if not fi.withdraw(_registry(), run_id):
        raise HTTPException(status_code=409, detail="That repository has already started indexing, or was never queued.")
    return {"run_id": run_id, "state": "withdrawn"}


@router.get("/progress")
def progress() -> dict:
    """The First indexing list: waiting, indexing, indexed, failed per repository, and "N of M done". From run rows."""
    return fi.progress(_registry())
