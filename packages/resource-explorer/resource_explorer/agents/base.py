"""Base agent — BeeAI RequirementAgent wrapper, following the lfai/ML_LLM_Ops pattern."""
from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from typing import Any

from resource_explorer.config import ExplorerConfig, get_config


class BaseExplorerAgent(ABC):
    """
    Wraps BeeAI RequirementAgent with standard configuration.

    Each subclass defines its tools and system prompt. The agent loop,
    retry logic, streaming, and Phoenix instrumentation are handled here.

    Pattern (from lfai/ML_LLM_Ops):
        agent = RequirementAgent(
            llm="ollama:llama3.1:8b",
            tools=[VectorStoreSearchTool(...)],
            instructions=self.system_prompt(),
        )
        result = await agent.run(query)
        text = result.output[0].text
    """

    #: The CompiledContext behind the most recent handle() call, or None. The
    #: HTTP route reads it after handle() to show the user the manifest that
    #: shaped the answer (see ConversationAgent._last_compiled).
    _last_compiled = None

    #: Whether this agent may be given the tool that files a RequestForAction. The in-app chat does;
    #: the A2A surface sets it False because its tools are read-only (see agents/rfa_tools.py).
    allow_writes = True

    #: Appended to a prompt that names the RFA tool when ``allow_writes`` is False.
    NO_WRITES_NOTE = (
        "\n\nYou cannot file RequestForActions in this session. If you find an actionable issue, "
        "describe it and say it should be raised in the Resource Explorer."
    )

    def _write_tools(self) -> list:
        """The tools that write, or none when this agent is read-only."""
        if not self.allow_writes:
            return []
        from resource_explorer.agents.rfa_tools import create_rfa_from_finding

        return [create_rfa_from_finding]

    def __init__(self, config: ExplorerConfig | None = None) -> None:
        self.config = config or get_config()

    def _multi_resource_evidence(
        self, query: str, slugs: list[str], perspectives=None, budget: int = 6000
    ) -> list[str]:
        """Prompt lines of compiled evidence across several resources, plus what is missing.

        The multi-resource counterpart of ConversationAgent._compiled_evidence:
        one deterministic compile over every named resource, with the coverage
        matrix that says which dimensions were measured for which resource.
        Gaps are named per resource, because "no stored result" for one side of
        a comparison must not read as "nothing found" on that side.

        Fail-soft: a compiler that cannot compile returns nothing and the agent
        proceeds with its tools, as it did before.
        """
        self._last_compiled = None
        try:
            from resource_explorer.context_compile import compile_multi_resource_context
            from resource_explorer.registry import ProjectRegistry

            compiled = compile_multi_resource_context(
                ProjectRegistry(),
                slugs,
                query,
                budget=budget,
                perspectives=list(perspectives or []),
                session_id=getattr(self, "session_id", None) or "",
            )
        except Exception:
            import logging

            logging.getLogger(__name__).debug(
                "multi-resource evidence unavailable for %s", slugs, exc_info=True
            )
            return []

        self._last_compiled = compiled

        out: list[str] = []
        if compiled.text.strip():
            out.append("Evidence (compiled from stored analysis results):")
            out.append(compiled.text)
        by_resource: dict[str, dict[str, list[str]]] = {}
        for gap in compiled.manifest.get("gaps", []):
            reason = gap.get("reason", "no stored result")
            by_resource.setdefault(gap.get("resource_slug", ""), {}).setdefault(
                reason, []
            ).append(gap["key"])
        for slug, by_state in sorted(by_resource.items()):
            for reason, keys in sorted(by_state.items()):
                out.append(f"[{slug}] Analyses that {reason}: " + ", ".join(sorted(keys)) + ".")
        if by_resource:
            out.append(
                "Say so if the question depends on one of them, and say which resource it is "
                "missing for. An analysis that ran and found nothing has answered; one that has "
                "not run has not. Do not read the first as the second, and do not compare a "
                "measured value on one side with an unmeasured one on the other as if both were known."
            )
        return out

    @abstractmethod
    def system_prompt(self) -> str:
        """Return the system prompt for this agent."""
        ...

    @abstractmethod
    def tools(self) -> list[Any]:
        """Return the list of BeeAI tools this agent has access to."""
        ...

    @abstractmethod
    def handle(self, query: str, resource_slug: str | None = None, **kwargs) -> str:
        """Process a query and return a response string."""
        ...

    def _build_agent(self):
        """Construct the BeeAI RequirementAgent."""
        from beeai_framework.agents.requirement import RequirementAgent

        return RequirementAgent(
            llm=self._build_llm(),
            tools=self.tools(),
            instructions=self.system_prompt(),
        )

    def _llm_name(self) -> str:
        """Return the BeeAI ChatModel name string, e.g. 'ollama:llama3.1:8b'."""
        cfg = self.config.llm
        backend = cfg.backend
        if backend == "openai":
            model = cfg.openai.model
        elif backend == "anthropic":
            model = cfg.anthropic.model
        else:
            model = cfg.ollama.model
        return f"{backend}:{model}"

    def _build_llm(self):
        """
        Return the value to pass as RequirementAgent's ``llm=`` — a bare
        ``"backend:model"`` string for openai/anthropic (unchanged), or a
        constructed ``OllamaChatModel`` for the ollama backend so the
        resolved-tier ``num_ctx`` (resource_explorer/config.py TIER_PRESETS)
        can be threaded through.

        BeeAI's ``OllamaChatModel`` talks to Ollama via litellm's
        OpenAI-compatible ``/v1/chat/completions`` endpoint, not Ollama's
        native ``/api/chat`` — so there is no ``options={"num_ctx": ...}``
        dict to pass the way llm_client.py's raw ``ollama`` client takes
        one. Verified empirically (monkeypatching litellm's ``acompletion``
        and inspecting the outgoing kwargs) that a ``settings={"num_ctx":
        ...}`` constructor kwarg survives BeeAI's own supported-params
        filtering and reaches the litellm call as a top-level ``num_ctx``
        kwarg, which litellm forwards to Ollama's OpenAI-compatible server —
        Ollama's compat layer reads ``num_ctx`` from the request body the
        same way it does on the native endpoint.
        """
        cfg = self.config.llm
        if cfg.backend != "ollama":
            return self._llm_name()

        from beeai_framework.adapters.ollama.backend.chat import OllamaChatModel

        return OllamaChatModel(
            cfg.ollama.model,
            base_url=cfg.ollama.base_url,
            settings={"num_ctx": cfg.ollama.num_ctx},
        )

    def _infer_project_slug(self, query: str) -> str | None:
        """
        Scan registered projects to find one whose slug or display name appears in the query.
        Uses whole-word matching and returns the longest match to resolve ambiguity
        (e.g. 'egeria_workspaces' beats 'egeria' when the query mentions 'egeria-workspaces').
        """
        slugs = self._infer_all_project_slugs(query)
        return slugs[0] if slugs else None

    def _infer_all_project_slugs(self, query: str) -> list[str]:
        """
        Return all resource slugs (projects, databases, filesystems) mentioned in the query,
        ordered by position in the query text.
        Falls back to alias table lookup when no direct slug/name match is found.
        """
        import re
        results: list[tuple[int, str]] = []
        try:
            from resource_explorer.registry import ProjectRegistry
            registry = ProjectRegistry()
            q = query.lower()
            q_normalized = q.replace("-", "_")
            
            # 1. Match projects/repos
            for project in registry.list_all():
                slug = project.slug.lower()
                slug_pattern = slug.replace("_", r"[-_ ]")
                m = re.search(r"\b" + slug_pattern + r"\b", q_normalized)
                pos = m.start() if m else None
                if pos is None and project.display_name:
                    words = re.split(r"[-\s]+", project.display_name.lower())
                    if words:
                        m = re.search(r"\b" + re.escape(words[0]) + r"\b", q)
                        if m and all(w in q for w in words):
                            pos = m.start()
                if pos is not None:
                    results.append((pos, project.slug))

            # 2. Match databases
            for db in registry.list_databases():
                slug = db.slug.lower()
                slug_pattern = slug.replace("_", r"[-_ ]")
                m = re.search(r"\b" + slug_pattern + r"\b", q_normalized)
                pos = m.start() if m else None
                if pos is None and db.display_name:
                    words = re.split(r"[-\s]+", db.display_name.lower())
                    if words:
                        m = re.search(r"\b" + re.escape(words[0]) + r"\b", q)
                        if m and all(w in q for w in words):
                            pos = m.start()
                if pos is not None:
                    results.append((pos, db.slug))

            # 3. Match filesystems
            for fs in registry.list_filesystems():
                slug = fs.slug.lower()
                slug_pattern = slug.replace("_", r"[-_ ]")
                m = re.search(r"\b" + slug_pattern + r"\b", q_normalized)
                pos = m.start() if m else None
                if pos is None and fs.display_name:
                    words = re.split(r"[-\s]+", fs.display_name.lower())
                    if words:
                        m = re.search(r"\b" + re.escape(words[0]) + r"\b", q)
                        if m and all(w in q for w in words):
                            pos = m.start()
                if pos is not None:
                    results.append((pos, fs.slug))

            # Fall back to alias lookup when no direct match found
            if not results:
                alias_slug = self._lookup_alias(q_normalized, registry)
                if alias_slug:
                    results.append((0, alias_slug))
        except Exception:
            pass
        results.sort(key=lambda x: x[0])
        return [slug for _, slug in results]

    def _lookup_alias(self, query_normalized: str, registry=None) -> str | None:
        """
        Check the alias table for any 1–4 word ngram in the query.
        query_normalized should already be lowercased with hyphens→underscores.
        """
        try:
            if registry is None:
                from resource_explorer.registry import ProjectRegistry
                registry = ProjectRegistry()
            words = query_normalized.split()
            for n in range(min(4, len(words)), 0, -1):
                for i in range(len(words) - n + 1):
                    term = "_".join(words[i:i + n])
                    slug = registry.resolve_alias(term)
                    if slug:
                        return slug
        except Exception:
            pass
        return None

    def _clarification_response(self, query: str) -> str:
        """Return a natural-language question asking which project the user means."""
        try:
            from resource_explorer.registry import ProjectRegistry
            slugs = [p.slug for p in ProjectRegistry().list_all()]
            available = ", ".join(slugs) if slugs else "none registered yet"
        except Exception:
            available = "unknown"
        return (
            f"Which project are you asking about? "
            f"Available projects: {available}. "
            f"Try rephrasing with the project name, e.g. 'How many stars does {available.split(',')[0].strip()} have?'"
        )

    def _run_agent(self, prompt: str) -> str:
        """Run the BeeAI RequirementAgent synchronously, handling both sync and async callers."""
        async def _inner():
            agent = self._build_agent()
            result = await agent.run(prompt)
            if hasattr(result, "output") and result.output:
                first = result.output[0]
                return first.text if hasattr(first, "text") else str(first)
            return str(result)

        try:
            asyncio.get_running_loop()
            # Called from inside an async context (e.g. FastAPI) — asyncio.run
            # cannot nest, so hand the coroutine to a thread with its own loop.
            # The ONE shared pool per process (resource_explorer/concurrency.py),
            # not a fresh ThreadPoolExecutor per call — see that module for why
            # six ad hoc pools became one.
            from resource_explorer.concurrency import run_sync

            # The shared pool carries the caller into its thread (concurrency._CallersContextPool),
            # so an A2A or chat request's agent reads the registry and Egeria as the caller.
            return run_sync(lambda: asyncio.run(_inner()))
        except RuntimeError:
            # No running event loop — safe to call asyncio.run directly
            return asyncio.run(_inner())
