"""API reference: fetching a running instance's OpenAPI document, read-only, validated, bounded, stored."""
from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from resource_explorer.api_reference import live
from resource_explorer.registry import Project, ProjectRegistry

FIX = Path(__file__).parent / "fixtures" / "openapi"
SPEC = (FIX / "testgen-openapi.json").read_text()
URL = "http://localhost:8530/api/openapi.json"


def _transport(handler):
    return httpx.MockTransport(handler)


def _ok(request):
    return httpx.Response(200, text=SPEC, headers={"content-type": "application/json"})


@pytest.fixture
def db(tmp_path):
    r = ProjectRegistry(db_path=str(tmp_path / "t.db"))
    r.add(Project(slug="tg", display_name="TG", github_url="https://github.com/x/tg", description=""))
    return r


class TestFetch:
    def test_valid_document_accepted(self):
        res = live.fetch_openapi(URL, transport=_transport(_ok))
        assert res.ok and res.reason == "" and res.document["openapi"] == "3.1.0"
        assert res.status_code == 200 and res.bytes == len(SPEC.encode()) and res.fetched_at
        assert res.content_type == "application/json"

    def test_html_is_rejected_by_content_type_naming_what_came_back(self):
        # the Streamlit console on :8501 serves HTML for every path, including a path that looks like a spec
        res = live.fetch_openapi("http://localhost:8501/api/openapi.json", transport=_transport(
            lambda r: httpx.Response(200, text="<!doctype html><html></html>", headers={"content-type": "text/html; charset=utf-8"})))
        assert not res.ok and res.document is None
        assert res.reason == "not an OpenAPI document: got text/html"

    def test_html_with_json_content_type_is_still_rejected(self):
        res = live.fetch_openapi(URL, transport=_transport(
            lambda r: httpx.Response(200, text="<html>nope</html>", headers={"content-type": "application/json"})))
        assert not res.ok and res.reason.startswith("not an OpenAPI document")

    def test_json_without_openapi_key_rejected(self):
        res = live.fetch_openapi(URL, transport=_transport(
            lambda r: httpx.Response(200, json={"status": "ok"})))
        assert not res.ok and "no 'openapi' or 'swagger' key" in res.reason

    def test_yaml_content_accepted(self):
        res = live.fetch_openapi("http://h/openapi.yaml", allow=["h"], resolver=lambda h, p: ["10.9.9.9"], transport=_transport(
            lambda r: httpx.Response(200, text=(FIX / "petstore-swagger2.yaml").read_text(),
                                     headers={"content-type": "application/yaml"})))
        assert res.ok and res.document["swagger"] == "2.0"

    def test_size_cap(self):
        big = json.dumps({"openapi": "3.0.0", "paths": {}, "pad": "x" * 5000})
        res = live.fetch_openapi(URL, max_bytes=1000, transport=_transport(
            lambda r: httpx.Response(200, text=big, headers={"content-type": "application/json"})))
        assert not res.ok and "larger than 1000 bytes" in res.reason

    def test_timeout_is_said(self):
        def boom(request):
            raise httpx.ReadTimeout("slow", request=request)
        res = live.fetch_openapi(URL, timeout=2, transport=_transport(boom))
        assert not res.ok and res.reason == "no answer in time"

    def test_connection_refused_is_said(self):
        def boom(request):
            raise httpx.ConnectError("refused", request=request)
        res = live.fetch_openapi(URL, transport=_transport(boom))
        assert not res.ok and res.reason == "could not connect"

    @pytest.mark.parametrize("status,needle", [(404, "404"), (401, "sign-in"), (500, "answered 500")])
    def test_http_errors(self, status, needle):
        res = live.fetch_openapi(URL, transport=_transport(lambda r: httpx.Response(status, text="x")))
        assert not res.ok and needle in res.reason

    @pytest.mark.parametrize("url", ["file:///etc/passwd", "ftp://h/x", "javascript:1", "localhost:8530/x", ""])
    def test_only_http_and_https(self, url):
        called = []
        res = live.fetch_openapi(url, transport=_transport(lambda r: called.append(r) or _ok(r)))
        assert not res.ok and "http(s)" in res.reason and not called

    def test_no_credentials_or_cookies_sent(self):
        seen = {}

        def handler(request):
            seen.update(request.headers)
            return _ok(request)
        live.fetch_openapi(URL, transport=_transport(handler))
        assert "authorization" not in seen and "cookie" not in seen
        assert "json" in seen.get("accept", "")

    def test_userinfo_in_url_is_refused_not_sent(self):
        res = live.fetch_openapi("http://user:pw@localhost:8530/openapi.json", transport=_transport(_ok))
        assert not res.ok and "credentials" in res.reason

    def test_same_host_redirect_followed(self):
        def handler(request):
            if request.url.path == "/old":
                return httpx.Response(302, headers={"location": "/api/openapi.json"})
            return _ok(request)
        res = live.fetch_openapi("http://localhost:8530/old", transport=_transport(handler))
        assert res.ok

    def test_redirect_to_another_host_refused(self):
        hits = []

        def handler(request):
            hits.append(str(request.url))
            return httpx.Response(302, headers={"location": "http://evil.example/openapi.json"})
        res = live.fetch_openapi(URL, transport=_transport(handler))
        assert not res.ok and "another host" in res.reason and len(hits) == 1

    def test_redirect_to_another_allowed_host_is_still_refused_as_another_host(self):
        def handler(request):
            return httpx.Response(302, headers={"location": "http://other.test/openapi.json"})
        res = live.fetch_openapi(URL, allow=["localhost", "other.test"], resolver=lambda h, p: ["127.0.0.1"],
                                 transport=_transport(handler))
        assert not res.ok and "another host" in res.reason

    def test_redirect_loop_bounded(self):
        res = live.fetch_openapi(URL, transport=_transport(lambda r: httpx.Response(302, headers={"location": "/api/openapi.json"})))
        assert not res.ok and "too many redirects" in res.reason


class TestRegister:
    def test_register_stores_url_document_and_time(self, db):
        row = live.register_instance(db, "tg", URL, by="dan", transport=_transport(_ok))
        assert row["ok"] and row["url"] == URL
        stored = live.load_instance(db, "tg")
        assert stored["url"] == URL and stored["fetched_at"] and stored["registered_by"] == "dan"
        assert live.instance_interface(db, "tg").title == "TestGen API" and stored["status"] == "ok"

    def test_rejected_registration_stores_nothing(self, db):
        row = live.register_instance(db, "tg", "http://localhost:8501/x", transport=_transport(
            lambda r: httpx.Response(200, text="<html/>", headers={"content-type": "text/html"})))
        assert not row["ok"] and row["reason"] == "not an OpenAPI document: got text/html"
        assert live.load_instance(db, "tg") is None

    def test_refetch_failure_keeps_last_good_document_and_says_so(self, db):
        live.register_instance(db, "tg", URL, transport=_transport(_ok))
        again = live.refetch_instance(db, "tg", transport=_transport(lambda r: httpx.Response(503, text="down")))
        assert not again["ok"]
        stored = live.load_instance(db, "tg")
        assert len(live.instance_interface(db, "tg").operations) == 26       # last good copy survives
        assert stored["status"] == "failed" and "503" in stored["error"]
        assert stored["fetched_at"]                                    # the time of the copy shown, not of the failure
        assert stored["attempted_at"] >= stored["fetched_at"]

    def test_refetch_success_replaces(self, db):
        live.register_instance(db, "tg", URL, transport=_transport(_ok))
        live.refetch_instance(db, "tg", transport=_transport(lambda r: httpx.Response(200, json={"openapi": "3.0.0", "paths": {}})))
        assert live.instance_interface(db, "tg").spec_version == "openapi 3.0.0"

    def test_refetch_without_registration(self, db):
        assert live.refetch_instance(db, "tg", transport=_transport(_ok))["reason"] == "no running-instance URL is registered"

    def test_clear(self, db):
        live.register_instance(db, "tg", URL, transport=_transport(_ok))
        live.clear_instance(db, "tg")
        assert live.load_instance(db, "tg") is None

    def test_interface_from_stored_instance_carries_url_and_time(self, db):
        live.register_instance(db, "tg", URL, transport=_transport(_ok))
        iface = live.instance_interface(db, "tg")
        assert iface.source == "running_instance" and iface.origin == URL and iface.as_of
        assert "running instance at http://localhost:8530/api/openapi.json, fetched" in iface.source_phrase
        assert len(iface.operations) == 26

    def test_content_lands_in_the_api_reference_tables(self, db):
        live.register_instance(db, "tg", URL, by="dan", transport=_transport(_ok))
        with db._conn() as c:
            assert c.execute("SELECT COUNT(*) FROM api_reference_operations").fetchone()[0] == 26
            assert c.execute("SELECT COUNT(*) FROM api_reference_schemas").fetchone()[0] == 73
            d = c.execute("SELECT origin, status, registered_by, source FROM api_reference_documents").fetchone()
            assert (d["origin"], d["status"], d["registered_by"], d["source"]) == (URL, "ok", "dan", "running_instance")
