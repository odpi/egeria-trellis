"""Filesystem management endpoints — list, get, register, survey, remove."""
from __future__ import annotations

import asyncio
import logging
from typing import Any
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from resource_explorer.registry import FileSystemEntity, ProjectRegistry, ProjectStatus

log = logging.getLogger(__name__)
router = APIRouter()


class FileSystemSummary(BaseModel):
    """Summary information for a filesystem entity."""
    slug: str
    display_name: str
    local_mount_point: str
    canonical_mount_point: str
    description: str
    status: str
    last_surveyed_at: str
    egeria_asset_guid: str
    file_count: int
    data_file_count: int
    egeria_url: str = ""
    egeria_server: str = ""
    egeria_user: str = ""
    group_slug: str = ""
    # 'undecided' when nobody has ever decided — see DatabaseSummary's own
    # comment (databases.py) for why this field exists now.
    disposition: str = "undecided"
    # egeria_asset_guid set AND the linkage is not recorded stale — see
    # `egeria_linkage.describe_publish_status`. Boolean only, not the raw
    # GUID, same convention as `ProjectSummary.is_published` (projects.py).
    # Lets /next's shared `lifecycleMark()` render the same "published to
    # Egeria" mark for a filesystem row it already renders for a repo row.
    is_published: bool = False
    # Non-empty only when a GUID IS cached but the linkage is stale — see
    # `DatabaseSummary.egeria_publish_note` (databases.py) for the full
    # rationale; the same gap existed here.
    egeria_publish_note: str = ""
    # Personal view filter, separate axis from disposition — see
    # `ProjectSummary.working_set_hidden` (projects.py) and
    # `registry.py`'s `resource_working_set` table. Needed so /next's
    # select-mode "hide" bulk action and "Show hidden" toggle work for
    # filesystems the same way they do for repos.
    working_set_hidden: bool = False


class FileSystemRegistration(BaseModel):
    """Request body for registering a new filesystem."""
    slug: str
    display_name: str
    local_mount_point: str
    canonical_mount_point: str = ""
    description: str = ""
    egeria_url: str = ""
    egeria_server: str = ""
    egeria_user: str = ""
    egeria_password: str = ""
    group_slug: str = ""


class FileSystemSurveyRequest(BaseModel):
    """Request body for running a filesystem survey."""
    mode: str = "hybrid"  # "local" | "hybrid" | "egeria"
    egeria_url: str | None = None
    egeria_server: str | None = None
    egeria_user: str | None = None
    egeria_password: str | None = None
    force_publish: bool = False


class EgeriaSurveyReportRow(BaseModel):
    """A SurveyReport as it actually exists in Egeria right now (live read via
    the real ReportSubject relationship). See databases.py's identical model
    and resource_explorer/surveyors/egeria_survey_reader.py."""
    guid: str
    qualified_name: str
    display_name: str
    surveyed_at: str
    annotation_count: int
    schema_count: int
    table_count: int
    column_count: int
    description: str


class EgeriaAnnotationItem(BaseModel):
    guid: str
    annotation_type: str
    summary: str
    confidence: int | None
    analysis_step: str
    explanation: str
    expression: str
    json_properties: dict
    #: Egeria's `contentStatus` — "DRAFT" marks an unconfirmed PROPOSAL
    #: (Phase 1 slice 10). Declared here too even though no filesystem
    #: analysis proposes anything yet: the three copies of this model are read
    #: by ONE frontend renderer, so a field present in two of them and absent
    #: from the third shows a badge on two tabs and silently not on the
    #: third — the exact failure this whole chain exists to avoid.
    content_status: str = ""


@router.get("/", response_model=list[FileSystemSummary])
def list_filesystems():
    """List all registered filesystems."""
    from resource_explorer.egeria_linkage import describe_publish_status

    registry = ProjectRegistry()
    filesystems = registry.list_filesystems()

    result = []
    for fs in filesystems:
        latest = registry.get_latest_filesystem_survey(fs.slug)
        disp = registry.get_disposition_for_entity("filesystem", fs.slug) or {}
        publish_status = describe_publish_status(
            registry, "filesystem", fs.slug, fs.egeria_asset_guid or "")
        result.append(
            FileSystemSummary(
                slug=fs.slug,
                display_name=fs.display_name,
                local_mount_point=fs.local_mount_point,
                canonical_mount_point=fs.canonical_mount_point or "",
                description=fs.description,
                status=fs.status.value,
                last_surveyed_at=fs.last_surveyed_at or "",
                egeria_asset_guid=fs.egeria_asset_guid or "",
                file_count=fs.file_count,
                data_file_count=fs.data_file_count,
                egeria_url=fs.egeria_url or "",
                egeria_server=fs.egeria_server or "",
                egeria_user=fs.egeria_user or "",
                group_slug=getattr(fs, "group_slug", "") or "",
                disposition=disp.get("disposition", "undecided"),
                is_published=publish_status["is_published"],
                egeria_publish_note=publish_status["note"],
                working_set_hidden=registry.is_working_set_hidden("filesystem", fs.slug),
            )
        )
    return result


@router.post("/", response_model=FileSystemSummary)
def register_filesystem(registration: FileSystemRegistration):
    """Register a new filesystem connection."""
    from resource_explorer.web.routes._validation import validate_egeria_user

    validate_egeria_user(registration.egeria_user)

    registry = ProjectRegistry()

    if registry.filesystem_exists(registration.slug):
        raise HTTPException(
            status_code=400,
            detail=f"FileSystem '{registration.slug}' already registered."
        )

    # Create filesystem entity
    fs = FileSystemEntity(
        slug=registration.slug,
        display_name=registration.display_name,
        local_mount_point=registration.local_mount_point,
        canonical_mount_point=registration.canonical_mount_point,
        description=registration.description,
        egeria_url=registration.egeria_url,
        egeria_server=registration.egeria_server,
        egeria_user=registration.egeria_user,
        egeria_password=registration.egeria_password,
        group_slug=registration.group_slug,
    )
    
    try:
        registry.register_filesystem(fs)
    except Exception as exc:
        log.exception("Failed to register filesystem")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to write to registry: {exc}"
        )
        
    return FileSystemSummary(
        slug=fs.slug,
        display_name=fs.display_name,
        local_mount_point=fs.local_mount_point,
        canonical_mount_point=fs.canonical_mount_point or "",
        description=fs.description,
        status=fs.status.value,
        last_surveyed_at="",
        egeria_asset_guid="",
        file_count=0,
        data_file_count=0,
        egeria_url=fs.egeria_url or "",
        egeria_server=fs.egeria_server or "",
        egeria_user=fs.egeria_user or "",
    )


@router.get("/{slug}/", response_model=FileSystemSummary)
def get_filesystem(slug: str):
    """Retrieve details for a specific registered filesystem."""
    registry = ProjectRegistry()
    fs = registry.get_filesystem(slug)
    if not fs:
        raise HTTPException(
            status_code=404,
            detail=f"FileSystem '{slug}' not found."
        )
    disp = registry.get_disposition_for_entity("filesystem", fs.slug) or {}
    from resource_explorer.egeria_linkage import describe_publish_status
    publish_status = describe_publish_status(
        registry, "filesystem", fs.slug, fs.egeria_asset_guid or "")

    return FileSystemSummary(
        slug=fs.slug,
        display_name=fs.display_name,
        local_mount_point=fs.local_mount_point,
        canonical_mount_point=fs.canonical_mount_point or "",
        description=fs.description,
        status=fs.status.value,
        last_surveyed_at=fs.last_surveyed_at or "",
        egeria_asset_guid=fs.egeria_asset_guid or "",
        file_count=fs.file_count,
        data_file_count=fs.data_file_count,
        disposition=disp.get("disposition", "undecided"),
        egeria_url=fs.egeria_url or "",
        egeria_server=fs.egeria_server or "",
        egeria_user=fs.egeria_user or "",
        is_published=publish_status["is_published"],
        egeria_publish_note=publish_status["note"],
        working_set_hidden=registry.is_working_set_hidden("filesystem", fs.slug),
    )


@router.get("/{slug}/analyses/last-activity")
async def get_analyses_last_activity(slug: str) -> dict[str, dict]:
    """{analysis_id: {last_run_at, last_run_status, last_published_at, ...}}
    for every local filesystem AnalysisKind — the filesystem equivalent of
    `projects.py`'s `GET /{slug}/analyses/last-activity` and `databases.py`'s
    identically-named route. See `workflows.analysis.build_analysis_last_
    activity`'s docstring for exactly what is real data today (run
    attribution) and what is not yet (publish attribution — filesystem's
    publish path does not record `project_published_analyses`/
    `project_published_annotation_types` either, same gap as database's)."""
    from resource_explorer.workflows.analysis import build_analysis_last_activity

    registry = ProjectRegistry()
    if not registry.get_filesystem(slug):
        raise HTTPException(status_code=404, detail=f"FileSystem '{slug}' not found.")

    return build_analysis_last_activity(registry, "filesystem", slug)


@router.get("/{slug}/survey-results")
async def get_filesystem_survey_results(slug: str, stage: str = "", include_empty: bool = False) -> dict:
    """The filesystem equivalent of `projects.py`'s `GET /{slug}/survey-
    results` ("By analysis" in /next) and `databases.py`'s identically-shaped
    route. See `workflows.analysis.build_survey_results`'s docstring — a
    filesystem gets one synthesized dashboard, since `FILESYSTEM_ANALYSIS_
    RESULTS_MAP` has exactly the one entry filesystem has an analysis for."""
    from resource_explorer.workflows.analysis import build_survey_results

    registry = ProjectRegistry()
    if not registry.get_filesystem(slug):
        raise HTTPException(status_code=404, detail=f"FileSystem '{slug}' not found.")

    return await asyncio.to_thread(
        build_survey_results, registry, "filesystem", slug, stage, include_empty,
    )


@router.get("/{slug}/questions")
async def get_filesystem_questions(
    slug: str,
    phase: str = "scouting",
    perspectives: str | None = None,
    purposes: str | None = None,
) -> dict:
    """The filesystem equivalent of `projects.py`'s `GET /{slug}/scouting-
    questions` and `databases.py`'s identically-shaped route — see that
    route's docstring."""
    from resource_explorer.workflows.scouting import build_question_checklist

    registry = ProjectRegistry()
    if not registry.get_filesystem(slug):
        raise HTTPException(status_code=404, detail=f"FileSystem '{slug}' not found.")

    persp_list = [p.strip() for p in (perspectives or "").split(",") if p.strip()]
    purp_list = [p.strip() for p in (purposes or "").split(",") if p.strip()]
    return build_question_checklist(registry, "filesystem", slug, phase, persp_list, purp_list)


@router.delete("/{slug}/")
def delete_filesystem(slug: str):
    """Remove a filesystem registration and all its surveys from the registry."""
    registry = ProjectRegistry()
    if not registry.filesystem_exists(slug):
        raise HTTPException(
            status_code=404,
            detail=f"FileSystem '{slug}' not found."
        )
    try:
        registry.remove_filesystem(slug)
        return {"status": "ok", "message": f"FileSystem '{slug}' removed."}
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to delete filesystem '{slug}': {exc}"
        )


@router.post("/{slug}/survey")
def survey_filesystem(slug: str, req: FileSystemSurveyRequest):
    """Run a local or hybrid survey on the filesystem, optionally publishing to Egeria."""
    from resource_explorer.web.routes._validation import validate_egeria_user

    validate_egeria_user(req.egeria_user or "")

    registry = ProjectRegistry()
    fs_entity = registry.get_filesystem(slug)
    if not fs_entity:
        raise HTTPException(
            status_code=404,
            detail=f"FileSystem '{slug}' not found."
        )

    # 1. Update status to active/surveying
    registry.update_filesystem_status(slug, ProjectStatus.ACTIVE)

    # 2. Run local or hybrid walk
    try:
        if req.mode == "local":
            from resource_explorer.surveyors.filesystem.local_filesystem_surveyor import (
                LocalFileSystemSurveyor,
                build_rfa_annotations,
            )
            local_surveyor = LocalFileSystemSurveyor(fs_entity, registry)
            res = local_surveyor.run()

            # Save local-only survey
            registry.add_filesystem_survey(
                fs_slug=slug,
                surveyed_at=res["surveyed_at"],
                survey_data=res,
                egeria_report_guid="local-only",
                source="local",
            )

            annotations = build_rfa_annotations(res)
            try:
                from resource_explorer.activity_logger import log_survey
                log_survey(
                    registry,
                    entity_type="filesystem",
                    entity_slug=slug,
                    entity_name=fs_entity.display_name,
                    entity_location=fs_entity.local_mount_point,
                    intent="assessment",
                    status="error" if annotations else "ok",
                    summary=f"FileSystem survey: {res['total_files']} files ({res['total_data_files']} data files)",
                    detail=f"Walked {fs_entity.local_mount_point}. Total size {res['total_size']}.",
                    annotations=annotations,
                )
            except Exception:
                log.exception(f"Failed to write activity log entry for filesystem survey {slug}")

            return {
                "status": "ok",
                "mode": "local",
                "total_files": res["total_files"],
                "total_data_files": res["total_data_files"],
                "total_size": res["total_size"],
            }
            
        elif req.mode in ("hybrid", "egeria"):
            # Routed through executes_at="egeria-adaptive" (the folded-in
            # run_hybrid_filesystem_survey strategy) via a one-step synthetic
            # Survey Definition, rather than calling
            # run_hybrid_filesystem_survey directly — see
            # docs/design-notes/EXECUTION-MODES-HYBRID-CLARIFICATION.md.
            from resource_explorer.surveyors.survey_definition_executor import (
                SurveyDefinitionExecutor,
            )

            executor = SurveyDefinitionExecutor(registry)
            exec_result = executor.run_synthetic_step(
                entity_type="filesystem",
                slug=slug,
                re_analysis_step="filesystem_inventory",
                executes_at="egeria-adaptive",
                egeria_url=req.egeria_url,
                egeria_server=req.egeria_server,
                egeria_user=req.egeria_user,
                egeria_password=req.egeria_password,
                force_egeria_publish=req.force_publish or (req.mode == "egeria"),
            )
            step_report = (exec_result.get("steps") or [{}])[0]
            # Reconstruct run_hybrid_filesystem_survey's historic flat
            # response shape: the handler nests the Egeria publish result
            # under "result" so the executor's own generic publish step
            # doesn't attempt to re-publish it a second time.
            detail = dict(step_report.get("detail") or {})
            nested = detail.pop("result", None) or {}
            res = {**detail, **nested}

            publish_info = res.get("egeria_publish") or {}

            return {
                "status": "ok",
                "mode": req.mode,
                "source": res.get("source", "custom"),
                "total_files": res["total_files"],
                "total_data_files": res["total_data_files"],
                "total_size": res["total_size"],
                "egeria_asset_guid": publish_info.get("filesystem_guid", ""),
                "egeria_report_guid": publish_info.get("report_guid", ""),
                "annotation_count": publish_info.get("annotation_count", 0),
            }
            
        else:
            raise HTTPException(status_code=400, detail=f"Unsupported survey mode '{req.mode}'")
            
    except Exception as exc:
        log.exception(f"Filesystem survey failed for {slug}")
        registry.update_filesystem_status(slug, ProjectStatus.ERROR, str(exc))
        raise HTTPException(
            status_code=500,
            detail=f"Survey execution failed: {exc}"
        )


@router.get("/{slug}/surveys")
def list_filesystem_surveys(slug: str):
    """Retrieve history of surveys for a filesystem."""
    registry = ProjectRegistry()
    if not registry.filesystem_exists(slug):
        raise HTTPException(
            status_code=404,
            detail=f"FileSystem '{slug}' not found."
        )
    return registry.list_filesystem_surveys(slug)


@router.get("/{slug}/surveys/latest")
def get_latest_survey(slug: str):
    """Retrieve the latest complete survey for a filesystem."""
    registry = ProjectRegistry()
    survey = registry.get_latest_filesystem_survey(slug)
    if not survey:
        raise HTTPException(
            status_code=404,
            detail=f"No survey history found for filesystem '{slug}'."
        )
    return survey


@router.get("/{slug}/egeria-surveys", response_model=list[EgeriaSurveyReportRow])
def get_filesystem_egeria_surveys(slug: str) -> list[EgeriaSurveyReportRow]:
    """List SurveyReports that actually exist in Egeria right now for this
    filesystem (a live read via the real ReportSubject relationship)."""
    from resource_explorer.surveyors.filesystem.egeria_filesystem_surveyor import (
        EgeriaFileSystemSurveyor,
        EgeriaFileSystemSurveyorError,
    )

    registry = ProjectRegistry()
    fs = registry.get_filesystem(slug)
    if not fs:
        raise HTTPException(status_code=404, detail=f"FileSystem '{slug}' not found.")
    if not fs.egeria_asset_guid:
        return []  # not yet cataloged in Egeria — nothing to walk from

    surveyor = EgeriaFileSystemSurveyor()
    try:
        reports = surveyor.get_survey_reports_by_guid(fs.egeria_asset_guid)
    except EgeriaFileSystemSurveyorError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    return [EgeriaSurveyReportRow(**r) for r in reports]


@router.get("/{slug}/egeria-surveys/{report_guid}/annotations", response_model=list[EgeriaAnnotationItem])
def get_filesystem_egeria_annotations(slug: str, report_guid: str) -> list[EgeriaAnnotationItem]:
    """Fetch all annotations for a specific live Egeria SurveyReport, by the
    report's own GUID."""
    from resource_explorer.surveyors.filesystem.egeria_filesystem_surveyor import (
        EgeriaFileSystemSurveyor,
        EgeriaFileSystemSurveyorError,
    )

    registry = ProjectRegistry()
    fs = registry.get_filesystem(slug)
    if not fs:
        raise HTTPException(status_code=404, detail=f"FileSystem '{slug}' not found.")

    surveyor = EgeriaFileSystemSurveyor()
    try:
        annotations = surveyor.get_annotations_by_report_guid(report_guid)
    except EgeriaFileSystemSurveyorError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    return [EgeriaAnnotationItem(**a) for a in annotations]


@router.post("/{slug}/publish")
def publish_survey_to_egeria(slug: str, req: FileSystemSurveyRequest):
    """Manually publish the latest local filesystem survey details to Egeria."""
    from resource_explorer.web.routes._validation import validate_egeria_user

    validate_egeria_user(req.egeria_user or "")

    registry = ProjectRegistry()
    fs_entity = registry.get_filesystem(slug)
    if not fs_entity:
        raise HTTPException(
            status_code=404,
            detail=f"FileSystem '{slug}' not found."
        )

    survey = registry.get_latest_filesystem_survey(slug)
    if not survey:
        raise HTTPException(
            status_code=400,
            detail="No local survey results available to publish. Run a survey first."
        )

    url = req.egeria_url or fs_entity.egeria_url or ""
    server = req.egeria_server or fs_entity.egeria_server or ""
    user = req.egeria_user or fs_entity.egeria_user or ""
    pwd = req.egeria_password or fs_entity.egeria_password or ""

    if not (url and server and user and pwd):
        raise HTTPException(
            status_code=400,
            detail="Missing Egeria credentials (url, server, user, password)."
        )

    try:
        from resource_explorer.surveyors.filesystem.egeria_filesystem_surveyor import EgeriaFileSystemSurveyor
        egeria_surveyor = EgeriaFileSystemSurveyor(
            platform_url=url,
            view_server=server,
            user_id=user,
            user_password=pwd,
        )
        publish_res = egeria_surveyor.catalog_and_survey(fs_entity, survey["survey_data"], registry=registry)
        return {
            "status": "ok",
            "egeria_asset_guid": publish_res.get("filesystem_guid", ""),
            "egeria_report_guid": publish_res.get("report_guid", ""),
            "annotation_count": publish_res.get("annotation_count", 0),
        }
    except Exception as exc:
        log.exception(f"Manual filesystem publish failed for {slug}")
        raise HTTPException(
            status_code=500,
            detail=f"Publish failed: {exc}"
        )


class ReachabilityResultModel(BaseModel):
    """One reachability probe result, past or just-run — Phase 1 slice #13.
    Mirrors resource_reachability's columns; see reachability.py's module
    docstring for the outcome vocabulary and how each maps to Egeria's raw
    CHECK_ASSET response."""
    resource_type: str = "filesystem"
    filesystem_slug: str
    probed_at: str
    probed_from: str = ""
    outcome: str
    error_code: str = ""
    error_detail: str = ""
    latency_ms: int | None = None
    engine_action_guid: str = ""


@router.get("/{slug}/reachability", response_model=ReachabilityResultModel | None)
def get_filesystem_reachability(slug: str):
    """Most recent reachability check for this filesystem, or null if it has
    never been checked — the "never checked" state is the absence of any
    row, not a sentinel value (see registry.get_latest_reachability's
    docstring)."""
    registry = ProjectRegistry()
    if not registry.filesystem_exists(slug):
        raise HTTPException(status_code=404, detail=f"FileSystem '{slug}' not found.")
    latest = registry.get_latest_reachability(slug)
    return ReachabilityResultModel(**latest) if latest else None


@router.get("/{slug}/reachability/history", response_model=list[ReachabilityResultModel])
def get_filesystem_reachability_history(slug: str, limit: int = 20):
    """Reachability probe history for this filesystem, most recent first."""
    registry = ProjectRegistry()
    if not registry.filesystem_exists(slug):
        raise HTTPException(status_code=404, detail=f"FileSystem '{slug}' not found.")
    rows = registry.list_reachability_history(slug, limit=limit)
    return [ReachabilityResultModel(**r) for r in rows]


@router.post("/{slug}/reachability", response_model=ReachabilityResultModel)
def check_filesystem_reachability_endpoint(slug: str):
    """Trigger the fast CHECK_ASSET reachability probe for this filesystem
    and return (and persist) its result.

    Scope (Phase 1 slice #13): filesystem/folder resources only — see
    reachability.py's module docstring for why database reachability is not
    handled by this same code path. Never 500s for an Egeria-side failure
    (timeout, disconnected platform, ...) — that is reported as a normal
    `outcome: "unknown"` result, per the three-state absence discipline, not
    an HTTP error. A 404 here means the filesystem itself isn't registered,
    which is a different, real error.
    """
    from resource_explorer.reachability import ReachabilityCheckScopeError, check_filesystem_reachability

    registry = ProjectRegistry()
    if not registry.filesystem_exists(slug):
        raise HTTPException(status_code=404, detail=f"FileSystem '{slug}' not found.")

    try:
        result = check_filesystem_reachability(slug, registry)
    except ReachabilityCheckScopeError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    latest = registry.get_latest_reachability(slug)
    return ReachabilityResultModel(**latest)
