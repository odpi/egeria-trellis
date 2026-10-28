"""Understanding polish: whole-database totals, why statistics are "not measured", stalled native
surveys, the survey-list scope reason, and the report route's process/request fields."""
from __future__ import annotations

from datetime import datetime
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from resource_explorer import native_survey_run as nsr
from resource_explorer.registry import (
    DatabaseEntity, Project, ProjectRegistry, SECTION_TABLES,
)

T1 = "2026-09-28T09:00:00"
BT = "BASE TABLE"


@pytest.fixture
def registry(tmp_path):
    r = ProjectRegistry(db_path=str(tmp_path / "t.db"))
    r.register_database(DatabaseEntity(slug="db1", display_name="DB1", db_type="postgresql",
                                       host="localhost", port=5442, database_name="db1"))
    r.add(Project(slug="myproj", display_name="P", github_url="https://github.com/t/p"))
    return r


@pytest.fixture
def client(registry, monkeypatch):
    monkeypatch.setattr(
        "resource_explorer.registry.ProjectRegistry.__init__",
        lambda self, db_path=None, database_url=None: setattr(self, "__dict__", registry.__dict__) or None)
    from resource_explorer.web.app import app
    return TestClient(app)


def _survey(registry, at=T1):
    with registry._conn() as conn:
        conn.execute("INSERT INTO database_surveys (database_slug, surveyed_at, egeria_report_guid, schema_count, "
                     "table_count, column_count, survey_data, source) VALUES ('db1', ?, '', 1, 1, 1, '{}', 'local')", (at,))


def _tables(registry, rows, at=T1):
    registry.write_detail_rows(
        "database_tables", "db1", at, source="local", coverage_section=SECTION_TABLES,
        rows=[{"schema_name": s, "table_name": t, "row_count": rc, "size_bytes": sz, "table_type": ty}
              for (s, t, rc, sz, ty) in rows])


def test_table_sizes_carries_whole_database_totals_and_how_many_tables_they_cover(registry, client):
    _survey(registry)
    _tables(registry, [("p", "a", 100, 4096, BT), ("p", "b", 50, None, BT), ("p", "c", None, None, BT), ("p", "v", None, None, "VIEW")])
    body = client.get("/api/stats/databases/db1/table_sizes").json()
    assert body["totals"] == {"tables": 3, "rows": 150, "rows_tables": 2, "size_bytes": 4096, "size_tables": 1}


def test_totals_are_none_not_zero_when_nothing_is_established(registry, client):
    _survey(registry)
    _tables(registry, [("p", "a", None, None, BT)])
    body = client.get("/api/stats/databases/db1/table_sizes").json()
    assert body["totals"] is None or (body["totals"]["rows"] is None and body["totals"]["size_bytes"] is None)


def test_statistics_not_measured_carries_the_credential_scope_where_the_rows_show_it(registry, client):
    _survey(registry)
    _tables(registry, [("p", "a", 1, 1, BT)])
    with patch("resource_explorer.db_chart_data.credential_scope",
               return_value={"checked": True, "partial": True, "fraction": "3 of 61 relation(s) in 6 of 8 schemas"}):
        body = client.get("/api/stats/databases/db1/statistics_currency").json()
    assert body["state"] == "not_measured" and "no_table_activity" in body["reasons"]
    assert body["scope"]["partial"] is True and "6 of 8" in body["scope"]["fraction"]
    assert body["run"]["source"] == "local"


class TestStall:
    def run(self, **kw):
        base = {"surveyed_at": "2026-10-09T01:41:00", "engine_action_guid": "ea-1", "engine_action_status": "IN_PROGRESS",
                "engine_action_message": "", "engine_action_read_at": "2026-10-11T13:00:00", "engine_action_read_error": ""}
        base.update(kw)
        return base

    def test_running_for_days_is_stalled_and_is_phase_running(self):
        d = nsr.derive_native_state(self.run(), now=datetime(2026, 10, 11, 13, 0))
        assert (d["state"], d["phase"], d["stalled"]) == (nsr.RUNNING, "running", True)
        assert d["requested_age_s"] == pytest.approx(2.5 * 86400 - 41 * 60, abs=60)

    def test_running_for_minutes_is_not_stalled(self):
        d = nsr.derive_native_state(self.run(surveyed_at="2026-10-11T12:50:00"), now=datetime(2026, 10, 11, 13, 0))
        assert d["phase"] == "running" and d["stalled"] is False

    @pytest.mark.parametrize("word", ["REQUESTED", "APPROVED", "WAITING", "ACTIVATING"])
    def test_waiting_to_start_is_its_own_phase_with_the_shorter_limit(self, word):
        d = nsr.derive_native_state(self.run(engine_action_status=word, surveyed_at="2026-10-11T11:30:00"),
                                    now=datetime(2026, 10, 11, 13, 0))
        assert d["state"] == nsr.RUNNING and d["phase"] == "waiting" and d["stalled"] is True
        fresh = nsr.derive_native_state(self.run(engine_action_status=word, surveyed_at="2026-10-11T12:30:00"),
                                        now=datetime(2026, 10, 11, 13, 0))
        assert fresh["stalled"] is False

    def test_a_finished_action_is_never_stalled(self):
        d = nsr.derive_native_state(self.run(engine_action_status="CANCELLED"), now=datetime(2026, 10, 11, 13, 0))
        assert d["state"] == nsr.FAILED and d["stalled"] is False and d["phase"] == ""

    def test_an_unreadable_request_time_is_unknown_not_false(self):
        d = nsr.derive_native_state(self.run(surveyed_at="garbage"), now=datetime(2026, 10, 11, 13, 0))
        assert d["stalled"] is None


class TestScopeReason:
    def _get(self, client, qs, questions_hit):
        with patch("resource_explorer.surveyors.survey_definition_reader.SurveyDefinitionReader.find_candidate_process_guids_by_questions", return_value=questions_hit), \
                patch("resource_explorer.surveyors.survey_definition_reader.SurveyDefinitionReader.find_candidate_process_guids", return_value=[]):
            return client.get(f"/api/survey-definitions/database/db1/candidates?{qs}").json()

    def test_a_stage_with_no_database_questions_says_no_questions(self, client):
        body = self._get(client, "phase=curate", [])
        assert body["scoping"] == "full-scan" and body["scoping_reason"] == "no_questions"
        assert body["scoping_question_count"] == 0

    def test_questions_with_no_published_survey_says_none_resolved(self, client):
        body = self._get(client, "phase=discovery", [])
        assert body["scoping"] == "full-scan" and body["scoping_reason"] == "none_resolved"
        assert body["scoping_question_count"] > 0
