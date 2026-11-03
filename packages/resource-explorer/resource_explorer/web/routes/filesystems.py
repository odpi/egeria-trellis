"""Filesystem management endpoints — list, get, register, survey, remove."""
from __future__ import annotations

import asyncio
import logging
from typing import Any
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from resource_explorer.web.routes._annotation_items import build_annotation_items
from resource_explorer.registry import FileSystemEntity, ProjectRegistry

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
    # Remote reach (outcome 3). No secret is ever in a summary: only who RE is to the bucket, by name.
    protocol: str = "local"
    location: str = ""
    credential_name: str = ""


class S3Fields(BaseModel):
    """The S3 registration fields (D-S3 §3). The key's secret is accepted only to be held in the secrets store (or, for
    "Check reach", in memory for that one call); it is never stored in the registry, logged or answered."""
    endpoint_url: str = ""
    region: str = ""
    bucket: str = ""
    root_prefix: str = ""
    path_style: bool = False
    tls_verify: bool = True
    credential_kind: str = "none"       # none | secret_ref | profile
    credential_ref: str = ""            # a profile's name; for a stored key, filled in by the server
    access_key_id: str = ""
    secret_access_key: str = ""
    session_token: str = ""


class FileSystemRegistration(S3Fields):
    """Request body for registering a new filesystem."""
    slug: str
    display_name: str
    local_mount_point: str = ""
    protocol: str = "local"
    canonical_mount_point: str = ""
    description: str = ""
    egeria_url: str = ""
    egeria_server: str = ""
    #: Ignored (owner, 2026-10-09): accepted only so an older caller is not refused; logged.
    egeria_user: str = ""
    egeria_password: str = ""
    group_slug: str = ""


class FileSystemSurveyRequest(BaseModel):
    """Request body for running a filesystem survey."""
    mode: str = "hybrid"  # "local" | "hybrid" | "egeria"
    egeria_url: str | None = None
    egeria_server: str | None = None
    #: Ignored (owner, 2026-10-09): accepted only so an older caller is not refused; logged.
    egeria_user: str | None = None
    egeria_password: str | None = None
    force_publish: bool = False
    #: Publish to Egeria (Brief I round 4: an explicit choice). Also chosen by SENDING mode
    #: "hybrid" or "egeria"; the default mode alone never publishes.
    publish: bool = False


def _publish_chosen(req: "FileSystemSurveyRequest") -> bool:
    """Whether the caller chose to publish this survey to Egeria: `publish`/`force_publish`, or an
    explicitly sent mode "hybrid"/"egeria" (not the field's default)."""
    sent_mode = "mode" in req.model_fields_set and req.mode in ("hybrid", "egeria")
    return bool(req.publish or req.force_publish or sent_mode)


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


@router.get("/by-passages")
def find_filesystems_by_passages(q: str) -> dict:
    """The Resource Locator's passage search: file systems whose pressed narrative files say something about `q`, ranked
    by best passage, each cited and labelled a proposal. File systems never pressed are counted, not searched."""
    from resource_explorer.narrative_context import locate

    if not q.strip():
        raise HTTPException(status_code=422, detail="q is empty.")
    return locate(ProjectRegistry(), q.strip())


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
                group_slug=getattr(fs, "group_slug", "") or "",
                disposition=disp.get("disposition", "undecided"),
                is_published=publish_status["is_published"],
                egeria_publish_note=publish_status["note"],
                working_set_hidden=registry.is_working_set_hidden("filesystem", fs.slug),
                **_remote_summary(fs),
            )
        )
    return result


def _remote_summary(fs) -> dict:
    from resource_explorer import reach_mechanisms

    if not reach_mechanisms.is_remote(fs):
        return {"protocol": "local", "location": "", "credential_name": ""}
    from resource_explorer.reach_mechanisms.s3 import S3Settings

    st = S3Settings.from_filesystem(fs)
    return {"protocol": fs.protocol, "location": st.location, "credential_name": st.credential_name()}


def _s3_settings(slug: str, f: S3Fields):
    """The mechanism settings a form describes. A typed key is named by the ref it WILL be held under."""
    from resource_explorer.reach_mechanisms import CREDENTIAL_SECRET_REF, ReachError
    from resource_explorer.reach_mechanisms.s3 import S3Settings, secret_ref

    kind = (f.credential_kind or "none").strip()
    ref = (f.credential_ref or "").strip()
    if kind == CREDENTIAL_SECRET_REF:
        ref = secret_ref(ProjectRegistry._normalize_slug(slug) if slug else "new")
    try:
        return S3Settings(bucket=f.bucket.strip(), endpoint_url=f.endpoint_url.strip(), region=f.region.strip(),
                          root_prefix=f.root_prefix.strip(), path_style=f.path_style, tls_verify=f.tls_verify,
                          credential_kind=kind, credential_ref=ref,
                          access_key_id=f.access_key_id.strip()).validate()
    except ReachError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


class S3ReachCheck(S3Fields):
    slug: str = ""


@router.get("/reach-mechanisms")
def list_reach_mechanisms() -> dict:
    """The installed reach mechanisms' manifests, and every entry point that was skipped, by name with its error."""
    from resource_explorer import reach_mechanisms
    from resource_explorer.reach_mechanisms.s3 import secrets_store_configured

    found = reach_mechanisms.discover_mechanisms()
    return {"mechanisms": [cls.manifest.as_dict() for cls in found.values()],
            "skipped": reach_mechanisms.mechanism_load_failures(),
            "secrets_store_configured": secrets_store_configured()}


@router.post("/check-reach")
def check_s3_reach(req: S3ReachCheck) -> dict:
    """"Check reach" on the register form, before Register: one ListObjectsV2 with MaxKeys=1 under the root, answered
    with the outcome-3 sentence or the source's refusal. Nothing is stored: a typed key is used for this one call and
    dropped. Gated like registration (it reaches out from this server)."""
    from resource_explorer.reach_mechanisms import CREDENTIAL_SECRET_REF, ReachError
    from resource_explorer.reach_mechanisms.s3 import S3Mechanism, re_host, unreachable_sentence

    _require_registration_right(ProjectRegistry())
    settings = _s3_settings(req.slug, req)
    key = None
    if settings.credential_kind == CREDENTIAL_SECRET_REF:
        if not (req.access_key_id.strip() and req.secret_access_key.strip()):
            raise HTTPException(status_code=400, detail="a stored key is checked with the key typed on the form: "
                                                        "give its access key id and secret access key")
        key = (req.access_key_id.strip(), req.secret_access_key.strip(), req.session_token.strip())
    try:
        return S3Mechanism(settings, key=key).check_reach()
    except ReachError as exc:
        from datetime import datetime, timezone

        return {"reachable": False, "error": str(exc), "host": re_host(), "credential_name": settings.credential_name(),
                "location": settings.location, "protocol": "s3",
                "sentence": unreachable_sentence(re_host(), settings.credential_name(), str(exc),
                                                 datetime.now(timezone.utc).isoformat())}


@router.post("/", response_model=FileSystemSummary)
def register_filesystem(registration: FileSystemRegistration):
    """Register a new filesystem connection."""
    from resource_explorer.web.routes._validation import ignore_egeria_credentials

    ignore_egeria_credentials(registration, where="register filesystem")

    registry = ProjectRegistry()
    _require_registration_right(registry)
    protocol = (registration.protocol or "local").strip().lower()
    if protocol == "s3":
        return _register_s3(registry, registration)
    if protocol != "local":
        raise HTTPException(status_code=400, detail=f"No reach mechanism speaks {protocol!r} on this server.")
    if not registration.local_mount_point.strip():
        raise HTTPException(status_code=400, detail="A local file system needs its mount point.")
    for what, mount in (("local mount point", registration.local_mount_point),
                        ("canonical mount point", registration.canonical_mount_point)):
        if mount.strip() and not mount.strip().rstrip("/"):
            raise HTTPException(
                status_code=400,
                detail=f"The {what} is the root of the file system ('/'). Register the folder to catalog, not the root.")

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

    # The answer is the STORED row (a slug is stored with dashes as underscores), so a caller that selects the
    # new file system by the slug it gets back finds the row the list shows.
    stored = registry.get_filesystem(fs.slug) or fs
    return FileSystemSummary(
        slug=stored.slug,
        display_name=stored.display_name,
        local_mount_point=stored.local_mount_point,
        canonical_mount_point=stored.canonical_mount_point or "",
        description=stored.description,
        status=stored.status.value,
        last_surveyed_at="",
        egeria_asset_guid="",
        file_count=0,
        data_file_count=0,
        egeria_url=stored.egeria_url or "",
        egeria_server=stored.egeria_server or "",
        group_slug=getattr(stored, "group_slug", "") or "",
    )


def _register_s3(registry: ProjectRegistry, registration: FileSystemRegistration) -> FileSystemSummary:
    """An S3 file system. A typed key goes to the secrets store FIRST (refused when none is configured, before any row is
    written); the registry keeps only its reference and the key id. Then the reach check runs and is recorded."""
    from resource_explorer import file_reach
    from resource_explorer.reach_mechanisms import CREDENTIAL_SECRET_REF, ReachError
    from resource_explorer.reach_mechanisms.s3 import store_key

    if registry.filesystem_exists(registration.slug):
        raise HTTPException(status_code=400, detail=f"FileSystem '{registration.slug}' already registered.")
    settings = _s3_settings(registration.slug, registration)
    if settings.credential_kind == CREDENTIAL_SECRET_REF:
        if registration.secret_access_key.strip():
            try:
                store_key(ProjectRegistry._normalize_slug(registration.slug), registration.access_key_id,
                          registration.secret_access_key, registration.session_token)
            except ReachError as exc:
                raise HTTPException(status_code=400, detail=str(exc))
        else:
            from resource_explorer import omsecrets_store

            if not omsecrets_store.has_collection(settings.credential_ref):
                raise HTTPException(status_code=400, detail=(
                    f"the secrets store holds no key named {settings.credential_ref!r}: type the key to hold it"))
    fs = FileSystemEntity(
        slug=registration.slug, display_name=registration.display_name,
        local_mount_point=settings.location, canonical_mount_point=settings.location,
        description=registration.description, egeria_url=registration.egeria_url,
        egeria_server=registration.egeria_server, group_slug=registration.group_slug,
        protocol="s3", endpoint_url=settings.endpoint_url, region=settings.region, bucket=settings.bucket,
        root_prefix=settings.root, path_style=settings.path_style, tls_verify=settings.tls_verify,
        credential_kind=settings.credential_kind, credential_ref=settings.credential_ref,
        access_key_id=settings.access_key_id)
    try:
        registry.register_filesystem(fs)
    except Exception as exc:
        log.exception("Failed to register filesystem")
        raise HTTPException(status_code=500, detail=f"Failed to write to registry: {type(exc).__name__}")
    stored = registry.get_filesystem(fs.slug) or fs
    # One list call, recorded as the outcome-3 row (a refusal is recorded too: probe_remote catches ReachError).
    file_reach.probe_filesystem(stored.slug, registry)
    return FileSystemSummary(
        slug=stored.slug, display_name=stored.display_name, local_mount_point=stored.local_mount_point,
        canonical_mount_point=stored.canonical_mount_point or "", description=stored.description,
        status=stored.status.value, last_surveyed_at="", egeria_asset_guid="", file_count=0, data_file_count=0,
        egeria_url=stored.egeria_url or "", egeria_server=stored.egeria_server or "",
        group_slug=stored.group_slug or "", **_remote_summary(stored))


class MountCheckRequest(BaseModel):
    path: str = ""


def _require_registration_right(registry: ProjectRegistry) -> None:
    """Who may register a file system, and probe a path on this server's disk: the person who may CREATE a file
    system's asset in Egeria, the write registering exists to make. The database registration route checks
    nothing (it has no element to judge); this reuses the one decision every resource write calls
    (`workflows.curate.resource_access`, the `publish` write for an element not in Egeria yet): not signed in is
    refused, a Portal curator/admin is allowed, zones not in use is open, zones in use is Egeria's own answer."""
    from resource_explorer.web.routes.curate import require_resource_write

    require_resource_write(registry, "filesystem", "", "publish", element_guid="")


@router.post("/check-mount")
def check_mount(req: MountCheckRequest) -> dict:
    """Can THIS server read a mount path? A warning for the register form, never a gate: the server may see the
    file system at a different path than the person (or Egeria) does. Answers one of `readable`,
    `not_a_folder`, `not_readable`, `empty` (nothing typed). Reads nothing inside the folder beyond opening it,
    and takes the path as typed (no `~` expansion). Gated like registration: it tells the caller what exists on
    this server's disk."""
    import os

    _require_registration_right(ProjectRegistry())
    path = (req.path or "").strip()
    if not path:
        return {"state": "empty"}
    if os.path.isdir(path):
        if os.access(path, os.R_OK | os.X_OK):
            return {"state": "readable"}
        return {"state": "not_readable"}
    if os.path.exists(path):
        return {"state": "not_a_folder"}
    return {"state": "not_readable"}


@router.get("/{slug}/survey-results/boards")
async def get_filesystem_survey_results_boards(slug: str, stage: str = "") -> dict:
    """The By-analysis contents board's cheap half for filesystems — see
    `projects.py`'s identically-shaped route and `workflows.analysis.
    list_survey_result_boards`'s docstring."""
    from resource_explorer.workflows.analysis import list_survey_result_boards

    registry = ProjectRegistry()
    if not registry.get_filesystem(slug):
        raise HTTPException(status_code=404, detail=f"FileSystem '{slug}' not found.")

    return await asyncio.to_thread(list_survey_result_boards, registry, "filesystem", slug, stage)


@router.get("/{slug}/survey-results")
async def get_filesystem_survey_results(
    slug: str, stage: str = "", include_empty: bool = False, board_id: str = "",
) -> dict:
    """The filesystem equivalent of `projects.py`'s `GET /{slug}/survey-
    results` ("By analysis" in /next) and `databases.py`'s identically-shaped
    route. See `workflows.analysis.build_survey_results`'s docstring — a
    filesystem gets one synthesized dashboard, since `FILESYSTEM_ANALYSIS_
    RESULTS_MAP` has exactly the one entry filesystem has an analysis for.

    `board_id` (optional): one board only, for the /next By-analysis pane's
    progressive fetch — see `projects.py`'s `get_survey_results` docstring."""
    from resource_explorer.workflows.analysis import build_survey_results

    registry = ProjectRegistry()
    if not registry.get_filesystem(slug):
        raise HTTPException(status_code=404, detail=f"FileSystem '{slug}' not found.")

    return await asyncio.to_thread(
        build_survey_results, registry, "filesystem", slug, stage, include_empty, board_id,
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
    route's docstring, including why this is now off the event loop
    (BY-ANALYSIS-PROGRESSIVE-AND-GRAPH, 2026-09-28: `question_has_data`
    reaches the same expensive readers `/survey-results` already
    off-loads)."""
    from resource_explorer.workflows.scouting import build_question_checklist

    registry = ProjectRegistry()
    if not registry.get_filesystem(slug):
        raise HTTPException(status_code=404, detail=f"FileSystem '{slug}' not found.")

    persp_list = [p.strip() for p in (perspectives or "").split(",") if p.strip()]
    purp_list = [p.strip() for p in (purposes or "").split(",") if p.strip()]
    return await asyncio.to_thread(
        build_question_checklist, registry, "filesystem", slug, phase, persp_list, purp_list,
    )


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
    return build_annotation_items(EgeriaAnnotationItem, annotations)


# ── Attach the root folder (Files step 1) ────────────────────────────────

def _root_folder_view(registry: ProjectRegistry, slug: str) -> dict:
    """The root folder's state, derived from the NEWEST live proof row (one read since Egeria's last reset) and
    the GUID RE holds: `attached` only when the newest row says a read-back by GUID matched; `not_confirmed` when
    it says it did not (with the words); `held` when a GUID is held (the survey path published the folder) but no
    read-back was recorded yet, which is "not measured", never "not attached"; `not_attached` when there is
    neither. `reset_at` is the reset time when one exists."""
    proof = registry.latest_filesystem_root_proof(slug)
    reset_at = registry.filesystem_reset_at(slug)
    if not proof:
        fs = registry.get_filesystem(slug)
        held = ((fs.egeria_asset_guid if fs else "") or "").strip()
        return {"state": "held" if held else "not_attached", "guid": held, "read_back_at": "", "by": "",
                "why": "", "reset_at": reset_at}
    attached = proof["proof"] == registry.FS_ROOT_ATTACHED
    detail = proof.get("detail") or {}
    return {
        "state": "attached" if attached else "not_confirmed",
        "guid": proof.get("element_guid", ""),
        "read_back_at": proof.get("read_at", ""),
        "by": proof.get("recorded_by", ""),
        "why": "" if attached else str(detail.get("why", "")),
        "reset_at": reset_at,
    }


@router.get("/{slug}/root-folder")
def get_root_folder(slug: str) -> dict:
    """Whether this file system's root folder is attached in Egeria, from the proof row (see `_root_folder_view`)."""
    registry = ProjectRegistry()
    if not registry.filesystem_exists(slug):
        raise HTTPException(status_code=404, detail=f"FileSystem '{slug}' not found.")
    return _root_folder_view(registry, slug)


@router.post("/{slug}/attach-root")
def attach_root_folder(slug: str) -> dict:
    """Attach the file system's ROOT FOLDER as an Egeria asset, as the signed-in person, and read it back by GUID.

    This is the page's one Publish for a file system at this step. It attaches the root folder ONLY (see
    `EgeriaFileSystemSurveyor.attach_root_folder`): no tags, group, documentation sources or feedback go with it
    (`single_publish.send_with_publish` is not called); the survey-driven publish above (`/publish`) is unchanged
    and still sends data files, the SurveyReport and annotations. A press with a GUID already held reads that
    element back and creates nothing. The proof row records the GUID Egeria's read-back returned and when."""
    registry = ProjectRegistry()
    fs_entity = registry.get_filesystem(slug)
    if not fs_entity:
        raise HTTPException(status_code=404, detail=f"FileSystem '{slug}' not found.")

    from resource_explorer.web.routes.curate import require_resource_write
    require_resource_write(registry, "filesystem", slug, "publish")

    try:
        from resource_explorer.egeria_clients import Caller
        from resource_explorer.egeria_identity import caller_user_id
        from resource_explorer.surveyors.filesystem.egeria_filesystem_surveyor import EgeriaFileSystemSurveyor

        surveyor = EgeriaFileSystemSurveyor(
            platform_url=fs_entity.egeria_url or None, view_server=fs_entity.egeria_server or None,
            identity=Caller())
        out = surveyor.attach_root_folder(fs_entity, registry=registry)
        who = caller_user_id()
        registry.append_filesystem_root_proof(
            slug,
            proof=registry.FS_ROOT_ATTACHED if out["matched"] else "folder_not_confirmed",
            element_guid=out["guid"], qualified_name=out.get("qualified_name", ""),
            detail={"why": out.get("why", ""), "type_name": out.get("type_name", ""), "created": out["created"]},
            recorded_by=who)
        reach = None
        reach_error = ""       # set when the probe could not run: the attach itself still succeeded
        if out["matched"]:
            # Files step 2: the reachability probe runs at registration (here, where the file system first has an
            # Egeria element to ask about) and before each survey. It never fails the attach.
            try:
                from resource_explorer import file_reach

                reach = file_reach.probe_filesystem(slug, registry).as_dict()
            except Exception as exc:  # noqa: BLE001
                log.warning("Reachability probe after attaching %s failed", slug, exc_info=True)
                reach_error = f"{type(exc).__name__}: {str(exc)[:200]}"
        return {**_root_folder_view(registry, slug), "created": out["created"],
                "qualified_name": out.get("qualified_name", ""), "type_name": out.get("type_name", ""),
                "reach": reach, "reach_error": reach_error}
    except PermissionError:
        raise      # no caller / expired sign-in: the app's 401 handler, never a 500
    except Exception as exc:
        from resource_explorer.egeria_clients import refusal

        refused = refusal(exc)
        if refused is not None:
            raise refused from exc
        log.exception("Attaching the root folder failed for %s", slug)
        raise HTTPException(status_code=500, detail=f"Publish failed: {exc}")


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


@router.get("/{slug}/reachability/history", response_model=list[ReachabilityResultModel])
def get_filesystem_reachability_history(slug: str, limit: int = 20):
    """Reachability probe history for this filesystem, most recent first."""
    registry = ProjectRegistry()
    if not registry.filesystem_exists(slug):
        raise HTTPException(status_code=404, detail=f"FileSystem '{slug}' not found.")
    rows = registry.list_reachability_history(slug, limit=limit)
    return [ReachabilityResultModel(**r) for r in rows]


# ── Who can read the path, and so who leads the folder survey (Files step 2) ──────────────────────────────────────

@router.get("/{slug}/reach")
def get_filesystem_reach(slug: str) -> dict:
    """The probe's state from the stored rows (no Egeria call): `egeria`, `re_only`, `undecided` or `not_checked`, with
    the one sentence, the date and who leads. "Not checked yet" is its own state, never either outcome."""
    from resource_explorer import file_reach

    registry = ProjectRegistry()
    fs = registry.get_filesystem(slug)
    if not fs:
        raise HTTPException(status_code=404, detail=f"FileSystem '{slug}' not found.")
    return file_reach.fork_for(registry, fs).as_dict()


@router.post("/{slug}/reach")
def check_filesystem_reach(slug: str) -> dict:
    """Run the mounted-prefix check now ("Check again": asks Egeria nothing; is the canonical mount under one of the
    container's mounted paths?), record the outcome with the path checked, and return the state the stored rows then
    prove. Needs the same authorisation as the survey run."""
    from resource_explorer import file_reach

    from resource_explorer.web.routes.curate import require_resource_write

    registry = ProjectRegistry()
    if not registry.filesystem_exists(slug):
        raise HTTPException(status_code=404, detail=f"FileSystem '{slug}' not found.")
    require_resource_write(registry, "filesystem", slug, "start_action")
    return file_reach.probe_filesystem(slug, registry).as_dict()


@router.get("/{slug}/folder-survey")
def get_filesystem_folder_survey(slug: str) -> dict:
    """The newest folder survey, whoever led it, in one shape (file types, counts, the inaccessible files, the walk's
    limits and whether it was capped), with the probe state. Registry-only."""
    from resource_explorer.folder_survey_view import folder_survey_view

    registry = ProjectRegistry()
    if not registry.filesystem_exists(slug):
        raise HTTPException(status_code=404, detail=f"FileSystem '{slug}' not found.")
    return folder_survey_view(registry, slug)


class FolderSurveyRequest(BaseModel):
    #: Publish Resource Explorer's walk as a SurveyReport after it runs (only where the probe proves Egeria could not
    #: reach the path). An Egeria-led survey is Egeria's own report and needs no publish.
    publish: bool = False


@router.post("/{slug}/folder-survey")
def run_filesystem_folder_survey(slug: str, req: FolderSurveyRequest = FolderSurveyRequest()) -> dict:
    """The folder survey, led by whoever can read the path. The probe runs first (before each survey) and its stored
    outcome decides: Egeria's Folder Survey is submitted through the native-survey port (it takes minutes: the answer
    is the submitted run, read back by the survey card), or Resource Explorer's walk runs here (names, sizes and times
    only) and is stored, and published when asked. A path nobody could be shown to read is refused with the probe's
    words, never surveyed by guess."""
    from resource_explorer import file_reach
    from resource_explorer import native_survey_run as nsr
    from resource_explorer.run_queue import requested_by
    from resource_explorer.web.routes.curate import require_resource_write

    registry = ProjectRegistry()
    fs = registry.get_filesystem(slug)
    if not fs:
        raise HTTPException(status_code=404, detail=f"FileSystem '{slug}' not found.")
    require_resource_write(registry, "filesystem", slug, "start_action")
    submitted_by = requested_by()

    fork = file_reach.ensure_checked(slug, registry)
    if fork.state in (file_reach.EGERIA, file_reach.EXPECTED):
        from resource_explorer.web.routes.native_surveys import _port, _technology_type

        try:
            with nsr.port_session(_port()) as port:
                run = nsr.submit_native_survey(
                    registry, port, "filesystem", slug, nsr.FOLDER_SURVEY_QN,
                    technology_type=_technology_type("filesystem"), submitted_by=submitted_by)
        except nsr.NativeSurveyBusy as exc:
            raise HTTPException(status_code=409, detail=str(exc))
        except nsr.NativeSurveyCannotRun as exc:
            raise HTTPException(status_code=422, detail=str(exc))
        except nsr.NativeSurveyError as exc:
            raise HTTPException(status_code=502, detail=str(exc))
        return {"led_by": "egeria", "surveyor": file_reach.SURVEYOR_EGERIA, "reach": fork.as_dict(), "run": run}

    if fork.surveyor != file_reach.SURVEYOR_RE:
        raise HTTPException(status_code=409, detail=file_reach.refusal_words(fork, for_egeria_lead=True))

    from resource_explorer.surveyors.filesystem.survey_definition_adapter import _run_filesystem_inventory

    from resource_explorer.surveyors.filesystem.survey_definition_adapter import FolderSurveyNotRunHere

    try:
        out = _run_filesystem_inventory(fs, registry, fork=fork)
    except (FileNotFoundError, FolderSurveyNotRunHere) as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    sd = out.get("survey_data") or {}
    limits = sd.get("walk_limits") or {}
    result: dict = {"led_by": "resource_explorer", "surveyor": file_reach.SURVEYOR_RE, "reach": fork.as_dict(),
                    "surveyed_at": sd.get("surveyed_at", ""), "total_files": sd.get("total_files"),
                    "capped": bool(limits.get("capped")), "capped_reasons": limits.get("capped_reasons", []),
                    "capped_words": limits.get("capped_words", ""),
                    "rows_kept": (sd.get("walk_extensions") or {}).get("rows_kept"),
                    "persisted": out.get("persisted", False), "published": False, "report_guid": ""}
    if req.publish:
        result.update(_publish_walk(registry, fs, sd, slug))
    return result


def _publish_walk(registry, fs, sd: dict, slug: str, *, check_reach: bool = True) -> dict:
    """Publish a stored Resource Explorer walk as its own SurveyReport under Egeria's folder-survey annotation types,
    as the signed-in person. Only where the stored probe row proves Egeria could not reach the path (the report says
    so). Returns the publish words to add to an answer; never raises for a refusal (it says why)."""
    from resource_explorer import file_reach
    from resource_explorer.surveyors.survey_definition_executor import NothingToPublish
    from resource_explorer.web.routes.curate import require_resource_write

    require_resource_write(registry, "filesystem", slug, "publish")
    out: dict = {"published": False, "report_guid": ""}
    try:
        from resource_explorer.egeria_clients import Caller
        from resource_explorer.surveyors.filesystem.egeria_filesystem_surveyor import EgeriaFileSystemSurveyor

        # A walk that stands in for the folder survey is published only where the probe proves Egeria could not reach the
        # path. RE's extensions BESIDE Egeria's own survey (`check_reach=False`) are measures Egeria does not keep, so
        # that proof is not what they wait for.
        refusal = file_reach.refusal_words(file_reach.fork_for(registry, fs), for_egeria_lead=False) if check_reach else ""
        if refusal:
            raise NothingToPublish(f"not published · {refusal}")
        surveyor = EgeriaFileSystemSurveyor(
            platform_url=fs.egeria_url or None, view_server=fs.egeria_server or None, identity=Caller())
        # The measures chosen Publish on Curate's table ride inside this report (the owner's ruling, 2026-10-28), each
        # read back by name; a table that could not be read sends nothing and says so (`state: not_read`).
        from resource_explorer import publish_table

        declared = publish_table.annotations_for(registry, slug, "filesystem")
        published = surveyor.publish_step_annotations(fs, sd, registry, declared_measures=declared)
        out["declared_measures"] = _record_declared_proofs(registry, slug, published, _publisher())
        from resource_explorer.folder_survey_view import _publish_words

        out.update(report_guid=published.get("report_guid", ""), annotation_count=published.get("annotation_count"),
                   annotations_in_egeria=published.get("annotations_in_egeria"), derived=published.get("derived"),
                   **_publish_words(published))
    except NothingToPublish as exc:
        out["publish_refused"] = str(exc)
    except PermissionError:
        raise
    except Exception as exc:  # noqa: BLE001 -- the walk stands; the publish failure is said
        from resource_explorer.egeria_clients import refusal as egeria_refusal

        refused = egeria_refusal(exc)
        if refused is not None:
            raise refused from exc
        log.exception("Publishing the folder survey failed for %s", slug)
        out["publish_error"] = str(exc)
    return out


def _publisher() -> str:
    """Who is publishing, for the proof rows: the signed-in person the Egeria call was made as."""
    from resource_explorer.run_queue import requested_by

    return requested_by() or ""


def _record_declared_proofs(registry, slug: str, published: dict, author: str) -> dict:
    """One proof row per chosen measure, from the report's read (`publish_table.record_proofs`), never from the send.
    Returns the words for the answer; a proof that could not be written is said, the publish having happened."""
    from resource_explorer import publish_table

    declared = published.get("declared_measures") or {}
    sent = declared.get("sent") or []
    out: dict = {"state": declared.get("state", ""), "sent": len(sent)}
    if declared.get("state") == "not_read":
        out["words"] = "declared measures not sent · the publish table could not be read"
    if not (sent or declared.get("not_built")):
        return out
    try:
        counts = publish_table.record_proofs(registry, slug, sent, report_guid=published.get("report_guid", ""),
                                             recorded_by=author, entity_type="filesystem")
    except Exception as exc:  # noqa: BLE001 -- the report is in Egeria; the missing proof is said, not hidden
        log.exception("recording the declared-measure proofs of %s failed", slug)
        out["words"] = f"{len(sent)} declared measures sent · their read-back could not be recorded: {exc}"
        return out
    out.update(read_back=counts["read_back"], words=publish_table.step_words(counts, declared.get("not_built")))
    return out


class WalkExtensionsRequest(BaseModel):
    #: Publish the extensions as a SurveyReport of Resource Explorer's own after they are measured.
    publish: bool = False


@router.post("/{slug}/walk-extensions")
def run_filesystem_walk_extensions(slug: str, req: WalkExtensionsRequest = WalkExtensionsRequest()) -> dict:
    """Resource Explorer's measures BESIDE an Egeria-led folder survey (Files build step 1): per-folder bytes and depth,
    owner, group and mode, content hashes under a size limit, and the folders' README-type narratives, with the per-file rows
    kept for this run. Only for a file system Egeria's Folder Survey leads; where Resource Explorer leads, the folder-survey
    press already measures all of this in the same walk (409 says so). Writes nothing into the path."""
    from resource_explorer import file_reach
    from resource_explorer.folder_survey_view import _egeria_candidate
    from resource_explorer.surveyors.filesystem import walk_extensions as we
    from resource_explorer.web.routes.curate import require_resource_write

    registry = ProjectRegistry()
    fs = registry.get_filesystem(slug)
    if not fs:
        raise HTTPException(status_code=404, detail=f"FileSystem '{slug}' not found.")
    require_resource_write(registry, "filesystem", slug, "start_action")
    fork = file_reach.ensure_checked(slug, registry)
    if fork.state not in (file_reach.EGERIA, file_reach.EXPECTED):
        if fork.surveyor == file_reach.SURVEYOR_RE:
            why = "Resource Explorer leads this file system's folder survey: its walk already measures these. Press Survey."
        else:
            why = file_reach.refusal_words(fork, for_egeria_lead=True)
        raise HTTPException(status_code=409, detail=why)
    egeria = _egeria_candidate(registry, slug) or {}
    guid = egeria.get("report_guid", "") if not egeria.get("none") else ""
    try:
        out = we.run_alongside_egeria(fs, registry, egeria_report_guid=guid)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    ext = out["walk_extensions"]
    result: dict = {"led_by": "egeria", "surveyor": file_reach.SURVEYOR_RE, "surveyed_at": out["surveyed_at"],
                    "run_id": ext["run_id"], "files": ext["files"], "capped": bool(out["walk_limits"].get("capped")),
                    "capped_words": ext.get("capped_words", ""), "rows_kept": ext.get("rows_kept"),
                    "beside_egeria_report": guid, "published": False, "report_guid": ""}
    if req.publish:
        result.update(_publish_walk(registry, fs, out, slug, check_reach=False))
    return result


@router.post("/{slug}/walk-extensions/publish")
def publish_filesystem_walk_extensions(slug: str) -> dict:
    """Publish the newest stored beside-Egeria extensions run (nothing is walked again). 409 when there is none."""
    registry = ProjectRegistry()
    fs = registry.get_filesystem(slug)
    if not fs:
        raise HTTPException(status_code=404, detail=f"FileSystem '{slug}' not found.")
    from resource_explorer import file_reach

    surveys = registry.list_filesystem_surveys(slug)
    runs = [r for r in surveys if r.get("source") == "walk-extensions"]
    if not runs:
        raise HTTPException(status_code=409, detail="No Resource Explorer extensions run to publish. Run it first.")
    newest = max(runs, key=lambda r: r.get("surveyed_at") or "")
    # Who leads NOW: if Resource Explorer leads, its own folder survey carries these measures; this beside-Egeria run is stale.
    fork = file_reach.ensure_checked(slug, registry)
    if fork.state not in (file_reach.EGERIA, file_reach.EXPECTED) and fork.surveyor == file_reach.SURVEYOR_RE:
        raise HTTPException(status_code=409, detail="Resource Explorer now leads this file system's folder survey, "
                            "so these beside-Egeria measures are out of date. Run the survey and publish that.")
    # One publish per run: a stored publish row for the same run means it is already in Egeria.
    if any(r.get("source") == "egeria-published" and r.get("egeria_report_guid")
           and r.get("surveyed_at") == newest.get("surveyed_at") for r in surveys):
        raise HTTPException(status_code=409, detail="This extensions run is already published to Egeria. "
                            "Run it again to publish newer measures.")
    return _publish_walk(registry, fs, newest["survey_data"], slug, check_reach=False)


@router.post("/{slug}/folder-survey/publish")
def publish_filesystem_folder_survey(slug: str) -> dict:
    """Publish the newest stored Resource Explorer walk (nothing is walked again). 409 when there is none."""
    registry = ProjectRegistry()
    fs = registry.get_filesystem(slug)
    if not fs:
        raise HTTPException(status_code=404, detail=f"FileSystem '{slug}' not found.")
    walks = [r for r in registry.list_filesystem_surveys(slug) if (r.get("survey_data") or {}).get("surveyed_by")]
    if not walks:
        raise HTTPException(status_code=409, detail="No Resource Explorer walk to publish. Run the survey first.")
    newest = max(walks, key=lambda r: (r["survey_data"].get("surveyed_at") or r.get("surveyed_at") or ""))
    return _publish_walk(registry, fs, newest["survey_data"], slug)


# ── Files build step 6: the derived steps (Analytic Library steps over the walk's kept per-file rows) ───────────────

@router.post("/{slug}/analyses/{analysis_id}/run")
def run_filesystem_analysis(slug: str, analysis_id: str, pressed_on: str = "") -> dict:
    """Run ONE file system analysis that is an Analytic Library step (fs_change_rates, fs_duplicates, fs_activity,
    fs_permissions, fs_layout_signals): validated here (an unknown id, or a step whose package is not installed, is a 400 naming why),
    then queued, as every step is (a step never runs in the web process). Zero-fetch: the step reads the walk's kept
    rows, opens no file and takes no credential. The answer is `{status: "started", activity_id, run_id}`; the result
    is stored, not published. The folder survey, the walk extensions and file_profiles keep their own presses."""
    from resource_explorer.activity_logger import log_analysis_run
    from resource_explorer.run_queue import requested_by
    from resource_explorer.surveyors import survey_step_host
    from resource_explorer.web.routes.curate import require_resource_write
    from resource_explorer.workflows.analysis import filesystem_library_step_key

    registry = ProjectRegistry()
    fs = registry.get_filesystem(slug)
    if not fs:
        raise HTTPException(status_code=404, detail=f"FileSystem '{slug}' not found.")
    require_resource_write(registry, "filesystem", slug, "start_action")
    step = filesystem_library_step_key(analysis_id)
    if not step:
        raise HTTPException(status_code=400, detail=(
            f"Analysis '{analysis_id}' is not a file system analysis this route runs: it runs the Analytic Library "
            "steps over the walk's rows (the folder survey, walk extensions and data-file profiles have their own presses)."))
    missing = survey_step_host.library_step_missing_reason(step, "filesystem")
    if missing:
        raise HTTPException(status_code=400, detail=missing)
    activity_id = log_analysis_run(registry, "filesystem", slug, fs.display_name, "running",
                                   f"Running '{analysis_id}' on {slug}…", analysis_id, published=None)
    run_id = registry.enqueue_run("filesystem_analysis_run", {"slug": slug, "analysis_id": analysis_id,
                                                              **({"pressed_on": pressed_on[:40]} if pressed_on else {})},
                                  result_ref=activity_id, requested_by=requested_by())
    log.info("enqueued filesystem_analysis_run %s for %s/%s (activity %s)", run_id, slug, analysis_id, activity_id)
    return {"status": "started", "slug": slug, "analysis_id": analysis_id, "activity_id": activity_id, "run_id": run_id}


@router.get("/{slug}/derived")
def get_filesystem_derived(slug: str) -> dict:
    """The derived steps' stored results (Files build step 6) and the price line before the Survey press (F12).
    Registry-only. Also in the folder survey's own read (`price`, `derived`)."""
    from resource_explorer import file_reach
    from resource_explorer.surveyors.filesystem import walk_derived

    registry = ProjectRegistry()
    fs = registry.get_filesystem(slug)
    if not fs:
        raise HTTPException(status_code=404, detail=f"FileSystem '{slug}' not found.")
    fork = file_reach.fork_for(registry, fs)
    return {"slug": slug, "price": walk_derived.price(registry, slug, egeria_leads=fork.surveyor == file_reach.SURVEYOR_EGERIA),
            **walk_derived.derived_view(registry, slug)}


# ── Files build step 7: the narrative files read in full, chunked by section into RE's search index (F11's long form) ─

@router.get("/{slug}/narrative-docs")
def get_filesystem_narrative_docs(slug: str) -> dict:
    """The price (the narrative files the newest walk found, their bytes) and the newest run. Registry-only."""
    from resource_explorer.surveyors.filesystem import narrative_docs

    registry = ProjectRegistry()
    if not registry.filesystem_exists(slug):
        raise HTTPException(status_code=404, detail=f"FileSystem '{slug}' not found.")
    return narrative_docs.view(registry, slug)


@router.post("/{slug}/narrative-docs")
def run_filesystem_narrative_docs(slug: str) -> dict:
    """The press (consent is this press; the card showed the price before it): read the narrative files the newest walk
    found, whole and size-capped, chunk them by section and replace this file system's collection in Resource Explorer's
    search index. Opens no other file, writes nothing into the path, publishes nothing to Egeria. 409 when no walk has
    found narrative files, while a run for this file system is in flight, or when the mount cannot be reached."""
    from resource_explorer.surveyors.filesystem import narrative_docs
    from resource_explorer.web.routes.curate import require_resource_write

    registry = ProjectRegistry()
    fs = registry.get_filesystem(slug)
    if not fs:
        raise HTTPException(status_code=404, detail=f"FileSystem '{slug}' not found.")
    require_resource_write(registry, "filesystem", slug, "start_action")
    try:
        summary = narrative_docs.run_narrative_docs(fs, registry)
    except (narrative_docs.NothingToRead, narrative_docs.AlreadyRunning, narrative_docs.NotReachable) as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return {**summary, "view": narrative_docs.view(registry, slug)}


# ── Files build step 8: data_class_match over the file profiles (F7), proposals only ─────────────────────────────────

@router.get("/{slug}/class-match")
def get_filesystem_class_match(slug: str) -> dict:
    """The price (the newest profile run's columns and files) and the newest run. Registry-only."""
    from resource_explorer.surveyors.filesystem import file_class_match

    registry = ProjectRegistry()
    if not registry.filesystem_exists(slug):
        raise HTTPException(status_code=404, detail=f"FileSystem '{slug}' not found.")
    return file_class_match.view(registry, slug)


@router.post("/{slug}/class-match")
def run_filesystem_class_match(slug: str) -> dict:
    """The press (consent is this press; the card showed what it reads before it): match every column of the newest
    profile run against the data classes Egeria holds (read as the signed-in person, read-only), with values from the
    head of each file Resource Explorer profiled, and the file names against the personal-data words. Every match is a
    proposal; nothing is published or created in Egeria, nothing is written into the path. 409 when nothing is
    profiled, while a run for this file system is in flight, or when the mount cannot be reached."""
    from resource_explorer.surveyors.filesystem import file_class_match
    from resource_explorer.web.routes.curate import require_resource_write

    registry = ProjectRegistry()
    fs = registry.get_filesystem(slug)
    if not fs:
        raise HTTPException(status_code=404, detail=f"FileSystem '{slug}' not found.")
    require_resource_write(registry, "filesystem", slug, "start_action")
    try:
        summary = file_class_match.run_file_class_match(fs, registry)
    except (file_class_match.NothingToMatch, file_class_match.AlreadyRunning, file_class_match.NotReachable) as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return {"columns": summary["columns"], "established": summary["established"],
            "view": file_class_match.view(registry, slug)}


# ── Files build step 5: file_profiles (column profiles of the walk's data files, priced before the run) ──────────────

@router.get("/{slug}/file-profiles")
def get_filesystem_file_profiles(slug: str) -> dict:
    """The cost line (from the walk rows and this machine's last measured rate) and the newest profile run, every count
    from the stored rows. Registry-only."""
    from resource_explorer.surveyors.filesystem import file_profiles as fp

    registry = ProjectRegistry()
    if not registry.filesystem_exists(slug):
        raise HTTPException(status_code=404, detail=f"FileSystem '{slug}' not found.")
    return fp.profiles_view(registry, slug)


@router.post("/{slug}/file-profiles")
def run_filesystem_file_profiles(slug: str) -> dict:
    """The press (consent is this press; the card showed the cost line before it). Profiles every data file of the newest
    walk: a `.csv` on an Egeria-led file system goes to Egeria's CSV Survey Service, every other readable file is profiled
    here. Writes nothing into the path. 409 when there is no walk yet, and 409 while a run for this file system is in
    flight (server-side claim: a second press submits nothing)."""
    from resource_explorer.surveyors.filesystem import file_profiles as fp
    from resource_explorer.web.routes.curate import require_resource_write

    registry = ProjectRegistry()
    fs = registry.get_filesystem(slug)
    if not fs:
        raise HTTPException(status_code=404, detail=f"FileSystem '{slug}' not found.")
    require_resource_write(registry, "filesystem", slug, "start_action")
    from resource_explorer.egeria_clients import Caller

    try:
        summary = fp.run_file_profiles(fs, registry, port_factory=lambda: fp.make_port(identity=Caller()))
    except (fp.NothingToProfile, fp.AlreadyRunning, fp.ClaimLost) as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return {**summary, "view": fp.profiles_view(registry, slug)}


@router.post("/{slug}/file-profiles/read-egeria")
def read_filesystem_file_profiles_from_egeria(slug: str) -> dict:
    """Read Egeria's CSV reports back by GUID for the newest run's submitted files (read-only in Egeria)."""
    from resource_explorer.surveyors.filesystem import file_profiles as fp
    from resource_explorer.web.routes.curate import require_resource_write

    registry = ProjectRegistry()
    fs = registry.get_filesystem(slug)
    if not fs:
        raise HTTPException(status_code=404, detail=f"FileSystem '{slug}' not found.")
    require_resource_write(registry, "filesystem", slug, "start_action")
    from resource_explorer import native_survey_run as nsr
    from resource_explorer.egeria_clients import Caller

    with nsr.port_session(fp.make_port(identity=Caller())) as port:
        counts = fp.read_back_egeria(fs, registry, port)
    return {**counts, "view": fp.profiles_view(registry, slug)}


@router.post("/{slug}/file-profiles/publish")
def publish_filesystem_file_profiles(slug: str) -> dict:
    """Publish Resource Explorer's column profiles of the newest run, only the columns whose profile differs from the last
    run (until the profile-publish reshape lands). Egeria's own reports stay Egeria's. 409 when nothing is to publish, or when this run's publish is
    complete. A partial publish is resumed: the same report, only the missing annotations. One publish per file system
    at a time (a `file_profiles_publish:<slug>` claim): a second press while one runs is 409 and publishes nothing."""
    import os

    from resource_explorer.surveyors.filesystem import file_profiles as fp
    from resource_explorer.web.routes.curate import require_resource_write

    registry = ProjectRegistry()
    fs = registry.get_filesystem(slug)
    if not fs:
        raise HTTPException(status_code=404, detail=f"FileSystem '{slug}' not found.")
    require_resource_write(registry, "filesystem", slug, "publish")
    claim_key = f"{fp.PUBLISH_STEP_KEY}:{registry._normalize_slug(slug)}"
    holder = f"{fp.machine()}:{os.getpid()}:{fp._now()}"
    if not registry.take_claim(claim_key, holder, stale_after_seconds=fp.CLAIM_STALE_SECONDS):
        raise HTTPException(status_code=409, detail=fp.PUBLISH_ALREADY_RUNNING_WORDS.format(slug=slug))
    try:
        return _publish_file_profiles_claimed(registry, fs, slug)
    finally:
        registry.release_claim(claim_key, holder)


def _publish_file_profiles_claimed(registry: ProjectRegistry, fs, slug: str) -> dict:
    """The publish itself, run only while this press holds the publish claim."""
    from resource_explorer.surveyors.filesystem import file_profiles as fp

    runs = registry.list_filesystem_file_column_runs(slug)
    if not runs:
        raise HTTPException(status_code=409, detail="No data-file profiles to publish. Run them first.")
    run_id = runs[0]
    if (fp.latest_publish_proof(registry, slug, run_id) or {}).get("complete"):
        raise HTTPException(status_code=409, detail="These profiles are already published to Egeria. Run them again to "
                            "publish newer ones.")
    try:
        rows = fp.changed_rows(registry, slug, run_id)
    except Exception as exc:  # noqa: BLE001 - logged; without the reset record the diff baseline is unknown
        raise HTTPException(status_code=503, detail="Could not check whether Egeria was reset, so nothing was "
                            f"published · the record of Egeria resets could not be read: {exc}")
    if not rows:
        raise HTTPException(status_code=409, detail="No profile Resource Explorer measured differs from the last run · "
                            "nothing new to publish")
    from resource_explorer.egeria_clients import Caller
    from resource_explorer.egeria_clients import refusal as egeria_refusal
    from resource_explorer.surveyors.filesystem.egeria_filesystem_surveyor import EgeriaFileSystemSurveyor

    canonical = (fs.canonical_mount_point or fs.local_mount_point).rstrip("/")
    surveyor = EgeriaFileSystemSurveyor(platform_url=fs.egeria_url or None, view_server=fs.egeria_server or None,
                                        identity=Caller())
    try:
        proof = surveyor.publish_file_profiles(fs, run_id, fp.publish_specs(rows, canonical), registry)
    except PermissionError:
        raise
    except Exception as exc:  # noqa: BLE001 -- the profiles stand; the publish failure is said
        refused = egeria_refusal(exc)
        if refused is not None:
            raise refused from exc
        log.exception("Publishing the file profiles failed for %s", slug)
        raise HTTPException(status_code=502, detail=f"not published · {exc}")
    return {**proof, "columns": len(rows), "view": fp.profiles_view(registry, slug)}
