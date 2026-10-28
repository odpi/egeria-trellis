"""Curate's "By analysis" tab: the Analytic Library's `curation_coverage`, run over RE's own registry, read-only.

The route never constructs `ProjectRegistry` (whose constructor migrates): `OdsReader` opens the store itself
read-only. When the library is not installed the answer is `not_applicable` with its reason, never a 500 and
never a table of zeros.
"""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException

router = APIRouter()


def registry_url() -> str:
    """The registry RE itself reads (the same default ProjectRegistry takes)."""
    from resource_explorer.config import get_config
    return get_config().registry.database_url


def _unavailable(resource_type: str, slug: str, reason: str) -> dict:
    return {"resource_type": resource_type, "slug": slug, "groups": [], "summary": "",
            "envelope": {"result_state": "not_applicable", "result_state_reason": reason,
                         "scope": f"{resource_type}:{slug}"}}


def curation_coverage_for(resource_type: str, slug: str, url: str | None = None) -> dict:
    """Run the routine and return its result dict. Blocking (a registry read); call off the loop."""
    try:
        from trellis_analytics.readers.ods import OdsReader
        from trellis_analytics.routines import curation_coverage
    except ImportError as exc:
        return _unavailable(resource_type, slug, f"the Analytic Library (trellis-analytics) is not installed: {exc}")
    return curation_coverage(OdsReader(url or registry_url()), resource_type, slug).to_dict()


@router.get("/curation-coverage/{resource_type}/{slug}")
async def read_curation_coverage(resource_type: str, slug: str) -> dict:
    return await asyncio.to_thread(curation_coverage_for, resource_type, slug)


# Entity types the route knows. The library answers `not_applicable` (with its reason) for the ones it does
# not yet measure; a type outside this set is a caller error (404).
KNOWN_ENTITY_TYPES = ("database", "file_system", "document_source")


def require_caller() -> None:
    """The tile is never anonymous, even under TRELLIS_ANONYMOUS_READ (which lets a GET past the login gate).

    The route is an ordinary authenticated route: `LoginRequiredMiddleware` gates it and `_identity_middleware`
    publishes RE's own session JWT's subject as `a2a_auth.current_caller`. A request that arrives with no
    caller, whatever let it through the gate, is 401.

    The Portal's flow (server-to-server, no cookies):
      1. `POST /api/auth/portal` body `{"portal_token": "<per-user SSO JWT>"}` -> `{"access_token": ...}`
      2. `GET /api/analytics/curation-coverage-tile/{entity_type}/{slug}` with `Authorization: Bearer <access_token>`
    """
    from resource_explorer.a2a_auth import current_caller

    if current_caller.get() is None:
        raise HTTPException(status_code=401, detail="Authentication required: send Authorization: Bearer <RE session token>.")


def curation_coverage_tile_for(entity_type: str, slug: str, url: str | None = None) -> dict:
    """The Portal tile dict, from the library's own `curation_coverage_tile` (one reshape, not a copy)."""
    try:
        from trellis_analytics.portal import curation_coverage_tile
    except ImportError as exc:
        # Only "the library itself is absent" is a state; a transitive ImportError is a bug and must surface.
        if (exc.name or "").split(".")[0] != "trellis_analytics":
            raise
        reason = "the Analytic Library is not installed on this RE"
        return {"state": "not_established", "state_reason": reason, "subtitle": f"Not established \u00b7 {reason}",
                "summary": "", "groups": [], "kpis": []}
    return curation_coverage_tile(slug, url or registry_url(), entity_type)


@router.get("/curation-coverage-tile/{entity_type}/{slug}")
async def read_curation_coverage_tile(entity_type: str, slug: str) -> dict:
    require_caller()
    if entity_type not in KNOWN_ENTITY_TYPES:
        raise HTTPException(status_code=404, detail=f"unknown entity type {entity_type!r}; known: {', '.join(KNOWN_ENTITY_TYPES)}")
    return await asyncio.to_thread(curation_coverage_tile_for, entity_type, slug)
