"""pyegeria's `egeria_spec_cache.as_client(client)` is a CONTEXT MANAGER, for `with as_client(client):`.

U3 (2026-11-17, ruling RULING-EA-U3-IDENTITY-SWITCH-bbc70228): `build_user_client` returned
`as_client(client)` and its callers used that as the client. Every identity field read as None, and
pyegeria fell back to the configured service account. This test reads the source (AST, nothing imported)
of EA and Studio and fails if:
- as_client(...) is called anywhere except as a `with` item or as a function's return value; or
- a function that returns as_client(...) (a wrapper, such as bound_to or bound_reads, followed through
  wrappers of wrappers) is called anywhere except as a `with` item or as another wrapper's return value.
"""
from __future__ import annotations

import ast
from pathlib import Path
from typing import Dict, List, Set, Tuple

PACKAGES = Path(__file__).resolve().parents[3]
ROOTS = [PACKAGES / "egeria-advisor" / "advisor", PACKAGES / "studio" / "trellis_studio"]
SEED = "as_client"


def _called(node: ast.AST) -> str:
    if isinstance(node, ast.Call):
        f = node.func
        if isinstance(f, ast.Name):
            return f.id
        if isinstance(f, ast.Attribute):
            return f.attr
    return ""


def _parse(roots: List[Path]) -> List[Tuple[Path, ast.AST]]:
    out = []
    for root in roots:
        for p in sorted(root.rglob("*.py")):
            out.append((p, ast.parse(p.read_text(encoding="utf-8"), filename=str(p))))
    return out


def _parents(tree: ast.AST) -> Dict[ast.AST, ast.AST]:
    return {child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}


def _wrappers(trees) -> Set[str]:
    """Names of functions that return SEED(...) or another wrapper's call, to a fixed point."""
    names = {SEED}
    while True:
        found = set(names)
        for _, tree in trees:
            for fn in ast.walk(tree):
                if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    if any(isinstance(n, ast.Return) and _called(n.value) in names for n in ast.walk(fn)):
                        found.add(fn.name)
        if found == names:
            return names
        names = found


def misuses(roots: List[Path]) -> List[str]:
    trees = _parse(roots)
    wrappers = _wrappers(trees)
    bad = []
    for path, tree in trees:
        parents = _parents(tree)
        for call in ast.walk(tree):
            name = _called(call)
            if name not in wrappers:
                continue
            up = parents.get(call)
            if isinstance(up, ast.withitem) and up.context_expr is call:
                continue                       # with as_client(c): / with bound_to(c):
            if isinstance(up, ast.Return):
                fn = up
                while fn is not None and not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    fn = parents.get(fn)
                if fn is not None and fn.name in wrappers:
                    continue                   # a wrapper handing it on, itself checked at its call sites
            where = path.relative_to(PACKAGES) if path.is_relative_to(PACKAGES) else path.name
            bad.append(f"{where}:{call.lineno}: {name}(...) used as a value, not in a `with`")
    return bad


def test_as_client_and_its_wrappers_are_only_ever_used_in_a_with():
    assert not misuses(ROOTS), "\n".join(misuses(ROOTS))


def test_the_check_finds_the_u3_shape(tmp_path):
    """The bbc70228 shape: a builder returns as_client(client) and its caller keeps the result as the client."""
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "m.py").write_text(
        "from pyegeria.view.egeria_spec_cache import as_client\n"
        "def build_user_client(creds):\n"
        "    client = object()\n"
        "    return as_client(client)\n"
        "def run(creds):\n"
        "    client = build_user_client(creds)\n"
        "    return answer(client)\n"
        "def fine(c):\n"
        "    with as_client(c):\n"
        "        return 1\n")
    found = misuses([tmp_path / "pkg"])
    assert len(found) == 1 and "m.py:6: build_user_client(...)" in found[0]


def test_the_known_wrappers_are_seen(tmp_path):
    names = _wrappers(_parse(ROOTS))
    assert {"bound_reads", "bound_to"} <= names and "build_user_client" not in names
