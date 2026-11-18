"""Sub-surveyor: Manifest Parse -> project_dependencies + project_analysis_findings.

Three tables are written ONLY by IngestionPipeline, and by no survey step:

  * project_dependencies                     (_parse_dependencies, called ONLY
                                               from full ingestion, never from
                                               refresh_profile)
  * project_analysis_findings kind="ci_quality"      (_parse_ci_workflows,
                                               called from both)
  * project_analysis_findings kind="repo_conventions" (_parse_repo_conventions,
                                               called from both)

Measured 2026-08-22: dependencies present for 3 of 58 registered resources,
ci_quality for 4/58, repo_conventions for 5/58. The org-import/discovery path
deliberately skips ingestion (see org_importer.py), so for most resources
these three tables are simply empty, and DependencySurveyor/CiQualitySurveyor/
RepoConventionsSurveyor — all read-only at survey time — report a confident
nothing forever. Same trap project_file_inventory and project_code_symbols
were in before repo_file_inventory and repo_symbol_extraction closed it.

Same self-contained microflow shape as FileInventorySurveyor and
SymbolExtractionSurveyor: acquire the shared zipball_root resource (D6),
write, then report what was written. Costs no extra network call in any
survey that already has a zipball step — resolve_resources shares one
extraction root per SurveyOrchestrator.run().

Delegates to the three existing parsers (DependencyParser, CiWorkflowParser,
RepoConventionsParser) rather than reimplementing them — one implementation
of "how to parse a manifest/workflow/convention signal", shared with
ingestion. Calls the parsers directly (not IngestionPipeline._parse_*)
because those methods swallow their own exceptions into a console.print and
return None, which is exactly the information this step needs to tell
`recovered` apart from `unverified` apart from a genuine zero — the outcome
labelling this step exists to produce would be lost behind that log line.
The registry calls this step makes (upsert_dependencies / upsert_finding)
are the same calls the pipeline methods make, so both paths still write the
tables identically.

Each of the three sub-parses runs in its own try/except: one parser raising
must not prevent the other two from writing (per-item isolation, a hard
rule in this codebase — see dependency.py's own DependencySurveyor for the
precedent this file follows for outcome labelling).
"""
from __future__ import annotations

import logging
from pathlib import Path

from resource_explorer.registry import Project, ProjectRegistry
from resource_explorer.step_outcome import RECOVERED, UNVERIFIED, StepOutcome, no_signal
from resource_explorer.surveyors.base_surveyor import BaseSurveyor
from resource_explorer.surveyors.survey_report import Annotation, ResourceMeasureAnnotation
from resource_explorer.ingestion.vendored import is_vendored_abs
from resource_explorer.survey_time import now_utc_iso

log = logging.getLogger(__name__)

STEP = "ManifestParse"

# Same manifest set as dependency.py's own _MANIFESTS (the known-positive for
# "this repo could have declared dependencies") — duplicated rather than
# imported across a module boundary, same call dependency.py itself made no
# different from symbol_extraction.py's _LANGUAGE_CTYPES: small, stable list,
# kept in sync manually. Unlike dependency.py (which intersects against
# project_file_inventory basenames), this step walks the freshly extracted
# zipball_root directly, so it is its own known-positive check rather than
# trusting an inventory another step may not have refreshed yet.
_MANIFESTS = frozenset({
    "pyproject.toml", "requirements.txt", "setup.py", "setup.cfg", "Pipfile",
    "package.json", "pom.xml", "build.gradle", "build.gradle.kts",
    "go.mod", "Cargo.toml", "Gemfile", "composer.json",
})


def _manifests_present(local_root: Path) -> list[str]:
    """Basenames from _MANIFESTS found anywhere under local_root — the
    known-positive check for the dependency sub-parse. Walking the whole
    tree here (rather than reusing project_file_inventory) means this check
    is correct even in a run where repo_file_inventory hasn't executed yet
    or was never selected."""
    found: set[str] = set()
    for p in local_root.rglob("*"):
        # A vendored dependency's own package.json is not this repository's
        # manifest; dependency_parser already skips these ad hoc.
        if p.is_file() and p.name in _MANIFESTS and not is_vendored_abs(p, local_root):
            found.add(p.name)
    return sorted(found)


class ManifestParseSurveyor(BaseSurveyor):
    """Parses dependency manifests, CI workflow content, and repo-convention
    signals from a freshly extracted zipball, writing project_dependencies
    and project_analysis_findings (kind="ci_quality"/"repo_conventions"),
    then reporting what it wrote — one ResourceMeasureAnnotation per
    sub-parse, each carrying its own StepOutcome.

    local_path is injected by SurveyOrchestrator via D6's requires_resources
    mechanism (StepInfo.requires_resources={"zipball_root": "local_path"}) —
    a fresh zipball extraction root, not a persistent clone.
    """

    def __init__(
        self, project: Project, registry: ProjectRegistry, local_path: str,
        surveyed_at: str | None = None,
    ) -> None:
        super().__init__(project, registry)
        self._local_path = local_path
        self._surveyed_at = surveyed_at or now_utc_iso()

    @property
    def step_name(self) -> str:
        return STEP

    def run(self) -> list[Annotation]:
        local_root = Path(self._local_path)
        results: list[Annotation] = []
        # One independent try/except per sub-parse, not one around all of
        # them: a bug in the CI-workflow parser must not cost the dependency
        # write, and vice versa. This is the isolation contract
        # file_inventory.py and symbol_extraction.py don't need (they write one
        # table each) but this step does, since it bundles five.
        results.append(self._parse_dependencies(local_root))
        results.append(self._parse_ci_quality(local_root))
        results.append(self._parse_repo_conventions(local_root))
        results.append(self._parse_supply_chain(local_root))
        results.append(self._parse_distribution(local_root))
        return results

    def _parse_distribution(self, local_root: Path) -> Annotation:
        """What the repository declares itself to be: distribution name,
        entry points, packages, publish workflow. Curate's first claim as a
        fact rather than an inference (Repo Handoff checks 2 and 3)."""
        slug = self.project.slug
        try:
            from resource_explorer.ingestion.distribution_parser import DistributionParser

            findings = DistributionParser().parse(local_root)
            if findings:
                self.registry.upsert_finding(slug, "distribution", findings,
                                             surveyed_at=self._surveyed_at)
                outcome = StepOutcome(RECOVERED, known_positive=True,
                                      detail={"declared": len(findings)})
                summary = (f"{len(findings)} declared distribution(s): "
                           + ", ".join(f["detail"]["name"] for f in findings[:4]))
            else:
                # A repo with no [project] table / package.json name declares
                # nothing. A real zero when a manifest exists; unverified
                # when none does, because there was nothing to read.
                manifests = _manifests_present(local_root)
                if manifests:
                    outcome = no_signal("manifests present but none declares a distribution name",
                                        known_positive=True)
                else:
                    outcome = StepOutcome(UNVERIFIED, cause="no_manifest_found")
                summary = "No declared distribution"
            self._record_snapshot("manifest_parse_distribution", len(findings), outcome)
            return ResourceMeasureAnnotation(
                check_name="distribution_manifest",
                summary=summary, analysis_step=STEP,
                confidence=100 if outcome.is_conclusive else 50,
                explanation=(
                    "Refreshed project_analysis_findings (kind=\"distribution\") from "
                    "a fresh zipball extraction — the distribution name, entry points, "
                    "packages and publish workflow the manifests declare, which Curate "
                    "reads for its first claim."
                ),
                resource_properties={"finding_count": len(findings)},
                json_properties=outcome.as_row(),
            )
        except Exception as exc:
            log.warning("ManifestParseSurveyor distribution parse failed for %s: %s", slug, exc)
            outcome = StepOutcome(UNVERIFIED, cause="parse_error", detail={"error": str(exc)})
            self._record_snapshot("manifest_parse_distribution", 0, outcome)
            return ResourceMeasureAnnotation(
                check_name="distribution_manifest",
                summary="Distribution parse failed", analysis_step=STEP, confidence=0,
                explanation=f"Could not parse declared distributions: {exc}",
                resource_properties={"finding_count": 0, "error": str(exc)},
                json_properties=outcome.as_row(),
            )

    def _record_snapshot(self, kind: str, count: int, outcome: StepOutcome) -> None:
        """Generic project_analysis_metrics snapshot for this sub-parse's
        own run history — same pattern SymbolExtractionSurveyor uses for its
        "symbol_extraction" kind, kept separate per sub-parse so each is
        independently trendable. Failure here must not affect the primary
        write it is reporting on, so it gets its own try/except."""
        try:
            self.registry.upsert_metric(
                self.project.slug, kind, {"count": count},
                detail=outcome.as_row(), surveyed_at=self._surveyed_at,
            )
        except Exception as exc:
            log.warning("Could not persist %s snapshot for %s: %s", kind, self.project.slug, exc)

    def _parse_dependencies(self, local_root: Path) -> Annotation:
        slug = self.project.slug
        try:
            from resource_explorer.ingestion.dependency_parser import DependencyParser

            deps = DependencyParser().parse(local_root, slug)
            manifests = _manifests_present(local_root)
            if deps:
                self.registry.upsert_dependencies(slug, deps)
                outcome = StepOutcome(RECOVERED, known_positive=True,
                                      detail={"matched": len(deps), "manifests": manifests})
                summary = f"{len(deps)} dependency(s) parsed from {len(manifests)} manifest(s)"
            elif manifests:
                # The known-positive fired (a manifest is right there) and the
                # parser still found nothing declared in it — that is a parser
                # gap, not a repo with no dependencies. unverified, not a
                # provable zero. Mirrors dependency.py's own
                # "manifests_present_no_deps_extracted" branch exactly.
                outcome = StepOutcome(UNVERIFIED, cause="manifest_present_no_deps_parsed",
                                      detail={"manifests": manifests})
                summary = (f"No dependencies parsed despite {len(manifests)} manifest(s) "
                           f"present: {', '.join(manifests)}")
            else:
                # No manifest anywhere in the extracted tree: the whole
                # zipball was walked (that walk IS the known-positive check),
                # so "this repo declares no dependencies" is the real,
                # provable answer.
                outcome = no_signal("no_dependency_manifest", known_positive=True)
                summary = "No dependency manifest found in the extracted tree"
            self._record_snapshot("manifest_parse_dependencies", len(deps), outcome)
            return ResourceMeasureAnnotation(
                check_name="dependency_manifest",
                summary=summary, analysis_step=STEP,
                confidence=100 if outcome.is_conclusive else 50,
                explanation=(
                    "Refreshed project_dependencies from a fresh zipball extraction. "
                    "project_dependencies was previously written only by full "
                    "ingestion, never by a survey step or by refresh_profile."
                ),
                resource_properties={"dependency_count": len(deps),
                                     "manifests_present": manifests},
                json_properties=outcome.as_row(),
            )
        except Exception as exc:
            log.warning("ManifestParseSurveyor dependency parse failed for %s: %s", slug, exc)
            outcome = StepOutcome(UNVERIFIED, cause="parse_error", detail={"error": str(exc)})
            self._record_snapshot("manifest_parse_dependencies", 0, outcome)
            return ResourceMeasureAnnotation(
                check_name="dependency_manifest",
                summary="Dependency parse failed", analysis_step=STEP, confidence=0,
                explanation=f"Could not parse dependency manifests: {exc}",
                resource_properties={"dependency_count": 0, "error": str(exc)},
                json_properties=outcome.as_row(),
            )

    def _parse_ci_quality(self, local_root: Path) -> Annotation:
        slug = self.project.slug
        try:
            from resource_explorer.ingestion.ci_workflow_parser import CiWorkflowParser

            findings = CiWorkflowParser().parse(local_root)
            if findings:
                self.registry.upsert_finding(slug, "ci_quality", findings,
                                             surveyed_at=self._surveyed_at)
                outcome = StepOutcome(RECOVERED, known_positive=True,
                                      detail={"matched": len(findings)})
                summary = f"{len(findings)} CI quality check(s) evaluated"
            else:
                # CiWorkflowParser returns [] both when .github/workflows is
                # absent and when it exists but nothing could be read. Either
                # way this cannot be turned into a provable zero: the parser
                # only ever looks at GitHub Actions, so a repo running CI via
                # Travis/CircleCI/Jenkins/etc reads identically to a repo
                # with no CI at all. Per step_outcome's rule, that is
                # unverified, not no_signal.
                outcome = StepOutcome(UNVERIFIED, cause="no_github_actions_workflows_found")
                summary = "No .github/workflows content to evaluate"
            self._record_snapshot("manifest_parse_ci_quality", len(findings), outcome)
            return ResourceMeasureAnnotation(
                check_name="ci_workflow_manifest",
                summary=summary, analysis_step=STEP,
                confidence=100 if outcome.is_conclusive else 50,
                explanation=(
                    "Refreshed project_analysis_findings (kind=\"ci_quality\") from a "
                    "fresh zipball extraction. Previously written only by full "
                    "ingestion and refresh_profile, never by a survey step."
                ),
                resource_properties={"finding_count": len(findings)},
                json_properties=outcome.as_row(),
            )
        except Exception as exc:
            log.warning("ManifestParseSurveyor CI workflow parse failed for %s: %s", slug, exc)
            outcome = StepOutcome(UNVERIFIED, cause="parse_error", detail={"error": str(exc)})
            self._record_snapshot("manifest_parse_ci_quality", 0, outcome)
            return ResourceMeasureAnnotation(
                check_name="ci_workflow_manifest",
                summary="CI workflow parse failed", analysis_step=STEP, confidence=0,
                explanation=f"Could not parse CI workflow content: {exc}",
                resource_properties={"finding_count": 0, "error": str(exc)},
                json_properties=outcome.as_row(),
            )

    def _parse_supply_chain(self, local_root: Path) -> Annotation:
        """Supply-chain findings from the same extracted zipball.

        Added for the same reason the other three sub-parses exist: the parser
        runs at ingestion and refresh_profile, so for a repo that arrived via
        org-import or discovery — which deliberately skip ingestion — the table
        was simply empty, and FossScorecard's three supply-chain checks reported
        unknown forever with no way for a survey to change that.
        """
        slug = self.project.slug
        try:
            from resource_explorer.ingestion.supply_chain_parser import SupplyChainParser

            findings = SupplyChainParser().parse(local_root)
            if findings:
                self.registry.upsert_finding(slug, "supply_chain", findings,
                                             surveyed_at=self._surveyed_at)
                outcome = StepOutcome(RECOVERED, known_positive=True,
                                      detail={"matched": len(findings)})
                summary = f"{len(findings)} supply-chain check(s) evaluated"
            else:
                # Same ambiguity CiWorkflowParser has, and for the same reason:
                # [] means "no .github/workflows", which is indistinguishable
                # here from a repo whose CI lives elsewhere. Not a provable
                # zero, so unverified rather than no_signal.
                outcome = StepOutcome(UNVERIFIED, cause="no_github_actions_workflows_found")
                summary = "No workflow YAML to evaluate for supply-chain signals"
            self._record_snapshot("manifest_parse_supply_chain", len(findings), outcome)
            return ResourceMeasureAnnotation(
                check_name="supply_chain_manifest",
                summary=summary, analysis_step=STEP,
                confidence=100 if outcome.is_conclusive else 50,
                explanation=(
                    "Refreshed project_analysis_findings (kind=\"supply_chain\") from "
                    "a fresh zipball extraction — token permissions, action pinning "
                    "and dangerous-workflow patterns, which FossScorecard reads."
                ),
                resource_properties={"finding_count": len(findings)},
                json_properties=outcome.as_row(),
            )
        except Exception as exc:
            log.warning("ManifestParseSurveyor supply-chain parse failed for %s: %s", slug, exc)
            outcome = StepOutcome(UNVERIFIED, cause="parse_error", detail={"error": str(exc)})
            self._record_snapshot("manifest_parse_supply_chain", 0, outcome)
            return ResourceMeasureAnnotation(
                check_name="supply_chain_manifest",
                summary="Supply-chain parse failed", analysis_step=STEP, confidence=0,
                explanation=f"Could not parse supply-chain signals: {exc}",
                resource_properties={"finding_count": 0, "error": str(exc)},
                json_properties=outcome.as_row(),
            )

    def _parse_repo_conventions(self, local_root: Path) -> Annotation:
        slug = self.project.slug
        try:
            from resource_explorer.ingestion.repo_conventions_parser import RepoConventionsParser

            findings = RepoConventionsParser().parse(local_root)
            if findings:
                self.registry.upsert_finding(slug, "repo_conventions", findings,
                                             surveyed_at=self._surveyed_at)
                outcome = StepOutcome(RECOVERED, known_positive=True,
                                      detail={"matched": len(findings)})
                summary = f"{len(findings)} repo convention check(s) evaluated"
            else:
                # RepoConventionsParser's own docstring: empty "may be...only
                # if the repo somehow has zero files (never in practice)".
                # There is no known-positive that would make an empty result
                # here provable rather than suspicious, so unverified per
                # step_outcome's rule, exactly as instructed for this
                # sub-parse — never no_signal.
                outcome = StepOutcome(UNVERIFIED, cause="repo_conventions_parser_returned_no_findings")
                summary = "No repo convention signals produced"
            self._record_snapshot("manifest_parse_conventions", len(findings), outcome)
            return ResourceMeasureAnnotation(
                check_name="repo_conventions_manifest",
                summary=summary, analysis_step=STEP,
                confidence=100 if outcome.is_conclusive else 50,
                explanation=(
                    "Refreshed project_analysis_findings (kind=\"repo_conventions\") "
                    "from a fresh zipball extraction. Previously written only by "
                    "full ingestion and refresh_profile, never by a survey step."
                ),
                resource_properties={"finding_count": len(findings)},
                json_properties=outcome.as_row(),
            )
        except Exception as exc:
            log.warning("ManifestParseSurveyor repo conventions parse failed for %s: %s", slug, exc)
            outcome = StepOutcome(UNVERIFIED, cause="parse_error", detail={"error": str(exc)})
            self._record_snapshot("manifest_parse_conventions", 0, outcome)
            return ResourceMeasureAnnotation(
                check_name="repo_conventions_manifest",
                summary="Repo conventions parse failed", analysis_step=STEP, confidence=0,
                explanation=f"Could not parse repo convention signals: {exc}",
                resource_properties={"finding_count": 0, "error": str(exc)},
                json_properties=outcome.as_row(),
            )
