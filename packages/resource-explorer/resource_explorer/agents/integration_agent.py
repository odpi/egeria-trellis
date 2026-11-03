"""Integration agent — answers 'how do X and Y work together?' queries."""
from __future__ import annotations

import re

from resource_explorer.agents.base import BaseExplorerAgent


def _name_key(text: str) -> str:
    """A name as it can be written in a question: lower case, with -, _, . and spaces as one hyphen. So docling-core,
    docling_core and "docling core" are one name."""
    return re.sub(r"[-_.\s]+", "-", (text or "").lower()).strip("-")


def _dependency_sentences(slug: str, name: str, rows: list[dict]) -> str:
    """What the stored rows say about one declared dependency: the manifests that declare it, grouped by version,
    dependency type and ecosystem. Only declarations are stored (manifests, never lock files), so every row is a
    direct dependency, and whether it is ALSO pulled in transitively is not recorded."""
    groups: dict[tuple[str, str, str], list[str]] = {}
    for d in rows:
        key = ((d.get("dep_version") or "").strip(), d.get("dep_type") or "", d.get("ecosystem") or "")
        groups.setdefault(key, []).append(d.get("source_file") or "(file not recorded)")
    files = sorted({f for fs in groups.values() for f in fs})
    lines = [(f"Yes. {slug} declares {name} as a direct dependency, in {len(files)} "
              f"manifest{'' if len(files) == 1 else 's'} (from its stored dependency list):")]
    for (version, dep_type, ecosystem), fs in sorted(groups.items()):
        kind = " ".join(x for x in (dep_type, ecosystem) if x) or "dependency"
        lines.append(f"- {kind}, version {version or 'not pinned'}: {', '.join(sorted(fs))}")
    lines.append(f"Only declared dependencies are recorded, so whether {name} also reaches {slug} transitively "
                 "is not known.")
    return "\n".join(lines)


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
        ]

    def handle(self, query: str, resource_slug: str | None = None, **kwargs) -> str:
        self._last_compiled = None
        self._answered_from = None
        slugs = self._infer_all_project_slugs(query)

        if len(slugs) < 2:
            # "Is there a relationship between data-prep-kit and docling-core?" names one resource and one of its
            # declared dependencies. That is answered from the stored dependency list, not by asking for a second
            # resource.
            answer = self._named_dependency_answer(query, slugs, resource_slug)
            if answer:
                return answer
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
            from resource_explorer.index_state import has_own_index
            if project and has_own_index(project.slug, project.collections):
                cols = ", ".join(sorted(project.collections))
                project_info.append(f"  {slug}: {cols}")
            else:
                # Only the shared site collections (or none) are listed: the repository's own content is not searchable.
                project_info.append(f"  {slug}: (no collections indexed — stats only)")

        direct = self._dependency_link_lines(slugs)
        self._evidence_lines = (["Direct evidence (declared dependencies, read first):"] + direct if direct else [])
        self._evidence_lines += self._multi_resource_evidence(query, slugs, kwargs.get("perspectives"))
        self._evidence_lines += self._cross_asset_link_lines(slugs)
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
            answer = self._run_agent(prompt)
        except Exception:
            answer = self._fallback(query, slugs)
        lead = self._direct_answer_lead(slugs)
        return f"{lead}\n\n{answer}" if lead else answer

    def _named_dependency_answer(self, query: str, slugs: list[str], resource_slug: str | None) -> str | None:
        """The answer when the question names a registered repository (or is asked on one) and a dependency that
        repository declares, read from its stored dependency rows: which manifests declare it, the version and the
        dependency type. None when no named or scoped repository declares anything the question names. Fail-soft:
        a failed read is no answer, and the agent goes on as before."""
        from resource_explorer.registry import ProjectRegistry

        try:
            registry = ProjectRegistry()
            candidates: list[str] = []
            for s in [*slugs, *([resource_slug] if resource_slug else [])]:
                norm = registry._normalize_slug(s)
                if norm not in candidates and registry.get(norm):
                    candidates.append(norm)
            q = _name_key(query)
            for slug in candidates:
                own = {_name_key(slug)}
                project = registry.get(slug)
                if project and project.display_name:
                    own.add(_name_key(project.display_name))
                rows = registry.query_dependencies(slug)
                named = sorted({d["dep_name"] for d in rows
                                if len(_name_key(d["dep_name"])) >= 4 and _name_key(d["dep_name"]) not in own
                                and re.search(rf"(?<![a-z0-9]){re.escape(_name_key(d['dep_name']))}(?![a-z0-9])", q)})
                if named:
                    self._answered_from = {"kind": "stored_dependencies", "resource_slug": slug,
                                           "analysis_id": "dependency_analysis", "dependencies": named}
                    return "\n\n".join(_dependency_sentences(slug, name, [d for d in rows if d["dep_name"] == name])
                                        for name in named)
        except Exception:
            import logging

            logging.getLogger(__name__).debug("dependency answer unavailable for %s", slugs, exc_info=True)
        return None

    def _declared_dependencies(self, slugs: list[str]) -> tuple[list[tuple[str, str, list[dict]]], list[str]]:
        """(a, b, dependencies of a naming b) for each ordered pair of named repositories, and the repositories with
        no dependencies recorded at all. Read from the latest recorded dependencies, so it does not wait on a
        dependency_analysis run record. Fail-soft: a failed read is no evidence."""
        import re

        from resource_explorer.registry import ProjectRegistry

        def norm(text: str) -> str:
            return re.sub(r"[^a-z0-9]", "", (text or "").lower())

        try:
            registry = ProjectRegistry()
            repos = [s for s in slugs if registry.get(registry._normalize_slug(s))]
            deps = {s: registry.query_dependencies(s) for s in repos}
        except Exception:
            return [], []
        found: list[tuple[str, str, list[dict]]] = []
        for a in repos:
            for b in repos:
                key = norm(b)
                if b == a or len(key) < 4:
                    continue
                hits = [d for d in deps[a] if key in norm(d["dep_name"])]
                if hits:
                    found.append((a, b, hits))
        return found, [a for a in repos if not deps[a]]

    @staticmethod
    def _dependency_groups(hits: list[dict]) -> dict[tuple[str, str], list[str]]:
        """Hits grouped by (name, version), each with the manifests that declare it. data_prep_kit declares
        docling-core ==2.50.1 in two requirements.txt files; listed per row it read "docling-core ==2.50.1;
        docling-core ==2.50.1", one version twice and no file named."""
        groups: dict[tuple[str, str], list[str]] = {}
        for d in hits:
            files = groups.setdefault((d["dep_name"], (d.get("dep_version") or "").strip()), [])
            f = d.get("source_file")
            if f and f not in files:
                files.append(f)
        return groups

    @classmethod
    def _dependency_names(cls, hits: list[dict]) -> str:
        groups = list(cls._dependency_groups(hits).items())
        shown = "; ".join(
            f"{name} {version}".strip() + (f" in {' and '.join(sorted(files))}" if files else "")
            for (name, version), files in groups[:5]
        )
        return shown + (f" (+{len(groups) - 5} more)" if len(groups) > 5 else "")

    def _dependency_link_lines(self, slugs: list[str]) -> list[str]:
        """Which named repository declares a dependency that names another named one (marquez declares
        io.openlineage:openlineage-java): the direct "can A work with B" evidence. A repository with none recorded
        is said to be unknown, never "does not depend". Fail-soft."""
        found, empty = self._declared_dependencies(slugs)
        out: list[str] = []
        for a in empty:
            out.append(f"[{a}] No dependencies are recorded for it, so whether it depends on "
                       f"{', '.join(b for b in slugs if b != a) or 'the others'} is unknown, not 'no'.")
        for a, b, hits in found:
            n = len(self._dependency_groups(hits))
            out.append(f"[{a}] declares {n} dependenc{'y' if n == 1 else 'ies'} naming {b}: "
                       f"{self._dependency_names(hits)}.")
        return out

    def _direct_answer_lead(self, slugs: list[str]) -> str:
        """The declared-dependency fact as a sentence to put ahead of the model's answer. The 8B model answered
        "can marquez work with openlineage?" from the large link tables and ignored the one line saying marquez
        declares an openlineage dependency, so the fact is stated by code, as the q11 confidence floor is."""
        found, _ = self._declared_dependencies(slugs)
        return " ".join(
            f"{a} declares {(n := len(self._dependency_groups(hits)))} dependenc{'y' if n == 1 else 'ies'} naming {b} "
            f"({self._dependency_names(hits)}), so the two are already connected."
            for a, b, hits in found
        )

    def _cross_asset_link_lines(self, slugs: list[str]) -> list[str]:
        """Stored cross-asset links (a repo's model names against a database's tables, plus drivers) for the named
        resources, in the prompt. A small model asked "can X work with Y?" answered from the prompt without calling
        query_cross_asset_links, then invented links; the stored ones are read here so it does not have to ask.
        Fail-soft: no links, or a failed read, adds nothing and the agent proceeds with its tools."""
        from resource_explorer.agents.tools import query_cross_asset_links_raw

        out: list[str] = []
        for slug in slugs:
            try:
                text = query_cross_asset_links_raw(resource_slug=slug, min_confidence="low", limit=8)
            except Exception:
                continue
            if text and text.strip():
                out.append(f"Cross-asset links stored for {slug} (shared names, not proof of ownership):\n{text.strip()}")
        return out

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
