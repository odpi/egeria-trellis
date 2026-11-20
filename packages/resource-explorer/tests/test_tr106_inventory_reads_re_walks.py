"""TR-106: the File System Inventory rows on Curate's publish table (files, directories, data_files, total_size) said "Not measured:
no survey has walked this file system yet" over a file system Resource Explorer's own walk had measured (86,491 files on
egeria_v6). The reader knew only the older per-entry rows and Egeria's Folder Survey. It now reads the newest walk of EITHER
kind; with neither it truly says "not measured".

An RE-led walk is stored as a filesystem_surveys row (`add_filesystem_survey`) carrying the walk's totals. Nothing reaches Egeria."""
from __future__ import annotations

from resource_explorer import publish_table
from tests.test_files2_reach_fork_folder_survey import _fs, registry  # noqa: F401 - fixture reused
from tests.test_files_review_defects_f39_w20 import OLD_F39_WORDS, _egeria_led_survey, _inventory_rows

WALK_AT = "2026-11-19T07:30:00"
WALK = {"surveyed_at": WALK_AT, "total_files": 86491, "total_directories": 2100, "total_data_files": 1889,
        "total_size_bytes": 8_400_000_000, "walk_extensions": {"run_id": WALK_AT, "led_by": "re"}}
LATER, EARLIER = "2099-01-01T00:00:00", "2000-01-01T00:00:00"


def _re_walk(registry, tmp_path, at=WALK_AT, **over):
    if not registry.get_filesystem("lake"):
        _fs(registry, tmp_path)
    registry.add_filesystem_survey("lake", at, {**WALK, "surveyed_at": at, "walk_extensions": {"run_id": at, "led_by": "re"}, **over})


def test_an_re_led_walk_alone_shows_real_counts_and_its_as_of(registry, tmp_path):
    _re_walk(registry, tmp_path)
    rows = _inventory_rows(registry)
    assert rows["files"]["value"] == 86491 and rows["files"]["value_words"] == "86491 files"
    assert rows["directories"]["value"] == 2100
    assert rows["data_files"]["value"] == 1889
    assert rows["total_size"]["value"] == 8_400_000_000
    for r in rows.values():
        assert r["as_of"] == WALK_AT, r["measure"]
        assert OLD_F39_WORDS not in (r.get("absence_words") or "")


def test_an_egeria_led_survey_alone_still_reads_egerias_counts(registry, tmp_path):
    _egeria_led_survey(registry, tmp_path)
    rows = _inventory_rows(registry)
    assert rows["files"]["value"] == 134 and rows["directories"]["value"] == 17
    assert rows["data_files"]["value"] is None and rows["data_files"]["absence"] == "not_measured"


def test_with_neither_the_rows_truly_say_not_measured(registry, tmp_path):
    _fs(registry, tmp_path)
    rows = _inventory_rows(registry)
    assert set(rows) == {"files", "directories", "data_files", "total_size"}
    for r in rows.values():
        assert r["value"] is None and r["absence"] == "not_measured"
        assert OLD_F39_WORDS in r["absence_words"]


def test_with_both_the_newest_decides(registry, tmp_path):
    _egeria_led_survey(registry, tmp_path)
    _re_walk(registry, tmp_path, at=LATER)
    assert _inventory_rows(registry)["files"]["value"] == 86491, "the RE walk is newer"


def test_with_both_an_older_walk_does_not_replace_egerias_newer_survey(registry, tmp_path):
    _egeria_led_survey(registry, tmp_path)
    _re_walk(registry, tmp_path, at=EARLIER)
    rows = _inventory_rows(registry)
    assert rows["files"]["value"] == 134 and rows["directories"]["value"] == 17, "Egeria's survey is newer"


def test_a_walk_row_that_counted_no_files_is_not_a_measurement(registry, tmp_path):
    """A survey row with no totals is never "0 files" and never "the folder is empty"."""
    _fs(registry, tmp_path)
    registry.add_filesystem_survey("lake", WALK_AT, {"surveyed_at": WALK_AT, "walk_extensions": {"run_id": WALK_AT}})
    rows = _inventory_rows(registry)
    assert rows["files"]["value"] is None and "empty" not in (rows["files"].get("absence_words") or "")
