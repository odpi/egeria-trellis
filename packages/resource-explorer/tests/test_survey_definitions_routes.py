"""Tests for the /api/survey-definitions routes."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from resource_explorer.registry import DatabaseEntity, Project, ProjectRegistry


@pytest.fixture
def registry(tmp_path):
    r = ProjectRegistry(db_path=str(tmp_path / "test.db"))
    r.add(Project(
        slug="myproj",
        display_name="My Project",
        github_url="https://github.com/test/myproj",
    ))
    r.register_database(DatabaseEntity(
        slug="mydb",
        display_name="My DB",
        db_type="postgresql",
        host="localhost",
        port=5432,
        database_name="mydb",
    ))
    return r


@pytest.fixture
def client(registry, monkeypatch):
    monkeypatch.setattr(
        "resource_explorer.registry.ProjectRegistry.__init__",
        lambda self, db_path=None: setattr(self, "__dict__", registry.__dict__) or None,
    )
    from resource_explorer.web.app import app
    return TestClient(app)


def _fake_step(qn, executes_at, re_analysis_step=None, description=""):
    from resource_explorer.surveyors.survey_definition_reader import SurveyStep
    return SurveyStep(
        guid=f"guid-{qn}", display_name=qn, qualified_name=qn,
        executes_at=executes_at, re_analysis_step=re_analysis_step, description=description,
    )


def _fake_survey_def(qn="GovActionProcess::Test", description="A test survey", steps=None):
    from resource_explorer.surveyors.survey_definition_reader import SurveyDefinition
    return SurveyDefinition(
        process_guid="proc-guid", display_name=qn, qualified_name=qn,
        supported_technology_type="PostgreSQL Database",
        steps=steps or [], description=description,
    )


class TestListCandidates:
    def test_unknown_entity_type_returns_400(self, client):
        resp = client.get("/api/survey-definitions/not-a-real-type/mydb/candidates")
        assert resp.status_code == 400

    def test_returns_step_detail_for_valid_candidate(self, client):
        step = _fake_step(
            "GovActionProcessStep::Test::SchemaAndStats", "resource-explorer",
            re_analysis_step="postgres_schema_and_stats", description="",
        )
        survey_def = _fake_survey_def(steps=[step])

        with patch(
            # The questions-scoped path (get_questions(resource_type="database")
            # resolves real local rows unconditionally, so it is always tried
            # first — see the questions-scoped fallback in list_candidates) must
            # be pinned to empty too, or this test's real fixture would win the
            # "if not thin_candidates" fallback race for the wrong reason and
            # silently pass by accident rather than by testing the full-scan
            # path it names. Confirmed live 2026-09-26: once real database
            # Survey Definitions with ScopedBy links existed on the dev
            # platform (Slice 12), the unmocked questions-scoped lookup started
            # returning them for real, which is what broke this test — not a
            # code regression, an incomplete mock.
            "resource_explorer.surveyors.survey_definition_reader.SurveyDefinitionReader.find_candidate_process_guids_by_questions",
            return_value=[],
        ), patch(
            "resource_explorer.surveyors.survey_definition_reader.SurveyDefinitionReader.find_candidate_process_guids",
            return_value=[{"qualified_name": "GovActionProcess::Test", "display_name": "Test", "guid": "proc-guid"}],
        ), patch(
            "resource_explorer.surveyors.survey_definition_reader.SurveyDefinitionReader.fetch",
            return_value=survey_def,
        ):
            resp = client.get("/api/survey-definitions/database/mydb/candidates")

        assert resp.status_code == 200
        data = resp.json()
        assert data["technology_type"] == "PostgreSQL Database"
        assert len(data["candidates"]) == 1
        candidate = data["candidates"][0]
        assert candidate["error"] is None
        assert candidate["description"] == "A test survey"
        assert len(candidate["steps"]) == 1
        step_out = candidate["steps"][0]
        assert step_out["executes_at"] == "resource-explorer"
        assert "SchemaAnalysisAnnotation" in step_out["annotation_types"]

    def test_a_definition_row_carries_the_analyses_its_steps_run(self, client):
        """Brief section 9: "3 steps · runs 5 analyses", derived from the steps' re_analysis_step
        properties through the adapter. A fetched (not authored) definition is used here, so the
        count follows the steps given and not any list kept by hand."""
        steps = [_fake_step("S::schema", "resource-explorer", re_analysis_step="postgres_schema_and_stats"),
                 _fake_step("S::cred", "resource-explorer", re_analysis_step="credential_capability")]
        survey_def = _fake_survey_def(qn="GovActionProcess::Edited", steps=steps)
        survey_def.survey_kind = "scouting"
        with patch(
            "resource_explorer.surveyors.survey_definition_reader.SurveyDefinitionReader.find_candidate_process_guids_by_questions",
            return_value=[],
        ), patch(
            "resource_explorer.surveyors.survey_definition_reader.SurveyDefinitionReader.find_candidate_process_guids",
            return_value=[{"qualified_name": "GovActionProcess::Edited", "display_name": "Edited", "guid": "g"}],
        ), patch(
            "resource_explorer.surveyors.survey_definition_reader.SurveyDefinitionReader.fetch",
            return_value=survey_def,
        ):
            resp = client.get("/api/survey-definitions/database/mydb/candidates")
        runs = resp.json()["candidates"][0]["runs_analyses"]
        assert sorted(a["analysis_id"] for a in runs["own"]) == [
            "credential_capability", "row_count_snapshot", "schema_inventory"]
        assert runs["also"] == []          # no operations step in this edited definition

    def test_survey_kind_query_param_threaded_to_reader(self, client):
        with patch(
            "resource_explorer.surveyors.survey_definition_reader.SurveyDefinitionReader.find_candidate_process_guids",
            return_value=[],
        ) as mock_find:
            resp = client.get("/api/survey-definitions/database/mydb/candidates?survey_kind=discovery")

        assert resp.status_code == 200
        mock_find.assert_called_once()
        assert mock_find.call_args.kwargs.get("survey_kind") == "discovery"

    def test_no_survey_kind_query_param_defaults_to_none(self, client):
        # Backward compatibility — omitting the param must behave exactly as
        # before this feature (every candidate for the technology type).
        with patch(
            # See test_returns_step_detail_for_valid_candidate's comment: the
            # questions-scoped path must be pinned empty too, or this database
            # candidate lookup finds Slice 12's real live Survey Definitions.
            "resource_explorer.surveyors.survey_definition_reader.SurveyDefinitionReader.find_candidate_process_guids_by_questions",
            return_value=[],
        ), patch(
            "resource_explorer.surveyors.survey_definition_reader.SurveyDefinitionReader.find_candidate_process_guids",
            return_value=[],
        ) as mock_find:
            resp = client.get("/api/survey-definitions/database/mydb/candidates")

        assert resp.status_code == 200
        assert mock_find.call_args.kwargs.get("survey_kind") is None

    def test_egeria_step_enriched_with_produced_annotation_types(self, client):
        step = _fake_step(
            "GovActionProcessStep::Test::RowCountSnapshot", "egeria", description="",
        )
        survey_def = _fake_survey_def(steps=[step])
        fake_produced = [{
            "name": "Capture Database Measurements",
            "description": "Capture summary statistics about a database.",
            "explanation": "",
            "annotation_type": "ResourceMeasureAnnotation",
            "analysis_step_name": "Profiling Associated Resources",
        }]

        with patch(
            # See test_returns_step_detail_for_valid_candidate's comment.
            "resource_explorer.surveyors.survey_definition_reader.SurveyDefinitionReader.find_candidate_process_guids_by_questions",
            return_value=[],
        ), patch(
            "resource_explorer.surveyors.survey_definition_reader.SurveyDefinitionReader.find_candidate_process_guids",
            return_value=[{"qualified_name": "GovActionProcess::Test", "display_name": "Test", "guid": "proc-guid"}],
        ), patch(
            "resource_explorer.surveyors.survey_definition_reader.SurveyDefinitionReader.fetch",
            return_value=survey_def,
        ), patch(
            "resource_explorer.surveyors.egeria_tech_type_catalog.EgeriaTechTypeCatalog.get_produced_annotation_types",
            return_value=fake_produced,
        ):
            resp = client.get("/api/survey-definitions/database/mydb/candidates")

        assert resp.status_code == 200
        step_out = resp.json()["candidates"][0]["steps"][0]
        assert step_out["egeria_produced_annotation_types"] == fake_produced

    def test_egeria_tech_type_catalog_failure_does_not_break_listing(self, client):
        step = _fake_step("GovActionProcessStep::Test::RowCountSnapshot", "egeria", description="")
        survey_def = _fake_survey_def(steps=[step])

        with patch(
            # See test_returns_step_detail_for_valid_candidate's comment:
            # without this, CI (no live Egeria/registry) 400s here instead
            # of exercising the full-scan fixture this test actually wants.
            "resource_explorer.surveyors.survey_definition_reader.SurveyDefinitionReader.find_candidate_process_guids_by_questions",
            return_value=[],
        ), patch(
            "resource_explorer.surveyors.survey_definition_reader.SurveyDefinitionReader.find_candidate_process_guids",
            return_value=[{"qualified_name": "GovActionProcess::Test", "display_name": "Test", "guid": "proc-guid"}],
        ), patch(
            "resource_explorer.surveyors.survey_definition_reader.SurveyDefinitionReader.fetch",
            return_value=survey_def,
        ), patch(
            "resource_explorer.surveyors.egeria_tech_type_catalog.EgeriaTechTypeCatalog.get_produced_annotation_types",
            side_effect=Exception("Egeria unreachable"),
        ):
            resp = client.get("/api/survey-definitions/database/mydb/candidates")

        assert resp.status_code == 200, resp.text
        step_out = resp.json()["candidates"][0]["steps"][0]
        assert "egeria_produced_annotation_types" not in step_out

    def test_unfetchable_candidate_surfaces_as_errored_not_crashing(self, client):
        with patch(
            # See test_returns_step_detail_for_valid_candidate's comment.
            "resource_explorer.surveyors.survey_definition_reader.SurveyDefinitionReader.find_candidate_process_guids_by_questions",
            return_value=[],
        ), patch(
            "resource_explorer.surveyors.survey_definition_reader.SurveyDefinitionReader.find_candidate_process_guids",
            return_value=[{"qualified_name": "GovActionProcess::Branching", "display_name": "Branching", "guid": "bad-guid"}],
        ), patch(
            "resource_explorer.surveyors.survey_definition_reader.SurveyDefinitionReader.fetch",
            side_effect=Exception("branching Survey Definitions are not supported yet"),
        ):
            resp = client.get("/api/survey-definitions/database/mydb/candidates")

        assert resp.status_code == 200
        candidates = resp.json()["candidates"]
        assert len(candidates) == 1
        assert candidates[0]["error"] is not None
        assert candidates[0]["steps"] == []

    def test_empty_candidates_list(self, client):
        with patch(
            # See test_returns_step_detail_for_valid_candidate's comment.
            "resource_explorer.surveyors.survey_definition_reader.SurveyDefinitionReader.find_candidate_process_guids_by_questions",
            return_value=[],
        ), patch(
            "resource_explorer.surveyors.survey_definition_reader.SurveyDefinitionReader.find_candidate_process_guids",
            return_value=[],
        ):
            resp = client.get("/api/survey-definitions/database/mydb/candidates")
        assert resp.status_code == 200
        assert resp.json()["candidates"] == []

    def test_phase_param_uses_scoped_lookup_when_questions_resolve(self, client):
        # D2 (docs/survey-question-context-plan.md): phase given, question
        # catalog returns entries -> scoped lookup used, full scan skipped.
        with patch(
            "resource_explorer.surveyors.question_catalog_reader.get_questions",
            return_value=[{"question": "Is this repository actively maintained?", "answering": {}}],
        ), patch(
            "resource_explorer.surveyors.survey_definition_reader.SurveyDefinitionReader.find_candidate_process_guids_by_questions",
            return_value=[{"qualified_name": "GovActionProcess::Scoped", "display_name": "Scoped", "guid": "g1"}],
        ) as mock_scoped, patch(
            "resource_explorer.surveyors.survey_definition_reader.SurveyDefinitionReader.find_candidate_process_guids",
        ) as mock_full_scan:
            resp = client.get("/api/survey-definitions/database/mydb/candidates?phase=scouting")

        assert resp.status_code == 200
        mock_scoped.assert_called_once()
        mock_full_scan.assert_not_called()
        assert resp.json()["candidates"][0]["qualified_name"] == "GovActionProcess::Scoped"

    def test_phase_param_falls_back_to_full_scan_when_scoped_lookup_empty(self, client):
        # No Question resolved to a real GUID yet (or none scoped to a
        # matching Survey Definition) -> falls back to the full scan rather
        # than showing an empty list, per D2's own decision.
        with patch(
            "resource_explorer.surveyors.question_catalog_reader.get_questions",
            return_value=[{"question": "Some question", "answering": {}}],
        ), patch(
            "resource_explorer.surveyors.survey_definition_reader.SurveyDefinitionReader.find_candidate_process_guids_by_questions",
            return_value=[],
        ), patch(
            "resource_explorer.surveyors.survey_definition_reader.SurveyDefinitionReader.find_candidate_process_guids",
            return_value=[{"qualified_name": "GovActionProcess::FullScan", "display_name": "FullScan", "guid": "g2"}],
        ) as mock_full_scan:
            resp = client.get("/api/survey-definitions/database/mydb/candidates?phase=scouting")

        assert resp.status_code == 200
        mock_full_scan.assert_called_once()
        assert resp.json()["candidates"][0]["qualified_name"] == "GovActionProcess::FullScan"

    def test_no_phase_param_falls_back_to_full_scan_when_no_cataloged_questions(self, client):
        # Originally exploited entity_type=database having zero cataloged
        # questions in the real packaged catalog. The 2026-09-21 multi-type
        # authoring stream (re/db-questions-csv) gave database real
        # questions, so this now mocks get_questions() directly to return
        # nothing -- the scenario being tested (a resource type genuinely
        # has no cataloged questions) has to exist regardless of what the
        # production CSV currently contains, same fix as elsewhere in this
        # reconciliation.
        with patch(
            "resource_explorer.surveyors.question_catalog_reader.get_questions",
            return_value=[],
        ), patch(
            "resource_explorer.surveyors.survey_definition_reader.SurveyDefinitionReader.find_candidate_process_guids_by_questions",
        ) as mock_scoped, patch(
            "resource_explorer.surveyors.survey_definition_reader.SurveyDefinitionReader.find_candidate_process_guids",
            return_value=[],
        ) as mock_full_scan:
            resp = client.get("/api/survey-definitions/database/mydb/candidates")

        assert resp.status_code == 200
        mock_scoped.assert_not_called()
        mock_full_scan.assert_called_once()

    def test_no_phase_param_still_uses_scoped_lookup_by_default(self, client):
        # Real live bug, fixed 2026-08-13: the UI never sends phase= at all
        # (see loadSurveyDefinitionsPanel() in index.html), and even when it
        # did, Discovery's own Survey Definitions are ScopedBy Questions
        # tagged asked_at=Scouting — never phase='discovery' — so gating the
        # scoped lookup behind an explicit phase param meant it silently
        # never engaged for the real Discovery UI. Confirmed here: omitting
        # phase entirely (matching exactly what the UI sends) still tries
        # the scoped lookup first, using every cataloged question for the
        # resource type.
        with patch(
            "resource_explorer.surveyors.question_catalog_reader.get_questions",
            return_value=[{"question": "Is this repository actively maintained?", "answering": {}}],
        ) as mock_get_questions, patch(
            "resource_explorer.surveyors.survey_definition_reader.SurveyDefinitionReader.find_candidate_process_guids_by_questions",
            return_value=[{"qualified_name": "GovActionProcess::Scoped", "display_name": "Scoped", "guid": "g1"}],
        ) as mock_scoped, patch(
            "resource_explorer.surveyors.survey_definition_reader.SurveyDefinitionReader.find_candidate_process_guids",
        ) as mock_full_scan:
            resp = client.get("/api/survey-definitions/repo/myproj/candidates?survey_kind=discovery")

        assert resp.status_code == 200
        # `perspectives` is deliberately NOT passed: these rows are what a
        # matched candidate reads its own perspectives from (Survey ->ScopedBy->
        # Question -> Perspective), and narrowing them first would make every
        # candidate report exactly the perspective that was asked for.
        # Perspective narrowing is applied to the assembled candidates instead.
        mock_get_questions.assert_called_once_with(resource_type="repo", phase=None)
        mock_scoped.assert_called_once()
        mock_full_scan.assert_not_called()
        assert resp.json()["candidates"][0]["qualified_name"] == "GovActionProcess::Scoped"

    def test_egeria_native_processes_excludes_delete_kind(self, client):
        # Uses the real config/technology_type_processes.yaml (seeded for
        # "PostgreSQL Relational Database") rather than mocking — this is the
        # externalized table, not something to fake in a unit test.
        with patch(
            # See test_returns_step_detail_for_valid_candidate's comment.
            "resource_explorer.surveyors.survey_definition_reader.SurveyDefinitionReader.find_candidate_process_guids_by_questions",
            return_value=[],
        ), patch(
            "resource_explorer.surveyors.survey_definition_reader.SurveyDefinitionReader.find_candidate_process_guids",
            return_value=[],
        ):
            resp = client.get("/api/survey-definitions/database/mydb/candidates")
        assert resp.status_code == 200, resp.text
        native = resp.json()["egeria_native_processes"]
        kinds = {p["kind"] for p in native}
        assert "delete" not in kinds
        assert "survey_existing" in kinds
        assert "catalog_and_survey" in kinds


class TestRunSurveyDefinition:
    """The route is fire-and-forget (live-reported gap — it used to block the
    whole HTTP request for the entire survey duration with no progress
    indicator, mirroring the exact same fix already applied to
    projects.py's run_scouting_scan). It now only has to: log a 'running'
    activity entry and start a background thread, returning the activity_id
    immediately. The actual run logic (_execute_survey_definition_sync /
    _run_survey_definition_background) is unit-tested directly below,
    decoupled from HTTP/threading — same coverage the old synchronous route
    test had, plus the new background-completion behavior."""

    def test_starts_a_background_run_and_returns_immediately(self, client, registry):
        with patch("resource_explorer.web.routes.survey_definitions._run_survey_definition_background"):
            resp = client.post(
                "/api/survey-definitions/database/mydb/run",
                json={"survey_definition_ref": "GovActionProcess::Test", "db_user": "u", "db_pwd": "p"},
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "started"
        assert "activity_id" in data
        entry = registry.get_activity(data["activity_id"])
        assert entry is not None
        assert entry["status"] == "running"
        assert entry["entity_slug"] == "mydb"


class TestExecuteSurveyDefinitionSync:
    """_execute_survey_definition_sync() is the plain, HTTP/threading-free
    function the background thread (and, transitively, the route) actually
    calls — same decoupled-unit-test shape as _run_scouting_scan_sync's own
    test class in test_scouting_overview_routes.py."""

    def test_returns_executor_result_with_portal_link(self, client):
        from resource_explorer.web.routes.survey_definitions import (
            SurveyDefinitionRunRequest,
            _execute_survey_definition_sync,
        )

        fake_result = {
            "source": "survey-definition", "entity_type": "database", "slug": "mydb",
            "process_qualified_name": "GovActionProcess::Test",
            "steps": [{"step": "SchemaAndStats", "status": "ok"}],
            "errors": [], "egeria_report_guid": "report-guid-123",
        }
        body = SurveyDefinitionRunRequest(survey_definition_ref="GovActionProcess::Test", db_user="u", db_pwd="p")
        with patch(
            "resource_explorer.surveyors.survey_definition_executor.run_survey_definition",
            return_value=fake_result,
        ) as mock_run:
            result = _execute_survey_definition_sync("database", "mydb", body)
        assert result["egeria_report_guid"] == "report-guid-123"
        assert result["egeria_portal_report_url"].endswith("/tech-catalog?guid=report-guid-123")
        mock_run.assert_called_once()
        _, kwargs = mock_run.call_args
        assert kwargs["survey_definition_ref"] == "GovActionProcess::Test"
        assert kwargs["db_user"] == "u"

    def test_without_report_guid_has_no_portal_link(self, client):
        from resource_explorer.web.routes.survey_definitions import (
            SurveyDefinitionRunRequest,
            _execute_survey_definition_sync,
        )

        fake_result = {
            "source": "survey-definition", "entity_type": "database", "slug": "mydb",
            "process_qualified_name": "GovActionProcess::Test",
            "steps": [], "errors": ["something went wrong"], "egeria_report_guid": "",
        }
        body = SurveyDefinitionRunRequest(survey_definition_ref="GovActionProcess::Test")
        with patch(
            "resource_explorer.surveyors.survey_definition_executor.run_survey_definition",
            return_value=fake_result,
        ):
            result = _execute_survey_definition_sync("database", "mydb", body)
        assert "egeria_portal_report_url" not in result


class TestRunSurveyDefinitionBackground:
    """The background thread's own completion behavior — writes the
    terminal status/summary/detail back onto the SAME activity entry the
    route created up front, so a poller sees the full structured result via
    `detail` (JSON-encoded) once status stops being 'running'."""

    def _activity_id(self, registry, slug="mydb", entity_type="database"):
        from resource_explorer.activity_logger import log_survey
        return log_survey(
            registry, entity_type=entity_type, entity_slug=slug, entity_name=slug,
            entity_location="", intent="assessment", status="running", summary="Running…",
        )

    def test_success_writes_ok_status_and_detail_json(self, client, registry):
        import json

        from resource_explorer.web.routes.survey_definitions import (
            SurveyDefinitionRunRequest,
            _run_survey_definition_background,
        )

        activity_id = self._activity_id(registry)
        fake_result = {
            "process_qualified_name": "GovActionProcess::Test",
            "steps": [{"step": "SchemaAndStats", "status": "ok"}],
            "errors": [], "egeria_report_guid": "",
        }
        body = SurveyDefinitionRunRequest(survey_definition_ref="GovActionProcess::Test")
        with patch(
            "resource_explorer.surveyors.survey_definition_executor.run_survey_definition",
            return_value=fake_result,
        ):
            _run_survey_definition_background("database", "mydb", body, activity_id)

        entry = registry.get_activity(activity_id)
        assert entry["status"] == "ok"
        detail = json.loads(entry["detail"])
        assert detail["process_qualified_name"] == "GovActionProcess::Test"

    def test_errors_in_result_write_error_status(self, client, registry):
        import json

        from resource_explorer.web.routes.survey_definitions import (
            SurveyDefinitionRunRequest,
            _run_survey_definition_background,
        )

        activity_id = self._activity_id(registry)
        fake_result = {"process_qualified_name": "GovActionProcess::Test", "steps": [], "errors": ["boom"]}
        body = SurveyDefinitionRunRequest(survey_definition_ref="GovActionProcess::Test")
        with patch(
            "resource_explorer.surveyors.survey_definition_executor.run_survey_definition",
            return_value=fake_result,
        ):
            _run_survey_definition_background("database", "mydb", body, activity_id)

        entry = registry.get_activity(activity_id)
        assert entry["status"] == "error"
        assert json.loads(entry["detail"])["errors"] == ["boom"]

    def test_executor_error_writes_error_status_without_crashing(self, client, registry):
        from resource_explorer.surveyors.survey_definition_executor import SurveyDefinitionExecutorError
        from resource_explorer.web.routes.survey_definitions import (
            SurveyDefinitionRunRequest,
            _run_survey_definition_background,
        )

        activity_id = self._activity_id(registry)
        body = SurveyDefinitionRunRequest(survey_definition_ref="GovActionProcess::Test")
        with patch(
            "resource_explorer.surveyors.survey_definition_executor.run_survey_definition",
            side_effect=SurveyDefinitionExecutorError("no candidates found"),
        ):
            _run_survey_definition_background("database", "mydb", body, activity_id)

        entry = registry.get_activity(activity_id)
        assert entry["status"] == "error"
        assert "no candidates found" in entry["summary"]

    def test_unexpected_exception_writes_error_status_without_crashing(self, client, registry):
        from resource_explorer.web.routes.survey_definitions import (
            SurveyDefinitionRunRequest,
            _run_survey_definition_background,
        )

        activity_id = self._activity_id(registry)
        body = SurveyDefinitionRunRequest(survey_definition_ref="GovActionProcess::Test")
        with patch(
            "resource_explorer.surveyors.survey_definition_executor.run_survey_definition",
            side_effect=RuntimeError("totally unexpected"),
        ):
            _run_survey_definition_background("database", "mydb", body, activity_id)

        entry = registry.get_activity(activity_id)
        assert entry["status"] == "error"
        assert "crashed" in entry["summary"]
