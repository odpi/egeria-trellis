"""Tests for /api/feedback routes — public submit, admin-only list/stats/triage.

Includes an explicit regression test that admin routes deny access when no
admin credential is configured at all (fail closed) — this directly targets
the fail-OPEN bug found in the Portal source this feature was ported from,
which is deliberately not carried over. See resource_explorer/web/admin_auth.py.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from resource_explorer.config import FeedbackConfig
from resource_explorer.feedback_store import FeedbackStore

ADMIN_TOKEN = "test-admin-token"


@pytest.fixture
def feedback_store(tmp_path):
    return FeedbackStore(db_path=str(tmp_path / "test_feedback.db"))


@pytest.fixture
def client(feedback_store, monkeypatch):
    # Route handlers instantiate FeedbackStore() with no args; redirect every
    # instance to the fixture's already-initialized tmp_path-backed store.
    monkeypatch.setattr(
        "resource_explorer.feedback_store.FeedbackStore.__init__",
        lambda self, db_path=None: setattr(self, "__dict__", feedback_store.__dict__) or None,
    )
    fake_config = SimpleNamespace(feedback=FeedbackConfig(admin_token=ADMIN_TOKEN))
    monkeypatch.setattr("resource_explorer.web.routes.feedback.get_config", lambda: fake_config)

    from resource_explorer.web.app import app
    return TestClient(app)


@pytest.fixture
def client_no_admin_configured(feedback_store, monkeypatch):
    """Same as `client`, but with no admin_token/admin_users configured at all."""
    monkeypatch.setattr(
        "resource_explorer.feedback_store.FeedbackStore.__init__",
        lambda self, db_path=None: setattr(self, "__dict__", feedback_store.__dict__) or None,
    )
    fake_config = SimpleNamespace(feedback=FeedbackConfig())
    monkeypatch.setattr("resource_explorer.web.routes.feedback.get_config", lambda: fake_config)

    from resource_explorer.web.app import app
    return TestClient(app)


class TestSubmit:
    def test_public_submit_succeeds_with_no_headers(self, client):
        resp = client.post("/api/feedback", json={"rating": 5, "message": "nice"})
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}

    def test_submit_rejects_out_of_range_rating(self, client):
        resp = client.post("/api/feedback", json={"rating": 9})
        assert resp.status_code == 400

    def test_submit_rejects_invalid_category(self, client):
        resp = client.post("/api/feedback", json={"category": "not-a-real-category"})
        assert resp.status_code == 400


class TestAdminGating:
    def test_list_with_no_admin_headers_is_denied(self, client):
        resp = client.get("/api/feedback")
        assert resp.status_code == 403

    def test_list_with_correct_token_succeeds(self, client):
        client.post("/api/feedback", json={"rating": 4, "message": "hello"})
        resp = client.get("/api/feedback", headers={"X-Admin-Token": ADMIN_TOKEN})
        assert resp.status_code == 200
        rows = resp.json()
        assert len(rows) == 1
        assert rows[0]["message"] == "hello"

    def test_list_with_wrong_token_is_denied(self, client):
        resp = client.get("/api/feedback", headers={"X-Admin-Token": "wrong"})
        assert resp.status_code == 403

    def test_stats_admin_gated(self, client):
        assert client.get("/api/feedback/stats").status_code == 403
        resp = client.get("/api/feedback/stats", headers={"X-Admin-Token": ADMIN_TOKEN})
        assert resp.status_code == 200
        assert resp.json()["total"] == 0

    def test_patch_admin_gated(self, client):
        client.post("/api/feedback", json={"message": "x"})
        [row] = client.get("/api/feedback", headers={"X-Admin-Token": ADMIN_TOKEN}).json()

        denied = client.patch(f"/api/feedback/{row['id']}", json={"triage_status": "triaged"})
        assert denied.status_code == 403

        allowed = client.patch(
            f"/api/feedback/{row['id']}",
            json={"triage_status": "triaged"},
            headers={"X-Admin-Token": ADMIN_TOKEN},
        )
        assert allowed.status_code == 200
        assert allowed.json()["triage_status"] == "triaged"

    def test_fail_closed_when_no_admin_configured_at_all(self, client_no_admin_configured):
        """Regression test: absence of FEEDBACK_ADMIN_TOKEN/FEEDBACK_ADMIN_USERS
        must deny admin access, not allow it. The Portal source this was ported
        from had the opposite (fail-open) behavior in exactly this situation."""
        c = client_no_admin_configured
        assert c.get("/api/feedback").status_code == 403
        assert c.get("/api/feedback/stats").status_code == 403
        # Even presenting *some* token must still fail — nothing is configured to match.
        assert c.get("/api/feedback", headers={"X-Admin-Token": "anything"}).status_code == 403


class TestTriageThroughTheLibrary:
    def test_a_change_records_its_note_and_who_made_it(self, client):
        client.post("/api/feedback", json={"message": "x"})
        h = {"X-Admin-Token": ADMIN_TOKEN}
        [row] = client.get("/api/feedback", headers=h).json()
        resp = client.patch(f"/api/feedback/{row['id']}",
                            json={"triage_status": "known_issue", "note": "  dup  "}, headers=h)
        assert resp.status_code == 200
        [event] = resp.json()["history"]
        assert (event["status"], event["note"], event["actor"]) == ("known_issue", "dup", "admin-token")

    def test_one_item_with_its_history_is_admin_only(self, client):
        client.post("/api/feedback", json={"message": "x"})
        h = {"X-Admin-Token": ADMIN_TOKEN}
        [row] = client.get("/api/feedback", headers=h).json()
        assert client.get(f"/api/feedback/{row['id']}").status_code == 403
        got = client.get(f"/api/feedback/{row['id']}", headers=h)
        assert got.status_code == 200 and got.json()["history"] == []
        assert client.get("/api/feedback/nope", headers=h).status_code == 404

    def test_an_empty_submission_is_a_400_not_a_500(self, client):
        assert client.post("/api/feedback", json={}).status_code == 400

    def test_an_unknown_status_is_refused(self, client):
        client.post("/api/feedback", json={"message": "x"})
        h = {"X-Admin-Token": ADMIN_TOKEN}
        [row] = client.get("/api/feedback", headers=h).json()
        assert client.patch(f"/api/feedback/{row['id']}", json={"triage_status": "closed"},
                            headers=h).status_code == 400
