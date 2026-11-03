"""Files build step 5 (2026-10-23): `file_profiles`, column rows per run with the source named, priced before the run.

Every test runs on a temp sqlite registry and fixture folders under tmp_path. The Egeria boundary is a fake bound to the
real port's signatures (`create_autospec(PyegeriaCsvSurveyPort, instance=True, spec_set=True)`). Nothing reaches Egeria, a
database or Ollama.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from unittest.mock import create_autospec

import pytest

from resource_explorer.native_survey_run import ReportRead, report_annotation
from resource_explorer.registry import FILESYSTEM_FILE_COLUMNS_DDL, FileSystemEntity, ProjectRegistry
from resource_explorer.surveyors.egeria_survey_reader import annotation_from_related_entry
from resource_explorer.surveyors.filesystem import file_profiles as fp
from resource_explorer.surveyors.filesystem import walk_extensions as we

ASSET = "bbbbbbbb-0000-0000-0000-0000000000f5"
LIMIT = 40_000          # the profile limit the tests use (the step's default is 50 MB)

#: Egeria's own answer: the ReportedAnnotation entries of CSV report 2b52abd2 (treasury-dts.csv, header
#: "Term Name,Qualified Name,Abbreviation,Summary,Description,Examples,Usage,Version Identifier,Status"), read live
#: 2026-10-25 by the coordinator's gate.
CSV_REPORT_FIXTURE = Path(__file__).parent / "fixtures" / "egeria_6_2" / "csv_survey_report_2b52abd2_annotations.json"
TREASURY_HEADER = ["Term Name", "Qualified Name", "Abbreviation", "Summary", "Description", "Examples", "Usage",
                   "Version Identifier", "Status"]


def _report(entries, guid="rep-a") -> ReportRead:
    """A ReportRead built the way `read_report` builds one: Egeria's wire entries through the paged reader's decoder."""
    return ReportRead(guid=guid, at="", originator_guid="x",
                      annotations=[report_annotation(annotation_from_related_entry(e)) for e in entries])


def _typed(v):
    """A plain value in Egeria's typed wire form (`elementProperties.propertyValueMap`)."""
    if isinstance(v, dict):
        return {"class": "MapTypePropertyValue", "mapValues": {"propertyValueMap": {k: _typed(x) for k, x in v.items()}}}
    if isinstance(v, list):
        return {"class": "ArrayTypePropertyValue",
                "arrayValues": {"propertyValueMap": {str(i): _typed(x) for i, x in enumerate(v)}}}
    return {"class": "PrimitiveTypePropertyValue", "primitiveValue": v}


def _wire(type_name, props, guid="g"):
    return {"element": {"elementGUID": guid, "type": {"typeName": type_name},
                        "elementProperties": {"propertyValueMap": {k: _typed(v) for k, v in props.items()}}}}


def _inspect(column, freq, values=None, asset="asset-1", guid=None):
    """One InspectDataValues annotation of the live shape (qualifiedName, displayName, valueCount, valueList)."""
    return _wire("ResourceProfileAnnotation", {
        "annotationType": "InspectDataValues", "qualifiedName": f"{asset}::InspectDataValues::{column}::1792972072411",
        "displayName": f"InspectDataValues ({column}) ", "valueCount": freq,
        "valueList": list(freq) if values is None else values}, guid=guid or f"ann-{column}")


def _schema(names, asset="asset-1"):
    return _wire("SchemaAnalysisAnnotation", {
        "annotationType": "Derive Schema From Data", "qualifiedName": f"{asset}::Derive Schema From Data::1",
        "additionalProperties": {"Column Names": "[" + ", ".join(names) + "]", "Column Count": str(len(names))}},
        guid="ann-schema")


@pytest.fixture
def registry(tmp_path):
    return ProjectRegistry(db_path=str(tmp_path / "fileprofiles.db"))


def _tree(tmp_path) -> Path:
    """3 csv, 1 parquet, 1 json, 1 xlsx, 1 binary data format (pickle), 1 oversize csv, and a README (not a data file)."""
    import pandas as pd

    root = tmp_path / "lake"
    (root / "sub").mkdir(parents=True)
    (root / "a.csv").write_text("id,name\n1,ann\n2,bob\n3,\n")
    (root / "b.csv").write_text("x,y,z\n1,2.5,2026-01-01\n2,3.5,2026-02-01\n")
    (root / "sub" / "c.csv").write_text("only\nq\nq\nr\n")
    pd.DataFrame({"k": [1, 2, 3], "v": ["a", "b", None]}).to_parquet(root / "d.parquet")
    (root / "e.json").write_text('[{"p": 1, "q": "s"}, {"p": 2, "q": "t"}]')
    pd.DataFrame({"m": [1, 2], "n": ["u", "w"]}).to_excel(root / "f.xlsx", index=False)
    (root / "g.pkl").write_bytes(b"\x80\x04binary")
    lines = ["n,word"] + [f"{i},w{i % 97}" for i in range(12_000)]
    (root / "big.csv").write_text("\n".join(lines) + "\n")
    (root / "README.md").write_text("# Lake\n\nData.\n")
    return root


def _walk(registry, root, *, slug="lake", led_by="re"):
    from resource_explorer.surveyors.filesystem.local_filesystem_surveyor import LocalFileSystemSurveyor

    fs = registry.get_filesystem(slug)
    if fs is None:
        registry.register_filesystem(FileSystemEntity(slug=slug, display_name="Lake", local_mount_point=str(root),
                                                      canonical_mount_point="/deployments/lake", egeria_asset_guid=ASSET))
        fs = registry.get_filesystem(slug)
    sd = LocalFileSystemSurveyor(fs, registry).run(profile_contents=False, walk_extensions=True)
    we.attach_extensions(fs, registry, sd, led_by=led_by)
    return fs


def _states(registry, slug="lake"):
    run = registry.list_filesystem_file_column_runs(slug)[0]
    return {r["file_path"]: r for r in registry.filesystem_file_profile_states(slug, run)}, run


def _port(*, listed=True):
    port = create_autospec(fp.pyegeria_port_class(), instance=True, spec_set=True)
    port.csv_survey_listed.return_value = listed
    port.egeria_file_system_name.return_value = "egeria-deployment"
    port.csv_asset_guid.side_effect = lambda fs, rel, canonical, fs_name: f"asset-{rel}"
    port.initiate.side_effect = lambda qn, target, guid, request_parameters=None: f"action-{guid}"
    return port


# ── 1. sampling: over the limit is sampled, never skipped ────────────────────────────────────────────────────────────

def test_a_file_over_the_limit_is_sampled_with_a_ratio_under_one_and_one_under_it_has_ratio_one(registry, tmp_path):
    root = _tree(tmp_path)
    fs = _walk(registry, root)
    fp.run_file_profiles(fs, registry, limit=LIMIT)
    states, run = _states(registry)
    assert states["big.csv"]["state"] == fp.SAMPLED
    assert 0 < states["big.csv"]["sample_ratio"] < 1
    assert states["a.csv"]["state"] == fp.MEASURED and states["a.csv"]["sample_ratio"] == 1
    big = registry.list_filesystem_file_columns("lake", run, file_path="big.csv")
    assert [c["column_name"] for c in big] == ["n", "word"]
    # a whole-line prefix was profiled: fewer records than the file holds, and no half row
    assert 0 < big[0]["record_ct"] < 12_000 and big[0]["null_ct"] == 0


def test_a_parquet_file_over_the_limit_reads_its_first_row_groups(tmp_path):
    import pandas as pd

    path = tmp_path / "rg.parquet"
    pd.DataFrame({"i": range(40_000), "s": [f"v{i}" for i in range(40_000)]}).to_parquet(path, row_group_size=5_000)
    size = path.stat().st_size
    out = fp.profile_file(path, "parquet", size, limit=size // 3)
    assert out["state"] == fp.SAMPLED and 0 < out["sample_ratio"] < 1
    assert out["columns"][0]["record_ct"] == round(out["sample_ratio"] * 40_000)
    assert out["columns"][0]["declared_type"] == "int64" and out["columns"][1]["declared_type"] == "string"


def test_a_whole_only_format_over_the_limit_is_a_row_saying_so_never_a_skip(tmp_path):
    path = tmp_path / "x.json"
    path.write_text('[{"a": 1}]' + " " * 5000)
    assert fp.profile_file(path, "json", path.stat().st_size, limit=100)["state"] == fp.OVER_LIMIT


# ── 2. formats not read, files not permitted ─────────────────────────────────────────────────────────────────────────

def test_an_unreadable_format_is_not_applicable_with_the_format_named_not_an_empty_profile(registry, tmp_path):
    root = _tree(tmp_path)
    fs = _walk(registry, root)
    fp.run_file_profiles(fs, registry, limit=LIMIT)
    states, run = _states(registry)
    assert states["g.pkl"]["state"] == fp.NOT_APPLICABLE and states["g.pkl"]["columns"] == 0
    rows = registry.list_filesystem_file_columns("lake", run, file_path="g.pkl")
    assert len(rows) == 1 and rows[0]["column_name"] == "" and rows[0]["record_ct"] is None
    counts = fp.card_counts(list(states.values()))
    assert counts["not_read"] == 1 and counts["not_read_formats"] == ["pkl"]
    assert "1 not read (formats: pkl)" in fp.card_words(counts)


@pytest.mark.skipif(os.geteuid() == 0, reason="root reads any file")
def test_an_inaccessible_file_is_not_permitted(registry, tmp_path):
    root = _tree(tmp_path)
    locked = root / "locked.csv"
    locked.write_text("a\n1\n")
    os.chmod(locked, 0)
    try:
        fs = _walk(registry, root)
        fp.run_file_profiles(fs, registry, limit=LIMIT)
        states, _ = _states(registry)
        assert states["locked.csv"]["state"] == fp.NOT_PERMITTED
        assert fp.card_counts(list(states.values()))["not_permitted"] == 1
    finally:
        os.chmod(locked, 0o644)


def test_a_secret_looking_data_file_is_never_opened(registry, tmp_path, monkeypatch):
    root = _tree(tmp_path)
    (root / "secret_keys.csv").write_text("k\nv\n")
    fs = _walk(registry, root)
    opened = []
    real = fp.profile_file
    monkeypatch.setattr(fp, "profile_file", lambda path, *a, **k: opened.append(path.name) or real(path, *a, **k))
    fp.run_file_profiles(fs, registry, limit=LIMIT)
    states, _ = _states(registry)
    assert states["secret_keys.csv"]["state"] == fp.SECRET_NAME and "secret_keys.csv" not in opened


# ── 3. the source choice ─────────────────────────────────────────────────────────────────────────────────────────────

def test_on_an_egeria_led_file_system_csv_goes_to_egeria_and_parquet_to_re(registry, tmp_path, monkeypatch):
    root = _tree(tmp_path)
    fs = _walk(registry, root, led_by="egeria")
    opened = []
    real = fp.profile_file
    monkeypatch.setattr(fp, "profile_file", lambda path, *a, **k: opened.append(path.name) or real(path, *a, **k))
    port = _port()
    out = fp.run_file_profiles(fs, registry, port=port, limit=LIMIT)
    states, _ = _states(registry)
    for name in ("a.csv", "b.csv", "sub/c.csv", "big.csv"):
        assert states[name]["state"] == fp.SUBMITTED
    assert states["d.parquet"]["state"] == fp.MEASURED and states["d.parquet"]["stats_source"] == fp.STATS_SOURCE_RE
    assert not {"a.csv", "b.csv", "c.csv", "big.csv"} & set(opened), "RE measures nothing Egeria measures"
    assert out["egeria_half"] == fp.EGERIA_HALF_SUBMITTED and out["egeria_files"] == 4
    # the survey is started on each file's asset, at the canonical path
    port.csv_asset_guid.assert_any_call(fs, "a.csv", "/deployments/lake/a.csv", "egeria-deployment")
    port.initiate.assert_any_call(fp.CSV_SURVEY_QN, fp.CSV_ACTION_TARGET, "asset-a.csv")


def test_on_an_re_led_file_system_csv_and_parquet_both_go_to_re(registry, tmp_path):
    root = _tree(tmp_path)
    fs = _walk(registry, root, led_by="re")
    port = _port()
    out = fp.run_file_profiles(fs, registry, port=port, limit=LIMIT)
    states, _ = _states(registry)
    assert states["a.csv"]["stats_source"] == fp.STATS_SOURCE_RE and states["d.parquet"]["stats_source"] == fp.STATS_SOURCE_RE
    assert out["egeria_half"] == fp.EGERIA_HALF_NONE
    port.csv_survey_listed.assert_not_called()
    port.initiate.assert_not_called()


def test_source_for_is_by_extension_and_lead():
    assert fp.source_for("x/a.csv", "egeria") == fp.SOURCE_EGERIA
    assert fp.source_for("x/a.parquet", "egeria") == fp.SOURCE_RE
    assert fp.source_for("x/a.csv", "re") == fp.SOURCE_RE
    assert fp.source_for("x/a.tsv", "egeria") == fp.SOURCE_RE


# ── 4. the Egeria half ───────────────────────────────────────────────────────────────────────────────────────────────

def test_egeria_not_listing_the_request_type_is_not_installed_and_the_re_half_still_runs(registry, tmp_path):
    root = _tree(tmp_path)
    fs = _walk(registry, root, led_by="egeria")
    port = _port(listed=False)
    out = fp.run_file_profiles(fs, registry, port=port, limit=LIMIT)
    states, _ = _states(registry)
    assert out["egeria_half"] == fp.EGERIA_HALF_NOT_INSTALLED
    assert states["a.csv"]["state"] == fp.NOT_INSTALLED
    assert states["d.parquet"]["state"] == fp.MEASURED and states["f.xlsx"]["state"] == fp.MEASURED
    port.initiate.assert_not_called()
    assert fp.card_counts(list(states.values()))["not_installed"] == 4


def test_a_failed_check_is_could_not_check_never_not_installed(registry, tmp_path):
    root = _tree(tmp_path)
    fs = _walk(registry, root, led_by="egeria")
    port = _port()
    port.csv_survey_listed.side_effect = TimeoutError("view server timed out")
    out = fp.run_file_profiles(fs, registry, port=port, limit=LIMIT)
    states, _ = _states(registry)
    assert out["egeria_half"] == fp.EGERIA_HALF_UNCHECKED and states["a.csv"]["state"] == fp.EGERIA_UNCHECKED
    assert "timed out" in out["egeria_actions"]["a.csv"]["error"]


def test_egerias_report_is_read_back_by_guid_into_column_rows_named_by_source(registry, tmp_path):
    root = _tree(tmp_path)
    fs = _walk(registry, root, led_by="egeria")
    port = _port()
    fp.run_file_profiles(fs, registry, port=port, limit=LIMIT)
    port.report_guid_for_action.side_effect = lambda action: "rep-a" if action == "action-asset-a.csv" else None
    port.read_report.return_value = _report([_schema(["id", "name"]), _inspect("name", {"ann": 1, "bob": 1, "": 1}),
                                             _inspect("id", {"1": 1, "2": 1, "3": 1})])
    out = fp.read_back_egeria(fs, registry, port)
    assert out["read"] == 1 and out["waiting"] == 3
    port.read_report.assert_called_once_with("rep-a")
    states, run = _states(registry)
    assert states["a.csv"]["state"] == fp.MEASURED and states["a.csv"]["stats_source"] == "egeria:rep-a"
    assert states["b.csv"]["state"] == fp.SUBMITTED
    cols = registry.list_filesystem_file_columns("lake", run, file_path="a.csv")
    assert [(c["column_name"], c["ordinal"], c["record_ct"], c["null_ct"], c["value_ct"], c["distinct_ct"])
            for c in cols] == [("id", 1, 3, None, None, 3), ("name", 2, 3, 1, 2, 2)]
    assert all(c["egeria_report_guid"] == "rep-a" for c in cols)
    counts = fp.card_counts(list(states.values()))
    assert counts["by_egeria"] == 1 and counts["submitted"] == 3


def test_egerias_live_report_names_every_column_by_its_header_and_derives_its_counts():
    """Report 2b52abd2 as Egeria wrote it: 9 InspectDataValues annotations (newest column first), a schema annotation
    and a physical-status one. Before this fix every column was stored as "InspectDataValues (<col>) " with no count."""
    cols = fp.columns_from_report(_report(json.loads(CSV_REPORT_FIXTURE.read_text()), guid="2b52abd2"))
    assert [c["column_name"] for c in cols] == TREASURY_HEADER
    assert [c["ordinal"] for c in cols] == list(range(1, 10))
    assert all(c["record_ct"] == 125 and c["state"] == fp.MEASURED for c in cols)
    by = {c["column_name"]: c for c in cols}
    # a column of blanks: Egeria's "" key is the null count
    assert (by["Usage"]["null_ct"], by["Usage"]["value_ct"], by["Usage"]["distinct_ct"]) == (125, 0, 0)
    assert by["Usage"]["min_len"] is None and by["Usage"]["max_len"] is None
    # no "" key: nulls not measured by Egeria, never 0; distinct is the number of keys
    assert (by["Term Name"]["null_ct"], by["Term Name"]["value_ct"], by["Term Name"]["distinct_ct"]) == (None, None, 121)
    assert (by["Summary"]["distinct_ct"], by["Summary"]["min_len"], by["Summary"]["max_len"]) == (120, 4, 103)
    assert (by["Status"]["distinct_ct"], by["Status"]["min_len"], by["Status"]["max_len"]) == (1, 5, 5)
    assert {c["stats_source"] for c in cols} == {"egeria:2b52abd2"}
    assert fp.schema_column_names(_report(json.loads(CSV_REPORT_FIXTURE.read_text()))) == TREASURY_HEADER


def test_a_leading_bom_is_stripped_from_egerias_column_name_and_its_schema_list():
    cols = fp.columns_from_report(_report([_schema(["\ufeffPatientId", "Date"]), _inspect("Date", {"2026-01-01": 2}),
                                           _inspect("\ufeffPatientId", {"P1": 1, "P2": 1})]))
    assert [(c["column_name"], c["ordinal"]) for c in cols] == [("PatientId", 1), ("Date", 2)]


def test_a_whole_undelimited_line_as_one_column_is_header_suspect_and_a_two_word_header_is_not():
    line = "PatientId Date AngleLeft AngleRight"
    cols = fp.columns_from_report(_report([_schema([line]),
                                           _inspect(line, {"P1 2026-01-01 30 31": 1, "P2 2026-01-02 29 33": 1})]))
    assert [(c["column_name"], c["state"]) for c in cols] == [(line, fp.HEADER_SUSPECT)]
    tabbed = fp.columns_from_report(_report([_inspect("a\tb", {"1\t2": 1})]))
    assert tabbed[0]["state"] == fp.HEADER_SUSPECT
    plain = fp.columns_from_report(_report([_schema(["Full Name"]), _inspect("Full Name", {"Ann Lee": 1})]))
    assert plain[0]["state"] == fp.MEASURED


def test_a_column_egeria_did_not_count_has_no_counts_never_zero():
    cols = fp.columns_from_report(_report([_inspect("x", {})]))
    assert [(c["column_name"], c["record_ct"], c["null_ct"], c["value_ct"], c["distinct_ct"]) for c in cols] == [
        ("x", None, None, None, None)]


def test_annotations_that_are_not_value_inspections_are_not_columns_and_the_reason_is_said():
    entries = [e for e in json.loads(CSV_REPORT_FIXTURE.read_text())
               if e["element"]["type"]["typeName"] != "ResourceProfileAnnotation"]
    report = _report(entries)
    assert fp.columns_from_report(report) == []
    assert "listed 9 columns but inspected no values" in fp.no_columns_why(report)
    assert fp.no_columns_why(_report([])) == "the report carries no annotations"
    assert "derived no columns" in fp.no_columns_why(_report([_schema([])]))


# ── 5. the end to end over the fixture folder ────────────────────────────────────────────────────────────────────────

def test_end_to_end_row_counts_and_states_over_the_fixture_folder(registry, tmp_path):
    """The PR body states these: 8 data files (README.md is not one), 7 profiled by RE (1 of them sampled), 1 not read."""
    root = _tree(tmp_path)
    fs = _walk(registry, root)
    out = fp.run_file_profiles(fs, registry, limit=LIMIT)
    states, run = _states(registry)
    assert set(states) == {"a.csv", "b.csv", "sub/c.csv", "d.parquet", "e.json", "f.xlsx", "g.pkl", "big.csv"}
    assert out["data_files"] == 8 and out["re_files"] == 8 and out["egeria_files"] == 0
    columns = {k: v["columns"] for k, v in states.items()}
    assert columns == {"a.csv": 2, "b.csv": 3, "sub/c.csv": 1, "d.parquet": 2, "e.json": 2, "f.xlsx": 2, "g.pkl": 0,
                       "big.csv": 2}
    rows = registry.list_filesystem_file_columns("lake", run)
    assert len(rows) == 15                     # 14 column rows + the pkl's one state row
    c = fp.card_counts(list(states.values()))
    assert fp.card_words(c) == ("7 files profiled (0 by Egeria, 7 by RE), 1 sampled, 1 not read (formats: pkl), "
                                "0 not permitted")
    a = {r["column_name"]: r for r in rows if r["file_path"] == "a.csv"}
    assert a["name"]["record_ct"] == 3 and a["name"]["null_ct"] == 1 and a["name"]["distinct_ct"] == 2
    assert a["name"]["inferred_general_type"] == "A" and a["name"]["min_len"] == 3 and a["id"]["inferred_general_type"] == "N"
    assert a["id"]["min_text"] == "1" and a["id"]["max_text"] == "3"
    # nothing was written into the path
    assert sorted(p.name for p in root.rglob("*")) == sorted(
        ["a.csv", "b.csv", "sub", "c.csv", "d.parquet", "e.json", "f.xlsx", "g.pkl", "big.csv", "README.md"])
    # the run's own record: the walk it profiled, bytes and seconds, this machine
    record = fp.run_record(registry, "lake", run)
    assert record["metrics"]["walk_run_id"] and record["metrics"]["machine"] == fp.machine()
    assert record["metrics"]["re_bytes_read"] > 0


# ── 6. the price before the run ──────────────────────────────────────────────────────────────────────────────────────

def test_the_cost_line_before_any_run_and_after_one(registry, tmp_path):
    root = _tree(tmp_path)
    fs = _walk(registry, root, led_by="egeria")
    first = fp.price(registry, "lake", limit=LIMIT)
    assert first["words"].startswith("8 data files, ")
    assert "Egeria will profile 4 CSV files, RE will profile 4 others; no run yet to price from" in first["words"]
    ticks = iter([100.0, 102.0])
    out = fp.run_file_profiles(fs, registry, port=_port(listed=False), limit=LIMIT, clock=lambda: next(ticks))
    second = fp.price(registry, "lake", limit=LIMIT)
    assert second["bytes_per_second"] == round(out["re_bytes_read"] / 2.0)
    assert re.search(r"; about \d[\d,]* s$", second["words"])


def test_no_walk_yet_says_so(registry, tmp_path):
    registry.register_filesystem(FileSystemEntity(slug="empty", display_name="E", local_mount_point=str(tmp_path)))
    assert fp.price(registry, "empty")["words"] == "no walk yet · run the folder survey first"
    with pytest.raises(fp.NothingToProfile):
        fp.run_file_profiles(registry.get_filesystem("empty"), registry)


# ── 7. publish: RE's rows only, changed ones only, counts not values ─────────────────────────────────────────────────

def test_publish_takes_res_changed_rows_only_and_carries_sample_ratio_and_source_not_values(registry, tmp_path):
    root = _tree(tmp_path)
    fs = _walk(registry, root)
    first = fp.run_file_profiles(fs, registry, limit=LIMIT)
    rows = fp.changed_rows(registry, "lake", first["run_id"])
    assert len(rows) == 14
    specs = fp.publish_specs(rows, "/deployments/lake")
    big = next(s for s in specs if s["additional"]["filePath"] == "/deployments/lake/big.csv")
    assert float(big["additional"]["sampleRatio"]) < 1 and big["additional"]["statsSource"] == "resource-explorer"
    assert "sampled" in big["explanation"] and "surveyed by Resource Explorer" in big["summary"]
    assert all("min_text" not in s["value_count"] and "max_text" not in s["value_count"] for s in specs)
    # the baseline is the last PUBLISHED run: with no publish yet, every row is new again
    second = fp.run_file_profiles(fs, registry, limit=LIMIT)
    assert len(fp.changed_rows(registry, "lake", second["run_id"])) == 14
    _published(registry, second["run_id"])
    second = fp.run_file_profiles(fs, registry, limit=LIMIT)
    assert fp.changed_rows(registry, "lake", second["run_id"]) == []
    (root / "a.csv").write_text("id,name\n1,ann\n2,bob\n3,cy\n4,dee\n")
    fs = _walk(registry, root)
    third = fp.run_file_profiles(fs, registry, limit=LIMIT)
    assert {r["file_path"] for r in fp.changed_rows(registry, "lake", third["run_id"])} == {"a.csv"}


def _published(registry, run_id, *, complete=True):
    registry.record_step_run("lake", fp.PUBLISH_STEP_KEY, run_id, entity_type="filesystem", source="egeria-published",
                             executor="local", metrics={"report_guid": "r", "complete": complete})


def test_changed_rows_ignore_an_incomplete_publish_and_the_published_run_survives_pruning(registry, tmp_path):
    root = _tree(tmp_path)
    fs = _walk(registry, root)
    first = fp.run_file_profiles(fs, registry, limit=LIMIT)
    _published(registry, first["run_id"])
    for _ in range(fp.RUNS_KEPT + 1):
        last = fp.run_file_profiles(fs, registry, limit=LIMIT)
    assert first["run_id"] in registry.list_filesystem_file_column_runs("lake")
    assert fp.changed_rows(registry, "lake", last["run_id"]) == []
    _published(registry, last["run_id"], complete=False)       # a partial publish is not a baseline
    (root / "a.csv").write_text("id,name\n1,ann\n2,bob\n3,cy\n4,dee\n")
    fs = _walk(registry, root)
    newer = fp.run_file_profiles(fs, registry, limit=LIMIT)
    assert fp.last_published_run(registry, "lake") == first["run_id"]
    assert {r["file_path"] for r in fp.changed_rows(registry, "lake", newer["run_id"])} == {"a.csv"}


def test_runs_are_kept_per_run_and_pruned_to_the_newest(registry, tmp_path):
    root = _tree(tmp_path)
    fs = _walk(registry, root)
    for _ in range(fp.RUNS_KEPT + 1):
        fp.run_file_profiles(fs, registry, limit=LIMIT)
    assert len(registry.list_filesystem_file_column_runs("lake")) == fp.RUNS_KEPT
    registry.remove_filesystem("lake")
    assert registry.list_filesystem_file_column_runs("lake") == []


# ── 8. the DDL (owner's go 2026-10-23): one additive, idempotent statement ───────────────────────────────────────────

def test_the_ddl_is_one_create_table_if_not_exists_and_nothing_else():
    assert len(FILESYSTEM_FILE_COLUMNS_DDL) == 1
    stmt = FILESYSTEM_FILE_COLUMNS_DDL[0]
    assert stmt.lstrip().upper().startswith("CREATE TABLE IF NOT EXISTS FILESYSTEM_FILE_COLUMNS")
    up = stmt.upper()
    assert "ALTER" not in up and "DROP" not in up and "INSERT" not in up and "INDEX" not in up


def test_the_ddl_survives_the_postgres_translator():
    from resource_explorer.registry import PostgresCursorWrapper

    stmt = FILESYSTEM_FILE_COLUMNS_DDL[0]
    assert "?" not in stmt and not re.search(r"(?<!:):[A-Za-z_]", stmt)
    assert "%" not in PostgresCursorWrapper(None)._translate_sql(stmt)


def test_the_table_is_created_on_a_fresh_registry_and_a_reopen_is_idempotent(tmp_path):
    import sqlite3

    path = str(tmp_path / "ddl.db")
    ProjectRegistry(db_path=path)
    ProjectRegistry(db_path=path)
    con = sqlite3.connect(path)
    cols = [c[1] for c in con.execute("PRAGMA table_info(filesystem_file_columns)")]
    assert cols == ["filesystem_slug", "run_id", "file_path", "column_name", "ordinal", "declared_type",
                    "inferred_general_type", "record_ct", "value_ct", "null_ct", "distinct_ct", "min_text", "max_text",
                    "min_len", "max_len", "sample_ratio", "stats_source", "egeria_report_guid", "egeria_datafield_guid",
                    "state"]
    con.execute("INSERT INTO filesystem_file_columns (filesystem_slug, run_id, file_path, column_name) VALUES ('s','r','f','c')")
    with pytest.raises(sqlite3.IntegrityError):
        con.execute("INSERT INTO filesystem_file_columns (filesystem_slug, run_id, file_path, column_name) VALUES ('s','r','f','c')")


# ── 9. the routes: the card's view comes with the folder survey, the press runs the step ─────────────────────────────

@pytest.fixture
def api(registry, monkeypatch):
    from fastapi.testclient import TestClient

    from resource_explorer.auth import create_access_token
    from resource_explorer.web.app import app

    monkeypatch.setattr("resource_explorer.registry.ProjectRegistry.__init__",
                        lambda self, db_path=None, **k: setattr(self, "__dict__", registry.__dict__) or None)
    from tests.zone_fakes import open_access

    open_access(monkeypatch, caller_if_none="fileprofiles-tester")
    headers = {"Authorization": "Bearer " + create_access_token(user_id="fileprofiles-tester", egeria_token="tok")}
    return TestClient(app), headers


@pytest.mark.usefixtures("signed_in_caller")
def test_the_press_profiles_and_the_folder_survey_view_carries_the_card(api, registry, tmp_path, monkeypatch):
    client, headers = api
    root = _tree(tmp_path)
    _walk(registry, root)
    before = client.get("/api/filesystems/lake/file-profiles", headers=headers).json()
    assert before["run"] is None and "no run yet to price from" in before["price"]["words"]
    monkeypatch.setattr(fp, "PROFILE_LIMIT_BYTES", LIMIT)
    monkeypatch.setattr(fp.run_file_profiles, "__kwdefaults__", {**fp.run_file_profiles.__kwdefaults__, "limit": LIMIT})
    r = client.post("/api/filesystems/lake/file-profiles", headers=headers)
    assert r.status_code == 200, r.text
    assert r.json()["view"]["run"]["words"].startswith("7 files profiled (0 by Egeria, 7 by RE), 1 sampled")
    view = client.get("/api/filesystems/lake/folder-survey", headers=headers).json()
    assert view["file_profiles"]["run"]["counts"]["profiled"] == 7


@pytest.mark.usefixtures("signed_in_caller")
def test_the_press_with_no_walk_is_409_with_the_words(api, registry, tmp_path):
    client, headers = api
    registry.register_filesystem(FileSystemEntity(slug="nowalk", display_name="N", local_mount_point=str(tmp_path)))
    r = client.post("/api/filesystems/nowalk/file-profiles", headers=headers)
    assert r.status_code == 409 and "run the folder survey first" in r.json()["detail"]


# ── 10. fix round 1 (Opus review, 2026-10-23) ────────────────────────────────────────────────────────────────────────

def _watch_opens(monkeypatch, root):
    import builtins
    import io

    opened: list[str] = []
    real_open, real_io_open, real_os_open = builtins.open, io.open, os.open

    def watch(path):
        try:
            p = os.fspath(path)
        except TypeError:
            return
        if isinstance(p, bytes):
            p = os.fsdecode(p)
        if isinstance(p, str) and p.startswith(str(root)):
            opened.append(p)

    monkeypatch.setattr(builtins, "open", lambda f, *a, **k: (watch(f), real_open(f, *a, **k))[1])
    monkeypatch.setattr(io, "open", lambda f, *a, **k: (watch(f), real_io_open(f, *a, **k))[1])
    monkeypatch.setattr(os, "open", lambda f, *a, **k: (watch(f), real_os_open(f, *a, **k))[1])
    return opened


def test_on_an_egeria_led_file_system_a_secret_csv_and_a_linked_csv_are_never_submitted(registry, tmp_path):
    root = _tree(tmp_path)
    (root / "credentials.csv").write_text("user,pw\nu,p\n")
    outside = tmp_path / "outside.csv"
    outside.write_text("a\n1\n")
    os.symlink(outside, root / "linked.csv")
    fs = _walk(registry, root, led_by="egeria")
    port = _port()
    fp.run_file_profiles(fs, registry, port=port, limit=LIMIT)
    states, _ = _states(registry)
    assert states["credentials.csv"]["state"] == fp.SECRET_NAME
    assert states["linked.csv"]["state"] == fp.LINK_NOT_FOLLOWED   # the walk keeps a link as a row; never followed
    submitted = {c.args[1] for c in port.csv_asset_guid.call_args_list}
    assert submitted == {"a.csv", "b.csv", "sub/c.csv", "big.csv"}
    assert states["a.csv"]["state"] == fp.SUBMITTED


def test_a_link_is_never_submitted_even_when_the_walk_row_does_not_say_link(registry, tmp_path):
    root = _tree(tmp_path)
    outside = tmp_path / "outside.csv"
    outside.write_text("a\n1\n")
    os.symlink(outside, root / "linked.csv")
    assert fp.precheck({"file_path": "linked.csv", "hash_state": ""}, root, True) == fp.LINK_NOT_FOLLOWED
    os.symlink(root / "sub", root / "subl")
    assert fp.precheck({"file_path": "subl/c.csv", "hash_state": ""}, root, True) == fp.LINK_NOT_FOLLOWED
    assert fp.precheck({"file_path": "credentials.csv", "hash_state": ""}, root, True) == fp.SECRET_NAME
    assert fp.precheck({"file_path": "a.csv", "hash_state": ""}, root, True) is None


def test_a_format_not_read_is_never_opened(registry, tmp_path, monkeypatch):
    root = _tree(tmp_path)
    fs = _walk(registry, root)
    opened = _watch_opens(monkeypatch, root)
    fp.run_file_profiles(fs, registry, limit=LIMIT)
    assert not any(p.endswith("g.pkl") for p in opened), opened
    assert any(p.endswith("a.csv") for p in opened)
    states, _ = _states(registry)
    assert states["g.pkl"]["state"] == fp.NOT_APPLICABLE


def test_a_second_press_while_one_runs_is_already_running_and_submits_nothing(registry, tmp_path):
    root = _tree(tmp_path)
    fs = _walk(registry, root, led_by="egeria")
    inner: list = []
    port = _port()

    def initiate(qn, target, guid, request_parameters=None):
        if not inner:
            with pytest.raises(fp.AlreadyRunning, match="already running"):
                fp.run_file_profiles(fs, registry, port=second, limit=LIMIT)
            inner.append(True)
        return f"action-{guid}"

    second = _port()
    port.initiate.side_effect = initiate
    fp.run_file_profiles(fs, registry, port=port, limit=LIMIT)
    assert inner == [True]
    second.csv_survey_listed.assert_not_called()
    second.initiate.assert_not_called()
    assert port.initiate.call_count == 4                    # each CSV once, never twice
    assert len(registry.list_filesystem_file_column_runs("lake")) == 1
    fp.run_file_profiles(fs, registry, port=_port(), limit=LIMIT)   # the claim was released
    assert len(registry.list_filesystem_file_column_runs("lake")) == 2


@pytest.mark.usefixtures("signed_in_caller")
def test_the_double_press_through_the_route_is_409_with_the_words(api, registry, tmp_path):
    client, headers = api
    root = _tree(tmp_path)
    _walk(registry, root)
    assert registry.take_claim(f"{fp.STEP_KEY}:lake", "another-press")
    r = client.post("/api/filesystems/lake/file-profiles", headers=headers)
    assert r.status_code == 409 and "already running" in r.json()["detail"]
    assert registry.list_filesystem_file_column_runs("lake") == []
    registry.release_claim(f"{fp.STEP_KEY}:lake", "another-press")
    assert client.post("/api/filesystems/lake/file-profiles", headers=headers).status_code == 200


def test_the_csv_template_qualified_name():
    assert fp.csv_asset_qualified_name("lake", "/deployments/lake/a.csv") == "CSVFile::lake:/deployments/lake/a.csv"


def _real_port_with_fake_surveyor(store, *, root_qn="FileFolder::lake:/deployments/lake", by_path=None):
    """The real port over a fake surveyor bound to the real signatures. The root folder reads back with `root_qn`;
    `by_path` is the typed pathName search's answer (default: Egeria's explicit "No elements found")."""
    from pyegeria import MetadataExpert

    from resource_explorer.surveyors.filesystem.egeria_filesystem_surveyor import EgeriaFileSystemSurveyor

    class _Surveyor(EgeriaFileSystemSurveyor):   # the spec, plus the instance attribute the port reads
        _metadata_expert = None

    fake = create_autospec(_Surveyor, instance=True, spec_set=True)
    fake._find_element_guid.side_effect = lambda name, type_name=None, *, qualified_name=False: store.get(name, "")
    fake._read_back_by_guid.side_effect = lambda guid, expect_type=None: {
        "matched": True, "why": "", "qualified_name": root_qn, "type_name": "FileFolder", "unknown_guid": False}
    fake._metadata_expert = create_autospec(MetadataExpert, instance=True, spec_set=True)
    fake._metadata_expert.find_metadata_elements_with_string.return_value = (
        "No elements found" if by_path is None else by_path)

    def create(info, canonical, fs_name, *, fallback=True):
        guid = f"{len(store):08d}-0000-0000-0000-000000000000"
        store[fp.csv_asset_qualified_name(fs_name, canonical)] = guid
        return guid

    fake._create_data_file_asset.side_effect = create
    port = fp.pyegeria_port_class()()
    port._fs_surveyor = fake
    port.csv_survey_listed = lambda: True
    port.initiate = lambda qn, target, guid, request_parameters=None: f"action-{guid}"
    return port, fake


def test_a_re_run_finds_the_csv_asset_by_its_template_qualified_name_and_creates_none(registry, tmp_path):
    root = _tree(tmp_path)
    fs = _walk(registry, root, led_by="egeria")
    store: dict = {}
    port, fake = _real_port_with_fake_surveyor(store)
    try:
        fp.run_file_profiles(fs, registry, port=port, limit=LIMIT)
        assert fake._create_data_file_asset.call_count == 4
        fp.run_file_profiles(fs, registry, port=port, limit=LIMIT)
        assert fake._create_data_file_asset.call_count == 4, "the second run created no asset"
    finally:
        port.close()
    fake.close.assert_called_once()
    calls = fake._find_element_guid.call_args_list
    assert calls and all(c.kwargs == {"type_name": "CSVFile", "qualified_name": True} for c in calls)
    assert calls[0].args[0] == "CSVFile::lake:/deployments/lake/a.csv"
    assert all(c.kwargs.get("fallback") is False for c in fake._create_data_file_asset.call_args_list)


def test_a_read_that_could_not_be_made_creates_no_asset_and_the_file_is_refused(registry, tmp_path):
    from resource_explorer.surveyors.filesystem.egeria_filesystem_surveyor import EgeriaFileSystemSurveyorError

    root = _tree(tmp_path)
    fs = _walk(registry, root, led_by="egeria")
    port, fake = _real_port_with_fake_surveyor({})
    fake._find_element_guid.side_effect = EgeriaFileSystemSurveyorError("timed out")
    try:
        out = fp.run_file_profiles(fs, registry, port=port, limit=LIMIT)
    finally:
        port.close()
    fake._create_data_file_asset.assert_not_called()
    assert out["egeria_half"] == fp.EGERIA_HALF_SUBMIT_FAILED


def test_a_report_with_no_column_profile_is_no_columns_never_profiled_by_egeria(registry, tmp_path):
    root = _tree(tmp_path)
    fs = _walk(registry, root, led_by="egeria")
    port = _port()
    fp.run_file_profiles(fs, registry, port=port, limit=LIMIT)
    port.report_guid_for_action.side_effect = lambda action: "rep-a" if action == "action-asset-a.csv" else None
    port.read_report.return_value = ReportRead(guid="rep-a", at="", originator_guid="x", annotations=[])
    out = fp.read_back_egeria(fs, registry, port)
    assert out["no_columns"] == 1 and out["read"] == 0
    assert out["no_columns_why"] == {"a.csv": "report rep-a: the report carries no annotations"}
    states, _ = _states(registry)
    assert states["a.csv"]["state"] == fp.NO_COLUMNS
    c = fp.card_counts(list(states.values()))
    assert c["no_columns"] == 1 and c["by_egeria"] == 0


def test_duplicate_column_names_in_a_report_dedupe_by_ordinal_and_a_bad_file_never_fails_the_read(registry, tmp_path):
    root = _tree(tmp_path)
    fs = _walk(registry, root, led_by="egeria")
    port = _port()
    fp.run_file_profiles(fs, registry, port=port, limit=LIMIT)
    port.report_guid_for_action.side_effect = lambda action: {"action-asset-a.csv": "rep-a",
                                                               "action-asset-b.csv": "rep-b"}.get(action)
    # a header "id,id": Egeria lists it twice and inspects it twice; one annotation read twice is not a third column
    once = _inspect("id", {"1": 3}, guid="ann-id-1")
    good = _report([_schema(["id", "id"]), once, once, _inspect("id", {"2": 3}, guid="ann-id-2")])
    port.read_report.side_effect = lambda guid: good if guid == "rep-a" else (_ for _ in ()).throw(ValueError("bad"))
    out = fp.read_back_egeria(fs, registry, port)
    assert out["read"] == 1 and out["failed"] == 1 and "b.csv" in out["errors"]
    _, run = _states(registry)
    cols = registry.list_filesystem_file_columns("lake", run, file_path="a.csv")
    assert [(c["column_name"], c["ordinal"]) for c in cols] == [("id", 1), ("id [2]", 2)]


def test_size_and_sampling_come_from_the_open_descriptor_not_the_walk_row(tmp_path):
    path = tmp_path / "grown.csv"
    path.write_text("n\n" + "\n".join(str(i) for i in range(20_000)) + "\n")
    out = fp.profile_file(path, "csv", 10, limit=LIMIT)          # the walk said 10 bytes; the file is bigger now
    assert out["size_bytes"] == path.stat().st_size and out["state"] == fp.SAMPLED
    assert out["sample_ratio"] == pytest.approx(out["bytes_read"] / out["size_bytes"], rel=0.01)
    small = fp.profile_file(tmp_path / "grown.csv", "csv", 10**12, limit=10**9)
    assert small["state"] == fp.MEASURED and small["sample_ratio"] == 1


def test_a_parquet_whose_single_row_group_is_over_the_limit_is_over_limit_never_read_whole(tmp_path, monkeypatch):
    import pandas as pd
    import pyarrow.parquet as pq

    path = tmp_path / "one.parquet"
    pd.DataFrame({"i": range(20_000), "s": [f"v{i}" for i in range(20_000)]}).to_parquet(path, row_group_size=50_000)
    read = []
    monkeypatch.setattr(pq.ParquetFile, "read", lambda self, *a, **k: read.append(1))
    out = fp.profile_file(path, "parquet", limit=path.stat().st_size // 2)
    assert out["state"] == fp.OVER_LIMIT and out["columns"] == [] and read == []


def test_a_headerless_csv_flags_data_like_headers_and_never_publishes_their_names(registry, tmp_path):
    root = tmp_path / "hl"
    root.mkdir()
    (root / "nohdr.csv").write_text("1,ann,2026-01-02\n2,ann,2026-01-03\n3,bob,2026-01-04\n")
    (root / "ok.csv").write_text("id,name\n1,x\n")
    fs = _walk(registry, root)
    out = fp.run_file_profiles(fs, registry, limit=LIMIT)
    cols = {c["column_name"]: c for c in registry.list_filesystem_file_columns("lake", out["run_id"], file_path="nohdr.csv")}
    assert {n: c["state"] for n, c in cols.items()} == {"1": fp.HEADER_SUSPECT, "ann": fp.HEADER_SUSPECT,
                                                        "2026-01-02": fp.HEADER_SUSPECT}
    ok = registry.list_filesystem_file_columns("lake", out["run_id"], file_path="ok.csv")
    assert all(c["state"] == fp.MEASURED for c in ok)
    published = fp.changed_rows(registry, "lake", out["run_id"])
    assert {r["file_path"] for r in published} == {"ok.csv"}
    names = {s["additional"]["columnName"] for s in fp.publish_specs(published, "/x")}
    assert names == {"id", "name"}
    view = fp.profiles_view(registry, "lake")["run"]
    assert view["counts"]["header_suspect"] == 1 and view["counts"]["profiled"] == 2
    shown = next(f for f in view["files"] if f["file_path"] == "nohdr.csv")["columns_list"]
    assert [c["column_name"] for c in shown] == ["column 1", "column 2", "column 3"]


def test_an_unreachable_mount_is_not_reachable_never_not_permitted(registry, tmp_path):
    root = _tree(tmp_path)
    fs = _walk(registry, root)
    gone = tmp_path / "gone"
    root.rename(gone)
    out = fp.run_file_profiles(fs, registry, limit=LIMIT)
    states, _ = _states(registry)
    assert out["mount_reachable"] is False
    assert {s["state"] for s in states.values()} == {fp.NOT_REACHABLE}
    c = fp.card_counts(list(states.values()))
    assert c["not_reachable"] == 8 and c["not_permitted"] == 0


def _publish_surveyor(store, *, fail_from=None):
    from pyegeria import AssetMaker
    from pyegeria.omvs.data_discovery import DataDiscovery

    from resource_explorer.surveyors.filesystem.egeria_filesystem_surveyor import EgeriaFileSystemSurveyor

    s = EgeriaFileSystemSurveyor(platform_url="https://x", view_server="v")
    s.connect = lambda: None
    s._find_element_guid = lambda name, type_name=None, *, qualified_name=False: store.get(name, "")
    maker = create_autospec(AssetMaker, instance=True, spec_set=True)

    def create_asset(body):
        guid = "aaaaaaaa-0000-0000-0000-00000000000r"
        store[body["properties"]["qualifiedName"]] = guid
        return guid

    maker.create_asset.side_effect = create_asset
    maker.get_asset_by_guid.side_effect = lambda guid, **k: {"elementHeader": {"guid": guid, "type": {"typeName": "SurveyReport"}},
                                                             "properties": {}}
    disc = create_autospec(DataDiscovery, instance=True, spec_set=True)
    made = []

    def create_annotation(body):
        if fail_from is not None and len(made) >= fail_from:
            raise RuntimeError("Egeria refused")
        guid = f"cccccccc-0000-0000-0000-{len(store):012d}"
        store[body["properties"]["qualifiedName"]] = guid
        made.append(guid)
        return guid

    disc.create_annotation.side_effect = create_annotation
    s._asset_maker, s._discovery = maker, disc
    return s, maker, disc


def test_a_partial_publish_is_resumed_on_the_same_report_adding_only_the_missing_annotations(registry, tmp_path):
    root = _tree(tmp_path)
    fs = _walk(registry, root)
    out = fp.run_file_profiles(fs, registry, limit=LIMIT)
    specs = fp.publish_specs(fp.changed_rows(registry, "lake", out["run_id"]), "/deployments/lake")
    store: dict = {}
    s, maker, disc = _publish_surveyor(store, fail_from=5)
    first = s.publish_file_profiles(fs, out["run_id"], specs, registry)
    assert first["annotations_in_egeria"] == 5 and first["complete"] is False and first["report_reused"] is False
    assert [bool(a["guid"]) for a in first["annotations"]] == [True] * 5 + [False] * (len(specs) - 5)
    assert first["annotations"][0]["column"] and first["annotations"][0]["file"].startswith("/deployments/lake/")
    s2, maker2, disc2 = _publish_surveyor(store)
    second = s2.publish_file_profiles(fs, out["run_id"], specs, registry)
    maker2.create_asset.assert_not_called()                          # the report was found by its qualifiedName
    assert second["report_guid"] == first["report_guid"] and second["report_reused"] is True
    assert disc2.create_annotation.call_count == len(specs) - 5      # only the missing ones
    assert second["complete"] is True and second["annotations_in_egeria"] == len(specs)
    assert fp.latest_publish_proof(registry, "lake", out["run_id"])["complete"] is True


# ── fix round 2 (2026-10-23): the name Egeria uses for the file system, the pathName read-first, the claims ──────────

PROBE_DIR = "/deployments/loading-bay/sample-data/hampton-drop-foot-weekly-measurements"
PROBE_QN = f"CSVFile::egeria-deployment:{PROBE_DIR}/week1.csv"      # FILES5-CSVFILE-QN-PROBE.json, verbatim shape
PROBE_GUID = "dddddddd-0000-0000-0000-000000000001"


def _probe_fs(registry, tmp_path, *, guid=ASSET):
    root = tmp_path / "hampton"
    root.mkdir()
    for n in (1, 2):
        (root / f"week{n}.csv").write_text("patient,score\np1,3\np2,4\n")
    registry.register_filesystem(FileSystemEntity(slug="hampton", display_name="Hampton", local_mount_point=str(root),
                                                  canonical_mount_point=PROBE_DIR, egeria_asset_guid=guid))
    return _walk(registry, root, slug="hampton", led_by="egeria")


def test_file_system_name_comes_from_the_root_folders_qualified_name_never_guessed():
    assert fp.file_system_name_from_root(f"FileFolder::egeria-deployment:{PROBE_DIR}", PROBE_DIR) == "egeria-deployment"
    assert fp.file_system_name_from_root("FileFolder::a:b:/x/y", "/x/y/") == "a:b"
    assert fp.file_system_name_from_root("FileFolder::egeria-deployment:/elsewhere", PROBE_DIR) is None
    assert fp.file_system_name_from_root(f"DataFolder::x:{PROBE_DIR}", PROBE_DIR) is None
    assert fp.file_system_name_from_root(f"FileFolder:::{PROBE_DIR}", PROBE_DIR) is None
    assert fp.file_system_name_from_root("", PROBE_DIR) is None


def test_an_existing_egeria_csvfile_of_the_live_shape_is_found_and_nothing_is_created_for_it(registry, tmp_path):
    fs = _probe_fs(registry, tmp_path)
    store = {PROBE_QN: PROBE_GUID}
    port, fake = _real_port_with_fake_surveyor(store, root_qn=f"FileFolder::egeria-deployment:{PROBE_DIR}")
    try:
        out = fp.run_file_profiles(fs, registry, port=port, limit=LIMIT)
    finally:
        port.close()
    assert out["egeria_actions"]["week1.csv"]["asset_guid"] == PROBE_GUID
    looked = [c.args[0] for c in fake._find_element_guid.call_args_list]
    assert PROBE_QN in looked and not any(":hampton:" in q or "::hampton" in q for q in looked), "never the slug"
    made = fake._create_data_file_asset.call_args_list
    assert [c.args[1] for c in made] == [f"{PROBE_DIR}/week2.csv"]          # only the file Egeria does not hold
    assert made[0].args[2] == "egeria-deployment" and made[0].kwargs["fallback"] is False
    fake._read_back_by_guid.assert_called_with(ASSET, expect_type="FileFolder")


def test_a_qualified_name_miss_is_read_again_by_typed_exact_path_name_and_a_hit_creates_nothing(registry, tmp_path):
    fs = _probe_fs(registry, tmp_path)
    hit = [{"elementHeader": {"guid": PROBE_GUID, "type": {"typeName": "CSVFile"}},
            "properties": {"qualifiedName": PROBE_QN, "pathName": f"{PROBE_DIR}/week1.csv"}}]
    # the root folder was attached by RE under its slug: the QN read misses, the pathName read finds Egeria's element
    port, fake = _real_port_with_fake_surveyor({}, root_qn=f"FileFolder::hampton:{PROBE_DIR}", by_path=hit)
    fake._metadata_expert.find_metadata_elements_with_string.side_effect = (
        lambda **kw: hit if kw["search_string"] == f"{PROBE_DIR}/week1.csv" else "No elements found")
    try:
        out = fp.run_file_profiles(fs, registry, port=port, limit=LIMIT)
    finally:
        port.close()
    assert [c.args[1] for c in fake._create_data_file_asset.call_args_list] == [f"{PROBE_DIR}/week2.csv"]
    assert out["egeria_actions"]["week1.csv"]["asset_guid"] == PROBE_GUID
    call = fake._metadata_expert.find_metadata_elements_with_string.call_args_list[0]
    assert call.kwargs["search_string"] == f"{PROBE_DIR}/week1.csv"
    assert call.kwargs["starts_with"] is True and call.kwargs["ends_with"] is True
    assert call.kwargs["ignore_case"] is False and call.kwargs["metadata_element_type"] == "CSVFile"
    assert call.kwargs["graph_query_depth"] == 0 and call.kwargs["page_size"] == 2
    assert call.kwargs["body"]["metadataElementTypeName"] == "CSVFile"
    fake._metadata_expert.get_elements_by_property_value.assert_not_called()   # the 30 s+ untyped search


@pytest.mark.parametrize("answer", [[], None, {"no": "guid"}, "something else"])
def test_a_path_name_read_that_is_not_an_explicit_miss_creates_nothing(registry, tmp_path, answer):
    fs = _probe_fs(registry, tmp_path)
    port, fake = _real_port_with_fake_surveyor({}, root_qn=f"FileFolder::egeria-deployment:{PROBE_DIR}", by_path=answer)
    if answer is None:
        fake._metadata_expert.find_metadata_elements_with_string.return_value = None
    try:
        out = fp.run_file_profiles(fs, registry, port=port, limit=LIMIT)
    finally:
        port.close()
    fake._create_data_file_asset.assert_not_called()
    assert out["egeria_half"] == fp.EGERIA_HALF_SUBMIT_FAILED


def test_two_elements_holding_the_path_name_are_ambiguous_and_nothing_is_created(registry, tmp_path):
    fs = _probe_fs(registry, tmp_path)
    path = f"{PROBE_DIR}/week1.csv"
    two = [{"elementHeader": {"guid": g, "type": {"typeName": "CSVFile"}}, "properties": {"pathName": path}}
           for g in (PROBE_GUID, "dddddddd-0000-0000-0000-000000000002")]
    port, fake = _real_port_with_fake_surveyor({}, root_qn=f"FileFolder::egeria-deployment:{PROBE_DIR}", by_path=two)
    try:
        out = fp.run_file_profiles(fs, registry, port=port, limit=LIMIT)
    finally:
        port.close()
    fake._create_data_file_asset.assert_not_called()
    assert "PathNameAmbiguous" in out["egeria_actions"]["week1.csv"]["error"]


def test_a_path_name_read_that_raises_creates_nothing(registry, tmp_path):
    fs = _probe_fs(registry, tmp_path)
    port, fake = _real_port_with_fake_surveyor({}, root_qn=f"FileFolder::egeria-deployment:{PROBE_DIR}")
    fake._metadata_expert.find_metadata_elements_with_string.side_effect = RuntimeError("timed out")
    try:
        out = fp.run_file_profiles(fs, registry, port=port, limit=LIMIT)
    finally:
        port.close()
    fake._create_data_file_asset.assert_not_called()
    assert out["egeria_half"] == fp.EGERIA_HALF_SUBMIT_FAILED


def test_both_explicit_misses_create_under_egerias_file_system_name(registry, tmp_path):
    fs = _probe_fs(registry, tmp_path)
    store: dict = {}
    port, fake = _real_port_with_fake_surveyor(store, root_qn=f"FileFolder::egeria-deployment:{PROBE_DIR}")
    try:
        fp.run_file_profiles(fs, registry, port=port, limit=LIMIT)
    finally:
        port.close()
    assert set(store) == {PROBE_QN, f"CSVFile::egeria-deployment:{PROBE_DIR}/week2.csv"}


@pytest.mark.parametrize("case", ["no_guid", "not_matched", "other_shape"])
def test_when_the_file_system_name_cannot_be_established_the_egeria_half_refuses(registry, tmp_path, case):
    fs = _probe_fs(registry, tmp_path, guid="" if case == "no_guid" else ASSET)
    port, fake = _real_port_with_fake_surveyor({}, root_qn=("FileFolder::x:/elsewhere" if case == "other_shape"
                                                            else f"FileFolder::egeria-deployment:{PROBE_DIR}"))
    if case == "not_matched":
        fake._read_back_by_guid.side_effect = lambda guid, expect_type=None: {
            "matched": False, "why": "Egeria does not hold an element with that GUID", "qualified_name": "",
            "type_name": "", "unknown_guid": True}
    try:
        out = fp.run_file_profiles(fs, registry, port=port, limit=LIMIT)
    finally:
        port.close()
    assert out["egeria_half"] == fp.EGERIA_HALF_FS_NAME_UNKNOWN
    fake._find_element_guid.assert_not_called()
    fake._metadata_expert.get_elements_by_property_value.assert_not_called()
    fake._create_data_file_asset.assert_not_called()
    states, _ = _states(registry, "hampton")
    assert {s["state"] for s in states.values()} == {fp.FS_NAME_UNKNOWN}
    assert all("could not be established" in a["error"] for a in out["egeria_actions"].values())
    assert fp.card_counts(list(states.values()))["file_system_name_unknown"] == 2


def test_a_lost_claim_stops_the_egeria_half_with_words_and_stores_nothing(registry, tmp_path, monkeypatch):
    root = _tree(tmp_path)
    fs = _walk(registry, root, led_by="egeria")
    port = _port()
    beats = iter([True, True, False])
    monkeypatch.setattr(registry, "refresh_claim", lambda key, holder: next(beats))
    with pytest.raises(fp.ClaimLost, match=r"lost its claim .* 2 CSV files already submitted"):
        fp.run_file_profiles(fs, registry, port=port, limit=LIMIT)
    assert port.initiate.call_count == 2
    assert registry.list_filesystem_file_column_runs("lake") == []
    assert registry.get_claim(f"{fp.STEP_KEY}:lake") is None


def test_a_lost_claim_stops_the_re_half_too(registry, tmp_path, monkeypatch):
    root = _tree(tmp_path)
    fs = _walk(registry, root)
    monkeypatch.setattr(registry, "refresh_claim", lambda key, holder: False)
    with pytest.raises(fp.ClaimLost, match="lost its claim"):
        fp.run_file_profiles(fs, registry, limit=LIMIT)
    assert registry.list_filesystem_file_column_runs("lake") == []


def test_submit_egeria_half_beats_once_per_file():
    beats = []
    port = _port()
    fs = FileSystemEntity(slug="lake", display_name="Lake", local_mount_point="/x", canonical_mount_point="/d")
    half, _, _ = fp.submit_egeria_half(port, fs, [{"file_path": "a.csv"}, {"file_path": "b.csv"}],
                                       heartbeat=lambda: beats.append(1) or True)
    assert half == fp.EGERIA_HALF_SUBMITTED and len(beats) == 2


@pytest.mark.usefixtures("signed_in_caller")
def test_a_second_publish_press_while_one_runs_is_409_with_the_words(api, registry, tmp_path):
    client, headers = api
    _walk(registry, _tree(tmp_path))
    key = f"{fp.PUBLISH_STEP_KEY}:lake"
    assert registry.take_claim(key, "another-publish")
    r = client.post("/api/filesystems/lake/file-profiles/publish", headers=headers)
    assert r.status_code == 409 and "publish of the data-file profiles of lake is already running" in r.json()["detail"]
    assert registry.get_claim(key)[0] == "another-publish"          # the other press's claim is untouched
    registry.release_claim(key, "another-publish")
    r = client.post("/api/filesystems/lake/file-profiles/publish", headers=headers)
    assert r.status_code == 409 and "No data-file profiles to publish" in r.json()["detail"]
    assert registry.get_claim(key) is None                          # released after the press


@pytest.mark.usefixtures("signed_in_caller")
def test_a_lost_claim_through_the_route_is_409_with_the_words(api, registry, tmp_path, monkeypatch):
    client, headers = api
    _walk(registry, _tree(tmp_path))
    monkeypatch.setattr(registry, "refresh_claim", lambda key, holder: False)
    monkeypatch.setattr(fp.run_file_profiles, "__kwdefaults__", {**fp.run_file_profiles.__kwdefaults__, "limit": LIMIT})
    r = client.post("/api/filesystems/lake/file-profiles", headers=headers)
    assert r.status_code == 409 and "lost its claim" in r.json()["detail"]


def test_a_way_down_that_cannot_be_checked_holds_the_file(tmp_path, monkeypatch):
    root = _tree(tmp_path)
    real = os.lstat

    def lstat(p, *a, **k):
        if str(p).endswith("sub"):
            raise PermissionError("denied")
        return real(p, *a, **k)

    monkeypatch.setattr(fp.os, "lstat", lstat)
    assert fp._is_linked(root, "sub/c.csv") is True
    assert fp.precheck({"file_path": "sub/c.csv", "hash_state": ""}, root, True) == fp.NOT_REACHABLE
    assert fp._is_linked(root, "a.csv") is False


def test_card_words_name_header_suspects_only_when_any():
    c = {"profiled": 2, "by_egeria": 0, "by_re": 2, "sampled": 0, "not_read": 0, "not_read_formats": [],
         "not_permitted": 0}
    assert fp.card_words(c).endswith("0 not permitted")
    assert fp.card_words({**c, "header_suspect": 0}).endswith("0 not permitted")
    assert fp.card_words({**c, "header_suspect": 1}).endswith("0 not permitted (1 header suspect)")
