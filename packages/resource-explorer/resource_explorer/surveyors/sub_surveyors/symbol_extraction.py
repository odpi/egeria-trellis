"""Sub-surveyor: Symbol Extraction → ResourceMeasureAnnotation.

D5 (docs/unified-survey-execution-model-plan.md) — the one real, previously-
named bug this microflow structurally eliminates: project_code_symbols was
populated only by RAG ingestion's inline _ingest_code() step (full add, or
incremental refresh of a project that already has code collections
ingested) — never by anything in SurveyOrchestrator's own step list. A repo
registered without RAG ingestion (e.g. the org-import/discovery path, which
deliberately skips ingestion — see org_importer.py) or one whose language
was never selected as a collection at ingest time could never get symbols
any other way, leaving ApiStructureSurveyor (repo_api_structure) and any
future consumer permanently empty with no self-healing path.

A self-contained microflow per D5's model: acquire the shared zipball_root
resource (D6), extract + upsert project_code_symbols (write), then report
what it just wrote (read) — same "every read needs a save" shape as every
other D5 microflow, just with extraction itself as the write instead of a
pre-existing pipeline step. Deliberately kept as its own StepInfo rather
than folded into ApiStructureSurveyor's reporting (the D5 doc's flagged,
unresolved choice) — see repo_survey_definition_adapter.py's ANALYSIS_KINDS
"api_structure" entry, which bundles this step ahead of repo_api_structure
so requesting API-structure analysis is now self-refreshing by construction,
while this step also stays independently runnable/schedulable on its own.
"""
from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

from resource_explorer.configdata.collection_config import COLLECTION_TYPES
from resource_explorer.registry import Project, ProjectRegistry
from resource_explorer.surveyors.base_surveyor import BaseSurveyor
from resource_explorer.step_outcome import StepOutcome, no_signal
from resource_explorer.surveyors.survey_report import Annotation, ResourceMeasureAnnotation
from resource_explorer.ingestion.vendored import is_vendored_abs

log = logging.getLogger(__name__)

STEP = "SymbolExtraction"

# Same ctype -> language mapping as IngestionPipeline._CTYPE_LANGUAGE, kept
# in sync manually (small, stable list) rather than importing a private
# pipeline attribute across module boundaries.
_LANGUAGE_CTYPES: dict[str, str] = {
    "python_code": "python",
    "javascript_code": "javascript",
    "java_code": "java",
    "go_code": "go",
}


def _local_files(local_root: Path, extensions: list[str]) -> list[tuple[str, str]]:
    """Walk local_root and return (relative_path, content) for matching
    files — same logic as IngestionPipeline._local_files, duplicated rather
    than imported since that method is instance-bound to IngestionPipeline's
    console/store and this surveyor has neither."""
    results = []
    for p in local_root.rglob("*"):
        if not p.is_file() or p.suffix.lower() not in extensions:
            continue
        # The duplicate of IngestionPipeline._local_files that PR #36 missed:
        # this walk put 20,654 vendored symbols back one survey after the
        # profile refresh had removed them. Same rule, same reason.
        if is_vendored_abs(p, local_root):
            continue
        try:
            content = p.read_text(encoding="utf-8", errors="ignore")
            results.append((str(p.relative_to(local_root)), content))
        except Exception:
            pass
    return results


class SymbolExtractionSurveyor(BaseSurveyor):
    """
    Extracts class/function/method symbols (tree-sitter/ast, via
    CodeSymbolExtractor) for every supported language found under
    `local_path`, clearing and re-upserting project_code_symbols (and, via
    upsert_code_symbols's own inherits_from derivation,
    project_code_relationships) per language — then reports what it wrote.

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
        self._surveyed_at = surveyed_at or datetime.utcnow().isoformat()

    @property
    def step_name(self) -> str:
        return STEP

    def run(self) -> list[Annotation]:
        results: list[Annotation] = []
        try:
            from resource_explorer.ingestion.code_symbol_extractor import (
                MARKER_CAPABLE_LANGUAGES,
                CodeSymbolExtractor,
            )

            slug = self.project.slug
            local_root = Path(self._local_path)
            extractor = CodeSymbolExtractor()
            counts_by_language: dict[str, int] = {}
            marker_counts_by_language: dict[str, int] = {}

            # How many candidate source files were actually READ, not just how
            # many symbols came out. Without this the step cannot tell "this
            # code declares no symbols" from "there was no code here" — and the
            # second is a real, observed failure: step_outcome.py's own
            # docstring records a run that read from a --no-checkout clone
            # whose root holds only `.git`, scanned an empty tree, and reported
            # its zero as though it were a finding.
            files_scanned = 0

            for ctype_name, language in _LANGUAGE_CTYPES.items():
                ctype = COLLECTION_TYPES.get(ctype_name)
                if ctype is None:
                    continue
                # Clear-then-conditionally-upsert (matches refresh_profile's
                # existing pattern) — a language with zero matching files
                # this run correctly ends up with zero symbols, not stale
                # ones from a file that was since deleted/renamed.
                self.registry.clear_code_symbols(slug, language)
                self.registry.clear_code_markers(slug, language)
                symbols = []
                markers = []
                marker_capable = language in MARKER_CAPABLE_LANGUAGES
                for path, content in _local_files(local_root, ctype.file_extensions):
                    files_scanned += 1
                    symbols.extend(extractor.extract(path, content, slug, language))
                    if marker_capable:
                        markers.extend(extractor.extract_markers(path, content, slug, language))
                if symbols:
                    self.registry.upsert_code_symbols(slug, symbols)
                    counts_by_language[language] = len(symbols)
                if markers:
                    self.registry.upsert_code_markers(slug, markers)
                    marker_counts_by_language[language] = len(markers)

            total = sum(counts_by_language.values())
            relationships = self.registry.get_code_relationships(slug)
            # Files read are the known-positive: having parsed real source
            # files and found no symbols is a fact about this code. Having
            # parsed none is a fact about the checkout, and claiming the first
            # when only the second is true is what makes a broken extraction
            # read as a repo with nothing in it.
            if total:
                outcome = StepOutcome("recovered", detail={"files_scanned": files_scanned})
            else:
                outcome = no_signal(
                    "no symbols extracted from the files scanned",
                    known_positive=files_scanned > 0,
                    files_scanned=files_scanned,
                )
            results.append(
                ResourceMeasureAnnotation(
                    summary=(
                        f"Extracted {total} symbol(s) across {len(counts_by_language)} "
                        f"language(s), {len(relationships)} inheritance relationship(s)"
                        + ("" if files_scanned else
                           " — no source files were read, so this zero is about the "
                           "checkout, not the code")
                    ),
                    analysis_step=STEP,
                    check_name="symbol_extraction",
                    confidence=100 if files_scanned else 0,
                    resource_properties={
                        "symbol_counts_by_language": counts_by_language,
                        "relationship_count": len(relationships),
                        "files_scanned": files_scanned,
                        "marker_counts_by_language": marker_counts_by_language,
                    },
                    json_properties=outcome.as_row(),
                )
            )

            try:
                # Generic project_analysis_metrics table (analysis-kind
                # extensibility redesign) — same pattern ApiStructureSurveyor
                # uses for its own "api_structure" kind, kept as a separate
                # "symbol_extraction" kind so this step's own run history
                # (independently schedulable, see ANALYSIS_KINDS) is
                # trendable on its own.
                #
                # `markers_captured: True` is the backfill flag DESIGN-
                # INTERFACE-SURFACE-IMPLEMENTED-RUNG.md's Decisions §2 calls
                # for: every repo surveyed before project_code_markers
                # existed has zero marker rows despite extraction having
                # genuinely run, and interface_surface must not read that as
                # a measured zero. A run under this code always writes this
                # flag (even when marker_counts_by_language is empty because
                # no marker-capable language had matching files), so its
                # ABSENCE on the latest symbol_extraction metric row — not
                # the marker table being empty — is what means "predates
                # marker capture, or never ran". `by_language` (the file
                # inventory's own count) travels alongside so interface_
                # surface can also tell "no python/java in this repo" apart
                # from "python ran and found nothing".
                self.registry.upsert_metric(
                    slug, "symbol_extraction",
                    {"symbol_count": total, "relationship_count": len(relationships)},
                    detail={"by_language": counts_by_language,
                            "marker_counts_by_language": marker_counts_by_language,
                            "markers_captured": True},
                    surveyed_at=self._surveyed_at,
                )
            except Exception as exc:
                log.warning("Could not persist symbol extraction snapshot for %s: %s", slug, exc)

            try:
                # A committed OpenAPI/Swagger file is parsed from the SAME checkout (no second download), so the
                # API reference can say "from the committed spec" (api_reference/committed.py). Its own
                # try-block: a spec that will not parse must not take the symbols down with it.
                from resource_explorer.api_reference.committed import capture_committed_specs

                capture_committed_specs(self.registry, slug, local_root, surveyed_at=self._surveyed_at)
            except (ValueError, OSError) as exc:
                log.warning("Could not capture committed API specs for %s: %s", slug, exc)

        except Exception as exc:
            log.exception("SymbolExtractionSurveyor failed for %s", self.project.slug)
            self._warn(results, str(exc))

        return results
