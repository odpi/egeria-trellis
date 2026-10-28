"""The Publish Queue API — the detailed record behind an activity-log summary.

The activity log is an audit trail, one line per operation. It says *that* a
publish was incomplete; these routes say which elements, their qualifiedNames,
how many attempts each took and what Egeria actually said.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from resource_explorer.registry import Project, ProjectRegistry

# Brief I: these tests drive the drain mechanics, as the background loop does: a declared daemon job.
pytestmark = pytest.mark.usefixtures("as_daemon")


@pytest.fixture()
def client(tmp_path, monkeypatch):
    db_url = f"sqlite:///{tmp_path/'t.db'}"
    monkeypatch.setenv("REGISTRY_DATABASE_URL", db_url)

    reg = ProjectRegistry(database_url=db_url)
    reg._init_schema()
    reg.add(Project(slug="p", display_name="p", github_url="https://github.com/o/p"))

    import resource_explorer.web.routes.outbox as outbox_routes
    # Retry is admin only; these tests are about the queue, so they act as an admin. TestRetryIsAdminOnly turns it off.
    monkeypatch.setattr("resource_explorer.web.admin_auth.is_admin_request", lambda *a, **k: True)
    monkeypatch.setattr("resource_explorer.auth.get_current_user", lambda *a, **k: {"sub": "dan"})
    monkeypatch.setattr(outbox_routes, "ProjectRegistry",
                        lambda *a, **k: ProjectRegistry(database_url=db_url))

    from resource_explorer.web.app import app
    return TestClient(app), reg


class TestListing:
    def test_it_returns_rows_counts_and_the_attempt_ceiling(self, client):
        c, reg = client
        reg.enqueue_outbox_element("repo", "p", "annotation", "Q::0", {"class": "X"}, run_id="r1")

        body = c.get("/api/outbox/").json()
        assert body["counts"] == {"pending": 1}
        assert len(body["rows"]) == 1
        assert body["rows"][0]["qualified_name"] == "Q::0"
        # The panel renders "attempts/N"; N has to come from the same constant
        # the retry policy uses, not a number copied into the frontend.
        assert body["max_attempts"] == ProjectRegistry.OUTBOX_MAX_ATTEMPTS

    def test_counts_cover_the_whole_table_not_the_filtered_page(self, client):
        c, reg = client
        dead = reg.enqueue_outbox_element("repo", "p", "annotation", "Q::0", {}, run_id="r1")
        reg.enqueue_outbox_element("repo", "p", "annotation", "Q::1", {}, run_id="r2")
        reg.mark_outbox_failed(dead, "x", max_attempts=1)

        body = c.get("/api/outbox/?status=pending").json()
        assert [r["qualified_name"] for r in body["rows"]] == ["Q::1"]
        assert body["counts"] == {"pending": 1, "dead": 1}, (
            "a panel filtered to pending must still report the dead row, or the "
            "filter silently becomes a claim that nothing is stuck"
        )

    def test_it_filters_by_run_so_an_activity_entry_can_link_to_its_own_publish(self, client):
        c, reg = client
        reg.enqueue_outbox_element("repo", "p", "annotation", "A::0", {}, run_id="run-a")
        reg.enqueue_outbox_element("repo", "p", "annotation", "B::0", {}, run_id="run-b")

        rows = c.get("/api/outbox/?run_id=run-b").json()["rows"]
        assert [r["qualified_name"] for r in rows] == ["B::0"]

    def test_an_unknown_status_is_rejected_rather_than_ignored(self, client):
        # Silently returning the unfiltered table would read as "nothing is
        # stuck" precisely when something is.
        c, _ = client
        res = c.get("/api/outbox/?status=nonsense")
        assert res.status_code == 400
        assert "nonsense" in res.json()["detail"]


class TestRetry:
    def test_a_dead_element_can_be_re_queued(self, client):
        c, reg = client
        row = reg.enqueue_outbox_element("repo", "p", "annotation", "Q::0", {}, run_id="r")
        reg.mark_outbox_failed(row, "permission denied", max_attempts=1)
        assert reg.outbox_counts() == {"dead": 1}

        assert c.post(f"/api/outbox/{row}/retry").status_code == 200
        assert reg.outbox_counts() == {"pending": 1}
        assert reg.claim_due_outbox_elements()[0]["attempts"] == 0

    def test_re_queueing_a_backing_off_element_is_refused(self, client):
        # "Retry" on a row that is merely waiting would discard its backoff.
        c, reg = client
        row = reg.enqueue_outbox_element("repo", "p", "annotation", "Q::0", {}, run_id="r")
        reg.mark_outbox_failed(row, "transient", max_attempts=9)

        res = c.post(f"/api/outbox/{row}/retry")
        assert res.status_code == 409
        assert "not dead" in res.json()["detail"]

    def test_an_unknown_element_is_404_not_409(self, client):
        c, _ = client
        assert c.post("/api/outbox/999999/retry").status_code == 404


class TestPurge:
    def test_it_removes_only_completed_rows(self, client):
        c, reg = client
        done = reg.enqueue_outbox_element("repo", "p", "annotation", "Q::0", {}, run_id="r")
        dead = reg.enqueue_outbox_element("repo", "p", "annotation", "Q::1", {}, run_id="r")
        reg.mark_outbox_done(done, "g")
        reg.mark_outbox_failed(dead, "x", max_attempts=1)
        with reg._conn() as conn:
            conn.execute("UPDATE egeria_outbox SET completed_at=? WHERE id=?",
                         ("2020-01-01T00:00:00", done))

        assert c.post("/api/outbox/purge?older_than_days=1").json()["removed"] == 1
        assert reg.outbox_counts() == {"dead": 1}


class TestRetryNeverResendsADestructiveWrite:
    """PI-131: the Publish Queue's Retry must not re-send an archive or delete."""

    @pytest.mark.parametrize("kind", [
        "catalogue_schema_leave_out", "doc_source_unpublish", "some_future_archive_kind",
    ])
    def test_a_dead_destructive_row_is_refused_and_stays_dead(self, client, kind):
        c, reg = client
        row = reg.enqueue_outbox_element("repo", "p", kind, "Q::0", {}, run_id="r")
        reg.mark_outbox_failed(row, "not retried: destructive write", max_attempts=1)
        assert reg.outbox_counts() == {"dead": 1}

        res = c.post(f"/api/outbox/{row}/retry")

        assert res.status_code == 409
        assert "destructive" in res.json()["detail"]
        assert reg.outbox_counts() == {"dead": 1}, "the refused retry must leave the row dead"
        assert reg.claim_due_outbox_elements() == [], "nothing may be queued for the drain"

    def test_the_listing_marks_destructive_rows_so_no_retry_is_drawn(self, client):
        c, reg = client
        reg.enqueue_outbox_element("repo", "p", "annotation", "Q::0", {}, run_id="r")
        reg.enqueue_outbox_element("repo", "p", "doc_source_unpublish", "Q::1", {}, run_id="r")
        by_kind = {r["element_kind"]: r["destructive"] for r in c.get("/api/outbox/").json()["rows"]}
        assert by_kind == {"annotation": False, "doc_source_unpublish": True}


class TestRetryIsAdminOnly:
    def test_a_non_admin_cannot_requeue_and_the_row_stays_dead(self, client, monkeypatch):
        c, reg = client
        row = reg.enqueue_outbox_element("repo", "p", "annotation", "Q::0", {}, run_id="r")
        reg.mark_outbox_failed(row, "x", max_attempts=1)
        monkeypatch.setattr("resource_explorer.web.admin_auth.is_admin_request", lambda *a, **k: False)
        monkeypatch.setattr("resource_explorer.web.admin_auth.admin_configured", lambda *a, **k: True)
        res = c.post(f"/api/outbox/{row}/retry")
        assert res.status_code == 403
        assert reg.outbox_counts() == {"dead": 1}


class TestRefusedConfidenceIsNotRetried:
    """re/membership-confidence: a membership RE refused to send ("not sent: confidence …") would be refused again
    on a retry; it needs the value corrected first. A "did not keep" row keeps its Retry."""

    def _dead(self, reg, words):
        row = reg.enqueue_outbox_element("work_list", "w", "collection_membership", f"CollectionMembership::c::{words[:8]}",
                                         {"collection_guid": "c", "member_guid": "m"}, run_id="r")
        reg.mark_outbox_failed(row, words, max_attempts=1)
        return row

    def test_the_listing_marks_it_and_the_retry_is_refused(self, client):
        c, reg = client
        row = self._dead(reg, "not sent: confidence 'high' is not a number")
        listed = {r["id"]: r for r in c.get("/api/outbox/").json()["rows"]}
        assert "correct it on the work list" in listed[row]["not_retryable"]
        res = c.post(f"/api/outbox/{row}/retry")
        assert res.status_code == 409 and "correct it on the work list" in res.json()["detail"]
        assert reg.outbox_counts() == {"dead": 1}

    def test_a_property_not_kept_row_keeps_its_retry(self, client):
        c, reg = client
        row = self._dead(reg, "Egeria accepted the membership but did not keep: confidence (sent 80, read back 0)")
        listed = {r["id"]: r for r in c.get("/api/outbox/").json()["rows"]}
        assert listed[row]["not_retryable"] == ""
        assert c.post(f"/api/outbox/{row}/retry").status_code == 200
