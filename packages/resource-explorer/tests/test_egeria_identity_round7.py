"""Brief I round 7 (CI on PR #579): an inline drain that is HANDED its clients needs no ambient
identity — the clients already carry who acts (the investigation publisher passes its own
CollectionManager). Only a drain that must build its own clients refuses without an identity.
Fakes only; nothing reaches Egeria."""
from __future__ import annotations

import pytest

from resource_explorer.egeria_outbox import OutboxClients, drain_outbox
from resource_explorer.registry import ProjectRegistry


@pytest.fixture
def db(tmp_path):
    reg = ProjectRegistry(database_url=f"sqlite:///{tmp_path/'r7.db'}")
    reg._init_schema()
    return reg


class _CM:
    def __init__(self):
        self.added = []

    def add_to_collection(self, collection_guid, member_guid, body=None):
        self.added.append((collection_guid, member_guid))


def _row(db):
    return db.enqueue_outbox_element("repo", "p", "collection_membership", "CollectionMembership::c::m",
                                     {"collection_guid": "c", "member_guid": "m"}, run_id="run-1")


def test_a_drain_handed_its_clients_applies_the_row_without_an_ambient_identity(db):
    cm = _CM()
    _row(db)
    out = drain_outbox(db, OutboxClients(collection_manager=cm), lambda qn: "", limit=1, run_id="run-1")
    assert out["done"] == 1 and cm.added == [("c", "m")]


def test_a_drain_that_must_build_its_own_clients_still_refuses_without_an_identity(db):
    _row(db)
    out = drain_outbox(db)
    assert out["claimed"] == 0 and "sign in" in out["identity_error"]
