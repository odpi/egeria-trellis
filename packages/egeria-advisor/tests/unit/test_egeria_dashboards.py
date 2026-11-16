"""EA Dashboards, read (brief B5): list and show Egeria-held sheets as the signed-in user.

No live Egeria: an in-memory Egeria (pyegeria's own dashboard test fake, vendored as
_dashboard_fake_egeria.py) written to with pyegeria's Dr.Egeria sheet writer, and read through
pyegeria's REAL resolver. Each viewer has their own fake, chosen by the token their client carries,
so a cache shared across viewers would show up as one viewer seeing the other's answer.

The mixed-visibility sheet mirrors the live proof sheet "scratch-restricted-demo": a public sheet with
a Report zoned to peterprofile. erinoverview must see [report, restricted]; peterprofile [report, report].
"""
from __future__ import annotations

import importlib.util
import pathlib

import pytest

from advisor import egeria_dashboards as ed
from advisor import egeria_spec_sync as ess

_HERE = pathlib.Path(__file__).parent
_spec = importlib.util.spec_from_file_location("ea_dashboard_fake_egeria", _HERE / "_dashboard_fake_egeria.py")
fake_egeria = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fake_egeria)

ERIN_TOKEN = "token-erinoverview"
PETER_TOKEN = "token-peterprofile"
DEMO = "scratch-restricted-demo"


class ViewerEgeria(fake_egeria.FakeEgeria):
    """The fake as one viewer sees it: GUIDs in `hidden` are in a zone they cannot see. Relationship
    reads and listings drop them silently; an explicit read by GUID is refused with Egeria's 403-020."""

    def __init__(self, user_id: str):
        super().__init__()
        self.user_id = user_id
        self.hidden: set = set()
        self.reads = 0
        self.fail: Exception | None = None

    def _visible(self, items):
        if not isinstance(items, list):
            return items
        kept = [e for e in items if (e.get("elementHeader") or {}).get("guid") not in self.hidden]
        return kept or "No elements found"

    async def _async_get_element_by_guid_(self, guid, **kwargs):
        self.reads += 1
        if self.fail:
            raise self.fail
        if guid in self.hidden:
            raise RuntimeError(f"OPEN-METADATA-SECURITY-403-020 User {self.user_id} is not authorized to issue "
                               f"an operation read on Report element {guid}")
        return await super()._async_get_element_by_guid_(guid)

    async def _async_get_element_by_unique_name(self, name, property_name=None, **kwargs):
        self.reads += 1
        if self.fail:
            raise self.fail
        guid = self.by_qn(name)
        if guid in self.hidden:                 # a lookup by name drops a hidden element, as a search does
            return "No elements found"
        return await super()._async_get_element_by_unique_name(name, property_name, **kwargs)

    async def _async_get_related_elements(self, *a, **k):
        return self._visible(await super()._async_get_related_elements(*a, **k))

    async def _async_get_elements_by_property_value(self, *a, **k):
        self.reads += 1
        if self.fail:
            raise self.fail
        return self._visible(await super()._async_get_elements_by_property_value(*a, **k))

    async def _async_get_elements(self, *a, **k):
        self.reads += 1
        if self.fail:
            raise self.fail
        return self._visible(await super()._async_get_elements(*a, **k))

    async def _async_get_placement_reports(self, dashboard_guid, placement_qualified_names, **kwargs):
        saved = self.relationships
        self.relationships = [r for r in saved if not (r["type"] == "DataSetContent" and r["end2"] in self.hidden)]
        try:
            return await super()._async_get_placement_reports(dashboard_guid, placement_qualified_names)
        finally:
            self.relationships = saved


async def _write(fake, sheet):
    from md_processing.v2.dashboard_sheet import DashboardSheetEgeriaWriter
    await DashboardSheetEgeriaWriter(fake).write_sheet(sheet)


def _demo_sheet():
    from pyegeria.view._output_dashboard_sheet_models import DashboardSheet, Placement
    return DashboardSheet(name=DEMO, heading="Restricted placement demo", description="B4 proof sheet", placements=[
        Placement(ref="Public Collections", span="2", emphasis="panel"),
        Placement(ref="Peter Only", span="1", emphasis="kpi"),
        Placement(ref="Read me", content="**Notes** for the sheet", span="full", emphasis="panel"),
    ])


def _report(fake, name, spec):
    return fake.add_entity("Report", f"DeployedReport::{name}", name, description=f"{name} description",
                           additionalProperties={"reportSpec": spec, "outputFormat": "DICT",
                                                 "search_string": "Coco"})


def _viewer(user_id, *, hide_peter_only: bool):
    import asyncio
    fake = ViewerEgeria(user_id)
    fake_egeria.seed_perspectives(fake)
    pub = _report(fake, "Public Collections", "Collections")
    peter = _report(fake, "Peter Only", "Collections")
    asyncio.run(_write(fake, _demo_sheet()))
    if hide_peter_only:
        fake.hidden.add(peter)          # zoned to peterprofile; erinoverview cannot see it
    fake.guids = {"pub": pub, "peter": peter}
    return fake


@pytest.fixture(autouse=True)
def _no_real_explorer(monkeypatch):
    """The fakes answer every read; a real ClassificationExplorer would try to reach the fake URL."""
    built = []
    try:
        import pyegeria.omvs.classification_explorer as ce
    except ImportError:
        yield built
        return

    class _Refused:
        def __init__(self, *a, **k):
            built.append(a)
            raise AssertionError("a real ClassificationExplorer was built: a fake lacks a read pyegeria uses")
    monkeypatch.setattr(ce, "ClassificationExplorer", _Refused)
    yield built
    assert not built, "a real ClassificationExplorer was built: a fake lacks a read pyegeria uses"


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("PYEGERIA_REPORT_SPEC_CACHE_DIR", str(tmp_path / "cache"))
    ed._reset_for_tests()
    yield
    ed._reset_for_tests()


@pytest.fixture
def viewers(monkeypatch):
    fakes = {ERIN_TOKEN: _viewer("erinoverview", hide_peter_only=True),
             PETER_TOKEN: _viewer("peterprofile", hide_peter_only=False)}

    def factory(conn):
        assert conn["user_pwd"] == ""                    # never a password, only the viewer's token
        return fakes[conn["token"]]
    monkeypatch.setattr(ed, "_build_client", factory)
    return fakes


def creds(token, user):
    return {"user_id": user, "password": "", "token": token}


ERIN = creds(ERIN_TOKEN, "erinoverview")
PETER = creds(PETER_TOKEN, "peterprofile")


# ── identity ───────────────────────────────────────────────────────────────────

def test_no_token_means_sign_in_never_a_service_user():
    with pytest.raises(ed.SignInRequired):
        ed.list_sheets({"user_id": "garygeeke", "password": "secret", "token": ""})
    with pytest.raises(ed.SignInRequired):
        ed.show_sheet(None, DEMO)


def test_cache_key_is_the_presented_credential_not_the_user_id():
    conn = {"token": "abc", "platform_url": "https://p", "view_server": "v", "user_id": "erin"}
    key = ed.credential_identity(conn)
    assert "abc" not in key and "erin" not in key
    assert key != ed.credential_identity({**conn, "token": "abd"})
    assert key == ed.credential_identity({**conn, "user_id": "someone-else"})   # claims do not matter


# ── list: three different answers ──────────────────────────────────────────────

def test_list_is_per_viewer(viewers):
    viewers[PETER_TOKEN].hidden.add(viewers[PETER_TOKEN].by_qn(f"Dashboard::{DEMO}"))
    erin = ed.list_sheets(ERIN)
    assert erin["state"] == "ok" and [s["name"] for s in erin["sheets"]] == [DEMO]
    assert erin["sheets"][0]["placement_count"] == 3
    peter = ed.list_sheets(PETER)
    assert peter["state"] == "no_sheets" and peter["sheets"] == []
    assert "no Dashboard Sheets that peterprofile can see" in peter["message"]


def test_list_read_failure_is_not_no_sheets_and_is_not_cached(viewers):
    viewers[ERIN_TOKEN].fail = ConnectionError("Egeria is starting")
    out = ed.list_sheets(ERIN)
    assert out["state"] == "read_failed" and "Could not read" in out["message"]
    assert "Egeria is starting" in out["message"]
    viewers[ERIN_TOKEN].fail = None
    assert ed.list_sheets(ERIN)["state"] == "ok"        # the failure was not cached


def test_refused_token_is_sign_in_again(viewers):
    import httpx
    from pyegeria.core._exceptions import PyegeriaUnauthorizedException
    viewers[ERIN_TOKEN].fail = PyegeriaUnauthorizedException(
        httpx.Response(401, request=httpx.Request("POST", "http://x")), {"caller method": "t"}, {"userid": "e"})
    with pytest.raises(ed.TokenRefused):
        ed.list_sheets(ERIN)
    with pytest.raises(ed.TokenRefused):
        ed.show_sheet(ERIN, DEMO)


# ── list: a configured folder's own reason (Architect F3) ──────────────────────

EA_FOLDER = "DashboardFolder::Consumer::EgeriaAdvisor"
LISTING_NOT_FOUND = _HERE / "fixtures" / "dashboards" / "listing_folder_not_found_2026-11-11.json"


def _captured_listing(monkeypatch, sheets=()):
    """pyegeria's real DashboardSheetListing, with the notes and folders captured read-only from hedwig's
    Egeria (pyegeria 6.2.36, no EA consumer folder there), returned by the resolver EA calls."""
    import json

    from pyegeria.view import dashboard_resolver
    from pyegeria.view.dashboards import DashboardSheetListing
    cap = json.loads(LISTING_NOT_FOUND.read_text())
    listing = DashboardSheetListing(list(sheets), notes=cap["notes"], folders=cap["folders"])
    monkeypatch.setattr(dashboard_resolver, "list_dashboard_sheets_for_viewer", lambda client, folders=None: listing)
    return cap


def test_folder_not_found_says_the_folders_reason_not_no_sheets(viewers, monkeypatch):
    cap = _captured_listing(monkeypatch)
    out = ed.list_sheets(ERIN)
    assert out["state"] == "no_sheets"
    assert cap["notes"][0] in out["message"]                      # the folder's own words, verbatim
    assert "not found, or not visible to erinoverview" in out["message"]
    assert "Egeria holds no Dashboard Sheets" not in out["message"]
    assert out["notes"] == cap["notes"]
    assert out["folders"] == cap["folders"]
    assert out["folders"][0]["qualifiedName"] == EA_FOLDER and out["folders"][0]["status"] == "not found"


def test_folder_notes_survive_the_cache(viewers, monkeypatch):
    cap = _captured_listing(monkeypatch)
    ed.list_sheets(ERIN)
    again = ed.list_sheets(ERIN)
    assert again["cached"] is True and again["notes"] == cap["notes"] and cap["notes"][0] in again["message"]


def test_notes_with_sheets_are_the_message(viewers, monkeypatch):
    from pyegeria.view.dashboard_resolver import list_dashboard_sheets_for_viewer
    real = list_dashboard_sheets_for_viewer(viewers[ERIN_TOKEN], [])
    assert [s.name for s in real] == [DEMO]
    cap = _captured_listing(monkeypatch, sheets=real)
    out = ed.list_sheets(ERIN)
    assert out["state"] == "ok" and [s["name"] for s in out["sheets"]] == [DEMO]
    assert out["message"] == cap["notes"][0]


@pytest.mark.parametrize("folder, words", [
    ({"qualifiedName": EA_FOLDER, "status": "not read", "reason": "Egeria answered 500", "sheets": 0},
     f"{EA_FOLDER}: Egeria answered 500"),
    ({"qualifiedName": EA_FOLDER, "status": "not read", "reason": None, "sheets": 0}, f"{EA_FOLDER}: not read"),
    ({"qualifiedName": EA_FOLDER, "status": None, "reason": None, "sheets": 0}, f"{EA_FOLDER}: not read"),
])
def test_an_unread_folder_with_no_notes_says_its_reason_or_status(viewers, monkeypatch, folder, words):
    from pyegeria.view import dashboard_resolver
    from pyegeria.view.dashboards import DashboardSheetListing
    listing = DashboardSheetListing([], notes=[], folders=[folder])
    monkeypatch.setattr(dashboard_resolver, "list_dashboard_sheets_for_viewer", lambda client, folders=None: listing)
    out = ed.list_sheets(ERIN)
    assert out["state"] == "no_sheets"
    assert out["message"] == f"No Dashboard Sheets to show for erinoverview: {words}."
    assert ": ." not in out["message"]


def test_read_folders_with_no_sheets_keep_the_no_sheets_words(viewers, monkeypatch):
    from pyegeria.view import dashboard_resolver
    from pyegeria.view.dashboards import DashboardSheetListing
    listing = DashboardSheetListing([], notes=[], folders=[
        {"qualifiedName": EA_FOLDER, "status": "read", "reason": None, "sheets": 0}])
    monkeypatch.setattr(dashboard_resolver, "list_dashboard_sheets_for_viewer", lambda client, folders=None: listing)
    out = ed.list_sheets(ERIN)
    assert out["state"] == "no_sheets" and "no Dashboard Sheets that erinoverview can see" in out["message"]
    assert out["folders"][0]["status"] == "read"


# ── show: restricted placements, order, span, emphasis, sources ────────────────

def test_erin_sees_report_and_restricted_peter_sees_two_reports(viewers):
    erin = ed.show_sheet(ERIN, DEMO)
    assert erin["state"] == "ok"
    kinds = [(p["position"], p["kind"]) for p in erin["sheet"]["placements"]]
    assert kinds == [(1, "report"), (2, "restricted"), (3, "text")]
    restricted = erin["sheet"]["placements"][1]
    assert restricted["restricted"] is True and restricted["reason"] == "not visible to you"
    assert restricted["runnable"] is False and restricted["restricted_element"] == "Report"
    assert restricted["report"] is None and restricted["spec"] is None   # nothing about it leaks

    peter = ed.show_sheet(PETER, DEMO)
    assert [p["kind"] for p in peter["sheet"]["placements"]] == ["report", "report", "text"]
    assert peter["sheet"]["placements"][1]["runnable"] is True


def test_viewers_never_share_a_cache_entry(viewers):
    ed.show_sheet(PETER, DEMO)                                   # peter's answer is cached first
    erin = ed.show_sheet(ERIN, DEMO)
    assert erin["cached"] is False
    assert erin["sheet"]["placements"][1]["kind"] == "restricted"
    again = ed.show_sheet(ERIN, DEMO)
    assert again["cached"] is True and again["sheet"]["placements"][1]["kind"] == "restricted"


def test_span_emphasis_text_and_builtin_source(viewers):
    pl = ed.show_sheet(PETER, DEMO)["sheet"]["placements"]
    assert (pl[0]["span"], pl[0]["emphasis"], pl[0]["title"]) == ("2", "panel", "Public Collections")
    assert (pl[1]["span"], pl[1]["emphasis"]) == ("1", "kpi")
    assert pl[0]["spec"] == "Collections" and pl[0]["source"] == "builtin"
    assert (pl[2]["kind"], pl[2]["content"], pl[2]["span"]) == ("text", "**Notes** for the sheet", "full")
    assert pl[2]["source"] is None and pl[2]["runnable"] is False


def test_unknown_sheet_is_not_visible_not_empty(viewers):
    out = ed.show_sheet(ERIN, "no-such-sheet")
    assert out["state"] == "not_visible" and out["sheet"] is None
    assert "no Dashboard Sheet named 'no-such-sheet' that erinoverview can see" in out["message"]


def test_hidden_sheet_reads_as_not_visible(viewers):
    viewers[ERIN_TOKEN].hidden.add(viewers[ERIN_TOKEN].by_qn(f"Dashboard::{DEMO}"))
    assert ed.show_sheet(ERIN, DEMO)["state"] == "not_visible"
    assert ed.show_sheet(PETER, DEMO)["state"] == "ok"


def test_show_read_failure_says_could_not_read(viewers, monkeypatch):
    def boom(*a, **k):
        raise ConnectionError("platform down")
    monkeypatch.setattr("pyegeria.view.dashboard_resolver.resolve_dashboard_sheet", boom)
    out = ed.show_sheet(ERIN, DEMO)
    assert out["state"] == "read_failed" and "Could not read Dashboard Sheet" in out["message"]


def test_egeria_and_user_sources(viewers):
    """A placement whose spec is held in Egeria shows "egeria"; one EA registered shows "user"."""
    from pyegeria.view.base_report_formats import get_report_registry
    from advisor.report_spec_parser import register_report_spec
    _ss = importlib.util.spec_from_file_location("ea_spec_sync_tests", _HERE / "test_egeria_spec_sync.py")
    spec_tests = importlib.util.module_from_spec(_ss)
    _ss.loader.exec_module(spec_tests)
    SpecEgeria, _spec_el = spec_tests.FakeEgeria, spec_tests._spec_el
    held = "EA Dashboard Held Spec"
    ess._reset_for_tests()
    from pyegeria.view import egeria_spec_cache
    egeria_spec_cache._invalidate_tier()
    ess.sync_now(client_factory=lambda: SpecEgeria(members=[_spec_el(held, "e0000000-0000-0000-0000-00000000000e")]))
    register_report_spec("ea-dashboard-user-spec", get_report_registry()["Collections"].model_copy())
    try:
        peter = viewers[PETER_TOKEN]
        peter.entities[peter.guids["pub"]]["props"]["additionalProperties"]["reportSpec"] = held
        peter.entities[peter.guids["peter"]]["props"]["additionalProperties"]["reportSpec"] = "ea-dashboard-user-spec"
        pl = ed.show_sheet(PETER, DEMO, refresh=True)["sheet"]["placements"]
        assert (pl[0]["spec"], pl[0]["source"]) == (held, "egeria")
        assert (pl[1]["spec"], pl[1]["source"]) == ("ea-dashboard-user-spec", "user")
    finally:
        from pyegeria.view import base_report_formats as brf
        brf._RUNTIME_REPORT_FORMATS.pop("ea-dashboard-user-spec", None)
        getattr(brf, "report_specs", {}).pop("ea-dashboard-user-spec", None)
        ess._reset_for_tests()
        egeria_spec_cache._invalidate_tier()


# ── run: EA's report execution, as the viewer ──────────────────────────────────

class FakePipeline:
    def __init__(self):
        self.calls = []

    def _read_pyegeria_connection(self, egeria_credentials=None):
        return {"view_server": "fake-view", "platform_url": "https://fake:9443",
                "user_id": egeria_credentials["user_id"], "user_pwd": "", "token": egeria_credentials["token"]}

    def exec_spec_raw(self, report_name, **kw):
        self.calls.append((report_name, kw))
        return {"kind": "text", "content": f"| ran {report_name} |"}, None

    @staticmethod
    def _format_output(raw, fmt, report_name, spec=None):
        from advisor.report_pipeline import ReportPipeline
        return ReportPipeline._format_output(raw, fmt, report_name, spec)


def test_run_report_placement_as_the_viewer(viewers):
    pipe = FakePipeline()
    out = ed.run_placement(PETER, DEMO, 2, pipeline=pipe)
    assert out["state"] == "ok" and out["response"] == "| ran Collections |"
    name, kw = pipe.calls[0]
    assert kw["egeria_credentials"] is PETER                      # the viewer's own credentials
    # B7a: the Report's own DICT is shown as a tile (B5 replaced it with TABLE).
    assert kw["search_string"] == "Coco" and kw["output_type"] == "DICT"


def test_restricted_text_and_absent_placements_never_run(viewers):
    pipe = FakePipeline()
    out = ed.run_placement(ERIN, DEMO, 2, pipeline=pipe)
    assert out["state"] == "refused" and out["restricted"] is True and "not visible to you" in out["message"]
    assert ed.run_placement(ERIN, DEMO, 3, pipeline=pipe)["state"] == "refused"
    assert ed.run_placement(ERIN, DEMO, 9, pipeline=pipe)["state"] == "refused"
    assert ed.run_placement(ERIN, "no-such-sheet", 1, pipeline=pipe)["state"] == "not_visible"
    assert pipe.calls == []


def _real_pipeline(monkeypatch, executor):
    """EA's real ReportPipeline (run_report -> exec_report_spec), with pyegeria's executor replaced."""
    from advisor.report_pipeline import ReportPipeline
    monkeypatch.setattr(ReportPipeline, "_read_pyegeria_connection", lambda self, egeria_credentials=None: {
        "view_server": "fake-view", "platform_url": "https://fake:9443",
        "user_id": egeria_credentials["user_id"], "user_pwd": "", "token": egeria_credentials["token"]})
    monkeypatch.setattr("pyegeria.exec_report_spec", executor)
    return ReportPipeline()


def test_analytic_placement_gets_only_its_analytic_params(viewers, monkeypatch):
    """B5-1: an analytic function refuses the find vocabulary. Live on laz, count_elements() raised
    "unexpected keyword argument 'search_string'"; the fake executor here is just as strict."""
    peter = viewers[PETER_TOKEN]
    props = peter.entities[peter.guids["peter"]]["props"]["additionalProperties"]
    props["reportSpec"] = "Analytic Demo - Element Count by Type"
    props["analyticParams"] = '{"metadata_element_type": "Asset"}'
    seen = {}

    def count_elements(metadata_element_type=None, as_of_time=None):   # no **kwargs: unknown names raise
        return {"kind": "json", "data": [{"type": metadata_element_type, "count": 7}]}

    def executor(name, output_format=None, params=None, token=None, user=None, **conn):
        seen.update(name=name, params=dict(params), token=token, user=user)
        return count_elements(**params)

    out = ed.run_placement(PETER, DEMO, 2, pipeline=_real_pipeline(monkeypatch, executor))
    assert out["state"] == "ok" and out["analytic"] is True, out
    assert seen["params"] == {"metadata_element_type": "Asset"}
    assert (seen["token"], seen["user"]) == (PETER_TOKEN, "peterprofile")
    assert "Asset" in out["response"]


def test_failed_run_is_failed_with_words_not_ok(viewers, monkeypatch):
    """B5-2: a run that raises is "failed" with the executor's words, never "ok" with nothing."""
    def executor(name, **kw):
        raise TypeError("count_elements() got an unexpected keyword argument 'search_string'")
    out = ed.run_placement(PETER, DEMO, 2, pipeline=_real_pipeline(monkeypatch, executor))
    assert out["state"] == "failed" and out["response"] is None
    assert "unexpected keyword argument 'search_string'" in out["message"]


def test_report_error_from_executor_is_failed(viewers, monkeypatch):
    def executor(name, **kw):
        raise ValueError("Report 'Collections' does not support output format TABLE")
    out = ed.run_placement(PETER, DEMO, 2, pipeline=_real_pipeline(monkeypatch, executor))
    assert out["state"] == "failed" and "does not support output format" in out["message"]


def test_a_refused_token_is_told_by_class_and_code_not_by_words():
    """TR-22: a transport 401 (pyegeria's PyegeriaUnauthorizedException, related_http_code 401) is the viewer's token;
    Egeria's hidden-zone 403-020 (the same class, related_http_code 403) is "restricted"; words alone are neither."""
    import httpx
    from pyegeria.core._exceptions import PyegeriaUnauthorizedException

    req = httpx.Request("POST", "http://x")
    refused = PyegeriaUnauthorizedException(httpx.Response(401, request=req), {}, {"userid": "e"})
    body = {"relatedHTTPCode": 403, "exceptionClassName": "UserNotAuthorizedException",
            "exceptionErrorMessageId": "OPEN-METADATA-SECURITY-403-020",
            "exceptionErrorMessage": "User x is not authorized to issue an operation read"}
    hidden = PyegeriaUnauthorizedException(httpx.Response(200, request=req, json=body), {}, body)
    assert ed._is_token_refused(refused)
    assert not ed._is_token_refused(hidden)
    assert not ed._is_token_refused(RuntimeError("HTTP 401: bearer token expired, unauthorized"))
    assert not ed._is_token_refused(RuntimeError("AUTHORIZATION_ERROR_401 OPEN-METADATA-SECURITY-403-020 read"))


def test_spec_sync_invalidates_resolved_sheets(viewers):
    ed.show_sheet(ERIN, DEMO)
    assert ed.show_sheet(ERIN, DEMO)["cached"] is True
    ess._after_sync()
    assert ed.show_sheet(ERIN, DEMO)["cached"] is False


# ── end to end through the HTTP routes ─────────────────────────────────────────

@pytest.fixture
def http(viewers, monkeypatch):
    from fastapi.testclient import TestClient
    from advisor import auth
    from advisor.web.app import app
    monkeypatch.setattr(auth, "_auth_enabled", lambda: True)
    monkeypatch.setattr("advisor.report_pipeline.ReportPipeline._read_pyegeria_connection",
                        lambda self, egeria_credentials=None: {
                            "view_server": "fake-view", "platform_url": "https://fake:9443",
                            "user_id": egeria_credentials["user_id"], "user_pwd": "",
                            "token": egeria_credentials["token"]})
    client = TestClient(app, raise_server_exceptions=False)

    def as_user(user, token):
        jwt = auth.create_access_token(user_id=user, egeria_token=token)
        return {"Authorization": f"Bearer {jwt}"}
    return client, as_user


def test_http_list_show_and_run(http, monkeypatch):
    client, as_user = http
    erin, peter = as_user("erinoverview", ERIN_TOKEN), as_user("peterprofile", PETER_TOKEN)

    listed = client.get("/api/dashboards", headers=erin)
    assert listed.status_code == 200 and listed.json()["sheets"][0]["name"] == DEMO

    shown = client.get(f"/api/dashboards/{DEMO}", headers=erin).json()
    assert [p["kind"] for p in shown["sheet"]["placements"]] == ["report", "restricted", "text"]
    shown = client.get(f"/api/dashboards/{DEMO}", headers=peter).json()
    assert [p["kind"] for p in shown["sheet"]["placements"]] == ["report", "report", "text"]

    missing = client.get("/api/dashboards/nope", headers=erin).json()
    assert missing["state"] == "not_visible"

    pipe = FakePipeline()
    monkeypatch.setattr("advisor.report_pipeline.get_report_pipeline", lambda: pipe)
    refused = client.post(f"/api/dashboards/{DEMO}/placements/2/run", headers=erin, json={}).json()
    assert refused["state"] == "refused" and refused["restricted"] is True
    ran = client.post(f"/api/dashboards/{DEMO}/placements/2/run", headers=peter, json={}).json()
    assert ran["state"] == "ok" and pipe.calls[0][1]["egeria_credentials"]["user_id"] == "peterprofile"


def test_http_requires_sign_in(http):
    client, _ = http
    assert client.get("/api/dashboards").status_code == 401
