"""Sub-surveyor: GitHub's native security/analysis feature toggles →
ClassificationAnnotation per feature.

Assessment expansion plan B2 (docs/assessment-expansion-plan.md). Reads
project_stats.security_and_analysis_json — already fetched by StatsFetcher
(stats_fetcher.py's _security_and_analysis(), populated from GitHub's
`repo.security_and_analysis` API object), zero new API calls, no zipball
needed.

Distinct from SecurityHygieneSurveyor (repo_security / "security_scan"),
which checks for *presence of artifacts* (SECURITY.md, CI config, LICENSE)
via file inventory — this reads GitHub's own *feature configuration state*
(is Dependabot/secret-scanning/etc. actually turned on), a different signal
entirely, hence its own analysis kind rather than folding into that one.
"""
from __future__ import annotations

import json
import logging

from resource_explorer.registry import Project, ProjectRegistry
from resource_explorer.surveyors.base_surveyor import BaseSurveyor
from resource_explorer.step_outcome import StepOutcome
from resource_explorer.surveyors import result_status
from resource_explorer.surveyors.survey_report import Annotation, ClassificationAnnotation
from resource_explorer.survey_time import now_utc_iso

log = logging.getLogger(__name__)

STEP = "SecurityFeaturesCheck"

# Mirrors stats_fetcher.py's StatsFetcher._SECURITY_FEATURE_NAMES exactly —
# the 7 feature keys GitHub's security_and_analysis API object exposes.
_FEATURE_NAMES = (
    "advanced_security", "dependabot_security_updates", "secret_scanning",
    "secret_scanning_ai_detection", "secret_scanning_non_provider_patterns",
    "secret_scanning_push_protection", "secret_scanning_validity_checks",
)


class SecurityFeaturesSurveyor(BaseSurveyor):
    """Reads project_stats.security_and_analysis_json (already fetched by
    StatsFetcher, no new API call) and emits one finding per feature whose
    status is known. A status of None means GitHub never exposed the data
    for this repo (per StatsFetcher's own docstring: only populated for
    repos the connected token administers — empty for most repos being
    scouted) — that feature is skipped entirely rather than reported as a
    gap, so repos GitHub simply didn't expose data for aren't penalized."""

    def __init__(self, project: Project, registry: ProjectRegistry, surveyed_at: str | None = None) -> None:
        super().__init__(project, registry)
        self._surveyed_at = surveyed_at or now_utc_iso()

    @property
    def step_name(self) -> str:
        return STEP

    def run(self) -> list[Annotation]:
        results: list[Annotation] = []
        try:
            slug = self.project.slug
            stats = self.registry.get_latest_project_stats(slug) or {}
            try:
                features = json.loads(stats.get("security_and_analysis_json") or "{}")
            except (TypeError, ValueError):
                features = {}

            if not features:
                # The common case, not an edge one: GitHub returns
                # security_and_analysis only to repository admins, so for 54 of
                # 60 catalogued repos this loop `continue`d on every feature
                # and the step emitted NOTHING. Silence made "we are not
                # allowed to see this" identical to "this step did not run",
                # and contributed nothing to which-tools-suit-which-repos.
                #
                # Both vocabularies apply here and neither replaces the other,
                # which is exactly the split repo_classification's docstring
                # settled: the RUN could not establish anything (`unverified`,
                # for the tool-fit query), and the READER should be told this
                # is by design rather than a failure (`result_status.skipped`,
                # for the card). A run asks "is my zero provable?"; a reader
                # asks "what should I do about it?".
                return [ClassificationAnnotation(
                    check_name="security_features",
                    summary=("Security feature settings are not visible for this "
                             "repository — GitHub returns them only to repository "
                             "admins. Not a finding that they are disabled."),
                    analysis_step=STEP,
                    candidate_classifications=[],
                    confidence=0,
                    json_properties={
                        **StepOutcome(
                            "unverified",
                            cause="security_and_analysis withheld — not a repo admin",
                        ).as_row(),
                        "result_status": result_status.skipped(
                            "GitHub returns security feature settings only to repository "
                            "admins, so these are invisible for a repo you do not own.",
                            gate="github_admin_only"),
                    },
                )]

            findings = []
            for name in _FEATURE_NAMES:
                status = features.get(name)
                if status is None:
                    continue  # unavailable, not a gap — see docstring above
                label = "pass" if status == "enabled" else "gap"
                summary = f"{name.replace('_', ' ')}: {status}"
                results.append(
                    ClassificationAnnotation(
                        check_name=name,
                        summary=summary,
                        analysis_step=STEP,
                        candidate_classifications=[label],
                        confidence=100,
                        json_properties={"feature": name, "status": status,
                                         **StepOutcome("recovered").as_row()},
                    )
                )
                findings.append({
                    "check_name": name, "label": label, "summary": summary,
                    "confidence": 100, "detail": {"status": status},
                })

            if findings:
                try:
                    # supersedes_previous=True: security family (docs/
                    # Backlog.md), this one call is the whole run's answer
                    # for (slug, "security_features", "").
                    self.registry.upsert_finding(
                        slug, "security_features", findings, surveyed_at=self._surveyed_at,
                        supersedes_previous=True,
                    )
                except Exception as exc:
                    log.warning("Could not persist security features for %s: %s", slug, exc)

        except Exception as exc:
            log.exception("SecurityFeaturesSurveyor failed for %s", self.project.slug)
            self._warn(results, str(exc))

        return results
