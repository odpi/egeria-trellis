"""W-B5-1: Q-0113 is "usable for OUR PURPOSE, and what does it require of us". The stored check says what the licence requires; the PURPOSE is the
reader's own. So the row is answered only for a reader whose investigations state a licence need, with the requirements AND that reader's fit
(`licence_usable.view`, under the request's caller); with no stated purpose it is partial and not counted ("no purpose stated by your
investigations"); with no identity it is partial ("fit not judged: no signed-in reader"). Nothing is stored: the stored row is unchanged,
and B never sees anything of A's private investigation.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from test_tr147_investigation_visibility_guard import A, B, api, world  # noqa: E402,F401  (fixtures)
from test_open_data import reg, store  # noqa: E402,F401
from test_tr54_b2_licence_usable import _set_licence  # noqa: E402

from resource_explorer.open_data import dataset_questions, service  # noqa: E402

LICENCE = "od_licence_usable"


@pytest.fixture
def judged(reg, store, world):
    """A dataset in A's creator-only investigation (which states a commercial need), with a licence that does NOT allow commercial use, and the
    check run once: the stored result is shared dataset facts only."""
    from resource_explorer.workflows.analysis import run_open_data_analysis

    ds = store.datasets_in_work_list(world["private_data"])[0]
    service._add_investigation_member(reg, world["private"], ds["slug"], "in scope")
    _set_licence(reg, ds["slug"], "cc-by-nc-4.0")
    assert run_open_data_analysis(ds["slug"], LICENCE, registry=reg).status == "ok"
    return ds


def _row(client, headers, slug):
    r = client.get(f"/api/open-data/datasets/{slug}/questions", headers=headers)
    assert r.status_code == 200, r.text
    body = r.json()
    return body, {q["id"]: q for q in body["questions"]}["Q-0113"]


def _stored(reg, slug):
    return reg.query_step_annotations("open_data", slug, LICENCE)[1]


@pytest.mark.usefixtures("mock_egeria_client_connections")
def test_a_reader_with_a_stated_purpose_sees_answered_with_the_requirements_and_their_own_fit(api, reg, judged):
    client, tokens = api
    body, q = _row(client, tokens[A], judged["slug"])
    assert q["state"] == "answered" and q["has_data"] is True and body["answered"] >= 1
    assert q["answer"].startswith("cc-by-nc-4.0: attribution required; share-alike not required; commercial use not allowed")
    assert "A private" in q["answer"] and "does not fit" in q["answer"]            # THEIR investigation's name, THEIR verdict
    assert "commercial use not allowed" in q["answer"]


@pytest.mark.usefixtures("mock_egeria_client_connections")
def test_a_reader_with_no_stated_purpose_sees_partial_not_counted_and_nothing_of_a(api, reg, judged):
    client, tokens = api
    body, q = _row(client, tokens[B], judged["slug"])
    assert q["state"] == "partial" and q["has_data"] is False
    assert "usable for your purpose: no purpose stated by your investigations" in q["answer"] + " " + q["words"]
    assert q["answer"].startswith("cc-by-nc-4.0: attribution required")                # the requirements are still shown: they are facts
    assert "A private" not in json.dumps(body) and "private" not in q["answer"].lower()   # none of A's private investigation
    assert [x["state"] for x in body["questions"] if x["id"] == "Q-0113"] == ["partial"] and body["answered"] == 0


@pytest.mark.usefixtures("mock_egeria_client_connections")
def test_no_identity_is_partial_fit_not_judged_never_the_shared_view_of_everyones_purposes(reg, store, judged):
    from resource_explorer.a2a_auth import current_caller

    assert current_caller.get() is None                                              # a script or a daemon: nobody signed in
    ds = store.get_dataset(judged["slug"])
    q = {x["id"]: x for x in dataset_questions.build(ds, registry=reg, store=store)["questions"]}["Q-0113"]
    assert q["state"] == "partial" and q["has_data"] is False
    assert "fit not judged: no signed-in reader" in q["answer"] + " " + q["words"]
    assert "A private" not in json.dumps(q)                                           # "" would see everyone's investigations: it is not asked


@pytest.mark.usefixtures("mock_egeria_client_connections")
def test_nothing_is_stored_by_reading_the_row(api, reg, judged):
    client, tokens = api
    before = json.dumps(_stored(reg, judged["slug"]), sort_keys=True, default=str)
    for who in (A, B, A):
        _row(client, tokens[who], judged["slug"])
    assert json.dumps(_stored(reg, judged["slug"]), sort_keys=True, default=str) == before
    stored = json.dumps(_stored(reg, judged["slug"]))
    assert "A private" not in stored and "fit" not in _stored(reg, judged["slug"])[0]["detail"]["additionalProperties"]["measures"]


@pytest.mark.usefixtures("mock_egeria_client_connections")
def test_a_licence_the_table_does_not_know_is_still_not_an_answer_for_anyone(api, reg, judged):
    from resource_explorer.workflows.analysis import run_open_data_analysis

    _set_licence(reg, judged["slug"], "OGL-Canada-2.0")
    assert run_open_data_analysis(judged["slug"], LICENCE, registry=reg).status == "ok"
    client, tokens = api
    q = _row(client, tokens[A], judged["slug"])[1]
    assert q["state"] == "partial" and q["answer"] == "not judged: licence OGL-Canada-2.0 is not in RE's table"


@pytest.mark.usefixtures("mock_egeria_client_connections")
def test_the_other_two_rows_are_not_asked_for_a_purpose(api, reg, judged):
    from resource_explorer.workflows.analysis import run_open_data_analysis

    client, tokens = api
    with reg._conn() as conn:
        conn.execute("UPDATE open_data SET record_json = ? WHERE slug = ?", (json.dumps({"accrualPeriodicity": "P1M"}), judged["slug"]))
    for d in ("2026-09-02", "2026-10-02"):
        reg.record_step_run(judged["slug"], "external_change", f"{d}T10:00:00", entity_type="open_data", source="resource-explorer",
                            executor="local", metrics={"snapshot": {"fields": {"modified": d[:8] + "01"}}})
    assert run_open_data_analysis(judged["slug"], "od_cadence_kept", registry=reg).status == "ok"
    q = {x["id"]: x for x in _row(client, tokens[B], judged["slug"])[0]["questions"]}["Q-0114"]
    assert q["state"] == "answered" and "purpose" not in q["answer"]               # a cadence has no purpose to fit
