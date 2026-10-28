"""Brief section 8, optional slice (owner-approved 2026-10-07: "yes to the analyses override").

A single database analysis can take a credential typed for that run: it runs IN-PROCESS on RE's own
engine, never through the run queue or Prefect, nothing stored, no retry (the row says so). The
password is session memory only (G2's rule): it must be in no registry row, activity row, log line,
queue payload or response. Every secret here is an obviously fake value and each test greps what was
stored, logged and returned for it. Temp SQLite, no connection, no Egeria.
"""
from __future__ import annotations

import json
import logging
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from resource_explorer.registry import DatabaseEntity, ProjectRegistry

FAKE_PW = "override-password-not-real"
STORED_PW = "stored-password-not-real"


@pytest.fixture
def registry(tmp_path):
    r = ProjectRegistry(db_path=str(tmp_path / "t.db"))
    r.register_database(DatabaseEntity(
        slug="mydb", display_name="My DB", db_type="postgresql", host="localhost", port=5432,
        database_name="mydb", db_user="stored_user", db_password=STORED_PW))
    return r


@pytest.fixture
def client(registry, monkeypatch):
    monkeypatch.setattr(
        "resource_explorer.registry.ProjectRegistry.__init__",
        lambda self, db_path=None: setattr(self, "__dict__", registry.__dict__) or None,
    )
    # Run the in-process thread inline so the test sees its whole effect.
    monkeypatch.setattr("resource_explorer.workflows.survey_definition.start_in_process",
                        lambda target, *a, name="": target(*a))
    from resource_explorer.web.app import app
    return TestClient(app)


def _everything(registry) -> str:
    return json.dumps({"activity": registry.list_activity(limit=1000), "runs": registry.list_runs()}, default=str)


def _survey_returning(annotations=None, errors=None, raises=None, seen=None):
    def run(slug, credentials, registry=None, steps=None, **kw):
        if seen is not None:
            seen.update(credentials=dict(credentials), steps=steps)
        if raises:
            raise RuntimeError(raises.replace("{pw}", credentials["password"]))
        return {"annotations": annotations or [], "errors": errors or []}
    return run


URL = "/api/databases/mydb/analyses/schema_inventory/run"


def test_the_override_runs_with_the_typed_credential_and_never_touches_the_queue(client, registry):
    seen = {}
    with patch("resource_explorer.surveyors.database.database_surveyor.run_database_survey",
               _survey_returning(seen=seen)):
        r = client.post(URL, json={"db_user": "one_off_user", "db_pwd": FAKE_PW})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "started" and body["run_id"] == ""
    assert body["ran_as"] == {"user": "one_off_user", "scope": "this run"}
    assert seen["credentials"] == {"user": "one_off_user", "password": FAKE_PW}, "the override was what connected"
    assert registry.list_runs() == [], "an override analysis reached the run queue"
    assert FAKE_PW not in r.text and FAKE_PW not in _everything(registry)


def test_the_row_says_who_it_ran_as_and_that_it_is_not_retried(client, registry):
    with patch("resource_explorer.surveyors.database.database_surveyor.run_database_survey",
               _survey_returning()):
        r = client.post(URL, json={"db_user": "one_off_user", "db_pwd": FAKE_PW})
    row = next(a for a in registry.list_activity(limit=50) if a["id"] == r.json()["activity_id"])
    detail = json.loads(row["detail"])
    assert detail["ran_as"] == {"user": "one_off_user", "scope": "this run"}
    assert detail["not_retried"] == "not retried · credential was for this run only"
    assert "one_off_user" in row["summary"] or row["status"] in ("ok", "error")
    last = registry.get_analysis_last_run("database", "mydb")["schema_inventory"]
    assert last["ran_as"] == {"user": "one_off_user", "scope": "this run"}


def test_a_failure_that_echoes_the_password_is_scrubbed_from_rows_and_logs(client, registry, caplog):
    caplog.set_level(logging.DEBUG)
    with patch("resource_explorer.surveyors.database.database_surveyor.run_database_survey",
               _survey_returning(raises="connect failed for password {pw}")):
        r = client.post(URL, json={"db_user": "one_off_user", "db_pwd": FAKE_PW})
    stored = _everything(registry)
    assert FAKE_PW not in stored and FAKE_PW not in r.text
    assert FAKE_PW not in caplog.text, "the password reached a log line"
    row = next(a for a in registry.list_activity(limit=50) if a["id"] == r.json()["activity_id"])
    assert row["status"] == "error", "the failure must still be reported"


def test_annotations_that_echo_the_password_are_scrubbed_before_they_are_stored(client, registry):
    ann = []
    with patch("resource_explorer.surveyors.database.database_surveyor.run_database_survey",
               _survey_returning(errors=[f"warn {FAKE_PW}"], annotations=ann)), \
         patch("resource_explorer.surveyors.survey_report.summarise_annotations",
               lambda a: [{"analysis_name": "x", "summary": f"saw {FAKE_PW}", "count": 1}]):
        client.post(URL, json={"db_user": "one_off_user", "db_pwd": FAKE_PW})
    assert FAKE_PW not in _everything(registry)


def test_a_password_without_a_user_is_refused_and_nothing_runs(client, registry):
    with patch("resource_explorer.surveyors.database.database_surveyor.run_database_survey",
               _survey_returning()) as run:
        r = client.post(URL, json={"db_user": "", "db_pwd": FAKE_PW})
    assert r.status_code == 400
    assert FAKE_PW not in r.text
    assert registry.list_activity(limit=10) == []


def test_a_zero_fetch_analysis_takes_no_credential(client, registry):
    r = client.post("/api/databases/mydb/analyses/db_classification/run",
                    json={"db_user": "u", "db_pwd": FAKE_PW})
    assert r.status_code == 400 and "opens no connection" in r.json()["detail"]
    assert registry.list_runs() == [] and FAKE_PW not in _everything(registry)


def test_without_a_credential_the_run_is_enqueued_exactly_as_before(client, registry):
    r = client.post(URL)                      # no body at all
    assert r.status_code == 200
    assert r.json()["run_id"] and r.json()["ran_as"] is None
    assert [run["kind"] for run in registry.list_runs()] == ["database_analysis_run"]
    r2 = client.post(URL, json={})            # an empty body is the same thing
    assert r2.json()["run_id"]


def test_the_queue_payload_of_an_ordinary_run_carries_no_credential(client, registry):
    client.post(URL, json={"db_user": "u"})
    payloads = json.dumps([r for r in registry.list_runs()], default=str)
    assert "db_pwd" not in payloads and FAKE_PW not in payloads
