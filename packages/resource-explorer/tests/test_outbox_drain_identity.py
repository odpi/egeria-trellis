"""Who the outbox drain calls Egeria as.

2026-10-07: seven `collection_membership` rows (#69837-#69843) failed on every
drain with `User not authorized received for user - ``` and were read as "the
call went out for the empty user". That reading was wrong: pyegeria renders the
user from `additional_info["userid"]`, and its Egeria-wrapped 401/403 branch
never sets one, so EVERY such error shows an empty user whoever was sent. What
the drain did leave open was *which* identity it used: it inherited whatever the
ContextVar held, and nothing stopped a blank service identity going out.

Fake clients and fake credentials only. Nothing here reaches Egeria.
"""
from __future__ import annotations

import logging
import threading
from datetime import datetime, timedelta

import pytest

import resource_explorer.egeria_outbox as outbox
from resource_explorer.egeria_identity import EgeriaIdentity, use_identity
from resource_explorer.egeria_outbox import (
    DESTRUCTIVE_OUTBOX_KINDS,
    NOT_RETRIED,
    OutboxClients,
    OutboxIdentityError,
    drain_outbox,
)
from resource_explorer.registry import ProjectRegistry

SERVICE = EgeriaIdentity(user_id="svc-fake", password="pw-fake", is_service_account=True)
BLANK = EgeriaIdentity(user_id="", password="pw-fake", is_service_account=True)
PERSON = EgeriaIdentity(user_id="person-fake", token="token-fake")


@pytest.fixture()
def db(tmp_path):
    reg = ProjectRegistry(database_url=f"sqlite:///{tmp_path/'t.db'}")
    reg._init_schema()
    return reg


class _Calls:
    """What the fake Egeria saw, as (user the client was built for, collection, member)."""

    def __init__(self):
        self.members: list[tuple[str, str, str]] = []
        self.publishers: list[object] = []


@pytest.fixture()
def calls(monkeypatch):
    seen = _Calls()

    class FakeCollectionManager:
        def __init__(self, user):
            self.user = user

        def add_to_collection(self, collection_guid, member_guid, body=None):
            seen.members.append((self.user, collection_guid, member_guid))

    class FakePublisher:
        def __init__(self, *args, identity=None, **kwargs):
            seen.publishers.append(self)
            self._identity = identity
            self.user_id = (identity.user_id if identity is not None else "")
            self._discovery = self._metadata_expert = None
            self._collection_manager = None

        def resolve_identity(self):
            from resource_explorer.egeria_identity import caller_credentials
            return self._identity or caller_credentials()

        def _connect(self):
            ident = self.resolve_identity()
            self.user_id = ident.user_id
            self._collection_manager = FakeCollectionManager(ident.user_id)

        def _find_element_guid(self, qn):
            return ""

    monkeypatch.setattr("resource_explorer.surveyors.egeria_publisher.EgeriaPublisher", FakePublisher)
    monkeypatch.setattr("resource_explorer.egeria_clients._daemon_credential", lambda: ("svc-fake", "pw-fake"))
    return seen


def _loop():
    """What the background drain loop passes (Brief I round 2): the daemon, explicitly. An
    inline drain inside a person's request runs as that person instead (see
    tests/test_egeria_identity_e2e.py)."""
    from resource_explorer.egeria_clients import Daemon, DaemonReason

    return Daemon(DaemonReason.OUTBOX)


def _enqueue_members(db, n=7):
    return [db.enqueue_outbox_element(
        "repo", "egeria_git", "collection_membership", f"CollectionMembership::coll::m{i}",
        {"collection_guid": "coll", "member_guid": f"m{i}"}) for i in range(n)]


def _in_plain_thread(fn):
    out: dict = {}

    def run():
        try:
            out["value"] = fn()
        except BaseException as exc:  # noqa: BLE001 - re-raised on the test thread below
            out["error"] = exc

    t = threading.Thread(target=run)
    t.start()
    t.join(30)
    if "error" in out:
        raise out["error"]
    return out["value"]


class TestDrainCallsAsTheServiceIdentity:
    def test_seven_members_succeed_from_a_background_thread_as_the_service_identity(self, db, calls):
        ids = _enqueue_members(db)
        summary = _in_plain_thread(lambda: drain_outbox(db, identity=_loop()))
        assert summary["done"] == 7 and summary["failed"] == 0
        assert {u for u, _, _ in calls.members} == {"svc-fake"}, "no call may carry '' or anyone else"
        assert sorted(m for _, _, m in calls.members) == sorted(f"m{i}" for i in range(7))
        assert db.outbox_counts() == {"done": len(ids)}

    def test_the_loops_daemon_is_not_displaced_by_a_persons_context(self, db, calls):
        # The background loop names the daemon explicitly, so a person's context in the
        # same thread cannot turn it into a person's drain.
        _enqueue_members(db, 2)
        with use_identity(PERSON):
            summary = drain_outbox(db, identity=_loop())
        assert summary["done"] == 2
        assert {u for u, _, _ in calls.members} == {"svc-fake"}

    def test_a_row_already_failed_succeeds_on_the_next_drain_with_no_migration(self, db, calls):
        (row_id,) = _enqueue_members(db, 1)
        db.claim_due_outbox_elements()
        db.mark_outbox_failed(row_id, "PyegeriaUnauthorizedException: old failure")
        with db._conn() as conn:  # the backoff has long passed
            conn.execute("UPDATE egeria_outbox SET next_attempt_at=? WHERE id=?",
                         ((datetime.utcnow() - timedelta(hours=1)).isoformat(), row_id))
        summary = drain_outbox(db, identity=_loop())
        assert summary["done"] == 1
        assert db.outbox_counts() == {"done": 1}


class TestABlankIdentityFailsLoudly:
    def test_it_is_a_configuration_error_not_an_egeria_call(self, monkeypatch, calls):
        monkeypatch.setattr("resource_explorer.egeria_clients._daemon_credential", lambda: ("", "pw-fake"))
        with pytest.raises(OutboxIdentityError, match="EGERIA_USER_ID"):
            outbox._default_clients(_loop())
        assert calls.members == [] and calls.publishers == []

    def test_rows_stay_pending_unburnt_with_the_sentence_on_each_row(self, db, calls, monkeypatch, caplog):
        monkeypatch.setattr("resource_explorer.egeria_clients._daemon_credential", lambda: ("", "pw-fake"))
        ids = _enqueue_members(db, 3)
        with caplog.at_level(logging.ERROR):
            summary = drain_outbox(db, identity=_loop())
        assert summary["config_error"] and "EGERIA_USER_ID" in summary["config_error"]
        assert summary["done"] == 0 and summary["failed"] == 0 and summary["dead"] == 0
        assert calls.members == []
        assert any(r.levelno == logging.ERROR and "EGERIA_USER_ID" in r.getMessage() for r in caplog.records)
        rows = {r["id"]: r for r in db.peek_due_outbox_elements()}
        assert set(rows) == set(ids)
        for r in rows.values():
            assert r["status"] == "pending" and r["attempts"] == 0
            assert "EGERIA_USER_ID" in r["last_error"]

    def test_fixing_the_configuration_lets_the_same_rows_through(self, db, calls, monkeypatch):
        monkeypatch.setattr("resource_explorer.egeria_clients._daemon_credential", lambda: ("", "pw-fake"))
        _enqueue_members(db, 2)
        drain_outbox(db, identity=_loop())
        monkeypatch.setattr("resource_explorer.egeria_clients._daemon_credential", lambda: ("svc-fake", "pw-fake"))
        assert drain_outbox(db, identity=_loop())["done"] == 2

    def test_a_destructive_row_is_not_attempted_and_not_burnt_either(self, db, calls, monkeypatch):
        monkeypatch.setattr("resource_explorer.egeria_clients._daemon_credential", lambda: ("", "pw-fake"))
        rid = db.enqueue_outbox_element("repo", "p", "doc_source_unpublish", "Unpub::1", {})
        summary = drain_outbox(db, identity=_loop())
        assert summary["config_error"]
        (row,) = db.peek_due_outbox_elements()
        assert row["id"] == rid and row["attempts"] == 0 and row["status"] == "pending"


class TestDestructiveKindsStillNeverRetry:
    def test_the_set_is_unchanged(self):
        assert DESTRUCTIVE_OUTBOX_KINDS == frozenset(
            {"catalogue_schema_leave_out", "doc_source_unpublish",
             # Brief T (2026-10-10): a tag's AttachedTag unlink and a Folio membership removal
             "informal_tag_detach", "group_folio_membership_detach",
             # 2026-10-12: a member removed from a work list, detached from its WorkingSet on re-publish
             "collection_membership_detach"})

    def test_a_failed_destructive_write_is_terminal_and_labelled(self, db, calls, monkeypatch):
        def refuse(clients, payload):
            raise RuntimeError("egeria said no")

        monkeypatch.setitem(outbox._CREATORS, "doc_source_unpublish", refuse)
        db.enqueue_outbox_element("repo", "p", "doc_source_unpublish", "Unpub::1", {})
        summary = drain_outbox(db, OutboxClients(acting_as="svc-fake"), lambda qn: "", identity=_loop())
        assert summary.get("not_retried") == 1
        (dead,) = db.list_dead_outbox_elements()
        assert dead["last_error"].startswith(NOT_RETRIED)
        assert db.peek_due_outbox_elements() == []


class TestAnEgeriaRefusalNamesWhoWasSent:
    def test_the_row_says_which_identity_was_refused(self, db):
        # pyegeria's own text ends "for user - ``" for this response shape; that is
        # its rendering gap, not evidence that no user was sent.
        class PyegeriaUnauthorizedException(Exception):
            pass

        class Refusing:
            def add_to_collection(self, *a, **k):
                raise PyegeriaUnauthorizedException("User not authorized received for user - ``.")

        (rid,) = _enqueue_members(db, 1)
        summary = drain_outbox(db, OutboxClients(collection_manager=Refusing(), acting_as="svc-fake"),
                               lambda qn: "", identity=_loop())
        assert summary["failed"] == 1
        with db._conn() as conn:
            err = conn.execute("SELECT last_error FROM egeria_outbox WHERE id=?", (rid,)).fetchone()["last_error"]
        assert "svc-fake" in err and "empty user" in err
