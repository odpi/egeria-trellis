"""The three open data questions: catalogue rows, the route's states for a dataset with and without
licence / modified / cadence in the record, and the served text of the section."""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from resource_explorer.open_data import dataset_questions, service
from resource_explorer.open_data.absence import NOT_REPORTED
from resource_explorer.open_data.store import OpenDataStore
from resource_explorer.registry import ProjectRegistry
from resource_explorer.surveyors.question_catalog_reader import all_question_ids, get_questions

ROOT = Path(__file__).resolve().parents[1]
NEXT = ROOT / "resource_explorer/web/static/next"
IDS = ("Q-0113", "Q-0114", "Q-0115")
LEADS = {"Q-0113": "od_licence_usable", "Q-0114": "od_cadence_kept", "Q-0115": "od_profile"}


def test_the_catalogue_has_the_three_rows_with_unique_ids_and_declared_analyses():
    qs = get_questions("open_data")
    assert [q["id"] for q in qs] == list(IDS)
    assert len(all_question_ids()) == len(set(all_question_ids()))
    cat = yaml.safe_load((ROOT / "resource_explorer/configdata/analysis_catalog.yaml").read_text())
    declared = {a["id"]: a for a in cat["open_data_analyses"]}
    for q in qs:
        lead = LEADS[q["id"]]
        assert q["answering"]["analysis_ids"] == [lead] and q["answering"]["kind"] == "gap"
        assert declared[lead]["declaration"]["built"] is False             # declared, no implementation
        assert q["levels"] == ["resource"] and q["catalog_history"].startswith("2026-11-02: added for open data")
    by = {q["id"]: q for q in qs}
    assert (by["Q-0113"]["stage"], by["Q-0114"]["stage"], by["Q-0115"]["stage"]) == ("Assessment", "Scouting", "Discovery")
    assert by["Q-0113"]["purposes"] == ["Select", "Use"] and "Governance" in by["Q-0113"]["perspectives"]


def test_other_resource_types_are_unchanged_by_the_new_section():
    assert "open_data" not in {q["id"] for q in get_questions("repo")}
    assert not any(q["id"] in IDS for t in ("repo", "database", "filesystem") for q in get_questions(t))


def test_the_csv_mirror_names_the_three_rows():
    import csv

    rows = {r["Question ID"]: r for r in csv.DictReader(open(ROOT / "docs/dr-egeria/resource_questions.csv", newline="", encoding="utf-8"))}
    for qid in IDS:
        assert rows[qid]["Resource Types"] == "open_data" and rows[qid]["Answering Analysis"].startswith("GAP:")


def _dataset(**kw):
    d = {"slug": "x", "portals": ["data_gov_ie"], "retrieved_at": "2026-11-02T10:00:00+00:00", "licence_id": NOT_REPORTED,
         "licence": {"attribution": NOT_REPORTED, "share_alike": NOT_REPORTED, "commercial": NOT_REPORTED},
         "modified": NOT_REPORTED, "record": {}}
    d.update(kw)
    return d


def _by(res):
    return {q["id"]: q for q in res["questions"]}


def test_a_dataset_with_nothing_stated_is_three_no_reader_rows_and_says_why():
    res = dataset_questions.build(_dataset())
    assert [q["state"] for q in res["questions"]] == ["no_reader"] * 3
    assert all(q["answer"] == "" and q["words"].startswith("No mechanism exists for this yet — ") for q in res["questions"])
    assert res["answered"] == 0 and res["partial"] == 0 and res["total"] == 3
    assert [s["stage"] for s in res["stages"]] == ["Scouting", "Discovery", "Assessment"]


def test_licence_and_modified_in_the_record_make_the_two_rows_partial_with_their_source():
    d = _dataset(licence_id="cc-by-4.0", licence={"attribution": True, "share_alike": False, "commercial": True},
                 modified="2026-10-30T02:00:47", record={"accrualPeriodicity": "R/P1Y"})
    by = _by(dataset_questions.build(d))
    assert by["Q-0113"]["state"] == "partial"
    assert by["Q-0113"]["answer"] == ("Licence cc-by-4.0 · attribution ✓ · share-alike ✗ · commercial ✓ — from the "
                                      "publisher's record at data_gov_ie, retrieved 2026-11-02")
    assert by["Q-0114"]["state"] == "partial"
    assert by["Q-0114"]["answer"].startswith("Last modified 2026-10-30; stated cadence R/P1Y — from the publisher's record")
    assert by["Q-0115"]["state"] == "no_reader"                         # the profile has no partial answer in a record


def test_a_licence_id_alone_or_a_modified_date_alone_is_still_partial_and_unknown_flags_say_so():
    only_id = _by(dataset_questions.build(_dataset(licence_id="https://open.fda.gov/license")))
    assert only_id["Q-0113"]["answer"].startswith("Licence https://open.fda.gov/license · attribution ? · share-alike ? · commercial ?")
    assert only_id["Q-0114"]["state"] == "no_reader"
    only_mod = _by(dataset_questions.build(_dataset(modified="2025-09-06")))
    assert only_mod["Q-0114"]["answer"].startswith("Last modified 2025-09-06 —") and only_mod["Q-0113"]["state"] == "no_reader"


@pytest.fixture
def client(tmp_path, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from resource_explorer.web.routes import open_data as routes

    reg = ProjectRegistry(db_path=str(tmp_path / "q.db"))
    store = OpenDataStore(reg)
    monkeypatch.setattr(routes, "_store", lambda: store)
    app = FastAPI()
    app.include_router(routes.router, prefix="/api/open-data")
    c = TestClient(app)
    c.store = store
    return c


def _add(store, slug, **kw):
    row = {"identifier": slug, "identifier_kind": "dcat_identifier", "title": slug, "publisher": NOT_REPORTED,
           "licence_id": kw.get("licence_id", NOT_REPORTED),
           "licence": kw.get("licence", {"attribution": NOT_REPORTED, "share_alike": NOT_REPORTED, "commercial": NOT_REPORTED}),
           "modified": kw.get("modified", NOT_REPORTED), "formats": NOT_REPORTED, "size": NOT_REPORTED,
           "record": kw.get("record", {})}
    store.add_dataset(slug=slug, row=row, portals=["p"], retrieved_at="2026-11-02T10:00:00+00:00", imported_by="t",
                      work_list_slug="wl", datacite=None)


def test_the_route_returns_the_three_with_and_without_the_record_fields(client):
    _add(client.store, "bare")
    _add(client.store, "stated", licence_id="cc-by-4.0", licence={"attribution": True, "share_alike": False, "commercial": True},
         modified="2026-10-30", record={"accrualPeriodicity": "R/P1M"})
    bare = client.get("/api/open-data/datasets/bare/questions").json()
    assert [q["state"] for q in bare["questions"]] == ["no_reader"] * 3 and bare["partial"] == 0
    stated = client.get("/api/open-data/datasets/stated/questions").json()
    assert {q["id"]: q["state"] for q in stated["questions"]} == {"Q-0113": "partial", "Q-0114": "partial", "Q-0115": "no_reader"}
    assert stated["partial"] == 2 and stated["total"] == 3
    assert client.get("/api/open-data/datasets/nope/questions").status_code == 404


def test_the_served_text_has_the_section_the_glyph_words_and_no_bare_zero():
    js = (NEXT / "open-data-questions.js").read_text(encoding="utf-8")
    assert "Open data questions" in js and "No mechanism exists for this yet" in js
    assert "glyphSpan(q.state)" in js and "data-od-stage-strip" in js
    assert "none of ${total} answered yet · no reader yet" in js and "0 of" not in js
    kind = (NEXT / "open-data-kind.js").read_text(encoding="utf-8")
    assert "mountQuestions(" in kind and "data-od-questions" in kind
    api = (ROOT / "resource_explorer/web/static/re-api.js").read_text(encoding="utf-8")
    assert "if (entityType === 'open_data') return `/api/open-data/datasets/${enc}/questions`;" in api
