"""API reference: the code-derived shape and the comparison between two sources."""
from __future__ import annotations

from pathlib import Path

import pytest

from resource_explorer.api_reference.compare import compare_interfaces, normalize_path
from resource_explorer.api_reference.derived import derive_from_code
from resource_explorer.api_reference.model import SOURCE_COMMITTED, SOURCE_DERIVED, SOURCE_LIVE
from resource_explorer.api_reference.openapi import load_document, parse_openapi
from resource_explorer.ingestion.code_symbol_extractor import CodeMarker, CodeSymbol
from resource_explorer.registry import Project, ProjectRegistry

FIX = Path(__file__).parent / "fixtures" / "openapi"


@pytest.fixture
def db(tmp_path):
    r = ProjectRegistry(db_path=str(tmp_path / "t.db"))
    r.add(Project(slug="tg", display_name="TG", github_url="https://github.com/x/tg", description=""))
    return r


def _marker(path, line, detail, qn, role="core", kind="http_api"):
    m = CodeMarker(resource_slug="tg", file_path=path, start_line=line, language="python",
                   marker_kind="route", framework="fastapi", interface_kind=kind, detail=detail, qualified_name=qn)
    m.file_role = role
    return m


def _seed(db):
    db.upsert_code_markers("tg", [
        _marker("testgen/api/app.py", 10, "GET /api/v1/health", "health"),
        _marker("testgen/api/jobs.py", 20, "GET /api/v1/jobs/{job_id}", "get_job"),
        _marker("testgen/api/jobs.py", 40, "DELETE /api/v1/jobs/{job_id}", "del_job"),
        _marker("testgen/api/internal.py", 3, "GET /debug/state", "debug"),
        _marker("tests/test_x.py", 3, "PATCH", "mock", role="test"),
        _marker("testgen/tasks.py", 3, "ci.build", "task", kind="messaging"),
    ])
    db.upsert_code_symbols("tg", [CodeSymbol(
        resource_slug="tg", file_path="testgen/api/jobs.py", language="python", kind="function", name="get_job",
        qualified_name="get_job", signature="(job_id: UUID) -> JobOut", docstring="Fetch one job.\nDetail.",
        start_line=20, end_line=30)])


class TestDerived:
    def test_same_operation_shape_labelled_derived(self, db):
        _seed(db)
        iface = derive_from_code(db, "tg")
        assert (iface.kind, iface.source) == ("http_api", SOURCE_DERIVED)
        assert sorted(o.key for o in iface.operations) == [
            "DELETE /api/v1/jobs/{job_id}", "GET /api/v1/health", "GET /api/v1/jobs/{job_id}", "GET /debug/state"]
        assert "not a published contract" in iface.source_phrase

    def test_handler_signature_docstring_and_location(self, db):
        _seed(db)
        op = next(o for o in derive_from_code(db, "tg").operations if o.key == "GET /api/v1/jobs/{job_id}")
        assert op.handler == "get_job" and op.summary == "Fetch one job."
        assert op.location == "testgen/api/jobs.py:20" and op.handler_signature == "(job_id: UUID) -> JobOut"

    def test_path_parameters_come_from_the_path_nothing_else_invented(self, db):
        _seed(db)
        op = next(o for o in derive_from_code(db, "tg").operations if o.key == "GET /api/v1/jobs/{job_id}")
        assert [(p["name"], p["in"], p["required"]) for p in op.parameters] == [("job_id", "path", True)]
        assert op.responses == [] and op.request_body is None and op.security == []

    def test_group_is_the_file_since_code_has_no_tags(self, db):
        _seed(db)
        ops = derive_from_code(db, "tg").operations
        assert {o.tags[0] for o in ops} == {"testgen/api/app.py", "testgen/api/jobs.py", "testgen/api/internal.py"}

    def test_tests_and_non_http_kinds_left_out(self, db):
        _seed(db)
        iface = derive_from_code(db, "tg")
        assert all("tests/" not in (o.location or "") for o in iface.operations)
        assert any("1 registration(s) under test paths left out" in n for n in iface.notes)

    def test_unmeasured_and_empty_are_different(self, db):
        assert derive_from_code(db, "tg") is None                   # nothing measured: no interface, not an empty one
        db.upsert_code_markers("tg", [_marker("tests/x.py", 1, "GET /a", "a", role="test")])
        iface = derive_from_code(db, "tg")
        assert iface is not None and iface.operations == []

    def test_cap_is_said(self, db):
        db.upsert_code_markers("tg", [_marker("a.py", i + 1, f"GET /r{i}", f"r{i}") for i in range(12)])
        iface = derive_from_code(db, "tg", max_operations=5)
        assert len(iface.operations) == 5 and iface.total_operations == 12 and any("5 of 12" in n for n in iface.notes)


def _spec(paths, source=SOURCE_LIVE):
    return parse_openapi({"openapi": "3.0.0", "paths": {p: {m: {"responses": {}} for m in ms} for p, ms in paths.items()}},
                         source=source, origin="x")


class TestNormalize:
    @pytest.mark.parametrize("raw,want", [
        ("/a/{id}/", "/a/{}"), ("/a/<int:id>", "/a/{}"), ("/a/:id", "/a/{}"), ("/", "/"), ("a/b", "/a/b"),
        ("/a/{job_id}/x/{other}", "/a/{}/x/{}"),
    ])
    def test_normalize_path(self, raw, want):
        assert normalize_path(raw) == want


class TestCompare:
    def test_both_only_a_only_b(self, db):
        _seed(db)
        code = derive_from_code(db, "tg")
        live = _spec({"/api/v1/health": ["get"], "/api/v1/jobs/{id}": ["get"], "/api/v1/extra": ["post"]})
        c = compare_interfaces(live, code)
        assert c["a"]["source"] == SOURCE_LIVE and c["b"]["source"] == SOURCE_DERIVED
        assert c["both"] == 2                        # {id} vs {job_id} is the same route
        assert [o["key"] for o in c["only_a"]] == ["POST /api/v1/extra"]
        assert sorted(o["key"] for o in c["only_b"]) == ["DELETE /api/v1/jobs/{job_id}", "GET /debug/state"]

    def test_only_in_code_grouped_by_file_so_whole_missing_files_show(self, db):
        _seed(db)
        c = compare_interfaces(_spec({"/api/v1/health": ["get"]}), derive_from_code(db, "tg"))
        assert c["only_b_by_file"] == {"testgen/api/internal.py": 1, "testgen/api/jobs.py": 2}

    def test_prefix_difference_is_a_probable_match_not_two_absences(self, db):
        db.upsert_code_markers("tg", [_marker("app.py", 1, "GET /jobs", "j")])
        c = compare_interfaces(_spec({"/api/v1/jobs": ["get"]}), derive_from_code(db, "tg"))
        assert c["both"] == 0 and c["only_a"] == [] and c["only_b"] == []
        assert c["probable"] == [{"a": "GET /api/v1/jobs", "b": "GET /jobs", "prefix": "/api/v1"}]

    def test_method_must_match(self, db):
        db.upsert_code_markers("tg", [_marker("app.py", 1, "POST /jobs", "j")])
        c = compare_interfaces(_spec({"/jobs": ["get"]}), derive_from_code(db, "tg"))
        assert c["both"] == 0 and len(c["only_a"]) == 1 and len(c["only_b"]) == 1

    def test_counts_and_sentences_say_both_numbers(self, db):
        _seed(db)
        c = compare_interfaces(_spec({"/api/v1/health": ["get"]}), derive_from_code(db, "tg"))
        assert (c["a"]["count"], c["b"]["count"]) == (1, 4)
        assert "1 operation" in c["summary"] and "4 operation" in c["summary"]

    def test_does_not_name_a_cause(self, db):
        _seed(db)
        c = compare_interfaces(_spec({"/api/v1/health": ["get"]}), derive_from_code(db, "tg"))
        assert "because" not in c["summary"].lower()
        assert "not decided here" in c["summary"]

    def test_testgen_live_vs_committed_style_identical_is_all_both(self):
        live = parse_openapi(load_document((FIX / "testgen-openapi.json").read_text()), source=SOURCE_LIVE, origin="u")
        same = parse_openapi(load_document((FIX / "testgen-openapi.json").read_text()), source=SOURCE_COMMITTED, origin="f")
        c = compare_interfaces(same, live)
        assert c["both"] == 26 and not c["only_a"] and not c["only_b"] and not c["probable"]
