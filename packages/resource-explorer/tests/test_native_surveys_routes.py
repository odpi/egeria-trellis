"""Route-level tests for /api/native-surveys (BRIEF-NATIVE-EGERIA-SURVEY-LAUNCH.md).

Same shared-registry client pattern as test_doc_sources_routes.py; Egeria is a
FakePort, so nothing here touches a network."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from resource_explorer.registry import DatabaseEntity, ProjectRegistry
from tests.test_native_survey_run import ASSET, CATALOG_QN, SURVEY_QN, FakePort, ann


@pytest.fixture(autouse=True)
def _zone_access_open(monkeypatch):
    """Brief Z2: this write path asks `curation_access` (the one decision). These tests are about something else,
    so no zone is in use: the access reads are faked open at the client boundary (`tests/zone_fakes.open_access`)."""
    from tests.zone_fakes import open_access

    return open_access(monkeypatch, caller_if_none="zone2-tester")


@pytest.fixture
def registry(tmp_path):
    r = ProjectRegistry(db_path=str(tmp_path / "test.db"))
    r.register_database(DatabaseEntity(
        slug="adventureworks", display_name="AdventureWorks", db_type="postgresql",
        host="localhost", port=5432, database_name="adventureworks",
        egeria_asset_guid=ASSET))
    r.register_database(DatabaseEntity(
        slug="bare", display_name="Bare", db_type="postgresql",
        host="localhost", port=5432, database_name="bare"))
    return r


@pytest.fixture
def port(monkeypatch):
    p = FakePort()
    monkeypatch.setattr("resource_explorer.web.routes.native_surveys._port", lambda: p)
    return p


@pytest.fixture
def client(registry, port, monkeypatch):
    monkeypatch.setattr(
        "resource_explorer.registry.ProjectRegistry.__init__",
        lambda self, db_path=None: setattr(self, "__dict__", registry.__dict__) or None,
    )
    from resource_explorer.web.app import app
    return TestClient(app)


def rows(resp):
    return {r["qualified_name"]: r for r in resp.json()["surveys"]}


def test_lists_the_native_surveys_with_run_or_a_reason(client):
    resp = client.get("/api/native-surveys/database/adventureworks")
    assert resp.status_code == 200
    by = rows(resp)
    assert by[SURVEY_QN]["runnable"] is True
    assert by[CATALOG_QN]["runnable"] is False and by[CATALOG_QN]["cannot_run_reason"]
    assert by[SURVEY_QN]["run"]["state"] == "not_run"


def test_listing_asks_egeria_nothing(client, port):
    client.get("/api/native-surveys/database/adventureworks")
    assert port.calls == []


def test_unknown_resource_and_entity_type_are_404(client):
    assert client.get("/api/native-surveys/database/nope").status_code == 404
    assert client.get("/api/native-surveys/repo/whatever").status_code == 404


def test_run_submits_and_returns_the_proven_state(client, port):
    resp = client.post("/api/native-surveys/database/adventureworks/run",
                       json={"process_qualified_name": SURVEY_QN})
    assert resp.status_code == 200
    run = resp.json()["run"]
    assert run["engine_action_guid"] in port.actions
    assert run["state"] == "running" and run["egeria_status"] == "REQUESTED"
    assert rows(resp)[SURVEY_QN]["in_flight"] is True


def test_a_second_run_while_in_flight_is_409(client):
    body = {"process_qualified_name": SURVEY_QN}
    assert client.post("/api/native-surveys/database/adventureworks/run", json=body).status_code == 200
    assert client.post("/api/native-surveys/database/adventureworks/run", json=body).status_code == 409


def test_a_survey_re_cannot_run_is_422_with_the_reason(client):
    r = client.post("/api/native-surveys/database/bare/run",
                    json={"process_qualified_name": SURVEY_QN})
    assert r.status_code == 422 and "has not been given this database" in r.json()["detail"]
    r = client.post("/api/native-surveys/database/adventureworks/run",
                    json={"process_qualified_name": CATALOG_QN})
    assert r.status_code == 422 and "Register the server with Egeria" in r.json()["detail"]


def test_egeria_refusing_the_submission_is_502_and_recorded(client, port, registry):
    port.initiate_error = RuntimeError("OMAG-400 not recognized")
    r = client.post("/api/native-surveys/database/adventureworks/run",
                    json={"process_qualified_name": SURVEY_QN})
    assert r.status_code == 502 and "not recognized" in r.json()["detail"]
    listed = rows(client.get("/api/native-surveys/database/adventureworks"))
    assert listed[SURVEY_QN]["run"]["state"] == "submit_failed"
    assert "not recognized" in listed[SURVEY_QN]["run"]["error"]


def test_refresh_moves_a_run_to_complete_and_serves_the_report(client, port):
    r = client.post("/api/native-surveys/database/adventureworks/run",
                    json={"process_qualified_name": SURVEY_QN})
    action = r.json()["run"]["engine_action_guid"]
    port.finish(action, report="rep-1", annotations=[ann(1), ann(2)])

    resp = client.post("/api/native-surveys/database/adventureworks/refresh")
    run = rows(resp)[SURVEY_QN]["run"]
    assert run["state"] == "complete" and run["annotation_count"] == 2

    rep = client.get("/api/native-surveys/database/adventureworks/reports/rep-1").json()
    assert rep["report_at"] == "2026-09-30T12:00:00+00:00" and len(rep["annotations"]) == 2
    assert client.get("/api/native-surveys/database/adventureworks/reports/other").status_code == 404
    assert client.get("/api/native-surveys/database/bare/reports/rep-1").status_code == 404


def _stored_report(client, port):
    r = client.post("/api/native-surveys/database/adventureworks/run", json={"process_qualified_name": SURVEY_QN})
    port.finish(r.json()["run"]["engine_action_guid"], report="rep-1", annotations=[ann(1), ann(2)])
    client.post("/api/native-surveys/database/adventureworks/refresh")
    return "/api/native-surveys/database/adventureworks/reports/rep-1"


def test_the_stored_copy_carries_its_read_time_and_no_reset_line_without_a_marker(client, port):
    url = _stored_report(client, port)
    rep = client.get(url).json()
    assert rep["stored_copy_read_at"] and rep["stored_copy_read_at"] == max(a["read_at"] for a in rep["annotations"])
    assert rep["egeria_reset_at"] == "" and rep["reset_since"] is False


def test_the_reset_line_appears_only_when_the_marker_postdates_the_read(client, port, registry):
    url = _stored_report(client, port)
    before = client.get(url).json()
    calls = list(port.calls)
    # a marker BEFORE the read: Egeria was reset, then this was read from the new Egeria -> live, no line
    registry.append_catalogue_commit_proof("adventureworks", proof="egeria_reset", node_kind="database",
                                           read_at="2000-01-01T00:00:00", detail={"text": "reset"})
    assert client.get(url).json()["reset_since"] is False
    # a marker AFTER the read: the stored copy outlived the element it was read from
    registry.append_catalogue_commit_proof("adventureworks", proof="egeria_reset", node_kind="database",
                                           read_at="2999-01-01T00:00:00", detail={"text": "reset"})
    after = client.get(url).json()
    assert after["reset_since"] is True and after["egeria_reset_at"] == "2999-01-01T00:00:00"
    # the data and its ordering are unchanged, and nothing was asked of Egeria
    assert after["annotations"] == before["annotations"]
    assert port.calls == calls


def test_refresh_with_nothing_in_flight_asks_egeria_nothing(client, port):
    client.post("/api/native-surveys/database/adventureworks/refresh")
    assert port.calls == []


def test_a_submission_writes_an_activity_entry(client, registry):
    client.post("/api/native-surveys/database/adventureworks/run",
                json={"process_qualified_name": SURVEY_QN})
    with registry._conn() as conn:
        rows_ = conn.execute(
            "SELECT status, summary FROM activity_log WHERE entity_slug = 'adventureworks'"
        ).fetchall()
    assert [r["status"] for r in rows_] == ["triggered"]
    assert "engine action" in rows_[0]["summary"]


def test_a_run_through_the_route_is_stamped_as_started_by_an_action_type(client, port, registry):
    import json

    r = client.post("/api/native-surveys/database/adventureworks/run", json={"process_qualified_name": SURVEY_QN})
    guid = r.json()["run"]["engine_action_guid"]
    row = next(x for x in registry.list_native_survey_runs("database", "adventureworks") if x["engine_action_guid"] == guid)
    assert json.loads(row["declared"]) == {"started_as": "gov_action_type"}
