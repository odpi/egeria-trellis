"""What Egeria 6.2 keeps of an annotation's properties: the strict half of every annotation fake.

Egeria's annotation property beans are `@JsonIgnoreProperties(ignoreUnknown=true)`. A field the bean has no setter
for is dropped when the request is read, the create still returns a GUID, and nothing says so. That is how every
`candidateClassifications` RE published on a ClassificationAnnotation reached Egeria with no content (found
2026-10-15). A fake that stores whatever it is sent cannot see that, so a fake must keep only what Egeria keeps.

The accepted fields are not written by hand. `scripts/extract_egeria_annotation_fields.py` reads them from Egeria's
beans at the V6.2 tag (the setters of each bean and its superclasses, which is what Jackson binds, plus the `class`
type id) into `fixtures/egeria_6_2/annotation_properties_fields.json`. For ClassificationAnnotation that agrees with
the typedef: OpenMetadataTypesArchive1_2.getClassificationAnnotationEntity adds CLASSIFICATION_NAME and PROPERTIES
to DataFieldAnnotation and nothing else.
"""
from __future__ import annotations

import json
from pathlib import Path

_FIXTURE = Path(__file__).parent / "fixtures" / "egeria_6_2" / "annotation_properties_fields.json"
_TABLE = json.loads(_FIXTURE.read_text(encoding="utf-8"))
SOURCE = _TABLE.pop("_source")

#: Egeria property class -> the JSON fields its bean keeps.
ACCEPTED_FIELDS: dict[str, frozenset[str]] = {cls: frozenset(v["fields"]) for cls, v in _TABLE.items()}


def accepted_fields(egeria_class: str) -> frozenset[str]:
    """The fields Egeria keeps for `egeria_class`. A class with no bean is refused, as Egeria refuses it (400)."""
    try:
        return ACCEPTED_FIELDS[egeria_class]
    except KeyError:
        raise ValueError(f"Egeria 6.2 has no annotation properties bean {egeria_class!r}") from None


def egeria_keeps(props: dict) -> tuple[dict, list[str]]:
    """(the properties as Egeria stores them, the sorted fields it dropped without saying so)."""
    allowed = accepted_fields(props.get("class", ""))
    dropped = sorted(set(props) - allowed)
    return {k: v for k, v in props.items() if k in allowed}, dropped
