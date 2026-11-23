"""TR-54 B3: od_cadence_kept. Compares the cadence a publisher states (accrualPeriodicity) with the modified dates RE has OBSERVED over
its refresh history (the kept `external_change` looks of the dataset). One observation is "not enough history", never "kept"; no cadence
stated is `nothing_found`, never "not kept"; a cadence that is not an interval (irregular, continuous) is stated and not judged. It reads
the stored history only (no portal call) and stores dataset facts only (no per-user data), like B2.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from test_open_data import FakeTransport, _register_demo, reg, store  # noqa: E402,F401

from resource_explorer.open_data import analyses, service  # noqa: E402
from resource_explorer.open_data.absence import NOT_REPORTED  # noqa: E402

ID = "od_cadence_kept"


# ── the cadence words ────────────────────────────────────────────────────────

@pytest.mark.parametrize("raw,days", [
    ("P1D", 1), ("P1W", 7), ("P2W", 14), ("P1M", 30), ("P3M", 91), ("P1Y", 365), ("PT1H", pytest.approx(1 / 24)),
    ("R/P1M", 30), ("R/P1D", 1), ("r/p1w", 7),
    ("http://purl.org/cld/freq/daily", 1), ("http://purl.org/cld/freq/weekly", 7), ("http://purl.org/cld/freq/biweekly", 14),
    ("http://purl.org/cld/freq/monthly", 30), ("http://purl.org/cld/freq/quarterly", 91), ("http://purl.org/cld/freq/annual", 365),
    ("http://purl.org/cld/freq/semiannual", 182), ("http://purl.org/cld/freq/irregular", None),
    ("Daily", 1), ("weekly", 7), ("Monthly", 30), ("quarterly", 91), ("annually", 365), ("yearly", 365),
    ("continuous", None), ("irregular", None), ("as needed", None),
])
def test_a_stated_cadence_is_read_as_days_between_updates_or_as_no_interval(raw, days):
    from resource_explorer.open_data.cadence import parse_cadence

    c = parse_cadence(raw)
    assert c is not None and c.days == days and c.raw == raw


@pytest.mark.parametrize("raw", ["", "  ", None, 42, {"a": 1}, ["monthly"], NOT_REPORTED,
                                 "unknown", "Unknown", "None", "N/A", "TBD", "null", "not specified"])
def test_no_cadence_stated_is_none(raw):
    from resource_explorer.open_data.cadence import parse_cadence

    assert parse_cadence(raw) is None


def test_an_unrecognised_word_is_kept_as_stated_and_is_not_an_interval():
    from resource_explorer.open_data.cadence import parse_cadence

    c = parse_cadence("every blue moon")
    assert c.days is None and c.raw == "every blue moon"


# ── the judgement, from the cadence and the observed (looked_at, modified) pairs ──

def _looks(*pairs):
    return [{"looked_at": f"{looked}T10:00:00", "modified": mod} for looked, mod in pairs]


def test_monthly_updated_each_month_is_kept():
    from resource_explorer.open_data.cadence_kept import judge

    r = judge("P1M", _looks(("2026-08-02", "2026-08-01"), ("2026-09-02", "2026-09-01"), ("2026-10-02", "2026-10-01")))
    assert r.state == "measured" and r.measures["verdict"] == "kept" and r.measures["cadence_checks"] == 1
    assert r.measures["observations"] == 3 and r.summary.startswith("kept")


def test_monthly_not_updated_for_three_months_is_late_and_says_by_how_much():
    from resource_explorer.open_data.cadence_kept import judge

    r = judge("P1M", _looks(("2026-08-02", "2026-08-01"), ("2026-11-15", "2026-08-01")))
    assert r.measures["verdict"] == "late" and r.measures["days_since_modified"] == 106 and r.measures["allowed_days"] == 45
    assert "late" in r.summary and "2026-08-01" in r.summary


def test_an_on_time_monthly_publisher_seen_only_quarterly_is_kept_with_the_sparse_looks_words():
    """W-B3-1: RE only sees a modified date when it looks, so dates a quarter apart say nothing about the publisher."""
    from resource_explorer.open_data.cadence_kept import judge

    r = judge("P1M", _looks(("2026-01-30", "2026-01-28"), ("2026-04-30", "2026-04-27"), ("2026-07-30", "2026-07-28")))
    assert r.measures["verdict"] == "kept" and "late" not in r.summary.split(";")[0]
    assert r.summary.startswith("kept in the looks RE has; RE looked every ~91 days, longer than the cadence")
    assert "a late update between looks cannot be seen" in r.summary and r.measures["looks_interval_days"] == 90.5
    assert r.measures["longest_gap_days"] == 92                       # still a fact, labelled as between the dates RE observed


def test_a_look_that_saw_a_date_older_than_the_cadence_allows_is_late_and_names_that_look():
    from resource_explorer.open_data.cadence_kept import judge

    r = judge("P1M", _looks(("2026-08-02", "2026-08-01"), ("2026-09-02", "2026-09-01"), ("2026-10-31", "2026-09-01"),
                            ("2026-11-02", "2026-11-01")))
    assert r.measures["verdict"] == "late" and r.measures["worst_look"] == "2026-10-31" and r.measures["worst_age_days"] == 60
    assert "the look on 2026-10-31 saw a modified date of 2026-09-01, 60 days old" in r.summary


def test_dense_looks_on_time_say_a_plain_kept():
    from resource_explorer.open_data.cadence_kept import judge

    r = judge("P1M", _looks(("2026-08-02", "2026-08-01"), ("2026-09-02", "2026-09-01"), ("2026-10-02", "2026-10-01")))
    assert r.summary.startswith("kept: stated cadence P1M") and "cannot be seen" not in r.summary and r.measures["looks_interval_days"] == 30.5


def test_the_summary_pluralises_refreshes():
    from resource_explorer.open_data.cadence_kept import judge

    assert "3 refreshes read" in judge("P1M", _looks(("2026-08-02", "2026-08-01"), ("2026-09-02", "2026-09-01"),
                                                     ("2026-10-02", "2026-10-01"))).summary


def test_a_placeholder_in_the_cadence_field_is_no_cadence_stated_and_the_raw_word_is_noted():
    from resource_explorer.open_data.cadence_kept import judge

    r = judge("Unknown", _looks(("2026-08-02", "2026-08-01"), ("2026-09-02", "2026-09-01")))
    assert r.state == "nothing_found" and r.measures["placeholder"] == "Unknown" and "'Unknown'" in r.explanation


def test_one_observation_is_not_enough_history_never_kept():
    from resource_explorer.open_data.cadence_kept import judge

    r = judge("P1M", _looks(("2026-11-02", "2026-11-01")))
    assert r.state == "not_measured" and r.summary == "not enough history (1 refresh)"
    assert "cadence_checks" not in r.measures and "kept" not in r.summary.replace("not enough", "")


def test_no_refresh_at_all_says_so_in_words():
    from resource_explorer.open_data.cadence_kept import judge

    r = judge("P1M", [])
    assert r.state == "not_measured" and r.summary == "not enough history (no refresh yet)"


def test_looks_that_state_no_modified_date_do_not_count_as_observations():
    from resource_explorer.open_data.cadence_kept import judge

    r = judge("P1M", _looks(("2026-08-02", NOT_REPORTED), ("2026-09-02", "2026-09-01"), ("2026-10-02", "not a date")))
    assert r.state == "not_measured" and r.summary == "not enough history (1 refresh)"


def test_no_cadence_stated_is_nothing_found_not_not_kept():
    from resource_explorer.open_data.cadence_kept import judge

    r = judge(None, _looks(("2026-08-02", "2026-08-01"), ("2026-09-02", "2026-09-01")))
    assert r.state == "nothing_found" and r.summary == "the portal states no update cadence for this dataset"
    assert r.measures["cadence_checks"] == 0 and "late" not in r.summary and "kept" not in r.summary


def test_a_cadence_that_is_not_an_interval_is_stated_and_not_judged():
    from resource_explorer.open_data.cadence_kept import judge

    r = judge("irregular", _looks(("2026-08-02", "2026-08-01"), ("2026-09-02", "2026-09-01")))
    assert r.state == "measured" and r.measures["judged"] is False
    assert r.summary == "not judged: the stated cadence 'irregular' is not an interval"


def test_the_stored_result_names_no_investigation_or_person():
    from resource_explorer.open_data.cadence_kept import judge

    stored = json.dumps([judge("P1M", _looks(("2026-08-02", "2026-08-01"), ("2026-09-02", "2026-09-01"))).measures])
    assert "investigation" not in stored and "title" not in stored and "user" not in stored


# ── the runner on the queue path ─────────────────────────────────────────────

@pytest.fixture
def dataset(store):
    portal = _register_demo(store)
    names = [r["dataset_id"] for r in service.run_search(store, portal["slug"], terms="dataset", pages=1,
                                                         transport=FakeTransport())["rows"]][:1]
    service.import_datasets(store, [service.Chosen(portal["slug"], n) for n in names], work_list_name="b3",
                            transport=FakeTransport(), egeria_lookup=lambda *a, **k: [])
    return store.list_datasets()[0]


def _state(reg, slug, cadence):
    import json as _j

    with reg._conn() as conn:
        row = conn.execute("SELECT record_json FROM open_data WHERE slug = ?", (slug,)).fetchone()
        rec = _j.loads(row[0] or "{}")
        rec["accrualPeriodicity"] = cadence
        conn.execute("UPDATE open_data SET record_json = ? WHERE slug = ?", (_j.dumps(rec), slug))


def _look(reg, slug, looked, modified):
    reg.record_step_run(slug, "external_change", f"{looked}T10:00:00", entity_type="open_data", source="resource-explorer",
                        executor="local", metrics={"snapshot": {"fields": {"modified": modified}}, "looked_at": f"{looked}T10:00:00"})


def _run(reg, dataset):
    from resource_explorer.activity_logger import log_analysis_run
    from resource_explorer.workflows.analysis import execute_and_record_open_data_analysis

    activity = log_analysis_run(reg, "open_data", dataset["slug"], str(dataset["title"]), "running", "Running…", ID, published=None)
    assert execute_and_record_open_data_analysis(dataset["slug"], ID, activity, registry=reg).status == "ok"
    _, rows = reg.query_step_annotations("open_data", dataset["slug"], ID)
    return rows[0]


def test_the_runner_is_built_in_and_calls_no_portal():
    assert analyses.refusal(ID) is None and analyses.price(ID, {"slug": "x"})["calls"] == 0


def test_a_run_reads_the_stated_cadence_and_the_refresh_history_and_stores_the_verdict(reg, store, dataset):
    _state(reg, dataset["slug"], "R/P1M")
    for looked, modified in (("2026-08-02", "2026-08-01"), ("2026-09-02", "2026-09-01"), ("2026-10-02", "2026-10-01")):
        _look(reg, dataset["slug"], looked, modified)
    row = _run(reg, dataset)
    m = row["detail"]["additionalProperties"]["measures"]
    assert row["result_state"] == "measured" and m["verdict"] == "kept" and m["cadence_checks"] == 1 and m["observations"] == 3


def test_a_run_with_one_refresh_is_not_enough_history(reg, store, dataset):
    _state(reg, dataset["slug"], "P1M")
    _look(reg, dataset["slug"], "2026-10-02", "2026-10-01")
    row = _run(reg, dataset)
    assert row["result_state"] == "not_measured" and row["summary"] == "not enough history (1 refresh)"


def test_a_run_with_no_cadence_in_the_record_is_nothing_found(reg, store, dataset):
    _state(reg, dataset["slug"], {"unexpected": "shape"})
    _look(reg, dataset["slug"], "2026-10-02", "2026-10-01")
    assert _run(reg, dataset)["result_state"] == "nothing_found"


def test_other_datasets_refreshes_are_not_read(reg, store, dataset):
    _state(reg, dataset["slug"], "P1M")
    _look(reg, "some-other-dataset", "2026-08-02", "2026-08-01")
    _look(reg, "some-other-dataset", "2026-09-02", "2026-09-01")
    assert _run(reg, dataset)["summary"] == "not enough history (no refresh yet)"


# ── the catalog ──────────────────────────────────────────────────────────────

def test_od_cadence_kept_is_built_and_still_answers_no_question_until_b5():
    import yaml

    cat = yaml.safe_load((Path(__file__).resolve().parents[1] / "resource_explorer/configdata/analysis_catalog.yaml").read_text())
    by = {a["id"]: a for a in cat["open_data_analyses"]}
    assert by[ID]["declaration"]["built"] is True and by["od_licence_usable"]["declaration"]["built"] is True
    assert by["od_profile"]["declaration"]["built"] is True and not by[ID].get("answers_questions")      # od_profile: built in B4
    assert "Not built" not in by[ID]["description"]


def test_a_dataset_that_lapsed_once_but_is_current_now_says_so():
    from resource_explorer.open_data.cadence_kept import judge

    r = judge("P1M", _looks(("2026-08-02", "2026-08-01"), ("2026-10-31", "2026-08-01"), ("2026-11-02", "2026-11-01")))
    assert r.measures["verdict"] == "late" and r.measures["worst_look"] == "2026-10-31"
    assert r.summary.endswith("; current now (last modified 2026-11-01, 1 day before the last look)")


def test_a_dataset_still_lapsed_at_the_last_look_is_not_called_current():
    from resource_explorer.open_data.cadence_kept import judge

    r = judge("P1M", _looks(("2026-08-02", "2026-08-01"), ("2026-11-15", "2026-08-01")))
    assert r.measures["verdict"] == "late" and "current now" not in r.summary
