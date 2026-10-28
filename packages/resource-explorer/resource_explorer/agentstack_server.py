"""AgentStack agent definitions — the seven specialists, and how they are hosted.

**Superseded as a hosting layer, 2026-09-04.** This module used to be both the
agent definitions *and* the server: `_serve_all()` started seven independent
`agentstack_sdk.server.Server` instances on ports 8080-8086, unauthenticated.
`docs/runtime-architecture-plan.md` §2 replaced that with the `a2a` process
role — **one service, one port, agents routed by path, a bearer token required
on every call** — which lives in `resource_explorer/a2a_role.py`. This module
now owns the agent definitions and exposes them through `agent_factories()`;
that is what the role mounts.

The one-port-per-agent path is kept below (`run(all_agents=True)`) only so
nothing that imported it breaks. Nothing in the CLI reaches it any more.

Why the role could not simply ask this SDK to host seven agents
---------------------------------------------------------------
`Server.agent()` raises ``ValueError("Server can have only one agent.")`` and
``Server.serve()`` owns a uvicorn of its own — one agent, one server, one port,
by construction (RE design rule 8). The supported multi-agent shape is one
level down: `agentstack_sdk.server.app.create_app(agent, ...)` returns a plain
FastAPI app per agent, which mounts under a path prefix like any other ASGI
app. See `a2a_role.py` for the mounting.

Agents that require a project scope (stats, health) use the A2A input_required
pattern: if they cannot infer the project from the query, they ask the user and
resume when the user replies.
"""
from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from uuid import uuid4

from typing import Any

from a2a.types import AgentSkill, DataPart, Message, Part, TaskState, TaskStatus, TextPart
from resource_explorer import _mcp_compat  # noqa: F401  -- must precede agentstack_sdk (mcp 2.x rename)
from agentstack_sdk.server import Server
from agentstack_sdk.server.context import RunContext


# Port offsets from base_port for each agent
_AGENT_OFFSETS: dict[str, int] = {
    "orchestrator": 0,
    "stats": 1,
    "code": 2,
    "docs": 3,
    "health": 4,
    "compare": 5,
    "integration": 6,
}


def _text(message: Message) -> str:
    """Extract plain text from an A2A Message."""
    parts = []
    for part in message.parts or []:
        root = part.root
        if hasattr(root, "text"):
            parts.append(root.text)
    return " ".join(parts).strip()


def _project_scope(query: str) -> tuple[str, str | None]:
    """Split 'project:<slug> <question>' into (question, slug). Returns (query, None) if no prefix."""
    if query.lower().startswith("project:"):
        head, _, rest = query.partition(" ")
        return rest.strip(), head.split(":", 1)[1].strip()
    return query, None


def _choice_status(question: str, example: str, query: str, named: list[str], context: RunContext) -> TaskStatus:
    """An input_required status offering ranked repo AND database candidates.

    The text names them for a client that only shows text; the data part carries the same choices
    as {"kind": "resource_choices", "choices": [{slug, name, type}]} for one that can render them,
    the way the web chat renders its chips.
    """
    try:
        from resource_explorer.resource_chips import candidate_chips

        choices = candidate_chips(query, named)
    except Exception:
        choices = []
    if choices:
        listing = ", ".join(f"{c['slug']} ({c['type']})" for c in choices)
    else:
        listing = "none registered"
    text = f"{question} Candidates: {listing}. Reply with the names, e.g. {example}"
    parts = [Part(root=TextPart(text=text))]
    if choices:
        parts.append(Part(root=DataPart(data={"kind": "resource_choices", "choices": choices})))
    return TaskStatus(
        state=TaskState.input_required,
        message=Message(
            role="agent",
            parts=parts,
            messageId=str(uuid4()),
            taskId=context.task_id,
            contextId=context.context_id,
        ),
    )


def _ask_project_status(context: RunContext) -> TaskStatus:
    """Build an input_required TaskStatus listing available projects."""
    try:
        from resource_explorer.registry import ProjectRegistry
        slugs = [p.slug for p in ProjectRegistry().list_all()]
        available = ", ".join(slugs) if slugs else "none registered"
    except Exception:
        available = "unknown"
    text = (
        f"Which project are you asking about? "
        f"Available projects: {available}. "
        f"Reply with the project name or 'project:<slug> <question>'."
    )
    return TaskStatus(
        state=TaskState.input_required,
        message=Message(
            role="agent",
            parts=[Part(root=TextPart(text=text))],
            messageId=str(uuid4()),
            taskId=context.task_id,
            contextId=context.context_id,
        ),
    )


def _slug_from_reply(reply: Message | None) -> str | None:
    """Extract a project slug from the user's clarification reply."""
    if not reply:
        return None
    from resource_explorer.agents.base import BaseExplorerAgent
    text = _text(reply)
    query, explicit = _project_scope(text)
    if explicit:
        return explicit
    # Use inference on the reply text
    class _Probe(BaseExplorerAgent):
        def system_prompt(self): return ""
        def tools(self): return []
        def handle(self, *a, **kw): return ""
    return _Probe()._infer_project_slug(text)


# ── per-agent Server factories ─────────────────────────────────────────────────

def _stats_server() -> Server:
    server = Server()

    @server.agent(
        name="Resource Explorer: Statistics",
        description=(
            "Answers quantitative questions about GitHub projects: stars, forks, "
            "contributors, commits, releases, lines of code, language breakdown, "
            "and committer trends. Prefix with 'project:<slug>' to scope."
        ),
        skills=[
            AgentSkill(id="project_stats", name="Project Statistics",
                       description="Stars, forks, contributors, commit counts, releases, LOC, languages",
                       tags=["statistics", "github", "metrics"]),
            AgentSkill(id="top_committers", name="Top Committers",
                       description="Ranked list of contributors by commit count over the last 90 days",
                       tags=["statistics", "contributors", "github"]),
            AgentSkill(id="commit_activity", name="Commit Activity Trends",
                       description="Weekly commit cadence chart over the last 90 days",
                       tags=["statistics", "trends", "github"]),
        ],
    )
    async def stats_fn(message: Message, context: RunContext) -> AsyncGenerator:
        from resource_explorer.agents.stats_agent import StatsAgent
        query, slug = _project_scope(_text(message))
        agent = StatsAgent()
        if not slug:
            slug = agent._infer_project_slug(query)
        if not slug:
            reply: Message = yield _ask_project_status(context)
            slug = _slug_from_reply(reply)
        yield agent.handle(query, resource_slug=slug)

    return server


def _code_server() -> Server:
    server = Server()

    @server.agent(
        name="Resource Explorer: Code Search",
        description=(
            "Searches indexed source code to find implementations, classes, functions, "
            "and usage examples across Python, JavaScript, Java, and Go. "
            "Prefix with 'project:<slug>' to scope."
        ),
        skills=[
            AgentSkill(id="code_search", name="Code Search",
                       description="Find implementations, methods, and classes in source code",
                       tags=["code", "search"]),
            AgentSkill(id="usage_examples", name="Usage Examples",
                       description="Find how a class or function is used across the codebase",
                       tags=["code", "examples"]),
        ],
    )
    def code_fn(message: Message) -> str:
        from resource_explorer.agents.code_agent import CodeAgent
        query, slug = _project_scope(_text(message))
        return CodeAgent().handle(query, resource_slug=slug)

    return server


def _docs_server() -> Server:
    server = Server()

    @server.agent(
        name="Resource Explorer: Documentation",
        description=(
            "Answers conceptual questions from indexed project documentation: "
            "READMEs, architecture guides, API references, PDFs, and web docs. "
            "Prefix with 'project:<slug>' to scope."
        ),
        skills=[
            AgentSkill(id="conceptual_qa", name="Conceptual Q&A",
                       description="Architecture, design patterns, getting started, configuration",
                       tags=["documentation", "qa"]),
            AgentSkill(id="api_reference", name="API Reference",
                       description="Endpoint definitions, parameter descriptions, examples",
                       tags=["documentation", "api"]),
        ],
    )
    def docs_fn(message: Message) -> str:
        from resource_explorer.agents.doc_agent import DocAgent
        query, slug = _project_scope(_text(message))
        return DocAgent().handle(query, resource_slug=slug)

    return server


def _health_server() -> Server:
    server = Server()

    @server.agent(
        name="Resource Explorer: Health",
        description=(
            "Assesses community health and maintenance status of GitHub projects: "
            "activity trends, bus factor, PR throughput, and release cadence. "
            "Prefix with 'project:<slug>' to scope."
        ),
        skills=[
            AgentSkill(id="health_score", name="Health Assessment",
                       description="Activity status, bus factor, and maintenance indicators",
                       tags=["health", "maintenance"]),
            AgentSkill(id="pr_metrics", name="PR Metrics",
                       description="Open/closed PR counts and merge rate from live GitHub API",
                       tags=["health", "github", "pull-requests"]),
        ],
    )
    async def health_fn(message: Message, context: RunContext) -> AsyncGenerator:
        from resource_explorer.agents.health_agent import HealthAgent
        query, slug = _project_scope(_text(message))
        agent = HealthAgent()
        if not slug:
            slug = agent._infer_project_slug(query)
        if not slug:
            reply: Message = yield _ask_project_status(context)
            slug = _slug_from_reply(reply)
        yield agent.handle(query, resource_slug=slug)

    return server


def _compare_server() -> Server:
    server = Server()

    @server.agent(
        name="Resource Explorer: Compare",
        description=(
            "Produces structured side-by-side comparisons of two or more GitHub projects "
            "across code architecture, documentation, statistics, and community health. "
            "Name the projects in the question, e.g. 'compare arrow and spark'."
        ),
        skills=[
            AgentSkill(id="project_comparison", name="Project Comparison",
                       description="Side-by-side analysis of two or more indexed projects",
                       tags=["compare", "analysis"]),
        ],
    )
    async def compare_fn(message: Message, context: RunContext) -> AsyncGenerator:
        from resource_explorer.agents.compare_agent import CompareAgent
        query = _text(message)
        agent = CompareAgent()
        agent.allow_writes = False   # the A2A surface is read-only
        slugs = agent._infer_all_project_slugs(query)

        if len(slugs) < 2:
            reply: Message = yield _choice_status(
                "Which two projects would you like to compare?", "'compare egeria and agentstack'.", query, slugs, context
            )
            if reply:
                for s in agent._infer_all_project_slugs(_text(reply)):
                    if s not in slugs:
                        slugs.append(s)

        # Build a query that includes resolved slugs so CompareAgent can find them
        combined = (" ".join(slugs) + " " + query) if slugs else query
        yield agent.handle(combined)

    return server


def _integration_server() -> Server:
    server = Server()

    @server.agent(
        name="Resource Explorer: Integration",
        description=(
            "Answers 'how do X and Y work together?' questions across two or more LF AI projects. "
            "Checks for shared contributors, explicit integration docs, compatible interfaces, "
            "and complementary feature sets. Name both projects in the question."
        ),
        skills=[
            AgentSkill(id="integration_analysis", name="Integration Analysis",
                       description="Ecosystem fit, shared contributors, and cross-project integration guidance",
                       tags=["integration", "ecosystem"]),
        ],
    )
    async def integration_fn(message: Message, context: RunContext) -> AsyncGenerator:
        from resource_explorer.agents.integration_agent import IntegrationAgent
        query = _text(message)
        agent = IntegrationAgent()
        agent.allow_writes = False   # the A2A surface is read-only
        slugs = agent._infer_all_project_slugs(query)

        if len(slugs) < 2:
            reply: Message = yield _choice_status(
                "Which two projects should I check for integration?", "'egeria and agentstack'.", query, slugs, context
            )
            if reply:
                for s in agent._infer_all_project_slugs(_text(reply)):
                    if s not in slugs:
                        slugs.append(s)

        combined = (" ".join(slugs) + " " + query) if slugs else query
        yield agent.handle(combined)

    return server


def _orchestrator_server(
    agent_ports: dict[str, int] | None = None,
    agent_paths: dict[str, str] | None = None,
) -> Server:
    """The default agent. Its skill descriptions point at the specialists.

    `agent_paths` is what the single-port `a2a` role passes (``/agents/stats``
    and friends); `agent_ports` is the legacy one-port-per-agent layout. Passing
    neither is fine — the descriptions then just name the specialist.
    """
    server = Server()

    def _where(name: str) -> str:
        if agent_paths and name in agent_paths:
            return f"path {agent_paths[name]}"
        if agent_ports and name in agent_ports:
            return f"port {agent_ports[name]}"
        return "this service"

    @server.agent(
        name="Resource Explorer",
        description=(
            "Multi-agent RAG assistant for exploring GitHub projects. "
            "Classifies query intent and routes to the appropriate specialist. "
            "Prefix with 'project:<slug>' to scope to one project."
        ),
        skills=[
            AgentSkill(id="stats", name="Statistics",
                       description=f"Delegates to stats agent — {_where('stats')}",
                       tags=["routing", "statistics"]),
            AgentSkill(id="code_search", name="Code Search",
                       description=f"Delegates to code agent — {_where('code')}",
                       tags=["code", "search"]),
            AgentSkill(id="documentation", name="Documentation",
                       description=f"Delegates to docs agent — {_where('docs')}",
                       tags=["routing", "documentation"]),
            AgentSkill(id="health", name="Health",
                       description=f"Delegates to health agent — {_where('health')}",
                       tags=["routing", "health"]),
            AgentSkill(id="compare", name="Compare",
                       description=f"Delegates to compare agent — {_where('compare')}",
                       tags=["routing", "compare"]),
            AgentSkill(id="integration", name="Integration",
                       description=f"Delegates to integration agent — {_where('integration')}",
                       tags=["routing", "integration"]),
            AgentSkill(id="general", name="General RAG",
                       description="General-purpose RAG across all indexed content",
                       tags=["routing", "rag"]),
        ],
        version="1.0.0",
    )
    def orchestrator_fn(message: Message) -> str:
        from resource_explorer.rag_system import RAGSystem
        query, slug = _project_scope(_text(message))
        return RAGSystem().query(query, resource_slug=slug)

    return server


# ── the agent registry (what the `a2a` role mounts) ────────────────────────────

#: Every specialist, in the order they are advertised. `orchestrator` is first
#: because it is the default agent — it is what answers at the root.
AGENT_NAMES: tuple[str, ...] = (
    "orchestrator", "stats", "code", "docs", "health", "compare", "integration",
)


def agent_factories(agent_paths: dict[str, str] | None = None) -> dict[str, Any]:
    """Return `{name: AgentFactory}` for every specialist.

    An `AgentFactory` is what `@server.agent(...)` produces: a callable taking
    the context store's `modify_dependencies` and returning an
    `agentstack_sdk.server.agent.Agent` (card + dependencies + execute fn),
    which is exactly what `create_app()` wants.

    Reaching through a throwaway `Server` for `_agent_factory` rather than
    calling `agentstack_sdk.server.agent.agent(...)` directly is deliberate:
    the decorator call sites above stay the single definition of every agent's
    name, description and skills. Duplicating them here to avoid one private
    attribute would give each agent two descriptions that could drift, which is
    the failure this repo has been bitten by elsewhere.
    """
    builders = {
        "orchestrator": lambda: _orchestrator_server(agent_paths=agent_paths),
        "stats": _stats_server,
        "code": _code_server,
        "docs": _docs_server,
        "health": _health_server,
        "compare": _compare_server,
        "integration": _integration_server,
    }
    factories: dict[str, Any] = {}
    for name in AGENT_NAMES:
        server = builders[name]()
        factory = server._agent_factory
        if factory is None:  # pragma: no cover - a decorator that did not run
            raise RuntimeError(f"agent {name!r} registered no factory")
        factories[name] = factory
    return factories


# ── legacy entry points (one port per agent, no auth) ──────────────────────────
#
# Superseded by the `a2a` role — see this module's docstring. Kept so an
# external caller that still imports `run()` gets the old behaviour rather than
# an ImportError, and so the shape the plan replaced stays legible next to its
# replacement.

async def _serve_all(host: str, base_port: int) -> None:
    agent_ports = {name: base_port + offset for name, offset in _AGENT_OFFSETS.items()}
    servers = [
        _orchestrator_server(agent_ports),
        _stats_server(),
        _code_server(),
        _docs_server(),
        _health_server(),
        _compare_server(),
        _integration_server(),
    ]
    print(f"Starting {len(servers)} agents:")
    for name, offset in _AGENT_OFFSETS.items():
        print(f"  {name:14} → http://{host}:{base_port + offset}")
    await asyncio.gather(*(
        s.serve(host=host, port=base_port + offset)
        for s, offset in zip(servers, _AGENT_OFFSETS.values())
    ))


def run(host: str = "0.0.0.0", port: int = 8100, all_agents: bool = False) -> None:
    if all_agents:
        asyncio.run(_serve_all(host, port))
    else:
        agent_ports = {name: port + offset for name, offset in _AGENT_OFFSETS.items()}
        _orchestrator_server(agent_ports).run(host=host, port=port)
