"""The run queue's own read surface.

Deliberately small, and deliberately NOT the surface the UI polls. The frontend
still polls `GET /api/activity/{activity_id}` for a run's *outcome* — that
contract predates the queue and did not change with it. These endpoints answer
a different question: what is queued, who claimed it, and is anything draining
the queue at all.

That distinction matters most in the state the queue newly makes possible. A
`web --no-embed-worker` process with no `resource-explorer worker` running
accepts every enqueue and executes none of them; the activity entry sits at
"running" and looks exactly like a slow survey. `GET /api/runs?state=queued`
is how that reads as "nothing is draining the queue" instead.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter()


class RunRow(BaseModel):
    id: str
    kind: str
    target: str = "{}"
    requested_by: str = ""
    state: str
    claimed_by: str = ""
    enqueued_at: str = ""
    claimed_at: str = ""
    heartbeat_at: str = ""
    started_at: str = ""
    finished_at: str = ""
    error: str = ""
    #: The activity_log id carrying this run's user-facing result.
    result_ref: str = ""


def _registry():
    from resource_explorer.registry import ProjectRegistry

    return ProjectRegistry()


@router.get("/{run_id}", response_model=RunRow)
def get_run(run_id: str) -> RunRow:
    row = _registry().get_run(run_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"Run '{run_id}' not found")
    return RunRow(**{k: v for k, v in row.items() if k in RunRow.model_fields})


@router.get("/", response_model=list[RunRow])
def list_runs(state: str = "", kind: str = "", limit: int = 100) -> list[RunRow]:
    """Newest first. `state` and `kind` are exact matches, both optional."""
    from resource_explorer.registry import ProjectRegistry

    if state and state not in ProjectRegistry.RUN_STATES:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown state '{state}' — expected one of "
                   f"{list(ProjectRegistry.RUN_STATES)}",
        )
    rows = _registry().list_runs(state=state or None, kind=kind or None, limit=limit)
    return [RunRow(**{k: v for k, v in r.items() if k in RunRow.model_fields})
            for r in rows]


@router.post("/{run_id}/cancel", response_model=RunRow)
def cancel_run(run_id: str) -> RunRow:
    """Cancel a run that has not been claimed yet.

    A claimed or running row is refused with a 409 rather than marked cancelled.
    It owns a real Python thread in some worker process and Python threads
    cannot be interrupted, so writing `cancelled` over it would be a claim about
    work that is still running — see registry.cancel_queued_run.
    """
    registry = _registry()
    row = registry.get_run(run_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"Run '{run_id}' not found")
    if not registry.cancel_queued_run(run_id):
        from resource_explorer.run_queue import UNCANCELLABLE_KINDS

        if row.get("kind") in UNCANCELLABLE_KINDS:      # refused by the registry; say why
            raise HTTPException(
                status_code=409,
                detail=(f"Run '{run_id}' is an {row['kind']} run, held for its Egeria engine action: RE has "
                        "claimed that action, and cancelling the run here would leave it claimed with nothing "
                        "to complete it."),
            )
        raise HTTPException(
            status_code=409,
            detail=(
                f"Run '{run_id}' is {row['state']}, not queued. A run already "
                f"claimed by {row.get('claimed_by') or 'a worker'} cannot be "
                "cancelled — it holds a thread that cannot be interrupted. If "
                "that worker dies, reconciliation resolves the row as failed."
            ),
        )
    return get_run(run_id)
