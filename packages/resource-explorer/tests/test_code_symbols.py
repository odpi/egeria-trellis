"""Symbol lists for the model and the drill-down: reader, question matching,
compiled api_structure section, and class -> methods."""
from __future__ import annotations

import pytest

from resource_explorer.code_symbols import match_question, read_symbols
from resource_explorer.context_compile import MAX_CLASSES_FULL, compile_context
from resource_explorer.ingestion.code_symbol_extractor import CodeSymbol
from resource_explorer.members import children_for, members_for
from resource_explorer.registry import Project, ProjectRegistry


@pytest.fixture
def db(tmp_path):
    r = ProjectRegistry(db_path=str(tmp_path / "t.db"))
    r.add(Project(slug="tg", display_name="TG", github_url="https://github.com/x/tg", description=""))
    return r


def sym(path, qn, kind, line, sig="", doc="", parent="", private=False, role="core"):
    s = CodeSymbol(resource_slug="tg", file_path=path, language="python", kind=kind, name=qn.split(".")[-1],
                   qualified_name=qn, signature=sig, docstring=doc, start_line=line, end_line=line + 3,
                   parent_class=parent, is_private=private)
    s.file_role = role
    return s


def seed(db):
    db.upsert_code_symbols("tg", [
        sym("testgen/commands/run.py", "TestRunner", "class", 10, doc="Runs test suites.\nMore."),
        sym("testgen/commands/run.py", "TestRunner.execute", "method", 14, "(self, suite: str) -> int",
            "Execute one suite.", parent="TestRunner"),
        sym("testgen/commands/run.py", "TestRunner.collect", "method", 30, "(self) -> list", parent="TestRunner"),
        sym("testgen/commands/run.py", "TestRunner._hidden", "method", 40, "(self)", parent="TestRunner", private=True),
        sym("testgen/commands/run.py", "run_all", "function", 60, "(cfg) -> None", "Run everything."),
        sym("testgen/commands/other.py", "Other", "class", 3),
        sym("testgen/ui/page.py", "Page", "class", 5),
        sym("tests/test_run.py", "TestRunnerTests", "class", 1, role="test"),
        sym("vendor/lib.py", "Vendored", "class", 1, role="vendored"),
    ])


def compile_text(db, q, budget=14000):
    return compile_context(db, "tg", q, budget=budget)


class TestReader:
    def test_non_test_public_with_counts(self, db):
        seed(db)
        ss = read_symbols(db, "tg")
        names = {s.qualified_name for s in ss.rows}
        assert "TestRunner._hidden" not in names and "TestRunnerTests" not in names and "Vendored" not in names
        assert (ss.excluded_tests, ss.excluded_vendored, ss.private_left_out) == (1, 1, 1)
        m = next(s for s in ss.rows if s.qualified_name == "TestRunner.execute")
        assert (m.start_line, m.signature, m.doc, m.parent_class) == (14, "(self, suite: str) -> int", "Execute one suite.", "TestRunner")

    def test_filters(self, db):
        seed(db)
        got = {s.name for s in read_symbols(db, "tg", kinds=("class",), path_prefix="testgen/commands").rows}
        assert got == {"TestRunner", "Other"}
        assert {s.name for s in read_symbols(db, "tg", parent_class="TestRunner").rows} == {"execute", "collect"}
        assert {s.name for s in read_symbols(db, "tg", name_terms=("run_all",)).rows} == {"run_all"}

    def test_matcher_is_deterministic_and_conservative(self, db):
        seed(db)
        rows = read_symbols(db, "tg").rows
        assert [c.name for c in match_question("methods of testrunner", rows).classes] == ["TestRunner"]
        assert match_question("classes in testgen/commands", rows).modules == ["testgen/commands"]
        assert [c.name for c in match_question("what does run_all do", rows).callables] == ["run_all"]
        assert match_question("how healthy is it", rows).asked is False


class TestCompile:
    def test_class_question_gets_its_methods_with_lines_first(self, db):
        seed(db)
        t = compile_text(db, "List the methods of TestRunner").text
        i = t.index("question-matched class TestRunner")
        assert "testgen/commands/run.py:10" in t[i:i + 200]
        assert "TestRunner.execute(self, suite: str) -> int (testgen/commands/run.py:14) - Execute one suite." in t
        assert "TestRunner.collect" in t and "_hidden" not in t
        assert i < t.index("- classes:")

    def test_module_question_gets_symbols_under_it(self, db):
        seed(db)
        t = compile_text(db, "Which classes are in testgen/commands?").text
        assert "question-matched module testgen/commands: " in t
        assert "class Other (testgen/commands/other.py:3)" in t and "function run_all" in t
        assert "Page" not in t.split("- classes:")[0]

    def test_no_match_says_so_in_one_line(self, db):
        seed(db)
        t = compile_text(db, "Show the class FooBarBaz").text
        assert t.count("matches a name in the question") == 1

    def test_extents_recorded_and_text_says_k_of_n(self, db):
        db.upsert_code_symbols("tg", [sym("a.py", f"C{i}", "class", i + 1) for i in range(MAX_CLASSES_FULL + 25)])
        c = compile_text(db, "list the classes", budget=40000)
        ext = c.manifest["lists"]["api_structure"]["classes"]
        assert ext["total"] == MAX_CLASSES_FULL + 25 and ext["shown"]["FULL"] == MAX_CLASSES_FULL
        assert f"{MAX_CLASSES_FULL} of {MAX_CLASSES_FULL + 25} shown to the model" in c.text

    def test_tests_excluded_and_counted_in_text(self, db):
        seed(db)
        t = compile_text(db, "list the classes").text
        assert "TestRunnerTests" not in t and "1 under test paths" in t and "1 vendored" in t


class TestDrillDown:
    def test_members_carry_line_signature_doc_and_class_opens_to_methods(self, db):
        seed(db)
        m = members_for(db, "tg", "api_structure", "symbol_count", scope="public").to_dict()
        f = next(x for g in m["groups"] for x in g["members"] if x["name"] == "testgen/commands/run.py")
        kids = children_for(db, "tg", "api_structure", f["children_key"], scope="public")
        cls = next(k for k in kids if k["name"] == "TestRunner")
        assert cls["line"] == 10 and cls["doc"] == "Runs test suites." and cls["children_key"].startswith("class:")
        ex = next(k for k in kids if k["name"] == "TestRunner.execute")
        assert ex["line"] == 14 and ex["signature"] == "(self, suite: str) -> int" and "line 14" in ex["detail"]
        methods = children_for(db, "tg", "api_structure", cls["children_key"], scope="public")
        assert [(k["name"], k["line"]) for k in methods] == [("TestRunner.execute", 14), ("TestRunner.collect", 30)]
        allm = children_for(db, "tg", "api_structure", cls["children_key"], scope="all")
        assert "TestRunner._hidden" in {k["name"] for k in allm}
