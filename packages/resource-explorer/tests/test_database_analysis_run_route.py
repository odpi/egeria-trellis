"""Tests for POST /api/databases/{slug}/analyses/{analysis_id}/run — the
database per-card dispatch fix (D6 prerequisite, docs/repo-scope-narrowing-
funnel.md), and the activity-tracking fix on top of it.

**The activity-tracking bug this file now pins against regressing:** this
route used to run the survey step(s) synchronously inside the request
(`await asyncio.to_thread(_run)`) and hand back a fully-resolved result with
no `activity_id` at all — `AnalysisRunResult` had no such field, and nothing
called `log_analysis_run`. `/next`'s shared `rerun()` calls this route the
same way it calls the repo route, reads `started.activity_id`, and polls
`GET /api/activity/{activity_id}` — so that came back `undefined`, the poll
404'd with "Activity entry not found", and a run that had, by then, already
succeeded was reported to the user as a failure. Reproduced live via
`db_activity_signals`'s "Is this database alive…" Questions-checklist card.

The fix mirrors `projects.py`'s `run_single_analysis` /
`tests/test_routes_enqueue_not_thread.py`: the route validates synchronously
(so an unmapped/unknown analysis_id or missing credentials is still a 400,
never a queued row) and then enqueues a `database_analysis_run` onto the run
queue rather than running anything itself — the actual survey work happens in
`resource_explorer.workflows.analysis.execute_and_record_database_analysis`,
called by `run_queue.py`'s `_handle_database_analysis_run`, exercised
directly by `tests/test_run_queue.py`-style tests below.
"""
from __future__ import annotations

import json
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from resource_explorer.registry import DatabaseEntity, ProjectRegistry


@pytest.fixture
def registry(tmp_path):
    r = ProjectRegistry(db_path=str(tmp_path / "test.db"))
    r.register_database(DatabaseEntity(
        slug="mydb", display_name="My DB", db_type="postgresql",
        host="localhost", port=5432, database_name="mydb",
        db_user="admin", db_password="secret",
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


class TestRunSingleDatabaseAnalysisValidation:
    """Validation still happens synchronously — an unknown database, an
    unmapped/unknown analysis_id, or missing credentials must be a 404/400
    with nothing queued, not a queued row that fails later in the worker."""

    def test_404_for_unknown_database(self, client, registry):
        resp = client.post("/api/databases/nope/analyses/schema_inventory/run")
        assert resp.status_code == 404
        assert registry.list_runs() == []

    def test_400_for_unmapped_analysis_id(self, client, registry):
        # egeria_db_survey is Egeria-native (publish action) — not local-survey-dispatchable.
        resp = client.post("/api/databases/mydb/analyses/egeria_db_survey/run")
        assert resp.status_code == 400
        assert "no local survey step" in resp.json()["detail"]
        assert registry.list_runs() == []

    def test_400_for_unknown_analysis_id(self, client, registry):
        resp = client.post("/api/databases/mydb/analyses/not_a_real_id/run")
        assert resp.status_code == 400
        assert registry.list_runs() == []

    def test_400_when_no_stored_credentials(self, client, registry):
        registry.register_database(DatabaseEntity(
            slug="nocreds", display_name="No Creds", db_type="postgresql",
            host="localhost", port=5432, database_name="nocreds",
        ))
        resp = client.post("/api/databases/nocreds/analyses/schema_inventory/run")
        assert resp.status_code == 400
        assert "No stored database credentials" in resp.json()["detail"]
        assert registry.list_runs() == []

    def test_db_derived_needs_no_credentials(self, client, registry):
        """db_derived is zero-fetch — it must not be refused for missing
        credentials the way a DATABASE_SURVEYOR_STEP_MAP entry is."""
        registry.register_database(DatabaseEntity(
            slug="nocreds2", display_name="No Creds 2", db_type="postgresql",
            host="localhost", port=5432, database_name="nocreds2",
        ))
        resp = client.post("/api/databases/nocreds2/analyses/schema_conventions/run")
        assert resp.status_code == 200, resp.text


class TestRunSingleDatabaseAnalysisEnqueues:
    """The route enqueues; it does not run anything itself, and the response
    carries the activity_id the frontend polls — same contract as the repo
    route (tests/test_routes_enqueue_not_thread.py)."""

    def test_the_response_carries_activity_id_and_run_id(self, client, registry):
        resp = client.post("/api/databases/mydb/analyses/schema_inventory/run")
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["status"] == "started"
        assert data["activity_id"]
        assert data["run_id"]
        assert data["slug"] == "mydb"
        assert data["analysis_id"] == "schema_inventory"

    def test_the_activity_entry_is_queryable_immediately(self, client, registry):
        """GET /api/activity/{activity_id} must work right after the POST
        returns, even though the background work has not run yet — this is
        exactly the request/response pair that used to 404 with "Activity
        entry not found"."""
        data = client.post("/api/databases/mydb/analyses/schema_inventory/run").json()
        resp = client.get(f"/api/activity/{data['activity_id']}")
        assert resp.status_code == 200
        assert resp.json()["status"] == "running"

    def test_the_route_leaves_a_queued_row_and_runs_nothing(self, client, registry):
        with patch(
            "resource_explorer.workflows.analysis.run_database_analysis"
        ) as run:
            resp = client.post("/api/databases/mydb/analyses/schema_inventory/run")
        assert resp.status_code == 200, resp.text
        run.assert_not_called()

        rows = registry.list_runs(state="queued")
        assert len(rows) == 1
        assert rows[0]["kind"] == "database_analysis_run"
        assert json.loads(rows[0]["target"]) == {
            "slug": "mydb", "analysis_id": "schema_inventory",
        }

    def test_the_queue_row_points_back_at_the_activity_entry(self, client, registry):
        data = client.post("/api/databases/mydb/analyses/schema_inventory/run").json()
        assert registry.get_run(data["run_id"])["result_ref"] == data["activity_id"]

    def test_credentials_are_not_carried_in_the_queue_row(self, client, registry):
        """The database's stored password must never land in the `runs` table
        — the worker re-reads it from the registry by slug at execution
        time, the same way the old synchronous handler did."""
        data = client.post("/api/databases/mydb/analyses/schema_inventory/run").json()
        target = registry.get_run(data["run_id"])["target"]
        assert "secret" not in target


class TestDatabaseAnalysisRunQueueHandler:
    """Exercises the worker-side path directly — this is what actually runs
    the survey and closes out the activity entry, whether or not anything is
    watching."""

    def test_dispatches_only_mapped_steps_and_closes_the_activity_entry(self, client, registry):
        with patch(
            "resource_explorer.surveyors.database.database_surveyor.run_database_survey",
        ) as mock_run:
            mock_run.return_value = {"annotations": [], "errors": []}
            data = client.post("/api/databases/mydb/analyses/schema_inventory/run").json()
            row = registry.claim_next_run("test-worker")
            from resource_explorer.run_queue import execute_run
            outcome = execute_run(row, registry)

        assert outcome.state == "succeeded"
        _, kwargs = mock_run.call_args
        assert kwargs["steps"] == ["schema", "views"]
        assert kwargs["credentials"] == {"user": "admin", "password": "secret"}

        entry = registry.get_activity(data["activity_id"])
        assert entry["status"] == "ok"

    def test_row_count_snapshot_dispatches_statistics_step(self, client, registry):
        with patch(
            "resource_explorer.surveyors.database.database_surveyor.run_database_survey",
        ) as mock_run:
            mock_run.return_value = {"annotations": [], "errors": []}
            client.post("/api/databases/mydb/analyses/row_count_snapshot/run")
            row = registry.claim_next_run("test-worker")
            from resource_explorer.run_queue import execute_run
            execute_run(row, registry)

        _, kwargs = mock_run.call_args
        assert kwargs["steps"] == ["schema", "statistics"]

    def test_survey_exception_marks_the_run_and_activity_entry_failed(self, client, registry):
        with patch(
            "resource_explorer.surveyors.database.database_surveyor.run_database_survey",
        ) as mock_run:
            mock_run.side_effect = RuntimeError("connection refused")
            data = client.post("/api/databases/mydb/analyses/schema_inventory/run").json()
            row = registry.claim_next_run("test-worker")
            from resource_explorer.run_queue import execute_run
            outcome = execute_run(row, registry)

        assert outcome.state == "failed"
        assert "connection refused" in outcome.error
        entry = registry.get_activity(data["activity_id"])
        assert entry["status"] == "error"
        assert "connection refused" in entry["detail"]

    def test_db_derived_analysis_runs_with_no_connection_and_closes_the_activity_entry(
            self, client, registry):
        data = client.post("/api/databases/mydb/analyses/schema_conventions/run").json()
        row = registry.claim_next_run("test-worker")
        from resource_explorer.run_queue import execute_run
        outcome = execute_run(row, registry)

        assert outcome.state == "succeeded"
        entry = registry.get_activity(data["activity_id"])
        assert entry["status"] == "ok"
        assert "no fetch" in entry["summary"]
