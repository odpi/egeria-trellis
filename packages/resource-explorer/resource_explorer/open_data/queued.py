"""Scout, refresh and profile as QUEUED per-dataset runs (TR-54 B4), and the work-list view of them.

A work-list press enqueues one `open_data_analysis_run` per dataset (`web/routes/open_data.py`); the worker runs each through the
runners here, as the person who pressed. Each runner wraps the per-dataset function the inline press used to loop over
(`service.scout_dataset`, `refresh.refresh_dataset`, `profile.profile_dataset`), so what a run reads, and what it keeps, is unchanged.

A portal that does not answer fails THAT run: `analyses.RunnerFailed` ends its activity in error, in words that name the dataset and say
the earlier values were kept, and stores no result; the other datasets' runs are not touched. Portal calls are priced before the press
(`analyses.price`) and counted after it (`calls_made`).
"""
from __future__ import annotations

import json

from resource_explorer.open_data import analyses
from resource_explorer.open_data.absence import NOT_REPORTED

KEPT = "the earlier values were kept"


def _failed(ds: dict, words: str, kept: str = KEPT) -> analyses.RunnerFailed:
    return analyses.RunnerFailed(f"{ds.get('title') or ds['slug']}: {words}; {kept}")


# ── scout ────────────────────────────────────────────────────────────────────

def run_scout(ctx: analyses.RunContext) -> analyses.OpenDataResult:
    from resource_explorer.open_data import service

    out = service.scout_dataset(ctx.store, ctx.dataset, transport=ctx.transport())
    if out["outcome"] != "scouted":
        raise _failed(ctx.dataset, out["words"])
    return analyses.OpenDataResult(
        state="measured", summary=out["words"],
        explanation="Re-read the dataset from its portal and refreshed what the portal states: licence and its flags, publisher, modified date, formats, size.",
        measures={"scouted": 1, "changed": bool(out.get("changed"))}, calls_made=1)


# ── refresh ──────────────────────────────────────────────────────────────────

def run_refresh(ctx: analyses.RunContext) -> analyses.OpenDataResult:
    from resource_explorer.open_data import refresh

    out = refresh.refresh_dataset(ctx.store, ctx.dataset, transport=ctx.transport(), registry=ctx.registry)
    if out["outcome"] == "could_not_refresh":
        raise _failed(ctx.dataset, out["words"], "the values and the earlier look it had were kept")
    return analyses.OpenDataResult(
        state="measured", summary=out["words"],
        explanation="Looked at the dataset on its portal again (the dataset, then its distributions) and differenced it against the last look kept.",
        measures={"outcome": out["outcome"], "changes": len(out.get("changes") or []),
                  "changed": out["outcome"] == "changed"}, calls_made=2)


# ── profile ──────────────────────────────────────────────────────────────────

def run_profile(ctx: analyses.RunContext) -> analyses.OpenDataResult:
    from resource_explorer.open_data import profile
    from resource_explorer.surveyors.filesystem import file_class_match as fcm

    result = profile.profile_dataset(ctx.store, ctx.dataset, transport=ctx.transport(), catalog=fcm.default_catalog(),
                                     registry=ctx.registry)
    if result.get("portal_failed"):
        portal = ctx.store.get_portal(ctx.dataset.get("portal_slug") or "") or {}
        raise _failed(ctx.dataset, f"{result['words']} (portal {portal.get('display_name') or ctx.dataset.get('portal_slug')})",
                      "the earlier profile was kept")
    ctx.registry.record_step_run(ctx.dataset["slug"], profile.STEP_KEY, result["ran_at"], entity_type=profile.ENTITY_TYPE,
                                 source=profile.SOURCE, executor="local", metrics=result)    # what the work-list profile view reads
    listed = int(result.get("distributions_listed") or 0)
    rows = result.get("distributions") or []
    profiled = sum(1 for r in rows if r.get("state") == profile.PROFILED)
    not_read = [f"{r.get('name')}: {r.get('words')}" for r in rows if r.get("state") != profile.PROFILED]
    if not listed:
        return analyses.OpenDataResult(
            state="nothing_found", summary=result["words"], explanation="The portal lists no distributions for this dataset, so there is nothing to read.",
            measures={"distributions_listed": 0})
    if not profiled:
        return analyses.OpenDataResult(
            state="not_measured", summary=f"not profiled: {result['words']}",
            explanation="No distribution was profiled; the reason for each is listed. A file that was not read is never counted as zero rows.",
            measures={"distributions_listed": listed, "not_read": not_read})
    return analyses.OpenDataResult(
        state="measured", summary=result["words"],
        explanation=("What was read of the dataset's tabular distributions (each capped), and what was not: a file over the cap, in a "
                     "format not read here, or past the first three is named, never counted as zero."),
        measures={"distributions_profiled": profiled, "distributions_listed": listed, "not_read": not_read,
                  "documents_read": len(result.get("documents") or [])})


SCOUT = analyses.Runner("external_data_scout", run_scout, lambda dataset: 1)
REFRESH = analyses.Runner("external_data_refresh", run_refresh, lambda dataset: 2)


def _profile_calls(dataset: dict) -> int:
    from resource_explorer.open_data import profile

    return int(profile.profile_price(1)["calls_at_most"])           # an upper bound, never a promise


PROFILE = analyses.Runner("external_data_profile", run_profile, _profile_calls)


# ── the work-list view of the runs ───────────────────────────────────────────

def work_list_runs(registry, store, work_list_slug: str, analysis_id: str) -> dict:
    """For each dataset of the work list, the newest run of `analysis_id`: its status and its words, so a press that had one portal
    fail says WHICH dataset failed and why. A dataset never run says so. Dataset facts only; nothing per-user."""
    runs = []
    for ds in store.datasets_in_work_list(work_list_slug):
        newest = None
        for entry in registry.list_activity(entity_type="open_data", entity_slug=ds["slug"], limit=50):
            try:
                detail = json.loads(entry.get("detail") or "{}")
            except (TypeError, ValueError):
                detail = {}
            if detail.get("analysis_id") == analysis_id:
                newest = entry
                break
        runs.append({"slug": ds["slug"], "title": ds["title"], "status": newest["status"] if newest else "never_run",
                     "summary": (newest.get("summary") or "") if newest else "never run",
                     "activity_id": newest.get("id") if newest else None})
    count = lambda status: sum(1 for r in runs if r["status"] == status)        # noqa: E731
    return {"work_list": work_list_slug, "analysis_id": analysis_id, "runs": runs,
            "ok": count("ok"), "failed": count("error"), "running": count("running"), "never_run": count("never_run")}
