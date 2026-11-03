"""Archive-cascade block (ISSUE-117): Egeria archived the WHOLE database tree when any part of it was archived (leaving
out us_sales archived the coco_pharma database element and both demo schemas, 2026-10-06).

Lifted 2026-10-23 for the leave-out ARCHIVE path only (proof: docs/design-notes/evidence/REPROOF-1021-PHASE2-RESULT.md):
by default a leave-out of a cataloguer-built schema archives, and its preview names the plan ("will archive N elements
anchored to <schema>: ..."), counted from the same list the apply walks. RE_ISSUE_117_BLOCK=on re-arms it. Soft delete,
the investigation-delete ARCHIVE and bulk delete keep the block. While blocked, NOTHING reaches Egeria for any archive
or delete of a database's tree (schema, table, column, schema type); the choice stays recorded in RE. The leave-out
tests written for the block ON re-arm it (`rearm`).

These tests run with the block at its DEFAULT (the env var is removed for each test; conftest sets it only for
the older catalogue tests that exercise the frozen delete path against the fake). The block can be lifted ONLY by
RE_ISSUE_117_BLOCK_OFF=<who>/<UTC ISO time>/<database slug>, for ONE database, for 60 minutes, read at call time:
there is no code constant. `test_known_negative_*` proves the guard can fail: with a good override for the slug the
same scenarios DO reach the delete path. These tests run un-stubbed (conftest leaves them alone)."""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from test_catalogue_commit import (  # noqa: E402,F401  (fixtures and helpers are shared)
    ME, _attached, _catalogued, _zones, choose, derived, entity_stub, fake, press, registry, step, view, world,
)
from resource_explorer import catalogue_commit as cc  # noqa: E402
from resource_explorer import catalogue_gateway as gw  # noqa: E402
from resource_explorer.catalogue_gateway import GatewayError  # noqa: E402


@pytest.fixture(autouse=True)
def _zone_access_open(monkeypatch):
    """Brief Z2: this write path asks `curation_access` (the one decision). These tests are about something else,
    so no zone is in use: the access reads are faked open at the client boundary (`tests/zone_fakes.open_access`)."""
    from tests.zone_fakes import open_access

    return open_access(monkeypatch)

# Brief I: a catalog commit is a person's own action; it runs as a signed-in caller.
pytestmark = pytest.mark.usefixtures("signed_in_caller")

WORDS = ("Egeria archives the whole database tree when any part of it is archived "
         "(archive-cascade block, archiveBeanInRepository) · choice kept, nothing sent")


def stamp(minutes_ago: float = 0.0) -> str:
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")


def rearm(monkeypatch, value: str = "on") -> None:
    monkeypatch.setenv("RE_ISSUE_117_BLOCK", value)


def override_for(monkeypatch, slug: str = "db", minutes_ago: float = 0.0) -> str:
    value = f"tester/{stamp(minutes_ago)}/{slug}"
    monkeypatch.setenv("RE_ISSUE_117_BLOCK_OFF", value)
    return value


@pytest.fixture(autouse=True)
def _default_state(monkeypatch):
    monkeypatch.delenv("RE_ISSUE_117_BLOCK_OFF", raising=False)
    monkeypatch.delenv("RE_ISSUE_117_BLOCK", raising=False)


@pytest.fixture(autouse=True)
def _fresh_override_log(monkeypatch):
    """A bad override is logged once per (value, reason) per process: production behaviour, kept on purpose. Start
    each test with an empty memory, so a warning another test in the same run already logged cannot hide this one's
    (the caplog assertions below would otherwise see ''). Monkeypatch restores the real set afterwards."""
    monkeypatch.setattr(gw, "_logged_override_reasons", set())


def sent_to_egeria(fake):
    """Every call that would change Egeria on the leave-out path."""
    return fake.ops("delete_element") + fake.ops("remove_catalog_target")


def test_the_block_is_on_as_shipped_except_for_the_leave_out_archive():
    assert gw.issue_117_blocked("db") is True and gw.issue_117_blocked("") is True and gw.ISSUE_117_WORDS == WORDS
    assert gw.issue_117_blocked("db", scope=gw.LEAVE_OUT_ARCHIVE) is False
    assert gw.issue_117_blocked("", scope=gw.LEAVE_OUT_ARCHIVE) is True, "a caller with no slug is blocked in every scope"
    assert gw.issue_117_blocked("db", scope="investigation_archive") is True, "only the named scope is lifted"
    assert gw.LIFTED_SCOPES == frozenset({gw.LEAVE_OUT_ARCHIVE})
    assert not hasattr(gw, "ISSUE_117_BLOCK"), "no code constant to edit"


# ── the real gateway: every kind of element, both forms ─────────────────────────────────────────────

@pytest.mark.parametrize("kind", ["schema", "table", "column", "schema type"])
@pytest.mark.parametrize("form", [gw.ARCHIVE, gw.SOFT_DELETE])
def test_the_real_gateway_sends_nothing_for_any_element_of_a_database_tree(kind, form):
    g = gw.PyegeriaCatalogueGateway(entity_stub())
    clients = {n: MagicMock(name=n) for n in ("AssetMaker", "MetadataExpert", "AutomatedCuration", "ServerOps")}
    g._clients.update(clients)
    with pytest.raises(GatewayError) as err:
        g.delete_element(f"guid-of-a-{kind}", form, slug="db")
    assert str(err.value) == WORDS
    for name, c in clients.items():
        assert c.method_calls == [], f"{name} was called: nothing may be sent"


# ── through the commit: preview, press, outbox ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("setup", [_attached, _catalogued])
def test_leaving_out_a_cataloged_schema_sends_nothing_and_keeps_the_choice(world, fake, setup, monkeypatch):
    rearm(monkeypatch)
    setup(world, fake, "sales")
    before = len(sent_to_egeria(fake))
    choose(world, "sales", "leave_out")
    choose(world, "archive", "catalogue")                    # something else to commit, so the press happens
    out, rec = press(world, fake)
    row = next(r for r in out["preview"]["leave_out"] if r["schema"] == "sales")
    assert row["form"] == "issue_117" and row["blocked"] is True
    assert row["text"] == f"sales: {WORDS}"
    assert len(sent_to_egeria(fake)) == before, "the delete path was reached for a cataloged schema"
    assert step(rec, "leave_outs")["state"] == "skipped"
    assert view(world)["schemas"][[s["name"] for s in view(world)["schemas"]].index("sales")]["effective"] == "leave_out", "the choice is kept"
    assert any(n["reason"] == WORDS for n in out["preview"]["manifest"]["not_committed"])


def test_the_outbox_handler_refuses_a_cataloged_schema_before_sending_anything(world, fake, monkeypatch):
    rearm(monkeypatch)
    _attached(world, fake, "sales")
    choose(world, "sales", "leave_out")
    before = len(sent_to_egeria(fake))
    with pytest.raises(GatewayError) as err:
        cc.apply_leave_out(world["registry"], fake, {"slug": "db", "schema": "sales", "form": gw.ARCHIVE, "curation_id": "c1", "by": ME})
    assert WORDS in str(err.value)
    assert len(sent_to_egeria(fake)) == before


def test_a_schema_that_was_never_cataloged_is_still_just_nothing_to_remove(world, fake):
    choose(world, "sales", "leave_out")
    choose(world, "archive", "catalogue")
    out, _ = press(world, fake)
    row = next(r for r in out["preview"]["leave_out"] if r["schema"] == "sales")
    assert row["form"] == "none" and "nothing to remove" in row["text"]


# ── the guard can fail ─────────────────────────────────────────────────────────────────────────────────────

# ── the switch: only the env var, one database, 60 minutes, read at call time ─────────────────────────────

def test_blank_values_leave_the_block_on(monkeypatch):
    for blank in ("", "   "):
        monkeypatch.setenv("RE_ISSUE_117_BLOCK_OFF", blank)
        assert gw.issue_117_blocked("db") is True


def test_the_named_slug_inside_the_window_passes_the_guard(monkeypatch):
    override_for(monkeypatch, "db", minutes_ago=59)
    assert gw.issue_117_blocked("db") is False


def test_known_negative_the_named_slug_after_expiry_is_refused(monkeypatch):
    override_for(monkeypatch, "db", minutes_ago=61)
    assert gw.issue_117_blocked("db") is True
    assert gw.issue_117_override()[2].startswith("expired at ")
    override_for(monkeypatch, "db", minutes_ago=60)             # the boundary itself is expired
    assert gw.issue_117_blocked("db") is True
    assert gw.issue_117_override()[2].startswith("expired at ")


def test_another_slug_is_refused_while_the_override_is_set(monkeypatch):
    override_for(monkeypatch, "throwaway_db")
    assert gw.issue_117_blocked("throwaway_db") is False
    for other in ("db", "coco_pharma", "throwaway_db2", "THROWAWAY_DB", ""):
        assert gw.issue_117_blocked(other) is True, other


@pytest.mark.parametrize("value", [
    "test-clearance",                                            # the old bare clearance
    "dwolfson/2026-10-07T01:00Z",                                # the old <who>/<UTC> shape: no slug
    "tests/legacy-delete-path-fixture",                          # two parts, no time
    "dwolfson/not-a-time/db",                                    # unparsable time
    "dwolfson//db", "/2026-10-07T01:00:00Z/db", "dwolfson/2026-10-07T01:00:00Z/",   # an empty part
    "a/b/c/d",                                                   # a bad time in the third position
])
def test_malformed_values_are_on_with_a_logged_reason(monkeypatch, caplog, value):
    monkeypatch.setenv("RE_ISSUE_117_BLOCK_OFF", value)
    assert gw.issue_117_blocked("db") is True and gw.issue_117_blocked("c") is True
    slug, _expires, reason = gw.issue_117_override()
    assert slug == "" and reason != "", "the refusal carries its reason in the return value"
    assert "archive-cascade block stays ON" in caplog.text


def test_a_time_more_than_a_few_minutes_ahead_is_on_but_a_small_skew_passes(monkeypatch, caplog):
    override_for(monkeypatch, "db", minutes_ago=-30)
    assert gw.issue_117_blocked("db") is True and "in the future" in caplog.text
    assert "in the future" in gw.issue_117_override()[2]
    override_for(monkeypatch, "db", minutes_ago=-2)
    assert gw.issue_117_blocked("db") is False
    assert gw.issue_117_override()[0] == "db"


OFF_LINE = ("archive-cascade block: OFF (lifted 2026-10-23, re-proven 2026-10-29 on odpi/egeria abe31272 / image "
            "3f396977, evidence docs/design-notes/evidence/REPROOF-1029-RESULT.md) · scope: leave-out archive")


def test_the_state_line_is_off_for_the_leave_out_by_default():
    assert gw.issue_117_state_line() == OFF_LINE


def test_the_state_line_is_on_with_the_re_arm_stamp(monkeypatch, caplog):
    rearm(monkeypatch, "on")
    assert gw.issue_117_state_line() == "archive-cascade block: ON (no stamp)"
    rearm(monkeypatch, "dwolfson/2026-10-23T14:05:00Z")
    assert gw.issue_117_state_line() == "archive-cascade block: ON (dwolfson/2026-10-23T14:05:00Z)"
    rearm(monkeypatch, "dwolfson/2026-10-23T10:05:00-04:00")                  # read as that instant, said in UTC
    assert gw.issue_117_state_line() == "archive-cascade block: ON (dwolfson/2026-10-23T14:05:00Z)"
    for odd in ("off", "yes", "dwolfson/not-a-time"):                          # never lifts: ON, no stamp, logged
        rearm(monkeypatch, odd)
        assert gw.issue_117_state_line() == "archive-cascade block: ON (no stamp)", odd
        assert gw.issue_117_blocked("db", scope=gw.LEAVE_OUT_ARCHIVE) is True, odd
    assert "archive-cascade block is ON with no stamp" in caplog.text
    rearm(monkeypatch, "   ")                                                   # blank is unset
    assert gw.issue_117_state_line() == OFF_LINE


def test_the_state_line_adds_a_live_slug_override(monkeypatch):
    monkeypatch.setattr(gw, "_now_utc", lambda: datetime(2026, 10, 21, 14, 30, tzinfo=timezone.utc))
    rearm(monkeypatch, "on")
    monkeypatch.setenv("RE_ISSUE_117_BLOCK_OFF", "dwolfson/2026-10-21T14:05:00Z/throwaway_db")
    assert gw.issue_117_state_line() == ("archive-cascade block: ON (no stamp) · OFF for throwaway_db until "
                                         "2026-10-21T15:05:00Z")
    monkeypatch.setenv("RE_ISSUE_117_BLOCK_OFF", "dwolfson/2026-10-21T12:05:00Z/throwaway_db")      # expired
    assert gw.issue_117_state_line() == "archive-cascade block: ON (no stamp)"
    monkeypatch.setenv("RE_ISSUE_117_BLOCK_OFF", "bare-clearance")
    assert gw.issue_117_state_line() == "archive-cascade block: ON (no stamp)"


def test_an_offset_time_is_read_as_that_instant(monkeypatch):
    monkeypatch.setattr(gw, "_now_utc", lambda: datetime(2026, 10, 21, 14, 30, tzinfo=timezone.utc))
    monkeypatch.setenv("RE_ISSUE_117_BLOCK_OFF", "who/2026-10-21T10:05:00-04:00/db")           # = 14:05Z
    assert gw.issue_117_blocked("db") is False


def test_the_session_start_logs_the_state(caplog):
    import logging
    from resource_explorer.web import app as web_app
    src = Path(web_app.__file__).read_text(encoding="utf-8")
    assert "issue_117_start_line" in src, "the lifespan logs the state and the platform's origin at start"


# ── the start-up line: the platform's origin beside the proven build (ruling (b), 2026-10-23) ──────────────────

PROVEN = "lift proven on odpi/egeria abe31272 / image 3f396977"


def test_the_start_line_prints_the_platform_origin_beside_the_proven_build():
    line = gw.issue_117_start_line(lambda: "Egeria OMAG Server Platform (version 6.3-SNAPSHOT)")
    assert line == f"{OFF_LINE} · platform reports Egeria OMAG Server Platform (version 6.3-SNAPSHOT) · {PROVEN}"


def test_an_unreadable_platform_says_so_and_the_block_state_is_unchanged(monkeypatch):
    assert gw.issue_117_start_line(lambda: "") == f"{OFF_LINE} · platform: unreachable at start"
    assert gw.issue_117_blocked("db", scope=gw.LEAVE_OUT_ARCHIVE) is False
    rearm(monkeypatch)
    assert gw.issue_117_start_line(lambda: "") == "archive-cascade block: ON (no stamp) · platform: unreachable at start"
    assert gw.issue_117_start_line(lambda: "an older build") == (
        f"archive-cascade block: ON (no stamp) · platform reports an older build · {PROVEN}"), "RE never compares builds"


def test_the_origin_read_is_one_get_and_never_raises(monkeypatch):
    import httpx
    calls = []

    class Answer:
        def __init__(self, code, text):
            self.status_code, self.text = code, text

    monkeypatch.setattr(gw, "log", gw.log)
    monkeypatch.setenv("EGERIA_PLATFORM_URL", "https://platform.test:9443")
    from resource_explorer import config as re_config
    monkeypatch.setattr(re_config, "egeria_platform_url", lambda: "https://platform.test:9443")
    for answer, want in ((Answer(200, " Egeria  6.3\n"), "Egeria 6.3"), (Answer(503, "down"), "")):
        monkeypatch.setattr(httpx, "get", lambda url, **kw: (calls.append(url), answer)[1])
        assert gw.read_platform_origin() == want
    def boom(url, **kw):
        calls.append(url)
        raise httpx.ConnectError("refused")
    monkeypatch.setattr(httpx, "get", boom)
    assert gw.read_platform_origin() == ""
    assert calls == ["https://platform.test:9443" + gw.PLATFORM_ORIGIN_PATH] * 3


# ── the proof row: "0 archived" comes from a row, not from the absence of one ──────────────────────────────

def _blocked_commit(world, fake, monkeypatch):
    rearm(monkeypatch)
    _catalogued(world, fake, "sales")
    choose(world, "sales", "leave_out")
    choose(world, "archive", "catalogue")
    return press(world, fake)


def test_a_refused_leave_out_is_a_proof_row_and_the_header_and_row_derive_from_it(world, fake, monkeypatch):
    out, rec = _blocked_commit(world, fake, monkeypatch)
    rows = [p for p in world["registry"].list_catalogue_commit_proofs("db") if p["proof"] == cc.P_LEAVE_OUT_BLOCKED]
    assert len(rows) == 1 and rows[0]["schema_name"] == "sales"
    assert rows[0]["detail"]["note"] == "kept, not sent · archive-cascade block"
    d = derived(world)
    assert "1 left out, kept, not sent · archive-cascade block · 0 archived or deleted in Egeria" in d["header"]["text"]
    assert d["schemas"]["sales"]["blocked_117"] is True and d["schemas"]["sales"]["second"].startswith("kept, not sent · archive-cascade block")
    assert "sales" in step(rec, "leave_outs")["detail"] and "kept, not sent" in step(rec, "leave_outs")["detail"]
    # delete the row: the claim goes with it (a word on screen derives from a proof row)
    conn = world["registry"]._conn
    with conn() as c:
        c.execute("DELETE FROM catalogue_commit_proofs WHERE proof = ?", (cc.P_LEAVE_OUT_BLOCKED,))
    d2 = derived(world)
    assert "ISSUE-117" not in d2["header"]["text"] and "blocked_117" not in d2["schemas"]["sales"]


def test_the_outbox_refusal_also_writes_the_row(world, fake, monkeypatch):
    rearm(monkeypatch)
    _attached(world, fake, "sales")
    choose(world, "sales", "leave_out")
    with pytest.raises(GatewayError):
        cc.apply_leave_out(world["registry"], fake, {"slug": "db", "schema": "sales", "form": gw.ARCHIVE, "curation_id": "c1", "by": ME})
    assert [p["detail"]["note"] for p in world["registry"].list_catalogue_commit_proofs("db") if p["proof"] == cc.P_LEAVE_OUT_BLOCKED] == ["kept, not sent · archive-cascade block"]


# ── the guard can fail: with the variable set the same scenarios reach the delete path ───────────────────────────

def test_known_negative_with_an_override_for_the_slug_the_commit_reaches_the_delete_path(world, fake, monkeypatch):
    rearm(monkeypatch)
    override_for(monkeypatch, "db")
    _attached(world, fake, "sales")
    choose(world, "sales", "leave_out")
    press(world, fake)
    assert fake.ops("delete_element"), "if this does not fail the other tests prove nothing"


def test_known_negative_with_an_override_for_the_slug_the_real_gateway_reaches_its_client(monkeypatch):
    override_for(monkeypatch, "db")
    g = gw.PyegeriaCatalogueGateway(entity_stub())
    md = MagicMock()
    g._clients.update({"MetadataExpert": md})
    g.delete_element("guid", gw.ARCHIVE, slug="db")
    assert md.delete_metadata_element.called


def test_the_real_gateway_refuses_another_slug_and_an_expired_override_and_sends_nothing(monkeypatch):
    md = MagicMock()
    for slug, minutes_ago in (("other_db", 0), ("db", 61)):
        override_for(monkeypatch, "db", minutes_ago=minutes_ago)
        g = gw.PyegeriaCatalogueGateway(entity_stub())
        g._clients.update({"MetadataExpert": md})
        with pytest.raises(GatewayError) as err:
            g.delete_element("guid", gw.ARCHIVE, slug=slug)
        assert str(err.value) == WORDS
    assert md.method_calls == []


def test_a_leave_out_on_another_slug_is_refused_while_the_override_names_a_different_database(world, fake, monkeypatch):
    rearm(monkeypatch)
    override_for(monkeypatch, "some_other_throwaway")
    _attached(world, fake, "sales")
    choose(world, "sales", "leave_out")
    choose(world, "archive", "catalogue")
    before = len(sent_to_egeria(fake))
    out, _ = press(world, fake)
    row = next(r for r in out["preview"]["leave_out"] if r["schema"] == "sales")
    assert row["form"] == "issue_117" and len(sent_to_egeria(fake)) == before
    with pytest.raises(GatewayError):
        cc.apply_leave_out(world["registry"], fake, {"slug": "db", "schema": "sales", "form": gw.ARCHIVE, "curation_id": "c1", "by": ME})
    assert len(sent_to_egeria(fake)) == before


def test_a_leave_out_for_the_named_slug_after_expiry_is_refused(world, fake, monkeypatch):
    rearm(monkeypatch)
    override_for(monkeypatch, "db", minutes_ago=61)
    _attached(world, fake, "sales")
    choose(world, "sales", "leave_out")
    before = len(sent_to_egeria(fake))
    with pytest.raises(GatewayError):
        cc.apply_leave_out(world["registry"], fake, {"slug": "db", "schema": "sales", "form": gw.ARCHIVE, "curation_id": "c1", "by": ME})
    assert len(sent_to_egeria(fake)) == before



# ── P_RESTORED: a restore script appends the row by kind name; the word derives from it ───────────────────────

def test_the_restored_proof_kind_name_is_a_stable_string():
    assert cc.P_RESTORED == "restored" and cc.P_RESTORED in cc.STATE_PROOFS


def _append_restored(world, schema="sales"):
    return world["registry"].append_catalogue_commit_proof(
        "db", proof="restored", node_kind="schema", schema_name=schema,
        element_guid="aaaaaaaa-0000-0000-0000-000000000001",
        detail={"from_guid": "bbbbbbbb-0000-0000-0000-000000000002", "note": "restored by the Egeria lead"},
        recorded_by="restore-script", read_at="2026-10-06T16:40:00")


def test_the_restored_word_derives_from_the_proof_row_and_goes_when_the_row_does(world, fake):
    choose(world, "sales", "catalogue")
    _append_restored(world)
    d = derived(world)
    st = d["schemas"]["sales"]
    assert st["state"] == "restored"
    assert st["words"] == "restored · aaaaaaaa · from bbbbbbbb · 10-06 16:40"
    assert st["second"] == "restored by the Egeria lead"
    assert "1 restored" in d["header"]["text"]
    # the row text on the page is that state's words (egeriaStateHtml prints st.words): same string
    with world["registry"]._conn() as c:
        c.execute("DELETE FROM catalogue_commit_proofs WHERE proof = ?", ("restored",))
    d2 = derived(world)
    assert d2["schemas"]["sales"]["state"] != "restored" and "restored" not in d2["schemas"]["sales"]["words"]
    assert "restored" not in d2["header"]["text"]


RESTORED_WORDS = ("archive blocked · this schema was restored after the 2026-10-06 incident; archiving restored "
                  "trees is not proven")


def test_a_restored_schema_that_is_left_out_is_still_blocked_from_any_archive(world, fake):
    """At the DEFAULT (no re-arm): a restored schema passes no lifted scope (architect, 2026-10-23)."""
    choose(world, "sales", "leave_out")
    choose(world, "archive", "catalogue")
    _append_restored(world)
    out, _ = press(world, fake)
    row = next(r for r in out["preview"]["leave_out"] if r["schema"] == "sales")
    assert row["form"] == "issue_117" and row["blocked"] is True and row["text"] == f"sales: {RESTORED_WORDS}"
    assert not sent_to_egeria(fake) and not fake.ops("delete_element")


def test_the_outbox_refuses_a_restored_schema_at_the_default_with_no_client_call(world, fake):
    _catalogued(world, fake, "sales")
    _append_restored(world)
    choose(world, "sales", "leave_out")
    before = len(sent_to_egeria(fake))
    with pytest.raises(GatewayError) as err:
        cc.apply_leave_out(world["registry"], fake, {"slug": "db", "schema": "sales", "form": gw.ARCHIVE,
                                                     "curation_id": "c1", "by": ME, "plan_guids": []})
    assert RESTORED_WORDS in str(err.value) and len(sent_to_egeria(fake)) == before


def test_known_negative_a_slug_override_opens_a_restored_schema(world, fake, monkeypatch):
    override_for(monkeypatch, "db")
    _attached(world, fake, "sales")
    _append_restored(world)
    choose(world, "sales", "leave_out")
    out, _ = press(world, fake)
    row = next(r for r in out["preview"]["leave_out"] if r["schema"] == "sales")
    assert row["form"] == gw.SOFT_DELETE and fake.ops("delete_element"), "if this does not fail the test above proves nothing"


# ── ruling (3): the preview's GUID set rides in the payload; the apply refuses on any drift ─────────────────

def _payload(world, fake, schema="sales"):
    """The leave-out payload a press would queue, from the preview's own row."""
    out = cc.start_commit(world["registry"], "db", ME, gateway=fake)
    item = next(i for i in out["curation"]["selection"]["leave_out"] if i["schema"] == schema)
    return {"slug": "db", "schema": schema, "form": item["form"], "curation_id": "c1", "by": ME,
            "plan_guids": item["plan_guids"]}


def _plan_refused(world, fake, payload):
    before = len(sent_to_egeria(fake))
    with pytest.raises(GatewayError) as err:
        cc.apply_leave_out(world["registry"], fake, payload)
    assert str(err.value) == "sales: the plan changed · preview again"
    assert len(sent_to_egeria(fake)) == before, "nothing is sent on refusal, not even the detach"
    rows = [p for p in world["registry"].list_catalogue_commit_proofs("db") if p["proof"] == cc.P_LEAVE_OUT_PLAN_CHANGED]
    assert rows and rows[-1]["detail"]["note"] == cc.PLAN_CHANGED
    assert derived(world)["schemas"]["sales"]["second"].startswith(cc.PLAN_CHANGED), "the schema cue derives from the row"
    return rows[-1]["detail"]


def test_the_preview_set_rides_in_the_payload_and_an_unchanged_walk_archives(world, fake):
    _catalogued(world, fake, "sales")
    choose(world, "sales", "leave_out")
    p = _payload(world, fake)
    assert p["form"] == gw.ARCHIVE and len(p["plan_guids"]) > 1
    cc.apply_leave_out(world["registry"], fake, p)
    assert {c[1] for c in fake.ops("delete_element")} == set(p["plan_guids"])


def test_known_negative_a_guid_the_preview_did_not_count_is_refused(world, fake):
    _catalogued(world, fake, "sales")
    choose(world, "sales", "leave_out")
    p = _payload(world, fake)
    counted = p["plan_guids"]
    p["plan_guids"] = counted[1:]                                    # the walk holds counted[0]; the preview did not count it
    d = _plan_refused(world, fake, p)
    assert d["not_counted"] == [counted[0]] and d["no_longer_held"] == []


def test_known_negative_a_guid_the_walk_no_longer_holds_is_refused(world, fake):
    _catalogued(world, fake, "sales")
    choose(world, "sales", "leave_out")
    p = _payload(world, fake)
    col = next(g for g in p["plan_guids"] if fake.elements[g]["type"] == "RelationalColumn")
    fake.elements[col]["deleted"] = True                             # gone from Egeria since the preview
    d = _plan_refused(world, fake, p)
    assert d["no_longer_held"] == [col] and d["not_counted"] == []


def test_a_payload_queued_before_the_plan_set_existed_is_refused(world, fake):
    _catalogued(world, fake, "sales")
    choose(world, "sales", "leave_out")
    p = _payload(world, fake)
    del p["plan_guids"]
    d = _plan_refused(world, fake, p)
    assert d["preview_set"] == "none in the payload"


# ── _send_destructive takes its scope from the caller; a SurveyReport is never archived or deleted ──────────

def _send(world, fake, *, typ="RelationalColumn", form=gw.ARCHIVE, **kw):
    cc._send_destructive(world["registry"], fake, "delete", slug="db", schema="sales", element_guid="g-1", typ=typ,
                         form=form, qualified_name="q", **kw)


def test_a_destructive_call_without_a_scope_is_blocked(world, fake):
    with pytest.raises(GatewayError) as err:
        _send(world, fake)                                            # no scope: fail closed
    assert str(err.value) == WORDS and fake.ops("delete_element") == []
    rows = [p for p in world["registry"].list_catalogue_commit_proofs("db") if p["proof"] == cc.P_DELETE_CALL]
    assert rows[-1]["detail"]["outcome"] == cc.DELETE_REFUSED


SURVEY_REPORT_WORDS = ("RE never archives or deletes an RE SurveyReport; a retirement, if ever, detaches "
                       "ReportedAnnotation links and leaves the report")


@pytest.mark.parametrize("setting", ["default", "re-armed", "slug override"])
@pytest.mark.parametrize("form", [gw.ARCHIVE, gw.SOFT_DELETE])
def test_a_survey_report_is_never_archived_or_deleted(world, fake, monkeypatch, setting, form):
    if setting == "re-armed":
        rearm(monkeypatch)
    elif setting == "slug override":
        override_for(monkeypatch, "db")
    with pytest.raises(GatewayError) as err:
        _send(world, fake, typ="SurveyReport", form=form, scope=gw.LEAVE_OUT_ARCHIVE)
    assert str(err.value) == SURVEY_REPORT_WORDS and fake.ops("delete_element") == []
    rows = [p for p in world["registry"].list_catalogue_commit_proofs("db") if p["proof"] == cc.P_DELETE_CALL]
    assert rows[-1]["detail"]["outcome"] == cc.DELETE_REFUSED and rows[-1]["detail"]["error"] == SURVEY_REPORT_WORDS


def test_known_negative_with_the_slug_override_another_type_is_sent(world, fake, monkeypatch):
    override_for(monkeypatch, "db")
    _catalogued(world, fake, "sales")
    col = next(g for g, x in fake.elements.items() if x["type"] == "RelationalColumn")
    cc._send_destructive(world["registry"], fake, "delete", slug="db", schema="sales", element_guid=col,
                         typ="RelationalColumn", form=gw.ARCHIVE, qualified_name="q")
    assert [c[1] for c in fake.ops("delete_element")] == [col], "if this does not fail the refusals prove nothing"


def test_a_walk_holding_a_survey_report_is_refused_whole_in_preview_and_apply(world, fake, monkeypatch):
    _catalogued(world, fake, "sales")
    choose(world, "sales", "leave_out")
    real = cc.read_hangs_off

    def with_report(*a, **k):
        r = real(*a, **k)
        return {**r, "delete_order": list(r["delete_order"]) + [("rep-1", "SurveyReport", "sales::report")]}
    monkeypatch.setattr(cc, "read_hangs_off", with_report)
    choose(world, "archive", "catalogue")
    before = len(sent_to_egeria(fake))
    out, _ = press(world, fake)
    row = next(r for r in out["preview"]["leave_out"] if r["schema"] == "sales")
    assert row["form"] == "survey_report" and row["blocked"] is True and row["text"] == f"sales: {SURVEY_REPORT_WORDS}"
    with pytest.raises(GatewayError) as err:
        cc.apply_leave_out(world["registry"], fake, {"slug": "db", "schema": "sales", "form": gw.ARCHIVE,
                                                     "curation_id": "c1", "by": ME, "plan_guids": []})
    assert SURVEY_REPORT_WORDS in str(err.value) and len(sent_to_egeria(fake)) == before


# ── the 2026-10-23 lift: the leave-out ARCHIVE only ──────────────────────────────────────────────────────────

def _types_sent(fake):
    return [fake.elements[c[1]]["type"] for c in fake.ops("delete_element")]


def test_by_default_a_cataloged_leave_out_archives_and_the_preview_names_the_plan_it_then_walks(world, fake):
    _catalogued(world, fake, "sales")
    before = len(fake.ops("delete_element"))
    choose(world, "sales", "leave_out")
    out, rec = press(world, fake)
    row = next(r for r in out["preview"]["leave_out"] if r["schema"] == "sales")
    assert row["form"] == gw.ARCHIVE and row["blocked"] is False
    sent = _types_sent(fake)[before:]
    assert all(c[2] == gw.ARCHIVE for c in fake.ops("delete_element")[before:])
    counts = {}
    for t in sent:
        counts[t] = counts.get(t, 0) + 1
    assert row["plan"] == {"total": len(sent), "by_type": counts}, "the preview counts the list the apply walks"
    assert sent[-1] == "DeployedDatabaseSchema" and counts["RelationalTable"] == 2 and counts["RelationalColumn"] == 4
    words = (f"will archive {len(sent)} elements anchored to sales: "
             + " · ".join(f"{n} {t}" for t, n in counts.items()))
    assert words in row["text"], row["text"]
    assert "Egeria archives the whole database tree" not in row["text"]
    assert step(rec, "leave_outs")["state"] == "done", step(rec, "leave_outs")
    assert not [p for p in world["registry"].list_catalogue_commit_proofs("db") if p["proof"] == cc.P_LEAVE_OUT_BLOCKED]


def test_the_plan_words_come_from_the_walk_list():
    walk = [("c1", "RelationalColumn", "q"), ("c2", "RelationalColumn", "q"), ("t1", "RelationalTable", "q"),
            ("s", "DeployedDatabaseSchema", "q")]
    plan = cc.archive_plan(walk)
    assert plan == {"total": 4, "by_type": {"RelationalColumn": 2, "RelationalTable": 1, "DeployedDatabaseSchema": 1}}
    assert cc.archive_plan_words(plan, "alpha") == ("will archive 4 elements anchored to alpha: 2 RelationalColumn · "
                                                    "1 RelationalTable · 1 DeployedDatabaseSchema")
    read = {"delete_order": walk[:-1], "element_guid": "s"}
    assert cc.leave_out_walk(read, "q") == walk


def test_a_plan_that_cannot_be_read_says_it_could_not_check_and_sends_nothing(world, fake, monkeypatch):
    _catalogued(world, fake, "sales")
    choose(world, "sales", "leave_out")
    choose(world, "archive", "catalogue")
    before = len(sent_to_egeria(fake))
    monkeypatch.setattr(cc, "read_hangs_off", lambda *a, **k: {"state": "cannot_check", "error": "Egeria said no"})
    out, _ = press(world, fake)
    row = next(r for r in out["preview"]["leave_out"] if r["schema"] == "sales")
    assert row["form"] == "cannot_check" and row["blocked"] is True and "plan" not in row
    assert row["text"] == f"sales: {cc.CANT_CHECK}"
    assert len(sent_to_egeria(fake)) == before


def test_re_armed_a_leave_out_is_refused_with_no_client_call(world, fake, monkeypatch):
    _catalogued(world, fake, "sales")
    rearm(monkeypatch, "dwolfson/2026-10-23T14:05:00Z")
    before = len(sent_to_egeria(fake))
    choose(world, "sales", "leave_out")
    choose(world, "archive", "catalogue")
    out, _ = press(world, fake)
    row = next(r for r in out["preview"]["leave_out"] if r["schema"] == "sales")
    assert row["form"] == "issue_117" and row["text"] == f"sales: {WORDS}"
    with pytest.raises(GatewayError) as err:
        cc.apply_leave_out(world["registry"], fake, {"slug": "db", "schema": "sales", "form": gw.ARCHIVE, "curation_id": "c1", "by": ME})
    assert WORDS in str(err.value)
    assert len(sent_to_egeria(fake)) == before


def test_re_armed_the_real_gateway_refuses_the_leave_out_archive_and_sends_nothing(monkeypatch):
    rearm(monkeypatch)
    g = gw.PyegeriaCatalogueGateway(entity_stub())
    md = MagicMock()
    g._clients.update({"MetadataExpert": md})
    with pytest.raises(GatewayError) as err:
        g.delete_element("guid", gw.ARCHIVE, slug="db", scope=gw.LEAVE_OUT_ARCHIVE)
    assert str(err.value) == WORDS and md.method_calls == []


def test_known_negative_by_default_the_real_gateway_sends_the_leave_out_archive(monkeypatch):
    g = gw.PyegeriaCatalogueGateway(entity_stub())
    md = MagicMock()
    g._clients.update({"MetadataExpert": md})
    g.delete_element("guid", gw.ARCHIVE, slug="db", scope=gw.LEAVE_OUT_ARCHIVE)
    assert md.delete_metadata_element.called, "if this does not fail the re-armed test proves nothing"


def test_a_soft_delete_keeps_the_block_whatever_scope_it_names(monkeypatch):
    g = gw.PyegeriaCatalogueGateway(entity_stub())
    md = MagicMock()
    g._clients.update({"MetadataExpert": md})
    with pytest.raises(GatewayError):
        g.delete_element("guid", gw.SOFT_DELETE, slug="db", scope=gw.LEAVE_OUT_ARCHIVE)
    assert md.method_calls == []


def test_a_soft_delete_leave_out_stays_blocked_by_default_and_nothing_is_detached(world, fake):
    _attached(world, fake, "sales")                       # nothing hangs off it: the soft-delete form
    choose(world, "sales", "leave_out")
    choose(world, "archive", "catalogue")
    before = len(sent_to_egeria(fake))
    out, rec = press(world, fake, refresh=False)          # no refresh: the cataloguer adds no tables to it
    row = next(r for r in out["preview"]["leave_out"] if r["schema"] == "sales")
    assert row["form"] == "issue_117" and row["text"] == f"sales: {WORDS}"
    assert len(sent_to_egeria(fake)) == before
    with pytest.raises(GatewayError) as err:
        cc.apply_leave_out(world["registry"], fake, {"slug": "db", "schema": "sales", "form": gw.SOFT_DELETE, "curation_id": "c1", "by": ME})
    assert WORDS in str(err.value)
    assert len(sent_to_egeria(fake)) == before, "a soft delete is refused before the target is detached"


def test_known_negative_with_a_slug_override_the_soft_delete_leave_out_goes_through(world, fake, monkeypatch):
    override_for(monkeypatch, "db")
    _attached(world, fake, "sales")
    choose(world, "sales", "leave_out")
    out, _ = press(world, fake)
    row = next(r for r in out["preview"]["leave_out"] if r["schema"] == "sales")
    assert row["form"] == gw.SOFT_DELETE and fake.ops("delete_element")


def test_re_armed_the_slug_override_still_opens_one_slug(world, fake, monkeypatch):
    rearm(monkeypatch)
    override_for(monkeypatch, "db")
    assert gw.issue_117_blocked("db", scope=gw.LEAVE_OUT_ARCHIVE) is False
    assert gw.issue_117_blocked("other_db", scope=gw.LEAVE_OUT_ARCHIVE) is True
    _catalogued(world, fake, "sales")
    before = len(fake.ops("delete_element"))
    choose(world, "sales", "leave_out")
    out, _ = press(world, fake)
    row = next(r for r in out["preview"]["leave_out"] if r["schema"] == "sales")
    assert row["form"] == gw.ARCHIVE and len(fake.ops("delete_element")) > before


def test_the_investigation_archive_and_bulk_delete_stay_blocked_by_default():
    """The callers outside the lift pass no scope: investigation_delete (anchor = Project, slug "") and bulk_ops."""
    import inspect
    from resource_explorer import bulk_ops, investigation_delete
    assert gw.issue_117_blocked("") is True and gw.issue_117_blocked("p1") is True
    for mod in (investigation_delete, bulk_ops):
        assert "LEAVE_OUT_ARCHIVE" not in inspect.getsource(mod), mod.__name__


# ── hardening 2026-10-23: bounded start read, short logged origin, subtype + untyped refusals ───────────────────

def test_a_slow_origin_read_never_holds_the_start_beyond_the_bound(monkeypatch):
    import asyncio
    import time
    from resource_explorer.web import app as web_app
    monkeypatch.setattr(gw, "read_platform_origin", lambda *a, **k: (time.sleep(1.5), "late origin")[1])

    async def timed():
        # Timed inside the loop, as the lifespan awaits it: asyncio.run itself waits for the default
        # executor on exit, so it would wait out the 1.5 s read the start line has already left behind.
        t0 = time.monotonic()
        out = await web_app.start_line_within(0.2)
        return out, time.monotonic() - t0
    line, took = asyncio.run(timed())
    assert took < 1.2, "the start did not wait for the slow read"
    assert line == f"{OFF_LINE} · platform: unreachable at start"
    assert "5" in str(web_app.START_LINE_SECONDS) and web_app.START_LINE_SECONDS == 5


def test_the_logged_origin_is_cut_at_200_characters(monkeypatch):
    import httpx

    class R:
        status_code = 200
        text = "X" * 500
    monkeypatch.setattr(httpx, "get", lambda *a, **k: R())
    monkeypatch.setenv("EGERIA_PLATFORM_URL", "https://example.invalid:9443")
    assert len(gw.read_platform_origin()) == 200
    assert len(gw.platform_origin_words("Y" * 500).split(" · ")[0]) == len("platform reports ") + 200


@pytest.mark.parametrize("typ", ["SurveyReport", "EgeriaSurveyReport", "MySurveyReport"])
def test_a_survey_report_subtype_is_refused_too(world, fake, typ):
    with pytest.raises(GatewayError) as err:
        _send(world, fake, typ=typ, scope=gw.LEAVE_OUT_ARCHIVE)
    assert str(err.value) == SURVEY_REPORT_WORDS and fake.ops("delete_element") == []


def test_a_walk_member_with_no_type_name_is_refused_whole_in_preview_and_apply(world, fake, monkeypatch):
    _catalogued(world, fake, "sales")
    choose(world, "sales", "leave_out")
    real = cc.read_hangs_off
    words = "couldn't tell what an element is · nothing archived"

    def with_untyped(*a, **k):
        r = real(*a, **k)
        return {**r, "delete_order": list(r["delete_order"]) + [("x-1", "", "sales::x")]}
    monkeypatch.setattr(cc, "read_hangs_off", with_untyped)
    choose(world, "archive", "catalogue")
    before = len(sent_to_egeria(fake))
    out, _ = press(world, fake)
    row = next(r for r in out["preview"]["leave_out"] if r["schema"] == "sales")
    assert row["blocked"] is True and row["text"] == f"sales: {words}"
    with pytest.raises(GatewayError) as err:
        cc.apply_leave_out(world["registry"], fake, {"slug": "db", "schema": "sales", "form": gw.ARCHIVE,
                                                     "curation_id": "c1", "by": ME, "plan_guids": []})
    assert words in str(err.value) and len(sent_to_egeria(fake)) == before


# ── merge of #845 (2026-10-23): a leave-out in a pressed run carries the marker AND passes the lift's guards ──────

def _pressed_press(world, fake, run_id="pressed-run"):
    import json

    from resource_explorer import presser_hold

    assert presser_hold.hold(run_id, ME, "held-token")
    try:
        # as `run_queue` runs a pressed commit: the run is marked pressed and its writes are made as the presser
        with presser_hold.pressed_run(run_id, ME), presser_hold.publishing_as_presser():
            out, rec = press(world, fake)
    finally:
        presser_hold.release(run_id)
    with world["registry"]._conn() as conn:
        rows = [json.loads(r["payload_json"]) for r in conn.execute(
            "SELECT payload_json FROM egeria_outbox WHERE run_id=? AND element_kind=?",
            (out["curation"]["id"], cc.KIND_LEAVE_OUT)).fetchall()]
    return out, rec, rows


def test_a_pressed_leave_out_carries_the_marker_and_the_preview_set_and_archives(world, fake):
    _catalogued(world, fake, "sales")
    before = len(fake.ops("delete_element"))
    choose(world, "sales", "leave_out")
    out, rec, payloads = _pressed_press(world, fake)
    assert payloads and all(p.get("as_presser") is True and p.get("plan_guids") for p in payloads), payloads
    assert step(rec, "leave_outs")["state"] == "done", step(rec, "leave_outs")
    sent = fake.ops("delete_element")[before:]
    assert sent and all(c[2] == gw.ARCHIVE for c in sent)


def test_a_pressed_leave_out_of_a_restored_schema_is_still_refused_with_no_client_call(world, fake):
    _catalogued(world, fake, "sales")
    _append_restored(world)
    before = len(sent_to_egeria(fake))
    choose(world, "sales", "leave_out")
    choose(world, "archive", "catalogue")
    out, rec, payloads = _pressed_press(world, fake)
    assert payloads == []                                   # refused at the press: a proof row, nothing queued
    assert [p for p in world["registry"].list_catalogue_commit_proofs("db")
            if p["proof"] == cc.P_LEAVE_OUT_BLOCKED and p.get("curation_id") == out["curation"]["id"]]
    assert not [c for c in sent_to_egeria(fake)[before:] if c[0] in ("delete_element", "remove_catalog_target")]
