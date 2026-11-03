"""Curate's publish table (declared extensibility slice 1, PR 2): read the rows, save a signed Publish | Keep in RE
choice per declared measure. Nothing here calls Egeria; the press is the database's catalogue commit
(`catalogue_scope.py`), which sends the chosen measures inside RE's survey report. See `publish_table.py`."""
from __future__ import annotations

import asyncio
import logging
import time

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from resource_explorer import publish_table as pt
from resource_explorer.registry import ProjectRegistry
from resource_explorer.web.routes.catalogue_scope import _authorize, _require_author

router = APIRouter()
log = logging.getLogger(__name__)


def _one_read(what: str, slug: str, fn, *args, **kwargs):
    """F38: run `fn` inside ONE db_derived read, so the database's derivations are recomputed once for the whole
    table, not once per derived measure (17 times on coco_pharma: 24-52 s, the same for 4 tables or 266), and log
    what it cost. Opened inside the worker thread: a context variable set outside `to_thread` is not the one read."""
    from resource_explorer.surveyors.database import db_derived

    t0 = time.perf_counter()
    with db_derived.one_read():
        try:
            return fn(*args, **kwargs)
        finally:
            memo = db_derived._READ_MEMO.get() or {}
            log.info("publish_table_timing what=%s slug=%s total_ms=%.0f derived_runs=%d peer_reads=%d", what, slug,
                     (time.perf_counter() - t0) * 1000, sum(1 for k in memo if k[0] == "run"),
                     sum(1 for k in memo if k[0] == "peer"))


class ChoiceBody(BaseModel):
    analysis_id: str
    measure: str
    choice: str


def _registry_for(entity_type: str, slug: str) -> ProjectRegistry:
    registry = ProjectRegistry()
    if entity_type == "database":
        found = registry.get_database(slug, allow_unreadable=True)
    elif entity_type == "filesystem":
        found = registry.get_filesystem(slug)
    else:
        raise HTTPException(status_code=404, detail=f"no publish table for '{entity_type}'")
    if not found:
        raise HTTPException(status_code=404, detail=f"{entity_type} '{slug}' not found")
    return registry


@router.get("/{entity_type}/{slug}")
async def read_table(entity_type: str, slug: str) -> dict:
    registry = _registry_for(entity_type, slug)
    return await asyncio.to_thread(_one_read, "read", slug, pt.build_rows, registry, entity_type, slug)


@router.put("/{entity_type}/{slug}/choice")
async def save_choice(entity_type: str, slug: str, body: ChoiceBody, request: Request) -> dict:
    author = _require_author(request, "choose what gets published")
    registry = _registry_for(entity_type, slug)
    if entity_type == "database":
        await _authorize(registry, slug)
    try:
        return await asyncio.to_thread(_one_read, "choice", slug, pt.save_choice, registry, entity_type, slug,
                                       analysis_id=body.analysis_id, measure=body.measure, choice=body.choice,
                                       author=author)
    except pt.ChoiceRefused as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
