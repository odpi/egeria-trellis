"""EA's elicitor uses functional status, not element status (EA_SPEC_AUTHORING_V2 + EA_SPEC_ELICITOR_FIXES).

The owner, 2026-11-13: "Make sure EA's elicitor uses functional status". Status words ("draft", "active",
"in development") are a `functional_status` condition on the grounded type's own property, found by walking
its supertypes, filtered on the server by a finder that takes it. A value the property doesn't take is
`unmet`, in words. No status word ever reaches Egeria as `limitResultsByStatus` (laz quiet run 31e359c5:
B02 a no-op, B05/B10/B11/B15/H05 400s).

Offline: a fake LLM, temp stores, and pyegeria's real client classes, executor and preview with only the
HTTP transport faked. Needs pyegeria with `pyegeria.authoring`.
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


@pytest.fixture
def fixes_on(monkeypatch):
    monkeypatch.setenv(v2.FLAG, "1")
    monkeypatch.setenv(v2.FIXES_FLAG, "1")
    monkeypatch.delenv(v2.STATUS_MERGE_FLAG, raising=False)


@pytest.fixture
def one_value_content(monkeypatch):
    """contentStatus on the property-value finder (one value per search): a pyegeria without the pager."""
    monkeypatch.setattr(v2, "content_status_list_find", lambda: False)


@pytest.fixture
def list_content(monkeypatch):
    """contentStatus on find_authored_elements with every value (TR-7 phase 2): a pyegeria with the pager."""
    monkeypatch.setattr(v2, "content_status_list_find", lambda: True)


@pytest.fixture
def merge_on(fixes_on, monkeypatch):
    """The per-value merge: off by default until a spec can run it (Architect's ruling 2026-11-14)."""
    monkeypatch.setenv(v2.STATUS_MERGE_FLAG, "1")


class FakeLLM:
    def __init__(self, *replies):
        self.replies = list(replies)
        self.prompts = []

    def generate(self, prompt, **kw):
        self.prompts.append(prompt)
        return self.replies.pop(0)


def _llm(monkeypatch, *replies):
    fake = FakeLLM(*[r if isinstance(r, str) else json.dumps(r) for r in replies])
    monkeypatch.setattr("advisor.agents.report_spec_elicitor_v2.get_planning_llm", lambda: fake)
    monkeypatch.setattr("advisor.agents.report_spec_elicitor.get_planning_llm", lambda: fake)
    return fake


def _elicit(monkeypatch, question, target, words, legacy=False):
    """Run the elicitor on `question` with an LLM that proposes `target` and the status `words`."""
    from advisor.agents.report_spec_elicitor import ReportSpecElicitor
    from advisor.report_draft import get_report_draft_manager
    key = v2.LEGACY_STATUS_PARAM if legacy else "functional_status"
    _llm(monkeypatch, {"title": "t", "heading": "H", "description": "d", "target_type": target,
                       "conditions": {key: words}, "columns": [{"name": "Name", "key": "display_name"}]})
    r = ReportSpecElicitor().start(question)
    return r, get_report_draft_manager().load(r["draft_id"])


def _sent_params(spec):
    return v2.build_format_set(spec).action.spec_params


# ── the read-back's proof cases ──────────────────────────────────────────────

@pytest.mark.parametrize("question, target, words, prop, values, finder, column", [
    ("Which glossary terms are still in draft status?", "GlossaryTerm", ["draft"], "contentStatus", ["DRAFT"],
     "ClassificationExplorer.find_elements_by_property_value", "content_status"),
    ("Which products are still in development?", "DigitalProduct", ["in development"], "deploymentStatus",
     ["UNDER_DEVELOPMENT"], "CollectionManager.find_digital_products", "deployment_status"),
    ("What projects are currently active?", "Project", ["active"], "projectStatus", ["Active"],
     "ClassificationExplorer.find_elements_by_property_value", "project_status"),
    ("Which processes failed?", "Process", ["FAILED"], "activityStatus", ["FAILED"],
     "AssetMaker.find_processes", "activity_status"),
    ("Which governance rules are still in draft?", "GovernanceRule", ["draft"], "contentStatus", ["DRAFT"],
     "ClassificationExplorer.find_elements_by_property_value", "content_status"),
])
@pytest.mark.parametrize("legacy", [False, True])
@pytest.mark.parametrize("list_mode", [False, True])
def test_status_words_become_the_types_own_status(fixes_on, stores, monkeypatch, question, target, words,
                                                  prop, values, finder, column, legacy, list_mode):
    monkeypatch.setattr(v2, "content_status_list_find", lambda: list_mode)
    if prop == "contentStatus" and list_mode:
        finder = "ClassificationExplorer.find_authored_elements"     # TR-7 phase 2: every value, one search
    r, spec = _elicit(monkeypatch, question, target, words, legacy=legacy)
    status = spec["conditions"]["functional_status"]
    assert (status["property"], status["values"]) == (prop, values)
    assert spec["action_function"] == finder and finder in r["response"]
    assert column in [c["key"] for c in spec["columns"]]          # the filter is visible as a column
    assert prop in r["response"] and not spec.get("unmet")
    params = _sent_params(spec)
    assert v2.LEGACY_STATUS_PARAM not in params and "limit_results_by_status" not in params


def test_a_value_the_types_status_does_not_take_is_unmet_in_words(fixes_on, stores, monkeypatch):
    r, spec = _elicit(monkeypatch, "Which processes are still in draft?", "Process", ["draft"])
    assert "functional_status" not in spec["conditions"]
    assert spec["unmet"].startswith("Processes have no 'draft' status; their statuses are activityStatus: ")
    assert "Not applied:** Processes have no 'draft' status" in r["response"]
    assert v2.LEGACY_STATUS_PARAM not in _sent_params(spec)       # never falls back to the element status


def test_a_type_with_no_status_of_its_own_is_unmet(fixes_on, stores, monkeypatch):
    r, spec = _elicit(monkeypatch, "Which teams are active?", "Team", ["active"])
    assert "functional_status" not in spec["conditions"]
    assert "Teams have no status of their own" in spec["unmet"]
    assert v2.LEGACY_STATUS_PARAM not in _sent_params(spec)


def test_draft_projects_is_projects_content_status(fixes_on, stores, monkeypatch, one_value_content):
    """The brief expected "draft projects" to be unmet. Egeria's type system says otherwise: Project's
    supertype is AuthoredReferenceable (OpenMetadataTypesArchive1_2.getProjectEntity), so it carries
    contentStatus as well as projectStatus, and DRAFT is a contentStatus value."""
    assert v2.is_a("Project", "AuthoredReferenceable")
    _, spec = _elicit(monkeypatch, "Which projects are still drafts?", "Project", ["draft"])
    status = spec["conditions"]["functional_status"]
    assert (status["property"], status["values"]) == ("contentStatus", ["DRAFT"])
    assert spec["action_function"] == "ClassificationExplorer.find_elements_by_property_value"


def test_content_status_filters_the_first_value_asked_and_says_what_was_left_out(fixes_on, stores, monkeypatch,
                                                                                 one_value_content):
    # B15 "approved and active": the saved spec runs one search, so chat previews just that (§5b-1)
    _, spec = _elicit(monkeypatch, "Which governance policies are approved and active?", "GovernancePolicy",
                      ["APPROVED", "ACTIVE"])
    status = spec["conditions"]["functional_status"]
    assert (status["property"], status["values"]) == ("contentStatus", ["APPROVED"])   # question order
    assert ("searched for APPROVED only; ACTIVE was not searched (one contentStatus value per search here)"
            in spec["unmet"])
    assert "merges" not in status["note"]


def test_content_status_keeps_every_value_with_the_merge_on(merge_on, stores, monkeypatch, one_value_content):
    # flag on: one exact property-value search per value, merged by answer()
    _, spec = _elicit(monkeypatch, "Which governance policies are active and approved?", "GovernancePolicy",
                      ["ACTIVE", "APPROVED"])
    status = spec["conditions"]["functional_status"]
    assert (status["property"], status["values"]) == ("contentStatus", ["ACTIVE", "APPROVED"])
    assert not spec.get("unmet") and "merges one contentStatus search per value" in status["note"]
    assert "the saved spec searches ACTIVE only" in status["note"]


def test_two_properties_one_report_the_rest_unmet(fixes_on, stores, monkeypatch):
    # B10: "in development or draft". DigitalProduct has both; one report filters one of them.
    r, spec = _elicit(monkeypatch, "Which products are still in development or draft?", "DigitalProduct",
                      ["in development", "draft"])
    status = spec["conditions"]["functional_status"]
    assert (status["property"], status["values"]) == ("deploymentStatus", ["UNDER_DEVELOPMENT"])
    assert "'draft' is a contentStatus value of DigitalProduct" in spec["unmet"]


def test_a_value_both_properties_take_goes_to_the_most_specific_and_the_note_says_so():
    status, unmet = v2.ground_functional_status("DigitalProduct", ["ACTIVE"])
    assert status["property"] == "deploymentStatus" and not unmet
    assert "ACTIVE is also a contentStatus value" in status["note"] and "most specific" in status["note"]
    status, _ = v2.ground_functional_status("Project", ["active"])
    assert (status["property"], status["values"]) == ("projectStatus", ["Active"])   # its valid-value case


@pytest.mark.parametrize("target", ["GlossaryTerm", "Project", "DigitalProduct", "Process", "GovernancePolicy",
                                    "GovernanceRule", "GovernanceMetric", "Team", "Host", "DataSet",
                                    "SolutionComponent", "Risk"])
@pytest.mark.parametrize("legacy", [False, True])
def test_no_spec_ever_carries_the_element_status_from_a_status_word(target, legacy):
    words = sorted({v for vals in v2.FUNCTIONAL_STATUS_VALUES.values() for v in vals} | {"COMPLETE", "SHINY"})
    key = v2.LEGACY_STATUS_PARAM if legacy else "functional_status"
    for word in words:
        cond, _, _ = v2.ground_conditions({key: [word]}, target)
        params = _sent_params({"title": "t", "target_type": target, "conditions": cond,
                               "columns": [{"name": "Name", "key": "display_name"}]})
        assert v2.LEGACY_STATUS_PARAM not in params and "limit_results_by_status" not in params, (target, word)


# ── the supertype walk ───────────────────────────────────────────────────────

@pytest.mark.parametrize("type_name, ancestor, want", [
    ("GlossaryTerm", "AuthoredReferenceable", True), ("DataSet", "DataAsset", True), ("CSVFile", "Asset", True),
    ("Host", "Infrastructure", True), ("DeployedSoftwareComponent", "Process", True),
    ("Process", "DataAsset", False), ("GlossaryTerm", "Asset", False), ("SoftwareCapability", "Infrastructure", False),
])
def test_is_a_walks_the_supertypes(type_name, ancestor, want):
    assert v2.is_a(type_name, ancestor) is want


@pytest.mark.parametrize("type_name, want", [
    ("DigitalProduct", ["deploymentStatus", "contentStatus"]), ("Project", ["projectStatus", "contentStatus"]),
    ("SolutionComponent", ["deploymentStatus", "contentStatus"]), ("GlossaryTerm", ["contentStatus"]),
    ("Process", ["activityStatus"]), ("Infrastructure", ["deploymentStatus"]), ("Team", []),
])
def test_status_properties_most_specific_first(type_name, want):
    assert v2.status_properties(type_name) == want


@pytest.mark.parametrize("type_name, prop, finder", [
    ("GlossaryTerm", "contentStatus", "ClassificationExplorer.find_elements_by_property_value"),
    ("DataSet", "contentStatus", "ClassificationExplorer.find_elements_by_property_value"),
    ("Host", "deploymentStatus", "AssetMaker.find_infrastructure"),
    ("DigitalProduct", "deploymentStatus", "CollectionManager.find_digital_products"),
    ("SolutionComponent", "deploymentStatus", "ClassificationExplorer.find_elements_by_property_value"),
    ("Process", "activityStatus", "AssetMaker.find_processes"),
    ("Project", "projectStatus", "ClassificationExplorer.find_elements_by_property_value"),
])
@pytest.mark.parametrize("list_mode", [False, True])
def test_each_property_has_a_server_side_finder_that_takes_it(monkeypatch, type_name, prop, finder, list_mode):
    monkeypatch.setattr(v2, "content_status_list_find", lambda: list_mode)
    if prop == "contentStatus" and list_mode:
        finder = "ClassificationExplorer.find_authored_elements"
    action = v2.status_find_action(type_name, prop, ["X"])
    assert action["function"] == finder
    param = {"contentStatus": "content_status_list", "deploymentStatus": "deployment_status_list",
             "activityStatus": "activity_status_list"}.get(prop)
    named = "property_value" if finder.endswith("property_value") else param
    assert v2.finder_takes(finder, named)
    assert action["spec_params"]["metadata_element_type"] == type_name


def test_a_one_value_finder_keeps_the_first_value_by_default(fixes_on):
    status, unmet = v2.ground_functional_status("Project", ["planned", "Active"])
    assert status["values"] == ["Planned"]
    assert unmet[0] == "searched for Planned only; Active was not searched (one projectStatus value per search here)"
    spec = {"title": "t", "target_type": "Project", "conditions": {"functional_status": status}}
    assert v2.per_value_specs(spec) == [spec]


def test_a_one_value_finder_keeps_every_value_for_a_per_value_run(merge_on):
    status, unmet = v2.ground_functional_status("Project", ["Active", "planned"])
    assert status["values"] == ["Active", "Planned"] and not unmet
    spec = {"title": "t", "target_type": "Project", "conditions": {"functional_status": status},
            "columns": [{"name": "Name", "key": "display_name"}]}
    assert [s["conditions"]["functional_status"]["values"] for s in v2.per_value_specs(spec)] == [["Active"],
                                                                                                   ["Planned"]]
    one, _ = v2.ground_functional_status("Process", ["failed", "completed"])   # a list finder: one run
    assert len(v2.per_value_specs({**spec, "target_type": "Process", "conditions": {"functional_status": one}})) == 1


def test_a_name_filter_cannot_ride_with_a_property_value_status():
    cond, _, unmet = v2.ground_conditions({"functional_status": ["active"], "search_string": "Coco"}, "Project")
    assert "search_string" not in cond and any("'Coco'" in u for u in unmet)
    cond, _, unmet = v2.ground_conditions({"functional_status": ["failed"], "search_string": "Coco"}, "Process")
    assert cond["search_string"] == "Coco" and not unmet          # find_processes takes both


# ── element status: only an explicit "deleted", only where the finder takes it ──────────────────────

def test_deleted_is_the_element_status_where_the_finder_takes_it():
    cond, _, unmet = v2.ground_conditions({"functional_status": ["failed"], "element_status": "INCLUDE_DELETED"},
                                          "Process")
    assert cond["element_status"] == ["ACTIVE", "DELETED"] and not unmet
    params = _sent_params({"title": "t", "target_type": "Process", "conditions": cond,
                           "columns": [{"name": "Name", "key": "display_name"}]})
    assert params[v2.LEGACY_STATUS_PARAM] == ["ACTIVE", "DELETED"] and params["activity_status_list"] == ["FAILED"]


def test_deleted_is_unmet_where_the_finder_cannot_list_it():
    cond, _, unmet = v2.ground_conditions({"functional_status": ["deleted"]}, "GlossaryTerm")
    assert cond == {} and unmet == ["deleted elements: this report's finder can't list them"]


# ── the LLM's menu ───────────────────────────────────────────────────────────

def test_the_llm_is_never_offered_the_element_status_filter():
    from advisor.agents import report_spec_elicitor_v2 as v2e
    for text in (v2e._condition_help(), v2e._condition_help("DigitalProduct"), v2e._condition_help("Team")):
        assert v2.LEGACY_STATUS_PARAM not in text and "functional_status" in text
    assert "deploymentStatus" in v2e._condition_help("DigitalProduct")
    assert "activityStatus" not in v2e._condition_help("DigitalProduct")
    assert "has no status of its own" in v2e._condition_help("Team")


def test_retarget_reads_the_status_again_for_the_new_type(fixes_on, stores, monkeypatch):
    from advisor.agents import report_spec_elicitor_v2 as v2e
    _, spec = _elicit(monkeypatch, "Which glossary terms are approved?", "GlossaryTerm", ["approved"])
    v2e.retarget(spec, "target type is Process")
    status = spec["conditions"]["functional_status"]
    assert (spec["target_type"], status["property"], status["values"]) == ("Process", "activityStatus", ["APPROVED"])
    assert spec["action_function"] == "AssetMaker.find_processes"
    assert "activity_status" in [c["key"] for c in spec["columns"]]


# ── offline end to end: pyegeria's executor, the request Egeria would get, the rows a user would see ──

def _bean(**props):
    return {"elementHeader": {"guid": "g-1", "type": {"typeName": "X"},
                              "origin": {"homeMetadataCollectionId": "mc1", "homeMetadataCollectionName": "mc"},
                              "versions": {"createdBy": "u", "createTime": "2026-11-07T00:00:00Z", "version": 1},
                              "status": "ACTIVE", "class": "ElementHeader"},
            "properties": {"qualifiedName": "X::one", "displayName": "One", **props}}


class _Resp:
    def __init__(self, elements):
        self.elements = elements

    def json(self):
        # Egeria leaves a null field out (the end of the read), so a null page has no "elements" at all
        return {} if self.elements is None else {"elements": self.elements}


def _page(body, elements):
    """What Egeria sends for one page, under the paging contract (pyegeria 6.2.42 pages a preview): the rows
    on the first page, then null (the end). An older pyegeria asks once and never sees the null."""
    return _Resp(list(elements) if not (body or {}).get("startFrom") else None)


def _searches(calls):
    """The finds a run made, one per search: the first page of each (no /api/about version read, and no
    follow-on page the pager asks for)."""
    return [(m, u, b) for m, u, b in calls if m == "POST" and isinstance(b, dict) and not b.get("startFrom")]


@pytest.fixture
def egeria_offline(monkeypatch):
    from pyegeria.core._server_client import ServerClient
    calls, beans = [], []

    async def fake_make_request(self, request_type, endpoint, payload=None, *args, **kwargs):
        body = json.loads(payload) if isinstance(payload, str) else payload
        calls.append((request_type, endpoint, body))
        return _page(body, beans)
    monkeypatch.setattr(ServerClient, "_async_make_request", fake_make_request)
    monkeypatch.setattr(ServerClient, "create_egeria_bearer_token", lambda self, *a, **k: "token")
    monkeypatch.setattr(ServerClient, "set_bearer_token", lambda self, token: None)
    monkeypatch.setattr(ServerClient, "check_connection", lambda self: "ok", raising=False)
    return calls, beans


class _Client:
    server_name, platform_url, user_id, user_pwd, token = "v", "https://x", "erin", None, "t"


@pytest.mark.parametrize("target, words, endpoint, body_key, body_value, bean_prop, cell", [
    ("GlossaryTerm", ["draft"], "/by-property-value-search", "propertyValue", "DRAFT",
     {"contentStatus": "DRAFT"}, ("content_status", "DRAFT")),
    ("DigitalProduct", ["in development"], "/digital-products/by-search-string", "deploymentStatusList",
     ["UNDER_DEVELOPMENT"], {"deploymentStatus": "UNDER_DEVELOPMENT"}, ("deployment_status", "UNDER_DEVELOPMENT")),
    ("Process", ["failed"], "/processes/find-by-search-string", "activityStatusList", ["FAILED"],
     {"activityStatus": "FAILED"}, ("activity_status", "FAILED")),
    ("Project", ["active"], "/by-property-value-search", "propertyValue", "Active",
     {"projectStatus": "Active"}, ("project_status", "Active")),
])
def test_the_request_filters_on_the_server_and_the_column_shows_it(egeria_offline, one_value_content, target,
                                                                    words, endpoint, body_key, body_value,
                                                                    bean_prop, cell):
    calls, beans = egeria_offline
    beans.append(_bean(**bean_prop))
    cond, _, unmet = v2.ground_conditions({"functional_status": words}, target)
    cols = v2.condition_columns(target, [{"name": "Name", "key": "display_name"}], cond)
    out = v2.answer({"title": "t", "target_type": target, "answers": {"Heading": "H"}, "conditions": cond,
                     "columns": cols}, _Client())
    assert out["ok"], out
    (_, url, body), = _searches(calls)
    assert url.endswith(endpoint), url
    assert body[body_key] == body_value
    assert "limitResultsByStatus" not in body
    assert body.get("metadataElementTypeName") == target
    if body_key == "propertyValue":
        assert body["propertyNames"] == [{"Project": "projectStatus", "GlossaryTerm": "contentStatus"}[target]]
    assert out["result"]["rows"][0][cell[0]] == cell[1]


def test_several_values_run_one_search_each_merged_by_guid_and_capped(merge_on, egeria_offline, monkeypatch,
                                                                       one_value_content):
    """Architect read-back 18add498: B15's first value (APPROVED) has no rows on laz; keeping only the first
    value answered with 0 rows although ACTIVE policies exist."""
    from pyegeria.core._server_client import ServerClient
    calls, _ = egeria_offline
    by_value = {"APPROVED": [], "ACTIVE": [_bean(contentStatus="ACTIVE") for _ in range(2)]}
    by_value["ACTIVE"][1]["elementHeader"] = {**by_value["ACTIVE"][1]["elementHeader"], "guid": "g-2"}
    by_value["DRAFT"] = [by_value["ACTIVE"][0], _bean(contentStatus="DRAFT")]   # g-1 again: dropped
    by_value["DRAFT"][1]["elementHeader"] = {**by_value["DRAFT"][1]["elementHeader"], "guid": "g-3"}

    async def per_value(self, request_type, endpoint, payload=None, *a, **k):
        body = json.loads(payload) if isinstance(payload, str) else payload
        calls.append((request_type, endpoint, body))
        if request_type != "POST":
            return _Resp([])                                      # /api/about (6.2.42's version read)
        return _page(body, by_value[body["propertyValue"]])
    monkeypatch.setattr(ServerClient, "_async_make_request", per_value)
    cond, _, _ = v2.ground_conditions({"functional_status": ["APPROVED", "ACTIVE", "DRAFT"]}, "GovernancePolicy")
    spec = {"title": "t", "target_type": "GovernancePolicy", "answers": {"Heading": "H"}, "conditions": cond,
            "columns": [{"name": "Name", "key": "display_name"}, {"name": "GUID", "key": "guid"},
                        {"name": "Status", "key": "content_status"}]}
    out = v2.answer(spec, _Client())
    assert out["ok"], out
    assert [b["propertyValue"] for _, _, b in _searches(calls)] == ["APPROVED", "ACTIVE", "DRAFT"]
    assert [r["guid"] for r in out["result"]["rows"]] == ["g-1", "g-2", "g-3"]
    assert [r["content_status"] for r in out["result"]["rows"]] == ["ACTIVE", "ACTIVE", "DRAFT"]
    assert any("merged: 3 rows shown" in n for n in out["notes"])
    # 6.2.41 never pages, so 3 rows of 25 is the whole answer. 6.2.42 pages under the paging contract, and
    # this finder's ends aren't confirmed, so the merge honestly says there may be more, never "all".
    unconfirmed = [n for n in out["notes"] if "didn't confirm" in n or "can't rule out" in n]
    assert out["capped"] is bool(unconfirmed)
    calls.clear()
    capped = v2.answer(spec, _Client(), max_rows=2)
    assert [r["guid"] for r in capped["result"]["rows"]] == ["g-1", "g-2"] and capped["capped"]
    assert "there may be more" in capped["markdown"]


def test_by_default_a_run_is_the_saved_specs_one_search(fixes_on, egeria_offline, one_value_content):
    calls, beans = egeria_offline
    beans.append(_bean(contentStatus="APPROVED"))
    cond, _, unmet = v2.ground_conditions({"functional_status": ["APPROVED", "ACTIVE"]}, "GovernancePolicy")
    spec = {"title": "t", "target_type": "GovernancePolicy", "answers": {"Heading": "H"}, "conditions": cond,
            "columns": [{"name": "Name", "key": "display_name"}]}
    out = v2.answer(spec, _Client())
    assert out["ok"] and [b["propertyValue"] for _, _, b in _searches(calls)] == ["APPROVED"]
    assert v2.build_format_set(spec).action.spec_params["property_value"] == "APPROVED"   # what is saved
    assert not any("merged" in n for n in out.get("notes") or [])


def test_saving_repeats_what_the_spec_leaves_out(fixes_on, stores, monkeypatch, one_value_content):
    from advisor.agents.report_spec_elicitor import ReportSpecElicitor
    r, spec = _elicit(monkeypatch, "Which governance policies are approved and active?", "GovernancePolicy",
                      ["APPROVED", "ACTIVE"])
    el = ReportSpecElicitor()
    r = el.process(r["draft_id"], "generate now")
    line = "⚠️ **Saved spec is narrower than your question. Not applied:** searched for APPROVED only"
    assert r["phase"] == "refine" and line in r["response"] and "ACTIVE was not searched" in r["response"]
    saved = el.save_and_exit(r["draft_id"])
    assert line in saved["response"]


def test_saving_a_spec_that_applies_everything_says_nothing_extra(fixes_on, stores, monkeypatch):
    from advisor.agents.report_spec_elicitor import ReportSpecElicitor
    r, _ = _elicit(monkeypatch, "Which glossary terms are draft?", "GlossaryTerm", ["draft"])
    el = ReportSpecElicitor()
    r = el.process(r["draft_id"], "generate now")
    assert "narrower" not in r["response"] and "narrower" not in el.save_and_exit(r["draft_id"])["response"]


def test_no_value_with_rows_says_so():
    prevs = [{"result": {"kind": "empty"}, "notes": []}, {"result": {"kind": "empty"}, "notes": []}]
    out = v2.merge_previews(prevs, ["APPROVED", "PROPOSED"], "contentStatus", 25)
    assert out["notes"][-1] == "one contentStatus search per value (APPROVED, PROPOSED): none had rows"


# ── TR-7 phase 2 (RULING-TR7-EA-PHASE2-STATUS-SEARCH-2026-11-15): contentStatus in one search, every value ──

def test_content_status_list_finder_is_feature_detected():
    import importlib
    try:
        paging = importlib.import_module("pyegeria.core.paging")
        want = hasattr(paging, "paging_for")
    except ImportError:
        want = False                     # 6.2.41: find_authored_elements exists, but nothing pages a run to its end
    from pyegeria.omvs.classification_explorer import ClassificationExplorer
    assert v2.content_status_list_find() is (want and hasattr(ClassificationExplorer, "find_authored_elements"))


def test_b15_keeps_every_value_in_one_saved_search(fixes_on, stores, monkeypatch, list_content):
    r, spec = _elicit(monkeypatch, "Which governance policies are approved and active?", "GovernancePolicy",
                      ["APPROVED", "ACTIVE"])
    status = spec["conditions"]["functional_status"]
    assert (status["property"], status["values"]) == ("contentStatus", ["APPROVED", "ACTIVE"])
    assert not spec.get("unmet") and "merges" not in status["note"]
    action = v2.build_format_set(spec).action
    assert action.function == "ClassificationExplorer.find_authored_elements"
    assert action.spec_params["content_status_list"] == ["APPROVED", "ACTIVE"]
    assert action.spec_params["metadata_element_type"] == "GovernancePolicy"
    assert v2.per_value_specs(spec) == [spec]                       # no client merge: the server ORs them
    from advisor.agents.report_spec_elicitor import ReportSpecElicitor
    r = ReportSpecElicitor().process(r["draft_id"], "generate now")
    assert "narrower" not in r["response"]


def _typed_egeria(monkeypatch):
    """Egeria holding an ACTIVE GovernancePolicy and an ACTIVE GlossaryTerm; a search scoped by type returns
    only that type, as Egeria does (metadataElementTypeName)."""
    from pyegeria.core._server_client import ServerClient
    held = [dict(_bean(contentStatus="ACTIVE"), elementHeader={**_bean()["elementHeader"], "guid": "g-pol",
                                                                "type": {"typeName": "GovernancePolicy"}}),
            dict(_bean(contentStatus="ACTIVE"), elementHeader={**_bean()["elementHeader"], "guid": "g-term",
                                                                "type": {"typeName": "GlossaryTerm"}})]
    held[0]["properties"] = {**held[0]["properties"], "displayName": "Retention policy"}
    held[1]["properties"] = {**held[1]["properties"], "displayName": "Customer (term)"}
    calls = []

    async def fake(self, request_type, endpoint, payload=None, *a, **k):
        body = json.loads(payload) if isinstance(payload, str) else payload
        calls.append((request_type, endpoint, body))
        if request_type != "POST":
            return _Resp([])
        t = (body or {}).get("metadataElementTypeName")
        want = set((body or {}).get("contentStatusList") or [])
        rows = [e for e in held if (not t or e["elementHeader"]["type"]["typeName"] == t)
                and (not want or e["properties"].get("contentStatus") in want)]
        return _page(body, rows)
    monkeypatch.setattr(ServerClient, "_async_make_request", fake)
    monkeypatch.setattr(ServerClient, "create_egeria_bearer_token", lambda self, *a, **k: "token")
    monkeypatch.setattr(ServerClient, "set_bearer_token", lambda self, token: None)
    return calls


def _policies_spec(values=("ACTIVE",)):
    cond, _, unmet = v2.ground_conditions({"functional_status": list(values)}, "GovernancePolicy")
    return {"title": "t", "target_type": "GovernancePolicy", "answers": {"Heading": "Policies"}, "conditions": cond,
            "columns": [{"name": "Name", "key": "display_name"}, {"name": "GUID", "key": "guid"},
                        {"name": "Status", "key": "content_status"}]}


def test_a_the_type_filter_is_honoured(list_content, monkeypatch):
    calls = _typed_egeria(monkeypatch)
    out = v2.answer(_policies_spec(("APPROVED", "ACTIVE")), _Client())
    assert out["ok"], out
    (_, url, body), = _searches(calls)
    assert url.endswith("/authored-elements/by-search-string")
    assert body["metadataElementTypeName"] == "GovernancePolicy"
    assert body["contentStatusList"] == ["APPROVED", "ACTIVE"]
    assert [r["guid"] for r in out["result"]["rows"]] == ["g-pol"]        # never the ACTIVE glossary term


@pytest.mark.parametrize("words", [["shiny"], ["shiny", "sparkly"]])
def test_b_an_invalid_status_value_is_refused_before_any_search(list_content, monkeypatch, words):
    calls = _typed_egeria(monkeypatch)
    spec = _policies_spec(words)
    assert v2.STATUS_REFUSED in spec["conditions"] and "functional_status" not in spec["conditions"]
    out = v2.answer(spec, _Client())
    assert out["ok"] is False and out["refused"] and out["markdown"].startswith("I didn't search:")
    assert "shiny" in out["markdown"] and "contentStatus" in out["markdown"]
    assert calls == []                                                     # nothing reached Egeria
    assert v2.STATUS_REFUSED not in v2.build_format_set(spec).action.spec_params


def test_b_a_valid_value_beside_an_invalid_one_searches_the_valid_one_and_says_so(list_content, monkeypatch):
    calls = _typed_egeria(monkeypatch)
    cond, _, unmet = v2.ground_conditions({"functional_status": ["active", "shiny"]}, "GovernancePolicy")
    assert cond["functional_status"]["values"] == ["ACTIVE"] and v2.STATUS_REFUSED not in cond
    assert any("'shiny'" in u for u in unmet)


def test_c_the_saved_spec_round_trips_to_the_same_search(list_content, monkeypatch):
    calls = _typed_egeria(monkeypatch)
    spec = _policies_spec(("APPROVED", "ACTIVE"))
    fs = v2.build_format_set(spec)
    md = v2.serialize(spec)                                          # save
    back = v2.format_set_from_document(md, v2.label_for(spec))      # load
    for one in (fs, back):
        assert one.action.function == "ClassificationExplorer.find_authored_elements"
        assert one.action.spec_params["content_status_list"] == ["APPROVED", "ACTIVE"]
        assert one.action.spec_params["metadata_element_type"] == "GovernancePolicy"
    from pyegeria.authoring import preview, SpecDraft                # run the loaded one
    out = preview(SpecDraft(label="tr7-roundtrip", format_set=back), client=_Client(), max_rows=25,
                  params={"start_from": 0, "search_string": "*"})
    (_, url, body), = _searches(calls)
    assert url.endswith("/authored-elements/by-search-string") and body["contentStatusList"] == ["APPROVED", "ACTIVE"]
    assert [r["guid"] for r in out["result"]["rows"]] == ["g-pol"]


@pytest.mark.parametrize("end, capped, want", [
    ("confirmed", False, "86 rows"),
    ("not_confirmed", False, "at least 86 rows"),
    ("first_page", False, "at least 86 rows"),
    (None, False, "at least 86 rows"),                               # an older pyegeria: no end at all
    ("confirmed", True, "at least 86 rows"),
])
def test_d_the_86_draft_terms_say_n_only_on_a_confirmed_end(end, capped, want):
    rows = [{"display_name": f"term {i}", "content_status": "DRAFT"} for i in range(86)]
    prev = {"result": {"kind": "table", "columns": ["display_name", "content_status"], "rows": rows},
            "capped": capped, "shown": 86, "notes": []}
    if end is not None:
        prev["end"] = end
    assert v2.count_words(prev) == want


def test_d_the_answer_leads_with_its_count(list_content, monkeypatch):
    def fake_preview(draft, *, client, max_rows, params=None, **kw):
        rows = [{"display_name": f"term {i}", "content_status": "DRAFT"} for i in range(3)]
        return {"result": {"kind": "table", "columns": ["display_name", "content_status"], "rows": rows},
                "capped": False, "shown": 3, "notes": [], "end": "not_confirmed"}
    monkeypatch.setattr("pyegeria.authoring.preview", fake_preview)
    cond, _, _ = v2.ground_conditions({"functional_status": ["draft"]}, "GlossaryTerm")
    out = v2.answer({"title": "t", "target_type": "GlossaryTerm", "answers": {"Heading": "H"}, "conditions": cond,
                     "columns": [{"name": "Name", "key": "display_name"}]}, _Client())
    assert out["notes"][0] == "at least 3 rows" and "*at least 3 rows" in out["markdown"]
