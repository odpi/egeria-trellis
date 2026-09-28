"""Project management endpoints — list, get, remove, refresh."""
from __future__ import annotations

import asyncio
import json
import re
import logging

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

log = logging.getLogger(__name__)
router = APIRouter()


# Disposition values that hide a repo from the sidebar by default — matches
# the frontend's _HIDDEN_DISPOSITIONS set exactly (index.html). "ignored" =
# passed on it early; "abandoned" = went further, then decided against it
# ("Scouting workflow redesign" plan, D3).
_HIDDEN_DISPOSITIONS = {"ignored", "abandoned"}


# Who a queued run is attributed to. `""` everywhere until RE adopts
# trellis-auth (plan §4) — imported rather than hard-coded so adopting auth is
# one edit in run_queue.py, not a sweep of every enqueue site.
from resource_explorer.run_queue import requested_by as _requested_by  # noqa: E402


class ProjectSummary(BaseModel):
    slug: str
    display_name: str
    github_url: str
    description: str
    status: str
    collections: list[str]
    last_indexed_at: str
    last_commit_sha: str
    group_slug: str = ""
    last_surveyed_at: str = ""  # "" = never surveyed (coarse scan or deep)
    is_published: bool = False  # egeria_asset_guid set AND linkage not stale — see egeria_linkage.describe_publish_status
    egeria_publish_note: str = ""  # non-empty only when a GUID is cached but the linkage is stale
    disposition: str = "undecided"  # undecided | tracking | investigating | abandoned | ignored — see registry.py's repo_dispositions
    working_set_hidden: bool = False  # personal view filter, separate axis from disposition — see registry.py's resource_working_set


def _to_summary(p, registry=None) -> ProjectSummary:
    disposition = "undecided"
    working_set_hidden = False
    guid = getattr(p, "egeria_asset_guid", "") or ""
    publish_status = {"is_published": bool(guid), "note": ""}
    if registry is not None:
        disp = registry.get_disposition(p.github_url)
        if disp:
            disposition = disp["disposition"]
        working_set_hidden = registry.is_working_set_hidden("repo", p.slug)
        from resource_explorer.egeria_linkage import describe_publish_status
        publish_status = describe_publish_status(registry, "repo", p.slug, guid)
    return ProjectSummary(
        slug=p.slug,
        display_name=p.display_name,
        github_url=p.github_url,
        description=p.description,
        status=p.status.value,
        collections=p.collections,
        last_indexed_at=p.last_indexed_at,
        last_commit_sha=p.last_commit_sha,
        group_slug=getattr(p, "group_slug", "") or "",
        last_surveyed_at=getattr(p, "last_surveyed_at", "") or "",
        is_published=publish_status["is_published"],
        egeria_publish_note=publish_status["note"],
        disposition=disposition,
        working_set_hidden=working_set_hidden,
    )


@router.get("/", response_model=list[ProjectSummary])
async def list_projects(include_ignored: bool = False, include_working_set_hidden: bool = False) -> list[ProjectSummary]:
    """Excludes `ignored`/`abandoned`-disposition repos and working-set-hidden
    repos by default — the sidebar's "Show hidden (N)" toggle passes both
    params to reveal everything. Reversible, not a hard delete either way
    (registry.py's repo_dispositions/resource_working_set rows are
    untouched) ("Discover repos to scout" plan, D10; "Scouting workflow
    redesign" plan, D3/D4)."""
    from resource_explorer.registry import ProjectRegistry
    registry = ProjectRegistry()
    summaries = [_to_summary(p, registry) for p in registry.list_all()]
    if not include_ignored:
        summaries = [s for s in summaries if s.disposition not in _HIDDEN_DISPOSITIONS]
    if not include_working_set_hidden:
        summaries = [s for s in summaries if not s.working_set_hidden]
    return summaries


class GroupStats(BaseModel):
    project_count: int
    database_count: int
    filesystem_count: int
    stars: int
    forks: int
    watchers: int
    open_issues: int
    commits_30d: int
    commits_90d: int
    contributors_count: int
    languages: dict[str, int]
    db_schema_count: int
    db_table_count: int
    db_column_count: int
    fs_file_count: int
    fs_data_file_count: int


class GroupSummary(BaseModel):
    slug: str
    display_name: str
    description: str = ""
    projects: list[str]
    databases: list[str]
    filesystems: list[str]
    stats: GroupStats


class GroupCreate(BaseModel):
    slug: str
    display_name: str
    description: str = ""


class GroupAssign(BaseModel):
    resource_type: str  # "repo", "database", "filesystem"
    group_slug: str     # "" clears the assignment


class GroupSuggestion(BaseModel):
    org: str
    repo_slugs: list[str]
    repo_count: int
    suggested_display_name: str


@router.get("/groups/suggestions", response_model=list[GroupSuggestion])
async def suggest_groups() -> list[GroupSuggestion]:
    """Suggests — never auto-applies — groupings for currently-ungrouped
    repos that share a GitHub org. Importing multiple repos from the same
    org-scoped search at once (e.g. unitycatalog/unitycatalog,
    unitycatalog/unitycatalog-python, unitycatalog/unitycatalog-rs) is a
    strong signal they're the same logical project, but nothing at import
    time assigns a group automatically — grouping stays a deliberate,
    explicit action (matches how `set_project_group()` is only ever called
    from a real user click, never from `add()`/import code paths). Only
    orgs with 2+ ungrouped repos are surfaced; already-grouped repos are
    excluded so an accepted suggestion never gets re-suggested."""
    from resource_explorer.registry import ProjectRegistry
    from resource_explorer.github.client import GitHubClient

    registry = ProjectRegistry()
    by_org: dict[str, list[str]] = {}
    for p in registry.list_all():
        if getattr(p, "group_slug", ""):
            continue
        slug = GitHubClient._url_to_slug(p.github_url)
        if "/" not in slug:
            continue
        org = slug.split("/")[0]
        by_org.setdefault(org, []).append(p.slug)

    suggestions = [
        GroupSuggestion(org=org, repo_slugs=slugs, repo_count=len(slugs), suggested_display_name=org)
        for org, slugs in by_org.items()
        if len(slugs) >= 2
    ]
    suggestions.sort(key=lambda s: -s.repo_count)
    return suggestions


@router.get("/groups", response_model=list[GroupSummary])
async def list_groups() -> list[GroupSummary]:
    from resource_explorer.registry import ProjectRegistry
    registry = ProjectRegistry()
    out = []
    for g in registry.list_groups():
        repos = registry.list_projects_in_group(g.slug)
        dbs = registry.list_databases_in_group(g.slug)
        fss = registry.list_filesystems_in_group(g.slug)
        stats = registry.get_group_aggregate_stats(g.slug)
        out.append(GroupSummary(
            slug=g.slug, display_name=g.display_name, description=g.description,
            projects=[m.slug for m in repos],
            databases=[m.slug for m in dbs],
            filesystems=[m.slug for m in fss],
            stats=GroupStats(**stats),
        ))
    return out


@router.post("/groups", response_model=GroupSummary)
async def create_group(body: GroupCreate) -> GroupSummary:
    from resource_explorer.registry import ProjectRegistry
    registry = ProjectRegistry()
    registry.create_group(body.slug, body.display_name, body.description)
    group = registry.get_group(body.slug)
    stats = registry.get_group_aggregate_stats(group.slug)
    return GroupSummary(
        slug=group.slug, display_name=group.display_name, description=group.description,
        projects=[], databases=[], filesystems=[], stats=GroupStats(**stats),
    )


@router.delete("/groups/{slug}")
async def delete_group(slug: str) -> dict:
    from resource_explorer.registry import ProjectRegistry
    registry = ProjectRegistry()
    if not registry.get_group(slug):
        raise HTTPException(status_code=404, detail=f"Group '{slug}' not found")
    unassigned = registry.delete_group(slug)
    return {"removed": slug, "resources_unassigned": unassigned}


@router.post("/{slug}/group")
async def assign_group(slug: str, body: GroupAssign) -> dict:
    from resource_explorer.registry import ProjectRegistry
    registry = ProjectRegistry()
    
    if body.group_slug and not registry.get_group(body.group_slug):
        raise HTTPException(status_code=404, detail=f"Group '{body.group_slug}' not found")
        
    if body.resource_type == "repo":
        if not registry.get(slug):
            raise HTTPException(status_code=404, detail=f"Repository '{slug}' not found")
        registry.set_project_group(slug, body.group_slug)
    elif body.resource_type == "database":
        if not registry.get_database(slug):
            raise HTTPException(status_code=404, detail=f"Database '{slug}' not found")
        registry.set_database_group(slug, body.group_slug)
    elif body.resource_type == "filesystem":
        if not registry.get_filesystem(slug):
            raise HTTPException(status_code=404, detail=f"Filesystem '{slug}' not found")
        registry.set_filesystem_group(slug, body.group_slug)
    else:
        raise HTTPException(status_code=400, detail=f"Invalid resource type '{body.resource_type}'")
        
    return {"slug": slug, "resource_type": body.resource_type, "group_slug": body.group_slug}


@router.get("/{slug}", response_model=ProjectSummary)
async def get_project(slug: str) -> ProjectSummary:
    from resource_explorer.registry import ProjectRegistry
    registry = ProjectRegistry()
    project = registry.get(slug)
    if not project:
        raise HTTPException(status_code=404, detail=f"Project '{slug}' not found")
    return _to_summary(project, registry)


class ScoutingOverview(BaseModel):
    """Light, fast-to-render Scouting-tier facts — GitHub API-derived only
    (no Enrichment data, which isn't available yet at this lifecycle stage —
    see the plan's discussion of purpose vs. description). Deliberately
    thin: "enough to answer 'should I analyze this further?'", not the deep
    survey report (still reachable via the "View full report" link, unchanged)."""
    slug: str
    display_name: str
    github_url: str
    description: str = ""       # GitHub's own repo description, not Enrichment's purpose
    primary_language: str = ""
    stars: int = 0
    forks: int = 0
    contributors_count: int = 0
    last_pushed_at: str = ""
    repo_size_kb: int = 0
    last_surveyed_at: str = ""
    # Last successful Coarse Profile refresh (IngestionPipeline.refresh_profile())
    # — the one Scouting-tier action that had no staleness signal at all
    # before this existed.
    last_profiled_at: str = ""
    is_published: bool = False
    # Non-empty only when a GUID IS cached but the linkage is stale — see
    # `DatabaseSummary.egeria_publish_note` (databases.py) for the full
    # rationale. Redundant with `egeria_link_stale` below (both come from the
    # same linkage row) but carries the ready-to-render sentence rather than
    # a bare boolean.
    egeria_publish_note: str = ""
    # When egeria_publish last actually wrote to the catalog — distinct from
    # is_published (a point-in-time boolean derived from whether a GUID is
    # currently set). The data already existed (project_egeria_surveys.
    # published_at, one row per publish) but was never surfaced here before;
    # only the bare yes/no was shown.
    last_published_at: str = ""
    disposition: str = "undecided"
    disposition_reason: str = ""
    # Computed from archived/disabled/is_fork/is_template — "archived" >
    # "disabled" > "fork" > "template" > "active", first match wins.
    lifecycle_state: str = "active"
    homepage: str = ""
    security_and_analysis: dict = {}
    deployments_count: int = 0
    latest_deployment_at: str = ""
    latest_deployment_environment: str = ""
    latest_deployment_ref: str = ""
    # Set when this repo's cached Egeria GUID was found not to exist
    # (resource_explorer/egeria_linkage.py). Carried here because this card is
    # where the "Published to Egeria" badge is shown, and that badge is actively
    # misleading while the link is broken — it reports a catalog entry RE can no
    # longer reach.
    egeria_link_stale: bool = False
    egeria_link_stale_guid: str = ""
    # Set when the latest published SurveyReport's GUID no longer resolves in
    # Egeria (PUBLISH-STATE-AFTER-REDEPLOY-CORRECTIONS.md / REPLY-PUBLISH-
    # STATE-GO-AHEAD.md) — a fourth publish-state reading distinct from
    # egeria_link_stale above (that one is the ASSET GUID; this is the
    # REPORT GUID a publish claim points to). Flagged, never cleared
    # automatically — see egeria_resync.py's _do_flag_vanished_publishes.
    publish_stale: bool = False
    publish_stale_guid: str = ""


@router.get("/{slug}/scouting-overview", response_model=ScoutingOverview)
async def get_scouting_overview(slug: str) -> ScoutingOverview:
    from resource_explorer.registry import ProjectRegistry
    registry = ProjectRegistry()
    project = registry.get(slug)
    if not project:
        raise HTTPException(status_code=404, detail=f"Project '{slug}' not found")

    stats = registry.get_latest_project_stats(project.slug) or {}
    disp = registry.get_disposition(project.github_url) or {}
    latest_survey = registry.get_latest_egeria_survey(project.slug)

    lifecycle_state = "active"
    if stats.get("archived"):
        lifecycle_state = "archived"
    elif stats.get("disabled"):
        lifecycle_state = "disabled"
    elif stats.get("is_fork"):
        lifecycle_state = "fork"
    elif stats.get("is_template"):
        lifecycle_state = "template"

    try:
        security_and_analysis = json.loads(stats.get("security_and_analysis_json") or "{}")
    except (TypeError, ValueError):
        security_and_analysis = {}

    linkage = registry.get_egeria_linkage("repo", project.slug) or {}
    publish_linkage = registry.get_egeria_linkage("repo_publish", project.slug) or {}
    from resource_explorer.egeria_linkage import describe_publish_status
    publish_status = describe_publish_status(
        registry, "repo", project.slug, project.egeria_asset_guid or "")

    return ScoutingOverview(
        egeria_link_stale=linkage.get("status") == "stale",
        egeria_link_stale_guid=linkage.get("stale_guid", ""),
        publish_stale=publish_linkage.get("status") == "stale",
        publish_stale_guid=publish_linkage.get("stale_guid", ""),
        slug=project.slug,
        display_name=project.display_name,
        github_url=project.github_url,
        description=project.description,
        primary_language=stats.get("primary_language") or "",
        stars=stats.get("stars") or 0,
        forks=stats.get("forks") or 0,
        contributors_count=stats.get("contributors_count") or 0,
        last_pushed_at=stats.get("last_pushed_at") or "",
        repo_size_kb=stats.get("repo_size_kb") or 0,
        last_surveyed_at=project.last_surveyed_at,
        last_profiled_at=project.last_profiled_at,
        is_published=publish_status["is_published"],
        egeria_publish_note=publish_status["note"],
        last_published_at=(latest_survey or {}).get("published_at") or "",
        disposition=disp.get("disposition", "undecided"),
        disposition_reason=disp.get("reason", ""),
        lifecycle_state=lifecycle_state,
        # Prefer the surveyed answer over GitHub's raw field. HomepageSurveyor
        # writes projects.homepage_url having tried GitHub's homepage first, then
        # the packaging manifests, then the README, then the repo URL — so it is
        # either the same value or a better one. stats.homepage remains the
        # fallback for repos surveyed before that step existed.
        homepage=(project.homepage_url or stats.get("homepage") or ""),
        security_and_analysis=security_and_analysis,
        deployments_count=stats.get("deployments_count") or 0,
        latest_deployment_at=stats.get("latest_deployment_at") or "",
        latest_deployment_environment=stats.get("latest_deployment_environment") or "",
        latest_deployment_ref=stats.get("latest_deployment_ref") or "",
    )


class ScoutingScanResult(BaseModel):
    status: str  # "ok" | "error"
    slug: str
    message: str = ""
    error: str | None = None


class ScoutingScanStarted(BaseModel):
    status: str = "started"
    slug: str
    activity_id: str
    # The queue row that will execute it. Added in step 2b; the frontend still
    # polls activity_id, so this is additive, never a replacement.
    run_id: str = ""


# The scan itself, the two "has anything been measured" helpers, and the
# activity-recording wrapper all live in resource_explorer/workflows/scouting.py
# now (step 2b). These are the names this module and its tests already used;
# they stay as aliases so a caller does not have to know which side of the move
# a function ended up on.
from resource_explorer.workflows.scouting import (  # noqa: E402
    execute_and_record_scouting_scan as _run_scouting_scan_background_impl,
    question_has_data as _question_has_data,
    results_have_data as _results_have_data,
    run_scouting_scan as _run_scouting_scan_workflow,
)


def _run_scouting_scan_sync(slug: str, registry) -> ScoutingScanResult:
    """Thin adapter: the workflow returns its own dataclass, the route's
    response model is pydantic. Kept so callers and tests inside this module
    keep the shape they had."""
    result = _run_scouting_scan_workflow(slug, registry)
    return ScoutingScanResult(
        status=result.status, slug=result.slug,
        message=result.message, error=result.error or None,
    )


def _run_scouting_scan_background(slug: str, activity_id: str) -> None:
    """Run the scan and record it. No longer spawned from a request — the
    queue worker calls the workflow directly; this survives as the seam the
    route's own tests patch."""
    _run_scouting_scan_background_impl(slug, activity_id)


@router.post("/{slug}/scouting-scan", response_model=ScoutingScanStarted)
async def run_scouting_scan(slug: str) -> ScoutingScanStarted:
    """Fast, API-only coarse scan (repo stats + language classification) —
    runs the "Repo Coarse Scout" Survey Definition via the same executor
    Discovery's manual "Run" button uses. Local-only by default, no
    auto-publish, matching the existing convention that publish is always
    a separate, deliberate action.

    **Enqueues; does not run.** Until step 2b this spawned a daemon thread in
    whichever web process served the request — see run_queue.py for why that
    had to stop. The response is unchanged: the activity entry is still created
    here, still returned, and the frontend still polls
    GET /api/activity/{activity_id}. `run_id` is added alongside it for anyone
    who wants the queue's own view (GET /api/runs/{run_id}); nothing existing
    reads it.

    With `--no-embed-worker` and no `resource-explorer worker` running, a
    queued row stays queued. That is the honest state — visible in
    GET /api/runs?state=queued — rather than the previous behaviour, where a
    web process claiming to serve HTTP only was quietly executing surveys.
    """
    from resource_explorer.activity_logger import log_survey
    from resource_explorer.registry import ProjectRegistry

    registry = ProjectRegistry()
    project = registry.get(slug)
    if not project:
        raise HTTPException(status_code=404, detail=f"Project '{slug}' not found")

    activity_id = log_survey(
        registry, entity_type="repo", entity_slug=slug,
        entity_name=project.display_name, entity_location=project.github_url,
        intent="scouting", status="running",
        summary=f"Scouting Scan running for {project.display_name}…",
    )
    run_id = registry.enqueue_run(
        "scouting_scan", {"slug": slug}, result_ref=activity_id,
        requested_by=_requested_by(),
    )
    log.info("enqueued scouting_scan run %s for %s (activity %s)", run_id, slug, activity_id)

    return ScoutingScanStarted(slug=slug, activity_id=activity_id, run_id=run_id)


class QuestionChecklistEntry(BaseModel):
    question: str
    stage: str  # single Funnel Stage, may be slash-combined (e.g. "Analysis/Enrichment")
    perspectives: list[str] = []
    kind: str  # analysis | direct | registry | human | chart | gap | partial | mixed | unknown
    analysis_ids: list[str] = []
    # `analysis_id:check_name` refs from the catalog — the finer key. When a
    # question declares these, a consumer can show just those findings from
    # an analysis that also answers five other questions, instead of the whole
    # analysis six times. Empty means "no check refs authored", not "no
    # checks apply" — fall back to analysis_ids.
    checks: list[str] = []
    note: str = ""
    answering_mechanism: str = ""
    # What this answer can and cannot claim, from the catalog's
    # Rationale/Source column. Distinct from `note`, which says how the
    # question is answered: this says what the answer is not entitled to
    # mean. secret_scan never claims "no secrets", only no matches against
    # this ruleset in this snapshot.
    rationale: str = ""
    # The catalog's own changelog for this row — past tense, maintainer-facing.
    catalog_history: str = ""
    # None = not applicable (direct/registry/human/chart/gap kinds — no RE
    # analysis backs these, so there's nothing to check); True/False only
    # for analysis/partial/mixed kinds, computed best-effort per resource.
    has_data: bool | None = None
    purposes: list[str] = []
    # Why this entry is in the result: which Perspectives/Purposes matched
    # and whether Purpose promoted it. See docs/context-compilation-design.md
    # §11 -- the derivation is the explanation with content, distinct from
    # "what got shown".
    derivation: dict = {}


class QuestionChecklist(BaseModel):
    phase: str
    perspectives: list[str] = []
    purposes: list[str] = []
    questions: list[QuestionChecklistEntry] = []


@router.get("/{slug}/scouting-questions", response_model=QuestionChecklist)
async def get_scouting_questions(
    slug: str,
    phase: str = "scouting",
    perspectives: str | None = None,
    purposes: str | None = None,
) -> QuestionChecklist:
    """Per-phase Question checklist -- which of the authored Scouting
    questions (docs/dr-egeria/resource_questions.csv, via
    question_catalog_reader.py) this phase raises or can answer, filtered
    by the active Perspective set (comma-separated query param, matching
    the UI's activePerspectives multi-select -- see index.html's
    togglePerspective()), with a computed has_data flag for
    analysis/partial/mixed-kind questions.

    As of the database/filesystem generalization (docs/Backlog.md,
    "scouting-questions was repo-only"), this is a thin wrapper over
    `workflows.scouting.build_question_checklist` -- see that function's
    docstring. repo's own behavior here is unchanged."""
    from resource_explorer.registry import ProjectRegistry
    from resource_explorer.workflows.scouting import build_question_checklist

    registry = ProjectRegistry()
    project = registry.get(slug)
    if not project:
        raise HTTPException(status_code=404, detail=f"Project '{slug}' not found")

    persp_list = [p.strip() for p in (perspectives or "").split(",") if p.strip()]
    purp_list = [p.strip() for p in (purposes or "").split(",") if p.strip()]
    return QuestionChecklist(
        **build_question_checklist(registry, "repo", slug, phase, persp_list, purp_list)
    )


# POST /{slug}/profile-scan was retired 2026-08-20. It refreshed
# project_file_inventory/project_data_profiles from a zipball and auto-chained
# the language-classification survey — all of which is now done by the
# repo_file_inventory survey step and the "Coarse Profile Survey" Survey
# Definition, reachable from Scouting's Survey sub-tab like any other survey.
#
# It had no caller: no UI code invoked it, and the scheduler calls
# IngestionPipeline.refresh_profile() directly rather than going through HTTP.
# It was also the last thing referring to a "Profile tab" that was never built.
# refresh_profile() itself is untouched and still used by the scheduler and
# IncrementalIndexer — only this HTTP surface is gone.


class AnalysisRunResult(BaseModel):
    """Still the response shape for run_scoped_analysis below — that route is
    out of scope for the backgrounding done here (2026-08-30's fix is
    specifically the analysis-card path's docs/Backlog.md "no prompt, no
    progress" entry). run_single_analysis itself no longer returns this: see
    its own docstring."""
    status: str  # "ok" | "error"
    slug: str
    analysis_id: str
    message: str = ""
    error: str | None = None


# The analysis-run and stage-batch workflows moved to
# resource_explorer/workflows/analysis.py in step 2b — including the
# ingest-vs-survey branch, the has_assigned_egeria_project auto-publish gate and
# its three-state `published`, and the summary text. Behaviour is unchanged; the
# functions are simply reachable from the CLI and the run queue now as well as
# from here. The names below are the seams this module and its tests use.
from resource_explorer.workflows.analysis import (  # noqa: E402
    STAGE_BATCH_ANALYSIS_ID as _STAGE_BATCH_ANALYSIS_ID,
    execute_and_record_analysis as _run_single_analysis_background_impl,
    execute_and_record_stage_batch as _run_stage_batch_background_impl,
    assess_freshness as _assess_freshness,
    estimate_run_cost as _estimate_run_cost,
    resolve_analysis_plan as _resolve_analysis_plan,
    resolve_stage_step_keys as _resolve_stage_step_keys,
    run_analysis as _run_analysis_workflow,
)


def _run_single_analysis_sync(slug: str, analysis_id: str, is_ingest: bool,
                              steps: list[str] | None) -> dict:
    """Thin adapter to the workflow, returning the plain dict this module's
    callers and tests already read."""
    return _run_analysis_workflow(
        slug, analysis_id, is_ingest=is_ingest, steps=steps,
    ).to_dict()


def _run_single_analysis_background(slug: str, analysis_id: str, activity_id: str,
                                    *, publish: str | None = None) -> None:
    _run_single_analysis_background_impl(slug, analysis_id, activity_id, publish=publish)


def _run_stage_batch_background(slug: str, stage: str, step_keys: list[str],
                                activity_id: str) -> None:
    _run_stage_batch_background_impl(slug, stage, step_keys, activity_id)


@router.post("/{slug}/analyses/{analysis_id}/run")
async def run_single_analysis(slug: str, analysis_id: str,
                              force: bool = False, publish: str | None = None) -> dict:
    """Queue one named analysis's mapped survey step(s) — the per-card "Run"
    action in Analysis/Assessment.

    **Enqueues; does not run.** Before step 2b this spawned a daemon thread
    inside the web process (itself a fix for an earlier version that blocked the
    whole HTTP request for the run's duration — measured 100s+ for
    architecture_recovery). The response contract is untouched: an activity_id
    comes back immediately, the frontend polls GET /api/activity/{id} until it
    is terminal, and reads the run's result out of that entry's own `detail`.
    What changed is who executes it — a `worker` role process claiming the row,
    which is the whole point of the queue.

    `publish` ("wait" | "background", query param — same convention as
    `force`): the per-run choice of whether to wait for the Egeria publish or
    enqueue it and move on (project owner, 2026-09-13 — measured: the survey
    steps take ~0.2s, the synchronous publish ~3min at ~3.6s/write for 53
    writes). Omitted keeps `RunsConfig.publish_inline`'s default — unchanged
    behaviour for every caller that does not ask.

    Validation still happens synchronously, so an unknown analysis_id is a 400
    rather than a queued row that fails a minute later in a different process.
    """
    from resource_explorer.activity_logger import log_analysis_run
    from resource_explorer.registry import ProjectRegistry

    if publish is not None and publish not in ("wait", "background"):
        raise HTTPException(
            status_code=400,
            detail=f"publish must be 'wait' or 'background', got {publish!r}",
        )

    registry = ProjectRegistry()
    project = registry.get(slug)
    if not project:
        raise HTTPException(status_code=404, detail=f"Project '{slug}' not found")

    # action:"ingest" (currently just rag_ingestion) isn't a SurveyOrchestrator
    # step at all — it re-embeds content into pgvector via IncrementalIndexer,
    # not a survey. Checked before the step-map lookup, same as scheduler.py's
    # own action:"publish" special-case, so an unknown analysis_id fails fast
    # and synchronously rather than being queued just to fail later.
    is_ingest, steps = _resolve_analysis_plan(analysis_id)
    if not is_ingest and not steps:
        raise HTTPException(
            status_code=400,
            detail=f"Analysis '{analysis_id}' has no mapped survey step(s) — "
                   "either it's a publish action (not a survey) or an unknown id.",
        )

    # Freshness gate — user-initiated runs only. Measured 2026-09-10: 20.7% of
    # all successful runs happened within five minutes of an identical prior
    # run, and ~95% of those produced no different findings, at up to 110s each.
    #
    # Declines rather than running, and SAYS SO in the same response shape the
    # caller already handles. A silent skip would be the same defect as every
    # other zero here — a Run that does nothing must say which nothing it did.
    # `force=true` always runs; a never-run or errored analysis is never fresh.
    #
    # The scheduler is deliberately NOT gated (project owner, 2026-09-10):
    # skipping nightly sweeps changes what "nightly" means, which is a different
    # decision from sparing someone a redundant click.
    from resource_explorer.config import get_config

    cfg = get_config().runs
    if cfg.gate_user_runs and not force and not is_ingest:
        freshness = _assess_freshness(registry, "repo", slug, analysis_id)
        if freshness.fresh:
            log.info("declined analysis_run for %s/%s — fresh via %s (%.0fs old)",
                     slug, analysis_id, freshness.via, freshness.age_seconds or 0)
            # The skip names the price (designer ruling, 2026-09-13): "too
            # fresh" alone reads as an obstacle; "too fresh, and here is what
            # a re-run costs" reads as the system being careful with someone's
            # time. The basis travels with the figure so a declared word is
            # never mistaken for a measurement.
            cost = _estimate_run_cost(registry, analysis_id)
            return {
                "status": "skipped",
                "reason": "already-fresh",
                "detail": f"{freshness.reason(analysis_id)} {cost.sentence()}",
                "last_run_at": freshness.last_run_at,
                "last_run_via": freshness.via,
                "age_seconds": int(freshness.age_seconds or 0),
                "rerun_cost_seconds": cost.seconds,
                "rerun_cost_basis": cost.basis,
                "rerun_cost_runs": cost.runs,
                "rerun_cost_via": cost.via,
                "rerun_steps_seconds": cost.steps_seconds,
                "rerun_publish_seconds": cost.publish_seconds,
                "rerun_split_runs": cost.split_runs,
                "activity_id": None,
                "run_id": None,
            }

    activity_id = log_analysis_run(
        registry, "repo", slug, project.display_name, "running",
        f"Running '{analysis_id}' on {slug}…", analysis_id, published=None,
    )
    run_id = registry.enqueue_run(
        "analysis_run", {"slug": slug, "analysis_id": analysis_id, "publish": publish},
        result_ref=activity_id, requested_by=_requested_by(),
    )
    log.info("enqueued analysis_run %s for %s/%s (activity %s, publish=%s)",
             run_id, slug, analysis_id, activity_id, publish)

    # The run dialog needs to say what a background publish saved — that
    # requires the price on the enqueue response too, not just the
    # freshness-skip path above. `estimate_run_cost` has no broad except by
    # design (it should fail loudly if the queue/catalog can't be read), but
    # a broken estimate must never take the run down with it: the run is
    # already enqueued by this point, so failure here is recorded
    # observably in the response instead of a bare log line — the
    # silent-success ratchet (tests/test_no_silent_success.py) forbids
    # swallowing it quietly.
    try:
        cost = _estimate_run_cost(registry, analysis_id)
        result: dict = {
            "rerun_cost_seconds": cost.seconds,
            "rerun_steps_seconds": cost.steps_seconds,
            "rerun_publish_seconds": cost.publish_seconds,
            "rerun_cost_basis": cost.basis,
            "rerun_cost_runs": cost.runs,
            "rerun_split_runs": cost.split_runs,
            # The "declared '{word}'" sentence variant needs the actual
            # catalog word, not just the basis that names its kind — the
            # frontend renders the sentence itself here (unlike the skip
            # path above, which bakes it into `detail` server-side via
            # cost.sentence()).
            "rerun_cost_declared": cost.declared,
        }
    except Exception as exc:
        log.warning("estimate_run_cost failed for %s/%s: %s", slug, analysis_id, exc)
        result = {
            "rerun_cost_seconds": None,
            "rerun_steps_seconds": None,
            "rerun_publish_seconds": None,
            "rerun_cost_basis": "unavailable",
            "rerun_cost_runs": 0,
            "rerun_split_runs": 0,
            "rerun_cost_declared": None,
            "rerun_cost_error": str(exc),
        }

    return {
        "status": "started", "activity_id": activity_id, "run_id": run_id,
        **result,
    }


@router.post("/{slug}/analyses/stage/{stage}/run")
async def run_stage_batch(slug: str, stage: str) -> dict:
    """'Run all <Stage>' — the pinned, visually-distinct action at the top of
    Scouting/Discovery/Assessment/Analysis's card grids (2026-09-03, direct
    feedback: "make it first and separate so a user can easily just select that
    and be confident that all the surveys are being run").

    Deliberately NOT a re-use of the existing per-stage Egeria Survey
    Definitions — those cover 3/3, 5/7, 8/14 and 7/10 of each stage's individual
    catalog entries respectively (measured 2026-09-03), so pinning one of them
    under a "Run all" label would silently under-run the stage. The step set is
    derived live from the catalog instead; see
    workflows.analysis.resolve_stage_step_keys.

    **Enqueues; does not run** — same change as the per-analysis route above,
    same unchanged response contract.
    """
    from resource_explorer.activity_logger import log_analysis_run
    from resource_explorer.registry import ProjectRegistry

    registry = ProjectRegistry()
    project = registry.get(slug)
    if not project:
        raise HTTPException(status_code=404, detail=f"Project '{slug}' not found")

    step_keys, analysis_count = _resolve_stage_step_keys(stage)
    if not step_keys:
        raise HTTPException(
            status_code=400,
            detail=f"Stage '{stage}' has no batch-runnable local analyses.",
        )

    activity_id = log_analysis_run(
        registry, "repo", slug, project.display_name, "running",
        f"Running all {len(step_keys)} {stage} step(s) on {slug}…",
        _STAGE_BATCH_ANALYSIS_ID, published=None,
    )
    run_id = registry.enqueue_run(
        "stage_batch", {"slug": slug, "stage": stage, "step_keys": step_keys},
        result_ref=activity_id, requested_by=_requested_by(),
    )
    log.info("enqueued stage_batch run %s for %s/%s (activity %s)",
             run_id, slug, stage, activity_id)

    return {"status": "started", "activity_id": activity_id, "run_id": run_id,
            "step_count": len(step_keys), "analysis_count": analysis_count}


@router.get("/{slug}/analyses/last-activity")
async def get_analyses_last_activity(slug: str) -> dict[str, dict]:
    """{analysis_id: {last_run_at, last_run_status, last_published_at}} for
    every local AnalysisKind — the Analyses cards' equivalent of Survey
    Definitions' /candidates endpoint already returning last_run_at/
    last_published_at inline. Kept as its own lightweight endpoint rather
    than folded into GET /api/analyses/{resource_type} (analyses.py):
    that route is resource-type-wide and shared/cached across every repo of
    the same type (see its own module docstring) — deliberately has no repo
    slug at all. The frontend fetches this alongside GET /api/schedules/...
    in _loadAnalysisCatalogPanel() and merges both client-side into each
    card, same pattern that route already used for per-card schedule state.

    last_published_at is real per-analysis-id data even though this route
    is new: get_last_published_annotation_types() (added earlier the same
    day for the Survey Results dashboards) already has everything needed —
    each analysis_id's own annotation_types (analysis_catalog.yaml) is the
    same join key used there, just applied per-analysis instead of
    per-dashboard-of-several-analyses.

    The actual attribution logic (two-tier publish attribution, the
    measured/never_run/not_established run basis, __auto_publishes__) now
    lives in `workflows.analysis.build_analysis_last_activity` — generalized
    there so `GET /api/databases/{slug}/analyses/last-activity` and the
    filesystem equivalent (`web/routes/databases.py`, `web/routes/
    filesystems.py`) can share it rather than fork this whole function; this
    route is now a thin 404-translating adapter, same pattern as the
    `/depth-offer` and `/catalogue-depth-offer` routes below."""
    from resource_explorer.registry import ProjectRegistry
    from resource_explorer.workflows.analysis import build_analysis_last_activity

    registry = ProjectRegistry()
    if not registry.get(slug):
        raise HTTPException(status_code=404, detail=f"Project '{slug}' not found")

    return build_analysis_last_activity(registry, "repo", slug)


@router.get("/{slug}/depth-offer")
async def get_depth_offer(slug: str) -> dict:
    """The /next pane's DepthOffer (designer, 2026-09-13): the assessment/
    analysis-tier analyses that have never run on this repo, priced with the
    measured/declared/unknown split, plus a total across only the measured
    ones. See `workflows/depth_offer.build_depth_offer` for the shape and
    the reasoning — this route is a thin 404-translating adapter, same
    pattern as GET /{slug}/analyses/last-activity above."""
    from resource_explorer.registry import ProjectRegistry
    from resource_explorer.workflows.depth_offer import build_depth_offer

    registry = ProjectRegistry()
    try:
        return build_depth_offer(registry, slug)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/{slug}/catalogue-depth-offer")
async def get_catalogue_depth_offer(slug: str) -> dict:
    """The layer-2 catalogue-depth offer (owner's ruling, 2026-09-15, on the
    designer's REPLY-CATALOGUE-IN-LAYERS.md §3): DepthOffer's three rules
    (not a nag, not a gate, not a scold) applied to promoting accepted
    architecture-recovery verdicts into real Egeria SolutionComponents,
    instead of to never-run analyses. See
    `workflows/catalogue_depth_offer.build_catalogue_depth_offer` for the
    shape and the reasoning — this route is a thin 404-translating adapter,
    same pattern as GET /{slug}/depth-offer above."""
    from resource_explorer.registry import ProjectRegistry
    from resource_explorer.workflows.catalogue_depth_offer import build_catalogue_depth_offer

    registry = ProjectRegistry()
    try:
        return build_catalogue_depth_offer(registry, slug)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


class CatalogueDepthOfferOutcome(BaseModel):
    outcome: str


@router.post("/{slug}/curate/commits/{cid}/layer2-offer")
async def record_catalogue_depth_offer(slug: str, cid: str, body: CatalogueDepthOfferOutcome,
                                       request: Request) -> dict:
    """Record the outcome of the layer-2 catalogue-depth offer on ONE
    catalogue record — once per record (`Curations.record_layer2_offer`
    refuses a second write). `decided_by` comes from the signed-in caller,
    never from the request body, same reasoning `record_depth_offer_route`
    gives for the identical choice."""
    from resource_explorer.auth import get_current_user
    from resource_explorer.curate_plan import Curations
    from resource_explorer.registry import ProjectRegistry
    from resource_explorer.workflows.catalogue_depth_offer import LAYER2_OFFER_OUTCOMES

    if body.outcome not in LAYER2_OFFER_OUTCOMES:
        raise HTTPException(
            status_code=422,
            detail=f"outcome must be one of {sorted(LAYER2_OFFER_OUTCOMES)}, got {body.outcome!r}",
        )
    user = get_current_user(request)
    decided_by = (user or {}).get("user_id") or (user or {}).get("sub") or (user or {}).get("username") or ""
    if not decided_by:
        raise HTTPException(status_code=401, detail="Sign in to answer the offer — it needs someone to have made the decision.")

    registry = ProjectRegistry()
    curations = Curations(registry)
    rec = curations.get(cid)
    if not rec or rec.get("entity_slug") != slug:
        raise HTTPException(status_code=404, detail=f"Catalogue record {cid!r} not found for {slug!r}")
    try:
        return curations.record_layer2_offer(cid, body.outcome, decided_by)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/{slug}/analyses/{analysis_id}/results")
async def get_analysis_results(slug: str, analysis_id: str, depth: str | None = None) -> dict:
    """Latest structured results for one repo analysis — the real
    per-analysis results view (Phase B), replacing "check the full report
    for details." Raw dict, not a Pydantic model — shape genuinely differs
    per analysis_id (dependency ecosystems vs. security check list vs. ...),
    see REPO_ANALYSIS_RESULTS_MAP's individual reader functions."""
    from resource_explorer.registry import ProjectRegistry
    from resource_explorer.surveyors.repo_survey_definition_adapter import REPO_ANALYSIS_RESULTS_MAP

    registry = ProjectRegistry()
    project = registry.get(slug)
    if not project:
        raise HTTPException(status_code=404, detail=f"Project '{slug}' not found")

    entry = REPO_ANALYSIS_RESULTS_MAP.get(analysis_id)
    if not entry:
        raise HTTPException(
            status_code=400,
            detail=f"Analysis '{analysis_id}' has no results view — "
                   "either it's scouting-tier (see Scouting instead) or an unknown id.",
        )
    results_reader, _ = entry

    # `depth` is honoured only by readers that declare a max_depth parameter
    # (today: architecture_recovery). It was already supported end-to-end in
    # arch_recovery/projection.py — DEFAULT_PROJECTION_DEPTH, and a
    # STAGE_PROJECTION_DEPTH table naming the level each stage wants — but no
    # caller ever passed it, so every consumer silently got depth 1 and had no
    # way to ask for anything else. Passing it to a reader that does not accept
    # it would be a TypeError, hence the signature check rather than a blanket
    # kwarg.
    result = None
    if depth is not None:
        import inspect

        if "max_depth" in inspect.signature(results_reader).parameters:
            if depth in ("all", "full", "none"):
                result = results_reader(registry, slug, max_depth=None)
            else:
                try:
                    parsed = int(depth)
                except ValueError:
                    raise HTTPException(
                        status_code=400,
                        detail=f"depth must be an integer or 'all', got {depth!r}",
                    )
                if parsed < 0:
                    raise HTTPException(status_code=400, detail="depth must be >= 0")
                result = results_reader(registry, slug, max_depth=parsed)
    if result is None:
        result = results_reader(registry, slug)

    # destination/destination_basis per finding (SPEC-ACTIONABLE-AND-HONEST.md
    # §3, resource_explorer/destinations.py) — the same annotation
    # Fact.as_dict() applies, added here too because this route returns the
    # raw results dict directly rather than through FactLayer. `_status`
    # (result_status.py, when the reader attached one) supplies the
    # whole-analysis state so rule (1) still wins over a per-row label.
    if isinstance(result, dict) and isinstance(result.get("checks"), list):
        from resource_explorer.destinations import annotate_checks

        status = (result.get("_status") or {}).get("state", "")
        annotate_checks(analysis_id, result["checks"], whole_state=status)
    return result


@router.get("/{slug}/analyses/{analysis_id}/trend")
async def get_analysis_trend(slug: str, analysis_id: str) -> dict:
    """Raw JSON trend data for one repo analysis — {runs: [{surveyed_at,
    value, ...}, ...]}, matching the existing survey_history endpoint's
    raw-JSON-not-Plotly-figure convention (D6) rather than building a new
    server-side Plotly figure per analysis type."""
    from resource_explorer.registry import ProjectRegistry
    from resource_explorer.surveyors.repo_survey_definition_adapter import REPO_ANALYSIS_RESULTS_MAP

    registry = ProjectRegistry()
    project = registry.get(slug)
    if not project:
        raise HTTPException(status_code=404, detail=f"Project '{slug}' not found")

    entry = REPO_ANALYSIS_RESULTS_MAP.get(analysis_id)
    if not entry:
        raise HTTPException(
            status_code=400,
            detail=f"Analysis '{analysis_id}' has no trend view — "
                   "either it's scouting-tier (see Scouting instead) or an unknown id.",
        )
    _, trend_reader = entry
    if trend_reader is None:
        # e.g. license_classification — a single current-state classification,
        # not a repeated-check story (license rarely changes), so it was
        # registered with trend_reader=None rather than a reader that would
        # always return a flat, near-meaningless one-point-per-run line.
        raise HTTPException(
            status_code=400,
            detail=f"Analysis '{analysis_id}' has no trend view — it's a current-state "
                   "classification, not tracked over time.",
        )
    return {"runs": trend_reader(registry, slug)}


@router.get("/{slug}/survey-results")
async def get_survey_results(slug: str, stage: str = "", include_empty: bool = False) -> dict:
    """Tier 2 — the Survey Results dashboards, off the event loop.

    This aggregation re-runs the same results readers the per-analysis cards
    use, and on the Analysis stage that is not a moment: measured at 109s for
    one repo. Run inline it blocked the whole event loop — a cheap call made
    while it was in flight took 100s, so ONE person opening this pane froze
    the app for everyone.

    Same fix, and same reason, as the `remove` route below it.
    """
    return await asyncio.to_thread(_survey_results_sync, slug, stage, include_empty)


def _survey_results_sync(slug: str, stage: str = "", include_empty: bool = False) -> dict:
    """Tier 2 -- the Survey Results dashboards for this repo.

    stage (optional): restrict to cards belonging to that funnel stage, so each
    intent's own Results tab shows only what it is responsible for. Omitted =
    every stage, the original repo-wide view.

    include_empty (optional): return cards with no stored results too. Off by
    default -- see build_survey_results' has_results comment for why an empty
    card is worse than an absent one.

    As of the database/filesystem generalization (docs/Backlog.md, "By
    analysis" was repo-only), this is a thin wrapper over
    `workflows.analysis.build_survey_results` -- see that function's
    docstring for the full picture, including what changed for the other two
    entity_types. repo's own behavior here is unchanged."""
    from resource_explorer.registry import ProjectRegistry
    from resource_explorer.workflows.analysis import build_survey_results

    registry = ProjectRegistry()
    project = registry.get(slug)
    if not project:
        raise HTTPException(status_code=404, detail=f"Project '{slug}' not found")

    return build_survey_results(registry, "repo", slug, stage, include_empty)


@router.get("/{slug}/survey-results/summary")
async def get_survey_results_summary(slug: str, phase: str = "") -> dict:
    """Tier 1 — the headline tiles, off the event loop.

    Cheap on Scouting (1.8s) and not cheap on Analysis (30s), because the
    headline readers are the same readers. Blocking work does not become safe
    for being usually fast.
    """
    return await asyncio.to_thread(_survey_results_summary_sync, slug, phase)


def _survey_results_summary_sync(slug: str, phase: str = "") -> dict:
    """Tier 1 — a phase-scoped 'is it worth proceeding' stat row
    (docs/survey-results-dashboard-plan.md D5). One stat tile per
    ANALYSIS_KINDS entry whose analysis_catalog.yaml intent matches `phase`
    and has a headline_reader — empty phase returns every tile with a
    headline_reader across all intents."""
    from resource_explorer.registry import ProjectRegistry
    from resource_explorer.surveyors.analysis_catalog_reader import get_analyses
    from resource_explorer.surveyors.repo_survey_definition_adapter import REPO_ANALYSIS_HEADLINE_MAP

    registry = ProjectRegistry()
    project = registry.get(slug)
    if not project:
        raise HTTPException(status_code=404, detail=f"Project '{slug}' not found")

    analyses = get_analyses(resource_type="repo", intent=phase or None)
    tiles = []
    for a in analyses:
        headline_reader = REPO_ANALYSIS_HEADLINE_MAP.get(a["id"])
        if not headline_reader:
            continue
        try:
            headline = headline_reader(registry, slug)
        except Exception:
            headline = None
        if headline:
            tiles.append({"analysis_id": a["id"], "analysis_name": a.get("name", a["id"]), **headline})
    return {"slug": slug, "phase": phase, "tiles": tiles}


class RefreshResult(BaseModel):
    status: str          # "ok" | "error"
    slug: str
    message: str = ""
    error: str | None = None


@router.post("/{slug}/refresh", response_model=RefreshResult)
async def refresh_project(slug: str) -> RefreshResult:
    """Incremental re-index + data profiling, runs synchronously in a thread."""
    from resource_explorer.registry import ProjectRegistry
    project = ProjectRegistry().get(slug)
    if not project:
        raise HTTPException(status_code=404, detail=f"Project '{slug}' not found")

    output_lines: list[str] = []

    def _do_refresh():
        import io, sys
        buf = io.StringIO()
        old_stdout = sys.stdout
        sys.stdout = buf
        try:
            from resource_explorer.ingestion.incremental import IncrementalIndexer
            from resource_explorer.query_cache import QueryCache
            IncrementalIndexer().refresh(project)
            QueryCache().invalidate_project(slug)
        finally:
            sys.stdout = old_stdout
            output_lines.extend(buf.getvalue().splitlines())

    try:
        await asyncio.to_thread(_do_refresh)
    except Exception as exc:
        return RefreshResult(status="error", slug=slug, error=str(exc))

    msg = "; ".join(output_lines) if output_lines else "Done"
    return RefreshResult(status="ok", slug=slug, message=msg)


@router.delete("/{slug}")
async def remove_project(slug: str) -> dict:
    from resource_explorer.registry import ProjectRegistry
    from resource_explorer.vector_store_pg import MultiCollectionStore
    registry = ProjectRegistry()
    project = registry.get(slug)
    if not project:
        raise HTTPException(status_code=404, detail=f"Project '{slug}' not found")

    def _do_remove():
        store = MultiCollectionStore()
        for collection in project.collections:
            try:
                store.drop_collection(collection)
            except Exception:
                # A stale/never-created collection (e.g. a broken registration
                # that errored before ingestion completed) shouldn't block
                # removing the registry row itself — best-effort, matching
                # OrgImporter's stats-fetch-failure precedent elsewhere.
                pass
        registry.remove(slug)

    # Blocking pgvector work — every other route in this file already runs
    # its blocking work via asyncio.to_thread; this one didn't, and a slow/
    # unreachable pgvector call here blocked the whole event loop (found
    # live: a delete on a broken registration hung indefinitely).
    await asyncio.to_thread(_do_remove)
    return {"removed": slug}


# ── sub-resources — the "Select"/"Catalog" stages of the repo scope-
# narrowing funnel (docs/repo-scope-narrowing-funnel.md, D2/D3/D4). Repo
# only for Phase 1; the underlying registry table is already generic
# across resource types (resource_type='repo' here) for when database/
# filesystem catch up. ───────────────────────────────────────────────────

class SubResourceRow(BaseModel):
    locator: str
    kind: str
    cataloged_at: str
    egeria_guid: str = ""


@router.get("/{slug}/sub-resources", response_model=list[SubResourceRow])
async def list_sub_resources(slug: str) -> list[SubResourceRow]:
    """What's currently tracked locally for this repo — backs the selection
    UI's "already cataloged" state so re-opening the panel later shows
    prior selections (D4 — catalog is repeatable, not a one-time gate)."""
    from resource_explorer.registry import ProjectRegistry
    registry = ProjectRegistry()
    if not registry.get(slug):
        raise HTTPException(status_code=404, detail=f"Repository '{slug}' not found")
    rows = registry.list_sub_resources("repo", slug)
    return [
        SubResourceRow(
            locator=r["locator"], kind=r["kind"],
            cataloged_at=r["cataloged_at"], egeria_guid=r.get("egeria_guid") or "",
        )
        for r in rows
    ]


class SubResourceCatalogItem(BaseModel):
    locator: str
    kind: str  # 'file' | 'folder'


class SubResourceCatalogRequest(BaseModel):
    items: list[SubResourceCatalogItem]
    publish_to_egeria: bool = True  # default both (local + Egeria); False = sandbox-mode escape hatch


class SubResourceCatalogResult(BaseModel):
    cataloged: list[str]              # locators tracked locally (incl. auto-included ancestors)
    published: dict[str, str] = {}    # locator -> Egeria guid; empty unless publish_to_egeria


@router.post("/{slug}/sub-resources/catalog", response_model=SubResourceCatalogResult)
async def catalog_sub_resources(slug: str, body: SubResourceCatalogRequest) -> SubResourceCatalogResult:
    """Track the selected sub-resources locally (always), and optionally
    publish them to Egeria as real FileFolder/DataFile assets in the same
    action (D3 — default both; unchecking publish_to_egeria is the
    sandbox-mode escape hatch). Repeatable (D4): re-cataloging an
    already-tracked locator is a no-op that never disturbs its
    egeria_guid. Every selected file's ancestor folders are auto-included
    even if not explicitly selected, mirroring SubResourceSurveyor's own
    ancestor-folder guarantee — NestedFile strictly requires a FileFolder
    parent, so an inconsistent selection would otherwise silently fail to
    publish."""
    from resource_explorer.registry import ProjectRegistry
    from resource_explorer.surveyors.egeria_publisher import EgeriaConnectionError, EgeriaPublisher
    from resource_explorer.surveyors.sub_surveyors import ancestor_folder_paths

    registry = ProjectRegistry()
    project = registry.get(slug)
    if not project:
        raise HTTPException(status_code=404, detail=f"Repository '{slug}' not found")
    if not body.items:
        raise HTTPException(status_code=400, detail="No items given to catalog")

    # Snapshot each item's owners/dates from the current findings into the
    # local row (denormalized — publish_sub_resources() shouldn't need to
    # re-join back to findings that a later survey run might supersede).
    findings_by_path: dict[str, dict] = {}
    for f in registry.query_findings(slug, "repo_sub_resource_survey"):
        detail = {}
        if f.get("detail_json"):
            try:
                detail = json.loads(f["detail_json"])
            except (TypeError, ValueError):
                detail = {}
        findings_by_path[detail.get("path", "")] = detail

    selected_kind_by_locator: dict[str, str] = {item.locator: item.kind for item in body.items}
    for item in body.items:
        if item.kind != "file":
            continue
        for ancestor in ancestor_folder_paths(item.locator):
            selected_kind_by_locator.setdefault(ancestor, "folder")

    cataloged: list[str] = []
    for locator in sorted(selected_kind_by_locator):
        registry.catalog_sub_resource(
            "repo", slug, locator, selected_kind_by_locator[locator],
            source_finding="repo_sub_resource_survey",
            detail=findings_by_path.get(locator),
        )
        cataloged.append(locator)

    published: dict[str, str] = {}
    if body.publish_to_egeria:
        if not project.egeria_asset_guid:
            raise HTTPException(
                status_code=409,
                detail="Repo has no Egeria asset yet — publish the repo itself first before publishing sub-resources.",
            )
        publisher = EgeriaPublisher(registry=registry)
        try:
            published = await asyncio.to_thread(
                publisher.publish_sub_resources,
                slug, project.github_url, project.egeria_asset_guid, cataloged,
            )
        except EgeriaConnectionError as exc:
            raise HTTPException(status_code=503, detail=str(exc))

    return SubResourceCatalogResult(cataloged=cataloged, published=published)


@router.delete("/{slug}/sub-resources")
async def uncatalog_sub_resource(slug: str, locator: str = "") -> dict:
    """Reversible (D4) — removes RE's local tracking record only; does not
    touch anything already published to Egeria. locator is a query param
    (not a path segment) so the root locator ("") is representable."""
    from resource_explorer.registry import ProjectRegistry
    registry = ProjectRegistry()
    if not registry.get(slug):
        raise HTTPException(status_code=404, detail=f"Repository '{slug}' not found")
    registry.uncatalog_sub_resource("repo", slug, locator)
    return {"slug": slug, "locator": locator, "uncataloged": True}


# ── scoped analysis — the "Narrow" stage of the repo scope-narrowing funnel
# (docs/repo-scope-narrowing-funnel.md, D5/D6). Runs a corpus-shaped analysis
# against one cataloged sub-resource instead of the whole repo; gated by
# is_shape_compatible() so a whole_resource_only (or shape-mismatched)
# analysis can never be requested scoped. ──────────────────────────────────

class ScopedAnalysisRequest(BaseModel):
    locator: str  # must already be tracked via /sub-resources/catalog


@router.post("/{slug}/sub-resources/analyses/{analysis_id}/run", response_model=AnalysisRunResult)
async def run_scoped_analysis(slug: str, analysis_id: str, body: ScopedAnalysisRequest) -> AnalysisRunResult:
    """Runs one analysis's step(s) scoped to a single cataloged sub-resource
    (SurveyOrchestrator.run(steps=..., scope_locator=...)) rather than the
    whole repo. Only reachable for analyses whose target_shape is compatible
    with the sub-resource's kind (D6) — mirrors run_single_analysis above,
    plus the scope gate and scope_locator passthrough."""
    from resource_explorer.registry import ProjectRegistry
    from resource_explorer.surveyors.analysis_catalog_reader import get_analyses, is_shape_compatible
    from resource_explorer.surveyors.repo_survey_definition_adapter import (
        REPO_ANALYSIS_SOURCE_STEPS,
    )
    from resource_explorer.surveyors.survey_orchestrator import SurveyOrchestrator

    registry = ProjectRegistry()
    project = registry.get(slug)
    if not project:
        raise HTTPException(status_code=404, detail=f"Project '{slug}' not found")

    sub_resource = next(
        (r for r in registry.list_sub_resources("repo", slug) if r["locator"] == body.locator), None,
    )
    if not sub_resource:
        raise HTTPException(
            status_code=404,
            detail=f"'{body.locator}' is not a cataloged sub-resource of '{slug}' — "
                   "catalog it first via POST /sub-resources/catalog.",
        )

    catalog_entry = next(
        (a for a in get_analyses("repo", include_egeria_live=False) if a["id"] == analysis_id), None,
    )
    if not catalog_entry:
        raise HTTPException(status_code=400, detail=f"Unknown analysis '{analysis_id}'")

    target_shape = catalog_entry.get("target_shape", "whole_resource_only")
    if not is_shape_compatible(target_shape, sub_resource["kind"]):
        raise HTTPException(
            status_code=400,
            detail=f"Analysis '{analysis_id}' (target_shape={target_shape}) cannot be scoped "
                   f"to a '{sub_resource['kind']}' sub-resource.",
        )

    # SOURCE steps: an analysis that owns none still runs its source's, and
    # refusing here would make architecture_diagram's Run button a 400.
    steps = REPO_ANALYSIS_SOURCE_STEPS.get(analysis_id)
    if not steps:
        raise HTTPException(
            status_code=400,
            detail=f"Analysis '{analysis_id}' has no mapped survey step(s).",
        )

    def _run():
        return SurveyOrchestrator(registry).run(slug, steps=steps, scope_locator=body.locator)

    try:
        result = await asyncio.to_thread(_run)
    except Exception as exc:
        return AnalysisRunResult(status="error", slug=slug, analysis_id=analysis_id, error=str(exc))

    if result.errors:
        return AnalysisRunResult(
            status="error", slug=slug, analysis_id=analysis_id, error="; ".join(result.errors),
        )
    return AnalysisRunResult(
        status="ok", slug=slug, analysis_id=analysis_id,
        message=f"{len(result.annotations)} annotation(s), scoped to '{body.locator}'.",
    )


@router.get("/{slug}/sub-resources/analyses/{analysis_id}/results")
async def get_scoped_analysis_results(slug: str, analysis_id: str, locator: str) -> dict:
    """Latest structured results for one analysis, scoped to a single
    cataloged sub-resource — reads the generic project_analysis_metrics
    table filtered by scope_locator. Only meaningful for the corpus-shaped
    kinds that persist scoped metrics today (api_structure, data_file_
    profiling — see repo_survey_definition_adapter.STEP_REGISTRY's
    accepts_scope_locator flag); other analysis_ids return an empty dict
    since nothing was ever persisted under a non-empty scope_locator for
    them (they're whole_resource_only and D6-gated out of this route by
    run_scoped_analysis above)."""
    from resource_explorer.registry import ProjectRegistry

    registry = ProjectRegistry()
    project = registry.get(slug)
    if not project:
        raise HTTPException(status_code=404, detail=f"Project '{slug}' not found")

    # analysis_id (analysis_catalog.yaml) -> project_analysis_metrics `kind`
    # discriminator — same mapping used to write these rows in the
    # surveyors themselves (upsert_metric(slug, kind, ...)).
    metrics_kind_by_analysis_id = {
        "api_structure": "api_structure",
        "data_file_profiling": "data_profile",
    }
    kind = metrics_kind_by_analysis_id.get(analysis_id)
    if not kind:
        return {}
    return registry.query_metrics(slug, kind, scope_locator=locator)


@router.get("/{slug}/analyses/{analysis_id}/measurements")
async def get_analysis_measurements(slug: str, analysis_id: str,
                                     entity_type: str = "repo",
                                     level: str = "resource") -> dict:
    """The numbers behind one analysis's answer — see
    resource_explorer/workflows/stage_page.py::build_measurements and the
    designer's round (STAGE-PAGE-ROUND.md point 10, "the fact opens under
    the answer"). Thin wrapper; the route only translates the pure
    function's LookupError into a 404.

    `entity_type` defaults to "repo" (same query-param convention as
    `answer_question()` in `routes/analyses.py`) — trusted as given, same as
    that route and `compile_endpoint()` in `routes/compile_context.py`. This
    used to be unaccepted here, so "the numbers behind this" always 404'd
    for a database/filesystem slug: `build_measurements()` always did a
    repo-only `registry.get()` lookup and always checked the analysis id
    against repo's own `ANALYSIS_KINDS`, regardless of what kind of resource
    the caller actually asked about (see that function's docstring, fixed
    2026-09-23 — the fourth instance of PR #226/#233/#236's "resource-type
    never threaded through" bug class).

    `level` (Slice 21a point 4) defaults to "resource" — every pre-existing
    caller. app.js passes the asking question's own primary level; see
    `build_measurements()`'s own docstring for what changes when it names a
    sub-resource level."""
    from resource_explorer.registry import ProjectRegistry
    from resource_explorer.workflows.stage_page import build_measurements

    registry = ProjectRegistry()
    try:
        return await asyncio.to_thread(
            build_measurements, registry, slug, analysis_id, entity_type, level)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.get("/{slug}/analyses-index")
async def get_analyses_index(slug: str, entity_type: str = "repo") -> dict:
    """Every catalog analysis for this resource, with the questions that
    name it, last run, price, and what it serves — see
    resource_explorer/workflows/stage_page.py::build_analyses_index and the
    designer's round (STAGE-PAGE-ROUND.md points 1-3, "AnalysesIndex").
    Thin wrapper; the route only translates the pure function's LookupError
    into a 404.

    `entity_type` defaults to "repo", same convention and same fix date as
    `get_analysis_measurements` above — `build_analyses_index()` carried the
    identical repo-only bug (its own docstring has the detail)."""
    from resource_explorer.registry import ProjectRegistry
    from resource_explorer.workflows.stage_page import build_analyses_index

    registry = ProjectRegistry()
    try:
        return await asyncio.to_thread(build_analyses_index, registry, slug, entity_type)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.get("/{slug}/members/{analysis_id}")
async def get_members(slug: str, analysis_id: str, metric: str = "", scope: str = "public",
                      limit: int = 200) -> dict:
    """The things a count counted — see resource_explorer/members.py.

    `scope` is `public` or `all`; the response says whether it was honoured,
    because today only symbols carry a public/internal marker. Read from
    the registry, not the display-shaped results, since those drop the
    detail this needs.
    """
    from resource_explorer.members import members_for
    from resource_explorer.registry import ProjectRegistry

    registry = ProjectRegistry()
    if not registry.get(slug):
        raise HTTPException(status_code=404, detail=f"Project '{slug}' not found")
    return await asyncio.to_thread(
        lambda: members_for(registry, slug, analysis_id, metric, scope=scope, limit=min(max(limit, 1), 1000)).to_dict())


@router.get("/{slug}/members/{analysis_id}/children")
async def get_member_children(slug: str, analysis_id: str, key: str, scope: str = "public",
                              limit: int = 200) -> dict:
    """One level down a member tree. `key` is opaque — whatever the parent
    row's `children_key` said."""
    from resource_explorer.members import children_for
    from resource_explorer.registry import ProjectRegistry

    registry = ProjectRegistry()
    if not registry.get(slug):
        raise HTTPException(status_code=404, detail=f"Project '{slug}' not found")
    rows = await asyncio.to_thread(lambda: children_for(registry, slug, analysis_id, key, scope=scope, limit=min(max(limit, 1), 1000)))
    return {"key": key, "members": rows}


class PromoteSelection(BaseModel):
    """A selection from a member list, with its provenance. `members` are the
    names as they were when selected — a snapshot, never a query."""
    action: str                      # work_list | rfa | journal
    metric: str = ""
    members: list[str] = Field(default_factory=list)
    total: int = 0
    facet: str = ""
    run_at: str = ""
    name: str = ""                   # the work item's name; proposed by the client, editable
    suggest_to: list[str] = Field(default_factory=list)   # journal only


@router.post("/{slug}/members/{analysis_id}/promote")
def promote_members(slug: str, analysis_id: str, body: PromoteSelection, request: Request) -> dict:
    """Promote a member-list selection: to a work list (I will deal with
    this), an RFA (someone must), or the journal (worth knowing). One
    provenance line, composed here, travels with all three. See the
    promotion note in resource_explorer/members.py.

    Signed-in only: a work item, a request for action and a journal entry
    all need someone to have made them.
    """
    from resource_explorer.activity_logger import log_rfa
    from resource_explorer.auth import get_current_user
    from resource_explorer.journal import Journal
    from resource_explorer.members import proposed_name, provenance_line
    from resource_explorer.registry import ProjectRegistry
    from resource_explorer.work_lists import WorkLists

    user = get_current_user(request)
    author = (user or {}).get("user_id") or (user or {}).get("sub") or (user or {}).get("username") or ""
    if not author:
        raise HTTPException(status_code=401, detail="Sign in to promote a selection — it needs someone to have made it.")
    if body.action not in ("work_list", "rfa", "journal"):
        raise HTTPException(status_code=422, detail="action must be work_list, rfa or journal")
    if not body.members:
        raise HTTPException(status_code=422, detail="nothing is selected")

    registry = ProjectRegistry()
    project = registry.get(slug)
    if not project:
        raise HTTPException(status_code=404, detail=f"Project '{slug}' not found")

    # The date is the server's: what the registry says this analysis last ran,
    # not what the browser sent (which was never populated off one path).
    from resource_explorer.members import last_run_at
    run_at = last_run_at(registry, slug, analysis_id) or body.run_at
    if body.suggest_to:
        # An audience is a perspective or a person; a free string minted a
        # work list named suggested-to-<anything> for any signed-in caller.
        from resource_explorer.surveyors.analysis_catalog_reader import EGERIA_PERSPECTIVES
        bad = [t for t in body.suggest_to if t not in EGERIA_PERSPECTIVES and not re.fullmatch(r"[A-Za-z0-9_.@-]{1,64}", t)]
        if bad:
            raise HTTPException(status_code=400, detail=f"suggest_to must name a perspective or a user id: {bad}")
    line = provenance_line(analysis_id=analysis_id, run_at=run_at, total=body.total,
                           members=body.members, facet=body.facet, metric=body.metric)
    name = body.name.strip() or proposed_name(project.display_name or slug, total=body.total,
                                              members=body.members, facet=body.facet, metric=body.metric)

    if body.action == "work_list":
        wl = WorkLists(registry).create(name, [slug], entity_type="repo", created_by=author,
                                        derived_from=f"members:{analysis_id}", rationale=line,
                                        description=f"Promoted from the {analysis_id} member list.")
        return {"action": "work_list", "name": name, "provenance": line, "work_list": wl.get("slug") if wl else None}

    if body.action == "rfa":
        rfa_id = log_rfa(registry, "repo", slug, project.display_name or slug, "open", name, detail=line,
                         analysis_name=analysis_id,
                         items=[{"kind": "member", "analysis_id": analysis_id, "name": m} for m in body.members[:50]])
        return {"action": "rfa", "name": name, "provenance": line, "rfa": rfa_id}

    entry = Journal(registry).write("repo", slug, author=author, body=f"{name} · {line}", suggest_to=body.suggest_to)
    return {"action": "journal", "name": name, "provenance": line, "journal": entry.get("id"),
            "work_lists": entry.get("work_lists", [])}


# ── Curate: review-and-commit ───────────────────────────────────────────
#
# One screen, three columns, one commit. The plan is a local read (facts,
# findings, enrichment, verdicts, disposition) so it renders when Egeria is
# down; the commit is a queued run whose steps write their outcomes to the
# curation record as they land, because a handoff is asynchronous and can
# fail elsewhere. See curate_plan.py for the design rules held.

class CurateSelection(BaseModel):
    confirm: list[str] = Field(default_factory=list)          # kinds from what_it_is: SoftwareCapability::<name>, Endpoint, ...
    sub_resources: list[str] = Field(default_factory=list)    # locators from the sub-resource survey
    data_files: bool = False                                   # contained datasets -- recorded in the manifest; publish path not built
    note: str = ""


@router.get("/{slug}/curate/plan")
def curate_plan(slug: str) -> dict:
    from resource_explorer.curate_plan import build_plan
    from resource_explorer.registry import ProjectRegistry
    try:
        return build_plan(ProjectRegistry(), slug)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Project '{slug}' not found")


@router.post("/{slug}/curate/commit")
def curate_commit(slug: str, body: CurateSelection, request: Request) -> dict:
    """Catalogue →. Records the act (who, when, what was selected, what the
    manifest said), logs an activity entry the pane polls, and enqueues the
    run. Signed-in only: the record needs an author, and Ownership on the
    asset is curation by default."""
    from resource_explorer.activity_logger import log_survey
    from resource_explorer.auth import get_current_user
    from resource_explorer.curate_plan import CURATE_POPULATION, Curations, build_plan
    from resource_explorer.registry import ProjectRegistry
    from resource_explorer.workflows.curate_commit import STEPS

    user = get_current_user(request)
    author = (user or {}).get("user_id") or (user or {}).get("sub") or (user or {}).get("username") or ""
    if not author:
        raise HTTPException(status_code=401, detail="Sign in to catalogue — the record needs an author, and the asset an owner.")
    registry = ProjectRegistry()
    project = registry.get(slug)
    if not project:
        raise HTTPException(status_code=404, detail=f"Project '{slug}' not found")
    plan = build_plan(registry, slug)
    if not plan["in_population"]:
        raise HTTPException(status_code=409, detail=(
            f"Only worthy things get curated: Curate's population is disposition {' or '.join(CURATE_POPULATION)}, "
            f"and this resource is '{plan['disposition']}'. Set its disposition first."))
    known = {r["kind"] for r in plan["what_it_is"] if r["candidate"]}
    unknown = [k for k in body.confirm if k not in known]
    if unknown:
        raise HTTPException(status_code=400, detail=f"Not candidates on this resource: {unknown}")
    manifest = {**plan["writes"], "entities": list(body.confirm),
                "contained": {"data_files": plan["writes"]["contained"]["data_files"] if body.data_files else 0,
                              "sub_resources": len(body.sub_resources)}}
    activity_id = log_survey(
        registry, entity_type="repo", entity_slug=slug,
        entity_name=project.display_name, entity_location=project.github_url,
        intent="curate", status="running",
        summary=f"Cataloguing {project.display_name}: {len(body.confirm)} entities, {len(body.sub_resources)} sub-resources…",
    )
    rec = Curations(registry).create(
        "repo", slug, author=author, selection=body.model_dump(), manifest=manifest,
        steps=list(STEPS), activity_id=activity_id)
    run_id = registry.enqueue_run("curate_commit", {"slug": slug, "curation_id": rec["id"]},
                                  result_ref=activity_id, requested_by=_requested_by())
    log.info("enqueued curate_commit %s for %s (activity %s)", run_id, slug, activity_id)
    return {"curation": rec, "activity_id": activity_id, "run_id": run_id}


@router.get("/{slug}/curate/commits/{curation_id}")
def curate_commit_status(slug: str, curation_id: str) -> dict:
    from resource_explorer.curate_plan import Curations
    from resource_explorer.registry import ProjectRegistry
    rec = Curations(ProjectRegistry()).get(curation_id)
    if not rec or rec["entity_slug"] != slug:
        raise HTTPException(status_code=404, detail="No such curation")
    return rec


# ── Records: the report record beside the catalogue record ───────────────
#
# REPORT-RECORD-AND-TWO-CALLS C1-C4. One table, two kinds; a report is the
# act of writing a list down. The server re-reads the members payload at
# save time, so what is stored is a snapshot of names as they were -- never
# the client's copy and never a query.

class SaveReport(BaseModel):
    question: str = ""                 # asked-as
    metric: str = ""
    members: list[str] | None = None   # None = the whole list
    facet: str = ""
    name: str = ""                     # typed name wins; else proposed server-side
    scope: str = "all"
    corrects: str = ""                 # a correction: the superseded record's id


@router.post("/{slug}/members/{analysis_id}/report")
def save_report(slug: str, analysis_id: str, body: SaveReport, request: Request) -> dict:
    from resource_explorer.auth import get_current_user
    from resource_explorer.curate_plan import Curations
    from resource_explorer.members import last_run_at, members_for
    from resource_explorer.registry import ProjectRegistry
    from resource_explorer.reports import build_report, default_name

    user = get_current_user(request)
    author = (user or {}).get("user_id") or (user or {}).get("sub") or (user or {}).get("username") or ""
    if not author:
        raise HTTPException(status_code=401, detail="Sign in to save a report — a record needs an author.")
    registry = ProjectRegistry()
    project = registry.get(slug)
    if not project:
        raise HTTPException(status_code=404, detail=f"Project '{slug}' not found")
    payload = members_for(registry, slug, analysis_id, metric=body.metric, scope=body.scope, limit=1000).to_dict()
    if payload.get("not_applicable"):
        # A "0 findings" report of a metrics-only analysis is exactly the
        # false record REPORT-ACTS says must never be made — refuse rather
        # than writing a report of a list that was never a list.
        raise HTTPException(status_code=422, detail=payload.get("reason") or
                            "This analysis records measurements, not members — there is nothing to report.")
    run_at = last_run_at(registry, slug, analysis_id)
    report = build_report(question=body.question, slug=slug, display_name=project.display_name or slug,
                          analysis_id=analysis_id, metric=body.metric or payload.get("metric", ""), run_at=run_at,
                          facet=body.facet, members_payload=payload, selected=body.members)
    if not report["shown"]:
        raise HTTPException(status_code=400, detail="Nothing to record — the selection matched no members.")
    from datetime import datetime, timezone
    names = [r["name"] for g in report["groups"] for r in g["rows"]]
    name = body.name.strip() or default_name(project.display_name or slug, total=report["total"], names=names,
                                              facet=body.facet, metric=report["metric"],
                                              written_on=datetime.now(timezone.utc).isoformat())
    if body.corrects:
        from resource_explorer.reports import correction_clause
        report["corrects"] = body.corrects
        # The header names what the correction is not carrying, when the
        # corrected record was a selection. Nothing when the populations match.
        report["header"] += correction_clause(Curations(registry).get(body.corrects))
    try:
        rec = Curations(registry).create_report("repo", slug, author=author, name=name, report=report, corrects=body.corrects)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"record": rec}


class RecordAct(BaseModel):
    action: str                        # work_list | rfa | journal
    rows: list[str] | None = None      # None = the whole report; a subset of the frozen snapshot otherwise
    name: str = ""                     # the work list's / RFA's name; defaults to the record's
    suggest_to: list[str] = Field(default_factory=list)
    journal_id: str = ""               # journal: the entry the client wrote, so its use is recorded


@router.post("/{slug}/records/{record_id}/act")
def act_on_record(slug: str, record_id: str, body: RecordAct, request: Request) -> dict:
    """The three acts on a report (REPORT-ACTS, 2026-09-14). A report's rows
    are frozen, so an act on it is an act on what WAS true: the server acts
    on the stored snapshot, never re-derives the list, and what it creates
    points at the record -- the provenance line plus 'as recorded in "…"',
    with the staleness carried in when the evidence has moved. The record
    learns it was used; its content is never touched."""
    from resource_explorer.activity_logger import log_rfa
    from resource_explorer.auth import get_current_user
    from resource_explorer.curate_plan import Curations
    from resource_explorer.members import last_run_at
    from resource_explorer.registry import ProjectRegistry
    from resource_explorer.reports import act_line, out_of_date
    from resource_explorer.work_lists import WorkLists

    user = get_current_user(request)
    author = (user or {}).get("user_id") or (user or {}).get("sub") or (user or {}).get("username") or ""
    if not author:
        raise HTTPException(status_code=401, detail="Sign in to act on a report — a work item needs someone who raised it.")
    if body.action not in ("work_list", "rfa", "journal"):
        raise HTTPException(status_code=400, detail="action must be work_list, rfa or journal")
    registry = ProjectRegistry()
    project = registry.get(slug)
    cur = Curations(registry)
    rec = cur.get(record_id)
    if not project or not rec or rec["entity_slug"] != slug or rec.get("kind") != "report":
        raise HTTPException(status_code=404, detail="No such report record")
    rep = rec.get("report") or {}
    stale = out_of_date(rep, last_run_at(registry, slug, rep.get("analysis_id", "")))
    line = act_line(rec, rows=body.rows, out_of_date_sentence=stale)
    name = body.name.strip() or rec["name"]
    if body.action == "work_list":
        wl = WorkLists(registry).create(name, [slug], entity_type="repo", created_by=author,
                                        derived_from=f"record:{record_id}", rationale=line,
                                        description=f'Raised from the report "{rec["name"]}".')
        listed = WorkLists(registry).get(wl.get("slug")) if wl else None
        target_name = (listed or {}).get("display_name") or name
        out = cur.add_use(record_id, act="work_list", target=wl.get("slug") if wl else "", target_name=target_name, by=author)
        return {"action": "work_list", "name": target_name, "work_list": wl.get("slug") if wl else None,
                "provenance": line, "record": out}
    if body.action == "rfa":
        # The RFA points at the record, so whoever receives it opens exactly
        # what the raiser was looking at. It never re-queries.
        rfa_id = log_rfa(registry, "repo", slug, project.display_name or slug, "open", name, detail=line,
                         analysis_name=rep.get("analysis_id", ""),
                         items=[{"kind": "record", "record_id": record_id, "name": rec["name"]}])
        out = cur.add_use(record_id, act="rfa", target=str(rfa_id), target_name=name, by=author)
        return {"action": "rfa", "name": name, "rfa": rfa_id, "provenance": line, "record": out}
    # journal: the entry was written by the client from a citation seed; only
    # the use is recorded here. Nothing canned is written on the person's behalf.
    out = cur.add_use(record_id, act="journal", target=body.journal_id, target_name="the journal", by=author)
    return {"action": "journal", "provenance": line, "record": out}


@router.get("/{slug}/records")
def list_records(slug: str) -> dict:
    """Both kinds, newest first, each report carrying its out-of-date
    sentence when its analysis has re-run since."""
    from resource_explorer.curate_plan import Curations
    from resource_explorer.members import last_run_at
    from resource_explorer.registry import ProjectRegistry
    from resource_explorer.reports import out_of_date

    registry = ProjectRegistry()
    if not registry.get(slug):
        raise HTTPException(status_code=404, detail=f"Project '{slug}' not found")
    out = []
    for rec in Curations(registry).for_resource("repo", slug):
        if rec.get("kind") == "report":
            rec["out_of_date"] = out_of_date(rec.get("report") or {}, last_run_at(registry, slug, (rec.get("report") or {}).get("analysis_id", "")))
        out.append(rec)
    return {"records": out}


def _entity_display_name(registry, entity_type: str, slug: str) -> str | None:
    """None if no such entity exists — used as the existence check by the
    entity-generic sibling routes below, the same role `registry.get(slug)`
    plays for the repo-only routes above."""
    if entity_type == "database":
        d = registry.get_database(slug)
        return d.display_name if d else None
    if entity_type == "filesystem":
        f = registry.get_filesystem(slug)
        return f.display_name if f else None
    p = registry.get(slug)
    return p.display_name if p else None


@router.get("/entity/{entity_type}/{slug}/records")
def list_entity_records(entity_type: str, slug: str) -> dict:
    """The database/filesystem sibling of `GET /{slug}/records` above —
    added when Disposition generalized to database/filesystem entities
    (Backlog.md, "Disposition is NOT fixed here", 2026-09-22).
    `Curations.for_resource` was already entity-type-generic; only this
    route (Project-only existence check) and `act_on_entity_record` below
    (hardcoded entity_type='repo') were not. `GET .../records/{record_id}`
    needed no sibling — it never checked entity_type at all."""
    from resource_explorer.curate_plan import Curations
    from resource_explorer.members import last_run_at
    from resource_explorer.registry import ProjectRegistry
    from resource_explorer.reports import out_of_date

    registry = ProjectRegistry()
    if _entity_display_name(registry, entity_type, slug) is None:
        raise HTTPException(status_code=404, detail=f"{entity_type} '{slug}' not found")
    out = []
    for rec in Curations(registry).for_resource(entity_type, slug):
        if rec.get("kind") == "report":
            rec["out_of_date"] = out_of_date(
                rec.get("report") or {}, last_run_at(registry, slug, (rec.get("report") or {}).get("analysis_id", "")),
            )
        out.append(rec)
    return {"records": out}


@router.post("/entity/{entity_type}/{slug}/records/{record_id}/act")
def act_on_entity_record(entity_type: str, slug: str, record_id: str, body: RecordAct, request: Request) -> dict:
    """The database/filesystem sibling of `POST /{slug}/records/{record_id}/act`
    above — same three acts, generalized the same way `list_entity_records`
    is: an entity-type-aware existence check plus threading `entity_type`
    through to `WorkLists`/`log_rfa` instead of the hardcoded 'repo'."""
    from resource_explorer.activity_logger import log_rfa
    from resource_explorer.auth import get_current_user
    from resource_explorer.curate_plan import Curations
    from resource_explorer.members import last_run_at
    from resource_explorer.registry import ProjectRegistry
    from resource_explorer.reports import act_line, out_of_date
    from resource_explorer.work_lists import WorkLists

    user = get_current_user(request)
    author = (user or {}).get("user_id") or (user or {}).get("sub") or (user or {}).get("username") or ""
    if not author:
        raise HTTPException(status_code=401, detail="Sign in to act on a report — a work item needs someone who raised it.")
    if body.action not in ("work_list", "rfa", "journal"):
        raise HTTPException(status_code=400, detail="action must be work_list, rfa or journal")
    registry = ProjectRegistry()
    display_name = _entity_display_name(registry, entity_type, slug)
    cur = Curations(registry)
    rec = cur.get(record_id)
    if display_name is None or not rec or rec["entity_slug"] != slug or rec.get("kind") != "report":
        raise HTTPException(status_code=404, detail="No such report record")
    rep = rec.get("report") or {}
    stale = out_of_date(rep, last_run_at(registry, slug, rep.get("analysis_id", "")))
    line = act_line(rec, rows=body.rows, out_of_date_sentence=stale)
    name = body.name.strip() or rec["name"]
    if body.action == "work_list":
        wl = WorkLists(registry).create(name, [slug], entity_type=entity_type, created_by=author,
                                        derived_from=f"record:{record_id}", rationale=line,
                                        description=f'Raised from the report "{rec["name"]}".')
        listed = WorkLists(registry).get(wl.get("slug")) if wl else None
        target_name = (listed or {}).get("display_name") or name
        out = cur.add_use(record_id, act="work_list", target=wl.get("slug") if wl else "", target_name=target_name, by=author)
        return {"action": "work_list", "name": target_name, "work_list": wl.get("slug") if wl else None,
                "provenance": line, "record": out}
    if body.action == "rfa":
        rfa_id = log_rfa(registry, entity_type, slug, display_name or slug, "open", name, detail=line,
                         analysis_name=rep.get("analysis_id", ""),
                         items=[{"kind": "record", "record_id": record_id, "name": rec["name"]}])
        out = cur.add_use(record_id, act="rfa", target=str(rfa_id), target_name=name, by=author)
        return {"action": "rfa", "name": name, "rfa": rfa_id, "provenance": line, "record": out}
    out = cur.add_use(record_id, act="journal", target=body.journal_id, target_name="the journal", by=author)
    return {"action": "journal", "provenance": line, "record": out}


@router.get("/{slug}/records/{record_id}")
def get_record(slug: str, record_id: str, fmt: str = "") -> object:
    """The record, or an export of it: `?fmt=md` / `?fmt=csv` carry the same
    header sentence and provenance line. The record is the thing."""
    from fastapi.responses import PlainTextResponse
    from resource_explorer.curate_plan import Curations
    from resource_explorer.members import last_run_at
    from resource_explorer.registry import ProjectRegistry
    from resource_explorer.reports import out_of_date, to_csv, to_markdown

    registry = ProjectRegistry()
    rec = Curations(registry).get(record_id)
    if not rec or rec["entity_slug"] != slug:
        raise HTTPException(status_code=404, detail="No such record")
    if rec.get("kind") == "report":
        rec["out_of_date"] = out_of_date(rec.get("report") or {}, last_run_at(registry, slug, (rec.get("report") or {}).get("analysis_id", "")))
    if fmt == "md":
        return PlainTextResponse(to_markdown(rec), media_type="text/markdown",
                                 headers={"Content-Disposition": f'attachment; filename="{record_id[:8]}.md"'})
    if fmt == "csv":
        return PlainTextResponse(to_csv(rec), media_type="text/csv",
                                 headers={"Content-Disposition": f'attachment; filename="{record_id[:8]}.csv"'})
    return rec


# ── Component review at the branch ──────────────────────────────────────
#
# The designer's ports round (2026-09-14). Rows are branches of the path the
# components are keyed by; a branch verdict is one row at the branch's
# scope and the reader resolves the longest prefix; ports are a column on
# the component, read from the deployment artifacts, with no verdict of
# their own. See component_tree.py.

@router.get("/{slug}/components/tree")
def components_tree(slug: str, prefix: str = "") -> dict:
    from resource_explorer.component_tree import component_tree
    from resource_explorer.registry import ProjectRegistry
    registry = ProjectRegistry()
    if not registry.get(slug):
        raise HTTPException(status_code=404, detail=f"Project '{slug}' not found")
    return component_tree(registry, slug, prefix)


@router.get("/{slug}/components/leaves")
def components_leaves(slug: str, branch: str) -> dict:
    from resource_explorer.component_tree import group_leaves, leaves
    from resource_explorer.registry import ProjectRegistry
    registry = ProjectRegistry()
    if not registry.get(slug):
        raise HTTPException(status_code=404, detail=f"Project '{slug}' not found")
    rows = leaves(registry, slug, branch)
    # `groups`/`ungrouped` (2026-09-17): the same flat rows, re-shaped by
    # scope-hierarchy cluster (component_tree.group_leaves) so a curator
    # opening a large branch sees ~10-row groups instead of one long list.
    # `leaves` stays flat and unchanged for the one other caller that reads
    # this function directly (branch_verdicts' materialization filter).
    groups, ungrouped = group_leaves(rows)
    return {"branch": branch, "leaves": rows, "groups": groups, "ungrouped": ungrouped}


class BranchVerdicts(BaseModel):
    scope_locators: list[str]          # branch paths and/or component paths
    verdict: str                       # accepted | rejected
    note: str = ""


@router.post("/{slug}/components/verdicts")
def branch_verdicts(slug: str, body: BranchVerdicts, request: Request) -> dict:
    """Accept or reject at the branch. One verdict row per scope given -- a
    branch path is a scope like any other, and every component under it
    inherits until its own row wins. Materialization of accepted components
    into Egeria is queued (kind materialize_components) so the pane returns
    at once; nothing runs until the caller confirmed the preview. No undo,
    and the word is not offered: a change is a new row and the trail keeps
    both."""
    from resource_explorer.activity_logger import log_survey
    from resource_explorer.auth import get_current_user
    from resource_explorer.registry import ProjectRegistry
    from resource_explorer.web.routes.curate import _authorize_curation

    user = get_current_user(request)
    author = (user or {}).get("user_id") or (user or {}).get("sub") or (user or {}).get("username") or ""
    if not author:
        raise HTTPException(status_code=401, detail="Sign in to record a verdict — it needs someone who made it.")
    if body.verdict not in ("accepted", "rejected"):
        raise HTTPException(status_code=400, detail="verdict must be accepted or rejected")
    scopes = [s.strip().rstrip("/") for s in body.scope_locators if s and s.strip().rstrip("/")]
    if not scopes:
        raise HTTPException(status_code=400, detail="no scope given")
    registry = ProjectRegistry()
    project = registry.get(slug)
    if not project:
        raise HTTPException(status_code=404, detail=f"Project '{slug}' not found")
    rows = []
    for scope in scopes:
        _authorize_curation(registry, "repo", slug, scope)
        rows.append(registry.record_component_verdict("repo", slug, scope, body.verdict, "", body.note, decided_by=author))
    out = {"verdicts": rows, "run_id": None, "activity_id": None}
    if body.verdict == "accepted":
        from resource_explorer.component_tree import leaves
        accepted_paths = sorted({l["path"] for scope in scopes for l in leaves(registry, slug, scope)
                                 if (l.get("verdict") or {}).get("verdict") == "accepted"})
        activity_id = log_survey(
            registry, entity_type="repo", entity_slug=slug,
            entity_name=project.display_name, entity_location=project.github_url,
            intent="curate", status="running",
            summary=f"Materialising {len(accepted_paths)} accepted component(s) of {project.display_name}…")
        run_id = registry.enqueue_run("materialize_components", {"slug": slug, "paths": accepted_paths},
                                      result_ref=activity_id, requested_by=_requested_by())
        out.update({"run_id": run_id, "activity_id": activity_id, "queued": len(accepted_paths)})
    return out


# ── Blueprints (SPEC-CURATE-SELECTION-AND-BLUEPRINTS.md §2) ─────────────────
#
# "A sibling reader, not a schema change" — verdict_target='blueprint' rows
# already live in architecture_component_verdicts (registry.py), materialized
# blueprints already have their own table, and _candidate_blueprints_results
# already resolves a cluster's own verdict/materialization AND each member/
# child's, keyed the same way curate.py's blueprint-verdict routes read and
# write (f"{perspective}::{cluster_name}"). This route only exposes that
# existing read, the way /components/tree exposes component_tree() — no new
# table, no new join.
@router.get("/{slug}/components/blueprints")
def components_blueprints(slug: str) -> dict:
    from resource_explorer.registry import ProjectRegistry
    from resource_explorer.surveyors.repo_survey_definition_adapter import (
        _candidate_blueprints_results,
    )
    registry = ProjectRegistry()
    if not registry.get(slug):
        raise HTTPException(status_code=404, detail=f"Project '{slug}' not found")
    blueprints = _candidate_blueprints_results(registry, slug)
    # RULING-WHAT-A-VERDICT-IS-ABOUT.md §0/§2d: a cluster only exists WITHIN
    # one Component.perspective (reading) — the perspectives present here are
    # the readings a curator can switch between, distinct from the diagram's
    # run_label preference and the chrome's unrelated Perspective filter.
    perspectives = sorted({b["perspective"] for b in blueprints if b.get("perspective")})
    return {"blueprints": blueprints, "perspectives": perspectives}


@router.get("/{slug}/gaps")
def get_gaps(slug: str) -> dict:
    """The gaps collection this project owns (SPEC-ACTIONABLE-AND-HONEST.md
    §3, resource_explorer/gaps.py): findings about the ANALYSIS rather than
    the repository — a disagreement between two measures, or a check that
    could not be established — collapsed on the resource page to one line
    ("3 of 11 community measures cannot be computed … Not a finding about
    this repository") and expanded here.

    Reads what FactLayer.facts() already recorded (that call is the one
    choke point every page load already goes through — see gaps.py's own
    docstring) rather than re-collecting, so this route is a plain read like
    every other GET here."""
    from resource_explorer.gaps import gaps_summary
    from resource_explorer.registry import ProjectRegistry

    registry = ProjectRegistry()
    if not registry.get(slug):
        raise HTTPException(status_code=404, detail=f"Project '{slug}' not found")
    return gaps_summary(registry, slug)


@router.post("/{slug}/gaps/{gap_id}/rfa")
def raise_gap_rfa(slug: str, gap_id: int, request: Request) -> dict:
    """Raise an RFA for one gap.

    The designer's model (§3) is an RFA *against the analysis* — the gap is
    not the reader's problem, it is whoever maintains the analysis's. This
    codebase's RFA entity types today are exactly {repo, database,
    filesystem, investigation} (activity_logger.log_rfa's real callers,
    grepped 2026-09-14) — there is no `analysis` entity type, so "against the
    analysis" is not representable yet. Raising it against the repo with the
    analysis named in the summary/detail is the honest fallback, not a
    silent downgrade: it is disclosed here, in the docstring, and again in
    this change's PR body, rather than pretending the RFA landed where the
    design says it should.

    404 when the project or the gap does not exist (or the gap belongs to a
    different project — a slug/gap_id mismatch is a caller error, not "not
    found" for a DIFFERENT reason, but the response is the same either way).
    409 when this gap already has an RFA — same convention as
    outbox.py's retry route: raising a second RFA for a gap already worked
    would duplicate the work item, not merely re-request it."""
    from resource_explorer.activity_logger import log_rfa
    from resource_explorer.auth import get_current_user
    from resource_explorer.registry import ProjectRegistry

    registry = ProjectRegistry()
    project = registry.get(slug)
    if not project:
        raise HTTPException(status_code=404, detail=f"Project '{slug}' not found")
    gap = registry.get_gap(gap_id)
    if not gap or gap["project_slug"] != slug:
        raise HTTPException(status_code=404, detail=f"No such gap {gap_id} for project '{slug}'")
    if gap.get("rfa_activity_id"):
        raise HTTPException(
            status_code=409,
            detail=f"Gap {gap_id} already has an RFA ({gap['rfa_activity_id']}) — "
                   "raising a second one would duplicate the work item.",
        )
    user = get_current_user(request)
    author = (user or {}).get("user_id") or (user or {}).get("sub") or (user or {}).get("username") or ""
    kind_label = "a disagreement between two measures" if gap["gap_kind"] == "disagreement" \
        else "a check that could not be established"
    summary = f"{gap['analysis_id']}: {gap['sentence']}"
    detail = (
        f"This is {kind_label} in the '{gap['analysis_id']}' analysis, not a "
        f"finding about {project.display_name or slug} itself — raised "
        "against the repository because no 'analysis' RFA entity type exists "
        "yet (see this route's docstring)."
    )
    rfa_id = log_rfa(
        registry, "repo", slug, project.display_name or slug, "open", summary,
        detail=detail, analysis_name=gap["analysis_id"],
        items=[{"kind": "gap", "gap_id": gap_id, "gap_kind": gap["gap_kind"],
                "check_name": gap["check_name"]}],
    )
    registry.mark_gap_rfa(gap_id, rfa_id)
    return {"gap_id": gap_id, "rfa": rfa_id, "raised_against": "repo", "raised_by": author}
