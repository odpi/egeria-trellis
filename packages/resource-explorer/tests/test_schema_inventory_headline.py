"""`schema_inventory` gets a headline reader (2026-09-26 follow-up to the
enumeration-floor PR): "How big is this database — schemas, tables, views,
columns, rows and bytes?" names schemas first, but `_schema_inventory_results`
had no schema-level field at all, so `scalarMeasures()`'s fallback rendered
"table count 56 · column count 427 ..." with no schema mentioned anywhere.

The headline deliberately NAMES the schemas (not just a count): a bare
count would satisfy `_check_level`'s sub-resource gate for "Which schemas
carry the data...?" (`container` level, `schema_inventory` alone) without
actually naming a single schema — reopening the exact gap slice 17b closed,
one level up. See `test_slice17_question_level_gate.py`'s
`TestRealCatalogAgreesWithTheSlice17bLiveBugReport` for the level-gate
side of this.
"""
from __future__ import annotations

from resource_explorer.surveyors.database.survey_definition_adapter import (
    _schema_inventory_headline,
)


class _FakeRegistry:
    def __init__(self, tables, columns, survey_data=None):
        self._tables = tables
        self._columns = columns
        self._survey_data = survey_data

    def query_detail_rows(self, table, slug):
        if table == "database_tables":
            return list(self._tables)
        if table == "database_columns":
            return list(self._columns)
        return []

    def get_latest_database_survey(self, slug):
        if self._survey_data is None:
            return None
        import json
        return {"survey_data": json.dumps(self._survey_data)}

    def get_database_surveys(self, slug):
        # `_credential_capability_results` now searches every stored survey
        # (newest first), not just the latest one — see its own docstring.
        if self._survey_data is None:
            return []
        import json
        return [{"survey_data": json.dumps(self._survey_data)}]


def _table(schema, name, table_type="BASE TABLE"):
    return {"schema_name": schema, "table_name": name, "table_type": table_type,
            "state": "measured", "row_count": None}


class TestSchemaInventoryHeadlineNamesSchemas:
    def test_a_small_number_of_schemas_are_named_in_full(self):
        registry = _FakeRegistry(
            tables=[_table("public", "a"), _table("audit", "b"), _table("staging", "c")],
            columns=[],
        )
        result = _schema_inventory_headline(registry, "mydb")
        assert result is not None
        label = result["label"]
        assert "3 schema(s)" in label
        assert "public" in label and "audit" in label and "staging" in label

    def test_many_schemas_collapse_to_a_bare_count(self):
        tables = [_table(f"schema_{i}", "t") for i in range(20)]
        registry = _FakeRegistry(tables=tables, columns=[])
        result = _schema_inventory_headline(registry, "mydb")
        label = result["label"]
        assert "20 schema(s)" in label
        assert "schema_0" not in label  # collapsed, not listed

    def test_relation_kinds_are_named_separately(self):
        registry = _FakeRegistry(
            tables=[
                _table("public", "orders", "BASE TABLE"),
                _table("public", "recent_orders", "VIEW"),
                _table("public", "monthly_totals", "MATERIALIZED VIEW"),
                _table("public", "remote_customers", "FOREIGN"),
            ],
            columns=[],
        )
        result = _schema_inventory_headline(registry, "mydb")
        label = result["label"]
        assert "4 relation(s)" in label
        assert "1 base" in label
        assert "1 view" in label
        assert "1 materialized view" in label
        assert "1 foreign" in label

    def test_column_count_is_named(self):
        registry = _FakeRegistry(
            tables=[_table("public", "orders")],
            columns=[
                {"schema_name": "public", "table_name": "orders", "column_name": "id"},
                {"schema_name": "public", "table_name": "orders", "column_name": "total"},
            ],
        )
        result = _schema_inventory_headline(registry, "mydb")
        assert "2 column(s)" in result["label"]

    def test_credential_totals_are_added_when_available(self):
        registry = _FakeRegistry(
            tables=[_table("public", "a")],
            columns=[],
            survey_data={"credential_capability": {
                "schema_total": 8, "schema_visible": 6, "table_total": 61, "table_select": 3,
            }},
        )
        result = _schema_inventory_headline(registry, "mydb")
        assert "6 visible to this credential" in result["label"]

    def test_leading_count_is_schema_total_not_schemas_with_tables(self):
        """Owner's ruling (2026-09-26): the leading number is how many
        schemas EXIST (the credential probe's `schema_total`), not how many
        have a stored table — "we should say 8 schemas if there are, even if
        one has no tables." Only 1 of the 8 schemas produced a table row
        here, so the old wording would have said "1 schema(s)"."""
        registry = _FakeRegistry(
            tables=[_table("public", "a")],
            columns=[],
            survey_data={"credential_capability": {
                "schema_total": 8, "schema_visible": 6, "table_total": 61, "table_select": 3,
            }},
        )
        result = _schema_inventory_headline(registry, "mydb")
        assert result["label"].startswith("8 schema(s), 1 with tables (public), ")

    def test_no_credential_data_omits_the_visibility_clause(self):
        registry = _FakeRegistry(tables=[_table("public", "a")], columns=[])
        result = _schema_inventory_headline(registry, "mydb")
        assert "visible to this credential" not in result["label"]

    def test_credential_totals_from_an_older_survey_still_show_when_the_latest_run_omits_the_probe(self):
        """Found live 2026-09-26, `coco_pharma`: the header's own "sees N of M
        schema(s)" text (`databases.py`'s `_to_summary`) searches every stored
        survey for a `credential_capability` reading, newest first — exactly
        to survive a later schema/statistics-only run that didn't re-run the
        probe. `_credential_capability_results` used to read only the single
        latest survey row, so it saw nothing and this headline's visibility
        clause silently disappeared the moment a later run without the probe
        landed, even though the header kept showing the older reading."""
        class _MultiSurveyRegistry:
            def query_detail_rows(self, table, slug):
                if table == "database_tables":
                    return [_table("public", "a")]
                return []

            def get_database_surveys(self, slug):
                import json
                return [
                    {"survey_data": json.dumps({})},  # newest: no probe this run
                    {"survey_data": json.dumps({"credential_capability": {
                        "schema_total": 8, "schema_visible": 6,
                        "table_total": 61, "table_select": 3,
                    }})},  # older: the probe's last real reading
                ]

        result = _schema_inventory_headline(_MultiSurveyRegistry(), "mydb")
        assert "6 visible to this credential" in result["label"]

    def test_returns_none_when_nothing_measured(self):
        registry = _FakeRegistry(tables=[], columns=[])
        assert _schema_inventory_headline(registry, "mydb") is None
