"""Egeria Project context API — Part 5 of
docs/discovery-automate-project-context-plan.md, the "uber intent."

Always "Egeria Project" in code/UI — deliberately distinct from RE's own
`Project` class (registry.py, a registered repo/resource) which is an
unrelated concept that happens to share the word (see that plan's own
naming-collision note).

Deliberately generic across entity_type (repo/database/filesystem), same
pattern as context.py's Enrichment routes — nothing here is repo-specific.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from resource_explorer.registry import ProjectRegistry

router = APIRouter()

#: `unbound` is written by scripts/clear_egeria_pointers_after_reset.py (the Egeria project this context named
#: is gone after a reset). It is not an answer: the publish gate treats it exactly as `unset`.
_VALID_STATUSES = {"unset", "unbound", "personal", "linked", "deferred", "declined"}


class ProjectContextData(BaseModel):
    entity_type: str
    entity_slug: str
    status: str
    egeria_project_guid: str = ""
    egeria_project_qualified_name: str = ""
    free_text_name: str = ""
    decided_at: str = ""


class SetProjectContextRequest(BaseModel):
    status: str
    egeria_project_guid: str = ""
    egeria_project_qualified_name: str = ""
    free_text_name: str = ""


class ProjectCandidate(BaseModel):
    guid: str
    qualified_name: str
    display_name: str
    description: str = ""


# NOTE: this fixed-path route must be registered before the generic
# /{entity_type}/{slug} routes below — FastAPI matches in registration
# order, and /{entity_type}/{slug} would otherwise swallow this path
# (treating "search" as entity_type, "candidates" as slug).
@router.get("/search/candidates", response_model=list[ProjectCandidate])
def search_project_candidates(q: str = "*", limit: int = 20) -> list[ProjectCandidate]:
    """Real Egeria Projects matching a text search — the "pick an existing
    Egeria Project" option's data source. Fail-soft: a search/connection
    error returns an empty list rather than a 500, since the picker's other
    options (personal/deferred/declined) must stay usable even when Egeria
    itself is unreachable."""
    from resource_explorer.surveyors.egeria_project_finder import (
        EgeriaProjectFinder, EgeriaProjectFinderError,
    )

    try:
        finder = EgeriaProjectFinder()
        results = finder.search_projects(search_string=q, limit=limit)
    except EgeriaProjectFinderError:
        return []
    return [ProjectCandidate(**r) for r in results]


@router.get("/{entity_type}/{slug}")
def get_project_context(entity_type: str, slug: str) -> ProjectContextData:
    """Current Egeria Project context for a resource — status "unset" (not
    a 404) when nothing has ever been decided, since "no opinion yet" is a
    normal, expected state here, not an error."""
    row = ProjectRegistry().get_project_context(entity_type, slug)
    if not row:
        return ProjectContextData(entity_type=entity_type, entity_slug=slug, status="unset")
    return ProjectContextData(**row)


@router.post("/{entity_type}/{slug}")
def set_project_context(entity_type: str, slug: str, req: SetProjectContextRequest) -> ProjectContextData:
    """Record a deliberate Egeria Project context decision — "just
    exploring" (personal), an existing Egeria Project (linked), a
    free-text name to correlate later (deferred), or an explicit decline
    (declined). Setting status back to "unset" is intentionally allowed
    too (re-opens the picker's default state) — not blocked as a one-way
    door."""
    if req.status not in _VALID_STATUSES:
        raise HTTPException(status_code=400, detail=f"Invalid status {req.status!r}. Must be one of {sorted(_VALID_STATUSES)}.")
    if req.status == "linked" and not req.egeria_project_guid:
        raise HTTPException(status_code=400, detail="status='linked' requires egeria_project_guid.")
    if req.status == "deferred" and not req.free_text_name:
        raise HTTPException(status_code=400, detail="status='deferred' requires free_text_name.")

    row = ProjectRegistry().set_project_context(
        entity_type, slug, req.status,
        egeria_project_guid=req.egeria_project_guid,
        egeria_project_qualified_name=req.egeria_project_qualified_name,
        free_text_name=req.free_text_name,
    )
    return ProjectContextData(**row)
