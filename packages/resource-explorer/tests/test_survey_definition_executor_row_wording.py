"""TR-93 read-back round 2: the executor's own activity row (`log_survey`) keeps its step breakdown, and takes only its STATUS
from the cancelled-steps rule. Before TR-93 the row said "Def: 3 steps: 2 ran, 1 skipped by design"; the helper's summary had
replaced it ("Def: Survey Definition run complete"). A run where every step was cancelled is `cancelled` and says nothing was
measured; a run with a completed step plus a cancellation stays ok. Fakes only."""
from unittest.mock import MagicMock, patch

from resource_explorer.surveyors.prefect_adapter import PrefectFlowRunCancelled
from resource_explorer.surveyors.survey_definition_executor import (
    ResourceTypeAdapter, SurveyDefinitionExecutor, register_adapter,
)
from resource_explorer.surveyors.survey_definition_reader import SurveyDefinition, SurveyStep


def _run(runners, *, cancel=(), engine="resource-explorer"):
    """Run a definition of `runners` (name -> callable) plus steps named in `cancel` (they raise a Prefect cancellation, and
    need engine="prefect"). Returns (result, (status, summary) the executor wrote to the activity log)."""
    adapter = ResourceTypeAdapter(
        entity_type="fake", technology_type="Fake Tech",
        re_analysis_steps={**runners, **{n: MagicMock(return_value={"annotations": []}) for n in cancel}},
        get_entity=lambda registry, slug: MagicMock(slug=slug, display_name="Fake Entity", github_url=""),
        publish=MagicMock(return_value="report-guid"))
    register_adapter(adapter)
    names = list(runners) + list(cancel)
    definition = SurveyDefinition(
        process_guid="proc-1", display_name="Def", qualified_name="GovActionProcess::Def",
        supported_technology_type="Fake Tech",
        steps=[SurveyStep(guid=f"s{i}", display_name=n, qualified_name=f"Step::{n}", executes_at="resource-explorer",
                          re_analysis_step=n) for i, n in enumerate(names)])
    reader = MagicMock()
    reader.fetch.return_value = definition
    reader.find_candidate_process_guids.return_value = [
        {"guid": "proc-1", "qualified_name": "GovActionProcess::Def", "display_name": "Def"}]
    registry = MagicMock()
    registry.get_survey_definition_guid.return_value = None

    import resource_explorer.config as config_module
    import resource_explorer.surveyors.prefect_adapter as prefect_adapter

    def _prefect(entity_type, slug, step_name, runner_kwargs, dispatch_info=None):
        if step_name in cancel:
            raise PrefectFlowRunCancelled("stopped by a person", "flow-run-1")
        return {"annotations": []}

    with patch("resource_explorer.surveyors.survey_definition_executor.log_survey") as log, \
         patch.object(prefect_adapter, "run_prefect_step", MagicMock(side_effect=_prefect)), \
         patch("resource_explorer.surveyors.survey_definition_executor._prefect_orchestration_enabled", return_value=False), \
         patch.object(config_module, "get_config", return_value=MagicMock(prefect=MagicMock(enabled=False))):
        result = SurveyDefinitionExecutor(registry, reader=reader).run(entity_type="fake", slug="my-fake",
                                                                       engine_override=engine)
    kwargs = log.call_args.kwargs
    return result, (kwargs["status"], kwargs["summary"])


def _ok(*_a, **_k):
    return {"annotations": []}


def _skipped(*_a, **_k):
    return {"skipped_by_design": "nothing was accepted to run"}


def test_a_clean_run_keeps_its_step_breakdown_in_the_executors_row():
    """The regression the Architect found on 05890c00: this row read "Def: Survey Definition run complete"."""
    _, (status, summary) = _run({"a": _ok, "b": _ok, "c": _skipped})
    assert status == "ok"
    assert summary == "Def: 3 steps: 2 ran, 1 skipped by design"


def test_a_run_where_every_step_was_cancelled_is_cancelled_and_says_nothing_was_measured():
    result, (status, summary) = _run({}, cancel=("slow",), engine="prefect")
    assert result["errors"] == [] and result["cancelled_count"] == 1
    assert status == "cancelled"
    assert summary == "Def: 1 step: cancelled, nothing measured"


def test_a_completed_step_plus_a_cancelled_one_is_ok_with_the_breakdown_and_no_nothing_measured():
    _, (status, summary) = _run({"a": _ok}, cancel=("slow",), engine="prefect")
    assert status == "ok"
    assert summary.startswith("Def: 2 steps: 1 ran, 1 cancelled") and "nothing measured" not in summary


def test_an_error_stays_an_error_in_the_executors_row():
    def boom(*_a, **_k):
        raise RuntimeError("boom")
    _, (status, summary) = _run({"a": _ok, "b": boom})
    assert status == "error" and summary.startswith("Def: 2 steps") and "1 error" in summary
