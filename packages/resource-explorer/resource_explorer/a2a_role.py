"""The `a2a` process role — every agent on one port, behind one bearer token.

`docs/runtime-architecture-plan.md` §2 names five process roles and says of
this one:

    RE already has one: `agentstack_server.py` runs each specialist agent
    (orchestrator, stats, code, docs, health, compare, integration) as its own
    A2A endpoint on ports 8080 to 8086 via `resource-explorer serve`. It has
    **no authentication at all** today and its one-port-per-agent layout is
    awkward in compose. It becomes a role: one service, one port, agents routed
    by path, the same Egeria bearer token (§4) accepted on every call ... and
    an agent card published per app.

This module is that role. `run_a2a(host, port, stop_event)` follows
`worker.py`'s shape: it blocks until the stop event is set, shuts down within a
bound, and the CLI wraps it with SIGTERM/SIGINT handlers and the shared
SIGUSR1 thread dump.

Routing: why a Starlette/FastAPI root and not the SDK
-----------------------------------------------------
The agentstack SDK **cannot** host more than one agent on one server.
`Server.agent()` raises ``ValueError("Server can have only one agent.")`` on a
second registration, and `Server.serve()` builds and owns its own uvicorn — one
agent, one port, by construction. (RE design rule 8 has said so since Project
Explorer; this is that rule re-confirmed against agentstack-sdk 0.7.1, not
assumed from it.)

What the SDK *does* support, one layer down, is building an agent's ASGI app
without the server around it: `agentstack_sdk.server.app.create_app(agent, ...)`
returns a plain FastAPI application (the A2A REST app, with the A2A JSON-RPC app
already mounted at `/jsonrpc`). Those mount under a path prefix like any other
ASGI app. So the layout is:

    /                             the orchestrator — the default agent
    /.well-known/agent-card.json  its card
    /.well-known/agents.json      the index of all seven (this module's own)
    /whoami                       who the presented token says you are
    /agents/<name>/              each specialist's A2A REST surface
    /agents/<name>/v1/message:send          …its REST send endpoint
    /agents/<name>/jsonrpc/                 …its JSON-RPC endpoint
    /agents/<name>/.well-known/agent-card.json   …its card

The orchestrator is mounted twice, at `/agents/orchestrator` and at `/`, so it
is both addressable by name like every other agent and the default at the root.
It is one app object mounted at two paths, not two apps: two would mean two task
stores, and a task created through one URL would be invisible through the other.

Mount order matters: the `/` mount matches everything, so it is added last,
after the index route and the named mounts. Starlette resolves routes in the
order they were added.

Discovery
---------
`/.well-known/agents.json` is this module's own addition, not an A2A standard
path. The standard gives you one card per agent at each agent's own
`/.well-known/agent-card.json`; it gives a client no way to learn that six more
agents exist alongside the one it found. The index closes that: name, URL, card
URL, description, skills, the required auth scheme, RE's version, and the Egeria
platform this service is bound to — enough for a Portal or an external
orchestrator to decide which agent to call and how to authenticate to it,
in one fetch.

Both it and the per-agent cards are readable **without** a token; everything
else needs one. See `a2a_auth.is_public_path` for why.
"""
from __future__ import annotations

import asyncio
import logging
import os
import threading
from typing import Any

# Module level, not inside `build_app`, and this matters: `from __future__ import
# annotations` makes every annotation a string, and FastAPI resolves a route
# handler's annotations against its *module* globals. A `Request` imported
# inside the factory is invisible there, so `request: Request` was read as a
# required query parameter named "request" and /whoami 422'd. starlette's
# Request is the same object `fastapi.Request` re-exports, and importing it
# here costs the CLI nothing that fastapi would not already cost.
from starlette.requests import Request

log = logging.getLogger(__name__)

#: The role's default port. One port for the whole service, replacing the
#: 8080-8086 block. 8090 sits clear of that block so a stale one-port-per-agent
#: deployment and this one can coexist during a migration.
DEFAULT_A2A_PORT = 8090

#: Path prefix under which every named agent is mounted.
AGENT_PREFIX = "/agents"


def agent_path(name: str) -> str:
    return f"{AGENT_PREFIX}/{name}"


def _public_base_url(host: str, port: int) -> str:
    """The URL an *outside* caller uses to reach this service.

    `A2A_PUBLIC_URL` exists because the bind address is not the reachable
    address the moment this runs in a container: the role binds `0.0.0.0`, and
    a card advertising `http://0.0.0.0:8090` is a card nobody can follow.
    """
    override = (os.getenv("A2A_PUBLIC_URL") or "").strip()
    if override:
        return override.rstrip("/")
    advertised = "127.0.0.1" if host in {"0.0.0.0", "::", ""} else host
    return f"http://{advertised}:{port}"


def _re_version() -> str:
    try:
        from resource_explorer import __version__

        return __version__
    except Exception:  # pragma: no cover
        return "unknown"


# ── the application ─────────────────────────────────────────────────────


def build_app(
    host: str = "127.0.0.1",
    port: int = DEFAULT_A2A_PORT,
    settings: Any | None = None,
) -> Any:
    """Build the single-port A2A application. No server, no binding.

    Split from `run_a2a` so tests can drive the whole routing and auth surface
    through a `TestClient` without a socket.
    """
    from resource_explorer import _mcp_compat  # noqa: F401  -- must precede agentstack_sdk (mcp 2.x rename)
    from agentstack_sdk.server.app import create_app
    from agentstack_sdk.server.store.memory_context_store import InMemoryContextStore
    from fastapi import FastAPI
    from fastapi.responses import JSONResponse

    from resource_explorer.a2a_auth import (
        A2AAuthMiddleware,
        caller,
        settings_from_env,
    )
    from resource_explorer.agentstack_server import AGENT_NAMES, agent_factories

    settings = settings if settings is not None else settings_from_env()
    base_url = _public_base_url(host, port)
    paths = {name: agent_path(name) for name in AGENT_NAMES}

    root = FastAPI(
        title="Resource Explorer — A2A",
        version=_re_version(),
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )

    # Each agent gets its own context store: an A2A context is a conversation,
    # and a conversation with the docs agent is not a conversation with the
    # stats agent even when the same caller runs both.
    apps: dict[str, Any] = {}
    cards: dict[str, Any] = {}
    for name, factory in agent_factories(agent_paths=paths).items():
        context_store = InMemoryContextStore()
        agent = factory(context_store.modify_dependencies)
        # Set before create_app: it appends "/jsonrpc/" to `card.url` and
        # derives `additional_interfaces` from it, so the base has to be the
        # externally reachable one already.
        agent.card.url = base_url if name == "orchestrator" else f"{base_url}{paths[name]}"
        apps[name] = create_app(agent, context_store=context_store)
        cards[name] = agent.card

    def _index() -> dict[str, Any]:
        agents = []
        for name in AGENT_NAMES:
            card = cards[name]
            mount = "" if name == "orchestrator" else paths[name]
            agents.append(
                {
                    "name": name,
                    "title": card.name,
                    "description": card.description,
                    "version": card.version,
                    "url": f"{base_url}{mount}",
                    "card_url": f"{base_url}{mount}/.well-known/agent-card.json",
                    "endpoints": {
                        "rest_message_send": f"{base_url}{mount}/v1/message:send",
                        "jsonrpc": f"{base_url}{mount}/jsonrpc/",
                    },
                    "skills": [s.id for s in (card.skills or [])],
                    "default": name == "orchestrator",
                }
            )
        return {
            "service": "resource-explorer",
            "role": "a2a",
            "version": _re_version(),
            "base_url": base_url,
            "default_agent": "orchestrator",
            "authentication": {
                "scheme": "Bearer",
                "required": not settings.allow_anonymous,
                "accepts": [
                    "trellis-app-jwt",
                    "egeria-bearer-token",
                ],
                "description": (
                    "Send Authorization: Bearer <token>. The token may be a trellis "
                    "app JWT (issued by this deployment's login or by the Portal's "
                    "SSO exchange) or a raw Egeria bearer token for the Egeria this "
                    "service is bound to. Agent cards and this index are public (and "
                    "name no internal address); "
                    "every agent endpoint is not."
                ),
                "anonymous_allowed": settings.allow_anonymous,
            },
            "agents": agents,
        }

    @root.get("/.well-known/agents.json")
    async def agents_index():  # pragma: no cover - exercised via TestClient
        return JSONResponse(_index())

    @root.get("/health")
    async def health():  # pragma: no cover - exercised via TestClient
        return JSONResponse(
            {"status": "ok", "role": "a2a", "agents": list(AGENT_NAMES)}
        )

    @root.get("/whoami")
    async def whoami(request: Request):  # pragma: no cover
        """Echo the identity behind the presented token.

        Not decoration: it is the one authenticated endpoint that costs nothing
        to call, which makes it how an operator (or a test, or the live check in
        `docs/a2a.md`) tells "my token is wrong" from "the agent is slow".
        """
        identity = caller()
        return JSONResponse(
            identity.as_dict() if identity else {"user_id": None, "auth_source": None}
        )

    # Named mounts first, the default-at-root mount last — a mount at "/"
    # matches every path, so anything added after it is unreachable.
    for name in AGENT_NAMES:
        root.mount(paths[name], apps[name])
    root.mount("/", apps["orchestrator"])

    app: Any = A2AAuthMiddleware(root, settings)
    # Keep the FastAPI object reachable for tests and for anything that wants
    # to introspect the routes; the middleware is a plain ASGI wrapper.
    app.fastapi_app = root  # type: ignore[attr-defined]
    app.index = _index  # type: ignore[attr-defined]
    return app


# ── the role ────────────────────────────────────────────────────────────


def run_a2a(
    host: str = "0.0.0.0",
    port: int = DEFAULT_A2A_PORT,
    stop_event: threading.Event | None = None,
    shutdown_timeout: float = 10.0,
) -> None:
    """Run the `a2a` role. Blocks until `stop_event` is set.

    Shaped like `worker.run_worker`: the caller owns the stop event and the
    signal handlers, this function owns the bound. uvicorn's own signal
    handlers are disabled so the CLI's are the ones that fire — otherwise
    uvicorn would replace them for the duration of `serve()` and the role's
    stop path would depend on which handler happened to be installed.
    """
    import uvicorn

    from resource_explorer.a2a_auth import settings_from_env
    from resource_explorer.observability.logging_setup import uvicorn_log_config

    stop_event = stop_event if stop_event is not None else threading.Event()
    settings = settings_from_env()

    if settings.allow_anonymous:
        log.warning(
            "A2A_ALLOW_ANONYMOUS=true — every agent on this service answers "
            "WITHOUT a bearer token, and any Egeria work it does runs as the "
            "service account rather than as the caller. Local development "
            "only; never set this where the port is reachable by anything you "
            "would not hand an Egeria login to."
        )
    elif not settings.auth_configured:
        log.warning(
            "No RE_JWT_SECRET/TRELLIS_JWT_SECRET is set — trellis app JWTs "
            "cannot be verified, so this service accepts only raw Egeria "
            "bearer tokens validated against %s.",
            settings.egeria_platform_url or "(no Egeria configured)",
        )

    app = build_app(host=host, port=port, settings=settings)

    class _Server(uvicorn.Server):
        def install_signal_handlers(self) -> None:
            # The CLI owns SIGTERM/SIGINT; they set stop_event, which the
            # watcher below turns into a graceful uvicorn shutdown.
            return

    config = uvicorn.Config(
        app=app,
        host=host,
        port=port,
        log_config=uvicorn_log_config(),
        timeout_graceful_shutdown=max(1, int(shutdown_timeout)),
    )
    server = _Server(config)

    async def _watch_stop() -> None:
        while not stop_event.is_set():
            await asyncio.sleep(0.25)
        log.info("a2a role: stop requested, draining (bound %ss)", shutdown_timeout)
        server.should_exit = True

    async def _main() -> None:
        watcher = asyncio.create_task(_watch_stop())
        try:
            await server.serve()
        finally:
            watcher.cancel()

    log.info(
        "a2a role starting on %s:%s — agents at %s/<name>, orchestrator at /, "
        "index at /.well-known/agents.json",
        host, port, AGENT_PREFIX,
    )
    try:
        asyncio.run(_main())
    finally:
        stop_event.set()
        # Same reason `web` and `worker` do this: a shared-pool worker stuck in
        # pyegeria would otherwise be joined by the interpreter on the way out,
        # past every bound above.
        from resource_explorer.concurrency import shutdown as _pool_shutdown

        _pool_shutdown(wait=False)
        log.info("a2a role stopped")
