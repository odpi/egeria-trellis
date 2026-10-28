"""The things a count counted — resource_explorer/members.py.

Members are read from the registry, not from the display-shaped results,
because those drop `detail_json` and that is where cve_scan keeps its
advisory ids. These use a fresh SQLite registry and write the rows the
readers read, so every reader is exercised against the shape it consumes.
"""
from __future__ import annotations

import json

import pytest

from resource_explorer.members import children_for, members_for
from resource_explorer.registry import Project, ProjectRegistry


@pytest.fixture
def db(tmp_path):
    r = ProjectRegistry(db_path=str(tmp_path / "t.db"))
    r.add(Project(slug="p", display_name="P", github_url="https://github.com/x/p", description=""))
    return r


class TestCveMembers:
    def test_advisories_come_from_detail_json_grouped_by_package(self, db):
        db.upsert_finding("p", "cve_scan", [
            {"check_name": "java:pkg-a", "label": "high", "summary": "2 advisories for pkg-a 1.0: GHSA-1, GHSA-2",
             "detail": {"package": "pkg-a", "version": "1.0", "severity": "HIGH", "advisory_ids": ["GHSA-1", "GHSA-2"]}},
            {"check_name": "java:pkg-b", "label": "moderate", "summary": "1 advisory",
             "detail": {"package": "pkg-b", "version": "2.0", "severity": "MODERATE", "advisory_ids": ["GHSA-3"]}},
        ], surveyed_at="2026-09-01T00:00:00")
        m = members_for(db, "p", "cve_scan").to_dict()
        assert m["total"] == 3
        assert [g["name"] for g in m["groups"]] == ["pkg-a 1.0", "pkg-b 2.0"]   # severity order
        assert [x["name"] for x in m["groups"][0]["members"]] == ["GHSA-1", "GHSA-2"]
        assert m["groups"][0]["members"][0]["detail"] == "high"
        assert m["source"].endswith("detail_json")


class TestSymbolMembers:
    def test_scope_public_drops_private_and_says_it_is_inferred(self, db):
        from resource_explorer.ingestion.code_symbol_extractor import CodeSymbol

        def sym(path, qn, kind, private):
            return CodeSymbol(resource_slug="p", file_path=path, language="python", kind=kind,
                              name=qn.split(".")[-1], qualified_name=qn, signature="", docstring="",
                              start_line=1, end_line=2, is_private=private)
        db.upsert_code_symbols("p", [
            sym("a.py", "a.public_fn", "function", False),
            sym("a.py", "a._hidden", "function", True),
            sym("b.py", "b.C", "class", False),
        ])
        pub = members_for(db, "p", "api_structure", "symbol_count", scope="public").to_dict()
        allm = members_for(db, "p", "api_structure", "symbol_count", scope="all").to_dict()
        assert pub["total"] == 2 and allm["total"] == 3
        assert pub["scope_honoured"] is True
        assert "inferred" in pub["note"]
        # files are the first level; symbols are one level down, on demand
        files = {x["name"]: x for g in pub["groups"] for x in g["members"]}
        assert set(files) == {"a.py", "b.py"}
        kids = children_for(db, "p", "api_structure", files["a.py"]["children_key"], scope="public")
        assert [k["name"] for k in kids] == ["a.public_fn"]
        kids_all = children_for(db, "p", "api_structure", files["a.py"]["children_key"], scope="all")
        assert {k["name"] for k in kids_all} == {"a.public_fn", "a._hidden"}


class TestFallbackAndScope:
    def test_unknown_analysis_falls_back_to_its_findings_with_detail(self, db):
        db.upsert_finding("p", "ci_quality", [
            {"check_name": "cli", "label": "implied", "summary": "from click", "detail": {"framework": "click"}},
            {"check_name": "http_api", "label": "implied", "summary": "from fastapi"},
        ], surveyed_at="2026-09-01T00:00:00")
        m = members_for(db, "p", "ci_quality").to_dict()
        assert m["total"] == 2 and m["groups"][0]["name"] == "implied"
        assert m["groups"][0]["members"][0]["extra"] == {"framework": "click"}
        # scope is recorded but not honoured -- the caller can see it was
        # asked for and not applied, rather than assume it was
        assert m["scope"] == "public" and m["scope_honoured"] is False
        assert m["not_applicable"] is False

    def test_bad_scope_is_coerced_not_trusted(self, db):
        assert members_for(db, "p", "ci_quality", scope="everything").to_dict()["scope"] == "public"

    def test_children_of_an_unknown_key_is_empty_not_an_error(self, db):
        assert children_for(db, "p", "cve_scan", "nope:x") == []


class TestMetricsOnlyAnalysisHasNoMembers:
    """The defect: `members ›` on a repository_health row opened the rail
    with "MEMBERS for egeria_trellis · 0 …" -- a measured-looking zero from
    project_analysis_findings, a table repository_health never writes. See
    resource_explorer/members.py's `_is_metrics_only`."""

    def test_metrics_only_analysis_is_not_applicable_with_reason_and_a_real_run_at(self, db):
        from resource_explorer.activity_logger import log_analysis_run

        log_analysis_run(db, "repo", "p", "P", "success", "ran repository_health", "repository_health")
        db.upsert_metric("p", "repository_health", {"quality_score": 0})
        m = members_for(db, "p", "repository_health").to_dict()
        assert m["not_applicable"] is True
        assert m["total"] is None                 # uncounted, not a measured zero
        assert m["source"] == "project_analysis_metrics"
        assert "repository health" in m["reason"] and "not members" in m["reason"]
        assert m["run_at"]                         # the run just happened -- not blank

    def test_a_findings_kind_analysis_with_no_reader_still_uses_the_fallback(self, db):
        # security_scan writes project_analysis_findings and has no entry in
        # members._READERS -- case (b), untouched by the metrics-only carve-out.
        db.upsert_finding("p", "security_scan", [
            {"check_name": "hardcoded_secret", "label": "high", "summary": "found one"},
        ], surveyed_at="2026-09-01T00:00:00")
        m = members_for(db, "p", "security_scan").to_dict()
        assert m["not_applicable"] is False
        assert m["total"] == 1 and m["source"] == "project_analysis_findings"

    def test_a_registered_reader_is_unaffected(self, db):
        db.upsert_finding("p", "cve_scan", [
            {"check_name": "java:pkg-a", "label": "high", "summary": "1 advisory",
             "detail": {"package": "pkg-a", "version": "1.0", "severity": "HIGH", "advisory_ids": ["GHSA-1"]}},
        ], surveyed_at="2026-09-01T00:00:00")
        m = members_for(db, "p", "cve_scan").to_dict()
        assert m["not_applicable"] is False and m["total"] == 1


class TestAnswerLinkOpensTheStoreTheAnalysisWrote:
    """The answer page's "open the full list" link calls members_for with NO
    metric (chat.js openMembers({slug, analysisId})). api_structure's reader
    was registered only under ("api_structure", "symbol_count"), so the bare
    call fell through to the findings fallback and said "0 findings" for a
    repository whose api_structure data lives in project_code_symbols and
    project_analysis_metrics (live: dataops_testgen, 2026-10-15)."""

    @staticmethod
    def _sym(path, qn):
        from resource_explorer.ingestion.code_symbol_extractor import CodeSymbol
        return CodeSymbol(resource_slug="p", file_path=path, language="python", kind="function",
                          name=qn.split(".")[-1], qualified_name=qn, signature="", docstring="",
                          start_line=1, end_line=2, is_private=False)

    def test_bare_api_structure_call_reads_the_symbol_store(self, db):
        db.upsert_code_symbols("p", [self._sym("a.py", "a.f"), self._sym("b.py", "b.g")])
        m = members_for(db, "p", "api_structure").to_dict()   # no metric, as the link sends it
        assert m["total"] == 2
        assert m["source"] == "project_code_symbols"
        assert not m["empty"]

    def test_bare_code_symbol_extraction_and_manifest_parse_resolve_to_a_reader(self, db):
        m = members_for(db, "p", "manifest_parse").to_dict()
        assert m["source"] == "project_dependencies"          # not the findings fallback

    def test_an_empty_list_says_what_is_missing_not_zero_findings(self, db):
        m = members_for(db, "p", "interface_surface").to_dict()
        assert m["empty"] is True
        assert m["reason"].startswith("not measured")  # endpoints: never extracted != zero
        m2 = members_for(db, "p", "api_structure").to_dict()
        assert m2["empty"] is True and m2["reason"] == "nothing stored by api_structure for this repo"


class TestMembersRouteForTheAnswerLink:
    def test_route_without_metric_returns_the_symbols_the_answer_used(self, db, monkeypatch):
        from fastapi.testclient import TestClient
        db.upsert_code_symbols("p", [TestAnswerLinkOpensTheStoreTheAnalysisWrote._sym("a.py", "a.f")])
        monkeypatch.setattr("resource_explorer.registry.ProjectRegistry.__init__",
                            lambda self, db_path=None: setattr(self, "__dict__", db.__dict__) or None)
        monkeypatch.setenv("TRELLIS_ANONYMOUS_READ", "true")
        from resource_explorer.web.app import app
        r = TestClient(app).get("/api/projects/p/members/api_structure?entity_type=repo")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["total"] == 1 and body["source"] == "project_code_symbols" and body["empty"] is False
        r2 = TestClient(app).get("/api/projects/p/members/interface_surface?entity_type=repo")
        assert r2.json()["empty"] is True and r2.json()["reason"].startswith("not measured")
