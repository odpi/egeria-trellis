"""Sub-surveyor: Dependencies → DataClassAnnotation.

`project_dependencies` is written by exactly one thing — `IngestionPipeline`
(`ingestion/pipeline.py`) — and by no survey step, the same trap that
`project_file_inventory` and `project_code_symbols` were in before
`repo_file_inventory` and `repo_symbol_extraction` were added. Measured
2026-08-22: **3 of 58 registered resources had any dependency row at all**, and
the repos with none ship manifests — docling a pyproject.toml, milvus a
Cargo.toml, go.mod and requirements.txt, trellis a package.json and a
pyproject.toml, egeria_git a build.gradle. All of them reported nothing, and
reported it silently.

This step therefore has an unusually good known-positive available, better than
the generic "the upstream table has rows": **a dependency manifest in the file
inventory**. If a manifest is present and no dependencies were extracted, the
zero is demonstrably wrong — that is `unverified`, not a finding. If no manifest
is present, a repo declaring no dependencies is a real and unremarkable answer,
and `no_signal` is honest.

The missing extractor itself is a separate piece of work (a `repo_dependency_
extraction` step, mirroring `repo_symbol_extraction`) — logged in
docs/Backlog.md rather than built here. This step's job is to stop the gap
being invisible.
"""
from __future__ import annotations

import logging
from collections import defaultdict

from resource_explorer.registry import Project, ProjectRegistry
from resource_explorer.step_outcome import RECOVERED, UNVERIFIED, StepOutcome, no_signal
from resource_explorer.surveyors.base_surveyor import BaseSurveyor
from resource_explorer.surveyors.survey_report import Annotation, DataClassAnnotation, ResourceMeasureAnnotation

log = logging.getLogger(__name__)

STEP = "DependencyAnalysis"

# Manifests whose presence proves dependencies were declarable — the
# known-positive for this step. Basename match, any depth: a monorepo keeps
# them in subdirectories.
_MANIFESTS = frozenset({
    "pyproject.toml", "requirements.txt", "setup.py", "setup.cfg", "Pipfile",
    "package.json", "pom.xml", "build.gradle", "build.gradle.kts",
    "go.mod", "Cargo.toml", "Gemfile", "composer.json", "*.csproj",
})


class DependencySurveyor(BaseSurveyor):
    """
    Reads project_dependencies and produces:
      - One DataClassAnnotation per ecosystem (PyPI, npm, Maven, …)
        listing the dependency names found.
      - One ResourceMeasureAnnotation with total counts by dep_type and ecosystem.
    """

    @property
    def step_name(self) -> str:
        return STEP

    def _manifests_in_inventory(self) -> tuple[int, list[str]]:
        """(inventory size, manifests this repo ships) — both, because the
        caller needs them for different reasons.

        The manifest list decides whether a zero is wrong. The inventory size
        decides whether the *absence* of a manifest can be believed: an empty
        inventory shows nothing either way, so it cannot support a provable
        zero, and returning only the list would silently conflate "looked, no
        manifest" with "could not look".
        """
        try:
            inventory = self.registry.get_file_inventory(self.project.slug)
        except Exception as exc:
            log.warning("Could not read file inventory for %s: %s", self.project.slug, exc)
            return 0, []
        names = {p.replace("\\", "/").rsplit("/", 1)[-1] for p in inventory}
        return len(inventory), sorted(names & _MANIFESTS)

    def _nothing_found(self, outcome: StepOutcome, manifests: list[str]) -> Annotation:
        # Branch on the manifest list, not on the outcome: two different causes
        # both land on `unverified` here and they need different words.
        if manifests:
            summary = (f"No dependencies extracted, but {len(manifests)} manifest(s) "
                       f"are present: {', '.join(manifests)}")
            explanation = (
                "project_dependencies is written only by the ingestion pipeline, "
                "never by a survey step. A repo that ships a manifest and records no "
                "dependencies has not been analysed — this is not a finding that it "
                "has none. Run a full ingestion/refresh for this repo."
            )
        elif outcome.known_positive:
            summary = "No dependencies — this repo declares no dependency manifest"
            explanation = (
                "The file inventory contains none of the recognised manifests "
                "(pyproject.toml, package.json, pom.xml, go.mod, Cargo.toml, …), so "
                "having no dependencies is the real answer rather than a gap."
            )
        else:
            summary = "No dependencies found, and the file inventory is empty"
            explanation = (
                "With no inventory there is no way to see whether this repo ships a "
                "manifest, so neither 'none declared' nor 'extraction missing' can be "
                "distinguished. Run repo_file_inventory (or a Profile refresh) first."
            )
        return ResourceMeasureAnnotation(
            check_name="dependency_summary",
            summary=summary, analysis_step=STEP, confidence=100,
            explanation=explanation,
            resource_properties={"total": 0, "by_ecosystem": {}, "by_type": {}},
            json_properties={"source": "project_dependencies",
                             "manifests_present": manifests, **outcome.as_row()},
        )

    def run(self) -> list[Annotation]:
        results: list[Annotation] = []
        try:
            deps = self.registry.query_dependencies(self.project.slug)
            if not deps:
                inventory_size, manifests = self._manifests_in_inventory()
                if manifests:
                    outcome = StepOutcome(
                        UNVERIFIED, cause="manifests_present_no_deps_extracted",
                        detail={"manifests": manifests})
                else:
                    # With no inventory there is no known-positive at all: we
                    # cannot see whether this repo ships a manifest, so "it
                    # declares no dependencies" is a claim the run has not
                    # earned. no_signal() degrades to unverified for exactly
                    # this, which is why known_positive is passed rather than
                    # assumed.
                    outcome = no_signal("no_dependency_manifest",
                                        known_positive=inventory_size > 0)
                results.append(self._nothing_found(outcome, manifests))
                return results

            by_ecosystem: dict[str, list[dict]] = defaultdict(list)
            for d in deps:
                by_ecosystem[d.get("ecosystem") or "unknown"].append(d)

            # The aggregate ResourceMeasureAnnotation is appended LAST, below,
            # after every per-ecosystem DataClassAnnotation — so its index in
            # THIS run's results list is `len(by_ecosystem)`, known up front
            # (the dict is fully populated already). annotation-linking-plan
            # Phase 2: each per-ecosystem annotation is evidence for that
            # aggregate — a same-run, list-index signal, safe only because it
            # is set here about this run's own list (see `Annotation.
            # evidence_of`'s docstring for why cross-run reconstruction is not
            # safe).
            summary_index = len(by_ecosystem)
            for ecosystem, items in sorted(by_ecosystem.items()):
                names = [d["dep_name"] for d in items]
                dep_types = list({d.get("dep_type") or "runtime" for d in items})
                results.append(
                    DataClassAnnotation(
                        check_name="dependencies_by_ecosystem",
                        item_key=ecosystem,
                        summary=f"{len(names)} {ecosystem} dependency(s): {', '.join(names[:8])}"
                        + (" …" if len(names) > 8 else ""),
                        analysis_step=STEP,
                        candidate_data_class_names=names,
                        confidence=95,
                        json_properties={
                            "ecosystem": ecosystem,
                            "dep_types": dep_types,
                            # Each entry is a dependency with TWO ENDS and a kind
                            # (DESIGN-DEPENDENCY-ROW-TWO-ENDS.md): the manifest's directory
                            # requires the package; the manifest file is the evidence (its line is
                            # not recorded, so none is claimed).
                            "dependencies": [
                                {"name": d["dep_name"], "version": d.get("dep_version", ""), "type": d.get("dep_type", ""),
                                 "relation": "requires", "target_type": "package", "kind": "build-time",
                                 "dependent": (d.get("source_file") or "").rsplit("/", 1)[0] if "/" in (d.get("source_file") or "") else "",
                                 "evidence": d.get("source_file") or ""}
                                for d in items
                            ],
                        },
                        evidence_of=summary_index,
                    )
                )

            # Aggregate counts
            by_type: dict[str, int] = defaultdict(int)
            by_eco_count: dict[str, int] = defaultdict(int)
            for d in deps:
                by_type[d.get("dep_type") or "runtime"] += 1
                by_eco_count[d.get("ecosystem") or "unknown"] += 1

            results.append(
                ResourceMeasureAnnotation(
                    check_name="dependency_total",
                    summary=f"{len(deps)} total dependencies across {len(by_ecosystem)} ecosystem(s)",
                    analysis_step=STEP,
                    resource_properties={
                        "total": len(deps),
                        "by_ecosystem": dict(by_eco_count),
                        "by_type": dict(by_type),
                    },
                    json_properties=StepOutcome(
                        RECOVERED, known_positive=True,
                        detail={"matched": len(deps)}).as_row(),
                )
            )

        except Exception as exc:
            log.exception("DependencySurveyor failed for %s", self.project.slug)
            self._warn(results, str(exc))

        return results
