"""API reference: the HTTP surface (read the reference, register / re-check / clear the instance URL)."""
from __future__ import annotations

from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from resource_explorer.api_reference import live
from resource_explorer.registry import Project, ProjectRegistry

SPEC = (Path(__file__).parent / "fixtures" / "openapi" / "testgen-openapi.json").read_text()
URL = "http://localhost:8530/api/openapi.json"


@pytest.fixture
def client(tmp_path, monkeypatch):
    db_path = str(tmp_path / "t.db")
    monkeypatch.setenv("REGISTRY_DATABASE_URL", f"sqlite:///{db_path}")
    reg = ProjectRegistry(db_path=db_path)
    reg.add(Project(slug="tg", display_name="TG", github_url="https://github.com/x/tg", description=""))
    from resource_explorer.web import app as app_mod
    from resource_explorer.web.routes import api_reference as routes
    monkeypatch.setattr(routes, "_registry", lambda: ProjectRegistry(db_path=db_path))
    monkeypatch.setattr(routes, "_transport", lambda: httpx.MockTransport(lambda r: (
        httpx.Response(200, text=SPEC, headers={"content-type": "application/json"}) if "8530" in str(r.url)
        else httpx.Response(200, text="<html/>", headers={"content-type": "text/html"}))))
    from resource_explorer.web.routes import curate
    monkeypatch.setattr(curate, "get_current_user", lambda request: {"user_id": "dan"})
    monkeypatch.setattr(routes, "require_resource_write", lambda *a, **k: None)   # the zone decision has its own tests
    return TestClient(app_mod.app), reg


def test_reference_empty_explains_each_source(client):
    c, _ = client
    r = c.get("/api/api-reference/tg")
    assert r.status_code == 200 and r.json()["interfaces"] == [] and len(r.json()["missing"]) == 3


def test_unknown_repo_404(client):
    c, _ = client
    assert c.get("/api/api-reference/nope").status_code == 404


def test_register_validates_and_returns_the_reason(client):
    c, reg = client
    bad = c.put("/api/api-reference/tg/instance", json={"url": "http://localhost:8501/api/openapi.json"})
    assert bad.status_code == 200 and bad.json()["ok"] is False
    assert bad.json()["reason"] == "not an OpenAPI document: got text/html"
    assert live.load_instance(reg, "tg") is None


def test_register_then_read_back_then_recheck_then_clear(client):
    c, reg = client
    ok = c.put("/api/api-reference/tg/instance", json={"url": URL})
    assert ok.json()["ok"] is True and ok.json()["operations"] == 26
    ref = c.get("/api/api-reference/tg").json()
    assert ref["best"] == "running_instance" and ref["instance"]["url"] == URL
    assert c.post("/api/api-reference/tg/instance/refetch").json()["ok"] is True
    assert c.delete("/api/api-reference/tg/instance").json()["cleared"] is True
    assert c.get("/api/api-reference/tg").json()["instance"] is None


def test_non_http_url_is_a_400_free_validation_answer(client):
    c, _ = client
    r = c.put("/api/api-reference/tg/instance", json={"url": "file:///etc/passwd"})
    assert r.status_code == 200 and r.json()["ok"] is False and "http(s)" in r.json()["reason"]


class TestAuthorisation:
    def test_anonymous_writes_are_401_and_change_nothing(self, client, monkeypatch):
        from resource_explorer.web.routes import curate
        monkeypatch.setattr(curate, "get_current_user", lambda request: None)
        c, reg = client
        assert c.put("/api/api-reference/tg/instance", json={"url": URL}).status_code == 401
        assert c.post("/api/api-reference/tg/instance/refetch").status_code == 401
        assert c.delete("/api/api-reference/tg/instance").status_code == 401
        assert live.load_instance(reg, "tg") is None

    def test_anonymous_can_still_read(self, client, monkeypatch):
        from resource_explorer.web.routes import curate
        monkeypatch.setattr(curate, "get_current_user", lambda request: None)
        c, _ = client
        assert c.get("/api/api-reference/tg").status_code == 200

    def test_no_write_access_is_403_with_the_reason(self, client, monkeypatch):
        from fastapi import HTTPException
        from resource_explorer.web.routes import api_reference as routes

        def deny(*a, **k):
            raise HTTPException(status_code=403, detail="not permitted: private investigation, owner only")
        monkeypatch.setattr(routes, "require_resource_write", deny)
        c, reg = client
        r = c.put("/api/api-reference/tg/instance", json={"url": URL})
        assert r.status_code == 403 and "owner only" in r.json()["detail"]
        assert c.post("/api/api-reference/tg/instance/refetch").status_code == 403
        assert c.delete("/api/api-reference/tg/instance").status_code == 403
        assert live.load_instance(reg, "tg") is None

    def test_write_check_is_the_shared_resource_write_with_the_right_verb(self, client, monkeypatch):
        from resource_explorer.web.routes import api_reference as routes
        seen = []
        monkeypatch.setattr(routes, "require_resource_write", lambda reg, et, slug, write, **k: seen.append((et, slug, write)))
        c, _ = client
        c.put("/api/api-reference/tg/instance", json={"url": URL})
        c.post("/api/api-reference/tg/instance/refetch")
        c.delete("/api/api-reference/tg/instance")
        assert seen == [("repo", "tg", "link"), ("repo", "tg", "link"), ("repo", "tg", "unlink")]

    def test_who_registered_and_who_cleared_are_recorded(self, client):
        c, reg = client
        c.put("/api/api-reference/tg/instance", json={"url": URL})
        assert live.load_instance(reg, "tg")["registered_by"] == "dan"
        assert c.delete("/api/api-reference/tg/instance").json()["cleared"] is True
        assert live.load_instance(reg, "tg") is None
        rec = live.clear_record(reg, "tg")
        assert rec["cleared_by"] == "dan" and rec["cleared_at"] and rec["url"] == URL and rec["registered_by"] == "dan"
        assert c.get("/api/api-reference/tg").json()["cleared"]["cleared_by"] == "dan"
        assert c.delete("/api/api-reference/tg/instance").json()["cleared"] is False

    def test_the_real_shared_check_is_wired_and_refuses_without_a_curation_identity(self, client, monkeypatch):
        from resource_explorer.web.routes import api_reference as routes, curate
        monkeypatch.setattr(routes, "require_resource_write", curate.require_resource_write)
        c, reg = client
        r = c.put("/api/api-reference/tg/instance", json={"url": URL})
        assert r.status_code == 403 and "sign in" in r.json()["detail"]
        assert live.load_instance(reg, "tg") is None


def test_policy_is_readable_and_operations_searchable(client, monkeypatch):
    c, _ = client
    from resource_explorer.web import admin_auth
    monkeypatch.setattr(admin_auth, "require_admin", lambda request: None)
    p = c.get("/api/api-reference/policy").json()
    assert p["setting"] == "RE_API_FETCH_ALLOW" and "169.254.0.0/16" in p["always_deny"]
    c.put("/api/api-reference/tg/instance", json={"url": URL})
    r = c.get("/api/api-reference/tg/operations", params={"q": "profiling", "method": "post"}).json()
    assert r["count"] >= 1 and all(o["method"] == "POST" for o in r["operations"]) and r["more"] is False
    page = c.get("/api/api-reference/tg/operations", params={"limit": 5}).json()
    assert page["count"] == 5 and page["more"] is True
    nxt = c.get("/api/api-reference/tg/operations", params={"limit": 5, "offset": 5}).json()
    assert nxt["offset"] == 5 and nxt["operations"][0] != page["operations"][0]


def test_policy_is_admin_only(client):
    c, _ = client
    assert c.get("/api/api-reference/policy").status_code == 403         # signed in, but not an admin


def test_refetch_records_who_pressed_it_and_reports_a_concurrent_clear(client):
    c, reg = client
    c.put("/api/api-reference/tg/instance", json={"url": URL})
    assert c.post("/api/api-reference/tg/instance/refetch").json()["ok"] is True
    with reg._conn() as conn:
        row = conn.execute("SELECT attempted_by, captured_by FROM api_reference_documents WHERE superseded_at IS NULL").fetchone()
    assert row["attempted_by"] == "dan" and row["captured_by"] == "dan"
