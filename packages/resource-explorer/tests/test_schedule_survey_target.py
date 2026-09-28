"""A schedule can now target a Survey Definition, not only an analysis."""
from unittest.mock import patch

import pytest

from resource_explorer.registry import TARGET_ANALYSIS, TARGET_SURVEY, Project


def test_a_survey_can_be_scheduled(pg_registry):
    """The whole point: until 2026-08-28 a schedule could only name an
    analysis, so a Survey Definition could be authored, listed and run by hand
    but never put on a cadence."""
    reg = pg_registry
    reg.add(Project(slug="s", display_name="s", github_url="https://github.com/x/s"))
    reg.save_schedule("repo", "s", "GovActionProcess::RepoScoutingSurvey",
                      "daily", target_kind=TARGET_SURVEY)

    rows = reg.get_schedules("repo", "s")
    assert len(rows) == 1
    assert rows[0]["target_kind"] == TARGET_SURVEY
    assert rows[0]["analysis_id"] == "GovActionProcess::RepoScoutingSurvey"


def test_an_analysis_schedule_still_defaults_to_the_analysis_target(pg_registry):
    """Rows written before target_kind existed, and every caller that has not
    been updated, must keep meaning what they meant."""
    reg = pg_registry
    reg.add(Project(slug="a", display_name="a", github_url="https://github.com/x/a"))
    reg.save_schedule("repo", "a", "foss_scorecard", "weekly")
    assert reg.get_schedules("repo", "a")[0]["target_kind"] == TARGET_ANALYSIS


def test_the_two_namespaces_cannot_collide_on_the_primary_key(pg_registry):
    """Why the key did not need rebuilding: a survey reference always contains
    '::' and no analysis id does, so both can share the ref column."""
    reg = pg_registry
    reg.add(Project(slug="c", display_name="c", github_url="https://github.com/x/c"))
    reg.save_schedule("repo", "c", "foss_scorecard", "daily")
    reg.save_schedule("repo", "c", "GovActionProcess::RepoFullSurvey", "weekly",
                      target_kind=TARGET_SURVEY)
    kinds = {r["analysis_id"]: r["target_kind"] for r in reg.get_schedules("repo", "c")}
    assert kinds == {"foss_scorecard": TARGET_ANALYSIS,
                     "GovActionProcess::RepoFullSurvey": TARGET_SURVEY}


def test_an_unknown_target_kind_is_refused(pg_registry):
    reg = pg_registry
    reg.add(Project(slug="u", display_name="u", github_url="https://github.com/x/u"))
    with pytest.raises(ValueError) as exc:
        reg.save_schedule("repo", "u", "x", "daily", target_kind="whatever")
    assert "target_kind" in str(exc.value)


def test_a_survey_schedule_dispatches_to_the_survey_executor():
    """Analysis schedules keep their old path; survey schedules take the new
    one. Routing on target_kind rather than on the shape of the reference,
    because guessing from the string is how the two would drift."""
    from resource_explorer import scheduler

    with patch.object(scheduler, "run_survey_definition", create=True), \
         patch("resource_explorer.surveyors.survey_definition_executor.run_survey_definition",
               return_value={"errors": [], "run_recorded": True}) as run, \
         patch.object(scheduler, "_run_repo_survey") as legacy:
        reg = type("R", (), {"get": lambda self, s: type("P", (), {
            "display_name": s, "github_url": ""})()})()
        scheduler._execute("repo", "demo", "GovActionProcess::X", reg,
                           target_kind="survey")
    run.assert_called_once()
    legacy.assert_not_called()


def test_a_survey_that_ran_but_was_not_recorded_reports_an_error():
    """It ran; the schedules view is where someone looks to find out whether it
    did, so a bookkeeping miss must not read as a clean run."""
    from resource_explorer import scheduler

    with patch("resource_explorer.surveyors.survey_definition_executor.run_survey_definition",
               return_value={"errors": [], "run_recorded": False}):
        reg = type("R", (), {"get": lambda self, s: type("P", (), {
            "display_name": s, "github_url": ""})()})()
        _, _, errors = scheduler._execute("repo", "demo", "GovActionProcess::X",
                                          reg, target_kind="survey")
    assert errors and "could not be recorded" in errors[0]


# ── The route layer, which is what actually made surveys schedulable ────────
class TestSchedulesRouteAcceptsASurveyTarget:
    """The registry and scheduler understood `target_kind` from the start of
    this work; the HTTP route did not pass it. So a survey could be authored,
    listed and run by hand, and still never be put on a cadence — the feature
    was complete everywhere except the one layer a user can reach.
    """

    def _client(self):
        from fastapi.testclient import TestClient
        from resource_explorer.web.app import app
        return TestClient(app)

    def test_a_survey_target_round_trips(self, monkeypatch):
        saved = {}

        def fake_save(**kwargs):
            saved.update(kwargs)

        from resource_explorer.web.routes import schedules as mod
        monkeypatch.setattr(mod.ProjectRegistry, "save_schedule",
                            lambda self, **kw: fake_save(**kw))
        # The repo has to exist now — the route checks before saving.
        monkeypatch.setattr(mod.ProjectRegistry, "get", lambda self, slug: object())
        resp = self._client().post(
            "/api/schedules/repo/myproj",
            json={"analysis_id": "GovActionProcess::RepoAssessmentSurvey",
                  "schedule": "weekly", "target_kind": "survey"},
        )
        assert resp.status_code == 200, resp.text
        assert saved["target_kind"] == "survey"
        assert saved["analysis_id"] == "GovActionProcess::RepoAssessmentSurvey"
        assert resp.json()["target_kind"] == "survey"

    def test_the_default_is_still_analysis(self, monkeypatch):
        """Omitting target_kind must behave exactly as before this existed."""
        saved = {}
        from resource_explorer.web.routes import schedules as mod
        monkeypatch.setattr(mod.ProjectRegistry, "save_schedule",
                            lambda self, **kw: saved.update(kw))
        monkeypatch.setattr(mod.ProjectRegistry, "get", lambda self, slug: object())
        resp = self._client().post(
            "/api/schedules/repo/myproj",
            json={"analysis_id": "repository_health", "schedule": "daily"},
        )
        assert resp.status_code == 200, resp.text
        assert saved["target_kind"] == "analysis"

    def test_an_unknown_target_kind_is_refused(self):
        resp = self._client().post(
            "/api/schedules/repo/myproj",
            json={"analysis_id": "x", "schedule": "daily", "target_kind": "nonsense"},
        )
        assert resp.status_code == 422
        assert "target_kind" in resp.text

    def test_a_survey_reports_no_cost_tier_rather_than_a_wrong_one(self):
        """Cost tiers live on individual steps. A survey is a graph of them, so
        it has no single tier — and inventing one would drive the
        "more frequent than recommended" advice off a number nobody computed."""
        from resource_explorer.web.routes.schedules import _cost_hint
        assert _cost_hint("repo", "GovActionProcess::RepoFullSurvey") == {}
        assert _cost_hint("repo", "repository_health") != {}


class TestDefinitionsListRoute:
    def test_it_lists_definitions_without_calling_egeria(self):
        """Read from the authored documents, so the scheduling surface keeps
        working when Egeria is unreachable."""
        from resource_explorer.web.routes.survey_definitions import list_definitions
        defs = list_definitions()
        assert defs, "no Survey Definitions documented"
        names = {d["name"] for d in defs}
        assert "RepoFullSurvey" in names

    def test_every_qualified_name_carries_the_prefix_that_actually_resolves(self):
        """find_process_guid_by_name returns None for a bare definition name
        and the real GUID for the GovActionProcess::-prefixed one (verified
        live 2026-08-28). Storing the bare name in a schedule would produce a
        schedule that looks fine and fails every time it fires."""
        from resource_explorer.web.routes.survey_definitions import list_definitions
        for d in list_definitions():
            assert d["qualified_name"] == f"GovActionProcess::{d['name']}"
            assert d["step_count"] > 0

    def test_resource_type_is_read_not_hardcoded(self):
        """Was `"resource_type": "repo"` unconditionally on every entry,
        regardless of what the document actually was — harmless while every
        authored document happened to be repo-scoped, but would have
        silently mislabeled the first database/filesystem Survey Definition
        ever authored. Now inferred from the source filename's
        `{resource_type}-survey-definition-*.md` convention (see
        survey_definition_docs.py's _resource_type_from_filename).

        That first database Survey Definition now exists (Slice 12,
        2026-09-26) — three `database-survey-definition-*.md` documents,
        authored in their own `survey-definitions-database/` directory for
        bootstrap.py's one-batch-per-directory rule, but merged back into
        `documented_definitions()`'s single list by `_extension_docs_dirs`.
        So the blanket "every entry is repo" this test used to assert is
        exactly the mislabeling risk its own docstring predicted — updated
        to assert per-document rather than blanket, the assertion this test
        was always really testing."""
        from resource_explorer.web.routes.survey_definitions import list_definitions
        defs = list_definitions()
        assert defs, "no Survey Definitions documented"
        by_type = {d["name"]: d["resource_type"] for d in defs}
        assert by_type["RepoFullSurvey"] == "repo"
        assert by_type["DatabaseAnalysisSurvey"] == "database"
        assert not any(rt not in ("repo", "database", "filesystem") for rt in by_type.values())


class TestAScheduleMustNameAResourceThatExists:
    """POST /api/schedules/repo/egeria was accepted and stored on 2026-08-28,
    though no repo has that slug — the real one is `egeria_git`.

    Nothing was broken by it; the scheduler reports a stale schedule correctly
    when it fires. The cost was entirely in WHEN it was reported. A typo that
    a caller could fix in the second they made it instead became a weekly
    cadence that looked healthy and never ran, discovered a week later in a
    row nobody was watching — coverage the user believed they had.
    """

    def _client(self):
        from fastapi.testclient import TestClient
        from resource_explorer.web.app import app
        return TestClient(app)

    def test_an_unknown_repo_is_refused(self, monkeypatch):
        from resource_explorer.web.routes import schedules as mod
        monkeypatch.setattr(mod.ProjectRegistry, "get", lambda self, slug: None)
        saved = []
        monkeypatch.setattr(mod.ProjectRegistry, "save_schedule",
                            lambda self, **kw: saved.append(kw))
        resp = self._client().post(
            "/api/schedules/repo/egeria",
            json={"analysis_id": "rag_ingestion", "schedule": "weekly"},
        )
        assert resp.status_code == 404
        assert "egeria" in resp.text
        assert saved == [], "refused, but stored anyway"

    def test_an_unknown_database_is_refused(self, monkeypatch):
        from resource_explorer.web.routes import schedules as mod
        monkeypatch.setattr(mod.ProjectRegistry, "get_database", lambda self, slug: None)
        resp = self._client().post(
            "/api/schedules/database/nope",
            json={"analysis_id": "index_health", "schedule": "daily"},
        )
        assert resp.status_code == 404

    def test_an_unrecognised_entity_type_is_not_reported_as_a_missing_resource(self):
        """The route has never constrained the entity_type vocabulary, and a
        kind added elsewhere must not be rejected here just because this dict
        has not caught up. What it must not do is say the resource is missing
        when it never looked."""
        from resource_explorer.web.routes.schedules import _require_resource
        _require_resource(object(), "something_new", "x")   # must not raise

    def test_validation_runs_before_the_lookup(self, monkeypatch):
        """A bad schedule value is refused as 422 whatever the slug — the
        caller gets the error that is actually theirs to fix, not a 404 about
        a resource that was never the problem."""
        from resource_explorer.web.routes import schedules as mod
        monkeypatch.setattr(mod.ProjectRegistry, "get", lambda self, slug: None)
        resp = self._client().post(
            "/api/schedules/repo/whatever",
            json={"analysis_id": "rag_ingestion", "schedule": "hourly"},
        )
        assert resp.status_code == 422
        assert "hourly" in resp.text

    def test_deleting_a_stale_schedule_is_still_possible(self, monkeypatch):
        """The guard must not reach DELETE. Removing a schedule that points at
        a resource which no longer exists is exactly what that route is for —
        guarding it would strand the rows it exists to clean up."""
        from resource_explorer.web.routes import schedules as mod
        monkeypatch.setattr(mod.ProjectRegistry, "get", lambda self, slug: None)
        monkeypatch.setattr(mod.ProjectRegistry, "delete_schedule",
                            lambda self, *a, **kw: True)
        resp = self._client().delete("/api/schedules/repo/long_gone/rag_ingestion")
        assert resp.status_code == 200
