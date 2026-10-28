"""Resource schedule API — store and retrieve analysis schedules per resource."""
from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from resource_explorer.registry import ProjectRegistry, TARGET_ANALYSIS, TARGET_SURVEY

router = APIRouter()


def _cost_hint(entity_type: str, analysis_id: str) -> dict:
    """Cost tiers and the cadence they warrant, for one analysis.

    Repo-only: cost tiers live on repo StepInfo entries, and database/filesystem
    analyses have no equivalent yet. Returning empty rather than guessing keeps
    that absence visible instead of showing every database analysis as "daily,
    costs nothing".
    """
    if entity_type != "repo":
        return {}
    # A Survey Definition has no cost tier: cost lives on individual repo
    # StepInfo entries, and a survey is a graph of them whose total depends on
    # which branches run. Returning empty says "not established" rather than
    # inventing a tier for a bundle — the same reason database analyses get
    # nothing here.
    if analysis_id.startswith("GovActionProcess::"):
        return {}
    from resource_explorer.surveyors.repo_survey_definition_adapter import (
        REPO_ANALYSIS_STEP_MAP, analysis_cost, recommended_schedule,
    )
    if analysis_id not in REPO_ANALYSIS_STEP_MAP:
        return {}
    fetch, compute = analysis_cost(analysis_id)
    return {"fetch_cost": fetch, "compute_cost": compute,
            "recommended_schedule": recommended_schedule(analysis_id)}

_VALID_SCHEDULES = {"manual", "daily", "weekly", "monthly"}


class ScheduleEntry(BaseModel):
    #: The target reference. An analysis id, or a Survey Definition's
    #: qualified name when target_kind is "survey". Named for its original
    #: meaning; see ProjectRegistry.save_schedule for why it was not renamed.
    analysis_id: str
    schedule: str = "manual"    # manual | daily | weekly | monthly
    enabled: bool = True
    #: "analysis" (default, unchanged behaviour) or "survey". The registry and
    #: scheduler have understood both since 2026-08-28; this route did not
    #: expose it, so a survey could be authored, listed and run by hand but
    #: never actually put on a cadence.
    target_kind: str = TARGET_ANALYSIS


#: How to find a resource of each schedulable kind. A schedule names a
#: resource that must still exist when the cadence fires, and nothing checked
#: that at the point the schedule was created.
_RESOURCE_LOOKUP = {
    "repo": lambda reg, slug: reg.get(slug),
    "database": lambda reg, slug: reg.get_database(slug, allow_unreadable=True),
    "filesystem": lambda reg, slug: reg.get_filesystem(slug),
}


def _require_resource(registry, entity_type: str, slug: str) -> None:
    """404 unless this resource exists.

    Measured 2026-08-28: POST /api/schedules/repo/egeria was accepted and
    stored, though no repo has that slug — the real one is `egeria_git`. The
    typo surfaced nowhere. It would have sat as a valid-looking weekly
    schedule and failed on its first run, a week later, with "schedule may be
    stale" written into a row nobody was watching.

    The scheduler already reports it correctly; the cost was entirely in WHEN.
    A mistake a caller can fix in the second it is made becomes, untouched, a
    silent gap in coverage the user believes they have.

    An unknown entity_type passes through deliberately rather than 422-ing:
    this route has never constrained the vocabulary, and inventing a whitelist
    here would reject a resource kind added elsewhere in the codebase before
    anyone thought to update this dict. What it must not do is claim a
    resource is missing when it simply cannot look it up.
    """
    from fastapi import HTTPException

    lookup = _RESOURCE_LOOKUP.get(entity_type)
    if lookup is None:
        return
    if lookup(registry, slug) is None:
        raise HTTPException(
            status_code=404,
            detail=f"No {entity_type} '{slug}' — a schedule against it could "
                   f"never run. Check the slug.",
        )


@router.get("/")
def list_all_schedules() -> list[dict]:
    """Every scheduled analysis across every resource — backs the Admin
    Schedules overview. Errors surface first; see
    ProjectRegistry.list_all_schedules()'s docstring."""
    return [{**r, **_cost_hint(r.get("entity_type", ""), r.get("analysis_id", ""))}
            for r in ProjectRegistry().list_all_schedules()]


@router.get("/{entity_type}/{slug}")
def get_schedules(entity_type: str, slug: str) -> list[dict]:
    """Return all schedules configured for a resource."""
    return [{**r, **_cost_hint(entity_type, r.get("analysis_id", ""))}
            for r in ProjectRegistry().get_schedules(entity_type, slug)]


@router.delete("/{entity_type}/{slug}/{analysis_id}")
def delete_schedule(entity_type: str, slug: str, analysis_id: str) -> dict:
    """Remove a schedule entirely (vs. disabling it) — useful for cleaning up
    stale schedules pointing at a resource that no longer exists."""
    from fastapi import HTTPException
    if not ProjectRegistry().delete_schedule(entity_type, slug, analysis_id):
        raise HTTPException(status_code=404, detail="Schedule not found")
    return {"status": "success"}


@router.post("/{entity_type}/{slug}/{analysis_id}/run")
def run_schedule_now(entity_type: str, slug: str, analysis_id: str) -> dict:
    """Run a scheduled analysis immediately, without waiting for its next slot.

    Dan, 2026-09-01: "With schedules and Surveys in Automate, seems like there
    should be a run now option?" There was not. Schedules had GET/POST/DELETE
    and `scheduler._run_due()` fires on a timer with no way to trigger one — so
    a schedule could only be WAITED for, which also made it impossible to
    confirm a schedule worked without watching for the slot to come round.

    Deliberately reuses `scheduler._execute`, the same dispatch the timer uses.
    A second code path would let "run now" and "ran on schedule" diverge, and
    the whole value of this is confirming what the SCHEDULE will do.

    Runs SYNCHRONOUSLY, in a worker thread, and returns the outcome. Not
    fire-and-forget: the point is to see the result. Note that some analyses are
    genuinely slow — repo_secret_scan measured 277s on a large repository — so
    a caller should expect this to block for as long as the analysis takes,
    which is the honest behaviour rather than a success returned before the work
    is done.

    Does NOT advance `next_run`. This is an out-of-band run, and moving the
    schedule because someone tested it would silently skip the next real slot —
    a side effect nobody asked for, and one that would be invisible until an
    expected run did not happen.
    """
    from fastapi import HTTPException

    from resource_explorer import scheduler as _scheduler

    registry = ProjectRegistry()
    _require_resource(registry, entity_type, slug)

    rows = registry.get_schedules(entity_type, slug)
    entry = next((r for r in rows if r.get("analysis_id") == analysis_id), None)
    if entry is None:
        # A 404 rather than running it anyway: this endpoint runs THE SCHEDULE,
        # and running an analysis that has none would answer a different
        # question from the one asked. /analyses/{id}/run already exists for the
        # unscheduled case.
        raise HTTPException(
            status_code=404,
            detail=f"No schedule for analysis '{analysis_id}' on {entity_type} '{slug}'. "
                   f"Use the resource's own analyses/{analysis_id}/run to run it unscheduled.")

    target_kind = entry.get("target_kind") or TARGET_ANALYSIS
    try:
        name, location, errors = _scheduler._execute(
            entity_type, slug, analysis_id, registry, target_kind=target_kind,
        )
    except Exception as exc:
        # Surfaced, not swallowed. A scheduled run that fails on the timer
        # writes its error to the activity log where someone may never look;
        # a run the user asked for should tell them to their face.
        raise HTTPException(
            status_code=502,
            detail=f"Run failed: {type(exc).__name__}: {exc}") from exc

    return {
        "status": "ok" if not errors else "completed_with_errors",
        "entity_type": entity_type, "entity_slug": slug,
        "analysis_id": analysis_id, "target_kind": target_kind,
        "entity_name": name, "entity_location": location,
        "errors": errors,
        # Stated explicitly so a caller cannot infer otherwise from a 200: the
        # schedule is untouched and its next slot still stands.
        "next_run_advanced": False,
        "schedule": entry.get("schedule", ""),
        "next_run": entry.get("next_run", ""),
    }


def _signed_in_user() -> str:
    from resource_explorer.run_queue import requested_by

    return requested_by() or ""


@router.post("/{entity_type}/{slug}")
def save_schedule(entity_type: str, slug: str, entry: ScheduleEntry) -> dict:
    """Create or update a schedule for a specific analysis on a resource."""
    from fastapi import HTTPException
    if entry.schedule not in _VALID_SCHEDULES:
        raise HTTPException(status_code=422, detail=f"Invalid schedule '{entry.schedule}'; must be one of {sorted(_VALID_SCHEDULES)}")
    if entry.target_kind not in (TARGET_ANALYSIS, TARGET_SURVEY):
        raise HTTPException(
            status_code=422,
            detail=f"Invalid target_kind '{entry.target_kind}'; must be "
                   f"'{TARGET_ANALYSIS}' or '{TARGET_SURVEY}'")
    registry = ProjectRegistry()
    _require_resource(registry, entity_type, slug)
    if entry.enabled:
        # Brief Z2 round 2: a scheduled run publishes what it measures (auto-publish when the resource has an
        # Egeria Project), so the person creating the schedule must be allowed to publish the resource. Round 3:
        # they are recorded as `created_by` and every timer run runs as them, so the run re-checks their rights;
        # a schedule with no creator (from before) follows the no-requester rule.
        from resource_explorer.web.routes.curate import require_resource_write
        require_resource_write(registry, entity_type, slug, "publish")
    registry.save_schedule(
        entity_type=entity_type,
        entity_slug=slug,
        analysis_id=entry.analysis_id,
        schedule=entry.schedule,
        enabled=entry.enabled,
        target_kind=entry.target_kind,
        created_by=_signed_in_user(),       # Brief Z2 round 3: a timer run runs as this person
    )
    hint = _cost_hint(entity_type, entry.analysis_id)
    out = {"status": "ok", "entity_type": entity_type, "entity_slug": slug,
           "analysis_id": entry.analysis_id, "schedule": entry.schedule,
           "enabled": entry.enabled, "target_kind": entry.target_kind, **hint}

    # Advisory only — the schedule above is already saved. A ceiling that
    # refused would be wrong: wanting an expensive analysis more often than its
    # cost suggests is a legitimate choice, and the reasons for it are invisible
    # from here. Doing it unknowingly is the case worth catching.
    from resource_explorer.surveyors.repo_survey_definition_adapter import (
        schedule_is_more_frequent_than_recommended,
    )
    if hint and schedule_is_more_frequent_than_recommended(entry.analysis_id, entry.schedule):
        out["note"] = (
            f"This analysis is {hint['fetch_cost']}-fetch/{hint['compute_cost']}-compute; "
            f"{hint['recommended_schedule']} is usually often enough. "
            f"Running it {entry.schedule} is fine if you mean to."
        )
    return out
