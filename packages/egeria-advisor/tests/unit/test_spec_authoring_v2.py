"""EA re-base prototype (EA_SPEC_AUTHORING_V2): report-spec authoring on pyegeria's authoring API.

Offline: a fake LLM, temp draft/document stores, no Egeria. Needs pyegeria with `pyegeria.authoring`
(phase 1, release/next 475eaec1 or later); skipped on an older pyegeria.
"""
from __future__ import annotations

import json

import pytest

pytest.importorskip("pyegeria.authoring")

from advisor import spec_authoring_v2 as v2  # noqa: E402


@pytest.fixture
def stores(tmp_path, monkeypatch):
    import advisor.report_draft as rd
    import advisor.report_spec_docs as rsd

    def _drafts():
        p = tmp_path / "drafts"
        p.mkdir(parents=True, exist_ok=True)
        return p
    monkeypatch.setattr(rd, "_drafts_path", _drafts)
    monkeypatch.setattr(rsd, "_load_paths", lambda: {k: tmp_path / k for k in ("inbox", "outbox", "trash", "versions")})
    return tmp_path


class FakeLLM:
    """Returns the queued replies in order; records the prompts."""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.prompts = []

    def generate(self, prompt, **kw):
        self.prompts.append(prompt)
        return self.replies.pop(0)


@pytest.fixture
def flag_on(monkeypatch):
    """The re-base alone."""
    monkeypatch.setenv(v2.FLAG, "1")
    monkeypatch.delenv(v2.FIXES_FLAG, raising=False)


@pytest.fixture
def fixes_on(monkeypatch):
    """The re-base plus the elicitor fixes."""
    monkeypatch.setenv(v2.FLAG, "1")
    monkeypatch.setenv(v2.FIXES_FLAG, "1")


def _llm(monkeypatch, *replies):
    fake = FakeLLM(*[r if isinstance(r, str) else json.dumps(r) for r in replies])
    monkeypatch.setattr("advisor.agents.report_spec_elicitor_v2.get_planning_llm", lambda: fake)
    monkeypatch.setattr("advisor.agents.report_spec_elicitor.get_planning_llm", lambda: fake)
    return fake


# ── grounding ────────────────────────────────────────────────────────────────

def test_flag_is_off_by_default(monkeypatch):
    monkeypatch.delenv(v2.FLAG, raising=False)
    assert not v2.enabled()


@pytest.mark.parametrize("name,want", [("Glossary Term", "GlossaryTerm"), ("Term", "GlossaryTerm"),
                                       ("governance metric", "GovernanceMetric"), ("Risk", "Risk"),
                                       ("DataProduct", "DigitalProduct"), ("NoSuchThing", None)])
def test_resolve_type(name, want):
    assert v2.resolve_type(name) == want


def test_find_method_comes_from_the_types_registered_spec_not_the_llm():
    a = v2.find_action("Risk")
    assert a["function"] == "GovernanceOfficer.find_governance_definitions"
    assert a["spec_params"] == {"metadata_element_type": "Risk"}
    assert a["from"] == "Risk-DrE-Basic"
    dp = v2.find_action("DigitalProduct")
    assert dp["function"] == "CollectionManager.find_collections"
    assert dp["spec_params"]["metadata_element_type"] == "DigitalProduct"


def test_a_type_with_no_find_spec_uses_the_generic_finder_scoped_by_type():
    a = v2.find_action("Asset")
    assert a["function"] == v2.GENERIC_FIND and a["from"] == "generic finder"
    assert a["spec_params"] == {"metadata_element_type": "Asset", "graph_query_depth": 0,
                                "property_names": ["displayName", "qualifiedName"]}
    assert {"page_size", "start_from"} <= set(a["optional_params"])


def test_ground_columns_keeps_attributes_maps_camel_case_and_drops_the_rest():
    cols, dropped = v2.ground_columns("DigitalProduct", [
        {"name": "Name", "key": "displayName"}, {"name": "Manager", "key": "productManager"},
        {"name": "Service Life", "key": "service_life"}, {"name": "Bogus", "key": "foo.bar"}])
    assert [c["key"] for c in cols] == ["display_name", "service_life"]
    assert any("productManager" in d for d in dropped) and any("foo.bar" in d for d in dropped)


def test_ground_columns_never_returns_none():
    cols, dropped = v2.ground_columns("Project", [{"name": "x", "key": "nope"}])
    assert cols and cols[0]["key"] == "display_name" and dropped


def test_conditions_are_checked_and_shown_as_columns():
    cond, notes, unmet = v2.ground_conditions({"functional_status": ["active", "SHINY"], "colour": "red",
                                               "output_sort_order": "last_update_recent"}, "Project")
    assert cond["functional_status"]["property"] == "projectStatus"
    assert cond["functional_status"]["values"] == ["Active"]
    assert cond["output_sort_order"] == "LAST_UPDATE_RECENT" and "limit_result_by_status" not in cond
    assert any("colour" in n for n in notes) and any("'shiny'" in u for u in unmet)
    cols = v2.condition_columns("Project", [{"name": "Name", "key": "display_name"}], cond)
    assert {"project_status", "update_time"} <= {c["key"] for c in cols}


def _spec(**kw):
    spec = {"title": "active projects", "draft_id": "draft_report_x", "target_type": "Project",
            "columns": [{"name": "Name", "key": "display_name"}, {"name": "Status", "key": "project_status"}],
            "answers": {"Heading": "Active Projects", "Description": "Projects now active"},
            "conditions": v2.ground_conditions({"functional_status": ["active"]}, "Project")[0]}
    spec.update(kw)
    return spec


def test_build_validate_serialize_and_read_back():
    spec = _spec()
    res = v2.validate(spec)
    assert res.ok, res.errors
    md = v2.serialize(spec)
    assert "## Create Report Spec" in md and "### Microflow" in md and "Create Column" not in md
    assert "### Parent ID" not in md
    fs = v2.format_set_from_document(md, "active-projects")
    assert fs.target_type == "Project"
    assert fs.action.function == "ClassificationExplorer.find_elements_by_property_value"
    assert fs.action.spec_params["property_value"] == "Active"
    assert fs.action.spec_params["property_names"] == ["projectStatus"]
    assert "limit_result_by_status" not in fs.action.spec_params
    assert [c.key for f in fs.formats for c in f.attributes] == ["display_name", "project_status"]


def test_validate_catches_an_ungrounded_column():
    res = v2.validate(_spec(columns=[{"name": "Bogus", "key": "no_such_attribute"}]))
    assert not res.ok and any("no_such_attribute" in e for e in res.errors)


def test_render_says_capped_and_empty():
    table = {"result": {"kind": "table", "columns": [{"key": "display_name", "label": "Name"}],
                        "rows": [{"display_name": "A|B"}]}, "notes": ["capped: 1 of 3 rows shown"]}
    md = v2.render_markdown(table)
    assert "| Name |" in md and "A\\|B" in md and "capped: 1 of 3" in md
    assert "No rows" in v2.render_markdown({"result": {"kind": "table", "columns": [], "rows": []},
                                            "notes": ["ran, and found nothing"]})


# ── the elicitor, flag on ─────────────────────────────────────────────────────

def test_ambiguous_request_gets_a_question_not_a_guess(fixes_on, stores, monkeypatch):
    from advisor.agents.report_spec_elicitor import ReportSpecElicitor
    _llm(monkeypatch, {"clarify": "Important by criticality, usage or governance classification?"})
    r = ReportSpecElicitor().start("show me the important stuff")
    assert r["phase"] == "clarify" and "criticality" in r["response"] and r["draft_id"] is None


def test_unknown_type_is_a_question(fixes_on, stores, monkeypatch):
    from advisor.agents.report_spec_elicitor import ReportSpecElicitor
    _llm(monkeypatch, {"title": "t", "heading": "h", "target_type": "Widget", "columns": []})
    r = ReportSpecElicitor().start("list the widgets")
    assert r["phase"] == "clarify" and "Widget" in r["response"]


def test_full_flow_grounds_generates_with_pyegeria_and_never_uses_ea_markdown(fixes_on, stores, monkeypatch):
    from advisor.agents.report_spec_elicitor import ReportSpecElicitor
    from advisor.report_draft import get_report_draft_manager
    from advisor.report_spec_docs import get_report_spec_doc_manager
    _llm(monkeypatch, {"title": "risks", "heading": "Risk Register", "description": "All risks",
                       "target_type": "Risk", "conditions": {},
                       "columns": [{"name": "Name", "key": "displayName"}, {"name": "Owner", "key": "riskOwner"}]})
    el = ReportSpecElicitor()
    monkeypatch.setattr(el, "_generate_report_spec_md", lambda spec: pytest.fail("EA's markdown generator used"))
    r = el.start("List the risks in the company risk register")
    assert r["phase"] == "confirm_action"
    assert "GovernanceOfficer.find_governance_definitions" in r["response"]
    assert "riskOwner" in r["response"]   # dropped, and the reply says so
    r = el.process(r["draft_id"], "continue_columns")
    assert r["phase"] == "elicit_columns"
    r = el.process(r["draft_id"], "generate now")
    assert r["phase"] == "refine" and r["validation"]["ok"]
    assert "Sign in" in r["response"]   # no client: no answer, and it says how to get one
    doc = get_report_spec_doc_manager().load(r["doc_id"])
    assert "### Microflow" in doc and "Create Column" not in doc
    draft = get_report_draft_manager().load(r["draft_id"])
    assert draft["v2"] and draft["format_set"]["target_type"] == "Risk"


def test_generate_answers_in_the_reply_when_signed_in(fixes_on, stores, monkeypatch):
    from advisor.agents import report_spec_elicitor_v2 as v2e
    from advisor.agents.report_spec_elicitor import ReportSpecElicitor
    _llm(monkeypatch, {"title": "projects", "heading": "Active Projects", "target_type": "Project",
                       "conditions": {"limit_result_by_status": ["ACTIVE"]},
                       "columns": [{"name": "Name", "key": "display_name"}]})
    monkeypatch.setattr("pyegeria.authoring.inprocess_runner",
                        lambda client: (lambda md, d: {"success": True, "commands_total": 1}))
    seen = {}

    def fake_preview(draft, *, client, max_rows, params=None, **kw):
        seen.update(client=client, max_rows=max_rows, fs=draft.format_set)
        return {"result": {"kind": "table", "columns": [{"key": "display_name", "label": "Name"},
                                                        {"key": "project_status", "label": "Status"}],
                           "rows": [{"display_name": "Campaign X", "project_status": "Active"}]},
                "capped": False, "shown": 1, "notes": []}
    monkeypatch.setattr("pyegeria.authoring.preview", fake_preview)
    client = object()
    el = ReportSpecElicitor()
    r = el.start("which projects are active", egeria_client=client)
    r = el.process(r["draft_id"], "generate now")
    assert seen["client"] is client and seen["max_rows"] == v2.ANSWER_ROWS
    # the LLM's old element-status key is read as a functional status: Project's own projectStatus
    assert seen["fs"].action.spec_params["property_value"] == "Active"
    assert "limit_result_by_status" not in seen["fs"].action.spec_params
    assert "project_status" in [c.key for f in seen["fs"].formats for c in f.attributes]   # shown
    assert "| Campaign X | Active |" in r["response"] and r["answer"]["ok"]
    assert r["validation"]["dr_egeria"]["success"]
    v2e._CLIENTS.clear()


def test_refine_edits_the_draft_and_regenerates(fixes_on, stores, monkeypatch):
    from advisor.agents.report_spec_elicitor import ReportSpecElicitor
    from advisor.report_spec_docs import get_report_spec_doc_manager
    _llm(monkeypatch,
         {"title": "projects", "heading": "Projects", "target_type": "Project",
          "columns": [{"name": "Name", "key": "display_name"}]},
         {"columns": [{"name": "Name", "key": "display_name"}, {"name": "Priority", "key": "priority"}],
          "conditions": {}})
    el = ReportSpecElicitor()
    r = el.start("list projects")
    r = el.process(r["draft_id"], "generate now")
    doc_id = r["doc_id"]
    r = el.process(r["draft_id"], "add the priority")
    assert r["doc_id"] == doc_id
    assert "priority" in get_report_spec_doc_manager().load(doc_id)


def test_execute_reads_a_v2_document_through_its_microflow(flag_on, stores, monkeypatch):
    from advisor.agents.report_spec_agent import ReportSpecAgent
    from advisor.report_spec_docs import get_report_spec_doc_manager
    doc_id = get_report_spec_doc_manager().create("active projects", v2.serialize(_spec()))
    monkeypatch.setattr("advisor.agents.report_spec_agent.parse_report_spec_markdown",
                        lambda md: pytest.fail("EA's parser used on a V2 document"))
    monkeypatch.setattr("advisor.agents.report_spec_agent.register_report_spec", lambda name, fs: None)
    r = ReportSpecAgent().execute(doc_id, dry_run=True)
    assert r["dry_run"] and "Project" in r["response"] and "find_elements_by_property_value" in r["response"]


def test_flag_off_keeps_the_old_elicitor(stores, monkeypatch):
    monkeypatch.delenv(v2.FLAG, raising=False)
    from advisor.agents.report_spec_elicitor import ReportSpecElicitor
    _llm(monkeypatch, {"title": "t", "heading": "H", "target_type": "Project",
                       "action_function": "ProjectManager.find_projects", "description": "d",
                       "columns": [{"name": "Name", "key": "display_name"}]})
    r = ReportSpecElicitor().start("list projects")
    assert r["phase"] == "confirm_action" and "Found with" not in r["response"]


# ── Stage 1b: publish documents come from pyegeria, unchanged ────────────────

def _fs(target="Project"):
    from pyegeria.view._output_format_models import ActionParameter, Column, Format, FormatSet
    return FormatSet(target_type=target, heading="H", description="A line\nand another",
                     aliases=["alias-one"],
                     formats=[Format(types=["TABLE", "DICT"], attributes=[Column(name="Name", key="display_name")])],
                     action=ActionParameter(function="ProjectManager.find_projects", required_params=["search_string"]))


@pytest.mark.parametrize("kw", [
    {}, {"also_portal": True}, {"held": {"held": True, "zones": []}},
    {"held": {"held": True, "zones": ["z1"]}},
])
def test_golden_publish_document_is_the_same_from_pyegeria(monkeypatch, kw):
    from advisor import egeria_spec_publish as pub
    monkeypatch.delenv(v2.FLAG, raising=False)
    ea = pub.build_publish_document("my-spec", _fs(), **kw)
    monkeypatch.setenv(v2.FLAG, "1")
    shared = pub.build_publish_document("my-spec", _fs(), **kw)
    assert shared["markdown"] == ea["markdown"]
    assert shared["digest"] == ea["digest"] and shared["steps"] == ea["steps"]


def test_golden_restricted_publish_document(monkeypatch):
    from advisor import egeria_spec_publish as pub
    monkeypatch.setenv(pub.RESTRICTED_PROOF_ENV, "READBACK-X")
    kw = dict(visibility="restricted", zones=["b-zone", "a-zone", "a-zone"])
    monkeypatch.delenv(v2.FLAG, raising=False)
    ea = pub.build_publish_document("my-spec", _fs(), **kw)
    monkeypatch.setenv(v2.FLAG, "1")
    assert pub.build_publish_document("my-spec", _fs(), **kw)["markdown"] == ea["markdown"]


@pytest.mark.parametrize("folders", [None, ["ReportSpecFolder::Consumer::Portal"],
                                     ["ReportSpecFolder::Consumer::EgeriaAdvisor", "ReportSpecFolder::Consumer::Portal"]])
def test_golden_withdraw_document(monkeypatch, folders):
    from advisor import egeria_spec_publish as pub
    monkeypatch.delenv(v2.FLAG, raising=False)
    ea = pub.build_withdraw_document("my-spec", folders=folders)
    monkeypatch.setenv(v2.FLAG, "1")
    shared = pub.build_withdraw_document("my-spec", folders=folders)
    assert shared["markdown"] == ea["markdown"] and shared["digest"] == ea["digest"]


@pytest.mark.parametrize("call,words", [
    (lambda pub: pub.build_publish_document("bad\nlabel", _fs()), "single line"),
    (lambda pub: pub.build_publish_document("x", _fs(), visibility="restricted", zones=["z"]), "not yet proven"),
    (lambda pub: pub.build_withdraw_document("x", folders=["Somewhere::Else"]), "consumer folders"),
])
def test_refusals_stay_eas_type_with_the_flag_on(monkeypatch, call, words):
    from advisor import egeria_spec_publish as pub
    monkeypatch.setenv(v2.FLAG, "1")
    monkeypatch.delenv(pub.RESTRICTED_PROOF_ENV, raising=False)
    with pytest.raises(pub.PublishRefused, match=words):
        call(pub)


# ── named types, near-miss keys, Asset ───────────────────────────────────────

@pytest.mark.parametrize("text,want", [
    ("Which teams have no leader?", ["Team"]),
    ("What data product catalogs exist?", ["DigitalProductCatalog"]),
    ("which glossaries are approved", ["Glossary"]),
    ("tell me about our stuff", []),
    ("give me a report on everything", []),        # "report" says what to build, not what it's about
    ("build me a dashboard for the board", []),
])
def test_types_named_in(fixes_on, text, want):
    assert v2.types_named_in(text)[:1] == want


def test_asset_is_offered_and_found_generically(fixes_on):
    assert "Asset" in v2.known_types()
    assert v2.find_action("Asset")["function"] == v2.GENERIC_FIND


def test_a_near_miss_key_is_read_as_the_nearest_attribute_and_said(fixes_on):
    cols, notes = v2.ground_columns("GovernanceMetric", [{"name": "Measurements", "key": "measurements"}])
    assert [c["key"] for c in cols] == ["measurement"]
    assert any("read as measurement" in n for n in notes)


def test_a_question_that_names_its_type_is_built_not_asked(fixes_on, stores, monkeypatch):
    from advisor.agents.report_spec_elicitor import ReportSpecElicitor
    fake = _llm(monkeypatch, {"clarify": "Teams, or something else?"},
                {"title": "teams", "heading": "Teams", "target_type": "Team",
                 "columns": [{"name": "Name", "key": "display_name"}]})
    r = ReportSpecElicitor().start("Which teams have no leader?")
    assert r["phase"] == "confirm_action" and "Team" in r["response"]
    assert "names Team elements" in fake.prompts[1]


def test_eas_parser_reads_a_v2_document_for_every_other_caller(flag_on):
    """web/reports.py and report_spec_docs.py still call parse_report_spec_markdown on inbox documents."""
    from advisor.report_spec_parser import parse_report_spec_markdown
    fs = parse_report_spec_markdown(v2.serialize(_spec()))
    assert fs.target_type == "Project" and fs.action.function == "ClassificationExplorer.find_elements_by_property_value"
    assert fs.action.spec_params["property_value"] == "Active"
    assert [c.key for f in fs.formats for c in f.attributes] == ["display_name", "project_status"]


# ── the split: re-base alone vs re-base + elicitor fixes ─────────────────────

def test_rebase_only_keeps_eas_own_prompt_and_grounds_what_it_returns(flag_on, stores, monkeypatch):
    from advisor.agents.report_spec_elicitor import ReportSpecElicitor, decompose_prompt
    fake = _llm(monkeypatch, {"title": "p", "heading": "P", "target_type": "Digital Product",
                              "action_function": "ProjectManager.find_projects",
                              "columns": [{"name": "Name", "key": "displayName"},
                                          {"name": "Svc", "key": "service_lifes"}]})
    r = ReportSpecElicitor().start("which products are in draft?")
    assert fake.prompts[0] == decompose_prompt("which products are in draft?", None)
    assert r["phase"] == "confirm_action"
    assert "CollectionManager.find_collections" in r["response"]   # not the LLM's find_projects
    assert "service_lifes" in r["response"]                          # dropped: no near-miss without fixes


def test_rebase_only_never_asks(flag_on, stores, monkeypatch):
    from advisor.agents.report_spec_elicitor import ReportSpecElicitor
    _llm(monkeypatch, {"title": "x", "heading": "X", "target_type": "MetadataOverview", "columns": []})
    r = ReportSpecElicitor().start("show me the important stuff")
    assert r["phase"] == "error" and "MetadataOverview" in r["response"]


def test_rebase_only_offers_no_conditions_and_no_near_miss(flag_on):
    assert not v2.fixes_enabled()
    cols, notes = v2.ground_columns("GovernanceMetric", [{"name": "M", "key": "measurements"}])
    assert all(c["key"] != "measurement" for c in cols)


def test_fixes_need_the_rebase(monkeypatch):
    monkeypatch.delenv(v2.FLAG, raising=False)
    monkeypatch.setenv(v2.FIXES_FLAG, "1")
    assert not v2.fixes_enabled()


def test_parser_dispatch_is_off_without_the_flag(monkeypatch):
    from advisor.report_spec_parser import parse_report_spec_markdown
    monkeypatch.setenv(v2.FLAG, "1")
    md = v2.serialize(_spec())
    monkeypatch.delenv(v2.FLAG, raising=False)
    monkeypatch.setattr(v2, "format_set_from_document",
                        lambda *a, **k: pytest.fail("V2 reader used with the flag off"))
    fs = parse_report_spec_markdown(md)   # EA's own parser, as before the prototype
    assert not [c for f in fs.formats or [] for c in f.attributes or []]


@pytest.mark.parametrize("fixes", [False, True])
def test_same_question_same_llm_reply_takes_a_different_path_with_and_without_the_fixes(
        stores, monkeypatch, fixes):
    """For the read-back: EA_SPEC_AUTHORING_V2 alone never reaches the clarify, conditions or
    build-when-named code; with EA_SPEC_ELICITOR_FIXES it does. Same question, same LLM reply."""
    from advisor.agents.report_spec_elicitor import ReportSpecElicitor, decompose_prompt
    monkeypatch.setenv(v2.FLAG, "1")
    if fixes:
        monkeypatch.setenv(v2.FIXES_FLAG, "1")
    else:
        monkeypatch.delenv(v2.FIXES_FLAG, raising=False)
    reply = {"clarify": "Which teams?", "title": "teams", "heading": "Teams", "target_type": "Team",
             "conditions": {"limit_result_by_status": ["ACTIVE"]},
             "columns": [{"name": "Name", "key": "display_name"}]}
    built = {k: v for k, v in reply.items() if k != "clarify"}
    fake = _llm(monkeypatch, reply, built)
    r = ReportSpecElicitor().start("Which teams are active?")
    prompts = "\n".join(fake.prompts)
    if not fixes:
        assert fake.prompts == [decompose_prompt("Which teams are active?", None)]   # EA's prompt, once
        assert "clarify" not in prompts and "limit_result_by_status" not in prompts
        assert r["phase"] == "confirm_action" and "Condition:** none" in r["response"]
    else:
        assert len(fake.prompts) == 2 and "names Team elements" in fake.prompts[1]  # build-when-named retry
        assert "functional_status" in prompts and "limit_result_by_status" not in prompts
        # Team has no status of its own: not applied, in words, and never sent as the element status
        assert r["phase"] == "confirm_action" and "Not applied:** Teams have no status" in r["response"]


# ── Live finding 2026-11-13: generic-find tables were blank; H08 lacked property_value ──────────────

_VIEW_BEAN = {
    "elementHeader": {"guid": "g-asset-1", "type": {"typeName": "CSVFile"},
                      "origin": {"homeMetadataCollectionId": "mc1", "homeMetadataCollectionName": "mc"},
                      "versions": {"createdBy": "u", "createTime": "2026-11-07T00:00:00Z", "version": 1},
                      "status": "ACTIVE", "class": "ElementHeader"},
    "properties": {"qualifiedName": "Asset::weekly-sales.csv", "displayName": "Weekly sales"},
}


@pytest.fixture
def egeria_offline(monkeypatch):
    """The real pyegeria client classes, executor and preview, with only the HTTP transport faked."""
    from pyegeria.core._server_client import ServerClient
    calls = _Calls()
    beans = [_VIEW_BEAN]

    class _Resp:
        def __init__(self, elements):
            self.elements = elements

        def json(self):
            # Egeria leaves a null field out (the end of the read), so a null page has no "elements" at all
            return {} if self.elements is None else {"elements": self.elements}

    async def fake_make_request(self, request_type, endpoint, payload=None, *args, **kwargs):
        calls.append((request_type, endpoint, payload))
        body = json.loads(payload) if isinstance(payload, str) else (payload or {})
        # the paging contract (pyegeria 6.2.42 pages a preview): rows from startFrom up to pageSize, then the
        # end (no "elements") once there are none left. One bean unless the test asks for more.
        start, size = int(body.get("startFrom") or 0), int(body.get("pageSize") or 100)
        page = beans[start:start + size]
        return _Resp(page if page else None)
    monkeypatch.setattr(ServerClient, "_async_make_request", fake_make_request)
    monkeypatch.setattr(ServerClient, "create_egeria_bearer_token", lambda self, *a, **k: "token")
    monkeypatch.setattr(ServerClient, "set_bearer_token", lambda self, token: None)
    monkeypatch.setattr(ServerClient, "check_connection", lambda self: "ok", raising=False)
    calls.beans = beans
    return calls


class _Calls(list):
    """The requests a run made; `.beans` are the elements the fake Egeria holds (add to it for more)."""


class _Client:
    server_name, platform_url, user_id, user_pwd, token = "v", "https://x", "erin", None, "t"


def _asset_spec(**extra):
    return {"title": "assets", "target_type": "Asset", "answers": {"Heading": "Assets"},
            "columns": [{"name": "Name", "key": "display_name"}, {"name": "GUID", "key": "guid"}], **extra}


def test_the_generic_finder_is_a_view_service_so_its_cells_carry_values(egeria_offline):
    out = v2.answer(_asset_spec(), _Client())
    assert out["ok"], out
    rows = out["result"]["rows"]
    assert rows == [{"display_name": "Weekly sales", "guid": "g-asset-1"}]
    assert "| Weekly sales | g-asset-1 |" in out["markdown"]
    assert not any(e.endswith("/metadata-elements/by-search-string") for _, e, _ in egeria_offline)


def test_a_run_always_passes_a_search_so_a_finder_without_a_default_runs(egeria_offline):
    # H08 (live): a property-value finder with no search failed "missing ... 'property_value'"
    out = v2.answer(_asset_spec(), _Client())
    assert out["ok"] and "property_value" not in (out.get("error") or "")


@pytest.mark.parametrize("spec, want", [
    ({}, "*"),
    ({"content_filters": {"search_string": "*"}}, "*"),
    ({"content_filters": {"search_string": "sales"}}, "sales"),
    ({"conditions": {"search_string": "Ninety"}, "content_filters": {"search_string": "*"}}, "Ninety"),
    ({"conditions": {"search_string": "  "}}, "*"),
])
def test_search_of(spec, want):
    assert v2.search_of(spec) == want


def test_the_old_generic_finder_returned_raw_elements_and_blank_cells(egeria_offline, monkeypatch):
    """Why the switch: MetadataExpert's search answers with OpenMetadataElements (elementGUID,
    elementProperties), and pyegeria's column mapping reads view beans, so every cell came back None,
    guid included: the live symptom on Asset, Process and Location (2026-11-13)."""
    from pyegeria.core._server_client import ServerClient
    raw = {"class": "OpenMetadataElement", "elementGUID": "g-raw-1", "type": {"typeName": "CSVFile"},
           "status": "ACTIVE", "versions": {"createdBy": "u", "version": 1},
           "origin": {"homeMetadataCollectionId": "m"},
           "elementProperties": {"class": "ElementProperties", "propertyValueMap": {"displayName": {
               "class": "PrimitiveTypePropertyValue", "primitiveTypeCategory": "OM_PRIMITIVE_TYPE_STRING",
               "primitiveValue": "Weekly sales"}}}}

    class _Raw:
        def json(self):
            return {"elements": [raw]}

    async def raw_request(self, *a, **k):
        return _Raw()
    monkeypatch.setattr(ServerClient, "_async_make_request", raw_request)
    monkeypatch.setattr(v2, "GENERIC_FIND", "MetadataExpert.find_metadata_elements_with_string")
    monkeypatch.setattr(v2, "GENERIC_FIND_PARAMS", {})
    out = v2.answer(_asset_spec(), _Client())
    assert out["result"]["rows"] == [{"display_name": None, "guid": None}]


def test_a_run_asks_for_only_the_rows_it_shows(egeria_offline):
    # Architect read-back 2c6e4667: preview's page_size was dropped as "not a parameter of this report",
    # so the finder fetched its default 100 (generic Asset ~21 s live)
    egeria_offline.beans[:] = [{**_VIEW_BEAN, "elementHeader": {**_VIEW_BEAN["elementHeader"], "guid": f"g-{i}"}}
                               for i in range(12)]          # more than max_rows
    out = v2.answer(_asset_spec(), _Client(), max_rows=7)
    bodies = [json.loads(b) if isinstance(b, str) else b for m, _, b in egeria_offline if m == "POST"]
    first = bodies[0]
    # 6.2.41 asks for exactly the rows shown. 6.2.42's preview pages at its fetch size (DEFAULT_FETCH_PAGE_SIZE)
    # up to max_rows; asking for only the rows shown comes back with pyegeria PY-19 (a first page no larger than
    # max_rows), not by working around pyegeria here (Architect, TR-6 A1 read-back).
    from pyegeria.view import format_set_executor as fse
    assert first.get("pageSize") in (7, getattr(fse, "DEFAULT_FETCH_PAGE_SIZE", 7))
    assert first.get("graphQueryDepth") == 0 and not first.get("startFrom")
    assert out["shown"] == 7 and len(out["result"]["rows"]) == 7 and out["capped"]


def test_registered_specs_keep_their_optional_params_and_gain_paging():
    fs = v2.build_format_set({"title": "risks", "target_type": "Risk",
                              "columns": [{"name": "Name", "key": "display_name"}]})
    assert {"page_size", "start_from"} <= set(fs.action.optional_params)
    assert not set(v2.CONDITION_PARAMS) & set(fs.action.optional_params)


# ---- TR-6: an ordinal text property is ranked, not sorted by its letters (C03) ---------------------------

#: C03's rows as the quiet live re-run returned them (31e359c5, results_rebased_fixes_live_quiet.json): Egeria's
#: PROPERTY_DESCENDING on importance, which is text, so Medium-High came first and Critical last.
C03_ROWS = [("Inconsistent Data Definitions Undermining Cross-Department Reporting", "Medium-High"),
            ("Terrorism-Related Incident at the Annual Conference", "Medium-High"),
            ("Failure to Discharge Martyn's Law Responsible Person Duties", "Medium-High"),
            ("Onboarding of a Fraudulent or Compromised Supplier", "High"),
            ("Loss of Undocumented Governance Knowledge on Staff Departure", "High"),
            ("Accidental Disclosure of Patient Data through Misconfigured Access", "High"),
            ("Insider Misuse of Sensitive Patient Data", "High"),
            ("Manufacturing Batch Records Failing GMP Traceability Requirements", "High"),
            ("Counterfeit or Substandard Materials Entering Manufacturing", "Critical"),
            ("Ransomware Disruption to Manufacturing Operations", "Critical"),
            ("Theft of Personalised Treatment Intellectual Property", "Critical"),
            ("Loss of Clinical Trial Data Integrity", "Critical")]
C03_COND = {"output_sort_order": "PROPERTY_DESCENDING", "order_property_name": "importance"}


def _table(rows, key="importance", capped=False):
    return {"result": {"kind": "table", "columns": [{"key": "display_name", "label": "Risk Name"},
                                                    {"key": key, "label": "Importance"}],
                       "rows": [{"display_name": n, key: v} for n, v in rows]},
            "capped": capped, "shown": len(rows), "notes": []}


def _order(prev):
    return [r["importance"] for r in prev["result"]["rows"]]


def test_c03_highest_importance_comes_first():
    out = v2.order_rows(_table(C03_ROWS), C03_COND)
    assert _order(out) == ["Critical"] * 4 + ["High"] * 5 + ["Medium-High"] * 3
    assert out["result"]["rows"][0]["display_name"] == "Counterfeit or Substandard Materials Entering Manufacturing"
    assert out["reordered"] == "rank"
    assert any("Medium-High < High < Critical" in n and "not alphabetically" in n for n in out["notes"])
    assert not any("may be among those not shown" in n for n in out["notes"])     # not capped


def test_ascending_puts_the_lowest_rank_first_and_keeps_egerias_order_on_a_tie():
    rows = [("a", "high"), ("b", "Low"), ("c", "MEDIUM"), ("d", "low"), ("e", "very high"), ("f", "medium_high")]
    out = v2.order_rows(_table(rows), {**C03_COND, "output_sort_order": "PROPERTY_ASCENDING"})
    assert [r["display_name"] for r in out["result"]["rows"]] == ["b", "d", "c", "f", "a", "e"]


def test_a_capped_run_says_higher_ranked_rows_may_be_missing():
    out = v2.order_rows(_table(C03_ROWS[:5], capped=True), C03_COND)
    assert _order(out) == ["High", "High", "Medium-High", "Medium-High", "Medium-High"]
    assert any("rows ranked higher may be among those not shown" in n for n in out["notes"])


def test_numbers_held_as_text_sort_as_numbers():
    out = v2.order_rows(_table([("a", "9"), ("b", "10"), ("c", "2.5")]), C03_COND)
    assert _order(out) == ["10", "9", "2.5"] and out["reordered"] == "numbers"


def test_text_off_the_scale_keeps_egerias_order_and_says_it_is_alphabetical():
    rows = [("a", "Amber"), ("b", "Red"), ("c", "Green")]
    out = v2.order_rows(_table(rows), C03_COND)
    assert _order(out) == ["Amber", "Red", "Green"] and "reordered" not in out
    assert any("sorted it alphabetically" in n for n in out["notes"])


def test_dates_need_no_note_and_rows_without_a_value_go_last():
    rows = [("a", "2026-11-01"), ("b", "2026-11-13T10:00:00Z")]
    out = v2.order_rows(_table(rows), C03_COND)
    assert out["notes"] == [] and _order(out) == ["2026-11-01", "2026-11-13T10:00:00Z"]
    out = v2.order_rows(_table([("a", ""), ("b", "Low"), ("c", "High")]), C03_COND)
    assert _order(out) == ["High", "Low", ""]


def test_no_property_sort_no_change():
    prev = _table(C03_ROWS)
    assert v2.order_rows(prev, {"output_sort_order": "LAST_UPDATE_RECENT"}) is prev
    assert v2.order_rows(prev, {}) is prev
    assert v2.order_rows({"result": {"kind": "text", "content": "x"}}, C03_COND)["result"]["kind"] == "text"


def test_answer_ranks_c03_in_the_reply(monkeypatch):
    def fake_preview(draft, *, client, max_rows, params=None, **kw):
        return _table(C03_ROWS)
    monkeypatch.setattr("pyegeria.authoring.preview", fake_preview)
    spec = _spec(target_type="Risk", columns=[{"name": "Risk Name", "key": "display_name"},
                                              {"name": "Importance", "key": "importance"}],
                 conditions=dict(C03_COND))
    monkeypatch.setattr(v2, "spec_draft", lambda one: object())
    out = v2.answer(spec, client=object())
    lines = [ln for ln in out["markdown"].splitlines() if ln.startswith("| ") and "Importance" not in ln]
    assert lines[0].endswith("| Critical |") and lines[-1].endswith("| Medium-High |")
    assert "not alphabetically" in out["markdown"]


def test_c03_in_the_real_recorded_shape():
    """The read-back asks for C03's REAL data shape: the quiet re-run's own run record (31e359c5,
    results_rebased_fixes_live_quiet.json: check.run) is what the elicitor's preview handed over. Its columns
    are bare keys and its rows are flat {key: text} dicts, and the 12 rows are the transcript's, in Egeria's
    order. pyegeria may also give a cell as a dict ({"value": ...}): ranked the same."""
    recorded = {"kind": "table", "shown": 12, "capped": False, "notes": [],
                "columns": ["display_name", "importance"],
                "rows": [{"display_name": n, "importance": v} for n, v in C03_ROWS]}
    prev = {"result": {"kind": "table", "columns": recorded["columns"], "rows": recorded["rows"]},
            "capped": recorded["capped"], "shown": recorded["shown"], "notes": recorded["notes"]}
    out = v2.order_rows(prev, C03_COND)
    assert [r["importance"] for r in out["result"]["rows"]] == ["Critical"] * 4 + ["High"] * 5 + ["Medium-High"] * 3
    md = v2.render_markdown(out)
    assert md.splitlines()[2].endswith("| Critical |")
    dict_cells = {**prev, "result": {**prev["result"], "rows": [
        {"display_name": {"value": n}, "importance": {"value": v}} for n, v in C03_ROWS]}}
    out = v2.order_rows(dict_cells, C03_COND)
    assert [r["importance"]["value"] for r in out["result"]["rows"]][:4] == ["Critical"] * 4


# ---- TR-6 A1: a capped run is re-read (bounded) before ranking ------------------------------------------

def _risk_spec():
    return _spec(target_type="Risk", columns=[{"name": "Risk Name", "key": "display_name"},
                                              {"name": "Importance", "key": "importance"}],
                 conditions=dict(C03_COND))


def _egeria(monkeypatch, rows, fail_big=False, end="confirmed"):
    """A fake preview that behaves like Egeria + pyegeria 6.2.42: rows in Egeria's alphabetical-descending
    order, up to max_rows, capped when there are more. `end` is the paging contract's end for a read that got
    everything ("confirmed", or e.g. "not_confirmed"; None: an older pyegeria). Records each read's max_rows."""
    seen = []
    ordered = sorted(rows, key=lambda nv: nv[1], reverse=True)            # Egeria: by the letters

    def fake_preview(draft, *, client, max_rows, params=None, **kw):
        seen.append(max_rows)
        if fail_big and max_rows > v2.ANSWER_ROWS:
            raise TimeoutError("slow")
        page = ordered[:max_rows]
        more = len(ordered) > max_rows
        out = {**_table(page), "capped": more, "shown": len(page)}
        if end is not None:
            out["end"] = "capped" if more else end
        return out
    monkeypatch.setattr("pyegeria.authoring.preview", fake_preview)
    monkeypatch.setattr(v2, "spec_draft", lambda one: object())
    return seen


#: 30 Medium-High, 5 High, 5 Critical: Egeria's first 25 (descending letters) are all Medium-High.
MANY = ([(f"mh {i}", "Medium-High") for i in range(30)] + [(f"h {i}", "High") for i in range(5)]
        + [(f"c {i}", "Critical") for i in range(5)])


def test_a_capped_highest_answer_is_re_read_so_its_top_rows_are_the_real_top(monkeypatch):
    seen = _egeria(monkeypatch, MANY)
    out = v2.answer(_risk_spec(), client=object())
    assert seen == [v2.ANSWER_ROWS, v2.RANK_READ_ROWS]
    got = [r["importance"] for r in out["result"]["rows"]]
    assert got[:10] == ["Critical"] * 5 + ["High"] * 5 and len(got) == v2.ANSWER_ROWS
    assert out["shown"] == v2.ANSWER_ROWS and out["capped"] is True            # more exist than shown...
    assert any("ranked all 40 rows" in n for n in out["notes"])                 # ...but the ranking saw them all
    assert not any("may be among those not shown" in n for n in out["notes"])
    assert out["markdown"].splitlines()[2].endswith("| Critical |")


def test_past_the_bound_the_note_stays(monkeypatch):
    monkeypatch.setattr(v2, "RANK_READ_ROWS", 30)                           # 40 rows, read 30
    _egeria(monkeypatch, MANY)
    out = v2.answer(_risk_spec(), client=object())
    assert any("ranked the first 30 rows" in n for n in out["notes"])
    assert any("rows ranked higher may be among those not shown" in n for n in out["notes"])


def test_a_failed_re_read_keeps_the_capped_answer_and_says_so(monkeypatch):
    _egeria(monkeypatch, MANY, fail_big=True)
    out = v2.answer(_risk_spec(), client=object())
    assert out["ok"] and any("couldn't read more rows to rank them (TimeoutError)" in n for n in out["notes"])
    assert any("may be among those not shown" in n for n in out["notes"])


def test_no_re_read_when_not_capped_or_not_re_ranked(monkeypatch):
    seen = _egeria(monkeypatch, C03_ROWS)                                    # 12 rows: not capped
    v2.answer(_risk_spec(), client=object())
    assert seen == [v2.ANSWER_ROWS]
    seen = _egeria(monkeypatch, [(f"x{i}", f"Colour {i}") for i in range(40)])   # capped, off the scale
    v2.answer(_risk_spec(), client=object())
    assert seen == [v2.ANSWER_ROWS]


@pytest.mark.parametrize("end", ["not_confirmed", None])
def test_not_capped_but_not_a_confirmed_end_is_the_first_n_not_all(monkeypatch, end):
    """Read-back R1 on 5b01e72a: a read that stopped below the bound without a confirmed end (a suspect null,
    an unconfirmed empty, or an older pyegeria with no `end`) may not be everything."""
    _egeria(monkeypatch, MANY, end=end)
    out = v2.answer(_risk_spec(), client=object())
    assert any("ranked the first 40 rows" in n for n in out["notes"])
    assert not any("ranked all" in n for n in out["notes"])
    assert any("rows ranked higher may be among those not shown" in n for n in out["notes"])
    assert out["result"]["rows"][0]["importance"] == "Critical"


# ---- TR-6 + TR-7 together: the count after a ranked re-read --------------------------------------------

def test_the_count_after_a_ranked_re_read_is_what_was_read(monkeypatch):
    _egeria(monkeypatch, MANY)                                    # 40 rows, a confirmed end
    out = v2.answer(_risk_spec(), client=object())
    assert out["notes"][0] == "40 rows" and len(out["result"]["rows"]) == v2.ANSWER_ROWS
    monkeypatch.setattr(v2, "RANK_READ_ROWS", 30)                # past the bound: never "all"
    _egeria(monkeypatch, MANY)
    assert v2.answer(_risk_spec(), client=object())["notes"][0] == "at least 30 rows"
    _egeria(monkeypatch, MANY, end="not_confirmed")
    monkeypatch.setattr(v2, "RANK_READ_ROWS", 500)
    assert v2.answer(_risk_spec(), client=object())["notes"][0] == "at least 40 rows"
