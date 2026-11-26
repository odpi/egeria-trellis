"""TR-54 B4 nit: the work-list view of runs must not read "never run" for a dataset that WAS run, only because more than 50 other
activity entries came after the run. It filters on the analysis run entries and scans further, and when even that is not enough it
says so ("no run among the last N entries") instead of "never run"."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from test_open_data import NO_EGERIA, _chosen, _register_demo, reg, store  # noqa: E402,F401
from test_open_data_profile import A, ProfileFake  # noqa: E402

from resource_explorer.activity_logger import log_analysis_run  # noqa: E402
from resource_explorer.open_data import queued, service  # noqa: E402


@pytest.fixture
def one(store):
    portal = _register_demo(store)
    wl = service.import_datasets(store, _chosen(portal["slug"], A), work_list_name="wl", transport=ProfileFake(),
                                 egeria_lookup=NO_EGERIA)["work_list"]
    return wl, store.datasets_in_work_list(wl)[0]


def _entry(reg, ds, analysis_id, status="ok"):
    activity = log_analysis_run(reg, "open_data", ds["slug"], str(ds["title"]), "running", "Running…", analysis_id, published=None)
    reg.update_activity_status(activity, status, summary=f"{analysis_id} {status}", detail='{"analysis_id": "%s"}' % analysis_id)
    return activity


def test_a_run_buried_under_more_than_fifty_other_entries_is_still_found(reg, store, one):
    wl, ds = one
    wanted = _entry(reg, ds, "external_data_scout")
    for i in range(70):                                  # 70 later entries for OTHER analyses on the same dataset
        _entry(reg, ds, "od_licence_usable" if i % 2 else "od_cadence_kept")
    view = queued.work_list_runs(reg, store, wl, "external_data_scout")
    assert view["runs"][0]["status"] == "ok" and view["runs"][0]["activity_id"] == wanted and view["never_run"] == 0


def test_the_newest_run_of_that_analysis_wins(reg, store, one):
    wl, ds = one
    _entry(reg, ds, "external_data_scout", "error")
    newer = _entry(reg, ds, "external_data_scout", "ok")
    assert queued.work_list_runs(reg, store, wl, "external_data_scout")["runs"][0]["activity_id"] == newer


def test_a_dataset_really_never_run_says_never_run(reg, store, one):
    wl, ds = one
    _entry(reg, ds, "od_licence_usable")
    view = queued.work_list_runs(reg, store, wl, "external_data_scout")
    assert view["runs"][0]["status"] == "never_run" and view["runs"][0]["summary"] == "never run"


def test_when_the_scan_limit_is_reached_without_a_hit_it_says_so_never_never_run(reg, store, one, monkeypatch):
    wl, ds = one
    _entry(reg, ds, "external_data_scout")
    for _ in range(8):
        _entry(reg, ds, "od_licence_usable")
    monkeypatch.setattr(queued, "RUN_SCAN_LIMIT", 5)
    row = queued.work_list_runs(reg, store, wl, "external_data_scout")["runs"][0]
    assert row["status"] == "unknown" and row["summary"] == "no run among the last 5 entries" and row["status"] != "never_run"
