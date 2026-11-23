"""TR-54 item B, step B1: a dataset analysis runs on the queue path, per dataset.

The queue kind `open_data_analysis_run` and its handler (the twin of the file-system one); `workflows.analysis.run_open_data_analysis` /
`execute_and_record_open_data_analysis`, which store ONE result per (dataset slug, analysis_id) and never publish; and the route a
dataset's checklist "Run" press uses, which enqueues and returns an activity_id (a run with none broke pollActivity). There is no
analysis logic here: B2-B4 register their runners. These tests register a stand-in runner, and a portal-calling one for the price.
No network, no Egeria; a temp SQLite registry.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from test_open_data import FakeTransport, _register_demo, reg, store  # noqa: E402,F401  (fixtures are shared)

from resource_explorer.open_data import analyses, service  # noqa: E402
from resource_explorer.run_queue import HANDLERS  # noqa: E402

STAND_IN = "od_licence_usable"          # a declared open_data analysis (built: false until B2)


@pytest.fixture(autouse=True)
def _clean_runners(monkeypatch):
    monkeypatch.setattr(analyses, "RUNNERS", {})


@pytest.fixture
def dataset(store):
    portal = _register_demo(store)
    res = service.import_datasets(store, [service.Chosen(portal["slug"], n) for n in
                                          [r["dataset_id"] for r in service.run_search(
                                              store, portal["slug"], terms="dataset", pages=1,
                                              transport=FakeTransport())["rows"]][:1]],
                                  work_list_name="b1", transport=FakeTransport(), egeria_lookup=lambda *a, **k: [])
    return store.list_datasets()[0]


def _register_stand_in(calls=0, state="measured"):
    def run(ctx):
        return analyses.OpenDataResult(state=state, summary=f"ran on {ctx.dataset['slug']}",
                                       measures={"licences_read": 1})
    analyses.register(STAND_IN, run, calls=lambda ds: calls)


# ── the queue kind ───────────────────────────────────────────────────────────

def test_the_kind_is_known_to_the_registry_and_has_a_handler():
    from resource_explorer.registry import ProjectRegistry
    assert "open_data_analysis_run" in ProjectRegistry.RUN_KINDS
    assert "open_data_analysis_run" in HANDLERS


def test_the_handler_runs_the_runner_stores_one_result_and_finishes_the_activity(reg, store, dataset, monkeypatch):
    from resource_explorer.activity_logger import log_analysis_run

    _register_stand_in()
    activity_id = log_analysis_run(reg, "open_data", dataset["slug"], dataset["title"], "running", "Running…", STAND_IN,
                                   published=None)
    monkeypatch.setattr("resource_explorer.registry.ProjectRegistry.__init__",
                        lambda self, db_path=None, **k: setattr(self, "__dict__", reg.__dict__) or None)
    outcome = HANDLERS["open_data_analysis_run"]({"slug": dataset["slug"], "analysis_id": STAND_IN}, activity_id)
    assert outcome.state == "succeeded", outcome.error
    entry = reg.get_activity(activity_id)
    assert entry["status"] == "ok" and "ran on" in entry["summary"]
    detail = json.loads(entry["detail"])
    assert detail["analysis_id"] == STAND_IN and detail["published"] is None          # stored, never published
    run, rows = reg.query_step_annotations("open_data", dataset["slug"], STAND_IN)
    assert run == activity_id and len(rows) == 1                                      # one result per (dataset, analysis)
    assert rows[0]["result_state"] == "measured"


def test_a_second_run_replaces_nothing_it_adds_its_own_run_and_the_latest_is_read(reg, store, dataset):
    from resource_explorer.workflows.analysis import execute_and_record_open_data_analysis

    _register_stand_in()
    for activity in ("act-1", "act-2"):
        reg.write_activity(__import__("resource_explorer.registry", fromlist=["ActivityEntry"]).ActivityEntry(
            id=activity, ts="2026-11-23T00:00:00", operation="analysis_run", intent="assessment", entity_type="open_data",
            entity_slug=dataset["slug"], entity_name="x", entity_location="", status="running", summary="", detail="{}"))
        r = execute_and_record_open_data_analysis(dataset["slug"], STAND_IN, activity, registry=reg)
        assert r.status == "ok"
    run, rows = reg.query_step_annotations("open_data", dataset["slug"], STAND_IN)
    assert run == "act-2" and len(rows) == 1


def test_a_runner_that_raises_ends_the_activity_in_error_with_its_words_and_stores_nothing(reg, store, dataset):
    from resource_explorer.workflows.analysis import execute_and_record_open_data_analysis

    def boom(ctx):
        raise RuntimeError("portal said no")
    analyses.register(STAND_IN, boom, calls=lambda ds: 0)
    from resource_explorer.activity_logger import log_analysis_run
    activity_id = log_analysis_run(reg, "open_data", dataset["slug"], "t", "running", "Running…", STAND_IN, published=None)
    r = execute_and_record_open_data_analysis(dataset["slug"], STAND_IN, activity_id, registry=reg)
    assert r.status == "error" and "portal said no" in r.error
    entry = reg.get_activity(activity_id)
    assert entry["status"] == "error" and "portal said no" in entry["summary"]
    assert reg.query_step_annotations("open_data", dataset["slug"], STAND_IN) == ("", [])


def test_a_dataset_that_does_not_exist_is_an_error_in_words(reg, store):
    from resource_explorer.workflows.analysis import run_open_data_analysis

    _register_stand_in()
    r = run_open_data_analysis("ghost", STAND_IN, registry=reg)
    assert r.status == "error" and r.error == "Open data 'ghost' not found"


# ── an unknown or unbuilt analysis is refused in words ───────────────────────

def test_an_unknown_analysis_id_is_refused_in_words(reg, store, dataset):
    from resource_explorer.workflows.analysis import run_open_data_analysis

    r = run_open_data_analysis(dataset["slug"], "no_such_analysis", registry=reg)
    assert r.status == "error"
    assert "'no_such_analysis' is not an open data analysis" in r.error and "od_licence_usable" in r.error


def test_a_declared_analysis_with_no_runner_says_it_is_not_built_never_ok(reg, store, dataset):
    from resource_explorer.workflows.analysis import run_open_data_analysis

    r = run_open_data_analysis(dataset["slug"], "od_profile", registry=reg)
    assert r.status == "error" and "'od_profile' is declared but has no reader yet" in r.error


# ── the price, before the press ──────────────────────────────────────────────

def test_an_analysis_that_calls_a_portal_shows_its_call_count_first():
    _register_stand_in(calls=3)
    p = analyses.price(STAND_IN, {"slug": "d"})
    assert p["calls"] == 3 and p["priced"] is True and "3 calls" in p["line"]


def test_an_analysis_that_calls_no_portal_says_so_not_zero_priced_as_free():
    _register_stand_in(calls=0)
    p = analyses.price(STAND_IN, {"slug": "d"})
    assert p["calls"] == 0 and "no portal call" in p["line"]


def test_the_price_of_an_unbuilt_analysis_is_not_priced_never_zero():
    p = analyses.price("od_profile", {"slug": "d"})
    assert p["priced"] is False and p["calls"] is None and "no reader yet" in p["line"]


# ── the route ────────────────────────────────────────────────────────────────

@pytest.fixture
def api(reg, monkeypatch):
    from fastapi.testclient import TestClient

    from resource_explorer.auth import create_access_token
    from resource_explorer.web.app import app
    from tests.zone_fakes import open_access

    monkeypatch.setattr("resource_explorer.registry.ProjectRegistry.__init__",
                        lambda self, db_path=None, **k: setattr(self, "__dict__", reg.__dict__) or None)
    open_access(monkeypatch, caller_if_none="b1-tester")
    headers = {"Authorization": "Bearer " + create_access_token(user_id="b1-tester", egeria_token="tok")}
    return TestClient(app), headers


@pytest.mark.usefixtures("signed_in_caller")
def test_the_route_enqueues_returns_an_activity_id_and_records_who_pressed(api, reg, dataset):
    client, headers = api
    _register_stand_in(calls=2)
    r = client.post(f"/api/open-data/datasets/{dataset['slug']}/analyses/{STAND_IN}/run?pressed_on=Checklist", headers=headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "started" and body["activity_id"] and body["run_id"]
    assert body["price"]["calls"] == 2
    queued = [q for q in reg.list_runs(limit=10) if q["id"] == body["run_id"]][0]
    assert queued["kind"] == "open_data_analysis_run" and queued["requested_by"] == "b1-tester"
    assert json.loads(queued["target"]) == {"slug": dataset["slug"], "analysis_id": STAND_IN, "pressed_on": "Checklist"}
    assert queued["result_ref"] == body["activity_id"]
    assert reg.get_activity(body["activity_id"])["status"] == "running"
    outcome = HANDLERS["open_data_analysis_run"](json.loads(queued["target"]), body["activity_id"])
    assert outcome.state == "succeeded", outcome.error
    assert reg.get_activity(body["activity_id"])["status"] == "ok"


@pytest.mark.usefixtures("signed_in_caller")
def test_the_route_refuses_in_words_before_it_queues_anything(api, reg, dataset):
    client, headers = api
    assert client.post(f"/api/open-data/datasets/ghost/analyses/{STAND_IN}/run", headers=headers).status_code == 404
    unknown = client.post(f"/api/open-data/datasets/{dataset['slug']}/analyses/nope/run", headers=headers)
    assert unknown.status_code == 400 and "is not an open data analysis" in unknown.json()["detail"]
    unbuilt = client.post(f"/api/open-data/datasets/{dataset['slug']}/analyses/od_profile/run", headers=headers)
    assert unbuilt.status_code == 400 and "no reader yet" in unbuilt.json()["detail"]
    assert [q for q in reg.list_runs(limit=20) if q["kind"] == "open_data_analysis_run"] == []


def test_the_route_needs_a_signed_in_person(api, dataset):
    client, _ = api
    r = client.post(f"/api/open-data/datasets/{dataset['slug']}/analyses/{STAND_IN}/run")
    assert r.status_code == 401


@pytest.mark.usefixtures("signed_in_caller")
def test_the_price_route_shows_the_call_count_before_the_press(api, dataset):
    client, headers = api
    _register_stand_in(calls=4)
    p = client.get(f"/api/open-data/datasets/{dataset['slug']}/analyses/{STAND_IN}/price", headers=headers).json()
    assert p["calls"] == 4 and "4 calls" in p["line"]
    assert client.get(f"/api/open-data/datasets/{dataset['slug']}/analyses/od_profile/price", headers=headers).json()["priced"] is False
