"""The endpoint list: reader, compiled context, and the drill-down.

Shaped like the live dataops_testgen rows (FastAPI routes under testgen/, a
large number of mock-patch "routes" under tests/ that must not count).
"""
from __future__ import annotations

import pytest

from resource_explorer.context_compile import MAX_ENDPOINTS_FULL, compile_context
from resource_explorer.endpoints import read_endpoints
from resource_explorer.ingestion.code_symbol_extractor import CodeMarker, CodeSymbol
from resource_explorer.members import members_for
from resource_explorer.registry import Project, ProjectRegistry


@pytest.fixture
def db(tmp_path):
    r = ProjectRegistry(db_path=str(tmp_path / "t.db"))
    r.add(Project(slug="tg", display_name="TG", github_url="https://github.com/x/tg", description=""))
    return r


def _marker(path, line, detail, qn, role=None, kind="http_api", fw="fastapi"):
    m = CodeMarker(resource_slug="tg", file_path=path, start_line=line, language="python",
                   marker_kind="route" if kind == "http_api" else "message_handler",
                   framework=fw, interface_kind=kind, detail=detail, qualified_name=qn)
    m.file_role = role
    return m


def _seed(db, n_routes=3, n_test=5):
    ms = [_marker("testgen/api/app.py", 10 + i, f"GET /r{i}", f"r{i}", role="core") for i in range(n_routes)]
    ms.append(_marker("testgen/api/auth.py", 5, "POST /token", "token", role="core"))
    ms += [_marker("tests/unit/test_api.py", 3 + i, "PATCH", f"mock{i}", role="test") for i in range(n_test)]
    db.upsert_code_markers("tg", ms)
    db.upsert_code_symbols("tg", [CodeSymbol(
        resource_slug="tg", file_path="testgen/api/auth.py", language="python", kind="function", name="token",
        qualified_name="token", signature="(form: OAuth2Form) -> Token", docstring="Issue a token.\nMore.",
        start_line=5, end_line=9)])
    db.upsert_finding("tg", "interface_surface", [
        {"check_name": "http_api", "label": "implemented", "confidence": 90,
         "summary": "1869 registration(s) found in code, across testgen/api/app.py " + "x" * 300}],
        surveyed_at="2026-10-14T21:24:25")


class TestReader:
    def test_test_paths_excluded_and_counted(self, db):
        _seed(db)
        es = read_endpoints(db, "tg")
        assert es.measured and es.total == 4 and es.excluded_tests == 5
        assert all("tests/" not in e.file_path for e in es.rows)

    def test_null_role_falls_back_to_path_rule(self, db):
        db.upsert_code_markers("tg", [_marker("tests/x.py", 1, "GET /a", "a", role=None),
                                      _marker("svc/x.py", 1, "GET /b", "b", role=None)])
        es = read_endpoints(db, "tg")
        assert [e.path for e in es.rows] == ["/b"] and es.excluded_tests == 1

    def test_method_path_handler_line_and_symbol_join(self, db):
        _seed(db)
        e = next(e for e in read_endpoints(db, "tg").rows if e.path == "/token")
        assert (e.method, e.handler, e.start_line) == ("POST", "token", 5)
        assert e.signature == "(form: OAuth2Form) -> Token" and e.doc == "Issue a token."

    def test_ordered_by_file_then_line(self, db):
        db.upsert_code_markers("tg", [_marker("b.py", 2, "GET /3", "c", role="core"),
                                      _marker("a.py", 9, "GET /2", "b", role="core"),
                                      _marker("a.py", 1, "GET /1", "a", role="core")])
        assert [e.path for e in read_endpoints(db, "tg").rows] == ["/1", "/2", "/3"]

    def test_never_extracted_is_not_measured(self, db):
        es = read_endpoints(db, "tg")
        assert es.measured is False and es.total == 0

    def test_captured_but_only_tests_is_measured_zero(self, db):
        db.upsert_code_markers("tg", [_marker("tests/t.py", 1, "GET /a", "a", role="test")])
        es = read_endpoints(db, "tg")
        assert es.measured is True and es.total == 0 and es.excluded_tests == 1


class TestCompile:
    def _compile(self, db):
        return compile_context(db, "tg", "List the individual REST apis for tg please", budget=12000)

    def test_model_sees_the_endpoint_lines(self, db):
        _seed(db)
        c = self._compile(db)
        assert "POST /token -> token" in c.text and "testgen/api/auth.py:5" in c.text
        assert "GET /r0 -> r0 (testgen/api/app.py:10)" in c.text
        assert "5 registration(s) under test paths left out" in c.text
        assert "mock0" not in c.text

    def test_extent_recorded_when_cut(self, db):
        db.upsert_code_markers("tg", [_marker("a.py", i + 1, f"GET /p{i}", f"h{i}", role="core")
                                      for i in range(MAX_ENDPOINTS_FULL + 30)])
        c = self._compile(db)
        ext = c.manifest["lists"]["interface_surface"]["endpoints"]
        assert ext["total"] == MAX_ENDPOINTS_FULL + 30 and ext["shown"]["FULL"] == MAX_ENDPOINTS_FULL
        assert f"{MAX_ENDPOINTS_FULL} of {MAX_ENDPOINTS_FULL + 30} shown to the model" in c.text
        assert "GET /p0 " in c.text and f"GET /p{MAX_ENDPOINTS_FULL + 5} " not in c.text

    def test_not_measured_is_said_not_zero(self, db):
        db.upsert_finding("tg", "interface_surface", [
            {"check_name": "http_api", "label": "implemented", "confidence": 90, "summary": "s" * 300}],
            surveyed_at="2026-10-14T21:24:25")
        c = self._compile(db)
        assert "endpoints: not measured" in c.text and "0 found" not in c.text
        assert "endpoints" not in c.manifest.get("lists", {}).get("interface_surface", {})


class TestDrillDown:
    def test_grouped_by_file_with_handler_and_line(self, db):
        _seed(db)
        m = members_for(db, "tg", "interface_surface").to_dict()
        assert m["total"] == 4 and not m["empty"]
        assert [g["name"] for g in m["groups"]] == ["testgen/api/app.py", "testgen/api/auth.py"]
        tok = m["groups"][1]["members"][0]
        assert tok["name"] == "POST /token" and "token" in tok["detail"] and "line 5" in tok["detail"]
        assert tok["extra"]["line"] == 5 and m["source"] == "project_code_markers"
        assert "5 registration(s) under test paths left out" in m["note"]

    def test_not_extracted_reads_not_measured(self, db):
        m = members_for(db, "tg", "interface_surface").to_dict()
        assert m["total"] is None and m["empty"] and m["reason"].startswith("not measured")

    def test_measured_none_reads_measured(self, db):
        db.upsert_code_markers("tg", [_marker("tests/t.py", 1, "GET /a", "a", role="test")])
        m = members_for(db, "tg", "interface_surface").to_dict()
        assert m["total"] == 0 and m["reason"].startswith("measured")
