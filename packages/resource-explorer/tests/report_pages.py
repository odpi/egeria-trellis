"""A MetadataExpert fake that serves a report's `ReportedAnnotation` list the way Egeria 6.2 does: a page at a time.

`create_autospec(MetadataExpert, spec_set=True, instance=True)`, so every call binds the real
`get_related_metadata_elements` signature (guid, relationship_type, body, ..., starting_at_end, start_from, page_size).
The report sits at end 1 of ReportedAnnotation; a call from any other end finds nothing. A page never holds more than
`cap` entries, whatever page size is asked; past the end the answer is pyegeria's "No elements found".

Also the helpers that turn plain annotation properties into the typed `elementProperties.propertyValueMap` the
related-elements read returns, and a depth-1 graph answer (`get_asset_by_guid`) into the entries it holds, so a test
written against the old single graph read can keep its data.
"""
from __future__ import annotations

from unittest.mock import create_autospec

#: Live, 2026-10-21 (coco_pharma report f533c846): one graph read of a 2,161-annotation report held 999.
GRAPH_PAGE = 999


def typed_properties(props: dict) -> dict:
    """Plain properties as Egeria's `elementProperties.propertyValueMap`: a typed value per property (primitive,
    map or array)."""
    def one(v):
        if isinstance(v, dict):
            return {"class": "MapTypePropertyValue",
                    "mapValues": {"propertyValueMap": {k: one(x) for k, x in v.items()}}}
        if isinstance(v, list):
            return {"class": "ArrayTypePropertyValue",
                    "arrayValues": {"propertyValueMap": {str(i): one(x) for i, x in enumerate(v)}}}
        return {"class": "PrimitiveTypePropertyValue", "primitiveValue": v}
    return {k: one(v) for k, v in props.items() if k != "class" and v is not None}


def related_entry(guid: str, type_name: str, props: dict) -> dict:
    """One RelatedMetadataElement of the related-elements answer."""
    return {"relationshipGUID": "rel-" + guid, "type": {"typeName": "ReportedAnnotation"}, "elementAtEnd1": False,
            "element": {"elementGUID": guid, "type": {"typeName": type_name},
                        "elementProperties": {"propertyValueMap": typed_properties(props)}}}


def entries_from_graph(result: dict) -> list[tuple[str, str, dict]]:
    """(guid, type name, plain properties) for each entry of a depth-1 graph answer's `reportedAnnotations`."""
    out = []
    for e in (result or {}).get("reportedAnnotations") or []:
        rel = e.get("relatedElement") or {}
        header = rel.get("elementHeader") or {}
        out.append((header.get("guid", ""), (header.get("type") or {}).get("typeName", "Annotation"),
                    rel.get("properties") or {}))
    return out


def paged_expert(provider, *, cap: int = GRAPH_PAGE, calls: list | None = None, stop_after: int | None = None):
    """An autospec'd MetadataExpert whose `get_related_metadata_elements` serves `provider(report_guid)` (a list of
    (guid, type name, plain props)) in pages. `calls` collects (report, start_from, size, returned) per page.
    `stop_after`: answer "No elements found" from the Nth page on, the way a reader that stops paging early would be
    fooled -- used to show a proof fails when pages are lost."""
    from pyegeria import MetadataExpert

    expert = create_autospec(MetadataExpert, spec_set=True, instance=True)
    seen = calls if calls is not None else []

    def serve(guid, relationship_type, body, for_lineage=None, for_duplicate_processing=None, starting_at_end=0,
              start_from=0, page_size=500, *args, **kwargs):
        if relationship_type != "ReportedAnnotation" or starting_at_end != 1:
            return "No elements found"
        entries = provider(guid)
        size = min(page_size or cap, cap)
        chunk = entries[start_from:start_from + size]
        seen.append((guid, start_from, size, len(chunk)))
        if not chunk or (stop_after is not None and len(seen) > stop_after):
            return "No elements found"
        return {"elementList": [related_entry(*e) for e in chunk]}

    expert.get_related_metadata_elements.side_effect = serve
    return expert
