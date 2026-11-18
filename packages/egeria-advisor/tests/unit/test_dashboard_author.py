"""EA dashboard authoring (brief B7b): preview, confirm, write, as the signed-in user.

No live Egeria. The sheet writes go through pyegeria's REAL DashboardSheetEgeriaWriter into the vendored
in-memory Egeria (one per viewer, chosen by token, as in test_egeria_dashboards.py), which records every
write. The two Dr.Egeria documents (Create Report, Add Member to Collection) are run by a fake of EA's
Dr.Egeria executor that writes the same element shapes into that viewer's fake: it is the one seam faked.

What the brief asks to be shown: the preview equals the write; a retry writes once; a delete removes only
that sheet's elements; filing says so when the folder is missing; nothing is written without a confirm.
"""
from __future__ import annotations

import re

import pytest

from advisor import egeria_dashboards as ed
from advisor import egeria_dashboards_author as author

from test_egeria_dashboards import (  # noqa: F401 -- fixtures and the viewer fakes
    DEMO, ERIN, ERIN_TOKEN, PETER, PETER_TOKEN, _isolated, viewers,
)

SPEC = "Analytic Demo - Element Count by Type"


@pytest.fixture(autouse=True)
def _plans():
    author._reset_for_tests()
    yield
    author._reset_for_tests()


@pytest.fixture
def egeria(viewers, monkeypatch):
    """Dr.Egeria's executor, faked: Create Report makes a Report element, Add Member links the folder."""
    runs = []
    for fake in viewers.values():
        fake.zones = []

        async def add_zone(guid, body, _fake=fake):
            _fake.writes.append(("_async_add_zone_membership", guid))
            _fake.zones.append((guid, body["properties"]["zoneMembership"]))
        fake._async_add_zone_membership = add_zone

    async def run(conn, markdown, directive):
        fake = viewers[conn["token"]]
        runs.append((conn["user_id"], directive, markdown))
        detail = []
        if directive == "process" and "## Create Report" in markdown:
            name = re.search(r"### Display Name\n(.+)\n", markdown).group(1)
            spec = re.search(r"### Report Spec\n(.+)\n", markdown).group(1)
            guid = fake.by_qn(f"DeployedReport::{name}") or fake.add_entity(
                "Report", f"DeployedReport::{name}", name, additionalProperties={"reportSpec": spec})
            fake.writes.append(("dr_egeria Create Report", guid))
            detail = [{"guid": guid, "message": f"Created Report {name}"}]
        if directive == "process" and "## Add Member to Collection" in markdown:
            folder = fake.by_qn(re.search(r"### Collection Id\n(.+)\n", markdown).group(1))
            member = fake.by_qn(re.search(r"### Element Id\n(.+)\n", markdown).group(1))
            fake._link("CollectionMembership", folder, member)
            fake.writes.append(("dr_egeria Add Member", member))
            detail = [{"guid": member, "message": "Added member"}]
        return {"success": True, "validation_errors": [], "execution_errors": [], "commands_detail": detail}

    monkeypatch.setattr(author, "_run_dr_egeria", run)
    return runs


def _folder(fake):
    return fake.add_entity("Collection", author.CONSUMER_FOLDER_QN, "Egeria Advisor dashboards")


SAVE = {"sheet": "My Sheet", "new_sheet": {"heading": "My sheet", "description": "Mine"},
        "report": {"name": "Asset Count", "spec": SPEC, "output_format": "DICT",
                   "analytic_params": {"type_name": "Asset"}},
        "placement": {"span": "1", "emphasis": "kpi"}}


def _plan_and_apply(creds, kind, req, confirm=True):
    p = author.plan(creds, kind, req)
    assert p["state"] == "planned", p
    return p, author.apply(creds, p["plan_id"], confirm=confirm)


# ── save to a new sheet ───────────────────────────────────────────────────────

def test_the_preview_writes_nothing_and_lists_every_action(viewers, egeria):
    peter = viewers[PETER_TOKEN]
    _folder(peter)
    before = list(peter.writes)
    p = author.plan(PETER, "save", SAVE)
    assert p["state"] == "planned" and peter.writes == before
    assert [d for _, d, _ in egeria] == ["validate"]                   # the Report document was validated only
    acts = p["actions"]
    assert acts[0] == "create Dashboard Dashboard::My Sheet"
    assert "classify Ownership Dashboard::My Sheet (owner peterprofile)" in acts
    assert any(a.startswith("link DataSetContent Dashboard::My Sheet -> Report 'Asset Count'") for a in acts)
    assert p["report"]["state"] == "create" and "## Create Report" in p["dr_egeria"][0]
    assert p["filing"]["state"] == "will_file" and "## Add Member to Collection" in p["dr_egeria"][1]


def test_the_write_equals_the_preview_and_is_the_users(viewers, egeria):
    peter = viewers[PETER_TOKEN]
    folder = _folder(peter)
    p, out = _plan_and_apply(PETER, "save", SAVE)
    assert out["state"] == "written" and out["matches_preview"] is True, out
    assert [author._normal(a) for a in out["actions"]] == p["actions"]
    assert out["report"]["state"] == "created" and out["filing"]["state"] == "filed"
    dash = peter.by_qn("Dashboard::My Sheet")
    assert peter.entities[dash]["ownership"]["owner"] == "peterprofile"   # the real person owns it
    assert any(r["end1"] == folder and r["end2"] == dash for r in peter.rels("CollectionMembership"))
    assert {u for u, _, _ in egeria} == {"peterprofile"}                  # every Dr.Egeria run as the user
    shown = ed.show_sheet(PETER, "My Sheet")["sheet"]["placements"]
    assert [(x["kind"], x["title"], x["spec"]) for x in shown] == [("report", "Asset Count", SPEC)]


def test_a_retry_writes_once(viewers, egeria):
    peter = viewers[PETER_TOKEN]
    _folder(peter)
    p, first = _plan_and_apply(PETER, "save", SAVE)
    writes = list(peter.writes)
    again = author.apply(PETER, p["plan_id"], confirm=True)
    assert again["state"] == "already_written" and peter.writes == writes
    assert again["actions"] == first["actions"]


def test_saving_the_same_thing_again_changes_nothing(viewers, egeria):
    peter = viewers[PETER_TOKEN]
    _folder(peter)
    _plan_and_apply(PETER, "save", SAVE)
    writes = list(peter.writes)
    again = dict(SAVE, new_sheet=None)
    p, out = _plan_and_apply(PETER, "save", again)
    assert p["report"]["state"] == "existing"
    assert all(a.startswith("unchanged") for a in p["actions"]), p["actions"]
    assert out["state"] == "written" and peter.writes == writes


def test_nothing_is_written_without_a_confirm(viewers, egeria):
    p = author.plan(PETER, "save", SAVE)
    writes = list(viewers[PETER_TOKEN].writes)
    assert author.apply(PETER, p["plan_id"])["state"] == "refused"
    assert author.apply(PETER, p["plan_id"], confirm="yes")["state"] == "refused"
    assert viewers[PETER_TOKEN].writes == writes


def test_a_plan_is_applied_only_by_the_sign_in_that_made_it(viewers, egeria):
    p = author.plan(PETER, "save", SAVE)
    out = author.apply(ERIN, p["plan_id"], confirm=True)
    assert out["state"] == "refused" and "another sign-in" in out["message"]


# ── filing ────────────────────────────────────────────────────────────────────

def test_filing_says_so_when_the_folder_is_not_seeded(viewers, egeria):
    p, out = _plan_and_apply(PETER, "save", SAVE)          # no folder in this Egeria
    assert p["filing"]["state"] == "not_filed"
    assert out["filing"]["state"] == "not_filed"
    assert "not filed: the EgeriaAdvisor dashboard folder" in out["filing"]["message"]
    assert len(p["dr_egeria"]) == 1                         # no Add Member document was planned


def test_a_dashes_or_line_break_value_cannot_strip_a_reports_zones(viewers, egeria):
    """The dangerous case: a value that ends the Report's block before "### Zone Membership" would make a
    restricted Report public. Refused before any Dr.Egeria run."""
    for bad in ("---", "x\u2028---", "x\u2029### Zone Membership"):
        req = {**SAVE, "zones": ["finance"], "report": {**SAVE["report"], "description": bad}}
        assert author.plan(PETER, "save", req)["state"] == "refused", bad
    assert egeria == []


def test_two_plans_for_one_new_sheet_file_it_once(viewers, egeria):
    """Read-back B7b #1: a double-clicked Preview, or two plans, used to file twice (Add Member does not
    dedupe). Membership is read again at apply, so the second says "already filed"."""
    peter = viewers[PETER_TOKEN]
    folder = _folder(peter)
    first, second = author.plan(PETER, "save", SAVE), author.plan(PETER, "save", SAVE)
    assert first["filing"]["state"] == second["filing"]["state"] == "will_file"
    one = author.apply(PETER, first["plan_id"], confirm=True)
    two = author.apply(PETER, second["plan_id"], confirm=True)
    assert one["filing"]["state"] == "filed"
    assert two["state"] == "written" and two["filing"]["state"] == "already_filed"
    dash = peter.by_qn("Dashboard::My Sheet")
    assert sum(1 for r in peter.rels("CollectionMembership") if r["end1"] == folder and r["end2"] == dash) == 1
    assert [w[0] for w in peter.writes].count("dr_egeria Add Member") == 1


def test_a_filing_failure_says_why_and_never_fails_the_apply(viewers, egeria, monkeypatch):
    peter = viewers[PETER_TOKEN]
    _folder(peter)
    real = author._run_dr_egeria

    async def refusing(conn, markdown, directive):
        if "## Add Member to Collection" in markdown and directive == "process":
            raise RuntimeError("PROPERTY_SERVER_ERROR_500 the membership could not be stored")
        return await real(conn, markdown, directive)
    monkeypatch.setattr(author, "_run_dr_egeria", refusing)
    out = _plan_and_apply(PETER, "save", SAVE)[1]
    assert out["state"] == "written" and peter.by_qn("Dashboard::My Sheet")      # the sheet is written
    assert out["filing"]["state"] == "not_filed"
    assert "PROPERTY_SERVER_ERROR_500" in out["filing"]["message"] and "file it again" in out["filing"]["message"]


def test_a_preview_of_an_already_filed_sheet_plans_no_filing(viewers, egeria):
    peter = viewers[PETER_TOKEN]
    _folder(peter)
    _plan_and_apply(PETER, "save", SAVE)
    import asyncio
    state = asyncio.run(author._filing_state(peter, "My Sheet"))
    assert state["state"] == "already_filed" and "dr_egeria" not in state


def test_saving_to_an_existing_sheet_files_nothing(viewers, egeria):
    out = author.plan(PETER, "save", dict(SAVE, sheet=DEMO, new_sheet=None))
    assert out["state"] == "planned" and out["filing"] is None


# ── zones ─────────────────────────────────────────────────────────────────────

def test_a_restricted_sheet_and_its_report_carry_the_zones(viewers, egeria):
    peter = viewers[PETER_TOKEN]
    p, out = _plan_and_apply(PETER, "save", dict(SAVE, zones=["finance", "peterprofile"]))
    assert "classify ZoneMembership Dashboard::My Sheet (zones finance, peterprofile)" in p["actions"]
    assert "### Zone Membership\nfinance, peterprofile" in p["dr_egeria"][0]
    assert peter.zones == [(peter.by_qn("Dashboard::My Sheet"), ["finance", "peterprofile"])]


def test_zones_are_only_set_on_a_new_sheet(viewers, egeria):
    out = author.plan(PETER, "save", dict(SAVE, sheet=DEMO, new_sheet=None, zones=["finance"]))
    assert out["state"] == "refused" and "when a sheet is created" in out["message"]


# ── refusals ──────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("field,value", [
    ("name", "Count\n## Delete Dashboard Sheet"),
    ("name", "# heading"),
    ("description", "a | b"),
    ("description", "line\x00"),
    ("description", "x ___ y"),
    # every line break str.splitlines() honours (pyegeria parses with it), read-back B7b #2
    ("description", "a\u2028## Delete Collection X"),
    ("description", "a\u2029## Delete Collection X"),
    ("description", "a\x85## Delete Collection X"),
    ("description", "a\x0b### Zone Membership"),
    ("description", "a\x0c### Zone Membership"),
    ("description", "a\x1c### Zone Membership"),
    ("description", "a\x1e### Zone Membership"),
    ("description", "---"),
    ("description", "  -----  "),
    ("description", "> quoted"),
    ("description", " ### Zone Membership"),
])
def test_user_text_cannot_become_dr_egeria_structure(viewers, egeria, field, value):
    req = {**SAVE, "report": {**SAVE["report"], field: value}}
    out = author.plan(PETER, "save", req)
    assert out["state"] == "refused" and egeria == []


def test_an_existing_report_name_for_another_spec_is_refused(viewers, egeria):
    out = author.plan(PETER, "save", dict(SAVE, report={**SAVE["report"], "name": "Public Collections"}))
    assert out["state"] == "refused" and "already exists for spec 'Collections'" in out["message"]


def test_a_new_sheet_name_that_exists_is_refused(viewers, egeria):
    out = author.plan(PETER, "create_sheet", {"sheet": DEMO})
    assert out["state"] == "refused" and "already exists" in out["message"]


# ── move, remove, delete ──────────────────────────────────────────────────────

def _titles(creds, sheet):
    return [p["title"] for p in ed.show_sheet(creds, sheet, refresh=True)["sheet"]["placements"]]


def test_move_swaps_two_placements_and_keeps_their_reports(viewers, egeria):
    assert _titles(PETER, DEMO) == ["Public Collections", "Peter Only", "Read me"]
    p, out = _plan_and_apply(PETER, "move", {"sheet": DEMO, "placement": "Peter Only", "direction": "up"})
    assert out["state"] == "written" and out["matches_preview"] is True, out
    assert _titles(PETER, DEMO) == ["Peter Only", "Public Collections", "Read me"]
    kinds = [x["kind"] for x in ed.show_sheet(PETER, DEMO, refresh=True)["sheet"]["placements"]]
    assert kinds == ["report", "report", "text"]
    edge = author.plan(PETER, "move", {"sheet": DEMO, "placement": "Read me", "direction": "down"})
    assert edge["state"] == "refused" and "already last" in edge["message"]


def test_remove_deletes_the_placement_never_the_report(viewers, egeria):
    peter = viewers[PETER_TOKEN]
    report = peter.guids["peter"]
    _, out = _plan_and_apply(PETER, "remove", {"sheet": DEMO, "placement": "Peter Only"})
    assert out["state"] == "written"
    assert _titles(PETER, DEMO) == ["Public Collections", "Read me"]
    assert report in peter.entities


def test_delete_needs_the_sheets_name_and_removes_only_its_elements(viewers, egeria):
    peter = viewers[PETER_TOKEN]
    _folder(peter)
    _plan_and_apply(PETER, "save", SAVE)                         # a second sheet, "My Sheet"
    demo_chain = {g for g, e in peter.entities.items() if DEMO in e["props"]["qualifiedName"]}
    reports = set(peter.of_type("Report"))
    p = author.plan(PETER, "delete_sheet", {"sheet": "My Sheet"})
    assert p["confirm_with"] == "My Sheet"
    assert author.apply(PETER, p["plan_id"], confirm=True)["state"] == "refused"
    out = author.apply(PETER, p["plan_id"], confirm="My Sheet")
    assert out["state"] == "written" and out["matches_preview"] is True
    assert peter.by_qn("Dashboard::My Sheet") is None
    assert demo_chain <= set(peter.entities)                     # the other sheet is untouched
    assert reports <= set(peter.entities)                        # no Report deleted
    deleted = [w for w in peter.writes if w[0].startswith("delete_")]
    assert sorted(w[1] for w in deleted) == ["Dashboard", "DisplayDataContainer", "DisplayDataField",
                                             "DisplayDataSchemaType"]          # its chain, nothing else
    assert not {w[2] for w in deleted} & (demo_chain | reports)


# ── HTTP ──────────────────────────────────────────────────────────────────────

@pytest.fixture
def http(viewers, egeria, monkeypatch):
    from fastapi.testclient import TestClient
    from advisor import auth
    from advisor.web.app import app
    monkeypatch.setattr(auth, "_auth_enabled", lambda: True)
    client = TestClient(app, raise_server_exceptions=False)

    def as_user(user, token):
        return {"Authorization": f"Bearer {auth.create_access_token(user_id=user, egeria_token=token)}"}
    return client, as_user


def test_http_plan_then_apply(http):
    client, as_user = http
    peter = as_user("peterprofile", PETER_TOKEN)
    p = client.post("/api/dashboards/author/plan", headers=peter, json={"kind": "save", "request": SAVE}).json()
    assert p["state"] == "planned"
    out = client.post("/api/dashboards/author/apply", headers=peter, json={"plan_id": p["plan_id"], "confirm": True}).json()
    assert out["state"] == "written"
    assert client.post("/api/dashboards/author/plan", json={"kind": "save", "request": SAVE}).status_code == 401
    assert client.post("/api/dashboards/author/plan", headers=peter, json={"kind": "save"}).status_code == 422
