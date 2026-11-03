"""Brief T on Postgres: the plan's row read (`list_outbox_rows_for_kinds`), the retirement of an unsent row
(`cancel_outbox_row`) and a full sync, against the throwaway `pg_registry` schema (SKIPPED under PGVECTOR_PORT=1).

The fake stands at the client boundary only, as in tests/test_tags_groups_to_egeria.py."""
from __future__ import annotations

import pytest

from resource_explorer import curation_egeria as ce
from resource_explorer.registry import Project
from tests.test_tags_groups_to_egeria import ASSET, FakeEgeria
from tests.test_outbox_destructive_lease_pg import _clear_outbox

pytestmark = pytest.mark.usefixtures("as_daemon")


@pytest.fixture()
def db(pg_registry):
    with pg_registry._conn() as conn:
        _clear_outbox(conn)
        conn.execute("DELETE FROM resource_tags WHERE entity_slug = 'tgp'")
    if not pg_registry.get("tgp"):
        pg_registry.add(Project(slug="tgp", display_name="tgp", github_url="https://github.com/o/tgp"))
    pg_registry.set_egeria_asset_guid("tgp", ASSET)
    pg_registry.create_group("tgsales", "Sales", "")
    yield pg_registry
    with pg_registry._conn() as conn:
        _clear_outbox(conn)
        conn.execute("DELETE FROM resource_tags WHERE entity_slug = 'tgp'")
    pg_registry.delete_group("tgsales")


@pytest.fixture()
def egeria(monkeypatch):
    from resource_explorer import egeria_outbox as ob

    from tests.zone_fakes import install as install_zones

    install_zones(monkeypatch)       # Brief F round 3: the drain re-checks access; no zone in use here
    fake = FakeEgeria()
    monkeypatch.setattr(ob, "_default_clients",
                        lambda identity=None: (ob.OutboxClients(feedback=fake, collection_manager=fake), lambda qn: ""))
    return fake


def test_a_tag_and_a_group_reach_egeria_and_the_next_read_says_so_on_postgres(db, egeria):
    db.add_resource_tag("repo", "tgp", "sales", author="peterprofile")
    db.set_project_group("tgp", "tgsales")
    out = ce.sync_curation(db, "repo", "tgp", by="peterprofile", check_access=False)
    assert len(out["queued"]) == 2 and out["to_send"] == 0
    st = {(i["kind"], i["name"]): i["state"] for i in out["items"]}
    assert st == {("tag", "sales"): "in_egeria", ("group", "tgsales"): "in_egeria"}
    rows = db.list_outbox_rows_for_kinds("repo", "tgp", ce.KINDS)
    assert [r["element_kind"] for r in rows] == [ce.GROUP_LINK, ce.TAG_LINK], "newest first"
    assert db.list_outbox_rows_for_kinds("repo", "tgp", []) == []


def test_cancel_outbox_row_retires_only_an_unsent_row_on_postgres(db, egeria):
    pending = db.enqueue_outbox_element("repo", "tgp", ce.TAG_LINK, "AttachedTag::x::a", {"tag": "a"})
    done = db.enqueue_outbox_element("repo", "tgp", ce.TAG_LINK, "AttachedTag::x::b", {"tag": "b"})
    db.mark_outbox_done(done, "g")
    assert db.cancel_outbox_row(pending, "superseded") is True
    assert db.cancel_outbox_row(done, "superseded") is False
    rows = {r["id"]: r for r in db.list_outbox_rows_for_kinds("repo", "tgp", ce.KINDS)}
    assert rows[pending]["status"] == "cancelled" and rows[pending]["last_error"] == "cancelled: superseded"
    assert rows[done]["status"] == "done"


def test_r3_delete_group_with_expected_members_on_postgres(db):
    db.set_project_group("tgp", "tgsales")
    with pytest.raises(db.GroupMembersChanged):
        db.delete_group("tgsales", expected_members=set())
    assert db.get("tgp").group_slug == "tgsales"
    assert db.delete_group("tgsales", expected_members={("repo", "tgp")}) == 1
    assert db.get("tgp").group_slug == "" and db.get_group("tgsales") is None
    db.create_group("tgsales", "Sales", "")      # the fixture's teardown deletes it


def test_r3_a_deferral_never_revives_a_dead_row_on_postgres(db):
    rid = db.enqueue_outbox_element("repo", "tgp", ce.TAG_DETACH, "AttachedTag::x::t", {"tag": "t"})
    claimed = db.claim_due_outbox_elements(element_id=rid)[0]["claimed_at"]
    with db._conn() as conn:
        conn.execute("UPDATE egeria_outbox SET status='dead', claimed_at='' WHERE id=?", (rid,))
    db.mark_outbox_deferred(rid, "late", claimed_at=claimed)
    assert db.get_outbox_element(rid)["status"] == "dead"
