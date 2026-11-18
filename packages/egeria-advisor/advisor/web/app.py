"""
Egeria Advisor Web UI — FastAPI application.

Endpoints:
  GET  /                  → index.html
  POST /api/query         → run a query, return result dict
  GET  /api/reports       → report spec catalog grouped by topic
  GET  /api/status        → system / MCP connection status
  POST /api/feedback      → record 👍 / 👎 on a response
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
import trellis_ui
from loguru import logger

# Cross-cutting helpers used by more than one route group live in
# advisor.web.shared (TC-5, BACKLOG.md) — imported, not redefined, so
# `advisor.web.app.<name>` keeps resolving for existing external lazy
# imports (advisor.rag_system imports `_intent_meta` this way).
from advisor.web.shared import (
    _STATIC,
    _intent_meta,  # noqa: F401 -- unused in this module, kept importable: advisor.rag_system imports it from here
)


def _cors_origin_regex() -> str:
    """
    localhost is always allowed (local dev). ADVISOR_EXTRA_CORS_ORIGINS (.env,
    comma-separated) adds extra exact origins — e.g. a Portal embedding this
    Advisor from a different origin. Same-origin browser access (the SPA served
    from the same host:port as the API) never needs this.
    """
    from advisor.config import settings
    patterns = [r"https?://localhost(:\d+)?"]
    extra = [o.strip() for o in settings.advisor_extra_cors_origins.split(",") if o.strip()]
    patterns.extend(re.escape(origin) for origin in extra)
    return "|".join(f"({p})" for p in patterns)


app = FastAPI(title="Egeria Advisor", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=_cors_origin_regex(),
    allow_methods=["*"],
    allow_headers=["*"],
    allow_credentials=True,
)
# The looks and the other pieces shared with Resource Explorer (packages/trellis-ui), at /static/trellis-ui/.
# Mounted FIRST: Starlette matches mounts in order, and "/static" would answer these paths with a 404.
trellis_ui.mount(app)
app.mount("/static", StaticFiles(directory=_STATIC), name="static")


def _install_login_required_middleware() -> None:
    """Install `trellis_auth.LoginRequiredMiddleware` — the shared login policy.

    **Ordering matters and is the reason this is a function rather than two
    inline lines.** Starlette applies `add_middleware` in reverse order: the
    *last* one added is the *outermost*. `_user_context_middleware` below is
    registered by its decorator at import time, after this call, so it ends up
    outside this one — which is what we want spelled out explicitly, because
    the requirement reads the other way round in prose ("install the login gate
    ahead of the user-context middleware", i.e. it must run *before* a handler
    does, not before the context middleware in the stack).

    The user-context middleware being outermost is harmless and slightly
    useful: it sets the ContextVar from whatever token is present and resets it
    in `finally`, so even a request this gate rejects leaves no identity behind.
    Nothing downstream of a 401 ever runs, so no handler can act on a
    half-authenticated context.

    Registered before the CORS middleware would be wrong in the other
    direction — a cross-origin 401 must still carry its CORS headers or the
    browser reports an opaque network error instead of "please sign in". CORS
    is added above, therefore ends up outside this, therefore decorates the
    401. That is the arrangement, and `test_login_required_middleware.py`
    pins it.
    """
    from trellis_auth import LoginRequiredMiddleware
    from advisor.auth import _base_config, get_policy

    app.add_middleware(
        LoginRequiredMiddleware,
        config=_base_config(),
        policy=get_policy(),
    )


#: Admin-only paths (Phase 0 S1, owner's decision 2026-11-10: one admin definition for RE and EA). A prefix, so a
#: route added under it is gated by default; plus the feedback triage reads and edits. NOT `POST /api/feedback`
#: (voting is for every signed-in user), and not `/admin` (the static page: every call it makes is gated).
ADMIN_PATH_PREFIXES = ("/api/admin/",)
ADMIN_EXACT_PATHS = ("/api/feedback/analysis",)  # A-F1: /api/feedback/v2 checks admin itself


def _install_admin_path_middleware() -> None:
    """Install `trellis_auth.AdminPathMiddleware` for the admin paths above.

    Added BEFORE the login middleware, so it sits INSIDE it (Starlette: the last added is the outermost): a
    request with no session gets the login 401, and this 403 answers only signed-in non-admins. The identity is
    EA's own `get_current_user` (its anonymous-mode stand-in counts as anonymous, so it is refused)."""
    from trellis_auth import AdminPathMiddleware
    from advisor.auth import ADMIN_ENV_PREFIX, get_current_user

    app.add_middleware(
        AdminPathMiddleware,
        app_prefix=ADMIN_ENV_PREFIX,
        current_user=get_current_user,
        prefixes=ADMIN_PATH_PREFIXES,
        exact=ADMIN_EXACT_PATHS,
    )


_install_admin_path_middleware()
_install_login_required_middleware()


@app.middleware("http")
async def _frame_ancestors_middleware(request: Request, call_next):
    """E1: who may frame EA. Every response says `frame-ancestors 'self' <ADVISOR_EMBED_ORIGINS>`, so only Studio
    (and EA itself) can embed it; before E1 anyone could. Registered after the login middleware, so it is outside it
    and a 401 carries the header too."""
    from advisor.web.embed import frame_ancestors_header
    response = await call_next(request)
    response.headers["Content-Security-Policy"] = frame_ancestors_header()
    return response


@app.middleware("http")
async def _user_context_middleware(request: Request, call_next):
    """
    Sets advisor.request_context's per-request ContextVar from this
    request's JWT (via advisor.auth.get_current_user), for every request —
    so any code reached from a route handler, no matter how many plain
    function calls deep (rag_system → plan_elicitor/report_spec_elicitor →
    the agents, in particular — see request_context.py's module docstring),
    can recover the signed-in user's identity without it being threaded
    through every intervening signature. Reset in `finally` so a handler
    that raises can't leak identity into whatever runs next.

    Mirrors the `user_id = None if not user or user.get("anonymous") else
    user.get("user_id") or user.get("sub")` extraction every namespaced
    route already does explicitly (those explicit computations still win —
    this only supplies the ambient default for code that doesn't have a
    `Request` to ask).
    """
    from advisor.auth import get_current_user
    from advisor.request_context import set_current_user, reset_current_user

    user = None
    try:
        user = get_current_user(request)
    except Exception:  # pragma: no cover - get_current_user itself never raises; defensive only
        logger.debug("_user_context_middleware: get_current_user failed", exc_info=True)
    user_id = None if not user or user.get("anonymous") else (user.get("user_id") or user.get("sub"))
    role = (user or {}).get("role")
    token = set_current_user(user_id, role)
    try:
        return await call_next(request)
    finally:
        reset_current_user(token)


from advisor.web.admin import router as _admin_router
app.include_router(_admin_router)

from advisor.web.embed import router as _embed_router
app.include_router(_embed_router)

from advisor.web.auth import router as _auth_router
app.include_router(_auth_router)

from advisor.web.feedback import router as _feedback_router
app.include_router(_feedback_router)

from advisor.web.query import router as _query_router
app.include_router(_query_router)

from advisor.web.plan_templates import router as _plan_templates_router
app.include_router(_plan_templates_router)

from advisor.web.sessions import router as _sessions_router
app.include_router(_sessions_router)

from advisor.web.templates import router as _templates_router
app.include_router(_templates_router)

from advisor.web.drafts import router as _drafts_router
app.include_router(_drafts_router)

from advisor.web.plans import router as _plans_router
app.include_router(_plans_router)

from advisor.web.reports import router as _reports_router
app.include_router(_reports_router)

from advisor.web.dashboards import router as _dashboards_router
app.include_router(_dashboards_router)


def _hardware_purposes() -> dict:
    """The device setting each of EA's models really runs with: its embeddings from
    advisor.yaml's `embeddings.device` (what advisor/embeddings.py reads; shipped as "auto"),
    Docling on auto. The report states these, not one generic choice."""
    from advisor.config import get_full_config

    return {"embeddings": get_full_config()["embeddings"].device, "docling": "auto"}


def _log_hardware_in_background(log) -> None:
    """The start-up hardware line (packages/trellis-accel): what the machine has, what this
    PyTorch can use, the device chosen and any finding that names a fix. Built off the startup
    path (importing torch takes seconds) and cached, so /health/hardware serves it without
    touching torch again. It reports; it never installs anything."""
    import threading

    def run():
        try:
            from trellis_accel import report_once, startup_line
            log.info(startup_line(report_once(_hardware_purposes())))
        except Exception as exc:  # noqa: BLE001 -- a report, never a reason to stop serving
            log.warning(f"hardware report could not be made: {exc}")

    threading.Thread(target=run, name="hardware-report", daemon=True).start()


def declare_multi_user_process() -> None:
    """EA serves many users: declare it to pyegeria's spec cache (spec-cache R-A, pyegeria 6.2.42), so a
    report-spec read made with no user's identity takes no fallback identity and sees no private spec."""
    from pyegeria.view.egeria_spec_cache import declare_multi_user
    declare_multi_user()


@app.on_event("startup")
async def _startup():
    """Ensure pgvector collection tables exist, then pre-warm the MCP agent
    in the background so the first report click is fast.

    **Restored 2026-09-07** — lost as an unintended casualty of TC-5 slice 1's
    bulk line-range deletion (BACKLOG.md), which correctly removed the
    _get_rag()/models/report-catalog block being moved to shared.py but also
    swept up this handler and _shutdown below, which were interleaved in the
    same range and had no replacement anywhere. Confirmed missing from the
    whole advisor/ package, not just this file — flagged by a peer session
    that rebuilt and redeployed the container and diffed pre/post-refactor
    startup behavior. No test caught it: nothing exercises this app's
    lifespan events, since every TC-5 verification used a bare
    `TestClient(app, ...)` rather than `with TestClient(app) as client:`.
    """
    import threading

    declare_multi_user_process()   # first: before any spec read, the background sync's included
    from advisor import spec_authoring_v2
    logger.info(spec_authoring_v2.flags_line())   # the effective values (re-base + fixes on by default)
    from advisor.config import warn_old_daemon_names, warn_server_identity_env
    warn_server_identity_env("web")   # TR-34: EGERIA_USER/EGERIA_USER_PASSWORD are a CLI identity, never a server's
    warn_old_daemon_names()           # TR-34: the service account's deprecated names, once
    # TR-12: fail closed, naming the variable, never its value: there is no built-in Postgres password any more.
    from advisor.config import require_pgvector_password
    require_pgvector_password()

    try:
        from advisor.vector_store_pg import PgVectorStore
        PgVectorStore().provision_schema()
    except Exception as exc:
        logger.warning(f"pgvector schema provisioning failed (queries needing missing collections will error): {exc}")

    def _warm():
        try:
            from advisor.report_pipeline import get_report_pipeline
            get_report_pipeline()._ensure_agent()
            logger.info("MCP agent pre-warmed on startup")
        except Exception as exc:
            logger.warning(f"MCP pre-warm failed (reports will initialize on first use): {exc}")

    threading.Thread(target=_warm, daemon=True).start()
    _log_hardware_in_background(logger)   # trellis-accel: device, build, findings

    # Report specs held in Egeria: sync EA's consumer folder into the EGERIA tier in the
    # background, retrying while Egeria starts. Never blocks startup (advisor/egeria_spec_sync.py).
    try:
        from advisor.egeria_spec_sync import start_background_sync
        start_background_sync()
    except Exception as exc:
        logger.warning(f"Report spec sync from Egeria not started: {exc}")


@app.on_event("shutdown")
async def _shutdown():
    """
    Terminate the MCP agent's subprocess(es) so they don't outlive this
    process. Without this, every uvicorn --reload restart during development
    orphans the MCP server subprocess instead of killing it — confirmed live
    2026-07-10: ~50 orphaned mcp_server.py processes had accumulated over two
    weeks of iterative development, one per reload, with no shutdown handler
    ever calling shutdown_mcp_agent() to reap them.

    Restored 2026-09-07 alongside _startup above — see that docstring.
    """
    try:
        from advisor.mcp_agent import shutdown_mcp_agent
        await shutdown_mcp_agent()
        logger.info("MCP agent shut down cleanly")
    except Exception as exc:
        logger.warning(f"MCP agent shutdown failed: {exc}")


# ── routes ─────────────────────────────────────────────────────────────────────

@app.get("/")
async def index() -> FileResponse:
    return FileResponse(_STATIC / "index.html")


@app.get("/health")
async def health() -> Dict[str, Any]:
    """Liveness, plus the RESOLVED report-spec authoring flags (on/off only, no settings values), so a
    post-deploy check can read them from the running service without its log."""
    from advisor import spec_authoring_v2
    return {"status": "ok", "spec_authoring": spec_authoring_v2.flags_state()}



@app.get("/health/hardware")
async def health_hardware() -> dict:
    """The hardware report made at start-up (packages/trellis-accel): the machine's GPUs, the
    installed PyTorch, the device chosen and findings that name a fix. Signed-in only: it is not
    in the public paths (GPU names and versions are not for anyone who can reach the port),
    unlike /health."""
    import asyncio

    from trellis_accel import report_once
    return await asyncio.to_thread(lambda: report_once(_hardware_purposes()))


@app.get("/api/status")
async def system_status() -> Dict[str, Any]:
    """The MCP servers EA is configured to use: {state, message, mcp_servers}.

    state: ok | none (no server enabled) | not_configured (no config file) | read_failed. It reports
    configuration only; it does not connect to any server or check the RAG system.
    """
    mcp_status: List[Dict[str, Any]] = []
    cfg_path = Path(__file__).parent.parent / "configdata" / "mcp_servers.json"
    if not cfg_path.exists():
        return {"state": "not_configured", "mcp_servers": [],
                "message": f"No MCP server configuration found at {cfg_path.name}."}
    try:
        cfg = json.loads(cfg_path.read_text())
        for name, srv in cfg.get("mcpServers", {}).items():
            if name.startswith("_"):
                continue
            mcp_status.append({
                "name": name,
                "enabled": srv.get("enabled", True),
                "transport": srv.get("transport", "stdio"),
                "description": srv.get("description", ""),
            })
    except Exception as exc:
        logger.warning(f"Status check failed: {exc}")
        return {"state": "read_failed", "mcp_servers": [],
                "message": f"Could not read the MCP server configuration: {exc}"}
    enabled = [s["name"] for s in mcp_status if s["enabled"]]
    if not enabled:
        return {"state": "none", "mcp_servers": mcp_status, "message": "No MCP server is enabled."}
    return {"state": "ok", "mcp_servers": mcp_status,
            "message": f"{len(enabled)} MCP server(s) configured: {', '.join(enabled)}. "
                       "Configured, not checked for a connection."}


@app.get("/api/actions")
async def list_actions() -> Dict[str, Any]:
    """Return all known Dr.Egeria commands grouped by family.

    Used by the Plan Editor command picker modal to populate the command catalog.
    Each entry: {name, family, aliases, in_catalog}
    """
    from advisor.command_keyword_index import get_command_keyword_index
    return {"families": get_command_keyword_index().all_commands()}


@app.get("/api/egeria/zones")
async def get_governance_zones(request: Request) -> Dict[str, Any]:
    """Every governance zone name the signed-in user can see in Egeria.

    {state, zones, count, message}: state is ok | capped | none | not_configured | not_signed_in |
    refused | unreachable | read_failed (advisor.egeria_context). count 0 with state "none" means
    Egeria answered and holds none; any other state with count 0 means Egeria was not read.

    Requires login: this is a live Egeria read and must be attributable to the signed-in user, not
    a shared service account.
    """
    from advisor.auth import require_egeria_user, get_egeria_credentials
    require_egeria_user(request)
    egeria_credentials = get_egeria_credentials(request)
    from advisor.egeria_context import EgeriaContext
    answer = EgeriaContext(egeria_credentials=egeria_credentials).governance_zones_answer()
    return {**answer, "count": len(answer["zones"])}


@app.get("/api/perspectives")
async def list_perspectives(request: Request) -> Dict[str, Any]:
    """Available perspectives, and where they came from.

    {perspectives, source, message}: source "egeria" ("from Egeria") or "built_in" ("built-in list:
    Egeria not reached: <reason>"). A due Egeria refresh runs as the signed-in caller.
    """
    from advisor.auth import get_egeria_credentials
    from advisor.perspective_manager import get_all, status
    perspectives = get_all(get_egeria_credentials(request))
    return {"perspectives": perspectives, **status()}
