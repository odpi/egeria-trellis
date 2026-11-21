"""TR-112b: `resource-explorer schema apply`, the schema-version marker, and RE_SCHEMA_MODE.

The Postgres tests need a THROWAWAY Postgres: set RE_SCHEMA_TEST_PG_URL to a database URL without a
search_path (for example postgresql+psycopg2://postgres:pw@127.0.0.1:15442/re_test). Each test makes and drops its own
schema. Without the variable they skip, so no test can reach a shared registry.
"""
from __future__ import annotations

import os
import re
import uuid

import pytest
from typer.testing import CliRunner

from resource_explorer import registry as reg_mod
from resource_explorer.registry import ProjectRegistry

BASE_URL = os.environ.get("RE_SCHEMA_TEST_PG_URL", "")
needs_pg = pytest.mark.skipif(not BASE_URL, reason="RE_SCHEMA_TEST_PG_URL (a throwaway Postgres) is not set")
LINE = re.compile(r"^schema ready · (\d+) statements · [0-9.]+ ?m?s$")
DDL = re.compile(r"^\s*(CREATE|ALTER|DROP|SET\s+LOCAL|COMMENT|TRUNCATE)\b", re.I)


def _url(schema: str) -> str:
    return f"{BASE_URL}?options=-csearch_path%3D{schema}"


@pytest.fixture()
def schema():
    import psycopg2
    name = f"resource_explorer_test_schemaapply_{uuid.uuid4().hex[:8]}"
    yield name
    c = psycopg2.connect(BASE_URL.replace("postgresql+psycopg2://", "postgresql://"))
    c.autocommit = True
    c.cursor().execute(f'DROP SCHEMA IF EXISTS "{name}" CASCADE')
    c.close()


@pytest.fixture()
def sql_log(monkeypatch):
    """Every SQL string the registry sends, via the cursor wrappers."""
    seen: list[str] = []
    for cls in (reg_mod.PostgresCursorWrapper, reg_mod.SQLiteCursorWrapper):
        orig = cls.execute

        def wrap(self, sql, params=None, _o=orig):
            seen.append(sql)
            return _o(self, sql, params)
        monkeypatch.setattr(cls, "execute", wrap)
    return seen


def _tables(schema_name: str) -> set[str]:
    import psycopg2
    c = psycopg2.connect(BASE_URL.replace("postgresql+psycopg2://", "postgresql://"))
    cur = c.cursor()
    cur.execute("SELECT table_name FROM information_schema.tables WHERE table_schema=%s", (schema_name,))
    out = {r[0] for r in cur.fetchall()}
    c.close()
    return out


def _clear_pg_caches():
    ProjectRegistry._pg_engine_cache.clear()
    ProjectRegistry._pg_schema_ready.clear()


def _apply(url, monkeypatch):
    monkeypatch.setenv("REGISTRY_DATABASE_URL", url)
    from resource_explorer import config
    monkeypatch.setattr(config, "_config", None)
    _clear_pg_caches()
    from resource_explorer.cli.main import app
    return CliRunner().invoke(app, ["schema", "apply"], catch_exceptions=False)


@needs_pg
def test_apply_twice_same_line_second_run_issues_no_ddl(schema, monkeypatch, sql_log):
    first = _apply(_url(schema), monkeypatch)
    assert first.exit_code == 0, first.output
    m1 = LINE.match(first.stdout.strip().splitlines()[-1])
    assert m1, first.stdout
    assert int(m1.group(1)) > 100                     # the ~150-statement setup, counted
    assert any(DDL.match(s) for s in sql_log)
    sql_log.clear()
    second = _apply(_url(schema), monkeypatch)
    assert second.exit_code == 0, second.output
    m2 = LINE.match(second.stdout.strip().splitlines()[-1])
    assert m2 and m2.group(1) == m1.group(1)          # the same line: the stored count
    assert [s for s in sql_log if DDL.match(s)] == []  # no DDL at all on the second run


@needs_pg
def test_apply_keeps_search_path(schema, monkeypatch):
    assert _apply(_url(schema), monkeypatch).exit_code == 0
    assert "projects" in _tables(schema) and "schema_version" in _tables(schema)
    # nothing leaked into public: the URL's options were honoured, not dropped
    assert "projects" not in _tables("public")


@needs_pg
def test_external_with_matching_marker_runs_no_ddl(schema, monkeypatch, sql_log):
    assert _apply(_url(schema), monkeypatch).exit_code == 0
    sql_log.clear()
    _clear_pg_caches()
    monkeypatch.setenv("RE_SCHEMA_MODE", "external")
    ProjectRegistry(database_url=_url(schema))
    assert [s for s in sql_log if DDL.match(s)] == []


@needs_pg
def test_external_missing_marker_refuses_and_creates_nothing(schema, monkeypatch):
    _clear_pg_caches()
    monkeypatch.setenv("RE_SCHEMA_MODE", "external")
    with pytest.raises(reg_mod.SchemaNotReady) as exc:
        ProjectRegistry(database_url=_url(schema))
    assert "the RE schema is not at this version: run `resource-explorer schema apply`" in str(exc.value)
    assert "the chart's schema Job" in str(exc.value)
    assert "projects" not in _tables(schema)          # never DDL in this mode


@needs_pg
def test_external_mismatched_marker_refuses(schema, monkeypatch):
    import psycopg2
    assert _apply(_url(schema), monkeypatch).exit_code == 0
    c = psycopg2.connect(BASE_URL.replace("postgresql+psycopg2://", "postgresql://"))
    c.autocommit = True
    c.cursor().execute(f'UPDATE "{schema}".schema_version SET schema_hash = %s', ("0" * 64,))
    c.close()
    _clear_pg_caches()
    monkeypatch.setenv("RE_SCHEMA_MODE", "external")
    with pytest.raises(reg_mod.SchemaNotReady, match="the RE schema is not at this version"):
        ProjectRegistry(database_url=_url(schema))


@needs_pg
def test_auto_is_unchanged_and_runs_setup(schema, monkeypatch):
    _clear_pg_caches()
    monkeypatch.delenv("RE_SCHEMA_MODE", raising=False)
    ProjectRegistry(database_url=_url(schema))
    assert "projects" in _tables(schema)
    assert "schema_version" not in _tables(schema)    # auto writes no marker: today's behaviour


def test_sqlite_apply_and_external(tmp_path, monkeypatch, sql_log):
    url = f"sqlite:///{tmp_path/'r.db'}"
    monkeypatch.setenv("REGISTRY_DATABASE_URL", url)
    from resource_explorer import config
    monkeypatch.setattr(config, "_config", None)
    from resource_explorer.cli.main import app
    out = CliRunner().invoke(app, ["schema", "apply"], catch_exceptions=False)
    assert out.exit_code == 0 and LINE.match(out.stdout.strip().splitlines()[-1]), out.output
    sql_log.clear()
    monkeypatch.setenv("RE_SCHEMA_MODE", "external")
    ProjectRegistry(database_url=url)
    assert [s for s in sql_log if DDL.match(s)] == []


def test_bad_mode_is_refused(tmp_path, monkeypatch):
    monkeypatch.setenv("RE_SCHEMA_MODE", "sometimes")
    with pytest.raises(ValueError, match="RE_SCHEMA_MODE"):
        ProjectRegistry(database_url=f"sqlite:///{tmp_path/'x.db'}")


# ── the fingerprint follows what _init_schema imports (TR-112b read-back W1) ─────────────────────────────────

def _fp(mutate=None):
    from resource_explorer import schema_fingerprint as sf

    def source_of(name):
        src = sf.default_source(name)
        return mutate(name, src) if (mutate and src is not None) else src
    return sf.fingerprint("resource_explorer.registry", "ProjectRegistry", source_of=source_of)


def _edit_in_definition(module, name):
    """Mutate the first string or int literal inside the top-level definition of `name` in `module`."""
    import ast

    def m(mod, src):
        if mod != module:
            return src
        node = next(n for n in ast.parse(src).body
                    if (isinstance(n, (ast.FunctionDef, ast.ClassDef)) and n.name == name)
                    or (isinstance(n, ast.Assign) and any(getattr(t, "id", None) == name for t in n.targets)))
        doc = node.body[0] if getattr(node, "body", None) and isinstance(node.body, list) else None
        const = next(c for c in ast.walk(node) if isinstance(c, ast.Constant) and isinstance(c.value, str)
                     and c is not getattr(doc, "value", None) and c.value)
        seg = ast.get_source_segment(src, const)
        q = 3 if seg.endswith(('"""', "'''")) else 1
        return src.replace(seg, seg[:-q] + "X" + seg[-q:], 1)   # one extra character inside a string literal
    return m


def test_fingerprint_is_stable_and_ignores_docstrings_and_comments():
    base = _fp()
    assert base == _fp()
    assert base == _fp(lambda n, s: s.replace('"""Run `_init_schema` and log exactly one INFO line',
                                               '"""Changed words. Run `_init_schema` and log exactly one INFO line')
                       if n == "resource_explorer.registry" else s)
    assert base == _fp(lambda n, s: s + "\n# a comment\n" if n == "resource_explorer.open_data.schema" else s)


@pytest.mark.parametrize("module,name", [
    ("resource_explorer.open_data.schema", "ADD_REPO_SLUG_COLUMN"),      # from ... import in _migrate_open_data_tables
    ("resource_explorer.open_data.schema", "STATEMENTS"),
    ("resource_explorer.surveyors.survey_report", "ANNOTATION_TYPES_REGISTRY"),   # imported inside _init_schema
    ("resource_explorer.github.org_importer", "_url_to_slug"),                   # an imported helper function
])
def test_fingerprint_moves_when_imported_schema_code_changes(module, name):
    assert _fp() != _fp(_edit_in_definition(module, name)), f"{module}.{name} is not in the fingerprint"
