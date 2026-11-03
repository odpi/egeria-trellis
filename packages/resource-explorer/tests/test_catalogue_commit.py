"""Curate slice B: the catalogue commit for a database, by schema-kind targets.

`BRIEF-CURATE-CATALOGUE-COMMIT-DATABASES.md`. Never touches the shared registry
(every registry here is a tmp SQLite file, asserted below) and never reaches
Egeria: a stateful `FakeEgeria` stands behind `CatalogueGateway`. The fake is
built from what the scratch runs recorded (see `fake_egeria_catalogue.py`), and
the real gateway's request bodies are pinned separately with mock clients
(`test_gateway_*`).

The lesson these tests are written to keep: a state word on screen derives from
a persisted proof row. So one test deletes the rows and the word goes with them.
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path
from unittest.mock import MagicMock
from tests.qn_lookup_bridge import expert_from_legacy

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from fake_egeria_catalogue import PROCESS_QN, FakeEgeria  # noqa: E402

from resource_explorer import catalogue_commit as cc  # noqa: E402
from resource_explorer import catalogue_gateway as gw  # noqa: E402
from resource_explorer import catalogue_scope as cs  # noqa: E402
from resource_explorer.registry import DatabaseEntity, ProjectRegistry  # noqa: E402

# Brief I: a catalog commit is a person's own action; it runs as a signed-in caller.
pytestmark = pytest.mark.usefixtures("signed_in_caller")

SURVEY_AT = "2026-10-02T09:00:00"
ME = "dwolfson"


def tbl(name, rows=10):
    return {"name": name, "table_type": "BASE TABLE", "row_count": rows, "row_count_state": "measured",
            "size_bytes": 100, "column_count": 1,
            "columns": [{"name": "id", "type": "integer", "nullable": False, "key_role": "PK",
                         "foreign_key": None, "comment": ""}]}


def sch(name, tables=()):
    tables = list(tables)
    return {"schema": name, "table_count": len(tables), "row_total": sum(t["row_count"] for t in tables),
            "bytes_total": None, "is_estimate": False, "classification": "data", "reason": "", "tables": tables}


SYSTEM = {"schema": None, "table_count": None, "row_total": None, "bytes_total": None,
          "is_estimate": False, "reason": "", "classification": "system", "system_count": 3}

#: What the database really holds, for the fake cataloguer: schema -> table -> columns.
SOURCE = {
    "sales": {"orders": ["id", "total"], "customers": ["id", "name"]},
    "archive": {"orders": ["id"], "old_stuff": ["id"]},
    "a_b": {"t_ab": ["id"]},
    "aXb": {"t_axb": ["id"]},
    "s_x": {"x_y": ["a", "only_in_x_y"], "xZy": ["only_in_xzy"]},
    "cm,ma": {"t_comma": ["id"]},
}


@pytest.fixture(autouse=True)
def _zones(monkeypatch):
    monkeypatch.setenv("EXPLORER_PUBLISH_ZONES", "zone-a,zone-b")


@pytest.fixture(autouse=True)
def _curation_access_reads(monkeypatch, _zones):
    """Brief Z round 2: the commit route checks curation access. The configured zones' controls admit every
    user (DEFAULT allUsers), so the caller is permitted and the start-up zone check lets the zone be written."""
    from tests.zone_fakes import grant_all, install

    fake = install(monkeypatch, zones=["zone-a", "zone-b"])
    fake.controls.update(grant_all("zone-a", "zone-b"))
    return fake


@pytest.fixture
def registry(tmp_path):
    db = str(tmp_path / "commit.db")
    r = ProjectRegistry(db_path=db)
    assert r.database_url == f"sqlite:///{db}" and str(tmp_path) in r.database_url    # never the shared one
    r.register_database(DatabaseEntity(
        slug="db", display_name="Shop", db_type="postgresql", host="localhost", egeria_host="host.docker.internal",
        port=5442, database_name="shop"))
    return r


@pytest.fixture
def world(registry, monkeypatch):
    w = {"tree": {"schemas": [sch(n, [tbl(t) for t in tabs]) for n, tabs in SOURCE.items()] + [SYSTEM]}}
    monkeypatch.setattr(cs, "_load_tree", lambda reg, slug: w["tree"])
    monkeypatch.setattr(cs, "_tree_survey_at", lambda reg, slug: SURVEY_AT)
    monkeypatch.setattr(cs, "_activity_counters", lambda reg, slug: {})
    monkeypatch.setattr(cs, "_row_sources", lambda reg, slug: {})
    w["registry"] = registry
    return w


@pytest.fixture
def fake():
    return FakeEgeria(SOURCE)


def choose(w, schema, choice, table=""):
    cs.set_node_choice(w["registry"], "db", ME, schema=schema, table=table, choice=choice)


def view(w):
    return cs.build_scope_view(w["registry"], "db")


def derived(w):
    return cc.derive_commit_state(w["registry"], "db", view(w))


def press(w, fake, *, refresh=True):
    out = cc.start_commit(w["registry"], "db", ME, refresh_now=refresh, gateway=fake)
    rec = cc.execute_commit(w["registry"], out["curation"]["id"], gateway=fake)
    return out, rec


def step(rec, name):
    return next(s for s in rec["steps"] if s["name"] == name)


def entity(w):
    return w["registry"].get_database("db")


# ── the commit's steps, in the manifest's order ──────────────────────────────


def test_commit_publishes_then_targets_and_writes_a_configured_zone_last_attaching_schema_kind_only(world, fake):
    choose(world, "sales", "catalogue")
    choose(world, "archive", "catalogue")
    out, rec = press(world, fake, refresh=False)
    names = [c[0] for c in fake.calls]
    # RE publishes first; a configured ZoneMembership is the LAST write, after every target (D1)
    assert names.index("publish_database") < names.index("initiate_catalog_action") < names.index("set_zone_membership")
    zone_call = fake.ops("set_zone_membership")[0]
    assert zone_call[1] == fake.db_guid and zone_call[2] == ("zone-a", "zone-b")
    # one SCHEMA-kind target per chosen schema, never the database or the server
    schema_guids = {g for g, e in fake.elements.items() if e["type"] == "DeployedDatabaseSchema"}
    assert {t.element_guid for t in fake.targets} == schema_guids and len(fake.targets) == 2
    assert fake.db_guid not in {t.element_guid for t in fake.targets}
    assert fake.server_guid not in {t.element_guid for t in fake.targets}
    # the schema element carries the qualified name the template produces
    assert fake.by_qn("PostgreSQL Relational Database Schema::host.docker.internal:5442::shop.sales") is not None
    assert [(s["name"], s["state"]) for s in rec["steps"]][:3] == [
        ("publish_elements", "done"), ("owner", "skipped"), ("schema_targets", "done")]
    assert cc.STEPS_DB == tuple(s["name"] for s in rec["steps"])
    # RE never restarts a connector, and with no refresh asked it does not even refresh one
    assert fake.restarts == 0 and fake.refreshes == 0
    assert step(rec, "refresh")["state"] == "skipped"


def test_never_attaches_the_same_schema_twice(world, fake):
    choose(world, "sales", "catalogue")
    press(world, fake, refresh=False)
    press(world, fake, refresh=False)
    assert len(fake.ops("initiate_catalog_action")) == 1 and len(fake.targets) == 1
    # and it READ the targets before attaching anything
    names = [c[0] for c in fake.calls]
    assert names.index("list_catalog_targets") < names.index("initiate_catalog_action")


def test_owner_from_context_is_added_after_the_database_exists(world, fake):
    world["registry"].save_context("database", "db", {"enrichment": {"owner": {"value": "erin", "author": ME}}})
    choose(world, "sales", "catalogue")
    out, rec = press(world, fake, refresh=False)
    assert fake.owners == {fake.db_guid: "erin"} and step(rec, "owner")["state"] == "done"
    names = [c[0] for c in fake.calls]
    assert names.index("publish_database") < names.index("set_owner")
    assert out["preview"]["manifest"]["owner"] == "erin"


def test_owner_refused_by_eg_source_says_so_not_success(world, fake):
    world["registry"].save_context("database", "db", {"enrichment": {"owner": {"value": "erin", "author": ME}}})
    fake.owner_policy = "refuse"
    choose(world, "sales", "catalogue")
    _, rec = press(world, fake, refresh=False)
    assert step(rec, "owner")["detail"] == cc.OWNER_REFUSED
    rows = [p for p in world["registry"].list_catalogue_commit_proofs("db") if p["proof"] == cc.P_OWNER]
    assert rows[0]["detail"]["outcome"] == "refused"


def test_no_owner_on_context_is_named_not_blocking(world, fake):
    choose(world, "sales", "catalogue")
    out, rec = press(world, fake, refresh=False)
    assert step(rec, "owner")["state"] == "skipped" and "not declared on Context" in step(rec, "owner")["detail"]
    assert any("Not carried: owner" in ln["text"] for ln in out["preview"]["manifest"]["lines"])


def test_survey_is_limited_by_request_parameter_and_names_the_chosen_schemas(world, fake):
    choose(world, "sales", "catalogue")
    choose(world, "archive", "catalogue")
    press(world, fake)
    assert fake.surveys == [(fake.db_guid, {"includeSchemaNames": "sales,archive"})]
    started = [p for p in world["registry"].list_catalogue_commit_proofs("db") if p["proof"] == cc.P_SURVEY]
    assert started[0]["detail"]["includeSchemaNames"] == ["sales", "archive"]
    # the proof row RE keeps for every native survey was written too
    with world["registry"]._conn() as conn:
        steps = conn.execute("SELECT executor_ref, engine_action_guid FROM step_runs WHERE slug = 'db'").fetchall()
    assert [(s["executor_ref"], bool(s["engine_action_guid"])) for s in steps] == [(PROCESS_QN, True)]


def test_forced_refresh_is_asked_for_and_catalogues_at_commit(world, fake):
    choose(world, "sales", "catalogue")
    _, rec = press(world, fake, refresh=True)
    assert fake.refreshes == 1 and fake.restarts == 0 and step(rec, "refresh")["state"] == "done"
    assert derived(world)["schemas"]["sales"]["state"] == "catalogued"


def test_publish_failure_skips_what_depends_on_it(world, fake):
    fake.fail["publish_database"] = "500 Egeria is down"
    choose(world, "sales", "catalogue")
    _, rec = press(world, fake)
    assert step(rec, "publish_elements")["state"] == "failed"
    for name in ("zone_membership", "owner", "survey_report", "survey"):
        assert step(rec, name)["state"] == "skipped", name
    assert step(rec, "schema_targets")["state"] == "skipped" and fake.targets == []
    assert rec["state"] == "failed"


# ── proof rows: every state word derives from one ────────────────────────────


def test_attached_waiting_then_catalogued_after_the_refresh_with_read_back_times(world, fake):
    choose(world, "sales", "catalogue")
    press(world, fake, refresh=False)
    s = derived(world)["schemas"]["sales"]
    assert s["state"] == "attached_waiting" and "waiting for Egeria's next refresh" in s["words"]
    assert "the connector's, not this schema's" in s["second"]            # connector-wide, never a target's
    assert derived(world)["tables"]["sales.orders"]["state"] == "follows_schema"
    fake.run_cataloguer()                                                  # Egeria's own cycle
    cc.read_back(world["registry"], fake, "db", ["sales"])
    d = derived(world)
    assert d["schemas"]["sales"]["state"] == "catalogued" and "2 tables" in d["schemas"]["sales"]["words"]
    assert d["tables"]["sales.orders"]["state"] == "catalogued"
    assert "read back" in d["tables"]["sales.orders"]["words"]
    # tables and columns are under the schema and NOTHING is directly under the database
    db_qn = "PostgreSQL Relational Database::host.docker.internal:5442::shop::"
    assert [e for e in fake.elements.values() if e["qn"].startswith(db_qn)] == []


def test_queued_is_the_outbox_row_and_failed_is_egeria_s_word(world, fake):
    choose(world, "sales", "catalogue")
    choose(world, "archive", "catalogue")
    press(world, fake, refresh=False)                                      # both attached; clean slate
    reg = world["registry"]
    # a row nobody has drained: queued, with its id
    oid = reg.enqueue_outbox_element("database", "db", cc.KIND_ATTACH, "x", {"slug": "db", "schema": "sales"}, run_id="r")
    assert derived(world)["schemas"]["sales"]["words"] == f"queued · outbox #{oid}"
    # a row Egeria refused: its own word, the step, and that it will retry
    reg.mark_outbox_failed(oid, "OutboxApplyError: 500 Egeria says no")
    s = derived(world)["schemas"]["sales"]
    assert s["state"] == "failed" and s["words"] == "failed · 500 Egeria says no"
    assert "step: attach" in s["second"] and "waiting for a worker" in s["second"]
    assert derived(world)["schemas"]["archive"]["state"] == "attached_waiting"      # the other is untouched
    # a row that gave up
    with reg._conn() as conn:
        conn.execute("UPDATE egeria_outbox SET status = 'dead', attempts = 8 WHERE id = ?", (oid,))
    assert "gave up after 8 attempts" in derived(world)["schemas"]["sales"]["second"]


def test_a_refused_attach_is_a_failed_state_with_egeria_s_word(world, fake):
    fake.catalog_action = "error"                       # Egeria's action type refuses, so the fallback runs ...
    fake.fail["add_catalog_target"] = "500 Egeria says no"        # ... and that is refused too
    choose(world, "sales", "catalogue")
    _, rec = press(world, fake, refresh=False)
    assert step(rec, "schema_targets")["state"] == "failed"
    s = derived(world)["schemas"]["sales"]
    assert s["state"] == "failed" and "500 Egeria says no" in s["words"] and fake.targets == []


def test_left_out_is_the_scope_record_and_not_a_state_of_egeria(world, fake):
    choose(world, "sales", "leave_out")
    s = derived(world)["schemas"]["sales"]
    assert s["state"] == "left_out" and s["words"] == "left out" and s["proof"] is None
    assert derived(world)["schemas"]["archive"]["state"] == "none"           # undecided says nothing


def test_state_words_vanish_with_their_proof_rows(world, fake):
    """The known negative: a status that survives the deletion of its proof was never derived from it."""
    choose(world, "sales", "catalogue")
    press(world, fake)
    assert derived(world)["schemas"]["sales"]["state"] == "catalogued"
    with world["registry"]._conn() as conn:
        conn.execute("DELETE FROM catalogue_commit_proofs")
        conn.execute("DELETE FROM egeria_outbox")
    d = derived(world)
    assert d["schemas"]["sales"]["state"] == "uncommitted" and d["schemas"]["sales"]["words"] == "not committed yet"
    assert d["tables"]["sales.orders"]["state"] == "none"
    assert d["header"] == {"state": "not_committed", "text": cc.NOT_COMMITTED_HEADER}


def test_header_marker_is_state_derived_never_a_constant(world, fake):
    choose(world, "sales", "catalogue")
    choose(world, "archive", "catalogue")
    before = derived(world)["header"]["text"]
    assert before == "Saved in Resource Explorer · not yet published to Egeria"
    press(world, fake, refresh=False)
    waiting = derived(world)["header"]["text"]
    cue_waiting = {c["key"]: c["cue"] for c in derived(world)["read_back"]["chips"]}["catalogued"]
    # The header names no state for attached schemas (architect, 2026-10-23): the read-back chips show it.
    assert "2 schemas chosen · 2 schemas attached" in waiting and "connector's last refresh" in waiting
    assert "waiting" not in waiting
    fake.run_cataloguer()
    cc.read_back(world["registry"], fake, "db", ["sales", "archive"])
    done = derived(world)["header"]["text"]
    assert "2 schemas chosen · 2 schemas attached" in done
    # Attached and catalogued share the header's words now; the state moved to the chips below it.
    cue_done = {c["key"]: c["cue"] for c in derived(world)["read_back"]["chips"]}["catalogued"]
    assert before != waiting and (cue_waiting, cue_done) == ("pending", "read_back")
    assert "Database element" in done and "in Egeria" in done


def test_read_failure_changes_no_state_and_says_when(world, fake):
    choose(world, "sales", "catalogue")
    press(world, fake, refresh=False)
    fake.fail["connector_status"] = "503 Egeria unreachable"     # F29: the read-back no longer lists every target
    s = cc.read_back(world["registry"], fake, "db", ["sales"])
    assert s["read_failed"] == 1
    d = derived(world)
    assert d["schemas"]["sales"]["state"] == "attached_waiting"               # the last good proof stands
    assert "last read of Egeria failed" in d["header"]["text"] and "503 Egeria unreachable" in d["header"]["text"]


def test_the_commit_survives_an_egeria_reset_by_recreating_from_the_scope(world, fake):
    choose(world, "sales", "catalogue")
    choose(world, "archive", "catalogue")
    press(world, fake)
    first = {g for g, e in fake.elements.items() if e["type"] == "DeployedDatabaseSchema"}
    fake.reset()                                                              # Egeria loses everything; RE's record stays
    assert len(world["registry"].list_catalogue_scope_events("db")) == 2
    press(world, fake)
    now = {g for g, e in fake.elements.items() if e["type"] == "DeployedDatabaseSchema"}
    assert len(now) == 2 and now.isdisjoint(first) and len(fake.targets) == 2
    d = derived(world)["schemas"]
    assert d["sales"]["state"] == d["archive"]["state"] == "catalogued"


# ── leave-out: two forms, chosen per schema from a relationships read ────────


def _catalogued(world, fake, *names):
    for n in names:
        choose(world, n, "catalogue")
    press(world, fake)


def _attached(world, fake, *names):
    """Attached to the cataloguer but not yet cataloged: no tables read back, so nothing but the template's own graph."""
    for n in names:
        choose(world, n, "catalogue")
    press(world, fake, refresh=False)


def test_leave_out_nothing_hangs_off_is_a_soft_delete_leaf_first_without_a_cascade(world, fake):
    _attached(world, fake, "sales")
    choose(world, "sales", "leave_out")
    out, rec = press(world, fake)
    row = out["preview"]["leave_out"][0]
    assert row["form"] == "soft_delete" and "nothing hangs off it" in row["text"]
    kinds = [t for _, t, f in fake.deleted_order]
    assert set(f for _, _, f in fake.deleted_order) == {"soft_delete"}
    # the template's own connection graph (4 elements) first, then the schema element: the fake refuses a parent
    # before its children. (A CATALOGED schema archives instead: see test_catalogue_rehearsal2_fixes.)
    assert sorted(kinds[:4]) == ["Connection", "Endpoint", "Endpoint", "VirtualConnection"]
    assert kinds[4:] == ["DeployedDatabaseSchema"] and len(kinds) == 5
    assert fake.targets == [] and fake.read_element(
        "PostgreSQL Relational Database Schema::host.docker.internal:5442::shop.sales") is None
    s = derived(world)["schemas"]["sales"]
    assert s["state"] == "deleted" and s["second"] == cc.LINGERING_LINE
    assert step(rec, "leave_outs")["state"] == "done"


def test_leave_out_with_a_term_assignment_is_an_archive_through_the_delete_endpoint(world, fake):
    _catalogued(world, fake, "archive")
    sqn = "PostgreSQL Relational Database Schema::host.docker.internal:5442::shop.archive"
    fake.add_term_assignment("orders::id", sqn, n=2)
    choose(world, "archive", "leave_out")
    out, rec = press(world, fake)
    row = out["preview"]["leave_out"][0]
    assert row["form"] == "archive" and "2 term assignments" in row["text"]
    assert "re-including it later creates a new element; the archived record stays" in row["text"]
    # the delete endpoint's ARCHIVE form, per element, leaf first: tables, columns and the schema type are anchored to the
    # database, so archiving the schema NEVER cascades them (live read 2026-10-06)
    kinds = [t for _, t, f in fake.deleted_order]
    assert {f for _, _, f in fake.deleted_order} == {"archive"} and kinds[-2:] == ["RelationalDBSchemaType", "DeployedDatabaseSchema"]
    assert kinds.index("RelationalTable") > kinds.index("RelationalColumn")
    assert all(e["archived"] for e in fake.elements.values() if e["qn"].startswith(sqn))
    # Egeria renamed it on archive: the original name reads absent even for lineage; the copy is under the new name
    assert fake.read_element(sqn) is None and fake.read_element(sqn, for_lineage=True) is None
    copies = fake.archived_siblings(sqn)
    assert [c.qualified_name for c in copies] == [f"{sqn}_archivedOn_" + copies[0].archived_on] and copies[0].archived
    s = derived(world)["schemas"]["archive"]
    assert s["state"] == "archived" and s["words"].startswith("archived in Egeria ·")
    assert fake.targets == []


def test_the_preview_names_the_form_before_the_press_and_writes_nothing(world, fake):
    _attached(world, fake, "sales", "archive")
    fake._cycle(fake.by_qn("PostgreSQL Relational Database Schema::host.docker.internal:5442::shop.archive")["guid"])
    fake.add_term_assignment("orders", "PostgreSQL Relational Database Schema::host.docker.internal:5442::shop.archive")
    choose(world, "sales", "leave_out")
    choose(world, "archive", "leave_out")
    deletes_before = len(fake.ops("delete_element"))
    p = cc.build_preview(world["registry"], "db", view(world), fake)
    forms = {r["schema"]: r["form"] for r in p["leave_out"]}
    assert forms == {"sales": "soft_delete", "archive": "archive"}
    assert p["button"] == "Publish · 0 schemas · deletes 1 from Egeria · archives 1"
    assert len(fake.ops("delete_element")) == deletes_before and len(fake.targets) == 2


def test_a_failed_relationships_read_is_couldn_t_check_and_blocks_that_schema_only(world, fake):
    _catalogued(world, fake, "sales", "archive")
    choose(world, "sales", "leave_out")
    choose(world, "archive", "catalogue")
    fake.fail["relationships"] = "503 read failed"
    p = cc.build_preview(world["registry"], "db", view(world), fake)
    row = p["leave_out"][0]
    assert row["form"] == "cannot_check" and row["blocked"] and cc.CANT_CHECK in row["text"]
    assert p["blocked_schemas"] == ["sales"]
    # never "nothing hangs off it", and the press leaves the schema exactly where it is
    assert "nothing hangs off it" not in row["text"]
    out, rec = press(world, fake)
    assert not [d for d in fake.deleted_order if "sales" in d[0]]
    assert derived(world)["schemas"]["sales"]["state"] == "catalogued"
    assert out["curation"]["selection"]["leave_out"] == []


def test_no_gateway_means_couldn_t_check_never_nothing(world):
    _catalogued_fake = FakeEgeria(SOURCE)
    choose(world, "sales", "catalogue")
    press(world, _catalogued_fake)
    choose(world, "sales", "leave_out")
    p = cc.build_preview(world["registry"], "db", view(world), None)
    assert p["leave_out"][0]["form"] == "cannot_check" and cc.CANT_CHECK in p["leave_out"][0]["text"]


def test_a_leave_out_that_was_never_catalogued_removes_nothing(world, fake):
    choose(world, "sales", "leave_out")
    choose(world, "archive", "catalogue")
    p = cc.build_preview(world["registry"], "db", view(world), fake)
    assert p["leave_out"][0]["form"] == "none" and "never published" in p["leave_out"][0]["text"]
    assert fake.ops("relationships") == [] and fake.ops("elements_under") == []


def test_the_form_is_re_derived_at_press_time_and_the_safer_wins(world, fake):
    _attached(world, fake, "sales")
    choose(world, "sales", "leave_out")
    from fake_egeria_catalogue import Relationship
    out = cc.start_commit(world["registry"], "db", ME, gateway=fake)          # preview said soft delete
    schema_guid = fake.by_qn("PostgreSQL Relational Database Schema::host.docker.internal:5442::shop.sales")["guid"]
    [item] = out["curation"]["selection"]["leave_out"]
    assert (item["schema"], item["form"], item["plan_guids"][-1]) == ("sales", "soft_delete", schema_guid)
    # a term was assigned to the schema meanwhile: the same elements (the plan holds), something now hangs off it
    fake.rels.setdefault(schema_guid, []).append(Relationship("SemanticAssignment", other_guid="term"))
    cc.execute_commit(world["registry"], out["curation"]["id"], gateway=fake)
    assert {f for _, _, f in fake.deleted_order} == {"archive"}               # something hung off it by then


def test_known_negative_the_cataloguer_filling_the_schema_after_the_preview_is_the_plan_changed(world, fake):
    """Ruling (3), 2026-10-23: the walk now holds GUIDs the preview did not count: refused, nothing sent (this
    test used to expect the safer form to archive the new tables; the plan check now stops it first)."""
    _attached(world, fake, "sales")
    choose(world, "sales", "leave_out")
    out = cc.start_commit(world["registry"], "db", ME, gateway=fake)
    fake._cycle(fake.by_qn("PostgreSQL Relational Database Schema::host.docker.internal:5442::shop.sales")["guid"])
    before = len(fake.ops("delete_element")) + len(fake.ops("remove_catalog_target"))
    rec = cc.execute_commit(world["registry"], out["curation"]["id"], gateway=fake)
    assert fake.deleted_order == [] and len(fake.ops("delete_element")) + len(fake.ops("remove_catalog_target")) == before
    assert step(rec, "leave_outs")["state"] == "failed" and cc.PLAN_CHANGED in step(rec, "leave_outs")["detail"]
    assert derived(world)["schemas"]["sales"]["second"].startswith(cc.PLAN_CHANGED)


def test_a_leave_out_whose_read_fails_at_press_time_deletes_nothing(world, fake):
    _catalogued(world, fake, "sales")
    choose(world, "sales", "leave_out")
    out = cc.start_commit(world["registry"], "db", ME, gateway=fake)
    fake.fail["relationships"] = "503 read failed"
    rec = cc.execute_commit(world["registry"], out["curation"]["id"], gateway=fake)
    assert fake.deleted_order == [] and step(rec, "leave_outs")["state"] == "failed"
    assert derived(world)["schemas"]["sales"]["state"] == "failed"
    assert cc.CANT_CHECK in derived(world)["schemas"]["sales"]["words"]


def test_choosing_again_after_queuing_a_leave_out_does_not_remove_it(world, fake):
    _catalogued(world, fake, "sales")
    choose(world, "sales", "leave_out")
    out = cc.start_commit(world["registry"], "db", ME, gateway=fake)
    choose(world, "sales", "catalogue")                                       # changed their mind
    cc.execute_commit(world["registry"], out["curation"]["id"], gateway=fake)
    assert fake.deleted_order == [] and len(fake.targets) == 1


# ── re-inclusion ─────────────────────────────────────────────────────────────


def test_re_inclusion_after_a_soft_delete_recreates_and_reattaches_with_new_guids(world, fake):
    fake.zone_lockout = False          # not about zones: the configured zone is one this identity may write in
    _attached(world, fake, "sales")
    sqn = "PostgreSQL Relational Database Schema::host.docker.internal:5442::shop.sales"
    old = fake.by_qn(sqn)["guid"]
    choose(world, "sales", "leave_out")
    press(world, fake)
    assert derived(world)["schemas"]["sales"]["state"] == "deleted"
    choose(world, "sales", "catalogue")
    assert "the next commit re-creates it" in derived(world)["schemas"]["sales"]["second"]
    press(world, fake)
    new = fake.read_element(sqn)
    assert new is not None and new.guid != old
    assert [t.element_guid for t in fake.targets] == [new.guid]
    assert derived(world)["schemas"]["sales"]["state"] == "catalogued"


def test_re_inclusion_after_an_archive_needs_confirmation_and_the_rest_proceeds(world, fake):
    """S19 (owner ruling 2026-10-14): the archive renamed the schema, so its name is free. Not created until the
    person confirms; another chosen schema proceeds; forced past the plan the attach still refuses."""
    _catalogued(world, fake, "archive")
    fake.add_term_assignment("orders", "PostgreSQL Relational Database Schema::host.docker.internal:5442::shop.archive")
    choose(world, "archive", "leave_out")
    press(world, fake)
    choose(world, "archive", "catalogue")
    s = derived(world)["schemas"]["archive"]
    assert s["state"] == "archived" and s["second"] == cc.REINCLUDE_TAIL
    p = cc.build_preview(world["registry"], "db", view(world), fake)
    nc = p["needs_confirmation"]
    assert [(r["node"], r["name"], r["date"]) for r in nc] == [("schema", "archive", "2026-10-09")]
    assert nc[0]["text"] == ("archive: archived in Egeria · 2026-10-09 · re-including creates a new element; "
                             "the archived record stays")
    assert "archive" not in p["attach"] and not p["can_commit"]               # nothing else to do
    choose(world, "sales", "catalogue")
    adds_before = len(fake.ops("create_schema_element"))
    press(world, fake)
    assert [c[1] for c in fake.ops("create_schema_element")[adds_before:]] == ["sales"]
    with pytest.raises(cc.SchemaRefused, match="re-including creates a new element"):
        cc.apply_attach(world["registry"], fake, {"slug": "db", "schema": "archive", "database_guid": fake.db_guid})


def test_like_matches_follows_jdbc_patterns():
    assert gw.like_matches("a_b", "aXb") and gw.like_matches("a_b", "a_b") and not gw.like_matches("a_b", "ab")
    assert gw.like_matches("p%t", "plain_t") and not gw.like_matches("p%t", "plain_x")
    assert not gw.like_matches("aXb", "a_b")                                  # only the PATTERN side has wildcards
    assert not gw.like_matches("a.b", "aXb")                                  # a dot is a dot


def test_same_named_tables_in_a_catalogued_and_a_left_out_schema_do_not_block_the_commit(world, fake):
    choose(world, "sales", "catalogue")
    choose(world, "archive", "leave_out")           # sales.orders and archive.orders share a name
    p = cc.build_preview(world["registry"], "db", view(world), fake)
    assert p["blockers"] == [] and p["can_commit"] is True and p["collisions"] == []


def test_schema_collision_a_b_and_aXb_is_flagged_before_the_press_and_disables_it(world, fake):
    choose(world, "a_b", "catalogue")
    p = cc.build_preview(world["registry"], "db", view(world), fake)
    assert [c["text"] for c in p["collisions"] if c["kind"] == "schema"] == ["⚠ Egeria's listing for a_b would also return aXb"]
    assert not p["can_commit"] and "name collision" in p["blockers"][0]
    with pytest.raises(cc.CommitBlocked) as err:
        cc.start_commit(world["registry"], "db", ME, gateway=fake)
    assert err.value.status == 409 and fake.calls == []                       # refused before any Egeria call
    # the way out: leave the wildcard-bearing schema out; the plain name beside it is no hazard
    choose(world, "a_b", "leave_out")
    choose(world, "aXb", "catalogue")
    assert cc.build_preview(world["registry"], "db", view(world), fake)["collisions"] == []


def test_table_collision_x_y_and_xZy_is_flagged_inside_a_chosen_schema(world, fake):
    choose(world, "s_x", "catalogue")
    p = cc.build_preview(world["registry"], "db", view(world), fake)
    tc = [c for c in p["collisions"] if c["kind"] == "table"]
    assert [(c["schema"], c["name"], c["other"]) for c in tc] == [("s_x", "x_y", "xZy")]
    assert not p["can_commit"]
    # a table choice cannot lift it: Egeria catalogues whole schemas, so leaving xZy out of the
    # tree changes nothing about what Egeria's listing for x_y would also return
    choose(world, "s_x", "leave_out", table="xZy")
    assert cc.build_preview(world["registry"], "db", view(world), fake)["collisions"]
    # an unchosen schema's collision is not flagged at all
    choose(world, "s_x", "leave_out")
    choose(world, "sales", "catalogue")
    assert cc.build_preview(world["registry"], "db", view(world), fake)["collisions"] == []


def test_the_real_cataloguer_behaviour_the_check_exists_for(world, fake):
    """The fake reproduces the recorded hazard: a schema target for a_b pulls in aXb's table."""
    cc.apply_attach(world["registry"], fake, {"slug": "db", "schema": "a_b", "database_guid": "x"})
    fake.run_cataloguer()
    assert fake.by_qn("PostgreSQL Relational Database Schema::host.docker.internal:5442::shop.a_b::t_axb") is not None


# ── the comma-bearing schema name ────────────────────────────────────────────


def test_a_comma_schema_attaches_but_cannot_be_scoped_in_the_survey_and_the_manifest_says_so(world, fake):
    choose(world, "sales", "catalogue")
    choose(world, "cm,ma", "catalogue")
    out, rec = press(world, fake, refresh=False)
    assert len(fake.targets) == 2                                              # a schema-kind target takes a comma fine
    assert fake.surveys[0][1] == {"includeSchemaNames": "sales"}               # never "sales,cm,ma"
    text = " ".join(ln["text"] for ln in out["preview"]["manifest"]["lines"])
    assert "Not scopable, a comma in the name: cm,ma" in text
    assert cc.SURVEY_LINE in text
    assert "cm,ma" in step(rec, "survey")["detail"] and "not scopable" in step(rec, "survey")["detail"]


def test_only_comma_schemas_chosen_means_no_survey_not_an_unscoped_one(world, fake):
    choose(world, "cm,ma", "catalogue")
    _, rec = press(world, fake, refresh=False)
    assert fake.surveys == [] and step(rec, "survey")["state"] == "skipped"
    assert "would survey every schema" in step(rec, "survey")["detail"]


def test_survey_schema_list_splits_scopable_from_comma_names():
    assert cc.survey_schema_list(["a", "b,c", "d"]) == (["a", "d"], ["b,c"])


# ── the manifest and the tables line ─────────────────────────────────────────


def test_the_manifest_lists_three_mechanisms_the_target_count_and_the_survey_schemas(world, fake):
    choose(world, "sales", "catalogue")
    choose(world, "archive", "catalogue")
    p = cc.build_preview(world["registry"], "db", view(world), fake)
    m = p["manifest"]
    mech = [ln["text"] for ln in m["lines"] if ln["mechanism"]]
    assert len(mech) == 3
    assert "RE publishes" in mech[0] and "zone-a, zone-b" in mech[0] and "LAST write" in mech[0]
    assert "2 schema targets (2 to attach now)" in mech[1] and "never the database or the server" in mech[1]
    assert "next refresh, not now" in mech[1]
    assert mech[2].startswith("Egeria's survey is limited to your chosen schemas: sales, archive")
    # its report is another report than RE's, and fills later (item 3: a "0 annotations" report is not RE's)
    assert "separate from RE's survey report" in mech[2] and "fills when Egeria's survey finishes" in mech[2]
    assert m["schema_targets"] == 2 and m["survey_schemas"] == ["sales", "archive"]
    assert m["whole_schemas_line"] == "Egeria adds whole schemas · table choices are kept for when it can"
    assert p["button"] == "Publish · 2 schemas" and p["can_commit"]


def test_table_choices_are_kept_and_say_egeria_catalogues_whole_schemas(world, fake):
    choose(world, "sales", "catalogue")
    choose(world, "sales", "leave_out", table="customers")
    press(world, fake)
    d = derived(world)
    # the whole schema is catalogued, the left-out table included: the tree says so
    assert d["tables"]["sales.customers"]["state"] == "catalogued"
    assert d["tables"]["sales.customers"]["second"] == cc.WHOLE_SCHEMAS_LINE
    # and the choice itself is untouched in RE's record
    assert next(t for t in next(s for s in view(world)["schemas"] if s["name"] == "sales")["tables"]
                if t["name"] == "customers")["effective"] == "leave_out"


def test_undecided_schemas_already_in_egeria_stay_targets_and_in_the_survey(world, fake):
    choose(world, "sales", "catalogue")
    press(world, fake, refresh=False)
    cs.clear_node_choice(world["registry"], "db", ME, schema="sales")            # now undecided
    choose(world, "archive", "catalogue")
    p = cc.build_preview(world["registry"], "db", view(world), fake)
    assert p["attach"] == ["archive", "sales"] and p["leave_out"] == []
    assert p["survey"]["schemas"] == ["archive", "sales"]


def test_nothing_to_commit_is_a_blocker_not_an_empty_run(world, fake):
    p = cc.build_preview(world["registry"], "db", view(world), fake)
    assert not p["can_commit"] and p["blockers"] == ["nothing to commit: choose at least one schema to publish"]


def test_signed_out_commit_is_refused(world, fake):
    with pytest.raises(cc.CommitBlocked) as err:
        cc.start_commit(world["registry"], "db", "", gateway=fake)
    assert err.value.status == 401


# ── the outbox kinds ─────────────────────────────────────────────────────────


def test_the_catalogue_kinds_are_self_resolving_so_an_existing_element_does_not_skip_the_attach(world, fake):
    """apply_element's generic step 2 would adopt a schema element found by name and mark the row done
    without ever attaching the target, the half that matters."""
    from resource_explorer import egeria_outbox as ob
    choose(world, "sales", "catalogue")
    fake.publish_database(entity(world), "", "")
    # the element already exists in Egeria under that qualified name
    eg = fake.create_schema_element(entity(world), "sales", fake.db_guid)
    row = {"id": 1, "element_kind": cc.KIND_ATTACH, "qualified_name": "...", "egeria_guid": "",
           "payload_json": '{"slug": "db", "schema": "sales", "database_guid": "%s"}' % fake.db_guid}
    got = ob.apply_element(row, ob.OutboxClients(catalogue_gateway=fake, registry=world["registry"]),
                           lambda qn: eg)        # a lookup that WOULD find it
    assert got == eg and len(fake.targets) == 1


def test_the_default_drain_gives_the_catalogue_kinds_the_registry(world, fake, monkeypatch):
    from resource_explorer import egeria_outbox as ob
    choose(world, "sales", "catalogue")
    fake.publish_database(entity(world), "", "")
    world["registry"].enqueue_outbox_element(
        "database", "db", cc.KIND_ATTACH, "q", {"slug": "db", "schema": "sales", "database_guid": fake.db_guid}, run_id="r")
    seen = {}

    def one(row, clients, find, resolve_row_guids=None):
        seen["registry"] = clients.registry
        return "g"
    monkeypatch.setattr(ob, "apply_element", one)
    ob.drain_outbox(world["registry"], ob.OutboxClients(catalogue_gateway=fake), lambda qn: "")
    assert seen["registry"] is world["registry"]


# ── storage: additive, and opens an old-shape database ───────────────────────


def test_migration_is_additive_and_an_old_shape_database_opens(tmp_path):
    path = str(tmp_path / "old.db")
    r = ProjectRegistry(db_path=path)
    r.register_database(DatabaseEntity(slug="db", display_name="Shop", db_type="postgresql", host="h", port=5432,
                                       database_name="shop"))
    r.append_catalogue_scope_event("db", node_kind="schema", schema_name="sales", choice="catalogue", author=ME)
    # make it the OLD shape: the table did not exist before this slice
    con = sqlite3.connect(path)
    con.execute("DROP TABLE catalogue_commit_proofs")
    con.commit()
    con.close()
    old = sqlite3.connect(path)
    assert "catalogue_commit_proofs" not in {t[0] for t in old.execute("SELECT name FROM sqlite_master")}
    old.close()
    r2 = ProjectRegistry(db_path=path)                                       # opening it migrates it
    assert r2.list_catalogue_commit_proofs("db") == []
    r2.append_catalogue_commit_proof("db", proof=cc.P_TARGET, schema_name="sales", detail={"k": 1})
    assert r2.list_catalogue_commit_proofs("db")[0]["detail"] == {"k": 1}
    # nothing existing was touched
    assert r2.get_database("db") is not None and len(r2.list_catalogue_scope_events("db")) == 1
    con = sqlite3.connect(path)
    cols = [c[1] for c in con.execute("PRAGMA table_info(catalogue_commit_proofs)")]
    assert cols[:3] == ["id", "database_slug", "curation_id"]
    con.close()


def test_removing_a_database_removes_its_proofs(registry):
    registry.append_catalogue_commit_proof("db", proof=cc.P_TARGET, schema_name="s")
    registry.remove_database("db")
    assert registry.list_catalogue_commit_proofs("db") == []


# ── routes ───────────────────────────────────────────────────────────────────


@pytest.fixture
def client(registry, world, fake, monkeypatch):
    from fastapi.testclient import TestClient
    monkeypatch.setattr("resource_explorer.registry.ProjectRegistry.__init__",
                        lambda self, db_path=None: setattr(self, "__dict__", registry.__dict__) or None)
    from resource_explorer import auth
    from resource_explorer.web.app import app
    from resource_explorer.web.routes import catalogue_scope as routes
    monkeypatch.setattr(routes, "get_current_user", auth.get_current_user)
    monkeypatch.setattr(cc, "make_gateway", lambda entity: fake)
    return TestClient(app)


def as_user(u):
    from resource_explorer.auth import create_access_token
    return {"Authorization": "Bearer " + create_access_token(user_id=u, egeria_token="t")}


def test_the_scope_read_carries_every_state_and_the_header_from_rows_only(client, world, fake):
    choose(world, "sales", "catalogue")
    j = client.get("/api/catalogue-scope/db").json()
    assert j["commit"]["header"]["text"] == cc.NOT_COMMITTED_HEADER
    assert j["commit"]["schemas"]["sales"]["state"] == "uncommitted"
    assert fake.calls == []                                                    # opening Curate never touches Egeria


def test_preview_route_reads_egeria_and_writes_nothing(client, world, fake):
    _attached(world, fake, "sales")
    choose(world, "sales", "leave_out")
    n = len(fake.ops("delete_element"))
    j = client.get("/api/catalogue-scope/db/commit-preview").json()
    assert j["leave_out"][0]["form"] == "soft_delete" and len(fake.ops("delete_element")) == n
    assert j["manifest"]["lines"][0]["mechanism"] == 1


def test_commit_route_signed_out_is_401_and_collision_is_409_and_nothing_is_queued(client, world, fake, registry):
    choose(world, "a_b", "catalogue")
    assert client.post("/api/catalogue-scope/db/commit", json={}).status_code == 401
    r = client.post("/api/catalogue-scope/db/commit", json={}, headers=as_user(ME))
    assert r.status_code == 409 and "name collision" in r.json()["detail"]
    with registry._conn() as conn:
        assert conn.execute("SELECT COUNT(*) AS n FROM runs").fetchone()["n"] == 0
    assert registry.list_catalogue_outbox_rows("db") == [] and fake.calls == []


def test_commit_route_queues_a_curation_and_a_run_and_the_record_shows_the_steps(client, world, fake, registry):
    choose(world, "sales", "catalogue")
    r = client.post("/api/catalogue-scope/db/commit", json={"refresh_now": True}, headers=as_user("alice"))
    assert r.status_code == 200, r.text
    cid = r.json()["curation"]["id"]
    assert r.json()["curation"]["author"] == "alice" and r.json()["curation"]["state"] == "queued"
    with registry._conn() as conn:
        run = dict(conn.execute("SELECT kind, target, requested_by FROM runs WHERE id = ?", (r.json()["run_id"],)).fetchone())
    assert run["kind"] == "catalogue_commit" and cid in run["target"] and run["requested_by"] == "alice"
    # the worker runs the handler; the record the screen polls then carries the steps
    from resource_explorer import run_queue
    out = run_queue.HANDLERS["catalogue_commit"]({"slug": "db", "curation_id": cid}, "")
    assert out.state == "succeeded", out.error
    rec = client.get(f"/api/catalogue-scope/db/commits/{cid}").json()
    assert [s["state"] for s in rec["steps"]].count("failed") == 0 and rec["state"] == "done"
    assert client.get("/api/catalogue-scope/db/commits").json()["commits"][0]["id"] == cid
    assert client.get("/api/catalogue-scope/db/commits/nope").status_code == 404
    j = client.get("/api/catalogue-scope/db").json()
    assert j["commit"]["schemas"]["sales"]["state"] == "catalogued"            # refresh_now was honoured


def test_read_back_route_needs_a_person_and_records_proof(client, world, fake):
    _catalogued(world, fake, "sales")
    fake.refreshes = 0
    assert client.post("/api/catalogue-scope/db/read-back").status_code == 401
    r = client.post("/api/catalogue-scope/db/read-back", headers=as_user(ME))
    assert r.status_code == 200 and r.json()["catalogued"] == 1
    # the button's status line is the commit step's chips text, never "waiting"
    assert r.json()["chips_words"] == cc.read_back_words(cc.read_back_chips_for(world["registry"], "db", ["sales"]))
    assert "waiting" not in r.json()["chips_words"]


# ── the real gateway's wire shapes ───────────────────────────────────────────


@pytest.fixture
def real(monkeypatch):
    g = gw.PyegeriaCatalogueGateway(entity_stub())
    clients = {n: MagicMock(name=n) for n in ("AssetMaker", "MetadataExpert", "AutomatedCuration", "ServerOps")}
    g._clients.update(clients)
    return g, clients


def entity_stub():
    return DatabaseEntity(slug="db", display_name="Shop", db_type="postgresql", host="localhost",
                          egeria_host="host.docker.internal", port=5442, database_name="shop")


def test_gateway_reads_the_targets_with_the_body_pyegeria_s_default_lacks(real):
    g, c = real
    c["AssetMaker"].get_catalog_targets.return_value = "No elements found"
    assert g.list_catalog_targets() == []
    args, kwargs = c["AssetMaker"].get_catalog_targets.call_args
    assert args[0] == gw.JDBC_CATALOGUER_GUID == "70dcd0b7-9f06-48ad-ad44-ae4d7a7762aa"
    assert kwargs["body"] == {"class": "ResultsRequestBody", "graphQueryDepth": 0}


def test_gateway_attaches_a_schema_kind_target_with_no_lists_and_archive_delete_method(real):
    g, c = real
    c["AssetMaker"].add_catalog_target.return_value = "rel-1"
    g.add_catalog_target("schema-guid", "shop_schema_sales")
    connector, element, body = c["AssetMaker"].add_catalog_target.call_args[0]
    assert (connector, element) == (gw.JDBC_CATALOGUER_GUID, "schema-guid")
    props = body["properties"]
    assert props["deleteMethod"] == "ARCHIVE" and "configurationProperties" not in props
    assert props["catalogTargetName"] == "shop_schema_sales"


def test_gateway_creates_the_schema_from_the_template_by_guid_under_the_database(real):
    g, c = real
    c["AutomatedCuration"].create_elem_from_template.return_value = "new-guid"
    assert g.create_schema_element(entity_stub(), "sales", "db-guid") == "new-guid"
    body = c["AutomatedCuration"].create_elem_from_template.call_args[0][0]
    assert body["templateGUID"] == gw.SCHEMA_TEMPLATE_GUID == "82a5417c-d882-4271-8444-4c6a996a8bfc"
    ph = body["placeholderPropertyValues"]
    assert ph["schemaName"] == "sales" and ph["serverName"] == "host.docker.internal:5442"
    assert ph["versionIdentifier"] and "~{" not in str(ph)
    assert body["parentGUID"] == "db-guid"


def test_gateway_archive_is_the_delete_endpoint_with_lineage_flags_never_the_archive_endpoint(real):
    g, c = real
    g.delete_element("guid-1", gw.ARCHIVE, slug="db")
    args, kwargs = c["MetadataExpert"].delete_metadata_element.call_args
    assert args[0] == "guid-1" and args[1]["deleteMethod"] == "ARCHIVE"
    assert args[1]["forLineage"] is True and args[1]["forDuplicateProcessing"] is True
    assert kwargs["cascade_delete"] is False
    c["MetadataExpert"].archive_metadata_element.assert_not_called()          # the /archive endpoint answers 500


def test_gateway_soft_delete_is_per_element_with_lineage_true_and_no_cascade(real):
    g, c = real
    g.delete_element("guid-2", gw.SOFT_DELETE, slug="db")
    args, kwargs = c["MetadataExpert"].delete_metadata_element.call_args
    assert args[1]["deleteMethod"] == "SOFT_DELETE" and args[1]["forLineage"] is True
    assert kwargs["cascade_delete"] is False


def test_gateway_survey_passes_include_schema_names_as_a_request_parameter(real):
    g, c = real
    ac = c["AutomatedCuration"]
    ac.ref_curation_command_base = "http://x/api"
    resp = MagicMock()
    resp.json.return_value = {"guid": "act-1"}

    def make(method, url, body):
        make.seen = (method, url, body)
        return resp
    ac.make_request = make
    from resource_explorer.run_queue import requested_by  # noqa: F401 (import path sanity)
    guid, qn = g.initiate_survey("db-guid", {"includeSchemaNames": "sales,archive"})
    method, url, body = make.seen
    assert guid == "act-1" and qn == PROCESS_QN
    assert body["requestParameters"] == {"includeSchemaNames": "sales,archive"}
    assert body["actionTargets"][0]["actionTargetGUID"] == "db-guid"
    assert url.endswith("/governance-action-types/initiate")


def test_gateway_refresh_names_the_exact_connector_and_never_restarts(real):
    g, c = real
    g.refresh_connector(120)
    c["ServerOps"].refresh_integration_connectors.assert_called_once_with(
        "JDBCDatabaseCataloguer", "qs-integration-daemon", 120)
    c["ServerOps"].restart_integration_connector.assert_not_called()


def test_gateway_reads_the_connector_time_as_the_connector_s():
    status = {"integrationConnectorReports": [
        {"connectorName": "OpenAPICataloguer", "lastRefreshTime": "x"},
        {"connectorName": "JDBCDatabaseCataloguer", "connectorStatus": "WAITING", "lastRefreshTime": "2026-10-05T02:18:59Z"}]}
    got = gw.parse_connector_status(status, "JDBCDatabaseCataloguer")
    assert got.last_refresh_time == "2026-10-05T02:18:59Z" and got.name == "JDBCDatabaseCataloguer"
    assert gw.parse_connector_status("No Integration Groups", "JDBCDatabaseCataloguer") is None


def test_the_database_template_gets_database_description_and_version_so_no_placeholder_remains():
    """S15: the placeholder is `databaseDescription`, not `description`; `versionIdentifier` was never supplied."""
    from resource_explorer.surveyors.database.egeria_database_surveyor import EgeriaDatabaseSurveyor
    s = EgeriaDatabaseSurveyor(platform_url="http://x")
    s._automated_curation = MagicMock()
    # A real miss is pyegeria's "No elements found"; a bare MagicMock answer is not one (round 3: odd answers are
    # could-not-check, never "create").
    s._automated_curation.get_guid_for_name.return_value = "No elements found"
    from unittest.mock import create_autospec
    from pyegeria import MetadataExpert
    s._metadata_expert = create_autospec(MetadataExpert, spec_set=True, instance=True)
    expert_from_legacy(s._automated_curation, s._metadata_expert)
    s._metadata_expert.find_metadata_elements_with_string.return_value = "No elements found"   # S19: no archived copy
    s.connect = lambda: None
    s._find_element_guid = lambda name: ""
    s._save_database_secret = lambda *a, **k: ("", "")
    calls = []
    s._create_postgres_element_from_template = lambda tech, ph: (calls.append((tech, ph)) or f"guid-{len(calls)}")
    s._warn_if_database_has_no_connection = lambda *a, **k: None
    s._catalog_and_survey(entity_stub(), "u", "p", registry=None, survey_after_catalog=False)
    ph = dict(calls)["PostgreSQL Relational Database"]
    assert ph["databaseDescription"] and ph["versionIdentifier"] and ph["description"]


# ── architect rulings, 2026-10-05 ────────────────────────────────────────────


def test_the_manifest_names_the_schemas_not_committed_in_one_sentence_form(world, fake):
    _catalogued(world, fake, "sales", "archive")
    choose(world, "sales", "leave_out")
    fake.fail["relationships"] = "503 read failed"
    p = cc.build_preview(world["registry"], "db", view(world), fake)
    nc = [ln["text"] for ln in p["manifest"]["lines"] if ln["id"] == "not_committed"]
    assert nc == ["1 schema not committed: sales · couldn't check what hangs off it"]
    assert p["manifest"]["not_committed"][0]["schemas"] == ["sales"] and p["can_commit"]     # the rest proceeds


def test_refused_reinclusion_and_a_failed_read_are_both_named_and_collisions_still_block_everything(world, fake):
    _catalogued(world, fake, "archive")
    fake.add_term_assignment("orders", "PostgreSQL Relational Database Schema::host.docker.internal:5442::shop.archive")
    choose(world, "archive", "leave_out")
    press(world, fake)
    choose(world, "archive", "catalogue")
    choose(world, "sales", "catalogue")
    p = cc.build_preview(world["registry"], "db", view(world), fake)
    assert ("1 schema not committed: archive · archived in Egeria · re-including creates a new element; the archived "
            "record stays · confirm Re-include") in [ln["text"] for ln in p["manifest"]["lines"]]
    assert p["can_commit"] and p["attach"] == ["sales"]
    choose(world, "a_b", "catalogue")                                          # a collision blocks the whole commit
    assert not cc.build_preview(world["registry"], "db", view(world), fake)["can_commit"]


def test_the_commit_with_no_scope_declared_writes_nothing_to_egeria(world, fake, monkeypatch):
    """The survey-definition retry goes through the commit: with no scope declared it stops, makes no
    gateway and no Egeria call, queues no outbox row and no run."""
    monkeypatch.setattr(cc, "make_gateway", lambda e: pytest.fail("no gateway may even be built"))
    with pytest.raises(cc.CommitBlocked) as err:
        cc.start_commit(world["registry"], "db", ME)
    assert err.value.status == 409 and err.value.message == "no scope declared · nothing published"
    assert fake.calls == [] and world["registry"].list_catalogue_outbox_rows("db") == []
    assert world["registry"].list_catalogue_commit_proofs("db") == []
    with world["registry"]._conn() as conn:
        assert conn.execute("SELECT COUNT(*) AS n FROM runs").fetchone()["n"] == 0


def test_commit_route_with_no_scope_is_409_with_the_sentence_and_no_egeria_call(client, world, fake):
    r = client.post("/api/catalogue-scope/db/commit", json={}, headers=as_user(ME))
    assert r.status_code == 409 and r.json()["detail"] == "no scope declared · nothing published"
    assert fake.calls == []


def test_a_leave_out_is_in_the_manifest_once_as_its_own_row_not_twice(world, fake):
    choose(world, "sales", "catalogue")
    choose(world, "archive", "leave_out")                    # never catalogued: "nothing to remove"
    p = cc.build_preview(world["registry"], "db", view(world), fake)
    row_text = p["leave_out"][0]["text"]
    assert row_text == "archive: nothing to remove · never published"
    manifest = " ".join(ln["text"] for ln in p["manifest"]["lines"])
    assert "nothing to remove" not in manifest                # the rows carry it, once
    assert all(ln["id"] != "leave_out" for ln in p["manifest"]["lines"])


# ── RE's own survey report: the words say what came back (coco_pharma, 2026-10-06) ──────────────


def _measured(world):
    world["registry"].record_database_survey("db", 2, 5, 9, {"schema_info": {"sales": {}}}, source="local",
                                             surveyed_at="2026-10-03T18:07:08")


def test_report_step_with_annotations_and_no_guid_says_what_came_back_not_report_not_found(world, fake, monkeypatch):
    _measured(world)
    monkeypatch.setattr(fake, "publish_local_report",
                        lambda *a, **k: {"annotation_count": 76, "asset_guid": "d1"})   # annotations only, no report element id
    choose(world, "sales", "catalogue")
    _, rec = press(world, fake)
    st = step(rec, "survey_report")
    assert st["state"] == "done"
    assert "report not found" not in st["detail"] and "report ?" not in st["detail"]
    assert st["detail"] == ("76 annotations from the 10-03 survey · Egeria returned no report element id, "
                            "so the report itself is not confirmed")


def test_report_step_with_a_guid_names_it_and_the_survey_it_came_from(world, fake):
    _measured(world)
    choose(world, "sales", "catalogue")
    _, rec = press(world, fake)
    d = step(rec, "survey_report")["detail"]
    assert d.startswith("report ") and "3 annotations published · from the 10-03 survey" in d


def test_report_step_reports_the_swallowed_publish_error_as_a_failure(world, fake, monkeypatch):
    _measured(world)
    monkeypatch.setattr(fake, "publish_local_report",
                        lambda *a, **k: {"annotation_count": 76, "report_guid": "", "report_error": "403 not authorized"})
    choose(world, "sales", "catalogue")
    _, rec = press(world, fake)
    st = step(rec, "survey_report")
    assert st["state"] == "failed" and "403 not authorized" in st["detail"]
    assert "76 annotations built, none published" in st["detail"]


def test_report_step_says_report_not_found_only_when_the_publish_returned_nothing(world, fake, monkeypatch):
    _measured(world)
    monkeypatch.setattr(fake, "publish_local_report", lambda *a, **k: {})
    choose(world, "sales", "catalogue")
    _, rec = press(world, fake)
    assert step(rec, "survey_report")["detail"] == "report not found · the publish returned nothing"


# ── Egeria 6.2 element header (tests/fixtures/egeria_6_2, captured live read-only 2026-10-14) ────────────────


def _captured_6_2(name):
    import json
    return json.loads((Path(__file__).parent / "fixtures" / "egeria_6_2" / f"{name}.json").read_text())["answer"]


@pytest.mark.parametrize("captured", ["asset_maker_by_guid_for_lineage__memento",                    # 6.2 header field
                                      "metadata_expert_by_unique_name_for_lineage__memento_archived_qn"])  # the list
def test_s19_needs_confirmation_when_the_archived_copy_is_in_either_captured_shape(world, fake, monkeypatch, captured):
    """The captured 6.2 Memento, renamed to the schema's own `_archivedOn_` name, answers the sibling find: the attach
    refuses with the date from the suffix and creates nothing."""
    _catalogued(world, fake, "archive")
    sqn = "PostgreSQL Relational Database Schema::host.docker.internal:5442::shop.archive"
    raw = _captured_6_2(captured)
    archived_qn = f"{sqn}_archivedOn_Fri Oct 09 01:28:08 GMT 2026"
    if "properties" in raw:
        raw["properties"]["qualifiedName"] = archived_qn
    else:
        raw["elementProperties"]["propertiesAsStrings"]["qualifiedName"] = archived_qn
    monkeypatch.setattr(fake, "read_element", lambda qn, *, for_lineage=False: None)
    monkeypatch.setattr(fake, "archived_siblings", lambda qn: gw.parse_archived_siblings([raw], qn))
    adds_before = len(fake.ops("create_schema_element"))
    with pytest.raises(cc.SchemaRefused, match="archived in Egeria · 2026-10-09 · re-including creates a new element"):
        cc.apply_attach(world["registry"], fake, {"slug": "db", "schema": "archive", "database_guid": fake.db_guid})
    assert len(fake.ops("create_schema_element")) == adds_before


def test_report_step_records_a_proof_per_declared_measure_from_the_read_back(world, fake, monkeypatch):
    """Curate's publish table (declared extensibility slice 1): the measures the report carried each leave a proof
    row, `measure_read_back` with the annotation's GUID the report read found, `measure_not_read_back` without."""
    from resource_explorer import publish_table as pt

    _measured(world)
    sent = [{"analysis_id": "example_step_x", "measure": "rows_sampled", "value": 1200, "unit": "rows",
             "as_of": "2026-10-27T08:30:00", "route": "generic", "type": "ResourceMeasureAnnotation",
             "qualified_name": "Annotation::q1", "guid": "ann-1", "why": ""},
            {"analysis_id": "example_step_x", "measure": "buckets", "value": 2, "unit": "buckets",
             "as_of": "2026-10-27T08:30:00", "route": "generic", "type": "ResourceMeasureAnnotation",
             "qualified_name": "Annotation::q2", "guid": "", "why": "the report read did not show this annotation"}]
    monkeypatch.setattr(fake, "publish_local_report", lambda *a, **k: {
        "report_guid": "r1", "report_element_guid": "r1", "annotation_count": 5, "annotations_in_egeria": 5,
        "declared_measures": {"state": "sent", "sent": sent}})
    choose(world, "sales", "catalogue")
    _, rec = press(world, fake)
    assert step(rec, "survey_report")["detail"].endswith("1 of 2 declared measures read back")
    proofs = {p["table_name"]: p for p in world["registry"].list_catalogue_commit_proofs("db")
              if p["node_kind"] == pt.NODE_MEASURE}
    assert proofs["rows_sampled"]["proof"] == pt.P_MEASURE_READ_BACK
    assert proofs["rows_sampled"]["element_guid"] == "ann-1" and proofs["rows_sampled"]["target_guid"] == "r1"
    assert proofs["rows_sampled"]["curation_id"] == rec["id"] and proofs["rows_sampled"]["recorded_by"] == ME
    assert proofs["buckets"]["proof"] == pt.P_MEASURE_NOT_READ_BACK
    # A measure proof is not a database node: the commit's own state word ignores it.
    assert all(p["schema_name"] == "example_step_x" for p in proofs.values())


def test_the_survey_step_names_the_report_the_measures_went_into_and_says_its_own_is_separate():
    """A commit's engine survey adds its own report ("0 annotations" until it finishes); RE's measures are in another."""
    from resource_explorer.catalogue_commit import _submitted_words

    words = _submitted_words("2026-10-29T10:00:00", ["sales"], [], "abcdef0123456789", "fb6d96f4aaaa")
    assert "RE's measures are in report fb6d96f4" in words
    assert "separate from RE's survey report" in words and "fills when Egeria's survey finishes" in words
    assert "RE's measures are in report" not in _submitted_words("2026-10-29T10:00:00", ["sales"], [], "abcdef01")
