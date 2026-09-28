"""Tests for FastAPI web routes — projects, stats, query endpoints."""
from __future__ import annotations

import importlib.util
import json
import sqlite3
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

_pygithub_available = pytest.mark.skipif(
    importlib.util.find_spec("github") is None, reason="PyGitHub not installed"
)

from resource_explorer.registry import Project, ProjectRegistry


# ── test app setup ─────────────────────────────────────────────────────────────

@pytest.fixture
def registry(tmp_path):
    r = ProjectRegistry(db_path=str(tmp_path / "test.db"))
    r.add(Project(
        slug="myproj",
        display_name="My Project",
        github_url="https://github.com/test/myproj",
        description="A test project",
        collections=["myproj_python_code", "myproj_markdown_docs"],
    ))
    return r


@pytest.fixture
def client(registry, monkeypatch):
    monkeypatch.setattr("resource_explorer.registry.ProjectRegistry.__init__",
                        lambda self, db_path=None: setattr(self, "__dict__", registry.__dict__) or None)
    from resource_explorer.web.app import app
    return TestClient(app)


# ── /health ───────────────────────────────────────────────────────────────────

class TestHealth:
    def test_health_returns_ok(self):
        from resource_explorer.web.app import app
        c = TestClient(app)
        resp = c.get("/health")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}


# ── /api/projects ─────────────────────────────────────────────────────────────

class TestProjectsRouter:
    def test_list_projects(self, client):
        resp = client.get("/api/projects/")
        assert resp.status_code == 200
        projects = resp.json()
        assert len(projects) == 1
        assert projects[0]["slug"] == "myproj"
        assert projects[0]["display_name"] == "My Project"

    def test_list_projects_includes_required_fields(self, client):
        resp = client.get("/api/projects/")
        p = resp.json()[0]
        assert "slug" in p
        assert "display_name" in p
        assert "github_url" in p
        assert "status" in p
        assert "collections" in p
        assert "last_indexed_at" in p

    def test_list_projects_defaults_disposition_to_undecided(self, client):
        resp = client.get("/api/projects/")
        assert resp.json()[0]["disposition"] == "undecided"

    def test_list_projects_excludes_ignored_by_default(self, client, registry):
        registry.set_disposition("https://github.com/test/myproj", "ignored", reason="too small")
        resp = client.get("/api/projects/")
        assert resp.json() == []

    def test_list_projects_includes_ignored_when_requested(self, client, registry):
        registry.set_disposition("https://github.com/test/myproj", "ignored", reason="too small")
        resp = client.get("/api/projects/?include_ignored=true")
        data = resp.json()
        assert len(data) == 1
        assert data[0]["disposition"] == "ignored"

    def test_list_projects_does_not_exclude_tracking_or_investigating(self, client, registry):
        registry.set_disposition("https://github.com/test/myproj", "investigating")
        resp = client.get("/api/projects/")
        assert len(resp.json()) == 1

    def test_list_projects_does_not_exclude_recommended(self, client, registry):
        """recommended is the positive terminal state — stays visible, same
        as tracking/investigating; only the negative terminal states
        (abandoned/ignored) hide from the default sidebar list."""
        registry.set_disposition("https://github.com/test/myproj", "recommended")
        resp = client.get("/api/projects/")
        data = resp.json()
        assert len(data) == 1
        assert data[0]["disposition"] == "recommended"

    def test_list_projects_does_not_exclude_using(self, client, registry):
        """using is the stronger positive terminal state (already actively
        using the resource, or known use elsewhere in the org) — stays
        visible, same as recommended/tracking/investigating."""
        registry.set_disposition("https://github.com/test/myproj", "using")
        resp = client.get("/api/projects/")
        data = resp.json()
        assert len(data) == 1
        assert data[0]["disposition"] == "using"

    def test_list_projects_excludes_abandoned_by_default(self, client, registry):
        registry.set_disposition("https://github.com/test/myproj", "abandoned", reason="no longer maintained")
        resp = client.get("/api/projects/")
        assert resp.json() == []
        resp = client.get("/api/projects/?include_ignored=true")
        assert len(resp.json()) == 1

    def test_list_projects_excludes_working_set_hidden_by_default(self, client, registry):
        registry.set_working_set_hidden("repo", "myproj", True)
        resp = client.get("/api/projects/")
        assert resp.json() == []

    def test_list_projects_includes_working_set_hidden_when_requested(self, client, registry):
        registry.set_working_set_hidden("repo", "myproj", True)
        resp = client.get("/api/projects/?include_working_set_hidden=true")
        data = resp.json()
        assert len(data) == 1
        assert data[0]["working_set_hidden"] is True

    def test_working_set_hidden_is_independent_of_disposition(self, client, registry):
        # Hiding via working set must not touch the canonical disposition,
        # and vice versa.
        registry.set_working_set_hidden("repo", "myproj", True)
        resp = client.get("/api/projects/myproj")
        assert resp.json()["disposition"] == "undecided"
        assert resp.json()["working_set_hidden"] is True

    def test_get_project_found(self, client):
        resp = client.get("/api/projects/myproj")
        assert resp.status_code == 200
        assert resp.json()["slug"] == "myproj"

    def test_get_project_not_found(self, client):
        resp = client.get("/api/projects/ghost")
        assert resp.status_code == 404

    def test_delete_project(self, client):
        with patch("resource_explorer.vector_store_pg.MultiCollectionStore") as mock_store:
            mock_store.return_value.drop_collection = MagicMock()
            resp = client.delete("/api/projects/myproj")
        assert resp.status_code == 200
        assert resp.json()["removed"] == "myproj"

    def test_delete_project_not_found(self, client):
        resp = client.delete("/api/projects/ghost")
        assert resp.status_code == 404

    def test_delete_project_removes_registry_row_even_if_collection_drop_fails(self, client, registry):
        # Regression guard — a broken/stale registration (e.g. a collection
        # that was never actually created in pgvector) must not block
        # removing the registry row itself; drop_collection() runs
        # best-effort per collection.
        with patch("resource_explorer.vector_store_pg.MultiCollectionStore") as mock_store:
            mock_store.return_value.drop_collection.side_effect = RuntimeError("no such collection")
            resp = client.delete("/api/projects/myproj")
        assert resp.status_code == 200
        assert resp.json()["removed"] == "myproj"
        assert registry.get("myproj") is None

    @_pygithub_available
    def test_refresh_project_returns_ok(self, client):
        with patch("resource_explorer.ingestion.incremental.IncrementalIndexer") as mock_idx, \
             patch("resource_explorer.query_cache.QueryCache") as mock_cache:
            mock_idx.return_value.refresh = MagicMock()
            mock_cache.return_value.invalidate_project = MagicMock(return_value=0)
            resp = client.post("/api/projects/myproj/refresh")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

    def test_refresh_project_not_found(self, client):
        resp = client.post("/api/projects/ghost/refresh")
        assert resp.status_code == 404


# ── /api/stats ────────────────────────────────────────────────────────────────

def _insert_stats(db_path, slug):
    conn = sqlite3.connect(db_path)
    conn.execute("""
        INSERT INTO project_stats
        (project_slug, fetched_at, stars, forks, watchers, open_issues,
         contributors_count, commits_30d, commits_90d, releases_count,
         latest_release, latest_release_at, primary_language, language_breakdown)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (slug, "2024-06-01T12:00:00", 1200, 150, 1200, 30,
          25, 15, 48, 10, "v2.0.0", "2024-05-01T00:00:00",
          "Python", json.dumps({"Python": 50000})))
    conn.commit()
    conn.close()


class TestStatsRouter:
    def test_get_stats_not_found_project(self, client):
        resp = client.get("/api/stats/ghost")
        assert resp.status_code == 404

    def test_get_stats_no_data_returns_404(self, client):
        resp = client.get("/api/stats/myproj")
        assert resp.status_code == 404

    def test_get_stats_with_data(self, client, registry):
        _insert_stats(registry.db_path, "myproj")
        resp = client.get("/api/stats/myproj")
        assert resp.status_code == 200
        body = resp.json()
        assert body["slug"] == "myproj"
        assert body["stats"]["stars"] == 1200
        assert body["stats"]["primary_language"] == "Python"
        assert isinstance(body["stats"]["language_breakdown"], dict)

    def test_get_history_valid_metric(self, client, registry):
        _insert_stats(registry.db_path, "myproj")
        resp = client.get("/api/stats/myproj/history?metric=stars")
        assert resp.status_code == 200
        body = resp.json()
        assert body["metric"] == "stars"
        assert len(body["data"]) == 1
        assert body["data"][0]["value"] == 1200

    def test_get_history_invalid_metric(self, client):
        resp = client.get("/api/stats/myproj/history?metric=banana")
        assert resp.status_code == 400

    def test_get_history_not_found_project(self, client):
        resp = client.get("/api/stats/ghost/history")
        assert resp.status_code == 404


# ── /api/query ────────────────────────────────────────────────────────────────

class TestQueryRouter:
    def test_query_endpoint(self, client):
        with patch("resource_explorer.rag_system.RAGSystem") as mock_rag_cls:
            mock_rag_cls.return_value.query.return_value = "mocked response"
            resp = client.post("/api/query/", json={"query": "what is this project?"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["response"] == "mocked response"
        assert "intent" in body

    def test_query_with_project_scope(self, client):
        with patch("resource_explorer.rag_system.RAGSystem") as mock_rag_cls:
            rag_mock = mock_rag_cls.return_value
            rag_mock.query.return_value = "scoped response"
            resp = client.post("/api/query/", json={
                "query": "what is this project?",
                "project_slug": "myproj",
            })
        assert resp.status_code == 200
        rag_mock.query.assert_called_once_with("what is this project?", resource_slug="myproj")


class TestQueryFeedbackRouter:
    """POST /api/query/feedback — vote is trinary (+1/0/-1), 0 being the
    neutral/"partially correct" vote (see docs/feedback-signals-shared.md).
    The route itself is a two-line delegate to MetricsCollector.record_feedback;
    what matters here is that vote=0 survives the request round-trip rather
    than being coerced or dropped (e.g. by a falsy-value check)."""

    def test_neutral_vote_passed_through_unmodified(self, client):
        with patch(
            "resource_explorer.observability.metrics_collector.MetricsCollector"
        ) as mock_cls:
            resp = client.post("/api/query/feedback", json={
                "query_hash": "abc123", "vote": 0,
            })
        assert resp.status_code == 200
        assert resp.json() == {"recorded": True}
        mock_cls.return_value.record_feedback.assert_called_once_with("abc123", 0)

    def test_positive_and_negative_votes_still_pass_through(self, client):
        with patch(
            "resource_explorer.observability.metrics_collector.MetricsCollector"
        ) as mock_cls:
            client.post("/api/query/feedback", json={"query_hash": "h", "vote": 1})
            client.post("/api/query/feedback", json={"query_hash": "h", "vote": -1})
        calls = [c.args for c in mock_cls.return_value.record_feedback.call_args_list]
        assert calls == [("h", 1), ("h", -1)]


# ── /api/activity/rfas + PATCH /api/activity/rfas/{rfa_id} ─────────────────────

def _write_rfa_activity_entry(registry, entry_id="entry-1", num_rfas=1, extra_annotations=None):
    from resource_explorer.registry import ActivityEntry
    annotations = [
        {"annotation_type": "RequestForActionAnnotation", "analysis_name": "Security Scan",
         "count": 1, "summary": f"Finding {i}", "status": "local"}
        for i in range(num_rfas)
    ]
    if extra_annotations:
        annotations.extend(extra_annotations)
    registry.write_activity(ActivityEntry(
        id=entry_id, ts="2026-08-01T00:00:00", operation="survey", intent="assessment",
        entity_type="repo", entity_slug="myproj", entity_name="My Project",
        annotations=annotations,
    ))


# ── /api/curate — tags, resource feedback, curator notes ───────────────────────

class TestCurateTagsRouter:
    def test_add_list_remove_tag(self, client):
        resp = client.post("/api/curate/tags/repo/myproj", json={"tag": "Gold-Tier"})
        assert resp.status_code == 200
        assert resp.json()["tag"] == "gold-tier"  # normalized lowercase

        listed = client.get("/api/curate/tags/repo/myproj").json()
        assert listed == ["gold-tier"]

        resp = client.delete("/api/curate/tags/repo/myproj/gold-tier")
        assert resp.status_code == 200
        assert client.get("/api/curate/tags/repo/myproj").json() == []

    def test_add_tag_rejects_empty(self, client):
        resp = client.post("/api/curate/tags/repo/myproj", json={"tag": "   "})
        assert resp.status_code == 400

    def test_list_all_tags_with_counts(self, client):
        client.post("/api/curate/tags/repo/proj-a", json={"tag": "gold-tier"})
        client.post("/api/curate/tags/database/db-a", json={"tag": "gold-tier"})
        tags = {t["tag"]: t["count"] for t in client.get("/api/curate/tags").json()}
        assert tags["gold-tier"] == 2

    def test_resources_by_tag(self, client):
        client.post("/api/curate/tags/repo/proj-a", json={"tag": "gold-tier"})
        resp = client.get("/api/curate/tags/gold-tier/resources")
        assert resp.status_code == 200
        assert {"entity_type": "repo", "entity_slug": "proj-a"} in resp.json()

    def test_resources_by_tag_route_does_not_shadow_list_tags_route(self, client):
        # Regression guard: /tags/{tag}/resources and /tags/{entity_type}/{slug}
        # are both 2-segment paths — declaration order matters (see curate.py's
        # comment). A resource literally named "resources" would be the edge
        # case that breaks if the order were ever flipped back.
        client.post("/api/curate/tags/repo/myproj", json={"tag": "gold-tier"})
        resp = client.get("/api/curate/tags/repo/myproj")
        assert resp.status_code == 200
        assert resp.json() == ["gold-tier"]


class TestCurateFeedbackRouter:
    def test_add_and_list_feedback(self, client):
        resp = client.post("/api/curate/feedback/repo/myproj", json={
            "rating": 4, "category": "quality", "message": "Schema looks stale",
        })
        assert resp.status_code == 200
        listed = client.get("/api/curate/feedback/repo/myproj").json()
        assert len(listed) == 1
        assert listed[0]["message"] == "Schema looks stale"

    def test_rejects_empty_message(self, client):
        resp = client.post("/api/curate/feedback/repo/myproj", json={"message": ""})
        assert resp.status_code == 400

    def test_rejects_out_of_range_rating(self, client):
        resp = client.post("/api/curate/feedback/repo/myproj", json={"rating": 9, "message": "x"})
        assert resp.status_code == 400

    def test_feedback_without_rating_is_allowed(self, client):
        resp = client.post("/api/curate/feedback/repo/myproj", json={"message": "just a note"})
        assert resp.status_code == 200
        assert resp.json()["rating"] is None


class TestCurateNotesRouter:
    def test_add_list_delete_note(self, client):
        resp = client.post("/api/curate/notes/repo/myproj", json={"note": "Needs a better README"})
        assert resp.status_code == 200
        note_id = resp.json()["id"]

        listed = client.get("/api/curate/notes/repo/myproj").json()
        assert len(listed) == 1

        resp = client.delete(f"/api/curate/notes/{note_id}")
        assert resp.status_code == 200
        assert client.get("/api/curate/notes/repo/myproj").json() == []

    def test_rejects_empty_note(self, client):
        resp = client.post("/api/curate/notes/repo/myproj", json={"note": "  "})
        assert resp.status_code == 400

    def test_delete_nonexistent_note_404s(self, client):
        resp = client.delete("/api/curate/notes/nonexistent-id")
        assert resp.status_code == 404


@pytest.fixture()
def signed_in_curator(client):
    """Sign the shared TestClient in, for the curate routes.

    Curate is authorization-gated as of 2026-09-04 (plan §4): ownership is
    curation by default, and "nobody is signed in" is denied whoever owns what.
    The suite runs with the login *gate* off (`tests/conftest.py`), so a curate
    route reached with no caller 403s at the workflow layer — the right answer,
    and it means these tests have to name who is curating rather than be exempt
    from the question.

    A **header**, not a ContextVar set here: `_identity_middleware` resolves the
    caller from the request's own token and resets it in `finally`, so an
    identity set in the test's context is overwritten with None before any
    handler runs. Setting the ContextVar directly looks like it works and does
    nothing — which is worth knowing, because it is the mistake the middleware's
    own `finally` guarantees.
    """
    from resource_explorer.auth import create_access_token

    token = create_access_token(user_id="dan", egeria_token="egeria-token")
    client.headers["Authorization"] = f"Bearer {token}"
    yield "dan"
    client.headers.pop("Authorization", None)


class TestQueryFeedbackRouter:
    """POST /api/query/feedback — vote is trinary (+1/0/-1), 0 being the
    neutral/"partially correct" vote (see docs/feedback-signals-shared.md).
    The route itself is a two-line delegate to MetricsCollector.record_feedback;
    what matters here is that vote=0 survives the request round-trip rather
    than being coerced or dropped (e.g. by a falsy-value check)."""

    def test_neutral_vote_passed_through_unmodified(self, client):
        with patch(
            "resource_explorer.observability.metrics_collector.MetricsCollector"
        ) as mock_cls:
            resp = client.post("/api/query/feedback", json={
                "query_hash": "abc123", "vote": 0,
            })
        assert resp.status_code == 200
        assert resp.json() == {"recorded": True}
        mock_cls.return_value.record_feedback.assert_called_once_with("abc123", 0)

    def test_positive_and_negative_votes_still_pass_through(self, client):
        with patch(
            "resource_explorer.observability.metrics_collector.MetricsCollector"
        ) as mock_cls:
            client.post("/api/query/feedback", json={"query_hash": "h", "vote": 1})
            client.post("/api/query/feedback", json={"query_hash": "h", "vote": -1})
        calls = [c.args for c in mock_cls.return_value.record_feedback.call_args_list]
        assert calls == [("h", 1), ("h", -1)]


# ── /api/activity/rfas + PATCH /api/activity/rfas/{rfa_id} ─────────────────────

def _write_rfa_activity_entry(registry, entry_id="entry-1", num_rfas=1, extra_annotations=None):
    from resource_explorer.registry import ActivityEntry
    annotations = [
        {"annotation_type": "RequestForActionAnnotation", "analysis_name": "Security Scan",
         "count": 1, "summary": f"Finding {i}", "status": "local"}
        for i in range(num_rfas)
    ]
    if extra_annotations:
        annotations.extend(extra_annotations)
    registry.write_activity(ActivityEntry(
        id=entry_id, ts="2026-08-01T00:00:00", operation="survey", intent="assessment",
        entity_type="repo", entity_slug="myproj", entity_name="My Project",
        annotations=annotations,
    ))


# ── /api/curate — tags, resource feedback, curator notes ───────────────────────

class TestCurateTagsRouter:
    def test_add_list_remove_tag(self, client):
        resp = client.post("/api/curate/tags/repo/myproj", json={"tag": "Gold-Tier"})
        assert resp.status_code == 200
        assert resp.json()["tag"] == "gold-tier"  # normalized lowercase

        listed = client.get("/api/curate/tags/repo/myproj").json()
        assert listed == ["gold-tier"]

        resp = client.delete("/api/curate/tags/repo/myproj/gold-tier")
        assert resp.status_code == 200
        assert client.get("/api/curate/tags/repo/myproj").json() == []

    def test_add_tag_rejects_empty(self, client):
        resp = client.post("/api/curate/tags/repo/myproj", json={"tag": "   "})
        assert resp.status_code == 400

    def test_list_all_tags_with_counts(self, client):
        client.post("/api/curate/tags/repo/proj-a", json={"tag": "gold-tier"})
        client.post("/api/curate/tags/database/db-a", json={"tag": "gold-tier"})
        tags = {t["tag"]: t["count"] for t in client.get("/api/curate/tags").json()}
        assert tags["gold-tier"] == 2

    def test_resources_by_tag(self, client):
        client.post("/api/curate/tags/repo/proj-a", json={"tag": "gold-tier"})
        resp = client.get("/api/curate/tags/gold-tier/resources")
        assert resp.status_code == 200
        assert {"entity_type": "repo", "entity_slug": "proj-a"} in resp.json()

    def test_resources_by_tag_route_does_not_shadow_list_tags_route(self, client):
        # Regression guard: /tags/{tag}/resources and /tags/{entity_type}/{slug}
        # are both 2-segment paths — declaration order matters (see curate.py's
        # comment). A resource literally named "resources" would be the edge
        # case that breaks if the order were ever flipped back.
        client.post("/api/curate/tags/repo/myproj", json={"tag": "gold-tier"})
        resp = client.get("/api/curate/tags/repo/myproj")
        assert resp.status_code == 200
        assert resp.json() == ["gold-tier"]


class TestCurateFeedbackRouter:
    def test_add_and_list_feedback(self, client):
        resp = client.post("/api/curate/feedback/repo/myproj", json={
            "rating": 4, "category": "quality", "message": "Schema looks stale",
        })
        assert resp.status_code == 200
        listed = client.get("/api/curate/feedback/repo/myproj").json()
        assert len(listed) == 1
        assert listed[0]["message"] == "Schema looks stale"

    def test_rejects_empty_message(self, client):
        resp = client.post("/api/curate/feedback/repo/myproj", json={"message": ""})
        assert resp.status_code == 400

    def test_rejects_out_of_range_rating(self, client):
        resp = client.post("/api/curate/feedback/repo/myproj", json={"rating": 9, "message": "x"})
        assert resp.status_code == 400

    def test_feedback_without_rating_is_allowed(self, client):
        resp = client.post("/api/curate/feedback/repo/myproj", json={"message": "just a note"})
        assert resp.status_code == 200
        assert resp.json()["rating"] is None


class TestCurateNotesRouter:
    def test_add_list_delete_note(self, client):
        resp = client.post("/api/curate/notes/repo/myproj", json={"note": "Needs a better README"})
        assert resp.status_code == 200
        note_id = resp.json()["id"]

        listed = client.get("/api/curate/notes/repo/myproj").json()
        assert len(listed) == 1

        resp = client.delete(f"/api/curate/notes/{note_id}")
        assert resp.status_code == 200
        assert client.get("/api/curate/notes/repo/myproj").json() == []

    def test_rejects_empty_note(self, client):
        resp = client.post("/api/curate/notes/repo/myproj", json={"note": "  "})
        assert resp.status_code == 400

    def test_delete_nonexistent_note_404s(self, client):
        resp = client.delete("/api/curate/notes/nonexistent-id")
        assert resp.status_code == 404


@pytest.fixture()
def signed_in_curator(client):
    """Sign the shared TestClient in, for the curate routes.

    Curate is authorization-gated as of 2026-09-04 (plan §4): ownership is
    curation by default, and "nobody is signed in" is denied whoever owns what.
    The suite runs with the login *gate* off (`tests/conftest.py`), so a curate
    route reached with no caller 403s at the workflow layer — the right answer,
    and it means these tests have to name who is curating rather than be exempt
    from the question.

    A **header**, not a ContextVar set here: `_identity_middleware` resolves the
    caller from the request's own token and resets it in `finally`, so an
    identity set in the test's context is overwritten with None before any
    handler runs. Setting the ContextVar directly looks like it works and does
    nothing — which is worth knowing, because it is the mistake the middleware's
    own `finally` guarantees.
    """
    from resource_explorer.auth import create_access_token

    token = create_access_token(user_id="dan", egeria_token="egeria-token")
    client.headers["Authorization"] = f"Bearer {token}"
    yield "dan"
    client.headers.pop("Authorization", None)


@pytest.mark.usefixtures("signed_in_curator", "mock_egeria_client_connections")
class TestCurateComponentVerdictsRouter:
    def test_add_and_list_verdict(self, client):
        resp = client.post("/api/curate/component-verdicts/repo/myproj", json={
            "scope_locator": "src/foo", "verdict": "accepted",
        })
        assert resp.status_code == 200
        assert resp.json()["verdict"] == "accepted"

        listed = client.get("/api/curate/component-verdicts/repo/myproj").json()
        assert listed["src/foo"]["verdict"] == "accepted"

    def test_rejects_empty_scope_locator(self, client):
        resp = client.post("/api/curate/component-verdicts/repo/myproj", json={
            "scope_locator": "  ", "verdict": "accepted",
        })
        assert resp.status_code == 400

    def test_rejects_unknown_verdict(self, client):
        resp = client.post("/api/curate/component-verdicts/repo/myproj", json={
            "scope_locator": "src/foo", "verdict": "maybe",
        })
        assert resp.status_code == 400

    def test_retyped_without_retyped_to_is_rejected(self, client):
        resp = client.post("/api/curate/component-verdicts/repo/myproj", json={
            "scope_locator": "src/foo", "verdict": "retyped",
        })
        assert resp.status_code == 400

    def test_retyped_with_retyped_to_succeeds(self, client):
        resp = client.post("/api/curate/component-verdicts/repo/myproj", json={
            "scope_locator": "src/foo", "verdict": "retyped", "retyped_to": "library",
        })
        assert resp.status_code == 200
        assert resp.json()["retyped_to"] == "library"

    def test_history_reflects_every_call_newest_first(self, client):
        client.post("/api/curate/component-verdicts/repo/myproj", json={
            "scope_locator": "src/foo", "verdict": "accepted",
        })
        client.post("/api/curate/component-verdicts/repo/myproj", json={
            "scope_locator": "src/foo", "verdict": "rejected",
        })
        history = client.get(
            "/api/curate/component-verdicts/repo/myproj/history",
            params={"scope_locator": "src/foo"},
        ).json()
        assert [h["verdict"] for h in history] == ["rejected", "accepted"]

    def test_accepting_with_no_underlying_finding_reports_a_materialization_error(self, client):
        """The verdict itself still saves — a curator's decision is real
        independent of whether there's anything left to act on — but nothing
        is silently skipped: the response says materialization was attempted
        and could not proceed."""
        resp = client.post("/api/curate/component-verdicts/repo/myproj", json={
            "scope_locator": "src/never-surveyed", "verdict": "accepted",
        })
        assert resp.status_code == 200
        assert resp.json()["verdict"] == "accepted"
        assert resp.json()["materialization"]["status"] == "error"

    def test_rejected_and_retyped_never_attempt_materialization(self, client, registry):
        registry.upsert_finding("myproj", "architecture_recovery", [{
            "check_name": "component", "label": "manifest",
            "detail": {"name": "svc", "type": "Software Service"},
        }], surveyed_at="2026-08-30T00:00:00", scope_locator="src/foo")

        rejected = client.post("/api/curate/component-verdicts/repo/myproj", json={
            "scope_locator": "src/foo", "verdict": "rejected",
        })
        retyped = client.post("/api/curate/component-verdicts/repo/myproj", json={
            "scope_locator": "src/foo", "verdict": "retyped", "retyped_to": "library",
        })
        assert "materialization" not in rejected.json()
        assert "materialization" not in retyped.json()

    def test_accepting_a_real_component_materializes_it(self, client, registry):
        registry.upsert_finding("myproj", "architecture_recovery", [{
            "check_name": "component", "label": "manifest",
            "detail": {"name": "svc", "type": "Software Service", "perspective": "deployment"},
        }], surveyed_at="2026-08-30T00:00:00", scope_locator="src/foo")

        with patch("resource_explorer.surveyors.arch_recovery.materializer.ComponentMaterializer") as MockCls:
            MockCls.return_value.materialize.return_value = {
                "status": "materialized", "guid": "guid-1",
                "qualified_name": "SolutionComponent::repo::myproj::src/foo",
            }
            resp = client.post("/api/curate/component-verdicts/repo/myproj", json={
                "scope_locator": "src/foo", "verdict": "accepted",
            })

        assert resp.status_code == 200
        materialize_call = MockCls.return_value.materialize.call_args
        assert materialize_call.args == ("repo", "myproj", "src/foo")
        assert materialize_call.kwargs["name"] == "svc"
        assert materialize_call.kwargs["component_type"] == "Software Service"
        assert materialize_call.kwargs["perspective"] == "deployment"
        assert resp.json()["materialization"] == {
            "status": "materialized", "guid": "guid-1",
            "qualified_name": "SolutionComponent::repo::myproj::src/foo",
        }


@pytest.mark.usefixtures("signed_in_curator", "mock_egeria_client_connections")
class TestCurateBlueprintVerdictsRouter:
    """docs/blueprint-materialization-plan.md Phase B. Wires are deliberately
    out of scope (project-owner decision, 2026-09-03) — none of these tests
    assert on wire enqueueing, and BlueprintMaterializer is mocked so no
    live Egeria call happens regardless."""

    def _seed_cluster(self, registry, *, members=None, children=None, oversized=False,
                      name="core-services", perspective="physical"):
        registry.upsert_finding("myproj", "architecture_blueprints", [{
            "check_name": "candidate_blueprint", "label": name,
            "detail": {"name": name, "perspective": perspective, "signal": "compose",
                       "carrier": "compose.yaml", "composed_into": "",
                       "size": len(members or []), "members": members or [],
                       "children": children or [], "parent": "", "oversized": oversized,
                       "target_size": 8, "run_scope": "", "not_a_claim": True},
        }], surveyed_at="2026-08-30T00:00:00")

    def test_add_and_list_verdict(self, client):
        resp = client.post("/api/curate/blueprint-verdicts/repo/myproj", json={
            "perspective": "physical", "cluster_name": "core-services", "verdict": "accepted",
        })
        assert resp.status_code == 200
        assert resp.json()["verdict"] == "accepted"

        listed = client.get("/api/curate/blueprint-verdicts/repo/myproj").json()
        assert listed["physical::core-services"]["verdict"] == "accepted"

    def test_component_verdicts_and_blueprint_verdicts_do_not_leak_into_each_other(self, client):
        client.post("/api/curate/component-verdicts/repo/myproj", json={
            "scope_locator": "src/foo", "verdict": "accepted",
        })
        client.post("/api/curate/blueprint-verdicts/repo/myproj", json={
            "perspective": "physical", "cluster_name": "core-services", "verdict": "accepted",
        })
        components = client.get("/api/curate/component-verdicts/repo/myproj").json()
        blueprints = client.get("/api/curate/blueprint-verdicts/repo/myproj").json()
        assert "physical::core-services" not in components
        assert "src/foo" not in blueprints

    def test_rejects_empty_perspective_or_cluster_name(self, client):
        resp = client.post("/api/curate/blueprint-verdicts/repo/myproj", json={
            "perspective": "  ", "cluster_name": "core-services", "verdict": "accepted",
        })
        assert resp.status_code == 400

    def test_rejects_unknown_verdict(self, client):
        resp = client.post("/api/curate/blueprint-verdicts/repo/myproj", json={
            "perspective": "physical", "cluster_name": "core-services", "verdict": "retyped",
        })
        assert resp.status_code == 400

    def test_rejected_never_attempts_materialization(self, client, registry):
        self._seed_cluster(registry, members=["a"])
        resp = client.post("/api/curate/blueprint-verdicts/repo/myproj", json={
            "perspective": "physical", "cluster_name": "core-services", "verdict": "rejected",
        })
        assert "materialization" not in resp.json()

    def test_accepting_with_no_underlying_cluster_reports_a_materialization_error(self, client):
        resp = client.post("/api/curate/blueprint-verdicts/repo/myproj", json={
            "perspective": "physical", "cluster_name": "never-clustered", "verdict": "accepted",
        })
        assert resp.status_code == 200
        assert resp.json()["verdict"] == "accepted"
        assert resp.json()["materialization"]["status"] == "error"

    def test_accepting_with_every_member_already_materialized_is_fully_materialized(
        self, client, registry,
    ):
        registry.upsert_finding("myproj", "architecture_recovery", [{
            "check_name": "component", "label": "manifest",
            "detail": {"name": "web", "slug": "web", "type": "Software Service"},
        }], surveyed_at="2026-08-30T00:00:00", scope_locator="src/web")
        registry.record_materialized_component(
            "repo", "myproj", "src/web",
            "SolutionComponent::repo::myproj::src/web", "guid-web",
        )
        self._seed_cluster(registry, members=["web"])

        with patch("resource_explorer.surveyors.arch_recovery.blueprint_materializer."
                  "BlueprintMaterializer") as MockCls:
            instance = MockCls.return_value
            instance.materialize_blueprint_element.return_value = {
                "status": "materialized", "guid": "bp-guid-1",
                "qualified_name": "SolutionBlueprint::repo::myproj::physical::core-services",
            }
            instance.resolve_member_guids.return_value = ({"web": "guid-web"}, [])
            instance.resolve_child_blueprint_guids.return_value = ({}, [])

            resp = client.post("/api/curate/blueprint-verdicts/repo/myproj", json={
                "perspective": "physical", "cluster_name": "core-services", "verdict": "accepted",
            })

        assert resp.status_code == 200
        materialization = resp.json()["materialization"]
        assert materialization["status"] == "materialized"
        assert materialization["guid"] == "bp-guid-1"
        assert "unmaterialized_members" not in materialization
        assert materialization["enqueued_membership_rows"] == 1

        element_call = instance.materialize_blueprint_element.call_args
        assert element_call.args == ("repo", "myproj", "physical", "core-services")
        assert element_call.kwargs["display_name"] == "core-services"

    def test_partial_member_materialization_reports_unmaterialized_members_and_still_enqueues(
        self, client, registry,
    ):
        """Decision 2: accepting a blueprint does NOT implicitly materialize
        its members. A curator sees exactly which ones still need their own
        accept, and the ones that ARE ready still get attached — partial
        progress, not all-or-nothing."""
        self._seed_cluster(registry, members=["web", "db"])

        with patch("resource_explorer.surveyors.arch_recovery.blueprint_materializer."
                  "BlueprintMaterializer") as MockCls:
            instance = MockCls.return_value
            instance.materialize_blueprint_element.return_value = {
                "status": "materialized", "guid": "bp-guid-2",
                "qualified_name": "SolutionBlueprint::repo::myproj::physical::core-services",
            }
            instance.resolve_member_guids.return_value = ({"web": "guid-web"}, ["db"])
            instance.resolve_child_blueprint_guids.return_value = ({}, [])

            resp = client.post("/api/curate/blueprint-verdicts/repo/myproj", json={
                "perspective": "physical", "cluster_name": "core-services", "verdict": "accepted",
            })

        materialization = resp.json()["materialization"]
        assert materialization["status"] == "partial"
        assert materialization["unmaterialized_members"] == ["db"]
        assert materialization["enqueued_membership_rows"] == 1  # only "web", the ready one

    def test_two_level_cluster_resolves_child_blueprint_by_name(self, client, registry):
        """Decision 1: a parent blueprint attaches only children that already
        have their own materialized SolutionBlueprint."""
        self._seed_cluster(registry, members=[], children=["core-services-sub"],
                          name="core-services")

        with patch("resource_explorer.surveyors.arch_recovery.blueprint_materializer."
                  "BlueprintMaterializer") as MockCls:
            instance = MockCls.return_value
            instance.materialize_blueprint_element.return_value = {
                "status": "materialized", "guid": "bp-guid-parent",
                "qualified_name": "SolutionBlueprint::repo::myproj::physical::core-services",
            }
            instance.resolve_member_guids.return_value = ({}, [])
            instance.resolve_child_blueprint_guids.return_value = ({"core-services-sub": "bp-guid-child"}, [])

            resp = client.post("/api/curate/blueprint-verdicts/repo/myproj", json={
                "perspective": "physical", "cluster_name": "core-services", "verdict": "accepted",
            })

        assert resp.json()["materialization"]["enqueued_membership_rows"] == 1
        child_call = instance.resolve_child_blueprint_guids.call_args
        assert child_call.args[-1] == ["core-services-sub"]

    def test_oversized_flag_is_passed_through_to_the_materializer(self, client, registry):
        self._seed_cluster(registry, members=["web"], oversized=True)

        with patch("resource_explorer.surveyors.arch_recovery.blueprint_materializer."
                  "BlueprintMaterializer") as MockCls:
            instance = MockCls.return_value
            instance.materialize_blueprint_element.return_value = {
                "status": "materialized", "guid": "bp-guid-3", "qualified_name": "x",
            }
            instance.resolve_member_guids.return_value = ({}, ["web"])
            instance.resolve_child_blueprint_guids.return_value = ({}, [])

            client.post("/api/curate/blueprint-verdicts/repo/myproj", json={
                "perspective": "physical", "cluster_name": "core-services", "verdict": "accepted",
            })

        assert instance.materialize_blueprint_element.call_args.kwargs["oversized"] is True

    def test_egeria_unreachable_still_saves_the_verdict(self, client, registry):
        """Same non-fatal-but-visible contract as the component route: the
        verdict is real regardless of whether Egeria could be reached just
        now."""
        self._seed_cluster(registry, members=["web"])
        from resource_explorer.surveyors.arch_recovery.blueprint_materializer import (
            BlueprintMaterializationError,
        )
        with patch("resource_explorer.surveyors.arch_recovery.blueprint_materializer."
                  "BlueprintMaterializer") as MockCls:
            MockCls.return_value.materialize_blueprint_element.side_effect = \
                BlueprintMaterializationError("Could not connect to Egeria")
            resp = client.post("/api/curate/blueprint-verdicts/repo/myproj", json={
                "perspective": "physical", "cluster_name": "core-services", "verdict": "accepted",
            })

        assert resp.status_code == 200
        assert resp.json()["verdict"] == "accepted"
        assert "Could not connect to Egeria" in resp.json()["materialization"]["error"]


# ── /api/schedules — per-resource + global overview ─────────────────────────────

class TestSchedulesRouter:
    def test_save_and_get_schedule(self, client):
        resp = client.post("/api/schedules/repo/myproj", json={"analysis_id": "security_scan", "schedule": "weekly"})
        assert resp.status_code == 200
        listed = client.get("/api/schedules/repo/myproj").json()
        assert len(listed) == 1
        assert listed[0]["schedule"] == "weekly"

    def test_save_schedule_rejects_invalid_cadence(self, client):
        resp = client.post("/api/schedules/repo/myproj", json={"analysis_id": "security_scan", "schedule": "hourly"})
        assert resp.status_code == 422

    def test_list_all_schedules_global(self, client, registry):
        """Both resources are registered first. They were not until 2026-08-28,
        when the route started checking — so this passed on two slugs that had
        never existed, and would have kept passing if the global listing had
        broken for real resources."""
        from resource_explorer.registry import DatabaseEntity, Project

        registry.add(Project(slug="proj-a", display_name="Proj A",
                             github_url="https://github.com/test/proj-a"))
        registry.register_database(DatabaseEntity(
            slug="db-a", display_name="DB A", database_name="db_a",
            db_type="postgres", host="localhost", port=5432))

        r1 = client.post("/api/schedules/repo/proj-a",
                         json={"analysis_id": "security_scan", "schedule": "daily"})
        r2 = client.post("/api/schedules/database/db-a",
                         json={"analysis_id": "schema_inventory", "schedule": "weekly"})
        assert (r1.status_code, r2.status_code) == (200, 200), (r1.text, r2.text)
        resp = client.get("/api/schedules/")
        assert resp.status_code == 200
        slugs = {r["entity_slug"] for r in resp.json()}
        assert slugs == {"proj-a", "db-a"}

    def test_delete_schedule(self, client):
        client.post("/api/schedules/repo/myproj", json={"analysis_id": "security_scan", "schedule": "daily"})
        resp = client.delete("/api/schedules/repo/myproj/security_scan")
        assert resp.status_code == 200
        assert client.get("/api/schedules/repo/myproj").json() == []

    def test_delete_nonexistent_schedule_404s(self, client):
        resp = client.delete("/api/schedules/repo/myproj/nonexistent")
        assert resp.status_code == 404

    def test_global_list_route_not_shadowed_by_per_resource_route(self, client):
        # Regression guard: GET / (0-segment) vs GET /{entity_type}/{slug}
        # (2-segment) are structurally distinct, but worth a guard given this
        # codebase's history of route-declaration-order bugs elsewhere.
        client.post("/api/schedules/repo/myproj", json={"analysis_id": "security_scan", "schedule": "daily"})
        all_resp = client.get("/api/schedules/")
        per_resource_resp = client.get("/api/schedules/repo/myproj")
        assert all_resp.status_code == 200 and per_resource_resp.status_code == 200
        assert len(all_resp.json()) == 1
        assert len(per_resource_resp.json()) == 1


class TestRfaRouter:
    @pytest.fixture(autouse=True)
    def _mock_egeria_sync(self):
        # PATCH /rfas/{id} attempts a real Egeria ToDo sync after its local
        # write (docs/rfa-egeria-todo-followup.md) — mocked here so these
        # ordinary route tests never depend on (or write real ToDo elements
        # into) a live Egeria platform, matching the established
        # EgeriaPublisher-mocking precedent (test_sub_resources_routes.py).
        # rfa_egeria_sync itself is exercised directly, with its own mocked
        # pyegeria clients, in test_rfa_egeria_sync.py.
        with patch("resource_explorer.rfa_egeria_sync.sync_rfa_action") as mock_sync:
            yield mock_sync

    def test_list_rfas_defaults_to_open(self, client, registry):
        _write_rfa_activity_entry(registry)
        resp = client.get("/api/activity/rfas")
        assert resp.status_code == 200
        rfas = resp.json()
        assert len(rfas) == 1
        assert rfas[0]["id"] == "entry-1::0"
        assert rfas[0]["rfa_status"] == "open"
        assert rfas[0]["assignee"] == ""

    def test_ids_are_stable_and_positional(self, client, registry):
        _write_rfa_activity_entry(registry, num_rfas=3, extra_annotations=[
            {"annotation_type": "ClassificationAnnotation", "summary": "not an rfa"},
        ])
        rfas = client.get("/api/activity/rfas").json()
        assert [r["id"] for r in rfas] == ["entry-1::0", "entry-1::1", "entry-1::2"]

    def test_patch_defer_persists_and_overlays_on_relist(self, client, registry):
        _write_rfa_activity_entry(registry)
        resp = client.patch("/api/activity/rfas/entry-1::0", json={
            "status": "deferred", "defer_until": "2026-09-01",
        })
        assert resp.status_code == 200
        assert resp.json()["rfa_status"] == "deferred"

        rfas = client.get("/api/activity/rfas").json()
        assert rfas[0]["rfa_status"] == "deferred"
        assert rfas[0]["defer_until"] == "2026-09-01"
        assert rfas[0]["action_updated_at"]  # timestamp recorded

    def test_patch_reassign_persists_assignee(self, client, registry):
        _write_rfa_activity_entry(registry)
        client.patch("/api/activity/rfas/entry-1::0", json={
            "status": "reassigned", "assignee": "dwolfson",
        })
        rfas = client.get("/api/activity/rfas").json()
        assert rfas[0]["rfa_status"] == "reassigned"
        assert rfas[0]["assignee"] == "dwolfson"

    def test_patch_complete_with_resolution_note(self, client, registry):
        _write_rfa_activity_entry(registry)
        client.patch("/api/activity/rfas/entry-1::0", json={
            "status": "completed", "resolution_note": "Fixed in PR #42",
        })
        rfas = client.get("/api/activity/rfas").json()
        assert rfas[0]["rfa_status"] == "completed"
        assert rfas[0]["resolution_note"] == "Fixed in PR #42"

    def test_patch_reopen_from_completed(self, client, registry):
        _write_rfa_activity_entry(registry)
        client.patch("/api/activity/rfas/entry-1::0", json={"status": "completed"})
        resp = client.patch("/api/activity/rfas/entry-1::0", json={"status": "open"})
        assert resp.status_code == 200
        rfas = client.get("/api/activity/rfas").json()
        assert rfas[0]["rfa_status"] == "open"

    def test_patch_rejects_invalid_status(self, client, registry):
        _write_rfa_activity_entry(registry)
        resp = client.patch("/api/activity/rfas/entry-1::0", json={"status": "bogus"})
        assert resp.status_code == 400

    def test_patch_rejects_malformed_id(self, client, registry):
        resp = client.patch("/api/activity/rfas/not-a-valid-id", json={"status": "completed"})
        assert resp.status_code == 400

    def test_patch_404s_for_unknown_entry(self, client, registry):
        resp = client.patch("/api/activity/rfas/nonexistent-entry::0", json={"status": "completed"})
        assert resp.status_code == 404

    def test_other_rfas_in_same_entry_unaffected(self, client, registry):
        _write_rfa_activity_entry(registry, num_rfas=2)
        client.patch("/api/activity/rfas/entry-1::0", json={"status": "completed"})
        rfas = {r["id"]: r for r in client.get("/api/activity/rfas").json()}
        assert rfas["entry-1::0"]["rfa_status"] == "completed"
        assert rfas["entry-1::1"]["rfa_status"] == "open"


class TestRfaNotesRouter:
    """Real bug fixed 2026-08-16 (found during a live smoke test): the RFA
    drawer's 'Record answer' button never called any backend endpoint at
    all — purely client-side, in-memory, lost on reload. This is the real,
    persisted replacement."""

    @pytest.fixture(autouse=True)
    def _mock_egeria_sync(self):
        # Every notes PATCH attempts sync_rfa_note (a no-op here in
        # practice, since these test RFAs never have egeria_todo_guid set —
        # but mocked anyway, matching the established precedent, rather
        # than relying on that early-return). test_note_and_status_action_
        # coexist also hits the status PATCH route, which attempts
        # sync_rfa_action — see TestRfaRouter's identical fixture.
        with patch("resource_explorer.rfa_egeria_sync.sync_rfa_action"), \
             patch("resource_explorer.rfa_egeria_sync.sync_rfa_note"):
            yield

    def test_defaults_to_empty(self, client, registry):
        _write_rfa_activity_entry(registry)
        rfas = client.get("/api/activity/rfas").json()
        assert rfas[0]["notes"] == ""

    def test_patch_persists_note_and_overlays_on_relist(self, client, registry):
        _write_rfa_activity_entry(registry)
        resp = client.patch("/api/activity/rfas/entry-1::0/notes", json={"notes": "Talked to the maintainer, waiting on their reply."})
        assert resp.status_code == 200
        assert resp.json()["notes"] == "Talked to the maintainer, waiting on their reply."

        rfas = client.get("/api/activity/rfas").json()
        assert rfas[0]["notes"] == "Talked to the maintainer, waiting on their reply."

    def test_note_survives_independent_of_any_status_action(self, client, registry):
        # A note can be the FIRST interaction with an RFA — no prior
        # Defer/Reassign/Complete required.
        _write_rfa_activity_entry(registry)
        client.patch("/api/activity/rfas/entry-1::0/notes", json={"notes": "Just a note, no action taken yet."})
        rfas = client.get("/api/activity/rfas").json()
        assert rfas[0]["rfa_status"] == "open"
        assert rfas[0]["notes"] == "Just a note, no action taken yet."

    def test_note_and_status_action_coexist(self, client, registry):
        _write_rfa_activity_entry(registry)
        client.patch("/api/activity/rfas/entry-1::0", json={"status": "deferred", "defer_until": "2026-09-01"})
        client.patch("/api/activity/rfas/entry-1::0/notes", json={"notes": "Deferred until the next release."})
        rfas = client.get("/api/activity/rfas").json()
        assert rfas[0]["rfa_status"] == "deferred"
        assert rfas[0]["defer_until"] == "2026-09-01"
        assert rfas[0]["notes"] == "Deferred until the next release."

    def test_patch_rejects_malformed_id(self, client, registry):
        resp = client.patch("/api/activity/rfas/not-a-valid-id/notes", json={"notes": "x"})
        assert resp.status_code == 400

    def test_patch_404s_for_unknown_entry(self, client, registry):
        resp = client.patch("/api/activity/rfas/nonexistent-entry::0/notes", json={"notes": "x"})
        assert resp.status_code == 404

    def test_other_rfas_in_same_entry_unaffected(self, client, registry):
        _write_rfa_activity_entry(registry, num_rfas=2)
        client.patch("/api/activity/rfas/entry-1::0/notes", json={"notes": "note on 0"})
        rfas = {r["id"]: r for r in client.get("/api/activity/rfas").json()}
        assert rfas["entry-1::0"]["notes"] == "note on 0"
        assert rfas["entry-1::1"]["notes"] == ""


# ── /api/analyses/{resource_type} + /perspectives + /egeria-status ─────────────

class TestAnalysisCatalogRouter:
    def test_list_analyses_for_database(self, client):
        resp = client.get("/api/analyses/database")
        assert resp.status_code == 200
        ids = {a["id"] for a in resp.json()}
        assert "schema_inventory" in ids

    def test_filters_by_intent(self, client):
        resp = client.get("/api/analyses/database?intent=curate")
        assert resp.status_code == 200
        ids = {a["id"] for a in resp.json()}
        assert ids == {"egeria_db_survey"}

    def test_list_perspectives_route_reachable(self, client):
        # Regression guard: /perspectives must be declared before /{resource_type}
        # or Starlette's declaration-order matching swallows it.
        resp = client.get("/api/analyses/perspectives")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)
        assert "perspectives" not in resp.json()  # i.e. it wasn't routed as resource_type="perspectives"

    def test_egeria_status_route(self, client):
        client.get("/api/analyses/database")  # populate the status this route reflects
        resp = client.get("/api/analyses/database/egeria-status")
        assert resp.status_code == 200
        body = resp.json()
        assert body["resource_type"] == "database"
        assert body["status"] in {"not_applicable", "unavailable", "ok", "unknown"}

    def test_egeria_status_not_applicable_for_unmapped_resource_type(self, client):
        client.get("/api/analyses/repo")
        resp = client.get("/api/analyses/repo/egeria-status")
        assert resp.json()["status"] == "not_applicable"


# ── /api/analyses/annotation-types ─────────────────────────────────────────────

class TestAnnotationTypesRouter:
    def test_list_annotation_types(self, client):
        resp = client.get("/api/analyses/annotation-types")
        assert resp.status_code == 200
        types = resp.json()
        assert len(types) >= 7  # Prepopulated default count
        # Check one of the default types is present
        assert any(t["type"] == "ResourceMeasureAnnotation" for t in types)

    def test_register_and_get_and_update_and_delete_annotation_type(self, client):
        # 1. Register a new type
        new_type = {
            "type": "CustomTestAnnotation",
            "display_name": "Custom Test",
            "description": "Used for testing CRUD",
            "properties": ["field1", "field2"],
            "egeria_type": "CustomEgeriaClass",
            "python_class": "CustomPythonClass"
        }
        res_post = client.post("/api/analyses/annotation-types", json=new_type)
        assert res_post.status_code == 200
        assert res_post.json() == {"status": "success"}

        # 2. Get the registered type
        res_get = client.get("/api/analyses/annotation-types/CustomTestAnnotation")
        assert res_get.status_code == 200
        body = res_get.json()
        assert body["type"] == "CustomTestAnnotation"
        assert body["display_name"] == "Custom Test"
        assert body["properties"] == ["field1", "field2"]

        # 3. Update the type
        updated = {
            "display_name": "Custom Test Updated",
            "description": "Updated description",
            "properties": ["field1", "field2", "field3"],
            "egeria_type": "CustomEgeriaClassUpdated",
            "python_class": "CustomPythonClassUpdated"
        }
        res_put = client.put("/api/analyses/annotation-types/CustomTestAnnotation", json=updated)
        assert res_put.status_code == 200
        assert res_put.json() == {"status": "success"}

        # Verify updates
        res_get_updated = client.get("/api/analyses/annotation-types/CustomTestAnnotation")
        assert res_get_updated.status_code == 200
        body_up = res_get_updated.json()
        assert body_up["display_name"] == "Custom Test Updated"
        assert body_up["properties"] == ["field1", "field2", "field3"]

        # 4. Delete the type
        res_del = client.delete("/api/analyses/annotation-types/CustomTestAnnotation")
        assert res_del.status_code == 200
        assert res_del.json() == {"status": "success"}

        # Verify 404 on get
        res_get_deleted = client.get("/api/analyses/annotation-types/CustomTestAnnotation")
        assert res_get_deleted.status_code == 404

    def test_usage_404s_for_unknown_type(self, client):
        resp = client.get("/api/analyses/annotation-types/NoSuchAnnotation/usage")
        assert resp.status_code == 404

    def test_usage_is_an_honest_lower_bound_not_an_exact_count(self, client):
        """SPEC-ADMIN-THE-FOUR-GAPS.md §4/§0: a delete/rename confirmation
        must say how many annotations reference a type, or say the count is
        unknown — never imply zero. This registers a fresh type nothing has
        published, so `projects_published` is genuinely 0, and pins that the
        route still marks it `exact: False` and says so in `note` rather
        than presenting 0 as "confirmed unused"."""
        new_type = {
            "type": "NeverPublishedAnnotation",
            "display_name": "Never Published",
            "description": "Registered but never recorded as published anywhere.",
        }
        assert client.post("/api/analyses/annotation-types", json=new_type).status_code == 200

        resp = client.get("/api/analyses/annotation-types/NeverPublishedAnnotation/usage")
        assert resp.status_code == 200
        body = resp.json()
        assert body["projects_published"] == 0
        assert body["exact"] is False
        assert "lower bound" in body["note"] or "not" in body["note"].lower()
        assert "0" in body["note"]


# ── /api/analyses/question-catalog/questions ────────────────────────────────
# Route-level wiring tests for the append-only write path — writer-level
# behavior (append works, retire works, an edit-in-place is refused) has its
# own dedicated coverage in tests/test_question_catalog_writer.py. These
# confirm the FastAPI routes call it correctly and translate its errors to
# the right status codes, isolated from the real committed CSV/YAML via the
# same monkeypatch technique that module's own tests use.

class TestQuestionCatalogWriteRoutes:
    @pytest.fixture(autouse=True)
    def _isolated_catalog(self, tmp_path, monkeypatch):
        import csv as csv_mod
        from resource_explorer.surveyors import question_catalog_writer as qcw

        header = ["Question", "Funnel Stage", "Why is this important?", "Rationale/Source",
                  "Answering Analysis", "Answering Mechanism", "Steward", "Purposes",
                  "Catalog History", "Status"]
        row = {"Question": "Is this repository actively maintained?", "Funnel Stage": "Scouting",
               "Why is this important?": "", "Rationale/Source": "", "Answering Analysis": "",
               "Answering Mechanism": "", "Steward": "X", "Purposes": "Select",
               "Catalog History": "", "Status": ""}
        csv_path = tmp_path / "resource_questions.csv"
        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            w = csv_mod.DictWriter(f, fieldnames=header)
            w.writeheader()
            w.writerow(row)

        monkeypatch.setattr(qcw, "_CSV_PATH", csv_path)
        monkeypatch.setattr(qcw, "_YAML_PATH", tmp_path / "question_catalog.yaml")
        monkeypatch.setattr(qcw, "_LOCK_PATH", tmp_path / "resource_questions.csv.lock")

    def test_add_question_route_succeeds(self, client):
        resp = client.post("/api/analyses/question-catalog/questions", json={
            "question": "Does this repository have a license?",
            "stage": "Scouting",
            "perspectives": ["Steward"],
            "purposes": ["Select"],
        })
        assert resp.status_code == 200
        assert resp.json() == {"status": "success"}

    def test_add_question_route_400s_on_duplicate_text(self, client):
        """The route-level proof of the append-only refusal: posting the
        text of an already-existing question is rejected, not upserted —
        editing is not accepted even hitting the route directly."""
        resp = client.post("/api/analyses/question-catalog/questions", json={
            "question": "Is this repository actively maintained?",
        })
        assert resp.status_code == 400
        assert "already exists" in resp.json()["detail"]

    def test_retire_question_route_succeeds(self, client):
        resp = client.post("/api/analyses/question-catalog/questions/retire", json={
            "question": "Is this repository actively maintained?",
        })
        assert resp.status_code == 200
        assert resp.json() == {"status": "success"}

    def test_retire_question_route_404s_on_unknown_question(self, client):
        resp = client.post("/api/analyses/question-catalog/questions/retire", json={
            "question": "This was never asked.",
        })
        assert resp.status_code == 404

    def test_retire_question_route_400s_on_already_retired(self, client):
        first = client.post("/api/analyses/question-catalog/questions/retire", json={
            "question": "Is this repository actively maintained?",
        })
        assert first.status_code == 200
        second = client.post("/api/analyses/question-catalog/questions/retire", json={
            "question": "Is this repository actively maintained?",
        })
        assert second.status_code == 400


class TestEgeriaRules:
    def test_get_dataclass_rules_fallback(self, client):
        import os
        with patch.dict(os.environ, {}, clear=False):
            if "EGERIA_PLATFORM_URL" in os.environ:
                del os.environ["EGERIA_PLATFORM_URL"]
            resp = client.get("/api/egeria/rules/dataclasses")
            assert resp.status_code == 200
            rules = resp.json()
            assert len(rules) == 6
            assert any(r["name"] == "EmailAddress" and r["source"] == "Local Fallback" for r in rules)

    def test_get_dataclass_rules_mocked_egeria(self, client):
        import os
        from unittest import mock
        
        mock_find_response = [
            {
                "properties": {
                    "qualifiedName": "ValidValueDefinition::EmailAddressKeyword::custom_secret",
                    "displayName": "custom_secret",
                    "preferredValue": "custom_secret"
                }
            }
        ]
        
        with mock.patch.dict(os.environ, {
            "EGERIA_PLATFORM_URL": "http://localhost:9443",
            "EGERIA_VIEW_SERVER": "view-server",
            "EGERIA_USER": "steward",
            "EGERIA_USER_PASSWORD": "steward"
        }):
            with mock.patch("pyegeria.omvs.reference_data.ReferenceDataManager") as MockRD, \
                 mock.patch("pyegeria.omvs.data_designer.DataDesigner") as MockDD:
                
                MockRD.return_value.find_valid_value_definitions.return_value = mock_find_response
                MockDD.return_value.get_guid_for_name.return_value = "dummy-guid"
                
                resp = client.get("/api/egeria/rules/dataclasses")
                assert resp.status_code == 200
                rules = resp.json()
                
                # Check email address rules has been fetched dynamically
                email_rule = next(r for r in rules if r["name"] == "EmailAddress")
                assert email_rule["source"] == "Egeria (Active)"
                assert "custom_secret" in email_rule["keywords"]


