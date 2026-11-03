"""Pure JSON/XML value-schema inference — no Postgres, no pyegeria, no
registry.

Implements the "given a bag of sampled JSON or XML values, what is the
schema" half of `docs/multi-resource-questions-design.md` §5.4's
`nested_column_profile` question (Phase 1 slice 11, `postgres_nested_columns`
— gated on slice 10, `postgres_column_profile`, which this step shares its
sampling infrastructure with).

Deliberately factored out with **zero database coupling** — no psycopg2, no
pyegeria, no `resource_explorer.registry` import — the same way slice 10 split
`column_matching.py` out of `column_profile_step.py`. §5.4's own row names the
reason: this module "produces a `SchemaAnalysisAnnotation` with an inferred
nested schema, **reused by §6's `nested_schema_profile`**" — the filesystem
path (Phase 2, not part of this slice) needs the identical "given these
parsed JSON documents / XML trees, what's the schema" logic against files on
disk, with no database anywhere in the loop. Anything Postgres-specific
(sampling SQL, `TABLESAMPLE`, budget accounting) belongs in
`surveyors/database/nested_columns_step.py`, which calls into this module the
same way `column_profile_step.py` calls into `column_matching.py`.

Two independent shapes, because JSON and XML do not profile the same way:

- `infer_json_schema()` — walks a bag of already-PARSED JSON values (Python
  `dict` / `list` / `str` / `int` / `float` / `bool` / `None`) and returns
  per-path key presence frequency, the JSON types observed at each path and
  whether they are consistent, and how deep the structure goes.
- `infer_xml_schema()` — walks a bag of `xml.etree.ElementTree.Element` trees
  and returns root-element frequency plus element/attribute NAME frequency —
  the "xpath key sampling" §5.4 asks for, done with the stdlib parser rather
  than a Postgres `xpath()` round trip specifically so the same function can
  profile an XML *file* in §6 with no database involved at all. See
  `POSTGRES-NESTED-COLUMNS-IMPLEMENTED.md` for why this is
  a documented deviation from the design doc's literal
  `jsonb_object_keys`/`jsonb_typeof`/`xpath()` SQL-function wording rather
  than an oversight.

**Absence discipline, restated for this shape.** A "bag of values" can fail
to produce a schema for reasons that must not collapse into one:

- nothing was sampled at all (capability absent, `catalog_stats_only`, budget
  exhausted, or the query failed) — this module is never even called, and the
  caller must record `STATE_NOT_SUPPORTED`/`STATE_NOT_COLLECTED` itself (see
  `nested_columns_step.py`);
- the sample ran and returned zero non-NULL values — `classify_json_sample`/
  `classify_xml_sample` return `LABEL_EMPTY`, the caller's `STATE_EMPTY`;
- **every sampled value was a JSON scalar, not an object or array** — a real,
  different finding from "not established": the column is JSONB-typed but
  holds no nested structure to profile, which is worth surfacing as its own
  label (`LABEL_SCALAR_ONLY`) rather than an empty schema;
- some values were scalars and some were structured — `LABEL_MIXED`, also a
  real finding (inconsistent use of the column across rows);
- every value was structured (object, or array of objects) — `LABEL_STRUCTURED`,
  the ordinary case, carrying the inferred schema;
- for XML specifically, a sampled value that does not parse as well-formed
  XML at all — `LABEL_UNPARSEABLE` — is itself worth reporting rather than
  being silently skipped, since a column typed `xml` holding non-XML content
  is a data-quality finding of its own.

None of these is `MATCH_*` from `column_matching.py`: there is no known
element to compare against here, only a value shape to describe, so that
module's verdict vocabulary genuinely does not fit (this is a profiling
question, not a matching one). What IS reused, in spirit, is slice 7's
`STATE_MEASURED`/`STATE_EMPTY`/`STATE_NOT_COLLECTED`/`STATE_NOT_SUPPORTED`
"did we get a sample" layer (`resource_explorer.registry`) — this module
stays free of that import so it has no database-registry coupling, and the
caller in `nested_columns_step.py` maps `LABEL_EMPTY` to `STATE_EMPTY` and so
on. `LABEL_SCALAR_ONLY`/`LABEL_MIXED`/`LABEL_STRUCTURED`/`LABEL_UNPARSEABLE`
sit *inside* `STATE_MEASURED` — they are findings, not not-established states.
"""
from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

# ── JSON value types ───────────────────────────────────────────────────────

JSON_TYPE_OBJECT = "object"
JSON_TYPE_ARRAY = "array"
JSON_TYPE_STRING = "string"
JSON_TYPE_NUMBER = "number"
JSON_TYPE_BOOLEAN = "boolean"
JSON_TYPE_NULL = "null"

#: Types that mean "the value at this path is nested structure", not a leaf.
JSON_CONTAINER_TYPES: frozenset[str] = frozenset({JSON_TYPE_OBJECT, JSON_TYPE_ARRAY})


def json_value_type(value: Any) -> str:
    """The JSON type name of a parsed Python value.

    `bool` is checked before `(int, float)` because `bool` is a subclass of
    `int` in Python — without the order, every `true`/`false` value would be
    reported as `"number"`.
    """
    if value is None:
        return JSON_TYPE_NULL
    if isinstance(value, bool):
        return JSON_TYPE_BOOLEAN
    if isinstance(value, (int, float)):
        return JSON_TYPE_NUMBER
    if isinstance(value, str):
        return JSON_TYPE_STRING
    if isinstance(value, list):
        return JSON_TYPE_ARRAY
    if isinstance(value, dict):
        return JSON_TYPE_OBJECT
    # A driver-specific scalar (e.g. Decimal) — treat as a leaf, not a
    # container, so it never triggers a recursive walk.
    return JSON_TYPE_STRING


# ── Verdict labels, shared by the JSON and XML shapes ──────────────────────

#: The sample ran and returned zero non-NULL values. Maps to the caller's
#: `STATE_EMPTY` (registry.py) — a measurement, not a not-established state.
LABEL_EMPTY = "empty"
#: Every sampled top-level value was a JSON scalar (JSON) or failed to parse
#: as an element at all in a way that leaves nothing to profile (XML, when
#: EVERY value is unparseable — see LABEL_UNPARSEABLE for the mixed case).
#: A real, different finding from "no nested structure was found": the column
#: is typed for nested data and none of the sample actually held any.
LABEL_SCALAR_ONLY = "scalar_only"
#: Some sampled values were scalars/unparseable and some were structured —
#: inconsistent use of the column across rows, worth surfacing on its own.
LABEL_MIXED = "mixed"
#: Every sampled value was structured (JSON object, or array containing at
#: least one object; XML that parsed as well-formed). The schema fields on
#: the returned dataclass are populated.
LABEL_STRUCTURED = "structured"
#: XML only: the sampled value did not parse as well-formed XML at all. A
#: data-quality finding (a column typed `xml` holding non-XML content), not
#: an absence — the sample was read, and this is what was in it.
LABEL_UNPARSEABLE = "unparseable"

#: Labels that are still real findings even though they carry no inferred
#: keys — a caller must not read an empty `keys`/`names` tuple under one of
#: these as "profiled and found nothing nested", which would be the JSON
#: analogue of the exact defect this slice's absence discipline exists to
#: avoid.
NO_SCHEMA_LABELS: frozenset[str] = frozenset(
    {LABEL_SCALAR_ONLY, LABEL_UNPARSEABLE}
)


# ── JSON: per-path key profile ──────────────────────────────────────────────

@dataclass(frozen=True)
class KeyProfile:
    """One key path's presence and type-consistency across the sample.

    `path` is dot-joined, with `[]` marking "inside an array" — e.g.
    `"items[].sku"` for the `sku` key of objects found inside an `items`
    array. `presence_fraction` is always computed against the number of
    top-level DOCUMENTS in the sample (see `infer_json_schema`), not against
    the parent path's own count — deliberately: a nested path's fraction then
    directly answers "in what share of all sampled rows does this exact path
    appear", which composes (a nested path's fraction can never exceed its
    parent's) and needs no second denominator to interpret.
    """

    path: str
    depth: int
    presence_count: int
    sample_size: int
    #: JSON type name -> number of times that type was observed at this path.
    observed_types: dict[str, int] = field(default_factory=dict)

    @property
    def presence_fraction(self) -> float:
        if self.sample_size <= 0:
            return 0.0
        return self.presence_count / float(self.sample_size)

    @property
    def type_consistent(self) -> bool:
        """Whether this key held one JSON type (ignoring `null`) across the
        sample.

        `null` is excluded from the consistency check: an optional string
        field that is sometimes absent-as-null is still "a string field", and
        counting `null` as a competing type would report every nullable
        column as inconsistent. A key that is ONLY ever null has no non-null
        type to be consistent or inconsistent about, so it reads as
        consistent (there is nothing to disagree about) — `dominant_type` is
        `None` for that case, which is the honest signal.
        """
        non_null = {t for t in self.observed_types if t != JSON_TYPE_NULL}
        return len(non_null) <= 1

    @property
    def dominant_type(self) -> str | None:
        non_null = {t: c for t, c in self.observed_types.items() if t != JSON_TYPE_NULL}
        if not non_null:
            return None
        return max(non_null.items(), key=lambda pair: pair[1])[0]

    def as_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "depth": self.depth,
            "presence_count": self.presence_count,
            "sample_size": self.sample_size,
            "presence_fraction": round(self.presence_fraction, 4),
            "observed_types": dict(self.observed_types),
            "type_consistent": self.type_consistent,
            "dominant_type": self.dominant_type,
        }


@dataclass(frozen=True)
class InferredJsonSchema:
    """The schema inferred from a bag of sampled JSON values.

    `document_count` (objects, or objects unrolled from a top-level array) is
    the denominator every `KeyProfile.presence_fraction` is stated against —
    NOT `sample_size`, which also counts scalar/empty-array top-level values
    that contribute no keys at all. Keeping the two separate is what lets a
    caller distinguish "a schema over the objects that were present" from
    "how many of the sampled values were objects in the first place" (that
    second question is `classify_json_sample`'s job, not this dataclass's).
    """

    sample_size: int
    scalar_count: int
    array_count: int
    object_count: int
    #: Objects considered for key inference: `object_count` top-level objects
    #: plus every dict found inside a top-level array.
    document_count: int
    keys: tuple[KeyProfile, ...] = ()
    max_depth: int = 0
    truncated: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "sample_size": self.sample_size,
            "scalar_count": self.scalar_count,
            "array_count": self.array_count,
            "object_count": self.object_count,
            "document_count": self.document_count,
            "max_depth": self.max_depth,
            "truncated": self.truncated,
            "keys": [k.as_dict() for k in self.keys],
        }


#: §5.8-style bounds, restated for this shape: a pathologically deep or wide
#: JSON document must not make this a second sampling budget of its own.
DEFAULT_MAX_DEPTH = 6
DEFAULT_MAX_KEYS = 500


def infer_json_schema(
    values: Sequence[Any],
    *,
    max_depth: int = DEFAULT_MAX_DEPTH,
    max_keys: int = DEFAULT_MAX_KEYS,
) -> InferredJsonSchema:
    """Infer a nested schema from a bag of already-PARSED JSON values.

    `values` are Python objects (the result of `json.loads`, or whatever a
    JSONB driver already deserialised to) — NOT JSON text. Passing raw text
    here is a caller error; `nested_columns_step.py` does the parsing before
    calling in, so a value that fails to parse never reaches this function
    disguised as one that did.

    A top-level value that is itself a JSON scalar (`scalar_count`) or an
    array with no dict elements contributes no keys — that is
    `classify_json_sample`'s finding to make (`LABEL_SCALAR_ONLY` /
    `LABEL_MIXED`), not an error here. An empty `values` sequence returns an
    all-zero schema; the caller decides what that means (§5.8's absence
    discipline is about the SAMPLE, which this function is never even
    handed a chance to see if it was empty).
    """
    documents: list[dict] = []
    scalar_count = 0
    array_count = 0
    object_count = 0

    for value in values:
        kind = json_value_type(value)
        if kind == JSON_TYPE_OBJECT:
            object_count += 1
            documents.append(value)
        elif kind == JSON_TYPE_ARRAY:
            array_count += 1
            documents.extend(v for v in value if isinstance(v, dict))
        else:
            scalar_count += 1

    key_stats: dict[str, dict[str, Any]] = {}
    max_depth_seen = 0

    def _walk(obj: dict, prefix: str, depth: int) -> None:
        nonlocal max_depth_seen
        if depth > max_depth:
            return
        for k, v in obj.items():
            path = f"{prefix}.{k}" if prefix else str(k)
            stat = key_stats.get(path)
            if stat is None:
                if len(key_stats) >= max_keys:
                    continue
                stat = {"depth": depth, "types": Counter()}
                key_stats[path] = stat
            stat["types"][json_value_type(v)] += 1
            max_depth_seen = max(max_depth_seen, depth)
            if isinstance(v, dict):
                _walk(v, path, depth + 1)
            elif isinstance(v, list):
                for el in v:
                    if isinstance(el, dict):
                        _walk(el, path + "[]", depth + 1)

    for doc in documents:
        _walk(doc, "", 1)

    document_count = len(documents)
    keys = tuple(
        KeyProfile(
            path=path,
            depth=stat["depth"],
            presence_count=sum(stat["types"].values()),
            sample_size=document_count,
            observed_types=dict(stat["types"]),
        )
        for path, stat in key_stats.items()
    )
    keys = tuple(
        sorted(keys, key=lambda k: (-k.presence_fraction, k.path))
    )

    return InferredJsonSchema(
        sample_size=len(values),
        scalar_count=scalar_count,
        array_count=array_count,
        object_count=object_count,
        document_count=document_count,
        keys=keys,
        max_depth=max_depth_seen,
        truncated=len(key_stats) >= max_keys,
    )


def classify_json_sample(schema: InferredJsonSchema) -> str:
    """One of `LABEL_EMPTY`/`LABEL_SCALAR_ONLY`/`LABEL_MIXED`/
    `LABEL_STRUCTURED`, from an already-built `InferredJsonSchema`.

    Reads only the top-level counts, deliberately — a document that IS an
    object with zero keys (`{}`) still counts as structured (it is nested
    data, just empty), whereas a scalar is never structured whatever its
    value.
    """
    if schema.sample_size == 0:
        return LABEL_EMPTY
    structured = schema.object_count + schema.array_count
    if structured == 0:
        return LABEL_SCALAR_ONLY
    if schema.scalar_count == 0:
        return LABEL_STRUCTURED
    return LABEL_MIXED


# ── XML: element/attribute name frequency ───────────────────────────────────

@dataclass(frozen=True)
class ElementNameProfile:
    """One element or attribute NAME's presence across the sampled XML
    documents that parsed successfully.

    `kind` is `"element"` or `"attribute"`. `presence_fraction` is against
    `sample_size` — the number of documents that parsed at all — not the
    number of times the name occurred (a name appearing five times in one
    document and zero elsewhere is still "present in one document", which is
    the useful cross-row signal; occurrence counts are noise for a schema
    question and are not tracked).
    """

    name: str
    kind: str
    presence_count: int
    sample_size: int

    @property
    def presence_fraction(self) -> float:
        if self.sample_size <= 0:
            return 0.0
        return self.presence_count / float(self.sample_size)

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "kind": self.kind,
            "presence_count": self.presence_count,
            "sample_size": self.sample_size,
            "presence_fraction": round(self.presence_fraction, 4),
        }


@dataclass(frozen=True)
class InferredXmlSchema:
    """The schema inferred from a bag of sampled XML values (raw text/bytes).

    `parsed_count` is the denominator for every `ElementNameProfile` — the
    unparseable ones contribute nothing to name frequency, on purpose, the
    same way a scalar JSON value contributes no keys.
    """

    sample_size: int
    parsed_count: int
    unparseable_count: int
    #: Root element local name -> number of parsed documents with that root.
    root_element_counts: dict[str, int] = field(default_factory=dict)
    names: tuple[ElementNameProfile, ...] = ()
    max_depth: int = 0
    truncated: bool = False

    @property
    def dominant_root_element(self) -> str | None:
        if not self.root_element_counts:
            return None
        return max(self.root_element_counts.items(), key=lambda pair: pair[1])[0]

    def as_dict(self) -> dict[str, Any]:
        return {
            "sample_size": self.sample_size,
            "parsed_count": self.parsed_count,
            "unparseable_count": self.unparseable_count,
            "root_element_counts": dict(self.root_element_counts),
            "dominant_root_element": self.dominant_root_element,
            "max_depth": self.max_depth,
            "truncated": self.truncated,
            "names": [n.as_dict() for n in self.names],
        }


def _local_name(tag: str) -> str:
    """Strip an ElementTree `{namespace}localname` qualifier.

    Namespace-qualified tags are the common case for real-world XML, and a
    key-frequency report keyed on `"{http://.../v3}Patient"` would never
    match `"Patient"` from a document using a different namespace prefix (or
    none) for the same element — every genuinely-shared name would look
    unique. `xpath()`'s own `name()` in Postgres has the identical
    local-name-only behaviour, so this keeps the two implementations
    describing the same thing (see the module docstring on why this uses
    ElementTree rather than calling `xpath()` in SQL).
    """
    if tag.startswith("{"):
        return tag.split("}", 1)[1]
    return tag


def _parse_xml(value: Any) -> ET.Element | None:
    if value is None:
        return None
    if isinstance(value, ET.Element):
        return value
    if isinstance(value, (bytes, bytearray)):
        text = value
    elif isinstance(value, str):
        text = value
    else:
        return None
    try:
        return ET.fromstring(text)
    except ET.ParseError:
        return None


def infer_xml_schema(
    raw_values: Sequence[Any],
    *,
    max_depth: int = DEFAULT_MAX_DEPTH,
    max_names: int = DEFAULT_MAX_KEYS,
) -> InferredXmlSchema:
    """Infer element/attribute name frequency and root-element shape from a
    bag of sampled XML values.

    `raw_values` are XML text/bytes (or already-parsed `Element`s, accepted
    directly so a caller that already parsed a file in §6 does not have to
    re-serialise it to text first). A value that fails to parse counts toward
    `unparseable_count` and contributes nothing to `names` — see
    `LABEL_UNPARSEABLE`/`classify_xml_sample`, which is how that shows up as
    a finding rather than being silently dropped.
    """
    root_counts: Counter[str] = Counter()
    name_stats: dict[tuple[str, str], set[int]] = {}
    unparseable = 0
    parsed_count = 0
    max_depth_seen = 0

    def _walk(el: ET.Element, doc_index: int, depth: int) -> None:
        nonlocal max_depth_seen
        if depth > max_depth:
            return
        max_depth_seen = max(max_depth_seen, depth)
        key = (_local_name(el.tag), "element")
        if key in name_stats or len(name_stats) < max_names:
            name_stats.setdefault(key, set()).add(doc_index)
        for attr_name in el.attrib:
            akey = (_local_name(attr_name), "attribute")
            if akey not in name_stats and len(name_stats) >= max_names:
                continue
            name_stats.setdefault(akey, set()).add(doc_index)
        for child in el:
            _walk(child, doc_index, depth + 1)

    for i, raw in enumerate(raw_values):
        root = _parse_xml(raw)
        if root is None:
            unparseable += 1
            continue
        parsed_count += 1
        root_counts[_local_name(root.tag)] += 1
        _walk(root, i, depth=1)

    names = tuple(
        sorted(
            (
                ElementNameProfile(
                    name=name, kind=kind,
                    presence_count=len(doc_indices), sample_size=parsed_count,
                )
                for (name, kind), doc_indices in name_stats.items()
            ),
            key=lambda n: (-n.presence_fraction, n.kind, n.name),
        )
    )

    return InferredXmlSchema(
        sample_size=len(raw_values),
        parsed_count=parsed_count,
        unparseable_count=unparseable,
        root_element_counts=dict(root_counts),
        names=names,
        max_depth=max_depth_seen,
        truncated=len(name_stats) >= max_names,
    )


def classify_xml_sample(schema: InferredXmlSchema) -> str:
    """One of `LABEL_EMPTY`/`LABEL_UNPARSEABLE`/`LABEL_MIXED`/`LABEL_STRUCTURED`.

    XML has no scalar/object distinction the way JSON does — a value is
    either well-formed XML or it is not — so `LABEL_SCALAR_ONLY` is not
    reachable from this function; `LABEL_UNPARSEABLE` is XML's equivalent
    finding ("the column is typed `xml` and nothing sampled from it actually
    was"), kept as a separate label rather than reusing `LABEL_SCALAR_ONLY`
    so the two shapes' findings are not conflated in a downstream reader that
    only checks the label string.
    """
    if schema.sample_size == 0:
        return LABEL_EMPTY
    if schema.parsed_count == 0:
        return LABEL_UNPARSEABLE
    if schema.unparseable_count == 0:
        return LABEL_STRUCTURED
    return LABEL_MIXED
