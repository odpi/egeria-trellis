"""Tests for the background scheduler's due-analysis execution.

Focus: every run — success or failure — must write a real ActivityEntry
(previously scheduler.py only logged to Python's own logger, invisible from
the UI) and must record the outcome on the schedule row itself, which is
what the Admin Schedules overview reads.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from resource_explorer.registry import DatabaseEntity, Project, ProjectRegistry
from resource_explorer import scheduler


@pytest.fixture
def registry(tmp_path):
    return ProjectRegistry(db_path=str(tmp_path / "test.db"))


@pytest.fixture
def registered_project(registry):
    registry.add(Project(
        slug="myproj",
        display_name="My Project",
        github_url="https://github.com/test/myproj",
        description="",
    ))
    return "myproj"


@pytest.fixture
def registered_database_with_credentials(registry):
    registry.register_database(DatabaseEntity(
        slug="mydb",
        display_name="My DB",
        db_type="postgresql",
        host="localhost",
        port=5432,
        database_name="mydb",
        db_user="admin",
        db_password="secret",
    ))
    return "mydb"


@pytest.fixture
def registered_database_without_credentials(registry):
    registry.register_database(DatabaseEntity(
        slug="mydb-no-creds",
        display_name="My DB (no creds)",
        db_type="postgresql",
        host="localhost",
        port=5432,
        database_name="mydb",
    ))
    return "mydb-no-creds"


def _make_due(registry, entity_type, slug, analysis_id="security_scan"):
    registry.save_schedule(entity_type, slug, analysis_id, "daily", True)
    # save_schedule computes next_run in the future — force it due now.
    with registry._conn() as conn:
        conn.execute(
            "UPDATE resource_schedules SET next_run = '2020-01-01T00:00:00+00:00' "
            "WHERE entity_type=? AND entity_slug=? AND analysis_id=?",
            (entity_type, slug, analysis_id),
        )


class TestRunDueSuccess:
    def test_successful_repo_survey_writes_ok_activity_entry(self, registry, registered_project):
        _make_due(registry, "repo", registered_project)
        fake_result = MagicMock(errors=[])
        with patch("resource_explorer.registry.ProjectRegistry", return_value=registry), \
             patch("resource_explorer.surveyors.survey_orchestrator.SurveyOrchestrator") as MockOrch:
            MockOrch.return_value.run.return_value = fake_result
            scheduler._run_due()

        entries = registry.list_activity(entity_slug=registered_project)
        assert len(entries) == 1
        assert entries[0]["status"] == "ok"
        assert entries[0]["operation"] == "survey"
        assert "completed successfully" in entries[0]["summary"]

    def test_successful_run_updates_schedule_status(self, registry, registered_project):
        _make_due(registry, "repo", registered_project)
        fake_result = MagicMock(errors=[])
        with patch("resource_explorer.registry.ProjectRegistry", return_value=registry), \
             patch("resource_explorer.surveyors.survey_orchestrator.SurveyOrchestrator") as MockOrch:
            MockOrch.return_value.run.return_value = fake_result
            scheduler._run_due()

        rows = registry.get_schedules("repo", registered_project)
        assert rows[0]["last_run_status"] == "ok"
        assert rows[0]["last_run_activity_id"]  # linked to the activity entry
        assert rows[0]["last_run"]

    def test_activity_id_on_schedule_matches_written_entry(self, registry, registered_project):
        _make_due(registry, "repo", registered_project)
        fake_result = MagicMock(errors=[])
        with patch("resource_explorer.registry.ProjectRegistry", return_value=registry), \
             patch("resource_explorer.surveyors.survey_orchestrator.SurveyOrchestrator") as MockOrch:
            MockOrch.return_value.run.return_value = fake_result
            scheduler._run_due()

        activity_id = registry.get_schedules("repo", registered_project)[0]["last_run_activity_id"]
        assert registry.get_activity(activity_id) is not None


class TestRunDueFailure:
    def test_survey_raising_writes_error_activity_entry(self, registry, registered_project):
        _make_due(registry, "repo", registered_project)
        with patch("resource_explorer.registry.ProjectRegistry", return_value=registry), \
             patch("resource_explorer.surveyors.survey_orchestrator.SurveyOrchestrator") as MockOrch:
            MockOrch.return_value.run.side_effect = RuntimeError("survey blew up")
            scheduler._run_due()

        entries = registry.list_activity(entity_slug=registered_project)
        assert len(entries) == 1
        assert entries[0]["status"] == "error"
        assert "survey blew up" in entries[0]["detail"]

    def test_survey_raising_updates_schedule_error_status(self, registry, registered_project):
        _make_due(registry, "repo", registered_project)
        with patch("resource_explorer.registry.ProjectRegistry", return_value=registry), \
             patch("resource_explorer.surveyors.survey_orchestrator.SurveyOrchestrator") as MockOrch:
            MockOrch.return_value.run.side_effect = RuntimeError("boom")
            scheduler._run_due()

        rows = registry.get_schedules("repo", registered_project)
        assert rows[0]["last_run_status"] == "error"

    def test_survey_completing_with_partial_errors_is_recorded_as_error(self, registry, registered_project):
        _make_due(registry, "repo", registered_project)
        fake_result = MagicMock(errors=["some sub-surveyor failed"])
        with patch("resource_explorer.registry.ProjectRegistry", return_value=registry), \
             patch("resource_explorer.surveyors.survey_orchestrator.SurveyOrchestrator") as MockOrch:
            MockOrch.return_value.run.return_value = fake_result
            scheduler._run_due()

        entries = registry.list_activity(entity_slug=registered_project)
        assert entries[0]["status"] == "error"
        assert "some sub-surveyor failed" in entries[0]["detail"]

    def test_missing_repo_writes_error_entry_not_a_crash(self, registry):
        _make_due(registry, "repo", "deleted-repo-slug")
        with patch("resource_explorer.registry.ProjectRegistry", return_value=registry):
            scheduler._run_due()  # must not raise

        entries = registry.list_activity(entity_slug="deleted-repo-slug")
        assert len(entries) == 1
        assert entries[0]["status"] == "error"
        assert "not found" in entries[0]["detail"]
        assert registry.get_schedules("repo", "deleted-repo-slug")[0]["last_run_status"] == "error"


class TestRunDueDatabaseSurvey:
    """Regression coverage for the real bug reported live: scheduled database
    surveys crashed with a bare KeyError('user') because scheduler.py passed
    an empty credentials dict with no resolution step. Fixed to resolve
    db.db_user/db.db_password the same way the manual survey route does."""

    def test_missing_credentials_produces_clear_error_not_a_crash(self, registry, registered_database_without_credentials):
        _make_due(registry, "database", registered_database_without_credentials, analysis_id="schema_inventory")
        with patch("resource_explorer.registry.ProjectRegistry", return_value=registry):
            scheduler._run_due()  # must not raise

        entries = registry.list_activity(entity_slug=registered_database_without_credentials)
        assert len(entries) == 1
        assert entries[0]["status"] == "error"
        assert "No stored database credentials" in entries[0]["detail"]
        assert registry.get_schedules("database", registered_database_without_credentials)[0]["last_run_status"] == "error"

    def test_stored_credentials_are_used_for_the_survey(self, registry, registered_database_with_credentials):
        _make_due(registry, "database", registered_database_with_credentials, analysis_id="schema_inventory")
        with patch("resource_explorer.registry.ProjectRegistry", return_value=registry), \
             patch("resource_explorer.surveyors.database.database_surveyor.run_database_survey") as mock_run:
            mock_run.return_value = {}
            scheduler._run_due()

        mock_run.assert_called_once()
        _, kwargs = mock_run.call_args
        assert kwargs["credentials"] == {"user": "admin", "password": "secret"}

    def test_successful_database_survey_writes_ok_entry(self, registry, registered_database_with_credentials):
        _make_due(registry, "database", registered_database_with_credentials, analysis_id="schema_inventory")
        with patch("resource_explorer.registry.ProjectRegistry", return_value=registry), \
             patch("resource_explorer.surveyors.database.database_surveyor.run_database_survey", return_value={}):
            scheduler._run_due()

        entries = registry.list_activity(entity_slug=registered_database_with_credentials)
        assert entries[0]["status"] == "ok"
        rows = registry.get_schedules("database", registered_database_with_credentials)
        assert rows[0]["last_run_status"] == "ok"

    def test_survey_exception_with_valid_credentials_is_recorded_as_error(self, registry, registered_database_with_credentials):
        _make_due(registry, "database", registered_database_with_credentials, analysis_id="schema_inventory")
        with patch("resource_explorer.registry.ProjectRegistry", return_value=registry), \
             patch("resource_explorer.surveyors.database.database_surveyor.run_database_survey") as mock_run:
            mock_run.side_effect = RuntimeError("connection refused")
            scheduler._run_due()

        entries = registry.list_activity(entity_slug=registered_database_with_credentials)
        assert entries[0]["status"] == "error"
        assert "connection refused" in entries[0]["detail"]

    def test_schema_inventory_dispatches_schema_and_views_steps_only(self, registry, registered_database_with_credentials):
        # Database per-card dispatch fix (D6 prerequisite) — schema_inventory
        # must not also run the statistics step.
        _make_due(registry, "database", registered_database_with_credentials, analysis_id="schema_inventory")
        with patch("resource_explorer.registry.ProjectRegistry", return_value=registry), \
             patch("resource_explorer.surveyors.database.database_surveyor.run_database_survey") as mock_run:
            mock_run.return_value = {}
            scheduler._run_due()

        _, kwargs = mock_run.call_args
        assert kwargs["steps"] == ["schema", "views"]

    def test_row_count_snapshot_dispatches_schema_and_statistics_steps_only(self, registry, registered_database_with_credentials):
        _make_due(registry, "database", registered_database_with_credentials, analysis_id="row_count_snapshot")
        with patch("resource_explorer.registry.ProjectRegistry", return_value=registry), \
             patch("resource_explorer.surveyors.database.database_surveyor.run_database_survey") as mock_run:
            mock_run.return_value = {}
            scheduler._run_due()

        _, kwargs = mock_run.call_args
        assert kwargs["steps"] == ["schema", "statistics"]


class TestRunDueDbDerivedScheduling:
    """Phase 1 slice 14 regression coverage: scheduling a db_derived
    analysis_id (e.g. db_change_rates) must run the zero-fetch
    run_db_derived() step, not fall through to _run_local_db_survey — which
    would wrongly require stored credentials and run the full
    DatabaseSurveyor instead. Found while wiring slice 14's comparator onto
    the scheduler; see scheduler.py's _run_db_survey for the full story."""

    def test_db_change_rates_needs_no_stored_credentials(
        self, registry, registered_database_without_credentials,
    ):
        _make_due(registry, "database", registered_database_without_credentials, analysis_id="db_change_rates")
        with patch("resource_explorer.registry.ProjectRegistry", return_value=registry), \
             patch("resource_explorer.surveyors.database.db_derived.run_db_derived", return_value={}) as mock_run:
            scheduler._run_due()

        mock_run.assert_called_once()
        entries = registry.list_activity(entity_slug=registered_database_without_credentials)
        assert entries[0]["status"] == "ok"
        rows = registry.get_schedules("database", registered_database_without_credentials)
        assert rows[0]["last_run_status"] == "ok"

    def test_db_change_rates_never_reaches_the_credentialed_survey_path(
        self, registry, registered_database_without_credentials,
    ):
        _make_due(registry, "database", registered_database_without_credentials, analysis_id="db_change_rates")
        with patch("resource_explorer.registry.ProjectRegistry", return_value=registry), \
             patch("resource_explorer.surveyors.database.db_derived.run_db_derived", return_value={}), \
             patch("resource_explorer.surveyors.database.database_surveyor.run_database_survey") as mock_survey:
            scheduler._run_due()

        mock_survey.assert_not_called()

    def test_db_derived_exception_is_recorded_as_error_not_a_crash(
        self, registry, registered_database_without_credentials,
    ):
        _make_due(registry, "database", registered_database_without_credentials, analysis_id="db_classification")
        with patch("resource_explorer.registry.ProjectRegistry", return_value=registry), \
             patch("resource_explorer.surveyors.database.db_derived.run_db_derived") as mock_run:
            mock_run.side_effect = RuntimeError("boom")
            scheduler._run_due()  # must not raise

        entries = registry.list_activity(entity_slug=registered_database_without_credentials)
        assert entries[0]["status"] == "error"
        assert "boom" in entries[0]["detail"]


class TestRunDueDispatch:
    """Regression coverage for the dispatch gap found while wiring up
    start_time: scheduler.py used to run the same generic local scan for
    EVERY database analysis_id regardless of what was actually scheduled —
    scheduling an Egeria-native re-survey or a Discovery Survey Definition
    candidate silently ran the wrong thing. Now dispatches by what
    analysis_id actually is."""

    @pytest.fixture
    def registered_database_cataloged_in_egeria(self, registry):
        registry.register_database(DatabaseEntity(
            slug="mydb-egeria",
            display_name="My Egeria DB",
            db_type="postgresql",
            host="localhost",
            port=5432,
            database_name="mydb",
            db_user="admin",
            db_password="secret",
            egeria_asset_guid="guid-1234",
        ))
        return "mydb-egeria"

    def test_egeria_native_entry_dispatches_to_trigger_survey_by_guid_not_local_scan(
        self, registry, registered_database_cataloged_in_egeria,
    ):
        _make_due(registry, "database", registered_database_cataloged_in_egeria, analysis_id="egeria_db_survey")
        with patch("resource_explorer.registry.ProjectRegistry", return_value=registry), \
             patch("resource_explorer.surveyors.database.database_surveyor.run_database_survey") as mock_local, \
             patch("resource_explorer.surveyors.database.egeria_database_surveyor.EgeriaDatabaseSurveyor") as MockSurveyor:
            MockSurveyor.return_value.trigger_survey_by_guid.return_value = "action-guid-1"
            scheduler._run_due()

        mock_local.assert_not_called()  # must NOT silently run the generic local scan
        MockSurveyor.return_value.trigger_survey_by_guid.assert_called_once()
        entries = registry.list_activity(entity_slug=registered_database_cataloged_in_egeria)
        assert entries[0]["status"] == "ok"

    def test_egeria_native_entry_passes_next_run_as_start_time(
        self, registry, registered_database_cataloged_in_egeria,
    ):
        _make_due(registry, "database", registered_database_cataloged_in_egeria, analysis_id="egeria_db_survey")
        next_run = registry.get_schedules("database", registered_database_cataloged_in_egeria)[0]["next_run"]
        with patch("resource_explorer.registry.ProjectRegistry", return_value=registry), \
             patch("resource_explorer.surveyors.database.egeria_database_surveyor.EgeriaDatabaseSurveyor") as MockSurveyor:
            MockSurveyor.return_value.trigger_survey_by_guid.return_value = "action-guid-1"
            scheduler._run_due()

        _, kwargs = MockSurveyor.return_value.trigger_survey_by_guid.call_args
        assert kwargs["start_time"].isoformat() == next_run

    def test_egeria_native_entry_without_asset_guid_produces_clear_error(self, registry):
        registry.register_database(DatabaseEntity(
            slug="mydb-not-cataloged", display_name="Not Cataloged", db_type="postgresql",
            host="localhost", port=5432, database_name="mydb",
            db_user="admin", db_password="secret",  # has creds, but no egeria_asset_guid
        ))
        _make_due(registry, "database", "mydb-not-cataloged", analysis_id="egeria_db_survey")
        with patch("resource_explorer.registry.ProjectRegistry", return_value=registry):
            scheduler._run_due()  # must not raise

        entries = registry.list_activity(entity_slug="mydb-not-cataloged")
        assert entries[0]["status"] == "error"
        assert "not yet published to Egeria" in entries[0]["detail"]

    def test_discovery_candidate_dispatches_to_run_survey_definition(self, registry, registered_database_with_credentials):
        qualified_name = "PostgreSQLSurvey::custom-candidate"  # not in the local catalog
        _make_due(registry, "database", registered_database_with_credentials, analysis_id=qualified_name)
        with patch("resource_explorer.registry.ProjectRegistry", return_value=registry), \
             patch("resource_explorer.surveyors.database.database_surveyor.run_database_survey") as mock_local, \
             patch("resource_explorer.surveyors.survey_definition_executor.run_survey_definition") as mock_run_def:
            mock_run_def.return_value = {"errors": []}
            scheduler._run_due()

        mock_local.assert_not_called()
        mock_run_def.assert_called_once()
        _, kwargs = mock_run_def.call_args
        assert kwargs["survey_definition_ref"] == qualified_name
        entries = registry.list_activity(entity_slug=registered_database_with_credentials)
        assert entries[0]["status"] == "ok"

    def test_discovery_candidate_errors_are_recorded(self, registry, registered_database_with_credentials):
        qualified_name = "PostgreSQLSurvey::custom-candidate"
        _make_due(registry, "database", registered_database_with_credentials, analysis_id=qualified_name)
        with patch("resource_explorer.registry.ProjectRegistry", return_value=registry), \
             patch("resource_explorer.surveyors.survey_definition_executor.run_survey_definition") as mock_run_def:
            mock_run_def.return_value = {"errors": ["step 2 failed"]}
            scheduler._run_due()

        entries = registry.list_activity(entity_slug=registered_database_with_credentials)
        assert entries[0]["status"] == "error"
        assert "step 2 failed" in entries[0]["detail"]


class TestRunDueRepoDispatch:
    """Regression coverage for the repo-side version of the dispatch gap:
    _run_repo_survey used to always run the full SurveyOrchestrator (all 10
    sub-surveyors) regardless of which analysis_id was actually scheduled.
    Now resolves to the specific step(s) via
    repo_survey_definition_adapter.REPO_ANALYSIS_STEP_MAP, mirroring the
    database path's dispatch-by-analysis_id pattern. Also covers repo's own
    (d) — dispatching an analysis_id not in the local catalog to
    run_survey_definition() as a Survey Definition qualified_name, closing
    the gap where the D7a Survey panel's per-candidate Schedule action wrote
    a schedule row the scheduler had no dispatch path for (previously
    misreported as "not found ... schedule may be stale")."""

    def test_mapped_analysis_id_dispatches_with_only_its_steps(self, registry, registered_project):
        _make_due(registry, "repo", registered_project, analysis_id="repository_health")
        fake_result = MagicMock(errors=[])
        with patch("resource_explorer.registry.ProjectRegistry", return_value=registry), \
             patch("resource_explorer.surveyors.survey_orchestrator.SurveyOrchestrator") as MockOrch:
            MockOrch.return_value.run.return_value = fake_result
            scheduler._run_due()

        MockOrch.return_value.run.assert_called_once_with(registered_project, steps=["repo_health"])
        entries = registry.list_activity(entity_slug=registered_project)
        assert entries[0]["status"] == "ok"

    def test_multi_step_analysis_id_passes_all_mapped_steps(self, registry, registered_project):
        _make_due(registry, "repo", registered_project, analysis_id="language_file_classification")
        fake_result = MagicMock(errors=[])
        with patch("resource_explorer.registry.ProjectRegistry", return_value=registry), \
             patch("resource_explorer.surveyors.survey_orchestrator.SurveyOrchestrator") as MockOrch:
            MockOrch.return_value.run.return_value = fake_result
            scheduler._run_due()

        _, kwargs = MockOrch.return_value.run.call_args
        assert set(kwargs["steps"]) == {"repo_language", "repo_file_classification", "repo_file_structure"}

    def test_publish_analysis_id_is_excluded_not_run(self, registry, registered_project):
        _make_due(registry, "repo", registered_project, analysis_id="egeria_publish")
        with patch("resource_explorer.registry.ProjectRegistry", return_value=registry), \
             patch("resource_explorer.surveyors.survey_orchestrator.SurveyOrchestrator") as MockOrch:
            scheduler._run_due()

        MockOrch.assert_not_called()
        entries = registry.list_activity(entity_slug=registered_project)
        assert entries[0]["status"] == "error"
        assert "excluded from scheduled runs by design" in entries[0]["detail"]

    def test_unmapped_analysis_id_dispatches_to_run_survey_definition_not_a_full_survey(self, registry, registered_project):
        # An analysis_id not in the local catalog is now (d) — a Survey
        # Definition qualified_name (the D7a Survey panel's own Schedule
        # action writes exactly this) — dispatched via
        # _run_repo_survey_definition(), not silently run as a full survey
        # and not immediately treated as stale (that was the pre-D7a-
        # scheduling behavior; a real gap this closes).
        qualified_name = "GovActionProcess::CustomRepoSurvey"
        _make_due(registry, "repo", registered_project, analysis_id=qualified_name)
        with patch("resource_explorer.registry.ProjectRegistry", return_value=registry), \
             patch("resource_explorer.surveyors.survey_orchestrator.SurveyOrchestrator") as MockOrch, \
             patch("resource_explorer.surveyors.survey_definition_executor.run_survey_definition") as mock_run_def:
            mock_run_def.return_value = {"errors": []}
            scheduler._run_due()

        MockOrch.assert_not_called()  # must NOT silently fall back to a full survey
        mock_run_def.assert_called_once()
        _, kwargs = mock_run_def.call_args
        assert kwargs["survey_definition_ref"] == qualified_name
        assert "db_user" not in kwargs and "db_pwd" not in kwargs  # repo has no credentials to thread through
        entries = registry.list_activity(entity_slug=registered_project)
        assert entries[0]["status"] == "ok"

    def test_stale_survey_definition_reference_is_recorded_as_an_error(self, registry, registered_project):
        # The Survey Definition itself was since deleted/renamed in Egeria —
        # run_survey_definition() raises SurveyDefinitionExecutorError, which
        # must be recorded as a clean activity-log error, not propagate and
        # crash the whole scheduler iteration.
        qualified_name = "GovActionProcess::DeletedRepoSurvey"
        _make_due(registry, "repo", registered_project, analysis_id=qualified_name)
        from resource_explorer.surveyors.survey_definition_executor import SurveyDefinitionExecutorError

        with patch("resource_explorer.registry.ProjectRegistry", return_value=registry), \
             patch("resource_explorer.surveyors.survey_definition_executor.run_survey_definition") as mock_run_def:
            mock_run_def.side_effect = SurveyDefinitionExecutorError(
                f"No Survey Definition found matching '{qualified_name}'"
            )
            scheduler._run_due()  # must not raise

        entries = registry.list_activity(entity_slug=registered_project)
        assert entries[0]["status"] == "error"
        assert "No Survey Definition found matching" in entries[0]["detail"]

    def test_survey_definition_dispatch_errors_are_recorded(self, registry, registered_project):
        qualified_name = "GovActionProcess::CustomRepoSurvey"
        _make_due(registry, "repo", registered_project, analysis_id=qualified_name)
        with patch("resource_explorer.registry.ProjectRegistry", return_value=registry), \
             patch("resource_explorer.surveyors.survey_definition_executor.run_survey_definition") as mock_run_def:
            mock_run_def.return_value = {"errors": ["step 2 failed"]}
            scheduler._run_due()

        entries = registry.list_activity(entity_slug=registered_project)
        assert entries[0]["status"] == "error"
        assert "step 2 failed" in entries[0]["detail"]


class TestRunDueRepoIngestDispatch:
    """rag_ingestion (action:"ingest") isn't a SurveyOrchestrator step — it
    dispatches to IncrementalIndexer instead, and unlike action:"publish"
    it IS schedulable (a local re-index, not a new Egeria catalog write)."""

    def test_ingest_analysis_id_dispatches_to_incremental_indexer(self, registry, registered_project):
        _make_due(registry, "repo", registered_project, analysis_id="rag_ingestion")
        with patch("resource_explorer.registry.ProjectRegistry", return_value=registry), \
             patch("resource_explorer.ingestion.incremental.IncrementalIndexer") as MockIndexer, \
             patch("resource_explorer.query_cache.QueryCache"), \
             patch("resource_explorer.surveyors.survey_orchestrator.SurveyOrchestrator") as MockOrch:
            scheduler._run_due()

        MockIndexer.return_value.refresh.assert_called_once()
        MockOrch.assert_not_called()
        entries = registry.list_activity(entity_slug=registered_project)
        assert entries[0]["status"] == "ok"

    def test_ingest_failure_is_recorded_as_error_not_raised(self, registry, registered_project):
        _make_due(registry, "repo", registered_project, analysis_id="rag_ingestion")
        with patch("resource_explorer.registry.ProjectRegistry", return_value=registry), \
             patch(
                 "resource_explorer.ingestion.incremental.IncrementalIndexer.refresh",
                 side_effect=RuntimeError("clone missing"),
             ):
            scheduler._run_due()  # must not raise

        entries = registry.list_activity(entity_slug=registered_project)
        assert entries[0]["status"] == "error"
        assert "clone missing" in entries[0]["detail"]


class TestRunDueRepoProfileDispatch:
    """repo_profile_refresh (action:"profile") isn't a SurveyOrchestrator step
    either — it dispatches to IngestionPipeline.refresh_profile() instead.
    Schedulable, like "ingest", unlike "publish"."""

    def test_profile_analysis_id_dispatches_to_refresh_profile_and_auto_classifies(
        self, registry, registered_project,
    ):
        _make_due(registry, "repo", registered_project, analysis_id="repo_profile_refresh")
        fake_result = MagicMock(file_count=3, symbol_count=0)
        with patch("resource_explorer.registry.ProjectRegistry", return_value=registry), \
             patch(
                 "resource_explorer.ingestion.pipeline.IngestionPipeline.refresh_profile",
                 return_value=fake_result,
             ) as mock_refresh, \
             patch("resource_explorer.surveyors.survey_orchestrator.SurveyOrchestrator") as MockOrch:
            MockOrch.return_value.run.return_value = MagicMock(errors=[])
            scheduler._run_due()

        mock_refresh.assert_called_once()
        _, kwargs = mock_refresh.call_args
        assert kwargs["include_symbols"] is False
        # Auto-chains the classification survey, matching the interactive
        # the on-demand POST .../profile-scan behavior — a scheduled
        # refresh shouldn't produce data nothing ever displays.
        from resource_explorer.surveyors.repo_survey_definition_adapter import REPO_ANALYSIS_STEP_MAP
        MockOrch.return_value.run.assert_called_once_with(
            registered_project, steps=REPO_ANALYSIS_STEP_MAP["language_file_classification"],
        )
        entries = registry.list_activity(entity_slug=registered_project)
        assert entries[0]["status"] == "ok"

    def test_scheduled_profile_refresh_updates_last_profiled_at(self, registry, registered_project):
        assert registry.get(registered_project).last_profiled_at == ""
        _make_due(registry, "repo", registered_project, analysis_id="repo_profile_refresh")
        fake_result = MagicMock(file_count=3, symbol_count=0)
        with patch("resource_explorer.registry.ProjectRegistry", return_value=registry), \
             patch(
                 "resource_explorer.ingestion.pipeline.IngestionPipeline.refresh_profile",
                 return_value=fake_result,
             ), \
             patch("resource_explorer.surveyors.survey_orchestrator.SurveyOrchestrator") as MockOrch:
            MockOrch.return_value.run.return_value = MagicMock(errors=[])
            scheduler._run_due()

        assert registry.get(registered_project).last_profiled_at != ""

    def test_profile_failure_is_recorded_as_error_not_raised(self, registry, registered_project):
        _make_due(registry, "repo", registered_project, analysis_id="repo_profile_refresh")
        with patch("resource_explorer.registry.ProjectRegistry", return_value=registry), \
             patch(
                 "resource_explorer.ingestion.pipeline.IngestionPipeline.refresh_profile",
                 side_effect=RuntimeError("rate limited"),
             ):
            scheduler._run_due()  # must not raise

        entries = registry.list_activity(entity_slug=registered_project)
        assert entries[0]["status"] == "error"
        assert "rate limited" in entries[0]["detail"]

    def test_classification_failure_after_successful_refresh_is_recorded_as_error(
        self, registry, registered_project,
    ):
        _make_due(registry, "repo", registered_project, analysis_id="repo_profile_refresh")
        fake_result = MagicMock(file_count=3, symbol_count=0)
        with patch("resource_explorer.registry.ProjectRegistry", return_value=registry), \
             patch(
                 "resource_explorer.ingestion.pipeline.IngestionPipeline.refresh_profile",
                 return_value=fake_result,
             ), \
             patch("resource_explorer.surveyors.survey_orchestrator.SurveyOrchestrator") as MockOrch:
            MockOrch.return_value.run.side_effect = RuntimeError("classifier exploded")
            scheduler._run_due()  # must not raise

        entries = registry.list_activity(entity_slug=registered_project)
        assert entries[0]["status"] == "error"
        assert "classifier exploded" in entries[0]["detail"]


class TestRunDueMisc:
    def test_no_due_schedules_is_a_noop(self, registry):
        with patch("resource_explorer.registry.ProjectRegistry", return_value=registry):
            scheduler._run_due()  # should not raise, nothing to do
        assert registry.list_activity() == []

    def test_disabled_schedule_never_runs(self, registry, registered_project):
        registry.save_schedule("repo", registered_project, "security_scan", "daily", False)
        with patch("resource_explorer.registry.ProjectRegistry", return_value=registry), \
             patch("resource_explorer.surveyors.survey_orchestrator.SurveyOrchestrator") as MockOrch:
            scheduler._run_due()
        MockOrch.assert_not_called()
        assert registry.list_activity() == []

    def test_unknown_entity_type_writes_error_entry(self, registry):
        registry.save_schedule("filesystem", "some-fs", "filesystem_inventory", "daily", True)
        with registry._conn() as conn:
            conn.execute(
                "UPDATE resource_schedules SET next_run = '2020-01-01T00:00:00+00:00' "
                "WHERE entity_type='filesystem' AND entity_slug='some-fs'"
            )
        with patch("resource_explorer.registry.ProjectRegistry", return_value=registry):
            scheduler._run_due()
        entries = registry.list_activity(entity_slug="some-fs")
        assert entries[0]["status"] == "error"
        assert "Unknown entity_type" in entries[0]["detail"]


class TestRfaReconciliation:
    """docs/rfa-egeria-todo-followup.md's "Sync mechanics" — reconciliation
    reuses this scheduler's background loop rather than a second one."""

    def test_reconcile_rfa_actions_delegates_to_rfa_egeria_sync(self, registry):
        with patch("resource_explorer.registry.ProjectRegistry", return_value=registry), \
             patch("resource_explorer.rfa_egeria_sync.reconcile_rfa_actions") as mock_reconcile:
            scheduler._reconcile_rfa_actions()
        mock_reconcile.assert_called_once_with(registry)

    def test_scheduler_loop_runs_both_run_due_and_reconciliation_each_iteration(self, registry):
        # _scheduler_loop() is an infinite loop — exercise one iteration's
        # worth of work by calling time.sleep once then raising to break out,
        # confirming both steps run and a failure in one doesn't skip the
        # other (each already wrapped in its own try/except).
        calls = []
        # _drain_egeria_outbox is patched too, and must be: the loop calls
        # THREE things and this test patched two. The third makes a real
        # Egeria call, so with a non-empty outbox the test blocks on the
        # network — a unit test about call ordering, hanging the suite.
        # Invisible until an outbox had rows in it, which is why it survived.
        with patch("resource_explorer.scheduler.time.sleep", side_effect=[None, KeyboardInterrupt]), \
             patch("resource_explorer.scheduler._run_due", side_effect=lambda: calls.append("run_due")), \
             patch("resource_explorer.scheduler._drain_egeria_outbox", side_effect=lambda: calls.append("drain")), \
             patch("resource_explorer.scheduler._reconcile_rfa_actions", side_effect=lambda: calls.append("reconcile")):
            try:
                scheduler._scheduler_loop()
            except KeyboardInterrupt:
                pass
        assert calls == ["run_due", "reconcile", "drain"]

    def test_run_due_failure_does_not_prevent_reconciliation(self):
        calls = []
        with patch("resource_explorer.scheduler.time.sleep", side_effect=[None, KeyboardInterrupt]), \
             patch("resource_explorer.scheduler._run_due", side_effect=RuntimeError("boom")), \
             patch("resource_explorer.scheduler._drain_egeria_outbox", side_effect=lambda: calls.append("drain")), \
             patch("resource_explorer.scheduler._reconcile_rfa_actions", side_effect=lambda: calls.append("reconcile")):
            try:
                scheduler._scheduler_loop()
            except KeyboardInterrupt:
                pass
        assert calls == ["reconcile", "drain"]
