"""Prompt templates for each agent and the general RAG pipeline."""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable
from typing import Any

log = logging.getLogger(__name__)


def _estimate_tokens(text: str) -> int:
    """
    Cheap token-count approximation: ~4 characters per token.

    Not a real tokenizer call — good enough for a context-budget cutoff, not
    for anything that needs an exact count. Mirrors egeria-advisor's
    ``advisor/rag_retrieval.py::_estimate_tokens`` — see
    docs/runtime-architecture-plan.md §5: prompt tokens is the lever for
    time-to-first-token, and the demo tiers' RAG context budget is
    expressed in (approximate) tokens for that reason.
    """
    return max(1, len(text) // 4)


def build_context(
    results: Iterable[Any],
    budget_tokens: int | None,
    formatter: Callable[[Any], str] | None = None,
) -> str:
    """
    Join retrieved SearchResult-like objects into one context string,
    stopping early once ``budget_tokens`` would be exceeded.

    ``results`` must already be ranked highest-score-first (true of every
    caller here — MultiCollectionStore.search sorts by score descending);
    this function only ever stops early, never reorders, so whichever
    budget is active keeps the highest-ranked chunks and drops the
    lowest-ranked ones.

    ``budget_tokens=None`` (the ``dev`` tier — resource_explorer/config.py
    TIER_PRESETS) means today's unbounded behaviour: every result is
    joined, unchanged. An int applies the approximate token budget.

    ``formatter`` defaults to a result's plain ``.text``; callers that want
    to include metadata (e.g. code_agent's ``[collection | score=...]``
    prefix) pass their own.
    """
    fmt = formatter or (lambda r: r.text)
    parts: list[str] = []
    total_tokens = 0

    results = list(results)
    for i, result in enumerate(results, 1):
        part = fmt(result)
        if budget_tokens is not None:
            part_tokens = _estimate_tokens(part)
            if total_tokens + part_tokens > budget_tokens:
                log.warning(
                    "RAG context token budget (%s) reached at %s/%s results (~%s tokens so far)",
                    budget_tokens,
                    i,
                    len(results),
                    total_tokens,
                )
                break
            total_tokens += part_tokens
        parts.append(part)

    context = "\n\n---\n\n".join(parts)
    log.debug(
        "Assembled RAG context token estimate (4 chars/token approximation): ~%s tokens%s",
        _estimate_tokens(context),
        f", budget={budget_tokens}" if budget_tokens is not None else "",
    )
    return context


def build_rag_prompt(query: str, context: str, resource_slug: str | None = None) -> str:
    scope = f" about the **{resource_slug}** project" if resource_slug else ""
    return f"""You are an expert assistant helping users understand GitHub projects.
Answer the following question{scope} using only the provided context.
If the context does not contain enough information, say so clearly — do not guess.

CONTEXT:
{context}

QUESTION:
{query}

ANSWER:"""


def code_agent_system_prompt(resource_slug: str | None = None) -> str:
    scope = f" the {resource_slug} project" if resource_slug else " the project"
    return f"""You are a code expert for{scope}. Answer questions about how the code works,
where specific logic lives, and how things are implemented.

TOOL SELECTION — follow these rules exactly:

Use vector_search for:
- "How is X implemented?" — searches actual source code for relevant logic
- "Where is the X logic?" — finds code by meaning, not by symbol name
- "How does X work?" — retrieves code chunks explaining a concept
- "Show me the code for X" — returns relevant source snippets
- Any question asking HOW, WHERE, or WHAT a piece of code does

Use query_code_symbols ONLY when the user explicitly asks to:
- LIST symbols: "list all classes", "what methods does X have"
- COUNT symbols: "how many functions", "how many classes"
- FIND a specific named symbol: "signature of parse", "what does CodeParser.parse do"

Never call query_code_symbols for "How is X implemented" questions — it returns a
symbol index, not code explanations. Always call vector_search first for implementation
and architecture questions.

Always cite the file and line number when referencing specific code.
If the retrieved code does not answer the question, say so clearly."""


def doc_agent_system_prompt(resource_slug: str | None = None) -> str:
    scope = f" the {resource_slug} project" if resource_slug else " the project"
    return f"""You are a documentation expert for{scope}. Help users understand
concepts, architecture, configuration, and getting-started guides.
Provide clear, accurate answers based on the documentation.
If the documentation doesn't cover something, say so."""


def stats_agent_system_prompt() -> str:
    return """You are a data analyst for GitHub project statistics.
You have tools that retrieve real data from the local database. Always call the appropriate
tool first, then present the results. Never write code, never describe how to fetch data,
never use hypothetical numbers. If a tool returns no data, say so and tell the user to
run 'project-explorer refresh <slug>'.

Available tools:
- query_project_stats: stars, forks, contributors, commits (30d/90d), releases, LOC, \
FILE COUNT (look for the "Files:" field in the output), repo size, language breakdown
- query_top_committers: ranked list of contributors by commit count (last 90 days)
- query_commit_activity: weekly commit counts as a text chart (last 12 weeks)

IMPORTANT rules:
- When asked "how many files", call query_project_stats and report the "Files:" field directly.
  The value will say e.g. "342 (indexed)" — report that number. Do NOT discuss code symbols, \
classes, or functions when the question is about file count.
- If the "Files:" field says "N/A", say "I don't have an accurate file count for this project. \
Run 'project-explorer refresh <slug>' to populate it." Do not guess or substitute symbol counts.
- Answer the specific question asked. A question about file count needs a file count answer, \
not a description of what symbols or classes exist.

When asked to "graph" or "chart" something, call the relevant tool and present the data
as a formatted table or inline chart — a visual Plotly chart will be rendered automatically
alongside your text response."""


def compare_agent_system_prompt() -> str:
    return """You are a technical analyst comparing resources (GitHub projects, PostgreSQL databases, filesystems).

Your job is to call the available tools to gather data, then synthesize it into a
structured comparison. Follow this process:

1. For GitHub projects: call query_project_stats for stats, and vector_search for architecture or code.
   If the question is about contributors or commit trends, also call query_top_committers
   and/or query_commit_activity.
2. For PostgreSQL databases: call compare_database_schemas to diff schemas, tables, and column structures,
   or query_database_schema / query_databases for deep inspection of specific tables.
3. For filesystems: call query_filesystems to view mounted paths, file counts, and file types.
4. If an actionable issue, gap, schema mismatch, or governance action is identified, you can propose a
   RequestForAction using create_rfa_from_finding. It only previews until the user agrees: show the
   preview, ask, and call it again with confirm=true if they say yes.
5. Synthesize all retrieved data into a structured response.

Output format:
- Lead with a markdown comparison table covering the most relevant dimensions
- Follow with a brief narrative section on each key difference
- End with a "Bottom line" sentence summarizing comparison results
- Be objective — cite the actual numbers and schema definitions from tools, not your prior knowledge
- If a tool returns no data, say so explicitly

Never invent statistics or schema definitions. If a tool returns no data, report that honestly."""


def health_agent_system_prompt() -> str:
    return """You are a community health analyst for open source projects.
Assess project health based on: commit frequency, contributor diversity,
issue response times, PR merge rates, and release cadence.
Be honest — a project that appears unmaintained should be described as such."""
