"""Tests for CodeIntelAgent — AST-ownership-transfer plan Phase 6.

CodeIntelAgent now reads Resource Explorer's resource_explorer.project_code_symbols
/ project_code_relationships tables (cross-schema SQL, same Postgres instance)
instead of EA's own code_symbols/code_relationships. This test seeds data
directly into RE's tables via raw SQL through EA's own db_manager — matching
exactly what the agent itself does — rather than importing RE's package,
since EA and RE don't depend on each other's Python code, only the shared
database.

Isolation (Phase 1 Q1-1, 2026-11-10): the seeded test runs in a throwaway schema
(conftest's ea_test_schema, `ea_test_<pid>_<hex>`, dropped afterwards) holding
copies of RE's three tables, and the agent's table names are pointed at it.
Until then it wrote RE's REAL resource_explorer schema on every EA unit run.
"""
import pytest
from datetime import datetime, timezone

from advisor.agents.code_intel_agent import (
    get_class_for_method,
    check_inheritance,
    get_class_hierarchy,
    get_codebase_stats,
    get_code_intel_agent
)
from advisor.db_consolidated import get_db_manager

_TEST_PROJECT_SLUG = "test_code_intel_dummy"

# RE's three tables (resource_explorer/registry.py), in Postgres form, with only what the agent and
# this test read; created in the throwaway schema, never in resource_explorer.
_RE_TABLES_DDL = """
CREATE TABLE {s}.projects (
    slug TEXT PRIMARY KEY, display_name TEXT NOT NULL, github_url TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE {s}.project_code_symbols (
    id SERIAL PRIMARY KEY, project_slug TEXT NOT NULL REFERENCES {s}.projects(slug),
    file_path TEXT NOT NULL, language TEXT NOT NULL, kind TEXT NOT NULL, name TEXT NOT NULL,
    qualified_name TEXT NOT NULL, signature TEXT DEFAULT '', docstring TEXT DEFAULT '',
    summary TEXT DEFAULT '', start_line INTEGER DEFAULT 0, end_line INTEGER DEFAULT 0,
    parent_class TEXT DEFAULT '', return_type TEXT DEFAULT '', is_private INTEGER DEFAULT 0,
    is_async INTEGER DEFAULT 0, complexity INTEGER DEFAULT 0,
    UNIQUE (project_slug, file_path, qualified_name)
);
CREATE TABLE {s}.project_code_relationships (
    id SERIAL PRIMARY KEY, project_slug TEXT NOT NULL REFERENCES {s}.projects(slug),
    relationship_type TEXT NOT NULL DEFAULT 'inherits_from', source_name TEXT NOT NULL,
    target_name TEXT NOT NULL, UNIQUE (project_slug, relationship_type, source_name, target_name)
);
"""


@pytest.fixture
def re_tables(ea_test_db, ea_test_schema, monkeypatch):
    """RE's code tables in the throwaway schema, with the agent reading them there."""
    from advisor.agents import code_intel_agent
    for stmt in _RE_TABLES_DDL.format(s=ea_test_schema).split(";"):
        if stmt.strip():
            ea_test_db.execute_update(stmt)
    monkeypatch.setattr(code_intel_agent, "_SYMBOLS_TABLE", f"{ea_test_schema}.project_code_symbols")
    monkeypatch.setattr(code_intel_agent, "_RELATIONSHIPS_TABLE", f"{ea_test_schema}.project_code_relationships")
    return ea_test_schema


def _seed_project(db, schema):
    now = datetime.now(timezone.utc).isoformat()
    db.execute_update(
        f"INSERT INTO {schema}.projects (slug, display_name, github_url, created_at) "
        "VALUES (%s, %s, %s, %s) ON CONFLICT (slug) DO NOTHING",
        (_TEST_PROJECT_SLUG, "Test Code Intel Dummy", "https://github.com/test/dummy", now),
    )


def _seed_symbol(db, schema, *, kind, name, qualified_name, start_line, end_line, docstring="", signature="", parent_class=""):
    db.execute_update(
        f"""INSERT INTO {schema}.project_code_symbols
           (project_slug, file_path, language, kind, name, qualified_name,
            signature, docstring, start_line, end_line, parent_class)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
           ON CONFLICT (project_slug, file_path, qualified_name) DO UPDATE SET
             kind=EXCLUDED.kind, signature=EXCLUDED.signature, docstring=EXCLUDED.docstring,
             start_line=EXCLUDED.start_line, end_line=EXCLUDED.end_line, parent_class=EXCLUDED.parent_class""",
        (_TEST_PROJECT_SLUG, "test_file.py", "python", kind, name, qualified_name,
         signature, docstring, start_line, end_line, parent_class),
    )


def _seed_relationship(db, schema, source_name, target_name):
    db.execute_update(
        f"""INSERT INTO {schema}.project_code_relationships
           (project_slug, relationship_type, source_name, target_name)
           VALUES (%s, 'inherits_from', %s, %s)
           ON CONFLICT (project_slug, relationship_type, source_name, target_name) DO NOTHING""",
        (_TEST_PROJECT_SLUG, source_name, target_name),
    )


def test_code_intel_agent(re_tables):
    db = get_db_manager()
    schema = re_tables
    assert schema.startswith("ea_test_")              # never resource_explorer
    _seed_project(db, schema)

    # Inheritance structure:
    # BaseClass
    # ClassA(BaseClass)
    # ClassB(ClassA)
    _seed_symbol(db, schema, kind="class", name="BaseClass", qualified_name="BaseClass",
                 start_line=1, end_line=10, docstring="Base class", signature="class BaseClass")
    _seed_symbol(db, schema, kind="class", name="ClassA", qualified_name="ClassA",
                 start_line=11, end_line=20, docstring="Class A", signature="class ClassA(BaseClass)")
    _seed_symbol(db, schema, kind="class", name="ClassB", qualified_name="ClassB",
                 start_line=21, end_line=30, docstring="Class B", signature="class ClassB(ClassA)")
    _seed_symbol(db, schema, kind="method", name="some_test_method", qualified_name="ClassB.some_test_method",
                 start_line=25, end_line=28, docstring="A method in ClassB",
                 signature="def some_test_method(self)", parent_class="ClassB")

    _seed_relationship(db, schema, "ClassA", "BaseClass")
    _seed_relationship(db, schema, "ClassB", "ClassA")

    # 1. Test get_class_for_method — collection param now resolves to a
    # literal project_slug for names not in _SCOPES (see _scope_clause).
    res_method = get_class_for_method("some_test_method", _TEST_PROJECT_SLUG)
    assert len(res_method) == 1
    assert res_method[0]["parent_class"] == "ClassB"

    # 2. Test check_inheritance
    res_inh = check_inheritance("ClassB", "BaseClass", _TEST_PROJECT_SLUG)
    assert res_inh["inherits"] is True
    assert len(res_inh["path"]) == 1
    assert res_inh["path"][0]["depth"] == 2

    res_inh_false = check_inheritance("BaseClass", "ClassB", _TEST_PROJECT_SLUG)
    assert res_inh_false["inherits"] is False

    # 3. Test get_class_hierarchy
    res_hier = get_class_hierarchy("ClassA", _TEST_PROJECT_SLUG)
    assert len(res_hier["ancestors"]) == 1
    assert res_hier["ancestors"][0]["class_name"] == "BaseClass"
    assert len(res_hier["descendants"]) == 1
    assert res_hier["descendants"][0]["class_name"] == "ClassB"

    # 4. Test get_codebase_stats
    stats = get_codebase_stats(_TEST_PROJECT_SLUG)
    assert stats["classes"] == 3
    assert stats["methods"] == 1
    assert stats["total_loc"] == (10 - 1 + 1) + (20 - 11 + 1) + (30 - 21 + 1) + (28 - 25 + 1)

    # 5. Test CodeIntelAgent handler
    agent = get_code_intel_agent()
    res_handle_stats = agent.handle(f"How many classes are defined in {_TEST_PROJECT_SLUG}?")
    assert res_handle_stats["query_type"] == "code_intel"

    res_handle_inh = agent.handle(f"Does ClassB inherit from BaseClass in {_TEST_PROJECT_SLUG}?")
    assert res_handle_inh["query_type"] == "code_intel"


class TestScopeClause:
    """_scope_clause() replaces the four near-duplicated hardcoded LIKE-block
    filters the pre-migration version of this file had — one per query
    function — with a single reusable helper (migration plan decision D4)."""

    def test_pyegeria_scope_resolves_to_project_and_path_filter(self):
        from advisor.agents.code_intel_agent import _scope_clause
        sql, params = _scope_clause("pyegeria")
        assert "project_slug = %s" in sql
        assert "file_path LIKE %s" in sql
        assert "file_path NOT LIKE %s" in sql
        assert params[0] == "egeria_python_git"
        assert params[1] == "pyegeria/%"
        assert len(params) == 2 + 5  # project + include prefix + 5 exclusions

    def test_egeria_java_scope_is_project_only(self):
        from advisor.agents.code_intel_agent import _scope_clause
        sql, params = _scope_clause("egeria_java")
        assert params == ["egeria_git"]
        assert "LIKE" not in sql

    def test_unrecognized_collection_treated_as_literal_project_slug(self):
        from advisor.agents.code_intel_agent import _scope_clause
        sql, params = _scope_clause("some_other_project")
        assert sql == "project_slug = %s"
        assert params == ["some_other_project"]

    def test_no_collection_scopes_to_default_project_set(self):
        from advisor.agents.code_intel_agent import _scope_clause, _DEFAULT_PROJECT_SLUGS
        sql, params = _scope_clause(None)
        assert "project_slug IN" in sql
        assert params == _DEFAULT_PROJECT_SLUGS
