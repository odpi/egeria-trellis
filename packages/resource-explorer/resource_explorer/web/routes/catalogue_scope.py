"""Catalogue scope API: what gets catalogued for a database (slice A).

A declared, signed, dated choice stored in RE. Every write route resolves the
author from the session first and answers 401 when nobody is signed in (the
same posture as the Curate author routes); the request bodies have no author
field. The scope routes write nothing to Egeria; the commit is slice B, below (`/commit-preview`, `/commit`,
`/commits`, `/read-back`; see `resource_explorer.catalogue_commit`). The scope
routes still only edit RE's record; only `/commit` and `/read-back` reach Egeria.
"""
from __future__ import annotations

import asyncio
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from resource_explorer import catalogue_commit as commit
from resource_explorer import catalogue_scope as scope
from resource_explorer.auth import get_current_user
from resource_explorer.registry import ProjectRegistry

router = APIRouter()


def _require_author(request: Request, action: str) -> str:
    user = get_current_user(request)
    author = (user or {}).get("user_id") or (user or {}).get("sub") or (user or {}).get("username") or ""
    if not author:
        raise HTTPException(
            status_code=401,
            detail=f"Sign in to {action}: a scope choice needs an author.",
        )
    return author


async def _authorize(registry: ProjectRegistry, slug: str, *, publish: bool = False) -> None:
    """Brief Z round 2: the same curation check every curate entry point uses, on the database's element."""
    from resource_explorer.web.routes.curate import authorize_resource_write

    await authorize_resource_write(registry, "database", slug, publish=publish)


def _registry_for(slug: str) -> ProjectRegistry:
    registry = ProjectRegistry()
    if not registry.get_database(slug, allow_unreadable=True):
        raise HTTPException(status_code=404, detail=f"Database '{slug}' not found")
    return registry


class NodeBody(BaseModel):
    schema_name: str
    table_name: str = ""
    choice: str = ""


class BulkNode(BaseModel):
    schema_name: str
    table_name: str = ""


class BulkBody(BaseModel):
    nodes: list[BulkNode] = []
    choice: str = ""
    all_schemas: bool = False


class DepthBody(BaseModel):
    depth: str


async def _run(fn, *args, **kwargs):
    try:
        return await asyncio.to_thread(fn, *args, **kwargs)
    except scope.ScopeError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.message) from exc


@router.get("/{slug}")
async def read_scope(slug: str) -> dict:
    registry = _registry_for(slug)

    def build() -> dict:
        view = scope.build_scope_view(registry, slug)
        # Every state word, and the header marker, from persisted rows only:
        # opening Curate never contacts Egeria.
        view["commit"] = commit.derive_commit_state(registry, slug, view)
        return view

    view = await asyncio.to_thread(build)
    entity = registry.get_database(slug, allow_unreadable=True)
    guid = getattr(entity, "egeria_asset_guid", "") or ""
    view["egeria_element"] = {"guid": guid, "short": guid[:8],
                              "text": guid[:8] if guid else "not published to Egeria"}
    return view


def _gateway_or_none(entity):
    try:
        return commit.make_gateway(entity)
    except Exception:  # noqa: BLE001 -- no client at all: the preview says it could not check
        return None


@router.get("/{slug}/commit-preview")
async def read_commit_preview(slug: str) -> dict:
    """What pressing Catalogue would do, with the Egeria READS it needs (what hangs
    off each schema being left out). Writes nothing."""
    registry = _registry_for(slug)
    entity = registry.get_database(slug, allow_unreadable=True)

    def build() -> dict:
        view = scope.build_scope_view(registry, slug)
        derived = commit.derive_commit_state(registry, slug, view)
        return commit.build_preview(registry, slug, view, _gateway_or_none(entity),
                                    db_entity=entity, derived=derived)

    return await asyncio.to_thread(commit.run_with_loop, build)


class ReincludeConfirm(BaseModel):
    """S19: the person confirmed re-including ONE archived element, named by its archived copy's GUID."""
    node: Literal["schema", "database"] = "schema"
    name: str = ""
    archived_guid: str = Field(min_length=1)


class CommitBody(BaseModel):
    refresh_now: bool = False
    #: S19 (owner ruling 2026-10-14): Re-include confirmations. The press re-reads each archived copy and refuses
    #: (409) when the copy named is no longer the newest one.
    confirm_reinclude: list[ReincludeConfirm] = Field(default_factory=list)


@router.post("/{slug}/commit")
async def post_commit(slug: str, body: CommitBody, request: Request) -> dict:
    """Catalogue →. Re-validates against a fresh preview, records the curation,
    and queues the run. The body carries no scope: the record is the scope."""
    author = _require_author(request, "catalogue")
    registry = _registry_for(slug)
    await _authorize(registry, slug, publish=True)
    try:
        out = await asyncio.to_thread(commit.run_with_loop, commit.start_commit, registry, slug, author,
                                      refresh_now=body.refresh_now,
                                      confirm_reinclude=[c.model_dump() for c in body.confirm_reinclude])
    except commit.CommitBlocked as exc:
        raise HTTPException(status_code=exc.status, detail=exc.message) from exc
    return {"curation": out["curation"], "run_id": out["run_id"], "activity_id": out["activity_id"]}


@router.get("/{slug}/commits")
async def list_commits(slug: str) -> dict:
    from resource_explorer.curate_plan import Curations
    registry = _registry_for(slug)
    recs = [r for r in Curations(registry).for_resource("database", slug) if r.get("kind") == "catalogue"]
    return {"commits": recs}


#: A commit still "running" this long after it was requested did not finish: the page says so and does not watch it.
STALE_UNFINISHED_HOURS = 6


@router.get("/{slug}/commits/latest")
async def read_latest_commit(slug: str) -> dict:
    """The NEWEST catalog commit for this database, for a page that has just loaded: its record (steps and run state,
    which the run writes from proof rows), whether it is terminal, how old it is, and the proof-derived state of
    every schema. Read-only: it writes nothing and never contacts Egeria, so a reload and a second tab both read the
    same registry rows and see the same thing. `commit` is null when nothing was ever committed."""
    from datetime import datetime, timezone
    from resource_explorer.curate_plan import Curations
    registry = _registry_for(slug)

    def build() -> dict:
        recs = [r for r in Curations(registry).for_resource("database", slug) if r.get("kind") == "catalogue"]
        if not recs:
            return {"commit": None, "terminal": None, "age_hours": None, "stale_unfinished": False, "states": {}}
        rec = recs[0]                                   # for_resource orders newest first
        terminal = rec.get("state") in ("done", "failed")
        age = None
        try:
            then = datetime.fromisoformat(str(rec.get("requested_at") or "").replace("Z", "+00:00"))
            if then.tzinfo is not None:
                then = then.astimezone(timezone.utc).replace(tzinfo=None)
            age = round((datetime.now(timezone.utc).replace(tzinfo=None) - then).total_seconds() / 3600, 2)
        except ValueError:
            pass
        view = scope.build_scope_view(registry, slug)
        states = commit.derive_commit_state(registry, slug, view)["schemas"]
        return {"commit": rec, "terminal": terminal, "age_hours": age,
                "stale_unfinished": bool(not terminal and age is not None and age > STALE_UNFINISHED_HOURS),
                "states": states}

    return await asyncio.to_thread(build)


@router.get("/{slug}/commits/{curation_id}")
async def read_commit(slug: str, curation_id: str) -> dict:
    from resource_explorer.curate_plan import Curations
    registry = _registry_for(slug)
    rec = Curations(registry).get(curation_id)
    if not rec or rec["entity_slug"] != slug:
        raise HTTPException(status_code=404, detail="No such publish commit")
    return rec


@router.post("/{slug}/read-back")
async def post_read_back(slug: str, request: Request) -> dict:
    """Read Egeria again and record what it says (proof rows). Changes no scope and
    writes nothing to Egeria."""
    author = _require_author(request, "read Egeria back")
    registry = _registry_for(slug)
    entity = registry.get_database(slug, allow_unreadable=True)

    def go() -> dict:
        view = scope.build_scope_view(registry, slug)
        derived = commit.derive_commit_state(registry, slug, view)
        gateway = _gateway_or_none(entity)
        if gateway is None:
            raise scope.ScopeError(503, "Egeria could not be reached: no client could be built")
        chosen, kept = commit._chosen_and_kept(view, derived["schemas"])
        return commit.read_back(registry, gateway, slug, chosen + kept, by=author)

    return await _run(commit.run_with_loop, go)


@router.get("/{slug}/new-since")
async def read_new_since(slug: str) -> dict:
    registry = _registry_for(slug)
    return await asyncio.to_thread(scope.new_since_declared, registry, slug)


@router.get("/{slug}/history")
async def read_history(slug: str) -> dict:
    registry = _registry_for(slug)
    return {"events": registry.list_catalogue_scope_events(slug),
            "declarations": registry.list_catalogue_scope_baselines(slug)}


@router.put("/{slug}/depth")
async def put_depth(slug: str, body: DepthBody, request: Request) -> dict:
    author = _require_author(request, "choose a depth")
    registry = _registry_for(slug)
    await _authorize(registry, slug)
    return await _run(scope.set_depth, registry, slug, author, depth=body.depth)


@router.put("/{slug}/node")
async def put_node(slug: str, body: NodeBody, request: Request) -> dict:
    author = _require_author(request, "choose what gets published")
    registry = _registry_for(slug)
    await _authorize(registry, slug)
    return await _run(scope.set_node_choice, registry, slug, author,
                      schema=body.schema_name, table=body.table_name, choice=body.choice)


@router.post("/{slug}/nodes")
async def post_nodes(slug: str, body: BulkBody, request: Request) -> dict:
    author = _require_author(request, "choose what gets published")
    registry = _registry_for(slug)
    await _authorize(registry, slug)
    return await _run(scope.set_nodes_choice, registry, slug, author,
                      nodes=[{"schema": n.schema_name, "table": n.table_name} for n in body.nodes],
                      choice=body.choice, all_schemas=body.all_schemas)


@router.post("/{slug}/node/confirm")
async def post_confirm(slug: str, body: NodeBody, request: Request) -> dict:
    author = _require_author(request, "confirm a proposal")
    registry = _registry_for(slug)
    await _authorize(registry, slug)
    return await _run(scope.confirm_proposal, registry, slug, author,
                      schema=body.schema_name, table=body.table_name)


@router.post("/{slug}/node/override")
async def post_override(slug: str, body: NodeBody, request: Request) -> dict:
    author = _require_author(request, "override a proposal")
    registry = _registry_for(slug)
    await _authorize(registry, slug)
    return await _run(scope.override_proposal, registry, slug, author,
                      schema=body.schema_name, table=body.table_name)


@router.post("/{slug}/node/clear")
async def post_clear(slug: str, body: NodeBody, request: Request) -> dict:
    author = _require_author(request, "clear a choice")
    registry = _registry_for(slug)
    await _authorize(registry, slug)
    return await _run(scope.clear_node_choice, registry, slug, author,
                      schema=body.schema_name, table=body.table_name)


@router.post("/{slug}/redeclare")
async def post_redeclare(slug: str, request: Request) -> dict:
    author = _require_author(request, "declare the scope")
    registry = _registry_for(slug)
    await _authorize(registry, slug)
    return await _run(scope.redeclare, registry, slug, author)
