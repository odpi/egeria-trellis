"""Database management endpoints — list, get, register, survey, remove."""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from resource_explorer.web.routes._annotation_items import build_annotation_items
from resource_explorer.credential_crypto import (
    CREDENTIAL_UNREADABLE_REASON,
    CredentialUnreadableError,
)

log = logging.getLogger(__name__)

router = APIRouter()


def _get_database_for_use(registry, slug: str):
    """get_database for a route that may CONNECT with the stored credential:
    an unreadable credential is a clear 409 for this database only, never an
    empty-password connect and never a pass-shaped result."""
    try:
        return registry.get_database(slug)
    except CredentialUnreadableError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None


class DatabaseSummary(BaseModel):
    """Summary information for a database entity."""
    slug: str
    display_name: str
    db_type: str
    host: str
    port: int
    database_name: str
    description: str
    status: str
    last_surveyed_at: str
    schema_count: int | None
    table_count: int | None
    column_count: int | None
    server_slug: str = ""       # FK to db_servers; empty for standalone databases
    egeria_asset_guid: str = "" # DB element GUID in Egeria; "" = not yet cataloged
    last_survey_source: str = ""# "local" | "egeria" | "egeria-published" from latest survey
    db_user: str = ""           # stored username (no password exposed)
    egeria_host: str = ""
    egeria_url: str = ""
    egeria_server: str = ""
    group_slug: str = ""
    # "ok" | "none" | "unreadable" — a real marker from the registry, never the
    # password. "unreadable" = a credential is stored but cannot be decrypted;
    # `credential_reason` is then the fixed, secret-free display string.
    credential_status: str = "none"
    credential_reason: str = ""
    # UTC ISO seconds of the last recorded credential change; None = before
    # this was recorded.
    credential_changed_at: str | None = None
    # 'undecided' when nobody has ever decided, same convention
    # `ProjectSummary.disposition` (projects.py) already uses — populated
    # once `repo_dispositions`' PK generalized to (entity_type, entity_slug)
    # (Backlog.md, "Disposition is NOT fixed here", 2026-09-22).
    disposition: str = "undecided"
    # egeria_asset_guid set AND the linkage is not recorded stale — see
    # `egeria_linkage.describe_publish_status`. Boolean only, not the raw
    # GUID, same convention as `ProjectSummary.is_published` (projects.py).
    # Lets /next's shared `lifecycleMark()` render the same "published to
    # Egeria" mark for a database row it already renders for a repo row.
    is_published: bool = False
    # Non-empty only when a GUID IS cached but the linkage is stale (the
    # element no longer exists in Egeria, e.g. after a platform reset) — a
    # ready-to-render sentence carrying the GUID history that a plain
    # `is_published=False` would otherwise discard. See
    # `egeria_linkage.describe_publish_status`'s own docstring for why this
    # exists: found live 2026-09-26, `coco_pharma` had shown a plain
    # "published to Egeria" for four days after its link went stale.
    egeria_publish_note: str = ""
    # Personal view filter, separate axis from disposition — see
    # `ProjectSummary.working_set_hidden` (projects.py) and
    # `registry.py`'s `resource_working_set` table. Needed so /next's
    # select-mode "hide" bulk action and "Show hidden" toggle work for
    # databases the same way they do for repos.
    working_set_hidden: bool = False
    # The `credential_capability` probe's last result (design REPLY-DATABASE-
    # CREDENTIAL-CAPABILITY-VISIBILITY.md §4, Piece 1 of ASK-...-#251), None
    # when the probe has never run for this database. Carried on the summary
    # row (rather than requiring a separate fetch) so /next's shared
    # `resourceHeaderHtml()` can render the persistent visibility banner from
    # the same row it already reads for every other resource type — a repo or
    # filesystem row simply never sets this field.
    credential_capability: dict | None = None
    # When the survey that carried that probe was taken (a stored row's own
    # `surveyed_at`), so the visibility banner can say WHICH survey its counts
    # are about instead of speaking for the whole page. "" when unknown.
    credential_capability_at: str = ""


class DatabaseRegistration(BaseModel):
    """Request body for registering a new database."""
    slug: str
    display_name: str
    db_type: str
    host: str
    port: int
    database_name: str
    connection_ref: str = ""
    description: str = ""
    # Optional stored credentials
    db_user: str = ""
    db_password: str = ""
    # Egeria-visible hostname for the DB (e.g. host.docker.internal when DB is in Docker)
    egeria_host: str = ""
    # Optional stored Egeria connection details
    egeria_url: str = ""
    egeria_server: str = ""
    #: Ignored (owner, 2026-10-09): accepted only so an older caller is not refused; logged.
    egeria_user: str = ""
    egeria_password: str = ""
    group_slug: str = ""


class ConnectionTestRequest(BaseModel):
    """Body of the register form's "Test connection". Nothing is stored."""
    host: str
    port: int = 5432
    database_name: str
    db_type: str = "postgresql"
    db_user: str = ""
    db_password: str = ""


class DatabaseCredentialsUpdate(BaseModel):
    """Request body for updating a registered database's stored credentials."""
    db_user: str = ""
    db_password: str = ""


class SurveyRequest(BaseModel):
    """Request body for triggering a database survey."""
    username: str = ""  # DB username — falls back to stored db_user if blank
    password: str = ""  # DB password — falls back to stored db_password if blank
    use_egeria: bool = False
    force_custom: bool = False
    egeria_url: str | None = None
    egeria_server: str | None = None
    secrets_path: str | None = None
    refresh: bool = True


class SurveyResult(BaseModel):
    """Result of a database survey operation."""
    status: str  # "ok" | "error" | "pending"
    slug: str
    message: str = ""
    error: str | None = None
    source: str | None = None  # "egeria" | "custom"
    schema_count: int | None = None
    table_count: int | None = None
    column_count: int | None = None


class EgeriaSurveyReportRow(BaseModel):
    """A SurveyReport as it actually exists in Egeria right now (live read) —
    distinct from the locally-cached rows returned by /surveys, since a
    Survey Definition's "egeria" step triggers an async native survey whose
    report doesn't appear in the local registry at all."""
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
    #: (Phase 1 slice 10, and the surface that matters most for it: this is
    #: the model behind the database tab's annotation list). Empty means "no
    #: contentStatus stated", not "confirmed".
    content_status: str = ""


def _to_summary(db) -> DatabaseSummary:
    """Convert DatabaseEntity to DatabaseSummary."""
    # Get latest survey data if available. `get_latest_database_survey`
    # (2026-09-29 round 1, PER-REQUEST-SERVER-LATENCY-IMPLEMENTED.md), not
    # `get_database_surveys(...)[0]` — this route used
    # to pay the full unbounded "every historical survey's full blob" fetch
    # for a value that only ever needed the single newest row.
    import json
    from resource_explorer.registry import ProjectRegistry
    registry = ProjectRegistry()
    latest = registry.get_latest_database_survey(db.slug)
    disp = registry.get_disposition_for_entity("database", db.slug) or {}

    # The credential_capability probe's last result, if the survey_data blob
    # (from ANY prior survey, not just the most recent one) carries one —
    # see credential_capability's own results reader
    # (_credential_capability_results) for why this reads the same blob
    # rather than a dedicated table. Checked across all stored surveys, most
    # recent first, since a plain schema/statistics-only run after the probe
    # ran would otherwise silently hide a still-current capability reading.
    #
    # Uses `find_latest_database_survey_with_key` (2026-09-29 round 2,
    # PER-REQUEST-SERVER-LATENCY-ROUND-2-IMPLEMENTED.md)
    # rather than looping `surveys` (already fetched above for
    # schema/table/column counts) — that method pushes the same
    # newest-first "has this key" search into Postgres instead of
    # rescanning every historical blob in Python a second time.
    credential_capability: dict | None = None
    credential_capability_at = ""
    cap_survey = registry.find_latest_database_survey_with_key(db.slug, "credential_capability")
    if cap_survey is not None:
        try:
            data = json.loads(cap_survey.get("survey_data") or "{}")
        except (ValueError, TypeError):
            data = {}
        credential_capability = data.get("credential_capability") or None
        credential_capability_at = str(cap_survey.get("surveyed_at") or "")

    from resource_explorer.egeria_linkage import describe_publish_status
    publish_status = describe_publish_status(
        registry, "database", db.slug, getattr(db, "egeria_asset_guid", "") or "")

    return DatabaseSummary(
        slug=db.slug,
        display_name=db.display_name,
        db_type=db.db_type,
        host=db.host,
        port=db.port,
        database_name=db.database_name,
        description=db.description,
        status=db.status.value,
        last_surveyed_at=db.last_surveyed_at or "",
        schema_count=latest.get("schema_count") if latest else None,
        table_count=latest.get("table_count") if latest else None,
        column_count=latest.get("column_count") if latest else None,
        server_slug=getattr(db, "server_slug", "") or "",
        egeria_asset_guid=getattr(db, "egeria_asset_guid", "") or "",
        last_survey_source=latest.get("source", "") if latest else "",
        db_user=db.db_user or "",
        egeria_host=db.egeria_host or "",
        egeria_url=db.egeria_url or "",
        egeria_server=db.egeria_server or "",
        group_slug=getattr(db, "group_slug", "") or "",
        credential_status=getattr(db, "credential_status", "none") or "none",
        credential_changed_at=getattr(db, "credential_changed_at", None),
        credential_reason=(CREDENTIAL_UNREADABLE_REASON
                           if getattr(db, "credential_status", "") == "unreadable" else ""),
        disposition=disp.get("disposition", "undecided"),
        working_set_hidden=registry.is_working_set_hidden("database", db.slug),
        credential_capability=credential_capability,
        credential_capability_at=credential_capability_at,
        is_published=publish_status["is_published"],
        egeria_publish_note=publish_status["note"],
    )


def _list_databases_sync(db_type: str | None) -> list[DatabaseSummary]:
    from resource_explorer.registry import ProjectRegistry
    registry = ProjectRegistry()
    databases = registry.list_databases(db_type=db_type)
    return [_to_summary(db) for db in databases]


@router.get("/", response_model=list[DatabaseSummary])
async def list_databases(db_type: str | None = None) -> list[DatabaseSummary]:
    """List all registered databases, optionally filtered by type.

    Wrapped in `asyncio.to_thread` (2026-09-29 round 2,
    PER-REQUEST-SERVER-LATENCY-ROUND-2-IMPLEMENTED.md) — found live while
    measuring round 2's gate numbers: `_to_summary` calls several
    synchronous registry methods (including the round-2
    `find_latest_database_survey_with_key`, a real query) directly inside
    this `async def` handler, unwrapped. With only 3 registered databases
    in the environment this was measured in, the effect wasn't a 68-project
    -scale stall (`list_projects`'s round-1 bug) but it was still real:
    `/api/auth/me`, which touches no database at all, was caught at 871ms
    in a concurrent boot-sequence measurement, queued behind this same
    event-loop-thread block. Same bug class and same fix as `list_projects`
    (round 1) and `/questions` (BY-ANALYSIS-PROGRESSIVE-AND-GRAPH-IMPLEMENTED.md §2a) — this is a
    third, independently-found instance.
    """
    return await asyncio.to_thread(_list_databases_sync, db_type)


@router.get("/{slug}", response_model=DatabaseSummary)
async def get_database(slug: str) -> DatabaseSummary:
    """Get details for a specific database."""
    from resource_explorer.registry import ProjectRegistry
    registry = ProjectRegistry()
    database = registry.get_database(slug, allow_unreadable=True)
    if not database:
        raise HTTPException(status_code=404, detail=f"Database '{slug}' not found")
    return _to_summary(database)


@router.get("/{slug}/analyses/last-activity")
async def get_analyses_last_activity(slug: str) -> dict[str, dict]:
    """{analysis_id: {last_run_at, last_run_status, last_published_at, ...}}
    for every local database AnalysisKind — the database equivalent of
    `projects.py`'s `GET /{slug}/analyses/last-activity`, added because the
    frontend's `_loadAnalysisCatalogPanel()` only ever fetched that repo
    route: a database's Analyses cards (Schema Conventions, Nested Column
    Profile, Data Class Match, Change Rates, ...) showed no run/result
    indicator at all, no matter how many real surveys had completed against
    the database (docs/Backlog.md has the live-reproduction details).

    Delegates to the same `workflows.analysis.build_analysis_last_activity`
    the repo route now uses — see that function's docstring for exactly
    what is and is not real data yet (run attribution is; publish
    attribution is not, for database/filesystem, until their publish paths
    also record `project_published_analyses`/`project_published_annotation_
    types` — logged as a follow-up rather than guessed at here)."""
    from resource_explorer.registry import ProjectRegistry
    from resource_explorer.workflows.analysis import build_analysis_last_activity

    registry = ProjectRegistry()
    if not registry.get_database(slug, allow_unreadable=True):
        raise HTTPException(status_code=404, detail=f"Database '{slug}' not found")

    return build_analysis_last_activity(registry, "database", slug)


@router.get("/{slug}/survey-results/boards")
async def get_database_survey_results_boards(slug: str, stage: str = "") -> dict:
    """The By-analysis contents board's cheap half for databases — see
    `projects.py`'s identically-shaped route and `workflows.analysis.
    list_survey_result_boards`'s docstring."""
    from resource_explorer.registry import ProjectRegistry
    from resource_explorer.workflows.analysis import list_survey_result_boards

    registry = ProjectRegistry()
    if not registry.get_database(slug, allow_unreadable=True):
        raise HTTPException(status_code=404, detail=f"Database '{slug}' not found")

    return await asyncio.to_thread(list_survey_result_boards, registry, "database", slug, stage)


@router.get("/{slug}/survey-results")
async def get_database_survey_results(
    slug: str, stage: str = "", include_empty: bool = False, board_id: str = "",
) -> dict:
    """The database equivalent of `projects.py`'s `GET /{slug}/survey-results`
    ("By analysis" in /next) — added because that pane was gated to
    `resourceType === 'repo'` on the honest grounds that the repo route reads
    `REPO_ANALYSIS_RESULTS_MAP` directly (docs/Backlog.md's "By analysis" was
    repo-only entry). Delegates to the same `workflows.analysis.
    build_survey_results` the repo route now uses — see that function's
    docstring for what a database gets here: one synthesized dashboard per
    analysis_id in `DATABASE_ANALYSIS_RESULTS_MAP` (14 of 18 database
    analyses have one; see that map's own docstring for the three that don't
    yet and why), not repo's curated multi-analysis groupings.

    `board_id` (optional): one board only, for the /next By-analysis pane's
    progressive fetch — see `projects.py`'s `get_survey_results` docstring.

    Off the event loop for the same reason as the repo route: the
    `db_derived`-backed readers recompute on every call and a slow one must
    not block other requests."""
    from resource_explorer.registry import ProjectRegistry
    from resource_explorer.workflows.analysis import build_survey_results

    registry = ProjectRegistry()
    if not registry.get_database(slug, allow_unreadable=True):
        raise HTTPException(status_code=404, detail=f"Database '{slug}' not found")

    return await asyncio.to_thread(
        build_survey_results, registry, "database", slug, stage, include_empty, board_id,
    )


@router.get("/{slug}/schema-inventory-tree")
async def get_database_schema_inventory_tree(slug: str) -> dict:
    """Slice 22's Schemas → Tables → Columns tree for /next's Schema
    Inventory view — one call, the whole tree, built from the structured
    `database_tables`/`database_columns` detail rows (never the classic
    UI's `survey_data` blob path). See `schema_inventory_tree()`'s own
    docstring for the shape.

    Since slice A2.1 the schemas come from `catalogue_scope.resolve_node_set` (the
    fullest, newest complete survey, native or local) and a `sources` block names
    them; `schema_inventory_tree()` is still the local half of that merge.

    404s the same way `get_database_survey_results` does; `to_thread`
    since this walks every stored table/column row plus the credential
    probe, same reasoning as that route."""
    from resource_explorer.catalogue_scope import resolve_node_set, sources_view
    from resource_explorer.registry import ProjectRegistry

    registry = ProjectRegistry()
    if not registry.get_database(slug, allow_unreadable=True):
        raise HTTPException(status_code=404, detail=f"Database '{slug}' not found")

    # A2.1: the same resolved node set the Curate tree reads (`resolve_node_set`),
    # not RE's own credential-scoped local survey alone. Each schema and table
    # carries its `source` and as-of; `sources` names the surveys so the pane can
    # say which one it reads and when the two disagree.
    resolved = await asyncio.to_thread(resolve_node_set, registry, slug)
    return {"schemas": resolved["schemas"], "sources": sources_view(resolved)}


@router.get("/{slug}/questions")
async def get_database_questions(
    slug: str,
    phase: str = "scouting",
    perspectives: str | None = None,
    purposes: str | None = None,
) -> dict:
    """The database equivalent of `projects.py`'s `GET /{slug}/scouting-
    questions` — added because `question_catalog_reader.get_questions()` was
    already resource-type-generic, but the ONLY route reaching it was
    hardcoded to the repo registry (docs/Backlog.md's "scouting-questions was
    repo-only" entry). Delegates to `workflows.scouting.
    build_question_checklist` — same has_data scoring machinery the repo
    route uses, keyed to `DATABASE_ANALYSIS_RESULTS_MAP` instead.

    Off the event loop (BY-ANALYSIS-PROGRESSIVE-AND-GRAPH, 2026-09-28): a
    live thread dump (`kill -USR1`, this file's own module docstring on the
    dump handler) caught this route's `build_question_checklist` call
    running SYNCHRONOUSLY on the main/event-loop thread, blocking every
    other in-flight request -- including this same process's own
    `to_thread`-wrapped `/survey-results` board reads, which cannot even
    have their results delivered while the loop itself is stuck inside a
    blocking DB call. `question_has_data` (`workflows/scouting.py`) reaches
    the identical `db_derived`-backed readers `/survey-results` already
    off-loads (`_db_derived_field_reader`'s `run_db_derived()`), so this had
    the exact same "recomputes from stored rows on every call" cost as that
    route -- it was just never wrapped. Same fix as `get_database_survey_
    results`/`get_database_schema_inventory_tree` above, same reason."""
    from resource_explorer.registry import ProjectRegistry
    from resource_explorer.workflows.scouting import build_question_checklist

    registry = ProjectRegistry()
    if not registry.get_database(slug, allow_unreadable=True):
        raise HTTPException(status_code=404, detail=f"Database '{slug}' not found")

    persp_list = [p.strip() for p in (perspectives or "").split(",") if p.strip()]
    purp_list = [p.strip() for p in (purposes or "").split(",") if p.strip()]
    return await asyncio.to_thread(
        build_question_checklist, registry, "database", slug, phase, persp_list, purp_list,
    )


@router.post("/_test-connection")
async def test_database_connection_before_register(req: ConnectionTestRequest) -> dict:
    """Parity G2 / PI-015: the "Test connection" of test-then-register.

    Connects with the typed credential (short timeout, off the event loop) and
    answers with one sentence. Registers nothing, writes no row, logs no
    credential; a refusal is a 200 whose status is "error", the same shape as
    `/api/db-servers/_test-inline`, so the form shows the sentence either way.
    """
    from resource_explorer.credential_check import probe_database_connection

    return await asyncio.to_thread(
        probe_database_connection, req.db_type, req.host, req.port,
        req.database_name, req.db_user, req.db_password)


@router.post("/register", response_model=DatabaseSummary)
async def register_database(req: DatabaseRegistration) -> DatabaseSummary:
    """Register a new database."""
    from resource_explorer.egeria_host import default_egeria_host
    from resource_explorer.registry import DatabaseEntity, ProjectRegistry, ProjectStatus
    from resource_explorer.web.routes._validation import ignore_egeria_credentials

    ignore_egeria_credentials(req, where="register database")

    registry = ProjectRegistry()

    # Check if slug already exists
    existing = registry.get_database(req.slug, allow_unreadable=True)
    if existing:
        raise HTTPException(status_code=400, detail=f"Database '{req.slug}' already exists")
    
    # Create database entity
    database = DatabaseEntity(
        slug=req.slug,
        display_name=req.display_name,
        db_type=req.db_type,
        host=req.host,
        port=req.port,
        database_name=req.database_name,
        connection_ref=req.connection_ref,
        description=req.description,
        status=ProjectStatus.ACTIVE,
        last_surveyed_at="",
        db_user=req.db_user,
        db_password=req.db_password,
        egeria_host=default_egeria_host(req.host, req.egeria_host),
        egeria_url=req.egeria_url,
        egeria_server=req.egeria_server,
        group_slug=req.group_slug,
    )
    
    # Register in registry
    registry.register_database(database)

    # One proof row naming who and when (PI-015's "saved · you · just now"
    # reads it back). The summary carries the slug only: no credential, no
    # user name, no host.
    import uuid
    from datetime import datetime, timezone

    from resource_explorer.registry import ActivityEntry, current_user_id

    registry.write_activity(ActivityEntry(
        id=str(uuid.uuid4()), ts=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        operation="register", intent="discovery", entity_type="database",
        entity_slug=req.slug, entity_name=req.display_name, status="ok",
        summary=f"Registered database {req.slug}", detail=current_user_id() or "",
    ))

    _project_credential_to_omsecrets(req.slug, req.db_user, req.db_password)

    return _to_summary(database)


@router.get("/{slug}/registration")
async def get_database_registration(slug: str) -> dict:
    """Who registered this database and when, read back from its registration
    row. `registered_by` is "" when no registration row exists (a database
    registered before rows were written, or by the CLI): unknown is said, not
    guessed."""
    from resource_explorer.registry import ProjectRegistry

    registry = ProjectRegistry()
    database = registry.get_database(slug, allow_unreadable=True)
    if not database:
        raise HTTPException(status_code=404, detail=f"Database '{slug}' not found")
    rows = registry.list_activity(
        entity_type="database", entity_slug=database.slug, operation="register", limit=1)
    return {
        "slug": database.slug,
        "registered_at": database.registered_at or "",
        "registered_by": (rows[0].get("detail") or "") if rows else "",
    }


@router.get("/{slug}/credential-drift")
async def get_database_credential_drift(slug: str) -> dict:
    """The drift check, as the CLI's `check-credential-drift` prints it:
    whether RE's registry and the `.omsecrets` file both hold this database's
    secrets collection. Collection presence only, never a value. `in_sync` is
    None (not False, not True) when no `.omsecrets` path is configured."""
    from resource_explorer.registry import ProjectRegistry

    registry = ProjectRegistry()
    if not registry.get_database(slug, allow_unreadable=True):
        raise HTTPException(status_code=404, detail=f"Database '{slug}' not found")
    return registry.check_credential_drift(slug)


def _project_credential_to_omsecrets(slug: str, db_user: str, db_password: str) -> None:
    """Write the same credential to the `.omsecrets` file's projection for
    this database, per design REPLY-DATABASE-CREDENTIAL-CAPABILITY-
    VISIBILITY.md §7: "one secrets-collection name per (resource, role),
    written to both places by RE in the same operation." A no-op when either
    no password was supplied or no local `.omsecrets` path is configured
    (`EGERIA_SECRETS_STORE_LOCAL_PATH`) — most deployments have neither the
    file nor a host-visible path to it, and that must never surface as an
    error on what is otherwise a successful registry write."""
    if not db_password:
        return
    from resource_explorer.omsecrets_store import secrets_collection_name, write_credential

    write_credential(secrets_collection_name(slug), db_user, db_password)


@router.patch("/{slug}/credentials", response_model=DatabaseSummary)
async def update_database_credentials(slug: str, req: DatabaseCredentialsUpdate) -> DatabaseSummary:
    """Update the stored db_user/db_password for an already-registered database,
    without disturbing its registration history (slug, egeria_asset_guid,
    survey history, etc.). The only supported way to repoint credentials today —
    see registry.py's update_database_credentials docstring.

    Also projects the same credential into the `.omsecrets` file (design
    REPLY-DATABASE-CREDENTIAL-CAPABILITY-VISIBILITY.md §7) via
    `_project_credential_to_omsecrets`, in the same operation as the
    registry write — a no-op when no local `.omsecrets` path is configured.
    """
    from resource_explorer.registry import ProjectRegistry

    registry = ProjectRegistry()

    database = registry.get_database(slug, allow_unreadable=True)
    if not database:
        raise HTTPException(status_code=404, detail=f"Database '{slug}' not found")

    # Connect test BEFORE anything is stored or projected, against this
    # database's own host/port/database_name; off the event loop. No bypass.
    from resource_explorer.credential_check import (
        CredentialCheckError,
        check_database_credential,
    )

    try:
        await asyncio.to_thread(
            check_database_credential, database, req.db_user, req.db_password)
    except CredentialCheckError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None

    registry.update_database_credentials(slug, req.db_user, req.db_password)
    _project_credential_to_omsecrets(slug, req.db_user, req.db_password)

    updated = registry.get_database(slug, allow_unreadable=True)
    return _to_summary(updated)


class EgeriaHostUpdate(BaseModel):
    """Request body for changing a database's Egeria-visible host (and nothing else)."""
    egeria_host: str = ""


@router.patch("/{slug}/egeria-host")
async def update_database_egeria_host(slug: str, req: EgeriaHostUpdate) -> dict:
    """Change ONLY this database's egeria_host. Same write check as the other database edits (an update on
    the database's own element). Says so when Egeria already holds elements under the old address: they
    are not changed here."""
    from resource_explorer.egeria_host import NOTE_IN_EGERIA, check_egeria_host, default_egeria_host
    from resource_explorer.registry import ProjectRegistry
    from resource_explorer.web.routes.curate import authorize_resource_write

    registry = ProjectRegistry()
    database = registry.get_database(slug, allow_unreadable=True)
    if not database:
        raise HTTPException(status_code=404, detail=f"Database '{slug}' not found")
    try:
        value = default_egeria_host(database.host, check_egeria_host(req.egeria_host))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    await authorize_resource_write(registry, "database", slug, write="publish")
    previous = database.egeria_host or ""
    registry.update_egeria_host("database", slug, value)
    in_egeria = bool(getattr(database, "egeria_asset_guid", "") or "")
    return {"slug": database.slug, "previous": previous, "egeria_host": value, "in_egeria": in_egeria,
            "note": NOTE_IN_EGERIA if in_egeria and previous != value else ""}


@router.post("/{slug}/survey", response_model=SurveyResult)
async def survey_database(slug: str, req: SurveyRequest) -> SurveyResult:
    """Trigger a database survey (hybrid mode by default)."""
    from resource_explorer.registry import ProjectRegistry, ProjectStatus
    
    registry = ProjectRegistry()
    database = _get_database_for_use(registry, slug)
    if not database:
        raise HTTPException(status_code=404, detail=f"Database '{slug}' not found")
    if req.use_egeria and not req.force_custom:
        # Brief Z2: the Egeria-adaptive survey can catalog the database in Egeria and start its native survey.
        from resource_explorer.web.routes.curate import authorize_resource_write
        await authorize_resource_write(registry, "database", slug, write="publish")
    
    # Update status to surveying
    registry.update_database_status(slug, ProjectStatus.INDEXING, "")
    
    # Resolve credentials — request overrides stored values
    resolved_user = req.username or database.db_user
    resolved_pwd  = req.password or database.db_password
    if not resolved_user or not resolved_pwd:
        raise HTTPException(
            status_code=400,
            detail="Database credentials are required. Either supply username/password in the request or store them at registration.",
        )
    credentials = {"user": resolved_user, "password": resolved_pwd}

    def _extract_counts(raw: dict) -> tuple[int | None, int | None, int | None]:
        """Pull schema/table/column counts from the surveyor result dict."""
        schema_info = raw.get("schema_info", {})
        sc = len(schema_info.get("schemas", [])) or raw.get("schema_count")
        tc = schema_info.get("total_tables") or raw.get("table_count")
        cc = schema_info.get("total_columns") or raw.get("column_count")
        return sc, tc, cc

    def _do_survey() -> dict[str, Any]:
        """Run survey in thread — raises on fatal connection errors."""
        if req.force_custom or not req.use_egeria:
            from resource_explorer.surveyors.database.database_surveyor import run_database_survey
            result = run_database_survey(
                db_slug=slug,
                credentials=credentials,
                registry=registry,
            )
            sc, tc, cc = _extract_counts(result)
            return {"source": "custom", "schema_count": sc, "table_count": tc, "column_count": cc,
                    "errors": result.get("errors", [])}
        else:
            # Routed through executes_at="egeria-adaptive" (the folded-in
            # HybridDatabaseSurveyor strategy selector — see
            # docs/design-notes/EXECUTION-MODES-HYBRID-CLARIFICATION.md)
            # rather than calling run_hybrid_survey directly, via a one-step
            # synthetic Survey Definition that never touches Egeria to be
            # constructed. `run_hybrid_survey` still exists and still works
            # (it's what the handler delegates to) — this only moves the
            # call site onto `executes_at` routing so the run's `source`
            # provenance is visible in a run report the same way a real
            # Survey Definition's steps are.
            from resource_explorer.surveyors.survey_definition_executor import (
                SurveyDefinitionExecutor,
            )

            executor = SurveyDefinitionExecutor(registry)
            exec_result = executor.run_synthetic_step(
                entity_type="database",
                slug=slug,
                re_analysis_step="postgres_schema_and_stats",
                executes_at="egeria-adaptive",
                db_user=resolved_user,
                db_pwd=resolved_pwd,
                refresh=req.refresh,
                platform_url=req.egeria_url,
                view_server=req.egeria_server,
                secrets_path=req.secrets_path,
            )
            step_report = (exec_result.get("steps") or [{}])[0]
            # A failed survey step is a FAILED survey (false-zero hotfix,
            # 2026-09-30): surface its real error on the run instead of
            # reconstructing an "ok" response with zero counts. The executor
            # has already withheld the failed output from the publish step, so
            # nothing was written to the registry or to Egeria.
            if step_report.get("status") in ("failed", "error"):
                run_errors = list(exec_result.get("errors") or []) or [
                    f"Survey step failed ({step_report.get('status')})"]
                raise RuntimeError("; ".join(str(e) for e in run_errors))
            # Reconstruct run_hybrid_survey's historic flat response shape:
            # the handler nests "schema_info"/"statistics" under "result" to
            # keep the executor's generic publish step from re-publishing
            # them a second time (see _run_egeria_adaptive's own docstring)
            # — unnest them again here, for this route's own response only.
            detail = dict(step_report.get("detail") or {})
            nested = detail.pop("result", None) or {}
            result = {**detail, **nested}
            result.setdefault("source", step_report.get("source", "custom"))
            result.setdefault("errors", [])
            result["errors"] = list(result["errors"]) + [
                e for e in exec_result.get("errors", []) if e not in result["errors"]
            ]
            sc, tc, cc = _extract_counts(result)
            return {"source": result.get("source", "hybrid"),
                    "schema_count": sc, "table_count": tc, "column_count": cc,
                    "errors": result.get("errors", [])}

    try:
        result = await asyncio.to_thread(_do_survey)
        registry.update_database_status(slug, ProjectStatus.ACTIVE, "")

        sc, tc, cc = result.get("schema_count"), result.get("table_count"), result.get("column_count")
        src = result.get("source", "local")
        non_fatal_errors = result.get("errors", [])
        counts_str = ", ".join(filter(None, [
            f"{sc} schema(s)" if sc else "",
            f"{tc} table(s)" if tc else "",
            f"{cc} column(s)" if cc else "",
        ]))

        # Write activity log entry
        try:
            from resource_explorer.activity_logger import log_survey
            log_survey(
                registry,
                entity_type="database",
                entity_slug=slug,
                entity_name=database.display_name,
                entity_location=f"{database.host}:{database.port}/{database.database_name}",
                intent="assessment",
                status="ok",
                summary=f"Survey complete — {counts_str or 'done'} ({src})",
                detail="; ".join(non_fatal_errors) if non_fatal_errors else "",
                annotations=[{"analysis_name": "Schema Inventory", "annotation_type": "SchemaAnalysisAnnotation",
                               "count": tc or 0, "status": "local", "summary": counts_str}],
            )
        except Exception:
            pass

        return SurveyResult(
            status="ok",
            slug=slug,
            message=f"Survey completed using {src} surveyor" + (
                f" ({len(non_fatal_errors)} non-fatal error(s))" if non_fatal_errors else ""
            ),
            source=src,
            schema_count=sc,
            table_count=tc,
            column_count=cc,
        )
    except Exception as exc:
        err_str = str(exc)
        # Write error to activity log
        try:
            from resource_explorer.activity_logger import log_survey
            log_survey(
                registry,
                entity_type="database",
                entity_slug=slug,
                entity_name=database.display_name,
                entity_location=f"{database.host}:{database.port}/{database.database_name}",
                intent="assessment",
                status="error",
                summary=f"Survey failed: {err_str[:200]}",
                detail=err_str,
            )
        except Exception:
            pass
        return SurveyResult(status="error", slug=slug, error=err_str)


class AnalysisRunResult(BaseModel):
    """The response `POST /{slug}/analyses/{analysis_id}/run` returns.

    Was a fully-resolved synchronous result — this handler used to run the
    survey inline (`await asyncio.to_thread(_run)`) and hand back `status`/
    `message`/`error` for a run that had, by the time this returns, already
    finished. It never wrote an activity_log entry at all, and this model
    carried no `activity_id`. `/next`'s shared `rerun()` (app.js) calls this
    route the same way it calls the repo route
    (`resource_explorer/web/static/re-api.js`'s `runAnalysis`), reads
    `started.activity_id`, and polls `GET /api/activity/{activity_id}` — so
    `activity_id` came back `undefined`, that GET 404'd with "Activity entry
    not found", and the run's own real result (which had already succeeded)
    was thrown away and reported as a failure. Reproduced live via
    `db_activity_signals`'s "Is this database alive…" Questions-checklist
    card, but not specific to it — every analysis_id in
    `DATABASE_SURVEYOR_STEP_MAP` (and every `db_derived` id) went through
    this same handler.

    Matches `projects.py`'s `run_single_analysis` or `run_stage_batch`'s
    response shape (`{"status": "started", "activity_id": ..., "run_id":
    ...}`) as closely as this route's own response model allows: `status` is
    now `"started"` on success, and `activity_id`/`run_id` are populated so
    the frontend's existing poll works unchanged. `slug`/`analysis_id` are
    kept for compatibility with `tests/test_database_analysis_run_route.py`'s
    pre-existing assertions and any other reader of this response.
    """
    status: str  # "started" | "error" (validation failures still raise HTTPException)
    slug: str
    analysis_id: str
    message: str = ""
    error: str | None = None
    activity_id: str = ""
    run_id: str = ""
    #: Set only for a run with a credential typed for that run (brief section 8): who it ran as.
    ran_as: dict | None = None


class AnalysisRunRequest(BaseModel):
    """Optional body of `POST /{slug}/analyses/{analysis_id}/run`. Both fields empty (or no body)
    is the ordinary enqueue with the stored credential. A password makes it a single in-process run
    with that credential, kept in memory for the call (never queued, never stored)."""
    db_user: str = ""
    db_pwd: str = ""


@router.post("/{slug}/analyses/{analysis_id}/run", response_model=AnalysisRunResult)
async def run_single_database_analysis(slug: str, analysis_id: str,
                                       body: AnalysisRunRequest | None = None) -> AnalysisRunResult:
    """Runs only the DatabaseSurveyor step(s) one named local database
    analysis needs, not the whole schema+statistics+views survey every
    time — the per-card "Run" action in Analysis/Assessment (database
    per-card dispatch fix, D6 prerequisite, repo-scope-narrowing-funnel
    plan). Egeria-native entries (egeria_db_survey) and Discovery Survey
    Definitions are not handled here — those already have their own
    dedicated dispatch paths (trigger_survey_by_guid / run_survey_definition
    via scheduler.py's _run_db_survey); this route is local-survey-only,
    mirroring scheduler.py's _run_local_db_survey.

    Two local shapes now, not one: the DatabaseSurveyor step path below, and
    the zero-fetch `db_derived` path (Phase 1 slice 9), which reads stored
    rows and so takes neither a step nor credentials.

    **Enqueues; does not run** (activity-tracking fix — see AnalysisRunResult's
    docstring for the bug this closes). Matches `projects.py`'s
    `run_single_analysis`: validation stays synchronous (an unmapped/unknown
    analysis_id, or missing credentials, is still a 400 here rather than a
    queued row that fails later in the worker), a real 'running' activity
    entry is written up front via `log_analysis_run` (already generic across
    entity_type — no change needed there), and the actual work is handed to
    the run queue's `database_analysis_run` kind
    (`run_queue.py::_handle_database_analysis_run` ->
    `workflows.analysis.execute_and_record_database_analysis`), which writes
    the terminal status onto the same activity entry when it finishes.

    The two branches get the same activity tracking, but not the same
    dispatch: `db_derived` reads stored rows only (no connection opened, no
    credentials needed — see db_derived.py's own module docstring) and was
    already fast enough to run inline before this fix, so it is *still*
    queued here for consistency and because a database that has never been
    reachable must not be treated specially by this route — but see
    `workflows.analysis.run_database_analysis` for confirmation it does no
    fetch of its own. The DATABASE_SURVEYOR_STEP_MAP branch opens a real
    connection and can legitimately take a while (the same shape that made
    the repo path's `architecture_recovery` worth backgrounding), so it is
    the one this fix is actually for.
    """
    from resource_explorer.activity_logger import log_analysis_run
    from resource_explorer.registry import ProjectRegistry
    from resource_explorer.run_queue import requested_by as _requested_by
    from resource_explorer.surveyors.database.database_surveyor import (
        database_analysis_has_runner,
    )
    from resource_explorer.surveyors.database.db_derived import DB_DERIVED_ANALYSES

    registry = ProjectRegistry()
    db = _get_database_for_use(registry, slug)
    if not db:
        raise HTTPException(status_code=404, detail=f"Database '{slug}' not found")

    if body is not None and body.db_pwd:
        return _start_override_analysis(registry, db, slug, analysis_id, body)

    # Validation stays synchronous — an unknown/unmapped analysis_id, or
    # missing credentials, must be a 400 here, not a queued row that fails a
    # minute later in a different process where nobody is looking (same rule
    # projects.py's run_single_analysis follows).
    if analysis_id in DB_DERIVED_ANALYSES:
        pass  # zero-fetch — no credentials check needed
    elif not database_analysis_has_runner(analysis_id):
        raise HTTPException(
            status_code=400,
            detail=f"Analysis '{analysis_id}' has no local survey step(s) mapped — "
                   "either it's Egeria-native/publish (use the appropriate dedicated "
                   "action instead) or an unknown id.",
        )
    elif not db.db_user or not db.db_password:
        raise HTTPException(
            status_code=400,
            detail="No stored database credentials — register the database with "
                   "db_user/db_password, or run a full survey with credentials, first.",
        )

    activity_id = log_analysis_run(
        registry, "database", slug, db.display_name, "running",
        f"Running '{analysis_id}' on {slug}…", analysis_id, published=None,
    )
    run_id = registry.enqueue_run(
        "database_analysis_run", {"slug": slug, "analysis_id": analysis_id},
        result_ref=activity_id, requested_by=_requested_by(),
    )
    log.info("enqueued database_analysis_run %s for %s/%s (activity %s)",
             run_id, slug, analysis_id, activity_id)

    return AnalysisRunResult(
        status="started", slug=slug, analysis_id=analysis_id,
        activity_id=activity_id, run_id=run_id,
    )


def _start_override_analysis(registry, db, slug: str, analysis_id: str,
                             body: AnalysisRunRequest) -> AnalysisRunResult:
    """One analysis with a credential typed for this run only (brief section 8, owner-approved).

    **Not enqueued.** The run queue refuses to persist a credential and this run must not reach it
    (nor Prefect): it runs on a thread in THIS process, on RE's own engine, the password held in
    memory for the call. No retry; the row says so. The password is in no row, log line or response.
    """
    import json

    from resource_explorer.activity_logger import log_analysis_run
    from resource_explorer.run_reconciler import process_identity
    from resource_explorer.surveyors.database.database_surveyor import database_analysis_has_runner
    from resource_explorer.surveyors.database.db_derived import DB_DERIVED_ANALYSES
    from resource_explorer.workflows.analysis import (
        NOT_RETRIED_WORDS,
        OVERRIDE_SCOPE,
        execute_override_database_analysis,
    )
    from resource_explorer.workflows.survey_definition import start_in_process

    if not body.db_user:
        raise HTTPException(
            status_code=400,
            detail="A credential for this run needs both a user and a password; the password alone is not enough.")
    if analysis_id in DB_DERIVED_ANALYSES:
        raise HTTPException(
            status_code=400,
            detail=f"Analysis '{analysis_id}' reads stored rows and opens no connection, so a credential "
                   "for this run would be used for nothing; run it without one.")
    if not database_analysis_has_runner(analysis_id):
        raise HTTPException(status_code=400, detail=f"Analysis '{analysis_id}' has no local survey step(s) mapped.")

    ran_as = {"user": body.db_user, "scope": OVERRIDE_SCOPE}
    activity_id = log_analysis_run(
        registry, "database", slug, db.display_name, "running",
        f"Running '{analysis_id}' on {slug} as {body.db_user} ({OVERRIDE_SCOPE})…", analysis_id,
        published=None, runner=process_identity(),
        extra={"ran_as": ran_as, "not_retried": NOT_RETRIED_WORDS})
    start_in_process(
        execute_override_database_analysis, slug, analysis_id, activity_id, body.db_user, body.db_pwd,
        name=f"analysis-override-{activity_id}")
    log.info("started database analysis override run for %s/%s as %s (activity %s)",
             slug, analysis_id, body.db_user, activity_id)
    return AnalysisRunResult(
        status="started", slug=slug, analysis_id=analysis_id, activity_id=activity_id, run_id="",
        ran_as=ran_as)


@router.delete("/{slug}")
async def remove_database(slug: str) -> dict:
    """Remove a database from the registry."""
    from resource_explorer.registry import ProjectRegistry
    
    registry = ProjectRegistry()
    database = registry.get_database(slug, allow_unreadable=True)
    if not database:
        raise HTTPException(status_code=404, detail=f"Database '{slug}' not found")
    
    registry.remove_database(slug)
    return {"removed": slug}


@router.get("/{slug}/surveys")
async def get_database_surveys(slug: str, include_invalid: bool = False,
                               slim: bool = False) -> list[dict]:
    """Get survey history for a database.

    Rows marked invalid (false-zero repair, `invalid_at` / `invalid_reason`) are
    excluded by default. `?include_invalid=true` returns them too, so the classic
    UI's "show invalid" toggle can show WHY a remembered survey is not counted.
    """
    from resource_explorer.registry import ProjectRegistry

    registry = ProjectRegistry()
    database = registry.get_database(slug, allow_unreadable=True)
    if not database:
        raise HTTPException(status_code=404, detail=f"Database '{slug}' not found")

    if slim:
        # `slim=true`: dates, counts, source and invalid marks without the
        # `survey_data` blob (the /next history table reads nothing from it).
        rows = registry.get_database_surveys(
            slug, include_invalid=include_invalid, with_survey_data=False)
        return [{k: v for k, v in r.items() if k != "survey_data"} for r in rows]
    return registry.get_database_surveys(slug, include_invalid=include_invalid)


@router.get("/{slug}/views")
async def get_database_views(slug: str) -> dict:
    """The views of the latest valid survey, read-only (PI-040).

    `state` is `measured` (the survey carries a `views` list, possibly empty),
    `not_measured` (it carries none, or the stored blob cannot be read: the
    reason says which) or `never_surveyed`. An empty list is only ever the
    first of these, so "no views" is never confused with "views not looked at".
    """
    import json as _json
    from resource_explorer.registry import ProjectRegistry

    registry = ProjectRegistry()
    if not registry.get_database(slug, allow_unreadable=True):
        raise HTTPException(status_code=404, detail=f"Database '{slug}' not found")
    row = registry.get_latest_database_survey(slug)
    if not row:
        return {"state": "never_surveyed", "run": None, "views": [],
                "reason": "no survey has run for this database"}
    run = {"surveyed_at": row.get("surveyed_at"), "source": row.get("source")}
    try:
        blob = _json.loads(row.get("survey_data") or "{}")
    except (TypeError, ValueError):
        return {"state": "not_measured", "run": run, "views": [],
                "reason": "the stored survey could not be read"}
    views = blob.get("views") if isinstance(blob, dict) else None
    if not isinstance(views, list):
        return {"state": "not_measured", "run": run, "views": [],
                "reason": "this survey did not run the sql_analysis step, so views were not analyzed"}
    # The PII keyword source of the run these views came from (`DatabaseSurveyor._store_results`);
    # "" for a survey stored before it was recorded.
    kw = blob.get("pii_keyword_source") if isinstance(blob.get("pii_keyword_source"), dict) else {}
    return {"state": "measured", "run": run, "reason": "", "keyword_source": kw.get("label") or "",
            "views": [
        {"kind": v.get("kind") or "view",
         "schema": v.get("schema") or v.get("schema_name") or "",
         "name": v.get("name") or v.get("view_name") or "",
         "definition": v.get("definition") or v.get("view_definition") or "",
         "dependencies": v.get("dependencies") or v.get("depends_on") or [],
         "complexity": v.get("complexity") if isinstance(v.get("complexity"), dict) else {},
         "lineage": v.get("lineage") if isinstance(v.get("lineage"), dict) else {}}
        for v in views if isinstance(v, dict)]}


@router.get("/{slug}/egeria-surveys", response_model=list[EgeriaSurveyReportRow])
async def get_database_egeria_surveys(slug: str) -> list[EgeriaSurveyReportRow]:
    """List SurveyReports that actually exist in Egeria right now for this database
    (a live read, not the local registry cache) — includes reports produced by
    Egeria's own native async survey engine, which the local /surveys endpoint
    never sees."""
    from resource_explorer.registry import ProjectRegistry
    from resource_explorer.surveyors.database.egeria_database_surveyor import (
        EgeriaDatabaseSurveyor,
        EgeriaDatabaseSurveyorError,
    )

    registry = ProjectRegistry()
    database = registry.get_database(slug, allow_unreadable=True)
    if not database:
        raise HTTPException(status_code=404, detail=f"Database '{slug}' not found")
    if not database.egeria_asset_guid:
        return []  # not yet cataloged in Egeria — nothing to walk from

    surveyor = EgeriaDatabaseSurveyor()
    try:
        reports = await asyncio.to_thread(surveyor.get_survey_reports_by_guid, database.egeria_asset_guid)
    except EgeriaDatabaseSurveyorError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    return [EgeriaSurveyReportRow(**r) for r in reports]


@router.get("/{slug}/egeria-surveys/{report_guid}/annotations", response_model=list[EgeriaAnnotationItem])
async def get_database_egeria_annotations(slug: str, report_guid: str) -> list[EgeriaAnnotationItem]:
    """Fetch all annotations for a specific live Egeria SurveyReport, by the
    report's own GUID — works regardless of which engine (RE or Egeria's native
    survey) produced the report, since it walks the real ReportedAnnotation
    relationship rather than guessing a naming convention."""
    from resource_explorer.registry import ProjectRegistry
    from resource_explorer.surveyors.database.egeria_database_surveyor import (
        EgeriaDatabaseSurveyor,
        EgeriaDatabaseSurveyorError,
    )

    registry = ProjectRegistry()
    database = registry.get_database(slug, allow_unreadable=True)
    if not database:
        raise HTTPException(status_code=404, detail=f"Database '{slug}' not found")

    surveyor = EgeriaDatabaseSurveyor()
    try:
        annotations = await asyncio.to_thread(surveyor.get_annotations_by_report_guid, report_guid)
    except EgeriaDatabaseSurveyorError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    return build_annotation_items(EgeriaAnnotationItem, annotations)


class PublishRequest(BaseModel):
    """Request body for publishing a database survey to Egeria.

    All fields are optional — stored values on the DatabaseEntity are used as defaults.
    Pass fields only to override stored values for a single call.
    """
    egeria_url: str | None = None
    egeria_server: str | None = None
    #: Ignored (owner, 2026-10-09): accepted only so an older caller is not refused; logged.
    egeria_user: str | None = None
    egeria_password: str | None = None
    db_user: str = ""
    db_pwd: str = ""


class PublishResult(BaseModel):
    """Result of publishing a database survey to Egeria."""
    status: str
    slug: str
    server_guid: str | None = None     # Egeria PostgreSQL server element GUID
    asset_guid: str | None = None      # Egeria database element GUID
    report_guid: str | None = None     # Egeria survey action GUID
    annotation_count: int | None = None
    server_display_name: str | None = None
    database_display_name: str | None = None
    # Native Egeria surveys this publish started, each backed by a persisted
    # step_runs proof row (registry.record_native_survey_submission). Empty
    # means NO survey was recorded; clients must not claim one started.
    survey_submissions: list[dict[str, Any]] = []
    error: str | None = None


async def _send_with_publish(registry, entity_type: str, slug: str, asset_guid: str) -> dict:
    """Brief F: what the page's ONE Publish sends after the resource's own publish, as the signed-in presser
    (`single_publish.send_with_publish`). Best-effort: each item's outcome is on its own row."""
    import asyncio

    from resource_explorer.egeria_identity import caller_user_id
    from resource_explorer.single_publish import send_with_publish

    try:
        return await asyncio.to_thread(send_with_publish, registry, entity_type, slug, asset_guid, caller_user_id())
    except Exception:  # noqa: BLE001
        log.warning("Could not send the presser's feedback for %s %s", entity_type, slug, exc_info=True)
        return {}


@router.post("/{slug}/publish", response_model=PublishResult)
async def publish_database_survey(slug: str, req: PublishRequest = PublishRequest()) -> PublishResult:
    """Publish the latest local database survey to Egeria.

    **Retired from the UI (Curate slice B, BRIEF-CURATE-CATALOGUE-COMMIT-DATABASES.md):**
    Classic's Publish button and modal are gone; "Catalogue" on a database's Curate pane
    (`POST /api/catalogue-scope/{slug}/commit`) is the publish now, with a manifest, a
    ZoneMembership written before any target, a survey limited to the chosen schemas, and a
    proof row behind every state. This route remains only for Classic API callers; RE itself
    never calls it (the survey-definition retry goes through the commit and its stored scope).
    It starts an UNSCOPED native survey and catalogues without a declared scope, so it must not
    be offered as the way to publish. Backlog: make it scope-aware, or remove it."""
    from resource_explorer.registry import ProjectRegistry
    from resource_explorer.web.routes._validation import ignore_egeria_credentials

    ignore_egeria_credentials(req, where="publish database")

    registry = ProjectRegistry()
    database = _get_database_for_use(registry, slug)
    if not database:
        raise HTTPException(status_code=404, detail=f"Database '{slug}' not found")
    # Brief Z round 2: the database's element, every operation a publish performs.
    from resource_explorer.web.routes.curate import authorize_resource_write
    await authorize_resource_write(registry, "database", slug, publish=True)

    # The latest MEASURED row, never merely the latest row (false-zero
    # hotfix, 2026-09-30): a newer empty/egeria-published row must not be
    # pushed to Egeria as if it were the database's real inventory.
    measured = registry.latest_measured_database_survey(slug)
    if measured is None:
        raise HTTPException(
            status_code=404,
            detail=f"No measured survey data for '{slug}' — run a survey first")

    from resource_explorer.run_queue import requested_by
    submitted_by = requested_by()      # read HERE, in the request; threads get it passed

    def _do_publish() -> dict[str, Any]:
        import asyncio as _aio
        import json as _json
        from resource_explorer.surveyors.database.egeria_database_surveyor import EgeriaDatabaseSurveyor
        # pyegeria sync wrappers call asyncio.get_event_loop() — set one for this thread
        loop = _aio.new_event_loop()
        _aio.set_event_loop(loop)

        # Resolve: request overrides > stored values > env vars (inside EgeriaDatabaseSurveyor)
        resolved_db_user = req.db_user or database.db_user
        resolved_db_pwd  = req.db_pwd  or database.db_password

        if not resolved_db_user or not resolved_db_pwd:
            raise ValueError(
                "Database credentials are required to publish to Egeria. "
                "Store them at registration or supply db_user/db_pwd in the request."
            )

        from resource_explorer.egeria_clients import Caller

        # WHO: the signed-in person (Brief I); a typed or stored Egeria user/password is no
        # longer used to authenticate. WHICH Egeria: the request's or entity's URL/server.
        surveyor = EgeriaDatabaseSurveyor(
            platform_url=req.egeria_url or database.egeria_url or None,
            view_server=req.egeria_server or database.egeria_server or None,
            identity=Caller(),
        )

        latest = measured
        survey_data = _json.loads(latest.get("survey_data", "{}"))
        schema_info = survey_data.get("schema_info", {})
        statistics  = survey_data.get("statistics", {})

        try:
            result = surveyor.publish_local_survey(
                db_entity=database,
                schema_info=schema_info,
                schema_count=latest.get("schema_count", 0),
                table_count=latest.get("table_count", 0),
                column_count=latest.get("column_count", 0),
                surveyed_at=latest.get("surveyed_at", ""),
                registry=registry,
                db_user=resolved_db_user,
                db_pwd=resolved_db_pwd,
                statistics=statistics,
                submitted_by=submitted_by,
            )
        finally:
            loop.close()
            _aio.set_event_loop(None)
        return result

    try:
        result = await asyncio.to_thread(_do_publish)
        egeria_host = database.egeria_host or database.host
        server_dn = f"{egeria_host}:{database.port}"
        try:
            from resource_explorer.activity_logger import log_catalog
            log_catalog(
                registry,
                entity_type="database",
                entity_slug=slug,
                entity_name=database.display_name,
                entity_location=f"{database.host}:{database.port}/{database.database_name}",
                status="ok",
                summary=f"Published to Egeria: {database.database_name} on {server_dn}",
                items=[
                    {"kind": "PostgreSQLServer", "display_name": server_dn,
                     "qualified_name": f"PostgreSQLServer::{server_dn}",
                     "guid": result.get("server_guid", ""), "location": ""},
                    {"kind": "PostgreSQLDatabase", "display_name": database.database_name,
                     "qualified_name": f"PostgreSQLDatabase::{database.database_name}",
                     "guid": result.get("asset_guid", ""), "location": ""},
                ],
            )
        except Exception:
            pass
        # BRIEF-DATABASE-DOCUMENTATION-SOURCES.md slice 1: every locally
        # declared documentation source becomes an ExternalReference on the
        # asset this publish just created/updated. Best-effort — a doc
        # source failing to publish must not fail the database's own
        # publish, which is why publish_local_doc_sources reports rather
        # than raises per source.
        asset_guid = result.get("asset_guid") or ""
        if asset_guid:
            # Brief F round 3: the documentation sources go ONCE, inside send_with_publish (it publishes exactly
            # `single_publish.unpublished_doc_sources`), not here as well.
            # Brief F: the same press sends the tags and group (the presser checked per item) and the PRESSER's own
            # rating and notes, as them (never anyone else's).
            await _send_with_publish(registry, "database", slug, asset_guid)
        return PublishResult(
            status="ok",
            slug=slug,
            server_guid=result.get("server_guid"),
            asset_guid=result.get("asset_guid"),
            report_guid=result.get("report_guid"),
            annotation_count=result.get("annotation_count"),
            survey_submissions=result.get("survey_submissions") or [],
            server_display_name=server_dn,
            database_display_name=database.database_name,
        )
    except PermissionError:
        raise      # no caller / expired sign-in: the app's 401 handler (Brief I)
    except Exception as exc:
        from resource_explorer.egeria_clients import refusal
        from resource_explorer.surveyors.database.egeria_database_surveyor import ArchivedDatabaseNeedsConfirmation

        if isinstance(exc, ArchivedDatabaseNeedsConfirmation):
            # S19: nothing created; the person confirms the re-include through a Curate commit, never here.
            return PublishResult(status="needs_confirmation", slug=slug, error=str(exc))
        refused = refusal(exc)
        # Egeria refused this person: said as a refusal with Egeria's sentence (Brief I).
        err_str = f"refused by Egeria: {refused}" if refused is not None else str(exc)
        try:
            from resource_explorer.activity_logger import log_catalog
            log_catalog(
                registry,
                entity_type="database",
                entity_slug=slug,
                entity_name=database.display_name,
                entity_location=f"{database.host}:{database.port}/{database.database_name}",
                status="error",
                summary=f"Egeria publish failed: {err_str[:200]}",
                detail=err_str,
            )
        except Exception:
            pass
        return PublishResult(status="error", slug=slug, error=err_str)


@router.get("/{slug}/diff")
async def get_database_diff(slug: str) -> dict:
    """Compare the two most recent database survey runs.

    Reads the structured `database_tables` rows (design §5.7), not the
    `survey_data` JSON blob. Until 2026-09-20 this function re-parsed the blob
    on every call — the design doc cites this very line as why the structured
    tables were needed.

    Returns an empty dict when fewer than two runs exist.

    **Absence is reported, not silently rendered as "no change".** The old
    implementation caught every exception from blob parsing and returned an
    empty set, so an unparseable blob, a blob in an older shape and a database
    with genuinely no tables all produced the same answer: "±0 tables, none
    added, none removed". A run with no structured rows now says so via
    `table_diff_state`, rather than claiming nothing changed.
    """
    from resource_explorer.registry import (
        ProjectRegistry, STATE_MEASURED, STATE_EMPTY,
    )
    registry = ProjectRegistry()
    surveys = registry.get_database_surveys(slug)
    if len(surveys) < 2:
        return {}

    curr = surveys[0]
    prev = surveys[1]

    def _tables_for(survey: dict) -> tuple[set[str], str, str]:
        """Qualified table names for one run, plus how well we know them.

        Returns (names, state, note). `state` is STATE_MEASURED only when
        this run actually has structured rows; anything else means the set is
        not a usable basis for a diff, and the caller must not present it as
        one. The state logic is shared with the database chart routes
        (`db_chart_data.section_state`) so the two cannot disagree.
        """
        from resource_explorer.db_chart_data import section_state
        rows = registry.query_detail_rows(
            "database_tables", slug,
            survey.get("surveyed_at") or "", survey.get("source") or None,
        )
        names = {
            f"{r.get('schema_name') or ''}.{r.get('table_name') or ''}"
            for r in rows
        }
        state, note = section_state(registry, slug, survey, "tables", len(rows))
        return names, state, note

    curr_tables, curr_state, curr_note = _tables_for(curr)
    prev_tables, prev_state, prev_note = _tables_for(prev)

    # Only diff two sides that were both genuinely measured. Subtracting a set
    # we do not have from one we do would report every table in the measured
    # run as newly added — a confident wrong answer, and the exact failure the
    # blob version produced whenever a parse failed.
    comparable = curr_state in (STATE_MEASURED, STATE_EMPTY) and prev_state in (
        STATE_MEASURED, STATE_EMPTY
    )
    table_diff_state = STATE_MEASURED if comparable else "not_comparable"

    result = {
        "prev_date":       prev["surveyed_at"],
        "curr_date":       curr["surveyed_at"],
        "deltas": {
            "schemas": (curr.get("schema_count") or 0) - (prev.get("schema_count") or 0),
            "tables":  (curr.get("table_count")  or 0) - (prev.get("table_count")  or 0),
            "columns": (curr.get("column_count") or 0) - (prev.get("column_count") or 0),
        },
        "new_tables":     sorted(curr_tables - prev_tables) if comparable else [],
        "removed_tables": sorted(prev_tables - curr_tables) if comparable else [],
        "table_diff_state": table_diff_state,
        "curr_table_state": curr_state,
        "prev_table_state": prev_state,
    }
    note = curr_note or prev_note
    if not comparable and note:
        result["table_diff_note"] = note
    return result
