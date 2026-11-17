"""S1: drafts list (mine), new from template/prototype, save, validate, preview, all as the user.

The routes are tested at Studio's seam (trellis_studio.authoring); the seam is tested with a fake store whose
answers have pyegeria.authoring's real shapes (fixtures/drafts_real_shape.json says where each came from); and
the no-Egeria parts (templates, new from a template, a structured table result) run real pyegeria code.
"""
from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

from trellis_studio import authoring

FIX = json.loads((Path(__file__).parent / "fixtures" / "drafts_real_shape.json").read_text())


class FakeClient:
    def __init__(self, user_id):
        self.user_id = user_id
        self.thread = threading.get_ident()


@pytest.fixture
def clients(monkeypatch):
    built = []

    def fake_user_client(user, settings):
        c = FakeClient(user.get("user_id"))
        built.append(c)
        return c
    monkeypatch.setattr(authoring, "user_client", fake_user_client)
    return built


class FakeStore:
    def __init__(self, client, drafts=None, notes=None, fail=None, refuse=None):
        self.client, self.drafts, self.notes_out, self.fail, self.refuse = client, drafts or [], notes or [], fail, refuse
        self.saved = []

    async def async_list(self, user_id, kind=None, *, notes=None):
        if self.fail:
            raise self.fail
        if notes is not None:
            notes.extend(self.notes_out)
        return [d for d in self.drafts if kind in (None, d["kind"])]

    async def async_save(self, user_id, draft):
        if self.refuse:
            raise self.refuse
        self.saved.append((user_id, draft))
        return draft.qualified_name or "x"

    async def async_load(self, user_id, kind, name):
        return None


def _use_store(monkeypatch, **kw):
    holder = {}

    def make(client):
        holder["store"] = FakeStore(client, **kw)
        return holder["store"]
    monkeypatch.setattr(authoring, "_store", make)
    return holder


# ---- the drafts list: "at least" unless confirmed ---------------------------------------------------------

def test_list_complete_only_when_pyegeria_confirmed(authed, clients, monkeypatch):
    _use_store(monkeypatch, drafts=FIX["drafts_list"])
    monkeypatch.setattr(authoring, "list_drafts_supports_notes", lambda: True)
    body = authed.get("/api/drafts").json()
    assert body["complete"] is True and body["count"] == 1
    assert body["drafts"][0]["qualified_name"].startswith("Coco Pharmaceuticals::DeployedReport::")
    assert clients[0].user_id == "peterprofile"


def test_list_unconfirmed_carries_pyegerias_words(authed, clients, monkeypatch):
    _use_store(monkeypatch, drafts=FIX["drafts_list"], notes=["Showing at least 1 report drafts; Egeria didn't confirm there are no more."])
    monkeypatch.setattr(authoring, "list_drafts_supports_notes", lambda: True)
    body = authed.get("/api/drafts").json()
    assert body["complete"] is False and "at least" in body["notes"][0]


def test_list_on_a_pyegeria_without_notes_is_never_complete(authed, clients, monkeypatch):
    _use_store(monkeypatch, drafts=[])
    monkeypatch.setattr(authoring, "list_drafts_supports_notes", lambda: False)
    body = authed.get("/api/drafts").json()
    assert body["complete"] is False and body["count"] == 0
    assert "may be more" in body["notes"][0]


def test_unreadable_list_is_502_never_empty(authed, clients, monkeypatch):
    _use_store(monkeypatch, fail=ConnectionError("boom"))
    r = authed.get("/api/drafts")
    assert r.status_code == 502 and "couldn't be read" in r.json()["detail"]
    assert "drafts" not in r.json()


def test_bad_kind_is_400(authed, clients):
    assert authed.get("/api/drafts?kind=table").status_code == 400


# ---- as the user, on a worker thread, nothing shared ------------------------------------------------------

def test_each_request_builds_its_own_client_for_its_own_user(app, client, clients, monkeypatch):
    import trellis_auth
    from conftest import fake_egeria_token
    _use_store(monkeypatch, drafts=[])
    monkeypatch.setattr(authoring, "list_drafts_supports_notes", lambda: True)
    for who in ("peterprofile", "erinoverview"):
        tok = trellis_auth.create_access_token(who, fake_egeria_token(who), app.state.auth_config)
        assert client.get("/api/drafts", headers={"Authorization": f"Bearer {tok}"}).status_code == 200
    assert [c.user_id for c in clients] == ["peterprofile", "erinoverview"]
    assert all(c.thread != threading.get_ident() for c in clients)   # built on a worker thread


# ---- save: refusals pass through in words ----------------------------------------------------------------

def _template_report():
    import pyegeria.authoring as a
    return a.new_from(template="report", new_label="My Report").model_dump(mode="json")


def test_save_writes_as_the_user(authed, clients, monkeypatch):
    holder = _use_store(monkeypatch)
    r = authed.put("/api/drafts", json={"draft": _template_report()})
    assert r.status_code == 200 and "private draft zone" in r.json()["words"]
    uid, draft = holder["store"].saved[0]
    assert uid == "peterprofile" and draft.display_name == "My Report"


def test_unknown_lookup_refuses_the_write_409(authed, clients, monkeypatch):
    import pyegeria.authoring as a
    words = "Couldn't check whether user id 'peterprofile' is also a governance zone's name; nothing was saved."
    _use_store(monkeypatch, refuse=a.AuthoringRefused(words))
    r = authed.put("/api/drafts", json={"draft": _template_report()})
    assert r.status_code == 409 and r.json()["detail"] == words


def test_invalid_draft_is_422(authed, clients, monkeypatch):
    _use_store(monkeypatch)
    r = authed.put("/api/drafts", json={"draft": {"kind": "report"}})
    assert r.status_code == 422


def test_load_not_in_zone_is_404(authed, clients, monkeypatch):
    _use_store(monkeypatch)
    r = authed.get("/api/drafts/report/Someone Else's")
    assert r.status_code == 404 and "private draft zone" in r.json()["detail"]


def test_egeria_refusing_the_token_is_401(authed, clients, monkeypatch):
    import httpx
    from pyegeria.core._exceptions import PyegeriaUnauthorizedException
    resp = httpx.Response(401, request=httpx.Request("POST", "https://egeria.test:9443/x"))
    _use_store(monkeypatch, fail=PyegeriaUnauthorizedException(resp, context={"caller method": "test"}))
    r = authed.get("/api/drafts")
    assert r.status_code == 401 and "Sign in again" in r.json()["detail"]


# ---- real pyegeria, no Egeria ----------------------------------------------------------------------------

def test_templates_are_pyegerias(authed):
    names = {t["name"]: t["kind"] for t in authed.get("/api/templates").json()["templates"]}
    assert names == {"find-spec": "spec", "report": "report", "sheet": "sheet"}


def test_new_from_template_needs_no_egeria_and_is_unsaved(authed, monkeypatch):
    def boom(*a, **k):
        raise AssertionError("a template needs no Egeria client")
    monkeypatch.setattr(authoring, "user_client", boom)
    r = authed.post("/api/drafts/new", json={"template": "find-spec", "new_label": "Glossary Terms Lite"})
    body = r.json()
    assert r.status_code == 200 and body["saved"] is False
    d = body["draft"]
    assert d["kind"] == "spec" and d["label"] == "Glossary Terms Lite" and d["derived_from"] == "template:find-spec"
    assert d["qualified_name"] == "ReportSpec::Glossary Terms Lite"
    assert authoring.parse_draft(d).label == "Glossary Terms Lite"   # round-trips


def test_new_needs_exactly_one_source(authed):
    assert authed.post("/api/drafts/new", json={"new_label": "x"}).status_code == 400
    assert authed.post("/api/drafts/new", json={"new_label": "x", "template": "report",
                                                "prototype": {"kind": "spec", "label": "y"}}).status_code == 400


def test_prototype_copy_runs_as_the_user(authed, clients, monkeypatch):
    import pyegeria.authoring as a
    seen = {}

    async def fake_new_from(*, prototype, new_label, client, template=None):
        seen.update(prototype=prototype, user=client.user_id)
        return a.new_from(template="report", new_label=new_label)
    monkeypatch.setattr(a, "async_new_from", fake_new_from)
    r = authed.post("/api/drafts/new", json={"prototype": {"kind": "report", "label": "Catalog Health - AI Context Readiness"},
                                             "new_label": "My copy"})
    assert r.status_code == 200
    assert seen == {"prototype": ("report", "Catalog Health - AI Context Readiness"), "user": "peterprofile"}


def test_preview_passes_pyegerias_structured_table_through(authed, clients, monkeypatch):
    import pyegeria.authoring as a
    from pyegeria.view.structured_table import build_structured_table
    rows = [{"display_name": FIX["laz_report_element"]["properties"]["displayName"],
             "qualified_name": FIX["laz_report_element"]["properties"]["qualifiedName"]}]
    table = build_structured_table(rows, [{"name": "Display Name", "key": "display_name"},
                                          {"name": "Qualified Name", "key": "qualified_name"}])
    seen = {}

    async def fake_preview(draft, *, client, max_rows, params=None, **kw):
        seen.update(user=client.user_id, max_rows=max_rows, kw=kw)
        return {"kind": "report", "label": draft.display_name, "viewer": client.user_id, "result": table,
                "capped": False, "shown": 1, "notes": ["1 rows shown; Egeria didn't confirm there are no more"],
                "end": "unconfirmed"}
    monkeypatch.setattr(a, "async_preview", fake_preview)
    r = authed.post("/api/preview", json={"draft": _template_report(), "max_rows": 10})
    body = r.json()
    assert r.status_code == 200 and body["result"]["kind"] == "table" and body["viewer"] == "peterprofile"
    assert body["notes"] == ["1 rows shown; Egeria didn't confirm there are no more"]
    assert seen == {"user": "peterprofile", "max_rows": 10, "kw": {}}


def test_validate_uses_pyegerias_answer_and_runs_dr_egeria_only_when_asked(authed, clients, monkeypatch):
    import pyegeria.authoring as a
    calls = []

    async def fake_validate(draft, client, *, run=None):
        calls.append(run is not None)
        return a.ValidationResult(checked=["structure"], not_checked=[] if run else ["Dr.Egeria validate: no runner given"])
    monkeypatch.setattr(a, "async_validate", fake_validate)
    monkeypatch.setattr(a, "inprocess_runner", lambda client: (lambda md, d: {"success": True}))
    b1 = authed.post("/api/validate", json={"draft": _template_report()}).json()
    b2 = authed.post("/api/validate", json={"draft": _template_report(), "dr_egeria": True}).json()
    assert calls == [False, True]
    assert b1["ok"] is True and "no runner given" in b1["not_checked"][0]


def test_renderer_route_says_which_copy(authed):
    body = authed.get("/api/renderer").json()
    assert body["source"] in ("pyegeria", "vendored")
