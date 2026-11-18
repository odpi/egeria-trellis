"""Sub-surveyor: License/copyleft risk classification → ClassificationAnnotation.

Assessment expansion plan B1 (docs/assessment-expansion-plan.md). Closes a
real gap flagged while authoring the Scouting Question/Perspective model
(docs/dr-egeria/resource_questions.csv): the GitHub API's license field is
already fetched at Scouting-tier for free, but nothing classifies it —
"Are there any restrictions for use?" and the explicit copyleft-terms
question were both GAP-tagged there.

Deliberately a static SPDX-id -> risk-tier lookup table, not an external
tool integration (FOSSology/LicenseFinder/ClearlyDefined, named as future
candidates in the CSV) — a legitimate, immediately-buildable first cut from
data RE already has (project_stats.license_spdx_id, itself added alongside
this surveyor — see stats_fetcher.py's _license_info()).
"""
from __future__ import annotations

import logging

from resource_explorer.registry import Project, ProjectRegistry
from resource_explorer.surveyors.base_surveyor import BaseSurveyor
from resource_explorer.step_outcome import StepOutcome, no_signal
from resource_explorer.surveyors.survey_report import Annotation, ClassificationAnnotation
from resource_explorer.survey_time import now_utc_iso

log = logging.getLogger(__name__)

STEP = "LicenseClassification"

# SPDX identifier -> risk tier. Not exhaustive — an id absent from this
# table falls through to "unknown" (never silently guessed as permissive).
# Grouped by real-world integration-risk category, not by OSI/FSF category
# boundaries exactly (e.g. source-available licenses like BUSL/SSPL aren't
# OSI-approved at all, but "copyleft" undersells the actual risk, which is
# closer to "may not be usable in a commercial product without a separate
# agreement" — kept as its own tier for that reason).
_PERMISSIVE = {
    "MIT", "BSD-2-Clause", "BSD-3-Clause", "BSD-3-Clause-Clear", "Apache-2.0",
    "ISC", "Unlicense", "0BSD", "Zlib", "BSL-1.0",  # BSL-1.0 = Boost Software
    # License 1.0 — NOT the Business Source License (that's "BUSL-1.1",
    # source-available, listed separately below). Easy to confuse; both
    # commonly shortened to "BSL" in casual references — SPDX id is
    # unambiguous, which is exactly why this surveyor classifies by id,
    # not by license name text.
    "PostgreSQL", "Python-2.0", "PSF-2.0", "WTFPL",
}
_WEAK_COPYLEFT = {
    "LGPL-2.0", "LGPL-2.1", "LGPL-3.0", "MPL-1.1", "MPL-2.0",
    "EPL-1.0", "EPL-2.0", "CDDL-1.0", "CDDL-1.1",
}
_STRONG_COPYLEFT = {
    "GPL-1.0", "GPL-2.0", "GPL-3.0", "AGPL-1.0", "AGPL-3.0",
}
_SOURCE_AVAILABLE = {
    # Real usage-restriction risk beyond copyleft — usually a time-boxed or
    # scale-boxed conversion to a permissive/OSI license, or an outright
    # prohibition on offering the software as a competing hosted service.
    "BUSL-1.1", "SSPL-1.0", "Elastic-2.0",
}

_TIER_LABELS = {
    "permissive": "Permissive",
    "weak_copyleft": "Weak copyleft",
    "strong_copyleft": "Strong copyleft",
    "source_available": "Source-available (non-OSI)",
    "unknown": "Unknown / unclassified",
    "none": "No license detected",
}


def _classify(spdx_id: str) -> str:
    if not spdx_id or spdx_id == "NOASSERTION":
        return "unknown"
    if spdx_id in _PERMISSIVE:
        return "permissive"
    if spdx_id in _WEAK_COPYLEFT:
        return "weak_copyleft"
    if spdx_id in _STRONG_COPYLEFT:
        return "strong_copyleft"
    if spdx_id in _SOURCE_AVAILABLE:
        return "source_available"
    return "unknown"


class LicenseClassifierSurveyor(BaseSurveyor):
    """Reads project_stats.license_spdx_id (already fetched by StatsFetcher,
    no new API call) and classifies it into a risk tier via the static
    lookup table above. Produces exactly one finding — license rarely
    changes, so unlike most Assessment-tier surveyors this isn't really a
    repeated-check story, just a single current-state classification."""

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
            license_name = stats.get("license") or ""
            spdx_id = stats.get("license_spdx_id") or ""

            # Three states were reported as two, and the pair that got merged
            # is the pair that matters: a repo GitHub told us has no license,
            # and a repo nothing has ever fetched stats for, both produced
            # "No license detected on this repository."
            #
            # The stats row is the known-positive. GitHub answering about this
            # repo and naming no license is a fact about the repo. No row at
            # all is a fact about our own pipeline, and saying "no license"
            # then is asserting the more alarming of the two readings on no
            # evidence — a repo with an unfetched profile reads as
            # unlicensed, which is exactly the direction a reader acts on.
            examined = bool(stats)

            if not examined:
                tier = "none"
                summary = ("License not established — no repository statistics have been "
                           "fetched, so nothing was examined. This is not a finding that "
                           "the repo is unlicensed.")
                outcome = StepOutcome("unverified", cause="no project_stats row")
            elif not license_name and not spdx_id:
                tier = "none"
                summary = "No license detected on this repository."
                outcome = no_signal("GitHub reported no license for this repository",
                                    known_positive=True)
            else:
                tier = _classify(spdx_id)
                label = _TIER_LABELS[tier]
                display_name = license_name or spdx_id
                summary = f"{display_name} — {label}"
                if tier == "unknown":
                    # A license IS present and this table does not carry it.
                    # Knowingly incomplete rather than absent — the one place
                    # in this step where `partial` is the honest word.
                    outcome = StepOutcome(
                        "partial", cause="SPDX id not in the risk table",
                        detail={"license_spdx_id": spdx_id})
                else:
                    outcome = StepOutcome("recovered", detail={"risk_tier": tier})

            confidence = (
                100 if tier in ("permissive", "weak_copyleft", "strong_copyleft", "source_available")
                # Nothing was examined: not a low-confidence answer, a non-answer.
                else 0 if not examined
                else 60
            )

            results.append(
                ClassificationAnnotation(
                    summary=summary,
                    analysis_step=STEP,
                    check_name="license_risk_tier",
                    candidate_classifications=[tier],
                    confidence=confidence,
                    json_properties={
                        "license_name": license_name,
                        "license_spdx_id": spdx_id,
                        "risk_tier": tier,
                        **outcome.as_row(),
                    },
                )
            )

            try:
                self.registry.upsert_finding(
                    slug, "license_classification",
                    [{
                        "check_name": "license_risk_tier",
                        "label": tier,
                        "summary": summary,
                        "confidence": confidence,
                        "detail": {"license_name": license_name, "license_spdx_id": spdx_id},
                    }],
                    surveyed_at=self._surveyed_at,
                )
            except Exception as exc:
                log.warning("Could not persist license classification for %s: %s", slug, exc)

        except Exception as exc:
            log.exception("LicenseClassifierSurveyor failed for %s", self.project.slug)
            self._warn(results, str(exc))

        return results
