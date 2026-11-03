"""Database server management endpoints — register, list, discover databases."""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter()


class ServerRegistration(BaseModel):
    slug: str
    display_name: str
    db_type: str = "postgresql"
    host: str
    port: int = 5432
    description: str = ""
    db_user: str = ""
    db_password: str = ""
    egeria_host: str = ""
    egeria_url: str = ""
    egeria_server: str = ""
    #: Ignored (owner, 2026-10-09): accepted only so an older caller is not refused; logged.
    egeria_user: str = ""
    egeria_password: str = ""
    group_slug: str = ""


class ServerSummary(BaseModel):
    slug: str
    display_name: str
    db_type: str
    host: str
    port: int
    description: str
    db_user: str
    egeria_host: str
    egeria_url: str
    egeria_server: str
    status: str
    registered_at: str
    group_slug: str = ""
    databases: list[dict] = []  # from databases table (server_slug FK)
    # A saved source remembers its last run. None = never run (not the same as
    # "ran and found nothing", which is last_run_candidate_count == 0). The
    # candidate set itself stays server-side; only its size is exposed here.
    last_run_at: str | None = None
    last_run_candidate_count: int | None = None


class DiscoveredDatabase(BaseModel):
    """One candidate database, as a source returned it.

    Every fact is either a measured value or an explicit None ("not read"):
    nothing here is defaulted to 0 or ''. The two exceptions are deliberate,
    and are measurements: `description == ""` means the server was asked and
    none is set; `can_connect` is the measured CONNECT privilege.
    """
    name: str
    key: str = ""               # resource_key('database', 'host:port/name')
    address: str = ""           # host:port/name, as the CSV contract writes it
    server_slug: str | None = None   # the saved source; None for a one-off run
    can_connect: bool = True
    size_pretty: str | None = None   # None = not read with this credential
    size_bytes: int | None = None
    owner: str | None = None
    description: str | None = None   # "" = measured empty ("none set"); None = not read
    encoding: str | None = None
    is_registered: bool = False      # already in our databases table?
    registered_slug: str | None = None
    verdict: dict | None = None      # prior verdict keyed by resource_key, or None
    is_new: bool | None = None       # None = no previous run to compare with
    # Only for an already-registered row: the registry's credential_status
    # ("ok" | "none" | "unreadable"); None for an unregistered candidate.
    credential_status: str | None = None


class SourceRun(BaseModel):
    """A saved source's Run: the candidates plus what changed since last time."""
    server_slug: str
    run_at: str
    previous_run_at: str | None = None   # None = first run of this source
    first_run: bool
    candidate_count: int
    new_count: int | None = None         # None on a first run: nothing to compare with
    candidates: list[DiscoveredDatabase]


def _safe_error(exc: Exception, *secrets: str) -> str:
    """An exception message with any credential removed. A driver message can
    echo the connection string; the password must never reach a response."""
    msg = str(exc)
    for secret in secrets:
        if secret:
            msg = msg.replace(secret, "***")
    return msg


def _build_candidates(registry, host: str, port: int, listed: list[dict],
                      server_slug: str | None,
                      previous_keys: list[str] | None) -> list[DiscoveredDatabase]:
    """Turn `list_databases()` rows into candidate rows: registered flag, prior
    verdict (keyed by resource_key, so "ignored" is remembered across runs and
    before any registration), and — when a previous run exists — which are new."""
    from resource_explorer.batch_io import resource_key

    listed_dbs = registry.list_databases()
    registered = {
        resource_key("database", f"{d.host}:{d.port}/{d.database_name}"): d.slug
        for d in listed_dbs
    }
    cred_status = {d.slug: getattr(d, "credential_status", None) for d in listed_dbs}
    keyed = [(db, resource_key("database", f"{host}:{port}/{db['name']}")) for db in listed]
    verdicts = registry.get_dispositions_for_entities("database", [k for _, k in keyed])
    # A registered database's verdict lives under its slug (the entity key once
    # registered); fall back to it so an already-registered row still shows it.
    by_slug = registry.get_dispositions_for_entities(
        "database", [registered[k] for _, k in keyed if k in registered])
    prev = set(previous_keys) if previous_keys is not None else None

    out = []
    for db, key in keyed:
        reg_slug = registered.get(key)
        verdict = verdicts.get(key) or (by_slug.get(reg_slug) if reg_slug else None)
        out.append(DiscoveredDatabase(
            name=db["name"],
            key=key,
            address=f"{host}:{port}/{db['name']}",
            server_slug=server_slug,
            can_connect=bool(db.get("can_connect", True)),
            size_pretty=db.get("size_pretty"),
            size_bytes=db.get("size_bytes"),
            owner=db.get("owner"),
            description=db.get("description"),
            encoding=db.get("encoding"),
            is_registered=reg_slug is not None,
            registered_slug=reg_slug,
            credential_status=cred_status.get(reg_slug) if reg_slug else None,
            verdict=({
                "disposition": verdict.get("disposition") or "undecided",
                "reason": verdict.get("reason") or "",
                "decided_at": verdict.get("decided_at") or "",
            } if verdict else None),
            is_new=(key not in prev) if prev is not None else None,
        ))
    return out


@router.get("/", response_model=list[ServerSummary])
async def list_servers():
    from resource_explorer.registry import ProjectRegistry
    registry = ProjectRegistry()
    servers = registry.list_servers()
    result = []
    for srv in servers:
        dbs = registry.list_databases(server_slug=srv.slug)
        result.append(ServerSummary(
            slug=srv.slug,
            display_name=srv.display_name,
            db_type=srv.db_type,
            host=srv.host,
            port=srv.port,
            description=srv.description,
            db_user=srv.db_user,
            egeria_host=srv.egeria_host,
            egeria_url=srv.egeria_url,
            egeria_server=srv.egeria_server,
            status=srv.status.value,
            registered_at=srv.registered_at,
            group_slug=srv.group_slug or "",
            last_run_at=srv.last_run_at,
            last_run_candidate_count=(
                len(srv.last_run_candidates) if srv.last_run_candidates is not None else None),
            databases=[
                {
                    "slug": d.slug,
                    "display_name": d.display_name,
                    "database_name": d.database_name,
                    "last_surveyed_at": d.last_surveyed_at,
                    "status": d.status.value,
                    "egeria_host": d.egeria_host or "",
                }
                for d in dbs
            ],
        ))
    return result


@router.post("/register", response_model=ServerSummary)
async def register_server(req: ServerRegistration):
    from resource_explorer.egeria_host import default_egeria_host
    from resource_explorer.registry import DatabaseServer, ProjectRegistry
    from resource_explorer.web.routes._validation import ignore_egeria_credentials

    ignore_egeria_credentials(req, where="register database server")
    registry = ProjectRegistry()
    if registry.get_server(req.slug):
        raise HTTPException(400, f"Server '{req.slug}' already exists")
    server = DatabaseServer(
        slug=req.slug,
        display_name=req.display_name,
        db_type=req.db_type,
        host=req.host,
        port=req.port,
        description=req.description,
        db_user=req.db_user,
        db_password=req.db_password,
        egeria_host=default_egeria_host(req.host, req.egeria_host),
        egeria_url=req.egeria_url,
        egeria_server=req.egeria_server,
        group_slug=req.group_slug,
    )
    registry.register_server(server)
    return ServerSummary(
        slug=server.slug,
        display_name=server.display_name,
        db_type=server.db_type,
        host=server.host,
        port=server.port,
        description=server.description,
        db_user=server.db_user,
        egeria_host=server.egeria_host,
        egeria_url=server.egeria_url,
        egeria_server=server.egeria_server,
        status=server.status.value,
        registered_at=server.registered_at,
        group_slug=server.group_slug or "",
    )


class ServerEgeriaHostUpdate(BaseModel):
    egeria_host: str = ""
    #: When set, databases of this server whose egeria_host still equals this old value take the new one.
    #: A database with any other value (its own override) is never touched.
    carry_databases_from: str | None = None


@router.patch("/{slug}/egeria-host")
async def update_server_egeria_host(slug: str, req: ServerEgeriaHostUpdate) -> dict:
    """Change ONLY this server's egeria_host. `carriable` lists its databases that still carry the old value;
    nothing is carried unless `carry_databases_from` names that old value, and each carried database passes
    the same write check as a database edit (one refusal, 403, and nothing is changed)."""
    from resource_explorer.egeria_host import NOTE_IN_EGERIA, check_egeria_host, default_egeria_host
    from resource_explorer.registry import ProjectRegistry
    from resource_explorer.web.routes.curate import authorize_resource_write

    registry = ProjectRegistry()
    server = registry.get_server(slug)
    if not server:
        raise HTTPException(404, f"Server '{slug}' not found")
    try:
        value = default_egeria_host(server.host, check_egeria_host(req.egeria_host))
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None
    previous = server.egeria_host or ""
    old = previous if req.carry_databases_from is None else req.carry_databases_from.strip()
    candidates = [d for d in registry.list_databases(server_slug=slug)
                  if (d.egeria_host or "") == old and old != value]
    carriable = [{"slug": d.slug, "in_egeria": bool(d.egeria_asset_guid or "")} for d in candidates]
    carried: list[dict] = []
    if req.carry_databases_from is not None:
        for d in candidates:
            await authorize_resource_write(registry, "database", d.slug, write="publish")
        carried, carriable = carriable, []
    registry.update_egeria_host("server", slug, value)
    for c in carried:
        registry.update_egeria_host("database", c["slug"], value)
    return {"slug": server.slug, "previous": previous, "egeria_host": value, "carriable": carriable,
            "carried": carried,
            "note": NOTE_IN_EGERIA if any(c["in_egeria"] for c in carried) else ""}


@router.delete("/{slug}")
async def remove_server(slug: str):
    from resource_explorer.registry import ProjectRegistry
    registry = ProjectRegistry()
    if not registry.get_server(slug):
        raise HTTPException(404, f"Server '{slug}' not found")
    registry.remove_server(slug)
    return {"removed": slug}


@router.post("/{slug}/run", response_model=SourceRun)
async def run_source(slug: str):
    """Run a saved source: discover on it, say what is new since its last run,
    then remember this run's candidate set for the next one.

    "New" is measured against the stored previous set, never inferred from the
    branch taken: `previous_run_at is None` means this source had never run, and
    then nothing is "new" (`new_count` is None), because there is nothing to
    compare with. A run that fails to connect stores nothing, so the previous
    run stays the baseline.
    """
    from datetime import datetime

    from resource_explorer.registry import ProjectRegistry
    from resource_explorer.surveyors.database.connection import server_connection
    registry = ProjectRegistry()
    server = registry.get_server(slug)
    if not server:
        raise HTTPException(404, f"Server '{slug}' not found")
    if not server.db_user:
        raise HTTPException(
            400,
            "Server has no stored credentials — update the server registration first",
        )

    def _discover():
        with server_connection(
            server.host, server.port, server.db_user, server.db_password, server.db_type
        ) as conn:
            return conn.list_databases()

    try:
        listed = await asyncio.to_thread(_discover)
    except Exception as exc:
        raise HTTPException(
            500, f"Could not connect to server: {_safe_error(exc, server.db_password)}") from exc

    candidates = _build_candidates(
        registry, server.host, server.port, listed, server.slug, server.last_run_candidates)
    run_at = datetime.utcnow().isoformat(timespec="seconds")
    first_run = server.last_run_candidates is None
    registry.record_server_run(slug, run_at, [c.key for c in candidates])
    return SourceRun(
        server_slug=server.slug,
        run_at=run_at,
        previous_run_at=None if first_run else server.last_run_at,
        first_run=first_run,
        candidate_count=len(candidates),
        new_count=None if first_run else sum(1 for c in candidates if c.is_new),
        candidates=candidates,
    )


class InlineDiscoverRequest(BaseModel):
    """A one-off discover: connection details typed into the dialog, nothing
    registered. The password is used for this one connection and is never
    stored, logged or echoed; 'save as a source' is the separate register call."""
    host: str
    port: int = 5432
    db_user: str
    db_password: str = ""
    db_type: str = "postgresql"


@router.post("/_discover-inline")
async def discover_inline(req: InlineDiscoverRequest) -> dict:
    """Discover on a server that is not registered (the dialog's "Discover on a
    server" tab). Returns candidates with `server_slug: null`: they cannot be
    registered until the server is saved as a source."""
    from resource_explorer.registry import ProjectRegistry
    from resource_explorer.surveyors.database.connection import server_connection

    def _discover():
        with server_connection(req.host, req.port, req.db_user, req.db_password, req.db_type) as conn:
            return conn.list_databases()

    try:
        listed = await asyncio.to_thread(_discover)
    except Exception as exc:
        raise HTTPException(
            500, f"Could not connect to server: {_safe_error(exc, req.db_password)}") from exc
    return {
        "host": req.host,
        "port": req.port,
        "candidates": [c.model_dump() for c in _build_candidates(
            ProjectRegistry(), req.host, req.port, listed, None, None)],
    }


class TestConnectionRequest(BaseModel):
    """Optional overrides for connection test — uses stored values if omitted."""
    host: str = ""
    port: int = 0
    db_user: str = ""
    db_password: str = ""


class InlineTestRequest(BaseModel):
    """Inline connection test — no registered server required."""
    host: str
    port: int = 5432
    db_user: str
    db_password: str = ""
    db_type: str = "postgresql"


@router.post("/_test-inline")
async def test_connection_inline(req: InlineTestRequest):
    """Test a database server connection using inline credentials (no registration needed)."""
    from resource_explorer.surveyors.database.connection import server_connection

    def _test():
        with server_connection(req.host, req.port, req.db_user, req.db_password, req.db_type) as conn:
            rows = conn.execute_query("SELECT version()")
            version = rows[0]["version"] if rows else "connected"
            db_rows = conn.list_databases()
            return {"version": version, "database_count": len(db_rows),
                    "connectable_count": sum(1 for d in db_rows if d["can_connect"])}

    try:
        result = await asyncio.to_thread(_test)
        return {
            "status": "ok",
            "host": req.host,
            "port": req.port,
            "server_version": result["version"],
            "database_count": result["database_count"],
            "connectable_count": result["connectable_count"],
        }
    except Exception as exc:
        return {"status": "error", "error": _safe_error(exc, req.db_password)}


@router.post("/{slug}/test")
async def test_server_connection(slug: str, req: TestConnectionRequest = TestConnectionRequest()):
    """Test connectivity to a registered server.  Returns ok + server_version on success."""
    from resource_explorer.registry import ProjectRegistry
    from resource_explorer.surveyors.database.connection import server_connection

    registry = ProjectRegistry()
    server = registry.get_server(slug)
    if not server:
        raise HTTPException(404, f"Server '{slug}' not found")

    host     = req.host     or server.host
    port     = req.port     or server.port
    db_user  = req.db_user  or server.db_user
    db_pwd   = req.db_password or server.db_password

    if not db_user:
        raise HTTPException(400, "No credentials stored — add a username and password to the server registration")

    def _test():
        with server_connection(host, port, db_user, db_pwd, server.db_type) as conn:
            rows = conn.execute_query("SELECT version()")
            version = rows[0]["version"] if rows else "connected"
            db_rows = conn.list_databases()
            return {"version": version, "database_count": len(db_rows),
                    "connectable_count": sum(1 for d in db_rows if d["can_connect"])}

    try:
        result = await asyncio.to_thread(_test)
        return {
            "status": "ok",
            "host": host,
            "port": port,
            "server_version": result["version"],
            "database_count": result["database_count"],
            "connectable_count": result["connectable_count"],
        }
    except Exception as exc:
        return {"status": "error", "error": _safe_error(exc, db_pwd)}


@router.get("/{slug}")
async def get_server(slug: str) -> ServerSummary:
    """Get details for a specific server."""
    from resource_explorer.registry import ProjectRegistry
    registry = ProjectRegistry()
    server = registry.get_server(slug)
    if not server:
        raise HTTPException(404, f"Server '{slug}' not found")
    dbs = registry.list_databases(server_slug=slug)
    return ServerSummary(
        slug=server.slug, display_name=server.display_name, db_type=server.db_type,
        host=server.host, port=server.port, description=server.description,
        db_user=server.db_user, egeria_host=server.egeria_host,
        egeria_url=server.egeria_url, egeria_server=server.egeria_server,
        status=server.status.value,
        registered_at=server.registered_at,
        last_run_at=server.last_run_at,
        last_run_candidate_count=(
            len(server.last_run_candidates) if server.last_run_candidates is not None else None),
        databases=[{"slug": d.slug, "display_name": d.display_name,
                    "database_name": d.database_name, "last_surveyed_at": d.last_surveyed_at,
                    "status": d.status.value, "egeria_host": d.egeria_host or ""} for d in dbs],
    )


@router.post("/{slug}/add-database")
async def add_database_from_server(slug: str, database_name: str, display_name: str = ""):
    """Register a specific database from this server into the databases table."""
    from resource_explorer.registry import ProjectRegistry
    registry = ProjectRegistry()
    server = registry.get_server(slug)
    if not server:
        raise HTTPException(404, f"Server '{slug}' not found")

    from resource_explorer.batch_io import build_database_entity

    # One builder shared with the CSV import (batch_io.apply_import), so a
    # database registered from a file is the same row as one registered here.
    db = build_database_entity(server, database_name, display_name)
    db_slug = db.slug
    if registry.get_database(db_slug, allow_unreadable=True):
        raise HTTPException(400, f"Database '{db_slug}' already registered")
    registry.register_database(db)
    # The slug the registry STORED ('_' for '-'), not the one asked for. The
    # dialog puts this into an investigation's scope and a group assignment, and
    # a scope member under 'a-b' points at nothing when the database is 'a_b'.
    return {"slug": registry._normalize_slug(db_slug), "database_name": database_name,
            "server_slug": slug}
