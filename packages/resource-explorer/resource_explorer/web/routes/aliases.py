"""Alias management endpoints."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

router = APIRouter()


class AliasRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    alias: str
    # alias keeps the JSON wire key as `project_slug` while the Python
    # vocabulary moves to `resource_slug`. A Pydantic field name IS the
    # wire key, so this is the one place the identifier rename and the
    # API contract are the same edit. index.html still sends the old
    # key; drop the alias when the wire format moves too.
    resource_slug: str = Field(alias="project_slug")
    #: Moving an alias that already points at ANOTHER resource is a separate, explicit choice.
    move: bool = False


@router.post("/")
async def add_alias(request: AliasRequest, http: Request) -> dict:
    """Store a confirmed alias → project_slug mapping."""
    from resource_explorer.registry import ProjectRegistry
    registry = ProjectRegistry()
    if not registry.exists(request.resource_slug):
        raise HTTPException(status_code=404, detail=f"Project '{request.resource_slug}' not found")
    held = registry.resolve_alias(request.alias)
    if held and held != registry._normalize_slug(request.resource_slug) and not request.move:
        # Never move an alias silently: it would change what a name resolves to in every future question.
        raise HTTPException(status_code=409, detail=f"alias already used for {held}")
    from resource_explorer.auth import get_current_user
    user = get_current_user(http) or {}
    who = user.get("user_id") or user.get("username") or "user"
    registry.add_alias(request.alias, request.resource_slug, confirmed_by=who)
    return {"alias": request.alias, "project_slug": request.resource_slug, "saved": True}


@router.get("/{slug}")
async def list_aliases(slug: str) -> dict:
    """List all aliases for a project."""
    from resource_explorer.registry import ProjectRegistry
    registry = ProjectRegistry()
    if not registry.exists(slug):
        raise HTTPException(status_code=404, detail=f"Project '{slug}' not found")
    return {"project_slug": slug, "aliases": registry.list_aliases(slug)}


@router.delete("/{alias}")
async def remove_alias(alias: str) -> dict:
    """Remove an alias."""
    from resource_explorer.registry import ProjectRegistry
    removed = ProjectRegistry().remove_alias(alias)
    if not removed:
        raise HTTPException(status_code=404, detail=f"Alias '{alias}' not found")
    return {"alias": alias, "removed": True}
