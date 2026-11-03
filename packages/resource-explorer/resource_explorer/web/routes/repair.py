"""Repair API — Admin-surface repo repair operations: rename, github_url
correction, drift-flagged collection enablement, investigation-membership
repoint/drop. See resource_explorer/repair.py for the orchestration this
wraps and docs/repair-operations-design.md for the guarantees each
operation makes.

Mirrors projects.py's pattern of running blocking work (pgvector/GitHub
calls) via asyncio.to_thread rather than in the event loop — the same
reasoning as remove_project()'s comment there: a slow/unreachable pgvector
or GitHub call must not block every other request.
"""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

router = APIRouter()


def _registry():
    from resource_explorer.registry import ProjectRegistry
    return ProjectRegistry()


class RenameRequest(BaseModel):
    new_slug: str


class GithubUrlRequest(BaseModel):
    new_url: str
    confirm: bool = False


class EnableCollectionRequest(BaseModel):
    collection_type: str


class RepointRequest(BaseModel):
    from_investigation: str
    to_investigation: str
    membership_rationale: str = ""


@router.post("/repos/{slug}/rename")
async def rename_repo(slug: str, req: RenameRequest) -> dict:
    from resource_explorer.repair import RepairError, rename_repo as do_rename
    try:
        result = await asyncio.to_thread(do_rename, slug, req.new_slug)
    except RepairError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return {
        "old_slug": result.old_slug, "new_slug": result.new_slug,
        "renamed_collections": result.renamed_collections,
        "unchanged_shared_collections": result.unchanged_shared_collections,
        "tables_touched": result.tables_touched,
    }


@router.post("/repos/{slug}/github-url")
async def set_github_url(slug: str, req: GithubUrlRequest) -> dict:
    from resource_explorer.repair import RepairError, change_github_url
    try:
        result = await asyncio.to_thread(change_github_url, slug, req.new_url, confirm=req.confirm)
    except RepairError as exc:
        # Not-confirmed is the same shape as any other refusal, not a 4xx
        # error class distinct from a real validation failure — the message
        # already names collection count and old/new url, and the caller
        # (Admin UI / CLI) surfaces it as a confirmation prompt, not a
        # generic error banner. See RepairError's docstring.
        raise HTTPException(status_code=422, detail=str(exc))
    return result


@router.post("/repos/{slug}/collections/enable")
async def enable_collection(slug: str, req: EnableCollectionRequest) -> dict:
    from resource_explorer.repair import RepairError, enable_collection as do_enable
    try:
        result = await asyncio.to_thread(do_enable, slug, req.collection_type)
    except RepairError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return result


@router.get("/repos/{slug}/drift")
async def repo_drift(slug: str) -> list[dict]:
    """Collection types this repo is eligible for but hasn't enabled — the
    same query collection_drift.detect_drift() runs during `refresh`,
    surfaced here so Admin can show "Enable" buttons without asking the
    user to read a CLI's stdout first."""
    from resource_explorer.collection_drift import detect_drift
    registry = _registry()
    if not registry.get(slug):
        raise HTTPException(status_code=404, detail=f"Repo '{slug}' not found")
    findings = await asyncio.to_thread(detect_drift, registry, slug)
    return [
        {"collection_type": f.collection_type, "matching_files": f.matching_files,
         "min_required": f.min_required, "sample_paths": list(f.sample_paths),
         "summary": f.summary}
        for f in findings
    ]


@router.get("/repos/{slug}/memberships")
async def repo_memberships(slug: str) -> list[dict]:
    from resource_explorer.repair import RepairError, list_repo_investigation_memberships
    try:
        return await asyncio.to_thread(list_repo_investigation_memberships, slug)
    except RepairError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.post("/repos/{slug}/memberships/repoint")
async def repoint_membership(slug: str, req: RepointRequest) -> dict:
    from resource_explorer.repair import RepairError, repoint_investigation_member
    try:
        return await asyncio.to_thread(
            repoint_investigation_member, slug, req.from_investigation, req.to_investigation,
            membership_rationale=req.membership_rationale,
        )
    except RepairError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@router.delete("/repos/{slug}/memberships/{investigation_slug}")
async def drop_membership(slug: str, investigation_slug: str) -> dict:
    from resource_explorer.repair import RepairError, drop_investigation_member
    try:
        members = await asyncio.to_thread(drop_investigation_member, slug, investigation_slug)
    except RepairError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return {"status": "success", "remaining_folio_members": members}


@router.post("/database-server-links")
async def link_database_servers(request: Request, dry_run: bool = False) -> dict:
    """Heal: link every published database to its server's DatabaseManager (CapabilityAssetUse, OWNS) where it has
    no link, reading each database's links first. Admin only: it writes to Egeria as RE's daemon account, and it
    runs only when this is pressed. `dry_run=true` reads everything and writes nothing."""
    from resource_explorer.repair import RepairError, link_database_servers as do_link
    from resource_explorer.web.admin_auth import require_admin

    require_admin(request)
    try:
        report = await asyncio.to_thread(do_link, dry_run=dry_run)
    except RepairError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return report.as_dict()
