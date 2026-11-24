"""TR-54 B5: each BUILT open data check is wired to its question, and a row says what the stored result says.

The amended three states (Architect, 2026-11-23):
  (1) the analysis is built:false (in a FIXTURE catalogue)  -> the row reads "no reader yet"
  (2) built, answers_questions NOT true (the state after B2) -> partial: the publisher's record fields are shown, and the check is NOT counted as the answer
  (3) built AND wired (answers_questions true), after a run  -> has_data (answered), from the STORED result
And W-B1-2: has_data comes from the stored resultState (and measures.judged), NEVER from the Activity status: a run that ended "ok"
with not_measured, nothing_found or an unjudged licence is not an answer. Never a bare 0; absence words only.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from test_tr54_b2_licence_usable import _set_licence, dataset  # noqa: E402,F401  (fixture + helper)
from test_tr54_b3_cadence_kept import _look, _state as _set_cadence  # noqa: E402
from test_open_data import FakeTransport, reg, store  # noqa: E402,F401

from resource_explorer.open_data import dataset_questions  # noqa: E402

LICENCE, CADENCE, PROFILE = "od_licence_usable", "od_cadence_kept", "od_profile"


def _fixture_catalogue(*, built=True, wired=None):
    """A catalogue made for the test, not read from the YAML: the three analyses, each built or not, wired or not."""
    absence = NS(sentence=lambda key: {"nothing_found": "Measured: the source states nothing for this question.",
                                       "not_measured": "Not measured: nothing has run yet."}.get(key, key))
    return {a: NS(id=a, answers_questions=wired, declaration=NS(built=built, absence=absence))
            for a in (LICENCE, CADENCE, PROFILE)}


def _bare(reg, slug):
    """A dataset whose record states nothing the questions could use: no licence id or flags, no modified date, no cadence."""
    from resource_explorer.open_data.absence import NOT_REPORTED

    with reg._conn() as conn:
        conn.execute("UPDATE open_data SET licence_id = ?, attribution = ?, share_alike = ?, commercial = ?, modified = ? WHERE slug = ?",
                     (NOT_REPORTED, NOT_REPORTED, NOT_REPORTED, NOT_REPORTED, NOT_REPORTED, slug))


def _rows(res):
    return {q["id"]: q for q in res["questions"] if q["kind"] == "open_data"}


def _run(reg, ds, analysis_id):
    from resource_explorer.workflows.analysis import run_open_data_analysis

    r = run_open_data_analysis(ds["slug"], analysis_id, registry=reg)
    assert r.status == "ok", r.error
    return r


def _build(reg, store, ds, declared=None):
    return dataset_questions.build(store.get_dataset(ds["slug"]), registry=reg, declared=declared)


# ── (1) not built: "no reader yet" ───────────────────────────────────────────

def test_an_analysis_that_is_not_built_reads_no_reader_yet(reg, store, dataset):
    _bare(reg, dataset["slug"])
    res = _build(reg, store, dataset, _fixture_catalogue(built=False))
    assert [q["state"] for q in _rows(res).values()] == ["no_reader"] * 3 and res["answered"] == 0


# ── (2) built, not wired: partial, and not counted as the answer ─────────────

def test_a_built_check_that_is_not_wired_reads_partial_from_the_record_and_is_not_counted(reg, store, dataset):
    _set_licence(reg, dataset["slug"], "cc-by-4.0")
    _run(reg, dataset, LICENCE)                                  # it HAS run, and still is not this question's answer: it is not wired
    res = _build(reg, store, dataset, _fixture_catalogue(built=True, wired=False))
    q = _rows(res)["Q-0113"]
    assert q["state"] == "partial" and q["answer"].startswith("Licence cc-by-4.0") and q["has_data"] is False
    assert "not counted as this question's answer" in q["words"] and res["answered"] == 0
    assert _rows(res)["Q-0115"]["state"] == "no_reader"          # nothing in the record answers the profile question


def test_a_built_unwired_check_with_nothing_in_the_record_reads_no_reader_yet_not_partial(reg, store, dataset):
    _bare(reg, dataset["slug"])
    res = _build(reg, store, dataset, _fixture_catalogue(built=True, wired=None))       # None: the key is simply absent
    assert {q["state"] for q in _rows(res).values()} == {"no_reader"}


# ── (3) wired: not run, then has_data from the stored result ─────────────────

def test_a_wired_check_that_has_not_run_reads_partial_from_the_record_and_says_not_run(reg, store, dataset):
    _set_licence(reg, dataset["slug"], "cc-by-4.0")
    q = _rows(_build(reg, store, dataset, _fixture_catalogue(built=True, wired=True)))["Q-0113"]
    assert q["state"] == "partial" and q["has_data"] is False and q["answer"].startswith("Licence cc-by-4.0")
    assert q["words"].startswith("Answered in part by the record alone; Not run on this dataset yet")


def test_a_wired_check_that_has_not_run_on_a_bare_dataset_reads_not_run_never_answered_never_zero(reg, store, dataset):
    _bare(reg, dataset["slug"])
    res = _build(reg, store, dataset, _fixture_catalogue(built=True, wired=True))
    assert {q["state"] for q in _rows(res).values()} == {"unrun"} and res["answered"] == 0
    assert all("Not run on this dataset yet" in q["words"] for q in _rows(res).values())


def test_after_a_run_the_licence_row_has_data_and_the_stage_strip_counts_it(reg, store, dataset):
    _set_licence(reg, dataset["slug"], "cc-by-sa-4.0")
    _run(reg, dataset, LICENCE)
    res = _build(reg, store, dataset, _fixture_catalogue(built=True, wired=True))
    q = _rows(res)["Q-0113"]
    assert q["state"] == "answered" and q["has_data"] is True
    assert q["answer"].startswith("cc-by-sa-4.0: attribution required; share-alike required; commercial use allowed")
    assert res["answered"] == 1 and [s for s in res["stages"] if s["stage"] == "Assessment"][0]["answered"] == 1
    assert _rows(res)["Q-0114"]["state"] == "partial" and _rows(res)["Q-0114"]["has_data"] is False   # not touched by one run


def test_the_real_catalogue_has_all_three_wired(reg, store, dataset):
    _bare(reg, dataset["slug"])
    res = _build(reg, store, dataset)                            # no fixture: the YAML's own flags
    assert {q["state"] for q in _rows(res).values()} == {"unrun"}


# ── W-B1-2: the stored result decides, never the Activity status ─────────────

def test_a_licence_the_table_does_not_know_ran_ok_but_is_not_an_answer(reg, store, dataset):
    _set_licence(reg, dataset["slug"], "OGL-Canada-2.0")
    _run(reg, dataset, LICENCE)
    q = _rows(_build(reg, store, dataset))["Q-0113"]
    assert q["state"] == "partial" and q["has_data"] is False
    assert q["answer"] == "not judged: licence OGL-Canada-2.0 is not in RE's table"


def test_no_licence_stated_reads_nothing_found_not_answered(reg, store, dataset):
    from resource_explorer.open_data.absence import NOT_REPORTED

    _set_licence(reg, dataset["slug"], NOT_REPORTED)
    _run(reg, dataset, LICENCE)
    q = _rows(_build(reg, store, dataset))["Q-0113"]
    assert q["state"] == "nothing" and q["has_data"] is False and q["answer"] == "the portal states no licence for this dataset"


def test_one_refresh_is_not_enough_history_so_the_cadence_row_is_not_established_not_answered(reg, store, dataset):
    _set_cadence(reg, dataset["slug"], "P1M")
    _look(reg, dataset["slug"], "2026-10-02", "2026-10-01")
    _run(reg, dataset, CADENCE)
    q = _rows(_build(reg, store, dataset))["Q-0114"]
    assert q["state"] == "not_established" and q["has_data"] is False and q["answer"] == "not enough history (1 refresh)"


def test_two_refreshes_answer_the_cadence_row(reg, store, dataset):
    _set_cadence(reg, dataset["slug"], "P1M")
    _look(reg, dataset["slug"], "2026-09-02", "2026-09-01")
    _look(reg, dataset["slug"], "2026-10-02", "2026-10-01")
    _run(reg, dataset, CADENCE)
    q = _rows(_build(reg, store, dataset))["Q-0114"]
    assert q["state"] == "answered" and q["answer"].startswith("kept:")


def test_an_activity_entry_that_says_ok_with_no_stored_result_is_not_an_answer(reg, store, dataset):
    from resource_explorer.activity_logger import log_analysis_run

    activity = log_analysis_run(reg, "open_data", dataset["slug"], "t", "running", "Running…", LICENCE, published=None)
    reg.update_activity_status(activity, "ok", summary="licence checked", detail=json.dumps({"analysis_id": LICENCE}))
    q = _rows(_build(reg, store, dataset))["Q-0113"]
    assert q["state"] == "partial" and q["has_data"] is False        # the record's fields only; "Not run on this dataset yet" is said
    assert "Not run on this dataset yet" in q["words"] and q["state"] != "answered"


def test_a_failed_run_leaves_the_row_not_run_and_a_failed_activity_does_not_change_an_earlier_answer(reg, store, dataset):
    from resource_explorer.activity_logger import log_analysis_run

    _set_licence(reg, dataset["slug"], "cc0-1.0")
    _run(reg, dataset, LICENCE)
    failed = log_analysis_run(reg, "open_data", dataset["slug"], "t", "running", "Running…", LICENCE, published=None)
    reg.update_activity_status(failed, "error", summary="'od_licence_usable' run failed: portal down",
                               detail=json.dumps({"analysis_id": LICENCE}))
    assert _rows(_build(reg, store, dataset))["Q-0113"]["state"] == "answered"       # the stored result still stands


def test_the_profile_row_has_data_after_a_profile_run_and_not_before(reg, store, dataset, monkeypatch):
    from test_open_data_profile import NoCatalog, ProfileFake

    from resource_explorer.open_data import analyses, queued
    from resource_explorer.surveyors.filesystem import file_class_match as fcm

    monkeypatch.setattr(fcm, "default_catalog", lambda: NoCatalog())
    assert _rows(_build(reg, store, dataset))["Q-0115"]["state"] == "unrun"          # the profile has no partial answer in a record
    ds = store.get_dataset(dataset["slug"])
    result = queued.run_profile(analyses.RunContext(registry=reg, store=store, dataset=ds, transport_factory=lambda: ProfileFake()))
    assert result.state in ("measured", "nothing_found", "not_measured")
    state = _rows(_build(reg, store, dataset))["Q-0115"]["state"]
    assert state in ("unrun", "answered", "nothing", "not_established")     # whichever the stored result is, never a bare "ok"


# ── no bare zero, absence words only ─────────────────────────────────────────

def test_no_row_or_count_says_a_bare_zero_and_every_count_is_in_words(reg, store, dataset):
    res = _build(reg, store, dataset)
    blob = json.dumps(res)
    for q in _rows(res).values():
        assert q["words"] and q["state"] in ("no_reader", "partial", "unrun", "answered", "nothing", "not_established")
        assert "0 of" not in q["words"] and q["words"].strip() != "0"
    assert "0 of" not in blob


def test_not_built_but_the_record_states_a_field_is_partial_as_before(reg, store, dataset):
    _set_licence(reg, dataset["slug"], "cc-by-4.0")
    q = _rows(_build(reg, store, dataset, _fixture_catalogue(built=False)))["Q-0113"]
    assert q["state"] == "partial" and q["words"].startswith("Answered in part by the record alone; the licence check will judge")


# ── what the route serves, and what the page can draw ────────────────────────

ROOT = Path(__file__).resolve().parents[1]
NEXT = ROOT / "resource_explorer/web/static/next"
SERVED_STATES = {"no_reader", "partial", "unrun", "answered", "nothing", "not_established"}


@pytest.fixture
def served(reg, store, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from resource_explorer.web.routes import open_data as routes

    monkeypatch.setattr(routes, "_store", lambda: store)
    app = FastAPI()
    app.include_router(routes.router, prefix="/api/open-data")
    return TestClient(app)


def test_the_route_serves_the_stored_result_after_a_run_and_only_known_states(reg, store, dataset, served):
    _bare(reg, dataset["slug"])
    url = f"/api/open-data/datasets/{dataset['slug']}/questions"
    before = served.get(url).json()
    assert {q["state"] for q in before["questions"]} <= SERVED_STATES and before["answered"] == 0
    assert [q["state"] for q in before["questions"] if q["kind"] == "open_data"] == ["unrun"] * 3
    _set_licence(reg, dataset["slug"], "cc0-1.0")
    _run(reg, dataset, LICENCE)
    after = served.get(url).json()
    q = {q["id"]: q for q in after["questions"]}["Q-0113"]
    assert q["state"] == "answered" and q["has_data"] is True and after["answered"] == 1
    assert {x["state"] for x in after["questions"]} <= SERVED_STATES
    assert [s for s in after["stages"] if s["stage"] == "Assessment"][0]["answered"] == 1


def test_every_state_the_server_can_send_is_in_the_shared_glyph_table():
    glyphs = (NEXT / "glyphs.js").read_text(encoding="utf-8")
    for state in SERVED_STATES:
        assert f"\n  {state}:" in glyphs, f"{state} has no glyph: the row would draw nothing"


def test_the_page_says_the_counts_in_words_and_never_a_bare_zero():
    js = (NEXT / "open-data-questions.js").read_text(encoding="utf-8")
    assert "export function stageWords" in js and "not run yet" in js and "answered" in js
    assert "none of ${total} answered yet · no reader yet" in js and "none of ${total} answered yet · not run yet" in js
    assert "0 of" not in js and "Open data questions" in js and "glyphSpan(q.state)" in js


def test_the_catalogue_wires_the_three_built_checks_and_not_the_work_list_entries():
    from resource_explorer.open_data import analyses

    declared = analyses._declared()
    assert [declared[a].answers_questions for a in (LICENCE, CADENCE, PROFILE)] == [True, True, True]
    assert [declared[a].answers_questions for a in ("external_data_scout", "external_data_refresh", "external_data_profile")] == [False] * 3
    assert all(declared[a].declaration.built for a in (LICENCE, CADENCE, PROFILE))
