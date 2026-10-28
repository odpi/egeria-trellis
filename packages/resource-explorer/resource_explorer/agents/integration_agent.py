"""Integration agent — answers 'how do X and Y work together?' queries."""
from __future__ import annotations

from resource_explorer.agents.base import BaseExplorerAgent


class IntegrationAgent(BaseExplorerAgent):
    """
    Answers ecosystem-fit and integration queries spanning two or more projects.

    Distinct from CompareAgent: instead of 'which is better?', the question is
    'can these be used together, and how?'  The agent looks for:
      - Explicit cross-project references in docs (does project A mention B?)
      - Shared contributors (active collaboration signal)
      - Compatible languages, interfaces, and data formats
      - Architectural fit from documentation excerpts

    Requires at least two project slugs resolvable from the query.
    Falls back to clarification if fewer than two can be inferred.
    """

    def system_prompt(self) -> str:
        return (
            "You are an expert on LF AI & Data projects, databases, and ecosystem relationships. "
            "Your job is to determine whether two or more resources can be used together and how. "
            "Focus on: explicit integration documentation, shared contributors (collaboration signal), "
            "cross-asset linkages (code repositories to database schemas via query_cross_asset_links), "
            "compatible data formats and interfaces, complementary feature sets, and architectural fit. "
            "Be concrete — cite specific APIs, schemas, data formats, or documentation passages. "
            "If integration is not well-documented, say so clearly rather than speculating. Data lineage is not available to you; do not infer or claim anything about it. "
            "Structure your response as: (1) Integration summary, (2) How they connect technically, "
            "(3) Cross-asset dependencies if any, (4) Shared contributors if any, "
            "(5) Getting started recommendation."
        )

    def tools(self) -> list:
        from resource_explorer.agents.tools import (
            vector_search,
            query_project_stats,
            query_top_committers,
            query_code_symbols,
            query_databases,
            query_database_schema,
            query_cross_asset_links,
        )
        return [
            vector_search,
            query_project_stats,
            query_top_committers,
            query_code_symbols,
            query_databases,
            query_database_schema,
            query_cross_asset_links,
            *self._write_tools(),
        ]

    def handle(self, query: str, resource_slug: str | None = None, **kwargs) -> str:
        slugs = self._infer_all_project_slugs(query)

        if len(slugs) < 2:
            from resource_explorer.registry import ProjectRegistry
            available = ", ".join(p.slug for p in ProjectRegistry().list_all())
            return (
                "Please name at least two projects to explore integration "
                f"(e.g. 'Can I use egeria with agentstack?'). "
                f"Available: {available or 'none indexed yet'}."
            )

        from resource_explorer.registry import ProjectRegistry
        all_projects = {p.slug: p for p in ProjectRegistry().list_all()}

        project_info = []
        for slug in slugs:
            project = all_projects.get(slug)
            if project and project.collections:
                cols = ", ".join(sorted(project.collections))
                project_info.append(f"  {slug}: {cols}")
            else:
                project_info.append(f"  {slug}: (no collections indexed — stats only)")

        self._evidence_lines = self._multi_resource_evidence(
            query, slugs, kwargs.get("perspectives")
        )
        evidence = "\n".join(self._evidence_lines)
        evidence_block = f"{evidence}\n\n" if evidence else ""
        prompt = (
            f"Investigate whether these projects can be integrated: {', '.join(slugs)}\n\n"
            f"Available collections per project:\n" + "\n".join(project_info) + "\n\n"
            f"{evidence_block}"
            "Steps to follow:\n"
            "1. For each project, search its docs/README for any mention of the other project(s).\n"
            "2. Compare primary languages and data interfaces (query_project_stats).\n"
            "3. Check for shared contributors (query_top_committers for each project, compare names/emails).\n"
            "4. Search for integration keywords: 'plugin', 'connector', 'adapter', 'compatible', 'together'.\n\n"
            f"Question: {query}\n\n"
            "Respond with: integration summary, technical connection points, "
            "shared contributors (if any), and a concrete getting-started recommendation."
        )

        try:
            return self._run_agent(prompt)
        except Exception:
            return self._fallback(query, slugs)

    def _fallback(self, query: str, slugs: list[str]) -> str:
        """Direct tool-call fallback when BeeAI is unavailable."""
        from resource_explorer.agents.tools import (
            query_project_stats_raw,
            vector_search_raw,
            query_top_committers_raw,
            query_cross_asset_links_raw,
        )
        from resource_explorer.collection_router import CollectionRouter
        from resource_explorer.llm_client import get_llm

        router = CollectionRouter()
        sections: list[str] = []
        for slug in slugs:
            stat_text = query_project_stats_raw(slug)
            committers = query_top_committers_raw(slug, 5)
            collections = router.select("integration documentation connector", slug)
            doc_text = vector_search_raw("integration connector compatible together", ",".join(collections)) if collections else "(no content)"
            sections.append(f"## {slug}\n{stat_text}\n\n### Top contributors\n{committers}\n\n### Docs\n{doc_text}")

        cross_links = query_cross_asset_links_raw()

        sections.extend(getattr(self, "_evidence_lines", []))
        combined = "\n\n---\n\n".join(sections)
        prompt = (
            f"{self.system_prompt()}\n\n"
            f"Project data:\n\n{combined}\n\n"
            f"Cross-Asset Dependencies:\n{cross_links}\n\n"
            f"Question: {query}\n\nIntegration analysis:"
        )
        return get_llm().complete(prompt)
