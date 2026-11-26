"""TR-125: file_class_match.sample_values sampled only csv/tsv/tab/psv/jsonl/ndjson/parquet heads, so a spreadsheet's
columns were never compared with a data class ("values are not sampled from .xlsx files here"), though file_profiles
already opens them. Now xlsx/xlsm/xls/ods are head-sampled with the same limits (the byte limit decides whether the
workbook is opened at all, since a workbook cannot be cut at a line; at most MAX_VALUES rows are read). json stays
"read whole only". No Egeria.
"""
from __future__ import annotations

import pytest

openpyxl = pytest.importorskip("openpyxl")

from resource_explorer.surveyors.filesystem import file_class_match as fcm  # noqa: E402
from resource_explorer.surveyors.filesystem import file_profiles as fp  # noqa: E402


def _workbook(path, rows, header=("email", "id")):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(list(header))
    for r in rows:
        ws.append(list(r))
    wb.save(path)
    return path


def test_an_xlsx_head_is_sampled_by_column(tmp_path):
    p = _workbook(tmp_path / "people.xlsx", [(f"u{i}@example.com", i) for i in range(30)])
    values, rows, why = fcm.sample_values(p, "xlsx")
    assert why == "" and rows == 30
    assert values["email"][0] == "u0@example.com" and len(values["email"]) == 30
    assert values["id"][:3] == [0, 1, 2]


def test_xlsm_is_sampled_the_same_way(tmp_path):
    p = _workbook(tmp_path / "people.xlsx", [(f"u{i}@example.com", i) for i in range(25)])
    values, rows, why = fcm.sample_values(p, "xlsm")           # openpyxl reads the same container
    assert why == "" and rows == 25 and "email" in values


def test_only_a_bounded_head_is_read_never_the_whole_sheet(tmp_path):
    p = _workbook(tmp_path / "big.xlsx", [(f"u{i}@example.com", i) for i in range(fcm.MAX_VALUES * 3)])
    values, rows, why = fcm.sample_values(p, "xlsx")
    assert why == "" and rows == fcm.MAX_VALUES and len(values["email"]) == fcm.MAX_VALUES


def test_a_workbook_over_the_byte_limit_is_not_opened_and_says_why(tmp_path):
    p = _workbook(tmp_path / "people.xlsx", [(f"u{i}@example.com", i) for i in range(30)])
    values, rows, why = fcm.sample_values(p, "xlsx", limit=100)       # a workbook cannot be cut at a line
    assert values is None and rows == 0
    assert "over" in why and "100" in why and "xlsx" in why


def test_a_missing_library_is_said_not_a_silent_skip(tmp_path, monkeypatch):
    p = _workbook(tmp_path / "people.xlsx", [("a@example.com", 1)])
    monkeypatch.setattr(fp, "_has", lambda module: False)
    values, rows, why = fcm.sample_values(p, "xlsx")
    assert values is None and "openpyxl" in why and ".xlsx" in why


def test_a_workbook_that_cannot_be_parsed_says_so(tmp_path):
    p = tmp_path / "broken.xlsx"
    p.write_bytes(b"this is not a workbook")
    values, rows, why = fcm.sample_values(p, "xlsx")
    assert values is None and "could not be parsed" in why


def test_an_empty_sheet_is_an_empty_sample_not_an_error(tmp_path):
    p = tmp_path / "empty.xlsx"
    openpyxl.Workbook().save(p)
    values, rows, why = fcm.sample_values(p, "xlsx")
    assert why == "" and rows == 0


def test_xls_and_ods_without_their_library_say_which_library(tmp_path, monkeypatch):
    p = tmp_path / "old.xls"
    p.write_bytes(b"x")
    monkeypatch.setattr(fp, "_has", lambda module: False)
    for ext, lib in (("xls", "xlrd"), ("ods", "odf")):
        values, _rows, why = fcm.sample_values(p, ext)
        assert values is None and lib in why


def test_json_is_still_read_whole_only_and_the_other_formats_are_unchanged(tmp_path):
    j = tmp_path / "x.json"
    j.write_text('[{"a": 1}]')
    assert fcm.sample_values(j, "json")[2] == "values are not sampled from .json files here (read whole only)"
    c = tmp_path / "x.csv"
    c.write_text("email\n" + "\n".join(f"u{i}@example.com" for i in range(25)) + "\n")
    values, rows, why = fcm.sample_values(c, "csv")
    assert why == "" and rows == 25 and len(values["email"]) == 25


def test_a_spreadsheet_column_is_matched_to_a_data_class_end_to_end(tmp_path):
    from resource_explorer.registry import FileSystemEntity, ProjectRegistry
    from resource_explorer.surveyors.database.column_matching import KnownDataClass
    from resource_explorer.surveyors.database.egeria_reference_catalog import ReferenceCatalog

    root = tmp_path / "tree"
    root.mkdir()
    _workbook(root / "people.xlsx", [(f"person{i}@example.com", i) for i in range(30)])
    reg = ProjectRegistry(db_path=str(tmp_path / "fs.db"))
    reg.register_filesystem(FileSystemEntity(slug="tree", display_name="tree", local_mount_point=str(root)))
    reg.add_filesystem_file_columns("tree", "2026-11-22T09:00:00", [
        {"file_path": "people.xlsx", "column_name": "email", "ordinal": 0, "declared_type": "", "inferred_general_type": "object",
         "record_ct": 30, "value_ct": 30, "null_ct": 0, "distinct_ct": 30, "sample_ratio": 1.0,
         "stats_source": "resource-explorer", "state": "measured"}])
    email = KnownDataClass(guid="g-email", qualified_name="DataClass::EmailAddress", display_name="Email Address",
                           data_type="string", specification=r"[^@\s]+@[^@\s]+\.[A-Za-z]{2,}",
                           match_property_names=("email",))
    out = fcm.run_file_class_match(reg.get_filesystem("tree"), reg, catalog_loader=lambda: ReferenceCatalog(data_classes=[email]))
    m = next(x for x in out["matches"] if x["column"] == "email")
    assert m["verdict"] == "matched" and m["matched_display_name"] == "Email Address"
    assert out["file_outcomes"].get("sampled") == 1
