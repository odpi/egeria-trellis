"""A leave-out's archive read-back (architect rulings 2026-10-25, after the post-reset re-proof).

Live, 2026-10-25: Egeria archived all 14 planned elements of schema alpha, yet RE said "…widgets::made_on was archived
but the read-back does not show it · not sent · press Publish again", wrote no proof row for the archive and set the
commit to failed.

THE CAUSE (pinned first, below): the read-back finds each element's archived copy with a TYPED starts-with find for
`<qualifiedName>_archivedOn_`, and the type was guessed from the name's template prefix. Every element under a schema
carries the schema's prefix (`PostgreSQL Relational Database Schema::...`), so a column's copy was searched as a
DeployedDatabaseSchema and Egeria's typed find answered "No elements found". `widgets::made_on` was simply the first
element of the walk; its AssociatedAnnotation links played no part. The fix types the find with the walk's own type.

Rulings: (1) a proof row per archive call the moment Egeria accepts it, before any read-back, "archived · read-back
pending", moved to "archived · confirmed" or "archived · read-back failed: <reason>"; (2) after a destructive write
the words are never "not sent" and never "press again", the commit state is "needs read-back", and Refresh re-reads.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import create_autospec

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from fake_egeria_catalogue import FakeEgeria, captured_memento  # noqa: E402
from test_catalogue_commit import (  # noqa: E402,F401  (fixtures and helpers are shared)
    ME, _curation_access_reads, _zones, choose, derived, press, registry, sch, step, tbl,
)
from resource_explorer import catalogue_commit as cc  # noqa: E402
from resource_explorer import catalogue_gateway as gw  # noqa: E402
from resource_explorer import catalogue_scope as cs  # noqa: E402
from resource_explorer.curate_plan import NEEDS_READ_BACK, Curations  # noqa: E402
from resource_explorer.egeria_outbox import NOT_RETRIED  # noqa: E402

pytestmark = pytest.mark.usefixtures("signed_in_caller")

#: alpha as the re-proof database held it: 6 columns, 2 tables, the template's 4-element connection graph, the schema
#: type and the schema = the 14 elements the live plan named.
ALPHA = {"alpha": {"widgets": ["id", "name", "made_on"], "widget_parts": ["id", "widget_id", "part"]}}
SQN = "PostgreSQL Relational Database Schema::host.docker.internal:5442::shop.alpha"


@pytest.fixture
def world(registry, monkeypatch):
    w = {"tree": {"schemas": [sch(n, [tbl(t) for t in tabs]) for n, tabs in ALPHA.items()]}}
    monkeypatch.setattr(cs, "_load_tree", lambda reg, slug: w["tree"])
    monkeypatch.setattr(cs, "_tree_survey_at", lambda reg, slug: "2026-10-25T17:00:00")
    monkeypatch.setattr(cs, "_activity_counters", lambda reg, slug: {})
    monkeypatch.setattr(cs, "_row_sources", lambda reg, slug: {})
    w["registry"] = registry
    return w


@pytest.fixture
def fake():
    return FakeEgeria(ALPHA)


def _catalogued_then_left_out(world, fake):
    """alpha cataloged, a term assignment hanging off a column (so the leave-out archives), then left out."""
    choose(world, "alpha", "catalogue")
    press(world, fake)
    fake.add_term_assignment("widgets::made_on", SQN, n=2)
    choose(world, "alpha", "leave_out")


def _rows(world, kind=cc.P_ELEMENT_ARCHIVED):
    return [p for p in world["registry"].list_catalogue_commit_proofs("db") if p["proof"] == kind]


def _newest_by_element(rows):
    out = {}
    for r in rows:
        out[r["element_guid"]] = r
    return out


def _leave_out_row(world):
    return next(r for r in world["registry"].list_catalogue_outbox_rows("db") if r["element_kind"] == cc.KIND_LEAVE_OUT)


# ── the cause, from the real gateway and the real response shape ─────────────────────────────────────────────────

#: The live column (re-proof 2026-10-25, `REPROOF-POSTRESET-1025-step3-POST-leaveout-failed.json`): its GUID, type and
#: the archived name Egeria gave it. The element is the captured 6.2 Memento answer with those values in it.
LIVE_QN = ("PostgreSQL Relational Database Schema::host.docker.internal:5432::re_reproof_postreset_1025.alpha"
           "::widgets::made_on")
LIVE_GUID = "50efdc5e-2679-4761-8339-98befd433679"
LIVE_ARCHIVED = LIVE_QN + "_archivedOn_Sun Oct 25 17:49:28 GMT 2026"


def _typed_find_gateway():
    """The real gateway on an autospecced MetadataExpert whose find answers as Egeria's TYPED find does: the column's
    Memento for its own type (RelationalColumn), "No elements found" for any other type."""
    from pyegeria import MetadataExpert

    client = create_autospec(MetadataExpert, spec_set=True, instance=True)

    def find(search_string="*", starts_with=True, start_from=0, page_size=0, metadata_element_type=None, body=None,
             **_kw):
        hit = (start_from == 0 and LIVE_ARCHIVED.startswith(search_string) and metadata_element_type == "RelationalColumn")
        return [captured_memento(LIVE_GUID, LIVE_ARCHIVED, "RelationalColumn")] if hit else "No elements found"

    client.find_metadata_elements_with_string.side_effect = find
    g = gw.PyegeriaCatalogueGateway(view_server="qs-view-server", platform_url="https://localhost:9443")
    g._clients = {"MetadataExpert": client}
    return g, client


def test_the_cause_a_columns_archived_copy_searched_by_its_name_prefix_is_typed_as_a_schema_and_never_found():
    g, client = _typed_find_gateway()
    # what the read-back did on 2026-10-25: the type guessed from the prefix is the SCHEMA's
    assert gw.archived_sibling_type(LIVE_QN) == "DeployedDatabaseSchema"
    assert g.archived_siblings(LIVE_QN) == []
    assert client.find_metadata_elements_with_string.call_args.kwargs["metadata_element_type"] == "DeployedDatabaseSchema"
    # the fix: the walk's own type on the find
    sibs = g.archived_siblings(LIVE_QN, type_name="RelationalColumn")
    assert [(s.guid, s.qualified_name, s.date, s.archived) for s in sibs] == [(LIVE_GUID, LIVE_ARCHIVED, "2026-10-25", True)]
    assert client.find_metadata_elements_with_string.call_args.kwargs["body"]["metadataElementTypeName"] == "RelationalColumn"


def test_the_fake_reproduces_the_live_miss_when_the_type_is_left_to_the_guess(world, fake, monkeypatch):
    """FAIL-ON-PURPOSE: the fake's archived-copy find runs the real typed find, so the old call (no type) fails the
    re-proof's way on every member under the schema, and only the schema itself is confirmed."""
    _catalogued_then_left_out(world, fake)
    real = fake.archived_siblings
    monkeypatch.setattr(fake, "archived_siblings", lambda qn, type_name="": real(qn))
    _out, rec = press(world, fake)
    rows = _newest_by_element(_rows(world)).values()
    failed = [r for r in rows if r["detail"]["read_back"] == cc.RB_FAILED]
    assert len(failed) == 13 and {r["detail"]["reason"] for r in failed} == {"no archived copy under its archived name"}
    assert [r["detail"]["type"] for r in rows if r["detail"]["read_back"] == cc.RB_CONFIRMED] == ["DeployedDatabaseSchema"]
    assert all(e["archived"] for e in fake.elements.values() if e["qn"].startswith(SQN))      # Egeria DID archive them
    assert step(rec, "leave_outs")["state"] == NEEDS_READ_BACK


# ── ruling 1: a row per accepted call, before any read-back ──────────────────────────────────────────────────────

def test_each_accepted_archive_has_a_pending_row_before_any_read_back_and_the_read_back_confirms_all_14(world, fake):
    _catalogued_then_left_out(world, fake)
    _out, rec = press(world, fake)
    rows = _rows(world)
    pending = [r for r in rows if r["detail"]["read_back"] == cc.RB_PENDING]
    confirmed = [r for r in rows if r["detail"]["read_back"] == cc.RB_CONFIRMED]
    assert len(pending) == 14 and len(confirmed) == 14
    assert max(r["id"] for r in pending) < min(r["id"] for r in confirmed)       # every pending row first
    assert {r["detail"]["words"] for r in pending} == {"archived · read-back pending"}
    assert {r["detail"]["words"] for r in confirmed} == {"archived · confirmed"}
    assert [r["element_guid"] for r in pending] == [r["element_guid"] for r in confirmed]
    assert {r["detail"]["planned"] for r in rows} == {14}
    by_type = {}
    for r in pending:
        by_type[r["detail"]["type"]] = by_type.get(r["detail"]["type"], 0) + 1
    assert by_type == {"RelationalColumn": 6, "RelationalTable": 2, "Endpoint": 2, "Connection": 1,
                       "VirtualConnection": 1, "RelationalDBSchemaType": 1, "DeployedDatabaseSchema": 1}
    assert len(_rows(world, cc.P_ARCHIVED)) == 1
    assert step(rec, "leave_outs")["state"] == "done"
    assert step(rec, "leave_outs")["detail"] == "1 of 1 archived in Egeria, each with its proof row"
    assert derived(world)["schemas"]["alpha"]["state"] == "archived"


def test_fail_on_purpose_a_port_that_accepts_all_14_then_throws_on_the_read_leaves_14_pending_rows(world, fake, monkeypatch):
    _catalogued_then_left_out(world, fake)
    real_read = fake.read_element
    thrown = []

    def read(qn, *, for_lineage=False):
        if len(fake.deleted_order) == 14 and not thrown:   # every archive accepted: the read-back's first read throws
            thrown.append(qn)
            raise ConnectionError("Egeria did not answer: read timed out")
        return real_read(qn, for_lineage=for_lineage)

    monkeypatch.setattr(fake, "read_element", read)
    _out, rec = press(world, fake)
    rows = _rows(world)
    assert len(rows) == 14 and {r["detail"]["read_back"] for r in rows} == {cc.RB_PENDING}
    assert _rows(world, cc.P_ARCHIVED) == []
    assert len([c for c in fake.calls if c[0] == "delete_element"]) == 14          # each sent once
    ob = _leave_out_row(world)
    assert ob["status"] in ("failed", "dead") and ob["last_error"].startswith(NOT_RETRIED)   # the guard is kept
    # ruling 2: the words and the states, from those rows
    first = rows[0]["detail"]["name"]
    words = step(rec, "leave_outs")["detail"]
    assert words == (f"alpha: archived in Egeria (14 of 14 accepted) · read-back failed on {first}: Egeria did not "
                     "answer: read timed out · do not press again · Refresh re-reads")
    assert "not sent" not in words and "press Publish again" not in words
    assert step(rec, "leave_outs")["state"] == NEEDS_READ_BACK and rec["state"] == NEEDS_READ_BACK
    tree = derived(world)["schemas"]["alpha"]
    assert tree["state"] == "archived" and tree["words"].startswith("archived in Egeria (14 of 14 accepted)")


def test_the_live_miss_words_name_the_element_and_the_reason_and_never_say_not_sent(world, fake, monkeypatch):
    _catalogued_then_left_out(world, fake)
    real = fake.archived_siblings
    # only the made_on column's copy is missed, as the re-proof first reported it
    monkeypatch.setattr(fake, "archived_siblings",
                        lambda qn, type_name="": [] if qn.endswith("::widgets::made_on") else real(qn, type_name=type_name))
    _out, rec = press(world, fake)
    words = step(rec, "leave_outs")["detail"]
    assert words == ("alpha: archived in Egeria (14 of 14 accepted) · read-back failed on widgets::made_on: no archived "
                     "copy under its archived name · do not press again · Refresh re-reads")
    latest = _newest_by_element(_rows(world))
    made_on = next(r for r in latest.values() if r["detail"]["name"] == "widgets::made_on")
    assert made_on["detail"]["words"] == "archived · read-back failed: no archived copy under its archived name"
    assert sum(r["detail"]["read_back"] == cc.RB_CONFIRMED for r in latest.values()) == 13


# ── Refresh re-reads ─────────────────────────────────────────────────────────────────────────────────────────────

def test_refresh_re_reads_a_needs_read_back_archive_and_settles_the_commit_from_the_new_rows(world, fake, monkeypatch):
    _catalogued_then_left_out(world, fake)
    real = fake.archived_siblings
    monkeypatch.setattr(fake, "archived_siblings", lambda qn, type_name="": real(qn))      # the live miss
    _out, rec = press(world, fake)
    assert rec["state"] == NEEDS_READ_BACK
    deletes = len([c for c in fake.calls if c[0] == "delete_element"])
    monkeypatch.setattr(fake, "archived_siblings", real)                                 # the fixed read
    summary = cc.read_back(world["registry"], fake, "db", ["alpha"], by=ME, reread=True)
    assert summary["read_failed"] == 0
    assert len([c for c in fake.calls if c[0] == "delete_element"]) == deletes          # Refresh only reads
    latest = _newest_by_element(_rows(world))
    assert len(latest) == 14 and {r["detail"]["read_back"] for r in latest.values()} == {cc.RB_CONFIRMED}
    archived = _rows(world, cc.P_ARCHIVED)
    assert len(archived) == 1 and archived[0]["element_guid"] == fake.by_qn(next(
        e["qn"] for e in fake.elements.values() if e["type"] == "DeployedDatabaseSchema"))["guid"]
    again = Curations(world["registry"]).get(rec["id"])
    assert step(again, "leave_outs")["state"] == "done"
    assert step(again, "leave_outs")["detail"] == "1 of 1 archived in Egeria, each with its proof row"
    assert again["state"] == "done"
    assert derived(world)["schemas"]["alpha"]["words"].startswith("archived in Egeria ·")


def test_a_refresh_whose_read_throws_changes_no_row_and_counts_a_failed_read(world, fake, monkeypatch):
    _catalogued_then_left_out(world, fake)
    real = fake.archived_siblings
    monkeypatch.setattr(fake, "archived_siblings", lambda qn, type_name="": real(qn))
    _out, rec = press(world, fake)
    before = len(_rows(world))
    monkeypatch.setattr(fake, "archived_siblings", lambda qn, type_name="": (_ for _ in ()).throw(
        gw.SiblingLookupFailed("read timed out")))
    summary = cc.read_back(world["registry"], fake, "db", ["alpha"], by=ME, reread=True)
    assert summary["read_failed"] >= 1 and len(_rows(world)) == before
    assert Curations(world["registry"]).get(rec["id"])["state"] == NEEDS_READ_BACK


def test_the_latest_commit_route_treats_needs_read_back_as_finished():
    from resource_explorer.curate_plan import FINISHED_STATES
    assert NEEDS_READ_BACK in FINISHED_STATES and "failed" in FINISHED_STATES and "done" in FINISHED_STATES
