"""W23 (ui-batch): US spelling in the strings a person reads. catalogue_commit.py's string literals (the words,
hovers, step lines and GatewayError messages that reach the screen) say "cataloger"; docstrings, comments and
identifiers are not strings a person reads and keep their spelling, as do Egeria's own names and the proof-row
notes stored as data."""
from __future__ import annotations

import ast
from pathlib import Path

from resource_explorer import catalogue_commit as cc

#: kept: an id other code references, and notes stored in proof rows (data, not words on a screen)
KEPT = {"cataloguer_creates", "target gone from the cataloguer's list on read-back", "the cataloguer's connector"}


def _strings(tree):
    docs = {id(n.body[0].value) for n in ast.walk(tree)
            if isinstance(n, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
            and n.body and isinstance(n.body[0], ast.Expr) and isinstance(n.body[0].value, ast.Constant)}
    for n in ast.walk(tree):
        if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in docs:
            yield n.lineno, n.value


def test_no_person_facing_string_says_cataloguer():
    tree = ast.parse(Path(cc.__file__).read_text())
    bad = [(line, s) for line, s in _strings(tree) if "cataloguer" in s and s.strip() not in KEPT]
    assert bad == []


def test_the_lines_the_owner_reads():
    assert "Egeria's cataloger still lists this schema" in cc.LINGERING_LINE
