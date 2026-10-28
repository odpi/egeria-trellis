"""Catalogue scope for a database, slice A (storage, routes, proposals, states).

the designer reply on catalogue scope (page 18 of the CatalogueScope wireframe). Never touches the shared
registry: every test uses a tmp SQLite file, asserted below. Nothing here
reaches Egeria, and a test asserts the module cannot.
"""
from __future__ import annotations

import inspect

import pytest
from fastapi.testclient import TestClient

from resource_explorer import catalogue_scope as cs
from resource_explorer.auth import create_access_token
from resource_explorer.registry import DatabaseEntity, ProjectRegistry

SURVEY_AT = "2026-10-02T09:00:00"


def tbl(name, rows=10, table_type="BASE TABLE"):
    return {"name": name, "table_type": table_type, "row_count": rows, "row_count_state": "measured",
            "size_bytes": 100, "column_count": 1,
            "columns": [{"name": "id", "type": "integer", "nullable": False, "key_role": "PK",
                         "foreign_key": None, "comment": ""}]}


def sch(name, tables=(), cls=None, table_count=None):
    tables = list(tables)
    if cls is None:
        cls = "data" if tables else "no_tables"
    return {"schema": name, "table_count": len(tables) if table_count is None else table_count,
            "row_total": sum(t["row_count"] or 0 for t in tables) if tables else None,
            "bytes_total": None, "is_estimate": False, "classification": cls, "reason": "",
            "tables": tables}


SYSTEM = {"schema": None, "table_count": None, "row_total": None, "bytes_total": None,
          "is_estimate": False, "reason": "", "classification": "system", "system_count": 3}


@pytest.fixture(autouse=True)
def _curation_access_reads(monkeypatch):
    """Brief Z round 2: the scope routes check curation access on the database element, which the fake
    Egeria answers with no ZoneMembership: open."""
    from tests.zone_fakes import install

    return install(monkeypatch)


@pytest.fixture
def registry(tmp_path):
    db = str(tmp_path / "scope.db")
    r = ProjectRegistry(db_path=db)
    # the tests must never be able to open the shared registry
    assert r.database_url == f"sqlite:///{db}" and str(tmp_path) in r.database_url
    r.register_database(DatabaseEntity(
        slug="db", display_name="db", db_type="postgresql", host="localhost", port=5442,
        database_name="db", egeria_asset_guid="abcdef12-0000-0000-0000-000000000000"))
    return r


@pytest.fixture
def world(registry, monkeypatch):
    """A mutable tree and fixed measurement dates, injected under the module."""
    w = {"tree": {"schemas": [sch("sales", [tbl("orders"), tbl("customers")]),
                              sch("archive", [tbl("orders"), tbl("old_stuff")]),
                              sch("empty_one"), SYSTEM]}}
    monkeypatch.setattr(cs, "_load_tree", lambda reg, slug: w["tree"])
    w["survey_at"] = SURVEY_AT
    monkeypatch.setattr(cs, "_tree_survey_at", lambda reg, slug: w["survey_at"])
    monkeypatch.setattr(cs, "_activity_counters", lambda reg, slug: w.get("activity", {}))
    monkeypatch.setattr(cs, "_row_sources", lambda reg, slug: w.get("row_sources", {}))
    w["registry"] = registry
    return w


def view(w, **kw):
    return cs.build_scope_view(w["registry"], "db", **kw)


def node(v, schema, table=None):
    s = next(x for x in v["schemas"] if x["name"] == schema)
    return s if table is None else next(t for t in s["tables"] if t["name"] == table)


def counters(tables, writes=0, reset="2026-10-01T00:00:00", at=SURVEY_AT):
    """Cumulative write counters for `tables` [(schema, table)], as the stored activity rows carry them."""
    return {(s, t): {"writes": writes, "reset": reset, "at": at, "source": "local"} for s, t in tables}


# ── storage: tables, migrations ──────────────────────────────────────────────

def test_the_registry_under_test_is_a_temp_sqlite_file(registry, tmp_path):
    assert registry.database_url.startswith("sqlite:///") and str(tmp_path) in registry.database_url
    assert "5442" not in registry.database_url and "5432" not in registry.database_url


def test_migration_is_idempotent_and_additive(tmp_path):
    db = str(tmp_path / "m.db")
    ProjectRegistry(db_path=db)
    r = ProjectRegistry(db_path=db)   # second init over the same file
    r2 = ProjectRegistry(db_path=db)
    for table, cols in (("catalogue_scope_events", {"database_slug", "node_kind", "schema_name",
                         "table_name", "choice", "action", "source", "proposal_rule",
                         "proposal_choice", "reason", "measured_at", "measured_json",
                         "author", "changed_at"}),
                        ("catalogue_scope_baselines", {"database_slug", "kind", "baseline_json",
                         "survey_at", "declared_by", "declared_at"})):
        with r2._conn() as conn:
            assert cols <= set(r2._get_table_columns(conn, table))
    assert r.list_catalogue_scope_events("db") == []


def test_events_are_append_only_and_need_an_author(registry):
    registry.append_catalogue_scope_event("db", node_kind="schema", schema_name="s",
                                          choice="catalogue", author="a")
    registry.append_catalogue_scope_event("db", node_kind="schema", schema_name="s",
                                          choice="leave_out", author="b")
    ev = registry.list_catalogue_scope_events("db")
    assert [(e["choice"], e["author"]) for e in ev] == [("catalogue", "a"), ("leave_out", "b")]
    with pytest.raises(ValueError):
        registry.append_catalogue_scope_event("db", node_kind="schema", schema_name="s",
                                              choice="catalogue", author="")


def test_this_module_cannot_reach_egeria():
    src = inspect.getsource(cs)
    for needle in ("pyegeria", "egeria_outbox", "requests", "httpx", "publish("):
        assert needle not in src


# ── proposals: one test per rule, with negatives ─────────────────────────────

def test_rule_empty_schema_proposes_leave_out_with_measurement_date(world):
    v = view(world)
    p = node(v, "empty_one")["proposal"]
    assert (p["rule"], p["choice"]) == ("empty_schema", "leave_out")
    assert p["reason"] == "0 tables, measured 10-02"
    assert node(v, "empty_one")["state"] == "proposed"
    assert node(v, "empty_one")["effective"] is None   # unconfirmed: still undecided


def test_rule_empty_schema_no_access_proposes_nothing(world):
    world["tree"]["schemas"].insert(0, sch("locked", cls="no_access", table_count=0))
    n = node(view(world), "locked")
    assert n["proposal"] is None and n["state"] == "undecided"
    assert n["access"] == "not_established"


def test_two_surveys_three_days_apart_with_idle_counters_propose_nothing(world):
    """The two false leave-out proposals of the first real use: coco_ods, eu_sales,
    target_sales and us_sales read "no writes since 09-30 (survey of 10-03)". Counters
    reset 10-03 and the survey is 10-03/10-04: two days of evidence is "can't tell"."""
    world["survey_at"] = "2026-10-04T09:00:00"
    world["activity"] = counters([(sn, t) for sn in ("archive", "sales") for t in ("orders", "old_stuff", "customers")],
                                 writes=0, reset="2026-10-03T07:30:00", at="2026-10-04T09:00:00")
    v = view(world)
    for sname in ("archive", "sales"):
        n = node(v, sname)
        assert n["proposal"] is None and n["live_proposal"] is None and n["state"] == "undecided"
        assert n["last_write"]["state"] == "cant_tell"
        for t in n["tables"]:
            assert t["proposal"] is None and t["state"] == "undecided"
    assert not any("no writes since" in str(n) for n in v["schemas"])


def test_the_activity_words_and_their_windows(world):
    world["survey_at"] = "2026-10-04T09:00:00"
    at = "2026-10-04T09:00:00"
    world["activity"] = {
        **counters([("archive", "orders")], writes=0, reset="2025-11-02T00:00:00", at=at),     # 336 days, zero
        **counters([("archive", "old_stuff")], writes=1204, reset="2026-06-02T00:00:00", at=at),
        **counters([("sales", "orders")], writes=0, reset="2026-10-03T07:30:00", at=at),       # 1 day
        **counters([("sales", "customers")], writes=0, reset="", at=at),                       # no reset date
    }
    v = view(world)
    assert node(v, "archive", "orders")["last_write"]["text"] == "dormant · 0 writes in 336 days (counters reset 2025-11-02)"
    assert node(v, "archive", "old_stuff")["last_write"]["text"] == "active · 1,204 writes since counters reset 06-02"
    assert node(v, "sales", "orders")["last_write"]["text"] == "can't tell · counters reset 10-03 · 1 day of evidence"
    assert node(v, "sales", "customers")["last_write"]["text"] == "can't tell · reset date not recorded"
    assert node(v, "archive", "old_stuff")["last_write"]["state"] == "active"
    # a table with no counters at all
    assert node(v, "empty_one")["last_write"]["text"] == "can't tell · counters not measured"
    # schema roll-up: one active table makes it active; mixed dormant+can't-tell is can't tell
    assert node(v, "archive")["last_write"]["state"] == "active"
    assert node(v, "sales")["last_write"]["state"] == "cant_tell"


def test_a_dormant_table_proposes_leave_out_and_the_reason_is_the_window(world):
    world["survey_at"] = "2026-10-04T09:00:00"
    world["activity"] = counters([("archive", "orders"), ("archive", "old_stuff")], writes=0,
                                 reset="2025-11-02T00:00:00", at="2026-10-04T09:00:00")
    v = view(world)
    n = node(v, "archive")
    assert (n["proposal"]["rule"], n["proposal"]["choice"]) == ("dormant", "leave_out")
    assert n["proposal"]["reason"] == "dormant, 0 writes in 336 days"
    assert node(v, "archive", "orders")["proposal"]["rule"] == "dormant"
    assert node(v, "sales")["proposal"] is None                       # no counters: can't tell


def test_the_dormancy_threshold_is_what_separates_dormant_from_cant_tell(world, monkeypatch):
    c = counters([("archive", "orders"), ("archive", "old_stuff")], writes=0, reset="2026-07-06T00:00:00",
                 at="2026-10-04T09:00:00")          # 90 days exactly
    world["survey_at"] = "2026-10-04T09:00:00"
    world["activity"] = c
    assert node(view(world), "archive")["last_write"]["state"] == "dormant"
    monkeypatch.setattr(cs, "DORMANCY_DAYS", 91)
    assert node(view(world), "archive")["last_write"]["state"] == "cant_tell"
    assert node(view(world), "archive")["proposal"] is None


def test_an_active_table_never_proposes_and_activity_is_not_the_two_survey_delta(world):
    world["survey_at"] = "2026-10-04T09:00:00"
    world["activity"] = {**counters([("archive", "orders")], writes=0, reset="2025-01-01T00:00:00", at="2026-10-04T09:00:00"),
                         **counters([("archive", "old_stuff")], writes=3, reset="2025-01-01T00:00:00", at="2026-10-04T09:00:00")}
    v = view(world)
    assert node(v, "archive")["proposal"] is None and node(v, "archive", "old_stuff")["proposal"] is None
    assert not hasattr(cs, "_change_rates")


def test_a_lens_term_in_table_names_is_a_suggested_rule_never_a_proposal(world):
    assert view(world)["suggested_rules"] == []
    v = view(world, lens={"subjectTerms": ["orders"]})
    assert node(v, "sales", "orders")["proposal"] is None and node(v, "archive", "orders")["proposal"] is None
    assert v["suggested_rules"] == [{"term": "orders", "count": 2,
        "text": "The lens names orders: 2 table names contain it · make that a scope rule?"}]
    assert view(world, lens={"subjectTerms": ["nothing_like_it"]})["suggested_rules"] == []
    assert "data_lens_match" not in cs.PROPOSAL_RULES


def test_the_database_has_no_lens_on_this_build():
    assert cs.data_lens_for(None, "db") is None


def test_pii_is_a_mark_never_a_proposal(world):
    v = view(world, data_classes={("sales", "customers"): {"classes": ["Email"], "pii_columns": 3}})
    t = node(v, "sales", "customers")
    assert t["proposal"] is None and t["state"] == "undecided"
    assert "PII · 3 columns" in t["marks"]
    assert "PII · 3 columns" in node(v, "sales")["marks"]


def test_staging_name_is_a_note_never_a_proposal(world):
    world["tree"]["schemas"].insert(0, sch("stg_load", [tbl("t1")], cls="staging"))
    n = node(view(world), "stg_load")
    assert n["proposal"] is None and "name suggests staging" in n["notes"]


def test_verdict_never_proposes():
    facts = {"kind": "schema", "name": "s", "classification": "data", "table_count": 5,
             "idle": None, "verdict": "recommended", "disposition": "using"}
    assert cs.propose_for_node(facts) is None
    assert set(cs.PROPOSAL_RULES) == {"empty_schema", "dormant"}


def test_system_schemas_are_folded_never_offered(world):
    v = view(world)
    assert all(s["name"] for s in v["schemas"]) and "pg_catalog" not in str(v["schemas"])
    assert v["system"]["text"] == "not published: system schemas are never offered"
    with pytest.raises(cs.ScopeError) as e:
        cs.set_node_choice(world["registry"], "db", "alice", schema="pg_catalog", choice="catalogue")
    assert e.value.status == 404


# ── the four observation states ──────────────────────────────────────────────

def test_confirm_override_and_the_struck_reason(world):
    r = world["registry"]
    cs.confirm_proposal(r, "db", "alice", schema="empty_one", now="2026-10-04T10:00:00")
    n = node(view(world), "empty_one")
    assert n["state"] == "confirmed" and n["effective"] == "leave_out"
    assert n["explicit"]["by"] == "alice" and n["explicit"]["source"] == "empty_schema"
    assert n["explicit"]["at"].startswith("2026-10-04")
    # a second proposal, overridden
    world["tree"]["schemas"].append(sch("tiny"))
    cs.override_proposal(r, "db", "bob", schema="tiny")
    o = node(view(world), "tiny")
    assert o["state"] == "overridden" and o["effective"] == "catalogue"
    assert o["overridden"]["reason"] == "0 tables, measured 10-02"
    assert o["overridden"]["choice"] == "leave_out"
    assert o["explicit"]["source"] == "person" and o["explicit"]["proposal_rule"] == "empty_schema"


def test_survey_now_disagrees_does_not_change_the_choice(world):
    r = world["registry"]
    cs.confirm_proposal(r, "db", "alice", schema="empty_one")
    world["tree"]["schemas"][2] = sch("empty_one", [tbl("a"), tbl("b"), tbl("c"), tbl("d")])
    n = node(view(world), "empty_one")
    assert n["state"] == "disagrees"
    assert n["effective"] == "leave_out"                        # the choice did not move
    assert n["disagrees"]["was"] == {"table_count": 0} and n["disagrees"]["now"] == {"table_count": 4}
    assert n["disagrees"]["survey_at"] == SURVEY_AT
    assert "it had 0 tables; it now has 4" in n["disagrees"]["words"]


def test_a_person_choice_with_no_proposal_is_chosen_not_an_observation_state(world):
    cs.set_node_choice(world["registry"], "db", "alice", schema="sales", choice="catalogue")
    assert node(view(world), "sales")["state"] == "chosen"


def test_clear_keeps_who_cleared_it_and_returns_to_undecided(world):
    r = world["registry"]
    cs.set_node_choice(r, "db", "alice", schema="sales", choice="catalogue")
    cs.clear_node_choice(r, "db", "bob", schema="sales")
    n = node(view(world), "sales")
    assert n["explicit"] is None and n["effective"] is None and n["state"] == "undecided"
    last = r.list_catalogue_scope_events("db")[-1]
    assert (last["choice"], last["author"], last["action"]) == ("", "bob", "clear")
    with pytest.raises(cs.ScopeError) as e:
        cs.clear_node_choice(r, "db", "bob", schema="sales")
    assert e.value.status == 409


def test_confirm_with_nothing_proposed_is_refused(world):
    with pytest.raises(cs.ScopeError) as e:
        cs.confirm_proposal(world["registry"], "db", "alice", schema="sales")
    assert e.value.status == 409


# ── undecided and inheritance ────────────────────────────────────────────────

def test_table_inherits_schema_and_differs_when_set(world):
    r = world["registry"]
    cs.set_node_choice(r, "db", "alice", schema="sales", choice="catalogue")
    v = view(world)
    t = node(v, "sales", "customers")
    assert (t["effective"], t["effective_from"], t["inherited"]) == ("catalogue", "schema", "catalogue")
    assert t["differs_from_schema"] is False and t["explicit"] is None
    cs.set_node_choice(r, "db", "alice", schema="sales", table="customers", choice="leave_out")
    t = node(view(world), "sales", "customers")
    assert t["effective"] == "leave_out" and t["differs_from_schema"] is True
    assert node(view(world), "sales", "orders")["effective"] == "catalogue"   # sibling unchanged


def test_undecided_schema_keeps_what_is_in_egeria_and_an_unconfirmed_proposal_is_undecided(world):
    v = view(world)
    assert node(v, "sales")["undecided_words"] == "keeps what’s in Egeria now"
    e = node(v, "empty_one")
    assert e["proposal"] is not None and e["effective"] is None
    assert world["registry"].list_catalogue_scope_events("db") == []      # nothing was written


# ── baseline and "new since" ─────────────────────────────────────────────────

def _seven_then_twentynine(world):
    seven = [sch(f"s{i}", [tbl("t1"), tbl("t2")]) for i in range(7)]
    world["tree"] = {"schemas": seven + [SYSTEM]}
    cs.set_node_choice(world["registry"], "db", "dwolfson", schema="s0", choice="catalogue",
                       now="2026-10-04T08:00:00")
    for i in range(1, 7):
        cs.set_node_choice(world["registry"], "db", "dwolfson", schema=f"s{i}", choice="catalogue")
    world["tree"] = {"schemas": seven + [sch(f"new{i}", [tbl("a"), tbl("b"), tbl("c")])
                                         for i in range(22)] + [SYSTEM]}
    world["survey_at"] = "2026-10-05T09:00:00"       # a survey AFTER the declaration (10-04 08:00) saw them


def test_nothing_is_new_before_a_scope_is_declared(world):
    v = view(world)
    assert v["declared"]["declared"] is False
    assert v["new_since"]["declared"] is False and v["new_since"]["schemas"] == 0
    assert not any(s["new_since"] for s in v["schemas"])


def test_22_schemas_added_after_the_declaration_are_counted(world):
    _seven_then_twentynine(world)
    v = view(world)
    assert v["declared"]["by"] == "dwolfson" and v["declared"]["at"].startswith("2026-10-04")
    ns = cs.new_since_declared(world["registry"], "db")
    assert (ns["schemas"], ns["tables"]) == (22, 66)
    assert ns["text"] == "22 new schemas (66 tables) not in your scope"
    assert all(node(v, f"new{i}")["new_since"] for i in range(22))
    assert not node(v, "s0")["new_since"]
    assert v["counts"]["schemas_catalogue"] == 7 and v["counts"]["schemas_offered"] == 29


def test_a_decision_on_a_new_schema_takes_it_out_of_the_count_and_redeclare_resets(world):
    _seven_then_twentynine(world)
    cs.set_node_choice(world["registry"], "db", "dwolfson", schema="new0", choice="leave_out")
    assert cs.new_since_declared(world["registry"], "db")["schemas"] == 21
    cs.redeclare(world["registry"], "db", "dwolfson")
    assert cs.new_since_declared(world["registry"], "db")["schemas"] == 0
    base = world["registry"].list_catalogue_scope_baselines("db")
    # D6 (rehearsal 2026-10-05): the implicit baseline a first choice makes stays "first"; the first
    # EXPLICIT declaration is "declare", not "redeclare"; only a declaration after that is a re-declaration.
    assert [b["kind"] for b in base] == ["first", "declare"]         # history kept
    assert len(base[0]["baseline"]["schemas"]) == 7 and len(base[1]["baseline"]["schemas"]) == 29
    cs.redeclare(world["registry"], "db", "dwolfson")
    assert [b["kind"] for b in world["registry"].list_catalogue_scope_baselines("db")] == ["first", "declare", "redeclare"]


def test_the_first_declaration_with_no_earlier_baseline_is_a_declare_and_the_view_says_so(world):
    cs.redeclare(world["registry"], "db", "dwolfson")
    assert [b["kind"] for b in world["registry"].list_catalogue_scope_baselines("db")] == ["declare"]
    assert cs.build_scope_view(world["registry"], "db")["declared"]["kind"] == "declare"


def test_new_table_in_a_known_schema_is_reported_separately(world):
    cs.set_node_choice(world["registry"], "db", "alice", schema="sales", choice="catalogue",
                       now="2026-10-02T10:00:00")
    world["tree"]["schemas"][0]["tables"].append(tbl("fresh"))
    world["survey_at"] = "2026-10-03T09:00:00"
    ns = cs.new_since_declared(world["registry"], "db")
    assert ns["schemas"] == 0 and ns["tables_in_known_schemas"] == 1
    assert node(view(world), "sales", "fresh")["new_since"] is True


# ── no "conflict" any more ───────────────────────────────────────────────────
# A table name chosen differently in two schemas is not something Egeria cannot express
# (the commit works by schema, and nothing reads such a pair), so the view computes none.

def test_same_named_tables_chosen_differently_are_not_a_conflict(world):
    r = world["registry"]
    cs.set_node_choice(r, "db", "alice", schema="sales", choice="catalogue")
    cs.set_node_choice(r, "db", "alice", schema="archive", choice="leave_out")   # both hold "orders"
    cs.set_node_choice(r, "db", "alice", schema="sales", table="orders", choice="leave_out")
    cs.set_node_choice(r, "db", "alice", schema="archive", table="orders", choice="catalogue")
    v = view(world)
    assert "conflicts" not in v
    for s in v["schemas"]:
        for t in s["tables"]:
            assert "conflict" not in t
        assert "conflict" not in s
    assert node(v, "archive", "orders")["differs_from_schema"] is True      # the plain mark stays
    assert not hasattr(cs, "resolve_conflict") and not hasattr(cs, "scope_conflicts")


# ── depth, header, element ───────────────────────────────────────────────────

def test_depth_is_stored_with_author_and_changes_what_the_tree_shows(world):
    r = world["registry"]
    v = view(world)
    assert v["depth"]["value"] == "schemas_and_tables" and v["depth"]["declared"] is False
    assert [d["id"] for d in v["depth"]["options"]] == list(cs.DEPTH_IDS)
    assert "columns" not in node(v, "sales", "orders")
    cs.set_depth(r, "db", "alice", depth="tables_and_columns")
    v = view(world)
    assert v["depth"]["value"] == "tables_and_columns" and v["depth"]["by"] == "alice"
    assert node(v, "sales", "orders")["columns"][0]["name"] == "id"
    assert "changes this tree only" in v["depth"]["help"]
    assert v["depth"]["commit_note"] == cs.DEPTH_NOT_HONOURED or v["depth"]["value"] == "tables_and_columns"
    with pytest.raises(cs.ScopeError):
        cs.set_depth(r, "db", "alice", depth="everything")


def test_depth_provenance_is_a_view_claim_and_names_no_lever_the_commit_does_not_use():
    assert cs.depth_provenance("schemas", "table") == "tables hidden in this view · the commit still publishes them"
    assert cs.depth_provenance("schemas_and_tables", "table") == "columns hidden in this view · the commit still publishes them"
    assert cs.depth_provenance("tables_and_columns", "table") == ""
    assert "the commit still publishes whole schemas" in cs.depth_provenance("database_only", "schema")
    # slice B takes the schema-kind door: there are no include lists, so no text may claim one
    for d in cs.DEPTHS:
        assert "include" not in d["how"] and d["commit_honours"] is (d["id"] == "tables_and_columns")


def test_header_survey_numbers_come_from_the_native_survey_row(world):
    r = world["registry"]
    assert view(world)["survey"]["state"] == "not_measured"
    r.record_database_survey("db", 99, 99, 99, {"schema_info": {"x": 1}}, source="local")
    assert view(world)["survey"]["state"] == "not_measured"        # RE's own survey is not Egeria's
    r.record_database_survey("db", 29, 266, 1000, {}, source="egeria", egeria_report_guid="g1",
                             surveyed_at="2026-10-03T00:00:00")
    s = view(world)["survey"]
    assert (s["state"], s["schema_count"], s["table_count"]) == ("measured", 29, 266)
    # and the native survey rows (step_runs + annotations), when stored, are what it reads first
    _native(r, schemas=2, tables_per=3)
    s = view(world)["survey"]
    assert (s["state"], s["schema_count"], s["table_count"]) == ("measured", 2, 11)


# ── routes ───────────────────────────────────────────────────────────────────

@pytest.fixture
def client(registry, world, monkeypatch):
    monkeypatch.setattr(
        "resource_explorer.registry.ProjectRegistry.__init__",
        lambda self, db_path=None: setattr(self, "__dict__", registry.__dict__) or None,
    )
    from resource_explorer import auth
    from resource_explorer.web.app import app
    from resource_explorer.web.routes import catalogue_scope as routes
    monkeypatch.setattr(routes, "get_current_user", auth.get_current_user)
    return TestClient(app)


def as_user(u):
    return {"Authorization": "Bearer " + create_access_token(user_id=u, egeria_token="t")}


WRITES = [
    ("depth", "put", "/api/catalogue-scope/db/depth", {"depth": "schemas"}),
    ("node", "put", "/api/catalogue-scope/db/node", {"schema_name": "sales", "choice": "catalogue"}),
    ("confirm", "post", "/api/catalogue-scope/db/node/confirm", {"schema_name": "empty_one"}),
    ("override", "post", "/api/catalogue-scope/db/node/override", {"schema_name": "empty_one"}),
    ("clear", "post", "/api/catalogue-scope/db/node/clear", {"schema_name": "sales"}),
    ("redeclare", "post", "/api/catalogue-scope/db/redeclare", None),
]


@pytest.mark.parametrize("name,method,url,body", WRITES, ids=[w[0] for w in WRITES])
def test_signed_out_write_is_401_and_writes_nothing(client, registry, name, method, url, body):
    r = getattr(client, method)(url, **({"json": body} if body is not None else {}))
    assert r.status_code == 401, r.text
    assert "Sign in to" in r.json()["detail"]
    assert registry.list_catalogue_scope_events("db") == []
    assert registry.list_catalogue_scope_baselines("db") == []


def test_read_is_open_signed_out_and_names_the_element(client):
    j = client.get("/api/catalogue-scope/db").json()
    assert j["egeria_element"]["short"] == "abcdef12"
    assert j["declared"]["declared"] is False
    assert client.get("/api/catalogue-scope/nope").status_code == 404


def test_set_confirm_override_clear_and_history_through_the_routes(client, registry):
    a, b = as_user("alice"), as_user("bob")
    r = client.put("/api/catalogue-scope/db/node", json={"schema_name": "sales", "choice": "catalogue",
                                                          "author": "mallory"}, headers=a)
    assert r.status_code == 200
    assert client.post("/api/catalogue-scope/db/node/confirm", json={"schema_name": "empty_one"},
                       headers=b).status_code == 200
    h = client.get("/api/catalogue-scope/db/history").json()
    assert [e["author"] for e in h["events"]] == ["alice", "bob"]        # body author ignored
    assert h["declarations"][0]["declared_by"] == "alice"
    j = client.get("/api/catalogue-scope/db").json()
    assert next(s for s in j["schemas"] if s["name"] == "empty_one")["state"] == "confirmed"
    assert client.post("/api/catalogue-scope/db/node/clear", json={"schema_name": "sales"},
                       headers=b).status_code == 200
    assert client.post("/api/catalogue-scope/db/node/confirm", json={"schema_name": "sales"},
                       headers=b).status_code == 409
    assert client.put("/api/catalogue-scope/db/node", json={"schema_name": "nope", "choice": "catalogue"},
                      headers=a).status_code == 404
    assert client.put("/api/catalogue-scope/db/node", json={"schema_name": "sales", "choice": "maybe"},
                      headers=a).status_code == 400
    assert len(registry.list_catalogue_scope_events("db")) == 3          # failures wrote nothing


def test_new_since_route_and_no_conflict_routes(client):
    a = as_user("alice")
    client.put("/api/catalogue-scope/db/node", json={"schema_name": "sales", "table_name": "orders",
                                                      "choice": "catalogue"}, headers=a)
    assert client.get("/api/catalogue-scope/db/new-since").json()["declared"] is True
    assert client.get("/api/catalogue-scope/db/conflicts").status_code in (404, 405)
    assert client.post("/api/catalogue-scope/db/resolve", json={"name": "orders", "choice": "catalogue"},
                       headers=a).status_code in (404, 405)


# ── slice A2: the tree reads the fullest, newest measured set ───────────────────
#
# The first real use (localhost_docker_coco_pharma): the tree showed RE's own
# credential-scoped survey of 10-03 (8 schemas, 61 tables) while the post-reset
# Egeria native survey (29 schemas, 266 tables) sat in step_runs and
# native_survey_annotations, unread.

import json as _json

NATIVE_AT = "2026-10-04T06:00:00"
LOCAL_AT = "2026-10-03T09:00:00"
PROC = "survey-postgres-database:coco"


def _ann(n, kind, props, summary=""):
    return {"guid": f"a-{kind.split()[2]}-{n}", "annotation_type": kind, "summary": summary or f"{kind} {n}",
            "analysis_step": "x", "explanation": "", "confidence": 100,
            "detail": {"json_properties": props if isinstance(props, str) else _json.dumps(props)}}


def _native(registry, schemas=29, tables_per=9, *, at=NATIVE_AT, guid="03b908a5-report", extra=(), reset="2026-10-03T07:30:00"):
    """A stored, complete native survey: `schemas` schema annotations and `tables_per` table
    annotations in each, plus one database annotation. 29 x 9 + 5 = 266 tables."""
    anns = [_ann(0, "Capture Database Measurements", {"lastStatisticsReset": reset} if reset else {})]
    total = 0
    for i in range(schemas):
        sname = f"s{i:02d}"
        n = tables_per + (5 if i == 0 else 0)
        anns.append(_ann(i, "Capture Database Schema Measurements",
                         {"schemaName": sname, "qualifiedSchemaName": f"coco.{sname}", "tableCount": str(n)}))
        for j in range(n):
            total += 1
            anns.append(_ann(total, "Capture Database Table Measurements",
                             {"tableName": f"t{j:02d}", "qualifiedTableName": f"coco.{sname}.t{j:02d}",
                              "tableSize": str(8192 * (j + 1)), "tableType": "VIEW" if j == 0 and i == 1 else "BASE TABLE",
                              "numberOfRowsInserted": "0", "numberOfRowsUpdated": "0", "numberOfRowsDeleted": "0"}))
    anns += list(extra)
    registry.record_native_survey_submission("database", "db", PROC, at, engine_action_guid="ea-1")
    registry.record_native_survey_report(
        "ea-1", entity_type="database", slug="db", process_qualified_name=PROC,
        report_guid=guid, report_at=at, read_at=at, annotations=anns)
    return total


def _local_tree(n_schemas=8, tables_per=7, names=None, rows=1000):
    names = names or [f"s{i:02d}" for i in range(n_schemas)]
    return {"schemas": [sch(n, [tbl(f"t{j:02d}", rows=rows) for j in range(tables_per)]) for n in names] + [SYSTEM]}


def test_the_tree_reads_the_native_survey_when_it_is_fuller_and_newer(world):
    assert _native(world["registry"]) == 266
    world["tree"] = _local_tree(8, 7)                      # 8 schemas, 56 tables, older
    world["survey_at"] = LOCAL_AT
    v = view(world)
    assert len(v["schemas"]) == 29
    assert sum(len(s["tables"]) for s in v["schemas"]) == 266
    ch = v["sources"]["chosen"]
    assert (ch["kind"], ch["schemas"], ch["tables"], ch["as_of"]) == ("egeria", 29, 266, NATIVE_AT)
    assert (v["sources"]["egeria"]["schema_count"], v["sources"]["egeria"]["table_count"]) == (29, 266)
    assert (v["sources"]["local"]["schema_count"], v["sources"]["local"]["table_count"]) == (8, 56)
    assert v["sources"]["disagree"] is True
    assert v["survey"]["state"] == "measured" and v["survey"]["schema_count"] == 29


def test_each_node_carries_its_own_source_and_as_of(world):
    _native(world["registry"])
    world["tree"] = _local_tree(8, 7)
    world["survey_at"] = LOCAL_AT
    v = view(world)
    n = node(v, "s05")
    assert n["source"]["kind"] == "egeria" and n["source"]["as_of"] == NATIVE_AT
    assert n["source"]["text"] == "from Egeria survey 10-04"
    assert node(v, "s05", "t03")["source"]["text"] == "from Egeria survey 10-04"
    # a local-only fact (rows) is filled in at node level and says where it came from
    t = node(v, "s05", "t03")
    assert t["row_count"] == 1000 and t["facts_from"]["rows"]["text"] == "from RE local survey 10-03"
    assert t["size_bytes"] == 8192 * 4 and t["facts_from"]["size"]["kind"] == "egeria"
    # a native node the local survey never saw has no rows and says so honestly
    assert node(v, "s20", "t03")["row_count"] is None and "rows" not in node(v, "s20", "t03")["facts_from"]


def test_a_database_with_only_a_local_survey_still_works(world):
    v = view(world)
    assert [s["name"] for s in v["schemas"]] == ["sales", "archive", "empty_one"]
    assert v["sources"]["chosen"]["kind"] == "local" and v["sources"]["egeria"]["state"] == "not_measured"
    assert v["sources"]["disagree"] is False
    assert node(v, "sales")["source"]["text"] == "from RE local survey 10-02"


def test_a_database_with_only_a_native_survey_works(world):
    _native(world["registry"], schemas=3, tables_per=2)
    world["tree"] = {"schemas": []}                        # no local rows at all
    v = view(world)
    assert [s["name"] for s in v["schemas"]] == ["s00", "s01", "s02"]
    assert v["sources"]["chosen"]["kind"] == "egeria" and v["sources"]["local"]["state"] == "not_measured"
    assert v["sources"]["disagree"] is False


def test_system_schemas_in_a_native_survey_stay_folded(world):
    extra = [_ann(900, "Capture Database Schema Measurements", {"schemaName": "pg_catalog"}),
             _ann(901, "Capture Database Table Measurements",
                  {"tableName": "pg_class", "qualifiedTableName": "coco.pg_catalog.pg_class"})]
    _native(world["registry"], schemas=2, tables_per=1, extra=extra)
    v = view(world)
    assert "pg_catalog" not in [s["name"] for s in v["schemas"]]
    assert v["system"]["folded"] == 1


def test_a_malformed_json_properties_does_not_break_the_tree(world):
    extra = [_ann(800, "Capture Database Table Measurements", "{not json at all"),
             _ann(801, "Capture Database Table Measurements", ""),
             # readable name, junk fact: it still lists, with the size not established
             _ann(802, "Capture Database Table Measurements",
                  {"tableName": "odd", "qualifiedTableName": "coco.s00.odd", "tableSize": "n/a"})]
    _native(world["registry"], schemas=2, tables_per=2, extra=extra)
    v = view(world)
    assert len(v["schemas"]) == 2
    odd = node(v, "s00", "odd")
    assert odd["size_bytes"] is None and odd["row_count"] is None
    assert v["sources"]["unreadable"] == 2          # counted, never silently dropped


def test_an_incomplete_native_survey_is_not_built_from(world):
    _native(world["registry"], schemas=3, tables_per=2)
    with world["registry"]._conn() as conn:
        conn.execute("DELETE FROM native_survey_annotations WHERE annotation_type = ?",
                     ("Capture Database Schema Measurements",))
    v = view(world)                                        # stored rows no longer match the run's count
    assert v["sources"]["chosen"]["kind"] == "local" and v["sources"]["egeria"]["state"] == "not_measured"


def test_a_newer_local_node_the_native_survey_never_saw_is_added_with_its_own_source(world):
    _native(world["registry"], schemas=3, tables_per=2, at="2026-10-01T06:00:00")
    world["tree"] = _local_tree(names=["s00", "s01", "s02", "brand_new"], tables_per=1)
    world["survey_at"] = "2026-10-03T09:00:00"
    v = view(world)
    assert node(v, "brand_new")["source"]["kind"] == "local"
    assert node(v, "s01")["source"]["kind"] == "egeria"
    # but a node only an OLDER measurement had is not listed (it may have been dropped since)
    world["survey_at"] = "2026-09-20T09:00:00"
    world["tree"] = _local_tree(names=["s00", "s01", "s02", "dropped_since"], tables_per=1)
    assert "dropped_since" not in [s["name"] for s in view(world)["schemas"]]


def test_the_fuller_measurement_wins_over_a_newer_thinner_one(world):
    _native(world["registry"], schemas=29, tables_per=2, at="2026-10-01T06:00:00")
    world["tree"] = _local_tree(8, 2)
    world["survey_at"] = "2026-10-05T09:00:00"               # newer, but sees 8 of 29
    v = view(world)
    assert v["sources"]["chosen"]["kind"] == "egeria" and len(v["schemas"]) == 29


def test_empty_schema_rule_still_proposes_from_the_chosen_source(world):
    extra = [_ann(700, "Capture Database Schema Measurements",
                  {"schemaName": "hollow", "qualifiedSchemaName": "coco.hollow", "tableCount": "0"})]
    _native(world["registry"], schemas=2, tables_per=2, extra=extra)
    n = node(view(world), "hollow")
    assert n["table_count"] == 0
    assert (n["proposal"]["rule"], n["proposal"]["choice"]) == ("empty_schema", "leave_out")
    assert n["proposal"]["reason"] == "0 tables, measured 10-04"


def test_native_counters_alone_read_cant_tell_with_the_database_reset_date(world):
    _native(world["registry"], schemas=2, tables_per=2)
    v = view(world)
    t = node(v, "s00", "t00")
    assert t["last_write"]["state"] == "cant_tell"
    assert t["last_write"]["text"] == "can't tell · counters reset 10-03 · 1 day of evidence"
    assert t["proposal"] is None and node(v, "s00")["proposal"] is None
    world["registry"]  # no reset recorded anywhere: say the window is not recorded
    r2 = world["registry"]
    with r2._conn() as conn:
        conn.execute("DELETE FROM native_survey_annotations WHERE annotation_type = 'Capture Database Measurements'")
        conn.execute("UPDATE step_runs SET report_annotation_count = report_annotation_count - 1")
    t = node(view(world), "s00", "t00")
    assert t["last_write"]["text"] == "can't tell · reset date not recorded"


# ── baseline and "new since" across sources ──────────────────────────────────────

def test_a_scope_declared_now_baselines_all_29_and_a_30th_is_new_since(world):
    _native(world["registry"])
    world["tree"] = _local_tree(8, 7)
    world["survey_at"] = LOCAL_AT
    cs.set_node_choice(world["registry"], "db", "dwolfson", schema="s00", choice="catalogue",
                       now="2026-10-04T12:00:00")
    base = world["registry"].list_catalogue_scope_baselines("db")[0]
    assert len(base["baseline"]["schemas"]) == 29 and len(base["baseline"]["tables"]) == 266
    assert (base["baseline"]["source"], base["baseline"]["as_of"]) == ("egeria", NATIVE_AT)
    assert base["survey_at"] == NATIVE_AT
    assert cs.new_since_declared(world["registry"], "db")["schemas"] == 0
    # a later native survey with a 30th schema
    world["registry"].record_native_survey_submission("database", "db", PROC, "2026-10-06T06:00:00",
                                                      engine_action_guid="ea-2")
    anns = [_ann(1, "Capture Database Schema Measurements", {"schemaName": "s30", "tableCount": "1"}),
            _ann(2, "Capture Database Table Measurements", {"tableName": "t0", "qualifiedTableName": "coco.s30.t0"})]
    anns += [_ann(10 + i, "Capture Database Schema Measurements", {"schemaName": f"s{i:02d}", "tableCount": "1"})
             for i in range(29)]
    anns += [_ann(50 + i, "Capture Database Table Measurements",
                  {"tableName": "t0", "qualifiedTableName": f"coco.s{i:02d}.t0"}) for i in range(29)]
    world["registry"].record_native_survey_report(
        "ea-2", entity_type="database", slug="db", process_qualified_name=PROC,
        report_guid="newer", report_at="2026-10-06T06:00:00", read_at="2026-10-06T06:00:00", annotations=anns)
    ns = cs.new_since_declared(world["registry"], "db")
    assert ns["schema_names"] == ["s30"] and ns["tables"] == 1
    assert node(view(world), "s30")["new_since"] is True
    assert node(view(world), "s05")["new_since"] is False


def test_a_baseline_from_a_thin_source_does_not_call_older_measured_nodes_new(world):
    """Scope declared on 10-03 from the 8-schema local survey; the 29-schema Egeria
    survey was taken 10-02 (before the declaration) so those 21 schemas existed then:
    not "new". A schema that only a measurement AFTER the declaration shows is."""
    world["tree"] = _local_tree(8, 7)
    world["survey_at"] = LOCAL_AT
    cs.set_node_choice(world["registry"], "db", "dwolfson", schema="s00", choice="catalogue",
                       now="2026-10-03T10:00:00")
    assert world["registry"].list_catalogue_scope_baselines("db")[0]["baseline"]["source"] == "local"
    _native(world["registry"], at="2026-10-02T06:00:00")           # older than the declaration
    v = view(world)
    assert len(v["schemas"]) == 29
    assert v["new_since"]["schemas"] == 0 and not any(s["new_since"] for s in v["schemas"])
    world["registry"].record_native_survey_submission("database", "db", PROC, "2026-10-09T06:00:00",
                                                      engine_action_guid="ea-9")
    anns = [_ann(1, "Capture Database Schema Measurements", {"schemaName": "s_after", "tableCount": "1"}),
            _ann(2, "Capture Database Table Measurements", {"tableName": "t", "qualifiedTableName": "coco.s_after.t"})]
    anns += [_ann(10 + i, "Capture Database Schema Measurements", {"schemaName": f"s{i:02d}", "tableCount": "1"})
             for i in range(29)]
    anns += [_ann(50 + i, "Capture Database Table Measurements",
                  {"tableName": "t0", "qualifiedTableName": f"coco.s{i:02d}.t0"}) for i in range(29)]
    world["registry"].record_native_survey_report(
        "ea-9", entity_type="database", slug="db", process_qualified_name=PROC,
        report_guid="later", report_at="2026-10-09T06:00:00", read_at="2026-10-09T06:00:00", annotations=anns)
    assert cs.new_since_declared(world["registry"], "db")["schema_names"] == ["s_after"]


# ── bulk choice ──────────────────────────────────────────────────────────────────

def test_catalogue_all_29_records_one_event_per_node_with_the_author(world):
    _native(world["registry"])
    out = cs.set_nodes_choice(world["registry"], "db", "dwolfson", all_schemas=True, choice="catalogue",
                              now="2026-10-04T12:00:00")
    assert len(out["written"]) == 29
    ev = [e for e in world["registry"].list_catalogue_scope_events("db") if e["node_kind"] == "schema"]
    assert len(ev) == 29 and {e["author"] for e in ev} == {"dwolfson"}
    assert {e["choice"] for e in ev} == {"catalogue"} and {e["changed_at"] for e in ev} == {"2026-10-04T12:00:00"}
    v = view(world)
    assert v["counts"]["schemas_catalogue"] == 29
    assert len(world["registry"].list_catalogue_scope_baselines("db")) == 1     # one declaration, not 29


def test_bulk_selected_leave_out_and_clear(world):
    r = world["registry"]
    out = cs.set_nodes_choice(r, "db", "alice", nodes=[{"schema": "sales"}, {"schema": "archive"}],
                              choice="leave_out")
    assert out["written"] == ["sales", "archive"]
    assert node(view(world), "archive")["effective"] == "leave_out"
    out = cs.set_nodes_choice(r, "db", "bob", nodes=[{"schema": "sales"}, {"schema": "empty_one"}], choice="")
    assert out["written"] == ["sales"] and out["skipped"] == ["empty_one"]       # nothing to clear there
    last = r.list_catalogue_scope_events("db")[-1]
    assert (last["choice"], last["author"], last["action"]) == ("", "bob", "clear")


def test_bulk_choice_over_a_proposal_records_confirm_or_override(world):
    r = world["registry"]
    cs.set_nodes_choice(r, "db", "alice", nodes=[{"schema": "empty_one"}], choice="leave_out")
    assert node(view(world), "empty_one")["state"] == "confirmed"
    cs.set_nodes_choice(r, "db", "alice", nodes=[{"schema": "empty_one"}], choice="catalogue")
    n = node(view(world), "empty_one")                                # the proposal still stands: opposite choice = override
    assert n["state"] == "overridden" and n["effective"] == "catalogue"


def test_bulk_does_not_overwrite_an_explicit_table_choice_and_reports_it(world):
    r = world["registry"]
    cs.set_node_choice(r, "db", "alice", schema="sales", table="customers", choice="catalogue")
    out = cs.set_nodes_choice(r, "db", "alice", nodes=[{"schema": "sales"}], choice="leave_out")
    assert out["differing_tables"] == [{"schema": "sales", "table": "customers", "choice": "catalogue"}]
    t = node(view(world), "sales", "customers")
    assert t["effective"] == "catalogue" and t["differs_from_schema"] is True
    assert node(view(world), "sales", "orders")["effective"] == "leave_out"   # the sibling follows the schema


def test_bulk_validates_before_writing_anything(world):
    r = world["registry"]
    with pytest.raises(cs.ScopeError) as e:
        cs.set_nodes_choice(r, "db", "alice", nodes=[{"schema": "sales"}, {"schema": "pg_catalog"}], choice="catalogue")
    assert e.value.status == 404 and r.list_catalogue_scope_events("db") == []
    for bad, status in (({"nodes": [], "choice": "catalogue"}, 400), ({"nodes": [{"schema": "sales"}], "choice": "maybe"}, 400)):
        with pytest.raises(cs.ScopeError) as e:
            cs.set_nodes_choice(r, "db", "alice", **bad)
        assert e.value.status == status
    with pytest.raises(cs.ScopeError) as e:
        cs.set_nodes_choice(r, "db", "", nodes=[{"schema": "sales"}], choice="catalogue")
    assert e.value.status == 401


def test_bulk_route_records_the_session_author_and_refuses_signed_out(client, registry):
    body = {"nodes": [{"schema_name": "sales"}, {"schema_name": "archive"}], "choice": "catalogue", "author": "mallory"}
    assert client.post("/api/catalogue-scope/db/nodes", json=body).status_code == 401
    assert registry.list_catalogue_scope_events("db") == []
    r = client.post("/api/catalogue-scope/db/nodes", json=body, headers=as_user("alice"))
    assert r.status_code == 200 and r.json()["written"] == ["sales", "archive"]
    assert {e["author"] for e in registry.list_catalogue_scope_events("db")} == {"alice"}
    r = client.post("/api/catalogue-scope/db/nodes", json={"all_schemas": True, "choice": "leave_out"},
                    headers=as_user("bob"))
    assert len(r.json()["written"]) == 3


# ── slice A2, designer round 2: rows and size with source, as-of, estimate and disagreement ──

def test_rows_show_their_kind_estimate_scan_and_their_source_and_date(world):
    world["tree"]["schemas"][0]["tables"][0].update(row_count=3412, row_count_state="measured")
    world["tree"]["schemas"][0]["tables"][1].update(row_count=3400, row_count_state="catalog_estimate")
    v = view(world)
    scan, est = node(v, "sales", "orders")["rows_view"], node(v, "sales", "customers")["rows_view"]
    assert (scan["text"], scan["state"]) == ("3,412", "measured") and scan["detail"].endswith("· scan")
    assert (est["text"], est["state"]) == ("≈3,400", "estimate") and "estimate" in est["detail"]
    assert "10-02" in scan["detail"] and "RE local survey" in scan["detail"]
    sm = node(v, "sales")["rows_view"]
    assert sm["text"] == "≈6,812"                       # an estimate inside makes the sum an estimate


def test_unmeasured_is_not_a_zero_and_a_real_zero_is_a_zero_and_no_access_is_not_established(world):
    t = world["tree"]["schemas"][0]["tables"]
    t[0].update(row_count=None)
    t[1].update(row_count=0)
    world["tree"]["schemas"].insert(0, sch("locked", cls="no_access", table_count=0))
    v = view(world)
    assert node(v, "sales", "orders")["rows_view"]["text"] == "not measured"
    assert node(v, "sales", "customers")["rows_view"]["text"] == "0"
    assert node(v, "sales")["rows_view"]["text"] == "≥ 0 · 1 table not measured"
    assert node(v, "locked")["rows_view"]["text"] == "? not established"
    assert node(v, "empty_one")["rows_view"]["text"] == "not measured"


def test_two_sources_that_disagree_by_more_than_2x_read_sources_disagree_with_both_values(world):
    world["tree"]["schemas"][0]["tables"][0].update(row_count=3400, row_count_state="catalog_estimate")
    world["row_sources"] = {("sales", "orders"): [
        {"source": "local", "rows": 3400, "state": "catalog_estimate", "at": "2026-10-02T09:00:00"},
        {"source": "egeria", "rows": 0, "state": "measured", "at": "2026-10-04T06:00:00"}]}
    c = node(view(world), "sales", "orders")["rows_view"]
    assert c["text"] == "◐ sources disagree" and c["state"] == "disagree"
    assert c["detail"] == "RE local survey 10-02: ≈3,400 · Egeria survey 10-04: 0"
    world["row_sources"][("sales", "orders")][1]["rows"] = 3000          # within 2x: no disagreement
    assert node(view(world), "sales", "orders")["rows_view"]["state"] != "disagree"


def test_size_is_formatted_dated_and_a_schema_with_unsized_tables_says_at_least(world):
    world["tree"]["schemas"][0]["tables"][0].update(size_bytes=22 * 1024 * 1024)
    world["tree"]["schemas"][0]["tables"][1].update(size_bytes=None)
    v = view(world)
    assert node(v, "sales", "orders")["size_view"]["text"] == "22 MB"
    assert node(v, "sales", "customers")["size_view"]["text"] == "not measured"
    assert node(v, "sales")["size_view"]["text"] == "≥ 22 MB"


# ── a failed read is observable, never "nothing known" / "not measured" ─────────────

def test_a_failed_read_of_the_earlier_surveys_says_cant_tell_and_flags_nothing_new(world, monkeypatch):
    world["tree"] = {"schemas": [sch(f"s{i}", [tbl("t")]) for i in range(2)] + [SYSTEM]}
    cs.set_node_choice(world["registry"], "db", "dwolfson", schema="s0", choice="catalogue",
                       now="2026-10-04T08:00:00")
    world["tree"] = {"schemas": [sch(f"s{i}", [tbl("t")]) for i in range(4)] + [SYSTEM]}
    world["survey_at"] = "2026-10-05T09:00:00"
    assert cs.new_since_declared(world["registry"], "db")["schemas"] == 2     # the reads work: 2 are new

    def boom(*a, **k):
        raise RuntimeError("registry unreachable")
    monkeypatch.setattr(cs, "_native_node_set", lambda reg, slug, before="": None)
    monkeypatch.setattr(world["registry"], "query_detail_rows", boom)
    with world["registry"]._conn() as conn:
        conn.execute("INSERT INTO database_tables (database_slug, surveyed_at, schema_name, table_name) "
                     "VALUES ('db', '2026-10-01T00:00:00', 's0', 't')")
    v = view(world)
    ns = v["new_since"]
    assert ns["text"] == "can't tell: the earlier surveys could not be read"
    assert ns["can_tell"] is False and "registry unreachable" in ns["error"]
    assert ns["schemas"] == 0 and not any(s["new_since"] for s in v["schemas"])
    assert not any(t["new_since"] for s in v["schemas"] for t in s["tables"])


def test_a_failed_local_survey_read_is_unreadable_not_not_measured(world, monkeypatch):
    world["tree"] = {"schemas": []}
    def boom(slug, *a, **k):
        raise RuntimeError("surveys table unreadable")
    monkeypatch.setattr(world["registry"], "get_database_surveys", boom)
    local = view(world)["sources"]["local"]
    assert local["state"] == "unreadable" and "surveys table unreadable" in local["read_error"]


# ── slice A2.1: one node set for Schema Inventory and Curate; zeros that are not measurements ──────

def _native_tables(registry, tables, schema="shop"):
    """A complete native survey of one schema whose tables carry exactly the given properties."""
    anns = [_ann(0, "Capture Database Measurements", {"lastStatisticsReset": "2026-10-03T07:30:00"}),
            _ann(1, "Capture Database Schema Measurements",
                 {"schemaName": schema, "qualifiedSchemaName": f"coco.{schema}", "tableCount": str(len(tables))})]
    for i, (name, props) in enumerate(tables.items(), start=2):
        anns.append(_ann(i, "Capture Database Table Measurements",
                         {"tableName": name, "qualifiedTableName": f"coco.{schema}.{name}", **props}))
    registry.record_native_survey_submission("database", "db", PROC, NATIVE_AT, engine_action_guid="ea-1")
    registry.record_native_survey_report("ea-1", entity_type="database", slug="db", process_qualified_name=PROC,
                                         report_guid="r-1", report_at=NATIVE_AT, read_at=NATIVE_AT, annotations=anns)


def test_a_native_size_of_zero_with_a_column_count_of_zero_is_not_a_measurement(world):
    """The native survey wrote tableSize 0 / columnCount 0 for tables it never measured."""
    world["tree"] = {"schemas": []}
    _native_tables(world["registry"], {
        "unmeasured": {"tableSize": "0", "columnCount": "0", "tableType": "BASE TABLE"},
        "wrote_rows": {"tableSize": "0", "columnCount": "4", "numberOfRowsInserted": "250"},
        "real": {"tableSize": "16384", "columnCount": "3"},
    })
    v = view(world)
    u, w, r = (node(v, "shop", n) for n in ("unmeasured", "wrote_rows", "real"))
    assert u["size_view"]["text"] == "not measured" and u["size_bytes"] is None and u["column_count"] is None
    assert w["size_view"]["text"] == "not measured" and w["size_bytes"] is None      # rows were counted
    assert w["column_count"] == 4                                                      # a real count stays
    assert r["size_view"]["text"] == "16 KB" and r["column_count"] == 3
    assert "size" not in u["facts_from"]                                              # no source claimed for a non-measurement


def test_a_measured_empty_table_still_reads_0_B_and_a_zero_with_no_evidence_does_not(world):
    """Size 0 stays "0 B" only when rows were counted as 0 by a measured source and the table has columns."""
    world["tree"] = {"schemas": [sch("shop", [
        {**tbl("empty", rows=0), "size_bytes": 0, "column_count": 3},        # rows scanned as 0
        {**tbl("estimated", rows=0), "size_bytes": 0, "column_count": 3, "row_count_state": "catalog_estimate"},
        {**tbl("no_rows", rows=None), "size_bytes": 0, "column_count": 3},   # nobody counted rows
        {**tbl("has_rows", rows=40), "size_bytes": 0, "column_count": 3},    # rows counted above zero
        {**tbl("zero_cols", rows=0), "size_bytes": 0, "column_count": 0, "columns": []},
    ])]}
    v = view(world)
    assert node(v, "shop", "empty")["size_view"]["text"] == "0 B"
    for n in ("estimated", "no_rows", "has_rows", "zero_cols"):
        assert node(v, "shop", n)["size_view"]["text"] == "not measured", n
    assert node(v, "shop", "zero_cols")["column_count"] is None


def test_a_column_count_of_zero_becomes_the_number_of_columns_listed(world):
    t = {**tbl("x"), "column_count": 0}                                   # tbl() lists one column
    assert cs.measured_size_rule(t)[1] == 1
    assert cs.measured_size_rule({**t, "columns": []})[1] is None


def test_a_schema_total_of_zero_is_not_a_measurement_unless_a_table_is_a_measured_empty_one(world):
    world["tree"] = {"schemas": [{**sch("shop", [tbl("a", rows=5)]), "bytes_total": 0},
                                 {**sch("hollow", [{**tbl("b", rows=0), "size_bytes": 0}]), "bytes_total": 0}]}
    v = view(world)
    assert node(v, "shop")["size_view"]["text"] == "100 B"
    assert node(v, "hollow")["size_view"]["text"] == "0 B"
    only_zero = {**sch("flat", [{**tbl("c", rows=5), "size_bytes": None}]), "bytes_total": 0}
    world["tree"] = {"schemas": [only_zero]}
    assert node(view(world), "flat")["size_view"]["text"] == "not measured"


def test_the_schema_inventory_route_reads_the_same_node_set_as_curate(client, world):
    """29 schemas from the Egeria survey, 8 from RE's stale local one: the Schema Inventory
    route answers with the 29, each carrying its source and as-of, and names both surveys."""
    _native(world["registry"])
    world["tree"] = _local_tree(8, 7)
    world["survey_at"] = LOCAL_AT
    j = client.get("/api/databases/db/schema-inventory-tree").json()
    real = [s for s in j["schemas"] if s.get("schema") and s["classification"] != "system"]
    assert len(real) == 29
    assert all(s["source"]["kind"] == "egeria" and s["source"]["as_of"] == NATIVE_AT for s in real)
    assert all(t["source"]["as_of"] == NATIVE_AT for s in real for t in s["tables"])
    src = j["sources"]
    assert (src["chosen"]["kind"], src["chosen"]["schemas"]) == ("egeria", 29)
    assert (src["local"]["schema_count"], src["local"]["table_count"], src["local"]["surveyed_at"]) == (8, 56, LOCAL_AT)
    assert src["disagree"] is True
    # and it is literally Curate's set, not a second merge
    cur = client.get("/api/catalogue-scope/db").json()
    assert [s["name"] for s in cur["schemas"]] == [s["schema"] for s in real]
    assert cur["sources"] == src
    # columns the native survey lacks are still filled from the local one
    assert next(s for s in real if s["schema"] == "s05")["tables"][3]["columns"][0]["name"] == "id"


def test_the_summary_says_when_the_credential_scoped_survey_was_taken(registry, client):
    registry.record_database_survey(
        "db", 8, 61, 479,
        {"credential_capability": {"connected_as": "surveyor", "schema_total": 8, "schema_visible": 6,
                                   "relation_total": 61, "relation_select": 3}},
        source="local", surveyed_at="2026-10-03T09:00:00")
    row = next(r for r in client.get("/api/databases/").json() if r["slug"] == "db")
    assert row["credential_capability"]["connected_as"] == "surveyor"
    assert row["credential_capability_at"].startswith("2026-10-03")


# ── the collapsed one-line summary reads what the route serves ──────────────

import json as _json2
import shutil as _shutil
import subprocess as _subprocess
from pathlib import Path as _Path

_SCOPE_SOURCES = (_Path(__file__).resolve().parents[1] / "resource_explorer" / "web" / "static"
                  / "next" / "stages" / "scope-sources.js")


def _scope_sources_mjs():
    import tempfile
    d = _Path(tempfile.mkdtemp(prefix="re-scope-sources-")) / "scope-sources.mjs"   # a bare .js is CommonJS to node
    d.write_text(_SCOPE_SOURCES.read_text(encoding="utf-8"), encoding="utf-8")
    return d


def _collapsed_line_for(view_json):
    """Run the shipped scopeCollapsedText (scope-sources.js, no imports) on the route's JSON."""
    out = _subprocess.run(
        ["node", "--input-type=module", "-e",
         f"import {{ scopeCollapsedText }} from '{_scope_sources_mjs().as_uri()}';"
         "let s='';process.stdin.on('data',d=>s+=d).on('end',()=>console.log(JSON.stringify(scopeCollapsedText(JSON.parse(s)))));"],
        input=_json2.dumps(view_json), capture_output=True, text=True, check=True)
    return _json2.loads(out.stdout)


@pytest.mark.skipif(_shutil.which("node") is None, reason="node not installed")
def test_the_collapsed_line_is_built_from_the_scope_route_json(client, registry, world):
    # nothing declared yet: no collapsed line (the section stays open)
    assert _collapsed_line_for(client.get("/api/catalogue-scope/db").json()) == ""
    _seven_then_twentynine(world)
    registry.record_database_survey("db", 29, 266, 1000, {"schema_info": {"x": 1}}, source="egeria",
                                    egeria_report_guid="g1", surveyed_at="2026-10-05T09:00:00")
    j = client.get("/api/catalogue-scope/db").json()
    line = _collapsed_line_for(j)
    assert line.startswith("Your scope: 7 of 29 schemas known to RE · declared by dwolfson 10-04")
    if j["survey"].get("state") == "measured":
        assert line.endswith(f"Egeria's latest survey covers {j['survey']['schema_count']} schemas, {j['survey']['table_count']} tables")
    else:   # no measured Egeria survey figures on the route: that clause is omitted, never zero
        assert "survey" not in line and "0 schemas" not in line
