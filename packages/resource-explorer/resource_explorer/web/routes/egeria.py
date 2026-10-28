"""Egeria integration endpoints — asset registration, survey history, publish."""
from __future__ import annotations

import asyncio
import json
import os

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from resource_explorer.web.routes._annotation_items import build_annotation_items
from resource_explorer.auth import get_current_user
from resource_explorer.registry import ProjectRegistry
from resource_explorer.resource_types import SURVEYED_RESOURCE_TYPES
from resource_explorer.config import egeria_platform_url, egeria_view_server  # noqa: E402,F401

router = APIRouter()


# ── identity ───────────────────────────────────────────────────────────────────
# RE has no per-user auth today (see resource_explorer/config.py::EgeriaConfig) —
# every request connects to Egeria as the same fixed service-account identity
# from config/.env, not a logged-in individual. This endpoint just surfaces
# that configured identity read-only for the header indicator; it is
# deliberately NOT a login mechanism. Real per-user login was raised and
# explicitly deferred as its own, larger piece of scope — see the Trellis
# design docs before building an actual auth flow against this.
@router.get("/admin-status")
def admin_status(request: Request) -> dict:
    """`admin`: this request carries a valid admin credential. `configured`: this deployment has any admin at all.
    `open`: no admin is configured AND RE_ADMIN_OPEN_WHEN_UNCONFIGURED is set, so any signed-in user may act.

    The Next controls draw "admin only" when not `admin` and either `configured` or not `open`, the same decision
    admin_auth.require_admin makes (owner ruling 2026-10-14: unconfigured fails closed unless the dev flag is set).
    """
    from resource_explorer.config import get_config
    from resource_explorer.web import admin_auth

    cfg = get_config().feedback
    configured = bool(admin_auth.admin_configured())
    return {"admin": bool(admin_auth.is_admin_request(request, cfg)),
            "configured": configured,
            "open": (not configured) and bool(cfg.admin_open_when_unconfigured)}


_BUILD_SHA: str | None = None
_BUILD_SHA_READ = False


def build_sha() -> str | None:
    """The git commit this process was started from, or None when it cannot be read.

    `RE_BUILD_SHA` wins when set (an image build that has no .git directory). Otherwise `git rev-parse HEAD` in the
    package's own directory, read once per process: the serving worktree is detached at the tip it was started
    from, and a restart is what changes it. None is "not read", never an empty string a caller could print as a sha.
    """
    global _BUILD_SHA, _BUILD_SHA_READ
    if _BUILD_SHA_READ:
        return _BUILD_SHA
    import os
    import subprocess
    from pathlib import Path

    sha = (os.environ.get("RE_BUILD_SHA") or "").strip()
    if not sha:
        try:
            out = subprocess.run(
                ["git", "rev-parse", "HEAD"], cwd=Path(__file__).resolve().parent,
                capture_output=True, text=True, timeout=5, check=True,
            )
            sha = out.stdout.strip()
        except Exception:  # noqa: BLE001 - no git, no repo, a timeout: all mean "not read"
            sha = ""
    _BUILD_SHA = sha or None
    _BUILD_SHA_READ = True
    return _BUILD_SHA


@router.get("/whoami")
def whoami() -> dict:
    """The configured Egeria connection, plus `last_egeria_call`: what THIS process recorded for
    the signed-in user's most recent Egeria client — `{"as": "you" | "service account
    (background)", purpose, at}` — or None when nothing was recorded here (a restart clears it).
    Recorded by the factory as each client is handed out, never inferred (Brief I).

    `daemon`: who background and queued work runs as (Brief L) — `{user_id, source}` from the
    daemon credential seam (`egeria_clients._daemon_credential`), not inferred.

    `identity_in_egeria` (7h): this process's last startup check of RE's ITProfile/UserIdentity
    (`{status: present|created|could not check|not checked, reason?, words}`), and
    `owner_lookup`: the signed-in user's outstanding failed person-profile lookup here, or None
    (always None with no signed-in user: never another user's failure)."""
    from resource_explorer.config import get_config
    from resource_explorer.egeria_actors import actor_lookup_status
    from resource_explorer.egeria_clients import daemon_identity_status, last_egeria_identity
    from resource_explorer.egeria_identity import caller_user_id

    cfg = get_config().egeria
    return {
        "user_id": cfg.user_id,
        "view_server": cfg.view_server,
        "platform_url": cfg.platform_url,
        "build_sha": build_sha(),
        "last_egeria_call": last_egeria_identity(caller_user_id()),
        "daemon": daemon_identity_status(),
        "identity_in_egeria": _identity_in_egeria(),
        "owner_lookup": actor_lookup_status(caller_user_id()),
    }


def _identity_in_egeria() -> dict:
    from resource_explorer.egeria_actors import identity_words, re_identity_status

    state = re_identity_status()
    return {"status": state.get("status"), "reason": state.get("reason") or "",
            "words": identity_words(state), "at": state.get("at") or ""}


# ── response models ───────────────────────────────────────────────────────────

class SurveyRow(BaseModel):
    surveyed_at: str
    egeria_report_guid: str
    published_at: str
    annotation_count: int | None


class EgeriaStatus(BaseModel):
    slug: str
    asset_guid: str | None
    is_registered: bool
    platform_url: str
    surveys: list[SurveyRow]


class PublishResult(BaseModel):
    status: str                    # "ok" | "error"
    report_guid: str | None = None
    annotation_count: int | None = None
    surveyed_at: str | None = None
    error: str | None = None
    stage: str | None = None       # "survey" | "publish"
    #: The publish word, from the run's proof rows (registry.publish_proofs), never from this route returning:
    #: "read_back" ("published · read back"), "queued" ("queued for publish"), "failed" ("publish failed",
    #: `publish_words` carries Egeria's), "unverified" ("could not verify"), or "nothing" ("nothing to publish").
    publish_state: str | None = None
    publish_word: str | None = None
    publish_words: str | None = None


class AnnotationItem(BaseModel):
    guid: str
    annotation_type: str
    summary: str
    confidence: int | None
    analysis_step: str
    explanation: str
    expression: str
    json_properties: dict
    subtype_data: dict


class AnnotationsResult(BaseModel):
    slug: str
    surveyed_at: str
    report_guid: str
    annotations: list[AnnotationItem]


class EgeriaSurveyReportRow(BaseModel):
    """A SurveyReport as it actually exists in Egeria right now (live read via
    the real ReportSubject relationship) — regardless of which engine (RE or
    Egeria's own native survey) produced it. See databases.py's identical
    model and resource_explorer/surveyors/egeria_survey_reader.py."""
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
    #: (Phase 1 slice 10). Defaulted, so existing construction keeps working;
    #: declared, because pydantic v2's `extra='ignore'` means an undeclared
    #: field from the reader vanishes here with no error at all — the reason
    #: this model is one of three that had to change in lockstep. Empty means
    #: "no contentStatus stated", not "confirmed".
    content_status: str = ""


class FileTypeSummary(BaseModel):
    label: str
    count: int
    source: str
    extensions: dict = {}     # details_json for the "Other" bucket


class DependencySummary(BaseModel):
    ecosystem: str
    count: int
    # None = direct-ness was not determined for this ecosystem (absence is not 0). Only go.mod records the
    # transitive marker ("// indirect"); no other parser says whether a row is direct, and dep_type is scope.
    direct: int | None = None


class DataProfileSummary(BaseModel):
    file_path: str
    format: str
    row_count: int | None = None
    col_count: int | None = None
    columns: list[dict] = []      # [{name, dtype, null_pct}]
    null_summary: str = ""
    file_size_bytes: int = 0
    profiled_at: str | None = None


#: Ecosystems whose parser marks transitive rows (dep_type "indirect"), so "direct" can be derived there.
_DIRECT_DETERMINED = frozenset({"go"})


def summarise_dependencies(deps: list[dict]) -> list[dict]:
    """Group dependency rows by ecosystem. `direct` is count minus "indirect" where the ecosystem's parser
    records that marker; elsewhere it is None (not determined), never 0."""
    dep_map: dict[str, dict] = {}
    for d in deps:
        eco = d.get("ecosystem") or "unknown"
        row = dep_map.setdefault(eco, {"ecosystem": eco, "count": 0, "direct": 0 if eco in _DIRECT_DETERMINED else None})
        row["count"] += 1
        if row["direct"] is not None and d.get("dep_type") != "indirect":
            row["direct"] += 1
    return sorted(dep_map.values(), key=lambda x: -x["count"])



class SurveyReportData(BaseModel):
    slug: str
    display_name: str
    github_url: str
    latest_survey: SurveyRow | None
    file_types: list[FileTypeSummary]
    total_files: int
    health: dict
    primary_language: str
    dependencies: list[DependencySummary]
    has_egeria_annotations: bool
    data_profiles: list[DataProfileSummary] = []
    local_surveyed_at: str = ""   # timestamp of the most recent local survey run
    # False when no project_stats row exists: `health` then carries zeros that were never measured, and
    # the Next report view draws them as "not read" (PI-053).
    health_read: bool = False


class CatalogItemResult(BaseModel):
    label: str
    guid: str | None = None
    status: str          # "created" | "error"
    error: str | None = None


class CatalogResult(BaseModel):
    status: str          # "ok" | "partial" | "error"
    cataloged: list[CatalogItemResult]


# ── helpers ───────────────────────────────────────────────────────────────────

def _platform_url() -> str:
    return egeria_platform_url()


def _get_project_or_404(slug: str):
    registry = ProjectRegistry()
    project = registry.get(slug)
    if not project:
        raise HTTPException(status_code=404, detail=f"Project '{slug}' not found")
    return project, registry


# ── routes ────────────────────────────────────────────────────────────────────

class DataClassRule(BaseModel):
    name: str
    display_name: str
    description: str
    keywords: list[str]
    source: str
    #: Which keyword source this read used, the same words on every rule ("keywords: Egeria, N
    #: sets" / "keywords: built-in list · Egeria had none" / "... · could not read Egeria"), from
    #: `egeria_reference_catalog.KeywordLoad.label`.
    keyword_source: str = ""


@router.get("/private-zone")
async def private_zone_state() -> dict:
    """Whether private investigations can publish, and if not, exactly why.

    Exposed rather than left in a log line because the failure is silent from
    the user's side: RE keeps showing personal investigations as private
    (Phase 3's filter is local and works regardless), while their surveys quietly
    refuse to reach Egeria. Someone has to be able to see the reason without
    grepping the worker log.

    `enforced: false` is not always a problem to fix here — on a stock
    freshstart no human account holds the platform-operator rights this needs,
    so the remedy is a deployment grant. The `remedy` field says which.
    """
    from resource_explorer.egeria_identity import (
        ensure_private_zone_exists, private_zone_status,
    )

    status = private_zone_status()
    if status.get("status") == "unknown":
        # Nothing has asked in this process yet. Ask now rather than reporting
        # "unknown" to a person who opened this page precisely to find out —
        # `ensure_private_zone_exists` is idempotent and never raises.
        ensure_private_zone_exists()
        status = private_zone_status()
    if any(e.get("verdict") == "unknown" for e in status.get("zone_controls") or []):
        # The draft/publish zones' controls, likewise asked now rather than shown as "not checked yet".
        from resource_explorer.egeria_identity import check_zone_controls

        check_zone_controls()
        status = private_zone_status()
    return status


#: Per data class: the same bound the survey-definition reader puts on this pyegeria call.
_DATACLASS_GUID_TIMEOUT_SECONDS = 15


@router.get("/rules/dataclasses", response_model=list[DataClassRule])
async def get_dataclass_rules() -> list[DataClassRule]:
    """Fetch the active PII Data Classes and their corresponding keyword matching rules.

    Queries Egeria's Valid Value sets if online; falls back to local rules if offline. Every rule
    carries `keyword_source`, the one label for which source this read used.
    """
    # The built-in rules are the data-class seed (configdata/data_classes.yaml), the same entries
    # the start-up bootstrap creates in Egeria.
    from resource_explorer.surveyors.database.bootstrap_data_classes import current_seed

    fallback_rules = [
        {"name": dc["name"], "display_name": dc["displayName"], "description": dc["description"],
         "keywords": list(dc["dataPatterns"]), "source": "Local Fallback"}
        for dc in current_seed()
    ]

    from resource_explorer.surveyors.database.egeria_reference_catalog import (
        KEYWORDS_NOT_CONFIGURED, KEYWORDS_UNREADABLE, KeywordLoad,
    )

    def _built_in(load: KeywordLoad) -> list[DataClassRule]:
        return [DataClassRule(**r, keyword_source=load.label) for r in fallback_rules]

    platform_url = egeria_platform_url()
    if not platform_url:
        return _built_in(KeywordLoad(KEYWORDS_NOT_CONFIGURED))

    try:
        from pyegeria.omvs.reference_data import ReferenceDataManager
        from pyegeria.omvs.data_designer import DataDesigner

        from resource_explorer.concurrency import run_sync
        from resource_explorer.egeria_clients import Caller, NoCallerIdentity, egeria_client

        view_server = egeria_view_server()
        # The signed-in reader (Brief I), resolved here in the request, not the env's user. With
        # nobody signed in this read answers the LOCAL rules, as it always did (round 5): it is a
        # read with a documented fallback, not an Egeria action someone is refused.
        try:
            reader = Caller()
        except NoCallerIdentity:
            return _built_in(KeywordLoad(KEYWORDS_UNREADABLE, reason="not signed in"))
        clients = egeria_client(reader, purpose="data class rules",
                                view_server=view_server, platform_url=platform_url)

        def _guid_for(qname: str):
            """`get_guid_for_name` is the known hang site: bounded through the shared pool, on a
            client built in the pool thread itself (pyegeria binds a client to its thread's loop)."""
            def _lookup():
                designer = egeria_client(reader, purpose="data class rules", view_server=view_server,
                                         platform_url=platform_url, shared=False).of(DataDesigner)
                try:
                    return designer.get_guid_for_name(qname)
                finally:                       # its own client, used once: closed after use (round 6)
                    close = getattr(designer, "close_session", None)
                    if callable(close):
                        try:
                            close()
                        except Exception:  # noqa: BLE001 - closing is tidy-up, never the answer
                            pass
            return run_sync(_lookup, timeout=_DATACLASS_GUID_TIMEOUT_SECONDS)

        def _fetch():
            from resource_explorer.surveyors.database.egeria_reference_catalog import (
                load_data_class_keywords,
            )
            from resource_explorer.surveyors.survey_definition_reader import _as_guid

            ref_manager = clients.of(ReferenceDataManager)
            # A DataClass Egeria does not hold has no keywords there. `_as_guid` rejects pyegeria's
            # truthy "No elements found" miss sentinel; a timed-out lookup (`TimeoutError`) stops
            # every later one inside the loader (round 6).
            load = load_data_class_keywords(
                ref_manager, [dc["name"] for dc in fallback_rules],
                data_class_exists=lambda name: bool(_as_guid(_guid_for(f"DataClass::{name}"))),
            )
            return [
                DataClassRule(**{**dc, "keywords": load.by_class.get(dc["name"]) or dc["keywords"],
                                 "source": "Egeria (Active)" if dc["name"] in load.by_class else "Local Fallback",
                                 "keyword_source": load.label})
                for dc in fallback_rules
            ]

        return await asyncio.to_thread(_fetch)
    except PermissionError:
        raise      # no caller / expired sign-in: 401, never the local fallback rules
    except Exception as exc:  # noqa: BLE001 - the built-in rules, labelled as "could not read"
        return _built_in(KeywordLoad(KEYWORDS_UNREADABLE, reason=str(exc)[:200]))


@router.get("/{slug}/status", response_model=EgeriaStatus)
async def egeria_status(slug: str) -> EgeriaStatus:
    """Return Egeria registration status and local survey history for a project.

    Reads from the local SQLite registry only — no Egeria connection needed.
    """
    project, registry = _get_project_or_404(slug)
    asset_guid = registry.get_egeria_asset_guid(slug)
    surveys_raw = registry.get_egeria_surveys(slug)
    surveys = [
        SurveyRow(
            surveyed_at=r["surveyed_at"],
            egeria_report_guid=r["egeria_report_guid"],
            published_at=r["published_at"],
            annotation_count=r.get("annotation_count"),
        )
        for r in surveys_raw
    ]
    return EgeriaStatus(
        slug=slug,
        asset_guid=asset_guid,
        is_registered=asset_guid is not None,
        platform_url=_platform_url(),
        surveys=surveys,
    )


@router.get("/{slug}/annotations", response_model=AnnotationsResult)
async def get_annotations(slug: str, surveyed_at: str, report_guid: str = "") -> AnnotationsResult:
    """Fetch all annotations for a specific survey run from Egeria.

    Requires a live Egeria connection (EGERIA_PLATFORM_URL env var).
    `surveyed_at` is the ISO timestamp used as part of the annotation qualifiedName.
    """
    project, registry = _get_project_or_404(slug)

    from resource_explorer.surveyors.egeria_reader import EgeriaReader, EgeriaReaderError
    reader = EgeriaReader(registry=registry)
    try:
        # EgeriaReader calls pyegeria sync wrappers — run in thread to avoid event loop conflict
        raw_annotations = await asyncio.to_thread(reader.get_annotations, slug, surveyed_at)
    except EgeriaReaderError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Egeria query failed: {exc}")

    annotations = [
        AnnotationItem(
            guid=a.get("guid", ""),
            annotation_type=a.get("annotation_type", ""),
            summary=a.get("summary", ""),
            confidence=a.get("confidence"),
            analysis_step=a.get("analysis_step", ""),
            explanation=a.get("explanation", ""),
            expression=a.get("expression", ""),
            json_properties=a.get("json_properties", {}),
            subtype_data=a.get("subtype_data", {}),
        )
        for a in raw_annotations
    ]

    return AnnotationsResult(
        slug=slug,
        surveyed_at=surveyed_at,
        report_guid=report_guid,
        annotations=annotations,
    )


@router.get("/{slug}/egeria-surveys", response_model=list[EgeriaSurveyReportRow])
async def get_repo_egeria_surveys(slug: str) -> list[EgeriaSurveyReportRow]:
    """List SurveyReports that actually exist in Egeria right now for this repo
    (a live read via the real ReportSubject relationship, not the older
    qualifiedName-guessing /annotations path above)."""
    from resource_explorer.surveyors.egeria_publisher import EgeriaConnectionError, EgeriaPublisher

    project, registry = _get_project_or_404(slug)
    if not project.egeria_asset_guid:
        return []  # not yet cataloged in Egeria — nothing to walk from

    publisher = EgeriaPublisher(registry=registry)
    try:
        reports = await asyncio.to_thread(publisher.get_survey_reports_by_guid, project.egeria_asset_guid)
    except EgeriaConnectionError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    return [EgeriaSurveyReportRow(**r) for r in reports]


@router.get("/{slug}/egeria-surveys/{report_guid}/annotations", response_model=list[EgeriaAnnotationItem])
async def get_repo_egeria_annotations(slug: str, report_guid: str) -> list[EgeriaAnnotationItem]:
    """Fetch all annotations for a specific live Egeria SurveyReport, by the
    report's own GUID — works regardless of which engine produced the report."""
    from resource_explorer.surveyors.egeria_publisher import EgeriaConnectionError, EgeriaPublisher

    _project, registry = _get_project_or_404(slug)

    publisher = EgeriaPublisher(registry=registry)
    try:
        annotations = await asyncio.to_thread(publisher.get_annotations_by_report_guid, report_guid)
    except EgeriaConnectionError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    return build_annotation_items(EgeriaAnnotationItem, annotations)


class ResetResult(BaseModel):
    status: str                  # "ok"
    slug: str
    asset_guid_cleared: bool
    surveys_deleted: int


class SurveyOnlyResult(BaseModel):
    status: str                      # "ok" | "error"
    annotation_count: int | None = None
    surveyed_at: str | None = None
    errors: list[str] = []
    error: str | None = None


# ── Egeria linkage divergence (docs/Backlog.md; egeria_linkage.py) ───────────
# Surfaced as its own routes rather than folded into /{slug}/reset because reset
# is repo-only and, crucially, undiscoverable: it is the right action but nothing
# ever tells you to take it. These make the condition visible and name the
# choices. The /{slug}/reset route stays as-is — same clearing, still valid.

class StaleLinkage(BaseModel):
    entity_type: str
    entity_slug: str
    stale_guid: str = ""
    detected_at: str = ""
    detail: str = ""


class LinkageResolveRequest(BaseModel):
    # republish — re-publish what RE already holds locally (fast; right when only
    #             Egeria was reset and the resource itself has not changed)
    # resurvey  — re-survey from scratch, then publish (right when the resource
    #             may have changed too, or the local data is old enough to doubt)
    # discard   — clear the broken link and stop; RE's local survey data is kept
    action: str


class LinkageResolveResult(BaseModel):
    status: str
    entity_type: str
    entity_slug: str
    action: str
    asset_guid_cleared: bool = False
    surveys_deleted: int = 0
    next_step: str = ""


@router.get("/linkage/stale", response_model=list[StaleLinkage])
def list_stale_linkages() -> list[StaleLinkage]:
    """Entities whose cached Egeria GUID was found not to exist.

    A row appears only after a real failure — nothing here probes Egeria. The
    reactive-only choice is deliberate for now: a proactive sweep would have to
    decide how often to re-check every cataloged entity, and the failure it
    guards against is rare and already loud once this exists.
    """
    return [StaleLinkage(**{k: v for k, v in row.items() if k != "status"})
            for row in ProjectRegistry().list_stale_egeria_linkages()]


class BulkTarget(BaseModel):
    entity_type: str = "repo"
    slug: str


class BulkRequest(BaseModel):
    """An explicit list of what to act on, never a server-side "everything stale".

    A sweep result and a work list are different things: `egeria-recheck` reports
    what it found at that moment, and deriving the target list here would act on
    something nobody read. The UI sends the rows it displayed, so what is acted on
    is always what was seen.
    """
    targets: list[BulkTarget]
    dry_run: bool = True   # defaults to the safe direction; the caller opts out


class BulkResolveRequest(BulkRequest):
    action: str = "republish"


@router.post("/linkage/resolve-all")
async def resolve_all_linkages(req: BulkResolveRequest, request: Request) -> dict:
    """Apply one resolution to many diverged resources.

    Republish is the usual choice after an Egeria reset — it re-publishes what RE
    already holds rather than re-scanning. Runs in a thread: a republish is a
    survey plus a publish per resource, so twenty of them is minutes, not
    milliseconds.
    """
    from resource_explorer.bulk_ops import resolve_all
    from resource_explorer.web import admin_auth

    admin_auth.require_admin(request)     # bulk republish writes to Egeria: admin only (one resource stays open)
    registry = ProjectRegistry()
    targets = [t.model_dump() for t in req.targets]
    try:
        result = await asyncio.to_thread(
            resolve_all, registry, targets, req.action, dry_run=req.dry_run)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return result.as_dict()


@router.post("/linkage/delete-local")
async def delete_resources_locally(req: BulkRequest) -> dict:
    """Remove resources from RE entirely — registry row and pgvector collections.

    Destructive, and the collections are the expensive part to reverse:
    re-registering means re-ingesting. Egeria is left alone. Defaults to a dry
    run; the caller has to ask for the real thing.
    """
    from resource_explorer.bulk_ops import delete_local

    result = await asyncio.to_thread(
        delete_local, ProjectRegistry(), [t.model_dump() for t in req.targets],
        dry_run=req.dry_run)
    return result.as_dict()


@router.post("/linkage/delete-in-egeria")
async def delete_resources_in_egeria(req: BulkRequest) -> dict:
    """Delete the Egeria asset RE recorded for each resource.

    Only ever the GUID RE itself stored — never resolved by name, never
    cascading, and a resource without a cached GUID is skipped rather than
    searched for. Egeria is the catalog of record and RE otherwise only adds to
    it; this is the one path that can remove governance history, so it is
    deliberately the narrowest of the three.

    RE's own data is untouched: registry rows, survey results and collections all
    survive.
    """
    from resource_explorer.bulk_ops import delete_in_egeria

    result = await asyncio.to_thread(
        delete_in_egeria, ProjectRegistry(), [t.model_dump() for t in req.targets],
        dry_run=req.dry_run)
    return result.as_dict()


@router.post("/linkage/{entity_type}/{slug}/resolve", response_model=LinkageResolveResult)
async def resolve_stale_linkage(
    entity_type: str, slug: str, req: LinkageResolveRequest,
) -> LinkageResolveResult:
    """Act on a detected divergence.

    All three actions clear the unusable GUID and the divergence record — that is
    what unblocks the resource, and it is common to every choice. They differ in
    what happens next, which is why the caller has to say which one they mean
    rather than this picking for them.

    Only `republish`/`resurvey` for a repo actually run the follow-up work here.
    For databases and filesystems the link is cleared and the caller is told which
    existing action to run: re-publishing those from cached local data needs
    per-type orchestration (a database publish reconstructs schema_info and fires
    Egeria's native survey), and there is no registered database or filesystem in
    this deployment to verify such a path against. Clearing is the part that is
    genuinely generic; pretending the rest was would be worse than saying so.
    """
    if entity_type not in SURVEYED_RESOURCE_TYPES:
        raise HTTPException(status_code=422, detail=f"Unknown entity type '{entity_type}'")
    if req.action not in ("republish", "resurvey", "discard"):
        raise HTTPException(
            status_code=422,
            detail=f"Unknown action '{req.action}'; expected republish, resurvey or discard")

    registry = ProjectRegistry()
    if not registry.get_egeria_linkage(entity_type, slug):
        raise HTTPException(
            status_code=404,
            detail=f"No stale Egeria linkage recorded for {entity_type} '{slug}'")
    # Brief Z2: the link is stale (Egeria no longer holds that GUID), so the element is checked as not in
    # Egeria: a republish creates it in the zones RE gives it; discard only clears RE's record (a decision).
    await _authorize(registry, entity_type, slug,
                     "publish" if entity_type == "repo" and req.action != "discard" else "decision",
                     element_guid="")

    cleared, deleted = False, 0
    if entity_type == "repo":
        result = registry.clear_egeria_registration(slug)
        cleared, deleted = result["asset_guid_cleared"], result["surveys_deleted"]
    elif entity_type == "database":
        registry.set_database_egeria_guid(slug, "")
        cleared = True
    else:
        registry.set_filesystem_egeria_guid(slug, "")
        cleared = True

    registry.clear_egeria_linkage_status(entity_type, slug)

    next_step = ""
    if req.action == "discard":
        # Say what was actually removed. For a repo, clearing the registration
        # also drops project_egeria_surveys — the record of past publishes and
        # their report GUIDs. Those GUIDs point into the same repository that no
        # longer has the asset, so keeping them would preserve nothing but stale
        # pointers; the publish history itself remains in the activity log. But
        # "untouched", which this said before, was not true of them, and the one
        # action a user might fear is the wrong place to be imprecise.
        removed = (f" {deleted} publish record(s) removed;" if deleted else "")
        next_step = (f"Link cleared.{removed} survey results and annotations are "
                     "untouched, and past publishes remain in the activity log. "
                     "Nothing will be written to Egeria until you publish again.")
    elif entity_type == "repo":
        from resource_explorer.surveyors.egeria_publisher import EgeriaPublisher
        from resource_explorer.surveyors.survey_orchestrator import SurveyOrchestrator

        try:
            if req.action == "resurvey":
                survey = await asyncio.to_thread(
                    lambda: SurveyOrchestrator(registry).run(slug))
            else:
                # republish: re-derive from what RE already holds locally. The
                # orchestrator reads the stored tables rather than re-fetching,
                # so this is the "fast, no re-scan" path the choice promises.
                survey = await asyncio.to_thread(
                    lambda: SurveyOrchestrator(registry).run(slug, max_fetch_cost="none"))
            # An explicit re-create waits for Egeria (RUNS_PUBLISH_INLINE governs a run's auto-publish only).
            guid = await asyncio.to_thread(
                lambda: EgeriaPublisher(registry=registry).publish(survey, defer_drain=False))
            next_step = f"Re-created in Egeria as SurveyReport {guid}."
        except Exception as exc:
            # The link is already cleared, so this is recoverable by retrying the
            # ordinary publish — say that rather than leaving a bare stack trace.
            raise HTTPException(
                status_code=502,
                detail=(f"Link cleared, but {req.action} failed: {exc}. The stale GUID is "
                        "gone, so an ordinary publish should now succeed.")) from exc
    else:
        verb = "publish" if req.action == "republish" else "survey and publish"
        next_step = (f"Link cleared. Run {verb} for this {entity_type} from its own panel "
                     "to re-create it in Egeria.")

    return LinkageResolveResult(
        status="ok", entity_type=entity_type, entity_slug=slug, action=req.action,
        asset_guid_cleared=cleared, surveys_deleted=deleted, next_step=next_step,
    )


class RecheckDetail(BaseModel):
    entity_type: str
    slug: str
    guid: str
    result: str    # "ok" | "stale" | "error"
    detail: str = ""


class RecheckResult(BaseModel):
    checked: int
    ok: int
    stale: int
    errors: int
    skipped: int
    details: list[RecheckDetail]


@router.post("/linkage/recheck", response_model=RecheckResult)
async def recheck_linkages(entity_type: list[str] | None = None) -> RecheckResult:
    """Proactively probe Egeria for every cached GUID instead of waiting for
    the reactive path (guard_linkage) to trip over one on next use.

    This is the sweep behind `resource-explorer egeria-recheck` — see
    egeria_linkage.recheck_all_linkages for why it both records new
    divergences and clears ones that have since resolved. Runs in a thread:
    it makes one network call per cataloged resource (~20 in a typical
    deployment) and pyegeria's sync wrappers are blocking.
    """
    from resource_explorer.egeria_linkage import recheck_all_linkages

    registry = ProjectRegistry()
    result = await asyncio.to_thread(
        recheck_all_linkages, registry, entity_types=entity_type)
    return RecheckResult(**result)


@router.post("/{slug}/reset", response_model=ResetResult)
async def reset_egeria(slug: str) -> ResetResult:
    """Clear the cached Egeria asset GUID and all published survey records for a project.

    Use this after resetting the Egeria database so the next --publish re-registers
    the project from scratch instead of referencing stale GUIDs.
    """
    project, registry = _get_project_or_404(slug)
    await _authorize(registry, "repo", slug, "decision")        # Brief Z2: RE's record of the element
    result = registry.clear_egeria_registration(slug)
    return ResetResult(
        status="ok",
        slug=slug,
        asset_guid_cleared=result["asset_guid_cleared"],
        surveys_deleted=result["surveys_deleted"],
    )


@router.post("/{slug}/survey", response_model=SurveyOnlyResult)
async def run_survey(slug: str) -> SurveyOnlyResult:
    """Run a survey for a project and persist results to SQLite.

    Does not publish to Egeria. Updates project_file_type_counts so the
    Survey Report tab refreshes automatically after this call completes.
    """
    project, registry = _get_project_or_404(slug)

    try:
        from resource_explorer.surveyors.survey_orchestrator import SurveyOrchestrator

        def _run():
            return SurveyOrchestrator(registry=registry).run(slug)

        result = await asyncio.to_thread(_run)
    except Exception as exc:
        return SurveyOnlyResult(status="error", error=str(exc))

    return SurveyOnlyResult(
        status="ok",
        annotation_count=len(result.annotations),
        surveyed_at=result.surveyed_at.isoformat(),
        errors=result.errors,
    )


class PublishRequest(BaseModel):
    zone_names: list[str] = []
    # None (default) = full survey, byte-for-byte the existing behavior.
    # A list scopes the survey (and therefore the published SurveyReport) to
    # just those SurveyOrchestrator step keys — lets each phase (Scouting,
    # coarse profiling, etc.) publish only what it actually knows, as its own
    # SurveyReport linked to the same asset, rather than one all-or-nothing
    # publish. Same step-key vocabulary as REPO_ANALYSIS_STEP_MAP.
    steps: list[str] | None = None
    # Optional — set when this publish was triggered from a Survey Definition
    # candidate's own "☁ Publish" button (renderSurveyPanel), so the resulting
    # activity_log entry can be attributed back to that candidate for the
    # last-published badge (registry.get_survey_definition_last_activity).
    # Blank for every other publish surface (Curate, Profile, etc.) — those
    # just don't get a last-published badge anywhere, which is correct: they
    # aren't tied to one Survey Definition.
    survey_definition_ref: str = ""


@router.post("/{slug}/publish", response_model=PublishResult)
async def publish_survey(slug: str, req: PublishRequest | None = None) -> PublishResult:
    """Run a survey (full, or scoped to req.steps) then publish the result to Egeria.

    The survey and publish steps both call blocking synchronous code (pyegeria's
    sync wrappers use loop.run_until_complete internally).  Running them inside
    FastAPI's async event loop would cause "Can't patch loop" errors, so both
    steps are offloaded to a thread pool via asyncio.to_thread().
    """
    project, registry = _get_project_or_404(slug)
    # Brief Z round 2: the repository's own element, every operation a publish performs.
    from resource_explorer.web.routes.curate import authorize_resource_write
    await authorize_resource_write(registry, "repo", slug, publish=True)

    # ── Egeria Project context gate (Part 5) ──────────────────────────────────
    # The first real Egeria write for a resource is exactly this route — every
    # repo publish surface (Scouting's registration-only publish, Assessment/
    # Analysis per-card publish, Profile publish, Curate's full publish) funnels
    # through here with different `steps`. Gating here once, rather than in
    # each frontend button handler, means a future publish surface can't
    # forget the check. status "unset" (never decided, including no row at
    # all) and "unbound" (the project it named is gone after an Egeria reset) block; every other status
    # (personal/linked/deferred/declined — all deliberate answers) proceeds.
    # The shared rule (repo_publish.resolve_project_context): a decided context proceeds; otherwise an
    # investigation bound to an Egeria Project answers (and the answer is WRITTEN, with the investigation
    # that supplied it); otherwise 428.
    from resource_explorer import repo_publish as _rp
    context = _rp.resolve_project_context(registry, slug)
    if context is None:
        return JSONResponse(
            status_code=428,
            content={
                "detail": "egeria_project_context_required",
                "entity_type": "repo",
                "entity_slug": slug,
            },
        )

    steps = req.steps if req else None

    # ── Step 1: survey (blocking — run in thread) ─────────────────────────────
    try:
        from resource_explorer.surveyors.survey_orchestrator import SurveyOrchestrator

        def _run_survey():
            return SurveyOrchestrator(registry=registry).run(slug, steps=steps)

        result = await asyncio.to_thread(_run_survey)
    except Exception as exc:
        return PublishResult(
            status="error",
            error=f"Survey failed: {exc}",
            stage="survey",
        )

    # ── Step 2: publish (blocking — run in thread) ────────────────────────────
    try:
        from resource_explorer.surveyors.egeria_publisher import EgeriaPublisher, EgeriaConnectionError

        zone_names = (req.zone_names if req else None) or None

        def _run_publish():
            # pyegeria sync wrappers call asyncio.get_event_loop() — set one for this thread
            import asyncio as _aio
            loop = _aio.new_event_loop()
            _aio.set_event_loop(loop)
            try:
                # The manual Publish waits for Egeria (RUNS_PUBLISH_INLINE governs a run's auto-publish only).
                publisher = EgeriaPublisher(registry=registry, zone_names=zone_names)
                return publisher.publish(result, defer_drain=False), publisher
            finally:
                loop.close()
                _aio.set_event_loop(None)

        report_guid, publisher = await asyncio.to_thread(_run_publish)
    except Exception as exc:
        return PublishResult(
            status="error",
            error=str(exc),
            stage="publish",
            surveyed_at=result.surveyed_at.isoformat(),
        )

    # Separate, minimal activity_log entry (not a change to EgeriaPublisher.
    # publish()'s own log_catalog call above, which every publish surface
    # shares) — only written when this publish came from a Survey Definition
    # candidate's own "☁ Publish" button, so its last-published badge
    # (registry.get_survey_definition_last_activity) has something to find.
    run_id = getattr(publisher, "publish_run_id", "")
    run_id = run_id if isinstance(run_id, str) else ""
    proof = manual_publish_proof(registry, run_id)
    if req and req.survey_definition_ref:
        from resource_explorer.activity_logger import log_catalog

        entry = log_catalog(
            registry, entity_type="repo", entity_slug=slug,
            entity_name=project.display_name, entity_location=project.github_url,
            status="ok",
            summary=(f"'{req.survey_definition_ref}' findings ({len(result.annotations)} annotations): "
                     f"{proof['word']}"),
            detail=json.dumps({"survey_definition_ref": req.survey_definition_ref}),
        )
        if run_id and entry:
            # Its badge (get_survey_definition_last_activity) follows the run's proof, not this row existing.
            registry.tag_activity_publish_run(entry, run_id)

    return PublishResult(
        status="ok",
        report_guid=report_guid,
        annotation_count=len(result.annotations),
        surveyed_at=result.surveyed_at.isoformat(),
        publish_state=proof["state"],
        publish_word=proof["word"],
        publish_words=proof.get("words") or "",
    )


def manual_publish_proof(registry, publish_run_id: str) -> dict:
    """The manual Publish's word, from the run's proof rows. No run id means nothing was queued: "nothing to
    publish". A run id whose rows cannot be read is "publish not established", never "published"."""
    if not publish_run_id:
        return {"state": "nothing", "word": "nothing to publish", "words": ""}
    try:
        proof = registry.publish_proofs([publish_run_id]).get(publish_run_id)
    except Exception as exc:  # noqa: BLE001 - said, never guessed
        return {"state": "unknown", "word": "publish not established", "words": f"the outbox could not be read: {exc}"}
    return proof or {"state": "unknown", "word": "publish not established", "words": "no outbox rows for this run"}


async def _authorize(registry: ProjectRegistry, entity_type: str, slug: str, write: str, *,
                     element_guid: str | None = None) -> None:
    """Brief Z2: the one curation decision (`workflows.curate.resource_access`, via the route form
    `authorize_resource_write`), on the resource's own element: 403 with the reason when refused."""
    from resource_explorer.web.routes.curate import authorize_resource_write

    await authorize_resource_write(registry, entity_type, slug, write=write, element_guid=element_guid)


# ── Egeria on a repository (parity slice G1, PI-001..PI-006) ─────────────────
# The Next UI's "Publish" section calls these. Every state word comes from proof rows
# (resource_explorer/repo_publish.py); nothing below archives or deletes in Egeria.

def _publish_author(request: Request, action: str) -> str:
    user = get_current_user(request)
    author = (user or {}).get("user_id") or (user or {}).get("sub") or (user or {}).get("username") or ""
    if not author:
        raise HTTPException(status_code=401, detail=f"Sign in to {action}: the record needs an author.")
    return author


class PublishReportRequest(BaseModel):
    #: "Publish without one" (PI-006): records the choice (status "declined") and proceeds. There is no
    #: zones field: zones are never a per-press choice (they come from EXPLORER_PUBLISH_ZONES).
    without_project: bool = False


@router.get("/{slug}/publish-state")
def get_publish_state(slug: str, request: Request) -> dict:
    """In Egeria or not, the asset GUID, the last report row and the project: RE's own records only. Brief F:
    plus what the signed-in viewer's Publish sends besides the report (`goes_out`; "" = signed out, who sends no
    feedback)."""
    from resource_explorer import repo_publish
    _project, registry = _get_project_or_404(slug)
    user = get_current_user(request)
    viewer = (user or {}).get("user_id") or (user or {}).get("sub") or (user or {}).get("username") or ""
    return repo_publish.publish_state(registry, slug, viewer=viewer)


@router.post("/{slug}/publish-report")
async def publish_report(slug: str, request: Request, req: PublishReportRequest | None = None):
    """Publish RE's survey report WHOLE, read it back, record the proof row (PI-001, PI-006)."""
    from resource_explorer import repo_publish
    _project, registry = _get_project_or_404(slug)
    author = _publish_author(request, "publish to Egeria")
    await _authorize(registry, "repo", slug, "publish")         # Brief Z2
    # to_thread copies the caller's context, so the project answer (per caller) is read as the caller.
    try:
        out = await asyncio.to_thread(
            repo_publish.publish_report, registry, slug, author,
            without_project=bool(req and req.without_project))
    except repo_publish.AlreadyRunning as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    if out.get("gate") == "context_required":
        return JSONResponse(status_code=428, content={
            "detail": "egeria_project_context_required", "entity_type": "repo", "entity_slug": slug,
            "sentence": out["sentence"], "sent": out.get("sent") or {}})
    if out.get("gate") == "no_survey":
        # Publish never surveys (brief section 1): with nothing kept it sends no REPORT and says so; Brief F round
        # 2: the rest of the press (tags, group, feedback) still went when the resource is in Egeria (`sent`).
        return JSONResponse(status_code=409, content={"detail": out["sentence"], "sent": out.get("sent") or {}})
    return out


class ResurveyRequest(BaseModel):
    #: Step keys to run (the stale ones). Omitted runs every step. Never a publish.
    steps: list[str] | None = None


@router.post("/{slug}/resurvey")
async def resurvey_repo(slug: str, request: Request, req: ResurveyRequest | None = None):
    """Run the survey and nothing else (brief section 1): no publish, no Egeria write. The band then
    shows the new age. A survey already running for this resource is a 409, not a second one."""
    from resource_explorer import repo_publish
    _project, registry = _get_project_or_404(slug)
    author = _publish_author(request, "re-survey")
    try:
        return await asyncio.to_thread(
            repo_publish.resurvey, registry, slug, author, (req.steps if req else None) or None)
    except repo_publish.AlreadyRunning as exc:
        raise HTTPException(status_code=409, detail=str(exc))


@router.post("/{slug}/forget-links")
async def forget_links(slug: str, request: Request) -> dict:
    """"Forget Egeria links": RE drops its cached GUIDs and survey history for this resource (PI-005).
    Nothing is sent to Egeria; nothing there is archived or deleted. Brief Z2: the same access check as every
    other change to the resource (a decision: an update), before anything is forgotten."""
    from resource_explorer import repo_publish
    _project, registry = _get_project_or_404(slug)
    author = _publish_author(request, "forget Egeria links")
    await _authorize(registry, "repo", slug, "decision")
    return await asyncio.to_thread(repo_publish.forget_links, registry, slug, author)


def _file_types_retired() -> HTTPException:
    from resource_explorer import repo_publish
    return HTTPException(status_code=410, detail=repo_publish.FILE_TYPES_RETIRED_SENTENCE)


@router.get("/{slug}/file-types")
def get_file_types(slug: str) -> dict:
    """RETIRED (ruling 2026-10-07): the DataSet-per-file-type preview. Answers 410 for any old caller."""
    raise _file_types_retired()


@router.post("/{slug}/file-types/commit")
async def commit_file_types(slug: str, request: Request) -> dict:
    """RETIRED (ruling 2026-10-07): the DataSet-per-file-type press. Answers 410; nothing is created."""
    raise _file_types_retired()


@router.get("/{slug}/file-type-measurements")
def get_file_type_measurements(slug: str) -> dict:
    """Read only: the file-type annotations the survey report carries, and the DataSets earlier presses
    made (shown as "retired mechanism · kept in Egeria"). Sends nothing to Egeria."""
    from resource_explorer import repo_publish
    project, registry = _get_project_or_404(slug)
    return repo_publish.file_type_measurements(registry, project, slug)


@router.post("/{slug}/materialize-annotations")
async def materialize_annotations(slug: str, report_guid: str | None = None) -> dict:
    """Pull annotations Egeria PRODUCED into RE's own findings/metrics tables.

    The inverse of publishing. A Survey Definition whose steps execute natively
    in Egeria writes its annotations there, where RE's Results views, trend
    charts and question-answering cannot see them. This brings them into
    project_analysis_findings / project_analysis_metrics, which those views
    already read -- so nothing downstream needs a special case.

    Idempotent per report: one already materialized is skipped, because those
    tables are append-only and a second pass would double every count read off
    them.

    Omit report_guid to sweep every not-yet-materialized report for the project.
    """
    import asyncio

    from resource_explorer.surveyors.egeria_annotation_materializer import (
        EgeriaAnnotationMaterializer,
    )

    _project, registry = _get_project_or_404(slug)
    materializer = EgeriaAnnotationMaterializer(registry=registry)
    # pyegeria's sync wrappers drive their own event loop, so this cannot run
    # on the request's -- same treatment every other Egeria call here gets.
    if report_guid:
        return await asyncio.to_thread(materializer.materialize_report, slug, report_guid)
    return await asyncio.to_thread(materializer.materialize_project, slug)


# ── resync ──────────────────────────────────────────────────────────────────
# Declared BEFORE /{slug}/... routes: Starlette matches in declaration order, so
# a literal path after a path-param catch-all at the same position is never
# reached — "resync" would be read as a slug.

@router.get("/resync/scheduler-status")
async def resync_status() -> dict:
    """Scheduled scan-and-clear status — same shape convention and cheap,
    in-process-state-only contract as bootstrap.py's /status route (see
    bootstrap.py's own bootstrap_status() above): reads
    egeria_resync.get_status(), never calls Egeria itself, so the Resync
    pane can poll it alongside its scan without adding load.

    Distinguishes "the scheduler ran and correctly found nothing to fix"
    from "the scheduler hasn't run in days" -- both otherwise look like an
    identical clean row.
    """
    from resource_explorer.egeria_resync import get_status

    return get_status()


@router.get("/resync/scan")
async def resync_scan() -> dict:
    """What has drifted between RE and Egeria. Read-only.

    Never writes, and never reads an unreachable Egeria as an empty one — a
    failed lookup is reported under `undetermined` rather than counted as drift.
    """
    import asyncio

    from resource_explorer.egeria_resync import EgeriaResync

    return (await asyncio.to_thread(EgeriaResync().scan)).as_dict()


class ResyncApplyRequest(BaseModel):
    steps: list[str] = []


@router.post("/resync/apply")
async def resync_apply(req: ResyncApplyRequest) -> dict:
    """Run the named repairs. Nothing runs unless it is asked for by name.

    Steps execute in dependency order regardless of the order given: clearing
    stale pointers must precede relinking, or a member is attached to an asset
    that is about to be cleared.
    """
    import asyncio

    from resource_explorer.egeria_resync import EgeriaResync

    if not req.steps:
        raise HTTPException(status_code=400, detail="no repair steps requested")
    try:
        return await asyncio.to_thread(EgeriaResync().apply, req.steps)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/{slug}/survey-report", response_model=SurveyReportData)
async def get_survey_report(slug: str) -> SurveyReportData:
    """Assemble survey report data entirely from SQLite — no Egeria connection needed.

    Returns file type counts, health stats, dependency summary, and the latest
    survey record.  Egeria annotation details are fetched separately via the
    /annotations endpoint when the user requests them.
    """
    import json as _json

    project, registry = _get_project_or_404(slug)

    # File types from latest survey run
    file_type_rows = registry.query_file_type_counts(slug)
    local_surveyed_at = file_type_rows[0].get("surveyed_at", "") if file_type_rows else ""
    file_types: list[FileTypeSummary] = []
    for r in file_type_rows:
        exts: dict = {}
        if r.get("details_json"):
            try:
                exts = _json.loads(r["details_json"])
            except Exception:
                pass
        file_types.append(FileTypeSummary(
            label=r["type_label"],
            count=r["file_count"],
            source=r.get("source", "extension"),
            extensions=exts,
        ))
    total_files = sum(f.count for f in file_types)

    # Project stats (stars, forks, language, license…)
    stats = registry.get_latest_project_stats(slug) or {}
    # NULL stays null: "not read" is not 0 (the Next report view draws null as "not read").
    health = {
        "stars":            stats.get("stars"),
        "forks":            stats.get("forks"),
        "open_issues":      stats.get("open_issues"),
        "contributors":     stats.get("contributors_count"),  # the column is contributors_count; "contributors" was never a key, so this always read 0
        "last_push":        stats.get("last_push", ""),
        "primary_language": stats.get("primary_language", ""),
        "license":          stats.get("license", ""),
        "topics":           stats.get("topics", ""),
    }

    # Dependencies grouped by ecosystem
    deps = registry.query_dependencies(slug)
    dep_summary = [DependencySummary(**v) for v in summarise_dependencies(deps)]

    # Data profiles from project_data_profiles
    raw_profiles = registry.get_data_profiles(slug)
    data_profiles: list[DataProfileSummary] = []
    for p in raw_profiles:
        cols: list[dict] = []
        if p.get("schema_json"):
            try:
                cols = _json.loads(p["schema_json"])
            except Exception:
                pass
        data_profiles.append(DataProfileSummary(
            file_path=p["file_path"],
            format=p["format"],
            row_count=p.get("row_count"),
            col_count=p.get("col_count"),
            columns=cols,
            null_summary=p.get("null_summary") or "",
            file_size_bytes=p.get("file_size_bytes") or 0,
            profiled_at=p.get("profiled_at"),
        ))

    # Latest Egeria survey (may be None if never published)
    latest_raw = registry.get_latest_egeria_survey(slug)
    latest_survey: SurveyRow | None = None
    if latest_raw:
        latest_survey = SurveyRow(
            surveyed_at=latest_raw["surveyed_at"],
            egeria_report_guid=latest_raw["egeria_report_guid"],
            published_at=latest_raw["published_at"],
            annotation_count=latest_raw.get("annotation_count"),
        )

    return SurveyReportData(
        slug=slug,
        display_name=project.display_name,
        github_url=project.github_url,
        latest_survey=latest_survey,
        file_types=file_types,
        total_files=total_files,
        health=health,
        primary_language=stats.get("primary_language") or "",  # a NULL column is not a str: it 500ed the route
        dependencies=dep_summary,
        has_egeria_annotations=latest_survey is not None,
        data_profiles=data_profiles,
        local_surveyed_at=local_surveyed_at,
        health_read=bool(stats),
    )


@router.post("/{slug}/catalog-elements")
async def catalog_elements(slug: str) -> CatalogResult:
    """RETIRED (ruling 2026-10-07, DESIGN-FILE-TYPES-AS-ANNOTATIONS.md): this created one DataSet per
    file type. It answers 410 and creates nothing; DataSets it made earlier stay in Egeria as history."""
    raise _file_types_retired()


@router.get("/{slug}/diff")
async def get_survey_diff(slug: str) -> dict:
    """Compare the two most recent local survey runs for a repo.

    Returns None (empty dict) when fewer than two runs exist.
    """
    project, registry = _get_project_or_404(slug)
    history = registry.query_file_type_history(slug)
    if len(history) < 2:
        return {}

    curr_ts = history[-1]["surveyed_at"]
    prev_ts = history[-2]["surveyed_at"]

    def _counts_at(ts: str) -> dict[str, int]:
        with registry._conn() as conn:
            rows = conn.execute(
                "SELECT type_label, file_count FROM project_file_type_counts "
                "WHERE project_slug = ? AND surveyed_at = ?",
                (registry._normalize_slug(slug), ts),
            ).fetchall()
        return {r["type_label"]: r["file_count"] for r in rows}

    curr_counts = _counts_at(curr_ts)
    prev_counts = _counts_at(prev_ts)

    all_types = set(curr_counts) | set(prev_counts)
    changes = []
    for t in all_types:
        prev_v = prev_counts.get(t, 0)
        curr_v = curr_counts.get(t, 0)
        if curr_v != prev_v:
            pct = round((curr_v - prev_v) / prev_v * 100) if prev_v > 0 else None
            changes.append({"label": t, "prev": prev_v, "curr": curr_v,
                            "delta": curr_v - prev_v, "pct": pct})
    changes.sort(key=lambda c: abs(c["delta"]), reverse=True)

    total_curr = sum(curr_counts.values())
    total_prev = sum(prev_counts.values())

    return {
        "prev_date": prev_ts,
        "curr_date": curr_ts,
        "total_prev": total_prev,
        "total_curr": total_curr,
        "total_delta": total_curr - total_prev,
        "changes": changes[:10],
    }




