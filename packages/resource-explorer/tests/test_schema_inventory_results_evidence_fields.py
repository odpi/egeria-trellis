"""`_schema_inventory_results`'s own value dict — the evidence panel's raw
source, as opposed to `_schema_inventory_headline`'s prose sentence
(covered by `test_schema_inventory_headline.py`).

Two owner-reported defects, both from the same live gate (2026-09-26):

1. **Correct-number-wrong-label**: the evidence panel showed "schema count
   7" directly under a headline reading "8 schema(s)" — both numbers were
   individually correct (7 schemas have at least one stored table row; 8
   schemas exist per the credential-capability probe), but showing them
   with the SAME implied meaning ("how many schemas") under one another
   read as a contradiction. Renamed `schema_count` -> `schemas_with_tables`
   and added `schema_total`/`schemas_visible` (the same probe numbers the
   headline and the header banner already use) so the panel states which
   number is which and agrees with the headline.
2. **Doubled fields**: covered by `test_next_evidence_panel_dedup.py`
   (app.js), not this file — `tables`/`table_count` appearing in both this
   reader's own value AND `_row_count_snapshot_results`'s.
"""
from __future__ import annotations

import json

from resource_explorer.surveyors.database.survey_definition_adapter import (
    _schema_inventory_results,
)


class _FakeRegistry:
    def __init__(self, tables, columns=None, survey_data=None):
        self._tables = tables
        self._columns = columns or []
        self._survey_data = survey_data

    def query_detail_rows(self, table, slug):
        if table == "database_tables":
            return list(self._tables)
        if table == "database_columns":
            return list(self._columns)
        return []

    def get_database_surveys(self, slug):
        if self._survey_data is None:
            return []
        return [{"survey_data": json.dumps(self._survey_data)}]


def _table(schema, name, table_type="BASE TABLE"):
    return {"schema_name": schema, "table_name": name, "table_type": table_type,
            "state": "measured", "row_count": None}


class TestSchemaCountRenamedToSchemasWithTables:
    def test_key_is_schemas_with_tables_not_schema_count(self):
        registry = _FakeRegistry(tables=[_table("public", "a"), _table("audit", "b")])
        value = _schema_inventory_results(registry, "mydb")
        assert value["schemas_with_tables"] == 2
        assert "schema_count" not in value

    def test_a_schema_of_only_views_does_not_count_as_with_tables(self):
        """Found live, `adventureworks`, 2026-09-27: this counted any schema
        with a ROW in `database_tables`, including view-only schemas — a
        schema whose relations are entirely views has zero actual tables,
        so it overstated the count (reported 10, truth 5 for AdventureWorks's
        base-table-bearing schemas)."""
        registry = _FakeRegistry(tables=[
            _table("public", "a"),
            _table("hr", "v_employee", table_type="VIEW"),
        ])
        value = _schema_inventory_results(registry, "mydb")
        assert value["schemas_with_tables"] == 1


class TestSchemaTotalAndVisibleFromTheProbe:
    def test_present_when_a_credential_capability_probe_has_run(self):
        registry = _FakeRegistry(
            tables=[_table("public", "a")],
            survey_data={"credential_capability": {
                "schema_total": 8, "schema_visible": 6, "table_total": 61, "table_select": 3,
            }},
        )
        value = _schema_inventory_results(registry, "mydb")
        assert value["schema_total"] == 8
        assert value["schemas_visible"] == 6

    def test_omitted_not_zero_when_no_probe_has_run(self):
        """`0 of 0` would claim "this database has no schemas at all" — a
        stronger, different, and false claim from "not measured yet"."""
        registry = _FakeRegistry(tables=[_table("public", "a")])
        value = _schema_inventory_results(registry, "mydb")
        assert "schema_total" not in value
        assert "schemas_visible" not in value
