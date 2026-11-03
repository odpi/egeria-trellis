"""Value profile routes: a database's latest value-profile batch, as the Value profile sub-tab reads it.

Design: docs/design-notes/DESIGN-VALUE-PROFILE-SCREEN-2026-10-30.md §6. Both are reads of RE's own tables (no database
credential, no Egeria call), over `_value_profile_results` as the one source; `resource_explorer/value_profile_screen.py`
holds everything that is not HTTP.

* ``GET /api/databases/{slug}/value-profile``: the header sentence, the filter counts and one line per table;
* ``GET /api/databases/{slug}/value-profile/{schema}/{table}``: that table's column rows, read when its card opens.
"""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException

router = APIRouter()


def _registry_for(slug: str):
    from resource_explorer.registry import ProjectRegistry

    registry = ProjectRegistry()
    db = registry.get_database(slug, allow_unreadable=True)
    if not db:
        raise HTTPException(status_code=404, detail=f"Database '{slug}' not found")
    return registry, db.slug


@router.get("/{slug}/value-profile")
async def read_value_profile(slug: str) -> dict:
    from resource_explorer import value_profile_screen as screen

    registry, key = _registry_for(slug)
    return await asyncio.to_thread(screen.build_summary, registry, key)


@router.get("/{slug}/value-profile/{schema}/{table}")
async def read_value_profile_table(slug: str, schema: str, table: str) -> dict:
    from resource_explorer import value_profile_screen as screen

    registry, key = _registry_for(slug)
    out = await asyncio.to_thread(screen.build_table, registry, key, schema, table)
    if out is None:
        raise HTTPException(status_code=404,
                            detail=f"{schema}.{table} is not in this database's latest value profile.")
    return out
