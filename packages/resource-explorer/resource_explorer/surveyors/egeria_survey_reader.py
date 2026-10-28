"""
Engine-agnostic reader for live Egeria SurveyReport/Annotation data, walking
the real ReportSubject/ReportedAnnotation relationships from a resource's own
asset GUID. Works for any resource type (database, repo, filesystem) that has
an AssetMaker-capable Egeria connection, since RE's own publishers
(EgeriaDatabaseSurveyor, EgeriaPublisher, EgeriaFileSystemSurveyor) all write
SurveyReports via the identical relationship shape — this is pure AssetMaker
graph traversal with no resource-type-specific logic.

Confirmed live 2026-07-08 via AssetMaker.get_asset_by_guid(guid,
body={"graphQueryDepth": 1}): the response has a top-level "reports" key (for
a resource asset) or "reportedAnnotations" key (for a SurveyReport asset),
each listing RelatedMetadataElementSummary entries.
"""
from __future__ import annotations

import json
import logging

log = logging.getLogger(__name__)


def _safe_int(value, default: int = 0) -> int:
    try:
        return int(value) if value is not None else default
    except (ValueError, TypeError):
        return default


def parse_json_properties(value) -> dict:
    """An annotation's `jsonProperties` as a dict.

    Egeria stores it as a JSON STRING (RE itself writes it that way). A dict
    passes through; empty / None / blank is `{}`. A string that is not valid
    JSON, or is valid JSON but not an object, is kept whole under `"raw"` so
    no text is lost and nothing is dropped without a trace."""
    if isinstance(value, dict):
        return value
    if value is None or (isinstance(value, str) and not value.strip()):
        return {}
    if not isinstance(value, str):
        return {"raw": str(value)}
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return {"raw": value}
    return parsed if isinstance(parsed, dict) else {"raw": value}


def get_survey_reports_by_guid(asset_maker, resource_guid: str) -> list[dict]:
    """All SurveyReports linked to a resource's asset GUID via ReportSubject,
    regardless of which engine (RE or Egeria's own native survey) created
    them. `asset_maker` is any connected pyegeria AssetMaker instance."""
    reports: list[dict] = []
    try:
        body = {"class": "GetRequestBody", "graphQueryDepth": 1}
        result = asset_maker.get_asset_by_guid(resource_guid, body=body, output_format="JSON")
        if not isinstance(result, dict):
            return []
        for entry in result.get("reports") or []:
            related = entry.get("relatedElement", {})
            header = related.get("elementHeader", {})
            props = related.get("properties", {})
            additional = props.get("additionalProperties", {})
            surveyed_at = header.get("versions", {}).get("createTime", "")
            reports.append({
                "guid": header.get("guid", ""),
                "qualified_name": props.get("qualifiedName", ""),
                "display_name": props.get("displayName", ""),
                "surveyed_at": surveyed_at,
                "annotation_count": _safe_int(additional.get("annotation_count")),
                "schema_count": _safe_int(additional.get("schema_count")),
                "table_count": _safe_int(additional.get("table_count")),
                "column_count": _safe_int(additional.get("column_count")),
                "description": props.get("description", ""),
            })
    except Exception as exc:
        log.debug(f"get_survey_reports_by_guid failed for {resource_guid}: {exc}")

    reports.sort(key=lambda r: r.get("surveyed_at", ""), reverse=True)
    return reports


def get_annotations_by_report_guid(asset_maker, report_guid: str) -> list[dict]:
    """All annotations attached to a SurveyReport via ReportedAnnotation,
    regardless of which engine produced the report.

    Swallows every failure and returns `[]` -- so `[]` here means "nothing
    came back", NOT "the report has no annotations". Callers that must tell
    those apart (the native-survey read-back) call `annotations_from_report`
    on a `get_asset_by_guid` result they fetched themselves."""
    annotations: list[dict] = []
    try:
        body = {"class": "GetRequestBody", "graphQueryDepth": 1}
        result = asset_maker.get_asset_by_guid(report_guid, body=body, output_format="JSON")
        if isinstance(result, dict):
            annotations = annotations_from_report(result)
    except Exception as exc:
        log.debug(f"get_annotations_by_report_guid failed for {report_guid}: {exc}")

    return annotations


def annotations_from_report(result: dict) -> list[dict]:
    """The flat annotation dicts out of one `get_asset_by_guid(report_guid,
    graphQueryDepth=1)` result. Pure -- raises nothing on a well-formed
    result, and never touches the network."""
    annotations: list[dict] = []
    for entry in result.get("reportedAnnotations") or []:
        related = entry.get("relatedElement", {})
        header = related.get("elementHeader", {})
        props = related.get("properties", {})
        annotation_type = props.get("annotationType", "") or header.get("type", {}).get("typeName", "")
        annotations.append({
            "guid": header.get("guid", ""),
            "annotation_type": annotation_type,
            "type_name": header.get("type", {}).get("typeName", ""),
            "qualified_name": props.get("qualifiedName", ""),
            "display_name": props.get("displayName", ""),
            "summary": props.get("summary", ""),
            "confidence": _safe_int(props.get("confidence", 100)),
            "analysis_step": props.get("analysisStep", ""),
            "additional_properties": props.get("additionalProperties") or {},
            "explanation": props.get("explanation", ""),
            "expression": props.get("expression", ""),
            "json_properties": parse_json_properties(props.get("jsonProperties")),
            # Egeria's `contentStatus` — whether the annotation's CONTENT
            # is complete. `DRAFT` means it is a PROPOSAL: a candidate
            # Data Class or reference-data set that no curator has
            # confirmed (Phase 1 slice 10, design §5.4; the mechanism is
            # `docs/egeria-support-for-multi-resource.md` §3 as settled by
            # the corrected probe 4).
            #
            # Added 2026-09-21 because nothing in RE read this field —
            # measured, not assumed: every occurrence of `contentStatus`
            # in the package was an outbound write (the two arch-recovery
            # materializers) or a comment. A DRAFT proposal was therefore
            # rendered identically to confirmed content, which would have
            # made slice 10's whole proposal mechanism invisible. This
            # reader is the first of the four hops that had to change; see
            # POSTGRES-COLUMN-PROFILE-IMPLEMENTED.md for the chain.
            #
            # Empty string is NOT "confirmed": it is "no contentStatus
            # stated", which is what every annotation published before
            # slice 10 carries. A renderer must not draw a badge for "".
            "content_status": props.get("contentStatus", "") or "",
            "user_defined_content_status": props.get("userDefinedContentStatus", "") or "",
            # The measurement payload itself. Until 2026-09-20 this reader
            # dropped every one of these, so a native survey's numbers
            # were read back as prose in `summary` and nothing else — the
            # reason native results could not be queried like local ones.
            #
            # `resourceProperties` is what ResourceMeasureAnnotation
            # carries (Map<String,String> — every value arrives as a
            # string, including the numbers), and the profile* fields are
            # ResourceProfileAnnotation's. Names are taken verbatim from
            # the Java property classes; see result_materializer.py.
            "resource_properties": props.get("resourceProperties") or {},
            "profile_properties": props.get("profileProperties") or {},
            "profile_counts": props.get("profileCounts") or {},
            "profile_doubles": props.get("profileDoubles") or {},
            "profile_flags": props.get("profileFlags") or {},
            "profile_dates": props.get("profileDates") or {},
            "value_list": props.get("valueList") or [],
            "value_count": props.get("valueCount") or {},
            "value_range_from": props.get("valueRangeFrom", ""),
            "value_range_to": props.get("valueRangeTo", ""),
            "average_value": props.get("averageValue", ""),
            "inferred_data_type": props.get("inferredDataType", ""),
        })

    return annotations
