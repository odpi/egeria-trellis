"""Community health agent — maintenance status, contributor diversity, activity trends."""
from __future__ import annotations


from resource_explorer.agents.base import BaseExplorerAgent
from resource_explorer.prompt_templates import health_agent_system_prompt
from resource_explorer.registry import ProjectRegistry


class HealthAgent(BaseExplorerAgent):
    """
    Answers questions about project health using GitHub API data.
    Uses query_project_stats tool so the LLM can retrieve the metrics it needs.
    """

    def system_prompt(self) -> str:
        return health_agent_system_prompt()

    def tools(self) -> list:
        from resource_explorer.agents.tools import query_project_stats, query_top_committers
        return [query_project_stats, query_top_committers]

    def handle(self, query: str, resource_slug: str | None = None, **kwargs) -> str:
        slug = resource_slug or self._infer_project_slug(query)

        if not slug:
            return self._clarification_response(query)

        prompt = f"Project: {slug}\n\nQuestion: {query}"
        try:
            return self._run_agent(prompt)
        except Exception:
            health_data = self._fetch_health(slug)
            if not health_data:
                return (
                    f"No health data available for '{slug}'. "
                    f"Run: project-explorer refresh {slug}"
                )
            from resource_explorer.llm_client import get_llm
            fallback_prompt = (
                f"{self.system_prompt()}\n\nHealth data:\n{health_data}\n\nQuestion: {query}\n\nAssessment:"
            )
            return get_llm().complete(fallback_prompt)

    # ── fallback ───────────────────────────────────────────────────────────────

    def _fetch_health(self, resource_slug: str | None) -> str:
        if not resource_slug:
            return ""
        registry = ProjectRegistry()
        project = registry.get(resource_slug)
        if not project:
            return ""
        base = self._stats_row(registry, resource_slug)
        if not base:
            return ""
        sections = self._format_health_sections(resource_slug, base)
        github_extra = self._github_health(project)
        if github_extra:
            sections.extend(["", "Pull Requests (live):"] + github_extra)
        return "\n".join(sections)

    def _stats_row(self, registry, resource_slug: str) -> dict:
        try:
            with registry._conn() as conn:
                row = conn.execute(
                    "SELECT * FROM project_stats WHERE project_slug = ? ORDER BY fetched_at DESC LIMIT 1",
                    (resource_slug,),
                ).fetchone()
            return dict(row) if row else {}
        except Exception:
            return {}

    def _format_health_sections(self, resource_slug: str, d: dict) -> list[str]:
        commits_30d = d.get("commits_30d") or 0
        commits_90d = d.get("commits_90d") or 0
        contributors = d.get("contributors_count") or 0
        releases = d.get("releases_count") or 0

        if commits_30d > 20:
            activity_status = "Very active"
        elif commits_30d > 5:
            activity_status = "Active"
        elif commits_30d > 0:
            activity_status = "Low activity"
        else:
            activity_status = "Inactive (no commits in last 30 days)"

        if contributors >= 20:
            bus_factor = "Low risk (20+ contributors)"
        elif contributors >= 5:
            bus_factor = "Moderate risk (5–19 contributors)"
        elif contributors >= 2:
            bus_factor = "Elevated risk (2–4 contributors)"
        else:
            bus_factor = "High risk (single maintainer)"

        release_cadence = f"{releases} total release(s)" if releases and commits_90d else "Unknown"

        return [
            f"Project: {resource_slug}",
            "",
            "Activity:",
            f"  Status: {activity_status}",
            f"  Commits (last 30 days): {commits_30d}",
            f"  Commits (last 90 days): {commits_90d}",
            "",
            "Community:",
            f"  Stars: {d.get('stars', 'N/A')}",
            f"  Forks: {d.get('forks', 'N/A')}",
            f"  Contributors: {contributors}",
            f"  Bus Factor: {bus_factor}",
            "",
            "Maintenance:",
            f"  Open Issues: {d.get('open_issues', 'N/A')}",
            f"  Releases: {release_cadence}",
            f"  Latest Release: {d.get('latest_release', 'N/A')}",
            f"  Latest Release Date: {d.get('latest_release_at', 'N/A')}",
            "",
            f"Primary Language: {d.get('primary_language', 'N/A')}",
            f"Stats as of: {d.get('fetched_at', 'N/A')}",
        ]

    def _github_health(self, project) -> list[str]:
        try:
            from resource_explorer.github.client import GitHubClient
            client = GitHubClient()
            repo = client.get_repo(project.github_url)
            open_prs = repo.get_pulls(state="open").totalCount
            closed_prs = repo.get_pulls(state="closed").totalCount
            total = open_prs + closed_prs
            merge_rate = f"{closed_prs / total:.0%}" if total else "N/A"
            return [
                f"  Open PRs: {open_prs}",
                f"  Closed PRs: {closed_prs}",
                f"  Merge rate: {merge_rate}",
            ]
        except Exception:
            return []
