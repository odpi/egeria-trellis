"""The `postgres_nested_columns` step (design §5.4/§5.7, Phase 1 slice 11).

Design §5.4's row for this question:

> **What is inside the JSON/JSONB/XML columns — keys, nesting, types,
> consistency across rows?** `nested_column_profile` (new; JSONB:
> `jsonb_object_keys`, `jsonb_typeof` over a sample, key frequency, depth;
> XML: root element and xpath key sampling; produces a `SchemaAnalysisAnnotation`
> with an inferred nested schema, reused by §6's `nested_schema_profile`)

§5.7's row for the step itself:

> `postgres_nested_columns` (new) | sampled JSON/XML values | api_heavy /
> medium | SchemaAnalysis (nested)

**Gated on slice 10 ("shares the inference core", per the coordinator
brief).** This module is the Postgres-specific half — identifying JSONB/JSON/
XML columns, sampling their raw values with slice 10's *exact* sampling
machinery, and building the annotations. The actual "given these sampled
values, what's the schema" logic is `nested_schema_inference.py`, imported
and never duplicated — the same split `column_profile_step.py` /
`column_matching.py` established for slice 10.

**What is deliberately reused, not reimplemented, from slice 10:**

- `sampling.py`'s `resolve_sampling_config` / `SamplingConfig` /
  `SampleProvenance` — the identical `max_rows`/`max_bytes`/`max_values`/
  strategy/seed/time-budget configuration surface, resolved with
  `purpose="matching"` (the same default slice 10 uses for value-based
  questions — a nested-schema claim is exactly that shape, not a `pg_stats`
  measure).
- `column_profile_step.py`'s `sample_column_values` and `SamplingBudget` —
  the exact SQL-execution and byte/time-budget accounting slice 10 built,
  including its `values is None` vs `values == []` distinction. No second
  sampling implementation exists in this module.
- `column_matching.py`'s `type_family` — the same column-type-to-family
  classifier slice 10 uses for its own `UNMATCHABLE_TYPE_FAMILIES` gate,
  reused here to identify which columns are `"json"` (covers `json` and
  `jsonb`) or `"xml"` in the first place.

**Absence discipline** (design brief's own wording): a column with no
JSONB/XML values sampled renders as `not_established` — never as "no nested
structure" — via `STATE_NOT_SUPPORTED`/`STATE_NOT_COLLECTED`
(`resource_explorer.registry`, slice 7's vocabulary, reused rather than a
fourth parallel one). A column where every sampled value is a JSON scalar (or,
for XML, unparseable) is `STATE_MEASURED` with a `scalar_only`/`unparseable`
label — a real, different finding, not an absence.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from resource_explorer.registry import (
    STATE_EMPTY,
    STATE_MEASURED,
    STATE_NOT_COLLECTED,
    STATE_NOT_SUPPORTED,
)
from resource_explorer.surveyors.database.column_matching import type_family
from resource_explorer.surveyors.database.column_profile_step import (
    SamplingBudget,
    _row_counts_by_table,
    sample_column_values,
)
from resource_explorer.surveyors.database.sampling import (
    SampleProvenance,
    not_sampled_provenance,
    resolve_sampling_config,
)
from resource_explorer.surveyors.nested_schema_inference import (
    LABEL_EMPTY,
    LABEL_MIXED,
    LABEL_SCALAR_ONLY,
    LABEL_STRUCTURED,
    LABEL_UNPARSEABLE,
    classify_json_sample,
    classify_xml_sample,
    infer_json_schema,
    infer_xml_schema,
)
from resource_explorer.surveyors.survey_report import (
    ResourceMeasureAnnotation,
    SchemaAnalysisAnnotation,
)

log = logging.getLogger(__name__)

#: The `analysis_step` every annotation from this step carries.
ANALYSIS_STEP = "DatabasePostgresNestedColumns"

#: The two column-type families this step profiles. Anything else is simply
#: not iterated — not a `not_applicable` verdict, because "this step does not
#: apply to a column type it never considered" needs no per-column record any
#: more than `data_class_match` records one for a table this survey never saw.
NESTED_TYPE_FAMILIES: frozenset[str] = frozenset({"json", "xml"})

#: `LABEL_* -> registry.STATE_*` — a label inside `STATE_MEASURED` is a
#: finding, not an absence, so only `LABEL_EMPTY` maps outside it.
_LABEL_TO_STATE: dict[str, str] = {LABEL_EMPTY: STATE_EMPTY}


def _parse_json_value(value: Any) -> Any:
    """Turn one raw sampled value into a Python JSON object.

    A JSONB/JSON driver value commonly arrives already deserialised (a
    `dict`/`list`/scalar); `psycopg2` with no JSON adapter registered, or a
    generic `execute_query` path, may instead hand back the raw text. Both
    are accepted: text is parsed with `json.loads`, and anything already a
    Python object is passed through unchanged. Text that fails to parse is
    surfaced as its raw string rather than raising — Postgres itself
    guarantees a `jsonb` column's stored bytes are valid JSON, so this branch
    exists only for a value read through some other path, and a defensive
    fallback is safer than letting the whole column's sample abort on one bad
    value.
    """
    if isinstance(value, (dict, list)) or value is None:
        return value
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (TypeError, ValueError):
            return value
    return value


def _iter_nested_columns(schema_info: dict) -> list[tuple[str, str, str, str]]:
    """Every `(schema, table, column, family)` this step profiles.

    `family` is `"json"` or `"xml"` — `column_matching.type_family`'s own
    coarse grouping, which already folds `json`/`jsonb` together (design §5.4
    profiles both identically; Postgres's `json_object_keys`-equivalent
    reasoning is the same for text-stored `json` as for binary `jsonb`).
    """
    found: list[tuple[str, str, str, str]] = []
    for schema in schema_info.get("schemas", []) or []:
        schema_name = schema.get("name", "")
        for table in schema.get("tables", []) or []:
            table_name = table.get("name", "")
            for column in table.get("columns", []) or []:
                column_name = column.get("name", "")
                if not column_name:
                    continue
                column_type = (
                    column.get("data_type") or column.get("type") or column.get("base_type") or ""
                )
                family = type_family(column_type)
                if family in NESTED_TYPE_FAMILIES:
                    found.append((schema_name, table_name, column_name, family))
    return found


def _measure_annotation(
    column_path: str, family: str, provenance: SampleProvenance, established: bool,
) -> ResourceMeasureAnnotation:
    """Design §5.8's envelope requirement, restated for this step: every
    nested-schema claim must state its sample basis, exactly like slice 10's
    `data_class_match`/`reference_data_match`.
    """
    return ResourceMeasureAnnotation(
        summary=f"Nested-column sample for {column_path} ({family}): {provenance.describe()}",
        analysis_step=ANALYSIS_STEP,
        annotation_type_name="NestedColumnSample",
        confidence=100 if established else 0,
        check_name="nested_column_sample",
        item_key=column_path,
        label="sampled" if provenance.reads_values else "not_sampled",
        resource_properties={
            "column": column_path,
            "column_family": family,
            **{k: v for k, v in provenance.as_dict().items() if v is not None},
        },
        json_properties={"sample": provenance.as_dict()},
        explanation=(
            "Which sampling strategy actually ran, with the sample size, the "
            "total row count and the seed — design §5.8 requires every "
            "sampled answer to carry this, including a nested-schema "
            "inference over JSON/XML values."
        ),
    )


def _schema_annotation(
    column_path: str,
    schema_name: str,
    family: str,
    label: str,
    schema_dict: dict[str, Any],
    provenance: SampleProvenance,
) -> SchemaAnalysisAnnotation:
    """The §5.4-required `SchemaAnalysisAnnotation` carrying the inferred
    nested schema, published for EVERY established column — including
    `scalar_only`/`unparseable`/`mixed`, whose `schema_dict` legitimately
    carries no keys/names. Omitting the annotation for those cases would
    recreate exactly the defect this slice's absence discipline exists to
    avoid: a column nobody could profile would look identical to one that was
    profiled and found to hold no nested structure.
    """
    explanation = {
        LABEL_STRUCTURED: (
            "Inferred from the sampled values: key/name presence fraction, "
            "observed JSON types and consistency, and nesting depth."
        ),
        LABEL_SCALAR_ONLY: (
            "Every sampled value was a JSON scalar, not an object or array — "
            "this column is typed for nested data but the sample held none. "
            "A real finding, not an absence: it may mean the column is "
            "misclassified as needing nested profiling."
        ),
        LABEL_MIXED: (
            "Some sampled values were scalars and some were structured — "
            "inconsistent use of this column across rows."
        ),
        LABEL_UNPARSEABLE: (
            "None of the sampled values parsed as well-formed XML — this "
            "column is typed xml but the sample held content that is not "
            "actually XML."
        ),
    }.get(label, "")
    return SchemaAnalysisAnnotation(
        summary=(
            f"{column_path} ({family}): {label}, {provenance.describe()}"
        ),
        analysis_step=ANALYSIS_STEP,
        annotation_type_name="NestedSchemaProfile",
        confidence=100,
        check_name="nested_column_profile",
        item_key=column_path,
        label=label,
        schema_name=schema_name,
        schema_type=family,
        explanation=explanation,
        json_properties={"schema": schema_dict, "sample": provenance.as_dict()},
    )


def run_nested_columns(
    conn,
    capabilities,
    schema_info: dict,
    stats_info: dict,
    *,
    resource_slug: str = "",
    sampling_overrides: dict[str, Any] | None = None,
    run_overrides: dict[str, Any] | None = None,
    scope_settings: dict[str, dict] | None = None,
    max_columns: int | None = None,
) -> dict:
    """`postgres_nested_columns`: sample every JSON/JSONB/XML column, then
    infer its nested schema.

    Mirrors `column_profile_step.run_column_profile`'s shape deliberately —
    same config resolution, same budget object, same per-column loop — so a
    reader of one recognises the other. Returns `annotations` plus a
    `nested_columns` summary dict (config, budget spent, per-column results)
    for a caller that wants the raw shape rather than only the annotations.
    """
    config = resolve_sampling_config(
        purpose="matching",
        resource_slug=resource_slug,
        # The stored scopes (sampling.scoped_settings), then the caller's own override as the RUN scope:
        # `sampling_overrides` is what a request or a caller passed for this run, never a global setting.
        **(scope_settings or {}),
        run_settings={**(sampling_overrides or {}), **(run_overrides or {})} or None,
    )
    budget = SamplingBudget(config)
    annotations: list = []
    columns_result: list[dict[str, Any]] = []

    supports_sampling = bool(getattr(capabilities, "value_sampling", False))
    if not supports_sampling:
        annotations.append(
            ResourceMeasureAnnotation(
                summary="Value sampling not supported by this engine",
                analysis_step=ANALYSIS_STEP,
                confidence=0,
                check_name="nested_column_sample",
                item_key="capability",
                label="unverified",
                resource_properties={"capability": "value_sampling", "supported": False},
                explanation=(
                    "This connection's engine capability declaration does not "
                    "include value_sampling, so nested_column_profile is not "
                    "established for any JSON/XML column — not a measurement "
                    "that those columns hold no nested structure."
                ),
            )
        )

    row_counts = _row_counts_by_table(stats_info)
    columns_seen = 0

    for schema_name, table_name, column_name, family in _iter_nested_columns(schema_info):
        if max_columns is not None and columns_seen >= max_columns:
            break
        columns_seen += 1
        column_path = f"{schema_name}.{table_name}.{column_name}"
        total_rows = row_counts.get((schema_name, table_name))

        if not supports_sampling:
            # `not_sampled_provenance` (not a hand-rolled SampleProvenance):
            # it normalises the RECORDED strategy to catalog_stats_only
            # whenever nothing was actually read, regardless of what the
            # configured strategy was — so `describe()` never implies a
            # sample was attempted when the capability was never there to
            # attempt one with.
            values, provenance = None, not_sampled_provenance(
                config,
                "this connection's engine declares no value_sampling "
                "capability",
            )
        else:
            values, provenance = sample_column_values(
                conn, schema_name, table_name, column_name,
                config, total_rows, budget,
            )

        if values is None:
            state = STATE_NOT_SUPPORTED if not supports_sampling else STATE_NOT_COLLECTED
            annotations.append(_measure_annotation(column_path, family, provenance, established=False))
            annotations.append(
                _schema_annotation(
                    column_path, schema_name, family, LABEL_EMPTY,
                    {"not_established_reason": provenance.reason_not_sampled or "no values were sampled"},
                    provenance,
                )
            )
            columns_result.append({
                "column": column_path, "family": family, "state": state,
                "label": LABEL_EMPTY, "schema": None,
            })
            continue

        if family == "json":
            parsed = [_parse_json_value(v) for v in values if v is not None]
            schema = infer_json_schema(parsed)
            label = classify_json_sample(schema)
        else:
            non_null = [v for v in values if v is not None]
            schema = infer_xml_schema(non_null)
            label = classify_xml_sample(schema)

        state = _LABEL_TO_STATE.get(label, STATE_MEASURED)
        annotations.append(_measure_annotation(column_path, family, provenance, established=True))
        annotations.append(
            _schema_annotation(
                column_path, schema_name, family, label, schema.as_dict(), provenance,
            )
        )
        columns_result.append({
            "column": column_path, "family": family, "state": state,
            "label": label, "schema": schema.as_dict(),
        })

    return {
        "annotations": annotations,
        "nested_columns": {
            "config": config.as_dict(),
            "budget": budget.as_dict(),
            "value_sampling_supported": supports_sampling,
            "columns": columns_result,
        },
    }
