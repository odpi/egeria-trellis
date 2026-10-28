"""Routes for registering a database's server with Egeria (optional) and for reading its pointers.

Fake Egeria only (RegPort). Same shared-registry client pattern as test_native_surveys_routes.py."""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from resource_explorer import catalog_and_survey as cas
from resource_explorer.registry import DatabaseEntity, ProjectRegistry
from tests.test_catalog_and_survey import (
    CATALOG_QN, DB_GUID, PASSWORD, SERVER_GUID, SERVER_NAME, SQN, RegPort, refusal)


@pytest.fixture(autouse=True)
def _zone_access_open(monkeypatch):
    """Brief Z2: this write path asks `curation_access` (the one decision). These tests are about something else,
    so no zone is in use: the access reads are faked open at the client boundary (`tests/zone_fakes.open_access`)."""
    from tests.zone_fakes import open_access

    return open_access(monkeypatch, caller_if_none="zone2-tester")

BASE = "/api/native-surveys/database"


@pytest.fixture
def registry(tmp_path):
    r = ProjectRegistry(db_path=str(tmp_path / "test.db"))
    r.register_database(DatabaseEntity(
        slug="adventureworks", display_name="AdventureWorks", db_type="postgresql",
        host="localhost", port=5432, database_name="adventureworks",
        egeria_host="host.docker.internal", db_user="dwolfson", db_password=PASSWORD))
    r.register_database(DatabaseEntity(
        slug="nouser", display_name="No user", db_type="postgresql",
        host="localhost", port=5432, database_name="nouser"))
    return r


@pytest.fixture
def port(monkeypatch):
    p = RegPort()
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


def by_qn(resp):
    return {r["qualified_name"]: r for r in resp.json()["surveys"]}


def test_listing_a_never_registered_database_is_neutral_and_asks_egeria_nothing(client, port):
    resp = client.get(f"{BASE}/adventureworks")
    assert resp.status_code == 200 and port.calls == []
    row = by_qn(resp)["PostgreSQLSurvey::survey-postgres-database"]
    assert row["neutral"] is True and row["run"]["state"] == "not_run"
    assert by_qn(resp)[CATALOG_QN]["register"]["available"] is True


def test_the_press_registers_and_returns_the_rows(client, port, registry):
    r = client.post(f"{BASE}/adventureworks/register")
    assert r.status_code == 200
    assert r.json()["registered"]["server"] == {
        "guid": SERVER_GUID, "qualified_name": SQN, "how": "created"}
    assert registry.get_setting(cas.server_pointer_key(SERVER_NAME)) == SERVER_GUID
    row = by_qn(r)[CATALOG_QN]
    assert row["run"]["state"] in ("submitted", "running") and row["in_flight"] is True
    # the other row for the same kind is untouched by the press: RE's own survey of it is not blocked
    assert registry.get_database("adventureworks").db_user == "dwolfson"


def test_a_record_that_cannot_be_registered_is_422_and_says_what_is_missing(client, port):
    r = client.post(f"{BASE}/nouser/register")
    assert r.status_code == 422 and "no database user" in r.json()["detail"]
    assert port.calls == []


def test_egeria_refusing_is_502_with_egerias_sentence_and_the_activity_row_has_no_password(
        client, port, registry):
    port.process_error = refusal(f"Connection refused at host (url carried {PASSWORD})")
    r = client.post(f"{BASE}/adventureworks/register")
    assert r.status_code == 502 and "Connection refused" in r.json()["detail"]
    assert PASSWORD not in r.text
    dump = json.dumps(registry.list_activity(limit=50), default=str)
    assert "Connection refused" in dump and PASSWORD not in dump
    rows = by_qn(client.get(f"{BASE}/adventureworks"))
    assert rows[CATALOG_QN]["run"]["state"] == "submit_failed"
    assert rows[CATALOG_QN]["reach_note"] == cas.REACH_NOTE


def test_check_reports_a_gone_pointer_and_the_rows_offer_the_control(client, port, registry):
    registry.set_database_egeria_guid("adventureworks", DB_GUID)
    r = client.post(f"{BASE}/adventureworks/check")
    assert r.status_code == 200 and r.json()["pointers"]["database"] == "gone"
    row = by_qn(r)["PostgreSQLSurvey::survey-postgres-database"]
    assert row["stale"] is True and row["cannot_run_reason"] == cas.STALE_WORDS
    assert row["register"] == {"available": True, "label": cas.REGISTER_AGAIN_LABEL, "why_not": ""}


def test_check_with_nothing_stored_asks_egeria_nothing(client, port):
    r = client.post(f"{BASE}/adventureworks/check")
    assert r.json()["pointers"] == {"database": "none", "server": "none", "error": ""}
    assert port.calls == []


def test_check_with_a_pointer_that_resolves_says_nothing_is_stale(client, port, registry):
    registry.set_database_egeria_guid("adventureworks", DB_GUID)
    port.assets.add(DB_GUID)
    r = client.post(f"{BASE}/adventureworks/check")
    assert r.json()["pointers"]["database"] == "resolves"
    assert by_qn(r)["PostgreSQLSurvey::survey-postgres-database"]["stale"] is False


def test_running_a_survey_on_a_gone_asset_is_422_with_the_new_words(client, port, registry):
    registry.set_database_egeria_guid("adventureworks", DB_GUID)
    r = client.post(f"{BASE}/adventureworks/run",
                    json={"process_qualified_name": "PostgreSQLSurvey::survey-postgres-database"})
    assert r.status_code == 422 and "no longer exists" in r.json()["detail"]
    assert "Publish the resource" not in r.json()["detail"]


def test_a_second_press_after_an_unresolved_run_is_409_and_start_again_is_explicit(client, port, registry):
    from tests.test_catalog_and_survey import age_run
    assert client.post(f"{BASE}/adventureworks/register").status_code == 200
    run = registry.list_native_survey_runs("database", "adventureworks", CATALOG_QN)[0]
    port.actions[run["engine_action_guid"]] = ("COMPLETED", "")
    client.post(f"{BASE}/adventureworks/refresh")
    age_run(registry)
    r = client.post(f"{BASE}/adventureworks/register")
    assert r.status_code == 409 and "Start again" in r.json()["detail"]
    assert len(port.processes) == 1
    row = by_qn(client.get(f"{BASE}/adventureworks"))[CATALOG_QN]
    assert row["register"]["start_again"] is True
    r = client.post(f"{BASE}/adventureworks/register", json={"start_again": True})
    assert r.status_code == 200 and len(port.processes) == 2


def test_the_response_says_whether_the_secrets_file_was_reprojected(client, port, tmp_path, monkeypatch):
    monkeypatch.setattr("resource_explorer.omsecrets_store.local_path", lambda: str(tmp_path / "s.omsecrets"))
    r = client.post(f"{BASE}/adventureworks/register")
    assert r.json()["registered"]["projected"]["written"] is True
