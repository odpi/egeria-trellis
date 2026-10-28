"""Publish ("Catalog & Survey in Egeria") must PERSIST the surveys it starts.

Defect (found live 2026-10-03/04): `EgeriaDatabaseSurveyor._catalog_and_survey`
initiated a server and a database survey, kept the engine-action GUIDs in local
variables and wrote them nowhere, so a survey started from Publish had no proof
row, never reached the read-back sweep, and the UI still said "survey started".

Rule under test: a "survey started" claim derives from a persisted
`step_runs` row (the same one a native-survey run writes), never from the
branch the code took. Egeria is a stub; the registry is a temp SQLite file.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from resource_explorer import native_survey_run as nsr
from resource_explorer.registry import DatabaseEntity, ProjectRegistry
from resource_explorer.surveyors.database import egeria_database_surveyor as eds


@pytest.fixture(autouse=True)
def _curation_access_open(monkeypatch):
    """Brief Z round 2: the publish route checks curation access first; here no zone is in use."""
    from tests.zone_fakes import permit_publish_routes

    return permit_publish_routes(monkeypatch)

SLUG = "coco"
SERVER_GUID = "11111111-0000-0000-0000-000000000001"
DB_GUID = "22222222-0000-0000-0000-000000000002"
SERVER_ACTION = "aaaaaaaa-0000-0000-0000-00000000000a"
DB_ACTION = "bbbbbbbb-0000-0000-0000-00000000000b"
DB_QN = "PostgreSQLSurvey::survey-postgres-database"
SERVER_QN = "PostgreSQLSurvey::survey-postgres-server"
SURVEY_DATA = {"schema_info": {"schemas": [{"name": "s"}], "total_tables": 1,
                               "total_columns": 2}, "statistics": {}}


@pytest.fixture
def registry(tmp_path):
    db_path = str(tmp_path / "t.db")
    assert str(tmp_path) in db_path          # a temp registry, asserted
    r = ProjectRegistry(db_path=db_path)
    r.register_database(DatabaseEntity(
        slug=SLUG, display_name=SLUG, db_type="postgresql", host="localhost",
        port=5432, database_name=SLUG, db_user="u", db_password="p",
        egeria_asset_guid=SERVER_GUID))
    r.record_database_survey(SLUG, 1, 1, 2, SURVEY_DATA, source="local",
                             surveyed_at="2026-10-01T00:00:00")
    return r


class StubSurveyor(eds.EgeriaDatabaseSurveyor):
    """The real _catalog_and_survey / publish_local_survey over a stub Egeria.

    `initiate` maps a technology type to a GUID, '' / None, or an Exception."""
    initiate: dict = {}

    def __init__(self, **kw):  # noqa: D401 -- no network, no credentials
        self._asset_maker = MagicMock()
        self._asset_maker.create_asset.return_value = "report-1"
        self._automated_curation = MagicMock()
        self.calls: list[tuple[str, str]] = []

    def connect(self):
        pass

    def _find_element_guid(self, name):
        return DB_GUID if name == SLUG else SERVER_GUID

    def _find_by_qualified_name(self, qualified_name):
        # Adoption is by qualifiedName (Brief L, 7f.7).
        return DB_GUID if qualified_name.startswith("PostgreSQL Relational Database::") else SERVER_GUID

    def _warn_if_database_has_no_connection(self, *a, **k):
        pass

    def _find_survey_process_name(self, tech_type):
        return None          # force the configured native-process branch

    def _initiate_native_survey(self, qn, target_guid):
        tech = {DB_QN: "PostgreSQL Relational Database", SERVER_QN: "PostgreSQL Server"}[qn]
        self.calls.append((tech, target_guid))
        out = type(self).initiate[tech]
        if isinstance(out, Exception):
            raise out
        return out

    def _create_annotations(self, *a, **k):
        pass


def surveyor_with(initiate):
    cls = type("S", (StubSurveyor,), {"initiate": initiate})
    return cls()


@pytest.fixture
def client(registry, monkeypatch):
    monkeypatch.setattr(
        "resource_explorer.registry.ProjectRegistry.__init__",
        lambda self, db_path=None: setattr(self, "__dict__", registry.__dict__) or None)
    from resource_explorer.auth import create_access_token
    from resource_explorer.web.app import app
    # Brief I: Publish is a person's own action; it runs as a signed-in user with an Egeria token.
    token = create_access_token(user_id="dan", egeria_token="tok-dan")
    return TestClient(app, headers={"Authorization": f"Bearer {token}"})


def publish(client, initiate):
    cls = type("S", (StubSurveyor,), {"initiate": initiate})
    with patch.object(eds, "EgeriaDatabaseSurveyor", cls):
        return client.post(f"/api/databases/{SLUG}/publish", json={})


def proof_rows(registry):
    """Through the reader the native-survey list itself uses."""
    return registry.list_native_survey_runs("database", SLUG)


GOOD = {"PostgreSQL Server": SERVER_ACTION, "PostgreSQL Relational Database": DB_ACTION}


# (a) one proof row per initiated survey, visible through the list's reader
def test_publish_records_one_proof_row_per_initiated_survey(client, registry):
    r = publish(client, GOOD)
    assert r.status_code == 200 and r.json()["status"] == "ok", r.text
    by_guid = {x["engine_action_guid"]: x for x in proof_rows(registry)}
    assert set(by_guid) == {SERVER_ACTION, DB_ACTION}
    assert by_guid[DB_ACTION]["step_key"] == nsr.NATIVE_SURVEY_STEP_PREFIX + DB_QN
    assert by_guid[SERVER_ACTION]["step_key"] == nsr.NATIVE_SURVEY_STEP_PREFIX + SERVER_QN
    assert all(x["submit_error"] == "" and x["entity_type"] == "database" for x in by_guid.values())
    # the native-survey list shows the database survey as submitted, not "not run"
    tech = "PostgreSQL Relational Database"
    row = {x["qualified_name"]: x for x in
           nsr.native_survey_rows(registry, "database", SLUG, tech)}[DB_QN]
    assert row["run"]["state"] == nsr.SUBMITTED and row["in_flight"] is True
    # the response claims exactly what was recorded
    assert {s["engine_action_guid"] for s in r.json()["survey_submissions"]} == {
        SERVER_ACTION, DB_ACTION}


# (d) the sweep reads the recorded rows back
def test_recorded_rows_are_picked_up_by_the_in_flight_sweep(client, registry):
    publish(client, GOOD)
    in_flight = {x["engine_action_guid"] for x in registry.list_in_flight_native_survey_runs()}
    assert in_flight == {SERVER_ACTION, DB_ACTION}


# (b) a raising initiation records nothing and the response does not claim one
def test_failed_initiation_records_nothing_and_claims_nothing(client, registry):
    r = publish(client, {"PostgreSQL Server": RuntimeError("engine host down"),
                         "PostgreSQL Relational Database": RuntimeError("engine host down")})
    assert r.json()["status"] == "ok"            # the catalog work itself succeeded
    assert r.json()["survey_submissions"] == []
    assert proof_rows(registry) == []
    assert registry.list_in_flight_native_survey_runs() == []


def test_one_failed_one_started_records_only_the_started_one(client, registry):
    r = publish(client, {"PostgreSQL Server": RuntimeError("boom"),
                         "PostgreSQL Relational Database": DB_ACTION})
    assert [s["engine_action_guid"] for s in r.json()["survey_submissions"]] == [DB_ACTION]
    assert [x["engine_action_guid"] for x in proof_rows(registry)] == [DB_ACTION]


# (c) an empty GUID is not proof of anything
@pytest.mark.parametrize("empty", ["", None, "   "])
def test_empty_guid_records_nothing(client, registry, empty):
    r = publish(client, {"PostgreSQL Server": empty, "PostgreSQL Relational Database": empty})
    assert r.json()["survey_submissions"] == []
    assert proof_rows(registry) == []


# (e) survey_after_catalog=False records nothing and never asks Egeria
def test_no_survey_after_catalog_records_nothing(registry):
    s = surveyor_with(GOOD)
    entity = registry.get_database(SLUG)
    out = s.catalog_and_survey(entity, "u", "p", registry=registry, survey_after_catalog=False)
    assert out["survey_submissions"] == [] and s.calls == []
    assert proof_rows(registry) == []


def test_catalog_and_survey_without_a_registry_still_works_and_claims_nothing():
    s = surveyor_with(GOOD)
    entity = DatabaseEntity(slug=SLUG, display_name=SLUG, db_type="postgresql", host="h",
                            port=1, database_name=SLUG, egeria_asset_guid=SERVER_GUID)
    out = s.catalog_and_survey(entity, "u", "p", registry=None)
    assert out["survey_action_guid"] == DB_ACTION and out["survey_submissions"] == []


def test_a_proof_row_that_cannot_be_written_is_not_claimed(registry):
    s = surveyor_with(GOOD)
    entity = registry.get_database(SLUG)
    with patch.object(ProjectRegistry, "record_native_survey_submission",
                      side_effect=RuntimeError("disk full")):
        out = s.catalog_and_survey(entity, "u", "p", registry=registry)
    assert out["survey_submissions"] == []          # started in Egeria, unprovable in RE


def test_submitted_by_is_passed_through_not_read_from_a_contextvar(registry):
    s = surveyor_with(GOOD)
    s.catalog_and_survey(registry.get_database(SLUG), "u", "p", registry=registry,
                         submitted_by="dan")
    assert {x["submitted_by"] for x in proof_rows(registry)} == {"dan"}


def test_a_discovered_survey_definition_process_is_recorded_under_its_own_name(registry):
    s = surveyor_with(GOOD)
    s._find_survey_process_name = lambda tech: f"Custom::{tech}"
    s._automated_curation.initiate_gov_action_process.return_value = DB_ACTION
    s.catalog_and_survey(registry.get_database(SLUG), "u", "p", registry=registry)
    keys = {x["step_key"] for x in proof_rows(registry)}
    assert nsr.NATIVE_SURVEY_STEP_PREFIX + "Custom::PostgreSQL Relational Database" in keys


def test_classic_ui_no_longer_publishes_a_database_so_it_makes_no_survey_claim():
    """Retired (Curate slice B): Classic's database Publish modal, whose success handler used to derive
    "survey started" from `survey_submissions`, is gone. The claim now lives in the catalogue commit's
    curation record, written from the survey's proof row (tests/test_catalogue_commit.py)."""
    from pathlib import Path
    html = (Path(__file__).resolve().parent.parent / "resource_explorer" / "web" / "static"
            / "index.html").read_text()
    assert "submitPublishDb" not in html and "Build structured item list" not in html
