"""Table result route (B1, DESIGN-B1-MEMBER-SCOPED-TABLE-RESULT-2026-11-02 §5): what the renderer's `table` block draws.

* ``GET /api/table-results/{resource_kind}/{slug}/{analysis_id}``: the headline (the analysis's own, else its declared
  summary), the summary measures behind it, the first `max_rows` stored rows typed and worded through the declaration,
  the truncation sentence, and the publish band. A read of RE's own tables: no credential, no Egeria call.
"""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException

router = APIRouter()


def build(registry, resource_kind: str, slug: str, analysis_id: str) -> dict | None:
    """The block's payload, or None when the analysis does not declare a table result."""
    from resource_explorer import table_result
    from resource_explorer.declared_context import declarations
    from resource_explorer.facts import FactLayer
    from resource_explorer.publish_table import run_fields

    dec = declarations(resource_kind).get(analysis_id)
    if dec is None or not dec.is_table_result():
        return None
    layer = FactLayer(registry, resource_type=resource_kind)
    fact = layer.fact(slug, analysis_id).as_dict()
    return table_result.payload(registry, resource_kind, slug, analysis_id, dec, fact.get("value") or {},
                                run_fields(fact), headline=str(fact.get("headline") or ""))


@router.get("/{resource_kind}/{slug}/{analysis_id}")
async def read_table_result(resource_kind: str, slug: str, analysis_id: str) -> dict:
    from resource_explorer.registry import ProjectRegistry

    registry = ProjectRegistry()
    out = await asyncio.to_thread(build, registry, resource_kind, slug, analysis_id)
    if out is None:
        raise HTTPException(status_code=404, detail=f"{analysis_id} does not declare a table result.")
    return out
