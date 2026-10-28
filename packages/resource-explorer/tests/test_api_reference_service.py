"""API reference: committed-spec capture, source preference, the assembled reference, compile and drill-down."""
from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from resource_explorer.api_reference import committed, live, service
from resource_explorer.api_reference.model import SOURCE_COMMITTED, SOURCE_DERIVED, SOURCE_LIVE
from resource_explorer.context_compile import compile_context
from resource_explorer.ingestion.code_symbol_extractor import CodeMarker
from resource_explorer.members import members_for
from resource_explorer.registry import Project, ProjectRegistry

FIX = Path(__file__).parent / "fixtures" / "openapi"
SPEC = (FIX / "testgen-openapi.json").read_text()
URL = "http://localhost:8530/api/openapi.json"


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


def _code(db, n=4):
    db.upsert_code_markers("tg", [_marker("testgen/api/app.py", 10 + i, f"GET /api/v1/r{i}", f"r{i}") for i in range(n)]
                           + [_marker("testgen/tasks.py", 1, "ci.build", "task", kind="messaging")])


def _instance(db):
    live.register_instance(db, "tg", URL, transport=httpx.MockTransport(
        lambda r: httpx.Response(200, text=SPEC, headers={"content-type": "application/json"})))


def _tree(tmp_path):
    root = tmp_path / "repo"
    (root / "docs").mkdir(parents=True)
    (root / "vendor" / "lib").mkdir(parents=True)
    (root / "tests" / "fixtures").mkdir(parents=True)
    (root / "openapi.yaml").write_text((FIX / "service-openapi3.yaml").read_text())
    (root / "docs" / "swagger.json").write_text('{"swagger": "2.0", "info": {"title": "Old", "version": "0"}, "paths": {}}')
    (root / "vendor" / "lib" / "openapi.yaml").write_text((FIX / "service-openapi3.yaml").read_text())
    (root / "tests" / "fixtures" / "openapi.json").write_text(SPEC)
    return root


class TestCommittedCapture:
    def test_finds_own_spec_files_not_vendored_or_test_copies(self, tmp_path):
        root = _tree(tmp_path)
        assert committed.find_spec_files(root) == ["openapi.yaml", "docs/swagger.json"]

    def test_capture_stores_parsed_interfaces_and_load_returns_them(self, db, tmp_path):
        out = committed.capture_committed_specs(db, "tg", _tree(tmp_path), surveyed_at="2026-10-15T10:00:00")
        assert out["found"] == 2 and out["parsed"] == 2
        ifaces, problems = committed.load_committed(db, "tg")
        assert problems == [] and [i.origin for i in ifaces] == ["openapi.yaml", "docs/swagger.json"]   # most operations first
        assert ifaces[0].source == SOURCE_COMMITTED and len(ifaces[0].operations) == 4
        assert ifaces[0].as_of == "2026-10-15T10:00:00"

    def test_unparseable_spec_is_a_stated_problem_not_an_absence(self, db, tmp_path):
        root = tmp_path / "r"
        root.mkdir()
        (root / "openapi.json").write_text("{not json")
        committed.capture_committed_specs(db, "tg", root, surveyed_at="2026-10-15T10:00:00")
        ifaces, problems = committed.load_committed(db, "tg")
        assert ifaces == [] and problems and problems[0]["path"] == "openapi.json" and "not an OpenAPI document" in problems[0]["reason"]

    def test_a_spec_that_was_removed_stops_answering(self, db, tmp_path):
        root = _tree(tmp_path)
        committed.capture_committed_specs(db, "tg", root, surveyed_at="2026-10-15T10:00:00")
        (root / "openapi.yaml").unlink()
        (root / "docs" / "swagger.json").unlink()
        committed.capture_committed_specs(db, "tg", root, surveyed_at="2026-10-16T10:00:00")
        assert committed.load_committed(db, "tg") == ([], [])

    def test_symbol_extraction_step_captures_specs_from_its_own_checkout(self, db, tmp_path):
        from resource_explorer.surveyors.sub_surveyors.symbol_extraction import SymbolExtractionSurveyor
        root = _tree(tmp_path)
        SymbolExtractionSurveyor(db.get("tg"), db, str(root), surveyed_at="2026-10-15T10:00:00").run()
        ifaces, _ = committed.load_committed(db, "tg")
        assert [i.origin for i in ifaces] == ["openapi.yaml", "docs/swagger.json"]


class TestPreference:
    def test_committed_over_live_over_code(self, db, tmp_path):
        _code(db)
        assert service.best_interface(db, "tg").source == SOURCE_DERIVED
        _instance(db)
        assert service.best_interface(db, "tg").source == SOURCE_LIVE
        committed.capture_committed_specs(db, "tg", _tree(tmp_path), surveyed_at="2026-10-15T10:00:00")
        assert service.best_interface(db, "tg").source == SOURCE_COMMITTED

    def test_nothing_known_is_none(self, db):
        assert service.best_interface(db, "tg") is None

    def test_reference_lists_every_source_with_labels_and_comparisons(self, db):
        _code(db)
        _instance(db)
        ref = service.build_reference(db, "tg")
        assert [i["source"] for i in ref["interfaces"]] == [SOURCE_LIVE, SOURCE_DERIVED]
        assert ref["best"] == SOURCE_LIVE
        assert ref["interfaces"][0]["source_phrase"].startswith("from the running instance at http://localhost:8530/api/openapi.json, fetched ")
        assert ref["interfaces"][1]["source_phrase"].startswith("derived from code, not a published contract")
        assert len(ref["comparisons"]) == 1 and ref["comparisons"][0]["a"]["source"] == SOURCE_LIVE
        assert ref["instance"]["url"] == URL and ref["instance"]["status"] == "ok"

    def test_failed_refetch_shows_in_instance_status_and_label_keeps_the_copy_time(self, db):
        _instance(db)
        live.refetch_instance(db, "tg", transport=httpx.MockTransport(lambda r: httpx.Response(503, text="x")))
        ref = service.build_reference(db, "tg")
        assert ref["instance"]["status"] == "failed" and "503" in ref["instance"]["error"]
        assert ref["interfaces"][0]["operations"], "last good copy is still shown"

    def test_source_absence_is_explained_not_blank(self, db):
        ref = service.build_reference(db, "tg")
        assert ref["interfaces"] == [] and ref["best"] == ""
        assert {m["source"] for m in ref["missing"]} == {SOURCE_COMMITTED, SOURCE_LIVE, SOURCE_DERIVED}
        by = {m["source"]: m["reason"] for m in ref["missing"]}
        assert "no markers were captured" in by[SOURCE_DERIVED] or "not measured" in by[SOURCE_DERIVED]
        assert "no OpenAPI" in by[SOURCE_COMMITTED] or "no committed" in by[SOURCE_COMMITTED]
        assert "no running-instance URL" in by[SOURCE_LIVE]

    def test_kind_agnostic_payload_shape(self, db):
        _instance(db)
        i = service.build_reference(db, "tg")["interfaces"][0]
        assert i["kind"] == "http_api" and i["noun"] == "operations"
        op = i["operations"][0]
        assert {"key", "name", "kind", "group", "summary", "description", "input_schema"} <= set(op)


class TestCompile:
    def _text(self, db):
        return compile_context(db, "tg", "List the individual REST apis for tg please", budget=40000)

    def test_code_only_is_unchanged_and_labelled_as_code(self, db):
        _code(db)
        c = self._text(db)
        assert "GET /api/v1/r0 -> r0 (testgen/api/app.py:10)" in c.text
        assert "derived from code, not a published contract" in c.text

    def test_running_instance_preferred_over_code_and_says_which(self, db):
        _code(db)
        _instance(db)
        c = self._text(db)
        assert "from the running instance at http://localhost:8530/api/openapi.json, fetched " in c.text
        assert "26 operations" in c.text and "GET /oauth/authorize" in c.text
        assert "also found in code" in c.text and "4 route(s)" in c.text
        assert "GET /api/v1/r0 -> r0" not in c.text                  # the code list does not compete with the spec's
        ext = c.manifest["lists"]["interface_surface"]["endpoints"]
        assert ext["total"] == 26

    def test_committed_spec_beats_running_instance(self, db, tmp_path):
        _instance(db)
        committed.capture_committed_specs(db, "tg", _tree(tmp_path), surveyed_at="2026-10-15T10:00:00")
        c = self._text(db)
        assert "committed spec file openapi.yaml" in c.text and "from the running instance" not in c.text
        assert "GET /orders" in c.text and "Place an order" in c.text

    def test_summaries_and_auth_in_lines(self, db):
        _instance(db)
        c = self._text(db)
        assert "HTTPBearer" in c.text

    def test_extent_when_spec_is_cut(self, db):
        from resource_explorer.context_compile import MAX_ENDPOINTS_SUMMARY
        _instance(db)
        c = self._text(db)
        ext = c.manifest["lists"]["interface_surface"]["endpoints"]
        assert ext["shown"]["SUMMARY"] == min(26, MAX_ENDPOINTS_SUMMARY)


class TestDrillDown:
    def test_members_follow_the_same_best_source_as_the_compile(self, db):
        _code(db)
        _instance(db)
        m = members_for(db, "tg", "interface_surface").to_dict()
        assert m["total"] == 26 and m["source"] == "api_reference:running_instance"
        assert "running instance at" in m["note"]
        names = [x["name"] for g in m["groups"] for x in g["members"]]
        assert "GET /oauth/authorize" in names

    def test_code_only_unchanged(self, db):
        _code(db)
        m = members_for(db, "tg", "interface_surface").to_dict()
        assert m["source"] == "project_code_markers" and m["total"] == 5


def store_find(m):
    from resource_explorer.api_reference import store
    return store.find_operations(m, "x")


class TestTablesForAnalysis:
    def test_operations_are_filterable_by_path_word_method_and_source(self, db, tmp_path):
        _instance(db)
        committed.capture_committed_specs(db, "tg", _tree(tmp_path), surveyed_at="2026-10-15T10:00:00")
        from resource_explorer.api_reference import store
        rows, more = store.find_operations(db, "tg", path_contains="/jobs/{}")
        assert rows and all("jobs" in r["path"] for r in rows) and {r["source"] for r in rows} == {SOURCE_LIVE}
        assert [r["path"] for r in store.find_operations(db, "tg", text="place an order")[0]] == ["/orders"]
        assert {r["method"] for r in store.find_operations(db, "tg", method="delete")[0]} == {"DELETE"}
        assert {r["source"] for r in store.find_operations(db, "tg", source=SOURCE_COMMITTED)[0]} == {SOURCE_COMMITTED}
        assert store.find_operations(db, "tg", text="no such thing anywhere") == ([], False)

    def test_a_recapture_replaces_rows_instead_of_growing_them(self, db, tmp_path):
        root = _tree(tmp_path)
        for at in ("2026-10-15T10:00:00", "2026-10-16T10:00:00", "2026-10-17T10:00:00"):
            committed.capture_committed_specs(db, "tg", root, surveyed_at=at)
        with db._conn() as c:
            assert c.execute("SELECT COUNT(*) FROM api_reference_operations WHERE source = 'committed_spec'").fetchone()[0] == 4
            assert c.execute("SELECT COUNT(*) FROM api_reference_documents WHERE superseded_at IS NULL").fetchone()[0] == 3  # 2 files + marker

    def test_a_fake_registry_reads_as_nothing(self):
        from unittest.mock import MagicMock
        m = MagicMock()
        assert committed.load_committed(m, "x") == ([], [])
        assert committed.capture_state(m, "x") == {"captured": False, "at": ""}
        assert live.load_instance(m, "x") is None
        assert store_find(m) == ([], False)
