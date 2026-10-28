"""Engine-note persistence (2026-09-28,
ENGINE-NOTE-PERSISTENCE-IMPLEMENTED.md).

The bug: `launchSurvey()` (app.js) appended an `engine_note` div to the
transient `#survey-note` node describing which engine actually ran a
Survey Definition ("running via Prefect (flow-run …)" on success, or "ran
locally: Prefect dispatch failed — <reason>" on fallback — the only signal
that Prefect silently failed). Immediately after, it called
`loadSurveyPane()`, which re-renders the whole pane including a fresh, empty
`#survey-note` — wiping the note out right after it was written. Confirmed
live 2026-09-28: dispatch itself worked correctly (flow-run completed,
step_runs recorded), but the UI only ever showed "✓ ran just now" with no
engine line.

The fix does NOT try to carry the transient note across the reload. Instead
the engine info is persisted as part of the run's recorded detail —
`SurveyDefinitionExecutor.run()` already writes `engine_note` into the same
`detail` JSON `log_survey()` records — and read back by
`registry.get_survey_definition_last_activity()` (same place `last_run_at`/
`last_run_status`/`last_run_errors` are already reconstructed from the
activity log), threaded through `survey_definitions.py`'s candidates route
as `last_run_engine_note`, and rendered on the DEFINITION'S ROW itself
(`surveyRowHtml` -> `engineNoteHtml`) rather than a page-lifetime DOM node.
That means it survives any re-render of the pane, and shows up for
past/historical runs too, not only the one just launched.

No jsdom/DOM harness exists in this codebase (confirmed with the project's
design session, 2026-09-28) — every existing /next test is source-level
(source-text extraction + assertions), the same pattern
`test_next_survey_pane_refresh.py` and `test_next_db_server_discovery.py`
established. This suite follows it, plus registry/route-level tests for the
backend half. Live-page verification (reload + tab-switch, both the success
and fallback text) was done separately on a scratch port — see the
IMPLEMENTED doc for screenshots and what was observed.
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from resource_explorer.activity_logger import log_survey
from resource_explorer.registry import DatabaseEntity, Project, ProjectRegistry

NEXT = Path(__file__).resolve().parents[1] / "resource_explorer" / "web" / "static" / "next"


def _app():
    return (NEXT / "app.js").read_text(encoding="utf-8")


def _fn(name):
    src = _app()
    start = src.index(f"function {name}(")
    end = src.index("\n}\n", start)
    return src[start:end]


# ── Frontend: the row renders from data, not from the transient note ───────

class TestEngineNoteRendersOnTheRow:
    def test_survey_row_html_calls_engine_note_html(self):
        row_fn = _fn("surveyRowHtml")
        assert "engineNoteHtml(c)" in row_fn

    def test_engine_note_html_is_sourced_from_persisted_run_detail(self):
        """Sourced from `last_run_engine_note` on the candidate `c` -- the
        same object `loadSurveyPane()` re-fetches from the API on every
        render -- not from a DOM node id like `#survey-note`."""
        fn = _fn("engineNoteHtml")
        assert "c.last_run_engine_note" in fn
        assert "survey-note" not in fn

    def test_engine_note_html_distinguishes_success_from_fallback(self):
        fn = _fn("engineNoteHtml")
        assert "ran via Prefect" in fn
        assert "ran locally: Prefect dispatch failed" in fn


class TestLaunchSurveyNoLongerWritesTheDoomedTransientNote:
    """Regression guard: the bug was launchSurvey() writing the engine note
    into `#survey-note` and then immediately wiping it with
    `loadSurveyPane()`. That specific write must be gone -- the row-level
    render is what carries the information now."""

    def test_launch_survey_does_not_append_engine_note_to_the_note_div(self):
        """The whole-definition engine choice ("running via Prefect …" /
        the unreachable/dispatch-failed fallback) must not be read out of
        `finishedEntry.detail.engine_note` and appended to `#survey-note`
        here any more -- that specific write-then-immediately-wipe was the
        bug. (The separate PER-STEP fallback detail block below it is a
        different, unrelated signal and is untouched by this fix.)"""
        fn = _fn("launchSurvey")
        assert "d.engine_note" not in fn
        assert "text-state-ok'}\">${esc(engineNote)}" not in fn

    def test_still_reloads_the_pane_so_the_row_reflects_the_new_run(self):
        """Unchanged: the pane reload is still what makes the JUST-launched
        run's engine line show up on the row (the reload re-fetches
        candidates, which now carry last_run_engine_note)."""
        fn = _fn("launchSurvey")
        assert "await loadSurveyPane();" in fn


# ── Backend: registry reads engine_note back off the activity log ──────────

@pytest.fixture
def db(tmp_path):
    return ProjectRegistry(db_path=str(tmp_path / "test.db"))


class TestRegistryCarriesEngineNote:
    def _log_survey(self, db, ref, engine_note):
        log_survey(
            db, entity_type="repo", entity_slug="s", entity_name="s",
            entity_location="", intent="discovery", status="ok",
            summary="ran",
            detail=json.dumps({"survey_definition_ref": ref, "engine_note": engine_note}),
        )

    def test_success_engine_note_is_carried_through(self, db):
        self._log_survey(db, "RefA", "running via Prefect (flow-run abc123ef-0000)")
        activity = db.get_survey_definition_last_activity("repo", "s")
        assert activity["RefA"]["last_run_engine_note"] == (
            "running via Prefect (flow-run abc123ef-0000)"
        )

    def test_fallback_engine_note_is_carried_through(self, db):
        self._log_survey(db, "RefB", "Prefect dispatch failed: ran locally")
        activity = db.get_survey_definition_last_activity("repo", "s")
        assert activity["RefB"]["last_run_engine_note"] == "Prefect dispatch failed: ran locally"

    def test_absent_engine_note_is_an_empty_string_not_missing(self, db):
        """A run recorded before this field existed, or one whose detail
        genuinely carries none, must not make the key absent -- the frontend
        treats absent/empty the same way (no line), but a KeyError here would
        break every caller that does `.get("last_run_engine_note", "")`
        defensively for no reason if the key were sometimes just not there."""
        log_survey(
            db, entity_type="repo", entity_slug="s", entity_name="s",
            entity_location="", intent="discovery", status="ok",
            summary="ran", detail=json.dumps({"survey_definition_ref": "RefC"}),
        )
        activity = db.get_survey_definition_last_activity("repo", "s")
        assert activity["RefC"]["last_run_engine_note"] == ""


# ── Backend: the candidates route exposes it per-candidate ─────────────────

@pytest.fixture
def registry(tmp_path):
    r = ProjectRegistry(db_path=str(tmp_path / "test.db"))
    r.add(Project(slug="myproj", display_name="My Project", github_url="https://github.com/test/myproj"))
    r.register_database(DatabaseEntity(
        slug="mydb", display_name="My DB", db_type="postgresql",
        host="localhost", port=5432, database_name="mydb",
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


class TestCandidatesRouteExposesEngineNote:
    def test_last_run_engine_note_on_a_candidate_that_ran(self, registry, client):
        log_survey(
            registry, entity_type="database", entity_slug="mydb", entity_name="mydb",
            entity_location="", intent="discovery", status="ok", summary="ran",
            detail=json.dumps({
                "survey_definition_ref": "GovActionProcess::Test",
                "engine_note": "running via Prefect (flow-run deadbeef-1111)",
            }),
        )
        step = _fake_step(
            "GovActionProcessStep::Test::SchemaAndStats", "resource-explorer",
            re_analysis_step="postgres_schema_and_stats", description="",
        )
        survey_def = _fake_survey_def(steps=[step])

        with patch(
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
        candidate = resp.json()["candidates"][0]
        assert candidate["last_run_engine_note"] == "running via Prefect (flow-run deadbeef-1111)"

    def test_last_run_engine_note_is_empty_string_when_never_run(self, registry, client):
        step = _fake_step(
            "GovActionProcessStep::Test::SchemaAndStats", "resource-explorer",
            re_analysis_step="postgres_schema_and_stats", description="",
        )
        survey_def = _fake_survey_def(steps=[step])

        with patch(
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
        candidate = resp.json()["candidates"][0]
        assert candidate["last_run_engine_note"] == ""
