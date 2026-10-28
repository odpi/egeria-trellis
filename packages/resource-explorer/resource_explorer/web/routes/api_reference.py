"""The API reference of a repository: read it, and register / re-check / clear a running instance's OpenAPI URL.

GET    /api/api-reference/{slug}                    every known source (committed spec, running instance,
                                                    derived from code), which is best, how they differ, and what
                                                    is missing and why
PUT    /api/api-reference/{slug}/instance           {url}: fetch read-only, keep only a real OpenAPI document
POST   /api/api-reference/{slug}/instance/refetch   re-check the registered URL
DELETE /api/api-reference/{slug}/instance           forget the URL and the stored copy

The three writes need a signed-in author (401 otherwise; the name is recorded, and a clear is recorded too, not a
silent delete) and the same resource-write check the other per-repository writes use (403 with the reason).
Reading is open.

A rejected URL is an ANSWER (HTTP 200, `ok: false`, `reason`: "not an OpenAPI document: got text/html"), not a
fault, so the control can show the sentence beside the field.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from resource_explorer.api_reference import fetch_policy, live, service, store
from resource_explorer.registry import ProjectRegistry
from resource_explorer.web import admin_auth
from resource_explorer.web.routes.curate import _require_author, require_resource_write

log = logging.getLogger(__name__)
router = APIRouter()


def _registry() -> ProjectRegistry:
    return ProjectRegistry()


def _transport():
    """None in production (a real network client); tests substitute an httpx transport."""
    return None


def _resolver():
    """None in production (the system resolver); tests substitute one."""
    return None


class InstanceBody(BaseModel):
    url: str


def _repo(registry: ProjectRegistry, slug: str) -> None:
    if registry.get(slug) is None:
        raise HTTPException(status_code=404, detail=f"repository '{slug}' not found")


@router.get("/policy")
def get_policy(request: Request) -> dict:
    """The effective address policy for registered API documents, read-only (the Admin page shows it). Admin
    only, like the other Admin routes: it describes the network RE can reach."""
    admin_auth.require_admin(request)
    return fetch_policy.describe()


@router.get("/{slug}/operations")
def search_operations(slug: str, q: str = "", path: str = "", method: str = "", source: str = "",
                      limit: int = 200, offset: int = 0) -> dict:
    """Operations of this repository from the stored documents, filtered by a word, a path fragment, a method
    or a source (committed_spec | running_instance)."""
    registry = _registry()
    _repo(registry, slug)
    rows, more = store.find_operations(registry, slug, source=source or None, text=q or None,
                                       path_contains=path or None, method=method or None,
                                       limit=max(1, min(limit, 1000)), offset=max(0, offset))
    return {"slug": slug, "count": len(rows), "offset": max(0, offset), "more": more, "operations": rows}


@router.get("/{slug}")
def get_reference(slug: str) -> dict:
    registry = _registry()
    _repo(registry, slug)
    return service.build_reference(registry, slug)


@router.put("/{slug}/instance")
def register_instance(slug: str, body: InstanceBody, request: Request) -> dict:
    registry = _registry()
    _repo(registry, slug)
    by = _require_author(request, "register an API address")
    require_resource_write(registry, "repo", slug, "link")
    return live.register_instance(registry, slug, body.url, by=by, transport=_transport(), resolver=_resolver())


@router.post("/{slug}/instance/refetch")
def refetch_instance(slug: str, request: Request) -> dict:
    registry = _registry()
    _repo(registry, slug)
    by = _require_author(request, "re-check an API address")
    require_resource_write(registry, "repo", slug, "link")
    return live.refetch_instance(registry, slug, by=by, transport=_transport(), resolver=_resolver())


@router.delete("/{slug}/instance")
def clear_instance(slug: str, request: Request) -> dict:
    registry = _registry()
    _repo(registry, slug)
    by = _require_author(request, "forget an API address")
    require_resource_write(registry, "repo", slug, "unlink")
    return {"cleared": live.clear_instance(registry, slug, by=by)}
