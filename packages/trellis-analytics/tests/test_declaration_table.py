"""B1: the member-scoped table result (DESIGN-B1-MEMBER-SCOPED-TABLE-RESULT-2026-11-02 §2-3). What a table column and
a table accept and refuse, the owner's 1,000-row cap, the extent a table derives, and what `missing()` names."""
import pytest
from pydantic import ValidationError
from trellis_analytics.declaration import (
    ABSENCE_STATES,
    TABLE_MAX_ROWS,
    TOKENS_PER_CELL,
    Declaration,
    TableColumn,
    TableDeclaration,
    refusal,
)

ABSENCE = {s: f"{s.replace('_', ' ')}: a sentence" for s in ABSENCE_STATES}


def measure(name, unit="columns"):
    return {"name": name, "unit": unit, "as_of": "run.surveyed_at", "value_field": name, "scope": "resource"}


TABLE = {
    "key_fields": ["schema", "table", "column"],
    "columns": ["schema", "table", "column",
                {"name": "state", "type": "state", "absence": "not profiled",
                 "states": ["measured", "not_measured", "not_permitted", "measured_nothing"]},
                {"name": "declared_type", "absence": "type not recorded"},
                {"name": "null_count", "type": "integer", "absence": "not counted", "measure_ref": "nulls_total"},
                {"name": "distinct_estimate", "type": "integer", "absence": "not estimated"}],
    "order": "schema, table, column as the catalog lists them",
    "truncation": "the first {shown} of {total} columns; every column is stored and the summary counts them all",
    "summary": ["columns_total", "columns_measured", "nulls_total"],
}


def member_table(table=None, **over):
    return {"result_kind": "table", "result_kind_note": "one row per column", "scope": "member",
            "measures": [measure("columns_total"), measure("columns_measured"), measure("nulls_total", "nulls")],
            "absence": ABSENCE, "confidence": {"form": "measured"}, "answers": [],
            "table": TABLE if table is None else table, **over}


def refused(body) -> str:
    with pytest.raises(ValidationError) as exc:
        Declaration.model_validate(body)
    return refusal(exc.value)


# ── a column ────────────────────────────────────────────────────────────────────────────────────────────────────

def test_a_bare_string_column_is_text():
    t = TableDeclaration.model_validate({"columns": ["a", {"name": "b", "type": "integer", "absence": "none"}]})
    assert [(c.name, c.type) for c in t.columns] == [("a", "text"), ("b", "integer")]


@pytest.mark.parametrize("body, said", [
    ({"name": "x", "type": "float"}, "type 'float' is not one of the column types"),
    ({"name": "x", "type": "bytes"}, "is a bytes column with no unit"),
    ({"name": "x", "type": "number"}, "is a number column with no unit"),
    ({"name": "x", "type": "state"}, "is a state column and declares no states"),
    ({"name": "x", "states": ["a"]}, "states belong to a state column"),
    ({"name": "x", "type": "state", "states": ["a", "a"]}, "declares a state more than once"),
    ({"name": " "}, "a table column declares no name"),
    ({"name": "x", "colour": "red"}, "is not a declaration field"),
])
def test_a_column_refuses_with_the_sentence(body, said):
    with pytest.raises(ValidationError) as exc:
        TableColumn.model_validate(body)
    assert said in refusal(exc.value)


def test_a_unit_column_with_its_unit_validates():
    assert TableColumn(name="size", type="bytes", unit="bytes", absence="not read").unit == "bytes"


# ── a table ─────────────────────────────────────────────────────────────────────────────────────────────────────

def test_the_cap_is_the_owners_and_may_only_be_lowered():
    assert TABLE_MAX_ROWS == 1000
    assert TableDeclaration.model_validate({"columns": ["a"]}).max_rows == 1000
    assert TableDeclaration.model_validate({"columns": ["a"], "max_rows": 50}).max_rows == 50
    with pytest.raises(ValidationError) as exc:
        TableDeclaration.model_validate({"columns": ["a"], "max_rows": 1001})
    assert "a table shows at most 1,000 rows (owner's cap, 2026-11-02)" in refusal(exc.value)
    with pytest.raises(ValidationError):
        TableDeclaration.model_validate({"columns": ["a"], "max_rows": 0})


def test_a_table_refuses_a_column_twice_and_a_truncation_without_its_placeholders():
    with pytest.raises(ValidationError) as exc:
        TableDeclaration.model_validate({"columns": ["a", {"name": "a"}]})
    assert "declares column a more than once" in refusal(exc.value)
    with pytest.raises(ValidationError) as exc:
        TableDeclaration.model_validate({"columns": ["a"], "truncation": "some of them"})
    assert "lacks {shown}, {total}" in refusal(exc.value)


# ── a table in a declaration ────────────────────────────────────────────────────────────────────────────────────

def test_the_member_table_declaration_validates_and_is_complete():
    d = Declaration.model_validate(member_table())
    assert d.missing() == [] and d.is_complete()
    assert d.table.fields() == ["schema", "table", "column", "state", "declared_type", "null_count",
                                "distinct_estimate"]
    assert d.table.column("null_count").measure_ref == "nulls_total"


def test_a_tables_extent_is_derived_from_the_table():
    d = Declaration.model_validate(member_table())
    [e] = d.extents
    assert e.name == "rows" and e.rows == 1000 and e.fields == d.table.fields()
    assert e.order == TABLE["order"] and e.truncation == TABLE["truncation"]
    assert e.token_cost == 1000 * 7 * TOKENS_PER_CELL
    lowered = Declaration.model_validate(member_table({**TABLE, "max_rows": 20}))
    assert lowered.extents[0].rows == 20


def test_a_table_that_also_writes_extents_is_refused():
    extent = {"name": "x", "fields": ["schema"], "rows": 5, "order": "by schema",
              "truncation": "{shown} of {total}", "token_cost": 10, "citation": "c"}
    assert "a table's extent is its table" in refused(member_table(extents=[extent]))


def test_the_derived_extent_survives_a_round_trip():
    """The generator copies a declaration as data, so its own dump must validate again."""
    d = Declaration.model_validate(member_table())
    assert Declaration.model_validate(d.model_dump()) == d


def test_a_member_table_without_a_summary_is_refused():
    assert "a member-scoped table declares no summary" in refused(member_table({**TABLE, "summary": []}))


def test_a_summary_or_measure_ref_names_a_declared_measure():
    assert "summary names rows_total, which is not a declared measure" in refused(
        member_table({**TABLE, "summary": ["rows_total"]}))
    cols = [c if not (isinstance(c, dict) and c["name"] == "null_count") else {**c, "measure_ref": "nope"}
            for c in TABLE["columns"]]
    assert "measure_ref names no declared measure (null_count -> nope)" in refused(
        member_table({**TABLE, "columns": cols}))


@pytest.mark.parametrize("scope", ["member", "field"])
def test_a_keyed_table_needs_its_key_its_absences_and_its_order(scope):
    over = {"scope": scope}
    assert f"a {scope}-scoped table declares no key_fields" in refused(member_table({**TABLE, "key_fields": []}, **over))
    bare = {**TABLE, "columns": TABLE["columns"] + ["comment"]}
    assert "declares no absence word for column comment" in refused(member_table(bare, **over))
    assert "declares no order" in refused(member_table({**TABLE, "order": ""}, **over))


def test_a_member_table_needs_its_truncation():
    assert "a member-scoped table declares no truncation" in refused(member_table({**TABLE, "truncation": ""}))


def test_a_field_scoped_table_needs_no_summary():
    assert Declaration.model_validate(member_table({**TABLE, "summary": []}, scope="field")).missing() == []


def test_missing_names_each_absence_of_an_unscoped_table_result():
    d = Declaration.model_validate({"result_kind": "table", "result_kind_note": "none fits", "absence": ABSENCE,
                                    "table": {"columns": ["a", "b", "c"], "key_fields": ["a"],
                                              "truncation": "{shown} of {total} rows"}})
    assert d.extents == []
    assert d.missing() == ["extents", "confidence", "answers", "scope", "table.columns.b.absence",
                           "table.columns.c.absence", "table.order"]


EXTENT = {"name": "status", "fields": ["a"], "rows": 1, "order": "as listed", "truncation": "{shown} of {total}",
          "token_cost": 10, "citation": "c"}


def test_the_earlier_table_shape_keeps_its_hand_written_extents():
    """The catalogue's first tables (no truncation) predate B1 and convert as touched (owner, 2026-10-31)."""
    d = Declaration.model_validate({"result_kind": "table", "result_kind_note": "none fits", "absence": ABSENCE,
                                    "scope": "field", "extents": [EXTENT],
                                    "table": {"columns": ["a", "b"], "key_fields": ["a"]}})
    assert [e.name for e in d.extents] == ["status"] and not d.table.is_result()
    assert "table.columns.b.absence" not in d.missing()
