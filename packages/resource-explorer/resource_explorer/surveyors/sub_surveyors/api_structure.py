"""Sub-surveyor: API / Module Structure → SchemaAnalysisAnnotation."""
from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime

from resource_explorer.registry import Project, ProjectRegistry
from resource_explorer.step_outcome import UNVERIFIED, StepOutcome, from_upstream_table
from resource_explorer.surveyors.base_surveyor import BaseSurveyor
from resource_explorer.surveyors.scoping import sql_scope_filter
from resource_explorer.surveyors.survey_report import (
    Annotation,
    ResourceMeasureAnnotation,
    SchemaAnalysisAnnotation,
)

log = logging.getLogger(__name__)

STEP = "ApiStructureAnalysis"

# Symbol kinds treated as public API surface
#: Languages whose symbol extractor actually computes cyclomatic complexity.
#:
#: go_symbol_extractor.py and js_symbol_extractor.py contain NO `complexity=`
#: assignment at all, so every row they write defaults to 0. Measured over the
#: live symbol table 2026-09-01, functions and methods only:
#:
#:     java        179,668  min 1  max 348  avg 1.59  55 distinct values
#:     python       97,833  min 1  max 186  avg 2.64  84 distinct values
#:     go           62,403  min 0  max   0  avg 0.00   1 distinct value
#:     javascript   35,764  min 0  max   0  avg 0.00   1 distinct value
#:
#: A stored 0 here means "never measured", not "trivially simple", and the two
#: are indistinguishable once averaged: an aggregate across all four languages
#: is dragged toward zero by 98,167 symbols nobody computed. Same gap, same two
#: languages, as _DOCSTRING_CAPABLE_LANGUAGES in documentation.py — and it was
#: nearly missed here because `COUNT(*) WHERE complexity IS NOT NULL` counts a
#: zero as populated.
_COMPLEXITY_CAPABLE_LANGUAGES = frozenset({"python", "java"})


_PUBLIC_KINDS = {"function", "class", "method"}


class ApiStructureSurveyor(BaseSurveyor):
    """
    Reads project_code_symbols (and, since AST-ownership-transfer plan
    Phase 4, project_code_relationships) and produces:
      - One SchemaAnalysisAnnotation per language summarising the module tree,
        counts of public symbols, complexity, and inheritance depth.
      - One ResourceMeasureAnnotation with total symbol/relationship counts.

    This surveyor doesn't itself extract symbols — extraction happens first (the generated
    definitions place repo_symbol_extraction ahead of this step),
    inline during ingestion (IngestionPipeline._ingest_code(), which already
    re-runs on every full add and incremental refresh — see
    ingestion/code_symbol_extractor.py / java_symbol_extractor.py). Since this
    surveyor is already in SurveyOrchestrator.run()'s fixed sub-surveyor list,
    it runs — and its annotations reflect current data — on every repo survey
    without any separate opt-in scheduling (migration plan decision D7).
    """

    def __init__(
        self, project: Project, registry: ProjectRegistry, surveyed_at: str | None = None,
        scope_locator: str = "",
    ) -> None:
        super().__init__(project, registry)
        # See SecurityHygieneSurveyor's identical constructor comment — shared
        # run-timestamp from SurveyOrchestrator (Phase B, D1).
        self._surveyed_at = surveyed_at or datetime.utcnow().isoformat()
        # Repo scope-narrowing funnel plan (docs/repo-scope-narrowing-funnel.md),
        # D5/D6 — "" (default) = whole-repo, unchanged from every existing
        # caller; a non-empty locator narrows to one cataloged sub-resource
        # (this kind is target_shape="corpus" — a path-prefix filter).
        self._scope_locator = scope_locator

    @property
    def step_name(self) -> str:
        return STEP

    def _record(self, outcome: StepOutcome, *, symbol_count: int,
                relationship_count: int, by_language: dict,
                complexity_by_language: dict | None = None,
                complexity_languages_not_measured: list | None = None) -> None:
        """Generic project_analysis_metrics row — symbol/relationship counts as
        the two trendable metrics, by_language as this run's detail blob.

        Written on every terminal path, including the zero. A trend that simply
        has no point for a run cannot be read: the gap means "no symbols",
        "step not selected" and "extraction never ran" all at once.

        `complexity_by_language` carries the per-language summary this surveyor
        already computed and previously dropped (D3). It is deliberately NOT
        reduced to one number: see _COMPLEXITY_CAPABLE_LANGUAGES for why an
        aggregate across all four languages is meaningless, and
        `complexity_languages_not_measured` for the ones left out, which are
        named rather than silently absent.
        """
        try:
            self.registry.upsert_metric(
                self.project.slug, "api_structure",
                {"symbol_count": symbol_count, "relationship_count": relationship_count},
                detail={"by_language": by_language,
                        "complexity_by_language": complexity_by_language or {},
                        "complexity_languages_not_measured":
                            complexity_languages_not_measured or [],
                        **outcome.as_row()},
                surveyed_at=self._surveyed_at,
                scope_locator=self._scope_locator,
            )
        except Exception as exc:
            log.warning("Could not persist API structure snapshot for %s: %s",
                        self.project.slug, exc)

    def _nothing_analysed(self, outcome: StepOutcome, unscoped: int) -> Annotation:
        """Say which kind of nothing this was, rather than returning none."""
        if outcome.outcome == UNVERIFIED:
            summary = "API structure not analysed — no code symbols extracted"
            explanation = (
                "project_code_symbols holds no rows for this repo. Symbols are "
                "written by repo_symbol_extraction (and by full RAG ingestion); "
                "until one of those has run this is not evidence that the "
                "repository has no public API."
            )
        else:
            summary = f"No symbols matched the scope '{self._scope_locator}'"
            explanation = (
                f"The repo has {unscoped:,} extracted symbol(s); none of them fall "
                "under this scope locator."
            )
        return ResourceMeasureAnnotation(
            check_name="api_structure_summary",
            summary=summary, analysis_step=STEP, confidence=100,
            explanation=explanation,
            resource_properties={"symbol_counts_by_kind": {}, "relationship_count": 0},
            json_properties={"source": "project_code_symbols", **outcome.as_row()},
        )

    def run(self) -> list[Annotation]:
        results: list[Annotation] = []
        try:
            slug = self.project.slug

            scope_sql, scope_params = sql_scope_filter(self._scope_locator)
            with self.registry._conn() as conn:
                rows = conn.execute(
                    "SELECT language, kind, name, qualified_name, file_path, complexity "
                    "FROM project_code_symbols WHERE project_slug = ?" + scope_sql +
                    " ORDER BY language, kind, name",
                    (slug, *scope_params),
                ).fetchall()

            if not rows:
                # An empty project_code_symbols is overwhelmingly "extraction
                # never ran", not "this repo has no code": measured 2026-08-22,
                # 13 of the 20 registered repos had a populated file inventory
                # and zero symbols — docling with 1,653 files and none, trellis
                # with 1,078. Returning nothing left the Analysis card blank,
                # which is the one output indistinguishable from never having
                # run the step at all.
                with self.registry._conn() as conn:
                    unscoped = conn.execute(
                        "SELECT COUNT(*) AS n FROM project_code_symbols "
                        "WHERE project_slug = ?", (slug,),
                    ).fetchone()["n"]
                outcome = from_upstream_table(
                    unscoped, 0,
                    empty_table_cause="empty_code_symbols",
                    no_match_cause="no_symbols_in_scope",
                    scope_locator=self._scope_locator,
                )
                results.append(self._nothing_analysed(outcome, unscoped))
                self._record(outcome, symbol_count=0, relationship_count=0, by_language={})
                return results

            # Inheritance/relationship edges are a cross-cutting graph, not
            # cleanly per-file — left whole-repo-scoped even for a scoped
            # run rather than guessing a filtering rule; the symbol-level
            # data above (this method's primary output) is correctly scoped.
            relationships = self.registry.get_code_relationships(slug)

            by_lang: dict[str, list] = defaultdict(list)
            kind_counts: dict[str, int] = defaultdict(int)
            for r in rows:
                by_lang[r["language"]].append(r)
                kind_counts[r["kind"]] += 1

            complexity_by_language: dict[str, dict] = {}
            for language, symbols in sorted(by_lang.items()):
                public = [s for s in symbols if s["kind"] in _PUBLIC_KINDS]
                module_files = list({s["file_path"] for s in symbols})
                top_names = [s["name"] for s in public if s["kind"] == "class"][:10]
                top_names += [s["name"] for s in public if s["kind"] == "function"][:10]

                complexities = [s["complexity"] or 0 for s in symbols if s["kind"] in ("function", "method")]
                complexity_summary = {
                    "max": max(complexities) if complexities else 0,
                    "avg": round(sum(complexities) / len(complexities), 1) if complexities else 0,
                }
                if language in _COMPLEXITY_CAPABLE_LANGUAGES and complexities:
                    complexity_by_language[language] = {
                        **complexity_summary, "measured_over": len(complexities),
                    }
                lang_class_names = {s["qualified_name"] for s in symbols if s["kind"] == "class"}
                lang_relationship_count = sum(
                    1 for r in relationships if r["source_name"] in lang_class_names
                )

                results.append(
                    SchemaAnalysisAnnotation(
                        check_name="api_surface_by_language",
                        item_key=language,
                        summary=(
                            f"{language}: {len(public)} public symbol(s) across "
                            f"{len(module_files)} file(s)"
                        ),
                        analysis_step=STEP,
                        schema_name=f"{slug}:{language}",
                        schema_type=language,
                        confidence=90,
                        json_properties={
                            "file_count": len(module_files),
                            "symbol_counts": {
                                k: sum(1 for s in symbols if s["kind"] == k)
                                for k in _PUBLIC_KINDS
                            },
                            "top_symbols": top_names[:15],
                            "complexity": complexity_summary,
                            "inheritance_edges": lang_relationship_count,
                        },
                    )
                )

            results.append(
                ResourceMeasureAnnotation(
                    check_name="indexed_symbol_total",
                    summary=(
                        f"Total indexed symbols: {len(rows)} across {len(by_lang)} language(s), "
                        f"{len(relationships)} inheritance relationship(s)"
                    ),
                    analysis_step=STEP,
                    resource_properties={
                        "symbol_counts_by_kind": dict(kind_counts),
                        "relationship_count": len(relationships),
                    },
                )
            )

            self._record(
                from_upstream_table(len(rows), len(rows),
                                    empty_table_cause="empty_code_symbols",
                                    no_match_cause="no_symbols_in_scope"),
                symbol_count=len(rows), relationship_count=len(relationships),
                by_language={lang: len(syms) for lang, syms in by_lang.items()},
                # D3 (docs/code-volume-and-doc-coverage-design.md): the
                # complexity summary was computed per language above and then
                # dropped on the floor — it reached the SchemaAnalysisAnnotation
                # and never the metric, so the question layer could not read it.
                #
                # Per language, never aggregated across them: go and javascript
                # store 0 for every symbol because their extractors never
                # compute it, and a mean over all four would be dragged toward
                # zero by 98,167 symbols nobody measured. The excluded
                # languages are named rather than omitted.
                complexity_by_language=complexity_by_language,
                complexity_languages_not_measured=sorted(
                    set(by_lang) - set(complexity_by_language)
                ),
            )

        except Exception as exc:
            log.exception("ApiStructureSurveyor failed for %s", self.project.slug)
            self._warn(results, str(exc))

        return results
