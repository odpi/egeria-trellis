"""Prefect observability — flow-run status, grouping, and cancellation for
locally-dispatched (executes_at: prefect) survey steps.

Scope boundary, worth keeping visible rather than implying more than this
covers: this is about **locally-run** survey steps that Prefect executes.
`executes_at: egeria` steps are coordinated by Egeria itself — RE has no more
visibility into those than it did before this module existed, and nothing
here should be read as covering them. See docs/re-ea-consolidation-audit.md
and the Prefect entry in docs/Backlog.md.

Follows schedules.py's precedent: thin APIRouter, no auth, and — since this
depends on a Prefect server that may not be running — every endpoint degrades
gracefully (empty list / status flag) rather than raising, so a missing
server shows as "not reachable" in the UI, not a broken page.
"""
from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, HTTPException

from resource_explorer.surveyors.prefect_adapter import re_prefect_client

router = APIRouter()
log = logging.getLogger(__name__)


async def _reachable() -> tuple[bool, str]:
    try:
        async with re_prefect_client() as client:
            await client.api_healthcheck()
        return True, ""
    except Exception as exc:  # pragma: no cover — exact exception shape depends on failure mode
        return False, str(exc)


@router.get("/status")
def get_status() -> dict:
    """Whether a Prefect server is reachable right now — never raises. The
    Admin panel uses this to decide whether to show flow-run data or an
    explanatory "not running" message."""
    from resource_explorer.config import get_config

    config = get_config()
    ok, error = asyncio.run(_reachable())
    return {
        "enabled": config.prefect.enabled,
        "route_local_steps": config.prefect.route_local_steps,
        "reachable": ok,
        "error": error,
        "api_url": config.prefect.api_url,
        "ui_url": config.prefect.ui_url,
    }


async def _list_flow_runs(limit: int) -> list[dict]:
    async with re_prefect_client() as client:
        runs = await client.read_flow_runs(
            sort="START_TIME_DESC",
            limit=limit,
        )
    out = []
    for r in runs:
        tags = {t.split(":", 1)[0]: t.split(":", 1)[1] for t in r.tags if ":" in t}
        out.append({
            "id": str(r.id),
            "name": r.name,
            "state": r.state.type.value if r.state else "UNKNOWN",
            "state_message": r.state.message if r.state else "",
            "created": r.created.isoformat() if r.created else "",
            "start_time": r.start_time.isoformat() if r.start_time else "",
            "end_time": r.end_time.isoformat() if r.end_time else "",
            "deployment_id": str(r.deployment_id) if r.deployment_id else None,
            "entity_type": tags.get("entity_type", ""),
            "slug": tags.get("slug", ""),
            "step": tags.get("step", ""),
        })
    return out


@router.get("/flow-runs")
def list_flow_runs(limit: int = 50) -> dict:
    """Recent/active flow runs, newest first — grouped by the RE resource
    they belong to via slug/step tags (see prefect_adapter.py's
    create_flow_run_from_deployment call). Returns an explanatory error
    rather than a 500 when the server isn't reachable, since "no flow runs"
    and "can't reach Prefect at all" are different states the panel needs to
    tell apart."""
    try:
        runs = asyncio.run(_list_flow_runs(limit))
    except Exception as exc:
        return {"reachable": False, "error": str(exc), "flow_runs": []}
    return {"reachable": True, "error": "", "flow_runs": runs}


async def _cancel(flow_run_id: str) -> None:
    from uuid import UUID

    from prefect.client.schemas.objects import StateType
    from prefect.states import Cancelled

    async with re_prefect_client() as client:
        await client.set_flow_run_state(
            flow_run_id=UUID(flow_run_id), state=Cancelled(), force=True,
        )


@router.post("/flow-runs/{flow_run_id}/cancel")
def cancel_flow_run(flow_run_id: str) -> dict:
    """Real cancellation, via Prefect — the local threading.Thread execution
    path (survey_definitions.py and friends) has no equivalent; this is the
    actual answer to "how do I stop a thrashing survey" for whichever steps
    are Prefect-routed."""
    try:
        asyncio.run(_cancel(flow_run_id))
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Could not cancel flow run: {exc}")
    return {"status": "cancelling", "id": flow_run_id}
