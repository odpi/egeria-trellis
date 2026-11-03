"""Backlog 7f.1/7f.5 end to end, and 7f.8: a database Survey Definition run
through the real executor, the real database adapter runners, the real
`DatabaseSurveyor` and the real `ProjectRegistry` (temp SQLite).

Faked only at the client boundaries:
* the surveyed Postgres: a `PostgreSQLConnection` subclass that answers its
  public getters from canned rows and opens no socket;
* Egeria: `EgeriaDatabaseSurveyor.publish_step_annotations`, patched with
  `autospec=True` so a call with the wrong arguments fails like the real one.

7f.1/7f.5 (fixed on main by Brief D): a three-step scan leaves ONE
`database_surveys` row, at the run's own `surveyed_at`, carrying the activity.

7f.8 (here): the run says what happened to its publish as `publish_state` in
its activity detail -- "published to Egeria", "publish failed · <why>" or
"local only · publish not chosen" -- so a failed publish and a publish nobody
chose no longer read the same. Before this the run recorded only
`published: false` plus an error string, and the step rows "answered by".
"""
from __future__ import annotations

import inspect
import json
from contextlib import contextmanager
from unittest.mock import patch

import pytest

from resource_explorer.registry import DatabaseEntity, ProjectRegistry
from resource_explorer.surveyors.database.connection import (
    EngineCapabilities,
    PostgreSQLConnection,
)
from resource_explorer.surveyors.database.egeria_database_surveyor import (
    EgeriaDatabaseSurveyor,
    EgeriaDatabaseSurveyorError,
)
from resource_explorer.registry import STATS_NEVER_RESET
from resource_explorer.surveyors.survey_definition_executor import (
    NOTHING_TO_PUBLISH,
    PUBLISH_NOT_CHOSEN,
    PUBLISHED,
    PUBLISHED_NOT_RECORDED,
    SurveyDefinitionExecutor,
)
from resource_explorer.surveyors.survey_definition_reader import (
    SurveyDefinition,
    SurveyStep,
)

SCOUTING_STEPS = ("postgres_schema_and_stats", "postgres_operations", "credential_capability")


class FakePostgres(PostgreSQLConnection):
    """The surveyed database. Subclasses the real connection, so every method
    the surveyor calls exists with its real signature; the overridden getters
    answer from canned rows, and anything not overridden reaches
    `execute_query`, which answers nothing."""

    #: What pg_stat_database.stats_reset says, as the real getter reports it:
    #: a timestamp, or STATS_NEVER_RESET for a NULL.
    reset = "2026-01-01T00:00:00"
    #: Set to make the schema read fail, so no step produces results.
    unreachable = False

    def __init__(self):  # no socket
        self.conn = None

    def execute_query(self, query: str, params: tuple = ()) -> list[dict]:
        return []

    def execute_query_isolated(self, query: str, params: tuple = ()) -> list[dict]:
        return []

    @property
    def capabilities(self) -> EngineCapabilities:
        return EngineCapabilities(column_stats=True, tuple_counters=True, index_stats=True)

    def get_schema_info(self) -> dict:
        if self.unreachable:
            raise ConnectionError("could not connect to server")
        return {
            "schemas": [{"name": "public", "description": "", "tables": [{
                "name": "orders", "type": "BASE TABLE", "description": "",
                "columns": [{"name": "id", "type": "integer", "base_type": "integer",
                             "nullable": False, "default": None, "position": 1,
                             "description": "", "is_primary_key": True,
                             "foreign_key": None, "source": "information_schema"}],
                "source": "information_schema"}]}],
            "total_tables": 1, "total_columns": 1,
        }

    def get_statistics(self) -> dict:
        return {
            "stats_reset": self.reset if self.reset != STATS_NEVER_RESET else "",
            "stats_reset_evidence": self.reset,
            "row_stats": [{"schemaname": "public", "tablename": "orders", "row_count": 5,
                           "last_analyzed": "", "last_vacuumed": "", "pending_changes": 0}],
            "table_stats": [{"schemaname": "public", "tablename": "orders",
                             "total_bytes": 8192, "total_size": "8 kB"}],
            "column_stats": [],
            "table_activity": [{
                "schemaname": "public", "tablename": "orders",
                "rows_inserted": 761184, "rows_updated": 1435, "rows_deleted": 0,
                "hot_updates": 0, "live_tuples": 761184, "dead_tuples": 0,
                "seq_scan": 861, "idx_scan": 0, "last_vacuum": "", "last_autovacuum": "",
                "last_analyze": "", "last_autoanalyze": "", "pending_changes": 0}],
        }


def test_the_fake_keeps_the_real_signatures():
    for name, member in vars(FakePostgres).items():
        if name.startswith("__") or not inspect.isfunction(member):
            continue
        real = getattr(PostgreSQLConnection, name)
        assert inspect.signature(member) == inspect.signature(real), name


@contextmanager
def surveyed_postgres():
    with patch("resource_explorer.surveyors.database.database_surveyor.database_connection") as ctx:
        ctx.return_value.__enter__.return_value = FakePostgres()
        ctx.return_value.__exit__.return_value = False
        yield


@pytest.fixture
def registry(tmp_path):
    return ProjectRegistry(db_path=str(tmp_path / "reg.db"))


@pytest.fixture
def db(registry):
    registry.register_database(DatabaseEntity(
        slug="advisor", display_name="Advisor", db_type="postgresql",
        host="localhost", port=5442, database_name="advisor",
        db_user="surveyor", db_password="not-a-real-password"))
    return registry.get_database("advisor")


def run_scouting_scan(registry, db, runner_kwargs=None) -> dict:
    steps = [SurveyStep(guid="", display_name=k, qualified_name=f"Step::{k}",
                        executes_at="resource-explorer", re_analysis_step=k)
             for k in SCOUTING_STEPS]
    survey_def = SurveyDefinition(
        process_guid="", display_name="Database Scouting Scan",
        qualified_name="GovActionProcess::DatabaseScoutingSurvey",
        supported_technology_type=None, steps=steps)
    with surveyed_postgres():
        return SurveyDefinitionExecutor(registry)._execute(
            entity_type="database", slug="advisor", entity=db, survey_def=survey_def,
            process_guid="", process_qn=survey_def.qualified_name, publish=None,
            engine_override="resource-explorer",
            runner_kwargs=runner_kwargs if runner_kwargs is not None
            else {"db_user": "surveyor", "db_pwd": "not-a-real-password"})


def run_detail(registry) -> dict:
    rows = [r for r in registry.list_activity(entity_slug="advisor", operation="survey")
            if "survey_definition_ref" in (r.get("detail") or "")]
    assert len(rows) == 1, rows
    return json.loads(rows[0]["detail"])


# -- 7f.1 / 7f.5, end to end -------------------------------------------------

def test_one_scouting_scan_is_one_snapshot_at_the_runs_time_with_its_activity(registry, db):
    result = run_scouting_scan(registry, db)
    assert [s["status"] for s in result["steps"]] == ["ok", "ok", "ok"], result["steps"]

    surveys = registry.get_database_surveys("advisor")
    assert [r["surveyed_at"] for r in surveys] == [result["surveyed_at"]], (
        "one scan must be ONE database_surveys row, at the run's own surveyed_at")
    activity = registry.query_detail_rows("database_table_activity", "advisor",
                                          result["surveyed_at"])
    assert [a["rows_inserted"] for a in activity] == [761184], (
        "the scan's activity must sit on its one snapshot")
    # 7f.3: the reset time read from pg_stat_database is stored on it
    assert [a["stats_reset"] for a in activity] == ["2026-01-01T00:00:00"]


def test_7f3_never_reset_is_stored_as_the_never_word_not_as_not_read(registry, db, monkeypatch):
    monkeypatch.setattr(FakePostgres, "reset", STATS_NEVER_RESET)
    result = run_scouting_scan(registry, db)
    activity = registry.query_detail_rows("database_table_activity", "advisor",
                                          result["surveyed_at"])
    assert [a["stats_reset"] for a in activity] == [STATS_NEVER_RESET]


# -- 7f.8: the run's publish word --------------------------------------------

def test_a_run_nobody_chose_to_publish_says_so(registry, db):
    result = run_scouting_scan(registry, db)
    assert result["publish_state"] == PUBLISH_NOT_CHOSEN
    assert run_detail(registry)["publish_state"] == PUBLISH_NOT_CHOSEN


def test_a_failed_publish_says_it_failed_and_why(registry, db):
    registry.set_project_context("database", "advisor", status="linked", egeria_project_guid="p-1")
    with patch.object(EgeriaDatabaseSurveyor, "publish_step_annotations", autospec=True,
                      side_effect=EgeriaDatabaseSurveyorError("EGERIA_PLATFORM_URL is not set")):
        result = run_scouting_scan(registry, db)
    stored = run_detail(registry)["publish_state"]
    assert stored == result["publish_state"]
    assert stored.startswith("publish failed · ") and "EGERIA_PLATFORM_URL is not set" in stored
    assert stored != PUBLISH_NOT_CHOSEN


def test_a_publish_that_landed_says_published(registry, db):
    registry.set_project_context("database", "advisor", status="linked", egeria_project_guid="p-1")
    with patch.object(EgeriaDatabaseSurveyor, "publish_step_annotations", autospec=True,
                      return_value={"asset_guid": "a-1", "report_guid": "r-1",
                                    "annotation_count": 3}) as published:
        result = run_scouting_scan(registry, db)
    assert published.call_count == 1
    assert result["publish_state"] == PUBLISHED
    assert run_detail(registry)["publish_state"] == PUBLISHED
    # The word and the stored publish mark come from the same publish.
    assert registry.get_database_surveys("advisor")[0].get("published_at")


def test_the_word_never_carries_the_runs_password(registry, db):
    registry.set_project_context("database", "advisor", status="linked", egeria_project_guid="p-1")
    with patch.object(EgeriaDatabaseSurveyor, "publish_step_annotations", autospec=True,
                      side_effect=EgeriaDatabaseSurveyorError("login not-a-real-password refused")):
        run_scouting_scan(registry, db)
    assert "not-a-real-password" not in json.dumps(run_detail(registry))


def test_a_publish_with_no_report_guid_is_stored_as_not_published(registry, db):
    """Egeria answered without a report guid: the word says failed, and the
    stored flag must agree with it, never `published: true` beside it."""
    registry.set_project_context("database", "advisor", status="linked", egeria_project_guid="p-1")
    with patch.object(EgeriaDatabaseSurveyor, "publish_step_annotations", autospec=True,
                      return_value={"asset_guid": "a-1", "report_guid": "", "annotation_count": 0}):
        result = run_scouting_scan(registry, db)
    assert result["publish_state"].startswith("publish failed · ")
    assert result["published"] is False
    assert run_detail(registry)["published"] is False


def test_a_refused_publish_says_failed_with_the_refusal(registry, db, monkeypatch):
    """No requester and a configured zone: the access re-check refuses. Real
    `person_write_refusal`, real config; only Egeria is faked (and never called)."""
    monkeypatch.setenv("EXPLORER_PUBLISH_ZONES", "Zone-A")
    registry.set_project_context("database", "advisor", status="linked", egeria_project_guid="p-1")
    with patch.object(EgeriaDatabaseSurveyor, "publish_step_annotations", autospec=True,
                      return_value={"report_guid": "r-1"}) as published:
        result = run_scouting_scan(registry, db)
    assert published.call_count == 0
    stored = run_detail(registry)["publish_state"]
    assert stored == result["publish_state"]
    assert stored.startswith("publish failed · not permitted"), stored
    assert run_detail(registry)["published"] is False


def test_a_run_whose_steps_all_failed_has_nothing_to_publish(registry, db, monkeypatch):
    monkeypatch.setattr(FakePostgres, "unreachable", True)
    registry.set_project_context("database", "advisor", status="linked", egeria_project_guid="p-1")
    with patch.object(EgeriaDatabaseSurveyor, "publish_step_annotations", autospec=True) as published:
        result = run_scouting_scan(registry, db)
    assert published.call_count == 0
    assert result["publish_state"] == NOTHING_TO_PUBLISH
    assert run_detail(registry)["publish_state"] == NOTHING_TO_PUBLISH
    assert NOTHING_TO_PUBLISH != PUBLISH_NOT_CHOSEN


def test_the_word_masks_a_short_stored_password_and_credentials_in_the_error(registry):
    """The stored password is 3 characters (below the run-password masking
    floor) and the run typed none; the error also carries URL userinfo and a
    bearer token. None of it may reach the stored detail, and the word is bounded."""
    registry.register_database(DatabaseEntity(
        slug="advisor", display_name="Advisor", db_type="postgresql",
        host="localhost", port=5442, database_name="advisor",
        db_user="surveyor", db_password="pw7"))
    short = registry.get_database("advisor")
    assert short.db_password == "pw7"
    registry.set_project_context("database", "advisor", status="linked", egeria_project_guid="p-1")
    noisy = ("login pw7 refused at https://admin:s3cretpass@egeria.example:9443/x "
             "Authorization: Bearer abcdef0123456789 " + "x" * 600)
    with patch.object(EgeriaDatabaseSurveyor, "publish_step_annotations", autospec=True,
                      side_effect=EgeriaDatabaseSurveyorError(noisy)):
        result = run_scouting_scan(registry, short, runner_kwargs={})
    detail = json.dumps(run_detail(registry))
    for secret in ("pw7", "s3cretpass", "abcdef0123456789"):
        assert secret not in detail, secret
    assert result["publish_state"].startswith("publish failed · ")
    assert len(result["publish_state"]) <= 300


def test_egeria_accepted_but_not_recorded_locally_still_says_published(registry, db):
    """The report guid is the proof: a local bookkeeping failure after Egeria
    accepted the report must not turn into "publish failed"."""
    registry.set_project_context("database", "advisor", status="linked", egeria_project_guid="p-1")

    # Deliberate fault injection on a registry method (not a client fake): the
    # local bookkeeping after an accepted publish is what must fail here.
    def broken(*_a, **_k):
        raise RuntimeError("registry write failed")

    with patch.object(EgeriaDatabaseSurveyor, "publish_step_annotations", autospec=True,
                      return_value={"asset_guid": "a-1", "report_guid": "r-9", "annotation_count": 1}), \
            patch.object(registry, "record_database_survey_published", broken):
        result = run_scouting_scan(registry, db)
    stored = run_detail(registry)
    assert stored["publish_state"].startswith(PUBLISHED_NOT_RECORDED), stored["publish_state"]
    assert stored["egeria_report_guid"] == "r-9"
    assert stored["published"] is True and result["published"] is True


def _register_with_password(registry, password):
    registry.register_database(DatabaseEntity(
        slug="advisor", display_name="Advisor", db_type="postgresql",
        host="localhost", port=5442, database_name="advisor",
        db_user="surveyor", db_password=password))
    registry.set_project_context("database", "advisor", status="linked", egeria_project_guid="p-1")
    return registry.get_database("advisor")


@pytest.mark.parametrize("password", ["e", "ish"])
def test_a_short_password_inside_the_vocabulary_does_not_corrupt_published(registry, password):
    entity = _register_with_password(registry, password)
    with patch.object(EgeriaDatabaseSurveyor, "publish_step_annotations", autospec=True,
                      return_value={"asset_guid": "a-1", "report_guid": "r-1", "annotation_count": 1}):
        result = run_scouting_scan(registry, entity, runner_kwargs={})
    assert result["publish_state"] == PUBLISHED
    assert run_detail(registry)["publish_state"] == PUBLISHED


@pytest.mark.parametrize("password", ["e", "ish"])
def test_a_short_password_keeps_publish_failed_and_stays_masked_in_the_reason(registry, password):
    entity = _register_with_password(registry, password)
    with patch.object(EgeriaDatabaseSurveyor, "publish_step_annotations", autospec=True,
                      side_effect=EgeriaDatabaseSurveyorError(f"login [{password}] refused")):
        result = run_scouting_scan(registry, entity, runner_kwargs={})
    word = run_detail(registry)["publish_state"]
    assert word == result["publish_state"]
    assert word.startswith("publish failed · "), word
    reason = word[len("publish failed · "):]
    assert f"[{password}]" not in reason and "[***]" in reason, reason
