"""Brief Z2 (2026-10-10): a database commit checks each schema element it would write, not only the database.

End to end through the real press and run (`catalogue_commit.start_commit` -> `execute_commit`), faking only the
two client boundaries: the catalogue gateway (`tests/fake_egeria_catalogue.FakeEgeria`) and the access reads
(`tests/zone_fakes`). A schema the person may not change is skipped with its own words, "not permitted ·
<reason>"; the rest of the commit proceeds; the NEXT read says the same.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from fake_egeria_catalogue import FakeEgeria as FakeCatalogue  # noqa: E402

from resource_explorer import catalogue_commit as cc  # noqa: E402
from resource_explorer import catalogue_scope as cs  # noqa: E402
from resource_explorer.registry import DatabaseEntity, ProjectRegistry  # noqa: E402
from tests.zone_fakes import element as zoned  # noqa: E402
from tests.zone_fakes import install  # noqa: E402

pytestmark = pytest.mark.usefixtures("signed_in_caller")

ME = "test-caller"            # the signed_in_caller fixture's user id
SECURED = "finance-zone"
SURVEY_AT = "2026-10-02T09:00:00"
SOURCE = {"sales": {"orders": ["id", "total"]}, "archive": {"old": ["id"]}, "a_b": {"t_ab": ["id"]}}


def tbl(name):
    return {"name": name, "table_type": "BASE TABLE", "row_count": 1, "row_count_state": "measured",
            "size_bytes": 1, "column_count": 1,
            "columns": [{"name": "id", "type": "integer", "nullable": False, "key_role": "PK",
                         "foreign_key": None, "comment": ""}]}


def sch(name, tables):
    return {"schema": name, "table_count": len(tables), "row_total": len(tables), "bytes_total": None,
            "is_estimate": False, "classification": "data", "reason": "", "tables": [tbl(t) for t in tables]}


@pytest.fixture
def access(monkeypatch):
    monkeypatch.delenv("EXPLORER_PUBLISH_ZONES", raising=False)
    monkeypatch.delenv("RE_ISSUE_117_BLOCK_OFF", raising=False)
    return install(monkeypatch)            # no configured zones: an unzoned element is open


@pytest.fixture
def world(tmp_path, monkeypatch, access):
    r = ProjectRegistry(db_path=str(tmp_path / "parts.db"))
    assert str(tmp_path) in r.database_url                                    # never the shared registry
    r.register_database(DatabaseEntity(slug="db", display_name="Shop", db_type="postgresql", host="localhost",
                                       egeria_host="host.docker.internal", port=5442, database_name="shop"))
    tree = {"schemas": [sch(n, list(t)) for n, t in SOURCE.items()]}
    monkeypatch.setattr(cs, "_load_tree", lambda reg, slug: tree)
    monkeypatch.setattr(cs, "_tree_survey_at", lambda reg, slug: SURVEY_AT)
    monkeypatch.setattr(cs, "_activity_counters", lambda reg, slug: {})
    monkeypatch.setattr(cs, "_row_sources", lambda reg, slug: {})
    return {"registry": r, "fake": FakeCatalogue(SOURCE), "access": access}


def choose(w, schema, choice):
    cs.set_node_choice(w["registry"], "db", ME, schema=schema, table="", choice=choice)


def press(w, *, refresh=False):
    out = cc.start_commit(w["registry"], "db", ME, refresh_now=refresh, gateway=w["fake"])
    rec = cc.execute_commit(w["registry"], out["curation"]["id"], gateway=w["fake"])
    return out, rec


def preview(w):
    view = cs.build_scope_view(w["registry"], "db")
    return cc.build_preview(w["registry"], "db", view, w["fake"])


def step(rec, name):
    return next(s for s in rec["steps"] if s["name"] == name)


def guid_of(w, schema):
    db = w["registry"].get_database("db")
    return w["fake"].read_element(cc.schema_qn(db, schema)).guid


def refuse(w, guid):
    w["access"].elements[guid] = zoned(guid, [SECURED])
    w["access"].controls[SECURED] = {"associatedSecurityList": {"DEFAULT": ["dbas"]}}


REASON = f"zone {SECURED} does not grant {ME} attach in Egeria"


def test_one_refused_schema_is_skipped_and_the_rest_are_written_and_the_next_read_agrees(world):
    reg = world["registry"]
    choose(world, "sales", "catalogue")
    choose(world, "archive", "catalogue")
    press(world)                                           # both cataloged, no zones anywhere
    sales = guid_of(world, "sales")
    refuse(world, sales)

    out, rec = press(world)
    p = out["preview"]
    assert p["attach"] == ["archive"]
    assert [(r["schema"], r["write"], r["words"]) for r in p["not_permitted"]] == \
        [("sales", "attach", f"not permitted · {REASON}")]
    assert out["curation"]["selection"]["not_permitted"] == [{"schema": "sales", "write": "attach", "reason": REASON}]
    # the run wrote the rest: an attach row for archive only, and a not-permitted row for sales
    cid = out["curation"]["id"]
    rows = [r for r in reg.list_catalogue_outbox_rows("db") if r["run_id"] == cid]
    assert [r["qualified_name"].rsplit(".", 1)[-1] for r in rows] == ["archive"]
    np = [x for x in reg.list_catalogue_commit_proofs("db") if x["proof"] == cc.P_NOT_PERMITTED]
    assert [(x["schema_name"], x["detail"]["reason"]) for x in np] == [("sales", REASON)]
    assert step(rec, "schema_targets")["state"] == "done"
    assert step(rec, "schema_targets")["detail"].endswith("not permitted: sales")
    assert step(rec, "publish_elements")["state"] == "done"
    # the NEXT read says the same
    again = preview(world)
    assert again["attach"] == ["archive"] and [r["schema"] for r in again["not_permitted"]] == ["sales"]


def test_an_element_rE_has_no_row_for_is_checked_at_the_press_and_in_the_run(world):
    """`a_b` is already in Egeria (made outside RE) but RE has no row for it: the press resolves it by qualified
    name through the gateway exactly as the run and the attach do (round 2, LOW 6), so the preview counts what
    the run writes, and the run refuses it again."""
    reg = world["registry"]
    db = reg.get_database("db")
    adopted = world["fake"].create_schema_element(db, "a_b", "")
    refuse(world, adopted)
    choose(world, "a_b", "catalogue")
    choose(world, "archive", "catalogue")
    out, rec = press(world)
    assert [r["schema"] for r in out["preview"]["not_permitted"]] == ["a_b"]
    assert out["preview"]["attach"] == ["archive"]
    assert adopted not in {t.element_guid for t in world["fake"].targets}
    assert {t.element_guid for t in world["fake"].targets} == {guid_of(world, "archive")}
    np = [x for x in reg.list_catalogue_commit_proofs("db") if x["proof"] == cc.P_NOT_PERMITTED]
    assert [x["schema_name"] for x in np] == ["a_b"]           # the press's refusal, recorded by the run


def test_every_part_refused_is_a_403_with_the_reason(world):
    choose(world, "sales", "catalogue")
    press(world)
    refuse(world, guid_of(world, "sales"))
    with pytest.raises(cc.CommitBlocked) as exc:
        cc.start_commit(world["registry"], "db", ME, gateway=world["fake"])
    assert exc.value.status == 403 and exc.value.message == REASON


def test_a_leave_out_refusal_is_said_before_the_issue_117_block(world):
    choose(world, "sales", "catalogue")
    choose(world, "archive", "catalogue")
    press(world)
    choose(world, "sales", "leave_out")
    rows = {r["schema"]: r for r in preview(world)["leave_out"]}
    assert rows["sales"]["form"] == "issue_117"            # permitted: the block is what holds it (unchanged)
    refuse(world, guid_of(world, "sales"))
    p = preview(world)
    rows = {r["schema"]: r for r in p["leave_out"]}
    assert rows["sales"]["form"] == "not_permitted" and rows["sales"]["blocked"] is True
    assert rows["sales"]["reason"] == f"not permitted · zone {SECURED} does not grant {ME} detach in Egeria"
    out, rec = press(world)
    proofs = [x for x in world["registry"].list_catalogue_commit_proofs("db") if x["schema_name"] == "sales"]
    assert cc.P_LEAVE_OUT_BLOCKED not in {x["proof"] for x in proofs}       # never reported as the block
    assert cc.P_NOT_PERMITTED in {x["proof"] for x in proofs}


def test_with_the_block_off_a_refused_table_holds_its_schema(world, monkeypatch):
    """The delete a leave-out would send walks every table and column; one table the person may not delete
    holds the whole schema (from the delete's own read, `read_hangs_off`). The block is not changed here: the
    test switches it off for itself only, and nothing is sent because the schema is held."""
    choose(world, "sales", "catalogue")
    choose(world, "archive", "catalogue")
    press(world, refresh=True)                            # the cataloguer makes the tables
    choose(world, "sales", "leave_out")
    monkeypatch.setenv("RE_ISSUE_117_BLOCK_OFF", "test/2026-10-10")
    db = world["registry"].get_database("db")
    read = cc.read_hangs_off(world["fake"], db, "sales")
    table = next(g for g, t, _q in read["delete_order"] if t == "RelationalTable")
    refuse(world, table)
    rows = {r["schema"]: r for r in preview(world)["leave_out"]}
    assert rows["sales"]["form"] == "not_permitted"
    assert rows["sales"]["reason"] == f"not permitted · zone {SECURED} does not grant {ME} delete in Egeria"
    before = list(world["fake"].calls)
    out, rec = press(world)
    assert not [c for c in world["fake"].calls[len(before):] if c[0] in ("delete", "archive")]


def test_a_portal_curator_override_is_carried_into_the_run(world, monkeypatch):
    from resource_explorer.a2a_auth import CallerIdentity, current_caller

    choose(world, "sales", "catalogue")
    press(world)
    refuse(world, guid_of(world, "sales"))
    token = current_caller.set(CallerIdentity(user_id="cora", egeria_token="t", auth_source="app-jwt", role="curator"))
    try:
        out = cc.start_commit(world["registry"], "db", "cora", gateway=world["fake"])
    finally:
        current_caller.reset(token)
    assert out["curation"]["selection"]["portal_role"] is True and out["preview"]["attach"] == ["sales"]
    # the run (as the requester, role "user" as run_queue sets it) does not re-check against the override
    token = current_caller.set(CallerIdentity(user_id="cora", egeria_token=None, auth_source="queued-run", role="user"))
    try:
        rec = cc.execute_commit(world["registry"], out["curation"]["id"], gateway=world["fake"])
    finally:
        current_caller.reset(token)
    assert step(rec, "schema_targets")["state"] == "done" and "not permitted" not in step(rec, "schema_targets")["detail"]


def test_the_run_refuses_the_whole_commit_when_the_database_itself_is_not_permitted(world):
    choose(world, "sales", "catalogue")
    press(world)
    db_guid = world["registry"].get_database("db").egeria_asset_guid
    out = cc.start_commit(world["registry"], "db", ME, gateway=world["fake"])
    refuse(world, db_guid)                                 # the zones changed between the press and the run
    calls = len(world["fake"].calls)
    rec = cc.execute_commit(world["registry"], out["curation"]["id"], gateway=world["fake"])
    assert step(rec, "publish_elements")["state"] == "failed"
    assert step(rec, "publish_elements")["detail"].startswith(f"not permitted · zone {SECURED} does not grant {ME}")
    assert rec["state"] == "failed"
    assert all(c[0] == "read_element" for c in world["fake"].calls[calls:])     # read, nothing sent to Egeria


def test_an_anonymous_viewer_is_not_checked_part_by_part_and_cannot_press(world):
    from resource_explorer.a2a_auth import current_caller

    choose(world, "sales", "catalogue")
    press(world)
    refuse(world, guid_of(world, "sales"))
    world["access"].calls.clear()                          # the press above read as a signed-in caller
    token = current_caller.set(None)
    try:
        p = preview(world)
        assert p["access_checked"] is False and p["attach"] == ["sales"] and world["access"].calls == []
        with pytest.raises(PermissionError):
            cc.start_commit(world["registry"], "db", ME, gateway=world["fake"])
    finally:
        current_caller.reset(token)
