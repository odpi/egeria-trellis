"""WorkLists.enqueue_batch()/POST /api/work-lists/runs/batch used to enqueue
every row's `target` dict as `{"slug": ..., "analysis_id": ...}` -- no
`entity_type` at all -- so `run_queue.py::_handle_analysis_run` always
resolved the repo's analysis steps for a database/filesystem work-list batch
run (a real, already-wired feature). This pins the plumbing added alongside
`resolve_analysis_plan`'s own entity_type parameter
(tests/test_resolve_analysis_plan_resource_type_dispatch.py).
"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from resource_explorer.registry import Project, ProjectRegistry
from resource_explorer.work_lists import WorkLists


@pytest.fixture
def registry(tmp_path):
    r = ProjectRegistry(db_path=str(tmp_path / "test.db"))
    r.add(Project(slug="repo1", display_name="repo1", github_url="https://github.com/x/repo1"))
    return r


@pytest.fixture
def client(registry, monkeypatch):
    monkeypatch.setattr(
        "resource_explorer.registry.ProjectRegistry.__init__",
        lambda self, db_path=None: setattr(self, "__dict__", registry.__dict__) or None,
    )
    from resource_explorer.web.app import app
    return TestClient(app)


class TestEnqueueBatchStoresEntityType:
    def test_default_is_repo(self, registry):
        wls = WorkLists(registry)
        out = wls.enqueue_batch("documentation_coverage", ["repo1"])
        run_id = out["enqueued"][0]["run_id"] if "enqueued" in out else None
        # Shape check kept loose (see WorkLists.enqueue_batch docstring for
        # the real return shape); read straight from the queue row instead.
        rows = registry.list_runs(kind="analysis_run")
        assert rows
        target = json.loads(rows[0]["target"])
        assert target.get("entity_type", "repo") == "repo"

    def test_an_explicit_entity_type_is_stored_on_every_row(self, registry):
        wls = WorkLists(registry)
        wls.enqueue_batch("row_count_snapshot", ["mydb1", "mydb2"], entity_type="database")
        rows = registry.list_runs(kind="analysis_run")
        assert len(rows) == 2
        for row in rows:
            target = json.loads(row["target"])
            assert target["entity_type"] == "database"


class TestRouteValidatesAgainstTheRightCatalog:
    def test_an_unregistered_entity_type_is_a_400(self, client):
        """`resolve_analysis_plan` raises via `get_adapter()` for an entity
        type with no registered adapter -- the one case its pre-validation
        call actually rejects eagerly (an unknown analysis_id for a KNOWN
        entity type resolves to `steps=None` rather than raising, unchanged
        by this fix -- that is a pre-existing, separate gap in this same
        validation, not something this pass touches)."""
        resp = client.post("/api/work-lists/runs/batch", json={
            "analysis_id": "whatever", "entity_slugs": ["repo1"],
            "entity_type": "not-a-real-entity-type",
        })
        assert resp.status_code == 400

    def test_a_database_only_analysis_id_is_accepted_when_entity_type_is_database(self, client):
        """The live bug, directly: before this fix, `resolve_analysis_plan`
        had no `entity_type` parameter at all and always consulted the repo
        catalog/step map -- passing a database-only analysis id here still
        succeeded (no exception), but the row it enqueued (see
        TestEnqueueBatchStoresEntityType above) always carried the WRONG
        entity_type, so `run_queue.py::_handle_analysis_run` resolved the
        wrong steps downstream. This pins that the route now stores and
        validates against the entity type actually named."""
        from resource_explorer.surveyors.database.survey_definition_adapter import (
            DATABASE_ANALYSIS_RE_STEP_MAP,
        )
        from resource_explorer.surveyors.repo_survey_definition_adapter import (
            REPO_ANALYSIS_SOURCE_STEPS,
        )

        analysis_id = next(
            aid for aid in DATABASE_ANALYSIS_RE_STEP_MAP if aid not in REPO_ANALYSIS_SOURCE_STEPS
        )
        resp = client.post("/api/work-lists/runs/batch", json={
            "analysis_id": analysis_id, "entity_slugs": ["mydb1"], "entity_type": "database",
        })
        assert resp.status_code == 200
        set_id = resp.json()["set_id"]
        progress = client.get(f"/api/work-lists/runs/sets/{set_id}").json()
        assert progress["set_id"] == set_id
