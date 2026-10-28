"""FastAPI application — query and project management web interface."""
from __future__ import annotations


import logging
import os
import uuid
from pathlib import Path
from typing import Optional

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from resource_explorer.config import WebUIConfig
from resource_explorer.web.routes import work_lists as work_lists_routes
from resource_explorer.web.routes import api_reference as api_reference_routes, analytics as analytics_routes, activity, aliases, auth as auth_routes, compile_context as compile_context_routes, analyses, automate, bootstrap as bootstrap_routes, context, catalogue_scope, curate, databases, db_servers as db_servers_routes, diagrams, discovery, doc_sources, egeria, feedback, investigations, journal, logs as logs_routes, prefect_status, prerequisites as prerequisite_routes, project_context, outbox, projects, query, repair, runs as runs_routes, schedules, stats, webhook, filesystems, survey_definitions, native_surveys


log = logging.getLogger(__name__)


def _embed_worker_enabled() -> bool:
    """Whether this web process also runs the worker role in-process.

    Read from the environment/config rather than passed in, because with
    `--workers N` uvicorn re-imports this module in each child process —
    a value the CLI held in a local would never reach them.
    `resource-explorer web` sets EXPLORER_EMBED_WORKER explicitly from
    its --embed-worker/--no-embed-worker flag; the default is True so
    `make dev`, an existing .env, and a bare
    `uvicorn resource_explorer.web.app:app` all keep the behaviour they
    had when the loops were started from this lifespan directly.
    """
    raw = os.environ.get("EXPLORER_EMBED_WORKER")
    if raw is not None:
        return raw.strip().lower() not in ("0", "false", "no", "off", "")
    try:
        from resource_explorer.config import get_config

        return bool(get_config().runtime.embed_worker)
    except Exception:
        return True


async def _lifespan(app: FastAPI):
    """Serve HTTP. Start background loops only when asked to embed one.

    Until 2026-09-04 this function started five things itself — the
    scheduler, the bootstrap monitor, the Egeria resync scanner, a
    survey-definition cache warm, and a one-shot orphaned-run
    reconciliation — which made the web server the only way to run any
    of them, and made N uvicorn workers mean N copies of each. They live
    in resource_explorer/worker.py now (docs/runtime-architecture-plan.md
    §2); this lifespan starts a thread only if EXPLORER_EMBED_WORKER says
    to, and even then leader election decides whether that thread's loops
    actually fire. See docs/process-model.md.
    """
    from resource_explorer.config import get_llm_tier_config

    get_llm_tier_config()  # logs the resolved tier/model/num_ctx/budget once

    from resource_explorer.surveyors.prefect_adapter import alog_prefect_reachability_at_startup

    await alog_prefect_reachability_at_startup()
    from resource_explorer.catalogue_gateway import issue_117_state_line
    logging.getLogger(__name__).info("%s", issue_117_state_line())   # on, or off with the clearance text
    from resource_explorer.egeria_clients import log_daemon_identity_at_startup
    log_daemon_identity_at_startup()        # INFO: which source the daemon identity came from (Brief L)

    # Re-project stored credentials if a redeploy deleted the secrets file.
    # Non-fatal by construction (heal_missing never raises) and no network.
    from resource_explorer.omsecrets_reproject import heal_missing

    heal_missing()

    worker_stop = None
    if _embed_worker_enabled():
        from resource_explorer.worker import start_embedded_worker

        _thread, worker_stop = start_embedded_worker()
        log.info("embedded worker role started in this web process")
    else:
        log.info(
            "no embedded worker in this web process (EXPLORER_EMBED_WORKER "
            "disabled) — background loops require `resource-explorer worker`"
        )
    yield
    if worker_stop is not None:
        worker_stop.set()


# Configure logging at import, so `uvicorn resource_explorer.web.app:app` run
# directly gets the same setup as `resource-explorer web`. Idempotent, so the
# CLI calling it first makes this a no-op rather than a second set of handlers.
from resource_explorer.observability.logging_setup import configure_logging as _configure_logging

_configure_logging()

app = FastAPI(
    title="Resource Explorer",
    description="Multi-agent RAG assistant for GitHub projects and databases",
    version="0.1.0",
    lifespan=_lifespan,
)


def _egeria_identity_refused(request, exc):
    """Brief I: no caller, or an expired caller token, is a 401 with the sentence — never the
    service account. `refused_by` lets the page show it as a sign-in cue rather than an error."""
    return JSONResponse(status_code=401, content={"detail": str(exc), "refused_by": "sign-in"})


def _egeria_refused(request, exc):
    """Brief I: Egeria's security refused the signed-in person. Shown as a refusal (403, Egeria's
    own sentence, `refused_by: egeria`), never retried as the service account. Raised by a route
    as `egeria_clients.EgeriaRefused` — pyegeria's own exception types are not registered here,
    because importing pyegeria at app load breaks the uvloop boot."""
    return JSONResponse(status_code=403, content={"detail": str(exc), "refused_by": "egeria"})


def _platform_not_allowed(request, exc):
    """Brief I: a resource names an Egeria platform RE is not configured for; nothing was sent."""
    return JSONResponse(status_code=403, content={"detail": str(exc), "refused_by": "platform"})


def _install_egeria_identity_handlers() -> None:
    from resource_explorer.egeria_clients import (
        CallerTokenExpired, EgeriaRefused, NoCallerIdentity, PlatformNotAllowed,
    )

    app.add_exception_handler(NoCallerIdentity, _egeria_identity_refused)
    app.add_exception_handler(CallerTokenExpired, _egeria_identity_refused)
    app.add_exception_handler(EgeriaRefused, _egeria_refused)
    app.add_exception_handler(PlatformNotAllowed, _platform_not_allowed)


_install_egeria_identity_handlers()


@app.middleware("http")
async def _phase_timing_middleware(request, call_next):
    """Per-request total wall time, logged at INFO as `phase_timing`.

    Diagnostic only — added for the 2026-09-29 per-request-latency
    investigation (PER-REQUEST-SERVER-LATENCY-IMPLEMENTED.md)
    and left in place since it is cheap (one perf_counter pair, one log
    line) and the next regression in this class needs exactly this data.
    Registered ahead of `_identity_middleware` below, which — per
    `_install_login_required_middleware`'s docstring on `add_middleware`'s
    `insert(0, ...)` + `reversed()` build order — makes `_identity_middleware`
    the OUTER layer and this one INNER: this timer starts after identity
    resolution, not before, so `total_ms` covers the route handler only
    (identity resolution is one JWT decode, negligible either way — see
    `_identity_middleware`'s own docstring). The pure-ASGI login gate and
    CORS wrap everything added via `@app.middleware`/`add_middleware` here
    regardless, so neither is included either way. The per-phase breakdown
    that actually found the root cause (`ProjectRegistry.__init__`
    re-running full schema verification on every construction) came from
    direct, isolated timing of that constructor, not from subdividing this
    middleware further — logged separately, see `ProjectRegistry.__init__`'s
    own timing below.
    """
    import time

    t0 = time.perf_counter()
    response = await call_next(request)
    elapsed_ms = (time.perf_counter() - t0) * 1000
    log.info(
        "phase_timing path=%s method=%s status=%s total_ms=%.1f",
        request.url.path, request.method, response.status_code, elapsed_ms,
    )
    return response


@app.middleware("http")
async def _identity_middleware(request, call_next):
    """Publish the signed-in caller for the duration of this request.

    Sets `a2a_auth.current_caller` — RE's **one** identity ContextVar, already
    set by the A2A middleware for agent traffic and by `use_identity()` for the
    CLI. Everything downstream (the publisher, the materializers, the run
    queue's `requested_by`, the registry's user scoping) asks that one
    question, so it does not matter which door a call came through.

    Reset in `finally`, so a handler that raises cannot leak an identity into
    whatever the worker thread serves next — the failure mode that makes a
    ContextVar-based identity worth writing carefully.

    Deliberately tolerant of an absent/invalid token: rejecting is
    `LoginRequiredMiddleware`'s job, and it runs outside this. A request that
    reaches here without a token is on the public allowlist, and the right
    thing to publish for it is "nobody".
    """
    from resource_explorer.a2a_auth import current_caller

    from resource_explorer.auth import get_current_user, identity_from_claims

    # Unguarded, deliberately: `get_current_user` already returns None rather
    # than raising for an absent, malformed or forged token — that IS the
    # tolerance this needs. A broad `except` here would additionally swallow a
    # genuine bug in identity resolution and serve the request as nobody,
    # which is the "we never measured this looks exactly like we measured
    # nothing" failure applied to authorization.
    claims = get_current_user(request)
    identity = identity_from_claims(claims) if claims is not None else None

    # A session for RE's daemon account is refused wherever it was minted (EA shares the JWT
    # secret; a pre-merge session may name it) — Brief L round 2, at identity RESOLUTION.
    from resource_explorer.egeria_clients import DaemonSignInRefused, refuse_daemon_sign_in

    if identity is not None:
        try:
            refuse_daemon_sign_in(identity.user_id)
        except DaemonSignInRefused as exc:
            from fastapi.responses import JSONResponse

            return JSONResponse(status_code=403, content={"detail": str(exc)})

    from resource_explorer.egeria_clients import client_scope

    reset = current_caller.set(identity)
    try:
        # One Egeria client per identity for this request (Brief I): sub-clients share one token.
        with client_scope():
            return await call_next(request)
    finally:
        current_caller.reset(reset)


def _install_login_required_middleware() -> None:
    """Install `trellis_auth.LoginRequiredMiddleware` — the shared login policy.

    **Ordering, measured rather than assumed.** Starlette's `add_middleware`
    does `user_middleware.insert(0, ...)` and the stack is then built by
    iterating `reversed(...)`, so the **last** middleware added ends up
    **outermost**. Verified empirically against this checkout's Starlette
    (two probe middlewares, the second-added logged first) rather than read
    off a docstring — the two readings are one `reversed()` apart and the
    wrong one puts the login gate inside the thing it is meant to gate.

    Hence the order in this file, bottom to top of the stack:

        _revalidate_experimental_assets  (added last → outermost)
          └── CORS
                └── LoginRequiredMiddleware   (this)
                      └── _identity_middleware (added first → innermost)
                            └── routes

    (`_revalidate_experimental_assets` is declared below the routers, so it
    is added last and therefore wraps everything. It only sets a response
    header on the way out, so its position does not matter to correctness —
    but it is listed here because a stack diagram that omits a layer is how
    the next person gets the ordering wrong.)

    * The login gate is outside `_identity_middleware`, so a rejected request
      never sets an identity at all.
    * CORS is outside the login gate, so a cross-origin 401 still carries its
      CORS headers. Without that the browser reports an opaque network error
      instead of "please sign in", and the login form never appears — the
      exact broken page this whole change exists to prevent.
    """
    from trellis_auth import LoginRequiredMiddleware

    from resource_explorer.auth import auth_config, get_policy

    app.add_middleware(
        LoginRequiredMiddleware, config=auth_config(), policy=get_policy(),
    )


_install_login_required_middleware()

# Added last, therefore outermost — see `_install_login_required_middleware`.
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

app.include_router(auth_routes.router, prefix="/api/auth", tags=["auth"])
app.include_router(query.router, prefix="/api/query", tags=["query"])
app.include_router(projects.router, prefix="/api/projects", tags=["projects"])
app.include_router(databases.router, prefix="/api/databases", tags=["databases"])
app.include_router(filesystems.router, prefix="/api/filesystems", tags=["filesystems"])
app.include_router(stats.router, prefix="/api/stats", tags=["stats"])
app.include_router(aliases.router, prefix="/api/aliases", tags=["aliases"])
app.include_router(egeria.router, prefix="/api/egeria", tags=["egeria"])
app.include_router(db_servers_routes.router, prefix="/api/db-servers", tags=["db-servers"])
app.include_router(webhook.router, prefix="/api", tags=["webhook"])
app.include_router(activity.router, prefix="/api/activity", tags=["activity"])
app.include_router(bootstrap_routes.router, prefix="/api/bootstrap", tags=["bootstrap"])
app.include_router(analyses.router, prefix="/api/analyses", tags=["analyses"])
app.include_router(analytics_routes.router, prefix="/api/analytics", tags=["analytics"])
app.include_router(context.router, prefix="/api/context", tags=["context"])
app.include_router(compile_context_routes.router, prefix="/api/context", tags=["context-compile"])
app.include_router(project_context.router, prefix="/api/project-context", tags=["project-context"])
app.include_router(automate.router, prefix="/api/automate", tags=["automate"])
app.include_router(curate.router, prefix="/api/curate", tags=["curate"])
app.include_router(catalogue_scope.router, prefix="/api/catalogue-scope", tags=["catalogue-scope"])
app.include_router(doc_sources.router, prefix="/api/doc-sources", tags=["doc-sources"])
app.include_router(api_reference_routes.router, prefix="/api/api-reference", tags=["api-reference"])
app.include_router(native_surveys.router, prefix="/api/native-surveys", tags=["native-surveys"])
app.include_router(schedules.router, prefix="/api/schedules", tags=["schedules"])
app.include_router(prefect_status.router, prefix="/api/prefect", tags=["prefect"])
app.include_router(survey_definitions.router, prefix="/api/survey-definitions", tags=["survey-definitions"])
app.include_router(outbox.router, prefix="/api/outbox", tags=["outbox"])
app.include_router(feedback.router, prefix="/api/feedback", tags=["feedback"])
app.include_router(journal.router, prefix="/api/journal", tags=["journal"])
app.include_router(discovery.router, prefix="/api/discovery", tags=["discovery"])
app.include_router(diagrams.router, prefix="/api/diagrams", tags=["diagrams"])
app.include_router(investigations.router, prefix="/api/investigations", tags=["investigations"])
app.include_router(repair.router, prefix="/api/admin/repair", tags=["repair"])
app.include_router(logs_routes.router, prefix="/api/logs", tags=["logs"])
app.include_router(runs_routes.router, prefix="/api/runs", tags=["runs"])
# Design §17.1 — "answering this needs X first; run it?" The plan is
# read-only; the run is the user's consent, and goes through the same
# executor (and so the same cost observation) as any other step.
app.include_router(prerequisite_routes.router, prefix="/api/prerequisites", tags=["prerequisites"])
app.include_router(work_lists_routes.router, prefix="/api/work-lists", tags=["work-lists"])

_STATIC = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=_STATIC), name="static")


@app.get("/health")
async def health() -> dict:
    """Pure liveness check — the process is up and answering HTTP, nothing
    more. Deliberately does NOT touch the database: returns 200 even when
    Postgres (and therefore almost every /api/ route) is unreachable, e.g.
    Docker not running after a reboot. Confirmed live — this genuinely
    happened and is exactly why /health/ready exists below: every /api/
    route 500'd while this kept returning 200, which is useless for
    detecting the actual problem. Frontend uses /health/ready instead."""
    return {"status": "ok"}


# A fresh token per server process: a page that sees it change knows the server restarted (and so what it can
# do may have changed: config flags, engine host) and re-reads what it shows. Not a secret, not an identity.
_BOOT_ID = uuid.uuid4().hex


@app.get("/health/ready")
async def health_ready() -> JSONResponse:
    """Readiness check — actually exercises the registry's database
    connection, so it fails the same way real /api/ routes do rather than
    reporting healthy while they 500. Backs the frontend's startup +
    periodic connectivity banner (see index.html's checkBackendHealth())."""
    from resource_explorer.registry import ProjectRegistry

    try:
        registry = ProjectRegistry()
        with registry._conn() as conn:
            conn.execute("SELECT 1")
    except Exception as exc:
        return JSONResponse(
            status_code=503,
            content={"status": "error", "database": "unreachable", "detail": str(exc), "boot_id": _BOOT_ID},
        )
    return JSONResponse(content={"status": "ok", "database": "ok", "boot_id": _BOOT_ID})


def _resolve_default_ui(raw: Optional[str]) -> str:
    """`next` unless the operator explicitly chose `classic`. An unrecognised
    value falls back to `next` with a warning rather than failing startup."""
    value = (raw or "").strip().lower()
    if value in ("", "next"):
        return "next"
    if value == "classic":
        return "classic"
    logging.getLogger(__name__).warning(
        "RE_DEFAULT_UI=%r is not 'next' or 'classic'; serving the Next UI at /", raw
    )
    return "next"


#: Read once at startup (RE_DEFAULT_UI, via config.WebUIConfig so `.env` counts).
_DEFAULT_UI = _resolve_default_ui(WebUIConfig().default_ui)

_CLASSIC_SHELL = _STATIC / "index.html"
_NEXT_SHELL = _STATIC / "next" / "index.html"


@app.get("/")
async def index() -> FileResponse:
    """The default UI: Next, unless RE_DEFAULT_UI=classic."""
    return FileResponse(_NEXT_SHELL if _DEFAULT_UI == "next" else _CLASSIC_SHELL)


@app.get("/classic")
async def classic_ui() -> FileResponse:
    """The Classic UI, explicitly. Same shell `/` served before Next became the default."""
    return FileResponse(_CLASSIC_SHELL)


@app.get("/admin/feedback")
async def admin_feedback() -> FileResponse:
    return FileResponse(_STATIC / "admin-feedback.html")


@app.middleware("http")
async def _revalidate_experimental_assets(request, call_next):
    """Make `/next`'s assets revalidate instead of being served from cache.

    `StaticFiles` sets no `Cache-Control`, so a browser applies heuristic
    freshness — and an ES module, once in a page's module map, is stickier
    still. The effect during this experiment was a tester (and the person
    building it) loading `app.js` against a CACHED `re-api.js`, which fails
    as `does not provide an export named ...` — an error that describes a
    stale cache but reads like a code bug, and which a plain reload does not
    clear.

    `no-cache` means "revalidate", not "do not store": the browser still
    caches and still gets a 304 when nothing changed. Scoped to the
    experimental UI's own files by exact prefix, so the rest of `/static`
    keeps whatever caching it has. Remove this along with `/next` if the
    experiment is dropped; if `/next` ever becomes the default, replace it
    with real cache-busting (a content hash in the filename) rather than
    keeping revalidation forever.
    """
    response = await call_next(request)
    path = request.url.path
    if path.startswith("/static/next/") or path == "/static/re-api.js":
        response.headers["Cache-Control"] = "no-cache"
    return response


@app.get("/next")
async def next_ui() -> FileResponse:
    """The Next UI at its original path, kept so existing bookmarks and links
    work. Served directly (not redirected) so query state and a Portal `#sso=`
    fragment arrive untouched; it also stays Next when RE_DEFAULT_UI=classic.

    Skin 1c, and the Questions pane rebuilt to report the ANSWER rather than
    the mechanism. The whole UI is a consumer of
    `/api/*`: this route adds no endpoint, touches no schema, and shares this
    app's session, so the experiment is additive and reversible — if it is a
    dead end, this function and `static/next/` go away together.

    Same shape as `/admin/feedback` above, which is the precedent for a second
    static page here. Its assets live under `/static/next/`, already public by
    the shared `/static/` prefix; the shell itself is listed in
    `RE_PUBLIC_PATHS` for the same reason `/` is.
    """
    return FileResponse(_NEXT_SHELL)
