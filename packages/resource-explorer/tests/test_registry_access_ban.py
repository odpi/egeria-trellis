"""Nothing under `resource_explorer/` opens the registry with `sqlite3.connect`.

Eight chat tools did (`sqlite3.connect(registry.db_path)`). On a Postgres registry `db_path` is an
empty SQLite file, so each returned "no such table" and the model reported it as missing data
("I don't have the commit count") while the store held the commits. Everything reads through
`ProjectRegistry._conn()`, which is the one door that honours `REGISTRY_DATABASE_URL`.

The AST is walked, not text-grepped, so a docstring naming `sqlite3.connect` is not a hit.
"""
from __future__ import annotations

import ast
from pathlib import Path

PKG = Path(__file__).resolve().parents[1] / "resource_explorer"

#: Files that may open a SQLite file, and why. None of them opens the registry.
ALLOWLIST = {
    # The metrics database (observability.metrics_db), a separate SQLite file by design.
    "vector_store_pg.py",
    # A deliberate read-only, authorizer-guarded SQLite connection for user-supplied survey SQL.
    "surveyors/survey_step_host.py",
}


def _calls_sqlite_connect(tree: ast.AST) -> list[int]:
    """Lines that call sqlite3's `connect`, through the module, an alias of it, or an imported name."""
    modules, connects = set(), set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules |= {a.asname or a.name for a in node.names if a.name == "sqlite3"}
        elif isinstance(node, ast.ImportFrom) and node.module == "sqlite3":
            connects |= {a.asname or a.name for a in node.names if a.name == "connect"}
    lines = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        f = node.func
        if (
            (isinstance(f, ast.Attribute) and f.attr == "connect"
             and isinstance(f.value, ast.Name) and f.value.id in modules)
            or (isinstance(f, ast.Name) and f.id in connects)
            # getattr(sqlite3, "connect")(...) and getattr(sqlite3, "connect") alone
            or (isinstance(f, ast.Call) and isinstance(f.func, ast.Name) and f.func.id == "getattr"
                and len(f.args) >= 2 and isinstance(f.args[0], ast.Name) and f.args[0].id in modules
                and isinstance(f.args[1], ast.Constant) and f.args[1].value == "connect")
        ):
            lines.append(node.lineno)
    return lines


def test_no_module_opens_the_registry_with_sqlite3_connect():
    offenders = []
    for path in sorted(PKG.rglob("*.py")):
        rel = path.relative_to(PKG).as_posix()
        if rel in ALLOWLIST:
            continue
        for line in _calls_sqlite_connect(ast.parse(path.read_text(encoding="utf-8"))):
            offenders.append(f"{rel}:{line}")
    assert offenders == [], (
        f"{offenders} call sqlite3.connect. Read the registry through ProjectRegistry()._conn(); "
        "on a Postgres registry a SQLite connection reads an empty file."
    )


def test_the_check_sees_a_call_and_ignores_a_docstring():
    assert _calls_sqlite_connect(ast.parse("import sqlite3\nsqlite3.connect('x')\n")) == [2]
    assert _calls_sqlite_connect(ast.parse('"""sqlite3.connect(x)"""\n')) == []


def test_the_check_sees_aliased_and_imported_forms():
    for src in (
        "import sqlite3 as s\ns.connect('x')\n",
        "from sqlite3 import connect\nconnect('x')\n",
        "from sqlite3 import connect as c\nc('x')\n",
        "import sqlite3\ngetattr(sqlite3, 'connect')('x')\n",
    ):
        assert _calls_sqlite_connect(ast.parse(src)) == [2], src
    assert _calls_sqlite_connect(ast.parse("import os\nos.connect('x')\n")) == []
