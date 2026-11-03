"""Cancelling queued outbox work, safely.

Written after 2026-09-01, when a pre-fix Committed Secrets run queued ~48,500
Egeria writes and there was no supported way to stop them.
`purge_outbox_completed()` handles 'done' rows only — deliberately, since
"failed/pending rows are live work" — so cancelling meant a hand-written
DELETE against live data.

The reason `preview_outbox_cancel` exists as its own method rather than as
advice: the first statement written by hand matched **39,603 of 48,113** rows,
because two buggy runs were queued rather than one. Printing the count first
is the only reason 8,510 bogus annotations were not left to publish on the
next restart.
"""
from __future__ import annotations

import pytest

from resource_explorer.registry import ProjectRegistry

# Brief I: these tests drive the drain mechanics, as the background loop does: a declared daemon job.
pytestmark = pytest.mark.usefixtures("as_daemon")


@pytest.fixture
def registry(tmp_path):
    return ProjectRegistry(db_path=str(tmp_path / "t.db"))


def _enqueue(registry, slug, run_id, n, kind="annotation", status="pending"):
    with registry._conn() as conn:
        for i in range(n):
            conn.execute(
                "INSERT INTO egeria_outbox (entity_type, entity_slug, run_id, "
                "element_kind, qualified_name, payload_json, status, attempts, "
                "next_attempt_at, created_at) "
                "VALUES ('repo', ?, ?, ?, ?, '{}', ?, 0, '', '2026-09-01T00:00:00')",
                (slug, run_id, kind, f"QN::{slug}::{run_id}::{i}", status),
            )


class TestPreview:
    def test_it_reports_what_survives_not_only_what_matches(self, registry):
        """"matched" alone cannot tell you whether the selection is the whole
        problem or part of it — which is exactly how 8,510 rows were nearly
        left behind."""
        _enqueue(registry, "p", "run-bad-1", 5)
        _enqueue(registry, "p", "run-bad-2", 3)

        pre = registry.preview_outbox_cancel(run_id="run-bad-1")
        assert pre["matched"] == 5
        assert pre["total_cancellable"] == 8
        assert pre["surviving"] == 3, "the 3 unmatched rows must be visible"

    def test_the_per_run_breakdown_exposes_a_second_batch(self, registry):
        """The actual 2026-09-01 shape: filtering on one repo matches two
        runs, and only the breakdown shows it."""
        _enqueue(registry, "p", "run-bad-1", 5)
        _enqueue(registry, "p", "run-bad-2", 3)

        assert registry.preview_outbox_cancel(entity_slug="p")["by_run"] == {
            "run-bad-1": 5, "run-bad-2": 3}

    def test_preview_changes_nothing(self, registry):
        _enqueue(registry, "p", "r", 4)
        registry.preview_outbox_cancel(entity_slug="p")
        assert registry.preview_outbox_cancel(entity_slug="p")["matched"] == 4


class TestCancel:
    def test_rows_are_cancelled_not_deleted(self, registry):
        """A deleted row cannot answer "why did 48,000 annotations never
        reach Egeria"."""
        _enqueue(registry, "p", "r", 3)
        assert registry.cancel_outbox_batch(run_id="r", reason="bad scan") == 3

        with registry._conn() as conn:
            rows = conn.execute(
                "SELECT status, last_error FROM egeria_outbox").fetchall()
        assert len(rows) == 3, "rows must survive as a record"
        assert all(r["status"] == "cancelled" for r in rows)
        assert all("bad scan" in r["last_error"] for r in rows)

    def test_a_cancelled_row_is_not_claimed_for_draining(self, registry):
        """The point of the whole exercise. Known-negative: use a status the
        claim query accepts and this fails."""
        _enqueue(registry, "p", "r", 3)
        registry.cancel_outbox_batch(run_id="r")
        assert registry.claim_due_outbox_elements(limit=50) == []

    def test_done_rows_are_never_cancelled(self, registry):
        """They already reached Egeria; cancelling one would misrepresent
        what happened."""
        _enqueue(registry, "p", "r", 2, status="done")
        _enqueue(registry, "p", "r", 3, status="pending")
        assert registry.cancel_outbox_batch(run_id="r") == 3

        with registry._conn() as conn:
            done = conn.execute(
                "SELECT COUNT(*) AS n FROM egeria_outbox WHERE status='done'"
            ).fetchone()["n"]
        assert done == 2

    def test_an_unfiltered_call_is_refused(self, registry):
        """It would cancel every queued row in the system, which is never
        what anyone means."""
        _enqueue(registry, "p", "r", 3)
        with pytest.raises(ValueError, match="at least one filter"):
            registry.cancel_outbox_batch()
        assert registry.preview_outbox_cancel(entity_slug="p")["matched"] == 3

    def test_filters_compose(self, registry):
        _enqueue(registry, "p", "r", 4, kind="annotation")
        _enqueue(registry, "p", "r", 2, kind="annotation_link")
        assert registry.cancel_outbox_batch(
            entity_slug="p", element_kind="annotation_link") == 2

    def test_preview_and_cancel_cannot_disagree(self, registry):
        """They share one predicate builder. If they ever diverged, the count
        a human approved would not be the rows that changed — which is the
        whole failure this is meant to prevent."""
        _enqueue(registry, "p", "r1", 4)
        _enqueue(registry, "q", "r2", 6)
        for kwargs in ({"run_id": "r1"}, {"entity_slug": "q"},
                       {"element_kind": "annotation"},
                       {"created_before": "2026-09-02T00:00:00"}):
            reg = ProjectRegistry(db_path=str(registry.db_path))
            expected = reg.preview_outbox_cancel(**kwargs)["matched"]
            assert reg.cancel_outbox_batch(**kwargs) == expected, kwargs
            # restore for the next filter
            with reg._conn() as conn:
                conn.execute("UPDATE egeria_outbox SET status='pending'")
