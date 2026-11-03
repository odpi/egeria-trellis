"""Sub-surveyor: Git Statistics → ResourceMeasureAnnotation.

Refreshes `project_stats` — stars, forks, contributors, commit activity,
releases, security-and-analysis config, deployments — from the GitHub API, and
reports what it fetched.

Why this is its own step rather than a side effect of one reader.
`project_stats` is the most widely read table in the repo survey set: eight
sub-surveyors read it (health, maturity, language, file_structure,
license_classifier, security_features, security_hygiene, homepage). Exactly one
of them — HealthSurveyor — refreshed it, as an internal side effect, and the
other seven read whatever happened to be there. So the freshness of license
classification, maturity, security-feature and homepage results depended on
whether repo_health happened to be in the same survey and happened to run first
— an ordering nothing declared and nothing enforced. Run any of those seven
without repo_health and they silently scored registration-time data.

That is the same shape as the two microflow steps already here:
repo_symbol_extraction (project_code_symbols, previously written only by RAG
ingestion) and repo_file_inventory (project_file_inventory, previously written
only by ingestion/refresh_profile). Each replaced an implicit prerequisite with
a declared, ordered, re-runnable step. This is the third and the most-shared of
the three.

Ordered first in STEP_REGISTRY, which is also the order "Repo Full Survey" runs
(the "*" sentinel in repo_survey_types.csv), so every reader in the same run
sees this run's numbers.

Cost: unchanged — one GitHub API pass per survey, now explicit and shared. `fast` skips StatsFetcher's
per-commit diff-stats calls — a confirmed real slowness (several hundred
sequential API calls for an active repo's 90-day history), which is why Coarse
Scout sets it.

Running a reader step in isolation (repo_health alone, say) no longer refreshes
stats implicitly; it reports against what is stored, and its `surveyed_at` shows
how old that is. That is the same trade repo_file_inventory made, and the reason
the refresh is a visible step rather than a hidden side effect.
"""
from __future__ import annotations

import logging
from datetime import datetime

from resource_explorer.registry import Project, ProjectRegistry
from resource_explorer.surveyors.base_surveyor import BaseSurveyor
from resource_explorer.step_outcome import StepOutcome
from resource_explorer.surveyors.survey_report import Annotation, ResourceMeasureAnnotation

log = logging.getLogger(__name__)

STEP = "GitStatistics"


class GitStatisticsSurveyor(BaseSurveyor):
    """Refreshes project_stats from the GitHub API, then reports the headline
    numbers it just stored."""

    def __init__(
        self, project: Project, registry: ProjectRegistry, fast: bool = False,
        surveyed_at: str | None = None,
    ) -> None:
        super().__init__(project, registry)
        self.fast = fast
        self._surveyed_at = surveyed_at or datetime.utcnow().isoformat()

    @property
    def step_name(self) -> str:
        return STEP

    def run(self) -> list[Annotation]:
        slug = self.project.slug
        refreshed, error = False, ""
        try:
            from resource_explorer.github.stats_fetcher import StatsFetcher

            StatsFetcher().fetch(slug, fetch_diff_stats=not self.fast)
            refreshed = True
        except Exception as exc:
            # Best-effort, exactly as the per-surveyor fetches were: a GitHub
            # hiccup must not fail the survey. Readers fall back to the stored
            # row, which is what they did before this step existed.
            error = str(exc)
            log.warning("GitStatisticsSurveyor: stats refresh failed for %s: %s", slug, exc)

        stats = {}
        try:
            stats = self.registry.get_latest_project_stats(slug) or {}
        except Exception as exc:
            log.warning("GitStatisticsSurveyor: could not read project_stats for %s: %s", slug, exc)

        if not stats:
            return [
                ResourceMeasureAnnotation(
                    check_name="git_statistics",
                    summary="No git statistics available",
                    analysis_step=STEP,
                    confidence=0,
                    explanation=(
                        f"Could not refresh or read project_stats for {slug}."
                        + (f" Refresh error: {error}" if error else "")
                    ),
                    resource_properties={"refreshed": False},
                    # Already confidence 0 with a stated reason — the reasoning
                    # was right and only the label was missing. Never
                    # `no_signal`: a repo with no readable stats row is a gap in
                    # what we hold, not a repo with no stars.
                    json_properties=StepOutcome(
                        "unverified",
                        cause="project_stats could not be refreshed or read",
                        detail={"refresh_error": error or ""}).as_row(),
                )
            ]

        headline = {
            "stars": stats.get("stars") or 0,
            "forks": stats.get("forks") or 0,
            "contributors": stats.get("contributors_count") or 0,
            "open_issues": stats.get("open_issues") or 0,
            "releases": stats.get("releases_count") or 0,
            "primary_language": stats.get("primary_language") or "",
            "last_pushed_at": stats.get("last_pushed_at") or "",
            "refreshed": refreshed,
        }
        # `refreshed` is the distinction that matters and the summary already
        # carries it: serving a stored row after a failed refresh is a real
        # answer about an older moment, which is `partial` — produced
        # something, knowingly incomplete — not a clean `recovered`.
        stats_outcome = (StepOutcome("recovered", detail={"refreshed": True})
                         if refreshed else
                         StepOutcome("partial", cause="refresh failed; serving stored stats",
                                     detail={"refresh_error": error or ""}))
        return [
            ResourceMeasureAnnotation(
                check_name="git_statistics",
                summary=(
                    f"★ {headline['stars']} · {headline['forks']} fork(s) · "
                    f"{headline['contributors']} contributor(s)"
                    + ("" if refreshed else " (from stored stats — refresh failed)")
                ),
                analysis_step=STEP,
                confidence=100 if refreshed else 50,
                explanation=(
                    "Refreshed project_stats from the GitHub API. Every step that reads "
                    "repository statistics in this run — health, maturity, language, file "
                    "structure, license, security features and hygiene, homepage — sees "
                    "these numbers rather than whatever an earlier, unrelated run left."
                    + (f" This run's refresh failed ({error}); the values above are the "
                       "previously stored ones." if not refreshed else "")
                ),
                resource_properties=headline,
                json_properties=stats_outcome.as_row(),
            )
        ]
