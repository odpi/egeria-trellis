"""FastAPI application — query and project management web interface."""
from __future__ import annotations


import logging
import os
import uuid
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from resource_explorer.web.routes import work_lists as work_lists_routes
from resource_explorer.web.routes import quality_batch as quality_batch_routes
from resource_explorer.web.routes import publish_table as publish_table_routes
from resource_explorer.web.routes import api_reference as api_reference_routes, analytics as analytics_routes, activity, aliases, auth as auth_routes, compile_context as compile_context_routes, analyses, automate, bootstrap as bootstrap_routes, context, catalogue_scope, quality_checks, curate, databases, first_index as first_index_routes, db_servers as db_servers_routes, diagrams, discovery, doc_sources, egeria, feedback, investigations, journal, logs as logs_routes, prefect_status, prerequisites as prerequisite_routes, project_context, outbox, projects, query, repair, runs as runs_routes, schedules, stats, webhook, filesystems, survey_definitions, native_surveys, open_data as open_data_routes
from resource_explorer.web.routes import table_preview as table_preview_routes
from resource_explorer.web.routes import value_profile as value_profile_routes
from resource_explorer.web.routes import table_results as table_results_routes


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


START_LINE_SECONDS = 5


async def start_line_within(seconds: float) -> str:
    """The archive-cascade start-up line, never holding start-up beyond `seconds`. The platform-origin read runs
    off-loop; on timeout the line says "platform: unreachable at start" and the block state is unchanged."""
    import asyncio as _asyncio

    from resource_explorer.catalogue_gateway import issue_117_start_line

    try:
        return await _asyncio.wait_for(_asyncio.to_thread(issue_117_start_line), seconds)
    except _asyncio.TimeoutError:
        return issue_117_start_line(lambda: "")


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
    from resource_explorer.config import check_public_url, log_llm_tier_config

    check_public_url()  # refuses to start on a malformed RE_PUBLIC_URL, quoting it (PublicUrlInvalid)
    log_llm_tier_config()  # the resolved tier/model/num_ctx/budgets; the lazy first-call line predates logging setup

    from resource_explorer.surveyors.prefect_adapter import alog_prefect_reachability_at_startup

    await alog_prefect_reachability_at_startup()
    import asyncio as _asyncio

    # on, or off with the lift's evidence, then the platform's origin beside the proven build (one read, off-loop)
    logging.getLogger(__name__).info("%s", await start_line_within(START_LINE_SECONDS))
    from resource_explorer.egeria_clients import log_daemon_identity_at_startup
    log_daemon_identity_at_startup()        # INFO: which source the daemon identity came from (Brief L)
    from resource_explorer.bootstrap import log_heal_identity_at_startup
    log_heal_identity_at_startup()          # who the heal applies Dr.Egeria as, and from where; warns when unset

    # Re-project stored credentials if a redeploy deleted the secrets file.
    # Non-fatal by construction (heal_missing never raises) and no network.
    from resource_explorer.omsecrets_reproject import heal_missing

    heal_missing()

    # Files step 2: what Egeria's platform container can read (configured pairs, then docker inspect, then the
    # quickstart compose file). Read once at start; never fails the start.
    try:
        from resource_explorer import egeria_mounts

        egeria_mounts.load_at_start()
    except Exception:  # noqa: BLE001
        logging.getLogger(__name__).warning("could not build the Egeria mount map", exc_info=True)

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


def _egeria_token_refused(request, exc):
    """F58: Egeria refused the signed-in person's token while the RE session is still valid (Egeria restarted since
    they signed in). 401 with the distinct marker: the shell branches on `error`, never on the words, and shows the
    sign-in form with the Egeria words — not the RE session's own `login_required`."""
    from resource_explorer.egeria_clients import EGERIA_TOKEN_REFUSED_CODE

    return JSONResponse(status_code=401, content={"error": EGERIA_TOKEN_REFUSED_CODE, "detail": str(exc)})


def _platform_not_allowed(request, exc):
    """Brief I: a resource names an Egeria platform RE is not configured for; nothing was sent."""
    return JSONResponse(status_code=403, content={"detail": str(exc), "refused_by": "platform"})


def _token_refusal_in(exc: BaseException):
    """F60: the `EgeriaTokenRefused` that ended this request, when there is one anywhere in what was raised — the
    exception itself, what it was raised `from`, or what was being handled when it was raised (`__context__`).
    `egeria_clients` raises the refusal at the person's client call; a route's own helpers then often wrap it
    (`EgeriaFileSystemSurveyorError(...) from exc`, `HTTPException(500, f"Publish failed: {exc}")` inside an
    `except Exception`), and each wrap would otherwise bury the one fact the person must hear — sign in again —
    under that route's words. Bounded, cycle-safe."""
    from resource_explorer.egeria_clients import EgeriaTokenRefused

    seen: set = set()
    e = exc
    for _ in range(12):
        if e is None or id(e) in seen:
            return None
        if isinstance(e, EgeriaTokenRefused):
            return e
        seen.add(id(e))
        e = e.__cause__ if e.__cause__ is not None else e.__context__
    return None


async def _http_exception_or_token_refused(request, exc):
    """F60: an HTTPException a route raised while handling Egeria's refusal of the person's token answers the
    401 marker (the refusal's own sentence), whatever status the route chose; every other HTTPException is
    FastAPI's default answer."""
    from fastapi.exception_handlers import http_exception_handler

    refused = _token_refusal_in(exc)
    if refused is not None:
        return _egeria_token_refused(request, refused)
    return await http_exception_handler(request, exc)


def _unhandled_or_token_refused(request, exc):
    """F60: an unhandled exception that carries the person's token refusal answers the 401 marker; anything else
    is the plain 500 Starlette would have sent (and Starlette still re-raises it to the server log)."""
    from starlette.responses import PlainTextResponse

    refused = _token_refusal_in(exc)
    if refused is not None:
        return _egeria_token_refused(request, refused)
    return PlainTextResponse("Internal Server Error", status_code=500)


def _install_egeria_identity_handlers() -> None:
    from starlette.exceptions import HTTPException as StarletteHTTPException

    from resource_explorer.egeria_clients import (
        CallerTokenExpired, EgeriaRefused, EgeriaTokenRefused, NoCallerIdentity, PlatformNotAllowed,
    )

    app.add_exception_handler(NoCallerIdentity, _egeria_identity_refused)
    app.add_exception_handler(CallerTokenExpired, _egeria_identity_refused)
    app.add_exception_handler(EgeriaRefused, _egeria_refused)
    app.add_exception_handler(EgeriaTokenRefused, _egeria_token_refused)
    app.add_exception_handler(PlatformNotAllowed, _platform_not_allowed)
    # F60: the refusal is answered the same way when a route wrapped it (see `_token_refusal_in`).
    app.add_exception_handler(StarletteHTTPException, _http_exception_or_token_refused)
    app.add_exception_handler(Exception, _unhandled_or_token_refused)


_install_egeria_identity_handlers()


#: The fields of a request-validation error a 422 keeps. FastAPI's default answer also carries `input` (the value that
#: failed, or the whole body when a field is missing) and `ctx`: a body holding a password (`db_pwd`, RE's own
#: override runs and the coordinated run's one-run credential) would be echoed back in the 422 and in anything that
#: logs it (DESIGN-COORDINATED-RUN-CREDENTIAL.md §2.2).
VALIDATION_ERROR_FIELDS = ("loc", "msg", "type")


#: The words a 422 says in place of a validation message that could carry a password (fix round 2): a validator's own
#: message may quote the value it refused.
VALIDATION_MSG_WITHHELD = "the value was not accepted (its message is not shown: it could carry a password)"


def _secret_named(name) -> bool:
    text = str(name or "").casefold()
    return "pwd" in text or "password" in text or "secret" in text


def _secret_values(body) -> list[str]:
    """Every value under a password-like key anywhere in the request body, as text (a number too)."""
    out: list[str] = []
    if isinstance(body, dict):
        for k, v in body.items():
            if _secret_named(k) and v not in (None, "") and not isinstance(v, (dict, list)):
                out.append(str(v))
            out.extend(_secret_values(v))
    elif isinstance(body, list):
        for v in body:
            out.extend(_secret_values(v))
    return out


def _validation_refused(request, exc):
    """Every 422 from request validation, app-wide: which field and why, never the value sent. A `msg` is withheld for
    a password-like field, and wherever it contains a password-like value of the body (a validator may quote it)."""
    from fastapi.encoders import jsonable_encoder

    secrets = _secret_values(getattr(exc, "body", None))
    errors = []
    for e in exc.errors() or []:
        if not isinstance(e, dict):
            continue
        kept = {k: e[k] for k in VALIDATION_ERROR_FIELDS if k in e}
        msg = str(kept.get("msg") or "")
        if any(_secret_named(part) for part in kept.get("loc") or ()) or any(v in msg for v in secrets):
            kept["msg"] = VALIDATION_MSG_WITHHELD
        errors.append(kept)
    return JSONResponse(status_code=422, content={"detail": jsonable_encoder(errors)})


def _install_validation_handler() -> None:
    from fastapi.exceptions import RequestValidationError

    app.add_exception_handler(RequestValidationError, _validation_refused)


_install_validation_handler()


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
app.include_router(table_preview_routes.router, prefix="/api/databases", tags=["table-preview"])
app.include_router(value_profile_routes.router, prefix="/api/databases", tags=["value-profile"])
app.include_router(table_results_routes.router, prefix="/api/table-results", tags=["table-results"])
app.include_router(filesystems.router, prefix="/api/filesystems", tags=["filesystems"])
app.include_router(open_data_routes.router, prefix="/api/open-data", tags=["open-data"])
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
app.include_router(publish_table_routes.router, prefix="/api/publish-table", tags=["publish-table"])
app.include_router(quality_checks.router, prefix="/api/quality-checks", tags=["quality-checks"])
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
app.include_router(first_index_routes.router, prefix="/api/first-index", tags=["first-index"])
app.include_router(runs_routes.router, prefix="/api/runs", tags=["runs"])
# Design §17.1 — "answering this needs X first; run it?" The plan is
# read-only; the run is the user's consent, and goes through the same
# executor (and so the same cost observation) as any other step.
app.include_router(prerequisite_routes.router, prefix="/api/prerequisites", tags=["prerequisites"])
app.include_router(work_lists_routes.router, prefix="/api/work-lists", tags=["work-lists"])
# Quality slice Q3: the DatabaseQualitySurvey across a work list, priced before it starts.
app.include_router(quality_batch_routes.router, prefix="/api/quality-batch", tags=["quality-batch"])

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
    periodic connectivity banner (see the shell's health check in next/shell-status.js)."""
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


_NEXT_SHELL = _STATIC / "next" / "index.html"


@app.get("/")
async def index() -> FileResponse:
    """The UI: the Next shell. `/next` serves the same file, kept so existing bookmarks work."""
    return FileResponse(_NEXT_SHELL)


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
    fragment arrive untouched.

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
