"""Sub-surveyor: File Size → ResourceMeasureAnnotation + RequestForAction.

Uses project_file_inventory (populated during add/refresh) to produce
precise per-file size metrics.  The FileStructureSurveyor reports a rough
`repo_size_kb` from the GitHub API; this surveyor gives exact sizing from
the actual stored inventory and flags oversized individual files.
"""
from __future__ import annotations

from resource_explorer.byte_format import format_bytes

import logging
from collections import defaultdict
from pathlib import Path

from resource_explorer.registry import Project, ProjectRegistry
from resource_explorer.step_outcome import UNVERIFIED, StepOutcome, from_upstream_table
from resource_explorer.surveyors.base_surveyor import BaseSurveyor
from resource_explorer.surveyors.file_classifier.type_cache import get_cache
from resource_explorer.surveyors.scoping import path_matches_scope
from resource_explorer.surveyors.survey_report import (
    Annotation,
    RequestForActionAnnotation,
    ResourceMeasureAnnotation,
)

log = logging.getLogger(__name__)

STEP = "FileSizeAnalysis"

_LARGE_FILE_THRESHOLD_MB = 50
_VERY_LARGE_FILE_THRESHOLD_MB = 200


def _fmt_size(size_bytes: int) -> str:
    return format_bytes(size_bytes)


class FileSizeSurveyor(BaseSurveyor):
    """
    Reads project_file_inventory to produce size-based annotations:
      - ResourceMeasureAnnotation: total size, average file size, size by type group
      - ResourceMeasureAnnotation: top-10 largest files
      - RequestForActionAnnotation: any file exceeding the large-file threshold
    """

    def __init__(
        self, project: Project, registry: ProjectRegistry,
        scope_locator: str = "",
    ) -> None:
        super().__init__(project, registry)
        # Repo scope-narrowing funnel plan (docs/repo-scope-narrowing-funnel.md),
        # D5/D6 — "" (default) = whole-repo, unchanged from every existing
        # caller; a non-empty locator narrows to one cataloged sub-resource
        # (this kind is target_shape="corpus" — a path-prefix filter).
        self._scope_locator = scope_locator

    @property
    def step_name(self) -> str:
        return STEP

    def _record_outcome(self, outcome: StepOutcome, *, total_bytes: int,
                        total_files: int) -> None:
        """Persist the run's headline numbers together with what they mean.

        This step measured nothing before today — it built annotations and
        returned. That was survivable while a zero could only mean "no files",
        but the inventory can be empty for a repo that was never ingested, and
        an annotation-less return is invisible from every card and every query.
        One metric row, carrying the outcome, is the smallest thing that makes
        the difference between "0 bytes" and "we could not tell" legible after
        the run is over.
        """
        try:
            self.registry.upsert_metric(
                self.project.slug, "repo_file_size",
                {"total_size_bytes": total_bytes, "total_files": total_files},
                detail=outcome.as_row(),
                scope_locator=self._scope_locator,
            )
        except Exception as exc:
            log.warning("Could not persist file-size outcome for %s: %s",
                        self.project.slug, exc)

    def _nothing_measured(self, outcome: StepOutcome) -> Annotation:
        """The annotation for a run that sized nothing, saying which kind of
        nothing it was — the whole point of the outcome vocabulary."""
        if outcome.outcome == UNVERIFIED:
            summary = "File sizes not measured — the file inventory is empty"
            explanation = (
                "project_file_inventory holds no rows for this repo, so there was "
                "nothing to size. This is not a finding about the repository: run "
                "repo_file_inventory (or a Profile refresh) first, then re-run."
            )
        else:
            summary = f"No files matched the scope '{self._scope_locator}'"
            explanation = (
                "The inventory has rows, so the repository was measured — none of "
                "its files fall under this scope locator."
            )
        return ResourceMeasureAnnotation(
            check_name="file_size_summary",
            summary=summary, analysis_step=STEP, confidence=100,
            explanation=explanation,
            resource_properties={"total_files": 0, "total_size_bytes": 0},
            json_properties={"source": "project_file_inventory", **outcome.as_row()},
        )

    def run(self) -> list[Annotation]:
        results: list[Annotation] = []
        try:
            inventory = self.registry.get_file_inventory_with_sizes(self.project.slug)
            rows_available = len(inventory)
            rows = inventory
            if self._scope_locator:
                rows = [r for r in rows if path_matches_scope(r["file_path"], self._scope_locator)]

            # rows_available is counted before the scope filter on purpose: an
            # empty inventory and a scope that matched nothing are different
            # answers, and only the first means this step could not run.
            outcome = from_upstream_table(
                rows_available, len(rows),
                empty_table_cause="empty_file_inventory",
                no_match_cause="no_files_in_scope",
                scope_locator=self._scope_locator,
            )
            if not rows:
                self._record_outcome(outcome, total_bytes=0, total_files=0)
                results.append(self._nothing_measured(outcome))
                return results

            cache = get_cache()
            total_bytes = sum(r["file_size_bytes"] for r in rows)
            total_files = len(rows)
            avg_bytes = total_bytes // total_files if total_files else 0
            self._record_outcome(outcome, total_bytes=total_bytes, total_files=total_files)

            # Group total size by deployedImplementationType label
            size_by_type: dict[str, int] = defaultdict(int)
            count_by_type: dict[str, int] = defaultdict(int)
            large_files: list[dict] = []

            for r in rows:
                p = Path(r["file_path"])
                ext = p.suffix.lstrip(".").lower() if p.suffix else ""
                meta = cache.lookup(p.name, ext or None)
                label = (
                    meta.get("deployedImplementationType")
                    or meta.get("fileType")
                    or "Other"
                )
                size_by_type[label] += r["file_size_bytes"]
                count_by_type[label] += 1

                if r["file_size_bytes"] >= _LARGE_FILE_THRESHOLD_MB * 1_048_576:
                    large_files.append(r)

            # Sort size_by_type descending for readability
            size_by_type_sorted = dict(
                sorted(size_by_type.items(), key=lambda kv: -kv[1])
            )
            size_by_type_fmt = {k: _fmt_size(v) for k, v in size_by_type_sorted.items()}

            results.append(
                ResourceMeasureAnnotation(
                    check_name="disk_footprint",
                    summary=(
                        f"Repository disk footprint: {_fmt_size(total_bytes)} "
                        f"across {total_files:,} files (avg {_fmt_size(avg_bytes)}/file)"
                    ),
                    analysis_step=STEP,
                    confidence=95,
                    resource_properties={
                        "total_size_bytes": total_bytes,
                        "total_size_human": _fmt_size(total_bytes),
                        "total_files": total_files,
                        "avg_size_bytes": avg_bytes,
                        "size_by_type": size_by_type_fmt,
                        "count_by_type": dict(
                            sorted(count_by_type.items(), key=lambda kv: -kv[1])
                        ),
                    },
                    json_properties={"source": "project_file_inventory"},
                )
            )

            # Top-10 largest files
            top10 = sorted(rows, key=lambda r: r["file_size_bytes"], reverse=True)[:10]
            if top10:
                results.append(
                    ResourceMeasureAnnotation(
                        check_name="largest_file",
                        summary=f"Largest file: {_fmt_size(top10[0]['file_size_bytes'])} ({top10[0]['file_path']})",
                        analysis_step=STEP,
                        confidence=100,
                        resource_properties={
                            "top_largest_files": {
                                r["file_path"]: _fmt_size(r["file_size_bytes"])
                                for r in top10
                            }
                        },
                        json_properties={"threshold_shown": 10},
                    )
                )

            # Flag large files
            for r in large_files:
                mb = r["file_size_bytes"] / 1_048_576
                severity = "very large" if mb >= _VERY_LARGE_FILE_THRESHOLD_MB else "large"
                results.append(
                    RequestForActionAnnotation(
                        check_name="oversized_file",
                        item_key=r["file_path"],
                        summary=f"{severity.title()} file: {r['file_path']} ({_fmt_size(r['file_size_bytes'])})",
                        analysis_step=STEP,
                        confidence=100,
                        explanation=(
                            f"File exceeds {_LARGE_FILE_THRESHOLD_MB} MB. "
                            "Consider using Git LFS, storing in object storage, "
                            "or referencing a versioned data release instead of committing directly."
                        ),
                        action_requested="Review large file — consider Git LFS or external storage",
                        action_target_name=r["file_path"],
                    )
                )

        except Exception as exc:
            log.exception("FileSizeSurveyor failed for %s", self.project.slug)
            self._warn(results, str(exc))

        return results
