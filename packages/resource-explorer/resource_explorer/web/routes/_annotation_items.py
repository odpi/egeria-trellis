"""Shared construction of the `.../egeria-surveys/{guid}/annotations` response
items for the repo, database and filesystem routes.

One annotation that does not fit the response model must not turn the whole
report into a 500 (that hid 614 annotations behind one bad one). The failing
item is returned as an item that says so, carrying the original input, so the
person sees it and nothing is dropped silently."""
from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, ValidationError


def build_annotation_items(model: type[BaseModel], annotations: list[dict]) -> list[Any]:
    items = []
    for a in annotations:
        try:
            items.append(model(**a))
        except ValidationError as exc:
            first = exc.errors()[0]
            where = ".".join(str(p) for p in first.get("loc", ()))
            items.append(model(
                guid=str(a.get("guid", "")),
                annotation_type=str(a.get("annotation_type", "") or "Unreadable annotation"),
                summary=f"This annotation could not be read: field {where or '?'} {first.get('msg', 'was invalid')}.",
                confidence=None,
                analysis_step="",
                explanation="",
                expression="",
                json_properties={"unreadable_input": json.dumps(a, default=str)},
            ))
    return items
