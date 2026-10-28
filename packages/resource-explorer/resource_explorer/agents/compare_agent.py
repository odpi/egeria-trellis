"""Comparison agent — side-by-side analysis of two or more projects or databases."""

from __future__ import annotations

from resource_explorer.agents.base import BaseExplorerAgent
from resource_explorer.prompt_templates import compare_agent_system_prompt


class CompareAgent(BaseExplorerAgent):
    """
    Uses shared tools so the LLM can independently retrieve content,
    statistics, and schemas for each resource being compared.

    Handles three comparison modes:
      - Statistical: stars, commits, contributors, releases — uses query_project_stats,
        query_top_committers, query_commit_activity
      - Content/architecture: documentation and code differences — uses vector_search
      - Database schema: table definitions, column types, nullability, PK/FK diffs — uses
        compare_database_schemas, query_database_schema, query_databases
    """

    def system_prompt(self) -> str:
        prompt = compare_agent_system_prompt()
        return prompt if self.allow_writes else prompt + self.NO_WRITES_NOTE

    def tools(self) -> list:
        from resource_explorer.agents.tools import (
            vector_search,
            query_project_stats,
            query_top_committers,
            query_commit_activity,
            query_code_symbols,
            get_symbol_detail,
            query_databases,
            query_database_schema,
            compare_database_schemas,
            query_cross_asset_links,
        )

        return [
            vector_search,
            query_project_stats,
            query_top_committers,
            query_commit_activity,
            query_code_symbols,
            get_symbol_detail,
            query_databases,
            query_database_schema,
            compare_database_schemas,
            query_cross_asset_links,
            *self._write_tools(),
        ]

    def handle(self, query: str, resource_slug: str | None = None, **kwargs) -> str:
        slugs = self._infer_all_project_slugs(query)

        if len(slugs) < 2:
            from resource_explorer.registry import ProjectRegistry

            reg = ProjectRegistry()
            available_repos = [p.slug for p in reg.list_all()]
            available_dbs = [db.slug for db in reg.list_databases()]
            all_available = ", ".join(available_repos + available_dbs)
            return (
                "Please name at least two resources to compare "
                f"(e.g. 'compare project-a and project-b' or 'compare db1 and db2'). "
                f"Available: {all_available or 'none indexed yet'}."
            )

        from resource_explorer.registry import ProjectRegistry

        registry = ProjectRegistry()
        all_projects = {p.slug: p for p in registry.list_all()}
        all_dbs = {db.slug: db for db in registry.list_databases()}

        is_db_comparison = all(s in all_dbs for s in slugs)
        has_db = any(s in all_dbs for s in slugs)

        resource_info = []
        for slug in slugs:
            if slug in all_dbs:
                db_ent = all_dbs[slug]
                resource_info.append(
                    f"  {slug}: (database — {db_ent.db_type} at {db_ent.host}:{db_ent.port})"
                )
            else:
                project = all_projects.get(slug)
                if project and project.collections:
                    cols = ", ".join(sorted(project.collections))
                    resource_info.append(f"  {slug}: {cols}")
                else:
                    resource_info.append(f"  {slug}: (no collections indexed — stats only)")

        q = query.lower()
        if is_db_comparison:
            guidance = (
                "This is a DATABASE schema comparison. Call compare_database_schemas to diff "
                "the schemas, tables, and columns between the databases, or query_database_schema "
                "for deeper inspections of specific tables."
            )
            if self.allow_writes:
                guidance += (
                    " If an actionable issue or discrepancy is found, you can propose a RequestForAction "
                    "using create_rfa_from_finding (it previews first; file it with confirm=true only if "
                    "the user agrees)."
                )
        elif has_db:
            guidance = (
                "This is a cross-resource comparison between databases and repositories/filesystems. "
                "Use query_database_schema / query_databases for database resources and "
                "query_project_stats / vector_search for code repositories."
            )
        else:
            is_stat_comparison = any(
                w in q
                for w in (
                    "star",
                    "fork",
                    "commit",
                    "contributor",
                    "release",
                    "issue",
                    "active",
                    "popular",
                    "growth",
                    "download",
                    "trend",
                    "statistics",
                    "stats",
                    "number",
                    "count",
                    "how many",
                )
            )
            guidance = (
                "This is primarily a STATISTICAL comparison. Call query_project_stats for each "
                "project first, then query_top_committers and query_commit_activity if the user "
                "asks about contributors or activity trends. Do NOT call vector_search unless "
                "the user also asks about architecture or code."
                if is_stat_comparison
                else "This is primarily a CONTENT comparison. Call vector_search for each project "
                "to retrieve relevant documentation or code, then call query_project_stats for "
                "a brief stats snapshot to provide context."
            )

        self._evidence_lines = self._multi_resource_evidence(
            query, slugs, kwargs.get("perspectives")
        )
        evidence = "\n".join(self._evidence_lines)
        evidence_block = f"{evidence}\n\n" if evidence else ""
        prompt = (
            f"Compare these resources: {', '.join(slugs)}\n\n"
            f"Available metadata per resource:\n" + "\n".join(resource_info) + "\n\n"
            f"{evidence_block}"
            f"Comparison guidance: {guidance}\n\n"
            f"Question: {query}\n\n"
            "Structure your response with:\n"
            "1. A summary table of key differences\n"
            "2. A section per comparison dimension (stats, architecture, schemas, etc.)\n"
            "3. A final recommendation or verdict if the user asked for one"
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
            compare_database_schemas_raw,
            query_database_schema_raw,
        )
        from resource_explorer.collection_router import CollectionRouter
        from resource_explorer.llm_client import get_llm
        from resource_explorer.registry import ProjectRegistry

        registry = ProjectRegistry()
        all_dbs = {db.slug: db for db in registry.list_databases()}
        is_db_comparison = all(s in all_dbs for s in slugs)

        sections: list[str] = []
        if is_db_comparison and len(slugs) >= 2:
            diff_text = compare_database_schemas_raw(slugs[0], slugs[1])
            sections.append(
                f"## Database Schema Comparison ({slugs[0]} vs {slugs[1]})\n\n{diff_text}"
            )
        else:
            router = CollectionRouter()
            for slug in slugs:
                if slug in all_dbs:
                    schema_text = query_database_schema_raw(slug)
                    sections.append(f"## {slug} (Database)\n\n### Schema\n{schema_text}")
                else:
                    stat_text = query_project_stats_raw(slug)
                    collections = router.select(query, slug)
                    if collections:
                        doc_text = vector_search_raw(query, ",".join(collections))
                    else:
                        doc_text = "(no indexed content)"
                    sections.append(
                        f"## {slug}\n\n### Stats\n{stat_text}\n\n### Content\n{doc_text}"
                    )

        sections.extend(getattr(self, "_evidence_lines", []))
        combined = "\n\n---\n\n".join(sections)
        fallback_prompt = (
            f"{self.system_prompt()}\n\n"
            f"Data for resources being compared:\n\n{combined}\n\n"
            f"Question: {query}\n\nComparison:"
        )
        return get_llm().complete(fallback_prompt)
