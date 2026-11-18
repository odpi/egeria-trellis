"""The store: items, idempotency, append-only triage, deletion, and external references.

Decisions Q1-Q7 are docs/trellis-feedback-extraction.md §7.
"""
from __future__ import annotations

import threading

import pytest
from trellis_feedback import STATUSES, FeedbackError, FeedbackItem, NotFound


def answer(**kw) -> FeedbackItem:
    base = {"app": "ignored", "kind": "answer", "vote": -1, "query_text": "what changed?", "response_text": "it did"}
    base.update(kw)
    return FeedbackItem(**base)


def product(**kw) -> FeedbackItem:
    base = {"app": "ignored", "kind": "product", "category": "bug", "message": "the button is wrong"}
    base.update(kw)
    return FeedbackItem(**base)


class TestItems:
    def test_a_submitted_item_gets_an_id_and_the_stores_app(self, store):
        item, created = store.submit(answer(user_id="peterprofile"))
        assert created and item.id and item.created_at
        assert item.app == "resource-explorer"
        got = store.get(item.id)
        assert got.query_text == "what changed?" and got.user_id == "peterprofile" and got.vote == -1

    def test_the_full_answer_text_is_kept(self, store):
        # Q4: always the full answer, whatever the vote -- including a positive one.
        long = "x" * 50_000
        item, _ = store.submit(answer(vote=1, response_text=long))
        assert store.get(item.id).response_text == long

    @pytest.mark.parametrize("bad, words", [
        ({"kind": "other"}, "kind"),
        ({"vote": 2}, "vote"),
        ({"query_text": ""}, "query_text"),
        ({"rating": 9}, "rating"),
    ])
    def test_a_malformed_answer_is_refused(self, store, bad, words):
        with pytest.raises(FeedbackError, match=words):
            store.submit(answer(**bad))
        assert store.count() == 0

    def test_a_product_item_needs_something_said(self, store):
        with pytest.raises(FeedbackError):
            store.submit(product(category="", message=""))
        with pytest.raises(FeedbackError, match="category"):
            store.submit(product(category="rant"))

    def test_extra_round_trips(self, store):
        item, _ = store.submit(product(extra={"route": "/admin", "n": 3}))
        assert store.get(item.id).extra == {"route": "/admin", "n": 3}

    def test_two_apps_on_one_database_do_not_see_each_other(self, make_store):
        re_store = make_store("resource-explorer")
        ea_store = make_store("egeria-advisor")
        a, _ = re_store.submit(answer())
        ea_store.submit(answer())
        assert re_store.count() == 1 and ea_store.count() == 1
        assert ea_store.get(a.id) is None
        with pytest.raises(NotFound):
            ea_store.triage(a.id, "actioned")


class TestIdempotency:
    def test_a_retried_submit_returns_the_first_item(self, store):
        first, created = store.submit(answer(), idempotency_key="k1")
        again, created_again = store.submit(answer(query_text="different body"), idempotency_key="k1")
        assert created and not created_again
        assert again.id == first.id and again.query_text == "what changed?"
        assert store.count() == 1

    def test_no_key_means_no_deduplication(self, store):
        store.submit(answer())
        store.submit(answer())
        assert store.count() == 2

    def test_another_users_key_never_finds_their_item(self, store):
        """0.1 read-back P2: a key is matched per user. User B sending user A's key gets a new item
        of B's own, never A's question, answer or contact details."""
        a, _ = store.submit(answer(user_id="alice", query_text="alice's question", response_text="secret",
                                   ), idempotency_key="shared")
        b, created = store.submit(answer(user_id="bob", query_text="bob's question"), idempotency_key="shared")
        assert created and b.id != a.id
        assert (b.user_id, b.query_text, b.response_text) == ("bob", "bob's question", "it did")
        # and A's own retry still finds A's item
        again, created_again = store.submit(answer(user_id="alice"), idempotency_key="shared")
        assert not created_again and again.id == a.id

    def test_the_same_key_in_two_apps_is_two_items(self, make_store):
        a, _ = make_store("resource-explorer").submit(answer(), idempotency_key="k")
        b, _ = make_store("egeria-advisor").submit(answer(), idempotency_key="k")
        assert a.id != b.id

    def test_concurrent_retries_with_one_key_store_one_item(self, store):
        results = []

        def go():
            results.append(store.submit(answer(), idempotency_key="race"))

        threads = [threading.Thread(target=go) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert store.count() == 1
        assert len({item.id for item, _ in results}) == 1
        assert sum(1 for _, created in results if created) == 1


class TestTriage:
    def test_an_untouched_item_is_new(self, store):
        item, _ = store.submit(answer())
        state = store.state(item.id)
        assert (state.status, state.events) == ("new", 0)

    def test_the_latest_event_is_the_status_and_every_note_is_kept(self, store):
        item, _ = store.submit(answer())
        store.triage(item.id, "triaged", note="seen", actor="erinoverview")
        store.triage(item.id, "known_issue", note="same as #12", actor="peterprofile")
        state = store.state(item.id)
        assert (state.status, state.note, state.actor, state.events) == ("known_issue", "same as #12", "peterprofile", 2)
        assert [(e.status, e.note) for e in store.history(item.id)] == [("triaged", "seen"), ("known_issue", "same as #12")]

    def test_events_are_ordered_by_sequence_not_time(self, store):
        item, _ = store.submit(answer())
        seqs = [store.triage(item.id, s).seq for s in ("triaged", "deferred", "actioned")]
        assert seqs == sorted(seqs) and len(set(seqs)) == 3
        assert store.state(item.id).status == "actioned"

    def test_all_six_statuses_are_accepted_and_nothing_else(self, store):
        # Q1: EA's five plus RE's `triaged`.
        assert STATUSES == ("new", "triaged", "known_issue", "actioned", "deferred", "not_an_issue")
        item, _ = store.submit(answer())
        for s in STATUSES:
            store.triage(item.id, s)
        with pytest.raises(FeedbackError, match="status"):
            store.triage(item.id, "resolved")

    def test_triage_of_a_missing_item_is_refused(self, store):
        with pytest.raises(NotFound):
            store.triage("no-such-id", "actioned")

    def test_an_undeclared_source_is_refused(self, store):
        with pytest.raises(FeedbackError, match="declared"):
            store.triage("1", "actioned", source="resource_feedbak")

    def test_list_filters_on_the_current_status(self, store):
        a, _ = store.submit(answer(query_text="a"))
        b, _ = store.submit(answer(query_text="b"))
        store.triage(a.id, "actioned")
        store.triage(b.id, "actioned")
        store.triage(b.id, "new")          # reopened
        assert [r["id"] for r in store.list(status="actioned")] == [a.id]
        assert [r["id"] for r in store.list(status="new")] == [b.id]

    def test_a_listed_rows_triage_target_is_always_that_row(self, store):
        """The guard for EA's live bug (§1): its admin view numbered records by their position in a
        filtered list, and triage treated the number as a raw line, so one skipped line made edits
        land on the wrong record. Here the only handle is the item's own id, so for every listed
        row, triaging by that row's id must change that row and no other -- including when odd rows
        (empty optional fields, unicode, a very long body) sit between them."""
        odd = [
            answer(query_text="ordinary"),
            answer(query_text="éè \U0001f44e 中文", response_text=""),
            product(message="", rating=3, category=""),
            answer(query_text="long", response_text="y" * 100_000),
            product(),
        ]
        for it in odd:
            store.submit(it)
        listed = store.list()
        assert len(listed) == len(odd)
        wanted = {}
        for n, row in enumerate(listed):
            status = STATUSES[1 + n % (len(STATUSES) - 1)]
            store.triage(row["id"], status, note=f"row {n}")
            wanted[row["id"]] = (status, f"row {n}")
        for row in store.list():
            assert (row["triage"]["status"], row["triage"]["note"]) == wanted[row["id"]]
            assert row == {**store.get(row["id"]).to_dict(), "triage": row["triage"]}

    def test_a_triage_racing_a_delete_never_leaves_an_orphan_event(self, store):
        """0.1 read-back P3: the existence check and the insert are one statement, so an event either
        lands before the delete (and goes with the item) or is refused. Whatever the interleaving,
        no event may outlive its item."""
        ids = [store.submit(answer(query_text=f"q{n}"))[0].id for n in range(40)]
        errors = []

        def triager():
            for _ in range(3):
                for item_id in ids:
                    try:
                        store.triage(item_id, "triaged")
                    except NotFound:
                        pass
                    except Exception as exc:  # noqa: BLE001 -- collected and asserted on below
                        errors.append(exc)

        def deleter():
            for item_id in ids:
                try:
                    store.delete_item(item_id)
                except Exception as exc:  # noqa: BLE001 -- collected and asserted on below
                    errors.append(exc)

        threads = [threading.Thread(target=triager), threading.Thread(target=deleter)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert not errors
        assert store.count() == 0
        assert store.referenced("item") == []

    def test_count_matching_applies_the_same_filters_as_list(self, store):
        a, _ = store.submit(answer())
        store.submit(product(category="bug"))
        store.submit(product(category="praise", message="nice"))
        store.triage(a.id, "actioned")
        assert store.count() == 3
        assert store.count_matching(kind="product") == 2
        assert store.count_matching(category="praise") == 1
        assert store.count_matching(status="actioned") == 1
        assert store.count_matching(status="new") == 2
        assert store.count_matching(status="new") == len(store.list(status="new"))

    def test_concurrent_triage_and_submits_lose_nothing(self, store):
        a, _ = store.submit(answer(query_text="a"))
        b, _ = store.submit(answer(query_text="b"))
        errors = []

        def triage(item_id, n):
            try:
                for i in range(n):
                    store.triage(item_id, "triaged" if i % 2 else "deferred", note=str(i))
            except Exception as exc:  # noqa: BLE001 -- collected and asserted on below
                errors.append(exc)

        def submit(n):
            try:
                for _ in range(n):
                    store.submit(product())
            except Exception as exc:  # noqa: BLE001 -- collected and asserted on below
                errors.append(exc)

        threads = [threading.Thread(target=triage, args=(a.id, 15)),
                   threading.Thread(target=triage, args=(b.id, 15)),
                   threading.Thread(target=submit, args=(15,))]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert not errors
        assert len(store.history(a.id)) == 15 and len(store.history(b.id)) == 15
        assert store.count(kind="product") == 15


class TestImport:
    """import_item: moving an app's existing feedback in, keeping its id and created_at."""

    LEGACY_ID = "0b6f3c1e-1111-4a2b-9c3d-123456789abc"   # a dashed uuid, 36 characters

    def test_an_imported_item_keeps_its_id_and_time(self, store):
        assert store.import_item(product(id=self.LEGACY_ID, created_at="2026-08-01T10:00:00+00:00"))
        got = store.get(self.LEGACY_ID)
        assert got.created_at == "2026-08-01T10:00:00+00:00"
        assert got.app == "resource-explorer"

    def test_a_second_import_of_the_same_id_writes_nothing(self, store):
        assert store.import_item(product(id=self.LEGACY_ID, created_at="2026-08-01T10:00:00+00:00"))
        assert store.import_item(product(id=self.LEGACY_ID, created_at="2026-08-01T10:00:00+00:00",
                                         message="changed")) is False
        assert store.count() == 1
        assert store.get(self.LEGACY_ID).message == "the button is wrong"

    def test_imported_items_sort_among_submitted_ones_by_their_own_time(self, store):
        store.submit(product(message="today"))
        store.import_item(product(id=self.LEGACY_ID, created_at="2026-08-01T10:00:00+00:00", message="old"))
        assert [r["message"] for r in store.list()] == ["today", "old"]

    def test_an_id_held_by_another_app_is_refused(self, make_store):
        re_store, ea_store = make_store("resource-explorer"), make_store("egeria-advisor")
        re_store.import_item(product(id=self.LEGACY_ID, created_at="2026-08-01T10:00:00+00:00"))
        with pytest.raises(FeedbackError, match="another app"):
            ea_store.import_item(product(id=self.LEGACY_ID, created_at="2026-08-01T10:00:00+00:00"))

    @pytest.mark.parametrize("kw", [{"id": ""}, {"id": "x" * 37}, {"created_at": ""}])
    def test_an_import_needs_an_id_and_a_time(self, store, kw):
        base = {"id": self.LEGACY_ID, "created_at": "2026-08-01T10:00:00+00:00"}
        base.update(kw)
        with pytest.raises(FeedbackError):
            store.import_item(product(**base))
        assert store.count() == 0

    def test_a_database_refusal_that_is_not_a_duplicate_says_what_it_was(self, store, monkeypatch):
        real_row = store._row

        def broken_row(item, key):
            row = real_row(item, key)
            row["kind"] = None          # NOT NULL: an IntegrityError that no other app's id explains
            return row
        monkeypatch.setattr(store, "_row", broken_row)
        with pytest.raises(FeedbackError) as exc:
            store.import_item(product(id=self.LEGACY_ID, created_at="2026-08-01T10:00:00+00:00"))
        assert "another app" not in str(exc.value)
        assert "was not stored" in str(exc.value)
        # Only the database's first line: no row values (Postgres DETAIL) and no SQL parameters.
        assert "the button is wrong" not in str(exc.value)
        assert "DETAIL" not in str(exc.value) and "[parameters" not in str(exc.value)
        assert exc.value.__cause__ is not None

    def test_an_import_is_validated_like_a_submit(self, store):
        with pytest.raises(FeedbackError):
            store.import_item(product(id=self.LEGACY_ID, created_at="2026-08-01T10:00:00+00:00",
                                      category="nonsense"))

    def test_a_status_is_written_with_the_item(self, store):
        assert store.import_item(product(id=self.LEGACY_ID, created_at="2026-08-01T10:00:00+00:00"),
                                 status="triaged", note="carried over", actor="migration")
        [event] = store.history(self.LEGACY_ID)
        assert (event.status, event.note, event.actor) == ("triaged", "carried over", "migration")

    def test_new_or_no_status_writes_no_event(self, store):
        store.import_item(product(id=self.LEGACY_ID, created_at="2026-08-01T10:00:00+00:00"), status="new")
        assert store.history(self.LEGACY_ID) == []

    def test_a_bad_status_writes_nothing_at_all(self, store):
        with pytest.raises(FeedbackError):
            store.import_item(product(id=self.LEGACY_ID, created_at="2026-08-01T10:00:00+00:00"), status="closed")
        assert store.get(self.LEGACY_ID) is None

    def test_a_failed_event_rolls_the_item_back(self, store, monkeypatch):
        def boom(conn, row):
            raise RuntimeError("crash between the item and its event")
        monkeypatch.setattr(store, "_append_event", boom)
        with pytest.raises(RuntimeError):
            store.import_item(product(id=self.LEGACY_ID, created_at="2026-08-01T10:00:00+00:00"), status="triaged")
        monkeypatch.undo()
        assert store.get(self.LEGACY_ID) is None
        # So the next run imports it, status included.
        assert store.import_item(product(id=self.LEGACY_ID, created_at="2026-08-01T10:00:00+00:00"), status="triaged")
        assert store.state(self.LEGACY_ID).status == "triaged"

    def test_racing_imports_of_one_row_write_it_once_with_one_event(self, store):
        results: list[bool] = []
        barrier = threading.Barrier(6)

        def run():
            barrier.wait()
            results.append(store.import_item(
                product(id=self.LEGACY_ID, created_at="2026-08-01T10:00:00+00:00"), status="actioned"))

        threads = [threading.Thread(target=run) for _ in range(6)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert sorted(results) == [False] * 5 + [True]
        assert store.count() == 1
        assert len(store.history(self.LEGACY_ID)) == 1

    def test_an_imported_item_can_be_triaged_and_deleted(self, store):
        store.import_item(product(id=self.LEGACY_ID, created_at="2026-08-01T10:00:00+00:00"))
        store.triage(self.LEGACY_ID, "triaged", actor="migration")
        assert store.state(self.LEGACY_ID).status == "triaged"
        assert store.delete_item(self.LEGACY_ID)
        assert store.history(self.LEGACY_ID) == []


class TestDeletion:
    def test_deleting_an_item_removes_its_events_too(self, store):
        # Q6: deleting removes the item, its text and its triage history.
        item, _ = store.submit(answer())
        store.triage(item.id, "triaged", note="has a note")
        assert store.delete_item(item.id)
        assert store.get(item.id) is None
        assert store.history(item.id) == []
        assert item.id not in store.referenced("item")

    def test_deleting_a_missing_item_says_so(self, store):
        assert store.delete_item("nope") is False

    def test_sequence_numbers_are_not_reused_after_a_delete(self, store):
        a, _ = store.submit(answer())
        last = store.triage(a.id, "triaged").seq
        store.delete_item(a.id)
        b, _ = store.submit(answer())
        assert store.triage(b.id, "triaged").seq > last


class TestExternalReferences:
    def test_an_external_row_is_triaged_by_reference(self, store):
        store.triage("42", "known_issue", source="resource_feedback", note="dup")
        assert store.state("42", source="resource_feedback").status == "known_issue"
        assert store.states(["42", "43"], source="resource_feedback")["43"].status == "new"
        # an external id and a library id are different subjects
        assert store.history("42") == []

    def test_a_row_deleted_outside_the_library_keeps_its_history_as_gone(self, store):
        # Q7: RE deletes the row itself; the events stay, shown as "item gone", until an admin
        # deletes them.
        store.triage("7", "triaged", source="resource_feedback", note="looking")
        store.triage("8", "actioned", source="resource_feedback")
        still_there = {"8"}
        assert store.gone("resource_feedback", lambda ids: [i for i in ids if i in still_there]) == ["7"]
        assert [e.note for e in store.history("7", source="resource_feedback")] == ["looking"]
        assert store.delete_events("7", source="resource_feedback") == 1
        assert store.gone("resource_feedback", lambda ids: [i for i in ids if i in still_there]) == []

    def test_library_items_cannot_be_gone_or_have_events_deleted_separately(self, store):
        item, _ = store.submit(answer())
        store.triage(item.id, "triaged")
        with pytest.raises(FeedbackError):
            store.delete_events(item.id, source="item")
        with pytest.raises(FeedbackError):
            store.gone("item", lambda ids: [])
        assert len(store.history(item.id)) == 1

    def test_a_reserved_or_malformed_source_name_is_refused(self, make_store):
        with pytest.raises(FeedbackError):
            make_store(external_sources=("item",))
        with pytest.raises(FeedbackError):
            make_store(external_sources=("Resource Feedback",))


class TestPostgresLocking:
    """The FOR KEY SHARE that `_append_event` takes on Postgres, pinned without timing (the
    architect's 0.1 read-back). Removing it is otherwise invisible to a test: the window it closes
    is a few statements wide. lock_timeout turns "would block" into an error instead.

    Postgres only. SQLite serialises writers, which covers the same case there."""

    def test_a_delete_waits_for_an_uncommitted_triage_and_then_takes_its_event(self, store):
        if store.is_sqlite:
            pytest.skip("Postgres row locking; SQLite serialises writers")
        from sqlalchemy import text
        from sqlalchemy.exc import OperationalError

        item, _ = store.submit(answer())
        row = {"app": store.app, "source": "item", "ref_id": item.id, "status": "triaged",
               "note": "held open", "actor": "a", "created_at": "2026-11-09T00:00:00+00:00"}
        conn_a = store.engine.connect()
        tx_a = conn_a.begin()
        try:
            store._append_event(conn_a, row)               # A: triage, not yet committed
            # B: a delete of the item row must block on A's FOR KEY SHARE. lock_timeout makes the
            # block an error (SQLSTATE 55P03) rather than a wait; without the lock it would succeed.
            with store.engine.connect() as conn_b:
                with conn_b.begin():
                    conn_b.execute(text("SET LOCAL lock_timeout = '200ms'"))
                    with pytest.raises(OperationalError) as caught:
                        conn_b.execute(store.items.delete().where(store.items.c.id == item.id))
                assert getattr(caught.value.orig, "pgcode", None) == "55P03", caught.value
            tx_a.commit()
        finally:
            conn_a.close()
        # After A commits, delete_item succeeds and takes A's event with it.
        assert [e.note for e in store.history(item.id)] == ["held open"]
        assert store.delete_item(item.id)
        assert store.history(item.id) == [] and store.referenced("item") == []

