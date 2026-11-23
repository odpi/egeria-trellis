"""TR-54 B2: od_licence_usable, the first real dataset check.

Input: the dataset's stored licence (from import or scout). Output: the declared measure `licences_read` and a judgement of what the
licence requires (attribution, share-alike, commercial use), against a stated purpose where the dataset's investigation states one.
The judgement is only what the licence IDENTIFIER says (RE's table of SPDX / Creative Commons / ODC ids): any other id is "not judged:
licence <id> is not in RE's table", a licence stated in words is "not judged", and none stated is `nothing_found`. Never "usable" by guess.
No portal call; stored, never published.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from test_open_data import FakeTransport, _register_demo, reg, store  # noqa: E402,F401  (fixtures are shared)

from resource_explorer.open_data import analyses, licence_usable, service  # noqa: E402
from resource_explorer.open_data.absence import NOT_REPORTED  # noqa: E402

ID = "od_licence_usable"


def _ds(licence_id=NOT_REPORTED, text=None, **kw):
    d = {"slug": "d1", "title": "T", "licence_id": licence_id,
         "licence": {"attribution": NOT_REPORTED, "share_alike": NOT_REPORTED, "commercial": NOT_REPORTED},
         "record": {"_licence_text": text} if text else {}}
    d.update(kw)
    return d


# ── the judgements ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("licence_id,attribution,share_alike,commercial", [
    ("cc-by-4.0", "required", "not required", "allowed"),
    ("CC-BY-4.0", "required", "not required", "allowed"),                 # an id is read case-insensitively
    ("cc-by-sa-4.0", "required", "required", "allowed"),
    ("cc0-1.0", "not required", "not required", "allowed"),
    ("odc-odbl", "required", "required", "allowed"),
    ("cc-by-nc-4.0", "required", "not required", "not allowed"),
    ("https://creativecommons.org/licenses/by/4.0/", "required", "not required", "allowed"),   # ... and an IRI RE maps
])
def test_a_licence_in_the_table_is_judged_on_each_requirement(licence_id, attribution, share_alike, commercial):
    r = licence_usable.judge(_ds(licence_id))
    assert r.state == "measured" and r.measures["licences_read"] == 1 and r.measures["judged"] is True
    assert r.measures["requirements"] == {"attribution": attribution, "share_alike": share_alike, "commercial_use": commercial}
    assert "not judged" not in r.summary


def test_a_licence_id_not_in_the_table_is_not_judged_and_never_called_usable():
    r = licence_usable.judge(_ds("OGL-Canada-2.0"))
    assert r.state == "measured" and r.measures["licences_read"] == 1 and r.measures["judged"] is False
    assert r.summary == "not judged: licence OGL-Canada-2.0 is not in RE's table"
    assert "requirements" not in r.measures and "usable" not in r.summary.lower()


def test_dkan_usa_gov_url_is_not_judged_because_re_does_not_map_it():
    r = licence_usable.judge(_ds("https://www.usa.gov/publicdomain/label/1.0/"))
    assert r.measures["judged"] is False
    assert r.summary == "not judged: licence https://www.usa.gov/publicdomain/label/1.0/ is not in RE's table"


def test_a_licence_stated_in_words_is_read_but_not_judged_and_not_called_no_licence():
    r = licence_usable.judge(_ds(text="Licence Ouverte (Etalab)"))
    assert r.state == "measured" and r.measures["licences_read"] == 1 and r.measures["judged"] is False
    assert "licence stated in words, not identified" in r.summary and "not judged" in r.summary
    assert "no licence" not in r.summary.lower() and "Licence Ouverte (Etalab)" in r.explanation


def test_no_licence_stated_is_nothing_found_not_not_usable():
    r = licence_usable.judge(_ds())
    assert r.state == "nothing_found" and r.measures["licences_read"] == 0 and r.measures["judged"] is False
    assert r.summary == "the portal states no licence for this dataset"
    assert "usable" not in r.summary.lower()


def test_the_judgement_comes_from_the_id_not_from_flags_stored_on_the_row():
    ds = _ds("OGL-Canada-2.0")
    ds["licence"] = {"attribution": True, "share_alike": False, "commercial": True}          # stored, but the id is not in the table
    assert licence_usable.judge(ds).measures["judged"] is False


# ── a stated purpose ─────────────────────────────────────────────────────────

def test_the_stored_judgement_carries_dataset_facts_only_never_a_fit_or_an_investigation():
    r = licence_usable.judge(_ds("cc-by-4.0"))
    assert "fit" not in r.measures and "purpose" not in json.dumps(r.measures).lower()
    assert r.summary == "cc-by-4.0: attribution required; share-alike not required; commercial use allowed"


@pytest.mark.parametrize("licence_id,need,verdict", [
    ("cc-by-4.0", {"commercial": True}, "fits"),
    ("cc-by-nc-4.0", {"commercial": True}, "does_not_fit"),
    ("cc-by-sa-4.0", {"share_alike": False}, "does_not_fit"),
    ("cc0-1.0", {"commercial": True, "share_alike": False}, "fits"),
    ("OGL-Canada-2.0", {"commercial": True}, "could_not_check"),
])
def test_a_licence_is_judged_against_the_purpose_the_investigation_states(licence_id, need, verdict):
    purpose = {"investigation": "water", "title": "Water study", "licence": need}
    fit = licence_usable.fit(licence_usable.judge(_ds(licence_id)).measures, [purpose])
    assert [f["verdict"] for f in fit] == [verdict] and fit[0]["investigation"] == "water" and fit[0]["title"] == "Water study"


def test_a_licence_stated_in_words_could_not_be_checked_against_a_purpose():
    measures = licence_usable.judge(_ds(text="Licence Ouverte (Etalab)")).measures
    fit = licence_usable.fit(measures, [{"investigation": "w", "title": "W", "licence": {"commercial": True}}])
    assert fit[0]["verdict"] == "could_not_check"


# ── the runner on the queue path ─────────────────────────────────────────────

@pytest.fixture
def dataset(store):
    portal = _register_demo(store)
    names = [r["dataset_id"] for r in service.run_search(store, portal["slug"], terms="dataset", pages=1,
                                                         transport=FakeTransport())["rows"]][:1]
    service.import_datasets(store, [service.Chosen(portal["slug"], n) for n in names], work_list_name="b2",
                            transport=FakeTransport(), egeria_lookup=lambda *a, **k: [])
    return store.list_datasets()[0]


def _set_licence(reg, slug, licence_id):
    with reg._conn() as conn:
        conn.execute("UPDATE open_data SET licence_id = ? WHERE slug = ?", (licence_id, slug))


def _activity(reg, ds):
    from resource_explorer.activity_logger import log_analysis_run
    return log_analysis_run(reg, "open_data", ds["slug"], str(ds["title"]), "running", "Running…", ID, published=None)


def test_the_built_in_runner_is_found_without_registration_and_calls_no_portal():
    assert analyses.refusal(ID) is None
    p = analyses.price(ID, {"slug": "x"})
    assert p["priced"] is True and p["calls"] == 0 and "no portal call" in p["line"]


def test_other_declared_analyses_are_still_not_built():
    assert "has no reader yet" in analyses.refusal("od_profile")


def test_a_run_stores_one_result_with_the_declared_measure_and_publishes_nothing(reg, store, dataset):
    from resource_explorer.workflows.analysis import execute_and_record_open_data_analysis

    _set_licence(reg, dataset["slug"], "cc-by-sa-4.0")
    activity = _activity(reg, dataset)
    r = execute_and_record_open_data_analysis(dataset["slug"], ID, activity, registry=reg)
    assert r.status == "ok", r.error
    entry = reg.get_activity(activity)
    assert entry["status"] == "ok" and json.loads(entry["detail"])["published"] is None
    run, rows = reg.query_step_annotations("open_data", dataset["slug"], ID)
    assert run == activity and len(rows) == 1 and rows[0]["result_state"] == "measured"
    detail = rows[0]["detail"]["additionalProperties"]
    assert detail["measures"]["licences_read"] == 1 and detail["measures"]["requirements"]["share_alike"] == "required"


def test_a_dataset_with_no_licence_stores_nothing_found(reg, store, dataset):
    from resource_explorer.workflows.analysis import execute_and_record_open_data_analysis

    _set_licence(reg, dataset["slug"], NOT_REPORTED)
    activity = _activity(reg, dataset)
    assert execute_and_record_open_data_analysis(dataset["slug"], ID, activity, registry=reg).status == "ok"
    _, rows = reg.query_step_annotations("open_data", dataset["slug"], ID)
    assert rows[0]["result_state"] == "nothing_found"


def test_an_investigation_with_no_stated_licence_need_adds_no_purpose(reg, store, dataset):
    inv = reg.create_investigation("Water study")["slug"]
    service._add_investigation_member(reg, inv, dataset["slug"], "in scope")
    store.save_data_request(inv, {"subjects": ["water"]}, "tester")
    assert licence_usable.purposes_for(reg, store, dataset) == []


# ── the catalog ──────────────────────────────────────────────────────────────

def test_only_od_licence_usable_is_flipped_to_built_and_no_analysis_answers_a_question_yet():
    import yaml

    cat = yaml.safe_load((Path(__file__).resolve().parents[1] / "resource_explorer/configdata/analysis_catalog.yaml").read_text())
    by = {a["id"]: a for a in cat["open_data_analyses"]}
    assert by[ID]["declaration"]["built"] is True
    assert by["od_profile"]["declaration"]["built"] is False          # od_cadence_kept was built in B3
    assert not by[ID].get("answers_questions")                      # stays false until a catalog question lists it (B5)
    assert [m["name"] for m in by[ID]["declaration"]["measures"]] == ["licences_read"]
    assert "Not built" not in by[ID]["description"]


# ── E1-1 (Architect nit): a placeholder word is not a licence id ─────────────

@pytest.mark.parametrize("word", ["None", "none", "Unknown", "UNKNOWN", "Other", "N/A", "n/a", "TBD", "tbd", "null", "not specified"])
def test_a_placeholder_word_is_not_a_licence_id_and_the_raw_word_is_kept(word):
    from resource_explorer.open_data.licences import classify_licence

    c = classify_licence(word)
    assert c.id == NOT_REPORTED and c.text == NOT_REPORTED and c.placeholder == word.strip()


def test_a_real_id_or_text_has_no_placeholder_note():
    from resource_explorer.open_data.licences import classify_licence

    for raw in ("cc-by-4.0", "OGL-Canada-2.0", "Licence Ouverte (Etalab)", "https://creativecommons.org/licenses/by/4.0/"):
        assert classify_licence(raw).placeholder is None


def test_a_licence_with_no_id_and_no_text_reads_as_none_stated():
    from resource_explorer.open_data.licences import licence_statement

    assert licence_statement({"licence_id": NOT_REPORTED, "record": {}})["state"] == "not_stated"
    assert licence_usable.judge(_ds()).state == "nothing_found"


def test_the_adapter_reads_a_placeholder_as_no_licence_and_notes_the_raw_word():
    from resource_explorer.open_data.dcat_ap import DcatApAdapter

    row = DcatApAdapter("https://x.example/data.json", FakeTransport()).normalise(
        {"title": "t", "identifier": "i", "license": "None"})
    assert row["licence_id"] == NOT_REPORTED and row["licence_text"] == NOT_REPORTED
    assert "'None'" in row["record"]["_licence_note"] and "states no licence" in row["record"]["_licence_note"]


# ── the purpose is read for the person who pressed, through the queue's own wrapper ──

def _private_investigation_of(reg, owner, dataset_slug, store, need):
    """A creator-only investigation owned by `owner` that holds the dataset and states a licence need."""
    from resource_explorer.a2a_auth import CallerIdentity, current_caller

    reset = current_caller.set(CallerIdentity(user_id=owner, egeria_token=None, auth_source="app-jwt"))
    try:
        inv = reg.create_investigation("Private water study", project_classification="PersonalProject")
    finally:
        current_caller.reset(reset)
    assert inv["visibility"] == "creator-only"
    service._add_investigation_member(reg, inv["slug"], dataset_slug, "in scope")
    store.save_data_request(inv["slug"], {"licence": need}, owner)
    return inv["slug"]


def _purposes_in_the_queue_wrapper(reg, store, dataset, requested_by):
    """`purposes_for` inside `run_queue._run_as_requester`, the wrapper every queued open data run executes in: it sets the
    presser as `current_caller` before acting as Daemon(RUN_QUEUE), so the registry's per-user scoping reads the presser."""
    from resource_explorer import run_queue

    with run_queue._run_as_requester({"requested_by": requested_by}):
        return licence_usable.purposes_for(reg, store, dataset)


@pytest.mark.usefixtures("mock_egeria_client_connections")
def test_the_pressers_own_private_investigation_is_found_and_another_persons_is_not(reg, store, dataset):
    inv = _private_investigation_of(reg, "user-a", dataset["slug"], store, {"commercial": True})
    assert [p["investigation"] for p in _purposes_in_the_queue_wrapper(reg, store, dataset, "user-a")] == [inv]
    assert _purposes_in_the_queue_wrapper(reg, store, dataset, "user-b") == []


# ── W-B2-2: the stored result is shared, so it holds dataset facts only; the fit is computed per viewer at read time ──

def _press(reg, dataset, requested_by):
    """One queued press, in the queue's own wrapper (the presser is current_caller, then Daemon(RUN_QUEUE))."""
    from resource_explorer import run_queue
    from resource_explorer.workflows.analysis import execute_and_record_open_data_analysis

    with run_queue._run_as_requester({"requested_by": requested_by}):
        activity = _activity(reg, dataset)
        assert execute_and_record_open_data_analysis(dataset["slug"], ID, activity, registry=reg).status == "ok"
    return activity


def _as(user_id):
    from contextlib import contextmanager

    from resource_explorer.a2a_auth import CallerIdentity, current_caller

    @contextmanager
    def cm():
        reset = current_caller.set(CallerIdentity(user_id=user_id, egeria_token=None, auth_source="app-jwt"))
        try:
            yield
        finally:
            current_caller.reset(reset)
    return cm()


@pytest.mark.usefixtures("mock_egeria_client_connections")
def test_the_stored_annotation_of_a_press_holds_no_investigation_slug_title_or_fit(reg, store, dataset):
    _set_licence(reg, dataset["slug"], "cc-by-nc-4.0")
    inv = _private_investigation_of(reg, "user-a", dataset["slug"], store, {"commercial": True})
    activity = _press(reg, dataset, "user-a")
    _, rows = reg.query_step_annotations("open_data", dataset["slug"], ID)
    stored = json.dumps(rows[0])
    assert inv not in stored and "Private water study" not in stored and "fit" not in rows[0]["detail"]["additionalProperties"]["measures"]
    assert rows[0]["detail"]["additionalProperties"]["measures"]["requirements"]["commercial_use"] == "not allowed"
    entry = json.dumps(reg.get_activity(activity))                       # the Activity summary names no title either
    assert "Private water study" not in entry and inv not in entry


@pytest.mark.usefixtures("mock_egeria_client_connections")
def test_a_read_as_the_investigations_owner_shows_the_fit_and_a_read_as_someone_else_does_not(reg, store, dataset):
    _set_licence(reg, dataset["slug"], "cc-by-nc-4.0")
    inv = _private_investigation_of(reg, "user-a", dataset["slug"], store, {"commercial": True})
    _press(reg, dataset, "user-a")
    with _as("user-a"):
        mine = licence_usable.view(reg, store, store.get_dataset(dataset["slug"]))
    assert [(f["investigation"], f["verdict"]) for f in mine["fit"]] == [(inv, "does_not_fit")]
    with _as("user-b"):
        theirs = licence_usable.view(reg, store, store.get_dataset(dataset["slug"]))
    assert theirs["fit"] == [] and theirs["requirements"] == mine["requirements"]       # the facts are shared, the fit is not
    assert "Private water study" not in json.dumps(theirs) and inv not in json.dumps(theirs)


@pytest.mark.usefixtures("mock_egeria_client_connections")
def test_a_reader_with_no_identity_gets_no_fit_not_the_shared_view_of_everyones_investigations(reg, store, dataset):
    _set_licence(reg, dataset["slug"], "cc-by-nc-4.0")
    _private_investigation_of(reg, "user-a", dataset["slug"], store, {"commercial": True})
    _press(reg, dataset, "user-a")
    seen = licence_usable.view(reg, store, store.get_dataset(dataset["slug"]))          # no caller: "" would see everything
    assert seen["fit"] == [] and seen["fit_words"] == "fit not judged: no signed-in reader"


@pytest.mark.usefixtures("mock_egeria_client_connections")
def test_a_scheduled_run_with_no_presser_reads_no_per_user_data_and_stores_the_requirements(reg, store, dataset, monkeypatch):
    from resource_explorer import run_queue
    from resource_explorer.workflows.analysis import execute_and_record_open_data_analysis

    _set_licence(reg, dataset["slug"], "cc-by-4.0")
    _private_investigation_of(reg, "user-a", dataset["slug"], store, {"commercial": True})
    monkeypatch.setattr(reg, "list_investigations", lambda *a, **k: pytest.fail("a run read the investigations"))
    monkeypatch.setattr(store, "get_data_request", lambda *a, **k: pytest.fail("a run read a data request"))
    with run_queue._run_as_requester({"requested_by": ""}):
        assert execute_and_record_open_data_analysis(dataset["slug"], ID, _activity(reg, dataset), registry=reg).status == "ok"
    _, rows = reg.query_step_annotations("open_data", dataset["slug"], ID)
    assert rows[0]["detail"]["additionalProperties"]["measures"]["requirements"]["attribution"] == "required"


@pytest.mark.usefixtures("mock_egeria_client_connections")
def test_the_dataset_api_carries_the_check_for_this_reader_only(reg, store, dataset, monkeypatch):
    from fastapi.testclient import TestClient

    from resource_explorer.web import routes
    from resource_explorer.web.app import app

    monkeypatch.setattr(routes.open_data, "_store", lambda: store)
    client = TestClient(app)
    assert client.get(f"/api/open-data/datasets/{dataset['slug']}").json()["licence_check"] is None      # not run yet: not a verdict
    _set_licence(reg, dataset["slug"], "cc-by-4.0")
    _press(reg, dataset, "user-a")
    body = client.get(f"/api/open-data/datasets/{dataset['slug']}").json()
    assert body["licence_check"]["requirements"]["attribution"] == "required" and body["licence_check"]["fit"] == []
