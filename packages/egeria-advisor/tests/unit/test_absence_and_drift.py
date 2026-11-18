"""EA Phase 3 A-P: perspectives from Egeria (P1), absence that says why (P2), pyegeria drift (P3).

BRIEFS-EA-TRACK-A-PHASE2-5-2026-11-11 §A-P. Every Egeria answer here is a live response captured
read-only from laz (tests/unit/fixtures/egeria_live, see its README), or pyegeria's own exception
classes; nothing is hand-shaped. Each absence site is tested on its failure path: the answer must
carry a sentence saying why, never a bare [] or None that reads as "none".
"""
from __future__ import annotations

import asyncio
import inspect
import json
from pathlib import Path

import pytest

from advisor import egeria_context as ec
from advisor import perspective_manager as pm

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "egeria_live"


def _fixture(name):
    return json.loads((FIXTURES / name).read_text())


PERSPECTIVES = _fixture("find_perspectives.json")
ZONES = _fixture("find_governance_zones.json")
ERIN = _fixture("get_actor_profiles_by_name_found.json")
REFUSALS = _fixture("refusals.json")
NO_ELEMENTS = REFUSALS["get_actor_profiles_by_name_missing"]          # "No elements found"
SIGNED_IN = {"user_id": "erinoverview", "password": "", "token": "a-bearer-token"}
CONN = {"view_server": "qs-view-server", "platform_url": "https://localhost:9443",
        "user_id": "erinoverview", "user_pwd": ""}


def _unauthorized():
    from pyegeria.core._exceptions import PyegeriaUnauthorizedException
    return PyegeriaUnauthorizedException(None, None, {"reason": REFUSALS["bad_token"]["text"],
                                                      "relatedHTTPCode": 401})


def _unreachable():
    from pyegeria.core._exceptions import PyegeriaConnectionException
    return PyegeriaConnectionException({"reason": REFUSALS["unreachable"]["text"]})


@pytest.fixture
def connected(monkeypatch):
    """EgeriaContext with a configured platform and a signed-in token; clients are the fakes the
    test installs on the pyegeria class each accessor imports."""
    def load(self):
        self._egeria_token = (self._egeria_credentials or {}).get("token", "")
        return dict(CONN)
    monkeypatch.setattr(ec.EgeriaContext, "_load_connection", load)
    import advisor.auth
    monkeypatch.setattr(advisor.auth, "apply_token", lambda client, token: None)
    return monkeypatch


def _install(monkeypatch, module: str, cls_name: str, **methods):
    """Replace pyegeria.<module>.<cls_name> with a fake whose methods are given."""
    import importlib
    mod = importlib.import_module(module)
    calls = []

    class Fake:
        def __init__(self, **conn):
            pass

    for name, fn in methods.items():
        def bound(self, *a, _fn=fn, _name=name, **k):
            calls.append((_name, a, k))
            return _fn(*a, **k)
        setattr(Fake, name, bound)
    monkeypatch.setattr(mod, cls_name, Fake)
    return calls


def _raise(exc):
    def f(*a, **k):
        raise exc
    return f


# ── P1 Perspectives ────────────────────────────────────────────────────────────

class TestPerspectives:
    def test_rows_read_properties_from_the_live_shape(self):
        rows = pm.rows_from_egeria(PERSPECTIVES)
        assert len(rows) == 12
        names = {r["egeria_name"] for r in rows}
        assert {"Financial", "Privacy", "Governance", "Steward"} <= names
        fin = next(r for r in rows if r["egeria_name"] == "Financial")
        assert fin["advisor_key"] == "financial"
        assert fin["description"].startswith("A focus on the cost")
        assert fin["guid"] == "009bc3f4-45cf-4f6f-894c-113cde6f35d6"
        assert fin["qualified_name"] == "Perspective::Financial"
        assert fin["source"] == pm.SOURCE_EGERIA

    def test_questions_are_the_scoped_glossary_terms_that_are_questions(self):
        rows = {r["egeria_name"]: r for r in pm.rows_from_egeria(PERSPECTIVES)}
        assert rows["Financial"]["questions"] and all(q.endswith("?") for q in rows["Financial"]["questions"])
        # Governance's first scoped elements are terms that are not questions.
        assert all(q.endswith("?") for q in rows["Governance"]["questions"])

    def test_not_signed_in_reads_nothing_and_says_so(self, connected):
        calls = _install(connected, "pyegeria.omvs.actor_manager", "ActorManager",
                         find_perspectives=lambda **k: PERSPECTIVES)
        for creds in (None, {"user_id": "erinoverview", "password": "", "token": ""}):
            rows, reason = pm.load_from_egeria(creds)
            assert rows == [] and reason.startswith("not signed in")
        assert calls == [], "never falls back to the service account"

    def test_loads_as_the_signed_in_user(self, connected):
        calls = _install(connected, "pyegeria.omvs.actor_manager", "ActorManager",
                         find_perspectives=lambda **k: PERSPECTIVES)
        rows, reason = pm.load_from_egeria(SIGNED_IN)
        assert reason is None and len(rows) == 12
        assert calls[0][0] == "find_perspectives"

    @pytest.mark.parametrize("exc, words", [
        (_unauthorized, "refused the sign-in"),
        (_unreachable, "Client failed to connect"),
    ])
    def test_a_failed_read_gives_its_reason(self, connected, exc, words):
        _install(connected, "pyegeria.omvs.actor_manager", "ActorManager", find_perspectives=_raise(exc()))
        rows, reason = pm.load_from_egeria(SIGNED_IN)
        assert rows == [] and words in reason

    def test_none_held_is_not_an_error(self, connected):
        _install(connected, "pyegeria.omvs.actor_manager", "ActorManager", find_perspectives=lambda **k: NO_ELEMENTS)
        rows, reason = pm.load_from_egeria(SIGNED_IN)
        assert rows == [] and reason == "Egeria holds no perspectives that erinoverview can see"


class TestPerspectiveStatus:
    @pytest.fixture(autouse=True)
    def fresh(self, monkeypatch):
        monkeypatch.setattr(pm, "_perspectives", [])
        monkeypatch.setattr(pm, "_loaded_at", None)
        monkeypatch.setattr(pm, "_attempted_at", None)
        monkeypatch.setattr(pm, "_status", {"source": pm.SOURCE_BUILT_IN, "message": "x"})

    def test_built_in_list_states_why(self, monkeypatch):
        monkeypatch.setattr(pm, "load_from_egeria", lambda c: ([], "Egeria refused the sign-in"))
        pm._do_refresh(SIGNED_IN)
        assert pm.status() == {"source": pm.SOURCE_BUILT_IN,
                               "message": "built-in list: Egeria not reached: Egeria refused the sign-in"}
        assert pm.get_all() and all(r["source"] == "csv" for r in pm.get_all())

    def test_from_egeria(self, monkeypatch):
        monkeypatch.setattr(pm, "load_from_egeria", lambda c: (pm.rows_from_egeria(PERSPECTIVES), None))
        pm._do_refresh(SIGNED_IN)
        assert pm.status() == {"source": pm.SOURCE_EGERIA, "message": "from Egeria"}

    def test_a_failed_refresh_keeps_the_egeria_list_and_says_it_failed(self, monkeypatch):
        monkeypatch.setattr(pm, "load_from_egeria", lambda c: (pm.rows_from_egeria(PERSPECTIVES), None))
        pm._do_refresh(SIGNED_IN)
        monkeypatch.setattr(pm, "load_from_egeria", lambda c: ([], "could not connect"))
        pm._do_refresh(SIGNED_IN)
        assert pm.status()["source"] == pm.SOURCE_EGERIA
        assert "the latest refresh failed: could not connect" in pm.status()["message"]
        assert len(pm.get_all()) == 12

    def test_anonymous_gets_the_built_in_list_with_not_signed_in(self, monkeypatch):
        started = []
        monkeypatch.setattr(pm.threading, "Thread", lambda **k: started.append(k) or _NoThread())
        rows = pm.get_all(None)
        assert rows and started == []
        assert pm.status()["message"].startswith("built-in list: Egeria not reached: not signed in")

    def test_a_signed_in_call_starts_a_refresh_and_a_failure_retries_soon(self, monkeypatch):
        started = []
        monkeypatch.setattr(pm.threading, "Thread", lambda **k: started.append(k) or _NoThread())
        pm.get_all(SIGNED_IN)
        assert len(started) == 1 and started[0]["args"] == (SIGNED_IN,)
        monkeypatch.setattr(pm, "load_from_egeria", lambda c: ([], "could not connect"))
        pm._do_refresh(SIGNED_IN)
        pm.get_all(SIGNED_IN)          # within RETRY_AFTER_SECONDS: no second thread
        assert len(started) == 1
        from datetime import timedelta
        monkeypatch.setattr(pm, "_attempted_at", pm._attempted_at - timedelta(seconds=pm.RETRY_AFTER_SECONDS + 1))
        pm.get_all(SIGNED_IN)          # not the 24h TTL: a failure is retried
        assert len(started) == 2

    def test_route_returns_source_and_message(self, monkeypatch):
        import advisor.auth
        from advisor.web import app as app_mod
        monkeypatch.setattr(advisor.auth, "get_egeria_credentials", lambda request: None)
        out = asyncio.run(app_mod.list_perspectives(request=None))
        assert out["source"] == pm.SOURCE_BUILT_IN
        assert out["message"].startswith("built-in list: Egeria not reached: not signed in")
        assert out["perspectives"]


class _NoThread:
    def start(self):
        pass


# ── P2 Zones ───────────────────────────────────────────────────────────────────

class TestZones:
    def test_reads_zones_only_and_pages_until_empty(self, connected):
        pages = [ZONES[:4], ZONES[4:], []]  # a short page is not the last page
        calls = _install(connected, "pyegeria.omvs.governance_officer", "GovernanceOfficer",
                         find_governance_definitions=lambda *a, **k: pages.pop(0))
        answer = ec.EgeriaContext(egeria_credentials=SIGNED_IN).governance_zones_answer()
        assert answer["state"] == ec.OK and answer["message"] is None
        assert answer["zones"] == sorted(z["properties"]["displayName"] for z in ZONES)
        assert len(calls) == 3
        kw = calls[0][2]
        assert kw["metadata_element_subtypes"] == ["GovernanceZone"] and kw["graph_query_depth"] == 0
        assert [c[2]["start_from"] for c in calls] == [0, 4, 6]

    def test_none_held(self, connected):
        _install(connected, "pyegeria.omvs.governance_officer", "GovernanceOfficer",
                 find_governance_definitions=lambda *a, **k: NO_ELEMENTS)
        answer = ec.EgeriaContext(egeria_credentials=SIGNED_IN).governance_zones_answer()
        assert answer == {"state": ec.NONE, "zones": [],
                          "message": "Egeria holds no governance zones that erinoverview can see."}

    def test_capped(self, connected):
        page = [dict(z, properties=dict(z["properties"], displayName=f"Zone {i}"))
                for i, z in enumerate((ZONES * 20)[:ec.ZONE_PAGE_SIZE])]
        _install(connected, "pyegeria.omvs.governance_officer", "GovernanceOfficer",
                 find_governance_definitions=lambda *a, **k: page)
        answer = ec.EgeriaContext(egeria_credentials=SIGNED_IN).governance_zones_answer()
        assert answer["state"] == ec.CAPPED
        assert answer["message"] == f"Showing the first {ec.ZONE_CAP} governance zones; Egeria may hold more."

    @pytest.mark.parametrize("exc, state, words", [
        (_unauthorized, ec.REFUSED, "refused the sign-in while reading governance zones (HTTP 401)"),
        (_unreachable, ec.UNREACHABLE, "Egeria not reached while reading governance zones: Client failed"),
    ])
    def test_a_failed_read_is_not_none(self, connected, exc, state, words):
        _install(connected, "pyegeria.omvs.governance_officer", "GovernanceOfficer",
                 find_governance_definitions=_raise(exc()))
        answer = ec.EgeriaContext(egeria_credentials=SIGNED_IN).governance_zones_answer()
        assert answer["state"] == state and words in answer["message"] and answer["zones"] == []

    def test_unreachable_when_the_client_cannot_be_made(self, connected):
        import pyegeria.omvs.governance_officer as mod

        class Down:
            def __init__(self, **conn):
                raise _unreachable()
        connected.setattr(mod, "GovernanceOfficer", Down)
        answer = ec.EgeriaContext(egeria_credentials=SIGNED_IN).governance_zones_answer()
        assert answer["state"] == ec.UNREACHABLE and "Client failed to connect" in answer["message"]

    def test_not_signed_in(self, connected):
        calls = _install(connected, "pyegeria.omvs.governance_officer", "GovernanceOfficer",
                         find_governance_definitions=lambda *a, **k: ZONES)
        answer = ec.EgeriaContext(egeria_credentials={"user_id": "erinoverview", "password": "", "token": ""}
                                  ).governance_zones_answer()
        assert answer["state"] == ec.NOT_SIGNED_IN and "Sign in to read from Egeria." in answer["message"]
        assert calls == []

    def test_not_configured(self, monkeypatch):
        import advisor.mcp_config
        monkeypatch.setattr(advisor.mcp_config, "get_pyegeria_platform_config",
                            lambda path=None: {"view_server": "", "platform_url": ""})
        answer = ec.EgeriaContext(egeria_credentials=SIGNED_IN).governance_zones_answer()
        assert answer == {"state": ec.NOT_CONFIGURED, "zones": [], "message": ec.NOT_CONFIGURED_MSG}

    def test_route_carries_state_and_message(self, monkeypatch):
        import advisor.auth
        from advisor.web import app as app_mod
        monkeypatch.setattr(advisor.auth, "require_egeria_user", lambda r: {"user_id": "erinoverview"})
        monkeypatch.setattr(advisor.auth, "get_egeria_credentials", lambda r: SIGNED_IN)
        monkeypatch.setattr(ec.EgeriaContext, "governance_zones_answer",
                            lambda self: {"state": ec.UNREACHABLE, "zones": [], "message": "Egeria not reached: x"})
        out = asyncio.run(app_mod.get_governance_zones(request=None))
        assert out == {"state": ec.UNREACHABLE, "zones": [], "count": 0, "message": "Egeria not reached: x"}


# ── P2 Template valid values ───────────────────────────────────────────────────

class TestTemplateValidValues:
    def _fields(self, monkeypatch, zones_answer, tech_answer):
        import advisor.auth
        from advisor.web import templates
        monkeypatch.setattr(advisor.auth, "require_egeria_user", lambda r: {"user_id": "erinoverview"})
        monkeypatch.setattr(advisor.auth, "get_egeria_credentials", lambda r: SIGNED_IN)
        monkeypatch.setattr(ec.EgeriaContext, "governance_zones_answer", lambda self: zones_answer)
        monkeypatch.setattr(ec.EgeriaContext, "technology_types_answer", lambda self: tech_answer)
        out = asyncio.run(templates.get_template_fields(None, "Create Solution Component", "basic"))
        return out, {f["name"]: f for f in out["fields"]}

    def test_unread_values_say_why(self, monkeypatch):
        down = {"state": ec.UNREACHABLE, "message": "Egeria not reached while reading governance zones: x"}
        out, f = self._fields(monkeypatch, {**down, "zones": []}, {**down, "technology_types": []})
        if not f:
            pytest.skip(out.get("message") or "no packaged templates in this pyegeria")
        zone = f["Zone Membership"]
        assert zone["valid_values"] == [] and zone["valid_values_state"] == ec.UNREACHABLE
        assert zone["valid_values_message"].startswith("Egeria not reached")
        tech = f["Planned Deployed Implementation Type"]
        assert tech["valid_values_state"] == ec.UNREACHABLE

    def test_read_values_fill_the_field(self, monkeypatch):
        names = [z["properties"]["displayName"] for z in ZONES]
        tech = [t["displayName"] for t in _fixture("get_all_technology_types_head.json")]
        out, f = self._fields(monkeypatch, {"state": ec.OK, "zones": names, "message": None},
                              {"state": ec.OK, "technology_types": tech, "message": None})
        if not f:
            pytest.skip(out.get("message") or "no packaged templates in this pyegeria")
        assert f["Zone Membership"]["valid_values"] == names
        assert f["Planned Deployed Implementation Type"]["valid_values"] == tech
        assert out["state"] == "ok"

    def test_no_matching_template_says_so(self, monkeypatch):
        import advisor.auth
        from advisor.web import templates
        monkeypatch.setattr(advisor.auth, "require_egeria_user", lambda r: {})
        monkeypatch.setattr(advisor.auth, "get_egeria_credentials", lambda r: None)
        out = asyncio.run(templates.get_template_fields(None, "Qqqqx Zzzzy", "basic"))
        assert out["fields"] == [] and out["state"] in ("not_found", "no_templates") and out["message"]


# ── P2 Agent enrichment ────────────────────────────────────────────────────────

class TestEnrichment:
    def test_found_is_an_exact_name_match(self, connected):
        _install(connected, "pyegeria.omvs.actor_manager", "ActorManager",
                 get_actor_profiles_by_name=lambda *a, **k: ERIN)
        answer = ec.EgeriaContext(egeria_credentials=SIGNED_IN).actor_answer("erin overview")
        assert answer["state"] == ec.FOUND
        assert answer["element"]["display_name"] == ERIN[0]["properties"]["displayName"]

    def test_a_near_name_is_not_found_and_named(self, connected):
        _install(connected, "pyegeria.omvs.actor_manager", "ActorManager",
                 get_actor_profiles_by_name=lambda *a, **k: ERIN)
        answer = ec.EgeriaContext(egeria_credentials=SIGNED_IN).actor_answer("Erin")
        assert answer["state"] == ec.NOT_FOUND and answer["element"] is None
        assert "Similar names: " + ERIN[0]["properties"]["displayName"] in answer["message"]

    def test_unreachable_is_not_not_found(self, connected):
        _install(connected, "pyegeria.omvs.actor_manager", "ActorManager",
                 get_actor_profiles_by_name=_raise(_unreachable()))
        _install(connected, "pyegeria.omvs.project_manager", "ProjectManager",
                 get_projects_by_name=_raise(_unreachable()))
        entities = {"roles": [{"person": "Erin Overview", "title": "Steward"}],
                    "objects": [{"type": "project", "name": "Clinical Trial"}]}
        ec.EgeriaContext(egeria_credentials=SIGNED_IN).enrich_entities(entities)
        role, obj = entities["roles"][0], entities["objects"][0]
        assert role["actor_found"] is None and "not reached" in role["actor_lookup_message"]
        assert obj["exists_in_egeria"] is None and "not reached" in obj["exists_check_message"]

        from advisor.agents.governance_plan_agent import unchecked_context_warnings
        warnings = unchecked_context_warnings(entities)
        assert warnings and all(w.startswith("Not checked against Egeria") for w in warnings)
        assert not any("not found" in w.lower() for w in warnings)
        assert "'Erin Overview'" in " ".join(warnings) and "'Clinical Trial'" in " ".join(warnings)

    def test_not_found_is_stated_only_when_egeria_answered(self, connected):
        _install(connected, "pyegeria.omvs.actor_manager", "ActorManager",
                 get_actor_profiles_by_name=lambda *a, **k: NO_ELEMENTS)
        entities = {"roles": [{"person": "No Such Person Zq", "title": "Owner"}], "objects": []}
        ec.EgeriaContext(egeria_credentials=SIGNED_IN).enrich_entities(entities)
        assert entities["roles"][0]["actor_found"] is False
        from advisor.agents.governance_plan_agent import unchecked_context_warnings
        assert unchecked_context_warnings(entities) == []


# ── /api/status ────────────────────────────────────────────────────────────────

class TestStatus:
    def test_reports_configuration_with_a_message(self):
        from advisor.web import app as app_mod
        out = asyncio.run(app_mod.system_status())
        assert out["state"] in ("ok", "none") and out["message"]
        assert "rag" not in out, "it never checked the RAG system"

    def test_unreadable_configuration_is_read_failed(self, monkeypatch):
        from advisor.web import app as app_mod
        monkeypatch.setattr(app_mod.json, "loads", _raise(ValueError("bad json")))
        out = asyncio.run(app_mod.system_status())
        assert out["state"] == "read_failed" and "bad json" in out["message"] and out["mcp_servers"] == []


# ── P3 Drift ───────────────────────────────────────────────────────────────────

class TestListingTools:
    def _pipeline(self, agent):
        from advisor.report_pipeline import ReportPipeline
        p = ReportPipeline.__new__(ReportPipeline)
        p._agent = agent
        return p

    def test_a_tool_on_an_absent_server_says_which_server(self):
        class Agent:
            clients = {"pyegeria": object()}
            def get_tool(self, name):
                return None
        out, note = self._pipeline(Agent())._try_listing_tool("Glossaries")
        assert out is None
        assert note == ("The egeria_list_glossaries listing needs the dr-egeria MCP server, "
                        "which is not connected (connected: pyegeria).")

    def test_sub_type_reports_never_fall_back(self):
        assert self._pipeline(None)._try_listing_tool("Glossary-Terms") == (None, None)

    def test_a_failed_call_says_so(self, monkeypatch):
        class Agent:
            clients = {"dr-egeria": object()}
            def get_tool(self, name):
                return object()
        p = self._pipeline(Agent())
        monkeypatch.setattr(p, "_call_tool", _raise(RuntimeError("boom")))
        out, note = p._try_listing_tool("collections")
        assert out is None and note == "The egeria_list_collections listing on the dr-egeria MCP server failed: boom"


class TestElementValue:
    """EA's reader agrees with pyegeria's private _get_element_value on live elements."""

    @pytest.mark.parametrize("key", ["display_name", "displayName", "qualified_name", "description",
                                     "elementHeader.guid", "properties.identifier", "identifier",
                                     "no_such_key", "guid"])
    def test_matches_pyegeria(self, key):
        from pyegeria.view.output_formatter import _get_element_value
        from advisor.report_pipeline import element_value
        for element in PERSPECTIVES[:3] + ZONES[:2]:
            assert element_value(element, key) == _get_element_value(element, key), key

    def test_no_private_pyegeria_reader_in_ea(self):
        root = Path(__file__).resolve().parents[2] / "advisor"
        offenders = [str(p.relative_to(root)) for p in root.rglob("*.py") if "_get_element_value" in p.read_text()]
        assert offenders == []


def test_prompt_example_uses_current_pyegeria_signature():
    from pyegeria import GlossaryManager
    import advisor.rag_system as rs
    src = Path(rs.__file__).read_text()
    assert "server_name=" not in src
    params = inspect.signature(GlossaryManager.__init__).parameters
    for kw in ("view_server=", "platform_url=", "user_id=", "user_pwd="):
        assert kw in src and kw[:-1] in params
    assert "display_name" in inspect.signature(GlossaryManager.create_glossary).parameters
