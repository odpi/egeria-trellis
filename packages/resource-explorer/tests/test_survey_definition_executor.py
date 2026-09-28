from unittest.mock import MagicMock, patch

from resource_explorer.surveyors import survey_definition_executor as sde_module
from resource_explorer.surveyors.survey_definition_executor import (
    ResourceTypeAdapter,
    SurveyDefinitionExecutor,
    SurveyDefinitionExecutorError,
    register_adapter,
)
from resource_explorer.surveyors.survey_definition_reader import SurveyDefinition, StepLink, SurveyStep
from resource_explorer.surveyors.survey_report import ClassificationAnnotation, assert_unique_qualified_names


def _fake_reader(survey_def, candidates=None):
    reader = MagicMock()
    reader.fetch.return_value = survey_def
    reader.find_candidate_process_guids.return_value = candidates or []
    return reader


def _fake_registry():
    registry = MagicMock()
    registry.get_survey_definition_guid.return_value = None
    return registry


def test_dispatch_loop_runs_known_step_and_reports_unknown_step():
    known_runner = MagicMock(return_value={"ok": True})
    adapter = ResourceTypeAdapter(
        entity_type="fake",
        technology_type="Fake Tech",
        re_analysis_steps={"known_step": known_runner},
        get_entity=lambda registry, slug: object(),
        publish=MagicMock(return_value="report-guid-1"),
    )
    register_adapter(adapter)

    survey_def = SurveyDefinition(
        process_guid="proc-1",
        display_name="Fake Survey",
        qualified_name="GovActionProcess::Fake",
        supported_technology_type="Fake Tech",
        steps=[
            SurveyStep(
                guid="s1", display_name="Known", qualified_name="Step::Known",
                executes_at="resource-explorer", re_analysis_step="known_step",
            ),
            SurveyStep(
                guid="s2", display_name="Unknown", qualified_name="Step::Unknown",
                executes_at="resource-explorer", re_analysis_step="totally_unknown_step",
            ),
            SurveyStep(
                guid="s3", display_name="EgeriaSide", qualified_name="Step::Egeria",
                executes_at="egeria", re_analysis_step=None,
            ),
        ],
    )

    registry = _fake_registry()
    reader = _fake_reader(survey_def, candidates=[{"guid": "proc-1", "qualified_name": "GovActionProcess::Fake", "display_name": "Fake"}])
    executor = SurveyDefinitionExecutor(registry, reader=reader)

    result = executor.run(entity_type="fake", slug="my-fake")

    known_runner.assert_called_once()
    adapter.publish.assert_called_once()
    # Two errors, not one — an executes_at="egeria" step with no registered
    # other_engine_handlers now counts as a real error too (2026-08-24,
    # "closing the stub"): it genuinely never executes anywhere, since RE's
    # own client-side walk is the only thing driving execution today. Used
    # to be silently swallowed (status "skipped_egeria", nothing in `errors`
    # at all) — a run with a step that never ran reported "complete."
    assert len(result["errors"]) == 2
    assert "totally_unknown_step" in result["errors"][0]
    assert "Step::Egeria" in result["errors"][1]
    statuses = {s["step"]: s["status"] for s in result["steps"]}
    assert statuses["Step::Known"] == "ok"
    assert statuses["Step::Unknown"] == "unknown_step"
    assert statuses["Step::Egeria"] == "not_executed_no_egeria_handler"
    assert result["egeria_report_guid"] == "report-guid-1"


def test_other_engine_handler_is_triggered_and_reported():
    known_runner = MagicMock(return_value={"ok": True})
    egeria_trigger = MagicMock(return_value={"engine_action_guid": "action-guid-1"})
    adapter = ResourceTypeAdapter(
        entity_type="fake3",
        technology_type="Fake Tech 3",
        re_analysis_steps={"known_step": known_runner},
        get_entity=lambda registry, slug: object(),
        publish=MagicMock(return_value="report-guid-2"),
        other_engine_handlers={"egeria": egeria_trigger},
    )
    register_adapter(adapter)

    survey_def = SurveyDefinition(
        process_guid="proc-2",
        display_name="Fake Survey 2",
        qualified_name="GovActionProcess::Fake2",
        supported_technology_type="Fake Tech 3",
        steps=[
            SurveyStep(
                guid="s1", display_name="Known", qualified_name="Step::Known",
                executes_at="resource-explorer", re_analysis_step="known_step",
            ),
            SurveyStep(
                guid="s2", display_name="EgeriaSide", qualified_name="Step::Egeria",
                executes_at="egeria", re_analysis_step=None,
            ),
        ],
    )

    registry = _fake_registry()
    reader = _fake_reader(survey_def, candidates=[{"guid": "proc-2", "qualified_name": "GovActionProcess::Fake2", "display_name": "Fake2"}])
    executor = SurveyDefinitionExecutor(registry, reader=reader)

    result = executor.run(entity_type="fake3", slug="my-fake3")

    egeria_trigger.assert_called_once()
    statuses = {s["step"]: s["status"] for s in result["steps"]}
    assert statuses["Step::Egeria"] == "triggered"
    egeria_entry = next(s for s in result["steps"] if s["step"] == "Step::Egeria")
    assert egeria_entry["detail"]["engine_action_guid"] == "action-guid-1"
    assert result["errors"] == []


def test_other_engine_handler_failure_reported_as_error():
    def _failing_handler(entity, registry, step, **_):
        raise RuntimeError("database not cataloged")

    adapter = ResourceTypeAdapter(
        entity_type="fake4",
        technology_type="Fake Tech 4",
        re_analysis_steps={},
        get_entity=lambda registry, slug: object(),
        publish=MagicMock(),
        other_engine_handlers={"egeria": _failing_handler},
    )
    register_adapter(adapter)

    survey_def = SurveyDefinition(
        process_guid="proc-3",
        display_name="Fake Survey 3",
        qualified_name="GovActionProcess::Fake3",
        supported_technology_type="Fake Tech 4",
        steps=[
            SurveyStep(
                guid="s1", display_name="EgeriaSide", qualified_name="Step::Egeria",
                executes_at="egeria", re_analysis_step=None,
            ),
        ],
    )
    registry = _fake_registry()
    reader = _fake_reader(survey_def, candidates=[{"guid": "proc-3", "qualified_name": "GovActionProcess::Fake3", "display_name": "Fake3"}])
    executor = SurveyDefinitionExecutor(registry, reader=reader)

    result = executor.run(entity_type="fake4", slug="my-fake4")

    statuses = {s["step"]: s["status"] for s in result["steps"]}
    assert statuses["Step::Egeria"] == "error"
    assert len(result["errors"]) == 1
    assert "database not cataloged" in result["errors"][0]


class TestRunBatch:
    """D1 (docs/survey-tab-unification-plan.md) — consecutive plain
    'resource-explorer' steps batch into one adapter.run_batch() call when
    the adapter provides one, instead of one call per step. Real fix, not a
    micro-optimization: per-step dispatch meant any step declaring a shared
    D6 resource (e.g. repo's zipball_root) re-acquired it independently
    every time."""

    def _survey_def(self, *step_keys, guid="proc-batch"):
        return SurveyDefinition(
            process_guid=guid,
            display_name="Batchable Survey",
            qualified_name="GovActionProcess::Batchable",
            supported_technology_type="Fake Tech",
            steps=[
                SurveyStep(
                    guid=f"s{i}", display_name=key, qualified_name=f"Step::{key}",
                    executes_at="resource-explorer", re_analysis_step=key,
                )
                for i, key in enumerate(step_keys)
            ],
        )

    def test_consecutive_steps_call_run_batch_once_not_per_step(self):
        run_batch = MagicMock(return_value={"annotations": ["a1", "a2"], "errors": []})
        per_step_runner = MagicMock()  # must NOT be called — proves batching, not per-step dispatch
        adapter = ResourceTypeAdapter(
            entity_type="batchable",
            technology_type="Fake Tech",
            re_analysis_steps={"step_a": per_step_runner, "step_b": per_step_runner},
            get_entity=lambda registry, slug: object(),
            publish=MagicMock(return_value="report-guid-batch"),
            run_batch=run_batch,
        )
        register_adapter(adapter)

        survey_def = self._survey_def("step_a", "step_b")
        registry = _fake_registry()
        reader = _fake_reader(survey_def, candidates=[{"guid": "proc-batch", "qualified_name": "GovActionProcess::Batchable", "display_name": "Batchable"}])
        executor = SurveyDefinitionExecutor(registry, reader=reader)

        result = executor.run(entity_type="batchable", slug="my-repo")

        run_batch.assert_called_once()
        args, _ = run_batch.call_args
        assert args[2] == ["step_a", "step_b"]
        per_step_runner.assert_not_called()
        statuses = {s["step"]: s["status"] for s in result["steps"]}
        assert statuses == {"Step::step_a": "ok", "Step::step_b": "ok"}
        assert result["errors"] == []
        adapter.publish.assert_called_once()

    def test_a_single_batchable_step_still_uses_the_per_step_path(self):
        """A group of exactly one falls through to the original per-step
        runner unchanged — batching only kicks in for 2+ consecutive steps,
        so single-step Survey Definitions (or single-step remainders) don't
        pay any behavior-change cost at all."""
        run_batch = MagicMock()
        per_step_runner = MagicMock(return_value={"annotations": ["a1"]})
        adapter = ResourceTypeAdapter(
            entity_type="batchable2",
            technology_type="Fake Tech",
            re_analysis_steps={"step_a": per_step_runner},
            get_entity=lambda registry, slug: object(),
            publish=MagicMock(return_value="report-guid"),
            run_batch=run_batch,
        )
        register_adapter(adapter)

        survey_def = self._survey_def("step_a", guid="proc-batch2")
        registry = _fake_registry()
        reader = _fake_reader(survey_def, candidates=[{"guid": "proc-batch2", "qualified_name": "GovActionProcess::Batchable", "display_name": "Batchable"}])
        executor = SurveyDefinitionExecutor(registry, reader=reader)

        executor.run(entity_type="batchable2", slug="my-repo")

        run_batch.assert_not_called()
        per_step_runner.assert_called_once()

    def test_run_batch_errors_are_surfaced_and_shared_across_the_group(self):
        run_batch = MagicMock(return_value={"annotations": [], "errors": ["repo_health failed: rate limited"]})
        adapter = ResourceTypeAdapter(
            entity_type="batchable3",
            technology_type="Fake Tech",
            re_analysis_steps={"step_a": MagicMock(), "step_b": MagicMock()},
            get_entity=lambda registry, slug: object(),
            publish=MagicMock(),
            run_batch=run_batch,
        )
        register_adapter(adapter)

        survey_def = self._survey_def("step_a", "step_b", guid="proc-batch3")
        registry = _fake_registry()
        reader = _fake_reader(survey_def, candidates=[{"guid": "proc-batch3", "qualified_name": "GovActionProcess::Batchable", "display_name": "Batchable"}])
        executor = SurveyDefinitionExecutor(registry, reader=reader)

        result = executor.run(entity_type="batchable3", slug="my-repo")

        assert "rate limited" in result["errors"][0]
        statuses = {s["step"]: s["status"] for s in result["steps"]}
        assert statuses == {"Step::step_a": "error", "Step::step_b": "error"}

    def test_run_batch_exception_is_caught_not_raised(self):
        adapter = ResourceTypeAdapter(
            entity_type="batchable4",
            technology_type="Fake Tech",
            re_analysis_steps={"step_a": MagicMock(), "step_b": MagicMock()},
            get_entity=lambda registry, slug: object(),
            publish=MagicMock(),
            run_batch=MagicMock(side_effect=RuntimeError("zipball download failed")),
        )
        register_adapter(adapter)

        survey_def = self._survey_def("step_a", "step_b", guid="proc-batch4")
        registry = _fake_registry()
        reader = _fake_reader(survey_def, candidates=[{"guid": "proc-batch4", "qualified_name": "GovActionProcess::Batchable", "display_name": "Batchable"}])
        executor = SurveyDefinitionExecutor(registry, reader=reader)

        result = executor.run(entity_type="batchable4", slug="my-repo")

        assert "zipball download failed" in result["errors"][0]
        statuses = {s["step"]: s["status"] for s in result["steps"]}
        assert statuses == {"Step::step_a": "error", "Step::step_b": "error"}

    def test_an_unknown_step_key_breaks_the_group_but_doesnt_block_the_rest(self):
        """A step this adapter doesn't recognize must never be silently
        absorbed into a batch — it still gets reported as 'unknown_step',
        same as the non-batched path always did."""
        run_batch = MagicMock(return_value={"annotations": [], "errors": []})
        adapter = ResourceTypeAdapter(
            entity_type="batchable5",
            technology_type="Fake Tech",
            re_analysis_steps={"step_a": MagicMock(), "step_c": MagicMock()},
            get_entity=lambda registry, slug: object(),
            publish=MagicMock(return_value="report-guid"),
            run_batch=run_batch,
        )
        register_adapter(adapter)

        survey_def = self._survey_def("step_a", "step_unknown", "step_c", guid="proc-batch5")
        registry = _fake_registry()
        reader = _fake_reader(survey_def, candidates=[{"guid": "proc-batch5", "qualified_name": "GovActionProcess::Batchable", "display_name": "Batchable"}])
        executor = SurveyDefinitionExecutor(registry, reader=reader)

        result = executor.run(entity_type="batchable5", slug="my-repo")

        statuses = {s["step"]: s["status"] for s in result["steps"]}
        assert statuses["Step::step_unknown"] == "unknown_step"
        # step_a and step_c are each isolated singletons (the unknown step
        # breaks any run of 2+) — run_batch should never be called at all.
        run_batch.assert_not_called()

    def test_database_adapter_has_no_run_batch_default_none(self):
        """Every adapter registered before D1 existed must keep run_batch=None
        (the exact prior one-call-per-step behavior) unless explicitly opted in."""
        from resource_explorer.surveyors.survey_definition_executor import get_adapter
        import resource_explorer.surveyors.database.survey_definition_adapter  # noqa: F401
        assert get_adapter("database").run_batch is None


def test_unknown_entity_type_raises():
    registry = _fake_registry()
    executor = SurveyDefinitionExecutor(registry, reader=_fake_reader(None))
    try:
        executor.run(entity_type="not-a-real-type", slug="whatever")
        assert False, "expected SurveyDefinitionExecutorError"
    except SurveyDefinitionExecutorError:
        pass


def test_multiple_candidates_raises_ambiguity_error():
    adapter = ResourceTypeAdapter(
        entity_type="fake2",
        technology_type="Fake Tech 2",
        re_analysis_steps={},
        get_entity=lambda registry, slug: object(),
        publish=MagicMock(),
    )
    register_adapter(adapter)

    registry = _fake_registry()
    reader = _fake_reader(
        None,
        candidates=[
            {"guid": "g1", "qualified_name": "Survey::One", "display_name": "One"},
            {"guid": "g2", "qualified_name": "Survey::Two", "display_name": "Two"},
        ],
    )
    executor = SurveyDefinitionExecutor(registry, reader=reader)
    try:
        executor.run(entity_type="fake2", slug="whatever")
        assert False, "expected SurveyDefinitionExecutorError for ambiguous candidates"
    except SurveyDefinitionExecutorError as exc:
        assert "Survey::One" in str(exc) and "Survey::Two" in str(exc)


class TestPublishGatedOnAssignedEgeriaProject:
    """Confirmed live 2026-08-27: this used to publish unconditionally for any
    repo a Survey Definition ran against, decided-in-Egeria or not — the only
    other publish path (egeria.py's manual button) has always gated this."""

    def _run(self, registry, publish=None):
        adapter = ResourceTypeAdapter(
            entity_type="fake3",
            technology_type="Fake Tech",
            re_analysis_steps={"known_step": MagicMock(return_value={"annotations": ["a1"]})},
            get_entity=lambda registry, slug: object(),
            publish=publish or MagicMock(return_value="report-guid"),
        )
        register_adapter(adapter)
        survey_def = SurveyDefinition(
            process_guid="proc-3",
            display_name="Fake Survey 3",
            qualified_name="GovActionProcess::Fake3",
            supported_technology_type="Fake Tech",
            steps=[
                SurveyStep(
                    guid="s1", display_name="Known", qualified_name="Step::Known",
                    executes_at="resource-explorer", re_analysis_step="known_step",
                ),
            ],
        )
        reader = _fake_reader(survey_def, candidates=[
            {"guid": "proc-3", "qualified_name": "GovActionProcess::Fake3", "display_name": "Fake3"},
        ])
        executor = SurveyDefinitionExecutor(registry, reader=reader)
        result = executor.run(entity_type="fake3", slug="my-fake3")
        return result, adapter

    def test_unassigned_resource_skips_publish(self):
        registry = _fake_registry()
        registry.has_assigned_egeria_project.return_value = False
        result, adapter = self._run(registry)

        adapter.publish.assert_not_called()
        assert result["published"] is False
        assert result["egeria_report_guid"] == ""

    def test_assigned_resource_still_publishes(self):
        registry = _fake_registry()
        registry.has_assigned_egeria_project.return_value = True
        result, adapter = self._run(registry)

        adapter.publish.assert_called_once()
        assert result["published"] is True
        assert result["egeria_report_guid"] == "report-guid"

    def test_a_publish_failure_is_reported_as_an_error_not_raised(self):
        registry = _fake_registry()
        registry.has_assigned_egeria_project.return_value = True
        result, adapter = self._run(registry, publish=MagicMock(side_effect=RuntimeError("egeria down")))

        assert result["published"] is False
        assert any("egeria down" in e for e in result["errors"])


class TestPublishChoiceThreeStates:
    """run-in-background plan: `SurveyDefinitionExecutor.run(publish=...)`
    honours the same per-run choice and the same three `published` states
    (True/False/"queued") the Analyses-card path does — no unconditional
    True. An adapter that predates the choice (a plain function with no
    `defer_drain` parameter) must keep working exactly as before: the
    executor detects that via signature inspection and never passes the
    kwarg it cannot accept."""

    def _run(self, registry, *, run_publish=None, adapter_publish=None):
        adapter = ResourceTypeAdapter(
            entity_type="fake_pub_choice",
            technology_type="Fake Tech",
            re_analysis_steps={"known_step": MagicMock(return_value={"annotations": ["a1"]})},
            get_entity=lambda registry, slug: object(),
            publish=adapter_publish or (lambda entity, outputs, at, reg, *, defer_drain=False: "report-guid"),
        )
        register_adapter(adapter)
        survey_def = SurveyDefinition(
            process_guid="proc-pub-choice",
            display_name="Fake Survey Publish Choice",
            qualified_name="GovActionProcess::FakePubChoice",
            supported_technology_type="Fake Tech",
            steps=[
                SurveyStep(
                    guid="s1", display_name="Known", qualified_name="Step::Known",
                    executes_at="resource-explorer", re_analysis_step="known_step",
                ),
            ],
        )
        reader = _fake_reader(survey_def, candidates=[
            {"guid": "proc-pub-choice", "qualified_name": "GovActionProcess::FakePubChoice",
             "display_name": "FakePubChoice"},
        ])
        registry.has_assigned_egeria_project.return_value = True
        executor = SurveyDefinitionExecutor(registry, reader=reader)
        kwargs = {} if run_publish is None else {"publish": run_publish}
        result = executor.run(entity_type="fake_pub_choice", slug="my-fake", **kwargs)
        return result

    def test_background_reports_queued_not_true(self):
        result = self._run(_fake_registry(), run_publish="background")
        assert result["published"] == "queued"
        assert result["egeria_report_guid"] == "report-guid"

    def test_wait_reports_true(self):
        result = self._run(_fake_registry(), run_publish="wait")
        assert result["published"] is True

    def test_no_choice_matches_the_config_default(self):
        """Byte-for-byte: an unset `publish` must behave exactly as it did
        before this feature existed."""
        result = self._run(_fake_registry())
        assert result["published"] is True

    def test_an_adapter_that_predates_defer_drain_is_never_passed_it(self):
        """A plain 4-arg publish callable (every adapter before this plan,
        and every test double using one) must not receive an unexpected
        kwarg — and since it was never told to defer, `published` must
        report True even when background was requested, which is the
        honest answer for an adapter that cannot defer."""
        calls = []

        def old_style_publish(entity, step_outputs, surveyed_at, registry):
            calls.append((entity, step_outputs, surveyed_at, registry))
            return "report-guid-old"

        result = self._run(_fake_registry(), run_publish="background",
                           adapter_publish=old_style_publish)
        assert len(calls) == 1
        assert result["published"] is True
        assert result["egeria_report_guid"] == "report-guid-old"


def _guarded_survey_def(entity_type: str, upstream_guard: str, required_guard: str):
    """Two RE-side steps, `upstream` -> `downstream`, linked with a real
    (non-"Any") guard. `upstream_guard` is what the fake `upstream_runner`
    below is told to return as its output's "guard" key; `required_guard` is
    what the link demands. Equal -> downstream runs; different -> it doesn't."""
    return SurveyDefinition(
        process_guid="proc-guard",
        display_name="Guarded Survey",
        qualified_name="GovActionProcess::Guarded",
        supported_technology_type="Guard Tech",
        steps=[
            SurveyStep(
                guid="up", display_name="Upstream", qualified_name="Step::Upstream",
                executes_at="resource-explorer", re_analysis_step="upstream_step",
            ),
            SurveyStep(
                guid="down", display_name="Downstream", qualified_name="Step::Downstream",
                executes_at="resource-explorer", re_analysis_step="downstream_step",
            ),
        ],
        links=[
            StepLink(previous_guid="up", next_guid="down", guard=required_guard,
                      mandatory_guard=False),
        ],
    )


class TestGuardEvaluationInTheLocalLoop:
    """docs/survey-guard-evaluation-design.md §5 — the local (non-Prefect) loop
    now honours a real (non-"Any") guard on a step link instead of running
    every step in the definition regardless. Prefect is forced off so these
    exercise the fallback loop specifically, deterministically, regardless of
    whether a Prefect server happens to be reachable on the machine running
    the suite."""

    def _run_guarded(self, upstream_guard, required_guard, upstream_side_effect=None):
        upstream_runner = MagicMock(
            side_effect=upstream_side_effect,
            return_value=({"guard": upstream_guard} if upstream_guard is not None else {}),
        )
        downstream_runner = MagicMock(return_value={"ok": True})
        adapter = ResourceTypeAdapter(
            entity_type="guardfake",
            technology_type="Guard Tech",
            re_analysis_steps={
                "upstream_step": upstream_runner,
                "downstream_step": downstream_runner,
            },
            get_entity=lambda registry, slug: object(),
            publish=MagicMock(return_value="report-guid-guard"),
        )
        register_adapter(adapter)

        survey_def = _guarded_survey_def("guardfake", upstream_guard, required_guard)
        registry = _fake_registry()
        registry.has_assigned_egeria_project.return_value = False
        reader = _fake_reader(survey_def, candidates=[
            {"guid": "proc-guard", "qualified_name": "GovActionProcess::Guarded",
             "display_name": "Guarded"},
        ])
        executor = SurveyDefinitionExecutor(registry, reader=reader)

        with patch.object(sde_module, "_prefect_orchestration_enabled", return_value=False):
            result = executor.run(entity_type="guardfake", slug="my-guardfake")

        return result, upstream_runner, downstream_runner

    def test_an_unsatisfied_guard_skips_the_step_without_running_it(self):
        """The regression this guards: a test that only inspected the report
        dict would still pass if the guard branch silently fell through and
        ran the downstream step anyway. Asserting `downstream_runner.assert_not_called()`
        is what actually exercises the skip — not just its label."""
        result, upstream_runner, downstream_runner = self._run_guarded(
            upstream_guard="good_enough", required_guard="needs_deep")

        upstream_runner.assert_called_once()
        downstream_runner.assert_not_called()

        statuses = {s["step"]: s for s in result["steps"]}
        assert statuses["Step::Upstream"]["status"] == "ok"
        assert statuses["Step::Downstream"]["status"] == "skipped_by_design"
        assert "needs_deep" in statuses["Step::Downstream"]["detail"]
        assert "good_enough" in statuses["Step::Downstream"]["detail"]
        # A skip is not a failure — must not appear in errors.
        assert result["errors"] == []

    def test_no_guard_emitted_at_all_also_skips_a_guarded_step(self):
        """Today's real situation (docs/survey-guard-evaluation-design.md
        §2.4): no RE step runner returns a "guard" key at all. A guarded
        downstream step must not be silently run just because nothing said
        no — absence of a matching guard is what "not satisfied" means."""
        result, upstream_runner, downstream_runner = self._run_guarded(
            upstream_guard=None, required_guard="needs_deep")

        downstream_runner.assert_not_called()
        statuses = {s["step"]: s for s in result["steps"]}
        assert statuses["Step::Downstream"]["status"] == "skipped_by_design"
        # Upstream DID run and explicitly produced no guard (its output had
        # no "guard" key at all) — distinct from an upstream that never ran,
        # which is the "never recorded" wording, exercised below.
        assert "produced None" in statuses["Step::Downstream"]["detail"]

    def test_an_upstream_that_never_ran_reads_differently_from_one_that_ran_with_no_guard(self):
        """Distinguishes 'upstream ran, said nothing' from 'upstream never
        ran at all' — see `_guard_check`'s two reason strings. Forced by
        making the upstream raise, so it is recorded as an error and never
        reaches `produced_guard`."""
        result, upstream_runner, downstream_runner = self._run_guarded(
            upstream_guard=None, required_guard="needs_deep",
            upstream_side_effect=RuntimeError("upstream blew up"))

        downstream_runner.assert_not_called()
        statuses = {s["step"]: s for s in result["steps"]}
        assert statuses["Step::Upstream"]["status"] == "error"
        assert statuses["Step::Downstream"]["status"] == "skipped_by_design"
        assert "never recorded" in statuses["Step::Downstream"]["detail"]

    def test_a_satisfied_guard_lets_the_step_run(self):
        result, upstream_runner, downstream_runner = self._run_guarded(
            upstream_guard="needs_deep", required_guard="needs_deep")

        downstream_runner.assert_called_once()
        statuses = {s["step"]: s for s in result["steps"]}
        assert statuses["Step::Downstream"]["status"] == "ok"

    def test_an_unconditional_link_is_unaffected(self):
        """guard="Any" (the default for every definition that exists today,
        per step_outcome.py's 2026-08-21 decision) must keep running
        regardless — this change must be a no-op for the common case."""
        result, upstream_runner, downstream_runner = self._run_guarded(
            upstream_guard=None, required_guard="Any")

        downstream_runner.assert_called_once()
        statuses = {s["step"]: s for s in result["steps"]}
        assert statuses["Step::Downstream"]["status"] == "ok"


def test_annotations_from_two_definitions_sharing_a_step_get_distinct_provenance():
    """A step authored into more than one Survey Definition — e.g.
    `repo_secret_scan`, included by both
    docs/dr-egeria/survey-definitions/repo-survey-definition-compliance.md
    and repo-survey-definition-full.md — must not collide when its own
    surveyor emits a keyless summary annotation once per run (SecretScanSurveyor's
    `scan_summary`, secret_scan.py).

    Regression for the live collision reproduced 2026-09-04: publishing
    refused with "Two annotations would publish the same qualifiedName
    'Annotation::egeria_python_git::<ts>::scan_summary'" because both
    definitions' runs contributed an identically-shaped, keyless annotation.
    `_stamp_definition_provenance` (survey_definition_executor.py) now stamps
    every keyless annotation with the Survey Definition's own qualified_name
    at the point its step's output is collected, so the two runs' annotations
    carry distinct, provenance-bearing item_keys instead.
    """

    def shared_step_runner(entity, registry, **_):
        # Mirrors SecretScanSurveyor.run()'s real shape: one keyless
        # check_name="scan_summary" ClassificationAnnotation per call.
        return {"annotations": [
            ClassificationAnnotation(
                check_name="scan_summary",
                summary="No matches against the vendored ruleset.",
                analysis_step="SecretScan",
            )
        ]}

    adapter = ResourceTypeAdapter(
        entity_type="fake_shared",
        technology_type="Fake Tech Shared",
        re_analysis_steps={"repo_secret_scan": shared_step_runner},
        get_entity=lambda registry, slug: object(),
        publish=MagicMock(return_value="report-guid-shared"),
    )
    register_adapter(adapter)

    def make_def(qn: str) -> SurveyDefinition:
        return SurveyDefinition(
            process_guid=f"proc-{qn}",
            display_name=qn,
            qualified_name=qn,
            supported_technology_type="Fake Tech Shared",
            steps=[
                SurveyStep(
                    guid="s1", display_name="SecretScan", qualified_name="Step::SecretScan",
                    executes_at="resource-explorer", re_analysis_step="repo_secret_scan",
                ),
            ],
        )

    registry = _fake_registry()

    collected_outputs = []

    def capture_publish(entity, step_outputs, surveyed_at, registry):
        collected_outputs.extend(step_outputs)
        return "report-guid-shared"

    adapter.publish = capture_publish
    registry.has_assigned_egeria_project.return_value = True

    for qn in ("GovActionProcess::Compliance", "GovActionProcess::Full"):
        survey_def = make_def(qn)
        reader = _fake_reader(survey_def)
        executor = SurveyDefinitionExecutor(registry, reader=reader)
        executor.run(entity_type="fake_shared", slug="my-fake-shared", survey_definition_ref=qn)

    all_annotations = [
        ann for output in collected_outputs for ann in output.get("annotations", [])
    ]
    assert len(all_annotations) == 2

    item_keys = {ann.item_key for ann in all_annotations}
    assert item_keys == {"GovActionProcess::Compliance", "GovActionProcess::Full"}

    # The actual publish-time guard: must not raise on the merged set.
    assert_unique_qualified_names("egeria_python_git", all_annotations)


class TestEngineOverride:
    """SurveyDefinitionExecutor.run(engine_override=...) — the per-RUN choice
    of which engine runs this definition's 'resource-explorer'-tagged steps,
    independent of (and taking precedence over, for this run only) the global
    `config.prefect.enabled`/`route_local_steps` config. See `run`'s own
    docstring for the full contract.

    `_prefect_orchestration_enabled` is forced False in every test here
    (same technique `TestGuardEvaluationInTheLocalLoop` already uses) so
    these exercise the per-step `_use_prefect` predicate in the local loop
    specifically, deterministically — whole-definition Prefect orchestration
    is a separate mechanism with its own gate, tested in
    TestEngineOverrideAndWholeDefinitionOrchestration below.
    """

    @staticmethod
    def _adapter_with_one_step(entity_type, runner, other_engine_handlers=None):
        adapter = ResourceTypeAdapter(
            entity_type=entity_type,
            technology_type="Fake Tech",
            re_analysis_steps={"known_step": runner},
            get_entity=lambda registry, slug: MagicMock(slug=slug, display_name="", github_url=""),
            publish=MagicMock(return_value="report-guid"),
            other_engine_handlers=other_engine_handlers or {},
        )
        register_adapter(adapter)
        return adapter

    @staticmethod
    def _one_step_def(entity_type, executes_at="resource-explorer", step_key="known_step"):
        return SurveyDefinition(
            process_guid=f"proc-{entity_type}",
            display_name="One Step Survey",
            qualified_name=f"GovActionProcess::{entity_type}",
            supported_technology_type="Fake Tech",
            steps=[
                SurveyStep(
                    guid="s1", display_name="Known", qualified_name="Step::Known",
                    executes_at=executes_at, re_analysis_step=step_key,
                ),
            ],
        )

    def _run(self, entity_type, adapter_kwargs=None, engine_override=None,
              config_enabled=False, config_route_local=False, executes_at="resource-explorer"):
        runner = MagicMock(return_value={"ok": True})
        adapter = self._adapter_with_one_step(entity_type, runner, **(adapter_kwargs or {}))
        survey_def = self._one_step_def(entity_type, executes_at=executes_at)
        registry = _fake_registry()
        registry.has_assigned_egeria_project.return_value = False
        reader = _fake_reader(survey_def, candidates=[
            {"guid": f"proc-{entity_type}", "qualified_name": f"GovActionProcess::{entity_type}",
             "display_name": "One Step Survey"},
        ])
        executor = SurveyDefinitionExecutor(registry, reader=reader)

        fake_cfg = MagicMock()
        fake_cfg.prefect.enabled = config_enabled
        fake_cfg.prefect.route_local_steps = config_route_local

        import resource_explorer.config as config_module
        import resource_explorer.surveyors.prefect_adapter as prefect_adapter_module

        with patch.object(sde_module, "_prefect_orchestration_enabled", return_value=False), \
             patch.object(config_module, "get_config", return_value=fake_cfg), \
             patch.object(prefect_adapter_module, "run_prefect_step",
                          return_value={"ok": True, "via": "prefect"}) as fake_run_prefect_step:
            result = executor.run(
                entity_type=entity_type, slug="my-thing", engine_override=engine_override,
            )
        return result, runner, fake_run_prefect_step

    def test_prefect_override_forces_a_resource_explorer_step_to_prefect_regardless_of_config(self):
        """(a) engine_override='prefect' on a step tagged
        executes_at='resource-explorer' routes it to Prefect even though the
        global config (enabled=False) would never have routed it there."""
        result, local_runner, fake_run_prefect_step = self._run(
            "override-a", engine_override="prefect",
            config_enabled=False, config_route_local=False,
        )
        fake_run_prefect_step.assert_called_once()
        local_runner.assert_not_called()
        statuses = {s["step"]: s for s in result["steps"]}
        assert statuses["Step::Known"]["status"] == "ok"
        assert statuses["Step::Known"]["engine"] == "prefect"

    def test_resource_explorer_override_forces_a_step_local_regardless_of_config(self):
        """(b) engine_override='resource-explorer' keeps a step local even
        though the global config (enabled=True, route_local_steps=True) would
        otherwise have routed it to Prefect."""
        result, local_runner, fake_run_prefect_step = self._run(
            "override-b", engine_override="resource-explorer",
            config_enabled=True, config_route_local=True,
        )
        local_runner.assert_called_once()
        fake_run_prefect_step.assert_not_called()
        statuses = {s["step"]: s for s in result["steps"]}
        assert statuses["Step::Known"]["status"] == "ok"
        assert "engine" not in statuses["Step::Known"]

    def test_none_preserves_existing_config_driven_behavior_prefect_side(self):
        """(c) Regression guard: with no override, a 'resource-explorer' step
        still goes to Prefect exactly when the existing config says so
        (enabled AND route_local_steps) — unchanged from before this feature
        existed."""
        result, local_runner, fake_run_prefect_step = self._run(
            "override-c1", engine_override=None,
            config_enabled=True, config_route_local=True,
        )
        fake_run_prefect_step.assert_called_once()
        local_runner.assert_not_called()

    def test_none_preserves_existing_config_driven_behavior_local_side(self):
        """(c) continued: and still stays local when the config says so —
        either flag off is enough, same as before."""
        result, local_runner, fake_run_prefect_step = self._run(
            "override-c2", engine_override=None,
            config_enabled=True, config_route_local=False,
        )
        local_runner.assert_called_once()
        fake_run_prefect_step.assert_not_called()

    def test_invalid_engine_override_raises_value_error(self):
        """(d) A typo'd or stale engine_override value fails loudly, before
        touching the adapter/registry/reader at all — not by falling back to
        the default silently."""
        executor = SurveyDefinitionExecutor(registry=MagicMock(), reader=MagicMock())
        try:
            executor.run(entity_type="whatever", slug="whatever", engine_override="airflow")
            assert False, "expected ValueError"
        except ValueError as exc:
            assert "airflow" in str(exc)

    def test_egeria_step_is_never_forced_through_either_override_value(self):
        """(e) The boundary with Part B: an executes_at='egeria' step must
        never be routed to Prefect OR forced to run locally by
        engine_override, in either direction — it stays exactly what it was
        before this feature existed (triggered via other_engine_handlers, or
        counted as not_executed_no_egeria_handler if none is registered)."""
        for override in ("prefect", "resource-explorer"):
            result, local_runner, fake_run_prefect_step = self._run(
                f"override-egeria-{override}", engine_override=override,
                config_enabled=True, config_route_local=True,
                executes_at="egeria",
            )
            fake_run_prefect_step.assert_not_called()
            local_runner.assert_not_called()
            statuses = {s["step"]: s for s in result["steps"]}
            assert statuses["Step::Known"]["status"] == "not_executed_no_egeria_handler"
            assert "Step::Known was not executed" in result["errors"][0]


class TestEngineOverrideAndWholeDefinitionOrchestration:
    """`engine_override` also has to reach `_prefect_orchestration_enabled`
    (the gate for handing the WHOLE definition to Prefect, a separate
    mechanism from the per-step `_use_prefect` predicate tested above) —
    otherwise a run with engine_override='resource-explorer' could still be
    swept into Prefect's whole-definition path if `config.prefect.enabled`
    happened to be True, defeating the override entirely."""

    def test_resource_explorer_override_disables_whole_definition_prefect_orchestration(self):
        from resource_explorer.surveyors.survey_definition_executor import (
            _prefect_orchestration_enabled,
        )

        import resource_explorer.config as config_module

        fake_cfg = MagicMock()
        fake_cfg.prefect.enabled = True
        with patch.object(config_module, "get_config", return_value=fake_cfg):
            assert _prefect_orchestration_enabled(None) is True
            assert _prefect_orchestration_enabled("resource-explorer") is False

    def test_prefect_override_enables_whole_definition_prefect_orchestration_even_if_config_off(self):
        from resource_explorer.surveyors.survey_definition_executor import (
            _prefect_orchestration_enabled,
        )

        import resource_explorer.config as config_module

        fake_cfg = MagicMock()
        fake_cfg.prefect.enabled = False
        with patch.object(config_module, "get_config", return_value=fake_cfg):
            assert _prefect_orchestration_enabled(None) is False
            assert _prefect_orchestration_enabled("prefect") is True


class TestCredentialFallbackToStoredEntity:
    """`run()` falls back to the entity's own stored db_user/db_password when
    the caller supplies neither — found live 2026-09-26 running the
    first-ever database Survey Definition (Slice 12): every RE step failed
    with "Database credentials are required to connect" even though the
    database's ordinary (non-Survey-Definition) survey button works fine,
    because that route already falls back to stored credentials
    (`databases.py`'s `req.db_user or database.db_user`) and this path never
    had that fallback. `getattr(entity, ..., "")` must be a no-op for entity
    types that carry no such attributes (repo, filesystem) — the second test
    below pins that."""

    def _survey_def_and_reader(self):
        survey_def = SurveyDefinition(
            process_guid="proc-1",
            display_name="Fake Survey",
            qualified_name="GovActionProcess::Fake",
            supported_technology_type="Fake Tech",
            steps=[
                SurveyStep(
                    guid="s1", display_name="OneStep", qualified_name="Step::One",
                    executes_at="resource-explorer", re_analysis_step="one_step",
                ),
            ],
        )
        return survey_def, _fake_reader(
            survey_def,
            candidates=[{"guid": "proc-1", "qualified_name": "GovActionProcess::Fake", "display_name": "Fake"}],
        )

    def test_falls_back_to_stored_credentials_when_caller_supplies_none(self):
        runner = MagicMock(return_value={"ok": True})
        entity = MagicMock()
        entity.db_user = "stored_user"
        entity.db_password = "stored_pwd"
        adapter = ResourceTypeAdapter(
            entity_type="fake",
            technology_type="Fake Tech",
            re_analysis_steps={"one_step": runner},
            get_entity=lambda registry, slug: entity,
            publish=MagicMock(return_value="report-guid-1"),
        )
        register_adapter(adapter)

        survey_def, reader = self._survey_def_and_reader()
        executor = SurveyDefinitionExecutor(_fake_registry(), reader=reader)
        executor.run(entity_type="fake", slug="my-fake")

        runner.assert_called_once()
        _, kwargs = runner.call_args
        assert kwargs["db_user"] == "stored_user"
        assert kwargs["db_pwd"] == "stored_pwd"

    def test_caller_supplied_credentials_win_over_stored(self):
        runner = MagicMock(return_value={"ok": True})
        entity = MagicMock()
        entity.db_user = "stored_user"
        entity.db_password = "stored_pwd"
        adapter = ResourceTypeAdapter(
            entity_type="fake",
            technology_type="Fake Tech",
            re_analysis_steps={"one_step": runner},
            get_entity=lambda registry, slug: entity,
            publish=MagicMock(return_value="report-guid-1"),
        )
        register_adapter(adapter)

        survey_def, reader = self._survey_def_and_reader()
        executor = SurveyDefinitionExecutor(_fake_registry(), reader=reader)
        executor.run(entity_type="fake", slug="my-fake", db_user="caller_user", db_pwd="caller_pwd")

        _, kwargs = runner.call_args
        assert kwargs["db_user"] == "caller_user"
        assert kwargs["db_pwd"] == "caller_pwd"

    def test_no_op_for_an_entity_with_no_stored_credential_attributes(self):
        """A repo/filesystem entity carries no db_user/db_password at all —
        the fallback must not invent empty-string kwargs where none were
        passed before this fix existed."""
        runner = MagicMock(return_value={"ok": True})
        entity = object()  # no db_user/db_password attributes at all
        adapter = ResourceTypeAdapter(
            entity_type="fake",
            technology_type="Fake Tech",
            re_analysis_steps={"one_step": runner},
            get_entity=lambda registry, slug: entity,
            publish=MagicMock(return_value="report-guid-1"),
        )
        register_adapter(adapter)

        survey_def, reader = self._survey_def_and_reader()
        executor = SurveyDefinitionExecutor(_fake_registry(), reader=reader)
        executor.run(entity_type="fake", slug="my-fake")

        _, kwargs = runner.call_args
        assert "db_user" not in kwargs
        assert "db_pwd" not in kwargs
