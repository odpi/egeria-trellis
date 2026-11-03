"""Quality slice Q3: price, then run, the DatabaseQualitySurvey across a work list of databases.

The price is read first and shown before anything is queued (design §2.2: "with the per-run price shown before the
batch starts"); the run queues one `survey_definition_run` per database and returns a run set, watched through the
work lists' own `GET /api/work-lists/runs/sets/{set_id}`. Scheduling the batch is the ordinary schedules API with
`entity_type = "work_list"` (`POST /api/schedules/work_list/{slug}`). All the logic is in `quality_batch.py`.
"""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from resource_explorer import quality_batch

router = APIRouter()


class QualityBatchRequest(BaseModel):
    #: The list to run across (its members as they are now), or an explicit set of database slugs.
    work_list_slug: str = ""
    entity_slugs: list[str] | None = None


def _members(registry, body: QualityBatchRequest) -> list[str]:
    if body.entity_slugs is not None:
        slugs = list(body.entity_slugs)
    elif body.work_list_slug:
        try:
            slugs = quality_batch.work_list_members(registry, body.work_list_slug)
        except KeyError:
            raise HTTPException(status_code=404, detail=f"work list {body.work_list_slug!r} not found") from None
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    else:
        raise HTTPException(status_code=400, detail="give entity_slugs, or a work_list_slug to run across")
    if not slugs:
        raise HTTPException(status_code=400, detail="nothing to run — the set is empty")
    return slugs


@router.post("/price")
async def price(body: QualityBatchRequest) -> dict:
    """One priced row per database, and the total when every row is measured. Reads only what RE stored."""
    from resource_explorer.registry import ProjectRegistry

    registry = ProjectRegistry()
    slugs = _members(registry, body)
    return await asyncio.to_thread(quality_batch.price_batch, registry, slugs)


@router.post("/run")
async def run(body: QualityBatchRequest) -> dict:
    """Queue the definition on every database that can run, as the person who pressed (their session is held per
    run, as a single press holds it). Each member comes back with its outcome and words; a press never skips an
    unchanged database (the person asked for it), it only says so in the row's price. Nothing permitted and nothing
    runnable is still a 200 with every member's words, never an empty success."""
    from resource_explorer.a2a_auth import caller
    from resource_explorer.registry import ProjectRegistry
    from resource_explorer.run_queue import requested_by
    from resource_explorer.workflows.egeria_work_access import batch_refusal
    from resource_explorer.zone_access import EgeriaAccessReader

    registry = ProjectRegistry()
    slugs = _members(registry, body)
    who = requested_by() or ""
    reader = EgeriaAccessReader()      # one batch: controls and accounts read once
    return await asyncio.to_thread(
        quality_batch.enqueue_quality_batch, registry, slugs, work_list_slug=body.work_list_slug,
        requested_by=who, started_by_person=who, pressed=caller(), skip_unchanged=False,
        refusal=lambda s: batch_refusal(registry, "database", s, reader=reader))
