"""resolve_analysis_plan() hardcoded `get_analyses("repo", ...)` and imported
`REPO_ANALYSIS_SOURCE_STEPS` directly -- unlike `_results_map_for(entity_type)`
a short distance below it in the same file, which already dispatches
correctly. Reached via web/routes/work_lists.py -> run_queue.py's
`_handle_analysis_run` for a database/filesystem batch run over a work list
(a real, already-wired feature): before this fix, a non-repo batch always
resolved the REPO's steps for whatever analysis_id was requested.

Mirrors tests/test_fact_layer_resource_type_dispatch.py's style.
"""
from __future__ import annotations

from resource_explorer.workflows.analysis import resolve_analysis_plan


class TestDefaultIsStillRepo:
    def test_omitting_entity_type_behaves_as_repo(self):
        """Every pre-existing caller omitted `entity_type` -- must be exactly
        the pre-fix behaviour."""
        from resource_explorer.surveyors.repo_survey_definition_adapter import (
            REPO_ANALYSIS_SOURCE_STEPS,
        )

        analysis_id = next(iter(REPO_ANALYSIS_SOURCE_STEPS))
        is_ingest_default, steps_default = resolve_analysis_plan(analysis_id)
        is_ingest_explicit, steps_explicit = resolve_analysis_plan(analysis_id, "repo")
        assert is_ingest_default == is_ingest_explicit
        assert steps_default == steps_explicit
        assert steps_default == REPO_ANALYSIS_SOURCE_STEPS[analysis_id]


class TestDatabaseDispatch:
    def test_a_database_analysis_resolves_the_database_steps(self):
        """The live bug, directly: before this fix, resolving ANY analysis_id
        (repo or not) always consulted REPO_ANALYSIS_SOURCE_STEPS -- a
        database-only analysis id, unknown to that map, would resolve to
        `steps=None` (undispatchable) even though the database adapter knows
        exactly what it needs."""
        from resource_explorer.surveyors.database.survey_definition_adapter import (
            DATABASE_ANALYSIS_RE_STEP_MAP,
        )
        from resource_explorer.surveyors.repo_survey_definition_adapter import (
            REPO_ANALYSIS_SOURCE_STEPS,
        )

        analysis_id = next(
            aid for aid in DATABASE_ANALYSIS_RE_STEP_MAP if aid not in REPO_ANALYSIS_SOURCE_STEPS
        )
        is_ingest, steps = resolve_analysis_plan(analysis_id, "database")
        assert is_ingest is False
        assert steps == DATABASE_ANALYSIS_RE_STEP_MAP[analysis_id]

    def test_a_database_only_id_is_undispatchable_under_the_repo_default(self):
        """The bug this test pins directly: the SAME analysis_id resolves to
        nothing when the caller (as every caller used to) omits entity_type,
        because it does not exist in the repo's own step map."""
        from resource_explorer.surveyors.database.survey_definition_adapter import (
            DATABASE_ANALYSIS_RE_STEP_MAP,
        )
        from resource_explorer.surveyors.repo_survey_definition_adapter import (
            REPO_ANALYSIS_SOURCE_STEPS,
        )

        analysis_id = next(
            aid for aid in DATABASE_ANALYSIS_RE_STEP_MAP if aid not in REPO_ANALYSIS_SOURCE_STEPS
        )
        _, steps_as_repo = resolve_analysis_plan(analysis_id)  # old default
        assert steps_as_repo is None


class TestFilesystemDispatch:
    def test_filesystem_has_no_declared_source_steps_and_does_not_crash(self):
        """Filesystem's adapter leaves `analysis_source_steps` undeclared
        (None) -- resolve_analysis_plan must treat that as "no known source
        steps", not raise."""
        is_ingest, steps = resolve_analysis_plan("filesystem_inventory", "filesystem")
        assert is_ingest is False
        assert steps is None or isinstance(steps, list)


class TestUnknownEntityTypeRaises:
    def test_an_unregistered_entity_type_raises_rather_than_defaulting(self):
        import pytest

        from resource_explorer.surveyors.survey_definition_executor import (
            SurveyDefinitionExecutorError,
        )

        with pytest.raises(SurveyDefinitionExecutorError):
            resolve_analysis_plan("whatever", "not-a-real-entity-type")
