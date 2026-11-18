"""Sub-surveyor: Security Hygiene → RequestForActionAnnotation."""
from __future__ import annotations

import logging

from resource_explorer.registry import Project, ProjectRegistry
from resource_explorer.step_outcome import UNVERIFIED, from_upstream_table
from resource_explorer.survey_time import now_utc_iso
from resource_explorer.surveyors.base_surveyor import BaseSurveyor
from resource_explorer.surveyors.survey_report import (
    Annotation,
    ClassificationAnnotation,
    RequestForActionAnnotation,
    findings_from_annotations,
)

log = logging.getLogger(__name__)

STEP = "SecurityHygieneCheck"

# Files whose presence indicates good security hygiene
_SECURITY_FILES = {
    "SECURITY.md": "Security policy",
    "SECURITY.rst": "Security policy",
    ".github/SECURITY.md": "Security policy (GitHub)",
}

_CI_INDICATORS = {
    ".github/workflows": "GitHub Actions CI",
    ".travis.yml": "Travis CI",
    "Jenkinsfile": "Jenkins CI",
    ".circleci/config.yml": "CircleCI",
    ".gitlab-ci.yml": "GitLab CI",
    "azure-pipelines.yml": "Azure Pipelines",
}

_LICENSE_FILES = {"LICENSE", "LICENSE.md", "LICENSE.txt", "LICENSE.rst", "COPYING"}


class SecurityHygieneSurveyor(BaseSurveyor):
    """
    Checks indexed file paths for the presence of security, CI, and license
    artifacts.  Emits RequestForAction for each gap found, and
    ClassificationAnnotation for each artifact present.

    Renamed from "SecuritySurveyor" — that name implied real security
    scanning (secrets, CVEs, SAST), but this only ever checked 3 hygiene
    artifacts (SECURITY.md, CI config, license presence), matching its own
    STEP = "SecurityHygieneCheck". The rename frees "security" as a family
    name for future analyses that do real scanning (secret detection,
    dependency-vulnerability/CVE checks, SAST, branch-protection audits) —
    see repo_survey_definition_adapter.py's ANALYSIS_KINDS registry,
    `family="security"`. Identifiers (analysis_catalog id "security_scan",
    step key "repo_security") deliberately did NOT change — only the
    Python class/file name, to avoid breaking existing schedules/data.
    """

    def __init__(self, project: Project, registry: ProjectRegistry, surveyed_at: str | None = None) -> None:
        super().__init__(project, registry)
        # Shared run-timestamp from SurveyOrchestrator, so this run's
        # findings can be correlated with other surveyors' writes from the
        # same orchestrator.run() call (Phase B, D1) — defaults to a fresh
        # timestamp so this surveyor stays independently callable/testable.
        self._surveyed_at = surveyed_at or now_utc_iso()

    @property
    def step_name(self) -> str:
        return STEP

    def run(self) -> list[Annotation]:
        results: list[Annotation] = []
        try:
            # project_file_inventory — every file in the repo — NOT
            # project_code_symbols, which is what this read until 2026-08-22.
            #
            # That was a real bug, not a style point: project_code_symbols only
            # ever holds .py/.js/.java/.go source files, so SECURITY.md and
            # .github/workflows/* could not appear in it by construction. Every
            # repo therefore failed the security-policy and CI checks
            # unconditionally, and the RFAs said so with confidence 90 and 85.
            # Confirmed against live data before changing: docling has
            # SECURITY.md and 13 workflow files in its inventory and zero of
            # either in project_code_symbols. documentation.py had already been
            # moved to the inventory for exactly this reason and left a comment
            # saying why; this step was missed.
            inventory = [p.replace("\\", "/")
                         for p in self.registry.get_file_inventory(self.project.slug)]
            paths = inventory
            filenames = {p.rsplit("/", 1)[-1] for p in paths}

            # Also check project_stats for license field — via the named
            # registry accessor (D2(c), docs/repo-survey-catalog-completion-
            # plan.md), not a hand-rolled query — this and health.py were a
            # confirmed duplicate of the same "latest project_stats row"
            # pattern.
            stats_row = self.registry.get_latest_project_stats(self.project.slug)
            license_from_stats = (stats_row["license"] if stats_row else "") or ""

            # An empty inventory cannot produce a gap. Reporting one would raise
            # three RequestForActions telling the user to add files that may
            # already be there — the most expensive possible form of this bug,
            # since an RFA is a request for someone's time.
            outcome = from_upstream_table(
                len(inventory), len(inventory),
                empty_table_cause="empty_file_inventory",
                no_match_cause="no_hygiene_artifacts")
            if outcome.outcome == UNVERIFIED:
                results.append(
                    ClassificationAnnotation(
                        summary="Security hygiene not checked — the file inventory is empty",
                        analysis_step=STEP,
                        check_name="inventory_available",
                        label="unverified",
                        candidate_classifications=[],
                        confidence=100,
                        explanation=(
                            "SECURITY.md, CI configuration and LICENSE are looked up in "
                            "project_file_inventory, which holds no rows for this repo. "
                            "Run repo_file_inventory (or a Profile refresh) first — an "
                            "absent file and an unread repository are not the same finding."
                        ),
                        json_properties=outcome.as_row(),
                    )
                )
                self._persist(results)
                return results

            # ── Security policy ───────────────────────────────────────────────
            has_security = any(
                fname in filenames or any(p.endswith(fname) for p in paths)
                for fname in _SECURITY_FILES
            )
            if has_security:
                results.append(
                    ClassificationAnnotation(
                        summary="Security policy file present",
                        analysis_step=STEP,
                        check_name="security_policy",
                        label="pass",
                        candidate_classifications=["HasSecurityPolicy"],
                        confidence=100,
                    )
                )
            else:
                results.append(
                    RequestForActionAnnotation(
                        summary="No SECURITY.md found",
                        analysis_step=STEP,
                        check_name="security_policy",
                        label="gap",
                        action_requested="Add a SECURITY.md file describing the vulnerability disclosure process",
                        action_target_name="SECURITY.md",
                        explanation="A security policy helps users report vulnerabilities responsibly.",
                        confidence=90,
                    )
                )

            # ── CI configuration ──────────────────────────────────────────────
            has_ci = any(
                any(p == indicator or p.startswith(indicator + "/") for p in paths)
                for indicator in _CI_INDICATORS
            ) or any(ind_file in filenames for ind_file in _CI_INDICATORS)

            if has_ci:
                ci_found = [
                    label for indicator, label in _CI_INDICATORS.items()
                    if any(p == indicator or p.startswith(indicator + "/") for p in paths)
                    or indicator in filenames
                ]
                results.append(
                    ClassificationAnnotation(
                        summary=f"CI configuration present: {', '.join(ci_found)}",
                        analysis_step=STEP,
                        check_name="ci_config",
                        label="pass",
                        candidate_classifications=ci_found,
                        confidence=95,
                    )
                )
            else:
                results.append(
                    RequestForActionAnnotation(
                        summary="No CI configuration detected",
                        analysis_step=STEP,
                        check_name="ci_config",
                        label="gap",
                        action_requested="Add a CI configuration (e.g. GitHub Actions workflow)",
                        action_target_name=".github/workflows/",
                        explanation="Automated testing improves code quality and contributor confidence.",
                        confidence=85,
                    )
                )

            # ── License ───────────────────────────────────────────────────────
            has_license = bool(license_from_stats) or bool(filenames & _LICENSE_FILES)
            if has_license:
                license_label = license_from_stats or "Present (file detected)"
                results.append(
                    ClassificationAnnotation(
                        summary=f"License: {license_label}",
                        analysis_step=STEP,
                        check_name="license",
                        label="pass",
                        candidate_classifications=[license_label],
                        confidence=100,
                    )
                )
            else:
                results.append(
                    RequestForActionAnnotation(
                        summary="No license file detected",
                        analysis_step=STEP,
                        check_name="license",
                        label="gap",
                        action_requested="Add a LICENSE file to clarify terms of use",
                        action_target_name="LICENSE",
                        explanation="Projects without a license are legally all-rights-reserved by default.",
                        confidence=90,
                    )
                )

            self._persist(results)

        except Exception as exc:
            log.exception("SecurityHygieneSurveyor failed for %s", self.project.slug)
            self._warn(results, str(exc))

        return results

    def _persist(self, annotations: list) -> None:
        """Write this run's findings, DERIVED from the annotations above.

        Derived rather than hand-written beside them: the two records had
        drifted with nothing to catch it — the SECURITY.md check emitted
        confidence=90 on the annotation and stored a finding that omitted
        confidence, so the table defaulted it to 100. Same check, same run,
        two numbers, no error anywhere. Deriving makes that impossible by
        construction rather than avoided by care.

        Also extracted so the unverified early return persists through exactly
        the same path as a completed run — otherwise "we could not check" would
        be the one outcome that left no row.
        """
        try:
            # "label" here is the pass/gap/unverified verdict; other finding
            # kinds (e.g. documentation) use the same column for a different
            # kind of value, which is why the generic schema names it `label`.
            # supersedes_previous=True: this one call is the whole run's
            # answer for (slug, "security_hygiene", "") — part of the
            # security family opted in alongside cve_scan (docs/Backlog.md).
            self.registry.upsert_finding(
                self.project.slug, "security_hygiene",
                findings_from_annotations(annotations),
                surveyed_at=self._surveyed_at,
                supersedes_previous=True,
            )
        except Exception as exc:
            log.warning("Could not persist security hygiene findings for %s: %s",
                        self.project.slug, exc)
