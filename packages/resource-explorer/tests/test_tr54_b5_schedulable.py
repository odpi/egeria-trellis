"""TR-54 B5: the built open data checks are schedulable, the way other queued analyses are: a schedule on a dataset, the timer, "run now".

A scheduled run has no presser (or the schedule's creator, as the timer runs it): it reads NO per-user data, stores the same shared dataset
facts a pressed run stores, and reaches no investigation. The question row then reads the stored result, as it does after a press.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from test_tr54_b2_licence_usable import _set_licence, dataset  # noqa: E402,F401
from test_open_data import reg, store  # noqa: E402,F401

from resource_explorer.open_data import analyses  # noqa: E402

LICENCE = "od_licence_usable"


@pytest.fixture
def api(reg, monkeypatch):
    from fastapi.testclient import TestClient

    from resource_explorer.auth import create_access_token
    from resource_explorer.web.app import app

    monkeypatch.setattr("resource_explorer.registry.ProjectRegistry.__init__",
                        lambda self, db_path=None, **k: setattr(self, "__dict__", reg.__dict__) or None)
    return TestClient(app), {"Authorization": "Bearer " + create_access_token(user_id="sched-user", egeria_token="tok")}


def _save(client, headers, slug, analysis_id=LICENCE, **kw):
    return client.post(f"/api/schedules/open_data/{slug}", headers=headers, json={"analysis_id": analysis_id, "schedule": "daily", **kw})



@pytest.fixture
def a_reader_with_a_purpose(monkeypatch):
    """These tests are about the STORED result deciding the row. The licence row also needs a reader whose investigations state a licence
    need (W-B5-1); that reader's fit is tested with real callers in test_tr54_w_b5_1_reader_fit.py."""
    monkeypatch.setattr("resource_explorer.open_data.dataset_questions._reader_fit", lambda registry, store, d: {
        "fit": [{"investigation": "mine", "title": "Mine", "verdict": "fits", "words": "meets the stated licence needs"}], "fit_words": ""})


@pytest.mark.usefixtures("mock_egeria_client_connections")

def test_a_built_check_can_be_scheduled_on_a_dataset_without_any_egeria_publish_right(api, reg, dataset, monkeypatch):
    from resource_explorer.web.routes import curate

    monkeypatch.setattr(curate, "require_resource_write",
                        lambda *a, **k: pytest.fail("an open data schedule never publishes, so it asks for no Egeria write right"))
    client, headers = api
    r = _save(client, headers, dataset["slug"])
    assert r.status_code == 200, r.text
    stored = reg.get_schedules("open_data", dataset["slug"])
    assert [(s["analysis_id"], s["schedule"], s["enabled"], s["created_by"]) for s in stored] == [(LICENCE, "daily", 1, "sched-user")]


@pytest.mark.usefixtures("mock_egeria_client_connections")
def test_a_schedule_is_refused_in_words_for_what_could_never_run(api, reg, dataset, monkeypatch):
    client, headers = api
    ghost = _save(client, headers, "no-such-dataset")
    assert ghost.status_code == 404 and "No open_data 'no-such-dataset'" in ghost.json()["detail"]
    typo = _save(client, headers, dataset["slug"], "od_licence_usabel")
    assert typo.status_code == 422 and typo.json()["detail"] == analyses.refusal("od_licence_usabel")
    survey = _save(client, headers, dataset["slug"], target_kind="survey")
    assert survey.status_code == 422 and "analysis" in survey.json()["detail"]
    monkeypatch.delitem(analyses.BUILT_IN, "od_profile")                         # declared, and with no reader
    unbuilt = _save(client, headers, dataset["slug"], "od_profile")
    assert unbuilt.status_code == 422 and "has no reader yet" in unbuilt.json()["detail"]
    assert reg.get_schedules("open_data", dataset["slug"]) == []                 # none of them was stored


def test_the_scheduler_runs_the_check_stores_the_result_and_reads_no_per_user_data(reg, store, dataset, monkeypatch):
    from resource_explorer import scheduler

    _set_licence(reg, dataset["slug"], "cc-by-sa-4.0")
    monkeypatch.setattr(reg, "list_investigations", lambda *a, **k: pytest.fail("a scheduled run read the investigations"))
    monkeypatch.setattr(store, "get_data_request", lambda *a, **k: pytest.fail("a scheduled run read a data request"))
    name, location, errors = scheduler._execute("open_data", dataset["slug"], LICENCE, reg)
    assert errors == [] and name == dataset["title"]
    _, rows = reg.query_step_annotations("open_data", dataset["slug"], LICENCE)
    assert rows[0]["result_state"] == "measured"
    assert rows[0]["detail"]["additionalProperties"]["measures"]["requirements"]["share_alike"] == "required"
    assert "investigation" not in json.dumps(rows[0]) and "fit" not in rows[0]["detail"]["additionalProperties"]["measures"]


def test_a_scheduled_run_that_cannot_run_says_so_in_words_and_stores_nothing(reg, dataset, monkeypatch):
    from resource_explorer import scheduler

    # a portal that does not answer fails the run: the error is in the schedule's errors, never a quiet "ok", and nothing is stored
    monkeypatch.setattr("resource_explorer.open_data.service.scout_dataset",
                        lambda *a, **k: {"outcome": "could_not_scout", "words": "could not read from CKAN demo: connection refused"})
    _, _, errors = scheduler._execute("open_data", dataset["slug"], "external_data_scout", reg)
    assert len(errors) == 1 and "connection refused" in errors[0] and "were kept" in errors[0]
    assert reg.query_step_annotations("open_data", dataset["slug"], "external_data_scout")[1] == []


def test_a_missing_dataset_or_an_unrunnable_analysis_is_an_error_not_a_pass(reg, dataset):
    from resource_explorer import scheduler

    assert "No open data 'ghost'" in scheduler._execute("open_data", "ghost", LICENCE, reg)[2][0]
    assert "is not an open data analysis" in scheduler._execute("open_data", dataset["slug"], "nope", reg)[2][0]


@pytest.mark.usefixtures("mock_egeria_client_connections")
def test_the_timer_runs_a_due_schedule_writes_its_activity_and_the_question_row_reads_the_result(api, reg, store, dataset, a_reader_with_a_purpose):
    from resource_explorer import scheduler
    from resource_explorer.open_data import dataset_questions

    client, headers = api
    _set_licence(reg, dataset["slug"], "cc0-1.0")
    assert _save(client, headers, dataset["slug"]).status_code == 200
    with reg._conn() as conn:
        conn.execute("UPDATE resource_schedules SET next_run = '2000-01-01T00:00:00+00:00' WHERE entity_type = 'open_data'")
    scheduler._run_due()
    sched = reg.get_schedules("open_data", dataset["slug"])[0]
    assert sched["last_run_status"] == "ok" and sched["last_run_activity_id"]
    entry = reg.get_activity(sched["last_run_activity_id"])
    assert entry["entity_type"] == "open_data" and entry["status"] == "ok" and "Open Data Licence Usable" in entry["summary"]
    q = {x["id"]: x for x in dataset_questions.build(store.get_dataset(dataset["slug"]), registry=reg)["questions"]}["Q-0113"]
    assert q["state"] == "answered" and q["has_data"] is True                      # the scheduled run answered it, as a press would


@pytest.mark.usefixtures("mock_egeria_client_connections")
def test_run_now_on_an_open_data_schedule_runs_it_and_does_not_advance_the_slot(api, reg, dataset, monkeypatch):
    from resource_explorer.web.routes import curate

    monkeypatch.setattr(curate, "require_resource_write", lambda *a, **k: pytest.fail("no Egeria write right for an open data run"))
    client, headers = api
    _set_licence(reg, dataset["slug"], "cc-by-4.0")
    assert _save(client, headers, dataset["slug"]).status_code == 200
    before = reg.get_schedules("open_data", dataset["slug"])[0]["next_run"]
    r = client.post(f"/api/schedules/open_data/{dataset['slug']}/{LICENCE}/run", headers=headers)
    assert r.status_code == 200 and r.json()["status"] == "ok" and r.json()["next_run_advanced"] is False
    assert reg.get_schedules("open_data", dataset["slug"])[0]["next_run"] == before
    assert reg.query_step_annotations("open_data", dataset["slug"], LICENCE)[1][0]["result_state"] == "measured"
