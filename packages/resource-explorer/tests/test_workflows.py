"""The extracted workflows, called directly.

Step 2b moved the analysis-run, scouting-scan, GitHub-discovery and
curate-materialization flows out of `web/routes/` into
`resource_explorer/workflows/`. The route tests still exercise the routes; this
file exercises the workflow functions themselves, because they now have two
other callers (the Typer CLI and the run queue) that no route test covers.

The mocks are the ones the corresponding route tests already use — the point of
the move is that behaviour did not change, so the fixtures should not have had
to either.
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from resource_explorer.registry import Project, ProjectRegistry


@pytest.fixture
def registry(tmp_path):
    r = ProjectRegistry(db_path=str(tmp_path / "wf.db"))
    r.add(Project(
        slug="myproj", display_name="My Project",
        github_url="https://github.com/test/myproj", description="A test repo.",
    ))
    return r


class TestNoFastAPIInWorkflows:
    """The property that makes these callable from a CLI and a worker at all.

    Asserted structurally rather than trusted: an `from fastapi import
    HTTPException` added for one convenient early-return would re-couple the
    core to the web tier, and nothing else in the suite would notice — the
    routes would still pass, and the CLI would still import, because FastAPI is
    installed.
    """

    def test_no_workflow_module_imports_fastapi(self):
        import ast
        from pathlib import Path

        wf = Path(__file__).resolve().parents[1] / "resource_explorer" / "workflows"
        offenders = []
        for path in sorted(wf.glob("*.py")):
            # Parsed, not grepped: the package docstring explains at length why
            # FastAPI must stay out, and a substring check flags that prose as
            # the violation it is describing.
            for node in ast.walk(ast.parse(path.read_text())):
                names = []
                if isinstance(node, ast.Import):
                    names = [a.name for a in node.names]
                elif isinstance(node, ast.ImportFrom):
                    names = [node.module or ""]
                if any(n.split(".")[0] in ("fastapi", "starlette") for n in names):
                    offenders.append(path.name)
        assert offenders == [], f"workflows importing fastapi: {sorted(set(offenders))}"


class TestAnalysisWorkflow:
    def _survey_result(self, annotations=1, errors=None):
        result = MagicMock()
        result.annotations = [{"a": i} for i in range(annotations)]
        result.errors = errors or []
        return result

    def test_the_ingest_branch_never_runs_a_survey(self, registry):
        """`action: "ingest"` re-embeds into pgvector; it is not a
        SurveyOrchestrator step, and treating it as one is how an unknown id
        used to reach the orchestrator."""
        from resource_explorer.workflows.analysis import run_analysis

        with patch("resource_explorer.ingestion.incremental.IncrementalIndexer") as Idx, \
             patch("resource_explorer.query_cache.QueryCache") as Cache, \
             patch("resource_explorer.surveyors.survey_orchestrator.SurveyOrchestrator") as Orch:
            result = run_analysis("myproj", "rag_ingestion", is_ingest=True,
                                  steps=None, registry=registry)
        assert result.status == "ok"
        assert "pgvector" in result.summary
        Idx.return_value.refresh.assert_called_once()
        Cache.return_value.invalidate_project.assert_called_once_with("myproj")
        Orch.assert_not_called()

    def test_an_unassigned_resource_never_attempts_publish(self, registry):
        """`published=None` is "never attempted", not "attempted and produced
        nothing" — the caller renders the two differently."""
        from resource_explorer.workflows.analysis import run_analysis

        with patch("resource_explorer.surveyors.survey_orchestrator.SurveyOrchestrator") as Orch, \
             patch("resource_explorer.surveyors.survey_report.summarise_annotations",
                   return_value=[]), \
             patch("resource_explorer.surveyors.egeria_publisher.EgeriaPublisher") as Pub:
            Orch.return_value.run.return_value = self._survey_result()
            result = run_analysis("myproj", "security_scan", is_ingest=False,
                                  steps=["repo_security"], registry=registry)
        assert result.status == "ok"
        assert result.published is None
        Pub.assert_not_called()

    def test_an_assigned_resource_publishes(self, registry):
        from resource_explorer.workflows.analysis import run_analysis

        registry.has_assigned_egeria_project = lambda *a, **k: True
        with patch("resource_explorer.surveyors.survey_orchestrator.SurveyOrchestrator") as Orch, \
             patch("resource_explorer.surveyors.survey_report.summarise_annotations",
                   return_value=[]), \
             patch("resource_explorer.surveyors.egeria_publisher.EgeriaPublisher") as Pub:
            Orch.return_value.run.return_value = self._survey_result()
            result = run_analysis("myproj", "security_scan", is_ingest=False,
                                  steps=["repo_security"], registry=registry)
        assert result.published is True
        Pub.return_value.publish.assert_called_once()

    def test_a_publish_failure_does_not_fail_the_run(self, registry):
        """The findings are real and stored either way; `published=False` keeps
        the ☁ Publish button visible as the recovery path."""
        from resource_explorer.workflows.analysis import run_analysis

        registry.has_assigned_egeria_project = lambda *a, **k: True
        with patch("resource_explorer.surveyors.survey_orchestrator.SurveyOrchestrator") as Orch, \
             patch("resource_explorer.surveyors.survey_report.summarise_annotations",
                   return_value=[]), \
             patch("resource_explorer.surveyors.egeria_publisher.EgeriaPublisher") as Pub:
            Orch.return_value.run.return_value = self._survey_result()
            Pub.return_value.publish.side_effect = RuntimeError("egeria down")
            result = run_analysis("myproj", "security_scan", is_ingest=False,
                                  steps=["repo_security"], registry=registry)
        assert result.status == "ok"
        assert result.published is False
        assert "auto-publish" in result.summary

    def test_a_missing_project_is_an_error_not_a_crash(self, registry):
        """A queue worker runs this minutes after the enqueue, so "the world has
        not moved" is a weaker assumption than it was when a route checked
        synchronously a moment earlier."""
        from resource_explorer.workflows.analysis import run_analysis

        result = run_analysis("ghost", "security_scan", is_ingest=False,
                              steps=["repo_security"], registry=registry)
        assert result.status == "error"
        assert "not found" in result.error


class TestAnalysisRunTimingsAndPublishMode:
    """`steps_seconds`/`publish_seconds`/`publish_mode` — added so a run's cost
    can be split into "thought" (the survey steps) vs "waited" (the
    synchronous Egeria drain). And `published`'s third state, "queued", for
    `RunsConfig.publish_inline=False` (the default since 2026-10-14). See
    config.py's `RunsConfig.publish_inline` docstring for the 2026-09-13
    measurement (inline drain: 546.61s of a 558.89s run) that motivated both.
    """

    def _survey_result(self, annotations=1, errors=None):
        result = MagicMock()
        result.annotations = [{"a": i} for i in range(annotations)]
        result.errors = errors or []
        return result

    def test_timings_recorded_and_not_attempted_without_a_project(self, registry):
        from resource_explorer.workflows.analysis import run_analysis

        with patch("resource_explorer.surveyors.survey_orchestrator.SurveyOrchestrator") as Orch, \
             patch("resource_explorer.surveyors.survey_report.summarise_annotations",
                   return_value=[]), \
             patch("resource_explorer.surveyors.egeria_publisher.EgeriaPublisher") as Pub:
            Orch.return_value.run.return_value = self._survey_result()
            result = run_analysis("myproj", "security_scan", is_ingest=False,
                                  steps=["repo_security"], registry=registry)
        assert result.steps_seconds is not None
        assert result.steps_seconds >= 0
        assert result.publish_seconds is None
        assert result.publish_mode == "not-attempted"
        Pub.assert_not_called()

    def test_inline_publish_records_mode_and_positive_publish_seconds(self, registry, monkeypatch):
        """`RUNS_PUBLISH_INLINE=true`: drained inline, `published is True`,
        `publish_mode == "inline"` (the default is enqueue-only since 2026-10-14)."""
        from resource_explorer.config import get_config
        from resource_explorer.workflows.analysis import run_analysis

        monkeypatch.setattr(get_config().runs, "publish_inline", True)
        registry.has_assigned_egeria_project = lambda *a, **k: True
        with patch("resource_explorer.surveyors.survey_orchestrator.SurveyOrchestrator") as Orch, \
             patch("resource_explorer.surveyors.survey_report.summarise_annotations",
                   return_value=[]), \
             patch("resource_explorer.surveyors.egeria_publisher.EgeriaPublisher") as Pub:
            Orch.return_value.run.return_value = self._survey_result()
            Pub.return_value.publish_deferred = False  # real EgeriaPublisher's own default
            result = run_analysis("myproj", "security_scan", is_ingest=False,
                                  steps=["repo_security"], registry=registry)
        assert result.published is True
        assert result.publish_mode == "inline"
        assert result.steps_seconds is not None and result.steps_seconds >= 0
        assert result.publish_seconds is not None and result.publish_seconds >= 0

    def test_publish_inline_false_records_published_as_queued(self, registry, monkeypatch):
        """`RunsConfig.publish_inline=False`: EgeriaPublisher enqueues instead
        of draining (see egeria_publisher.py's `_create_annotations`) and sets
        `publish_deferred = True`; `run_analysis` must turn that into the
        THIRD `published` state, "queued" — not True (nothing has landed in
        Egeria yet) and not False (nothing failed either)."""
        from resource_explorer.config import get_config
        from resource_explorer.workflows.analysis import run_analysis

        monkeypatch.setattr(get_config().runs, "publish_inline", False)
        registry.has_assigned_egeria_project = lambda *a, **k: True
        with patch("resource_explorer.surveyors.survey_orchestrator.SurveyOrchestrator") as Orch, \
             patch("resource_explorer.surveyors.survey_report.summarise_annotations",
                   return_value=[]), \
             patch("resource_explorer.surveyors.egeria_publisher.EgeriaPublisher") as Pub:
            Orch.return_value.run.return_value = self._survey_result()
            Pub.return_value.publish_deferred = True
            result = run_analysis("myproj", "security_scan", is_ingest=False,
                                  steps=["repo_security"], registry=registry)
        assert result.published == "queued"
        assert result.published is not True  # a bool True must never collapse into this
        assert result.publish_mode == "enqueued"
        assert "queued for publish" in result.summary

    def test_execute_and_record_analysis_writes_timings_onto_the_activity_detail(self, registry, monkeypatch):
        """The instrumentation only matters if it reaches the row a person (or
        the frontend) actually reads — `update_activity_status`'s `detail`."""
        from resource_explorer.activity_logger import log_analysis_run
        from resource_explorer.workflows.analysis import execute_and_record_analysis

        from resource_explorer.config import get_config

        monkeypatch.setattr(get_config().runs, "publish_inline", True)
        registry.has_assigned_egeria_project = lambda *a, **k: True
        activity_id = log_analysis_run(
            registry, "repo", "myproj", "My Project", "running",
            "Running 'security_scan' on myproj…", "security_scan",
        )
        # execute_and_record_analysis's inner run_analysis() call always passes
        # registry=None (see its source — a deliberate, pre-existing quirk
        # unrelated to this change), so it builds its OWN ProjectRegistry()
        # rather than reusing the one passed in here. Patched at its source so
        # that fresh instance is this test's registry too.
        with patch("resource_explorer.registry.ProjectRegistry", return_value=registry), \
             patch("resource_explorer.surveyors.survey_orchestrator.SurveyOrchestrator") as Orch, \
             patch("resource_explorer.surveyors.survey_report.summarise_annotations",
                   return_value=[]), \
             patch("resource_explorer.surveyors.egeria_publisher.EgeriaPublisher") as Pub:
            Orch.return_value.run.return_value = self._survey_result()
            Pub.return_value.publish_deferred = False
            execute_and_record_analysis(
                "myproj", "security_scan", activity_id, registry=registry,
            )
        entry = registry.get_activity(activity_id)
        detail = json.loads(entry["detail"])
        assert detail["published"] is True
        assert detail["publish_mode"] == "inline"
        assert isinstance(detail["steps_seconds"], (int, float)) and detail["steps_seconds"] >= 0
        assert isinstance(detail["publish_seconds"], (int, float)) and detail["publish_seconds"] >= 0


class TestRunAnalysisPublishChoice:
    """run-in-background plan: the caller chooses per RUN whether to wait for
    the Egeria publish or enqueue it and move on — `publish="wait"` /
    `"background"` override `RunsConfig.publish_inline` for that run only.
    `None` (every pre-existing caller) keeps the config default, unchanged."""

    def _survey_result(self, annotations=1):
        result = MagicMock()
        result.annotations = [{"a": i} for i in range(annotations)]
        result.errors = []
        return result

    def test_background_defers_even_though_config_default_is_inline(self, registry, monkeypatch):
        from resource_explorer.config import get_config
        from resource_explorer.workflows.analysis import run_analysis

        monkeypatch.setattr(get_config().runs, "publish_inline", True)  # the default
        registry.has_assigned_egeria_project = lambda *a, **k: True
        with patch("resource_explorer.surveyors.survey_orchestrator.SurveyOrchestrator") as Orch, \
             patch("resource_explorer.surveyors.survey_report.summarise_annotations",
                   return_value=[]), \
             patch("resource_explorer.surveyors.egeria_publisher.EgeriaPublisher") as Pub:
            Orch.return_value.run.return_value = self._survey_result()
            Pub.return_value.publish_deferred = True
            Pub.return_value.publish_run_id = "Annotation::myproj::t1"
            result = run_analysis("myproj", "security_scan", is_ingest=False,
                                  steps=["repo_security"], registry=registry,
                                  publish="background")
        assert result.published == "queued"
        assert result.publish_mode == "enqueued"
        assert result.publish_run_id == "Annotation::myproj::t1"
        Pub.return_value.publish.assert_called_once_with(
            Orch.return_value.run.return_value, defer_drain=True,
        )

    def test_wait_drains_even_though_config_default_is_background(self, registry, monkeypatch):
        from resource_explorer.config import get_config
        from resource_explorer.workflows.analysis import run_analysis

        monkeypatch.setattr(get_config().runs, "publish_inline", False)
        registry.has_assigned_egeria_project = lambda *a, **k: True
        with patch("resource_explorer.surveyors.survey_orchestrator.SurveyOrchestrator") as Orch, \
             patch("resource_explorer.surveyors.survey_report.summarise_annotations",
                   return_value=[]), \
             patch("resource_explorer.surveyors.egeria_publisher.EgeriaPublisher") as Pub:
            Orch.return_value.run.return_value = self._survey_result()
            Pub.return_value.publish_deferred = False
            result = run_analysis("myproj", "security_scan", is_ingest=False,
                                  steps=["repo_security"], registry=registry, publish="wait")
        assert result.published is True
        assert result.publish_mode == "inline"
        Pub.return_value.publish.assert_called_once_with(
            Orch.return_value.run.return_value, defer_drain=False,
        )

    def test_no_choice_keeps_the_config_default_byte_for_byte(self, registry):
        """Sabotage check for the choice's own default path: with `publish`
        omitted, the config default decides, nothing else — enqueue-only
        since 2026-10-14 (project owner: a run does not wait for its publish)."""
        from resource_explorer.workflows.analysis import run_analysis

        registry.has_assigned_egeria_project = lambda *a, **k: True
        with patch("resource_explorer.surveyors.survey_orchestrator.SurveyOrchestrator") as Orch, \
             patch("resource_explorer.surveyors.survey_report.summarise_annotations",
                   return_value=[]), \
             patch("resource_explorer.surveyors.egeria_publisher.EgeriaPublisher") as Pub:
            Orch.return_value.run.return_value = self._survey_result()
            Pub.return_value.publish_deferred = True  # config default is publish_inline=False
            Pub.return_value.publish_run_id = "Annotation::myproj::t"
            result = run_analysis("myproj", "security_scan", is_ingest=False,
                                  steps=["repo_security"], registry=registry)
        assert result.published == "queued"
        assert result.publish_mode == "enqueued"
        Pub.return_value.publish.assert_called_once_with(
            Orch.return_value.run.return_value, defer_drain=True,
        )


class TestCreateAnnotationsEnqueueVsDrain:
    """`EgeriaPublisher._create_annotations` — the narrowest place
    `RunsConfig.publish_inline` can gate, since that's the one call already
    doing enqueue-then-drain (see its docstring). Exercised directly rather
    than through `run_analysis` (which mocks EgeriaPublisher entirely
    elsewhere in this file): that would just be re-testing the same mock in a
    longer path, not the decision this class actually makes.
    """

    def _publisher(self, registry):
        from resource_explorer.surveyors.egeria_publisher import EgeriaPublisher

        return EgeriaPublisher(platform_url="https://fake", registry=registry)

    def _result(self):
        from resource_explorer.surveyors.survey_report import (
            ResourceMeasureAnnotation, SurveyResult,
        )

        result = SurveyResult(
            resource_slug="myproj", project_display_name="My Project",
            github_url="https://github.com/test/myproj",
        )
        result.add(ResourceMeasureAnnotation(
            summary="1 file", analysis_step="a", resource_properties={"n": 1},
        ))
        return result

    def test_publish_inline_false_enqueues_and_does_not_drain(self, registry, monkeypatch):
        from resource_explorer.config import get_config

        monkeypatch.setattr(get_config().runs, "publish_inline", False)

        def _fail_if_called(*a, **k):
            pytest.fail("drain_outbox must not be called when publish_inline is False")

        monkeypatch.setattr("resource_explorer.egeria_outbox.drain_outbox", _fail_if_called)

        publisher = self._publisher(registry)
        counts = publisher._create_annotations(self._result(), "report-guid-1")

        assert publisher.publish_deferred is True
        assert counts["annotations_queued"] == 1
        assert counts["annotations_produced"] == 1
        assert counts["links_created"] == 0
        assert counts["links_failed"] == 0

    def test_publish_inline_true_still_drains(self, registry, monkeypatch):
        """`RUNS_PUBLISH_INLINE=true`: sabotaging this (skipping the flag check)
        would leave `drain_called` at 0 here just as it would falsely pass the
        False-case test above — this is the other half of that guard. The inline
        path drains the annotations, then the run's read-back; a drain that
        landed nothing (this spy) leaves the run queued, not published."""
        from resource_explorer.config import get_config

        monkeypatch.setattr(get_config().runs, "publish_inline", True)

        drained_runs = []

        def _spy(*a, **k):
            drained_runs.append(k.get("run_id"))
            return {"claimed": 1, "done": 1, "failed": 0, "dead": 0}

        monkeypatch.setattr("resource_explorer.egeria_outbox.drain_outbox", _spy)

        publisher = self._publisher(registry)
        result = self._result()
        counts = publisher._create_annotations(result, "report-guid-1")

        prefix = f"Annotation::myproj::{result.surveyed_at.isoformat()}"
        assert drained_runs == [prefix, f"{prefix}::read_back"]
        assert publisher.publish_read_back is False
        assert publisher.publish_deferred is True, "not read back is queued, whichever branch ran"
        assert counts["annotations_queued"] == 1

    def test_defer_drain_override_wins_over_the_config_default(self, registry, monkeypatch):
        """`publish(defer_drain=True)` must defer even when
        RunsConfig.publish_inline says the opposite — the run-in-background
        per-call choice, not just the deployment-wide flag."""
        from resource_explorer.config import get_config

        monkeypatch.setattr(get_config().runs, "publish_inline", True)  # opposite of the ask

        def _fail_if_called(*a, **k):
            pytest.fail("drain_outbox must not be called when defer_drain=True")

        monkeypatch.setattr("resource_explorer.egeria_outbox.drain_outbox", _fail_if_called)

        publisher = self._publisher(registry)
        publisher._defer_drain_override = True
        counts = publisher._create_annotations(self._result(), "report-guid-1")

        assert publisher.publish_deferred is True
        assert counts["annotations_queued"] == 1

    def test_deferred_mode_enqueues_the_evidence_link_by_row_id(self, registry, monkeypatch):
        """The link cannot carry a real GUID yet (the annotation was never
        drained) — it must be enqueued keyed by the two annotations' own
        outbox row ids instead, and reported as `links_queued`."""
        from resource_explorer.config import get_config
        from resource_explorer.surveyors.survey_report import (
            ResourceMeasureAnnotation, SchemaAnalysisAnnotation, SurveyResult,
        )

        monkeypatch.setattr(get_config().runs, "publish_inline", False)

        result = SurveyResult(
            resource_slug="myproj", project_display_name="My Project",
            github_url="https://github.com/test/myproj",
        )
        result.add(ResourceMeasureAnnotation(summary="agg", analysis_step="a"))
        result.add(SchemaAnalysisAnnotation(summary="ev", analysis_step="a", evidence_of=0))

        publisher = self._publisher(registry)
        counts = publisher._create_annotations(result, "report-guid-2")

        assert publisher.publish_deferred is True
        assert counts["links_queued"] == 1
        assert counts["links_skipped"] == 0

        # The link row is real, keyed by row id — not yet resolvable, since
        # its referent annotations are still 'pending', not 'done'.
        prefix = f"Annotation::myproj::{result.surveyed_at.isoformat()}"
        claimed = registry.claim_due_outbox_elements(run_id=f"{prefix}::links")
        assert len(claimed) == 1
        assert claimed[0]["element_kind"] == "annotation_link"
        payload = json.loads(claimed[0]["payload_json"])
        assert "summary_row_id" in payload and "evidence_row_id" in payload


class TestPublishBadgeBookkeepingWaitsForTheFlip:
    """Review fix on 84f4851: `EgeriaPublisher.publish()` used to call
    `record_published_annotation_types`/`record_published_analyses`
    regardless of `publish_deferred`, so a background run's ☁ Published
    badge flipped before a single annotation had reached Egeria. Those two
    calls must wait for `registry.complete_publish_run_if_done()` — the
    point the run's outbox rows are verifiably done — same as inline mode
    would have written them, just later.

    `EgeriaPublisher.publish()` end-to-end needs a live-ish asset/report/
    governance path, none of which this fix touches — stubbed out so
    `_create_annotations` (the part that actually changed) runs for real
    against a real registry.
    """

    def _publisher(self, registry, monkeypatch):
        from resource_explorer.surveyors.egeria_publisher import EgeriaPublisher

        monkeypatch.setattr(EgeriaPublisher, "_connect", lambda self: None)
        monkeypatch.setattr(EgeriaPublisher, "_find_or_create_asset",
                            lambda self, result: "asset-guid")
        monkeypatch.setattr(EgeriaPublisher, "_publish_homepage_reference",
                            lambda self, result, asset_guid: "")
        monkeypatch.setattr(EgeriaPublisher, "_create_survey_report",
                            lambda self, result, asset_guid: "report-guid")
        monkeypatch.setattr(EgeriaPublisher, "_stamp_governance",
                            lambda self, *guids, produced=True: {})
        return EgeriaPublisher(platform_url="https://fake", registry=registry)

    def _result(self):
        from resource_explorer.surveyors.survey_report import (
            ResourceMeasureAnnotation, SurveyResult,
        )

        result = SurveyResult(
            resource_slug="myproj", project_display_name="My Project",
            github_url="https://github.com/test/myproj",
        )
        result.add(ResourceMeasureAnnotation(
            summary="1 file", analysis_step="a", resource_properties={"n": 1},
        ))
        result.steps_run = ["repo_security"]
        return result

    def test_background_publish_leaves_the_badge_tables_untouched(self, registry, monkeypatch):
        publisher = self._publisher(registry, monkeypatch)
        publisher.publish(self._result(), defer_drain=True)

        assert publisher.publish_deferred is True
        assert registry.get_last_published_annotation_types("myproj") == {}
        assert registry.get_last_published_analyses("myproj") == {}
        # What WOULD have been written — stashed, not applied yet.
        assert publisher.pending_published_record is not None
        assert publisher.pending_published_record["slug"] == "myproj"
        assert publisher.pending_published_record["report_guid"] == "report-guid"
        assert publisher.pending_published_record["annotation_types"]

    def test_the_flip_populates_the_badge_tables_exactly_as_inline_would_have(
        self, registry, monkeypatch,
    ):
        from resource_explorer.activity_logger import log_analysis_run

        publisher = self._publisher(registry, monkeypatch)
        publisher.publish(self._result(), defer_drain=True)
        run_id = publisher.publish_run_id

        # The activity row execute_and_record_analysis would have written,
        # tagged with this run's publish_run_id — the real wiring this test
        # exercises, not just the registry calls in isolation.
        entry_id = log_analysis_run(
            registry, "repo", "myproj", "My Project", "ok",
            "1 annotation(s); queued", "security_scan", published="queued",
        )
        registry.update_activity_status(
            entry_id, "ok",
            detail=json.dumps({
                "published": "queued",
                "pending_published_record": publisher.pending_published_record,
            }),
            publish_run_id=run_id,
        )

        # Drain for real — this is registry bookkeeping only, no live Egeria
        # call needed to mark a row done.
        for row in registry.claim_due_outbox_elements(run_id=run_id):
            registry.mark_outbox_done(row["id"], f"guid-{row['id']}")

        # Every write landed, but the run is not read back yet: no flip, no badge.
        assert registry.complete_publish_run_if_done(run_id) is False
        assert registry.get_last_published_annotation_types("myproj") == {}
        for row in registry.claim_due_outbox_elements(run_id=f"{run_id}::read_back"):
            registry.mark_outbox_done(row["id"], "report-guid", note="read back")

        assert registry.complete_publish_run_if_done(run_id) is True
        assert registry.get_last_published_annotation_types("myproj"), (
            "the badge table must be populated once the run's rows landed"
        )
        assert registry.get_last_published_analyses("myproj")
        detail = json.loads(registry.get_activity(entry_id)["detail"])
        assert detail["published"] is True
        assert "pending_published_record" not in detail

    def test_inline_publish_still_writes_the_badge_tables_immediately(self, registry, monkeypatch):
        """Sabotage check for the guard itself: an inline publish
        (defer_drain=False) whose writes land and read back before it returns
        keeps writing at publish() time. The drain is faked at the registry
        (every claimed row of the scoped run marked done), not at Egeria."""
        def _drain(reg, *a, run_id=None, **k):
            for row in reg.claim_due_outbox_elements(run_id=run_id):
                reg.mark_outbox_done(row["id"], f"guid-{row['id']}")
            return {"claimed": 1, "done": 1, "failed": 0, "dead": 0}

        monkeypatch.setattr("resource_explorer.egeria_outbox.drain_outbox", _drain)
        publisher = self._publisher(registry, monkeypatch)
        publisher.publish(self._result(), defer_drain=False)

        assert publisher.publish_read_back is True
        assert publisher.publish_deferred is False
        assert registry.get_last_published_annotation_types("myproj")
        assert registry.get_last_published_analyses("myproj")


class TestStageBatchWorkflow:
    def test_a_stage_batch_with_some_errors_and_some_output_is_not_a_failure(self, registry):
        from resource_explorer.workflows.analysis import run_stage_batch

        with patch("resource_explorer.surveyors.repo_survey_definition_adapter._run_batch",
                   return_value={"annotations": [{"a": 1}], "errors": ["step x blew up"]}), \
             patch("resource_explorer.surveyors.survey_report.summarise_annotations",
                   return_value=[]):
            result = run_stage_batch("myproj", "scouting", ["repo_health"], registry=registry)
        assert result.status == "ok", "11 of 12 steps' findings is not a failed batch"
        assert result.errors == ["step x blew up"]
        assert "1 error(s)" in result.summary

    def test_a_stage_batch_that_produced_nothing_is_a_failure(self, registry):
        from resource_explorer.workflows.analysis import run_stage_batch

        with patch("resource_explorer.surveyors.repo_survey_definition_adapter._run_batch",
                   return_value={"annotations": [], "errors": ["everything blew up"]}), \
             patch("resource_explorer.surveyors.survey_report.summarise_annotations",
                   return_value=[]):
            result = run_stage_batch("myproj", "scouting", ["repo_health"], registry=registry)
        assert result.status == "error"


class TestScoutingWorkflow:
    def test_an_egeria_definition_loss_falls_back_to_running_the_steps(self, registry):
        """Egeria-side Survey Definitions do not survive an Egeria database
        reset. Without the fallback that silently breaks Scouting Scan
        entirely — and the message has to SAY it ran locally, or a user
        reasonably concludes the Egeria route is fine."""
        from resource_explorer.surveyors.survey_definition_executor import (
            SurveyDefinitionExecutorError,
        )
        from resource_explorer.workflows.scouting import run_scouting_scan

        survey_result = MagicMock()
        survey_result.errors = []
        with patch("resource_explorer.surveyors.survey_definition_executor.run_survey_definition",
                   side_effect=SurveyDefinitionExecutorError("no such definition")), \
             patch("resource_explorer.surveyors.survey_orchestrator.SurveyOrchestrator") as Orch:
            Orch.return_value.run.return_value = survey_result
            result = run_scouting_scan("myproj", registry)
        assert result.status == "ok"
        assert "ran locally" in result.message
        assert Orch.return_value.run.call_args.kwargs["fast"] is True

    def test_the_fallback_failing_too_reports_both_causes(self, registry):
        from resource_explorer.surveyors.survey_definition_executor import (
            SurveyDefinitionExecutorError,
        )
        from resource_explorer.workflows.scouting import run_scouting_scan

        with patch("resource_explorer.surveyors.survey_definition_executor.run_survey_definition",
                   side_effect=SurveyDefinitionExecutorError("no such definition")), \
             patch("resource_explorer.surveyors.survey_orchestrator.SurveyOrchestrator") as Orch:
            Orch.return_value.run.side_effect = RuntimeError("github down")
            result = run_scouting_scan("myproj", registry)
        assert result.status == "error"
        assert "no such definition" in result.error
        assert "github down" in result.error

    def test_a_question_with_no_analyses_is_none_not_false(self):
        """"Nothing to check" and "checked and found nothing" are different
        answers, and the checklist renders them differently."""
        from resource_explorer.workflows.scouting import question_has_data

        assert question_has_data(MagicMock(), "myproj", []) is None

    def test_a_shaped_but_empty_payload_does_not_count_as_data(self):
        from resource_explorer.workflows.scouting import results_have_data

        assert results_have_data({"findings": [], "gap_count": 0}) is False
        assert results_have_data({"findings": [{"x": 1}], "gap_count": 0}) is True


class TestDiscoveryWorkflow:
    def test_a_query_with_no_filters_is_refused(self):
        """Before the archived:false/fork:false defaults are appended — those
        are not user filters, so an otherwise-empty request would produce a
        valid-looking query matching everything."""
        from resource_explorer.workflows.discovery import (
            DiscoveryError,
            RepoSearchCriteria,
            build_query,
        )

        with pytest.raises(DiscoveryError, match="At least one search filter"):
            build_query(RepoSearchCriteria())

    def test_defaults_exclude_forks_and_archived(self):
        from resource_explorer.workflows.discovery import RepoSearchCriteria, build_query

        q = build_query(RepoSearchCriteria(keyword="egeria"))
        assert "archived:false" in q and "fork:false" in q

    def test_filters_become_qualifiers(self):
        from resource_explorer.workflows.discovery import RepoSearchCriteria, build_query

        q = build_query(RepoSearchCriteria(
            keyword="metadata", min_stars=50, language="python", org="odpi",
            topic="governance", license="apache-2.0", pushed_after="2026-01-01",
        ))
        for expected in ("metadata", "stars:>=50", "language:python", "org:odpi",
                         "topic:governance", "license:apache-2.0", "pushed:>2026-01-01"):
            assert expected in q

    def test_a_github_auth_failure_is_reported_as_upstream(self):
        from github import GithubException

        from resource_explorer.workflows.discovery import (
            DiscoveryError,
            RepoSearchCriteria,
            run_search_query,
        )

        registry = MagicMock()
        registry.get_setting.return_value = None
        with patch("resource_explorer.github.client.GitHubClient") as Client:
            Client.return_value.search_repos.side_effect = GithubException(403, {}, {})
            with pytest.raises(DiscoveryError) as exc:
                run_search_query(RepoSearchCriteria(keyword="x"), registry)
        assert exc.value.kind == "upstream"
        assert "GITHUB_TOKEN" in str(exc.value)

    def test_an_invalid_query_is_the_callers_problem_not_githubs(self):
        from github import GithubException

        from resource_explorer.workflows.discovery import (
            DiscoveryError,
            RepoSearchCriteria,
            run_search_query,
        )

        registry = MagicMock()
        registry.get_setting.return_value = None
        with patch("resource_explorer.github.client.GitHubClient") as Client:
            Client.return_value.search_repos.side_effect = GithubException(422, {}, {})
            with pytest.raises(DiscoveryError) as exc:
                run_search_query(RepoSearchCriteria(keyword="x"), registry)
        assert exc.value.kind == "bad_request"

    def test_expand_org_reports_truncation_rather_than_implying_completeness(self):
        """"I gave you 3 lines and got 214 repos" needs an explanation attached,
        and a capped expansion must never read as the whole account."""
        from resource_explorer.workflows.discovery import expand_org

        with patch("resource_explorer.github.client.GitHubClient") as Client:
            Client.return_value.search_repos.return_value = [
                {"html_url": f"https://github.com/o/r{i}"} for i in range(5)
            ]
            expansion = expand_org("o", limit=5)
        assert expansion.truncated is True
        assert len(expansion.urls) == 5

    def test_expand_org_reports_a_failure_instead_of_raising(self):
        from resource_explorer.workflows.discovery import expand_org

        with patch("resource_explorer.github.client.GitHubClient") as Client:
            Client.return_value.search_repos.side_effect = RuntimeError("network")
            expansion = expand_org("o")
        assert expansion.urls == []
        assert "network" in expansion.error

    def test_an_unknown_disposition_is_refused(self):
        from resource_explorer.workflows.discovery import DiscoveryError, set_disposition

        with pytest.raises(DiscoveryError, match="Invalid disposition"):
            set_disposition(MagicMock(), "https://github.com/a/b", "maybe")

    def test_a_valid_disposition_is_recorded_with_the_slug_when_known(self, registry):
        from resource_explorer.workflows.discovery import set_disposition

        result = set_disposition(registry, "https://github.com/test/myproj", "tracking")
        assert result["disposition"] == "tracking"
        assert registry.get_disposition("https://github.com/test/myproj")["disposition"] == "tracking"


class TestCurateWorkflow:
    def test_a_non_accepted_verdict_materializes_nothing(self, registry):
        from resource_explorer.workflows.curate import materialize_component_if_accepted

        assert materialize_component_if_accepted(
            registry, "repo", "myproj", "src/api", "rejected") is None

    def test_a_non_repo_entity_materializes_nothing(self, registry):
        """The materializer is wired for repos only — the one kind
        architecture_recovery runs against. Reusing it past what it was built
        for would be a silent wrong answer, not a missing feature."""
        from resource_explorer.workflows.curate import materialize_component_if_accepted

        assert materialize_component_if_accepted(
            registry, "database", "mydb", "public", "accepted") is None

    def test_a_missing_finding_is_an_error_not_a_silent_skip(self, registry):
        """The component's finding row can be gone — withdrawn since the review
        surface loaded. Saying so beats returning None, which the caller reads
        as "nothing to do"."""
        from resource_explorer.workflows.curate import materialize_component_if_accepted

        result = materialize_component_if_accepted(
            registry, "repo", "myproj", "src/api", "accepted")
        assert result["status"] == "error"
        assert "no component finding" in result["error"]

    def test_a_missing_candidate_blueprint_is_an_error(self, registry):
        from resource_explorer.workflows.curate import materialize_blueprint_if_accepted

        result = materialize_blueprint_if_accepted(
            registry, "repo", "myproj", "runtime", "Ingest", "accepted")
        assert result["status"] == "error"
        assert "no candidate_blueprint" in result["error"]
