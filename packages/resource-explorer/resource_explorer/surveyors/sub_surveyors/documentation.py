"""Sub-surveyor: Documentation Quality → ClassificationAnnotation."""
from __future__ import annotations

import logging

from resource_explorer.registry import Project, ProjectRegistry
from resource_explorer.step_outcome import UNVERIFIED, from_upstream_table
from resource_explorer.surveyors.base_surveyor import BaseSurveyor
from resource_explorer.surveyors.survey_report import (
    Annotation, ClassificationAnnotation, ResourceMeasureAnnotation)
from resource_explorer.survey_time import now_utc_iso

log = logging.getLogger(__name__)

STEP = "DocumentationAnalysis"

#: Languages whose symbol extractor actually captures documentation.
#:
#: go_symbol_extractor.py and js_symbol_extractor.py hardcode `docstring=""`
#: at every assignment site (3 and 4 respectively). Measured 2026-09-01 over
#: the whole symbol table: go 0 of 70,955, javascript 0 of 48,533 — against
#: java 60,437 of 203,521 and python 54,985 of 114,055.
#:
#: Go has doc comments and JavaScript has JSDoc. Both exist in the language;
#: neither is extracted. So a coverage figure for those languages would be a
#: fact about OUR extractor wearing the clothes of a fact about THEIR code —
#: and it would come out as 0%, the most alarming value available, not the
#: quietest. Refusing to report is the only honest option until the
#: extractors are taught to read them.
_DOCSTRING_CAPABLE_LANGUAGES = frozenset({"python", "java"})



# Collection types that indicate documentation was indexed
_DOC_COLLECTIONS = {
    "markdown_docs": "Markdown documentation",
    "web_docs": "Web / hosted documentation",
    "api_reference": "API reference (OpenAPI / docstrings)",
    "examples": "Code examples / notebooks",
    "pdfs": "PDF documentation",
    "release_notes": "Release notes / changelog",
}

# File names that indicate good project hygiene. Matched against
# project_file_inventory (every file in the repo — see _store_file_inventory's
# own docstring), not project_code_symbols (only .py/.js/.java/.go files ever
# get symbol rows, so a plain-text/no-symbol file like these can never
# appear there — this whole check was silently a no-op before Assessment
# expansion plan B3 found it while reconciling CODEOWNERS specifically;
# fixed for the whole dict, not just that one entry, since it's the same bug).
_HYGIENE_FILES = {
    "README.md": "README",
    "README.rst": "README",
    "CHANGELOG.md": "Changelog",
    "CHANGELOG.rst": "Changelog",
    "CHANGES.md": "Changelog",
    "CONTRIBUTING.md": "Contributing guide",
    "CONTRIBUTING.rst": "Contributing guide",
    "CODE_OF_CONDUCT.md": "Code of conduct",
    "AUTHORS": "Authors list",
    "AUTHORS.md": "Authors list",
}

# CODEOWNERS is handled separately (not via _HYGIENE_FILES' basename-anywhere
# matching) because GitHub only recognizes it at 3 specific locations —
# registry.file_exists() checks those exact paths via an indexed lookup
# against project_file_inventory's UNIQUE(project_slug, file_path).
_CODEOWNERS_PATHS = ("CODEOWNERS", ".github/CODEOWNERS", "docs/CODEOWNERS")


class DocumentationSurveyor(BaseSurveyor):
    """
    Inspects which documentation collections were indexed and which
    hygiene files appear in the file inventory.

    Produces ClassificationAnnotations describing:
      - Which doc collection types are present
      - Which hygiene files were found
      - An overall doc quality label (Comprehensive / Partial / Minimal)
    """

    def __init__(self, project: Project, registry: ProjectRegistry, surveyed_at: str | None = None) -> None:
        super().__init__(project, registry)
        # See SecurityHygieneSurveyor's identical constructor comment — shared
        # run-timestamp from SurveyOrchestrator (Phase B, D1).
        self._surveyed_at = surveyed_at or now_utc_iso()

    @property
    def step_name(self) -> str:
        return STEP

    def run(self) -> list[Annotation]:
        results: list[Annotation] = []
        findings: list[dict] = []
        try:
            # ── collection presence from registry ─────────────────────────────
            slug = self.project.slug
            project_collections = set(self.project.collections)
            present_doc_types: list[str] = []

            for col_type, label in _DOC_COLLECTIONS.items():
                col_name = f"{slug}_{col_type}"
                if col_name in project_collections:
                    present_doc_types.append(label)
                    results.append(
                        ClassificationAnnotation(
                            check_name="doc_collection",
                            item_key=col_type,
                            summary=f"Collection present: {label}",
                            analysis_step=STEP,
                            candidate_classifications=[col_type],
                            confidence=100,
                        )
                    )
                    findings.append({
                        "finding_type": "collection_present", "label": col_type,
                        "summary": f"Collection present: {label}",
                        "confidence": 100, "detail": {"display_label": label},
                    })

            # ── hygiene files from the file inventory ─────────────────────────
            # project_file_inventory (every file in the repo), not
            # project_code_symbols (only .py/.js/.java/.go files ever get
            # rows there — see _HYGIENE_FILES' comment).
            inventory = self.registry.get_file_inventory(slug)
            inventory_filenames = {
                p.replace("\\", "/").rsplit("/", 1)[-1]
                for p in inventory
            }
            found_hygiene: list[str] = []
            for fname, label in _HYGIENE_FILES.items():
                if fname in inventory_filenames and label not in found_hygiene:
                    found_hygiene.append(label)

            # CODEOWNERS folds into the same found_hygiene list/combined
            # annotation as the rest — same shape as the README/CHANGELOG/
            # CONTRIBUTING checks above, just sourced via the exact-path
            # lookup rather than inventory_filenames' basename-anywhere match.
            codeowners_path = self.registry.file_exists(slug, *_CODEOWNERS_PATHS)
            if codeowners_path:
                found_hygiene.append("Code owners")

            if found_hygiene:
                results.append(
                    ClassificationAnnotation(
                        check_name="hygiene_files",
                        summary=f"Hygiene files found: {', '.join(found_hygiene)}",
                        analysis_step=STEP,
                        candidate_classifications=found_hygiene,
                        confidence=95,
                    )
                )
                findings.append({
                    "finding_type": "hygiene_files", "label": ", ".join(found_hygiene),
                    "summary": f"Hygiene files found: {', '.join(found_hygiene)}",
                    "confidence": 95, "detail": {"files": found_hygiene},
                })

            # ── API documentation coverage ────────────────────────────────────
            # Dan, 2026-09-01, having written most of egeria-python:
            # "the documentation I put in was where I thought it was most
            # useful... quantity is not the same as usefulness."
            #
            # So this is a ResourceMeasureAnnotation and NOT a
            # ClassificationAnnotation, it does NOT feed `score` below, and it
            # raises no RequestForAction. It states how much of the public
            # surface carries a docstring. It does not claim that number
            # should be higher — nobody here knows which symbol deserved a
            # docstring, and the person best placed to know has said the
            # distribution was deliberate.
            #
            # See docs/code-volume-and-doc-coverage-design.md D2a.
            per_language = self._docstring_coverage(slug)
            measurable = {
                lang: c for lang, c in per_language.items()
                if lang in _DOCSTRING_CAPABLE_LANGUAGES
            }
            unmeasurable = sorted(set(per_language) - set(measurable))
            if measurable:
                pub = sum(c["public"] for c in measurable.values())
                documented = sum(c["public_documented"] for c in measurable.values())
                pct = (documented / pub * 100) if pub else 0.0
                langs = ", ".join(sorted(measurable))
                note = ""
                if unmeasurable:
                    # Named, not silently omitted: a percentage over 2 of 4
                    # languages must not read like one over all 4.
                    note = (f" Not counted for {', '.join(unmeasurable)} — "
                            f"their extractors do not capture documentation, "
                            f"so absence there says nothing about the code.")
                results.append(
                    ResourceMeasureAnnotation(
                        check_name="api_docstring_coverage",
                        summary=(f"{documented:,} of {pub:,} public symbols carry a "
                                 f"docstring ({pct:.1f}%), measured over {langs}."
                                 f"{note}"),
                        analysis_step=STEP,
                        resource_properties={
                            "public_symbols": pub,
                            "public_documented": documented,
                            "coverage_pct": round(pct, 1),
                            "by_language": measurable,
                            "languages_not_measured": unmeasurable,
                        },
                        json_properties={
                            "source": "project_code_symbols",
                            # The reader three months from now should not have
                            # to find the design doc to learn this.
                            "interpretation": (
                                "Presence, not usefulness. A lower figure may reflect "
                                "a deliberate choice about where documentation is worth "
                                "writing, not a defect. This measure does not contribute "
                                "to the documentation quality label."
                            ),
                        },
                    )
                )
                findings.append({
                    "finding_type": "api_docstring_coverage",
                    "label": f"{pct:.1f}%",
                    "summary": (f"{documented:,} of {pub:,} public symbols carry a "
                                f"docstring ({pct:.1f}%), measured over {langs}."),
                    "confidence": 100,
                    "detail": {
                        "public_symbols": pub, "public_documented": documented,
                        "coverage_pct": round(pct, 1), "by_language": measurable,
                        "languages_not_measured": unmeasurable,
                        "is_quality_verdict": False,
                    },
                })
            elif per_language:
                # Symbols exist, but in no language whose docs we can read.
                # This is the Go/JS case and it must never render as 0%.
                results.append(
                    ResourceMeasureAnnotation(
                        check_name="api_docstring_coverage",
                        summary=(f"API docstring coverage not established for "
                                 f"{', '.join(sorted(per_language))} — this survey's "
                                 f"symbol extractors do not capture documentation for "
                                 f"those languages."),
                        analysis_step=STEP,
                        resource_properties={"languages_not_measured": sorted(per_language)},
                        json_properties={
                            "source": "project_code_symbols",
                            "interpretation": (
                                "A gap in our extraction, not in their code. Go doc "
                                "comments and JSDoc both exist and are not read."
                            ),
                        },
                    )
                )

            # ── overall quality label ─────────────────────────────────────────
            # Half of `score` comes from the file inventory. With an empty
            # inventory the hygiene half is not zero, it is unmeasured — and the
            # label that came out was "Minimal", a confident verdict about a
            # repository nobody had looked at. That is the exact failure this
            # vocabulary exists to name, on the one step here whose output is a
            # judgement rather than a count.
            outcome = from_upstream_table(
                len(inventory), len(found_hygiene),
                empty_table_cause="empty_file_inventory",
                no_match_cause="no_hygiene_files",
                doc_collections=len(present_doc_types),
            )
            # DECLARED (design §5): GovernanceMetric::ResourceExplorer::
            # DocumentationSignalCount::1.0, whose declaration states plainly
            # that this counts KINDS of documentation and not quality — one
            # excellent guide scores below five stubs. That limitation belongs
            # in the declaration rather than in a reader's assumption.
            score = len(present_doc_types) + len(found_hygiene)
            if score >= 5:
                quality = "Comprehensive"
            elif score >= 2:
                quality = "Partial"
            else:
                quality = "Minimal"

            unverified = outcome.outcome == UNVERIFIED
            if unverified:
                summary = (
                    f"Documentation quality not established — the file inventory is "
                    f"empty, so only {len(present_doc_types)} collection signal(s) "
                    f"could be counted"
                )
            else:
                summary = f"Documentation quality: {quality} ({score} signal(s) detected)"

            results.append(
                ClassificationAnnotation(
                    check_name="documentation_quality",
                    summary=summary,
                    analysis_step=STEP,
                    candidate_classifications=[] if unverified else [quality],
                    confidence=70,
                    explanation=(
                        "Hygiene files (README, CHANGELOG, CONTRIBUTING, SECURITY, "
                        "CODEOWNERS) are read from project_file_inventory, which holds "
                        "no rows for this repo. Run repo_file_inventory (or a Profile "
                        "refresh) and re-run before treating this as a quality finding."
                        if unverified else ""
                    ),
                    json_properties={
                        "doc_collection_types": present_doc_types,
                        "hygiene_files": found_hygiene,
                        "signal_count": score,
                        **outcome.as_row(),
                    },
                )
            )
            findings.append({
                # The label the row carries must match what the annotation says.
                # Persisting "Minimal" while the annotation says "not established"
                # would put the wrong answer in the trend, which is the surface
                # that outlives the run.
                "finding_type": "quality_score",
                "label": "Unverified" if unverified else quality,
                "summary": summary,
                "confidence": 70,
                "detail": {
                    "doc_collection_types": present_doc_types,
                    "hygiene_files": found_hygiene,
                    "signal_count": score,
                    **outcome.as_row(),
                },
            })

            try:
                # Generic project_analysis_findings table (analysis-kind
                # extensibility redesign) — translate this surveyor's own
                # "finding_type"/"label" vocabulary to the generic
                # check_name/label field names at the call boundary.
                self.registry.upsert_finding(
                    slug, "documentation",
                    [
                        {
                            "check_name": f["finding_type"], "label": f["label"],
                            "summary": f.get("summary", ""),
                            "confidence": f.get("confidence", 100), "detail": f.get("detail"),
                        }
                        for f in findings
                    ],
                    surveyed_at=self._surveyed_at,
                )
            except Exception as exc:
                log.warning("Could not persist documentation findings for %s: %s", slug, exc)

        except Exception as exc:
            log.exception("DocumentationSurveyor failed for %s", self.project.slug)
            self._warn(results, str(exc))

        return results

    def _docstring_coverage(self, slug: str) -> dict:
        """Public-symbol docstring counts per language, from data already
        stored at ingestion. No new extraction.

        Public only: `is_private` is a 0/1 integer column, and COALESCE keeps
        a NULL (never classified) counted as public rather than dropped —
        undercounting the denominator would inflate the percentage, which is
        the direction that flatters."""
        with self.registry._conn() as conn:
            rows = conn.execute(
                "SELECT language, "
                "COUNT(*) AS total, "
                "SUM(CASE WHEN COALESCE(is_private, 0) = 0 THEN 1 ELSE 0 END) AS public, "
                "SUM(CASE WHEN COALESCE(is_private, 0) = 0 "
                "         AND docstring IS NOT NULL AND docstring <> '' "
                "    THEN 1 ELSE 0 END) AS public_documented "
                "FROM project_code_symbols WHERE project_slug = ? "
                "GROUP BY language",
                (slug,),
            ).fetchall()
        return {
            (r["language"] or "unknown"): {
                "total": int(r["total"] or 0),
                "public": int(r["public"] or 0),
                "public_documented": int(r["public_documented"] or 0),
            }
            for r in rows if (r["total"] or 0) > 0
        }
