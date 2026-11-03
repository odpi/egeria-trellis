"""Whole-definition Prefect default (2026-09-28, project owner decision — see
PREFECT-DEFAULT-WHOLE-DEFINITION-IMPLEMENTED.md).

Tonight's dispatch-honesty fix proved real end-to-end Prefect dispatch works
for a whole Survey Definition. This flips a default: an unforced run
(`engine_override=None`) now ATTEMPTS Prefect first for a whole definition —
the inverse of `TestFreshStoredAnswerActuallyReachesPrefect`
(test_survey_definition_executor.py), which proved Prefect CAN be reached.
These tests prove it is now the DEFAULT choice, that the escape hatch
(`engine_override="resource-explorer"`) still forces local unchanged, and
that `PREFECT_ROUTE_LOCAL_STEPS` (the separate, deliberately-still-opt-in
per-step REST path) was not touched by this change.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from resource_explorer.surveyors.survey_definition_executor import (
    ResourceTypeAdapter,
    SurveyDefinitionExecutor,
    register_adapter,
)
from resource_explorer.surveyors.survey_definition_reader import SurveyDefinition, SurveyStep


@pytest.fixture(autouse=True)
def _deterministic_prefect_enabled(monkeypatch):
    """These tests are about the whole-definition ENGINE DECISION, not about
    `prefect.enabled` itself — force the same deterministic behaviour
    `test_prefect_orchestration_respects_engine_routing.py` does, so this
    suite does not depend on ambient `PREFECT_ENABLED`/a real reachable
    server in whatever environment runs it."""
    monkeypatch.setattr(
        "resource_explorer.surveyors.survey_definition_executor._prefect_orchestration_enabled",
        lambda engine_override=None: engine_override != "resource-explorer",
    )


def _fake_registry():
    registry = MagicMock()
    registry.get_survey_definition_guid.return_value = None
    registry.has_assigned_egeria_project.return_value = False
    return registry


def _fake_reader(survey_def):
    reader = MagicMock()
    reader.fetch.return_value = survey_def
    return reader


def _local_only_definition_and_executor(entity_type: str, slug: str):
    entity = MagicMock()
    entity.slug = slug

    def _local_step(entity, registry, **kwargs):
        return {"annotations": []}

    adapter = ResourceTypeAdapter(
        entity_type=entity_type,
        technology_type="Whole Def Default Fixture",
        re_analysis_steps={"local_step": _local_step},
        get_entity=lambda registry, s: entity,
        publish=lambda *a, **kw: "",
    )
    register_adapter(adapter)

    survey_def = SurveyDefinition(
        process_guid=f"proc-{slug}",
        display_name="Whole Def Default Survey",
        qualified_name=f"GovActionProcess::{slug}",
        supported_technology_type="Whole Def Default Fixture",
        steps=[
            SurveyStep(
                guid="s1", display_name="Local", qualified_name="Step::Local",
                executes_at="resource-explorer", re_analysis_step="local_step",
            ),
        ],
    )
    registry = _fake_registry()
    reader = _fake_reader(survey_def)
    executor = SurveyDefinitionExecutor(registry, reader=reader)
    return executor, entity_type, slug


class TestEngineOverrideNoneNowDefaultsToPrefect:
    def test_reachable_prefect_is_attempted_and_named_on_success(self, monkeypatch):
        """The inverse of TestFreshStoredAnswerActuallyReachesPrefect: with
        `engine_override=None` and Prefect reported reachable,
        `_run_via_prefect` must actually be called (not merely reachable in
        principle) — proving Prefect is the default, not just an option."""
        executor, entity_type, slug = _local_only_definition_and_executor(
            "whole_def_default_ok", "wd-ok-entity"
        )

        monkeypatch.setattr(
            "resource_explorer.surveyors.prefect_adapter.check_prefect_reachable_sync",
            lambda: (True, "http://localhost:4200/api"),
        )
        calls = []
        real_run_via_prefect = executor._run_via_prefect

        def _spy(*a, **kw):
            calls.append(True)
            return (
                [{"step": "Step::Local", "re_analysis_step": "local_step",
                  "status": "ok", "engine": "prefect", "flow_run_id": "fr-123"}],
                [{"annotations": []}],
                [],
            )

        monkeypatch.setattr(executor, "_run_via_prefect", _spy)

        result = executor.run(entity_type=entity_type, slug=slug)

        assert calls, "engine_override=None must attempt whole-definition Prefect orchestration"
        assert "Prefect" in result["engine_note"]
        assert "fr-123" in result["engine_note"]

    def test_unreachable_prefect_falls_back_local_with_named_reason(self, monkeypatch):
        """The default must never reintroduce silent degradation: when the
        reachability check fails, the run stays local AND says why — not a
        bare 'ok' indistinguishable from a run that was never offered
        Prefect at all."""
        executor, entity_type, slug = _local_only_definition_and_executor(
            "whole_def_default_unreachable", "wd-unreach-entity"
        )

        monkeypatch.setattr(
            "resource_explorer.surveyors.prefect_adapter.check_prefect_reachable_sync",
            lambda: (False, "http://localhost:4200/api"),
        )
        calls = []
        monkeypatch.setattr(
            executor, "_run_via_prefect",
            lambda *a, **kw: calls.append(True) or (None),
        )

        result = executor.run(entity_type=entity_type, slug=slug)

        assert not calls, "must not attempt Prefect orchestration when unreachable"
        assert result["engine_note"].startswith("Prefect API unreachable")
        assert "ran locally" in result["engine_note"]
        # And the work still completed locally — this is a fallback, not a failure.
        assert result["errors"] == []
        assert result["steps"][0]["status"] == "ok"


class TestEngineOverrideEscapeHatchUnchanged:
    def test_explicit_resource_explorer_still_forces_local(self, monkeypatch):
        """`engine_override="resource-explorer"` must still force local
        execution, unchanged by this default flip — and must not even
        consult reachability, since it was never going to attempt Prefect."""
        executor, entity_type, slug = _local_only_definition_and_executor(
            "whole_def_default_forced_local", "wd-forced-entity"
        )

        reachability_calls = []
        monkeypatch.setattr(
            "resource_explorer.surveyors.prefect_adapter.check_prefect_reachable_sync",
            lambda: reachability_calls.append(True) or (True, "http://localhost:4200/api"),
        )
        prefect_calls = []
        monkeypatch.setattr(
            executor, "_run_via_prefect",
            lambda *a, **kw: prefect_calls.append(True) or None,
        )

        result = executor.run(
            entity_type=entity_type, slug=slug, engine_override="resource-explorer",
        )

        assert not prefect_calls, "explicit local override must not attempt Prefect orchestration"
        assert not reachability_calls, "forced-local override has no reason to check reachability"
        assert result["steps"][0]["status"] == "ok"
        assert result["engine_note"] == ""


class TestRouteLocalStepsDefaultUnchanged:
    def test_route_local_steps_is_still_opt_in(self):
        """Regression guard against flipping the wrong flag: the per-step
        REST dispatch path (`PREFECT_ROUTE_LOCAL_STEPS`) is a separate,
        deliberately-deferred cost tradeoff (~3-4x local from 1s-granularity
        polling) and must stay off by default — only whole-definition
        orchestration's default changed."""
        from resource_explorer.config import PrefectConfig

        # Constructed with no env override, so this is the field's own
        # declared default, not whatever happens to be in this process's
        # ambient environment.
        cfg = PrefectConfig(_env_file=None)
        assert cfg.route_local_steps is False
