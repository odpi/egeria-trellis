"""CSV in and out (slice 2 of discovery-sources-for-every-kind), server side.

REPLY-DESIGNER-DISCOVERY-SOURCES-ALL-KINDS.md sections 3 and 4, and the owner's
rulings of 2026-10-01/02: credentials NEVER appear in a CSV, in or out; a
database row names WHERE its credential comes from (`server`, a registered db
server, or `connection_ref`, a reference name) and never carries it.

Never touches the shared registry or a real database server: every registry is a
tmp SQLite file created inside the test, and the first fixture below fails the
test if that ever stops being true. No test connects to anything.
"""
from __future__ import annotations

import csv
import io
import json

import pytest
from fastapi.testclient import TestClient

from resource_explorer import batch_io
from resource_explorer.batch_io import (
    ALL_COLUMNS,
    INTENT_COLUMNS,
    NO_INVESTIGATION,
    NO_LENS,
    STATUS_COLUMNS,
    assert_no_credential_columns,
    export_filename,
    export_rows,
    import_file,
    is_credential_column,
    parse_csv_strict,
    plan_import,
    preview_file,
    rows_to_csv_text,
    scope_export_rows,
    write_csv,
)
from resource_explorer.registry import DatabaseServer, Project, ProjectRegistry

SECRET = "hunter2-do-not-leak"
CELL_SECRET = "csv-cell-pw-do-not-leak"


# ── safety: every registry in this file is a sqlite file under tmp_path ──────

@pytest.fixture(autouse=True)
def _registry_is_sqlite_under_tmp_path(tmp_path, monkeypatch):
    """The first thing every test checks: no registry here can be the shared
    Postgres. Also points the environment at a temp SQLite file and a dead
    pgvector port, so a stray `ProjectRegistry()` still cannot reach anything."""
    url = f"sqlite:///{tmp_path / 'env-registry.db'}"
    monkeypatch.setenv("REGISTRY_DATABASE_URL", url)
    monkeypatch.setenv("PGVECTOR_PORT", "1")
    yield
    # (the per-test `registry` fixture asserts its own path; see below)


@pytest.fixture(autouse=True)
def _a_signed_in_importer_with_no_zones(monkeypatch):
    """Brief T round 2: an import's group change takes the curation check (a group is a Folio in Egeria), which
    needs a signed-in caller. These tests are about the import, so a person is signed in and no zone is in use
    (the zone reads are faked at the client boundary: tests/zone_fakes.py). The refusal itself is pinned in
    tests/test_tags_groups_to_egeria.py."""
    from resource_explorer.a2a_auth import current_caller
    from tests.zone_fakes import install, signed_in

    install(monkeypatch)
    token = signed_in("importer")
    yield
    current_caller.reset(token)


@pytest.fixture
def registry(tmp_path):
    reg = ProjectRegistry(db_path=str(tmp_path / "t.db"))
    assert reg.database_url.startswith("sqlite:///" + str(tmp_path)), reg.database_url
    assert "5442" not in reg.database_url and "postgres" not in reg.database_url
    reg.register_server(DatabaseServer(
        slug="regional-pg", display_name="Regional PG", host="pg.regional", port=5432,
        db_user="scout_ro", db_password=SECRET))
    reg.create_group("regional", "Regional")
    reg.create_group("other", "Other")
    reg.add(Project(slug="sqlglot", display_name="sqlglot",
                    github_url="https://github.com/tobymao/sqlglot", description="",
                    collections=[]))
    # One database already registered, from the server.
    from resource_explorer.batch_io import build_database_entity
    reg.register_database(build_database_entity(reg.get_server("regional-pg"), "orders"))
    return reg


@pytest.fixture
def client(registry, monkeypatch):
    """TestClient whose every `ProjectRegistry()` is the tmp registry above."""
    monkeypatch.setattr(
        "resource_explorer.registry.ProjectRegistry.__init__",
        lambda self, db_path=None, database_url=None: setattr(self, "__dict__", registry.__dict__) or None,
    )
    from resource_explorer.web.app import app
    return TestClient(app)


HEADER = ("resource_type,address,display_name,group,server,connection_ref,disposition,"
          "notes,region,status_registered,db_password\n")

FIXTURE = HEADER + "\n".join([
    # line 2: new, importable (names a registered server)
    f"database,pg.regional:5432/sales,,regional,regional-pg,,,,eu,yes,{CELL_SECRET}",
    # line 3: already registered
    "database,pg.regional:5432/orders,,,regional-pg,,,,,,",
    # line 4: new, names neither: needs a person
    "database,pg.regional:5432/hr,,,,,,,,,",
    # line 5: the same address as line 2: duplicate in the file
    "database,pg.regional:5432/sales,,,regional-pg,,,,,,",
    # line 6: invalid (not host:port/name)
    "database,not-an-address,,,regional-pg,,,,,,",
    # line 7: a kind with no import path
    "filesystem,/mnt/share,,,,,,,,,",
    # line 8: new, importable through a connection_ref (a NAME)
    "database,pg.regional:5432/ref_only,,,,corp-secrets-entry,,,,,",
    # line 9: a repo: not importable from the databases dialog
    "repo,https://github.com/apache/airflow,,,,,,,,,",
    # line 10: a server that is not registered
    "database,pg.regional:5432/ghost,,,nowhere-pg,,,,,,",
]) + "\n"


# ── the five-count preview ───────────────────────────────────────────────────

def test_the_preview_is_the_five_counts_each_opening_its_lines(registry):
    p = preview_file(registry, FIXTURE)
    assert p["refused"] is None
    assert p["rows"] == 9
    assert p["counts"] == {
        "new": 3, "ready": 2, "needs_person": 1,
        "already_registered": 1, "already_in_investigation": 0,
        "duplicate_in_file": 1, "invalid": 2, "not_importable": 2,
    }
    by = lambda k: sorted(i["line"] for i in p["lines"][k])        # noqa: E731
    assert by("new") == [2, 4, 8]
    assert by("already_registered") == [3]
    assert by("duplicate_in_file") == [5]
    assert by("invalid") == [6, 10]            # each opens with its line number
    assert by("not_importable") == [7, 9]
    # the reasons are said, per line
    reasons = {i["line"]: i["message"] for k in p["lines"] for i in p["lines"][k]}
    assert "host:port/name" in reasons[6]
    assert "nowhere-pg" in reasons[10]
    assert "file system" in reasons[7]
    assert "Find repos" in reasons[9]
    assert "needs a person: name a server or a credential" in reasons[4]


def test_a_new_database_with_neither_server_nor_ref_is_not_importable_until_chosen(registry):
    plan = plan_import(registry, parse_csv_strict(FIXTURE).rows, importable=("database",))
    assert [r.line for r in plan.needs_person] == [4]
    assert 4 not in [r.line for r in plan.to_register]

    res = import_file(registry, FIXTURE, group="")
    slugs = sorted(r["slug"] for r in res["registered"])
    assert slugs == ["pg_regional_5432_ref_only", "regional_pg_sales"]
    assert registry.get_database("regional_pg_hr", allow_unreadable=True) is None

    # chosen in the preview -> importable
    p = preview_file(registry, FIXTURE, server_choices={4: "regional-pg"})
    assert p["counts"]["needs_person"] == 0
    res = import_file(registry, FIXTURE, server_choices={4: "regional-pg"})
    assert [r["slug"] for r in res["registered"]] == ["regional_pg_hr"]


def test_needs_person_rows_offer_the_servers_that_match_their_host(registry):
    p = preview_file(registry, FIXTURE)
    item = [i for i in p["lines"]["new"] if i["line"] == 4][0]
    assert item["matching_servers"] == ["regional_pg"]
    assert [s["slug"] for s in p["servers"]] == ["regional_pg"]
    # no credential field on a server in the payload
    assert all(set(s) == {"slug", "display_name", "host", "port"} for s in p["servers"])


def test_the_credential_comes_from_the_server_never_from_the_csv(registry):
    import_file(registry, FIXTURE)
    db = registry.get_database("regional_pg_sales")
    assert db.db_user == "scout_ro" and db.db_password == SECRET
    assert db.server_slug == "regional_pg" and db.group_slug == "regional"
    ref = registry.get_database("pg_regional_5432_ref_only")
    assert ref.connection_ref == "corp-secrets-entry"
    assert ref.db_user == "" and ref.db_password == ""     # a reference NAME; no stored credential


def test_a_connection_ref_must_be_a_name_not_a_value(registry):
    bad = HEADER + "database,pg.regional:5432/x,,,,user:pw@host/db,,,,,\n"
    p = preview_file(registry, bad)
    assert p["counts"]["invalid"] == 1 and "reference name" in p["lines"]["invalid"][0]["message"]
    assert registry.get_database("pg_regional_5432_x", allow_unreadable=True) is None


# ── columns: unknown, status_, credential-like, missing ──────────────────────

def test_unknown_and_status_columns_are_said_once(registry):
    p = preview_file(registry, FIXTURE)
    assert p["messages"].count("Ignored columns: region") == 1
    assert [m for m in p["messages"] if m.startswith("1 status_ column ignored")] == [
        "1 status_ column ignored: those are written by RE, never read"]
    assert p["ignored"]["unknown"] == ["region"]
    assert p["ignored"]["status"] == ["status_registered"]


def test_status_columns_never_decide_anything(registry):
    # status_registered=yes on a row that is not registered: classified from the registry alone
    p = preview_file(registry, FIXTURE)
    assert 2 in [i["line"] for i in p["lines"]["new"]]


def test_a_missing_required_column_refuses_the_file_naming_it_and_showing_the_header(registry):
    p = preview_file(registry, "name,url,notes\nfoo,bar,baz\n")
    assert p["refused"] and p["counts"] is None
    assert p["missing"] == ["resource_type", "address"]
    assert "name, url, notes" in p["refused"]
    p = preview_file(registry, "resource_type,display_name\ndatabase,x\n")
    assert p["missing"] == ["address"] and "resource_type, display_name" in p["refused"]
    p = preview_file(registry, "address,notes\npg:1/x,hi\n")
    assert p["missing"] == ["resource_type"]
    assert preview_file(registry, "")["refused"]


def test_line_numbers_are_the_files_own_lines(registry):
    text = ("# a comment\n\nresource_type,address\n"
            "database,pg.regional:5432/ok_one\n"
            "# another\ndatabase,\n")
    p = preview_file(registry, text, server_choices={})
    assert [i["line"] for i in p["lines"]["invalid"]] == [6]
    assert [i["line"] for i in p["lines"]["new"]] == [4]


def test_a_credential_like_column_is_named_and_its_values_never_read(registry):
    p = preview_file(registry, FIXTURE)
    assert p["ignored"]["credential"] == ["db_password"]
    assert any("Credential column(s) ignored: db_password" in m for m in p["messages"])
    # the cell's value is nowhere in anything the preview returns
    assert CELL_SECRET not in json.dumps(p)
    res = import_file(registry, FIXTURE)
    assert CELL_SECRET not in json.dumps(res)


@pytest.mark.parametrize("name", [
    "password", "db_password", "Password", "secret", "client_secret", "credential", "credentials",
    "token", "api_token", "auth_token", "api_key", "apikey", "private_key", "passphrase", "pwd",
    "connection_string", "dsn", "connection_ref_password",
])
def test_credential_like_names_are_recognised(name):
    assert is_credential_column(name)
    with pytest.raises(AssertionError):
        assert_no_credential_columns(["address", name])


@pytest.mark.parametrize("name", ["connection_ref", "server", "address", "display_name",
                                  "disposition_reason", "status_slug", "group"])
def test_ordinary_names_and_connection_ref_are_not_credential_columns(name):
    assert not is_credential_column(name)


def test_no_column_the_format_reads_or_writes_is_credential_like():
    assert_no_credential_columns(ALL_COLUMNS)
    assert_no_credential_columns(INTENT_COLUMNS)
    assert_no_credential_columns(STATUS_COLUMNS)
    assert "connection_ref" in INTENT_COLUMNS and "server" in INTENT_COLUMNS


def test_no_export_carries_a_credential_value_or_column(registry, tmp_path):
    inv = registry.create_investigation("Cred check", egeria_binding="local")
    ws = registry.get_or_create_working_set(inv["slug"])
    registry.add_working_set_member(ws["slug"], "database", "regional_pg_orders")
    outputs = [
        rows_to_csv_text(export_rows(registry)),
        rows_to_csv_text(scope_export_rows(registry, inv["slug"])),
        rows_to_csv_text(batch_io.candidate_export_rows(
            registry, [{"name": "orders", "address": "pg.regional:5432/orders",
                        "server_slug": "regional-pg"}])),
    ]
    out = tmp_path / "inv.csv"
    write_csv(export_rows(registry), out)
    outputs.append(out.read_text())
    for text in outputs:
        assert SECRET not in text and "scout_ro" not in text
        header = next(csv.reader(io.StringIO(text)))
        assert not [h for h in header if is_credential_column(h)]
        assert header == list(ALL_COLUMNS)


def test_a_credential_column_cannot_be_smuggled_into_a_write(monkeypatch, registry, tmp_path):
    monkeypatch.setattr(batch_io, "ALL_COLUMNS", tuple(ALL_COLUMNS) + ("db_password",))
    with pytest.raises(AssertionError):
        write_csv(export_rows(registry), tmp_path / "x.csv")
    with pytest.raises(AssertionError):
        rows_to_csv_text([])


# ── re-import is idempotent; a round trip changes nothing ────────────────────

def test_reimporting_the_same_file_gives_zero_new(registry):
    first = import_file(registry, FIXTURE, server_choices={4: "regional-pg"})
    assert len(first["registered"]) == 3
    p = preview_file(registry, FIXTURE)
    assert p["counts"]["new"] == 0 and p["counts"]["needs_person"] == 0
    assert p["counts"]["already_registered"] == 4        # orders + the three just added
    again = import_file(registry, FIXTURE)
    assert again["registered"] == [] and again["failures"] == []
    assert len(registry.list_databases()) == 4


def test_export_then_import_changes_nothing(registry):
    registry.set_database_group("regional_pg_orders", "regional")
    registry.set_disposition_for_entity("database", "regional_pg_orders", "tracking", reason="core")
    registry.set_disposition("https://github.com/tobymao/sqlglot", "using", reason="dep")
    text = rows_to_csv_text(export_rows(registry))
    before = (len(registry.list_databases()), len(registry.list_all()))
    p = preview_file(registry, text)
    assert p["refused"] is None
    assert p["counts"]["new"] == 0 and p["counts"]["invalid"] == 0
    assert p["counts"]["already_registered"] == 2         # the database and the repo, both registered
    assert p["proposed_changes"] == []
    res = import_file(registry, text)
    assert res["registered"] == [] and res["changed"] == [] and res["failures"] == []
    assert before == (len(registry.list_databases()), len(registry.list_all()))
    assert registry.get_database("regional_pg_orders").group_slug == "regional"


def test_intent_changes_on_registered_rows_are_proposed_and_applied_only_on_confirm(registry):
    registry.set_database_group("regional_pg_orders", "regional")
    text = "resource_type,address,group,disposition,disposition_reason\n" \
           "database,pg.regional:5432/orders,other,ignored,not ours\n"
    p = preview_file(registry, text)
    changes = {c["field"]: c for c in p["proposed_changes"]}
    assert changes["group"]["current"] == "regional" and changes["group"]["proposed"] == "other"
    assert changes["disposition"]["current"] == "undecided" and changes["disposition"]["proposed"] == "ignored"
    # shown, not applied
    res = import_file(registry, text)
    assert res["changed"] == []
    assert registry.get_database("regional_pg_orders").group_slug == "regional"
    assert not registry.get_disposition_for_entity("database", "regional_pg_orders")
    # applied only on confirm, and only the ones accepted
    res = import_file(registry, text, accept_changes=[{"line": 2, "field": "group"}])
    assert [c["field"] for c in res["changed"]] == ["group"]
    assert registry.get_database("regional_pg_orders").group_slug == "other"
    assert not registry.get_disposition_for_entity("database", "regional_pg_orders")
    res = import_file(registry, text, accept_changes=[{"line": 2, "field": "disposition"}])
    assert registry.get_disposition_for_entity("database", "regional_pg_orders")["disposition"] == "ignored"
    # a change to the same value is not proposed again
    assert preview_file(registry, text)["proposed_changes"] == []


def test_a_proposed_group_that_does_not_exist_is_blocked(registry):
    text = "resource_type,address,group\ndatabase,pg.regional:5432/orders,nowhere\n"
    ch = preview_file(registry, text)["proposed_changes"][0]
    assert ch["blocked"] and "nowhere" in ch["blocked"]
    res = import_file(registry, text, accept_changes=[{"line": 2, "field": "group"}])
    assert res["changed"] == []


def test_the_command_line_still_registers_repos_only(registry):
    rows = parse_csv_strict(FIXTURE).rows
    plan = plan_import(registry, rows, importable=("repo",))
    assert [r.line for r in plan.to_register] == [9]
    assert 2 in [r.line for r in plan.unsupported_type]


# ── exports: contract, words, filenames ──────────────────────────────────────

def test_export_filenames_say_what_and_when():
    assert export_filename("scope", "Customer 360", "2026-10-01") == "re-scope-customer-360-2026-10-01.csv"
    assert export_filename("inventory", date="2026-10-01") == "re-inventory-2026-10-01.csv"
    assert export_filename("work-list", "Q4 Cohort!", "2026-10-01") == "re-work-list-q4-cohort-2026-10-01.csv"
    assert export_filename("candidates", "Regional PG", "2026-10-01") == "re-candidates-regional-pg-2026-10-01.csv"


def test_intent_columns_come_first_then_status_columns():
    assert list(ALL_COLUMNS) == list(INTENT_COLUMNS) + list(STATUS_COLUMNS)
    assert INTENT_COLUMNS.index("server") < len(INTENT_COLUMNS)
    assert "status_in_scope" in STATUS_COLUMNS and "status_fit" in STATUS_COLUMNS
    assert all(c.startswith("status_") for c in STATUS_COLUMNS)


def _investigation_with(registry, name, members):
    inv = registry.create_investigation(name, egeria_binding="local")
    ws = registry.get_or_create_working_set(inv["slug"])
    for kind, slug, state in members:
        registry.add_working_set_member(ws["slug"], kind, slug, state=state)
    return inv


def test_scope_export_states_scope_and_fit_as_words(registry):
    inv = _investigation_with(registry, "Customer 360", [
        ("database", "regional_pg_orders", "in-scope"),
        ("repo", "sqlglot", "in-scope"),
        ("database", "regional_pg_gone", "in-scope"),         # no longer registered
        ("repo", "excluded-one", "excluded"),
    ])
    rows = scope_export_rows(registry, inv["slug"])
    by = {(r["resource_type"], r["status_slug"]): r for r in rows}
    assert set(by) == {("database", "regional_pg_orders"), ("repo", "sqlglot"),
                       ("database", "regional_pg_gone")}      # the excluded member is not exported
    assert by[("database", "regional_pg_orders")]["status_in_scope"] == "in scope"
    assert by[("database", "regional_pg_orders")]["status_fit"] == NO_LENS
    assert by[("database", "regional_pg_orders")]["server"] == "regional_pg"
    gone = by[("database", "regional_pg_gone")]
    assert gone["address"] == "" and "no longer" in gone["status_registered"]
    # a record, not an instruction: re-importing it names the gone one by line
    p = preview_file(registry, rows_to_csv_text(rows))
    assert [i["line"] for i in p["lines"]["invalid"]] and "no address" in p["lines"]["invalid"][0]["message"]


def test_other_exports_say_no_investigation_named(registry):
    rows = export_rows(registry)
    assert {r["status_in_scope"] for r in rows} == {NO_INVESTIGATION}
    assert {r["status_fit"] for r in rows} == {NO_INVESTIGATION}


def test_a_scope_exported_into_a_second_investigation_adds_resources_and_no_fit(registry):
    a = _investigation_with(registry, "Customer 360", [
        ("database", "regional_pg_orders", "in-scope"), ("repo", "sqlglot", "in-scope")])
    b = registry.create_investigation("Second look", egeria_binding="local")
    text = rows_to_csv_text(scope_export_rows(registry, a["slug"]))
    assert NO_LENS in text                              # the file says the fit in words

    p = preview_file(registry, text)
    assert p["counts"]["new"] == 0 and p["counts"]["already_registered"] == 2
    res = import_file(registry, text, investigation=b["slug"])
    members = {(m["entity_type"], m["entity_slug"]): m for m in registry.list_investigation_members(b["slug"])}
    assert ("database", "regional_pg_orders") in members and ("repo", "sqlglot") in members
    assert res["failures"] == []
    for m in members.values():
        assert m["state"] == "in-scope"
        # the fit did not travel as an instruction
        blob = json.dumps(m).lower()
        assert "fit" not in blob and "lens" not in blob
    # idempotent into the same investigation
    again = import_file(registry, text, investigation=b["slug"])
    assert len(registry.list_investigation_members(b["slug"])) == len(members)
    assert again["registered"] == []


def test_importing_into_a_missing_investigation_or_group_refuses(registry):
    with pytest.raises(ValueError):
        import_file(registry, FIXTURE, investigation="no-such")
    with pytest.raises(ValueError):
        import_file(registry, FIXTURE, group="no-such")
    with pytest.raises(ValueError):
        import_file(registry, "name,url\n")
    assert len(registry.list_databases()) == 1             # nothing was written


def test_only_the_lines_the_person_ticked_are_acted_on(registry):
    res = import_file(registry, FIXTURE, lines=[2])
    assert [r["slug"] for r in res["registered"]] == ["regional_pg_sales"]
    assert registry.get_database("pg_regional_5432_ref_only", allow_unreadable=True) is None


# ── the routes ───────────────────────────────────────────────────────────────

def test_preview_and_import_routes(client, registry):
    r = client.post("/api/discovery/from-file/preview", json={"text": FIXTURE})
    assert r.status_code == 200, r.text
    assert r.json()["counts"]["new"] == 3
    assert CELL_SECRET not in r.text and SECRET not in r.text

    r = client.post("/api/discovery/from-file/preview", json={"text": "a,b\n1,2\n"})
    assert r.status_code == 200 and r.json()["refused"] and r.json()["missing"]

    inv = registry.create_investigation("Into here", egeria_binding="local")
    r = client.post("/api/discovery/from-file/import",
                    json={"text": FIXTURE, "investigation": inv["slug"], "group": "regional",
                          "server_choices": {"4": "regional-pg"}})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["counts"]["registered"] == 3
    assert CELL_SECRET not in r.text and SECRET not in r.text
    members = {m["entity_slug"] for m in registry.list_investigation_members(inv["slug"])}
    # the three new ones, and the already-registered database (scope-only)
    assert {"regional_pg_sales", "regional_pg_hr", "pg_regional_5432_ref_only",
            "regional_pg_orders"} <= members

    bad = client.post("/api/discovery/from-file/import", json={"text": "x,y\n"})
    assert bad.status_code == 400 and "address" in bad.json()["detail"]
    bad = client.post("/api/discovery/from-file/preview",
                      json={"text": FIXTURE, "server_choices": {"not-a-line": "x"}})
    assert bad.status_code == 400


def test_export_routes_name_their_files(client, registry):
    day = batch_io.export_filename("x").split("-")[-3:]
    date = "-".join(day).removesuffix(".csv")
    r = client.get("/api/discovery/inventory.csv")
    assert r.status_code == 200
    assert f'filename="re-inventory-{date}.csv"' in r.headers["content-disposition"]
    assert SECRET not in r.text

    inv = _investigation_with(registry, "Customer 360", [("database", "regional_pg_orders", "in-scope")])
    r = client.get(f"/api/investigations/{inv['slug']}/scope.csv")
    assert r.status_code == 200, r.text
    assert f'filename="re-scope-{inv["slug"]}-{date}.csv"' in r.headers["content-disposition"]
    assert "in scope" in r.text and SECRET not in r.text
    assert client.get("/api/investigations/nope/scope.csv").status_code == 404

    from resource_explorer.work_lists import WorkLists
    wl = WorkLists(registry).create("Q4 cohort", ["regional_pg_orders"], entity_type="database",
                                    investigation=inv["slug"])
    r = client.get(f"/api/work-lists/{wl['slug']}/export.csv")
    assert r.status_code == 200, r.text
    assert f'filename="re-work-list-q4-cohort-{date}.csv"' in r.headers["content-disposition"]
    assert "regional_pg_orders" in r.text and "in scope" in r.text
    assert client.get("/api/work-lists/nope/export.csv").status_code == 404

    r = client.post("/api/discovery/candidates.csv", json={
        "label": "Regional PG", "server_slug": "regional-pg",
        "candidates": [{"name": "orders", "address": "pg.regional:5432/orders", "server_slug": "regional-pg"},
                       {"name": "hr", "address": "pg.regional:5432/hr", "server_slug": "regional-pg",
                        "verdict": {"disposition": "ignored", "reason": "not ours"}}]})
    assert r.status_code == 200
    assert f'filename="re-candidates-regional-pg-{date}.csv"' in r.headers["content-disposition"]
    rows = list(csv.DictReader(io.StringIO(r.text)))
    assert [(x["address"], x["server"], x["status_registered"]) for x in rows] == [
        ("pg.regional:5432/orders", "regional-pg", "yes"), ("pg.regional:5432/hr", "regional-pg", "no")]
    assert rows[1]["disposition"] == "ignored"
    # a candidate file is a file you can load back
    assert preview_file(registry, r.text)["counts"]["already_registered"] == 1


def test_add_database_returns_the_stored_slug_so_scope_points_at_something(client, registry):
    """A slice 1 defect found while building this: the route returned the slug it
    asked for ('regional-pg-x'), the registry stores 'regional_pg_x', and the
    dialog put the asked-for one into the investigation's scope."""
    r = client.post("/api/db-servers/regional-pg/add-database?database_name=fresh_one")
    assert r.status_code == 200, r.text
    assert r.json()["slug"] == "regional_pg_fresh_one"
    assert registry.get_database(r.json()["slug"], allow_unreadable=True)


# ── re-confirming a file into the investigation it is already in ─────────────

def _members(registry, inv_slug):
    return {(m["entity_type"], m["entity_slug"]): m for m in registry.list_investigation_members(inv_slug)}


def test_reimporting_into_the_same_investigation_offers_nothing_to_add(registry):
    inv = registry.create_investigation("Customer 360", egeria_binding="local")
    import_file(registry, FIXTURE, server_choices={4: "regional-pg"}, investigation=inv["slug"])
    assert len(_members(registry, inv["slug"])) == 4
    p = preview_file(registry, FIXTURE, investigation=inv["slug"])
    assert p["counts"]["already_registered"] == 4
    assert p["counts"]["already_in_investigation"] == 4
    assert all(i["in_investigation"] for i in p["lines"]["already_registered"])
    # a preview with no investigation chosen says nothing about membership
    p0 = preview_file(registry, FIXTURE)
    assert p0["counts"]["already_in_investigation"] == 0
    assert not any(i["in_investigation"] for i in p0["lines"]["already_registered"])


def test_a_scope_export_into_a_second_investigation_still_offers_its_rows(registry):
    a = _investigation_with(registry, "Customer 360", [
        ("database", "regional_pg_orders", "in-scope"), ("repo", "sqlglot", "in-scope")])
    b = registry.create_investigation("Second look", egeria_binding="local")
    text = rows_to_csv_text(scope_export_rows(registry, a["slug"]))
    # B has no working set yet: nothing is a member, nothing is created by looking
    p = preview_file(registry, text, investigation=b["slug"])
    assert p["counts"]["already_registered"] == 2 and p["counts"]["already_in_investigation"] == 0
    assert not any(i["in_investigation"] for i in p["lines"]["already_registered"])
    assert registry.investigation_working_set_slug(b["slug"]) == ""
    # the same file previewed against A: both are already there
    pa = preview_file(registry, text, investigation=a["slug"])
    assert pa["counts"]["already_in_investigation"] == 2
    # one member of B now: only that row flips
    import_file(registry, text, lines=[2], investigation=b["slug"])
    pb = preview_file(registry, text, investigation=b["slug"])
    assert pb["counts"]["already_in_investigation"] == 1
    assert sorted(i["line"] for i in pb["lines"]["already_registered"] if i["in_investigation"]) == [2]


def test_the_preview_route_takes_the_investigation(client, registry):
    inv = registry.create_investigation("Customer 360", egeria_binding="local")
    import_file(registry, FIXTURE, server_choices={4: "regional-pg"}, investigation=inv["slug"])
    r = client.post("/api/discovery/from-file/preview", json={"text": FIXTURE, "investigation": inv["slug"]})
    assert r.status_code == 200, r.text
    assert r.json()["counts"]["already_in_investigation"] == 4
    r = client.post("/api/discovery/from-file/preview", json={"text": FIXTURE})
    assert r.json()["counts"]["already_in_investigation"] == 0


def test_reconfirming_keeps_the_rationale_and_state_the_owner_set(registry):
    inv = registry.create_investigation("Customer 360", egeria_binding="local")
    import_file(registry, FIXTURE, server_choices={4: "regional-pg"}, investigation=inv["slug"],
                rationale="Loaded from a file on 2026-10-01")
    ws = registry.investigation_working_set_slug(inv["slug"])
    registry.add_working_set_member(ws, "database", "regional_pg_orders",
                                    membership_rationale="the system of record for orders", state="excluded")
    import_file(registry, FIXTURE, investigation=inv["slug"], rationale="Loaded from a file on 2026-10-03")
    m = _members(registry, inv["slug"])[("database", "regional_pg_orders")]
    assert m["membership_rationale"] == "the system of record for orders"
    assert m["state"] == "excluded"
    # an untouched member keeps its first import's words too
    other = _members(registry, inv["slug"])[("database", "regional_pg_sales")]
    assert other["membership_rationale"] == "Loaded from a file on 2026-10-01"


def test_a_first_time_add_still_writes_the_imports_rationale_and_state(registry):
    inv = registry.create_investigation("Customer 360", egeria_binding="local")
    import_file(registry, FIXTURE, investigation=inv["slug"], rationale="Loaded from a file on 2026-10-01")
    m = _members(registry, inv["slug"])[("database", "regional_pg_orders")]
    assert m["membership_rationale"] == "Loaded from a file on 2026-10-01"
    assert m["state"] == "in-scope"


def test_keep_existing_fills_an_empty_rationale_and_default_add_still_overwrites(registry):
    ws = registry.get_or_create_working_set(
        registry.create_investigation("Customer 360", egeria_binding="local")["slug"])["slug"]
    registry.add_working_set_member(ws, "repo", "sqlglot")                      # rationale empty
    registry.add_working_set_member(ws, "repo", "sqlglot", membership_rationale="filled", keep_existing=True)
    row = [m for m in registry.list_working_set_members(ws) if m["entity_slug"] == "sqlglot"][0]
    assert row["membership_rationale"] == "filled"
    # the explicit edit path (default) is unchanged: it overwrites
    registry.add_working_set_member(ws, "repo", "sqlglot", membership_rationale="edited", state="excluded")
    row = [m for m in registry.list_working_set_members(ws) if m["entity_slug"] == "sqlglot"][0]
    assert (row["membership_rationale"], row["state"]) == ("edited", "excluded")
