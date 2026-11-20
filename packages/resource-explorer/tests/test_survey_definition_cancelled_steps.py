"""Tests for cancelled steps in Survey Definition runs.

TR-93: A cancelled step must not read as an error in the Activity view.
A step that was dispatched to Prefect and then CANCELED by a person
(TR-62b: the step stops and writes nothing) is recorded as a step with
status "cancelled", but the executor should NOT append a message to the run's
"errors" list.

Required behaviour:
- A cancelled step is counted and shown as cancelled, not as an error.
  A run with only cancelled steps: stored with a non-error status and the
  summary "Completed · 1 step cancelled" (plural: "Completed · 2 steps cancelled").
- A run with both real errors and cancellations says both:
  "Completed with 1 error · 1 step cancelled" (use "1 error" / "2 errors" here;
  keep the existing wording for a run with errors and no cancellation,
  "Completed with N error(s)" -> do NOT change that existing string).
- A real error still reads as an error (status `error`, existing summary).
  A run with no errors and no cancellations is unchanged
  ("Survey Definition run complete", status `ok`).
- Do not hide the cancellation: it stays in the run's stored detail
  (the step entry with status "cancelled" and its message) so a person
  can see which step was cancelled.
"""
from unittest.mock import MagicMock, patch

import pytest

from resource_explorer.surveyors.prefect_adapter import PrefectFlowRunCancelled
from resource_explorer.surveyors.survey_definition_executor import (
    ResourceTypeAdapter,
    SurveyDefinitionExecutor,
    register_adapter,
)
from resource_explorer.surveyors.survey_definition_reader import SurveyDefinition, SurveyStep


def _fake_registry():
    registry = MagicMock()
    registry.get_survey_definition_guid.return_value = None
    return registry


def _fake_reader(survey_def, candidates=None):
    reader = MagicMock()
    reader.fetch.return_value = survey_def
    reader.find_candidate_process_guids.return_value = candidates or []
    return reader


def _make_adapter(step_key="test_step"):
    """Create a minimal adapter for testing."""
    return ResourceTypeAdapter(
        entity_type="fake",
        technology_type="Fake Tech",
        re_analysis_steps={step_key: MagicMock(return_value={"annotations": []})},
        get_entity=lambda registry, slug: MagicMock(slug=slug, display_name="Fake Entity", github_url=""),
        publish=MagicMock(return_value="report-guid-1"),
    )


def _make_survey_def(step_key="test_step", executes_at="resource-explorer"):
    """Create a survey definition with one step."""
    return SurveyDefinition(
        process_guid="proc-1",
        display_name="Test Survey",
        qualified_name="GovActionProcess::Test",
        supported_technology_type="Fake Tech",
        steps=[
            SurveyStep(
                guid="s1",
                display_name="Test Step",
                qualified_name=f"Step::{step_key}",
                executes_at=executes_at,
                re_analysis_step=step_key,
            ),
        ],
    )


class TestCancelledStepNotAnError:
    """TR-93: A cancelled step must not read as an error in the Activity view."""

    def test_run_with_one_cancelled_step_and_no_errors_is_stored_as_ok(self):
        """A run with one cancelled step and no errors is stored with status 'ok'."""
        adapter = _make_adapter("test_step")
        register_adapter(adapter)

        survey_def = _make_survey_def("test_step")

        registry = _fake_registry()
        reader = _fake_reader(survey_def, candidates=[{"guid": "proc-1", "qualified_name": "GovActionProcess::Test", "display_name": "Test"}])
        executor = SurveyDefinitionExecutor(registry, reader=reader)

        # Mock run_prefect_step to raise PrefectFlowRunCancelled
        import resource_explorer.surveyors.prefect_adapter as prefect_adapter_module
        import resource_explorer.config as config_module

        def _fake_run_prefect_step(entity_type, slug, step_name, runner_kwargs,
                                   dispatch_info=None):
            raise PrefectFlowRunCancelled("Step was cancelled by user", "flow-run-123")

        fake_run_prefect_step = MagicMock(side_effect=_fake_run_prefect_step)

        # Use engine_override='prefect' to force Prefect execution
        # Also patch _prefect_orchestration_enabled to return False so we use the local loop
        # which calls run_prefect_step for individual steps
        with patch.object(prefect_adapter_module, "run_prefect_step", fake_run_prefect_step), \
             patch("resource_explorer.surveyors.survey_definition_executor._prefect_orchestration_enabled", return_value=False), \
             patch.object(config_module, "get_config", return_value=MagicMock(prefect=MagicMock(enabled=False))):
            result = executor.run(entity_type="fake", slug="my-fake", engine_override="prefect")

        # Check the result dict
        assert "cancelled_count" in result, f"cancelled_count not in result: {result.keys()}"
        assert result["cancelled_count"] == 1, f"Expected cancelled_count=1, got {result.get('cancelled_count')}"
        # errors should be empty (cancelled steps don't add to errors)
        assert result.get("errors") == []
        # The step should have status "cancelled"
        assert len(result["steps"]) == 1
        assert result["steps"][0]["status"] == "cancelled"
        # The step should have the cancelled message in detail
        assert "cancelled" in result["steps"][0].get("detail", "").lower()

    def test_run_with_one_cancelled_and_one_failed_step_has_error_status(self):
        """A run with one cancelled and one failed step has status 'error'."""
        adapter = _make_adapter("step1")
        # Add a second step that will fail
        adapter.re_analysis_steps["step2"] = MagicMock(side_effect=RuntimeError("Step 2 failed"))
        register_adapter(adapter)

        survey_def = SurveyDefinition(
            process_guid="proc-2",
            display_name="Test Survey 2",
            qualified_name="GovActionProcess::Test2",
            supported_technology_type="Fake Tech",
            steps=[
                SurveyStep(
                    guid="s1",
                    display_name="Cancelled Step",
                    qualified_name="Step::step1",
                    executes_at="prefect",
                    re_analysis_step="step1",
                ),
                SurveyStep(
                    guid="s2",
                    display_name="Failed Step",
                    qualified_name="Step::step2",
                    executes_at="resource-explorer",
                    re_analysis_step="step2",
                ),
            ],
        )

        registry = _fake_registry()
        reader = _fake_reader(survey_def, candidates=[{"guid": "proc-2", "qualified_name": "GovActionProcess::Test2", "display_name": "Test 2"}])
        executor = SurveyDefinitionExecutor(registry, reader=reader)

        # Mock run_prefect_step to raise PrefectFlowRunCancelled for step1
        import resource_explorer.surveyors.prefect_adapter as prefect_adapter_module
        import resource_explorer.config as config_module

        def _fake_run_prefect_step(entity_type, slug, step_name, runner_kwargs,
                                   dispatch_info=None):
            if step_name == "step1":
                raise PrefectFlowRunCancelled("Step 1 was cancelled", "flow-run-456")
            # For step2, return a result (it's a resource-explorer step that runs locally)
            # The mock adapter.re_analysis_steps["step2"] will raise the error

        fake_run_prefect_step = MagicMock(side_effect=_fake_run_prefect_step)

        with patch.object(prefect_adapter_module, "run_prefect_step", fake_run_prefect_step), \
             patch("resource_explorer.surveyors.survey_definition_executor._prefect_orchestration_enabled", return_value=False), \
             patch.object(config_module, "get_config", return_value=MagicMock(prefect=MagicMock(enabled=False))):
            result = executor.run(entity_type="fake", slug="my-fake2")

        # Check the result dict
        assert "cancelled_count" in result, f"cancelled_count not in result: {result.keys()}"
        assert result["cancelled_count"] == 1, f"Expected cancelled_count=1, got {result.get('cancelled_count')}"
        # errors should contain the failed step's error
        assert len(result.get("errors", [])) == 1, f"Expected 1 error, got {len(result.get('errors', []))}: {result.get('errors')}"
        assert "Step 2 failed" in result["errors"][0]
        # Check step statuses
        steps_by_key = {s["re_analysis_step"]: s for s in result["steps"]}
        assert steps_by_key["step1"]["status"] == "cancelled"
        assert steps_by_key["step2"]["status"] == "error"

    def test_run_with_only_failed_step_has_error_status(self):
        """A run with only a failed step has status 'error' (unchanged behavior)."""
        adapter = _make_adapter("test_step")
        # Make the step fail
        adapter.re_analysis_steps["test_step"] = MagicMock(side_effect=RuntimeError("Test failed"))
        register_adapter(adapter)

        survey_def = _make_survey_def("test_step")

        registry = _fake_registry()
        reader = _fake_reader(survey_def, candidates=[{"guid": "proc-3", "qualified_name": "GovActionProcess::Test3", "display_name": "Test 3"}])
        executor = SurveyDefinitionExecutor(registry, reader=reader)

        result = executor.run(entity_type="fake", slug="my-fake3")

        # Check the result dict
        assert "cancelled_count" in result, f"cancelled_count not in result: {result.keys()}"
        assert result["cancelled_count"] == 0
        # errors should contain the error
        assert len(result.get("errors", [])) == 1
        assert "Test failed" in result["errors"][0]

    def test_run_with_no_errors_and_no_cancellations_is_ok(self):
        """A clean run with no errors and no cancellations has status 'ok' (unchanged)."""
        adapter = _make_adapter("test_step")
        register_adapter(adapter)

        survey_def = _make_survey_def("test_step")

        registry = _fake_registry()
        reader = _fake_reader(survey_def, candidates=[{"guid": "proc-4", "qualified_name": "GovActionProcess::Test4", "display_name": "Test 4"}])
        executor = SurveyDefinitionExecutor(registry, reader=reader)

        result = executor.run(entity_type="fake", slug="my-fake4")

        # Check the result dict
        assert "cancelled_count" in result, f"cancelled_count not in result: {result.keys()}"
        assert result["cancelled_count"] == 0
        # errors should be empty
        assert result.get("errors") == []


class TestCancelledStepSummary:
    """Test the summary wording for cancelled steps."""

    def test_summary_for_cancelled_only_run(self):
        """Summary for a run with only cancelled steps."""
        adapter = _make_adapter("test_step")
        register_adapter(adapter)

        survey_def = _make_survey_def("test_step")

        registry = _fake_registry()
        reader = _fake_reader(survey_def, candidates=[{"guid": "proc-5", "qualified_name": "GovActionProcess::Test5", "display_name": "Test 5"}])
        executor = SurveyDefinitionExecutor(registry, reader=reader)

        import resource_explorer.surveyors.prefect_adapter as prefect_adapter_module
        import resource_explorer.config as config_module

        def _fake_run_prefect_step(entity_type, slug, step_name, runner_kwargs,
                                   dispatch_info=None):
            raise PrefectFlowRunCancelled("Step was cancelled", "flow-run-789")

        fake_run_prefect_step = MagicMock(side_effect=_fake_run_prefect_step)

        with patch.object(prefect_adapter_module, "run_prefect_step", fake_run_prefect_step), \
             patch("resource_explorer.surveyors.survey_definition_executor._prefect_orchestration_enabled", return_value=False), \
             patch.object(config_module, "get_config", return_value=MagicMock(prefect=MagicMock(enabled=False))):
            result = executor.run(entity_type="fake", slug="my-fake5", engine_override="prefect")

        # Check the result dict
        assert "cancelled_count" in result, f"cancelled_count not in result: {result.keys()}"
        assert result["cancelled_count"] == 1
        # Check the step status
        assert len(result["steps"]) == 1
        step = result["steps"][0]
        # The step should have status "cancelled"
        assert step["status"] == "cancelled"

    def test_summary_for_cancelled_and_error_run(self):
        """Summary for a run with both cancelled and error steps."""
        adapter = _make_adapter("step1")
        adapter.re_analysis_steps["step2"] = MagicMock(side_effect=RuntimeError("Step 2 failed"))
        register_adapter(adapter)

        survey_def = SurveyDefinition(
            process_guid="proc-6",
            display_name="Test Survey 6",
            qualified_name="GovActionProcess::Test6",
            supported_technology_type="Fake Tech",
            steps=[
                SurveyStep(
                    guid="s1",
                    display_name="Cancelled Step",
                    qualified_name="Step::step1",
                    executes_at="prefect",
                    re_analysis_step="step1",
                ),
                SurveyStep(
                    guid="s2",
                    display_name="Failed Step",
                    qualified_name="Step::step2",
                    executes_at="resource-explorer",
                    re_analysis_step="step2",
                ),
            ],
        )

        registry = _fake_registry()
        reader = _fake_reader(survey_def, candidates=[{"guid": "proc-6", "qualified_name": "GovActionProcess::Test6", "display_name": "Test 6"}])
        executor = SurveyDefinitionExecutor(registry, reader=reader)

        import resource_explorer.surveyors.prefect_adapter as prefect_adapter_module
        import resource_explorer.config as config_module

        def _fake_run_prefect_step(entity_type, slug, step_name, runner_kwargs,
                                   dispatch_info=None):
            if step_name == "step1":
                raise PrefectFlowRunCancelled("Step 1 was cancelled", "flow-run-abc")
            # For step2, let it fail naturally

        fake_run_prefect_step = MagicMock(side_effect=_fake_run_prefect_step)

        with patch.object(prefect_adapter_module, "run_prefect_step", fake_run_prefect_step), \
             patch("resource_explorer.surveyors.survey_definition_executor._prefect_orchestration_enabled", return_value=False), \
             patch.object(config_module, "get_config", return_value=MagicMock(prefect=MagicMock(enabled=False))):
            result = executor.run(entity_type="fake", slug="my-fake6")

        # Check the result dict
        assert "cancelled_count" in result, f"cancelled_count not in result: {result.keys()}"
        assert result["cancelled_count"] == 1
        # errors should contain the error
        assert len(result.get("errors", [])) == 1


class TestCancelledStepDetailPreserved:
    """Test that cancellation details are preserved in the run's detail."""

    def test_cancelled_step_message_in_detail(self):
        """The cancellation message stays in the step's detail."""
        adapter = _make_adapter("test_step")
        register_adapter(adapter)

        survey_def = _make_survey_def("test_step")

        registry = _fake_registry()
        reader = _fake_reader(survey_def, candidates=[{"guid": "proc-7", "qualified_name": "GovActionProcess::Test7", "display_name": "Test 7"}])
        executor = SurveyDefinitionExecutor(registry, reader=reader)

        import resource_explorer.surveyors.prefect_adapter as prefect_adapter_module
        import resource_explorer.config as config_module

        cancel_msg = "User clicked Cancel on step 'test_step'"

        def _fake_run_prefect_step(entity_type, slug, step_name, runner_kwargs,
                                   dispatch_info=None):
            raise PrefectFlowRunCancelled(cancel_msg, "flow-run-xyz")

        fake_run_prefect_step = MagicMock(side_effect=_fake_run_prefect_step)

        with patch.object(prefect_adapter_module, "run_prefect_step", fake_run_prefect_step), \
             patch("resource_explorer.surveyors.survey_definition_executor._prefect_orchestration_enabled", return_value=False), \
             patch.object(config_module, "get_config", return_value=MagicMock(prefect=MagicMock(enabled=False))):
            result = executor.run(entity_type="fake", slug="my-fake7", engine_override="prefect")

        # The step should have the cancellation message in detail
        assert len(result["steps"]) == 1
        step = result["steps"][0]
        assert step["status"] == "cancelled"
        # The detail should contain the cancellation message
        assert cancel_msg in step.get("detail", "")


class TestCancelledStepMultiple:
    """Test multiple cancelled steps."""

    def test_run_with_two_cancelled_steps(self):
        """A run with two cancelled steps has cancelled_count=2."""
        adapter = _make_adapter("step1")
        # Add a second step
        adapter.re_analysis_steps["step2"] = MagicMock(return_value={"annotations": []})
        register_adapter(adapter)

        survey_def = SurveyDefinition(
            process_guid="proc-8",
            display_name="Test Survey 8",
            qualified_name="GovActionProcess::Test8",
            supported_technology_type="Fake Tech",
            steps=[
                SurveyStep(
                    guid="s1",
                    display_name="Cancelled Step 1",
                    qualified_name="Step::step1",
                    executes_at="prefect",
                    re_analysis_step="step1",
                ),
                SurveyStep(
                    guid="s2",
                    display_name="Cancelled Step 2",
                    qualified_name="Step::step2",
                    executes_at="prefect",
                    re_analysis_step="step2",
                ),
            ],
        )

        registry = _fake_registry()
        reader = _fake_reader(survey_def, candidates=[{"guid": "proc-8", "qualified_name": "GovActionProcess::Test8", "display_name": "Test 8"}])
        executor = SurveyDefinitionExecutor(registry, reader=reader)

        import resource_explorer.surveyors.prefect_adapter as prefect_adapter_module
        import resource_explorer.config as config_module

        def _fake_run_prefect_step(entity_type, slug, step_name, runner_kwargs,
                                   dispatch_info=None):
            raise PrefectFlowRunCancelled(f"Step {step_name} was cancelled", "flow-run-xyz")

        fake_run_prefect_step = MagicMock(side_effect=_fake_run_prefect_step)

        with patch.object(prefect_adapter_module, "run_prefect_step", fake_run_prefect_step), \
             patch("resource_explorer.surveyors.survey_definition_executor._prefect_orchestration_enabled", return_value=False), \
             patch.object(config_module, "get_config", return_value=MagicMock(prefect=MagicMock(enabled=False))):
            result = executor.run(entity_type="fake", slug="my-fake8", engine_override="prefect")

        # Check the result dict
        assert "cancelled_count" in result, f"cancelled_count not in result: {result.keys()}"
        assert result["cancelled_count"] == 2
        # errors should be empty
        assert result.get("errors") == []
        # Both steps should have status "cancelled"
        assert len(result["steps"]) == 2
        for step in result["steps"]:
            assert step["status"] == "cancelled"
