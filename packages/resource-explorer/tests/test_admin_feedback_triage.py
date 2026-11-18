"""Admin → Feedback triage: a status filter, a status control with a note, and each item's history.

docs/trellis-feedback-extraction.md §6 step 3 parts C and D. Page items and rated answers carry a trellis-feedback
status (six, Q1); Curate's resource_feedback rows carry one by reference (Q2), and a deleted one that was triaged is
listed as "item gone" until an admin clears its history (Q7).
"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from resource_explorer.web.app import app

STATIC = Path(__file__).resolve().parents[1] / "resource_explorer" / "web" / "static" / "next" / "admin"


@pytest.fixture
def feedback_store(tmp_path):
    from resource_explorer.feedback_store import FeedbackStore
    return FeedbackStore(db_path=str(tmp_path / "triage_feedback.db"))


@pytest.fixture
def client(monkeypatch, feedback_store, tmp_path):
    # Its own registry file: this writes Curate feedback / projects, which must not reach another test even when
    # REGISTRY_DATABASE_URL was set to one file for the whole run.
    monkeypatch.setenv("REGISTRY_DATABASE_URL", f"sqlite:///{tmp_path}/registry.db")
    import resource_explorer.config as _config_module
    _config_module._config = None
    monkeypatch.setattr(
        "resource_explorer.feedback_store.FeedbackStore.__init__",
        lambda self, db_path=None: setattr(self, "__dict__", feedback_store.__dict__) or None,
    )
    return TestClient(app)


@pytest.fixture
def admin_headers(monkeypatch):
    from resource_explorer import config as cfg_mod
    monkeypatch.setattr(cfg_mod.get_config().feedback, "admin_token", "test-token", raising=False)
    return {"X-Admin-Token": "test-token"}


def _seed(store):
    from resource_explorer.feedback_store import FeedbackEntry
    from resource_explorer.registry import ProjectRegistry

    page = store.add(FeedbackEntry(page="/scouting", category="bug", message="broken"))
    other = store.add(FeedbackEntry(page="/curate", category="praise", message="nice"))
    answer_id, _ = store.add_answer(vote=-1, question="what changed?")
    resource = ProjectRegistry().add_resource_feedback("repo", "r1", 4, "quality", "solid")
    return page.id, other.id, answer_id, resource["id"]


class TestTheStatusFilter:
    def test_it_selects_page_and_answer_items_by_their_current_status(self, client, feedback_store, admin_headers):
        page_id, other_id, answer_id, _ = _seed(feedback_store)
        feedback_store.update_triage_status(page_id, "known_issue")
        feedback_store.update_triage_status(answer_id, "known_issue")
        feedback_store.update_triage_status(other_id, "triaged")
        data = client.get("/api/curate/feedback?status=known_issue", headers=admin_headers).json()
        assert {r["id"] for r in data["feedback"]} == {page_id, answer_id}
        assert data["filtered"] is True

    def test_an_untouched_resource_row_is_new_and_the_filter_applies_to_it(self, client, feedback_store,
                                                                            admin_headers):
        _seed(feedback_store)
        new_only = client.get("/api/curate/feedback?status=new", headers=admin_headers).json()["feedback"]
        assert {r["source"] for r in new_only} == {"page", "answer", "resource"}
        [res] = [r for r in new_only if r["source"] == "resource"]
        assert res["triage"]["events"] == 0

    def test_an_unknown_status_is_a_400(self, client, feedback_store, admin_headers):
        resp = client.get("/api/curate/feedback?status=closed", headers=admin_headers)
        assert resp.status_code == 400
        assert "known_issue" in resp.json()["detail"]

    def test_the_vocabulary_is_served_in_order(self, client, feedback_store, admin_headers):
        data = client.get("/api/curate/feedback", headers=admin_headers).json()
        assert data["statuses"] == ["new", "triaged", "known_issue", "actioned", "deferred", "not_an_issue"]

    def test_a_status_read_that_came_back_full_says_older_resource_rows_may_be_missing(
            self, client, feedback_store, admin_headers, monkeypatch):
        # The cap is lowered to 2 so three rows overflow it; the real cap is 2000 (#1020 P3).
        from resource_explorer.registry import ProjectRegistry
        monkeypatch.setattr("resource_explorer.web.routes.curate.RESOURCE_STATUS_READ_CAP", 2)
        reg = ProjectRegistry()
        for i in range(3):
            reg.add_resource_feedback("repo", f"r{i}", 3, "quality", f"row {i}")
        full = client.get("/api/curate/feedback?status=new&limit=1", headers=admin_headers).json()
        assert full["resource_limited"] is True and full["resource_read"] == 2

    def test_a_status_read_that_was_not_cut_short_is_not_limited(self, client, feedback_store, admin_headers,
                                                                  monkeypatch):
        monkeypatch.setattr("resource_explorer.web.routes.curate.RESOURCE_STATUS_READ_CAP", 2)
        _seed(feedback_store)                                    # one resource row: under the cap
        data = client.get("/api/curate/feedback?status=new&limit=1", headers=admin_headers).json()
        assert data["resource_limited"] is False and data["resource_read"] == 1

    def test_resource_read_counts_the_rows_read_not_the_rows_the_filter_kept(self, client, feedback_store,
                                                                              admin_headers, monkeypatch):
        monkeypatch.setattr("resource_explorer.web.routes.curate.RESOURCE_STATUS_READ_CAP", 5)
        _seed(feedback_store)                                    # one resource row, status new
        data = client.get("/api/curate/feedback?status=known_issue", headers=admin_headers).json()
        assert data["resource_limited"] is False and data["resource_read"] == 1
        assert not [r for r in data["feedback"] if r["source"] == "resource"]

    def test_without_a_status_the_listing_is_never_limited(self, client, feedback_store, admin_headers):
        # An unfiltered read is ordinary paging by `limit`, not an over-read that can miss matches.
        _seed(feedback_store)
        data = client.get("/api/curate/feedback?limit=1", headers=admin_headers).json()
        assert data["resource_limited"] is False

    def test_a_change_the_library_refuses_is_a_400_with_its_words(self, client, feedback_store, admin_headers,
                                                                  monkeypatch):
        # From trellis-feedback 0.3 a status can contradict the item's current cause; the library says so with
        # FeedbackError, and the row must show that sentence, not "HTTP 500" (read-back of #1018).
        from trellis_feedback import FeedbackError
        page_id, _, _, _ = _seed(feedback_store)

        def refuse(*a, **kw):
            raise FeedbackError("status 'not_an_issue' contradicts the item's current cause 'analysis_defect': "
                                "re-diagnose it with this change")
        monkeypatch.setattr(feedback_store.store, "triage", refuse)
        resp = client.patch(f"/api/feedback/{page_id}", headers=admin_headers, json={"triage_status": "not_an_issue"})
        assert resp.status_code == 400
        assert "re-diagnose" in resp.json()["detail"]

    def test_a_change_made_through_patch_is_what_the_listing_shows(self, client, feedback_store, admin_headers):
        page_id, _, _, _ = _seed(feedback_store)
        resp = client.patch(f"/api/feedback/{page_id}", headers=admin_headers,
                            json={"triage_status": "deferred", "note": "after the release"})
        assert resp.status_code == 200, resp.text
        [row] = [r for r in client.get("/api/curate/feedback", headers=admin_headers).json()["feedback"]
                 if r.get("id") == page_id]
        assert row["triage_status"] == "deferred"
        assert (row["triage"]["note"], row["triage"]["actor"], row["triage"]["events"]) == (
            "after the release", "admin-token", 1)


def _feedback_js() -> str:
    return (STATIC / "feedback.js").read_text()


class TestThePage:
    """Source checks on admin/feedback.js: the control saves through the triage route and says when it did not."""

    def test_each_page_and_answer_row_has_a_status_control_with_a_note(self):
        js = _feedback_js()
        assert "triageHtml(r, statuses, causes, chain)" in js
        assert "data-triage-status" in js and "data-triage-note" in js and "data-triage-save" in js

    def test_save_patches_the_item_by_id_with_status_and_note(self):
        js = _feedback_js()
        assert "fetch(url, {\n          method: 'PATCH'" in js
        assert "? `/api/feedback/resource_feedback/${encodeURIComponent(id)}`" in js
        assert ": `/api/feedback/${encodeURIComponent(id)}`;" in js
        assert "const payload = { triage_status: triageStatus, note };" in js
        assert "JSON.stringify(payload)" in js

    def test_a_refused_save_is_said_on_the_row(self):
        js = _feedback_js()
        assert "error.textContent = `Not saved: ${detail}`;" in js

    def test_the_vocabulary_comes_from_the_server(self):
        js = _feedback_js()
        assert "const statuses = data.statuses || ['new'];" in js
        assert "chip(statusWord(s), 'status', s)" in js

    def test_history_is_read_from_the_item_route(self):
        js = _feedback_js()
        assert "data-triage-history" in js
        assert "(item.history || [])" in js

    def test_every_row_has_the_control_and_gone_rows_can_be_cleared(self):
        js = _feedback_js()
        assert "const status = triageHtml(r, statuses, causes, chain);" in js
        assert "resource_feedback has no triage workflow" not in js
        assert "goneHtml(data.gone || [])" in js and "method: 'DELETE'" in js


class TestCurateFeedbackByReference:
    """Part D: a resource_feedback row's status lives in trellis-feedback under source "resource_feedback"."""

    def test_a_status_change_is_kept_by_reference_and_listed(self, client, feedback_store, admin_headers):
        *_, row_id = _seed(feedback_store)
        resp = client.patch(f"/api/feedback/resource_feedback/{row_id}", headers=admin_headers,
                            json={"triage_status": "known_issue", "note": "seen on two repos"})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert (body["triage_status"], body["message"], body["source"]) == ("known_issue", "solid", "resource")
        [event] = body["history"]
        assert (event["source"], event["ref_id"], event["actor"]) == ("resource_feedback", row_id, "admin-token")
        listed = client.get("/api/curate/feedback?status=known_issue", headers=admin_headers).json()["feedback"]
        assert [(r["source"], r["id"]) for r in listed] == [("resource", row_id)]
        assert listed[0]["triage"]["note"] == "seen on two repos"

    def test_the_row_itself_is_not_changed(self, client, feedback_store, admin_headers):
        from resource_explorer.registry import ProjectRegistry
        *_, row_id = _seed(feedback_store)
        before = ProjectRegistry().get_resource_feedback(row_id)
        client.patch(f"/api/feedback/resource_feedback/{row_id}", headers=admin_headers,
                     json={"triage_status": "actioned"})
        assert ProjectRegistry().get_resource_feedback(row_id) == before

    def test_a_row_that_does_not_exist_cannot_be_triaged(self, client, feedback_store, admin_headers):
        resp = client.patch("/api/feedback/resource_feedback/no-such-row", headers=admin_headers,
                            json={"triage_status": "triaged"})
        assert resp.status_code == 404
        assert feedback_store.resource_triage("no-such-row")["history"] == []

    def test_triage_is_admin_only(self, client, feedback_store, admin_headers):
        *_, row_id = _seed(feedback_store)
        assert client.patch(f"/api/feedback/resource_feedback/{row_id}",
                            json={"triage_status": "triaged"}).status_code == 403
        assert client.get(f"/api/feedback/resource_feedback/{row_id}").status_code == 403
        assert client.delete(f"/api/feedback/resource_feedback/{row_id}").status_code == 403

    def test_a_deleted_row_is_listed_as_gone_with_its_history_until_cleared(self, client, feedback_store,
                                                                             admin_headers):
        """Q7: deleting the row (here, directly; RE's Curate tab has no delete yet) does not reach into the
        feedback store. The history stays, shown as "item gone", until an admin clears it."""
        from resource_explorer.registry import ProjectRegistry
        *_, row_id = _seed(feedback_store)
        client.patch(f"/api/feedback/resource_feedback/{row_id}", headers=admin_headers,
                     json={"triage_status": "deferred", "note": "later"})
        reg = ProjectRegistry()
        with reg._conn() as conn:
            conn.execute("DELETE FROM resource_feedback WHERE id = ?", (row_id,))
        data = client.get("/api/curate/feedback", headers=admin_headers).json()
        assert row_id not in {str(r.get("id")) for r in data["feedback"]}
        [gone] = data["gone"]
        assert (gone["ref_id"], gone["triage"]["status"], gone["triage"]["note"]) == (row_id, "deferred", "later")
        one = client.get(f"/api/feedback/resource_feedback/{row_id}", headers=admin_headers).json()
        assert one["gone"] is True and len(one["history"]) == 1
        cleared = client.delete(f"/api/feedback/resource_feedback/{row_id}", headers=admin_headers)
        assert cleared.json() == {"cleared": 1}
        assert client.get("/api/curate/feedback", headers=admin_headers).json()["gone"] == []
        assert client.get(f"/api/feedback/resource_feedback/{row_id}", headers=admin_headers).status_code == 404

    def test_an_existing_rows_history_cannot_be_cleared(self, client, feedback_store, admin_headers):
        *_, row_id = _seed(feedback_store)
        client.patch(f"/api/feedback/resource_feedback/{row_id}", headers=admin_headers,
                     json={"triage_status": "triaged"})
        assert client.delete(f"/api/feedback/resource_feedback/{row_id}", headers=admin_headers).status_code == 409
        assert len(feedback_store.resource_triage(row_id)["history"]) == 1
