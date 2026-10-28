"""Sub-surveyor: File Inventory → ResourceMeasureAnnotation.

Closes the same class of bug D5 closed for `project_code_symbols`, on the
table that far more steps depend on. `project_file_inventory` was written by
exactly three things — full RAG ingestion, IncrementalIndexer, and
IngestionPipeline.refresh_profile() (the scheduler's profile job) —
and by no survey step at all. Meanwhile `repo_file_structure`, `repo_language`,
`repo_file_classification`, `repo_file_size`, `repo_documentation` and
`repo_sub_resource_survey` all *read* it.

The practical effect was that "Coarse Profile Survey" did not do what its own
description promises. Of its four steps only `repo_data_profiling` acquired the
zipball; the other three reported whatever the inventory happened to hold from
some earlier, unrelated run. Survey a repo that has changed since it was last
ingested and it reports the previous file structure, language shape and
classification, with nothing indicating the data is stale. A repo registered
without RAG ingestion at all (the org-import/discovery path deliberately skips
it — see org_importer.py) had no self-healing path whatsoever: its inventory
stayed empty and every dependent step reported nothing forever.
DataProfilerSurveyor's own "project_file_inventory is empty. File inventory is
populated ..." message was the visible edge of exactly this.

Same self-contained microflow shape as SymbolExtractionSurveyor: acquire the
shared zipball_root resource (D6), refresh the table (write), then report what
it just wrote (read). Because `resolve_resources` guarantees one download per
SurveyOrchestrator.run() no matter how many selected steps declare
`requires_resources={"zipball_root": ...}`, adding this step costs no extra
network call in any survey that already contains a zipball-using step — it
shares the same extraction root as repo_data_profiling and
repo_symbol_extraction.

Ordering matters and is enforced by position in STEP_REGISTRY (which is also
the order "Repo Full Survey" runs, via the "*" sentinel in
repo_survey_types.csv): this step must precede every step that reads the
inventory, or the run refreshes the table only after its consumers have already
read the stale copy.

The write itself delegates to IngestionPipeline._store_file_inventory rather
than reimplementing the walk — one implementation of "what counts as a file in
this repo", shared with ingestion and refresh_profile.
"""
from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

from resource_explorer.registry import Project, ProjectRegistry
from resource_explorer.surveyors.base_surveyor import BaseSurveyor
from resource_explorer.step_outcome import StepOutcome, no_signal
from resource_explorer.surveyors.survey_report import Annotation, ResourceMeasureAnnotation

log = logging.getLogger(__name__)

STEP = "FileInventory"


class FileInventorySurveyor(BaseSurveyor):
    """Refreshes `project_file_inventory` from a freshly extracted zipball,
    then reports what it wrote.

    local_path is injected by SurveyOrchestrator via D6's requires_resources
    mechanism (StepInfo.requires_resources={"zipball_root": "local_path"}) — a
    fresh zipball extraction root, not a persistent clone.
    """

    def __init__(
        self, project: Project, registry: ProjectRegistry, local_path: str,
        surveyed_at: str | None = None,
    ) -> None:
        super().__init__(project, registry)
        self._local_path = local_path
        self._surveyed_at = surveyed_at or datetime.utcnow().isoformat()

    @property
    def step_name(self) -> str:
        return STEP

    def _file_type_annotations(self) -> list[Annotation]:
        """Egeria's own folder-survey annotations for this inventory (file_type_profile.py). A failure
        to build them is reported as a low-confidence measure, never swallowed and never fatal."""
        try:
            from resource_explorer.surveyors.file_type_profile import build_file_type_annotations

            return build_file_type_annotations(self.registry, self.project.slug, surveyed_at=self._surveyed_at)
        except Exception as exc:
            log.warning("File type profile failed for %s: %s", self.project.slug, exc)
            return [ResourceMeasureAnnotation(
                check_name="file_type_profile", summary="File type profile could not be built",
                analysis_step=STEP, confidence=0,
                explanation=f"The file type annotations were not produced: {exc}",
                resource_properties={"error": str(exc)},
                json_properties=StepOutcome("unverified", cause="file type profile failed",
                                            detail={"error": str(exc)}).as_row())]

    def run(self) -> list[Annotation]:
        results: list[Annotation] = []
        try:
            from types import SimpleNamespace

            from rich.console import Console

            from resource_explorer.ingestion.pipeline import IngestionPipeline

            local_root = Path(self._local_path)
            # Delegated rather than reimplemented: _store_file_inventory owns the
            # definition of "what counts as a file here" (skip rules, size
            # handling, per-file metadata) and is shared with full ingestion and
            # refresh_profile. A second walk here would drift from those two the
            # way the three annotation-props builders did.
            #
            # Called against a minimal stand-in rather than a real
            # IngestionPipeline: that constructor takes no arguments and eagerly
            # builds a MultiCollectionStore (a pgvector connection) plus a
            # Console, none of which this step needs — and it would open a vector
            # store connection on every survey run. _store_file_inventory touches
            # only `self.registry` and `self.console`, so those are all it gets.
            shim = SimpleNamespace(registry=self.registry, console=Console(quiet=True))
            file_count = IngestionPipeline._store_file_inventory(
                shim, self.project.slug, local_root
            )

            # The line census rides the same walk, for the same reason and via
            # the same shim (design doc D1).
            #
            # It was first placed on IngestionPipeline.run() and then on
            # refresh_profile(), and neither is reachable from the UI:
            # refresh_profile's HTTP route was retired 2026-08-20 as having no
            # caller, leaving it to the scheduler and the CLI. This step IS
            # reachable — it is what the "Coarse Profile Survey" runs — so
            # putting the census here is what makes the decomposition
            # refreshable by the person looking at it, rather than only by a
            # full RAG re-ingest.
            #
            # Idempotent and cheap relative to the walk already happening:
            # census_tree re-reads the same files _store_file_inventory just
            # stat-ed. Best-effort, because a census failure must not fail an
            # inventory that succeeded.
            IngestionPipeline._record_line_census(shim, self.project.slug, local_root)

            results.append(
                ResourceMeasureAnnotation(
                    check_name="file_inventory",
                    summary=f"{file_count} file(s) inventoried",
                    analysis_step=STEP,
                    explanation=(
                        "Refreshed project_file_inventory from a fresh zipball extraction. "
                        "Steps that read the inventory (file structure, language, "
                        "classification, file size, documentation, sub-resource survey) see "
                        "this run's contents rather than whatever an earlier ingestion left."
                    ),
                    resource_properties={
                        "file_count": file_count,
                        "surveyed_at": self._surveyed_at,
                    },
                    # A zipball that extracted to zero files is not a repo with
                    # no files — it is an extraction that produced nothing, and
                    # every step downstream reads this inventory. There is no
                    # known-positive available here: the count IS the
                    # measurement, so a zero cannot be shown to be real.
                    json_properties=(
                        StepOutcome("recovered", detail={"file_count": file_count})
                        if file_count else
                        StepOutcome("unverified",
                                    cause="zipball extraction inventoried no files")
                    ).as_row(),
                )
            )
            if file_count:       # zero files is recorded above as unverified, not as a measured zero
                results.extend(self._file_type_annotations())
        except Exception as exc:
            # Never take the whole survey down: the dependent steps degrade to
            # the previous inventory (which is exactly today's behaviour), and
            # the failure is reported rather than silently skipped.
            log.warning("FileInventorySurveyor failed for %s: %s", self.project.slug, exc)
            results.append(
                ResourceMeasureAnnotation(
                    check_name="file_inventory",
                    summary="File inventory refresh failed",
                    analysis_step=STEP,
                    confidence=0,
                    explanation=(
                        f"Could not refresh project_file_inventory: {exc}. Steps reading the "
                        "inventory in this run report against the previously stored contents."
                    ),
                    resource_properties={"file_count": 0, "error": str(exc)},
                    json_properties=StepOutcome("unverified", cause="file inventory refresh failed",
                                              detail={"error": str(exc)}).as_row(),
                )
            )
        return results
