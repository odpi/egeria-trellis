"""The ad-hoc Dr.Egeria document runner (owner, 2026-10-14: "load Dr.Egeria commands - sometimes adhoc").

The runner reuses the bootstrap's processor (`bootstrap.run_dr_egeria_document`, the `dr_egeria` CLI in a
subprocess), so the process boundary is faked at `bootstrap.subprocess.run` with the summary table the CLI
prints. The pyegeria steps (engine, service, SupportedGovernanceService link) talk to Egeria only through pyegeria:
those clients are `create_autospec(<real class>, spec_set=True)` installed at the one constructor seam
(`egeria_clients._build`). The registry is a real temp SQLite registry.
"""
from __future__ import annotations

import io
import json
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import create_autospec

import pytest

from resource_explorer.registry import ProjectRegistry

DAEMON = "resourceexplorernpa"
ENGINE_GUID = "0a1b2c3d-0000-4000-8000-00000000e001"
SERVICE_GUID = "0a1b2c3d-0000-4000-8000-00000000f001"
LINK_GUID = "0a1b2c3d-0000-4000-8000-00000000a001"
IDENT_PASSWORD = "pw-for-tests-only"


def _ident():
    from resource_explorer.bootstrap import DrEgeriaIdentity

    return DrEgeriaIdentity(user="tester", source="--as", password=IDENT_PASSWORD)


IDENT = _ident()

DOC = """<!-- a test declaration -->

## Create Governance Action Type
### Display Name
Resource Explorer Survey — db_derived

### Qualified Name
ResourceExplorerSurvey::re-db-derived

### Description
Zero-fetch derivation over already-stored rows.

___

## Link Action to Action Executor
### Governance Action Type
ResourceExplorerSurvey::re-db-derived

### Governance Engine
ResourceExplorerSurvey

### Request Type
re-db-derived

___
"""

STEPS = {
    "steps": [
        {"command": "Create Survey Action Engine", "qualified_name": "ResourceExplorerSurvey",
         "display_name": "Resource Explorer Survey Engine", "description": "Hosted by RE's worker."},
        {"command": "Create Survey Action Service",
         "qualified_name": "SurveyActionService::ResourceExplorerSurvey::db_derived",
         "display_name": "Resource Explorer Survey — db_derived", "description": "db_derived"},
        {"command": "Link Supported Governance Service", "governance_engine": "ResourceExplorerSurvey",
         "governance_service": "SurveyActionService::ResourceExplorerSurvey::db_derived",
         "request_type": "re-db-derived", "service_request_type": "re-db-derived"},
    ]
}


def _summary(rows: list[tuple[str, str, str, str, str]], errors: dict[str, list[str]] | None = None) -> str:
    """What `dr_egeria --process --summary-only` prints: the errors section, then the summary table, rendered
    with rich exactly as md_processing.dr_egeria.process_md_file_v2 builds it (same box, same columns)."""
    from rich.box import Box
    from rich.console import Console
    from rich.table import Table

    dotted = Box("┏━┳┓\n┃ ┃┃\n┡━╇┩\n│ ││\n├·┼┤\n├─┼┤\n│ ││\n└─┴┘\n")
    buf = io.StringIO()
    console = Console(width=int(os.environ.get("EGERIA_WIDTH", "4000")), file=buf)
    if errors:
        console.print("[bold red]Processing Errors:[/bold red]\n")
        for header, lines in errors.items():
            console.print(f"[red]-- {header} --[/red]")
            for e in lines:
                console.print(f"   [red]- {e}[/red]")
        console.print("\n")
    table = Table(title="Dr. Egeria v2 Processing Summary", show_header=True, show_lines=True, box=dotted)
    for col in ("Markdown Command", "Canonical Command", "Status", "Found", "Display Name / End 1",
                "Qualified Name / End2", "Message"):
        table.add_column(col, overflow="fold")
    for command, status, name, qn, message in rows:
        table.add_row(command, command, status, "No", name, qn, message)
    console.print(table)
    return buf.getvalue()


MATCHING_LINK = {
    "type": {"typeName": "SupportedGovernanceService"},
    "element": {"elementGUID": SERVICE_GUID},
    "relationshipProperties": {"propertyValueMap": {"requestType": {"primitiveValue": "re-db-derived"}}}}
OTHER_LINK = dict(MATCHING_LINK, element={"elementGUID": "0a1b2c3d-0000-4000-8000-00000000f999"})


def _as_user(monkeypatch, sub, admins=("dan",), admin_token=""):
    """Sign `sub` in (None: anonymous) and configure the admin list (FEEDBACK_ADMIN_USERS) and token."""
    from resource_explorer.config import get_config

    feedback = get_config().feedback
    monkeypatch.setattr(feedback, "admin_users", list(admins))
    monkeypatch.setattr(feedback, "admin_token", admin_token)
    monkeypatch.setattr("resource_explorer.auth.get_current_user",
                        lambda *a, **k: {"sub": sub} if sub else None)
    if sub:
        from resource_explorer import egeria_clients
        from resource_explorer.egeria_identity import EgeriaIdentity

        monkeypatch.setattr(egeria_clients, "Caller", lambda: EgeriaIdentity(
            user_id=sub, token="caller-token", kind=egeria_clients.KIND_CALLER))


OK_OUTPUT_ROWS = [
    ("Create Governance Action Type", "SUCCESS", "Resource Explorer Survey — db_derived",
     "ResourceExplorerSurvey::re-db-derived", "Created Governance Action Type"),
    ("Link Action to Action Executor", "SUCCESS", "", "", "Linked Action to Action Executor"),
]


@pytest.fixture
def root(tmp_path, monkeypatch):
    docs = tmp_path / "dr-egeria"
    (docs / "engine-host").mkdir(parents=True)
    (docs / "engine-host" / "decl.md").write_text(DOC)
    (docs / "engine-host" / "decl.pyegeria-steps.json").write_text(json.dumps(STEPS))
    (docs / "plain").mkdir()
    (docs / "plain" / "only.md").write_text(DOC)
    monkeypatch.setenv("RE_DR_EGERIA_DOCS_DIR", str(docs))
    monkeypatch.setenv("EGERIA_USER_ID", DAEMON)
    monkeypatch.setenv("EGERIA_USER_PASSWORD", "daemon-secret")
    monkeypatch.setenv("EGERIA_USER", "someone-else")          # what an inherited env would have run as
    return docs


@pytest.fixture
def reg(tmp_path):
    return ProjectRegistry(db_path=str(tmp_path / "runner.db"))


class Fakes:
    """The pyegeria boundary plus the dr_egeria process boundary, recording one ordered call log."""

    def __init__(self, monkeypatch, *, output: str | None = None, engine_exists=False, service_exists=False,
                 link_exists=False):
        from pyegeria import AssetMaker, MetadataExpert

        from resource_explorer import bootstrap, egeria_clients

        self.calls: list[str] = []
        self.built: list[tuple[str, str]] = []
        self.subprocess_envs: list[dict] = []
        self.argvs: list[list[str]] = []
        self.output = output if output is not None else _summary(OK_OUTPUT_ROWS)
        self.expert = create_autospec(MetadataExpert, spec_set=True, instance=True)
        self.maker = create_autospec(AssetMaker, spec_set=True, instance=True)
        known = {}
        if engine_exists:
            known["ResourceExplorerSurvey"] = ENGINE_GUID
        if service_exists:
            known["SurveyActionService::ResourceExplorerSurvey::db_derived"] = SERVICE_GUID
        self.known = known

        def by_name(name=None, property_name="qualifiedName", as_of_time=None, body=None, **kwargs):
            self.calls.append(f"find {name}")
            return self.known.get(name, "No elements found")      # pyegeria's live NO_ELEMENTS_FOUND

        def create_capability(body=None):
            self.calls.append("create engine")
            self.known[body["properties"]["qualifiedName"]] = ENGINE_GUID
            return ENGINE_GUID

        def create_asset(asset_type=None, body=None):
            self.calls.append("create service")
            self.known[body["properties"]["qualifiedName"]] = SERVICE_GUID
            return SERVICE_GUID

        #: Pages of the engine's SupportedGovernanceService read, by page number; past the end: "No elements found".
        self.related_pages: list = [{"elementList": [MATCHING_LINK]}] if link_exists else []
        self.link_answer = LINK_GUID
        self.link_visible_after_create = False

        def related(guid, relationship_type, body, for_lineage=None, for_duplicate_processing=None,
                    starting_at_end=0, start_from=0, page_size=500, timeout=30, mermaid_only=False, **kwargs):
            self.calls.append(f"related {relationship_type}")
            page = (body or {}).get("startFrom", 0) // max((body or {}).get("pageSize", 1), 1)
            return self.related_pages[page] if page < len(self.related_pages) else "No elements found"

        def link(governance_engine_guid, governance_service_guid, body=None):
            self.calls.append("link supported service")
            self.link_body = body
            if self.link_visible_after_create:
                self.related_pages = [{"elementList": [dict(MATCHING_LINK, relationshipGUID=LINK_GUID)]}]
            return self.link_answer

        self.expert.get_metadata_guid_by_unique_name.side_effect = by_name
        self.expert.get_related_metadata_elements.side_effect = related
        self.maker.create_software_capability.side_effect = create_capability
        self.maker.create_asset.side_effect = create_asset
        self.maker.link_supported_governance_service.side_effect = link
        self.expert.create_egeria_bearer_token.return_value = "tok"
        self.maker.create_egeria_bearer_token.return_value = "tok"
        by_cls = {MetadataExpert: self.expert, AssetMaker: self.maker}

        def build(cls, view_server, platform_url, user_id, password):
            self.built.append((cls.__name__, user_id))
            return by_cls[cls]

        monkeypatch.setattr(egeria_clients, "_build", build)

        real_run = bootstrap.subprocess.run      # the stdlib module: `ps` (process_identity) must still work

        def fake_run(argv, **kwargs):
            child = "resource_explorer.dr_egeria_child" in argv
            if not child:
                return real_run(argv, **kwargs)
            self.argvs.append(list(argv))
            self.calls.append("dr_egeria " + " ".join(a for a in argv[1:] if not a.startswith("/")
                                                       and a not in ("--user_pass",) and a != IDENT_PASSWORD))
            self.subprocess_envs.append(dict(kwargs.get("env") or {}))
            return SimpleNamespace(returncode=0, stdout=self.output, stderr="")

        monkeypatch.setattr(bootstrap.subprocess, "run", fake_run)

    def writes(self) -> list[str]:
        return [c for c in self.calls if not c.startswith(("find ", "related "))]


def _rows(reg) -> list[dict]:
    return reg.list_activity(operation="dr_egeria")


# ── dry run ─────────────────────────────────────────────────────────────────

def test_dry_run_builds_no_client_runs_nothing_and_writes_no_row(root, reg, monkeypatch):
    from resource_explorer import dr_egeria_runner as runner

    fakes = Fakes(monkeypatch)
    out = runner.run_document("engine-host/decl.md", dry_run=True, identity=IDENT, registry=reg)

    assert fakes.calls == [] and fakes.built == [], "a dry run makes no Egeria call and starts no dr_egeria"
    assert _rows(reg) == []
    assert out["status"] == "planned" and out["dry_run"] is True
    assert [c["command"] for c in out["commands"]] == [
        "Create Survey Action Engine", "Create Survey Action Service", "Link Supported Governance Service",
        "Create Governance Action Type", "Link Action to Action Executor"]
    assert out["not_safe_to_repeat"] == ["Link Action to Action Executor"]
    assert all(c["status"] == "planned" for c in out["commands"])


def test_dry_run_reports_an_unknown_command_as_invalid(root, reg, monkeypatch):
    from resource_explorer import dr_egeria_runner as runner

    (root / "plain" / "bad.md").write_text("## Create Frobnicator Thing\n### Display Name\nx\n")
    Fakes(monkeypatch)
    out = runner.run_document("plain/bad.md", dry_run=True, identity=IDENT, registry=reg)
    assert out["valid"] is False and any("Frobnicator" in e for e in out["errors"])
    with pytest.raises(runner.DocumentRefused):
        runner.run_document("plain/bad.md", identity=IDENT, registry=reg)
    assert _rows(reg) == []


def test_a_destructive_command_is_refused(root, reg, monkeypatch):
    from resource_explorer import dr_egeria_runner as runner

    (root / "plain" / "del.md").write_text("## Delete Glossary Term\n### Qualified Name\nx\n")
    fakes = Fakes(monkeypatch)
    with pytest.raises(runner.DocumentRefused, match="owner"):
        runner.run_document("plain/del.md", identity=IDENT, registry=reg)
    assert fakes.calls == []


# ── logging ─────────────────────────────────────────────────────────────────

def test_a_run_writes_the_running_row_first_then_a_terminal_row_with_per_command_results(root, reg, monkeypatch):
    from resource_explorer import dr_egeria_runner as runner
    from resource_explorer.run_reconciler import process_identity

    fakes = Fakes(monkeypatch)
    written = []
    real_write = reg.write_activity

    def spy(entry):
        written.append((entry.status, json.loads(entry.detail or "{}"), list(entry.items), list(fakes.calls)))
        return real_write(entry)

    monkeypatch.setattr(reg, "write_activity", spy)
    out = runner.run_document("engine-host/decl.md", identity=IDENT, registry=reg)

    first_status, first_detail, _items, calls_before = written[0]
    assert first_status == "running" and calls_before == [], "the running row is written before any work"
    assert first_detail["_runner"]["pid"] == process_identity()["pid"]
    assert first_detail["sha256"] and first_detail["document"] == "engine-host/decl.md"

    row = _rows(reg)[0]
    assert row["status"] == "ok" and row["id"] == out["activity_id"]
    assert "_runner" not in json.loads(row["detail"])
    items = row["items"]
    assert [(i["command"], i["status"]) for i in items] == [
        ("Create Survey Action Engine", "ok"), ("Create Survey Action Service", "ok"),
        ("Link Supported Governance Service", "ok"), ("Create Governance Action Type", "ok"),
        ("Link Action to Action Executor", "ok")]
    assert items[0]["element"] == "ResourceExplorerSurvey" and items[0]["outcome"] == "created"
    assert items[3]["element"] == "ResourceExplorerSurvey::re-db-derived"
    # Status words come from the persisted row.
    assert out["status"] == row["status"] and out["commands"] == items


def test_a_failing_command_is_recorded_with_its_error_words(root, reg, monkeypatch):
    from resource_explorer import dr_egeria_runner as runner

    output = _summary(
        [OK_OUTPUT_ROWS[0],
         ("Link Action to Action Executor", "FAILURE", "", "", "Execution failed: engine not found")],
        errors={"Link Action to Action Executor ()": ["Governance Engine ResourceExplorerSurvey not found"]})
    Fakes(monkeypatch, output=output)
    out = runner.run_document("plain/only.md", identity=IDENT, registry=reg)

    row = _rows(reg)[0]
    assert row["status"] == "error" and out["status"] == "error"
    last = row["items"][-1]
    assert last["command"] == "Link Action to Action Executor" and last["status"] == "error"
    assert "engine not found" in last["error"]
    assert "ResourceExplorerSurvey not found" in last["error"]


def test_output_that_cannot_be_read_is_unknown_never_ok(root, reg, monkeypatch):
    from resource_explorer import dr_egeria_runner as runner

    Fakes(monkeypatch, output="Traceback (most recent call last):\n  boom\n")
    out = runner.run_document("plain/only.md", identity=IDENT, registry=reg)
    row = _rows(reg)[0]
    assert row["status"] == "error" and out["status"] == "error"
    assert {i["status"] for i in row["items"]} == {"unknown"}



def test_a_summary_with_a_missing_row_is_unknown_only_for_that_command(root, reg, monkeypatch):
    """One SUCCESS row for a two-command document: the command it names is ok, the one without a row is
    unknown and says so."""
    from resource_explorer import dr_egeria_runner as runner

    Fakes(monkeypatch, output=_summary(OK_OUTPUT_ROWS[:1]))
    runner.run_document("plain/only.md", identity=IDENT, registry=reg)
    row = _rows(reg)[0]
    assert row["status"] == "error"
    assert [i["status"] for i in row["items"]] == ["ok", "unknown"]
    assert "1 row(s) for 2 command(s)" in row["items"][1]["error"]


def test_a_failed_pyegeria_step_stops_the_run_before_dr_egeria(root, reg, monkeypatch):
    from resource_explorer import dr_egeria_runner as runner

    fakes = Fakes(monkeypatch)
    fakes.maker.create_asset.side_effect = RuntimeError("Egeria said no")
    runner.run_document("engine-host/decl.md", identity=IDENT, registry=reg)
    row = _rows(reg)[0]
    assert row["status"] == "error"
    statuses = [(i["command"], i["status"]) for i in row["items"]]
    assert statuses[1] == ("Create Survey Action Service", "error")
    assert "Egeria said no" in row["items"][1]["error"]
    assert [s for _c, s in statuses[2:]] == ["not_run"] * 3
    assert not any(c.startswith("dr_egeria") for c in fakes.calls)


# ── repeats ─────────────────────────────────────────────────────────────────

def test_a_second_run_of_the_same_document_is_refused_without_force(root, reg, monkeypatch):
    from resource_explorer import dr_egeria_runner as runner

    fakes = Fakes(monkeypatch)
    first = runner.run_document("engine-host/decl.md", identity=IDENT, registry=reg)
    n = len(fakes.calls)
    with pytest.raises(runner.RepeatRefused) as refused:
        runner.run_document("engine-host/decl.md", identity=IDENT, registry=reg)
    assert first["activity_id"] in str(refused.value)
    assert "Link Action to Action Executor" in str(refused.value)
    assert len(fakes.calls) == n and len(_rows(reg)) == 1, "a refused repeat touches nothing"

    again = runner.run_document("engine-host/decl.md", force=True, identity=IDENT, registry=reg)
    assert again["status"] == "ok" and len(_rows(reg)) == 2


def test_a_changed_document_is_a_new_document(root, reg, monkeypatch):
    from resource_explorer import dr_egeria_runner as runner

    Fakes(monkeypatch)
    runner.run_document("plain/only.md", identity=IDENT, registry=reg)
    (root / "plain" / "only.md").write_text(DOC.replace("Zero-fetch", "Zero fetch"))
    assert runner.run_document("plain/only.md", identity=IDENT, registry=reg)["status"] == "ok"


def test_an_interrupted_run_still_counts_as_applied(root, reg, monkeypatch):
    from resource_explorer import dr_egeria_runner as runner

    Fakes(monkeypatch)
    out = runner.run_document("plain/only.md", identity=IDENT, registry=reg)
    reg.update_activity_status(out["activity_id"], "interrupted")
    with pytest.raises(runner.RepeatRefused):
        runner.run_document("plain/only.md", identity=IDENT, registry=reg)


# ── path ────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("bad", ["/etc/passwd", "../outside.md", "engine-host/../../outside.md",
                                 "engine-host/decl.pyegeria-steps.json", "missing.md", "link.md"])
def test_only_markdown_under_the_docs_directory_is_accepted(root, reg, monkeypatch, bad):
    from resource_explorer import dr_egeria_runner as runner

    (root.parent / "outside.md").write_text(DOC)
    (root / "link.md").symlink_to(root.parent / "outside.md")
    fakes = Fakes(monkeypatch)
    with pytest.raises(runner.DocumentRefused):
        runner.run_document(bad, identity=IDENT, registry=reg)
    with pytest.raises(runner.DocumentRefused):
        runner.run_document(bad, dry_run=True, identity=IDENT, registry=reg)
    assert fakes.calls == [] and _rows(reg) == []


def test_the_default_root_is_the_repo_docs_dr_egeria(monkeypatch):
    from resource_explorer import bootstrap, dr_egeria_runner as runner

    monkeypatch.delenv("RE_DR_EGERIA_DOCS_DIR", raising=False)
    assert runner.docs_root() == bootstrap.DOCS_DIR.resolve()
    assert runner.resolve_document("engine-host/resource-explorer-survey-engine.md").is_file()


# ── pyegeria steps ──────────────────────────────────────────────────────────

def test_find_or_create_never_creates_what_already_exists(root, reg, monkeypatch):
    from resource_explorer import dr_egeria_runner as runner

    fakes = Fakes(monkeypatch, engine_exists=True, service_exists=True, link_exists=True)
    runner.run_document("engine-host/decl.md", identity=IDENT, registry=reg)
    fakes.maker.create_software_capability.assert_not_called()
    fakes.maker.create_asset.assert_not_called()
    fakes.maker.link_supported_governance_service.assert_not_called()
    items = _rows(reg)[0]["items"]
    assert [i["outcome"] for i in items[:3]] == ["found", "found", "found"]


def test_an_engine_that_exists_is_found_and_only_the_missing_parts_are_created(root, reg, monkeypatch):
    from resource_explorer import dr_egeria_runner as runner

    fakes = Fakes(monkeypatch, engine_exists=True)
    runner.run_document("engine-host/decl.md", identity=IDENT, registry=reg)
    fakes.maker.create_software_capability.assert_not_called()
    fakes.maker.create_asset.assert_called_once()
    fakes.maker.link_supported_governance_service.assert_called_once()
    assert fakes.link_body["properties"]["requestType"] == "re-db-derived"
    assert fakes.link_body["properties"]["serviceRequestType"] == "re-db-derived"


def test_a_lookup_that_cannot_answer_creates_nothing(root, reg, monkeypatch):
    from resource_explorer import dr_egeria_runner as runner

    fakes = Fakes(monkeypatch)
    fakes.expert.get_metadata_guid_by_unique_name.side_effect = lambda *a, **k: None
    runner.run_document("engine-host/decl.md", identity=IDENT, registry=reg)
    fakes.maker.create_software_capability.assert_not_called()
    assert _rows(reg)[0]["items"][0]["status"] == "error"


def test_engine_then_service_then_link_then_the_action_type(root, reg, monkeypatch):
    from resource_explorer import dr_egeria_runner as runner

    fakes = Fakes(monkeypatch)
    runner.run_document("engine-host/decl.md", identity=IDENT, registry=reg)
    assert fakes.writes() == ["create engine", "create service", "link supported service",
                              "dr_egeria -m resource_explorer.dr_egeria_child --server qs-view-server --url https://localhost:9443 --userid tester decl.md"]


def test_a_steps_file_out_of_order_is_refused(root, reg, monkeypatch):
    from resource_explorer import dr_egeria_runner as runner

    steps = {"steps": [STEPS["steps"][2], STEPS["steps"][0], STEPS["steps"][1]]}
    (root / "engine-host" / "decl.pyegeria-steps.json").write_text(json.dumps(steps))
    fakes = Fakes(monkeypatch)
    with pytest.raises(runner.DocumentRefused, match="order"):
        runner.run_document("engine-host/decl.md", identity=IDENT, registry=reg)
    assert fakes.calls == []


# ── identity ────────────────────────────────────────────────────────────────

def test_every_client_and_the_child_run_as_the_supplied_user(root, reg, monkeypatch):
    from resource_explorer import dr_egeria_runner as runner

    monkeypatch.setenv("EGERIA_USER", "someone-else")                  # the environment must not matter
    monkeypatch.setenv("EGERIA_USER_PASSWORD", "env-secret")
    fakes = Fakes(monkeypatch)
    lines = []
    out = runner.run_document("engine-host/decl.md", registry=reg, identity=IDENT, announce=lines.append)
    assert fakes.built and {u for _c, u in fakes.built} == {"tester"}
    argv = fakes.argvs[0]
    assert argv[argv.index("--userid") + 1] == "tester" and IDENT_PASSWORD not in " ".join(argv)
    assert fakes.subprocess_envs[0]["RE_DR_EGERIA_CHILD_PASSWORD"] == IDENT_PASSWORD
    assert "--server" in argv and "--url" in argv
    env = fakes.subprocess_envs[0]
    assert not {"EGERIA_USER", "EGERIA_USER_PASSWORD", "EGERIA_USER_ID"} & set(env)
    assert lines == ["applying as tester from --as"]
    row = _rows(reg)[0]
    detail = json.loads(row["detail"])
    assert detail["applying_as"] == "tester" and detail["identity_source"] == "--as"
    assert out["applying_as"] == "tester"
    assert IDENT_PASSWORD not in json.dumps(row, default=str)


def test_a_real_run_with_no_identity_is_refused_even_with_egeria_user_in_the_environment(root, reg, monkeypatch):
    """Fail on purpose: pyegeria's CLI would pick its own user. RE must never reach that path."""
    from resource_explorer import dr_egeria_runner as runner

    monkeypatch.setenv("EGERIA_USER", "resourceexplorernpa")
    fakes = Fakes(monkeypatch)
    with pytest.raises(runner.NoIdentity, match="no Egeria identity set"):
        runner.run_document("engine-host/decl.md", registry=reg)
    assert fakes.calls == [] and fakes.argvs == [] and _rows(reg) == []


def test_the_processor_itself_refuses_without_an_identity(tmp_path, monkeypatch):
    from resource_explorer import bootstrap

    Fakes(monkeypatch)
    doc = tmp_path / "d.md"
    doc.write_text("x")
    with pytest.raises(bootstrap.NoDrEgeriaIdentity):
        bootstrap.run_dr_egeria_document(doc, identity=None)
    with pytest.raises(bootstrap.NoDrEgeriaIdentity):
        bootstrap.DrEgeriaIdentity(user="", source="--as", password="x")


def test_a_signed_in_person_runs_the_child_with_the_token_in_env_only(root, reg, monkeypatch):
    from resource_explorer import dr_egeria_runner as runner
    from resource_explorer.bootstrap import SOURCE_ADMIN_SESSION, DrEgeriaIdentity

    fakes = Fakes(monkeypatch)
    ident = DrEgeriaIdentity(user="dan", source=SOURCE_ADMIN_SESSION, token="dans-token")
    runner.run_document("plain/only.md", registry=reg, identity=ident)
    argv = fakes.argvs[0]
    assert "resource_explorer.dr_egeria_child" in argv and argv[argv.index("--userid") + 1] == "dan"
    assert "dans-token" not in " ".join(argv) and "--user_pass" not in argv
    assert fakes.subprocess_envs[0]["RE_DR_EGERIA_CHILD_TOKEN"] == "dans-token"
    assert {u for _c, u in fakes.built} <= {"dan"}
    assert "dans-token" not in json.dumps(_rows(reg), default=str)


def test_the_heal_passes_the_service_account_explicitly(tmp_path, monkeypatch):
    from resource_explorer import bootstrap

    fakes = Fakes(monkeypatch)
    monkeypatch.setenv("EGERIA_USER", "someone-else")
    from resource_explorer.config import get_config

    egeria = get_config().egeria
    monkeypatch.setattr(egeria, "user_id", "svc-account")
    monkeypatch.setattr(egeria, "user_password", "svc-pass")
    doc = tmp_path / "d.md"
    doc.write_text("x")
    ok, _detail = bootstrap._run_dr_egeria(doc)
    assert ok
    argv = fakes.argvs[0]
    assert argv[argv.index("--userid") + 1] == "svc-account" and "svc-pass" not in " ".join(argv)
    assert fakes.subprocess_envs[0]["RE_DR_EGERIA_CHILD_PASSWORD"] == "svc-pass"


def test_the_heal_refuses_when_the_service_account_is_not_set(tmp_path, monkeypatch):
    from resource_explorer import bootstrap
    from resource_explorer.config import get_config

    fakes = Fakes(monkeypatch)
    monkeypatch.setattr(get_config().egeria, "user_id", "")
    doc = tmp_path / "d.md"
    doc.write_text("x")
    ok, detail = bootstrap._run_dr_egeria(doc)
    assert not ok and "EGERIA_USER_ID not set" in detail and fakes.argvs == []


def test_the_startup_check_logs_the_heal_identity_and_its_source_never_the_password(monkeypatch, caplog):
    import logging

    from resource_explorer import bootstrap
    from resource_explorer.config import get_config

    monkeypatch.setattr(get_config().egeria, "user_id", "svc-account")
    monkeypatch.setattr(get_config().egeria, "user_password", "svc-pass")
    with caplog.at_level(logging.INFO):
        out = bootstrap.log_heal_identity_at_startup()
    assert out["heal_user"] == "svc-account" and out["set"] is True and "EGERIA_USER_ID" in out["source"]
    assert "svc-account" in caplog.text and "svc-pass" not in caplog.text
    assert "DIFFER" not in caplog.text and "differs" not in out


def test_the_startup_check_warns_loudly_when_the_heal_has_no_service_account(monkeypatch, caplog):
    import logging

    from resource_explorer import bootstrap
    from resource_explorer.config import get_config

    monkeypatch.setattr(get_config().egeria, "user_id", "")
    with caplog.at_level(logging.WARNING):
        out = bootstrap.log_heal_identity_at_startup()
    assert out["set"] is False and out["heal_user"] == ""
    assert any(r.levelno == logging.WARNING and "NO service account" in r.getMessage() for r in caplog.records)


def test_a_heal_with_no_service_account_records_it_on_the_batch_status(tmp_path, monkeypatch):
    from resource_explorer import bootstrap
    from resource_explorer.config import get_config

    fakes = Fakes(monkeypatch)
    monkeypatch.setattr(get_config().egeria, "user_id", "")
    batch = bootstrap.Batch(batch_id="b-nosvc", path=tmp_path, display_name="b", canary={}, files=["d.md"])
    monkeypatch.setattr(bootstrap, "discover_batches", lambda docs_dir: [batch])
    monkeypatch.setattr(bootstrap, "canary_present", lambda b: False)
    monkeypatch.setattr(bootstrap, "heal_batch", lambda b: pytest.fail("no service account: nothing is healed"))
    monkeypatch.setattr(bootstrap, "_status", {})
    out = bootstrap._check_and_heal_locked(tmp_path, False)
    assert out["batches"]["b-nosvc"]["action"] == "failed"
    st = bootstrap._status["b-nosvc"]
    assert st.last_heal_result.startswith("no service account") and st.consecutive_failures == 0
    assert fakes.argvs == []


def test_the_runner_has_its_own_daemon_reason():
    from resource_explorer.egeria_clients import DaemonReason

    assert DaemonReason.DR_EGERIA.value == "dr_egeria"


# ── CLI and route ───────────────────────────────────────────────────────────

def test_the_cli_and_the_route_call_the_same_function(root, monkeypatch):
    from fastapi.testclient import TestClient
    from typer.testing import CliRunner

    from resource_explorer import dr_egeria_runner as runner
    from resource_explorer.cli.main import app as cli_app
    from resource_explorer.web.app import app as web_app

    seen = []

    def fake_run(path, *, dry_run=False, force=False, requested_by="", registry=None, identity=None, announce=None):
        seen.append((str(path), dry_run, force))
        return {"status": "planned", "document": str(path), "dry_run": dry_run, "commands": [],
                "not_safe_to_repeat": [], "errors": [], "summary": "", "valid": True}

    monkeypatch.setattr(runner, "run_document", fake_run)
    res = CliRunner().invoke(cli_app, ["dr-egeria", "run", "engine-host/decl.md", "--dry-run"])
    assert res.exit_code == 0, res.output

    _as_user(monkeypatch, "dan")
    r = TestClient(web_app).post("/api/bootstrap/dr-egeria/run",
                                 json={"path": "engine-host/decl.md", "dry_run": True})
    assert r.status_code == 200, r.text
    assert seen == [("engine-host/decl.md", True, False), ("engine-host/decl.md", True, False)]


def test_the_route_is_admin_only(root, monkeypatch):
    from fastapi.testclient import TestClient

    from resource_explorer import dr_egeria_runner as runner
    from resource_explorer.web.app import app as web_app

    monkeypatch.setattr(runner, "run_document", lambda *a, **k: pytest.fail("a refused caller runs nothing"))
    _as_user(monkeypatch, "peter")
    client = TestClient(web_app)
    for body in ({"path": "engine-host/decl.md"}, {"path": "engine-host/decl.md", "dry_run": True}):
        assert client.post("/api/bootstrap/dr-egeria/run", json=body).status_code == 403
    _as_user(monkeypatch, None)
    assert client.post("/api/bootstrap/dr-egeria/run", json={"path": "x.md"}).status_code == 403


def test_the_route_maps_refusals(root, reg, monkeypatch):
    from fastapi.testclient import TestClient

    from resource_explorer import dr_egeria_runner as runner
    from resource_explorer.web.app import app as web_app

    _as_user(monkeypatch, "dan")
    real = runner.run_document
    monkeypatch.setattr(runner, "run_document", lambda path, **k: real(path, **{**k, "registry": reg}))
    Fakes(monkeypatch)
    client = TestClient(web_app)
    assert client.post("/api/bootstrap/dr-egeria/run", json={"path": "/etc/passwd"}).status_code == 400
    assert client.post("/api/bootstrap/dr-egeria/run", json={"path": "plain/only.md"}).status_code == 200
    again = client.post("/api/bootstrap/dr-egeria/run", json={"path": "plain/only.md"})
    assert again.status_code == 409 and "--force" not in again.json()["detail"]
    assert client.post("/api/bootstrap/dr-egeria/run",
                       json={"path": "plain/only.md", "force": True}).status_code == 200


def test_the_cli_refuses_a_repeat_with_exit_1(root, reg, monkeypatch):
    from typer.testing import CliRunner

    from resource_explorer import dr_egeria_runner as runner
    from resource_explorer.cli.main import app as cli_app

    real = runner.run_document
    monkeypatch.setattr(runner, "run_document", lambda path, **k: real(path, **{**k, "registry": reg}))
    Fakes(monkeypatch)
    first = CliRunner().invoke(cli_app, ["dr-egeria", "run", "plain/only.md", "--as", "tester"],
                               env={"RE_DR_EGERIA_PASSWORD": IDENT_PASSWORD})
    assert first.exit_code == 0, first.output
    assert "not safe to repeat" in first.output and "Link Action to Action Executor" in first.output
    second = CliRunner().invoke(cli_app, ["dr-egeria", "run", "plain/only.md", "--as", "tester"],
                                env={"RE_DR_EGERIA_PASSWORD": IDENT_PASSWORD})
    assert second.exit_code == 1 and "--force" in second.output



# ── review round 1 ──────────────────────────────────────────────────────────

def _route(monkeypatch, reg=None):
    from fastapi.testclient import TestClient

    from resource_explorer import dr_egeria_runner as runner
    from resource_explorer.web.app import app as web_app

    if reg is not None:
        real = runner.run_document
        monkeypatch.setattr(runner, "run_document", lambda path, **k: real(path, **{**k, "registry": reg}))
    return TestClient(web_app)


def test_the_route_is_refused_when_no_admin_list_is_configured(root, reg, monkeypatch):
    """Not the fail-open require_admin: an empty FEEDBACK_ADMIN_USERS refuses everyone, an admin TOKEN too."""
    _as_user(monkeypatch, "dan", admins=(), admin_token="t0ken")
    client = _route(monkeypatch, reg)
    res = client.post("/api/bootstrap/dr-egeria/run", json={"path": "plain/only.md", "dry_run": True},
                      headers={"X-Admin-Token": "t0ken", "X-Admin-User": "dan"})
    assert res.status_code == 403 and "no admin configured" in res.json()["detail"]


def test_an_admin_name_in_the_header_does_not_make_a_non_admin_an_admin(root, reg, monkeypatch):
    _as_user(monkeypatch, "peter", admins=("dan",))
    client = _route(monkeypatch, reg)
    res = client.post("/api/bootstrap/dr-egeria/run", json={"path": "plain/only.md", "dry_run": True},
                      headers={"X-Admin-User": "dan"})
    assert res.status_code == 403


def test_an_admin_jwt_is_let_through(root, reg, monkeypatch):
    _as_user(monkeypatch, "dan", admins=("dan",))
    client = _route(monkeypatch, reg)
    res = client.post("/api/bootstrap/dr-egeria/run", json={"path": "plain/only.md", "dry_run": True})
    assert res.status_code == 200 and res.json()["status"] == "planned"


def test_a_nul_byte_in_the_path_is_400(root, reg, monkeypatch):
    from resource_explorer import dr_egeria_runner as runner

    with pytest.raises(runner.DocumentRefused):
        runner.run_document("plain/only\x00.md", dry_run=True, identity=IDENT, registry=reg)
    _as_user(monkeypatch, "dan")
    assert _route(monkeypatch, reg).post("/api/bootstrap/dr-egeria/run",
                                         json={"path": "plain/only\x00.md"}).status_code == 400


def test_a_bootstrap_batch_document_is_refused_without_force(root, reg, monkeypatch):
    """The heal may already have applied it (heals before this change left no row), so a batch document is
    refused unless forced, whatever this runner's own rows say."""
    from resource_explorer import dr_egeria_runner as runner

    batch = root / "questions"
    batch.mkdir()
    (batch / "q.md").write_text(DOC)
    (batch / "_batch.json").write_text(json.dumps({"display_name": "Q", "idempotent": False,
                                                   "canary": {"qualified_name": "x"}, "files": ["q.md"],
                                                   "post_heal": {"script": "scripts/fix.py"}}))
    fakes = Fakes(monkeypatch)
    planned = runner.run_document("questions/q.md", dry_run=True, identity=IDENT, registry=reg)
    assert "bootstrap batch questions" in planned["batch_note"]
    with pytest.raises(runner.RepeatRefused, match="bootstrap batch questions"):
        runner.run_document("questions/q.md", identity=IDENT, registry=reg)
    assert fakes.calls == [] and _rows(reg) == []
    assert runner.run_document("questions/q.md", force=True, identity=IDENT, registry=reg)["status"] == "ok"


def test_an_interrupt_finalises_the_row_and_is_re_raised(root, reg, monkeypatch):
    from resource_explorer import bootstrap
    from resource_explorer import dr_egeria_runner as runner

    Fakes(monkeypatch)

    def interrupted(doc, *, identity=None, env=None):
        raise KeyboardInterrupt

    monkeypatch.setattr(bootstrap, "run_dr_egeria_document", interrupted)
    with pytest.raises(KeyboardInterrupt):
        runner.run_document("plain/only.md", identity=IDENT, registry=reg)
    row = _rows(reg)[0]
    assert row["status"] == "interrupted"
    assert "_runner" not in json.loads(row["detail"])


def _running_row(reg, sha256: str, runner_identity: dict) -> str:
    from resource_explorer.registry import ActivityEntry

    entry_id = "prior-" + runner_identity.get("started_at", "x")[:4]
    reg.write_activity(ActivityEntry(
        id=entry_id, ts="2026-10-14T00:00:00+00:00", operation="dr_egeria", intent="enrichment",
        entity_type="dr_egeria_document", entity_slug="plain/only.md", status="running", summary="running",
        detail=json.dumps({"document": "plain/only.md", "sha256": sha256, "dry_run": False,
                           "_runner": runner_identity})))
    return entry_id


def _dead_pid() -> int:
    import subprocess as sp
    import sys

    proc = sp.Popen([sys.executable, "-c", "pass"])
    proc.wait()
    return proc.pid


def test_a_running_row_whose_process_is_gone_is_reconciled_and_does_not_block_a_forced_run(root, reg, monkeypatch):
    from resource_explorer import dr_egeria_runner as runner

    Fakes(monkeypatch)
    sha = runner.plan_document("plain/only.md").sha256
    prior = _running_row(reg, sha, {"pid": _dead_pid(), "started_at": "Tue Oct 14 00:00:00 2026"})
    with pytest.raises(runner.RepeatRefused):            # reconciled to interrupted: it may have written, so applied
        runner.run_document("plain/only.md", identity=IDENT, registry=reg)
    assert reg.get_activity(prior)["status"] == "interrupted"
    assert runner.run_document("plain/only.md", force=True, identity=IDENT, registry=reg)["status"] == "ok"


def test_a_running_row_with_a_live_owner_refuses_even_with_force(root, reg, monkeypatch):
    from resource_explorer import dr_egeria_runner as runner
    from resource_explorer.run_reconciler import process_identity

    fakes = Fakes(monkeypatch)
    sha = runner.plan_document("plain/only.md").sha256
    prior = _running_row(reg, sha, process_identity())
    with pytest.raises(runner.RunRefused, match="in progress"):
        runner.run_document("plain/only.md", force=True, identity=IDENT, registry=reg)
    assert reg.get_activity(prior)["status"] == "running" and not fakes.writes()


@pytest.mark.parametrize("heading", ["## delete glossary term", "## Clear Valid Metadata Map Name",
                                     "## Cancel Engine Action", "## Unlink Element From Scope",
                                     "## Initiate Engine Action"])
def test_only_allow_listed_verbs_run(root, reg, monkeypatch, heading):
    from resource_explorer import dr_egeria_runner as runner

    (root / "plain" / "x.md").write_text(DOC + "\n" + heading + "\n### Qualified Name\nx\n\n___\n")
    fakes = Fakes(monkeypatch)
    out = runner.run_document("plain/x.md", dry_run=True, identity=IDENT, registry=reg)
    assert out["valid"] is False and any("not run by RE" in e for e in out["errors"]), out["errors"]
    with pytest.raises(runner.DocumentRefused):
        runner.run_document("plain/x.md", identity=IDENT, registry=reg)
    assert fakes.calls == []


def test_a_link_with_no_guid_back_is_ok_only_when_the_read_back_finds_it(root, reg, monkeypatch):
    from resource_explorer import dr_egeria_runner as runner

    fakes = Fakes(monkeypatch)
    fakes.link_answer = None
    fakes.link_visible_after_create = True
    runner.run_document("engine-host/decl.md", identity=IDENT, registry=reg)
    link = _rows(reg)[0]["items"][2]
    assert (link["status"], link["outcome"], link["guid"]) == ("ok", "created", LINK_GUID)


def test_a_link_with_no_guid_back_and_no_read_back_is_unknown(root, reg, monkeypatch):
    from resource_explorer import dr_egeria_runner as runner

    fakes = Fakes(monkeypatch)
    fakes.link_answer = None
    runner.run_document("engine-host/decl.md", identity=IDENT, registry=reg)
    row = _rows(reg)[0]
    assert row["items"][2]["status"] == "unknown" and row["status"] == "error"


def test_an_empty_later_page_ends_the_link_read(root, reg, monkeypatch):
    from resource_explorer import dr_egeria_runner as runner

    fakes = Fakes(monkeypatch)
    fakes.related_pages = [{"elementList": [OTHER_LINK]}, []]
    runner.run_document("engine-host/decl.md", identity=IDENT, registry=reg)
    fakes.maker.link_supported_governance_service.assert_called_once()
    assert _rows(reg)[0]["items"][2]["status"] == "ok"


def test_a_steps_file_that_points_outside_the_docs_directory_is_refused(root, reg, monkeypatch):
    from resource_explorer import dr_egeria_runner as runner

    outside = root.parent / "evil.pyegeria-steps.json"
    outside.write_text(json.dumps(STEPS))
    (root / "plain" / "only.pyegeria-steps.json").symlink_to(outside)
    fakes = Fakes(monkeypatch)
    with pytest.raises(runner.DocumentRefused, match="steps"):
        runner.run_document("plain/only.md", dry_run=True, identity=IDENT, registry=reg)
    assert fakes.calls == []


def test_a_refused_repeat_says_how_the_earlier_run_ended(root, reg, monkeypatch):
    from resource_explorer import dr_egeria_runner as runner

    fakes = Fakes(monkeypatch)
    fakes.maker.create_asset.side_effect = RuntimeError("Egeria said no")
    runner.run_document("engine-host/decl.md", identity=IDENT, registry=reg)
    with pytest.raises(runner.RepeatRefused) as refused:
        runner.run_document("engine-host/decl.md", identity=IDENT, registry=reg)
    words = str(refused.value)
    assert "already applied" not in words
    assert "ended error" in words and "1 ok, 1 error, 3 not run" in words


# ── the summary reader on the survey-definition documents ────────────────────
# Fixtures are the real `dr_egeria` rendering (md_processing.process_md_file_v2, the installed pyegeria) of
# survey-definitions/repo-survey-definition-analysis.md, captured with a stub client and no Egeria call: lookups find
# nothing except RE's engine (declared before this document runs), every write answers one stub GUID. Re-captured
# 2026-10-15 for engine host case 3 at widths 1158 (process) and 200 (folded); re-captured 2026-10-22 the same way
# after the questions-CSV edit added one Link Element To Scope (42 commands).

_FIXTURES = Path(__file__).parent / "fixtures"
_ANALYSIS_DOC = "survey-definitions/repo-survey-definition-analysis.md"


def _statuses(items):
    return [(i["command"], i["status"]) for i in items]


@pytest.mark.parametrize("fixture", ["dr_egeria_summary_repo_analysis_process.txt",
                                     "dr_egeria_summary_repo_analysis_folded.txt"])
def test_every_command_of_the_analysis_definition_gets_a_real_status(fixture):
    from resource_explorer import dr_egeria_runner as runner

    plan = runner.plan_document(_ANALYSIS_DOC)
    items = runner.dr_egeria_results(plan.dr_egeria, (_FIXTURES / fixture).read_text(), "ok")
    # Re-captured for engine host case 3 (the document now carries one "Link Action to Action Executor" per step), with a
    # stub client that knows the engine its executor links name: every one of the 42 commands gets a real status.
    ran = items
    assert len(ran) == 42 and "unknown" not in {i["status"] for i in ran}
    assert {i["status"] for i in ran if i["command"] == "Link Action to Action Executor"} == {"ok"}
    assert {s for c, s in _statuses(ran) if c != "Link Element To Scope"} == {"ok"}


def test_a_full_size_document_lines_up_row_for_row_even_when_rich_folds_every_cell():
    from resource_explorer import dr_egeria_runner as runner

    plan = runner.plan_document("survey-definitions/repo-survey-definition-full.md")
    rows = [(c.command, "SUCCESS", "n", "GovActionProcessStep::" + "x" * 90, "Executed " + c.command)
            for c in plan.dr_egeria]
    for width in ("80", "220", "4000"):
        os.environ["EGERIA_WIDTH"] = width
        try:
            out = _summary(rows)
        finally:
            del os.environ["EGERIA_WIDTH"]
        items = runner.dr_egeria_results(plan.dr_egeria, out, "ok")
        # every command of the document (119 before engine host case 3 added an executor link per step: 159; 161 since the questions-CSV edit
        # split the code-symbols question from the size one and added the "how much has changed" scope link)
        assert len(items) == len(plan.dr_egeria) == 161 and {i["status"] for i in items} == {"ok"}, width


def test_a_deferred_row_is_unknown_with_its_reason_not_ok():
    from resource_explorer import dr_egeria_runner as runner

    cmds = [runner.PlannedCommand("dr_egeria", "Link Next Process Step", "", False, ""),
            runner.PlannedCommand("dr_egeria", "Link First Process Step", "", False, "")]
    out = _summary([("Link Next Process Step", "DEFERRED", "", "", "Deferred: waiting on x"),
                    ("Link First Process Step", "SUCCESS", "", "", "Executed")])
    items = runner.dr_egeria_results(cmds, out, "ok")
    assert [i["status"] for i in items] == ["unknown", "ok"]
    assert "'DEFERRED'" in items[0]["error"] and "waiting on x" in items[0]["error"]


def test_a_pipe_inside_the_message_does_not_void_the_table():
    from resource_explorer import dr_egeria_runner as runner

    cmds = [runner.PlannedCommand("dr_egeria", "Link Next Process Step", "", False, "")]
    out = _summary([("Link Next Process Step", "SUCCESS", "", "", "Executed a │ b")])
    items = runner.dr_egeria_results(cmds, out, "ok")
    assert items[0]["status"] == "ok" and "a │ b" in items[0]["message"]


def test_a_missing_row_costs_only_its_own_command():
    from resource_explorer import dr_egeria_runner as runner

    cmds = [runner.PlannedCommand("dr_egeria", n, "", True, "") for n in ("Create A", "Create B", "Create C")]
    out = _summary([("Create A", "SUCCESS", "", "", "m"), ("Create C", "FAILURE", "", "", "boom")])
    items = runner.dr_egeria_results(cmds, out, "ok")
    assert _statuses(items) == [("Create A", "ok"), ("Create B", "unknown"), ("Create C", "error")]
    assert "no row for 'Create B'" in items[1]["error"]


# ── identity: CLI, route, config ────────────────────────────────────────────

def test_the_cli_refuses_without_as(root, reg, monkeypatch):
    from typer.testing import CliRunner

    from resource_explorer import dr_egeria_runner as runner
    from resource_explorer.cli.main import app as cli_app

    monkeypatch.setenv("EGERIA_USER", "resourceexplorernpa")
    monkeypatch.setattr(runner, "run_document", lambda *a, **k: pytest.fail("nothing runs without an identity"))
    res = CliRunner().invoke(cli_app, ["dr-egeria", "run", "plain/only.md"], env={"RE_DR_EGERIA_PASSWORD": "x"})
    assert res.exit_code == 1 and "no Egeria identity set; pass --as or sign in" in res.output


@pytest.mark.parametrize("how", ["getpass", "env"])
def test_the_cli_takes_the_password_from_getpass_or_the_one_invocation_env(root, reg, monkeypatch, how):
    from typer.testing import CliRunner

    from resource_explorer import dr_egeria_runner as runner
    from resource_explorer.cli import dr_egeria_commands
    from resource_explorer.cli.main import app as cli_app

    real = runner.run_document
    monkeypatch.setattr(runner, "run_document", lambda path, **k: real(path, **{**k, "registry": reg}))
    fakes = Fakes(monkeypatch)
    monkeypatch.delenv("RE_DR_EGERIA_PASSWORD", raising=False)
    env = {}
    if how == "getpass":
        monkeypatch.setattr(dr_egeria_commands.getpass, "getpass", lambda prompt="": IDENT_PASSWORD)
    else:
        env = {"RE_DR_EGERIA_PASSWORD": IDENT_PASSWORD}
        monkeypatch.setattr(dr_egeria_commands.getpass, "getpass",
                            lambda prompt="": pytest.fail("the env var is used when set"))
    res = CliRunner().invoke(cli_app, ["dr-egeria", "run", "plain/only.md", "--as", "tester"], env=env)
    assert res.exit_code == 0, res.output
    assert "applying as tester from --as" in res.output
    argv = fakes.argvs[0]
    assert argv[argv.index("--userid") + 1] == "tester" and "--user_pass" not in argv
    assert IDENT_PASSWORD not in " ".join(argv), "no secret on argv"
    assert fakes.subprocess_envs[0]["RE_DR_EGERIA_CHILD_PASSWORD"] == IDENT_PASSWORD
    assert "RE_DR_EGERIA_CHILD_TOKEN" not in fakes.subprocess_envs[0]
    assert IDENT_PASSWORD not in res.output
    assert IDENT_PASSWORD not in json.dumps(_rows(reg), default=str)


def test_the_route_applies_as_the_callers_own_session_not_the_daemon(root, reg, monkeypatch):
    _as_user(monkeypatch, "dan")
    client = _route(monkeypatch, reg)
    fakes = Fakes(monkeypatch)
    res = client.post("/api/bootstrap/dr-egeria/run", json={"path": "plain/only.md"})
    assert res.status_code == 200, res.text
    argv = fakes.argvs[0]
    assert argv[argv.index("--userid") + 1] == "dan" and "--user_pass" not in argv
    assert fakes.subprocess_envs[0]["RE_DR_EGERIA_CHILD_TOKEN"] == "caller-token"
    assert {u for _c, u in fakes.built} <= {"dan"}
    detail = json.loads(_rows(reg)[0]["detail"])
    assert detail["applying_as"] == "dan" and detail["identity_source"] == "admin session"
    assert res.json()["applying_as"] == "dan"


def test_the_route_has_no_signed_in_egeria_session_is_401_and_runs_nothing(root, reg, monkeypatch):
    from resource_explorer import egeria_clients

    _as_user(monkeypatch, "dan")
    monkeypatch.setattr(egeria_clients, "Caller", lambda: (_ for _ in ()).throw(egeria_clients.NoCallerIdentity()))
    client = _route(monkeypatch, reg)
    fakes = Fakes(monkeypatch)
    assert client.post("/api/bootstrap/dr-egeria/run", json={"path": "plain/only.md"}).status_code == 401
    assert fakes.argvs == []


def test_an_unset_service_account_refuses_and_never_falls_back_to_a_persona(monkeypatch):
    """Fail on purpose: EGERIA_USER_ID unset must refuse the daemon path, not become a person."""
    from resource_explorer import egeria_clients
    from resource_explorer.config import EgeriaConfig, get_config

    monkeypatch.delenv("EGERIA_USER_ID", raising=False)
    monkeypatch.delenv("EGERIA_USER_PASSWORD", raising=False)
    assert EgeriaConfig(_env_file=None).user_id == ""
    assert EgeriaConfig(_env_file=None).user_password == ""
    monkeypatch.setattr(get_config().egeria, "user_id", "")
    with pytest.raises(egeria_clients.NoServiceAccount, match="EGERIA_USER_ID not set"):
        egeria_clients.Daemon(egeria_clients.DaemonReason.OUTBOX)
    assert egeria_clients.daemon_identity_status() == {"user_id": None, "source": "unset"}
    monkeypatch.setattr(get_config().egeria, "user_id", "svc")
    monkeypatch.setattr(get_config().egeria, "user_password", "")
    with pytest.raises(egeria_clients.NoServiceAccount, match="EGERIA_USER_PASSWORD not set"):
        egeria_clients.Daemon(egeria_clients.DaemonReason.OUTBOX)


# ── fix round 1 (2026-10-21) ────────────────────────────────────────────────

def test_no_secret_is_on_argv_for_a_password_or_a_token_identity(tmp_path):
    from resource_explorer import bootstrap
    from resource_explorer.bootstrap import DrEgeriaIdentity

    for ident, env_key in ((DrEgeriaIdentity("tester", "--as", password="pw-S3CRET"), "RE_DR_EGERIA_CHILD_PASSWORD"),
                           (DrEgeriaIdentity("dan", "admin session", token="tok-S3CRET"), "RE_DR_EGERIA_CHILD_TOKEN")):
        argv, extra = bootstrap.dr_egeria_command("d.md", ident, server="vs", url="https://h:9443")
        assert "S3CRET" not in " ".join(argv) and "--user_pass" not in argv
        assert argv[1:3] == ["-m", "resource_explorer.dr_egeria_child"] and argv[-1] == "d.md"
        assert list(extra) == [env_key] and "S3CRET" in extra[env_key]


def test_the_cli_refuses_as_the_services_own_account(root, reg, monkeypatch):
    from typer.testing import CliRunner

    from resource_explorer import dr_egeria_runner as runner
    from resource_explorer.cli.main import app as cli_app
    from resource_explorer.config import get_config

    monkeypatch.setattr(get_config().egeria, "user_id", "svc-account")
    monkeypatch.setattr(get_config().egeria, "user_password", "svc-pass")
    monkeypatch.setattr(runner, "run_document", lambda *a, **k: pytest.fail("the daemon is never an --as identity"))
    res = CliRunner().invoke(cli_app, ["dr-egeria", "run", "plain/only.md", "--as", "SVC-Account"],
                             env={"RE_DR_EGERIA_PASSWORD": "x"})
    assert res.exit_code == 1 and "service account" in res.output


def test_the_route_refuses_when_the_signed_in_user_is_the_services_own_account(root, reg, monkeypatch):
    from resource_explorer.config import get_config

    monkeypatch.setattr(get_config().egeria, "user_id", "svc-account")
    monkeypatch.setattr(get_config().egeria, "user_password", "svc-pass")
    _as_user(monkeypatch, "svc-account", admins=("svc-account",))
    fakes = Fakes(monkeypatch)
    res = _route(monkeypatch, reg).post("/api/bootstrap/dr-egeria/run", json={"path": "plain/only.md"})
    assert res.status_code == 403 and "service account" in res.json()["detail"]
    assert fakes.argvs == []


def test_the_route_refuses_a_token_that_expires_inside_the_document_timeout(root, reg, monkeypatch):
    import time

    from resource_explorer import bootstrap, egeria_clients

    _as_user(monkeypatch, "dan")
    fakes = Fakes(monkeypatch)
    client = _route(monkeypatch, reg)
    monkeypatch.setattr(egeria_clients, "_token_expiry", lambda t: int(time.time()) + bootstrap.HEAL_TIMEOUT_SECONDS - 60)
    res = client.post("/api/bootstrap/dr-egeria/run", json={"path": "plain/only.md"})
    assert res.status_code == 401 and "sign in again" in res.json()["detail"] and fakes.argvs == []
    monkeypatch.setattr(egeria_clients, "_token_expiry", lambda t: int(time.time()) + bootstrap.HEAL_TIMEOUT_SECONDS + 600)
    assert client.post("/api/bootstrap/dr-egeria/run", json={"path": "plain/only.md"}).status_code == 200


def test_a_blank_signed_in_user_id_is_401_not_500(root, reg, monkeypatch):
    from resource_explorer import egeria_clients
    from resource_explorer.egeria_identity import EgeriaIdentity

    _as_user(monkeypatch, "dan")
    monkeypatch.setattr(egeria_clients, "Caller", lambda: EgeriaIdentity(user_id=" ", token="t",
                                                                          kind=egeria_clients.KIND_CALLER))
    fakes = Fakes(monkeypatch)
    res = _route(monkeypatch, reg).post("/api/bootstrap/dr-egeria/run", json={"path": "plain/only.md"})
    assert res.status_code == 401 and fakes.argvs == []


def test_a_named_person_is_recorded_as_themselves_not_as_the_service_account(monkeypatch):
    from resource_explorer import egeria_clients

    ident = egeria_clients.Named("tester", "pw", egeria_clients.DaemonReason.DR_EGERIA)
    assert ident.is_service_account is False
    egeria_clients._record(ident, "Dr.Egeria document run")
    entry = egeria_clients.last_egeria_identity("tester")
    assert entry is not None and "service account" not in entry["as"] and entry["as"].startswith("you")


@pytest.mark.parametrize("secret_env", ["RE_DR_EGERIA_CHILD_TOKEN", "RE_DR_EGERIA_CHILD_PASSWORD"])
def test_the_child_calls_the_real_process_function_with_arguments_it_accepts(tmp_path, monkeypatch, secret_env):
    """`create_autospec` of the REAL `process_md_file_v2`: a pyegeria rename or a changed signature fails here."""
    from unittest.mock import create_autospec

    import md_processing.dr_egeria as dr
    from resource_explorer import dr_egeria_child, egeria_clients

    spec = create_autospec(dr.process_md_file_v2, spec_set=True)
    monkeypatch.setattr(dr, "process_md_file_v2", spec)
    sentinel = object()

    class _Clients:
        def of(self, cls):
            return sentinel

    built = []
    monkeypatch.setattr(egeria_clients, "egeria_client", lambda identity, **k: built.append((identity, k)) or _Clients())
    for k in ("RE_DR_EGERIA_CHILD_TOKEN", "RE_DR_EGERIA_CHILD_PASSWORD"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv(secret_env, "s3cret")
    doc = tmp_path / "d.md"
    doc.write_text("x")
    rc = dr_egeria_child.main(["--userid", "dan", "--server", "vs", "--url", "https://h:9443", str(doc)])
    assert rc == 0 and spec.call_count == 1
    assert spec.call_args.kwargs["client"] is sentinel and spec.call_args.kwargs["input_file"] == str(doc)
    identity = built[0][0]
    assert identity.user_id == "dan"
    assert (identity.token == "s3cret") if secret_env.endswith("TOKEN") else (identity.password == "s3cret")


def test_the_child_exits_2_without_exactly_one_secret(tmp_path, monkeypatch):
    from resource_explorer import dr_egeria_child

    args = ["--userid", "dan", "--server", "vs", "--url", "u", "d.md"]
    for k in ("RE_DR_EGERIA_CHILD_TOKEN", "RE_DR_EGERIA_CHILD_PASSWORD"):
        monkeypatch.delenv(k, raising=False)
    assert dr_egeria_child.main(args) == 2
    monkeypatch.setenv("RE_DR_EGERIA_CHILD_TOKEN", "a")
    monkeypatch.setenv("RE_DR_EGERIA_CHILD_PASSWORD", "b")
    assert dr_egeria_child.main(args) == 2
