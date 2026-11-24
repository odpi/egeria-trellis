"""TR-54 B4: scout, profile and refresh are queued per-dataset runs, and od_profile is built.

A work-list press enqueues ONE run per dataset (never an inline loop in the request); each run is a per-dataset analysis
(`external_data_scout`, `external_data_refresh`, `external_data_profile`; `od_profile` is the same profile run reached from one
dataset). One portal that does not answer fails ONE run, in words that name the dataset and the portal, and leaves its earlier values
alone; the other runs finish. The old inline response shape ({scouted, rows ...}) is gone. Portal calls are priced before the press.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from test_open_data import FakeTransport, NO_EGERIA, _chosen, _register_demo, reg, store  # noqa: E402,F401
from test_open_data_profile import A, B, C, NoCatalog, ProfileFake  # noqa: E402

from resource_explorer.open_data import analyses, service  # noqa: E402
from resource_explorer.open_data.absence import NOT_REPORTED  # noqa: E402
from resource_explorer.run_queue import HANDLERS  # noqa: E402

PRESSES = [("scout", "external_data_scout"), ("refresh", "external_data_refresh"), ("profile", "external_data_profile")]


class FailsFor(ProfileFake):
    """A portal that answers for every dataset but one: its package_show (and any file of it) fails."""

    def __init__(self, bad):
        super().__init__()
        self.bad = bad

    def get_json(self, url, params=None, headers=None):
        if (params or {}).get("id") == self.bad:
            raise ConnectionError("connection refused")
        return super().get_json(url, params, headers)


@pytest.fixture
def three(store):
    portal = _register_demo(store)
    wl = service.import_datasets(store, _chosen(portal["slug"], A, B, C), work_list_name="b4", transport=ProfileFake(),
                                 egeria_lookup=NO_EGERIA)["work_list"]
    return wl


@pytest.fixture
def api(reg, monkeypatch):
    from fastapi.testclient import TestClient

    from resource_explorer.auth import create_access_token
    from resource_explorer.web.app import app
    from resource_explorer.web.routes import open_data as routes
    from tests.zone_fakes import open_access

    monkeypatch.setattr("resource_explorer.registry.ProjectRegistry.__init__",
                        lambda self, db_path=None, **k: setattr(self, "__dict__", reg.__dict__) or None)
    open_access(monkeypatch, caller_if_none="b4-tester")
    pressed = ProfileFake()
    monkeypatch.setattr(routes, "_transport", lambda: pressed)             # the press itself must not touch the portal
    headers = {"Authorization": "Bearer " + create_access_token(user_id="b4-tester", egeria_token="tok")}
    return TestClient(app), headers, pressed


def _run(reg, dataset_slug, analysis_id, transport):
    """What the worker does for one queued run: the handler's own function, with the portal transport a test controls."""
    from resource_explorer.activity_logger import log_analysis_run
    from resource_explorer.workflows.analysis import execute_and_record_open_data_analysis

    activity = log_analysis_run(reg, "open_data", dataset_slug, dataset_slug, "running", "Running…", analysis_id, published=None)
    result = execute_and_record_open_data_analysis(dataset_slug, analysis_id, activity, registry=reg,
                                                   transport_factory=lambda: transport)
    return activity, result


# ── the press: one queued run per dataset ────────────────────────────────────

@pytest.mark.usefixtures("signed_in_caller")
@pytest.mark.parametrize("verb,analysis_id", PRESSES)
def test_a_three_dataset_list_gives_three_queued_runs_and_the_press_calls_no_portal(api, reg, store, three, verb, analysis_id):
    client, headers, pressed = api
    r = client.post(f"/api/open-data/work-lists/{three}/{verb}", headers=headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["queued"] == 3 and len(body["runs"]) == 3 and body["analysis_id"] == analysis_id
    queued = [q for q in reg.list_runs(limit=20) if q["kind"] == "open_data_analysis_run"]
    assert len(queued) == 3 and {q["requested_by"] for q in queued} == {"b4-tester"}
    assert {json.loads(q["target"])["analysis_id"] for q in queued} == {analysis_id}
    assert {json.loads(q["target"])["slug"] for q in queued} == {d["slug"] for d in store.datasets_in_work_list(three)}
    assert all(reg.get_activity(run["activity_id"])["status"] == "running" for run in body["runs"])
    assert pressed.calls == []                                             # the request queued work, it did not do it


@pytest.mark.usefixtures("signed_in_caller")
@pytest.mark.parametrize("verb", ["scout", "refresh", "profile"])
def test_the_old_inline_response_shape_is_gone(api, three, verb):
    client, headers, _ = api
    body = client.post(f"/api/open-data/work-lists/{three}/{verb}", headers=headers).json()
    assert not ({"scouted", "could_not_scout", "rows", "datasets", "profiled", "not_profiled", "first_look", "changed"} & set(body))


@pytest.mark.usefixtures("signed_in_caller")
def test_the_press_is_priced_and_needs_a_signed_in_person_and_a_work_list(api, three):
    client, headers, _ = api
    assert client.post(f"/api/open-data/work-lists/{three}/scout", headers=headers).json()["price"]["line"] == "3 datasets × 1 call = 3 calls"
    assert client.post(f"/api/open-data/work-lists/{three}/refresh", headers=headers).json()["price"]["calls"] == 6
    assert "up to" in client.post(f"/api/open-data/work-lists/{three}/profile", headers=headers).json()["price"]["line"]
    assert client.post(f"/api/open-data/work-lists/{three}/scout").status_code == 401
    assert client.post("/api/open-data/work-lists/nope/scout", headers=headers).status_code == 404


def test_the_per_dataset_price_of_each_run_is_its_calls():
    assert analyses.price("external_data_scout", {"slug": "x"})["calls"] == 1
    assert analyses.price("external_data_refresh", {"slug": "x"})["calls"] == 2
    assert analyses.price("od_profile", {"slug": "x"})["calls"] == 6          # 1 listing, 3 data files, 2 documents: an upper bound


# ── one portal failure leaves the other runs done, and says which failed ─────

@pytest.mark.parametrize("analysis_id", ["external_data_scout", "external_data_refresh", "external_data_profile"])
def test_one_portal_failure_leaves_the_other_two_done_and_names_the_one_that_failed(reg, store, three, analysis_id):
    datasets = store.datasets_in_work_list(three)
    before = {d["slug"]: (d["publisher"], d["modified"], d["retrieved_at"]) for d in datasets}
    transport = FailsFor(B)
    done = {}
    for d in datasets:
        done[d["slug"]] = _run(reg, d["slug"], analysis_id, transport)
    bad = [d for d in datasets if B in d["identifier"]][0]
    failed = [slug for slug, (_, r) in done.items() if r.status == "error"]
    assert failed == [bad["slug"]] and sum(1 for _, r in done.values() if r.status == "ok") == 2
    entry = reg.get_activity(done[bad["slug"]][0])
    assert entry["status"] == "error"
    assert bad["title"] in entry["summary"] and "CKAN demo" in entry["summary"] and "connection refused" in entry["summary"]
    assert "kept" in entry["summary"]
    after = {d["slug"]: (d["publisher"], d["modified"]) for d in store.datasets_in_work_list(three)}
    assert after[bad["slug"]] == before[bad["slug"]][:2]                  # a failed read never blanks a column
    assert reg.query_step_annotations("open_data", bad["slug"], analysis_id)[1] == []     # and stores no result


def test_the_work_list_view_of_runs_says_which_failed(reg, store, three):
    from resource_explorer.open_data import queued

    transport = FailsFor(B)
    for d in store.datasets_in_work_list(three):
        _run(reg, d["slug"], "external_data_scout", transport)
    view = queued.work_list_runs(reg, store, three, "external_data_scout")
    assert (view["ok"], view["failed"], view["running"]) == (2, 1, 0)
    bad = [r for r in view["runs"] if r["status"] == "error"]
    assert len(bad) == 1 and "connection refused" in bad[0]["summary"] and bad[0]["title"] in bad[0]["summary"]


# ── the runners themselves ───────────────────────────────────────────────────

def test_a_scout_run_refreshes_the_stored_columns_and_counts_its_one_call(reg, store, three):
    d = store.datasets_in_work_list(three)[0]
    t = ProfileFake()
    _, r = _run(reg, d["slug"], "external_data_scout", t)
    assert r.status == "ok" and len([c for c in t.calls if "package_show" in c]) == 1
    _, rows = reg.query_step_annotations("open_data", d["slug"], "external_data_scout")
    assert rows[0]["result_state"] == "measured" and rows[0]["detail"]["additionalProperties"]["calls_made"] == 1


def test_a_refresh_run_keeps_its_look_as_before(reg, store, three):
    d = store.datasets_in_work_list(three)[0]
    _, r = _run(reg, d["slug"], "external_data_refresh", ProfileFake())
    assert r.status == "ok"
    assert len(reg.query_step_runs(slug=d["slug"], step_key="external_change", entity_type="open_data")) == 1


def test_a_profile_run_reads_the_distributions_and_keeps_the_profile_for_the_work_list_view(reg, store, three, monkeypatch):
    from resource_explorer.open_data import profile
    from resource_explorer.surveyors.filesystem import file_class_match as fcm

    monkeypatch.setattr(fcm, "default_catalog", lambda: NoCatalog)
    d = [x for x in store.datasets_in_work_list(three) if A in x["identifier"] or A in str(x["record"])][0]
    activity, r = _run(reg, d["slug"], "od_profile", ProfileFake())
    assert r.status == "ok", r.error
    _, rows = reg.query_step_annotations("open_data", d["slug"], "od_profile")
    m = rows[0]["detail"]["additionalProperties"]["measures"]
    assert rows[0]["result_state"] == "measured" and m["distributions_profiled"] == 2 and m["distributions_listed"] == 5
    assert any("not profiled" in w for w in m["not_read"])                  # what was NOT read is said, never 0 or silent
    seen = profile.view(store, three)["datasets"]
    assert [x for x in seen if x["slug"] == d["slug"]][0]["profile"]["outcome"] == "profiled"


def test_a_profile_of_a_dataset_the_portal_lists_nothing_for_is_not_measured_not_zero(reg, store, three, monkeypatch):
    from resource_explorer.surveyors.filesystem import file_class_match as fcm

    monkeypatch.setattr(fcm, "default_catalog", lambda: NoCatalog)
    d = [x for x in store.datasets_in_work_list(three) if "child-gg" in x["identifier"] or C in str(x["record"])][0]
    _, r = _run(reg, d["slug"], "od_profile", ProfileFake())
    _, rows = reg.query_step_annotations("open_data", d["slug"], "od_profile")
    assert r.status == "ok" and rows[0]["result_state"] in ("measured", "not_measured")
    assert "distributions_profiled" not in rows[0]["detail"]["additionalProperties"]["measures"] or rows[0]["result_state"] == "measured"


def test_nothing_per_user_is_stored_by_any_of_the_runs(reg, store, three):
    d = store.datasets_in_work_list(three)[0]
    for analysis_id in ("external_data_scout", "external_data_refresh"):
        _run(reg, d["slug"], analysis_id, ProfileFake())
        _, rows = reg.query_step_annotations("open_data", d["slug"], analysis_id)
        stored = json.dumps(rows[0])
        assert "investigation" not in stored and "b4-tester" not in stored


# ── the catalog and the question ─────────────────────────────────────────────

def test_the_three_work_list_entries_are_queued_and_the_three_built_checks_are_wired_to_their_questions_by_b5():
    import yaml

    cat = yaml.safe_load((Path(__file__).resolve().parents[1] / "resource_explorer/configdata/analysis_catalog.yaml").read_text())
    by = {a["id"]: a for a in cat["open_data_analyses"]}
    for aid in ("external_data_scout", "external_data_profile", "external_data_refresh"):
        assert by[aid]["availability"] == "queued" and by[aid].get("answers_questions") is False
    assert all(by[a]["declaration"]["built"] is True for a in ("od_licence_usable", "od_cadence_kept", "od_profile"))
    assert by["od_profile"].get("answers_questions") is True and "Not built" not in by["od_profile"]["description"]   # wired in B5


def test_every_declared_open_data_analysis_now_has_a_runner():
    declared = analyses._declared()
    assert all(analyses.refusal(a) is None for a in declared), {a: analyses.refusal(a) for a in declared if analyses.refusal(a)}


# ── W-B4-1: a run already queued or running is not queued twice ──────────────

def _runs(reg):
    return [q for q in reg.list_runs(limit=100) if q["kind"] == "open_data_analysis_run"]


@pytest.mark.usefixtures("signed_in_caller")
@pytest.mark.parametrize("verb,analysis_id", PRESSES)
def test_two_presses_in_a_row_give_one_queue_row_per_dataset_and_the_second_says_already_queued(api, reg, three, verb, analysis_id):
    client, headers, _ = api
    first = client.post(f"/api/open-data/work-lists/{three}/{verb}", headers=headers).json()
    second = client.post(f"/api/open-data/work-lists/{three}/{verb}", headers=headers).json()
    assert len(_runs(reg)) == 3                                         # not 6: no second full set of runs
    assert (first["queued"], first["already_queued"]) == (3, 0) and first["price"]["calls"] > 0
    assert (second["queued"], second["already_queued"]) == (0, 3)
    assert second["price"]["calls"] == 0 and "already queued" in second["price"]["line"]      # priced only what was newly queued
    assert all(r["already_queued"] for r in second["runs"])
    assert [(r["slug"], r["run_id"], r["activity_id"]) for r in second["runs"]] == [(r["slug"], r["run_id"], r["activity_id"]) for r in first["runs"]]


@pytest.mark.usefixtures("signed_in_caller")
def test_the_per_dataset_route_says_already_queued_with_the_same_ids(api, reg, store, three):
    client, headers, _ = api
    slug = store.datasets_in_work_list(three)[0]["slug"]
    a = client.post(f"/api/open-data/datasets/{slug}/analyses/od_licence_usable/run", headers=headers).json()
    b = client.post(f"/api/open-data/datasets/{slug}/analyses/od_licence_usable/run", headers=headers).json()
    assert a["status"] == "started" and b["status"] == "already queued"
    assert (a["run_id"], a["activity_id"]) == (b["run_id"], b["activity_id"]) and len(_runs(reg)) == 1


@pytest.mark.usefixtures("signed_in_caller")
def test_a_different_analysis_or_a_different_dataset_is_not_blocked(api, reg, store, three):
    client, headers, _ = api
    one, other = [d["slug"] for d in store.datasets_in_work_list(three)][:2]
    client.post(f"/api/open-data/datasets/{one}/analyses/od_licence_usable/run", headers=headers)
    assert client.post(f"/api/open-data/datasets/{one}/analyses/od_cadence_kept/run", headers=headers).json()["status"] == "started"
    assert client.post(f"/api/open-data/datasets/{other}/analyses/od_licence_usable/run", headers=headers).json()["status"] == "started"
    assert len(_runs(reg)) == 3


@pytest.mark.usefixtures("signed_in_caller")
def test_a_finished_run_does_not_block_the_next_press(api, reg, store, three):
    client, headers, _ = api
    slug = store.datasets_in_work_list(three)[0]["slug"]
    first = client.post(f"/api/open-data/datasets/{slug}/analyses/od_licence_usable/run", headers=headers).json()
    with reg._conn() as conn:
        conn.execute("UPDATE runs SET state = 'succeeded' WHERE id = ?", (first["run_id"],))
    again = client.post(f"/api/open-data/datasets/{slug}/analyses/od_licence_usable/run", headers=headers).json()
    assert again["status"] == "started" and again["run_id"] != first["run_id"]


@pytest.mark.usefixtures("signed_in_caller")
def test_a_run_whose_worker_died_does_not_lock_the_dataset_out(api, reg, store, three):
    client, headers, _ = api
    slug = store.datasets_in_work_list(three)[0]["slug"]
    first = client.post(f"/api/open-data/datasets/{slug}/analyses/od_licence_usable/run", headers=headers).json()
    with reg._conn() as conn:           # claimed long ago, never a heartbeat since: a dead worker's run
        conn.execute("UPDATE runs SET state = 'running', claimed_at = '2020-01-01T00:00:00+00:00' WHERE id = ?", (first["run_id"],))
    again = client.post(f"/api/open-data/datasets/{slug}/analyses/od_licence_usable/run", headers=headers).json()
    assert again["status"] == "started" and again["run_id"] != first["run_id"]
