"""Tests for FeedbackStore — insert, list, filter, stats, triage update, and the move of the legacy
`feedback` table into trellis-feedback (docs/trellis-feedback-extraction.md §6 step 3)."""
from __future__ import annotations

import sqlite3

import pytest
from trellis_feedback import FeedbackError

from resource_explorer.feedback_store import (
    MIGRATION_ACTOR,
    VALID_TRIAGE_STATUSES,
    FeedbackEntry,
    FeedbackStore,
)


@pytest.fixture
def store(tmp_path):
    return FeedbackStore(db_path=str(tmp_path / "test_feedback.db"))


class TestAddAndList:
    def test_add_and_list(self, store):
        entry = store.add(FeedbackEntry(page="nav-chat", rating=5, message="great tool"))
        rows = store.list()
        assert len(rows) == 1
        assert rows[0]["id"] == entry.id
        assert rows[0]["page"] == "nav-chat"
        assert rows[0]["rating"] == 5
        assert rows[0]["message"] == "great tool"
        assert rows[0]["triage_status"] == "new"

    def test_list_empty(self, store):
        assert store.list() == []

    def test_list_filters_by_triage_status(self, store):
        a = store.add(FeedbackEntry(message="a"))
        store.add(FeedbackEntry(message="b"))
        store.update_triage_status(a.id, "triaged")

        triaged = store.list(triage_status="triaged")
        new_only = store.list(triage_status="new")
        assert len(triaged) == 1
        assert triaged[0]["id"] == a.id
        assert len(new_only) == 1

    def test_list_respects_limit(self, store):
        for i in range(5):
            store.add(FeedbackEntry(message=f"msg-{i}"))
        assert len(store.list(limit=2)) == 2

    def test_list_newest_first(self, store):
        first = store.add(FeedbackEntry(message="first"))
        second = store.add(FeedbackEntry(message="second"))
        rows = store.list()
        assert rows[0]["id"] == second.id
        assert rows[1]["id"] == first.id


class TestStats:
    def test_stats_empty(self, store):
        stats = store.stats()
        assert stats == {"total": 0, "new": 0, "wants_response": 0, "avg_rating": None}

    def test_stats_counts_and_average(self, store):
        store.add(FeedbackEntry(rating=4))
        store.add(FeedbackEntry(rating=2))
        store.add(FeedbackEntry(rating=None, message="call me", wants_response=True))
        stats = store.stats()
        assert stats["total"] == 3
        assert stats["new"] == 3
        assert stats["wants_response"] == 1
        assert stats["avg_rating"] == 3.0


class TestTriageUpdate:
    def test_update_triage_status(self, store):
        entry = store.add(FeedbackEntry(message="x"))
        updated = store.update_triage_status(entry.id, "actioned")
        assert updated is not None
        assert updated["triage_status"] == "actioned"

    def test_update_missing_returns_none(self, store):
        assert store.update_triage_status("does-not-exist", "triaged") is None

    def test_update_invalid_status_raises(self, store):
        entry = store.add(FeedbackEntry(message="x"))
        with pytest.raises(ValueError):
            store.update_triage_status(entry.id, "bogus")


class TestTheLibrarysWorkflow:
    def test_all_six_statuses_are_accepted(self, store):
        assert VALID_TRIAGE_STATUSES == {"new", "triaged", "known_issue", "actioned", "deferred", "not_an_issue"}
        entry = store.add(FeedbackEntry(message="x"))
        for status in sorted(VALID_TRIAGE_STATUSES):
            assert store.update_triage_status(entry.id, status)["triage_status"] == status

    def test_each_change_keeps_its_note_and_actor(self, store):
        entry = store.add(FeedbackEntry(message="x"))
        store.update_triage_status(entry.id, "triaged", note="looking", actor="erinoverview")
        store.update_triage_status(entry.id, "known_issue", note="dup of #12", actor="peterprofile")
        got = store.get(entry.id)
        assert got["triage_status"] == "known_issue"
        assert [(e["status"], e["note"], e["actor"]) for e in got["history"]] == [
            ("triaged", "looking", "erinoverview"), ("known_issue", "dup of #12", "peterprofile")]

    def test_a_submission_with_nothing_said_is_refused(self, store):
        with pytest.raises(FeedbackError):
            store.add(FeedbackEntry(wants_response=True))
        assert store.list() == []

    def test_an_unknown_category_filter_matches_nothing(self, store):
        store.add(FeedbackEntry(category="bug", message="x"))
        assert store.list(category="nonexistent-category") == []


LEGACY_SCHEMA = """
    CREATE TABLE feedback (
        id TEXT PRIMARY KEY, session_id TEXT DEFAULT '', page TEXT DEFAULT '',
        element_guid TEXT DEFAULT '', rating INTEGER, category TEXT DEFAULT '',
        message TEXT DEFAULT '', email TEXT DEFAULT '', wants_response INTEGER DEFAULT 0,
        consent_to_contact INTEGER DEFAULT 0, build_version TEXT DEFAULT '',
        user_agent TEXT DEFAULT '', viewport TEXT DEFAULT '', locale TEXT DEFAULT '',
        triage_status TEXT DEFAULT 'new', created_at TEXT NOT NULL
    )
"""


def _legacy_db(path, rows):
    """A database as RE left it before the move: only the legacy `feedback` table, as the old
    store created it."""
    conn = sqlite3.connect(path)
    conn.execute(LEGACY_SCHEMA)
    conn.executemany(
        "INSERT INTO feedback (id, page, rating, category, message, wants_response, triage_status, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?)", rows)
    conn.commit()
    conn.close()


class TestLegacyMigration:
    ROWS = (
        ("11111111-1111-4111-8111-111111111111", "/scouting", 5, "praise", "lovely", 0, "new",
         "2026-08-01T10:00:00.000001"),
        ("22222222-2222-4222-8222-222222222222", "/curate", None, "bug", "broken", 1, "triaged",
         "2026-09-01T10:00:00.000001"),
        ("33333333-3333-4333-8333-333333333333", "/admin", 2, "confusing", "huh", 0, "actioned",
         "2026-10-01T10:00:00.000001"),
    )

    def _open(self, path):
        from resource_explorer import feedback_store as fs
        fs._stores.pop(f"sqlite:///{path}", None)   # a new process, as far as the copy can tell
        return FeedbackStore(db_path=str(path))

    def test_legacy_rows_arrive_with_their_ids_times_and_statuses(self, tmp_path):
        path = tmp_path / "legacy.db"
        _legacy_db(path, self.ROWS)
        rows = {r["id"]: r for r in self._open(path).list()}
        assert set(rows) == {r[0] for r in self.ROWS}
        old = rows["22222222-2222-4222-8222-222222222222"]
        assert (old["page"], old["category"], old["message"], old["wants_response"]) == ("/curate", "bug", "broken", True)
        assert old["created_at"] == "2026-09-01T10:00:00.000001+00:00"
        assert {i: r["triage_status"] for i, r in rows.items()} == {r[0]: r[6] for r in self.ROWS}
        assert old["triage"]["actor"] == MIGRATION_ACTOR

    def test_a_second_run_writes_nothing(self, tmp_path):
        path = tmp_path / "legacy.db"
        _legacy_db(path, self.ROWS)
        first = self._open(path)
        before = [(r["id"], r["triage"]["events"]) for r in first.list()]
        second = self._open(path)
        assert [(r["id"], r["triage"]["events"]) for r in second.list()] == before
        assert second.stats()["total"] == 3
        # A triage change made after the copy survives a restart unchanged.
        second.update_triage_status(self.ROWS[0][0], "deferred", actor="erinoverview")
        third = self._open(path)
        assert third.get(self.ROWS[0][0])["triage_status"] == "deferred"
        assert len(third.get(self.ROWS[1][0])["history"]) == 1

    def test_legacy_and_new_feedback_list_newest_first_together(self, tmp_path):
        path = tmp_path / "legacy.db"
        _legacy_db(path, self.ROWS)
        store = self._open(path)
        store.add(FeedbackEntry(message="today"))
        assert [r["message"] for r in store.list()] == ["today", "huh", "broken", "lovely"]

    def test_the_legacy_table_is_left_in_place(self, tmp_path):
        path = tmp_path / "legacy.db"
        _legacy_db(path, self.ROWS)
        self._open(path)
        conn = sqlite3.connect(path)
        assert conn.execute("SELECT COUNT(*) FROM feedback").fetchone()[0] == 3
        conn.close()

    def test_a_row_the_library_refuses_is_left_behind_not_fatal(self, tmp_path):
        path = tmp_path / "legacy.db"
        _legacy_db(path, [*self.ROWS,
                         ("44444444-4444-4444-8444-444444444444", "/x", None, "", "", 0, "new", "2026-10-02T00:00:00")])
        store = self._open(path)
        assert len(store.list()) == 3

    def test_a_database_with_no_legacy_table_is_fine(self, tmp_path):
        assert self._open(tmp_path / "fresh.db").list() == []

    def test_an_unknown_legacy_status_is_copied_as_new_and_counted(self, tmp_path):
        from resource_explorer import feedback_store as fs
        path = tmp_path / "legacy.db"
        _legacy_db(path, [("55555555-5555-4555-8555-555555555555", "/x", None, "bug", "odd", 0, "closed",
                           "2026-10-02T00:00:00")])
        fs._stores.pop(f"sqlite:///{path}", None)
        lib = fs._LibraryStore(f"sqlite:///{path}", fs.APP, external_sources=fs.EXTERNAL_SOURCES)
        counts = fs.migrate_legacy_feedback(lib)
        assert (counts["imported"], counts["unknown_status"], counts["events"]) == (1, 1, 0)
        assert lib.state("55555555-5555-4555-8555-555555555555").status == "new"
        lib.engine.dispose()

    def test_a_database_error_on_one_row_is_that_row_refused_not_a_broken_store(self, tmp_path, monkeypatch, caplog):
        from sqlalchemy.exc import DataError

        from resource_explorer import feedback_store as fs
        path = tmp_path / "legacy.db"
        _legacy_db(path, self.ROWS)
        bad = self.ROWS[1][0]
        real = fs._LibraryStore.import_item

        def flaky(self_, item, **kw):
            if item.id == bad:
                raise DataError("INSERT ...", {"message": "broken", "email": "a@b.c"},
                                Exception("value out of range\nDETAIL:  Failing row contains (a@b.c, broken)."))
            return real(self_, item, **kw)
        monkeypatch.setattr(fs._LibraryStore, "import_item", flaky)
        with caplog.at_level("WARNING", logger="resource_explorer.feedback_store"):
            store = self._open(path)
        assert {r["id"] for r in store.list()} == {self.ROWS[0][0], self.ROWS[2][0]}
        assert "value out of range" in caplog.text
        assert "a@b.c" not in caplog.text and "broken" not in caplog.text
        assert f"sqlite:///{path}" in fs._stores      # cached: later requests do not re-run the copy

    def test_every_legacy_column_is_carried_into_the_item(self, tmp_path):
        """Real reports: nothing the old table held may be dropped on the way across."""
        path = tmp_path / "legacy.db"
        conn = sqlite3.connect(path)
        conn.execute(LEGACY_SCHEMA)
        full = {
            "id": "66666666-6666-4666-8666-666666666666", "session_id": "sess-1", "page": "/curate/repo/x",
            "element_guid": "answer:x:Q-0001", "rating": 4, "category": "suggestion", "message": "make it bigger",
            "email": "someone@example.org", "wants_response": 1, "consent_to_contact": 1,
            "build_version": "0.9.1", "user_agent": "Mozilla/5.0 (X11; Linux x86_64)", "viewport": "1920x1080",
            "locale": "en-GB", "triage_status": "triaged", "created_at": "2026-08-11T09:30:00.123456",
        }
        conn.execute(f"INSERT INTO feedback ({', '.join(full)}) VALUES ({', '.join('?' * len(full))})",
                     list(full.values()))
        conn.commit()
        conn.close()
        got = self._open(path).get(full["id"])
        for col in ("session_id", "page", "element_guid", "rating", "category", "message", "email",
                    "build_version", "user_agent", "viewport", "locale"):
            assert got[col] == full[col], col
        assert got["wants_response"] is True and got["consent_to_contact"] is True
        assert got["created_at"] == "2026-08-11T09:30:00.123456+00:00"
        assert got["triage_status"] == "triaged" and got["kind"] == "product"

    def test_empty_category_or_user_agent_is_still_copied(self, tmp_path):
        path = tmp_path / "legacy.db"
        _legacy_db(path, [("77777777-7777-4777-8777-777777777777", "/x", None, "", "a message, no category",
                           0, "new", "2026-10-02T00:00:00")])
        got = self._open(path).list()
        assert [(r["message"], r["category"], r["user_agent"]) for r in got] == [("a message, no category", "", "")]
