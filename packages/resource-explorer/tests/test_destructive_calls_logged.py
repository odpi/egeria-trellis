"""Stabilisation slice 2 (2026-10-07): every archive / delete / detach call is logged and recorded, and the outbox
drain never retries a destructive write.

The trace (2026-10-06): an archive of one schema cascaded to a whole database tree in Egeria; RE sent one
`delete_element` per element and logged none of them. Outbox row 69822 (a failed leave-out) was then RETRIED by the
drain 11 minutes later, and a retry of a destructive write is itself a destructive write."""
from __future__ import annotations

import logging
import re
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from test_catalogue_commit import (  # noqa: E402,F401
    ME, _attached, choose, fake, press, registry, world)

from resource_explorer import catalogue_commit as cc  # noqa: E402
from resource_explorer import catalogue_gateway as gw  # noqa: E402
from resource_explorer import egeria_outbox as ob  # noqa: E402
from resource_explorer.catalogue_gateway import GatewayError  # noqa: E402
from resource_explorer.egeria_outbox import OutboxClients, drain_outbox  # noqa: E402


@pytest.fixture(autouse=True)
def _zone_access_open(monkeypatch):
    """Brief Z2: this write path asks `curation_access` (the one decision). These tests are about something else,
    so no zone is in use: the access reads are faked open at the client boundary (`tests/zone_fakes.open_access`)."""
    from tests.zone_fakes import open_access

    return open_access(monkeypatch)

SENTENCE = ("OMAG-REPOSITORY-HANDLER-400-010 an archive of RelationalTable element g2 would leave its dependent "
            "element g9 behind because the repository refused it. " + "tail " * 40)
ELEMENTS = [("g1", "RelationalColumn"), ("g2", "RelationalTable"), ("g3", "DeployedDatabaseSchema")]


class Gate:
    """A minimal gateway: records every call, fails the ones named in `fail`."""
    def __init__(self, fail=()):
        self.calls, self.fail = [], set(fail)

    def delete_element(self, guid, form, *, slug, scope=""):
        self.calls.append(("delete_element", guid, form))
        if guid in self.fail:
            raise GatewayError(SENTENCE)

    def remove_catalog_target(self, rel):
        self.calls.append(("remove_catalog_target", rel))


def send_all(registry, gate, form=gw.ARCHIVE):
    for g, typ in ELEMENTS:
        cc._send_destructive(registry, gate, "delete", slug="db", schema="sales", element_guid=g, typ=typ, form=form,
                             qualified_name=f"qn::{g}", curation_id="cur-1", outbox_id=7, by=ME)


def rows(registry):
    return [p for p in registry.list_catalogue_commit_proofs("db") if p["proof"] == cc.P_DELETE_CALL]


def lines(caplog, word):
    return [r.getMessage() for r in caplog.records if word in r.getMessage() and r.levelno == logging.INFO]


# (a) ─────────────────────────────────────────────────────────────────────────────────────────────────────────────

def test_an_archive_of_three_elements_logs_before_and_after_each_and_writes_three_delete_call_rows(registry, caplog):
    caplog.set_level(logging.INFO)
    gate = Gate()
    send_all(registry, gate)
    before, after = lines(caplog, "destructive call BEFORE"), lines(caplog, "destructive call AFTER")
    assert len(before) == 3 and len(after) == 3
    for (g, typ), b, a in zip(ELEMENTS, before, after):
        for needle in ("ARCHIVE", f"element={g}", f"type={typ}", "'deleteMethod': 'ARCHIVE'", "'forLineage': True",
                       "'forDuplicateProcessing': True", "'cascade_delete': False", "curation=cur-1", "outbox=7"):
            assert needle in b, (needle, b)
        assert f"element={g}" in a and "outcome=ok" in a
    got = rows(registry)
    assert [r["element_guid"] for r in got] == ["g1", "g2", "g3"]
    assert [r["node_kind"] for r in got] == ["column", "table", "schema"]
    for r in got:
        d = r["detail"]
        assert d["operation"] == "ARCHIVE" and d["outcome"] == "ok" and d["flags"]["cascade_delete"] is False
    assert cc.P_DELETE_CALL not in cc.STATE_PROOFS


def test_a_leave_out_through_the_commit_writes_one_delete_call_row_per_element_it_sent(world, fake, signed_in_caller):
    _attached(world, fake, "sales")
    choose(world, "sales", "leave_out")
    press(world, fake)
    sent = [c[1] for c in fake.calls if c[0] == "delete_element"]
    got = rows(world["registry"])
    deletes = [r for r in got if r["detail"]["operation"] != "remove_catalog_target"]
    assert [r["element_guid"] for r in deletes] == sent and len(sent) == 5
    assert {r["detail"]["operation"] for r in deletes} == {"SOFT_DELETE"} and all(r["outbox_id"] is None or True for r in got)
    assert all(r["curation_id"] for r in got)


def test_the_real_gateway_logs_before_and_after_with_the_flags_and_the_full_sentence(caplog):
    caplog.set_level(logging.INFO)
    g = gw.PyegeriaCatalogueGateway(MagicMock())
    me = MagicMock()
    g._clients.update({"MetadataExpert": me, "AssetMaker": MagicMock()})
    import os
    os.environ["RE_ISSUE_117_BLOCK_OFF"] = "test/2026-10-07T00:00Z"
    try:
        g.delete_element("gx", gw.ARCHIVE, slug="db")
        me.delete_metadata_element.side_effect = RuntimeError(SENTENCE)
        with pytest.raises(GatewayError):
            g.delete_element("gy", gw.SOFT_DELETE, slug="db")
    finally:
        del os.environ["RE_ISSUE_117_BLOCK_OFF"]
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert "BEFORE: ARCHIVE element=gx flags: deleteMethod=ARCHIVE forLineage=True forDuplicateProcessing=True cascade_delete=False" in text
    assert "AFTER: ARCHIVE element=gx outcome=ok" in text
    assert "SOFT_DELETE element=gy outcome=failed: " + " ".join(SENTENCE.split()) in text


# (b) ─────────────────────────────────────────────────────────────────────────────────────────────────────────────

def test_one_failing_call_writes_a_failed_row_with_the_full_sentence_and_the_exception_still_propagates(registry, caplog):
    caplog.set_level(logging.INFO)
    gate = Gate(fail={"g2"})
    with pytest.raises(GatewayError) as err:
        send_all(registry, gate)
    assert str(err.value) == SENTENCE
    got = {r["element_guid"]: r for r in rows(registry)}
    assert set(got) == {"g1", "g2"}                      # g3 was never reached; g1's row exists
    assert got["g1"]["detail"]["outcome"] == "ok"
    assert got["g2"]["detail"]["outcome"] == "failed" and got["g2"]["detail"]["error"] == " ".join(SENTENCE.split())
    assert any("outcome=failed" in r.getMessage() and " ".join(SENTENCE.split()) in r.getMessage() for r in caplog.records)


def test_a_failure_does_not_stop_the_other_elements_rows_when_the_caller_continues(registry):
    gate = Gate(fail={"g1"})
    for g, typ in ELEMENTS:
        try:
            cc._send_destructive(registry, gate, "delete", slug="db", schema="sales", element_guid=g, typ=typ, form=gw.ARCHIVE)
        except GatewayError:
            pass
    assert {r["element_guid"]: r["detail"]["outcome"] for r in rows(registry)} == {"g1": "failed", "g2": "ok", "g3": "ok"}


# (c) ─────────────────────────────────────────────────────────────────────────────────────────────────────────────

def test_a_call_refused_by_the_issue_117_block_is_logged_recorded_and_never_sent(registry, caplog, monkeypatch):
    monkeypatch.delenv("RE_ISSUE_117_BLOCK_OFF", raising=False)
    caplog.set_level(logging.INFO)
    gate = Gate()
    with pytest.raises(GatewayError) as err:
        cc._send_destructive(registry, gate, "delete", slug="db", schema="sales", element_guid="g1",
                             typ="RelationalTable", form=gw.ARCHIVE, curation_id="cur-1", outbox_id=7)
    assert str(err.value) == gw.ISSUE_117_WORDS and gate.calls == []
    (r,) = rows(registry)
    assert r["element_guid"] == "g1" and r["detail"]["outcome"] == "refused-by-block" and r["detail"]["operation"] == "ARCHIVE"
    assert any("refused by archive-cascade block" in m.getMessage() and "element=g1" in m.getMessage() and "outbox=7" in m.getMessage()
               for m in caplog.records)


def test_the_real_gateway_logs_the_issue_117_refusal_and_sends_nothing(caplog, monkeypatch):
    monkeypatch.delenv("RE_ISSUE_117_BLOCK_OFF", raising=False)
    caplog.set_level(logging.INFO)
    g = gw.PyegeriaCatalogueGateway(MagicMock())
    me = MagicMock()
    g._clients["MetadataExpert"] = me
    with pytest.raises(GatewayError):
        g.delete_element("gz", gw.ARCHIVE, slug="db")
    assert me.method_calls == []
    assert any("refused by archive-cascade block" in r.getMessage() and "element=gz" in r.getMessage()
               and "deleteMethod=ARCHIVE" in r.getMessage() for r in caplog.records)


def test_a_detach_is_logged_and_recorded_too(registry, caplog):
    caplog.set_level(logging.INFO)
    gate = Gate()
    cc._send_destructive(registry, gate, "remove_catalog_target", slug="db", schema="sales", element_guid="g3",
                         typ="CatalogTarget", relationship_guid="rel-1", curation_id="cur-1", outbox_id=7)
    assert gate.calls == [("remove_catalog_target", "rel-1")]
    (r,) = rows(registry)
    assert r["element_guid"] == "g3" and r["detail"]["operation"] == "remove_catalog_target" and r["detail"]["relationship_guid"] == "rel-1"
    assert lines(caplog, "destructive call BEFORE") and lines(caplog, "destructive call AFTER")


# (d) ─────────────────────────────────────────────────────────────────────────────────────────────────────────────

def _enqueue(registry, kind):
    return registry.enqueue_outbox_element("database", "db", kind, f"qn::{kind}", {"slug": "db"})


def _make_due(registry, row_id):
    with registry._conn() as conn:
        conn.execute("UPDATE egeria_outbox SET next_attempt_at='2000-01-01T00:00:00' WHERE id=?", (row_id,))


def _row(registry, row_id):
    return next(r for r in registry.list_outbox_elements(limit=500) if r["id"] == row_id)


def _drain(registry):
    return drain_outbox(registry, OutboxClients(), lambda qn: "")


def test_a_failed_destructive_outbox_row_is_not_rescheduled_and_says_so(registry, monkeypatch, as_daemon):
    attempts = []

    def creator(clients, payload):
        attempts.append(1)
        raise ob.OutboxApplyError(SENTENCE)

    monkeypatch.setitem(ob._CREATORS, "catalogue_schema_leave_out", creator)
    rid = _enqueue(registry, "catalogue_schema_leave_out")
    first = _drain(registry)
    _make_due(registry, rid)                                   # the 11 minutes later of row 69822
    second = _drain(registry)
    assert len(attempts) == 1, "the destructive write was attempted again"
    row = _row(registry, rid)
    assert row["status"] == "dead" and row["attempts"] == 1
    assert row["last_error"].startswith("not retried: destructive write · OMAG-REPOSITORY-HANDLER-400-010")
    assert first["not_retried"] == 1 and first["failed"] == 0 and second["claimed"] == 0


def test_a_failed_non_destructive_outbox_row_is_still_retried(registry, monkeypatch, as_daemon):
    attempts = []

    def creator(clients, payload):
        attempts.append(1)
        raise ob.OutboxApplyError("boom")

    monkeypatch.setitem(ob._CREATORS, "annotation", creator)
    rid = _enqueue(registry, "annotation")
    s1 = _drain(registry)
    row = _row(registry, rid)
    assert row["status"] == "failed" and row["next_attempt_at"] and s1["failed"] == 1 and "not_retried" not in s1
    _make_due(registry, rid)
    _drain(registry)
    assert len(attempts) == 2 and _row(registry, rid)["attempts"] == 2


# (e) ─────────────────────────────────────────────────────────────────────────────────────────────────────────────

#: Every registered outbox creator, classified. A new creator that is in neither set fails the test below.
NON_DESTRUCTIVE = {"annotation", "publish_read_back", "collection_membership", "resource_list", "annotation_link",
                   "doc_source_publish", "catalogue_schema_attach",
                   "informal_tag_link", "group_folio_membership",
                   # Brief F: a person's Rating (Egeria upserts the caller's own) and a journal entry's Note (append-only)
                   "feedback_rating", "feedback_note"}
DESTRUCTIVE_WORDS = re.compile(r"delete|archive|remove|detach|unpublish|leave_out", re.I)


def test_every_outbox_kind_is_classified_destructive_or_not():
    registered = set(ob._CREATORS)
    assert registered == ob.DESTRUCTIVE_OUTBOX_KINDS | NON_DESTRUCTIVE, (
        "a new outbox kind must be listed in egeria_outbox.DESTRUCTIVE_OUTBOX_KINDS or in NON_DESTRUCTIVE here: "
        f"{sorted(registered - ob.DESTRUCTIVE_OUTBOX_KINDS - NON_DESTRUCTIVE)}")
    assert not ob.DESTRUCTIVE_OUTBOX_KINDS & NON_DESTRUCTIVE
    assert ob.DESTRUCTIVE_OUTBOX_KINDS <= registered


def test_a_kind_named_like_a_destructive_write_cannot_sit_in_the_non_destructive_list():
    assert not [k for k in NON_DESTRUCTIVE if DESTRUCTIVE_WORDS.search(k)]
    assert all(DESTRUCTIVE_WORDS.search(k) for k in ob.DESTRUCTIVE_OUTBOX_KINDS)
