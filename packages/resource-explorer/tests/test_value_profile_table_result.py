"""B1, commit 3: value_profile is the first table result. Its declaration validates and is complete; the step stores
every column as a typed row; the Value profile screen reads the typed table and says exactly what the native read said
(the fixture run's equivalence, the twin of evidence/B1-VALUE-PROFILE-EQUIVALENCE); the served summary names the
truncation sentence; and "the numbers behind this" carries the summary."""
from __future__ import annotations

import pytest

from resource_explorer import table_result
from resource_explorer import value_profile_screen as screen
from resource_explorer.declared_context import declarations
from resource_explorer.registry import DatabaseEntity, ProjectRegistry
from resource_explorer.surveyors.database import survey_definition_adapter as sda
from resource_explorer.surveyors.database.quality import value_profile as vp
from tests.test_value_profile_screen import AT, FREQUENT, PROFILE, PROPOSALS, SLUG, _col

#: Twelve columns: the screen test's ten, and two more on a table of their own.
TWELVE = PROFILE + [
    _col("refunds", "id", "N", record_ct=2, value_ct=2, distinct_value_ct=2, null_value_ct=0, min_value=1.0,
         max_value=2.0, avg_value=1.5, stdev_value=0.7071, sum_value="3", fractional_sum="0"),
    _col("refunds", "note", "A", record_ct=2, value_ct=1, distinct_value_ct=1, null_value_ct=1, zero_length_ct=0,
         min_length=3, max_length=3, avg_length=3.0, frequency_state="read"),
]


def _registry(tmp_path, typed=True) -> ProjectRegistry:
    reg = ProjectRegistry(db_path=str(tmp_path / "vp.db"))
    reg.register_database(DatabaseEntity(slug=SLUG, display_name="Coco ODS", db_type="postgresql",
                                         host="localhost", port=5442, database_name="coco_ods"))
    reg.write_detail_rows("database_value_profiles", SLUG, AT, rows=TWELVE)
    reg.write_detail_rows("database_value_profile_frequent_values", SLUG, AT, rows=FREQUENT)
    reg.write_detail_rows("database_quality_proposals", SLUG, AT, rows=PROPOSALS)
    if typed:
        assert vp.write_typed_rows(reg, SLUG, AT) == 12
    return reg


def test_the_declaration_is_a_complete_table_result():
    dec = declarations("database")["value_profile"]
    assert dec.result_kind == "table" and dec.table.is_result() and dec.missing() == []
    assert dec.table.key_fields == ["schema", "table", "column"] and dec.table.max_rows == 1000
    assert dec.table.summary == ["profiled_columns", "columns"]
    assert [e.name for e in dec.extents] == ["rows"] and dec.extents[0].rows == 1000
    # Nothing the tab shows is lost: every measure the native read hands the screen is a declared column.
    shown = {k for c in sda._value_profile_native_results(_registry_stub(), SLUG)["columns"] for k in c} - {
        "column_path", "schema_name", "table_name", "column_name"}
    assert shown <= set(dec.table.column_names())


def _registry_stub():
    class R:
        def query_detail_rows(self, table, slug, **kw):
            return [{**r, "surveyed_at": AT} for r in TWELVE] if table == "database_value_profiles" else []
    return R()


def test_twelve_columns_round_trip_typed_equals_native(tmp_path):
    reg = _registry(tmp_path)
    native = sda._value_profile_native_results(reg, SLUG)
    typed = sda._value_profile_results(reg, SLUG)
    assert sda._value_profile_typed_rows(reg, SLUG), "the screen must be reading the typed table"
    assert typed == native
    assert typed["column_count"] == 12 and typed["measured_count"] == 9


def test_the_screen_says_the_same_thing_from_either_read(tmp_path, monkeypatch):
    reg = _registry(tmp_path)
    typed_summary = screen.build_summary(reg, SLUG)
    typed_tables = {f"{t['schema_name']}.{t['table_name']}": screen.build_table(reg, SLUG, t["schema_name"],
                                                                               t["table_name"])
                    for t in typed_summary["tables"]}
    monkeypatch.setattr(sda, "_value_profile_typed_rows", lambda *_: [])
    native_summary = screen.build_summary(reg, SLUG)
    assert {k: v for k, v in typed_summary.items() if k != "table_result"} == \
        {k: v for k, v in native_summary.items() if k != "table_result"}
    for key, table in typed_tables.items():
        schema, name = key.split(".", 1)
        assert table == screen.build_table(reg, SLUG, schema, name)

    # The Questions row's words (the Q-0088 family reads this headline) are the same from either read.
    monkeypatch.undo()
    typed_line = sda._value_profile_headline(reg, SLUG)["label"]
    monkeypatch.setattr(sda, "_value_profile_typed_rows", lambda *_: [])
    assert typed_line == sda._value_profile_headline(reg, SLUG)["label"]
    assert typed_line.startswith("9 of 12 columns profiled")


def test_the_served_summary_names_the_truncation_sentence_and_the_numbers_behind(tmp_path):
    block = screen.build_summary(_registry(tmp_path), SLUG)["table_result"]
    assert block["table"]["truncation"] == ("the first 12 of 12 columns; every column is stored and the summary counts "
                                            "them all")
    assert block["band"] == table_result.ROWS_STAY
    assert [m["words"] for m in block["measures"]] == ["9 of 12 columns", "12 columns"]
    first = block["table"]["rows"][0]
    assert (first["schema"], first["table"], first["column"]) == ("sales", "big", "amount")
    assert first["record_ct"] == "not counted" and "record_ct" in first["absent"]


def test_a_profile_stored_before_the_conversion_still_reads_natively(tmp_path):
    reg = _registry(tmp_path, typed=False)
    assert sda._value_profile_results(reg, SLUG)["column_count"] == 12
    assert screen.build_summary(reg, SLUG)["table_result"] is None


def test_the_cap_shows_a_thousand_and_stores_all(tmp_path):
    reg = ProjectRegistry(db_path=str(tmp_path / "big.db"))
    reg.register_database(DatabaseEntity(slug=SLUG, display_name="Coco ODS", db_type="postgresql",
                                         host="localhost", port=5442, database_name="coco_ods"))
    rows = [_col(f"t{i // 50:03d}", f"c{i:04d}", "N", record_ct=1, value_ct=1, distinct_value_ct=1, null_value_ct=0)
            for i in range(1916)]
    reg.write_detail_rows("database_value_profiles", SLUG, AT, rows=rows)
    assert vp.write_typed_rows(reg, SLUG, AT) == 1916
    dec = declarations("database")["value_profile"]
    got = table_result.table_rows(reg, "database", SLUG, "value_profile", dec)
    assert (got["shown"], got["total"]) == (1000, 1916)
    assert got["truncation"].startswith("the first 1,000 of 1,916 columns")
    assert sda._value_profile_results(reg, SLUG)["column_count"] == 1916


@pytest.mark.parametrize("key", ["filled", "distinct", "range", "common", "flags", "checks"])
def test_a_measured_columns_cells_are_unchanged(tmp_path, monkeypatch, key):
    reg = _registry(tmp_path)
    typed = screen.build_table(reg, SLUG, "sales", "orders")
    monkeypatch.setattr(sda, "_value_profile_typed_rows", lambda *_: [])
    native = screen.build_table(reg, SLUG, "sales", "orders")
    assert [c.get("cells", {}).get(key) for c in typed["columns"]] == [c.get("cells", {}).get(key)
                                                                      for c in native["columns"]]
