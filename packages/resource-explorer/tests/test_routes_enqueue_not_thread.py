"""Routes enqueue; they do not spawn threads.

The one property step 2b's whole point rests on, and the one a later change is
most likely to undo by accident — spawning a thread "just for this one route"
looks local and harmless, and would put a survey back inside a web process that
the plan's `web` role says must never outlive a request.

Asserted three ways, because each catches a different mistake:

* **Structurally**, that no route module spawns a thread for these kinds. A
  behavioural test cannot see a thread that was started and immediately
  finished.
* **Behaviourally**, that hitting the route leaves a `queued` row.
* **By absence**, that nothing ran — the run's own workflow function is
  patched and asserted uncalled, so a route that both enqueued AND executed
  would fail rather than look right.
"""
from __future__ import annotations

import json
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from resource_explorer.registry import Project, ProjectRegistry


@pytest.fixture
def registry(tmp_path, monkeypatch):
    r = ProjectRegistry(db_path=str(tmp_path / "routes.db"))
    r.add(Project(
        slug="myproj", display_name="My Project",
        github_url="https://github.com/test/myproj", description="A test repo.",
    ))
    # A callable stand-in rather than a MagicMock: routes read
    # ProjectRegistry.RUN_STATES / RUN_KINDS as class attributes for validation,
    # and a mock would answer those with a mock, turning a 400 into a TypeError.
    def _factory(*a, **k):
        return r

    _factory.RUN_STATES = ProjectRegistry.RUN_STATES
    _factory.RUN_KINDS = ProjectRegistry.RUN_KINDS
    monkeypatch.setattr("resource_explorer.registry.ProjectRegistry", _factory)
    return r


@pytest.fixture
def client(registry):
    from resource_explorer.web.app import app

    return TestClient(app)


class TestNoThreadSpawning:
    ROUTE_MODULES = ("projects.py", "survey_definitions.py", "databases.py")

    def test_the_backgrounding_route_modules_no_longer_spawn_threads(self):
        from pathlib import Path

        routes = (Path(__file__).resolve().parents[1] / "resource_explorer"
                  / "web" / "routes")
        offenders = {
            name: src.count("threading.Thread")
            for name in self.ROUTE_MODULES
            for src in [(routes / name).read_text()]
            if "threading.Thread" in src
        }
        assert offenders == {}, (
            "a route is spawning a thread again — that work belongs on the run "
            f"queue (run_queue.py), not inside a web process: {offenders}"
        )


class TestAnalysisRunEnqueues:
    def test_the_route_leaves_a_queued_row_and_runs_nothing(self, client, registry):
        with patch("resource_explorer.workflows.analysis.run_analysis") as run:
            resp = client.post("/api/projects/myproj/analyses/security_scan/run")
        assert resp.status_code == 200, resp.text
        run.assert_not_called()

        rows = registry.list_runs(state="queued")
        assert len(rows) == 1
        assert rows[0]["kind"] == "analysis_run"
        assert json.loads(rows[0]["target"]) == {
            "slug": "myproj", "analysis_id": "security_scan", "publish": None,
        }

    def test_the_response_still_carries_the_activity_id_the_frontend_polls(self, client, registry):
        """The queue is invisible to the browser on purpose: it polls
        GET /api/activity/{id}, exactly as before."""
        resp = client.post("/api/projects/myproj/analyses/security_scan/run")
        data = resp.json()
        assert data["status"] == "started"
        assert registry.get_activity(data["activity_id"])["status"] == "running"

    def test_the_queue_row_points_back_at_that_activity_entry(self, client, registry):
        data = client.post("/api/projects/myproj/analyses/security_scan/run").json()
        assert registry.get_run(data["run_id"])["result_ref"] == data["activity_id"]

    def test_validation_is_still_synchronous(self, client, registry):
        """An unknown id must be a 400, not a queued row that fails a minute
        later in another process where nobody is looking."""
        resp = client.post("/api/projects/myproj/analyses/not_a_real_analysis/run")
        assert resp.status_code == 400
        assert registry.list_runs() == []


class TestPublishChoiceThreadsThroughTheQueueRow:
    """run-in-background plan: `publish` travels in the queue row's `target`
    dict exactly the way `force` already does — a query param, not run."""

    def test_background_is_recorded_on_the_queued_row(self, client, registry):
        resp = client.post(
            "/api/projects/myproj/analyses/security_scan/run?publish=background"
        )
        assert resp.status_code == 200, resp.text
        row = registry.list_runs(state="queued")[0]
        assert json.loads(row["target"]) == {
            "slug": "myproj", "analysis_id": "security_scan", "publish": "background",
        }

    def test_wait_is_recorded_on_the_queued_row(self, client, registry):
        resp = client.post(
            "/api/projects/myproj/analyses/security_scan/run?publish=wait"
        )
        assert resp.status_code == 200, resp.text
        row = registry.list_runs(state="queued")[0]
        assert json.loads(row["target"])["publish"] == "wait"

    def test_an_invalid_publish_value_is_a_400_not_a_queued_row(self, client, registry):
        resp = client.post(
            "/api/projects/myproj/analyses/security_scan/run?publish=whenever"
        )
        assert resp.status_code == 400
        assert registry.list_runs() == []


class TestScoutingScanEnqueues:
    def test_the_route_queues_a_scouting_scan(self, client, registry):
        with patch("resource_explorer.workflows.scouting.run_scouting_scan") as run:
            resp = client.post("/api/projects/myproj/scouting-scan")
        assert resp.status_code == 200, resp.text
        run.assert_not_called()
        assert registry.list_runs(state="queued")[0]["kind"] == "scouting_scan"

    def test_unknown_repo_is_still_a_404(self, client, registry):
        assert client.post("/api/projects/nope/scouting-scan").status_code == 404
        assert registry.list_runs() == []


class TestSurveyDefinitionRunEnqueues:
    def test_the_route_queues_the_run_with_its_parameters(self, client, registry):
        with patch("resource_explorer.workflows.survey_definition.run_definition") as run:
            resp = client.post(
                "/api/survey-definitions/repo/myproj/run",
                json={"survey_definition_ref": "RepoFullSurvey", "db_user": "u"},
            )
        assert resp.status_code == 200, resp.text
        run.assert_not_called()
        row = registry.list_runs(state="queued")[0]
        assert row["kind"] == "survey_definition_run"
        target = json.loads(row["target"])
        assert target["entity_type"] == "repo"
        assert target["params"]["survey_definition_ref"] == "RepoFullSurvey"
        assert target["params"]["db_user"] == "u"

    def test_the_running_activity_row_does_not_claim_the_web_process_owns_it(
            self, client, registry):
        """The web process is not the runner any more. Stamping it would make a
        web restart mark every in-flight run interrupted while a worker was
        still driving it — see run_queue.execute_run, which stamps the real
        owner when the work starts."""
        from resource_explorer.run_reconciler import owner_of

        data = client.post(
            "/api/survey-definitions/repo/myproj/run",
            json={"survey_definition_ref": "RepoFullSurvey"},
        ).json()
        detail = registry.get_activity(data["activity_id"])["detail"]
        assert owner_of(detail) == {}
        # …but the ref the last-run badge reads is still there.
        assert json.loads(detail)["survey_definition_ref"] == "RepoFullSurvey"


class TestRunsApi:
    def test_get_a_run(self, client, registry):
        run_id = registry.enqueue_run("scouting_scan", {"slug": "myproj"})
        resp = client.get(f"/api/runs/{run_id}")
        assert resp.status_code == 200
        assert resp.json()["state"] == "queued"

    def test_an_unknown_run_is_404(self, client):
        assert client.get("/api/runs/nope").status_code == 404

    def test_list_filters_by_state(self, client, registry):
        registry.enqueue_run("scouting_scan", {"slug": "a"})
        still_queued = registry.enqueue_run("scouting_scan", {"slug": "b"})
        done = registry.claim_next_run("h:1")   # oldest first, so this is "a"
        registry.finish_run(done["id"], "succeeded")

        rows = client.get("/api/runs/?state=queued").json()
        assert [r["id"] for r in rows] == [still_queued]
        assert {r["id"] for r in client.get("/api/runs/?state=succeeded").json()} == {done["id"]}

    def test_list_rejects_an_unknown_state(self, client):
        assert client.get("/api/runs/?state=wandering").status_code == 400

    def test_cancel_a_queued_run(self, client, registry):
        run_id = registry.enqueue_run("scouting_scan", {"slug": "a"})
        assert client.post(f"/api/runs/{run_id}/cancel").status_code == 200
        assert registry.get_run(run_id)["state"] == "cancelled"

    def test_cancelling_a_claimed_run_is_a_409_not_a_lie(self, client, registry):
        run_id = registry.enqueue_run("scouting_scan", {"slug": "a"})
        registry.claim_next_run("h:1")
        resp = client.post(f"/api/runs/{run_id}/cancel")
        assert resp.status_code == 409
        assert registry.get_run(run_id)["state"] == "claimed"


def test_cancelling_a_held_engine_action_row_is_a_409(client, registry):
    """A held kind's row stands for an Egeria engine action RE has claimed; cancelling it locally would leave
    that action claimed with no one to complete it."""
    run_id = registry.enqueue_run("engine_action", {"engine_action_guid": "a-1"})
    resp = client.post(f"/api/runs/{run_id}/cancel")
    assert resp.status_code == 409
    assert "engine_action" in resp.json()["detail"]
    assert registry.get_run(run_id)["state"] == "queued"
