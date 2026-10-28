"""Bring annotations produced BY Egeria into Resource Explorer's own tables.

The direction every other piece of this codebase runs is RE -> Egeria: a
surveyor produces annotations locally and EgeriaPublisher writes them out. A
Survey Definition whose steps execute natively in Egeria inverts that — the
annotations are created in Egeria and RE never sees them, so its Results views,
trend charts and question-answering have nothing to read.

This closes that direction by materializing them into `project_analysis_findings`
(and `project_analysis_metrics` for numeric ones), which is what every existing
Results renderer already reads. Nothing downstream needs to know a finding came
from Egeria rather than from a local surveyor — except that it is recorded, on
every row, because a number whose provenance is unclear is the thing this
codebase keeps having to un-confuse.

Two deliberate constraints:

* Annotations are fetched **by relationship** — `get_new_annotations(report_guid)`,
  which walks the report's own annotation links — never by qualifiedName prefix.
  EgeriaReader's existing `get_annotations()` searches `Annotation::{slug}::{ts}::`,
  which is RE's OWN naming convention; a natively-produced annotation does not
  carry it and would silently not be found.

  Measured against a real published report (b5ccdfd1, egeria_git):
  `get_annotations_for_element` returns "No elements found" for both the report
  GUID and the asset GUID, while `get_new_annotations` returns the annotation.
  The first was the obvious-sounding call and is the wrong one.

* Materializing is **append-only and idempotent per report**. A report already
  materialized is skipped rather than re-inserted, because these tables are
  append-only history: running twice would double every count read off them.

* Reports **Resource Explorer itself published** are skipped. Their annotations
  came from a local run whose findings are already in these tables, so
  importing them writes a second copy of the same run — and because the copy
  carries the REPORT's timestamp while the original carries the run's, the two
  land milliseconds apart under the same kind, and `query_metrics` "latest run"
  then picks between them by accident. Caught on real data: materializing
  egeria_git's only report produced 12 metric rows at ...442655 shadowed by the
  local run's 5 at ...448468.

Status: no Survey Definition currently has a natively-executing step (measured
2026-08-26 — all 8 definitions, 71 steps, executes_at=resource-explorer), so
this path is exercised by tests and not yet by live data. It is written to be
correct when native steps are authored, and says so rather than implying it has
been proven end to end.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from resource_explorer.registry import ProjectRegistry

log = logging.getLogger(__name__)

#: Where a materialized finding's `kind` comes from when the annotation cannot
#: be attributed to a known analysis. Deliberately its own value rather than
#: being folded into a neighbouring analysis's kind: "Egeria produced this and
#: we could not say which analysis it belongs to" is a different fact from
#: "this is a security_scan finding", and merging them would make the second
#: unreliable.
UNATTRIBUTED_KIND = "egeria_native"

#: Annotation types whose payload is a measurement rather than a judgement.
#: These go to project_analysis_metrics; everything else is a finding.
_MEASURE_TYPES = {
    "ResourceMeasureAnnotation",
    "ResourceProfileAnnotation",
    "QualityScoreAnnotation",
}


from resource_explorer.surveyors.survey_report import step_of  # noqa: E402


class EgeriaAnnotationMaterializer:
    """Reads a SurveyReport's annotations out of Egeria and stores them locally."""

    def __init__(self, registry: "ProjectRegistry | None" = None, reader=None) -> None:
        from resource_explorer.registry import ProjectRegistry

        self._registry = registry or ProjectRegistry()
        self._reader = reader

    # ── the reader is injected so tests never need a live Egeria ────────────
    def _get_reader(self):
        if self._reader is None:
            from resource_explorer.surveyors.egeria_reader import EgeriaReader

            self._reader = EgeriaReader(registry=self._registry)
        return self._reader

    def materialize_report(self, slug: str, report_guid: str,
                           surveyed_at: str = "") -> dict:
        """Materialize one SurveyReport's annotations for one project.

        Returns a summary dict: how many annotations were seen, how many became
        findings vs metrics, and which kinds were written. `skipped` is set when
        this report has already been materialized.
        """
        already = self._registry.get_setting(_report_marker(slug, report_guid))
        if already:
            return {
                "slug": slug, "report_guid": report_guid, "skipped": True,
                "reason": "already materialized",
                "materialized_at": already, "annotations": 0,
                "findings": 0, "metrics": 0, "kinds": [],
            }

        annotations, fetch_error = self._fetch_annotations(report_guid)
        surveyed_at = surveyed_at or _utcnow()
        if fetch_error:
            # NOT marked materialized: a read that failed must stay retryable.
            # Marking it would make an unreachable Egeria permanently
            # indistinguishable from a report that genuinely had nothing.
            return {
                "slug": slug, "report_guid": report_guid, "skipped": False,
                "error": fetch_error, "annotations": 0, "findings": 0,
                "metrics": 0, "kinds": [],
            }

        findings_by_kind: dict[str, list[dict]] = {}
        metrics_by_kind: dict[str, dict[str, float]] = {}
        detail_by_kind: dict[str, dict] = {}

        for ann in annotations:
            kind = self._kind_for(ann)
            if (ann.get("type_name") in _MEASURE_TYPES
                    or ann.get("annotation_type") in _MEASURE_TYPES):
                values = _numeric_values(ann)
                if values:
                    metrics_by_kind.setdefault(kind, {}).update(values)
                    detail_by_kind.setdefault(kind, _detail_for(ann))
                    continue
                # A measure annotation carrying nothing numeric is still a real
                # observation. Recording it as a finding keeps it rather than
                # dropping it for failing to be a number.
            findings_by_kind.setdefault(kind, []).append(_finding_for(ann))

        for kind, findings in findings_by_kind.items():
            self._registry.upsert_finding(slug, kind, findings, surveyed_at=surveyed_at)
        for kind, metrics in metrics_by_kind.items():
            self._registry.upsert_metric(
                slug, kind, metrics, detail=detail_by_kind.get(kind),
                surveyed_at=surveyed_at,
            )

        # Marked only after the writes land: a crash mid-way must leave the
        # report re-materializable, not silently marked done with partial rows.
        # A genuinely empty report IS marked -- it was read successfully and
        # holds nothing, which is a real answer, unlike the error case above.
        self._registry.set_setting(_report_marker(slug, report_guid), surveyed_at)

        return {
            "slug": slug, "report_guid": report_guid, "skipped": False,
            "annotations": len(annotations),
            "findings": sum(len(v) for v in findings_by_kind.values()),
            "metrics": sum(len(v) for v in metrics_by_kind.values()),
            "kinds": sorted(set(findings_by_kind) | set(metrics_by_kind)),
            "surveyed_at": surveyed_at,
        }

    def materialize_project(self, slug: str, limit: int = 25,
                            include_own: bool = False) -> dict:
        """Materialize every not-yet-materialized report for this project.

        Reports RE published itself are skipped unless include_own is set —
        see this module's docstring for the duplicate-run problem that causes.
        """
        reader = self._get_reader()
        reports = reader.get_survey_reports_from_egeria(slug) or []
        results = []
        own = 0
        for r in reports[:limit]:
            if not r.get("guid"):
                continue
            if not include_own and _is_own_report(r.get("qualified_name", ""), slug):
                own += 1
                results.append({
                    "slug": slug, "report_guid": r["guid"], "skipped": True,
                    "reason": "published by Resource Explorer — its findings are "
                              "already local",
                    "annotations": 0, "findings": 0, "metrics": 0, "kinds": [],
                })
                continue
            results.append(self.materialize_report(
                slug, r["guid"], surveyed_at=r.get("surveyed_at", ""),
            ))
        done = [r for r in results if not r.get("skipped") and not r.get("error")]
        failed = [r for r in results if r.get("error")]
        return {
            "slug": slug,
            "reports_seen": len(results),
            "reports_materialized": len(done),
            "annotations": sum(r.get("annotations", 0) for r in done),
            "findings": sum(r.get("findings", 0) for r in done),
            "metrics": sum(r.get("metrics", 0) for r in done),
            "kinds": sorted({k for r in done for k in r.get("kinds", [])}),
            # Surfaced, not folded into the counts: a sweep where every report
            # failed to read would otherwise report the same zeroes as one where
            # every report was genuinely empty.
            "reports_failed": len(failed),
            # Named rather than folded into reports_seen: "we found nothing new
            # in Egeria" and "everything there came from us" are different
            # answers, and only the second means the loop is working.
            "reports_skipped_own": own,
            "reports": results,
        }

    # ── internals ───────────────────────────────────────────────────────────
    def _fetch_annotations(self, report_guid: str) -> tuple[list[dict], str]:
        """By relationship, not by name — see this module's docstring.

        Returns (annotations, error). The error is returned rather than raised
        so the caller can tell "this report has no annotations" from "we could
        not read them" — which matters here more than usual, because the second
        must not be recorded as a completed materialization.
        """
        from resource_explorer.surveyors.egeria_reader import _parse_annotation

        reader = self._get_reader()
        reader.connect()
        try:
            results = reader._discovery.get_new_annotations(
                report_guid, page_size=500, output_format="JSON",
            )
        except Exception as exc:
            log.debug("get_new_annotations(%s) failed: %s", report_guid, exc)
            return [], str(exc)
        if not isinstance(results, list):
            # pyegeria returns a string ("No elements found") when empty.
            return [], ""
        return [
            _parse_annotation(el.get("elementHeader", {}) or {}, el.get("properties", {}) or {})
            for el in results
        ], ""

    def _kind_for(self, annotation: dict) -> str:
        """Which analysis this annotation belongs to, if any.

        Two passes, strongest evidence first:

        1. `analysisStep` equal to a re_analysis_step key. Exact, but rare in
           practice — a real published annotation carries "HealthAssessment",
           the surveyor's own step name, not "repo_health" (measured against
           annotation 2f1f804a on egeria_git). Kept as the first pass because a
           natively-executing step CAN be authored to set the key.

        2. `annotationType` whose producing step is UNAMBIGUOUS — exactly one
           step in STEP_REGISTRY declares it. QualityScoreAnnotation belongs
           only to repo_health, so the annotation above attributes correctly.
           Types with several producers (ResourceMeasureAnnotation has 15) are
           deliberately NOT used: picking one would file a finding under an
           analysis that did not produce it, which is worse than admitting we
           cannot tell.

        Anything else gets UNATTRIBUTED_KIND rather than a guess.
        """
        try:
            from resource_explorer.surveyors.repo_survey_definition_adapter import (
                REPO_ANALYSIS_STEP_MAP,
            )
        except ImportError:  # pragma: no cover - defensive
            return UNATTRIBUTED_KIND

        step = step_of(annotation).strip()   # producingStep first; analysisStep for older reports
        if step:
            for analysis_id, step_keys in REPO_ANALYSIS_STEP_MAP.items():
                if step in step_keys:
                    return analysis_id

        owner = _sole_producer_of(annotation.get("annotation_type") or "")
        if owner:
            for analysis_id, step_keys in REPO_ANALYSIS_STEP_MAP.items():
                if owner in step_keys:
                    return analysis_id
        return UNATTRIBUTED_KIND


def _is_own_report(qualified_name: str, slug: str) -> str:
    """Did Resource Explorer publish this report?

    EgeriaPublisher writes `SurveyReport::<TypePrefix>::<slug>::<timestamp>`.
    A report created by a natively-executing Egeria survey does not carry that
    shape, which is exactly what makes it worth importing.
    """
    if not qualified_name.startswith("SurveyReport::"):
        return ""
    return qualified_name if f"::{slug}::" in qualified_name else ""


def _sole_producer_of(annotation_type: str) -> str:
    """The one step key that declares this annotation type, or "".

    Returns "" when several steps declare it. An ambiguous type must not be
    resolved by picking a winner: filing a finding under an analysis that did
    not produce it makes that analysis's results wrong, while leaving it
    unattributed only makes them incomplete — and visibly so.
    """
    if not annotation_type:
        return ""
    try:
        from resource_explorer.surveyors.repo_survey_definition_adapter import STEP_REGISTRY
    except ImportError:  # pragma: no cover - defensive
        return ""
    owners = [
        key for key, spec in STEP_REGISTRY.items()
        if annotation_type in (getattr(spec, "annotation_types", None) or [])
    ]
    return owners[0] if len(owners) == 1 else ""


def _report_marker(slug: str, report_guid: str) -> str:
    return f"egeria_materialized::{slug}::{report_guid}"


def _utcnow() -> str:
    return datetime.now(timezone.utc).replace(tzinfo=None).isoformat()


def _finding_for(annotation: dict) -> dict:
    """One annotation as an upsert_finding() row.

    `check_name` is what kind of check, `label` its value — matching the
    contract every local surveyor already writes to, so the existing
    findings_list renderers display these with no special-casing.
    """
    confidence = annotation.get("confidence")
    return {
        "check_name": annotation.get("annotation_type") or annotation.get("type_name") or "annotation",
        "label": annotation.get("summary") or annotation.get("annotation_type") or "",
        "summary": annotation.get("explanation") or annotation.get("summary") or "",
        "confidence": 100 if confidence is None else confidence,
        "detail": _detail_for(annotation),
    }


def _detail_for(annotation: dict) -> dict:
    """Everything Egeria said, kept whole.

    `source` and `egeria_guid` are always present: a finding whose provenance is
    ambiguous is exactly the confusion this direction of data flow could
    introduce, so it is recorded on every row rather than inferred later from
    which table it landed in.
    """
    return {
        "source": "egeria",
        "egeria_guid": annotation.get("guid", ""),
        "annotation_type": annotation.get("annotation_type", ""),
        "type_name": annotation.get("type_name", ""),
        "analysis_step": annotation.get("analysis_step", ""),
        "expression": annotation.get("expression", ""),
        "json_properties": annotation.get("json_properties") or {},
        "subtype_data": annotation.get("subtype_data") or {},
    }


def _numeric_values(annotation: dict) -> dict[str, float]:
    """Numbers a measure annotation carries, flattened to {name: value}.

    Only genuinely numeric values are taken. A string that merely looks like a
    number is left alone: coercing it would invent a measurement Egeria did not
    make, and booleans are excluded because Python calls them ints.
    """
    out: dict[str, float] = {}
    sources = [annotation.get("json_properties") or {}]
    subtype = annotation.get("subtype_data") or {}
    for key in ("resourceProperties", "qualityScores"):
        val = subtype.get(key)
        if isinstance(val, str):
            try:
                val = json.loads(val)
            except (json.JSONDecodeError, TypeError):
                val = None
        if isinstance(val, dict):
            sources.append(val)
    for source in sources:
        for name, value in source.items():
            if isinstance(value, bool):
                continue
            if isinstance(value, (int, float)):
                out[str(name)] = float(value)
    return out
