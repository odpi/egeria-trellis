"""Route markers describe what a product exposes, and api_structure reads the
symbols of the run it is in (inventory 2026-10-15, dataops_testgen).

* 2,438 markers, 54 real routes: the rest were `patch("...")` mock calls and
  invoke `@task` build commands, almost all under `tests/`.
* api_structure stored 5,937 symbols two seconds before extraction wrote 9,713.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from resource_explorer.ingestion.code_symbol_extractor import CodeSymbolExtractor
from resource_explorer.ingestion.test_paths import is_test_path

ROUTES_PY = '''
from fastapi import APIRouter
router = APIRouter()

@router.get("/token")
def get_token():
    return 1
'''

TEST_PY = '''
from unittest.mock import patch
import unittest.mock as mock

@patch("app.routes.x")
def test_a(m):
    pass

@mock.patch("app.routes.y")
def test_b(m):
    pass

@patch("app.routes.z", new=1)
def helper(): ...
'''

TASKS_PY = '''
from invoke import task

@task
def lint(c):
    pass
'''

CELERY_PY = '''
from celery import shared_task

@shared_task
def work():
    pass
'''

JAVA_ROUTE = '''
@RestController
class T {
    @GetMapping("/x")
    public String x() { return ""; }
}
'''


def _write(root, files):
    for rel, text in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)


def _markers(root, files):
    ex = CodeSymbolExtractor()
    out = []
    for rel, text in files.items():
        lang = "java" if rel.endswith(".java") else "python"
        out.extend(ex.extract_markers(rel, text, "p", lang))
    return out


FILES = {
    "app/routes.py": ROUTES_PY,
    "tests/test_routes.py": TEST_PY,
    "tests/test_more_routes.py": ROUTES_PY,      # a sample route in a test is still not product surface
    "invocations/ci.py": TASKS_PY,
    "app/jobs.py": CELERY_PY,
}


class TestPythonMarkers:
    def test_only_the_real_route_and_real_celery_task_are_recorded(self, tmp_path):
        ms = _markers(tmp_path, FILES)
        assert sorted((m.file_path, m.framework, m.detail) for m in ms) == [
            ("app/jobs.py", "celery", ""),
            ("app/routes.py", "fastapi", "GET /token"),
        ]

    def test_mock_patch_forms_are_not_routes_even_outside_tests(self):
        ms = CodeSymbolExtractor().extract_markers("app/helpers.py", TEST_PY, "p", "python")
        assert ms == []

    def test_invoke_task_is_not_celery(self):
        assert CodeSymbolExtractor().extract_markers("invocations/ci.py", TASKS_PY, "p", "python") == []

    def test_real_registration_forms_still_count(self):
        src = '''
@app.post("/a")
def a(): ...
@app.patch("/b")
def b(): ...
@bp.route("/c", methods=["PUT"])
def c(): ...
@router.get("")
def d(): ...
'''
        ms = CodeSymbolExtractor().extract_markers("app/x.py", src, "p", "python")
        assert sorted(m.detail for m in ms) == ["GET", "PATCH /b", "POST /a", "PUT /c"]


class TestDetectorGaps:
    def _m(self, src, path="app/x.py"):
        return CodeSymbolExtractor().extract_markers(path, src, "p", "python")

    def test_celery_app_instance_and_imported_app(self):
        assert len(self._m("from celery import Celery\napp = Celery('x')\n@app.task\ndef a(): ...\n")) == 1
        assert len(self._m("from proj.celery import app\n@app.task\ndef a(): ...\n")) == 1
        assert len(self._m("from .celery_app import capp as c\n@c.task\ndef a(): ...\n")) == 1

    def test_non_celery_app_task_is_not_celery(self):
        assert self._m("from invoke import Collection\nns = Collection()\n@ns.task\ndef a(): ...\n") == []

    def test_litestar_keyword_path(self):
        ms = self._m('@get(path="/x")\ndef a(): ...\n@post(path="/y")\ndef b(): ...\n')
        assert sorted(m.detail for m in ms) == ["GET /x", "POST /y"]
        assert self._m('@get(path="a.b.c")\ndef a(): ...\n') == []


class TestTestPathRule:
    @pytest.mark.parametrize("rel", [
        "tests/a.py", "pkg/tests/a.py", "test_a.py", "Test_a.py", "pkg/a_test.py", "src/test/java/a/B.java",
        "web/__tests__/a.js", "web/a.test.ts", "web/A.SPEC.TS", "src/main/java/FooTest.java",
        "src/main/java/FooTests.java", "x/FooTestCase.java", "x/FooTestIT.java", "conftest.py",
        "x/a_test.go", "src/integrationTest/java/A.java", "mod/src/testFixtures/kotlin/A.kt",
        "svc/src/it/java/A.java", "web/e2e/login.js", "web/cypress/support/x.js", "shop/tests.py",
        "test/helpers.py", "mod/test/helpers.py", "E2E/a.py",
    ])
    def test_tests(self, rel):
        assert is_test_path(rel)

    @pytest.mark.parametrize("rel", [
        "testgen/api/app.py", "app/routes.py", "src/main/java/a/B.java", "latest/a.py", "contest/x.py",
        "src/main/java/TestimonialController.java", "src/main/java/BusinessUnIT.java",
        "src/main/java/Contest.java", "app/lib/test/x.py", "app/lib/it/x.py", "attest.py",
        "src/main/java/TestUtil.java", "a/b/c/test/x.py",
    ])
    def test_product_code(self, rel):
        assert not is_test_path(rel)


class TestJavaFixture:
    def test_route_annotation_under_src_test_is_excluded(self):
        ex = CodeSymbolExtractor()
        if not ex.extract_markers("src/main/java/a/T.java", JAVA_ROUTE, "p", "java"):
            pytest.skip("java tree-sitter grammar unavailable")
        assert ex.extract_markers("src/test/java/a/T.java", JAVA_ROUTE, "p", "java") == []
        assert len(ex.extract_markers("src/main/java/a/T.java", JAVA_ROUTE, "p", "java")) == 1


class TestInterfaceSurfaceCount:
    def test_the_surveyor_stores_and_reports_one_registration(self, tmp_path):
        from resource_explorer.registry import Project, ProjectRegistry
        from resource_explorer.surveyors.sub_surveyors.symbol_extraction import SymbolExtractionSurveyor
        from resource_explorer.surveyors.sub_surveyors.interface_surface import _registrations

        repo = tmp_path / "repo"
        _write(repo, {k: v for k, v in FILES.items() if k != "app/jobs.py"})
        registry = ProjectRegistry(db_path=str(tmp_path / "r.db"))
        project = Project(slug="fx", display_name="fx", github_url="https://github.com/x/fx")
        registry.add(project)
        SymbolExtractionSurveyor(project, registry, str(repo)).run()
        rows = registry.get_code_markers("fx")
        assert [(r["file_path"], r["detail"]) for r in rows] == [("app/routes.py", "GET /token")]
        regs = _registrations(rows, None)
        assert len(regs["http_api"]) == 1 and "messaging" not in regs

    def test_stale_test_rows_already_stored_are_not_counted(self):
        rows = [{"interface_kind": "http_api", "detail": "PATCH", "file_path": "tests/t.py"},
                {"interface_kind": "http_api", "detail": "GET /a", "file_path": "app/a.py"}]
        from resource_explorer.surveyors.sub_surveyors.interface_surface import _registrations
        assert len(_registrations(rows, None)["http_api"]) == 1


class TestApiStructureOrder:
    """The planner is untouched; the ORDER is carried by the generated definitions."""

    def test_registry_places_extraction_before_api_structure(self):
        from resource_explorer.surveyors.repo_survey_definition_adapter import STEP_REGISTRY
        keys = list(STEP_REGISTRY)
        assert keys.index("repo_symbol_extraction") < keys.index("repo_api_structure")

    def test_every_live_definition_doc_with_both_steps_orders_them(self):
        from resource_explorer.surveyors.survey_definition_docs import documented_definitions
        seen = 0
        for name, doc in documented_definitions().items():
            if "repo_api_structure" in doc.steps and "repo_symbol_extraction" in doc.steps:
                seen += 1
                assert doc.steps.index("repo_symbol_extraction") < doc.steps.index("repo_api_structure"), name
        assert seen >= 2

    def test_stored_count_equals_the_symbols_written_in_the_same_run(self, tmp_path):
        from resource_explorer.registry import Project, ProjectRegistry
        from resource_explorer.surveyors.sub_surveyors.api_structure import ApiStructureSurveyor
        from resource_explorer.surveyors.sub_surveyors.symbol_extraction import SymbolExtractionSurveyor

        repo = tmp_path / "repo"
        _write(repo, {"app/a.py": "def one(): ...\ndef two(): ...\nclass C:\n    def m(self): ...\n"})
        registry = ProjectRegistry(db_path=str(tmp_path / "r.db"))
        project = Project(slug="fx", display_name="fx", github_url="https://github.com/x/fx")
        registry.add(project)
        SymbolExtractionSurveyor(project, registry, str(repo)).run()
        ApiStructureSurveyor(project, registry).run()
        assert registry.query_metrics("fx", "api_structure")["symbol_count"] == 4
