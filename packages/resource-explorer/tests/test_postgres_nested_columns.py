"""Phase 1 slice 11 — `postgres_nested_columns`.

Gated on slice 10 ("shares the inference core"): this step reuses
`column_profile_step.sample_column_values`/`SamplingBudget` and
`sampling.resolve_sampling_config` unchanged — no second sampling
implementation exists here — and hands the raw sampled values to
`nested_schema_inference.py` (tested on its own in
`test_nested_schema_inference.py`).

Covers, per the task brief:

1. every absence state — capability absent, no values sampled, sampled-empty
   — renders as not-established, never as "no nested structure";
2. a column where every sampled value is a JSON scalar is a distinct,
   established finding (`scalar_only`), not folded into "no match"/absence;
3. a `SchemaAnalysisAnnotation` carries the inferred schema, for every state
   including the no-schema ones;
4. the §5.8 sample-provenance envelope is present on every emitted claim —
   both the `ResourceMeasureAnnotation` and the `SchemaAnalysisAnnotation`;
5. non-JSON/XML columns are never iterated by this step at all.
"""
from __future__ import annotations

import re

from resource_explorer.registry import (
    STATE_EMPTY,
    STATE_MEASURED,
    STATE_NOT_COLLECTED,
    STATE_NOT_SUPPORTED,
)
from resource_explorer.surveyors.database.connection import EngineCapabilities
from resource_explorer.surveyors.database.nested_columns_step import (
    NESTED_TYPE_FAMILIES,
    _iter_nested_columns,
    run_nested_columns,
)
from resource_explorer.surveyors.nested_schema_inference import (
    LABEL_EMPTY,
    LABEL_MIXED,
    LABEL_SCALAR_ONLY,
    LABEL_STRUCTURED,
    LABEL_UNPARSEABLE,
)
from resource_explorer.surveyors.survey_report import (
    ResourceMeasureAnnotation,
    SchemaAnalysisAnnotation,
)


class _FakeSamplingConnection:
    """Same duck-typed shape as `test_postgres_column_profile.py`'s fake:
    records the SQL, replays canned values per column, and exposes
    `.capabilities`. Kept local to this file rather than imported cross-file,
    matching the tests' independence from slice 10's own test module.
    """

    def __init__(self, values_by_column=None, capabilities=None):
        self.values_by_column = values_by_column or {}
        self._capabilities = capabilities or EngineCapabilities(
            column_stats=True, tuple_counters=True, value_sampling=True,
        )
        self.executed: list[str] = []

    @property
    def capabilities(self):
        return self._capabilities

    def execute_query(self, query, params=()):
        self.executed.append(query)
        column = _column_from_sql(query)
        values = self.values_by_column.get(column, [])
        return [{"value": v} for v in values]

    def execute_query_isolated(self, query, params=()):
        """§F: `column_profile_step.py` calls this instead of `execute_query`
        now; proxied straight through since this fake has no transaction to
        protect."""
        return self.execute_query(query, params)


def _column_from_sql(query: str) -> str:
    match = re.search(r'SELECT "([^"]+)" AS value', query)
    return match.group(1) if match else ""


_SCHEMA_INFO = {
    "schemas": [{
        "name": "public",
        "tables": [{
            "name": "events",
            "columns": [
                {"name": "payload", "data_type": "jsonb"},
                {"name": "metadata", "data_type": "xml"},
                {"name": "id", "data_type": "integer"},
                {"name": "name", "data_type": "text"},
            ],
        }],
    }],
}

_STATS_INFO = {"row_stats": [
    {"schemaname": "public", "tablename": "events", "row_count": 100_000},
]}


def _run(values_by_column, capabilities=None, **kwargs):
    conn = _FakeSamplingConnection(values_by_column, capabilities=capabilities)
    result = run_nested_columns(
        conn, capabilities or conn.capabilities, _SCHEMA_INFO, _STATS_INFO,
        resource_slug="coco_ods", **kwargs,
    )
    return conn, result


class TestIterNestedColumns:
    def test_only_json_and_xml_columns_are_found(self):
        found = _iter_nested_columns(_SCHEMA_INFO)
        names = {c for _, _, c, _ in found}
        assert names == {"payload", "metadata"}

    def test_family_is_reported_per_column(self):
        found = {c: f for _, _, c, f in _iter_nested_columns(_SCHEMA_INFO)}
        assert found["payload"] == "json"
        assert found["metadata"] == "xml"

    def test_json_and_xml_are_the_only_families(self):
        assert NESTED_TYPE_FAMILIES == {"json", "xml"}


class TestAbsenceStates:
    def test_capability_absent_is_not_supported_never_no_structure(self):
        _, result = _run(
            {}, capabilities=EngineCapabilities(value_sampling=False),
        )
        columns = {c["column"].split(".")[-1]: c for c in result["nested_columns"]["columns"]}
        assert columns["payload"]["state"] == STATE_NOT_SUPPORTED
        assert columns["payload"]["schema"] is None
        # And the capability gap itself is a confidence-0 annotation.
        capability_anns = [
            a for a in result["annotations"]
            if isinstance(a, ResourceMeasureAnnotation) and a.item_key == "capability"
        ]
        assert capability_anns and capability_anns[0].confidence == 0

    def test_no_value_sampled_is_not_collected(self):
        """catalog_stats_only or an exhausted budget both mean `values is
        None` from `sample_column_values` — this step must render that as
        STATE_NOT_COLLECTED, not as an empty/scalar_only schema."""
        _, result = _run({}, run_overrides={"strategy": "catalog_stats_only"})
        columns = {c["column"].split(".")[-1]: c for c in result["nested_columns"]["columns"]}
        assert columns["payload"]["state"] == STATE_NOT_COLLECTED
        assert columns["payload"]["label"] == LABEL_EMPTY
        assert columns["payload"]["schema"] is None

    def test_sample_ran_but_all_null_is_state_empty(self):
        _, result = _run({"payload": [], "metadata": []})
        columns = {c["column"].split(".")[-1]: c for c in result["nested_columns"]["columns"]}
        assert columns["payload"]["state"] == STATE_EMPTY
        assert columns["payload"]["label"] == LABEL_EMPTY

    def test_every_column_still_gets_a_schema_analysis_annotation(self):
        """The absence-discipline invariant this whole slice is about: a
        column nobody could profile must not be silently missing from the
        output — it gets an annotation saying so."""
        _, result = _run({}, capabilities=EngineCapabilities(value_sampling=False))
        schema_anns = [a for a in result["annotations"] if isinstance(a, SchemaAnalysisAnnotation)]
        item_keys = {a.item_key for a in schema_anns}
        assert any(k.endswith("payload") for k in item_keys)
        assert any(k.endswith("metadata") for k in item_keys)


class TestScalarOnlyIsARealFinding:
    def test_a_jsonb_column_of_scalars_is_scalar_only_not_absence(self):
        _, result = _run({"payload": ["1", "2", "3"]})
        columns = {c["column"].split(".")[-1]: c for c in result["nested_columns"]["columns"]}
        payload = columns["payload"]
        assert payload["state"] == STATE_MEASURED  # a real measurement...
        assert payload["label"] == LABEL_SCALAR_ONLY  # ...with this finding
        assert payload["schema"]["scalar_count"] == 3
        assert payload["schema"]["document_count"] == 0

    def test_a_mix_of_scalars_and_objects_is_mixed(self):
        _, result = _run({"payload": ['{"a": 1}', "42", '{"a": 2}']})
        columns = {c["column"].split(".")[-1]: c for c in result["nested_columns"]["columns"]}
        assert columns["payload"]["label"] == LABEL_MIXED

    def test_structured_jsonb_produces_a_populated_schema(self):
        _, result = _run({"payload": ['{"id": 1, "tags": ["a"]}', '{"id": 2}']})
        columns = {c["column"].split(".")[-1]: c for c in result["nested_columns"]["columns"]}
        payload = columns["payload"]
        assert payload["state"] == STATE_MEASURED
        assert payload["label"] == LABEL_STRUCTURED
        paths = {k["path"] for k in payload["schema"]["keys"]}
        assert "id" in paths
        by_path = {k["path"]: k for k in payload["schema"]["keys"]}
        assert by_path["id"]["presence_fraction"] == 1.0
        assert by_path["tags"]["presence_fraction"] == 0.5

    def test_already_parsed_dict_values_are_accepted_directly(self):
        """A driver that already deserialises jsonb to Python objects must
        work identically to one handing back text."""
        _, result = _run({"payload": [{"id": 1}, {"id": 2}]})
        columns = {c["column"].split(".")[-1]: c for c in result["nested_columns"]["columns"]}
        assert columns["payload"]["label"] == LABEL_STRUCTURED


class TestXmlColumn:
    def test_structured_xml_produces_root_and_names(self):
        docs = ["<order id='1'><item/></order>", "<order id='2'><item/></order>"]
        _, result = _run({"metadata": docs})
        columns = {c["column"].split(".")[-1]: c for c in result["nested_columns"]["columns"]}
        metadata = columns["metadata"]
        assert metadata["label"] == LABEL_STRUCTURED
        assert metadata["schema"]["dominant_root_element"] == "order"
        names = {n["name"] for n in metadata["schema"]["names"]}
        assert "item" in names

    def test_unparseable_xml_is_its_own_label(self):
        _, result = _run({"metadata": ["not xml", "still not xml"]})
        columns = {c["column"].split(".")[-1]: c for c in result["nested_columns"]["columns"]}
        assert columns["metadata"]["label"] == LABEL_UNPARSEABLE
        assert columns["metadata"]["state"] == STATE_MEASURED  # it WAS sampled


class TestSampleProvenanceEnvelope:
    """§5.8's requirement, restated for this step: every nested-schema claim
    states its sample basis. Checked on BOTH annotation kinds this step
    emits."""

    def test_measure_annotation_carries_the_envelope(self):
        _, result = _run({"payload": ['{"id": 1}']})
        measures = [
            a for a in result["annotations"]
            if isinstance(a, ResourceMeasureAnnotation) and a.item_key.endswith("payload")
        ]
        assert measures
        assert "sample" in measures[0].json_properties
        assert measures[0].json_properties["sample"]["envelope"]

    def test_schema_annotation_carries_the_envelope_too(self):
        _, result = _run({"payload": ['{"id": 1}']})
        schema_anns = [
            a for a in result["annotations"]
            if isinstance(a, SchemaAnalysisAnnotation) and a.item_key.endswith("payload")
        ]
        assert schema_anns
        assert "sample" in schema_anns[0].json_properties
        assert schema_anns[0].json_properties["sample"]["envelope"]

    def test_envelope_present_even_for_a_not_established_column(self):
        _, result = _run({}, capabilities=EngineCapabilities(value_sampling=False))
        schema_anns = [
            a for a in result["annotations"]
            if isinstance(a, SchemaAnalysisAnnotation) and a.item_key.endswith("payload")
        ]
        assert schema_anns
        assert "sample" in schema_anns[0].json_properties

    def test_schema_annotation_names_schema_and_type(self):
        _, result = _run({"payload": ['{"id": 1}']})
        schema_anns = [
            a for a in result["annotations"]
            if isinstance(a, SchemaAnalysisAnnotation) and a.item_key.endswith("payload")
        ]
        assert schema_anns[0].schema_name == "public"
        assert schema_anns[0].schema_type == "json"


class TestSharesSlice10Sampling:
    def test_a_tablesample_query_is_actually_issued(self):
        """Proves this step reuses slice 10's SQL machinery rather than a
        parallel implementation — the same TABLESAMPLE shape must appear."""
        conn, _ = _run({"payload": ['{"id": 1}']})
        assert any("TABLESAMPLE" in q for q in conn.executed)

    def test_max_columns_bounds_the_loop(self):
        _, result = _run(
            {"payload": ['{"id": 1}'], "metadata": ["<a/>"]}, max_columns=1,
        )
        assert len(result["nested_columns"]["columns"]) == 1
