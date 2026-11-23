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

def test_with_no_purpose_stated_the_requirements_are_listed_and_no_fit_is_judged():
    r = licence_usable.judge(_ds("cc-by-4.0"), purposes=[])
    assert r.measures["fit"] == [] and "no purpose stated" in r.explanation


@pytest.mark.parametrize("licence_id,need,verdict", [
    ("cc-by-4.0", {"commercial": True}, "fits"),
    ("cc-by-nc-4.0", {"commercial": True}, "does_not_fit"),
    ("cc-by-sa-4.0", {"share_alike": False}, "does_not_fit"),
    ("cc0-1.0", {"commercial": True, "share_alike": False}, "fits"),
    ("OGL-Canada-2.0", {"commercial": True}, "could_not_check"),
])
def test_a_licence_is_judged_against_the_purpose_the_investigation_states(licence_id, need, verdict):
    purpose = {"investigation": "water", "title": "Water study", "licence": need}
    r = licence_usable.judge(_ds(licence_id), purposes=[purpose])
    assert [f["verdict"] for f in r.measures["fit"]] == [verdict] and r.measures["fit"][0]["investigation"] == "water"
    assert "Water study" in r.summary


def test_a_licence_stated_in_words_could_not_be_checked_against_a_purpose():
    r = licence_usable.judge(_ds(text="Licence Ouverte (Etalab)"), purposes=[
        {"investigation": "w", "title": "W", "licence": {"commercial": True}}])
    assert r.measures["fit"][0]["verdict"] == "could_not_check"


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
    assert "has no reader yet" in analyses.refusal("od_cadence_kept")
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


def test_the_purpose_is_read_from_the_investigation_the_dataset_belongs_to(reg, store, dataset):
    from resource_explorer.workflows.analysis import execute_and_record_open_data_analysis

    inv = reg.create_investigation("Water study")["slug"]
    service._add_investigation_member(reg, inv, dataset["slug"], "in scope")
    store.save_data_request(inv, {"licence": {"commercial": True}}, "tester")
    _set_licence(reg, dataset["slug"], "cc-by-nc-4.0")
    activity = _activity(reg, dataset)
    assert execute_and_record_open_data_analysis(dataset["slug"], ID, activity, registry=reg).status == "ok"
    _, rows = reg.query_step_annotations("open_data", dataset["slug"], ID)
    fit = rows[0]["detail"]["additionalProperties"]["measures"]["fit"]
    assert fit[0]["verdict"] == "does_not_fit" and fit[0]["investigation"] == inv


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
    assert by["od_cadence_kept"]["declaration"]["built"] is False and by["od_profile"]["declaration"]["built"] is False
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
