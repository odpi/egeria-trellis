"""Brief Z2 round 2 (2026-10-10): the Opus round-1 findings, each pinned by a test that fails on ed7e133c.

HIGH 1  a write with no real requester never runs on RE's service account's rights; `runs enqueue` attributes a
        publishing run to the signed-in person only; a schedule is checked when it is created.
HIGH 2  resync apply checks each resource it publishes or links.
MED 3   an element adopted by qualifiedName is checked before it is written (the repo publisher; the commit's
        database element).
MED 4   Egeria's anchors are emulated: an anchored member is judged on its anchor (ATTACH/DETACH/DELETE_MEMBER) plus
        READ on itself; an element already in Egeria with no zones is open whatever RE configures.
LOW 5-8 survey excludes a refused schema; an all-refused 403 lists the other blockers; doc-source rows are re-checked
        at drain time as their requester.

No Egeria anywhere: the access reads are faked at the client boundary (`tests/zone_fakes.py`), the catalogue at the
gateway (`tests/fake_egeria_catalogue.py`).
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from fake_egeria_catalogue import FakeEgeria as FakeCatalogue  # noqa: E402

from resource_explorer import catalogue_commit as cc  # noqa: E402
from resource_explorer import catalogue_scope as cs  # noqa: E402
from resource_explorer.registry import DatabaseEntity, Project, ProjectRegistry  # noqa: E402
from tests.zone_fakes import element as el  # noqa: E402
from tests.zone_fakes import install  # noqa: E402

ME = "test-caller"
SECURED = "finance-zone"


def _as(user_id, role="user"):
    from resource_explorer.a2a_auth import CallerIdentity, current_caller

    return current_caller.set(CallerIdentity(user_id=user_id, egeria_token="t", auth_source="app-jwt", role=role))


@pytest.fixture
def access(monkeypatch):
    monkeypatch.delenv("EXPLORER_PUBLISH_ZONES", raising=False)
    monkeypatch.delenv("RE_ISSUE_117_BLOCK_OFF", raising=False)
    return install(monkeypatch)


@pytest.fixture
def registry(tmp_path):
    r = ProjectRegistry(db_path=str(tmp_path / "r2.db"))
    assert str(tmp_path) in r.database_url                                    # never the shared registry
    r.add(Project(slug="p", display_name="P", github_url="https://github.com/x/p", description=""))
    r.set_egeria_asset_guid("p", "g-p")
    return r


def _secure(fake, guid, control, zones=(SECURED,), anchor=""):
    fake.elements[guid] = el(guid, list(zones), anchor=anchor)
    fake.controls[SECURED] = {"associatedSecurityList": control}


# ── MED 4: anchors, and no fallback to RE's configured zones for an existing element ───────────────────

class TestAnchors:
    def test_an_existing_element_with_no_zones_is_open_whatever_re_configures(self, registry, monkeypatch,
                                                                            signed_in_caller):
        from resource_explorer.workflows.curate import curation_access

        fake = install(monkeypatch, zones=[SECURED])           # RE configures SECURED
        fake.controls[SECURED] = {"associatedSecurityList": {"DEFAULT": ["dbas"]}}
        d = curation_access(registry, "repo", "p", "", element_guid="g-p")    # g-p carries no zone
        assert d.allowed, d.reason
        assert curation_access(registry, "repo", "p", "", element_guid="").allowed is False   # a new one: configured

    def test_an_anchored_member_is_judged_on_its_anchor_with_the_anchor_operation(self, registry, access,
                                                                                 signed_in_caller):
        from resource_explorer.workflows.curate import curation_access

        _secure(access, "g-db", {"ATTACH": [ME], "DETACH": [ME], "DELETE_MEMBER": ["dbas"]})
        access.elements["g-schema"] = el("g-schema", anchor="g-db")             # no zones of its own
        attach = curation_access(registry, "repo", "p", "", element_guid="g-schema", operations=("ATTACH",))
        assert attach.allowed and attach.zones == (SECURED,)
        delete = curation_access(registry, "repo", "p", "", element_guid="g-schema", operations=("DELETE",))
        assert not delete.allowed
        assert delete.reason == f"zone {SECURED} does not grant {ME} delete (anchored member) in Egeria"

    def test_the_member_must_also_be_readable(self, registry, access, signed_in_caller):
        from resource_explorer.workflows.curate import curation_access

        access.elements["g-db"] = el("g-db", ["db-zone"])
        access.controls["db-zone"] = {"associatedSecurityList": {"DEFAULT": [ME]}}
        _secure(access, "g-schema", {"READ": ["dbas"]}, anchor="g-db")
        d = curation_access(registry, "repo", "p", "", element_guid="g-schema", operations=("ATTACH",))
        assert d.reason == f"zone {SECURED} does not grant {ME} read in Egeria"

    def test_an_element_that_is_its_own_anchor_uses_its_own_zones(self, registry, access, signed_in_caller):
        from resource_explorer.workflows.curate import curation_access

        _secure(access, "g-p", {"DEFAULT": ["dbas"]}, anchor="g-p")
        assert not curation_access(registry, "repo", "p", "", element_guid="g-p").allowed

    def test_an_unreadable_anchor_denies(self, registry, access, signed_in_caller):
        from resource_explorer.workflows.curate import curation_access

        access.elements["g-schema"] = el("g-schema", anchor="g-db")
        access.fail_element = "g-db"
        d = curation_access(registry, "repo", "p", "", element_guid="g-schema", operations=("ATTACH",))
        assert d.reason.startswith("could not check access in Egeria (")


# ── the database commit, end to end: a zone granting ATTACH but not DELETE_MEMBER ──────────────────────

SOURCE = {"sales": {"orders": ["id"]}, "archive": {"old": ["id"]}}


def _tbl(name):
    return {"name": name, "table_type": "BASE TABLE", "row_count": 1, "row_count_state": "measured",
            "size_bytes": 1, "column_count": 1, "columns": [{"name": "id", "type": "integer", "nullable": False,
                                                             "key_role": "PK", "foreign_key": None, "comment": ""}]}


@pytest.fixture
def world(tmp_path, monkeypatch, access, signed_in_caller):
    r = ProjectRegistry(db_path=str(tmp_path / "commit.db"))
    r.register_database(DatabaseEntity(slug="db", display_name="Shop", db_type="postgresql", host="localhost",
                                       egeria_host="host.docker.internal", port=5442, database_name="shop"))
    tree = {"schemas": [{"schema": n, "table_count": 1, "row_total": 1, "bytes_total": None, "is_estimate": False,
                         "classification": "data", "reason": "", "tables": [_tbl(t) for t in tabs]}
                        for n, tabs in SOURCE.items()]}
    monkeypatch.setattr(cs, "_load_tree", lambda reg, slug: tree)
    monkeypatch.setattr(cs, "_tree_survey_at", lambda reg, slug: "2026-10-02T09:00:00")
    monkeypatch.setattr(cs, "_activity_counters", lambda reg, slug: {})
    monkeypatch.setattr(cs, "_row_sources", lambda reg, slug: {})
    return {"registry": r, "fake": FakeCatalogue(SOURCE), "access": access}


def _choose(w, schema, choice):
    cs.set_node_choice(w["registry"], "db", ME, schema=schema, table="", choice=choice)


def _press(w):
    out = cc.start_commit(w["registry"], "db", ME, gateway=w["fake"])
    return out, cc.execute_commit(w["registry"], out["curation"]["id"], gateway=w["fake"])


def _guid(w, schema):
    return w["fake"].read_element(cc.schema_qn(w["registry"].get_database("db"), schema)).guid


def _anchor_schemas_to_the_database(w, control):
    db_guid = w["registry"].get_database("db").egeria_asset_guid
    _secure(w["access"], db_guid, control)
    for s in SOURCE:
        w["access"].elements[_guid(w, s)] = el(_guid(w, s), anchor=db_guid)


def test_a_zone_granting_attach_but_not_delete_member_allows_attach_and_refuses_leave_out(world):
    _choose(world, "sales", "catalogue")
    _choose(world, "archive", "catalogue")
    _press(world)
    _anchor_schemas_to_the_database(world, {"DEFAULT": [ME], "DELETE_MEMBER": ["dbas"]})
    _choose(world, "archive", "leave_out")
    out, rec = _press(world)
    p = out["preview"]
    assert p["attach"] == ["sales"]                                           # attach on the anchor: granted
    leave = {r["schema"]: r for r in p["leave_out"]}
    assert leave["archive"]["form"] == "not_permitted"                       # DELETE_MEMBER on the anchor: refused
    assert leave["archive"]["reason"].endswith(f"does not grant {ME} delete (anchored member) in Egeria")


# ── LOW 5: the survey never names a schema refused at run time ─────────────────────────────────────────

def test_a_schema_refused_in_the_run_is_not_surveyed(world):
    _choose(world, "sales", "catalogue")
    _choose(world, "archive", "catalogue")
    _press(world)
    out = cc.start_commit(world["registry"], "db", ME, gateway=world["fake"])
    _secure(world["access"], _guid(world, "sales"), {"DEFAULT": ["dbas"]})   # refused between press and run
    cc.execute_commit(world["registry"], out["curation"]["id"], gateway=world["fake"])
    params = [c for c in world["fake"].calls if c[0] == "initiate_survey"][-1]
    assert "sales" not in str(params) and "archive" in str(params)


# ── LOW 7: every part refused, and the block too: a 403 that lists both ───────────────────────────────

def test_an_all_refused_press_still_names_the_issue_117_block(world):
    _choose(world, "sales", "catalogue")
    _choose(world, "archive", "catalogue")
    _press(world)
    _choose(world, "archive", "leave_out")
    _secure(world["access"], _guid(world, "sales"), {"DEFAULT": ["dbas"]})
    preview = cc.build_preview(world["registry"], "db", cs.build_scope_view(world["registry"], "db"), world["fake"])
    assert preview["all_refused"] is True
    assert [b["word"] for b in preview["also_blocked"]] == ["ISSUE-117 block"]
    with pytest.raises(cc.CommitBlocked) as exc:
        cc.start_commit(world["registry"], "db", ME, gateway=world["fake"])
    assert exc.value.status == 403
    assert any(r.startswith("ISSUE-117 block · ") for r in exc.value.reasons)


# ── MED 3: the commit's database element is resolved by qualified name ────────────────────────────────

def test_the_run_checks_the_database_element_found_by_qualified_name(world):
    _choose(world, "sales", "catalogue")
    _press(world)
    found = world["registry"].get_database("db").egeria_asset_guid
    world["registry"].set_database_egeria_guid("db", "")                      # RE forgot it; Egeria still has it
    out = cc.start_commit(world["registry"], "db", ME, gateway=world["fake"])
    _secure(world["access"], found, {"DEFAULT": ["dbas"]})
    rec = cc.execute_commit(world["registry"], out["curation"]["id"], gateway=world["fake"])
    first = next(s for s in rec["steps"] if s["name"] == "publish_elements")
    assert first["state"] == "failed" and first["detail"].startswith(f"not permitted · zone {SECURED}")


# ── MED 3: the repo publisher checks an asset it adopts by qualifiedName ───────────────────────────────

def test_the_publisher_refuses_an_adopted_asset_and_creates_nothing(registry, access, signed_in_caller):
    from resource_explorer.surveyors.egeria_publisher import EgeriaPublisher
    from resource_explorer.workflows.curate import WriteNotPermitted

    registry.clear_egeria_registration("p")
    _secure(access, "g-adopted", {"DEFAULT": ["dbas"]})
    pub = EgeriaPublisher.__new__(EgeriaPublisher)
    pub._registry = registry
    pub.zone_names = None
    maker = MagicMock()
    maker.find_assets.return_value = [{"elementHeader": {"guid": "g-adopted"},
                                       "properties": {"qualifiedName": "GitHubRepository::https://github.com/x/p"}}]
    pub._asset_maker = maker
    result = MagicMock(github_url="https://github.com/x/p", resource_slug="p")
    with pytest.raises(WriteNotPermitted) as exc:
        pub._find_or_create_asset(result)
    assert str(exc.value).startswith(f"not permitted · zone {SECURED} does not grant {ME}")
    assert not [c for c in maker.method_calls if c[0].startswith("create")]
    assert registry.get_egeria_asset_guid("p") in ("", None)                 # nothing cached


# ── HIGH 2: resync apply, per resource ─────────────────────────────────────────────────────────────────

def test_resync_publish_skips_a_refused_repository_and_publishes_the_rest(registry, access, signed_in_caller):
    from resource_explorer.egeria_resync import EgeriaResync

    registry.add(Project(slug="q", display_name="Q", github_url="https://github.com/x/q", description=""))
    _secure(access, "g-p", {"DEFAULT": ["dbas"]})
    r = EgeriaResync(registry=registry)
    done = []
    r._publish_one = lambda slug, steps: done.append(slug) or {"slug": slug, "ok": True}
    out = r._publish_many(["p", "q"], None)
    assert done == ["q"]
    assert out["not_permitted"] == [{"slug": "p", "words": f"not permitted · zone {SECURED} does not grant {ME} "
                                                            "update in Egeria"}]


def test_resync_relink_skips_a_refused_member(registry, access, signed_in_caller, monkeypatch):
    from resource_explorer.egeria_resync import EgeriaResync
    from resource_explorer.surveyors import egeria_investigation_publisher as eip

    registry.add(Project(slug="q", display_name="Q", github_url="https://github.com/x/q", description=""))
    _secure(access, "g-p", {"DEFAULT": ["dbas"]})
    seen = {}
    monkeypatch.setattr(registry, "list_investigations", lambda *a, **k: [{"slug": "i", "egeria_project_guid": "pg"}])
    monkeypatch.setattr(registry, "get_investigation", lambda slug: {"slug": slug, "egeria_project_guid": "pg"})
    monkeypatch.setattr(registry, "get_or_create_working_set", lambda slug: {"egeria_collection_guid": "cg"})
    monkeypatch.setattr(registry, "list_investigation_members", lambda slug: [
        {"entity_type": "repo", "entity_slug": "p"}, {"entity_type": "repo", "entity_slug": "q"}])
    monkeypatch.setattr(eip.EgeriaInvestigationPublisher, "_managers", lambda self: (None, None))
    # Brief Z3: the refusal is asked inside _link_members (for a member with an asset), so the real one runs; only
    # the queue boundary is faked (what is enqueued is what would be attached).
    monkeypatch.setattr(eip.EgeriaInvestigationPublisher, "_asset_guid", lambda self, et, s: f"g-{s}")
    monkeypatch.setattr("resource_explorer.egeria_outbox.enqueue_collection_members",
                        lambda reg, slug, cg, members, run_id="": seen.setdefault("members", members) and [])
    monkeypatch.setattr("resource_explorer.egeria_outbox.drain_outbox", lambda *a, **k: {})
    monkeypatch.setattr(registry, "investigation_working_set_slug", lambda slug: "ws-i")
    monkeypatch.setattr(registry, "get_working_set", lambda ws: {"egeria_collection_guid": "cg"})
    out = EgeriaResync(registry=registry)._do_relink_investigation_members()
    assert [m["entity_slug"] for m in seen["members"]] == ["q"]
    assert out["per_investigation"]["i"]["not_permitted"] == [
        {"entity_type": "repo", "slug": "p", "words": f"not permitted · zone {SECURED} does not grant {ME} attach "
                                                       "in Egeria"}]


# ── HIGH 1: runs enqueue attributes a publishing run to the person at the shell ────────────────────────

class TestRunsEnqueue:
    @pytest.mark.parametrize("kind", ["analysis_run", "survey_definition_run", "database_analysis_run"])
    def test_a_publishing_run_needs_a_session(self, registry, monkeypatch, kind):
        import typer

        from resource_explorer.cli import runs_commands as rc
        from resource_explorer.cli import session as cli_session

        monkeypatch.setattr(rc, "_registry", lambda: registry)

        def no_session(console=None):
            raise typer.Exit(code=2)
        monkeypatch.setattr(cli_session, "require_and_activate", no_session)
        with pytest.raises(typer.Exit) as exc:
            rc.runs_enqueue(kind, '{"slug": "p"}', requested_by="")
        assert exc.value.exit_code == 2 and registry.list_runs(kind=kind, limit=5) == []

    def test_requested_by_someone_else_is_refused(self, registry, monkeypatch):
        import typer

        from resource_explorer.cli import runs_commands as rc
        from resource_explorer.cli import session as cli_session

        monkeypatch.setattr(rc, "_registry", lambda: registry)
        monkeypatch.setattr(cli_session, "require_and_activate", lambda console=None: MagicMock(user_id="dana"))
        with pytest.raises(typer.Exit) as exc:
            rc.runs_enqueue("analysis_run", '{"slug": "p"}', requested_by="erin")
        assert exc.value.exit_code == 1 and registry.list_runs(kind="analysis_run", limit=5) == []
        rc.runs_enqueue("analysis_run", '{"slug": "p"}', requested_by="dana")
        assert [r["requested_by"] for r in registry.list_runs(kind="analysis_run", limit=5)] == ["dana"]


# ── HIGH 1: a schedule is checked when it is created ───────────────────────────────────────────────────

def test_creating_a_schedule_asks_the_access_check(registry, access, monkeypatch):
    from fastapi.testclient import TestClient

    monkeypatch.setattr("resource_explorer.registry.ProjectRegistry.__init__",
                        lambda self, db_path=None: setattr(self, "__dict__", registry.__dict__) or None)
    monkeypatch.setenv("TRELLIS_ANONYMOUS_READ", "true")
    monkeypatch.setattr("resource_explorer.auth.get_current_user",
                        lambda request: {"user_id": ME, "egeria_token": "t"})
    _secure(access, "g-p", {"DEFAULT": ["dbas"]})
    from resource_explorer.web.app import app

    r = TestClient(app).post("/api/schedules/repo/p", json={"analysis_id": "repo_health", "schedule": "daily"})
    assert r.status_code == 403, r.text
    assert r.json()["detail"] == f"zone {SECURED} does not grant {ME} update in Egeria"
    assert registry.get_schedules("repo", "p") == []                          # nothing saved


# ── LOW 8: a documentation-source row is re-checked at drain time, as its requester ────────────────────

def test_a_doc_source_row_is_refused_at_drain_time_for_its_requester(registry, access, monkeypatch):
    from resource_explorer import egeria_outbox as eo

    _secure(access, "g-p", {"DEFAULT": ["dbas"]})
    token = _as("dana")
    try:
        row_id = eo.enqueue_doc_source_publish(registry, "repo", "p", "s1", "https://docs.example/x")
    finally:
        from resource_explorer.a2a_auth import current_caller

        current_caller.reset(token)
    applied = []
    monkeypatch.setattr(eo, "apply_element", lambda *a, **k: applied.append(a) or "g")
    summary = eo.drain_outbox(registry, MagicMock(), lambda qn: "", element_id=row_id,
                              identity=MagicMock(kind="daemon"))
    assert applied == [] and summary["not_permitted"] == 1
    row = next(r for r in registry.list_outbox_elements(limit=10) if r["id"] == row_id)
    assert row["status"] == "dead"
    assert row["last_error"] == f"not permitted · zone {SECURED} does not grant dana attach in Egeria"
