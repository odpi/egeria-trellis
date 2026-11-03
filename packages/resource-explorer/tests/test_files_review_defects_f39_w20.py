"""The Files defects read in the Architect's Files review of 2026-10-30, §2 (kept outside the repo).

F39  Curate's publish table on an Egeria-led file system said "Not measured: no survey has walked this file system yet"
     over a file system Egeria's Folder Survey had walked: the row read only RE's own `filesystem_inventory` run.
F41  The same root on the Questions tabs: Q-0094, Q-0100 and Q-0095 read has_data=false there. Where Egeria leads,
     `_filesystem_inventory_results` reads the folder-survey read-back, with the surveyor named.
F40  fs_activity's unit was a template never filled: "more than Cold Days days". It says the value the step ran with.
W20  Egeria's unclassified files read "null 11": they read "unclassified".
(W19, the doubled sentence on the card, is a render test: test-harness/files-review-w19.test.mjs.)

The Egeria-led survey is stored by the real native-survey port flow (`submit_native_survey`, `refresh_run`) against the
fake port of test_files2_reach_fork_folder_survey; nothing reaches Egeria.
"""
from __future__ import annotations

import pytest

from resource_explorer import native_survey_run as nsr
from resource_explorer import publish_table
from resource_explorer.facts import FactLayer
from tests.test_files2_reach_fork_folder_survey import _ann, _fs, _Port, _row, registry  # noqa: F401 - fixture reused

EGERIA_WORDS = "surveyed by Egeria's Folder Survey"
OLD_F39_WORDS = "no survey has walked this file system yet"


def _egeria_led_survey(registry, tmp_path, *, complete=True):
    """loading_bay's shape: verified Egeria reach and a completed Folder Survey read back into the registry."""
    if not registry.get_filesystem("lake"):
        _fs(registry, tmp_path)
    _row(registry, "egeria_verified", at="2026-10-20T10:00:00+00:00", slug="lake", path="/deployments/data-lake")
    port = _Port()
    nsr.submit_native_survey(registry, port, "filesystem", "lake", nsr.FOLDER_SURVEY_QN,
                             technology_type="File System Directory", submitted_by="dan")
    if not complete:
        return port
    port.status = ("COMPLETED", "done")
    port.reports["ea-folder-9"] = "report-9"
    port.originators["report-9"] = "ea-folder-9"
    port.annotations["report-9"] = [
        _ann(1, "Capture File Counts", resource_properties={
            "Number of files": "134", "Total file size": "6.0130446E7", "Number of subdirectories (folders)": "17",
            "Number of hidden files": "2", "Number of symbolic links": "0", "Number of unclassified files": "11"}),
        _ann(2, "Profile File Types", value_count={"Markdown File": 57, "CSV File": 36, "null": 11}),
        _ann(3, "Profile File Extensions", value_count={"md": 60, "csv": 36, "null": 2}),
    ]
    run = registry.list_native_survey_runs("filesystem", "lake", nsr.FOLDER_SURVEY_QN)[0]
    nsr.refresh_run(registry, port, run, entity_type="filesystem", slug="lake")
    return port


def _inventory_rows(registry):
    return {r["measure"]: r for r in publish_table.build_rows(registry, "filesystem", "lake")["rows"]
            if r["analysis_id"] == "filesystem_inventory"}


# ── F39: the publish table reads what Egeria's survey measured ──────────────────────────────────────────────────────

def test_f39_publish_rows_read_egerias_folder_survey_not_never_walked(registry, tmp_path):
    _egeria_led_survey(registry, tmp_path)
    rows = _inventory_rows(registry)
    assert rows["files"]["value"] == 134 and rows["files"]["value_words"] == "134 files"
    assert rows["directories"]["value"] == 17
    assert rows["total_size"]["value"] == 60130446
    for r in rows.values():
        assert OLD_F39_WORDS not in (r.get("absence_words") or "")
    # Already in Egeria through Egeria's own report: named, and never written a second time.
    assert not rows["files"]["publishable"]
    assert rows["files"]["elsewhere"]["route"] == "egeria_survey"
    assert rows["files"]["why_not"].startswith(EGERIA_WORDS)
    assert rows["files"]["elsewhere"]["report_guid"] == "report-9"


def test_f39_a_count_egeria_does_not_make_is_not_measured_never_measured_nothing(registry, tmp_path):
    """Egeria's survey does not single out data files: absence is never 0, and never "the folder is empty"."""
    _egeria_led_survey(registry, tmp_path)
    row = _inventory_rows(registry)["data_files"]
    assert row["value"] is None and row["absence"] == "not_measured"
    assert row["absence_words"] == "Not measured: Egeria's Folder Survey counts files by type, not data files apart."
    assert "empty" not in row["absence_words"]


def test_f39_an_egeria_led_file_system_with_no_completed_survey_still_says_not_measured(registry, tmp_path):
    _egeria_led_survey(registry, tmp_path, complete=False)
    rows = _inventory_rows(registry)
    assert rows["files"]["value"] is None
    assert rows["files"]["absence_words"] == f"Not measured: {OLD_F39_WORDS}."


# ── F41: the fact layer, and so the Questions tabs, read the Egeria-led result ─────────────────────────────────────

def test_f41_fact_is_measured_from_egeria_with_the_surveyor_named(registry, tmp_path):
    _egeria_led_survey(registry, tmp_path)
    fact = FactLayer(registry, resource_type="filesystem").fact("lake", "filesystem_inventory")
    assert fact.state == "measured"
    assert fact.provenance == "egeria"
    assert fact.value["surveyed_by"] == "Egeria's Folder Survey"
    assert fact.value["report_sentence"] == EGERIA_WORDS
    assert fact.headline == f"134 files in 17 folders · 57.3 MB · {EGERIA_WORDS}"
    assert fact.note == f"Read back from Egeria's own survey report ({EGERIA_WORDS}), not from a run of Resource Explorer's."


@pytest.mark.parametrize(("phase", "qid"), [("scouting", "Q-0094"), ("scouting", "Q-0100"), ("discovery", "Q-0095")])
def test_f41_questions_answered_by_egerias_survey_have_data(registry, tmp_path, phase, qid):
    from resource_explorer.workflows.scouting import build_question_checklist

    def has_data():
        out = build_question_checklist(registry, "filesystem", "lake", phase, [], [])
        return next(q for q in out["questions"] if q["id"] == qid)["has_data"]

    _fs(registry, tmp_path)
    _row(registry, "egeria_verified", at="2026-10-20T10:00:00+00:00", slug="lake", path="/deployments/data-lake")
    assert not has_data()           # never measured stays never measured
    _egeria_led_survey(registry, tmp_path)
    assert has_data() is True


def test_f41_where_re_leads_the_run_gate_is_unchanged(registry, tmp_path):
    """An RE-led file system is not read back from Egeria: no run recorded is still "not yet run"."""
    _fs(registry, tmp_path)
    _row(registry, "re_only", at="2026-10-20T10:00:00+00:00", slug="lake", path="/deployments/data-lake")
    fact = FactLayer(registry, resource_type="filesystem").fact("lake", "filesystem_inventory")
    assert fact.state == "never_run"


# ── F40: the unit says the value the step ran with ─────────────────────────────────────────────────────────────────

def test_f40_unit_fills_cold_days_from_the_result():
    unit = "files not modified for more than {Cold Days} days (cold)"
    assert publish_table.filled_unit(unit, {"Cold Days": 365, "Cold Files": 3}) == \
        "files not modified for more than 365 days (cold)"
    # The first result that holds it; a result without it is passed over.
    assert publish_table.filled_unit(unit, {}, {"Cold Days": 30}) == "files not modified for more than 30 days (cold)"
    # Nothing measured: never the field's name and never a guessed number.
    assert publish_table.filled_unit(unit, {}) == "files not modified for more than a set number of days (cold)"


def test_f40_the_catalogued_fs_activity_unit_is_a_placeholder_not_words():
    from resource_explorer.surveyors.analysis_catalog_reader import _load

    entry = next(e for e in _load()["filesystem"] if e.id == "fs_activity")
    units = [m.unit for m in entry.declaration.measures]
    assert units == ["files not modified for more than {Cold Days} days (cold)",
                     "files not read for more than {Cold Days} days (cold)"]


def test_f40_publish_row_says_365_days(registry, tmp_path, monkeypatch):
    from resource_explorer.surveyors.analysis_catalog_reader import _load

    entry = next(e for e in _load()["filesystem"] if e.id == "fs_activity")
    _fs(registry, tmp_path)
    record = {"value": {"Cold Files": 4, "Cold Days": 365, "Hot Days": 7, "asOf": "2026-10-29T00:14:56+00:00"},
              "state": "measured", "reason": "", "measured_at": "2026-10-29T00:14:56+00:00"}
    monkeypatch.setattr("resource_explorer.declared_context.measure_records",
                        lambda *a, **k: {"fs_activity": record, "fs_access_activity": {**record, "value": {
                            "Cold Days": 365}, "state": "not_measured", "reason": "access times not recorded"}})
    rows = {r["measure"]: r for r in publish_table.build_rows(registry, "filesystem", "lake", entries=[entry])["rows"]}
    assert rows["fs_activity"]["unit"] == "files not modified for more than 365 days (cold)"
    assert rows["fs_activity"]["value_words"] == "4 files not modified for more than 365 days (cold)"
    assert rows["fs_access_activity"]["unit"] == "files not read for more than 365 days (cold)"
    assert all("Cold Days days" not in r["unit"] for r in rows.values())


# ── W20: Egeria's unclassified files read "unclassified" ──────────────────────────────────────────────────────────

def test_w20_null_type_and_extension_read_unclassified(registry, tmp_path):
    from resource_explorer.folder_survey_view import folder_survey_view

    _egeria_led_survey(registry, tmp_path)
    res = folder_survey_view(registry, "lake")["results"]
    assert {"name": "unclassified", "count": 11} in res["file_types"]
    assert {"name": "unclassified", "count": 2} in res["extensions"]
    assert all(r["name"] != "null" for r in res["file_types"] + res["extensions"])


def test_w20_unnamed_keys_merge_into_one_unclassified_count():
    from resource_explorer.surveyors.filesystem.folder_survey_annotations import _counts

    got = _counts({"detail": {"value_count": {"null": 3, "": 2, "csv": 9}}})
    assert got == [{"name": "csv", "count": 9}, {"name": "unclassified", "count": 5}]
